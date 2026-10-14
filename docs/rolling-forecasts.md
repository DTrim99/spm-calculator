# Rolling expenditure and rent forecasts

The calculator projects missing observations and advances the expenditure and
rent windows used to estimate SPM thresholds. Price growth and real spending
growth are separate assumptions. An inflation-only projection holds the real
spending of new observations constant; it does not imply that actual real
spending will remain constant.

Version 1.1 loads one canonical schema 2 artifact for 2022–2036, with
published national inputs through 2025 and conditional research forecasts
thereafter. [Archived source snapshots](spm-releases.md) and forecast
commitments retain their original bytes. The artifact records an information
date of September 9, 2026, source receipts and a content digest. Its Python
consumer works without PolicyEngine, Microcosm, Axiom or a network connection.

## Reference years and moving windows

The revised [BLS methodology](https://www.bls.gov/pir/spm/spm_thresholds_2025.htm)
uses five years of CE data lagged by one year. A threshold for reference year
T uses collection quarters (T−5)Q2 through TQ1. The current observed files end
in 2025Q1. The [Census SPM geography method](https://www2.census.gov/programs-surveys/supplemental-poverty-measure/datasets/spm/spm_techdoc.pdf)
uses the preceding five calendar years of ACS rents.

| SPM reference year | CE collection window | Projected CE quarters | ACS calendar window | Projected ACS cohorts |
| --- | --- | ---: | --- | ---: |
| 2022 | 2017Q2–2022Q1 | 0 | 2017–2021 | 0 |
| 2023 | 2018Q2–2023Q1 | 0 | 2018–2022 | 0 |
| 2024 | 2019Q2–2024Q1 | 0 | 2019–2023 | 0 |
| 2025 | 2020Q2–2025Q1 | 0 | 2020–2024 | 0 |
| 2026 | 2021Q2–2026Q1 | 4 | 2021–2025 | 1 |
| 2027 | 2022Q2–2027Q1 | 8 | 2022–2026 | 2 |
| 2028 | 2023Q2–2028Q1 | 12 | 2023–2027 | 3 |
| 2029 | 2024Q2–2029Q1 | 16 | 2024–2028 | 4 |
| 2030 | 2025Q2–2030Q1 | 20 | 2025–2029 | 5 |
| 2035 | 2030Q2–2035Q1 | 20 | 2030–2034 | 5 |
| 2036 | 2031Q2–2036Q1 | 20 | 2031–2035 | 5 |

National thresholds and housing shares for 2022–2025 use published BLS
values directly. Published Census indices anchor represented areas in
2022–2024; the post-2024 geographic projection uses the 2024 rent-index
anchor. Residual areas without a published anchor remain explicitly modeled.
A 2025 local amount therefore combines published national inputs with
modeled geography. The [dated source check](../spm_calculator/data/current/forecast_source_checks.json)
records that 2025 local geography was not yet published at the September 9
information cutoff.

## Prices and real spending

For a missing CE quarter, the donor is the latest observed quarter in the
same season. For example, projected 2026Q1 uses observed 2025Q1, while
projected 2025Q2 uses observed 2024Q2. Demographics, tenure, weights and
missing-value patterns stay with that donor. Monetary expenditures scale by:

`projected expenditure = donor expenditure × price factor × real spending factor`.

The price factor uses the CE composite index with weights from the origin's
five-year sample. Historical annual CPI observations are pinned. The future
path uses the February 2026 CBO calendar-year CPI-U forecast:
2.9248% in 2026, 2.5476% in 2027, 2.3614% in 2028, 2.2880% in 2029 and
about 2.26% annually in 2030–2036. The [pinned CBO source](https://raw.githubusercontent.com/US-CBO/cbo-data/284a95665f9f2f74ed1f482feb629b43fce323da/data/economic/economic_projections/calendar_2026-02.csv)
and [horizon inputs](../spm_calculator/data/current/forecast_horizon_inputs.json)
record the exact annual levels and growth rates. Growth ratios apply to
the observed BLS 2025 anchor. Applying aggregate growth uniformly to future
CE price components and baseline rents is a package assumption; CBO does
not supply those component or local-rent forecasts here.
The committed BLS API receipt verifies all-items CPI-U annual averages of
313.689 for 2024 and 321.943 for 2025. The latter covers eleven months:
October 2025 data were not collected.

Two spending scenarios use the same historical records and price path:

- **No real spending growth (`zero_real`)** gives new observations a real
  spending factor of one.
- **CE real-spending trend (`ce_trend`)** fits the log of near-median FCSU
  expenditures in five nonoverlapping annual blocks ending in Q1. All blocks
  use the same reference-family normalization and origin-year prices. The
  forecast applies half the fitted annual log slope: a declared 0.5 shrinkage
  toward zero. For a donor advanced by k years, the real factor is
  `exp(0.5 × fitted log slope × k)`.

Each annual block selects its own 47–53 percentile expenditure band. The
trend may be negative. Ten-block and pandemic-excluded fits show sensitivity
to the estimation period; the latter excludes blocks ending in 2021Q1 and
2022Q1. Ordinary least-squares slope standard errors describe the fit, not
survey-design uncertainty or a forecast interval. The default `ce_trend`
specification was selected independently of the retrospective test results.
It is not labeled the best-performing model.

The current-origin fits in the [pinned CE component](../spm_calculator/data/current/ce_rolling_forecast.json)
give the following annual rates after the declared 0.5 shrinkage:

| Fit | Annual blocks | Annual real spending growth |
| --- | ---: | ---: |
| Five-year trend | 5 | 0.9143% |
| Ten-year trend | 10 | 0.8167% |
| Pandemic-excluded trend | 3 | −0.1354% |

The difference between these fits is sensitivity to the estimation period,
not a confidence interval.

Aggregate real consumption growth is not interchangeable with real FCSU
spending among families near the SPM estimation range. The common real-growth
scenario also holds future sample composition and relative category spending
fixed. It does not model changing household composition or behavioral
responses. Existing [CE replication approximations](current-ce-replication.md)
remain, including annual price treatment and omitted internet and in-kind
imputations.

## Thresholds and housing shares

Every projected year reruns the CE estimator on its own twenty-quarter
window. Let C(T,h) be its raw national threshold for year T and tenure h.
Future national thresholds use:

`threshold(T,h) = published threshold(2025,h) × C(T,h) / C(2025,h)`.

The estimator's housing fraction is `0.82 × tenure SU mean / C(T,h)`.
The projected housing share equals the published BLS 2025 shelter-plus-
utilities share times the ratio of that estimated fraction in T to its
value in 2025. Anchoring recovers the published base exactly. Shares outside (0,1) cause an error.

The sample and composite-price weights are recomputed inside each target
window. Donor scaling uses the frozen origin weights to bridge price years;
the estimator then applies only the remaining target-year price adjustment.
This is an explicit approximation, with no second application of the same
inflation interval.

A positive real-growth assumption need not raise every tenure's threshold
at every horizon. In 2026, the national renter threshold is $43,829.55 under
`ce_trend`, compared with $43,888.11 under `zero_real`. The reselected
band's mean FCSUti spending rises by $76.46, while its overall SU mean
rises by $127.94 and its renter SU mean falls by $33.25. The formula
subtracts the overall SU mean and adds the renter SU mean, so this housing
adjustment offsets the increase in common spending. By
2030, renter thresholds are $50,011.91 and $48,954.72 respectively; by 2035,
they are $58,520.42 and $54,735.04. These are conditional research results
from the [canonical artifact](../spm_calculator/data/current/rolling_forecast_2026_09_09.json),
not published BLS forecasts.

## Geographic rent indices

ACS inputs are cash-rented, occupied two-bedroom housing units with complete
kitchen and plumbing, positive gross rent and positive survey weight.
`GRNTP × ADJHSG / 1,000,000` applies the source product's dollar adjustment
once. The calculation pools weighted individual records; it does not average
annual medians. Census explains [why multiyear estimates describe a pooled
period](https://www.census.gov/newsroom/blogs/random-samplings/2022/03/period-estimates-american-community-survey.html).

Public PUMS identifies PUMAs rather than exact Census SPM areas. Pinned
fractional PUMA-to-county-to-area mappings use population allocation and
Census's 2013 metropolitan definitions. Historical 2022 estimation uses
2010 PUMAs; 2023 onward uses 2020 PUMAs. This approximates area membership
and does not identify actual household counties.

The menu has 349 areas in each year: 342 published areas and 7 unanchored
modeled residuals in 2022, then 341 published-menu areas and 8 unanchored
modeled residuals. The later years' published-menu identifiers carry modeled
rent indices. Source population outside the published menu remains in the
full-US denominator. Every calculated area must have positive coverage.
County FIPS is a lookup input assigning a unit to its year-specific area,
not a separate county estimation level.

The 2019–2023 and 2020–2024 PUMS products have different weights and dollar
vintages. Let R be the modeled ratio of local to national pooled median rent.
The forecast bridges the common 2020–2023 period:

`index(T) = official index(2024) × R(old overlap)/R(old full) × R(new T)/R(new overlap)`.

Each ratio uses records from one product. The overlap comparison removes the
measured common-window vintage shift; it does not eliminate every revision
effect. The four-year overlap uses the five-year product's supplied weights
and is not an official four-year estimate. Weighted microdata medians also
approximate Census's grouped-data interpolation.

Missing ACS years copy the observed 2024 cohort. Baseline nominal gross rents
grow at the all-items CPI path and are deflated by that same path. New cohorts
therefore have zero real rent growth. Historical cohorts still roll out, so
local and national pooled medians can change differently. A separate
sensitivity adds two percentage points to each missing year's nominal rent
growth while leaving the deflator unchanged. The CE spending scenario does
not change this ACS assumption.

For housing share s and rent index I, the location factor is `1 + s × (I−1)`.
Both s and I can change over time. Under this artifact's fixed 2024 donor
distribution, uniform nominal growth and matching deflator, all 349 relative
rent indices reach their final constant level in SPM year 2029, through
2036. The first unchanged transition is 2029→2030. The 2029 window contains
the observed 2024 cohort plus four projected cohorts; 2030 is the first
all-projected window. The estimator continues advancing windows; it does
not manually freeze rent values.

Housing shares still move slightly from 2029 to 2030, so geographic factors
change by up to about 0.0000001024 under `ce_trend`. After 2030, remaining
factor changes are at floating-point precision. National dollar thresholds
continue changing. These assumptions do not generate persistent local
rent-growth differences. The artifact records this limit in
`assumptions.acs.relative_index_stabilization`.

### Historical series breaks

The artifact preserves two 2022→2023 breaks rather than interpreting them
as annual rent growth:

| Area or assignment | 2022 rent index | 2023 rent index | Interpretation |
| --- | ---: | ---: | --- |
| Massachusetts Nonmetro (`25002`) | 1.551 | 1.043 | Published-source series break |
| Sumter County, SC (`45085`) | 0.489 | 0.7155172413793104 | Assignment changes from published South Carolina Metro (`45001`) to unanchored modeled residual (`modeled_residual_metro:45`) |

The selected area's `series_breaks` metadata labels both endpoints. Sumter's
notice applies to that county assignment; it is not a statement that all
counties in the modeled residual changed assignment. Values and source
identities remain available for audit.

## Support and topcoding

Repeated projected copies do not create new original observations. Diagnostics
combine their allocation-adjusted weights by original record before computing
the Kish count, `(sum weights)² / sum(weights²)`. An expected whole-record
count sums each original's geographic allocation fraction once. An area is
flagged when the minimum of unique records, expected count and Kish count is
below 30. Projected windows also flag thin support in their original donor
cohort. This is a declared research heuristic, not a Census publication
standard or a survey-design effective sample size.

The artifact reports cohort-specific cash-rent and utility topcoding flags,
affected weight shares and a conservative median warning. A median below the
cash-rent topcode does not prove that utility topcoding is irrelevant. Thin
areas retain their values and warnings; the consumer never substitutes a
different geography.

## Retrospective evaluation

CE forecasts use origins 2019–2024 and targets through 2025, giving 6, 5, 4,
3, 2 and 1 folds at horizons one through six. Each fold uses its own published
revised-method origin threshold and only CE records through that origin's Q1.
Both candidates and the inflation-only comparator use realized annual prices
through the target. These are current-vintage, price-conditional retrospective
tests, not a reconstruction of real-time forecast accuracy.

The primary error is mean absolute percentage error (MAPE), equally weighting
the 63 fold-tenure results. Short horizons have more folds. A second metric
equally weights the per-horizon MAPEs for displayed horizons one through five.
The app warns when a scenario fails to beat the CPI-only baseline overall,
on that horizon-balanced measure, or at the selected forecast horizon.

The ACS holdout uses the corrected 2018–2022 product, anchors to the official
2023 index, drops 2018 and adds a projected copy of the 2022 cohort as 2023.
It compares predicted 2024 indices with official 2024 values and unchanged
2023 indices across all 341 areas. Its primary metric equally weights area
absolute percentage errors. This tests the donor mechanism, not the later
cross-vintage bridge. The app flags failure to improve on unchanged indices.

Results from the pinned CE and [ACS components](../spm_calculator/data/current/acs_rolling_forecast.json):

| CE method | Overall MAPE | Horizon-balanced MAPE, years 1–5 | One-year MAPE |
| --- | ---: | ---: | ---: |
| CE real-spending trend | 2.8428% | 3.3276% | 0.7152% |
| No real spending growth | 3.1487% | 3.6955% | 0.6512% |
| All-items CPI extrapolation | 5.3117% | 5.9268% | 2.2868% |

Both rolling methods outperform CPI extrapolation at every evaluated horizon.
Zero real growth performs slightly better at one year; the trend performs
better on the overall and horizon-balanced comparisons. Only two folds
inform the five-year result and one informs the supplemental six-year result.
The ACS donor method's MAPE is 1.2537%, compared with 1.7284% for unchanged
indices. Its median absolute percentage error is 0.8745%, compared with
1.5048%. These comparisons have the source-vintage and price-conditioning
limits described above.

Missing folds, areas or invalid results block generation. Numerical
underperformance is reported rather than treated as a data failure. The
artifact also freezes a hashed 2025 geographic prediction for comparison with
the later official workbook; that forward test is pending official data.
Neither these tests nor the fitted trend provide forecast uncertainty bounds.

## Standalone calculation

Run this example after installing the package:

```python
from spm_calculator import SPMUnit, load_forecast

forecast = load_forecast()
assignment = forecast.resolve_county(2026, "06075", county_vintage="2020")
area = forecast.areas_for_year(2026)[assignment["area_id"]]
result = forecast.calculate_unit(
    SPMUnit(
        unit_id="household-1",
        num_adults=2,
        num_children=2,
        tenure="renter",
        year=2026,
        geography_kind=assignment["kind"],
        geography_id=assignment["area_id"],
        resources=50000,
    ),
    scenario="ce_trend",
)
print(area["name"], area["area_type"], area["status"])
print(round(result["threshold"], 2), result["is_in_poverty"])
```

Use `geography_kind="national"` without an area ID for an explicitly
national calculation. `entry(year, scenario=...)` exposes each year's
thresholds, shares, rent indices and windows. Results record the selected
scenario, artifact identity and per-area diagnostics. An area's
`official_published_area` describes its source-menu status; `status`
describes the current geographic component.

Unknown years, areas, counties, scenarios or county vintages raise errors.
An `as_of` date before the information cutoff also raises. No alternate
runtime release path or geographic substitution supplies missing inputs.
Pass `expected_sha256` when loading a retained artifact to verify its content
identity; retrieve the current identity from `forecast.content_sha256`.
The [provider](policyengine-release-integration.md),
[Frame](microcosm-integration.md) and [real Axiom](axiom-integration.md)
examples use this same artifact. Their adapters ship inside this package;
the PolicyEngine country and wrapper integration ships in `policyengine-us`
2.0 and the `policyengine` wrapper 6.0, both in progress. See the
[1.0 migration guide](migration.md).

## Rebuild and verify

Raw CE ZIPs and ACS PUMS products stay in configurable external caches. The
component manifests pin their URLs and hashes; compact crosswalks and source
receipts ship with the package. With matching raw source bytes cached:

These commands regenerate outputs; use them only when intentionally rebuilding
the scientific artifact, not to run a threshold calculation.

```sh
python scripts/build_ce_forecast.py --output spm_calculator/data/current/ce_rolling_forecast.json
python scripts/build_acs_forecast.py --output spm_calculator/data/current/acs_rolling_forecast.json
python scripts/build_rolling_forecast.py
```

Both scientific builders support `--cache-dir` and an offline `--check` that
repeats the calculations from cached raw inputs. The assembly check below
uses committed component results, verifies their scientific code and package
input hashes, checks the official CPI receipt and required evaluation
coverage, and reproduces the portable artifact without raw microdata:

```sh
python scripts/build_rolling_forecast.py --check
```

The committed browser export identifies the verified PyPI package 1.0.0.post1.
Reproduce and verify the export with:

```sh
python scripts/export_web_release.py --published-package-version 1.0.0.post1
python scripts/export_web_release.py --published-package-version 1.0.0.post1 --check
```

For an unpublished development preview, omit `--published-package-version`
when generating and checking that preview's export. The default remains
`local_preview`; an explicit published version must match `pyproject.toml`
and be verified before use.

Changing scientific logic or pinned scientific inputs requires rebuilding the
affected component before assembly. A change confined to code-identity
serialization can use a separately recorded equivalent-code adaptation when
the original source and receipts are retained, normalization logic and cached
products are unchanged, and exact comparison confirms that every scientific
field survives reassembly. Such an adaptation records both source identities;
it does not claim a fresh raw-data parse. See the
[current artifact identity](spm-releases.md#current-artifact-identity) for the
retained evidence and current consumer pins. Hashes check integrity; they are
not signatures or independent evidence of source authenticity.
