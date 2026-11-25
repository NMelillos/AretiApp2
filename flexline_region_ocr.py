"""Bounded local, labelled-region evidence for image-only Equity Flexline PDFs.

No activity is imported here. No OCR character is repaired. Layout coordinates
select labelled regions, never values; disagreement rejects the document.
"""
from collections import OrderedDict
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time

MONEY = r'-?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}'
REGIONS = {
    'issuer': (.16, .073, .32, .096),
    'owner': (.185, .14, .34, .157),
    'product': (.44, .195, .704, .218),
    'identity': (.58, .044, .945, .168),
    'dates': (.096, .387, .948, .424),
    'history': (.096, .45, .947, .49),
    'interest': (.096, .503, .947, .562),
    'summary': (.096, .574, .947, .63),
    **{f'cell{i}': ((82 + 104*i)/850, .75, (183 + 104*i)/850, .806)
       for i in range(7)},
}
_CACHE = OrderedDict()
_CACHE_LOCK = threading.Lock()
# Cold baseline timed out per batch at 0.12 CPU; one worker took 22.42s.
# Allow bounded cold/resource headroom with the existing scanned route's 90s cap.
OCR_TIMEOUT = 90
_LOG = logging.getLogger(__name__)


def diagnostic(stage, status, started, regions=0):
    """Operational fields only: never exception messages or OCR/source content."""
    _LOG.warning('flexline_ocr stage=%s status=%s regions=%d elapsed_ms=%d',
                 stage, status, regions, round((time.monotonic() - started) * 1000))


def worker_diagnostics(stderr, count):
    """Forward only the fixed operational schema, including on a timeout."""
    if isinstance(stderr, bytes):
        stderr = stderr.decode('utf-8', errors='replace')
    for line in (stderr or '').splitlines():
        try:
            event = json.loads(line)
            if (event.get('stage') in ('model_identity', 'worker_start', 'recognize', 'worker_stop', 'input_bound')
                    and event.get('status') in ('ok', 'begin', 'error')
                    and type(event.get('elapsed_ms')) is int
                    and type(event.get('completed')) is int
                    and 0 <= event['completed'] <= count):
                _LOG.warning('flexline_ocr worker_stage=%s status=%s regions=%d completed=%d elapsed_ms=%d',
                             event['stage'], event['status'], count, event['completed'], event['elapsed_ms'])
        except (ValueError, AttributeError):
            pass


class FlexlineValidationError(ValueError):
    """A scan must not fall through to generic financial parsing."""


def fail():
    raise FlexlineValidationError(
        'VALIDATION FAILED / MANUAL REVIEW — image-only Flexline source evidence '
        'could not be verified; no rows imported.')


def normalized(text):
    return ' '.join(text.upper().split())


