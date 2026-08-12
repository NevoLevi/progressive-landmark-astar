"""Prospectively frozen analysis for the progressive-landmarks experiment.

The module deliberately separates three phases:

1. replay and validate the complete development/evaluation evidence;
2. construct every statistic in memory; and
3. publish a deterministic, manifest-last analysis directory.

No timing value is inspected by the analysis routines until the correctness,
query-identity, schedule, digest, counter, authorization, and current-binding
gates have all passed.  Timed repetitions are reduced to one even-sample
median per query and method; repetitions are never treated as experimental
units.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import csv
from dataclasses import dataclass
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import random
import shutil
import statistics
import tempfile
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from . import runner as runner_module
from .development_gate import (
    AUDIT_SCHEMA,
    AUDITOR_ID,
    DevelopmentGateError,
    load_development_audit,
)
from .protocol import METHODS, canonical_json_sha256, verify_protocol
from .runner import (
    FORMAL_DEVELOPMENT_QUERIES,
    FORMAL_EVALUATION_QUERIES,
    FREEZE_SCHEMA,
    SEARCHES_PER_QUERY,
    TIMED_REPETITIONS,
    LoadedRun,
    RunnerError,
    load_complete_run,
)


ANALYZER_ID = "progressive-landmarks-prospective-analysis-v2"
ANALYSIS_MANIFEST_SCHEMA = "progressive-landmarks-analysis-manifest-v2"
SUMMARY_SCHEMA = "progressive-landmarks-analysis-summary-v2"
PROVENANCE_SCHEMA = "progressive-landmarks-analysis-provenance-v2"
BOOTSTRAP_REPLICATES = 10_000
BOOTSTRAP_SEED = 23_725_513
AMORTIZATION_QUERIES = (1, 10, 100, 1000)
EXPECTED_EVALUATION_MAPS = 8
EXPECTED_QUERIES_PER_MAP = 100
EXPECTED_FAMILIES = ("maze", "random", "room", "warehouse")
FIGURE_STEMS = (
    "stage_schematic",
    "per_map_time_ratios",
    "saved_work_vs_time",
    "family_mechanism_decomposition",
    "preprocessing_amortization",
)
COUNTER_NAMES = (
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
)
_SHA256_HEX = frozenset("0123456789abcdef")


class AnalysisError(RuntimeError):
    """Raised when evidence cannot safely be analyzed or published."""


@dataclass(frozen=True, slots=True)
class AnalysisInputs:
    plan: Mapping[str, Any]
    evaluation: LoadedRun
    development: LoadedRun
    freeze: Mapping[str, Any]
    audit: Mapping[str, Any]
    freeze_path: Path
    audit_path: Path
    development_manifest: Path


@dataclass(frozen=True, slots=True)
class LoadedAnalysis:
    root: Path
    manifest: Mapping[str, Any]
    summary: Mapping[str, Any]
    provenance: Mapping[str, Any]


def _fail(message: str) -> None:
    raise AnalysisError(message)


def _plain_int(value: Any, *, label: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        _fail(f"{label} must be a plain integer >= {minimum}")
    return value


def _text(value: Any, *, label: str) -> str:
    if type(value) is not str or not value:
        _fail(f"{label} must be a non-empty string")
    return value


def _sha(value: Any, *, label: str) -> str:
    digest = _text(value, label=label)
    if len(digest) != 64 or any(character not in _SHA256_HEX for character in digest):
        _fail(f"{label} must be a lowercase SHA-256 digest")
    return digest


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


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_nonfinite(token: str) -> None:
    _fail(f"non-finite JSON number: {token}")


def _strict_json_bytes(payload: bytes, *, label: str) -> Any:
    try:
        return json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except AnalysisError:
        raise
    except (UnicodeError, json.JSONDecodeError) as error:
        raise AnalysisError(f"cannot strictly decode {label}: {error}") from error


def _strict_json_file(path: Path) -> Any:
    try:
        return _strict_json_bytes(path.read_bytes(), label=str(path))
    except OSError as error:
        raise AnalysisError(f"cannot read {path}: {error}") from error


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise AnalysisError(f"cannot hash {path}: {error}") from error
    return digest.hexdigest()


def _json_bytes(value: Any) -> bytes:
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


def _write_new(path: Path, payload: bytes) -> None:
    try:
        with path.open("xb") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
    except OSError as error:
        raise AnalysisError(f"cannot persist {path}: {error}") from error


def _lexists(path: Path) -> bool:
    return os.path.lexists(os.fspath(path))


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _freeze_value(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_value(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    return value


def even_sample_median(values: Sequence[int | float]) -> float:
    """Return the ordinary median of a nonempty, even-sized finite sample."""

    if not values or len(values) % 2:
        _fail("even-sample median requires a nonempty even number of values")
    checked: list[float] = []
    for index, value in enumerate(values):
        if type(value) not in {int, float} or not math.isfinite(value):
            _fail(f"median value {index} must be a finite plain number")
        checked.append(float(value))
    checked.sort()
    midpoint = len(checked) // 2
    return (checked[midpoint - 1] + checked[midpoint]) / 2.0


def _quantile(values: Sequence[int | float], probability: float) -> float:
    """Hyndman-Fan type-7 quantile (the common linear empirical quantile)."""

    if not values:
        _fail("quantile requires at least one value")
    if not 0.0 <= probability <= 1.0:
        _fail("quantile probability must lie in [0,1]")
    ordered = sorted(float(value) for value in values)
    if any(not math.isfinite(value) for value in ordered):
        _fail("quantile values must be finite")
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _median(values: Sequence[int | float]) -> float:
    if not values:
        _fail("median requires at least one value")
    return float(statistics.median(values))


def distribution_summary(values: Sequence[int | float]) -> dict[str, Any]:
    """Return one JSON-safe seven-number descriptive summary."""

    if not values:
        _fail("distribution summary requires at least one value")
    checked = [float(value) for value in values]
    if any(not math.isfinite(value) for value in checked):
        _fail("distribution values must be finite")
    return {
        "n": len(checked),
        "mean": math.fsum(checked) / len(checked),
        "median": _median(checked),
        "q1": _quantile(checked, 0.25),
        "q3": _quantile(checked, 0.75),
        "min": min(checked),
        "max": max(checked),
    }


def _average_ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: (values[index], index))
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor + 1
        while end < len(order) and values[order[end]] == values[order[cursor]]:
            end += 1
        average = ((cursor + 1) + end) / 2.0
        for position in range(cursor, end):
            ranks[order[position]] = average
        cursor = end
    return ranks


def spearman_rho(xs: Sequence[int | float], ys: Sequence[int | float]) -> float | None:
    """Return tie-corrected Spearman rho, or ``None`` for a constant variable."""

    if len(xs) != len(ys) or len(xs) < 2:
        _fail("Spearman inputs must have equal length >= 2")
    x_values = [float(value) for value in xs]
    y_values = [float(value) for value in ys]
    if any(not math.isfinite(value) for value in x_values + y_values):
        _fail("Spearman inputs must be finite")
    x_ranks = _average_ranks(x_values)
    y_ranks = _average_ranks(y_values)
    x_mean = math.fsum(x_ranks) / len(x_ranks)
    y_mean = math.fsum(y_ranks) / len(y_ranks)
    numerator = math.fsum(
        (x_rank - x_mean) * (y_rank - y_mean)
        for x_rank, y_rank in zip(x_ranks, y_ranks, strict=True)
    )
    x_ss = math.fsum((rank - x_mean) ** 2 for rank in x_ranks)
    y_ss = math.fsum((rank - y_mean) ** 2 for rank in y_ranks)
    if x_ss == 0.0 or y_ss == 0.0:
        return None
    return numerator / math.sqrt(x_ss * y_ss)


def hierarchical_bootstrap_median(
    values_by_map: Mapping[str, Sequence[int | float]],
    *,
    replicates: int = BOOTSTRAP_REPLICATES,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Bootstrap a median by sampling maps, then queries within sampled maps."""

    _plain_int(replicates, label="bootstrap replicates", minimum=1)
    _plain_int(seed, label="bootstrap seed")
    if not values_by_map:
        _fail("hierarchical bootstrap requires at least one map")
    map_names = sorted(values_by_map)
    checked: dict[str, tuple[float, ...]] = {}
    for name in map_names:
        values = tuple(float(value) for value in values_by_map[name])
        if not values or any(not math.isfinite(value) for value in values):
            _fail(f"bootstrap cluster {name!r} must contain finite values")
        checked[name] = values
    observed_values = [value for name in map_names for value in checked[name]]
    observed = _median(observed_values)
    generator = random.Random(seed)
    estimates: list[float] = []
    for _ in range(replicates):
        sample: list[float] = []
        for _cluster_position in map_names:
            selected_name = map_names[generator.randrange(len(map_names))]
            cluster = checked[selected_name]
            sample.extend(cluster[generator.randrange(len(cluster))] for _ in cluster)
        estimates.append(_median(sample))
    return {
        "estimand": "median paired log(staged/lazy_full search-time ratio)",
        "point_log_ratio": observed,
        "point_ratio": math.exp(observed),
        "ci95_log_ratio": [
            _quantile(estimates, 0.025),
            _quantile(estimates, 0.975),
        ],
        "ci95_ratio": [
            math.exp(_quantile(estimates, 0.025)),
            math.exp(_quantile(estimates, 0.975)),
        ],
        "bootstrap_probability_ratio_below_one": math.fsum(
            estimate < 0.0 for estimate in estimates
        )
        / replicates,
        "replicates": replicates,
        "seed": seed,
        "interval_method": "percentile interval with type-7 quantile endpoints",
        "top_level_clusters": len(map_names),
        "query_observations": len(observed_values),
        "resampling_unit": "map-then-query-within-sampled-map",
    }


