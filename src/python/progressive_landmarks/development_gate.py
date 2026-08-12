"""External audit and write-once freeze gate for formal development evidence.

The experiment runner validates its own immutable result format.  This module is
an intentionally separate authorization boundary: it re-verifies the canonical
protocol, demands the exact formal development matrix, checks that every binding
still matches the current repository and machine, emits a detailed audit record,
and only then writes the minimal manifest accepted by sealed evaluation.

No statistic in the development result is used for parameter selection here.
Passing means that the pre-registered experiment executed correctly and is bound
to the current implementation; it is not a claim that any method performed well.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
from typing import Any, Iterable, Mapping, Sequence

from .protocol import (
    METHODS,
    canonical_json_sha256,
    verify_protocol,
)
from .runner import (
    FREEZE_SCHEMA,
    FORMAL_DEVELOPMENT_QUERIES,
    SEARCHES_PER_QUERY,
    TIMED_REPETITIONS,
    WARMUP_REPETITIONS,
    LoadedRun,
    RunnerError,
    load_complete_run,
)
from . import runner as runner_module


AUDIT_SCHEMA = "progressive-landmarks-development-audit-v2"
AUDITOR_ID = "progressive-landmarks-external-development-gate-v2"
FORMAL_DEVELOPMENT_SEARCHES = FORMAL_DEVELOPMENT_QUERIES * SEARCHES_PER_QUERY
EXPECTED_MAPS = 4
EXPECTED_QUERIES_PER_MAP = 40
EXPECTED_WARMUP_REPETITIONS = WARMUP_REPETITIONS
EXPECTED_TIMED_REPETITIONS = TIMED_REPETITIONS
_SHA256_HEX = frozenset("0123456789abcdef")
_REQUIRED_COUNTERS = frozenset(
    {
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
)


class DevelopmentGateError(RuntimeError):
    """Raised when development evidence cannot authorize sealed evaluation."""


@dataclass(frozen=True, slots=True)
class DevelopmentAudit:
    """Canonical audit and authorization payloads ready for write-once storage."""

    audit: Mapping[str, Any]
    freeze: Mapping[str, Any]


def _fail(message: str) -> None:
    raise DevelopmentGateError(message)


def _plain_int(value: Any, *, label: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        _fail(f"{label} must be a plain integer >= {minimum}")
    return value


def _text(value: Any, *, label: str) -> str:
    if type(value) is not str or not value:
        _fail(f"{label} must be a non-empty string")
    return value


def _sha(value: Any, *, label: str) -> str:
    rendered = _text(value, label=label)
    if len(rendered) != 64 or any(
        character not in _SHA256_HEX for character in rendered
    ):
        _fail(f"{label} must be a lowercase SHA-256 digest")
    return rendered


def _object(value: Any, *, label: str, keys: Iterable[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{label} must be an object")
    expected = set(keys)
    actual = set(value)
    if actual != expected:
        _fail(
            f"{label} keys differ: missing={sorted(expected - actual)}, "
            f"unexpected={sorted(actual - expected)}"
        )
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise DevelopmentGateError(f"cannot hash {path}: {error}") from error
    return digest.hexdigest()


def _json_file_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            _plain_value(value),
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("ascii")


def _plain_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_value(item) for item in value]
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail(f"duplicate JSON key in audit: {key!r}")
        result[key] = value
    return result


def _reject_nonfinite(token: str) -> None:
    _fail(f"non-finite JSON number in audit: {token}")


def load_development_audit(path: str | Path) -> dict[str, Any]:
    """Strictly decode and self-hash-check one detailed development audit."""

    audit_path = Path(path)
    try:
        payload = audit_path.read_bytes()
        text = payload.decode("ascii")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except DevelopmentGateError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DevelopmentGateError(
            f"cannot strictly decode audit {audit_path}: {error}"
        ) from error
    audit = _object(
        value,
        label="development audit",
        keys={
            "schema",
            "auditor",
            "status",
            "selection_performed",
            "authorization_recommendation",
            "bindings",
            "development_result",
            "checks",
            "audit_sha256",
        },
    )
    if (
        audit["schema"] != AUDIT_SCHEMA
        or audit["auditor"] != AUDITOR_ID
        or audit["status"] != "passed"
        or audit["selection_performed"] is not False
        or audit["authorization_recommendation"] != "sealed_evaluation"
    ):
        _fail("development audit has an invalid authorization status")
    supplied = _sha(audit["audit_sha256"], label="development audit.audit_sha256")
    core = {key: item for key, item in audit.items() if key != "audit_sha256"}
    if supplied != canonical_json_sha256(core):
        _fail("development audit self-hash does not match its canonical content")
    bindings = _object(
        audit["bindings"],
        label="development audit.bindings",
        keys={
            "protocol_id",
            "config_sha256",
            "plan_sha256",
            "code_sha256",
            "analysis_sha256",
            "analysis_cli_sha256",
            "core_sha256",
            "development_gate_sha256",
            "freeze_cli_sha256",
            "protocol_sha256",
            "runner_sha256",
            "cli_sha256",
            "source_snapshot_sha256",
            "environment_sha256",
        },
    )
    if bindings["protocol_id"] != "progressive_landmarks_v2":
        _fail("development audit protocol binding is invalid")
    for key, item in bindings.items():
        if key != "protocol_id":
            _sha(item, label=f"development audit.bindings.{key}")
    development = _object(
        audit["development_result"],
        label="development audit.development_result",
        keys={
            "manifest_path",
            "manifest_sha256",
            "formal",
            "complete",
            "validation",
            "map_count",
            "query_count",
            "search_run_count",
            "artifact_bindings",
        },
    )
    _text(development["manifest_path"], label="development_result.manifest_path")
    _sha(development["manifest_sha256"], label="development_result.manifest_sha256")
    if (
        development["formal"] is not True
        or development["complete"] is not True
        or development["validation"] != "passed"
        or development["map_count"] != EXPECTED_MAPS
        or development["query_count"] != FORMAL_DEVELOPMENT_QUERIES
        or development["search_run_count"] != FORMAL_DEVELOPMENT_SEARCHES
        or not isinstance(development["artifact_bindings"], Mapping)
    ):
        _fail("development audit result evidence is not the exact formal matrix")
    artifacts = _object(
        development["artifact_bindings"],
        label="development audit.development_result.artifact_bindings",
        keys={
            "queries.jsonl",
            "maps.json",
            "run.json",
            "development_freeze_candidate.json",
        },
    )
    expected_records = {
        "queries.jsonl": FORMAL_DEVELOPMENT_QUERIES,
        "maps.json": EXPECTED_MAPS,
        "run.json": 1,
        "development_freeze_candidate.json": 1,
    }
    for name, raw_binding in artifacts.items():
        binding = _object(
            raw_binding,
            label=f"development audit artifact {name}",
            keys={"sha256", "bytes", "records"},
        )
        _sha(binding["sha256"], label=f"development audit artifact {name}.sha256")
        _plain_int(
            binding["bytes"],
            label=f"development audit artifact {name}.bytes",
            minimum=1,
        )
        if binding["records"] != expected_records[name]:
            _fail(f"development audit artifact {name} has the wrong record count")
    checks = _object(
        audit["checks"],
        label="development audit.checks",
        keys={
            "verified_protocol",
            "canonical_plan_sha256",
            "canonical_query_identity_and_order",
            "formal_four_map_coverage",
            "exact_one_plus_eight_rotations",
            "every_method_and_repetition_present",
            "costs_match_bfs_and_paths_validated",
            "formal_replay_passed",
            "deterministic_summaries_match_repetitions",
            "full_landmark_expansion_digests_match",
            "stage_timing_disabled",
            "search_timings_positive",
            "counter_schemas_and_identities_valid",
            "candidate_and_current_bindings_match",
            "per_map_query_counts",
            "method_invocation_counts",
            "phase_invocation_counts",
            "timed_observations_per_query_method",
            "counter_totals_by_method",
        },
    )
    for key in {
        "verified_protocol",
        "canonical_query_identity_and_order",
        "exact_one_plus_eight_rotations",
        "every_method_and_repetition_present",
        "costs_match_bfs_and_paths_validated",
        "formal_replay_passed",
        "deterministic_summaries_match_repetitions",
        "full_landmark_expansion_digests_match",
        "stage_timing_disabled",
        "search_timings_positive",
        "counter_schemas_and_identities_valid",
        "candidate_and_current_bindings_match",
    }:
        if checks[key] is not True:
            _fail(f"development audit check did not pass: {key}")
    _sha(checks["canonical_plan_sha256"], label="checks.canonical_plan_sha256")
    coverage = checks["formal_four_map_coverage"]
    per_map = checks["per_map_query_counts"]
    method_counts = checks["method_invocation_counts"]
    phase_counts = checks["phase_invocation_counts"]
    counter_totals = checks["counter_totals_by_method"]
    if (
        not isinstance(coverage, list)
        or len(coverage) != EXPECTED_MAPS
        or len(set(coverage)) != EXPECTED_MAPS
        or not isinstance(per_map, Mapping)
        or set(per_map) != set(coverage)
        or set(per_map.values()) != {EXPECTED_QUERIES_PER_MAP}
        or not isinstance(method_counts, Mapping)
        or set(method_counts) != set(METHODS)
        or set(method_counts.values()) != {FORMAL_DEVELOPMENT_QUERIES * 9}
        or phase_counts
        != {
            "warmup": FORMAL_DEVELOPMENT_QUERIES * len(METHODS),
            "timed": FORMAL_DEVELOPMENT_QUERIES
            * EXPECTED_TIMED_REPETITIONS
            * len(METHODS),
        }
        or checks["timed_observations_per_query_method"] != EXPECTED_TIMED_REPETITIONS
        or not isinstance(counter_totals, Mapping)
        or set(counter_totals) != set(METHODS)
    ):
        _fail("development audit detailed checks have an invalid shape or total")
    for method, totals in counter_totals.items():
        _object(
            totals,
            label=f"development audit counter totals {method}",
            keys=_REQUIRED_COUNTERS,
        )
        for name, item in totals.items():
            _plain_int(item, label=f"counter totals {method}.{name}")
    if checks["canonical_plan_sha256"] != bindings["plan_sha256"]:
        _fail("development audit canonical plan check differs from its binding")
    return dict(audit)


def _safe_manifest_relative_path(development_manifest: Path, freeze_path: Path) -> str:
    """Return the runner-compatible child path from freeze to development manifest."""

    try:
        relative = development_manifest.relative_to(freeze_path.parent)
    except ValueError as error:
        raise DevelopmentGateError(
            "development manifest must be at or below the freeze directory; "
            "place the freeze directly above the immutable development result"
        ) from error
    rendered = relative.as_posix()
    pure = PurePosixPath(rendered)
    if (
        not rendered
        or rendered != pure.as_posix()
        or pure.is_absolute()
        or pure.name != "manifest.json"
        or len(pure.parts) < 2
        or "\\" in rendered
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        _fail(
            "development manifest path must be a canonical child POSIX path "
            "ending in manifest.json"
        )
    return rendered


def _safe_audit_relative_path(audit_path: Path, freeze_path: Path) -> str:
    try:
        relative = audit_path.relative_to(freeze_path.parent)
    except ValueError as error:
        raise DevelopmentGateError(
            "development audit must be at or below the freeze directory"
        ) from error
    rendered = relative.as_posix()
    pure = PurePosixPath(rendered)
    if (
        not rendered
        or rendered != pure.as_posix()
        or pure.is_absolute()
        or pure.name != "development_audit.json"
        or "\\" in rendered
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        _fail(
            "audit path must be a canonical relative child path ending in "
            "development_audit.json"
        )
    return rendered


def _within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


def _expected_schedule(plan: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    timing = _object(
        plan.get("timing_orders"),
        label="plan.timing_orders",
        keys={"warmup", "timed"},
    )
    combined: list[Mapping[str, Any]] = []
    for phase, count, timed in (
        ("warmup", EXPECTED_WARMUP_REPETITIONS, False),
        ("timed", EXPECTED_TIMED_REPETITIONS, True),
    ):
        rows = timing[phase]
        if not isinstance(rows, list) or len(rows) != count:
            _fail(f"canonical plan does not contain exact {phase} repetitions")
        for repetition, raw_row in enumerate(rows):
            row = _object(
                raw_row,
                label=f"plan.timing_orders.{phase}[{repetition}]",
                keys={"repetition", "timed", "methods"},
            )
            offset = 0 if phase == "warmup" else repetition % len(METHODS)
            expected_methods = list(METHODS[offset:] + METHODS[:offset])
            if (
                row["repetition"] != repetition
                or row["timed"] is not timed
                or row["methods"] != expected_methods
            ):
                _fail(f"canonical plan {phase} rotation differs from v2")
            combined.append(
                {
                    "phase": phase,
                    "repetition": repetition,
                    "timed": timed,
                    "methods": expected_methods,
                }
            )
    return tuple(combined)


def _expected_development_queries(
    plan: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    rows = plan.get("queries")
    if not isinstance(rows, list):
        _fail("canonical plan queries must be an array")
    selected = tuple(
        row
        for row in rows
        if isinstance(row, Mapping) and row.get("experiment_split") == "development"
    )
    if len(selected) != FORMAL_DEVELOPMENT_QUERIES:
        _fail("canonical plan does not contain exactly 160 development queries")
    return selected


def _query_without_split(query: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in query.items() if key != "experiment_split"}


def _expected_freeze_bindings(run_bindings: Mapping[str, Any]) -> dict[str, Any]:
    """Use the runner's single authoritative extraction contract."""

    try:
        value = runner_module._freeze_binding_values(run_bindings)
    except (KeyError, TypeError, RunnerError) as error:
        raise DevelopmentGateError(
            f"cannot derive sealed-evaluation bindings from development run: {error}"
        ) from error
    expected_keys = {
        "protocol_id",
        "config_sha256",
        "plan_sha256",
        "code_sha256",
        "analysis_sha256",
        "analysis_cli_sha256",
        "core_sha256",
        "development_gate_sha256",
        "freeze_cli_sha256",
        "protocol_sha256",
        "runner_sha256",
        "cli_sha256",
        "source_snapshot_sha256",
        "environment_sha256",
    }
    _object(value, label="freeze bindings", keys=expected_keys)
    for key, item in value.items():
        if key == "protocol_id":
            _text(item, label=f"freeze bindings.{key}")
        else:
            _sha(item, label=f"freeze bindings.{key}")
    return value


