# Quickstart

Install the package with Python 3.9 or newer:

```sh
pip install spm-calculator==1.1.0
```

With uv, `uv pip install spm-calculator==1.1.0`, or
`uv add spm-calculator==1.1.0` inside a uv project. To run the
examples against this checkout instead, use `python -m pip install -e .`.

The bundled artifact supports offline calculations without a Census API key.
The optional PolicyEngine, Microcosm and Axiom adapters ship inside this
package; each needs its own runtime installed separately.

## Published national values and shares

```python
from spm_calculator import SPMUnit, load_forecast

forecast = load_forecast()
entry = forecast.entry(2025)
for tenure, threshold in entry["thresholds"].items():
    print(tenure, threshold, entry["housing_shares"][tenure])

result = forecast.calculate_unit(
    SPMUnit("example", 2, 2, "renter", 2025, geography_kind="national")
)
print(round(result["threshold"], 2))  # 41700.56
print(result["national_status"])  # published
print(result["housing_share_status"])  # published_anchor
```

`entry["thresholds"]` gives annual USD for the two-adult/two-child reference
family. `entry["housing_shares"]` gives fractions, not dollar amounts. The
published 2025 renter housing share is `0.4336857704`; the national housing
portion is the family's scaled national threshold times that share. Source
identities and exact published threshold cells are described in
[validation](validation.md).

## Select a year, scenario and area

```python
from spm_calculator import SPMUnit, load_forecast

forecast = load_forecast()
year, scenario = 2026, "ce_trend"
areas = forecast.areas_for_year(year, scenario=scenario)
assignment = forecast.resolve_county(
    year, "06037", county_vintage="2020", scenario=scenario
)
print(areas[assignment["area_id"]])

unit = SPMUnit(
    "los-angeles-example", 2, 2, "renter", year,
    geography_kind=assignment["kind"],
    geography_id=assignment["area_id"],
    resources=40_000,
)
result = forecast.calculate_unit(unit, scenario=scenario)
print(result["threshold"], result["housing_portion"], result["is_in_poverty"])
print(result["provenance"]["geography"])
```

Resolve the county again when the year changes. Preserve `assignment` alongside
the result if its lookup provenance is needed; the scalar `calculate_unit`
method only receives the resolved area. The CLI and population adapters attach
county-assignment provenance themselves.

`metro` is the calculation kind for supported SPM estimation areas, including
state residual and modeled residual areas. Each record's `area_type`,
`official_published_area` and `status` describe its actual interpretation.
To choose an area directly, use `geography_kind="metro"` and an ID from
`areas_for_year`. To choose national, use `geography_kind="national"` and omit
the area ID. A state name alone is not an SPM location. Unknown counties, areas,
years and scenarios fail; there are no runtime fallback flags.

## Compare scenarios or units

```python
from spm_calculator import SPMUnit, load_forecast

forecast = load_forecast()
units = [
    SPMUnit("single-adult", 1, 0, "renter", 2036, geography_kind="national"),
    SPMUnit("reference", 2, 2, "renter", 2036, geography_kind="national"),
]
for scenario in ("ce_trend", "zero_real"):
    results = [forecast.calculate_unit(unit, scenario=scenario) for unit in units]
    print(scenario, [round(result["threshold"], 2) for result in results])
```

These scenarios condition future expenditures on their declared assumptions;
they do not supply forecast confidence intervals or population/resource
projections. See [rolling forecasts](rolling-forecasts.md).

`SPMUnit` counts are already classified SPM adults and children. Person-based
adapters use age ≥18, or age ≥15 with an explicit independent-minor role, inside
native SPM membership. An absent independence column may use explicit household
head/spouse roles. Row order and the oldest person's age are not role inputs.

## Retain a reviewed artifact

```python
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from spm_calculator import load_forecast

forecast = load_forecast()
# For later replay, retain the reviewed content digest separately from the file.
reviewed_digest = forecast.content_sha256
with TemporaryDirectory() as directory:
    path = Path(directory) / "forecast.json"
    path.write_text(json.dumps(forecast.to_dict()), encoding="utf-8")
    replay = load_forecast(path, expected_sha256=reviewed_digest)
    assert replay.content_sha256 == reviewed_digest
```

This round trip demonstrates integrity, not independent source authentication.
An `as_of` earlier than the artifact's `information_date` fails. The current
artifact uses a conservative retained-source cutoff; it is not a reconstructed
historical information set. See the [artifact contract](spm-releases.md).

## Command line

Use the installed command or replace `spm-calculator` with
`python -m spm_calculator.cli` when running directly from the checkout:

```sh
spm-calculator info
spm-calculator verify
spm-calculator calculate --year 2025 --adults 2 --children 2 --tenure renter --national
spm-calculator --scenario ce_trend calculate --year 2026 --adults 2 --children 2 --county 06037 --county-vintage 2020
spm-calculator --scenario zero_real calculate --year 2036 --adults 1 --children 0 --area 35620 --resources 30000
spm-calculator areas --year 2022
spm-calculator --scenario zero_real export --format csv
```

`calculate` requires exactly one of `--national`, `--area` or `--county`.
Put global options before the subcommand. `--forecast FILE` selects an artifact,
`--expect-sha256 DIGEST` verifies a separately retained content hash, and
`--as-of YYYY-MM-DD` enforces the information-date boundary. `export --format json`
exports the whole artifact, including both scenarios; CSV exports the selected
scenario's national thresholds and shares. CLI validation failures exit with
status 2.

Continue with [PolicyEngine](policyengine-release-integration.md),
[real Microcosm Frames](microcosm-integration.md), or
[actual Axiom core](axiom-integration.md).
