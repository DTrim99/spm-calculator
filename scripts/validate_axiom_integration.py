"""Retain real Frame → Axiom core evidence from public synthetic fixtures.

Run from a source checkout with pandas and microcosm-frame installed. The
canonical SPMForecast is a comparison oracle, never a native evaluator. Output
is unsigned development evidence for the local 1.0 candidate, not publication.
"""

import argparse
import copy
import hashlib
import json
import math
import subprocess
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import pandas as pd
from microcosm.frame import EntitySchema, Frame, WeightKind, Weights

from spm_calculator.axiom_adapter import (
    MODULE_ID,
    AxiomRuntimeError,
    AxiomSPMAdapter,
    export_build_spec,
    make_request,
)
from spm_calculator.errors import SPMInputError
from spm_calculator.microcosm_adapter import (
    apply_forecast_to_frame,
    prepare_frame_inputs,
    summarize_spm_units,
)
from spm_calculator.rolling_forecast import load_forecast


def write(path, data):
    def plain(value):
        if isinstance(value, Mapping):
            return {key: plain(child) for key, child in value.items()}
        if isinstance(value, (list, tuple)):
            return [plain(child) for child in value]
        return value

    with path.open("x", encoding="utf-8") as output:
        json.dump(
            plain(data), output, indent=2, sort_keys=True, allow_nan=False
        )
        output.write("\n")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args, cwd=REPO):
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def synthetic_frame(compositions=None, *, resources=None, tenures=None):
    """Real Frame with source-native unit links; the first two share a home."""
    compositions = compositions or [
        [(16, True), (3, False)],
        [(36, False), (40, False)],
        [(18, False)],
    ]
    count = len(compositions)
    household_ids = [1 if index < 2 else index for index in range(count)]
    persons = []
    for index, composition in enumerate(compositions):
        for age, role in composition:
            persons.append(
                {
                    "person_id": len(persons) + 101,
                    "person_household_id": household_ids[index],
                    "person_spm_unit_id": (index + 1) * 10,
                    "age": age,
                    "is_spm_independent_minor_role": role,
                }
            )
    units = {
        "spm_unit_id": [(index + 1) * 10 for index in range(count)],
        "spm_tenure": tenures or ["renter"] * count,
        "county_fips": [
            ("06037", "06037", "06003")[index % 3] for index in range(count)
        ],
    }
    if resources is not None:
        units["resources"] = resources
    unique_households = sorted(set(household_ids))
    return Frame(
        {
            "person": pd.DataFrame(persons),
            "household": pd.DataFrame({"household_id": unique_households}),
            "spm_unit": pd.DataFrame(units),
        },
        EntitySchema(group_entities=("household", "spm_unit")),
        {
            "household": Weights(
                [100 * (index + 1) for index in range(len(unique_households))],
                WeightKind.DESIGN,
            )
        },
        metadata={
            "source": "Public synthetic software fixture; native SPM membership"
        },
    )


def selection(
    year=2025, scenario="ce_trend", *, national=False, resources=True
):
    columns = {"tenure": "spm_tenure"}
    if not national:
        columns["county_fips"] = "county_fips"
    if resources:
        columns["resources"] = "resources"
    return {
        "year": year,
        "scenario": scenario,
        "unit_entity": "spm_unit",
        "weight_entity": "household",
        "membership_provenance": "Synthetic source-native person_spm_unit_id links",
        "geography_kind": "national" if national else "county",
        "county_vintage": "2020",
        "columns": columns,
    }


def snapshot(frame):
    return {
        "tables": {
            entity: frame.table(entity).to_dict(orient="list")
            for entity in frame.entities
        },
        "weights": {
            entity: {
                "kind": frame.weights_for(entity).kind.value,
                "values": frame.weights_for(entity).values.tolist(),
                "sha256": hashlib.sha256(
                    frame.weights_for(entity).values.tobytes()
                ).hexdigest(),
            }
            for entity in frame.weighted_entities
        },
        "metadata": copy.deepcopy(dict(frame.metadata)),
        "mass_log": repr(frame.mass_log),
    }