def _current_bindings(plan: Mapping[str, Any], repository_root: Path) -> dict[str, Any]:
    """Obtain the runner's current development-side binding construction."""

    try:
        current = runner_module._current_bindings(plan, repository_root, "development")
    except (KeyError, TypeError, RunnerError, OSError) as error:
        raise DevelopmentGateError(
            f"cannot construct current bindings: {error}"
        ) from error
    return _expected_freeze_bindings(current)


def _validate_manifest_summary(loaded: LoadedRun) -> None:
    manifest = loaded.manifest
    if (
        manifest.get("experiment_split") != "development"
        or manifest.get("formal") is not True
        or manifest.get("complete") is not True
        or manifest.get("validation") != "passed"
        or manifest.get("record_counts")
        != {
            "maps": EXPECTED_MAPS,
            "queries": FORMAL_DEVELOPMENT_QUERIES,
            "search_runs": FORMAL_DEVELOPMENT_SEARCHES,
        }
    ):
        _fail("manifest is not the exact successful formal development matrix")


def _validate_run_summary(
    loaded: LoadedRun,
    plan: Mapping[str, Any],
    expected_schedule: Sequence[Mapping[str, Any]],
) -> None:
    run = loaded.run
    expected_counts = {
        "maps": EXPECTED_MAPS,
        "queries": FORMAL_DEVELOPMENT_QUERIES,
        "warmup_repetitions": EXPECTED_WARMUP_REPETITIONS,
        "timed_repetitions": EXPECTED_TIMED_REPETITIONS,
        "searches_per_query": SEARCHES_PER_QUERY,
        "search_runs": FORMAL_DEVELOPMENT_SEARCHES,
    }
    if (
        run.get("protocol_id") != plan.get("protocol_id")
        or run.get("experiment_split") != "development"
        or run.get("formal") is not True
        or run.get("nonformal_smoke") is not False
        or run.get("status") != "complete"
        or run.get("validation") != "passed"
        or list(run.get("methods", ())) != list(METHODS)
        or _plain_value(run.get("schedule", ())) != _plain_value(expected_schedule)
        or dict(run.get("counts", {})) != expected_counts
        or run.get("evaluation_authorization") is not None
    ):
        _fail("run metadata differs from the exact formal development protocol")
    plan_landmarks = plan.get("landmarks")
    if not isinstance(plan_landmarks, Mapping) or dict(run.get("landmarks", {})) != {
        "full_pivots": plan_landmarks.get("full_pivots"),
        "staged_prefix_pivots": plan_landmarks.get("staged_prefix_pivots"),
    }:
        _fail("run landmark configuration differs from the verified plan")


