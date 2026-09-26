"""Explicit, atomic editing of existing allocations, without schema changes."""
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, localcontext
import hashlib
import json

import pandas as pd

import db


class SplitEditError(ValueError):
    pass


def number(value):
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise SplitEditError('Enter a valid signed decimal amount.') from None
    if (not result.is_finite() or result.copy_abs() > Decimal(str(db.MAX_SAFE_FINANCIAL_AMOUNT))
            or abs(result.as_tuple().exponent) > 1000 or len(result.as_tuple().digits) > 1000):
        raise SplitEditError('Enter a finite amount within the supported range.')
    return result


def _token(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()


def _rows(cur, parent_id, lock=False):
    extra = ', xmin::text AS _version' if db.USING_POSTGRES else ''
    if db.USING_POSTGRES:
        extra += ''.join(', ' + col + '::text AS _exact_' + col
                         for col in ('amount', 'amount_usd', 'fx_rate', 'split_original_amount'))
    cur.execute('SELECT *' + extra + ' FROM classified_transactions '
                'WHERE id=? OR split_parent_id=? ORDER BY id' +
                (' FOR UPDATE' if lock and db.USING_POSTGRES else ''), (parent_id, parent_id))
    names = [d[0] for d in cur.description]
    rows = [dict(zip(names, r)) for r in cur.fetchall()]
    if db.USING_POSTGRES:
        for row in rows:
            for col in ('amount', 'amount_usd', 'fx_rate', 'split_original_amount'):
                row[col] = row.pop('_exact_' + col)
    return rows


def _structure(rows, parent_id):
    parents = [r for r in rows if r['id'] == parent_id]
    children = [r for r in rows if r['id'] != parent_id]
    if len(parents) != 1 or len(children) < 2:
        raise SplitEditError('A complete existing split group is required.')
    parent = parents[0]
    group = parent.get('split_group_id')
    if not group or parent.get('split_parent_id') is not None or parent.get('status') != 'excluded':
        raise SplitEditError('The original split transaction is inconsistent.')
    indexes = [r.get('split_allocation_index') for r in children]
    if None in indexes or len(set(indexes)) != len(children):
        raise SplitEditError('The split allocation links are inconsistent.')
    for row in children:
        if (row.get('split_parent_id') != parent_id or row.get('split_group_id') != group
                or row.get('status') not in ('pending', 'reviewed')
                or any(row.get(c) != parent.get(c) for c in (
                    'statement_hash', 'account_name', 'bank', 'account_number', 'currency', 'rate_type'))
                or row.get('split_original_amount') is None
                or number(row['split_original_amount']) != number(parent['amount'])):
            raise SplitEditError('The split source identity or allocation state is inconsistent.')
    return parent, sorted(children, key=lambda r: r['split_allocation_index'])


def load_group(parent_id):
    conn = None
    try:
        conn = db.get_connection()
        cur = conn.cursor()
        if db.USING_POSTGRES:
            cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            cur.execute('SET LOCAL extra_float_digits=3')
        rows = _rows(cur, int(parent_id))
        _structure(rows, int(parent_id))
        return dict(parent_id=int(parent_id), rows=rows, token=_token(rows))
    except SplitEditError:
        raise
    except Exception:
        raise SplitEditError('The split could not be loaded safely.') from None
    finally:
        if conn is not None:
            conn.rollback()
            conn.close()


def _validate_total(parent, children, field='amount'):
    expected = parent.get(field)
    values = [r.get(field) for r in children]
    if expected is None and all(v is None for v in values):
        return
    if expected is None or any(v is None for v in values):
        raise SplitEditError('The split financial values are incomplete.')
    expected = number(expected)
    amounts = [number(v) for v in values]
    with localcontext() as context:
        context.prec = max(100, sum(len(v.as_tuple().digits) + abs(v.as_tuple().exponent)
                                    for v in [expected, *amounts]) + 10)
        if sum(amounts, Decimal(0)) != expected:
            raise SplitEditError('Split amounts must equal the unchanged original total. Nothing was saved.')
    if field == 'amount' and (expected == 0 or any(v == 0 or v.is_signed() != expected.is_signed() for v in amounts)):
        raise SplitEditError('Every allocation must retain the original debit or credit sign.')
    if field == 'amount_usd' and any(v != 0 and v.is_signed() != expected.is_signed() for v in amounts):
        raise SplitEditError('USD allocations must retain the original USD sign.')


def save_group(snapshot, edits):
    """Snapshot comparison under locks protects every source and sibling field."""
    parent_id = int(snapshot['parent_id'])
    conn = None
    try:
        conn = db.get_connection()
        cur = conn.cursor()
        if db.USING_POSTGRES:
            cur.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE')
            cur.execute('SET LOCAL lock_timeout=3000')
            cur.execute('SET LOCAL statement_timeout=15000')
            cur.execute('SET LOCAL extra_float_digits=3')
        else:
            cur.execute('BEGIN IMMEDIATE')
        rows = _rows(cur, parent_id, lock=True)
        if _token(rows) != snapshot['token']:
            raise SplitEditError('This split changed in another session. Reload the group before saving.')
        parent, children = _structure(rows, parent_id)
        _validate_total(parent, children)
        _validate_total(parent, children, 'amount_usd')
        if (len(edits) != len(children) or len({r['id'] for r in edits}) != len(children)
                or {r['id'] for r in edits} != {r['id'] for r in children}):
            raise SplitEditError('Save must include every existing allocation exactly once.')
        allowed = {'id', 'amount', 'category', 'subcategory', 'report_group', 'reviewed'}
        if any(set(r) - allowed for r in edits):
            raise SplitEditError('Immutable split fields cannot be edited.')
        cur.execute("SELECT category, COALESCE(subcategory,''), COALESCE(report_group,'') FROM category_list")
        taxonomy = {}
        for category, subcategory, group in cur.fetchall():
            taxonomy.setdefault((category, subcategory), set()).add(group)
        submitted = {r['id']: r for r in edits}
        updated = []
        for row in children:
            edit = submitted[row['id']]
            pair = (str(edit['category']).strip(), str(edit['subcategory']).strip())
            groups = taxonomy.get(pair, set())
            if len(groups) != 1 or str(edit['report_group']) not in groups:
                raise SplitEditError('Choose a valid Category / Subcategory and its Setup reporting group.')
            if not isinstance(edit['reviewed'], bool):
                raise SplitEditError('Reviewed must be checked or unchecked.')
            next_row = dict(row, amount=str(number(edit['amount'])), category=pair[0], subcategory=pair[1],
                            reviewed=int(edit['reviewed']), status='reviewed' if edit['reviewed'] else 'pending')
            updated.append(next_row)
        _validate_total(parent, updated)
        reallocate = any(number(a['amount']) != number(b['amount']) for a, b in zip(children, updated))
        if reallocate and parent['amount_usd'] is not None:
            total, usd = number(parent['amount']), number(parent['amount_usd'])
            quantum = Decimal(1).scaleb(min(-2, usd.as_tuple().exponent))
            with localcontext() as context:
                context.prec = max(100, sum(len(number(r['amount']).as_tuple().digits) for r in updated)
                                   + len(usd.as_tuple().digits) + abs(usd.as_tuple().exponent) + 50)
                units = int(usd.copy_abs() / quantum)
                quotas = [Decimal(units) * number(r['amount']).copy_abs() / total.copy_abs() for r in updated]
                allocated = [int(q.to_integral_value(rounding=ROUND_FLOOR)) for q in quotas]
                # Largest remainders conserve the source USD total without a
                # negative last allocation. Equal remainders follow stable order.
                order = sorted(range(len(updated)), key=lambda i: (-(quotas[i] - allocated[i]), i))
                for index in order[:units - sum(allocated)]:
                    allocated[index] += 1
                for row, count in zip(updated, allocated):
                    row['amount_usd'] = str((Decimal(count) * quantum).copy_sign(usd))
        _validate_total(parent, updated, 'amount_usd')
        changed = 0
        for before, after in zip(children, updated):
            fields = [c for c in ('category', 'subcategory', 'reviewed', 'status') if before[c] != after[c]]
            if reallocate:
                fields += [c for c in ('amount', 'amount_usd')
                           if before[c] is not None and number(before[c]) != number(after[c])]
            if not fields:
                continue
            if before['reviewed'] != after['reviewed']:
                fields.append('reviewed_at')
                after['reviewed_at'] = db._now() if after['reviewed'] else before['reviewed_at']
            cur.execute('UPDATE classified_transactions SET ' + ','.join(c + '=?' for c in fields) +
                        ' WHERE id=?', (*[after[c] for c in fields], before['id']))
            if cur.rowcount != 1:
                raise SplitEditError('A split allocation changed during Save.')
            for field in fields:
                db._audit_transaction_change(cur, before['id'], field, before[field], after[field], 'split_edit')
            changed += 1
        actual = _rows(cur, parent_id)
        actual_parent, actual_children = _structure(actual, parent_id)
        if actual_parent != parent or len(actual_children) != len(updated):
            raise SplitEditError('The original transaction changed during Save.')
        for desired, stored in zip(updated, actual_children):
            for col in set(desired) - {'_version'}:
                same = (desired[col] == stored[col] if col not in ('amount', 'amount_usd')
                        else desired[col] is None and stored[col] is None
                        or desired[col] is not None and stored[col] is not None and number(desired[col]) == number(stored[col]))
                if not same:
                    raise SplitEditError('Storage could not preserve the requested split values. Nothing was saved.')
        _validate_total(actual_parent, actual_children)
        _validate_total(actual_parent, actual_children, 'amount_usd')
        conn.commit()
        return changed
    except SplitEditError:
        if conn is not None: conn.rollback()
        raise
    except Exception:
        if conn is not None: conn.rollback()
        raise SplitEditError('The split could not be saved safely. Reload the group and try again.') from None
    finally:
        if conn is not None: conn.close()


def render_editor(st, frame, categories, prefix, clear_caches):
    if frame.empty or 'split_parent_id' not in frame:
        return
    ids = sorted({int(v) for v in frame.split_parent_id.dropna()})
    if not ids:
        return
    with st.expander('Edit existing split allocations'):
        selected = st.selectbox('Original transaction ID', ids, key=prefix + '_existing_split_id')
        key = prefix + '_existing_split_snapshot'
        if key not in st.session_state or st.session_state[key]['parent_id'] != selected:
            try:
                st.session_state[key] = load_group(selected)
            except SplitEditError as error:
                st.error(str(error))
                return
        snapshot = st.session_state[key]
        parent, children = _structure(snapshot['rows'], selected)
        pairs = {}
        for row in categories.to_dict('records'):
            pair = (str(row['category']), str(row.get('subcategory') or ''))
            label = ' / '.join(pair)
            mapping = (*pair, str(row.get('report_group') or ''))
            if label in pairs and pairs[label] != mapping:
                st.error('Setup contains ambiguous classifications. No split changes can be saved.')
                return
            pairs[label] = mapping
        data = pd.DataFrame([dict(id=r['id'], amount=str(r['amount']),
                                 classification=' / '.join((r['category'] or '', r['subcategory'] or '')),
                                 reviewed=bool(r['reviewed'])) for r in children])
        generation = st.session_state.get(key + '_generation', 0)
        editor_key = key + '_' + snapshot['token'] + '_' + str(generation)
        st.caption('Original total: ' + str(parent['amount']) + ' ' + str(parent['currency']))
        with st.form(editor_key + '_form'):
            edited = st.data_editor(data, hide_index=True, use_container_width=True, num_rows='fixed',
                key=editor_key, column_config={
                    'id': st.column_config.NumberColumn('ID', disabled=True),
                    'amount': st.column_config.TextColumn('Signed amount', required=True),
                    'classification': st.column_config.SelectboxColumn('Category / Subcategory', options=list(pairs), required=True),
                    'reviewed': st.column_config.CheckboxColumn('Reviewed')})
            save = st.form_submit_button('Save split changes', type='primary')
            cancel = st.form_submit_button('Cancel split changes')
        if cancel:
            st.session_state.pop(key, None)
            st.session_state[key + '_generation'] = generation + 1
            st.rerun()
        # Group remains derived from the same Setup pair used by all reports.
        st.dataframe(pd.DataFrame([{'ID': r['id'], 'Reporting group': pairs.get(r['classification'], ('', '', ''))[2]}
                                  for r in edited.to_dict('records')]), hide_index=True)
        if save:
            try:
                payload = []
                for row in edited.to_dict('records'):
                    if row['classification'] not in pairs:
                        raise SplitEditError('Choose an existing Setup classification.')
                    category, subcategory, group = pairs[row['classification']]
                    payload.append(dict(id=row['id'], amount=row['amount'], category=category,
                                        subcategory=subcategory, report_group=group, reviewed=row['reviewed']))
                count = save_group(snapshot, payload)
            except SplitEditError as error:
                st.error(str(error))
            else:
                clear_caches()
                st.session_state.pop(key, None)
                st.session_state[key + '_generation'] = generation + 1
                st.success(str(count) + ' split allocations saved.')
                st.rerun()
