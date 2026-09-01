#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
REPO_ROOT=Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path: sys.path.insert(0,str(REPO_ROOT / "src"))
from finance_query.research.argmax_period_route_materialization import build_argmax_period_route_materialization
def main() -> None:
 p=argparse.ArgumentParser()
 for name in ("plans","source-audit-dir","base-period-packets","base-period-manifest","base-route-overlay","base-route-overlay-manifest","full-corpus-artifact-dir","row-review-queue","structured-tables","evidence-context","evidence-context-manifest","output-dir"): p.add_argument(f"--{name}",type=Path,required=True)
 p.add_argument("--expected-question-count",type=int,default=1012);p.add_argument("--minimum-row-jaccard",type=float,default=0.9);p.add_argument("--minimum-row-margin",type=float,default=0.2);a=p.parse_args()
 print(json.dumps(build_argmax_period_route_materialization(plans_path=a.plans,source_audit_dir=a.source_audit_dir,base_period_packets_path=a.base_period_packets,base_period_manifest_path=a.base_period_manifest,base_route_overlay_path=a.base_route_overlay,base_route_overlay_manifest_path=a.base_route_overlay_manifest,full_corpus_artifact_dir=a.full_corpus_artifact_dir,row_review_queue_path=a.row_review_queue,structured_tables_path=a.structured_tables,evidence_context_path=a.evidence_context,evidence_context_manifest_path=a.evidence_context_manifest,output_dir=a.output_dir,expected_question_count=a.expected_question_count,minimum_row_jaccard=a.minimum_row_jaccard,minimum_row_margin=a.minimum_row_margin),ensure_ascii=False,indent=2,sort_keys=True))
if __name__=="__main__": main()
