"""The adaptation allowlist cannot admit changes to scientific values."""

import copy
import hashlib
import json

import pytest

from scripts import verify_provenance_equivalence as verifier
from scripts.verify_horizon_extension import retained_bytes
from spm_calculator.release import canonical_bytes
from spm_calculator.rolling_forecast import seal_forecast


@pytest.fixture(scope="module")
def documents():
    original_path = (
        verifier.ROOT
        / "web/public/data/canonical"
        / f"rolling-forecast-{verifier.ORIGINAL_FILE_SHA256}.json"
    )
    original_bytes = original_path.read_bytes()
    assert (
        hashlib.sha256(original_bytes).hexdigest()
        == verifier.ORIGINAL_FILE_SHA256
    )
    # The adapted artifact, retained after the 2036 horizon rebuild.
    adapted = retained_bytes(
        "spm_calculator/data/current/rolling_forecast_2026_09_09.json"
    )
    return json.loads(original_bytes), json.loads(adapted)


def _reseal(document):
    document["assumption_sha256"] = hashlib.sha256(
        canonical_bytes(document["assumptions"])
    ).hexdigest()
    return seal_forecast(document)


def test_current_adaptation_preserves_the_complete_scientific_document(
    documents,
):
    evidence = verifier.compare_forecasts(*documents, calculate=False)
    assert evidence["scientific_fields_exactly_equal"] is True
    assert evidence["calculation_grid_executed"] is False
    assert evidence["complete_scenarios_sha256"] == (
        "d5e542944d26a51418b2246b77b00d4e0814574b0f676a693e1475703eb041be"
    )
    assert "amount_comparison" not in evidence


@pytest.mark.parametrize(
    "mutation",
    [
        "threshold",
        "rent_index",
        "scientific_metadata",
        "sensitivity",
        "other_code_identity",
        "source_metadata",
        "source_order",
        "extra_component",
        "adaptation_source",
        "adaptation_link",
    ],
)
def test_resealed_unapproved_changes_are_rejected(documents, mutation):
    original, source = documents
    current = copy.deepcopy(source)
    entry = current["scenarios"]["ce_trend"]["years"]["2026"]
    if mutation == "threshold":
        entry["thresholds"]["renter"] += 0.001
    elif mutation == "rent_index":
        entry["rent_indices"]["25002"] += 0.001
    elif mutation == "scientific_metadata":
        current["assumptions"]["acs"]["support_threshold"] += 1
    elif mutation == "sensitivity":
        current["rent_sensitivity"]["label"] += " changed"
    elif mutation == "other_code_identity":
        module = "spm_calculator/rolling_forecast.py"
        # A real byte mutation's digest, rather than an accepted alternate pin.
        current["code_sha256"][module] = hashlib.sha256(
            (verifier.ROOT / module).read_bytes() + b"\n# mutation\n"
        ).hexdigest()
    elif mutation == "source_metadata":
        current["sources"][0]["availability_note"] += " changed"
    elif mutation == "source_order":
        current["sources"][:2] = reversed(current["sources"][:2])
    elif mutation == "extra_component":
        current["component_sha256"]["unexpected.json"] = current[
            "component_sha256"
        ][verifier.MANIFEST]
    elif mutation == "adaptation_source":
        source = next(
            row
            for row in current["sources"]
            if row["id"] == "acs-code-identity-adaptation"
        )
        source["title"] = "Fresh raw-source reparse"
    elif mutation == "adaptation_link":
        current["assumptions"]["acs"]["provenance_adaptation"]["path"] = (
            "spm_calculator/data/current/unrelated.json"
        )
    with pytest.raises(ValueError):
        verifier.compare_forecasts(original, _reseal(current), calculate=False)


def test_integer_float_changes_are_not_hidden_by_python_equality(documents):
    original, source = documents
    current = copy.deepcopy(source)
    current["assumptions"]["acs"]["support_threshold"] = float(
        current["assumptions"]["acs"]["support_threshold"]
    )
    assert (
        current["assumptions"]["acs"]["support_threshold"]
        == original["assumptions"]["acs"]["support_threshold"]
    )
    with pytest.raises(ValueError, match="remaining document fields"):
        verifier.compare_forecasts(original, _reseal(current), calculate=False)
