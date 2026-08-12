"""Focused fail-closed tests for the progressive-landmarks result runner."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

import progressive_landmarks.runner as runner_module
from progressive_landmarks.protocol import METHODS, canonical_json_sha256
from progressive_landmarks.runner import (
    CANDIDATE_SCHEMA,
    RunnerError,
    load_complete_run,
    run_split,
)


@dataclass(frozen=True, slots=True)
class _FakeStats:
    expanded: int
    generated: int
    relaxations: int
    reopened: int
    pops: int
    stale_pops: int
    requeues: int
    manhattan_calls: int
    prefix_calls: int
    suffix_calls: int
    full_calls: int
    pivot_evaluations: int
    distance_table_reads: int
    heuristic_cache_hits: int
    unique_discovered: int
    max_open_entries: int
    max_live_states: int
    stage_ns: int
    search_ns: int


def _map_payload() -> bytes:
    return b"type octile\nheight 1\nwidth 2\nmap\n..\n"


def _binding(relative: str, payload: bytes, *, split: str, family: str) -> dict:
    return {
        "experiment_split": split,
        "source_split": "train" if split == "development" else "validation",
        "family": family,
        "map": {
            "path": relative,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "width": 2,
            "height": 1,
            "traversable_states": 2,
            "connected_components": 1,
        },
        "scenarios": [],
        "query_count": 0,
    }


def _query(index: int, split: str, map_name: str) -> dict:
    family = ("maze", "random", "room", "warehouse")[
        int(Path(map_name).stem.rsplit("-", 1)[1]) % 4
    ]
    return {
        "query_id": f"{split}:q{index}",
        "experiment_split": split,
        "source_split": "train" if split == "development" else "validation",
        "family": family,
        "map": map_name,
        "scenario": "synthetic.scen",
        "scenario_index": 1,
        "scenario_row": index + 1,
        "source_line": index + 2,
        "source_bucket": 0,
        "start": [0, 0],
        "goal": [1, 0],
    }


def _plan(repository_root: Path) -> dict:
    payload = _map_payload()
    map_root = repository_root / "source" / "corpus" / "maps"
    map_root.mkdir(parents=True)
    development_names = [f"dev-{index}.map" for index in range(4)]
    evaluation_names = [f"eval-{index}.map" for index in range(8)]
    for name in development_names + evaluation_names:
        (map_root / name).write_bytes(payload)
    input_bindings = [
        _binding(
            f"corpus/maps/{name}",
            payload,
            split="development",
            family=("maze", "random", "room", "warehouse")[index],
        )
        for index, name in enumerate(development_names)
    ] + [
        _binding(
            f"corpus/maps/{name}",
            payload,
            split="sealed_evaluation",
            family=("maze", "random", "room", "warehouse")[index % 4],
        )
        for index, name in enumerate(evaluation_names)
    ]
    timed = []
    for repetition in range(8):
        offset = repetition % len(METHODS)
        timed.append(
            {
                "repetition": repetition,
                "timed": True,
                "methods": list(METHODS[offset:] + METHODS[:offset]),
            }
        )
    core = {
        "schema": "progressive-landmarks-plan-v2",
        "protocol_id": "progressive_landmarks_v2",
        "master_seed": 23725513,
        "config_binding": {
            "path": "config.json",
            "sha256": "c" * 64,
            "hash_basis": "file-bytes",
        },
        "source_binding": {"relative_root": "source"},
        "methods": list(METHODS),
        "landmarks": {"full_pivots": 32, "staged_prefix_pivots": 4},
        "search_model": {},
        "timing_orders": {
            "warmup": [
                {
                    "repetition": 0,
                    "timed": False,
                    "methods": list(METHODS),
                }
            ],
            "timed": timed,
        },
        "input_bindings": input_bindings,
        "queries": [
            *(
                _query(
                    map_index * 40 + query_index,
                    "development",
                    development_names[map_index],
                )
                for map_index in range(4)
                for query_index in range(40)
            ),
            *(
                _query(
                    index,
                    "sealed_evaluation",
                    evaluation_names[index // 100],
                )
                for index in range(800)
            ),
        ],
        "counts": {},
    }
    return {**core, "plan_sha256": canonical_json_sha256(core)}


def _install_synthetic_protocol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, list[dict]]:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    scripts = repository_root / "scripts"
    scripts.mkdir()
    (scripts / "run_progressive_landmarks.py").write_text(
        "# synthetic CLI binding\n", encoding="ascii"
    )
    (scripts / "freeze_progressive_landmarks_development.py").write_text(
        "# synthetic freeze CLI binding\n", encoding="ascii"
    )
    (scripts / "analyze_progressive_landmarks.py").write_text(
        "# synthetic analysis CLI binding\n", encoding="ascii"
    )
    config = repository_root / "config.json"
    config.write_text("{}\n", encoding="ascii")
    plan = _plan(repository_root)
    monkeypatch.setattr(
        runner_module,
        "verify_protocol",
        lambda config_path, *, repository_root: plan,
    )
    calls: list[dict] = []

    def fake_astar(
        grid,
        start,
        goal,
        *,
        mode,
        landmarks=None,
        prefix_landmarks=4,
        full_landmarks=32,
        measure_stage_time=False,
    ):
        assert grid.name in {f"dev-{index}.map" for index in range(4)} | {
            f"eval-{index}.map" for index in range(8)
        }
        assert start == (0, 0) and goal == (1, 0)
        assert (landmarks is None) is (mode == "manhattan")
        assert prefix_landmarks == 4 and full_landmarks == 32
        assert measure_stage_time is False
        calls.append({"mode": mode, "measure_stage_time": measure_stage_time})
        if mode == "eager_full":
            prefix_calls, suffix_calls, full_calls = 0, 0, 2
            pivots, reads = 64, 96
        elif mode == "lazy_full":
            prefix_calls, suffix_calls, full_calls = 0, 0, 1
            pivots, reads = 32, 64
        elif mode == "staged":
            prefix_calls, suffix_calls, full_calls = 1, 1, 0
            pivots, reads = 32, 64
        else:
            prefix_calls = suffix_calls = full_calls = pivots = reads = 0
        stats = _FakeStats(
            expanded=1,
            generated=1,
            relaxations=1,
            reopened=0,
            pops=2,
            stale_pops=0,
            requeues=0,
            manhattan_calls=2,
            prefix_calls=prefix_calls,
            suffix_calls=suffix_calls,
            full_calls=full_calls,
            pivot_evaluations=pivots,
            distance_table_reads=reads,
            heuristic_cache_hits=0,
            unique_discovered=2,
            max_open_entries=2,
            max_live_states=1,
            stage_ns=0,
            search_ns=1000 + len(calls),
        )
        full_digest = hashlib.sha256(b"same full expansion").hexdigest()
        return SimpleNamespace(
            mode=mode,
            found=True,
            cost=1,
            path=((0, 0), (1, 0)),
            expansion_digest=(
                hashlib.sha256(b"manhattan expansion").hexdigest()
                if mode == "manhattan"
                else full_digest
            ),
            stats=stats,
        )

    monkeypatch.setattr(runner_module, "astar_search", fake_astar)
    return repository_root, config, calls


def test_smoke_run_is_nonformal_and_preserves_exact_rotations_and_counters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository_root, config, calls = _install_synthetic_protocol(tmp_path, monkeypatch)
    output = tmp_path / "smoke-result"
    run_split(
        config,
        output,
        repository_root=repository_root,
        experiment_split="development",
        development_smoke=True,
        max_queries=1,
    )

    loaded = load_complete_run(output)
    assert loaded.run["formal"] is False
    assert loaded.run["nonformal_smoke"] is True
    assert loaded.run["counts"]["queries"] == 1
    assert loaded.run["counts"]["search_runs"] == 36
    assert "development_freeze_candidate.json" not in loaded.manifest["artifacts"]
    row = loaded.queries[0]
    assert len(row["runs"]) == 36
    expected_orders = [
        list(METHODS),
        list(METHODS),
        list(METHODS[1:] + METHODS[:1]),
        list(METHODS[2:] + METHODS[:2]),
        list(METHODS[3:] + METHODS[:3]),
        list(METHODS),
        list(METHODS[1:] + METHODS[:1]),
        list(METHODS[2:] + METHODS[:2]),
        list(METHODS[3:] + METHODS[:3]),
    ]
    observed_orders = []
    offset = 0
    for expected in expected_orders:
        observed_orders.append(
            [row["runs"][offset + index]["method"] for index in range(4)]
        )
        offset += 4
    assert observed_orders == expected_orders
    assert [call["mode"] for call in calls] == [
        method for order in expected_orders for method in order
    ]
    assert all(call["measure_stage_time"] is False for call in calls)
    eager = row["deterministic_by_method"]["eager_full"]
    assert eager["counters"]["heuristic_cache_hits"] == 0
    assert eager["counters"]["unique_discovered"] == 2
    assert eager["counters"]["pivot_evaluations"] == 64
    assert eager["counters"]["distance_table_reads"] == 96
    assert eager["counters"]["pops"] == 2
    assert all(invocation["timing_ns"]["stage_ns"] == 0 for invocation in row["runs"])
    assert all(invocation["timing_ns"]["search_ns"] > 0 for invocation in row["runs"])
    map_record = loaded.maps["maps"][0]
    assert map_record["actual_landmarks"] == 2
    assert map_record["packed_distance_bytes"] == 16
    assert len(map_record["table_sha256"]) == 64


def test_failure_leaves_no_partial_output_or_staging_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository_root, config, _ = _install_synthetic_protocol(tmp_path, monkeypatch)
    successful_search = runner_module.astar_search
    invocations = 0

    def fail_during_query(*args, **kwargs):
        nonlocal invocations
        invocations += 1
        if invocations == 5:
            raise RuntimeError("injected search failure")
        return successful_search(*args, **kwargs)

    monkeypatch.setattr(runner_module, "astar_search", fail_during_query)
    output = tmp_path / "atomic-failure"
    with pytest.raises(RuntimeError, match="injected search failure"):
        run_split(
            config,
            output,
            repository_root=repository_root,
            experiment_split="development",
            development_smoke=True,
            max_queries=1,
        )
    assert not output.exists()
    assert not list(tmp_path.glob(".atomic-failure.staging-*"))


def test_complete_loader_rejects_tampering_and_returns_immutable_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository_root, config, _ = _install_synthetic_protocol(tmp_path, monkeypatch)
    output = tmp_path / "tamper-result"
    run_split(
        config,
        output,
        repository_root=repository_root,
        experiment_split="development",
        development_smoke=True,
        max_queries=1,
    )
    loaded = load_complete_run(output)
    with pytest.raises(TypeError):
        loaded.run["status"] = "tampered"

    queries = output / "queries.jsonl"
    payload = bytearray(queries.read_bytes())
    payload[10] ^= 1
    queries.write_bytes(payload)
    with pytest.raises(RunnerError, match="SHA-256 mismatch"):
        load_complete_run(output)


def _rehash_artifact(output: Path, name: str, payload: bytes, records: int) -> None:
    (output / name).write_bytes(payload)
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="ascii"))
    manifest["artifacts"][name] = {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "records": records,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="ascii",
        newline="\n",
    )


def _refresh_formal_candidate(output: Path) -> None:
    manifest = json.loads((output / "manifest.json").read_text(encoding="ascii"))
    candidate_path = output / "development_freeze_candidate.json"
    candidate = json.loads(candidate_path.read_text(encoding="ascii"))
    candidate["development_result"]["artifact_bindings"] = {
        name: manifest["artifacts"][name]
        for name in ("queries.jsonl", "maps.json", "run.json")
    }
    payload = (
        json.dumps(candidate, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    ).encode("ascii")
    _rehash_artifact(output, candidate_path.name, payload, 1)


def test_loader_rejects_semantic_tampering_even_after_rehash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository_root, config, _ = _install_synthetic_protocol(tmp_path, monkeypatch)
    pristine = tmp_path / "semantic-pristine"
    run_split(
        config,
        pristine,
        repository_root=repository_root,
        experiment_split="development",
        development_smoke=True,
        max_queries=1,
    )

    cases = (
        ("unknown-run-key", "run.json", "unknown"),
        ("unknown-map-key", "maps.json", "unknown"),
        ("unknown-query-key", "queries.jsonl", "unknown"),
        ("bad-rotation", "queries.jsonl", "rotation"),
        ("bad-summary", "queries.jsonl", "summary"),
        ("bad-full-digest", "queries.jsonl", "digest"),
        ("duplicate-landmark", "maps.json", "landmark"),
    )
    for directory_name, artifact, mutation in cases:
        output = tmp_path / directory_name
        shutil.copytree(pristine, output)
        if artifact == "run.json":
            value = json.loads((output / artifact).read_text(encoding="ascii"))
            value["surprise"] = True
            payload = (
                json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
            ).encode("ascii")
        elif artifact == "maps.json":
            value = json.loads((output / artifact).read_text(encoding="ascii"))
            if mutation == "unknown":
                value["maps"][0]["surprise"] = True
            else:
                value["maps"][0]["landmarks"][1] = value["maps"][0]["landmarks"][0]
            payload = (
                json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
            ).encode("ascii")
        else:
            row = json.loads((output / artifact).read_text(encoding="ascii"))
            if mutation == "unknown":
                row["runs"][0]["surprise"] = True
            elif mutation == "rotation":
                row["runs"][0]["method"] = "staged"
            elif mutation == "summary":
                row["runs"][0]["result"]["counters"]["pops"] += 1
            else:
                row["deterministic_by_method"]["staged"]["expansion_digest"] = "f" * 64
                for invocation in row["runs"]:
                    if invocation["method"] == "staged":
                        invocation["result"]["expansion_digest"] = "f" * 64
            payload = (
                json.dumps(
                    row,
                    ensure_ascii=True,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("ascii")
        records = 1 if artifact != "maps.json" else 1
        _rehash_artifact(output, artifact, payload, records)
        with pytest.raises(RunnerError):
            load_complete_run(output)


def test_loader_rejects_duplicate_query_ids_after_rehash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository_root, config, _ = _install_synthetic_protocol(tmp_path, monkeypatch)
    output = tmp_path / "duplicate-query"
    run_split(
        config,
        output,
        repository_root=repository_root,
        experiment_split="development",
        development_smoke=True,
        max_queries=2,
    )
    rows = [
        json.loads(line)
        for line in (output / "queries.jsonl").read_text(encoding="ascii").splitlines()
    ]
    rows[1]["query"]["query_id"] = rows[0]["query"]["query_id"]
    payload = b"".join(
        (
            json.dumps(
                row,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("ascii")
        for row in rows
    )
    _rehash_artifact(output, "queries.jsonl", payload, 2)
    with pytest.raises(RunnerError, match="duplicate stored query ID"):
        load_complete_run(output)


def test_loader_rejects_rehashed_code_binding_aggregate_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository_root, config, _ = _install_synthetic_protocol(tmp_path, monkeypatch)
    output = tmp_path / "code-aggregate-mismatch"
    run_split(
        config,
        output,
        repository_root=repository_root,
        experiment_split="development",
        development_smoke=True,
        max_queries=1,
    )
    run = json.loads((output / "run.json").read_text(encoding="ascii"))
    files = run["bindings"]["code"]["files"]
    assert set(files) == {
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
    files["package_init"]["sha256"] = "f" * 64
    payload = (
        json.dumps(run, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    ).encode("ascii")
    _rehash_artifact(output, "run.json", payload, 1)
    with pytest.raises(RunnerError, match="aggregate SHA-256"):
        load_complete_run(output)


def test_synthetic_formal_development_requires_160_and_emits_only_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository_root, config, calls = _install_synthetic_protocol(tmp_path, monkeypatch)
    output = tmp_path / "synthetic-formal-development"
    run_split(
        config,
        output,
        repository_root=repository_root,
        experiment_split="development",
    )
    with pytest.raises(RunnerError, match="requires config_path"):
        load_complete_run(output)
    loaded = load_complete_run(
        output, config_path=config, repository_root=repository_root
    )
    assert loaded.run["formal"] is True
    assert loaded.run["nonformal_smoke"] is False
    assert loaded.run["counts"]["maps"] == 4
    assert loaded.run["counts"]["queries"] == 160
    assert loaded.run["counts"]["search_runs"] == 5760
    assert len(calls) == 7040
    candidate = json.loads(
        (output / "development_freeze_candidate.json").read_text(encoding="ascii")
    )
    assert candidate["schema"] == CANDIDATE_SCHEMA
    assert candidate["authorization"] == "not-authorized"
    assert candidate["development_result"]["query_count"] == 160
    assert candidate["development_result"]["search_run_count"] == 5760
    assert set(candidate["bindings"]) == {
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
    assert candidate["next_step"].endswith("freeze-v2")

    for case in ("cost", "oracle-path", "digest", "counter", "table"):
        impossible = tmp_path / f"coherently-rehashed-{case}-formal"
        shutil.copytree(output, impossible)
        if case == "table":
            maps = json.loads((impossible / "maps.json").read_text(encoding="ascii"))
            maps["maps"][0]["landmarks"].reverse()
            maps["maps"][0]["table_sha256"] = "f" * 64
            payload = (
                json.dumps(maps, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
            ).encode("ascii")
            _rehash_artifact(impossible, "maps.json", payload, 4)
            expected_message = "landmark table differs"
        else:
            rows = [
                json.loads(line)
                for line in (impossible / "queries.jsonl")
                .read_text(encoding="ascii")
                .splitlines()
            ]
            row = rows[0]
            if case == "cost":
                row["oracle"]["cost"] = 2
                for summary in row["deterministic_by_method"].values():
                    summary["cost"] = 2
                for invocation in row["runs"]:
                    invocation["result"]["cost"] = 2
                expected_message = "BFS evidence differs"
            elif case == "oracle-path":
                row["oracle"]["path_sha256"] = "f" * 64
                expected_message = "BFS evidence differs"
            else:
                method = "manhattan" if case == "digest" else "eager_full"
                summary = row["deterministic_by_method"][method]
                if case == "digest":
                    summary["expansion_digest"] = "f" * 64
                else:
                    summary["counters"]["expanded"] += 1
                    summary["counters"]["pops"] += 1
                for invocation in row["runs"]:
                    if invocation["method"] == method:
                        invocation["result"] = summary
                expected_message = "deterministic result differs"
            payload = b"".join(
                (
                    json.dumps(
                        item,
                        ensure_ascii=True,
                        allow_nan=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode("ascii")
                for item in rows
            )
            _rehash_artifact(impossible, "queries.jsonl", payload, len(rows))
        _refresh_formal_candidate(impossible)
        with pytest.raises(RunnerError, match=expected_message):
            load_complete_run(
                impossible,
                config_path=config,
                repository_root=repository_root,
            )


def test_runner_refuses_to_overwrite_existing_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository_root, config, calls = _install_synthetic_protocol(tmp_path, monkeypatch)
    output = tmp_path / "occupied"
    output.mkdir()
    marker = output / "user-data.txt"
    marker.write_text("preserve me", encoding="ascii")
    with pytest.raises(RunnerError, match="refusing to overwrite"):
        run_split(
            config,
            output,
            repository_root=repository_root,
            experiment_split="development",
            development_smoke=True,
            max_queries=1,
        )
    assert marker.read_text(encoding="ascii") == "preserve me"
    assert not calls


def test_evaluation_rejects_missing_or_development_candidate_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository_root, config, _ = _install_synthetic_protocol(tmp_path, monkeypatch)
    with pytest.raises(RunnerError, match="requires --freeze-manifest"):
        run_split(
            config,
            tmp_path / "forbidden-evaluation",
            repository_root=repository_root,
            experiment_split="sealed_evaluation",
        )

    candidate = tmp_path / "development_freeze_candidate.json"
    candidate.write_text(
        json.dumps(
            {
                "schema": CANDIDATE_SCHEMA,
                "authorization": "not-authorized",
                "eligible_for_external_freeze": True,
                "bindings": {},
                "development_result": {},
                "next_step": "external-verifier",
            }
        ),
        encoding="ascii",
    )
    with pytest.raises(RunnerError, match="candidate is not authorization"):
        run_split(
            config,
            tmp_path / "still-forbidden-evaluation",
            repository_root=repository_root,
            experiment_split="sealed_evaluation",
            freeze_manifest=candidate,
        )


def test_freeze_validation_replays_development_and_rejects_path_escapes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import progressive_landmarks.development_gate as gate_module
    from progressive_landmarks.development_gate import freeze_formal_development

    repository_root, config, _ = _install_synthetic_protocol(tmp_path, monkeypatch)
    monkeypatch.setattr(
        gate_module,
        "verify_protocol",
        lambda config_path, *, repository_root: runner_module.verify_protocol(
            config_path, repository_root=repository_root
        ),
    )
    results = tmp_path / "results"
    development = results / "development"
    audit_path = results / "development_audit.json"
    freeze_path = results / "sealed_evaluation_freeze.json"
    run_split(
        config,
        development,
        repository_root=repository_root,
        experiment_split="development",
    )
    freeze_formal_development(
        development,
        config_path=config,
        repository_root=repository_root,
        audit_path=audit_path,
        freeze_path=freeze_path,
    )
    plan = runner_module.verify_protocol(config, repository_root=repository_root)
    evaluation_bindings = runner_module._current_bindings(
        plan, repository_root, "sealed_evaluation"
    )
    authorization = runner_module._validate_evaluation_freeze(
        freeze_path,
        evaluation_bindings,
        config_path=config,
        repository_root=repository_root,
    )
    assert (
        authorization["development_manifest_sha256"]
        == hashlib.sha256((development / "manifest.json").read_bytes()).hexdigest()
    )

    evaluation = results / "evaluation"
    run_split(
        config,
        evaluation,
        repository_root=repository_root,
        experiment_split="sealed_evaluation",
        freeze_manifest=freeze_path,
    )
    with pytest.raises(RunnerError, match="requires freeze_manifest"):
        load_complete_run(
            evaluation,
            config_path=config,
            repository_root=repository_root,
        )
    loaded_evaluation = load_complete_run(
        evaluation,
        config_path=config,
        repository_root=repository_root,
        freeze_manifest=freeze_path,
    )
    assert loaded_evaluation.run["evaluation_authorization"] == authorization

    wrong_freeze = results / "v1-freeze.json"
    wrong_freeze.write_text(
        json.dumps(
            {
                "schema": "progressive-landmarks-sealed-evaluation-freeze-v1",
                "authorization": "sealed_evaluation",
            }
        ),
        encoding="ascii",
    )
    with pytest.raises(RunnerError, match="external sealed-evaluation freeze"):
        load_complete_run(
            evaluation,
            config_path=config,
            repository_root=repository_root,
            freeze_manifest=wrong_freeze,
        )

    forged_evaluation = results / "evaluation-forged-authorization"
    shutil.copytree(evaluation, forged_evaluation)
    forged_run = json.loads(
        (forged_evaluation / "run.json").read_text(encoding="ascii")
    )
    forged_run["evaluation_authorization"] = {
        "path": "development_freeze_candidate.json",
        "sha256": "f" * 64,
        "development_manifest_sha256": "e" * 64,
    }
    forged_payload = (
        json.dumps(forged_run, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    ).encode("ascii")
    _rehash_artifact(forged_evaluation, "run.json", forged_payload, 1)
    with pytest.raises(RunnerError, match="stored authorization differs"):
        load_complete_run(
            forged_evaluation,
            config_path=config,
            repository_root=repository_root,
            freeze_manifest=freeze_path,
        )

    valid_audit_bytes = audit_path.read_bytes()
    valid_freeze_bytes = freeze_path.read_bytes()
    for nested, key in (
        ("development_audit", "path"),
        ("development_result", "manifest_path"),
    ):
        freeze = json.loads(valid_freeze_bytes)
        freeze[nested][key] = (
            "C:/escape/development_audit.json"
            if key == "path"
            else "C:/escape/manifest.json"
        )
        freeze_path.write_text(
            json.dumps(freeze, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
            encoding="ascii",
            newline="\n",
        )
        with pytest.raises(RunnerError, match="safe canonical relative path"):
            runner_module._validate_evaluation_freeze(
                freeze_path,
                evaluation_bindings,
                config_path=config,
                repository_root=repository_root,
            )

    audit_path.write_bytes(valid_audit_bytes)
    audit = json.loads(valid_audit_bytes)
    audit["checks"]["formal_replay_passed"] = False
    audit_core = {key: value for key, value in audit.items() if key != "audit_sha256"}
    audit["audit_sha256"] = canonical_json_sha256(audit_core)
    audit_payload = (
        json.dumps(audit, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    ).encode("ascii")
    audit_path.write_bytes(audit_payload)
    freeze = json.loads(valid_freeze_bytes)
    freeze["development_audit"]["sha256"] = hashlib.sha256(audit_payload).hexdigest()
    freeze_path.write_text(
        json.dumps(freeze, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="ascii",
        newline="\n",
    )
    with pytest.raises(RunnerError, match="audit check did not pass"):
        runner_module._validate_evaluation_freeze(
            freeze_path,
            evaluation_bindings,
            config_path=config,
            repository_root=repository_root,
        )

    audit_path.write_bytes(valid_audit_bytes)
    freeze_path.write_bytes(valid_freeze_bytes)
    impossible = results / "development-impossible"
    shutil.copytree(development, impossible)
    rows = [
        json.loads(line)
        for line in (impossible / "queries.jsonl")
        .read_text(encoding="ascii")
        .splitlines()
    ]
    for row in rows:
        row["oracle"]["cost"] = 2
        for summary in row["deterministic_by_method"].values():
            summary["cost"] = 2
        for invocation in row["runs"]:
            invocation["result"]["cost"] = 2
    payload = b"".join(
        (
            json.dumps(
                row,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("ascii")
        for row in rows
    )
    _rehash_artifact(impossible, "queries.jsonl", payload, len(rows))
    _refresh_formal_candidate(impossible)
    impossible_manifest = impossible / "manifest.json"
    impossible_manifest_value = json.loads(
        impossible_manifest.read_text(encoding="ascii")
    )
    audit = json.loads(valid_audit_bytes)
    audit["development_result"].update(
        manifest_path="development-impossible/manifest.json",
        manifest_sha256=hashlib.sha256(impossible_manifest.read_bytes()).hexdigest(),
        artifact_bindings=impossible_manifest_value["artifacts"],
    )
    audit_core = {key: value for key, value in audit.items() if key != "audit_sha256"}
    audit["audit_sha256"] = canonical_json_sha256(audit_core)
    audit_payload = (
        json.dumps(audit, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    ).encode("ascii")
    audit_path.write_bytes(audit_payload)
    freeze = json.loads(valid_freeze_bytes)
    freeze["development_result"] = audit["development_result"]
    freeze["development_audit"]["sha256"] = hashlib.sha256(audit_payload).hexdigest()
    freeze_path.write_text(
        json.dumps(freeze, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="ascii",
        newline="\n",
    )
    with pytest.raises(RunnerError, match="failed independent replay audit"):
        runner_module._validate_evaluation_freeze(
            freeze_path,
            evaluation_bindings,
            config_path=config,
            repository_root=repository_root,
        )


@pytest.mark.parametrize(
    "unsafe",
    (
        "C:/escape/manifest.json",
        "C:\\escape\\manifest.json",
        "/escape/manifest.json",
        "../escape/manifest.json",
        "nested/../manifest.json",
        "nested:evil/manifest.json",
    ),
)
def test_safe_relative_path_rejects_cross_platform_escape_forms(unsafe: str) -> None:
    with pytest.raises(RunnerError, match="safe canonical relative path"):
        runner_module._safe_relative_parts(
            unsafe,
            label="test path",
            expected_name="manifest.json",
        )


def test_safe_child_rejects_intermediate_symlink_escape(tmp_path: Path) -> None:
    base = tmp_path / "base"
    outside = tmp_path / "outside"
    base.mkdir()
    outside.mkdir()
    (outside / "manifest.json").write_text("{}\n", encoding="ascii")
    try:
        (base / "linked").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    with pytest.raises(RunnerError, match="symbolic link"):
        runner_module._resolve_safe_child(
            base,
            "linked/manifest.json",
            label="linked manifest",
            expected_name="manifest.json",
        )


def test_query_cap_is_categorically_confined_to_development_smoke(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository_root, config, _ = _install_synthetic_protocol(tmp_path, monkeypatch)
    with pytest.raises(RunnerError, match="only with development_smoke"):
        run_split(
            config,
            tmp_path / "not-smoke",
            repository_root=repository_root,
            experiment_split="development",
            max_queries=1,
        )
    with pytest.raises(RunnerError, match="categorically forbidden for evaluation"):
        run_split(
            config,
            tmp_path / "eval-cap",
            repository_root=repository_root,
            experiment_split="sealed_evaluation",
            max_queries=1,
        )
