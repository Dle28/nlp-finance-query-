#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import yaml
from finance_query.grounded_critic_protocol import CRITIC_PROTOCOL, source_contract

def sha(path: Path) -> str:
 d=hashlib.sha256(); d.update(path.read_bytes()); return d.hexdigest()
def rows(path: Path): return [json.loads(x) for x in path.read_text().splitlines() if x]
def main() -> None:
 p=argparse.ArgumentParser(); p.add_argument("--execution",type=Path,required=True); p.add_argument("--execution-manifest",type=Path,required=True); p.add_argument("--bindings",type=Path,required=True); p.add_argument("--metric-registry",type=Path,required=True); p.add_argument("--output-dir",type=Path,required=True); a=p.parse_args()
 m=json.loads(a.execution_manifest.read_text()); expected=m["outputs"]["execution"]["sha256"]
 if sha(a.execution)!=expected: raise ValueError("SHA-256 mismatch for execution")
 execution={r["question_id"]:r for r in rows(a.execution)}; bindings={r["question_id"]:r for r in rows(a.bindings)}
 if set(execution)!=set(bindings) or set(execution)!=set(range(1,1013)): raise ValueError("Execution/binding ID coverage mismatch")
 registry={x["metric_id"]:x for x in yaml.safe_load(a.metric_registry.read_text())["metrics"]}; packets=[]
 for q in sorted(execution):
  record=execution[q]
  if record["execution_status"]!="execution_replay_ready": continue
  ids=[]; excerpts=[]
  for stage in bindings[q]["stages"]:
   for op in stage["required_operands"]:
    for c in op.get("binding_candidates",[]):
     ident=f'{c["internal_table_uid"]}:{c["row_index"]}:{c["column_index"]}'; ids.append(ident); excerpts.append({k:c.get(k) for k in ("role","concept_id","internal_table_uid","row_index","column_index","raw_source_row","raw_source_cell","cell_provenance","header_source_cells","period_labels","unit_labels")})
  packets.append({"schema_version":1,"protocol":CRITIC_PROTOCOL,"question_id":q,"question_context":record["question_context"],"typed_stage_plan":[{"stage_id":s["stage_id"],"metric_id":s["metric_id"],"formula_ast":registry[s["metric_id"]]["formula_ast"]} for s in bindings[q]["stages"] if s["metric_id"] in registry],"bounded_source_excerpts":excerpts,"deterministic_execution_trace":record["stage_traces"],"execution_status":record["execution_status"],"allowed_decisions":["accept","reject","abstain"],"allowed_reason_codes":sorted(__import__("finance_query.grounded_critic_protocol",fromlist=["ALLOWED_REASON_CODES"]).ALLOWED_REASON_CODES),"allowed_packet_evidence_ids":sorted(set(ids)),"source_contract":source_contract()})
 a.output_dir.mkdir(parents=True,exist_ok=True); out=a.output_dir/"grounded_critic_packets_v1.jsonl"; out.write_text("".join(json.dumps(r,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n" for r in packets))
 manifest={"protocol":CRITIC_PROTOCOL,"inputs":{"execution":{"path":str(a.execution),"sha256":sha(a.execution)},"bindings":{"path":str(a.bindings),"sha256":sha(a.bindings)},"metric_registry":{"path":str(a.metric_registry),"sha256":sha(a.metric_registry)}},"outputs":{"packets":{"path":str(out),"sha256":sha(out)}},"counts":{"packet_count":len(packets)},"source_contract":source_contract()}; mp=a.output_dir/"grounded_critic_packets_v1.manifest.json"; mp.write_text(json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2)+"\n"); print(mp)
if __name__=="__main__": main()
