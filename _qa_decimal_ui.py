"""Actual monetary UI functions must not send editable money through JS numbers."""
import ast
from contextlib import nullcontext
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace


def main():
    import pandas as pd
    tree = ast.parse(Path('app.py').read_text(encoding='utf-8'))
    functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}

    class UI:
        def __init__(self, value, save=True):
            self.value, self.save = value, save
            self.session_state = {}
            self.messages = []
            self.cache_data = SimpleNamespace(clear=lambda: None)

        def expander(self, *args, **kwargs):
            return nullcontext()

        def columns(self, count):
            return [self] * count

        def date_input(self, *args, **kwargs):
            return '2026-01-01'

        def selectbox(self, label, options, **kwargs):
            return options[0]

        def text_input(self, label, **kwargs):
            return self.value if label == 'Amount' else 'Synthetic'

        def number_input(self, *args, **kwargs):
            raise AssertionError('Editable money still crosses a binary-float browser widget')

        def button(self, *args, **kwargs):
            return self.save

        def error(self, message):
            self.messages.append(message)

        warning = error
        success = error
        info = error

        def rerun(self):
            pass

    for value, save, expected in [('90071992547409.37', True, Decimal('90071992547409.37')),
                                  ('-0.01', True, Decimal('-0.01')),
                                  ('1.234567890123456789', True, Decimal('1.234567890123456789')),
                                  ('0', True, Decimal('0')), ('NaN', True, None),
                                  ('invalid', True, None), ('1.23', False, None)]:
        ui, writes = UI(value, save), []
        namespace = dict(st=ui, get_accounts=lambda: pd.DataFrame(),
            _subcategory_options_for=lambda category: [''],
            _render_report_group_preview=lambda *args: None,
            insert_manual_transaction=lambda *args: writes.append(args) or True)
        exec(compile(ast.Module(body=[functions['render_manual_transaction_form']],
                               type_ignores=[]), 'app.py', 'exec'), namespace)
        namespace['render_manual_transaction_form'](['Synthetic'], [])
        if expected is None:
            assert not writes, 'Invalid/cancelled input reached write function'
        else:
            assert len(writes) == 1 and writes[0][2] == expected
            assert isinstance(writes[0][2], Decimal)

    editor = functions['editable_pending_table']
    configs = [n for n in ast.walk(editor) if isinstance(n, ast.Dict)
               and any(isinstance(k, ast.Constant) and k.value == 'amount' for k in n.keys)]
    assert len(configs) == 1
    config = {k.value: v for k, v in zip(configs[0].keys, configs[0].values) if isinstance(k, ast.Constant)}
    assert config['amount'].func.attr == 'TextColumn', 'Pending amount editor uses a numeric JS payload'
    assignments = [n for n in ast.walk(editor) if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name)
                           and t.value.id == 'table' and isinstance(t.slice, ast.Constant)
                           and t.slice.value == 'amount' for t in n.targets)]
    namespace = {'table': pd.DataFrame({'amount': [Decimal('90071992547409.37'), None]}),
                 'pd': pd}
    assert len(assignments) == 1, 'Pending amount must be serialized as exact text before editing'
    exec(compile(ast.Module(body=assignments, type_ignores=[]), 'app.py', 'exec'), namespace)
    assert namespace['table'].amount.tolist() == ['90071992547409.37', '']
    print('PASS actual manual form and pending editor: exact text, Decimal save, invalid/cancel zero writes')


if __name__ == '__main__':
    main()
