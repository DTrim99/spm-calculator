"""The supported CLI executes the canonical offline forecast, including years."""

import csv
import io
import json
import subprocess
import sys
from pathlib import Path

from spm_calculator.rolling_forecast import load_forecast

ROOT = Path(__file__).parents[1]


def run(*args):
    return subprocess.run(
        [sys.executable, "-S", "-m", "spm_calculator", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def test_offline_cli_years_component_status_and_csv():
    info = run("info")
    assert info.returncode == 0, info.stderr
    assert json.loads(info.stdout)["years"] == list(range(2022, 2037))
    result = run("export", "--format", "csv")
    assert result.returncode == 0, result.stderr
    rows = list(csv.DictReader(io.StringIO(result.stdout)))
    assert len(rows) == 15 * 3
    shares = [row for row in rows if row["year"] == "2025"]
    assert all(
        row["national_status"] == "published"
        and row["housing_share_status"] == "published_anchor"
        for row in shares
    )


def test_offline_county_is_an_area_assignment_not_a_separate_estimate():
    result = run(
        "calculate",
        "--year",
        "2035",
        "--adults",
        "2",
        "--children",
        "2",
        "--county",
        "06037",
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assignment = load_forecast().resolve_county(2035, "06037")
    assert data["provenance"]["county_assignment"] == assignment
    area = run(
        "calculate",
        "--year",
        "2035",
        "--adults",
        "2",
        "--children",
        "2",
        "--area",
        assignment["area_id"],
    )
    assert json.loads(area.stdout)["threshold"] == data["threshold"]


def test_offline_invalid_selections_refuse_without_fallback():
    for args in (
        ("calculate", "--year", "2025", "--adults", "2"),
        ("calculate", "--year", "2037", "--adults", "2", "--national"),
        ("calculate", "--year", "2025", "--adults", "2", "--county", "99999"),
        ("--scenario", "unknown", "info"),
        ("--release", "old.json", "info"),
    ):
        assert run(*args).returncode == 2


def test_offline_area_menu_is_year_specific():
    before, after = (
        run("areas", "--year", "2022"),
        run("areas", "--year", "2024"),
    )
    assert before.returncode == after.returncode == 0
    old, current = json.loads(before.stdout), json.loads(after.stdout)
    assert "45001" in old and "45001" not in current
    assert sum(area["official_published_area"] for area in old.values()) == 342
    assert (
        sum(area["official_published_area"] for area in current.values())
        == 341
    )
