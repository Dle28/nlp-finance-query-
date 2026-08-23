from finance_query.person2_routing_reviews import review_routing_item


def test_metadata_provisional_routing_item_is_rejected_without_role_promotion() -> None:
    uid = "u"
    item = {"queue_kind": "LOANS_TO_CUSTOMERS_ROUTING_REVIEW", "exclusive_primary_cause": "ROUTING_ELIGIBILITY", "immutable_source_identity": {"question_id": 1, "stage_id": "s", "role": "x"}, "nearby_exact_concept_candidates": [{"internal_table_uid": uid, "document_id": "d", "row_index": 0, "raw_source_row": ["Cho vay khách hàng"], "gate_vector": {"navigation_gate": {"observed": "blocked"}}}]}
    v2 = {uid: {"document_id": "d", "rows": [["Cho vay khách hàng"]]}}
    v3 = {uid: {"document_id": "d", "grid": {"rectangular": True, "provenance_complete": True, "width": 1, "reason_codes": []}, "row_profiles": [{"row_index": 0}]}}
    routing = {uid: {"document_id": "d", "table_type_status": "metadata_provisional", "routing_eligible": False}}
    review = review_routing_item(item, v2_by_uid=v2, v3_by_uid=v3, routing_by_uid=routing, completed_at_utc="2026-08-12T00:00:00Z")
    assert review["decision"] == "reject"
    assert review["table_role_correct"] is None
    assert review["navigation_eligible"] is False
    assert review["source_contract"]["promotion_allowed"] is False
