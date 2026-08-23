"""V2 blank-decision repair overlay; non-empty input is explicitly unsupported."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
from typing import Sequence
def sha(p:Path):return hashlib.sha256(p.read_bytes()).hexdigest()
def materialize(queue_paths:Sequence[Path],output_dir:Path):
 records=[]
 for p in queue_paths:
  for line in p.read_text().splitlines():
   if not line:continue
   r=json.loads(line);d=(r.get("decision_contract") or {}).get("decision")
   if d is not None:raise ValueError("NON_EMPTY_REPAIR_OVERLAY_UNSUPPORTED")
   records.append({"source_queue_sha256":sha(p),"immutable_source_identity":r.get("immutable_source_identity") or {"question_id":r.get("question_id"),"stage_id":r.get("stage_id"),"role":r.get("role")},"decision":None,"eligible_for_materialization":False,"source_contract":{"evidence_eligible":False,"training_eligible":False,"submission_eligible":False,"promotion_allowed":False}})
 output_dir.mkdir(parents=True,exist_ok=True);approved=output_dir/"approved_grounding_repairs_v2.jsonl";unresolved=output_dir/"rejected_or_unresolved_repairs_v2.jsonl";approved.write_text("");unresolved.write_text("".join(json.dumps(r,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n" for r in records));m={"schema_version":2,"protocol":"grounding_repairs_overlay_v2","inputs":{"queues":[{"path":str(p),"sha256":sha(p)} for p in queue_paths]},"outputs":{"approved":{"path":str(approved),"sha256":sha(approved)},"unresolved":{"path":str(unresolved),"sha256":sha(unresolved)}},"counts":{"approved":0,"unresolved":len(records)},"source_contract":{"evidence_eligible":False,"training_eligible":False,"submission_eligible":False,"promotion_allowed":False}};mp=output_dir/"grounding_repairs_v2.manifest.json";mp.write_text(json.dumps(m,ensure_ascii=False,sort_keys=True,indent=2)+"\n");return mp
