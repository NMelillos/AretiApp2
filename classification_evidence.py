"""Read-only batch indexes for distinctive history and existing taxonomy labels."""
import re


NOISE = set('account atm auth bank card cash commission credit debit fee fees from to '
            'payment payments purchase purchases transfer withdrawal online internet web '
            'pmt autopay auto direct pos ref reference trace transaction statement monthly '
            'subscription inc ltd limited llc company corp corporation com www http https '
            'service services trading member since continue next page united states '
            'london nicosia angeles francisco october november december january february '
            'march april may june july august september help trip city town store shop '
            'paid sale plan basic fund funds los san tips out advice shareholder'.split())


def words(value):
    return tuple(re.findall(r'[^\W\d_]+', str(value or '').casefold(), re.UNICODE))


def distinctive(value):
    return {w for w in words(value) if len(w) >= 4 and w not in NOISE}


def history_index(memory, categories):
    active = {str(c).casefold(): c for c in categories}
    index = {}
    for row in memory.to_dict('records'):
        category = active.get(str(row.get('category', '')).casefold())
        if not category:
            continue
        pair = (category, str(row.get('subcategory') or '').strip())
        text = row.get('normalized_description') or row.get('original_description') or ''
        for token in distinctive(text):
            index.setdefault(token, {}).setdefault(pair, text)
    return index


def history_match(description, index):
    found = [index[t] for t in distinctive(description) if t in index]
    pairs = {pair for item in found for pair in item}
    if len(pairs) != 1:
        return None, bool(pairs)
    pair = next(iter(pairs))
    return (pair, next(item[pair] for item in found)), False


def taxonomy_index(taxonomy):
    index = {}
    for row in taxonomy.to_dict('records'):
        category, subcategory = str(row['category']), str(row.get('subcategory') or '')
        for label, pair in ((category, (category, '')), (subcategory, (category, subcategory))):
            tokens = words(label)
            if not tokens or not any(len(t) >= 4 and t not in NOISE for t in tokens):
                continue
            index.setdefault(tokens[0], []).append((tokens, pair))
    return index


def taxonomy_match(description, index):
    tokens = words(description)
    matches = []
    for offset, token in enumerate(tokens):
        for label, pair in index.get(token, ()):
            if tokens[offset:offset+len(label)] == label:
                matches.append(((len(label), sum(map(len, label)), bool(pair[1])), pair))
    if not matches:
        return None, False
    score = max(score for score, _ in matches)
    pairs = {pair for rank, pair in matches if rank == score}
    return (next(iter(pairs)), False) if len(pairs) == 1 else (None, True)
