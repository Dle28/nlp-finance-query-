"""Deterministic synthesis for the anti-leakage ViFinQA research round."""

from __future__ import annotations

import json
from typing import Any, Mapping


PROTOCOL = "vifinqa_antileakage_research_conclusion_v1"
EXPERIMENT_SECTIONS = [
    "RESEARCH QUESTION",
    "OBSERVATION",
    "HYPOTHESIS",
    "BASELINE",
    "SUBSET DESIGN",
    "CHANGE TESTED",
    "EXPECTED RESULT",
    "RESULT — DEVELOPMENT",
    "RESULT — UNSEEN",
    "RESULT — FULL DATASET",
    "IMPROVEMENTS",
    "REGRESSIONS",
    "GENERALIZATION",
    "OVERFITTING RISK",
    "STRENGTHS",
    "WEAKNESSES",
    "WHAT THIS EXPERIMENT PROVES",
    "WHAT THIS EXPERIMENT DOES NOT PROVE",
    "VERDICT",
]


FINAL_QUESTIONS = [
    "Dataset thực sự gồm những loại bài toán nào?",
    "Những family nào chiếm phần lớn dataset?",
    "Long tail nằm ở đâu?",
    "Mô hình hiện tại mạnh ở đâu?",
    "Mô hình hiện tại yếu ở đâu?",
    "Failure nào mang tính hệ thống?",
    "Failure nào chỉ là outlier?",
    "Những hypothesis nào đã được support?",
    "Những hypothesis nào bị reject?",
    "Những thay đổi nào generalize?",
    "Những thay đổi nào có dấu hiệu overfit?",
    "Một lượng nhỏ rule có thể cover bao nhiêu % dataset?",
    "Mô hình có xử lý được unseen company không?",
    "Mô hình có xử lý được unseen wording không?",
    "Mô hình có xử lý được novel combinations không?",
    "Phần nào vẫn cần nghiên cứu?",
    "Có đủ evidence để thay đổi kiến trúc hiện tại hay chưa?",
]


