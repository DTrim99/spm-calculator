"""Synthetic real-Frame acceptance against the actual Axiom core runtime.

AXIOM_CORE_BIN is required for native acceptance. An absent setting skips only
native tests; a configured invalid binary fails. No evaluator is mocked.
"""

import copy
import hashlib
import json
import os
from decimal import Decimal

import pytest

from spm_calculator.axiom_adapter import (
    HOUSING_ID,
    MODULE_ID,
    POVERTY_ID,
    SOURCE_HEADER,
    THRESHOLD_ID,
    AxiomRuntimeError,
    AxiomSPMAdapter,
    export_build_spec,
    make_request,
)
from spm_calculator.errors import SPMInputError
from spm_calculator.microcosm_adapter import (
    OUTPUT_COLUMNS,
    apply_forecast_to_frame,
    prepare_frame_inputs,
    summarize_spm_units,
)
from spm_calculator.release import TENURES, equivalence_factor
from spm_calculator.rolling_forecast import (
    SPMForecast,
    load_forecast,
    seal_forecast,
)


@pytest.fixture(scope="module")
def forecast():
    return load_forecast()


@pytest.fixture
def frame_factory():
    mc = pytest.importorskip("microcosm.frame")
    pd = pytest.importorskip("pandas")

    def create(
        compositions=(((35, False), (8, False)),),
        *,
        tenures=None,
        counties=None,
        resources=None,
        households=None,
        household_weights=None,
        roles=True,
        heads=None,
        spouses=None,
        reverse_persons=False,
    ):
        size = len(compositions)
        tenures = list(tenures or ["renter"] * size)
        counties = list(counties or ["06037"] * size)
        households = list(households or [1] * size)
        people = []
        for unit_index, composition in enumerate(compositions):
            for age, role in composition:
                person_id = len(people) + 1
                row = {
                    "person_id": person_id,
                    "person_household_id": households[unit_index],
                    "person_spm_unit_id": (unit_index + 1) * 10,
                    "age": age,
                }
                if roles:
                    row["is_spm_independent_minor_role"] = role
                if heads is not None:
                    row["is_household_head"] = heads[person_id - 1]
                if spouses is not None:
                    row["is_household_spouse"] = spouses[person_id - 1]
                people.append(row)
        if reverse_persons:
            people.reverse()
        units = {
            "spm_unit_id": [(i + 1) * 10 for i in range(size)],
            "spm_tenure": tenures,
            "county_fips": counties,
        }
        if resources is not None:
            units["resources"] = list(resources)
        household_ids = sorted(set(households))
        return mc.Frame(
            {
                "person": pd.DataFrame(people),
                "household": pd.DataFrame({"household_id": household_ids}),
                "spm_unit": pd.DataFrame(units),
            },
            mc.EntitySchema(group_entities=("household", "spm_unit")),
            {
                "household": mc.Weights(
                    household_weights or [100] * len(household_ids),
                    mc.WeightKind.DESIGN,
                )
            },
            metadata={
                "source": "Synthetic native membership acceptance fixture"
            },
        )

    return create


def selection(*, year=2025, scenario=None, resources=False, **kwargs):
    columns = {"tenure": "spm_tenure", "county_fips": "county_fips"}
    if resources:
        columns["resources"] = "resources"
    return {
        "year": year,
        "scenario": scenario,
        "unit_entity": "spm_unit",
        "weight_entity": "household",
        "membership_provenance": "Synthetic source-native SPM unit links",
        "columns": columns,
        **kwargs,
    }


def national_selection(**kwargs):
    result = selection(**kwargs)
    result["geography_kind"] = "national"
    result["columns"].pop("county_fips")
    return result


@pytest.fixture(scope="module")
def adapter(forecast):
    binary = os.environ.get("AXIOM_CORE_BIN")
    if not binary:
        pytest.skip("Set AXIOM_CORE_BIN to run actual Axiom acceptance")
    return AxiomSPMAdapter(forecast, binary=binary)


@pytest.fixture(scope="module")
def bundles(adapter, tmp_path_factory):
    directory = tmp_path_factory.mktemp("axiom-canonical-native")
    result = {}
    for scenario in ("ce_trend", "zero_real"):
        path = directory / f"{scenario}.bundle.json"
        result[scenario] = (
            path,
            adapter.build_bundle(path, scenario=scenario),
        )
    return result


