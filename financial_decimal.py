"""Finite decimal boundaries and precision-sized monetary arithmetic."""
from decimal import Decimal, InvalidOperation, localcontext, ROUND_HALF_EVEN


def decimal_value(value):
    if isinstance(value, bool):
        raise ValueError('Invalid financial value')
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('Invalid financial value') from None
    if not result.is_finite():
        raise ValueError('Non-finite financial value')
    return result


def optional_decimal(value):
    try:
        return decimal_value(value)
    except ValueError:
        return None


def exact_sum(values):
    values = [decimal_value(v) for v in values]
    if not values:
        return Decimal(0)
    low = min(v.as_tuple().exponent for v in values)
    high = max(v.adjusted() for v in values)
    with localcontext() as context:
        context.prec = max(28, high - low + len(str(len(values))) + 3)
        return sum(values, Decimal(0))


def product(left, right):
    left, right = decimal_value(left), decimal_value(right)
    with localcontext() as context:
        context.prec = max(28, len(left.as_tuple().digits) + len(right.as_tuple().digits) + 2)
        return left * right


def cents(value):
    value = decimal_value(value)
    with localcontext() as context:
        context.prec = max(28, len(value.as_tuple().digits), value.adjusted() + 4)
        return value.quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN)


def reciprocal(value):
    value = decimal_value(value)
    if not value:
        raise ValueError('Zero exchange rate')
    # Inversion is inherently rounded for recurring decimals. Keep at least
    # decimal128 precision; never quantize an exchange rate to currency cents.
    with localcontext() as context:
        context.prec = max(34, len(value.as_tuple().digits) + 16)
        context.rounding = ROUND_HALF_EVEN
        return Decimal(1) / value


def excel_value(value):
    # Excel numeric cells cannot retain arbitrary NUMERIC precision.
    return str(value) if isinstance(value, Decimal) else value


def exact_frame(frame):
    return frame.apply(lambda column: column.map(excel_value))


def quotient(numerator, denominator):
    numerator, denominator = decimal_value(numerator), decimal_value(denominator)
    with localcontext() as context:
        context.prec = max(34, len(numerator.as_tuple().digits) + len(denominator.as_tuple().digits)
                           + abs(numerator.as_tuple().exponent - denominator.as_tuple().exponent) + 16)
        return numerator / denominator


def decimal_mean(values):
    values = [v for item in values if (v := optional_decimal(item)) is not None]
    return quotient(exact_sum(values), len(values)) if values else Decimal(0)


def decimal_std(values):
    values = [v for item in values if (v := optional_decimal(item)) is not None]
    if len(values) < 2:
        return None
    mean = decimal_mean(values)
    differences = [exact_sum([v, -mean]) for v in values]
    variance = quotient(exact_sum(product(v, v) for v in differences), len(values) - 1)
    with localcontext() as context:
        context.prec = max(34, len(variance.as_tuple().digits) + 16)
        return variance.sqrt()
