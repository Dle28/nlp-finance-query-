#!/usr/bin/env python3
"""Aligned evidence-assisted annotation UI for Kaggle/Jupyter/local notebooks.

This v2 keeps the assisted-review logic from ``assisted_annotate_widget.py`` but
renders extracted financial-statement rows as a straight visual grid so human
review is faster.

Important: PDF/HTML extraction can still produce irregular row widths and merged
headers. The renderer therefore pads missing cells only for visualization and
explicitly warns reviewers when source rows are irregular. It does not claim to
reconstruct semantic table columns perfectly.

No retrieval/index/model artifacts are rebuilt by this script.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any

from annotate_widget import compact_rows, normalize_tokens, widgets, clear_output, display
from assisted_annotate_widget import AssistedRetrievalAnnotator, load_config, parse_args


_NUMBER_RE = re.compile(r"^\(?-?\d[\d.,\s]*%?\)?$")


def _is_numberish(value: str) -> bool:
    text = str(value).strip()
    return bool(text and _NUMBER_RE.fullmatch(text))


def _cell_html(cell: str, question_tokens: set[str], col_index: int, row_index: int) -> str:
    text = str(cell)
    tokens = normalize_tokens(text)
    matched = bool(tokens & question_tokens)

    background = "#fff3cd" if matched else ("#fbfcfe" if row_index % 2 else "#ffffff")
    align = "right" if _is_numberish(text) else "left"
    nowrap = "white-space:nowrap;" if _is_numberish(text) else ""

    if col_index == 0:
        width = "min-width:300px;max-width:520px;"
    else:
        width = "min-width:130px;max-width:260px;"

    if text:
        content = html.escape(text)
    else:
        content = "<span style='color:#c1c7d0'>—</span>"

    return (
        f"<td style='padding:8px 10px;border-right:1px solid #e1e5ea;"
        f"border-bottom:1px solid #e1e5ea;vertical-align:top;background:{background};"
        f"text-align:{align};{nowrap}{width}overflow-wrap:anywhere;"
        f"font-size:12px;line-height:1.4'>{content}</td>"
    )


def aligned_rows_html(rows: list[list[str]], question: str, limit: int) -> str:
    selected = compact_rows(rows, question, limit)
    if not selected:
        return "<i>No structured rows stored for this table.</i>"

    question_tokens = normalize_tokens(question)
    max_cols = max(len(row) for _, row in selected)
    irregular = len({len(row) for _, row in selected}) > 1

    warning = ""
    if irregular:
        warning = (
            "<div style='padding:8px 10px;margin:5px 0 9px 0;"
            "background:#eef5ff;border-left:4px solid #4b83c3;"
            "border-radius:4px;font-size:12px;color:#344054'>"
            "<b>Irregular source rows detected.</b> Empty cells are padded only for "
            "visual alignment. Source row order and original cell positions are preserved."
            "</div>"
        )

    header_cells = []
    for col_index in range(max_cols):
        min_width = "300px" if col_index == 0 else "130px"
        header_cells.append(
            f"<th style='position:sticky;top:0;z-index:4;background:#eef3f8;"
            f"padding:8px 10px;border-right:1px solid #cfd6df;"
            f"border-bottom:1px solid #cfd6df;min-width:{min_width};"
            f"font-size:11px;text-align:left;color:#475467'>c{col_index}</th>"
        )

    header = (
        "<thead><tr>"
        "<th style='position:sticky;left:0;top:0;z-index:6;background:#e8eef7;"
        "padding:8px;border-right:1px solid #cfd6df;border-bottom:1px solid #cfd6df;"
        "min-width:58px;text-align:center;font-size:11px;color:#475467'>row</th>"
        + "".join(header_cells)
        + "</tr></thead>"
    )

    body_rows: list[str] = []

    for row_index, row in selected:
        padded = list(row) + [""] * (max_cols - len(row))

        row_number = (
            f"<td style='position:sticky;left:0;z-index:3;background:#f7f9fb;"
            f"padding:8px;border-right:1px solid #d6dce4;border-bottom:1px solid #e1e5ea;"
            f"color:#667085;font-size:11px;text-align:center;font-weight:600'>"
            f"r{row_index}</td>"
        )

        cells = [
            _cell_html(str(cell), question_tokens, col_index, row_index)
            for col_index, cell in enumerate(padded)
        ]

        body_rows.append("<tr>" + row_number + "".join(cells) + "</tr>")

    return (
        warning
        + "<div style='max-height:560px;overflow:auto;border:1px solid #d9dee5;"
        "border-radius:8px;background:white'>"
        "<table style='border-collapse:separate;border-spacing:0;width:max-content;"
        "min-width:100%;font-variant-numeric:tabular-nums'>"
        + header
        + "<tbody>"
        + "".join(body_rows)
        + "</tbody></table></div>"
    )


class ReadableAssistedRetrievalAnnotator(AssistedRetrievalAnnotator):
    """Assisted annotator with aligned candidate-table rendering."""

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

        context_html = html.escape(context[-1400:]) if context else "No context stored."
        collapsed_context = (
            "<details style='font-size:12px;margin:6px 0'>"
            "<summary style='cursor:pointer;color:#555'><b>Show report context</b></summary>"
            f"<div style='padding:8px 5px;line-height:1.45;color:#555'>{context_html}</div>"
            "</details>"
        )

        preview = aligned_rows_html(rows, question, self.args.preview_rows)

        return widgets.VBox(
            [
                checkbox,
                widgets.HTML(
                    f"<div style='font-size:12px;color:#444;padding:3px 0'>{metadata}</div>"
                ),
                widgets.HTML(collapsed_context),
                widgets.HTML(
                    "<div style='font-size:12px;margin:4px 0;color:#555'>"
                    "<b>Aligned evidence rows</b> — yellow cells overlap question terms."
                    "</div>"
                ),
                widgets.HTML(preview),
            ]
        )


def main() -> None:
    args = parse_args()
    load_config(args.config)
    annotator = ReadableAssistedRetrievalAnnotator(args)
    clear_output(wait=True)
    display(annotator.root)
    print(f"Annotations persist atomically to: {args.output}")
    print(
        "Readable v2: extracted rows are displayed as aligned visual columns; "
        "irregular rows are padded only for review."
    )


if __name__ == "__main__":
    main()
