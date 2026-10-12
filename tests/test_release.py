"""Release corruption, temporal leakage and household boundary checks."""

import json
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from spm_calculator.release import (
    SPMRelease,
    SPMUnit,
    load_release,
    seal_release,
)

ROOT = Path(__file__).resolve().parents[1]


def test_published_reference_and_strict_poverty_boundary():
    release = load_release()
    # Full-precision 2025 BLS workbook, pinned by the release source digest.
    amount = 41700.555713
    equal = release.calculate_unit(
        SPMUnit("u", 2, 2, "renter", 2025, resources=amount)
    )
    assert equal["threshold"] == amount
    assert equal["is_in_poverty"] is False
    below = release.calculate_unit(
        SPMUnit("u", 2, 2, "renter", 2025, resources=amount - 1)
    )
    assert below["is_in_poverty"] is True
    assert equal["provenance"]["housing_share"]["status"] == "carried"
    assert equal["provenance"]["housing_share"]["reference_year"] == 2024


def test_release_snapshot_and_returned_entries_do_not_alias():
    document = load_release().to_dict()
    release = SPMRelease.from_dict(document)
    document["years"]["2025"]["thresholds"]["renter"] = 1
    entry = release.entry(2025)
    entry["thresholds"]["renter"] = 2
    assert release.entry(2025)["thresholds"]["renter"] == 41700.555713


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["years"]["2025"]["thresholds"].pop("renter"),
        lambda d: d["years"]["2025"]["thresholds"].update(renter=float("nan")),
        lambda d: d.update(information_date="2026-08-01"),
        lambda d: d["sources"][0].update(publication_date="2026-09-09"),
        lambda d: d["years"]["2025"].update(available_on="2026-07-01"),
        lambda d: d["years"]["2025"]["housing_shares"].update(renter=1.1),
        lambda d: d.update(units="USD/month"),
        lambda d: d["years"]["2025"]["housing_share_provenance"].update(
            status="published"
        ),
        lambda d: d["years"]["2025"].update(source_ids=["bls-2025"]),
    ],
)
def test_invalid_artifacts_cannot_be_resealed(change):
    document = load_release().to_dict()
    change(document)
    with pytest.raises(ValueError):
        seal_release(document)


def test_corrupt_content_expected_digest_and_duplicate_keys(tmp_path):
    document = load_release().to_dict()
    document["years"]["2025"]["thresholds"]["renter"] += 1
    with pytest.raises(ValueError, match="hash mismatch"):
        SPMRelease.from_dict(document)
    with pytest.raises(ValueError, match="expected_sha256"):
        load_release(expected_sha256="0" * 64)
    path = tmp_path / "duplicate.json"
    path.write_text('{"schema_version":1,"schema_version":1}')
    with pytest.raises(ValueError, match="Duplicate"):
        load_release(path)


def test_finite_inputs_cannot_silently_overflow_outputs():
    document = load_release().to_dict()
    document["years"]["2025"]["thresholds"]["renter"] = 1e308
    release = SPMRelease.from_dict(seal_release(document))
    with pytest.raises(ValueError, match="finite"):
        release.calculate_unit(
            SPMUnit("u", 2, 2, "renter", 2025, geographic_adjustment=3)
        )


def test_future_lookup_and_information_dates_are_explicit():
    release = load_release()
    with pytest.raises(ValueError, match="no entry"):
        release.entry(2026)
    with pytest.raises(ValueError, match="information date"):
        load_release(as_of="2026-08-01")
    document = release.to_dict()
    document["years"]["2025"]["status"] = "nowcast"
    estimate = SPMRelease.from_dict(seal_release(document))
    with pytest.raises(ValueError, match="allow_estimated"):
        estimate.entry(2025)
    assert estimate.entry(2025, allow_estimated=True)["status"] == "nowcast"


def test_pinned_geography_and_unknown_area_handling():
    release = load_release()
    result = release.calculate_unit(
        SPMUnit(
            "u",
            2,
            2,
            "renter",
            2025,
            geography_kind="congressional_district",
            geography_id="0601",
        )
    )
    geo = result["provenance"]["geography"]
    assert geo["year"] == 2023
    assert result["geographic_factor"] == pytest.approx(
        1 - 0.443 + 0.443 * geo["rent_index"]
    )
    with pytest.raises(ValueError, match="unavailable"):
        release.geography_factor(2025, "renter", kind="metro", geoid="unknown")
    fallback = release.geography_factor(
        2025, "renter", kind="metro", geoid="unknown", missing="national"
    )
    assert fallback["status"] == "explicit_national_fallback"
    with pytest.raises(ValueError, match="negative housing"):
        release.calculate_unit(
            SPMUnit("u", 2, 2, "renter", 2025, geographic_adjustment=0.1)
        )


def test_release_builder_reproduces_exact_bundled_bytes():
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/build_release.py"), "--check"],
        cwd=ROOT,
        check=True,
    )


def test_new_download_cannot_be_backdated_into_existing_release(tmp_path):
    source = tmp_path / "data"
    shutil.copytree(ROOT / "spm_calculator/data", source)
    path = source / "acs_rents_2023.json"
    document = json.loads(path.read_text())
    document["retrieved_on"] = "2026-09-09"
    path.write_text(json.dumps(document))
    build = runpy.run_path(str(ROOT / "scripts/build_release.py"))[
        "build_document"
    ]
    build.__globals__["DATA"] = source
    with pytest.raises(
        ValueError, match="after this release information date"
    ):
        build()


def test_published_web_export_reproduces_exact_bundled_bytes(tmp_path):
    # Verify the deployed release independently of a pending package version.
    # The exporter still reads the current calculation code and bundled bytes.
    config = json.loads(
        (ROOT / "web/public/data/release_config.json").read_text()
    )
    published_version = config["packageVersion"]
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nversion = "{published_version}"\n'
    )
    (tmp_path / "scripts").mkdir()
    script = tmp_path / "scripts/export_web_release.py"
    shutil.copyfile(ROOT / "scripts/export_web_release.py", script)
    for directory in ("spm_calculator", "web"):
        (tmp_path / directory).symlink_to(
            ROOT / directory, target_is_directory=True
        )
    subprocess.run(
        [
            sys.executable,
            str(script),
            "--published-package-version",
            published_version,
            "--check",
        ],
        cwd=tmp_path,
        check=True,
    )
