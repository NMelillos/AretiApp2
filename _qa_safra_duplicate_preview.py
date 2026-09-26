"""Execute the actual Import UI with synthetic PDFs and deny-by-default DB writes."""
import ast
from contextlib import ExitStack, closing
import hashlib
from io import BytesIO
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
from unittest.mock import patch

import pandas as pd
import parsing
from _qa_safra_completion import fixture
from _qa_safra_lifecycle import PDF, Stop, UI, upload
from _qa_safra_uat import labelled_accounts


def without_duplicate_preview(name, source):
    from _qa_split_editing import without_split_editing
    source = without_split_editing(name, source)
    source = source.replace(b'\r\n', b'\n')
    additions = {
        'app.py': ('            from safra_duplicate_preview import is_existing_safra, render_preview\n'
                   '            if is_existing_safra(statement_hash):\n'
                   '                render_preview(st, file_bytes, uploaded_statement.name, accounts, parse_statement)\n'
                   '                st.stop()\n'),
        'db.py': ('    from safra_duplicate_preview import is_existing_safra\n'
                  '    if is_existing_safra(statement_hash):\n'
                  '        return 0, True, 0\n'),
    }
    addition = additions.get(name, '').encode()
    if not addition or addition not in source:
        return source
    assert source.count(addition) == 1
    restored = source.replace(addition, b'', 1)
    baseline = subprocess.check_output(['git', 'show', 'da7b064bf110f64b2a64e0f0702485a9e84d52e7:' + name]).replace(b'\r\n', b'\n')
    assert restored == baseline, 'Change outside exact duplicate preview dispatch/guard'
    return restored


class PreviewUI(UI):
    def __init__(self, action='', content=b'synthetic duplicate preview'):
        super().__init__(False, content)
        self.action = action
        self.buttons = []

    def button(self, label, **kwargs):
        self.buttons.append(label)
        return label == self.action


def run_ui(db, pages, action='', content=b'synthetic duplicate preview'):
    ui = PreviewUI(action, content)
    tree = ast.parse(Path('app.py').read_text(encoding='utf-8'))
    branch = next(n for n in tree.body if isinstance(n, ast.If) and ast.unparse(n.test) == "page == 'Import'")
    env = dict(db.__dict__, st=ui, pd=pd, BytesIO=BytesIO)
    names = {'parse_statement', 'parse_statement_balance', 'classify_statement_rows', 'flag_duplicates', 'missing_setup_items'}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    for n in nodes:
        n.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'app.py', 'exec'), env)
    env['get_rates'] = lambda: pd.DataFrame([{'rate_type': 'USD/USD'}])
    with patch.object(parsing.pdfplumber, 'open', return_value=PDF(pages)), patch.object(
            parsing, '_parse_safra_pages', wraps=parsing._parse_safra_pages) as parser:
        try:
            exec(compile(ast.Module(body=branch.body, type_ignores=[]), 'app.py', 'exec'), env)
        except Stop:
            pass
        return ui, parser.call_count


