#!/usr/bin/env python3
"""Safely finalize administrative metadata for the landmark submission.

This utility deliberately has a very small write boundary.  It updates exactly
three version-controlled text files and only the complete placeholder fragments
declared below.  Scientific inputs, raw results, analysis, and the compiled PDF
are never touched.

The command requires an explicit ``--dry-run`` or ``--apply``.  A dry run
performs all validation and computes the resulting hashes without writing.
An apply stages every replacement first, replaces each target atomically, and
restores all already-replaced targets if any later replacement fails.

The supplied revision must be the repository's current Git ``HEAD``, every
target must still equal its blob in that commit, and the repository URL must
match a configured Git remote.  This offline tool cannot prove that the remote
is publicly readable; the publication checklist therefore requires a separate
public-browser check.  Both report locations use the same canonical
``<repository>/commit/<sha>`` URL.

The write transaction rejects static links, path escapes, changed path
identities, and byte drift observed immediately before replacement.  Like a
portable pathname-based updater, it does not claim protection from a hostile
local process racing the final check and the operating system's atomic replace
syscall.  Run it only in a locally controlled checkout.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Mapping, Sequence
from urllib.parse import urlsplit


STUDENT_MARKER = "[STUDENT NAME AND ID REQUIRED]"
REPOSITORY_BLOCKER = "BLOCKER: INSERT PUBLIC REPOSITORY URL AND COMMIT SHA"
REPOSITORY_URL_MARKER = "<REPOSITORY_URL>"
COMMIT_SHA_MARKER = "<COMMIT_SHA>"

README_REFERENCE_PLACEHOLDER = "`<REPOSITORY_URL>@<COMMIT_SHA>`"
MAIN_AUTHOR_PLACEHOLDER = (
    r"\author{\textbf{[STUDENT NAME AND ID REQUIRED]}\\"
    "\n"
    r"        \textbf{[STUDENT NAME AND ID REQUIRED]}}"
)
REPRODUCIBILITY_REFERENCE_PLACEHOLDER = (
    r"\fbox{\parbox{0.92\columnwidth}{\centering"
    "\n"
    r"\textbf{BLOCKER: INSERT PUBLIC REPOSITORY URL AND COMMIT SHA}}}"
)
REPRODUCIBILITY_REFERENCE_CONTEXT = (
    "Exact experiment and figure-generation code, configuration, source manifests,\n"
    "frozen raw results, and analysis are preserved at:\n"
    "\n"
    r"\begin{center}"
    "\n" + REPRODUCIBILITY_REFERENCE_PLACEHOLDER + "\n" + r"\end{center}"
)

TARGET_RELATIVE_PATHS = (
    "README.md",
    "report/main.tex",
    "report/sections/10_reproducibility.tex",
)
ALL_MARKERS = (
    STUDENT_MARKER,
    REPOSITORY_BLOCKER,
    REPOSITORY_URL_MARKER,
    COMMIT_SHA_MARKER,
)
EXPECTED_MARKER_COUNTS = {
    "README.md": {
        STUDENT_MARKER: 0,
        REPOSITORY_BLOCKER: 0,
        REPOSITORY_URL_MARKER: 1,
        COMMIT_SHA_MARKER: 1,
    },
    "report/main.tex": {
        STUDENT_MARKER: 2,
        REPOSITORY_BLOCKER: 0,
        REPOSITORY_URL_MARKER: 0,
        COMMIT_SHA_MARKER: 0,
    },
    "report/sections/10_reproducibility.tex": {
        STUDENT_MARKER: 0,
        REPOSITORY_BLOCKER: 1,
        REPOSITORY_URL_MARKER: 0,
        COMMIT_SHA_MARKER: 0,
    },
}

_NAME_TOKEN = r"[A-Za-z]+(?:['-][A-Za-z]+)*\.?"
_ENGLISH_FULL_NAME_RE = re.compile(rf"{_NAME_TOKEN}(?: {_NAME_TOKEN})+")
_STUDENT_ID_RE = re.compile(r"[0-9]{5,12}")
_COMMIT_SHA_RE = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})")
_DNS_LABEL_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
_URL_PATH_RE = re.compile(r"/[A-Za-z0-9._~!$&'()*+,;=:@/-]+")
_PLACEHOLDER_NAMES = frozenset(
    {"anonymous student", "student name", "unknown student", "tbd student"}
)


class MetadataFinalizationError(RuntimeError):
    """Raised when validation or the all-target write transaction fails."""


@dataclass(frozen=True)
class Student:
    name: str
    student_id: str


@dataclass(frozen=True)
class SubmissionMetadata:
    student_one: Student
    student_two: Student
    repository_url: str
    scientific_commit_sha: str
    canonical_commit_url: str


@dataclass(frozen=True)
class FileChange:
    relative_path: str
    before_sha256: str
    after_sha256: str
    before_bytes: bytes
    after_bytes: bytes
    target_path: Path
    mode: int
    target_identity: tuple[int, int, int]
    directory_identities: tuple[tuple[str, str, int, int, int], ...]

    def public_record(self) -> dict[str, str]:
        return {
            "path": self.relative_path,
            "before_sha256": self.before_sha256,
            "after_sha256": self.after_sha256,
        }


@dataclass(frozen=True)
class FinalizationResult:
    mode: str
    repository_root: Path
    scientific_commit_sha: str
    canonical_commit_url: str
    changes: tuple[FileChange, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "mode": self.mode,
            "repository_root": str(self.repository_root),
            "scientific_commit_sha": self.scientific_commit_sha,
            "canonical_commit_url": self.canonical_commit_url,
            "files": [change.public_record() for change in self.changes],
        }


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _stat_identity(metadata: os.stat_result) -> tuple[int, int, int]:
    return (metadata.st_dev, metadata.st_ino, stat.S_IFMT(metadata.st_mode))


def _validate_student(name: str, student_id: str, label: str) -> Student:
    if name != name.strip():
        raise MetadataFinalizationError(f"{label} name has leading or trailing space")
    if not _ENGLISH_FULL_NAME_RE.fullmatch(name):
        raise MetadataFinalizationError(
            f"{label} name must be a full English name using ASCII letters, "
            "single spaces, apostrophes, hyphens, and optional periods"
        )
    if not 3 <= len(name) <= 100:
        raise MetadataFinalizationError(f"{label} name has an invalid length")
    if name.casefold() in _PLACEHOLDER_NAMES:
        raise MetadataFinalizationError(f"{label} name is still a placeholder")
    if student_id != student_id.strip() or not _STUDENT_ID_RE.fullmatch(student_id):
        raise MetadataFinalizationError(
            f"{label} ID must contain 5 to 12 ASCII decimal digits"
        )
    if set(student_id) == {"0"}:
        raise MetadataFinalizationError(f"{label} ID cannot be all zeros")
    return Student(name=name, student_id=student_id)


def _validate_scientific_commit_sha(value: str) -> str:
    if value != value.strip() or not _COMMIT_SHA_RE.fullmatch(value):
        raise MetadataFinalizationError(
            "scientific commit SHA must be exactly 40 or 64 hexadecimal characters"
        )
    canonical = value.lower()
    if set(canonical) == {"0"}:
        raise MetadataFinalizationError("scientific commit SHA cannot be all zeros")
    return canonical


def _validate_public_repository_url(value: str) -> str:
    if value != value.strip() or not value.isascii():
        raise MetadataFinalizationError(
            "repository URL must be an unpadded ASCII HTTPS URL"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise MetadataFinalizationError(
            "repository URL must not contain control characters"
        )
    if any(character in value for character in "{}\\"):
        raise MetadataFinalizationError(
            "repository URL must not contain braces or backslashes"
        )
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise MetadataFinalizationError(f"malformed repository URL: {exc}") from exc
    if parsed.scheme != "https":
        raise MetadataFinalizationError("repository URL must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise MetadataFinalizationError("repository URL must not contain credentials")
    if port is not None:
        raise MetadataFinalizationError(
            "repository URL must not contain an explicit port"
        )
    if parsed.query or parsed.fragment:
        raise MetadataFinalizationError(
            "repository URL must not contain a query or fragment"
        )
    hostname = parsed.hostname
    if hostname is None or hostname.endswith("."):
        raise MetadataFinalizationError(
            "repository URL must contain a canonical DNS host"
        )
    labels = hostname.split(".")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise MetadataFinalizationError(
            "repository URL must use a DNS hostname rather than an IP address"
        )
    if len(labels) < 2 or any(not _DNS_LABEL_RE.fullmatch(label) for label in labels):
        raise MetadataFinalizationError(
            "repository URL must use a public-looking DNS hostname"
        )
    if labels[-1].casefold() in {
        "example",
        "invalid",
        "local",
        "localhost",
        "internal",
        "test",
    }:
        raise MetadataFinalizationError("repository URL host is not public-looking")
    if not _URL_PATH_RE.fullmatch(parsed.path):
        raise MetadataFinalizationError(
            "repository URL must contain a nonempty, unescaped repository path"
        )
    if "//" in parsed.path or "\\" in parsed.path or "%" in parsed.path:
        raise MetadataFinalizationError("repository URL path is not canonical")

    path = parsed.path.rstrip("/")
    segments = path.removeprefix("/").split("/")
    if not segments or any(segment in {"", ".", ".."} for segment in segments):
        raise MetadataFinalizationError("repository URL path is not canonical")
    if segments[-1].endswith(".git"):
        segments[-1] = segments[-1][:-4]
        if not segments[-1]:
            raise MetadataFinalizationError("repository URL does not name a repository")
    for index, segment in enumerate(segments[:-1]):
        if segment.casefold() == "commit" and _COMMIT_SHA_RE.fullmatch(
            segments[index + 1]
        ):
            raise MetadataFinalizationError(
                "supply the repository base URL, not an existing commit URL"
            )
    if re.search(r"@[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$", segments[-1]):
        raise MetadataFinalizationError(
            "supply the repository base URL without an @commit suffix"
        )
    return f"https://{hostname.lower()}/{'/'.join(segments)}"


def _canonicalize_git_remote_url(value: str) -> str:
    """Convert a configured HTTPS or SSH Git remote to its HTTPS base URL."""

    candidate = value.strip()
    if candidate.lower().startswith("https://"):
        try:
            parsed_https = urlsplit(candidate)
            port = parsed_https.port
        except ValueError as exc:
            raise MetadataFinalizationError(f"malformed Git remote URL: {exc}") from exc
        if parsed_https.hostname is None or port is not None:
            raise MetadataFinalizationError(
                "configured HTTPS Git remote must use a canonical host without a port"
            )
        if parsed_https.query or parsed_https.fragment:
            raise MetadataFinalizationError(
                "configured Git remote URL is not canonical"
            )
        # Credentials in a local remote are irrelevant to the public code link
        # and must never be copied into the report.
        return _validate_public_repository_url(
            f"https://{parsed_https.hostname}{parsed_https.path}"
        )

    scp_match = None
    if "://" not in candidate:
        scp_match = re.fullmatch(
            r"(?:[A-Za-z0-9._-]+@)?"
            r"(?P<host>[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?):"
            r"(?P<path>[^\\\s:]+)",
            candidate,
        )
    if scp_match is not None:
        return _validate_public_repository_url(
            f"https://{scp_match.group('host')}/{scp_match.group('path')}"
        )

    try:
        parsed = urlsplit(candidate)
    except ValueError as exc:
        raise MetadataFinalizationError(f"malformed Git remote URL: {exc}") from exc
    if parsed.scheme not in {"ssh", "git+ssh"} or parsed.hostname is None:
        raise MetadataFinalizationError(
            "configured Git remote must use HTTPS or SSH repository syntax"
        )
    if parsed.password is not None or parsed.query or parsed.fragment:
        raise MetadataFinalizationError("configured Git remote URL is not canonical")
    try:
        port = parsed.port
    except ValueError as exc:
        raise MetadataFinalizationError(f"malformed Git remote URL: {exc}") from exc
    if port not in {None, 22}:
        raise MetadataFinalizationError(
            "configured Git remote may use only the standard SSH port 22"
        )
    return _validate_public_repository_url(f"https://{parsed.hostname}{parsed.path}")


def _run_git(
    repository_root: Path,
    arguments: Sequence[str],
    *,
    binary: bool = False,
) -> bytes | str:
    command = ["git", "-C", os.fspath(repository_root), *arguments]
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            env=environment,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise MetadataFinalizationError(f"cannot execute Git: {exc}") from exc
    if completed.returncode != 0:
        diagnostic = completed.stderr.decode("utf-8", errors="replace").strip()
        raise MetadataFinalizationError(
            "Git publication check failed" + (f": {diagnostic}" if diagnostic else "")
        )
    if binary:
        return completed.stdout
    try:
        return completed.stdout.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise MetadataFinalizationError("Git emitted non-UTF-8 metadata") from exc


def _validate_git_publication_context(
    repository_root: Path,
    metadata: SubmissionMetadata,
    *,
    expected_worktree_bytes: Mapping[str, bytes],
) -> None:
    """Bind metadata to local Commit A, its target blobs, and a configured remote."""

    top_level_text = _run_git(repository_root, ["rev-parse", "--show-toplevel"])
    assert isinstance(top_level_text, str)
    try:
        top_level = Path(top_level_text).resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise MetadataFinalizationError(
            "Git top-level path cannot be resolved"
        ) from exc
    if top_level != repository_root:
        raise MetadataFinalizationError(
            "repository root must be the exact Git worktree top level"
        )

    head_text = _run_git(repository_root, ["rev-parse", "--verify", "HEAD^{commit}"])
    assert isinstance(head_text, str)
    head = head_text.lower()
    if not _COMMIT_SHA_RE.fullmatch(head):
        raise MetadataFinalizationError(
            "Git HEAD is not a full SHA-1/SHA-256 commit ID"
        )
    if metadata.scientific_commit_sha != head:
        raise MetadataFinalizationError(
            "scientific commit SHA must equal the local Git HEAD (Commit A)"
        )

    if set(expected_worktree_bytes) != set(TARGET_RELATIVE_PATHS):
        raise MetadataFinalizationError("internal Git target inventory mismatch")

    remote_names_text = _run_git(repository_root, ["remote"])
    assert isinstance(remote_names_text, str)
    matching_remotes: list[str] = []
    for remote_name in remote_names_text.splitlines():
        if not remote_name:
            continue
        for push in (False, True):
            command = ["remote", "get-url", "--all"]
            if push:
                command.append("--push")
            command.append(remote_name)
            urls_text = _run_git(repository_root, command)
            assert isinstance(urls_text, str)
            for remote_url in urls_text.splitlines():
                try:
                    canonical = _canonicalize_git_remote_url(remote_url)
                except MetadataFinalizationError:
                    continue
                if canonical == metadata.repository_url:
                    matching_remotes.append(remote_name)
    if not matching_remotes:
        raise MetadataFinalizationError(
            "repository URL must exactly match a configured HTTPS/SSH Git remote"
        )

    for relative_path in TARGET_RELATIVE_PATHS:
        target = _safe_target_path(repository_root, relative_path)
        working_bytes = target.read_bytes()
        committed = _run_git(
            repository_root,
            ["cat-file", "blob", f"{head}:{relative_path}"],
            binary=True,
        )
        assert isinstance(committed, bytes)
        if expected_worktree_bytes[relative_path] != committed:
            raise MetadataFinalizationError(
                f"captured bytes for {relative_path} do not equal Commit A"
            )
        if working_bytes != committed:
            raise MetadataFinalizationError(
                f"{relative_path} does not equal its blob in Commit A"
            )


def validate_metadata(
    *,
    student_one_name: str,
    student_one_id: str,
    student_two_name: str,
    student_two_id: str,
    repository_url: str,
    scientific_commit_sha: str,
) -> SubmissionMetadata:
    """Validate user-supplied values and return their canonical representation."""

    student_one = _validate_student(student_one_name, student_one_id, "student one")
    student_two = _validate_student(student_two_name, student_two_id, "student two")
    if student_one.student_id == student_two.student_id:
        raise MetadataFinalizationError("the two student IDs must be distinct")
    base_url = _validate_public_repository_url(repository_url)
    commit_sha = _validate_scientific_commit_sha(scientific_commit_sha)
    commit_url = f"{base_url}/commit/{commit_sha}"
    return SubmissionMetadata(
        student_one=student_one,
        student_two=student_two,
        repository_url=base_url,
        scientific_commit_sha=commit_sha,
        canonical_commit_url=commit_url,
    )


def _safe_target_path(repository_root: Path, relative_path: str) -> Path:
    """Resolve an allowlisted target while rejecting escapes and all symlinks."""

    root = repository_root.resolve(strict=True)
    relative = PurePosixPath(relative_path)
    windows_relative = PureWindowsPath(relative_path)
    if (
        relative.is_absolute()
        or windows_relative.is_absolute()
        or bool(windows_relative.drive)
        or not relative.parts
        or ".." in relative.parts
        or ".." in windows_relative.parts
    ):
        raise MetadataFinalizationError(f"unsafe target path: {relative_path!r}")
    if any(part in {"", "."} for part in relative.parts):
        raise MetadataFinalizationError(f"non-canonical target path: {relative_path!r}")

    lexical = root
    for part in relative.parts:
        lexical = lexical / part
        if lexical.is_symlink():
            raise MetadataFinalizationError(
                f"refusing symbolic-link target component: {relative_path}"
            )
    try:
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(root)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise MetadataFinalizationError(
            f"target is missing or escapes repository root: {relative_path}"
        ) from exc
    if not resolved.is_file():
        raise MetadataFinalizationError(
            f"target is not a regular file: {relative_path}"
        )
    return resolved


def _read_text_target(path: Path, relative_path: str) -> tuple[bytes, str, int]:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        mode = stat.S_IMODE(path.stat().st_mode)
    except (OSError, UnicodeError) as exc:
        raise MetadataFinalizationError(f"cannot read {relative_path}: {exc}") from exc
    if raw.startswith(b"\xef\xbb\xbf"):
        raise MetadataFinalizationError(f"UTF-8 BOM is not allowed in {relative_path}")
    return raw, text, mode


def _capture_directory_identities(
    repository_root: Path, relative_path: str
) -> tuple[tuple[str, str, int, int, int], ...]:
    relative = PurePosixPath(relative_path)
    current = repository_root
    identities: list[tuple[str, str, int, int, int]] = []
    directory_parts = (".", *relative.parts[:-1])
    for index, part in enumerate(directory_parts):
        if index > 0:
            current = current / part
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise MetadataFinalizationError(
                f"cannot inspect directory chain for {relative_path}: {exc}"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise MetadataFinalizationError(
                f"unsafe directory component for {relative_path}: {current}"
            )
        identity = _stat_identity(metadata)
        label = "." if index == 0 else "/".join(relative.parts[:index])
        try:
            resolved = current.resolve(strict=True)
            resolved.relative_to(repository_root)
        except (OSError, ValueError) as exc:
            raise MetadataFinalizationError(
                f"directory component escapes repository for {relative_path}: {current}"
            ) from exc
        identities.append((label, os.fspath(resolved), *identity))
    return tuple(identities)


def _assert_change_preconditions(
    repository_root: Path,
    change: FileChange,
    *,
    expected_bytes: bytes,
    expected_identity: tuple[int, int, int] | None,
) -> None:
    target = _safe_target_path(repository_root, change.relative_path)
    if target != change.target_path:
        raise MetadataFinalizationError(
            f"target path identity changed for {change.relative_path}"
        )
    current_directories = _capture_directory_identities(
        repository_root, change.relative_path
    )
    if current_directories != change.directory_identities:
        raise MetadataFinalizationError(
            f"parent directory identity changed for {change.relative_path}"
        )
    try:
        target_identity = _stat_identity(target.lstat())
        current_bytes = target.read_bytes()
    except OSError as exc:
        raise MetadataFinalizationError(
            f"cannot revalidate {change.relative_path}: {exc}"
        ) from exc
    if expected_identity is not None and target_identity != expected_identity:
        raise MetadataFinalizationError(
            f"file identity changed for {change.relative_path}"
        )
    if current_bytes != expected_bytes:
        raise MetadataFinalizationError(
            f"concurrent modification detected in {change.relative_path}"
        )


def _require_exact_inventory(texts: dict[str, str]) -> None:
    if set(texts) != set(TARGET_RELATIVE_PATHS):
        raise MetadataFinalizationError("internal target inventory mismatch")
    for relative_path, text in texts.items():
        for marker in ALL_MARKERS:
            actual = text.count(marker)
            expected = EXPECTED_MARKER_COUNTS[relative_path][marker]
            if actual != expected:
                raise MetadataFinalizationError(
                    f"placeholder inventory mismatch in {relative_path}: "
                    f"{marker!r} occurs {actual} time(s), expected {expected}"
                )

    full_fragments = {
        "README.md": README_REFERENCE_PLACEHOLDER,
        "report/main.tex": MAIN_AUTHOR_PLACEHOLDER,
        "report/sections/10_reproducibility.tex": REPRODUCIBILITY_REFERENCE_CONTEXT,
    }
    for relative_path, fragment in full_fragments.items():
        count = texts[relative_path].count(fragment)
        if count != 1:
            raise MetadataFinalizationError(
                f"exact placeholder fragment mismatch in {relative_path}: "
                f"found {count}, expected 1"
            )


def _replacement_texts(metadata: SubmissionMetadata) -> dict[str, tuple[str, str]]:
    author = (
        r"\author{\textbf{"
        + metadata.student_one.name
        + " (ID: "
        + metadata.student_one.student_id
        + r")}\\"
        "\n"
        r"        \textbf{"
        + metadata.student_two.name
        + " (ID: "
        + metadata.student_two.student_id
        + ")}}"
    )
    reproducibility_reference = (
        r"\fbox{\parbox{0.92\columnwidth}{\centering"
        "\n"
        r"\textbf{Experiment and figure-generation code:}\\"
        + "\\url{"
        + metadata.canonical_commit_url
        + "}}}"
    )
    return {
        "README.md": (
            README_REFERENCE_PLACEHOLDER,
            f"`{metadata.canonical_commit_url}`",
        ),
        "report/main.tex": (MAIN_AUTHOR_PLACEHOLDER, author),
        "report/sections/10_reproducibility.tex": (
            REPRODUCIBILITY_REFERENCE_PLACEHOLDER,
            reproducibility_reference,
        ),
    }


def _prepare_changes(
    repository_root: Path, metadata: SubmissionMetadata
) -> tuple[FileChange, ...]:
    raw_by_path: dict[str, bytes] = {}
    text_by_path: dict[str, str] = {}
    mode_by_path: dict[str, int] = {}
    target_by_path: dict[str, Path] = {}
    for relative_path in TARGET_RELATIVE_PATHS:
        target = _safe_target_path(repository_root, relative_path)
        raw, text, mode = _read_text_target(target, relative_path)
        raw_by_path[relative_path] = raw
        text_by_path[relative_path] = text
        mode_by_path[relative_path] = mode
        target_by_path[relative_path] = target

    _require_exact_inventory(text_by_path)
    replacements = _replacement_texts(metadata)
    changes: list[FileChange] = []
    resulting_texts: dict[str, str] = {}
    for relative_path in TARGET_RELATIVE_PATHS:
        old_fragment, new_fragment = replacements[relative_path]
        before_text = text_by_path[relative_path]
        after_text = before_text.replace(old_fragment, new_fragment, 1)
        resulting_texts[relative_path] = after_text
        after_bytes = after_text.encode("utf-8")
        changes.append(
            FileChange(
                relative_path=relative_path,
                before_sha256=_sha256(raw_by_path[relative_path]),
                after_sha256=_sha256(after_bytes),
                before_bytes=raw_by_path[relative_path],
                after_bytes=after_bytes,
                target_path=target_by_path[relative_path],
                mode=mode_by_path[relative_path],
                target_identity=_stat_identity(target_by_path[relative_path].lstat()),
                directory_identities=_capture_directory_identities(
                    repository_root, relative_path
                ),
            )
        )

    for relative_path, text in resulting_texts.items():
        for marker in ALL_MARKERS:
            if marker in text:
                raise MetadataFinalizationError(
                    f"replacement left marker {marker!r} in {relative_path}"
                )
    return tuple(changes)


def _write_private_temp(
    target: Path,
    payload: bytes,
    mode: int,
    role: str,
    *,
    temporary_directory: Path,
) -> Path:
    descriptor = -1
    temporary_name = ""
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.metadata-{role}-",
            suffix=".tmp",
            dir=temporary_directory,
        )
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_name, mode)
        return Path(temporary_name)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)
        raise


def _atomic_replace(source: Path, destination: Path) -> None:
    """Test seam around the platform's same-filesystem atomic replacement."""

    os.replace(source, destination)


