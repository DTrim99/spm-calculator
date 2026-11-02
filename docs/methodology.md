# Methodology

Version 1.1 combines published national SPM thresholds and housing shares for
2022–2025 with conditional CE/ACS rolling forecasts for 2026–2036.
`load_forecast()` reads the bundled schema 2 artifact offline. It does not
require an estimated-year opt-in or retrieve missing inputs. The
PolicyEngine, Microcosm and Axiom adapters ship inside this package; their
runtimes install separately, and the PolicyEngine country and wrapper
integration ships in `policyengine-us` 2.0 and the `policyengine` wrapper 6.0,
both in progress. See the [1.0 migration guide](migration.md).

The [BLS 2025 publication](https://www.bls.gov/pir/spm/spm_thresholds_2025.htm)
provides national thresholds for a two-adult, two-child reference unit.
The geographic component has separate provenance: published Census rent
indices where available, and explicitly modeled area estimates otherwise.
A published national base does not make a modeled local threshold official.

## Threshold calculation

For tenure t, year T, classified adult/child counts A/C and area g:

```text
unadjusted threshold = national threshold(T,t) × equivalence factor(A,C)
geographic factor = 1 + housing share(T,t) × (rent index(T,g) − 1)
threshold = unadjusted threshold × geographic factor
housing portion = unadjusted threshold × housing share(T,t) × rent index(T,g)
in poverty = resources < threshold
```

Explicit national calculations use a geographic factor of one. Results
retain the selected scenario, source identity, component statuses, windows
and area diagnostics. Resources are optional; without them `is_in_poverty`
is `None`. This package does not forecast population characteristics or
SPM resources.

## Published national inputs

The canonical artifact uses the corrected BLS national series. It preserves
the numeric cells in the bundled workbooks; whole-dollar web-page amounts
are display rounding. BLS asks users to retain spreadsheet significant
digits when calculating with the thresholds. The [source-cell validation](validation.md)
records worksheet locations and provenance.

| Tenure | 2025 national threshold, dollars | 2025 housing share |
| --- | ---: | ---: |
| Owner with mortgage | 41,322.707394 | 0.4285074824 |
| Owner without mortgage | 34,325.997720 | 0.3120194707 |
| Renter | 41,700.555713 | 0.4336857704 |

Sources: [BLS threshold workbook](https://www.bls.gov/pir/spm/spm_thresholds.xlsx)
and [BLS shares workbook](https://www.bls.gov/pir/spm/spm_shares.xlsx), retained
with source receipts in the package. Housing share means Total Shelter
Share plus Total Utilities Share, excluding telephone and internet. It is
different from the tenure population shares returned by
`published_thresholds.get_tenure_shares`.

```python
from spm_calculator import get_published_thresholds, load_forecast

forecast = load_forecast()
entry = forecast.entry(2025)
assert entry["thresholds"] == get_published_thresholds(2025)
assert entry["national_status"] == "published"
assert entry["housing_share_status"] == "published_anchor"
print(entry["thresholds"]["renter"], entry["housing_shares"]["renter"])
# 41700.555713 0.4336857704
```

## Equivalence scale and unit composition

The Betson three-parameter scale adjusts the reference threshold for
classified SPM adults and children. Its raw scale is:

| Composition | Raw scale |
| --- | --- |
| One adult with children | `(1 + 0.8 + 0.5 × (C − 1))**0.7` |
| Multiple adults with children | `(A + 0.5 × C)**0.7` |
| One adult, no children | `1.0` |
| Two adults, no children | `1.41` |
| Three or more adults, no children | `A**0.7` |

Divide by `3**0.7` to normalize to two adults and two children.
`SPMUnit` requires at least one classified adult.

Population adapters use native SPM membership. A person is an SPM adult
when `age >= 18`, or when `age >= 15` and
`is_spm_independent_minor_role` is true. When that primitive role is
unavailable, callers must supply explicit household-head and spouse inputs.
Neither age ordering nor row order identifies those roles. For example,
a 16-year-old independent head and a dependent child form a one-adult,
one-child SPM unit under this contract.

This population classification does not resolve the separate research
sample-policy uncertainty for minor-only CE consumer units. CE interviews
and population SPM units are distinct observations.

## Geographic assignment and estimation

The supported geographic inputs are explicit national selection or an area
in `forecast.areas_for_year(year)`. The API calls its area namespace
`"metro"`; each area's `area_type` identifies whether it is an MSA, state
nonmetro area or state metro residual. Use the selected year's menu and
metadata instead of assuming all entries are MSAs or published estimates.

`resolve_county(year, county_fips, county_vintage="2020")` assigns a county
to an area. County is an assignment input, not the rent-estimation unit.
The assignment is a research mapping and can change with year. The
calculator does not estimate a county rent or substitute another geography
when a requested county or area is unavailable. Selecting national geography
is an explicit caller decision.

For the published areas, 2022–2024 rent indices come from the selected year's
Census workbook. Combining those indices with corrected BLS thresholds and
housing shares does not reproduce the superseded Census workbook's dollar
thresholds. The 2022 menu contains 342 published areas plus 7 modeled
residuals; 2023 onward contains 341 published-menu areas plus 8 modeled
residuals. A published area identifier can have a modeled index in later
years. See [rolling forecasts](rolling-forecasts.md) for the historical
breaks, model support and post-2024 construction.

The research ACS estimator pools cash-rented, occupied, two-bedroom PUMS
housing records with complete kitchen and plumbing, positive gross rent and
positive survey weights. It applies `GRNTP × ADJHSG / 1,000,000` once and
uses weighted local-to-national median ratios. Fractional PUMA allocations
approximate area membership; county crosswalks do not recover confidential
household locations. Thin support and topcoding remain visible in results.

## CE replication and future windows

The research estimator uses twenty CE collection quarters, `(T−5)Q2`
through `TQ1`, for threshold year T. It selects consumer units with children
and uses the original `FINLWT21` survey weights for expenditures and
percentiles. Population calibration weights must not replace them.

FMLI expenditures cover food, clothing, shelter, utilities and telephone.
`UTIL` already includes telephone. Shelter includes observed mortgage
principal outlays. Food construction handles the 2024 redesign using an
explicit `GROCER` allocation approximation. There is no matching FMLI
internet summary, and the estimator omits BLS in-kind benefit imputations.

The pipeline annualizes each quarterly recall window by four, converts to
target-year prices using explicit annual CPI, and normalizes to the
reference composition. It selects the inclusive 47th–53rd percentile band
of equivalized expenditures with a weighted midpoint-CDF convention. For
that band E and tenure h, it computes:

```text
0.82 × (1.2 × FCSUti_E − SU_E + SU_Eh)
```

SU excludes telephone. The [2026 BLS correction](bls-2026-correction.md)
changed the anchor from 83% to 82%. Annual rather than quarterly price
treatment, the percentile implementation, food allocation, omitted
internet/in-kind amounts and selected sample policies prevent a claim of
exact BLS reproduction. The [September 8 CE experiment](current-ce-replication.md)
quantifies selected sample-policy sensitivities, while preserving its
original receipt and tables.

For 2026–2036, the current estimator advances each twenty-quarter window
using same-season observed donors. It anchors national levels and housing
shares to published BLS 2025 inputs. `ce_trend` applies a declared
half-shrunk near-median real-expenditure trend; `zero_real` holds donor real
spending constant. Both apply the pinned February 2026 CBO aggregate CPI-U
growth path under explicit component and rent assumptions.

These are conditional research forecasts, not BLS/Census forecasts or
estimated uncertainty bounds. The [rolling-forecast documentation](rolling-forecasts.md)
provides the window table, formulas, evaluation results and donor limits.