def validate(regions, account, owner):
    """Validate independent labelled readings before returning financial evidence."""
    r = {key: normalized(value) for key, value in regions.items()}
    if (r.get('issuer') != 'FIFTH THIRD' or r.get('owner') != owner
            or not re.fullmatch(r'FIFTH THIRD EQUITY FLEXLINE(?:SM)?', r.get('product', ''))):
        fail()

    def match(pattern, text):
        found = re.fullmatch(pattern, text)
        if not found:
            fail()
        return found.groups()

    def amount(value):
        return Decimal(value.replace(',', ''))

    long_date = r'([A-Z]+ \d{1,2}, \d{4})'
    when, number, due, minimum, new, maturity = match(
        r'LINE OF CREDIT STATEMENT STATEMENT DATE ' + long_date
        + r' ACCOUNT NUMBER (\d{11}) PAYMENT DUE DATE ' + long_date
        + rf' MINIMUM PAYMENT DUE ({MONEY}) NEW BALANCE ({MONEY}) MATURITY DATE '
        + long_date, r.get('identity', ''))
    try:
        when, due, maturity = (datetime.strptime(value, '%B %d, %Y').date()
                               for value in (when, due, maturity))
        short_when, short_due, credit, available, middle_min = match(
            r'STATEMENT DATE PAYMENT DATE CREDIT LINE AVAILABLE CREDIT MINIMUM PAYMENT DUE '
            + rf'(\d{{2}}/\d{{2}}/\d{{2}}) (\d{{2}}/\d{{2}}/\d{{2}}) ({MONEY}) ({MONEY}) ({MONEY})',
            r.get('dates', ''))
        if (datetime.strptime(short_when, '%m/%d/%y').date() != when
                or datetime.strptime(short_due, '%m/%d/%y').date() != due
                or not when < due <= maturity):
            fail()
    except ValueError:
        fail()
    if number != account:
        fail()
    labels = ('ACCOUNT', 'PREVIOUS BALANCE', 'ADVANCES AND DEBITS',
              'FINANCE CHARGE', 'PAYMENTS AND CREDITS', 'NEW BALANCE', 'MINIMUM PAYMENT DUE')
    cells = [match(re.escape(label) + (r' (\d{11})' if i == 0 else rf' ({MONEY})'),
                   r.get(f'cell{i}', ''))[0] for i, label in enumerate(labels)]
    if cells[0] != number:
        fail()
    summary = match(
        r'ACCOUNT SUMMARY PREVIOUS ADVANCES PAYMENTS NEW MINIMUM '
        r'ACCOUNT BALANCE AND DEBITS FINANCE CHARGE AND CREDITS BALANCE PAYMENT DUE '
        r'LINE OF CREDIT ' + ' '.join([rf'({MONEY})'] * 6), r.get('summary', ''))
    values = tuple(map(amount, summary))
    if values != tuple(map(amount, cells[1:])):
        fail()
    previous, advances, finance, payments, closing, minimum_summary = values
    if (closing != amount(new) or minimum_summary != amount(minimum)
            or minimum_summary != amount(middle_min)
            or previous + advances + finance - payments != closing):
        fail()
    interest = match(
        r'INTEREST RATE SUMMARY ANNUAL PERCENTAGE DAILY AVERAGE DAYS FINANCE '
        r'ACCOUNT RATE PERIODIC RATE DAILY BALANCE INCYCLE CHARGE LINE OF CREDIT '
        + rf'(\d+\.\d+) (\d+\.\d+) ({MONEY}) (\d+) ({MONEY})', r.get('interest', ''))
    if amount(interest[4]) != finance or not 1 <= int(interest[3]) <= 366:
        fail()
    history_prefix = ('HISTORY SUMMARY DATE DESCRIPTION OF TRANSACTIONS, ADVANCES, '
                      'PAYMENTS, AND CREDITS AMOUNT ')
    history = r.get('history', '')
    if not history.startswith(history_prefix):
        fail()
    body = history[len(history_prefix):]
    row = rf'(\d{{2}}/\d{{2}}/\d{{2}}) PAYMENT ({MONEY}) CR'
    if not re.fullmatch(row + '(?: ' + row + ')*', body):
        fail()
    history_rows = re.findall(row, body)
    try:
        if any(datetime.strptime(date, '%m/%d/%y').date() > when for date, _ in history_rows):
            fail()
    except ValueError:
        fail()
    if sum((amount(value) for _, value in history_rows), Decimal(0)) != payments:
        fail()
    return dict(account_number=number, period_end=when.isoformat(),
                closing_balance=-abs(closing), previous=previous, advances=advances,
                finance=finance, payments=payments, new=closing)


