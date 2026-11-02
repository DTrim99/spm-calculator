"""Assemble pinned CE/ACS results into the portable forecast artifact.

Scientific component builders use explicitly supplied raw-data caches. This
assembly and --check need only committed inputs and never access the network.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from spm_calculator.acs_forecast_sources import load_historical_source_bundle
from spm_calculator.forecast_inputs import load_horizon_inputs
from spm_calculator.release import TENURES, canonical_bytes, load_release
from spm_calculator.rolling_forecast import DEFAULT_FORECAST, seal_forecast

ROOT = Path(__file__).resolve().parents[1]
CURRENT = ROOT / "spm_calculator/data/current"
CE_PATH = CURRENT / "ce_rolling_forecast.json"
ACS_PATH = CURRENT / "acs_rolling_forecast.json"
OUT = CURRENT / DEFAULT_FORECAST
INFO_DATE = "2026-09-09"
SCENARIOS = ("ce_trend", "zero_real")
MAPE = "mean_absolute_percentage_error"
BALANCED_MAPE = "horizon_balanced_mean_absolute_percentage_error"


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def _repo_path(relative):
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Expected repository-relative input: {relative}")
    return ROOT / path


def verify_source_checks():
    checks = read_json(CURRENT / "forecast_source_checks.json")
    receipt_meta = checks["bls_cpi"]
    path = _repo_path(receipt_meta["path"])
    if sha256(path) != receipt_meta["sha256"]:
        raise ValueError("Pinned BLS CPI receipt hash mismatch")
    receipt = read_json(path)
    if receipt["status"] != "REQUEST_SUCCEEDED":
        raise ValueError("Pinned BLS receipt is not a successful response")
    series = receipt["Results"]["series"]
    if len(series) != 1 or series[0]["seriesID"] != "CUUR0000SA0":
        raise ValueError("Expected BLS all-items CPI-U receipt")
    annual = {
        row["year"]: float(row["value"])
        for row in series[0]["data"]
        if row["period"] == "M13"
    }
    packaged = read_json(ROOT / "spm_calculator/data/bls/cpi_annual.json")[
        "series"
    ]["CUUR0000SA0"]
    if annual != {"2024": 313.689, "2025": 321.943} or any(
        packaged[year] != value for year, value in annual.items()
    ):
        raise ValueError(
            "Packaged CPI differs from the pinned official annual values"
        )
    publication = checks["census_publication_check"]
    if (
        checks["information_date"] != INFO_DATE
        or publication["local_geography_status"] != "2025_not_yet_published"
    ):
        raise ValueError(
            "Reconcile the forecast anchor with source availability"
        )
    return checks


def _finite(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, (float, int))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(
            "Validation requires finite nonnegative error metrics"
        )
    return value


def _compare(candidate, baseline, metric=MAPE, flag="beats_baseline"):
    comparison = _finite(candidate[metric]) < _finite(baseline[metric])
    if type(candidate.get(flag)) is not bool or candidate[flag] != comparison:
        raise ValueError(
            "Validation comparison flag does not match its metrics"
        )


def validate_evaluations(ce, acs, areas):
    """Require complete declared evaluation coverage before publication."""
    cb, ab = ce["backtest"], acs["backtest"]
    for result in (cb, ab):
        if (
            result.get("status") != "complete"
            or result.get("unvalidated") is not False
        ):
            raise ValueError("Required retrospective evaluation is incomplete")
    expected = {
        (origin, target)
        for origin in range(2019, 2025)
        for target in range(origin + 1, 2026)
    }
    folds = cb["folds"]
    if (
        len(folds) != 21
        or {(f["origin_year"], f["target_year"]) for f in folds} != expected
        or cb["fold_tenure_observation_count"] != 63
    ):
        raise ValueError(
            "CE evaluation must contain all 21 folds and 63 tenure observations"
        )
    if (
        set(cb["scenarios"]) != set(SCENARIOS)
        or cb["default_scenario"] != "ce_trend"
    ):
        raise ValueError(
            "Scored CE scenario identifiers differ from displayed scenarios"
        )
    for fold in folds:
        if fold["scenarios"]["ce_trend"]["shrinkage"] != 0.5:
            raise ValueError("Scored ce_trend must use shrinkage 0.5")
        for result in [
            fold["baseline"],
            *[fold["scenarios"][sid] for sid in SCENARIOS],
        ]:
            errors = result["errors_by_tenure"]
            if set(errors) != set(TENURES):
                raise ValueError("Missing CE fold tenure")
            for error in errors.values():
                _finite(error["absolute_percentage_error"])
    for scenario in cb["scenarios"].values():
        if scenario["observation_count"] != 63:
            raise ValueError("Missing CE validation observations")
        _compare(scenario, cb["baseline"])
        _compare(
            scenario,
            cb["baseline"],
            BALANCED_MAPE,
            "beats_horizon_balanced_baseline",
        )
    if set(cb["by_horizon"]) != {str(h) for h in range(1, 7)}:
        raise ValueError("Missing CE validation horizon")
    for h, result in cb["by_horizon"].items():
        if result["fold_count"] != 7 - int(h):
            raise ValueError("Incorrect horizon fold count")
        for sid in SCENARIOS:
            _compare(result["scenarios"][sid], result["baseline"])
    if ab["common_area_count"] != 341 or set(ab["areas"]) != set(areas):
        raise ValueError("ACS evaluation must cover every published area")
    for metric in ("candidate_metrics", "carry_metrics"):
        if ab[metric]["area_count"] != 341:
            raise ValueError("ACS metric coverage is incomplete")
        _finite(ab[metric]["mape"])
    if type(ab.get("beats_baseline")) is not bool or ab["beats_baseline"] != (
        ab["candidate_metrics"]["mape"] < ab["carry_metrics"]["mape"]
    ):
        raise ValueError("ACS comparison flag does not match its metrics")
    return {
        "ce": {
            "metric": MAPE,
            **{k: v for k, v in cb.items() if k not in {"folds", "origins"}},
        },
        "acs": {
            "status": "complete",
            "unvalidated": False,
            MAPE: ab["candidate_metrics"]["mape"],
            "baseline_mean_absolute_percentage_error": ab["carry_metrics"][
                "mape"
            ],
            "beats_baseline": ab["beats_baseline"],
            "area_count": 341,
            "origin_spm_year": 2023,
            "target_spm_year": 2024,
        },
        "note": "Retrospective, current source vintages, conditional on realized prices. CE overall MAPE weights 63 fold-tenure observations; horizon-balanced MAPE gives horizons 1–5 equal weight. ACS uses equal-area errors and does not validate the later vintage bridge. Default scenario selected independently of scores.",
    }


def _verify_component_inputs(ce, acs):
    identities = {**ce["code_sha256"], **acs["metadata"]["generator_identity"]}
    horizon = load_horizon_inputs()
    historical = load_historical_source_bundle()
    identities["scripts/build_forecast_inputs.py"] = horizon[
        "generator_sha256"
    ]
    identities["scripts/build_acs_2021_inputs.py"] = historical[
        "generator_sha256"
    ]
    if (
        sha256(CURRENT / "acs_2021_source_manifest.json")
        != historical["manifest_sha256"]
    ):
        raise ValueError("Historical source manifest changed")
    if (
        historical["county_assignment_sha256"]
        != horizon["county_assignments"]["sha256"]
    ):
        raise ValueError("Historical county assignment changed")
    if not identities:
        raise ValueError("Scientific components must identify their code")
    for name, digest in identities.items():
        if sha256(_repo_path(name)) != digest:
            raise ValueError(
                f"Scientific code changed: rebuild component for {name}"
            )
    for source in ce["sources"] + acs["sources"]:
        name = source.get("package_path", source.get("path", ""))
        if (
            name.startswith("spm_calculator/")
            and sha256(_repo_path(name)) != source["sha256"]
        ):
            raise ValueError(f"Scientific input changed: {name}")
    return identities


def _sources(ce, acs, checks):
    result = []
    seen = {}
    for group, entries in (
        ("ce", ce["sources"]),
        ("acs", acs["sources"]),
        ("horizon", load_horizon_inputs()["sources"]),
        ("check", [checks["bls_cpi"], checks["census_publication_check"]]),
    ):
        for index, source in enumerate(entries):
            name = source.get("package_path", source.get("path"))
            url = source.get("url")
            if not url and source.get("cache_path") in {
                "geography/puma2020_county2020_overlap.csv",
                "geography/county2020_spm2024_research_mapping.csv",
            }:
                # The portable input is the hashed compact source bundle.
                # Original derived-CSV receipts remain in the ACS component.
                continue
            if not url and name:
                url = "https://github.com/PolicyEngine/spm-calculator"
            if not url:
                raise ValueError(
                    "Scientific source is missing its URL or package identity"
                )
            identity = source.get("id", f"{group}-{index:03d}")
            if identity in seen:
                if seen[identity] != source["sha256"]:
                    raise ValueError("Conflicting scientific source identity")
                continue
            seen[identity] = source["sha256"]
            result.append(
                {
                    **source,
                    "id": identity,
                    "url": url,
                    "available_on": INFO_DATE,
                    "availability_note": "Exact source bytes retained by the research build; this date does not reconstruct historical availability.",
                }
            )
    return result


def _growth_diagnostics(growth):
    fits = [
        ("5-year trend", growth),
        ("10-year trend", growth["sensitivities"]["latest_ten_blocks"]),
        ("Pandemic excluded", growth["sensitivities"]["pandemic_excluded"]),
    ]
    return {
        "fits": [
            {
                "label": label,
                "realGrowthRate": fit["annual_rate"],
                "n": fit["n"],
                "olsSlopeStandardError": fit.get("OLS_slope_standard_error"),
                "unshrunkRealGrowthRate": fit.get("unshrunk_annual_rate"),
                "shrinkage": fit["shrinkage"],
                "status": fit["status"],
            }
            for label, fit in fits
        ],
        "note": "Rates apply declared 0.5 shrinkage. OLS standard errors describe fits, not survey-design uncertainty or forecast intervals.",
    }


def assemble(ce, acs, *, checks, component_sha256):
    release = load_release()
    horizon = load_horizon_inputs()
    areas = horizon["areas"]
    official_areas = horizon["historical"]["2024"]["rent_indices"]
    if (
        ce["information_date"] != INFO_DATE
        or acs["metadata"]["information_date"] != INFO_DATE
    ):
        raise ValueError("Forecast information date mismatch")
    validation = validate_evaluations(ce, acs, official_areas)
    code = _verify_component_inputs(ce, acs)
    expected_years = {str(y) for y in range(2022, 2037)}
    if set(acs["indices_by_year"]) != expected_years or set(
        ce["scenarios"]
    ) != set(SCENARIOS):
        raise ValueError(
            "Forecast requires both scenarios and all 2022–2036 years"
        )
    scenarios = {}
    for identity in SCENARIOS:
        component = ce["scenarios"][identity]
        if set(component["years"]) != expected_years:
            raise ValueError("Incomplete CE forecast year coverage")
        years = {}
        for year in sorted(expected_years):
            entry = component["years"][year]
            diagnostics = acs["diagnostics_by_year"][year]
            window = diagnostics["window"]
            observed = window["observed_years"]
            projected = window["projected_years"]
            expected_cohorts = list(range(int(year) - 5, int(year)))
            if (
                not isinstance(observed, list)
                or not isinstance(projected, list)
                or sorted(observed + projected) != expected_cohorts
            ):
                raise ValueError(
                    "ACS observed/projected cohorts do not cover the target window"
                )
            if set(diagnostics["areas"]) != set(acs["indices_by_year"][year]):
                raise ValueError("Missing area support diagnostics")
            if (
                int(year) <= 2025
                and entry["thresholds"]
                != release.entry(int(year))["thresholds"]
            ):
                raise ValueError("Published national anchor changed")
            if (
                int(year) <= 2025
                and entry["housing_shares"]
                != horizon["published_housing_shares"][year]
            ):
                raise ValueError("Published BLS housing-share anchor changed")
            if int(year) <= 2024:
                official = horizon["historical"][year]["rent_indices"]
                if {
                    code: acs["indices_by_year"][year][code]
                    for code in official
                } != official:
                    raise ValueError("Published geographic anchor changed")
            years[year] = {
                "thresholds": entry["thresholds"],
                "housing_shares": entry["housing_shares"],
                "national_source_ids": ["bls-spm-thresholds"],
                "housing_share_source_ids": ["bls-spm-shares"],
                "rent_indices": acs["indices_by_year"][year],
                "national_status": (
                    "published" if int(year) <= 2025 else "forecast"
                ),
                "geography_status": "mixed",
                "geography_by_area": acs["geography_by_year"][year],
                "housing_share_status": entry["housing_share_status"],
                "ce_window": entry["window"],
                "acs_window": {
                    "start": window["start"],
                    "end": window["end"],
                    "observed_years": len(observed),
                    "projected_years": len(projected),
                    "observed_cohort_years": observed,
                    "projected_cohort_years": projected,
                },
                "median_diagnostics": diagnostics["areas"],
            }
        scenarios[identity] = {
            "label": (
                "CE real-spending trend"
                if identity == "ce_trend"
                else "No real spending growth"
            ),
            "real_growth_rate": component["annual_rate"],
            "shrinkage": component["shrinkage"],
            "years": years,
        }
    sensitivity = acs["sensitivity"]["rent_plus_2pp"]
    rent_sensitivity = {
        "label": "Nominal rent growth +2 percentage points; deflator unchanged",
        "nominal_rent_growth_by_year": sensitivity["metadata"][
            "nominal_rent_growth_by_year"
        ],
        "price_growth_by_year": sensitivity["metadata"][
            "price_growth_by_year"
        ],
        "index_difference_by_year": sensitivity["index_difference_by_year"],
        "location_factor_difference_formula": "selected_year_housing_share * index_difference",
    }
    assumptions = {
        "price_projections": ce["assumptions"]["inflation_rates"],
        "ce": ce["assumptions"],
        "acs": acs["metadata"],
        "real_growth_fit": ce["real_growth"],
        "default_scenario": "ce_trend",
        "default_selection": "Declared 0.5-shrunk trend, independent of holdout scores",
    }
    code.update(
        {
            name: sha256(ROOT / name)
            for name in (
                "scripts/build_rolling_forecast.py",
                "spm_calculator/rolling_forecast.py",
                "spm_calculator/release.py",
            )
        }
    )
    return seal_forecast(
        {
            "schema_version": 2,
            "method": "rolling_ce_acs_v1",
            "units": "USD/year",
            "reference_family": {"adults": 2, "children": 2},
            "forecast_id": "spm-rolling-2026-09-09",
            "created_on": INFO_DATE,
            "information_date": INFO_DATE,
            "base_release_sha256": release.content_sha256,
            "national_anchor_year": 2025,
            "default_scenario": "ce_trend",
            "areas": areas,
            "county_assignments": horizon["county_assignments"],
            "scenarios": scenarios,
            "assumptions": assumptions,
            "assumption_sha256": hashlib.sha256(
                canonical_bytes(assumptions)
            ).hexdigest(),
            "sources": _sources(ce, acs, checks),
            "code_sha256": code,
            "component_sha256": component_sha256,
            "validation": validation,
            "real_growth_diagnostics": _growth_diagnostics(ce["real_growth"]),
            "rent_sensitivity": rent_sensitivity,
            "forward_test": acs["forward_test"],
            "uncertainty": "Not estimated; research sensitivities are not confidence intervals",
        }
    )


def build_document():
    checks = verify_source_checks()
    components = {
        str(path.relative_to(ROOT)): sha256(path)
        for path in (
            CE_PATH,
            ACS_PATH,
            CURRENT / "forecast_source_checks.json",
            CURRENT / "forecast_horizon_inputs.json",
            CURRENT / "acs_2021_source_inputs.json",
            CURRENT / "acs_2021_source_manifest.json",
            CURRENT / "acs_normalized_products.json",
        )
    }
    return assemble(
        read_json(CE_PATH),
        read_json(ACS_PATH),
        checks=checks,
        component_sha256=components,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    encoded = (
        json.dumps(
            build_document(), indent=2, ensure_ascii=False, allow_nan=False
        )
        + "\n"
    ).encode()
    if args.check:
        if not OUT.exists() or OUT.read_bytes() != encoded:
            raise SystemExit(
                "Forecast differs from its pinned inputs; rebuild required"
            )
        print("Forecast matches pinned components, source checks and code")
    else:
        OUT.write_bytes(encoded)
        print(OUT)


if __name__ == "__main__":
    main()
