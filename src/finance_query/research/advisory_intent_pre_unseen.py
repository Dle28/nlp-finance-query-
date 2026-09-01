"""Fail-closed validation for advisory-intent pre-unseen receipts."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
from typing import Any,Mapping
PROTOCOL="explicit_advisory_intent_abstention_pre_unseen_freeze_v1"
def _sha(path:Path)->str:return hashlib.sha256(path.read_bytes()).hexdigest()
def validate_pre_unseen(receipt_path:Path,repo_root:Path)->dict[str,Any]:
 receipt=json.loads(receipt_path.read_text());errors=[];loaded={};inputs=receipt.get("inputs") or {}
 if receipt.get("protocol")!=PROTOCOL or receipt.get("schema_version")!=1:errors.append("protocol mismatch")
 if receipt.get("experiment_id")!="explicit_advisory_intent_abstention_v1":errors.append("experiment mismatch")
 if not isinstance(inputs,Mapping) or not inputs:errors.append("inputs missing");inputs={}
 for name,descriptor in inputs.items():
  path=Path(str((descriptor or {}).get("path") or ""));path=path if path.is_absolute() else repo_root/path
  if not path.is_file() or _sha(path)!=(descriptor or {}).get("sha256"):errors.append(f"input hash mismatch: {name}");continue
  if path.suffix==".json":loaded[name]=json.loads(path.read_text())
 h=loaded.get("hypothesis_freeze") or {};d=loaded.get("development_report") or {};t=h.get("decision_thresholds") or {};c=(d.get("arms") or {}).get("B_A_plus_explicit_advisory_abstention") or {};o=d.get("candidate_outcome_counts") or {};observed={"newly_correct":int(o.get("IMPROVED",0)),"precision_on_predictions":c.get("precision_on_predictions"),"false_confident_candidate":int(c.get("false_confident_count",0)),"regressed_baseline_predictions":int(o.get("REGRESSED",0))};gate=receipt.get("development_gate") or {}
 if gate.get("status")!="PASS" or gate.get("observed")!=observed:errors.append("development gate mismatch")
 if not(observed["newly_correct"]>=t.get("minimum_newly_correct_development",0) and observed["precision_on_predictions"]==t.get("minimum_precision_on_predictions") and observed["false_confident_candidate"]<=t.get("maximum_false_confident_candidate",0) and observed["regressed_baseline_predictions"]<=t.get("maximum_regressed_baseline_predictions",0)):errors.append("thresholds failed")
 required={"minimum_newly_correct_untouched","minimum_precision_on_predictions","maximum_false_confident_candidate","maximum_regressed_baseline_predictions","metric_holdout_prediction","composition_holdout_prediction"};pred=receipt.get("frozen_untouched_predictions") or {}
 if not isinstance(pred,Mapping) or not required.issubset(pred):errors.append("predictions incomplete")
 if receipt.get("untouched_evaluation_allowed") is not True:errors.append("untouched locked")
 if receipt.get("full_vifinqa_dataset_allowed") is not False:errors.append("full ViFinQA unlocked")
 contract=receipt.get("source_contract") or {}
 if not(contract.get("research_only") is True and contract.get("submission_eligible") is False and contract.get("vifinqa_answer_authority") is False):errors.append("unsafe contract")
 return {"protocol":"explicit_advisory_intent_abstention_pre_unseen_validation_v1","status":"PASS" if not errors else "FAIL","error_count":len(errors),"errors":errors,"receipt_sha256":_sha(receipt_path),"untouched_evaluation_allowed":not errors,"full_vifinqa_dataset_allowed":False}