def _safe_relative_file(path: Path, base: Path, *, required_name: str) -> Path:
    if path.is_symlink():
        _fail(f"{required_name} must not be a symbolic link")
    try:
        relative = path.resolve(strict=True).relative_to(base.resolve(strict=True))
    except (OSError, ValueError) as error:
        raise AnalysisError(
            f"{required_name} must resolve at or below {base}"
        ) from error
    pure = PurePosixPath(relative.as_posix())
    if pure.is_absolute() or ".." in pure.parts or pure.name != required_name:
        _fail(f"unsafe {required_name} path")
    return Path(*pure.parts)


def _load_replayed_run(
    path: Path,
    *,
    config_path: Path,
    repository_root: Path,
    expected_split: str,
    freeze_manifest: Path | None = None,
) -> LoadedRun:
    """Invoke the runner's formal replay loader; structural-only loads are forbidden."""

    if expected_split not in {"development", "sealed_evaluation"}:
        _fail("formal replay expected_split is invalid")
    if expected_split == "sealed_evaluation" and freeze_manifest is None:
        _fail("sealed-evaluation replay requires the external freeze manifest")
    if expected_split == "development" and freeze_manifest is not None:
        _fail("development replay forbids a sealed-evaluation freeze manifest")
    arguments: dict[str, Any] = {
        "config_path": config_path,
        "repository_root": repository_root,
    }
    if freeze_manifest is not None:
        arguments["freeze_manifest"] = freeze_manifest
    try:
        loaded = load_complete_run(path, **arguments)
    except TypeError as error:  # API downgrade must fail rather than silently weaken.
        raise AnalysisError(
            "runner does not expose the required replaying formal-load API"
        ) from error
    except (RunnerError, OSError, ValueError) as error:
        raise AnalysisError(
            f"formal result replay validation failed: {error}"
        ) from error
    if loaded.run.get("experiment_split") != expected_split:
        _fail("formal replay returned the wrong experiment split")
    return loaded


def _expected_freeze_bindings(run: Mapping[str, Any]) -> dict[str, Any]:
    bindings = run.get("bindings")
    if not isinstance(bindings, Mapping):
        _fail("formal run has no binding object")
    try:
        return runner_module._freeze_binding_values(bindings)
    except (KeyError, TypeError, RunnerError) as error:
        raise AnalysisError(f"cannot derive frozen run bindings: {error}") from error


def _current_freeze_bindings(
    plan: Mapping[str, Any], repository_root: Path, split: str
) -> dict[str, Any]:
    try:
        current = runner_module._current_bindings(plan, repository_root, split)
        return runner_module._freeze_binding_values(current)
    except (KeyError, OSError, TypeError, RunnerError) as error:
        raise AnalysisError(f"cannot derive current bindings: {error}") from error


