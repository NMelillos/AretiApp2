"""Read tabular source numerics without an intermediate financial float."""
from io import BytesIO
import posixpath
from xml.etree import ElementTree
from zipfile import ZipFile, BadZipFile

from financial_decimal import decimal_value


def read_excel_exact(uploaded_file, header=0):
    import openpyxl
    import pandas as pd
    uploaded_file.seek(0)
    content = uploaded_file.read()
    ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    try:
        with ZipFile(BytesIO(content)) as archive:
            document = ElementTree.fromstring(archive.read('xl/workbook.xml'))
            sheet = document.find('s:sheets/s:sheet', ns)
            relation = sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
            relationships = ElementTree.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
            target = next(r.attrib['Target'] for r in relationships if r.attrib['Id'] == relation)
            target = posixpath.normpath(target.lstrip('/') if target.startswith('/') else 'xl/' + target)
            if not target.startswith('xl/'):
                raise ValueError('Unsupported workbook relationship')
            cells = ElementTree.fromstring(archive.read(target)).findall('.//s:sheetData/s:row/s:c', ns)
            lexical = {}
            for cell in cells:
                value = cell.find('s:v', ns)
                if cell.find('s:f', ns) is not None and (value is None or value.text is None):
                    raise ValueError('Workbook formula has no cached source value')
                if cell.attrib.get('t', 'n') == 'n' and value is not None and value.text is not None:
                    lexical[cell.attrib['r']] = value.text
    except BadZipFile:
        raise ValueError('Exact financial input requires XLSX or CSV; legacy binary Excel needs source review') from None
    workbook = openpyxl.load_workbook(BytesIO(content), read_only=False, data_only=True, keep_links=False)
    try:
        rows = [[decimal_value(lexical[cell.coordinate])
                 if cell.coordinate in lexical and not cell.is_date else cell.value
                 for cell in row] for row in workbook.worksheets[0].iter_rows()]
    finally:
        workbook.close()
    while rows and all(value is None for value in rows[-1]):
        rows.pop()
    if header is None:
        return pd.DataFrame(rows, dtype=object)
    if header != 0:
        raise ValueError('Unsupported workbook header position')
    if not rows:
        return pd.DataFrame()
    names, seen = [], set()
    for index, value in enumerate(rows[0]):
        base = f'Unnamed: {index}' if value is None else value
        name, suffix = base, 1
        while name in seen:
            name = f'{base}.{suffix}'
            suffix += 1
        names.append(name)
        seen.add(name)
    return pd.DataFrame(rows[1:], columns=names, dtype=object)
