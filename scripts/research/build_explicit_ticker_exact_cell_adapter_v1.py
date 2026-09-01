#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path: sys.path.insert(0,str(ROOT / "src"))
from finance_query.research.explicit_ticker_exact_cell_adapter import build_explicit_ticker_exact_cell_adapter
def main()->None:
    parser=argparse.ArgumentParser()
    for name in ("targets","semantic-config","plans","candidate-artifact-dir","full-assets","full-assets-manifest","structured-tables","evidence-context","evidence-context-manifest","output-dir"): parser.add_argument(f"--{name}",type=Path,required=True)
    parser.add_argument("--expected-question-count",type=int,default=1012); parser.add_argument("--minimum-row-jaccard",type=float,default=.8); parser.add_argument("--minimum-row-margin",type=float,default=.1)
    a=parser.parse_args(); print(json.dumps(build_explicit_ticker_exact_cell_adapter(targets_path=a.targets,semantic_config_path=a.semantic_config,plans_path=a.plans,candidate_artifact_dir=a.candidate_artifact_dir,full_assets_path=a.full_assets,full_assets_manifest_path=a.full_assets_manifest,structured_tables_path=a.structured_tables,evidence_context_path=a.evidence_context,evidence_context_manifest_path=a.evidence_context_manifest,output_dir=a.output_dir,expected_question_count=a.expected_question_count,minimum_row_jaccard=a.minimum_row_jaccard,minimum_row_margin=a.minimum_row_margin),ensure_ascii=False,indent=2,sort_keys=True))
if __name__=="__main__": main()
