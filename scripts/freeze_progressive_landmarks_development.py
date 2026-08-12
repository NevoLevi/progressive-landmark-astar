#!/usr/bin/env python3
"""Audit formal development evidence and authorize sealed evaluation once."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PYTHON_SOURCE = REPOSITORY_ROOT / "src" / "python"
if str(PYTHON_SOURCE) not in sys.path:
    sys.path.insert(0, str(PYTHON_SOURCE))

from progressive_landmarks.development_gate import (
    DevelopmentGateError,
    freeze_formal_development,
)
from progressive_landmarks.runner import RunnerError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fail-closed external audit of an immutable formal progressive-landmarks "
            "development result; writes a detailed audit and sealed-evaluation freeze."
        )
    )
    parser.add_argument("development_result", type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--audit-output", required=True, type=Path)
    parser.add_argument("--freeze-output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = freeze_formal_development(
            arguments.development_result,
            config_path=arguments.config,
            repository_root=arguments.repository_root,
            audit_path=arguments.audit_output,
            freeze_path=arguments.freeze_output,
        )
    except (DevelopmentGateError, RunnerError, OSError, ValueError) as error:
        print(
            json.dumps(
                {
                    "schema": "progressive-landmarks-development-gate-error-v2",
                    "status": "failed",
                    "error": str(error),
                },
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "schema": result.audit["schema"],
                "status": "passed",
                "audit_output": str(arguments.audit_output),
                "freeze_output": str(arguments.freeze_output),
                "audit_sha256": result.audit["audit_sha256"],
            },
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
