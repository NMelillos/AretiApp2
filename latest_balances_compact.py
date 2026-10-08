"""Executive balance list using the existing latest/approved-monthly-FX snapshot."""
from html import escape
from financial_decimal import optional_decimal, exact_sum
from latest_import_balances import snapshot


def currency_totals(rows):
    grouped={}
    for row in rows:
        value=optional_decimal(row.get('Closing balance'))
        if row.get('Status')=='IMPORTED' and row.get('Verification')=='SOURCE RECONCILIATION NOT VERIFIED' and value is not None:
            grouped.setdefault(row['Currency'],[]).append(value)
    return {key:exact_sum(values) for key,values in sorted(grouped.items())}


def compact_model(rows):
    visible=[]; included=[]; missing_rate=unverified=0
    for row in rows:
        native=optional_decimal(row.get('Closing balance'))
        eligible=(row.get('Status')=='IMPORTED' and row.get('Verification')=='SOURCE RECONCILIATION NOT VERIFIED')
        usd=optional_decimal(row.get('Closing balance converted to USD')) if eligible else None
        if eligible and usd is None:
            missing_rate+=1
        elif not eligible:
            unverified+=1
        if usd is not None: included.append(usd)
        visible.append(dict(account=str(row.get('Account name') or row.get('Account number') or 'Unknown account'),
            currency=str(row.get('Currency') or ''),closing=native,usd=usd))
    return visible,exact_sum(included),missing_rate,unverified


def compact_html(rows):
    visible,total,_,_=compact_model(rows)
    def money(value):return format(value,',f') if value is not None else '—'
    table='<div style="overflow-x:auto"><table class="compact-balances" style="font-size:10px;line-height:1.15;border-collapse:collapse;width:auto;table-layout:fixed"><thead><tr>'
    table+=''.join('<th style="padding:2px 5px;white-space:nowrap;text-align:left">'+label+'</th>' for label in ('Account','Currency','Closing balance','Closing balance USD'))
    table+='</tr></thead><tbody>'
    for row in visible:
        table+='<tr><td title="'+escape(row['account'],quote=True)+'" style="padding:2px 5px;white-space:nowrap;max-width:210px;overflow:hidden;text-overflow:ellipsis">'+escape(row['account'])+'</td>'
        table+=''.join('<td style="padding:2px 5px;white-space:nowrap;text-align:right">'+escape(str(value))+'</td>' for value in (row['currency'],money(row['closing']),money(row['usd'])))+'</tr>'
    table+='</tbody><tfoot><tr><th colspan="3" style="padding:3px 5px;text-align:right;white-space:nowrap">USD TOTAL</th><th style="padding:3px 5px;text-align:right;white-space:nowrap">'+format(total,',.2f')+'</th></tr></tfoot></table></div>'
    return table


def render(st,db):
    try:rows,_,_=snapshot(db)
    except Exception:
        st.error('Latest Import Balances is temporarily unavailable. Please try again.');return
    st.subheader('5. Latest Import Balances')
    st.markdown(compact_html(rows),unsafe_allow_html=True)
    _,_,missing,unverified=compact_model(rows)
    note='INTERIM PARTIAL REPORT: current Setup accounts; USD sum of visible included rows.'
    if missing:note+=f' Excludes {missing} account(s) with no approved FX rate.'
    if unverified:note+=f' Excludes {unverified} unverified/liability balance(s).'
    st.caption(note+' — marks balances excluded from the USD sum. Details: Latest Import Balances page.')