def scalar(native, name):
    output = native["outputs"][f"{MODULE_ID}#{name}"]
    assert output["kind"] == "scalar"
    return Decimal(str(output["value"]["value"]))


def trace_nodes(native, name):
    return [
        node
        for node in native["trace"].values()
        if node.get("id") == f"{MODULE_ID}#{name}"
    ]


def assert_canonical_results(forecast, frame, result, kwargs):
    prepared = prepare_frame_inputs(frame, forecast, **kwargs)
    expected = [
        forecast.calculate_unit(unit, scenario=kwargs.get("scenario"))
        for unit in prepared.units
    ]
    assert len(result["results"]) == len(expected)
    assert len(result["receipt"]["result"]["results"]) == len(expected)
    for canonical, actual, native in zip(
        expected, result["results"], result["receipt"]["result"]["results"]
    ):
        for name in (
            "reference_threshold",
            "equivalence_factor",
            "geographic_factor",
            "unadjusted_threshold",
            "threshold",
            "housing_share",
            "housing_portion",
        ):
            assert actual[name] == pytest.approx(
                canonical[name], abs=1e-8, rel=1e-12
            )
        for name in (
            "unit_id",
            "year",
            "tenure",
            "forecast_id",
            "forecast_sha256",
            "scenario",
            "national_status",
            "geography_status",
            "housing_share_status",
            "is_in_poverty",
        ):
            assert actual[name] == canonical[name]
        native_geography = dict(actual["provenance"]["geography"])
        canonical_geography = dict(canonical["provenance"]["geography"])
        assert native_geography.pop("factor") == pytest.approx(
            canonical_geography.pop("factor"), rel=1e-12
        )
        assert native_geography == canonical_geography
        assert float(scalar(native, "adjusted_threshold")) == pytest.approx(
            canonical["threshold"], abs=1e-8, rel=1e-12
        )
        assert float(scalar(native, "housing_portion")) == pytest.approx(
            canonical["housing_portion"], abs=1e-8, rel=1e-12
        )
        assert native["trace"]


def test_portable_export_binds_current_forecast_and_declared_domain(forecast):
    spec = export_build_spec(
        forecast,
        years=[2022, 2025, 2036],
        scenario="zero_real",
        max_adults=3,
        max_children=4,
    )
    assert spec["format"] == "axiom/build-spec/v0"
    assert spec["root"] == MODULE_ID
    source = spec["modules"][MODULE_ID]
    assert source.startswith(SOURCE_HEADER)
    identity = json.loads(source.splitlines()[0][len(SOURCE_HEADER) :])
    assert identity["forecast_sha256"] == forecast.content_sha256
    assert identity["forecast_id"] == forecast.forecast_id
    assert identity["years"] == [2022, 2025, 2036]
    assert identity["scenario"] == "zero_real"
    assert identity["composition_domain"] == {
        "min_adults": 1,
        "max_adults": 3,
        "min_children": 0,
        "max_children": 4,
    }
    assert '"format": "rulespec/v1"' in source
    assert '"effective_to": "2036-12-31"' in source
    assert "count_where(member_of_spm_unit, is_measurement_adult)" in source
    assert "age >= 18 or (age >= 15 and independent_minor_role)" in source
    assert str(equivalence_factor(3, 4)) in source
    assert (
        str(forecast.entry(2036, scenario="zero_real")["thresholds"]["renter"])
        in source
    )


def test_request_contains_only_native_membership_and_primitive_facts(
    forecast, frame_factory
):
    frame = frame_factory((((16, True), (3, False)),))
    request, prepared = make_request(forecast, frame, **selection())
    assert request["mode"] == "explain"
    assert request["queries"][0]["period"] == {
        "period_kind": "custom",
        "name": "spm-calendar-year",
        "start": "2025-01-01",
        "end": "2025-12-31",
    }
    assert THRESHOLD_ID in request["queries"][0]["outputs"]
    assert HOUSING_ID in request["queries"][0]["outputs"]
    assert POVERTY_ID not in request["queries"][0]["outputs"]
    assert "assessment_date" not in json.dumps(request)
    assert {fact["name"] for fact in request["dataset"]["inputs"]} == {
        f"{MODULE_ID}#input.{name}"
        for name in (
            "housing_tenure",
            "area_index",
            "age",
            "independent_minor_role",
        )
    }
    assert {
        tuple(relation["tuple"])
        for relation in request["dataset"]["relations"]
    } == {
        ("1", "10"),
        ("2", "10"),
    }
    assert prepared.units[0].num_adults == 1
    assert prepared.units[0].num_children == 1
    assert prepared.provenance["forecast_sha256"] == forecast.content_sha256
    assert prepared.provenance["county_assignments"][
        0
    ] == forecast.resolve_county(2025, "06037")


