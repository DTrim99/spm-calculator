# Microcosm integration

`apply_forecast_to_frame` applies the verified canonical `SPMForecast` to an
actual `microcosm.frame.Frame`. Native person-to-SPM-unit links determine
membership, primitive ages and roles determine composition, and Microcosm owns
typed weights and weighted summaries. This uses calculator 1.0.0; it is not
certification of a population release.

The optional adapter needs the Microcosm frame and graph packages; the
standalone calculator does not import them. Validation used Microcosm revision
`923cec2e174c93ef0032de3415fbec87f716eea4`, `microcosm-frame` 0.1.0 and Python
3.14.4. To prepare a separate environment using that checked-out revision:

```sh
uv pip install /path/to/microcosm/packages/microcosm-graph \
  /path/to/microcosm/packages/microcosm-frame
uv pip install spm-calculator==1.0.0
```

## Build and measure a real Frame

This self-contained example creates three synthetic SPM units in two
households. Units 10 and 20 share a household but retain their separate native
membership. Unit 20 contains an independent 16-year-old parent and an
8-year-old child. The dependent 16-year-old in unit 30 counts as a child.

```python
import numpy as np
import pandas as pd
from microcosm.frame import EntitySchema, Frame, WeightKind, Weights
from spm_calculator.microcosm_adapter import (
    apply_forecast_to_frame,
    summarize_spm_units,
)
from spm_calculator.rolling_forecast import load_forecast

frame = Frame(
    {
        "person": pd.DataFrame(
            {
                "person_id": [1, 2, 3, 4, 5, 6, 7],
                "person_household_id": [1, 1, 1, 2, 2, 2, 2],
                "person_spm_unit_id": [10, 20, 20, 30, 30, 30, 30],
                "age": [30, 16, 8, 38, 35, 16, 5],
                "is_spm_independent_minor_role": [
                    False, True, False, False, False, False, False
                ],
            }
        ),
        "household": pd.DataFrame({"household_id": [1, 2]}),
        "spm_unit": pd.DataFrame(
            {
                "spm_unit_id": [10, 20, 30],
                "spm_tenure": [
                    "renter", "owner_with_mortgage", "owner_without_mortgage"
                ],
                "county_fips": ["01001", "01001", "02013"],
                "resources": [0.0, 1_000_000.0, 1_000_000.0],
            }
        ),
    },
    EntitySchema(group_entities=("household", "spm_unit")),
    {"household": Weights([100, 200], WeightKind.DESIGN)},
    metadata={"source": "Synthetic integration example"},
)
forecast = load_forecast()
source_weights = frame.weights_for("household")
source_values = source_weights.values.copy()
result = apply_forecast_to_frame(
    frame,
    forecast,
    year=2025,
    scenario="ce_trend",
    unit_entity="spm_unit",
    weight_entity="household",
    membership_provenance="Synthetic native person_spm_unit_id links",
    county_vintage="2020",
    columns={
        "tenure": "spm_tenure",
        "county_fips": "county_fips",
        "resources": "resources",
    },
)
summary = summarize_spm_units(
    result, unit_entity="spm_unit", weight_entity="household"
)
print(result.table("spm_unit")[["spm_unit_id", "spm_forecast_threshold"]])
print(summary)
assert summary["poor_units"] == 100
assert summary["unit_poverty_rate"] == 0.25
assert result.weights_for("household") is source_weights
np.testing.assert_array_equal(source_weights.values, source_values)
assert "spm_forecast_threshold" not in frame.table("spm_unit")

receipt = result.metadata["spm_forecast_application"]
assert receipt["forecast_sha256"] == forecast.content_sha256
for unit_result in receipt["unit_results"]:
    geography = unit_result["provenance"]["geography"]
    print(
        unit_result["unit_id"],
        geography["area_type"],
        geography["official_published_area"],
        geography["status"],
    )
```

Microcosm resolves unit weights from the declared household weight source.
Its `wmean` and `wsum` operations calculate every weighted summary. In this
fixture, the three units carry weights 100, 100 and 200; only the first unit
is poor. The resulting 0.25 is **SPM-unit poverty**, with units in the
denominator. It is not a person-level Census poverty rate.