def _validate_map_coverage(
    loaded: LoadedRun, plan: Mapping[str, Any]
) -> tuple[str, ...]:
    expected_bindings = [
        row
        for row in plan.get("input_bindings", ())
        if isinstance(row, Mapping) and row.get("experiment_split") == "development"
    ]
    expected = [PurePosixPath(row["map"]["path"]).name for row in expected_bindings]
    observed_rows = loaded.maps.get("maps")
    if not isinstance(observed_rows, tuple) or len(observed_rows) != EXPECTED_MAPS:
        _fail("maps artifact does not contain exactly four development maps")
    observed = [row.get("map") for row in observed_rows]
    if observed != expected or len(set(observed)) != EXPECTED_MAPS:
        _fail("map coverage or order differs from the canonical plan")
    for row, binding in zip(observed_rows, expected_bindings, strict=True):
        expected_map = binding["map"]
        if (
            row.get("family") != binding["family"]
            or row.get("source_split") != "train"
            or row.get("map_sha256") != expected_map["sha256"]
            or row.get("width") != expected_map["width"]
            or row.get("height") != expected_map["height"]
            or row.get("traversable_states") != expected_map["traversable_states"]
            or row.get("requested_landmarks") != 32
            or row.get("actual_landmarks")
            != min(32, expected_map["traversable_states"])
        ):
            _fail(f"map evidence differs from canonical binding: {row.get('map')!r}")
    return tuple(observed)