def test_requested_missing_runtime_has_no_evaluator_fallback(
    forecast, tmp_path
):
    with pytest.raises(AxiomRuntimeError, match="not executable"):
        AxiomSPMAdapter(forecast, binary=tmp_path / "absent-runtime")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"years": [1900]},
        {"years": []},
        {"scenario": "missing"},
        {"max_adults": 0},
        {"max_children": -1},
        {"max_adults": True},
        {"as_of": "1900-01-01"},
    ],
)
def test_invalid_exports_are_rejected(forecast, kwargs):
    with pytest.raises(ValueError):
        export_build_spec(forecast, **kwargs)


@pytest.mark.parametrize("scenario", ["ce_trend", "zero_real"])
@pytest.mark.parametrize("year", range(2022, 2037))
def test_actual_native_all_years_tenures_and_scenarios(
    adapter, bundles, forecast, frame_factory, year, scenario
):
    frame = frame_factory(
        (
            ((35, False), (33, False), (8, False), (5, False)),
            ((41, False),),
            ((16, True), (3, False)),
        ),
        tenures=TENURES,
        counties=["06037", "01001", "36061"],
        resources=[0, 1_000_000, 0],
    )
    path, identity = bundles[scenario]
    kwargs = selection(year=year, scenario=scenario, resources=True)
    result = adapter.execute_bundle(
        path, identity["bundle_sha256"], frame, **kwargs
    )
    assert result["receipt"]["assurance"] == "development_unsigned"
    engine = result["receipt"]["context"]["engine"]
    assert engine["revision"] == "d142c645917817cf590e036fb99f99b2d4780e1a"
    assert engine["version"] == "0.2.2"
    assert engine["artifact_format_version"] == 2
    assert_canonical_results(forecast, frame, result, kwargs)
    assert [row["is_in_poverty"] for row in result["results"]] == [
        True,
        False,
        True,
    ]


def test_actual_native_classification_edges_and_cross_entity_counts(
    adapter, bundles, forecast, frame_factory
):
    cases = [
        (14, False, 1, 1),
        (14, True, 1, 1),
        (15, False, 1, 1),
        (15, True, 2, 0),
        (17, False, 1, 1),
        (17, True, 2, 0),
        (18, False, 2, 0),
        (18, True, 2, 0),
    ]
    frame = frame_factory(
        [((35, False), (age, role)) for age, role, _, _ in cases]
    )
    path, identity = bundles["ce_trend"]
    result = adapter.execute_bundle(
        path, identity["bundle_sha256"], frame, **selection()
    )
    assert len(frame.table("household")) == 1
    for native, (age, role, adults, children) in zip(
        result["receipt"]["result"]["results"], cases
    ):
        assert scalar(native, "num_adults") == adults
        assert scalar(native, "num_children") == children
        nodes = trace_nodes(native, "is_measurement_adult")
        assert len(nodes) == 2
        assert sum(node["outcome"] == "holds" for node in nodes) == adults
        assert (
            "count_related"
            in trace_nodes(native, "num_adults")[0]["executed_expression"]
        )
    assert_canonical_results(forecast, frame, result, selection())


def test_actual_native_independent_minor_and_explicit_role_precedence(
    adapter, bundles, frame_factory
):
    frame = frame_factory(
        (((16, True), (3, False)), ((35, False), (16, False))),
        heads=[True, False, False, True],
        spouses=[False] * 4,
    )
    path, identity = bundles["ce_trend"]
    result = adapter.execute_bundle(
        path, identity["bundle_sha256"], frame, **selection()
    )
    assert [
        (scalar(row, "num_adults"), scalar(row, "num_children"))
        for row in result["receipt"]["result"]["results"]
    ] == [(1, 1), (1, 1)]
    assert (
        result["spm_inputs"]["composition"]["role_source"]
        == "is_spm_independent_minor_role"
    )


