"""Verify that a one-year horizon extension preserves every retained year.

Compare the current canonical forecast with the retained pre-extension
download. Only declared derived identities, the rebuilt components, the
extended code and year-keyed records that gain exactly the added year may
differ. Every 2022–2035 value must survive byte-for-byte. The default also
executes both canonical consumers over the declared household-composition
grid for every retained year and calculates the added year once.
"""

from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import math
import re
import struct
from pathlib import Path

from spm_calculator.release import TENURES, SPMUnit, canonical_bytes
from spm_calculator.rolling_forecast import DEFAULT_FORECAST, SPMForecast

ROOT = Path(__file__).resolve().parents[1]
BASELINE_FILE_SHA256 = (
    "cc06784feb81f8c7935d4494cea0a9821a79af868ac50383da8be37c6dc14b99"
)
BASELINE_CONTENT_SHA256 = (
    "3d86d5c4c0423480e6b69b75d222ffa4a7a2639e4094df5ba2504af01be17173"
)
BASELINE = (
    f"web/public/data/canonical/rolling-forecast-{BASELINE_FILE_SHA256}.json"
)
CURRENT = f"spm_calculator/data/current/{DEFAULT_FORECAST}"
RECEIPT = "spm_calculator/data/current/scientific_refresh_receipt.json"
ARCHIVE = "spm_calculator/data/provenance/horizon-2036-extension/"
PREVIOUS_RECEIPT = ARCHIVE + "scientific_refresh_receipt.json"
# Files the component rebuild replaces, with their pre-extension bytes.
# Earlier provenance checks read these instead of the rebuilt files.
RETAINED = {
    "spm_calculator/data/current/acs_rolling_forecast.json": (
        ARCHIVE + "acs_rolling_forecast.json.gz",
        "47ed3e6a6924994079fa2f1f5698021b3fab795d78b36a331de5b3ba067d1a78",
    ),
    "spm_calculator/data/current/ce_rolling_forecast.json": (
        ARCHIVE + "ce_rolling_forecast.json.gz",
        "26b863a3e5ea3e23fb4fcd547ccd497e25625d55542b8711a4825239aebb4e21",
    ),
    RECEIPT: (
        PREVIOUS_RECEIPT,
        "b0a27cd88e8161c50b17805ef43399ec948c89a0d5e4a2a8d9e562159ee33db1",
    ),
    CURRENT: (BASELINE, BASELINE_FILE_SHA256),
}
RETAINED_YEARS = tuple(range(2022, 2036))
ADDED_YEAR = 2036
# Hashed code that the extension edits. Any other identity change fails.
EXTENDED_CODE = (
    "scripts/build_acs_forecast.py",
    "scripts/build_forecast_inputs.py",
    "scripts/build_rolling_forecast.py",
    "spm_calculator/ce_forecast.py",
    "spm_calculator/forecast_inputs.py",
)
# The horizon inputs gain the added year. The 2021 ACS bundle pins their
# county-assignment digest, whose year map gains the added year.
REBUILT_INPUTS = (
    "spm_calculator/data/current/acs_2021_source_inputs.json",
    "spm_calculator/data/current/forecast_horizon_inputs.json",
)
REBUILT_COMPONENTS = (
    "spm_calculator/data/current/acs_rolling_forecast.json",
    "spm_calculator/data/current/ce_rolling_forecast.json",
    *REBUILT_INPUTS,
)
# A fresh ACS build does not repeat the equivalent-code adaptation link.
# The retained normalized-products manifest still carries it.
ADAPTATION_SOURCE = "acs-code-identity-adaptation"
AMOUNT_FIELDS = (
    "threshold",
    "housing_portion",
    "reference_threshold",
    "unadjusted_threshold",
    "geographic_factor",
)
YEAR_KEY = re.compile(r"(19|20)\d\d")


def _digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)


def _same(original, current, label):
    _require(
        canonical_bytes(original) == canonical_bytes(current),
        f"Scientific or undeclared provenance difference: {label}",
    )


def retained_bytes(path):
    """Return a repository file's bytes from before the horizon rebuild.

    Paths the rebuild did not replace read their current bytes.
    """
    stored, expected = RETAINED.get(path, (path, None))
    data = (ROOT / stored).read_bytes()
    if stored.endswith(".gz"):
        data = gzip.decompress(data)
    _require(
        expected is None or hashlib.sha256(data).hexdigest() == expected,
        f"Retained pre-extension evidence changed: {stored}",
    )
    return data


def _year_keyed(value):
    return (
        isinstance(value, dict)
        and bool(value)
        and all(
            isinstance(key, str) and YEAR_KEY.fullmatch(key) for key in value
        )
    )


