"""Synthetic review-only and classification-only save contract regressions."""
import ast
from contextlib import closing
from decimal import Decimal
import hashlib
import os
from pathlib import Path
import tempfile
import subprocess
from unittest.mock import patch

import pandas as pd

from _qa_pending_save_normalization import prepare_function


def without_reviewed_income_save(name, source):
    from _qa_import_history_reliability import without_import_history_reliability
    source = without_import_history_reliability(name, source)
    if name not in ('app.py', 'db.py'):
        return source
    baseline = subprocess.check_output(['git', 'show', '48bb7b098a3977041ba15852c0bd1b54f67e6f71:'+name]).replace(b'\r\n', b'\n')
    source = source.replace(b'\r\n', b'\n')
    if source == baseline:
        return source
    hashes = {
        'editable_pending_table': '71c2c329ca3dfcff984c15dba7d7720542f98cd82ff07974d632fff434eaba7e',
        '_prepare_pending_review_save_rows': '062f8a0059899c7bc25b5ff51e7f99dffa47b64f3e3e258318e659d92018cc99',
        '_clear_data_editor_state': '661b673dedbee929148d91f3ea1aad11868b3216f4850468e97b490d345fa734',
        'save_reviewed_rows': '1849c0a4823f27d83f11f818642311cdfd99ce10f0e3429fcc1a5523dddddbf5',
    }
    old_text, text = baseline.decode(), source.decode()
    old = {n.name:n for n in ast.parse(old_text).body if isinstance(n, ast.FunctionDef)}
    for node in ast.parse(text).body:
        if isinstance(node, ast.FunctionDef) and node.name in hashes:
            assert hashlib.sha256(ast.dump(node).encode()).hexdigest() == hashes[node.name], 'Unreviewed save code'
            current = ast.get_source_segment(source.decode(), node)
            text = text.replace(current, ast.get_source_segment(old_text, old[node.name]), 1)
    if name == 'app.py':
        current = 'No rows were ticked as Reviewed and no classification or Amount changes were detected. '
        assert text.count(current) == 1
        text = text.replace(current, 'No rows were ticked as Reviewed and no Amount corrections were detected. ', 1)
    assert text.encode() == baseline, 'Unrelated protected source changed'
    return baseline