def _load_authorization(
    *,
    freeze_path: Path,
    audit_path: Path,
    repository_root: Path,
    config_path: Path,
    plan: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Mapping[str, Any], LoadedRun, Path]:
    freeze_value = _object(
        _strict_json_file(freeze_path),
        label="sealed-evaluation freeze",
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
        freeze_value["schema"] != FREEZE_SCHEMA
        or freeze_value["authorization"] != "sealed_evaluation"
        or freeze_value["issued_by"] != AUDITOR_ID
    ):
        _fail("freeze is not the v2 external sealed-evaluation authorization")
    try:
        audit = load_development_audit(audit_path)
    except (DevelopmentGateError, OSError) as error:
        raise AnalysisError(f"development audit validation failed: {error}") from error
    audit_binding = _object(
        freeze_value["development_audit"],
        label="freeze.development_audit",
        keys={"path", "sha256", "schema"},
    )
    audit_relative = _safe_relative_file(
        audit_path, freeze_path.parent, required_name="development_audit.json"
    )
    if (
        audit_binding["path"] != audit_relative.as_posix()
        or _sha(audit_binding["sha256"], label="freeze development-audit SHA")
        != _sha256_file(audit_path)
        or audit_binding["schema"] != AUDIT_SCHEMA
    ):
        _fail("freeze development-audit binding differs from the supplied audit")

    development_binding = _object(
        freeze_value["development_result"],
        label="freeze.development_result",
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
    relative_text = _text(
        development_binding["manifest_path"],
        label="freeze.development_result.manifest_path",
    )
    pure = PurePosixPath(relative_text)
    if (
        pure.is_absolute()
        or ".." in pure.parts
        or "\\" in relative_text
        or pure.name != "manifest.json"
    ):
        _fail("freeze development manifest path is unsafe")
    development_manifest = freeze_path.parent / Path(*pure.parts)
    if (
        not development_manifest.is_file()
        or development_manifest.is_symlink()
        or _sha256_file(development_manifest)
        != _sha(
            development_binding["manifest_sha256"], label="development manifest SHA"
        )
    ):
        _fail("development manifest does not match the freeze")
    if (
        development_binding["formal"] is not True
        or development_binding["complete"] is not True
        or development_binding["validation"] != "passed"
        or development_binding["map_count"] != 4
        or development_binding["query_count"] != FORMAL_DEVELOPMENT_QUERIES
        or development_binding["search_run_count"]
        != FORMAL_DEVELOPMENT_QUERIES * SEARCHES_PER_QUERY
        or not isinstance(development_binding["artifact_bindings"], Mapping)
    ):
        _fail("freeze does not attest the exact successful development matrix")
    if audit.get("bindings") != freeze_value["bindings"] or audit.get(
        "development_result"
    ) != dict(development_binding):
        _fail("development audit and freeze attest different evidence")
    checks = audit.get("checks")
    required_true_checks = {
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
    }
    if not isinstance(checks, Mapping) or any(
        checks.get(key) is not True for key in required_true_checks
    ):
        _fail("development audit does not record every required passed gate")

    development = _load_replayed_run(
        development_manifest.parent,
        config_path=config_path,
        repository_root=repository_root,
        expected_split="development",
    )
    _validate_formal_run_shape(development, plan, split="development")
    if _plain(development_binding["artifact_bindings"]) != _plain(
        development.manifest["artifacts"]
    ):
        _fail("development artifact bindings differ from the freeze/audit")
    if audit["checks"].get("canonical_plan_sha256") != plan.get("plan_sha256"):
        _fail("development audit is not bound to the current canonical plan")
    frozen = dict(freeze_value["bindings"])
    if frozen != _expected_freeze_bindings(
        development.run
    ) or frozen != _current_freeze_bindings(plan, repository_root, "development"):
        _fail("development/freeze bindings differ from the current verified project")
    return freeze_value, audit, development, development_manifest


def _query_without_split(query: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: _plain(value) for key, value in query.items() if key != "experiment_split"
    }


def _expected_schedule(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    orders = plan.get("timing_orders")
    if not isinstance(orders, Mapping) or set(orders) != {"warmup", "timed"}:
        _fail("verified plan has no exact timing schedule")
    result: list[dict[str, Any]] = []
    for phase, timed, count in (
        ("warmup", False, 1),
        ("timed", True, TIMED_REPETITIONS),
    ):
        rows = orders[phase]
        if not isinstance(rows, Sequence) or len(rows) != count:
            _fail(f"verified plan has the wrong {phase} repetition count")
        for row in rows:
            result.append({"phase": phase, **_plain(row)})
            if row.get("timed") is not timed:
                _fail(f"verified plan {phase} flag is invalid")
    return result


def _validate_counter_identities(
    counters: Mapping[str, Any], *, method: str, query_id: str
) -> None:
    _object(counters, label=f"{query_id}/{method}.counters", keys=COUNTER_NAMES)
    for name, value in counters.items():
        _plain_int(value, label=f"{query_id}/{method}.{name}")
    if (
        counters["reopened"] != 0
        or counters["expanded"] < 1
        or counters["unique_discovered"] < 2
        or counters["generated"] < counters["relaxations"]
        or counters["relaxations"] < counters["unique_discovered"] - 1
        or counters["pops"] < counters["expanded"] + 1 + counters["requeues"]
        or counters["stale_pops"] > counters["pops"]
        or counters["heuristic_cache_hits"] > counters["relaxations"]
        or counters["max_open_entries"] < 1
        or not 1 <= counters["max_live_states"] <= counters["unique_discovered"]
    ):
        _fail(f"{query_id}/{method}: search counter invariant failed")
    prefix = counters["prefix_calls"]
    suffix = counters["suffix_calls"]
    full = counters["full_calls"]
    pivots = counters["pivot_evaluations"]
    reads = counters["distance_table_reads"]
    if method == "manhattan":
        if any((prefix, suffix, full, pivots, reads, counters["requeues"])):
            _fail(f"{query_id}: Manhattan contains landmark work")
        return
    if (
        reads != 32 + pivots
        or counters["manhattan_calls"] != counters["unique_discovered"]
    ):
        _fail(f"{query_id}/{method}: landmark read/call accounting failed")
    if method == "eager_full":
        valid = prefix == suffix == counters["requeues"] == 0 and pivots == full * 32
    elif method == "lazy_full":
        valid = (
            prefix == suffix == 0
            and pivots == full * 32
            and full == counters["requeues"] + 1
        )
    elif method == "staged":
        valid = (
            full == 0
            and pivots == prefix * 4 + suffix * 28
            and prefix >= suffix >= 1
            and counters["requeues"] == prefix + suffix - 2
        )
    else:  # pragma: no cover - closed method set
        valid = False
    if not valid:
        _fail(f"{query_id}/{method}: method-specific counter identity failed")


def _validate_formal_run_shape(
    loaded: LoadedRun, plan: Mapping[str, Any], *, split: str
) -> None:
    expected_queries = (
        FORMAL_DEVELOPMENT_QUERIES
        if split == "development"
        else FORMAL_EVALUATION_QUERIES
    )
    expected_maps = 4 if split == "development" else EXPECTED_EVALUATION_MAPS
    queries_per_map = 40 if split == "development" else EXPECTED_QUERIES_PER_MAP
    manifest = loaded.manifest
    run = loaded.run
    expected_counts = {
        "maps": expected_maps,
        "queries": expected_queries,
        "search_runs": expected_queries * SEARCHES_PER_QUERY,
    }
    if (
        manifest.get("experiment_split") != split
        or manifest.get("formal") is not True
        or manifest.get("complete") is not True
        or manifest.get("validation") != "passed"
        or dict(manifest.get("record_counts", {})) != expected_counts
    ):
        _fail(f"{split} manifest is not the exact successful formal matrix")
    if (
        run.get("experiment_split") != split
        or run.get("formal") is not True
        or run.get("nonformal_smoke") is not False
        or run.get("status") != "complete"
        or run.get("validation") != "passed"
        or list(run.get("methods", ())) != list(METHODS)
        or _plain(run.get("schedule", ())) != _expected_schedule(plan)
        or run.get("counts", {}).get("maps") != expected_maps
        or run.get("counts", {}).get("queries") != expected_queries
        or run.get("counts", {}).get("searches_per_query") != SEARCHES_PER_QUERY
        or run.get("counts", {}).get("search_runs")
        != expected_queries * SEARCHES_PER_QUERY
    ):
        _fail(f"{split} run metadata differs from the exact frozen matrix")
    plan_landmarks = plan.get("landmarks", {})
    if dict(run.get("landmarks", {})) != {
        "full_pivots": plan_landmarks.get("full_pivots"),
        "staged_prefix_pivots": plan_landmarks.get("staged_prefix_pivots"),
    }:
        _fail(f"{split} landmark configuration differs from the plan")

    expected_inputs = [
        item
        for item in plan.get("input_bindings", ())
        if isinstance(item, Mapping) and item.get("experiment_split") == split
    ]
    observed_maps = loaded.maps.get("maps")
    if not isinstance(observed_maps, tuple) or len(observed_maps) != expected_maps:
        _fail(f"{split} map artifact has the wrong exact size")
    for observed, expected in zip(observed_maps, expected_inputs, strict=True):
        map_binding = expected["map"]
        if (
            observed.get("map") != PurePosixPath(map_binding["path"]).name
            or observed.get("family") != expected["family"]
            or observed.get("source_split") != expected["source_split"]
            or observed.get("map_sha256") != map_binding["sha256"]
            or observed.get("width") != map_binding["width"]
            or observed.get("height") != map_binding["height"]
            or observed.get("traversable_states") != map_binding["traversable_states"]
        ):
            _fail(f"{split} map identity/order differs from the verified plan")

    expected_query_rows = [
        item
        for item in plan.get("queries", ())
        if isinstance(item, Mapping) and item.get("experiment_split") == split
    ]
    if (
        len(expected_query_rows) != expected_queries
        or len(loaded.queries) != expected_queries
    ):
        _fail(f"{split} does not contain the exact planned query count")
    per_map: Counter[str] = Counter()
    for index, (row, expected) in enumerate(
        zip(loaded.queries, expected_query_rows, strict=True)
    ):
        query_id = expected["query_id"]
        if (
            row.get("sequence_index") != index
            or row.get("experiment_split") != split
            or _plain(row.get("query")) != _query_without_split(expected)
        ):
            _fail(f"{split} query identity/order differs at index {index}")
        per_map[expected["map"]] += 1
        validation = row.get("validation")
        if not isinstance(validation, Mapping) or any(
            validation.get(key) is not True
            for key in (
                "all_costs_match_bfs",
                "all_repetitions_deterministic",
                "full_landmark_expansion_digests_match",
            )
        ):
            _fail(f"{query_id}: correctness gates are not all true")
        oracle = row.get("oracle")
        if not isinstance(oracle, Mapping):
            _fail(f"{query_id}: missing replayed BFS oracle")
        oracle_cost = _plain_int(
            oracle.get("cost"), label=f"{query_id}.oracle.cost", minimum=1
        )
        summaries = row.get("deterministic_by_method")
        if not isinstance(summaries, Mapping) or set(summaries) != set(METHODS):
            _fail(f"{query_id}: method summaries are incomplete")
        full_digests = {
            summaries[method].get("expansion_digest")
            for method in ("eager_full", "lazy_full", "staged")
        }
        if len(full_digests) != 1:
            _fail(f"{query_id}: full-landmark expansion digests differ")
        for method in METHODS:
            result = summaries[method]
            if (
                not isinstance(result, Mapping)
                or result.get("mode") != method
                or result.get("found") is not True
                or result.get("cost") != oracle_cost
            ):
                _fail(f"{query_id}/{method}: result differs from replayed BFS")
            _sha(result.get("path_sha256"), label=f"{query_id}/{method}.path_sha256")
            _sha(
                result.get("expansion_digest"),
                label=f"{query_id}/{method}.expansion_digest",
            )
            counters = result.get("counters")
            if not isinstance(counters, Mapping):
                _fail(f"{query_id}/{method}: counters missing")
            _validate_counter_identities(counters, method=method, query_id=query_id)

        invocations = row.get("runs")
        if not isinstance(invocations, tuple) or len(invocations) != SEARCHES_PER_QUERY:
            _fail(f"{query_id}: invocation matrix is not exactly 36")
        cursor = 0
        timed_counts: Counter[str] = Counter()
        for schedule_row in _expected_schedule(plan):
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
                    _fail(f"{query_id}: invocation rotation differs at {cursor}")
                timing = invocation.get("timing_ns")
                if (
                    not isinstance(timing, Mapping)
                    or set(timing) != {"stage_ns", "search_ns"}
                    or _plain_int(timing["stage_ns"], label="stage_ns") != 0
                    or _plain_int(timing["search_ns"], label="search_ns", minimum=1) < 1
                    or invocation.get("result") != summaries[method]
                ):
                    _fail(f"{query_id}/{method}: primary timing/result contract failed")
                if schedule_row["timed"]:
                    timed_counts[method] += 1
                cursor += 1
        if timed_counts != Counter({method: TIMED_REPETITIONS for method in METHODS}):
            _fail(f"{query_id}: timed repetitions are not exactly 8 per method")
    if len(per_map) != expected_maps or set(per_map.values()) != {queries_per_map}:
        _fail(f"{split} does not cover the exact map-by-query matrix")


def _load_inputs(
    evaluation_result: Path,
    *,
    config_path: Path,
    repository_root: Path,
    freeze_manifest: Path,
    development_audit: Path,
) -> AnalysisInputs:
    supplied_evaluation = Path(evaluation_result)
    supplied_freeze = Path(freeze_manifest)
    supplied_audit = Path(development_audit)
    for supplied, label in (
        (supplied_evaluation, "sealed-evaluation result"),
        (supplied_freeze, "freeze manifest"),
        (supplied_audit, "development audit"),
    ):
        if supplied.is_symlink():
            _fail(f"{label} must not be a symbolic link")
    root = repository_root.resolve(strict=True)
    config = config_path.resolve(strict=True)
    freeze_path = supplied_freeze.resolve(strict=True)
    audit_path = supplied_audit.resolve(strict=True)
    try:
        plan = verify_protocol(config, repository_root=root)
    except Exception as error:
        raise AnalysisError(
            f"canonical protocol verification failed: {error}"
        ) from error
    if (
        plan.get("schema") != "progressive-landmarks-plan-v2"
        or plan.get("protocol_id") != "progressive_landmarks_v2"
        or plan.get("master_seed") != BOOTSTRAP_SEED
        or plan.get("counts", {}).get("sealed_evaluation_queries")
        != FORMAL_EVALUATION_QUERIES
        or plan.get("counts", {}).get("timed_repetitions") != TIMED_REPETITIONS
    ):
        _fail("verified protocol is not the exact prospective v2 analysis design")
    freeze, audit, development, development_manifest = _load_authorization(
        freeze_path=freeze_path,
        audit_path=audit_path,
        repository_root=root,
        config_path=config,
        plan=plan,
    )
    evaluation = _load_replayed_run(
        supplied_evaluation,
        config_path=config,
        repository_root=root,
        expected_split="sealed_evaluation",
        freeze_manifest=freeze_path,
    )
    _validate_formal_run_shape(evaluation, plan, split="sealed_evaluation")
    frozen = dict(freeze["bindings"])
    if frozen != _expected_freeze_bindings(
        evaluation.run
    ) or frozen != _current_freeze_bindings(plan, root, "sealed_evaluation"):
        _fail("evaluation bindings differ from the freeze/current verified project")
    authorization = evaluation.run.get("evaluation_authorization")
    if (
        not isinstance(authorization, Mapping)
        or authorization.get("sha256") != _sha256_file(freeze_path)
        or authorization.get("development_manifest_sha256")
        != freeze["development_result"]["manifest_sha256"]
    ):
        _fail("evaluation run is not bound to the supplied development freeze")
    try:
        authorization_path = Path(authorization["path"]).resolve(strict=True)
    except (KeyError, OSError, TypeError) as error:
        raise AnalysisError(
            f"evaluation authorization path cannot be resolved: {error}"
        ) from error
    if authorization_path != freeze_path:
        _fail("evaluation authorization path differs from the supplied freeze")
    return AnalysisInputs(
        plan,
        evaluation,
        development,
        freeze,
        audit,
        freeze_path,
        audit_path,
        development_manifest,
    )


def _timed_observations(row: Mapping[str, Any], method: str) -> list[int]:
    values = [
        invocation["timing_ns"]["search_ns"]
        for invocation in row["runs"]
        if invocation["timed"] is True and invocation["method"] == method
    ]
    if len(values) != TIMED_REPETITIONS:
        _fail(f"{row['query']['query_id']}/{method}: expected exactly 8 timings")
    return values


def _query_metrics(loaded: LoadedRun) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for stored in loaded.queries:
        query = stored["query"]
        record: dict[str, Any] = {
            "sequence_index": stored["sequence_index"],
            "query_id": query["query_id"],
            "family": query["family"],
            "map": query["map"],
            "source_split": query["source_split"],
            "scenario": query["scenario"],
            "scenario_index": query["scenario_index"],
            "scenario_row": query["scenario_row"],
            "source_line": query["source_line"],
            "source_bucket": query["source_bucket"],
            "start_x": query["start"][0],
            "start_y": query["start"][1],
            "goal_x": query["goal"][0],
            "goal_y": query["goal"][1],
            "oracle_cost": stored["oracle"]["cost"],
        }
        for method in METHODS:
            observations = _timed_observations(stored, method)
            for repetition, value in enumerate(observations):
                record[f"{method}_search_ns_r{repetition}"] = value
            record[f"{method}_median_search_ns"] = even_sample_median(observations)
            counters = stored["deterministic_by_method"][method]["counters"]
            for counter in COUNTER_NAMES:
                record[f"{method}_{counter}"] = counters[counter]

        staged_time = record["staged_median_search_ns"]
        lazy_time = record["lazy_full_median_search_ns"]
        ratio = staged_time / lazy_time
        lazy = stored["deterministic_by_method"]["lazy_full"]["counters"]
        staged = stored["deterministic_by_method"]["staged"]["counters"]
        pivot_saved = lazy["pivot_evaluations"] - staged["pivot_evaluations"]
        read_saved = lazy["distance_table_reads"] - staged["distance_table_reads"]
        suffix_avoided = staged["prefix_calls"] - staged["suffix_calls"]
        eligible_post_start = staged["prefix_calls"] - 1
        lazy_open = 1 + lazy["relaxations"] + lazy["requeues"] + lazy["pops"]
        staged_open = 1 + staged["relaxations"] + staged["requeues"] + staged["pops"]
        record.update(
            {
                "staged_lazy_time_ratio": ratio,
                "staged_lazy_log_time_ratio": math.log(ratio),
                "pivot_saved": pivot_saved,
                "pivot_saving": pivot_saved / lazy["pivot_evaluations"],
                "read_saved": read_saved,
                "read_saving": read_saved / lazy["distance_table_reads"],
                "suffix_avoided_calls": suffix_avoided,
                "suffix_avoidance_rate": (
                    suffix_avoided / eligible_post_start
                    if eligible_post_start > 0
                    else None
                ),
                "lazy_full_open_operations": lazy_open,
                "staged_open_operations": staged_open,
                "extra_open_work": staged_open - lazy_open,
            }
        )
        rows.append(record)
    return rows


def _nonnull(rows: Sequence[Mapping[str, Any]], key: str) -> list[float]:
    return [float(row[key]) for row in rows if row.get(key) is not None]


def _group_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    metrics = {
        key: distribution_summary(_nonnull(rows, key))
        for key in (
            "staged_lazy_time_ratio",
            "staged_lazy_log_time_ratio",
            "pivot_saved",
            "pivot_saving",
            "read_saved",
            "read_saving",
            "suffix_avoided_calls",
            "extra_open_work",
        )
    }
    suffix_rates = _nonnull(rows, "suffix_avoidance_rate")
    metrics["suffix_avoidance_rate"] = (
        distribution_summary(suffix_rates) if suffix_rates else None
    )
    method_summary: dict[str, Any] = {}
    for method in METHODS:
        counters = {}
        for counter in COUNTER_NAMES:
            values = _nonnull(rows, f"{method}_{counter}")
            counters[counter] = {
                "sum": int(math.fsum(values)),
                "mean": math.fsum(values) / len(values),
                "median": _median(values),
            }
        method_summary[method] = {
            "median_search_ns": distribution_summary(
                _nonnull(rows, f"{method}_median_search_ns")
            ),
            "counters": counters,
        }
    return {"queries": len(rows), "paired_metrics": metrics, "methods": method_summary}


def _build_map_rows(
    query_rows: Sequence[Mapping[str, Any]], maps: Sequence[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_map: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in query_rows:
        by_map[row["map"]].append(row)
    output: list[dict[str, Any]] = []
    detailed: dict[str, Any] = {}
    for map_row in maps:
        name = map_row["map"]
        rows = by_map[name]
        if not rows or any(
            row["family"] != map_row["family"]
            or row["source_split"] != map_row["source_split"]
            for row in rows
        ):
            _fail(f"query/map family or source-split identity differs: {name}")
        summary = _group_summary(rows)
        detailed[name] = summary
        flat: dict[str, Any] = {
            "map": name,
            "family": map_row["family"],
            "source_split": map_row["source_split"],
            "queries": len(rows),
            "landmark_build_ns": map_row["landmark_build_ns"],
            "packed_distance_bytes": map_row["packed_distance_bytes"],
        }
        for metric, values in summary["paired_metrics"].items():
            if values is None:
                for statistic in ("median", "q1", "q3"):
                    flat[f"{metric}_{statistic}"] = None
            else:
                for statistic in ("median", "q1", "q3"):
                    flat[f"{metric}_{statistic}"] = values[statistic]
        for method in METHODS:
            time_values = summary["methods"][method]["median_search_ns"]
            flat[f"{method}_mean_search_ns"] = time_values["mean"]
            flat[f"{method}_median_search_ns"] = time_values["median"]
            for query_count in AMORTIZATION_QUERIES:
                build = 0 if method == "manhattan" else map_row["landmark_build_ns"]
                flat[f"{method}_amortized_ns_q{query_count}"] = (
                    time_values["mean"] + build / query_count
                )
        output.append(flat)
    return output, detailed


def _hypotheses(
    rows: Sequence[Mapping[str, Any]], bootstrap: Mapping[str, Any]
) -> list[dict[str, Any]]:
    pivot = distribution_summary(_nonnull(rows, "pivot_saving"))
    reads = distribution_summary(_nonnull(rows, "read_saving"))
    ratio = distribution_summary(_nonnull(rows, "staged_lazy_time_ratio"))
    rho = spearman_rho(
        _nonnull(rows, "read_saving"),
        _nonnull(rows, "staged_lazy_log_time_ratio"),
    )
    return [
        {
            "hypothesis": "H1",
            "status": "passed",
            "estimand": "BFS equality and full-landmark trace invariance",
            "estimate": "800/800 queries passed both gates",
            "interval": "not applicable (exact validation)",
            "interpretation": "Correctness/invariance gate passed before timing access.",
        },
        {
            "hypothesis": "H2",
            "status": (
                "supported"
                if pivot["median"] > 0.0 and reads["median"] > 0.0
                else "not supported"
            ),
            "estimand": "median exact pivot/read saving of staged vs lazy_full",
            "estimate": (f"pivot={pivot['median']:.9g}; read={reads['median']:.9g}"),
            "interval": "descriptive IQRs in summary/map tables",
            "interpretation": "Exact deterministic work counts; no timing inference.",
        },
        {
            "hypothesis": "H3",
            "status": "descriptive",
            "estimand": "median paired staged/lazy_full time ratio; read-saving association",
            "estimate": (
                f"ratio={ratio['median']:.9g}; Spearman rho="
                f"{('undefined' if rho is None else format(rho, '.9g'))}"
            ),
            "interval": (
                f"hierarchical bootstrap 95% CI "
                f"[{bootstrap['ci95_ratio'][0]:.9g}, {bootstrap['ci95_ratio'][1]:.9g}]"
            ),
            "interpretation": "Association is descriptive and not causal.",
        },
    ]


def _summarize(
    query_rows: list[dict[str, Any]],
    map_rows: list[dict[str, Any]],
    map_details: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    overall = _group_summary(query_rows)
    family_rows: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in query_rows:
        family_rows[row["family"]].append(row)
    families = {
        family: _group_summary(family_rows[family]) for family in EXPECTED_FAMILIES
    }
    values_by_map = {
        map_name: _nonnull(
            [row for row in query_rows if row["map"] == map_name],
            "staged_lazy_log_time_ratio",
        )
        for map_name in sorted(map_details)
    }
    bootstrap = hierarchical_bootstrap_median(values_by_map)
    rho_read = spearman_rho(
        _nonnull(query_rows, "read_saving"),
        _nonnull(query_rows, "staged_lazy_log_time_ratio"),
    )
    rho_open = spearman_rho(
        _nonnull(query_rows, "extra_open_work"),
        _nonnull(query_rows, "staged_lazy_log_time_ratio"),
    )
    amortization: dict[str, dict[str, float]] = {method: {} for method in METHODS}
    for method in METHODS:
        for query_count in AMORTIZATION_QUERIES:
            values = [
                float(row[f"{method}_amortized_ns_q{query_count}"]) for row in map_rows
            ]
            amortization[method][str(query_count)] = math.fsum(values) / len(values)
    hypotheses = _hypotheses(query_rows, bootstrap)
    core = {
        "schema": SUMMARY_SCHEMA,
        "analyzer": ANALYZER_ID,
        "protocol_id": "progressive_landmarks_v2",
        "design": {
            "maps": EXPECTED_EVALUATION_MAPS,
            "families": list(EXPECTED_FAMILIES),
            "queries": FORMAL_EVALUATION_QUERIES,
            "queries_per_map": EXPECTED_QUERIES_PER_MAP,
            "methods": list(METHODS),
            "timed_repetitions_per_query_method": TIMED_REPETITIONS,
            "query_timing_reducer": (
                "ordinary even median: mean of sorted fourth and fifth of eight"
            ),
            "quartile_definition": "Hyndman-Fan type 7 linear empirical quantile",
            "experimental_units": "maps (top-level) and queries within maps",
            "repetitions_are_units": False,
        },
        "integrity": {
            "performance_unlocked_after_all_gates": True,
            "bfs_replayed": True,
            "deterministic_methods_replayed": True,
            "all_costs_match_bfs": True,
            "all_full_landmark_expansion_digests_match": True,
            "all_800_queries_retained": True,
            "post_hoc_exclusions": 0,
        },
        "primary_staged_vs_lazy_full": bootstrap,
        "overall": overall,
        "maps": dict(map_details),
        "families": families,
        "descriptive_spearman": {
            "read_saving_vs_log_time_ratio": rho_read,
            "extra_open_work_vs_log_time_ratio": rho_open,
            "n": FORMAL_EVALUATION_QUERIES,
            "rank_ties": "average ranks",
            "interpretation": "descriptive association; no causal claim",
        },
        "preprocessing_amortization": {
            "formula": "mean per-query median search_ns + map landmark_build_ns / Q",
            "aggregation": "compute within each map, then equal-weight the eight maps",
            "queries_per_map_Q": list(AMORTIZATION_QUERIES),
            "mean_amortized_ns_by_method": amortization,
            "manhattan_build_ns": 0,
            "landmark_build_charge": "same measured per-map cost for all landmark modes",
        },
        "hypothesis_outcomes": hypotheses,
        "figures": [f"figures/{stem}.png" for stem in FIGURE_STEMS],
    }
    return {**core, "summary_sha256": canonical_json_sha256(core)}, hypotheses


def _csv_bytes(rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=list(columns),
        extrasaction="raise",
        lineterminator="\n",
    )
    writer.writeheader()
    for row in rows:
        rendered = {
            column: (
                ""
                if row.get(column) is None
                else (
                    format(row[column], ".17g")
                    if type(row.get(column)) is float
                    else row.get(column)
                )
            )
            for column in columns
        }
        writer.writerow(rendered)
    return buffer.getvalue().encode("utf-8")


def _tex_escape(value: Any) -> str:
    text = str(value)
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
    }
    return "".join(replacements.get(character, character) for character in text)


def _hypothesis_tex(rows: Sequence[Mapping[str, Any]]) -> bytes:
    lines = [
        r"\begin{tabular}{llll}",
        r"\toprule",
        r"Hypothesis & Status & Estimate & Interval \\",
        r"\midrule",
    ]
    for row in rows:
        lines.append(
            " & ".join(
                _tex_escape(row[key])
                for key in ("hypothesis", "status", "estimate", "interval")
            )
            + r" \\"
        )
    lines.extend((r"\bottomrule", r"\end{tabular}"))
    return ("\n".join(lines) + "\n").encode("ascii", errors="strict")


def _figure_metadata(extension: str) -> dict[str, Any]:
    if extension == "pdf":
        return {
            "Creator": ANALYZER_ID,
            "Producer": ANALYZER_ID,
            "CreationDate": None,
            "ModDate": None,
        }
    return {"Software": ANALYZER_ID}


def _save_figure(figure: Any, directory: Path, stem: str) -> None:
    for extension in ("png", "pdf"):
        figure.savefig(
            directory / f"{stem}.{extension}",
            dpi=180 if extension == "png" else None,
            bbox_inches="tight",
            metadata=_figure_metadata(extension),
        )


def _render_figures(
    query_rows: Sequence[Mapping[str, Any]],
    map_rows: Sequence[Mapping[str, Any]],
    output: Path,
) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt
    except ImportError as error:
        raise AnalysisError("matplotlib is required for the planned figures") from error

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 9,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "figure.dpi": 100,
        }
    )
    colors = {
        "maze": "#4C78A8",
        "random": "#F58518",
        "room": "#54A24B",
        "warehouse": "#E45756",
    }

    figure, axis = plt.subplots(figsize=(8.4, 2.8))
    stages = [
        (0.08, "Manhattan\n$H_0$"),
        (0.39, "4 pivots\n$H_4$"),
        (0.70, "32 pivots\n$H_{32}$"),
    ]
    for x, label in stages:
        axis.add_patch(
            plt.Rectangle(
                (x, 0.35), 0.20, 0.30, facecolor="#E8EEF7", edgecolor="#315A89"
            )
        )
        axis.text(x + 0.10, 0.50, label, ha="center", va="center", fontsize=11)
    for left, right in ((0.28, 0.39), (0.59, 0.70)):
        axis.annotate(
            "",
            xy=(right, 0.50),
            xytext=(left, 0.50),
            arrowprops={"arrowstyle": "->", "lw": 1.8},
        )
    axis.text(
        0.50,
        0.16,
        "Refine only after a valid OPEN pop; stop immediately on a valid goal pop",
        ha="center",
    )
    axis.set_xlim(0, 1)
    axis.set_ylim(0, 1)
    axis.axis("off")
    _save_figure(figure, output, "stage_schematic")
    plt.close(figure)

    map_names = [row["map"] for row in map_rows]
    ratios = [
        [
            record["staged_lazy_time_ratio"]
            for record in query_rows
            if record["map"] == name
        ]
        for name in map_names
    ]
    figure, axis = plt.subplots(figsize=(9.5, 4.8))
    boxes = axis.boxplot(
        ratios,
        tick_labels=[name.removesuffix(".map") for name in map_names],
        showfliers=False,
        patch_artist=True,
    )
    for box, map_row in zip(boxes["boxes"], map_rows, strict=True):
        box.set_facecolor(colors[map_row["family"]])
        box.set_alpha(0.65)
    axis.axhline(1.0, color="black", lw=1, linestyle="--")
    axis.set_ylabel("Per-query median time ratio (staged / lazy_full)")
    axis.tick_params(axis="x", rotation=35)
    axis.set_title("Paired search-only timing by sealed map")
    _save_figure(figure, output, "per_map_time_ratios")
    plt.close(figure)

    figure, axes = plt.subplots(1, 2, figsize=(11.0, 4.7))
    for family in EXPECTED_FAMILIES:
        rows = [row for row in query_rows if row["family"] == family]
        axes[0].scatter(
            [row["read_saving"] for row in rows],
            [row["staged_lazy_log_time_ratio"] for row in rows],
            s=14,
            alpha=0.52,
            label=family,
            color=colors[family],
            linewidths=0,
        )
        axes[1].scatter(
            [row["read_saving"] for row in rows],
            [row["extra_open_work"] for row in rows],
            s=14,
            alpha=0.52,
            label=family,
            color=colors[family],
            linewidths=0,
        )
    axes[0].axhline(0.0, color="black", lw=1, linestyle="--")
    axes[0].set_xlabel("Exact distance-table read saving")
    axes[0].set_ylabel("log(staged / lazy_full median search time)")
    axes[0].set_title("Saved work versus paired runtime")
    axes[1].axhline(0.0, color="black", lw=1, linestyle="--")
    axes[1].set_xlabel("Exact distance-table read saving")
    axes[1].set_ylabel("Extra OPEN operations (staged - lazy_full)")
    axes[1].set_title("Saved work versus added OPEN work")
    axes[1].legend(frameon=False, ncols=2)
    figure.tight_layout()
    _save_figure(figure, output, "saved_work_vs_time")
    plt.close(figure)

    family_positions = list(range(len(EXPECTED_FAMILIES)))
    figure, axes = plt.subplots(1, 3, figsize=(10.5, 3.6))
    for axis, metric, title in (
        (axes[0], "pivot_saving", "Median pivot saving"),
        (axes[1], "suffix_avoidance_rate", "Median suffix avoidance"),
        (axes[2], "extra_open_work", "Median extra OPEN operations"),
    ):
        values = []
        for family in EXPECTED_FAMILIES:
            candidates = [
                row[metric]
                for row in query_rows
                if row["family"] == family and row[metric] is not None
            ]
            values.append(_median(candidates) if candidates else 0.0)
        axis.bar(
            family_positions,
            values,
            color=[colors[family] for family in EXPECTED_FAMILIES],
            alpha=0.78,
        )
        axis.set_xticks(family_positions, EXPECTED_FAMILIES, rotation=25)
        axis.set_title(title)
        axis.axhline(0.0, color="black", lw=0.8)
    figure.suptitle("Mechanism decomposition by family")
    figure.tight_layout()
    _save_figure(figure, output, "family_mechanism_decomposition")
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(7.2, 4.6))
    method_colors = {
        "manhattan": "#777777",
        "eager_full": "#4C78A8",
        "lazy_full": "#F58518",
        "staged": "#54A24B",
    }
    for method in METHODS:
        values = [
            math.fsum(
                float(row[f"{method}_amortized_ns_q{query_count}"]) for row in map_rows
            )
            / len(map_rows)
            / 1_000_000.0
            for query_count in AMORTIZATION_QUERIES
        ]
        axis.plot(
            AMORTIZATION_QUERIES,
            values,
            marker="o",
            label=method,
            color=method_colors[method],
        )
    axis.set_xscale("log")
    axis.set_xlabel("Queries per map used to amortize preprocessing (Q)")
    axis.set_ylabel("Mean amortized time per query (ms)")
    axis.set_title("Landmark preprocessing amortization")
    axis.legend(frameon=False)
    _save_figure(figure, output, "preprocessing_amortization")
    plt.close(figure)


def _binding(path: Path, repository_root: Path) -> dict[str, Any]:
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


def _provenance(
    inputs: AnalysisInputs, *, config_path: Path, repository_root: Path
) -> dict[str, Any]:
    module_path = Path(__file__).resolve(strict=True)
    cli_path = repository_root / "scripts" / "analyze_progressive_landmarks.py"
    files = {
        "analysis": _binding(module_path, repository_root),
        "cli": _binding(cli_path, repository_root),
    }
    core = {
        "schema": PROVENANCE_SCHEMA,
        "analyzer": ANALYZER_ID,
        "analysis_code": {
            "files": files,
            "sha256": canonical_json_sha256(files),
        },
        "protocol": {
            "config": _binding(config_path, repository_root),
            "plan_sha256": inputs.plan["plan_sha256"],
        },
        "sealed_evaluation": {
            "manifest": _binding(
                inputs.evaluation.root / "manifest.json", repository_root
            ),
            "artifact_bindings": _plain(inputs.evaluation.manifest["artifacts"]),
            "bindings": _plain(inputs.evaluation.run["bindings"]),
        },
        "development_authorization": {
            "freeze": _binding(inputs.freeze_path, repository_root),
            "audit": _binding(inputs.audit_path, repository_root),
            "audit_sha256": inputs.audit["audit_sha256"],
            "development_manifest": _binding(
                inputs.development_manifest, repository_root
            ),
            "selection_performed": False,
            "development_outcomes_used_in_analysis": False,
        },
        "analysis_parameters": {
            "bootstrap_replicates": BOOTSTRAP_REPLICATES,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "bootstrap_hierarchy": "map-then-query-within-map",
            "amortization_queries": list(AMORTIZATION_QUERIES),
            "timing_reducer": "even median of eight",
        },
        "source_run_timestamps": {
            "sealed_evaluation_started_at_utc": inputs.evaluation.run["started_at_utc"],
            "sealed_evaluation_completed_at_utc": inputs.evaluation.run[
                "completed_at_utc"
            ],
            "development_started_at_utc": inputs.development.run["started_at_utc"],
            "development_completed_at_utc": inputs.development.run["completed_at_utc"],
        },
    }
    return {**core, "provenance_sha256": canonical_json_sha256(core)}


def _artifact_binding(payload: bytes, *, records: int) -> dict[str, Any]:
    return {
        "sha256": _sha256_bytes(payload),
        "bytes": len(payload),
        "records": records,
    }


def _query_columns() -> list[str]:
    columns = [
        "sequence_index",
        "query_id",
        "family",
        "map",
        "source_split",
        "scenario",
        "scenario_index",
        "scenario_row",
        "source_line",
        "source_bucket",
        "start_x",
        "start_y",
        "goal_x",
        "goal_y",
        "oracle_cost",
    ]
    for method in METHODS:
        columns.extend(
            f"{method}_search_ns_r{repetition}"
            for repetition in range(TIMED_REPETITIONS)
        )
        columns.append(f"{method}_median_search_ns")
        columns.extend(f"{method}_{counter}" for counter in COUNTER_NAMES)
    columns.extend(
        (
            "staged_lazy_time_ratio",
            "staged_lazy_log_time_ratio",
            "pivot_saved",
            "pivot_saving",
            "read_saved",
            "read_saving",
            "suffix_avoided_calls",
            "suffix_avoidance_rate",
            "lazy_full_open_operations",
            "staged_open_operations",
            "extra_open_work",
        )
    )
    return columns


def _map_columns() -> list[str]:
    columns = [
        "map",
        "family",
        "source_split",
        "queries",
        "landmark_build_ns",
        "packed_distance_bytes",
    ]
    for metric in (
        "staged_lazy_time_ratio",
        "staged_lazy_log_time_ratio",
        "pivot_saved",
        "pivot_saving",
        "read_saved",
        "read_saving",
        "suffix_avoided_calls",
        "extra_open_work",
        "suffix_avoidance_rate",
    ):
        columns.extend(f"{metric}_{statistic}" for statistic in ("median", "q1", "q3"))
    for method in METHODS:
        columns.extend((f"{method}_mean_search_ns", f"{method}_median_search_ns"))
        columns.extend(
            f"{method}_amortized_ns_q{query_count}"
            for query_count in AMORTIZATION_QUERIES
        )
    return columns


def analyze_sealed_evaluation(
    evaluation_result: str | Path,
    output_directory: str | Path,
    *,
    config_path: str | Path,
    repository_root: str | Path,
    freeze_manifest: str | Path,
    development_audit: str | Path,
) -> Path:
    """Validate sealed evidence, analyze it once, and atomically publish outputs."""

    root = Path(repository_root).resolve(strict=True)
    config = Path(config_path).resolve(strict=True)
    output = Path(output_directory).resolve(strict=False)
    if _lexists(output):
        _fail(f"refusing to overwrite existing analysis output: {output}")

    # This completes every correctness/provenance gate before timing extraction.
    inputs = _load_inputs(
        Path(evaluation_result),
        config_path=config,
        repository_root=root,
        freeze_manifest=Path(freeze_manifest),
        development_audit=Path(development_audit),
    )
    query_rows = _query_metrics(inputs.evaluation)
    map_records = list(inputs.evaluation.maps["maps"])
    map_rows, map_details = _build_map_rows(query_rows, map_records)
    summary, hypotheses = _summarize(query_rows, map_rows, map_details)
    provenance = _provenance(inputs, config_path=config, repository_root=root)

    query_payload = _csv_bytes(query_rows, _query_columns())
    map_payload = _csv_bytes(map_rows, _map_columns())
    hypothesis_columns = (
        "hypothesis",
        "status",
        "estimand",
        "estimate",
        "interval",
        "interpretation",
    )
    hypothesis_payload = _csv_bytes(hypotheses, hypothesis_columns)
    tex_payload = _hypothesis_tex(hypotheses)
    summary_payload = _json_bytes(summary)
    provenance_payload = _json_bytes(provenance)

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent)
    )
    published = False
    try:
        figures = staging / "figures"
        figures.mkdir()
        _write_new(staging / "summary.json", summary_payload)
        _write_new(staging / "query_metrics.csv", query_payload)
        _write_new(staging / "map_metrics.csv", map_payload)
        _write_new(staging / "hypothesis_table.csv", hypothesis_payload)
        _write_new(staging / "hypothesis_table.tex", tex_payload)
        _write_new(staging / "provenance.json", provenance_payload)
        _render_figures(query_rows, map_rows, figures)

        artifacts = {
            "summary.json": _artifact_binding(summary_payload, records=1),
            "query_metrics.csv": _artifact_binding(
                query_payload, records=FORMAL_EVALUATION_QUERIES
            ),
            "map_metrics.csv": _artifact_binding(
                map_payload, records=EXPECTED_EVALUATION_MAPS
            ),
            "hypothesis_table.csv": _artifact_binding(
                hypothesis_payload, records=len(hypotheses)
            ),
            "hypothesis_table.tex": _artifact_binding(
                tex_payload, records=len(hypotheses)
            ),
            "provenance.json": _artifact_binding(provenance_payload, records=1),
        }
        for stem in FIGURE_STEMS:
            for extension in ("png", "pdf"):
                relative = f"figures/{stem}.{extension}"
                payload = (staging / relative).read_bytes()
                artifacts[relative] = _artifact_binding(payload, records=1)
        manifest = {
            "schema": ANALYSIS_MANIFEST_SCHEMA,
            "analyzer": ANALYZER_ID,
            "protocol_id": "progressive_landmarks_v2",
            "complete": True,
            "validation": "passed",
            "artifacts": dict(sorted(artifacts.items())),
            "record_counts": {
                "queries": FORMAL_EVALUATION_QUERIES,
                "maps": EXPECTED_EVALUATION_MAPS,
                "hypotheses": len(hypotheses),
                "figures": len(FIGURE_STEMS) * 2,
            },
        }
        # Completion marker is intentionally last.
        _write_new(staging / "manifest.json", _json_bytes(manifest))
        load_complete_analysis(staging)
        if _lexists(output):
            _fail(f"refusing to overwrite raced analysis output: {output}")
        try:
            os.rename(staging, output)
        except OSError as error:
            raise AnalysisError(
                f"cannot atomically publish analysis: {error}"
            ) from error
        published = True
        return output
    finally:
        if not published and _lexists(staging):
            shutil.rmtree(staging)


