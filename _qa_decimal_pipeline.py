"""Synthetic exact-money boundaries; never uses an application database."""
from decimal import Decimal
from io import BytesIO
import os


def main():
    for key in ('DATABASE_URL', 'POSTGRES_URL'):
        os.environ.pop(key, None)
    import pandas as pd
    import openpyxl
    import db
    import parsing
    from report_money import decimal_sum
    import ast
    from pathlib import Path
    from financial_storage_qa import protected_source, FUNCTIONS
    for name, allowed in FUNCTIONS.items():
        source = Path(name).read_bytes()
        protected_source(name, source)
        for authorized_function in (True, False):
            tree = ast.parse(source)
            node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                        and (n.name in allowed) == authorized_function)
            node.body.append(ast.Pass())
            try:
                protected_source(name, ast.unparse(tree).encode())
            except AssertionError:
                pass
            else:
                raise AssertionError('Source guard accepted an unreviewed function mutation')
        try:
            protected_source(name, source + b'\nUNAPPROVED_MUTATION = True\n')
        except AssertionError:
            pass
        else:
            raise AssertionError('Source guard accepted unrelated module code')

    values = ('712345.30', '-712345.30', '9007199254740991.37',
              '-0.000000000000000000123456789', '1.234567890123456789')
    for text in values:
        expected = Decimal(text)
        parsed = parsing._parse_amount(expected)
        assert isinstance(parsed, Decimal) and parsed == expected, 'Parser lost exact money'
        assert db._float_or_none(text) == expected, 'Database boundary lost exact money'
    assert decimal_sum([Decimal('1E40'), Decimal('0.01'), Decimal('-1E40')]) == Decimal('0.01')
    assert db._usd_from_amount('123.45', '1.234567890123456789') == Decimal('152.41')
    exported = db.dataframe_to_excel_bytes({'Financial': pd.DataFrame({'amount': [Decimal(v) for v in values]})})
    workbook = openpyxl.load_workbook(BytesIO(exported), read_only=True, data_only=False)
    try:
        cells = list(workbook.active.iter_rows(min_row=2, max_col=1))
        assert [r[0].value for r in cells] == [str(Decimal(v)) for v in values], 'Excel export changed exact values'
        assert all(r[0].data_type == 's' for r in cells), 'Excel numeric cells truncate precision'
    finally:
        workbook.close()
    print('PASS Decimal parser, parameter, sum, FX and exact-text export boundaries')


if __name__ == '__main__':
    main()
