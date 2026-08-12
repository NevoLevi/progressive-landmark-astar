from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "finalize_progressive_landmarks_metadata.py"
SPEC = importlib.util.spec_from_file_location("metadata_finalizer", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
finalizer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = finalizer
SPEC.loader.exec_module(finalizer)

SHA40 = "abcdefabcdefabcdefabcdefabcdefabcdefabcd"
SHA64 = "abcdef0123456789" * 4
REMOTE = "https://github.com/example/progressive-landmarks"


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", os.fspath(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _commit_placeholder_tree(
    root: Path,
    *,
    remote: str = REMOTE,
    object_format: str | None = None,
) -> tuple[dict[str, bytes], str]:
    original = _write_placeholder_tree(root)
    init_arguments = ["init", "-q"]
    if object_format is not None:
        init_arguments.append(f"--object-format={object_format}")
    _git(root, *init_arguments)
    _git(root, "config", "user.name", "Metadata Test")
    _git(root, "config", "user.email", "metadata@example.invalid")
    _git(root, "add", *finalizer.TARGET_RELATIVE_PATHS)
    _git(root, "commit", "-q", "-m", "Scientific artifact")
    _git(root, "remote", "add", "origin", remote)
    return original, _git(root, "rev-parse", "HEAD")


def _write_placeholder_tree(root: Path) -> dict[str, bytes]:
    contents = {
        "README.md": (
            "# Artifact\n\nImmutable revision:\n\n"
            f"{finalizer.README_REFERENCE_PLACEHOLDER}\n"
        ),
        "report/main.tex": (
            "\\documentclass{article}\n"
            f"{finalizer.MAIN_AUTHOR_PLACEHOLDER}\n"
            "\\begin{document}\n\\end{document}\n"
        ),
        "report/sections/10_reproducibility.tex": (
            "\\section{Reproducibility and Provenance}\n"
            "\\label{sec:reproducibility}\n\n"
            f"{finalizer.REPRODUCIBILITY_REFERENCE_CONTEXT}\n"
        ),
    }
    for relative_path, text in contents.items():
        path = root / Path(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    return {
        relative_path: (root / Path(relative_path)).read_bytes()
        for relative_path in contents
    }


def _metadata(
    *,
    repository_url: str = "https://github.com/example/progressive-landmarks",
    scientific_commit_sha: str = SHA40,
):
    return finalizer.validate_metadata(
        student_one_name="Alice Example",
        student_one_id="123456789",
        student_two_name="Bob O'Connor",
        student_two_id="987654321",
        repository_url=repository_url,
        scientific_commit_sha=scientific_commit_sha,
    )


def _committed_metadata(root: Path, *, repository_url: str = REMOTE):
    return _metadata(
        repository_url=repository_url,
        scientific_commit_sha=_git(root, "rev-parse", "HEAD"),
    )


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        relative_path: (root / Path(relative_path)).read_bytes()
        for relative_path in finalizer.TARGET_RELATIVE_PATHS
    }


def test_dry_run_validates_and_changes_nothing(tmp_path: Path) -> None:
    remote = "git@Codeberg.org:team/project.git"
    original, head = _commit_placeholder_tree(tmp_path, remote=remote)
    metadata = _metadata(
        repository_url="https://Codeberg.org/team/project.git/",
        scientific_commit_sha=head.upper(),
    )

    result = finalizer.finalize_metadata(tmp_path, metadata, dry_run=True)

    assert result.mode == "dry-run"
    assert result.scientific_commit_sha == head
    assert result.canonical_commit_url == (
        f"https://codeberg.org/team/project/commit/{head}"
    )
    assert _snapshot(tmp_path) == original
    assert all(change.before_sha256 != change.after_sha256 for change in result.changes)
    assert not list(tmp_path.rglob("*.tmp"))