def _before_replace(change: FileChange) -> None:
    """Test seam invoked before the final identity/byte revalidation."""

    del change


def _assert_private_temp(
    repository_root: Path, temporary: Path, expected_bytes: bytes
) -> tuple[int, int, int]:
    """Validate a staged source immediately before it is consumed by replace."""

    try:
        if temporary.parent.resolve(strict=True) != repository_root:
            raise MetadataFinalizationError(
                f"temporary file escaped repository root: {temporary.name}"
            )
        metadata = temporary.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise MetadataFinalizationError(
                f"temporary path is not a regular file: {temporary.name}"
            )
        current_bytes = temporary.read_bytes()
    except OSError as exc:
        raise MetadataFinalizationError(
            f"cannot validate temporary file {temporary.name}: {exc}"
        ) from exc
    if current_bytes != expected_bytes:
        raise MetadataFinalizationError(
            f"temporary file content changed: {temporary.name}"
        )
    return _stat_identity(metadata)


def _assert_git_head(repository_root: Path, expected_sha: str) -> None:
    head_text = _run_git(repository_root, ["rev-parse", "--verify", "HEAD^{commit}"])
    assert isinstance(head_text, str)
    if head_text.lower() != expected_sha:
        raise MetadataFinalizationError(
            "local Git HEAD changed after Commit A validation"
        )


