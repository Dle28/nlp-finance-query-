#!/usr/bin/env python3
"""Freeze the Vietnamese percent-change decision gate before unseen scoring."""
from __future__ import annotations
import argparse,hashlib,json
from pathlib import Path
def sha(path:Path)->str:return hashlib.sha256(path.read_bytes()).hexdigest()
def desc(path:Path)->dict[str,str]:return {"path":str(path),"sha256":sha(path)}
def load(path:Path)->dict:return json.loads(path.read_text(encoding="utf-8"))
def main()->None:
    p=argparse.ArgumentParser();p.add_argument("--source-hypothesis",type=Path,required=True);p.add_argument("--split-artifact",type=Path,required=True);p.add_argument("--discovery-artifact",type=Path,required=True);p.add_argument("--development-artifact",type=Path,required=True);p.add_argument("--output",type=Path,required=True);a=p.parse_args()
    if a.output.exists():raise SystemExit(f"refusing to overwrite: {a.output}")
    paths={"source_hypothesis_config":a.source_hypothesis,"hypothesis_freeze":a.split_artifact/"hypothesis_freeze_v1.json","split_manifest":a.split_artifact/"manifest.json","split_summary":a.split_artifact/"split_summary_v1.json","discovery_manifest":a.discovery_artifact/"manifest.json","discovery_report":a.discovery_artifact/"evaluation_report_v1.json","development_manifest":a.development_artifact/"manifest.json","development_report":a.development_artifact/"evaluation_report_v1.json"}
    missing=[str(x) for x in paths.values() if not x.is_file()]
    if missing:raise SystemExit(f"missing inputs: {missing}")
    h=load(paths["hypothesis_freeze"]);s=load(paths["split_summary"]);d=load(paths["development_report"]);t=h["decision_thresholds"];c=d["arms"]["B_A_plus_narrow_relative_percent_change_contract"];o=d["candidate_outcome_counts"];observed={"newly_correct":int(o.get("IMPROVED",0)),"precision_on_predictions":c["precision_on_predictions"],"false_confident_candidate":int(c["false_confident_count"]),"regressed_baseline_predictions":int(o.get("REGRESSED",0))}
    passed=observed["newly_correct"]>=t["minimum_newly_correct_development"] and observed["precision_on_predictions"]==t["minimum_precision_on_predictions"] and observed["false_confident_candidate"]<=t["maximum_false_confident_candidate"] and observed["regressed_baseline_predictions"]<=t["maximum_regressed_baseline_predictions"]
    if not passed:raise SystemExit("development gate failed")
    metric=s["metric_holdout"];composition=s["composition_holdout"]
    receipt={"protocol":"vietnamese_percent_change_semantics_pre_unseen_freeze_v1","schema_version":1,"experiment_id":h["experiment_id"],"freeze_stage":"AFTER_DEVELOPMENT_BEFORE_UNTOUCHED","inputs":{k:desc(v) for k,v in paths.items()},"development_gate":{"status":"PASS","observed":observed},"frozen_untouched_predictions":{"minimum_newly_correct_untouched":t["minimum_newly_correct_untouched"],"minimum_precision_on_predictions":t["minimum_precision_on_predictions"],"maximum_false_confident_candidate":t["maximum_false_confident_candidate"],"maximum_regressed_baseline_predictions":t["maximum_regressed_baseline_predictions"],"metric_holdout_prediction":{"family":metric["family"],"available_untouched_records":metric["untouched_count"],"development_records":metric["development_count"],"minimum_improved":t["minimum_metric_holdout_improved"]},"composition_holdout_prediction":{"signature":composition["signature"],"available_untouched_records":composition["untouched_count"],"development_records":composition["development_count"],"minimum_improved":t["minimum_composition_holdout_improved"]}},"untouched_evaluation_allowed":True,"full_vifinqa_dataset_allowed":False,"source_contract":{"research_only":True,"evidence_eligible":False,"submission_eligible":False,"promotion_allowed":False,"vifinqa_answer_authority":False}}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(receipt,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8");print(json.dumps(receipt,indent=2,sort_keys=True))
if __name__=="__main__":main()
