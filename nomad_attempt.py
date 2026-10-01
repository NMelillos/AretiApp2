"""Payload-free repair failure evidence. Never serialize exception messages."""
from datetime import datetime, timezone
import json
import logging
import re
import uuid

SESSION_KEY = '_nomad_last_failure'
SAFE_CODES = frozenset('''STALE_HASH EXPECTED_OLD_VALUE_CHANGED EXPECTED_OLD_UPDATE_FAILED
FROZEN_LIVE_PRECONDITION_CHANGED FROZEN_MANIFEST_CHANGED FROZEN_RLS_STATE_CHANGED
FROZEN_RLS_POLICY_CHANGED PUBLIC_FUNCTIONS_REQUIRE_SEPARATE_REVIEW
EXISTING_DATABASE_IDENTITY_CANNOT_VERIFY_COMPLETE_ROWS LOCKED_PRECONDITION_CHANGED
PDF_FINGERPRINT_CHANGED REPAIR_SCOPE_CHANGED PRECONDITION_COUNT_CHANGED REPAIR_FIELD_FORBIDDEN
IMPORT_LINK_CHANGED SOURCE_COUNTS_CHANGED SOURCE_RECONCILIATION_FAILED DATA_OR_METADATA_CHANGED
DEPENDENCY_CHANGED CATALOG_DEPENDENCY_CHANGED COLUMN_CONTRACT_CHANGED WRONG_DATABASE
OPERATION_BINDING_CHANGED OPERATION_ALREADY_REVERSED POST_STATE_CHANGED SNAPSHOT_INTEGRITY_FAILED
AUDIT_GUARD_CHANGED AUDIT_TRIGGER_CHANGED AUDIT_SCHEMA_GRANTS_REQUIRE_REVIEW
AUDIT_TABLE_GRANTS_REQUIRE_REVIEW AUDIT_RULES_REQUIRE_REVIEW SNAPSHOT_NOT_FOUND
COMMIT_UNCERTAIN_KEEP_WRITERS_CLOSED_VERIFY INDEPENDENT_VERIFY_FAILED FENCE_STATE_CHANGED
IDLE_CONNECTION_REQUIRED OPERATION_BUSY APPROVAL_CHANGED CONTROLLER_CONTRACT_REQUIRED
DELIBERATE_CONFIRMATION_REQUIRED NONFINITE_SOURCE_AMOUNT ROW_COUNT_CHANGED ROW_FIELDS_CHANGED
DDL_EVENT_TRIGGERS_REQUIRE_SEPARATE_REVIEW NONSTANDARD_TABLE_REQUIRES_REVIEW
INHERITANCE_REQUIRES_REVIEW UNKNOWN_INBOUND_DEPENDENCY UNKNOWN_OUTBOUND_DEPENDENCY
ROW_SECURITY_REQUIRES_REVIEW'''.split())


class Attempt:
    def __init__(self, repair_id):
        self.data = dict(attempt_id=uuid.uuid4().hex, repair_id=repair_id,
                         phase='connect', transaction_begun=False, commit_attempted=False,
                         commit_outcome_known=True, durable_fence_state='UNKNOWN',
                         transaction_classification='NOT_STARTED')

    def phase(self, name):
        self.data['phase'] = name
        if name == 'transaction_locks': self.data['transaction_begun'] = True
        if name == 'repair_commit':
            self.data.update(commit_attempted=True, commit_outcome_known=False)
        if name == 'repair_committed': self.data['commit_outcome_known'] = True

    def failure(self, error):
        # PostgreSQL text/detail/context can contain SQL parameters and credentials.
        # Retain structural identifiers only, never str(error), repr or traceback.
        sqlstate = None
        current = error
        seen = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            code = getattr(current, 'pgcode', None)
            if isinstance(code, str) and re.fullmatch('[0-9A-Z]{5}', code):
                sqlstate = code
                break
            current = current.__cause__ or current.__context__
        cls = type(error).__name__
        if not re.fullmatch('[A-Za-z_][A-Za-z_0-9]{0,79}', cls): cls = 'Exception'
        identifier = error.args[0] if len(error.args) == 1 and isinstance(error.args[0], str) else None
        identifier = identifier if identifier in SAFE_CODES else 'REPAIR_FAILURE_' + (sqlstate or cls)
        self.data.update(timestamp_utc=datetime.now(timezone.utc).isoformat(),
                         exception_class=cls, sqlstate=sqlstate,
                         error_identifier=identifier,
                         message='Repair failed at the recorded phase; private exception text withheld.')
        return dict(self.data)

    def publish(self):
        logging.getLogger('nomad.repair.failure').error(json.dumps(self.data, sort_keys=True))
        try:
            import streamlit as st
            st.session_state[SESSION_KEY] = dict(self.data)
        except Exception:
            # Server evidence remains available even without a Streamlit context.
            pass
