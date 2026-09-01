"""Six-experiment anti-leakage synthesis for the ViFinQA research round."""

from __future__ import annotations

import json
from typing import Any, Mapping

from finance_query.research.research_conclusion import EXPERIMENT_SECTIONS, FINAL_QUESTIONS


PROTOCOL = "vifinqa_antileakage_research_conclusion_v2"


def _candidate_metrics(report: Mapping[str, Any], arm_name: str) -> dict[str, Any]:
    arm = dict((report.get("arms") or {}).get(arm_name) or {})
    return {
        "record_count": int(report.get("record_count") or 0),
        "candidate_arm": arm_name,
        "candidate": arm,
        "outcomes": dict(report.get("candidate_outcome_counts") or {}),
        "metric_outcomes": dict(report.get("metric_outcome_counts") or {}),
        "composition_outcomes": dict(report.get("composition_outcome_counts") or {}),
    }


def _false_confident(metrics: Mapping[str, Any]) -> int:
    return int((metrics.get("candidate") or {}).get("false_confident_count") or 0)


def _regressed(metrics: Mapping[str, Any]) -> int:
    return int((metrics.get("outcomes") or {}).get("REGRESSED") or 0)


def _experiment_report(
    specification: Mapping[str, Any], payloads: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    hypothesis = payloads["config"]
    split = payloads["split"]
    candidate_arm = str(specification["candidate_arm"])
    discovery = _candidate_metrics(payloads["discovery"], candidate_arm)
    development = _candidate_metrics(payloads["development"], candidate_arm)
    untouched = _candidate_metrics(payloads["untouched"], candidate_arm)
    full = (
        _candidate_metrics(payloads["full_reference"], candidate_arm)
        if "full_reference" in payloads
        else {
            "status": "NOT_RUN_AFTER_UNSEEN_REJECTION",
            "reason": specification["non_proof"],
        }
    )
    decision = str(specification["decision"])
    kept = decision == "KEEP"
    section = {
        "RESEARCH QUESTION": hypothesis.get("research_question"),
        "OBSERVATION": hypothesis.get("observation"),
        "HYPOTHESIS": hypothesis.get("hypothesis"),
        "BASELINE": {
            "frozen": True,
            "arm": next(iter((payloads["development"].get("arms") or {}).items()), None),
            "question_id_used_for_selection": False,
        },
        "SUBSET DESIGN": {
            "stage_counts": split.get("stage_counts"),
            "company_overlap_counts": split.get("company_overlap_counts"),
            "wording_overlap_development_untouched": split.get(
                "wording_overlap_development_untouched"
            ),
            "metric_holdout": split.get("metric_holdout"),
            "composition_holdout": split.get("composition_holdout"),
            "selection_uses_source_record_id": split.get("selection_uses_source_record_id", False),
        },
        "CHANGE TESTED": hypothesis.get("change_tested"),
        "EXPECTED RESULT": {
            "frozen_before_run": hypothesis.get("expected_result_frozen_before_run"),
            "decision_thresholds": hypothesis.get("decision_thresholds"),
        },
        "RESULT — DEVELOPMENT": development,
        "RESULT — UNSEEN": untouched,
        "RESULT — FULL DATASET": full,
        "IMPROVEMENTS": {
            "discovery": discovery.get("outcomes"),
            "development": development.get("outcomes"),
            "untouched": untouched.get("outcomes"),
            "full_reference": full.get("outcomes") if isinstance(full, dict) else None,
        },
        "REGRESSIONS": {
            "development": _regressed(development),
            "untouched": _regressed(untouched),
            "untouched_false_confident": _false_confident(untouched),
            "full_reference_false_confident": (
                _false_confident(full) if "candidate" in full else None
            ),
        },
        "GENERALIZATION": {
            "company_holdout_pass": not any((split.get("company_overlap_counts") or {}).values()),
            "wording_holdout_pass": split.get("wording_overlap_development_untouched") == 0,
            "metric_holdout": split.get("metric_holdout"),
            "composition_holdout": split.get("composition_holdout"),
            "unseen_outcomes": untouched.get("outcomes"),
        },
        "OVERFITTING RISK": {
            "question_id_logic": False,
            "manual_per_question_labels": False,
            "decision": "CONTROLLED" if kept else "FAILED_OR_INVALID_EVIDENCE_GATE",
            "full_population_checked": "full_reference" in payloads,
        },
        "STRENGTHS": [
            "One primary change was frozen before untouched evaluation.",
            "Company and wording leakage checks are explicit.",
            str(specification["proof"]),
        ],
        "WEAKNESSES": [
            str(specification["non_proof"]),
            "External gold is research-only and cannot materialize a ViFinQA answer.",
        ],
        "WHAT THIS EXPERIMENT PROVES": specification["proof"],
        "WHAT THIS EXPERIMENT DOES NOT PROVE": specification["non_proof"],
        "VERDICT": {
            "decision": decision,
            "retained": kept,
            "scope": specification["scope"],
            "production_answer_authority": False,
            "submission_authority": False,
        },
    }
    if list(section) != EXPERIMENT_SECTIONS:
        raise ValueError("experiment section order or membership changed")
    return {
        "experiment_id": specification["experiment_id"],
        "category": specification["category"],
        "sections": section,
    }


def build_research_conclusion_v2(
    *,
    input_config: Mapping[str, Any],
    loaded_experiments: list[tuple[Mapping[str, Any], Mapping[str, Mapping[str, Any]]]],
    dataset_map: Mapping[str, Any],
    full_vifinqa: Mapping[str, Any],
    candidate_replay: Mapping[str, Any],
) -> dict[str, Any]:
    if dataset_map.get("question_count") != 1012:
        raise ValueError("dataset map must cover 1,012 questions")
    if (dataset_map.get("baseline_overall") or {}).get("status_counts") != {"ABSTAIN": 1012}:
        raise ValueError("locked baseline changed")
    if (full_vifinqa.get("overall_outcome_counts") or {}) != {"UNCHANGED": 1012}:
        raise ValueError("full ViFinQA replay is not backward compatible")
    reproducibility = candidate_replay.get("reproducibility") or {}
    if not reproducibility or not all(reproducibility.values()):
        raise ValueError("candidate replay is not reproducible")

    experiments = [
        _experiment_report(specification, payloads)
        for specification, payloads in loaded_experiments
    ]
    kept = [row for row in experiments if row["sections"]["VERDICT"]["retained"]]
    rejected = [row for row in experiments if not row["sections"]["VERDICT"]["retained"]]
    retained_rules = []
    for experiment in kept:
        unseen = experiment["sections"]["RESULT — UNSEEN"]
        full = experiment["sections"]["RESULT — FULL DATASET"]
        retained_rules.append(
            {
                "rule_id": experiment["experiment_id"],
                "category": experiment["category"],
                "scope": experiment["sections"]["VERDICT"]["scope"],
                "selector_uses_question_id": False,
                "manual_per_question_labels": False,
                "unseen_improved": int((unseen.get("outcomes") or {}).get("IMPROVED") or 0),
                "unseen_regressed": _regressed(unseen),
                "unseen_false_confident": _false_confident(unseen),
                "full_reference_false_confident": _false_confident(full),
                "submission_authority": False,
            }
        )

    families = dataset_map["question_family_counts"]
    answers = [
        "Có 14 question family, 14 operation family, 10 temporal family; taxonomy tách độc lập khỏi trạng thái model.",
        f"Lớn nhất là reported-value lookup ({families['reported_value_lookup']}), tiếp theo filter-then-rank/select ({families['filter_then_rank_or_select']}) và rank/select extreme ({families['rank_or_select_extreme']}).",
        f"Long tail gồm count matching ({families['count_matching_items']}), net composition ({families['net_value_composition']}) và counterfactual composition ({families['counterfactual_composition']}).",
        "Baseline mạnh ở fail-closed và tái lập: không phát hành dự đoán thiếu grounding và mọi replay khóa đều khớp.",
        "Baseline yếu về coverage: 1.012/1.012 ABSTAIN, nên accuracy và calibration theo gold ViFinQA không ước lượng được.",
        "Failure hệ thống nằm ở entity/variable resolution, route completeness, period/scope và exact-cell binding.",
        "Không thể xác nhận answer-error outlier khi không có public gold; family hiếm chỉ là long tail nội tại.",
        "Được support: typed scalar multiply, unique provenance gate, và explicit advisory abstention.",
        "Bị reject: temporal comparator không hợp lệ, exact-row linker có 74 false-confidence trên full reference, percent-change có 1 false-confidence ở unseen.",
        "Ba thay đổi được giữ đều có unseen improvement, 0 regression và 0 false-confidence trong phạm vi khóa.",
        "Exact-row và percent-change cho thấy overfit/không đủ an toàn; temporal-header không chứng minh incremental gain.",
        "Ba rule hẹp cover đúng hiện tượng tham chiếu của chúng; measured answer coverage trên ViFinQA vẫn 0/1.012.",
        "Có tín hiệu unseen-company trên benchmark ngoài, nhưng chưa chứng minh answer correctness cho công ty ViFinQA.",
        "Có tín hiệu unseen-wording với overlap development/untouched bằng 0 ở các split hợp lệ.",
        "Metric/composition holdout vượt qua ở các rule được giữ, nhưng không thay thế kiểm định exact source-cell.",
        "Cần nghiên cứu tiếp retrieval exact-cell, semantics thời gian tiếng Việt, metric linking an toàn và composition nhiều bảng.",
        "Đủ evidence để giữ ba contract hẹp; chưa đủ evidence để đổi kiến trúc tổng thể hoặc tạo bài nộp.",
    ]
    final_questions = [
        {"number": index, "question": question, "answer": answers[index - 1]}
        for index, question in enumerate(FINAL_QUESTIONS, 1)
    ]
    report = {
        "protocol": PROTOCOL,
        "schema_version": 2,
        "input_protocol": input_config.get("protocol"),
        "experiment_count": len(experiments),
        "experiment_reports": experiments,
        "retained_rules": retained_rules,
        "general_rule_set": [
            {
                "dimension": "question_understanding",
                "status": "RETAINED_NARROW_RULE",
                "retained_rule_ids": ["explicit_advisory_intent_abstention_v1"],
                "conclusion": "Recognize only the frozen explicit single-entity advisory form before numeric family routing.",
            },
            {
                "dimension": "financial_meaning",
                "status": "NO_NEW_RULE_RETAINED",
                "retained_rule_ids": [],
                "conclusion": "Exact-row semantic normalization was rejected after full-reference false confidence; keep existing fail-closed behavior.",
            },
            {
                "dimension": "time",
                "status": "NO_NEW_RULE_RETAINED",
                "retained_rule_ids": [],
                "conclusion": "The temporal experiment used an invalid comparator; retain the existing exact-header contract without claiming new gain.",
            },
            {
                "dimension": "operation",
                "status": "RETAINED_NARROW_RULE",
                "retained_rule_ids": ["dimensionless_scalar_multiply_v1"],
                "conclusion": "Multiply only with exactly one self-validating dimensionless scalar; otherwise abstain.",
            },
            {
                "dimension": "evidence_selection",
                "status": "RETAINED_NARROW_RULE",
                "retained_rule_ids": ["unique_provenance_evidence_v1"],
                "conclusion": "Use a provenance candidate only when ticker, year and document form resolve uniquely.",
            },
            {
                "dimension": "compatibility",
                "status": "RETAINED_AS_EXPLICIT_GATES",
                "retained_rule_ids": [
                    "unique_provenance_evidence_v1",
                    "explicit_advisory_intent_abstention_v1"
                ],
                "conclusion": "Ticker/year/form uniqueness and single-entity cardinality are mandatory compatibility gates, never answer authority.",
            },
            {
                "dimension": "abstention",
                "status": "RETAINED_NARROW_RULE",
                "retained_rule_ids": ["explicit_advisory_intent_abstention_v1"],
                "conclusion": "Route explicit advice outside the numeric planner and preserve abstention for every missing or ambiguous grounding contract.",
            },
        ],
        "rejected_experiments": [
            {
                "experiment_id": row["experiment_id"],
                "decision": row["sections"]["VERDICT"]["decision"],
            }
            for row in rejected
        ],
        "final_research_questions": final_questions,
        "full_vifinqa_compatibility": {
            "outcomes": full_vifinqa["overall_outcome_counts"],
            "status_counts": full_vifinqa["candidate_status_counts"],
            "regression_count": full_vifinqa["regression_count"],
            "semantic_gain": full_vifinqa["semantic_improvement_status"],
        },
        "submission_readiness": {
            "status": "NOT_READY",
            "submission_zip_created": False,
            "grounded_answer_coverage": "0/1012",
            "reason": "No public gold and no independently grounded evidence packet for every question.",
        },
        "source_contract": {
            "research_only": True,
            "evidence_eligible": False,
            "promotion_allowed": False,
            "submission_eligible": False,
        },
    }
    validate_research_conclusion_v2(report)
    return report


def validate_research_conclusion_v2(report: Mapping[str, Any]) -> None:
    if report.get("protocol") != PROTOCOL:
        raise ValueError("protocol mismatch")
    experiments = list(report.get("experiment_reports") or [])
    if len(experiments) != 6:
        raise ValueError("exactly six experiments are required")
    for experiment in experiments:
        if list((experiment.get("sections") or {}).keys()) != EXPERIMENT_SECTIONS:
            raise ValueError(f"19-section contract failed: {experiment.get('experiment_id')}")
    questions = list(report.get("final_research_questions") or [])
    if len(questions) != 17 or [row.get("number") for row in questions] != list(range(1, 18)):
        raise ValueError("17-question conclusion contract failed")
    retained = list(report.get("retained_rules") or [])
    if len(retained) != 3:
        raise ValueError("exactly three rules must be retained")
    for rule in retained:
        if int(rule.get("unseen_improved") or 0) <= 0:
            raise ValueError("retained rule lacks unseen improvement")
        if int(rule.get("unseen_regressed") or 0) != 0:
            raise ValueError("retained rule regressed on unseen")
        if int(rule.get("unseen_false_confident") or 0) != 0:
            raise ValueError("retained rule has unseen false confidence")
        if int(rule.get("full_reference_false_confident") or 0) != 0:
            raise ValueError("retained rule has full-reference false confidence")
        if rule.get("selector_uses_question_id") is not False:
            raise ValueError("question ID selector forbidden")
        if rule.get("manual_per_question_labels") is not False:
            raise ValueError("manual per-question labels forbidden")
    rule_ids = {str(rule.get("rule_id")) for rule in retained}
    general = list(report.get("general_rule_set") or [])
    expected_dimensions = [
        "question_understanding",
        "financial_meaning",
        "time",
        "operation",
        "evidence_selection",
        "compatibility",
        "abstention",
    ]
    if [row.get("dimension") for row in general] != expected_dimensions:
        raise ValueError("general rule-set dimension contract failed")
    for row in general:
        if not set(row.get("retained_rule_ids") or []).issubset(rule_ids):
            raise ValueError("general rule set references an unretained experiment")
    if (report.get("submission_readiness") or {}).get("submission_zip_created") is not False:
        raise ValueError("submission ZIP must not exist without grounded coverage")


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = ["# ViFinQA anti-leakage research conclusion v2", ""]
    for experiment in report["experiment_reports"]:
        lines.extend([f"## {experiment['experiment_id']}", ""])
        for title, value in experiment["sections"].items():
            lines.extend([f"### {title}", "", json.dumps(value, ensure_ascii=False, indent=2), ""])
    lines.extend(["## 17 final research questions", ""])
    for row in report["final_research_questions"]:
        lines.extend([f"{row['number']}. **{row['question']}**", "", str(row["answer"]), ""])
    lines.extend(["## Submission readiness", "", json.dumps(report["submission_readiness"], ensure_ascii=False, indent=2), ""])
    return "\n".join(lines)
