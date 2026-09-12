"""Synthetic Item 19 editing regression; local isolated SQLite only."""
import ast
import hashlib
import inspect
import os
import sqlite3
import tempfile
from contextlib import closing, nullcontext
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd


def load_functions(source, names, env):
    nodes = [node for node in ast.parse(source).body
             if isinstance(node, ast.FunctionDef) and node.name in names]
    assert len(nodes) == len(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "app.py", "exec"), env)


class Rerun(Exception):
    pass


class UI:
    def __init__(self):
        self.session_state = {}
        self.column_config = SimpleNamespace(Column=lambda *a, **k: k,
                                             SelectboxColumn=lambda *a, **k: k)
        self.action = None
        self.transform = lambda frame: frame
        self.errors = []
        self.keys = []
        self.editor_count = 0
        self.table_count = 0

    def markdown(self, *args, **kwargs):
        pass

    def success(self, *args):
        pass

    def error(self, message):
        self.errors.append(message)

    def form(self, *args, **kwargs):
        return nullcontext()

    def form_submit_button(self, label, **kwargs):
        return label == self.action

    def data_editor(self, frame, **kwargs):
        self.editor_count += 1
        self.keys.append(kwargs['key'])
        assert set(frame) - set(kwargs['disabled']) == {'category_subcategory'}
        assert kwargs['num_rows'] == 'fixed'
        self.displayed = frame.copy(deep=True)
        return self.transform(frame.copy(deep=True))

    def dataframe(self, *args, **kwargs):
        self.table_count += 1

    def rerun(self):
        raise Rerun()


