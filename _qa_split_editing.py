"""Synthetic regression for editing existing split groups through the real UI."""
import ast
from contextlib import closing
from decimal import Decimal
import os
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

import pandas as pd

BASE = 'ec58e2ed09b5b956665bfe3ae7259220778b5ea9'


def without_split_editing(name, source):
    from _qa_reviewed_income_save import without_reviewed_income_save
    source = without_reviewed_income_save(name, source)
    source = source.replace(b'\r\n', b'\n')
    if name != 'app.py' or b'from split_editing import render_editor' not in source:
        return source
    restored = source
    for block in (
        '    from split_editing import render_editor\n'
        '    render_editor(st, df, categories_df, key_prefix, _clear_transaction_read_caches)\n',
        '        from split_editing import render_editor\n'
        '        render_editor(st, pending_view, get_categories(include_subcategories=True), "pending", _clear_transaction_read_caches)\n',
        '                    "amount": st.column_config.NumberColumn("Amount", disabled=True),\n',
    ):
        assert restored.count(block.encode()) == 1
        restored = restored.replace(block.encode(), b'', 1)
    assert restored == subprocess.check_output(['git', 'show', BASE + ':' + name]).replace(b'\r\n', b'\n'), 'Unrelated app code changed'
    return restored


class Rerun(Exception):
    pass


class UI:
    def __init__(self, action='', edit=None, state=None):
        self.controls, self.errors, self.frames = [], [], []
        self.action, self.edit = action, edit
        self.session_state = state if state is not None else {}
        self.column_config = self
    def NumberColumn(self, *args, **kwargs): return kwargs
    TextColumn = SelectboxColumn = CheckboxColumn = NumberColumn
    def expander(self, label, **kwargs):
        self.controls.append(label)
        return self
    def form(self, *args, **kwargs): return self
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def selectbox(self, label, options, **kwargs): return options[0]
    def caption(self, *args): pass
    def data_editor(self, frame, **kwargs):
        self.frames.append(frame.copy())
        return self.edit(frame.copy()) if self.edit else frame.copy()
    def dataframe(self, frame, **kwargs): self.frames.append(frame.copy())
    def form_submit_button(self, label, **kwargs): return label == self.action
    def error(self, message): self.errors.append(message)
    def success(self, message): pass
    def rerun(self): raise Rerun()


def seed(db, sign=-1):
    db.add_category('Synthetic A', 'One', 'Group A')
    db.add_category('Synthetic B', 'Two', 'Group B')
    db.add_category('Tour Income', 'Projects', 'Woking Way LLC')
    db.insert_manual_transaction('2026-01-01', 'Synthetic split ' + os.urandom(6).hex(), sign * 100, 'Synthetic A', 'One',
                                 dict(account_name='Synthetic', bank='Synthetic', account_number='FAKE',
                                      currency='USD', rate_type='USD/USD'))
    parent = int(db.get_all_transactions().iloc[0].id)
    db.split_transaction(parent, [dict(amount=40, category='Synthetic A', subcategory='One'),
                                  dict(amount=60, category='Synthetic B', subcategory='Two')])
    return parent


def payload(snapshot):
    return [dict(id=r['id'], amount=str(r['amount']), category=r['category'], subcategory=r['subcategory'],
                 report_group={'Synthetic A': 'Group A', 'Synthetic B': 'Group B', 'Tour Income': 'Woking Way LLC'}[r['category']],
                 reviewed=bool(r['reviewed'])) for r in snapshot['rows'] if r['id'] != snapshot['parent_id']]


def database_snapshot(db):
    with closing(db.get_connection()) as conn:
        cur = conn.cursor()
        if db.USING_POSTGRES: cur.execute('SET LOCAL extra_float_digits=3')
        tables = ('classified_transactions', 'transaction_change_log', 'statement_imports', 'statement_balances')
        result = []
        for table in tables:
            cur.execute('SELECT * FROM ' + table + ' ORDER BY id')
            result.append(cur.fetchall())
        return result