def _csv_record_count(payload: bytes, *, label: str) -> int:
    try:
        text = payload.decode("utf-8")
        reader = csv.DictReader(io.StringIO(text, newline=""))
        if reader.fieldnames is None or len(reader.fieldnames) != len(
            set(reader.fieldnames)
        ):
            _fail(f"{label} has a missing or duplicate header")
        return sum(1 for _ in reader)
    except UnicodeError as error:
        raise AnalysisError(f"cannot decode {label}: {error}") from error


def _csv_rows(
    payload: bytes, *, label: str, columns: Sequence[str]
) -> list[dict[str, str]]:
    try:
        text = payload.decode("utf-8")
        reader = csv.DictReader(io.StringIO(text, newline=""))
        if reader.fieldnames != list(columns):
            _fail(f"{label} header differs from the frozen schema")
        rows = list(reader)
    except UnicodeError as error:
        raise AnalysisError(f"cannot decode {label}: {error}") from error
    if any(None in row or set(row) != set(columns) for row in rows):
        _fail(f"{label} contains a ragged row")
    return rows


def _csv_int(value: str, *, label: str, minimum: int | None = 0) -> int:
    if not value or value.strip() != value:
        _fail(f"{label} is not a canonical integer")
    try:
        parsed = int(value, 10)
    except ValueError as error:
        raise AnalysisError(f"{label} is not an integer") from error
    if (minimum is not None and parsed < minimum) or str(parsed) != value:
        requirement = "integer" if minimum is None else f"integer >= {minimum}"
        _fail(f"{label} is not a canonical {requirement}")
    return parsed


