#!/usr/bin/env python3
"""Build and verify the minimal public progressive-landmarks release.

The release boundary is deliberately an allowlist.  Nothing outside
``PAYLOAD_PATHS`` can enter the archive, even if it is present in the research
workspace.  Archives use stored ZIP members with fixed metadata so identical
input bytes produce identical archive bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
from typing import Any, Iterable, Mapping
import zipfile


SCHEMA = "progressive-landmarks-public-release-v1"
ARCHIVE_ROOT = "progressive-landmarks-artifact"
MANIFEST_NAME = "RELEASE_MANIFEST.json"
FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
REGULAR_MODE = stat.S_IFREG | 0o644
MAX_FILE_BYTES = 50 * 1024 * 1024  # GitHub warns at, but still accepts, 50 MiB.

MAP_NAMES = (
    "maze-128-128-1.map",
    "maze-128-128-2.map",
    "maze-128-128-10.map",
    "random-64-64-10.map",
    "random-32-32-20.map",
    "random-32-32-10.map",
    "room-64-64-8.map",
    "room-64-64-16.map",
    "room-32-32-4.map",
    "warehouse-20-40-10-2-1.map",
    "warehouse-10-20-10-2-1.map",
    "warehouse-10-20-10-2-2.map",
)

ANALYSIS_FIGURES = (
    "family_mechanism_decomposition",
    "per_map_time_ratios",
    "preprocessing_amortization",
    "saved_work_vs_time",
    "stage_schematic",
)

REPORT_SECTIONS = tuple(
    f"{index:02d}_{name}.tex"
    for index, name in (
        (1, "introduction"),
        (2, "related_work"),
        (3, "questions"),
        (4, "search_model"),
        (5, "methods"),
        (6, "protocol"),
        (7, "results"),
        (8, "discussion"),
        (9, "limitations"),
        (10, "reproducibility"),
        (11, "conclusion"),
    )
)

ROOT_FILES = (
    ".gitattributes",
    ".gitignore",
    "NOTICE.md",
    "README.md",
    "pyproject.toml",
    "requirements-progressive-landmarks-lock.txt",
)

ACTIVE_PROJECT_FILES = tuple(
    f"active_project/{name}"
    for name in (
        "EXPERIMENT_PROVENANCE.md",
        "LITERATURE_MAP.md",
        "PLAN.md",
        "PROJECT_SPEC.md",
        "STATUS.md",
        "TOPIC_PROPOSAL.md",
    )
)

SOURCE_FILES = (
    "data/source/movingai_mapf_2021-06-17/CORPUS_MANIFEST.json",
    "data/source/movingai_mapf_2021-06-17/PROVENANCE.md",
    "data/source/movingai_mapf_2021-06-17/SHA256SUMS",
    "data/source/movingai_mapf_2021-06-17/archives/mapf-map.zip",
    "data/source/movingai_mapf_2021-06-17/archives/mapf-scen-random.zip",
    *(f"data/source/movingai_mapf_2021-06-17/corpus/maps/{name}" for name in MAP_NAMES),
    *(
        "data/source/movingai_mapf_2021-06-17/corpus/scenarios/"
        f"{map_name.removesuffix('.map')}-random-{scenario_index}.scen"
        for map_name in MAP_NAMES
        for scenario_index in range(1, 5)
    ),
)

RESULT_FILES = (
    "data/results/progressive_landmarks_v2_rerun1/development/development_freeze_candidate.json",
    "data/results/progressive_landmarks_v2_rerun1/development/manifest.json",
    "data/results/progressive_landmarks_v2_rerun1/development/maps.json",
    "data/results/progressive_landmarks_v2_rerun1/development/queries.jsonl",
    "data/results/progressive_landmarks_v2_rerun1/development/run.json",
    "data/results/progressive_landmarks_v2_rerun1/development_audit.json",
    "data/results/progressive_landmarks_v2_rerun1/sealed_evaluation_freeze.json",
    "data/results/progressive_landmarks_v2_rerun1/sealed_evaluation/manifest.json",
    "data/results/progressive_landmarks_v2_rerun1/sealed_evaluation/maps.json",
    "data/results/progressive_landmarks_v2_rerun1/sealed_evaluation/queries.jsonl",
    "data/results/progressive_landmarks_v2_rerun1/sealed_evaluation/run.json",
)

ANALYSIS_FILES = (
    "data/processed/progressive_landmarks_analysis_v2/hypothesis_table.csv",
    "data/processed/progressive_landmarks_analysis_v2/hypothesis_table.tex",
    "data/processed/progressive_landmarks_analysis_v2/manifest.json",
    "data/processed/progressive_landmarks_analysis_v2/map_metrics.csv",
    "data/processed/progressive_landmarks_analysis_v2/provenance.json",
    "data/processed/progressive_landmarks_analysis_v2/query_metrics.csv",
    "data/processed/progressive_landmarks_analysis_v2/summary.json",
    *(
        f"data/processed/progressive_landmarks_analysis_v2/figures/{name}.{suffix}"
        for name in ANALYSIS_FIGURES
        for suffix in ("pdf", "png")
    ),
)

SCRIPT_FILES = tuple(
    f"scripts/{name}.py"
    for name in (
        "analyze_progressive_landmarks",
        "finalize_progressive_landmarks_metadata",
        "freeze_progressive_landmarks_development",
        "package_progressive_landmarks_release",
        "repro_audit",
        "run_progressive_landmarks",
        "verify_progressive_landmarks_protocol",
    )
)

PACKAGE_FILES = tuple(
    f"src/python/progressive_landmarks/{name}"
    for name in (
        "__init__.py",
        "analysis.py",
        "core.py",
        "development_gate.py",
        "protocol.py",
        "runner.py",
    )
)

TEST_FILES = tuple(
    f"tests/python/{name}.py"
    for name in (
        "test_finalize_progressive_landmarks_metadata",
        "test_progressive_landmarks_analysis",
        "test_progressive_landmarks_core",
        "test_progressive_landmarks_development_gate",
        "test_progressive_landmarks_protocol",
        "test_progressive_landmarks_release",
        "test_progressive_landmarks_runner",
        "test_repro_audit",
    )
)

REPORT_FILES = (
    "report/.gitignore",
    "report/AAAI27_AUTHOR_KIT_PROVENANCE.md",
    "report/README.md",
    "report/aaai2027.bst",
    "report/main.pdf",
    "report/main.tex",
    *(f"report/sections/{name}" for name in REPORT_SECTIONS),
    "report/generated/README.md",
    "report/generated/family_mechanism_decomposition.pdf",
    "report/generated/hypothesis_table.tex",
    "report/generated/per_map_time_ratios.pdf",
    "report/generated/preprocessing_amortization.pdf",
    "report/generated/saved_work_vs_time.pdf",
    "report/generated/stage_schematic.pdf",
)

PAYLOAD_PATHS = tuple(
    sorted(
        {
            *ROOT_FILES,
            *ACTIVE_PROJECT_FILES,
            "configs/progressive_landmarks_v2.json",
            *SOURCE_FILES,
            *RESULT_FILES,
            *ANALYSIS_FILES,
            "references/references.bib",
            *SCRIPT_FILES,
            *PACKAGE_FILES,
            *TEST_FILES,
            *REPORT_FILES,
        }
    )
)

_EXPECTED_PAYLOAD_COUNT = 152
if len(PAYLOAD_PATHS) != _EXPECTED_PAYLOAD_COUNT:
    raise RuntimeError(
        f"release allowlist drifted: expected {_EXPECTED_PAYLOAD_COUNT} paths, "
        f"observed {len(PAYLOAD_PATHS)}"
    )

FORBIDDEN_COMPONENTS = frozenset(
    {
        ".git",
        ".pytest_cache",
        "Course Materials and Lectures",
        "Projects Examples",
        "__pycache__",
        "build",
        "external",
        "output",
        "tmp",
        "vcpkg_installed",
    }
)
FORBIDDEN_ROOT_FILES = frozenset(
    {
        "CMakeLists.txt",
        "EXPERIMENT_LOG.md",
        "List of Projects Chosen Already.md",
        "List of Projects Chosen Already.txt",
        "PLAN.md",
        "PROJECT_SPEC.md",
        "RESEARCH_LOG.md",
        "STATUS.md",
        "TOPIC_PROPOSAL.md",
        "requirements-direct-lock.txt",
        "vcpkg.json",
        "הנחיות להגשת הפרויקט.pdf",
    }
)
FORBIDDEN_PREFIXES = (
    "configs/cbs_",
    "configs/policy_",
    "data/external/",
    "data/fixtures/",
    "data/instances/",
    "data/processed/policy_",
    "data/results/progressive_landmarks_development_v2/",
    "data/results/progressive_landmarks_sealed_evaluation_v2/",
    "src/cpp/",
    "src/python/msrla_experiments/",
)

DRAFT_MARKERS = (
    "[STUDENT NAME AND ID REQUIRED]",
    "BLOCKER: INSERT PUBLIC REPOSITORY URL AND COMMIT SHA",
    "<REPOSITORY_URL>",
    "<COMMIT_SHA>",
)
DRAFT_SCAN_PATHS = frozenset(
    {
        "README.md",
        "report/main.tex",
        "report/sections/10_reproducibility.tex",
    }
)
TEXT_SUFFIXES = frozenset(
    {
        "",
        ".bib",
        ".bst",
        ".csv",
        ".json",
        ".jsonl",
        ".map",
        ".md",
        ".py",
        ".scen",
        ".sty",
        ".tex",
        ".toml",
        ".txt",
    }
)


class ReleaseError(RuntimeError):
    """Raised when release construction or verification fails closed."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _pretty_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")


