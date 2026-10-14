"""An identity adaptation must retain historical evidence and exact science."""

import ast
import copy
import gzip
import hashlib
import json
import shutil
import socket
import sys
from pathlib import Path

import pytest

from scripts import adapt_acs_code_identity as adaptation
from scripts.verify_horizon_extension import retained_bytes
from spm_calculator.acs_forecast_sources import normalization_logic_sha256
from spm_calculator.release import canonical_bytes

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / adaptation.ARCHIVE
CURRENT = ROOT / adaptation.CURRENT
ORIGINAL_HASHES = {
    "acs_forecast_sources.py.txt": (
        "d96fb6556f3a019025c2bd7dfdef5b9f5d05a7a4d819ab568f3a8bc41bf2aed3"
    ),
    "acs_normalized_products.json": (
        "7f9dd53454eeac90a83437643944e47d60d27d35cf3a99d2a988c39be25b99a8"
    ),
    "acs_2021_normalization_receipt.json": (
        "925d0f4fcc06694f808afe7155b88924cb14e425f51cb0dac40363384ed3b5bf"
    ),
    "acs_rolling_forecast.json.gz": (
        "8ad5edcd82c9535e9a8170539f55ce21474245d69f09775d9c0cd9a49f0cda20"
    ),
    "scientific_refresh_receipt.json": (
        "ea431cf183c80c8c0446f614ba355bddc6a30714906460bf1e8df54cd332af54"
    ),
}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def assert_real_link(link):
    stored = (ROOT / link["path"]).read_bytes()
    assert sha256(stored) == link["sha256"]
    if link.get("encoding") == "gzip":
        assert sha256(gzip.decompress(stored)) == link["decoded_sha256"]


@pytest.fixture(scope="module")
def originals():
    return adaptation.original_inputs()[0]


@pytest.fixture(scope="module")
def current():
    # The adaptation's outputs as it left them. The 2036 horizon rebuild
    # replaced some of these files; their exact bytes are retained.
    return {
        name: json.loads(retained_bytes(adaptation.CURRENT + filename))
        for name, filename in {
            "operation": "acs_code_identity_adaptation.json",
            "manifest": "acs_normalized_products.json",
            "normalization_receipt": "acs_2021_normalization_receipt.json",
            "acs_component": "acs_rolling_forecast.json",
            "scientific_refresh_receipt": "scientific_refresh_receipt.json",
            "artifact": "rolling_forecast_2026_09_09.json",
        }.items()
    }


@pytest.fixture
def isolated_inputs(tmp_path, monkeypatch):
    # Relocate real input bytes, changing only the repository root. Keeping
    # verification/digest functions intact makes tamper tests exercise pins.
    paths = [
        adaptation.MODULE,
        adaptation.BASELINE,
        "scripts/adapt_acs_code_identity.py",
        *[adaptation.ARCHIVE + name for name in ORIGINAL_HASHES],
    ]
    for relative in paths:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    monkeypatch.setattr(adaptation, "ROOT", tmp_path)
    return tmp_path


@pytest.mark.parametrize("filename,expected", ORIGINAL_HASHES.items())
def test_original_evidence_retains_immutable_pre_adaptation_bytes(
    filename, expected
):
    stored = (ARCHIVE / filename).read_bytes()
    raw = gzip.decompress(stored) if filename.endswith(".gz") else stored
    assert sha256(raw) == expected


def test_retained_original_download_has_its_original_identity(originals):
    stored = (ROOT / adaptation.BASELINE).read_bytes()
    assert sha256(stored) == (
        "76ab8435f087f167ad01b8495ebd016415ba8086f32bfbd3dab961dcc8976c0a"
    )
    assert originals["artifact"]["content_sha256"] == (
        "b9dbf5ae49697e3bf3abee2fa22b7429703412cb1e58478938a682a0dfddc821"
    )
    assert originals["artifact"]["assumption_sha256"] == (
        "f4a92d547d1a5039701680a57fb02e368438aa5dd1a11e952351020450132983"
    )


def test_adaptation_reproduces_current_outputs_offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Provenance adaptation must remain offline")

    monkeypatch.setattr(socket, "socket", forbidden)
    first = adaptation.build_adaptation()
    assert first == adaptation.build_adaptation()
    for relative, data in first.items():
        assert data == retained_bytes(relative)


