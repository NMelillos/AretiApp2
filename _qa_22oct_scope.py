"""The 22 October packages cannot alter accepted report, parser or financial workflows."""
import ast,unittest
from pathlib import Path

class ScopeTests(unittest.TestCase):
    def test_runtime_modules_are_loaded_from_candidate(self):
        import importlib
        here=Path(__file__).resolve().parent
        for name in ('db','parsing','cnb_import','citi_refund','boc_summary_balances',
                     'duplicate_balance_import','completed_balance_sources','amex_balance_metadata',
                     'import_history','latest_import_balances','latest_balances_compact','balance_report_sections'):
            with self.subTest(module=name):
                self.assertEqual(Path(importlib.import_module(name).__file__).resolve().parent,here)

    def test_exact_application_scope_and_all_other_modules_protected(self):
        here=Path(__file__).resolve().parent;baseline=here.parent/'areti-urgent-import-family'
        allowed={'app.py','duplicate_balance_import.py','latest_import_balances.py','latest_balances_compact.py','import_history.py'}
        for path in baseline.glob('*.py'):
            if path.name.startswith('_qa') or path.name in allowed:continue
            self.assertEqual(path.read_text(encoding='utf-8-sig'),(here/path.name).read_text(encoding='utf-8-sig'),path.name)
        old=ast.parse((baseline/'app.py').read_text(encoding='utf-8-sig'));current=ast.parse((here/'app.py').read_text(encoding='utf-8-sig'))
        find=lambda tree:next(n for n in ast.walk(tree) if isinstance(n,ast.If) and ast.unparse(n.test)=="page == 'Import'")
        # Replace only the Import branch body, never its elif/other-page chain.
        find(current).body=find(old).body
        self.assertEqual(ast.dump(current),ast.dump(old))
        self.assertEqual((here/'requirements.txt').read_bytes(),(baseline/'requirements.txt').read_bytes())

if __name__=='__main__':unittest.main(verbosity=2)
