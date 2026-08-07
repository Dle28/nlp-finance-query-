#!/usr/bin/env python3
"""Diagnose a ViFinQA review bundle before auto/human annotation.

This script never writes gold labels. It verifies the exported bundle and flags:

- ADJACENT_CONTEXT_HIT: query evidence is in older context_before, not candidate rows;
- RETRIEVAL_RISK: direct-lookup Top-K does not strongly cover the core query concept;
- EVIDENCE_RISK: plausible table, but exported evidence is header/weak and a numeric
  neighbor is more useful;
- PLANNER_RISK: planner metric contains ticker/date/entity boilerplate;
- COMPLEX_FAMILY_REVIEW: multi-step family cannot be judged by one-table coverage;
- AMBIGUOUS_TOPK / LOOKS_REVIEWABLE.

Outputs are diagnostics only, not labels.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import shutil
import tarfile
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

TOKEN_RE = re.compile(r"[A-Za-zÀ-ỹ0-9%]+", re.UNICODE)

STOPWORDS = {
    "cua", "của", "la", "là", "bao", "nhieu", "nhiêu", "vao", "vào",
    "tai", "tại", "trong", "cho", "theo", "den", "đến", "ngay", "ngày",
    "thang", "tháng", "nam", "năm", "cuoi", "cuối", "va", "và", "mot", "một",
    "cac", "các", "duoc", "được", "co", "có", "dong", "đồng", "trieu", "triệu",
    "ty", "tỷ",
}
ENTITY_NOISE = {
    "cong", "công", "ty", "tong", "tổng", "co", "cổ", "phan", "phần", "me", "mẹ",
    "tnhh", "mtv", "tap", "tập", "doan", "đoàn", "ctcp",
}
HEADER_TERMS = {
    "nguyên giá", "hao mòn lũy kế", "hao mòn lũy kê", "giá trị còn lại",
    "số cuối năm", "số đầu năm", "năm nay", "năm trước", "mã số", "thuyết minh",
    "giá gốc", "giá trị có thể thu hồi", "thời gian quá hạn", "hạng mục",
}
END_ROW_LABELS = {"số cuối năm", "cuối năm", "31/12", "31-12", "năm nay"}
BEGIN_ROW_LABELS = {"số đầu năm", "đầu năm", "01/01", "1/1", "năm trước"}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--bundle-dir", type=Path)
    src.add_argument("--bundle-archive", type=Path)
    p.add_argument("--output-dir", type=Path, default=Path("data/diagnostics"))
    p.add_argument("--audit-size", type=int, default=12)
    p.add_argument("--force", action="store_true")
    return p.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    out = []
    with path.open("r", encoding="utf-8-sig") as f:
        for line_no, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL {path}:{line_no}") from exc
    return out


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text).casefold()).strip()


def toks(text: Any, drop_stop: bool = True) -> list[str]:
    values = [x.casefold() for x in TOKEN_RE.findall(str(text))]
    if drop_stop:
        values = [x for x in values if x not in STOPWORDS and len(x) >= 2]
    return values


def metric_from_plan(plan: dict[str, Any]) -> str:
    ops = plan.get("operands") or []
    return str(ops[0].get("metric") or "") if ops else ""


def core_question_text(item: dict[str, Any]) -> str:
    q = norm(item.get("question") or "")
    plan = item.get("question_plan") or {}
    tickers = [str(x).casefold() for x in plan.get("tickers") or [] if str(x).strip()]

    for ticker in tickers:
        q = re.sub(
            r"\bcủa\s+công\s+ty\s+mẹ\s+.*?\(\s*" + re.escape(ticker) + r"\s*\)"
            r"(?=\s+(?:vào|đến|tại|ngày|năm|cuối|đầu)|[,.?]|$)", " ", q, flags=re.I,
        )
        q = re.sub(
            r"\bcủa\s+[^,.?]{0,180}?\(\s*" + re.escape(ticker) + r"\s*\)"
            r"(?=\s+(?:vào|đến|tại|ngày|năm|cuối|đầu)|[,.?]|$)", " ", q, flags=re.I,
        )
        q = re.sub(r"\(\s*" + re.escape(ticker) + r"\s*\)", " ", q, flags=re.I)
        q = re.sub(
            r"\bcủa\s+công\s+ty\s+mẹ\s+.*?\b" + re.escape(ticker) +
            r"\b(?=\s+(?:vào|đến|tại|ngày|năm|cuối|đầu)|[,.?]|$)", " ", q, flags=re.I,
        )
        q = re.sub(
            r"\bcủa\s+[^,.?]{0,120}?\b" + re.escape(ticker) +
            r"\b(?=\s+(?:vào|đến|tại|ngày|năm|cuối|đầu)|[,.?]|$)", " ", q, flags=re.I,
        )

    q = re.sub(r"\b(?:vào|đến|tại)\s+cuối\s+năm\s+\d{4}\b", " ", q)
    q = re.sub(r"\b(?:vào|đến|tại)\s+đầu\s+năm\s+\d{4}\b", " ", q)
    q = re.sub(r"\b(?:vào|đến|tại)\s+ngày\s+\d{1,2}\s+tháng\s+\d{1,2}\s+năm\s+\d{4}\b", " ", q)
    q = re.sub(r"\bngày\s+\d{1,2}\s+tháng\s+\d{1,2}\s+năm\s+\d{4}\b", " ", q)
    q = re.sub(r"\blà\s+bao\s+nhiêu\s+(?:nghìn\s+)?(?:tỷ|triệu)?\s*đồng\b", " ", q)
    q = re.sub(r"[?.,;:]+", " ", q)
    return norm(q)


def ngrams(ts: list[str], lo: int = 2, hi: int = 4) -> list[str]:
    return [" ".join(ts[i:i+n]) for n in range(lo, hi + 1) for i in range(len(ts) - n + 1)]


def question_terms(item: dict[str, Any]) -> tuple[list[str], list[str]]:
    plan = item.get("question_plan") or {}
    tickers = {str(x).casefold() for x in plan.get("tickers") or []}
    years = {str(x) for x in plan.get("years") or []}
    qt = [x for x in toks(core_question_text(item)) if x not in tickers and x not in years and x not in ENTITY_NOISE]
    if len(qt) < 2:
        qt = [x for x in toks(item.get("question") or "") if x not in tickers and x not in years and x not in ENTITY_NOISE]
    return qt, ngrams(qt)


def context_tail(table: dict[str, Any], size: int = 300) -> str:
    return norm(table.get("context_before") or "")[-size:]


def earlier_context(table: dict[str, Any], size: int = 300) -> str:
    ctx = norm(table.get("context_before") or "")
    return ctx[:-size] if len(ctx) > size else ""


def table_text(table: dict[str, Any]) -> str:
    parts = [context_tail(table)]
    parts.extend(" ".join(str(x) for x in row) for row in table.get("rows") or [])
    return norm(" ".join(parts))


def coverage(text: str, qtokens: list[str], phrases: list[str], pw: dict[str, float], tw: dict[str, float]) -> tuple[float, list[str]]:
    text = norm(text)
    token_set = set(toks(text))
    mt = {x for x in qtokens if x in token_set}
    token_den = sum(tw.get(x, 1.0) for x in set(qtokens)) or 1.0
    token_cov = sum(tw.get(x, 1.0) for x in mt) / token_den
    mp = [p for p in phrases if p in text]
    phrase_den = sum(pw.get(p, 1.0) for p in set(phrases)) or 1.0
    phrase_cov = sum(pw.get(p, 1.0) for p in set(mp)) / phrase_den if phrases else 0.0
    return (0.58 * phrase_cov + 0.42 * token_cov if phrases else token_cov), mp


def numeric_row(row: list[Any]) -> bool:
    for cell in row[1:]:
        if len(re.sub(r"\D", "", str(cell))) >= 4:
            return True
    return False


def row_text(row: list[Any]) -> str:
    return " | ".join(str(x).strip() for x in row if str(x).strip())


def header_like(row: list[Any]) -> bool:
    if not row or numeric_row(row):
        return False
    joined = norm(row_text(row))
    return any(term in joined for term in HEADER_TERMS) or len(row) >= 2


def planner_warnings(item: dict[str, Any]) -> list[str]:
    plan = item.get("question_plan") or {}
    metric = metric_from_plan(plan)
    mt = toks(metric, drop_stop=False)
    tickers = {str(x).casefold() for x in plan.get("tickers") or []}
    years = {str(x) for x in plan.get("years") or []}
    out = []
    if any(x in tickers for x in mt): out.append("metric_contains_ticker")
    if any(x in years for x in mt): out.append("metric_contains_year")
    if sum(x in ENTITY_NOISE for x in mt) >= 2: out.append("metric_contains_entity_boilerplate")
    if sum(x in {"31", "12", "01", "tháng", "ngày", "năm"} for x in mt) >= 2: out.append("metric_contains_date_tokens")
    if metric and len(metric) > 0.65 * len(str(item.get("question") or "")): out.append("metric_is_too_close_to_full_question")
    return out


def suggest_numeric_neighbor(item: dict[str, Any], candidate: dict[str, Any], table: dict[str, Any], qt: list[str], phrases: list[str], pw: dict[str, float], tw: dict[str, float]) -> dict[str, Any] | None:
    rows = table.get("rows") or []
    if not rows: return None
    base = candidate.get("best_row_index")
    base = int(base) if isinstance(base, int) else 0
    q = norm(item.get("question") or "")
    wants_end = "cuối năm" in q or "31 tháng 12" in q or "31/12" in q or "đến ngày" in q or "tại ngày" in q
    wants_begin = "đầu năm" in q or "01 tháng 01" in q or "01/01" in q
    best = None
    for i, row in enumerate(rows):
        if not numeric_row(row): continue
        cov, _ = coverage(row_text(row), qt, phrases, pw, tw)
        label = norm(row[0] if row else "")
        period = 1.0 if ((wants_end and any(x in label for x in END_ROW_LABELS)) or (wants_begin and any(x in label for x in BEGIN_ROW_LABELS))) else 0.0
        proximity = 1.0 / (1.0 + abs(i - base))
        score = 0.35 * cov + 0.50 * period + 0.15 * proximity
        rec = {"row_index": i, "row": row, "text": row_text(row), "score": round(score, 6)}
        if best is None or rec["score"] > best["score"]: best = rec
    return best


def diagnose(item: dict[str, Any], tables: dict[str, dict[str, Any]]) -> dict[str, Any]:
    candidates = item.get("candidates") or []
    qt, all_phrases = question_terms(item)
    n = max(1, len(candidates))
    texts = [table_text(tables.get(str(c.get("internal_table_uid")), {})) for c in candidates]

    tdf = {t: sum(t in set(toks(text)) for text in texts) for t in set(qt)}
    tw = {t: 1.0 + math.log((n + 1) / (tdf[t] + 1)) for t in set(qt)}
    pdf = {p: sum(p in text for text in texts) for p in set(all_phrases)}
    pw = {p: (1.0 + math.log((n + 1) / (pdf[p] + 1))) * (1.0 + 0.15 * (len(p.split()) - 2)) for p in set(all_phrases)}
    phrases = sorted(set(all_phrases), key=lambda p: (pw[p], len(p.split())), reverse=True)[:10]

    rows = []
    for c, text in zip(candidates, texts):
        uid = str(c.get("internal_table_uid"))
        table = tables.get(uid) or {}
        full_cov, matched = coverage(text, qt, phrases, pw, tw)
        direct = str(c.get("direct_evidence") or "")
        direct_cov, direct_matched = coverage(direct, qt, phrases, pw, tw)
        adjacent_cov, adjacent_matched = coverage(earlier_context(table), qt, phrases, pw, tw)
        ef = c.get("evidence_features") or {}
        rank = max(1, int(c.get("rank") or 999))
        score = (
            0.46 * full_cov + 0.16 * direct_cov + 0.14 * float(c.get("metadata_score", 0) or 0)
            + 0.10 * float(ef.get("row_score", 0) or 0) + 0.06 * float(bool(ef.get("numeric")))
            + 0.08 / math.sqrt(rank)
        )
        rows.append({
            "rank": rank, "internal_table_uid": uid, "document_id": c.get("document_id"),
            "table_topic": c.get("table_topic"), "direct_evidence": direct,
            "full_concept_coverage": round(full_cov, 6), "direct_concept_coverage": round(direct_cov, 6),
            "adjacent_context_coverage": round(adjacent_cov, 6),
            "matched_distinctive_phrases": matched[:10], "direct_matched_phrases": direct_matched[:10],
            "adjacent_context_matched_phrases": adjacent_matched[:10],
            "diagnostic_score": round(score, 6),
            "suggested_numeric_neighbor": suggest_numeric_neighbor(item, c, table, qt, phrases, pw, tw),
        })
    rows.sort(key=lambda x: x["diagnostic_score"], reverse=True)
    best = rows[0] if rows else None
    second = rows[1] if len(rows) > 1 else None
    best_adj = max(rows, key=lambda x: x["adjacent_context_coverage"], default=None)
    warnings = planner_warnings(item)

    if not best:
        diagnosis, risk, reason = "RETRIEVAL_RISK", "HIGH", "Top-K is empty."
    else:
        margin = best["diagnostic_score"] - (second["diagnostic_score"] if second else 0.0)
        table = tables.get(best["internal_table_uid"], {})
        original = next((c for c in candidates if str(c.get("internal_table_uid")) == best["internal_table_uid"]), {})
        idx = original.get("best_row_index")
        rows_raw = table.get("rows") or []
        direct_header = isinstance(idx, int) and 0 <= idx < len(rows_raw) and header_like(rows_raw[idx])
        neighbor = best.get("suggested_numeric_neighbor")
        if best["full_concept_coverage"] < 0.22 and float((best_adj or {}).get("adjacent_context_coverage", 0)) >= 0.38:
            diagnosis, risk = "ADJACENT_CONTEXT_HIT", "HIGH"
            reason = "Core query appears in older context_before but not candidate rows; likely adjacent-table/boundary issue."
        elif best["full_concept_coverage"] < 0.18 or (best["full_concept_coverage"] < 0.24 and not best["matched_distinctive_phrases"]):
            diagnosis, risk = "RETRIEVAL_RISK", "HIGH"
            reason = "No Top-K candidate strongly covers the core query concept."
        elif (direct_header or best["direct_concept_coverage"] + 0.12 < best["full_concept_coverage"]) and neighbor and neighbor["score"] >= 0.18:
            diagnosis, risk = "EVIDENCE_RISK", "MEDIUM"
            reason = "Plausible table exists, but direct evidence is weak/header-like; numeric neighbor is more reviewable."
        elif warnings:
            diagnosis, risk = "PLANNER_RISK", "MEDIUM"
            reason = "Planner metric is noisy; judge from source table/evidence rather than metric alone."
        elif margin < 0.035:
            diagnosis, risk = "AMBIGUOUS_TOPK", "MEDIUM"
            reason = "Top candidates are close; challenger/human review is appropriate."
        else:
            diagnosis, risk = "LOOKS_REVIEWABLE", "LOW"
            reason = "Candidate has coherent concept/evidence support."

    family = str((item.get("question_plan") or {}).get("family") or item.get("weak_family") or "")
    if family != "direct_lookup" and diagnosis == "RETRIEVAL_RISK":
        diagnosis, risk = "COMPLEX_FAMILY_REVIEW", "MEDIUM"
        reason = "This family may require multiple operands/tables; single-table coverage cannot prove retrieval failure."

    return {
        "id": item.get("id"), "question": item.get("question"), "weak_family": item.get("weak_family"),
        "planner_family": family, "planner_metric": metric_from_plan(item.get("question_plan") or {}),
        "core_question_text": core_question_text(item), "planner_warnings": warnings,
        "distinctive_query_phrases": phrases[:8], "diagnosis": diagnosis, "risk": risk, "reason": reason,
        "best_candidate": best, "runner_up_candidate": second, "best_adjacent_context_candidate": best_adj,
        "candidate_diagnostics": rows,
    }


def verify_bundle(root: Path) -> dict[str, Any]:
    for name in ("manifest.json", "review_items.jsonl", "tables.jsonl", "errors.jsonl"):
        if not (root / name).is_file(): raise FileNotFoundError(root / name)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    sums = root / "SHA256SUMS"
    if sums.is_file():
        for line in sums.read_text(encoding="utf-8").splitlines():
            if not line.strip(): continue
            digest, name = line.split(None, 1)
            path = root / name.strip().lstrip("*")
            if sha256_file(path) != digest: raise RuntimeError(f"SHA256 mismatch: {path.name}")
    return manifest


def audit_queue(diags: list[dict[str, Any]], size: int) -> list[dict[str, Any]]:
    priority = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    dp = {"ADJACENT_CONTEXT_HIT": 0, "RETRIEVAL_RISK": 1, "EVIDENCE_RISK": 2, "PLANNER_RISK": 3,
          "AMBIGUOUS_TOPK": 4, "COMPLEX_FAMILY_REVIEW": 5, "LOOKS_REVIEWABLE": 6}
    ordered = sorted(diags, key=lambda d: (priority[d["risk"]], dp.get(d["diagnosis"], 9), int(d["id"])))
    picked = []
    for canary in (13, 53):
        x = next((d for d in diags if int(d.get("id")) == canary), None)
        if x: picked.append(x)
    family_count = Counter(str(x.get("weak_family") or "unknown") for x in picked)
    for d in ordered:
        if d in picked: continue
        fam = str(d.get("weak_family") or "unknown")
        if family_count[fam] >= 2: continue
        picked.append(d); family_count[fam] += 1
        if len(picked) >= size: return picked[:size]
    for d in ordered:
        if d not in picked: picked.append(d)
        if len(picked) >= size: break
    return picked[:size]


def main() -> None:
    args = parse_args()
    temp = None
    if args.bundle_archive:
        archive = args.bundle_archive.resolve()
        if not archive.is_file(): raise FileNotFoundError(archive)
        temp = Path(tempfile.mkdtemp(prefix="vifinqa_diag_"))
        with tarfile.open(archive, "r:gz") as tar:
            base = temp.resolve()
            for member in tar.getmembers():
                target = (temp / member.name).resolve()
                if target != base and base not in target.parents: raise RuntimeError(f"Unsafe archive member: {member.name}")
            tar.extractall(temp)
        bundle = temp
    else:
        bundle = args.bundle_dir.resolve()

    try:
        manifest = verify_bundle(bundle)
        items = load_jsonl(bundle / "review_items.jsonl")
        table_rows = load_jsonl(bundle / "tables.jsonl")
        tables = {str(x["internal_table_uid"]): x for x in table_rows}
        if len(items) != int(manifest.get("review_item_count", len(items))): raise RuntimeError("Bundle count mismatch")

        out = args.output_dir.resolve()
        if out.exists() and any(out.iterdir()) and not args.force: raise RuntimeError(f"Output not empty: {out}; use --force")
        if out.exists() and args.force: shutil.rmtree(out)
        out.mkdir(parents=True, exist_ok=True)

        diags = [diagnose(item, tables) for item in items]
        write_jsonl(out / "review_bundle_diagnostics.jsonl", diags)

        with (out / "review_bundle_summary.csv").open("w", newline="", encoding="utf-8-sig") as f:
            fields = ["id", "weak_family", "diagnosis", "risk", "best_rank", "best_document", "best_topic",
                      "best_full_concept", "best_direct_concept", "suggested_evidence_row", "planner_warnings", "reason", "question"]
            w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
            for d in diags:
                b = d.get("best_candidate") or {}; nb = b.get("suggested_numeric_neighbor") or {}
                w.writerow({"id": d["id"], "weak_family": d.get("weak_family"), "diagnosis": d["diagnosis"], "risk": d["risk"],
                            "best_rank": b.get("rank"), "best_document": b.get("document_id"), "best_topic": b.get("table_topic"),
                            "best_full_concept": b.get("full_concept_coverage"), "best_direct_concept": b.get("direct_concept_coverage"),
                            "suggested_evidence_row": nb.get("text"), "planner_warnings": ";".join(d.get("planner_warnings") or []),
                            "reason": d["reason"], "question": d["question"]})

        chosen = audit_queue(diags, args.audit_size)
        write_jsonl(out / "manual_audit_queue.jsonl", [{
            "id": d["id"], "question": d["question"], "weak_family": d.get("weak_family"), "diagnosis": d["diagnosis"],
            "risk": d["risk"], "reason": d["reason"],
            "recommended_candidate_rank": (d.get("best_candidate") or {}).get("rank"),
            "recommended_candidate_uid": (d.get("best_candidate") or {}).get("internal_table_uid"),
            "table_topic": (d.get("best_candidate") or {}).get("table_topic"),
            "current_direct_evidence": (d.get("best_candidate") or {}).get("direct_evidence"),
            "suggested_evidence_row_index": ((d.get("best_candidate") or {}).get("suggested_numeric_neighbor") or {}).get("row_index"),
            "suggested_evidence": ((d.get("best_candidate") or {}).get("suggested_numeric_neighbor") or {}).get("text"),
            "planner_warnings": d.get("planner_warnings"),
        } for d in chosen])

        counts = Counter(d["diagnosis"] for d in diags); risks = Counter(d["risk"] for d in diags)
        fam = defaultdict(lambda: defaultdict(int))
        for d in diags: fam[str(d.get("weak_family") or "unknown")][d["diagnosis"]] += 1
        summary = {
            "bundle_git_commit": manifest.get("git_commit"), "question_count": len(diags),
            "diagnosis_counts": dict(counts), "risk_counts": dict(risks),
            "family_diagnosis_counts": {k: dict(v) for k, v in fam.items()}, "audit_size": len(chosen),
        }
        (out / "diagnostic_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print("\nNo gold labels were written. Next: inspect manual_audit_queue.jsonl before baseline auto-review.")
    finally:
        if temp is not None: shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    main()