def _strip_added_year(before, after, path, changed):
    """Remove exactly one added-year key wherever a year-keyed record grew."""
    added = str(ADDED_YEAR)

    def child(key):
        return f"{path}/{key}" if path else str(key)

    if isinstance(before, dict) and isinstance(after, dict):
        if (
            _year_keyed(before)
            and added not in before
            and set(after) == set(before) | {added}
        ):
            after.pop(added)
            changed.append(child(added))
        for key in before.keys() & after.keys():
            _strip_added_year(before[key], after[key], child(key), changed)
    elif (
        isinstance(before, list)
        and isinstance(after, list)
        and len(before) == len(after)
    ):
        for index, (left, right) in enumerate(zip(before, after)):
            _strip_added_year(left, right, child(index), changed)


def _restore_identities(original, candidate, changed):
    for field in ("content_sha256", "assumption_sha256"):
        _require(
            _hash(candidate[field]) and candidate[field] != original[field],
            f"Expected a new derived {field}",
        )
        candidate[field] = original[field]
        changed.append(field)
    for container in (
        ("code_sha256",),
        ("assumptions", "acs", "generator_identity"),
    ):
        before, after = original, candidate
        for key in container:
            before, after = before[key], after[key]
        _require(
            set(before) == set(after),
            "Code identity coverage changed: " + "/".join(container),
        )
        for name in sorted(before):
            label = "/".join((*container, name))
            if name in EXTENDED_CODE:
                _require(
                    _hash(after[name]) and after[name] != before[name],
                    f"Expected the extended code identity: {label}",
                )
                after[name] = before[name]
                changed.append(label)
            else:
                _require(
                    after[name] == before[name],
                    f"Undeclared code identity change: {label}",
                )
    for path in REBUILT_COMPONENTS:
        before = original["component_sha256"][path]
        after = candidate["component_sha256"][path]
        _require(_hash(after) and after != before, f"Expected rebuilt {path}")
        candidate["component_sha256"][path] = before
        changed.append(f"component_sha256/{path}")


def _restore_sources(original, candidate, changed):
    prior = {source["id"]: source for source in original["sources"]}
    current = [source["id"] for source in candidate["sources"]]
    _require(
        not set(current) - set(prior),
        "Undeclared source addition: "
        + ", ".join(sorted(set(current) - set(prior))),
    )
    removed = [identity for identity in prior if identity not in current]
    _require(
        set(removed) <= {ADAPTATION_SOURCE},
        "Undeclared source removal: " + ", ".join(removed),
    )
    for source in candidate["sources"]:
        name = source.get("package_path", source.get("path"))
        before = prior[source["id"]]["sha256"]
        if name in REBUILT_INPUTS:
            _require(
                _hash(source["sha256"]) and source["sha256"] != before,
                f"Expected the rebuilt input: {source['id']}",
            )
            source["sha256"] = before
            changed.append(f"sources/id={source['id']}/sha256")
    for index, source in enumerate(original["sources"]):
        if source["id"] in removed:
            candidate["sources"].insert(index, copy.deepcopy(source))
            changed.append(f"sources/id={source['id']} (not repeated)")
    acs = candidate["assumptions"]["acs"]
    if (
        "provenance_adaptation" in original["assumptions"]["acs"]
        and "provenance_adaptation" not in acs
    ):
        _require(
            ADAPTATION_SOURCE in removed,
            "Adaptation link and source must leave together",
        )
        acs["provenance_adaptation"] = copy.deepcopy(
            original["assumptions"]["acs"]["provenance_adaptation"]
        )
        changed.append("assumptions/acs/provenance_adaptation (not repeated)")


def compare_documents(original, current):
    """Reverse only the declared extension edits, then require equality.

    Artifact validation and full calculations are performed by
    ``compare_forecasts``. This helper also supports focused mutation tests.
    """
    _require(
        original["content_sha256"] == BASELINE_CONTENT_SHA256,
        "Expected the independently pinned pre-extension artifact",
    )
    candidate = copy.deepcopy(current)
    changed = []
    _restore_identities(original, candidate, changed)
    _restore_sources(original, candidate, changed)
    stabilization = candidate["assumptions"]["acs"][
        "relative_index_stabilization"
    ]
    _require(
        original["assumptions"]["acs"]["relative_index_stabilization"][
            "through_year"
        ]
        == max(RETAINED_YEARS)
        and stabilization["through_year"] == ADDED_YEAR,
        "Stabilization disclosure must extend through the added year",
    )
    stabilization["through_year"] = max(RETAINED_YEARS)
    changed.append("assumptions/acs/relative_index_stabilization/through_year")
    assignment = candidate["county_assignments"]
    _require(
        assignment["sha256"]
        == _digest({k: v for k, v in assignment.items() if k != "sha256"}),
        "County assignment digest does not match its records",
    )
    assignment["sha256"] = original["county_assignments"]["sha256"]
    changed.append("county_assignments/sha256")
    for identity, scenario in original["scenarios"].items():
        _require(
            sorted(int(year) for year in scenario["years"])
            == list(RETAINED_YEARS),
            f"Unexpected retained years in {identity}",
        )
    _strip_added_year(original, candidate, "", changed)
    # Reversing only the enumerated edits preserves every retained value,
    # source order, numeric type and signed zero as well as all arrays.
    _same(original, candidate, "all remaining document fields")
    return {
        "retained_scenarios_sha256": _digest(original["scenarios"]),
        "retained_fields_exactly_equal": True,
        "allowed_changes": sorted(changed),
    }


