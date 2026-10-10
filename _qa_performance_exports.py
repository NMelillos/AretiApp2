"""Read-only export deferral; exact download content against closed baseline."""
import ast,unittest,zipfile
from contextlib import closing
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from functools import partial
import streamlit as st,db,reporting
from streamlit.testing.v1 import AppTest
from _qa_post_import_message import CompletionTests
BASE=Path(r'C:/Users/Student/.codex/worktrees/areti-own-prompt-only/test')

class ExportTests(CompletionTests):
 def setUp(self):
  super().setUp()
  app=self.run_app(self.app());self.import_file(app)
  with closing(db.get_connection()) as c:
   c.execute("UPDATE classified_transactions SET reviewed=1,status='reviewed',category='Synthetic',subcategory='General'");c.commit()
  db.add_category('Synthetic second', 'General', 'Second group')
  with closing(db.get_connection()) as c:
   c.execute("UPDATE classified_transactions SET category='Synthetic second' WHERE id=(SELECT MAX(id) FROM classified_transactions)");c.commit()
  st.cache_data.clear()
 def capture(self,path,page):
  downloads={}
  def collect(label,**kw):downloads[label]=kw['data'];return False
  app=AppTest.from_file(str(path),default_timeout=45)
  app.session_state['authenticated']=True;app.session_state['login_user']='Synthetic reviewer';app.query_params['page']=page
  before=self.snapshot()
  with patch.object(st,'download_button',side_effect=collect):app.run()
  self.assertFalse(app.exception,[e.message for e in app.exception]);self.assertEqual(self.snapshot(),before)
  return app,downloads
 def content(self,data):
  return data() if callable(data) else data
 def compare(self,page):
  _,old=self.capture(BASE/'app.py',page)
  _,new=self.capture(Path('app.py'),page)
  self.assertEqual(set(new),set(old))
  before=self.snapshot()
  for label in old:
   self.assertTrue(callable(new[label]),label)
   with patch.object(db,'get_connection',side_effect=AssertionError('Download must use captured data, not database')):
    content=self.content(new[label])
   reference=self.content(old[label])
   if label.endswith('PDF') or 'complete PDF' in label:self.assertEqual(content,reference,label)
   else:
    with zipfile.ZipFile(BytesIO(content)) as a,zipfile.ZipFile(BytesIO(reference)) as b:
     self.assertEqual(a.namelist(),b.namelist(),label)
     for name in a.namelist():
      if name!='docProps/core.xml':self.assertEqual(a.read(name),b.read(name),(label,name))
  self.assertEqual(self.snapshot(),before)
 def test_setup_backup_exact_and_no_write(self):self.compare('Setup')
 def test_database_export_exact_and_split_visible(self):
  self.compare('Database')
  app,_=self.capture(Path('app.py'),'Database')
  self.assertTrue(any(e.label=='Split one transaction into multiple allocations' for e in app.expander))
 def test_reports_all_exports_exact_and_group_binding(self):self.compare('Reports')
 def test_idle_page_builds_no_export_bytes(self):
  for page in ('Setup','Database','Reports'):
   with patch.object(db,'dataframe_to_excel_bytes',side_effect=AssertionError('Eager general export')),patch.object(reporting,'build_sample_expenses_report',side_effect=AssertionError('Eager report workbook')),patch.object(reporting,'build_pdf_report',side_effect=AssertionError('Eager report PDF')):
    self.capture(Path('app.py'),page)
 def test_native_download_registration(self):
  app=AppTest.from_file('app.py',default_timeout=45);app.session_state['authenticated']=True;app.session_state['login_user']='Synthetic reviewer';app.query_params['page']='Reports'
  before=self.snapshot();app.run();self.assertFalse(app.exception);self.assertTrue(app.get('download_button'));self.assertEqual(self.snapshot(),before)
 def test_download_uses_original_snapshot(self):
  _,downloads=self.capture(Path('app.py'),'Database');data=downloads['Download filtered database Excel'];original=data()
  with closing(db.get_connection()) as c:c.execute("UPDATE classified_transactions SET original_description='Changed synthetic description'");c.commit()
  with patch.object(db,'get_connection',side_effect=AssertionError('No deferred DB read')):
   new=data()
  with zipfile.ZipFile(BytesIO(original)) as a,zipfile.ZipFile(BytesIO(new)) as b:
   for name in a.namelist():
    if name!='docProps/core.xml':self.assertEqual(a.read(name),b.read(name))
 def test_all_business_functions_and_modules_unchanged(self):
  for p in BASE.glob('*.py'):
   if p.name!='app.py':self.assertEqual(p.read_bytes(),Path(p.name).read_bytes(),p.name)
  before=ast.parse((BASE/'app.py').read_text(encoding='utf-8-sig'));after=ast.parse(Path('app.py').read_text(encoding='utf-8-sig'))
  def functions(tree):return {n.name:ast.dump(n) for n in tree.body if isinstance(n,ast.FunctionDef)}
  self.assertEqual(functions(before),functions(after))

def load_tests(loader,tests,pattern):
 return unittest.TestSuite(ExportTests(n) for n in ExportTests.__dict__ if n.startswith('test_'))
if __name__=='__main__':unittest.main(verbosity=2)
