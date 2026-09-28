"""Deterministic, privacy-scoped precondition contract; no production access."""
from decimal import Decimal
import copy


def main():
    from repair_readiness import state_hash, describe_fields
    row = dict(id=1, amount=Decimal('-12.30'), category='Synthetic', reviewed=0,
               split_parent_id=None, _read_version='100', statement_hash='synthetic')
    assert state_hash(row) == state_hash(dict(reversed(list(row.items()))))
    for field, value in [('amount', Decimal('-12.300000001')), ('category', 'Other'),
                         ('reviewed', 1), ('split_parent_id', 9), ('_read_version', '101')]:
        altered = dict(row, **{field: value})
        assert state_hash(row) != state_hash(altered), field
    assert state_hash(None) != state_hash('NULL')
    assert state_hash(Decimal('1.0')) != state_hash(Decimal('1.00'))
    assert state_hash(0.0) != state_hash(-0.0)
    fields = describe_fields('classified_transactions', row, {'amount': Decimal('-12.31')})
    assert all(r['Field'] != '_read_version' for r in fields)
    amount = next(r for r in fields if r['Field'] == 'amount')
    assert amount['Current database value'] == '-12.30'
    assert amount['Proposed PDF/parser value'] == '-12.31'
    assert amount['Disposition'] == 'PDF DIFFERENCE - DIAGNOSTIC ONLY'
    assert next(r for r in fields if r['Field'] == 'category')['Disposition'] == 'UNCHANGED / PRESERVE'
    try: describe_fields('classified_transactions', dict(row, password='must-not-display'), {})
    except ValueError: pass
    else: raise AssertionError('Unexpected sensitive column was exposed')
    print('PASS readiness hashes: ordering, exact precision, types, null, versions, metadata changes; preserve labels and unknown-field rejection')


if __name__ == '__main__':
    main()