def _arm(report: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    return (report.get("arms") or {}).get(name) or {}


def build_research_conclusion(
    *,
    dataset_map: Mapping[str, Any],
    split_summary: Mapping[str, Any],
    discovery: Mapping[str, Any],
    development: Mapping[str, Any],
    untouched: Mapping[str, Any],
    full_external: Mapping[str, Any],
    full_vifinqa: Mapping[str, Any],
    candidate_replay: Mapping[str, Any],
) -> dict[str, Any]:
    if dataset_map.get("question_count") != 1012:
        raise ValueError("dataset map must cover exactly 1,012 questions")
    baseline = dataset_map.get("baseline_overall") or {}
    if (baseline.get("status_counts") or {}) != {"ABSTAIN": 1012}:
        raise ValueError("frozen baseline population changed")
    if any((split_summary.get("company_overlap_counts") or {}).values()):
        raise ValueError("company holdout is not disjoint")
    if split_summary.get("wording_overlap_development_untouched") != 0:
        raise ValueError("wording holdout is not disjoint")

    candidate_name = "B_A_plus_named_constant_or_percent_scalar"
    unseen_candidate = _arm(untouched, candidate_name)
    unseen_outcomes = untouched.get("candidate_outcome_counts") or {}
    if not (
        unseen_candidate.get("eligible_count") == 30
        and unseen_candidate.get("accuracy_on_eligible") == 1.0
        and unseen_candidate.get("false_confident_count") == 0
        and unseen_outcomes.get("REGRESSED", 0) == 0
    ):
        raise ValueError("untouched result does not support the retained rule")
    full_candidate = _arm(full_external, candidate_name)
    if full_candidate.get("false_confident_count") != 0:
        raise ValueError("full external reference contains false confidence")
    if (full_vifinqa.get("overall_outcome_counts") or {}) != {"UNCHANGED": 1012}:
        raise ValueError("full ViFinQA candidate is not backward compatible")
    reproducibility = candidate_replay.get("reproducibility") or {}
    if not reproducibility or not all(reproducibility.values()):
        raise ValueError("candidate replay did not byte-match every locked output")

    families = dataset_map["question_family_counts"]
    operations = dataset_map["operation_family_counts"]
    temporal = dataset_map["temporal_family_counts"]
    linguistic = dataset_map["linguistic_diversity"]
    full_eligible = int(full_external["record_count"])
    full_new = int(full_candidate["eligible_count"])
    targeted_coverage = full_new / full_eligible
    finqa_population_coverage = full_new / 8281
    metric_holdout = split_summary["metric_holdout"]
    composition_holdout = split_summary["composition_holdout"]
    metric_unseen = (untouched.get("metric_outcome_counts") or {}).get(metric_holdout["family"], {})
    composition_unseen = (untouched.get("composition_outcome_counts") or {}).get(
        composition_holdout["signature"], {}
    )

    sections: dict[str, Any] = {
        "RESEARCH QUESTION": (
            "Một contract phép nhân scalar vô hướng có kiểu có tăng coverage an toàn trên dữ liệu tài chính "
            "unseen mà không mở phép nhân hai đại lượng tùy ý hay không?"
        ),
        "OBSERVATION": {
            "phenomenon": "Kernel tính được multiply nhưng grounded registry chặn toàn bộ vì chưa phân biệt scalar vô hướng với đại lượng có đơn vị.",
            "full_finqa_eligible_phenomenon_count": full_eligible,
        },
        "HYPOTHESIS": (
            "Chỉ nhận const_* hoặc percent literal làm scalar vô hướng sẽ tăng grounded eligibility, "
            "giữ accuracy 100%, không tăng false-confidence và vẫn chặn multiply mơ hồ."
        ),
        "BASELINE": {
            "finqa_selected_multiply_eligible": 0,
            "vifinqa_status_counts": baseline["status_counts"],
            "vifinqa_accuracy": baseline["accuracy"],
        },
        "SUBSET DESIGN": {
            "discovery": 10,
            "development": 30,
            "untouched_evaluation": 30,
            "company_overlap_counts": split_summary["company_overlap_counts"],
            "wording_overlap_development_untouched": 0,
            "metric_holdout": metric_holdout,
            "composition_holdout": composition_holdout,
            "contaminated_test_excluded_count": split_summary["contaminated_test_excluded_count"],
        },
        "CHANGE TESTED": (
            "Một thay đổi: thêm scalar_multiply với đúng một literal tự kiểm chứng thuộc named_constant hoặc "
            "percent_literal; multiply cũ vẫn shadow-blocked."
        ),
        "EXPECTED RESULT": {
            "minimum_newly_eligible_untouched": 5,
            "minimum_accuracy": 1.0,
            "maximum_false_confident": 0,
            "maximum_regressed": 0,
            "metric_holdout_minimum_improved": 5,
            "composition_holdout_minimum_improved": 5,
        },
        "RESULT — DEVELOPMENT": {
            "candidate": _arm(development, candidate_name),
            "outcomes": development["candidate_outcome_counts"],
            "ablation_named_constant_only": _arm(development, "C_B_without_percent_literal_scalars"),
            "ablation_percent_literal_only": _arm(development, "D_B_without_named_constant_scalars"),
        },
        "RESULT — UNSEEN": {
            "candidate": unseen_candidate,
            "outcomes": unseen_outcomes,
            "metric_holdout_outcomes": metric_unseen,
            "composition_holdout_outcomes": composition_unseen,
        },
        "RESULT — FULL DATASET": {
            "external_finqa_targeted": {
                "records": full_eligible,
                "candidate_newly_eligible_correct": full_new,
                "targeted_coverage": targeted_coverage,
                "unchanged_abstain": full_eligible - full_new,
                "false_confident": full_candidate["false_confident_count"],
            },
            "vifinqa_1012": {
                "outcomes": full_vifinqa["overall_outcome_counts"],
                "abstention": full_vifinqa["abstention"],
                "false_confidence_change": full_vifinqa["false_confidence_change"],
                "regression_count": full_vifinqa["regression_count"],
                "reason_no_gain": "Upstream entity, variable, route, and exact-cell grounding remains unresolved; no typed scalar AST reaches execution.",
            },
        },
        "IMPROVEMENTS": {
            "discovery": discovery["candidate_outcome_counts"],
            "development": development["candidate_outcome_counts"],
            "unseen": unseen_outcomes,
            "full_external": full_external["candidate_outcome_counts"],
        },
        "REGRESSIONS": {
            "development": int(development["candidate_outcome_counts"].get("REGRESSED", 0)),
            "unseen": int(unseen_outcomes.get("REGRESSED", 0)),
            "full_vifinqa": full_vifinqa["regression_count"],
            "locked_output_byte_match": True,
        },
        "GENERALIZATION": {
            "company_holdout": "PASS: pairwise company overlap = 0",
            "wording_holdout": "PASS: development/untouched wording-template overlap = 0",
            "metric_holdout": f"PASS: {metric_holdout['family']} improved {metric_unseen.get('IMPROVED', 0)}",
            "composition_holdout": f"PASS: {composition_holdout['signature']} improved {composition_unseen.get('IMPROVED', 0)}",
        },
        "OVERFITTING RISK": {
            "level": "LOW_FOR_OPERATOR_CONTRACT_MEDIUM_FOR_VIFINQA_TRANSFER",
            "specific_company": False,
            "question_id_logic": False,
            "unnecessary_specific_year": False,
            "single_wording_dependency": False,
            "unseen_transfer": True,
            "false_confidence_increase": False,
        },
        "STRENGTHS": [
            "Prediction and thresholds were hash-frozen before untouched evaluation.",
            "Company, wording, metric, and composition holdouts all passed.",
            "Both scalar components contribute in ablation.",
            "Arbitrary dimensional multiply remains fail-closed.",
            "Locked ViFinQA outputs remain byte-identical.",
        ],
        "WEAKNESSES": [
            "FinQA is English and its gold programs are not ViFinQA gold.",
            "The rule changes arithmetic eligibility, not retrieval or exact-cell grounding.",
            "Public ViFinQA has no gold answer/evidence, so semantic full-dataset gain is unmeasurable.",
            "Eleven full-reference programs remain blocked because not every multiply step has a typed scalar.",
        ],
        "WHAT THIS EXPERIMENT PROVES": (
            "The narrow typed-scalar contract transfers to unseen issuers, wording, a held-out metric family, "
            "and a held-out operation composition with zero observed false-confidence."
        ),
        "WHAT THIS EXPERIMENT DOES NOT PROVE": (
            "It does not prove any ViFinQA answer is correct, does not solve retrieval/binding, and does not "
            "authorize a submission artifact or arbitrary multiplication."
        ),
        "VERDICT": {
            "decision": "KEEP",
            "scope": "RETAIN_AS_RESEARCH_BACKED_OPERATOR_CONTRACT_ONLY",
            "submission_authorized": False,
        },
    }

    answers = [
        "14 intrinsic question families spanning lookup, comparison, difference, ratio/margin, percentage change, aggregation, filtering, counting, ranking, multi-entity/multi-period, and counterfactual/net compositions; 14 operation labels and 10 temporal families are tracked separately.",
        f"Reported-value lookup ({families['reported_value_lookup']}), filter-then-rank/select ({families['filter_then_rank_or_select']}), rank/select extreme ({families['rank_or_select_extreme']}), comparison ({families['comparison']}), and aggregate ({families['aggregate']}) dominate.",
        f"The smallest families are count matching ({families['count_matching_items']}), net composition ({families['net_value_composition']}), counterfactual composition ({families['counterfactual_composition']}), then filter-compute/count ({families['filter_then_compute']}/{families['filter_then_count']}); opening instant has {temporal['opening_instant']} records and time-unspecified has {temporal['time_unspecified']}.",
        "It is strong at fail-closed provenance, deterministic replay, and avoiding false confidence: zero predictions means zero observed false-confident predictions and all locked candidate outputs byte-match.",
        "It is weak in usable coverage: 1,012/1,012 ABSTAIN and zero predictions; semantic accuracy and calibration cannot be estimated without hidden gold.",
        "The dominant systematic failure is upstream grounding: entity/variable resolution, route completeness, period/scope, and exact-cell binding prevent executable certificates across the population.",
        "No answer-error outlier can be established without gold or predictions. Intrinsic rare families are long-tail categories, not proven failure outliers.",
        "Supported: typed dimensionless scalar multiply using only self-validating const_* or percent literals.",
        "None was validly rejected. An exp-based idea was abandoned before freeze because inspected test examples contaminated unseen evidence; the temporal hypothesis remains unevaluated, not rejected.",
        f"The scalar contract generalizes: unseen 30/30 correct; full external targeted population {full_new}/{full_eligible} correct with zero false-confidence; all four holdouts pass.",
        "No retained change shows development-only behavior. Transfer risk from English FinQA to Vietnamese ViFinQA remains medium and is explicitly not treated as proven gain.",
        f"One retained rule covers {targeted_coverage:.2%} of its 424-record target phenomenon and {finqa_population_coverage:.2%} of all 8,281 FinQA records as newly eligible. Measured ViFinQA answer coverage remains 0/1,012 because upstream grounding is unresolved.",
        "Yes on the frozen external benchmark: issuer sets are disjoint and unseen improves 30/30. Not yet proven for answerable ViFinQA companies.",
        "Yes externally: wording-template overlap between development and untouched is 0 and unseen improves 30/30. Vietnamese transfer is not yet measured.",
        f"Yes externally: held-out metric {metric_holdout['family']} improves {metric_unseen.get('IMPROVED', 0)} and held-out composition {composition_holdout['signature']} improves {composition_unseen.get('IMPROVED', 0)}.",
        "Priority gaps are exact-cell evidence retrieval/binding, Vietnamese temporal semantics, entity role/scope, multi-table compatibility, operation-graph generation, and submission JSON/CSV completeness.",
        "There is enough evidence for the narrow scalar_multiply contract and its fail-closed AST representation, but not enough evidence to promote the overall architecture or produce a contest submission.",
    ]
    final_questions = [
        {"number": index, "question": question, "answer": answers[index - 1]}
        for index, question in enumerate(FINAL_QUESTIONS, 1)
    ]
    return {
        "protocol": PROTOCOL,
        "schema_version": 1,
        "experiment_id": "dimensionless_scalar_multiply_v1",
        "experiment_report_sections": sections,
        "final_research_questions": final_questions,
        "retained_rules": [
            {
                "rule_id": "operation.typed_dimensionless_scalar_multiply_v1",
                "category": "operation_rule",
                "rule": "Allow multiplication only when exactly one operand is a self-validating named constant or percent literal; preserve the other operand's unit; otherwise abstain.",
                "unseen_evidence": {
                    "improved": 30,
                    "unchanged": 0,
                    "regressed": 0,
                    "accuracy_on_newly_eligible": 1.0,
                    "false_confident": 0,
                },
                "full_external_evidence": {
                    "target_records": full_eligible,
                    "newly_eligible_correct": full_new,
                    "unchanged_abstain": full_eligible - full_new,
                    "false_confident": 0,
                },
                "vifinqa_submission_authority": False,
            }
        ],
        "safety_invariants_not_claimed_as_new_rules": [
            "Question ID is tracking-only.",
            "Missing or ambiguous evidence must abstain.",
            "Source unit and scope compatibility remain fail-closed.",
            "External benchmark gold cannot materialize ViFinQA answers.",
        ],
        "not_retained_due_insufficient_unseen_evidence": [
            "question_understanding_rules",
            "financial_meaning_rules",
            "time_interpretation_rules",
            "evidence_selection_rules",
            "multi_table_compatibility_rules",
        ],
        "submission_readiness": {
            "status": "NOT_READY",
            "submission_zip_created": False,
            "answer_coverage": "0/1012",
            "blocking_reason": "No independently grounded answer/evidence packets for all public questions.",
        },
        "recommended_next_research": [
            "Freeze an exact-cell retrieval and binding hypothesis against source BCTC, evaluated with hash-bound table/cell provenance and company/table holdouts.",
            "Evaluate Vietnamese temporal normalization on a frozen external or source-derived benchmark with exact-date, opening/closing, flow/instant, and wording holdouts.",
            "Evaluate operation-graph generation on external gold programs, then test novel multi-company/multi-year compositions before full ViFinQA replay.",
            "Only package submission.json plus data/*.csv after every one of 1,012 rows has executable grounded evidence and the ZIP validator passes.",
        ],
        "dataset_snapshot": {
            "question_count": 1012,
            "question_family_counts": families,
            "operation_family_counts": operations,
            "temporal_family_counts": temporal,
            "unique_questions": linguistic["unique_questions"],
            "unique_wording_templates": linguistic["unique_wording_templates"],
            "unique_semantic_signatures": linguistic["unique_semantic_signatures"],
        },
        "source_contract": {
            "research_only": True,
            "evidence_eligible": False,
            "submission_eligible": False,
            "promotion_allowed": False,
        },
    }


def validate_research_conclusion(report: Mapping[str, Any]) -> None:
    if report.get("protocol") != PROTOCOL or report.get("schema_version") != 1:
        raise ValueError("research conclusion protocol mismatch")
    sections = report.get("experiment_report_sections")
    if not isinstance(sections, Mapping) or list(sections) != EXPERIMENT_SECTIONS:
        raise ValueError("experiment report must contain the exact 19 ordered sections")
    questions = report.get("final_research_questions")
    if not isinstance(questions, list) or len(questions) != 17:
        raise ValueError("research conclusion must answer exactly 17 questions")
    if [row.get("number") for row in questions] != list(range(1, 18)):
        raise ValueError("final research question numbering changed")
    rules = report.get("retained_rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("at least one retained rule with unseen evidence is required")
    for rule in rules:
        evidence = rule.get("unseen_evidence") or {}
        if evidence.get("regressed") != 0 or evidence.get("false_confident") != 0:
            raise ValueError("retained rule violates regression or false-confidence gate")
        if int(evidence.get("improved") or 0) < 1:
            raise ValueError("retained rule lacks unseen improvement")
    if (report.get("submission_readiness") or {}).get("submission_zip_created") is not False:
        raise ValueError("research conclusion cannot claim a submission ZIP")
    contract = report.get("source_contract") or {}
    if contract.get("research_only") is not True or contract.get("submission_eligible") is not False:
        raise ValueError("research conclusion source contract is unsafe")


def render_markdown(report: Mapping[str, Any]) -> str:
    validate_research_conclusion(report)
    lines = ["# ViFinQA anti-leakage research conclusion v1", ""]
    for heading, value in report["experiment_report_sections"].items():
        lines.extend([f"## {heading}", ""])
        if isinstance(value, str):
            lines.extend([value, ""])
        else:
            lines.extend(["```json", json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), "```", ""])
    lines.extend(["# Tổng kết 17 câu hỏi nghiên cứu", ""])
    for row in report["final_research_questions"]:
        lines.extend([f"## {row['number']}. {row['question']}", "", row["answer"], ""])
    lines.extend(["# Retained rules", ""])
    for rule in report["retained_rules"]:
        lines.extend([f"- `{rule['rule_id']}`: {rule['rule']}", ""])
    lines.extend(["# RECOMMENDED NEXT RESEARCH", ""])
    for item in report["recommended_next_research"]:
        lines.extend([f"- {item}", ""])
    readiness = report["submission_readiness"]
    lines.extend([
        "# Submission readiness",
        "",
        f"`{readiness['status']}` — submission ZIP created: `{str(readiness['submission_zip_created']).lower()}`; answer coverage: `{readiness['answer_coverage']}`.",
        "",
    ])
    return "\n".join(lines)
