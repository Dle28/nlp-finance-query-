from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
RUN = ROOT / "artifacts/runs/vifinqa-grounded-e2e-human-verified-v5_20260823-final/grounded_e2e_run_v1.json"
REVIEW_BUNDLE = ROOT / "review_ui/public/review-data.json"
SPEC = importlib.util.spec_from_file_location(
    "build_grounded_campaign_review_handoff",
    ROOT / "scripts/build_grounded_campaign_review_handoff.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_builds_blank_hash_bound_campaign_handoff(tmp_path: Path) -> None:
    result = MODULE.build(run_receipt=RUN, output_dir=tmp_path / "campaign", review_bundle=REVIEW_BUNDLE)
    candidates = [
        json.loads(line)
        for line in Path(result["outputs"]["candidates"]["path"]).read_text(encoding="utf-8").splitlines()
    ]
    response = json.loads(Path(result["outputs"]["blank_human_response"]["path"]).read_text(encoding="utf-8"))
    assert result["candidate_count"] == 23
    assert result["human_campaign_decision_count"] == 0
    assert result["campaign_status"] == "semantic_revision_required"
    assert [row["question_id"] for row in candidates] == sorted(row["question_id"] for row in candidates)
    assert all(row["review_decision"] is None for row in candidates)
    assert all(row["source_contract"]["promotion_allowed"] is False for row in candidates)
    assert response["decision"] is None
    assert response["promotion_allowed"] is False
    assert response["blocking_issue_count"] == 22
    issues = [json.loads(line) for line in Path(result["outputs"]["entity_role_issue_briefs"]["path"]).read_text(encoding="utf-8").splitlines()]
    assert len(issues) == 22
    assert any(issue["question_id"] == 211 and issue["strict_status"] == "UNRESOLVED" for issue in issues)


def test_rejects_unlocked_or_overwritten_campaign(tmp_path: Path) -> None:
    run = json.loads(RUN.read_text(encoding="utf-8"))
    run["reproducibility"].pop("answer_certificates_match")
    unlocked = tmp_path / "unlocked.json"
    unlocked.write_text(json.dumps(run), encoding="utf-8")
    with pytest.raises(ValueError, match="fully locked"):
        MODULE.build(run_receipt=unlocked, output_dir=tmp_path / "unlocked-output", review_bundle=REVIEW_BUNDLE)

    output = tmp_path / "existing"
    output.mkdir()
    with pytest.raises(FileExistsError):
        MODULE.build(run_receipt=RUN, output_dir=output, review_bundle=REVIEW_BUNDLE)