def _commit_changes(
    repository_root: Path,
    changes: tuple[FileChange, ...],
    *,
    scientific_commit_sha: str,
) -> None:
    staged: dict[str, Path] = {}
    backups: dict[str, Path] = {}
    installed_identities: dict[str, tuple[int, int, int]] = {}
    replaced: list[FileChange] = []
    try:
        for change in changes:
            _assert_change_preconditions(
                repository_root,
                change,
                expected_bytes=change.before_bytes,
                expected_identity=change.target_identity,
            )
            if change.target_path.parent.stat().st_dev != repository_root.stat().st_dev:
                raise MetadataFinalizationError(
                    f"target is on a different filesystem: {change.relative_path}"
                )
            backups[change.relative_path] = _write_private_temp(
                change.target_path,
                change.before_bytes,
                change.mode,
                "backup",
                temporary_directory=repository_root,
            )
            staged[change.relative_path] = _write_private_temp(
                change.target_path,
                change.after_bytes,
                change.mode,
                "staged",
                temporary_directory=repository_root,
            )

        for change in changes:
            _before_replace(change)
            _assert_git_head(repository_root, scientific_commit_sha)
            _assert_change_preconditions(
                repository_root,
                change,
                expected_bytes=change.before_bytes,
                expected_identity=change.target_identity,
            )
            staged_identity = _assert_private_temp(
                repository_root,
                staged[change.relative_path],
                change.after_bytes,
            )
            _atomic_replace(staged[change.relative_path], change.target_path)
            replaced.append(change)
            _assert_change_preconditions(
                repository_root,
                change,
                expected_bytes=change.after_bytes,
                expected_identity=staged_identity,
            )
            installed_identities[change.relative_path] = staged_identity
            _assert_git_head(repository_root, scientific_commit_sha)
    except BaseException as exc:
        rollback_errors: list[str] = []
        for change in reversed(replaced):
            backup = backups.get(change.relative_path)
            try:
                if backup is None or not backup.is_file():
                    raise OSError("rollback backup is unavailable")
                _assert_change_preconditions(
                    repository_root,
                    change,
                    expected_bytes=change.after_bytes,
                    expected_identity=installed_identities.get(change.relative_path),
                )
                backup_identity = _assert_private_temp(
                    repository_root, backup, change.before_bytes
                )
                _atomic_replace(backup, change.target_path)
                _assert_change_preconditions(
                    repository_root,
                    change,
                    expected_bytes=change.before_bytes,
                    expected_identity=backup_identity,
                )
            except BaseException as rollback_exc:
                rollback_errors.append(f"{change.relative_path}: {rollback_exc}")
        if rollback_errors:
            raise MetadataFinalizationError(
                "metadata update failed and rollback was incomplete: "
                + "; ".join(rollback_errors)
            ) from exc
        if isinstance(exc, MetadataFinalizationError):
            raise
        raise MetadataFinalizationError(
            f"metadata update failed; all replaced targets were rolled back: {exc}"
        ) from exc
    finally:
        for temporary in (*staged.values(), *backups.values()):
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


