"""Pinned calendar-year prices and historical published geography inputs."""

import pytest

from spm_calculator.forecast_inputs import (
    load_horizon_inputs,
    projection_rates,
)


def test_cbo_calendar_growth_uses_one_source_vintage():
    # CBO February2026 calendar CPI-U levels, not Q4/Q4 or a mixed-vintage ratio.
    rates = projection_rates()
    assert set(rates) == set(range(2026, 2037))
    assert rates[2026] == pytest.approx(331.807 / 322.378 - 1)
    assert rates[2035] == pytest.approx(407.346 / 398.355 - 1)
    assert rates[2036] == pytest.approx(416.542 / 407.346 - 1)
    with pytest.raises(ValueError, match="coverage"):
        projection_rates(end_year=2037)


def test_published_historical_area_and_share_sources_are_year_specific():
    history = load_horizon_inputs()["historical"]
    assert len(history["2022"]["rent_indices"]) == 342
    assert len(history["2023"]["rent_indices"]) == 341
    assert set(history["2022"]["rent_indices"]) - set(
        history["2023"]["rent_indices"]
    ) == {"45001"}
    assert history["2022"]["census_workbook_housing_shares"]["renter"] == 0.443
    assert (
        history["2022"]["census_workbook_housing_shares"][
            "owner_without_mortgage"
        ]
        == 0.334
    )
    assert (
        history["2023"]["census_workbook_housing_shares"][
            "owner_without_mortgage"
        ]
        == 0.323
    )
    # Corrected BLS total-shelter + total-utilities shares, excluding phone/internet.
    assert history["2022"]["housing_shares"]["renter"] == pytest.approx(
        0.3765169162 + 0.064925513
    )
    assert load_horizon_inputs()["published_housing_shares"]["2025"][
        "renter"
    ] == pytest.approx(0.3730896039 + 0.0605961665)


def test_model_extension_preserves_original_area_fractions_and_allocates_all_pumas():
    from collections import defaultdict

    from spm_calculator.acs_forecast_sources import load_source_bundle

    original = load_source_bundle()["area_allocations"]
    actual = load_horizon_inputs()["area_allocations_2020"]
    assert [
        r for r in actual if not r["area_code"].startswith("modeled_")
    ] == [r for r in original if r["area_code"] is not None]
    sums = defaultdict(float)
    for row in actual:
        sums[row["puma_geoid"]] += row["fraction"]
    assert len(sums) == 2462
    assert all(abs(value - 1) < 1e-10 for value in sums.values())


def test_historical_2010_population_allocation_and_zero_population_transfer():
    from collections import defaultdict

    from spm_calculator.acs_forecast_sources import (
        load_historical_source_bundle,
    )

    bundle = load_historical_source_bundle()
    sums = defaultdict(float)
    areas = set()
    for row in bundle["area_allocations"]:
        sums[row["puma_geoid"]] += row["fraction"]
        if row["area_code"] is not None:
            areas.add(row["area_code"])
    assert len(sums) == 2351
    assert all(abs(value - 1) < 1e-12 for value in sums.values())
    assert len(areas) == 7
    assert "modeled_residual_metro:45" not in areas
    assert bundle["validation"]["population_2010"] == 308745538
    transfer = bundle["geography_method"]["documented_yuba_transfer"]
    assert transfer["affected_source_blocks"] == 10
    assert transfer["split_source_blocks"] == 4
    assert transfer["affected_source_population_2010"] == 0
    assert transfer["population_imputed_by_area"] is False
    assert len(bundle["topcodes"]["2017"]) == 52
