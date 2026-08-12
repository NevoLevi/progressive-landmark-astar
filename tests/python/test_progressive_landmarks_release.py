from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import sys
import zipfile

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPOSITORY_ROOT / "scripts" / "package_progressive_landmarks_release.py"
SPEC = importlib.util.spec_from_file_location(
    "package_progressive_landmarks_release", SCRIPT
)
assert SPEC is not None and SPEC.loader is not None
release = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release
SPEC.loader.exec_module(release)


def _fixture_root(tmp_path: Path, *, draft: bool = False) -> Path:
    root = tmp_path / "repository"
    for relative in release.PAYLOAD_PATHS:
        path = root / Path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        content = f"fixture:{relative}\n".encode("utf-8")
        if draft and relative == "report/main.tex":
            content += b"[STUDENT NAME AND ID REQUIRED]\n"
        path.write_bytes(content)
    return root


def _rewrite_archive(
    source: Path,
    target: Path,
    *,
    replacements: dict[str, bytes] | None = None,
    extras: dict[str, bytes] | None = None,
    mode_overrides: dict[str, int] | None = None,
) -> None:
    replacements = replacements or {}
    extras = extras or {}
    mode_overrides = mode_overrides or {}
    with (
        zipfile.ZipFile(source, "r") as old,
        zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as new,
    ):
        members: dict[str, tuple[bytes, int]] = {
            info.filename: (
                replacements.get(info.filename, old.read(info.filename)),
                mode_overrides.get(info.filename, info.external_attr >> 16),
            )
            for info in old.infolist()
        }
        for name, data in extras.items():
            members[name] = (data, release.REGULAR_MODE)
        for name in sorted(members):
            data, mode = members[name]
            info = release._zip_info(name)
            info.external_attr = mode << 16
            new.writestr(info, data)


def test_allowlist_is_active_minimal_and_contains_release_contract() -> None:
    assert len(release.PAYLOAD_PATHS) == 152
    assert list(release.PAYLOAD_PATHS) == sorted(release.PAYLOAD_PATHS)
    assert len(set(release.PAYLOAD_PATHS)) == len(release.PAYLOAD_PATHS)
    for required in (
        ".gitattributes",
        "NOTICE.md",
        "report/AAAI27_AUTHOR_KIT_PROVENANCE.md",
        "requirements-progressive-landmarks-lock.txt",
        "scripts/finalize_progressive_landmarks_metadata.py",
        "scripts/package_progressive_landmarks_release.py",
        "scripts/repro_audit.py",
        "tests/python/test_progressive_landmarks_release.py",
        "tests/python/test_finalize_progressive_landmarks_metadata.py",
        "data/results/progressive_landmarks_v2_rerun1/sealed_evaluation/queries.jsonl",
        "data/processed/progressive_landmarks_analysis_v2/manifest.json",
    ):
        assert required in release.PAYLOAD_PATHS
    forbidden_tokens = (
        "Course Materials and Lectures",
        "Projects Examples",
        "msrla_experiments",
        "src/cpp/",
        "progressive_landmarks_development_v2",
        "progressive_landmarks_sealed_evaluation_v2",
    )
    assert not any(
        token in relative
        for relative in release.PAYLOAD_PATHS
        for token in forbidden_tokens
    )
    assert "report/aaai2027.sty" not in release.PAYLOAD_PATHS
    assert "report/aaai2027.bst" in release.PAYLOAD_PATHS
    notice = (REPOSITORY_ROOT / "NOTICE.md").read_text(encoding="utf-8")
    provenance = (REPOSITORY_ROOT / "report/AAAI27_AUTHOR_KIT_PROVENANCE.md").read_text(
        encoding="utf-8"
    )
    assert "does **not** redistribute `aaai2027.sty`" in notice
    assert "intentionally omits `aaai2027.sty`" in provenance
    assert (
        "391BCE82815BF698B8E382DD3AE7E30C75D7AB46DF140CB295B1266016BC8623" in provenance
    )


def test_active_only_repro_audit_is_public_boundary() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(REPOSITORY_ROOT / "scripts" / "repro_audit.py"),
            "--root",
            str(REPOSITORY_ROOT),
            "--active-only",
            "--json",
        ],
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
    assert "authoritative progressive analysis" in names
    assert "registered exact oracle" not in names


