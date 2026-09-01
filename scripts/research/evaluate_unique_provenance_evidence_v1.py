#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, sys
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path: sys.path.insert(0, str(REPO_ROOT / "src"))
from finance_query.research.unique_provenance_evidence import evaluate_unique_provenance_stage, load_jsonl
from finance_query.research.unique_provenance_pre_unseen import validate_unique_provenance_pre_unseen
def sha(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def main() -> None:
    p=argparse.ArgumentParser(); p.add_argument("--split-artifact",type=Path,required=True); p.add_argument("--dataset",type=Path,required=True); p.add_argument("--official-splits",type=Path,required=True); p.add_argument("--stage",choices=["discovery","development","untouched_evaluation"],required=True); p.add_argument("--output-dir",type=Path,required=True); p.add_argument("--pre-unseen-freeze",type=Path); a=p.parse_args()
    if a.output_dir.exists(): raise SystemExit(f"refusing to overwrite: {a.output_dir}")
    if a.stage=="untouched_evaluation":
        if a.pre_unseen_freeze is None or not a.pre_unseen_freeze.is_file(): raise SystemExit("untouched requires pre-unseen receipt")
        r=json.loads(a.pre_unseen_freeze.read_text());
        if r.get("protocol")!="unique_provenance_evidence_pre_unseen_freeze_v1" or r.get("untouched_evaluation_allowed") is not True: raise SystemExit("invalid receipt")
        if validate_unique_provenance_pre_unseen(a.pre_unseen_freeze,REPO_ROOT)["status"]!="PASS": raise SystemExit("receipt validation failed")
    hpath=a.split_artifact/"hypothesis_freeze_v1.json"; h=json.loads(hpath.read_text()); seed=h["split_design"]["seed"]
    records=load_jsonl(a.dataset); split=json.loads(a.official_splits.read_text())["ticker"]; dev=set(split["held_out_dev_tickers"]); test=set(split["held_out_test_tickers"])
    stage_records={"discovery":[r for r in records if r["ticker"] not in dev|test],"development":[r for r in records if r["ticker"] in dev],"untouched_evaluation":[r for r in records if r["ticker"] in test]}[a.stage]
    apath=a.split_artifact/"split_assignments_v1.jsonl"; rows,report=evaluate_unique_provenance_stage(assignments=load_jsonl(apath),records=stage_records,stage=a.stage,seed=seed)
    a.output_dir.mkdir(parents=True); rp=a.output_dir/"evaluation_rows_v1.jsonl"; rp.write_text("".join(json.dumps(x,ensure_ascii=False,sort_keys=True)+"\n" for x in rows)); jp=a.output_dir/"evaluation_report_v1.json"; jp.write_text(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True)+"\n")
    m={"protocol":"unique_provenance_evidence_evaluation_manifest_v1","schema_version":1,"inputs":{"assignments":{"path":str(apath),"sha256":sha(apath)},"hypothesis_freeze":{"path":str(hpath),"sha256":sha(hpath)},"dataset":{"path":str(a.dataset),"sha256":sha(a.dataset)},"official_splits":{"path":str(a.official_splits),"sha256":sha(a.official_splits)}},"outputs":{rp.name:{"sha256":sha(rp)},jp.name:{"sha256":sha(jp)}},"benchmark_stage":a.stage,"question_or_passage_materialized":False,"full_vifinqa_dataset_allowed":False}; (a.output_dir/"manifest.json").write_text(json.dumps(m,ensure_ascii=False,indent=2,sort_keys=True)+"\n"); print(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True))
if __name__ == "__main__": main()
