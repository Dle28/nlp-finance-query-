#!/usr/bin/env python3
"""Evidence-assisted retrieval annotation UI for Kaggle/Jupyter.

This is a conservative reviewer layered on top of ``annotate_widget.py``.  It
never silently writes gold labels.  Instead it:

- inspects the top-k retrieval candidates;
- scores metadata agreement (ticker/year/scope), metric-row overlap and numeric
  evidence;
- shows the strongest candidate and the exact evidence row(s) before the user
  opens the full candidate accordion;
- classifies the suggestion as HIGH / NEEDS_REVIEW / LOW;
- provides a one-click ``Accept recommendation`` action while preserving the
  existing annotation file and already-reviewed questions.

HIGH is deliberately restricted to direct-lookup questions with strong row
matching.  Analytical/multi-table families remain NEEDS_REVIEW even if the top
candidate looks strong.
"""

from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path
from typing import Any

from annotate_widget import (
    RetrievalAnnotator,
    load_config,
    normalize_tokens,
    widgets,
    clear_output,
    display,
)


DIGIT_RE = re.compile(r"\d")


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
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/labels/retriever_verified_60.jsonl"),
    )
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--start-id", type=int, default=None)
    parser.add_argument("--no-dense", action="store_true")
    parser.add_argument("--repo-root", type=Path, default=None)
    parser.add_argument("--preview-rows", type=int, default=14)
    parser.add_argument(
        "--high-threshold",
        type=float,
        default=0.74,
        help="Minimum heuristic evidence score for a HIGH direct-lookup suggestion.",
    )
    return parser.parse_args()


def _metric_from_plan(plan: dict[str, Any]) -> str:
    operands = plan.get("operands") or []
    if not operands:
        return ""
    return str(operands[0].get("metric") or "")


def _row_numeric(row: list[str]) -> bool:
    # Require a numeric-looking value outside the first descriptive cell.
    for cell in row[1:]:
        text = str(cell).strip()
        if DIGIT_RE.search(text):
            return True
    return False


def _row_features(
    row: list[str],
    metric_tokens: set[str],
    question_tokens: set[str],
) -> dict[str, Any]:
    text = " ".join(str(v) for v in row).casefold()
    tokens = normalize_tokens(text)
    metric_overlap = (
        len(tokens & metric_tokens) / max(1, len(metric_tokens))
        if metric_tokens
        else 0.0
    )
    question_overlap = (
        len(tokens & question_tokens) / max(1, len(question_tokens))
        if question_tokens
        else 0.0
    )
    numeric = _row_numeric(row)
    score = 0.72 * metric_overlap + 0.18 * question_overlap + (0.10 if numeric else 0.0)
    return {
        "score": score,
        "metric_overlap": metric_overlap,
        "question_overlap": question_overlap,
        "numeric": numeric,
        "text": text,
    }


def _candidate_review(
    annotator: RetrievalAnnotator,
    candidate: dict[str, Any],
    rank: int,
    question: str,
    plan: dict[str, Any],
) -> dict[str, Any]:
    asset = annotator.pipeline.store.get_asset(candidate["internal_table_uid"])
    rows: list[list[str]] = []
    if asset is not None:
        try:
            rows = json.loads(asset.get("rows_json") or "[]")
        except json.JSONDecodeError:
            rows = []

    metric = _metric_from_plan(plan)
    metric_tokens = normalize_tokens(metric)
    question_tokens = normalize_tokens(question)

    row_reviews: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        features = _row_features(row, metric_tokens, question_tokens)
        row_reviews.append({"index": index, "row": row, **features})

    row_reviews.sort(key=lambda item: item["score"], reverse=True)
    best_row = row_reviews[0] if row_reviews else None

    tickers = [str(v).casefold() for v in (plan.get("tickers") or [])]
    document_id = str(candidate.get("document_id") or "").casefold()
    ticker_match = not tickers or any(ticker in document_id for ticker in tickers)

    scope = plan.get("scope")
    scope_match = not scope or candidate.get("scope") == scope

    years = {int(v) for v in (plan.get("years") or []) if str(v).isdigit()}
    report_year = candidate.get("report_year")
    year_match = not years or report_year in years

    lex_rank = candidate.get("lexical_rank")
    dense_rank = candidate.get("dense_rank")
    lex_bonus = 1.0 if lex_rank is not None and int(lex_rank) <= 3 else 0.0
    dense_bonus = 1.0 if dense_rank is not None and int(dense_rank) <= 5 else 0.0

    row_score = float(best_row["score"]) if best_row else 0.0
    metadata_score = (
        (0.08 if ticker_match else 0.0)
        + (0.08 if scope_match else 0.0)
        + (0.05 if year_match else 0.0)
        + (0.025 * lex_bonus)
        + (0.025 * dense_bonus)
    )
    evidence_score = min(1.0, row_score * 0.74 + metadata_score)

    return {
        "rank": rank,
        "uid": candidate["internal_table_uid"],
        "document_id": candidate.get("document_id"),
        "evidence_score": evidence_score,
        "ticker_match": ticker_match,
        "scope_match": scope_match,
        "year_match": year_match,
        "best_row": best_row,
        "top_rows": row_reviews[:3],
        "candidate": candidate,
    }