def test_actual_native_explicit_head_spouse_fallback_is_row_order_invariant(
    adapter, bundles, frame_factory
):
    kwargs = {
        "compositions": (((15, False), (17, False), (14, False)),),
        "roles": False,
        "heads": [True, False, False],
        "spouses": [False, True, False],
    }
    first, second = (
        frame_factory(**kwargs),
        frame_factory(**kwargs, reverse_persons=True),
    )
    path, identity = bundles["ce_trend"]
    outputs = [
        adapter.execute_bundle(
            path, identity["bundle_sha256"], frame, **selection()
        )
        for frame in (first, second)
    ]
    assert outputs[0]["results"] == outputs[1]["results"]
    for output in outputs:
        native = output["receipt"]["result"]["results"][0]
        assert scalar(native, "num_adults") == 2
        assert scalar(native, "num_children") == 1
        assert output["spm_inputs"]["composition"]["role_source"] == {
            "head": "is_household_head",
            "spouse": "is_household_spouse",
        }


def test_actual_native_trace_executes_lookup_arithmetic_and_housing(
    adapter, bundles, frame_factory
):
    path, identity = bundles["ce_trend"]
    frame = frame_factory(resources=[0])
    result = adapter.execute_bundle(
        path, identity["bundle_sha256"], frame, **selection(resources=True)
    )
    native = result["receipt"]["result"]["results"][0]
    for name in (
        "is_measurement_adult",
        "num_adults",
        "num_children",
        "reference_threshold",
        "equivalence_factor",
        "geographic_factor",
        "housing_share",
        "housing_portion",
        "adjusted_threshold",
        "is_in_poverty",
    ):
        nodes = trace_nodes(native, name)
        assert nodes, name
        assert nodes[0].get("executed_expression"), name
    assert native["outputs"][POVERTY_ID]["outcome"] == "holds"


def test_actual_native_strict_poverty_at_exact_national_reference_threshold(
    adapter, bundles, forecast, frame_factory
):
    threshold = forecast.entry(2025)["thresholds"]["renter"]
    composition = ((35, False), (30, False), (8, False), (4, False))
    frame = frame_factory(
        [composition] * 3,
        resources=[threshold - 0.01, threshold, threshold + 0.01],
    )
    path, identity = bundles["ce_trend"]
    kwargs = national_selection(resources=True)
    result = adapter.execute_bundle(
        path, identity["bundle_sha256"], frame, **kwargs
    )
    native = result["receipt"]["result"]["results"]
    assert [row["outputs"][POVERTY_ID]["outcome"] for row in native] == [
        "holds",
        "not_holds",
        "not_holds",
    ]
    assert scalar(native[1], "adjusted_threshold") == Decimal(str(threshold))
    assert_canonical_results(forecast, frame, result, kwargs)


@pytest.mark.parametrize(
    "case,code",
    [
        ("year", "SPM_YEAR_UNAVAILABLE"),
        ("county", "SPM_GEOGRAPHY_UNAVAILABLE"),
        ("county_vintage", "SPM_GEOGRAPHY_UNAVAILABLE"),
        ("no_adult", "SPM_COMPOSITION_REQUIRED"),
        ("missing_role", "SPM_COMPOSITION_REQUIRED"),
        ("scenario", "SPM_SCENARIO_UNAVAILABLE"),
    ],
)
def test_invalid_frame_inputs_have_same_codes_as_canonical_adapter(
    forecast, frame_factory, case, code
):
    frame = frame_factory()
    kwargs = selection()
    if case == "year":
        kwargs["year"] = 1900
    elif case == "county":
        frame.table("spm_unit").loc[0, "county_fips"] = "99999"
    elif case == "county_vintage":
        kwargs["county_vintage"] = "2010"
    elif case == "no_adult":
        frame.person.loc[:, "age"] = 14
    elif case == "missing_role":
        frame.person.drop(
            columns=["is_spm_independent_minor_role"], inplace=True
        )
    elif case == "scenario":
        kwargs["scenario"] = "missing"
    for operation in (
        apply_forecast_to_frame,
        lambda f, fc, **kw: make_request(fc, f, **kw),
    ):
        with pytest.raises(SPMInputError) as caught:
            operation(frame, forecast, **kwargs)
        assert caught.value.code == code


@pytest.mark.parametrize(
    "composition",
    [
        ((35, False), (30, False), (21, False)),
        ((35, False), (8, False), (7, False), (6, False)),
    ],
)
def test_request_rejects_compositions_outside_declared_lookup_domain(
    forecast, frame_factory, composition
):
    frame = frame_factory([composition])
    with pytest.raises(SPMInputError) as caught:
        make_request(
            forecast, frame, max_adults=2, max_children=2, **selection()
        )
    assert caught.value.code == "SPM_COMPOSITION_UNSUPPORTED"


