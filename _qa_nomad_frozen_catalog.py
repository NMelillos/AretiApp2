"""Synthetic exact-catalog mutation coverage; no private evidence or DB access."""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
from unittest.mock import patch


def main():
    import nomad_precheck as n
    import nomad_runtime as runtime
    b = n.bindings
    review = b.EXTENDED_CATALOG_REVIEW
    assert review['catalog_digest'] == b.EXTENDED_CATALOG_DIGEST
    assert b.EXTENDED_CATALOG_DIGEST == '6cae0b306f98590d14e06e6f889752398352c43aee667b656d6140b738520e3a'
    assert review['pdf_sha256'] == runtime.PDF_SHA256
    assert len(b.HASH_BINDINGS) == 24
    assert review['preconditions_digest'] == n.digest(
        [[*key, value] for key, value in sorted(b.HASH_BINDINGS.items())])

    catalog = {
        'columns': [{'table_name': 'synthetic', 'column_name': 'amount',
                     'data_type': 'real', 'is_nullable': 'YES', 'column_default': None}],
        'constraints': [{'contype': 'p', 'definition': 'PRIMARY KEY (id)'}],
        'indexes': [{'indexdef': 'CREATE INDEX synthetic_amount ON synthetic (amount)'}],
        'relations': [{'relname': 'synthetic', 'relrowsecurity': True,
                       'relforcerowsecurity': False}],
        'triggers': [],
        'dependencies': [{'catalog': 'pg_class', 'objid': 1, 'deptype': 'a'}],
    }
    hashes = [{'Scope': key[0], 'Record ID': key[1],
               'Precondition SHA-256': hashlib.sha256(str(key).encode()).hexdigest()}
              for key in b.HASH_BINDINGS]
    expected = {(row['Scope'], row['Record ID']): hashlib.sha256(
        row['Precondition SHA-256'].encode()).hexdigest() for row in hashes}
    diagnostics = {'hashes': hashes, 'schema': [{'Table': 'synthetic',
                   'column_name': 'amount', 'data_type': 'real'}]}
    plan = {'changes': [{'table': 'synthetic', 'id': 1, 'field': 'amount'}]}
    with ExitStack() as stack:
        for name, value in [('HASH_BINDINGS', expected),
                            ('SCHEMA_DIGEST', n.digest(diagnostics['schema'])),
                            ('PLAN_DIGEST', n.digest(plan['changes'])),
                            ('EXTENDED_CATALOG_DIGEST', n.digest(catalog))]:
            stack.enter_context(patch.object(b, name, value))
        stack.enter_context(patch.object(runtime, 'derive', return_value=plan))
        assert n.evaluate({}, diagnostics, b'synthetic', catalog)['overall'] == 'PASS'
        variants = []
        for field, value in [('column_default', '0'), ('is_nullable', 'NO'),
                             ('data_type', 'numeric')]:
            bad = deepcopy(catalog); bad['columns'][0][field] = value
            variants.append(bad)
        for field in ('relrowsecurity', 'relforcerowsecurity'):
            bad = deepcopy(catalog)
            bad['relations'][0][field] = not bad['relations'][0][field]
            variants.append(bad)
        for section in catalog:
            bad = deepcopy(catalog); bad[section].append({'unexpected': True})
            variants.append(bad)
            bad = deepcopy(catalog); del bad[section]
            variants.append(bad)
        bad = deepcopy(catalog)
        bad['constraints'].append({'contype': 'f', 'definition': 'FOREIGN KEY (id) REFERENCES other(id)'})
        variants.append(bad)
        bad = deepcopy(catalog); bad['dependencies'][0]['objid'] = 2
        variants.append(bad)
        bad = deepcopy(catalog); bad['indexes'][0]['indexdef'] += ' WHERE amount > 0'
        variants.append(bad)
        for bad in variants:
            before = deepcopy(bad)
            result = n.evaluate({}, diagnostics, b'synthetic', bad)
            assert result['overall'] == 'BLOCKED'
            assert 'FROZEN_EXTENDED_CATALOG_CHANGED' in result['mismatch_reasons']
            assert bad == before
        assert n.evaluate({}, diagnostics, b'synthetic', catalog)['overall'] == 'PASS'
    assert review['catalog_digest'] == b.EXTENDED_CATALOG_DIGEST
    print('PASS frozen catalog provenance; exact match; defaults/nullability/constraints/index/FK/RLS/trigger/dependency changes block without refreshing baseline')


if __name__ == '__main__':
    main()
