#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.binding_conflict_workbench import build_binding_conflict_workbench


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a fail-closed exact binding conflict workbench")
    for name in (
        "execution", "execution_manifest", "bindings", "bindings_manifest",
        "period_packets", "period_manifest", "structured_tables",
        "evidence_context", "evidence_context_manifest",
    ):
        parser.add_argument("--" + name.replace("_", "-"), type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = build_binding_conflict_workbench(**vars(args))
    print(json.dumps({"status": result["status"], "counts": result["counts"], "manifest_path": result["manifest_path"]}, indent=2))


if __name__ == "__main__":
    main()
