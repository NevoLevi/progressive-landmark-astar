from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPOSITORY_ROOT / "scripts" / "repro_audit.py"
SPEC = importlib.util.spec_from_file_location("repro_audit", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
repro_audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = repro_audit
SPEC.loader.exec_module(repro_audit)


def test_current_active_checkpoint_has_no_integrity_failures() -> None:
    checks = repro_audit.audit_repository(REPOSITORY_ROOT, include_historical=False)
    failures = [check for check in checks if check.status == repro_audit.FAIL]
    assert failures == []
    for active_name in (
        "active progressive-landmarks protocol",
        "authoritative progressive development",
        "progressive external replay and freeze",
        "authoritative progressive sealed evaluation",
        "authoritative progressive analysis",
        "superseded progressive attempt isolation",
    ):
        check = next(item for item in checks if item.name == active_name)
        assert check.status == repro_audit.PASS
    archived_boundary = next(
        check for check in checks if check.name == "archived MVC/CBS evidence boundary"
    )
    assert archived_boundary.status == repro_audit.PASS
    assert "excluded from active provenance" in archived_boundary.detail


def test_full_workspace_historical_checkpoint_has_no_integrity_failures() -> None:
    if not (REPOSITORY_ROOT / repro_audit.NORMAL_CONFIG).is_file():
        pytest.skip("archived MVC/CBS workspace is not part of the public artifact")
    checks = repro_audit.audit_repository(REPOSITORY_ROOT)
    failures = [check for check in checks if check.status == repro_audit.FAIL]
    assert failures == []
    names = {check.name for check in checks}
    assert "sealed split registration" in names
    assert "registered exact oracle" in names
    assert "bottom-k normalized calibration" in names
    contextual = next(check for check in checks if check.name == "contextual model")
    assert contextual.status == repro_audit.PASS
    assert "mechanism gate rejected" in contextual.detail
    assert "holdout remains sealed" in contextual.detail


def test_active_only_audit_has_no_historical_dependencies() -> None:
    checks = repro_audit.audit_repository(REPOSITORY_ROOT, include_historical=False)
    failures = [check for check in checks if check.status == repro_audit.FAIL]
    assert failures == []
    names = {check.name for check in checks}
    assert "active progressive-landmarks protocol" in names
    assert "authoritative progressive analysis" in names
    assert "active submission report source" in names
    assert "sealed split registration" not in names
    assert "contextual model" not in names
    assert "local solver build" not in names


def test_strict_json_rejects_duplicate_keys(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text('{"same":1,"same":2}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        repro_audit.load_json(path)


def test_sha256_file_is_binary_and_deterministic(tmp_path: Path) -> None:
    path = tmp_path / "payload.bin"
    path.write_bytes(b"repro-audit\x00\xff\n")
    assert (
        repro_audit.sha256_file(path)
        == "92e5534f4aeb4f76ca73f55d95a995e4087b999486717957aedb1aab409bc427"
    )


def test_superseded_progressive_reference_detection_is_path_exact() -> None:
    authoritative = {
        "path": "data/results/progressive_landmarks_v2_rerun1/development/manifest.json"
    }
    assert repro_audit.superseded_progressive_references(authoritative) == []

    superseded = {
        "manifest": "data/results/progressive_landmarks_development_v2/manifest.json",
        "audit": "data/results/development_audit.json",
    }
    assert repro_audit.superseded_progressive_references(superseded) == [
        "data/results/development_audit.json",
        "data/results/progressive_landmarks_development_v2/manifest.json",
    ]


def test_progressive_expected_completion_markers_match_checkout() -> None:
    for relative, expected_sha256 in repro_audit.PROGRESSIVE_EXPECTED_FILES.items():
        assert repro_audit.sha256_file(REPOSITORY_ROOT / relative) == expected_sha256


def test_cli_json_is_read_only_and_complete() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["summary"]["FAIL"] == 0
    assert payload["summary"]["PENDING"] == 0
    assert payload["summary"]["WARN"] == 0
    assert payload["summary"]["PASS"] == 10
    names = {check["name"] for check in payload["checks"]}
    assert "registered exact oracle" not in names


def test_cli_active_only_excludes_historical_checks() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--active-only", "--json"],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["summary"]["FAIL"] == 0
    names = {check["name"] for check in payload["checks"]}
    assert "authoritative progressive sealed evaluation" in names
    assert "registered exact oracle" not in names
    assert "contextual model" not in names
