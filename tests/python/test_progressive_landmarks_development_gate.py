"""Fail-closed tests for the external formal-development authorization gate."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import progressive_landmarks.development_gate as gate_module
from progressive_landmarks.development_gate import (
    AUDIT_SCHEMA,
    AUDITOR_ID,
    DevelopmentGateError,
    audit_formal_development,
    freeze_formal_development,
    load_development_audit,
)
from progressive_landmarks.protocol import METHODS, canonical_json_sha256
from progressive_landmarks.runner import (
    FREEZE_SCHEMA,
    FORMAL_DEVELOPMENT_QUERIES,
    SEARCHES_PER_QUERY,
    TIMED_REPETITIONS,
    WARMUP_REPETITIONS,
)


SHA = "a" * 64
COUNTER_NAMES = {
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


def _schedule() -> list[dict]:
    rows = [
        {
            "phase": "warmup",
            "repetition": 0,
            "timed": False,
            "methods": list(METHODS),
        }
    ]
    for repetition in range(TIMED_REPETITIONS):
        offset = repetition % len(METHODS)
        rows.append(
            {
                "phase": "timed",
                "repetition": repetition,
                "timed": True,
                "methods": list(METHODS[offset:] + METHODS[:offset]),
            }
        )
    return rows


def _query(index: int, map_name: str, family: str) -> dict:
    scenario_index = (index // 10) % 4 + 1
    scenario_row = index % 10 + 1
    return {
        "query_id": f"development:{map_name}:s{scenario_index}:r{scenario_row}",
        "experiment_split": "development",
        "source_split": "train",
        "family": family,
        "map": map_name,
        "scenario": f"{map_name[:-4]}-random-{scenario_index}.scen",
        "scenario_index": scenario_index,
        "scenario_row": scenario_row,
        "source_line": scenario_row + 1,
        "source_bucket": index,
        "start": [0, 0],
        "goal": [1, 0],
    }


def _plan() -> dict:
    families = ("maze", "random", "room", "warehouse")
    map_names = tuple(f"{family}.map" for family in families)
    inputs = []
    queries = []
    for family, name in zip(families, map_names, strict=True):
        inputs.append(
            {
                "experiment_split": "development",
                "source_split": "train",
                "family": family,
                "map": {
                    "path": f"corpus/maps/{name}",
                    "sha256": hashlib.sha256(name.encode()).hexdigest(),
                    "width": 2,
                    "height": 1,
                    "traversable_states": 2,
                    "connected_components": 1,
                },
                "scenarios": [],
                "query_count": 40,
            }
        )
        queries.extend(_query(index, name, family) for index in range(40))
    schedule = _schedule()
    core = {
        "schema": "progressive-landmarks-plan-v2",
        "protocol_id": "progressive_landmarks_v2",
        "master_seed": 23725513,
        "config_binding": {
            "path": "configs/progressive_landmarks_v2.json",
            "sha256": "b" * 64,
            "hash_basis": "file-bytes",
        },
        "source_binding": {"relative_root": "source", "corpus_id": "fixture"},
        "methods": list(METHODS),
        "landmarks": {"full_pivots": 32, "staged_prefix_pivots": 4},
        "search_model": {},
        "timing_orders": {
            "warmup": [
                {key: value for key, value in schedule[0].items() if key != "phase"}
            ],
            "timed": [
                {key: value for key, value in row.items() if key != "phase"}
                for row in schedule[1:]
            ],
        },
        "input_bindings": inputs,
        "queries": queries,
        "counts": {},
    }
    return {**core, "plan_sha256": canonical_json_sha256(core)}


def _bindings() -> dict:
    code_files = {
        name: {"path": f"{name}.py", "sha256": character * 64, "bytes": 10}
        for name, character in zip(
            (
                "analysis",
                "analysis_cli",
                "cli",
                "core",
                "development_gate",
                "freeze_cli",
                "package_init",
                "protocol",
                "runner",
            ),
            "012345678",
            strict=True,
        )
    }
    snapshot = {"corpus_id": "fixture", "manifest": {"sha256": "1" * 64}}
    environment_value = {
        "fixture": True,
    }
    return {
        "protocol_id": "progressive_landmarks_v2",
        "config": {
            "path": "configs/progressive_landmarks_v2.json",
            "sha256": "b" * 64,
            "hash_basis": "file-bytes",
        },
        "plan_sha256": _plan()["plan_sha256"],
        "code": {"files": code_files, "sha256": canonical_json_sha256(code_files)},
        "source": {
            "value": {"snapshot": snapshot, "split_inputs": []},
            "sha256": "2" * 64,
        },
        "environment": {
            "value": environment_value,
            "sha256": canonical_json_sha256(environment_value),
        },
    }


def _freeze_bindings(bindings: dict) -> dict:
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
            bindings["source"]["value"]["snapshot"]
        ),
        "environment_sha256": bindings["environment"]["sha256"],
    }


def _counters(method: str) -> dict[str, int]:
    values = {name: 0 for name in COUNTER_NAMES}
    values.update(
        expanded=1,
        generated=1,
        relaxations=1,
        manhattan_calls=2,
        unique_discovered=2,
        max_open_entries=1,
        max_live_states=1,
    )
    if method == "manhattan":
        values["pops"] = 2
    elif method == "eager_full":
        values.update(
            pops=2, full_calls=2, pivot_evaluations=64, distance_table_reads=96
        )
    elif method == "lazy_full":
        values.update(
            pops=2, full_calls=1, pivot_evaluations=32, distance_table_reads=64
        )
    else:
        values.update(
            pops=2,
            prefix_calls=1,
            suffix_calls=1,
            pivot_evaluations=32,
            distance_table_reads=64,
        )
    return values


def _summary(method: str) -> dict:
    return {
        "mode": method,
        "found": True,
        "cost": 1,
        "path_sha256": hashlib.sha256(f"path:{method}".encode()).hexdigest(),
        "expansion_digest": (
            hashlib.sha256(b"manhattan").hexdigest()
            if method == "manhattan"
            else hashlib.sha256(b"full").hexdigest()
        ),
        "counters": _counters(method),
    }


def _loaded_fixture(tmp_path: Path):
    from progressive_landmarks.runner import LoadedRun

    result_root = tmp_path / "results" / "progressive_landmarks_development_v2"
    result_root.mkdir(parents=True)
    (result_root / "manifest.json").write_text("{}\n", encoding="ascii")
    plan = _plan()
    bindings = _bindings()
    schedule = _schedule()
    summaries = {method: _summary(method) for method in METHODS}
    query_rows = []
    for index, planned in enumerate(plan["queries"]):
        runs = []
        for schedule_row in schedule:
            for order_position, method in enumerate(schedule_row["methods"]):
                runs.append(
                    {
                        "phase": schedule_row["phase"],
                        "repetition": schedule_row["repetition"],
                        "timed": schedule_row["timed"],
                        "order_position": order_position,
                        "method": method,
                        "timing_ns": {"stage_ns": 0, "search_ns": index + 1},
                        "result": summaries[method],
                    }
                )
        query_rows.append(
            {
                "sequence_index": index,
                "experiment_split": "development",
                "query": {
                    key: value
                    for key, value in planned.items()
                    if key != "experiment_split"
                },
                "oracle": {"algorithm": "bfs", "cost": 1, "path_sha256": SHA},
                "deterministic_by_method": summaries,
                "runs": tuple(runs),
                "validation": {
                    "all_costs_match_bfs": True,
                    "all_repetitions_deterministic": True,
                    "full_landmark_expansion_digests_match": True,
                },
            }
        )
    maps = []
    for item in plan["input_bindings"]:
        binding = item["map"]
        maps.append(
            {
                "map": Path(binding["path"]).name,
                "family": item["family"],
                "source_split": "train",
                "map_sha256": binding["sha256"],
                "width": 2,
                "height": 1,
                "traversable_states": 2,
                "landmark_build_ns": 1,
                "packed_distance_bytes": 16,
                "requested_landmarks": 32,
                "actual_landmarks": 2,
                "landmarks": ((0, 0), (1, 0)),
                "table_sha256": hashlib.sha256(item["family"].encode()).hexdigest(),
            }
        )
    artifacts = {
        name: {
            "sha256": hashlib.sha256(name.encode()).hexdigest(),
            "bytes": 1,
            "records": records,
        }
        for name, records in (
            ("queries.jsonl", 160),
            ("maps.json", 4),
            ("run.json", 1),
            ("development_freeze_candidate.json", 1),
        )
    }
    loaded = LoadedRun(
        root=result_root,
        manifest={
            "experiment_split": "development",
            "formal": True,
            "complete": True,
            "validation": "passed",
            "record_counts": {
                "maps": 4,
                "queries": 160,
                "search_runs": 160 * SEARCHES_PER_QUERY,
            },
            "artifacts": artifacts,
        },
        run={
            "protocol_id": "progressive_landmarks_v2",
            "experiment_split": "development",
            "formal": True,
            "nonformal_smoke": False,
            "status": "complete",
            "validation": "passed",
            "methods": list(METHODS),
            "schedule": schedule,
            "landmarks": {"full_pivots": 32, "staged_prefix_pivots": 4},
            "counts": {
                "maps": 4,
                "queries": 160,
                "warmup_repetitions": WARMUP_REPETITIONS,
                "timed_repetitions": TIMED_REPETITIONS,
                "searches_per_query": SEARCHES_PER_QUERY,
                "search_runs": 160 * SEARCHES_PER_QUERY,
            },
            "bindings": bindings,
            "evaluation_authorization": None,
        },
        maps={"maps": tuple(maps)},
        queries=tuple(query_rows),
    )
    return loaded, plan, _freeze_bindings(bindings)


def _install_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    loaded, plan, freeze_bindings = _loaded_fixture(tmp_path)
    repository = tmp_path / "repository"
    repository.mkdir()
    config = repository / "config.json"
    config.write_text("{}\n", encoding="ascii")
    monkeypatch.setattr(gate_module, "verify_protocol", lambda *args, **kwargs: plan)
    monkeypatch.setattr(gate_module, "load_complete_run", lambda path, **kwargs: loaded)
    monkeypatch.setattr(gate_module, "_current_bindings", lambda *args: freeze_bindings)
    return loaded, repository, config


def test_exact_formal_matrix_writes_detailed_audit_then_runner_freeze(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loaded, repository, config = _install_fixture(tmp_path, monkeypatch)
    results = tmp_path / "results"
    audit_path = results / "development_audit.json"
    freeze_path = results / "sealed_evaluation_freeze.json"
    result = freeze_formal_development(
        loaded.root,
        config_path=config,
        repository_root=repository,
        audit_path=audit_path,
        freeze_path=freeze_path,
    )
    stored_audit = load_development_audit(audit_path)
    stored_freeze = json.loads(freeze_path.read_text(encoding="ascii"))
    assert stored_audit == result.audit
    assert stored_audit["schema"] == AUDIT_SCHEMA
    assert stored_audit["selection_performed"] is False
    assert stored_audit["development_result"]["search_run_count"] == 5760
    assert stored_audit["checks"]["timed_observations_per_query_method"] == 8
    assert stored_freeze == result.freeze
    assert stored_freeze["schema"] == FREEZE_SCHEMA
    assert stored_freeze["issued_by"] == AUDITOR_ID
    assert stored_freeze["development_result"]["manifest_path"] == (
        "progressive_landmarks_development_v2/manifest.json"
    )
    assert stored_freeze["development_audit"] == {
        "path": "development_audit.json",
        "sha256": hashlib.sha256(audit_path.read_bytes()).hexdigest(),
        "schema": AUDIT_SCHEMA,
    }
    assert set(stored_freeze["bindings"]) == {
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


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda loaded: loaded.queries[0]["runs"][4]["timing_ns"].__setitem__(
                "stage_ns", 1
            ),
            "timing contract",
        ),
        (
            lambda loaded: loaded.queries[1]["runs"][0].__setitem__("method", "staged"),
            "invocation order",
        ),
        (
            lambda loaded: loaded.queries[2]["deterministic_by_method"][
                "staged"
            ].__setitem__("expansion_digest", "9" * 64),
            "digests differ",
        ),
        (
            lambda loaded: loaded.queries[3]["deterministic_by_method"]["lazy_full"][
                "counters"
            ].__setitem__("distance_table_reads", 1),
            "read/call accounting",
        ),
        (
            lambda loaded: loaded.queries[4]["query"].__setitem__("goal", [0, 0]),
            "identity/order",
        ),
    ],
)
def test_auditor_rejects_semantic_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutate,
    message: str,
) -> None:
    loaded, repository, config = _install_fixture(tmp_path, monkeypatch)
    mutate(loaded)
    with pytest.raises(DevelopmentGateError, match=message):
        audit_formal_development(
            loaded.root,
            config_path=config,
            repository_root=repository,
            audit_path=tmp_path / "results" / "development_audit.json",
            freeze_path=tmp_path / "results" / "freeze.json",
        )


def test_current_binding_drift_and_unsafe_paths_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loaded, repository, config = _install_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        gate_module,
        "_current_bindings",
        lambda *args: {**_freeze_bindings(_bindings()), "core_sha256": "0" * 64},
    )
    with pytest.raises(DevelopmentGateError, match="differ from current"):
        audit_formal_development(
            loaded.root,
            config_path=config,
            repository_root=repository,
            audit_path=tmp_path / "results" / "development_audit.json",
            freeze_path=tmp_path / "results" / "freeze.json",
        )
    with pytest.raises(DevelopmentGateError, match="at or below"):
        audit_formal_development(
            loaded.root,
            config_path=config,
            repository_root=repository,
            audit_path=tmp_path / "elsewhere" / "development_audit.json",
            freeze_path=tmp_path / "elsewhere" / "freeze.json",
        )


def test_write_once_and_audit_self_hash_tamper_detection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loaded, repository, config = _install_fixture(tmp_path, monkeypatch)
    audit_path = tmp_path / "results" / "development_audit.json"
    freeze_path = tmp_path / "results" / "freeze.json"
    freeze_formal_development(
        loaded.root,
        config_path=config,
        repository_root=repository,
        audit_path=audit_path,
        freeze_path=freeze_path,
    )
    with pytest.raises(DevelopmentGateError, match="write-once"):
        freeze_formal_development(
            loaded.root,
            config_path=config,
            repository_root=repository,
            audit_path=audit_path,
            freeze_path=freeze_path,
        )
    decoded = json.loads(audit_path.read_text(encoding="ascii"))
    decoded["status"] = "failed"
    audit_path.write_text(json.dumps(decoded), encoding="ascii")
    with pytest.raises(DevelopmentGateError, match="invalid authorization status"):
        load_development_audit(audit_path)


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("false-check", "check did not pass"),
        ("nested-count", "wrong record count"),
        ("binding-mismatch", "canonical plan check differs"),
    ),
)
def test_strict_audit_loader_rejects_self_hashed_semantic_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    message: str,
) -> None:
    loaded, repository, config = _install_fixture(tmp_path, monkeypatch)
    audit_path = tmp_path / "results" / "development_audit.json"
    freeze_path = tmp_path / "results" / "freeze.json"
    freeze_formal_development(
        loaded.root,
        config_path=config,
        repository_root=repository,
        audit_path=audit_path,
        freeze_path=freeze_path,
    )
    audit = json.loads(audit_path.read_text(encoding="ascii"))
    if mutation == "false-check":
        audit["checks"]["formal_replay_passed"] = False
    elif mutation == "nested-count":
        audit["development_result"]["artifact_bindings"]["queries.jsonl"][
            "records"
        ] = 159
    else:
        audit["checks"]["canonical_plan_sha256"] = "0" * 64
    core = {key: value for key, value in audit.items() if key != "audit_sha256"}
    audit["audit_sha256"] = canonical_json_sha256(core)
    audit_path.write_text(
        json.dumps(audit, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="ascii",
        newline="\n",
    )
    with pytest.raises(DevelopmentGateError, match=message):
        load_development_audit(audit_path)


def test_cli_help_is_read_only_and_documents_required_outputs(tmp_path: Path) -> None:
    script = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "freeze_progressive_landmarks_development.py"
    )
    completed = subprocess.run(
        [sys.executable, str(script), "--help"],
        check=False,
        capture_output=True,
        cwd=Path(__file__).resolve().parents[2],
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
        text=True,
    )
    assert completed.returncode == 0
    assert "--audit-output" in completed.stdout
    assert "--freeze-output" in completed.stdout
    assert not list(tmp_path.iterdir())
