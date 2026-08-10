#!/usr/bin/env python3
"""Emit a hash-bound read-only grounding-health dashboard."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from analyze_formula_evidence import validate_manifest as validate_formula_manifest  # noqa: E402
from finance_query.evaluation_dashboard import (  # noqa: E402
    EVALUATION_DASHBOARD_PROTOCOL,
    EVALUATION_DASHBOARD_VERSION,
    build_evaluation_dashboard,
)
from finance_query.ocr_quality import validate_ocr_quality_sidecar  # noqa: E402
from finance_query.semantic_catalog import validate_semantic_catalog_sidecar  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--machine-reviews", type=Path, required=True)
    parser.add_argument("--formula-evidence", type=Path, required=True)
    parser.add_argument("--ocr-quality", type=Path, default=None)
    parser.add_argument("--semantic-catalog", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def main() -> None:
    args = parse_args()
    bundle = args.bundle_dir.resolve()
    reviews = args.machine_reviews.resolve()
    formula = args.formula_evidence.resolve()
    structure = bundle / "tables_structured_v2.jsonl"
    context = bundle / "tables_evidence_context_v3.jsonl"
    quality = (args.ocr_quality or bundle / "ocr_quality_profiles_v1.jsonl").resolve()
    catalog = (args.semantic_catalog or bundle / "semantic_catalog_v1.jsonl").resolve()
    validate_formula_manifest(bundle, formula)
    validate_ocr_quality_sidecar(bundle, structure, context, quality)
    validate_semantic_catalog_sidecar(bundle, structure, context, bundle / "report_segments_v1.jsonl", catalog)
    dashboard = build_evaluation_dashboard(
        load_jsonl(reviews), load_jsonl(formula), load_jsonl(catalog), load_jsonl(quality)
    )
    dashboard["inputs"] = {
        "machine_reviews": {"path": str(reviews), "sha256": sha256_file(reviews)},
        "formula_evidence": {"path": str(formula), "sha256": sha256_file(formula)},
        "ocr_quality_profile": {"path": str(quality), "sha256": sha256_file(quality)},
        "semantic_catalog": {"path": str(catalog), "sha256": sha256_file(catalog)},
    }
    if dashboard["evaluation_dashboard_version"] != EVALUATION_DASHBOARD_VERSION or dashboard["protocol"] != EVALUATION_DASHBOARD_PROTOCOL:
        raise RuntimeError("Dashboard protocol mismatch")
    output = args.output.resolve()
    atomic_json(output, dashboard)
    print(json.dumps({"output": str(output), "question_count": dashboard["question_count"], "status_counts": dashboard["status_counts"], "join_diagnostics": dashboard["join_diagnostics"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