def _recommendation(
    annotator: RetrievalAnnotator,
    row: dict[str, Any],
    result: dict[str, Any],
    top_k: int,
    high_threshold: float,
) -> dict[str, Any]:
    question = str(row["question"])
    plan = result["question_plan"]
    candidates = result["retrieved_tables"][:top_k]

    reviews = [
        _candidate_review(annotator, candidate, rank, question, plan)
        for rank, candidate in enumerate(candidates, start=1)
    ]
    reviews.sort(key=lambda item: item["evidence_score"], reverse=True)

    if not reviews:
        return {
            "level": "LOW",
            "reason": "Retriever returned no candidates.",
            "best": None,
            "reviews": [],
        }

    best = reviews[0]
    second = reviews[1]["evidence_score"] if len(reviews) > 1 else 0.0
    margin = best["evidence_score"] - second
    family = str(plan.get("family") or "")
    best_row = best.get("best_row") or {}

    strict_direct = (
        family == "direct_lookup"
        and best["rank"] <= 3
        and best["ticker_match"]
        and best["scope_match"]
        and best["year_match"]
        and bool(best_row.get("numeric"))
        and float(best_row.get("metric_overlap", 0.0)) >= 0.55
        and best["evidence_score"] >= high_threshold
        and margin >= 0.035
    )

    if strict_direct:
        level = "HIGH"
        reason = "Strong direct-lookup row + metadata agreement; human one-click confirmation recommended."
    elif best["evidence_score"] >= 0.52:
        level = "NEEDS_REVIEW"
        reason = "Plausible evidence, but ambiguity/multi-step semantics require human confirmation."
    else:
        level = "LOW"
        reason = "Weak row evidence; inspect candidates/raw report before labeling."

    return {
        "level": level,
        "reason": reason,
        "best": best,
        "reviews": reviews,
        "margin": margin,
    }


def _evidence_html(recommendation: dict[str, Any]) -> str:
    level = recommendation["level"]
    colors = {
        "HIGH": ("#e8f5e9", "#1b5e20"),
        "NEEDS_REVIEW": ("#fff8e1", "#8a5a00"),
        "LOW": ("#ffebee", "#8e1b1b"),
    }
    bg, fg = colors[level]
    best = recommendation.get("best")

    if best is None:
        return (
            f"<div style='padding:10px;border-radius:6px;background:{bg};color:{fg}'>"
            f"<b>Assisted review: {level}</b><br>{html.escape(recommendation['reason'])}</div>"
        )

    best_row = best.get("best_row") or {}
    row = best_row.get("row") or []
    row_text = " | ".join(html.escape(str(cell)) for cell in row)
    score = float(best["evidence_score"])
    overlap = float(best_row.get("metric_overlap", 0.0))
    numeric = "yes" if best_row.get("numeric") else "no"

    checks = (
        f"ticker={'✓' if best['ticker_match'] else '✗'} &nbsp; "
        f"scope={'✓' if best['scope_match'] else '✗'} &nbsp; "
        f"year={'✓' if best['year_match'] else '✗'} &nbsp; "
        f"numeric={'✓' if best_row.get('numeric') else '✗'}"
    )

    return (
        f"<div style='padding:10px;border-radius:6px;background:{bg};color:{fg};margin:6px 0'>"
        f"<b>Assisted review: {level}</b> — recommend candidate <b>#{best['rank']}</b> "
        f"(evidence={score:.3f}, metric-overlap={overlap:.2f})<br>"
        f"{checks}<br>"
        f"<span style='color:#444'>{html.escape(recommendation['reason'])}</span><br>"
        f"<b>Best evidence row:</b> <code>{row_text}</code>"
        f"</div>"
    )


