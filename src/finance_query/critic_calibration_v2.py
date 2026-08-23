"""Independent-label calibration metrics and conservative stratum promotion."""
from __future__ import annotations
from math import sqrt
from typing import Any,Mapping,Sequence

def wilson(success:int,n:int,z:float=1.96)->tuple[float,float]|None:
 if not n:return None
 p=success/n;d=1+z*z/n;c=(p+z*z/(2*n))/d;m=z*sqrt(p*(1-p)/n+z*z/(4*n*n))/d;return c-m,c+m
def ratio(numer:int,denom:int)->float|None:return None if not denom else numer/denom


def human_reviewed_audit_metrics(items: Sequence[Mapping[str, Any]]) -> dict[str, int | float | None]:
    """Measure accept precision only from independently human-verified labels."""
    human_rows = [row for row in items if row.get("label_provenance") == "human_verified"]
    accepts = [row for row in human_rows if row.get("critic_status") == "accept"]
    accepted_correct = sum(
        row.get("independent_status") == "accept" for row in accepts
    )
    interval = wilson(accepted_correct, len(accepts))
    return {
        "independent_audit_sample_size": len(human_rows),
        "independent_audit_accept_count": len(accepts),
        "independent_audit_accepted_correct_count": accepted_correct,
        "independent_audit_precision": ratio(accepted_correct, len(accepts)),
        "audit_ci95_lower": interval[0] if interval else None,
        "audit_ci95_upper": interval[1] if interval else None,
    }


def score_stratum(items:Sequence[Mapping[str,Any]],policy:Mapping[str,Any])->dict[str,Any]:
 labelled=[x for x in items if x.get("label_provenance") in {"human_verified","independent_ai_source_review"}]
 n=len(labelled); critic=[x.get("critic_status") for x in labelled]; truth=[x.get("independent_status") for x in labelled]
 accepts=[i for i,x in enumerate(critic) if x=="accept"];rejects=[i for i,x in enumerate(critic) if x=="reject"];abstains=[i for i,x in enumerate(critic) if x=="abstain"]
 source=sum(bool(x.get("source_coordinate_agree")) for x in labelled);unit=sum(bool(x.get("unit_period_agree")) for x in labelled);replay=sum(bool(x.get("deterministic_replay_agree")) for x in labelled);unsupported=sum(bool(x.get("unsupported_evidence")) for x in labelled)
 metrics={"sample_size":n,"source_coordinate_agreement":ratio(source,n),"unit_period_agreement":ratio(unit,n),"accept_precision":ratio(sum(truth[i]=="accept" for i in accepts),len(accepts)),"false_acceptance_rate":ratio(sum(truth[i]!="accept" for i in accepts),len(accepts)),"reject_precision":ratio(sum(truth[i]=="reject" for i in rejects),len(rejects)),"abstention_correctness":ratio(sum(truth[i]=="abstain" for i in abstains),len(abstains)),"deterministic_replay_agreement":ratio(replay,n),"unsupported_evidence_rate":ratio(unsupported,n),"source_coordinate_wilson":wilson(source,n)}
 required={"sample_size":policy["minimum_sample_size"],"source_coordinate_agreement":policy["minimum_source_coordinate_agreement"],"unit_period_agreement":policy["minimum_unit_period_agreement"],"accept_precision":policy["minimum_accept_precision"],"false_acceptance_rate":policy["maximum_false_acceptance_rate"],"abstention_correctness":policy["minimum_abstention_correctness"],"deterministic_replay_agreement":policy["minimum_deterministic_replay_agreement"],"unsupported_evidence_rate":policy["maximum_unsupported_evidence_rate"]}
 promote=n>=required["sample_size"] and all(metrics[k] is not None and metrics[k]>=required[k] for k in ("source_coordinate_agreement","unit_period_agreement","accept_precision","abstention_correctness","deterministic_replay_agreement")) and metrics["false_acceptance_rate"] is not None and metrics["false_acceptance_rate"]<=required["false_acceptance_rate"] and metrics["unsupported_evidence_rate"] is not None and metrics["unsupported_evidence_rate"]<=required["unsupported_evidence_rate"]
 return {**metrics,"promotion_recommendation":"promote" if promote else "needs_human"}
