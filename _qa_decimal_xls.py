"""Real synthetic BIFF workbook must never enter a monetary write path."""
from io import BytesIO
from unittest.mock import patch
import os


def main():
    for key in ('DATABASE_URL', 'POSTGRES_URL'):
        os.environ.pop(key, None)
    import xlwt  # QA-only fixture writer; not a production dependency.
    import db
    import parsing
    from financial_tabular import read_excel_exact
    workbook = xlwt.Workbook()
    sheet = workbook.add_sheet('Synthetic')
    for col, name in enumerate(('Date', 'Description', 'Amount')):
        sheet.write(0, col, name)
    sheet.write(1, 0, '2026-01-01')
    sheet.write(1, 1, 'Synthetic only')
    sheet.write(1, 2, 123.45)
    payload = BytesIO()
    workbook.save(payload)
    assert payload.getvalue().startswith(bytes.fromhex('D0CF11E0A1B11AE1'))
    with patch.object(db, 'get_connection', side_effect=AssertionError('Database access forbidden')):
        for action in (read_excel_exact, parsing.parse_excel, db.replace_rates_from_excel,
                       db.import_database_updates_from_excel):
            try:
                action(BytesIO(payload.getvalue()))
            except ValueError as error:
                assert 'legacy binary Excel' in str(error), str(error)
            else:
                raise AssertionError('Legacy monetary XLS was not rejected')
    print('PASS genuine legacy XLS fails closed before database access; no guessed cents')


if __name__ == '__main__':
    main()