def check_added_year(current):
    """Require a complete added year shaped like the last retained year."""
    document = current.to_dict()
    _require(
        current.years == (*RETAINED_YEARS, ADDED_YEAR),
        "Unexpected calculation year coverage",
    )
    summary = {}
    for identity, scenario in sorted(document["scenarios"].items()):
        last = scenario["years"][str(max(RETAINED_YEARS))]
        entry = scenario["years"][str(ADDED_YEAR)]
        for field in (
            "rent_indices",
            "geography_by_area",
            "median_diagnostics",
        ):
            _require(
                set(entry[field]) == set(last[field]),
                f"{identity}/{ADDED_YEAR} {field} coverage differs",
            )
        for field in (
            "national_status",
            "housing_share_status",
            "geography_status",
            "national_source_ids",
            "housing_share_source_ids",
        ):
            _require(
                entry[field] == last[field],
                f"{identity}/{ADDED_YEAR} {field} differs from "
                f"{max(RETAINED_YEARS)}",
            )
        _require(
            entry["ce_window"]["projected_quarters"] == 20
            and entry["acs_window"]["projected_years"] == 5,
            f"{identity}/{ADDED_YEAR} must use a fully projected window",
        )
        summary[identity] = {
            "thresholds": entry["thresholds"],
            "housing_shares": entry["housing_shares"],
            "threshold_growth_from_previous_year": {
                tenure: entry["thresholds"][tenure]
                / last["thresholds"][tenure]
                - 1
                for tenure in sorted(entry["thresholds"])
            },
            "rent_indices_equal_previous_year": (
                entry["rent_indices"] == last["rent_indices"]
            ),
            "ce_window": entry["ce_window"],
            "acs_window": entry["acs_window"],
        }
    return summary


def _grid(forecast, scenario, year):
    for area in sorted(forecast.areas_for_year(year, scenario=scenario)):
        for tenure in sorted(TENURES):
            for adults in range(1, 9):
                for children in range(9):
                    yield SPMUnit(
                        unit_id="horizon-extension",
                        num_adults=adults,
                        num_children=children,
                        tenure=tenure,
                        year=year,
                        resources=40_000,
                        geography_kind="metro",
                        geography_id=area,
                    )


def _amounts(result, forecast):
    _require(
        result.pop("forecast_sha256") == forecast.content_sha256,
        "Calculation failed to identify its artifact",
    )
    return struct.pack(">5d", *(result[key] for key in AMOUNT_FIELDS))


def compare_calculations(original, current):
    """Run the actual scalar API for every declared composition and area."""
    original_digest, current_digest = hashlib.sha256(), hashlib.sha256()
    added_digest = hashlib.sha256()
    calculations, added_calculations = 0, 0
    area_counts = {}
    scenarios = sorted(original.to_dict()["scenarios"])
    _require(scenarios == ["ce_trend", "zero_real"], "Unexpected scenarios")
    _require(
        original.years == RETAINED_YEARS,
        "Unexpected retained calculation year coverage",
    )
    for scenario in scenarios:
        for year in RETAINED_YEARS:
            _require(
                sorted(original.areas_for_year(year, scenario=scenario))
                == sorted(current.areas_for_year(year, scenario=scenario)),
                "Calculation area coverage changed",
            )
            for unit in _grid(original, scenario, year):
                before = original.calculate_unit(unit, scenario=scenario)
                after = current.calculate_unit(unit, scenario=scenario)
                before_bytes = _amounts(before, original)
                after_bytes = _amounts(after, current)
                _require(
                    before == after and before_bytes == after_bytes,
                    "Calculation output changed at "
                    f"{scenario}/{year}/{unit.geography_id}/{unit.tenure}/"
                    f"{unit.num_adults}/{unit.num_children}",
                )
                original_digest.update(before_bytes)
                current_digest.update(after_bytes)
                calculations += 1
            area_counts[f"{scenario}/{year}"] = len(
                original.areas_for_year(year, scenario=scenario)
            )
        for unit in _grid(current, scenario, ADDED_YEAR):
            result = current.calculate_unit(unit, scenario=scenario)
            amounts = _amounts(result, current)
            _require(
                all(math.isfinite(result[key]) for key in AMOUNT_FIELDS)
                and result["threshold"] > 0,
                f"Invalid {scenario}/{ADDED_YEAR} calculation",
            )
            added_digest.update(amounts)
            added_calculations += 1
        area_counts[f"{scenario}/{ADDED_YEAR}"] = len(
            current.areas_for_year(ADDED_YEAR, scenario=scenario)
        )
    _require(
        original_digest.digest() == current_digest.digest(),
        "Calculation amount digests differ",
    )
    return {
        "retained_calculations": calculations,
        "exact_field_comparisons": calculations * len(AMOUNT_FIELDS),
        "fields": list(AMOUNT_FIELDS),
        "all_result_fields_equal_except": ["forecast_sha256"],
        "max_absolute_difference": 0,
        "original_sha256_big_endian_float64": original_digest.hexdigest(),
        "current_sha256_big_endian_float64": current_digest.hexdigest(),
        "added_year_calculations": added_calculations,
        "added_year_sha256_big_endian_float64": added_digest.hexdigest(),
        "scenario_count": len(scenarios),
        "retained_years": list(RETAINED_YEARS),
        "added_year": ADDED_YEAR,
        "area_counts": area_counts,
        "composition_grid": {
            "adults": list(range(1, 9)),
            "children": list(range(9)),
        },
        "resources": 40_000,
        "ordering": (
            "scenario, retained years then added year, area, tenure, "
            "adults, children ascending; fields in recorded order"
        ),
    }


