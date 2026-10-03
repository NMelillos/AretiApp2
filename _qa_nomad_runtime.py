"""Synthetic-only runtime derivation and authenticated functional UI checks."""
from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch, Mock
import hashlib


def without_nomad_runtime(name, source):
    if name != 'app.py': return source
    source = source.replace(b'\r\n', b'\n')
    from _qa_ops_console import without_ops_hook
    source = without_ops_hook(source)
    recovery_hook = (b'    if st.query_params.get("nomad_recovery") == "1":\n'
                     b'        from nomad_recovery_page import render_nomad_recovery_page\n'
                     b'        render_nomad_recovery_page()\n'
                     b'        st.stop()\n')
    if recovery_hook in source:
        assert source.count(recovery_hook) == 1
        assert source.count(b'else:\n    require_login()\n' + recovery_hook) == 1, 'Recovery hook moved outside main login gate'
        assert source.index(recovery_hook) < source.index(b'import pandas as pd'), 'Recovery hook must precede heavy imports'
        source = source.replace(recovery_hook, b'', 1)
    blocks = (
        b'\nif page != "Setup":\n    st.session_state.pop("_nomad_runtime_server_evidence", None)\n',
        b'\n    from nomad_runtime import render as render_nomad_runtime\n    render_nomad_runtime(st)\n',
        b'        st.session_state.pop("_nomad_runtime_server_evidence", None)\n',
    )
    anchors = (b'    st.query_params["page"] = page\n',
               b'elif page == "Setup":\n    st.subheader("Setup")\n',
               b'    if right.button("Sign out"):\n')
    for block, anchor in zip(blocks, anchors):
        if block in source:
            assert source.count(block) == 1
            assert source.count(anchor + block) == 1, 'Runtime hook moved outside reviewed location'
            source = source.replace(block, b'', 1)
    return source


