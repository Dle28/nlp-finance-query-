#!/usr/bin/env python3
"""Score review-bundle candidates with a local cross-encoder, without relabeling.

The output is a sidecar: each score is tied to one question, candidate UID and
deterministic canonical source input.  It cannot become a label on its own.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.semantic_rerank import (  # noqa: E402
    SEMANTIC_RERANK_SCHEMA_VERSION,
    SEMANTIC_INPUT_RENDERER_VERSION,
    semantic_candidate_input,
    semantic_input_digest,
)
from finance_query.table_structure import validate_structure_sidecar  # noqa: E402
from finance_query.evidence_context import validate_evidence_context_sidecar  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--evidence-context",
        type=Path,
        default=None,
        help="Canonical context sidecar; defaults to tables_evidence_context_v3.jsonl in the bundle.",
    )
    parser.add_argument("--model", default="BAAI/bge-reranker-v2-m3")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--max-length", type=int, default=512)
    parser.add_argument("--top-candidates", type=int, default=40)
    parser.add_argument("--family", action="append", default=[])
    parser.add_argument("--local-files-only", action="store_true")
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as file:
        return [json.loads(line) for line in file if line.strip()]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)


def main() -> None:
    args = parse_args()
    if args.batch_size < 1 or args.max_length < 32 or args.top_candidates < 1:
        raise ValueError("batch-size/top-candidates must be positive and max-length must be at least 32")
    bundle = args.bundle_dir.resolve()
    structured = bundle / "tables_structured_v2.jsonl"
    contexts_path = (args.evidence_context or bundle / "tables_evidence_context_v3.jsonl").resolve()
    if contexts_path.parent != bundle:
        raise ValueError("Semantic context sidecar must reside in the review bundle")
    validate_structure_sidecar(bundle, structured)
    validate_evidence_context_sidecar(bundle, structured, contexts_path)

    items = load_jsonl(bundle / "review_items.jsonl")
    tables = {str(row["internal_table_uid"]): row for row in load_jsonl(structured)}
    contexts = {str(row["internal_table_uid"]): row for row in load_jsonl(contexts_path)}
    requested_families = {str(value) for value in args.family if str(value)}

    rows: list[dict[str, Any]] = []
    pairs: list[tuple[str, str]] = []
    locations: list[tuple[int, int]] = []
    for item in items:
        family = str((item.get("question_plan") or {}).get("family") or item.get("weak_family") or "")
        scored: list[dict[str, Any]] = []
        if not requested_families or family in requested_families:
            for candidate in (item.get("candidates") or [])[: args.top_candidates]:
                uid = str(candidate.get("internal_table_uid") or "")
                table, context = tables.get(uid), contexts.get(uid)
                if table is None or context is None:
                    continue
                model_input = semantic_candidate_input(item["question"], candidate, table, context)
                # A semantic score is diagnostic evidence, not a fallback
                # retriever.  Do not score tables unless the candidate points
                # to one exact raw V2 row that is visible in its model input.
                if not model_input:
                    continue
                scored.append(
                    {
                        "internal_table_uid": uid,
                        "rank": int(candidate.get("rank") or 0),
                        "input_sha256": semantic_input_digest(item["question"], model_input),
                        "source_input": model_input,
                        "score": None,
                    }
                )
                pairs.append((str(item["question"]), model_input))
                locations.append((len(rows), len(scored) - 1))
        rows.append({"id": int(item["id"]), "family": family, "candidate_scores": scored})

    from sentence_transformers import CrossEncoder
    import torch

    model = CrossEncoder(
        args.model,
        device=args.device,
        max_length=args.max_length,
        activation_fn=torch.nn.Sigmoid(),
        local_files_only=args.local_files_only,
    )
    scores = model.predict(pairs, batch_size=args.batch_size, show_progress_bar=True)
    if len(scores) != len(locations):
        raise RuntimeError("Cross-encoder returned an unexpected score count")
    for score, (row_index, candidate_index) in zip(scores, locations, strict=True):
        rows[row_index]["candidate_scores"][candidate_index]["score"] = float(score)

    output = args.output.resolve()
    write_jsonl(output, rows)
    manifest = {
        "schema_version": SEMANTIC_RERANK_SCHEMA_VERSION,
        "input_renderer_version": SEMANTIC_INPUT_RENDERER_VERSION,
        "bundle_review_items_sha256": sha256_file(bundle / "review_items.jsonl"),
        "structured_tables_sha256": sha256_file(structured),
        "evidence_context_sha256": sha256_file(contexts_path),
        "evidence_context_file": contexts_path.name,
        "model": args.model,
        "device": args.device,
        "batch_size": args.batch_size,
        "max_length": args.max_length,
        "top_candidates": args.top_candidates,
        "families": sorted(requested_families),
        "question_count": len(rows),
        "pair_count": len(pairs),
        "sidecar_sha256": sha256_file(output),
    }
    # Keep the manifest adjacent to—and uniquely named after—its sidecar.  A
    # bundle can hold separate runs for direct lookup and later formula
    # families, so a fixed manifest name would silently overwrite provenance.
    manifest_path = output.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "manifest": str(manifest_path), "pairs": len(pairs)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
