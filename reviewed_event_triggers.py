"""Exact 2026-09-30 catalog approval for read-only preclearance only.

This does not approve snapshot/audit CREATE TABLE or relax the atomic Apply guard.
No connection creation, expectation regeneration, DDL or mutation is provided.
"""
import hashlib
import json

from event_trigger_diagnostics import CATALOG, DEPENDENCIES, TARGETS, rows

EVIDENCE_SHA256 = 'bc0b9ebd4ca698b428b6e4dce2e4a071fcdc504b03ac88cac246ce0ad70dc811'
TRIGGERS_SHA256 = 'd880d48d130bb91aa19af4f0f3239a2bc5fc7a0b8334edebf88ee393b825d63a'
DEPENDENCIES_SHA256 = 'd8cd13e0227b927ea4fdc21018e2fe80d8422114187e46839c3f6453e7279941'
FIELDS = ('trigger_oid','trigger_name','event','enabled','owner','function_oid',
          'function_name','language','volatility','security_definer','settings',
          'command_tags','function_owner','function_acl','definition_sha256')


class CatalogMismatch(ValueError):
    """Fixed safe reasons, never catalog values or SQL in errors."""


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),
                                    ensure_ascii=True).encode()).hexdigest()


def validate_evidence(evidence):
    try:
        if evidence['server_version'] != '17.6' or evidence['session_replication_role'] != 'origin':
            raise CatalogMismatch('REVIEWED_SERVER_OR_SESSION_CHANGED')
        if evidence['targets'] != TARGETS:
            raise CatalogMismatch('REVIEWED_MIGRATION_SCOPE_CHANGED')
        triggers = evidence['triggers']
        for row in triggers:
            if hashlib.sha256(row['definition'].encode()).hexdigest() != row['definition_sha256']:
                raise CatalogMismatch('TRIGGER_DEFINITION_HASH_MISMATCH')
        canonical = sorted([{k:r[k] for k in FIELDS} for r in triggers], key=lambda r:r['trigger_name'])
        if digest(canonical) != TRIGGERS_SHA256:
            raise CatalogMismatch('REVIEWED_TRIGGER_IDENTITY_OR_DEFINITION_CHANGED')
        dependencies = sorted(evidence['dependencies'],key=lambda r:json.dumps(r,sort_keys=True))
        if digest(dependencies) != DEPENDENCIES_SHA256:
            raise CatalogMismatch('REVIEWED_TRIGGER_DEPENDENCIES_CHANGED')
    except (KeyError,TypeError,AttributeError) as error:
        raise CatalogMismatch('REVIEWED_CATALOG_EVIDENCE_INCOMPLETE') from None
    return {'trigger_allowlist':'PASS','scope':'nine financial ALTER COLUMN TYPE operations only'}


def validate_catalog(cursor):
    """Caller owns a read-only transaction and must roll it back on every path."""
    cursor.execute('SHOW transaction_read_only')
    if cursor.fetchone()[0] != 'on':
        raise CatalogMismatch('READ_ONLY_TRANSACTION_REQUIRED')
    cursor.execute('SHOW server_version'); version=cursor.fetchone()[0]
    cursor.execute('SHOW session_replication_role'); role=cursor.fetchone()[0]
    cursor.execute(CATALOG); triggers=rows(cursor)
    cursor.execute(DEPENDENCIES,([r['trigger_oid'] for r in triggers],[r['function_oid'] for r in triggers]))
    dependencies=rows(cursor)
    for row in triggers:
        row['definition_sha256']=hashlib.sha256(row['definition'].encode()).hexdigest()
    return validate_evidence(dict(server_version=version,session_replication_role=role,
        targets=TARGETS,triggers=triggers,dependencies=dependencies))