def _strict_json(data: bytes, *, label: str) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ReleaseError(f"{label} has duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        return json.loads(
            data.decode("utf-8-sig"),
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ReleaseError(f"{label} contains non-finite JSON value {value!r}")
            ),
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"cannot decode {label}: {exc}") from exc


def _validate_relative_path(relative: str) -> PurePosixPath:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ReleaseError(
            f"release path is not canonical POSIX relative form: {relative!r}"
        )
    path = PurePosixPath(relative)
    if (
        path.is_absolute()
        or path.as_posix() != relative
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ReleaseError(f"unsafe release path: {relative!r}")
    if any(
        part in FORBIDDEN_COMPONENTS or part.startswith("build-") for part in path.parts
    ):
        raise ReleaseError(f"forbidden private/build path in release: {relative}")
    if len(path.parts) == 1 and relative in FORBIDDEN_ROOT_FILES:
        raise ReleaseError(f"forbidden historical root file in release: {relative}")
    if any(relative.startswith(prefix) for prefix in FORBIDDEN_PREFIXES):
        raise ReleaseError(
            f"forbidden historical/private prefix in release: {relative}"
        )
    return path


def _read_regular_file(root: Path, relative: str) -> bytes:
    posix = _validate_relative_path(relative)
    cursor = root
    for part in posix.parts:
        cursor = cursor / part
        try:
            metadata = cursor.lstat()
        except OSError as exc:
            raise ReleaseError(f"missing release payload {relative}: {exc}") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise ReleaseError(f"release payload traverses a symbolic link: {relative}")
    if not stat.S_ISREG(metadata.st_mode):
        raise ReleaseError(f"release payload is not a regular file: {relative}")
    resolved_root = root.resolve(strict=True)
    resolved = cursor.resolve(strict=True)
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ReleaseError(
            f"release payload escapes repository root: {relative}"
        ) from exc
    if metadata.st_size >= MAX_FILE_BYTES:
        raise ReleaseError(
            f"release payload reaches the 50 MiB warning boundary: {relative} "
            f"({metadata.st_size} bytes)"
        )
    try:
        data = cursor.read_bytes()
    except OSError as exc:
        raise ReleaseError(f"cannot read release payload {relative}: {exc}") from exc
    if len(data) != metadata.st_size:
        raise ReleaseError(f"release payload changed while being read: {relative}")
    return data


def _draft_findings(payloads: Mapping[str, bytes]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for relative in sorted(payloads):
        if relative not in DRAFT_SCAN_PATHS:
            continue
        if PurePosixPath(relative).suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = payloads[relative].decode("utf-8-sig")
        except UnicodeError:
            continue
        for marker in DRAFT_MARKERS:
            if marker in text:
                findings.append({"path": relative, "marker": marker})
    return findings


def _manifest(payloads: Mapping[str, bytes]) -> dict[str, Any]:
    entries = [
        {
            "bytes": len(payloads[relative]),
            "path": relative,
            "sha256": _sha256(payloads[relative]),
        }
        for relative in sorted(payloads)
    ]
    value: dict[str, Any] = {
        "schema": SCHEMA,
        "archive_root": ARCHIVE_ROOT,
        "determinism": {
            "compression": "stored",
            "member_mode": "0644",
            "member_timestamp": "1980-01-01T00:00:00",
            "path_order": "ascending-posix",
        },
        "limits": {"max_payload_file_bytes_exclusive": MAX_FILE_BYTES},
        "draft_placeholders": _draft_findings(payloads),
        "payload": entries,
    }
    value["manifest_sha256"] = _sha256(_canonical_json_bytes(value))
    return value


def _zip_info(member_name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(member_name, date_time=FIXED_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.create_version = 20
    info.extract_version = 20
    info.external_attr = REGULAR_MODE << 16
    info.internal_attr = 0
    info.extra = b""
    info.comment = b""
    return info


def _member_name(relative: str) -> str:
    return f"{ARCHIVE_ROOT}/{relative}"


def build_release(
    repository_root: str | Path,
    archive_path: str | Path,
    *,
    allow_draft: bool = False,
) -> dict[str, Any]:
    """Build a deterministic allowlisted archive and verify it before return."""

    root = Path(repository_root).resolve(strict=True)
    if not root.is_dir() or root.is_symlink():
        raise ReleaseError("repository root must be a real directory, not a link")
    output = Path(archive_path)
    if not output.is_absolute():
        output = (Path.cwd() / output).resolve()
    if output.exists() or output.is_symlink():
        raise ReleaseError(f"refusing to overwrite release archive: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    payloads = {
        relative: _read_regular_file(root, relative) for relative in PAYLOAD_PATHS
    }
    draft = _draft_findings(payloads)
    if draft and not allow_draft:
        locations = ", ".join(sorted({item["path"] for item in draft}))
        raise ReleaseError(
            "release still contains identity/repository placeholders in: " + locations
        )
    manifest = _manifest(payloads)
    manifest_bytes = _pretty_json_bytes(manifest)
    members = {_member_name(relative): data for relative, data in payloads.items()}
    members[_member_name(MANIFEST_NAME)] = manifest_bytes

    try:
        with output.open("xb") as raw:
            with zipfile.ZipFile(raw, mode="w", allowZip64=True) as archive:
                archive.comment = b""
                for member_name in sorted(members):
                    archive.writestr(_zip_info(member_name), members[member_name])
        verified = verify_release(output, allow_draft=allow_draft)
    except BaseException:
        try:
            output.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return verified


def _manifest_without_hash(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in manifest.items() if key != "manifest_sha256"}


def _validate_zip_metadata(info: zipfile.ZipInfo) -> None:
    if info.is_dir():
        raise ReleaseError(f"directory entries are forbidden: {info.filename}")
    if info.date_time != FIXED_ZIP_TIMESTAMP:
        raise ReleaseError(f"non-deterministic timestamp: {info.filename}")
    if info.compress_type != zipfile.ZIP_STORED:
        raise ReleaseError(f"non-deterministic compression method: {info.filename}")
    if info.create_system != 3 or (info.external_attr >> 16) != REGULAR_MODE:
        raise ReleaseError(f"member is not fixed-mode regular file: {info.filename}")
    if info.extra or info.comment:
        raise ReleaseError(f"member has non-empty ZIP metadata: {info.filename}")
    if info.flag_bits & 0x1:
        raise ReleaseError(f"encrypted ZIP members are forbidden: {info.filename}")
    if info.file_size >= MAX_FILE_BYTES and not info.filename.endswith(
        f"/{MANIFEST_NAME}"
    ):
        raise ReleaseError(f"member reaches 50 MiB boundary: {info.filename}")


def _relative_member_name(member: str) -> str:
    if "\\" in member or member.startswith("/"):
        raise ReleaseError(f"unsafe ZIP member path: {member!r}")
    path = PurePosixPath(member)
    if (
        path.as_posix() != member
        or len(path.parts) < 2
        or path.parts[0] != ARCHIVE_ROOT
    ):
        raise ReleaseError(f"ZIP member is outside the fixed archive root: {member!r}")
    relative = PurePosixPath(*path.parts[1:]).as_posix()
    if relative != MANIFEST_NAME:
        _validate_relative_path(relative)
    return relative


def verify_release(
    archive_path: str | Path,
    *,
    allow_draft: bool = False,
) -> dict[str, Any]:
    """Verify member inventory, metadata, manifest, hashes, and placeholders."""

    path = Path(archive_path)
    if path.is_symlink() or not path.is_file():
        raise ReleaseError(
            f"release archive is missing, non-regular, or linked: {path}"
        )
    try:
        with zipfile.ZipFile(path, mode="r") as archive:
            if archive.comment:
                raise ReleaseError("archive comment must be empty")
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise ReleaseError("archive contains duplicate member names")
            if names != sorted(names):
                raise ReleaseError("archive members are not in ascending POSIX order")
            relatives: list[str] = []
            for info in infos:
                relatives.append(_relative_member_name(info.filename))
                _validate_zip_metadata(info)
            expected = sorted((*PAYLOAD_PATHS, MANIFEST_NAME))
            if sorted(relatives) != expected:
                extras = sorted(set(relatives) - set(expected))
                missing = sorted(set(expected) - set(relatives))
                raise ReleaseError(
                    f"archive inventory differs from allowlist: extras={extras}, missing={missing}"
                )

            manifest_bytes = archive.read(_member_name(MANIFEST_NAME))
            manifest = _strict_json(manifest_bytes, label=MANIFEST_NAME)
            if not isinstance(manifest, dict) or set(manifest) != {
                "archive_root",
                "determinism",
                "draft_placeholders",
                "limits",
                "manifest_sha256",
                "payload",
                "schema",
            }:
                raise ReleaseError("release manifest keys differ")
            if (
                manifest.get("schema") != SCHEMA
                or manifest.get("archive_root") != ARCHIVE_ROOT
            ):
                raise ReleaseError("release manifest identity differs")
            if manifest.get("limits") != {
                "max_payload_file_bytes_exclusive": MAX_FILE_BYTES
            } or manifest.get("determinism") != {
                "compression": "stored",
                "member_mode": "0644",
                "member_timestamp": "1980-01-01T00:00:00",
                "path_order": "ascending-posix",
            }:
                raise ReleaseError(
                    "release manifest deterministic-build contract differs"
                )
            expected_self_hash = _sha256(
                _canonical_json_bytes(_manifest_without_hash(manifest))
            )
            if manifest.get("manifest_sha256") != expected_self_hash:
                raise ReleaseError("release manifest self-hash differs")

            descriptors = manifest.get("payload")
            if not isinstance(descriptors, list) or len(descriptors) != len(
                PAYLOAD_PATHS
            ):
                raise ReleaseError("release manifest payload count differs")
            descriptor_paths: list[str] = []
            payloads: dict[str, bytes] = {}
            for descriptor in descriptors:
                if not isinstance(descriptor, dict) or set(descriptor) != {
                    "bytes",
                    "path",
                    "sha256",
                }:
                    raise ReleaseError("release manifest payload descriptor differs")
                relative = descriptor.get("path")
                if not isinstance(relative, str):
                    raise ReleaseError("release manifest payload path is not a string")
                _validate_relative_path(relative)
                descriptor_paths.append(relative)
                data = archive.read(_member_name(relative))
                payloads[relative] = data
                if descriptor.get("bytes") != len(data) or descriptor.get(
                    "sha256"
                ) != _sha256(data):
                    raise ReleaseError(f"release payload hash/size differs: {relative}")
            if descriptor_paths != list(PAYLOAD_PATHS):
                raise ReleaseError(
                    "release manifest payload paths/order differ from allowlist"
                )

            findings = _draft_findings(payloads)
            if manifest.get("draft_placeholders") != findings:
                raise ReleaseError("release manifest placeholder inventory differs")
            if findings and not allow_draft:
                locations = ", ".join(sorted({item["path"] for item in findings}))
                raise ReleaseError(
                    "release still contains identity/repository placeholders in: "
                    + locations
                )
    except (OSError, zipfile.BadZipFile, KeyError, RuntimeError) as exc:
        if isinstance(exc, ReleaseError):
            raise
        raise ReleaseError(f"cannot verify release archive {path}: {exc}") from exc
    return manifest


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _summary(path: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "archive": str(path.resolve()),
        "archive_bytes": path.stat().st_size,
        "archive_sha256": _sha256_file(path),
        "draft_placeholders": manifest["draft_placeholders"],
        "manifest_sha256": manifest["manifest_sha256"],
        "payload_files": len(manifest["payload"]),
        "schema": manifest["schema"],
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="ZIP archive to build or verify")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root for building (default: parent of scripts/)",
    )
    parser.add_argument(
        "--verify", action="store_true", help="verify an existing archive; do not build"
    )
    parser.add_argument(
        "--allow-draft",
        action="store_true",
        help="permit the known identity/repository placeholders for isolated draft QA",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        manifest = (
            verify_release(args.archive, allow_draft=args.allow_draft)
            if args.verify
            else build_release(args.root, args.archive, allow_draft=args.allow_draft)
        )
        print(
            json.dumps(
                _summary(args.archive, manifest),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    except (OSError, ReleaseError) as exc:
        print(f"release error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
