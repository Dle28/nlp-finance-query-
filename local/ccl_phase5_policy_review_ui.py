#!/usr/bin/env python3
"""Serve a local-only human policy decision form for the Phase 5 smoke gate."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from errno import EADDRINUSE
import hashlib
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
REVIEW_PROTOCOL = "vifinqa_ccl_phase5_component_selection_policy_review_v1"
RESPONSE_PROTOCOL = "vifinqa_ccl_phase5_component_selection_policy_response_v1"
REVIEW_NAME = "component_selection_policy_review_v1.json"
TEMPLATE_NAME = "component_selection_policy_response_template_v1.json"
MANIFEST_NAME = "component_selection_policy_review_manifest.json"
KEEP_BLOCKED = "KEEP_DISPATCH_BLOCKED_AND_EXPAND_CALIBRATION"
AUTHORIZE_SMOKE = "AUTHORIZE_BOUNDED_SMOKE_ONLY"
RESPONSE_KEYS = {
    "schema_version",
    "protocol",
    "immutable_policy_review_sha256",
    "reviewer_id",
    "reviewed_at_utc",
    "policy_decision",
    "acknowledge_calibration_evidence",
    "acknowledge_candidate_lineage",
    "approved_candidate_job_manifest_sha256",
    "rationale",
    "training_eligible",
    "certification_allowed",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def load_policy_package(review_path: Path, template_path: Path, manifest_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Verify the pending policy package before exposing a human decision form."""
    for path in (review_path, template_path, manifest_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    review = _json(review_path)
    template = _json(template_path)
    manifest = _json(manifest_path)
    if (
        review.get("protocol") != REVIEW_PROTOCOL
        or review.get("run_status") != "policy_decision_pending"
        or review.get("kaggle_dataset_packaging_allowed") is not False
        or review.get("model_execution_allowed") is not False
        or review.get("training_eligible") is not False
        or review.get("certification_allowed") is not False
    ):
        raise ValueError("Policy review is not a pending non-promotable dispatch gate")
    if (
        manifest.get("protocol") != REVIEW_PROTOCOL
        or manifest.get("run_status") != "policy_decision_pending"
        or manifest.get("training_eligible") is not False
        or manifest.get("certification_allowed") is not False
    ):
        raise ValueError("Policy-review manifest is unsupported")
    outputs = manifest.get("outputs") or {}
    if (
        (outputs.get(REVIEW_NAME) or {}).get("sha256") != sha256_file(review_path)
        or (outputs.get(TEMPLATE_NAME) or {}).get("sha256") != sha256_file(template_path)
    ):
        raise ValueError("Policy package SHA-256 mismatch")
    if (
        set(template) != RESPONSE_KEYS
        or template.get("protocol") != RESPONSE_PROTOCOL
        or template.get("immutable_policy_review_sha256") != canonical_sha(review)
        or any(
            template.get(key) is not None
            for key in RESPONSE_KEYS - {"schema_version", "protocol", "immutable_policy_review_sha256", "training_eligible", "certification_allowed"}
        )
        or template.get("training_eligible") is not False
        or template.get("certification_allowed") is not False
    ):
        raise ValueError("Policy response template is not blank or correctly bound")
    return review, template


def build_response(review: Mapping[str, Any], template: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    """Convert browser input to the resolver's exact non-promotable response schema."""
    reviewer_id = payload.get("reviewer_id")
    rationale = payload.get("rationale")
    decision = payload.get("policy_decision")
    if not isinstance(reviewer_id, str) or not reviewer_id.strip():
        raise ValueError("Reviewer ID is required")
    if not isinstance(rationale, str) or len(rationale.strip()) < 20:
        raise ValueError("Please enter a rationale of at least 20 characters")
    if decision not in {KEEP_BLOCKED, AUTHORIZE_SMOKE}:
        raise ValueError("Choose one policy decision")
    if payload.get("acknowledge_calibration_evidence") is not True or payload.get("acknowledge_candidate_lineage") is not True:
        raise ValueError("Confirm both the calibration evidence and candidate lineage")
    candidate = review.get("candidate_job") or {}
    candidate_sha = candidate.get("job_manifest_sha256")
    if not isinstance(candidate_sha, str) or not candidate_sha:
        raise ValueError("Policy review has no candidate job identity")
    approved_sha = candidate_sha if decision == AUTHORIZE_SMOKE else None
    return {
        **template,
        "reviewer_id": reviewer_id.strip(),
        "reviewed_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "policy_decision": decision,
        "acknowledge_calibration_evidence": True,
        "acknowledge_candidate_lineage": True,
        "approved_candidate_job_manifest_sha256": approved_sha,
        "rationale": rationale.strip(),
        "training_eligible": False,
        "certification_allowed": False,
    }


def _render_page(review: Mapping[str, Any]) -> str:
    metrics = review.get("calibration_score") or {}
    global_metrics = metrics.get("global_metrics") or {}
    candidate = review.get("candidate_job") or {}
    recommendation = review.get("recommendation") or {}
    reasons = recommendation.get("reason_codes") or []
    reason_html = "".join(f"<li>{escape(str(reason))}</li>" for reason in reasons)
    reviewed = escape(str(global_metrics.get("reviewed_item_count", "?")))
    abstentions = escape(str(global_metrics.get("human_abstention_count", "?")))
    requests = escape(str(candidate.get("request_count", "?")))
    candidate_sha = escape(str(candidate.get("job_manifest_sha256", "")))
    return f"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Phase 5 - Policy gate</title><style>
:root {{ --ink:#18231c; --moss:#326548; --cream:#f7f2e7; --paper:#fffdf7; --line:#d7d0c1; --warn:#8a4d12; }}
* {{ box-sizing:border-box; }} body {{ margin:0; color:var(--ink); background:radial-gradient(circle at 12% 10%,#dbe8d5 0,transparent 28rem),var(--cream); font:17px/1.55 Georgia,serif; }}
main {{ max-width:880px; margin:42px auto; padding:0 20px 48px; }} h1 {{ margin:0; font-size:clamp(2rem,5vw,3.8rem); line-height:1; }} .kicker {{ color:var(--moss); font:bold 12px/1.2 monospace; letter-spacing:.14em; text-transform:uppercase; }}
.card {{ margin-top:22px; padding:24px; background:var(--paper); border:1px solid var(--line); border-radius:17px; box-shadow:8px 9px 0 #d9dfce; }} .intro {{ border-left:8px solid var(--moss); }} .stats {{ display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }} .stat {{ padding:16px; background:#eef4e9; border-radius:10px; }} .stat b {{ display:block; font-size:2rem; line-height:1; }} .choice {{ padding:14px 16px; margin-top:12px; border:1px solid var(--line); border-radius:10px; }} .choice.recommended {{ background:#eef4e9; border-color:#9cb99d; }} .choice h3 {{ margin:0; font-size:1.08rem; }} .choice p {{ margin:5px 0 0; }}
code {{ font:12px/1.35 ui-monospace,monospace; overflow-wrap:anywhere; }} .warning {{ color:var(--warn); font-weight:bold; }} label {{ display:block; margin-top:16px; font-weight:bold; }} input,textarea,select {{ width:100%; margin-top:5px; padding:10px; border:1px solid var(--line); border-radius:7px; background:#fff; font:inherit; }} textarea {{ min-height:100px; }} .check {{ font-weight:normal; }} .check input {{ width:auto; margin-right:8px; }} button {{ margin-top:20px; padding:12px 18px; border:0; border-radius:7px; color:#fff; background:var(--moss); font:700 16px Georgia,serif; cursor:pointer; }} #result {{ margin-top:14px; font-weight:bold; }} @media(max-width:620px) {{ .stats {{ grid-template-columns:1fr; }} main {{ margin-top:24px; }} }}
</style></head><body><main><p class="kicker">Human decision required / no model execution</p><h1>Phase 5 policy gate</h1>
<section class="card intro"><h2>Ban dang review gi?</h2><p><b>Day la quyet dinh co cho phep chay thu mo hinh hay khong. Day khong phai man hinh review bang du lieu, cot, hay dap an tai chinh.</b></p><p>Cau hoi duy nhat la: voi ket qua review truoc do, co nen cho he thong chay dung {requests} request thu nghiem hay tiep tuc chan va thu thap them bang chung?</p><div class="choice recommended"><h3>Lua chon 1 - Giu chan va mo rong calibration (khuyen nghi)</h3><p>Khong chay Kaggle hay GPU. Buoc tiep theo la review them mau de biet mo hinh co dang tin hay khong.</p></div><div class="choice"><h3>Lua chon 2 - Chi cho phep smoke {requests} request nay</h3><p>Chi chay thu de kiem tra quy trinh. Khong tao label, khong train, khong chung nhan, va khong co nghia mo hinh da dung.</p></div></section>
<section class="card"><p class="warning">Tai sao khuyen nghi giu chan: bang chung hien co con qua it.</p><div class="stats"><div class="stat"><b>{reviewed}</b>mau da review</div><div class="stat"><b>{abstentions}</b>mau chua the ket luan</div><div class="stat"><b>{requests}</b>request neu cho phep smoke</div></div><h2>Ly do ky thuat</h2><ul>{reason_html}</ul><p>Smoke job duoc dinh danh bang SHA-256: <code>{candidate_sha}</code></p></section>
<form class="card" id="form"><h2>Ghi quyet dinh cua ban</h2><p>Neu ban chua chac chan, chon <b>Giu chan va mo rong calibration</b>. Lua chon nay an toan va khong lam mat du lieu nao.</p><label>Reviewer ID<input name="reviewer_id" required autocomplete="name"></label><label>Quyet dinh<select name="policy_decision"><option value="{KEEP_BLOCKED}">Giu chan va mo rong calibration (khuyen nghi)</option><option value="{AUTHORIZE_SMOKE}">Chi cho phep smoke 5 request nay</option></select></label><label>Ly do (toi thieu 20 ky tu)<textarea name="rationale" required></textarea></label><label class="check"><input type="checkbox" name="calibration">Toi hieu day la quyet dinh chay thu, khong phai ket qua danh gia dung/sai cua mo hinh.</label><label class="check"><input type="checkbox" name="lineage">Toi dong y quyet dinh nay chi ap dung cho dung smoke job 5 request dang hien thi.</label><button type="submit">Luu policy response</button><div id="result"></div></form>
<script>document.getElementById('form').addEventListener('submit',async(e)=>{{e.preventDefault();const f=new FormData(e.target);const p={{reviewer_id:f.get('reviewer_id'),policy_decision:f.get('policy_decision'),rationale:f.get('rationale'),acknowledge_calibration_evidence:f.get('calibration')==='on',acknowledge_candidate_lineage:f.get('lineage')==='on'}};const r=await fetch('/submit',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(p)}});document.getElementById('result').textContent=(await r.json()).message;}});</script>
</main></body></html>"""


def _handler(review: Mapping[str, Any], template: Mapping[str, Any], response_path: Path) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: HTTPStatus, payload: Mapping[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path != "/":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            body = _render_page(review).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/submit":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16_384:
                    raise ValueError("invalid request size")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("request must be a JSON object")
                response = build_response(review, template, payload)
                with response_path.open("x", encoding="utf-8") as file:
                    file.write(json.dumps(response, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
            except FileExistsError:
                self._send(HTTPStatus.CONFLICT, {"message": f"Refusing to overwrite {response_path}"})
            except (ValueError, json.JSONDecodeError) as error:
                self._send(HTTPStatus.BAD_REQUEST, {"message": str(error)})
            else:
                self._send(HTTPStatus.CREATED, {"message": f"Saved response to {response_path}; run the resolver next."})

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return Handler


def main() -> None:
    default_dir = ROOT / "artifacts/research/ccl_phase5_component_selection_policy_review_v1_001"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-review", type=Path, default=default_dir / REVIEW_NAME)
    parser.add_argument("--policy-template", type=Path, default=default_dir / TEMPLATE_NAME)
    parser.add_argument("--policy-manifest", type=Path, default=default_dir / MANIFEST_NAME)
    parser.add_argument("--response", type=Path, default=ROOT / "artifacts/research/ccl_phase5_component_selection_policy_response_v1_001.json")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    review, template = load_policy_package(args.policy_review, args.policy_template, args.policy_manifest)
    if args.response.exists():
        raise FileExistsError(f"Refusing to overwrite existing response: {args.response}")
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), _handler(review, template, args.response))
    except OSError as error:
        if error.errno == EADDRINUSE:
            parser.error(f"Port {args.port} is already in use; choose another with --port.")
        raise
    print(f"Policy UI: http://127.0.0.1:{args.port}")
    print(f"Response destination: {args.response}")
    print("The UI cannot dispatch a model or package a Kaggle dataset. Stop with Ctrl-C after saving.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
