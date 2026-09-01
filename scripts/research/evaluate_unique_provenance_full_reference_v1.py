#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
REPO_ROOT=Path(__file__).resolve().parents[2]
if str(REPO_ROOT/"src") not in sys.path:sys.path.insert(0,str(REPO_ROOT/"src"))
from finance_query.research.full_dataset_compare import sha256_file
from finance_query.research.unique_provenance_evidence import evaluate_unique_provenance_full_reference,load_jsonl
from finance_query.research.unique_provenance_pre_unseen import validate_unique_provenance_pre_unseen
def main()->None:
    p=argparse.ArgumentParser();p.add_argument("--dataset",type=Path,required=True);p.add_argument("--split-artifact",type=Path,required=True);p.add_argument("--pre-unseen-freeze",type=Path,required=True);p.add_argument("--untouched-report",type=Path,required=True);p.add_argument("--output-dir",type=Path,required=True);a=p.parse_args()
    if a.output_dir.exists():raise SystemExit(f"refusing to overwrite: {a.output_dir}")
    if validate_unique_provenance_pre_unseen(a.pre_unseen_freeze,REPO_ROOT)["status"]!="PASS":raise SystemExit("pre-unseen validation failed")
    receipt=json.loads(a.pre_unseen_freeze.read_text());report=json.loads(a.untouched_report.read_text());pred=receipt["frozen_untouched_predictions"];cand=report["arms"]["B_A_plus_ticker_year_form_unique_gate"];out=report["candidate_outcome_counts"];metric=pred["metric_holdout_prediction"];comp=pred["composition_holdout_prediction"]
    observed={"newly_correct":int(out.get("IMPROVED",0)),"precision_on_predictions":cand["precision_on_predictions"],"false_confident_candidate":int(cand["false_confident_count"]),"regressed_baseline_predictions":int(out.get("REGRESSED",0)),"metric_holdout_improved":int((report["metric_outcome_counts"].get(metric["family"]) or {}).get("IMPROVED",0)),"composition_holdout_improved":int((report["composition_outcome_counts"].get(comp["signature"]) or {}).get("IMPROVED",0))}
    passed=observed["newly_correct"]>=pred["minimum_newly_correct_untouched"] and observed["precision_on_predictions"]==pred["minimum_precision_on_predictions"] and observed["false_confident_candidate"]<=pred["maximum_false_confident_candidate"] and observed["regressed_baseline_predictions"]<=pred["maximum_regressed_baseline_predictions"] and observed["metric_holdout_improved"]>=metric["minimum_improved"] and observed["composition_holdout_improved"]>=comp["minimum_improved"]
    if not passed:raise SystemExit("untouched gate failed")
    hp=a.split_artifact/"hypothesis_freeze_v1.json";h=json.loads(hp.read_text());rows,full=evaluate_unique_provenance_full_reference(records=load_jsonl(a.dataset),seed=h["split_design"]["seed"]);full["untouched_gate"]={"status":"PASS","observed":observed};a.output_dir.mkdir(parents=True);rp=a.output_dir/"evaluation_rows_v1.jsonl";rp.write_text("".join(json.dumps(x,ensure_ascii=False,sort_keys=True)+"\n" for x in rows));jp=a.output_dir/"evaluation_report_v1.json";jp.write_text(json.dumps(full,ensure_ascii=False,indent=2,sort_keys=True)+"\n");m={"protocol":"unique_provenance_evidence_full_external_reference_manifest_v1","schema_version":1,"inputs":{"dataset":{"path":str(a.dataset),"sha256":sha256_file(a.dataset)},"hypothesis_freeze":{"path":str(hp),"sha256":sha256_file(hp)},"pre_unseen_freeze":{"path":str(a.pre_unseen_freeze),"sha256":sha256_file(a.pre_unseen_freeze)},"untouched_report":{"path":str(a.untouched_report),"sha256":sha256_file(a.untouched_report)}},"outputs":{rp.name:{"sha256":sha256_file(rp)},jp.name:{"sha256":sha256_file(jp)}},"untouched_gate":full["untouched_gate"],"source_contract":full["source_contract"]};(a.output_dir/"manifest.json").write_text(json.dumps(m,indent=2,sort_keys=True)+"\n");print(json.dumps(full,indent=2,sort_keys=True))
if __name__=="__main__":main()
