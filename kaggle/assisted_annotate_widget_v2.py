#!/usr/bin/env python3
"""Readable evidence-assisted annotation UI for Kaggle/Jupyter.

This v2 keeps the assisted-review logic from ``assisted_annotate_widget.py`` but
changes candidate rendering. Financial-statement HTML extraction often produces
irregular rows (different cell counts, merged headers, repeated column groups),
so forcing those rows into one rectangular HTML table is misleading and hard to
read.

Instead, each source row is rendered as a compact row card:

- original row index is preserved;
- cells stay in original order;
- long cells wrap instead of stretching the notebook horizontally;
- cells overlapping question terms are highlighted;
- irregular-width rows are explicitly flagged;
- context is collapsed under a ``details`` element.

No retrieval/index/model artifacts are rebuilt by this script.
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

from annotate_widget import compact_rows, normalize_tokens, widgets, clear_output, display
from assisted_annotate_widget import (
    AssistedRetrievalAnnotator,
    load_config,
    parse_args,
)


def _cell_style(cell: str, question_tokens: set[str]) -> tuple[str, str]:
    text = str(cell)
    tokens = normalize_tokens(text)
    matched = bool(tokens & question_tokens)
    if matched:
        return (
            "background:#fff3cd;border:1px solid #e0b84c;color:#3b2a00;",
            "matched",
        )
    return (
        "background:#f8f9fa;border:1px solid #d9dee3;color:#222;",
        "normal",
    )


def readable_rows_html(rows: list[list[str]], question: str, limit: int) -> str:
    selected = compact_rows(rows, question, limit)
    if not selected:
        return "<i>No structured rows stored for this table.</i>"

    question_tokens = normalize_tokens(question)
    widths = [len(row) for _, row in selected]
    irregular = len(set(widths)) > 1

    warning = ""
    if irregular:
        warning = (
            "<div style='padding:7px 9px;margin:5px 0 8px 0;"
            "background:#eef5ff;border-left:4px solid #4b83c3;font-size:12px'>"
            "<b>Irregular source rows detected.</b> Cells are shown in original "
            "sequence instead of being forced into columns. This avoids false "
            "alignment from merged PDF/HTML headers."
            "</div>"
        )

    cards: list[str] = []
    for row_index, row in selected:
        cells: list[str] = []
        for cell_index, cell in enumerate(row):
            style, _ = _cell_style(str(cell), question_tokens)
            escaped = html.escape(str(cell))
            cells.append(
                "<div style='"
                + style
                + "padding:5px 8px;border-radius:5px;min-width:70px;"
                  "max-width:360px;white-space:normal;overflow-wrap:anywhere;"
                  "font-size:12px;line-height:1.35'>"
                + f"<span style='color:#777;font-size:10px'>c{cell_index}</span><br>"
                + escaped
                + "</div>"
            )

        cards.append(
            "<div style='display:flex;align-items:flex-start;gap:7px;"
            "padding:6px 0;border-bottom:1px solid #ececec'>"
            f"<div style='min-width:42px;color:#666;font-size:11px;padding-top:6px'>r{row_index}</div>"
            "<div style='display:flex;flex-wrap:wrap;gap:5px;flex:1'>"
            + "".join(cells)
            + "</div></div>"
        )

    return (
        warning
        + "<div style='max-height:430px;overflow-y:auto;overflow-x:hidden;"
          "border:1px solid #e3e6e8;border-radius:6px;padding:4px 10px;"
          "background:white'>"
        + "".join(cards)
        + "</div>"
    )


class ReadableAssistedRetrievalAnnotator(AssistedRetrievalAnnotator):
    """Assisted annotator with row-card candidate rendering."""

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
            f"<b>{html.escape(str(candidate['document_id']))}</b> &nbsp;|&nbsp; "
            f"year={candidate.get('report_year')} &nbsp;|&nbsp; "
            f"scope={html.escape(str(candidate.get('scope')))} &nbsp;|&nbsp; "
            f"lex={candidate.get('lexical_rank')} &nbsp;|&nbsp; "
            f"dense={candidate.get('dense_rank')} &nbsp;|&nbsp; "
            f"RRF={float(candidate.get('fused_score', 0)):.5f}"
        )

        context_html = html.escape(context[-1200:]) if context else "No context stored."
        collapsed_context = (
            "<details style='font-size:12px;margin:6px 0'>"
            "<summary style='cursor:pointer;color:#555'><b>Show report context</b></summary>"
            f"<div style='padding:7px 4px;line-height:1.45;color:#555'>{context_html}</div>"
            "</details>"
        )

        preview = readable_rows_html(rows, question, self.args.preview_rows)
        content = widgets.VBox(
            [
                checkbox,
                widgets.HTML(
                    f"<div style='font-size:12px;color:#444;padding:3px 0'>{metadata}</div>"
                ),
                widgets.HTML(collapsed_context),
                widgets.HTML(
                    "<div style='font-size:12px;margin:4px 0;color:#555'>"
                    "<b>Evidence-focused source rows</b> — yellow cells overlap question terms."
                    "</div>"
                ),
                widgets.HTML(preview),
            ]
        )
        return content


def main() -> None:
    args = parse_args()
    load_config(args.config)
    annotator = ReadableAssistedRetrievalAnnotator(args)
    clear_output(wait=True)
    display(annotator.root)
    print(f"Annotations persist atomically to: {args.output}")
    print("Readable v2: irregular extracted rows are displayed as row cards, not a forced grid.")


if __name__ == "__main__":
    main()
