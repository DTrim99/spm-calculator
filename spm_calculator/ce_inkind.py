"""BLS-style in-kind benefit imputation for CE Interview consumer units.

BLS adds the value of five in-kind benefits to each consumer unit's
out-of-pocket FCSU expenditures before estimating SPM thresholds
(https://www.bls.gov/pir/spmhome.htm, "Imputation of in-kind benefits at
the consumer unit level"). SNAP is excluded because EBT purchases are
assumed to appear in reported food spending. This module implements that
stage as an explicit, optional step:

``rental_assistance``
    BLS fits "a limited dependent variable model" of rent on CE renters,
    with rent "censored from above" for units receiving assistance, and
    values the benefit as imputed market rent minus rent paid. Here a
    weighted Tobit of log monthly rent on unit characteristics is fit per
    reference year. Units reporting ``GOVTCOST == 1`` or ``PUBLHOUS == 1``
    in the CE rent file are right-censored at their rent paid; their market
    rent is a draw from the fitted distribution above that rent.
``liheap`` and ``broadband``
    Participation from logistic regressions fit on pooled CPS ASEC
    households; amounts by predictive mean matching on the values CPS
    households report (LIHEAP) or Census assigns from FCC rules (EBB/ACP,
    reference years 2021 onward).
``nslp``
    Participation from a logistic regression on CPS households with a
    member aged 5-18 (the CE universe applies only that age rule). Values
    follow the Census method that BLS says it uses. Reference years with
    Census values in the public-use files (2018 onward) hot-deck the
    household value from participating CPS households in the same state,
    SNAP and free-lunch cell, which carries Census's COVID-era, P-EBT,
    Summer EBT and universal-free-meal rules. Earlier years apply the
    traditional formula: USDA reimbursement plus donated-food value per
    lunch, 5/9 spring and 4/9 fall school year, times 179 days; free below
    150 percent of the official poverty line, otherwise free or reduced
    with equal probability.
``wic``
    Participation from a logistic regression on CPS households in the
    WIC universe. Census's participant rule (all children under 5, the
    mother of a child aged 0-1, otherwise one pregnant woman) times 12
    times the USDA fiscal-year average monthly food cost. BLS adds WIC
    only where the state had not finished WIC EBT, so units in states
    whose statewide EBT date precedes the reference period receive none.

Every random draw is a deterministic function of the unit's identifier,
the seed, the component and the implicate, so results do not depend on
row order. Multiple implicates draw model parameters from their estimated
sampling distribution (proper multiple imputation).

Amounts are annual dollars of the CE reference year. They enter FCSUti
(all components) and, for ``liheap`` and ``rental_assistance``, the
shelter-and-utilities total; see :mod:`spm_calculator.ce_inkind_stage`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Optional, Sequence

import numpy as np
import pandas as pd

from spm_calculator.ce_inkind_stage import (
    IN_KIND_COMPONENTS,
    IN_KIND_SHELTER_UTILITY_COMPONENTS,
    zero_in_kind,
)
from spm_calculator.ce_threshold import _read_bundle_member, download_ce_bundle

IMPLEMENTATION = "ce-inkind-cps-asec-v1"

__all__ = [
    "IN_KIND_COMPONENTS",
    "IN_KIND_SHELTER_UTILITY_COMPONENTS",
    "IMPLEMENTATION",
    "InKindConfig",
    "InKindImputation",
    "ce_unit_covariates",
    "impute_in_kind",
    "load_ce_unit_inputs",
    "zero_in_kind",
]

DATA_DIR = Path(__file__).parent / "data" / "inkind"

#: Census traditional school lunch year and calendar weighting
#: (Shrider 2023, SEHSD-WP-2023-20, p. 2).
NSLP_SCHOOL_DAYS = 179
NSLP_SPRING_SHARE = 5 / 9

#: Free-lunch assignment cutoff relative to the official poverty line
#: (BLS spmhome; Census uses the same 150 percent rule).
FREE_LUNCH_POVERTY_RATIO = 1.5

#: EBB began in May 2021; Census values broadband from reference 2021.
BROADBAND_FIRST_REFERENCE_YEAR = 2021

#: BLS drops the woman requirement for children under 5 from the 2025
#: thresholds onward.
WIC_BROAD_UNIVERSE_FIRST_TARGET = 2025

#: Income-to-poverty bins shared by every participation model.
IPR_EDGES = (0.5, 1.0, 1.3, 1.5, 1.85, 2.5, 4.0)

#: Renters paying less than this per month are not used to fit market
#: rent (implausible market rents, such as employer-provided housing).
MIN_UNASSISTED_MONTHLY_RENT = 50.0

#: Columns :func:`ce_unit_covariates` needs from :func:`load_ce_unit_inputs`.
UNIT_INPUT_COLUMNS = (
    "NEWID",
    "n_lt5",
    "n_infants",
    "n_5_18",
    "n_lt18",
    "n_ge65",
    "n_women_15_45",
    "n_women_15_plus",
    "housing_assistance",
    "rent_paid_monthly",
)

_TENURE_BY_CUTENURE = {
    1: "owner_with_mortgage",
    2: "owner_without_mortgage",
    3: "owner_with_mortgage",
    4: "renter",
    5: "no_cash_rent",
    6: "no_cash_rent",
}

#: BUILDING groups that are stable across 2014-2025 collection. Between
#: April 2013 and July 2015 code 11 also held detached houses, dorms and
#: mobile homes (CE PUMD dictionary), so those codes share one group.
_BUILDING_GROUPS = {
    1: "detached_mobile_other",
    9: "detached_mobile_other",
    10: "detached_mobile_other",
    11: "detached_mobile_other",
    2: "row_townhouse",
    3: "row_townhouse",
    4: "duplex_plex",
    5: "duplex_plex",
    6: "garden",
    7: "high_rise",
    8: "apartment_flat",
}


@dataclass(frozen=True)
class InKindConfig:
    """Settings for :func:`impute_in_kind`.

    Attributes:
        implicates: Number of multiple-imputation implicates.
        seed: Seed for every hashed draw and parameter draw.
        ridge: L2 penalty on non-intercept logit and regression
            coefficients, in units of the weight-normalized sample.
        pmm_neighbors: Donor pool size for predictive mean matching.
        min_cell_donors: Smallest hot-deck cell used before collapsing.
        parameter_draws: Draw model parameters per implicate. ``False``
            uses point estimates (improper imputation; diagnostics only).
        components: Components to impute; the rest are zero.
    """

    implicates: int = 5
    seed: int = 20261105
    ridge: float = 1.0
    pmm_neighbors: int = 5
    min_cell_donors: int = 5
    parameter_draws: bool = True
    components: tuple = IN_KIND_COMPONENTS

    def __post_init__(self):
        if int(self.implicates) != self.implicates or self.implicates < 1:
            raise ValueError("implicates must be a positive integer")
        if not np.isfinite(self.ridge) or self.ridge < 0:
            raise ValueError("ridge must be finite and nonnegative")
        if self.pmm_neighbors < 1 or self.min_cell_donors < 1:
            raise ValueError("pmm_neighbors and min_cell_donors must be >= 1")
        unknown = set(self.components) - set(IN_KIND_COMPONENTS)
        if unknown:
            raise ValueError(f"Unknown in-kind components {sorted(unknown)}")

    def methodology(self) -> dict:
        settings = asdict(self)
        settings["components"] = list(self.components)
        return {"implementation": IMPLEMENTATION, **settings}


# ---------------------------------------------------------------------------
# Deterministic draws
# ---------------------------------------------------------------------------


def _salt(*parts) -> int:
    text = "|".join(str(part) for part in parts)
    return int.from_bytes(
        hashlib.sha256(text.encode("utf-8")).digest()[:8], "little"
    )


def _splitmix64(values: np.ndarray) -> np.ndarray:
    with np.errstate(over="ignore"):
        z = values + np.uint64(0x9E3779B97F4A7C15)
        z = (z ^ (z >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
        z = (z ^ (z >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
        return z ^ (z >> np.uint64(31))


def hashed_uniform(keys, *, seed: int, stream: str, implicate: int):
    """Uniform(0, 1) draws that depend only on each key and the stream.

    The value for a unit is the same whatever the order or composition of
    the frame it appears in, which makes the imputation order invariant.
    """
    raw = np.asarray(keys)
    if raw.size and (raw < 0).any():
        raise ValueError("Draw keys must be nonnegative integers")
    keyed = raw.astype(np.uint64) ^ np.uint64(_salt(seed, stream, implicate))
    bits = _splitmix64(keyed) >> np.uint64(11)
    return (bits.astype(np.float64) + 0.5) / float(2**53)


def _rng(*parts) -> np.random.Generator:
    return np.random.default_rng(_salt(*parts))


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------


@lru_cache(maxsize=None)
def load_opm_thresholds() -> dict:
    """Census official poverty thresholds, reference years 2014-2024."""
    document = json.loads((DATA_DIR / "opm_thresholds.json").read_text())
    return document["years"]


def opm_threshold(year, n_people, n_children, head_age) -> np.ndarray:
    """Official poverty threshold for each unit, by the Census matrix.

    Household size and children under 18 stand in for family size and
    related children, identically in CE and CPS.
    """
    years = np.broadcast_to(np.asarray(year, dtype=int), np.shape(n_people))
    size = np.clip(np.asarray(n_people, dtype=float), 1, 9).astype(int)
    kids = np.clip(np.nan_to_num(np.asarray(n_children, dtype=float)), 0, 8)
    kids = np.minimum(kids.astype(int), size - 1)
    old = np.nan_to_num(np.asarray(head_age, dtype=float)) >= 65
    out = np.full(size.shape, np.nan)
    tables = load_opm_thresholds()
    for y in np.unique(years):
        if str(int(y)) not in tables:
            raise ValueError(f"No official poverty thresholds for {y}")
        table = tables[str(int(y))]
        in_year = years == y
        one = in_year & (size == 1)
        out[one & old] = table["1"]["65_plus"]
        out[one & ~old] = table["1"]["under_65"]
        two = in_year & (size == 2)
        for flag, key in ((True, "65_plus"), (False, "under_65")):
            for child in (0, 1):
                mask = two & (old == flag) & (kids == child)
                out[mask] = table["2"][key][child]
        for persons in range(3, 10):
            row = table[str(persons)]
            mask = in_year & (size == persons)
            out[mask] = np.asarray(row)[np.minimum(kids[mask], len(row) - 1)]
    return out


@lru_cache(maxsize=None)
def _nslp_rates() -> pd.DataFrame:
    rates = pd.read_csv(DATA_DIR / "usda_nslp_rates.csv")
    return rates.set_index("school_year")


def nslp_formula_values(reference_year: int) -> dict[str, float]:
    """Census traditional annual per-child free and reduced lunch values.

    Per-lunch USDA national average reimbursement (below-60-percent rate)
    plus the donated-foods value, weighted 5/9 by the school year ending in
    ``reference_year`` and 4/9 by the one beginning in it, times 179 days.
    """
    rates = _nslp_rates()
    spring = f"{reference_year - 1}-{reference_year % 100:02d}"
    fall = f"{reference_year}-{(reference_year + 1) % 100:02d}"
    missing = [year for year in (spring, fall) if year not in rates.index]
    if missing:
        raise ValueError(f"No USDA NSLP rates for school years {missing}")
    values = {}
    for kind, column in (
        ("free", "free_base_usd"),
        ("reduced", "reduced_price_base_usd"),
    ):
        per_meal = NSLP_SPRING_SHARE * (
            rates.at[spring, column]
            + rates.at[spring, "usda_foods_minimum_usd"]
        ) + (1 - NSLP_SPRING_SHARE) * (
            rates.at[fall, column] + rates.at[fall, "usda_foods_minimum_usd"]
        )
        values[kind] = float(NSLP_SCHOOL_DAYS * per_meal)
    return values


@lru_cache(maxsize=None)
def wic_monthly_food_cost() -> dict[int, float]:
    """USDA national average monthly WIC food cost per person, by FY."""
    table = pd.read_csv(DATA_DIR / "usda_wic_monthly_cost.csv")
    return {
        int(row.fiscal_year): float(
            row.average_monthly_food_cost_per_person_usd
        )
        for row in table.itertuples()
    }


@lru_cache(maxsize=None)
def wic_ebt_statewide_dates() -> dict[int, date]:
    """Statewide WIC EBT implementation date by state FIPS code.

    Covers the 50 states and DC from the FNS WIC EBT detail status report;
    tribal organizations and territories are not CE geography.
    """
    import us

    table = pd.read_csv(DATA_DIR / "usda_wic_ebt_statewide.csv")
    dates = {}
    for row in table.itertuples():
        if row.agency_type not in ("State", "District of Columbia"):
            continue
        if row.agency_type == "District of Columbia":
            state = us.states.DC
        else:
            state = us.states.lookup(str(row.agency))
        if state is None:
            raise ValueError(f"Unknown WIC agency {row.agency}")
        dates[int(state.fips)] = date.fromisoformat(
            str(row.statewide_implementation_date)
        )
    if len(dates) != 51:
        raise ValueError(f"Expected 51 WIC EBT dates, found {len(dates)}")
    return dates


# ---------------------------------------------------------------------------
# CE inputs
# ---------------------------------------------------------------------------


def _quarter_member_candidates(year: int, quarter: int, prefix: str):
    yy = f"{year % 100:02d}"
    if quarter == 1:
        return [
            (year - 1, f"{prefix}{yy}1.csv"),
            (year, f"{prefix}{yy}1x.csv"),
        ]
    return [(year, f"{prefix}{yy}{quarter}.csv")]


def _bundle(year: int, cache_dir: Optional[Path]) -> Path:
    return download_ce_bundle(year, cache_dir=cache_dir, allow_download=False)


def load_ce_unit_inputs(
    quarters: Sequence[tuple[int, int]], cache_dir: Optional[Path] = None
) -> pd.DataFrame:
    """Member composition and rental-assistance flags for CE quarters.

    Reads the MEMI member file and the rented-living-quarters expenditure
    file (``rntYY.csv``, ``QYEAR`` = collection year and quarter) from the
    cached CE bundles. Never downloads.

    Returns:
        One row per ``NEWID`` with member counts by age and sex,
        ``housing_assistance`` (any rent record with ``GOVTCOST == 1`` or
        ``PUBLHOUS == 1``) and ``rent_paid_monthly``: the unit's rent
        records' ``QRT3MCMX`` (rent for the three-month reference period)
        summed and divided by three. That sum equals the unit's
        rent-of-dwelling expenditure, UCC 210110, and leaves out the
        maintenance, insurance and other costs that the FMLI summary
        ``RENDWEPQ + RENDWECQ`` adds. A missing ``QRT3MCMX`` counts as no
        rent, as it does in UCC 210110.
    """
    rent_files: dict[int, pd.DataFrame] = {}
    pieces = []
    for year, quarter in quarters:
        members = None
        for bundle_year, basename in _quarter_member_candidates(
            year, quarter, "memi"
        ):
            try:
                members = _read_bundle_member(
                    _bundle(bundle_year, cache_dir), basename
                )
                break
            except (FileNotFoundError, RuntimeError, ValueError):
                continue
        if members is None:
            raise FileNotFoundError(f"No CE MEMI file for {year}Q{quarter}")
        members = members[["NEWID", "AGE", "SEX"]].apply(
            pd.to_numeric, errors="coerce"
        )
        age, female = members["AGE"], members["SEX"] == 2
        flags = pd.DataFrame(
            {
                "NEWID": members["NEWID"],
                "n_lt5": age < 5,
                "n_infants": age <= 1,
                "n_5_18": (age >= 5) & (age <= 18),
                "n_lt18": age < 18,
                "n_ge65": age >= 65,
                "n_women_15_45": female & (age >= 15) & (age <= 45),
                "n_women_15_plus": female & (age >= 15),
            }
        )
        counts = flags.groupby("NEWID").sum().astype(int)
        rent = None
        bundles = [year - 1, year] if quarter == 1 else [year]
        for bundle_year in bundles:
            if bundle_year not in rent_files:
                try:
                    frame = _read_bundle_member(
                        _bundle(bundle_year, cache_dir),
                        f"rnt{bundle_year % 100:02d}.csv",
                    )
                except (FileNotFoundError, RuntimeError, ValueError):
                    continue
                rent_files[bundle_year] = frame[
                    ["QYEAR", "NEWID", "GOVTCOST", "PUBLHOUS", "QRT3MCMX"]
                ].apply(pd.to_numeric, errors="coerce")
            frame = rent_files[bundle_year]
            selected = frame.loc[frame["QYEAR"] == year * 10 + quarter]
            if len(selected):
                rent = selected
                break
        if rent is None:
            raise FileNotFoundError(f"No CE rent file for {year}Q{quarter}")
        assisted = (
            ((rent["GOVTCOST"] == 1) | (rent["PUBLHOUS"] == 1))
            .groupby(rent["NEWID"])
            .any()
        )
        counts["housing_assistance"] = assisted.reindex(
            counts.index, fill_value=False
        ).astype(bool)
        rent_paid = rent["QRT3MCMX"].fillna(0).groupby(rent["NEWID"]).sum()
        counts["rent_paid_monthly"] = (
            rent_paid.reindex(counts.index, fill_value=0.0).astype(float) / 3.0
        )
        counts["ce_year"], counts["ce_quarter"] = year, quarter
        pieces.append(counts.reset_index())
    out = pd.concat(pieces, ignore_index=True)
    if out["NEWID"].duplicated().any():
        raise ValueError("CE NEWID is not unique across the loaded quarters")
    return out


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(np.nan, index=frame.index)
    return pd.to_numeric(frame[column], errors="coerce")


def ce_unit_covariates(
    fmli: pd.DataFrame, units: pd.DataFrame
) -> pd.DataFrame:
    """Harmonize CE FMLI records with member and rent inputs.

    The columns match :func:`spm_calculator.cps_asec.build_household_donors`
    plus CE-only housing characteristics used by the rent model.
    """
    missing = sorted(set(UNIT_INPUT_COLUMNS) - set(units.columns))
    if missing:
        raise ValueError(
            f"CE unit inputs lack {missing}; build them with "
            "load_ce_unit_inputs"
        )
    f = fmli.reset_index(drop=True)
    newid = _numeric(f, "NEWID")
    if newid.isna().any() or newid.duplicated().any():
        raise ValueError("CE FMLI NEWID must be present and unique")
    merged = pd.DataFrame({"NEWID": newid.astype("int64")}).merge(
        units.drop(columns=["ce_year", "ce_quarter"], errors="ignore"),
        on="NEWID",
        how="left",
        validate="one_to_one",
    )
    if merged["n_lt5"].isna().any():
        missing = merged.loc[merged["n_lt5"].isna(), "NEWID"].head().tolist()
        raise ValueError(f"CE units without member records, e.g. {missing}")
    year = _numeric(f, "ce_year").astype(int)
    quarter = _numeric(f, "ce_quarter").astype(int)
    region = _numeric(f, "REGION")
    state = _numeric(f, "STATE")
    tenure_code = _numeric(f, "CUTENURE")
    race = _numeric(f, "REF_RACE")
    education = _numeric(f, "EDUC_REF")
    building = _numeric(f, "BUILDING").map(_BUILDING_GROUPS)
    out = pd.DataFrame(
        {
            "key": merged["NEWID"].to_numpy(),
            "weight": _numeric(f, "FINLWT21").to_numpy(),
            "ce_year": year.to_numpy(),
            "ce_quarter": quarter.to_numpy(),
            "reference_year": np.where(quarter >= 2, year, year - 1),
            "interview_year": _numeric(f, "QINTRVYR").to_numpy(),
            "interview_month": _numeric(f, "QINTRVMO").to_numpy(),
            "region": region.where(region.isin([1, 2, 3, 4])).to_numpy(),
            "state": state.where((state >= 1) & (state <= 56)).to_numpy(),
            "tenure": tenure_code.map(_TENURE_BY_CUTENURE)
            .fillna("no_cash_rent")
            .to_numpy(),
            "cutenure": tenure_code.to_numpy(),
            "income": _numeric(f, "FINCBTXM").fillna(0).to_numpy(),
            # CE completes missing SNAP amounts in JFS_AMTM; FS_MTHI counts
            # months of receipt. Any of the three marks a recipient.
            "snap": (
                (_numeric(f, "JFS_AMT").fillna(0) > 0)
                | (_numeric(f, "JFS_AMTM").fillna(0) > 0)
                | (_numeric(f, "FS_MTHI").fillna(0) > 0)
            ).to_numpy(),
            "public_assistance": (
                _numeric(f, "WELFAREM").fillna(0) > 0
            ).to_numpy(),
            "ssi": (_numeric(f, "FSSIXM").fillna(0) > 0).to_numpy(),
            "housing_assistance": merged["housing_assistance"]
            .fillna(False)
            .astype(bool)
            .to_numpy(),
            "n_people": _numeric(f, "FAM_SIZE").to_numpy(),
            "n_earners": _numeric(f, "NO_EARNR").fillna(0).to_numpy(),
            "head_age": _numeric(f, "AGE_REF").to_numpy(),
            "head_female": (_numeric(f, "SEX_REF") == 2).to_numpy(),
            "head_race": np.select(
                [race == 1, race == 2], ["white", "black"], "other"
            ),
            "head_hispanic": (_numeric(f, "HISP_REF") == 1).to_numpy(),
            "head_education": np.select(
                [
                    education < 12,
                    education == 12,
                    education.between(13, 14),
                    education >= 15,
                ],
                ["lt_hs", "hs", "some_college", "ba_plus"],
                "lt_hs",
            ),
            "rent_paid_monthly": merged["rent_paid_monthly"]
            .fillna(0.0)
            .to_numpy(dtype=float),
            "bedrooms": _numeric(f, "BEDROOMQ").to_numpy(),
            "bathrooms": _numeric(f, "BATHRMQ").to_numpy(),
            "building": building.fillna("unknown").to_numpy(),
            "urban": (_numeric(f, "BLS_URBN") == 1).to_numpy(),
            "popsize": _numeric(f, "POPSIZE").fillna(0).to_numpy(),
        }
    )
    for column in (
        "n_lt5",
        "n_infants",
        "n_5_18",
        "n_lt18",
        "n_ge65",
        "n_women_15_45",
        "n_women_15_plus",
    ):
        out[column] = merged[column].astype(int).to_numpy()
    return out


# ---------------------------------------------------------------------------
# Shared covariates and models
# ---------------------------------------------------------------------------


def _with_poverty_ratio(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    threshold = opm_threshold(
        frame["reference_year"].to_numpy(),
        frame["n_people"].to_numpy(),
        frame["n_lt18"].to_numpy(),
        frame["head_age"].to_numpy(),
    )
    frame["poverty_ratio"] = frame["income"].to_numpy() / threshold
    return frame


def _dummy(values: np.ndarray, levels) -> list[np.ndarray]:
    return [(values == level).astype(float) for level in levels]


def design_matrix(
    frame: pd.DataFrame, geography: str, states: Sequence[int] = ()
) -> np.ndarray:
    """Covariates shared by CPS and CE units for participation and PMM.

    ``geography`` is ``"state"`` (one indicator per state after the
    first), ``"region"`` (Census regions 2-4) or ``"none"``.
    """
    ratio = frame["poverty_ratio"].to_numpy(dtype=float)
    bins = np.digitize(np.nan_to_num(ratio, nan=0.0), IPR_EDGES)
    size = np.clip(frame["n_people"].to_numpy(dtype=float), 1, 6)
    age = frame["head_age"].to_numpy(dtype=float)
    columns = [np.ones(len(frame))]
    columns += _dummy(bins, range(len(IPR_EDGES)))
    for flag in ("snap", "public_assistance", "ssi", "housing_assistance"):
        columns.append(frame[flag].to_numpy(dtype=float))
    columns += _dummy(
        frame["tenure"].to_numpy(),
        ("owner_with_mortgage", "owner_without_mortgage", "no_cash_rent"),
    )
    columns += _dummy(size, (2, 3, 4, 5, 6))
    columns += _dummy(np.clip(frame["n_lt5"].to_numpy(), 0, 2), (1, 2))
    columns += _dummy(np.clip(frame["n_5_18"].to_numpy(), 0, 3), (1, 2, 3))
    columns += _dummy(np.clip(frame["n_ge65"].to_numpy(), 0, 2), (1, 2))
    columns += _dummy(np.clip(frame["n_earners"].to_numpy(), 0, 2), (1, 2))
    columns += [
        (age < 25).astype(float),
        ((age >= 25) & (age < 35)).astype(float),
        ((age >= 45) & (age < 55)).astype(float),
        ((age >= 55) & (age < 65)).astype(float),
        (age >= 65).astype(float),
    ]
    columns.append(frame["head_female"].to_numpy(dtype=float))
    columns += _dummy(frame["head_race"].to_numpy(), ("black", "other"))
    columns.append(frame["head_hispanic"].to_numpy(dtype=float))
    columns += _dummy(
        frame["head_education"].to_numpy(),
        ("lt_hs", "some_college", "ba_plus"),
    )
    if geography == "state":
        columns += _dummy(frame["state"].to_numpy(), list(states)[1:])
    elif geography == "region":
        columns += _dummy(frame["region"].to_numpy(), (2, 3, 4))
    elif geography != "none":
        raise ValueError(f"Unknown geography {geography!r}")
    return np.column_stack(columns)


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 0.5 * (1.0 + np.tanh(0.5 * x))


def _penalty(width: int, ridge: float) -> np.ndarray:
    penalty = np.full(width, float(ridge))
    penalty[0] = 0.0
    return np.diag(penalty)


def fit_logit(
    x: np.ndarray, y: np.ndarray, weight: np.ndarray, ridge: float = 1.0
) -> tuple[np.ndarray, np.ndarray]:
    """Ridge-penalized weighted logit by Newton-Raphson.

    Weights are normalized to mean one, so the returned covariance (the
    inverse penalized information) reflects the sample size, not the
    population the weights represent.

    Returns:
        ``(beta, covariance)``.
    """
    w = weight / weight.mean()
    beta = np.zeros(x.shape[1])
    penalty = _penalty(x.shape[1], ridge)
    rate = np.clip(np.average(y, weights=w), 1e-6, 1 - 1e-6)
    beta[0] = np.log(rate / (1 - rate))
    for _ in range(100):
        p = _sigmoid(x @ beta)
        hessian = (x * (w * p * (1 - p))[:, None]).T @ x + penalty
        gradient = x.T @ (w * (y - p)) - penalty @ beta
        step = np.linalg.solve(hessian, gradient)
        # Damp very long Newton steps (near-separated cells).
        longest = np.max(np.abs(step))
        if longest > 5:
            step = step * (5 / longest)
        beta = beta + step
        if np.max(np.abs(step)) < 1e-9:
            break
    else:
        raise RuntimeError("Logit did not converge")
    p = _sigmoid(x @ beta)
    hessian = (x * (w * p * (1 - p))[:, None]).T @ x + penalty
    return beta, np.linalg.inv(hessian)


def fit_linear(
    x: np.ndarray, y: np.ndarray, weight: np.ndarray, ridge: float = 1.0
) -> tuple[np.ndarray, np.ndarray]:
    """Ridge-penalized weighted least squares with its covariance."""
    w = weight / weight.mean()
    gram = (x * w[:, None]).T @ x + _penalty(x.shape[1], ridge)
    beta = np.linalg.solve(gram, x.T @ (w * y))
    residual = y - x @ beta
    dof = max(len(y) - x.shape[1], 1)
    sigma2 = float(np.sum(w * residual**2) / dof)
    return beta, sigma2 * np.linalg.inv(gram)


def _draw(beta, covariance, rng, enabled: bool) -> np.ndarray:
    if not enabled:
        return beta
    # Symmetrize before factoring to absorb floating-point asymmetry.
    covariance = (covariance + covariance.T) / 2
    jitter = 1e-12 * np.eye(len(beta))
    return beta + np.linalg.cholesky(
        covariance + jitter
    ) @ rng.standard_normal(len(beta))


def _geography_masks(ce: pd.DataFrame) -> dict[str, np.ndarray]:
    has_state = ce["state"].notna().to_numpy()
    has_region = ce["region"].notna().to_numpy()
    return {
        "state": has_state,
        "region": ~has_state & has_region,
        "none": ~has_state & ~has_region,
    }


def _sorted_by_key(frame: pd.DataFrame) -> pd.DataFrame:
    # Fitting on key-sorted rows makes estimates independent of input order.
    return frame.sort_values("key", kind="stable").reset_index(drop=True)


def _participation(
    ce: pd.DataFrame,
    cps: pd.DataFrame,
    outcome: np.ndarray,
    component: str,
    reference_year: int,
    config: InKindConfig,
    diagnostics: dict,
) -> list[np.ndarray]:
    """Imputed participation for each implicate (boolean per CE row).

    When no CPS donor participates, or every one does, the logit has no
    finite estimate; every CE unit then takes the donors' constant
    outcome, and ``diagnostics["constant_outcome"]`` records it. Broadband
    reaches this case once Census's values fall to zero after the ACP
    ended.
    """
    outcome = np.asarray(outcome, dtype=bool)
    if len(outcome) == 0:
        raise ValueError(f"No CPS ASEC donors in the {component} universe")
    if outcome.all() or not outcome.any():
        constant = bool(outcome[0])
        diagnostics["model_columns"] = {}
        diagnostics["constant_outcome"] = constant
        return [
            np.full(len(ce), constant, dtype=bool)
            for _ in range(config.implicates)
        ]
    states = sorted(int(s) for s in cps["state"].dropna().unique())
    masks = _geography_masks(ce)
    draws = [np.zeros(len(ce), dtype=bool) for _ in range(config.implicates)]
    fits = {}
    for geography, mask in masks.items():
        if not mask.any():
            continue
        x_cps = design_matrix(cps, geography, states)
        beta, covariance = fit_logit(
            x_cps,
            outcome.astype(float),
            cps["weight"].to_numpy(),
            config.ridge,
        )
        fits[geography] = int(x_cps.shape[1])
        x_ce = design_matrix(ce.loc[mask], geography, states)
        keys = ce.loc[mask, "key"].to_numpy()
        for m in range(config.implicates):
            rng = _rng(config.seed, component, reference_year, geography, m)
            coefficients = _draw(beta, covariance, rng, config.parameter_draws)
            p = _sigmoid(x_ce @ coefficients)
            u = hashed_uniform(
                keys,
                seed=config.seed,
                stream=f"{component}:participation",
                implicate=m,
            )
            draws[m][mask] = u < p
    diagnostics["model_columns"] = fits
    return draws


def _pmm(
    ce: pd.DataFrame,
    take: np.ndarray,
    donors: pd.DataFrame,
    amounts: np.ndarray,
    component: str,
    reference_year: int,
    implicate: int,
    config: InKindConfig,
) -> np.ndarray:
    """Predictive mean matching of observed donor amounts (type-1 match)."""
    out = np.zeros(len(ce))
    if not take.any():
        return out
    states = sorted(int(s) for s in donors["state"].dropna().unique())
    masks = _geography_masks(ce)
    log_amount = np.log(amounts)
    for geography, mask in masks.items():
        target = mask & take
        if not target.any():
            continue
        x_donor = design_matrix(donors, geography, states)
        beta, covariance = fit_linear(
            x_donor, log_amount, donors["weight"].to_numpy(), config.ridge
        )
        rng = _rng(
            config.seed, component, "pmm", reference_year, geography, implicate
        )
        coefficients = _draw(beta, covariance, rng, config.parameter_draws)
        donor_hat = x_donor @ beta
        order = np.argsort(donor_hat, kind="stable")
        sorted_hat = donor_hat[order]
        ce_hat = (
            design_matrix(ce.loc[target], geography, states) @ coefficients
        )
        pools = _pmm_pools(sorted_hat, ce_hat, config.pmm_neighbors)
        k = pools.shape[1]
        u = hashed_uniform(
            ce.loc[target, "key"].to_numpy(),
            seed=config.seed,
            stream=f"{component}:pmm-donor",
            implicate=implicate,
        )
        pick = np.minimum((u * k).astype(int), k - 1)
        chosen = order[pools[np.arange(len(u)), pick]]
        out[target] = amounts[chosen]
    return out


def _pmm_pools(
    sorted_hat: np.ndarray, target_hat: np.ndarray, k: int
) -> np.ndarray:
    """Positions of ``k`` donors nearest each target, nearest first.

    ``sorted_hat`` holds the donors' predicted values in ascending order.
    In a sorted array, some set of ``k`` nearest donors always lies within
    the ``2k`` positions that start ``k`` before the target's insertion
    point, so the pool comes from that window. The window shifts inward at
    either end rather than repeating the end donor, so every pool holds
    ``min(k, len(sorted_hat))`` distinct donors with the smallest
    distances. Among donors at equal distance, the pool takes those that
    come first in the window. When donors tie at the ``k``-th distance,
    those need not be the lowest positions overall: with 20 donors
    predicted at 0 and a target at 1, the window spans positions 10-19
    and the pool is positions 10-14.

    Returns:
        An integer array of shape ``(len(target_hat), min(k, n))``.
    """
    n = len(sorted_hat)
    if n == 0:
        raise ValueError("Predictive mean matching needs at least one donor")
    k = min(k, n)
    width = min(2 * k, n)
    position = np.searchsorted(sorted_hat, target_hat)
    start = np.clip(position - k, 0, n - width)
    candidates = start[:, None] + np.arange(width)[None, :]
    distance = np.abs(sorted_hat[candidates] - target_hat[:, None])
    nearest = np.argsort(distance, axis=1, kind="stable")[:, :k]
    return np.take_along_axis(candidates, nearest, axis=1)


def _hotdeck(
    ce: pd.DataFrame,
    donors: pd.DataFrame,
    ladder: Sequence[Sequence[str]],
    u: np.ndarray,
    min_donors: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Weighted random donor within the finest cell with enough donors.

    Returns donor row positions and the ladder level used for each unit.
    """
    chosen = np.full(len(ce), -1)
    level = np.full(len(ce), -1)
    remaining = np.ones(len(ce), dtype=bool)
    donor_key = donors["key"].to_numpy()
    weights = donors["weight"].to_numpy(dtype=float)
    for depth, columns in enumerate(ladder):
        if not remaining.any():
            break
        if columns:
            raw_groups = donors.groupby(list(columns), sort=False).indices
            ce_cells = list(
                ce.loc[remaining, list(columns)].itertuples(
                    index=False, name=None
                )
            )
        else:
            raw_groups = {(): np.arange(len(donors))}
            ce_cells = [()] * int(remaining.sum())
        # Donor order within a cell is by key, so a draw never depends on
        # the order of the donor table.
        groups = {}
        for cell, members in raw_groups.items():
            if len(members) < min_donors:
                continue
            members = members[np.argsort(donor_key[members], kind="stable")]
            cell = cell if isinstance(cell, tuple) else (cell,)
            groups[cell] = (members, np.cumsum(weights[members]))
        targets = np.flatnonzero(remaining)
        for row, cell in zip(targets, ce_cells):
            found = groups.get(tuple(cell))
            if found is None:
                continue
            members, cumulative = found
            index = np.searchsorted(cumulative, u[row] * cumulative[-1])
            chosen[row] = members[min(index, len(members) - 1)]
            level[row] = depth
            remaining[row] = False
    if remaining.any():
        raise ValueError("Hot deck left units without a donor")
    return chosen, level


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------


