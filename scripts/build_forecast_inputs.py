"""Build compact horizon inputs offline from retained primary source files."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

import openpyxl

from spm_calculator.acs_forecast_sources import (
    load_source_bundle,
    sha256_file,
    verify_file,
)
from spm_calculator.release import TENURES, canonical_bytes

ROOT = Path(__file__).resolve().parents[1]
CURRENT = ROOT / "spm_calculator/data/current"
CBO_COMMIT = "284a95665f9f2f74ed1f482feb629b43fce323da"
PINS = {
    "cbo_calendar_2026_02.csv": "6b54df40058d206247e78fadfe5ebd11e02ae29efd2f0e1a4a86b6767c9ca559",
    "bls_spm_shares.xlsx": "6709dec0e39c8c7b881320161a4373035befc586ecefecd8d78d8431d89b4577",
    "census_spm_2022.xlsx": "c7888e18bbeb437111881fcc70672c1e2898674394a64870423de4d28d8fbc5b",
    "census_spm_2023.xlsx": "618231b83c49335e3123cb661998010769c19b4feaf6d35b38b3371a4de2df4c",
    "census_spm_2024.xlsx": "419feb588d9e81c1c91b2b8400ce1faefd2178750a47af80ee8989b830c7578f",
    "county2020_spm2024_research_mapping.csv": "5acc1605a906e0c5817ede957d0d937049193e2121d18bfb92a1e44d512d8f60",
    "puma2020_county2020_overlap.csv": "b0d9995978f65ccc2e8eb4327d79c10900484fb7eba89f8c91e7df8ebc62f872",
}
STATE_NAMES = {
    "01": "Alabama",
    "06": "California",
    "08": "Colorado",
    "24": "Maryland",
    "36": "New York",
    "45": "South Carolina",
    "47": "Tennessee",
    "55": "Wisconsin",
}


def _source(identity, name, url):
    return {
        "id": identity,
        "package_path": f"spm_calculator/data/current/{name}",
        "url": url,
        "sha256": PINS[name],
        "available_on": "2026-09-09",
    }


def build_inputs():
    for name, digest in PINS.items():
        verify_file(CURRENT / name, digest)
    sources, history, areas = [], {}, {}
    wb = openpyxl.load_workbook(
        CURRENT / "bls_spm_shares.xlsx", data_only=True, read_only=True
    )
    share_rows = list(wb["2005-2024"].values)
    published_shares = {}
    for year in range(2022, 2026):
        column = next(
            i for i, value in enumerate(share_rows[3]) if value == str(year)
        )
        shares = {}
        for tenure, start in zip(TENURES, (0, 20, 40)):
            if (
                share_rows[11 + start][0] != "Total Shelter Share"
                or share_rows[15 + start][0] != "Total Utilities Share"
            ):
                raise ValueError(
                    "Unexpected BLS housing-share component layout"
                )
            shares[tenure] = float(share_rows[11 + start][column]) + float(
                share_rows[15 + start][column]
            )
        published_shares[str(year)] = shares
    sources.append(
        _source(
            "bls-spm-shares",
            "bls_spm_shares.xlsx",
            "https://www.bls.gov/pir/spm/spm_shares.xlsx",
        )
    )
    for year, report in ((2022, 280), (2023, 283), (2024, 287)):
        name = f"census_spm_{year}.xlsx"
        wb = openpyxl.load_workbook(
            CURRENT / name, data_only=False, read_only=True
        )
        sheet = wb[f"Thresholds {year}"]
        rows = [
            row for row in sheet.values if isinstance(row[0], (int, float))
        ]
        assert len(rows) == (342 if year == 2022 else 341)
        shares = {}
        for tenure, column in zip(TENURES, (3, 4, 5)):
            constants = set()
            for row in rows:
                match = re.search(r"\*0\.(\d+)\+\(1-0\.(\d+)\)", row[column])
                if not match or match[1] != match[2]:
                    raise ValueError(
                        "Unrecognized Census housing-share formula"
                    )
                constants.add(float("0." + match[1]))
            if len(constants) != 1:
                raise ValueError("Census tenure share differs between areas")
            shares[tenure] = constants.pop()
        history[str(year)] = {
            "rent_indices": {str(int(r[0])): float(r[2]) for r in rows},
            "housing_shares": published_shares[str(year)],
            "census_workbook_housing_shares": shares,
            "source_id": f"census-spm-{year}",
            "housing_share_source_id": "bls-spm-shares",
            "acs_window": {"start": year - 5, "end": year - 1},
            "component_vintage_note": "Published Census geography; national thresholds and housing shares use corrected BLS2026 publication",
        }
        for row in rows:
            kind = (
                "state_nonmetro"
                if row[1].endswith(" Nonmetro")
                else (
                    "state_metro_residual"
                    if row[1].endswith(" Metro")
                    else "msa"
                )
            )
            areas[str(int(row[0]))] = {"name": row[1], "area_type": kind}
        sources.append(
            _source(
                f"census-spm-{year}",
                name,
                f"https://www2.census.gov/programs-surveys/demo/tables/p60/{report}/SPM-pov-threshold-{year}.xlsx",
            )
        )
    with (CURRENT / "county2020_spm2024_research_mapping.csv").open() as f:
        counties = {row["county_fips"]: row for row in csv.DictReader(f)}
    assignments = {
        county: row["spm_area_code"] or f"modeled_residual_metro:{county[:2]}"
        for county, row in counties.items()
    }
    if len(counties) != 3143:
        raise ValueError("Incomplete2020county research assignment universe")
    extensions = {
        county for county, row in counties.items() if not row["spm_area_code"]
    }
    if len(extensions) != 34 or {c[:2] for c in extensions} != set(
        STATE_NAMES
    ):
        raise ValueError("Unexpected residual extension coverage")
    for state, name in STATE_NAMES.items():
        areas[f"modeled_residual_metro:{state}"] = {
            "name": f"{name} other metropolitan areas (modeled)",
            "area_type": "modeled_residual_metro",
            "state_fips": state,
        }
    fractions = defaultdict(float)
    with (CURRENT / "puma2020_county2020_overlap.csv").open() as f:
        for row in csv.DictReader(f):
            county = row["county_fips"]
            if county in extensions:
                fractions[(row["puma_geoid"], assignments[county])] += float(
                    row["population_share"]
                )
    original = load_source_bundle()["area_allocations"]
    complete = [row for row in original if row["area_code"] is not None]
    complete += [
        {"puma_geoid": puma, "area_code": code, "fraction": value}
        for (puma, code), value in sorted(fractions.items())
        if value > 0
    ]
    sums = defaultdict(float)
    for row in complete:
        sums[row["puma_geoid"]] += row["fraction"]
    if len(sums) != 2462 or any(
        abs(value - 1) > 1e-10 for value in sums.values()
    ):
        raise ValueError("Incomplete PUMA allocation")
    older = {
        c: ("45001" if area == "modeled_residual_metro:45" else area)
        for c, area in assignments.items()
    }
    assignment = {
        "county_vintage": "2020",
        "boundary_vintage": "2013-02-28",
        "assignment_method": "County membership in February2013 CBSAs, published Census area menu and within-state residual groups; public research assignment, not exact confidential CPS state-part assignment",
        "maps": {"2022": older, "2023": assignments},
        "year_maps": {
            str(y): "2022" if y == 2022 else "2023" for y in range(2022, 2037)
        },
    }
    assignment["sha256"] = hashlib.sha256(
        canonical_bytes(assignment)
    ).hexdigest()
    with (CURRENT / "cbo_calendar_2026_02.csv").open() as f:
        indices = {
            int(row["date"]): float(row["value"])
            for row in csv.DictReader(f)
            if row["variable"] == "cpiu"
        }
    rates = {
        str(y): indices[y] / indices[y - 1] - 1 for y in range(2026, 2037)
    }
    sources.append(
        _source(
            "cbo-cpi-u-2026-02",
            "cbo_calendar_2026_02.csv",
            f"https://raw.githubusercontent.com/US-CBO/cbo-data/{CBO_COMMIT}/data/economic/economic_projections/calendar_2026-02.csv",
        )
    )
    for name in (
        "county2020_spm2024_research_mapping.csv",
        "puma2020_county2020_overlap.csv",
    ):
        original_source = next(
            source
            for source in load_source_bundle()["sources"]
            if source["cache_path"] == f"geography/{name}"
        )
        sources.append(
            {
                **original_source,
                **_source(
                    name,
                    name,
                    "https://github.com/PolicyEngine/spm-calculator",
                ),
                "kind": "derived_research_crosswalk",
            }
        )
    result = {
        "schema_version": 1,
        "information_date": "2026-09-09",
        "historical": history,
        "areas": areas,
        "published_housing_shares": published_shares,
        "housing_share_definition": "Total Shelter Share + Total Utilities Share; telephone/internet excluded; corrected revised BLS series",
        "county_assignments": assignment,
        "area_allocations_2020": complete,
        "sources": sources,
        "prices": {
            "source_vintage": "2026-02",
            "source_repository_commit": CBO_COMMIT,
            "series": "cpiu",
            "units": "Index,1982-84=100; calendar-year average",
            "cbo_index_levels": {
                str(y): value for y, value in indices.items()
            },
            "annual_growth_by_year": rates,
            "formula": "CBO_level[y]/CBO_level[y-1]-1; future growth applied to observed BLS2025 anchor",
            "component_assumption": "Apply aggregate CPI-U growth uniformly to all future CE price components; not CBO component forecasts",
            "rent_assumption": "Baseline nominal rent growth equals this CPI-U path; same path deflates future rent cohorts; not CBO local rent forecasts",
        },
        "generator_sha256": sha256_file(Path(__file__)),
    }
    result["content_sha256"] = hashlib.sha256(
        canonical_bytes(result)
    ).hexdigest()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    out = CURRENT / "forecast_horizon_inputs.json"
    data = (
        json.dumps(build_inputs(), indent=2, sort_keys=True, allow_nan=False)
        + "\n"
    ).encode()
    if args.check:
        if out.read_bytes() != data:
            raise ValueError(
                "Horizon source artifact differs from offline rebuild"
            )
    else:
        out.write_bytes(data)
    print(hashlib.sha256(data).hexdigest())


if __name__ == "__main__":
    main()