class _ExclusiveLock:
    def __init__(self, repository_root: Path) -> None:
        self.path = repository_root / ".finalize-progressive-landmarks-metadata.lock"
        self.descriptor: int | None = None

    def __enter__(self) -> "_ExclusiveLock":
        if self.path.is_symlink():
            raise MetadataFinalizationError("metadata lock path is a symbolic link")
        try:
            self.descriptor = os.open(
                self.path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            os.write(self.descriptor, f"pid={os.getpid()}\n".encode("ascii"))
            os.fsync(self.descriptor)
        except FileExistsError as exc:
            raise MetadataFinalizationError(
                f"metadata finalization is locked: {self.path}"
            ) from exc
        except BaseException:
            if self.descriptor is not None:
                os.close(self.descriptor)
                self.descriptor = None
            self.path.unlink(missing_ok=True)
            raise
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def finalize_metadata(
    repository_root: Path,
    metadata: SubmissionMetadata,
    *,
    dry_run: bool,
) -> FinalizationResult:
    """Validate the exact inventory and optionally apply all three updates."""

    try:
        root = repository_root.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise MetadataFinalizationError(
            f"repository root does not exist: {repository_root}"
        ) from exc
    if not root.is_dir():
        raise MetadataFinalizationError(f"repository root is not a directory: {root}")

    if dry_run:
        changes = _prepare_changes(root, metadata)
        _validate_git_publication_context(
            root,
            metadata,
            expected_worktree_bytes={
                change.relative_path: change.before_bytes for change in changes
            },
        )
        for change in changes:
            _assert_change_preconditions(
                root,
                change,
                expected_bytes=change.before_bytes,
                expected_identity=change.target_identity,
            )
        mode = "dry-run"
    else:
        with _ExclusiveLock(root):
            changes = _prepare_changes(root, metadata)
            _validate_git_publication_context(
                root,
                metadata,
                expected_worktree_bytes={
                    change.relative_path: change.before_bytes for change in changes
                },
            )
            _commit_changes(
                root,
                changes,
                scientific_commit_sha=metadata.scientific_commit_sha,
            )
        mode = "applied"
    return FinalizationResult(
        mode=mode,
        repository_root=root,
        scientific_commit_sha=metadata.scientific_commit_sha,
        canonical_commit_url=metadata.canonical_commit_url,
        changes=changes,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Finalize the two student identities and one immutable repository "
            "revision in the progressive-landmarks submission sources."
        ),
        epilog=(
            "Offline checks bind the revision to local HEAD, the draft files to "
            "their HEAD blobs, and the HTTPS base to a configured Git remote. "
            "Independently verify that the commit is reachable in a public browser. "
            "Hostile local races in the final check-to-replace instant are outside "
            "this tool's threat model."
        ),
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path.cwd(),
        help="project root containing README.md and report/ (default: cwd)",
    )
    parser.add_argument("--student-one-name", required=True)
    parser.add_argument("--student-one-id", required=True)
    parser.add_argument("--student-two-name", required=True)
    parser.add_argument("--student-two-id", required=True)
    parser.add_argument(
        "--repository-url",
        required=True,
        help=(
            "public HTTPS repository base URL matching a configured fetch/push "
            "Git remote, without a page or commit suffix"
        ),
    )
    parser.add_argument(
        "--scientific-commit-sha",
        required=True,
        help="full 40- or 64-hex Commit-A ID; must equal local HEAD",
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--dry-run",
        action="store_true",
        help="validate committed HEAD blobs and configured remote without writing",
    )
    action.add_argument(
        "--apply", action="store_true", help="apply the validated transaction"
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        metadata = validate_metadata(
            student_one_name=args.student_one_name,
            student_one_id=args.student_one_id,
            student_two_name=args.student_two_name,
            student_two_id=args.student_two_id,
            repository_url=args.repository_url,
            scientific_commit_sha=args.scientific_commit_sha,
        )
        result = finalize_metadata(
            args.repository_root,
            metadata,
            dry_run=args.dry_run,
        )
    except MetadataFinalizationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    payload = result.as_dict()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"mode: {result.mode}")
        print(f"immutable revision: {result.canonical_commit_url}")
        for change in result.changes:
            print(
                f"{change.relative_path}: "
                f"{change.before_sha256} -> {change.after_sha256}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