def test_deterministic_build_manifest_and_verify(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    first_manifest = release.build_release(root, first)
    second_manifest = release.build_release(root, second)

    assert first.read_bytes() == second.read_bytes()
    assert first_manifest == second_manifest == release.verify_release(first)
    manifest_core = {
        key: value for key, value in first_manifest.items() if key != "manifest_sha256"
    }
    assert first_manifest["manifest_sha256"] == release._sha256(
        release._canonical_json_bytes(manifest_core)
    )
    assert [row["path"] for row in first_manifest["payload"]] == list(
        release.PAYLOAD_PATHS
    )
    with zipfile.ZipFile(first) as archive:
        infos = archive.infolist()
        assert [info.filename for info in infos] == sorted(
            info.filename for info in infos
        )
        assert all(info.date_time == release.FIXED_ZIP_TIMESTAMP for info in infos)
        assert all(info.compress_type == zipfile.ZIP_STORED for info in infos)
        assert all((info.external_attr >> 16) == release.REGULAR_MODE for info in infos)


def test_default_rejects_draft_and_allow_draft_round_trips(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path, draft=True)
    refused = tmp_path / "refused.zip"
    with pytest.raises(release.ReleaseError, match="placeholders"):
        release.build_release(root, refused)
    assert not refused.exists()

    draft = tmp_path / "draft.zip"
    manifest = release.build_release(root, draft, allow_draft=True)
    assert manifest["draft_placeholders"] == [
        {
            "marker": "[STUDENT NAME AND ID REQUIRED]",
            "path": "report/main.tex",
        }
    ]
    with pytest.raises(release.ReleaseError, match="placeholders"):
        release.verify_release(draft)
    assert release.verify_release(draft, allow_draft=True) == manifest


def test_verify_rejects_payload_tamper(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    pristine = tmp_path / "pristine.zip"
    release.build_release(root, pristine)
    relative = "src/python/progressive_landmarks/core.py"
    member = f"{release.ARCHIVE_ROOT}/{relative}"
    with zipfile.ZipFile(pristine) as archive:
        original = archive.read(member)
    tampered = tmp_path / "tampered.zip"
    _rewrite_archive(
        pristine,
        tampered,
        replacements={member: bytes([original[0] ^ 1]) + original[1:]},
    )
    with pytest.raises(release.ReleaseError, match="hash/size differs"):
        release.verify_release(tampered)


def test_verify_rejects_private_extra_path(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    pristine = tmp_path / "pristine.zip"
    release.build_release(root, pristine)
    private_member = (
        f"{release.ARCHIVE_ROOT}/Course Materials and Lectures/private.pptx"
    )
    polluted = tmp_path / "polluted.zip"
    _rewrite_archive(pristine, polluted, extras={private_member: b"private"})
    with pytest.raises(release.ReleaseError, match="forbidden private/build path"):
        release.verify_release(polluted)


def test_verify_rejects_symlink_member_metadata(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    pristine = tmp_path / "pristine.zip"
    release.build_release(root, pristine)
    member = f"{release.ARCHIVE_ROOT}/README.md"
    linked = tmp_path / "linked.zip"
    _rewrite_archive(
        pristine,
        linked,
        mode_overrides={member: stat.S_IFLNK | 0o777},
    )
    with pytest.raises(release.ReleaseError, match="fixed-mode regular file"):
        release.verify_release(linked)


def test_manifest_json_has_no_duplicate_or_nonfinite_values(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    pristine = tmp_path / "pristine.zip"
    release.build_release(root, pristine)
    member = f"{release.ARCHIVE_ROOT}/{release.MANIFEST_NAME}"
    malformed = tmp_path / "malformed.zip"
    duplicate = b'{"schema":"x","schema":"y"}\n'
    _rewrite_archive(pristine, malformed, replacements={member: duplicate})
    with pytest.raises(release.ReleaseError, match="duplicate JSON key"):
        release.verify_release(malformed)


def test_summary_is_stable_machine_readable_shape(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    archive = tmp_path / "release.zip"
    manifest = release.build_release(root, archive)
    summary = release._summary(archive, manifest)
    json.dumps(summary, allow_nan=False, sort_keys=True)
    assert summary["payload_files"] == len(release.PAYLOAD_PATHS)
    assert len(summary["archive_sha256"]) == 64
