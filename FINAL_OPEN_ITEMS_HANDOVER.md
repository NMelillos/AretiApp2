# Final open items checkpoint — 2026-10-07

Baseline 34b92af1a883e1af991322d665ac6144971a0359 is deployed LIVE (HTTP 200, Auto-Deploy OFF); authenticated new-release smoke and business UAT remain pending. This separate codex/areti-final-open-items worktree preserves all earlier candidates.

IMPLEMENTED: Latest Import Balances now exposes the selected statement's stored credits/debits in the page dataset, print HTML and Excel. Unknown values remain unknown. Selection, identities, balances, totals, precision, exclusions, compact THIRD, financial/parser/import logic and authentication are unchanged.

TESTED: six focused unittest cases PASS, zero failures; existing report/export standalone regression PASS; isolation safety PASS. Covered exact synthetic stored flows, unknown fields, export consistency/no mutation, older-statement ordering, overlap/history/duplicate protection, compact currency totals and actual THIRD navigation/no backfill. The 47 baseline cases are prior evidence, not rerun proof. Syntax/diff checks PASS. Children have allowlisted environments, blocked external network/credential files and disposable-only SQLite.

Line of Credit: original three scanned pages and both email screenshots visually reviewed. Source annotations resolve the requested statement-balance basis and email requests no transaction insertion. Exact source has no text layer; isolated runtime lacks Tesseract and production OCR capability is unverified. Dedicated adapter/end-to-end exact-source import is BLOCKED; no unverified adapter or weakened empty-statement safeguard supplied. Private source facts and minimum unblock are in FINAL_OPEN_ITEMS_PRIVATE_PROPOSALS.md in the private evidence root.

Read-only historical proposals: exact BOC/CNB balance/import IDs and current metadata identified; local original hashes match stored hashes. Metadata-only before/after proposals, reconciliation and required row-level/safeguard approval checks recorded privately. No production repair or migration. No new functional Safra defect proven. Performance deferred while Line of Credit is blocked.

DEPLOYED: NO for this new report correction. DATA-REPAIRED: NONE. USER-ACCEPTED: prior confirmed paths preserved; new report and balance-only UAT pending. No push/deployment, financial writes, historical reimport, taxonomy change or hosting configuration action. Recovery remains f3ea9ce2d0143ef3239871bb94791ffb39fd857e. No repeated unchanged full QA/profiling.
