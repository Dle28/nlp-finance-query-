"""Leakage-resistant self-supervised curriculum from normalized BCTC tables.

The competition provides no train/dev labels.  This module therefore derives
retrieval triplets and structured-program examples only from financial tables.
It never reads the 1,012 competition questions.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
import hashlib
import json
from pathlib import Path
import re
from typing import Any


YEAR_RE = re.compile(r"(?:19|20)\d{2}")
NUMBER_RE = re.compile(r"^\(?[-+]?\d[\d.,\s]*\)?%?$")
SCOPE_RE = re.compile(r"_(separate|consolidated|aggregated)(?:_|$)", re.IGNORECASE)
QUERY_TEMPLATES = (
    "{metric} của {ticker} năm {year} là bao nhiêu?",
    "Cho biết {metric} tại {ticker} trong kỳ {year}.",
    "Tra cứu {metric} của doanh nghiệp {ticker} năm {year}.",
    "Giá trị {metric} trong báo cáo {scope} của {ticker} năm {year} là bao nhiêu?",
    "Ở kỳ báo cáo {year}, {ticker} ghi nhận {metric} bằng bao nhiêu?",
    "Tìm số liệu {metric} của {ticker} cho năm {year}.",
)


def _read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _is_number(text: Any) -> bool:
    value = _normalize(text)
    return bool(value and NUMBER_RE.match(value) and any(ch.isdigit() for ch in value))


def _document_fields(document_id: str) -> tuple[str, int | None, str]:
    ticker = document_id.split("_", 1)[0].upper()
    years = YEAR_RE.findall(document_id)
    year = int(years[-1]) if years else None
    scope_match = SCOPE_RE.search(document_id)
    scope = scope_match.group(1).lower() if scope_match else "unspecified"
    return ticker, year, scope


def _split_for_ticker(ticker: str, seed: int, train_percent: int, validation_percent: int) -> str:
    bucket = int(hashlib.sha256(f"{seed}:{ticker}".encode()).hexdigest()[:8], 16) % 100
    if bucket < train_percent:
        return "train"
    if bucket < train_percent + validation_percent:
        return "validation"
    return "test"


def _row_label(row: Sequence[Any]) -> str | None:
    parts: list[str] = []
    for cell in row[:3]:
        value = _normalize(cell)
        if value and not _is_number(value):
            parts.append(value)
    label = " ".join(parts).strip(" -:")
    if len(label) < 3 or not any(ch.isalpha() for ch in label):
        return None
    return label


def _meaningful_labels(table: Mapping[str, Any]) -> list[str]:
    labels: list[str] = []
    for row in table.get("rows") or []:
        label = _row_label(row)
        if label and any(_is_number(cell) for cell in row[1:]):
            labels.append(label)
    return list(dict.fromkeys(labels))


def _semantic_passage(table: Mapping[str, Any], max_labels: int) -> str:
    labels = _meaningful_labels(table)[:max_labels]
    headers = [_normalize(value) for value in table.get("column_labels") or [] if _normalize(value)]
    parts = [
        f"tài liệu: {table.get('document_id', '')}",
        f"mục: {_normalize(table.get('table_section'))}",
        f"mục đích: {_normalize(table.get('table_purpose'))}",
        f"header: {' | '.join(headers[:12])}",
        f"chỉ tiêu: {' | '.join(labels)}",
    ]
    return "passage: " + " ; ".join(part for part in parts if not part.endswith(": "))


def _token_set(text: str) -> set[str]:
    return {token for token in re.findall(r"\w+", text.lower(), flags=re.UNICODE) if len(token) > 1}


def _jaccard(left: str, right: str) -> float:
    a, b = _token_set(left), _token_set(right)
    return len(a & b) / max(1, len(a | b))


def _year_columns(table: Mapping[str, Any]) -> dict[int, int]:
    result: dict[int, int] = {}
    rows = table.get("rows") or []
    for row in rows[:4]:
        for column, cell in enumerate(row):
            for year in YEAR_RE.findall(_normalize(cell)):
                result.setdefault(int(year), column)
    return result


def _program_messages(
    *, document_id: str, ticker: str, year: int | None, scope: str, metric: str,
    table: Mapping[str, Any], operation: str, periods: Sequence[int],
) -> list[dict[str, str]]:
    if operation == "lookup":
        question = f"{metric} của {ticker} năm {year} là bao nhiêu?"
    elif operation == "subtract":
        question = f"{metric} của {ticker} thay đổi bao nhiêu từ năm {periods[0]} đến năm {periods[1]}?"
    else:
        question = f"Tốc độ tăng trưởng {metric} của {ticker} từ năm {periods[0]} đến năm {periods[1]} là bao nhiêu phần trăm?"
    schema = {
        "document_id": document_id,
        "scope": scope,
        "columns": (table.get("column_labels") or [])[:12],
        "row_labels": _meaningful_labels(table)[:24],
    }
    answer = {
        "operation": operation,
        "operands": [
            {"metric": metric, "period": period, "scope": scope}
            for period in periods
        ],
        "requested_unit": "from_question",
    }
    return [
        {
            "role": "system",
            "content": (
                "Bạn là bộ lập kế hoạch Text-to-Pandas. Chỉ xuất JSON AST; "
                "không đoán số và không tạo nguồn ngoài schema."
            ),
        },
        {
            "role": "user",
            "content": json.dumps({"question": question, "schema": schema}, ensure_ascii=False),
        },
        {"role": "assistant", "content": json.dumps(answer, ensure_ascii=False, sort_keys=True)},
    ]


def build_curriculum(
    *, tables_path: Path, output_dir: Path, config: Mapping[str, Any], max_tables: int | None = None,
) -> dict[str, Any]:
    """Build retrieval triplets, reranker pairs, and AST SFT messages."""
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite: {output_dir}")
    source_config = config["source"]
    observed_sha = _sha256(tables_path)
    expected_sha = str(source_config.get("expected_sha256") or "")
    if max_tables is None and expected_sha and observed_sha != expected_sha:
        raise ValueError(f"source sha256 mismatch: {observed_sha} != {expected_sha}")

    tables = list(_read_jsonl(tables_path))
    if max_tables is not None:
        tables = tables[:max_tables]
    elif len(tables) != int(source_config["expected_table_count"]):
        raise ValueError(f"table count mismatch: {len(tables)}")
    output_dir.mkdir(parents=True)

    curriculum = config["curriculum"]
    split_config = config["split"]
    seed = int(split_config["seed"])
    max_rows = int(curriculum["max_rows_per_table"])
    max_labels = int(curriculum["max_row_labels_in_passage"])

    prepared: list[dict[str, Any]] = []
    by_document: dict[str, list[int]] = defaultdict(list)
    by_ticker_year: dict[tuple[str, int | None], list[int]] = defaultdict(list)
    for table in tables:
        document_id = str(table.get("document_id") or "")
        uid = str(table.get("internal_table_uid") or "")
        ticker, year, scope = _document_fields(document_id)
        labels = _meaningful_labels(table)
        if not uid or not labels:
            continue
        item = {
            "table": table,
            "uid": uid,
            "document_id": document_id,
            "ticker": ticker,
            "year": year,
            "scope": scope,
            "labels": labels,
            "passage": _semantic_passage(table, max_labels),
            "split": _split_for_ticker(
                ticker, seed, int(split_config["train_percent"]), int(split_config["validation_percent"])
            ),
        }
        index = len(prepared)
        prepared.append(item)
        by_document[document_id].append(index)
        by_ticker_year[(ticker, year)].append(index)

    retrieval_files = {
        split: (output_dir / f"retrieval_triplets_{split}.jsonl").open("w", encoding="utf-8")
        for split in ("train", "validation", "test")
    }
    reranker_files = {
        split: (output_dir / f"reranker_pairs_{split}.jsonl").open("w", encoding="utf-8")
        for split in ("train", "validation", "test")
    }
    sft_files = {
        split: (output_dir / f"program_sft_{split}.jsonl").open("w", encoding="utf-8")
        for split in ("train", "validation", "test")
    }
    counts: dict[str, dict[str, int]] = {
        kind: {split: 0 for split in ("train", "validation", "test")}
        for kind in ("retrieval_triplets", "reranker_pairs", "program_sft")
    }
    split_tickers: dict[str, set[str]] = defaultdict(set)

    try:
        for item_index, item in enumerate(prepared):
            split = item["split"]
            split_tickers[split].add(item["ticker"])
            negative_pool = [
                idx for idx in by_document[item["document_id"]] if idx != item_index
            ] or [
                idx for idx in by_ticker_year[(item["ticker"], item["year"])] if idx != item_index
            ]
            if not negative_pool:
                continue
            for metric_index, metric in enumerate(item["labels"][:max_rows]):
                scored_negatives = sorted(
                    (
                        (_jaccard(metric, " ".join(prepared[idx]["labels"])), idx)
                        for idx in negative_pool
                    ),
                    key=lambda pair: (pair[0], prepared[pair[1]]["uid"]),
                    reverse=True,
                )
                eligible = [pair for pair in scored_negatives if pair[0] < 0.85]
                negative = prepared[(eligible or scored_negatives)[0][1]]
                template = QUERY_TEMPLATES[
                    int(hashlib.sha256(f"{item['uid']}:{metric_index}".encode()).hexdigest()[:8], 16)
                    % len(QUERY_TEMPLATES)
                ]
                query = template.format(
                    metric=metric, ticker=item["ticker"], year=item["year"] or "không rõ",
                    scope=item["scope"],
                )
                record_id = hashlib.sha256(f"{item['uid']}:{metric}".encode()).hexdigest()
                triplet = {
                    "id": record_id,
                    "split": split,
                    "anchor": f"query: {query}",
                    "positive": item["passage"],
                    "negative": negative["passage"],
                    "positive_uid": item["uid"],
                    "negative_uid": negative["uid"],
                    "ticker": item["ticker"],
                    "report_year": item["year"],
                }
                retrieval_files[split].write(json.dumps(triplet, ensure_ascii=False) + "\n")
                counts["retrieval_triplets"][split] += 1
                for label, passage, uid in (
                    (1.0, item["passage"], item["uid"]),
                    (0.0, negative["passage"], negative["uid"]),
                ):
                    pair = {
                        "id": f"{record_id}:{int(label)}",
                        "query": f"query: {query}",
                        "passage": passage,
                        "label": label,
                        "table_uid": uid,
                    }
                    reranker_files[split].write(json.dumps(pair, ensure_ascii=False) + "\n")
                    counts["reranker_pairs"][split] += 1

                lookup = {
                    "id": f"{record_id}:lookup",
                    "messages": _program_messages(
                        document_id=item["document_id"], ticker=item["ticker"], year=item["year"],
                        scope=item["scope"], metric=metric, table=item["table"], operation="lookup",
                        periods=[item["year"]] if item["year"] else [],
                    ),
                }
                sft_files[split].write(json.dumps(lookup, ensure_ascii=False) + "\n")
                counts["program_sft"][split] += 1

                year_columns = sorted(_year_columns(item["table"]))
                if len(year_columns) >= 2 and metric_index == 0:
                    old_year, new_year = year_columns[-2], year_columns[-1]
                    for operation in ("subtract", "percentage_change"):
                        program = {
                            "id": f"{record_id}:{operation}",
                            "messages": _program_messages(
                                document_id=item["document_id"], ticker=item["ticker"], year=item["year"],
                                scope=item["scope"], metric=metric, table=item["table"], operation=operation,
                                periods=[old_year, new_year],
                            ),
                        }
                        sft_files[split].write(json.dumps(program, ensure_ascii=False) + "\n")
                        counts["program_sft"][split] += 1
    finally:
        for handle in (*retrieval_files.values(), *reranker_files.values(), *sft_files.values()):
            handle.close()

    ticker_sets = {split: sorted(values) for split, values in split_tickers.items()}
    overlaps = {
        "train_validation": sorted(set(ticker_sets.get("train", [])) & set(ticker_sets.get("validation", []))),
        "train_test": sorted(set(ticker_sets.get("train", [])) & set(ticker_sets.get("test", []))),
        "validation_test": sorted(set(ticker_sets.get("validation", [])) & set(ticker_sets.get("test", []))),
    }
    if any(overlaps.values()):
        raise AssertionError(f"ticker leakage detected: {overlaps}")
    manifest = {
        "protocol": "vifinqa_self_supervised_curriculum_v1",
        "source_path": str(tables_path),
        "source_sha256": observed_sha,
        "source_table_count_read": len(tables),
        "eligible_table_count": len(prepared),
        "counts": counts,
        "split_tickers": ticker_sets,
        "split_ticker_overlap": overlaps,
        "competition_questions_read": 0,
        "seed": seed,
        "max_tables_smoke": max_tables,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest
