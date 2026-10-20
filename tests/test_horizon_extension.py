"""A horizon extension may add 2036 but cannot change any retained year."""

import copy
import hashlib
import json

import pytest

from scripts import verify_horizon_extension as verifier
from spm_calculator.release import canonical_bytes
from spm_calculator.rolling_forecast import seal_forecast

ADDED = str(verifier.ADDED_YEAR)
LAST = str(max(verifier.RETAINED_YEARS))


@pytest.fixture(scope="module")
def baseline():
    data = (verifier.ROOT / verifier.BASELINE).read_bytes()
    assert hashlib.sha256(data).hexdigest() == verifier.BASELINE_FILE_SHA256
    return json.loads(data)


def _reseal(document):
    document["assumption_sha256"] = hashlib.sha256(
        canonical_bytes(document["assumptions"])
    ).hexdigest()
    assignment = document["county_assignments"]
    assignment["sha256"] = hashlib.sha256(
        canonical_bytes({k: v for k, v in assignment.items() if k != "sha256"})
    ).hexdigest()
    return seal_forecast(document)


def _rebuilt(digest):
    return hashlib.sha256(f"rebuilt:{digest}".encode()).hexdigest()


def _extend(original):
    """Record a synthetic added year the way a fresh component rebuild does."""
    current = copy.deepcopy(original)
    for scenario in current["scenarios"].values():
        entry = copy.deepcopy(scenario["years"][LAST])
        entry["ce_window"].update(start="2031Q2", end="2036Q1")
        entry["acs_window"].update(
            start=2031,
            end=2035,
            projected_cohort_years=list(range(2031, 2036)),
        )
        scenario["years"][ADDED] = entry
    assumptions = current["assumptions"]
    acs = assumptions["acs"]
    for record in (
        assumptions["price_projections"],
        assumptions["ce"]["inflation_rates"],
        assumptions["ce"]["price_source"]["annual_growth_by_year"],
        *assumptions["ce"]["annual_cpi"].values(),
        acs["nominal_rent_growth_by_year"],
        acs["price_growth_by_year"],
        acs["price_inputs"]["forecast_source"]["annual_growth_by_year"],
        acs["puma_vintage_by_spm_year"],
        current["rent_sensitivity"]["index_difference_by_year"],
        current["rent_sensitivity"]["nominal_rent_growth_by_year"],
        current["rent_sensitivity"]["price_growth_by_year"],
        current["county_assignments"]["year_maps"],
    ):
        record[ADDED] = copy.deepcopy(record[LAST])
    acs["relative_index_stabilization"]["through_year"] = verifier.ADDED_YEAR
    for container in (current["code_sha256"], acs["generator_identity"]):
        for name in verifier.EXTENDED_CODE:
            if name in container:
                container[name] = _rebuilt(container[name])
    for path in verifier.REBUILT_COMPONENTS:
        current["component_sha256"][path] = _rebuilt(
            current["component_sha256"][path]
        )
    for source in current["sources"]:
        name = source.get("package_path", source.get("path"))
        if name in verifier.REBUILT_INPUTS:
            source["sha256"] = _rebuilt(source["sha256"])
    current["sources"] = [
        source
        for source in current["sources"]
        if source["id"] != verifier.ADAPTATION_SOURCE
    ]
    del acs["provenance_adaptation"]
    return _reseal(current)


@pytest.fixture(scope="module")
def extended(baseline):
    return _extend(baseline)


def test_extension_admits_only_the_declared_changes(baseline, extended):
    evidence = verifier.compare_forecasts(baseline, extended, calculate=False)
    assert evidence["retained_fields_exactly_equal"] is True
    assert evidence["calculation_grid_executed"] is False
    assert "amount_comparison" not in evidence
    changed = set(evidence["allowed_changes"])
    assert {
        "content_sha256",
        "assumption_sha256",
        "county_assignments/sha256",
        f"county_assignments/year_maps/{ADDED}",
        f"scenarios/ce_trend/years/{ADDED}",
        f"scenarios/zero_real/years/{ADDED}",
        f"assumptions/price_projections/{ADDED}",
        "assumptions/acs/relative_index_stabilization/through_year",
        f"sources/id={verifier.ADAPTATION_SOURCE} (not repeated)",
        "assumptions/acs/provenance_adaptation (not repeated)",
    } <= changed
    assert {
        f"code_sha256/{name}" for name in verifier.EXTENDED_CODE
    } <= changed
    assert {
        f"component_sha256/{path}" for path in verifier.REBUILT_COMPONENTS
    } <= changed
    summary = evidence["added_year_summary"]
    assert set(summary) == {"ce_trend", "zero_real"}
    for scenario in summary.values():
        assert scenario["ce_window"]["start"] == "2031Q2"
        assert scenario["acs_window"]["projected_years"] == 5
        assert scenario["rent_indices_equal_previous_year"] is True


def test_comparison_requires_the_pinned_pre_extension_artifact(extended):
    with pytest.raises(ValueError, match="pinned pre-extension artifact"):
        verifier.compare_documents(extended, extended)


