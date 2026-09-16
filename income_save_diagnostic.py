"""Temporary, value-free diagnostics for a rejected Income/Charity Save."""
from db import ConcurrentTransactionEditError


class _IncomeCharityConflict(ConcurrentTransactionEditError):
    def __init__(self, predicates):
        RuntimeError.__init__(
            self, 'Income/Charity Save conflict predicates: ' + ', '.join(predicates)
        )


def raise_conflict(cursor, transaction_id, snapshot):
    # Re-evaluate only the existing UPDATE guards, with its original parameters.
    # This observation can differ if another writer commits after that UPDATE.
    names = ('CATEGORY', 'SUBCATEGORY', 'REVIEWED', 'STATUS',
             'AMOUNT', 'AMOUNT_USD', 'CURRENCY', 'FX_RATE')
    try:
        cursor.execute('''
            SELECT category IS NOT DISTINCT FROM ?,
                   subcategory IS NOT DISTINCT FROM ?,
                   reviewed IS NOT DISTINCT FROM ?,
                   status IS NOT DISTINCT FROM ?,
                   amount IS NOT DISTINCT FROM CAST(? AS REAL),
                   amount_usd IS NOT DISTINCT FROM CAST(? AS REAL),
                   currency IS NOT DISTINCT FROM ?,
                   fx_rate IS NOT DISTINCT FROM CAST(? AS REAL)
            FROM classified_transactions
            WHERE id = ?
        ''', (snapshot[0], snapshot[1], snapshot[2], snapshot[3],
              snapshot[8], snapshot[9], snapshot[10], snapshot[11], transaction_id))
        result = cursor.fetchone()
        if result is None:
            failed = ('ROW_MISSING',)
        else:
            failed = tuple(name for name, matched in zip(names, result) if not matched)
            if not failed:
                failed = ('NONE_OBSERVED',)
    except Exception:
        # Never expose a driver's SQL/parameter-bearing error. The caller still
        # rolls back the complete Save, including earlier rows in the batch.
        failed = ('DIAGNOSTIC_UNAVAILABLE',)
    raise _IncomeCharityConflict(failed) from None
