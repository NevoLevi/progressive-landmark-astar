#!/usr/bin/env python3
"""Verify the frozen progressive-landmarks corpus and emit its exact plan."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PYTHON_SOURCE = REPOSITORY_ROOT / "src" / "python"
if str(PYTHON_SOURCE) not in sys.path:
    sys.path.insert(0, str(PYTHON_SOURCE))

from progressive_landmarks.protocol import ProtocolError, verify_protocol  # noqa: E402


DEFAULT_CONFIG = REPOSITORY_ROOT / "configs" / "progressive_landmarks_v2.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--repository-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument(
        "--output",
        type=Path,
        help="write canonical plan JSON to this path (stdout when omitted)",
    )
    args = parser.parse_args(argv)
    try:
        plan = verify_protocol(
            args.config.resolve(), repository_root=args.repository_root.resolve()
        )
    except ProtocolError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    rendered = (
        json.dumps(plan, ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2)
        + "\n"
    )
    if args.output is None:
        sys.stdout.write(rendered)
    else:
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="ascii", newline="\n")
        except OSError as exc:
            print(f"ERROR: cannot write {args.output}: {exc}", file=sys.stderr)
            return 1
        print(
            f"verified 12 maps, 48 scenarios, 960 queries, 34560 planned runs; "
            f"plan_sha256={plan['plan_sha256']}; wrote {args.output}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
