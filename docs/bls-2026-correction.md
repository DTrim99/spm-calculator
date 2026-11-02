# The 2026 BLS threshold correction

On July 17, 2026, the Census Bureau [announced](https://www.census.gov/newsroom/press-releases/2026/statement-on-supplemental-poverty-measure.html) that BLS had found errors in the Supplemental Poverty Measure thresholds and would re-release SPM estimates for 2019–2024. BLS [reissued corrected thresholds](https://www.bls.gov/pir/spm/spm_thresholds_2024_correction.htm) the same day, attributing the errors to "corrections to the computer code used to generate the thresholds" introduced with the September 2021 methodology change, and re-derived the median anchor from 83% to 82% of the 47th–53rd percentile FCSUti average to minimize the break in series.

This page documents what changed, what this package shipped before version 0.4, and the historical replication and forecast experiments. Version 1.0, published on [PyPI](https://pypi.org/project/spm-calculator/1.0.0/), uses the [canonical 2022–2035 forecast](rolling-forecasts.md). Removed APIs named below identify historical experiments only. The separately dated [current CE experiment](current-ce-replication.md) records the September 8, 2026 implementation, source hashes, sample exclusions and sensitivity results. Historical tables below remain identified as historical results.

## How large the BLS correction is

Two-adult, two-child national thresholds, as published in the Census P60 reports versus the corrected workbook:

| Year | Tenure | Published | Corrected | Change |
|---|---|---|---|---|
| 2019 | Owner w/ mortgage | 29,080 | 29,076.17 | −0.0% |
| 2019 | Owner w/o mortgage | 24,413 | 24,514.95 | +0.4% |
| 2019 | Renter | 29,194 | 28,913.05 | −1.0% |
| 2020 | Owner w/ mortgage | 29,959 | 29,814.55 | −0.5% |
| 2020 | Owner w/o mortgage | 25,222 | 25,249.14 | +0.1% |
| 2020 | Renter | 30,150 | 29,978.90 | −0.6% |
| 2021 | Owner w/ mortgage | 31,107 | 30,983.24 | −0.4% |
| 2021 | Owner w/o mortgage | 26,279 | 26,055.40 | −0.9% |
| 2021 | Renter | 31,453 | 31,216.69 | −0.8% |
| 2022 | Owner w/ mortgage | 34,235 | 33,978.49 | −0.7% |
| 2022 | Owner w/o mortgage | 28,909 | 28,454.92 | −1.6% |
| 2022 | Renter | 34,518 | 34,140.06 | −1.1% |
| 2023 | Owner w/ mortgage | 36,915 | 36,966.24 | +0.1% |
| 2023 | Owner w/o mortgage | 30,870 | 30,587.73 | −0.9% |
| 2023 | Renter | 37,482 | 37,230.75 | −0.7% |
| 2024 | Owner w/ mortgage | 39,068 | 39,231.00 | +0.4% |
| 2024 | Owner w/o mortgage | 32,586 | 32,878.59 | +0.9% |
| 2024 | Renter | 39,430 | 39,219.89 | −0.5% |

Every change is within ±1.6%. Census's July announcement said it would quantify the effect on SPM poverty rates before the September 2026 report; this threshold project does not calculate that population effect.

This is BLS's second code-correction episode in this series: the P60-280 threshold table footnote records that the 2022 thresholds already reflected "corrections in the computer code used to model" in-kind benefits.

## What this package shipped before 0.4

Versions through 0.3.1 hand-entered the threshold dict. Comparing it against what Census actually published:

| Year | Package ≤0.3.1 (renter) | Published (renter) | Error |
|---|---|---|---|
| 2019 | 27,515 | 29,194 | −5.8% |
| 2020 | 28,881 | 30,150 | −4.2% |
| 2021 | 31,453 | 31,453 | 0.0% |
| 2022 | 33,402 | 34,518 | −3.2% |
| 2023 | 36,606 | 37,482 | −2.3% |
| 2024 | 39,430 | 39,430 | 0.0% |

Owner-tenure errors reach −7.4% (2019). The 2019–2020 rows appear to be misattributed vintages (old-methodology 2019, and a value set matching no publication for 2020); 2022–2023 match no BLS or Census publication we could locate. Only 2021 (renter) and 2024 were correct.

The package's own data errors were several times larger than the BLS threshold revisions that prompted this work. The package errors arose in hand-entered reference data. BLS attributed its revisions to computation-code corrections; these are different failure mechanisms.

## What the 0.4 correction introduced

- **Provenance-tracked series.** `scripts/build_threshold_series.py` is the only writer of the packaged data. It parses the frozen corrected 2005–2024 workbook and BLS's bundled current workbook, records both SHA-256 digests, and emits full-precision thresholds, standard errors, and tenure shares through 2025.
- **Three bundled series.** `bls-corrected-2026-07-17` (default), `census-published-pre-correction` (what every published 2019–2024 SPM statistic used, cross-verified against two consecutive P60 reports per year), and `package-legacy-0.3` (verbatim, for reproducing results from earlier releases).
- **Source comparison.** A weekly CI job checks the frozen corrected workbook, BLS's rolling current workbook, and the live 2025 Chart 1 data page against the packaged series, opening an issue on divergence. This can detect stale package copies, transcription errors and newly published revisions when the job and sources are available. It cannot identify an error shared by the official source and its package copy, or discover a BLS calculation error before BLS changes its published data.
- **Replication fixes.** Benchmarking the CE-based replication against both reference series surfaced four bugs in our own methodology code, detailed below.

## Could an independent replication have caught the BLS bug?

This package includes a from-scratch implementation of the BLS threshold methodology over raw Consumer Expenditure PUMD. We benchmarked it for target years 2019–2024 against both the published and corrected series (`scripts/benchmark_bls_replication.py`), matching each comparison's anchor (83% versus published, 82% versus corrected) so anchor choice cannot manufacture a fit.

Building the benchmark surfaced four errors in our replication code, none previously detected because the validation test allowed 5% tolerance and was skipped by default:

1. **Annualization off by 2×.** FMLI's `*PQ`/`*CQ` pair is one three-month recall window split across calendar quarters; the code treated it as six months of spending and multiplied by 2 instead of 4.
2. **Telephone double-counted.** The FMLI `UTIL` summary already contains `TELEPH`; the code added telephone again.
3. **Phantom columns.** The mortgage-principal (`MRTPRINPQ`) and internet (`INFOTECHPQ`) columns the code referenced do not exist in FMLI; both silently contributed zero. Principal now uses the real outlay columns (`EMRTPNO*`, `MRTPRNO*`); internet has no FMLI summary variable and is a documented gap.
4. **Wrong formula shape.** The code took per-tenure percentiles of the FCSUti distribution. BLS computes `0.82 × (1.2 × FCSUti_E − SU_E + SU_Eh)` over a pooled 47th–53rd percentile estimation sample, swapping the tenure-specific shelter-utilities average — including the 1.2 multiplier for other basic goods and services.

The earlier benchmark's approximate variant (BLS quarter window, principal-inclusive shelter, ×4 annualization) produced **1–4.5% mean absolute deviation per year** with no imputed in-kind benefits. The following table is historical, preceding the current explicit youth/tenure sample policies. It does not describe the rebuilt estimator's current output. Signed deviations for those earlier matched-anchor comparisons:

| Year | vs published (83%) | vs corrected (82%) |
|---|---|---|
| 2019 | +4.0 / +6.6 / +3.1% | +2.7 / +4.9 / +2.9% |
| 2020 | +2.9 / +5.0 / +2.3% | +2.2 / +3.6 / +1.6% |
| 2021 | +1.2 / +3.1 / +0.7% | +0.4 / +2.8 / +0.2% |
| 2022 | −1.7 / +0.3 / −1.9% | −2.1 / +0.7 / −2.0% |
| 2023 | −3.4 / −2.5 / −3.6% | −4.7 / −2.8 / −4.1% |
| 2024 | −2.4 / +0.1 / −3.2% | −4.0 / −2.0 / −3.9% |

(Owner w/ mortgage / owner w/o mortgage / renter.) BLS includes in-kind benefit imputations that this replication omits. This benchmark did not isolate their contribution to the differences.

These comparisons do not demonstrate that this replication could have identified BLS's code errors before publication. Its remaining methodological differences are large enough to complicate attribution of a discrepancy to a BLS error. There is no estimated statistical detection limit here: observed replication discrepancies are not a measured sampling-error floor.

Comparing published artifacts addresses a narrower question: whether our copy matches its selected source, and whether a source has changed. It would expose package hand-entry discrepancies and an available July 17 revision. It would not diagnose the original error inside the BLS calculation.

At matched anchors, the two historical variants fit their selected source series similarly. This is a descriptive comparison, not independent validation of BLS's re-anchoring calculation.

## 2025 published

BLS finalized the 2025 research SPM thresholds on August 24, 2026. The current workbook provides full-precision thresholds, standard errors, and tenure shares; its thresholds round to the values on the BLS publication page:

| Tenure | Full-precision workbook | BLS page | Growth from corrected 2024 |
|---|---:|---:|---:|
| Owner w/ mortgage | $41,322.71 | $41,323 | 5.332% |
| Owner w/o mortgage | $34,326.00 | $34,326 | 4.402% |
| Renter | $41,700.56 | $41,701 | 6.325% |

The package's committed nowcast is now an evaluation artifact rather than a current estimate. Against the full-precision actuals, it missed by −0.69%, −0.55%, and −2.27% respectively, for a **1.17% mean absolute error** across tenures. For comparison, aging the corrected 2024 base by CPI-U missed by 2.58%, the CE replication ratio alone missed by 0.75%, and the original pre-composite-repair nowcast missed by 0.98%. The renter estimate accounted for most of the committed nowcast's error.

Reproduction uses a tracked snapshot of the 2024 and 2025 CE-replication levels that defined the original forecasting commitment. This intentionally preserves the estimate after later corrections to the package's general CE tenure mapping and validation rules; the snapshot is evaluation provenance, not a current-method CE result.

## Known approximations

The CE replication retains the following expenditure and price approximations:

- It deflates each interview with an annual-average FCSUti CPI keyed to collection year rather than BLS's quarterly treatment of the terminal Q1.
- It applies the 80% food allocation to the combined FMLI `GROCER` summary rather than UCC 790210 alone.
- It omits home-internet expenditures because FMLI has no matching summary.
- It omits BLS's in-kind imputations for broadband, LIHEAP, NSLP, WIC, and rental assistance.

The rebuilt estimator also names unresolved minor-only-unit and tenure-3/5/6 sample policies, and documents its unverified midpoint-CDF convention. The [current experiment](current-ce-replication.md) measures youth-policy sensitivity up to 0.0411% and tenure-5/6 sensitivity up to 1.7372%; other approximation effects remain unmeasured. These results do not establish that all approximations are negligible. Separately, the current canonical calculation combines year-specific published or modeled rent indices with corrected BLS national thresholds and housing shares. This source-vintage combination does not reproduce the superseded Census local dollar thresholds. Per-area provenance identifies the geographic component and its limits.

## Projecting thresholds past the published years

BLS re-estimates thresholds from the rolling five-year CE window, so the published series moves with consumption as well as prices. The historical retrospective backtest evaluated four projection rules over 2020–2024 using corrected prior-year bases and corrected actuals (the now-removed `scripts/backtest_threshold_projection.py`). The frozen result is `spm_calculator/data/nowcast/backtest_2020_2024.json`. This experiment used corrected data and realized inputs, not authenticated historical information sets. The table preserves its original labels; “status quo” describes the historical comparator, not the current provider.

| Rule | Mean abs error/yr | Worst year |
|---|---|---|
| All-Items CPI-U aging (status quo in policyengine-us) | 2.23% | 3.99% (2023) |
| FCSUti-composite CPI aging (realized) | 1.57% | 2.59% (2024) |
| CE replication growth ratio | **0.41%** | **0.65% (2020)** |
| 50/50 blend of the last two | 0.76% | 1.39% (2023) |

The price-aging estimates were generally below the corrected actuals in the later years. Prices alone do not capture all changes in expenditures or benefit imputations, but this backtest does not separately identify those contributions. All methods use retrospective inputs; a prospective forecast must also account for expenditure and CPI values unavailable at its actual prediction date. The historical backtest ranks replication first. The blend remains the committed 2025 method, preserving the original selection rather than retrospectively changing that commitment.

The **archived 2025 nowcast** (formerly exposed by `nowcast_thresholds(2025)`; retained as `spm_calculator/data/nowcast/nowcast_2025.json`) applied the blend to the corrected 2024 base:

| Tenure | Replication ratio | FCSUti CPI ratio | Blend | Nowcast 2025 |
|---|---|---|---|---|
| Owner w/ mortgage | 1.0599 | 1.0321 | 1.0460 | $41,036.34 |
| Owner w/o mortgage | 1.0443 | 1.0321 | 1.0382 | $34,135.99 |
| Renter | 1.0462 | 1.0321 | 1.0392 | $40,755.98 |

Two data notes. 2025 CPI annual averages are 11-month means — BLS canceled the October 2025 CPI release during the federal shutdown. And computing the replicated 2025 threshold surfaced one more schema break: from the 2024Q2 files, CE replaces the `FOOD`/`FDHOME` summaries with `GROCER` (all grocery purchases, food and nonfood). The package currently approximates food at home as 80% of all `GROCER`; as noted below, BLS applies the 80% factor only to UCC 790210, so this is not an exact replication. Before the per-row vintage-aware construction, pooled windows silently zeroed food for redesign-era quarters and replicated 2025 thresholds *fell* 4–5% nominal — the same silent-schema-drift failure class as everything else on this page.

BLS published the actual 2025 thresholds on August 24, 2026. The former `nowcast_thresholds` and `get_thresholds` APIs have been removed. The JSON archive preserves the table above for evaluation; current published lookup uses `get_published_thresholds(2025)`, and current unit calculations use `load_forecast().calculate_unit(...)`.

The 2026 CE collection window ended in 2026Q1. These historical experiments made no new 2026 forecast or preregistration promise. The separate [canonical rolling forecast](rolling-forecasts.md) now projects 2026–2036 conditional on its September 9, 2026 information set and declared donor, price and real-spending assumptions. Its retrospective checks are not a reconstructed real-time evaluation. Any prospective commitment requires a real timestamp, a declared available-input cutoff and a prediction made before the relevant outcome is published.