def _nslp_donor_values(
    donors: pd.DataFrame, config: InKindConfig
) -> np.ndarray:
    """Household school lunch value for participating CPS donors."""
    values = donors["spm_schlunch"].to_numpy(dtype=float)
    formula = ~np.isfinite(values)
    if formula.any():
        u = hashed_uniform(
            donors.loc[formula, "key"].to_numpy(),
            seed=config.seed,
            stream="nslp:donor-free-reduced",
            implicate=0,
        )
        free_rule = donors.loc[formula, "below_free_cutoff"].to_numpy()
        free = free_rule | (u < 0.5)
        per_child = np.empty(int(formula.sum()))
        for year in np.unique(donors.loc[formula, "reference_year"]):
            rates = nslp_formula_values(int(year))
            in_year = donors.loc[formula, "reference_year"].to_numpy() == year
            per_child[in_year] = np.where(
                free[in_year], rates["free"], rates["reduced"]
            )
        values = values.copy()
        values[formula] = per_child * donors.loc[
            formula, "nslp_children"
        ].to_numpy(dtype=float)
    return np.clip(values, 0, None)


def _wic_participants(ce: pd.DataFrame) -> np.ndarray:
    """Census's WIC participant count for a participating unit.

    All children under 5, plus the mother of a child aged 0-1 when the
    unit has a woman aged 15 or older (a father-only unit has no maternal
    participant); otherwise one pregnant woman.
    """
    young = ce["n_lt5"].to_numpy()
    mother = (ce["n_infants"].to_numpy() > 0) & (
        ce["n_women_15_plus"].to_numpy() >= 1
    )
    return np.where(young > 0, young + mother.astype(int), 1).astype(float)


