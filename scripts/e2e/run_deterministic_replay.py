#!/usr/bin/env python3
"""Chạy luồng E2E xác định của ViFinQA từ manifest hash-bound."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.e2e import (  # noqa: E402
    load_deterministic_replay_inputs,
    run_deterministic_replay,
    summarize_deterministic_replay,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Phải là thư mục mới; hệ thống từ chối ghi đè artifact cũ.",
    )
    args = parser.parse_args()
    result = run_deterministic_replay(
        load_deterministic_replay_inputs(args.config.resolve()),
        output_dir=args.output_dir.resolve(),
    )
    print(
        json.dumps(
            summarize_deterministic_replay(result, output_dir=args.output_dir.resolve()),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
