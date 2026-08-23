from decimal import Decimal
import pytest
from pathlib import Path
from finance_query.exact_cell_bindings_v2 import build, resolve_source_unit, table_header_unit_anchors

def test_source_anchor_unit_matrix_is_provenanced_and_deterministic():
 for text,mult in (("Đơn vị: VND","1"),("Đơn vị: nghìn đồng","1000"),("Đơn vị: nghìn VND","1000"),("Đơn vị: triệu đồng","1000000"),("Đơn vị: triệu VND","1000000"),("Đơn vị: tỷ đồng","1000000000"),("Đơn vị: tỷ VND","1000000000"),("Đơn vị: trăm tỷ đồng","100000000000"),("Đơn vị: nghìn tỷ đồng","1000000000000")):
  _,value,actual=resolve_source_unit([{"raw_source_cell":text}]);assert actual==Decimal(mult)
 assert resolve_source_unit([{"raw_source_cell":"2020Triệu VND"}])[2]==Decimal("1000000")
 assert resolve_source_unit([])==(None,None,None)
 assert resolve_source_unit([{"raw_source_cell":"VND"},{"raw_source_cell":"triệu đồng"}])==(None,None,None)

def test_provenance_backed_table_header_unit_fallback_is_narrow_and_conflict_safe():
 table={"header_row_indices":[0],"rows":[["Năm 2018","Đơn vị tính: VND Năm 2017"],["x","1"]],"cell_provenance":[[{"source_row":0,"source_cell":0},{"source_row":0,"source_cell":1}],[{"source_row":1,"source_cell":0},{"source_row":1,"source_cell":1}]]}
 anchors=table_header_unit_anchors(table=table,selected_anchors=[{"row_index":0,"column_index":0,"raw_source_cell":"Năm 2018","cell_provenance":{"source_row":0,"source_cell":0}}])
 assert resolve_source_unit(anchors)[2]==Decimal("1")
 table["rows"][0].append("Đơn vị: triệu đồng");table["cell_provenance"][0].append({"source_row":0,"source_cell":2})
 assert resolve_source_unit(table_header_unit_anchors(table=table,selected_anchors=anchors[:1]))==(None,None,None)

def test_nonempty_repair_overlay_is_not_silently_ignored(tmp_path:Path):
 root=Path(__file__).resolve().parents[1]; run=root/"artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle"; repair=tmp_path/"approved.jsonl"; repair.write_text('{"scope":"metadata"}\n'); manifest=tmp_path/"repairs.json";manifest.write_text("{}")
 with pytest.raises(ValueError,match="NON_EMPTY_REPAIR_OVERLAY_UNSUPPORTED"):
  build(period_packets=root/"artifacts/research/period_column_candidates_v1/period_column_candidate_packets_v1.jsonl",period_manifest=root/"artifacts/research/period_column_candidates_v1/period_column_candidate_packets_v1.manifest.json",route_overlay=root/"artifacts/research/route_completeness_v2/route_completeness_overlay_v2.jsonl",route_overlay_manifest=root/"artifacts/research/route_completeness_v2/route_completeness_overlay_v2.manifest.json",structured_tables=run/"tables_structured_v2.jsonl",metric_registry=root/"configs/financial_metric_registry_v1.yaml",output=tmp_path/"out.jsonl",approved_repairs=repair,repairs_manifest=manifest)

def test_exact_reported_currency_binding_does_not_take_the_times_fallback(tmp_path:Path):
 root=Path(__file__).resolve().parents[1]; run=root/"artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle"; output=tmp_path/"bindings.jsonl"
 build(period_packets=root/"artifacts/research/period_column_candidates_v1/period_column_candidate_packets_v1.jsonl",period_manifest=root/"artifacts/research/period_column_candidates_v1/period_column_candidate_packets_v1.manifest.json",route_overlay=root/"artifacts/research/route_completeness_v2/route_completeness_overlay_v2.jsonl",route_overlay_manifest=root/"artifacts/research/route_completeness_v2/route_completeness_overlay_v2.manifest.json",structured_tables=run/"tables_structured_v2.jsonl",metric_registry=root/"configs/financial_metric_registry_v1.yaml",output=output)
 rows={row["question_id"]:row for row in map(__import__("json").loads,output.read_text().splitlines())}
 assert rows[12]["binding_packet_status"]=="binding_ready"
 assert rows[12]["stages"][0]["route_kind"]=="reported_concept"
 assert rows[702]["binding_packet_status"]=="binding_ready"
 operand=rows[702]["stages"][0]["required_operands"][0]
 assert operand["binding_status"]=="binding_ready"
 assert operand["source_to_vnd_multiplier"]=="1"
