#!/usr/bin/env python3
"""Build an immutable review bundle from an already-built ViFinQA retriever.

This script is intended to run on Kaggle after heavy artifacts are available.
It does NOT train models. It performs retrieval, resolves every candidate UID to
its stored table rows/context, derives a faithful one-line review summary from
those exact rows, and writes a bundle that can be reviewed offline/local.

Important design rule: ``direct_evidence`` is constructed directly from source
cells. The script never invents a semantic table description with an LLM.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from finance_query.config import ProjectPaths
from finance_query.pipeline import ViFinQARetrievalPipeline, load_config


TOKEN_RE = re.compile(r"[\w%]+", re.UNICODE)
DIGIT_RE = re.compile(r"\d")
LETTER_RE = re.compile(r"[A-Za-zÀ-ỹ]", re.UNICODE)
NUMERIC_ONLY_RE = re.compile(r"^[\s()\-+\d.,%/]+$")

STOPWORDS = {
    "cua", "của", "la", "là", "bao", "nhieu", "nhiêu", "nam", "năm",
    "cong", "công", "ty", "vao", "vào", "cuoi", "cuối", "dau", "đầu",
    "tai", "tại", "trong", "dong", "đồng", "trieu", "triệu", "ty", "tỷ",
    "ngay", "ngày", "thang", "tháng", "den", "đến", "mot", "một", "cac",
    "các", "va", "và", "cho", "theo", "bao_nhieu",
}

GENERIC_LABELS = {
    "mã số", "ma so", "thuyết minh", "thuyet minh", "vnd", "vnđ", "đơn vị",
    "don vi", "chỉ tiêu", "chi tieu", "số cuối năm", "so cuoi nam",
    "số đầu năm", "so dau nam", "năm nay", "nam nay", "năm trước", "nam truoc",
}


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--questions",
        type=Path,
        default=Path("data/labels/annotation_questions_60.jsonl"),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/annotation_baseline.yaml"),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--no-dense", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--allow-errors", action="store_true")
    parser.add_argument("--min-asset-count", type=int, default=100_000)
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL: {path}:{line_number}") from exc
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def count_jsonl(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open("r", encoding="utf-8") as file:
        return sum(1 for line in file if line.strip())


# ---------------------------------------------------------------------------
# Integrity gate
# ---------------------------------------------------------------------------


def q13_oracle_exists(assets_path: Path) -> bool:
    if not assets_path.is_file():
        return False
    with assets_path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            row = json.loads(line)
            if (
                row.get("ticker") == "SAB"
                and row.get("report_year") == 2016
                and row.get("scope") == "separate"
                and "tiền và các khoản tương đương tiền"
                    in str(row.get("search_text") or "").casefold()
            ):
                return True
    return False


def inspect_artifacts(repo_root: Path, min_asset_count: int, require_dense: bool) -> dict[str, Any]:
    artifacts = repo_root / "artifacts"
    assets = artifacts / "table_assets.jsonl"
    lexical = artifacts / "lexical_index.sqlite3"
    dense_uids = artifacts / "dense_uids.jsonl"
    dense_index = artifacts / "dense.index"

    asset_count = count_jsonl(assets)
    uid_count = count_jsonl(dense_uids)

    lexical_count = 0
    if lexical.is_file():
        connection = sqlite3.connect(lexical)
        try:
            lexical_count = int(connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0])
        finally:
            connection.close()

    dense_ntotal = 0
    if dense_index.is_file():
        try:
            import faiss
            dense_ntotal = int(faiss.read_index(str(dense_index)).ntotal)
        except Exception as exc:  # pragma: no cover - environment-specific
            raise RuntimeError(f"Cannot inspect FAISS index: {dense_index}") from exc

    valid_base = (
        asset_count >= min_asset_count
        and asset_count == lexical_count
        and q13_oracle_exists(assets)
    )
    valid_dense = (
        uid_count == asset_count
        and dense_ntotal == asset_count
    ) if require_dense else True

    return {
        "asset_count": asset_count,
        "lexical_count": lexical_count,
        "dense_uid_count": uid_count,
        "dense_ntotal": dense_ntotal,
        "q13_oracle": q13_oracle_exists(assets),
        "valid_base": valid_base,
        "valid_dense": valid_dense,
        "valid": valid_base and valid_dense,
    }


# ---------------------------------------------------------------------------
# Faithful table projection
# ---------------------------------------------------------------------------


def normalize_tokens(text: str) -> set[str]:
    return {
        token.casefold()
        for token in TOKEN_RE.findall(text)
        if len(token) >= 2 and token.casefold() not in STOPWORDS
    }


def metric_from_plan(plan: dict[str, Any]) -> str:
    operands = plan.get("operands") or []
    if not operands:
        return ""
    return str(operands[0].get("metric") or "")


def row_has_numeric(row: list[str]) -> bool:
    return any(DIGIT_RE.search(str(cell)) for cell in row[1:])


def row_features(
    row: list[str],
    metric_tokens: set[str],
    question_tokens: set[str],
) -> dict[str, Any]:
    text = " ".join(str(cell) for cell in row)
    tokens = normalize_tokens(text)
    metric_overlap = (
        len(tokens & metric_tokens) / max(1, len(metric_tokens))
        if metric_tokens else 0.0
    )
    question_overlap = (
        len(tokens & question_tokens) / max(1, len(question_tokens))
        if question_tokens else 0.0
    )
    numeric = row_has_numeric(row)
    score = 0.72 * metric_overlap + 0.18 * question_overlap + (0.10 if numeric else 0.0)
    return {
        "metric_overlap": metric_overlap,
        "question_overlap": question_overlap,
        "numeric": numeric,
        "score": score,
    }


def direct_row_text(row: list[str], max_chars: int = 520) -> str:
    text = " | ".join(str(cell).strip() for cell in row if str(cell).strip())
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def textual_cells(row: list[str]) -> list[str]:
    values: list[str] = []
    for cell in row:
        text = re.sub(r"\s+", " ", str(cell)).strip()
        if not text or NUMERIC_ONLY_RE.fullmatch(text) or not LETTER_RE.search(text):
            continue
        if text.casefold() in GENERIC_LABELS:
            continue
        values.append(text)
    return values


def choose_topic(rows: list[list[str]], best_row_index: int | None, metric_tokens: set[str]) -> str:
    candidates: list[tuple[float, int, str]] = []

    indices = list(range(min(10, len(rows))))
    if best_row_index is not None:
        indices.extend(
            i for i in range(max(0, best_row_index - 2), min(len(rows), best_row_index + 3))
            if i not in indices
        )

    for index in indices:
        labels = textual_cells(rows[index])
        if not labels:
            continue
        label = " / ".join(labels[:2])
        tokens = normalize_tokens(label)
        overlap = len(tokens & metric_tokens) / max(1, len(metric_tokens)) if metric_tokens else 0.0
        # Prefer metric-specific labels, then early structural headers.
        score = 0.78 * overlap + 0.22 * (1.0 / (1 + index))
        candidates.append((score, index, label))

    if not candidates:
        return "Không xác định được tiêu đề từ các cell văn bản"

    candidates.sort(key=lambda item: (item[0], -item[1]), reverse=True)
    return candidates[0][2][:260]


def project_table_for_question(
    rows: list[list[str]],
    question: str,
    plan: dict[str, Any],
) -> dict[str, Any]:
    metric = metric_from_plan(plan)
    metric_tokens = normalize_tokens(metric)
    question_tokens = normalize_tokens(question)

    scored_rows: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        features = row_features(row, metric_tokens, question_tokens)
        scored_rows.append({"index": index, "row": row, **features})

    scored_rows.sort(key=lambda item: item["score"], reverse=True)
    best = scored_rows[0] if scored_rows else None
    best_index = int(best["index"]) if best is not None else None

    if best_index is None:
        window: list[dict[str, Any]] = []
        evidence = "Không có structured row trong table payload"
    else:
        start = max(0, best_index - 2)
        stop = min(len(rows), best_index + 3)
        window = [{"index": i, "row": rows[i]} for i in range(start, stop)]
        evidence = direct_row_text(rows[best_index])

    topic = choose_topic(rows, best_index, metric_tokens)
    summary = f"Chủ đề gợi ý từ bảng: {topic}. Dòng gần query: {evidence}"
    summary = re.sub(r"\s+", " ", summary).strip()[:900]

    return {
        "table_topic": topic,
        "one_line_summary": summary,
        "direct_evidence": evidence,
        "best_row_index": best_index,
        "evidence_window": window,
        "evidence_features": {
            "metric_overlap": float(best.get("metric_overlap", 0.0)) if best else 0.0,
            "question_overlap": float(best.get("question_overlap", 0.0)) if best else 0.0,
            "numeric": bool(best.get("numeric", False)) if best else False,
            "row_score": float(best.get("score", 0.0)) if best else 0.0,
        },
    }


def metadata_features(plan: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    tickers = [str(value).casefold() for value in (plan.get("tickers") or [])]
    document_id = str(candidate.get("document_id") or "").casefold()
    ticker_match = not tickers or any(ticker in document_id for ticker in tickers)

    scope = plan.get("scope")
    scope_match = not scope or candidate.get("scope") == scope

    years = {int(value) for value in (plan.get("years") or []) if str(value).isdigit()}
    year_match = not years or candidate.get("report_year") in years

    score = (
        (0.40 if ticker_match else 0.0)
        + (0.30 if scope_match else 0.0)
        + (0.30 if year_match else 0.0)
    )
    return {
        "ticker_match": ticker_match,
        "scope_match": scope_match,
        "year_match": year_match,
        "metadata_score": score,
    }


# ---------------------------------------------------------------------------
# Bundle builder
# ---------------------------------------------------------------------------


def candidate_record(
    candidate: dict[str, Any],
    rank: int,
    asset: dict[str, Any],
    question: str,
    plan: dict[str, Any],
) -> dict[str, Any]:
    try:
        rows = json.loads(asset.get("rows_json") or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid rows_json for UID={candidate['internal_table_uid']}") from exc

    projection = project_table_for_question(rows, question, plan)
    meta = metadata_features(plan, candidate)

    return {
        "rank": rank,
        "internal_table_uid": candidate["internal_table_uid"],
        "document_id": candidate.get("document_id"),
        "ticker": candidate.get("ticker"),
        "report_year": candidate.get("report_year"),
        "scope": candidate.get("scope"),
        "lexical_rank": candidate.get("lexical_rank"),
        "dense_rank": candidate.get("dense_rank"),
        "fused_score": float(candidate.get("fused_score", 0.0)),
        "preview": candidate.get("preview"),
        **meta,
        **projection,
    }


def git_commit(repo_root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            text=True,
        ).strip()
    except Exception:
        return "unknown"


def main() -> None:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    output_dir = args.output_dir.resolve()

    if args.top_k <= 0:
        raise ValueError("--top-k must be > 0")

    if output_dir.exists() and any(output_dir.iterdir()):
        if not args.force:
            raise RuntimeError(
                f"Output directory is not empty: {output_dir}. Use --force to replace it."
            )
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    health = inspect_artifacts(
        repo_root,
        min_asset_count=args.min_asset_count,
        require_dense=not args.no_dense,
    )
    print("Artifact health:")
    print(json.dumps(health, ensure_ascii=False, indent=2))
    if not health["valid"]:
        raise RuntimeError("Artifact integrity gate FAILED; review bundle was not created.")

    questions = load_jsonl(args.questions)
    if not questions:
        raise ValueError(f"No questions: {args.questions}")

    paths = ProjectPaths.from_repository(repo_root)
    pipeline = ViFinQARetrievalPipeline(
        paths=paths,
        config=load_config(args.config),
        use_dense=not args.no_dense,
    )

    review_items: list[dict[str, Any]] = []
    tables: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []

    for position, row in enumerate(questions, start=1):
        question_id = int(row["id"])
        question = str(row["question"])
        print(f"[{position:03d}/{len(questions):03d}] Q{question_id}")

        try:
            result = pipeline.retrieve(question, question_id)
            plan = result["question_plan"]
            retrieved = result["retrieved_tables"][: args.top_k]
            candidate_rows: list[dict[str, Any]] = []

            for rank, candidate in enumerate(retrieved, start=1):
                uid = candidate["internal_table_uid"]
                asset = pipeline.store.get_asset(uid)
                if asset is None:
                    raise RuntimeError(f"Retrieved UID cannot be resolved: {uid}")

                asset_dict = dict(asset)
                record = candidate_record(candidate, rank, asset_dict, question, plan)
                candidate_rows.append(record)

                if uid not in tables:
                    try:
                        raw_rows = json.loads(asset_dict.get("rows_json") or "[]")
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"Invalid rows_json for UID={uid}") from exc
                    tables[uid] = {
                        "internal_table_uid": uid,
                        "document_id": candidate.get("document_id"),
                        "ticker": candidate.get("ticker"),
                        "report_year": candidate.get("report_year"),
                        "scope": candidate.get("scope"),
                        "context_before": asset_dict.get("context_before") or "",
                        "rows": raw_rows,
                    }

            review_items.append(
                {
                    "id": question_id,
                    "question": question,
                    "weak_family": row.get("weak_family"),
                    "question_plan": plan,
                    "candidate_count": len(candidate_rows),
                    "candidates": candidate_rows,
                }
            )
        except Exception as exc:
            error = {
                "id": question_id,
                "question": question,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            errors.append(error)
            print("ERROR:", error)
            if not args.allow_errors:
                write_jsonl(output_dir / "errors.jsonl", errors)
                raise

    review_items_path = output_dir / "review_items.jsonl"
    tables_path = output_dir / "tables.jsonl"
    errors_path = output_dir / "errors.jsonl"

    write_jsonl(review_items_path, review_items)
    write_jsonl(tables_path, tables.values())
    write_jsonl(errors_path, errors)

    config_path = args.config.resolve()
    questions_path = args.questions.resolve()
    manifest = {
        "schema_version": 2,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit(repo_root),
        "config_path": str(args.config),
        "config_sha256": sha256_file(config_path),
        "questions_path": str(args.questions),
        "questions_sha256": sha256_file(questions_path),
        "question_count": len(questions),
        "review_item_count": len(review_items),
        "unique_table_count": len(tables),
        "top_k": args.top_k,
        "use_dense": not args.no_dense,
        "artifact_health": health,
        "error_count": len(errors),
        "files": {},
    }

    checksummed = [review_items_path, tables_path, errors_path]
    for path in checksummed:
        manifest["files"][path.name] = {
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    sums_path = output_dir / "SHA256SUMS"
    lines = [
        f"{sha256_file(path)}  {path.name}"
        for path in [manifest_path, review_items_path, tables_path, errors_path]
    ]
    sums_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    archive_path = output_dir / "vifinqa_review_bundle.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tar:
        for path in [manifest_path, review_items_path, tables_path, errors_path, sums_path]:
            tar.add(path, arcname=path.name)

    archive_sha = sha256_file(archive_path)
    (output_dir / "vifinqa_review_bundle.tar.gz.sha256").write_text(
        f"{archive_sha}  {archive_path.name}\n",
        encoding="utf-8",
    )

    print("\nBundle ready:", output_dir)
    print("Questions:", len(review_items))
    print("Unique tables:", len(tables))
    print("Errors:", len(errors))
    print("Archive:", archive_path)
    print("Archive MiB:", round(archive_path.stat().st_size / 1024**2, 2))
    print("SHA256:", archive_sha)

    if errors and not args.allow_errors:
        raise RuntimeError("Bundle contains retrieval errors.")


if __name__ == "__main__":
    main()
