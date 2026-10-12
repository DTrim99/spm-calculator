"""Compact UI inputs preserve canonical calculations, warnings and provenance."""

import hashlib
import json
from pathlib import Path

import pytest

from scripts.export_web_release import (
    MEDIAN_FIELDS,
    METRIC_FIELDS,
    build_config,
    encode_config,
    package_distribution,
    pick,
)
from spm_calculator.release import SPMUnit, canonical_bytes
from spm_calculator.rolling_forecast import load_forecast

REPO = Path(__file__).parents[1]
CONFIG = REPO / "web/public/data/release_config.json"
YEARS = range(2022, 2036)
PUBLISHED_2025 = {
    "owner_with_mortgage": 41322.707394,
    "owner_without_mortgage": 34325.99772,
    "renter": 41700.555713,
}
PUBLISHED_SHARES_2025 = {
    "owner_with_mortgage": 0.4285074824,
    "owner_without_mortgage": 0.3120194707,
    "renter": 0.4336857704,
}


@pytest.fixture(scope="module")
def config():
    return build_config()


@pytest.fixture(scope="module")
def projection():
    return load_forecast()


@pytest.fixture(scope="module")
def document(projection):
    return projection.to_dict()


# Retain the source archive's independent regression coverage. These values
# are no longer inputs to the browser's canonical forecast export.
def test_canonical_thresholds_use_published_2025_values(config):
    entry = config["forecast"]["scenarios"]["ce_trend"]["years"]["2025"]
    assert entry["thresholds"] == pytest.approx(PUBLISHED_2025)


def test_archived_nowcast_remains_separate_from_canonical_forecast(config):
    archive = json.loads(
        (REPO / "spm_calculator/data/nowcast/nowcast_2025.json").read_text()
    )
    actual = config["forecast"]["scenarios"]["ce_trend"]["years"]["2025"][
        "thresholds"
    ]
    assert archive["superseded_by"]["series"] == "bls-published-2025"
    errors = {
        tenure: (value / actual[tenure] - 1) * 100
        for tenure, value in archive["values"].items()
    }
    assert sum(abs(value) for value in errors.values()) / 3 == pytest.approx(
        1.170566081645456
    )
    assert archive["values"]["renter"] == pytest.approx(40755.97769114959)
    assert errors["renter"] == pytest.approx(-2.265144926008611)


def test_committed_browser_export_matches_canonical_artifact(config):
    # Keep the deployed package pin until the next release reaches PyPI.
    # A package metadata update may precede that separate browser promotion.
    # Every scientific input must still match the current canonical artifact.
    published = {
        **config,
        "packageVersion": "1.0.0.post1",
        "packageDistribution": package_distribution(
            "1.0.0.post1", "1.0.0.post1"
        ),
    }
    assert CONFIG.read_bytes() == encode_config(published)
    assert published["packageDistribution"] == {
        "status": "published",
        "version": "1.0.0.post1",
        "publishedVersion": "1.0.0.post1",
        "pypiUrl": "https://pypi.org/project/spm-calculator/1.0.0.post1/",
    }
    assert config["packageVersion"] == "1.0.1"
    assert config["packageDistribution"]["status"] == "local_preview"
    assert {
        key: value
        for key, value in published.items()
        if key not in {"packageVersion", "packageDistribution"}
    } == {
        key: value
        for key, value in config.items()
        if key not in {"packageVersion", "packageDistribution"}
    }
    assert config["schemaVersion"] == 2
    assert config["forecast"]["schemaVersion"] == 2
    assert config["availableYears"] == list(YEARS)
    assert set(config["areasByYear"]) == {str(year) for year in YEARS}


def test_browser_has_no_legacy_execution_inputs(config):
    assert (
        not {
            "baseThresholds",
            "housingSharesByYear",
            "housingShareProvenanceByYear",
            "metroAreas",
            "metroData",
            "metroDataYear",
            "nowcast",
            "nowcastEvaluation",
            "acsLookup",
            "state",
            "county",
            "congressional_district",
        }
        & config.keys()
    )
    assert (
        not {"thresholdsByYear", "factorsByYear"} & config["forecast"].keys()
    )
    assert not (REPO / "web/public/data/spm_config.json").exists()
    assert not (REPO / "web/public/data/metro_geoadj.json").exists()


