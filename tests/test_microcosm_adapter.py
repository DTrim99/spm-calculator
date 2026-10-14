"""Canonical SPM on real Microcosm Frames; no country-engine substitutes."""

import hashlib
from collections.abc import Mapping

import pytest

mc = pytest.importorskip("microcosm.frame")
pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")

from spm_calculator.errors import SPMInputError
from spm_calculator.microcosm_adapter import (
    OUTPUT_COLUMNS,
    _attach_results,
    apply_forecast_to_frame,
    prepare_frame_inputs,
    summarize_spm_units,
)
from spm_calculator.release import SPMUnit, canonical_bytes
from spm_calculator.rolling_forecast import (
    SPMForecast,
    forecast_digest,
    load_forecast,
)


@pytest.fixture(scope="module")
def forecast():
    return load_forecast()


@pytest.fixture
def frame():
    # Two native SPM units share household 1. A dependent 16-year-old is in
    # household 2, while unit 20 has an independent 16-year-old and a child.
    return mc.Frame(
        {
            "person": pd.DataFrame(
                {
                    "person_id": [1, 2, 3, 4, 5, 6, 7],
                    "person_household_id": [1, 1, 1, 2, 2, 2, 2],
                    "person_spm_unit_id": [10, 20, 20, 30, 30, 30, 30],
                    "age": [30, 16, 8, 38, 35, 16, 5],
                    "is_spm_independent_minor_role": [
                        False,
                        True,
                        False,
                        False,
                        False,
                        False,
                        False,
                    ],
                }
            ),
            "household": pd.DataFrame({"household_id": [1, 2]}),
            "spm_unit": pd.DataFrame(
                {
                    "spm_unit_id": [10, 20, 30],
                    # Deliberately incorrect legacy counts must never be used.
                    "spm_num_adults": [99, 99, 99],
                    "spm_num_children": [99, 99, 99],
                    "spm_tenure": [
                        "renter",
                        "owner_with_mortgage",
                        "owner_without_mortgage",
                    ],
                    "county_fips": ["01001", "01003", "02013"],
                    "resources": [0.0, 1_000_000.0, 1_000_000.0],
                }
            ),
        },
        mc.EntitySchema(group_entities=("household", "spm_unit")),
        {"household": mc.Weights([100, 200], mc.WeightKind.DESIGN)},
        strata=pd.Series(["a", "a", "a", "b", "b", "b", "b"]),
        metadata={
            "source": "synthetic software fixture",
            "receipt": {"values": [1, 2]},
        },
    )


def inputs(**kwargs):
    return {
        "year": 2025,
        "unit_entity": "spm_unit",
        "weight_entity": "household",
        "membership_provenance": "Synthetic native membership; all persons represented",
        "columns": {
            "tenure": "spm_tenure",
            "county_fips": "county_fips",
            "resources": "resources",
        },
        **kwargs,
    }


def apply(frame, forecast, **kwargs):
    return apply_forecast_to_frame(frame, forecast, **inputs(**kwargs))


def clone_frame(frame, *, person=None, units=None, weights=None):
    return mc.Frame(
        {
            "person": frame.person if person is None else person,
            "household": frame.table("household"),
            "spm_unit": frame.table("spm_unit") if units is None else units,
        },
        frame.schema,
        (
            {
                entity: frame.weights_for(entity)
                for entity in frame.weighted_entities
            }
            if weights is None
            else weights
        ),
        frame.strata,
        mass_log=frame.mass_log,
        metadata=frame.metadata,
    )


