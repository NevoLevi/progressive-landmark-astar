#!/usr/bin/env python3
"""Run one immutable progressive-landmarks experiment split."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PYTHON_SOURCE = REPOSITORY_ROOT / "src" / "python"
if str(PYTHON_SOURCE) not in sys.path:
    sys.path.insert(0, str(PYTHON_SOURCE))

from progressive_landmarks.protocol import ProtocolError  # noqa: E402
from progressive_landmarks.runner import RunnerError, run_split  # noqa: E402


DEFAULT_CONFIG = REPOSITORY_ROOT / "configs" / "progressive_landmarks_v2.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--split",
        choices=("development", "sealed_evaluation"),
        required=True,
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--repository-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument(
        "--freeze-manifest",
        type=Path,
        help="external authorization required for sealed_evaluation",
    )
    parser.add_argument(
        "--development-smoke",
        action="store_true",
        help="mark a development run as non-formal",
    )
    parser.add_argument(
        "--max-queries",
        type=int,
        help="deterministic prefix cap; valid only with --development-smoke",
    )
    args = parser.parse_args(argv)
    try:
        output = run_split(
            args.config,
            args.output,
            repository_root=args.repository_root,
            experiment_split=args.split,
            freeze_manifest=args.freeze_manifest,
            development_smoke=args.development_smoke,
            max_queries=args.max_queries,
        )
    except (OSError, ProtocolError, RunnerError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"published immutable {args.split} result: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