@pytest.mark.parametrize("year", YEARS)
def test_area_menu_is_year_specific_and_preserves_status(
    config, projection, year
):
    areas = config["areasByYear"][str(year)]
    assert areas == {
        area_id: pick(area, ("name", "area_type"))
        for area_id, area in projection.areas_for_year(year).items()
    }
    entry = projection.entry(year)
    assert areas.keys() == entry["rent_indices"].keys()
    assert areas.keys() == entry["geography_by_area"].keys()
    assert len(areas) == 349
    metadata = config["forecast"]["scenarios"]["ce_trend"]["years"][str(year)][
        "geography_by_area"
    ]
    published_count = sum(
        a["official_published_area"] for a in metadata.values()
    )
    assert published_count == (342 if year == 2022 else 341)
    modeled = [
        {**areas[area_id], **area}
        for area_id, area in metadata.items()
        if not area["official_published_area"]
    ]
    assert len(modeled) == (7 if year == 2022 else 8)
    for area in modeled:
        assert area["area_type"] == "modeled_residual_metro"
        assert area["status"] == "modeled_unanchored"
        assert area["anchor_status"] == "modeled_unanchored"
    assert {a["area_type"] for a in areas.values()} == {
        "msa",
        "state_metro_residual",
        "state_nonmetro",
        "modeled_residual_metro",
    }


def test_area_removed_from_later_year_is_not_in_union_menu(config):
    assert "45001" in config["areasByYear"]["2022"]
    assert "modeled_residual_metro:45" not in config["areasByYear"]["2022"]
    for year in range(2023, 2036):
        assert "45001" not in config["areasByYear"][str(year)]
        assert "modeled_residual_metro:45" in config["areasByYear"][str(year)]


def test_forecast_preserves_canonical_inputs_and_visible_provenance(
    config, document
):
    forecast = config["forecast"]
    assert forecast["method"] == "rolling_ce_acs_v1"
    for exported, canonical in (
        ("contentSha256", "content_sha256"),
        ("baseReleaseSha256", "base_release_sha256"),
        ("assumptionSha256", "assumption_sha256"),
        ("componentSha256", "component_sha256"),
        ("codeSha256", "code_sha256"),
        ("informationDate", "information_date"),
    ):
        assert forecast[exported] == document[canonical]
    assert forecast["defaultScenario"] == "ce_trend"
    assert forecast["latestPublishedYear"] == forecast["baseYear"] == 2025
    assert forecast["geographyAnchorYear"] == 2024
    assert forecast["sources"] == [
        pick(source, ("id", "title", "label", "url", "sha256"))
        for source in document["sources"]
    ]
    for identity, scenario in document["scenarios"].items():
        exported = forecast["scenarios"][identity]
        assert exported["realGrowthRate"] == scenario["real_growth_rate"]
        assert set(exported["years"]) == {str(year) for year in YEARS}
        for year, original in scenario["years"].items():
            entry = exported["years"][year]
            for key in (
                "thresholds",
                "housing_shares",
                "rent_indices",
                "geography_by_area",
                "national_status",
                "housing_share_status",
                "geography_status",
                "national_source_ids",
                "housing_share_source_ids",
            ):
                assert entry[key] == original[key]
            for window in ("ce_window", "acs_window"):
                assert all(
                    value == original[window][key]
                    for key, value in entry[window].items()
                )
    assert (
        forecast["assumptionSha256"]
        == hashlib.sha256(canonical_bytes(document["assumptions"])).hexdigest()
    )


def test_only_displayed_validation_and_fit_metrics_are_exported(
    config, document
):
    exported = config["forecast"]["validation"]
    original = document["validation"]
    assert exported["acs"] == original["acs"]
    for group in (None, *original["ce"]["by_horizon"]):
        before = (
            original["ce"]
            if group is None
            else original["ce"]["by_horizon"][group]
        )
        after = (
            exported["ce"]
            if group is None
            else exported["ce"]["by_horizon"][group]
        )
        assert after["baseline"] == pick(before["baseline"], METRIC_FIELDS)
        for identity, scenario in before["scenarios"].items():
            assert after["scenarios"][identity] == pick(
                scenario, METRIC_FIELDS
            )
    assert config["forecast"]["realGrowthDiagnostics"]["fits"] == [
        pick(fit, ("label", "realGrowthRate", "olsSlopeStandardError", "n"))
        for fit in document["real_growth_diagnostics"]["fits"]
    ]


