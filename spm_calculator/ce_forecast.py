"""Conditional CE rolling-window research scenarios, using pinned inputs.

Reference year T uses (T-5)Q2..TQ1. Missing quarters repeat the latest
OBSERVED same-season interviews, retaining their weights and identities.
The existing research estimator is deliberately unchanged. This module
does not estimate survey-design uncertainty or behavioral responses.
"""

from __future__ import annotations

import hashlib
import json
import math
import zipfile
from datetime import date
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd

from .ce_threshold import (
    TENURES,
    bls_quarter_window,
    construct_normalized_expenditures,
    estimate_thresholds,
    load_ce_quarter,
    normalize_ce_sample,
    replicate_thresholds,
)
from .fcsuti_cpi import get_fcsuti_inflation_factor

INFORMATION_DATE = "2026-09-09"
TREND_SHRINKAGE = 0.5
SCENARIOS = ("zero_real", "ce_trend")
ALL_ITEMS_CPI = "CUUR0000SA0"
MONETARY_COLUMNS = tuple(
    f"{component}{period}"
    for component in (
        "FOOD",
        "FDHOME",
        "FDAWAY",
        "GROCER",
        "APPAR",
        "SHELT",
        "UTIL",
        "TELEPH",
    )
    for period in ("PQ", "CQ")
) + ("EMRTPNOP", "EMRTPNOC", "MRTPRNOP", "MRTPRNOC")
KEEP = set(MONETARY_COLUMNS) | {
    "NEWID",
    "AGE_REF",
    "PERSLT18",
    "FAM_SIZE",
    "CUTENURE",
    "FINLWT21",
    "ce_year",
    "ce_quarter",
}
POLICY = {
    "youth_policy": "exclude_unresolved",
    "tenure_policy": "exclude_5_6_code3_mortgage",
    "mortgage_principal": "include",
    "annualization": "quarter4",
    "median_share": 0.82,
}
Quarter = tuple[int, int]
Frames = Mapping[Quarter, pd.DataFrame]
Prices = Mapping[str, pd.Series]


def _label(quarter: Quarter) -> str:
    return f"{quarter[0]}Q{quarter[1]}"


def _quarter_range(start: Quarter, end: Quarter) -> list[Quarter]:
    return [
        (year, quarter)
        for year in range(start[0], end[0] + 1)
        for quarter in range(1, 5)
        if start <= (year, quarter) <= end
    ]


