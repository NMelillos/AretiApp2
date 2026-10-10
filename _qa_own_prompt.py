"""Bounded prompt regression: real function bodies; mocked provider; no DB access."""
import ast, json, unittest, urllib.request
from pathlib import Path
from decimal import Decimal
from unittest.mock import Mock, patch
import pandas as pd

BASELINE=Path(r'C:/Users/Student/.codex/worktrees/areti-uat-addendum/test')
SOURCE=Path('app.py').read_text(encoding='utf-8-sig')
NAMES={'_report_group_prompt_lookup','_build_reporting_group_analysis',
       '_build_custom_reporting_group_analysis','_build_ai_report_data_context',
       '_request_openai_report','_extract_openai_response_text','_analysis_rows_as_text'}
def load(source):
    nodes=[n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name in NAMES]
    scope={'pd':pd,'json':json,'urllib':urllib,'_money':lambda v:f'{v:.2f}',
           '_executive_signed_amount_series':lambda f:f['expense_usd'],
           '_executive_status_delta':lambda a,b:a-b}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'app.py','exec'),scope)
    return scope

class PromptTests(unittest.TestCase):
    def setUp(self):
        self.scope=load(SOURCE)
        self.frame=pd.DataFrame([dict(report_group='Synthetic',month='2026-10',category='A',subcategory='B',
            txn_date='2026-10-01',full_statement_description='Synthetic only',
            expense_usd=Decimal('962.60'))])
        self.sender=Mock(return_value='Synthetic provider report')
        self.scope['_run_custom_ai_prompt']=self.sender
    def generate(self,prompt):
        return self.scope['_build_reporting_group_analysis'](self.frame,['2026-10'],{},'Synthetic',prompt)
    def test_custom_saved_prompt_used_exactly(self):
        prompt='Only my instructions.\nKeep punctuation & accents: café.'
        settings=pd.DataFrame([dict(report_group='Synthetic',ai_prompt=prompt)])
        original=settings.copy(deep=True)
        self.scope['get_report_group_settings']=lambda:settings
        saved=self.scope['_report_group_prompt_lookup']()['Synthetic']
        self.assertEqual(self.generate(saved),'Synthetic provider report')
        self.assertEqual(self.sender.call_args.args[0],prompt)
        pd.testing.assert_frame_equal(settings,original)
    def test_blank_prompt_makes_no_request(self):
        for prompt in ('','   \n',None):
            self.assertIn('No approved custom AI prompt',self.generate(prompt))
        self.sender.assert_not_called()
    def test_actual_request_contains_only_saved_instructions(self):
        prompt='My own prompt\nSecond line.'
        response=Mock();response.read.return_value=b'{"output_text":"Generated"}'
        manager=Mock();manager.__enter__=Mock(return_value=response);manager.__exit__=Mock(return_value=False)
        with patch.object(urllib.request,'urlopen',return_value=manager) as transport:
            result=self.scope['_request_openai_report'](prompt,'Synthetic context','synthetic-model',20,1,'synthetic-only')
        payload=json.loads(transport.call_args.args[0].data)
        self.assertEqual(payload['instructions'],prompt)
        self.assertEqual(payload['input'],'Synthetic context')
        self.assertEqual(result,'Generated')
    def test_no_financial_data_mutation(self):
        original=self.frame.copy(deep=True)
        self.generate('Saved prompt')
        pd.testing.assert_frame_equal(self.frame,original)
        self.assertIn('962.60',self.sender.call_args.args[1])
    def test_report_context_identical_to_baseline(self):
        self.generate('Saved prompt');new=self.sender.call_args.args
        old=load((BASELINE/'app.py').read_text(encoding='utf-8-sig'))
        sender=Mock();old['_run_custom_ai_prompt']=sender
        old['_build_reporting_group_analysis'](self.frame,['2026-10'],{},'Synthetic','Saved prompt')
        self.assertEqual(new,sender.call_args.args)
    def test_setup_has_no_advertised_default(self):
        self.assertNotIn('Default AI prompt used when the group prompt is empty',SOURCE)
        self.assertNotIn('def _default_reporting_group_prompt',SOURCE)
        self.assertIn('If it is blank, no AI report is generated.',SOURCE)
    def test_saved_storage_and_protected_sources_unchanged(self):
        for p in BASELINE.glob('*.py'):
            if p.name!='app.py':self.assertEqual(p.read_bytes(),Path(p.name).read_bytes(),p.name)
    def test_only_setup_display_and_unused_function_changed(self):
        before=ast.parse((BASELINE/'app.py').read_text(encoding='utf-8-sig'))
        after=ast.parse(SOURCE)
        def normalize(tree):
            tree.body=[n for n in tree.body if not(isinstance(n,ast.FunctionDef) and n.name=='_default_reporting_group_prompt')]
            for n in ast.walk(tree):
                if n is tree:
                    for child in ast.walk(n):
                        if hasattr(child,'body') and isinstance(child.body,list):
                            child.body=[x for x in child.body if not(
                                isinstance(x,ast.With) and any(isinstance(i.context_expr,ast.Call) and i.context_expr.args and isinstance(i.context_expr.args[0],ast.Constant) and i.context_expr.args[0].value=='Default AI prompt used when the group prompt is empty' for i in x.items)
                                or isinstance(x,ast.Expr) and isinstance(x.value,ast.Call) and x.value.args and isinstance(x.value.args[0],ast.Constant) and x.value.args[0].value=='AI reports use only the saved reporting-group prompt. If it is blank, no AI report is generated.')]
            return ast.dump(tree)
        self.assertEqual(normalize(before),normalize(after))

if __name__=='__main__':unittest.main(verbosity=2)
