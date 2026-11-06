"""Build the packaged parameters for the CE in-kind imputation stage.

Two inputs are converted here; the USDA tables in
``spm_calculator/data/inkind/usda_*.csv`` are committed transcriptions
with a source URL on every row and are read directly by the package.

1. Official poverty thresholds, 2014-2024, from the Census Bureau's
   ``threshYY.xlsx`` tables:
   https://www2.census.gov/programs-surveys/cps/tables/time-series/historical-poverty-thresholds/
   Download them into one directory (``--thresholds-dir``) first.
2. The per-child school lunch values Census assigned in the public-use
   CPS ASEC files for reference years 2018 and 2019 (survey years 2019
   and 2020), used as the external check on the USDA formula. They are
   computed from the cached public-use files (``--cps-cache``) and stored
   as aggregate modes only.

Usage::

    uv run python scripts/build_inkind_parameters.py \
        --thresholds-dir ~/reviews/spm-inkind/downloads/opm_thresholds \
        --cps-cache ~/.cache/spm-calculator/cps-asec
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import openpyxl

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "spm_calculator" / "data" / "inkind"
THRESHOLD_URL = (
    "https://www2.census.gov/programs-surveys/cps/tables/time-series/"
    "historical-poverty-thresholds/thresh{yy}.xlsx"
)
SIZE_ROWS = {
    "Three people": 3,
    "Four people": 4,
    "Five people": 5,
    "Six people": 6,
    "Seven people": 7,
    "Eight people": 8,
    "Nine people or more": 9,
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _numbers(row) -> list[int]:
    return [int(v) for v in row[2:11] if isinstance(v, (int, float))]


def parse_thresholds(path: Path) -> dict:
    """Return the threshold matrix of one Census ``threshYY.xlsx`` table."""
    sheet = openpyxl.load_workbook(path, data_only=True).worksheets[0]
    one, two, larger = {}, {}, {}
    section = None
    for row in sheet.iter_rows(values_only=True):
        label = str(row[0] or "").strip()
        if label.startswith("One person"):
            section = 1
        elif label.startswith("Two people"):
            section = 2
        elif label.startswith("Under 65") and section == 1:
            one["under_65"] = _numbers(row)[0]
        elif label.startswith("65 years") and section == 1:
            one["65_plus"] = _numbers(row)[0]
        elif label.startswith("Householder under 65") and section == 2:
            two["under_65"] = _numbers(row)
        elif label.startswith("Householder 65") and section == 2:
            two["65_plus"] = _numbers(row)
        elif label in SIZE_ROWS:
            size = SIZE_ROWS[label]
            values = _numbers(row)
            if len(values) != size:
                raise ValueError(f"{path.name}: {label} has {values}")
            larger[str(size)] = values
    if set(one) != {"under_65", "65_plus"} or set(two) != set(one):
        raise ValueError(f"{path.name}: incomplete one/two-person rows")
    if any(len(two[key]) != 2 for key in two) or len(larger) != 7:
        raise ValueError(f"{path.name}: unexpected threshold layout")
    return {"1": one, "2": two, **larger}


def observed_census_lunch_values(cps_cache: Path) -> dict:
    """Modal per-child lunch values in Census's public-use SPM files.

    Households where every school-age child receives a free or
    reduced-price lunch and that hold one SPM unit isolate the value
    Census assigns per participating child. The free and reduced values
    are the two most common positive per-child amounts.
    """
    from spm_calculator.cps_asec import load_household_donors

    observed = {}
    for survey_year in (2019, 2020):
        donors, _ = load_household_donors([survey_year], cps_cache)
        clean = donors.loc[
            donors["nslp"]
            & (donors["nslp_children"] > 0)
            & (donors["nslp_children"] == donors["n_5_18"])
        ]
        per_child = (clean["spm_schlunch"] / clean["nslp_children"]).round()
        counts = per_child.loc[per_child > 0].value_counts()
        top = sorted(int(v) for v in counts.index[:2])
        observed[str(survey_year - 1)] = {
            "free": top[1],
            "reduced": top[0],
            "households": int(len(clean)),
            "share_of_households_at_these_two_values": float(
                counts.iloc[:2].sum() / counts.sum()
            ),
            "survey_year": survey_year,
            "method": (
                "Two most common positive values of SPM_SCHLUNCH summed "
                "over the household's SPM units divided by HFLUNNO, among "
                "HFLUNCH == 1 households whose HFLUNNO equals the count "
                "of members aged 5-18"
            ),
        }
    return observed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--thresholds-dir", type=Path, required=True)
    parser.add_argument("--cps-cache", type=Path, required=True)
    args = parser.parse_args()
    years, files = {}, {}
    for year in range(2014, 2025):
        path = args.thresholds_dir / f"thresh{year % 100:02d}.xlsx"
        years[str(year)] = parse_thresholds(path)
        files[str(year)] = {
            "url": THRESHOLD_URL.format(yy=f"{year % 100:02d}"),
            "sha256": _sha256(path),
        }
    thresholds = {
        "description": (
            "Official poverty thresholds by family size, number of related "
            "children under 18 and, for one- and two-person units, age of "
            "the householder (Census Bureau)."
        ),
        "files": files,
        "years": years,
    }
    (OUT / "opm_thresholds.json").write_text(
        json.dumps(thresholds, indent=2, sort_keys=True) + "\n"
    )
    observed = observed_census_lunch_values(args.cps_cache)
    for values in observed.values():
        if not np.isfinite(values["free"]) or values["free"] <= 0:
            raise ValueError("Observed Census lunch value must be positive")
    (OUT / "census_school_lunch_observed.json").write_text(
        json.dumps(
            {
                "description": (
                    "Per-child annual school lunch values the Census Bureau "
                    "assigned in the public-use CPS ASEC, by reference year. "
                    "Aggregate modes only; no record-level data."
                ),
                "source": (
                    "https://www2.census.gov/programs-surveys/cps/datasets/"
                    "{survey_year}/march/asecpub{yy}csv.zip"
                ),
                "reference_years": observed,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    print(json.dumps(observed, indent=2))


if __name__ == "__main__":
    main()