def _csv_float(value: str, *, label: str, nullable: bool = False) -> float | None:
    if nullable and value == "":
        return None
    if not value or value.strip() != value:
        _fail(f"{label} is not a canonical finite float")
    try:
        parsed = float(value)
    except ValueError as error:
        raise AnalysisError(f"{label} is not a float") from error
    if not math.isfinite(parsed):
        _fail(f"{label} is not finite")
    return parsed


def _parse_query_metrics(payload: bytes) -> list[dict[str, Any]]:
    columns = _query_columns()
    decoded = _csv_rows(payload, label="query_metrics.csv", columns=columns)
    if len(decoded) != FORMAL_EVALUATION_QUERIES:
        _fail("query_metrics.csv does not contain exactly 800 rows")
    string_fields = {"query_id", "family", "map", "source_split", "scenario"}
    float_fields = {f"{method}_median_search_ns" for method in METHODS} | {
        "staged_lazy_time_ratio",
        "staged_lazy_log_time_ratio",
        "pivot_saving",
        "read_saving",
        "suffix_avoidance_rate",
    }
    nullable_float_fields = {"suffix_avoidance_rate"}
    rows: list[dict[str, Any]] = []
    query_ids: set[str] = set()
    query_endpoints: set[tuple[str, int, int, int, int]] = set()
    per_map: Counter[str] = Counter()
    map_identity: dict[str, tuple[str, str]] = {}
    for index, raw in enumerate(decoded):
        row: dict[str, Any] = {}
        for column in columns:
            value = raw[column]
            if column in string_fields:
                row[column] = _text(value, label=f"query_metrics[{index}].{column}")
            elif column in float_fields:
                row[column] = _csv_float(
                    value,
                    label=f"query_metrics[{index}].{column}",
                    nullable=column in nullable_float_fields,
                )
            else:
                signed_derived = column in {
                    "pivot_saved",
                    "read_saved",
                    "extra_open_work",
                }
                row[column] = _csv_int(
                    value,
                    label=f"query_metrics[{index}].{column}",
                    minimum=(
                        None
                        if signed_derived
                        else (
                            1
                            if column == "oracle_cost"
                            or column.endswith("_search_ns_r0")
                            or any(
                                column.endswith(f"_search_ns_r{repetition}")
                                for repetition in range(TIMED_REPETITIONS)
                            )
                            else 0
                        )
                    ),
                )
        if row["sequence_index"] != index:
            _fail("query_metrics.csv sequence indexes are not contiguous")
        if any(
            row[name] < 1 for name in ("scenario_index", "scenario_row", "source_line")
        ):
            _fail("query_metrics.csv contains an invalid scenario position")
        if row["query_id"] in query_ids:
            _fail("query_metrics.csv contains a duplicate query ID")
        query_ids.add(row["query_id"])
        if row["family"] not in EXPECTED_FAMILIES:
            _fail("query_metrics.csv contains an invalid family")
        if row["source_split"] not in {"validation", "holdout"}:
            _fail("query_metrics.csv crosses the sealed split")
        if PurePosixPath(row["map"]).name != row["map"] or not row["map"].endswith(
            ".map"
        ):
            _fail("query_metrics.csv contains an unsafe map name")
        identity = (row["family"], row["source_split"])
        previous_identity = map_identity.setdefault(row["map"], identity)
        if previous_identity != identity:
            _fail("query_metrics.csv map identity is inconsistent")
        per_map[row["map"]] += 1
        endpoint = (
            row["map"],
            row["start_x"],
            row["start_y"],
            row["goal_x"],
            row["goal_y"],
        )
        if endpoint[1:3] == endpoint[3:5] or endpoint in query_endpoints:
            _fail("query_metrics.csv contains invalid or duplicate endpoints")
        query_endpoints.add(endpoint)

        for method in METHODS:
            observations = [
                row[f"{method}_search_ns_r{repetition}"]
                for repetition in range(TIMED_REPETITIONS)
            ]
            if row[f"{method}_median_search_ns"] != even_sample_median(observations):
                _fail(f"query_metrics.csv has an invalid even median: {method}")
            counters = {name: row[f"{method}_{name}"] for name in COUNTER_NAMES}
            _validate_counter_identities(
                counters, method=method, query_id=row["query_id"]
            )

        lazy_pivots = row["lazy_full_pivot_evaluations"]
        staged_pivots = row["staged_pivot_evaluations"]
        lazy_reads = row["lazy_full_distance_table_reads"]
        staged_reads = row["staged_distance_table_reads"]
        prefix = row["staged_prefix_calls"]
        suffix = row["staged_suffix_calls"]
        eligible = prefix - 1
        lazy_open = (
            1
            + row["lazy_full_relaxations"]
            + row["lazy_full_requeues"]
            + row["lazy_full_pops"]
        )
        staged_open = (
            1 + row["staged_relaxations"] + row["staged_requeues"] + row["staged_pops"]
        )
        ratio = row["staged_median_search_ns"] / row["lazy_full_median_search_ns"]
        expected_derived = {
            "staged_lazy_time_ratio": ratio,
            "staged_lazy_log_time_ratio": math.log(ratio),
            "pivot_saved": lazy_pivots - staged_pivots,
            "pivot_saving": (lazy_pivots - staged_pivots) / lazy_pivots,
            "read_saved": lazy_reads - staged_reads,
            "read_saving": (lazy_reads - staged_reads) / lazy_reads,
            "suffix_avoided_calls": prefix - suffix,
            "suffix_avoidance_rate": (
                (prefix - suffix) / eligible if eligible > 0 else None
            ),
            "lazy_full_open_operations": lazy_open,
            "staged_open_operations": staged_open,
            "extra_open_work": staged_open - lazy_open,
        }
        if any(row[key] != expected for key, expected in expected_derived.items()):
            _fail(f"query_metrics.csv derived metric differs: {row['query_id']}")
        rows.append(row)
    if len(per_map) != EXPECTED_EVALUATION_MAPS or set(per_map.values()) != {
        EXPECTED_QUERIES_PER_MAP
    }:
        _fail("query_metrics.csv does not contain the exact 8x100 matrix")
    if _csv_bytes(rows, columns) != payload:
        _fail("query_metrics.csv is not in canonical serialized form")
    return rows


