#!/usr/bin/env python3
"""Build a deterministic, non-promotable Phase 5 component-selection pilot."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import yaml

from finance_query.certified_canonical.phase5_component_selection import (
    PHASE5_COMPONENT_SELECTION_PROTOCOL,
    render_phase5_component_selection_prompt,
)
from finance_query.certified_canonical.pipeline import CertifiedCanonicalError
from finance_query.table_structure import sha256_file


PROTOCOL = "vifinqa_ccl_phase5_component_selection_pilot_v1"
SCHEMA_VERSION = 1
MAX_MODEL_PARAMETERS_BILLIONS = 14.7
MAX_PILOT_PACKETS = 32
_PACKETS_NAME = "component_selection_pilot_packets_v1.jsonl"
_PACKET_MANIFEST_NAME = "component_selection_pilot_packet_manifest.json"
_REQUESTS_NAME = "component_selection_pilot_requests_v1.jsonl"
_TOKEN_PREFLIGHT_NAME = "component_selection_pilot_token_preflight.json"
_REPORT_NAME = "component_selection_pilot_job_report.json"
_MANIFEST_NAME = "component_selection_pilot_job_manifest.json"
_NOTE_STATUS = "DETERMINISTIC_NOTE_CONTEXT_COMPONENTS_REQUIRED"
_ROW_STATUS = "DETERMINISTIC_ROW_LABEL_RELATION_CONTEXT_ONLY"
_TOKEN_BUDGET_SELECTION_STRATEGY = "token_budget_then_packet_id_by_source_context"
_NAVIGATION_INPUT_NAMES = frozenset(
    {"report_navigation_overlay_v1.jsonl", "report_navigation_overlay_manifest.json"}
)


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


def _write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    with path.open("x", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _load_tokenizer(*, model_id: str, revision: str) -> Any:
    """Load the exact route tokenizer without silently substituting a tokenizer."""
    try:
        from transformers import AutoTokenizer
    except ImportError as error:
        raise CertifiedCanonicalError("pilot token preflight requires transformers") from error
    try:
        return AutoTokenizer.from_pretrained(model_id, revision=revision, local_files_only=True)
    except Exception as error:
        raise CertifiedCanonicalError("pilot token preflight cannot load the exact route tokenizer locally") from error


def _chat_prompt(*, tokenizer: Any, prompt: str, model_id: str) -> str:
    kwargs: dict[str, Any] = {"tokenize": False, "add_generation_prompt": True}
    if model_id.casefold().startswith("qwen/qwen3"):
        kwargs["enable_thinking"] = False
    return str(tokenizer.apply_chat_template([{"role": "user", "content": prompt}], **kwargs))


def _input_token_count(*, tokenizer: Any, prompt: str) -> int:
    encoded = tokenizer(prompt, add_special_tokens=False)
    input_ids = getattr(encoded, "input_ids", None)
    if not isinstance(input_ids, list):
        raise CertifiedCanonicalError("pilot tokenizer did not return a list of input IDs")
    return len(input_ids)


def _token_preflight(
    *, packets: list[dict[str, Any]], model_id: str, revision: str, max_input_tokens: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Measure the final chat prompt before deterministic pilot selection."""
    tokenizer = _load_tokenizer(model_id=model_id, revision=revision)
    chat_template = getattr(tokenizer, "chat_template", None)
    if not isinstance(chat_template, str) or not chat_template:
        raise CertifiedCanonicalError("pilot tokenizer has no chat template")
    results: list[dict[str, Any]] = []
    for packet in packets:
        packet_id = str(packet.get("component_selection_packet_id") or "")
        if not packet_id:
            raise CertifiedCanonicalError("pilot source packet lacks an identity")
        prompt = render_phase5_component_selection_prompt(packet)
        chat_prompt = _chat_prompt(tokenizer=tokenizer, prompt=prompt, model_id=model_id)
        token_count = _input_token_count(tokenizer=tokenizer, prompt=chat_prompt)
        results.append(
            {
                "component_selection_packet_id": packet_id,
                "source_first_route_status": str(packet.get("source_first_route_status") or ""),
                "input_token_count": token_count,
                "within_max_input_tokens": token_count <= max_input_tokens,
                "exclusion_reason": None if token_count <= max_input_tokens else "INPUT_TOKEN_BUDGET_EXCEEDED",
            }
        )
    tokenizer_info = {
        "model_id": model_id,
        "revision": revision,
        "tokenizer_class": type(tokenizer).__name__,
        "chat_template_sha256": hashlib.sha256(chat_template.encode("utf-8")).hexdigest(),
        "local_files_only": True,
    }
    return results, tokenizer_info


