#!/usr/bin/env python3
"""Local-only human review UI for an offline ViFinQA review bundle.

No FAISS, E5, SQLite retrieval, corpus rebuild, or GPU is used here. The widget
renders candidate summaries/direct evidence already exported on Kaggle, then
opens aligned source rows only when the human needs them.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    import ipywidgets as widgets
    from IPython.display import clear_output, display
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Local review requires ipywidgets + IPython/Jupyter.") from exc


TOKEN_RE = re.compile(r"[\w%]+", re.UNICODE)
NUMERIC_RE = re.compile(r"^[\s()\-+\d.,%/]+$")

STOPWORDS = {
    "cua", "của", "la", "là", "bao", "nhieu", "nhiêu", "nam", "năm",
    "cong", "công", "ty", "vao", "vào", "cuoi", "cuối", "dau", "đầu",
    "tai", "tại", "trong", "dong", "đồng", "trieu", "triệu", "ty", "tỷ",
    "ngay", "ngày", "thang", "tháng", "den", "đến", "mot", "một",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--machine-reviews", type=Path, default=None)
    parser.add_argument("--queue", type=Path, default=None)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/labels/retriever_verified_60.jsonl"),
    )
    parser.add_argument("--preview-rows", type=int, default=18)
    parser.add_argument("--start-id", type=int, default=None)
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
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


def atomic_write(path: Path, rows_by_id: dict[int, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        for qid in sorted(rows_by_id):
            file.write(json.dumps(rows_by_id[qid], ensure_ascii=False) + "\n")
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)


def normalize_tokens(text: str) -> set[str]:
    return {
        token.casefold()
        for token in TOKEN_RE.findall(text)
        if len(token) >= 2 and token.casefold() not in STOPWORDS
    }


def is_numeric(text: str) -> bool:
    value = str(text).strip()
    return bool(value and NUMERIC_RE.fullmatch(value) and any(char.isdigit() for char in value))


def focused_rows(
    rows: list[list[str]],
    question: str,
    candidate: dict[str, Any],
    limit: int,
) -> list[tuple[int, list[str]]]:
    if not rows:
        return []

    keep: set[int] = set(range(min(3, len(rows))))
    best = candidate.get("best_row_index")
    if isinstance(best, int):
        for index in range(max(0, best - 3), min(len(rows), best + 4)):
            keep.add(index)

    qtokens = normalize_tokens(question)
    scored = []
    for index, row in enumerate(rows):
        tokens = normalize_tokens(" ".join(str(cell) for cell in row))
        overlap = len(tokens & qtokens) / max(1, len(qtokens)) if qtokens else 0.0
        scored.append((overlap, index))
    scored.sort(reverse=True)

    for score, index in scored:
        if score <= 0:
            break
        keep.add(index)
        if len(keep) >= limit:
            break

    return [(index, rows[index]) for index in sorted(keep)[:limit]]


def aligned_table_html(
    rows: list[list[str]],
    question: str,
    candidate: dict[str, Any],
    limit: int,
) -> str:
    selected = focused_rows(rows, question, candidate, limit)
    if not selected:
        return "<i>No structured rows stored.</i>"

    qtokens = normalize_tokens(question)
    max_cols = max(len(row) for _, row in selected)
    irregular = len({len(row) for _, row in selected}) > 1

    warning = ""
    if irregular:
        warning = (
            "<div style='padding:7px 10px;margin:4px 0 8px;background:#eef5ff;"
            "border-left:4px solid #4b83c3;font-size:12px'>"
            "<b>Irregular extracted rows.</b> Empty cells are padded only for visual alignment; "
            "source cell order is unchanged.</div>"
        )

    head = (
        "<thead><tr>"
        "<th style='position:sticky;left:0;top:0;z-index:5;background:#e7edf5;"
        "padding:7px;border:1px solid #ccd4df'>row</th>"
        + "".join(
            f"<th style='position:sticky;top:0;z-index:4;background:#edf2f7;"
            f"padding:7px 10px;border:1px solid #ccd4df;min-width:"
            f"{'300px' if col == 0 else '135px'}'>c{col}</th>"
            for col in range(max_cols)
        )
        + "</tr></thead>"
    )

    body: list[str] = []
    best_index = candidate.get("best_row_index")
    for row_index, row in selected:
        padded = list(row) + [""] * (max_cols - len(row))
        row_bg = "#f2fbf3" if row_index == best_index else "#ffffff"
        cells = [
            f"<td style='position:sticky;left:0;z-index:2;background:#f7f9fb;"
            f"padding:7px;border:1px solid #dde2e8;color:#667085;text-align:center'>"
            f"r{row_index}</td>"
        ]
        for column, cell in enumerate(padded):
            text = str(cell)
            hit = bool(normalize_tokens(text) & qtokens)
            background = "#fff3cd" if hit else row_bg
            align = "right" if is_numeric(text) else "left"
            width = "min-width:300px;max-width:470px;" if column == 0 else "min-width:135px;"
            content = html.escape(text) if text else "<span style='color:#c0c6cf'>—</span>"
            cells.append(
                f"<td style='padding:7px 10px;border:1px solid #dde2e8;"
                f"vertical-align:top;background:{background};text-align:{align};"
                f"{width}overflow-wrap:anywhere;font-size:12px'>{content}</td>"
            )
        body.append("<tr>" + "".join(cells) + "</tr>")

    return (
        warning
        + "<div style='max-height:520px;overflow:auto;border:1px solid #d9dee5;"
        "border-radius:7px;background:white'>"
        "<table style='border-collapse:separate;border-spacing:0;width:max-content;min-width:100%;"
        "font-variant-numeric:tabular-nums'>"
        + head + "<tbody>" + "".join(body) + "</tbody></table></div>"
    )


def machine_html(review: dict[str, Any] | None) -> str:
    if not review:
        return "<div style='color:#777'>No machine review loaded.</div>"

    status = str(review.get("consensus_status") or "unknown")
    color = {
        "machine_calibrated": "#e7f6e9",
        "machine_high_confidence": "#e7f6e9",
        "machine_provisional": "#fff8df",
        "needs_human": "#ffecec",
        "retrieval_failure": "#ffecec",
    }.get(status, "#f4f4f4")

    votes = review.get("agent_votes") or {}
    vote_text = " &nbsp; ".join(
        f"{html.escape(str(name))}=<code>{html.escape(str(value or 'none'))}</code>"
        for name, value in votes.items()
    )
    evidence = html.escape(str(review.get("machine_candidate_direct_evidence") or ""))
    summary = html.escape(str(review.get("machine_candidate_summary") or ""))

    return (
        f"<div style='padding:10px;border-radius:7px;background:{color};font-size:12px'>"
        f"<b>Machine consensus:</b> {html.escape(status)} &nbsp; | &nbsp; "
        f"confidence={float(review.get('machine_confidence') or 0.0):.3f} &nbsp; | &nbsp; "
        f"agreement={float(review.get('agreement') or 0.0):.2f}<br>"
        f"<b>Recommended rank:</b> {html.escape(str(review.get('machine_candidate_rank')))}<br>"
        f"<b>Summary:</b> {summary}<br>"
        f"<b>Direct evidence:</b> <code>{evidence}</code><br>"
        f"<b>Votes:</b> {vote_text}<br>"
        f"<b>Verifier:</b> {html.escape(str((review.get('verifier') or {}).get('verdict')))} — "
        f"{html.escape(str((review.get('verifier') or {}).get('reason') or ''))}"
        f"</div>"
    )


class BundleReviewer:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        bundle = args.bundle_dir.resolve()
        manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
        if int(manifest.get("error_count") or 0) != 0:
            raise RuntimeError("Bundle has retrieval errors; human review refused.")

        all_items = load_jsonl(bundle / "review_items.jsonl")
        self.items_by_id = {int(item["id"]): item for item in all_items}
        self.tables = {
            str(row["internal_table_uid"]): row
            for row in load_jsonl(bundle / "tables.jsonl")
        }
        self.machine = {
            int(row["id"]): row
            for row in load_jsonl(args.machine_reviews)
        } if args.machine_reviews else {}

        if args.queue:
            queue_ids = [int(row["id"]) for row in load_jsonl(args.queue)]
            missing = [qid for qid in queue_ids if qid not in self.items_by_id]
            if missing:
                raise RuntimeError(f"Queue contains IDs missing from bundle: {missing}")
            self.items = [self.items_by_id[qid] for qid in queue_ids]
        else:
            self.items = all_items

        if not self.items:
            raise RuntimeError("Review queue is empty.")

        self.annotations = {
            int(row["id"]): row for row in load_jsonl(args.output)
        }
        self.index = self._initial_index(args.start_id)
        self.checkboxes: list[widgets.Checkbox] = []
        self.current_candidates: list[dict[str, Any]] = []

        self.progress = widgets.IntProgress(min=0, max=len(self.items), description="Reviewed")
        self.counter = widgets.HTML()
        self.question_box = widgets.HTML()
        self.machine_box = widgets.HTML()
        self.candidate_box = widgets.VBox()
        self.notes = widgets.Textarea(
            placeholder="Optional human note: why candidate is correct/wrong, planner issue, missing table...",
            layout=widgets.Layout(width="100%", height="68px"),
        )
        self.planner_issue = widgets.Checkbox(description="Planner/metadata issue", indent=False)

        self.accept_machine = widgets.Button(
            description="Accept machine →", button_style="success", icon="check-circle"
        )
        self.save_selected = widgets.Button(
            description="Save selected →", button_style="success", icon="check"
        )
        self.no_candidate = widgets.Button(
            description="No candidate in Top-K →", button_style="warning", icon="times"
        )
        self.skip = widgets.Button(description="Skip →", icon="forward")
        self.prev = widgets.Button(description="← Previous", icon="arrow-left")
        self.next = widgets.Button(description="Next →", icon="arrow-right")
        self.status = widgets.HTML()

        self.accept_machine.on_click(self._accept_machine)
        self.save_selected.on_click(self._save_selected)
        self.no_candidate.on_click(self._save_none)
        self.skip.on_click(lambda _: self._advance(1))
        self.prev.on_click(lambda _: self._advance(-1))
        self.next.on_click(lambda _: self._advance(1))

        self.root = widgets.VBox(
            [
                widgets.HBox([self.progress, self.counter]),
                self.question_box,
                widgets.HTML("<b>Machine multi-agent review</b>"),
                self.machine_box,
                widgets.HTML(
                    "<b>Candidate tables</b> — summary/direct evidence first; open aligned rows only when needed"
                ),
                self.candidate_box,
                self.planner_issue,
                self.notes,
                widgets.HBox(
                    [
                        self.prev,
                        self.accept_machine,
                        self.save_selected,
                        self.no_candidate,
                        self.skip,
                        self.next,
                    ]
                ),
                self.status,
            ]
        )
        self.render()

    def _initial_index(self, start_id: int | None) -> int:
        if start_id is not None:
            for index, item in enumerate(self.items):
                if int(item["id"]) >= start_id:
                    return index
        for index, item in enumerate(self.items):
            if int(item["id"]) not in self.annotations:
                return index
        return 0

    def _candidate_panel(
        self,
        candidate: dict[str, Any],
        question: str,
        selected: bool,
    ) -> widgets.Widget:
        rank = int(candidate["rank"])
        uid = str(candidate["internal_table_uid"])
        table = self.tables.get(uid)
        if table is None:
            raise RuntimeError(f"Bundle table payload missing UID: {uid}")

        checkbox = widgets.Checkbox(
            value=selected,
            description=f"Relevant candidate #{rank}",
            indent=False,
        )
        self.checkboxes.append(checkbox)

        summary = html.escape(str(candidate.get("one_line_summary") or ""))
        evidence = html.escape(str(candidate.get("direct_evidence") or ""))
        meta = (
            f"<b>{html.escape(str(candidate.get('document_id')))}</b> | "
            f"year={candidate.get('report_year')} | scope={html.escape(str(candidate.get('scope')))} | "
            f"L={candidate.get('lexical_rank')} | D={candidate.get('dense_rank')} | "
            f"RRF={float(candidate.get('fused_score') or 0.0):.5f}"
        )

        quick = widgets.HTML(
            "<div style='padding:8px 10px;background:#f8fafc;border:1px solid #e2e8f0;"
            "border-radius:7px;font-size:12px'>"
            f"{meta}<br><b>1-line:</b> {summary}<br>"
            f"<b>Direct row:</b> <code>{evidence}</code>"
            "</div>"
        )

        table_html = aligned_table_html(
            table.get("rows") or [],
            question,
            candidate,
            self.args.preview_rows,
        )
        detail = widgets.HTML(
            "<details style='margin-top:6px'><summary style='cursor:pointer'><b>Open aligned source rows</b></summary>"
            + table_html
            + "</details>"
        )
        context = html.escape(str(table.get("context_before") or "")[-1400:])
        context_box = widgets.HTML(
            "<details style='margin-top:4px;font-size:12px'><summary style='cursor:pointer'>Report context</summary>"
            f"<div style='padding:7px;color:#555'>{context}</div></details>"
        )
        return widgets.VBox([checkbox, quick, detail, context_box])

    def render(self) -> None:
        item = self.items[self.index]
        qid = int(item["id"])
        question = str(item["question"])
        existing = self.annotations.get(qid, {})
        machine = self.machine.get(qid)

        reviewed = sum(1 for item in self.items if int(item["id"]) in self.annotations)
        self.progress.value = reviewed
        self.counter.value = f"<b>{reviewed}/{len(self.items)}</b> reviewed | position {self.index + 1}/{len(self.items)}"
        self.question_box.value = (
            f"<div style='font-size:17px;padding:8px 0'><b>Q{qid}</b> "
            f"<span style='color:#666'>[{html.escape(str(item.get('weak_family') or ''))}]</span><br>"
            f"{html.escape(question)}</div>"
        )
        self.machine_box.value = machine_html(machine)
        self.notes.value = str(existing.get("review_notes") or "")
        self.planner_issue.value = bool(existing.get("planner_issue", False))

        selected_uids = set(existing.get("positive_table_uids") or [])
        self.checkboxes = []
        self.current_candidates = list(item.get("candidates") or [])
        panels = []
        titles = []
        for candidate in self.current_candidates:
            panels.append(
                self._candidate_panel(
                    candidate,
                    question,
                    str(candidate["internal_table_uid"]) in selected_uids,
                )
            )
            title = (
                f"#{candidate['rank']} {candidate.get('table_topic') or candidate.get('document_id')} "
                f"| y={candidate.get('report_year')} | L{candidate.get('lexical_rank')} D{candidate.get('dense_rank')}"
            )
            titles.append(title[:120])

        if panels:
            accordion = widgets.Accordion(children=panels, selected_index=None)
            for index, title in enumerate(titles):
                accordion.set_title(index, title)
            if machine and machine.get("machine_candidate_rank"):
                rank = int(machine["machine_candidate_rank"])
                if 1 <= rank <= len(panels):
                    accordion.selected_index = rank - 1
            self.candidate_box.children = (accordion,)
        else:
            self.candidate_box.children = (widgets.HTML("<i>No candidates in bundle.</i>"),)

        self.accept_machine.disabled = not bool(machine and machine.get("machine_candidate_uid"))
        self.status.value = (
            f"<span style='color:#666'>Saved status: "
            f"{html.escape(str(existing.get('annotation_status', 'not reviewed')))}</span>"
        )

    def _persist(self, status: str, positive_uids: list[str]) -> None:
        item = self.items[self.index]
        qid = int(item["id"])
        machine = self.machine.get(qid) or {}
        ranks = [
            int(candidate["rank"])
            for candidate in self.current_candidates
            if str(candidate["internal_table_uid"]) in set(positive_uids)
        ]
        self.annotations[qid] = {
            "id": qid,
            "question": item["question"],
            "annotation_status": status,
            "human_verified": True,
            "positive_table_uids": positive_uids,
            "selected_ranks": sorted(ranks),
            "planner_issue": bool(self.planner_issue.value),
            "review_notes": self.notes.value.strip(),
            "machine_candidate_uid": machine.get("machine_candidate_uid"),
            "machine_consensus_status": machine.get("consensus_status"),
            "machine_confidence": machine.get("machine_confidence"),
            "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        atomic_write(self.args.output, self.annotations)
        self.status.value = f"<b style='color:#176b2c'>Saved Q{qid}: {status}</b>"

    def _accept_machine(self, _button: widgets.Button) -> None:
        item = self.items[self.index]
        machine = self.machine.get(int(item["id"])) or {}
        selected = machine.get("machine_candidate_uid")
        if not selected:
            self.status.value = "<b style='color:#a00'>No machine candidate.</b>"
            return
        self._persist("human_verified", [str(selected)])
        self._advance(1)

    def _save_selected(self, _button: widgets.Button) -> None:
        selected = [
            str(candidate["internal_table_uid"])
            for candidate, checkbox in zip(self.current_candidates, self.checkboxes)
            if checkbox.value
        ]
        if not selected:
            self.status.value = "<b style='color:#a00'>Select at least one candidate or use No candidate.</b>"
            return
        self._persist("human_verified", selected)
        self._advance(1)

    def _save_none(self, _button: widgets.Button) -> None:
        self._persist("verified_no_candidate", [])
        self._advance(1)

    def _advance(self, delta: int) -> None:
        self.index = min(max(self.index + delta, 0), len(self.items) - 1)
        self.render()


def main() -> None:
    args = parse_args()
    reviewer = BundleReviewer(args)
    clear_output(wait=True)
    display(reviewer.root)
    print("Local static review only — no dense model/index is loaded.")
    print("Human labels persist atomically to:", args.output)


if __name__ == "__main__":
    main()
