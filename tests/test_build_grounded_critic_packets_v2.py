from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from finance_query.grounded_critic_protocol import source_contract


ROOT = Path(__file__).resolve().parents[1]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def test_builder_writes_hash_bound_manifest_and_closed_world_packet(tmp_path: Path) -> None:
    execution_path = tmp_path / "execution.jsonl"
    routes_path = tmp_path / "routes.jsonl"
    execution_rows = []
    route_rows = []
    for question_id in range(1, 1013):
        ready = question_id == 6
        execution_rows.append(
            {
                "question_id": question_id,
                "execution_status": "execution_replay_ready" if ready else "route_incomplete",
                "question_context": {"years": [2017]},
                "stage_traces": (
                    [
                        {
                            "stage_id": "stage_1",
                            "operand_sources": [
                                {
                                    "role": "operating_cash_flow",
                                    "document_id": "VSC_2017",
                                    "internal_table_uid": "table-6",
                                    "row_index": 19,
                                    "column_index": 2,
                                    "cell_provenance": {"source_row": 19, "source_cell": 2},
                                    "raw_value_decimal": "145731366146",
                                    "base_vnd_value_decimal": "145731366146",
                                    "source_to_vnd_multiplier": "1",
                                }
                            ],
                        }
                    ]
                    if ready
                    else []
                ),
            }
        )
        route_rows.append(
            {"question_id": question_id, "route_status": "route_complete" if ready else "route_incomplete"}
        )
    _write_jsonl(execution_path, execution_rows)
    _write_jsonl(routes_path, route_rows)
    execution_manifest = tmp_path / "execution.manifest.json"
    route_manifest = tmp_path / "routes.manifest.json"
    execution_manifest.write_text(json.dumps({"outputs": {"execution": {"sha256": _sha(execution_path)}}}))
    route_manifest.write_text(json.dumps({"outputs": {"overlay": {"sha256": _sha(routes_path)}}}))
    output_path = tmp_path / "grounded_critic_packets_v2.jsonl"

    subprocess.run(
        [
            sys.executable,
            "scripts/build_grounded_critic_packets_v2.py",
            "--execution",
            str(execution_path),
            "--execution-manifest",
            str(execution_manifest),
            "--route-overlay",
            str(routes_path),
            "--route-overlay-manifest",
            str(route_manifest),
            "--output",
            str(output_path),
        ],
        cwd=ROOT,
        check=True,
    )

    packet = json.loads(output_path.read_text(encoding="utf-8"))
    assert packet["question_id"] == 6
    assert packet["execution_status"] == "execution_replay_ready"
    assert packet["allowed_packet_evidence_ids"] == ["table-6:19:2"]
    assert packet["bounded_source_excerpts"][0]["evidence_id"] == "table-6:19:2"
    assert packet["source_contract"] == source_contract()

    manifest_path = output_path.with_suffix(".manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["outputs"]["packets"]["sha256"] == _sha(output_path)
    assert manifest["inputs"]["execution"]["sha256"] == _sha(execution_path)
    assert manifest["inputs"]["route_overlay"]["sha256"] == _sha(routes_path)
    assert manifest["id_coverage"]["execution_question_ids"]["count"] == 1012
    assert manifest["id_coverage"]["route_overlay_question_ids"]["count"] == 1012
    assert manifest["id_coverage"]["packet_question_ids"]["question_ids"] == [6]
    assert manifest["source_contract"] == source_contract()
