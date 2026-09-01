#!/usr/bin/env python3
from __future__ import annotations
import argparse,hashlib,json,sys
from pathlib import Path
REPO_ROOT=Path(__file__).resolve().parents[2]
if str(REPO_ROOT/"src") not in sys.path:sys.path.insert(0,str(REPO_ROOT/"src"))
from finance_query.research.vietnamese_percent_change_semantics import build_finqa_company_index,evaluate_stage,load_jsonl
from finance_query.research.vietnamese_percent_change_pre_unseen import validate_pre_unseen
def sha(path:Path)->str:return hashlib.sha256(path.read_bytes()).hexdigest()
def main()->None:
    p=argparse.ArgumentParser();p.add_argument("--split-artifact",type=Path,required=True);p.add_argument("--source",type=Path,required=True);p.add_argument("--finqa-root",type=Path,required=True);p.add_argument("--stage",choices=["discovery","development","untouched_evaluation"],required=True);p.add_argument("--output-dir",type=Path,required=True);p.add_argument("--pre-unseen-freeze",type=Path);a=p.parse_args()
    if a.output_dir.exists():raise SystemExit(f"refusing to overwrite: {a.output_dir}")
    if a.stage=="untouched_evaluation":
        if a.pre_unseen_freeze is None or not a.pre_unseen_freeze.is_file():raise SystemExit("untouched requires pre-unseen receipt")
        if validate_pre_unseen(a.pre_unseen_freeze,REPO_ROOT)["status"]!="PASS":raise SystemExit("pre-unseen receipt validation failed")
    hp=a.split_artifact/"hypothesis_freeze_v1.json";h=json.loads(hp.read_text());ap=a.split_artifact/"split_assignments_v1.jsonl";rows,report=evaluate_stage(assignments=load_jsonl(ap),records=json.loads(a.source.read_text()),stage=a.stage,seed=h["split_design"]["seed"],company_index=build_finqa_company_index(a.finqa_root));a.output_dir.mkdir(parents=True);rp=a.output_dir/"evaluation_rows_v1.jsonl";rp.write_text("".join(json.dumps(x,ensure_ascii=False,sort_keys=True)+"\n" for x in rows));jp=a.output_dir/"evaluation_report_v1.json";jp.write_text(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True)+"\n");m={"protocol":"vietnamese_percent_change_semantics_evaluation_manifest_v1","schema_version":1,"benchmark_stage":a.stage,"inputs":{"assignments":{"path":str(ap),"sha256":sha(ap)},"hypothesis_freeze":{"path":str(hp),"sha256":sha(hp)},"source":{"path":str(a.source),"sha256":sha(a.source)},**{f"finqa_{s}":{"path":str(a.finqa_root/f"{s}.json"),"sha256":sha(a.finqa_root/f"{s}.json")} for s in ("train","dev","test")}},"outputs":{rp.name:{"sha256":sha(rp)},jp.name:{"sha256":sha(jp)}},"source_contract":report["source_contract"]};(a.output_dir/"manifest.json").write_text(json.dumps(m,indent=2,sort_keys=True)+"\n");print(json.dumps(report,indent=2,sort_keys=True))
if __name__=="__main__":main()
