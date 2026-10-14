# Axiom integration

The optional `spm_calculator.axiom_adapter` sends an actual Microcosm `Frame`
to the Axiom core Rust runtime. Core classifies people, counts members of each
native SPM unit, looks up dated parameters, and computes the adjusted threshold,
housing portion and poverty judgment. The bridge uses the same verified
schema-2 `SPMForecast` as the [standalone calculator](quickstart.md).

This is integration evidence for calculator **1.0**. It is a
Frame-to-core bridge, not a
complete PolicyEngine-to-Axiom migration. The dense Microcosm `AxiomEngine`
does not support this combination of cross-entity relations and dated derived
rules and is not the execution path used here.

## Runtime and dependencies

Run the examples from the calculator repository root, with pandas and the
optional `microcosm-frame` package installed in the same Python environment.
The verified environment is the `microcosm-spm-validation` checkout at
`923cec2e174c93ef0032de3415fbec87f716eea4`, with `microcosm-frame 0.1.0`,
Python `3.14.4`, pandas `3.0.5` and NumPy `2.5.3`.

The verified real runtime is [axiom-core at 8ac3a54](https://github.com/TheAxiomFoundation/axiom-core/tree/8ac3a54f60fa3737166ff0c986d9660f34a25435),
using engine revision `d142c645917817cf590e036fb99f99b2d4780e1a`, engine
version `0.2.2`, artifact format `2`. The tested executable SHA-256 is
`a7b48c9aef987018776d1ee921aad985a458b2c5798d32b8369a30ea69eb73ac`.
These describe the tested runtime, not a claim about the newest release.

Set `AXIOM_CORE_BIN` to an actual compatible executable, or supply
`binary="/path/to/axiom-core"` to `AxiomSPMAdapter`. The local validation paths
used for the examples are:

```sh
export AXIOM_CORE_BIN=/Users/maxghenis/TheAxiomFoundation/axiom-core-spm-validation/target/debug/axiom-core
export SPM_VALIDATION_PYTHON=/Users/maxghenis/PolicyEngine/microcosm-spm-validation/.venv/bin/python
"$AXIOM_CORE_BIN" capabilities
```

An unavailable or invalid executable raises `AxiomRuntimeError`. No Python
mock evaluator or alternative runtime path is substituted.

## Calculate on a real Frame

The following Python example is self-contained after setting the executable.
It has two SPM units in one household. The first contains an independent
16-year-old and a child. Its source household design weight remains a native
`Weights` object; the adapter does not calibrate or mutate it.

```python
import os

import pandas as pd
from microcosm.frame import EntitySchema, Frame, WeightKind, Weights

from spm_calculator.axiom_adapter import AxiomSPMAdapter
from spm_calculator.microcosm_adapter import summarize_spm_units
from spm_calculator.rolling_forecast import load_forecast

forecast = load_forecast()
frame = Frame(
    {
        "person": pd.DataFrame({
            "person_id": [101, 102, 103, 104],
            "person_household_id": [1, 1, 1, 1],
            "person_spm_unit_id": [10, 10, 20, 20],
            "age": [16, 3, 36, 40],
            "is_spm_independent_minor_role": [True, False, False, False],
        }),
        "household": pd.DataFrame({"household_id": [1]}),
        "spm_unit": pd.DataFrame({
            "spm_unit_id": [10, 20],
            "spm_tenure": ["renter", "owner_with_mortgage"],
            "county_fips": ["06037", "06037"],
            "resources": [0.0, 100_000.0],
        }),
    },
    EntitySchema(group_entities=("household", "spm_unit")),
    {"household": Weights([100.0], WeightKind.DESIGN)},
    metadata={"source": "Public synthetic example; native SPM unit links"},
)
selection = dict(
    year=2025,
    scenario="ce_trend",
    unit_entity="spm_unit",
    weight_entity="household",
    membership_provenance="Synthetic source-native person_spm_unit_id links",
    county_vintage="2020",
    columns={
        "tenure": "spm_tenure",
        "county_fips": "county_fips",
        "resources": "resources",
    },
)
adapter = AxiomSPMAdapter(forecast, binary=os.environ["AXIOM_CORE_BIN"])
execution = adapter.calculate(frame, **selection)
assert [(r["num_adults"], r["num_children"]) for r in execution["results"]] == [
    (1, 1), (2, 0),
]
assert execution["receipt"]["result"]["results"][0]["trace"]
print([(r["unit_id"], r["threshold"], r["is_in_poverty"])
       for r in execution["results"]])

# This call also executes real core, then attaches its outputs to a new Frame.
measured = adapter.apply_to_frame(frame, **selection)
assert measured.weights_for("household") is frame.weights_for("household")
print(summarize_spm_units(
    measured, unit_entity="spm_unit", weight_entity="household",
))
```

`summarize_spm_units` uses Microcosm `wmean`, `wsum` and `resolve_weights`.
Its denominator is SPM units, so the result is a unit poverty rate. It is not a
person poverty rate or evidence of population calibration.

Resources are caller-measured annual SPM resources. The bridge does not compute
taxes, benefits or the resource aggregate. Omit the `"resources"` column mapping
to leave poverty unavailable; omission never means zero. See
[Microcosm integration](microcosm-integration.md) for the shared Frame contract.

Membership comes from `person_spm_unit_id`, not a household assumption or row
order. The primitive adult rule is exactly
`age >= 18 or (age >= 15 and is_spm_independent_minor_role)`. If the independent
minor role column is absent, explicit Boolean `is_household_head` and
`is_household_spouse` inputs are required. A present primitive role takes
precedence; missing values are rejected. No oldest-person inference is made.

County FIPS is an assignment input, not an estimation unit. For each selected
year and scenario, the adapter uses `resolve_county(..., county_vintage="2020")`
and the corresponding `areas_for_year(...)` menu. Unit results retain the
assigned area's `area_type`, status, `official_published_area` and diagnostics,
as well as the separate county assignment. A modeled residual area does not
become an official published area by resolving a county to it.

For explicit national calculations, replace the geography selection and remove
the county mapping:

```python
national_selection = {
    **selection,
    "geography_kind": "national",
    "columns": {"tenure": "spm_tenure", "resources": "resources"},
}
national_execution = adapter.calculate(frame, **national_selection)
assert all(r["geographic_factor"] == 1.0 for r in national_execution["results"])
```

Explicit metro selection is also supported through `geography_kind="metro"`
and `geography_id` from the selected year's area menu. Unknown years, counties
or scenarios and units without measurement adults fail explicitly; there is no
national fallback or legacy release opt-in path.

## Source export and replay

`export_build_spec(forecast, years=..., scenario=...)` works without installing
core. It exports actual dated RuleSpec YAML inside an `axiom/build-spec/v0`
JSON source closure. Omitted `years` selects the forecast's full 2022–2036
coverage; omitted `scenario` selects `forecast.default_scenario`.

The source binds the current forecast content hash, source and component
identities, scenario, years and declared composition domain. A provenance-only
artifact refresh needs a new export and bundle even if threshold values stay
the same. Do not pin an old forecast hash merely to keep an example working.

Continuing the Python example:

```python
import json
import tempfile
from pathlib import Path

from spm_calculator.axiom_adapter import export_build_spec

with tempfile.TemporaryDirectory(prefix="spm-core-example-") as directory:
    output = Path(directory)
    spec = export_build_spec(forecast, years=[2025], scenario="ce_trend")
    (output / "build-spec.json").write_text(json.dumps(spec, indent=2))
    bundle = output / "spm.bundle.json"
    identity = adapter.build_bundle(bundle, years=[2025], scenario="ce_trend")
    # Keep this digest separately when retaining a bundle for later replay.
    expected_digest = identity["bundle_sha256"]
    replay = adapter.execute_bundle(bundle, expected_digest, frame, **selection)
    assert replay["results"] == execution["results"]
```

The native request contains ages, Boolean independent-minor roles, person-to-unit
relations, tenure and year-specific area indices, and optional resources. It
contains no precomputed composition counts, equivalence factor, geography
factor, housing amount or final threshold.

Core executes classification and relation counts, then national threshold,
housing share, rent-index and equivalence-table lookups. It calculates
`1 + housing_share * (rent_index - 1)`, the scaled threshold, housing portion
and strict poverty judgment. Fractional power is unsupported: the canonical
calculator exports its equivalence factors as a bounded lookup table. The
default declared domain is **1–10 adults and 0–10 children** (110 combinations).
`max_adults` and `max_children` can explicitly change the exported domain.
Out-of-domain requests are rejected, and native guards use a missing lookup
key instead of a default factor.

Core verifies the expected bundle digest, rebuilt artifact and executable
identity. The adapter separately regenerates and checks the complete source
closure against the selected forecast, scenario and composition domain. A
bundle is bound to its original executable bytes; rebuild from the portable
source on another executable or platform. Core calls this
`development_unsigned`: hashes establish integrity, not authentication,
signed admission or legal validity. No browser/WASM receipt parity is claimed.

Each request uses an inclusive January 1–December 31 custom period. The adapter
checks the forecast's information date; it does not send core's unsupported
knowledge-time selection fields. Native request and response structures,
including full explanation traces, remain available in the execution result.

## Decimal and capability limits

Native core uses decimal arithmetic; the canonical Python calculator uses
binary floating point. Amount comparisons use `abs_tol=1e-8` and
`rel_tol=1e-12`. Exact poverty classification at a binary-float boundary is
**not guaranteed**. Poverty is always core's exact `resources < threshold`
judgment, with no rounding or Python replacement.

The verified 2025 county `06037`, owner-with-mortgage, one-adult example has
canonical threshold/resources `23799.950378588343` and exact native threshold
`23799.950378588343650804047128`. Core returns poverty because the exact native
threshold is larger, although both amounts display as the same Python float.
Results retain `native_decimal_values` and `provenance.numeric_semantics`; the
complete receipt contains the original decimal values and judgment. The
validation script records this probe against whatever verified forecast it
loads, rather than requiring a historical artifact hash.

Core also has finite decimal range and precision. Inputs such as `1e30` or
`1e-30` can produce native `invalid_dataset` failures even though Python regards
them as finite. `AxiomRuntimeError.response` and `.returncode` preserve the
structured failure. Representable small inputs are transported as plain decimal
literals. Strict below/equal/above comparisons are separately verified at the
exact national reference threshold.

This integration does not establish CE estimation accuracy, resource-model
accuracy, population representativeness, legal validity or full PolicyEngine
parity. The [source validation](validation.md) and
[rolling forecast assumptions](rolling-forecasts.md) describe the separate
scientific evidence and its limits. In particular, published 2025 national
thresholds and housing shares do not make conditional 2026–2036 forecasts
published official estimates.

## Run and retain validation

From the repository root and configured environment:

```sh
"$SPM_VALIDATION_PYTHON" scripts/validate_axiom_integration.py \
  --binary "$AXIOM_CORE_BIN" \
  --core-checkout /Users/maxghenis/TheAxiomFoundation/axiom-core-spm-validation \
  --output-dir /tmp/spm-axiom-evidence-new
```

`--core-checkout` is optional. `--output-dir` is required and must not exist;
existing evidence is never overwritten. Failure exits nonzero and, after
creating the directory, records `failure.json` with any structured native
response. Every policy evaluation in the script executes real core; canonical
calculations are comparison oracles only.

The script retains full-year source specs and bundles for both scenarios,
full native build identities and capability responses, actual source/binary
hashes, synthetic Frames, requests, complete receipts, unit results and input
provenance. `validation.json` records endpoint probes for 2022, 2025 and 2036,
all tenures, native multi-unit membership, independent minors, explicit source
roles, unchanged typed weights and native traces. It also retains national and
decimal boundary probes, input error parity, rejection outside the declared
composition domain, and a structured native decimal-range failure.

For the broader adapter acceptance suite:

```sh
"$SPM_VALIDATION_PYTHON" -m pytest \
  tests/test_microcosm_adapter.py tests/test_axiom_adapter.py -q
```

These tests require the optional Frame dependency. Native tests skip only when
no binary is configured; a configured invalid binary fails. Synthetic replay
receipts are public software fixtures. Receipts for real user Frames may
contain private data and are not public source artifacts.
