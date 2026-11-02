"""Bind the 2036 extension to its archived evidence and its documented rule."""

import copy
import hashlib
import itertools
import json

import pytest

from scripts import verify_horizon_extension as verifier
from spm_calculator.forecast_inputs import load_horizon_inputs
from spm_calculator.release import canonical_bytes
from spm_calculator.rolling_forecast import SPMForecast, seal_forecast

ADDED = str(verifier.ADDED_YEAR)
LAST = str(max(verifier.RETAINED_YEARS))
ACS = "spm_calculator/data/current/acs_rolling_forecast.json"
CE = "spm_calculator/data/current/ce_rolling_forecast.json"
CPI_SERIES = (
    "CUUR0000SA0",
    "CUUR0000SAA",
    "CUUR0000SAF",
    "CUUR0000SAF11",
    "CUUR0000SAH1",
    "CUUR0000SAH2",
    "CUUR0000SEED",
    "CUUR0000SEFV",
)
CODE = {"scripts/build_acs_forecast.py", "spm_calculator/forecast_inputs.py"}
INPUT_SOURCES = {
    "sources/id=acs-2021-source-inputs/sha256",
    "sources/id=forecast-horizon-inputs/sha256",
}


def _added(*records):
    return {f"{record}/{ADDED}" for record in records}


# Every path at which a current file may differ from its retained bytes.
EXPECTED_DIFFERENCES = {
    verifier.CURRENT: {
        "assumption_sha256",
        "content_sha256",
        "county_assignments/sha256",
        "assumptions/acs/provenance_adaptation",
        "assumptions/acs/relative_index_stabilization/through_year",
        "sources/id=acs-code-identity-adaptation",
        "sources/id=ce-020/sha256",
        *INPUT_SOURCES,
        *(f"assumptions/acs/generator_identity/{name}" for name in CODE),
        *(f"code_sha256/{name}" for name in verifier.EXTENDED_CODE),
        *(f"component_sha256/{path}" for path in verifier.REBUILT_COMPONENTS),
        *_added(
            "assumptions/acs/nominal_rent_growth_by_year",
            "assumptions/acs/price_growth_by_year",
            "assumptions/acs/price_inputs/forecast_source/annual_growth_by_year",
            "assumptions/acs/puma_vintage_by_spm_year",
            *(f"assumptions/ce/annual_cpi/{s}" for s in CPI_SERIES),
            "assumptions/ce/inflation_rates",
            "assumptions/ce/price_source/annual_growth_by_year",
            "assumptions/price_projections",
            "county_assignments/year_maps",
            "rent_sensitivity/index_difference_by_year",
            "rent_sensitivity/nominal_rent_growth_by_year",
            "rent_sensitivity/price_growth_by_year",
            "scenarios/ce_trend/years",
            "scenarios/zero_real/years",
        ),
    },
    CE: {
        "code_sha256/spm_calculator/ce_forecast.py",
        "code_sha256/spm_calculator/forecast_inputs.py",
        "sources/20/sha256",
        *_added(
            *(f"assumptions/annual_cpi/{s}" for s in CPI_SERIES),
            "assumptions/inflation_rates",
            "assumptions/price_source/annual_growth_by_year",
            "scenarios/ce_trend/years",
            "scenarios/zero_real/years",
        ),
    },
    ACS: {
        "metadata/provenance_adaptation",
        "metadata/relative_index_stabilization/through_year",
        "sources/id=acs-code-identity-adaptation",
        *INPUT_SOURCES,
        *(f"metadata/generator_identity/{name}" for name in CODE),
        *_added(
            "diagnostics_by_year",
            "geography_by_year",
            "indices_by_year",
            "metadata/nominal_rent_growth_by_year",
            "metadata/price_growth_by_year",
            "metadata/price_inputs/forecast_source/annual_growth_by_year",
            "metadata/puma_vintage_by_spm_year",
            "sensitivity/rent_plus_2pp/diagnostics_by_year",
            "sensitivity/rent_plus_2pp/index_difference_by_year",
            "sensitivity/rent_plus_2pp/indices_by_year",
            "sensitivity/rent_plus_2pp/metadata/nominal_rent_growth_by_year",
            "sensitivity/rent_plus_2pp/metadata/price_growth_by_year",
        ),
    },
}


def _by_id(items):
    if all(
        isinstance(x, dict) and isinstance(x.get("id"), str) for x in items
    ):
        return {f"id={x['id']}": x for x in items}
    return None


def differences(before, after, path=""):
    """Yield each path where two JSON documents differ in exact encoding."""
    if isinstance(before, list) and isinstance(after, list):
        keyed = (_by_id(before), _by_id(after))
        if None not in keyed:
            before, after = keyed
        elif len(before) == len(after):
            before, after = dict(enumerate(before)), dict(enumerate(after))
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(before.keys() | after.keys(), key=str):
            child = f"{path}/{key}" if path else str(key)
            if key not in before or key not in after:
                yield child
            else:
                yield from differences(before[key], after[key], child)
    elif canonical_bytes(before) != canonical_bytes(after):
        yield path


def _current(path):
    return json.loads((verifier.ROOT / path).read_bytes())


@pytest.fixture(scope="module")
def current():
    return _current(verifier.CURRENT)


@pytest.mark.parametrize("path", sorted(EXPECTED_DIFFERENCES))
def test_retained_content_equals_the_archived_bytes(path):
    archived = json.loads(verifier.retained_bytes(path))
    assert (
        set(differences(archived, _current(path)))
        == EXPECTED_DIFFERENCES[path]
    )


