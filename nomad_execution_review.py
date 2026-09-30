"""Frozen production approval applied to the execution path, never inferred live."""
import financial_atomic as work
import nomad_precheck as precheck
from reviewed_event_triggers import validate_execution_catalog, digest
from event_trigger_diagnostics import rows


class ReviewedNomad:
    def execution_catalog(self, cur):
        validate_execution_catalog(cur)

    def public_functions(self, functions):
        # OID is also bound by the reviewed ensure_rls function identity/body hash.
        work.require(functions == [(17170,)], 'PUBLIC_FUNCTIONS_REQUIRE_SEPARATE_REVIEW')

    def security(self, cur, catalog):
        expected = [(t, 'r', True, False) for t in sorted(work.TABLES)]
        actual = [tuple(r[:4]) for r in catalog['security'] if r[0] in work.TABLES]
        work.require(actual == expected, 'FROZEN_RLS_STATE_CHANGED')
        # The captured pg_depend baseline has no pg_policy objects for these
        # eight tables. Do not infer permission to add a policy from RLS enablement.
        work.require(not [p for p in catalog['policies'] if p[0] in work.TABLES], 'FROZEN_RLS_POLICY_CHANGED')
        cur.execute('''SELECT current_user=session_user,
            bool_and(c.relowner=r.oid OR r.rolbypassrls OR r.rolsuper)
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            CROSS JOIN pg_roles r WHERE n.nspname='public' AND c.relname=ANY(%s)
            AND r.rolname=current_user''', (list(work.TABLES),))
        work.require(cur.fetchone() == (True, True), 'EXISTING_DATABASE_IDENTITY_CANNOT_VERIFY_COMPLETE_ROWS')

    def preconditions(self, cur, content, manifest):
        precheck.require_auth()
        precheck.release_identity()
        self.execution_catalog(cur)
        catalog = {}
        for key, query in precheck.CATALOG_QUERIES.items():
            cur.execute(query, (precheck.TABLES,) * query.count('%s'))
            catalog[key] = rows(cur)
        comparison, diagnostics = precheck.collect_locked(cur, content)
        evidence = precheck.evaluate(comparison, diagnostics, content, catalog)
        work.require(evidence['overall'] == 'PASS', 'FROZEN_LIVE_PRECONDITION_CHANGED')
        from nomad_runtime import derive
        work.require(work.state_hash(derive(content, comparison, diagnostics)) == work.state_hash(manifest), 'FROZEN_MANIFEST_CHANGED')

    def derive(self, cur, content):
        from nomad_runtime import derive
        comparison, diagnostics = precheck.collect_locked(cur, content)
        manifest = derive(content, comparison, diagnostics)
        self.preconditions(cur, content, manifest)
        return manifest
