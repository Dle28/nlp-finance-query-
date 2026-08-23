from __future__ import annotations

from finance_query.person1_review_plan import build_person1_review_plan


def test_entity_and_year_reviews_stay_rejected() -> None:
    metadata = [
        {"queue_kind": "entity_metadata", "immutable_source_identity": {"question_id": 104, "stage_id": "stage_1_profit_before_tax", "role": "profit_before_tax"}},
        {"queue_kind": "year_metadata", "immutable_source_identity": {"question_id": 819, "stage_id": "year_metadata", "role": "credit_loss_provision"}},
    ]
    plans = build_person1_review_plan(metadata_queue=metadata, period_queue=[])
    assert [plan["decision"] for plan in plans] == ["reject_repair", "reject_repair"]
    assert all(plan["proposed_patch"] is None for plan in plans)


def test_bluemarq_conflict_stays_uncertain() -> None:
    metadata = [{"queue_kind": "sector_metadata", "immutable_source_identity": {"question_id": 176, "stage_id": "stage_1_net_revenue", "role": "net_revenue"}}]
    plan = build_person1_review_plan(metadata_queue=metadata, period_queue=[])[0]
    assert plan["decision"] == "uncertain"
    assert plan["proposed_patch"] is None
