"""Fail-closed, immutable experiment runner for progressive landmarks.

The protocol module owns corpus selection, while :mod:`progressive_landmarks.core`
owns search.  This module joins those two contracts without mutating either one:
it verifies the frozen plan, constructs one immutable landmark table per used
map, executes the exact rotated schedule, validates every result against an
independent BFS oracle, and publishes a content-addressed result directory in a
single rename.

Sealed evaluation is deliberately harder to launch than development.  It needs
an externally produced freeze manifest which points at, and revalidates, a
complete formal development run.  The development runner emits only a
``development_freeze_candidate.json``; that candidate is evidence for a later
analysis/freeze step and is never accepted as authorization.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import platform
import shutil
import sys
import tempfile
from time import perf_counter_ns
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from .core import (
    LandmarkTable,
    SearchResult,
    astar_search,
    bfs_shortest_path,
    build_landmark_table,
    read_moving_ai_map,
    validate_path,
)
from .protocol import (
    METHODS,
    canonical_json_bytes,
    canonical_json_sha256,
    verify_protocol,
)


RUN_SCHEMA = "progressive-landmarks-run-v2"
MAPS_SCHEMA = "progressive-landmarks-maps-v2"
QUERY_SCHEMA = "progressive-landmarks-query-result-v2"
MANIFEST_SCHEMA = "progressive-landmarks-result-manifest-v2"
CANDIDATE_SCHEMA = "progressive-landmarks-development-freeze-candidate-v2"
FREEZE_SCHEMA = "progressive-landmarks-sealed-evaluation-freeze-v2"
DEVELOPMENT_AUDIT_SCHEMA = "progressive-landmarks-development-audit-v2"
PLAN_SCHEMA = "progressive-landmarks-plan-v2"
PROTOCOL_ID = "progressive_landmarks_v2"
FORMAL_DEVELOPMENT_QUERIES = 160
FORMAL_EVALUATION_QUERIES = 800
WARMUP_REPETITIONS = 1
TIMED_REPETITIONS = 8
SEARCHES_PER_QUERY = len(METHODS) * (WARMUP_REPETITIONS + TIMED_REPETITIONS)
_SHA256_HEX = frozenset("0123456789abcdef")


class RunnerError(RuntimeError):
    """Raised when execution or immutable-result validation fails closed."""


@dataclass(frozen=True, slots=True)
class LoadedRun:
    """Read-only view of a hash-validated published result directory."""

    root: Path
    manifest: Mapping[str, Any]
    run: Mapping[str, Any]
    maps: Mapping[str, Any]
    queries: tuple[Mapping[str, Any], ...]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise RunnerError(f"duplicate JSON key: {key!r}")
        value[key] = item
    return value


def _reject_constant(token: str) -> None:
    raise RunnerError(f"non-finite JSON number: {token}")


def _strict_json_bytes(payload: bytes, *, label: str) -> Any:
    try:
        text = payload.decode("ascii")
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except RunnerError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RunnerError(f"cannot strictly decode {label}: {exc}") from exc


def _strict_json_file(path: Path) -> Any:
    try:
        return _strict_json_bytes(path.read_bytes(), label=str(path))
    except OSError as exc:
        raise RunnerError(f"cannot read {path}: {exc}") from exc


def _object(value: Any, *, label: str, keys: Iterable[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RunnerError(f"{label} must be an object")
    expected = set(keys)
    actual = set(value)
    if actual != expected:
        raise RunnerError(
            f"{label} keys differ: missing={sorted(expected - actual)}, "
            f"unexpected={sorted(actual - expected)}"
        )
    return value


def _plain_int(value: Any, *, label: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise RunnerError(f"{label} must be a plain integer >= {minimum}")
    return value


def _text(value: Any, *, label: str) -> str:
    if type(value) is not str or not value:
        raise RunnerError(f"{label} must be a non-empty string")
    return value


def _sha(value: Any, *, label: str) -> str:
    text = _text(value, label=label)
    if len(text) != 64 or any(character not in _SHA256_HEX for character in text):
        raise RunnerError(f"{label} must be a lowercase SHA-256 digest")
    return text


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise RunnerError(f"cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def _json_file_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("ascii")


def _jsonl_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(canonical_json_bytes(row) + b"\n" for row in rows)


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _write_new(path: Path, payload: bytes) -> None:
    try:
        with path.open("xb") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
    except OSError as exc:
        raise RunnerError(f"cannot persist {path}: {exc}") from exc


def _lexists(path: Path) -> bool:
    return os.path.lexists(os.fspath(path))


def _safe_relative_parts(
    value: Any,
    *,
    label: str,
    expected_name: str | None = None,
    minimum_parts: int = 1,
) -> tuple[str, ...]:
    """Validate a canonical relative path under both POSIX and Windows rules."""

    rendered = _text(value, label=label)
    posix = PurePosixPath(rendered)
    windows = PureWindowsPath(rendered)
    native = Path(rendered)
    if (
        rendered != posix.as_posix()
        or posix.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or bool(windows.root)
        or bool(native.drive)
        or bool(native.root)
        or ":" in rendered
        or "\\" in rendered
        or len(posix.parts) < minimum_parts
        or any(part in {"", ".", ".."} for part in posix.parts)
        or (expected_name is not None and posix.name != expected_name)
    ):
        raise RunnerError(f"{label} is not a safe canonical relative path")
    return posix.parts


def _resolve_safe_child(
    base: Path,
    relative: Any,
    *,
    label: str,
    expected_name: str | None = None,
    minimum_parts: int = 1,
    require_file: bool = True,
) -> Path:
    """Resolve an existing child and reject drive, traversal, and symlink escape."""

    base = base.resolve(strict=True)
    parts = _safe_relative_parts(
        relative,
        label=label,
        expected_name=expected_name,
        minimum_parts=minimum_parts,
    )
    unresolved = base.joinpath(*parts)
    cursor = base
    for part in parts:
        cursor /= part
        if cursor.is_symlink():
            raise RunnerError(f"{label} must not traverse a symbolic link")
    try:
        resolved = unresolved.resolve(strict=True)
        resolved.relative_to(base)
    except (OSError, ValueError) as exc:
        raise RunnerError(
            f"{label} does not resolve inside its authority directory"
        ) from exc
    if require_file and not resolved.is_file():
        raise RunnerError(f"{label} does not resolve to a regular file")
    if not require_file and not resolved.is_dir():
        raise RunnerError(f"{label} does not resolve to a directory")
    return resolved


def _relative_file_binding(path: Path, repository_root: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    try:
        rendered = resolved.relative_to(repository_root).as_posix()
    except ValueError:
        rendered = f"<external>/{resolved.name}"
    return {
        "path": rendered,
        "sha256": _sha256_file(resolved),
        "bytes": resolved.stat().st_size,
    }


def _validate_plan_hash(plan: Mapping[str, Any]) -> None:
    if plan.get("schema") != PLAN_SCHEMA or plan.get("protocol_id") != PROTOCOL_ID:
        raise RunnerError(
            "verified plan is not the frozen progressive-landmarks v2 plan"
        )
    expected = _sha(plan.get("plan_sha256"), label="plan.plan_sha256")
    core = {key: value for key, value in plan.items() if key != "plan_sha256"}
    observed = canonical_json_sha256(core)
    if observed != expected:
        raise RunnerError("verified plan content does not match plan_sha256")


def _code_bindings(repository_root: Path) -> dict[str, Any]:
    package_root = Path(__file__).resolve().parent
    paths = {
        "analysis": package_root / "analysis.py",
        "analysis_cli": repository_root
        / "scripts"
        / "analyze_progressive_landmarks.py",
        "cli": repository_root / "scripts" / "run_progressive_landmarks.py",
        "core": package_root / "core.py",
        "development_gate": package_root / "development_gate.py",
        "freeze_cli": repository_root
        / "scripts"
        / "freeze_progressive_landmarks_development.py",
        "package_init": package_root / "__init__.py",
        "protocol": package_root / "protocol.py",
        "runner": Path(__file__).resolve(),
    }
    files = {
        name: _relative_file_binding(path, repository_root)
        for name, path in sorted(paths.items())
    }
    return {"files": files, "sha256": canonical_json_sha256(files)}


def _environment_binding() -> dict[str, Any]:
    value = {
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "python_compiler": platform.python_compiler(),
        "python_executable": str(Path(sys.executable).resolve()),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "byteorder": sys.byteorder,
        "cpu_count": os.cpu_count(),
        "pythonhashseed": os.environ.get("PYTHONHASHSEED"),
    }
    return {"value": value, "sha256": canonical_json_sha256(value)}


def _split_input_bindings(
    plan: Mapping[str, Any], experiment_split: str
) -> list[dict[str, Any]]:
    bindings = plan.get("input_bindings")
    if not isinstance(bindings, list):
        raise RunnerError("plan.input_bindings must be an array")
    selected = [
        row
        for row in bindings
        if isinstance(row, dict) and row.get("experiment_split") == experiment_split
    ]
    expected_maps = 4 if experiment_split == "development" else 8
    if len(selected) != expected_maps:
        raise RunnerError(
            f"plan has {len(selected)} map bindings for {experiment_split}, "
            f"expected {expected_maps}"
        )
    return selected


def _source_binding(
    plan: Mapping[str, Any], split_bindings: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    value = {
        "snapshot": plan.get("source_binding"),
        "split_inputs": list(split_bindings),
    }
    return {"value": value, "sha256": canonical_json_sha256(value)}


def _current_bindings(
    plan: Mapping[str, Any], repository_root: Path, experiment_split: str
) -> dict[str, Any]:
    config_binding = plan.get("config_binding")
    if not isinstance(config_binding, dict):
        raise RunnerError("plan.config_binding must be an object")
    split_inputs = _split_input_bindings(plan, experiment_split)
    code = _code_bindings(repository_root)
    environment = _environment_binding()
    source = _source_binding(plan, split_inputs)
    return {
        "protocol_id": _text(plan.get("protocol_id"), label="plan.protocol_id"),
        "config": config_binding,
        "plan_sha256": _sha(plan.get("plan_sha256"), label="plan.plan_sha256"),
        "code": code,
        "source": source,
        "environment": environment,
    }


def _table_sha256(table: LandmarkTable) -> str:
    digest = hashlib.sha256(b"progressive-landmarks-table-v1\0")
    digest.update(table.grid.name.encode("utf-8"))
    digest.update(table.grid.width.to_bytes(8, "little"))
    digest.update(table.grid.height.to_bytes(8, "little"))
    digest.update(len(table.landmarks).to_bytes(8, "little"))
    for (x, y), packed in zip(table.landmarks, table.packed_distances, strict=True):
        digest.update(x.to_bytes(8, "little"))
        digest.update(y.to_bytes(8, "little"))
        digest.update(len(packed).to_bytes(8, "little"))
        digest.update(packed)
    return digest.hexdigest()


def _path_sha256(path: Sequence[tuple[int, int]]) -> str:
    return canonical_json_sha256([[x, y] for x, y in path])


def _search_payload(result: SearchResult) -> tuple[dict[str, Any], dict[str, int]]:
    statistics = asdict(result.stats)
    timing: dict[str, int] = {}
    deterministic_stats: dict[str, Any] = {}
    for field in fields(result.stats):
        value = statistics[field.name]
        if field.name.endswith("_ns"):
            timing[field.name] = _plain_int(
                value, label=f"search stats {field.name}", minimum=0
            )
        else:
            deterministic_stats[field.name] = value
    if "search_ns" not in timing:
        raise RunnerError("search result does not expose raw search_ns")
    deterministic = {
        "mode": result.mode,
        "found": result.found,
        "cost": result.cost,
        "path_sha256": _path_sha256(result.path),
        "expansion_digest": result.expansion_digest,
        "counters": deterministic_stats,
    }
    return deterministic, timing


def _validate_schedule(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    orders = plan.get("timing_orders")
    if not isinstance(orders, dict) or set(orders) != {"warmup", "timed"}:
        raise RunnerError("plan timing_orders has the wrong shape")
    combined: list[dict[str, Any]] = []
    for phase, expected_count, timed in (
        ("warmup", WARMUP_REPETITIONS, False),
        ("timed", TIMED_REPETITIONS, True),
    ):
        rows = orders[phase]
        if not isinstance(rows, list) or len(rows) != expected_count:
            raise RunnerError(
                f"plan must contain exactly {expected_count} {phase} rows"
            )
        for repetition, row in enumerate(rows):
            if not isinstance(row, dict) or set(row) != {
                "repetition",
                "timed",
                "methods",
            }:
                raise RunnerError(f"plan {phase} schedule row has the wrong shape")
            expected_methods = list(METHODS[repetition % len(METHODS) :]) + list(
                METHODS[: repetition % len(METHODS)]
            )
            if phase == "warmup":
                expected_methods = list(METHODS)
            if (
                row["repetition"] != repetition
                or row["timed"] is not timed
                or row["methods"] != expected_methods
            ):
                raise RunnerError(
                    f"plan {phase} rotation differs from the frozen schedule"
                )
            combined.append(
                {
                    "phase": phase,
                    "repetition": repetition,
                    "timed": timed,
                    "methods": expected_methods,
                }
            )
    return combined


def _selected_queries(
    plan: Mapping[str, Any],
    experiment_split: str,
    *,
    development_smoke: bool,
    max_queries: int | None,
) -> tuple[list[dict[str, Any]], bool]:
    if experiment_split not in {"development", "sealed_evaluation"}:
        raise RunnerError("split must be 'development' or 'sealed_evaluation'")
    if type(development_smoke) is not bool:
        raise RunnerError("development_smoke must be a boolean")
    if max_queries is not None and (type(max_queries) is not int or max_queries <= 0):
        raise RunnerError("max_queries must be a positive plain integer")
    if experiment_split == "sealed_evaluation":
        if development_smoke:
            raise RunnerError(
                "development smoke mode is categorically forbidden for evaluation"
            )
        if max_queries is not None:
            raise RunnerError("max_queries is categorically forbidden for evaluation")
    elif max_queries is not None and not development_smoke:
        raise RunnerError("max_queries is allowed only with development_smoke")

    all_queries = plan.get("queries")
    if not isinstance(all_queries, list):
        raise RunnerError("plan.queries must be an array")
    selected = [
        row
        for row in all_queries
        if isinstance(row, dict) and row.get("experiment_split") == experiment_split
    ]
    expected = (
        FORMAL_DEVELOPMENT_QUERIES
        if experiment_split == "development"
        else FORMAL_EVALUATION_QUERIES
    )
    if len(selected) != expected:
        raise RunnerError(
            f"formal {experiment_split} plan must contain exactly {expected} queries"
        )
    if development_smoke and max_queries is not None:
        selected = selected[: min(max_queries, len(selected))]
    formal = not development_smoke
    if formal and experiment_split == "development" and len(selected) != 160:
        raise RunnerError("formal development execution requires exactly 160 queries")
    return selected, formal


def _map_paths(
    plan: Mapping[str, Any], repository_root: Path, experiment_split: str
) -> dict[str, tuple[Path, Mapping[str, Any]]]:
    source = plan.get("source_binding")
    if not isinstance(source, dict):
        raise RunnerError("plan.source_binding must be an object")
    relative_root = _text(source.get("relative_root"), label="source.relative_root")
    source_root = _resolve_safe_child(
        repository_root,
        relative_root,
        label="source.relative_root",
        require_file=False,
    )
    result: dict[str, tuple[Path, Mapping[str, Any]]] = {}
    for item in _split_input_bindings(plan, experiment_split):
        map_binding = item.get("map")
        if not isinstance(map_binding, dict):
            raise RunnerError("map input binding must be an object")
        relative = _text(map_binding.get("path"), label="map.path")
        _safe_relative_parts(relative, label="map.path")
        name = _text(item.get("map", {}).get("path"), label="map.path")
        map_name = PurePosixPath(name).name
        if map_name in result:
            raise RunnerError(f"duplicate map binding: {map_name}")
        path = _resolve_safe_child(source_root, relative, label="map.path")
        if _sha256_file(path) != _sha(map_binding.get("sha256"), label="map.sha256"):
            raise RunnerError(
                f"map hash changed after protocol verification: {map_name}"
            )
        result[map_name] = (path, item)
    return result


def _run_query(
    *,
    sequence_index: int,
    query: Mapping[str, Any],
    grid: Any,
    table: LandmarkTable,
    schedule: Sequence[Mapping[str, Any]],
    prefix_landmarks: int,
    full_landmarks: int,
) -> dict[str, Any]:
    query_id = _text(query.get("query_id"), label="query.query_id")
    start_raw, goal_raw = query.get("start"), query.get("goal")
    if (
        not isinstance(start_raw, list)
        or len(start_raw) != 2
        or any(type(value) is not int for value in start_raw)
        or not isinstance(goal_raw, list)
        or len(goal_raw) != 2
        or any(type(value) is not int for value in goal_raw)
    ):
        raise RunnerError(f"{query_id}: endpoints have the wrong shape")
    start = (start_raw[0], start_raw[1])
    goal = (goal_raw[0], goal_raw[1])
    oracle = bfs_shortest_path(grid, start, goal)
    if not oracle.found or oracle.cost is None:
        raise RunnerError(f"{query_id}: protocol-selected query has no BFS path")
    validate_path(grid, oracle.path, start, goal, oracle.cost)

    runs: list[dict[str, Any]] = []
    deterministic_by_method: dict[str, dict[str, Any]] = {}
    for schedule_row in schedule:
        methods = schedule_row["methods"]
        for order_position, method in enumerate(methods):
            result = astar_search(
                grid,
                start,
                goal,
                mode=method,
                landmarks=None if method == "manhattan" else table,
                prefix_landmarks=prefix_landmarks,
                full_landmarks=full_landmarks,
                measure_stage_time=False,
            )
            deterministic, timing = _search_payload(result)
            if not result.found or result.cost != oracle.cost:
                raise RunnerError(
                    f"{query_id}/{method}: search cost {result.cost!r} "
                    f"does not equal BFS oracle {oracle.cost}"
                )
            validate_path(grid, result.path, start, goal, oracle.cost)
            previous = deterministic_by_method.setdefault(method, deterministic)
            if deterministic != previous:
                raise RunnerError(
                    f"{query_id}/{method}: non-timing result differs across repetitions"
                )
            runs.append(
                {
                    "phase": schedule_row["phase"],
                    "repetition": schedule_row["repetition"],
                    "timed": schedule_row["timed"],
                    "order_position": order_position,
                    "method": method,
                    "timing_ns": timing,
                    "result": deterministic,
                }
            )
    if len(runs) != SEARCHES_PER_QUERY:
        raise RunnerError(
            f"{query_id}: runner did not execute exactly {SEARCHES_PER_QUERY} searches"
        )
    if set(deterministic_by_method) != set(METHODS):
        raise RunnerError(f"{query_id}: not every frozen method was executed")
    full_digests = {
        deterministic_by_method[method]["expansion_digest"]
        for method in ("eager_full", "lazy_full", "staged")
    }
    if len(full_digests) != 1:
        raise RunnerError(
            f"{query_id}: full-landmark methods expanded different state sequences"
        )

    copied_query = {
        key: value for key, value in query.items() if key not in {"experiment_split"}
    }
    return {
        "schema": QUERY_SCHEMA,
        "sequence_index": sequence_index,
        "experiment_split": query["experiment_split"],
        "query": copied_query,
        "oracle": {
            "algorithm": "independent-unit-cost-4-neighbor-bfs",
            "cost": oracle.cost,
            "path_sha256": _path_sha256(oracle.path),
        },
        "deterministic_by_method": deterministic_by_method,
        "runs": runs,
        "validation": {
            "all_costs_match_bfs": True,
            "all_repetitions_deterministic": True,
            "full_landmark_expansion_digests_match": True,
        },
    }


def _artifact_binding(payload: bytes, records: int) -> dict[str, Any]:
    return {
        "sha256": _sha256_bytes(payload),
        "bytes": len(payload),
        "records": records,
    }


def _candidate(
    *,
    bindings: Mapping[str, Any],
    artifact_bindings: Mapping[str, Any],
    query_count: int,
) -> dict[str, Any]:
    return {
        "schema": CANDIDATE_SCHEMA,
        "authorization": "not-authorized",
        "eligible_for_external_freeze": True,
        "bindings": _freeze_binding_values(bindings),
        "development_result": {
            "formal": True,
            "complete": True,
            "validation": "passed",
            "query_count": query_count,
            "search_run_count": query_count * SEARCHES_PER_QUERY,
            "artifact_bindings": dict(artifact_bindings),
        },
        "next_step": "external-verifier-must-issue-sealed-evaluation-freeze-v2",
    }


def _freeze_binding_values(bindings: Mapping[str, Any]) -> dict[str, Any]:
    files = bindings["code"]["files"]
    return {
        "protocol_id": bindings["protocol_id"],
        "config_sha256": bindings["config"]["sha256"],
        "plan_sha256": bindings["plan_sha256"],
        "code_sha256": bindings["code"]["sha256"],
        "analysis_sha256": files["analysis"]["sha256"],
        "analysis_cli_sha256": files["analysis_cli"]["sha256"],
        "core_sha256": files["core"]["sha256"],
        "development_gate_sha256": files["development_gate"]["sha256"],
        "freeze_cli_sha256": files["freeze_cli"]["sha256"],
        "protocol_sha256": files["protocol"]["sha256"],
        "runner_sha256": files["runner"]["sha256"],
        "cli_sha256": files["cli"]["sha256"],
        "source_snapshot_sha256": canonical_json_sha256(
            _plain_tree(bindings["source"]["value"]["snapshot"])
        ),
        "environment_sha256": bindings["environment"]["sha256"],
    }


def _validate_evaluation_freeze(
    freeze_path: Path,
    bindings: Mapping[str, Any],
    *,
    config_path: Path,
    repository_root: Path,
) -> dict[str, Any]:
    if freeze_path.is_symlink():
        raise RunnerError("freeze manifest must not be a symbolic link")
    freeze_path = freeze_path.resolve(strict=True)
    decoded = _strict_json_file(freeze_path)
    if not isinstance(decoded, dict):
        raise RunnerError("freeze manifest must be an object")
    if decoded.get("schema") != FREEZE_SCHEMA:
        raise RunnerError(
            "evaluation requires an external sealed-evaluation freeze manifest; "
            "a development candidate is not authorization"
        )
    freeze = _object(
        decoded,
        label="freeze manifest",
        keys={
            "schema",
            "authorization",
            "issued_by",
            "bindings",
            "development_result",
            "development_audit",
        },
    )
    if (
        freeze["authorization"] != "sealed_evaluation"
        or freeze["issued_by"] != "progressive-landmarks-external-development-gate-v2"
    ):
        raise RunnerError("freeze manifest does not authorize sealed evaluation")
    expected_bindings = _freeze_binding_values(bindings)
    actual_bindings = _object(
        freeze["bindings"],
        label="freeze.bindings",
        keys=expected_bindings,
    )
    for key, expected in expected_bindings.items():
        if actual_bindings[key] != expected:
            raise RunnerError(f"freeze binding differs from current {key}")

    audit = _object(
        freeze["development_audit"],
        label="freeze.development_audit",
        keys={"path", "sha256", "schema"},
    )
    audit_path = _resolve_safe_child(
        freeze_path.parent,
        audit["path"],
        label="development_audit.path",
        expected_name="development_audit.json",
    )
    if _sha256_file(audit_path) != _sha(
        audit["sha256"], label="development_audit.sha256"
    ):
        raise RunnerError("development audit hash does not match freeze binding")
    if audit["schema"] != DEVELOPMENT_AUDIT_SCHEMA:
        raise RunnerError("development audit binding has the wrong frozen schema")
    # Import lazily to keep the runner/development-gate dependency acyclic.
    from .development_gate import (
        DevelopmentGateError,
        audit_formal_development,
        load_development_audit,
    )

    try:
        audit_value = load_development_audit(audit_path)
    except DevelopmentGateError as exc:
        raise RunnerError(f"development audit failed strict validation: {exc}") from exc
    if audit_value["bindings"] != actual_bindings:
        raise RunnerError("development audit bindings differ from the freeze")

    development = _object(
        freeze["development_result"],
        label="freeze.development_result",
        keys={
            "manifest_path",
            "manifest_sha256",
            "formal",
            "complete",
            "validation",
            "query_count",
            "search_run_count",
            "map_count",
            "artifact_bindings",
        },
    )
    manifest_path = _resolve_safe_child(
        freeze_path.parent,
        development["manifest_path"],
        label="development.manifest_path",
        expected_name="manifest.json",
        minimum_parts=2,
    )
    if audit_value["development_result"] != development:
        raise RunnerError("development audit result evidence differs from the freeze")
    if _sha256_file(manifest_path) != _sha(
        development["manifest_sha256"], label="development.manifest_sha256"
    ):
        raise RunnerError("development manifest hash does not match freeze binding")
    try:
        expected_authorization = audit_formal_development(
            manifest_path.parent,
            config_path=config_path,
            repository_root=repository_root,
            audit_path=audit_path,
            freeze_path=freeze_path,
        )
    except (DevelopmentGateError, RunnerError, OSError) as exc:
        raise RunnerError(
            f"referenced development result failed independent replay audit: {exc}"
        ) from exc
    if audit_value != _plain_tree(expected_authorization.audit):
        raise RunnerError("development audit differs from a fresh independent audit")
    if freeze != _plain_tree(expected_authorization.freeze):
        raise RunnerError("freeze differs from fresh independent authorization")
    return {
        "path": str(freeze_path),
        "sha256": _sha256_file(freeze_path),
        "development_manifest_sha256": development["manifest_sha256"],
    }


def run_split(
    config_path: str | Path,
    output_directory: str | Path,
    *,
    repository_root: str | Path,
    experiment_split: str,
    freeze_manifest: str | Path | None = None,
    development_smoke: bool = False,
    max_queries: int | None = None,
) -> Path:
    """Execute and atomically publish exactly one protocol split.

    ``max_queries`` is accepted only for explicitly non-formal development smoke
    runs.  A formal development run always contains exactly 160 queries; sealed
    evaluation always contains exactly 800 and requires external authorization.
    """

    repository_root = Path(repository_root).resolve(strict=True)
    config_path = Path(config_path).resolve(strict=True)
    output = Path(output_directory).resolve(strict=False)
    if not output.name or output == output.parent:
        raise RunnerError("output directory must identify a concrete child path")
    if _lexists(output):
        raise RunnerError(f"refusing to overwrite existing output: {output}")

    plan = verify_protocol(config_path, repository_root=repository_root)
    _validate_plan_hash(plan)
    queries, formal = _selected_queries(
        plan,
        experiment_split,
        development_smoke=development_smoke,
        max_queries=max_queries,
    )
    schedule = _validate_schedule(plan)
    bindings = _current_bindings(plan, repository_root, experiment_split)
    authorization: dict[str, Any] | None = None
    if experiment_split == "sealed_evaluation":
        if freeze_manifest is None:
            raise RunnerError("sealed evaluation requires --freeze-manifest")
        authorization = _validate_evaluation_freeze(
            Path(freeze_manifest),
            bindings,
            config_path=config_path,
            repository_root=repository_root,
        )
    elif freeze_manifest is not None:
        raise RunnerError("freeze_manifest is accepted only for sealed evaluation")

    landmarks = plan.get("landmarks")
    if not isinstance(landmarks, dict):
        raise RunnerError("plan.landmarks must be an object")
    full_landmarks = _plain_int(
        landmarks.get("full_pivots"), label="landmarks.full_pivots", minimum=1
    )
    prefix_landmarks = _plain_int(
        landmarks.get("staged_prefix_pivots"),
        label="landmarks.staged_prefix_pivots",
        minimum=0,
    )
    if prefix_landmarks > full_landmarks:
        raise RunnerError("staged prefix exceeds full landmark count")
    map_paths = _map_paths(plan, repository_root, experiment_split)
    used_names = list(
        dict.fromkeys(_text(row.get("map"), label="query.map") for row in queries)
    )
    if any(name not in map_paths for name in used_names):
        raise RunnerError("query references a map without a split input binding")

    output.parent.mkdir(parents=True, exist_ok=True)
    started_at_utc = _utc_now()
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent)
    )
    published = False
    try:
        map_records: list[dict[str, Any]] = []
        grids: dict[str, Any] = {}
        tables: dict[str, LandmarkTable] = {}
        for map_name in used_names:
            map_path, input_binding = map_paths[map_name]
            grid = read_moving_ai_map(map_path)
            expected_map = input_binding["map"]
            if (
                grid.name != map_name
                or grid.width != expected_map["width"]
                or grid.height != expected_map["height"]
                or grid.traversable_count != expected_map["traversable_states"]
            ):
                raise RunnerError(f"map metadata differs from plan: {map_name}")
            started = perf_counter_ns()
            table = build_landmark_table(grid, full_landmarks)
            build_ns = perf_counter_ns() - started
            if len(table.landmarks) != min(full_landmarks, grid.traversable_count):
                raise RunnerError(f"landmark table has the wrong size: {map_name}")
            grids[map_name] = grid
            tables[map_name] = table
            map_records.append(
                {
                    "map": map_name,
                    "family": input_binding["family"],
                    "source_split": input_binding["source_split"],
                    "map_sha256": expected_map["sha256"],
                    "width": grid.width,
                    "height": grid.height,
                    "traversable_states": grid.traversable_count,
                    "landmark_build_ns": build_ns,
                    "packed_distance_bytes": sum(
                        len(row) for row in table.packed_distances
                    ),
                    "requested_landmarks": full_landmarks,
                    "actual_landmarks": len(table.landmarks),
                    "landmarks": [[x, y] for x, y in table.landmarks],
                    "table_sha256": _table_sha256(table),
                }
            )

        query_records = [
            _run_query(
                sequence_index=index,
                query=query,
                grid=grids[query["map"]],
                table=tables[query["map"]],
                schedule=schedule,
                prefix_landmarks=prefix_landmarks,
                full_landmarks=full_landmarks,
            )
            for index, query in enumerate(queries)
        ]
        search_runs = len(query_records) * SEARCHES_PER_QUERY
        queries_payload = _jsonl_bytes(query_records)
        maps_value = {"schema": MAPS_SCHEMA, "maps": map_records}
        maps_payload = _json_file_bytes(maps_value)
        completed_at_utc = _utc_now()
        run_value = {
            "schema": RUN_SCHEMA,
            "protocol_id": plan["protocol_id"],
            "experiment_split": experiment_split,
            "formal": formal,
            "nonformal_smoke": not formal,
            "status": "complete",
            "validation": "passed",
            "started_at_utc": started_at_utc,
            "completed_at_utc": completed_at_utc,
            "methods": list(METHODS),
            "schedule": schedule,
            "landmarks": {
                "full_pivots": full_landmarks,
                "staged_prefix_pivots": prefix_landmarks,
            },
            "counts": {
                "maps": len(map_records),
                "queries": len(query_records),
                "warmup_repetitions": WARMUP_REPETITIONS,
                "timed_repetitions": TIMED_REPETITIONS,
                "searches_per_query": SEARCHES_PER_QUERY,
                "search_runs": search_runs,
            },
            "bindings": bindings,
            "evaluation_authorization": authorization,
        }
        run_payload = _json_file_bytes(run_value)
        base_artifacts = {
            "queries.jsonl": _artifact_binding(queries_payload, len(query_records)),
            "maps.json": _artifact_binding(maps_payload, len(map_records)),
            "run.json": _artifact_binding(run_payload, 1),
        }
        candidate_payload: bytes | None = None
        if experiment_split == "development" and formal:
            candidate_payload = _json_file_bytes(
                _candidate(
                    bindings=bindings,
                    artifact_bindings=base_artifacts,
                    query_count=len(query_records),
                )
            )
            base_artifacts["development_freeze_candidate.json"] = _artifact_binding(
                candidate_payload, 1
            )

        _write_new(staging / "queries.jsonl", queries_payload)
        _write_new(staging / "maps.json", maps_payload)
        _write_new(staging / "run.json", run_payload)
        if candidate_payload is not None:
            _write_new(staging / "development_freeze_candidate.json", candidate_payload)
        manifest_value = {
            "schema": MANIFEST_SCHEMA,
            "protocol_id": plan["protocol_id"],
            "experiment_split": experiment_split,
            "formal": formal,
            "complete": True,
            "validation": "passed",
            "artifacts": base_artifacts,
            "record_counts": {
                "maps": len(map_records),
                "queries": len(query_records),
                "search_runs": search_runs,
            },
        }
        # The manifest is the completion marker and is intentionally written last.
        _write_new(staging / "manifest.json", _json_file_bytes(manifest_value))

        if formal:
            load_complete_run(
                staging,
                config_path=config_path,
                repository_root=repository_root,
                freeze_manifest=(
                    freeze_manifest if experiment_split == "sealed_evaluation" else None
                ),
            )
        else:
            load_complete_run(staging)
        if _lexists(output):
            raise RunnerError(f"refusing to overwrite raced output: {output}")
        try:
            os.rename(staging, output)
        except OSError as exc:
            raise RunnerError(f"cannot atomically publish {output}: {exc}") from exc
        published = True
        return output
    finally:
        if not published and _lexists(staging):
            shutil.rmtree(staging)


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _count_jsonl_records(payload: bytes, *, label: str) -> list[dict[str, Any]]:
    if payload and not payload.endswith(b"\n"):
        raise RunnerError(f"{label} must end in a newline")
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(payload.splitlines(), start=1):
        value = _strict_json_bytes(line, label=f"{label}:{number}")
        if not isinstance(value, dict):
            raise RunnerError(f"{label}:{number} must be an object")
        rows.append(value)
    return rows


_COUNTER_FIELDS = {
    "expanded",
    "generated",
    "relaxations",
    "reopened",
    "pops",
    "stale_pops",
    "requeues",
    "manhattan_calls",
    "prefix_calls",
    "suffix_calls",
    "full_calls",
    "pivot_evaluations",
    "distance_table_reads",
    "heuristic_cache_hits",
    "unique_discovered",
    "max_open_entries",
    "max_live_states",
}


def _validate_loaded_bindings(value: Any) -> dict[str, Any]:
    bindings = _object(
        value,
        label="run.bindings",
        keys={
            "protocol_id",
            "config",
            "plan_sha256",
            "code",
            "source",
            "environment",
        },
    )
    _text(bindings["protocol_id"], label="bindings.protocol_id")
    _sha(bindings["plan_sha256"], label="bindings.plan_sha256")
    config = _object(
        bindings["config"],
        label="bindings.config",
        keys={"path", "sha256", "hash_basis"},
    )
    if config["path"] is not None:
        _text(config["path"], label="bindings.config.path")
    _sha(config["sha256"], label="bindings.config.sha256")
    if config["hash_basis"] not in {"file-bytes", "canonical-json"}:
        raise RunnerError("bindings.config.hash_basis is invalid")

    code = _object(bindings["code"], label="bindings.code", keys={"files", "sha256"})
    files = code["files"]
    if not isinstance(files, dict):
        raise RunnerError("bindings.code.files must be an object")
    required_files = {
        "analysis",
        "analysis_cli",
        "cli",
        "core",
        "development_gate",
        "freeze_cli",
        "package_init",
        "protocol",
        "runner",
    }
    if set(files) != required_files:
        raise RunnerError("bindings.code.files has an invalid inventory")
    for name, raw_binding in files.items():
        binding = _object(
            raw_binding,
            label=f"bindings.code.files[{name!r}]",
            keys={"path", "sha256", "bytes"},
        )
        _text(binding["path"], label=f"bindings.code.files[{name!r}].path")
        _sha(binding["sha256"], label=f"bindings.code.files[{name!r}].sha256")
        _plain_int(
            binding["bytes"], label=f"bindings.code.files[{name!r}].bytes", minimum=1
        )
    if _sha(code["sha256"], label="bindings.code.sha256") != canonical_json_sha256(
        files
    ):
        raise RunnerError("bindings.code aggregate SHA-256 is invalid")

    source = _object(
        bindings["source"], label="bindings.source", keys={"value", "sha256"}
    )
    source_value = _object(
        source["value"],
        label="bindings.source.value",
        keys={"snapshot", "split_inputs"},
    )
    if not isinstance(source_value["snapshot"], dict) or not isinstance(
        source_value["split_inputs"], list
    ):
        raise RunnerError("bindings.source.value has invalid nested values")
    if _sha(source["sha256"], label="bindings.source.sha256") != canonical_json_sha256(
        source_value
    ):
        raise RunnerError("bindings.source aggregate SHA-256 is invalid")

    environment = _object(
        bindings["environment"],
        label="bindings.environment",
        keys={"value", "sha256"},
    )
    environment_value = _object(
        environment["value"],
        label="bindings.environment.value",
        keys={
            "python_implementation",
            "python_version",
            "python_compiler",
            "python_executable",
            "platform",
            "machine",
            "processor",
            "byteorder",
            "cpu_count",
            "pythonhashseed",
        },
    )
    if _sha(
        environment["sha256"], label="bindings.environment.sha256"
    ) != canonical_json_sha256(environment_value):
        raise RunnerError("bindings.environment aggregate SHA-256 is invalid")
    return bindings


def _expected_loaded_schedule() -> list[dict[str, Any]]:
    result = [
        {
            "phase": "warmup",
            "repetition": 0,
            "timed": False,
            "methods": list(METHODS),
        }
    ]
    for repetition in range(TIMED_REPETITIONS):
        offset = repetition % len(METHODS)
        result.append(
            {
                "phase": "timed",
                "repetition": repetition,
                "timed": True,
                "methods": list(METHODS[offset:] + METHODS[:offset]),
            }
        )
    return result


def _validate_loaded_run(value: Any) -> dict[str, Any]:
    run = _object(
        value,
        label="run.json",
        keys={
            "schema",
            "protocol_id",
            "experiment_split",
            "formal",
            "nonformal_smoke",
            "status",
            "validation",
            "started_at_utc",
            "completed_at_utc",
            "methods",
            "schedule",
            "landmarks",
            "counts",
            "bindings",
            "evaluation_authorization",
        },
    )
    if run["schema"] != RUN_SCHEMA:
        raise RunnerError("run.json has the wrong schema")
    if run["protocol_id"] != PROTOCOL_ID:
        raise RunnerError("run protocol ID is not progressive_landmarks_v2")
    split = run["experiment_split"]
    if split not in {"development", "sealed_evaluation"}:
        raise RunnerError("run.experiment_split is invalid")
    if type(run["formal"]) is not bool or type(run["nonformal_smoke"]) is not bool:
        raise RunnerError("run formal/smoke flags must be booleans")
    if run["nonformal_smoke"] is run["formal"]:
        raise RunnerError("run formal and nonformal_smoke flags are inconsistent")
    if split == "sealed_evaluation" and run["formal"] is not True:
        raise RunnerError("sealed evaluation cannot be a non-formal smoke run")
    if run["status"] != "complete" or run["validation"] != "passed":
        raise RunnerError("run.json is not a successful completed run")
    timestamp_values: list[datetime] = []
    try:
        for key in ("started_at_utc", "completed_at_utc"):
            timestamp = _text(run[key], label=f"run.{key}")
            if not timestamp.endswith("Z"):
                raise ValueError("timestamp is not explicitly UTC")
            timestamp_values.append(
                datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            )
    except ValueError as exc:
        raise RunnerError(
            "run timestamps are not explicit UTC ISO-8601 values"
        ) from exc
    if timestamp_values[1] < timestamp_values[0]:
        raise RunnerError("run completion precedes its start")
    if run["methods"] != list(METHODS):
        raise RunnerError("run method list differs from the frozen methods")
    schedule = run["schedule"]
    if not isinstance(schedule, list) or schedule != _expected_loaded_schedule():
        raise RunnerError("run schedule differs from the frozen 1+8 rotations")
    for index, row in enumerate(schedule):
        _object(
            row,
            label=f"run.schedule[{index}]",
            keys={"phase", "repetition", "timed", "methods"},
        )
    landmarks = _object(
        run["landmarks"],
        label="run.landmarks",
        keys={"full_pivots", "staged_prefix_pivots"},
    )
    full = _plain_int(
        landmarks["full_pivots"], label="run.landmarks.full_pivots", minimum=1
    )
    prefix = _plain_int(
        landmarks["staged_prefix_pivots"],
        label="run.landmarks.staged_prefix_pivots",
    )
    if prefix > full:
        raise RunnerError("run staged landmark prefix exceeds the full count")
    _object(
        run["counts"],
        label="run.counts",
        keys={
            "maps",
            "queries",
            "warmup_repetitions",
            "timed_repetitions",
            "searches_per_query",
            "search_runs",
        },
    )
    bindings = _validate_loaded_bindings(run["bindings"])
    if bindings["protocol_id"] != run["protocol_id"]:
        raise RunnerError("run protocol ID disagrees with its binding")
    authorization = run["evaluation_authorization"]
    if split == "development":
        if authorization is not None:
            raise RunnerError(
                "development run unexpectedly has evaluation authorization"
            )
    else:
        authorization = _object(
            authorization,
            label="run.evaluation_authorization",
            keys={"path", "sha256", "development_manifest_sha256"},
        )
        _text(authorization["path"], label="evaluation_authorization.path")
        _sha(authorization["sha256"], label="evaluation_authorization.sha256")
        _sha(
            authorization["development_manifest_sha256"],
            label="evaluation_authorization.development_manifest_sha256",
        )
    return run


def _validate_loaded_maps(
    value: Any, run: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    maps = _object(value, label="maps.json", keys={"schema", "maps"})
    if maps["schema"] != MAPS_SCHEMA or not isinstance(maps["maps"], list):
        raise RunnerError("maps.json has the wrong schema")
    by_name: dict[str, dict[str, Any]] = {}
    table_hashes: set[str] = set()
    for index, raw_row in enumerate(maps["maps"]):
        row = _object(
            raw_row,
            label=f"maps.maps[{index}]",
            keys={
                "map",
                "family",
                "source_split",
                "map_sha256",
                "width",
                "height",
                "traversable_states",
                "landmark_build_ns",
                "packed_distance_bytes",
                "requested_landmarks",
                "actual_landmarks",
                "landmarks",
                "table_sha256",
            },
        )
        name = _text(row["map"], label=f"maps.maps[{index}].map")
        if PurePosixPath(name).name != name or not name.endswith(".map"):
            raise RunnerError("stored map name is not a plain .map filename")
        if name in by_name:
            raise RunnerError(f"duplicate stored map: {name}")
        if row["family"] not in {"maze", "random", "room", "warehouse"}:
            raise RunnerError(f"invalid family for stored map: {name}")
        expected_source_splits = (
            {"train"}
            if run["experiment_split"] == "development"
            else {"validation", "holdout"}
        )
        if row["source_split"] not in expected_source_splits:
            raise RunnerError(f"stored map crosses experiment split: {name}")
        _sha(row["map_sha256"], label=f"maps.maps[{index}].map_sha256")
        width = _plain_int(row["width"], label=f"maps.maps[{index}].width", minimum=1)
        height = _plain_int(
            row["height"], label=f"maps.maps[{index}].height", minimum=1
        )
        traversable = _plain_int(
            row["traversable_states"],
            label=f"maps.maps[{index}].traversable_states",
            minimum=1,
        )
        if traversable > width * height:
            raise RunnerError(f"stored traversable count exceeds map area: {name}")
        _plain_int(
            row["landmark_build_ns"],
            label=f"maps.maps[{index}].landmark_build_ns",
        )
        requested = _plain_int(
            row["requested_landmarks"],
            label=f"maps.maps[{index}].requested_landmarks",
            minimum=1,
        )
        actual = _plain_int(
            row["actual_landmarks"],
            label=f"maps.maps[{index}].actual_landmarks",
            minimum=1,
        )
        if requested != run["landmarks"]["full_pivots"] or actual != min(
            requested, traversable
        ):
            raise RunnerError(f"stored landmark count is inconsistent: {name}")
        packed_bytes = _plain_int(
            row["packed_distance_bytes"],
            label=f"maps.maps[{index}].packed_distance_bytes",
            minimum=1,
        )
        if packed_bytes != actual * width * height * 4:
            raise RunnerError(f"stored packed table byte count is inconsistent: {name}")
        landmark_rows = row["landmarks"]
        if not isinstance(landmark_rows, list) or len(landmark_rows) != actual:
            raise RunnerError(f"stored landmark list has the wrong length: {name}")
        coordinates: list[tuple[int, int]] = []
        for offset, cell in enumerate(landmark_rows):
            if (
                not isinstance(cell, list)
                or len(cell) != 2
                or any(type(coordinate) is not int for coordinate in cell)
                or not 0 <= cell[0] < width
                or not 0 <= cell[1] < height
            ):
                raise RunnerError(f"invalid landmark coordinate in {name} at {offset}")
            coordinates.append((cell[0], cell[1]))
        if len(set(coordinates)) != len(coordinates):
            raise RunnerError(f"stored landmarks are not unique: {name}")
        table_sha = _sha(row["table_sha256"], label=f"maps.maps[{index}].table_sha256")
        if table_sha in table_hashes:
            raise RunnerError("different map records reuse a landmark-table digest")
        table_hashes.add(table_sha)
        by_name[name] = row
    return maps, by_name


def _validate_loaded_result(
    value: Any, *, label: str, expected_mode: str, oracle_cost: int
) -> dict[str, Any]:
    result = _object(
        value,
        label=label,
        keys={"mode", "found", "cost", "path_sha256", "expansion_digest", "counters"},
    )
    if result["mode"] != expected_mode or result["found"] is not True:
        raise RunnerError(f"{label} has inconsistent mode or success status")
    if result["cost"] != oracle_cost:
        raise RunnerError(f"{label} cost differs from its BFS oracle")
    _sha(result["path_sha256"], label=f"{label}.path_sha256")
    _sha(result["expansion_digest"], label=f"{label}.expansion_digest")
    counters = _object(
        result["counters"], label=f"{label}.counters", keys=_COUNTER_FIELDS
    )
    for name, counter in counters.items():
        _plain_int(counter, label=f"{label}.counters.{name}")
    if (
        counters["reopened"] != 0
        or counters["unique_discovered"] < 1
        or counters["generated"] < counters["relaxations"]
        or counters["unique_discovered"] > counters["relaxations"] + 1
        or counters["max_live_states"] > counters["unique_discovered"]
        or counters["manhattan_calls"] != counters["unique_discovered"]
        or counters["pops"] - counters["stale_pops"]
        != counters["expanded"] + counters["requeues"] + 1
        or (expected_mode in {"manhattan", "eager_full"} and counters["requeues"])
        or (
            expected_mode == "eager_full"
            and counters["full_calls"] != counters["unique_discovered"]
        )
    ):
        raise RunnerError(
            f"{label} violates deterministic consistent-search invariants"
        )
    return result


def _validate_loaded_queries(
    rows: list[dict[str, Any]],
    run: Mapping[str, Any],
    maps_by_name: Mapping[str, Mapping[str, Any]],
) -> None:
    expected_schedule = _expected_loaded_schedule()
    query_ids: set[str] = set()
    per_map: dict[str, int] = {name: 0 for name in maps_by_name}
    for expected_index, raw_row in enumerate(rows):
        row = _object(
            raw_row,
            label=f"queries[{expected_index}]",
            keys={
                "schema",
                "sequence_index",
                "experiment_split",
                "query",
                "oracle",
                "deterministic_by_method",
                "runs",
                "validation",
            },
        )
        if row["schema"] != QUERY_SCHEMA or row["sequence_index"] != expected_index:
            raise RunnerError("query schema or contiguous sequence index is invalid")
        if row["experiment_split"] != run["experiment_split"]:
            raise RunnerError("query crosses the run experiment split")
        query = _object(
            row["query"],
            label=f"queries[{expected_index}].query",
            keys={
                "query_id",
                "source_split",
                "family",
                "map",
                "scenario",
                "scenario_index",
                "scenario_row",
                "source_line",
                "source_bucket",
                "start",
                "goal",
            },
        )
        query_id = _text(query["query_id"], label=f"queries[{expected_index}].query_id")
        if query_id in query_ids:
            raise RunnerError(f"duplicate stored query ID: {query_id}")
        query_ids.add(query_id)
        map_name = _text(query["map"], label=f"queries[{expected_index}].map")
        map_row = maps_by_name.get(map_name)
        if map_row is None:
            raise RunnerError(f"query references an absent map table: {map_name}")
        if (
            query["family"] != map_row["family"]
            or query["source_split"] != map_row["source_split"]
        ):
            raise RunnerError(
                f"query map identity disagrees with maps.json: {query_id}"
            )
        per_map[map_name] += 1
        _text(query["scenario"], label=f"queries[{expected_index}].scenario")
        for name in ("scenario_index", "scenario_row", "source_line"):
            _plain_int(
                query[name], label=f"queries[{expected_index}].{name}", minimum=1
            )
        _plain_int(
            query["source_bucket"], label=f"queries[{expected_index}].source_bucket"
        )
        endpoints: list[tuple[int, int]] = []
        for name in ("start", "goal"):
            cell = query[name]
            if (
                not isinstance(cell, list)
                or len(cell) != 2
                or any(type(coordinate) is not int for coordinate in cell)
                or not 0 <= cell[0] < map_row["width"]
                or not 0 <= cell[1] < map_row["height"]
            ):
                raise RunnerError(f"query has an invalid {name}: {query_id}")
            endpoints.append((cell[0], cell[1]))
        if endpoints[0] == endpoints[1]:
            raise RunnerError(f"query endpoints are not distinct: {query_id}")

        oracle = _object(
            row["oracle"],
            label=f"queries[{expected_index}].oracle",
            keys={"algorithm", "cost", "path_sha256"},
        )
        if oracle["algorithm"] != "independent-unit-cost-4-neighbor-bfs":
            raise RunnerError(f"query has the wrong oracle: {query_id}")
        oracle_cost = _plain_int(
            oracle["cost"], label=f"queries[{expected_index}].oracle.cost", minimum=1
        )
        _sha(
            oracle["path_sha256"],
            label=f"queries[{expected_index}].oracle.path_sha256",
        )
        summaries = row["deterministic_by_method"]
        if not isinstance(summaries, dict) or set(summaries) != set(METHODS):
            raise RunnerError(
                f"query method summaries differ from frozen methods: {query_id}"
            )
        checked_summaries = {
            method: _validate_loaded_result(
                summaries[method],
                label=f"queries[{expected_index}].deterministic_by_method.{method}",
                expected_mode=method,
                oracle_cost=oracle_cost,
            )
            for method in METHODS
        }
        full_digests = {
            checked_summaries[method]["expansion_digest"]
            for method in ("eager_full", "lazy_full", "staged")
        }
        if len(full_digests) != 1:
            raise RunnerError(
                f"full-landmark expansion digest invariant failed: {query_id}"
            )
        validation = _object(
            row["validation"],
            label=f"queries[{expected_index}].validation",
            keys={
                "all_costs_match_bfs",
                "all_repetitions_deterministic",
                "full_landmark_expansion_digests_match",
            },
        )
        if any(value is not True for value in validation.values()):
            raise RunnerError(f"query validation flag is not true: {query_id}")

        invocations = row["runs"]
        if not isinstance(invocations, list) or len(invocations) != SEARCHES_PER_QUERY:
            raise RunnerError(
                f"query does not contain exactly {SEARCHES_PER_QUERY} search invocations"
            )
        invocation_index = 0
        for schedule_row in expected_schedule:
            for order_position, method in enumerate(schedule_row["methods"]):
                invocation = _object(
                    invocations[invocation_index],
                    label=f"queries[{expected_index}].runs[{invocation_index}]",
                    keys={
                        "phase",
                        "repetition",
                        "timed",
                        "order_position",
                        "method",
                        "timing_ns",
                        "result",
                    },
                )
                expected_metadata = {
                    "phase": schedule_row["phase"],
                    "repetition": schedule_row["repetition"],
                    "timed": schedule_row["timed"],
                    "order_position": order_position,
                    "method": method,
                }
                if any(
                    invocation[key] != value for key, value in expected_metadata.items()
                ):
                    raise RunnerError(
                        f"search invocation violates frozen rotation: {query_id}"
                    )
                timing = _object(
                    invocation["timing_ns"],
                    label=f"queries[{expected_index}].runs[{invocation_index}].timing_ns",
                    keys={"stage_ns", "search_ns"},
                )
                if (
                    _plain_int(timing["stage_ns"], label="timing.stage_ns") != 0
                    or _plain_int(
                        timing["search_ns"], label="timing.search_ns", minimum=1
                    )
                    < 1
                ):
                    raise RunnerError(
                        "stored timing violates runner measurement contract"
                    )
                result = _validate_loaded_result(
                    invocation["result"],
                    label=f"queries[{expected_index}].runs[{invocation_index}].result",
                    expected_mode=method,
                    oracle_cost=oracle_cost,
                )
                if result != checked_summaries[method]:
                    raise RunnerError(
                        f"invocation result differs from method summary: {query_id}"
                    )
                invocation_index += 1

    if rows and any(count == 0 for count in per_map.values()):
        raise RunnerError("stored map-table inventory is not covered by queries")
    if run["formal"]:
        expected_maps = 4 if run["experiment_split"] == "development" else 8
        expected_queries_per_map = (
            40 if run["experiment_split"] == "development" else 100
        )
        if len(maps_by_name) != expected_maps or set(per_map.values()) != {
            expected_queries_per_map
        }:
            raise RunnerError("formal run has the wrong map/query coverage matrix")


def _validate_candidate(
    value: Any,
    *,
    run: Mapping[str, Any],
    manifest_artifacts: Mapping[str, Any],
) -> None:
    candidate = _object(
        value,
        label="development_freeze_candidate.json",
        keys={
            "schema",
            "authorization",
            "eligible_for_external_freeze",
            "bindings",
            "development_result",
            "next_step",
        },
    )
    if (
        candidate["schema"] != CANDIDATE_SCHEMA
        or candidate["authorization"] != "not-authorized"
        or candidate["eligible_for_external_freeze"] is not True
        or candidate["next_step"]
        != "external-verifier-must-issue-sealed-evaluation-freeze-v2"
    ):
        raise RunnerError("development freeze candidate has an invalid status")
    expected_bindings = _freeze_binding_values(run["bindings"])
    bindings = _object(
        candidate["bindings"],
        label="candidate.bindings",
        keys=expected_bindings,
    )
    if bindings != expected_bindings:
        raise RunnerError("candidate bindings differ from the development run")
    development = _object(
        candidate["development_result"],
        label="candidate.development_result",
        keys={
            "formal",
            "complete",
            "validation",
            "query_count",
            "search_run_count",
            "artifact_bindings",
        },
    )
    if (
        development["formal"] is not True
        or development["complete"] is not True
        or development["validation"] != "passed"
        or development["query_count"] != run["counts"]["queries"]
        or development["search_run_count"] != run["counts"]["search_runs"]
    ):
        raise RunnerError("candidate does not attest this successful formal run")
    expected_artifacts = {
        name: manifest_artifacts[name]
        for name in ("queries.jsonl", "maps.json", "run.json")
    }
    if development["artifact_bindings"] != expected_artifacts:
        raise RunnerError("candidate artifact bindings differ from the manifest")


def _plain_tree(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain_tree(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_tree(item) for item in value]
    return value


def _replay_formal_run(
    loaded: LoadedRun,
    *,
    config_path: Path,
    repository_root: Path,
) -> dict[str, Any]:
    """Rebuild all deterministic evidence from the pinned protocol and sources."""

    try:
        plan = verify_protocol(config_path, repository_root=repository_root)
    except Exception as exc:
        raise RunnerError(f"formal replay protocol verification failed: {exc}") from exc
    _validate_plan_hash(plan)
    split = loaded.run["experiment_split"]
    expected_bindings = _current_bindings(plan, repository_root, split)
    if _plain_tree(loaded.run["bindings"]) != expected_bindings:
        raise RunnerError(
            "formal result bindings differ from the canonical current inputs"
        )

    planned_queries = [
        row
        for row in plan.get("queries", [])
        if isinstance(row, dict) and row.get("experiment_split") == split
    ]
    if len(planned_queries) != len(loaded.queries):
        raise RunnerError("formal replay query count differs from the canonical plan")
    for index, (stored, planned) in enumerate(
        zip(loaded.queries, planned_queries, strict=True)
    ):
        expected_query = {
            key: value for key, value in planned.items() if key != "experiment_split"
        }
        if (
            stored["sequence_index"] != index
            or stored["experiment_split"] != split
            or _plain_tree(stored["query"]) != expected_query
        ):
            raise RunnerError(
                f"formal replay query identity/order differs at sequence {index}"
            )

    landmark_plan = plan.get("landmarks")
    if not isinstance(landmark_plan, dict):
        raise RunnerError("formal replay plan has no landmark object")
    full_landmarks = _plain_int(
        landmark_plan.get("full_pivots"),
        label="formal replay full_pivots",
        minimum=1,
    )
    prefix_landmarks = _plain_int(
        landmark_plan.get("staged_prefix_pivots"),
        label="formal replay staged_prefix_pivots",
    )
    if _plain_tree(loaded.run["landmarks"]) != {
        "full_pivots": full_landmarks,
        "staged_prefix_pivots": prefix_landmarks,
    }:
        raise RunnerError("formal replay landmark parameters differ from the plan")

    paths = _map_paths(plan, repository_root, split)
    expected_map_names = [
        PurePosixPath(binding["map"]["path"]).name
        for binding in _split_input_bindings(plan, split)
    ]
    stored_maps = list(loaded.maps["maps"])
    if [row["map"] for row in stored_maps] != expected_map_names:
        raise RunnerError("formal replay map inventory/order differs from the plan")

    grids: dict[str, Any] = {}
    tables: dict[str, LandmarkTable] = {}
    for stored in stored_maps:
        name = stored["map"]
        path, input_binding = paths[name]
        grid = read_moving_ai_map(path)
        expected_map = input_binding["map"]
        expected_metadata = {
            "map": name,
            "family": input_binding["family"],
            "source_split": input_binding["source_split"],
            "map_sha256": expected_map["sha256"],
            "width": expected_map["width"],
            "height": expected_map["height"],
            "traversable_states": expected_map["traversable_states"],
        }
        if any(stored[key] != value for key, value in expected_metadata.items()):
            raise RunnerError(f"formal replay map metadata differs: {name}")
        if (
            grid.name != name
            or grid.width != stored["width"]
            or grid.height != stored["height"]
            or grid.traversable_count != stored["traversable_states"]
        ):
            raise RunnerError(f"formal replay parsed map differs: {name}")
        table = build_landmark_table(grid, full_landmarks)
        packed_bytes = sum(len(row) for row in table.packed_distances)
        if (
            _plain_tree(stored["landmarks"]) != [[x, y] for x, y in table.landmarks]
            or stored["requested_landmarks"] != full_landmarks
            or stored["actual_landmarks"] != len(table.landmarks)
            or stored["packed_distance_bytes"] != packed_bytes
            or stored["table_sha256"] != _table_sha256(table)
        ):
            raise RunnerError(f"formal replay landmark table differs: {name}")
        grids[name] = grid
        tables[name] = table

    for stored, planned in zip(loaded.queries, planned_queries, strict=True):
        query_id = planned["query_id"]
        grid = grids[planned["map"]]
        table = tables[planned["map"]]
        start = tuple(planned["start"])
        goal = tuple(planned["goal"])
        oracle = bfs_shortest_path(grid, start, goal)
        if not oracle.found or oracle.cost is None:
            raise RunnerError(f"formal replay BFS cannot solve {query_id}")
        validate_path(grid, oracle.path, start, goal, oracle.cost)
        expected_oracle = {
            "algorithm": "independent-unit-cost-4-neighbor-bfs",
            "cost": oracle.cost,
            "path_sha256": _path_sha256(oracle.path),
        }
        if _plain_tree(stored["oracle"]) != expected_oracle:
            raise RunnerError(f"formal replay BFS evidence differs: {query_id}")
        stored_summaries = stored["deterministic_by_method"]
        for method in METHODS:
            result = astar_search(
                grid,
                start,
                goal,
                mode=method,
                landmarks=None if method == "manhattan" else table,
                prefix_landmarks=prefix_landmarks,
                full_landmarks=full_landmarks,
                measure_stage_time=False,
            )
            validate_path(grid, result.path, start, goal, oracle.cost)
            deterministic, _ = _search_payload(result)
            if deterministic != _plain_tree(stored_summaries[method]):
                raise RunnerError(
                    f"formal replay deterministic result differs: {query_id}/{method}"
                )
    return expected_bindings


def load_complete_run(
    output_directory: str | Path,
    *,
    config_path: str | Path | None = None,
    repository_root: str | Path | None = None,
    freeze_manifest: str | Path | None = None,
) -> LoadedRun:
    """Deeply validate a result; formal evidence additionally requires full replay."""

    unresolved_root = Path(output_directory)
    if unresolved_root.is_symlink():
        raise RunnerError("result root must be a real directory, not a symlink")
    root = unresolved_root.resolve(strict=True)
    if not root.is_dir():
        raise RunnerError("result root must be a real directory, not a symlink")
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise RunnerError("result has no regular manifest completion marker")
    manifest = _object(
        _strict_json_file(manifest_path),
        label="result manifest",
        keys={
            "schema",
            "protocol_id",
            "experiment_split",
            "formal",
            "complete",
            "validation",
            "artifacts",
            "record_counts",
        },
    )
    if (
        manifest["schema"] != MANIFEST_SCHEMA
        or manifest["complete"] is not True
        or manifest["validation"] != "passed"
    ):
        raise RunnerError("result manifest is not a successful completion marker")
    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, dict):
        raise RunnerError("manifest.artifacts must be an object")
    expected_artifacts = {"queries.jsonl", "maps.json", "run.json"}
    if manifest["experiment_split"] == "development" and manifest["formal"] is True:
        expected_artifacts.add("development_freeze_candidate.json")
    if set(artifacts) != expected_artifacts:
        raise RunnerError("manifest artifact inventory is incomplete or unexpected")
    actual_files = {
        child.name
        for child in root.iterdir()
        if child.is_file() and not child.is_symlink()
    }
    if actual_files != expected_artifacts | {"manifest.json"}:
        raise RunnerError(
            "result directory contains missing, extra, or symlinked files"
        )

    payloads: dict[str, bytes] = {}
    for name in sorted(expected_artifacts):
        binding = _object(
            artifacts[name],
            label=f"manifest.artifacts[{name!r}]",
            keys={"sha256", "bytes", "records"},
        )
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise RunnerError(f"artifact is not a regular file: {name}")
        payload = path.read_bytes()
        payloads[name] = payload
        if len(payload) != _plain_int(binding["bytes"], label=f"{name}.bytes"):
            raise RunnerError(f"artifact byte count mismatch: {name}")
        if _sha256_bytes(payload) != _sha(binding["sha256"], label=f"{name}.sha256"):
            raise RunnerError(f"artifact SHA-256 mismatch: {name}")

    queries = _count_jsonl_records(payloads["queries.jsonl"], label="queries.jsonl")
    run = _validate_loaded_run(
        _strict_json_bytes(payloads["run.json"], label="run.json")
    )
    maps, maps_by_name = _validate_loaded_maps(
        _strict_json_bytes(payloads["maps.json"], label="maps.json"), run
    )
    query_count = len(queries)
    map_count = len(maps["maps"])
    search_runs = sum(
        len(row.get("runs", [])) if isinstance(row.get("runs"), list) else -1
        for row in queries
    )
    counts = manifest.get("record_counts")
    if not isinstance(counts, dict) or counts != {
        "maps": map_count,
        "queries": query_count,
        "search_runs": search_runs,
    }:
        raise RunnerError("manifest record counts do not match stored records")
    expected_records = {
        "queries.jsonl": query_count,
        "maps.json": map_count,
        "run.json": 1,
    }
    if "development_freeze_candidate.json" in artifacts:
        expected_records["development_freeze_candidate.json"] = 1
        candidate = _strict_json_bytes(
            payloads["development_freeze_candidate.json"],
            label="development_freeze_candidate.json",
        )
        _validate_candidate(candidate, run=run, manifest_artifacts=artifacts)
    for name, records in expected_records.items():
        if artifacts[name]["records"] != records:
            raise RunnerError(f"artifact record count mismatch: {name}")
    counts = _object(
        run["counts"],
        label="run.counts",
        keys={
            "maps",
            "queries",
            "warmup_repetitions",
            "timed_repetitions",
            "searches_per_query",
            "search_runs",
        },
    )
    if (
        run.get("protocol_id") != manifest["protocol_id"]
        or run.get("experiment_split") != manifest["experiment_split"]
        or run.get("formal") is not manifest["formal"]
        or run.get("status") != "complete"
        or run.get("validation") != "passed"
        or counts["maps"] != map_count
        or counts["queries"] != query_count
        or counts["warmup_repetitions"] != WARMUP_REPETITIONS
        or counts["timed_repetitions"] != TIMED_REPETITIONS
        or counts["searches_per_query"] != SEARCHES_PER_QUERY
        or counts["search_runs"] != search_runs
        or search_runs != query_count * SEARCHES_PER_QUERY
    ):
        raise RunnerError("run.json disagrees with the manifest or stored records")
    if run["formal"]:
        expected_queries = (
            FORMAL_DEVELOPMENT_QUERIES
            if run["experiment_split"] == "development"
            else FORMAL_EVALUATION_QUERIES
        )
        if query_count != expected_queries:
            raise RunnerError("formal result has the wrong exact query count")
    elif not 1 <= query_count <= FORMAL_DEVELOPMENT_QUERIES:
        raise RunnerError("development smoke result has an invalid query count")
    _validate_loaded_queries(queries, run, maps_by_name)
    loaded = LoadedRun(
        root,
        _freeze(manifest),
        _freeze(run),
        _freeze(maps),
        tuple(_freeze(row) for row in queries),
    )
    if run["formal"]:
        if config_path is None or repository_root is None:
            raise RunnerError(
                "formal result loading requires config_path and repository_root replay authority"
            )
        if run["experiment_split"] == "sealed_evaluation" and freeze_manifest is None:
            raise RunnerError(
                "formal sealed-evaluation loading requires freeze_manifest authority"
            )
        if run["experiment_split"] == "development" and freeze_manifest is not None:
            raise RunnerError("freeze_manifest is forbidden for development results")
        replay_root = Path(repository_root).resolve(strict=True)
        replay_config = Path(config_path).resolve(strict=True)
        replayed_bindings = _replay_formal_run(
            loaded,
            config_path=replay_config,
            repository_root=replay_root,
        )
        if run["experiment_split"] == "sealed_evaluation":
            assert freeze_manifest is not None
            authorization = _validate_evaluation_freeze(
                Path(freeze_manifest),
                replayed_bindings,
                config_path=replay_config,
                repository_root=replay_root,
            )
            if authorization != _plain_tree(run["evaluation_authorization"]):
                raise RunnerError(
                    "sealed-evaluation stored authorization differs from the external freeze"
                )
    else:
        if (config_path is None) is not (repository_root is None):
            raise RunnerError(
                "config_path and repository_root must be provided together"
            )
        if freeze_manifest is not None:
            raise RunnerError("freeze_manifest is forbidden for non-formal results")
    return loaded


__all__ = [
    "CANDIDATE_SCHEMA",
    "DEVELOPMENT_AUDIT_SCHEMA",
    "FREEZE_SCHEMA",
    "LoadedRun",
    "MANIFEST_SCHEMA",
    "MAPS_SCHEMA",
    "PLAN_SCHEMA",
    "PROTOCOL_ID",
    "QUERY_SCHEMA",
    "RUN_SCHEMA",
    "RunnerError",
    "load_complete_run",
    "run_split",
]
