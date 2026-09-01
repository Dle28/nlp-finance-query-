#!/usr/bin/env python3
"""Build candidate-only direct-lookup E2E route revisions."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
REPO_ROOT=Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:sys.path.insert(0,str(REPO_ROOT / "src"))
from finance_query.research.machine_direct_lookup_route_materialization import build_machine_direct_lookup_route_materialization
def main()->None:
 parser=argparse.ArgumentParser()
 for name in ("triage","plans","base-period-packets","base-period-manifest","base-route-overlay","base-route-overlay-manifest","table-diagnostics","row-candidates","structured-tables","evidence-context","evidence-context-manifest","output-dir"):parser.add_argument(f"--{name}",type=Path,required=True)
 parser.add_argument("--expected-question-count",type=int,default=1012);parser.add_argument("--minimum-row-jaccard",type=float,default=.8);parser.add_argument("--minimum-row-margin",type=float,default=.2);parser.add_argument("--allow-v3-header-recovery",action="store_true")
 a=parser.parse_args();r=build_machine_direct_lookup_route_materialization(triage_path=a.triage,plans_path=a.plans,base_period_packets_path=a.base_period_packets,base_period_manifest_path=a.base_period_manifest,base_route_overlay_path=a.base_route_overlay,base_route_overlay_manifest_path=a.base_route_overlay_manifest,table_diagnostics_path=a.table_diagnostics,row_candidates_path=a.row_candidates,structured_tables_path=a.structured_tables,evidence_context_path=a.evidence_context,evidence_context_manifest_path=a.evidence_context_manifest,output_dir=a.output_dir,expected_question_count=a.expected_question_count,minimum_row_jaccard=a.minimum_row_jaccard,minimum_row_margin=a.minimum_row_margin,allow_v3_header_recovery=a.allow_v3_header_recovery)
 print(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True))
if __name__=="__main__":main()
