from __future__ import annotations

import json
from decimal import Decimal

from finance_query.e2e.core.formula_evidence_bridge import (
    FORMULA_EVIDENCE_BRIDGE_PROTOCOL,
    build_formula_evidence_candidates,
)


def _table(uid: str, document_id: str, rows: list[list[str]]) -> dict:
    return {
        "internal_table_uid": uid,
        "document_id": document_id,
        "rows": rows,
        "source_provenance": {"table_sha256": f"hash-{uid}"},
    }


def _binding(uid: str, document_id: str, row: int, column: int, raw: str) -> dict:
    return {
        "internal_table_uid": uid,
        "document_id": document_id,
        "ticker": document_id.split("_", 1)[0],
        "scope": "consolidated" if "consolidated" in document_id else "separate",
        "report_year": int(document_id.split("_")[3]),
        "source_unit": "vnd",
        "row_index": row,
        "binding": {
            "status": "cell_bound",
            "row_index": row,
            "column_index": column,
            "raw_value": raw,
            "source_row": [],
            "source_cell": {"source_row": row, "source_cell": column},
        },
    }


def _record(
    question_id: int,
    question: str,
    formula_id: str,
    operands: list[dict],
    selected: dict[str, dict],
    *,
    output_unit: str,
) -> dict:
    return {
        "id": question_id,
        "question": question,
        "formula": {
            "formula_id": formula_id,
            "expression": formula_id,
            "output_unit": output_unit,
            "confidence": 0.99,
            "definition_status": "defined",
            "operands": operands,
        },
        "selected_operand_matches": selected,
        "operand_coverage_status": "complete",
        "evidence_completeness": "complete",
    }


def test_bridge_replays_known_formula_families_from_current_cells(tmp_path) -> None:
    old_uid = "old"
    new_uid = "new"
    finance_uid = "finance"
    qns_uids = {str(year): f"qns-{year}" for year in (2017, 2019, 2020, 2021, 2023)}
    tables = {
        old_uid: _table(
            old_uid,
            "ACV_financial_statements_2021_consolidated",
            [["label", "code", "note", "100", "0"], ["cash", "110", "", "100", "0"]],
        ),
        new_uid: _table(
            new_uid,
            "ACV_financial_statements_2022_consolidated",
            [["label", "code", "note", "150", "100"], ["cash", "110", "", "150", "100"]],
        ),
        finance_uid: _table(
            finance_uid,
            "KBC_financial_statements_2015_separate",
            [
                ["code", "label", "note", "value", "prior"],
                ["21", "Doanh thu hoạt động tài chính", "", "313.825.269.273", "0"],
                [
                    "22",
                    "Chi phí tài chính Trong đó: Chi phí lãi vay",
                    "",
                    "42.828.996.818 28.722.879.897",
                    "0",
                ],
            ],
        ),
    }
    for year, uid in qns_uids.items():
        tables[uid] = _table(
            uid,
            f"QNS_financial_statements_{year}_separate",
            [["label", "code", "value"], ["Lưu chuyển tiền thuần từ hoạt động kinh doanh", "20", str(int(year) * 10)]],
        )

    def selected(uid: str, document_id: str, row: int, column: int, raw: str) -> dict:
        return _binding(uid, document_id, row, column, raw)

    percentage_question = "ACV growth from 2021 to 2022"
    percentage_operands = [
        {"operand_id": "x_old", "entity": "ACV", "years": [2021]},
        {"operand_id": "x_new", "entity": "ACV", "years": [2022]},
    ]
    percentage = _record(
        1,
        percentage_question,
        "percentage_change",
        percentage_operands,
        {
            "x_old": selected(old_uid, "ACV_financial_statements_2021_consolidated", 1, 3, "100"),
            "x_new": selected(new_uid, "ACV_financial_statements_2022_consolidated", 1, 3, "150"),
        },
        output_unit="percent",
    )
    percentage["answer"] = "999999"  # stored values must never be trusted

    finance_question = "KBC lãi thuần hoạt động tài chính năm 2015 theo tỷ đồng"
    finance_operands = [
        {"operand_id": "finance_income", "entity": "KBC", "years": [2015]},
        {"operand_id": "finance_expense", "entity": "KBC", "years": [2015]},
    ]
    finance = _record(
        2,
        finance_question,
        "net_finance_result",
        finance_operands,
        {
            "finance_income": selected(finance_uid, "KBC_financial_statements_2015_separate", 1, 3, "313.825.269.273"),
            "finance_expense": selected(finance_uid, "KBC_financial_statements_2015_separate", 2, 3, "42.828.996.818 28.722.879.897"),
        },
        output_unit="source_unit",
    )

    argmax_question = "QNS năm nào có lưu chuyển tiền thuần cao nhất"
    argmax_operands = []
    argmax_selected = {}
    for year in (2017, 2019, 2020, 2021, 2023):
        uid = qns_uids[str(year)]
        document_id = f"QNS_financial_statements_{year}_separate"
        operand_id = f"qns_operating_cash_flow_{year}"
        argmax_operands.append({"operand_id": operand_id, "entity": "QNS", "years": [year]})
        argmax_selected[operand_id] = selected(uid, document_id, 1, 2, str(year * 10))
    argmax = _record(
        3,
        argmax_question,
        "operating_cash_flow_argmax_period",
        argmax_operands,
        argmax_selected,
        output_unit="year",
    )

    sidecar = tmp_path / "formula.jsonl"
    sidecar.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in (percentage, finance, argmax)),
        encoding="utf-8",
    )
    questions = {row["id"]: {"id": row["id"], "question": row["question"]} for row in (percentage, finance, argmax)}

    candidates, stats = build_formula_evidence_candidates(
        sidecar,
        tables_by_uid=tables,
        questions_by_id=questions,
    )

    assert stats["protocol"] == FORMULA_EVIDENCE_BRIDGE_PROTOCOL
    assert sorted(candidates) == [1, 2, 3]
    assert candidates[1]["answer"] == Decimal("50")
    assert candidates[2]["answer"] == Decimal("270.996272455")
    assert candidates[2]["sources"][1]["value"] == Decimal("42.828996818")
    assert candidates[2]["sources"][1]["raw_value"] == "42.828.996.818 28.722.879.897"
    assert candidates[3]["answer"] == Decimal("2023")


