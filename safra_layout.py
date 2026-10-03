"""Safra-only description wrapping, established by PDF table geometry.

The strict lexer remains responsible for every date, amount and reconciliation.
Text without layout evidence is never granted continuation status.
"""
import re

DATE = r"\d{2}\.\d{2}\.\d{4}"
MONEY = r"[+-]?(?:\d{1,3}(?: \d{3})+|\d+),\d{2}"
BOOKING = re.compile(rf"({DATE} \d+ )(.+?)( {DATE} {MONEY} {MONEY})")


def booking_text(page, text):
    if not hasattr(page, 'extract_words'):
        return text
    words = page.extract_words()
    lines = []
    for word in words:
        if not lines or abs(word['top'] - lines[-1][0]['top']) > 1.5:
            lines.append([])
        lines[-1].append(word)
    normalized = lambda line: ' '.join(w['text'] for w in line)
    headers = [line for line in lines if 'Transaction' in [w['text'] for w in line]
               and 'Debit' in [w['text'] for w in line]
               and 'Credit' in [w['text'] for w in line]]
    if len(headers) != 1:
        return text  # No reliable geometry: the strict lexer must decide.
    header = headers[0]
    description_left = next(w['x0'] for w in header if w['text'] == 'Transaction')
    value_left = next((w['x0'] for w in header if w['text'] == 'Value'), None)
    if value_left is None:
        return text
    source = [re.sub(r'\s+', ' ', line).strip() for line in text.splitlines()]
    replacements = {}
    consumed = set()
    for index, line in enumerate(source):
        match = BOOKING.fullmatch(line)
        if not match:
            continue
        locations = [row for row in lines if normalized(row) == line]
        if len(locations) != 1:
            continue
        previous = locations[0]
        tails = []
        tail_index = index + 1
        while tail_index < len(source):
            tail = source[tail_index]
            candidates = [row for row in lines if normalized(row) == tail]
            if len(candidates) != 1 or not tail:
                break
            row = candidates[0]
            height = max(w['bottom'] - w['top'] for w in previous)
            gap = row[0]['top'] - previous[0]['top']
            # Only an immediately following line aligned with the description
            # column; a date/reference/value/amount-column line stays unknown.
            if (not 0 < gap <= height * 1.6
                    or abs(row[0]['x0'] - description_left) > 1
                    or any(w['x0'] < description_left - 1 or w['x1'] >= value_left - 5 for w in row)
                    or re.search(DATE, tail) or re.search(MONEY, tail)
                    or re.match(r'^(?:No bookings|Account statement|Current account|Account number|(?:Client number )?Date Ref\.)', tail)
                    or BOOKING.fullmatch(tail)):
                break
            tails.append(tail)
            consumed.add(tail_index)
            previous = row
            tail_index += 1
        if tails:
            replacements[index] = match[1] + match[2] + ' ' + ' '.join(tails) + match[3]
    return '\n'.join(replacements.get(i, line) for i, line in enumerate(source) if i not in consumed)
