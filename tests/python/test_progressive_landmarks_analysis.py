"""Prospective statistics, immutable publication, and tamper tests."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from types import MappingProxyType

import pytest

import progressive_landmarks.analysis as analysis_module
from progressive_landmarks.analysis import (
    ANALYZER_ID,
    AnalysisError,
    AnalysisInputs,
    analyze_sealed_evaluation,
    distribution_summary,
    even_sample_median,
    hierarchical_bootstrap_median,
    load_complete_analysis,
    spearman_rho,
)
from progressive_landmarks.protocol import METHODS, canonical_json_sha256
from progressive_landmarks.runner import LoadedRun


SHA = "a" * 64


def _counters(method: str) -> dict[str, int]:
    values = {name: 0 for name in analysis_module.COUNTER_NAMES}
    values.update(
        expanded=10,
        generated=25,
        relaxations=20,
        reopened=0,
        manhattan_calls=15 if method != "manhattan" else 20,
        heuristic_cache_hits=0,
        unique_discovered=15 if method != "manhattan" else 20,
        max_open_entries=8,
        max_live_states=12,
    )
    if method == "manhattan":
        values.update(pops=12)
    elif method == "eager_full":
        values.update(
            pops=12,
            full_calls=15,
            pivot_evaluations=480,
            distance_table_reads=512,
        )
    elif method == "lazy_full":
        values.update(
            pops=25,
            requeues=9,
            full_calls=10,
            pivot_evaluations=320,
            distance_table_reads=352,
        )
    else:
        values.update(
            pops=30,
            requeues=13,
            prefix_calls=10,
            suffix_calls=5,
            pivot_evaluations=180,
            distance_table_reads=212,
        )
    return values


def _summary(method: str) -> dict:
    return {
        "mode": method,
        "found": True,
        "cost": 10,
        "path_sha256": SHA,
        "expansion_digest": SHA if method != "manhattan" else "b" * 64,
        "counters": _counters(method),
    }


def _one_query_row(index: int = 0, *, map_name: str = "maze-1.map") -> dict:
    family = map_name.split("-")[0]
    summaries = {method: _summary(method) for method in METHODS}
    times = {
        "manhattan": (80, 10, 70, 20, 60, 30, 50, 40),
        "eager_full": (90, 20, 80, 30, 70, 40, 60, 50),
        "lazy_full": (9, 9, 9, 9, 9, 9, 9, 9),
        "staged": (4, 1, 8, 2, 7, 3, 6, 5),
    }
    runs = []
    for repetition in range(8):
        for position, method in enumerate(METHODS):
            runs.append(
                {
                    "phase": "timed",
                    "repetition": repetition,
                    "timed": True,
                    "order_position": position,
                    "method": method,
                    "timing_ns": {
                        "stage_ns": 0,
                        "search_ns": times[method][repetition],
                    },
                    "result": summaries[method],
                }
            )
    return {
        "sequence_index": index,
        "experiment_split": "sealed_evaluation",
        "query": {
            "query_id": f"q{index}",
            "source_split": (
                "validation" if map_name.endswith("-1.map") else "holdout"
            ),
            "family": family,
            "map": map_name,
            "scenario": f"{map_name[:-4]}-random-1.scen",
            "scenario_index": 1,
            "scenario_row": index % 100 + 1,
            "source_line": index % 100 + 2,
            "source_bucket": index,
            "start": [index % 100, 0],
            "goal": [index % 100, 1],
        },
        "oracle": {
            "algorithm": "independent-unit-cost-4-neighbor-bfs",
            "cost": 10,
            "path_sha256": SHA,
        },
        "deterministic_by_method": summaries,
        "runs": tuple(runs),
        "validation": {
            "all_costs_match_bfs": True,
            "all_repetitions_deterministic": True,
            "full_landmark_expansion_digests_match": True,
        },
    }


def test_exact_small_sample_statistics_and_mechanism_decomposition() -> None:
    assert even_sample_median((8, 1, 7, 2, 6, 3, 5, 4)) == 4.5
    assert distribution_summary((1, 2, 3, 4)) == {
        "n": 4,
        "mean": 2.5,
        "median": 2.5,
        "q1": 1.75,
        "q3": 3.25,
        "min": 1.0,
        "max": 4.0,
    }
    assert spearman_rho((1, 2, 2, 4), (4, 2, 2, 1)) == pytest.approx(-1.0)

    loaded = LoadedRun(Path.cwd(), {}, {}, {"maps": ()}, (_one_query_row(),))
    metric = analysis_module._query_metrics(loaded)[0]
    assert metric["staged_median_search_ns"] == 4.5
    assert metric["lazy_full_median_search_ns"] == 9.0
    assert metric["staged_lazy_time_ratio"] == 0.5
    assert metric["staged_lazy_log_time_ratio"] == pytest.approx(math.log(0.5))
    assert metric["pivot_saved"] == 140
    assert metric["pivot_saving"] == pytest.approx(140 / 320)
    assert metric["read_saved"] == 140
    assert metric["read_saving"] == pytest.approx(140 / 352)
    assert metric["suffix_avoided_calls"] == 5
    assert metric["suffix_avoidance_rate"] == pytest.approx(5 / 9)
    assert metric["extra_open_work"] == 9


def test_formal_shape_normalizes_frozen_schedule_containers() -> None:
    timing_orders = {
        "warmup": (
            {
                "repetition": 0,
                "timed": False,
                "methods": list(METHODS),
            },
        ),
        "timed": tuple(
            {
                "repetition": repetition,
                "timed": True,
                "methods": list(
                    METHODS[repetition % len(METHODS) :]
                    + METHODS[: repetition % len(METHODS)]
                ),
            }
            for repetition in range(8)
        ),
    }
    expected_schedule = analysis_module._expected_schedule(
        {"timing_orders": timing_orders}
    )
    frozen_schedule = tuple(
        MappingProxyType(
            {
                **row,
                "methods": tuple(row["methods"]),
            }
        )
        for row in expected_schedule
    )
    loaded = LoadedRun(
        Path.cwd(),
        {
            "experiment_split": "development",
            "formal": True,
            "complete": True,
            "validation": "passed",
            "record_counts": {
                "maps": 4,
                "queries": 160,
                "search_runs": 160 * analysis_module.SEARCHES_PER_QUERY,
            },
        },
        {
            "experiment_split": "development",
            "formal": True,
            "nonformal_smoke": False,
            "status": "complete",
            "validation": "passed",
            "methods": tuple(METHODS),
            "schedule": frozen_schedule,
            "counts": {
                "maps": 4,
                "queries": 160,
                "searches_per_query": analysis_module.SEARCHES_PER_QUERY,
                "search_runs": 160 * analysis_module.SEARCHES_PER_QUERY,
            },
            "landmarks": MappingProxyType(
                {"full_pivots": 32, "staged_prefix_pivots": 4}
            ),
        },
        {"maps": ()},
        (),
    )
    plan = {
        "timing_orders": timing_orders,
        "landmarks": {"full_pivots": 32, "staged_prefix_pivots": 4},
        "input_bindings": (),
    }
    with pytest.raises(AnalysisError, match="map artifact has the wrong exact size"):
        analysis_module._validate_formal_run_shape(loaded, plan, split="development")


def test_hierarchical_bootstrap_is_map_then_query_and_fixed_seed() -> None:
    log_two = math.log(2.0)
    first = hierarchical_bootstrap_median(
        {"map-a": (log_two, log_two), "map-b": (log_two, log_two)},
        replicates=37,
        seed=23725513,
    )
    second = hierarchical_bootstrap_median(
        {"map-b": (log_two, log_two), "map-a": (log_two, log_two)},
        replicates=37,
        seed=23725513,
    )
    assert first == second
    assert first["point_ratio"] == pytest.approx(2.0)
    assert first["ci95_ratio"] == pytest.approx([2.0, 2.0])
    assert first["top_level_clusters"] == 2
    assert first["query_observations"] == 4
    assert first["bootstrap_probability_ratio_below_one"] == 0.0


def test_planned_png_pdf_figures_render_deterministically(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    map_names = tuple(
        f"{family}-{copy}.map"
        for family in ("maze", "random", "room", "warehouse")
        for copy in (1, 2)
    )
    stored = tuple(
        _one_query_row(index, map_name=name) for index, name in enumerate(map_names)
    )
    query_rows = analysis_module._query_metrics(
        LoadedRun(Path.cwd(), {}, {}, {"maps": ()}, stored)
    )
    raw_maps = [
        {
            "map": name,
            "family": name.split("-")[0],
            "source_split": "validation" if name.endswith("-1.map") else "holdout",
            "landmark_build_ns": 1_000_000 * (index + 1),
            "packed_distance_bytes": 256,
        }
        for index, name in enumerate(map_names)
    ]
    map_rows, _ = analysis_module._build_map_rows(query_rows, raw_maps)
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    analysis_module._render_figures(query_rows, map_rows, first)
    analysis_module._render_figures(query_rows, map_rows, second)
    expected = {
        f"{stem}.{extension}"
        for stem in analysis_module.FIGURE_STEMS
        for extension in ("png", "pdf")
    }
    assert {path.name for path in first.iterdir()} == expected
    for name in expected:
        first_payload = (first / name).read_bytes()
        second_payload = (second / name).read_bytes()
        assert first_payload == second_payload
        assert (
            first_payload.startswith(b"\x89PNG")
            if name.endswith(".png")
            else first_payload.startswith(b"%PDF")
        )


def _fake_inputs(tmp_path: Path) -> AnalysisInputs:
    families = ("maze", "random", "room", "warehouse")
    map_names = tuple(f"{family}-{copy}.map" for family in families for copy in (1, 2))
    rows = tuple(
        _one_query_row(index, map_name=map_names[index // 100]) for index in range(800)
    )
    map_rows = tuple(
        {
            "map": name,
            "family": name.split("-")[0],
            "source_split": "validation" if name.endswith("-1.map") else "holdout",
            "map_sha256": hashlib.sha256(name.encode()).hexdigest(),
            "width": 2,
            "height": 1,
            "traversable_states": 2,
            "landmark_build_ns": 1_000_000 * (index + 1),
            "packed_distance_bytes": 256,
            "requested_landmarks": 32,
            "actual_landmarks": 2,
            "landmarks": ((0, 0), (1, 0)),
            "table_sha256": hashlib.sha256(f"table:{name}".encode()).hexdigest(),
        }
        for index, name in enumerate(map_names)
    )
    evaluation_root = tmp_path / "evaluation"
    development_root = tmp_path / "development"
    evaluation_root.mkdir()
    development_root.mkdir()
    for directory in (evaluation_root, development_root):
        (directory / "manifest.json").write_text("{}\n", encoding="ascii")
    timestamps = {
        "started_at_utc": "2026-01-01T00:00:00.000000Z",
        "completed_at_utc": "2026-01-01T00:01:00.000000Z",
    }
    evaluation = LoadedRun(
        evaluation_root,
        {"artifacts": {}},
        {**timestamps, "bindings": {}},
        {"maps": map_rows},
        rows,
    )
    development = LoadedRun(
        development_root,
        {"artifacts": {}},
        {**timestamps, "bindings": {}},
        {"maps": ()},
        (),
    )
    freeze = tmp_path / "freeze.json"
    audit = tmp_path / "development_audit.json"
    freeze.write_text("{}\n", encoding="ascii")
    audit.write_text("{}\n", encoding="ascii")
    return AnalysisInputs(
        plan={"plan_sha256": "c" * 64},
        evaluation=evaluation,
        development=development,
        freeze={},
        audit={"audit_sha256": "d" * 64},
        freeze_path=freeze,
        audit_path=audit,
        development_manifest=development_root / "manifest.json",
    )


def _fixed_bootstrap(values_by_map, **kwargs):
    values = [value for name in sorted(values_by_map) for value in values_by_map[name]]
    point = float(sorted(values)[len(values) // 2])
    return {
        "estimand": "median paired log(staged/lazy_full search-time ratio)",
        "point_log_ratio": point,
        "point_ratio": math.exp(point),
        "ci95_log_ratio": [point, point],
        "ci95_ratio": [math.exp(point), math.exp(point)],
        "bootstrap_probability_ratio_below_one": float(point < 0),
        "replicates": 10_000,
        "seed": 23_725_513,
        "top_level_clusters": 8,
        "query_observations": 800,
        "resampling_unit": "map-then-query-within-sampled-map",
    }


def _placeholder_figures(query_rows, map_rows, output: Path) -> None:
    for stem in analysis_module.FIGURE_STEMS:
        (output / f"{stem}.png").write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        (output / f"{stem}.pdf").write_bytes(b"%PDF-1.4\nfixture\n%%EOF\n")


def test_atomic_publication_loader_and_tamper_detection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = _fake_inputs(tmp_path)
    monkeypatch.setattr(analysis_module, "_load_inputs", lambda *args, **kwargs: inputs)
    monkeypatch.setattr(
        analysis_module, "hierarchical_bootstrap_median", _fixed_bootstrap
    )
    monkeypatch.setattr(analysis_module, "_render_figures", _placeholder_figures)
    repository_root = Path(__file__).resolve().parents[2]
    config = tmp_path / "config.json"
    config.write_text("{}\n", encoding="ascii")
    output = tmp_path / "published-analysis"
    published = analyze_sealed_evaluation(
        inputs.evaluation.root,
        output,
        config_path=config,
        repository_root=repository_root,
        freeze_manifest=inputs.freeze_path,
        development_audit=inputs.audit_path,
    )
    assert published == output
    loaded = load_complete_analysis(output)
    assert loaded.summary["analyzer"] == ANALYZER_ID
    assert loaded.summary["design"]["queries"] == 800
    assert loaded.summary["primary_staged_vs_lazy_full"]["replicates"] == 10_000
    assert (
        loaded.provenance["development_authorization"][
            "development_outcomes_used_in_analysis"
        ]
        is False
    )
    extra = output / "unexpected-directory"
    extra.mkdir()
    with pytest.raises(AnalysisError, match="inventory"):
        load_complete_analysis(output)
    extra.rmdir()
    with pytest.raises(AnalysisError, match="overwrite"):
        analyze_sealed_evaluation(
            inputs.evaluation.root,
            output,
            config_path=config,
            repository_root=repository_root,
            freeze_manifest=inputs.freeze_path,
            development_audit=inputs.audit_path,
        )

    with (output / "query_metrics.csv").open("ab") as target:
        target.write(b"tamper\n")
    with pytest.raises(AnalysisError, match="binding mismatch"):
        load_complete_analysis(output)


def test_formal_loader_is_always_called_with_replay_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    development = LoadedRun(
        tmp_path / "development",
        {},
        {"experiment_split": "development"},
        {},
        (),
    )
    evaluation = LoadedRun(
        tmp_path / "evaluation",
        {},
        {"experiment_split": "sealed_evaluation"},
        {},
        (),
    )
    calls: list[tuple[Path, dict]] = []
    expected_freeze = tmp_path / "freeze.json"

    def fake_loader(path, **kwargs):
        calls.append((Path(path), dict(kwargs)))
        if Path(path).name == "evaluation":
            if kwargs.get("freeze_manifest") != expected_freeze:
                raise analysis_module.RunnerError("external freeze differs")
            return evaluation
        return development

    monkeypatch.setattr(analysis_module, "load_complete_run", fake_loader)
    config = tmp_path / "config.json"
    repository = tmp_path / "repository"
    assert (
        analysis_module._load_replayed_run(
            development.root,
            config_path=config,
            repository_root=repository,
            expected_split="development",
        )
        is development
    )
    assert (
        analysis_module._load_replayed_run(
            evaluation.root,
            config_path=config,
            repository_root=repository,
            expected_split="sealed_evaluation",
            freeze_manifest=expected_freeze,
        )
        is evaluation
    )
    assert calls == [
        (
            development.root,
            {"config_path": config, "repository_root": repository},
        ),
        (
            evaluation.root,
            {
                "config_path": config,
                "repository_root": repository,
                "freeze_manifest": expected_freeze,
            },
        ),
    ]
    with pytest.raises(AnalysisError, match="requires the external freeze"):
        analysis_module._load_replayed_run(
            evaluation.root,
            config_path=config,
            repository_root=repository,
            expected_split="sealed_evaluation",
        )
    with pytest.raises(AnalysisError, match="forbids"):
        analysis_module._load_replayed_run(
            development.root,
            config_path=config,
            repository_root=repository,
            expected_split="development",
            freeze_manifest=expected_freeze,
        )
    with pytest.raises(AnalysisError, match="external freeze differs"):
        analysis_module._load_replayed_run(
            evaluation.root,
            config_path=config,
            repository_root=repository,
            expected_split="sealed_evaluation",
            freeze_manifest=tmp_path / "wrong-freeze.json",
        )

    def legacy_loader(path):
        return result

    monkeypatch.setattr(analysis_module, "load_complete_run", legacy_loader)
    with pytest.raises(AnalysisError, match="replaying formal-load API"):
        analysis_module._load_replayed_run(
            development.root,
            config_path=config,
            repository_root=repository,
            expected_split="development",
        )


def test_integrity_failure_publishes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reject(*args, **kwargs):
        raise AnalysisError("replayed BFS mismatch")

    monkeypatch.setattr(analysis_module, "_load_inputs", reject)
    repository_root = Path(__file__).resolve().parents[2]
    config = tmp_path / "config.json"
    config.write_text("{}\n", encoding="ascii")
    output = tmp_path / "must-not-exist"
    with pytest.raises(AnalysisError, match="BFS mismatch"):
        analyze_sealed_evaluation(
            tmp_path / "evaluation",
            output,
            config_path=config,
            repository_root=repository_root,
            freeze_manifest=tmp_path / "freeze.json",
            development_audit=tmp_path / "audit.json",
        )
    assert not output.exists()
    assert not list(tmp_path.glob(".must-not-exist.staging-*"))


def test_cli_help_is_read_only_and_names_every_gate_input(tmp_path: Path) -> None:
    script = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "analyze_progressive_landmarks.py"
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
    assert "--freeze-manifest" in completed.stdout
    assert "--development-audit" in completed.stdout
    assert "--repository-root" in completed.stdout
    assert "--output" in completed.stdout
    assert not list(tmp_path.iterdir())


def test_cross_artifact_recompute_detects_coherent_summary_and_manifest_rebinding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = _fake_inputs(tmp_path)
    monkeypatch.setattr(analysis_module, "_load_inputs", lambda *args, **kwargs: inputs)
    monkeypatch.setattr(
        analysis_module, "hierarchical_bootstrap_median", _fixed_bootstrap
    )
    monkeypatch.setattr(analysis_module, "_render_figures", _placeholder_figures)
    repository_root = Path(__file__).resolve().parents[2]
    config = tmp_path / "config.json"
    config.write_text("{}\n", encoding="ascii")
    output = tmp_path / "analysis"
    analyze_sealed_evaluation(
        inputs.evaluation.root,
        output,
        config_path=config,
        repository_root=repository_root,
        freeze_manifest=inputs.freeze_path,
        development_audit=inputs.audit_path,
    )
    summary_path = output / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="ascii"))
    summary["integrity"]["all_costs_match_bfs"] = False
    summary_core = {
        key: value for key, value in summary.items() if key != "summary_sha256"
    }
    summary["summary_sha256"] = canonical_json_sha256(summary_core)
    payload = (json.dumps(summary, sort_keys=True, indent=2) + "\n").encode("ascii")
    summary_path.write_bytes(payload)
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="ascii"))
    manifest["artifacts"]["summary.json"]["sha256"] = hashlib.sha256(
        payload
    ).hexdigest()
    manifest["artifacts"]["summary.json"]["bytes"] = len(payload)
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="ascii"
    )
    with pytest.raises(AnalysisError, match="recomputed query/map evidence"):
        load_complete_analysis(output)
