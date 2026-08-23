"""V2 exact bindings: source-unit provenance plus question-output conversion."""
from __future__ import annotations
from collections import Counter
from decimal import Decimal
import hashlib,json
from pathlib import Path
from typing import Any,Mapping
import re
import yaml
from .exact_cell_bindings import parse_vietnamese_numeric_candidate
from .financial_taxonomy import normalize_label

PROTOCOL="exact_cell_unit_binding_candidates_v2"
UNIT={"nghin ty dong":("nghin_ty_dong",Decimal("1000000000000")),"nghin ty vnd":("nghin_ty_dong",Decimal("1000000000000")),"tram ty dong":("tram_ty_dong",Decimal("100000000000")),"tram ty vnd":("tram_ty_dong",Decimal("100000000000")),"ty dong":("ty_dong",Decimal("1000000000")),"ty vnd":("ty_dong",Decimal("1000000000")),"trieu dong":("trieu_dong",Decimal("1000000")),"trieu vnd":("trieu_dong",Decimal("1000000")),"nghin dong":("nghin_dong",Decimal("1000")),"nghin vnd":("nghin_dong",Decimal("1000")),"vnd":("vnd",Decimal("1"))}

def sha(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def rows(p:Path):return [json.loads(x) for x in p.read_text().splitlines() if x]
def contract():return {"candidate_only":True,"evidence_eligible":False,"training_eligible":False,"submission_eligible":False,"promotion_allowed":False,"may_select_value":False,"may_execute_formula":False}
def _require(p:Path,expected:object,label:str):
 if not isinstance(expected,str) or sha(p)!=expected:raise ValueError(f"SHA-256 mismatch for {label}")
def resolve_source_unit(anchors:list[dict[str,Any]])->tuple[str|None,str|None,Decimal|None]:
 found=[]
 for anchor in anchors:
  text=re.sub(r"(?<=\d)(?=[a-z])"," ",normalize_label(anchor.get("raw_source_cell") or ""))
  for phrase,(unit,mult) in sorted(UNIT.items(),key=lambda item:-len(item[0])):
   if phrase in text:
    found.append((unit,mult));break
 unique=list(dict.fromkeys(found))
 return (unique[0][0],unique[0][0],unique[0][1]) if len(unique)==1 else (None,None,None)

def table_header_unit_anchors(*,table:Mapping[str,Any],selected_anchors:list[dict[str,Any]])->list[dict[str,Any]]:
 """Return all provenance-backed header cells when the selected header has no unit.

 The fallback may recover a table-wide printed unit (for example a merged OCR
 header rendered in the comparative column).  It never borrows a unit from a
 data row, table title, or another table, and conflicts remain blocked by
 ``resolve_source_unit``.
 """
 if resolve_source_unit(selected_anchors)[2] is not None:return selected_anchors
 rows=table.get("rows") or []; provenance=table.get("cell_provenance") or []
 selected_coordinates={(int(anchor["row_index"]),int(anchor["column_index"])) for anchor in selected_anchors}
 out=list(selected_anchors)
 for row_index in table.get("header_row_indices") or []:
  if not isinstance(row_index,int) or row_index<0 or row_index>=len(rows) or row_index>=len(provenance):continue
  for column_index,raw in enumerate(rows[row_index]):
   if (row_index,column_index) in selected_coordinates or column_index>=len(provenance[row_index]):continue
   anchor={"row_index":row_index,"column_index":column_index,"raw_source_cell":str(raw),"cell_provenance":provenance[row_index][column_index]}
   if resolve_source_unit([anchor])[2] is not None:out.append(anchor)
 return out

def build(*,period_packets:Path,period_manifest:Path,route_overlay:Path,route_overlay_manifest:Path,structured_tables:Path,metric_registry:Path,output:Path,approved_repairs:Path|None=None,repairs_manifest:Path|None=None)->dict[str,Any]:
 pm=json.loads(period_manifest.read_text()); _require(period_packets,pm["outputs"]["period_packets"]["sha256"],"period packets")
 rm=json.loads(route_overlay_manifest.read_text()); _require(route_overlay,rm["outputs"]["overlay"]["sha256"],"route overlay")
 _require(structured_tables,pm["inputs"]["structured_tables_v2"]["sha256"],"V2 tables")
 if bool(approved_repairs)!=bool(repairs_manifest):raise ValueError("approved repairs/manifest must be paired")
 if approved_repairs and approved_repairs.read_text().strip():raise ValueError("NON_EMPTY_REPAIR_OVERLAY_UNSUPPORTED")
 packets={r["question_id"]:r for r in rows(period_packets)}; routes={r["question_id"]:r for r in rows(route_overlay)}; tables={r["internal_table_uid"]:r for r in rows(structured_tables)}
 if set(packets)!=set(routes) or set(packets)!=set(range(1,1013)):raise ValueError("route/period ID mismatch")
 registry={r["metric_id"]:r for r in yaml.safe_load(metric_registry.read_text())["metrics"]}; out=[]; candidate_counts=Counter()
 for q in sorted(packets):
  packet,route=packets[q],routes[q]; stages=[]
  for stage in packet.get("stages") or []:
   ops=[]; metric=registry.get(stage.get("metric_id")); output_kind="currency" if metric is None or metric.get("output_unit")=="source_unit" else ("percent" if metric.get("output_unit")=="percent" else "times")
   for op in stage.get("required_operands") or []:
    pcs=list(op.get("period_column_candidates") or []); status="binding_blocked"; record=None; reason=[]
    if route["route_status"]!="route_complete":reason=["ROUTE_INCOMPLETE"]
    elif packet.get("packet_status") != "unique_period_column_candidate":
     packet_status = str(packet.get("packet_status") or "")
     reason=[{
      "packet_blocked": "PERIOD_CANDIDATE_BLOCKED",
      "ambiguous_period_columns": "PERIOD_AMBIGUOUS",
      "no_period_column": "PERIOD_COLUMN_MISSING",
      "unreliable_numeric_source": "PERIOD_SOURCE_UNRELIABLE",
     }.get(packet_status, "PERIOD_NOT_UNIQUE")]
    elif len(pcs)!=1:reason=["PERIOD_NOT_UNIQUE"]
    else:
     c=pcs[0]; uid=str(c["internal_table_uid"]); ri,ci=int(c["row_index"]),int(c["column_index"]); table=tables[uid]; raw=str(table["rows"][ri][ci]); parsed,decimal,policy=parse_vietnamese_numeric_candidate(raw)
     anchors=[]
     for coord in c.get("header_source_cells") or []:
      hr,hc=int(coord["row_index"]),int(coord["column_index"]); anchors.append({"row_index":hr,"column_index":hc,"raw_source_cell":str(table["rows"][hr][hc]),"cell_provenance":table["cell_provenance"][hr][hc]})
     anchors=table_header_unit_anchors(table=table,selected_anchors=anchors)
     source_unit,mult=resolve_source_unit(anchors)[:2]; multiplier=resolve_source_unit(anchors)[2]
     request=route["requested_output_unit"]
     if parsed!="parsed_decimal_candidate":status="numeric_parse_failure";reason=[policy or "NUMERIC_PARSE_FAILURE"]
     elif output_kind=="currency" and multiplier is None:status="unit_missing";reason=["SOURCE_UNIT_ANCHOR_MISSING_OR_CONFLICTING"]
     elif output_kind=="currency" and request["kind"]!="currency":status="unit_conflict";reason=["REQUESTED_OUTPUT_KIND_CONFLICT"]
     elif output_kind in {"percent","times"} and request["kind"] not in {output_kind,"source_unit"}:status="unit_conflict";reason=["REQUESTED_OUTPUT_KIND_CONFLICT"]
     else:
      status="binding_ready";reason=[]
     record={"question_id":q,"stage_id":stage.get("stage_id"),"role":op.get("role"),"concept_id":op.get("concept_id"),"binding_status":status,"formula_output_kind":output_kind,"requested_output_unit":request,"document_id":table.get("document_id"),"internal_table_uid":uid,"row_index":ri,"column_index":ci,"raw_source_row":table["rows"][ri],"raw_source_cell":raw,"cell_provenance":table["cell_provenance"][ri][ci],"header_source_cells":c.get("header_source_cells") or [],"period_labels":c.get("period_labels") or [],"period_resolution_method":c.get("period_resolution_method"),"period_source_title_sha256":c.get("period_source_title_sha256"),"period_source_date":c.get("period_source_date"),"period_entity_corroboration":c.get("period_entity_corroboration"),"source_unit_anchors":anchors,"source_unit":source_unit,"source_to_vnd_multiplier":None if multiplier is None else format(multiplier,"f"),"vnd_to_output_divisor":request.get("vnd_to_output_divisor"),"raw_decimal_candidate":decimal,"numeric_parse_policy":policy,"reason_codes":reason,"source_contract":contract()}
    ops.append(record or {"question_id":q,"stage_id":stage.get("stage_id"),"role":op.get("role"),"concept_id":op.get("concept_id"),"binding_status":status,"binding_candidates":[],"reason_codes":reason,"source_contract":contract()}); candidate_counts[status]+=1
   stages.append({"stage_id":stage.get("stage_id"),"route_kind":stage.get("route_kind"),"metric_id":stage.get("metric_id"),"required_operands":ops})
  statuses=[op["binding_status"] for s in stages for op in s["required_operands"]]
  ps="binding_ready" if statuses and all(x=="binding_ready" for x in statuses) else ("route_incomplete" if "ROUTE_INCOMPLETE" in [x for s in stages for op in s["required_operands"] for x in op.get("reason_codes",[])] else "binding_blocked")
  out.append({"schema_version":2,"protocol":PROTOCOL,"question_id":q,"route_status":route["route_status"],"binding_packet_status":ps,"question_context":route["question_context"],"requested_output_unit":route["requested_output_unit"],"stages":stages,"source_contract":contract()})
 output.parent.mkdir(parents=True,exist_ok=True);output.write_text("".join(json.dumps(r,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n" for r in out))
 res={"schema_version":2,"protocol":PROTOCOL,"inputs":{"period_packets":{"path":str(period_packets),"sha256":sha(period_packets)},"route_overlay":{"path":str(route_overlay),"sha256":sha(route_overlay)},"structured_tables":{"path":str(structured_tables),"sha256":sha(structured_tables)},"metric_registry":{"path":str(metric_registry),"sha256":sha(metric_registry)},"approved_repairs":None if not approved_repairs else {"path":str(approved_repairs),"sha256":sha(approved_repairs)}},"outputs":{"bindings":{"path":str(output),"sha256":sha(output)}},"counts":{"question_count":len(out),"binding_packet_status_counts":dict(Counter(x["binding_packet_status"] for x in out)),"binding_status_counts":dict(candidate_counts)},"source_contract":contract()}; mp=output.with_suffix(".manifest.json");mp.write_text(json.dumps(res,ensure_ascii=False,sort_keys=True,indent=2)+"\n");return {**res,"manifest_path":str(mp)}