def build_component_selection_pilot_job(*, config_path: Path, output_dir: Path) -> dict[str, Any]:
    config_path = config_path.resolve()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if config.get("protocol") != PROTOCOL or int(config.get("schema_version") or 0) != SCHEMA_VERSION:
        raise CertifiedCanonicalError("unsupported component-selection pilot configuration")
    inputs = config.get("inputs")
    selection = config.get("selection")
    route = config.get("route")
    if not isinstance(inputs, Mapping) or not isinstance(selection, Mapping) or not isinstance(route, Mapping):
        raise CertifiedCanonicalError("pilot configuration requires inputs, selection and route")
    repo_root = config_path.parent.parent.resolve()
    packet_path = _resolve(repo_root, inputs.get("packets"))
    manifest_path = _resolve(repo_root, inputs.get("packet_manifest"))
    immutable_inputs = [config_path, packet_path, manifest_path]
    if any(not path.is_file() for path in immutable_inputs):
        raise FileNotFoundError("pilot configuration references a missing input")
    if output_dir.exists() or output_dir.resolve() in {path.resolve() for path in immutable_inputs}:
        raise FileExistsError("pilot output-dir must be new and distinct from immutable inputs")
    before_hashes = {path.name: sha256_file(path) for path in immutable_inputs}
    packet_manifest = _json(manifest_path)
    if (
        packet_manifest.get("protocol") != PHASE5_COMPONENT_SELECTION_PROTOCOL
        or packet_manifest.get("run_status") != "phase_5_component_selection_packets_complete_not_dispatched"
        or packet_manifest.get("training_eligible") is not False
        or packet_manifest.get("certification_allowed") is not False
        or ((packet_manifest.get("outputs") or {}).get(packet_path.name) or {}).get("sha256") != sha256_file(packet_path)
    ):
        raise CertifiedCanonicalError("pilot source packet manifest is unsupported or changed")
    navigation_inputs = packet_manifest.get("inputs") or {}
    if (
        packet_manifest.get("navigation_overlay_required") is not True
        or not isinstance(navigation_inputs, Mapping)
        or not _NAVIGATION_INPUT_NAMES.issubset(navigation_inputs)
    ):
        raise CertifiedCanonicalError("pilot source packet manifest is missing the required navigation overlay")
    route_id = str(route.get("route_id") or "")
    model_id = str(route.get("model_id") or "")
    revision = str(route.get("revision") or "")
    parameter_count = float(route.get("parameter_count_billions") or 0)
    max_input_tokens = route.get("max_input_tokens")
    max_new_tokens = route.get("max_new_tokens")
    if (
        not route_id
        or not model_id
        or not revision
        or not 0 < parameter_count < MAX_MODEL_PARAMETERS_BILLIONS
        or route.get("load_in_4bit") is not True
        or not isinstance(max_input_tokens, int)
        or not 512 <= max_input_tokens <= 4096
        or not isinstance(max_new_tokens, int)
        or not 64 <= max_new_tokens <= 512
    ):
        raise CertifiedCanonicalError("pilot route violates the sub-14.7B 4-bit contract")
    if selection.get("strategy") != _TOKEN_BUDGET_SELECTION_STRATEGY:
        raise CertifiedCanonicalError("pilot selection strategy is not deterministic")
    note_count = selection.get("note_context_count")
    row_count = selection.get("row_label_context_count")
    if (
        not isinstance(note_count, int)
        or not isinstance(row_count, int)
        or note_count < 1
        or row_count < 1
        or note_count + row_count > MAX_PILOT_PACKETS
    ):
        raise CertifiedCanonicalError("pilot source-context quotas are invalid")
    source_rows = [row for row in _jsonl(packet_path) if row.get("route_id") == route_id]
    packet_by_id = {str(row.get("component_selection_packet_id") or ""): row for row in source_rows}
    if not packet_by_id or len(packet_by_id) != len(source_rows):
        raise CertifiedCanonicalError("pilot source packet identities are invalid")
    grouped = {
        _NOTE_STATUS: sorted((row for row in source_rows if row.get("source_first_route_status") == _NOTE_STATUS), key=lambda row: str(row["component_selection_packet_id"])),
        _ROW_STATUS: sorted((row for row in source_rows if row.get("source_first_route_status") == _ROW_STATUS), key=lambda row: str(row["component_selection_packet_id"])),
    }
    if len(grouped[_NOTE_STATUS]) < note_count or len(grouped[_ROW_STATUS]) < row_count:
        raise CertifiedCanonicalError("pilot quotas exceed the available source-context strata")
    token_preflight, tokenizer_info = _token_preflight(
        packets=grouped[_NOTE_STATUS] + grouped[_ROW_STATUS],
        model_id=model_id,
        revision=revision,
        max_input_tokens=max_input_tokens,
    )
    preflight_by_id = {str(row["component_selection_packet_id"]): row for row in token_preflight}
    eligible_grouped = {
        status: [row for row in rows if preflight_by_id[str(row["component_selection_packet_id"])]["within_max_input_tokens"]]
        for status, rows in grouped.items()
    }
    if len(eligible_grouped[_NOTE_STATUS]) < note_count or len(eligible_grouped[_ROW_STATUS]) < row_count:
        raise CertifiedCanonicalError("pilot token budget leaves too few source-context packets for a quota")
    selected = eligible_grouped[_NOTE_STATUS][:note_count] + eligible_grouped[_ROW_STATUS][:row_count]
    selected_ids = [str(row["component_selection_packet_id"]) for row in selected]
    if len(selected_ids) != len(set(selected_ids)):
        raise CertifiedCanonicalError("pilot selected packet identities are not unique")
    normalized_route = {
        "route_id": route_id,
        "model_id": model_id,
        "revision": revision,
        "parameter_count_billions": parameter_count,
        "load_in_4bit": True,
        "max_input_tokens": max_input_tokens,
        "max_new_tokens": max_new_tokens,
        "packet_ids": selected_ids,
    }
    requests: list[dict[str, Any]] = []
    for packet in selected:
        render_phase5_component_selection_prompt(packet)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "protocol": PROTOCOL,
            "route": normalized_route,
            "component_selection_packet_id": packet["component_selection_packet_id"],
            "packet": packet,
            "training_eligible": False,
            "certification_allowed": False,
        }
        requests.append({"component_selection_request_id": _sha_json(payload), **payload})
    after_hashes = {path.name: sha256_file(path) for path in immutable_inputs}
    if after_hashes != before_hashes:
        raise CertifiedCanonicalError("pilot construction changed a hash-bound input")
    output_dir.mkdir(parents=True)
    token_preflight_path = output_dir / _TOKEN_PREFLIGHT_NAME
    token_preflight_payload = {
        "schema_version": SCHEMA_VERSION,
        "protocol": PROTOCOL,
        "route_id": route_id,
        "max_input_tokens": max_input_tokens,
        "tokenizer": tokenizer_info,
        "source_candidate_counts": {status: len(rows) for status, rows in grouped.items()},
        "eligible_candidate_counts": {status: len(rows) for status, rows in eligible_grouped.items()},
        "results": token_preflight,
        "training_eligible": False,
        "certification_allowed": False,
    }
    token_preflight_path.write_text(json.dumps(token_preflight_payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    selected_packets_path = output_dir / _PACKETS_NAME
    _write_jsonl(selected_packets_path, selected)
    selected_manifest = {
        "schema_version": SCHEMA_VERSION,
        "protocol": PHASE5_COMPONENT_SELECTION_PROTOCOL,
        "route_id": route_id,
        "run_status": "phase_5_component_selection_packets_complete_not_dispatched",
        "source_packet_manifest_sha256": sha256_file(manifest_path),
        "navigation_overlay_required": True,
        "selection": {
            "strategy": selection["strategy"],
            "note_context_count": note_count,
            "row_label_context_count": row_count,
            "token_preflight_sha256": sha256_file(token_preflight_path),
            "max_input_tokens": max_input_tokens,
            "selected_input_token_counts": {
                packet_id: preflight_by_id[packet_id]["input_token_count"] for packet_id in selected_ids
            },
        },
        "selected_packet_ids": selected_ids,
        "outputs": {_PACKETS_NAME: {"sha256": sha256_file(selected_packets_path)}},
        "training_eligible": False,
        "certification_allowed": False,
    }
    selected_manifest_path = output_dir / _PACKET_MANIFEST_NAME
    selected_manifest_path.write_text(json.dumps(selected_manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    requests_path = output_dir / _REQUESTS_NAME
    _write_jsonl(requests_path, requests)
    report = {
        "schema_version": SCHEMA_VERSION,
        "protocol": PROTOCOL,
        "route": normalized_route,
        "run_status": "prepared_component_selection_pilot_not_executed",
        "request_count": len(requests),
        "source_status_counts": dict(sorted(Counter(str(row["source_first_route_status"]) for row in selected).items())),
        "token_preflight": {
            "sha256": sha256_file(token_preflight_path),
            "source_candidate_counts": {status: len(rows) for status, rows in grouped.items()},
            "eligible_candidate_counts": {status: len(rows) for status, rows in eligible_grouped.items()},
            "max_selected_input_tokens": max(preflight_by_id[packet_id]["input_token_count"] for packet_id in selected_ids),
        },
        "input_hashes_unchanged": True,
        "navigation_overlay_required": True,
        "model_execution_allowed": True,
        "training_eligible_output_count": 0,
        "certification_allowed": False,
    }
    report_path = output_dir / _REPORT_NAME
    report_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    job_manifest = {
        "schema_version": SCHEMA_VERSION,
        "protocol": PROTOCOL,
        "route": normalized_route,
        "run_status": "prepared_component_selection_pilot_not_executed",
        "inputs": {path.name: {"path": str(path), "sha256": before_hashes[path.name]} for path in immutable_inputs},
        "outputs": {
            _PACKETS_NAME: {"sha256": sha256_file(selected_packets_path)},
            _PACKET_MANIFEST_NAME: {"sha256": sha256_file(selected_manifest_path)},
            _REQUESTS_NAME: {"sha256": sha256_file(requests_path)},
            _TOKEN_PREFLIGHT_NAME: {"sha256": sha256_file(token_preflight_path)},
            _REPORT_NAME: {"sha256": sha256_file(report_path)},
        },
        "input_hashes_unchanged": True,
        "navigation_overlay_required": True,
        "model_execution_allowed": True,
        "training_eligible": False,
        "certification_allowed": False,
    }
    job_manifest_path = output_dir / _MANIFEST_NAME
    job_manifest_path.write_text(json.dumps(job_manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {"output_dir": str(output_dir), "request_count": len(requests), "manifest_path": str(job_manifest_path), "training_eligible": False, "certification_allowed": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_component_selection_pilot_job(config_path=args.config, output_dir=args.output_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
