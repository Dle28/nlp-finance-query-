#!/usr/bin/env python3
"""Build hash-bound, closed-world critic packets from corrected execution V2."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from finance_query.grounded_critic_protocol import ALLOWED_REASON_CODES, source_contract


PROTOCOL = "grounded_critic_packets_v2"
EXPECTED_QUESTION_IDS = set(range(1, 1013))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_id_sha256(question_ids: Iterable[int]) -> str:
    payload = json.dumps(sorted(question_ids), separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def require_manifest_output(path: Path, manifest: Mapping[str, Any], key: str, label: str) -> None:
    expected = ((manifest.get("outputs") or {}).get(key) or {}).get("sha256")
    if not isinstance(expected, str) or sha(path) != expected:
        raise ValueError(f"SHA-256 mismatch for {label}")


def require_full_coverage(rows: list[dict[str, Any]], label: str) -> dict[int, dict[str, Any]]:
    indexed = {int(row["question_id"]): row for row in rows}
    if len(indexed) != len(rows) or set(indexed) != EXPECTED_QUESTION_IDS:
        raise ValueError(f"{label} ID coverage mismatch")
    return indexed


def evidence_from_execution(record: Mapping[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    ids: set[str] = set()
    excerpts: list[dict[str, Any]] = []
    for trace in record.get("stage_traces") or []:
        for source in trace.get("operand_sources") or []:
            try:
                evidence_id = ":".join(
                    [
                        str(source["internal_table_uid"]),
                        str(int(source["row_index"])),
                        str(int(source["column_index"])),
                    ]
                )
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(
                    f"Q{record.get('question_id')}: execution-ready trace has invalid source coordinates"
                ) from error
            ids.add(evidence_id)
            excerpts.append(
                {
                    "evidence_id": evidence_id,
                    "stage_id": trace.get("stage_id"),
                    "role": source.get("role"),
                    "document_id": source.get("document_id"),
                    "internal_table_uid": source["internal_table_uid"],
                    "row_index": source["row_index"],
                    "column_index": source["column_index"],
                    "cell_provenance": source.get("cell_provenance"),
                    "raw_value_decimal": source.get("raw_value_decimal"),
                    "base_vnd_value_decimal": source.get("base_vnd_value_decimal"),
                    "source_to_vnd_multiplier": source.get("source_to_vnd_multiplier"),
                }
            )
    if not ids:
        raise ValueError(f"Q{record.get('question_id')}: execution-ready packet has no bounded evidence")
    return sorted(ids), excerpts


def id_coverage(question_ids: Iterable[int]) -> dict[str, Any]:
    ids = sorted(question_ids)
    return {
        "count": len(ids),
        "question_id_sha256": canonical_id_sha256(ids),
        "question_ids": ids,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execution", type=Path, required=True)
    parser.add_argument("--execution-manifest", type=Path, required=True)
    parser.add_argument("--route-overlay", type=Path, required=True)
    parser.add_argument("--route-overlay-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    execution_manifest = json.loads(args.execution_manifest.read_text(encoding="utf-8"))
    route_manifest = json.loads(args.route_overlay_manifest.read_text(encoding="utf-8"))
    require_manifest_output(args.execution, execution_manifest, "execution", "execution V2")
    require_manifest_output(args.route_overlay, route_manifest, "overlay", "route-completeness V2")

    execution = require_full_coverage(read_rows(args.execution), "execution V2")
    routes = require_full_coverage(read_rows(args.route_overlay), "route-completeness V2")

    packets: list[dict[str, Any]] = []
    for question_id in sorted(EXPECTED_QUESTION_IDS):
        record = execution[question_id]
        route = routes[question_id]
        if (
            record.get("execution_status") != "execution_replay_ready"
            or route.get("route_status") != "route_complete"
        ):
            continue
        evidence_ids, excerpts = evidence_from_execution(record)
        packets.append(
            {
                "schema_version": 2,
                "protocol": PROTOCOL,
                "question_id": question_id,
                "question_context": record.get("question_context"),
                "execution_status": record["execution_status"],
                "execution": record,
                "deterministic_execution_trace": record.get("stage_traces") or [],
                "bounded_source_excerpts": excerpts,
                "allowed_decisions": ["accept", "reject", "abstain"],
                "allowed_reason_codes": sorted(ALLOWED_REASON_CODES),
                "allowed_packet_evidence_ids": evidence_ids,
                "source_contract": source_contract(),
            }
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(
            json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for packet in packets
        ),
        encoding="utf-8",
    )
    packet_ids = [packet["question_id"] for packet in packets]
    manifest = {
        "schema_version": 2,
        "protocol": PROTOCOL,
        "inputs": {
            "execution": {"path": str(args.execution), "sha256": sha(args.execution)},
            "execution_manifest": {
                "path": str(args.execution_manifest),
                "sha256": sha(args.execution_manifest),
            },
            "route_overlay": {"path": str(args.route_overlay), "sha256": sha(args.route_overlay)},
            "route_overlay_manifest": {
                "path": str(args.route_overlay_manifest),
                "sha256": sha(args.route_overlay_manifest),
            },
        },
        "outputs": {"packets": {"path": str(args.output), "sha256": sha(args.output)}},
        "id_coverage": {
            "expected_question_ids": id_coverage(EXPECTED_QUESTION_IDS),
            "execution_question_ids": id_coverage(execution),
            "route_overlay_question_ids": id_coverage(routes),
            "packet_question_ids": id_coverage(packet_ids),
        },
        "counts": {
            "packet_count": len(packets),
            "execution_status_counts": dict(
                sorted(Counter(row.get("execution_status") for row in execution.values()).items())
            ),
            "route_status_counts": dict(
                sorted(Counter(row.get("route_status") for row in routes.values()).items())
            ),
        },
        "source_contract": source_contract(),
    }
    manifest_path = args.output.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    print(manifest_path)


if __name__ == "__main__":
    main()
