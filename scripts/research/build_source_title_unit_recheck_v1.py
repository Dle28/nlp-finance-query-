#!/usr/bin/env python3
"""Build one candidate-only source-title unit recheck revision."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
REPO_ROOT=Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:sys.path.insert(0,str(REPO_ROOT / "src"))
from finance_query.research.source_title_unit_recheck import build_source_title_unit_recheck
def main()->None:
 parser=argparse.ArgumentParser()
 for name in ("base-period-packets","base-period-manifest","bindings","bindings-manifest","structured-tables","evidence-context","evidence-context-manifest","output-dir"):parser.add_argument(f"--{name}",type=Path,required=True)
 args=parser.parse_args(); result=build_source_title_unit_recheck(base_period_packets=args.base_period_packets,base_period_manifest=args.base_period_manifest,bindings=args.bindings,bindings_manifest=args.bindings_manifest,structured_tables=args.structured_tables,evidence_context=args.evidence_context,evidence_context_manifest=args.evidence_context_manifest,output_dir=args.output_dir)
 print(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True))
if __name__=="__main__":main()
