"""Temporary authenticated catalog evidence, never a migration approval path."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import re

from existing_import_compare import authorized
from financial_atomic_command import connection


CATALOG = """SELECT e.oid AS trigger_oid,e.evtname AS trigger_name,
e.evtevent AS event,e.evtenabled AS enabled,
pg_catalog.pg_get_userbyid(e.evtowner) AS owner,e.evtfoid AS function_oid,
pg_catalog.format('%I.%I',n.nspname,p.proname) AS function_name,
pg_catalog.pg_get_functiondef(p.oid) AS definition,l.lanname AS language,
p.provolatile AS volatility,p.prosecdef AS security_definer,
p.proconfig AS settings,e.evttags AS command_tags,
pg_catalog.pg_get_userbyid(p.proowner) AS function_owner,
p.proacl::text AS function_acl
FROM pg_catalog.pg_event_trigger e
JOIN pg_catalog.pg_proc p ON p.oid=e.evtfoid
JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
JOIN pg_catalog.pg_language l ON l.oid=p.prolang
WHERE e.evtenabled<>'D' ORDER BY e.evtname"""
DEPENDENCIES = """SELECT d.classid::regclass::text AS source_catalog,
d.objid AS source_oid,d.objsubid AS source_subid,d.deptype AS dependency_type,
d.refclassid::regclass::text AS referenced_catalog,d.refobjid AS referenced_oid,
d.refobjsubid AS referenced_subid,
pg_catalog.pg_describe_object(d.refclassid,d.refobjid,d.refobjsubid) AS reference,
x.extname AS extension,x.extversion AS extension_version
FROM pg_catalog.pg_depend d LEFT JOIN pg_catalog.pg_extension x
ON d.refclassid='pg_catalog.pg_extension'::regclass AND x.oid=d.refobjid
WHERE (d.classid='pg_catalog.pg_event_trigger'::regclass AND d.objid=ANY(%s))
OR (d.classid='pg_catalog.pg_proc'::regclass AND d.objid=ANY(%s))
ORDER BY d.classid,d.objid,d.refclassid,d.refobjid,d.objsubid,d.refobjsubid"""
TARGETS = {'classified_transactions':['amount','amount_usd','fx_rate','split_original_amount'],
           'statement_balances':['opening_balance','money_out','money_in','closing_balance'],
           'rates':['rate_value']}


class DiagnosticBlocked(ValueError):
    pass


def require_auth():
    if not authorized():
        raise DiagnosticBlocked('Authenticated main Areti session required.')


def safe_export(payload):
    require_auth()
    text = json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2)
    # Function bodies/settings can themselves contain embedded credentials. Do
    # not silently redact definitions and pretend the resulting review is complete.
    if re.search(r'(?i)(database_url|postgres_url|password|passwd|secret|token|api[_-]?key|'
                 r'authorization|bearer\s|postgres(?:ql)?://|https?://|-----BEGIN .*PRIVATE KEY)', text):
        raise DiagnosticBlocked('Catalog evidence withheld: potentially sensitive content requires private review.')
    return text.encode('utf-8')


def rows(cur):
    names = [d[0] for d in cur.description]
    return [dict(zip(names,r)) for r in cur.fetchall()]


def collect():
    require_auth()
    with closing(connection()) as conn:
        conn.set_session(readonly=True, autocommit=False, isolation_level='REPEATABLE READ')
        try:
            with conn.cursor() as cur:
                cur.execute('SHOW transaction_read_only')
                if cur.fetchone()[0] != 'on':
                    raise DiagnosticBlocked('Read-only transaction could not be verified.')
                cur.execute('SHOW server_version'); version = cur.fetchone()[0]
                cur.execute('SHOW session_replication_role'); role = cur.fetchone()[0]
                cur.execute(CATALOG); triggers = rows(cur)
                cur.execute(DEPENDENCIES, ([r['trigger_oid'] for r in triggers],
                                           [r['function_oid'] for r in triggers]))
                dependencies = rows(cur)
        finally:
            conn.rollback()
    require_auth()
    for trigger in triggers:
        tags = trigger['command_tags']
        tag_match = tags is None or 'ALTER TABLE' in tags
        enabled = trigger['enabled'] == 'A' or (
            trigger['enabled'] == ('R' if role == 'replica' else 'O'))
        event_match = trigger['event'] in ('ddl_command_start','ddl_command_end','sql_drop','table_rewrite')
        trigger['alter_table_relevance'] = ('POSSIBLE - function/dependency review required'
            if tag_match and enabled and event_match else 'Not selected for ALTER TABLE in captured session mode')
        trigger['definition_sha256'] = hashlib.sha256(trigger['definition'].encode()).hexdigest()
    payload = dict(format_version=1,captured_at=datetime.now(timezone.utc).isoformat(),
                   server_version=version,session_replication_role=role,targets=TARGETS,
                   triggers=triggers,dependencies=dependencies,
                   limitation='Catalog dependencies may omit dynamic SQL and string-body function calls. '
                   'No function was executed. No trigger is approved by this evidence. '
                   'sql_drop depends on dropped objects; table_rewrite depends on a rewrite.')
    safe_export(payload)
    return payload


def render(ui):
    if not authorized():
        return
    ui.subheader('DDL event trigger diagnostics')
    if ui.button('Collect read-only event trigger evidence', key='event_trigger_collect'):
        try:
            evidence = collect()
            data = safe_export(evidence)
            require_auth()
            ui.dataframe([{k:r[k] for k in ('trigger_name','event','enabled','function_name',
                          'alter_table_relevance')} for r in evidence['triggers']], hide_index=True)
            ui.caption('Read-only catalog evidence. Migration safety has not been approved.')
            ui.download_button('Export event trigger evidence', data=data,
                file_name='Event_Trigger_Evidence_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_UTC')+'.json',
                mime='application/json',on_click='ignore',key='event_trigger_export')
        except Exception:
            ui.error('DIAGNOSTIC BLOCKED - authorized read-only, non-sensitive catalog evidence could not be produced.')