def test_compact_transport_has_bounded_size_and_no_scientific_records(config):
    encoded = encode_config(config)
    wire = json.loads(encoded)
    assert wire["uiSchemaVersion"] == 1
    assert len(encoded) < 1_500_000
    assert "areasByYear" not in wire
    assert len(wire["areaMenus"]) == 2
    assert len(wire["geographies"]) < len(YEARS)
    for scientific_field in (
        "allocated_weight_by_cohort",
        "eligible_record_count_by_cohort",
        "rent_index_topcode_sources",
        "component_topcoding_weight_shares",
        "county_assignments",
        "folds",
        "forwardTest",
        "rentSensitivity",
    ):
        assert f'"{scientific_field}"' not in encoded.decode()
    for scenario in wire["forecast"]["scenarios"].values():
        for entry in scenario["years"].values():
            assert "rent_indices" not in entry
            assert 0 <= entry["geographyRef"] < len(wire["geographies"])


def test_compaction_does_not_assume_scenarios_share_geography(config):
    import copy

    changed = copy.deepcopy(config)
    changed["forecast"]["scenarios"]["zero_real"]["years"]["2035"][
        "rent_indices"
    ]["35620"] += 0.01
    wire = json.loads(encode_config(changed))
    scenarios = wire["forecast"]["scenarios"]
    left = scenarios["ce_trend"]["years"]["2035"]["geographyRef"]
    right = scenarios["zero_real"]["years"]["2035"]["geographyRef"]
    assert left != right
    assert (
        wire["geographies"][right]["rent_indices"]["35620"]
        == changed["forecast"]["scenarios"]["zero_real"]["years"]["2035"][
            "rent_indices"
        ]["35620"]
    )


def test_full_scientific_download_is_byte_identical_and_content_pinned(
    config, document
):
    original = (
        REPO / "spm_calculator/data/current/rolling_forecast_2026_09_09.json"
    ).read_bytes()
    audit = config["forecast"]["auditArtifact"]
    downloaded = REPO / "web/public" / audit["url"].lstrip("/")
    assert downloaded.read_bytes() == original
    assert audit["sha256"] == hashlib.sha256(original).hexdigest()
    assert audit["sha256"] in audit["name"]
    assert audit["bytes"] == len(original)
    assert (
        json.loads(downloaded.read_bytes())["content_sha256"]
        == document["content_sha256"]
    )


def test_visible_diagnostics_preserve_values_without_unused_area_records(
    config, document
):
    for identity, scenario in document["scenarios"].items():
        for year, original in scenario["years"].items():
            compact = config["forecast"]["scenarios"][identity]["years"][year][
                "median_diagnostics"
            ]
            for area_id, diagnostic in compact.items():
                assert diagnostic == pick(
                    original["median_diagnostics"][area_id], MEDIAN_FIELDS
                )
                assert (
                    original["geography_by_area"][area_id]["status"]
                    != "published_anchor"
                )


def test_historical_series_breaks_preserved_from_canonical(config, document):
    for year, area_id in (
        ("2022", "25002"),
        ("2023", "25002"),
        ("2022", "45001"),
        ("2023", "modeled_residual_metro:45"),
    ):
        expected = document["scenarios"]["ce_trend"]["years"][year][
            "geography_by_area"
        ][area_id]["series_breaks"]
        actual = config["forecast"]["scenarios"]["ce_trend"]["years"][year][
            "geography_by_area"
        ][area_id]["series_breaks"]
        assert actual == expected
        assert actual[0]["from_year"] == 2022
        assert actual[0]["to_year"] == 2023


def test_component_statuses_are_independent_and_area_specific(config):
    years = config["forecast"]["scenarios"]["ce_trend"]["years"]
    for year in range(2022, 2026):
        entry = years[str(year)]
        assert entry["national_status"] == "published"
        assert entry["housing_share_status"] == "published_anchor"
        assert entry["national_source_ids"] == ["bls-spm-thresholds"]
        assert entry["housing_share_source_ids"] == ["bls-spm-shares"]
        assert entry["geography_status"] == "mixed"
    for year in range(2022, 2025):
        geography = years[str(year)]["geography_by_area"]
        assert geography["35620"]["status"] == "published_anchor"
        assert (
            geography["modeled_residual_metro:01"]["status"]
            == "modeled_unanchored"
        )
    entry = years["2025"]
    assert entry["thresholds"] == pytest.approx(PUBLISHED_2025)
    assert entry["housing_shares"] == pytest.approx(PUBLISHED_SHARES_2025)
    assert entry["geography_by_area"]["35620"]["status"] == "modeled"
    assert (
        entry["geography_by_area"]["35620"]["anchor_status"]
        == "published_anchor"
    )
    for year in range(2026, 2036):
        assert years[str(year)]["national_status"] == "forecast"
        assert years[str(year)]["housing_share_status"] == "modeled"