def test_bridge_reselects_exact_metric_matches_when_sidecar_is_partial(tmp_path) -> None:
    uid = "mbs"
    document_id = "MBS_financial_statements_2022"
    tables = {
        uid: {
            **_table(
                uid,
                document_id,
                [
                    ["CHỈ TIÊU", "Mã số", "Năm 2022VND"],
                    ["133", "3. Chi phí trả trước ngắn hạn", "100"],
                    ["252", "2. Chi phí trả trước dài hạn", "400"],
                ],
            ),
            "scope": "unknown",
        }
    }

    def match(row: int, label: str, raw: str) -> dict:
        return {
            **_binding(uid, document_id, row, 2, raw),
            "scope": "unknown",
            "candidate_rank": 1,
            "source_row": [label, "", raw],
            "match_score": 1.0,
        }

    question = "Tính tỷ lệ chi phí trả trước ngắn hạn trên chi phí trả trước dài hạn năm 2022."
    record = _record(
        4,
        question,
        "explicit_stated_fraction",
        [
            {
                "operand_id": "numerator",
                "entity": "MBS",
                "years": [2022],
                "metric_hints": ["chi phi tra truoc ngan han"],
            },
            {
                "operand_id": "denominator",
                "entity": "MBS",
                "years": [2022],
                "metric_hints": ["chi phi tra truoc dai han"],
            },
        ],
        {},
        output_unit="percent",
    )
    record["evidence_completeness"] = "partial"
    record["operand_matches"] = {
        "numerator": [match(1, "133. Chi phí trả trước ngắn hạn", "100")],
        "denominator": [match(2, "252. Chi phí trả trước dài hạn", "400")],
    }

    sidecar = tmp_path / "partial_formula.jsonl"
    sidecar.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    candidates, stats = build_formula_evidence_candidates(
        sidecar,
        tables_by_uid=tables,
        questions_by_id={4: {"id": 4, "question": question}},
    )

    assert sorted(candidates) == [4]
    assert candidates[4]["answer"] == Decimal("25")
    assert candidates[4]["formula_bridge_resolution"]["mode"] == (
        "operand_match_reselection_v1"
    )
    assert stats["stats"]["reselected_candidates"] == 1
    assert candidates[4]["sources"][0]["raw_value"] == "100"
