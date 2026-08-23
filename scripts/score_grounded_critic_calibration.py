#!/usr/bin/env python3
"""Score independent critic labels; promote no stratum without every policy gate."""
from __future__ import annotations
import argparse,json,hashlib
from pathlib import Path
from collections import defaultdict
from finance_query.critic_calibration_v2 import (
    human_reviewed_audit_metrics,
    score_stratum,
)

def sha(p:Path): return hashlib.sha256(p.read_bytes()).hexdigest()
def main() -> None:
 p=argparse.ArgumentParser(); p.add_argument("--items",type=Path,required=True); p.add_argument("--items-manifest",type=Path,required=True); p.add_argument("--output-dir",type=Path,required=True); p.add_argument("--minimum-sample-size",type=int,default=30); a=p.parse_args()
 m=json.loads(a.items_manifest.read_text());
 if sha(a.items)!=m["outputs"]["items"]["sha256"]: raise ValueError("SHA-256 mismatch for calibration items")
 items=[json.loads(x) for x in a.items.read_text().splitlines() if x]; buckets=defaultdict(list)
 for item in items: buckets[json.dumps(item["stratum"],sort_keys=True)].append(item)
 policy={"minimum_sample_size":a.minimum_sample_size,"minimum_source_coordinate_agreement":.99,"minimum_unit_period_agreement":.99,"minimum_accept_precision":.99,"maximum_false_acceptance_rate":.01,"minimum_abstention_correctness":.95,"minimum_deterministic_replay_agreement":1.,"maximum_unsupported_evidence_rate":0.}
 scores=[]; unresolved=[]; all_labelled=[]
 for key,vals in sorted(buckets.items()):
  labelled=[]
  for item in vals:
   label=item.get("independent_label")
   if not item.get("calibration_eligible"): continue
   if not isinstance(label,dict) or label.get("provenance") not in {"human_verified","independent_ai_source_review"}: raise ValueError("Calibration item has invalid independent label provenance")
   if label.get("status") not in {"accept","reject","abstain"}: raise ValueError("Calibration item has invalid independent label status")
   contract=label.get("source_contract") or {}
   if any(contract.get(k) is not False for k in ("evidence_eligible","training_eligible","submission_eligible","promotion_allowed")): raise ValueError("Calibration label violates non-promotable source contract")
   labelled.append({"label_provenance":label["provenance"],"critic_status":item["critic_result"].get("status"),"independent_status":label["status"],"source_coordinate_agree":label.get("source_coordinate_agree"),"unit_period_agree":label.get("unit_period_agree"),"deterministic_replay_agree":label.get("deterministic_replay_agree"),"unsupported_evidence":label.get("unsupported_evidence")})
  all_labelled.extend(labelled)
  score={"stratum":json.loads(key),**score_stratum(labelled,policy)}
  score["agreement"]=None if not labelled else sum(row["critic_status"]==row["independent_status"] for row in labelled)/len(labelled)
  scores.append(score); unresolved.extend({"question_id":x["question_id"],"state":"machine_provisional" if x["critic_result"].get("provenance")=="machine_provisional" else "needs_human","reason":"INSUFFICIENT_INDEPENDENT_CALIBRATION","stratum":x["stratum"],"source_contract":{"evidence_eligible":False,"training_eligible":False,"submission_eligible":False,"promotion_allowed":False}} for x in vals)
 a.output_dir.mkdir(parents=True,exist_ok=True); scoresp=a.output_dir/"calibration_scores_v1.json"; promoted=a.output_dir/"promoted_strata_v1.jsonl"; unresolvedp=a.output_dir/"unresolved_review_queue_v1.jsonl"; scoresp.write_text(json.dumps({"policy_candidate":policy,"strata":scores,"promotion_claim":"none","source_contract":{"evidence_eligible":False,"training_eligible":False,"submission_eligible":False,"promotion_allowed":False},"training_quality_metrics":{"metric_protocol":"human_verified_independent_accept_precision_wilson_v1",**human_reviewed_audit_metrics(all_labelled)}},ensure_ascii=False,sort_keys=True,indent=2)+"\n"); promoted.write_text(""); unresolvedp.write_text("".join(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n" for x in sorted(unresolved,key=lambda x:x["question_id"])))
 manifest={"protocol":"grounded_critic_calibration_v1","inputs":{"items":{"path":str(a.items),"sha256":sha(a.items)},"items_manifest":{"path":str(a.items_manifest),"sha256":sha(a.items_manifest)}},"outputs":{"scores":{"path":str(scoresp),"sha256":sha(scoresp)},"promoted":{"path":str(promoted),"sha256":sha(promoted)},"unresolved":{"path":str(unresolvedp),"sha256":sha(unresolvedp)}},"counts":{"item_count":len(items),"promoted_strata":0,"unresolved_count":len(unresolved)},"source_contract":{"evidence_eligible":False,"training_eligible":False,"submission_eligible":False,"promotion_allowed":False}}; (a.output_dir/"grounded_critic_calibration_v1.manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2)+"\n")
if __name__=="__main__": main()
