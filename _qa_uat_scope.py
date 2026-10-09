"""Protect the accepted application and unrelated financial code byte-for-byte."""
import ast,unittest
from pathlib import Path
BASE=Path(r'C:/Users/Student/.codex/worktrees/areti-autonomous-final-20261008/test')
class ScopeTests(unittest.TestCase):
 def test_only_authorized_modules_changed(self):
  allowed={'parsing.py','import_history.py','latest_balances_compact.py','latest_import_balances.py','_qa_report_integrity.py','_qa_autonomous_workflows.py'}
  for path in BASE.glob('*.py'):
   if path.name not in allowed:self.assertEqual(Path(path.name).read_bytes(),path.read_bytes(),path.name)
 def test_existing_parser_functions_unchanged(self):
  def functions(path):return [(n.name,ast.dump(n,include_attributes=False)) for n in ast.parse(path.read_text(encoding='utf-8-sig')).body if isinstance(n,ast.FunctionDef) and n.name not in {'parse_pdf','extract_statement_balance'}]
  self.assertEqual(functions(BASE/'parsing.py'),functions(Path('parsing.py')))
 def test_atomic_import_existing_paths_unchanged(self):
  old=ast.parse((BASE/'import_history.py').read_text(encoding='utf-8-sig'));new=ast.parse(Path('import_history.py').read_text(encoding='utf-8-sig'))
  func=next(n for n in new.body if isinstance(n,ast.FunctionDef) and n.name=='commit_statement')
  guards=[]
  for n in func.body:
   if isinstance(n,ast.If) and any(isinstance(v,ast.Constant) and v.value in ('Fifth Third labelled sections',) for v in ast.walk(n.test)):guards.append(n)
  self.assertEqual(len(guards),1)
  for n in guards:func.body.remove(n)
  self.assertEqual(ast.dump(new,include_attributes=False),ast.dump(old,include_attributes=False))
if __name__=='__main__':unittest.main()