class AssistedRetrievalAnnotator(RetrievalAnnotator):
    def __init__(self, args: argparse.Namespace) -> None:
        self.recommendation_box = widgets.HTML()
        self.accept_button = widgets.Button(
            description="Accept recommendation →",
            button_style="success",
            icon="check-circle",
        )
        self.accept_button.on_click(self._accept_recommendation)
        self.current_recommendation: dict[str, Any] | None = None

        super().__init__(args)

        # Rebuild the root layout, reusing all widgets created by the base class.
        self.root = widgets.VBox(
            [
                widgets.HBox([self.progress, self.counter]),
                self.question_box,
                widgets.HTML("<b>Question plan</b>"),
                self.plan_box,
                widgets.HTML("<b>Assisted first-pass review</b>"),
                self.recommendation_box,
                widgets.HBox([self.accept_button]),
                widgets.HTML("<b>Candidate tables</b> — expand only when needed"),
                self.candidate_container,
                widgets.HBox([self.plan_issue]),
                self.notes,
                widgets.HBox(
                    [
                        self.prev_button,
                        self.save_button,
                        self.none_button,
                        self.skip_button,
                        self.next_button,
                    ]
                ),
                self.status_box,
            ]
        )
        self.render()

    def render(self) -> None:
        super().render()
        row = self.questions[self.index]
        result = self._result(row)
        recommendation = _recommendation(
            self,
            row,
            result,
            self.args.top_k,
            self.args.high_threshold,
        )
        self.current_recommendation = recommendation
        self.recommendation_box.value = _evidence_html(recommendation)

        best = recommendation.get("best")
        if best is None:
            self.accept_button.disabled = True
            self.accept_button.description = "No recommendation"
        else:
            self.accept_button.disabled = False
            self.accept_button.description = f"Accept candidate #{best['rank']} →"
            self.accept_button.button_style = (
                "success" if recommendation["level"] == "HIGH" else "warning"
            )

        # Open the recommended candidate instead of always opening rank #1.
        children = self.candidate_container.children
        if children and isinstance(children[0], widgets.Accordion) and best is not None:
            recommended_rank = int(best["rank"])
            if 1 <= recommended_rank <= len(children[0].children):
                children[0].selected_index = recommended_rank - 1

    def _accept_recommendation(self, _button: widgets.Button) -> None:
        recommendation = self.current_recommendation or {}
        best = recommendation.get("best")
        if best is None:
            self.status_box.value = "<b style='color:#a00'>No recommendation to accept.</b>"
            return

        level = str(recommendation.get("level") or "UNKNOWN")
        previous_note = self.notes.value.strip()
        assisted_note = (
            f"assisted_review={level}; recommended_rank={best['rank']}; "
            f"evidence_score={float(best['evidence_score']):.3f}"
        )
        self.notes.value = f"{previous_note}; {assisted_note}".strip("; ")
        self._persist("verified_assisted", [best["uid"]])
        self._advance(1)


def main() -> None:
    args = parse_args()
    # Validate config here so CLI errors fail before widget construction.
    load_config(args.config)
    annotator = AssistedRetrievalAnnotator(args)
    clear_output(wait=True)
    display(annotator.root)
    print(f"Annotations persist atomically to: {args.output}")
    print("HIGH = conservative direct-lookup suggestion; all labels still require explicit acceptance.")


if __name__ == "__main__":
    main()