def _extract(content, account, owner):
    import pdfplumber
    from PIL import ImageOps
    from fifth_third_scanned import LOCK
    with pdfplumber.open(BytesIO(content)) as document:
        if not 1 <= len(document.pages) <= 2:
            return None
        page = document.pages[0]
        # Some non-text PDFs draw glyph outlines rather than a full-page bitmap.
        # Both need rendering; neither is financial identity until labels agree.
        if (page.extract_text() or '').strip() or not (page.images or page.curves):
            return None
        if page.width * page.height * (300 / 72)**2 > 12_000_000:
            fail()
        if not LOCK.acquire(blocking=False):
            diagnostic('lock', 'busy', time.monotonic())
            fail()
        started = time.monotonic()
        stage = 'render'
        try:
            image = page.to_image(resolution=300).original
            diagnostic(stage, 'ok', started)
            with tempfile.TemporaryDirectory(prefix='areti-flexline-regions-') as folder:
                stage = 'crop'
                keys = list(REGIONS)
                if not 1 <= len(keys) <= 16:
                    fail()
                files = []
                for key in keys:
                    box = tuple(round(value * (image.width if i % 2 == 0 else image.height))
                                for i, value in enumerate(REGIONS[key]))
                    crop = ImageOps.expand(ImageOps.grayscale(image.crop(box)), border=20, fill=255)
                    target = Path(folder) / (key + '.png')
                    crop.save(target)
                    files.append(str(target))
                diagnostic(stage, 'ok', started, len(keys))
                stage = 'subprocess'
                worker_started = time.monotonic()
                env = {key: os.environ[key] for key in ('PATH', 'SystemRoot', 'WINDIR', 'TEMP', 'TMP') if key in os.environ}
                result = subprocess.run(['node', str(Path(__file__).parent / 'fifth_ocr/read.cjs'), '--regions', *files],
                                        capture_output=True, text=True, encoding='utf-8', timeout=OCR_TIMEOUT, env=env)
                diagnostic(stage, 'nonzero' if result.returncode else 'ok', worker_started, len(keys))
                worker_diagnostics(result.stderr, len(keys))
                if result.returncode:
                    fail()
                stage = 'json'
                data = json.loads(result.stdout)
                stage = 'model_identity'
                if (data.get('engine') != '7.0.0'
                        or data.get('modelHash') != '5dc5d8d640a212c9d6184921ba103b186f50e0fed9ee716c53e6b312b400d747'
                        or not isinstance(data.get('pages'), list)
                        or len(data['pages']) != len(keys)
                        or not all(isinstance(value, str) for value in data['pages'])):
                    fail()
                evidence = dict(zip(keys, data['pages']))
                stage = 'source_identity'
                product, identity = normalized(evidence['product']), normalized(evidence['identity'])
                probable = ('FLEXLINE' in product or 'LINE OF CREDIT' in identity
                            or (normalized(evidence['owner']) == owner and account in identity))
                if not probable:
                    return None
                # Establish identity before interpreting any financial regions.
                if (normalized(evidence['issuer']) != 'FIFTH THIRD'
                        or normalized(evidence['owner']) != owner
                        or not re.fullmatch(r'FIFTH THIRD EQUITY FLEXLINE(?:SM)?', product)
                        or 'LINE OF CREDIT STATEMENT' not in identity
                        or f'ACCOUNT NUMBER {account} ' not in identity + ' '):
                    fail()
                stage = 'source_validation'
                validated = validate(evidence, account, owner)
                diagnostic('complete', 'ok', started, len(keys))
                return validated
        except subprocess.TimeoutExpired as error:
            worker_diagnostics(error.stderr, len(REGIONS))
            diagnostic(stage, 'timeout', started, len(REGIONS))
            raise
        except Exception as error:
            diagnostic(stage, type(error).__name__, started, len(REGIONS))
            raise
        finally:
            LOCK.release()


def evidence(content, account, owner):
    """Cache only validated metadata / rejection, never PDF bytes or raw OCR."""
    key = (sha256(content).hexdigest(), account, owner)
    with _CACHE_LOCK:
        if key in _CACHE:
            value = _CACHE[key]
            _CACHE.move_to_end(key)
            if value is False:
                fail()
            return dict(value) if value is not None else None
    try:
        if len(content) > 30_000_000:
            fail()
        value = _extract(content, account, owner)
    except Exception as error:
        diagnostic('evidence', type(error).__name__, time.monotonic())
        value = False
    with _CACHE_LOCK:
        _CACHE[key] = value
        while len(_CACHE) > 8:
            _CACHE.popitem(last=False)
    if value is False:
        fail()
    return dict(value) if value is not None else None