def inspect_run(forecast, frame, run, kwargs, *, boundary=False):
    """Compare core outputs to the canonical oracle; retain decimal semantics."""
    allowed = {
        f"{MODULE_ID}#input.{name}"
        for name in (
            "age",
            "independent_minor_role",
            "housing_tenure",
            "area_index",
            "resources",
        )
    }
    require(
        {fact["name"] for fact in run["request"]["dataset"]["inputs"]}
        <= allowed,
        "Native request contains nonprimitive/precomputed policy inputs",
    )
    prepared = prepare_frame_inputs(frame, forecast, **kwargs)
    require(
        len(run["results"])
        == len(prepared.units)
        == len(run["receipt"]["result"]["results"]),
        "Native unit cardinality differs",
    )
    comparisons = []
    for unit, actual, native in zip(
        prepared.units, run["results"], run["receipt"]["result"]["results"]
    ):
        canonical = forecast.calculate_unit(unit, scenario=kwargs["scenario"])
        deltas = {}
        for field in (
            "reference_threshold",
            "equivalence_factor",
            "geographic_factor",
            "unadjusted_threshold",
            "threshold",
            "housing_share",
            "housing_portion",
        ):
            require(
                math.isclose(
                    actual[field],
                    canonical[field],
                    abs_tol=1e-8,
                    rel_tol=1e-12,
                ),
                f"Native/canonical {field} differs for {unit.unit_id}",
            )
            deltas[field] = abs(actual[field] - canonical[field])
        require(
            (actual["num_adults"], actual["num_children"])
            == (unit.num_adults, unit.num_children),
            "Native primitive classification differs",
        )
        for rule in (
            "is_measurement_adult",
            "num_adults",
            "num_children",
            "reference_threshold",
            "equivalence_factor",
            "rent_index",
            "geographic_factor",
            "housing_share",
            "housing_portion",
            "adjusted_threshold",
        ):
            nodes = [
                node
                for node in native["trace"].values()
                if node.get("id") == f"{MODULE_ID}#{rule}"
            ]
            require(
                nodes and nodes[0].get("executed_expression"),
                f"Missing native trace: {rule}",
            )
        if unit.resources is not None:
            exact = Decimal(actual["native_decimal_values"]["threshold"])
            require(
                actual["is_in_poverty"]
                == (Decimal(str(unit.resources)) < exact),
                "Native poverty did not preserve strict decimal comparison",
            )
        if not boundary:
            require(
                actual["is_in_poverty"] == canonical["is_in_poverty"],
                "Nonboundary poverty differs from canonical oracle",
            )
        comparisons.append(
            {
                "unit_id": unit.unit_id,
                "num_adults": actual["num_adults"],
                "num_children": actual["num_children"],
                "absolute_differences": deltas,
                "canonical_threshold": canonical["threshold"],
                "native_threshold_decimal": actual["native_decimal_values"][
                    "threshold"
                ],
                "resources": unit.resources,
                "canonical_is_in_poverty": canonical["is_in_poverty"],
                "native_is_in_poverty": actual["is_in_poverty"],
            }
        )
    return comparisons


