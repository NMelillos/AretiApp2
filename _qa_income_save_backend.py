"""Exercise classification Save against isolated SQLite and PostgreSQL."""
import ast
from contextlib import closing
from decimal import Decimal
import hashlib
import os
from pathlib import Path
import tempfile
import subprocess
from unittest.mock import Mock

import pandas as pd

from _qa_income_charity_edit import UI, Rerun, load_functions


def without_real_guard_casts(source):
    source = source.replace(b'\r\n', b'\n')
    baseline = subprocess.check_output(['git', 'show', '9410204dbff4f5f614f06754bb9727e5e808e698:db.py']).replace(b'\r\n', b'\n')
    for name in ('amount', 'amount_usd', 'fx_rate'):
        new = f'AND {name} IS NOT DISTINCT FROM CAST(? AS REAL)'.encode()
        old = f'AND {name} IS NOT DISTINCT FROM ?'.encode()
        assert source.count(new) == 1
        source = source.replace(new, old, 1)
    assert source == baseline, 'Change outside the three exact REAL comparison casts'
    return source


def exercise(db, backend):
    db.init_db()
    for category, subcategory, group in [('Income', 'First', 'Income'),
            ('Income', 'Second', 'Income'), ('Other Income', 'Third', 'Income'),
            ('Charity', 'First', 'Family expenses'), ('Charity', 'Second', 'Family expenses')]:
        db.add_category(category, subcategory, group)
    with closing(db.get_connection()) as conn:
        cur = conn.cursor()
        for row_id, category, amount in [(101, 'Income', 123.45), (102, 'Income', 234.56),
                                         (103, 'Charity', -35.801)]:
            cur.execute('''INSERT INTO classified_transactions
                (id,row_hash,txn_date,amount,amount_usd,fx_rate,currency,category,subcategory,
                 original_description,account_name,reviewed,status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (row_id, f'synthetic-{row_id}', '2026-01-01', amount, amount, 1.2345,
                 'USD', category, 'First', 'Synthetic fixture', 'Synthetic holder', 1, 'reviewed'))
        conn.commit()

    def rows():
        from reporting import _prepare_report_data
        with closing(db.get_connection()) as conn:
            cur = conn.cursor()
            cur.execute('SELECT * FROM classified_transactions ORDER BY id')
            frame = pd.DataFrame(cur.fetchall(), columns=[c[0] for c in cur.description])
        return _prepare_report_data(frame, db.get_categories(include_subcategories=True),
                                    include_all_valid=True)[1]

    def raw():
        with closing(db.get_connection()) as conn:
            cur = conn.cursor()
            cur.execute('SELECT id,amount,amount_usd,fx_rate,currency,txn_date,original_description,account_name,reviewed,status FROM classified_transactions ORDER BY id')
            return cur.fetchall()

    ui = UI()
    env = {'pd': pd, 'hashlib': hashlib, 'st': ui,
           '_CATEGORY_PAIR_COLUMN': 'category_subcategory', '_NO_SUBCATEGORY_LABEL': 'No subcategory',
           'save_reviewed_rows': db.save_reviewed_rows, 'get_categories': db.get_categories,
           '_executive_signed_amount_series': lambda frame: frame.amount_usd,
           '_clear_transaction_read_caches': Mock()}
    names = {'_category_pair_label', '_parse_category_pair_label', '_category_pair_options',
             '_with_category_pair_column', '_editor_row_signature', '_scoped_editor_key',
             '_save_income_charity_edits', '_render_income_charity_editor', '_render_income_charity_transactions'}
    load_functions(Path('app.py').read_text(encoding='utf-8'), names, env)
    render = env['_render_income_charity_transactions']
    save = env['_save_income_charity_edits']
    paired = env['_with_category_pair_column']
    cats = db.get_categories(include_subcategories=True)
    protected = raw()

    def submit(frame, action, replacements):
        ui.action = action
        def edit(display):
            for row_id, pair in replacements.items():
                display.loc[display.id == row_id, 'category_subcategory'] = pair
            return display
        ui.transform = edit
        try:
            render(frame, editable=True)
        except Rerun:
            pass
        assert not ui.errors, f'{backend}: actual report Save failed: {ui.errors}'

    original = rows()
    for row_id, category in [(101, 'Income'), (103, 'Charity')]:
        frame = rows().query('id == @row_id')
        submit(frame, None, {})
        submit(frame.copy(), None, {})
        submit(frame, 'Save', {row_id: f'{category} / Second'})
        assert rows().set_index('id').loc[row_id, 'subcategory'] == 'Second'
    print(f'PASS {backend}: immediate Income/Charity Save, ordinary rerun, fresh connection')
    frame = rows().query('category == "Income"').iloc[::-1]
    submit(frame, 'Cancel', {101: 'Other Income / Third'})
    assert rows().set_index('id').loc[101, 'category'] == 'Income'
    submit(frame, 'Save', {101: 'Other Income / Third', 102: 'Other Income / Third'})
    current = rows().query('id in [101,102]')
    assert current.category.eq('Other Income').all()
    assert save(current, paired(current), cats) == 0
    assert raw() == protected
    print(f'PASS {backend}: Cancel, reversed row order, stable IDs, batch, duplicate submission')
    before = rows()
    with closing(db.get_connection()) as conn:
        cur = conn.cursor()
        cur.execute('UPDATE classified_transactions SET category=?,subcategory=? WHERE id=?', ('Income', 'First', 102))
        conn.commit()
    edited = paired(before)
    edited.loc[edited.id.isin([101,102]), 'category_subcategory'] = 'Income / Second'
    try:
        save(before, edited, cats)
    except db.ConcurrentTransactionEditError:
        pass
    else:
        raise AssertionError('A genuinely stale second row must reject the complete batch')
    after = rows().set_index('id')
    assert after.loc[101, 'category'] == 'Other Income'
    assert after.loc[102, 'subcategory'] == 'First'
    unrelated = before.query('id == 103')
    change = paired(unrelated)
    change['category_subcategory'] = 'Charity / First'
    assert save(unrelated, change, cats) == 1
    assert raw() == protected
    from reporting import income_charity_month_values, income_charity_percentage
    month = pd.Period('2026-01', freq='M')
    old = income_charity_month_values(original, [month])[1]
    new = income_charity_month_values(rows(), [month])[1]
    assert old == new
    assert Decimal(str(income_charity_percentage(new['Income'][month], new['Charity'][month]))) == Decimal('10')
    print(f'PASS {backend}: genuine stale conflict, batch rollback, unrelated external update, Decimal totals/ratio')
    # Equivalent transport representations must not change the guarded classification.
    for reviewed in (True, 1, 1.0):
        snapshot = rows().query('id == 103').copy()
        snapshot['reviewed'] = reviewed
        snapshot['category'] = ' Charity '
        snapshot['txn_date'] = pd.Timestamp('2026-01-01')
        snapshot['amount'] = Decimal('-35.801')
        snapshot['updated_at'] = pd.Timestamp('2026-01-02T12:00:00')
        edit = paired(snapshot)
        target = 'Second' if snapshot.iloc[0].subcategory == 'First' else 'First'
        edit['category_subcategory'] = 'Charity / ' + target
        assert save(snapshot, edit, cats) == 1
    db.add_category('Charity', '', 'Family expenses')
    cats = db.get_categories(include_subcategories=True)
    with closing(db.get_connection()) as conn:
        cur = conn.cursor()
        cur.execute('UPDATE classified_transactions SET subcategory=NULL WHERE id=?', (103,))
        conn.commit()
    snapshot = rows().query('id == 103')
    assert snapshot.iloc[0].subcategory == ''
    edit = paired(snapshot)
    edit['category_subcategory'] = 'Charity / First'
    assert save(snapshot, edit, cats) == 1
    print(f'PASS {backend}: whitespace, null/empty subcategory, bool/int/float flag, date/Decimal/timestamp representations')
    # An actual change after the reread must still fail the SQL compare-and-swap.
    original_connection = db.get_connection
    from unittest.mock import patch
    raced = [False]
    class Cursor:
        def __init__(self, wrapped):
            self.wrapped = wrapped
        def __getattr__(self, name):
            return getattr(self.wrapped, name)
        def execute(self, sql, params=None):
            if 'UPDATE classified_transactions' in sql and 'IS NOT DISTINCT FROM' in sql and not raced[0]:
                raced[0] = True
                with closing(original_connection()) as other:
                    other.cursor().execute('UPDATE classified_transactions SET amount=? WHERE id=?', (124.5, 101))
                    other.commit()
            return self.wrapped.execute(sql, params) if params is not None else self.wrapped.execute(sql)
    class Connection:
        def __init__(self):
            self.wrapped = original_connection()
        def __getattr__(self, name):
            return getattr(self.wrapped, name)
        def cursor(self):
            return Cursor(self.wrapped.cursor())
    snapshot = rows().query('id == 101')
    edit = paired(snapshot)
    edit['category_subcategory'] = 'Income / Second'
    with patch.object(db, 'get_connection', Connection):
        try:
            save(snapshot, edit, cats)
        except db.ConcurrentTransactionEditError:
            pass
        else:
            raise AssertionError('Concurrent numeric change must reject classification Save')
    latest = rows().set_index('id')
    assert Decimal(str(latest.loc[101, 'amount'])) == Decimal('124.5')
    assert latest.loc[101, 'category'] == snapshot.iloc[0].category
    print(f'PASS {backend}: external financial change between reread and update is protected')


def main():
    root = Path(os.environ['TEMP']).resolve()
    assert root.drive.upper() == 'E:'
    for key in list(os.environ):
        if any(p in key.upper() for p in ('DATABASE', 'POSTGRES', 'SUPABASE')) or key.upper().startswith('PG'):
            del os.environ[key]
    import db
    assert not db.USING_POSTGRES
    with tempfile.TemporaryDirectory(prefix='save-backend-', dir=root) as folder:
        db.DB_PATH = str(Path(folder) / 'synthetic.sqlite')
        exercise(db, 'SQLite')
    import psycopg2
    # Explicit opt-in to an independently verified, loopback-only disposable cluster.
    expected = Path(os.environ['ARETI_QA_PG_DATA']).resolve()
    assert expected.is_relative_to(root)
    port = int(os.environ['ARETI_QA_PG_PORT'])
    connection = psycopg2.connect(host='127.0.0.1', port=port, user='qa_local', dbname='postgres', sslmode='disable')
    connection.autocommit = True
    schema = 'qa_income_' + os.urandom(6).hex()
    try:
        with connection.cursor() as cur:
            cur.execute('SHOW data_directory')
            assert Path(cur.fetchone()[0]).resolve() == expected
            cur.execute('CREATE DATABASE ' + schema)
        def local_connection():
            raw = psycopg2.connect(host='127.0.0.1', port=port, user='qa_local', dbname=schema, sslmode='disable')
            return db.PostgresConnection(raw)
        db.USING_POSTGRES = True
        db.get_connection = local_connection
        exercise(db, 'PostgreSQL')
    finally:
        with connection.cursor() as cur:
            cur.execute('DROP DATABASE ' + schema)
        connection.close()


if __name__ == '__main__':
    main()
