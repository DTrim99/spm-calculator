"""Replicate 2019-2025 CE thresholds with and without the in-kind stage.

Loads the cached CE Interview bundles (2014Q2-2025Q1) and the cached CPS
ASEC public-use files (survey years 2015-2025), imputes BLS-style in-kind
benefits (``spm_calculator.ce_inkind``), and reports for each target year
and tenure:

- the out-of-pocket replication, which must equal the archived receipt
  ``spm_calculator/data/current/ce_replication_2019_2025.json`` exactly,
  and the same run with an all-zero in-kind table, which must too;
- the replication with imputed in-kind benefits (mean and spread over
  implicates) and the share of the gap to BLS's corrected thresholds that
  the stage closes;
- the stand-alone lift from each component;
- exact decompositions of 2022->2025 and year-over-year growth into
  FCSUti price, real out-of-pocket spending and in-kind, using the
  package composite CPI and BLS's FCSUti index;
- a price-index sensitivity: the same replication, with and without the
  stage, deflated by BLS's published FCSUti index instead of the package
  composite CPI.

No network requests are made.

Run: uv run --no-sync python scripts/replicate_ce_inkind.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import scipy

from spm_calculator.ce_decomposition import (
    BLS_FCSUTI_INDEX,
    BLS_FCSUTI_SOURCE,
    TENURES,
    bls_fcsuti_index,
    decompose_growth,
    growth_percent,
)
from spm_calculator.ce_inkind import (
    InKindConfig,
    ce_unit_covariates,
    impute_in_kind,
    load_ce_unit_inputs,
)
from spm_calculator.ce_inkind_stage import (
    IN_KIND_COMPONENTS,
    prepare_out_of_pocket,
    replicate_with_in_kind,
    zero_in_kind,
)
from spm_calculator.ce_threshold import (
    bls_quarter_window,
    load_ce_quarter,
    replicate_thresholds,
)
from spm_calculator.cps_asec import load_household_donors
from spm_calculator.fcsuti_cpi import CPI_SERIES
from spm_calculator.published_thresholds import get_published_thresholds

ROOT = Path(__file__).resolve().parent.parent
ARCHIVED_RECEIPT = (
    ROOT / "spm_calculator/data/current/ce_replication_2019_2025.json"
)
RECEIPT = (
    ROOT / "spm_calculator/data/current/ce_inkind_replication_2019_2025.json"
)
DEFAULT_DETAIL = ROOT / "benchmark_output/ce_inkind_2019_2025_detail.json"
TARGETS = range(2019, 2026)
#: Columns of the out-of-pocket replication (scripts/replicate_current_ce.py).
KEEP = {
    "NEWID",
    "AGE_REF",
    "PERSLT18",
    "FAM_SIZE",
    "CUTENURE",
    "FINLWT21",
    "ce_year",
    "ce_quarter",
    "FOODPQ",
    "FOODCQ",
    "FDHOMEPQ",
    "FDHOMECQ",
    "FDAWAYPQ",
    "FDAWAYCQ",
    "GROCERPQ",
    "GROCERCQ",
    "APPARPQ",
    "APPARCQ",
    "SHELTPQ",
    "SHELTCQ",
    "UTILPQ",
    "UTILCQ",
    "TELEPHPQ",
    "TELEPHCQ",
    "EMRTPNOP",
    "EMRTPNOC",
    "MRTPRNOP",
    "MRTPRNOC",
}
#: Additional FMLI columns the in-kind stage reads.
IN_KIND_COLUMNS = {
    "REGION",
    "STATE",
    "FINCBTXM",
    "JFS_AMT",
    "JFS_AMTM",
    "FS_MTHI",
    "WELFAREM",
    "FSSIXM",
    "NO_EARNR",
    "SEX_REF",
    "REF_RACE",
    "HISP_REF",
    "EDUC_REF",
    "QINTRVYR",
    "QINTRVMO",
    "BEDROOMQ",
    "BATHRMQ",
    "BUILDING",
    "BLS_URBN",
    "POPSIZE",
}
CODE_PATHS = (
    "spm_calculator/ce_threshold.py",
    "spm_calculator/ce_inkind.py",
    "spm_calculator/ce_inkind_stage.py",
    "spm_calculator/ce_decomposition.py",
    "spm_calculator/cps_asec.py",
    "spm_calculator/fcsuti_cpi.py",
    "spm_calculator/equivalence_scale.py",
    "scripts/replicate_ce_inkind.py",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def mean_thresholds(results: list[dict]) -> dict[str, float]:
    return {
        tenure: float(np.mean([r["thresholds"][tenure] for r in results]))
        for tenure in TENURES
    }


def spread(results: list[dict]) -> dict[str, float]:
    return {
        tenure: float(
            np.std([r["thresholds"][tenure] for r in results], ddof=1)
        )
        if len(results) > 1
        else 0.0
        for tenure in TENURES
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ce-cache",
        type=Path,
        default=Path.home() / ".cache/spm-calculator/ce-pumd",
    )
    parser.add_argument(
        "--cps-cache",
        type=Path,
        default=Path.home() / ".cache/spm-calculator/cps-asec",
    )
    parser.add_argument(
        "--cpi-input",
        type=Path,
        default=ROOT / "spm_calculator/data/bls/cpi_annual.json",
    )
    parser.add_argument("--implicates", type=int, default=5)
    parser.add_argument("--receipt", type=Path, default=RECEIPT)
    parser.add_argument("--detail", type=Path, default=DEFAULT_DETAIL)
    args = parser.parse_args()
    if args.receipt.resolve() == ARCHIVED_RECEIPT.resolve():
        parser.error("--receipt cannot overwrite the archived receipt")
    started = time.time()
    as_of = datetime.now(timezone.utc).date().isoformat()
    cpi_document = json.loads(args.cpi_input.read_text())
    cpi_series = {
        sid: pd.Series({int(y): v for y, v in values.items()})
        for sid, values in cpi_document["series"].items()
    }
    archived = json.loads(ARCHIVED_RECEIPT.read_text())

    all_quarters = sorted({q for t in TARGETS for q in bls_quarter_window(t)})
    raw, bundles = {}, {}
    for quarter in all_quarters:
        frame = load_ce_quarter(
            *quarter, cache_dir=args.ce_cache, allow_download=False
        )
        source = frame.attrs["source"]
        bundle = Path(source["bundle_path"])
        bundles.setdefault(
            bundle.name, {"sha256": sha256(bundle), "url": source["url"]}
        )
        columns = [c for c in frame.columns if c in KEEP | IN_KIND_COLUMNS]
        raw[quarter] = frame[columns].copy()
        raw[quarter].attrs = {}
    print(f"Loaded {len(raw)} CE quarters", flush=True)
    fmli = pd.concat(raw.values(), ignore_index=True)
    units = load_ce_unit_inputs(all_quarters, cache_dir=args.ce_cache)
    ce_units = ce_unit_covariates(fmli, units)
    reference_years = sorted(
        int(y) for y in ce_units["reference_year"].unique()
    )
    donors, cps_provenance = load_household_donors(
        [year + 1 for year in reference_years], args.cps_cache
    )
    print(
        f"CE units {len(ce_units)}, CPS donors {len(donors)}, "
        f"reference years {reference_years[0]}-{reference_years[-1]}",
        flush=True,
    )
    config = InKindConfig(implicates=args.implicates)
    imputation = impute_in_kind(ce_units, donors, config)
    print(f"Imputed in {time.time() - started:.0f}s", flush=True)

    def window(target: int) -> pd.DataFrame:
        frame = pd.concat(
            [raw[q] for q in bls_quarter_window(target)], ignore_index=True
        )
        return frame[[c for c in frame.columns if c in KEEP]]

    options = {"youth_policy": "exclude_unresolved", "cpi_series": cpi_series}

    results, detail = {}, {}
    without, with_mean, with_by_implicate = {}, {}, {}
    for target in TARGETS:
        frame = window(target)
        prepared = prepare_out_of_pocket(frame, target, **options)
        base = prepared.result

        def run(in_kind, prepared=prepared):
            return replicate_with_in_kind(prepared, in_kind)

        expected = archived["results"][str(target)]["variants"][
            "exclude_unresolved"
        ]
        if base["thresholds"] != expected["thresholds"]:
            raise RuntimeError(f"{target}: out-of-pocket run != receipt")
        if base["methodology_id"] != expected["methodology_id"]:
            raise RuntimeError(f"{target}: methodology id changed")
        zero = run(zero_in_kind(frame["NEWID"].unique()))
        if zero["thresholds"] != base["thresholds"]:
            raise RuntimeError(f"{target}: zero in-kind changed thresholds")
        implicate_runs = [
            run(imputation.for_target(m, target))
            for m in range(config.implicates)
        ]
        component_lift = {}
        for component in IN_KIND_COMPONENTS:
            runs = []
            for m in range(config.implicates):
                table = imputation.for_target(m, target)
                for other in IN_KIND_COMPONENTS:
                    if other != component:
                        table[other] = 0.0
                runs.append(run(table))
            mean = mean_thresholds(runs)
            component_lift[component] = {
                tenure: 100 * (mean[tenure] / base["thresholds"][tenure] - 1)
                for tenure in TENURES
            }
        published = get_published_thresholds(target)
        ours0 = base["thresholds"]
        ours1 = mean_thresholds(implicate_runs)
        without[target], with_mean[target] = ours0, ours1
        with_by_implicate[target] = [r["thresholds"] for r in implicate_runs]
        stage = [r["construction"]["in_kind"] for r in implicate_runs]
        results[str(target)] = {
            "published": published,
            "without_in_kind": ours0,
            "with_in_kind": ours1,
            "with_in_kind_by_implicate": [
                r["thresholds"] for r in implicate_runs
            ],
            "with_in_kind_between_implicate_sd": spread(implicate_runs),
            "gap_percent_without": {
                t: 100 * (ours0[t] / published[t] - 1) for t in TENURES
            },
            "gap_percent_with": {
                t: 100 * (ours1[t] / published[t] - 1) for t in TENURES
            },
            "share_of_gap_closed": {
                t: (ours1[t] - ours0[t]) / (published[t] - ours0[t])
                for t in TENURES
            },
            "in_kind_lift_percent": {
                t: 100 * (ours1[t] / ours0[t] - 1) for t in TENURES
            },
            "component_lift_percent": component_lift,
            "sample_mean_in_kind_threshold_year_dollars": {
                component: float(
                    np.mean(
                        [
                            s["weighted_mean_threshold_year_dollars"][
                                component
                            ]
                            for s in stage
                        ]
                    )
                )
                for component in IN_KIND_COMPONENTS
            },
            "sample_share_with_any_in_kind": float(
                np.mean([s["weighted_share_with_any"] for s in stage])
            ),
            "band": {
                "without": {
                    k: base["estimation"][k]
                    for k in ("band_rows", "fcsuti_mean", "su_mean")
                },
                "with_mean_fcsuti": float(
                    np.mean(
                        [
                            r["estimation"]["fcsuti_mean"]
                            for r in implicate_runs
                        ]
                    )
                ),
                "with_mean_su": float(
                    np.mean(
                        [r["estimation"]["su_mean"] for r in implicate_runs]
                    )
                ),
            },
            "methodology_id_without": base["methodology_id"],
            "methodology_ids_with": [
                r["methodology_id"] for r in implicate_runs
            ],
        }
        detail[str(target)] = {
            "without": base,
            "with_implicate_0": implicate_runs[0],
        }
        print(
            json.dumps(
                {
                    "target": target,
                    "without": ours0,
                    "with": ours1,
                    "published": published,
                    "closed": results[str(target)]["share_of_gap_closed"],
                }
            ),
            flush=True,
        )

    # Price-index sensitivity: every CPI component series replaced by BLS's
    # published FCSUti index, so each unit's price factor is I(T) / I(c).
    fcsuti_series = pd.Series(
        {year: float(level) for year, level in BLS_FCSUTI_INDEX.items()}
    )
    fcsuti_options = {
        "youth_policy": "exclude_unresolved",
        "cpi_series": {sid: fcsuti_series for sid in CPI_SERIES.values()},
    }
    deflated_without, deflated_with, deflated_by_implicate = {}, {}, {}
    for target in TARGETS:
        prepared = prepare_out_of_pocket(
            window(target), target, **fcsuti_options
        )
        factors = prepared.result["construction"]["inflation_factors"]
        for year, factor in factors.items():
            expected = bls_fcsuti_index(target) / bls_fcsuti_index(year)
            if not math.isclose(factor, expected, rel_tol=1e-12):
                raise RuntimeError(
                    f"{target}: FCSUti factor for {year} is {factor}, "
                    f"expected {expected}"
                )
        runs = [
            replicate_with_in_kind(prepared, imputation.for_target(m, target))
            for m in range(config.implicates)
        ]
        published = get_published_thresholds(target)
        ours0 = prepared.result["thresholds"]
        ours1 = mean_thresholds(runs)
        deflated_without[target], deflated_with[target] = ours0, ours1
        deflated_by_implicate[target] = [r["thresholds"] for r in runs]
        results[str(target)]["fcsuti_deflated"] = {
            "without_in_kind": ours0,
            "with_in_kind": ours1,
            "with_in_kind_by_implicate": deflated_by_implicate[target],
            "with_in_kind_between_implicate_sd": spread(runs),
            "gap_percent_without": {
                t: 100 * (ours0[t] / published[t] - 1) for t in TENURES
            },
            "gap_percent_with": {
                t: 100 * (ours1[t] / published[t] - 1) for t in TENURES
            },
            "in_kind_lift_percent": {
                t: 100 * (ours1[t] / ours0[t] - 1) for t in TENURES
            },
            "inflation_factors": {str(y): f for y, f in factors.items()},
            "methodology_id_without": prepared.result["methodology_id"],
        }
        print(
            json.dumps(
                {
                    "target": target,
                    "fcsuti_deflated_without": ours0,
                    "fcsuti_deflated_with": ours1,
                }
            ),
            flush=True,
        )

    pairs = [(2022, 2025)] + [(t - 1, t) for t in TARGETS if t > TARGETS[0]]
    decompositions = {}
    for base_year, target_year in pairs:
        counterfactual = replicate_thresholds(
            window(base_year), target_year, **options
        )["thresholds"]
        package_price = {
            t: counterfactual[t] / without[base_year][t] for t in TENURES
        }
        bls_price = bls_fcsuti_index(target_year) / bls_fcsuti_index(base_year)
        published_growth = growth_percent(
            get_published_thresholds(base_year),
            get_published_thresholds(target_year),
        )
        common = {
            "base_without": without[base_year],
            "base_with": with_mean[base_year],
            "target_without": without[target_year],
            "target_with": with_mean[target_year],
        }
        # Pair implicates by index to carry imputation uncertainty into
        # the in-kind term (overlapping reference years share draws).
        in_kind_points = [
            decompose_growth(
                base_without=without[base_year],
                base_with=a,
                target_without=without[target_year],
                target_with=b,
                price=bls_price,
            )
            for a, b in zip(
                with_by_implicate[base_year], with_by_implicate[target_year]
            )
        ]
        decompositions[f"{base_year}-{target_year}"] = {
            "package_cpi": decompose_growth(**common, price=package_price),
            "bls_fcsuti_index": decompose_growth(**common, price=bls_price),
            "in_kind_percentage_points_by_implicate": {
                t: [
                    d[t]["percentage_points"]["in_kind"]
                    for d in in_kind_points
                ]
                for t in TENURES
            },
            "counterfactual_base_window_in_target_prices": counterfactual,
            "bls_price_factor": bls_price,
            "published_growth_percent": published_growth,
            "growth_percent_without": growth_percent(
                without[base_year], without[target_year]
            ),
            "growth_percent_with": growth_percent(
                with_mean[base_year], with_mean[target_year]
            ),
            "fcsuti_deflated": {
                "growth_percent_without": growth_percent(
                    deflated_without[base_year], deflated_without[target_year]
                ),
                "growth_percent_with": growth_percent(
                    deflated_with[base_year], deflated_with[target_year]
                ),
                "decomposition": decompose_growth(
                    base_without=deflated_without[base_year],
                    base_with=deflated_with[base_year],
                    target_without=deflated_without[target_year],
                    target_with=deflated_with[target_year],
                    price=bls_price,
                ),
            },
        }

    receipt = {
        "schema_version": 1,
        "generated_by": "scripts/replicate_ce_inkind.py",
        "generated_on": as_of,
        "classification": "current_retrospective_research",
        "git_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "code_sha256": {p: sha256(ROOT / p) for p in CODE_PATHS},
        "runtime": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scipy": scipy.__version__,
        },
        "in_kind_methodology": imputation.methodology,
        "cpi_input": {
            "path": "spm_calculator/data/bls/cpi_annual.json",
            "sha256": sha256(args.cpi_input),
        },
        "bls_fcsuti_index": {
            "levels_2013_100": {
                str(y): v for y, v in BLS_FCSUTI_INDEX.items()
            },
            "source": BLS_FCSUTI_SOURCE,
        },
        "archived_receipt_check": {
            "path": "spm_calculator/data/current/ce_replication_2019_2025.json",
            "sha256": sha256(ARCHIVED_RECEIPT),
            "out_of_pocket_thresholds_identical": True,
            "zero_in_kind_thresholds_identical": True,
        },
        "ce_bundles": bundles,
        "cps_asec_files": cps_provenance,
        "results": results,
        "decompositions": decompositions,
        "imputation_diagnostics": imputation.diagnostics,
        "limitations": [
            "BLS has not published its imputation code or covariates; the models follow its method description",
            "Rental assistance uses a log-normal Tobit on CE unit characteristics and takes reported rents as exact",
            "School lunch donor values for reference years 2014-2017 come from the USDA formula; later years use Census SPM_SCHLUNCH values",
            "The imputation assigns school lunch only to households it imputes as receiving free, reduced-price or universal-free lunch; Census also values paid lunches and, in some years, P-EBT or Summer EBT for other households",
            "Census's school lunch values for reference years 2020-2023 include P-EBT, which arrived on EBT cards; BLS says it follows Census's COVID-era methods, but by its reasoning for leaving out SNAP, reported food spending may already include those benefits",
            "WIC uses USDA's national fiscal-year average food cost; Census has used state averages since its revised 2019 estimates",
            "Participation models share one covariate set across programs and years; CE and CPS income concepts differ",
            "The replication omits home internet spending, which BLS's FCSUti includes",
            "The runs estimate imputation uncertainty (implicate spread) but not sampling uncertainty",
        ],
        "runtime_seconds": round(time.time() - started),
    }
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(
        json.dumps(receipt, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    args.detail.parent.mkdir(parents=True, exist_ok=True)
    args.detail.write_text(
        json.dumps(detail, indent=2, sort_keys=True, default=str) + "\n"
    )
    print(f"Saved {args.receipt} in {time.time() - started:.0f}s", flush=True)


if __name__ == "__main__":
    main()