def validate(args, out):
    forecast = load_forecast()
    adapter = AxiomSPMAdapter(forecast, binary=args.binary)
    capabilities = adapter.capabilities()
    write(out / "capabilities.json", capabilities)
    source_files = (
        "spm_calculator/axiom_adapter.py",
        "spm_calculator/microcosm_adapter.py",
        "spm_calculator/rolling_forecast.py",
        "spm_calculator/release.py",
        "scripts/validate_axiom_integration.py",
    )
    provenance = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "publication_status": "local 1.0 candidate; wrapper/API not published",
        "fixture_kind": "Public synthetic real Microcosm Frames",
        "forecast_id": forecast.forecast_id,
        "forecast_sha256": forecast.content_sha256,
        "forecast_information_date": forecast.to_dict()["information_date"],
        "package_versions": {
            name: version(name)
            for name in (
                "spm-calculator",
                "microcosm-frame",
                "pandas",
                "numpy",
            )
        },
        "python": sys.version,
        "source_checkout_head": git("rev-parse", "HEAD"),
        "source_checkout_dirty": bool(git("status", "--porcelain")),
        "source_file_sha256": {
            name: sha256(REPO / name) for name in source_files
        },
        "binary": str(adapter.binary),
        "binary_sha256": sha256(adapter.binary),
        "composition_domain": {
            "min_adults": 1,
            "max_adults": 10,
            "min_children": 0,
            "max_children": 10,
        },
        "comparison_tolerance": {"absolute": 1e-8, "relative": 1e-12},
    }
    if args.core_checkout:
        core = args.core_checkout.resolve()
        provenance["declared_core_source_checkout"] = {
            "path": str(core),
            "git_head": git("rev-parse", "HEAD", cwd=core),
            "dirty": bool(git("status", "--porcelain", cwd=core)),
            "cargo_lock_sha256": sha256(core / "Cargo.lock"),
            "note": "Observed checkout, not independent proof that it built these binary bytes",
        }
    write(out / "provenance.json", provenance)
    bundles = {}
    for scenario in ("ce_trend", "zero_real"):
        # Full forecast-year source supports replay beyond the bounded default probes.
        write(
            out / f"{scenario}.spec.json",
            export_build_spec(forecast, scenario=scenario),
        )
        path = out / f"{scenario}.bundle.json"
        build = adapter.build_bundle(path, scenario=scenario)
        write(out / f"{scenario}.build.json", build)
        bundles[scenario] = path, build["bundle_sha256"]
    checks = []

    def run_case(name, frame, kwargs, *, boundary=False):
        before = snapshot(frame)
        write(out / f"{name}.frame.json", before)
        request, prepared = make_request(forecast, frame, **kwargs)
        write(out / f"{name}.request.json", request)
        write(out / f"{name}.spm_inputs.json", prepared.provenance)
        path, digest = bundles[kwargs["scenario"]]
        result = adapter.execute_bundle(path, digest, frame, **kwargs)
        write(out / f"{name}.receipt.json", result["receipt"])
        write(out / f"{name}.results.json", result["results"])
        require(
            result["request"] == request,
            "Retained request differs from executed request",
        )
        require(
            snapshot(frame) == before, "Source Frame or typed weights mutated"
        )
        comparisons = inspect_run(
            forecast, frame, result, kwargs, boundary=boundary
        )
        checks.append(
            {
                "name": name,
                "year": kwargs["year"],
                "scenario": kwargs["scenario"],
                "comparisons": comparisons,
            }
        )
        return result

    frame = synthetic_frame(
        resources=[0.0, 1_000_000.0, 0.0],
        tenures=["renter", "owner_with_mortgage", "owner_without_mortgage"],
    )
    for scenario in ("ce_trend", "zero_real"):
        for year in (2022, 2025, 2036):
            run_case(f"{scenario}-{year}", frame, selection(year, scenario))

    # Apply through the public API and delegate every weighted statistic to Frame.
    before = snapshot(frame)
    attached = adapter.apply_to_frame(frame, **selection())
    execution = attached.metadata["spm_forecast_application"]["execution"]
    write(out / "apply-to-frame.execution.json", execution)
    write(
        out / "apply-to-frame.summary.json",
        summarize_spm_units(
            attached, unit_entity="spm_unit", weight_entity="household"
        ),
    )
    require(snapshot(frame) == before, "apply_to_frame mutated source data")
    require(
        snapshot(attached)["weights"] == before["weights"],
        "Attached weights changed",
    )
    require(
        attached.weights_for("household") is frame.weights_for("household"),
        "Typed source weight object was replaced",
    )

    threshold = forecast.entry(2025)["thresholds"]["renter"]
    reference = [(35, False), (30, False), (8, False), (4, False)]
    national = synthetic_frame(
        [reference] * 3,
        resources=[threshold - 0.01, threshold, threshold + 0.01],
    )
    boundary = run_case(
        "national-strict-boundary", national, selection(national=True)
    )
    require(
        [r["is_in_poverty"] for r in boundary["results"]]
        == [True, False, False],
        "National strict boundary failed",
    )
    require(
        Decimal(boundary["results"][1]["native_decimal_values"]["threshold"])
        == Decimal(str(threshold)),
        "National boundary was not exact",
    )

    decimal_frame = synthetic_frame(
        [[(35, False)]], tenures=["owner_with_mortgage"]
    )
    prepared = prepare_frame_inputs(
        decimal_frame, forecast, **selection(resources=False)
    )
    canonical_threshold = forecast.calculate_unit(prepared.units[0])[
        "threshold"
    ]
    decimal_frame.table("spm_unit")["resources"] = [canonical_threshold]
    run_case("decimal-boundary", decimal_frame, selection(), boundary=True)

    # Absent primitive roles require explicit source head/spouse facts.
    roles = synthetic_frame(
        [[(15, False), (17, False), (14, False)]], resources=[0.0]
    )
    roles.person.drop(columns=["is_spm_independent_minor_role"], inplace=True)
    roles.person["is_household_head"] = [True, False, False]
    roles.person["is_household_spouse"] = [False, True, False]
    role_run = run_case("explicit-source-roles", roles, selection())
    require(
        [(r["num_adults"], r["num_children"]) for r in role_run["results"]]
        == [(2, 1)],
        "Explicit minor role classification failed",
    )

    error_checks = []
    for case, code in (
        ("year", "SPM_YEAR_UNAVAILABLE"),
        ("county", "SPM_GEOGRAPHY_UNAVAILABLE"),
        ("no-adult", "SPM_COMPOSITION_REQUIRED"),
        ("unsupported-count", "SPM_COMPOSITION_UNSUPPORTED"),
    ):
        invalid = synthetic_frame(resources=[0.0, 0.0, 0.0])
        kwargs = selection()
        if case == "year":
            kwargs["year"] = 1900
        elif case == "county":
            invalid.table("spm_unit").loc[0, "county_fips"] = "99999"
        elif case == "no-adult":
            invalid.person.loc[:, "age"] = 14
        else:
            invalid = synthetic_frame([[(35, False)] * 11], resources=[0.0])
        operations = [
            (
                "axiom_transport",
                lambda: make_request(forecast, invalid, **kwargs),
            )
        ]
        if case != "unsupported-count":
            operations.append(
                (
                    "canonical_frame",
                    lambda: apply_forecast_to_frame(
                        invalid, forecast, **kwargs
                    ),
                )
            )
        for provider, operation in operations:
            try:
                operation()
            except SPMInputError as error:
                require(
                    error.code == code, f"Unexpected input error for {case}"
                )
                error_checks.append(
                    {
                        "case": case,
                        "provider": provider,
                        "code": error.code,
                        "message": str(error),
                    }
                )
            else:
                raise AssertionError(
                    f"Invalid input accepted: {case}/{provider}"
                )

    # The native finite-decimal range is narrower than Python's finite floats.
    numeric = synthetic_frame([[(35, False)]], resources=[1e30])
    request, _ = make_request(forecast, numeric, **selection())
    write(out / "native-decimal-range.request.json", request)
    try:
        adapter.execute_bundle(*bundles["ce_trend"], numeric, **selection())
    except AxiomRuntimeError as error:
        write(out / "native-decimal-range.response.json", error.response)
        require(
            error.returncode and error.response,
            "Native failure lost structured response",
        )
        require(
            "invalid_dataset" in json.dumps(error.response),
            "Unexpected native decimal failure",
        )
        error_checks.append(
            {
                "case": "native-decimal-range",
                "returncode": error.returncode,
                "response": error.response,
            }
        )
    else:
        raise AssertionError(
            "Expected native decimal range rejection was absent"
        )
    write(out / "expected-errors.json", error_checks)
    report = {
        **provenance,
        "all_checks_passed": True,
        "checks": checks,
        "expected_error_checks": len(error_checks),
        "source_weights_unchanged": True,
        "native_trace_checked": True,
        "limits": [
            "Frame-to-core bridge; dense Microcosm AxiomEngine lacks cross-entity relations and dated derived rules",
            "Core looks up the canonical equivalence table; fractional power is unsupported",
            "Default supported domain is 1–10 adults and 0–10 children; out-of-domain inputs are rejected",
            "Decimal arithmetic can differ at exact canonical binary-float poverty boundaries; native judgments are preserved",
            "No tax/benefit calculation, SPM resource construction, CE estimation, population certification or full PE-to-Axiom migration",
            "Unsigned development bundles establish integrity, not authentication, signed admission or legal validity",
            "Observed local candidate evidence; wrapper/API production publication is not claimed",
        ],
    }
    write(out / "validation.json", report)
    print(
        json.dumps(
            {
                "output_dir": str(out),
                "forecast_sha256": forecast.content_sha256,
                "runs": len(checks),
                "unit_comparisons": sum(len(c["comparisons"]) for c in checks),
                "expected_error_checks": len(error_checks),
                "all_checks_passed": True,
            },
            indent=2,
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--binary", required=True, help="Actual axiom-core executable"
    )
    parser.add_argument(
        "--core-checkout",
        type=Path,
        help="Optional observed source checkout provenance",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="New evidence directory; must not exist",
    )
    args = parser.parse_args()
    out = args.output_dir.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=False)
    try:
        validate(args, out)
    except Exception as error:
        failure = {"type": type(error).__name__, "message": str(error)}
        if isinstance(error, AxiomRuntimeError):
            failure.update(
                response=error.response, returncode=error.returncode
            )
        write(out / "failure.json", failure)
        raise


if __name__ == "__main__":
    main()
