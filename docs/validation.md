# Validation

Validation checks source fidelity, deterministic calculation and research
forecast performance separately. Version 1.1 uses published BLS national
inputs through 2025 and conditional forecasts through 2036. Passing source
checks does not establish exact replication of BLS code or prospective
forecast accuracy.

## Published BLS cells and source vintages

The canonical 2022–2025 national inputs match numeric cells in the bundled
BLS workbooks. The [cell receipt](../spm_calculator/data/current/bls_published_cell_receipt.json)
records original workbook bytes, worksheet coordinates and numeric text.
BLS directs users to keep the spreadsheet's significant digits in
calculations on its [SPM methodology page](https://www.bls.gov/pir/spmhome.htm).

| Year | Tenure | Worksheet cell | Canonical dollars | BLS page dollars |
| --- | --- | --- | ---: | ---: |
| 2024 | Owner with mortgage | `V4` | 39,230.994457 | 39,231 |
| 2024 | Owner without mortgage | `V7` | 32,878.594848 | 32,879 |
| 2024 | Renter | `V10` | 39,219.893902 | 39,220 |
| 2025 | Owner with mortgage | `W4` | 41,322.707394 | 41,323 |
| 2025 | Owner without mortgage | `W7` | 34,325.997720 | 34,326 |
| 2025 | Renter | `W10` | 41,700.555713 | 41,701 |

All cells are on worksheet `2005-2024`, including the 2025 column. Sources:
[corrected workbook](https://www.bls.gov/pir/spm/spm_threshold_200524_corrected.xlsx),
[current workbook](https://www.bls.gov/pir/spm/spm_thresholds.xlsx),
[corrected chart data](https://www.bls.gov/pir/spm/spm_correction_chart_data.htm)
and [2025 Chart 1](https://www.bls.gov/pir/spm/spm_chart_1_2025_data.htm).
The earlier Census 2024 amounts—39,068, 32,586 and 39,430 dollars—belong to
a superseded publication vintage. They are not rounding of the corrected
cells. The [correction history](bls-2026-correction.md) preserves those
historical comparisons.

The package also checks BLS housing shares, source hashes, normalized ACS
product receipts and scientific component identities. A live source drift
check can detect a changed source or stale copy; matching official bytes
cannot diagnose an error inside the official calculation.

## Geography and calculation checks

The tests preserve each published Census rent index at its historical
anchor and check modeled area diagnostics separately. The canonical formula
combines the selected year's rent index with corrected BLS national inputs
and housing shares. It does not claim equality to the superseded Census
workbook's local dollar thresholds.

County tests check year-specific area assignment and vintage validation.
County is not a rent-estimation unit. Unknown inputs raise; national
selection is explicit. Thin support, topcoding and published-to-modeled
series breaks remain in the result provenance.

```python
from spm_calculator import SPMUnit, load_forecast, spm_equivalence_scale

forecast = load_forecast()
assert spm_equivalence_scale(2, 2) == 1.0
assert abs(spm_equivalence_scale(1, 0) - 1 / 3**0.7) < 1e-12
assert abs(spm_equivalence_scale(2, 0) - 1.41 / 3**0.7) < 1e-12
result = forecast.calculate_unit(SPMUnit(
    unit_id="reference", num_adults=2, num_children=2,
    tenure="renter", year=2025, geography_kind="national",
))
assert result["threshold"] == 41700.555713
assert result["geographic_factor"] == 1.0
assert result["geography_status"] == "explicit_national"
```

Real Microcosm Frame checks cover native membership, primitive adult/child
classification and typed survey weights. The [Axiom integration](axiom-integration.md)
checks the real core runtime and reports its composition-domain and decimal
boundary limits; dense `AxiomEngine` does not support the required relations
and dated derived rules.

## Research evaluation limits

The [rolling forecast evaluation](rolling-forecasts.md#retrospective-evaluation)
uses 21 CE origin/target folds, or 63 tenure results, conditional on realized
prices and current source vintages. Overall MAPE is 2.8428% for `ce_trend`,
3.1487% for `zero_real` and 5.3117% for CPI extrapolation. These are not
real-time forecast error estimates.

The ACS 2023→2024 holdout covers 341 published areas. Its equal-area MAPE
is 1.2537%, versus 1.7284% for unchanged indices. It tests the donor
mechanism, not the later cross-vintage bridge or unanchored residual areas.
The frozen 2025 geographic forward comparison remains pending official
inputs. No survey-design or forecast uncertainty interval is estimated.

## Run offline checks

From the source checkout with its development dependencies installed:

```sh
python scripts/build_rolling_forecast.py --check
SKIP_CE_DOWNLOAD=1 python -m pytest -q tests/test_bls_published_cell_receipt.py tests/test_threshold_series.py tests/test_equivalence_scale.py tests/test_rolling_forecast.py tests/test_rolling_forecast_artifact.py tests/test_scientific_refresh.py tests/test_current_ce_artifact.py
```

The assembly check uses committed CE/ACS components and source receipts;
it does not rebuild raw microdata or download inputs. The scientific
[rebuild instructions](rolling-forecasts.md#rebuild-and-verify) describe
separate cached-source calculations.

An optional ASEC check requires an external Census CPS ASEC HDFStore:

```sh
SPM_CALCULATOR_ASEC_H5=/path/to/census_cps_2024.h5 python -m pytest -q tests/test_asec_parity.py
```

It compares household partitions rather than arbitrary SPM ID labels,
requires a 99% household match floor, and requires exact partition parity
when `PECOHAB` is present. Its threshold check deliberately uses the
archived pre-correction Census national series and the file's own
geographic factors. It validates that historical source, not current
canonical revised-BLS forecast amounts. Without the fixture, these tests
skip.
