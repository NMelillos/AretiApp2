# Interim reduced release: conditional authority and gate evidence

2026-10-05. The user approved `7ea6920` as an **INTERIM, REDUCED UI/reporting release only**, and conditionally authorized push, merge and deployment after all existing release gates are verified. This approval is recorded and must not be requested again. A partial report of current Setup accounts is accepted; complete reconciled all-account reporting remains OPEN.

Deployment remains HELD. No push, merge, deployment, historical production repair, schema change, reimport, taxonomy remapping or safeguard removal has occurred. Full candidate and handover `eb46d0ee2c263ab3d3e3108a398d3ecac1c39a8e` remain clean and preserved.

| Gate | Verified evidence | Status |
| --- | --- | --- |
| Actual hosting service, live SHA and access before push/merge | Fresh Render `list_services(workspaceId=tea-d8nphahkh4rs73fesktg, includePreviews=true)` returned `null`; local Render CLI unavailable. Workspace read access was already confirmed by the user. Exact service/access question submitted, with no guessed target | BLOCKED: actual service, live SHA and deploy/log access unverified |
| Complete proposed release against verified live version, including inheritance | Fresh remote-main SHA remains f3ea9ce. Reduced branch is based on f3ea9ce with three application files changed; all full-candidate parser/reconciliation/history-coverage modules excluded. Other baseline financial modules, Import body, permissions, NOMAD and SPLIT dependencies are preserved | BLOCKED: remote main and historical successful f3ea9ce receipt do not establish current live code; complete inherited diff against live cannot be verified |
| Required final release regression | First full reduced-candidate script inventory executed through the isolated runner; failures retained, including historical app fingerprints and old report expectations. Initial local PostgreSQL port error corrected; only its failed checks are eligible for targeted rerun | NOT PASSED: see INTERIM_QA_RESULTS.md for exact receipts and counts; no claim that the original 89/89 applies |
| Authenticated non-production workflows | Local Streamlit AppTest with synthetic isolated SQLite, local test credentials, Setup/Pending/History/report/Corrections and protected financial population checks | Local technical evidence only; no deployed staging or Areti workflow acceptance inferred |
| Pending query performance | Synthetic 5,141 stored rows / 5,138 visible rows, preserving existing hidden IDs. Five measured reads: median 0.083s, maximum 0.126s; authenticated Pending rendering 2.280s, one fresh pending read shared by header/page, full database no-write assertion | Local SQLite benchmark PASS; hosted PostgreSQL/network/authenticated environment performance remains unverified |
| Practical rollback and inherited startup behavior | Historical deploy receipt retained; no accessible current hosting service. Existing startup financial modules are unchanged and local synthetic no-write checks pass | BLOCKED: rollback route/current preceding code version and live migration/backfill compatibility cannot be confirmed |
| Any necessary work pause | No external release action initiated; no pause assumed | Pending only if deployment requires a pause; obtain confirmation then |
| Post-deployment exact SHA, authenticated read-only views/logs | No deployment attempted | NOT RUN |

## Report labeling follow-up within the approved interim scope

The approved candidate could still label its total as all accounts when no warnings existed. A local follow-up makes the report consistently say **INTERIM PARTIAL REPORT: current Setup accounts only**, and explicitly says that accounts absent from Setup are outside the report and are not enumerated. The UI, printable HTML/PDF and workbook disclose scope and the partial eligible-deposit total, with per-account exclusions/reasons and visible stored balances. This does not expand balance eligibility, repair uncertain values or close full reporting requirements.

Technical warning types must be distinguished from exclusions: timestamp-order uncertainty is a verification warning and does not alone exclude an otherwise eligible balance. Card/liability sign convention, ambiguous ownership/rates, incomplete metadata, unavailable FX and unverified precision remain disclosed exclusions. Source reconciliation is not asserted. All financial calculation, parsing, persistence, duplicate and import-restriction behavior remains unchanged by this labeling follow-up.

## Exact next steps and operational limits

1. Restore verified access to the actual hosting service using the pending service/dashboard/workspace question. Do not guess a service, bypass access, or push to discover an auto-deploy target.
2. Review the complete release diff against its verified live SHA and the inherited baseline startup behavior. Confirm practical rollback and whether a work pause is necessary.
3. Resolve and review reduced-build regression compatibility without weakening frozen-source/tamper checks or substituting old behavior for runtime tests. Old report assertions must reflect approved visible uncertainty and both export formats; unreviewed hashes remain rejected. Release regression is a real outstanding gate, not a waived warning.
4. Complete the required final release verification and appropriate non-production/Areti acceptance. Conditional authorization then applies; no repeated interim-scope approval question is needed.
5. Only after all gates pass, perform the established release, verify exact live SHA and authenticated read-only Setup/History/pending/report, and inspect logs. Never perform financial imports or repair as a production smoke test.

Until then, Areti has no newly deployed interim UI/report. Existing deployed behavior has not been changed by this work; established read-only workflows remain the relevant operational boundary. Historical CNB/SPLIT and source-bound corrections remain held, along with unapproved repair, reimport and taxonomy work. This candidate does not fix original 429 cause, BOC/Citi parsing, statement-import outcomes, historical data, CNB/SPLIT discrepancies or the full master. Current live import restrictions remain unverified; the reduced branch adds none, and the full candidate's new unknown-historical-coverage restriction stays separate and unapproved.