def thaw_metadata(value):
    if isinstance(value, Mapping):
        return {key: thaw_metadata(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw_metadata(child) for child in value]
    return value


def test_native_units_primitive_roles_and_weighted_unit_denominator(
    frame, forecast
):
    prepared = prepare_frame_inputs(frame, forecast, **inputs())
    assert [(u.num_adults, u.num_children) for u in prepared.units] == [
        (1, 0),
        (1, 1),
        (2, 2),
    ]
    assert [p["unit_id"] for p in prepared.persons] == [
        "10",
        "20",
        "20",
        "30",
        "30",
        "30",
        "30",
    ]
    assert prepared.max_unit_size == 4
    applied = apply(frame, forecast)
    summary = summarize_spm_units(
        applied, unit_entity="spm_unit", weight_entity="household"
    )
    assert summary["poor_units"] == 100
    assert summary["unit_poverty_rate"] == pytest.approx(1 / 4)
    assert summary["unit_poverty_rate"] != pytest.approx(1 / 11)
    assert summary["mean_threshold"] == mc.wmean(
        applied, OUTPUT_COLUMNS["threshold"], entity="spm_unit"
    )
    assert summary["poor_units"] == mc.wsum(
        applied, OUTPUT_COLUMNS["is_in_poverty"], entity="spm_unit"
    )


def test_application_preserves_all_source_tables_weights_and_receipts(
    frame, forecast
):
    before = {
        entity: frame.table(entity).copy(deep=True)
        for entity in frame.entities
    }
    original_weights = frame.weights_for("household")
    original_values = original_weights.values.copy()
    applied = apply(frame, forecast)
    for entity in frame.entities:
        pd.testing.assert_frame_equal(frame.table(entity), before[entity])
        pd.testing.assert_frame_equal(
            applied.table(entity)[before[entity].columns], before[entity]
        )
    assert applied.weights_for("household") is original_weights
    np.testing.assert_array_equal(original_weights.values, original_values)
    assert not original_weights.values.flags.writeable
    pd.testing.assert_series_equal(applied.strata, frame.strata)
    assert applied.mass_log == frame.mass_log
    assert applied.metadata["receipt"] == frame.metadata["receipt"]
    assert "spm_forecast_application" not in frame.metadata
    receipt = applied.metadata["spm_forecast_application"]
    assert (
        receipt["weights"]["source_values_sha256"]
        == hashlib.sha256(original_values.tobytes()).hexdigest()
    )
    assert receipt["forecast_sha256"] == forecast.content_sha256
    assert receipt["composition"]["person_membership_inputs_sha256"]


@pytest.mark.parametrize("year", range(2022, 2037))
@pytest.mark.parametrize("scenario", ["ce_trend", "zero_real"])
def test_every_artifact_year_scenario_tenure_matches_canonical(
    frame, forecast, year, scenario
):
    prepared = prepare_frame_inputs(
        frame, forecast, **inputs(year=year, scenario=scenario)
    )
    applied = apply(frame, forecast, year=year, scenario=scenario)
    receipts = applied.metadata["spm_forecast_application"]["unit_results"]
    for index, (unit, receipt) in enumerate(zip(prepared.units, receipts)):
        expected = forecast.calculate_unit(unit, scenario=scenario)
        for field in OUTPUT_COLUMNS:
            assert receipt[field] == expected[field]
            assert (
                applied.table("spm_unit")[OUTPUT_COLUMNS[field]].iloc[index]
                == expected[field]
            )
        geography = receipt["provenance"]["geography"]
        menu = forecast.areas_for_year(year, scenario=scenario)[
            unit.geography_id
        ]
        for field in ("area_type", "official_published_area", "status"):
            assert geography[field] == menu[field]
        assert (
            thaw_metadata(geography["diagnostics"])
            == expected["provenance"]["geography"]["diagnostics"]
        )
        assignment = receipt["provenance"]["county_assignment"]
        assert assignment["area_id"] == unit.geography_id
        assert assignment["county_vintage"] == "2020"
        assert unit.geography_kind == "metro"
    assert (
        receipts[2]["provenance"]["geography"]["area_type"] == "state_nonmetro"
    )


def test_explicit_metro_and_national_selection(frame, forecast):
    area = forecast.resolve_county(2025, "01001")["area_id"]
    mapping = {"tenure": "spm_tenure", "resources": "resources"}
    for kind, identity in (("national", None), ("metro", area)):
        result = apply(
            frame,
            forecast,
            geography_kind=kind,
            geography_id=identity,
            columns=mapping,
        )
        for index, receipt in enumerate(
            result.metadata["spm_forecast_application"]["unit_results"]
        ):
            a, c = [(1, 0), (1, 1), (2, 2)][index]
            expected = forecast.calculate_unit(
                SPMUnit(
                    str([10, 20, 30][index]),
                    a,
                    c,
                    frame.table("spm_unit")["spm_tenure"].iloc[index],
                    2025,
                    geography_kind=kind,
                    geography_id=identity,
                )
            )
            assert receipt["threshold"] == expected["threshold"]
            assert "county_assignment" not in receipt["provenance"]
            if kind == "national":
                assert receipt["geography_status"] == "explicit_national"


def test_default_metro_column_and_missing_resources(frame, forecast):
    units = frame.table("spm_unit").assign(geography_id="33860")
    result = apply(
        clone_frame(frame, units=units),
        forecast,
        geography_kind="metro",
        columns=None,
    )
    assert (
        result.table("spm_unit")[OUTPUT_COLUMNS["is_in_poverty"]].isna().all()
    )
    summary = summarize_spm_units(
        result, unit_entity="spm_unit", weight_entity="household"
    )
    assert summary["poor_units"] is None
    assert summary["unit_poverty_rate"] is None


def test_selected_year_uses_selected_county_assignment(frame, forecast):
    document = forecast.to_dict()
    assignments = document["county_assignments"]
    old_map = assignments["year_maps"]["2025"]
    assignments["maps"]["synthetic_changed_boundary"] = {
        **assignments["maps"][old_map],
        "01001": "2002",
    }
    assignments["year_maps"]["2025"] = "synthetic_changed_boundary"
    assignments.pop("sha256")
    assignments["sha256"] = hashlib.sha256(
        canonical_bytes(assignments)
    ).hexdigest()
    document["content_sha256"] = forecast_digest(document)
    changed = SPMForecast.from_dict(document)
    older = prepare_frame_inputs(frame, changed, **inputs(year=2024))
    newer = prepare_frame_inputs(frame, changed, **inputs(year=2025))
    assert older.units[0].geography_id == "33860"
    assert newer.units[0].geography_id == "2002"
    assert (
        apply(frame, changed).metadata["spm_forecast_application"][
            "forecast_sha256"
        ]
        == changed.content_sha256
    )


@pytest.mark.parametrize(
    "age,role,adults,children",
    [
        (14, True, 2, 2),
        (15, True, 3, 1),
        (17, True, 3, 1),
        (17, False, 2, 2),
        (18, False, 3, 1),
    ],
)
def test_primitive_age_boundaries(
    frame, forecast, age, role, adults, children
):
    frame.person.loc[5, ["age", "is_spm_independent_minor_role"]] = [age, role]
    unit = prepare_frame_inputs(frame, forecast, **inputs()).units[2]
    assert (unit.num_adults, unit.num_children) == (adults, children)


def test_explicit_head_spouse_fallback_and_primitive_precedence(
    frame, forecast
):
    person = frame.person.drop(columns="is_spm_independent_minor_role").assign(
        is_household_head=[True, True, False, True, False, False, False],
        is_household_spouse=[False, False, False, False, True, True, False],
    )
    prepared = prepare_frame_inputs(
        clone_frame(frame, person=person), forecast, **inputs()
    )
    assert [(u.num_adults, u.num_children) for u in prepared.units] == [
        (1, 0),
        (1, 1),
        (3, 1),
    ]
    assert prepared.provenance["composition"]["role_source"] == {
        "head": "is_household_head",
        "spouse": "is_household_spouse",
    }
    person["is_spm_independent_minor_role"] = frame.person[
        "is_spm_independent_minor_role"
    ]
    prepared = prepare_frame_inputs(
        clone_frame(frame, person=person), forecast, **inputs()
    )
    assert (prepared.units[2].num_adults, prepared.units[2].num_children) == (
        2,
        2,
    )


def test_explicit_roles_ignore_person_id_and_age_order(frame, forecast):
    person = frame.person.copy()
    person.loc[[1, 2], "age"] = [8, 16]
    person.loc[[1, 2], "is_spm_independent_minor_role"] = [False, True]
    unit = prepare_frame_inputs(
        clone_frame(frame, person=person), forecast, **inputs()
    ).units[1]
    assert (unit.num_adults, unit.num_children) == (1, 1)


def test_custom_person_columns_preserve_source_primitive(frame, forecast):
    person = frame.person.rename(
        columns={
            "age": "source_age",
            "is_spm_independent_minor_role": "source_independent",
        }
    )
    prepared = prepare_frame_inputs(
        clone_frame(frame, person=person),
        forecast,
        **inputs(
            person_columns={
                "age": "source_age",
                "independent_minor_role": "source_independent",
            }
        ),
    )
    assert (
        prepared.provenance["composition"]["role_source"]
        == "source_independent"
    )
    assert prepared.persons[1]["independent_minor_role"] is True


@pytest.mark.parametrize("year", [2021, 2037, 2025.0, True, "2025"])
def test_unsupported_year_has_stable_error(frame, forecast, year):
    with pytest.raises(SPMInputError) as error:
        apply(frame, forecast, year=year)
    assert error.value.code == "SPM_YEAR_UNAVAILABLE"


@pytest.mark.parametrize(
    "county,code",
    [
        ("99999", "SPM_GEOGRAPHY_UNAVAILABLE"),
        (1001, "SPM_GEOGRAPHY_UNAVAILABLE"),
        ("1001", "SPM_GEOGRAPHY_UNAVAILABLE"),
        (None, "SPM_GEOGRAPHY_REQUIRED"),
        ("", "SPM_GEOGRAPHY_REQUIRED"),
    ],
)
def test_invalid_county_has_stable_error(frame, forecast, county, code):
    frame.table("spm_unit")["county_fips"] = pd.Series(
        [county, "01003", "02013"], dtype=object
    )
    with pytest.raises(SPMInputError) as error:
        apply(frame, forecast)
    assert error.value.code == code


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"county_vintage": "2010"}, "SPM_GEOGRAPHY_UNAVAILABLE"),
        ({"scenario": "unknown"}, "SPM_SCENARIO_UNAVAILABLE"),
        (
            {
                "geography_kind": "metro",
                "geography_id": "unknown",
                "columns": {"tenure": "spm_tenure"},
            },
            "SPM_GEOGRAPHY_UNAVAILABLE",
        ),
    ],
)
def test_unavailable_selection_has_stable_error(frame, forecast, kwargs, code):
    with pytest.raises(SPMInputError) as error:
        apply(frame, forecast, **kwargs)
    assert error.value.code == code