def compare_forecasts(original: dict, current: dict, *, calculate=True):
    """Return exact comparison evidence or reject any undeclared difference.

    ``calculate=False`` only omits the exhaustive API grid; it still validates
    both complete artifacts, every retained field and the added year.
    """
    before = SPMForecast.from_dict(
        original, expected_sha256=BASELINE_CONTENT_SHA256
    )
    after = SPMForecast.from_dict(current)
    evidence = compare_documents(original, current)
    evidence.update(
        {
            "status": "all_exact_equality_checks_passed",
            "original_content_sha256": before.content_sha256,
            "current_content_sha256": after.content_sha256,
            "added_year": ADDED_YEAR,
            "added_year_summary": check_added_year(after),
            "calculation_grid_executed": calculate,
        }
    )
    if calculate:
        evidence["amount_comparison"] = compare_calculations(before, after)
    return evidence


def _link(path):
    data = (ROOT / path).read_bytes()
    return {"path": path, "sha256": hashlib.sha256(data).hexdigest()}


def build_receipt(original_bytes, current_bytes, *, calculate=True):
    original, current = json.loads(original_bytes), json.loads(current_bytes)
    comparison = compare_forecasts(original, current, calculate=calculate)
    return {
        "schema_version": 1,
        "kind": "horizon_extension_verification",
        "status": comparison["status"],
        "scientific_component_rebuild_performed": True,
        "added_year": ADDED_YEAR,
        "retained_years": list(RETAINED_YEARS),
        "baseline_artifact": {
            "path": BASELINE,
            "sha256": hashlib.sha256(original_bytes).hexdigest(),
            "content_sha256": original["content_sha256"],
            "assumption_sha256": original["assumption_sha256"],
        },
        "final_artifact": {
            "path": CURRENT,
            "sha256": hashlib.sha256(current_bytes).hexdigest(),
            "content_sha256": current["content_sha256"],
            "assumption_sha256": current["assumption_sha256"],
        },
        "rebuilt_components": {
            path: _link(path)["sha256"] for path in REBUILT_COMPONENTS
        },
        "extended_code": {
            path: _link(path)["sha256"] for path in EXTENDED_CODE
        },
        "previous_scientific_refresh": _link(PREVIOUS_RECEIPT),
        "horizon_comparison": comparison,
        "audit_script": _link("scripts/verify_horizon_extension.py"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, default=ROOT / BASELINE)
    parser.add_argument("--current", type=Path, default=ROOT / CURRENT)
    parser.add_argument(
        "--output",
        type=Path,
        help=f"Write the receipt (normally {RECEIPT}) instead of printing it",
    )
    parser.add_argument(
        "--no-calculate",
        action="store_true",
        help="Skip the exhaustive calculation grid",
    )
    args = parser.parse_args()
    original_bytes = args.original.read_bytes()
    _require(
        hashlib.sha256(original_bytes).hexdigest() == BASELINE_FILE_SHA256,
        "Original artifact file does not match the immutable baseline pin",
    )
    receipt = build_receipt(
        original_bytes,
        args.current.read_bytes(),
        calculate=not args.no_calculate,
    )
    encoded = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode()
    if args.output is None:
        print(encoded.decode(), end="")
    else:
        args.output.write_bytes(encoded)
        print(args.output)


if __name__ == "__main__":
    main()
