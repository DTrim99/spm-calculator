# PolicyEngine integration

In the integration described here, PolicyEngine US uses the canonical
`SPMForecast` for SPM thresholds and housing portions by default. It keeps
taxes, benefits and SPM resources in the country model. The housing portion
affects the cap on counted housing assistance, so this measurement change can
also change resources.

These examples use `spm-calculator` **1.0.0**, published on
[PyPI](https://pypi.org/project/spm-calculator/1.0.0/). The country and
wrapper sides ship in their own packages: `policyengine-us` 2.0 and the
`policyengine` wrapper 6.0 are in progress, and the
[1.0 migration guide](migration.md) describes the coordinated pins. A released
country or wrapper version that predates them does not read this forecast.
These examples do not certify a population dataset.

## Use the provider directly

The provider accepts a verified forecast, scenario and location selection. This
example needs only the calculator; importing its provider does not import
PolicyEngine.

```python
from spm_calculator.policyengine_adapter import PolicyEngineSPMProvider
from spm_calculator.rolling_forecast import load_forecast

forecast = load_forecast()
provider = PolicyEngineSPMProvider(forecast, scenario="ce_trend")
result = provider.calculate_unit(
    year=2025,
    adults=2,
    children=2,
    tenure="renter",
    county_fips="01001",
)
assignment = result["provenance"]["county_assignment"]
area = result["provenance"]["geography"]
print(round(result["threshold"], 2))
print(assignment["area_id"], area["area_type"], area["status"])
print(provider.provenance()["forecast_sha256"])

# National geography is a deliberate selection.
national = PolicyEngineSPMProvider(
    forecast, scenario="zero_real", geography_kind="national"
)
national_result = national.calculate_unit(
    year=2026, adults=2, children=2, tenure="renter"
)
assert national_result["geography_status"] == "explicit_national"
print(round(national_result["threshold"], 2))
```

County is an assignment input, not an estimation unit. The provider resolves
five-character FIPS against the selected year's area menu using
`county_vintage="2020"`. The result retains the assignment and the area's
`area_type`, `official_published_area`, `status` and `diagnostics`. The `metro`
API kind also carries state nonmetro areas; inspect `area_type` for the actual
type. A fixed area selection uses `geography_kind="metro"` and an available
`geography_id` from `forecast.areas_for_year(year, scenario=...)`.

The bundled forecast covers 2022–2036. Its published national entries and
housing shares remain distinct from conditional CE/ACS forecasts and modeled
geography. Read [rolling forecasts](rolling-forecasts.md) and
[source validation](validation.md) before interpreting those statuses. There
is no CPI extrapolation, estimated-year opt-in or missing-location fallback.
Unknown years and locations fail.

## Run the country model

Install `spm-calculator==1.0.0` and the accompanying country source in an
isolated environment. The following example uses `policyengine_us.Simulation`,
which registers the calculator's variables before reading input records.
Omitting `spm` selects the installed forecast's default scenario and county
assignment.

```python
from policyengine_us import Simulation

year = 2025
people = {
    "parent": {
        "age": {year: 16},
        "is_spm_independent_minor_role": True,
    },
    "child": {
        "age": {year: 8},
        "is_spm_independent_minor_role": False,
    },
    "adult": {
        "age": {year: 35},
        "is_spm_independent_minor_role": False,
    },
    "dependent": {
        "age": {year: 17},
        "is_spm_independent_minor_role": False,
    },
}
members = list(people)
situation = {
    "people": people,
    "tax_units": {"tax": {"members": members}},
    "spm_units": {
        "minor_family": {
            "members": ["parent", "child"],
            "spm_unit_tenure_type": {year: "RENTER"},
        },
        "adult_family": {
            "members": ["adult", "dependent"],
            "spm_unit_tenure_type": {year: "RENTER"},
        },
    },
    "families": {"family": {"members": members}},
    "marital_units": {name: {"members": [name]} for name in members},
    "households": {
        "household": {
            "members": members,
            "county_fips": {year: "01001"},
        }
    },
}
simulation = Simulation(situation=situation)
assert simulation.spm_config["geography_kind"] == "county"
assert simulation.calculate("spm_measurement_adults", year).tolist() == [1, 1]
assert simulation.calculate("spm_measurement_children", year).tolist() == [1, 1]
print(simulation.calculate("spm_unit_spm_threshold", year).tolist())
print(simulation.calculate("spm_unit_spm_threshold_housing_portion", year).tolist())
print(simulation.spm_config)
assert str(year) in simulation.spm_provenance()["years"]

# A new simulation can select national geography explicitly.
national_simulation = Simulation(
    situation=situation,
    spm={"geography_kind": "national", "scenario": "zero_real"},
)
print(national_simulation.calculate("spm_unit_spm_threshold", year).tolist())
```

The four synthetic people form two native SPM units within one household.
Classification counts a person as an SPM adult when
`age >= 18 or (age >= 15 and is_spm_independent_minor_role)`. The 16-year-old
parent therefore counts as an adult; the dependent 17-year-old counts as a
child. This leaves the country's generic benefit-eligibility counts
`spm_unit_count_adults` and `spm_unit_count_children` separate. A supplied
primitive role takes precedence; when it is absent, the country derives it
from explicit `is_household_head` and `is_household_spouse` inputs. Neither
age ordering nor row order supplies these roles. The country raises
`SPM_COMPOSITION_REQUIRED` when a measured unit has no classified adult.

The country accepts exactly these `spm` settings:

| Setting | Meaning |
| --- | --- |
| `forecast_content_sha256` | Optional expected content digest of the installed artifact; mismatches fail. |
| `scenario` | Named artifact scenario; the bundled default is `ce_trend`, with `zero_real` as a sensitivity. |
| `geography_kind` | `county` by default, or explicit `metro` or `national`. |
| `geography_id` | Required for a fixed `metro` selection; otherwise omitted. |
| `county_vintage` | County assignment vintage; the bundled mapping uses `"2020"`. |
| `as_of` | Optional availability-date constraint applied to the artifact. |

The country does not accept an external artifact path. For reproducibility,
retain `simulation.spm_config` and `simulation.spm_provenance()` with the source
and package identities used for a run. The latter records evaluated years,
areas, the current content digest and runtime versions. Do not copy a stale
digest from a documentation example.

## Input and resource contracts

SPM measurement requires native membership, ages, source-backed roles, tenure,
and an available county or explicit area/national selection. State alone does
not identify an SPM area. `SPM_GEOGRAPHY_REQUIRED` also applies when a resource
output evaluates the housing cap, including units with zero housing assistance.
Consequently, a successful state-only tax calculation does not establish that
`household_net_income` or `marginal_tax_rate` can run with the same inputs.

The provider supplies final canonical amounts; the country casts each final
amount once to its storage dtype. In the validated country environment, stored
float32 amounts may differ from the calculator's float64 values. Housing-cap
arithmetic uses the raw canonical housing portion before the final storage
cast. PolicyEngine continues to calculate actual assistance, HUD total tenant
payment and all other resources.

`validate_policyengine_inputs` rejects formula-owned SPM amounts, the new
measurement counts, capped housing assistance and derived resource/poverty
outputs. The country also rejects those columns when loading datasets. Keep
observed Census values under separate report-only names. Neither validation
nor forecast selection reconstructs native SPM membership or changes weights.

## Wrapper and API status

The wrapper contract is `pe.us.calculate_household(spm=...)` with an
`SPMSelection` object or mapping containing the settings above. The wrapper
resolves those settings against an independently selected bundle artifact and
returns detached `provenance.spm_config` and `provenance.spm` receipts. It
requires a matching bundle configuration and country installation. That
contract belongs to the `policyengine` wrapper, whose 6.0 release is in
progress; see the [1.0 migration guide](migration.md). This guide does not
claim a live endpoint.

The executed country examples used a country build at version 1.824.7,
PolicyEngine Core 3.30.1 and Python 3.13.9. These synthetic household examples
establish an API contract, not population-data certification. A production
population release must independently preserve native membership and weights,
carry the source-backed independence primitive, and exclude saved formula
outputs from the model input contract. The separate
[Microcosm](microcosm-integration.md) and [Axiom](axiom-integration.md) adapters
do not migrate the country's tax/benefit rules into Axiom.