def _wic_ebt_complete(ce: pd.DataFrame, config: InKindConfig) -> np.ndarray:
    """Whether the unit's state had finished WIC EBT before its reference
    period midpoint (1.5 months before the interview month)."""
    dates = wic_ebt_statewide_dates()
    year = ce["interview_year"].fillna(ce["ce_year"]).to_numpy(dtype=float)
    month = (
        ce["interview_month"]
        .fillna(3 * ce["ce_quarter"] - 1)
        .to_numpy(dtype=float)
    )
    months = year * 12 + (month - 1) - 1.5
    midpoint = np.array(
        [
            date(int(m // 12), int(m % 12) + 1, 15 if m % 1 else 1).toordinal()
            for m in months
        ]
    )
    state = ce["state"].to_numpy()
    complete = np.zeros(len(ce), dtype=bool)
    known = np.isfinite(state)
    ordinal = np.array(
        [
            dates.get(int(s), date.max).toordinal() if np.isfinite(s) else 0
            for s in state
        ]
    )
    complete[known] = ordinal[known] <= midpoint[known]
    # Units with suppressed state: probability of EBT completion is the
    # weighted share of known-state units in the same region and quarter.
    unknown = ~known
    if unknown.any():
        cells = ce[["region", "ce_year", "ce_quarter"]].fillna(-1)
        weight = ce["weight"].to_numpy(dtype=float)
        frame = pd.DataFrame(
            {
                "cell": list(cells.itertuples(index=False, name=None)),
                "w": weight * known,
                "wc": weight * (known & complete),
            }
        )
        share = frame.groupby("cell")[["w", "wc"]].transform("sum")
        prob = np.where(
            share["w"] > 0,
            share["wc"] / share["w"].where(share["w"] > 0, 1),
            1.0,
        )
        u = hashed_uniform(
            ce.loc[unknown, "key"].to_numpy(),
            seed=config.seed,
            stream="wic:ebt-unknown-state",
            implicate=0,
        )
        complete[unknown] = u < prob[unknown]
    return complete


def _rent_design(frame: pd.DataFrame, states: Sequence[int]) -> np.ndarray:
    bedrooms = np.clip(frame["bedrooms"].fillna(2).to_numpy(), 0, 4)
    bathrooms = np.clip(frame["bathrooms"].fillna(1).to_numpy(), 0, 3)
    columns = [np.ones(len(frame))]
    columns += _dummy(bedrooms, (0, 1, 3, 4))
    columns += _dummy(bathrooms, (0, 2, 3))
    columns += _dummy(
        frame["building"].to_numpy(),
        (
            "row_townhouse",
            "duplex_plex",
            "garden",
            "high_rise",
            "apartment_flat",
            "unknown",
        ),
    )
    columns.append(frame["urban"].to_numpy(dtype=float))
    columns += _dummy(frame["popsize"].to_numpy(), (1, 2, 3, 4))
    state = frame["state"].to_numpy()
    columns += _dummy(state, list(states)[1:])
    unknown_state = ~np.isfinite(state)
    region = frame["region"].fillna(0).to_numpy()
    for value in (1, 2, 3, 4, 0):
        columns.append((unknown_state & (region == value)).astype(float))
    columns += _dummy(frame["ce_quarter"].to_numpy(), (1, 3, 4))
    return np.column_stack(columns)


def fit_tobit(
    x: np.ndarray,
    y: np.ndarray,
    censored: np.ndarray,
    weight: np.ndarray,
    ridge: float = 1.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Weighted Tobit with right censoring, by Newton's method.

    Uses Olsen's parameters ``gamma = beta / sigma`` and
    ``tau = 1 / sigma``, under which the log likelihood is concave.
    For censored rows ``y`` is the censoring point: the latent value is
    at least ``y``. The first column of ``x`` must be the intercept,
    which the ridge penalty leaves free.

    Newton starts from the intercept-only fit to the uncensored rows, so
    it stays well conditioned when covariates outnumber those rows, and
    stops once the Newton decrement (twice the predicted gain in the
    penalized log likelihood) falls below ``1e-10``. The inverse Mills
    ratio comes from ``erfcx``, which keeps it accurate deep in the lower
    tail, and each censored row's curvature is held to its exact range
    ``[0, 1]``, so the Hessian stays negative definite.

    Returns:
        ``(theta, covariance)`` where ``theta = (gamma, tau)``.
    """
    from scipy.special import erfcx, log_ndtr

    observed = ~np.asarray(censored, dtype=bool)
    if not observed.any():
        raise ValueError("A Tobit fit needs at least one uncensored row")
    if not np.allclose(x[:, 0], 1.0):
        raise ValueError("The first column of x must be the intercept")
    w = weight / weight.mean()
    width = x.shape[1]
    penalty = _penalty(width + 1, ridge)
    penalty[-1, -1] = 0.0
    centre = float(np.average(y[observed], weights=w[observed]))
    spread = float(
        np.sqrt(np.average((y[observed] - centre) ** 2, weights=w[observed]))
    )
    sigma0 = spread if spread > 1e-8 else 1.0
    theta = np.zeros(width + 1)
    theta[0] = centre / sigma0
    theta[-1] = 1.0 / sigma0

    def pieces(theta):
        gamma, tau = theta[:-1], theta[-1]
        index = x @ gamma
        r = tau * y - index
        s = -r
        log_cdf = log_ndtr(s)
        # phi(s) / Phi(s) without cancellation in either tail.
        lam = np.sqrt(2 / np.pi) / erfcx(-s / np.sqrt(2))
        loglik = (
            np.sum(w * np.where(observed, np.log(tau) - 0.5 * r**2, log_cdf))
            - 0.5 * theta @ penalty @ theta
        )
        g_gamma = x.T @ (w * np.where(observed, r, lam))
        g_tau = np.sum(w * np.where(observed, 1 / tau - r * y, -lam * y))
        gradient = np.append(g_gamma, g_tau) - penalty @ theta
        curvature = np.where(observed, 1.0, np.clip(lam * (s + lam), 0.0, 1.0))
        h_gg = -(x * (w * curvature)[:, None]).T @ x
        h_gt = x.T @ (w * curvature * y)
        h_tt = -np.sum(
            w * np.where(observed, 1 / tau**2 + y**2, curvature * y**2)
        )
        hessian = np.empty((width + 1, width + 1))
        hessian[:width, :width] = h_gg
        hessian[:width, width] = h_gt
        hessian[width, :width] = h_gt
        hessian[width, width] = h_tt
        return loglik, gradient, hessian - penalty

    loglik, gradient, hessian = pieces(theta)
    for _ in range(200):
        step = np.linalg.solve(hessian, -gradient)
        decrement = float(gradient @ step)
        if abs(decrement) < 1e-10:
            break
        scale = 1.0
        while scale > 1e-12:
            candidate = theta + scale * step
            if candidate[-1] > 0:
                new = pieces(candidate)
                if new[0] > loglik:
                    break
            scale /= 2
        else:
            # Rounding hides any remaining gain along the Newton direction.
            if decrement < 1e-6:
                break
            raise RuntimeError("Tobit line search failed")
        theta = candidate
        loglik, gradient, hessian = new
    else:
        raise RuntimeError("Tobit did not converge")
    return theta, np.linalg.inv(-hessian)


def _rental_assistance(
    ce: pd.DataFrame,
    reference_year: int,
    config: InKindConfig,
    diagnostics: dict,
) -> list[np.ndarray]:
    from scipy.special import ndtr, ndtri

    out = [np.zeros(len(ce)) for _ in range(config.implicates)]
    renters = (ce["cutenure"] == 4).to_numpy()
    assisted = renters & ce["housing_assistance"].to_numpy()
    rent = ce["rent_paid_monthly"].to_numpy(dtype=float)
    fit_rows = (
        renters & ~assisted & (rent >= MIN_UNASSISTED_MONTHLY_RENT)
    ) | (assisted & (rent > 0))
    order = np.argsort(ce["key"].to_numpy(), kind="stable")
    fit_rows_sorted = order[fit_rows[order]]
    diagnostics.update(
        {
            "renters": int(renters.sum()),
            "assisted": int(assisted.sum()),
            "assisted_zero_rent": int((assisted & (rent <= 0)).sum()),
            "fit_rows": int(fit_rows.sum()),
            "sigma": None,
        }
    )
    # Without assisted renters there is nothing to value, and an owner-only
    # sample has no rows to fit.
    if not assisted.any():
        return out
    states = sorted(int(s) for s in ce["state"].dropna().unique())
    x = _rent_design(ce, states)
    y = np.log(np.where(rent > 0, rent, 1.0))
    theta, covariance = fit_tobit(
        x[fit_rows_sorted],
        y[fit_rows_sorted],
        assisted[fit_rows_sorted],
        ce["weight"].to_numpy(dtype=float)[fit_rows_sorted],
        config.ridge,
    )
    diagnostics["sigma"] = float(1 / theta[-1])
    keys = ce.loc[assisted, "key"].to_numpy()
    for m in range(config.implicates):
        rng = _rng(config.seed, "rental_assistance", reference_year, m)
        drawn = _draw(theta, covariance, rng, config.parameter_draws)
        if drawn[-1] <= 0:
            drawn = theta
        sigma = 1 / drawn[-1]
        mu = x[assisted] @ (drawn[:-1] * sigma)
        paid = rent[assisted]
        v = hashed_uniform(
            keys,
            seed=config.seed,
            stream="rental_assistance:market-rent",
            implicate=m,
        )
        a = np.where(
            paid > 0,
            (np.log(np.where(paid > 0, paid, 1)) - mu) / sigma,
            -np.inf,
        )
        upper = ndtr(-a)
        z = -ndtri(np.clip(v * upper, 1e-300, 1.0))
        market = np.exp(mu + sigma * z)
        benefit = np.maximum(market - np.maximum(paid, 0.0), 0.0)
        out[m][assisted] = 12.0 * benefit
    return out


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


@dataclass
class InKindImputation:
    """Result of :func:`impute_in_kind`.

    Attributes:
        implicates: One frame per implicate with ``NEWID``,
            ``reference_year``, ``wic_broad_universe_only`` and one annual
            dollar column per component.
        diagnostics: Model, universe and amount diagnostics by component
            and reference year.
        methodology: Settings and parameter-file hashes.
    """

    implicates: list
    diagnostics: dict
    methodology: dict

    def for_target(self, implicate: int, target_year: int) -> pd.DataFrame:
        """Amounts for one implicate under ``target_year`` vintage rules.

        Before the 2025 thresholds the WIC universe requires a woman 15+
        in units whose only WIC-eligible members are children under 5.
        """
        frame = self.implicates[implicate].copy()
        if target_year < WIC_BROAD_UNIVERSE_FIRST_TARGET:
            frame.loc[frame["wic_broad_universe_only"], "wic"] = 0.0
        out = frame[["NEWID", *IN_KIND_COMPONENTS]].copy()
        out.attrs["methodology"] = {
            **self.methodology,
            "implicate": implicate,
            "vintage_rules_for_target": int(target_year),
        }
        return out


def _parameter_hashes() -> dict:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(DATA_DIR.glob("*"))
        if path.suffix in (".csv", ".json")
    }


def _weighted_mean(values, weights) -> Optional[float]:
    """Weighted mean, or None for a group with no weight (no members)."""
    weights = np.asarray(weights, dtype=float)
    total = weights.sum()
    if not total > 0:
        return None
    return float(np.sum(np.asarray(values, dtype=float) * weights) / total)


def impute_in_kind(
    ce_units: pd.DataFrame,
    cps_donors: pd.DataFrame,
    config: InKindConfig = InKindConfig(),
) -> InKindImputation:
    """Impute annual in-kind benefits to CE units.

    Args:
        ce_units: Output of :func:`ce_unit_covariates` for every CE
            interview to impute (all reference years at once).
        cps_donors: Output of
            :func:`spm_calculator.cps_asec.load_household_donors` covering
            survey years ``reference_year + 1`` for every CE reference year.
        config: Imputation settings.

    Returns:
        :class:`InKindImputation`. Amounts are nonnegative and finite by
        construction; units outside a component's universe receive zero.
    """
    ce = _with_poverty_ratio(ce_units.reset_index(drop=True))
    if ce["key"].duplicated().any():
        raise ValueError("CE unit keys must be unique")
    cps = cps_donors.copy()
    cps["key"] = cps["survey_year"].astype("int64") * 1_000_000 + cps[
        "household_id"
    ].astype("int64")
    cps = _with_poverty_ratio(cps)
    for frame in (ce, cps):
        frame["below_free_cutoff"] = (
            frame["poverty_ratio"].to_numpy() < FREE_LUNCH_POVERTY_RATIO
        )
        frame["n_5_18_cell"] = np.clip(frame["n_5_18"].to_numpy(), 0, 4)
        frame["snap_cell"] = frame["snap"].astype(bool)
    amounts = {
        component: [np.zeros(len(ce)) for _ in range(config.implicates)]
        for component in IN_KIND_COMPONENTS
    }
    broad_only = np.zeros(len(ce), dtype=bool)
    diagnostics: dict = {component: {} for component in IN_KIND_COMPONENTS}
    wic_ebt_complete = (
        _wic_ebt_complete(ce, config)
        if "wic" in config.components
        else np.zeros(len(ce), dtype=bool)
    )
    for reference_year in sorted(ce["reference_year"].unique()):
        reference_year = int(reference_year)
        in_year = (ce["reference_year"] == reference_year).to_numpy()
        ce_year = _sorted_by_key(ce.loc[in_year])
        positions = np.flatnonzero(in_year)[
            np.argsort(ce.loc[in_year, "key"].to_numpy(), kind="stable")
        ]
        donors = _sorted_by_key(
            cps.loc[cps["reference_year"] == reference_year]
        )
        if donors.empty:
            raise ValueError(
                f"No CPS ASEC donors for reference year {reference_year} "
                f"(survey year {reference_year + 1})"
            )
        weight = ce_year["weight"].to_numpy(dtype=float)

        def record(component, universe, take, values, extra=None):
            rates = [
                _weighted_mean(t[universe], weight[universe])
                if universe.any()
                else 0.0
                for t in take
            ]
            diagnostics[component][str(reference_year)] = {
                "ce_universe_units": int(universe.sum()),
                "ce_participation_rate_mean": float(np.mean(rates)),
                "ce_participation_rate_by_implicate": rates,
                "ce_mean_amount_per_unit": float(
                    np.mean([_weighted_mean(v, weight) for v in values])
                ),
                "ce_mean_amount_per_participant": float(
                    np.mean(
                        [
                            _weighted_mean(v[t], weight[t]) if t.any() else 0.0
                            for v, t in zip(values, take)
                        ]
                    )
                ),
                **(extra or {}),
            }

        if "nslp" in config.components:
            universe = ce_year["n_5_18"].to_numpy() >= 1
            cps_universe = donors["n_5_18"].to_numpy() >= 1
            info: dict = {}
            take = _participation(
                ce_year.loc[universe],
                donors.loc[cps_universe],
                donors.loc[cps_universe, "nslp"].to_numpy(),
                "nslp",
                reference_year,
                config,
                info,
            )
            participants = donors.loc[
                cps_universe & donors["nslp"].to_numpy()
            ].copy()
            participants = participants.loc[participants["n_5_18"] >= 1]
            donor_value = _nslp_donor_values(participants, config)
            participants = participants.assign(nslp_value=donor_value)
            ladder = [
                ("state", "snap_cell", "below_free_cutoff", "n_5_18_cell"),
                ("region", "snap_cell", "below_free_cutoff", "n_5_18_cell"),
                ("region", "snap_cell", "below_free_cutoff"),
                ("snap_cell", "below_free_cutoff"),
                (),
            ]
            values, takes, levels = [], [], []
            for m, chosen_take in enumerate(take):
                full_take = np.zeros(len(ce_year), dtype=bool)
                full_take[universe] = chosen_take
                value = np.zeros(len(ce_year))
                if full_take.any():
                    targets = ce_year.loc[full_take]
                    u = hashed_uniform(
                        targets["key"].to_numpy(),
                        seed=config.seed,
                        stream="nslp:hot-deck",
                        implicate=m,
                    )
                    chosen, level = _hotdeck(
                        targets,
                        participants,
                        ladder,
                        u,
                        config.min_cell_donors,
                    )
                    donor_kids = participants["n_5_18"].to_numpy(dtype=float)[
                        chosen
                    ]
                    value[full_take] = (
                        participants["nslp_value"].to_numpy()[chosen]
                        * targets["n_5_18"].to_numpy(dtype=float)
                        / donor_kids
                    )
                    levels.append(
                        np.bincount(level, minlength=len(ladder)).tolist()
                    )
                values.append(value)
                takes.append(full_take)
            record(
                "nslp",
                universe,
                takes,
                values,
                {
                    "cps_participation_rate": _weighted_mean(
                        donors.loc[cps_universe, "nslp"],
                        donors.loc[cps_universe, "weight"],
                    ),
                    "cps_mean_value_per_participant": _weighted_mean(
                        participants["nslp_value"], participants["weight"]
                    ),
                    "value_source": (
                        None
                        if participants.empty
                        else "census_spm_schlunch"
                        if participants["spm_schlunch"].notna().all()
                        else "usda_formula"
                    ),
                    "formula_per_child": nslp_formula_values(reference_year),
                    "hot_deck_level_counts": levels,
                    **info,
                },
            )
            for m in range(config.implicates):
                amounts["nslp"][m][positions] = values[m]

        if "wic" in config.components:
            woman = ce_year["n_women_15_45"].to_numpy() >= 1
            child_with_woman = (ce_year["n_lt5"].to_numpy() >= 1) & (
                ce_year["n_women_15_plus"].to_numpy() >= 1
            )
            narrow = woman | child_with_woman
            broad = narrow | (ce_year["n_lt5"].to_numpy() >= 1)
            cps_narrow = (donors["n_women_15_45"].to_numpy() >= 1) | (
                (donors["n_lt5"].to_numpy() >= 1)
                & (donors["n_women_15_plus"].to_numpy() >= 1)
            )
            info = {}
            take = _participation(
                ce_year.loc[broad],
                donors.loc[cps_narrow],
                donors.loc[cps_narrow, "wic"].to_numpy(),
                "wic",
                reference_year,
                config,
                info,
            )
            cost = wic_monthly_food_cost()
            if reference_year not in cost:
                raise ValueError(f"No WIC food cost for FY{reference_year}")
            per_unit = _wic_participants(ce_year) * 12.0 * cost[reference_year]
            ebt = wic_ebt_complete[positions]
            values, takes = [], []
            for chosen_take in take:
                full_take = np.zeros(len(ce_year), dtype=bool)
                full_take[broad] = chosen_take
                takes.append(full_take)
                values.append(np.where(full_take & ~ebt, per_unit, 0.0))
            record(
                "wic",
                broad,
                takes,
                values,
                {
                    "cps_participation_rate": _weighted_mean(
                        donors.loc[cps_narrow, "wic"],
                        donors.loc[cps_narrow, "weight"],
                    ),
                    "monthly_food_cost": cost[reference_year],
                    "ce_weighted_share_ebt_complete": _weighted_mean(
                        ebt[broad], weight[broad]
                    )
                    if broad.any()
                    else None,
                    **info,
                },
            )
            for m in range(config.implicates):
                amounts["wic"][m][positions] = values[m]
            broad_only[positions] = broad & ~narrow

        for component, outcome_column, amount_column, first_year in (
            ("liheap", "liheap", "liheap_amount", None),
            (
                "broadband",
                None,
                "spm_bbsubval",
                BROADBAND_FIRST_REFERENCE_YEAR,
            ),
        ):
            if component not in config.components:
                continue
            if first_year is not None and reference_year < first_year:
                diagnostics[component][str(reference_year)] = {
                    "status": "program_not_in_effect"
                }
                continue
            amount = donors[amount_column].fillna(0).to_numpy(dtype=float)
            outcome = (
                donors[outcome_column].to_numpy(dtype=bool) & (amount > 0)
                if outcome_column
                else amount > 0
            )
            info = {}
            universe = np.ones(len(ce_year), dtype=bool)
            take = _participation(
                ce_year,
                donors,
                outcome,
                component,
                reference_year,
                config,
                info,
            )
            pool = donors.loc[outcome]
            values = [
                _pmm(
                    ce_year,
                    chosen_take,
                    pool,
                    amount[outcome],
                    component,
                    reference_year,
                    m,
                    config,
                )
                for m, chosen_take in enumerate(take)
            ]
            record(
                component,
                universe,
                take,
                values,
                {
                    "cps_participation_rate": _weighted_mean(
                        outcome, donors["weight"]
                    ),
                    "cps_mean_amount_per_participant": _weighted_mean(
                        amount[outcome], donors.loc[outcome, "weight"]
                    ),
                    **info,
                },
            )
            for m in range(config.implicates):
                amounts[component][m][positions] = values[m]

        if "rental_assistance" in config.components:
            info = {}
            values = _rental_assistance(ce_year, reference_year, config, info)
            renters = (ce_year["cutenure"] == 4).to_numpy()
            assisted = renters & ce_year["housing_assistance"].to_numpy()
            record(
                "rental_assistance",
                renters,
                [assisted] * config.implicates,
                values,
                info,
            )
            for m in range(config.implicates):
                amounts["rental_assistance"][m][positions] = values[m]

    frames = []
    for m in range(config.implicates):
        frame = pd.DataFrame(
            {
                "NEWID": ce["key"].to_numpy(),
                "reference_year": ce["reference_year"].to_numpy(),
                "wic_broad_universe_only": broad_only,
            }
        )
        for component in IN_KIND_COMPONENTS:
            values = amounts[component][m]
            if not (np.isfinite(values).all() and (values >= 0).all()):
                raise RuntimeError(f"Invalid imputed {component} amounts")
            frame[component] = values
        frames.append(frame)
    methodology = {
        **config.methodology(),
        "parameter_files_sha256": _parameter_hashes(),
    }
    return InKindImputation(frames, diagnostics, methodology)