def test_actual_administrative_values_pass_in_committed_matching_repo(
    tmp_path: Path,
) -> None:
    repository_url = "https://github.com/NevoLevi/progressive-landmark-astar"
    original, head = _commit_placeholder_tree(tmp_path, remote=repository_url + ".git")
    metadata = finalizer.validate_metadata(
        student_one_name="Nevo Levi",
        student_one_id="207350646",
        student_two_name="Dvir Chitrit",
        student_two_id="206766818",
        repository_url=repository_url,
        scientific_commit_sha=head,
    )

    result = finalizer.finalize_metadata(tmp_path, metadata, dry_run=True)

    assert result.mode == "dry-run"
    assert result.canonical_commit_url == f"{repository_url}/commit/{head}"
    assert _snapshot(tmp_path) == original
    resulting = {
        change.relative_path: change.after_bytes.decode("utf-8")
        for change in result.changes
    }
    assert "Nevo Levi (ID: 207350646)" in resulting["report/main.tex"]
    assert "Dvir Chitrit (ID: 206766818)" in resulting["report/main.tex"]


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        (
            "https://github.com/owner/repository.git/",
            "https://github.com/owner/repository",
        ),
        (
            "https://token:secret@github.com/owner/repository.git",
            "https://github.com/owner/repository",
        ),
        ("git@github.com:owner/repository.git", "https://github.com/owner/repository"),
        (
            "ssh://git@github.com/owner/repository.git",
            "https://github.com/owner/repository",
        ),
        (
            "git+ssh://git@github.com:22/owner/repository.git",
            "https://github.com/owner/repository",
        ),
        (
            "git@gitlab.com:group/subgroup/repository.git",
            "https://gitlab.com/group/subgroup/repository",
        ),
    ],
)
def test_common_git_remote_syntaxes_are_canonicalized(
    remote: str, expected: str
) -> None:
    assert finalizer._canonicalize_git_remote_url(remote) == expected


def test_nonstandard_ssh_remote_port_is_rejected() -> None:
    with pytest.raises(finalizer.MetadataFinalizationError, match="port 22"):
        finalizer._canonicalize_git_remote_url(
            "ssh://git@github.com:2222/owner/repository.git"
        )


def test_matching_push_remote_is_accepted(tmp_path: Path) -> None:
    public_url = "https://github.com/example/progressive-landmarks"
    original, head = _commit_placeholder_tree(
        tmp_path, remote="git@github.com:mirror/read-only.git"
    )
    _git(tmp_path, "remote", "set-url", "--push", "origin", public_url + ".git")
    metadata = _metadata(repository_url=public_url, scientific_commit_sha=head)

    result = finalizer.finalize_metadata(tmp_path, metadata, dry_run=True)

    assert result.mode == "dry-run"
    assert _snapshot(tmp_path) == original


def test_sha256_git_commit_is_supported_when_git_supports_it(tmp_path: Path) -> None:
    try:
        original, head = _commit_placeholder_tree(tmp_path, object_format="sha256")
    except subprocess.CalledProcessError as exc:
        pytest.skip(f"installed Git cannot create SHA-256 repositories: {exc}")
    assert len(head) == 64

    result = finalizer.finalize_metadata(
        tmp_path,
        _metadata(scientific_commit_sha=head),
        dry_run=True,
    )

    assert result.scientific_commit_sha == head
    assert _snapshot(tmp_path) == original


def test_apply_replaces_only_exact_fragments_with_one_canonical_url(
    tmp_path: Path,
) -> None:
    original, head = _commit_placeholder_tree(tmp_path)
    metadata = _committed_metadata(tmp_path)

    result = finalizer.finalize_metadata(tmp_path, metadata, dry_run=False)

    assert result.mode == "applied"
    commit_url = f"{REMOTE}/commit/{head}"
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    main = (tmp_path / "report/main.tex").read_text(encoding="utf-8")
    reproducibility = (tmp_path / "report/sections/10_reproducibility.tex").read_text(
        encoding="utf-8"
    )
    assert f"`{commit_url}`" in readme
    assert r"\textbf{Alice Example (ID: 123456789)}" in main
    assert r"\textbf{Bob O'Connor (ID: 987654321)}" in main
    assert r"\textbf{Experiment and figure-generation code:}\\" in reproducibility
    assert rf"\url{{{commit_url}}}" in reproducibility
    assert reproducibility.count(commit_url) == 1
    assert readme.count(commit_url) == 1
    combined = readme + main + reproducibility
    assert all(marker not in combined for marker in finalizer.ALL_MARKERS)
    assert len(result.changes) == 3
    assert _snapshot(tmp_path) != original
    assert not (tmp_path / ".finalize-progressive-landmarks-metadata.lock").exists()
    assert not list(tmp_path.rglob("*.tmp"))


