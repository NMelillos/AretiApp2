"""Fail-closed image fallback for scanned Fifth Third commercial statements."""
from datetime import datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading

from fifth_third_import import FifthThirdParseError, recognized, parse

ROOT = Path(__file__).resolve().parent
MONEY = r'(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}'
LOCK = threading.BoundedSemaphore(1)


def fail():
    raise FifthThirdParseError('VALIDATION FAILED / MANUAL REVIEW — scanned Fifth Third statement could not be verified; no rows imported.')


def structural(text):
    text = re.sub(r'\s+', ' ', text).upper()
    return all(label in text for label in ('FIFTH THIRD', 'ACCOUNT NUMBER:',
        'ACCOUNT SUMMARY', 'STATEMENT PERIOD DATE:', 'BEGINNING BALANCE', 'ENDING BALANCE'))


def candidate(text):
    text = re.sub(r'\s+', ' ', text).upper()
    return 'FIFTH THIRD' in text and all(label in text for label in
        ('ACCOUNT NUMBER:', 'ACCOUNT SUMMARY', 'STATEMENT PERIOD DATE:'))


def image_pages(pdf):
    return 1 <= len(pdf.pages) <= 2 and all(any(
        image['width'] * image['height'] >= page.width * page.height * .8
        for image in page.images) for page in pdf.pages)


def parse_document(pdf, pages):
    text = '\n'.join(pages)
    if recognized(text):
        try:
            return parse(pages)
        except FifthThirdParseError:
            if not image_pages(pdf):
                raise
    elif not candidate(text):
        return None
    elif not image_pages(pdf):
        fail()
    if not LOCK.acquire(blocking=False):
        fail()
    try:
        with tempfile.TemporaryDirectory(prefix='areti-fifth-ocr-') as folder:
            files = []
            for index, page in enumerate(pdf.pages):
                # Bound image memory; no rescaling of numeric tokens or OCR repair.
                if page.width * page.height * (300 / 72) ** 2 > 12_000_000:
                    fail()
                target = Path(folder) / f'page-{index}.png'
                page.to_image(resolution=300).save(target)
                files.append(str(target))
            env = {k: os.environ[k] for k in ('PATH', 'SystemRoot', 'WINDIR', 'TEMP', 'TMP') if k in os.environ}
            result = subprocess.run(['node', str(ROOT / 'fifth_ocr' / 'read.cjs'), *files],
                capture_output=True, text=True, encoding='utf-8', timeout=90, env=env)
            if result.returncode:
                fail()
            return parse_ocr(json.loads(result.stdout)['pages'])
    except FifthThirdParseError:
        raise
    except Exception:
        fail()
    finally:
        LOCK.release()