The adapter returns a new Frame with `spm_forecast_threshold`,
`spm_forecast_housing_portion`, `spm_forecast_is_in_poverty`, artifact/scenario
identities and status columns on the SPM-unit table. Source tables, person
links, weight objects and values, strata, and mass-change history remain
unchanged. Immutable metadata under `spm_forecast_application` retains input
and weight digests, selected-year county assignments and full unit results.
If resources are omitted, thresholds remain available and the summary's
poverty fields are `None`.

## Choose the year and geography

The default geography mode reads the unit's `county_fips`, then resolves it
through `forecast.resolve_county(year, ..., county_vintage="2020")`. County is
an assignment input, not an estimation unit. Each result retains the assigned
area's actual type, official-publication flag, status and diagnostics. The
`metro` API kind includes state nonmetro areas; the receipt's `area_type`
distinguishes them.

Use `forecast.areas_for_year(year, scenario=...)` to inspect area availability
in a selected year. Do not reuse a current county assignment for every
historical year. The artifact covers 2022–2036 and exposes `ce_trend` and
`zero_real` scenarios without an estimated-year opt-in. See
[rolling forecasts](rolling-forecasts.md) for the distinction between published
national inputs, modeled geography and conditional forward estimates.

For an explicit national calculation, continue from the Frame above and omit
county from the column mapping:

```python
national_result = apply_forecast_to_frame(
    frame,
    forecast,
    year=2026,
    scenario="zero_real",
    unit_entity="spm_unit",
    weight_entity="household",
    membership_provenance="Synthetic native person_spm_unit_id links",
    geography_kind="national",
    columns={"tenure": "spm_tenure", "resources": "resources"},
)
assert (
    national_result.table("spm_unit")["spm_forecast_geography_status"]
    == "explicit_national"
).all()
print(national_result.table("spm_unit")["spm_forecast_threshold"].tolist())
```

A fixed area uses `geography_kind="metro"` and `geography_id=<available area
id>`, with the same tenure/resources mapping. For different explicit areas by
unit, omit the fixed `geography_id` argument and include
`"geography_id": "your_area_column"` in `columns`. The adapter accepts no
state-only geography, congressional district, raw geographic factor or silent
national fallback.

## Composition and input errors

The adapter classifies each person by
`age >= 18 or (age >= 15 and is_spm_independent_minor_role)`, within existing
SPM membership. It ignores precomputed adult/child count columns. If the
primitive role column is absent, callers must supply explicit Boolean
`is_household_head` and `is_household_spouse` columns. A present primitive
column takes precedence and must contain valid Boolean values. Missing roles
do not become false, and age/row ordering never identifies a head or spouse.
Use `person_columns` to map `age`, `independent_minor_role`, `head` or `spouse`
to alternative source column names.

| Invalid input | Error code |
| --- | --- |
| Missing or invalid composition primitives, or no classified adult | `SPM_COMPOSITION_REQUIRED` |
| Missing county or area identifier | `SPM_GEOGRAPHY_REQUIRED` |
| Unavailable county or area | `SPM_GEOGRAPHY_UNAVAILABLE` |
| Year outside the artifact | `SPM_YEAR_UNAVAILABLE` |
| Unknown scenario | `SPM_SCENARIO_UNAVAILABLE` |

These failures raise `SPMInputError` where shown; Frame/schema and weight-source
contract failures can raise `ValueError` or `TypeError`. The adapter
revalidates the source Frame before using it. Ambiguous weight inheritance or
unequal inherited member weights fail instead of being averaged.

## Validation scope

Run the real Frame checks in the environment containing the optional packages:

```sh
python -m pytest tests/test_microcosm_adapter.py -q
```

The checks cover native membership, both scenarios and all artifact years,
minor roles, year-specific geography, typed weights and source immutability.
Reweighting changes weighted summaries but not individual thresholds. The
adapter neither constructs SPM resources nor estimates CE thresholds; CE
consumer units and interview weights belong to the separate statistical
pipeline. Synthetic interoperability checks do not certify a population
release or establish that Microcosm's quantiles reproduce BLS estimation.

The [Axiom integration](axiom-integration.md) uses the same real Frame through
a source-bound bridge to the actual relation-capable core runtime. The dense
Microcosm `AxiomEngine` does not support the required cross-entity relations
and dated derived rules. That bridge's declared composition domain and decimal
poverty-boundary limits apply separately; this guide makes no dense-engine
parity or complete PolicyEngine-to-Axiom migration claim.