def _positive(value: float, name: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return result


def _tenure_values(values: Mapping[str, float], name: str) -> dict:
    if set(values) != set(TENURES):
        raise ValueError(f"{name} requires all three tenures")
    return {tenure: _positive(values[tenure], name) for tenure in TENURES}


def _shares(values: Mapping[str, float]) -> dict:
    result = _tenure_values(values, "Housing shares")
    if any(value >= 1 for value in result.values()):
        raise ValueError("Housing shares must be strictly within (0, 1)")
    return result


def _validate_prices(cpi_series: Prices, start: int, end: int) -> None:
    if not cpi_series:
        raise ValueError("Explicit CPI series are required")
    for sid, series in cpi_series.items():
        if not series.index.is_unique:
            raise ValueError(f"CPI {sid} has duplicate annual indexes")
        missing = set(range(start, end + 1)) - set(series.index)
        if missing:
            raise ValueError(
                f"CPI {sid} missing required years {sorted(missing)}"
            )
        values = series.loc[list(range(start, end + 1))].to_numpy(dtype=float)
        if not (np.isfinite(values) & (values > 0)).all():
            raise ValueError(f"CPI {sid} must be finite and positive")


def extend_cpi_series(
    cpi_series: Prices,
    *,
    observed_end_year: int = 2025,
    end_year: int = 2030,
    inflation_rates: Mapping[int, float],
) -> dict[str, pd.Series]:
    """Preserve annual observed prices; extend every series at passed rates.

    Preexisting future entries are rejected rather than silently treating
    them as observed or overwriting a potentially conflicting price path.
    """
    if end_year < observed_end_year:
        raise ValueError("end_year cannot precede the observed CPI boundary")
    result = {}
    for sid, series in cpi_series.items():
        if series.empty or any(
            year > observed_end_year for year in series.index
        ):
            raise ValueError("CPI inputs must end at the observed boundary")
        _validate_prices(
            {sid: series}, int(min(series.index)), observed_end_year
        )
        extended = series.astype(float).copy().sort_index()
        for year in range(observed_end_year + 1, end_year + 1):
            if year not in inflation_rates:
                raise ValueError(f"Missing inflation rate for {year}")
            factor = _positive(
                1 + float(inflation_rates[year]), "Price growth factor"
            )
            extended.loc[year] = _positive(
                extended.loc[year - 1] * factor, "CPI"
            )
        result[sid] = extended
    if not result:
        raise ValueError("Explicit CPI series are required")
    return result


def _observed_at_origin(frames: Frames, origin_year: int) -> dict:
    cutoff = (origin_year, 1)
    # Filter first: target-period data, even invalid data, cannot affect a fit.
    observed = {q: frame for q, frame in frames.items() if q <= cutoff}
    if not observed or cutoff not in observed:
        raise ValueError(f"Missing observed origin quarter {_label(cutoff)}")
    required = _quarter_range(min(observed), cutoff)
    missing = [q for q in required if q not in observed]
    if missing:
        raise ValueError(
            f"Missing historical quarters/gaps: {list(map(_label, missing))}"
        )
    for (year, quarter), frame in observed.items():
        if quarter not in (1, 2, 3, 4) or frame.empty:
            raise ValueError("Observed quarters must be valid and nonempty")
        for column, value in (("ce_year", year), ("ce_quarter", quarter)):
            if column not in frame or not frame[column].eq(value).all():
                raise ValueError(
                    f"Observed frame {year}Q{quarter} has invalid {column}"
                )
        if "ce_is_projected" in frame and frame["ce_is_projected"].any():
            raise ValueError(
                "Projected quarters cannot serve as observed donors"
            )
    return observed


def _require_quarters(frames: Frames, quarters: list[Quarter]) -> None:
    missing = [q for q in quarters if q not in frames]
    if missing:
        raise ValueError(
            f"Missing required historical quarters: {list(map(_label, missing))}"
        )


def _source(frame: pd.DataFrame) -> dict:
    source = dict(frame.attrs.get("source", {}))
    if "bundle_path" in source:
        source["bundle_path"] = Path(source["bundle_path"]).name
    return source


def _replicate(raw: pd.DataFrame, year: int, cpi_series: Prices) -> dict:
    result = replicate_thresholds(raw, year, cpi_series=cpi_series, **POLICY)
    _tenure_values(result["thresholds"], "Replicated thresholds")
    return result


def _origin_replication(frames: Frames, origin: int, prices: Prices) -> dict:
    quarters = bls_quarter_window(origin)
    _require_quarters(frames, quarters)
    _validate_prices(prices, quarters[0][0], origin)
    return _replicate(
        pd.concat([frames[q] for q in quarters], ignore_index=True),
        origin,
        prices,
    )


def _price_index(
    cpi_series: Prices,
    origin_weights: Mapping[str, float],
    origin_year: int,
    start_year: int,
    end_year: int,
) -> dict[int, float]:
    _validate_prices(cpi_series, start_year, end_year)
    index = {}
    for year in range(start_year, end_year + 1):
        if year < origin_year:
            value = 1 / get_fcsuti_inflation_factor(
                year,
                origin_year,
                weights=origin_weights,
                cpi_series=cpi_series,
            )
        else:
            value = get_fcsuti_inflation_factor(
                origin_year,
                year,
                weights=origin_weights,
                cpi_series=cpi_series,
            )
        index[year] = _positive(value, "Common donor price index")
    return index


def project_ce_window(
    observed_frames: Frames,
    *,
    origin_year: int,
    target_year: int,
    cpi_series: Prices,
    origin_weights: Mapping[str, float],
    real_log_rate: float = 0.0,
) -> tuple[pd.DataFrame, dict]:
    """Assemble exactly 20 quarters without filling historical gaps.

    Donors always come directly from observations through origin Q1.
    NEWID, row order, counts, weights, tenure and NaNs survive copying;
    provenance columns identify original interviews, not new samples.
    """
    if not math.isfinite(real_log_rate):
        raise ValueError("Real log rate must be finite")
    observed = _observed_at_origin(observed_frames, origin_year)
    window = bls_quarter_window(target_year)
    cutoff = (origin_year, 1)
    _require_quarters(observed, [q for q in window if q <= cutoff])
    prices = _price_index(
        cpi_series,
        origin_weights,
        origin_year,
        min(min(observed)[0], window[0][0]),
        max(origin_year, target_year),
    )
    donors = {
        season: max((q for q in observed if q[1] == season), default=None)
        for season in range(1, 5)
    }
    pieces, donor_metadata, original_quarters = [], [], set()
    projected_count = 0
    for target in window:
        projected = target > cutoff
        donor = donors[target[1]] if projected else target
        if donor is None:
            raise ValueError(
                f"Missing observed same-season donor for {_label(target)}"
            )
        original = observed[donor]
        frame = original.copy(deep=True)
        nominal = prices[target[0]] / prices[donor[0]] if projected else 1.0
        real = (
            math.exp(real_log_rate * (target[0] - donor[0]))
            if projected
            else 1.0
        )
        factor = _positive(nominal * real, "Donor scaling factor")
        if projected:
            for column in MONETARY_COLUMNS:
                if column in frame:
                    frame[column] = (
                        pd.to_numeric(frame[column], errors="raise") * factor
                    )
        frame["ce_year"], frame["ce_quarter"] = target
        frame["ce_donor_year"], frame["ce_donor_quarter"] = donor
        frame["ce_donor_row_position"] = np.arange(len(frame))
        frame["ce_is_projected"] = projected
        frame.attrs = {"source": _source(original)}
        pieces.append(frame)
        original_quarters.add(donor)
        projected_count += int(projected)
        donor_metadata.append(
            {
                "target": _label(target),
                "donor": _label(donor),
                "projected": projected,
                "rows": len(frame),
                "nominal_price_factor": float(nominal),
                "real_factor": real,
                "scale_factor": factor,
                "source": _source(original),
            }
        )
    assembled = pd.concat(pieces, ignore_index=True)
    assembled.attrs = {}
    return assembled, {
        "window": {
            "start": _label(window[0]),
            "end": _label(window[-1]),
            "observed_quarters": 20 - projected_count,
            "projected_quarters": projected_count,
        },
        "donor_quarters": donor_metadata,
        "records": {
            "rows": len(assembled),
            "unique_original_interview_rows": sum(
                len(observed[q]) for q in original_quarters
            ),
            "interpretation": "Original quarter plus row position; repeated interviews and synthetic copies are not independent samples",
        },
    }


def ce_housing_shares(replication: Mapping) -> dict[str, float]:
    """Estimator housing fraction is 0.82 * tenure SU mean / threshold."""
    thresholds = _tenure_values(
        replication["thresholds"], "Replicated thresholds"
    )
    estimation = replication["estimation"]
    if estimation["median_share"] != POLICY["median_share"]:
        raise ValueError("CE shares require the corrected 0.82 median share")
    return _shares(
        {
            tenure: 0.82
            * estimation["tenures"][tenure]["su_mean"]
            / thresholds[tenure]
            for tenure in TENURES
        }
    )


def _fit_statistics(annual_blocks: list[dict], shrinkage: float) -> dict:
    """OLS diagnostics; the SE is neither survey nor forecast uncertainty."""
    if not math.isfinite(shrinkage) or not 0 <= shrinkage <= 1:
        raise ValueError("Shrinkage must be finite and in [0, 1]")
    result = {
        "n": len(annual_blocks),
        "annual_blocks": annual_blocks,
        "shrinkage": float(shrinkage),
        "annual_log_slope": None,
        "log_slope": None,
        "OLS_slope_standard_error": None,
        "unshrunk_annual_rate": None,
        "shrunk_annual_rate": None,
        "annual_rate": None,
        "applied_log_rate": None,
        "standard_error_interpretation": "OLS fit diagnostic only; not survey-design uncertainty or forecast uncertainty; repeated interviews and serial dependence unmodeled",
    }
    x = np.array([block["end_year"] for block in annual_blocks], dtype=float)
    x = x - x.mean() if len(x) else x
    ssx = float(x @ x)
    if len(x) < 3 or ssx == 0:
        return {
            **result,
            "status": "unavailable",
            "reason": "Fewer than three annual blocks or singular time design",
        }
    y = np.log(
        [
            _positive(block["fcsuti_mean"], "Annual FCSUti mean")
            for block in annual_blocks
        ]
    )
    slope = float(x @ (y - y.mean()) / ssx)
    residual = y - y.mean() - slope * x
    se = math.sqrt(float(residual @ residual) / (len(x) - 2) / ssx)
    return {
        **result,
        "status": "available",
        "annual_log_slope": slope,
        "log_slope": slope,
        "OLS_slope_standard_error": se,
        "unshrunk_annual_rate": math.expm1(slope),
        "shrunk_annual_rate": math.expm1(shrinkage * slope),
        "annual_rate": math.expm1(shrinkage * slope),
        "applied_log_rate": shrinkage * slope,
    }


def _annual_statistics(
    observed: Frames,
    block_years: list[int],
    origin_year: int,
    cpi_series: Prices,
    origin_weights: Mapping[str, float],
) -> list[dict]:
    quarters = [
        q
        for year in block_years
        for q in ((year - 1, 2), (year - 1, 3), (year - 1, 4), (year, 1))
    ]
    _require_quarters(observed, quarters)
    _validate_prices(cpi_series, quarters[0][0], origin_year)
    raw = pd.concat([observed[q] for q in quarters], ignore_index=True)
    normalized, _ = normalize_ce_sample(
        raw,
        youth_policy=POLICY["youth_policy"],
        tenure_policy=POLICY["tenure_policy"],
    )
    constructed, _ = construct_normalized_expenditures(
        normalized,
        origin_year,
        cpi_series=cpi_series,
        mortgage_principal="include",
        annualization="quarter4",
    )
    # Reuse construction/equivalence stages but freeze EFFECTIVE inflation
    # weights at the origin's five-year window, also for the ten-block fit.
    factors = {
        int(year): get_fcsuti_inflation_factor(
            int(year),
            origin_year,
            weights=origin_weights,
            cpi_series=cpi_series,
        )
        for year in normalized["ce_year"].unique()
    }
    fixed = constructed["ce_year"].map(factors)
    correction = fixed / constructed["inflation_factor"]
    for column in ("fcsuti_threshold_year", "fcsuti_2a2c", "su_2a2c"):
        constructed[column] = constructed[column] * correction
    constructed["inflation_factor"] = fixed
    constructed["block_year"] = constructed["ce_year"] + (
        constructed["ce_quarter"] > 1
    )
    annual = []
    for year in block_years:
        block = constructed.loc[constructed["block_year"] == year]
        _, estimation = estimate_thresholds(block, median_share=0.82)
        annual.append(
            {
                "end_year": year,
                "start": f"{year - 1}Q2",
                "end": f"{year}Q1",
                "fcsuti_mean": _positive(
                    estimation["fcsuti_mean"], "Annual FCSUti mean"
                ),
                "included_interview_rows": len(block),
                "included_interview_weight_sum": float(
                    block["ce_weight"].sum()
                ),
                "estimation": estimation,
            }
        )
    return annual


def fit_real_growth(
    observed_frames: Frames,
    *,
    origin_year: int,
    cpi_series: Prices,
    origin_weights: Mapping[str, float],
    shrinkage: float = TREND_SHRINKAGE,
) -> dict:
    """Fit five nonoverlapping annual blocks, plus declared sensitivities.

    Custom shrinkage is available for diagnostics. Scored ce_trend always
    calls this with 0.5; its identity is independent of validation scores.
    """
    observed = _observed_at_origin(observed_frames, origin_year)
    five_years = list(range(origin_year - 4, origin_year + 1))
    annual = _annual_statistics(
        observed, five_years, origin_year, cpi_series, origin_weights
    )
    main = _fit_statistics(annual, shrinkage)
    if main["status"] != "available":
        raise ValueError("Mandatory five-block CE fit unavailable")
    ten_years = list(range(origin_year - 9, origin_year + 1))
    available = [
        year
        for year in ten_years
        if all(
            q in observed
            for q in ((year - 1, 2), (year - 1, 3), (year - 1, 4), (year, 1))
        )
    ]
    ten_annual = _annual_statistics(
        observed, available, origin_year, cpi_series, origin_weights
    )
    if len(available) == 10:
        ten_fit = _fit_statistics(ten_annual, shrinkage)
    else:
        ten_fit = _fit_statistics([], shrinkage)
        ten_fit.update(
            {
                "n": len(available),
                "annual_blocks": ten_annual,
                "reason": "Latest ten complete annual blocks unavailable; no shortened ten-block fit",
                "missing_block_end_years": sorted(
                    set(ten_years) - set(available)
                ),
            }
        )
    pandemic_fit = _fit_statistics(
        [block for block in annual if block["end_year"] not in (2021, 2022)],
        shrinkage,
    )
    return {
        **main,
        "origin_year": origin_year,
        "observed_cutoff": f"{origin_year}Q1",
        "common_price_year": origin_year,
        "origin_five_year_cpi_component_weights": dict(origin_weights),
        "sensitivities": {
            "latest_ten_blocks": ten_fit,
            "pandemic_excluded": pandemic_fit,
        },
        "limitations": [
            "Five-block fit at origin 2025 begins with the pandemic trough; pandemic exclusion leaves three blocks",
            "Common real growth across monetary components is a parsimonious scenario; donor composition repeats",
            "No full behavioral, tenure or distributional response is estimated",
        ],
    }


def _errors(predicted: Mapping, actual: Mapping) -> dict:
    predicted = _tenure_values(predicted, "Predicted thresholds")
    actual = _tenure_values(actual, "Published thresholds")
    return {
        tenure: {
            "prediction": predicted[tenure],
            "actual": actual[tenure],
            "signed_error": predicted[tenure] - actual[tenure],
            "absolute_error": abs(predicted[tenure] - actual[tenure]),
            "percentage_error": 100 * (predicted[tenure] / actual[tenure] - 1),
            "absolute_percentage_error": abs(
                100 * (predicted[tenure] / actual[tenure] - 1)
            ),
        }
        for tenure in TENURES
    }


def _metrics(errors: list[dict]) -> dict:
    if not errors:
        raise ValueError("Cannot evaluate missing CE fold observations")
    result = {"observation_count": len(errors)}
    for field in (
        "signed_error",
        "absolute_error",
        "percentage_error",
        "absolute_percentage_error",
    ):
        values = np.array([row[field] for row in errors], dtype=float)
        if not np.isfinite(values).all():
            raise ValueError("Nonfinite CE validation errors")
        result[f"mean_{field}"] = float(np.mean(values))
        result[f"median_{field}"] = float(np.median(values))
    return result


def evaluate_ce_backtest(
    observed_frames: Frames,
    *,
    cpi_series: Prices,
    published_thresholds: Mapping[int, Mapping[str, float]],
) -> dict:
    """All 21 folds, conditional on realized annual CPI/current vintages.

    No observations after OQ1 enter a candidate forecast or fit. Published
    target thresholds are used only to score it; its level anchor is O.
    """
    for year in range(2019, 2026):
        if year not in published_thresholds:
            raise ValueError(
                f"Missing corrected revised-method published thresholds for {year}"
            )
        _tenure_values(published_thresholds[year], "Published thresholds")
    _validate_prices(cpi_series, 2014, 2025)
    if ALL_ITEMS_CPI not in cpi_series:
        raise ValueError("Backtest requires BLS all-items CUUR0000SA0")
    folds, origins = [], {}
    for origin in range(2019, 2025):
        observed = _observed_at_origin(observed_frames, origin)
        base = _origin_replication(observed, origin, cpi_series)
        weights = base["construction"]["cpi_component_weights"]
        growth = fit_real_growth(
            observed,
            origin_year=origin,
            cpi_series=cpi_series,
            origin_weights=weights,
            shrinkage=TREND_SHRINKAGE,
        )
        origins[str(origin)] = {"replication": base, "real_growth": growth}
        for target in range(origin + 1, min(origin + 6, 2025) + 1):
            fold = {
                "origin_year": origin,
                "target_year": target,
                "horizon": target - origin,
                "observed_cutoff": f"{origin}Q1",
                "scenarios": {},
            }
            for scenario in SCENARIOS:
                log_rate = (
                    growth["applied_log_rate"]
                    if scenario == "ce_trend"
                    else 0.0
                )
                raw, metadata = project_ce_window(
                    observed,
                    origin_year=origin,
                    target_year=target,
                    cpi_series=cpi_series,
                    origin_weights=weights,
                    real_log_rate=log_rate,
                )
                replication = _replicate(raw, target, cpi_series)
                predicted = {
                    tenure: published_thresholds[origin][tenure]
                    * replication["thresholds"][tenure]
                    / base["thresholds"][tenure]
                    for tenure in TENURES
                }
                fold["scenarios"][scenario] = {
                    "thresholds": predicted,
                    "replication": replication,
                    **metadata,
                    "real_log_rate": log_rate,
                    "annual_rate": math.expm1(log_rate),
                    "shrinkage": (
                        TREND_SHRINKAGE if scenario == "ce_trend" else 0.0
                    ),
                    "errors_by_tenure": _errors(
                        predicted, published_thresholds[target]
                    ),
                }
            ratio = float(
                cpi_series[ALL_ITEMS_CPI].loc[target]
                / cpi_series[ALL_ITEMS_CPI].loc[origin]
            )
            predicted = {
                tenure: published_thresholds[origin][tenure] * ratio
                for tenure in TENURES
            }
            fold["baseline"] = {
                "thresholds": predicted,
                "cpi_ratio": ratio,
                "errors_by_tenure": _errors(
                    predicted, published_thresholds[target]
                ),
            }
            folds.append(fold)
    expected = {
        (origin, target)
        for origin in range(2019, 2025)
        for target in range(origin + 1, 2026)
    }
    if (
        len(folds) != 21
        or {(row["origin_year"], row["target_year"]) for row in folds}
        != expected
    ):
        raise ValueError("CE validation requires all 21 origin-target folds")

    def metrics_for(rows: list[dict], scenario: str) -> dict:
        entries = [
            (
                row["baseline"]
                if scenario == "baseline"
                else row["scenarios"][scenario]
            )
            for row in rows
        ]
        if any(
            set(entry["errors_by_tenure"]) != set(TENURES) for entry in entries
        ):
            raise ValueError(
                "CE validation requires all fold-tenure observations"
            )
        errors = [
            entry["errors_by_tenure"][tenure]
            for entry in entries
            for tenure in TENURES
        ]
        return {
            **_metrics(errors),
            "by_tenure": {
                tenure: _metrics(
                    [entry["errors_by_tenure"][tenure] for entry in entries]
                )
                for tenure in TENURES
            },
        }

    by_horizon = {}
    for horizon in range(1, 7):
        rows = [fold for fold in folds if fold["horizon"] == horizon]
        if len(rows) != 7 - horizon:
            raise ValueError("Incomplete CE horizon coverage")
        baseline = metrics_for(rows, "baseline")
        scenarios = {sid: metrics_for(rows, sid) for sid in SCENARIOS}
        for candidate in scenarios.values():
            candidate["beats_baseline"] = (
                candidate["mean_absolute_percentage_error"]
                < baseline["mean_absolute_percentage_error"]
            )
        by_horizon[str(horizon)] = {
            "fold_count": len(rows),
            "baseline": baseline,
            "scenarios": scenarios,
        }
    baseline = metrics_for(folds, "baseline")
    baseline["horizon_balanced_mean_absolute_percentage_error"] = float(
        np.mean(
            [
                by_horizon[str(h)]["baseline"][
                    "mean_absolute_percentage_error"
                ]
                for h in range(1, 6)
            ]
        )
    )
    summaries = {sid: metrics_for(folds, sid) for sid in SCENARIOS}
    for sid, candidate in summaries.items():
        if candidate["observation_count"] != 63:
            raise ValueError(
                "CE validation requires 63 observations per scenario"
            )
        balanced = float(
            np.mean(
                [
                    by_horizon[str(h)]["scenarios"][sid][
                        "mean_absolute_percentage_error"
                    ]
                    for h in range(1, 6)
                ]
            )
        )
        candidate.update(
            {
                "horizon_balanced_mean_absolute_percentage_error": balanced,
                "beats_baseline": candidate["mean_absolute_percentage_error"]
                < baseline["mean_absolute_percentage_error"],
                "beats_horizon_balanced_baseline": balanced
                < baseline["horizon_balanced_mean_absolute_percentage_error"],
            }
        )
    return {
        "status": "complete",
        "unvalidated": False,
        "fold_count": 21,
        "fold_tenure_observation_count": 63,
        "default_scenario": "ce_trend",
        "scenarios": summaries,
        "baseline": {**baseline, "series_id": ALL_ITEMS_CPI},
        "by_horizon": by_horizon,
        "folds": folds,
        "origins": origins,
        "assumptions": [
            "Retrospective conditional on realized annual CPI, including 2025 eleven-month average; not contemporaneous-vintage price forecasts",
            "Corrected revised-method origin-specific anchors, including 2019 Revised, never 2019 Published",
            "Overall MAPE weights 63 fold-tenure observations equally; more short-horizon folds emphasize shorter horizons",
            "Horizon-balanced MAPE averages horizons 1..5 equally; horizon 6 is supplemental",
            "ce_trend shrinkage 0.5 and default selected independently of scores; no claim of unbiased tuned-model selection",
            "Current source vintages with original CE release/revision dates unknown",
        ],
    }


def forecast_from_frames(
    observed_frames: Frames,
    *,
    cpi_series: Prices,
    published_thresholds: Mapping[int, Mapping[str, float]],
    housing_share_anchor: Mapping[str, float],
    national_anchor_year: int = 2025,
    share_anchor_year: int = 2024,
    end_year: int = 2030,
    include_backtest: bool = True,
    information_date: str = INFORMATION_DATE,
    start_year: int | None = None,
    published_housing_shares: Mapping[int, Mapping[str, float]] | None = None,
) -> dict:
    """Transport-free core; skipping backtests produces unvalidated research."""
    date.fromisoformat(information_date)
    if not share_anchor_year <= national_anchor_year <= end_year:
        raise ValueError("Require share anchor <= national anchor <= end year")
    observed = _observed_at_origin(observed_frames, national_anchor_year)
    _validate_prices(cpi_series, min(observed)[0], end_year)
    share_anchor = _shares(housing_share_anchor)
    first_year = share_anchor_year if start_year is None else start_year
    if first_year > share_anchor_year:
        raise ValueError("Start year must not follow the share anchor")
    supplied_shares = dict(published_housing_shares or {})
    for values in supplied_shares.values():
        _shares(values)
    for year in range(first_year, national_anchor_year + 1):
        if year not in published_thresholds:
            raise ValueError(f"Missing published anchor thresholds for {year}")
        _tenure_values(published_thresholds[year], "Published thresholds")
    base = _origin_replication(observed, national_anchor_year, cpi_series)
    weights = base["construction"]["cpi_component_weights"]
    share_replication = _origin_replication(
        observed, share_anchor_year, cpi_series
    )
    raw_share_anchor = ce_housing_shares(share_replication)
    growth = fit_real_growth(
        observed,
        origin_year=national_anchor_year,
        cpi_series=cpi_series,
        origin_weights=weights,
        shrinkage=TREND_SHRINKAGE,
    )
    scenarios = {}
    for sid in SCENARIOS:
        rate = growth["applied_log_rate"] if sid == "ce_trend" else 0.0
        years = {}
        for year in range(first_year, end_year + 1):
            raw, metadata = project_ce_window(
                observed,
                origin_year=national_anchor_year,
                target_year=year,
                cpi_series=cpi_series,
                origin_weights=weights,
                real_log_rate=rate,
            )
            replication = (
                base
                if year == national_anchor_year
                else (
                    share_replication
                    if year == share_anchor_year
                    else _replicate(raw, year, cpi_series)
                )
            )
            raw_shares = ce_housing_shares(replication)
            thresholds = (
                dict(published_thresholds[year])
                if year <= national_anchor_year
                else {
                    tenure: published_thresholds[national_anchor_year][tenure]
                    * replication["thresholds"][tenure]
                    / base["thresholds"][tenure]
                    for tenure in TENURES
                }
            )
            shares = (
                dict(supplied_shares[year])
                if year in supplied_shares
                else (
                    dict(share_anchor)
                    if year == share_anchor_year
                    else {
                        tenure: share_anchor[tenure]
                        * raw_shares[tenure]
                        / raw_share_anchor[tenure]
                        for tenure in TENURES
                    }
                )
            )
            _tenure_values(thresholds, "Forecast thresholds")
            _shares(shares)
            years[str(year)] = {
                "thresholds": thresholds,
                "housing_shares": shares,
                "raw_ce_housing_shares": raw_shares,
                "replication": replication,
                **metadata,
                "national_status": (
                    "published"
                    if year <= national_anchor_year
                    else "research_forecast"
                ),
                "housing_share_status": (
                    "published_anchor"
                    if year == share_anchor_year or year in supplied_shares
                    else "modeled"
                ),
            }
        scenarios[sid] = {
            "years": years,
            "annual_rate": math.expm1(rate),
            "real_log_rate": rate,
            "shrinkage": TREND_SHRINKAGE if sid == "ce_trend" else 0.0,
        }
    backtest = (
        evaluate_ce_backtest(
            observed,
            cpi_series=cpi_series,
            published_thresholds=published_thresholds,
        )
        if include_backtest
        else {
            "status": "not_run",
            "unvalidated": True,
            "reason": "Explicit core-only diagnostic; not a releasable validated artifact",
        }
    )
    return {
        "schema_version": 1,
        "information_date": information_date,
        "default_scenario": "ce_trend",
        "scenarios": scenarios,
        "real_growth": growth,
        "backtest": backtest,
        "sources": [],
        "code_sha256": {},
        "assumptions": {
            "classification": "conditional_rolling_window_research_scenarios",
            "national_anchor_year": national_anchor_year,
            "share_anchor_year": share_anchor_year,
            "latest_observed_quarter": f"{national_anchor_year}Q1",
            "reference_year_window": "(T-5)Q2 through TQ1; SPM reference year, not publication year",
            "policy": dict(POLICY),
            "donor_rule": "Latest observed same-season quarter, never a projected donor; preserve original NEWID, weights, counts, ordering and missingness",
            "monetary_columns": list(MONETARY_COLUMNS),
            "donor_price_index": "P[y<origin]=1/FCSUti_factor(y,origin); P[y>=origin]=FCSUti_factor(origin,y); nominal scale=P[target_collection_year]/P[donor_collection_year]",
            "price_weights": "Historical bridge and training weights frozen at origin five-year CE window; each target estimator recalculates its future-window CPI component weights",
            "remaining_deflator": "After donor collection-year to projected collection-year scaling, estimator applies only projected collection-year to threshold-year inflation",
            "real_growth": "Uniform component scaling exp(real_log_rate * donor-to-target year difference); composition repeats, no full behavioral response",
            "national_formula": "published_origin * replication_target / replication_origin; published historical thresholds returned directly",
            "housing_share_formula": "Published BLS_share_anchor * (0.82 * SU_mean_target / replication_target) / (0.82 * SU_mean_anchor / replication_anchor); supplied published historical shares returned directly",
            "uncertainty": "Not estimated; OLS slope SE is a fit diagnostic only",
            "research_approximations": [
                "Annual instead of quarterly CPI by collection year",
                "FMLI combined GROCER food allocation approximation; internet and in-kind imputations omitted",
                "Unresolved youth exclusion and tenure 3/5/6 policies; BLS percentile-code parity unverified",
                "Frozen origin bridge does not exactly match every target's recalculated CPI weights",
                "Synthetic donor repetitions are not independent observations",
            ],
        },
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_observed_ce(cache_dir: Path) -> tuple[dict, list]:
    """Read canonical 2014Q2..2025Q1 offline; hash each input bundle once."""
    frames, bundle_sources = {}, {}
    for bundle_year in range(2014, 2025):
        bundle = Path(cache_dir) / f"intrvw{bundle_year % 100:02d}.zip"
        if not bundle.is_file():
            raise FileNotFoundError(
                f"Required canonical CE bundle: {bundle.name}"
            )
    for quarter in _quarter_range((2014, 2), (2025, 1)):
        raw = load_ce_quarter(
            *quarter, cache_dir=cache_dir, allow_download=False
        )
        source = _source(raw)
        name = source["bundle_path"]
        year, season = quarter
        expected_bundle_year = year - (season == 1)
        expected_bundle = f"intrvw{expected_bundle_year % 100:02d}.zip"
        expected_member = f"fmli{year % 100:02d}{season}.csv"
        if (
            name != expected_bundle
            or Path(source["member"]).name.lower() != expected_member
        ):
            raise ValueError(
                "CE loader substituted a noncanonical quarter vintage"
            )
        bundle = Path(cache_dir) / name
        if name not in bundle_sources:
            bundle_sources[name] = {
                "kind": "ce_interview_bundle",
                "path": name,
                "url": source["url"],
                "sha256": _sha256(bundle),
                "original_publication_date": None,
                "original_revision_date": None,
                "members": [],
            }
        with zipfile.ZipFile(bundle) as archive:
            member_sha = hashlib.sha256(
                archive.read(source["member"])
            ).hexdigest()
        source.update(
            {
                "bundle_sha256": bundle_sources[name]["sha256"],
                "member_sha256": member_sha,
            }
        )
        bundle_sources[name]["members"].append(
            {
                "path": source["member"],
                "sha256": member_sha,
                "quarter": _label(quarter),
                "rows": len(raw),
            }
        )
        thinned = raw[[column for column in raw if column in KEEP]].copy()
        thinned.attrs = {"source": source}
        frames[quarter] = thinned
    return frames, list(bundle_sources.values())


def corrected_published_thresholds() -> dict[int, dict[str, float]]:
    """Read only revised corrected anchors, explicitly excluding 2019 Published."""
    path = Path(__file__).parent / "data/bls/threshold_series.json"
    document = json.loads(path.read_text())
    segments = document["series"]["bls-corrected-2026-07-17"]["segments"]
    result = {}
    for segment in ("revised_2019_2024", "bls-published-2025"):
        for year, tenures in segments[segment]["years"].items():
            result[int(year)] = {
                tenure: values["threshold"]
                for tenure, values in tenures.items()
            }
    if set(result) != set(range(2019, 2026)):
        raise ValueError(
            "Corrected revised-method published series incomplete"
        )
    return result


def build_ce_forecast(
    *,
    cache_dir: Path,
    cpi_path: Path,
    published_thresholds: dict[int, dict[str, float]],
    housing_share_anchor: dict[str, float],
    national_anchor_year: int = 2025,
    share_anchor_year: int = 2025,
    end_year: int = 2036,
    inflation_rates: dict[int, float],
    include_backtest: bool = True,
    information_date: str = INFORMATION_DATE,
) -> dict:
    """Build the current release component offline with complete validation.

    Source identities use package-relative or cache-relative names, never
    machine paths or the runtime clock. CPI receipt equality is checked by
    the root assembler, which owns the pinned official receipt.
    """
    if not include_backtest:
        raise ValueError(
            "Current release requires complete CE backtest validation"
        )
    if (national_anchor_year, share_anchor_year) != (2025, 2025):
        raise ValueError(
            "Current release requires published2025 national and share anchors"
        )
    canonical = corrected_published_thresholds()
    if any(
        published_thresholds.get(year) != values
        for year, values in canonical.items()
    ):
        raise ValueError(
            "Published thresholds must match corrected revised-method series, including 2019 Revised"
        )
    from spm_calculator.forecast_inputs import (
        load_horizon_inputs,
        projection_rates,
    )

    horizon = load_horizon_inputs()
    published_shares = {
        int(year): values
        for year, values in horizon["published_housing_shares"].items()
    }
    if housing_share_anchor != published_shares[2025]:
        raise ValueError(
            "Housing share anchor must match corrected published BLS2025 shares"
        )
    if inflation_rates != projection_rates(end_year=end_year):
        raise ValueError(
            "Future prices must match the pinned CBO annual-average growth path"
        )
    cpi_document = json.loads(Path(cpi_path).read_text())
    prices = {
        sid: pd.Series({int(year): value for year, value in annual.items()})
        for sid, annual in cpi_document["series"].items()
    }
    prices = extend_cpi_series(
        prices, end_year=end_year, inflation_rates=inflation_rates
    )
    frames, sources = load_observed_ce(Path(cache_dir))
    result = forecast_from_frames(
        frames,
        cpi_series=prices,
        published_thresholds=published_thresholds,
        housing_share_anchor=housing_share_anchor,
        national_anchor_year=national_anchor_year,
        share_anchor_year=share_anchor_year,
        end_year=end_year,
        information_date=information_date,
        start_year=2022,
        published_housing_shares=published_shares,
    )
    root = Path(__file__).resolve().parent.parent
    paths = [
        "spm_calculator/ce_forecast.py",
        "spm_calculator/ce_threshold.py",
        "spm_calculator/published_thresholds.py",
        "spm_calculator/fcsuti_cpi.py",
        "spm_calculator/equivalence_scale.py",
        "spm_calculator/forecast_inputs.py",
    ]
    script = root / "scripts/build_ce_forecast.py"
    if script.exists():
        paths.append("scripts/build_ce_forecast.py")
    result["code_sha256"] = {path: _sha256(root / path) for path in paths}
    sources.extend(
        [
            {
                "kind": "annual_cpi",
                "path": "spm_calculator/data/bls/cpi_annual.json",
                "sha256": _sha256(Path(cpi_path)),
                "retrieved": cpi_document.get("retrieved"),
                "sources": cpi_document.get("sources"),
                "note": cpi_document.get("note"),
            },
            {
                "id": "bls-spm-thresholds",
                "url": "https://www.bls.gov/pir/spm/spm_thresholds.xlsx",
                "kind": "published_thresholds",
                "path": "spm_calculator/data/bls/threshold_series.json",
                "sha256": _sha256(
                    root / "spm_calculator/data/bls/threshold_series.json"
                ),
                "series_id": "bls-corrected-2026-07-17",
                "segments": ["revised_2019_2024", "bls-published-2025"],
            },
        ]
    )
    result["sources"] = sources
    result["sources"].extend(horizon["sources"])
    result["sources"].append(
        {
            "kind": "horizon_inputs",
            "path": "spm_calculator/data/current/forecast_horizon_inputs.json",
            "sha256": _sha256(
                root
                / "spm_calculator/data/current/forecast_horizon_inputs.json"
            ),
        }
    )
    result["assumptions"]["price_source"] = horizon["prices"]
    result["assumptions"]["housing_share_source"] = {
        "id": "bls-spm-shares",
        "definition": horizon["housing_share_definition"],
        "anchor_year": 2025,
    }
    result["assumptions"]["inflation_rates"] = {
        str(year): float(inflation_rates[year])
        for year in range(2026, end_year + 1)
    }
    result["assumptions"]["annual_cpi"] = {
        sid: {str(year): float(value) for year, value in series.items()}
        for sid, series in prices.items()
    }
    # Fail here on any non-JSON numeric result; there is no partial artifact.
    return json.loads(json.dumps(result, allow_nan=False))