def test_future_cbo_ratios_are_explicit_uniform_component_and_rent_assumptions(
    config,
    document,
):
    forecast = config["forecast"]
    price = document["assumptions"]["ce"]["price_source"]
    assert "uniformly" in price["component_assumption"]
    assert "not CBO component forecasts" in price["component_assumption"]
    assert "not CBO local rent forecasts" in price["rent_assumption"]
    levels = price["cbo_index_levels"]
    for year in range(2026, 2036):
        growth = levels[str(year)] / levels[str(year - 1)] - 1
        assert forecast["cpiProjections"][str(year)] == pytest.approx(growth)
        assert document["assumptions"]["acs"]["nominal_rent_growth_by_year"][
            str(year)
        ] == pytest.approx(growth)


def test_preview_requires_explicit_publication_of_exact_package_version(
    config,
):
    version = config["packageVersion"]
    assert config["packageDistribution"] == {
        "status": "local_preview",
        "version": version,
        "publishedVersion": None,
        "pypiUrl": None,
    }
    assert package_distribution(version, version) == {
        "status": "published",
        "version": version,
        "publishedVersion": version,
        "pypiUrl": f"https://pypi.org/project/spm-calculator/{version}/",
    }
    with pytest.raises(ValueError, match="must match pyproject.toml"):
        package_distribution(version, "0.0.0-other-version")


@pytest.mark.parametrize("scenario", ["ce_trend", "zero_real"])
@pytest.mark.parametrize("year", YEARS)
def test_browser_inputs_reproduce_standalone_local_threshold(
    config, projection, year, scenario
):
    entry = config["forecast"]["scenarios"][scenario]["years"][str(year)]
    for area in ("35620", "41860", "modeled_residual_metro:01"):
        unit = SPMUnit(
            "family",
            1,
            2,
            "renter",
            year,
            geography_kind="metro",
            geography_id=area,
        )
        result = projection.calculate_unit(unit, scenario=scenario)
        factor = 1 + entry["housing_shares"]["renter"] * (
            entry["rent_indices"][area] - 1
        )
        browser_amount = (
            entry["thresholds"]["renter"]
            * result["equivalence_factor"]
            * factor
        )
        assert browser_amount == pytest.approx(result["threshold"])
        assert entry["ce_window"]["end"] == f"{year}Q1"
        assert entry["acs_window"]["end"] == year - 1


def test_export_rejects_a_source_change_during_derivation(
    tmp_path, monkeypatch
):
    from scripts import export_web_release as exporter

    source = tmp_path / "forecast.json"
    source_bytes = exporter.FORECAST.read_bytes()
    source.write_bytes(source_bytes)
    output = tmp_path / "public/data/release_config.json"
    original_build = exporter.build_config

    def build_then_change_source(**kwargs):
        config = original_build(**kwargs)
        source.write_bytes(source_bytes + b"\n")
        return config

    monkeypatch.setattr(exporter, "FORECAST", source)
    monkeypatch.setattr(exporter, "OUT", output)
    monkeypatch.setattr(exporter, "build_config", build_then_change_source)
    with pytest.raises(SystemExit, match="changed during export"):
        exporter.export()
    assert not output.exists()
    assert not output.parent.exists()


def test_export_check_requires_an_intact_pinned_download(
    tmp_path, monkeypatch, config
):
    from scripts import export_web_release as exporter

    output = tmp_path / "release_config.json"
    output.write_bytes(encode_config(config))
    monkeypatch.setattr(exporter, "OUT", output)
    with pytest.raises(SystemExit, match="Pinned audit download differs"):
        exporter.export(check=True)
    audit = (
        output.parent
        / "canonical"
        / config["forecast"]["auditArtifact"]["name"]
    )
    audit.parent.mkdir()
    audit.write_bytes(b"corrupt")
    with pytest.raises(
        SystemExit, match="Existing pinned audit download is corrupt"
    ):
        exporter.export()
    assert audit.read_bytes() == b"corrupt"
    assert output.read_bytes() == encode_config(config)
