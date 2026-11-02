"""Build the ACS rolling-window research artifact from pinned cached sources.

This command is offline, including --check. Raw ACS archives stay outside the
repository; committed compact sources contain geography and topcoding inputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from spm_calculator.acs_forecast import (
    add_index_topcode_diagnostics,
    build_window,
    evaluate_indices,
    project_acs_windows,
    summarize_window,
)
from spm_calculator.acs_forecast_sources import (
    BUNDLE_SHA256,
    CPI_RECEIPT_SHA256,
    load_historical_source_bundle,
    load_pums_records,
    load_source_bundle,
    sha256_file,
    verify_file,
)
from spm_calculator.fcsuti_cpi import get_packaged_cpi_series
from spm_calculator.forecast_inputs import (
    load_horizon_inputs,
    projection_rates,
)

ROOT = Path(__file__).resolve().parents[1]


def encode(result: dict) -> bytes:
    """Canonical readable JSON without clocks, machine paths or nonfinite floats."""
    return (
        json.dumps(
            result,
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode()


def add_horizon_disclosures(result: dict) -> None:
    """Describe source breaks and fixed-donor convergence without changing estimates."""
    geography = result["geography_by_year"]
    indices = result["indices_by_year"]
    for previous_area, current_area, kind, county_fips, interpretation in (
        (
            "25002",
            "25002",
            "published_series_break",
            None,
            "Massachusetts Nonmetro: the published Census rent index changes "
            "from 1.551 in 2022 to 1.043 in 2023; this is a published-source "
            "break, not estimated annual rent growth.",
        ),
        (
            "45001",
            "modeled_residual_metro:45",
            "published_to_modeled_break",
            "45085",
            "Sumter County, South Carolina: the 2022 published South Carolina "
            "Metro area disappears from the 2023 Census menu. Its assignment "
            "changes to an unanchored modeled residual metro area; the "
            "resulting change must not be interpreted as annual rent growth.",
        ),
    ):
        disclosure = {
            "kind": kind,
            "from_year": 2022,
            "to_year": 2023,
            "from_area_id": previous_area,
            "to_area_id": current_area,
            "from_rent_index": indices["2022"][previous_area],
            "to_rent_index": indices["2023"][current_area],
            "source_ids": ["census-spm-2022", "census-spm-2023"],
            "interpretation": interpretation,
        }
        if county_fips is not None:
            disclosure["affected_county_fips"] = [county_fips]
            disclosure["source_ids"] += [
                "acs_forecast_inputs",
                "forecast-horizon-inputs",
            ]
        for year, area in (("2022", previous_area), ("2023", current_area)):
            geography[year][area]["series_breaks"] = [disclosure.copy()]

    future_years = sorted(int(year) for year in indices if int(year) >= 2025)
    first_constant = next(
        (
            year
            for year in future_years[:-1]
            if all(
                indices[str(later)] == indices[str(year)]
                for later in future_years
                if later > year
            )
        ),
        None,
    )
    all_projected = next(
        year
        for year in future_years
        if not result["diagnostics_by_year"][str(year)]["window"][
            "observed_years"
        ]
    )
    result["metadata"]["relative_index_stabilization"] = {
        "latest_observed_donor_year": 2024,
        "first_constant_index_year": first_constant,
        "first_unchanged_transition_year": (
            first_constant + 1 if first_constant is not None else None
        ),
        "first_all_projected_window_year": all_projected,
        "through_year": max(future_years),
        "comparison": "Exact equality of every baseline area rent index",
        "interpretation": "Each future annual cohort projects the same 2024 "
        "donors. Under the baseline uniform nominal growth and deflation, "
        "relative indices stabilize once the window contains only that donor "
        "distribution, including its original observed cohort. These are not "
        "independent future survey observations.",
        "geographic_factor_formula": "1 + housing_share * (rent_index - 1)",
        "geographic_factor_caveat": "A constant relative rent index does not "
        "force a constant geographic factor: the selected year's CE housing "
        "share can still move. Floating-point noise is not substantive movement.",
    }


def _price_paths() -> tuple[dict, dict]:
    receipt = ROOT / "spm_calculator/data/current/acs_cpi_verification.json"
    verify_file(receipt, CPI_RECEIPT_SHA256)
    source = json.loads(receipt.read_text())
    if source.get("status") != "REQUEST_SUCCEEDED":
        raise ValueError("Pinned BLS receipt is not a successful API response")
    values = {
        int(r["year"]): float(r["value"])
        for series in source["Results"]["series"]
        if series["seriesID"] == "CUUR0000SA0"
        for r in series["data"]
        if r["period"] == "M13"
    }
    if values != {2024: 313.689, 2025: 321.943}:
        raise ValueError(
            "Pinned observed BLS annual values need reconciliation"
        )
    cpi = get_packaged_cpi_series("CUUR0000SA0", 2022, 2025)
    if any(float(cpi[y]) != value for y, value in values.items()):
        raise ValueError(
            "Packaged CPI differs from directly verified BLS receipt"
        )
    rates = {
        2025: values[2025] / values[2024] - 1,
        **projection_rates(),
    }
    details = {
        "series_id": "CUUR0000SA0",
        "observed_annual_values": {str(y): float(v) for y, v in cpi.items()},
        "observed_2025_note": "BLS M13 annual average uses eleven months; October is missing owing to the lapse in appropriations.",
        "forecast_source": load_horizon_inputs()["prices"],
        "receipt_sha256": CPI_RECEIPT_SHA256,
        "receipt_url": "https://api.bls.gov/publicAPI/v2/timeseries/data/CUUR0000SA0?startyear=2024&endyear=2025&annualaverage=true",
    }
    return rates, details


def evaluate_frozen_prediction(
    frozen_prediction: dict, official_workbook: Path
) -> dict:
    """Score the frozen 2025 vector before any recalibration to a new workbook."""
    import openpyxl

    prediction = frozen_prediction["frozen_prediction_indices"]
    expected = hashlib.sha256(encode(prediction)).hexdigest()
    if frozen_prediction["frozen_prediction_sha256"] != expected:
        raise ValueError("Frozen prediction SHA256 mismatch")
    wb = openpyxl.load_workbook(official_workbook, data_only=True)
    sheet = wb["Thresholds 2025"]
    target = {
        str(int(row[0])): float(row[2])
        for row in sheet.iter_rows(min_row=3, values_only=True)
        if isinstance(row[0], (int, float))
        and isinstance(row[2], (int, float))
    }
    result = evaluate_indices(
        prediction, target, frozen_prediction["carry_indices"]
    )
    result.update(
        {
            "kind": "frozen_forward_prediction_evaluation",
            "target_spm_year": 2025,
            "prediction_sha256": expected,
            "official_workbook_sha256": sha256_file(official_workbook),
        }
    )
    return result


def subset_allocations(allocations, area_codes):
    """Retain every PUMA's mass while selecting modeled areas for diagnostics."""
    selected = allocations.copy()
    selected["area_code"] = selected.area_code.where(
        selected.area_code.isin(area_codes)
    )
    return selected.groupby(
        ["puma_geoid", "area_code"], dropna=False, as_index=False, sort=False
    ).fraction.sum()