def setup(db):
    db.init_db()
    db.add_category('Income', 'Original', 'Income')
    db.add_category('Projects', 'Work', 'Income')
    with closing(db.get_connection()) as conn:
        cur = conn.cursor()
        for i in range(1, 4):
            cur.execute('''INSERT INTO classified_transactions
                (id,row_hash,txn_date,amount,amount_usd,fx_rate,currency,category,subcategory,
                 original_description,reviewed,status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                (i, 'synthetic-review-'+str(i), '2026-01-01', '123.456', '123.456', '1',
                 'USD', 'Income', 'Original', 'Synthetic review fixture', 0, 'pending'))
        conn.commit()


def read(db):
    with closing(db.get_connection()) as conn:
        cur = conn.cursor()
        if db.USING_POSTGRES: cur.execute('SET LOCAL extra_float_digits=3')
        cur.execute('SELECT * FROM classified_transactions ORDER BY id')
        return pd.DataFrame(cur.fetchall(), columns=[c[0] for c in cur.description])


def exercise(db):
    prepare = prepare_function()
    setup(db)
    cats = db.get_categories(True)
    before = read(db)
    edited = before.copy()
    edited['category_subcategory'] = 'Income / Original'
    edited.loc[0, 'category_subcategory'] = 'Projects / Work'
    payload = prepare(before, edited, cats)
    assert payload.id.tolist() == [1], 'Classification-only change was discarded or untouched amounts rounded'
    assert not payload['_amount_changed'].any()
    observed = []
    original = db.get_connection
    class Cursor:
        def __init__(self, cur): self.cur = cur
        def __getattr__(self, n): return getattr(self.cur, n)
        def execute(self, query, params=None):
            if query.lstrip().startswith('UPDATE classified_transactions'): observed.append(query)
            return self.cur.execute(query, params) if params is not None else self.cur.execute(query)
    class Connection:
        def __init__(self): self.conn = original()
        def __getattr__(self, n): return getattr(self.conn, n)
        def cursor(self): return Cursor(self.conn.cursor())
    with patch.object(db, 'get_connection', Connection):
        assert db.save_reviewed_rows(payload) == 1
    assert observed and all('amount' not in q.split('WHERE')[0] for q in observed), 'Classification rewrites money'
    after = read(db)
    financial = ['amount','amount_usd','fx_rate','split_original_amount','original_description','currency','row_hash']
    pd.testing.assert_frame_equal(before[financial], after[financial])
    assert after.iloc[0].category == 'Projects'
    edited = after.copy()
    edited['reviewed'] = edited['reviewed'].astype(bool)
    edited['category_subcategory'] = ['Projects / Work', 'Income / Original', 'Income / Original']
    edited.loc[:1, 'reviewed'] = True
    payload = prepare(after, edited, cats)
    assert payload.id.tolist() == [1,2]
    with patch.object(db, 'get_connection', Connection):
        assert db.save_reviewed_rows(payload) == 2
    assert all('amount' not in q.split('WHERE')[0] for q in observed)
    fresh = read(db)
    assert fresh.reviewed.tolist() == [1,1,0] and fresh.status.tolist() == ['reviewed','reviewed','pending']
    assert db.get_pending_transactions().id.tolist() == [3]
    pd.testing.assert_frame_equal(before[financial], fresh[financial])
    no_op = payload.copy()
    no_op['_expected_reviewed'] = True
    no_op['_expected_status'] = 'reviewed'
    from _qa_split_editing import database_snapshot
    no_op_before = database_snapshot(db)
    assert db.save_reviewed_rows(no_op) == 2
    assert database_snapshot(db) == no_op_before, 'No-op Save rewrites reviewed state'
    try: db.save_reviewed_rows(payload)
    except db.ConcurrentTransactionEditError: pass
    else: raise AssertionError('Stale review snapshot accepted')
    unchanged = fresh.copy()
    unchanged['category_subcategory'] = ['Projects / Work', 'Income / Original', 'Income / Original']
    unchanged['reviewed'] = False
    assert prepare(fresh.iloc[2:], unchanged.iloc[2:], cats).empty
    import reporting
    scoped = reporting._assign_report_groups(fresh, cats)
    income = reporting.income_charity_scope(scoped)
    assert set(income.id) == {1,2,3} and income.id.is_unique
    assert sum(map(lambda v: Decimal(str(v)), income.amount), Decimal(0)) == Decimal('370.368')
    assert set(scoped.report_group) == {'Income'}
    # An injected second-row failure must roll back rows and audit together.
    batch = pd.DataFrame([dict(id=i, category='Income', subcategory='Original', reviewed=True,
                              _expected_category=fresh.iloc[i-1].category,
                              _expected_subcategory=fresh.iloc[i-1].subcategory,
                              _expected_reviewed=fresh.iloc[i-1].reviewed) for i in (1,3)])
    audit = db._audit_transaction_change
    def fail(cur, tx, *args):
        if tx == 3: raise RuntimeError('PRIVATE_SYNTHETIC_CANARY')
        return audit(cur, tx, *args)
    from _qa_split_editing import database_snapshot
    snapshot = database_snapshot(db)
    with patch.object(db, '_audit_transaction_change', fail):
        try: db.save_reviewed_rows(batch)
        except Exception as error: assert 'PRIVATE_SYNTHETIC_CANARY' not in str(error)
        else: raise AssertionError('Injected failure accepted')
    assert database_snapshot(db) == snapshot
    # Income submits its displayed status. A later exclusion must not be
    # overwritten or silently accepted as a successful classification save.
    status_snapshot = pd.DataFrame([dict(id=1, category='Income', subcategory='Original',
        reviewed=True, status='reviewed', _expected_category='Projects',
        _expected_subcategory='Work', _expected_reviewed=True)])
    with closing(db.get_connection()) as conn:
        conn.cursor().execute('UPDATE classified_transactions SET status=? WHERE id=1', ('excluded',))
        conn.commit()
    excluded = database_snapshot(db)
    try: db.save_reviewed_rows(status_snapshot, income_edit=True)
    except db.ConcurrentTransactionEditError: pass
    else: raise AssertionError('Income Save accepted a stale excluded status')
    assert database_snapshot(db) == excluded


def ui_flow(db, source=None):
    from _qa_income_charity_edit import load_functions
    from _qa_split_editing import UI
    prepare = prepare_function()
    env = prepare.__globals__.copy()
    class PendingUI(UI):
        def data_editor(self, frame, **kwargs):
            self.controls.append(kwargs['key'])
            return super().data_editor(frame, **kwargs)
    ui = PendingUI()
    env.update(st=ui, hashlib=hashlib, get_categories=db.get_categories)
    names = {'editable_pending_table', '_with_category_pair_column', '_category_pair_options',
             '_editor_row_signature', '_scoped_editor_key', '_clear_data_editor_state'}
    load_functions(source or Path('app.py').read_text(encoding='utf-8'), names, env)
    table = env['editable_pending_table']
    frame = read(db)
    frame['suggested_category'] = ''
    frame['suggested_subcategory'] = ''
    first = table(frame, ['Income','Projects'], [], 'qa_pending', defer_changes=True)
    # Same identities, different DB order/data: do not reset widget state or snapshot.
    rerun = frame.iloc[::-1].copy()
    rerun['category'] = 'Projects'
    second = table(rerun, ['Income','Projects'], [], 'qa_pending', defer_changes=True)
    assert ui.controls[0] == ui.controls[1], 'Pending widget key changes with row order'
    pd.testing.assert_frame_equal(first, second)
    ui.edit = lambda f: f.assign(reviewed=[True, True, False])
    edited = table(rerun, ['Income','Projects'], [], 'qa_pending', defer_changes=True)
    payload = prepare(rerun, edited, db.get_categories(True))
    assert payload.id.tolist() == [1,2] and payload._expected_category.eq('Income').all()
    assert db.save_reviewed_rows(payload) == 2
    env['_clear_data_editor_state'](ui.controls[-1])
    assert not any(k.endswith('__pending_baseline') for k in ui.session_state)
    assert read(db).reviewed.tolist() == [1,1,0]
    assert db.get_pending_transactions().id.tolist() == [3]
    # Fresh session/table and no selections: the empty-selection path is legitimate.
    ui.session_state = {}
    ui.edit = None
    remaining = frame.iloc[2:].copy()
    assert prepare(remaining, table(remaining, ['Income','Projects'], [], 'qa_pending', True), db.get_categories(True)).empty
    print('PASS headless actual Pending editor: stable IDs, frozen snapshot, batch selection, save, new session')


def main():
    import db
    assert not db.USING_POSTGRES and Path(os.environ['TEMP']).drive.upper() == 'E:'
    with tempfile.TemporaryDirectory(dir=os.environ['TEMP']) as root, patch.object(db, 'DB_PATH', str(Path(root)/'qa.sqlite')):
        exercise(db)
    with tempfile.TemporaryDirectory(dir=os.environ['TEMP']) as root, patch.object(db, 'DB_PATH', str(Path(root)/'ui.sqlite')):
        setup(db)
        ui_flow(db)
    print('PASS SQLite review/classification/no-op/stale/rollback/membership')
    import psycopg2
    options = dict(host='127.0.0.1', port=int(os.environ['ARETI_QA_PG_PORT']), user='qa_local', sslmode='disable')
    admin = psycopg2.connect(dbname='postgres', **options)
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute('SHOW data_directory')
            assert Path(cur.fetchone()[0]).resolve() == Path(os.environ['ARETI_QA_PG_DATA']).resolve()
        for kind in ('REAL','DOUBLE PRECISION','NUMERIC'):
            name = 'qa_reviewed_' + os.urandom(6).hex()
            with admin.cursor() as cur: cur.execute('CREATE DATABASE ' + name)
            def connect():
                return db.PostgresConnection(psycopg2.connect(dbname=name, options='-c extra_float_digits=0', **options))
            try:
                with patch.object(db, 'USING_POSTGRES', True), patch.object(db, 'get_connection', connect):
                    db.init_db()
                    if kind != 'REAL':
                        with closing(connect()) as conn:
                            for column in ('amount','amount_usd','fx_rate','split_original_amount'):
                                conn.cursor().execute('ALTER TABLE classified_transactions ALTER COLUMN '+column+' TYPE '+kind)
                            conn.commit()
                    exercise(db)
                    if kind == 'REAL':
                        # Simulate the actual driver/display round-trip at server precision 0.
                        with closing(connect()) as conn:
                            cur = conn.cursor()
                            cur.execute('UPDATE classified_transactions SET amount=?,amount_usd=? WHERE id=3', ('789123.37','789123.37'))
                            conn.commit()
                        stored = read(db)
                        with closing(connect()) as conn:
                            cur = conn.cursor()
                            cur.execute('SELECT * FROM classified_transactions WHERE id=3')
                            displayed = pd.DataFrame(cur.fetchall(), columns=[c[0] for c in cur.description])
                        assert Decimal(str(displayed.iloc[0].amount)) != Decimal(str(stored.iloc[2].amount))
                        edited = displayed.copy()
                        edited['category_subcategory'] = 'Income / Original'
                        edited['reviewed'] = True
                        payload = prepare_function()(displayed, edited, db.get_categories(True))
                        assert not payload['_amount_changed'].any()
                        assert db.save_reviewed_rows(payload) == 1, 'Display-only numeric loss blocked review-only Save'
                        assert read(db).iloc[2].amount == stored.iloc[2].amount
                    print('PASS', kind, 'reduced-output precision, review, classifications, exact stored-money preservation')
            finally:
                with admin.cursor() as cur: cur.execute('DROP DATABASE ' + name)
    finally: admin.close()
    for name in ('app.py','db.py'):
        source = Path(name).read_bytes()
        without_reviewed_income_save(name, source)
        try: without_reviewed_income_save(name, source+b'\nUNAUTHORIZED=True\n')
        except AssertionError: pass
        else: raise AssertionError('Protected source guard weakened')


if __name__ == '__main__': main()