def _validate_counters(
    counters: Mapping[str, Any], *, method: str, query_id: str
) -> None:
    _object(counters, label=f"{query_id}/{method}.counters", keys=_REQUIRED_COUNTERS)
    for name, value in counters.items():
        _plain_int(value, label=f"{query_id}/{method}.counters.{name}")
    if (
        counters["reopened"] != 0
        or counters["unique_discovered"] < 2
        or counters["expanded"] < 1
        or counters["generated"] < counters["relaxations"]
        or counters["unique_discovered"] > counters["relaxations"] + 1
        or counters["pops"] - counters["stale_pops"]
        != counters["expanded"] + counters["requeues"] + 1
        or counters["stale_pops"] > counters["pops"]
        or counters["heuristic_cache_hits"] > counters["relaxations"]
        or counters["manhattan_calls"] != counters["unique_discovered"]
        or counters["max_open_entries"] < 1
        or counters["max_live_states"] < 1
        or counters["max_live_states"] > counters["unique_discovered"]
    ):
        _fail(f"{query_id}/{method}: search counters violate basic identities/ranges")

    prefix = counters["prefix_calls"]
    suffix = counters["suffix_calls"]
    full = counters["full_calls"]
    pivots = counters["pivot_evaluations"]
    reads = counters["distance_table_reads"]
    if method == "manhattan":
        if any((prefix, suffix, full, pivots, reads, counters["requeues"])):
            _fail(f"{query_id}: Manhattan run contains landmark work")
    else:
        if (
            reads != 32 + pivots
            or counters["manhattan_calls"] != counters["unique_discovered"]
        ):
            _fail(f"{query_id}/{method}: landmark read/call accounting is inconsistent")
        if method == "eager_full":
            if (
                prefix
                or suffix
                or counters["requeues"]
                or full != counters["unique_discovered"]
                or pivots != full * 32
            ):
                _fail(f"{query_id}: eager-full counter identity failed")
        elif method == "lazy_full":
            if (
                prefix
                or suffix
                or pivots != full * 32
                or full != counters["requeues"] + 1
            ):
                _fail(f"{query_id}: lazy-full counter identity failed")
        elif method == "staged":
            if full or pivots != prefix * 4 + suffix * 28:
                _fail(f"{query_id}: staged pivot counter identity failed")
            if prefix < 1 or suffix < 1 or counters["requeues"] != prefix + suffix - 2:
                _fail(f"{query_id}: staged requeue/call counter identity failed")


