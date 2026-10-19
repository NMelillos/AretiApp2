# Areti Income category editing — 19 October 2026

Classification: **REGRESSION**. The existing editor previously allowed a valid category/subcategory change across derived Reporting Groups. A later preservation guard rejected that intentional edit. The newly confirmed business rule is already implemented in the live membership filter; no new membership requirement or financial rule is introduced.

## Verified baseline and boundaries

Exact Render service srv-d7aj84khg0os73b4g0i0 in confirmed workspace tea-d6mhq3p4tr6s738eegm0 reports live deployment dep-db9m2m3ncjis739rjgtg at 127599fec5c4ecc9028ab155f9abfcd0a0a5ad25. The scoped worktree was clean before editing. New work is isolated on codex/areti-income-category-unlock. Existing branches, worktrees and all earlier candidates remain unchanged. Immediate code recovery target is that verified pre-release live commit, 127599fec5c4ecc9028ab155f9abfcd0a0a5ad25; this is not a claim that a rollback was executed or rehearsed here.

## Git sequence (Cyprus offsets retained)

| Commit | Date | Evidence |
|---|---|---|
| 32024814feb1f56ff68a7014bbdb30d6be8ba3e8 | 2026-09-12 09:56:03 +03:00 | “Enable Income and Charity editing in Areti report”: introduces the classification-only editor/save flow; no old-group lock. |
| 9a12e21d73e9e414c561aea74cf176de5e2be6b3 | 2026-09-12 13:34:49 +03:00 | “Preserve Income reporting groups across views and edits”: adds the exact rejection in app.py `_save_income_charity_edits`; reporting.py `third_hierarchy_item19_exclusions` separately preserves income rows in their existing hierarchy groups. `_qa_income_groups.py` explicitly expects cross-group rejection. |
| c8a2c54d9488a3068ce7ee138adf8ba282c4ecf6 | 2026-09-14 07:36:47 +03:00 | “Include Reporting Group in Income membership”: extends `is_income`/`income_charity_scope` from Category/Subcategory to the three-field OR rule; also broadens the save lock to rows qualifying through Reporting Group. |
| 300a07eaeb8e1fdfa29aab48cc640f0c8663344c | 2026-09-18 19:06:34 +03:00 | “Fix lossless Income classification amount snapshots”: keeps the save on `income_edit=True`; exact financial comparisons and classification-only persistence remain unchanged by this package. |

Evidence commands: `git blame` around the save helper; `git log -S 'Income edits must preserve Reporting Group' -- app.py`; full `git show` patches, commit metadata and introducing QA assertions. Private forensic files contain the pinned live and pre-lock Git source and introducing diff. The original purpose evidenced by that commit is Income group preservation across report views and edits. It was introduced directly in the Income/Charity editor, not in SPLIT, Pending Review or Database editing. No evidence supports a SPLIT origin or later accidental propagation from another workflow. The defect is treating passive preservation as a hard constraint on an intentional new classification. No independent historic business approval for that restriction is inferred from the commit message.

## Exact live path before the fix

Areti report Income/Charity section → `_render_income_charity_editor` → fixed-row editor with only Category/Subcategory selectable → Save → `_save_income_charity_edits` → `save_reviewed_rows(..., income_edit=True)` → guarded transaction update, audit/memory, post-update verification, atomic commit. Reporting Group is read-only display data derived by `_assign_report_groups` from Setup, not a separately persisted transaction group column.

The UI offers valid current Setup pairs across groups. The old helper then compares the original displayed group, current original-pair mapping and target-pair mapping; any difference raises “Income edits must preserve Reporting Group” before persistence. This blocks a valid new classification and can also block editing a legacy row whose original mapping has changed.

Pinned execution reproduces the live rejection with zero writes. Executing the same source-bound edit using the helper from the introducing commit's parent successfully saves and derives GROUP-B instead of GROUP-A. This proves prior support rather than assuming a new requirement.

## Minimal correction

Only `_save_income_charity_edits` changes in application code. Remove the original-group equality lock. Check the selected current Setup mapping for ambiguity, using the same normalized subcategory key and exact-pair then category fallback as existing report assignment. Missing mapping metadata rejects clearly before submitting writes; conflicting target groups reject the whole batch. An explicitly blank Setup group retains the existing “Unassigned reporting group” convention. The target group follows the valid new classification on report reload; no account, taxonomy or group record is rewritten and no force-to-INCOME behavior exists.

The existing valid-pair check, stable IDs, review-state validation, expected prior classification/review status, atomic backend, exact financial compare-and-swap, rollback, SPLIT amount guards and source/duplicate identities remain unchanged. No editable amount/date/description/account fields are forwarded to persistence. Existing classification-related audit, categorisation memory and reviewed-at behavior use the unchanged backend. INCOME remains case-insensitive whole-word **Category OR Subcategory OR Reporting Group contains “income”**, without duplicate membership or reassignment.

## Executed validation

27/27 focused unittest methods pass, with zero failures/skips. Tests cover historical reproduction, same/different-group saves, pending/reviewed preservation, membership through each field/case/negative controls, invalid/unresolved/ambiguous mappings, category fallback, explicit unassigned mapping, forced second-row failure with complete audit/memory rollback, deleted/stale transactions, changed review state, stale Setup pair rejection, financial-field injection protection, SPLIT classification and amount guard, actual editor Save/Cancel flow and repeat no-op. Snapshot checks preserve all nonclassification transaction columns (allowing the existing reviewed-at stamp), source import/balance/account/rate/Setup metadata, row identities and allocation structures.

The existing `_qa_income_save_backend.exercise(db, 'SQLite')` passes separately, covering actual renderer Save/reruns/cancel, stable IDs, batch rollback, Decimal totals/ratio, transport representations, null subcategory and a simulated financial race between read and update. The new source-scope test proves every application AST node outside the single helper and every other application module unchanged against the verified live tree, including reporting/THIRD, Latest Import Balances, db.py, SPLIT, all bank parsers and safeguards.

Only disposable local SQLite is used, in a credential-free child with external networking, credential-file reads, nested subprocesses and outside-root SQLite denied. Isolation safety, changed-source syntax and diff checks pass. No PostgreSQL runtime execution, production SQL, production classification edit, import, repair, migration or schema/environment change is claimed. Old archival QA expecting cross-group rejection remains evidence of the superseded restriction; it is not weakened or presented as a passing requirement of this corrected package.

## Release and UAT boundary

User authorizes exact tested code deployment if regression is proven and checks pass, maintaining Auto-Deploy state. Reverify exact allowed service/main/live SHA before pushing and deploying the exact final commit. Verify root/health and bounded startup logs. No real financial row is edited for smoke testing. Production Save success remains **READY FOR ARETI UAT** unless an authorised safe test record is actually available; local simulated writes are not represented as live business acceptance.
