"""Conservative historical conversion: preserve evidence, never infer lost digits."""
from financial_schema import FINANCIAL_COLUMNS


def preservation_manifest(snapshot):
    """Unknown float origins remain explicitly unverified, not reconstructed.

    The snapshot has decoded server IEEE bytes to exact Decimal. Carrying that
    value forward changes storage only; it does not certify the source amount.
    Authoritative source corrections are a separate fingerprint-bound allowlist.
    """
    types = {(r[0], r[1]): r[2] for r in snapshot['schema']}
    result = []
    for table, rows in snapshot['tables'].items():
        for row in rows:
            for field in FINANCIAL_COLUMNS[table]:
                value = row[field]
                mode = ('NULL_PRESERVE' if value is None else
                        'EXACT_NUMERIC_PRESERVE' if types[table, field] == 'numeric'
                        else 'LEGACY_BINARY_UNVERIFIED')
                result.append(dict(table=table, id=row['id'], field=field,
                    old=None if value is None else str(value),
                    new=None if value is None else str(value), policy=mode,
                    evidence='Exact stored value; source correction requires separate verified evidence'))
    return result
