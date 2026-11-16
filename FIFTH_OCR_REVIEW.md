# Scanned Fifth Third fallback

The existing text-native Fifth Third parser runs first and is unchanged. The
fallback is limited to structurally identified Fifth Third statements with
full-page raster images, at most two pages: activity followed by disclosures.
Failed native validation also triggers this fallback when those image conditions
hold. Other banks are not routed through it.

## Reproducible local OCR

- Tesseract.js 7.0.0 and tesseract.js-core 7.0.0 (lockfile integrity pinned).
- `@tesseract.js-data/eng` 1.0.0, `4.0.0_best_int`, English LSTM (OEM 1).
- Uncompressed model SHA256:
  `5dc5d8d640a212c9d6184921ba103b186f50e0fed9ee716c53e6b312b400d747`.
- Node.js required; local proof used 24.21.0. No native Tesseract executable.
- Existing pdfplumber/PDFium/Pillow renderer, 300 dpi, no preprocessing, no
  decimal insertion or character substitution. Local proof versions:
  pdfplumber 0.11.9, pypdfium2 5.9.0, Pillow 12.2.0.
- Language assets load only from installed packages. No runtime model download,
  external OCR request, or persistent OCR cache.

## Proposed Render build command

Keep the existing startup command and Auto-Deploy OFF. The build must install
the lockfile and verify the engine/model before recording deployment identity:

```sh
pip install -r requirements.txt && npm ci --prefix fifth_ocr --omit=dev --ignore-scripts --no-audit --no-fund && node fifth_ocr/read.cjs --verify && python deployment_identity.py
```

This is a proposed configuration change, not evidence that it has been applied.
Render's native runtime documents Node/npm availability. Docker is not required
for the production design. The production-equivalent build gate remains separate
from successful development-machine OCR tests.

## Financial and lifecycle gates

Account/header agreement, period, page sequence, numeric tokens, section counts,
section totals, summary reconciliation, every daily balance and final balance
must validate using Decimal. Ambiguous or unsupported output raises
`FifthThirdParseError` with `VALIDATION FAILED / MANUAL REVIEW`; it cannot fall
through to the generic OCR row scanner. No inferred values or synthetic rows.

The returned metadata uses the existing Fifth Third preview validator and atomic
commit path. Preview does not import, duplicates remain blocked, and failed
persistence rolls back and remains retryable. No database, Setup, report, bank
identity, or historical-record changes are included.

## Resource bounds

Only one OCR request per process runs at a time; competing requests fail closed
and remain retryable. Rendering is limited to 12 million pixels per page and two
pages. The OCR subprocess has a 90-second timeout and a minimal environment without
database credentials. Its worker exits after each request; temporary page images
are removed. There is no OCR startup cost for text-native imports. First scanned
preview pays rendering and worker initialization; preview may invoke the separate
balance reader too. Local one-page worker measured about 172 MiB peak RSS and
4.2 seconds; these are not production latency or combined application-memory claims.

Original customer PDFs and generated OCR results remain private QA evidence,
outside Git. `_qa_fifth_scanned.py` binds them through `ARETI_FIFTH_SOURCE_ROOT`.

## Verification completed, 16 November 2026

- 8 OCR-specific/lifecycle checks and the 5 existing Fifth Third checks pass.
- 44 shared-bank checks pass (CNB, Safra, Revolut, BOC, Citi).
- 210 Python files pass syntax validation. Protected-source comparison confirms
  only the two Fifth Third dispatch points changed in existing application code.
- A fresh Linux virtual environment installed the unchanged `requirements.txt`
  plus the npm lockfile, verified model/engine initialization, and parsed the exact
  original PDF through the application: all nine amounts and eight daily balances
  matched. No development-machine OCR package was used.
- Linux rehearsal: Python 3.14.4, Node 24.21.0, Streamlit 1.61.1,
  pdfplumber 0.11.10, pypdfium2 5.15.2, Pillow 12.3.0. This is an isolated
  Linux rehearsal, not a claim that production runtime smoke verification occurred.

Deployment is held until the existing service's build command installs/verifies
these OCR dependencies. The connected Render tools cannot update that command,
and dashboard automation failed to initialize. No production configuration,
deployment, or financial-data change was performed.