def main():
    root = Path(os.environ['TEMP']).resolve()
    assert root.drive.upper() == 'E:', 'QA runtime must be on E:'
    for key in list(os.environ):
        if any(part in key.upper() for part in ('DATABASE', 'POSTGRES', 'SUPABASE')) or key.upper().startswith('PG'):
            os.environ.pop(key, None)
    with tempfile.TemporaryDirectory(prefix='income-charity-', dir=root) as folder:
        database = Path(folder).resolve() / 'synthetic.sqlite'
        assert database.is_relative_to(root) and database.drive.upper() == 'E:'
        os.environ['ARETI_DB_PATH'] = str(database)
        os.environ['ARETI_SHARED_FOLDER'] = folder
        import db
        assert not db.USING_POSTGRES
        db.DB_PATH = str(database)
        original_connect = db.get_connection
        connections = []

        def safe_connect():
            assert not db.USING_POSTGRES and Path(db.DB_PATH).resolve() == database
            connection = original_connect()
            connections.append((inspect.currentframe().f_back.f_code.co_name, connection))
            return connection

        db.get_connection = safe_connect
        db.init_db()
        for category, subcategory, group in [
            ('Income', 'First', 'Income'), ('Income', 'Second', 'Income'),
            ('Other Income', 'Alternative', 'Income'), ('Charity', 'First', 'Family expenses'),
            ('Charity', 'Second', 'Family expenses'), ('Expense', 'General', 'Family expenses'),
        ]:
            db.add_category(category, subcategory, group)
        with closing(safe_connect()) as conn, conn:
            for row_id, category, amount in [(1, 'Income', 100), (2, 'Income', 200), (3, 'Charity', -30)]:
                conn.execute('''INSERT INTO classified_transactions
                    (id,row_hash,txn_date,amount,amount_usd,currency,category,subcategory,
                     original_description,normalized_description,account_name,bank,account_number,reviewed,status)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (row_id, f'synthetic-{row_id}', '2026-01-01', amount, amount, 'USD', category,
                     'First', f'Synthetic row {row_id}', f'synthetic row {row_id}', 'Synthetic account',
                     'Synthetic bank', 'synthetic-account', 1, 'reviewed'))

        def rows():
            with closing(safe_connect()) as conn, conn:
                frame = pd.read_sql_query('SELECT * FROM classified_transactions ORDER BY id', conn)
            frame['report_group'] = frame.category.map({'Income': 'Income', 'Other Income': 'Income',
                                                       'Charity': 'Family expenses', 'Expense': 'Family expenses'})
            frame['month'] = pd.Period('2026-01', freq='M')
            frame['report_amount'] = frame.amount_usd
            return frame

        source = Path(__file__).with_name('app.py').read_text(encoding='utf-8')
        ui = UI()
        env = {'pd': pd, 'hashlib': hashlib, 'st': ui,
               '_CATEGORY_PAIR_COLUMN': 'category_subcategory', '_NO_SUBCATEGORY_LABEL': 'No subcategory',
               'save_reviewed_rows': db.save_reviewed_rows, 'get_categories': db.get_categories,
               '_executive_signed_amount_series': lambda frame: frame.amount_usd,
               '_clear_transaction_read_caches': Mock()}
        names = {'_category_pair_label', '_parse_category_pair_label', '_category_pair_options',
                 '_with_category_pair_column', '_editor_row_signature', '_scoped_editor_key',
                 '_save_income_charity_edits', '_render_income_charity_editor',
                 '_render_income_charity_transactions'}
        load_functions(source, names, env)
        render = env['_render_income_charity_transactions']
        save = env['_save_income_charity_edits']
        paired = env['_with_category_pair_column']
        categories = db.get_categories(include_subcategories=True)
        initial = rows()
        with closing(safe_connect()) as conn, conn:
            schema_before = conn.execute("SELECT name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name").fetchall()

        def submit(frame, action, replacements):
            ui.action = action
            def edit(display):
                for row_id, label in replacements.items():
                    display.loc[display.id == row_id, 'category_subcategory'] = label
                return display
            ui.transform = edit
            try:
                render(frame, editable=True)
            except Rerun:
                pass

        income = initial[initial.category == 'Income']
        charity = initial[initial.category == 'Charity']
        render(income)
        assert ui.table_count == 1 and ui.editor_count == 0, 'THIRD default remains read-only'
        submit(income, 'Cancel', {1: 'Income / Second', 2: 'Income / Second'})
        pd.testing.assert_frame_equal(initial, rows())
        cancel_key = ui.keys[-1]
        submit(income, None, {})
        assert ui.keys[-1] != cancel_key
        assert ui.displayed.category_subcategory.tolist() == ['Income / First'] * 2
        submit(income, 'Save', {1: 'Income / Second', 2: 'Income / Second'})
        assert rows().subcategory.tolist() == ['Second', 'Second', 'First']
        submit(charity, 'Save', {3: 'Charity / Second'})
        assert rows().subcategory.tolist() == ['Second'] * 3
        assert len(set(ui.keys)) >= 3, 'independent editor scopes and Cancel revision'
        assert not ui.errors, ui.errors
        env['_clear_transaction_read_caches'].assert_called()
        print('PASS: Income/Charity bulk Save, Cancel, rerun keys and fresh-connection persistence')

        # A fresh session rebuilds solely from persisted rows, not old widget state.
        ui.session_state.clear()
        submit(rows().iloc[:1], None, {})
        assert ui.displayed.category_subcategory.tolist() == ['Income / Second']
        before = rows()
        edited = paired(before.iloc[:1].copy())
        edited['category_subcategory'] = 'Other Income / Alternative'
        assert save(before.iloc[:1], edited, categories) == 1
        assert rows().iloc[0].category == 'Other Income'
        assert rows().iloc[0].subcategory == 'Alternative', 'old child not retained after category change'

        def rejected(baseline, edits, label):
            snapshot = rows()
            try:
                save(baseline, edits, categories)
            except (ValueError, db.ConcurrentTransactionEditError):
                pass
            else:
                raise AssertionError(label)
            pd.testing.assert_frame_equal(snapshot, rows())

        before = rows()
        invalid = paired(before)
        invalid.loc[0, 'category_subcategory'] = 'Income / Second'
        invalid.loc[1, 'category_subcategory'] = 'Charity / Alternative'
        rejected(before, invalid, 'invalid pair must reject entire batch')
        invalid.loc[1, 'category_subcategory'] = ''
        rejected(before, invalid, 'blank pair must reject')
        duplicate = pd.concat([paired(before.iloc[:1])] * 2)
        rejected(before.iloc[:1], duplicate, 'duplicate IDs must reject')
        wrong_id = paired(before.iloc[:1])
        wrong_id['id'] = 999
        rejected(before.iloc[:1], wrong_id, 'unknown IDs must reject')

        # The first update must roll back when a later optimistic lock fails.
        stale = before.copy()
        stale.loc[1, 'subcategory'] = 'First'
        edits = paired(stale)
        edits.loc[0, 'category_subcategory'] = 'Income / Second'
        edits.loc[1, 'category_subcategory'] = 'Income / Second'
        rejected(stale, edits, 'stale second row must roll back first row')
        print('PASS: invalid pairs, category dependency, duplicate identity and atomic stale-batch rollback')

        from reporting import income_charity_month_values, income_charity_percentage
        months = [pd.Period('2026-01', freq='M')]
        for frame in [initial, rows()]:
            scoped, monthly, _ = income_charity_month_values(frame, months)
            assert len(scoped) == scoped.id.nunique() == 3
            assert Decimal(str(monthly['Income'][months[0]])) == Decimal('300')
            assert Decimal(str(monthly['Charity'][months[0]])) == Decimal('-30')
            assert Decimal(str(income_charity_percentage(300, -30))) == Decimal('10')
        after = rows()
        protected = ['id','txn_date','amount','amount_usd','currency','account_name','bank','account_number',
                     'original_description','reviewed','status','row_hash']
        pd.testing.assert_frame_equal(initial[protected], after[protected])
        for frame in [after.iloc[:0], after[after.report_group == 'Income'], after[after.category == 'Charity']]:
            submit(frame, None, {})
        assert save(after, paired(after), categories) == 0
        with closing(safe_connect()) as conn, conn:
            schema_after = conn.execute("SELECT name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name").fetchall()
            assert conn.execute('SELECT COUNT(*) FROM transaction_memory').fetchone()[0] > 0
        assert schema_after == schema_before
        print('PASS: protected fields, financial totals/ratio, unique population, empty/single-type inputs, memory and schema')
        owners = {}
        for owner, connection in connections:
            owners[owner] = owners.get(owner, 0) + 1
            try:
                connection.execute('SELECT 1')
            except sqlite3.ProgrammingError as exc:
                assert 'closed' in str(exc).lower(), (owner, str(exc))
            else:
                raise AssertionError(f'Connection still open; owner={owner}')
        db.get_connection = original_connect
        print(f'PASS: all {len(connections)} connections explicitly closed; owners={owners}')
    assert not Path(folder).exists(), 'Temporary database directory must be removed normally'
    print('PASS: deterministic temporary-database cleanup completed')


if __name__ == '__main__':
    main()
