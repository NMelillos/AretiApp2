# Post-UAT bounded package

Baseline bb0f77d7c04f951a5e9dd6fe1b09e10e073525b1; local-only codex/areti-balance-cnb-report. No push/deploy or performance work authorized/executed in this package. Obtain exact candidate SHA from Git HEAD. All earlier worktrees remain preserved.

## Outcomes and limits

True empty BOC statements already committed their balances. The missing path was a new source with only overlapping stored transactions: the old commit rolled back its header before recording source metadata. The new path preserves duplicate protection, proves every source movement against a distinct exact existing row and records references in the balance notes within the same transaction. It inserts no transactions. It never modifies an existing import. References are revalidated read-only; changed/missing references cannot make an overlap record verified. Renamed/new-byte sources remain bounded by the unchanged duplicate backend and exact source validation.

CNB already extracted and reconciled its explicit Account Summary, but returned only transactions/identity. Normal Upload's fallback metadata extractor was incomplete. Metadata now travels with the parsed frame and through the original atomic commit. Existing CNB Setup mapping is authoritative; commit checks source rows/identity and reconciliation. Transaction extraction unchanged.

Latest Import Balances retains current Setup scope and uncertainty/exclusion labels. Newly verified older BOC/CNB statements may appear in history but cannot replace a newer complete balance in the report. The compact THIRD view uses that same selection. Native-currency partial subtotals exclude incomplete, ambiguous, unverified-storage/precision and liability rows. Source reconciliation remains explicitly unverified at report level; no claim of a full reconciled all-account total. No invented FX, schema or historical import changes.

Safra review and relevant regression evidence show no new functional defect requiring changes. Six-section and empty-section behavior, full identity handling and Pending/history remain intact. Historical issues and separately held full candidate are not resolved by this package. Statement Summary stays read-only and BOC-only.

## Executed evidence

47 unique unittest case IDs PASS; zero remaining FAIL. Existing `_qa_latest_import_balances.py` standalone SQLite selection/precision/FX/export/UI checks PASS. Actual new THIRD route/backfill writer exclusion, per-currency totals, unauthenticated denial, safe failures and protected-page no-mutation checks executed. Source CNB's two pages visually reviewed. Original BOC source comparisons/integration and both Comerica imports executed on this candidate. Tests use allowlisted credential-free children with external network/credential-file access blocked and disposable-root SQLite only. Private logs/source expectations are outside Git in the chat visualizations evidence root; failed runs retained and corrected narrowly. No old 56/86/89 suite result reused for new code.

## Historical metadata repair proposal — NOT EXECUTABLE AUTHORITY

Enforced-read-only aggregate audit of the owner-confirmed dedicated Areti project found one affected historical CNB import with missing period/credits/debits/closing fields. Opening/count match the available exact source. The specified BOC source also has one existing import with incomplete period/financial metadata and consistent transaction count. Both originals are available privately. No row was changed. Release does not automatically repair these existing imports; exact-file duplicate rejection remains correct.

For a separately approved repair: bind each operation to the exact existing source SHA, import ID and one verified account/currency. Reparse the original with the validated adapter; compare every stored source row/date/amount and identities, classification/SPLIT state and existing metadata. Refuse ambiguity/conflicts; do not guess missing values or touch transactions. Prepare exact field-level before/after evidence and scoped approval, including any existing nonempty conflicting value. Under the existing financial control/Areti authorization fences, update only the approved metadata of the existing balance record in one audited transaction; preserve original import timestamp/count/hash, classifications, balances outside that source and all history. No new transaction import, header deletion or timestamp promotion. Verify counts/transaction fingerprints unchanged and report ordering/exclusions correct; maintain a scoped metadata rollback snapshot. No automatic repair helper is added to this candidate.

## Release boundary

READY for controlled release review of the new paths after existing release gates/approval. No automatic deployment. Code release requires no production repair or schema migration. Making the already-imported historical sources complete additionally requires the separate approvals described above. Previous recovery target: f3ea9ce2d0143ef3239871bb94791ffb39fd857e. Auto-Deploy must remain OFF; verify hosting state again only when release is authorized.
