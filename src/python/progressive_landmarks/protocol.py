"""Strict, read-only protocol construction for progressive-landmark experiments.

The checked-in configuration describes *how* queries are selected.  This module
revalidates the checksum-pinned Moving AI snapshot and materializes the exact
query rows into a canonical JSON plan.  It intentionally contains no search or
landmark implementation.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping, Sequence


SCHEMA = "progressive-landmarks-protocol-v2"
CORPUS_SCHEMA = "movingai-mapf-corpus-manifest-v1"
CORPUS_ID = "cbs_cgdgwdg_map_split_v1"
FAMILIES = ("maze", "random", "room", "warehouse")
SOURCE_SPLITS = ("train", "validation", "holdout")
EXPERIMENT_SPLITS = ("development", "sealed_evaluation")
METHODS = ("manhattan", "eager_full", "lazy_full", "staged")
SCENARIO_INDICES = (1, 2, 3, 4)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ProtocolError(ValueError):
    """Raised when a protocol or any bound input fails closed validation."""


@dataclass(frozen=True, slots=True)
class Grid:
    width: int
    height: int
    rows: tuple[str, ...]
    component_by_state: Mapping[tuple[int, int], int]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ProtocolError(f"duplicate JSON key: {key!r}")
        value[key] = item
    return value


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
        value = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda token: (_raise(f"non-finite JSON number: {token}")),
        )
    except ProtocolError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"cannot strictly decode {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProtocolError(f"{path}: top-level JSON value must be an object")
    return value


def _raise(message: str) -> None:
    raise ProtocolError(message)


def _expect_object(value: Any, where: str, keys: Iterable[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError(f"{where} must be an object")
    expected = set(keys)
    actual = set(value)
    if actual != expected:
        raise ProtocolError(
            f"{where} keys differ: missing={sorted(expected - actual)}, "
            f"unexpected={sorted(actual - expected)}"
        )
    return value


def _expect_list(value: Any, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise ProtocolError(f"{where} must be an array")
    return value


def _expect_str(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise ProtocolError(f"{where} must be a string")
    return value


def _expect_bool(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise ProtocolError(f"{where} must be a boolean")
    return value


def _expect_int(value: Any, where: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError(f"{where} must be an integer")
    if positive and value <= 0:
        raise ProtocolError(f"{where} must be positive")
    return value


def _expect_literal(value: Any, expected: Any, where: str) -> None:
    if value != expected or type(value) is not type(expected):
        raise ProtocolError(f"{where} must equal {expected!r}")


def _safe_relative_path(value: Any, where: str) -> str:
    text = _expect_str(value, where)
    path = PurePosixPath(text)
    if (
        not text
        or text != path.as_posix()
        or path.is_absolute()
        or "\\" in text
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ProtocolError(f"{where} must be a canonical safe relative POSIX path")
    return text


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ProtocolError(f"cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    """Encode a plan deterministically for content addressing."""

    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def load_protocol(path: Path | str) -> dict[str, Any]:
    """Strictly decode and structurally validate a protocol configuration."""

    config = _read_json(Path(path))
    _validate_config(config)
    return config


def _validate_config(config: dict[str, Any]) -> None:
    _expect_object(
        config,
        "config",
        {
            "schema",
            "protocol_id",
            "master_seed",
            "source_snapshot",
            "experimental_split",
            "query_selection",
            "search_model",
            "methods",
            "landmarks",
            "timing",
            "limits",
        },
    )
    _expect_literal(config["schema"], SCHEMA, "config.schema")
    _expect_literal(
        config["protocol_id"], "progressive_landmarks_v2", "config.protocol_id"
    )
    _expect_literal(config["master_seed"], 23725513, "config.master_seed")

    source = _expect_object(
        config["source_snapshot"],
        "config.source_snapshot",
        {
            "relative_root",
            "corpus_id",
            "manifest_schema",
            "manifest",
            "checksums",
            "payload_roots",
        },
    )
    _expect_literal(
        _safe_relative_path(source["relative_root"], "source.relative_root"),
        "data/source/movingai_mapf_2021-06-17",
        "source.relative_root",
    )
    _expect_literal(source["corpus_id"], CORPUS_ID, "source.corpus_id")
    _expect_literal(source["manifest_schema"], CORPUS_SCHEMA, "source.manifest_schema")
    for key, expected_path in (
        ("manifest", "CORPUS_MANIFEST.json"),
        ("checksums", "SHA256SUMS"),
    ):
        binding = _expect_object(source[key], f"source.{key}", {"path", "sha256"})
        _expect_literal(
            _safe_relative_path(binding["path"], f"source.{key}.path"),
            expected_path,
            f"source.{key}.path",
        )
        digest = _expect_str(binding["sha256"], f"source.{key}.sha256")
        if SHA256_RE.fullmatch(digest) is None:
            raise ProtocolError(f"source.{key}.sha256 must be lowercase SHA-256")
    _expect_literal(source["payload_roots"], ["archives", "corpus"], "payload_roots")

    split = _expect_object(
        config["experimental_split"],
        "config.experimental_split",
        {
            "families",
            "development_source_splits",
            "sealed_evaluation_source_splits",
            "maps",
        },
    )
    _expect_literal(split["families"], list(FAMILIES), "experimental_split.families")
    _expect_literal(
        split["development_source_splits"],
        ["train"],
        "experimental_split.development_source_splits",
    )
    _expect_literal(
        split["sealed_evaluation_source_splits"],
        ["validation", "holdout"],
        "experimental_split.sealed_evaluation_source_splits",
    )
    maps = _expect_object(
        split["maps"], "experimental_split.maps", set(EXPERIMENT_SPLITS)
    )
    expected_lengths = {"development": 4, "sealed_evaluation": 8}
    all_names: list[str] = []
    for split_name in EXPERIMENT_SPLITS:
        rows = _expect_list(maps[split_name], f"maps.{split_name}")
        if len(rows) != expected_lengths[split_name]:
            raise ProtocolError(
                f"maps.{split_name} must contain {expected_lengths[split_name]} maps"
            )
        for index, row in enumerate(rows):
            item = _expect_object(
                row,
                f"maps.{split_name}[{index}]",
                {"family", "map", "source_split"},
            )
            family = _expect_str(item["family"], "map.family")
            map_name = _safe_relative_path(item["map"], "map.map")
            source_split = _expect_str(item["source_split"], "map.source_split")
            if family not in FAMILIES or source_split not in SOURCE_SPLITS:
                raise ProtocolError(f"invalid family or source split for {map_name}")
            allowed = (
                ["train"] if split_name == "development" else ["validation", "holdout"]
            )
            if source_split not in allowed:
                raise ProtocolError(
                    f"{map_name}: source split leaks across experiment split"
                )
            if PurePosixPath(map_name).name != map_name or not map_name.endswith(
                ".map"
            ):
                raise ProtocolError(
                    f"map name must be a plain .map filename: {map_name}"
                )
            all_names.append(map_name)
    if len(set(all_names)) != len(all_names):
        raise ProtocolError("development and sealed evaluation maps must not overlap")

    selection = _expect_object(
        config["query_selection"],
        "config.query_selection",
        {
            "scenario_kind",
            "scenario_file_indices",
            "development_valid_rows_per_file",
            "sealed_evaluation_valid_rows_per_file",
            "selection_rule",
            "validity_rule",
            "duplicate_key",
            "upstream_reference_distance",
            "correctness_oracle",
        },
    )
    frozen_selection = {
        "scenario_kind": "random",
        "scenario_file_indices": list(SCENARIO_INDICES),
        "development_valid_rows_per_file": 10,
        "sealed_evaluation_valid_rows_per_file": 25,
        "selection_rule": "first-valid-n-in-source-order",
        "validity_rule": "distinct-traversable-endpoints-in-same-4-neighbor-component",
        "duplicate_key": "map-start-goal",
        "upstream_reference_distance": "ignored",
        "correctness_oracle": "independent-unit-cost-4-neighbor-bfs",
    }
    for key, expected in frozen_selection.items():
        _expect_literal(selection[key], expected, f"query_selection.{key}")

    search = _expect_object(
        config["search_model"],
        "config.search_model",
        {
            "agents",
            "movement",
            "edge_cost",
            "wait_actions",
            "traversable_symbols",
            "blocked_symbols",
        },
    )
    frozen_search = {
        "agents": 1,
        "movement": "4-neighbor-cardinal",
        "edge_cost": 1,
        "wait_actions": False,
        "traversable_symbols": ["."],
        "blocked_symbols": ["@", "T"],
    }
    for key, expected in frozen_search.items():
        if key == "wait_actions":
            _expect_bool(search[key], f"search_model.{key}")
        _expect_literal(search[key], expected, f"search_model.{key}")

    _expect_literal(config["methods"], list(METHODS), "config.methods")
    landmarks = _expect_object(
        config["landmarks"],
        "config.landmarks",
        {
            "scope",
            "selection",
            "distance_model",
            "full_pivots",
            "staged_prefix_pivots",
            "initial_pivot",
            "farthest_tie_break",
        },
    )
    frozen_landmarks = {
        "scope": "per-map",
        "selection": "deterministic-row-major-farthest-first-v1",
        "distance_model": "unit-cost-4-neighbor",
        "full_pivots": 32,
        "staged_prefix_pivots": 4,
        "initial_pivot": "row-major-first-traversable-state",
        "farthest_tie_break": "row-major",
    }
    for key, expected in frozen_landmarks.items():
        _expect_literal(landmarks[key], expected, f"landmarks.{key}")

    timing = _expect_object(
        config["timing"],
        "config.timing",
        {
            "warmup_repetitions",
            "timed_repetitions",
            "method_order",
            "warmup_rotation",
        },
    )
    frozen_timing = {
        "warmup_repetitions": 1,
        "timed_repetitions": 8,
        "method_order": "left-rotate-base-order-by-repetition",
        "warmup_rotation": 0,
    }
    for key, expected in frozen_timing.items():
        _expect_literal(timing[key], expected, f"timing.{key}")

    limits = _expect_object(
        config["limits"],
        "config.limits",
        {
            "max_maps",
            "max_scenario_files",
            "max_queries",
            "max_planned_search_runs",
            "max_scenario_rows_scanned_per_file",
            "max_map_cells",
        },
    )
    for key, value in limits.items():
        _expect_int(value, f"limits.{key}", positive=True)
    exact_limits = {
        "max_maps": 12,
        "max_scenario_files": 48,
        "max_queries": 960,
        "max_planned_search_runs": 34560,
        "max_scenario_rows_scanned_per_file": 10000,
        "max_map_cells": 100000,
    }
    for key, expected in exact_limits.items():
        _expect_literal(limits[key], expected, f"limits.{key}")


def _load_checksums(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ProtocolError(f"cannot read {path}: {exc}") from exc
    checksums: dict[str, str] = {}
    for line_number, row in enumerate(lines, start=1):
        if not row:
            raise ProtocolError(f"{path.name}:{line_number}: blank rows are forbidden")
        parts = row.split("  ", 1)
        if len(parts) != 2 or SHA256_RE.fullmatch(parts[0]) is None:
            raise ProtocolError(f"{path.name}:{line_number}: malformed checksum row")
        relative = _safe_relative_path(parts[1], f"{path.name}:{line_number}.path")
        if relative in checksums:
            raise ProtocolError(f"{path.name}:{line_number}: duplicate path {relative}")
        checksums[relative] = parts[0]
    return checksums


def _read_grid(path: Path, cell_cap: int) -> Grid:
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ProtocolError(f"cannot read map {path}: {exc}") from exc
    if len(lines) < 4 or lines[0] != "type octile" or lines[3] != "map":
        raise ProtocolError(f"{path.name}: invalid Moving AI map header")
    height_fields, width_fields = lines[1].split(), lines[2].split()
    if (
        len(height_fields) != 2
        or height_fields[0] != "height"
        or len(width_fields) != 2
        or width_fields[0] != "width"
    ):
        raise ProtocolError(f"{path.name}: invalid map dimensions")
    try:
        height, width = int(height_fields[1]), int(width_fields[1])
    except ValueError as exc:
        raise ProtocolError(f"{path.name}: non-integer map dimensions") from exc
    if height <= 0 or width <= 0 or height * width > cell_cap:
        raise ProtocolError(f"{path.name}: invalid or over-cap map extent")
    rows = tuple(lines[4:])
    if len(rows) != height or any(len(row) != width for row in rows):
        raise ProtocolError(f"{path.name}: map extent disagrees with header")
    symbols = set("".join(rows))
    if not symbols <= {".", "@", "T"}:
        raise ProtocolError(
            f"{path.name}: unexpected terrain symbols {sorted(symbols)}"
        )

    component: dict[tuple[int, int], int] = {}
    component_id = 0
    for y, row in enumerate(rows):
        for x, symbol in enumerate(row):
            if symbol != "." or (x, y) in component:
                continue
            component[(x, y)] = component_id
            frontier = deque([(x, y)])
            while frontier:
                cx, cy = frontier.popleft()
                for nx, ny in (
                    (cx + 1, cy),
                    (cx - 1, cy),
                    (cx, cy + 1),
                    (cx, cy - 1),
                ):
                    state = (nx, ny)
                    if (
                        0 <= nx < width
                        and 0 <= ny < height
                        and rows[ny][nx] == "."
                        and state not in component
                    ):
                        component[state] = component_id
                        frontier.append(state)
            component_id += 1
    if not component:
        raise ProtocolError(f"{path.name}: map has no traversable state")
    return Grid(width, height, rows, component)


def _scenario_name(map_name: str, index: int) -> str:
    return f"{Path(map_name).stem}-random-{index}.scen"


def _select_queries(
    *,
    path: Path,
    expected_map: str,
    grid: Grid,
    experiment_split: str,
    family: str,
    source_split: str,
    scenario_index: int,
    count: int,
    scan_cap: int,
) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ProtocolError(f"cannot read scenario {path}: {exc}") from exc
    if not lines or lines[0] not in {"version 1", "version 1.0"}:
        raise ProtocolError(f"{path.name}: invalid Moving AI scenario header")
    selected: list[dict[str, Any]] = []
    rows = lines[1:]
    for ordinal, line in enumerate(rows[:scan_cap], start=1):
        line_number = ordinal + 1
        fields = line.split("\t")
        if len(fields) != 9:
            raise ProtocolError(
                f"{path.name}:{line_number}: expected 9 tab-separated fields"
            )
        try:
            bucket = int(fields[0])
            width, height = int(fields[2]), int(fields[3])
            sx, sy, gx, gy = map(int, fields[4:8])
            float(fields[8])  # Syntax check only; the diagonal distance is ignored.
        except ValueError as exc:
            raise ProtocolError(
                f"{path.name}:{line_number}: invalid numeric field"
            ) from exc
        if fields[1] != expected_map or (width, height) != (grid.width, grid.height):
            raise ProtocolError(
                f"{path.name}:{line_number}: map identity or dimensions disagree"
            )
        start, goal = (sx, sy), (gx, gy)
        start_component = grid.component_by_state.get(start)
        goal_component = grid.component_by_state.get(goal)
        if (
            start != goal
            and start_component is not None
            and start_component == goal_component
        ):
            query_id = (
                f"{experiment_split}:{Path(expected_map).stem}:"
                f"s{scenario_index}:r{line_number}"
            )
            selected.append(
                {
                    "query_id": query_id,
                    "experiment_split": experiment_split,
                    "source_split": source_split,
                    "family": family,
                    "map": expected_map,
                    "scenario": path.name,
                    "scenario_index": scenario_index,
                    "scenario_row": ordinal,
                    "source_line": line_number,
                    "source_bucket": bucket,
                    "start": [sx, sy],
                    "goal": [gx, gy],
                }
            )
            if len(selected) == count:
                break
    if len(selected) != count:
        raise ProtocolError(
            f"{path.name}: required first {count} valid rows, found {len(selected)} "
            f"within scan cap {scan_cap}"
        )
    return selected


def _verify_source(
    config: dict[str, Any], repository_root: Path
) -> tuple[Path, dict[str, Any], dict[str, str], str, str]:
    source_config = config["source_snapshot"]
    source_root = repository_root / Path(source_config["relative_root"])
    manifest_path = source_root / source_config["manifest"]["path"]
    checksums_path = source_root / source_config["checksums"]["path"]
    manifest_hash, checksums_hash = _sha256(manifest_path), _sha256(checksums_path)
    if manifest_hash != source_config["manifest"]["sha256"]:
        raise ProtocolError("bound corpus manifest SHA-256 does not match")
    if checksums_hash != source_config["checksums"]["sha256"]:
        raise ProtocolError("bound SHA256SUMS SHA-256 does not match")
    manifest = _read_json(manifest_path)
    if (
        manifest.get("schema") != CORPUS_SCHEMA
        or manifest.get("corpus_id") != CORPUS_ID
    ):
        raise ProtocolError("bound corpus manifest identity does not match")
    checksums = _load_checksums(checksums_path)

    payloads: set[str] = set()
    for root_name in source_config["payload_roots"]:
        root = source_root / root_name
        if not root.is_dir():
            raise ProtocolError(f"missing payload root: {root_name}")
        payloads.update(
            path.relative_to(source_root).as_posix()
            for path in root.rglob("*")
            if path.is_file()
        )
    if payloads != set(checksums):
        raise ProtocolError(
            "checksum inventory differs from payload inventory: "
            f"unpinned={sorted(payloads - set(checksums))}, "
            f"missing={sorted(set(checksums) - payloads)}"
        )
    for relative, expected in checksums.items():
        if _sha256(source_root / Path(relative)) != expected:
            raise ProtocolError(f"payload SHA-256 mismatch: {relative}")
    return source_root, manifest, checksums, manifest_hash, checksums_hash


def _rotated_method_orders(config: Mapping[str, Any]) -> dict[str, Any]:
    methods = list(config["methods"])

    def rotate(amount: int) -> list[str]:
        offset = amount % len(methods)
        return methods[offset:] + methods[:offset]

    warmups = [
        {
            "repetition": index,
            "timed": False,
            "methods": rotate(config["timing"]["warmup_rotation"] + index),
        }
        for index in range(config["timing"]["warmup_repetitions"])
    ]
    timed = [
        {"repetition": index, "timed": True, "methods": rotate(index)}
        for index in range(config["timing"]["timed_repetitions"])
    ]
    return {"warmup": warmups, "timed": timed}


def build_plan(
    config: Mapping[str, Any],
    *,
    repository_root: Path | str,
    config_path: Path | str | None = None,
) -> dict[str, Any]:
    """Verify all bound inputs and materialize the exact canonical experiment plan."""

    # A mapping supplied programmatically receives the same validation as disk JSON.
    normalized = json.loads(canonical_json_bytes(config))
    _validate_config(normalized)
    root = Path(repository_root).resolve()
    source_root, manifest, checksums, manifest_hash, checksums_hash = _verify_source(
        normalized, root
    )
    manifest_maps = manifest.get("maps")
    if not isinstance(manifest_maps, list):
        raise ProtocolError("corpus manifest maps must be an array")
    registered: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(manifest_maps):
        item = _expect_object(
            row,
            f"corpus.maps[{index}]",
            {"family", "map", "split", "split_digest", "states", "agent_bands"},
        )
        map_name = _expect_str(item["map"], f"corpus.maps[{index}].map")
        if map_name in registered:
            raise ProtocolError(f"duplicate map in corpus manifest: {map_name}")
        registered[map_name] = item
    if len(registered) != 12:
        raise ProtocolError("corpus manifest must register exactly 12 maps")

    if config_path is None:
        config_binding = {
            "path": None,
            "sha256": canonical_json_sha256(normalized),
            "hash_basis": "canonical-json",
        }
    else:
        resolved_config = Path(config_path).resolve()
        try:
            relative_config = resolved_config.relative_to(root).as_posix()
        except ValueError:
            relative_config = f"<external>/{resolved_config.name}"
        config_binding = {
            "path": relative_config,
            "sha256": _sha256(resolved_config),
            "hash_basis": "file-bytes",
        }
    source_binding = {
        "relative_root": normalized["source_snapshot"]["relative_root"],
        "corpus_id": CORPUS_ID,
        "manifest": {
            "path": normalized["source_snapshot"]["manifest"]["path"],
            "sha256": manifest_hash,
        },
        "checksums": {
            "path": normalized["source_snapshot"]["checksums"]["path"],
            "sha256": checksums_hash,
        },
    }
    queries: list[dict[str, Any]] = []
    input_bindings: list[dict[str, Any]] = []
    observed_maps: dict[str, list[str]] = {key: [] for key in EXPERIMENT_SPLITS}
    selected_map_names: set[str] = set()
    for experiment_split in EXPERIMENT_SPLITS:
        map_rows = normalized["experimental_split"]["maps"][experiment_split]
        per_file = (
            normalized["query_selection"]["development_valid_rows_per_file"]
            if experiment_split == "development"
            else normalized["query_selection"]["sealed_evaluation_valid_rows_per_file"]
        )
        for map_row in map_rows:
            family, map_name, source_split = (
                map_row["family"],
                map_row["map"],
                map_row["source_split"],
            )
            source_item = registered.get(map_name)
            if source_item is None:
                raise ProtocolError(
                    f"configured map is absent from corpus manifest: {map_name}"
                )
            if (
                source_item.get("family") != family
                or source_item.get("split") != source_split
            ):
                raise ProtocolError(
                    f"configured identity disagrees with corpus for {map_name}"
                )
            selected_map_names.add(map_name)
            observed_maps[experiment_split].append(map_name)
            map_relative = f"corpus/maps/{map_name}"
            if map_relative not in checksums:
                raise ProtocolError(f"map absent from SHA256SUMS: {map_relative}")
            grid = _read_grid(
                source_root / Path(map_relative), normalized["limits"]["max_map_cells"]
            )
            if len(grid.component_by_state) != source_item.get("states"):
                raise ProtocolError(f"traversable-state count disagrees for {map_name}")
            scenario_bindings: list[dict[str, Any]] = []
            map_queries: list[dict[str, Any]] = []
            for scenario_index in SCENARIO_INDICES:
                scenario_name = _scenario_name(map_name, scenario_index)
                scenario_relative = f"corpus/scenarios/{scenario_name}"
                if scenario_relative not in checksums:
                    raise ProtocolError(
                        f"scenario absent from SHA256SUMS: {scenario_relative}"
                    )
                scenario_bindings.append(
                    {
                        "index": scenario_index,
                        "path": scenario_relative,
                        "sha256": checksums[scenario_relative],
                    }
                )
                map_queries.extend(
                    _select_queries(
                        path=source_root / Path(scenario_relative),
                        expected_map=map_name,
                        grid=grid,
                        experiment_split=experiment_split,
                        family=family,
                        source_split=source_split,
                        scenario_index=scenario_index,
                        count=per_file,
                        scan_cap=normalized["limits"][
                            "max_scenario_rows_scanned_per_file"
                        ],
                    )
                )
            input_bindings.append(
                {
                    "experiment_split": experiment_split,
                    "source_split": source_split,
                    "family": family,
                    "map": {
                        "path": map_relative,
                        "sha256": checksums[map_relative],
                        "width": grid.width,
                        "height": grid.height,
                        "traversable_states": len(grid.component_by_state),
                        "connected_components": len(
                            set(grid.component_by_state.values())
                        ),
                    },
                    "scenarios": scenario_bindings,
                    "query_count": len(map_queries),
                }
            )
            queries.extend(map_queries)
    if selected_map_names != set(registered):
        raise ProtocolError(
            "configured maps do not exactly cover corpus manifest: "
            f"missing={sorted(set(registered) - selected_map_names)}, "
            f"unexpected={sorted(selected_map_names - set(registered))}"
        )

    split_counts = Counter(query["experiment_split"] for query in queries)
    expected_split_counts = {"development": 160, "sealed_evaluation": 800}
    if dict(split_counts) != expected_split_counts or len(queries) != 960:
        raise ProtocolError(
            f"query matrix mismatch: total={len(queries)}, splits={dict(split_counts)}"
        )
    query_ids = [query["query_id"] for query in queries]
    duplicate_keys = [
        (query["map"], tuple(query["start"]), tuple(query["goal"])) for query in queries
    ]
    if len(set(query_ids)) != len(query_ids):
        raise ProtocolError("duplicate query IDs in selected plan")
    if len(set(duplicate_keys)) != len(duplicate_keys):
        raise ProtocolError("duplicate map/start/goal queries in selected plan")
    if set(observed_maps["development"]) & set(observed_maps["sealed_evaluation"]):
        raise ProtocolError("map overlap between development and sealed evaluation")

    family_counts = Counter(
        (query["experiment_split"], query["family"]) for query in queries
    )
    expected_family_counts = {
        **{("development", family): 40 for family in FAMILIES},
        **{("sealed_evaluation", family): 200 for family in FAMILIES},
    }
    if dict(family_counts) != expected_family_counts:
        raise ProtocolError(f"family/query matrix mismatch: {dict(family_counts)}")
    orders = _rotated_method_orders(normalized)
    planned_search_runs = (
        len(queries)
        * len(METHODS)
        * (
            normalized["timing"]["warmup_repetitions"]
            + normalized["timing"]["timed_repetitions"]
        )
    )
    observed_limits = {
        "maps": len(input_bindings),
        "scenario_files": sum(len(item["scenarios"]) for item in input_bindings),
        "queries": len(queries),
        "planned_search_runs": planned_search_runs,
    }
    cap_keys = {
        "maps": "max_maps",
        "scenario_files": "max_scenario_files",
        "queries": "max_queries",
        "planned_search_runs": "max_planned_search_runs",
    }
    for observed_key, cap_key in cap_keys.items():
        if observed_limits[observed_key] > normalized["limits"][cap_key]:
            raise ProtocolError(f"{observed_key} exceeds configured workload cap")

    core = {
        "schema": "progressive-landmarks-plan-v2",
        "protocol_id": normalized["protocol_id"],
        "master_seed": normalized["master_seed"],
        "config_binding": config_binding,
        "source_binding": source_binding,
        "methods": list(METHODS),
        "landmarks": normalized["landmarks"],
        "search_model": normalized["search_model"],
        "timing_orders": orders,
        "input_bindings": input_bindings,
        "queries": queries,
        "counts": {
            "maps": 12,
            "scenario_files": 48,
            "queries": 960,
            "development_queries": 160,
            "sealed_evaluation_queries": 800,
            "methods": 4,
            "warmup_repetitions": 1,
            "timed_repetitions": 8,
            "planned_search_runs": 34560,
        },
    }
    return {**core, "plan_sha256": canonical_json_sha256(core)}


def verify_protocol(
    config_path: Path | str, *, repository_root: Path | str
) -> dict[str, Any]:
    """Load, verify, and return the exact deterministic experiment plan."""

    return build_plan(
        load_protocol(config_path),
        repository_root=repository_root,
        config_path=config_path,
    )
