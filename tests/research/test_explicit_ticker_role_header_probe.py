from __future__ import annotations

import pytest

from finance_query.research.explicit_ticker_role_header_probe import _rules


def test_role_header_config_rejects_noncompact_semantic_fragments(tmp_path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(
        '{"protocol":"vifinqa_explicit_ticker_role_header_probe_v1","schema_version":1,"rules":[{"question_id":1,"operand_id":"x0","rule_id":"x","period_role":"opening","required_row_compact_fragments":["thue thu"],"required_header_compact_fragments":["sophainop"],"allowed_table_functions":["financial_note_detail"]}]}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="incomplete"):
        _rules(path)
