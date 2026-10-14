# spm-calculator

Calculate Supplemental Poverty Measure (SPM) thresholds with published national
inputs and conditional CE/ACS rolling forecasts. The package works offline for
standalone calculations and supplies the same forecast artifact to optional
PolicyEngine, Microcosm Frame and actual Axiom core integrations.

Install the package from
[PyPI](https://pypi.org/project/spm-calculator/). The calculator runs at
[policyengine.org/us/spm-calculator](https://policyengine.org/us/spm-calculator),
the documentation is at
[policyengine-docs.vercel.app/spm-calculator](https://policyengine-docs.vercel.app/spm-calculator/),
and the companion paper is at
[spm-threshold-paper.vercel.app](https://spm-threshold-paper.vercel.app/).

PolicyEngine integrations require coordinated dependency pins; read
the [migration guide](https://github.com/PolicyEngine/spm-calculator/blob/main/docs/migration.md) before upgrading an existing environment.

## Published 2025 inputs

The national reference family has two SPM adults and two children. The bundled
2025 thresholds and tenure-specific housing shares come from the BLS workbooks:

| Tenure | Annual national threshold, USD | Housing share |
| --- | ---: | ---: |
| Owner with mortgage | 41,322.707394 | 0.4285074824 |
| Owner without mortgage | 34,325.997720 | 0.3120194707 |
| Renter | 41,700.555713 | 0.4336857704 |

The [BLS housing-share workbook](https://github.com/PolicyEngine/spm-calculator/blob/main/spm_calculator/data/current/bls_spm_shares.xlsx)
supplies the shelter-plus-utilities fractions. The [published-cell receipt](https://github.com/PolicyEngine/spm-calculator/blob/main/spm_calculator/data/current/bls_published_cell_receipt.json)
links threshold cells to the source workbooks and rounded BLS page values.
The [source and validation guide](https://github.com/PolicyEngine/spm-calculator/blob/main/docs/validation.md) distinguishes these
published values from replicated or projected amounts. A published national
base does not make a local 2025 estimate an official Census threshold: the
selected area's rent input can be modeled.

## Install

Python 3.9 or newer:

```sh
pip install spm-calculator==1.0.0.post1
```

With uv, `uv pip install spm-calculator==1.0.0.post1`, or
`uv add spm-calculator==1.0.0.post1` inside a uv project. To work on the
package itself, install this checkout instead with
`python -m pip install -e .`.

A calculation needs no Census API key or source download. Optional integrations
require their own runtime installations; see the linked guides below.

## Calculate a threshold

```python
from spm_calculator import SPMUnit, load_forecast

forecast = load_forecast()
result = forecast.calculate_unit(
    SPMUnit(
        unit_id="example-family",
        num_adults=2,
        num_children=2,
        tenure="renter",
        year=2025,
        geography_kind="national",
        resources=40_000,
    )
)
print(f"Threshold: ${result['threshold']:,.2f}")  # $41,700.56
print(result["is_in_poverty"])  # True
print(forecast.forecast_id, forecast.content_sha256)
```

Here `resources` means already measured annual SPM resources, not gross income.
Omit it when only a threshold is needed. `SPMUnit` accepts already classified
counts. In person-based integrations, an adult is a person aged at least 18,
or aged at least 15 with an explicit SPM independence role. Native SPM membership
and source roles determine those counts; household size alone does not.

For a county, resolve its assignment for the target year, then calculate with
that SPM estimation area:

```python
from spm_calculator import SPMUnit, load_forecast

forecast = load_forecast()
assignment = forecast.resolve_county(
    2026, "06037", county_vintage="2020", scenario="ce_trend"
)
result = forecast.calculate_unit(
    SPMUnit(
        "example-family", 2, 2, "renter", 2026,
        geography_kind=assignment["kind"],
        geography_id=assignment["area_id"],
    ),
    scenario="ce_trend",
)
print(result["threshold"], result["national_status"])
print(result["provenance"]["geography"])
```

County is an assignment input, not an estimation unit. Use
`forecast.areas_for_year(year, scenario=...)` to list that year's supported
areas and their statuses. `metro` is the API kind for named MSAs, state residual
metro/nonmetro areas and explicitly modeled residual areas; inspect `area_type`
and `official_published_area` rather than inferring official status from the
kind. National calculations are an explicit location choice. Unknown locations,
years and scenarios raise errors.

## Conditional 2026–2036 forecasts

The default schema-2 artifact covers 2022–2036. It preserves published national
values through 2025 and projects 2026–2036 using moving five-year Consumer
Expenditure Survey and American Community Survey windows. `ce_trend` is the
default real-spending scenario; `zero_real` provides zero real-spending growth.
The artifact records price assumptions, their source vintage, housing shares,
rent indices, source windows and diagnostics separately for each selected year.

These are conditional research estimates. Public-use rent allocation,
unresolved CE sample policies, fixed future donors and weights, and unestimated
forecast uncertainty limit interpretation. Relative rent indices stabilize from
2029 under the baseline donor and price assumptions even as windows advance;
this is not evidence of persistent local growth differences. Read the
[rolling forecast methods and validation limits](https://github.com/PolicyEngine/spm-calculator/blob/main/docs/rolling-forecasts.md)
before comparing scenarios or historical geography series breaks.

## Command line

After installation:

```sh
spm-calculator info
spm-calculator verify
spm-calculator calculate --year 2025 --adults 2 --children 2 --tenure renter --national
spm-calculator --scenario ce_trend calculate --year 2026 --adults 2 --children 2 --county 06037
spm-calculator areas --year 2035
spm-calculator --scenario zero_real export --format csv
```

Global options (`--forecast`, `--expect-sha256`, `--as-of`, `--scenario`) precede
the subcommand. Retain a reviewed content digest and supply it on replay; the
reader does not obtain a newer artifact over the network. See the
[quickstart](https://github.com/PolicyEngine/spm-calculator/blob/main/docs/quickstart.md) and [artifact contract](https://github.com/PolicyEngine/spm-calculator/blob/main/docs/spm-releases.md).

## Integrations and app

- [PolicyEngine](https://github.com/PolicyEngine/spm-calculator/blob/main/docs/policyengine-release-integration.md): in this
  integration the country model reads forecast configuration by default and
  retains its tax, benefit and resource formulas. The country and wrapper
  sides ship in their own packages: `policyengine-us` 2.0 and the
  `policyengine` wrapper 6.0 are in progress. Read the
  [1.0 migration guide](https://github.com/PolicyEngine/spm-calculator/blob/main/docs/migration.md) before pinning them.
- [Microcosm Frame](https://github.com/PolicyEngine/spm-calculator/blob/main/docs/microcosm-integration.md): preserve native membership
  and typed weights, attach canonical results and summarize with Frame operations.
- [Axiom core](https://github.com/PolicyEngine/spm-calculator/blob/main/docs/axiom-integration.md): execute person classification,
  native unit counts, bounded canonical scale lookup and threshold/housing/poverty
  arithmetic in real core. The dense Microcosm AxiomEngine does not support this
  bridge; exact decimal poverty boundaries can differ from Python float results.

The browser app is live at
[policyengine.org/us/spm-calculator](https://policyengine.org/us/spm-calculator).
Run it locally from `web` with `bun install --frozen-lockfile` and
`bun run dev`; its export consumes the canonical artifact.

## Sources and research history

- [Validation](https://github.com/PolicyEngine/spm-calculator/blob/main/docs/validation.md): published BLS cells and shares, Census
  geography anchors, forecast comparisons and reproducible checks.
- [Methodology](https://github.com/PolicyEngine/spm-calculator/blob/main/docs/methodology.md): threshold, housing and equivalence formulas.
- [Current CE replication experiment](https://github.com/PolicyEngine/spm-calculator/blob/main/docs/current-ce-replication.md): source
  policies, measured replication differences and unresolved approximations.
- [2026 BLS correction and frozen experiments](https://github.com/PolicyEngine/spm-calculator/blob/main/docs/bls-2026-correction.md):
  historical publication vintages and the immutable 2025 forecast commitment.
- [API reference](https://github.com/PolicyEngine/spm-calculator/blob/main/docs/api.md): current calculation and membership interfaces.
- [Companion paper](https://spm-threshold-paper.vercel.app/): calculating and
  projecting Supplemental Poverty Measure thresholds.

[MIT license](https://github.com/PolicyEngine/spm-calculator/blob/main/LICENSE).
