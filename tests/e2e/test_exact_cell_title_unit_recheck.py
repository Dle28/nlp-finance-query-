from __future__ import annotations

import hashlib

from finance_query.e2e.core.exact_cell_bindings_v2 import (
    resolve_source_title_unit_recheck,
    sha_json,
)


def test_title_unit_recheck_requires_hash_bound_v3_context() -> None:
    table = {
        "document_id": "AAA_2023_separate",
        "internal_table_uid": "table-1",
        "source_provenance": {"source_sha256": "a" * 64, "table_sha256": "b" * 64},
    }
    context = {
        "document_id": "AAA_2023_separate",
        "internal_table_uid": "table-1",
        "source_provenance": {"source_sha256": "a" * 64, "table_sha256": "b" * 64},
        "context_trace": {"source_title": "Báo cáo riêng Đơn vị: VND"},
    }
    title = context["context_trace"]["source_title"]
    candidate = {
        "internal_table_uid": "table-1",
        "unit_source_title_recheck": {
            "document_id": "AAA_2023_separate",
            "internal_table_uid": "table-1",
            "source_sha256": "a" * 64,
            "table_sha256": "b" * 64,
            "evidence_context_row_sha256": sha_json(context),
            "source_title_sha256": hashlib.sha256(title.encode()).hexdigest(),
            "source_unit": "vnd",
            "source_to_vnd_multiplier": "1",
            "raw_unit_label": "VND",
        },
    }

    unit, multiplier, anchor, reason = resolve_source_title_unit_recheck(
        candidate=candidate, table=table, contexts={"table-1": context}
    )

    assert unit == "vnd"
    assert str(multiplier) == "1"
    assert anchor is not None
    assert reason is None

    candidate["unit_source_title_recheck"]["source_title_sha256"] = "wrong"
    _, _, _, rejected_reason = resolve_source_title_unit_recheck(
        candidate=candidate, table=table, contexts={"table-1": context}
    )
    assert rejected_reason == "SOURCE_UNIT_TITLE_RECHECK_TITLE_MISMATCH"
