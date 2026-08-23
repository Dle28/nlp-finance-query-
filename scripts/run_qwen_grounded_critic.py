#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
from finance_query.grounded_critic_protocol import validate_critic_response

def sha(p:Path): return hashlib.sha256(p.read_bytes()).hexdigest()
def main() -> None:
 p=argparse.ArgumentParser(); p.add_argument("--packets",type=Path,required=True); p.add_argument("--packets-manifest",type=Path,required=True); p.add_argument("--responses",type=Path); p.add_argument("--dry-run",action="store_true"); p.add_argument("--output",type=Path,required=True); a=p.parse_args()
 m=json.loads(a.packets_manifest.read_text());
 if sha(a.packets)!=m["outputs"]["packets"]["sha256"]: raise ValueError("SHA-256 mismatch for critic packets")
 if bool(a.responses)==bool(a.dry_run): raise ValueError("Provide exactly one of --responses or --dry-run")
 packets=[json.loads(x) for x in a.packets.read_text().splitlines() if x]; provided={r["question_id"]:r for r in ([json.loads(x) for x in a.responses.read_text().splitlines() if x] if a.responses else [])}; out=[]
 for packet in packets:
  response=provided.get(packet["question_id"]) if a.responses else {"question_id":packet["question_id"],"status":"abstain","stage_reviews":[],"binding_reviews":[],"execution_review":{},"missing_evidence":[],"conflicts":[],"feedback":{"missing_concepts":[],"suggested_table_types":[],"suggested_retrieval_stage":None,"reason_codes":["MODEL_NOT_RUN"]},"packet_evidence_ids_used":[]}
  if response is None: raise ValueError("Missing critic response")
  out.append(validate_critic_response(packet,response))
 a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text("".join(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n" for x in out)); print(a.output)
if __name__=="__main__": main()
