"""In-kind stage invariants, imputation contracts and the decomposition.

Invariants (stated in the PR and docs/ce-inkind-imputation.md):

1. Imputed amounts are finite and >= 0 for every unit and component.
2. The stage never lowers any unit's FCSUti or SU.
3. An all-zero in-kind table reproduces the out-of-pocket output exactly;
   the stage leaves ``spm_calculator.ce_threshold`` and its results as
   they were.
4. Imputation is order invariant and deterministic.
5. Units outside a component's universe receive zero.
6. Growth decomposes exactly: total = price x real x in-kind, and the
   additive contributions sum to total growth.
7. Every predictive-mean-matching pool holds min(k, n) distinct donors,
   the k nearest to the target by predicted value.
"""

import hashlib
import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from spm_calculator import ce_inkind, ce_threshold
from spm_calculator.ce_decomposition import (
    BLS_FCSUTI_INDEX,
    TENURES,
    bls_fcsuti_index,
    decompose_growth,
)
from spm_calculator.ce_inkind import (
    IN_KIND_COMPONENTS,
    InKindConfig,
    ce_unit_covariates,
    fit_logit,
    fit_tobit,
    hashed_uniform,
    impute_in_kind,
    load_ce_unit_inputs,
    nslp_formula_values,
    opm_threshold,
    wic_ebt_statewide_dates,
)
from spm_calculator.ce_inkind_stage import (
    METHODOLOGY_PREFIX,
    add_in_kind,
    prepare_out_of_pocket,
    replicate_with_in_kind,
    zero_in_kind,
)
from spm_calculator.ce_threshold import (
    construct_normalized_expenditures,
    normalize_ce_sample,
    replicate_thresholds,
)
from spm_calculator.fcsuti_cpi import CPI_SERIES

ROOT = Path(ce_inkind.__file__).parent.parent
DATA = Path(ce_inkind.__file__).parent / "data" / "inkind"
RECEIPT = (
    ROOT / "spm_calculator/data/current/ce_inkind_replication_2019_2025.json"
)
FIPS = sorted(wic_ebt_statewide_dates())
PROPERTY = settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


# ---------------------------------------------------------------------------
# Synthetic inputs
# ---------------------------------------------------------------------------


def flat_cpi():
    return {
        sid: pd.Series({year: 100.0 for year in range(2014, 2027)})
        for sid in CPI_SERIES.values()
    }


def ce_frame(seed: int, n: int = 60, year: int = 2023) -> pd.DataFrame:
    """CE FMLI-like rows with varied composition and spending."""
    rng = np.random.default_rng(seed)
    children = rng.integers(1, 4, n)
    adults = rng.integers(1, 3, n)
    frame = pd.DataFrame(
        {
            "NEWID": np.arange(1, n + 1) * 10 + 1,
            "CUTENURE": rng.choice([1, 2, 4], n),
            "PERSLT18": children,
            "FAM_SIZE": children + adults,
            "ce_year": year,
            "ce_quarter": rng.integers(1, 5, n),
            "FINLWT21": rng.uniform(500, 5000, n),
        }
    )
    for name, scale in (
        ("FOOD", 1500),
        ("APPAR", 300),
        ("SHELT", 3000),
        ("UTIL", 800),
        ("TELEPH", 200),
    ):
        for q in ("PQ", "CQ"):
            frame[f"{name}{q}"] = rng.uniform(0, scale, n)
    return frame


def in_kind_table(newids, amounts: np.ndarray) -> pd.DataFrame:
    table = pd.DataFrame({"NEWID": np.asarray(newids)})
    for index, component in enumerate(IN_KIND_COMPONENTS):
        table[component] = amounts[:, index]
    return table


