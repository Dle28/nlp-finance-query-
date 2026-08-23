from decimal import Decimal
import json
from pathlib import Path
import yaml
from finance_query.grounded_execution_v2 import convert_currency
def test_currency_conversion_policy_uses_decimal_not_question_constants():
 base,out=convert_currency(Decimal("145731366146"),Decimal("1"),Decimal("1000000000"));assert base==Decimal("145731366146") and out==Decimal("145.731366146")
 base,out=convert_currency(Decimal("-150202628445"),Decimal("1"),Decimal("1000000"));assert out==Decimal("-150202.628445")

def test_q400_v2_is_route_incomplete_not_execution_ready():
 root=Path(__file__).resolve().parents[1]; v1={r["question_id"]:r for r in map(json.loads,open(root/"artifacts/research/question_concept_routing_v1/question_concept_routes_v1.jsonl"))}; rows={r["question_id"]:r for r in map(json.loads,open(root/"artifacts/research/grounded_execution_v2/grounded_execution_replay_v2.jsonl"))}
 assert v1[400]["route_status"]=="metric_candidate"
 assert rows[400]["execution_status"]=="route_incomplete"

def test_single_exact_reported_concept_executes_as_a_currency_lookup(tmp_path:Path):
 from finance_query.grounded_execution_v2 import run
 root=Path(__file__).resolve().parents[1]; source=root/"artifacts/research/exact_cell_unit_bindings_v2/exact_cell_unit_binding_candidates_v2.jsonl"; records=[json.loads(line) for line in source.read_text().splitlines()]
 record=next(row for row in records if row["question_id"]==12); record["binding_packet_status"]="binding_ready"; stage=record["stages"][0]; stage["route_kind"]="reported_concept"; op=stage["required_operands"][0]; op.update({"binding_status":"binding_ready","formula_output_kind":"currency","source_to_vnd_multiplier":"1","raw_decimal_candidate":"5000000000000","requested_output_unit":{"kind":"currency","unit":"nghin_ty_dong","vnd_to_output_divisor":"1000000000000"}})
 all_records=[record if row["question_id"]==12 else {**row,"route_status":"route_incomplete"} for row in records]
 bindings=tmp_path/"bindings.jsonl"; bindings.write_text("".join(json.dumps(row)+"\n" for row in all_records)); manifest=tmp_path/"bindings.manifest.json"; import hashlib; manifest.write_text(json.dumps({"outputs":{"bindings":{"sha256":hashlib.sha256(bindings.read_bytes()).hexdigest()}}}))
 output=tmp_path/"execution.jsonl"; run(bindings=bindings,bindings_manifest=manifest,metric_registry=root/"configs/financial_metric_registry_v1.yaml",output=output)
 result=next(json.loads(line) for line in output.read_text().splitlines() if json.loads(line)["question_id"]==12)
 assert result["execution_status"]=="execution_replay_ready"
 assert result["stage_traces"][0]["execution_kind"]=="reported_value_lookup"
 assert result["stage_traces"][0]["converted_output_decimal"]=="5"
