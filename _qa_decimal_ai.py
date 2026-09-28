"""Exercise real analysis bodies without starting Streamlit or calling a provider."""
import ast
from decimal import Decimal
from pathlib import Path
import subprocess
import pandas as pd


def load(source):
    names = {'_build_family_analysis', '_build_reporting_group_analysis', '_largest_change_rows',
             '_format_analysis_bullets', '_analysis_rows_as_text', '_build_ai_report_data_context',
             '_build_custom_reporting_group_analysis'}
    tree = ast.parse(source)
    scope = {'pd': pd, '_money': lambda v: f'{v:.2f}',
             '_percent': lambda v: f'{v:.1f}%',
             '_executive_signed_amount_series': lambda f: f['expense_usd'],
             '_executive_status_delta': lambda a, b: a-b,
             '_run_custom_ai_prompt': lambda prompt, context: (prompt, context)}
    exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef)
                                 and n.name in names], type_ignores=[]), '<analysis>', 'exec'), scope)
    return scope


def main():
    old = load(subprocess.check_output(['git', 'show', 'HEAD:app.py']))
    new = load(Path('app.py').read_bytes())
    frame = pd.DataFrame([dict(report_group='1-family', month='2026-01', category='A',
                               subcategory='B', expense_usd=Decimal('12.50'))])
    args = (frame, ['2026-01'], {}, 'Keep this instruction')
    before, _ = old['_build_family_analysis'](*args)
    after, exported = new['_build_family_analysis'](*args)
    assert before == after, 'Narrative or instructions changed for exact ordinary values'
    assert isinstance(exported.iloc[0]['Amount'], Decimal), 'Financial export coerces to float'
    frame.loc[0, 'expense_usd'] = Decimal('9007199254740991.37')
    _, exported = new['_build_family_analysis'](frame, ['2026-01'], {})
    assert exported.iloc[0]['Amount'] == frame.iloc[0]['expense_usd']
    frame['txn_date'] = '2026-01-01'
    frame['full_statement_description'] = 'Synthetic reference'
    prompt, context = new['_build_reporting_group_analysis'](frame, ['2026-01'], {}, '1-family', 'Approved prompt')
    assert prompt == 'Approved prompt' and '9007199254740991.37' in context
    assert new['_build_reporting_group_analysis'](frame, [], {}, '1-family', '') == old['_build_reporting_group_analysis'](frame, [], {}, '1-family', '')
    values = [Decimal('10.25'), Decimal('20.50'), Decimal('0'), Decimal('-3.25')]
    sample = pd.concat([frame] * len(values), ignore_index=True)
    sample['expense_usd'] = values
    sample['month'] = ['2026-01', '2026-02', '2026-01', '2026-02']
    sample['category'] = ['A', 'A', 'B', 'B']
    args = (sample, ['2026-01', '2026-02'], {}, 'Approved prompt')
    old_text, old_rows = old['_build_family_analysis'](*args)
    new_text, new_rows = new['_build_family_analysis'](*args)
    assert old_text == new_text
    for column in ('Section', 'Category', 'Subcategory', 'Comment'):
        assert old_rows[column].tolist() == new_rows[column].tolist()
    assert all(isinstance(v, Decimal) for v in new_rows['Amount'] if v != '')
    old_tree = ast.parse(subprocess.check_output(['git', 'show', 'HEAD:app.py']))
    new_tree = ast.parse(Path('app.py').read_bytes())
    allowed = {'_build_family_analysis', '_build_reporting_group_analysis', '_largest_change_rows',
               '_analysis_rows_as_text', '_build_ai_report_data_context'}
    current = {n.name: n for n in new_tree.body if isinstance(n, ast.FunctionDef)}
    for node in old_tree.body:
        if isinstance(node, ast.FunctionDef) and any(k in node.name for k in ('openai', 'ai_report', 'analysis', 'custom_ai', 'reporting_group_prompt')) and node.name not in allowed:
            assert ast.dump(node) == ast.dump(current[node.name]), node.name
    print('PASS AI narrative preservation and exact financial export')


if __name__ == '__main__':
    main()
