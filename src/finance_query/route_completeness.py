"""Whole-question operation audit layered over immutable route candidates V1."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from .financial_taxonomy import normalize_label
from .questions import infer_entity_role


ROUTE_COMPLETENESS_PROTOCOL = "route_completeness_overlay_v3"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def requested_output_unit(question: str) -> dict[str, Any]:
    """Parse only explicit question wording; never infer a requested unit."""
    text = normalize_label(question)
    raw = " ".join(str(question).casefold().split())
    entries = (
        ("nghin ty dong", "currency", "nghin_ty_dong", "1000000000000"),
        ("tram ty dong", "currency", "tram_ty_dong", "100000000000"),
        ("ty dong", "currency", "ty_dong", "1000000000"),
        ("trieu dong", "currency", "trieu_dong", "1000000"),
        ("nghin dong", "currency", "nghin_dong", "1000"),
        ("vnd", "currency", "vnd", "1"),
        ("dong", "currency", "vnd", "1"),
        ("phan tram", "percent", "percent", None),
        ("%", "percent", "percent", None),
        ("lan", "times", "times", None),
    )
    for phrase, kind, unit, divisor in entries:
        raw_phrase = (phrase.replace("nghin", "nghìn").replace("tram", "trăm").replace("ty", "tỷ").replace("trieu", "triệu").replace("dong", "đồng").replace("phan", "phần").replace("lan", "lần"))
        if phrase == "%" and "%" in question:
            return {"kind": kind, "unit": unit, "vnd_to_output_divisor": divisor, "source": "literal_question"}
        if phrase == "vnd" and re.search(r"(?<!\w)vnd(?!\w)", raw):
            return {"kind": kind, "unit": unit, "vnd_to_output_divisor": divisor, "source": "literal_question"}
        if phrase != "%" and phrase != "vnd" and re.search(rf"(?<!\w){re.escape(raw_phrase)}(?!\w)", raw):
            return {"kind": kind, "unit": unit, "vnd_to_output_divisor": divisor, "source": "literal_question"}
    return {"kind": "source_unit", "unit": "source_unit", "vnd_to_output_divisor": None, "source": "no_explicit_unit"}


def operation_requirements(question: str, plan: Mapping[str, Any]) -> list[str]:
    text = normalize_label(question)
    required = {"reported_value"}
    def has(*phrases: str) -> bool:
        return any(re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) for phrase in phrases)
    if has("ty le", "tren", "phan tram", "%"): required.add("ratio_or_percent")
    if has(
        "tru di",
        "chenh lech",
        "hieu so",
        "lon hon",
        "be hon",
        "nho hon",
        "nhieu hon",
        "it hon",
        "cao hon",
        "thap hon",
        "vuot",
    ):
        required.add("subtract_or_difference")
    if has("trung binh", "binh quan", "trung vi"): required.add("average_or_median")
    if has("cao nhat", "thap nhat", "top", "xep hang"): required.add("min_max_ranking")
    if has("tang truong", "toc do tang", "so voi nam lien truoc"): required.add("year_over_year_growth")
    # ``dương`` is a sign predicate only when it names a sign.  A plain token
    # match also occurs in the common financial row label ``tiền và các khoản
    # tương đương tiền``.  Treating that label as a positivity filter blocks a
    # direct lookup before any source evidence can be inspected.  The previous
    # token check is deliberately narrow: it removes only the fixed idiom,
    # while retaining explicit predicates such as ``giá trị dương`` and
    # ``tăng trưởng dương``.
    sign_match = has("am", "lon hon 0", "nho hon 0")
    for match in re.finditer(r"(?<!\w)duong(?!\w)", text):
        prefix = text[: match.start()].rstrip()
        if prefix.rsplit(" ", 1)[-1:] != ["tuong"]:
            sign_match = True
            break
    if sign_match:
        required.add("positive_negative_filter")
    if len(plan.get("tickers") or []) > 1: required.add("multi_company_population")
    years = list(plan.get("years") or [])
    if len(years) > 1 or has("giai doan", "ca ba nam", "tu nam"): required.add("multi_year_range")
    # A planner family is routing metadata, not proof that multiple stage
    # outputs must be composed.  In particular, literal rows such as "Tổng
    # cộng tài sản" were previously misclassified as aggregations solely from
    # ``plan.family``.  Require an explicit operation/dimension whose semantics
    # necessarily consume a population or more than one stage output.
    composed_operations = {
        "subtract_or_difference",
        "average_or_median",
        "min_max_ranking",
        "year_over_year_growth",
        "multi_company_population",
        "multi_year_range",
        "positive_negative_filter",
    }
    family_requires_review = plan.get("family") in {
        "conditional_analytical",
        "multi_entity_or_period_aggregation",
        "cross_entity_comparison",
    }
    literal_single_total = (
        has("tong cong")
        and required == {"reported_value"}
        and len(plan.get("tickers") or []) <= 1
        and len(years) <= 1
    )
    if required.intersection(composed_operations) or (
        family_requires_review and not literal_single_total
    ):
        required.add("stage_output_dependency")
    if has("lam tron", "chu so thap phan"): required.add("requested_rounding")
    return sorted(required)


def route_coverage(route: Mapping[str, Any], requirements: Sequence[str]) -> list[str]:
    stages = list(route.get("stages") or [])
    metrics = [str(stage.get("metric_id") or "") for stage in stages]
    covered = {"reported_value"} if stages else set()
    if any(metric in {"quick_ratio", "current_ratio", "gross_profit_margin", "net_profit_margin", "interest_coverage", "loan_to_deposit"} for metric in metrics): covered.add("ratio_or_percent")
    if len(stages) > 1: covered.add("stage_output_dependency")
    # V1 route metadata has no controlled operator graph for the remaining
    # whole-question operations. It must not claim them implicitly.
    return sorted(covered.intersection(requirements))


def _contract() -> dict[str, bool]:
    return {"navigation_metadata_only": True, "evidence_eligible": False, "training_eligible": False, "submission_eligible": False, "promotion_allowed": False, "may_execute_formula": False, "may_select_value": False}


def build_route_completeness_overlay(*, questions_path: Path, routes_path: Path, routes_manifest_path: Path, output: Path) -> dict[str, Any]:
    manifest = json.loads(routes_manifest_path.read_text(encoding="utf-8"))
    # The original route-candidate manifest used ``output`` while bounded route
    # packets use ``outputs.packets``.  Both are valid route inputs, but a
    # lineage edge is accepted only when the corresponding output hash matches.
    expected = (manifest.get("output") or {}).get("sha256")
    if not isinstance(expected, str):
        expected = ((manifest.get("outputs") or {}).get("packets") or {}).get("sha256")
    if not isinstance(expected, str) or sha256_file(routes_path) != expected:
        raise ValueError("SHA-256 mismatch for route input")
    questions = [json.loads(line) for line in questions_path.read_text(encoding="utf-8").splitlines() if line]
    routes = [json.loads(line) for line in routes_path.read_text(encoding="utf-8").splitlines() if line]
    qmap = {int(item.get("id") or item.get("question_id") or 0): item for item in questions}; rmap = {int(item.get("question_id") or 0): item for item in routes}
    if len(qmap) != 1012 or len(rmap) != 1012 or set(qmap) != set(rmap): raise ValueError("Question/route V1 coverage mismatch")
    records=[]
    for qid in sorted(qmap):
        item, route = qmap[qid], rmap[qid]; plan = item.get("effective_question_plan") or item.get("question_plan") or {}
        required=operation_requirements(str(item.get("question") or ""), plan); covered=route_coverage(route,required); missing=sorted(set(required)-set(covered))
        context_missing=[code for code in route.get("reason_codes") or [] if str(code).startswith("MISSING_")]
        complete=not missing and not context_missing and route.get("route_status") not in {"abstain"}
        status="route_complete" if complete else ("composed_execution_required" if "stage_output_dependency" in required else "route_incomplete")
        context = dict(route.get("question_context") or {})
        entity_role = infer_entity_role(str(item.get("question") or ""))
        if entity_role:
            context["entity_role"] = entity_role
            context["entity_role_source"] = "explicit_question_literal"
        records.append({"schema_version":3,"protocol":ROUTE_COMPLETENESS_PROTOCOL,"question_id":qid,"route_v1_status":route.get("route_status"),"route_status":status,"question":item.get("question"),"question_context":context,"required_operations":required,"covered_operations":covered,"missing_operations":missing,"requested_output_unit":requested_output_unit(str(item.get("question") or "")),"reason_codes":sorted(set(context_missing+(["WHOLE_QUESTION_OPERATION_UNCOVERED"] if missing else []))),"downstream_execution_eligible":False,"source_route_sha256":sha256_file(routes_path),"source_contract":_contract()})
    output.parent.mkdir(parents=True,exist_ok=True); output.write_text("".join(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n" for x in records),encoding="utf-8")
    result={"schema_version":3,"protocol":ROUTE_COMPLETENESS_PROTOCOL,"inputs":{"questions":{"path":str(questions_path),"sha256":sha256_file(questions_path)},"routes_v1":{"path":str(routes_path),"sha256":sha256_file(routes_path)},"routes_manifest_v1":{"path":str(routes_manifest_path),"sha256":sha256_file(routes_manifest_path)}},"outputs":{"overlay":{"path":str(output),"sha256":sha256_file(output)}},"counts":{"question_count":len(records),"route_status_counts":dict(sorted(Counter(x["route_status"] for x in records).items())),"requested_unit_counts":dict(sorted(Counter(x["requested_output_unit"]["unit"] for x in records).items()))},"source_contract":_contract()}
    mp=output.with_suffix(".manifest.json"); mp.write_text(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8"); return {**result,"manifest_path":str(mp)}