def build_result(
    cache_dir: Path,
    *,
    information_date: str = "2026-09-09",
    progress=None,
    forward_output: Path | None = None,
    forward_only: bool = False,
) -> dict:
    """Build baseline, sensitivity and complete 341-area historical evaluation."""
    if information_date != "2026-09-09":
        raise ValueError(
            "This source vintage is pinned to information date 2026-09-09"
        )
    log = progress or (lambda message: None)
    bundle = load_source_bundle()
    area_codes = sorted(bundle["official_indices"]["2024"])
    if len(area_codes) != 341 or set(area_codes) != set(
        bundle["official_indices"]["2023"]
    ):
        raise ValueError("Required official 341-area anchor coverage differs")
    horizon = load_horizon_inputs()
    holdout_allocations = pd.DataFrame(bundle["area_allocations"])
    allocations = pd.DataFrame(horizon["area_allocations_2020"])
    modeled_codes = sorted(
        code
        for code in horizon["areas"]
        if code.startswith("modeled_residual_metro:")
    )
    rates, price_details = _price_paths()
    observed = {}
    for vintage in (2023, 2024, 2022):
        log(f"Loading pinned ACS {vintage} five-year records")
        observed[vintage] = load_pums_records(cache_dir, vintage, bundle)
        log(
            f"ACS {vintage}: {len(observed[vintage]):,} eligible rental records"
        )
    log(
        "Estimating explicit unanchored residual groups and projecting ACS windows"
    )
    modeled_anchor = summarize_window(
        observed[2023],
        subset_allocations(allocations, modeled_codes),
        modeled_codes,
    )
    anchors = {
        **bundle["official_indices"]["2024"],
        **{
            code: row["model_rent_index"]
            for code, row in modeled_anchor["areas"].items()
        },
    }
    anchor_status = {
        code: "modeled_unanchored"
        if code in modeled_codes
        else "published_anchor"
        for code in anchors
    }
    kwargs = dict(
        end_spm_year=2036,
        anchor_status_by_area=anchor_status,
        price_growth_by_year=rates,
    )
    result = project_acs_windows(
        observed[2023],
        observed[2024],
        allocations,
        anchors,
        nominal_rent_growth_by_year=rates,
        **kwargs,
    )
    log("Computing declared nominal-rent +2 percentage point sensitivity")
    sensitivity = project_acs_windows(
        observed[2023],
        observed[2024],
        allocations,
        anchors,
        nominal_rent_growth_by_year={y: r + 0.02 for y, r in rates.items()},
        **kwargs,
    )
    if forward_output is not None:
        forward_output.write_bytes(
            encode(
                {
                    "status": "intermediate_forward_component_historical_years_pending",
                    "information_date": information_date,
                    "horizon_inputs_sha256": sha256_file(
                        ROOT
                        / "spm_calculator/data/current/forecast_horizon_inputs.json"
                    ),
                    "indices_by_year": result["indices_by_year"],
                    "diagnostics_by_year": result["diagnostics_by_year"],
                    "metadata": result["metadata"],
                    "code_sha256": {
                        name: sha256_file(ROOT / name)
                        for name in (
                            "scripts/build_acs_forecast.py",
                            "spm_calculator/acs_forecast.py",
                            "spm_calculator/acs_forecast_sources.py",
                        )
                    },
                }
            )
        )
        log(
            "Wrote intermediate future component for independent impact review"
        )
        if forward_only:
            return json.loads(forward_output.read_text())
    elif forward_only:
        raise ValueError(
            "Forward-only build requires an explicit forward output"
        )
    # Historical published components remain exact; only missing groups use PUMS.
    log(
        "Loading 2017–2021 ACS source for seven unpublished 2022 residual groups"
    )
    historical_path = (
        ROOT / "spm_calculator/data/current/acs_2021_source_inputs.json"
    )
    historical_bundle = load_historical_source_bundle()
    historical_records = load_pums_records(cache_dir, 2021, historical_bundle)
    historical_sources = historical_bundle["sources"]
    for year, records, mapping in (
        (
            2022,
            historical_records,
            pd.DataFrame(historical_bundle["area_allocations"]),
        ),
        (2023, observed[2022], allocations),
    ):
        official = horizon["historical"][str(year)]["rent_indices"]
        groups = [
            code
            for code in modeled_codes
            if not (year == 2022 and code.endswith(":45"))
        ]
        summary = summarize_window(
            records, subset_allocations(mapping, groups), groups
        )
        indices = {
            **official,
            **{
                code: row["model_rent_index"]
                for code, row in summary["areas"].items()
            },
        }
        summary["areas"].update(
            {
                code: {
                    "anchor_status": "published_anchor",
                    "rent_index_topcode_warning": False,
                    "rent_index_topcode_sources": {},
                    "rent_index_topcode_scope": "published_anchor_not_pums_estimate",
                }
                for code in official
            }
        )
        for component in (result, sensitivity):
            component["indices_by_year"][str(year)] = indices
            component["diagnostics_by_year"][str(year)] = summary
    del historical_records
    result["geography_by_year"] = {}
    for year, indices in result["indices_by_year"].items():
        result["geography_by_year"][year] = {
            code: {
                "status": "modeled_unanchored"
                if code in modeled_codes
                else ("published_anchor" if int(year) <= 2024 else "modeled"),
                "anchor_status": "modeled_unanchored"
                if code in modeled_codes
                else "published_anchor",
                "official_published_area": code not in modeled_codes,
                "source_ids": (
                    ["acs-2021-source-inputs"]
                    if year == "2022"
                    else ["acs_forecast_inputs", "forecast-horizon-inputs"]
                )
                if code in modeled_codes
                else [f"census-spm-{min(int(year), 2024)}"]
                + (
                    ["acs_forecast_inputs", "forecast-horizon-inputs"]
                    if int(year) > 2024
                    else []
                ),
                "puma_vintage": 2010 if year == "2022" else 2020,
                "allocation_county_vintage": 2010
                if year == "2022" and code in modeled_codes
                else 2020,
                "interpretation": "Public PUMA population allocation; unpublished area has no Census anchor"
                if code in modeled_codes
                else "Published Census index"
                if int(year) <= 2024
                else "Rolling ratio relative to published 2024 Census index",
            }
            for code in indices
        }
    sensitivity["index_difference_by_year"] = {
        year: {
            code: value - result["indices_by_year"][year][code]
            for code, value in indices.items()
        }
        for year, indices in sensitivity["indices_by_year"].items()
    }
    result["sensitivity"] = {"rent_plus_2pp": sensitivity}
    add_horizon_disclosures(result)
    log("Evaluating 2023-origin donor mechanism against official 2024 indices")
    origin_summary = summarize_window(
        observed[2022], holdout_allocations, area_codes
    )
    cpi = get_packaged_cpi_series("CUUR0000SA0", 2022, 2023)
    historical_rate = {2023: float(cpi[2023] / cpi[2022] - 1)}
    target_window = build_window(
        observed[2022],
        2024,
        donor_year=2022,
        nominal_rent_growth_by_year=historical_rate,
        price_growth_by_year=historical_rate,
    )
    target_summary = summarize_window(
        target_window, holdout_allocations, area_codes
    )
    add_index_topcode_diagnostics(target_summary, {"origin": origin_summary})
    carry = bundle["official_indices"]["2023"]
    prediction = {
        code: carry[code]
        * target_summary["areas"][code]["model_rent_index"]
        / origin_summary["areas"][code]["model_rent_index"]
        for code in area_codes
    }
    backtest = evaluate_indices(
        prediction, bundle["official_indices"]["2024"], carry
    )
    if (
        backtest["status"] != "complete"
        or backtest["common_area_count"] != 341
    ):
        raise ValueError("Required historical ACS evaluation is incomplete")
    for code, row in backtest["areas"].items():
        row["thin_support"] = bool(
            origin_summary["areas"][code]["thin_support"]
            or target_summary["areas"][code]["thin_support"]
        )
        row["median_topcode_warning"] = bool(
            origin_summary["areas"][code]["median_topcode_warning"]
            or target_summary["areas"][code]["median_topcode_warning"]
        )
        row["local_median_topcode_warning"] = row["median_topcode_warning"]
        row["rent_index_topcode_warning"] = target_summary["areas"][code][
            "rent_index_topcode_warning"
        ]
        row["rent_index_topcode_sources"] = target_summary["areas"][code][
            "rent_index_topcode_sources"
        ]
    backtest.update(
        {
            "kind": "retrospective_conditional_donor_mechanism_holdout",
            "origin_spm_year": 2023,
            "target_spm_year": 2024,
            "origin_acs_window": [2018, 2022],
            "target_acs_window": [2019, 2023],
            "donor_year": 2022,
            "nominal_rent_growth_by_year": {"2023": historical_rate[2023]},
            "price_growth_by_year": {"2023": historical_rate[2023]},
            "evaluation_scope": "All 341 common published areas; equal-area MAPE; prices realized and source vintages current. Does not validate the later overlap bridge or prospective accuracy.",
            "origin_diagnostics": origin_summary,
            "target_diagnostics": target_summary,
        }
    )
    result["backtest"] = backtest
    frozen = {
        code: result["indices_by_year"]["2025"][code] for code in area_codes
    }
    frozen_path = (
        ROOT / "spm_calculator/data/current/acs_frozen_forward_2025.json"
    )
    frozen_receipt = json.loads(frozen_path.read_text())
    if (
        frozen != frozen_receipt["frozen_prediction_indices"]
        or hashlib.sha256(encode(frozen)).hexdigest()
        != frozen_receipt["frozen_prediction_sha256"]
    ):
        raise ValueError("Original 341-area 2025 forward prediction changed")
    result["forward_test"] = frozen_receipt
    result["schema_version"] = 2
    result["method"] = "acs_pums_rolling_window_research"
    result["metadata"].update(
        {
            "information_date": information_date,
            "source_vintages": bundle["source_vintages"],
            "puma_vintage_by_spm_year": {
                str(y): 2010 if y == 2022 else 2020 for y in range(2022, 2037)
            },
            "historical_2022_geography": historical_bundle["geography_method"],
            "cbsa_vintage": "2013-02-28",
            "county_vintage": 2020,
            "survey_year_extraction_rule": "First four digits of validated Census housing SERIALNO",
            "dollar_adjustment_rule": "GRNTP * source ADJHSG / 1000000 exactly once; donor nominal growth divided by its separately declared price path",
            "price_inputs": price_details,
            "topcode_method": bundle["topcode_method"],
            "geography_method": bundle["geography_method"],
            "source_bundle_sha256": BUNDLE_SHA256,
            "source_product_counts": {
                str(v): {
                    str(int(y)): int(n)
                    for y, n in records.groupby("cohort_year").size().items()
                }
                for v, records in observed.items()
            },
            "approximations": [
                bundle["geography_method"],
                "Discrete weighted PUMS medians approximate confidential Census grouped-data median interpolation; topcoding may affect medians.",
                "Four-year overlap subpools retain their product's five-year calibrated weights; the bridge removes their measured vintage shift but does not make future weights known.",
                "Donor households, housing stock and weights remain fixed; baseline assumes zero real rent growth for new cohorts.",
                "Neither Kish support nor OLS/price sensitivities constitute a survey-design variance or forecast confidence interval.",
            ],
            "generator_identity": {
                name: sha256_file(ROOT / name)
                for name in [
                    "spm_calculator/acs_forecast.py",
                    "spm_calculator/acs_forecast_sources.py",
                    "scripts/build_acs_forecast.py",
                    "spm_calculator/forecast_inputs.py",
                    "scripts/build_acs_2021_inputs.py",
                ]
            },
        }
    )
    result["sources"] = (
        bundle["sources"]
        + historical_sources
        + horizon["sources"]
        + [
            {
                "id": "acs-2021-source-inputs",
                "package_path": "spm_calculator/data/current/acs_2021_source_inputs.json",
                "sha256": sha256_file(historical_path),
            },
            {
                "id": "forecast-horizon-inputs",
                "package_path": "spm_calculator/data/current/forecast_horizon_inputs.json",
                "sha256": sha256_file(
                    ROOT
                    / "spm_calculator/data/current/forecast_horizon_inputs.json"
                ),
            },
            {
                "id": "acs-frozen-forward-2025",
                "package_path": "spm_calculator/data/current/acs_frozen_forward_2025.json",
                "sha256": sha256_file(frozen_path),
            },
            {
                "id": "acs-normalized-products",
                "package_path": "spm_calculator/data/current/acs_normalized_products.json",
                "sha256": sha256_file(
                    ROOT
                    / "spm_calculator/data/current/acs_normalized_products.json"
                ),
            },
            {
                "id": "acs_forecast_inputs",
                "package_path": "spm_calculator/data/current/acs_forecast_inputs.json",
                "sha256": BUNDLE_SHA256,
            },
            {
                "id": "bls_cpi_verification",
                "package_path": "spm_calculator/data/current/acs_cpi_verification.json",
                "url": price_details["receipt_url"],
                "sha256": CPI_RECEIPT_SHA256,
                "acquired_at_utc": "2026-09-09",
            },
            {
                "id": "packaged_cpi_annual",
                "package_path": "spm_calculator/data/bls/cpi_annual.json",
                "sha256": sha256_file(
                    ROOT / "spm_calculator/data/bls/cpi_annual.json"
                ),
            },
        ]
    )
    # Validate the full result can be serialized before returning partial work.
    encode(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path.home() / ".cache/spm-calculator/acs-pums",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--information-date", default="2026-09-09")
    parser.add_argument("--forward-output", type=Path)
    parser.add_argument("--forward-only", action="store_true")
    args = parser.parse_args()
    result = build_result(
        args.cache_dir,
        information_date=args.information_date,
        progress=lambda message: print(message, flush=True),
        forward_output=args.forward_output,
        forward_only=args.forward_only,
    )
    encoded = encode(result)
    if args.check:
        if not args.output.exists() or args.output.read_bytes() != encoded:
            raise SystemExit(
                "ACS artifact differs from pinned offline rebuild"
            )
        print("ACS artifact matches pinned offline rebuild")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(encoded)
        print(
            f"Wrote {len(encoded):,} bytes; SHA256 {hashlib.sha256(encoded).hexdigest()}"
        )
    if args.forward_only:
        return
    print(
        json.dumps(
            {
                "candidate": result["backtest"]["candidate_metrics"],
                "carry": result["backtest"]["carry_metrics"],
            }
        )
    )


if __name__ == "__main__":
    main()
