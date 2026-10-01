"""Temporary, session-authorized runtime plan; no persisted PDF or private manifest."""
from contextlib import closing
from decimal import Decimal, InvalidOperation
import hashlib
from pathlib import Path

import streamlit as st
from streamlit.runtime.scriptrunner import get_script_run_ctx
import financial_atomic as work
from financial_atomic_command import connection
from financial_preconditions import collect_locked, hash_index
from existing_import_compare import authorized

PDF_SHA256 = 'b8413ee856c8bbc14529662297e70f7057ea9b992335c8269c5b43a8f5bf4d0f'
ATOMIC_SHA256 = '35d99ad30c3800e8f43065ab2600a6ced5296d776cde61dac9891241955b11bd'
TRANSACTIONS = frozenset((5910,5911,5914,5915,5916,5917,5918))
APPROVED_FIELDS = frozenset(
    [('classified_transactions', i, 'amount') for i in TRANSACTIONS] +
    [('statement_balances', i, field) for i in (165,167,168,170)
     for field in (('opening_balance','closing_balance') if i == 168 else
                   ('opening_balance','money_out','closing_balance'))])
OPERATION = 'nomad-runtime-final-v1'
STATE = '_nomad_runtime_server_evidence'
SAFE_REASONS = {
    'PDF_FINGERPRINT_CHANGED': 'original PDF fingerprint does not match',
    'EXACT_APPROVED_SCOPE_REQUIRED': 'differences are not exactly the approved 18-field scope',
    'UNAPPROVED_FINANCIAL_DIFFERENCE': 'unexpected row or financial field differs',
    'DEPENDENCY_SCOPE_CHANGED': 'dependency scope differs from the reviewed scope',
    'DDL_EVENT_TRIGGERS_REQUIRE_SEPARATE_REVIEW': 'database DDL event triggers require separate review',
    'PUBLIC_FUNCTIONS_REQUIRE_SEPARATE_REVIEW': 'database functions require separate review',
    'SPLIT_CONFLICT': 'SPLIT dependencies require separate review',
    'PROJECT_BINDING_FAILED': 'database project binding could not be verified',
    'TLS_CONFIGURATION_UNVERIFIED': 'database transport configuration could not be verified',
    'EXPECTED_OLD_VALUE_CHANGED': 'financial values changed after precheck',
}


def check_source():
    work.require(hashlib.sha256(Path(work.__file__).read_bytes().replace(b'\r\n', b'\n')).hexdigest() == ATOMIC_SHA256,
                 'REVIEWED_ATOMIC_SOURCE_CHANGED')


def session_id():
    ctx = get_script_run_ctx(suppress_warning=True)
    work.require(ctx is not None and authorized(), 'AUTHORIZED_MAIN_SESSION_REQUIRED')
    return ctx.session_id


def clear():
    st.session_state.pop(STATE, None)


def derive(content, comparison, diagnostics):
    work.require(hashlib.sha256(content).hexdigest() == PDF_SHA256, 'PDF_FINGERPRINT_CHANGED')
    sections, transactions = comparison['sections'], comparison['transactions']
    work.require(len(sections) == 6 and len(transactions) == 9, 'SOURCE_COUNTS_CHANGED')
    work.require({(s['Import ID'],s['Balance ID']) for s in sections} ==
                 {(169+i,165+i) for i in range(6)}, 'IMPORT_BALANCE_IDENTITY_CHANGED')
    work.require({r['Record ID'] for r in transactions} == set(range(5910,5919)), 'TRANSACTION_IDENTITY_CHANGED')
    work.require(all(r['Import ID'] == (169 if r['Record ID'] < 5914 else
                 174 if r['Record ID'] == 5918 else 171) for r in transactions), 'TRANSACTION_IMPORT_CHANGED')
    work.require(all(s['SPLIT dependencies'] == 0 for s in sections), 'SPLIT_CONFLICT')
    work.require({s['Import ID']:s['Transactions'] for s in sections} ==
                 dict(zip(range(169,175),(4,0,4,0,0,1))), 'SECTION_COUNTS_CHANGED')
    changes, seen = [], set()
    links = {(r['Table'], r['Record ID']):r['Current database value'] for r in diagnostics['fields']
             if r['Field'] == 'statement_hash'}
    for row in diagnostics['fields']:
        if row['Disposition'] != 'PDF DIFFERENCE - DIAGNOSTIC ONLY': continue
        key = row['Table'], row['Record ID'], row['Field']
        work.require(key in APPROVED_FIELDS and key not in seen, 'UNAPPROVED_FINANCIAL_DIFFERENCE')
        seen.add(key)
        try:
            old, new = Decimal(row['Current database value']), Decimal(row['Proposed PDF/parser value'])
        except (InvalidOperation, ValueError):
            raise work.Blocked('INVALID_FINANCIAL_EVIDENCE') from None
        work.require(old.is_finite() and new.is_finite() and old != new, 'INVALID_FINANCIAL_EVIDENCE')
        link = links.get(key[:2])
        work.require(link and link != 'NULL', 'IMPORT_LINK_MISSING')
        changes.append(dict(table=key[0],id=key[1],field=key[2],old=str(old),new=str(new),statement_hash=link))
    work.require(seen == APPROVED_FIELDS, 'EXACT_APPROVED_SCOPE_REQUIRED')
    work.require(len(hash_index(diagnostics['hashes'])) == 24, 'DEPENDENCY_SCOPE_CHANGED')
    return dict(pdf_sha256=PDF_SHA256, changes=changes, preconditions=diagnostics['hashes'])


def precheck(content):
    work.require(authorized(), 'AUTHORIZED_MAIN_SESSION_REQUIRED')
    session_id()
    check_source()
    work.require(hashlib.sha256(content).hexdigest() == PDF_SHA256, 'PDF_FINGERPRINT_CHANGED')
    with closing(connection()) as conn:
        work.session(conn, True)
        try:
            with conn.cursor() as cur:
                comparison, diagnostics = collect_locked(cur, content)
                manifest = derive(content, comparison, diagnostics)
        finally:
            conn.rollback()
        plan = work.prepare(conn, content, manifest)
    return dict(manifest=manifest, plan=plan)


def render(ui):
    # Retire all runtime repair evidence, including evidence from older sessions.
    clear()
    if not authorized():
        return
    session_id()
    from event_trigger_diagnostics import render as render_event_trigger_diagnostics
    render_event_trigger_diagnostics(ui)
    from nomad_precheck import render as render_nomad_precheck
    evidence = render_nomad_precheck(ui)
    ui.subheader('NOMAD Final Repair')
    from nomad_controlled_repair import render as render_controlled_repair
    render_controlled_repair(ui, evidence)
