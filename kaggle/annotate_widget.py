#!/usr/bin/env python3
"""Notebook-friendly retrieval annotation UI for Kaggle/Jupyter.

The terminal annotator is useful for SSH/CLI workflows, but reviewing ten table
candidates per question is much faster with a compact widget:

- one question at a time with progress;
- compact question-plan summary;
- candidate accordions with provenance and table-row previews;
- checkboxes for multi-table positives;
- Save / No relevant / Skip / Previous navigation;
- optional planner-issue flag and reviewer notes;
- atomic JSONL persistence and resume/edit support.

Run from a notebook with `%run kaggle/annotate_widget.py ...`.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from finance_query.config import ProjectPaths
from finance_query.pipeline import ViFinQARetrievalPipeline, load_config

try:
    import ipywidgets as widgets
    from IPython.display import clear_output, display
except ImportError as exc:  # pragma: no cover - notebook dependency
    raise RuntimeError(
        "Notebook annotation requires ipywidgets and IPython. "
        "On Kaggle they are normally preinstalled."
    ) from exc


TOKEN_RE = re.compile(r"[\w%]+", re.UNICODE)
NUMERIC_RE = re.compile(r"^[\s()\-+\d.,%]+$")


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
    parser.add_argument(
        "--preview-rows",
        type=int,
        default=14,
        help="Maximum compact table rows shown inside each candidate accordion.",
    )
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_number}") from exc
    return rows


def load_annotations(path: Path) -> dict[int, dict[str, Any]]:
    if not path.is_file():
        return {}
    return {int(row["id"]): row for row in load_jsonl(path)}


def atomic_write_annotations(path: Path, annotations: dict[int, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        for question_id in sorted(annotations):
            file.write(json.dumps(annotations[question_id], ensure_ascii=False) + "\n")
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)


def normalize_tokens(text: str) -> set[str]:
    stop = {
        "cua", "của", "la", "là", "bao", "nhieu", "nhiêu", "nam", "năm",
        "cong", "công", "ty", "vao", "vào", "cuoi", "cuối", "dau", "đầu",
        "tai", "tại", "trong", "dong", "đồng", "trieu", "triệu", "ty", "tỷ",
    }
    return {
        token.casefold()
        for token in TOKEN_RE.findall(text)
        if len(token) >= 2 and token.casefold() not in stop
    }


def row_score(row: list[str], question_tokens: set[str]) -> float:
    text = " ".join(row).casefold()
    tokens = set(TOKEN_RE.findall(text))
    if not tokens or not question_tokens:
        return 0.0
    overlap = len(tokens & question_tokens) / max(1, len(question_tokens))
    label_bonus = 0.15 if row and not NUMERIC_RE.fullmatch(row[0].strip()) else 0.0
    return overlap + label_bonus


def compact_rows(rows: list[list[str]], question: str, limit: int) -> list[tuple[int, list[str]]]:
    if not rows:
        return []
    question_tokens = normalize_tokens(question)
    keep: set[int] = set(range(min(3, len(rows))))

    scored = sorted(
        ((row_score(row, question_tokens), index) for index, row in enumerate(rows)),
        reverse=True,
    )
    for score, index in scored:
        if score <= 0:
            continue
        keep.add(index)
        if index > 0:
            keep.add(index - 1)
        if index + 1 < len(rows):
            keep.add(index + 1)
        if len(keep) >= limit:
            break

    selected = sorted(keep)[:limit]
    return [(index, rows[index]) for index in selected]


def table_html(rows: list[list[str]], question: str, limit: int) -> str:
    selected = compact_rows(rows, question, limit)
    if not selected:
        return "<i>No structured rows stored for this table.</i>"

    question_tokens = normalize_tokens(question)
    body: list[str] = []
    for row_index, row in selected:
        cells = []
        for cell in row:
            escaped = html.escape(str(cell))
            cell_tokens = set(TOKEN_RE.findall(str(cell).casefold()))
            style = "background:#fff3cd;" if cell_tokens & question_tokens else ""
            cells.append(
                f'<td style="padding:3px 7px;border:1px solid #ddd;{style}">{escaped}</td>'
            )
        body.append(
            f'<tr><td style="color:#777;padding:3px 6px;border:1px solid #ddd;">{row_index}</td>'
            + "".join(cells)
            + "</tr>"
        )
    return (
        '<div style="overflow-x:auto;max-height:360px;overflow-y:auto;">'
        '<table style="border-collapse:collapse;font-size:12px;">'
        + "".join(body)
        + "</table></div>"
    )


def plan_html(plan: dict[str, Any], weak_family: str | None) -> str:
    operands = plan.get("operands") or []
    metric = operands[0].get("metric") if operands else ""
    rows = [
        ("family", plan.get("family")),
        ("weak family", weak_family),
        ("confidence", f"{float(plan.get('family_confidence', 0)):.3f}"),
        ("ticker", ", ".join(plan.get("tickers") or [])),
        ("year", ", ".join(str(v) for v in plan.get("years") or [])),
        ("scope", plan.get("scope")),
        ("requested unit", plan.get("requested_unit")),
        ("metric", metric),
        ("operation", (plan.get("operation_ast") or {}).get("op")),
    ]
    cells = "".join(
        f"<tr><th style='text-align:left;padding:2px 8px;color:#555'>{html.escape(str(key))}</th>"
        f"<td style='padding:2px 8px'>{html.escape(str(value or ''))}</td></tr>"
        for key, value in rows
    )
    return f"<table style='font-size:13px'>{cells}</table>"


class RetrievalAnnotator:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.paths = ProjectPaths.from_repository(args.repo_root)
        self.pipeline = ViFinQARetrievalPipeline(
            paths=self.paths,
            config=load_config(args.config),
            use_dense=not args.no_dense,
        )
        self.questions = load_jsonl(args.questions)
        if not self.questions:
            raise ValueError(f"No questions in {args.questions}")
        self.annotations = load_annotations(args.output)
        self.cache: dict[int, dict[str, Any]] = {}
        self.index = self._initial_index(args.start_id)
        self.candidate_checks: list[widgets.Checkbox] = []
        self.current_candidates: list[dict[str, Any]] = []

        self.progress = widgets.IntProgress(
            value=0,
            min=0,
            max=len(self.questions),
            description="Reviewed",
            bar_style="info",
            layout=widgets.Layout(width="55%"),
        )
        self.counter = widgets.HTML()
        self.question_box = widgets.HTML()
        self.plan_box = widgets.HTML()
        self.status_box = widgets.HTML()
        self.candidate_container = widgets.VBox()
        self.plan_issue = widgets.Checkbox(
            value=False,
            description="Planner/metadata issue",
            indent=False,
        )
        self.notes = widgets.Textarea(
            placeholder="Optional review note, e.g. wrong unit/metric/scope or why no candidate is correct",
            layout=widgets.Layout(width="100%", height="62px"),
        )

        self.save_button = widgets.Button(
            description="Save selected →",
            button_style="success",
            icon="check",
        )
        self.none_button = widgets.Button(
            description="No relevant →",
            button_style="warning",
            icon="times",
        )
        self.skip_button = widgets.Button(description="Skip →", icon="forward")
        self.prev_button = widgets.Button(description="← Previous", icon="arrow-left")
        self.next_button = widgets.Button(description="Next →", icon="arrow-right")

        self.save_button.on_click(self._save_selected)
        self.none_button.on_click(self._save_none)
        self.skip_button.on_click(self._skip)
        self.prev_button.on_click(self._previous)
        self.next_button.on_click(self._next)

        self.root = widgets.VBox(
            [
                widgets.HBox([self.progress, self.counter]),
                self.question_box,
                widgets.HTML("<b>Question plan</b>"),
                self.plan_box,
                widgets.HTML("<b>Candidate tables</b> — expand a row, then tick Relevant"),
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

    def _initial_index(self, start_id: int | None) -> int:
        if start_id is not None:
            for index, row in enumerate(self.questions):
                if int(row["id"]) >= start_id:
                    return index
        for index, row in enumerate(self.questions):
            if int(row["id"]) not in self.annotations:
                return index
        return 0

    def _result(self, row: dict[str, Any]) -> dict[str, Any]:
        question_id = int(row["id"])
        if question_id not in self.cache:
            self.cache[question_id] = self.pipeline.retrieve(
                str(row["question"]),
                question_id,
            )
        return self.cache[question_id]

    def _candidate_panel(
        self,
        candidate: dict[str, Any],
        rank: int,
        question: str,
        checked: bool,
    ) -> widgets.Widget:
        asset = self.pipeline.store.get_asset(candidate["internal_table_uid"])
        rows: list[list[str]] = []
        context = ""
        if asset is not None:
            try:
                rows = json.loads(asset.get("rows_json") or "[]")
            except json.JSONDecodeError:
                rows = []
            context = asset.get("context_before") or ""

        checkbox = widgets.Checkbox(
            value=checked,
            description=f"Relevant candidate #{rank}",
            indent=False,
        )
        self.candidate_checks.append(checkbox)

        metadata = (
            f"<b>{html.escape(candidate['document_id'])}</b> | "
            f"year={candidate.get('report_year')} | scope={html.escape(str(candidate.get('scope')))} | "
            f"lex={candidate.get('lexical_rank')} | dense={candidate.get('dense_rank')} | "
            f"RRF={float(candidate.get('fused_score', 0)):.5f}"
        )
        context_html = html.escape(context[-700:]) if context else ""
        content = widgets.VBox(
            [
                checkbox,
                widgets.HTML(f"<div style='font-size:12px;color:#444'>{metadata}</div>"),
                widgets.HTML(
                    f"<div style='font-size:12px;margin:5px 0'><b>Context:</b> {context_html}</div>"
                ),
                widgets.HTML(table_html(rows, question, self.args.preview_rows)),
            ]
        )
        return content

    def render(self) -> None:
        row = self.questions[self.index]
        question_id = int(row["id"])
        question = str(row["question"])
        result = self._result(row)
        candidates = result["retrieved_tables"][: self.args.top_k]
        self.current_candidates = candidates
        existing = self.annotations.get(question_id, {})
        selected = set(existing.get("positive_table_uids") or [])

        reviewed_count = sum(
            1 for question in self.questions if int(question["id"]) in self.annotations
        )
        self.progress.value = reviewed_count
        self.counter.value = (
            f"<b>{reviewed_count}/{len(self.questions)}</b> reviewed &nbsp; | &nbsp; "
            f"position {self.index + 1}/{len(self.questions)}"
        )
        weak_family = row.get("weak_family")
        self.question_box.value = (
            f"<div style='font-size:17px;padding:8px 0'><b>Q{question_id}</b> "
            f"<span style='color:#666'>[{html.escape(str(weak_family or ''))}]</span><br>"
            f"{html.escape(question)}</div>"
        )
        self.plan_box.value = plan_html(result["question_plan"], weak_family)
        self.plan_issue.value = bool(existing.get("planner_issue", False))
        self.notes.value = str(existing.get("review_notes") or "")
        self.status_box.value = (
            f"<span style='color:#666'>Saved status: "
            f"{html.escape(str(existing.get('annotation_status', 'not reviewed')))}</span>"
        )

        self.candidate_checks = []
        panels: list[widgets.Widget] = []
        titles: list[str] = []
        for rank, candidate in enumerate(candidates, start=1):
            panels.append(
                self._candidate_panel(
                    candidate,
                    rank,
                    question,
                    candidate["internal_table_uid"] in selected,
                )
            )
            title = (
                f"#{rank} {candidate['document_id']} | y={candidate.get('report_year')} "
                f"| {candidate.get('scope')} | L{candidate.get('lexical_rank')} "
                f"D{candidate.get('dense_rank')}"
            )
            titles.append(title[:120])

        if panels:
            accordion = widgets.Accordion(children=panels, selected_index=0)
            for index, title in enumerate(titles):
                accordion.set_title(index, title)
            self.candidate_container.children = (accordion,)
        else:
            self.candidate_container.children = (
                widgets.HTML("<i>No candidates returned by the retriever.</i>"),
            )

    def _persist(self, status: str, selected_uids: list[str]) -> None:
        row = self.questions[self.index]
        question_id = int(row["id"])
        result = self._result(row)
        candidate_uids = [candidate["internal_table_uid"] for candidate in self.current_candidates]
        rank_lookup = {uid: rank for rank, uid in enumerate(candidate_uids, start=1)}

        self.annotations[question_id] = {
            "id": question_id,
            "question": str(row["question"]),
            "weak_family": row.get("weak_family"),
            "positive_table_uids": selected_uids,
            "selected_ranks": [rank_lookup[uid] for uid in selected_uids if uid in rank_lookup],
            "annotation_status": status,
            "planner_issue": bool(self.plan_issue.value),
            "review_notes": self.notes.value.strip(),
            "question_plan": result["question_plan"],
            "candidate_uids": candidate_uids,
            "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        atomic_write_annotations(self.args.output, self.annotations)

    def _advance(self, delta: int = 1) -> None:
        self.index = max(0, min(len(self.questions) - 1, self.index + delta))
        self.render()

    def _save_selected(self, _button: widgets.Button) -> None:
        selected_uids = [
            candidate["internal_table_uid"]
            for candidate, checkbox in zip(
                self.current_candidates,
                self.candidate_checks,
                strict=True,
            )
            if checkbox.value
        ]
        if not selected_uids:
            self.status_box.value = (
                "<b style='color:#b35c00'>No candidate selected. "
                "Tick Relevant or use No relevant.</b>"
            )
            return
        self._persist("verified", selected_uids)
        self._advance(1)

    def _save_none(self, _button: widgets.Button) -> None:
        self._persist("verified_no_candidate", [])
        self._advance(1)

    def _skip(self, _button: widgets.Button) -> None:
        self._advance(1)

    def _previous(self, _button: widgets.Button) -> None:
        self._advance(-1)

    def _next(self, _button: widgets.Button) -> None:
        self._advance(1)


def main() -> None:
    args = parse_args()
    annotator = RetrievalAnnotator(args)
    clear_output(wait=True)
    display(annotator.root)
    print(f"Annotations persist atomically to: {args.output}")


if __name__ == "__main__":
    main()