def test_missing_age_or_primitive_never_invents_composition(frame, forecast):
    for columns in (["age"], ["is_spm_independent_minor_role"]):
        with pytest.raises(SPMInputError) as error:
            apply(
                clone_frame(frame, person=frame.person.drop(columns=columns)),
                forecast,
            )
        assert error.value.code == "SPM_COMPOSITION_REQUIRED"


@pytest.mark.parametrize("value", [None, 1, "true"])
def test_primitive_requires_actual_nonmissing_boolean(frame, forecast, value):
    person = frame.person.copy()
    person["is_spm_independent_minor_role"] = person[
        "is_spm_independent_minor_role"
    ].astype(object)
    person.loc[1, "is_spm_independent_minor_role"] = value
    with pytest.raises(SPMInputError) as error:
        apply(clone_frame(frame, person=person), forecast)
    assert error.value.code == "SPM_COMPOSITION_REQUIRED"


@pytest.mark.parametrize("age", [-1, 15.5, float("nan"), True, "16"])
def test_age_requires_nonnegative_integer(frame, forecast, age):
    person = frame.person.copy()
    person["age"] = person["age"].astype(object)
    person.loc[1, "age"] = age
    with pytest.raises(SPMInputError) as error:
        apply(clone_frame(frame, person=person), forecast)
    assert error.value.code == "SPM_COMPOSITION_REQUIRED"


