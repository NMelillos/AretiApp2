"""Reuse completed English Revolut/CNB source proofs for independent balances only."""
import re


def prepare(db,frame):
    accounts=db.get_accounts()
    evidence=frame.attrs.get('revolut_source_account')
    if evidence:
        from revolut_account_context import effective_account
        compact=lambda value:re.sub(r'[^\w]','',str(value or '')).upper()
        choices=accounts[accounts.bank.map(compact).isin(('REVOLUT','REVOLUTBANK','REVOLUTBANKUAB'))
            & accounts.account_number.map(compact).eq(evidence['account_number'])
            & accounts.currency.fillna('').str.strip().str.upper().isin(('',evidence['currency']))]
        if len(choices)!=1:raise ValueError('English Revolut requires one unambiguous source account/currency match.')
        original=choices.iloc[0].to_dict()
        balance={key:evidence[key] for key in ('account_number','currency','period_start','period_end','opening_balance','money_in','money_out','closing_balance')}
        balance.update(bank='Revolut',source='English Revolut Completed Source')
        account=effective_account(frame,balance,original)
        checked=frame.copy();checked['account_number']=account['account_number'];checked['currency']=account['currency']
        effective_account(checked,balance,original)
    elif frame.attrs.get('statement_balance',{}).get('source')=='CNB Account Summary':
        from cnb_import import cnb_account,validate_preview
        original=cnb_account(frame,accounts);account=dict(original)
        balance=dict(frame.attrs['statement_balance'])
        checked=frame.copy();checked['account_number']=account['account_number'];checked['currency']=account['currency']
        validate_preview(checked,balance,account)
    else:
        raise ValueError('No supported completed English Revolut or CNB source proof.')
    # Currency resolved by an existing source-bound transaction convention must
    # not masquerade as a Setup change or evade the atomic stale-Setup check.
    account['_setup_identity']=tuple(original.get(k) for k in ('account_name','bank','account_number','currency'))
    return checked,balance,account