@pytest.mark.parametrize("dimension", ["adults", "children"])
def test_actual_runtime_rejects_native_composition_outside_lookup_domain(
    adapter, forecast, frame_factory, tmp_path, dimension
):
    bounded = AxiomSPMAdapter(
        forecast, binary=adapter.binary, max_adults=2, max_children=2
    )
    path = tmp_path / "bounded.bundle.json"
    identity = bounded.build_bundle(path, years=[2025])
    frame = frame_factory([((35, False), (30, False), (8, False), (7, False))])
    request, _ = make_request(
        forecast, frame, max_adults=2, max_children=2, **selection()
    )
    for fact in request["dataset"]["inputs"]:
        if fact["name"] == f"{MODULE_ID}#input.age":
            if dimension == "adults":
                fact["value"]["value"] = 35
            elif fact["entity_id"] == "2":
                fact["value"]["value"] = 16
    with pytest.raises(AxiomRuntimeError) as caught:
        bounded._run(
            "run",
            "--bundle",
            path,
            "--expect",
            identity["bundle_sha256"],
            request=request,
        )
    assert caught.value.response["error"]["code"] == "execution_failed"
    assert "key `-1`" in caught.value.response["error"]["message"]


def test_actual_runtime_rejects_unknown_tenure_index(
    adapter, bundles, forecast, frame_factory
):
    path, identity = bundles["ce_trend"]
    request, _ = make_request(forecast, frame_factory(), **selection())
    for fact in request["dataset"]["inputs"]:
        if fact["name"] == f"{MODULE_ID}#input.housing_tenure":
            fact["value"]["value"] = 99
    with pytest.raises(AxiomRuntimeError):
        adapter._run(
            "run",
            "--bundle",
            path,
            "--expect",
            identity["bundle_sha256"],
            request=request,
        )


def test_actual_runtime_rejects_wrong_digest_and_source_tampering(
    adapter, frame_factory, tmp_path
):
    path = tmp_path / "source.bundle.json"
    identity = adapter.build_bundle(path, years=[2025])
    original = path.read_bytes()
    frame = frame_factory()
    with pytest.raises(AxiomRuntimeError):
        adapter.execute_bundle(path, "0" * 64, frame, **selection())
    assert path.read_bytes() == original
    corrupted = json.loads(original)
    corrupted["modules"][MODULE_ID] += " "
    path.write_text(json.dumps(corrupted))
    with pytest.raises(AxiomRuntimeError):
        adapter.execute_bundle(
            path, identity["bundle_sha256"], frame, **selection()
        )


def test_bundle_cannot_be_relabelled_to_another_forecast_or_scenario(
    adapter, bundles, forecast, frame_factory
):
    document = forecast.to_dict()
    document["forecast_id"] = "synthetic-same-values-different-source-identity"
    relabelled = SPMForecast.from_dict(seal_forecast(document))
    other = AxiomSPMAdapter(relabelled, binary=adapter.binary)
    path, identity = bundles["ce_trend"]
    frame = frame_factory()
    with pytest.raises(AxiomRuntimeError):
        other.execute_bundle(
            path, identity["bundle_sha256"], frame, **selection()
        )
    with pytest.raises((AxiomRuntimeError, ValueError)):
        adapter.execute_bundle(
            path,
            identity["bundle_sha256"],
            frame,
            **selection(scenario="zero_real"),
        )


def test_actual_runtime_uncompiled_year_and_existing_bundle_are_preserved(
    adapter, frame_factory, tmp_path
):
    path = tmp_path / "one-year.bundle.json"
    identity = adapter.build_bundle(path, years=[2025])
    original = path.read_bytes()
    with pytest.raises((ValueError, AxiomRuntimeError)):
        adapter.execute_bundle(
            path,
            identity["bundle_sha256"],
            frame_factory(),
            **selection(year=2024),
        )
    with pytest.raises(AxiomRuntimeError):
        adapter.build_bundle(path, years=[2025])
    assert path.read_bytes() == original