def test_operation_links_real_original_and_current_source_bytes(
    originals, current
):
    operation = current["operation"]
    assert operation["kind"] == "equivalent_code_provenance_adaptation"
    for field in (
        "raw_source_reparse_performed",
        "scientific_component_rebuild_performed",
        "normalized_cache_files_rewritten",
    ):
        assert operation[field] is False
    assert set(operation["original"]) == {
        "source",
        "manifest",
        "normalization_receipt",
        "acs_component",
        "scientific_refresh_receipt",
        "artifact",
    }
    for link in operation["original"].values():
        assert_real_link(link)
    assert_real_link(operation["current_source"])
    assert_real_link(operation["builder"])
    assert operation["current_source"] == {
        "path": adaptation.MODULE,
        "sha256": (
            "28d31b3db0caa26f457660f1be9618f63fb08573460c13c68de6cd01b3f7e4bc"
        ),
    }
    assert (
        operation["original"]["source"]["sha256"]
        != (operation["current_source"]["sha256"])
    )
    assert "earlier runs" in operation["evidence_scope"]
    assert "does not repeat or relabel" in operation["evidence_scope"]
    evidence = operation["normalization_evidence"]
    assert evidence["scientific_top_level_sections_byte_equal"] is True
    old_source = originals["source"].decode()
    new_source = (ROOT / adaptation.MODULE).read_text()
    for name, expected in evidence[
        "normalization_function_source_sha256"
    ].items():
        sections = []
        for source in (old_source, new_source):
            node = next(
                node
                for node in ast.parse(source).body
                if isinstance(node, ast.FunctionDef) and node.name == name
            )
            section = ast.get_source_segment(source, node).encode()
            assert sha256(section) == expected
            sections.append(section)
        assert sections[0] == sections[1]
    assert set(evidence["normalization_function_source_sha256"]) == {
        "_universe",
        "normalize_housing",
        "apply_puma_update",
        "_historical_housing",
        "_restore_historical_record_ids",
    }
    for vintage in range(2021, 2025):
        assert evidence["normalization_logic_sha256_by_vintage"][
            str(vintage)
        ] == normalization_logic_sha256(vintage)


def test_every_normalized_cache_identity_and_later_receipt_is_unchanged(
    originals, current
):
    candidate = copy.deepcopy(current["manifest"])
    historical = candidate["2021"]
    assert (
        historical.pop("provenance_adaptation")
        == (current["normalization_receipt"]["provenance_adaptation"])
    )
    assert historical["parser_sha256"] == sha256(
        (ROOT / adaptation.MODULE).read_bytes()
    )
    historical["parser_sha256"] = originals["manifest"]["2021"][
        "parser_sha256"
    ]
    # Reverse only the admitted source identity and chain. Every original
    # cache path, product/source/topcode/PUMA/logic hash and column survives.
    assert canonical_bytes(candidate) == canonical_bytes(originals["manifest"])
    for vintage in ("2022", "2023", "2024"):
        assert canonical_bytes(
            current["manifest"][vintage]
        ) == canonical_bytes(originals["manifest"][vintage])


def test_current_normalization_inherits_exact_reparse_without_relabeling_it(
    originals, current
):
    original = originals["normalization_receipt"]
    candidate = copy.deepcopy(current["normalization_receipt"])
    chain = candidate.pop("provenance_adaptation")
    assert_real_link(chain)
    original_link = candidate.pop("original_reparse_evidence")
    assert (
        original_link
        == current["operation"]["original"]["normalization_receipt"]
    )
    assert_real_link(original_link)
    assert candidate.pop("raw_source_reparse_performed") is False
    assert candidate["verification"] == adaptation.VERIFICATION
    assert original["verification"] == (
        "raw-source reparse exactly equals all retained normalized records"
    )
    assert candidate["parser_sha256"] == sha256(
        (ROOT / adaptation.MODULE).read_bytes()
    )
    assert candidate["manifest_sha256"] == sha256(
        (CURRENT / "acs_normalized_products.json").read_bytes()
    )
    for field in ("verification", "parser_sha256", "manifest_sha256"):
        candidate[field] = original[field]
    assert canonical_bytes(candidate) == canonical_bytes(original)


def test_acs_component_changes_only_exact_declared_provenance(
    originals, current
):
    original = originals["acs_component"]
    candidate = copy.deepcopy(current["acs_component"])
    chain = candidate["metadata"].pop("provenance_adaptation")
    assert chain == current["normalization_receipt"]["provenance_adaptation"]
    assert_real_link(chain)
    identities = candidate["metadata"]["generator_identity"]
    assert identities[adaptation.MODULE] == sha256(
        (ROOT / adaptation.MODULE).read_bytes()
    )
    identities[adaptation.MODULE] = original["metadata"]["generator_identity"][
        adaptation.MODULE
    ]
    addition = candidate["sources"].pop()
    assert addition == {
        "id": "acs-code-identity-adaptation",
        "package_path": chain["path"],
        "sha256": chain["sha256"],
        "title": (
            "Equivalent-code ACS provenance adaptation (no raw-source reparse)"
        ),
    }
    for before, after in zip(original["sources"], candidate["sources"]):
        if before["id"] == "acs-normalized-products":
            assert after["sha256"] == sha256(
                (CURRENT / "acs_normalized_products.json").read_bytes()
            )
            after["sha256"] = before["sha256"]
    assert canonical_bytes(candidate) == canonical_bytes(original)