def test_no_adult_is_not_repaired_by_age_or_row_order(frame, forecast):
    frame.person.loc[1, "is_spm_independent_minor_role"] = False
    with pytest.raises(SPMInputError) as error:
        apply(frame, forecast)
    assert error.value.code == "SPM_COMPOSITION_REQUIRED"
    assert "no classified adult" in str(error.value)


def test_reweighting_changes_accounting_without_changing_thresholds(
    frame, forecast
):
    reweighted = frame.with_weights(
        "household",
        mc.Weights([200, 100], mc.WeightKind.CALIBRATED),
        mass="conserve",
    )
    first, second = apply(frame, forecast), apply(reweighted, forecast)
    pd.testing.assert_series_equal(
        first.table("spm_unit")[OUTPUT_COLUMNS["threshold"]],
        second.table("spm_unit")[OUTPUT_COLUMNS["threshold"]],
    )
    summary = summarize_spm_units(
        second, unit_entity="spm_unit", weight_entity="household"
    )
    assert summary["unit_poverty_rate"] == pytest.approx(2 / 5)
    assert summary["weights"]["kind"] == "calibrated"
    assert second.mass_log == reweighted.mass_log
    assert second.weights_for("household") is reweighted.weights_for(
        "household"
    )


@pytest.mark.parametrize(
    "source,values",
    [
        ("person", [100, 100, 100, 200, 200, 200, 200]),
        ("spm_unit", [50, 150, 200]),
    ],
)
def test_native_person_or_unit_weights_keep_their_type_and_source(
    frame, forecast, source, values
):
    weights = mc.Weights(values, mc.WeightKind.IMPORTANCE)
    weighted = clone_frame(frame, weights={source: weights})
    result = apply(weighted, forecast, weight_entity=source)
    assert result.weights_for(source) is weights
    summary = summarize_spm_units(
        result, unit_entity="spm_unit", weight_entity=source
    )
    assert summary["weights"]["source_entity"] == source
    assert summary["weights"]["kind"] == "importance"
    assert summary["poor_units"] == values[0]


