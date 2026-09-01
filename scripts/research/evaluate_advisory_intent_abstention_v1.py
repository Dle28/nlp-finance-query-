#!/usr/bin/env python3
from __future__ import annotations
import argparse,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
if str(ROOT/"src") not in sys.path:sys.path.insert(0,str(ROOT/"src"))
from finance_query.research.advisory_intent_abstention import evaluate_stage,load_jsonl
from finance_query.research.advisory_intent_pre_unseen import validate_pre_unseen
def sha(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def main()->None:
 p=argparse.ArgumentParser();p.add_argument("--split-artifact",type=Path,required=True);p.add_argument("--dataset",type=Path,required=True);p.add_argument("--stage",choices=["discovery","development","untouched_evaluation"],required=True);p.add_argument("--output-dir",type=Path,required=True);p.add_argument("--pre-unseen-freeze",type=Path);a=p.parse_args()
 if a.output_dir.exists():raise SystemExit(f"refusing to overwrite: {a.output_dir}")
 if a.stage=="untouched_evaluation":
  if a.pre_unseen_freeze is None or not a.pre_unseen_freeze.is_file():raise SystemExit("untouched requires receipt")
  if validate_pre_unseen(a.pre_unseen_freeze,ROOT)["status"]!="PASS":raise SystemExit("receipt validation failed")
 hp=a.split_artifact/"hypothesis_freeze_v1.json";ap=a.split_artifact/"split_assignments_v1.jsonl";h=json.loads(hp.read_text());rows,report=evaluate_stage(assignments=load_jsonl(ap),records=load_jsonl(a.dataset),stage=a.stage,seed=h["split_design"]["seed"]);a.output_dir.mkdir(parents=True);rp=a.output_dir/"evaluation_rows_v1.jsonl";rp.write_text("".join(json.dumps(x,ensure_ascii=False,sort_keys=True)+"\n" for x in rows));jp=a.output_dir/"evaluation_report_v1.json";jp.write_text(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True)+"\n");m={"protocol":"explicit_advisory_intent_abstention_evaluation_manifest_v1","schema_version":1,"benchmark_stage":a.stage,"inputs":{"assignments":{"path":str(ap),"sha256":sha(ap)},"hypothesis_freeze":{"path":str(hp),"sha256":sha(hp)},"dataset":{"path":str(a.dataset),"sha256":sha(a.dataset)}},"outputs":{rp.name:{"sha256":sha(rp)},jp.name:{"sha256":sha(jp)}},"source_contract":report["source_contract"]};(a.output_dir/"manifest.json").write_text(json.dumps(m,indent=2,sort_keys=True)+"\n");print(json.dumps(report,indent=2,sort_keys=True))
if __name__=="__main__":main()
