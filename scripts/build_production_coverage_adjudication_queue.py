#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path
from finance_query.production_coverage_adjudication import build


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("bindings", "bindings-manifest", "execution", "execution-manifest", "period-packets", "period-manifest", "route-overlay", "route-overlay-manifest", "no-candidate-audit", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    print(build(**{key.replace("-", "_"): value for key, value in vars(args).items()})["manifest_path"])


if __name__ == "__main__":
    main()