def residue_regression(db):
    import split_editing as split
    for sign in (-1, 1):
        for i in range(6): db.add_category('Synthetic allocation', str(i), 'Group A')
        db.insert_manual_transaction('2026-01-01', 'Synthetic residue ' + os.urandom(6).hex(),
                                     sign * 100, 'Synthetic allocation', '0',
                                     dict(account_name='Synthetic', bank='Synthetic', account_number='FAKE',
                                          currency='USD', rate_type='USD/USD'))
        parent = int(db.get_all_transactions().iloc[0].id)
        db.split_transaction(parent, [dict(amount=value, category='Synthetic allocation', subcategory=str(i))
                                      for i, value in enumerate((17, 17, 17, 17, 17, 15))])
        with closing(db.get_connection()) as conn:
            cur = conn.cursor()
            cur.execute('UPDATE classified_transactions SET amount_usd=? WHERE id=?', (str(sign * Decimal('.03')), parent))
            cur.execute('SELECT id FROM classified_transactions WHERE split_parent_id=? ORDER BY split_allocation_index', (parent,))
            for i, (child,) in enumerate(cur.fetchall()):
                cur.execute('UPDATE classified_transactions SET amount_usd=? WHERE id=?',
                            (str(sign * Decimal('.01') if i < 3 else Decimal(0)), child))
            conn.commit()
        snapshot = split.load_group(parent)
        children = sorted([r for r in snapshot['rows'] if r['id'] != parent], key=lambda r: r['split_allocation_index'])
        edits = [dict(id=r['id'], amount=str(sign * value), category=r['category'], subcategory=r['subcategory'],
                      report_group='Group A', reviewed=bool(r['reviewed']))
                 for r, value in zip(children, (17, 17, 17, 17, 16, 16))]
        split.save_group(snapshot, edits)
        actual = [r for r in split.load_group(parent)['rows'] if r['id'] != parent]
        usd = [Decimal(str(r['amount_usd'])) for r in actual]
        assert sum(usd, Decimal(0)) == sign * Decimal('.03')
        assert all(v == 0 or v.is_signed() == (sign < 0) for v in usd), 'USD residual reversed an allocation sign'