def test_actual_apply_preserves_frame_weights_source_and_native_receipt(
    adapter, forecast, frame_factory
):
    pd = pytest.importorskip("pandas")
    frame = frame_factory(
        [((35, False),), ((35, False), (30, False), (8, False))],
        households=[1, 2],
        household_weights=[100, 200],
        resources=[0, 1_000_000],
    )
    before_person, before_units = (
        frame.person.copy(deep=True),
        frame.table("spm_unit").copy(deep=True),
    )
    weights = frame.weights_for("household")
    weight_values = weights.values.copy()
    before_metadata = copy.deepcopy(frame.metadata)
    applied = adapter.apply_to_frame(frame, **selection(resources=True))
    pd.testing.assert_frame_equal(frame.person, before_person)
    pd.testing.assert_frame_equal(frame.table("spm_unit"), before_units)
    pd.testing.assert_frame_equal(applied.person, before_person)
    assert frame.metadata == before_metadata
    assert applied.metadata["source"] == frame.metadata["source"]
    assert applied.weights_for("household") is weights
    assert (weights.values == weight_values).all()
    assert OUTPUT_COLUMNS["threshold"] not in frame.table("spm_unit")
    application = applied.metadata["spm_forecast_application"]
    assert application["forecast_sha256"] == forecast.content_sha256
    assert application["weights"]["kind"] == "design"
    execution = application["execution"]
    assert execution["receipt"]["assurance"] == "development_unsigned"
    assert execution["receipt"]["result"]["results"][0]["trace"]
    assert (
        execution["binary_sha256"]
        == hashlib.sha256(adapter.binary.read_bytes()).hexdigest()
    )
    summary = summarize_spm_units(
        applied, unit_entity="spm_unit", weight_entity="household"
    )
    assert summary["poor_units"] == 100
    assert summary["unit_poverty_rate"] == pytest.approx(1 / 3)
    assert summary["unit_poverty_rate"] != pytest.approx(1 / 7)


def test_actual_missing_resources_remain_unknown(adapter, frame_factory):
    applied = adapter.apply_to_frame(frame_factory(), **selection())
    assert (
        applied.table("spm_unit")[OUTPUT_COLUMNS["is_in_poverty"]].isna().all()
    )
    summary = summarize_spm_units(
        applied, unit_entity="spm_unit", weight_entity="household"
    )
    assert summary["poor_units"] is None
    assert summary["unit_poverty_rate"] is None


@pytest.mark.parametrize("year", [2022, 2025])
def test_actual_native_selected_year_area_types_status_and_diagnostics(
    adapter, bundles, forecast, frame_factory, year
):
    selected = {}
    for area_id, area in forecast.areas_for_year(year).items():
        selected.setdefault(area["area_type"], area_id)
    assert set(selected) == {
        "msa",
        "state_nonmetro",
        "state_metro_residual",
        "modeled_residual_metro",
    }
    frame = frame_factory([((35, False), (8, False))] * len(selected))
    frame.table("spm_unit")["area"] = list(selected.values())
    kwargs = selection(
        year=year,
        geography_kind="metro",
        columns={"tenure": "spm_tenure", "geography_id": "area"},
    )
    path, identity = bundles["ce_trend"]
    result = adapter.execute_bundle(
        path, identity["bundle_sha256"], frame, **kwargs
    )
    assert_canonical_results(forecast, frame, result, kwargs)
    for row, (area_type, area_id) in zip(result["results"], selected.items()):
        geography = row["provenance"]["geography"]
        expected = forecast.areas_for_year(year)[area_id]
        assert geography["area_type"] == area_type
        assert (
            geography["official_published_area"]
            == expected["official_published_area"]
        )
        assert geography["status"] == expected["status"]
        assert geography["diagnostics"] == forecast.entry(year).get(
            "median_diagnostics", {}
        ).get(area_id, {})
    assert result["spm_inputs"]["county_assignments"] == [None] * len(selected)


def test_actual_native_every_declared_equivalence_composition_matches_canonical(
    adapter, bundles, forecast, frame_factory
):
    domain = [
        (adults, children) for adults in range(1, 11) for children in range(11)
    ]
    frame = frame_factory(
        [
            ((35, False),) * adults + ((8, False),) * children
            for adults, children in domain
        ]
    )
    path, identity = bundles["ce_trend"]
    kwargs = national_selection()
    result = adapter.execute_bundle(
        path, identity["bundle_sha256"], frame, **kwargs
    )
    for native, (adults, children) in zip(
        result["receipt"]["result"]["results"], domain
    ):
        assert scalar(native, "num_adults") == adults
        assert scalar(native, "num_children") == children
        assert scalar(native, "equivalence_factor") == Decimal(
            str(equivalence_factor(adults, children))
        )
    assert_canonical_results(forecast, frame, result, kwargs)


