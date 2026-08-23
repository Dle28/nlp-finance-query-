"""Immutable five-packet GPU smoke-job preparation for component selection."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import yaml

from finance_query.table_structure import sha256_file

from .phase5_component_selection import (
    PHASE5_COMPONENT_SELECTION_PROTOCOL,
    render_phase5_component_selection_prompt,
)
from .pipeline import CertifiedCanonicalError


PHASE5_COMPONENT_SELECTION_SMOKE_PROTOCOL = "vifinqa_ccl_phase5_component_selection_smoke_v1"
PHASE5_COMPONENT_SELECTION_SMOKE_SCHEMA_VERSION = 1
_REQUESTS_NAME = "component_selection_smoke_requests_v1.jsonl"
_PACKETS_NAME = "component_selection_smoke_packets_v1.jsonl"
_PACKET_MANIFEST_NAME = "component_selection_smoke_packet_manifest.json"
_REPORT_NAME = "component_selection_smoke_job_report.json"
_MANIFEST_NAME = "component_selection_smoke_job_manifest.json"
_MAX_MODEL_PARAMETERS_BILLIONS = 14.7
_MAX_SMOKE_PACKETS = 5
_NAVIGATION_INPUT_NAMES = frozenset(
    {"report_navigation_overlay_v1.jsonl", "report_navigation_overlay_manifest.json"}
)


@dataclass(frozen=True, slots=True)
class Phase5ComponentSelectionSmokeJobResult:
    output_dir: Path
    request_count: int
    manifest_path: Path


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha_json(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise CertifiedCanonicalError(f"invalid JSON input: {path}") from error
    if not isinstance(value, dict):
        raise CertifiedCanonicalError(f"expected JSON object: {path}")
    return value


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise CertifiedCanonicalError(f"invalid JSONL input: {path}:{line_number}") from error
            if not isinstance(value, dict):
                raise CertifiedCanonicalError(f"expected JSONL object: {path}:{line_number}")
            rows.append(value)
    return rows


def _resolve(repo_root: Path, value: object) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else (repo_root / path).resolve()


def _new_output(output_dir: Path, inputs: list[Path]) -> None:
    if output_dir.resolve() in {path.resolve() for path in inputs}:
        raise CertifiedCanonicalError("output-dir cannot be a component-selection smoke input")
    if output_dir.exists():
        raise FileExistsError("output-dir must be new; component-selection smoke jobs are immutable")


def _validated_config(config_path: Path) -> tuple[dict[str, Any], dict[str, Path], dict[str, Any]]:
    config_path = config_path.resolve()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if (
        config.get("protocol") != PHASE5_COMPONENT_SELECTION_SMOKE_PROTOCOL
        or int(config.get("schema_version") or 0) != PHASE5_COMPONENT_SELECTION_SMOKE_SCHEMA_VERSION
    ):
        raise CertifiedCanonicalError("unsupported component-selection smoke configuration")
    inputs = config.get("inputs")
    route = config.get("route")
    if not isinstance(inputs, Mapping) or not isinstance(route, Mapping):
        raise CertifiedCanonicalError("component-selection smoke configuration requires inputs and route")
    repo_root = config_path.parent.parent.resolve()
    paths = {
        "config": config_path,
        "packets": _resolve(repo_root, inputs.get("packets")),
        "packet_manifest": _resolve(repo_root, inputs.get("packet_manifest")),
    }
    if any(not path.is_file() for path in paths.values()):
        raise FileNotFoundError("component-selection smoke configuration references a missing input")
    route_id = str(route.get("route_id") or "")
    model_id = str(route.get("model_id") or "")
    revision = str(route.get("revision") or "")
    parameter_count = float(route.get("parameter_count_billions") or 0)
    packet_ids = route.get("packet_ids")
    if (
        not route_id
        or not model_id
        or not revision
        or not 0 < parameter_count < _MAX_MODEL_PARAMETERS_BILLIONS
        or route.get("load_in_4bit") is not True
        or not isinstance(packet_ids, list)
        or len(packet_ids) != _MAX_SMOKE_PACKETS
        or not all(isinstance(value, str) and value for value in packet_ids)
        or len(set(packet_ids)) != len(packet_ids)
    ):
        raise CertifiedCanonicalError("component-selection smoke route violates its fixed model or five-packet contract")
    max_input_tokens = route.get("max_input_tokens")
    max_new_tokens = route.get("max_new_tokens")
    if (
        not isinstance(max_input_tokens, int)
        or isinstance(max_input_tokens, bool)
        or not 512 <= max_input_tokens <= 4096
        or not isinstance(max_new_tokens, int)
        or isinstance(max_new_tokens, bool)
        or not 64 <= max_new_tokens <= 512
    ):
        raise CertifiedCanonicalError("component-selection smoke token budget is invalid")
    normalized_route = {
        "route_id": route_id,
        "model_id": model_id,
        "revision": revision,
        "parameter_count_billions": parameter_count,
        "load_in_4bit": True,
        "max_input_tokens": max_input_tokens,
        "max_new_tokens": max_new_tokens,
        "packet_ids": list(packet_ids),
    }
    return config, paths, normalized_route


def build_phase5_component_selection_smoke_job(*, config_path: Path, output_dir: Path) -> Phase5ComponentSelectionSmokeJobResult:
    """Select exactly five immutable packets for a proposal-only GPU smoke run."""
    _, paths, route = _validated_config(config_path)
    inputs = list(paths.values())
    _new_output(output_dir, inputs)
    before_hashes = {name: sha256_file(path) for name, path in paths.items()}
    packet_manifest = _json(paths["packet_manifest"])
    if (
        packet_manifest.get("protocol") != PHASE5_COMPONENT_SELECTION_PROTOCOL
        or packet_manifest.get("run_status") != "phase_5_component_selection_packets_complete_not_dispatched"
        or packet_manifest.get("navigation_overlay_required") is not True
        or packet_manifest.get("training_eligible") is not False
        or packet_manifest.get("certification_allowed") is not False
    ):
        raise CertifiedCanonicalError("component-selection packet manifest is unsupported or promotable")
    navigation_inputs = packet_manifest.get("inputs") or {}
    if not isinstance(navigation_inputs, Mapping) or not _NAVIGATION_INPUT_NAMES.issubset(navigation_inputs):
        raise CertifiedCanonicalError("component-selection packet manifest is missing the required navigation overlay")
    expected = ((packet_manifest.get("outputs") or {}).get(paths["packets"].name) or {}).get("sha256")
    if expected != sha256_file(paths["packets"]):
        raise CertifiedCanonicalError("component-selection packets do not match their manifest")
    packet_by_id = {
        str(packet.get("component_selection_packet_id") or ""): packet
        for packet in _jsonl(paths["packets"])
        if packet.get("route_id") == route["route_id"]
    }
    if not packet_by_id or len(packet_by_id) != len(
        [packet for packet in _jsonl(paths["packets"]) if packet.get("route_id") == route["route_id"]]
    ):
        raise CertifiedCanonicalError("component-selection packet identities are invalid")
    if set(route["packet_ids"]) - set(packet_by_id):
        raise CertifiedCanonicalError("component-selection smoke route references unknown packet IDs")
    selected = [packet_by_id[packet_id] for packet_id in route["packet_ids"]]
    statuses = {str(packet.get("source_first_route_status") or "") for packet in selected}
    if not {
        "DETERMINISTIC_NOTE_CONTEXT_COMPONENTS_REQUIRED",
        "DETERMINISTIC_ROW_LABEL_RELATION_CONTEXT_ONLY",
    }.issubset(statuses):
        raise CertifiedCanonicalError("five-packet smoke must cover both note and row-label source contexts")
    requests: list[dict[str, Any]] = []
    for packet in selected:
        render_phase5_component_selection_prompt(packet)
        payload = {
            "schema_version": PHASE5_COMPONENT_SELECTION_SMOKE_SCHEMA_VERSION,
            "protocol": PHASE5_COMPONENT_SELECTION_SMOKE_PROTOCOL,
            "route": route,
            "component_selection_packet_id": packet["component_selection_packet_id"],
            "packet": packet,
            "training_eligible": False,
            "certification_allowed": False,
        }
        requests.append({"component_selection_request_id": _sha_json(payload), **payload})
    after_hashes = {name: sha256_file(path) for name, path in paths.items()}
    if after_hashes != before_hashes:
        raise CertifiedCanonicalError("component-selection smoke job construction changed a hash-bound input")
    output_dir.mkdir(parents=True)
    packets_path = output_dir / _PACKETS_NAME
    with packets_path.open("x", encoding="utf-8") as file:
        for packet in selected:
            file.write(json.dumps(packet, ensure_ascii=False, sort_keys=True) + "\n")
    selected_packet_manifest = {
        "schema_version": 1,
        "protocol": PHASE5_COMPONENT_SELECTION_PROTOCOL,
        "route_id": route["route_id"],
        "run_status": "phase_5_component_selection_packets_complete_not_dispatched",
        "source_packet_manifest_sha256": sha256_file(paths["packet_manifest"]),
        "navigation_overlay_required": True,
        "selected_packet_ids": list(route["packet_ids"]),
        "outputs": {_PACKETS_NAME: {"sha256": sha256_file(packets_path)}},
        "training_eligible": False,
        "certification_allowed": False,
    }
    selected_packet_manifest_path = output_dir / _PACKET_MANIFEST_NAME
    selected_packet_manifest_path.write_text(
        json.dumps(selected_packet_manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    requests_path = output_dir / _REQUESTS_NAME
    with requests_path.open("x", encoding="utf-8") as file:
        for request in requests:
            file.write(json.dumps(request, ensure_ascii=False, sort_keys=True) + "\n")
    report = {
        "schema_version": PHASE5_COMPONENT_SELECTION_SMOKE_SCHEMA_VERSION,
        "protocol": PHASE5_COMPONENT_SELECTION_SMOKE_PROTOCOL,
        "route": route,
        "run_status": "prepared_component_selection_smoke_not_executed",
        "request_count": len(requests),
        "source_status_counts": {
            status: sum(1 for request in requests if request["packet"].get("source_first_route_status") == status)
            for status in sorted(statuses)
        },
        "input_hashes_unchanged": True,
        "navigation_overlay_required": True,
        "model_execution_allowed": True,
        "training_eligible_output_count": 0,
        "certification_allowed": False,
    }
    report_path = output_dir / _REPORT_NAME
    report_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": PHASE5_COMPONENT_SELECTION_SMOKE_SCHEMA_VERSION,
        "protocol": PHASE5_COMPONENT_SELECTION_SMOKE_PROTOCOL,
        "route": route,
        "run_status": "prepared_component_selection_smoke_not_executed",
        "inputs": {name: {"path": str(path), "sha256": before_hashes[name]} for name, path in paths.items()},
        "outputs": {
            _PACKETS_NAME: {"sha256": sha256_file(packets_path)},
            _PACKET_MANIFEST_NAME: {"sha256": sha256_file(selected_packet_manifest_path)},
            _REQUESTS_NAME: {"sha256": sha256_file(requests_path)},
            _REPORT_NAME: {"sha256": sha256_file(report_path)},
        },
        "input_hashes_unchanged": True,
        "navigation_overlay_required": True,
        "model_execution_allowed": True,
        "training_eligible": False,
        "certification_allowed": False,
    }
    manifest_path = output_dir / _MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return Phase5ComponentSelectionSmokeJobResult(output_dir=output_dir, request_count=len(requests), manifest_path=manifest_path)