def test_added_year_follows_the_documented_rule(current):
    assumptions, acs = current["assumptions"], current["assumptions"]["acs"]
    pinned = load_horizon_inputs()["prices"]
    levels = assumptions["ce"]["price_source"]["cbo_index_levels"]
    growth = assumptions["price_projections"][ADDED]
    assert levels == pinned["cbo_index_levels"]
    assert growth == levels[ADDED] / levels[LAST] - 1
    assert growth == pinned["annual_growth_by_year"][ADDED]
    for record in (
        assumptions["ce"]["inflation_rates"],
        assumptions["ce"]["price_source"]["annual_growth_by_year"],
        acs["price_growth_by_year"],
        acs["nominal_rent_growth_by_year"],
        acs["price_inputs"]["forecast_source"]["annual_growth_by_year"],
        current["rent_sensitivity"]["price_growth_by_year"],
    ):
        assert record[ADDED] == growth
    sensitivity = current["rent_sensitivity"]["nominal_rent_growth_by_year"]
    assert sensitivity[ADDED] == pytest.approx(growth + 0.02, rel=1e-12)
    for series in assumptions["ce"]["annual_cpi"].values():
        assert series[ADDED] == pytest.approx(
            series[LAST] * (1 + growth), rel=1e-12
        )
    vintages = acs["puma_vintage_by_spm_year"]
    assert vintages[ADDED] == vintages[LAST]
    year_maps = current["county_assignments"]["year_maps"]
    assert year_maps[ADDED] == year_maps[LAST]
    for scenario in current["scenarios"].values():
        last, entry = scenario["years"][LAST], scenario["years"][ADDED]
        # zero_real: CPI-U only; ce_trend: CPI-U times the real trend.
        factor = (1 + growth) * (1 + scenario["real_growth_rate"])
        for tenure, value in entry["thresholds"].items():
            expected = last["thresholds"][tenure] * factor
            assert value == pytest.approx(expected, rel=1e-12, abs=0)
        assert entry["housing_shares"] == pytest.approx(
            last["housing_shares"], rel=1e-12, abs=0
        )
        assert entry["rent_indices"] == last["rent_indices"]
        assert entry["geography_by_area"] == last["geography_by_area"]


def test_receipt_builder_reproduces_the_committed_receipt(monkeypatch):
    # Full 2036 grid (150,768 calculations); no retained grid.
    full_grid = verifier._grid

    def added_year_only(forecast, scenario, year):
        if year == verifier.ADDED_YEAR:
            return full_grid(forecast, scenario, year)
        return iter(())

    monkeypatch.setattr(verifier, "_grid", added_year_only)
    receipt = verifier.build_receipt(
        (verifier.ROOT / verifier.BASELINE).read_bytes(),
        (verifier.ROOT / verifier.CURRENT).read_bytes(),
    )
    committed = json.loads((verifier.ROOT / verifier.RECEIPT).read_bytes())
    amounts = receipt["horizon_comparison"].pop("amount_comparison")
    pinned = committed["horizon_comparison"].pop("amount_comparison")
    assert receipt == committed
    for key in (
        "added_year_calculations",
        "added_year_sha256_big_endian_float64",
        "area_counts",
        "fields",
        "composition_grid",
        "retained_years",
        "added_year",
    ):
        assert amounts[key] == pinned[key]
    assert amounts["retained_calculations"] == 0
    # The committed retained grid must say that nothing moved.
    assert (
        pinned["original_sha256_big_endian_float64"]
        == pinned["current_sha256_big_endian_float64"]
    )
    assert pinned["exact_field_comparisons"] == pinned[
        "retained_calculations"
    ] * len(pinned["fields"])
    for key in (
        "all_result_fields_equal_except",
        "scenario_count",
        "resources",
        "ordering",
        "max_absolute_difference",
    ):
        assert amounts[key] == pinned[key]


@pytest.mark.parametrize(
    "change,message",
    [
        ("source", "External source pins changed"),
        ("normalized", "Normalized ACS products changed"),
    ],
)
def test_rebuild_facts_reject_a_changed_external_input(
    current, change, message
):
    baseline = json.loads((verifier.ROOT / verifier.BASELINE).read_bytes())
    changed = copy.deepcopy(current)
    digest = hashlib.sha256(b"changed").hexdigest()
    if change == "source":
        source = next(s for s in changed["sources"] if s["id"] == "ce-000")
        source["sha256"] = digest
    else:
        changed["component_sha256"][verifier.NORMALIZED_PRODUCTS] = digest
    with pytest.raises(ValueError, match=message):
        verifier.rebuild_facts(baseline, changed)


def test_calculation_grid_rejects_a_retained_amount_change(current):
    original = SPMForecast.from_dict(
        json.loads((verifier.ROOT / verifier.BASELINE).read_bytes()),
        expected_sha256=verifier.BASELINE_CONTENT_SHA256,
    )
    changed = copy.deepcopy(current)
    entry = changed["scenarios"]["ce_trend"]["years"]["2022"]
    entry["thresholds"] = {k: v * 1.01 for k, v in entry["thresholds"].items()}
    changed["assumption_sha256"] = hashlib.sha256(
        canonical_bytes(changed["assumptions"])
    ).hexdigest()
    mutant = SPMForecast.from_dict(seal_forecast(changed))
    full_grid = verifier._grid
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            verifier,
            "_grid",
            lambda f, s, y: itertools.islice(full_grid(f, s, y), 3),
        )
        with pytest.raises(ValueError, match="Calculation output changed"):
            verifier.compare_calculations(original, mutant)
