"""Explicit migration scope. Importing this module performs no database work."""
FINANCIAL_COLUMNS = {
    'classified_transactions': ('amount', 'amount_usd', 'split_original_amount', 'fx_rate'),
    'statement_balances': ('opening_balance', 'money_out', 'money_in', 'closing_balance'),
    'rates': ('rate_value',),
}

REPAIR_FIELDS = {
    'classified_transactions': frozenset({'amount'}),
    'statement_balances': frozenset(FINANCIAL_COLUMNS['statement_balances']),
}
