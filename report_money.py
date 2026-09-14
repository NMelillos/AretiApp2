"""Decimal conversion at the report boundary; no early monetary rounding."""
from decimal import Decimal, InvalidOperation


def decimal_amount(value):
    if value is None:
        return Decimal(0)
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return Decimal(0)
    return result if result.is_finite() else Decimal(0)


def decimal_sum(values):
    return sum((decimal_amount(value) for value in values), Decimal(0))
