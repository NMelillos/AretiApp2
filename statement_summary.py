"""Source-only statement fields. No transaction parser or persistence dependency."""
from dataclasses import dataclass, replace
from datetime import datetime
from io import BytesIO
import re

from financial_decimal import decimal_value


@dataclass(frozen=True)
class Evidence:
    value: str
    page: int
    anchor: str


@dataclass(frozen=True)
class Field:
    value: str | None = None
    currency: str | None = None
    evidence: tuple[Evidence, ...] = ()
    note: str = "Explicit source label not found; NOT VERIFIED."

    @property
    def verified(self):
        return self.value is not None


@dataclass(frozen=True)
class Summary:
    bank: str
    account: str
    currency: Field
    period_start: Field
    period_end: Field
    opening_balance: Field
    closing_balance: Field


_NUMBER = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d+"
_MONEY = rf"(?:\({_NUMBER}\)|{_NUMBER})(?:\s+(?:CR|DR))?"


def _amount(raw):
    text = raw.strip()
    suffix = re.search(r"\s+(CR|DR)$", text, re.I)
    if suffix:
        text = text[:suffix.start()]
    negative = text.startswith('(') and text.endswith(')')
    if negative:
        text = text[1:-1]
    if (negative and text.startswith(('-', '+'))) or (suffix and (negative or text.startswith(('-', '+')))):
        raise ValueError('Conflicting source sign conventions')
    value = decimal_value(text.replace(',', ''))
    # copy_negate does not round even beyond the current Decimal context.
    if negative or (suffix and suffix[1].upper() == 'DR'):
        value = value.copy_negate()
    return format(value, 'f')


def _resolve(candidates, note="Explicit source anchor; no transaction-derived inference."):
    evidence = tuple(candidates)
    if not evidence:
        return Field()
    if len({item.value for item in evidence}) != 1:
        return Field(evidence=evidence, note="Conflicting source candidates; NOT VERIFIED.")
    return Field(value=evidence[0].value, evidence=evidence, note=note)


def _boc(pages):
    candidates = {key: [] for key in ('currency', 'period_start', 'period_end', 'opening_balance', 'closing_balance')}
    accounts, header_balances = set(), []
    invalid = set()
    for page, text in enumerate(pages, 1):
        accounts.update(re.findall(r'Account\s*Number\s*:?\s*(\d{4,})', text, re.I))
        for original in text.splitlines():
            line = re.sub(r'\s+', ' ', original).strip()
            match = re.fullmatch(r'Currency\s+([A-Z]{3})', line)
            if match:
                candidates['currency'].append(Evidence(match[1], page, line))
            period = re.fullmatch(r'Statement\s*Period\s*:\s*(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})', line, re.I)
            if period:
                for key, raw in zip(('period_start', 'period_end'), period.groups()):
                    try:
                        value = datetime.strptime(raw, '%d/%m/%Y').date().isoformat()
                        candidates[key].append(Evidence(value, page, line))
                    except ValueError:
                        invalid.add(key)
            opening = re.fullmatch(rf'Balance\s*brought\s*forward\s+({_MONEY})', line, re.I)
            closing = re.fullmatch(rf'Total\s*/\s*Balance\s*Carried\s*Forward\s+({_MONEY})\s+({_MONEY})\s+({_MONEY})', line, re.I)
            header = re.fullmatch(rf'(?:Statement of Account\s+(?:Sight Account\s+)?)?Balance\s+({_MONEY})', line, re.I)
            for key, raw in [('opening_balance', opening[1] if opening else None),
                             ('closing_balance', closing[3] if closing else None)]:
                if raw is not None:
                    try:
                        candidates[key].append(Evidence(_amount(raw), page, line))
                    except ValueError:
                        invalid.add(key)
            if header:
                try:
                    header_balances.append(Evidence(_amount(header[1]), page, line))
                except ValueError:
                    invalid.add('closing_balance')
    fields = {key: _resolve(value) for key, value in candidates.items()}
    for key in invalid:
        fields[key] = replace(fields[key], value=None, note='Invalid source date or sign; NOT VERIFIED.')
    if len(accounts) > 1:
        fields = {key: replace(value, value=None, note='Multiple account identities; section association NOT VERIFIED.') for key, value in fields.items()}
    start, end = fields['period_start'], fields['period_end']
    if start.verified and end.verified and start.value > end.value:
        for key in ('period_start', 'period_end'):
            fields[key] = replace(fields[key], value=None, note='Source period is reversed; NOT VERIFIED.')
    close = fields['closing_balance']
    if close.verified and any(decimal_value(e.value) != decimal_value(close.value) for e in header_balances):
        fields['closing_balance'] = replace(close, value=None, evidence=close.evidence + tuple(header_balances), note='Header and carried-forward closing balances conflict; NOT VERIFIED.')
    for key in ('opening_balance', 'closing_balance'):
        value = fields[key]
        if value.verified and not fields['currency'].verified:
            fields[key] = replace(value, value=None, note='Source currency unavailable or ambiguous; NOT VERIFIED.')
        elif value.verified:
            fields[key] = replace(value, currency=fields['currency'].value)
    account = '****' + next(iter(accounts))[-4:] if len(accounts) == 1 else 'NOT VERIFIED'
    return Summary('Bank of Cyprus', account, **fields)


def extract_pages(pages):
    """Bank adapters consume page text only, never transaction results."""
    pages = tuple(pages)
    compact = re.sub(r'\s+', '', '\n'.join(pages)).upper()
    if 'BANKOFCYPRUSPUBLICCOMPANY' in compact or 'BCYPCY2N' in compact:
        return _boc(pages)
    unknown = Field(note='Bank/layout unsupported or unreadable; NOT VERIFIED. No OCR or financial inference performed.')
    return Summary('NOT VERIFIED', 'NOT VERIFIED', unknown, unknown, unknown, unknown, unknown)


def extract_pdf(content):
    """Read PDF bytes in memory, with bounded file/page sizes; no writes."""
    if len(content) > 20 * 1024 * 1024:
        raise ValueError('PDF exceeds the 20 MiB local summary limit.')
    if not content.startswith(b'%PDF-'):
        raise ValueError('A readable PDF is required.')
    import pdfplumber
    with pdfplumber.open(BytesIO(content)) as pdf:
        if len(pdf.pages) > 100:
            raise ValueError('PDF exceeds the 100-page local summary limit.')
        return extract_pages(page.extract_text() or '' for page in pdf.pages)
