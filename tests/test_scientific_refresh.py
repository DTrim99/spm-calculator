"""Provenance refresh must preserve estimates and disclose source limitations."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.build_acs_forecast import add_horizon_disclosures
from spm_calculator import acs_forecast_sources
from spm_calculator.rolling_forecast import load_forecast

ROOT = Path(__file__).resolve().parents[1]
CURRENT = ROOT / "spm_calculator/data/current"
ORIGINAL_SCENARIOS_SHA256 = (
    "72f859ebac3b3c64ad1a7aced670bcc2b6c886873161e1e6c5b7978c242c5555"
)


@pytest.fixture(scope="module")
def forecast_document():
    return json.loads(
        (CURRENT / "rolling_forecast_2026_09_09.json").read_text()
    )


@pytest.fixture(scope="module")
def acs_document():
    return json.loads((CURRENT / "acs_rolling_forecast.json").read_text())


def test_full_scenarios_preserve_every_original_value(forecast_document):
    scenarios = deepcopy(forecast_document["scenarios"])
    for scenario in scenarios.values():
        # The original artifact ended in 2035; later years are additions.
        scenario["years"] = {
            year: entry
            for year, entry in scenario["years"].items()
            if int(year) <= 2035
        }
        for entry in scenario["years"].values():
            for area in entry["geography_by_area"].values():
                area.pop("series_breaks", None)
    encoded = json.dumps(
        scenarios, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    assert hashlib.sha256(encoded).hexdigest() == ORIGINAL_SCENARIOS_SHA256


@pytest.mark.parametrize("scenario", ["ce_trend", "zero_real"])
@pytest.mark.parametrize(
    "from_area,to_area,kind,from_index,to_index",
    [
        (
            "25002",
            "25002",
            "published_series_break",
            1.551,
            1.043,
        ),
        (
            "45001",
            "modeled_residual_metro:45",
            "published_to_modeled_break",
            0.489,
            0.7155172413793104,
        ),
    ],
)
def test_source_breaks_are_available_at_both_year_endpoints(
    forecast_document, scenario, from_area, to_area, kind, from_index, to_index
):
    years = forecast_document["scenarios"][scenario]["years"]
    before = years["2022"]["geography_by_area"][from_area]
    after = years["2023"]["geography_by_area"][to_area]
    assert before["series_breaks"] == after["series_breaks"]
    assert len(before["series_breaks"]) == 1
    disclosure = before["series_breaks"][0]
    assert disclosure["kind"] == kind
    assert (disclosure["from_year"], disclosure["to_year"]) == (2022, 2023)
    assert disclosure["from_area_id"] == from_area
    assert disclosure["to_area_id"] == to_area
    assert disclosure["from_rent_index"] == from_index
    assert disclosure["to_rent_index"] == to_index
    assert (
        disclosure["from_rent_index"]
        == years["2022"]["rent_indices"][from_area]
    )
    assert (
        disclosure["to_rent_index"] == years["2023"]["rent_indices"][to_area]
    )
    source_ids = {source["id"] for source in forecast_document["sources"]}
    assert set(disclosure["source_ids"]) <= source_ids
    assert {"census-spm-2022", "census-spm-2023"} <= set(
        disclosure["source_ids"]
    )
    assert "annual rent growth" in disclosure["interpretation"]
    assert before["official_published_area"] is True
    if kind == "published_to_modeled_break":
        assert disclosure["affected_county_fips"] == ["45085"]
        assert after["status"] == "modeled_unanchored"
        assert after["official_published_area"] is False
    else:
        assert after["status"] == "published_anchor"
        assert after["official_published_area"] is True


def test_only_the_declared_transition_endpoints_gain_break_labels(
    forecast_document,
):
    for scenario in forecast_document["scenarios"].values():
        actual = {
            (year, area_id)
            for year, entry in scenario["years"].items()
            for area_id, area in entry["geography_by_area"].items()
            if "series_breaks" in area
        }
        assert actual == {
            ("2022", "25002"),
            ("2023", "25002"),
            ("2022", "45001"),
            ("2023", "modeled_residual_metro:45"),
        }


def test_stabilization_distinguishes_level_transition_and_donor_window(
    acs_document, forecast_document
):
    disclosure = acs_document["metadata"]["relative_index_stabilization"]
    assert (
        forecast_document["assumptions"]["acs"]["relative_index_stabilization"]
        == disclosure
    )
    assert disclosure["latest_observed_donor_year"] == 2024
    assert disclosure["first_constant_index_year"] == 2029
    assert disclosure["first_unchanged_transition_year"] == 2030
    assert disclosure["first_all_projected_window_year"] == 2030
    assert disclosure["through_year"] == 2036
    indices = acs_document["indices_by_year"]
    for year in range(2025, 2029):
        assert indices[str(year)] != indices["2029"]
    for year in range(2030, 2037):
        assert indices[str(year)] == indices["2029"]
        window = acs_document["diagnostics_by_year"][str(year)]["window"]
        assert window["observed_years"] == []
        assert window["projected_years"] == list(range(year - 5, year))
    assert acs_document["diagnostics_by_year"]["2029"]["window"] == {
        "start": 2024,
        "end": 2028,
        "observed_years": [2024],
        "projected_years": [2025, 2026, 2027, 2028],
    }
    assert "same 2024 donors" in disclosure["interpretation"]
    assert (
        "not independent future survey observations"
        in disclosure["interpretation"]
    )


@pytest.mark.parametrize("scenario", ["ce_trend", "zero_real"])
def test_constant_rent_indices_allow_housing_share_and_factor_movement(
    forecast_document, scenario
):
    disclosure = forecast_document["assumptions"]["acs"][
        "relative_index_stabilization"
    ]
    assert (
        disclosure["geographic_factor_formula"]
        == "1 + housing_share * (rent_index - 1)"
    )
    assert (
        "housing share can still move"
        in disclosure["geographic_factor_caveat"]
    )
    assert (
        "Floating-point noise is not substantive movement"
        in disclosure["geographic_factor_caveat"]
    )
    years = forecast_document["scenarios"][scenario]["years"]
    before, after = years["2029"], years["2030"]
    assert before["rent_indices"] == after["rent_indices"]
    forecast = load_forecast()
    for tenure in before["housing_shares"]:
        assert (
            abs(
                after["housing_shares"][tenure]
                - before["housing_shares"][tenure]
            )
            > 1e-9
        )
        expected = [
            1
            + entry["housing_shares"][tenure]
            * (entry["rent_indices"]["25002"] - 1)
            for entry in (before, after)
        ]
        actual = [
            forecast.geography_factor(
                year, tenure, kind="metro", geoid="25002", scenario=scenario
            )["factor"]
            for year in (2029, 2030)
        ]
        assert actual == expected
        assert abs(actual[1] - actual[0]) > 1e-10


def test_disclosure_derives_convergence_from_values_without_changing_them(
    acs_document,
):
    candidate = deepcopy(acs_document)
    candidate["indices_by_year"]["2031"]["25002"] += 0.001
    before = deepcopy(candidate["indices_by_year"])
    add_horizon_disclosures(candidate)
    disclosure = candidate["metadata"]["relative_index_stabilization"]
    assert disclosure["first_constant_index_year"] == 2032
    assert disclosure["first_unchanged_transition_year"] == 2033
    assert disclosure["first_all_projected_window_year"] == 2030
    assert candidate["indices_by_year"] == before


def test_ce_component_tracks_the_published_source_accessor(forecast_document):
    component = json.loads((CURRENT / "ce_rolling_forecast.json").read_text())
    path = "spm_calculator/published_thresholds.py"
    digest = hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
    assert component["code_sha256"][path] == digest
    assert forecast_document["code_sha256"][path] == digest


def test_2021_exact_normalization_receipt_binds_current_cache_identity():
    receipt = json.loads(
        (CURRENT / "acs_2021_normalization_receipt.json").read_text()
    )
    manifest_path = CURRENT / "acs_normalized_products.json"
    assert (
        receipt["manifest_sha256"]
        == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    )
    products = json.loads(manifest_path.read_text())
    historical = products["2021"]
    assert receipt["retained_normalized_sha256"] == historical["sha256"]
    for key in (
        "normalization_logic_sha256",
        "parser_sha256",
        "source_sha256",
        "columns",
    ):
        assert receipt[key] == historical[key]
    assert receipt["normalization_logic_sha256"] == (
        acs_forecast_sources.normalization_logic_sha256(2021)
    )
    assert (
        receipt["parser_sha256"]
        == hashlib.sha256(
            Path(acs_forecast_sources.__file__).read_bytes()
        ).hexdigest()
    )
    source_bundle = acs_forecast_sources.load_historical_source_bundle()
    assert receipt["source_sha256"] == [
        source["sha256"]
        for source in source_bundle["sources"]
        if source["id"] == "2021/csv_hus.zip"
    ]
    assert receipt["records"] == 616858
    assert len(receipt["columns"]) == len(set(receipt["columns"])) == 16
    assert receipt["check_exact"] is True
    assert receipt["check_dtype"] is False
    # Exact reparse evidence belongs to the archived run. The current receipt
    # binds the same retained cache through an explicit equivalent-code step.
    original_link = receipt["original_reparse_evidence"]
    original_bytes = (ROOT / original_link["path"]).read_bytes()
    assert (
        hashlib.sha256(original_bytes).hexdigest() == (original_link["sha256"])
    )
    original = json.loads(original_bytes)
    assert original["verification"] == (
        "raw-source reparse exactly equals all retained normalized records"
    )
    assert original["parser_sha256"] == (
        "d96fb6556f3a019025c2bd7dfdef5b9f5d05a7a4d819ab568f3a8bc41bf2aed3"
    )
    for key in (
        "check_exact",
        "check_dtype",
        "records",
        "columns",
        "retained_normalized_sha256",
        "normalization_logic_sha256",
        "source_sha256",
    ):
        assert receipt[key] == original[key]
    assert receipt["raw_source_reparse_performed"] is False
    assert receipt["verification"] == (
        "Inherited exact raw-reparse evidence from the original receipt; "
        "current parser identity admitted by equivalent-code adaptation. "
        "No raw-source reparse performed by this adaptation."
    )
    chain = receipt["provenance_adaptation"]
    assert chain == historical["provenance_adaptation"]
    adaptation_bytes = (ROOT / chain["path"]).read_bytes()
    assert hashlib.sha256(adaptation_bytes).hexdigest() == chain["sha256"]
    adaptation = json.loads(adaptation_bytes)
    assert adaptation["raw_source_reparse_performed"] is False
    assert adaptation["original"]["normalization_receipt"] == original_link
    assert adaptation["current_source"] == {
        "path": "spm_calculator/acs_forecast_sources.py",
        "sha256": receipt["parser_sha256"],
    }
    assert receipt["unchanged_product_receipts"] == [2022, 2023, 2024]
    for vintage in receipt["unchanged_product_receipts"]:
        logic = acs_forecast_sources.normalization_logic_sha256(vintage)
        assert products[str(vintage)]["normalization_logic_sha256"] == logic
        assert logic != historical["normalization_logic_sha256"]