def test_new_verification_receipt_binds_current_artifacts_and_historical_run(
    originals, current
):
    receipt = current["scientific_refresh_receipt"]
    forecast = current["artifact"]
    assert receipt["kind"] == "equivalent_code_adaptation_verification"
    assert receipt["status"] == "all_exact_equality_checks_passed"
    assert receipt["raw_source_reparse_performed"] is False
    assert receipt["scientific_component_rebuild_performed"] is False
    assert (
        receipt["previous_scientific_refresh"]
        == (current["operation"]["original"]["scientific_refresh_receipt"])
    )
    for key in (
        "previous_scientific_refresh",
        "provenance_adaptation",
        "baseline_artifact",
        "audit_script",
    ):
        assert_real_link(receipt[key])
    assert (
        receipt["baseline_content_sha256"]
        == originals["artifact"]["content_sha256"]
    )
    assert receipt["final_content_sha256"] == forecast["content_sha256"]
    assert receipt["final_assumption_sha256"] == forecast["assumption_sha256"]
    assert (
        receipt["final_content_sha256"] != receipt["baseline_content_sha256"]
    )
    assert set(receipt["artifact_sha256"]) == {
        *originals["scientific_refresh_receipt"]["artifact_sha256"],
        adaptation.CURRENT + "acs_2021_normalization_receipt.json",
        adaptation.OPERATION,
    }
    for path, expected in receipt["artifact_sha256"].items():
        assert sha256(retained_bytes(path)) == expected
    for vintage, identity in receipt["normalized_cache_identity"].items():
        assert identity == {
            key: value
            for key, value in originals["manifest"][vintage].items()
            if key != "parser_sha256"
        }
    chain = receipt["provenance_adaptation"]
    assert forecast["assumptions"]["acs"]["provenance_adaptation"] == chain
    source = next(
        source
        for source in forecast["sources"]
        if source["id"] == "acs-code-identity-adaptation"
    )
    assert source["sha256"] == chain["sha256"]
    assert source["package_path"] == chain["path"]
    comparison = receipt["scientific_equivalence"]
    assert comparison["scientific_fields_exactly_equal"] is True
    assert comparison["calculation_grid_executed"] is True
    assert comparison["complete_scenarios_sha256"] == sha256(
        canonical_bytes(originals["artifact"]["scenarios"])
    )
    assert canonical_bytes(forecast["scenarios"]) == canonical_bytes(
        originals["artifact"]["scenarios"]
    )
    amounts = comparison["amount_comparison"]
    assert amounts["calculations"] == 2 * 14 * 349 * 3 * 8 * 9
    assert amounts["exact_field_comparisons"] == amounts["calculations"] * 5
    assert amounts["max_absolute_difference"] == 0
    assert (
        amounts["original_sha256_big_endian_float64"]
        == (amounts["current_sha256_big_endian_float64"])
    )


@pytest.mark.parametrize("filename", ORIGINAL_HASHES)
def test_adaptation_rejects_changed_archived_evidence(
    isolated_inputs, filename
):
    path = isolated_inputs / adaptation.ARCHIVE / filename
    stored = path.read_bytes()
    if filename.endswith(".gz"):
        path.write_bytes(gzip.compress(gzip.decompress(stored) + b"\n"))
    else:
        path.write_bytes(stored + b"\n")
    with pytest.raises(ValueError, match="Original evidence changed"):
        adaptation.build_adaptation()


def test_adaptation_rejects_changed_original_download(isolated_inputs):
    path = isolated_inputs / adaptation.BASELINE
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(
        ValueError, match="Original canonical download changed"
    ):
        adaptation.build_adaptation()


def test_adaptation_rejects_unapproved_current_source_bytes(isolated_inputs):
    path = isolated_inputs / adaptation.MODULE
    path.write_bytes(path.read_bytes() + b"\n# Unapproved source identity.\n")
    with pytest.raises(ValueError, match="Source bytes differ"):
        adaptation.build_adaptation()


@pytest.mark.parametrize(
    "filename",
    ["acs_2021_normalization_receipt.json", "acs_normalized_products.json"],
)
def test_check_rejects_missing_adaptation_chain(
    isolated_inputs, monkeypatch, filename
):
    for relative, data in adaptation.build_adaptation().items():
        path = isolated_inputs / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    path = isolated_inputs / adaptation.CURRENT / filename
    document = json.loads(path.read_bytes())
    receipt = (
        document["2021"] if filename.endswith("products.json") else document
    )
    del receipt["provenance_adaptation"]
    path.write_bytes(adaptation.encoded(document))
    monkeypatch.setattr(sys, "argv", ["adapt_acs_code_identity.py", "--check"])
    with pytest.raises(ValueError, match="Adaptation output differs"):
        adaptation.main()