def parse_ocr(pages):
    """All values derive from the fresh reading; never repair numeric characters."""
    from parsing import _frame_from_pdf_rows
    if not 1 <= len(pages) <= 2 or not structural(pages[0]):
        fail()
    for i, page in enumerate(pages, 1):
        if len(re.findall(rf'\bPage\s+{i}\s+of\s*{len(pages)}\b', page)) != 1:
            fail()
    if len(pages) == 2:
        second = re.sub(r'\s+', ' ', pages[1]).upper()
        if not all(s in second for s in ('FIFTH THIRD', 'BALANCING', 'ELECTRONIC TRANSACTIONS')):
            fail()
        if re.search(r'ACCOUNT SUMMARY|BEGINNING BALANCE|ENDING BALANCE\s+\$[\d,]+\.\d{2}', second):
            fail()
        if re.search(r'\b\d{2}/\d{2}\s+' + MONEY, second):
            fail()
    text = pages[0]
    def unique(pattern):
        values = re.findall(pattern, text, re.MULTILINE)
        if len(values) != 1:
            fail()
        return values[0]
    def money(value):
        if not re.fullmatch(MONEY, value):
            fail()
        return Decimal(value.replace(',', ''))
    account = unique(r'\bAccount Number:\s*(\d+)\s*$')
    if account != unique(r'^Account Summary\s*-\s*(\d+)\s*$'):
        fail()
    dates = unique(r'^Statement Period Date:\s*(\d{1,2}/\d{1,2}/\d{4})\s*-\s*(\d{1,2}/\d{1,2}/\d{4})\s*$')
    try:
        start, end = (datetime.strptime(d, '%m/%d/%Y').date() for d in dates)
    except ValueError:
        fail()
    if start > end or (start.year, start.month) != (end.year, end.month):
        fail()
    od, opening = unique(rf'^(\d{{2}}/\d{{2}}) Beginning Balance \$({MONEY})(?:\s+Number of Days in Period \d+)?\s*$')
    cd, closing = unique(rf'^(\d{{2}}/\d{{2}}) Ending Balance \$({MONEY})\s*$')
    if od != start.strftime('%m/%d') or cd != end.strftime('%m/%d'):
        fail()
    opening, closing = money(opening), money(closing)
    labels = ('Checks', 'Withdrawals / Debits', 'Deposits / Credits')
    expected = {}
    for label in labels:
        count, total = unique(rf'^(\d+) {re.escape(label)} \$\(?({MONEY})\)?\s*$')
        expected[label] = (int(count), money(total))
    observed = {label: [] for label in labels}
    headers = set()
    section = None
    daily = {}
    for raw in text.splitlines():
        line = re.sub(r'\s+', ' ', raw).strip()
        header = re.fullmatch(rf'(Checks?|Withdrawals / Debits|Deposits / Credits) (\d+) (?:checks?|items?) totaling \$({MONEY})', line)
        if header:
            section = 'Checks' if header[1] in ('Check', 'Checks') else header[1]
            if section in headers or expected[section] != (int(header[2]), money(header[3])):
                fail()
            headers.add(section)
            continue
        if 'Daily Balance Summary' in line:
            section = 'daily'
            continue
        if section == 'daily':
            pairs = re.findall(rf'\b(\d{{2}}/\d{{2}}) ({MONEY})(?=\s|$)', line)
            if re.search(r'\d{2}/\d{2}', line) and len(pairs) != len(re.findall(r'\d{2}/\d{2}', line)):
                fail()
            for date, value in pairs:
                if date in daily:
                    fail()
                daily[date] = money(value)
            continue
        if section not in observed:
            continue
        if section == 'Checks':
            row = re.fullmatch(rf'(\d+)\s+[is]\s+(\d{{2}}/\d{{2}}) ({MONEY})', line)
            if row:
                observed[section].append([row[2], 'Check ' + row[1], money(row[3])])
            elif re.search(r'\d{2}/\d{2}', line):
                fail()
            elif re.search(r'\d[.,]\w', line):
                fail()
        else:
            row = re.search(rf'(?<!\d)(\d{{2}}/\d{{2}}) ({MONEY}) (.+)$', line)
            if row:
                observed[section].append([row[1], row[3], money(row[2])])
            elif re.search(r'\d{2}/\d{2}', line):
                fail()
            elif line.startswith('FROM CARD') and observed[section]:
                observed[section][-1][1] += ' | ' + line
            elif re.search(r'\d[.,]\w', line):
                fail()
    if headers != set(labels) or not daily:
        fail()
    rows = []
    for label in labels:
        entries = observed[label]
        if (len(entries), sum((v for _, _, v in entries), Decimal(0))) != expected[label]:
            fail()
        for date, description, value in entries:
            try:
                when = datetime.strptime(date + '/' + str(start.year), '%m/%d/%Y').date()
            except ValueError:
                fail()
            if not start <= when <= end:
                fail()
            rows.append([when.isoformat(), description, value if label == labels[2] else -value, 'USD', 'Fifth Third labelled sections'])
    credits = expected[labels[2]][1]
    debits = expected[labels[0]][1] + expected[labels[1]][1]
    if opening + credits - debits != closing:
        fail()
    activity = {}
    for when, _, amount, _, _ in rows:
        activity[when[5:].replace('-', '/')] = activity.get(when[5:].replace('-', '/'), Decimal(0)) + amount
    if not set(activity).issubset(daily):
        fail()
    running = opening
    for date in sorted(daily):
        try:
            when = datetime.strptime(date + '/' + str(start.year), '%m/%d/%Y').date()
        except ValueError:
            fail()
        if not start <= when <= end:
            fail()
        running += activity.get(date, Decimal(0))
        if running != daily[date]:
            fail()
    if running != closing:
        fail()
    frame = _frame_from_pdf_rows(rows)
    frame.attrs['statement_balance'] = dict(bank='Fifth Third Bank', account_number=account,
        currency='USD', period_start=start.isoformat(), period_end=end.isoformat(),
        opening_balance=opening, money_in=credits, money_out=debits, closing_balance=closing,
        source='Fifth Third labelled sections', source_movements=[(r[0], r[2]) for r in rows],
        transaction_count=len(rows), reconciliation_convention='deposit',
        extraction='validated image OCR', daily_balance_count=len(daily))
    return frame