def exercise(db, kind):
    import split_editing as split
    for sign in (-1, 1):
        parent = seed(db, sign)
        initial = split.load_group(parent)
        changes = payload(initial)
        changes[0].update(category='Tour Income', subcategory='Projects', report_group='Woking Way LLC', reviewed=False)
        observed = []
        original_connection = db.get_connection
        class Cursor:
            def __init__(self, cur): self.cur = cur
            def __getattr__(self, name): return getattr(self.cur, name)
            def execute(self, query, params=None):
                if query.startswith('UPDATE classified_transactions'): observed.append(query)
                self.cur.execute(query, params) if params is not None else self.cur.execute(query)
                return self
        class Connection:
            def __init__(self): self.conn = original_connection()
            def __getattr__(self, name): return getattr(self.conn, name)
            def cursor(self): return Cursor(self.conn.cursor())
        with patch.object(db, 'get_connection', Connection):
            assert split.save_group(initial, changes) == 1
        assert observed and all('amount' not in q and 'fx_rate' not in q for q in observed)
        fresh = split.load_group(parent)
        children = [r for r in fresh['rows'] if r['id'] != parent]
        assert children[0]['category'] == 'Tour Income' and children[0]['subcategory'] == 'Projects'
        assert not children[0]['reviewed']
        before = database_snapshot(db)
        assert split.save_group(fresh, payload(fresh)) == 0
        assert database_snapshot(db) == before
        try: split.save_group(initial, changes)
        except split.SplitEditError: pass
        else: raise AssertionError('Stale snapshot accepted')
        assert database_snapshot(db) == before
        invalid = payload(fresh)
        invalid[0]['amount'] = str(sign * 39)
        try: split.save_group(fresh, invalid)
        except split.SplitEditError: pass
        else: raise AssertionError('Invalid total accepted')
        assert database_snapshot(db) == before
        edited = payload(fresh)
        edited[0]['amount'], edited[1]['amount'] = str(sign * Decimal('35.251')), str(sign * Decimal('64.749'))
        assert split.save_group(fresh, edited) == 2
        latest = split.load_group(parent)
        split_rows = [r for r in latest['rows'] if r['id'] != parent]
        assert sum((Decimal(str(r['amount'])) for r in split_rows), Decimal(0)) == sign * 100
        assert sum((Decimal(str(r['amount_usd'])) for r in split_rows), Decimal(0)) == sign * 100
        assert split_rows[0]['amount'] == str(sign * Decimal('35.251')) or Decimal(str(split_rows[0]['amount'])) == sign * Decimal('35.251')
        invalid = payload(latest)
        invalid[0]['account_number'] = 'FORBIDDEN'
        try: split.save_group(latest, invalid)
        except split.SplitEditError: pass
        else: raise AssertionError('Identity edit accepted')
        invalid = payload(latest)
        invalid[0]['amount'], invalid[1]['amount'] = str(-sign * 5), str(sign * 105)
        try: split.save_group(latest, invalid)
        except split.SplitEditError: pass
        else: raise AssertionError('Opposite sign accepted')
        # Failure after the first update must roll back both financial and audit writes.
        before = database_snapshot(db)
        failed = payload(latest)
        for r in failed: r['reviewed'] = not r['reviewed']
        updates = []
        class FailingCursor(Cursor):
            def execute(self, query, params=None):
                if query.startswith('UPDATE classified_transactions'):
                    updates.append(query)
                    if len(updates) == 2: raise RuntimeError('PRIVATE_SYNTHETIC_CANARY')
                return super().execute(query, params)
        class FailingConnection(Connection):
            def cursor(self): return FailingCursor(self.conn.cursor())
        with patch.object(db, 'get_connection', FailingConnection):
            try: split.save_group(latest, failed)
            except split.SplitEditError as error: assert 'PRIVATE_SYNTHETIC_CANARY' not in str(error)
            else: raise AssertionError('Injected failure accepted')
        assert len(updates) == 2 and database_snapshot(db) == before
        tiny = payload(latest)
        tiny[0]['amount'], tiny[1]['amount'] = str(sign * Decimal('35.250000001')), str(sign * Decimal('64.749999999'))
        if kind == 'REAL':
            try: split.save_group(latest, tiny)
            except split.SplitEditError as error: assert 'Storage could not preserve' in str(error)
            else: raise AssertionError('Unrepresentable REAL value silently rounded')
            assert database_snapshot(db) == before
        else:
            assert split.save_group(latest, tiny) == 2
        if kind == 'NUMERIC':
            current = split.load_group(parent)
            ultra = payload(current)
            prefix = '-' if sign < 0 else ''
            ultra[0]['amount'] = prefix + '35.2500000000000000000000000001'
            ultra[1]['amount'] = prefix + '64.7499999999999999999999999999'
            assert split.save_group(current, ultra) == 2
            reloaded = payload(split.load_group(parent))
            assert [r['amount'] for r in reloaded] == [r['amount'] for r in ultra]
        import reporting
        all_rows = db.get_all_transactions()
        active = db.filter_financially_active_transactions(all_rows)
        assert parent not in set(active.id)
        assert len(active[active.split_parent_id.eq(parent)]) == 2
        scoped = reporting._assign_report_groups(active.copy(), db.get_categories(True))
        selected = scoped[scoped.id.eq(split_rows[0]['id'])].iloc[0]
        assert selected.report_group == 'Woking Way LLC'
        assert reporting.is_income(selected.category, selected.subcategory, selected.report_group)
        totals, _ = reporting.build_report_verification(all_rows, db.get_categories(True))
        assert Decimal(str(totals['net_movement'])) == sum((Decimal(str(r)) for r in active.amount), Decimal(0))
    residue_regression(db)
    print('PASS', kind, 'signed/finer totals, persistence, taxonomy, stale rejection, no-op, rollback, storage safety, USD residue')

def application_panel():
    tree = ast.parse(Path('app.py').read_text(encoding='utf-8'))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
              and n.name == 'render_transaction_split_panel')
    namespace = {'pd': pd, '_clear_transaction_read_caches': lambda: None}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), 'app.py', 'exec'), namespace)
    return namespace


