"""GPU execution and fail-closed validation for CCL Phase 3 proposals.

The executor records raw model text.  The validator can extract only strict
proposal objects whose quoted evidence is present in the request packet.  Both
steps deliberately stop short of semantic certification and data promotion.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterator, Mapping, TextIO

from finance_query.table_structure import sha256_file

from .bakeoff import BAKEOFF_PROTOCOL, BAKEOFF_SCHEMA_VERSION, PROPOSAL_FIELD_RELATION_TYPES
from .evidence_graph import validate_proposal_selection
from .pipeline import CertifiedCanonicalError


_FORBIDDEN_RESPONSE_KEYS = {
    "certified",
    "training_eligible",
    "promotion_allowed",
    "raw_value_replacement",
}
_RESPONSE_OUTPUT_NAMES = ("llm_raw_responses_v1.jsonl", "model_execution_report.json")
_VALIDATION_OUTPUT_NAMES = (
    "llm_proposals_validated_v1.jsonl",
    "llm_response_validation_report.json",
)

_MODEL_ANCHOR_FIELDS = (
    "anchor_id",
    "kind",
    "role",
    "row_index",
    "column_index",
    "quoted_text",
    "canonical_label",
)


@dataclass(frozen=True, slots=True)
class ModelExecutionResult:
    output_dir: Path
    manifest_path: Path
    response_count: int
    route_id: str


@dataclass(frozen=True, slots=True)
class ProposalValidationResult:
    output_dir: Path
    manifest_path: Path
    valid_response_count: int
    invalid_response_count: int


def _sha_json(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_lines(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise CertifiedCanonicalError(f"{path}:{line_number} is not a JSON object")
            yield value


def _write_json_line(file: TextIO, value: Mapping[str, Any]) -> None:
    file.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def _new_output(path: Path, inputs: list[Path]) -> None:
    if path.resolve() in {item.resolve() for item in inputs}:
        raise CertifiedCanonicalError("output-dir cannot be an immutable input")
    if path.exists():
        raise FileExistsError("output-dir must be new; GPU receipts are immutable")
    path.mkdir(parents=True)


def _load_job(job_manifest_path: Path, requests_path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    job = json.loads(job_manifest_path.read_text(encoding="utf-8"))
    if (
        job.get("protocol") != BAKEOFF_PROTOCOL
        or int(job.get("schema_version") or 0) != BAKEOFF_SCHEMA_VERSION
        or job.get("run_status") != "prepared_phase_3_inference_not_executed"
        or job.get("training_eligible") is not False
        or job.get("model_execution_recorded") is not False
    ):
        raise CertifiedCanonicalError("model job manifest is not an immutable prepared Phase 3 job")
    expected = ((job.get("outputs") or {}).get(requests_path.name) or {}).get("sha256")
    if expected != sha256_file(requests_path):
        raise CertifiedCanonicalError("model requests do not match their bake-off job manifest")
    routes = {
        str(route.get("route_id")): dict(route)
        for route in job.get("routes") or []
        if isinstance(route, dict) and route.get("route_id")
    }
    if not routes:
        raise CertifiedCanonicalError("model job manifest has no routes")
    return job, routes


def _route_requests(
    requests_path: Path, route: Mapping[str, Any]
) -> list[dict[str, Any]]:
    requests = [
        request
        for request in _json_lines(requests_path)
        if str(request.get("route_id")) == str(route["route_id"])
    ]
    if not requests:
        raise CertifiedCanonicalError(f"route {route['route_id']} has no prepared requests")
    ids: set[str] = set()
    for request in requests:
        request_id = str(request.get("request_id") or "")
        if (
            not request_id
            or request_id in ids
            or request.get("training_eligible") is not False
            or request.get("model_id") != route.get("model_id")
            or request.get("model_revision") != route.get("revision")
        ):
            raise CertifiedCanonicalError("prepared request contract is malformed")
        max_input_tokens = request.get("max_input_tokens")
        if max_input_tokens is not None and (
            isinstance(max_input_tokens, bool) or not isinstance(max_input_tokens, int) or max_input_tokens < 512
        ):
            raise CertifiedCanonicalError("prepared request max_input_tokens is malformed")
        ids.add(request_id)
    return requests


def _model_packet_view(request: Mapping[str, Any]) -> dict[str, Any]:
    """Render a selection menu, not the full graph, for the model.

    The validator retains the full hash-bound graph. The model only needs
    selectable source anchors and relations whose type is legal for a requested
    field. Hiding canonical slots prevents a model from selecting a useful
    structural node that is deliberately non-selectable evidence.
    """
    task = dict(request.get("task") or {})
    packet = request.get("packet") or {}
    graph = packet.get("evidence_graph") or {}
    anchors = {
        str(item.get("anchor_id")): item
        for item in graph.get("anchors") or []
        if isinstance(item, Mapping) and bool(item.get("selectable")) and item.get("anchor_id")
    }
    anchor_menu = [
        {key: anchor[key] for key in _MODEL_ANCHOR_FIELDS if key in anchor}
        for _, anchor in sorted(anchors.items())
    ]
    requested_fields = [str(field) for field in task.get("fields") or []]
    relation_constraints = task.get("field_relation_constraints") or {}
    relation_options_by_field: dict[str, list[dict[str, Any]]] = {}
    for field in requested_fields:
        allowed_types = PROPOSAL_FIELD_RELATION_TYPES.get(field, frozenset())
        constrained_types = relation_constraints.get(field) if isinstance(relation_constraints, Mapping) else None
        if isinstance(constrained_types, list):
            allowed_types = allowed_types.intersection(str(value) for value in constrained_types)
        options: list[dict[str, Any]] = []
        for relation in graph.get("relations") or []:
            if not isinstance(relation, Mapping) or str(relation.get("relation_type")) not in allowed_types:
                continue
            selectable_endpoints = [
                anchor_id
                for anchor_id in (
                    str(relation.get("source_anchor_id") or ""),
                    str(relation.get("target_anchor_id") or ""),
                )
                if anchor_id in anchors
            ]
            if not selectable_endpoints or not relation.get("relation_id"):
                continue
            options.append(
                {
                    "relation_id": str(relation["relation_id"]),
                    "relation_type": str(relation["relation_type"]),
                    "selectable_endpoint_anchor_ids": sorted(set(selectable_endpoints)),
                }
            )
        relation_options_by_field[field] = sorted(options, key=lambda item: item["relation_id"])
    return {
        "task": task,
        "evidence_selection_menu": {
            "selectable_anchors": anchor_menu,
            "relation_options_by_field": relation_options_by_field,
            "menu_contract": "select only listed anchor IDs; for each proposal choose a relation listed for its field and one of that relation's selectable_endpoint_anchor_ids",
        },
    }


def _prompt(request: Mapping[str, Any], response_schema: Mapping[str, Any]) -> str:
    """Build a deliberately small generation contract around the source packet.

    The hash-bound raw-response envelope already records request/model identity.
    It is both cheaper and safer to have the model propose only semantic fields
    and finite graph IDs, then bind that object to the envelope in validation.
    """
    contract = response_schema.get("response_contract") or {}
    if contract.get("response_contract_version") != "relation_only_json_v3":
        raise CertifiedCanonicalError("response schema must use relation_only_json_v3")
    required = contract.get("required_top_level_keys")
    allowed = contract.get("allowed_top_level_keys")
    if required != ["proposals", "unresolved_conditions"] or allowed != required:
        raise CertifiedCanonicalError("response schema top-level contract is malformed")
    schema_task_contract = contract.get("task_contract")
    if schema_task_contract is not None and schema_task_contract != request.get("task"):
        raise CertifiedCanonicalError("response schema task contract does not match the hash-bound request")
    source_packet = _model_packet_view(request)
    value_constraints = (request.get("task") or {}).get("field_value_constraints") or {}
    constrained_value_lines: list[str] = []
    if isinstance(value_constraints, Mapping):
        for field, constraint in sorted(value_constraints.items()):
            values = constraint.get("allowed_proposed_values") if isinstance(constraint, Mapping) else None
            if isinstance(values, list) and values:
                constrained_value_lines.extend(
                    (
                        f"For {field}, proposed_value must be exactly one of: {json.dumps(values, ensure_ascii=False)}.",
                        f"If no listed value is directly supported, emit no {field} proposal and state it in unresolved_conditions.",
                        f"Do not emit a heading, a relation type, an alias, or free prose as {field}.",
                    )
                )
    return "\n".join(
        (
            "You are a source-anchored semantic proposal worker for Vietnamese financial reports.",
            "Return one JSON object only. Do not use Markdown fences or explanatory prose.",
            "You may propose interpretations but you are not a certification authority.",
            "The ONLY top-level keys are proposals and unresolved_conditions.",
            "Do not echo request_id, internal_table_uid, model_id, model_revision, source text, or this contract.",
            "For each proposal use field, proposed_value, evidence_relation_ids, alternative_candidates, and confidence_not_for_promotion.",
            "Use only evidence_selection_menu: select one relation listed for that proposal field. Never invent an ID.",
            "Do NOT emit evidence_anchor_ids. The deterministic validator derives the selectable source endpoint from each selected relation.",
            "Emit at most two proposals. One well-grounded proposal is complete; abstain instead of proposing another field without a menu option.",
            "Use alternative_candidates: [] when there is no source-bounded alternative; never put plain strings in that list.",
            "If evidence is missing or conflicting, add a short unresolved condition and abstain from that field.",
            "Output shape (replace every placeholder; do not copy the template):",
            '{"proposals":[{"field":"<requested field>","proposed_value":"<supported value>","evidence_relation_ids":["<menu relation id>"],"alternative_candidates":[],"confidence_not_for_promotion":true}],"unresolved_conditions":[]}',
            *constrained_value_lines,
            "Request:",
            json.dumps(source_packet, ensure_ascii=False, sort_keys=True),
        )
    )


def _cuda_runtime_contract(torch_module: Any, *, require_4bit: bool) -> dict[str, Any]:
    """Return an auditable CUDA runtime profile or stop before model download.

    Kaggle can currently schedule a P100 (SM60) while its base image ships a
    newer PyTorch wheel that omits SM60 kernels.  A GPU being visible is not
    sufficient: loading an NF4 model with that wheel fails only after checkpoint
    download.  Check the compiled architectures here, before any model I/O.
    """
    cuda = torch_module.cuda
    if not cuda.is_available():
        if require_4bit:
            raise CertifiedCanonicalError("4-bit model execution requires CUDA")
        return {"cuda_available": False}
    capability = tuple(int(value) for value in cuda.get_device_capability(0))
    if len(capability) != 2:
        raise CertifiedCanonicalError("CUDA device capability is malformed")
    arch_list = [str(value) for value in cuda.get_arch_list()]
    runtime = {
        "cuda_available": True,
        "gpu_name": str(cuda.get_device_name(0)),
        "gpu_compute_capability": list(capability),
        "torch_version": str(torch_module.__version__),
        "torch_cuda_version": str(getattr(torch_module.version, "cuda", "") or ""),
        "torch_arch_list": arch_list,
    }
    if require_4bit:
        if capability < (6, 0):
            raise CertifiedCanonicalError(
                "NF4 4-bit execution requires CUDA compute capability 6.0 or newer"
            )
        expected_arch = f"sm_{capability[0]}{capability[1]}"
        if arch_list and expected_arch not in arch_list:
            raise CertifiedCanonicalError(
                "installed PyTorch wheel cannot execute on "
                f"{runtime['gpu_name']} ({expected_arch}); install a wheel containing {expected_arch} "
                "before loading the 4-bit checkpoint"
            )
    return runtime


def _chat_template_kwargs(route: Mapping[str, Any]) -> dict[str, Any]:
    """Render the model-specific instruction envelope without hidden reasoning.

    Qwen3 enables thinking by default.  This route requires exactly one JSON
    object and deterministically validates it, so hidden ``<think>`` content is
    both needless latency and likely to make an otherwise useful proposal fail
    the closed-world response parser.  Do not impose this argument on other
    model families, whose chat templates need not support it.
    """
    kwargs: dict[str, Any] = {"add_generation_prompt": True, "return_tensors": "pt"}
    if str(route.get("model_id") or "").startswith("Qwen/Qwen3-"):
        kwargs["enable_thinking"] = False
    return kwargs


def _input_ids_for_request(
    tokenizer: Any,
    request: Mapping[str, Any],
    schema: Mapping[str, Any],
    route: Mapping[str, Any],
) -> Any:
    """Tokenize one request exactly as generation will, before GPU allocation."""
    prompt = _prompt(request, schema)
    if hasattr(tokenizer, "apply_chat_template") and tokenizer.chat_template:
        tokenized = tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt}],
            **_chat_template_kwargs(route),
        )
        if isinstance(tokenized, Mapping):
            return tokenized["input_ids"]
        return tokenized
    return tokenizer(prompt, return_tensors="pt")["input_ids"]


def run_model_route(
    *,
    job_manifest_path: Path,
    requests_path: Path,
    response_schema_path: Path,
    output_dir: Path,
    route_id: str,
    max_new_tokens: int = 1024,
    limit: int | None = None,
    load_in_4bit: bool = True,
) -> ModelExecutionResult:
    """Execute one enabled GPU route and retain raw outputs for later validation.

    The function is intentionally not invoked by the bake-off builder. It
    imports model libraries lazily so the deterministic repository tests never
    download a checkpoint or require CUDA.
    """
    if max_new_tokens < 32:
        raise CertifiedCanonicalError("max_new_tokens must be at least 32")
    job_manifest_path = job_manifest_path.resolve()
    requests_path = requests_path.resolve()
    response_schema_path = response_schema_path.resolve()
    if not response_schema_path.is_file():
        raise FileNotFoundError(response_schema_path)
    job, routes = _load_job(job_manifest_path, requests_path)
    route = routes.get(route_id)
    if route is None or not bool(route.get("enabled")):
        raise CertifiedCanonicalError("requested model route is absent or disabled")
    if str(route.get("modality")) != "text":
        raise CertifiedCanonicalError("only text routes can run without hash-bound source page images")
    parameter_count = float(route.get("parameter_count_billions") or 0)
    if not 0 < parameter_count < 14.7:
        raise CertifiedCanonicalError("model route violates strict <14.7B policy")
    schema = json.loads(response_schema_path.read_text(encoding="utf-8"))
    if schema.get("protocol") != BAKEOFF_PROTOCOL:
        raise CertifiedCanonicalError("response schema belongs to a different protocol")
    requests = _route_requests(requests_path, route)
    if limit is not None:
        if limit < 1:
            raise CertifiedCanonicalError("limit must be positive")
        requests = requests[:limit]
    _new_output(output_dir, [job_manifest_path, requests_path, response_schema_path])
    input_hashes = {
        "job_manifest": sha256_file(job_manifest_path),
        "requests": sha256_file(requests_path),
        "response_schema": sha256_file(response_schema_path),
    }
    try:
        import torch
        import bitsandbytes
        import torchvision
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    except ImportError as error:  # pragma: no cover - depends on GPU environment
        raise CertifiedCanonicalError("GPU route requires torch and transformers") from error
    runtime = _cuda_runtime_contract(torch, require_4bit=load_in_4bit)
    runtime["bitsandbytes_version"] = str(bitsandbytes.__version__)
    runtime["torchvision_version"] = str(torchvision.__version__)
    tokenizer = AutoTokenizer.from_pretrained(str(route["model_id"]), revision=str(route["revision"]))
    request_token_counts: dict[str, int] = {}
    for request in requests:
        input_ids = _input_ids_for_request(tokenizer, request, schema, route)
        token_count = int(input_ids.shape[-1])
        request_token_counts[str(request["request_id"])] = token_count
        max_input_tokens = request.get("max_input_tokens")
        if max_input_tokens is not None and token_count > max_input_tokens:
            raise CertifiedCanonicalError(
                "prepared request exceeds its token budget before model loading: "
                f"request_id={request['request_id']} tokens={token_count} budget={max_input_tokens}"
            )
    model_kwargs: dict[str, Any] = {"revision": str(route["revision"])}
    if torch.cuda.is_available():
        model_kwargs["torch_dtype"] = "auto"
        model_kwargs["device_map"] = "auto"
        if load_in_4bit:
            model_kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.float16,
            )
    elif load_in_4bit:
        raise CertifiedCanonicalError("4-bit model execution requires CUDA")
    model = AutoModelForCausalLM.from_pretrained(str(route["model_id"]), **model_kwargs)
    model.eval()

    raw_path = output_dir / "llm_raw_responses_v1.jsonl"
    generation_contract = {
        "do_sample": False,
        "max_new_tokens": max_new_tokens,
        "qwen3_thinking_enabled": False if str(route["model_id"]).startswith("Qwen/Qwen3-") else None,
        "max_input_tokens": sorted(
            {request.get("max_input_tokens") for request in requests if request.get("max_input_tokens") is not None}
        ),
        "observed_max_input_tokens": max(request_token_counts.values()),
    }
    with raw_path.open("x", encoding="utf-8") as file:
        for index, request in enumerate(requests, start=1):
            prompt = _prompt(request, schema)
            input_ids = _input_ids_for_request(tokenizer, request, schema, route).to(model.device)
            with torch.inference_mode():
                output_ids = model.generate(
                    input_ids,
                    do_sample=False,
                    max_new_tokens=max_new_tokens,
                    pad_token_id=tokenizer.eos_token_id,
                )
            completion = tokenizer.decode(output_ids[0][input_ids.shape[-1] :], skip_special_tokens=True)
            completion_token_count = int(output_ids.shape[-1] - input_ids.shape[-1])
            record = {
                "schema_version": BAKEOFF_SCHEMA_VERSION,
                "protocol": BAKEOFF_PROTOCOL,
                "request_id": request["request_id"],
                "internal_table_uid": request["internal_table_uid"],
                "route_id": route["route_id"],
                "model_id": route["model_id"],
                "model_revision": route["revision"],
                "prompt_sha256": _sha_json(prompt),
                "raw_response": completion,
                "completion_token_count": completion_token_count,
                "reached_max_new_tokens": completion_token_count >= max_new_tokens,
                "response_status": "unvalidated_raw_model_output",
                "training_eligible": False,
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            }
            _write_json_line(file, record)
            if index == 1 or index == len(requests) or index % 10 == 0:
                print(
                    json.dumps(
                        {
                            "event": "ccl_phase3_route_progress",
                            "route_id": route_id,
                            "completed_requests": index,
                            "total_requests": len(requests),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    flush=True,
                )
    after_hashes = {
        "job_manifest": sha256_file(job_manifest_path),
        "requests": sha256_file(requests_path),
        "response_schema": sha256_file(response_schema_path),
    }
    if input_hashes != after_hashes:
        raise CertifiedCanonicalError("model route changed a hash-bound input")
    report = {
        "schema_version": BAKEOFF_SCHEMA_VERSION,
        "protocol": BAKEOFF_PROTOCOL,
        "run_status": "model_execution_complete_responses_unvalidated",
        "route_id": route_id,
        "model_id": route["model_id"],
        "model_revision": route["revision"],
        "load_in_4bit": load_in_4bit,
        "runtime": runtime,
        "generation_contract": generation_contract,
        "response_count": len(requests),
        "input_hashes_unchanged": True,
        "training_eligible_output_count": 0,
        "next_gate": "validate_raw_responses_against_source_anchor_contract",
    }
    report_path = output_dir / "model_execution_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": BAKEOFF_SCHEMA_VERSION,
        "protocol": BAKEOFF_PROTOCOL,
        "run_status": report["run_status"],
        "inputs": {
            "job_manifest": {"path": str(job_manifest_path), "sha256": input_hashes["job_manifest"]},
            "requests": {"path": str(requests_path), "sha256": input_hashes["requests"]},
            "response_schema": {"path": str(response_schema_path), "sha256": input_hashes["response_schema"]},
        },
        "route": route,
        "load_in_4bit": load_in_4bit,
        "runtime": runtime,
        "generation_contract": generation_contract,
        "outputs": {
            name: {
                "path": str(output_dir / name),
                "sha256": sha256_file(output_dir / name),
                "bytes": (output_dir / name).stat().st_size,
            }
            for name in _RESPONSE_OUTPUT_NAMES
        },
        "training_eligible": False,
        "certification_allowed": False,
    }
    manifest_path = output_dir / "model_execution_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return ModelExecutionResult(output_dir, manifest_path, len(requests), route_id)


def _extract_json(text: object) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """Parse one response object with one auditable, non-semantic repair.

    Some otherwise complete Qwen responses append exactly one unmatched closing
    token after a valid JSON object. We never infer missing content, balance
    arbitrary JSON, strip prose, or change a field. The sole permitted repair
    drops a final ``]`` or ``\"`` *only* when the strict parser reports
    ``Extra data`` and parsing the exact prefix yields an object. The validator
    records the repair so a proposal is never silently normalized.
    """
    candidate = str(text or "").strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.IGNORECASE)
        candidate = re.sub(r"\s*```$", "", candidate).strip()
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as error:
        if error.msg == "Extra data" and candidate[-1:] in {"]", '"'}:
            try:
                repaired = json.loads(candidate[:-1])
            except json.JSONDecodeError:
                pass
            else:
                if isinstance(repaired, dict):
                    return repaired, None, "dropped_single_terminal_extra_data_character"
        return None, "response_not_a_single_json_object", None
    return (value, None, None) if isinstance(value, dict) else (None, "response_not_a_json_object", None)


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return {str(key).casefold() for key in value} | set().union(
            *(_all_keys(item) for item in value.values())
        )
    if isinstance(value, list):
        return set().union(*(_all_keys(item) for item in value)) if value else set()
    return set()


def _derive_selectable_anchor_ids(
    *, evidence_relation_ids: object, graph: Mapping[str, Any]
) -> tuple[list[str], list[str]]:
    """Bind a relation-only proposal to one deterministic selectable endpoint."""
    if not isinstance(evidence_relation_ids, list) or not evidence_relation_ids:
        return [], ["evidence_relation_ids_missing"]
    anchors = {
        str(item.get("anchor_id")): item
        for item in graph.get("anchors") or []
        if isinstance(item, Mapping) and item.get("anchor_id")
    }
    relations = {
        str(item.get("relation_id")): item
        for item in graph.get("relations") or []
        if isinstance(item, Mapping) and item.get("relation_id")
    }
    selected: list[str] = []
    errors: list[str] = []
    for raw_relation_id in evidence_relation_ids:
        relation = relations.get(str(raw_relation_id))
        if relation is None:
            errors.append("unknown_evidence_relation_id")
            continue
        source_id = str(relation.get("source_anchor_id") or "")
        target_id = str(relation.get("target_anchor_id") or "")
        # Source is the governing heading/header/row label in every current
        # graph relation. Prefer it deterministically; fall back only when the
        # source is a non-selectable structural slot.
        if bool((anchors.get(source_id) or {}).get("selectable")):
            selected.append(source_id)
        elif bool((anchors.get(target_id) or {}).get("selectable")):
            selected.append(target_id)
        else:
            errors.append("relation_has_no_selectable_endpoint")
    return list(dict.fromkeys(selected)), sorted(set(errors))


def _validate_response(
    raw: Mapping[str, Any], request: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    errors: list[str] = []
    if raw.get("request_id") != request.get("request_id"):
        errors.append("request_id_mismatch")
    if raw.get("internal_table_uid") != request.get("internal_table_uid"):
        errors.append("internal_table_uid_mismatch")
    if raw.get("model_id") != request.get("model_id") or raw.get("model_revision") != request.get("model_revision"):
        errors.append("model_identity_mismatch")
    proposal, parse_error, response_format_normalization = _extract_json(raw.get("raw_response"))
    if parse_error:
        errors.append(parse_error)
    if proposal is not None:
        forbidden = sorted(_FORBIDDEN_RESPONSE_KEYS.intersection(_all_keys(proposal)))
        if forbidden:
            errors.append("forbidden_response_key:" + ",".join(forbidden))
        expected_top_level = {"proposals", "unresolved_conditions"}
        for field in sorted(expected_top_level):
            if field not in proposal:
                errors.append(f"missing_response_field:{field}")
        unexpected_top_level = sorted(str(field) for field in set(proposal) - expected_top_level)
        if unexpected_top_level:
            errors.append("unexpected_response_top_level_key:" + ",".join(unexpected_top_level))
        if not isinstance(proposal.get("unresolved_conditions"), list):
            errors.append("unresolved_conditions_not_list")
        proposals = proposal.get("proposals")
        if not isinstance(proposals, list):
            errors.append("proposals_not_list")
        else:
            resolved_proposals: list[dict[str, Any]] = []
            for index, item in enumerate(proposals):
                if not isinstance(item, dict):
                    errors.append(f"proposal_{index}_not_object")
                    continue
                for field in (
                    "field",
                    "proposed_value",
                    "evidence_relation_ids",
                    "alternative_candidates",
                    "confidence_not_for_promotion",
                ):
                    if field not in item:
                        errors.append(f"proposal_{index}_missing:{field}")
                if "evidence_anchors" in item:
                    errors.append(f"proposal_{index}_legacy_lexical_anchor_only")
                requested_fields = (request.get("task") or {}).get("fields") or []
                if item.get("field") not in requested_fields:
                    errors.append(f"proposal_{index}_field_not_requested")
                graph = (request.get("packet") or {}).get("evidence_graph") or {}
                task = request.get("task") or {}
                field = str(item.get("field") or "")
                value_constraints = task.get("field_value_constraints") or {}
                value_constraint = value_constraints.get(field) if isinstance(value_constraints, Mapping) else None
                allowed_values = (
                    value_constraint.get("allowed_proposed_values")
                    if isinstance(value_constraint, Mapping)
                    else None
                )
                if isinstance(allowed_values, list) and item.get("proposed_value") not in allowed_values:
                    errors.append(f"proposal_{index}_proposed_value_not_allowed_for_field:{field}")
                relation_constraints = task.get("field_relation_constraints") or {}
                allowed_relation_types = (
                    relation_constraints.get(field) if isinstance(relation_constraints, Mapping) else None
                )
                if isinstance(allowed_relation_types, list):
                    relation_by_id = {
                        str(relation.get("relation_id")): relation
                        for relation in graph.get("relations") or []
                        if isinstance(relation, Mapping) and relation.get("relation_id")
                    }
                    for relation_id in item.get("evidence_relation_ids") or []:
                        relation = relation_by_id.get(str(relation_id))
                        if relation is not None and relation.get("relation_type") not in allowed_relation_types:
                            errors.append(f"proposal_{index}_relation_type_not_allowed_for_field:{field}")
                resolved_item = dict(item)
                if "evidence_anchor_ids" not in item:
                    derived_anchor_ids, derivation_errors = _derive_selectable_anchor_ids(
                        evidence_relation_ids=item.get("evidence_relation_ids"), graph=graph
                    )
                    errors.extend(f"proposal_{index}_{error}" for error in derivation_errors)
                    resolved_item["evidence_anchor_ids"] = derived_anchor_ids
                    resolved_item["evidence_anchor_ids_derivation"] = "relation_selectable_endpoint_v1"
                selection_errors = validate_proposal_selection(
                    field=item.get("field"),
                    evidence_anchor_ids=resolved_item.get("evidence_anchor_ids"),
                    evidence_relation_ids=item.get("evidence_relation_ids"),
                    graph=graph,
                )
                errors.extend(f"proposal_{index}_{error}" for error in selection_errors)
                resolved_proposals.append(resolved_item)
                if not isinstance(item.get("alternative_candidates"), list):
                    errors.append(f"proposal_{index}_alternatives_not_list")
                else:
                    for alternative_index, alternative in enumerate(item["alternative_candidates"]):
                        if not isinstance(alternative, dict):
                            errors.append(f"proposal_{index}_alternative_{alternative_index}_not_object")
                            continue
                        if str(alternative.get("status") or "").casefold() == "unresolved":
                            continue
                        alternative_errors = validate_proposal_selection(
                            field=item.get("field"),
                            evidence_anchor_ids=alternative.get("evidence_anchor_ids"),
                            evidence_relation_ids=alternative.get("evidence_relation_ids"),
                            graph=(request.get("packet") or {}).get("evidence_graph") or {},
                        )
                        errors.extend(
                            f"proposal_{index}_alternative_{alternative_index}_{error}"
                            for error in alternative_errors
                        )
    status = "VALID_PROPOSAL_ONLY" if not errors else "INVALID_UNRESOLVED"
    record = {
        "schema_version": BAKEOFF_SCHEMA_VERSION,
        "protocol": BAKEOFF_PROTOCOL,
        "request_id": request["request_id"],
        "internal_table_uid": request["internal_table_uid"],
        "route_id": request["route_id"],
        "status": status,
        "validation_errors": sorted(set(errors)),
        "evidence_selection_contract": "finite_packet_ids_only_v1",
        "training_eligible": False,
        "certification_allowed": False,
    }
    if proposal is not None and not errors:
        record["proposal_sha256"] = _sha_json(proposal)
        record["proposals"] = resolved_proposals
        record["unresolved_conditions"] = proposal["unresolved_conditions"]
        if response_format_normalization is not None:
            record["response_format_normalization"] = {
                "method": response_format_normalization,
                "raw_response_sha256": hashlib.sha256(
                    str(raw.get("raw_response") or "").encode("utf-8")
                ).hexdigest(),
            }
    return record, proposal if not errors else None


def validate_raw_responses(
    *,
    job_manifest_path: Path,
    requests_path: Path,
    raw_responses_path: Path,
    output_dir: Path,
    route_id: str | None = None,
) -> ProposalValidationResult:
    """Validate model text against the prepared request and source-anchor envelope."""
    job_manifest_path = job_manifest_path.resolve()
    requests_path = requests_path.resolve()
    raw_responses_path = raw_responses_path.resolve()
    if not raw_responses_path.is_file():
        raise FileNotFoundError(raw_responses_path)
    job, _ = _load_job(job_manifest_path, requests_path)
    _new_output(output_dir, [job_manifest_path, requests_path, raw_responses_path])
    before_hashes = {
        "job_manifest": sha256_file(job_manifest_path),
        "requests": sha256_file(requests_path),
        "raw_responses": sha256_file(raw_responses_path),
    }
    all_requests = {str(item["request_id"]): item for item in _json_lines(requests_path)}
    if route_id is not None:
        requests = {
            request_id: item
            for request_id, item in all_requests.items()
            if str(item.get("route_id")) == route_id
        }
        if not requests:
            raise CertifiedCanonicalError(f"route {route_id!r} has no prepared requests")
    else:
        requests = all_requests
    raw_responses = list(_json_lines(raw_responses_path))
    seen: set[str] = set()
    valid = 0
    invalid = 0
    results_path = output_dir / "llm_proposals_validated_v1.jsonl"
    with results_path.open("x", encoding="utf-8") as file:
        for raw in raw_responses:
            request_id = str(raw.get("request_id") or "")
            request = requests.get(request_id)
            if request is None or request_id in seen:
                record = {
                    "schema_version": BAKEOFF_SCHEMA_VERSION,
                    "protocol": BAKEOFF_PROTOCOL,
                    "request_id": request_id or None,
                    "status": "INVALID_UNRESOLVED",
                    "validation_errors": ["unknown_or_duplicate_raw_response"],
                    "training_eligible": False,
                    "certification_allowed": False,
                }
                invalid += 1
            else:
                seen.add(request_id)
                record, _ = _validate_response(raw, request)
                if record["status"] == "VALID_PROPOSAL_ONLY":
                    valid += 1
                else:
                    invalid += 1
            _write_json_line(file, record)
        for request_id, request in requests.items():
            if request_id not in seen:
                invalid += 1
                _write_json_line(
                    file,
                    {
                        "schema_version": BAKEOFF_SCHEMA_VERSION,
                        "protocol": BAKEOFF_PROTOCOL,
                        "request_id": request_id,
                        "internal_table_uid": request["internal_table_uid"],
                        "route_id": request["route_id"],
                        "status": "INVALID_UNRESOLVED",
                        "validation_errors": ["missing_raw_response"],
                        "training_eligible": False,
                        "certification_allowed": False,
                    },
                )
    after_hashes = {
        "job_manifest": sha256_file(job_manifest_path),
        "requests": sha256_file(requests_path),
        "raw_responses": sha256_file(raw_responses_path),
    }
    if before_hashes != after_hashes:
        raise CertifiedCanonicalError("response validation changed a hash-bound input")
    report = {
        "schema_version": BAKEOFF_SCHEMA_VERSION,
        "protocol": BAKEOFF_PROTOCOL,
        "run_status": "proposal_validation_complete_not_certified",
        "expected_response_count": len(requests),
        "route_id": route_id,
        "observed_raw_response_count": len(raw_responses),
        "valid_proposal_only_count": valid,
        "invalid_or_missing_unresolved_count": invalid,
        "input_hashes_unchanged": True,
        "training_eligible_output_count": 0,
        "next_gate": "deterministic_semantic_verifier_phase_4_5",
    }
    report_path = output_dir / "llm_response_validation_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": BAKEOFF_SCHEMA_VERSION,
        "protocol": BAKEOFF_PROTOCOL,
        "run_status": report["run_status"],
        "inputs": {
            "job_manifest": {"path": str(job_manifest_path), "sha256": before_hashes["job_manifest"]},
            "requests": {"path": str(requests_path), "sha256": before_hashes["requests"]},
            "raw_responses": {"path": str(raw_responses_path), "sha256": before_hashes["raw_responses"]},
        },
        "outputs": {
            name: {
                "path": str(output_dir / name),
                "sha256": sha256_file(output_dir / name),
                "bytes": (output_dir / name).stat().st_size,
            }
            for name in _VALIDATION_OUTPUT_NAMES
        },
        "training_eligible": False,
        "certification_allowed": False,
        "source_job_manifest_sha256": sha256_file(job_manifest_path),
        "source_job_execution_recorded": job.get("model_execution_recorded"),
        "route_id": route_id,
    }
    manifest_path = output_dir / "proposal_validation_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return ProposalValidationResult(output_dir, manifest_path, valid, invalid)