def ce_units(seed: int, reference_years, n: int = 400) -> pd.DataFrame:
    """Harmonized CE units (the output of ce_unit_covariates)."""
    rng = np.random.default_rng(seed)
    frames = []
    for offset, reference_year in enumerate(reference_years):
        quarter = rng.choice([1, 2, 3, 4], n)
        ce_year = np.where(quarter == 1, reference_year + 1, reference_year)
        n_people = rng.integers(1, 7, n)
        n_lt18 = np.minimum(rng.integers(0, 4, n), n_people - 1)
        n_lt5 = np.minimum(rng.integers(0, 2, n), n_lt18)
        n_5_18 = np.maximum(n_lt18 - n_lt5, 0)
        state = rng.choice(FIPS, n).astype(float)
        state[rng.uniform(size=n) < 0.15] = np.nan
        cutenure = rng.choice([1, 2, 4, 4, 5], n)
        frames.append(
            pd.DataFrame(
                {
                    "key": (offset + 1) * 100_000 + np.arange(n),
                    "weight": rng.uniform(1000, 20000, n),
                    "ce_year": ce_year,
                    "ce_quarter": quarter,
                    "reference_year": reference_year,
                    "interview_year": ce_year,
                    "interview_month": 3 * quarter - 1,
                    "region": rng.choice([1, 2, 3, 4], n).astype(float),
                    "state": state,
                    "tenure": pd.Series(cutenure)
                    .map(ce_inkind._TENURE_BY_CUTENURE)
                    .to_numpy(),
                    "cutenure": cutenure.astype(float),
                    "income": rng.lognormal(10.5, 1.0, n),
                    "snap": rng.uniform(size=n) < 0.15,
                    "public_assistance": rng.uniform(size=n) < 0.03,
                    "ssi": rng.uniform(size=n) < 0.04,
                    "housing_assistance": (cutenure == 4)
                    & (rng.uniform(size=n) < 0.2),
                    "n_people": n_people.astype(float),
                    "n_earners": rng.integers(0, 3, n).astype(float),
                    "head_age": rng.integers(18, 85, n).astype(float),
                    "head_female": rng.uniform(size=n) < 0.5,
                    "head_race": rng.choice(["white", "black", "other"], n),
                    "head_hispanic": rng.uniform(size=n) < 0.2,
                    "head_education": rng.choice(
                        ["lt_hs", "hs", "some_college", "ba_plus"], n
                    ),
                    "rent_paid_monthly": np.where(
                        cutenure == 4, rng.lognormal(6.9, 0.5, n), 0.0
                    ),
                    "bedrooms": rng.integers(0, 5, n).astype(float),
                    "bathrooms": rng.integers(1, 4, n).astype(float),
                    "building": rng.choice(
                        ["detached_mobile_other", "garden", "apartment_flat"],
                        n,
                    ),
                    "urban": rng.uniform(size=n) < 0.9,
                    "popsize": rng.integers(1, 6, n).astype(float),
                    "n_lt5": n_lt5,
                    "n_infants": np.minimum(n_lt5, rng.integers(0, 2, n)),
                    "n_5_18": n_5_18,
                    "n_lt18": n_lt18,
                    "n_ge65": rng.integers(0, 2, n),
                    "n_women_15_45": rng.integers(0, 2, n),
                    "n_women_15_plus": rng.integers(0, 3, n),
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def cps_donors(seed: int, reference_years, n: int = 1500) -> pd.DataFrame:
    """CPS ASEC donor households (the output of build_household_donors)."""
    rng = np.random.default_rng(seed + 1)
    frames = []
    for reference_year in reference_years:
        survey_year = reference_year + 1
        n_people = rng.integers(1, 7, n)
        n_lt18 = np.minimum(rng.integers(0, 4, n), n_people - 1)
        n_lt5 = np.minimum(rng.integers(0, 2, n), n_lt18)
        n_5_18 = np.maximum(n_lt18 - n_lt5, 0)
        nslp = (n_5_18 > 0) & (rng.uniform(size=n) < 0.4)
        liheap = rng.uniform(size=n) < 0.05
        wic = rng.uniform(size=n) < 0.05
        broadband = rng.uniform(size=n) < 0.05
        census = survey_year >= 2019
        frames.append(
            pd.DataFrame(
                {
                    "survey_year": survey_year,
                    "reference_year": reference_year,
                    "household_id": np.arange(1, n + 1),
                    "weight": rng.uniform(500, 5000, n),
                    "region": rng.choice([1, 2, 3, 4], n).astype(float),
                    "state": rng.choice(FIPS, n).astype(float),
                    "tenure": rng.choice(
                        [
                            "renter",
                            "owner_with_mortgage",
                            "owner_without_mortgage",
                        ],
                        n,
                    ),
                    "income": rng.lognormal(10.5, 1.0, n),
                    "snap": rng.uniform(size=n) < 0.12,
                    "housing_assistance": rng.uniform(size=n) < 0.04,
                    "nslp": nslp,
                    "nslp_children": np.where(nslp, n_5_18, 0),
                    "wic": wic,
                    "wic_people": np.where(wic, 2, 0),
                    "liheap": liheap,
                    "liheap_amount": np.where(
                        liheap, rng.lognormal(6.0, 0.6, n), 0.0
                    ),
                    "n_people": n_people,
                    "n_lt5": n_lt5,
                    "n_5_18": n_5_18,
                    "n_lt18": n_lt18,
                    "n_ge65": rng.integers(0, 2, n),
                    "n_women_15_45": rng.integers(0, 2, n),
                    "n_women_15_plus": rng.integers(0, 3, n),
                    "n_earners": rng.integers(0, 3, n),
                    "public_assistance": rng.uniform(size=n) < 0.03,
                    "ssi": rng.uniform(size=n) < 0.04,
                    "head_age": rng.integers(18, 85, n),
                    "head_female": rng.uniform(size=n) < 0.5,
                    "head_race": rng.choice(["white", "black", "other"], n),
                    "head_hispanic": rng.uniform(size=n) < 0.2,
                    "head_education": rng.choice(
                        ["lt_hs", "hs", "some_college", "ba_plus"], n
                    ),
                    "broadband": broadband,
                    "broadband_months": np.where(broadband, 6, 0),
                    "spm_schlunch": np.where(nslp, 700.0 * n_5_18, 0.0)
                    if census
                    else np.nan,
                    "spm_wicval": np.nan,
                    "spm_engval": np.nan,
                    "spm_bbsubval": np.where(broadband, 360.0, 0.0)
                    if census
                    else np.nan,
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


@pytest.fixture(scope="module")
def imputed():
    years = (2017, 2022)
    units = ce_units(7, years)
    donors = cps_donors(7, years)
    config = InKindConfig(implicates=2)
    return units, donors, config, impute_in_kind(units, donors, config)


# ---------------------------------------------------------------------------
# Invariants 2 and 3: the threshold stage
# ---------------------------------------------------------------------------


STAGE_COLUMNS = ("fcsuti_threshold_year", "fcsuti_2a2c", "su_2a2c")


def expenditures_for(raw: pd.DataFrame, target_year: int = 2024):
    normalized, _ = normalize_ce_sample(raw)
    frame, _ = construct_normalized_expenditures(
        normalized, target_year, cpi_series=flat_cpi()
    )
    return frame


@PROPERTY
@given(
    seed=st.integers(0, 10_000),
    scale=st.floats(0, 20_000, allow_nan=False, allow_infinity=False),
)
def test_stage_never_lowers_fcsuti_or_su(seed, scale):
    raw = ce_frame(seed)
    rng = np.random.default_rng(seed)
    amounts = rng.uniform(0, 1, (len(raw), len(IN_KIND_COMPONENTS))) * scale
    amounts[rng.uniform(size=amounts.shape) < 0.5] = 0.0
    without = expenditures_for(raw)
    with_stage, stage = add_in_kind(
        without, in_kind_table(raw["NEWID"], amounts)
    )
    for column in STAGE_COLUMNS:
        assert (
            with_stage[column].to_numpy() >= without[column].to_numpy()
        ).all()
    # The stage changes only its three outputs and adds its own columns.
    untouched = [c for c in without.columns if c not in STAGE_COLUMNS]
    pd.testing.assert_frame_equal(with_stage[untouched], without[untouched])
    factor = without["inflation_factor"].to_numpy()
    scale_2a2c = without["fcsuti_2a2c"] / without["fcsuti_threshold_year"]
    np.testing.assert_allclose(
        with_stage["fcsuti_threshold_year"] - without["fcsuti_threshold_year"],
        with_stage["fcsuti_in_kind"] * factor,
        rtol=1e-9,
        atol=1e-6,
    )
    shelter = with_stage[["in_kind_liheap", "in_kind_rental_assistance"]]
    np.testing.assert_allclose(
        with_stage["su_2a2c"] - without["su_2a2c"],
        shelter.sum(axis=1) * factor * scale_2a2c.fillna(0),
        rtol=1e-9,
        atol=1e-6,
    )
    assert 0 <= stage["weighted_share_with_any"] <= 1


@PROPERTY
@given(seed=st.integers(0, 10_000))
def test_zero_in_kind_reproduces_out_of_pocket_exactly(seed):
    raw = ce_frame(seed)
    run = prepare_out_of_pocket(raw, 2024, cpi_series=flat_cpi())
    base = replicate_thresholds(raw, 2024, cpi_series=flat_cpi())
    for key in ("thresholds", "methodology_id", "construction", "estimation"):
        assert run.result[key] == base[key]
    zero = replicate_with_in_kind(run, zero_in_kind(raw["NEWID"]))
    assert zero["thresholds"] == base["thresholds"]
    assert zero["estimation"] == base["estimation"]
    frame, _ = add_in_kind(run.expenditures, zero_in_kind(raw["NEWID"]))
    for column in STAGE_COLUMNS:
        np.testing.assert_array_equal(frame[column], run.expenditures[column])


def test_staged_methodology_extends_the_out_of_pocket_config():
    raw = ce_frame(3)
    run = prepare_out_of_pocket(raw, 2024, cpi_series=flat_cpi())
    assert "in_kind" not in run.result["methodology_config"]
    assert run.result["methodology_config"]["implementation"] == (
        "ce-fmli-annual-cpi-research-v1"
    )
    staged = replicate_with_in_kind(run, zero_in_kind(raw["NEWID"]))
    config = dict(staged["methodology_config"])
    assert config.pop("in_kind")["implementation"] == "zero"
    assert config == run.result["methodology_config"]
    assert staged["methodology_id"].startswith(f"{METHODOLOGY_PREFIX}:")
    assert staged["out_of_pocket"] == {
        "methodology_id": run.result["methodology_id"],
        "thresholds": run.result["thresholds"],
    }
    other = zero_in_kind(raw["NEWID"])
    other.attrs["methodology"] = {"implementation": "zero", "implicate": 1}
    assert (
        replicate_with_in_kind(run, other)["methodology_id"]
        != staged["methodology_id"]
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_committed_receipt_comes_from_this_code():
    """The committed in-kind receipt matches the code and parameters here."""
    receipt = json.loads(RECEIPT.read_text())
    for name, digest in receipt["code_sha256"].items():
        assert sha256(ROOT / name) == digest, f"rerun the replication: {name}"
    assert receipt["in_kind_methodology"]["parameter_files_sha256"] == (
        ce_inkind._parameter_hashes()
    )
    archived = ROOT / receipt["archived_receipt_check"]["path"]
    assert sha256(archived) == receipt["archived_receipt_check"]["sha256"]
    assert receipt["archived_receipt_check"][
        "out_of_pocket_thresholds_identical"
    ]
    assert receipt["archived_receipt_check"][
        "zero_in_kind_thresholds_identical"
    ]
    for result in receipt["results"].values():
        for tenure in TENURES:
            assert (
                result["with_in_kind"][tenure]
                >= result["without_in_kind"][tenure]
            )


def test_in_kind_shifts_thresholds_by_known_amounts():
    """Identical families: the lift is analytic.

    Every unit gets the same FCSUti and SU, so the band means are those
    values: T = 0.82 (1.2 FCSUti - SU + SU_h) with SU_h = SU.
    """
    raw = ce_frame(11).iloc[:3].copy()
    for column in raw.columns:
        if column.endswith(("PQ", "CQ")):
            raw[column] = 0.0
    raw["FOODPQ"] = raw["FOODCQ"] = 100.0
    raw["SHELTPQ"] = raw["SHELTCQ"] = 300.0
    raw["PERSLT18"], raw["FAM_SIZE"] = 2, 4
    amounts = np.zeros((3, len(IN_KIND_COMPONENTS)))
    amounts[:, IN_KIND_COMPONENTS.index("nslp")] = 400.0
    amounts[:, IN_KIND_COMPONENTS.index("rental_assistance")] = 1200.0
    run = prepare_out_of_pocket(raw, 2023, cpi_series=flat_cpi())
    result = replicate_with_in_kind(run, in_kind_table(raw["NEWID"], amounts))
    fcsuti = 4 * (100 + 100 + 300 + 300) + 400 + 1200
    for tenure in TENURES:
        assert result["thresholds"][tenure] == pytest.approx(
            0.82 * 1.2 * fcsuti
        )
        assert run.result["thresholds"][tenure] == pytest.approx(
            0.82 * 1.2 * (fcsuti - 1600)
        )


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda t: t.assign(nslp=-1.0), "nonnegative"),
        (lambda t: t.assign(wic=np.nan), "nonnegative"),
        (lambda t: t.assign(liheap="x"), "numeric"),
        (lambda t: pd.concat([t, t.iloc[:1]]), "unique"),
        (lambda t: t.iloc[1:], "lacks sample units"),
        (lambda t: t.drop(columns=["broadband"]), "missing columns"),
    ],
)
def test_invalid_in_kind_tables_are_rejected(mutate, message):
    raw = ce_frame(2)
    table = mutate(zero_in_kind(raw["NEWID"]))
    with pytest.raises(ValueError, match=message):
        add_in_kind(expenditures_for(raw), table)


def test_stage_accepts_extra_units_and_needs_constructed_frames():
    raw = ce_frame(4)
    expenditures = expenditures_for(raw)
    extra = zero_in_kind(np.append(raw["NEWID"].to_numpy(), [10**9]))
    frame, _ = add_in_kind(expenditures, extra)
    assert len(frame) == len(expenditures)
    with pytest.raises(ValueError, match="construct_normalized_expenditures"):
        add_in_kind(raw, zero_in_kind(raw["NEWID"]))


# ---------------------------------------------------------------------------
# Invariants 1, 4 and 5: the imputation
# ---------------------------------------------------------------------------


def test_amounts_are_finite_and_nonnegative(imputed):
    _, _, _, result = imputed
    for frame in result.implicates:
        values = frame[list(IN_KIND_COMPONENTS)].to_numpy()
        assert np.isfinite(values).all() and (values >= 0).all()
    assert any(
        (frame[list(IN_KIND_COMPONENTS)] > 0).any().any()
        for frame in result.implicates
    )


@PROPERTY
@given(seed=st.integers(0, 1_000_000))
def test_amounts_nonnegative_for_any_inputs(seed):
    years = (2016, 2023)
    result = impute_in_kind(
        ce_units(seed, years, n=150),
        cps_donors(seed, years, n=600),
        InKindConfig(implicates=1),
    )
    values = result.implicates[0][list(IN_KIND_COMPONENTS)].to_numpy()
    assert np.isfinite(values).all() and (values >= 0).all()


def test_order_invariance_and_determinism(imputed):
    units, donors, config, result = imputed
    shuffled = units.sample(frac=1.0, random_state=3)
    again = impute_in_kind(
        shuffled, donors.sample(frac=1.0, random_state=4), config
    )
    for m in range(config.implicates):
        a = result.implicates[m].set_index("NEWID").sort_index()
        b = again.implicates[m].set_index("NEWID").sort_index()
        pd.testing.assert_frame_equal(a, b)


def test_universes(imputed):
    units, _, _, result = imputed
    by_key = units.set_index("key")
    ebt = ce_inkind._wic_ebt_complete(
        ce_inkind._with_poverty_ratio(units), InKindConfig()
    )
    ebt = pd.Series(ebt, index=units["key"])
    for frame in result.implicates:
        f = frame.set_index("NEWID")
        u = by_key.loc[f.index]
        assert (u.loc[f["nslp"] > 0, "n_5_18"] >= 1).all()
        wic = f["wic"] > 0
        assert (~ebt.loc[f.index][wic]).all()
        broad = (u["n_women_15_45"] >= 1) | (u["n_lt5"] >= 1)
        assert broad[wic].all()
        assert (u.loc[f["broadband"] > 0, "reference_year"] >= 2021).all()
        rent = f["rental_assistance"] > 0
        assert (u.loc[rent, "cutenure"] == 4).all()
        assert u.loc[rent, "housing_assistance"].all()


def test_wic_universe_vintage_rule(imputed):
    _, _, _, result = imputed
    frame = result.implicates[0]
    broad_only = frame["wic_broad_universe_only"]
    before = result.for_target(0, 2024)
    after = result.for_target(0, 2025)
    assert (before.loc[broad_only.to_numpy(), "wic"] == 0).all()
    pd.testing.assert_series_equal(
        after["wic"], frame["wic"], check_names=False
    )
    assert after.attrs["methodology"]["vintage_rules_for_target"] == 2025


def test_hashed_uniform_is_keyed_and_bounded():
    keys = np.arange(10_000)
    u = hashed_uniform(keys, seed=1, stream="s", implicate=0)
    assert ((u > 0) & (u < 1)).all()
    assert abs(u.mean() - 0.5) < 0.02
    reordered = hashed_uniform(keys[::-1], seed=1, stream="s", implicate=0)
    np.testing.assert_array_equal(u, reordered[::-1])
    other = hashed_uniform(keys, seed=1, stream="t", implicate=0)
    assert not np.array_equal(u, other)


# ---------------------------------------------------------------------------
# Parameters and models
# ---------------------------------------------------------------------------


def test_official_poverty_thresholds():
    assert opm_threshold(2022, [4], [2], [40])[0] == 29678
    assert opm_threshold(2014, [1], [0], [30])[0] == 12316
    assert opm_threshold(2014, [1], [0], [70])[0] == 11354
    assert opm_threshold(2024, [2], [1], [70])[0] == 21540
    # Sizes above nine and impossible child counts are capped.
    assert opm_threshold(2024, [12], [11], [40])[0] == 60645
    assert opm_threshold(2024, [3], [3], [40])[0] == 25273


@pytest.mark.parametrize("reference_year", ["2018", "2019"])
def test_nslp_formula_matches_census_values(reference_year):
    """Differential check: USDA formula vs the values Census assigned."""
    observed = json.loads(
        (DATA / "census_school_lunch_observed.json").read_text()
    )["reference_years"][reference_year]
    formula = nslp_formula_values(int(reference_year))
    assert abs(formula["free"] - observed["free"]) <= 2
    assert abs(formula["reduced"] - observed["reduced"]) <= 2
    assert formula["free"] > formula["reduced"] > 0


def test_nslp_formula_covers_window_years():
    values = [nslp_formula_values(y)["free"] for y in range(2014, 2025)]
    assert all(b > a for a, b in zip(values, values[1:]))


def test_wic_ebt_dates():
    dates = wic_ebt_statewide_dates()
    assert len(dates) == 51
    assert dates[13].isoformat() == "2022-10-26"  # Georgia
    assert dates[11].isoformat() == "2022-04-27"  # District of Columbia
    assert max(dates.values()).isoformat() == "2022-10-26"


def test_logit_recovers_coefficients():
    rng = np.random.default_rng(0)
    x = np.column_stack([np.ones(20_000), rng.normal(size=(20_000, 2))])
    beta = np.array([-1.0, 0.8, -0.5])
    y = rng.uniform(size=20_000) < 1 / (1 + np.exp(-(x @ beta)))
    estimate, covariance = fit_logit(x, y.astype(float), np.ones(20_000), 0.0)
    assert np.allclose(estimate, beta, atol=4 * np.sqrt(np.diag(covariance)))


def test_tobit_recovers_parameters_under_censoring():
    """Right censoring at points drawn independently of the latent error.

    The censoring point plays the part of rent paid by an assisted renter:
    the model knows only that market rent exceeds it. About 38% of rows
    censor. Tolerances are about four standard errors.
    """
    rng = np.random.default_rng(1)
    n = 20_000
    x = np.column_stack([np.ones(n), rng.normal(size=n)])
    beta, sigma = np.array([7.0, 0.3]), 0.4
    latent = x @ beta + sigma * rng.normal(size=n)
    point = x @ beta + rng.normal(0.2, 0.5, n)
    censored = latent > point
    y = np.where(censored, point, latent)
    assert 0.3 < censored.mean() < 0.45
    theta, covariance = fit_tobit(x, y, censored, np.ones(n), ridge=0.0)
    assert 1 / theta[-1] == pytest.approx(sigma, abs=0.012)
    assert theta[:-1] / theta[-1] == pytest.approx(beta, abs=0.015)
    assert np.all(np.linalg.eigvalsh(covariance) > 0)
    # Ignoring the censoring biases the fit: market rent exceeds the point.
    naive = np.linalg.lstsq(x, y, rcond=None)[0]
    assert naive[0] < beta[0] - 0.05


def test_unit_inputs_from_bundle(tmp_path):
    """MEMI counts, RNT assistance flags and rent paid from a bundle."""
    memi = pd.DataFrame(
        {
            "NEWID": [11, 11, 11, 21, 31],
            "AGE": [35, 4, 1, 70, 40],
            "SEX": [2, 1, 2, 1, 1],
        }
    )
    rent = pd.DataFrame(
        {
            "QYEAR": [20232, 20232, 20233, 20232],
            "NEWID": [11, 11, 21, 31],
            "GOVTCOST": [2, 1, 1, 2],
            "PUBLHOUS": [2, 2, 2, 2],
            # Rent for the three-month reference period; "." is missing.
            "QRT3MCMX": ["2400", "600", "3000", "."],
        }
    )
    with zipfile.ZipFile(tmp_path / "intrvw23.zip", "w") as archive:
        archive.writestr("intrvw23/memi232.csv", memi.to_csv(index=False))
        archive.writestr("intrvw23/expn23/rnt23.csv", rent.to_csv(index=False))
    units = load_ce_unit_inputs([(2023, 2)], cache_dir=tmp_path).set_index(
        "NEWID"
    )
    assert units.loc[11, "n_lt5"] == 2 and units.loc[11, "n_infants"] == 1
    assert units.loc[11, "n_women_15_45"] == 1
    assert units.loc[11, "housing_assistance"]
    assert not units.loc[21, "housing_assistance"]  # its record is 2023Q3
    assert units.loc[21, "n_ge65"] == 1
    # Records of one unit add up; other quarters and missing amounts don't.
    assert units.loc[11, "rent_paid_monthly"] == pytest.approx(1000.0)
    assert units.loc[21, "rent_paid_monthly"] == 0.0
    assert units.loc[31, "rent_paid_monthly"] == 0.0


def _cached_bundle(year: int) -> Path:
    bundle = ce_threshold._default_cache_dir() / f"intrvw{year % 100:02d}.zip"
    if not (bundle.exists() and zipfile.is_zipfile(bundle)):
        pytest.skip(f"CE bundle {bundle.name} is not cached")
    return bundle


@pytest.mark.parametrize("year", [2015, 2019, 2024])
def test_rent_paid_equals_ucc_210110(year):
    """Differential check on cached CE data: the rent records' QRT3MCMX,
    summed per unit, equals the unit's rent-of-dwelling expenditure in the
    MTBI file (UCC 210110). The FMLI summary RENDWEPQ + RENDWECQ also
    counts maintenance, insurance and other costs, so it differs."""
    bundle = _cached_bundle(year)
    units = load_ce_unit_inputs(
        [(year, 2)], cache_dir=bundle.parent
    ).set_index("NEWID")
    mtbi = ce_threshold._read_bundle_member(
        bundle, f"mtbi{year % 100:02d}2.csv"
    )
    mtbi = mtbi.apply(pd.to_numeric, errors="coerce")
    ucc = mtbi.loc[mtbi["UCC"] == 210110].groupby("NEWID")["COST"].sum()
    paid = 3 * units["rent_paid_monthly"]
    expected = ucc.reindex(paid.index, fill_value=0.0)
    np.testing.assert_allclose(paid.to_numpy(), expected.to_numpy(), atol=0.5)
    assert (paid > 0).sum() > 1000


def test_ce_unit_covariates_harmonizes_codes():
    fmli = pd.DataFrame(
        {
            "NEWID": [11, 21],
            "FINLWT21": [100.0, 200.0],
            "ce_year": [2024, 2024],
            "ce_quarter": [1, 3],
            "REGION": [3, "."],
            "STATE": [13, np.nan],
            "CUTENURE": [4, 3],
            "FINCBTXM": [20000, 90000],
            "JFS_AMT": [1200, "."],
            "EDUC_REF": [11, 15],
            "REF_RACE": [2, 4],
            "BUILDING": [11, 7],
            # The FMLI summary adds non-rent costs; the model ignores it.
            "RENDWEPQ": [900, 0],
            "RENDWECQ": [300, 0],
            "FAM_SIZE": [3, 2],
        }
    )
    units = pd.DataFrame(
        {
            "NEWID": [11, 21],
            "n_lt5": [1, 0],
            "n_infants": [0, 0],
            "n_5_18": [1, 0],
            "n_lt18": [2, 0],
            "n_ge65": [0, 1],
            "n_women_15_45": [1, 0],
            "n_women_15_plus": [1, 1],
            "housing_assistance": [True, False],
            "rent_paid_monthly": [380.0, 0.0],
        }
    )
    out = ce_unit_covariates(fmli, units)
    assert out["reference_year"].tolist() == [2023, 2024]
    assert out["tenure"].tolist() == ["renter", "owner_with_mortgage"]
    assert out["snap"].tolist() == [True, False]
    assert out["head_education"].tolist() == ["lt_hs", "ba_plus"]
    assert out["head_race"].tolist() == ["black", "other"]
    assert out["building"].tolist() == ["detached_mobile_other", "high_rise"]
    assert np.isnan(out.loc[1, "region"]) and np.isnan(out.loc[1, "state"])
    assert out.loc[0, "rent_paid_monthly"] == pytest.approx(380.0)


def unit_inputs_for(newids) -> pd.DataFrame:
    """Member and rent inputs for FMLI-only tests: one adult per unit."""
    n = len(newids)
    frame = pd.DataFrame({"NEWID": list(newids)})
    for column in (
        "n_lt5",
        "n_infants",
        "n_5_18",
        "n_lt18",
        "n_ge65",
        "n_women_15_45",
    ):
        frame[column] = np.zeros(n, dtype=int)
    frame["n_women_15_plus"] = np.ones(n, dtype=int)
    frame["housing_assistance"] = False
    frame["rent_paid_monthly"] = 0.0
    return frame


def test_snap_flag_counts_receipt_without_a_raw_amount():
    """CE completes a missing SNAP amount in JFS_AMTM, and FS_MTHI counts
    months of receipt, so either marks a recipient. 2024Q2 NEWID 5422304
    reports FS_MTHI 12 and JFS_AMTM 3,679 with JFS_AMT 0."""
    fmli = pd.DataFrame(
        {
            "NEWID": [11, 21, 31, 41, 51],
            "ce_year": 2024,
            "ce_quarter": 2,
            "CUTENURE": 4,
            "JFS_AMT": [1200, 0, 0, ".", "."],
            "JFS_AMTM": [1200, 3679, 0, ".", 0],
            "FS_MTHI": [12, 12, 3, ".", 0],
        }
    )
    out = ce_unit_covariates(fmli, unit_inputs_for(fmli["NEWID"]))
    assert out["snap"].tolist() == [True, True, True, False, False]


def test_wic_counts_a_mother_only_when_a_woman_lives_in_the_unit():
    """Census's rule: children under 5, the mother of an infant, otherwise
    one pregnant woman. A father raising an infant adds no participant."""
    units = pd.DataFrame(
        {
            "n_lt5": [1, 1, 2, 0],
            "n_infants": [1, 1, 0, 0],
            "n_women_15_plus": [0, 1, 1, 1],
        }
    )
    participants = ce_inkind._wic_participants(units)
    assert participants.tolist() == [1.0, 2.0, 2.0, 1.0]


def test_rental_assistance_is_zero_without_assisted_renters():
    """An owner-only sample has no rent rows to fit; the component returns
    zeros instead of failing."""
    units = ce_units(5, (2022,), n=60)
    units["cutenure"] = 1.0
    units["tenure"] = "owner_with_mortgage"
    units["housing_assistance"] = False
    units["rent_paid_monthly"] = 0.0
    result = impute_in_kind(
        units,
        cps_donors(5, (2022,), n=300),
        InKindConfig(implicates=2, components=("rental_assistance",)),
    )
    for frame in result.implicates:
        assert (frame[list(IN_KIND_COMPONENTS)] == 0).all().all()
    diagnostics = result.diagnostics["rental_assistance"]["2022"]
    assert diagnostics["assisted"] == 0 and diagnostics["sigma"] is None


# ---------------------------------------------------------------------------
# Invariant 7: predictive mean matching pools
# ---------------------------------------------------------------------------


def brute_force_distances(sorted_hat, target, k):
    return np.sort(np.abs(sorted_hat - target))[:k]


@PROPERTY
@given(
    donors=st.lists(st.integers(-20, 20).map(float), min_size=1, max_size=40),
    targets=st.lists(
        st.floats(-40, 40, allow_nan=False, allow_infinity=False),
        min_size=1,
        max_size=20,
    ),
    k=st.integers(1, 8),
)
def test_pmm_pools_hold_the_k_nearest_distinct_donors(donors, targets, k):
    """Differential checks, with ties (integer predictions) and targets
    outside the donor range: distances against brute force over all
    donors, and donor identities against a scalar reading of the
    documented rule (the window of 2k positions starting k before the
    insertion point, shifted inward at the ends; equal distances in
    window order)."""
    sorted_hat = np.sort(np.array(donors))
    target_hat = np.array(targets)
    pools = ce_inkind._pmm_pools(sorted_hat, target_hat, k)
    n = len(sorted_hat)
    size = min(k, n)
    width = min(2 * size, n)
    assert pools.shape == (len(target_hat), size)
    for row, target in zip(pools, target_hat):
        assert len(set(row.tolist())) == size
        distances = np.abs(sorted_hat[row] - target)
        assert np.all(np.diff(distances) >= 0)  # nearest first
        np.testing.assert_array_equal(
            distances, brute_force_distances(sorted_hat, target, size)
        )
        insertion = int(np.searchsorted(sorted_hat, target))
        start = min(max(insertion - size, 0), n - width)
        window = range(start, start + width)
        expected = sorted(
            window, key=lambda j: (abs(sorted_hat[j] - target), j)
        )[:size]
        assert row.tolist() == expected


def test_pmm_pools_at_the_boundaries():
    """Targets beyond either end get the k end donors, not k copies of
    the end donor."""
    sorted_hat = np.arange(10.0)
    pools = ce_inkind._pmm_pools(sorted_hat, np.array([-100.0, 100.0]), 5)
    assert pools[0].tolist() == [0, 1, 2, 3, 4]
    assert pools[1].tolist() == [9, 8, 7, 6, 5]
    few = ce_inkind._pmm_pools(np.array([1.0, 2.0, 3.0]), np.array([0.0]), 5)
    assert sorted(few[0].tolist()) == [0, 1, 2]
    with pytest.raises(ValueError, match="at least one donor"):
        ce_inkind._pmm_pools(np.array([]), np.array([0.0]), 5)


def test_pmm_pools_take_ties_first_in_the_window():
    """The documented tie rule. Twenty donors share one prediction; a
    target above them takes the last five of the window, positions 10-14,
    not the five lowest positions overall."""
    tied = np.zeros(20)
    assert ce_inkind._pmm_pools(tied, np.array([1.0]), 5)[0].tolist() == [
        10,
        11,
        12,
        13,
        14,
    ]
    assert ce_inkind._pmm_pools(tied, np.array([-1.0]), 5)[0].tolist() == [
        0,
        1,
        2,
        3,
        4,
    ]


@pytest.mark.parametrize("constant", [False, True])
def test_constant_participation_needs_no_model(constant):
    """When every CPS donor participates, or none does, the logit has no
    finite estimate. Imputation takes the constant outcome instead of
    failing, and its diagnostics stay valid JSON. Broadband reaches the
    all-zero case once Census's values end with the ACP."""
    units = ce_units(11, (2022,), n=150)
    donors = cps_donors(11, (2022,), n=500)
    rng = np.random.default_rng(11)
    in_lunch_universe = donors["n_5_18"].to_numpy() >= 1
    donors["nslp"] = constant & in_lunch_universe
    donors["nslp_children"] = np.where(donors["nslp"], donors["n_5_18"], 0)
    donors["spm_schlunch"] = np.where(
        donors["nslp"], 700.0 * donors["n_5_18"], 0.0
    )
    donors["wic"] = constant
    donors["liheap"] = constant
    donors["liheap_amount"] = (
        rng.lognormal(6.0, 0.6, len(donors)) if constant else 0.0
    )
    donors["broadband"] = constant
    donors["spm_bbsubval"] = 360.0 if constant else 0.0
    components = ("nslp", "wic", "liheap", "broadband")
    result = impute_in_kind(
        units, donors, InKindConfig(implicates=2, components=components)
    )
    for component in components:
        diagnostics = result.diagnostics[component]["2022"]
        assert diagnostics["constant_outcome"] is constant
        assert diagnostics["ce_participation_rate_mean"] == float(constant)
    for frame in result.implicates:
        f = frame.set_index("NEWID")
        u = units.set_index("key").loc[f.index]
        if constant:
            assert (f["liheap"] > 0).all() and (f["broadband"] > 0).all()
            assert (f.loc[u["n_5_18"].to_numpy() >= 1, "nslp"] > 0).all()
        else:
            assert (f[list(components)] == 0).all().all()
    json.dumps(result.diagnostics, allow_nan=False)


def test_unit_inputs_must_come_from_the_loader():
    fmli = pd.DataFrame({"NEWID": [11], "ce_year": [2024], "ce_quarter": [2]})
    units = unit_inputs_for([11]).drop(columns="rent_paid_monthly")
    with pytest.raises(ValueError, match="rent_paid_monthly"):
        ce_unit_covariates(fmli, units)


# ---------------------------------------------------------------------------
# Invariant 6: decomposition
# ---------------------------------------------------------------------------

positive = st.floats(1_000, 100_000, allow_nan=False, allow_infinity=False)


@PROPERTY
@given(
    values=st.lists(positive, min_size=12, max_size=12),
    price=st.floats(0.5, 2.0),
)
def test_decomposition_identity(values, price):
    def tenure_map(chunk):
        return dict(zip(TENURES, chunk))

    parts = [tenure_map(values[i : i + 3]) for i in range(0, 12, 3)]
    out = decompose_growth(
        base_without=parts[0],
        base_with=parts[1],
        target_without=parts[2],
        target_with=parts[3],
        price=price,
    )
    for tenure in TENURES:
        factors = out[tenure]["factors"]
        product = (
            factors["price"]
            * factors["real_out_of_pocket"]
            * factors["in_kind"]
        )
        assert product == pytest.approx(factors["total"], rel=1e-12)
        points = out[tenure]["percentage_points"]
        assert points["price"] + points["real_out_of_pocket"] + points[
            "in_kind"
        ] == pytest.approx(points["total"], abs=1e-9)
        logs = out[tenure]["log_points"]
        assert logs["price"] + logs["real_out_of_pocket"] + logs[
            "in_kind"
        ] == pytest.approx(logs["total"], abs=1e-9)


def test_no_in_kind_change_means_unit_in_kind_factor():
    same = dict(zip(TENURES, (30_000.0, 31_000.0, 27_000.0)))
    lifted = {t: 1.02 * v for t, v in same.items()}
    out = decompose_growth(
        base_without=same,
        base_with=lifted,
        target_without={t: 1.1 * v for t, v in same.items()},
        target_with={t: 1.1 * 1.02 * v for t, v in same.items()},
        price=1.05,
    )
    for tenure in TENURES:
        assert out[tenure]["factors"]["in_kind"] == pytest.approx(1.0)
        assert out[tenure]["factors"]["real_out_of_pocket"] == pytest.approx(
            1.1 / 1.05
        )


def test_bls_index_matches_published_inflation():
    """Differential check of BLS's level table against the rates its 2025
    thresholds page states: "annual FCSUti inflation fell to 3.44 percent
    in 2025, continuing to decline from a peak of 7.21 percent in 2022"
    (https://www.bls.gov/pir/spm/spm_thresholds_2025.htm)."""
    rate = {
        year: 100 * (bls_fcsuti_index(year) / bls_fcsuti_index(year - 1) - 1)
        for year in range(min(BLS_FCSUTI_INDEX) + 1, 2026)
    }
    assert round(rate[2025], 2) == 3.44
    assert round(rate[2022], 2) == 7.21
    assert max(rate, key=rate.get) == 2022
    assert rate[2022] > rate[2023] > rate[2024] > rate[2025]
    assert bls_fcsuti_index(2013) == 100.0
    with pytest.raises(ValueError, match="No BLS FCSUti index"):
        bls_fcsuti_index(2026)


@PROPERTY
@given(
    seed=st.integers(0, 10_000),
    target=st.sampled_from([2023, 2024, 2025]),
)
def test_single_index_deflator_scales_thresholds_by_its_ratio(seed, target):
    """With every CPI component set to BLS's FCSUti index, each unit's
    price factor is I(T) / I(c), so moving the target year scales every
    threshold by the index ratio (the FCSUti-deflated sensitivity)."""
    raw = ce_frame(seed)
    raw["ce_year"] = np.random.default_rng(seed).integers(2019, 2024, len(raw))
    index = pd.Series(BLS_FCSUTI_INDEX)
    cpi = {sid: index for sid in CPI_SERIES.values()}
    run = prepare_out_of_pocket(raw, target, cpi_series=cpi)
    factors = run.result["construction"]["inflation_factors"]
    assert set(factors) == set(raw["ce_year"].unique())
    for year, factor in factors.items():
        assert factor == pytest.approx(
            bls_fcsuti_index(target) / bls_fcsuti_index(year), rel=1e-12
        )
    base = prepare_out_of_pocket(raw, 2023, cpi_series=cpi)
    ratio = bls_fcsuti_index(target) / bls_fcsuti_index(2023)
    for tenure in TENURES:
        assert run.result["thresholds"][tenure] == pytest.approx(
            base.result["thresholds"][tenure] * ratio, rel=1e-9
        )
