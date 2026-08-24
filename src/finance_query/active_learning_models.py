"""Open-source model execution contracts for active-learning shadow cycles.

This module prepares numeric-free packets, validates raw proposer/critic JSON,
and reconciles blind outputs.  It never executes a model at import time and no
output is answer-, training-, promotion-, or submission-eligible.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterable, Mapping, Sequence

from .active_learning import MAX_MODEL_PARAMETERS_BILLIONS, MODEL_POLICY_PROTOCOL, QUEUE_OUTPUTS, _model_policy
from .evidence_closure import canonical_sha256, load_json, load_jsonl, sha256_file
from .qwen_inference import parse_json_object


MODEL_JOB_PROTOCOL = "vifinqa_active_learning_open_source_model_job_v1"
MODEL_PACKET_PROTOCOL = "vifinqa_active_learning_model_packet_v1"
MODEL_REQUEST_PROTOCOL = "vifinqa_active_learning_model_request_v1"
RAW_RESPONSE_PROTOCOL = "vifinqa_active_learning_raw_model_response_v1"
VALIDATED_RESPONSE_PROTOCOL = "vifinqa_active_learning_validated_model_response_v1"
RECONCILIATION_PROTOCOL = "vifinqa_active_learning_model_reconciliation_v1"

MODEL_ROLES = ("open_source_model_proposer", "open_source_model_critic")
QUEUE_RULE_TYPES = {
    "formula_definition": "formula_definition_rule",
    "operand_compatibility": "operand_compatibility_rule",
    "route_binding": "route_binding_rule",
    "route_operator": "route_operator_rule",
    "route_cause": "route_cause_rule",
    "temporal": "temporal_interpretation_rule",
    "v12_recertification": "v13_recertification_rule",
}
ALLOWED_REASON_CODES = {
    "SUPPORTED_BY_PACKET",
    "INSUFFICIENT_PACKET_EVIDENCE",
    "AMBIGUOUS_SEMANTICS",
    "UNKNOWN_OR_OOD_PATTERN",
    "POLICY_SCHEMA_UNSUPPORTED",
    "SOURCE_REFERENCE_MISSING",
    "MODEL_RUNTIME_ERROR",
}
FORBIDDEN_KEYS = {
    "answer",
    "answer_decimal",
    "numeric_answer",
    "raw_value",
    "parsed_value",
    "source_value",
    "final_answer",
    "training_eligible",
    "promotion_allowed",
    "submission_eligible",
    "human_verified",
}
_HEX_64 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class ModelJobResult:
    output_dir: Path
    manifest_path: Path
    packet_count: int


@dataclass(frozen=True, slots=True)
class ValidationResult:
    output_path: Path
    valid_count: int
    invalid_count: int


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    output_dir: Path
    manifest_path: Path
    agreement_count: int
    escalation_count: int


def _json_sha(value: object) -> str:
    return canonical_sha256(value)


def _require_sha(value: object, label: str) -> str:
    if not isinstance(value, str) or _HEX_64.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _scan_forbidden(value: object, *, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).casefold()
            if normalized in FORBIDDEN_KEYS or normalized.endswith("_answer"):
                raise ValueError(f"numeric/authorizing field forbidden at {path}.{key}")
            _scan_forbidden(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _scan_forbidden(item, path=f"{path}[{index}]")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _manifest_output(manifest: Mapping[str, Any], name: str) -> Path:
    record = (manifest.get("outputs") or {}).get(name)
    if not isinstance(record, Mapping):
        raise ValueError(f"manifest lacks output {name}")
    path = Path(str(record.get("path") or ""))
    if not path.is_file() or sha256_file(path) != record.get("sha256"):
        raise ValueError(f"manifest output hash mismatch: {name}")
    return path


def _manifest_input(manifest: Mapping[str, Any], name: str, *, manifest_path: Path) -> Path:
    record = (manifest.get("inputs") or {}).get(name)
    if not isinstance(record, Mapping):
        raise ValueError(f"manifest lacks input {name}")
    path = Path(str(record.get("path") or ""))
    if not path.is_absolute():
        path = manifest_path.resolve().parent / path
    if not path.is_file() or sha256_file(path) != record.get("sha256"):
        raise ValueError(f"manifest input hash mismatch: {name}")
    return path


def _source_packet_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    """Expose the typed, numeric-free closure packet rather than a bare hash."""
    projection = {
        key: value
        for key, value in row.items()
        if key not in {"schema_version", "protocol", "question_id", "source_contract", "submission_eligible"}
    }
    _scan_forbidden(projection)
    return projection


def _routes_by_role(policy_path: Path) -> dict[str, dict[str, Any]]:
    routes = _model_policy(policy_path)
    by_role: dict[str, dict[str, Any]] = {}
    for route in routes.values():
        role = str(route.get("role") or "")
        if role not in MODEL_ROLES or role in by_role:
            raise ValueError("model policy must define one proposer and one critic route")
        if float(route["parameter_count_billions"]) >= MAX_MODEL_PARAMETERS_BILLIONS:
            raise ValueError("model route violates strict parameter cap")
        by_role[role] = route
    if set(by_role) != set(MODEL_ROLES):
        raise ValueError("model policy lacks proposer or critic")
    if by_role[MODEL_ROLES[0]]["model_id"].split("/")[0] == by_role[MODEL_ROLES[1]]["model_id"].split("/")[0]:
        raise ValueError("proposer and critic must use different model families")
    return by_role


def _policy_contract(queue: str) -> dict[str, Any]:
    rule_type = QUEUE_RULE_TYPES.get(queue)
    if rule_type is None:
        raise ValueError(f"unsupported active-learning queue {queue}")
    return {
        "expected_rule_type": rule_type,
        "policy_keys": ["rule_type", "rule_value", "applicability_conditions", "required_checks"],
        "value_contract": "categorical strings only; no financial value or answer",
        "effect": "proposal_only_revalidate_every_question",
    }


def build_model_job(
    *,
    active_cycle_manifest_path: Path,
    model_policy_path: Path,
    output_dir: Path,
) -> ModelJobResult:
    """Build immutable blind proposer/critic requests for the 64 active items."""
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite model job: {output_dir}")
    cycle = load_json(active_cycle_manifest_path)
    if cycle.get("protocol") != "vifinqa_active_learning_cycle_v1":
        raise ValueError("model job requires active-learning cycle V1")
    if (cycle.get("release_decision") or {}).get("status") != "blocked":
        raise ValueError("model job cannot consume an authorizing cycle")
    routes = _routes_by_role(model_policy_path)
    batch = load_jsonl(_manifest_output(cycle, "review_batch"))
    clusters = {
        str(row["cluster_id"]): row
        for row in load_jsonl(_manifest_output(cycle, "cluster_inventory"))
    }
    active = [row for row in batch if row.get("evaluation_role") == "active_learning"]
    if len(active) != int((cycle.get("counts") or {}).get("active_learning_review_count") or -1):
        raise ValueError("active review count differs from cycle manifest")
    closure_path = _manifest_input(cycle, "closure_manifest", manifest_path=active_cycle_manifest_path)
    closure = load_json(closure_path)
    if closure.get("protocol") != "vifinqa_v13_evidence_closure_workbench_v1":
        raise ValueError("model job requires a V13 evidence-closure manifest")
    source_packets: dict[tuple[str, int], dict[str, Any]] = {}
    queues_needed = {str(row["queue"]) for row in active}
    if not queues_needed <= set(QUEUE_OUTPUTS):
        raise ValueError("active batch contains an unsupported closure queue")
    for queue in sorted(queues_needed):
        output_name = QUEUE_OUTPUTS[queue]
        for row in load_jsonl(_manifest_output(closure, output_name)):
            key = (queue, int(row["question_id"]))
            if key in source_packets:
                raise ValueError(f"duplicate closure source packet for {queue}/Q{key[1]}")
            source_packets[key] = row
    packets: list[dict[str, Any]] = []
    requests_by_role: dict[str, list[dict[str, Any]]] = {role: [] for role in MODEL_ROLES}
    for item in sorted(active, key=lambda row: int(row["question_id"])):
        qid = int(item["question_id"])
        refs = sorted({_require_sha(value, "allowed source reference") for value in item.get("allowed_source_ref_sha256") or []})
        if not refs:
            raise ValueError(f"Q{qid} model packet has no packet-bound source reference")
        cluster = clusters.get(str(item.get("cluster_id") or ""))
        if cluster is None:
            raise ValueError(f"Q{qid} lacks cluster inventory row")
        source_packet = source_packets.get((str(item["queue"]), qid))
        if source_packet is None or source_packet.get("packet_id") != item.get("source_packet_id"):
            raise ValueError(f"Q{qid} lacks its hash-bound closure source packet")
        payload = {
            "schema_version": 1,
            "protocol": MODEL_PACKET_PROTOCOL,
            "review_item_id": item["review_item_id"],
            "packet_payload_sha256": item["packet_payload_sha256"],
            "question_id": qid,
            "queue": item["queue"],
            "assignment_role": item["assignment_role"],
            "raw_claim": item["raw_claim"],
            "cluster_id": item["cluster_id"],
            "cluster_features": cluster.get("features"),
            "source_packet_projection": _source_packet_projection(source_packet),
            "source_packet_ref_sha256": source_packet["packet_id"],
            "allowed_source_ref_sha256": refs,
            "policy_contract": _policy_contract(str(item["queue"])),
            "blind_review": True,
            "prior_model_output_visible": False,
            "numeric_answer_visible": False,
            "authorization_boundary": "proposal_only_non_authorizing",
        }
        _scan_forbidden(payload)
        packet = {**payload, "model_packet_id": _json_sha(payload)}
        packets.append(packet)
        for role in MODEL_ROLES:
            route = routes[role]
            request_payload = {
                "schema_version": 1,
                "protocol": MODEL_REQUEST_PROTOCOL,
                "model_role": role,
                "route_id": route["route_id"],
                "model_id": route["model_id"],
                "model_revision": route["revision"],
                "weight_shards_sha256": _json_sha(route["weight_shards"]),
                "model_packet_id": packet["model_packet_id"],
                "review_item_id": item["review_item_id"],
                "question_id": qid,
                "packet": packet,
                "response_contract": {
                    "protocol": VALIDATED_RESPONSE_PROTOCOL,
                    "verdicts": ["PROPOSE_RULE", "ABSTAIN"],
                    "allowed_reason_codes": sorted(ALLOWED_REASON_CODES),
                    "policy_contract": packet["policy_contract"],
                },
                "generation_contract": {
                    "do_sample": False,
                    "max_new_tokens": 512,
                    "temperature": 0,
                    "qwen3_thinking_enabled": False if str(route["model_id"]).startswith("Qwen/Qwen3-") else None,
                },
                "training_eligible": False,
                "certification_allowed": False,
                "submission_eligible": False,
            }
            requests_by_role[role].append({**request_payload, "request_id": _json_sha(request_payload)})
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        packet_path = staging / "open_source_model_review_packets_v1.jsonl"
        _write_jsonl(packet_path, packets)
        request_paths = {
            MODEL_ROLES[0]: staging / "qwen3_8b_proposer_requests_v1.jsonl",
            MODEL_ROLES[1]: staging / "mistral_nemo_12b_critic_requests_v1.jsonl",
        }
        for role, path in request_paths.items():
            _write_jsonl(path, requests_by_role[role])
        summary = {
            "schema_version": 1,
            "protocol": MODEL_JOB_PROTOCOL,
            "status": "PREPARED_GPU_EXECUTION_NOT_RUN",
            "packet_count": len(packets),
            "request_count_by_role": {role: len(rows) for role, rows in requests_by_role.items()},
            "blind_critic": True,
            "chatgpt_in_model_graph": False,
            "training_eligible": False,
            "certification_allowed": False,
            "release_status": "blocked",
        }
        summary_path = staging / "active_learning_model_job_summary.json"
        _write_json(summary_path, summary)
        manifest = {
            **summary,
            "inputs": {
                "active_cycle_manifest": {"path": str(active_cycle_manifest_path.resolve()), "sha256": sha256_file(active_cycle_manifest_path)},
                "model_policy": {"path": str(model_policy_path.resolve()), "sha256": sha256_file(model_policy_path)},
            },
            "model_routes": routes,
            "outputs": {
                "packets": {"path": str(output_dir / packet_path.name), "sha256": sha256_file(packet_path)},
                "proposer_requests": {"path": str(output_dir / request_paths[MODEL_ROLES[0]].name), "sha256": sha256_file(request_paths[MODEL_ROLES[0]])},
                "critic_requests": {"path": str(output_dir / request_paths[MODEL_ROLES[1]].name), "sha256": sha256_file(request_paths[MODEL_ROLES[1]])},
                "summary": {"path": str(output_dir / summary_path.name), "sha256": sha256_file(summary_path)},
            },
            "definition": {"implementation_path": str(Path(__file__).resolve()), "implementation_sha256": sha256_file(Path(__file__))},
        }
        manifest_path = staging / "active_learning_model_job.manifest.json"
        _write_json(manifest_path, manifest)
        staging.rename(output_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return ModelJobResult(output_dir, output_dir / "active_learning_model_job.manifest.json", len(packets))


def render_prompt(request: Mapping[str, Any]) -> str:
    """Render the same finite JSON-only contract for local or Kaggle runners."""
    if request.get("protocol") != MODEL_REQUEST_PROTOCOL:
        raise ValueError("unexpected model request protocol")
    packet = request.get("packet")
    if not isinstance(packet, Mapping):
        raise ValueError("model request lacks packet")
    role = str(request.get("model_role") or "")
    role_instruction = (
        "Đề xuất một rule có thể tái kiểm tra cho cluster."
        if role == MODEL_ROLES[0]
        else "Đánh giá độc lập cùng packet; không được xem output proposer. Chỉ đề xuất rule nếu packet tự nó đủ."
    )
    response_shape = {
        "schema_version": 1,
        "protocol": VALIDATED_RESPONSE_PROTOCOL,
        "review_item_id": packet["review_item_id"],
        "model_role": role,
        "verdict": "PROPOSE_RULE hoặc ABSTAIN",
        "policy": {
            "rule_type": packet["policy_contract"]["expected_rule_type"],
            "rule_value": "categorical string",
            "applicability_conditions": ["string"],
            "required_checks": ["string"],
        },
        "reason_codes": ["một hoặc nhiều code được phép"],
        "cited_source_ref_sha256": ["chỉ hash nằm trong allowed_source_ref_sha256"],
    }
    return (
        "Bạn là worker open-source trong active-learning shadow; không phải certificate authority.\n"
        f"{role_instruction}\n"
        "Cấm suy đoán hoặc xuất đáp án, giá trị tài chính, exact numeric cell hay trạng thái human_verified.\n"
        "Nếu thiếu bằng chứng: ABSTAIN. Chỉ trả về đúng một JSON object, không markdown.\n"
        f"ALLOWED_REASON_CODES={json.dumps(sorted(ALLOWED_REASON_CODES), ensure_ascii=False)}\n"
        f"PACKET={json.dumps(packet, ensure_ascii=False, sort_keys=True)}\n"
        f"OUTPUT_SHAPE={json.dumps(response_shape, ensure_ascii=False, sort_keys=True)}"
    )


def _strings(value: object, label: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{label} must be a list of non-empty strings")
    return [item.strip() for item in value]


def _validate_policy(policy: object, request: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(policy, Mapping) or set(policy) != {"rule_type", "rule_value", "applicability_conditions", "required_checks"}:
        raise ValueError("policy keys differ from the closed schema")
    expected = request["packet"]["policy_contract"]["expected_rule_type"]
    if policy.get("rule_type") != expected or not isinstance(policy.get("rule_value"), str) or not policy["rule_value"].strip():
        raise ValueError("policy rule type/value is invalid")
    normalized = {
        "rule_type": expected,
        "rule_value": policy["rule_value"].strip(),
        "applicability_conditions": sorted(_strings(policy.get("applicability_conditions"), "applicability_conditions")),
        "required_checks": sorted(_strings(policy.get("required_checks"), "required_checks")),
    }
    _scan_forbidden(normalized)
    return normalized


def validate_raw_responses(
    *,
    requests_path: Path,
    raw_responses_path: Path,
    output_path: Path,
) -> ValidationResult:
    requests = {str(row["request_id"]): row for row in load_jsonl(requests_path)}
    rows = load_jsonl(raw_responses_path)
    if len(rows) != len(requests):
        raise ValueError("raw response count differs from request count")
    seen: set[str] = set()
    validated: list[dict[str, Any]] = []
    for envelope in rows:
        request_id = str(envelope.get("request_id") or "")
        request = requests.get(request_id)
        if request is None or request_id in seen:
            raise ValueError("raw response is not uniquely bound to a request")
        seen.add(request_id)
        if (
            envelope.get("protocol") != RAW_RESPONSE_PROTOCOL
            or envelope.get("review_item_id") != request.get("review_item_id")
            or envelope.get("model_id") != request.get("model_id")
            or envelope.get("model_revision") != request.get("model_revision")
            or envelope.get("model_role") != request.get("model_role")
            or envelope.get("prompt_sha256") != _json_sha(render_prompt(request))
        ):
            raise ValueError("raw response envelope lineage mismatch")
        status = "INVALID_MODEL_RESPONSE"
        reason = "UNPARSED_RESPONSE"
        policy = None
        policy_sha = None
        verdict = "ABSTAIN"
        cited: list[str] = []
        try:
            response = parse_json_object(str(envelope.get("raw_response") or ""))
            _scan_forbidden(response)
            allowed_keys = {"schema_version", "protocol", "review_item_id", "model_role", "verdict", "policy", "reason_codes", "cited_source_ref_sha256"}
            if set(response) != allowed_keys:
                raise ValueError("response keys differ from closed schema")
            if (
                response.get("schema_version") != 1
                or
                response.get("protocol") != VALIDATED_RESPONSE_PROTOCOL
                or response.get("review_item_id") != request.get("review_item_id")
                or response.get("model_role") != request.get("model_role")
            ):
                raise ValueError("response identity differs from request")
            verdict = str(response.get("verdict") or "")
            if verdict not in {"PROPOSE_RULE", "ABSTAIN"}:
                raise ValueError("invalid verdict")
            reasons = sorted(_strings(response.get("reason_codes"), "reason_codes"))
            if any(value not in ALLOWED_REASON_CODES for value in reasons):
                raise ValueError("unknown reason code")
            cited = sorted(_strings(response.get("cited_source_ref_sha256"), "cited_source_ref_sha256", allow_empty=True))
            allowed_refs = set(request["packet"]["allowed_source_ref_sha256"])
            if any(_HEX_64.fullmatch(value) is None or value not in allowed_refs for value in cited):
                raise ValueError("response cites a source reference outside the packet")
            if verdict == "PROPOSE_RULE":
                if request["packet"]["source_packet_ref_sha256"] not in cited:
                    raise ValueError("proposal lacks its typed source-packet reference")
                policy = _validate_policy(response.get("policy"), request)
                policy_sha = _json_sha(policy)
                status = "VALID_PROPOSAL"
                reason = None
            else:
                if response.get("policy") is not None:
                    raise ValueError("ABSTAIN response must not contain a policy")
                status = "VALID_ABSTENTION"
                reason = reasons[0]
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
            reason = f"INVALID_RESPONSE:{type(error).__name__}:{error}"
        payload = {
            "schema_version": 1,
            "protocol": VALIDATED_RESPONSE_PROTOCOL,
            "request_id": request_id,
            "review_item_id": request["review_item_id"],
            "question_id": request["question_id"],
            "model_role": request["model_role"],
            "model_id": request["model_id"],
            "model_revision": request["model_revision"],
            "validation_status": status,
            "verdict": verdict if status != "INVALID_MODEL_RESPONSE" else "ABSTAIN",
            "policy": policy,
            "policy_sha256": policy_sha,
            "cited_source_ref_sha256": cited if status != "INVALID_MODEL_RESPONSE" else [],
            "reason": reason,
            "training_eligible": False,
            "certification_allowed": False,
            "submission_eligible": False,
        }
        validated.append({**payload, "validated_response_id": _json_sha(payload)})
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output_path, sorted(validated, key=lambda row: int(row["question_id"])))
    valid = sum(row["validation_status"] == "VALID_PROPOSAL" for row in validated)
    return ValidationResult(output_path, valid, len(validated) - valid)


def _validate_reconciliation_input(
    row: Mapping[str, Any],
    *,
    packet: Mapping[str, Any],
    expected_role: str,
) -> None:
    if (
        row.get("protocol") != VALIDATED_RESPONSE_PROTOCOL
        or row.get("review_item_id") != packet.get("review_item_id")
        or row.get("question_id") != packet.get("question_id")
        or row.get("model_role") != expected_role
        or row.get("training_eligible") is not False
        or row.get("certification_allowed") is not False
        or row.get("submission_eligible") is not False
    ):
        raise ValueError("validated response boundary or identity mismatch")
    payload = {key: value for key, value in row.items() if key != "validated_response_id"}
    if row.get("validated_response_id") != _json_sha(payload):
        raise ValueError("validated response self-hash mismatch")
    status = row.get("validation_status")
    if status == "VALID_PROPOSAL":
        if row.get("policy_sha256") != _json_sha(row.get("policy")):
            raise ValueError("validated policy hash mismatch")
        cited = set(row.get("cited_source_ref_sha256") or [])
        if packet.get("source_packet_ref_sha256") not in cited:
            raise ValueError("validated proposal is not bound to the typed source packet")
        if not cited <= set(packet.get("allowed_source_ref_sha256") or []):
            raise ValueError("validated proposal cites a source outside the packet")
    elif status not in {"VALID_ABSTENTION", "INVALID_MODEL_RESPONSE"}:
        raise ValueError("unknown validated response status")


def reconcile_validated_responses(
    *,
    packets_path: Path,
    proposer_validated_path: Path,
    critic_validated_path: Path,
    output_dir: Path,
) -> ReconciliationResult:
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite reconciliation: {output_dir}")
    packets = {str(row["review_item_id"]): row for row in load_jsonl(packets_path)}
    proposer = {str(row["review_item_id"]): row for row in load_jsonl(proposer_validated_path)}
    critic = {str(row["review_item_id"]): row for row in load_jsonl(critic_validated_path)}
    if set(packets) != set(proposer) or set(packets) != set(critic):
        raise ValueError("proposer/critic outputs do not cover the same review packets")
    policies: list[dict[str, Any]] = []
    escalation: list[dict[str, Any]] = []
    for review_id in sorted(packets, key=lambda value: int(packets[value]["question_id"])):
        packet = packets[review_id]
        left = proposer[review_id]
        right = critic[review_id]
        _validate_reconciliation_input(left, packet=packet, expected_role=MODEL_ROLES[0])
        _validate_reconciliation_input(right, packet=packet, expected_role=MODEL_ROLES[1])
        agree = (
            left.get("validation_status") == right.get("validation_status") == "VALID_PROPOSAL"
            and left.get("policy_sha256") == right.get("policy_sha256")
            and left.get("policy_sha256") is not None
        )
        if agree:
            payload = {
                "schema_version": 1,
                "protocol": RECONCILIATION_PROTOCOL,
                "question_id": packet["question_id"],
                "review_item_id": review_id,
                "queue": packet["queue"],
                "cluster_id": packet["cluster_id"],
                "policy": left["policy"],
                "policy_sha256": left["policy_sha256"],
                "proposer_validated_response_id": left["validated_response_id"],
                "critic_validated_response_id": right["validated_response_id"],
                "reconciliation_status": "AGREEMENT_MACHINE_PROVISIONAL",
                "materialization_eligible": False,
                "training_eligible": False,
                "promotion_allowed": False,
                "submission_eligible": False,
            }
            policies.append({**payload, "policy_candidate_id": _json_sha(payload)})
        else:
            if "INVALID_MODEL_RESPONSE" in {left.get("validation_status"), right.get("validation_status")}:
                reason = "INVALID_MODEL_RESPONSE"
            elif "VALID_ABSTENTION" in {left.get("validation_status"), right.get("validation_status")}:
                reason = "MODEL_ABSTENTION"
            else:
                reason = "PROPOSER_CRITIC_DISAGREEMENT"
            payload = {
                "schema_version": 1,
                "protocol": RECONCILIATION_PROTOCOL,
                "question_id": packet["question_id"],
                "review_item_id": review_id,
                "queue": packet["queue"],
                "cluster_id": packet["cluster_id"],
                "escalation_reason": reason,
                "allowed_reviewer_types": ["human_adjudicator", "chatgpt_human_equivalent_reviewer"],
                "chatgpt_role": "bounded_external_review_only",
                "training_eligible": False,
                "promotion_allowed": False,
                "submission_eligible": False,
            }
            escalation.append({**payload, "escalation_id": _json_sha(payload)})
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    policies_path = staging / "machine_provisional_policy_candidates_v1.jsonl"
    escalation_path = staging / "human_escalation_queue_v1.jsonl"
    summary = {
        "schema_version": 1,
        "protocol": RECONCILIATION_PROTOCOL,
        "status": "RECONCILED_SHADOW_NOT_PROMOTED",
        "packet_count": len(packets),
        "agreement_count": len(policies),
        "escalation_count": len(escalation),
        "training_eligible_count": 0,
        "promotion_allowed_count": 0,
        "release_status": "blocked",
    }
    try:
        _write_jsonl(policies_path, policies)
        _write_jsonl(escalation_path, escalation)
        summary_path = staging / "reconciliation_summary.json"
        _write_json(summary_path, summary)
        manifest = {
            **summary,
            "inputs": {
                "packets": {"path": str(packets_path.resolve()), "sha256": sha256_file(packets_path)},
                "proposer_validated": {"path": str(proposer_validated_path.resolve()), "sha256": sha256_file(proposer_validated_path)},
                "critic_validated": {"path": str(critic_validated_path.resolve()), "sha256": sha256_file(critic_validated_path)},
            },
            "outputs": {
                "policy_candidates": {"path": str(output_dir / policies_path.name), "sha256": sha256_file(policies_path)},
                "escalation_queue": {"path": str(output_dir / escalation_path.name), "sha256": sha256_file(escalation_path)},
                "summary": {"path": str(output_dir / summary_path.name), "sha256": sha256_file(summary_path)},
            },
        }
        manifest_path = staging / "reconciliation.manifest.json"
        _write_json(manifest_path, manifest)
        staging.rename(output_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return ReconciliationResult(output_dir, output_dir / manifest_path.name, len(policies), len(escalation))