def main():
    import db
    assert not db.USING_POSTGRES and Path(os.environ['TEMP']).drive.upper() == 'E:'
    with tempfile.TemporaryDirectory(dir=os.environ['TEMP']) as folder, patch.object(
            db, 'DB_PATH', str(Path(folder) / 'split.sqlite')):
        db.init_db()
        parent = seed(db)
        children = db.get_all_transactions().query('split_parent_id == @parent')
        ui = UI()
        env = application_panel()
        env['st'] = ui
        env['render_transaction_split_panel'](children, db.get_categories(True), ['Synthetic A'], 'qa')
        assert 'Edit existing split allocations' in ui.controls, 'Existing split groups have no allocation editing UI'
        def edit(frame):
            frame.loc[0, 'amount'], frame.loc[1, 'amount'] = '-30.005', '-69.995'
            frame.loc[0, 'classification'] = 'Tour Income / Projects'
            frame.loc[0, 'reviewed'] = False
            return frame
        before = database_snapshot(db)
        for action in ('', 'Cancel split changes'):
            ui = UI(action, edit)
            env['st'] = ui
            try: env['render_transaction_split_panel'](children, db.get_categories(True), ['Synthetic A'], 'qa')
            except Rerun: pass
            assert not ui.errors and database_snapshot(db) == before
        ui = UI('Save split changes', edit)
        env['st'] = ui
        try: env['render_transaction_split_panel'](children, db.get_categories(True), ['Synthetic A'], 'qa')
        except Rerun: pass
        assert not ui.errors
        for _ in range(2):
            fresh_ui = UI()
            env['st'] = fresh_ui
            env['render_transaction_split_panel'](children, db.get_categories(True), ['Synthetic A'], 'qa')
            assert Decimal(fresh_ui.frames[0].iloc[0].amount) == Decimal('-30.005')
            assert fresh_ui.frames[0].iloc[0].classification == 'Tour Income / Projects'
            assert fresh_ui.frames[1].iloc[0]['Reporting group'] == 'Woking Way LLC'
        def invalid(frame):
            frame.loc[0, 'amount'] = '-1'
            return frame
        before = database_snapshot(db)
        ui = UI('Save split changes', invalid)
        env['st'] = ui
        env['render_transaction_split_panel'](children, db.get_categories(True), ['Synthetic A'], 'qa')
        assert ui.errors and database_snapshot(db) == before
        # Execute the real Pending Review integration statements, not a mock save path.
        tree = ast.parse(Path('app.py').read_text(encoding='utf-8'))
        pending = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                       and ast.unparse(n.test) == "page == 'Pending Review'")
        nodes = [n for n in ast.walk(pending) if isinstance(n, ast.ImportFrom) and n.module == 'split_editing'
                 or isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                 and isinstance(n.value.func, ast.Name) and n.value.func.id == 'render_editor']
        assert len(nodes) == 2
        ui = UI()
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'app.py', 'exec'),
             dict(st=ui, pending_view=children, get_categories=db.get_categories,
                  _clear_transaction_read_caches=lambda: None))
        assert 'Edit existing split allocations' in ui.controls and database_snapshot(db) == before
        exercise(db, 'SQLite')
    print('PASS real Database/Pending UI, Save, refresh, new session, Cancel and invalid total; headless only')
    import psycopg2
    options = dict(host='127.0.0.1', port=int(os.environ['ARETI_QA_PG_PORT']), user='qa_local', sslmode='disable')
    admin = psycopg2.connect(dbname='postgres', **options)
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute('SHOW data_directory')
            assert Path(cur.fetchone()[0]).resolve() == Path(os.environ['ARETI_QA_PG_DATA']).resolve()
        for kind in ('REAL', 'DOUBLE PRECISION', 'NUMERIC'):
            name = 'qa_split_edit_' + os.urandom(6).hex()
            with admin.cursor() as cur: cur.execute('CREATE DATABASE ' + name)
            def connect():
                return db.PostgresConnection(psycopg2.connect(dbname=name, options='-c extra_float_digits=0', **options))
            try:
                with patch.object(db, 'USING_POSTGRES', True), patch.object(db, 'get_connection', connect):
                    db.init_db()
                    if kind != 'REAL':
                        with closing(connect()) as conn:
                            for column in ('amount', 'amount_usd', 'fx_rate', 'split_original_amount'):
                                conn.cursor().execute('ALTER TABLE classified_transactions ALTER COLUMN ' + column + ' TYPE ' + kind)
                            conn.commit()
                    exercise(db, kind)
            finally:
                with admin.cursor() as cur: cur.execute('DROP DATABASE ' + name)
    finally: admin.close()
    without_split_editing('app.py', Path('app.py').read_bytes())
    try: without_split_editing('app.py', Path('app.py').read_bytes() + b'\nUNAUTHORIZED=True\n')
    except AssertionError: pass
    else: raise AssertionError('Protected app guard weakened')


if __name__ == '__main__':
    main()
