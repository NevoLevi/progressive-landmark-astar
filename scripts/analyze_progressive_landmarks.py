#!/usr/bin/env python3
"""Validate and analyze the immutable progressive-landmarks sealed evaluation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PYTHON_SOURCE = REPOSITORY_ROOT / "src" / "python"
if str(PYTHON_SOURCE) not in sys.path:
    sys.path.insert(0, str(PYTHON_SOURCE))

from progressive_landmarks.analysis import (  # noqa: E402
    AnalysisError,
    analyze_sealed_evaluation,
)
from progressive_landmarks.development_gate import DevelopmentGateError  # noqa: E402
from progressive_landmarks.protocol import ProtocolError  # noqa: E402
from progressive_landmarks.runner import RunnerError  # noqa: E402


DEFAULT_CONFIG = REPOSITORY_ROOT / "configs" / "progressive_landmarks_v2.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Replay every correctness gate, validate the v2 development freeze/audit, "
            "and atomically publish the prospective sealed-evaluation analysis."
        )
    )
    parser.add_argument("sealed_evaluation_result", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--repository-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--freeze-manifest", required=True, type=Path)
    parser.add_argument("--development-audit", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        output = analyze_sealed_evaluation(
            arguments.sealed_evaluation_result,
            arguments.output,
            config_path=arguments.config,
            repository_root=arguments.repository_root,
            freeze_manifest=arguments.freeze_manifest,
            development_audit=arguments.development_audit,
        )
    except (
        AnalysisError,
        DevelopmentGateError,
        ProtocolError,
        RunnerError,
        OSError,
        ValueError,
    ) as error:
        print(
            json.dumps(
                {
                    "schema": "progressive-landmarks-analysis-error-v2",
                    "status": "failed",
                    "error": str(error),
                },
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            {
                "schema": "progressive-landmarks-analysis-published-v2",
                "status": "passed",
                "output": str(output),
            },
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