def main():
    from pathlib import Path
    import subprocess
    for name in ('app.py', 'auth.py'):
        source = Path(name).read_bytes().replace(b'\r\n', b'\n')
        base = subprocess.check_output(['git','show','de6557df7b6865c82406773fb0b7a6ad15147cd5:'+name]).replace(b'\r\n',b'\n')
        if name == 'app.py': source = without_nomad_runtime(name, source)
        if name == 'auth.py':
            from _qa_ops_console import without_ops_auth_cleanup
            source = without_ops_auth_cleanup(source)
        assert source == base, 'Unrelated application/auth change'
    import nomad_runtime as n
    import financial_atomic as work
    content = b'%PDF-synthetic'
    keys = n.APPROVED_FIELDS
    fields = [dict(Table=t, **{'Record ID': i, 'Field': f,
        'Current database value': '12.5', 'Proposed PDF/parser value': '12.75',
        'Disposition': 'PDF DIFFERENCE - DIAGNOSTIC ONLY'}) for t, i, f in sorted(keys)]
    fields += [dict(Table=t, **{'Record ID': i, 'Field': 'statement_hash',
        'Current database value': 'synthetic-'+str(i), 'Proposed PDF/parser value': 'NOT APPLICABLE',
        'Disposition': 'UNCHANGED / PRESERVE'}) for t, i in {(t, i) for t, i, f in keys}]
    comparison = {'sections': [dict(**{'Import ID': 169+j, 'Balance ID':165+j,
        'SPLIT dependencies':0, 'Transactions':count}) for j, count in enumerate((4,0,4,0,0,1))],
        'transactions':[{'Record ID':i, 'Import ID':169 if i < 5914 else 174 if i == 5918 else 171}
                        for i in range(5910,5919)]}
    hashes = [{'Scope':'synthetic', 'Record ID':str(i), 'Precondition SHA-256':'a'*64} for i in range(23)]
    hashes.append({'Scope':'complete import scope + schema + dependencies','Record ID':'ALL','Precondition SHA-256':'b'*64})
    diagnostics = dict(fields=fields, hashes=hashes)
    def blocked(call):
        try: call()
        except work.Blocked: return
        raise AssertionError('Unsafe request accepted')
    with patch.object(n, 'PDF_SHA256', hashlib.sha256(content).hexdigest()):
        result = n.derive(content, comparison, diagnostics)
        assert len(result['changes']) == 18
        assert len({(r['table'],r['id']) for r in result['changes']}) == 11
        assert all(Decimal(r['new']) == Decimal('12.75') for r in result['changes'])
        blocked(lambda:n.derive(b'wrong',comparison,diagnostics))
        for mutate in ('id','field','extra','missing','null'):
            bad = deepcopy(diagnostics)
            if mutate == 'id': bad['fields'][0]['Record ID'] = 5912
            if mutate == 'field': bad['fields'][0]['Field'] = 'fx_rate'
            if mutate == 'extra': bad['fields'].append(dict(bad['fields'][0]))
            if mutate == 'missing': bad['fields'].pop(0)
            if mutate == 'null': bad['fields'][0]['Current database value'] = 'NULL'
            blocked(lambda:n.derive(content,comparison,bad))
        bad = deepcopy(comparison); bad['sections'][0]['SPLIT dependencies'] = 1
        blocked(lambda:n.derive(content,bad,diagnostics))
        bad = deepcopy(comparison); bad['transactions'][0]['Import ID'] = 174
        blocked(lambda:n.derive(content,bad,diagnostics))
    class UI:
        def __init__(self): self.session_state={}; self.clicked=set(); self.text=''; self.output=[]
        def subheader(self,*a,**k): pass
        def file_uploader(self,*a,**k): return SimpleNamespace(getvalue=lambda:content)
        def button(self,label,disabled=False,**k): return label in self.clicked and not disabled
        def text_input(self,*a,**k): return self.text
        def error(self,text): self.output.append(text)
        def success(self,text): self.output.append(text)
        def info(self,text): self.output.append(text)
    ui=UI()
    real_precheck = n.precheck
    state={'authenticated':True,'login_user':'Areti'}
    with patch.object(n.st,'session_state',ui.session_state), patch.object(n,'authorized',return_value=True), \
         patch('nomad_precheck.authorized',return_value=True), \
         patch('nomad_recovery_status.read_status',return_value={'state':'NORMAL','transaction_status':'NOT STARTED'}), \
         patch.object(n,'session_id',return_value='session-a'), patch.object(n,'check_source'), \
         patch.object(n,'PDF_SHA256',hashlib.sha256(content).hexdigest()), \
         patch.object(n,'precheck',return_value={'manifest':result,'plan':{'safe':'hash'}}) as prepare, \
         patch.object(n.work,'apply',return_value={'repeated':False}) as apply:
        # Repair UI is explicitly retired by the read-only preclearance release.
        # Old widget payloads and old session evidence must never call Apply.
        for clicks in (set(),{'RUN NOMAD FINAL PRECHECK'},{'EXECUTE NOMAD CONTROLLED REPAIR'}):
            ui.clicked=clicks; ui.text='CONFIRM NOMAD REPAIR'
            ui.session_state[n.STATE]={'manifest':result,'plan':{'safe':'hash'},'attempted':False}
            n.render(ui); n.render(ui)
            assert n.STATE not in ui.session_state
            prepare.assert_not_called(); apply.assert_not_called()
            assert 'Repair is unavailable' in ui.output[-1]
        with patch.object(n,'authorized',return_value=False):
            n.render(ui); assert n.STATE not in ui.session_state
            blocked(lambda:real_precheck(content))
    # Actual existing authorization implementation: client-style values are irrelevant.
    import existing_import_compare as auth
    for username, primary, third, ctx, expected in [
        ('Areti',True,False,True,True),('areti',True,False,True,False),
        ('Other',True,False,True,False),('Areti',False,False,True,False),
        ('Areti',True,True,True,False),('Areti',True,False,False,False)]:
        fake={'login_user':username,'authenticated':primary,'third_report_authenticated':third,
              'login_username':'Areti','query_username':'Areti'}
        with patch.object(auth.st,'session_state',fake), patch.object(auth,'get_script_run_ctx',return_value=object() if ctx else None):
            assert auth.authorized() is expected
    print('PASS runtime scope, wrong PDF/ID/field, authorization, retired repair controls, stale session refusal')


if __name__ == '__main__': main()
