# Forecast and unit API

`SPMForecast` calculates thresholds for a selected year, area and household.
The reader loads a verified artifact without downloading data or importing an
optional model runtime. `SPMUnit` is the scalar measurement input; it does not
construct household membership or SPM resources.

## Load and inspect

```python
from spm_calculator import SPMForecast, load_forecast

forecast = load_forecast()
assert isinstance(forecast, SPMForecast)
print(forecast.forecast_id, forecast.content_sha256)
print(forecast.years)  # 2022 through 2036
print(forecast.default_scenario)  # ce_trend
entry = forecast.entry(2025)
print(entry["thresholds"]["renter"], entry["housing_shares"]["renter"])
```

`load_forecast(path=None, *, expected_sha256=None, as_of=None)` reads a local
JSON file or the bundled default artifact. `SPMForecast.from_dict(document,
expected_sha256=..., as_of=...)` verifies an in-memory document. `to_dict()`
returns a detached JSON-compatible dictionary; mutating it does not alter the
forecast. Retain an independently reviewed content digest for later replay.

The public read-only properties are `forecast_id`, `content_sha256`, `years`
and `default_scenario`. Methods accept `scenario=None` for the artifact's
default and optional `as_of`:

| Method | Result |
| --- | --- |
| `entry(year, scenario=..., as_of=...)` | Selected-year national thresholds, housing shares, rent indices, statuses and window diagnostics. |
| `areas_for_year(year, scenario=..., as_of=...)` | Available area IDs mapped to names, types and selected-year metadata. |
| `resolve_county(year, county_fips, county_vintage="2020", scenario=..., as_of=...)` | County assignment with `kind`, `area_id` and boundary/source provenance. |
| `geography_factor(year, tenure, kind="national", geoid=None, scenario=..., as_of=...)` | Factor and selected geography's metadata. |
| `calculate_unit(unit, scenario=..., as_of=...)` | Annual threshold, housing portion, optional poverty judgment and provenance. |

Unknown years, scenarios or areas raise errors. `as_of` cannot precede the
artifact's information date. See [geography](geography.md) and the
[schema-2 artifact contract](../spm-releases.md) for the data fields.

## SPMUnit inputs

| Field | Type and interpretation |
| --- | --- |
| `unit_id` | Nonempty string identifying an existing measurement unit. |
| `num_adults` | Integer ≥1, already classified SPM adults. |
| `num_children` | Integer ≥0, already classified SPM children. |
| `tenure` | `renter`, `owner_with_mortgage` or `owner_without_mortgage`. |
| `year` | Integer target year, present in the selected forecast. |
| `resources` | Optional finite annual SPM resource amount; omission leaves poverty unknown. |
| `geography_kind` | Use explicit `national` or `metro` with the current forecast. |
| `geography_id` | Area ID for `metro`; omit for `national`. |

`SPMUnit` also defines `geographic_adjustment` and `geography_vintage` for an
explicit researcher-supplied whole-threshold factor. That factor is not a rent
index or county estimate and must not be combined with a named geography.
The Frame/provider/CLI workflows use selected-year named areas or explicit
national inputs. The shared unit type contains other archived geography kinds;
`SPMForecast` does not offer those as current named estimation paths.

```python
from spm_calculator import SPMUnit, load_forecast

forecast = load_forecast()
unit = SPMUnit(
    unit_id="synthetic-reference",
    num_adults=2,
    num_children=2,
    tenure="renter",
    year=2025,
    resources=40_000,
    geography_kind="national",
)
result = forecast.calculate_unit(unit)
assert result["is_in_poverty"] is True
print(result["threshold"], result["housing_portion"])
```

Poverty uses strict `resources < threshold` on the unrounded amount. Resources
are already measured SPM resources, not earnings. For person-based adapters,
measurement adults are aged ≥18, or ≥15 with an explicit independent-minor role.
Classification belongs inside the existing SPM membership; it differs from
generic benefit-eligibility age counts.

## Calculation results

| Group | Actual result keys |
| --- | --- |
| Identity | `unit_id`, `year`, `tenure`, `forecast_id`, `forecast_sha256`, `base_release_sha256`, `scenario`, `methodology_id` |
| Component statuses | `national_status`, `geography_status`, `bundled_geography_status`, `housing_share_status` |
| Amounts and factors | `reference_threshold`, `equivalence_factor`, `geographic_factor`, `unadjusted_threshold`, `threshold`, `housing_share`, `housing_portion` |
| Resource comparison | `resources`, `is_in_poverty` (`None` when resources are omitted) |
| Provenance | `information_date`, `geography`, `ce_window`, `acs_window`, `uncertainty` inside `provenance` |

A county assignment does not enter `calculate_unit` directly. Retain the
`resolve_county` result separately or use the CLI/adapter that attaches it.
Axiom results additionally retain exact native decimal values and native
receipts; see its [numeric limits](../axiom-integration.md).

## Published-source access

For published national lookup without a forecast scenario:

```python
from spm_calculator import get_published_thresholds
from spm_calculator.published_thresholds import get_threshold_with_metadata

print(get_published_thresholds(2025)["renter"])  # 41700.555713
print(get_threshold_with_metadata(2025)["source"])  # published
```

This source helper supports only years in the selected published series.
It does not project an absent year. `get_tenure_shares` in the published-source
module returns tenure population shares, not shelter/utilities housing shares;
use the forecast entry's `housing_shares` for threshold calculations.