def _validate_queries(
    loaded: LoadedRun,
    expected_queries: Sequence[Mapping[str, Any]],
    expected_schedule: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if len(loaded.queries) != FORMAL_DEVELOPMENT_QUERIES:
        _fail("development result does not contain exactly 160 query rows")
    per_map: Counter[str] = Counter()
    method_invocations: Counter[str] = Counter()
    phase_invocations: Counter[str] = Counter()
    timed_observations: Counter[tuple[str, str]] = Counter()
    counter_totals: dict[str, dict[str, int]] = {
        method: {name: 0 for name in sorted(_REQUIRED_COUNTERS)} for method in METHODS
    }

    for index, (row, planned) in enumerate(
        zip(loaded.queries, expected_queries, strict=True)
    ):
        query_id = planned["query_id"]
        if (
            row.get("sequence_index") != index
            or row.get("experiment_split") != "development"
            or _plain_value(row.get("query", {})) != _query_without_split(planned)
        ):
            _fail(f"query identity/order differs from canonical plan at index {index}")
        per_map[planned["map"]] += 1
        oracle = row.get("oracle")
        if (
            not isinstance(oracle, Mapping)
            or _plain_int(
                oracle.get("cost"), label=f"{query_id}.oracle.cost", minimum=1
            )
            < 1
        ):
            _fail(f"{query_id}: BFS oracle evidence is invalid")
        validation = row.get("validation")
        if not isinstance(validation, Mapping) or set(validation.values()) != {True}:
            _fail(f"{query_id}: validation flags are not unanimously true")

        summaries = row.get("deterministic_by_method")
        if not isinstance(summaries, Mapping) or set(summaries) != set(METHODS):
            _fail(f"{query_id}: deterministic method summaries are incomplete")
        full_digests = {
            summaries[method].get("expansion_digest")
            for method in ("eager_full", "lazy_full", "staged")
        }
        if len(full_digests) != 1:
            _fail(f"{query_id}: full-landmark expansion digests differ")
        for method in METHODS:
            summary = summaries[method]
            if (
                not isinstance(summary, Mapping)
                or summary.get("mode") != method
                or summary.get("found") is not True
                or summary.get("cost") != oracle["cost"]
            ):
                _fail(f"{query_id}/{method}: deterministic summary is invalid")
            _sha(summary.get("path_sha256"), label=f"{query_id}/{method}.path_sha256")
            _sha(
                summary.get("expansion_digest"),
                label=f"{query_id}/{method}.expansion_digest",
            )
            counters = summary.get("counters")
            if not isinstance(counters, Mapping):
                _fail(f"{query_id}/{method}: missing counter object")
            _validate_counters(counters, method=method, query_id=query_id)
            for name, value in counters.items():
                counter_totals[method][name] += value

        invocations = row.get("runs")
        if not isinstance(invocations, tuple) or len(invocations) != SEARCHES_PER_QUERY:
            _fail(
                f"{query_id}: invocation matrix is not exactly "
                f"{SEARCHES_PER_QUERY} rows"
            )
        cursor = 0
        for schedule_row in expected_schedule:
            for order_position, method in enumerate(schedule_row["methods"]):
                invocation = invocations[cursor]
                expected_metadata = {
                    "phase": schedule_row["phase"],
                    "repetition": schedule_row["repetition"],
                    "timed": schedule_row["timed"],
                    "order_position": order_position,
                    "method": method,
                }
                if any(
                    invocation.get(key) != value
                    for key, value in expected_metadata.items()
                ):
                    _fail(f"{query_id}: invocation order differs at position {cursor}")
                timing = invocation.get("timing_ns")
                if not isinstance(timing, Mapping) or set(timing) != {
                    "stage_ns",
                    "search_ns",
                }:
                    _fail(f"{query_id}/{method}: timing object has the wrong schema")
                if (
                    _plain_int(timing["stage_ns"], label="timing.stage_ns") != 0
                    or _plain_int(
                        timing["search_ns"], label="timing.search_ns", minimum=1
                    )
                    < 1
                ):
                    _fail(f"{query_id}/{method}: primary timing contract failed")
                if invocation.get("result") != summaries[method]:
                    _fail(f"{query_id}/{method}: invocation differs from summary")
                method_invocations[method] += 1
                phase_invocations[schedule_row["phase"]] += 1
                if schedule_row["timed"]:
                    timed_observations[(query_id, method)] += 1
                cursor += 1

    if (
        set(per_map.values()) != {EXPECTED_QUERIES_PER_MAP}
        or len(per_map) != EXPECTED_MAPS
    ):
        _fail("development queries do not cover four maps with 40 queries each")
    repetitions = EXPECTED_WARMUP_REPETITIONS + EXPECTED_TIMED_REPETITIONS
    if method_invocations != Counter(
        {method: FORMAL_DEVELOPMENT_QUERIES * repetitions for method in METHODS}
    ):
        _fail("method invocation totals differ from the exact 160-query design")
    if phase_invocations != Counter(
        {
            "warmup": FORMAL_DEVELOPMENT_QUERIES
            * EXPECTED_WARMUP_REPETITIONS
            * len(METHODS),
            "timed": FORMAL_DEVELOPMENT_QUERIES
            * EXPECTED_TIMED_REPETITIONS
            * len(METHODS),
        }
    ):
        _fail("warmup/timed invocation totals differ from exact design")
    if set(timed_observations.values()) != {EXPECTED_TIMED_REPETITIONS} or len(
        timed_observations
    ) != FORMAL_DEVELOPMENT_QUERIES * len(METHODS):
        _fail(
            "timed repetition coverage is not exactly "
            f"{EXPECTED_TIMED_REPETITIONS} per query/method"
        )
    return {
        "per_map_query_counts": dict(sorted(per_map.items())),
        "method_invocation_counts": dict(method_invocations),
        "phase_invocation_counts": dict(phase_invocations),
        "timed_observations_per_query_method": EXPECTED_TIMED_REPETITIONS,
        "counter_totals_by_method": counter_totals,
    }


def audit_formal_development(
    development_result: str | Path,
    *,
    config_path: str | Path,
    repository_root: str | Path,
    audit_path: str | Path,
    freeze_path: str | Path,
) -> DevelopmentAudit:
    """Audit a formal development result and build, but do not persist, authorization."""

    root = Path(repository_root).resolve(strict=True)
    config = Path(config_path).resolve(strict=True)
    output_audit = Path(audit_path).resolve(strict=False)
    output_freeze = Path(freeze_path).resolve(strict=False)
    if output_audit == output_freeze:
        _fail("audit_path and freeze_path must be distinct")
    if not output_freeze.name or output_freeze.name == "manifest.json":
        _fail("freeze_path must identify a dedicated JSON authorization file")
    try:
        plan = verify_protocol(config, repository_root=root)
    except Exception as error:
        raise DevelopmentGateError(
            f"canonical protocol verification failed: {error}"
        ) from error
    expected_queries = _expected_development_queries(plan)
    schedule = _expected_schedule(plan)
    try:
        loaded = load_complete_run(
            development_result,
            config_path=config,
            repository_root=root,
        )
    except (RunnerError, OSError) as error:
        raise DevelopmentGateError(
            f"development result validation failed: {error}"
        ) from error

    development_manifest = loaded.root / "manifest.json"
    if _within(output_audit, loaded.root) or _within(output_freeze, loaded.root):
        _fail(
            "audit and freeze outputs must be outside the immutable development result"
        )
    relative_manifest = _safe_manifest_relative_path(
        development_manifest, output_freeze
    )
    relative_audit = _safe_audit_relative_path(output_audit, output_freeze)
    _validate_manifest_summary(loaded)
    _validate_run_summary(loaded, plan, schedule)
    maps = _validate_map_coverage(loaded, plan)
    query_audit = _validate_queries(loaded, expected_queries, schedule)

    plan_hash = _sha(plan.get("plan_sha256"), label="plan.plan_sha256")
    run_bindings = loaded.run.get("bindings")
    if not isinstance(run_bindings, Mapping):
        _fail("development run has no binding object")
    frozen_bindings = _expected_freeze_bindings(run_bindings)
    current_bindings = _current_bindings(plan, root)
    if frozen_bindings != current_bindings:
        differing = sorted(
            key
            for key in frozen_bindings
            if frozen_bindings.get(key) != current_bindings.get(key)
        )
        _fail(
            f"development bindings differ from current repository/environment: {differing}"
        )
    if frozen_bindings["plan_sha256"] != plan_hash:
        _fail("development run is not bound to the currently verified canonical plan")

    manifest_sha = _sha256_file(development_manifest)
    audit_core = {
        "schema": AUDIT_SCHEMA,
        "auditor": AUDITOR_ID,
        "status": "passed",
        "selection_performed": False,
        "authorization_recommendation": "sealed_evaluation",
        "bindings": frozen_bindings,
        "development_result": {
            "manifest_path": relative_manifest,
            "manifest_sha256": manifest_sha,
            "formal": True,
            "complete": True,
            "validation": "passed",
            "map_count": EXPECTED_MAPS,
            "query_count": FORMAL_DEVELOPMENT_QUERIES,
            "search_run_count": FORMAL_DEVELOPMENT_SEARCHES,
            "artifact_bindings": _plain_value(loaded.manifest["artifacts"]),
        },
        "checks": {
            "verified_protocol": True,
            "canonical_plan_sha256": plan_hash,
            "canonical_query_identity_and_order": True,
            "formal_four_map_coverage": list(maps),
            "exact_one_plus_eight_rotations": True,
            "every_method_and_repetition_present": True,
            "costs_match_bfs_and_paths_validated": True,
            "formal_replay_passed": True,
            "deterministic_summaries_match_repetitions": True,
            "full_landmark_expansion_digests_match": True,
            "stage_timing_disabled": True,
            "search_timings_positive": True,
            "counter_schemas_and_identities_valid": True,
            "candidate_and_current_bindings_match": True,
            **query_audit,
        },
    }
    audit = {**audit_core, "audit_sha256": canonical_json_sha256(audit_core)}
    audit_payload = _json_file_bytes(audit)
    freeze = {
        "schema": FREEZE_SCHEMA,
        "authorization": "sealed_evaluation",
        "issued_by": AUDITOR_ID,
        "bindings": frozen_bindings,
        "development_audit": {
            "path": relative_audit,
            "sha256": hashlib.sha256(audit_payload).hexdigest(),
            "schema": AUDIT_SCHEMA,
        },
        "development_result": {
            "manifest_path": relative_manifest,
            "manifest_sha256": manifest_sha,
            "formal": True,
            "complete": True,
            "validation": "passed",
            "map_count": EXPECTED_MAPS,
            "query_count": FORMAL_DEVELOPMENT_QUERIES,
            "search_run_count": FORMAL_DEVELOPMENT_SEARCHES,
            "artifact_bindings": _plain_value(loaded.manifest["artifacts"]),
        },
    }
    return DevelopmentAudit(audit, freeze)


def _atomic_write_once(path: Path, payload: bytes) -> None:
    """Publish one file atomically, refusing every pre-existing filesystem entry."""

    if os.path.lexists(os.fspath(path)):
        _fail(f"refusing to overwrite existing artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor: int | None = None
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.staging-", dir=path.parent
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as target:
            descriptor = None
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        if os.path.lexists(os.fspath(path)):
            _fail(f"refusing to overwrite raced artifact: {path}")
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise DevelopmentGateError(
                f"refusing to overwrite raced artifact: {path}"
            ) from error
        except OSError as error:
            raise DevelopmentGateError(
                f"cannot atomically publish {path}: {error}"
            ) from error
    except OSError as error:
        raise DevelopmentGateError(f"cannot persist {path}: {error}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def freeze_formal_development(
    development_result: str | Path,
    *,
    config_path: str | Path,
    repository_root: str | Path,
    audit_path: str | Path,
    freeze_path: str | Path,
) -> DevelopmentAudit:
    """Audit and atomically publish detailed audit plus write-once authorization."""

    resolved_audit = Path(audit_path).resolve(strict=False)
    resolved_freeze = Path(freeze_path).resolve(strict=False)
    if resolved_audit == resolved_freeze:
        _fail("audit_path and freeze_path must be distinct")
    if os.path.lexists(os.fspath(resolved_audit)) or os.path.lexists(
        os.fspath(resolved_freeze)
    ):
        _fail("audit and freeze outputs are write-once and must both be absent")
    result = audit_formal_development(
        development_result,
        config_path=config_path,
        repository_root=repository_root,
        audit_path=resolved_audit,
        freeze_path=resolved_freeze,
    )
    _atomic_write_once(resolved_audit, _json_file_bytes(result.audit))
    try:
        if load_development_audit(resolved_audit) != _plain_value(result.audit):
            _fail("persisted development audit differs from the audited value")
        _atomic_write_once(resolved_freeze, _json_file_bytes(result.freeze))
    except Exception:
        try:
            resolved_audit.unlink()
        except OSError:
            pass
        raise
    return result


__all__ = [
    "AUDIT_SCHEMA",
    "AUDITOR_ID",
    "DevelopmentAudit",
    "DevelopmentGateError",
    "audit_formal_development",
    "freeze_formal_development",
    "load_development_audit",
]
