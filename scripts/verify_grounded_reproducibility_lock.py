#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.reproducibility_lock import verify_grounded_lock


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    result = verify_grounded_lock(args.lock.resolve(), repository_root=args.repo_root.resolve())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
