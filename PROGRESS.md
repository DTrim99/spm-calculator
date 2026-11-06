# CE in-kind benefit imputation — November 5, 2026

## State

Draft PR [#55](https://github.com/PolicyEngine/spm-calculator/pull/55) on
`max/ce-inkind-imputation-20261105`. The branch holds the stage,
imputation, tests, receipt and docs page. The first independent review
asked for five fixes, now made; a second review and merge follow.

## Done

- `ce_inkind_stage.py`: an optional stage that adds in-kind amounts to the
  CE expenditure frame through `ce_threshold`'s public functions. The
  default replication, its September 8 receipt and the canonical forecast's
  code identity stay unchanged.
- `ce_inkind.py` and `cps_asec.py`: BLS-style imputation of school lunch,
  WIC, LIHEAP, broadband and rental assistance from CPS ASEC household
  donors and CE rent records, with five implicates.
- `ce_decomposition.py`: an exact price × real out-of-pocket × in-kind
  growth decomposition under the package CPI and BLS's FCSUti index.
- Receipt `spm_calculator/data/current/ce_inkind_replication_2019_2025.json`
  and docs page `docs/ce-inkind-imputation.md`.
- Review fixes: rent paid now comes from the rent file's rent-only
  records (UCC 210110) rather than the FMLI rented-dwelling summary;
  predictive mean matching pools hold distinct donors at the ends of the
  donor range; SNAP receipt counts CE's completed amounts and months of
  receipt; WIC counts an infant's mother only when the unit has a woman
  15 or older; rental assistance returns zero for owner-only samples.
- Result: the stage lifts thresholds 1.1–3.8% and overshoots BLS's
  corrected levels for every tenure in every year from 2019 to 2025. It
  changes 2022–2025 growth by −0.6 to +0.9 points; real out-of-pocket
  spending carries growth beyond prices.
- Price-index sensitivity: deflating with BLS's published FCSUti index
  instead of the package composite adds 0.9–2.0 points of 2022–2025
  growth and brings the replication without the stage within 0.7 points
  of BLS's growth for every tenure.

## Next

- Second GPT-6.1 Sol review, CI and merge.
- A summary for John Creamer, held for Max's go.
- Follow-ups: the stage in forecast windows (off by default) and home
  internet spending, which the replication still omits.

---

# Methods paper embedded at /paper — September 14, 2026

## State

In progress on `max/paper-embed-20260914`. The manuscript render is copied
into the calculator's static assets; the wrapper page, tool link, sitemap
entry and tests follow. No merge or deploy.

## Done

- Cloned `PolicyEngine/spm-threshold-paper` at `6937830` ("Introduce SPM
  threshold methods in active prose (#7)", 2026-09-14) and rendered it fresh
  with Quarto 1.9.36: `quarto render --to html` exit 0 and
  `quarto render --to pdf` exit 0.
- Copied `_output/paper/{index.html,index.pdf,index_files/}` byte-identically
  to `web/public/paper/web/`. The HTML references `index_files/` relatively
  and carries no root-relative URLs, so it serves standalone from
  `/paper/web/index.html`.
- Render hashes (sha256): `index.html`
  `d1e3711ea1105fc045fb35810af589f2fd35cfbed0c580f385eca198501acc5b`,
  `index.pdf`
  `9fd1d77ee65d7af70cbb85ca7108b398a858d8257f824745a05ac1b5370cb1c9`.

## Next

- Wrapper page `web/app/paper/page.jsx` with the shared version constant.
- Link the paper from the workbench and retire the external Vercel URL.
- Sitemap entry, Vitest coverage, full test/lint/build verification, draft PR.

---

# Documentation refresh for the published 1.0.0 release — September 11, 2026

## State

Complete. The README and documentation state the published release facts
instead of describing an unpublished local candidate. No scientific statement
changed, and no data file, source module, test or workflow was touched.

## Done

- Verified spm-calculator 1.0.0 on PyPI, uploaded 2026-09-11T15:45:10Z from
  main commit 22bab36e; both the wheel and the sdist are present.
- Installed the published wheel in an isolated environment: it calculates
  offline and its bundled artifact content digest matches the pin recorded in
  [the artifact contract](docs/spm-releases.md),
  `3d86d5c4c0423480e6b69b75d222ffa4a7a2639e4094df5ba2504af01be17173`.
- Verified the live app, documentation hub and paper all serve HTTP 200.
- Replaced every pre-publication statement about the package, website, wrapper
  or API with the published facts, and gave the PyPI install commands beside
  the editable checkout.
- Attributed the PolicyEngine country and wrapper behaviour to this
  integration rather than to a released version, since the published country
  model and wrapper do not read this forecast yet; recorded that the country
  and wrapper sides ship in `policyengine-us` 2.0 and the `policyengine`
  wrapper 6.0, both in progress, and linked the
  [1.0 migration guide](docs/migration.md).
- Confirmed against the published wheel that the PolicyEngine, Microcosm and
  Axiom adapters ship inside this package, and corrected the prose that had
  placed them elsewhere.
- Added `migration` to `docs/_toc.yml`. Five pages the documentation hub
  publishes now link to the guide, and the hub returned 404 for it.
- Preserved every scientific hedge that is about official data rather than
  software, including the frozen 2025 geographic forward comparison and the
  pending official workbook for the forward test.
- Docs build `myst build --html`: exit 0. `ruff format --check .` and
  `ruff check .`: exit 0. `pytest tests/ -q`: exit 0, 507 passed, 81 skipped.
- Reran the 28 documentation Python examples: 22 pass, 6 fail exactly as they
  did before the change, with byte-identical output. The six need optional
  runtimes or a preceding block's variables.

## Publication alignment — September 13, 2026

- PyPI publishes `spm-calculator==1.0.0.post1` with package installation
  instructions, stable classifier, and links to the calculator, documentation
  hub, and methods paper. Downloaded wheel and sdist hashes match the registry;
  all 62 runtime/data files match 1.0.0 and reviewed source commit
  `698d923ac62b2cdf123f1098973c81c323fc4d62` byte for byte.
- The browser export identifies verified published version `1.0.0.post1`.
  Reproduce it with `python scripts/export_web_release.py
  --published-package-version 1.0.0.post1` and verify with `--check`.
- The package metadata change preserves the scientific artifact and all
  calculation values. Coordinated downstream pins remain independently
  declared by their runtime bundles.

---

# Equivalent-code provenance adaptation — September 9, 2026

## State

Completed the provenance adaptation and all required validation. No commit,
push, publication, or heavy CE/ACS/PUMS rebuild occurred. The task-specific
no-commit instruction applies; prior journals retain their historical meaning.

## Done

- Archived original receipts, ACS component and source bytes with exact hashes.
- Added a deterministic equivalent-code adaptation receipt explicitly recording
  no fresh raw-source reparse or scientific component rebuild.
- Adapted current receipt/manifest and ACS metadata; reassembled and exported.
- Compared every complete scientific document field and 2,110,752 canonical
  calculation pairs (10,553,760 numeric fields): all exactly equal.
- Verified all four retained normalized NPZ files against original cache hashes.
- Updated current documentation pins; preserved immutable original downloads,
  root README/migration edits and reviewed source/fingerprint regression edits.
- Full tests: 497 passed, 77 existing skips on each of Python 3.12 and 3.13.
- Passed assembly/export checks and exact final-receipt replay on Python 3.12;
  all 115 web tests, Ruff and whitespace checks passed.
- Verified 81,794 permitted original acceptance files unchanged and confirmed
  all six archive/six current evidence files are present verbatim in the wheel.
- Wrote CALCULATOR-PROVENANCE-ADAPTATION-REPORT.md with commands, exact old/new
  identities, equality evidence and coordinated consumer changes for root.
- Current content SHA: 3d86d5c4c0423480e6b69b75d222ffa4a7a2639e4094df5ba2504af01be17173.
- Current file SHA: cc06784feb81f8c7935d4494cea0a9821a79af868ac50383da8be37c6dc14b99.

## Next

- Root: coordinate paper/wrapper/runtime imports using the report's new content
  and file pins; retain all historical acceptance evidence under its old IDs.
- No implementation or required calculator validation remains in this lane.

---

# Calculator CI fingerprint portability — September 9, 2026

## State

Completed and verified a Python 3.9–3.14 portable research-source cache
fingerprint preserving both existing normalization hashes. Task-specific
no-commit instruction overrides standing commit instructions. Canonical/component
artifacts remain untouched; two full-suite source-provenance gates require
root's coordinated identity adaptation.

## Done

- Read applicable repository instructions and recorded starting Git state.
- Reproduced Python 3.12's original fingerprint failures and isolated empty-list
  AST formatting differences, including the 3.12 addition of `type_params`.
- Added an explicit serializer matching the pinned Python 3.13 representation;
  all other existing top-level scientific source sections are unchanged.
- Limited implementation ownership to ACS source fingerprint code and relevant
  ACS/rolling tests; README and migration documentation belong to root.
- Passed all 50 ACS source tests on real Python 3.12.14 and 3.13.9, with semantic
  mutation/cache-admission, formatting, literal/default/order and exact-pin checks.
- Ran actual production fingerprint/serializer code on all six supported Python
  minor versions: original 3.13 bytes, 11 scientific mutations, 25 syntax cases.
- Passed the final combined ACS source/rolling consumer scope: 68 tests each on
  Python 3.12 and 3.13. Full suites each produced 462 passes, 77 existing skips,
  and only the two expected unchanged source-receipt/component hash failures.
- Passed pinned Ruff 0.15.0 lint/format and owned whitespace checks.
- Completed the read-only provenance audit and verified 37 packaged data/web
  configuration files remain byte-identical to their starting snapshots.

## Next

- Root: review `CALCULATOR-CI-PORTABILITY-REPORT.md`, coordinate explicit parser
  receipt/ACS component code-identity adaptation, then reassemble/export and
  recheck provenance while preserving all scientific values and normalized bytes.
- New source SHA: `28d31b3db0caa26f457660f1be9618f63fb08573460c13c68de6cd01b3f7e4bc`.
- No implementation work remains in this bounded lane; no artifacts were
  regenerated/resealed and no commits, push, or publication occurred.

---

# Canonical SPM rollout — September 9, 2026

The canonical scientific artifact and adapters are committed at c89d20f.
Documentation and executable examples now use the same 1.0.0 source.
Real Frame/native Axiom checks, canonical scientific comparisons and the
standalone web suite pass. Mobile tables were checked in a 390-pixel preview
and now scroll through all columns without clipping. Runtime source publication,
exact Fable agreement and coordinated consumer/data releases remain separate.
As of this September 9 entry no PyPI release, dataset publication or production
deployment had occurred. Version 1.0.0 was published on September 11, 2026; the
entry at the top of this file records that release.

The earlier work journals below record development stages. Their no-commit,
remaining-work and artifact-current claims are historical; check Git/PRs and
the dated release evidence for current status.

---

# Progress

## Canonical Frame and actual Axiom bridge — 2026-09-09

### State

Implementation and focused real Frame/core acceptance complete: 140 passed.
Task-specific no-commit/no-push/no-deploy instruction applies.

### Done

- Finished the pre-existing canonical Frame adapter and real native membership,
  primitive classification, county assignment, diagnostics and typed weight path.
- Passed 72 real Frame tests across 2022–2035 and both scenarios.
- Replaced the old Axiom release implementation with source-bound dated tables,
  native person classification/counts and actual core threshold/housing/poverty.
- Compiled both 14-year scenario bundles; tested all 110 declared compositions
  and native out-of-bounds guards without a default factor or evaluator fallback.
- Retained six complete synthetic replay requests/receipts and source bundles
  in /tmp/spm-canonical-axiom-evidence (18 unit checks, max difference 1.46e-11).
- Exposed exact native amounts and documented verified decimal/float poverty
  boundary differences; normalized resource decimal literal transport.
- Wrote the detailed HANDOFF-AXIOM-SPM.md output; Python 3.9 portable export passed.
- Passed all 140 combined real Frame/core tests in 129.28 seconds, with no native
  skips; Ruff lint/format and owned diff whitespace checks also passed.
- Recorded implementation hashes and retained the final test log with evidence.

### Next

- Coordinating owner: copy report/evidence, migrate out-of-scope old docs/script,
  and rebuild source-bound bundles after any scientific provenance refresh.

---

## Package cleanup — 2026-09-09

### State

Owned implementation and focused scientific verification are complete.
Cross-owner legacy tests and live artifact fingerprints still require
coordination, detailed in the package cleanup report. This lane follows the
explicit no-commit/no-checkout restriction; earlier progress below is retained
as historical context.

### Done

- Recorded the pre-existing worktree changes and ownership boundaries.
- Mapped the mixed forecast module's published-source access and CE imports.
- Assigned independent script cleanup, test migration and read-only artifact
  dependency audit within the authorized boundaries.
- Removed five obsolete implementation modules and their package exports,
  three obsolete generators and five obsolete test modules.
- Extracted published-only threshold access; migrated CE scientific imports,
  BLS benchmarks/drift checking and retained source/scientific tests.
- Kept the CE source benchmark with a separate output location and early
  rejection of the frozen historical receipt path.
- Preserved all 37 published year/series records exactly, including metadata,
  standard errors and tenure shares. Verified all 34 packaged data files and
  both complete canonical 2022–2035 scenario objects remain unchanged.
- Passed 76 retained source/CE tests (8 download-dependent skips), 169 further
  scientific/release/forecast tests and 12 drift tests. Artifact gates exposed
  the expected live CE source fingerprint and frozen-receipt test dependencies,
  plus an unrelated concurrently removed web artifact reference.

### Next

- Scientific owner: refresh live CE/rolling provenance without changing amounts
  or rebuilding CE/ACS/population inputs; retain the frozen CE acceptance receipt.
- Coordinating owner: retire/migrate out-of-boundary legacy tests and docs, and
  use the separately updated country implementation for major 1.0.0 checks.
- Coordinating owner: copy the completed `/tmp/package-cleanup-report.md` to
  `/Users/maxghenis/spm-rebuild-20260908/rollout/package-cleanup-report.md`.
  This session's filesystem sandbox rejected that destination with
  `Operation not permitted`; report content is complete in the allowed path.

---

## State

Implementation and verification are complete on `max/bls-2026-threshold-correction`. The branch is ready for review; it has not been pushed or merged.

## Done

- Confirmed the requested worktree, branch, clean starting state, and starting commit `d2746cf`.
- Fixed the drift-watch pipeline status capture so checker exit code 1 can open an issue, with a regression test (review A).
- Bundled and pinned BLS's current workbook, generated the full-precision 2025 segment, advanced the published base, and retained the superseded nowcast with a warning (review B and assignment items 1–3).
- Corrected the tracked CPI composite and made both projection scripts use it by default (review C).
- Added the executable 50/50 rule and a provenance-bearing four-rule backtest artifact with golden MAEs (review D).
- Removed all specified stale pre-repair values and added a repository-wide regression guard (review E).
- Corrected the CE six-code tenure schema and made incomplete CE/CPI inputs fail closed (reviews F–G).
- Pinned trusted workbook hashes and expanded drift checks across both workbooks, all measures, and the 2025 Chart 1 table (reviews H–I).
- Made published-threshold fallback an explicit opt-in (review J).
- Regenerated the web configuration so 2025 is published, preserved future-nowcast UI machinery, and added the archived 2025 evaluation.
- Updated the correction narrative and README, and added the towncrier fragment.
- Froze the original CE-replication levels as tracked evaluation provenance so the corrected CE classifier cannot rewrite the historical 2025 forecasting commitment.
- Documented all requested CE replication approximations and completed the final requirements audit.
- Reproduced the threshold series, four-rule backtest, nowcast, and web configuration from their generators without uncommitted differences.
- Passed the final verification bar: 235 Python tests passed and 18 skipped; Ruff format/check passed; 6 web tests passed; the stale-marker guard passed; and the nowcast script reproduced $41,036.34 / $34,135.99 / $40,755.98 from tracked inputs.

## Next

- Review the commits and open/update PR #32 when ready. No implementation work remains in this lane.

## Final scientific provenance and test refresh — 2026-09-09

### State

Scientific ownership is complete. Final forecast content SHA-256:
`b9dbf5ae49697e3bf3abee2fa22b7429703412cb1e58478938a682a0dfddc821`.
The specific no-commit instruction overrode the standing commit instruction;
no commit, push, publication or deployment occurred. Original checkout,
canonical-impact snapshot and sealed scientific archives remain unchanged.

### Done

- Repaired ACS 2021 normalization identity to include historical schema and
  2017 ID restoration helpers, with mutation regression coverage. Preserved
  complete 2022–24 cache receipts and their normalization identities.
- Reparsed all 616,858 retained 2021 rental records from pinned raw inputs;
  every one of 16 columns exactly matches. Added the verified cache receipt.
- Rebuilt CE and ACS sequentially from cached sources with numerical-library
  threads bounded at two. Included published_thresholds.py in CE identity.
- Reassembled the full forecast and passed deterministic offline checking.
  Every original scenario value is identical after removing only new
  historical-break metadata; original scenario SHA remains 72f859eb…5555.
- Verified 2024–26 thresholds, shares, rents and 2,261,520 canonical output
  field comparisons across 452,304 synthetic calculation pairs against the
  immutable analyzed snapshot. Maximum difference is zero; no population run.
- Verified 12 literal BLS workbook cells and all rounded page values. Preserved
  full published precision; recorded workbook URLs, hashes and cell addresses.
- Added Massachusetts and Sumter published/source-transition disclosures at
  both year endpoints. Derived exact constant rent-index levels from 2029
  through 2035, distinguished 2030's first fully projected window, and recorded
  the small housing-share/factor movement followed by floating-point noise.
- Retired obsolete projection/tract collection tests, retained explicit ASEC
  source parity, migrated the drift monkeypatch, and pinned frozen archive bytes.
- Scoped scientific suite: 355 passed, 10 skipped, one web-dependent test
  deselected. Focused artifact/cache suite: 68 passed, 2 skips. Source-cell
  tests: 14 passed. Final normalization-receipt binding module: 12 passed
  (one additional regression beyond the broader-suite count). Lint, formatting
  and scoped diff checks pass.
- Wrote SCIENTIFIC-SPM-HANDOFF.md with exact commands, results, hashes,
  equality evidence, source-cell receipt and remaining owner actions.

### Next

- Coordinating owner: refresh the web export and Fable gate with final content
  SHA above. The broader run's sole failure is the release test's embedded web
  export check, which still reports the old canonical identity.
- Runtime/provider owners retain CLI, PolicyEngine, microcosm, Axiom and any
  population/native-cap acceptance. Optional real ASEC source parity requires
  its external fixture; live workbook checksums were not freshly fetched.