def _parse_map_metrics(payload: bytes) -> list[dict[str, Any]]:
    columns = _map_columns()
    decoded = _csv_rows(payload, label="map_metrics.csv", columns=columns)
    if len(decoded) != EXPECTED_EVALUATION_MAPS:
        _fail("map_metrics.csv does not contain exactly eight maps")
    string_fields = {"map", "family", "source_split"}
    int_fields = {"queries", "landmark_build_ns", "packed_distance_bytes"}
    rows: list[dict[str, Any]] = []
    identities: set[tuple[str, str]] = set()
    map_names: set[str] = set()
    for index, raw in enumerate(decoded):
        row: dict[str, Any] = {}
        for column in columns:
            if column in string_fields:
                row[column] = _text(raw[column], label=f"map_metrics[{index}].{column}")
            elif column in int_fields:
                row[column] = _csv_int(
                    raw[column],
                    label=f"map_metrics[{index}].{column}",
                    minimum=1 if column in {"queries", "packed_distance_bytes"} else 0,
                )
            else:
                row[column] = _csv_float(
                    raw[column],
                    label=f"map_metrics[{index}].{column}",
                    nullable="suffix_avoidance_rate" in column,
                )
        if (
            row["map"] in map_names
            or PurePosixPath(row["map"]).name != row["map"]
            or not row["map"].endswith(".map")
            or row["family"] not in EXPECTED_FAMILIES
            or row["source_split"] not in {"validation", "holdout"}
            or row["queries"] != EXPECTED_QUERIES_PER_MAP
        ):
            _fail("map_metrics.csv contains an invalid map identity/count")
        map_names.add(row["map"])
        identities.add((row["family"], row["source_split"]))
        rows.append(row)
    if identities != {
        (family, source_split)
        for family in EXPECTED_FAMILIES
        for source_split in ("validation", "holdout")
    }:
        _fail("map_metrics.csv does not contain one map per family/source split")
    if _csv_bytes(rows, columns) != payload:
        _fail("map_metrics.csv is not in canonical serialized form")
    return rows


