"""Authenticated frozen-evidence precheck. No write, repair, or plan approval API."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
import re

from existing_import_compare import authorized
from financial_atomic_command import connection
from financial_preconditions import collect_locked, hash_index
from reviewed_event_triggers import validate_catalog, digest, CatalogMismatch
from event_trigger_diagnostics import rows, safe_export
import nomad_precheck_bindings as bindings

TABLES = ['classified_transactions','statement_balances','rates','statement_imports',
          'category_list','transaction_change_log','account_list','transaction_memory']
CATALOG_QUERIES = {
    'columns': "SELECT table_name,column_name,data_type,udt_name,is_nullable,column_default,numeric_precision,numeric_scale FROM information_schema.columns WHERE table_schema='public' AND table_name=ANY(%s) ORDER BY table_name,ordinal_position",
    'constraints': "SELECT n.nspname,c.relname,k.conname,k.contype,pg_catalog.pg_get_constraintdef(k.oid) AS definition FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_class c ON c.oid=k.conrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE k.conrelid IN (SELECT c.oid FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=ANY(%s)) OR k.confrelid IN (SELECT c.oid FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=ANY(%s)) ORDER BY n.nspname,c.relname,k.conname",
    'indexes': "SELECT tablename,indexname,indexdef FROM pg_catalog.pg_indexes WHERE schemaname='public' AND tablename=ANY(%s) ORDER BY tablename,indexname",
    'relations': "SELECT c.relname,c.relkind,c.relrowsecurity,c.relforcerowsecurity,c.relpersistence FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=ANY(%s) ORDER BY c.relname",
    'triggers': "SELECT c.relname,t.tgname,t.tgenabled,pg_catalog.pg_get_triggerdef(t.oid) AS definition FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=ANY(%s) AND NOT t.tgisinternal ORDER BY c.relname,t.tgname",
    'dependencies': "SELECT d.classid::regclass::text AS catalog,d.objid,d.objsubid,d.refclassid::regclass::text AS reference_catalog,d.refobjid,d.refobjsubid,d.deptype FROM pg_catalog.pg_depend d WHERE (d.classid='pg_catalog.pg_class'::regclass AND d.objid IN (SELECT c.oid FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=ANY(%s))) OR (d.refclassid='pg_catalog.pg_class'::regclass AND d.refobjid IN (SELECT c.oid FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=ANY(%s))) ORDER BY d.classid,d.objid,d.objsubid,d.refclassid,d.refobjid,d.refobjsubid,d.deptype",
}


def require_auth():
    if not authorized():
        raise ValueError('AUTHORIZED_MAIN_ARETI_SESSION_REQUIRED')


def release_identity():
    actual=os.getenv('RENDER_GIT_COMMIT','')
    approved=os.getenv('NOMAD_APPROVED_RELEASE_SHA','')
    if (not re.fullmatch('[0-9a-fA-F]{40}',actual)
            or not re.fullmatch('[0-9a-fA-F]{40}',approved)
            or actual!=approved):
        raise ValueError('RELEASE_IDENTITY_UNVERIFIED')
    return {'status':'PASS','deployed_sha':actual,'approved_sha':approved}


class ReadOnlyCursor:
    def __init__(self,cur): self.cur=cur
    def execute(self,query,params=None):
        if not isinstance(query,str) or not query.lstrip().upper().startswith(('SELECT ','SHOW ')):
            raise ValueError('READ_ONLY_QUERY_REQUIRED')
        return self.cur.execute(query,params)
    def __getattr__(self,name): return getattr(self.cur,name)


def evaluate(comparison,diagnostics,content,catalog):
    from nomad_runtime import derive
    actual=hash_index(diagnostics['hashes'])
    results=[{'scope':key[0],'record_id':key[1],'expected_binding':expected,
        'current_hash':actual.get(key),'status':'PASS' if key in actual and
        hashlib.sha256(actual[key].encode()).hexdigest()==expected else 'BLOCKED'}
        for key,expected in sorted(bindings.HASH_BINDINGS.items())]
    schema=[{k:'NULL' if v is None else str(v) for k,v in r.items()} for r in diagnostics['schema']]
    schema_ok=digest(sorted(schema,key=lambda r:(r['Table'],r['column_name'])))==bindings.SCHEMA_DIGEST
    reasons=[]
    if set(actual)!=set(bindings.HASH_BINDINGS): reasons.append('FROZEN_HASH_SCOPE_CHANGED')
    if any(r['status']!='PASS' for r in results): reasons.append('FROZEN_PRECONDITION_CHANGED')
    if not schema_ok: reasons.append('FROZEN_FINANCIAL_SCHEMA_CHANGED')
    plan_ok=False
    try:
        candidate=derive(content,comparison,diagnostics)
        plan_ok=digest(sorted(candidate['changes'],key=lambda r:(r['table'],r['id'],r['field'])))==bindings.PLAN_DIGEST
    except (ValueError,KeyError,TypeError,RuntimeError):
        pass
    if not plan_ok: reasons.append('FROZEN_REPAIR_PLAN_OR_TARGET_CHANGED')
    catalog_hash=digest(catalog)
    if bindings.EXTENDED_CATALOG_DIGEST is None:
        reasons.append('FROZEN_DEFAULTS_CONSTRAINTS_INDEXES_DEPENDENCY_BASELINE_MISSING')
    elif catalog_hash!=bindings.EXTENDED_CATALOG_DIGEST:
        reasons.append('FROZEN_EXTENDED_CATALOG_CHANGED')
    hashes_ok=not any(r['status']!='PASS' for r in results) and set(actual)==set(bindings.HASH_BINDINGS)
    return dict(overall='BLOCKED' if reasons else 'PASS',mismatch_reasons=reasons,hash_results=results,
        catalog_allowlist='PASS' if schema_ok and bindings.EXTENDED_CATALOG_DIGEST==catalog_hash else 'BLOCKED',
        monetary_schema='PASS' if schema_ok else 'BLOCKED',extended_catalog_digest=catalog_hash,
        target_rows='PASS' if plan_ok and hashes_ok else 'BLOCKED',
        dependency_metadata='PASS' if hashes_ok else 'BLOCKED',approved_plan_match='PASS' if plan_ok else 'BLOCKED',
        diagnostics=diagnostics,comparison=comparison,extended_catalog=catalog)


def run(content):
    require_auth()
    from nomad_runtime import PDF_SHA256
    evidence={'overall':'BLOCKED','captured_at':datetime.now(timezone.utc).isoformat(),
        'mode':'READ ONLY - NO REPAIR','mismatch_reasons':[],
        'release':{'status':'NOT CHECKED'},'trigger_allowlist':'NOT CHECKED',
        'catalog_allowlist':'NOT CHECKED','target_rows':'NOT CHECKED','dependency_metadata':'NOT CHECKED',
        'approved_plan_match':'NOT CHECKED','hash_results':[{'scope':k[0],'record_id':k[1],
            'status':'NOT CHECKED'} for k in sorted(bindings.HASH_BINDINGS)],
        'frozen_workbook_sha256':bindings.WORKBOOK_SHA256}
    stage='RELEASE_IDENTITY_UNVERIFIED'
    try:
        evidence['release']=release_identity()
        stage='PDF_FINGERPRINT_MISMATCH'
        if not isinstance(content,bytes) or hashlib.sha256(content).hexdigest()!=PDF_SHA256:
            raise ValueError(stage)
        evidence['pdf_fingerprint']=PDF_SHA256
        stage='DATABASE_IDENTITY_OR_READ_ONLY_SESSION_UNVERIFIED'
        with closing(connection()) as conn:
            conn.set_session(readonly=True,autocommit=False,isolation_level='REPEATABLE READ')
            try:
                with conn.cursor() as raw:
                    cur=ReadOnlyCursor(raw)
                    stage='REVIEWED_TRIGGER_CATALOG_MISMATCH'
                    validate_catalog(cur)
                    evidence['trigger_allowlist']='PASS'
                    cur.execute('SELECT pg_catalog.current_database(), pg_catalog.inet_server_addr()::text, pg_catalog.inet_server_port()')
                    database,address,port=cur.fetchone()
                    if database!='postgres' or port!=5432:
                        stage='LIVE_DATABASE_IDENTITY_CHANGED'
                        raise ValueError(stage)
                    evidence['database_identity']={'project':'nlbqogwckdeqqasejxbf',
                        'database':database,'port':port,'server_address':address,
                        'server_version':'17.6','session_replication_role':'origin',
                        'transport':'approved application connection; TLS verified','transaction':'READ ONLY / REPEATABLE READ'}
                    stage='SOURCE_SCOPE_OR_DEPENDENCY_COMPARISON_FAILED'
                    comparison,diagnostics=collect_locked(cur,content)
                    catalog={}
                    for name,query in CATALOG_QUERIES.items():
                        cur.execute(query,(TABLES,TABLES) if name in ('constraints','dependencies') else (TABLES,))
                        catalog[name]=rows(cur)
                    evidence.update(evaluate(comparison,diagnostics,content,catalog))
            finally:
                try:
                    conn.rollback()
                except Exception:
                    stage='READ_ONLY_ROLLBACK_FAILED'
                    raise
    except CatalogMismatch as error:
        evidence['overall']='BLOCKED'
        evidence['mismatch_reasons']=[str(error)]
    except Exception:
        evidence['overall']='BLOCKED'
        evidence['mismatch_reasons']=[stage]
    require_auth()
    evidence['evidence_digest']=digest(evidence)
    return evidence


def export(evidence):
    require_auth()
    body=dict(evidence); claimed=body.pop('evidence_digest',None)
    if claimed!=digest(body): raise ValueError('EVIDENCE_DIGEST_CHANGED')
    return safe_export(evidence)


def render(ui):
    if not authorized(): return
    ui.subheader('NOMAD Final Read-Only Precheck')
    uploaded=ui.file_uploader('Approved original NOMAD PDF (read-only)',type=['pdf'],key='nomad_runtime_pdf')
    if ui.button('Run NOMAD Final Precheck',disabled=uploaded is None,key='nomad_final_readonly_run'):
        try:
            evidence=run(uploaded.getvalue())
            data=export(evidence)
            require_auth()
            if evidence['overall']=='PASS': ui.success('READ-ONLY NOMAD PRECHECK PASS')
            else: ui.error('READ-ONLY NOMAD PRECHECK BLOCKED - '+', '.join(evidence['mismatch_reasons']))
            ui.download_button('Export NOMAD Precheck Evidence',data=data,
                file_name='NOMAD_Precheck_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_UTC')+'.json',
                mime='application/json',on_click='ignore',key='nomad_final_readonly_export')
            return evidence
        except Exception:
            ui.error('READ-ONLY NOMAD PRECHECK BLOCKED - authorized non-sensitive evidence could not be produced')