def test_already_finalized_tree_is_rejected_without_writes(tmp_path: Path) -> None:
    _commit_placeholder_tree(tmp_path)
    metadata = _committed_metadata(tmp_path)
    finalizer.finalize_metadata(tmp_path, metadata, dry_run=False)
    finalized = _snapshot(tmp_path)

    with pytest.raises(finalizer.MetadataFinalizationError, match="inventory mismatch"):
        finalizer.finalize_metadata(tmp_path, metadata, dry_run=True)

    assert _snapshot(tmp_path) == finalized


@pytest.mark.parametrize(
    ("relative_path", "mutation"),
    [
        ("README.md", lambda text: text + "<COMMIT_SHA>\n"),
        (
            "report/main.tex",
            lambda text: text.replace(
                finalizer.MAIN_AUTHOR_PLACEHOLDER,
                finalizer.STUDENT_MARKER + "\n" + finalizer.STUDENT_MARKER,
            ),
        ),
        (
            "report/sections/10_reproducibility.tex",
            lambda text: text.replace(r"\centering", r"\centering{}"),
        ),
    ],
)
def test_any_inventory_or_full_fragment_drift_fails_closed(
    tmp_path: Path, relative_path: str, mutation
) -> None:
    _commit_placeholder_tree(tmp_path)
    path = tmp_path / Path(relative_path)
    path.write_text(mutation(path.read_text(encoding="utf-8")), encoding="utf-8")
    _git(tmp_path, "add", relative_path)
    _git(tmp_path, "commit", "-q", "-m", "Commit malformed placeholders")
    before = _snapshot(tmp_path)

    with pytest.raises(finalizer.MetadataFinalizationError):
        finalizer.finalize_metadata(
            tmp_path, _committed_metadata(tmp_path), dry_run=False
        )

    assert _snapshot(tmp_path) == before
    assert not (tmp_path / ".finalize-progressive-landmarks-metadata.lock").exists()


@pytest.mark.parametrize(
    "repository_url",
    [
        "http://github.com/example/project",
        "https://user:secret@github.com/example/project",
        "https://github.com:8443/example/project",
        "https://localhost/example/project",
        "https://127.0.0.1/example/project",
        "https://git.example.local/example/project",
        "https://github.com/example/project?tab=readme",
        "https://github.com/example/project#readme",
        "https://github.com/example/%2e%2e/project",
        "https://github.com/example/proj{ect}",
        "https://github.com/example/proj\\ect",
        "https://github.com/example/proj\nect",
        "https://github.com/example/project/commit/" + SHA40,
        "https://github.com/example/project@" + SHA40,
    ],
)
def test_nonpublic_or_nonbase_repository_urls_are_rejected(
    repository_url: str,
) -> None:
    with pytest.raises(finalizer.MetadataFinalizationError):
        _metadata(repository_url=repository_url)


@pytest.mark.parametrize(
    "scientific_commit_sha",
    ["abc", "g" * 40, "0" * 40, "a" * 39, "a" * 41, "a" * 63, "a" * 65],
)
def test_nonimmutable_commit_identifiers_are_rejected(
    scientific_commit_sha: str,
) -> None:
    with pytest.raises(finalizer.MetadataFinalizationError):
        _metadata(scientific_commit_sha=scientific_commit_sha)


@pytest.mark.parametrize(
    ("name", "student_id"),
    [
        ("Alice", "123456789"),
        (" Alice Example", "123456789"),
        ("Alice  Example", "123456789"),
        ("אליס דוגמה", "123456789"),
        ("Student Name", "123456789"),
        ("Alice Example", "12A456789"),
        ("Alice Example", "000000000"),
    ],
)
def test_invalid_identity_values_are_rejected(name: str, student_id: str) -> None:
    with pytest.raises(finalizer.MetadataFinalizationError):
        finalizer.validate_metadata(
            student_one_name=name,
            student_one_id=student_id,
            student_two_name="Bob Example",
            student_two_id="987654321",
            repository_url="https://github.com/example/project",
            scientific_commit_sha=SHA40,
        )


def test_duplicate_student_ids_are_rejected() -> None:
    with pytest.raises(finalizer.MetadataFinalizationError, match="distinct"):
        finalizer.validate_metadata(
            student_one_name="Alice Example",
            student_one_id="123456789",
            student_two_name="Bob Example",
            student_two_id="123456789",
            repository_url="https://github.com/example/project",
            scientific_commit_sha=SHA40,
        )