def test_unequal_member_weights_do_not_get_averaged(frame, forecast):
    weighted = clone_frame(
        frame,
        weights={
            "person": mc.Weights(
                [100, 100, 110, 200, 200, 200, 200], mc.WeightKind.DESIGN
            )
        },
    )
    with pytest.raises(ValueError):
        apply(weighted, forecast, weight_entity="person")


def test_wrong_weight_entity_is_not_silently_substituted(frame, forecast):
    with pytest.raises(ValueError, match="not requested"):
        apply(frame, forecast, weight_entity="spm_unit")
    with pytest.raises(ValueError, match="not requested"):
        summarize_spm_units(
            apply(frame, forecast),
            unit_entity="spm_unit",
            weight_entity="spm_unit",
        )


def test_postconstruction_membership_corruption_fails_before_application(
    frame, forecast
):
    frame.person.loc[0, "person_spm_unit_id"] = 999
    with pytest.raises(ValueError):
        apply(frame, forecast)


def test_result_attachment_rejects_reordered_native_units(frame, forecast):
    prepared = prepare_frame_inputs(frame, forecast, **inputs())
    results = [forecast.calculate_unit(unit) for unit in prepared.units]
    with pytest.raises(ValueError, match="membership order"):
        _attach_results(frame, prepared, results[::-1])


def test_requires_actual_frame_and_explicit_membership(frame, forecast):
    with pytest.raises(TypeError, match="actual"):
        apply(object(), forecast)
    with pytest.raises(ValueError, match="membership provenance"):
        apply(frame, forecast, membership_provenance="")