@pytest.mark.parametrize(
    "tampering", ["area_index", "missing_role", "no_adult"]
)
def test_actual_runtime_rejects_invalid_native_inputs(
    adapter, bundles, forecast, frame_factory, tampering
):
    request, _ = make_request(forecast, frame_factory(), **selection())
    inputs = request["dataset"]["inputs"]
    if tampering == "missing_role":
        # A 16-year-old requires the role primitive to resolve classification.
        for fact in inputs:
            if (
                fact["name"] == f"{MODULE_ID}#input.age"
                and fact["entity_id"] == "2"
            ):
                fact["value"]["value"] = 16
        request["dataset"]["inputs"] = [
            fact
            for fact in inputs
            if not (
                fact["name"] == f"{MODULE_ID}#input.independent_minor_role"
                and fact["entity_id"] == "2"
            )
        ]
    else:
        field = "age" if tampering == "no_adult" else "area_index"
        for fact in inputs:
            if fact["name"] == f"{MODULE_ID}#input.{field}":
                fact["value"]["value"] = (
                    16 if tampering == "no_adult" else 999999
                )
    path, identity = bundles["ce_trend"]
    with pytest.raises(AxiomRuntimeError):
        adapter._run(
            "run",
            "--bundle",
            path,
            "--expect",
            identity["bundle_sha256"],
            request=request,
        )


def test_actual_native_preserves_decimal_poverty_at_canonical_float_boundary(
    adapter, bundles, forecast, frame_factory
):
    frame = frame_factory([((35, False),)], tenures=["owner_with_mortgage"])
    prepared = prepare_frame_inputs(frame, forecast, **selection())
    canonical_threshold = forecast.calculate_unit(prepared.units[0])[
        "threshold"
    ]
    frame.table("spm_unit")["resources"] = [canonical_threshold]
    kwargs = selection(resources=True)
    path, identity = bundles["ce_trend"]
    result = adapter.execute_bundle(
        path, identity["bundle_sha256"], frame, **kwargs
    )
    native = result["receipt"]["result"]["results"][0]
    exact_threshold = scalar(native, "adjusted_threshold")
    actual = result["results"][0]
    canonical = forecast.calculate_unit(
        prepare_frame_inputs(frame, forecast, **kwargs).units[0]
    )
    assert canonical["is_in_poverty"] is False
    # Core uses exact decimal arithmetic on the exported inputs. The tiny tail
    # can put a resource equal to the canonical float amount below core's amount.
    assert Decimal(str(canonical_threshold)) < exact_threshold
    assert native["outputs"][POVERTY_ID]["outcome"] == "holds"
    assert actual["is_in_poverty"] is True
    assert actual["threshold"] == canonical_threshold
    assert (
        Decimal(actual["native_decimal_values"]["threshold"])
        == exact_threshold
    )
    assert actual["provenance"]["numeric_semantics"]


def test_actual_native_accepts_small_representable_resource_literal(
    adapter, bundles, frame_factory
):
    frame = frame_factory(resources=[0.00001])
    path, identity = bundles["ce_trend"]
    result = adapter.execute_bundle(
        path, identity["bundle_sha256"], frame, **selection(resources=True)
    )
    assert result["results"][0]["is_in_poverty"] is True
    resource = next(
        fact
        for fact in result["request"]["dataset"]["inputs"]
        if fact["name"] == f"{MODULE_ID}#input.resources"
    )
    assert Decimal(resource["value"]["value"]) == Decimal("0.00001")
    assert "e" not in resource["value"]["value"].lower()


def test_actual_native_numeric_limit_error_response_is_preserved(
    adapter, bundles, frame_factory
):
    frame = frame_factory(resources=[1e30])
    path, identity = bundles["ce_trend"]
    with pytest.raises(AxiomRuntimeError) as caught:
        adapter.execute_bundle(
            path, identity["bundle_sha256"], frame, **selection(resources=True)
        )
    assert caught.value.returncode != 0
    assert caught.value.response["ok"] is False
    assert caught.value.response["error"]["code"] == "invalid_dataset"
    assert "decimal" in caught.value.response["error"]["message"]