def test_path_escape_is_rejected(tmp_path: Path) -> None:
    _write_placeholder_tree(tmp_path)
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    with pytest.raises(finalizer.MetadataFinalizationError, match="unsafe target"):
        finalizer._safe_target_path(tmp_path, "../outside.txt")
    with pytest.raises(finalizer.MetadataFinalizationError, match="unsafe target"):
        finalizer._safe_target_path(tmp_path, outside.as_posix())
    with pytest.raises(finalizer.MetadataFinalizationError, match="unsafe target"):
        finalizer._safe_target_path(tmp_path, r"report\..\..\outside.txt")


def test_symbolic_link_target_is_rejected(tmp_path: Path) -> None:
    _commit_placeholder_tree(tmp_path)
    target = tmp_path / "README.md"
    outside = tmp_path.parent / f"{tmp_path.name}-outside.md"
    outside.write_text(finalizer.README_REFERENCE_PLACEHOLDER + "\n", encoding="utf-8")
    target.unlink()
    try:
        target.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symbolic links unavailable in this environment: {exc}")

    with pytest.raises(finalizer.MetadataFinalizationError, match="symbolic-link"):
        finalizer.finalize_metadata(
            tmp_path, _committed_metadata(tmp_path), dry_run=True
        )


def test_mid_transaction_failure_rolls_back_every_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original, _ = _commit_placeholder_tree(tmp_path)
    real_replace = finalizer._atomic_replace
    calls = 0

    def fail_second_replace(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected replacement failure")
        real_replace(source, destination)

    monkeypatch.setattr(finalizer, "_atomic_replace", fail_second_replace)

    with pytest.raises(finalizer.MetadataFinalizationError, match="rolled back"):
        finalizer.finalize_metadata(
            tmp_path, _committed_metadata(tmp_path), dry_run=False
        )

    assert _snapshot(tmp_path) == original
    assert not (tmp_path / ".finalize-progressive-landmarks-metadata.lock").exists()
    assert not list(tmp_path.rglob("*.tmp"))


def test_git_head_remote_and_committed_blob_are_mandatory(tmp_path: Path) -> None:
    _commit_placeholder_tree(tmp_path)
    head = _git(tmp_path, "rev-parse", "HEAD")

    with pytest.raises(finalizer.MetadataFinalizationError, match="local Git HEAD"):
        finalizer.finalize_metadata(
            tmp_path,
            _metadata(scientific_commit_sha="1" * len(head)),
            dry_run=True,
        )

    with pytest.raises(finalizer.MetadataFinalizationError, match="configured"):
        finalizer.finalize_metadata(
            tmp_path,
            _metadata(
                repository_url="https://github.com/different/repository",
                scientific_commit_sha=head,
            ),
            dry_run=True,
        )

    # A browser page is structurally valid HTTPS, but it is not the configured
    # repository base and therefore fails at the authoritative remote binding.
    with pytest.raises(finalizer.MetadataFinalizationError, match="configured"):
        finalizer.finalize_metadata(
            tmp_path,
            _metadata(
                repository_url=f"{REMOTE}/tree/main",
                scientific_commit_sha=head,
            ),
            dry_run=True,
        )

    readme = tmp_path / "README.md"
    readme.write_bytes(readme.read_bytes() + b"uncommitted\n")
    with pytest.raises(finalizer.MetadataFinalizationError, match="Commit A"):
        finalizer.finalize_metadata(
            tmp_path, _committed_metadata(tmp_path), dry_run=True
        )


def test_non_git_directory_is_rejected(tmp_path: Path) -> None:
    _write_placeholder_tree(tmp_path)
    with pytest.raises(finalizer.MetadataFinalizationError, match="Git"):
        finalizer.finalize_metadata(tmp_path, _metadata(), dry_run=True)


def test_concurrent_byte_edit_before_final_replace_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original, _ = _commit_placeholder_tree(tmp_path)
    metadata = _committed_metadata(tmp_path)
    calls = 0

    def mutate_before_replace(change) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            change.target_path.write_bytes(b"concurrent edit\n")

    monkeypatch.setattr(finalizer, "_before_replace", mutate_before_replace)
    with pytest.raises(finalizer.MetadataFinalizationError, match="concurrent"):
        finalizer.finalize_metadata(tmp_path, metadata, dry_run=False)

    assert (tmp_path / "README.md").read_bytes() == b"concurrent edit\n"
    assert (tmp_path / "report/main.tex").read_bytes() == original["report/main.tex"]
    assert (
        tmp_path / "report/sections/10_reproducibility.tex"
    ).read_bytes() == original["report/sections/10_reproducibility.tex"]
    assert not list(tmp_path.rglob("*.tmp"))


def test_leaf_identity_substitution_before_replace_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original, _ = _commit_placeholder_tree(tmp_path)
    metadata = _committed_metadata(tmp_path)
    displaced = tmp_path / "README.displaced"
    calls = 0

    def substitute_leaf(change) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            change.target_path.replace(displaced)
            change.target_path.write_bytes(change.before_bytes)

    monkeypatch.setattr(finalizer, "_before_replace", substitute_leaf)

    with pytest.raises(finalizer.MetadataFinalizationError, match="file identity"):
        finalizer.finalize_metadata(tmp_path, metadata, dry_run=False)

    assert displaced.read_bytes() == original["README.md"]
    assert (tmp_path / "README.md").read_bytes() == original["README.md"]
    assert (tmp_path / "report/main.tex").read_bytes() == original["report/main.tex"]
    assert not list(tmp_path.glob("*.tmp"))
    assert not (tmp_path / ".finalize-progressive-landmarks-metadata.lock").exists()


def test_parent_identity_substitution_during_transaction_rolls_back_prior_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original, _ = _commit_placeholder_tree(tmp_path)
    metadata = _committed_metadata(tmp_path)
    displaced_report = tmp_path / "report.displaced"
    calls = 0

    def substitute_parent(change) -> None:
        nonlocal calls
        calls += 1
        if calls != 2:
            return
        report = tmp_path / "report"
        report.rename(displaced_report)
        report.mkdir()
        (report / "sections").mkdir()
        (report / "main.tex").write_bytes(original["report/main.tex"])
        (report / "sections/10_reproducibility.tex").write_bytes(
            original["report/sections/10_reproducibility.tex"]
        )

    monkeypatch.setattr(finalizer, "_before_replace", substitute_parent)

    with pytest.raises(finalizer.MetadataFinalizationError, match="directory identity"):
        finalizer.finalize_metadata(tmp_path, metadata, dry_run=False)

    assert (tmp_path / "README.md").read_bytes() == original["README.md"]
    assert (tmp_path / "report/main.tex").read_bytes() == original["report/main.tex"]
    assert (displaced_report / "main.tex").read_bytes() == original["report/main.tex"]
    assert not list(tmp_path.glob("*.tmp"))
    assert not (tmp_path / ".finalize-progressive-landmarks-metadata.lock").exists()


def test_parent_directory_identity_change_is_rejected(tmp_path: Path) -> None:
    _commit_placeholder_tree(tmp_path)
    metadata = _committed_metadata(tmp_path)
    changes = finalizer._prepare_changes(tmp_path.resolve(), metadata)
    main_change = next(
        change for change in changes if change.relative_path == "report/main.tex"
    )

    report = tmp_path / "report"
    moved = tmp_path / "report-original"
    report.rename(moved)
    report.mkdir()
    (report / "sections").mkdir()
    (report / "main.tex").write_bytes((moved / "main.tex").read_bytes())
    (report / "sections/10_reproducibility.tex").write_bytes(
        (moved / "sections/10_reproducibility.tex").read_bytes()
    )

    with pytest.raises(finalizer.MetadataFinalizationError, match="directory identity"):
        finalizer._assert_change_preconditions(
            tmp_path.resolve(),
            main_change,
            expected_bytes=main_change.before_bytes,
            expected_identity=main_change.target_identity,
        )


def test_cli_json_dry_run_is_nonmutating(tmp_path: Path, capsys) -> None:
    original, head = _commit_placeholder_tree(tmp_path)

    return_code = finalizer.main(
        [
            "--repository-root",
            os.fspath(tmp_path),
            "--student-one-name",
            "Alice Example",
            "--student-one-id",
            "123456789",
            "--student-two-name",
            "Bob Example",
            "--student-two-id",
            "987654321",
            "--repository-url",
            REMOTE,
            "--scientific-commit-sha",
            head,
            "--dry-run",
            "--json",
        ]
    )

    captured = capsys.readouterr()
    assert return_code == 0
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["mode"] == "dry-run"
    assert payload["scientific_commit_sha"] == head
    assert [item["path"] for item in payload["files"]] == list(
        finalizer.TARGET_RELATIVE_PATHS
    )
    assert "123456789" not in captured.out
    assert "987654321" not in captured.out
    assert _snapshot(tmp_path) == original