def main():
    import db
    assert not db.USING_POSTGRES and Path(os.environ['TEMP']).drive.upper() == 'E:'
    pages = fixture()
    parsed = parsing._parse_safra_pages(pages)
    accounts = labelled_accounts(parsed)
    content = b'synthetic duplicate preview'
    digest = db.build_statement_hash(content)
    with tempfile.TemporaryDirectory(dir=os.environ['TEMP']) as folder, patch.object(
            db, 'DB_PATH', str(Path(folder) / 'preview.sqlite')), patch.object(db, 'get_accounts', return_value=accounts):
        db.init_db()
        db.add_category('Synthetic', 'General', 'Synthetic')
        with closing(db.get_connection()) as conn:
            conn.execute('INSERT INTO rates (rate_month,rate_type,rate_value) VALUES (?,?,?)',
                         ('2026-01-01', 'GBP/USD', 1.25))
            conn.commit()
        imported = upload(db, pages, True, content)
        assert not imported.errors and len(db.get_all_transactions()) == 9, imported.errors
        path = Path(db.DB_PATH)
        before_bytes = hashlib.sha256(path.read_bytes()).hexdigest()
        with closing(db.get_connection()) as conn:
            before_dump = '\n'.join(conn.iterdump())
        real_connection = db.get_connection
        writes = []

        def read_connection():
            conn = real_connection()
            # Only SQL reads are permitted, including inside nominally read helpers.
            def authorize(action, *args):
                if action not in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION):
                    writes.append(action)
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            conn.set_authorizer(authorize)
            return conn

        with ExitStack() as stack:
            stack.enter_context(patch.object(db, 'get_connection', side_effect=read_connection))
            # Deny every public DB API unless it is an explicitly required reader
            # or a pure identity helper. New mutators are denied automatically.
            allowed = {'get_connection', 'get_accounts', 'get_categories', 'get_rates',
                       'statement_already_imported', 'build_statement_hash'}
            spies = []
            for name, obj in list(vars(db).items()):
                if (not name.startswith('_') and name not in allowed
                        and getattr(obj, '__module__', None) == 'db' and callable(obj)
                        and not isinstance(obj, type)):
                    spies.append(stack.enter_context(patch.object(db, name,
                        side_effect=AssertionError('Forbidden DB API: ' + name))))
            ui, calls = run_ui(db, pages)
            assert 'Validate existing statement in read-only preview' in ui.buttons
            assert calls == 0 and not ui.errors
            for action in ('Validate existing statement in read-only preview', '', 'Cancel',
                           'Import to pending review', 'Validate existing statement in read-only preview'):
                ui, calls = run_ui(db, pages, action)
                assert 'Import to pending review' not in ui.buttons
                assert not ui.errors, ui.errors
                assert calls == int(action == 'Validate existing statement in read-only preview')
                if calls:
                    table = ui.tables[0]
                    assert len(table) == 6
                    assert table.IBAN.tolist() == [s['source_iban'] for s in parsed.attrs['safra_sections']]
                    assert table.Currency.tolist() == ['USD', 'EUR', 'GBP', 'USD', 'EUR', 'GBP']
                    assert table.Transactions.tolist() == [4, 0, 4, 0, 0, 1]
                    assert table['Validation result'].eq('Passed').all()
                    assert 'Booking currencies' in table
            bad = [p.replace('Balance in GBP', 'Balance in USD') for p in pages]
            ui, calls = run_ui(db, bad, 'Validate existing statement in read-only preview')
            assert calls == 1 and ui.errors and not ui.tables
            assert all(not spy.called for spy in spies)
        # Direct and stripped/crafted frame calls are rejected before any SQL write.
        with patch.object(db, 'get_connection', side_effect=read_connection):
            for frame in (parsed.copy(), pd.DataFrame([{'Amount': 1}])):
                assert db.save_pending_transactions(frame, 'synthetic.pdf', digest) == (0, True, 0)
                with closing(read_connection()) as conn:
                    assert db.save_pending_transactions(frame, 'synthetic.pdf', digest, _connection=conn) == (0, True, 0)
        assert not writes
        with closing(real_connection()) as conn:
            assert '\n'.join(conn.iterdump()) == before_dump
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before_bytes
    for name in ('app.py', 'db.py'):
        raw = Path(name).read_bytes()
        without_duplicate_preview(name, raw)
        try:
            without_duplicate_preview(name, raw + b'\nUNAUTHORIZED = True\n')
        except AssertionError:
            pass
        else:
            raise AssertionError('Compatibility guard accepted unrelated code')
    print('PASS duplicate Safra actual UI, six accounts, parser, cancellation/rerun/failure, direct import rejection, deny-by-default APIs/SQL and byte-identical DB')


if __name__ == '__main__':
    main()