@pytest.mark.parametrize(
    "mutation",
    [
        "retained_threshold",
        "retained_rent_index",
        "retained_price_path",
        "second_added_year",
        "other_code_identity",
        "unchanged_extended_code",
        "other_component",
        "source_removed",
        "stabilization_year",
        "validation_metric",
        "real_growth_rate",
        "added_year_status",
    ],
)
def test_resealed_unapproved_changes_are_rejected(
    baseline, extended, mutation
):
    current = copy.deepcopy(extended)
    years = current["scenarios"]["ce_trend"]["years"]
    if mutation == "retained_threshold":
        years["2030"]["thresholds"]["renter"] += 0.001
    elif mutation == "retained_rent_index":
        years[LAST]["rent_indices"]["25002"] += 0.001
    elif mutation == "retained_price_path":
        current["assumptions"]["price_projections"]["2030"] += 1e-9
    elif mutation == "second_added_year":
        differences = current["rent_sensitivity"]["index_difference_by_year"]
        differences["2037"] = differences[ADDED]
    elif mutation == "other_code_identity":
        module = "spm_calculator/rolling_forecast.py"
        current["code_sha256"][module] = hashlib.sha256(
            (verifier.ROOT / module).read_bytes() + b"\n# mutation\n"
        ).hexdigest()
    elif mutation == "unchanged_extended_code":
        module = "spm_calculator/ce_forecast.py"
        current["code_sha256"][module] = baseline["code_sha256"][module]
    elif mutation == "other_component":
        path = "spm_calculator/data/current/acs_normalized_products.json"
        current["component_sha256"][path] = _rebuilt(
            current["component_sha256"][path]
        )
    elif mutation == "source_removed":
        current["sources"].pop(0)
    elif mutation == "stabilization_year":
        current["assumptions"]["acs"]["relative_index_stabilization"][
            "through_year"
        ] = 2037
    elif mutation == "validation_metric":
        current["validation"]["acs"]["mean_absolute_percentage_error"] += 1e-9
    elif mutation == "real_growth_rate":
        current["scenarios"]["ce_trend"]["real_growth_rate"] += 1e-9
    elif mutation == "added_year_status":
        years[ADDED]["housing_share_status"] = "published_anchor"
    with pytest.raises(ValueError):
        verifier.compare_forecasts(baseline, _reseal(current), calculate=False)


def test_integer_float_changes_are_not_hidden_by_python_equality(
    baseline, extended
):
    current = copy.deepcopy(extended)
    acs = current["assumptions"]["acs"]
    acs["support_threshold"] = float(acs["support_threshold"])
    assert (
        acs["support_threshold"]
        == baseline["assumptions"]["acs"]["support_threshold"]
    )
    with pytest.raises(ValueError, match="remaining document fields"):
        verifier.compare_forecasts(baseline, _reseal(current), calculate=False)


def _sha256(path):
    return hashlib.sha256((verifier.ROOT / path).read_bytes()).hexdigest()


def test_committed_receipt_verifies_the_committed_forecast(baseline):
    """The current receipt records the full check of this exact artifact."""
    current_bytes = (verifier.ROOT / verifier.CURRENT).read_bytes()
    receipt = json.loads((verifier.ROOT / verifier.RECEIPT).read_bytes())
    assert receipt["kind"] == "horizon_extension_verification"
    assert receipt["status"] == "all_exact_equality_checks_passed"
    assert receipt["baseline_artifact"]["sha256"] == (
        verifier.BASELINE_FILE_SHA256
    )
    assert receipt["final_artifact"]["sha256"] == (
        hashlib.sha256(current_bytes).hexdigest()
    )
    for path, digest in {
        **receipt["rebuilt_components"],
        **receipt["extended_code"],
    }.items():
        assert _sha256(path) == digest
    for key in ("previous_scientific_refresh", "audit_script"):
        assert _sha256(receipt[key]["path"]) == receipt[key]["sha256"]

    comparison = receipt["horizon_comparison"]
    evidence = verifier.compare_forecasts(
        baseline, json.loads(current_bytes), calculate=False
    )
    assert comparison["allowed_changes"] == evidence["allowed_changes"]
    assert comparison["added_year_summary"] == evidence["added_year_summary"]
    assert comparison["calculation_grid_executed"] is True
    amounts = comparison["amount_comparison"]
    assert amounts["retained_calculations"] == 2 * 14 * 349 * 3 * 8 * 9
    assert amounts["added_year_calculations"] == 2 * 349 * 3 * 8 * 9
    assert amounts["max_absolute_difference"] == 0
    # The retained grid reproduces the September 9 artifact's calculations,
    # whose digest the archived adaptation receipt recorded.
    previous = json.loads(verifier.retained_bytes(verifier.RECEIPT))
    original = previous["scientific_equivalence"]["amount_comparison"]
    assert (
        amounts["current_sha256_big_endian_float64"]
        == original["original_sha256_big_endian_float64"]
    )
