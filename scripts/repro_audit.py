#!/usr/bin/env python3
"""Read-only reproducibility checkpoint for the active landmark artifact.

This command intentionally does not build code, run tests, launch searches,
write scientific data, or repair partial runs. It verifies the authoritative
progressive-landmarks bundle and its isolation from a preserved superseded
attempt, then reports which report/administrative tasks remain pending. Frozen
MVC/CBS evidence is checked only as historical side material.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


PASS = "PASS"
PENDING = "PENDING"
WARN = "WARN"
FAIL = "FAIL"

NORMAL_CONFIG = Path("configs/policy_calibration_collection_aij3_bottomk_dev_v2.json")
COLD_CONFIG = Path(
    "configs/policy_calibration_collection_aij3_bottomk_cold_root_dev_v2.json"
)
ANALYSIS_CONFIG = Path("configs/policy_calibration_analysis_aij3_bottomk_dev_v2.json")
CALIBRATION_OUTPUT = Path("data/processed/policy_calibration_aij3_bottomk_dev_v2")
CONTEXT_OUTPUT = Path("data/processed/policy_contextual_aij3_bottomk_dev_v2")
MVC_REPORT_MANIFEST = Path("report/generated/mvc_negative_report_manifest.json")
ORACLE_AUDIT = Path("data/instances/expanded_v1/oracle_milp_v1.audit_v2.json")
LEGACY_OUTPUTS = (
    Path("data/results/policy_calibration_collection_aij3_dev_v1"),
    Path("data/results/policy_calibration_collection_aij3_cold_root_dev_v1"),
    Path("data/processed/policy_calibration_aij3_dev_v1"),
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

PROGRESSIVE_CONFIG = Path("configs/progressive_landmarks_v2.json")
PROGRESSIVE_RESULT_ROOT = Path("data/results/progressive_landmarks_v2_rerun1")
PROGRESSIVE_DEVELOPMENT = PROGRESSIVE_RESULT_ROOT / "development"
PROGRESSIVE_AUDIT = PROGRESSIVE_RESULT_ROOT / "development_audit.json"
PROGRESSIVE_FREEZE = PROGRESSIVE_RESULT_ROOT / "sealed_evaluation_freeze.json"
PROGRESSIVE_EVALUATION = PROGRESSIVE_RESULT_ROOT / "sealed_evaluation"
PROGRESSIVE_ANALYSIS = Path("data/processed/progressive_landmarks_analysis_v2")
PROGRESSIVE_PLAN_SHA256 = (
    "01aa82ec39842555d2e24216ccd93b9197f498daebf30e0e61aedd4fee5bd523"
)
PROGRESSIVE_EXPECTED_FILES = {
    PROGRESSIVE_CONFIG: "850176ee199020920e9a425db2fced560c776993148775472e2098e33c125410",
    PROGRESSIVE_DEVELOPMENT
    / "development_freeze_candidate.json": "dcb98ad65e6ddb274d3cdec46454b8918db6a17a2ad46dbdfe23b7442278d679",
    PROGRESSIVE_DEVELOPMENT
    / "maps.json": "f426aee15cee7e3dd83a448b0fb930b8c203403cbf70f6d60ccbe706d5a001f1",
    PROGRESSIVE_DEVELOPMENT
    / "queries.jsonl": "6dc517fd7b239b24ab82bfd578b2dea27b130bf9dfce9eb9d3305617518e6bb3",
    PROGRESSIVE_DEVELOPMENT
    / "run.json": "ec98e11f1a5a94eba1cf09f0038fc94185b18754d2a855b9fc3bb316d3d3bf0d",
    PROGRESSIVE_DEVELOPMENT
    / "manifest.json": "1d993d4ac7106730f5cddbfe7b5c15d979e8e4da9652b2a2d9f67b781d68814d",
    PROGRESSIVE_AUDIT: "d8792f0d34ef344b9dcd7aa441b4463c38769724803f2b9e807ba992fa8beab1",
    PROGRESSIVE_FREEZE: "3bce31ce4f942eccb0a0fc18c302e47fa477503a06b466cf6d798b21340f0e72",
    PROGRESSIVE_EVALUATION
    / "maps.json": "47db3e4a2b5b05a9671f27fec65259dc95318f055763e9dacae2dda640edb2d5",
    PROGRESSIVE_EVALUATION
    / "queries.jsonl": "8ba0e1a008bcfe1e8281c98b454ff243bad1e9c367bc5f25ac35236218e63ed5",
    PROGRESSIVE_EVALUATION
    / "run.json": "23e35eae365cf0780094c3e087b684b73cd26ca4a554dcc2d7539263321fc55f",
    PROGRESSIVE_EVALUATION
    / "manifest.json": "edaea56bb3aaa0b55b903e6dcde9692217a9d24a77da6a66bb52c1e583e62d53",
    PROGRESSIVE_ANALYSIS
    / "manifest.json": "47c3244fedcd52d2da0fa6f4889e0cb0cdb3289306f8b0ca69792149096c66df",
}
PROGRESSIVE_SUPERSEDED_FILES = {
    Path(
        "data/results/progressive_landmarks_development_v2/manifest.json"
    ): "f53642241843d1708ddccb88fd43f939782e09330847e0eeaf811d1deaf40388",
    Path(
        "data/results/development_audit.json"
    ): "e69fae4122a383f645057053abe94b9538e54814fbd74f5abc03fbc93c11a38d",
    Path(
        "data/results/sealed_evaluation_freeze.json"
    ): "910ee0600b43e42e65983c140be190c6029c6963770dae6b0c2c72fb495ea0ba",
    Path(
        "data/results/progressive_landmarks_sealed_evaluation_v2/manifest.json"
    ): "401acfe53bf7de5c59e64a8050f48bc22269ea28fc83a58dbd19b90a0d0ad125",
}


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    status: str
    detail: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def load_json(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8-sig"),
        object_pairs_hook=_without_duplicate_keys,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"non-finite JSON value {value!r}")
        ),
    )


def canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _require_exact_file(root: Path, relative: Path, expected_sha256: str) -> Path:
    path = root / relative
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"missing/non-regular artifact: {relative.as_posix()}")
    actual = sha256_file(path)
    if actual != expected_sha256:
        raise ValueError(
            f"artifact SHA-256 differs: {relative.as_posix()} "
            f"(expected {expected_sha256}, observed {actual})"
        )
    return path


def _nested_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _nested_strings(key)
            yield from _nested_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _nested_strings(item)


def superseded_progressive_references(value: Any) -> list[str]:
    """Return bindings that point at a preserved, non-authoritative v2 attempt."""

    forbidden = tuple(path.as_posix() for path in PROGRESSIVE_SUPERSEDED_FILES)
    return sorted(
        item
        for item in _nested_strings(value)
        if any(item == path or item.startswith(f"{path}/") for path in forbidden)
    )


def _validate_progressive_result(
    root: Path,
    relative: Path,
    *,
    split: str,
    maps: int,
    queries: int,
    searches: int,
) -> Mapping[str, Any]:
    result_root = root / relative
    manifest_path = result_root / "manifest.json"
    expected_manifest_sha = PROGRESSIVE_EXPECTED_FILES[relative / "manifest.json"]
    _require_exact_file(root, relative / "manifest.json", expected_manifest_sha)
    manifest = load_json(manifest_path)
    expected_counts = {"maps": maps, "queries": queries, "search_runs": searches}
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != "progressive-landmarks-result-manifest-v2"
        or manifest.get("protocol_id") != "progressive_landmarks_v2"
        or manifest.get("experiment_split") != split
        or manifest.get("formal") is not True
        or manifest.get("complete") is not True
        or manifest.get("validation") != "passed"
        or manifest.get("record_counts") != expected_counts
    ):
        raise ValueError(f"{split} completion marker differs from the frozen matrix")

    expected_names = {
        path.name
        for path in PROGRESSIVE_EXPECTED_FILES
        if path.parent == relative and path.name != "manifest.json"
    }
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != expected_names:
        raise ValueError(f"{split} manifest artifact inventory differs")
    actual_names = {item.name for item in result_root.iterdir() if item.is_file()}
    if actual_names != expected_names | {"manifest.json"} or any(
        item.is_symlink() or not item.is_file() for item in result_root.iterdir()
    ):
        raise ValueError(f"{split} directory inventory differs or contains a link")
    for name, descriptor in artifacts.items():
        if not isinstance(descriptor, dict) or set(descriptor) != {
            "bytes",
            "records",
            "sha256",
        }:
            raise ValueError(f"{split} descriptor differs: {name}")
        artifact_path = _require_exact_file(
            root,
            relative / name,
            PROGRESSIVE_EXPECTED_FILES[relative / name],
        )
        if (
            descriptor["sha256"] != sha256_file(artifact_path)
            or descriptor["bytes"] != artifact_path.stat().st_size
        ):
            raise ValueError(f"{split} manifest binding differs: {name}")

    run = load_json(result_root / "run.json")
    if (
        not isinstance(run, dict)
        or run.get("schema") != "progressive-landmarks-run-v2"
        or run.get("protocol_id") != "progressive_landmarks_v2"
        or run.get("experiment_split") != split
        or run.get("formal") is not True
        or run.get("nonformal_smoke") is not False
        or run.get("status") != "complete"
        or run.get("validation") != "passed"
        or run.get("counts")
        != {
            **expected_counts,
            "searches_per_query": 36,
            "timed_repetitions": 8,
            "warmup_repetitions": 1,
        }
        or run.get("bindings", {}).get("plan_sha256") != PROGRESSIVE_PLAN_SHA256
        or run.get("bindings", {}).get("code", {}).get("sha256")
        != "6412fb0e509302fed5c3f58d82b5e0dae761ffcf8da5ee7ae7aab5c11d0d0d92"
    ):
        raise ValueError(f"{split} run metadata/bindings differ")
    return manifest


def _audit_progressive_landmarks(
    root: Path,
    checks: list[Check],
    *,
    require_superseded_markers: bool,
) -> None:
    try:
        config_path = _require_exact_file(
            root,
            PROGRESSIVE_CONFIG,
            PROGRESSIVE_EXPECTED_FILES[PROGRESSIVE_CONFIG],
        )
        config = load_json(config_path)
        if (
            not isinstance(config, dict)
            or config.get("schema") != "progressive-landmarks-protocol-v2"
            or config.get("protocol_id") != "progressive_landmarks_v2"
        ):
            raise ValueError("progressive-landmarks v2 configuration identity differs")
        checks.append(
            Check(
                "active progressive-landmarks protocol",
                PASS,
                f"config hash verified; canonical plan sha256={PROGRESSIVE_PLAN_SHA256}",
            )
        )
    except (KeyError, OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        checks.append(Check("active progressive-landmarks protocol", FAIL, str(exc)))

    try:
        development_manifest = _validate_progressive_result(
            root,
            PROGRESSIVE_DEVELOPMENT,
            split="development",
            maps=4,
            queries=160,
            searches=5760,
        )
        checks.append(
            Check(
                "authoritative progressive development",
                PASS,
                "4 maps, 160 queries, and 5,760 searches hash-verified",
            )
        )
    except (KeyError, OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        development_manifest = None
        checks.append(Check("authoritative progressive development", FAIL, str(exc)))

    try:
        audit_path = _require_exact_file(
            root, PROGRESSIVE_AUDIT, PROGRESSIVE_EXPECTED_FILES[PROGRESSIVE_AUDIT]
        )
        freeze_path = _require_exact_file(
            root, PROGRESSIVE_FREEZE, PROGRESSIVE_EXPECTED_FILES[PROGRESSIVE_FREEZE]
        )
        audit = load_json(audit_path)
        freeze = load_json(freeze_path)
        if not isinstance(audit, dict) or not isinstance(freeze, dict):
            raise ValueError("audit/freeze roots must be objects")
        audit_core = {
            key: value for key, value in audit.items() if key != "audit_sha256"
        }
        if (
            audit.get("schema") != "progressive-landmarks-development-audit-v2"
            or audit.get("status") != "passed"
            or audit.get("selection_performed") is not False
            or audit.get("authorization_recommendation") != "sealed_evaluation"
            or audit.get("audit_sha256") != canonical_json_sha256(audit_core)
            or audit.get("checks", {}).get("formal_replay_passed") is not True
            or audit.get("checks", {}).get("verified_protocol") is not True
            or audit.get("development_result", {}).get("manifest_path")
            != "development/manifest.json"
            or audit.get("development_result", {}).get("manifest_sha256")
            != PROGRESSIVE_EXPECTED_FILES[PROGRESSIVE_DEVELOPMENT / "manifest.json"]
        ):
            raise ValueError("external development audit contract differs")
        if (
            freeze.get("schema") != "progressive-landmarks-sealed-evaluation-freeze-v2"
            or freeze.get("authorization") != "sealed_evaluation"
            or freeze.get("issued_by")
            != "progressive-landmarks-external-development-gate-v2"
            or freeze.get("bindings") != audit.get("bindings")
            or freeze.get("development_result") != audit.get("development_result")
            or freeze.get("development_audit")
            != {
                "path": "development_audit.json",
                "schema": "progressive-landmarks-development-audit-v2",
                "sha256": PROGRESSIVE_EXPECTED_FILES[PROGRESSIVE_AUDIT],
            }
        ):
            raise ValueError("sealed-evaluation freeze/audit binding differs")
        if development_manifest is None:
            raise ValueError("development result failed before authorization audit")
        checks.append(
            Check(
                "progressive external replay and freeze",
                PASS,
                "formal replay passed, selection_performed=false, authorization bound",
            )
        )
    except (KeyError, OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        checks.append(Check("progressive external replay and freeze", FAIL, str(exc)))

    try:
        _validate_progressive_result(
            root,
            PROGRESSIVE_EVALUATION,
            split="sealed_evaluation",
            maps=8,
            queries=800,
            searches=28800,
        )
        checks.append(
            Check(
                "authoritative progressive sealed evaluation",
                PASS,
                "8 maps, 800 queries, and 28,800 searches hash-verified",
            )
        )
    except (KeyError, OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        checks.append(
            Check("authoritative progressive sealed evaluation", FAIL, str(exc))
        )

    try:
        manifest_path = _require_exact_file(
            root,
            PROGRESSIVE_ANALYSIS / "manifest.json",
            PROGRESSIVE_EXPECTED_FILES[PROGRESSIVE_ANALYSIS / "manifest.json"],
        )
        manifest = load_json(manifest_path)
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema") != "progressive-landmarks-analysis-manifest-v2"
            or manifest.get("analyzer")
            != "progressive-landmarks-prospective-analysis-v2"
            or manifest.get("protocol_id") != "progressive_landmarks_v2"
            or manifest.get("complete") is not True
            or manifest.get("validation") != "passed"
            or manifest.get("record_counts")
            != {"figures": 10, "hypotheses": 3, "maps": 8, "queries": 800}
        ):
            raise ValueError("analysis completion marker differs")
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, dict) or len(artifacts) != 16:
            raise ValueError("analysis artifact inventory differs")
        expected_paths = {"manifest.json"}
        for name, descriptor in artifacts.items():
            if (
                not isinstance(name, str)
                or not isinstance(descriptor, dict)
                or set(descriptor) != {"bytes", "records", "sha256"}
                or not SHA256_RE.fullmatch(str(descriptor.get("sha256")))
            ):
                raise ValueError(f"analysis descriptor differs: {name!r}")
            relative_artifact = PROGRESSIVE_ANALYSIS / Path(name)
            artifact = root / relative_artifact
            expected_paths.add(name)
            if (
                not artifact.is_file()
                or artifact.is_symlink()
                or artifact.stat().st_size != descriptor["bytes"]
                or sha256_file(artifact) != descriptor["sha256"]
            ):
                raise ValueError(f"analysis artifact binding differs: {name}")
        actual_paths = {
            path.relative_to(root / PROGRESSIVE_ANALYSIS).as_posix()
            for path in (root / PROGRESSIVE_ANALYSIS).rglob("*")
            if path.is_file()
        }
        if actual_paths != expected_paths:
            raise ValueError("analysis tree contains missing or extra files")

        summary = load_json(root / PROGRESSIVE_ANALYSIS / "summary.json")
        provenance = load_json(root / PROGRESSIVE_ANALYSIS / "provenance.json")
        if not isinstance(summary, dict) or not isinstance(provenance, dict):
            raise ValueError("analysis summary/provenance roots must be objects")
        summary_core = {
            key: value for key, value in summary.items() if key != "summary_sha256"
        }
        provenance_core = {
            key: value
            for key, value in provenance.items()
            if key != "provenance_sha256"
        }
        primary = summary.get("primary_staged_vs_lazy_full", {})
        if (
            summary.get("summary_sha256") != canonical_json_sha256(summary_core)
            or provenance.get("provenance_sha256")
            != canonical_json_sha256(provenance_core)
            or summary.get("integrity", {}).get("all_800_queries_retained") is not True
            or summary.get("integrity", {}).get("all_costs_match_bfs") is not True
            or summary.get("integrity", {}).get(
                "all_full_landmark_expansion_digests_match"
            )
            is not True
            or summary.get("integrity", {}).get("performance_unlocked_after_all_gates")
            is not True
            or primary.get("point_ratio") != 1.0543002344159267
            or primary.get("ci95_ratio") != [0.9890432530795072, 1.1015852399893313]
        ):
            raise ValueError(
                "analysis self-hash, integrity gate, or primary result differs"
            )
        authorization = provenance.get("development_authorization", {})
        sealed = provenance.get("sealed_evaluation", {})
        if (
            authorization.get("development_outcomes_used_in_analysis") is not False
            or authorization.get("selection_performed") is not False
            or authorization.get("audit", {}).get("path")
            != PROGRESSIVE_AUDIT.as_posix()
            or authorization.get("audit", {}).get("sha256")
            != PROGRESSIVE_EXPECTED_FILES[PROGRESSIVE_AUDIT]
            or authorization.get("freeze", {}).get("path")
            != PROGRESSIVE_FREEZE.as_posix()
            or authorization.get("freeze", {}).get("sha256")
            != PROGRESSIVE_EXPECTED_FILES[PROGRESSIVE_FREEZE]
            or authorization.get("development_manifest", {}).get("path")
            != (PROGRESSIVE_DEVELOPMENT / "manifest.json").as_posix()
            or sealed.get("manifest", {}).get("path")
            != (PROGRESSIVE_EVALUATION / "manifest.json").as_posix()
            or sealed.get("manifest", {}).get("sha256")
            != PROGRESSIVE_EXPECTED_FILES[PROGRESSIVE_EVALUATION / "manifest.json"]
        ):
            raise ValueError(
                "analysis provenance does not bind the authoritative bundle"
            )
        relied_on = superseded_progressive_references(provenance)
        if relied_on:
            raise ValueError(f"analysis relies on superseded artifact(s): {relied_on}")
        checks.append(
            Check(
                "authoritative progressive analysis",
                PASS,
                "800 queries, 8 maps, 3 hypotheses, 10 figures; ratio=1.054300234",
            )
        )
    except (KeyError, OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        checks.append(Check("authoritative progressive analysis", FAIL, str(exc)))

    try:
        if require_superseded_markers:
            for relative, expected_sha in PROGRESSIVE_SUPERSEDED_FILES.items():
                _require_exact_file(root, relative, expected_sha)
            detail = (
                "four old completion markers preserved; authoritative provenance "
                "excludes them"
            )
        else:
            detail = (
                "authoritative provenance excludes every registered superseded path; "
                "old payload is not required by the active release"
            )
        checks.append(Check("superseded progressive attempt isolation", PASS, detail))
    except (OSError, ValueError) as exc:
        checks.append(Check("superseded progressive attempt isolation", FAIL, str(exc)))


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _resolve_from(source: Path, registered: Any) -> Path:
    if not isinstance(registered, str) or not registered:
        raise ValueError("registered path must be a non-empty string")
    candidate = Path(registered)
    return (
        candidate.resolve()
        if candidate.is_absolute()
        else (source.parent / candidate).resolve()
    )


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _collection_errors(
    config: Mapping[str, Any], *, expected_cap: int, expected_algorithms: set[str]
) -> list[str]:
    errors: list[str] = []
    if config.get("schema") != "msrla-run-config-v2":
        errors.append("schema is not msrla-run-config-v2")
    algorithms = config.get("algorithms")
    if not isinstance(algorithms, list):
        return [*errors, "algorithms is not a list"]
    observed_ids = {
        item.get("algorithm_id") for item in algorithms if isinstance(item, dict)
    }
    if observed_ids != expected_algorithms or len(algorithms) != len(
        expected_algorithms
    ):
        errors.append("algorithm IDs differ from the replacement registration")
    for index, algorithm in enumerate(algorithms):
        if not isinstance(algorithm, dict):
            errors.append(f"algorithms[{index}] is not an object")
            continue
        sampling = algorithm.get("trace_sampling")
        if (
            algorithm.get("mode") != "pairwise_aij3"
            or algorithm.get("trace_decisions") is not True
            or algorithm.get("trace_detail") != "action_timing"
            or not isinstance(sampling, dict)
            or sampling.get("design") != "bottom_k_node_v1"
            or sampling.get("unit") != "node"
            or sampling.get("rank_algorithm") != "splitmix64-node-permutation-rank-v1"
            or sampling.get("cap") != expected_cap
        ):
            errors.append(f"algorithms[{index}] trace contract drifted")
    storage = config.get("attempt_storage")
    if (
        not isinstance(storage, dict)
        or storage.get("format") != "per-trial-gzip-json-v1"
        or storage.get("compression") != "gzip"
        or storage.get("compression_level") != 6
    ):
        errors.append("compressed-attempt storage contract drifted")
    limits = config.get("process_output_limits")
    if (
        not isinstance(limits, dict)
        or not _is_int(limits.get("stdout_bytes"))
        or not _is_int(limits.get("stderr_bytes"))
    ):
        errors.append("bounded process-output contract is absent")
    return errors


def _audit_registered_inputs(root: Path, checks: list[Check]) -> dict[str, Any] | None:
    paths = [root / NORMAL_CONFIG, root / COLD_CONFIG, root / ANALYSIS_CONFIG]
    if any(not path.is_file() for path in paths):
        missing = [str(path.relative_to(root)) for path in paths if not path.is_file()]
        checks.append(Check("replacement registrations", FAIL, f"missing: {missing}"))
        return None
    try:
        normal, cold, analysis = (load_json(path) for path in paths)
        if not all(isinstance(value, dict) for value in (normal, cold, analysis)):
            raise ValueError("registration root must be a JSON object")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        checks.append(Check("replacement registrations", FAIL, str(exc)))
        return None

    normal_errors = _collection_errors(
        normal,
        expected_cap=16384,
        expected_algorithms={
            "aij3_timing_forced_evaluate_three_stage",
            "aij3_timing_forced_bypass_stage1",
            "aij3_timing_forced_h2_then_bypass_h3",
        },
    )
    cold_errors = _collection_errors(
        cold,
        expected_cap=1,
        expected_algorithms={"aij3_timing_forced_evaluate_three_stage_cold_root"},
    )
    if analysis.get("schema") != "msrla-aij3-calibration-config-v1":
        normal_errors.append("analysis registration schema drifted")
    errors = normal_errors + cold_errors
    checks.append(
        Check(
            "replacement registration contracts",
            PASS if not errors else FAIL,
            (
                "normal K=16384 and cold K=1, gzip/action_timing"
                if not errors
                else "; ".join(errors)
            ),
        )
    )

    try:
        manifest_path = _resolve_from(paths[0], normal["instance_manifest"])
        cold_manifest = _resolve_from(paths[1], cold["instance_manifest"])
        if manifest_path != cold_manifest or not _within(manifest_path, root):
            raise ValueError("normal/cold manifests differ or escape repository")
        manifest = load_json(manifest_path)
        records = manifest["instances"]
        if not isinstance(records, list) or not records:
            raise ValueError("instance manifest has no records")
        ids: dict[str, Mapping[str, Any]] = {}
        graph_failures: list[str] = []
        for record in records:
            if not isinstance(record, dict) or not isinstance(
                record.get("instance_id"), str
            ):
                raise ValueError("instance manifest record is malformed")
            instance_id = record["instance_id"]
            if instance_id in ids:
                raise ValueError(f"duplicate instance ID {instance_id!r}")
            ids[instance_id] = record
            graph_path = (
                manifest_path.parent / str(record.get("graph_path"))
            ).resolve()
            expected_sha = record.get("sha256")
            if (
                not _within(graph_path, manifest_path.parent)
                or not graph_path.is_file()
                or not isinstance(expected_sha, str)
                or sha256_file(graph_path) != expected_sha
            ):
                graph_failures.append(instance_id)
        if graph_failures:
            raise ValueError(f"graph hash/path failures: {graph_failures[:5]}")
        checks.append(
            Check(
                "expanded-v1 instance files",
                PASS,
                f"{len(records)} graph hashes verified; manifest "
                f"sha256={sha256_file(manifest_path)}",
            )
        )

        development = analysis["development_instance_ids"]
        training = analysis["training_instance_ids"]
        validation = analysis["validation_instance_ids"]
        normal_ids = normal["instance_ids"]
        cold_ids = cold["instance_ids"]
        if not all(
            isinstance(value, list) for value in (development, training, validation)
        ):
            raise ValueError("development split fields must be lists")
        if (
            normal_ids != development
            or cold_ids != development
            or len(set(development)) != len(development)
            or set(training) & set(validation)
            or set(training) | set(validation) != set(development)
            or any(
                ids[item].get("replicate") not in {0, 1, 2, 3} for item in development
            )
            or any(ids[item].get("replicate") not in {0, 1, 2} for item in training)
            or any(ids[item].get("replicate") != 3 for item in validation)
        ):
            raise ValueError("development/train/validation split contract drifted")
        checks.append(
            Check(
                "sealed split registration",
                PASS,
                f"{len(training)} train (replicates 0-2), {len(validation)} validation "
                "(replicate 3), no holdout replicate 4-7 IDs",
            )
        )

        oracle = analysis["oracle"]
        oracle_path = _resolve_from(paths[2], oracle["path"])
        expected_oracle_sha = oracle["sha256"]
        actual_oracle_sha = sha256_file(oracle_path)
        if (
            not isinstance(expected_oracle_sha, str)
            or not SHA256_RE.fullmatch(expected_oracle_sha)
            or actual_oracle_sha != expected_oracle_sha
        ):
            raise ValueError("registered exact-oracle SHA-256 differs")
        checks.append(
            Check(
                "registered exact oracle",
                PASS,
                f"sha256={actual_oracle_sha}",
            )
        )
    except (KeyError, OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        checks.append(Check("registered corpus/oracle/split", FAIL, str(exc)))
        return None

    return {
        "normal_config": normal,
        "normal_config_path": paths[0],
        "cold_config": cold,
        "cold_config_path": paths[1],
        "analysis": analysis,
        "analysis_path": paths[2],
        "manifest_path": manifest_path,
        "oracle_path": oracle_path,
    }


def _audit_historical_oracle(
    root: Path, inputs: Mapping[str, Any], checks: list[Check]
) -> None:
    path = root / ORACLE_AUDIT
    if not path.is_file():
        checks.append(
            Check("independent oracle audit", PENDING, f"absent: {ORACLE_AUDIT}")
        )
        return
    try:
        audit = load_json(path)
        if (
            audit.get("schema") != "msrla-milp-oracle-audit-v2"
            or audit.get("audit_status") != "passed"
            or audit["inputs"]["manifest_sha256"]
            != sha256_file(inputs["manifest_path"])
            or audit["oracle_artifact"]["sha256"] != sha256_file(inputs["oracle_path"])
            or audit["oracle_artifact"]["optimal_records"] != 96
            or audit["bound_audit"]["independent_exact_cost_agreement_records"] != 96
        ):
            raise ValueError("audit status, cardinality, or input hashes differ")
        checks.append(
            Check(
                "independent oracle audit",
                PASS,
                "96/96 MILP optima independently agreed with complement-clique exact costs",
            )
        )
    except (KeyError, OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        checks.append(Check("independent oracle audit", FAIL, str(exc)))


def _audit_compressed_run(
    label: str,
    config_path: Path,
    config: Mapping[str, Any],
    output: Path,
    checks: list[Check],
) -> None:
    planned = (
        len(config.get("instance_ids", []))
        * len(config.get("algorithms", []))
        * config.get("repetitions", 0)
    )
    if not output.exists():
        checks.append(
            Check(label, PENDING, f"not launched; registered plan has {planned} trials")
        )
        return
    try:
        if output.is_symlink() or not output.is_dir():
            raise ValueError("run output is not a regular directory")
        required = (
            output / "config.snapshot.json",
            output / "instances.snapshot.json",
            output / "run_manifest.json",
            output / "attempt_index.json",
            output / "attempts",
        )
        missing = [path.name for path in required if not path.exists()]
        if missing:
            raise ValueError(f"missing immutable run entries: {missing}")
        snapshot = output / "config.snapshot.json"
        if snapshot.read_bytes() != config_path.read_bytes():
            raise ValueError("config snapshot differs from registered config bytes")
        run_manifest = load_json(output / "run_manifest.json")
        index = load_json(output / "attempt_index.json")
        if (
            run_manifest.get("schema") != "msrla-experiment-run-v2"
            or index.get("schema") != "msrla-attempt-index-v1"
            or run_manifest.get("run_id") != index.get("run_id")
            or run_manifest.get("planned_trials") != planned
            or index.get("planned_trials") != planned
        ):
            raise ValueError("run manifest/index plan contract differs")
        entries = index.get("entries")
        if not isinstance(entries, list) or len(entries) > planned:
            raise ValueError("attempt index length is invalid")
        indexed_names: set[str] = set()
        attempts_dir = output / "attempts"
        for sequence, entry in enumerate(entries):
            if not isinstance(entry, dict) or entry.get("sequence_index") != sequence:
                raise ValueError(f"attempt index entry {sequence} is non-contiguous")
            filename = entry.get("filename")
            if (
                not isinstance(filename, str)
                or Path(filename).name != filename
                or filename in indexed_names
            ):
                raise ValueError(f"attempt index entry {sequence} filename is invalid")
            indexed_names.add(filename)
            attempt_path = attempts_dir / filename
            if (
                not attempt_path.is_file()
                or attempt_path.stat().st_size != entry.get("compressed_size_bytes")
                or sha256_file(attempt_path) != entry.get("compressed_sha256")
            ):
                raise ValueError(f"attempt {sequence} stored hash/size differs")
        actual_names = {path.name for path in attempts_dir.iterdir() if path.is_file()}
        extras = sorted(actual_names - indexed_names)
        if extras:
            checks.append(
                Check(
                    f"{label} orphan state",
                    WARN,
                    f"{len(extras)} unindexed file(s); use the exact --resume command "
                    "for fail-closed recovery, never edit them",
                )
            )
        status = PASS if len(entries) == planned and not extras else PENDING
        detail = f"{len(entries)}/{planned} immutable attempts hash-verified"
        if len(entries) < planned:
            detail += "; resume is required"
        checks.append(Check(label, status, detail))
    except (KeyError, OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        checks.append(Check(label, FAIL, str(exc)))


def _audit_normalized_calibration(path: Path, checks: list[Check]) -> str | None:
    if not path.exists():
        checks.append(
            Check("bottom-k normalized calibration", PENDING, f"absent: {path}")
        )
        return None
    try:
        if path.is_symlink() or not path.is_dir():
            raise ValueError("calibration artifact is not a regular directory")
        manifest_path = path / "manifest.json"
        manifest = load_json(manifest_path)
        if (
            manifest.get("schema") != "msrla-aij3-normalized-v2"
            or manifest.get("artifact_id") != "aij3-bottom-k-calibration-v2"
        ):
            raise ValueError("normalized calibration schema/artifact ID differs")
        expected_files = {"manifest.json"}
        for descriptor in manifest["tables"].values():
            artifact = path / descriptor["path"]
            expected_files.add(descriptor["path"])
            if (
                not artifact.is_file()
                or artifact.stat().st_size != descriptor["compressed_size_bytes"]
                or sha256_file(artifact) != descriptor["compressed_sha256"]
            ):
                raise ValueError(f"table binding differs: {descriptor['path']}")
        for descriptor in manifest["compact_outputs"].values():
            artifact = path / descriptor["path"]
            expected_files.add(descriptor["path"])
            if (
                not artifact.is_file()
                or artifact.stat().st_size != descriptor["size_bytes"]
                or sha256_file(artifact) != descriptor["sha256"]
            ):
                raise ValueError(
                    f"compact output binding differs: {descriptor['path']}"
                )
        actual_files = {item.name for item in path.iterdir() if item.is_file()}
        if actual_files != expected_files:
            raise ValueError("normalized calibration contains missing/extra files")
        calibration_descriptor = manifest["compact_outputs"]["calibration"]
        calibration = load_json(path / calibration_descriptor["path"])
        ready = calibration["completeness"]["ready_for_contextual_fit"] is True
        manifest_sha = sha256_file(manifest_path)
        checks.append(
            Check(
                "bottom-k normalized calibration",
                PASS if ready else PENDING,
                f"manifest sha256={manifest_sha}; contextual gate "
                f"{'passed' if ready else 'not passed'}",
            )
        )
        return manifest_sha
    except (KeyError, OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        checks.append(Check("bottom-k normalized calibration", FAIL, str(exc)))
        return None


def _audit_context(
    path: Path, calibration_sha: str | None, checks: list[Check]
) -> None:
    if not path.exists():
        expected = (
            f"pass --expected-calibration-manifest-sha256 {calibration_sha}"
            if calibration_sha
            else "requires a complete normalized calibration and its manifest SHA-256"
        )
        checks.append(Check("contextual model", PENDING, expected))
        return
    try:
        if path.is_symlink() or not path.is_dir():
            raise ValueError("context artifact is not a regular directory")
        if {item.name for item in path.iterdir()} != {
            "context_model.json",
            "manifest.json",
        }:
            raise ValueError("context artifact must contain exactly model and manifest")
        model_path = path / "context_model.json"
        manifest_path = path / "manifest.json"
        model = load_json(model_path)
        manifest = load_json(manifest_path)
        output = manifest["output"]
        if (
            manifest.get("schema") != "msrla-aij3-context-model-bottom-k-manifest-v2"
            or model.get("schema") != "msrla-aij3-context-model-bottom-k-v2"
            or output.get("path") != "context_model.json"
            or output.get("size_bytes") != model_path.stat().st_size
            or output.get("sha256") != sha256_file(model_path)
            or (
                calibration_sha is not None
                and manifest["inputs"].get("normalized_manifest_sha256")
                != calibration_sha
            )
        ):
            raise ValueError("context model/manifest/input hash binding differs")
        ready = model["completeness"]["ready_for_cpp_parity_implementation"] is True
        pre_refit_passed = model["selection"]["action_gates"]["passed"]
        post_refit_passed = model["development_refit"][
            "post_refit_actual_exported_model_mechanism_audit"
        ]["passed"]
        holdout_opened = model["development_split"]["holdout_replicates_opened"]
        if holdout_opened != []:
            raise ValueError("MVC contextual fit opened a registered holdout replicate")
        if ready:
            status = PENDING
            detail = "C++ parity gate open; final freeze remains false"
        elif pre_refit_passed is False and post_refit_passed is False:
            status = PASS
            detail = (
                "registered mechanism gate rejected the model before C++ parity; "
                "post-refit gate also failed and holdout remains sealed"
            )
        else:
            status = PENDING
            detail = "C++ parity gate not open; development decision is unresolved"
        checks.append(
            Check(
                "contextual model",
                status,
                f"manifest sha256={sha256_file(manifest_path)}; {detail}",
            )
        )
    except (KeyError, OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        checks.append(Check("contextual model", FAIL, str(exc)))


def _audit_mvc_negative_report(root: Path, checks: list[Check]) -> None:
    manifest_path = root / MVC_REPORT_MANIFEST
    if not manifest_path.is_file():
        checks.append(
            Check(
                "frozen MVC negative-result report",
                PASS,
                "historical generated report is not required by the active project",
            )
        )
        return
    try:
        manifest = load_json(manifest_path)
        if set(manifest) != {
            "schema",
            "manifest_written_last",
            "source_hashes",
            "outputs",
        }:
            raise ValueError("MVC report manifest keys differ")
        if (
            manifest["schema"] != "msrla-mvc-negative-report-manifest-v1"
            or manifest["manifest_written_last"] is not True
        ):
            raise ValueError("MVC report manifest schema/write-last marker differs")
        expected_sources = {
            "calibration_sha256": sha256_file(
                root / CALIBRATION_OUTPUT / "calibration.json"
            ),
            "context_model_sha256": sha256_file(
                root / CONTEXT_OUTPUT / "context_model.json"
            ),
        }
        if manifest["source_hashes"] != expected_sources:
            raise ValueError("MVC report source hashes differ from frozen inputs")
        outputs = manifest["outputs"]
        if not isinstance(outputs, dict) or set(outputs) != {
            "summary",
            "table",
            "figure",
        }:
            raise ValueError("MVC report output set differs")
        expected_paths = {
            "summary": "mvc_negative_summary.json",
            "table": "table_aij3_calibration.tex",
            "figure": "fig_aij3_calibration.pdf",
        }
        for label, expected_path in expected_paths.items():
            descriptor = outputs[label]
            if not isinstance(descriptor, dict) or set(descriptor) != {
                "path",
                "size_bytes",
                "sha256",
            }:
                raise ValueError(f"MVC {label} descriptor keys differ")
            if descriptor["path"] != expected_path:
                raise ValueError(f"MVC {label} path differs")
            artifact = manifest_path.parent / expected_path
            if (
                not artifact.is_file()
                or artifact.is_symlink()
                or descriptor["size_bytes"] != artifact.stat().st_size
                or descriptor["sha256"] != sha256_file(artifact)
            ):
                raise ValueError(f"MVC {label} hash/size binding differs")
        summary = load_json(manifest_path.parent / expected_paths["summary"])
        if (
            summary.get("schema") != "msrla-mvc-negative-report-summary-v1"
            or summary.get("holdout_replicates_opened") != []
            or summary.get("decision", {}).get("mvc_policy_frozen") is not False
            or summary.get("decision", {}).get("mvc_holdout_opened") is not False
            or summary.get("pre_refit_validation_gate", {}).get("passed") is not False
            or summary.get("post_refit_development_gate", {}).get("passed") is not False
        ):
            raise ValueError("MVC report decision/holdout contract differs")
        checks.append(
            Check(
                "frozen MVC negative-result report",
                PASS,
                f"manifest sha256={sha256_file(manifest_path)}; holdout sealed",
            )
        )
    except (KeyError, OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        checks.append(Check("frozen MVC negative-result report", FAIL, str(exc)))


def _audit_report(root: Path, checks: list[Check]) -> None:
    try:
        report_sources = sorted((root / "report").rglob("*.tex"))
        source = "\n".join(path.read_text(encoding="utf-8") for path in report_sources)
        placeholders = (
            "\\draftresultstrue" in source
            or "[STUDENT NAME" in source
            or "BLOCKER:" in source
            or "<REPOSITORY_URL>" in source
            or "pendingvalue" in source
        )
        active = "landmark" in source.lower() and "progressive" in source.lower()
    except OSError as exc:
        checks.append(Check("report source", FAIL, str(exc)))
        placeholders = True
        active = False
    checks.append(
        Check(
            "active submission report source",
            PASS if active and not placeholders else PENDING,
            (
                "progressive-landmarks narrative with no identity/repository placeholders"
                if active and not placeholders
                else "active narrative, identity fields, or repository link remain pending"
            ),
        )
    )
    pdf = root / "report" / "main.pdf"
    checks.append(
        Check(
            "submission report",
            PASS if pdf.is_file() and not placeholders else PENDING,
            (
                f"pdf sha256={sha256_file(pdf)}"
                if pdf.is_file() and not placeholders
                else "draft-results/identity/repository placeholders or official-style PDF remain"
            ),
        )
    )


def audit_repository(root: Path, *, include_historical: bool = True) -> list[Check]:
    """Audit the active artifact and, optionally, archived research programs.

    ``include_historical=False`` is the public-release boundary: it verifies
    every input and output used by the progressive-landmarks paper while
    avoiding any dependency on the preserved MVC/CBS workspace history.
    """

    root = root.resolve()
    checks: list[Check] = []
    required = [
        ".gitattributes",
        ".gitignore",
        "NOTICE.md",
        "README.md",
        "active_project/TOPIC_PROPOSAL.md",
        "active_project/PROJECT_SPEC.md",
        "active_project/LITERATURE_MAP.md",
        "active_project/EXPERIMENT_PROVENANCE.md",
        "active_project/PLAN.md",
        "active_project/STATUS.md",
        "pyproject.toml",
        "requirements-progressive-landmarks-lock.txt",
        "scripts/verify_progressive_landmarks_protocol.py",
        "scripts/run_progressive_landmarks.py",
        "scripts/freeze_progressive_landmarks_development.py",
        "scripts/analyze_progressive_landmarks.py",
        "scripts/package_progressive_landmarks_release.py",
        "src/python/progressive_landmarks/core.py",
        "src/python/progressive_landmarks/protocol.py",
        "src/python/progressive_landmarks/runner.py",
        "src/python/progressive_landmarks/development_gate.py",
        "src/python/progressive_landmarks/analysis.py",
        "report/main.tex",
    ]
    if include_historical:
        required.append("requirements-direct-lock.txt")
    missing = [item for item in required if not (root / item).is_file()]
    checks.append(
        Check(
            "active source and durable-memory files",
            PASS if not missing else FAIL,
            "all present" if not missing else f"missing: {missing}",
        )
    )

    _audit_progressive_landmarks(
        root,
        checks,
        require_superseded_markers=include_historical,
    )

    if include_historical:
        # These checks preserve the health of explicitly archived MVC/CBS side
        # material. None is an input to the active landmark artifact.
        inputs = _audit_registered_inputs(root, checks)
        if inputs is not None:
            _audit_historical_oracle(root, inputs, checks)
            for legacy in LEGACY_OUTPUTS:
                checks.append(
                    Check(
                        f"superseded output remains absent: {legacy.name}",
                        PASS if not (root / legacy).exists() else FAIL,
                        (
                            "not launched"
                            if not (root / legacy).exists()
                            else "must not be used"
                        ),
                    )
                )
            collections = inputs["analysis"]["collections"]
            normal_output = _resolve_from(
                inputs["analysis_path"],
                collections["aij3_timing_forced_evaluate_three_stage"]["run_directory"],
            )
            cold_output = _resolve_from(
                inputs["analysis_path"],
                collections["aij3_timing_forced_evaluate_three_stage_cold_root"][
                    "run_directory"
                ],
            )
            _audit_compressed_run(
                "normal bottom-k collection",
                inputs["normal_config_path"],
                inputs["normal_config"],
                normal_output,
                checks,
            )
            _audit_compressed_run(
                "cold-root bottom-k collection",
                inputs["cold_config_path"],
                inputs["cold_config"],
                cold_output,
                checks,
            )

        calibration_sha = _audit_normalized_calibration(
            root / CALIBRATION_OUTPUT, checks
        )
        _audit_context(root / CONTEXT_OUTPUT, calibration_sha, checks)
        _audit_mvc_negative_report(root, checks)
        solver = root / "build-ninja" / "mvc_solver.exe"
        checks.append(
            Check(
                "local solver build",
                PASS if solver.is_file() else PENDING,
                (
                    f"sha256={sha256_file(solver)}"
                    if solver.is_file()
                    else "run the documented build"
                ),
            )
        )
    checks.append(
        Check(
            "archived MVC/CBS evidence boundary",
            PASS,
            "historical domains remain side material and are excluded from active provenance",
        )
    )
    _audit_report(root, checks)
    return checks


def _summary(checks: Iterable[Check]) -> dict[str, int]:
    result = {status: 0 for status in (PASS, PENDING, WARN, FAIL)}
    for check in checks:
        result[check.status] += 1
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root (default: parent of scripts/)",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="also return nonzero for PENDING or WARN checks",
    )
    parser.add_argument(
        "--active-only",
        action="store_true",
        help="verify only the active artifact (default; retained for explicit scripts)",
    )
    parser.add_argument(
        "--include-historical",
        action="store_true",
        help="also verify archived MVC/CBS workspace evidence",
    )
    args = parser.parse_args(argv)
    if args.active_only and args.include_historical:
        parser.error("--active-only and --include-historical are mutually exclusive")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    checks = audit_repository(args.root, include_historical=args.include_historical)
    counts = _summary(checks)
    if args.json:
        print(
            json.dumps(
                {
                    "root": str(args.root.resolve()),
                    "summary": counts,
                    "checks": [asdict(item) for item in checks],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
    else:
        for check in checks:
            print(f"[{check.status:7}] {check.name}: {check.detail}")
        print(
            "summary: "
            + ", ".join(
                f"{status}={counts[status]}" for status in (PASS, PENDING, WARN, FAIL)
            )
        )
    if counts[FAIL] or (args.require_complete and (counts[PENDING] or counts[WARN])):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