def _validate_analysis_tree(root: Path, expected_files: set[str]) -> None:
    expected_root_files = {
        PurePosixPath(name).name
        for name in expected_files
        if PurePosixPath(name).parent == PurePosixPath(".")
    }
    expected_figure_files = {
        PurePosixPath(name).name
        for name in expected_files
        if PurePosixPath(name).parent == PurePosixPath("figures")
    }
    children = list(root.iterdir())
    if any(child.is_symlink() for child in children):
        _fail("analysis root contains a symbolic link")
    root_files = {child.name for child in children if child.is_file()}
    root_directories = {child.name for child in children if child.is_dir()}
    if root_files != expected_root_files | {"manifest.json"} or root_directories != {
        "figures"
    }:
        _fail("analysis root inventory is incomplete or unexpected")
    figures = root / "figures"
    figure_children = list(figures.iterdir())
    if any(child.is_symlink() or not child.is_file() for child in figure_children):
        _fail("analysis figures inventory contains a link or non-file")
    if {child.name for child in figure_children} != expected_figure_files:
        _fail("analysis figures inventory is incomplete or unexpected")


def load_complete_analysis(output_directory: str | Path) -> LoadedAnalysis:
    """Strictly hash-check one completed immutable analysis directory."""

    root = Path(output_directory).resolve(strict=True)
    if not root.is_dir() or root.is_symlink():
        _fail("analysis root must be a real directory")
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        _fail("analysis has no regular manifest completion marker")
    manifest = _object(
        _strict_json_file(manifest_path),
        label="analysis manifest",
        keys={
            "schema",
            "analyzer",
            "protocol_id",
            "complete",
            "validation",
            "artifacts",
            "record_counts",
        },
    )
    if (
        manifest["schema"] != ANALYSIS_MANIFEST_SCHEMA
        or manifest["analyzer"] != ANALYZER_ID
        or manifest["protocol_id"] != "progressive_landmarks_v2"
        or manifest["complete"] is not True
        or manifest["validation"] != "passed"
    ):
        _fail("analysis manifest is not a successful v2 completion marker")
    expected = {
        "summary.json",
        "query_metrics.csv",
        "map_metrics.csv",
        "hypothesis_table.csv",
        "hypothesis_table.tex",
        "provenance.json",
        *(
            f"figures/{stem}.{extension}"
            for stem in FIGURE_STEMS
            for extension in ("png", "pdf")
        ),
    }
    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, Mapping) or set(artifacts) != expected:
        _fail("analysis artifact inventory is incomplete or unexpected")
    _validate_analysis_tree(root, expected)
    payloads: dict[str, bytes] = {}
    for name in sorted(expected):
        binding = _object(
            artifacts[name],
            label=f"analysis artifacts[{name!r}]",
            keys={"sha256", "bytes", "records"},
        )
        path = root / Path(*PurePosixPath(name).parts)
        if not path.is_file() or path.is_symlink():
            _fail(f"analysis artifact is not a regular file: {name}")
        payload = path.read_bytes()
        payloads[name] = payload
        if len(payload) != _plain_int(
            binding["bytes"], label=f"{name}.bytes"
        ) or _sha256_bytes(payload) != _sha(binding["sha256"], label=f"{name}.sha256"):
            _fail(f"analysis artifact binding mismatch: {name}")
    counts = _object(
        manifest["record_counts"],
        label="analysis record_counts",
        keys={"queries", "maps", "hypotheses", "figures"},
    )
    if dict(counts) != {
        "queries": FORMAL_EVALUATION_QUERIES,
        "maps": EXPECTED_EVALUATION_MAPS,
        "hypotheses": 3,
        "figures": len(FIGURE_STEMS) * 2,
    }:
        _fail("analysis record counts differ from the prospective design")
    for name, expected_records in (
        ("query_metrics.csv", FORMAL_EVALUATION_QUERIES),
        ("map_metrics.csv", EXPECTED_EVALUATION_MAPS),
        ("hypothesis_table.csv", 3),
    ):
        if (
            _csv_record_count(payloads[name], label=name) != expected_records
            or artifacts[name]["records"] != expected_records
        ):
            _fail(f"analysis CSV record count mismatch: {name}")
    expected_single_record = expected - {
        "query_metrics.csv",
        "map_metrics.csv",
        "hypothesis_table.csv",
        "hypothesis_table.tex",
    }
    for name in expected_single_record:
        if artifacts[name]["records"] != 1:
            _fail(f"analysis artifact record count mismatch: {name}")
    if artifacts["hypothesis_table.tex"]["records"] != 3:
        _fail("analysis artifact record count mismatch: hypothesis_table.tex")
    summary = _object(
        _strict_json_bytes(payloads["summary.json"], label="summary.json"),
        label="summary.json",
        keys={
            "schema",
            "analyzer",
            "protocol_id",
            "design",
            "integrity",
            "primary_staged_vs_lazy_full",
            "overall",
            "maps",
            "families",
            "descriptive_spearman",
            "preprocessing_amortization",
            "hypothesis_outcomes",
            "figures",
            "summary_sha256",
        },
    )
    summary_core = {
        key: value for key, value in summary.items() if key != "summary_sha256"
    }
    if (
        summary["schema"] != SUMMARY_SCHEMA
        or summary["analyzer"] != ANALYZER_ID
        or summary["summary_sha256"] != canonical_json_sha256(summary_core)
        or summary.get("integrity", {}).get("performance_unlocked_after_all_gates")
        is not True
    ):
        _fail("analysis summary schema, self-hash, or integrity gate is invalid")
    query_metric_rows = _parse_query_metrics(payloads["query_metrics.csv"])
    stored_map_rows = _parse_map_metrics(payloads["map_metrics.csv"])
    raw_map_rows = [
        {
            key: row[key]
            for key in (
                "map",
                "family",
                "source_split",
                "landmark_build_ns",
                "packed_distance_bytes",
            )
        }
        for row in stored_map_rows
    ]
    recomputed_map_rows, recomputed_map_details = _build_map_rows(
        query_metric_rows, raw_map_rows
    )
    if _csv_bytes(recomputed_map_rows, _map_columns()) != payloads["map_metrics.csv"]:
        _fail("map_metrics.csv differs from recomputed query-level evidence")
    recomputed_summary, recomputed_hypotheses = _summarize(
        query_metric_rows, recomputed_map_rows, recomputed_map_details
    )
    if dict(summary) != recomputed_summary:
        _fail("summary.json differs from recomputed query/map evidence")
    hypothesis_columns = (
        "hypothesis",
        "status",
        "estimand",
        "estimate",
        "interval",
        "interpretation",
    )
    if (
        _csv_bytes(recomputed_hypotheses, hypothesis_columns)
        != payloads["hypothesis_table.csv"]
        or _hypothesis_tex(recomputed_hypotheses) != payloads["hypothesis_table.tex"]
    ):
        _fail("hypothesis tables differ from recomputed analysis outcomes")
    provenance = _object(
        _strict_json_bytes(payloads["provenance.json"], label="provenance.json"),
        label="provenance.json",
        keys={
            "schema",
            "analyzer",
            "analysis_code",
            "protocol",
            "sealed_evaluation",
            "development_authorization",
            "analysis_parameters",
            "source_run_timestamps",
            "provenance_sha256",
        },
    )
    provenance_core = {
        key: value for key, value in provenance.items() if key != "provenance_sha256"
    }
    if (
        provenance["schema"] != PROVENANCE_SCHEMA
        or provenance["analyzer"] != ANALYZER_ID
        or provenance["provenance_sha256"] != canonical_json_sha256(provenance_core)
        or provenance.get("development_authorization", {}).get(
            "development_outcomes_used_in_analysis"
        )
        is not False
    ):
        _fail("analysis provenance schema, self-hash, or split boundary is invalid")
    return LoadedAnalysis(
        root,
        _freeze_value(dict(manifest)),
        _freeze_value(dict(summary)),
        _freeze_value(dict(provenance)),
    )


__all__ = [
    "AMORTIZATION_QUERIES",
    "ANALYSIS_MANIFEST_SCHEMA",
    "ANALYZER_ID",
    "AnalysisError",
    "BOOTSTRAP_REPLICATES",
    "BOOTSTRAP_SEED",
    "LoadedAnalysis",
    "SUMMARY_SCHEMA",
    "analyze_sealed_evaluation",
    "distribution_summary",
    "even_sample_median",
    "hierarchical_bootstrap_median",
    "load_complete_analysis",
    "spearman_rho",
]
