#!/usr/bin/env python3
"""Compile recognised questions into metric-registry retrieval route plans."""
from __future__ import annotations

import argparse
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
    STAGE_ROUTING_VERSION,
    build_staged_route_plan,
)
from finance_query.table_structure import sha256_file  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--questions", type=Path, required=True)
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
    questions = load_jsonl(args.questions.resolve())
    rows = []
    for item in questions:
        plan = item.get("question_plan") or {}
        route = build_staged_route_plan(
            str(item.get("question") or ""),
            scope=str(plan.get("scope") or "") or None,
        )
        rows.append({"id": int(item["id"]), "question": item.get("question"), "route": route})
    output = args.output.resolve()
    atomic_write_jsonl(output, rows)
    manifest = {
        "schema_version": STAGE_ROUTING_VERSION,
        "protocol": STAGE_ROUTING_PROTOCOL,
        "questions_sha256": sha256_file(args.questions.resolve()),
        "question_count": len(rows),
        "planned_count": sum(row["route"]["routing_status"] == "planned" for row in rows),
        "abstain_count": sum(row["route"]["routing_status"] == "abstain" for row in rows),
        "submission_eligible": False,
        "sidecar_sha256": sha256_file(output),
    }
    output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
