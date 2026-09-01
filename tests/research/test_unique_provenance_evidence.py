from __future__ import annotations
from finance_query.research.unique_provenance_evidence import _record, evaluate_unique_provenance_full_reference, evaluate_unique_provenance_stage, select_unique_compatible
def record(*, same_meta_negative: bool=False) -> dict:
    hn={"text":"negative","text_sha1":"hn","ticker":"BBB","year":"2025","doc_type":"10-K"}
    if same_meta_negative: hn["ticker"]="AAA"
    return {"id":"id","ticker":"AAA","year":2025,"doc_type":"10-K","topic":"Financials","reasoning_type":"Quantitative","difficulty":"Easy","passage_type":"Single-Passage","question":"What was revenue?","passages":[{"text":"gold","text_sha1":"gold"}],"hard_negatives":[hn]}
def test_unique_exact_compatibility_selects_gold_and_ambiguity_abstains() -> None:
    assert select_unique_compatible(record())["gold"] is True
    assert select_unique_compatible(record(same_meta_negative=True)) is None
def test_stage_reports_safe_improvement() -> None:
    r=record(); seed="seed"; x=_record(r,"development",seed); a={"benchmark_stage":"development","record_sha256":x["record_sha256"],"source_record_tracking_hash":"track","metric_family":x["metric_family"],"composition_signature":x["composition_signature"],"source_contract":{"research_only":True}}
    rows,report=evaluate_unique_provenance_stage(assignments=[a],records=[r],stage="development",seed=seed)
    assert rows[0]["candidate_outcome"]=="IMPROVED"; assert report["arms"]["B_A_plus_ticker_year_form_unique_gate"]["false_confident_count"]==0
def test_full_reference_keeps_unique_compatibility_precision() -> None:
    rows,report=evaluate_unique_provenance_full_reference(records=[record(),record(same_meta_negative=True)],seed="seed");assert len(rows)==2;candidate=report["arms"]["B_A_plus_ticker_year_form_unique_gate"];assert candidate["predicted_count"]==1;assert candidate["precision_on_predictions"]==1.0;assert candidate["false_confident_count"]==0
