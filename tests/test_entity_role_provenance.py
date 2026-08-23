from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from finance_query.entity_role_provenance import (
    EntityRoleProvenanceError,
    build_candidate_queue,
)
from finance_query.semantic_approvals import sha256_file


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_builds_hash_bound_candidates_and_skips_title_bound_issue(tmp_path: Path) -> None:
    source = tmp_path / "issuer.txt"
    source.write_text(
        "Dòng trước\nCông ty là công ty mẹ có các công ty con.\nDòng sau\n",
        encoding="utf-8",
    )
    source_sha = sha256_file(source)
    issues = tmp_path / "issues.jsonl"
    _jsonl(
        issues,
        [
            {
                "question_id": 1,
                "strict_status": "UNRESOLVED",
                "claim_entity_identity": "ABC",
                "claim_entity_role": "parent",
                "source_role_literal_present": False,
                "issue_brief_sha256": "a" * 64,
            },
            {
                "question_id": 2,
                "strict_status": "UNRESOLVED",
                "claim_entity_identity": "ABC",
                "claim_entity_role": "parent",
                "source_role_literal_present": True,
                "issue_brief_sha256": "b" * 64,
            },
        ],
    )
    campaign = tmp_path / "campaign.json"
    campaign.write_text(
        json.dumps(
            {
                "items": [
                    {"question_id": 1, "documentUid": "ABC_2024_separate"},
                    {"question_id": 2, "documentUid": "ABC_2024_separate"},
                ]
            }
        ),
        encoding="utf-8",
    )
    tables = tmp_path / "tables.jsonl"
    _jsonl(
        tables,
        [
            {
                "document_id": "ABC_2024_separate",
                "source_provenance": {
                    "source_path": str(source),
                    "source_sha256": source_sha,
                },
            }
        ],
    )

    result = build_candidate_queue(
        issue_briefs=issues,
        campaign_bundle=campaign,
        structured_tables=tables,
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
    )

    assert result["counts"] == {
        "queue_item_count": 1,
        "candidate_count": 1,
        "already_bound_in_source_title": 1,
        "documents_with_role_literal": 1,
    }
    row = json.loads(
        Path(result["outputs"]["queue"]["path"]).read_text(encoding="utf-8")
    )
    candidate = row["candidates"][0]
    assert candidate["line_number"] == 2
    assert candidate["source_path"] == "issuer.txt"
    assert candidate["raw_text_sha256"] == hashlib.sha256(
        candidate["raw_text"].encode("utf-8")
    ).hexdigest()
    assert row["source_contract"]["promotion_allowed"] is False


def test_rejects_source_hash_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "issuer.txt"
    source.write_text("Công ty mẹ\n", encoding="utf-8")
    issues = tmp_path / "issues.jsonl"
    _jsonl(
        issues,
        [
            {
                "question_id": 1,
                "strict_status": "UNRESOLVED",
                "source_role_literal_present": False,
            }
        ],
    )
    campaign = tmp_path / "campaign.json"
    campaign.write_text(
        json.dumps({"items": [{"question_id": 1, "documentUid": "doc"}]}),
        encoding="utf-8",
    )
    tables = tmp_path / "tables.jsonl"
    _jsonl(
        tables,
        [
            {
                "document_id": "doc",
                "source_provenance": {
                    "source_path": str(source),
                    "source_sha256": "f" * 64,
                },
            }
        ],
    )

    with pytest.raises(EntityRoleProvenanceError, match="source SHA-256 mismatch"):
        build_candidate_queue(
            issue_briefs=issues,
            campaign_bundle=campaign,
            structured_tables=tables,
            repository_root=tmp_path,
            output_dir=tmp_path / "out",
        )
