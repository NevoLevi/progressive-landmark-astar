"""Adversarial tests for the frozen progressive-landmarks protocol."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path

import pytest

from progressive_landmarks import protocol


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPOSITORY_ROOT / "configs" / "progressive_landmarks_v2.json"
SOURCE_RELATIVE = Path("data/source/movingai_mapf_2021-06-17")
EXPECTED_PLAN_SHA256 = (
    "01aa82ec39842555d2e24216ccd93b9197f498daebf30e0e61aedd4fee5bd523"
)


def _config() -> dict[str, object]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _write_config(root: Path, value: object) -> Path:
    path = root / "configs" / "progressive_landmarks_v2.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="ascii",
        newline="\n",
    )
    return path


def _copy_source(root: Path) -> Path:
    destination = root / SOURCE_RELATIVE
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(REPOSITORY_ROOT / SOURCE_RELATIVE, destination)
    return destination


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _replace_checksum(checksum_path: Path, relative: str, digest: str) -> None:
    rows = checksum_path.read_text(encoding="ascii").splitlines()
    replaced = False
    updated: list[str] = []
    for row in rows:
        old_digest, old_relative = row.split("  ", 1)
        if old_relative == relative:
            assert len(old_digest) == 64
            updated.append(f"{digest}  {relative}")
            replaced = True
        else:
            updated.append(row)
    assert replaced
    checksum_path.write_text("\n".join(updated) + "\n", encoding="ascii", newline="\n")


def test_frozen_plan_has_exact_queries_bindings_and_rotations() -> None:
    plan = protocol.verify_protocol(CONFIG_PATH, repository_root=REPOSITORY_ROOT)

    assert plan["plan_sha256"] == EXPECTED_PLAN_SHA256
    core = {key: value for key, value in plan.items() if key != "plan_sha256"}
    assert protocol.canonical_json_sha256(core) == EXPECTED_PLAN_SHA256
    assert plan["config_binding"] == {
        "path": "configs/progressive_landmarks_v2.json",
        "sha256": _sha256(CONFIG_PATH),
        "hash_basis": "file-bytes",
    }
    assert plan["counts"] == {
        "maps": 12,
        "scenario_files": 48,
        "queries": 960,
        "development_queries": 160,
        "sealed_evaluation_queries": 800,
        "methods": 4,
        "warmup_repetitions": 1,
        "timed_repetitions": 8,
        "planned_search_runs": 34560,
    }
    assert plan["methods"] == list(protocol.METHODS)

    queries = plan["queries"]
    assert Counter(item["experiment_split"] for item in queries) == {
        "development": 160,
        "sealed_evaluation": 800,
    }
    assert Counter((item["experiment_split"], item["family"]) for item in queries) == {
        **{("development", family): 40 for family in protocol.FAMILIES},
        **{("sealed_evaluation", family): 200 for family in protocol.FAMILIES},
    }
    assert len({item["query_id"] for item in queries}) == 960
    assert (
        len(
            {
                (item["map"], tuple(item["start"]), tuple(item["goal"]))
                for item in queries
            }
        )
        == 960
    )

    rows_by_file: dict[tuple[str, str], list[int]] = defaultdict(list)
    for item in queries:
        rows_by_file[(item["map"], item["scenario"])].append(item["scenario_row"])
        assert item["source_line"] == item["scenario_row"] + 1
    assert len(rows_by_file) == 48
    for (map_name, _), rows in rows_by_file.items():
        expected_count = (
            10
            if "development:"
            in next(item["query_id"] for item in queries if item["map"] == map_name)
            else 25
        )
        assert rows == list(range(1, expected_count + 1))

    assert len(plan["input_bindings"]) == 12
    assert sum(len(item["scenarios"]) for item in plan["input_bindings"]) == 48
    assert all(
        len(binding["map"]["sha256"]) == 64
        and all(len(row["sha256"]) == 64 for row in binding["scenarios"])
        for binding in plan["input_bindings"]
    )
    assert plan["timing_orders"]["warmup"] == [
        {
            "repetition": 0,
            "timed": False,
            "methods": list(protocol.METHODS),
        }
    ]
    expected_timed = []
    base = list(protocol.METHODS)
    for repetition in range(8):
        offset = repetition % 4
        expected_timed.append(
            {
                "repetition": repetition,
                "timed": True,
                "methods": base[offset:] + base[:offset],
            }
        )
    assert plan["timing_orders"]["timed"] == expected_timed
    for method in protocol.METHODS:
        assert [
            sum(order["methods"][position] == method for order in expected_timed)
            for position in range(len(protocol.METHODS))
        ] == [2, 2, 2, 2]


def test_plan_is_byte_deterministic() -> None:
    first = protocol.verify_protocol(CONFIG_PATH, repository_root=REPOSITORY_ROOT)
    second = protocol.verify_protocol(CONFIG_PATH, repository_root=REPOSITORY_ROOT)
    assert protocol.canonical_json_bytes(first) == protocol.canonical_json_bytes(second)


def test_strict_decoder_rejects_duplicate_config_key(tmp_path: Path) -> None:
    raw = CONFIG_PATH.read_text(encoding="utf-8")
    tampered = raw.replace("{", '{\n  "schema": "shadowed-by-duplicate",', 1)
    path = tmp_path / "duplicate.json"
    path.write_text(tampered, encoding="utf-8")
    with pytest.raises(protocol.ProtocolError, match="duplicate JSON key"):
        protocol.load_protocol(path)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda value: value["experimental_split"]["maps"]["development"][0].update(
                {"source_split": "validation"}
            ),
            "leaks across experiment split",
        ),
        (
            lambda value: value["experimental_split"]["maps"]["sealed_evaluation"][
                0
            ].update(
                {"map": value["experimental_split"]["maps"]["development"][0]["map"]}
            ),
            "must not overlap",
        ),
    ],
)
def test_split_tampering_fails_closed(
    tmp_path: Path, mutation: object, message: str
) -> None:
    value = _config()
    mutation(value)
    path = _write_config(tmp_path, value)
    with pytest.raises(protocol.ProtocolError, match=message):
        protocol.load_protocol(path)


def test_family_identity_tampering_fails_against_manifest(tmp_path: Path) -> None:
    value = _config()
    value["experimental_split"]["maps"]["development"][0]["family"] = "random"
    path = _write_config(tmp_path, value)
    with pytest.raises(protocol.ProtocolError, match="identity disagrees"):
        protocol.verify_protocol(path, repository_root=REPOSITORY_ROOT)


def test_frozen_query_count_tampering_fails_closed(tmp_path: Path) -> None:
    value = _config()
    value["query_selection"]["development_valid_rows_per_file"] = 9
    path = _write_config(tmp_path, value)
    with pytest.raises(protocol.ProtocolError, match="must equal 10"):
        protocol.load_protocol(path)


def test_selected_query_count_is_rechecked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = protocol._select_queries

    def short_selector(**kwargs: object) -> list[dict[str, object]]:
        return original(**kwargs)[:-1]

    monkeypatch.setattr(protocol, "_select_queries", short_selector)
    with pytest.raises(protocol.ProtocolError, match="query matrix mismatch"):
        protocol.build_plan(_config(), repository_root=REPOSITORY_ROOT)


def test_bound_source_payload_hash_tampering_fails_closed(tmp_path: Path) -> None:
    source = _copy_source(tmp_path)
    map_path = source / "corpus" / "maps" / "random-32-32-10.map"
    map_path.write_bytes(map_path.read_bytes() + b"\n")
    config_path = _write_config(tmp_path, _config())

    with pytest.raises(protocol.ProtocolError, match="payload SHA-256 mismatch"):
        protocol.verify_protocol(config_path, repository_root=tmp_path)


def test_duplicate_selected_query_fails_even_with_consistent_source_hashes(
    tmp_path: Path,
) -> None:
    source = _copy_source(tmp_path)
    scenario_one = source / "corpus" / "scenarios" / "maze-128-128-1-random-1.scen"
    scenario_two = source / "corpus" / "scenarios" / "maze-128-128-1-random-2.scen"
    first_row = scenario_one.read_text(encoding="ascii").splitlines()[1]
    rows = scenario_two.read_text(encoding="ascii").splitlines()
    rows[1] = first_row
    scenario_two.write_text("\n".join(rows) + "\n", encoding="ascii", newline="\n")

    checksums = source / "SHA256SUMS"
    relative = "corpus/scenarios/maze-128-128-1-random-2.scen"
    _replace_checksum(checksums, relative, _sha256(scenario_two))
    value = _config()
    value["source_snapshot"]["checksums"]["sha256"] = _sha256(checksums)
    config_path = _write_config(tmp_path, value)

    with pytest.raises(protocol.ProtocolError, match="duplicate map/start/goal"):
        protocol.verify_protocol(config_path, repository_root=tmp_path)


def test_bound_manifest_hash_tampering_fails_closed(tmp_path: Path) -> None:
    value = copy.deepcopy(_config())
    value["source_snapshot"]["manifest"]["sha256"] = "0" * 64
    path = _write_config(tmp_path, value)
    with pytest.raises(protocol.ProtocolError, match="manifest SHA-256"):
        protocol.verify_protocol(path, repository_root=REPOSITORY_ROOT)
