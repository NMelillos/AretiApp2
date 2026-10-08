"""Compare protected executable paths to the preserved exact live baseline."""
import ast,os,unittest
from pathlib import Path
class ScopeTests(unittest.TestCase):
    def test_protected_modules_and_all_other_app_functions_unchanged(self):
        base=Path(os.environ['ARETI_REAL_BASELINE'])
        for p in base.iterdir():
            if p.name not in ('app.py','parsing.py','db.py'):
                self.assertEqual(Path(p.name).read_bytes().replace(b'\r\n',b'\n'),p.read_bytes().replace(b'\r\n',b'\n'),p.name)
        prior=ast.parse((base/'app.py').read_text(encoding='utf-8'));current=ast.parse(Path('app.py').read_text(encoding='utf-8'))
        current_functions={n.name:n for n in current.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
        for node in prior.body:
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name!='render_third_link_report':
                self.assertEqual(ast.dump(node),ast.dump(current_functions[node.name]),node.name)
        a=next(n for n in prior.body if isinstance(n,ast.If) and ast.unparse(n.test)=="page == 'Import'")
        b=next(n for n in current.body if isinstance(n,ast.If) and ast.unparse(n.test)=="page == 'Import'")
        self.assertEqual(ast.dump(a),ast.dump(b),'Normal Upload and Pending dispatch')

        old=ast.parse((base/'db.py').read_text(encoding='utf-8'));new=ast.parse(Path('db.py').read_text(encoding='utf-8'))
        old.body=[n for n in old.body if not isinstance(n,ast.FunctionDef) or n.name!='mark_duplicate_transactions']
        new.body=[n for n in new.body if not isinstance(n,ast.FunctionDef) or n.name!='mark_duplicate_transactions']
        self.assertEqual(ast.dump(old),ast.dump(new),'All persistence, classification, fence and backend duplicate code unchanged')
        old=ast.parse((base/'parsing.py').read_text(encoding='utf-8'));new=ast.parse(Path('parsing.py').read_text(encoding='utf-8'))
        # Preserve definition order too: the baseline defines a helper name twice.
        functions=lambda tree:[ast.dump(n) for n in tree.body if isinstance(n,ast.FunctionDef) and n.name!='parse_pdf']
        self.assertEqual(functions(old),functions(new),'All other parser definitions and order unchanged')

    def test_actual_imported_paths_match_candidate(self):
        import db,parsing,cnb_import,citi_refund,boc_summary_balances
        for module in (db,parsing,cnb_import,citi_refund,boc_summary_balances):
            self.assertEqual(Path(module.__file__).resolve().parent,Path.cwd().resolve())
