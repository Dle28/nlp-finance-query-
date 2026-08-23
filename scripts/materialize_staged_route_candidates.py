#!/usr/bin/env python3
"""Materialize metadata-filtered candidates and feedback for every route stage.

This is deliberately before semantic retrieval. It records which source
tables satisfy the document/table/variable contract and where a route must
stop. Dynamic stages remain ``awaiting_prior_stage`` until the deterministic
executor provides the entities selected by their predecessor.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.report_normalization import (  # noqa: E402
    STAGE_ROUTING_PROTOCOL,
    route_stage_candidates,
)
from finance_query.table_structure import sha256_file  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--routes", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def atomic_write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def main() -> None:
    args = parse_args()
    catalog = load_jsonl(args.catalog.resolve())
    routes = load_jsonl(args.routes.resolve())
    rows: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    for row in routes:
        route = dict(row.get("route") or {})
        stages = []
        for stage in route.get("stages") or []:
            result = route_stage_candidates(catalog, stage, scope=route.get("scope"))
            status_counts[str(result["status"])] += 1
            stages.append({"stage": stage, "candidate_result": result})
        rows.append(
            {
                "id": int(row["id"]),
                "routing_status": route.get("routing_status"),
                "family": route.get("family"),
                "stages": stages,
                "submission_eligible": False,
            }
        )
    output = args.output.resolve()
    atomic_write_jsonl(output, rows)
    manifest = {
        "schema_version": 1,
        "protocol": f"{STAGE_ROUTING_PROTOCOL}_candidate_materialization_v1",
        "catalog_sha256": sha256_file(args.catalog.resolve()),
        "routes_sha256": sha256_file(args.routes.resolve()),
        "question_count": len(rows),
        "stage_status_counts": dict(sorted(status_counts.items())),
        "submission_eligible": False,
        "sidecar_sha256": sha256_file(output),
    }
    output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
