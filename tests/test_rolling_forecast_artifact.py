"""Release gates for complete, reproducible forecast inputs and evaluations."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from scripts import build_rolling_forecast as builder
from spm_calculator.release import load_release

ROOT = Path(__file__).parents[1]


@pytest.fixture(scope="module")
def components():
    ce = json.loads(builder.CE_PATH.read_text())
    acs = json.loads(builder.ACS_PATH.read_text())
    areas = load_release().to_dict()["geographies"]["metro"]["areas"]
    return ce, acs, areas


def test_complete_real_evaluation_and_all_horizons(components):
    result = builder.validate_evaluations(*components)
    assert result["ce"]["fold_count"] == 21
    assert result["ce"]["fold_tenure_observation_count"] == 63
    assert result["acs"]["area_count"] == 341
    assert result["ce"]["by_horizon"]["5"]["fold_count"] == 2
    for result in (result["ce"], result["acs"]):
        assert result["status"] == "complete"
        assert result["unvalidated"] is False


@pytest.mark.parametrize(
    "failure",
    [
        "missing_fold",
        "missing_tenure",
        "wrong_shrinkage",
        "missing_area",
        "blocked",
        "bad_metric_flag",
    ],
)
def test_incomplete_or_inconsistent_evaluation_cannot_ship(
    components, failure
):
    ce, acs, areas = copy.deepcopy(components)
    if failure == "missing_fold":
        ce["backtest"]["folds"].pop()
    elif failure == "missing_tenure":
        ce["backtest"]["folds"][0]["scenarios"]["ce_trend"][
            "errors_by_tenure"
        ].pop("renter")
    elif failure == "wrong_shrinkage":
        ce["backtest"]["folds"][0]["scenarios"]["ce_trend"]["shrinkage"] = 1
    elif failure == "missing_area":
        acs["backtest"]["areas"].pop("41860")
    elif failure == "blocked":
        acs["backtest"]["status"] = "blocked"
    elif failure == "bad_metric_flag":
        candidate = ce["backtest"]["scenarios"]["ce_trend"]
        candidate["beats_baseline"] = not candidate["beats_baseline"]
    with pytest.raises(ValueError):
        builder.validate_evaluations(ce, acs, areas)


def test_cpi_receipt_is_package_relative_and_matches_official_m13():
    checks = builder.verify_source_checks()
    assert not Path(checks["bls_cpi"]["path"]).is_absolute()
    assert (
        checks["bls_cpi"]["sha256"]
        == "482678b4dc2070b434ff44b2d09417992b63546dea0ac3a30ec300244e14415e"
    )


def test_different_packaged_cpi_cannot_be_labeled_observed(monkeypatch):
    read = builder.read_json

    def altered(path):
        result = read(path)
        if Path(path).name == "cpi_annual.json":
            result["series"]["CUUR0000SA0"]["2025"] = 322
        return result

    monkeypatch.setattr(builder, "read_json", altered)
    with pytest.raises(ValueError, match="official annual values"):
        builder.verify_source_checks()


@pytest.mark.parametrize(
    "path,digest",
    [
        (
            "spm_calculator/data/releases/spm-2026-09-08.json",
            "a12113b5b574e0d4371814f2c3f127b00f91016bddc94c0f6459b7a7742cf02d",
        ),
        (
            "spm_calculator/data/current/ce_replication_2019_2025.json",
            "d04e2a61552795f9d90e282045610774a4ff68f67b97067677358741f58a289c",
        ),
        (
            "spm_calculator/data/nowcast/nowcast_2025.json",
            "17b4f9c2283234c9774ed9b969c1476b4b87b4bc5686c5c4ddd98441f93a84a8",
        ),
        (
            "spm_calculator/data/nowcast/replication_thresholds_2024_2025.json",
            "adee1d02b15082c90a6fa742f959925d3e8b0e5c0c292ba50666f21dee2d655c",
        ),
        (
            "spm_calculator/data/nowcast/backtest_2020_2024.json",
            "74e9b08e9c0a8c4f6b8e44349de74ab927fb89519aa510b1f3da3ac86d320e79",
        ),
    ],
)
def test_published_and_archived_artifact_bytes_remain_unchanged(path, digest):
    # Active web exports are regenerated from the canonical artifact. The
    # sealed release and scientific archives retain historical evidence.
    assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest


def test_committed_forecast_rebuilds_offline(monkeypatch):
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError("Assembly must remain offline")

    monkeypatch.setattr(socket, "socket", forbidden)
    assert builder.build_document() == json.loads(builder.OUT.read_text())


def test_canonical_year_coverage_publication_status_and_county_assignment():
    from spm_calculator.forecast_inputs import load_horizon_inputs
    from spm_calculator.rolling_forecast import load_forecast

    forecast = load_forecast()
    horizon = load_horizon_inputs()
    assert forecast.years == tuple(range(2022, 2037))
    for scenario in ("ce_trend", "zero_real"):
        for year in forecast.years:
            entry = forecast.entry(year, scenario=scenario)
            areas = forecast.areas_for_year(year, scenario=scenario)
            assert len(areas) == 349
            published_count = sum(
                row["official_published_area"] for row in areas.values()
            )
            assert published_count == (342 if year == 2022 else 341)
            assert sum(
                row["status"] == "modeled_unanchored" for row in areas.values()
            ) == (7 if year == 2022 else 8)
            assert entry["national_status"] == (
                "published" if year <= 2025 else "forecast"
            )
            assert entry["housing_share_status"] == (
                "published_anchor" if year <= 2025 else "modeled"
            )
            if year <= 2025:
                assert (
                    entry["housing_shares"]
                    == horizon["published_housing_shares"][str(year)]
                )
            if year <= 2024:
                expected = horizon["historical"][str(year)]["rent_indices"]
                assert {
                    code: entry["rent_indices"][code] for code in expected
                } == expected
            assert set(entry["median_diagnostics"]) == set(
                entry["rent_indices"]
            )
    assert forecast.resolve_county(2022, "45085")["area_id"] == "45001"
    assert (
        forecast.resolve_county(2023, "45085")["area_id"]
        == "modeled_residual_metro:45"
    )
    with pytest.raises(ValueError, match="unavailable"):
        forecast.geography_factor(2023, "renter", kind="metro", geoid="45001")


def test_frozen_2025_rent_vector_and_prospective_thin_support_policy(
    components,
):
    _, acs, _ = components
    frozen = json.loads(
        (
            ROOT / "spm_calculator/data/current/acs_frozen_forward_2025.json"
        ).read_text()
    )
    assert acs["forward_test"] == frozen
    assert len(frozen["frozen_prediction_indices"]) == 341
    assert acs["metadata"]["thin_support_policy"].startswith(
        "Retain the computed"
    )
    areas = acs["diagnostics_by_year"]["2035"]["areas"]
    for code in ("modeled_residual_metro:24", "modeled_residual_metro:45"):
        assert areas[code]["thin_support"]
        assert areas[code]["donor_support"]["kish_effective_count"] > 0
        assert acs["indices_by_year"]["2035"][code] > 0
