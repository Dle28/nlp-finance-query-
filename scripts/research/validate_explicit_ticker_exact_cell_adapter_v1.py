#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path: sys.path.insert(0,str(ROOT / "src"))
from finance_query.research.explicit_ticker_exact_cell_adapter import validate_explicit_ticker_exact_cell_adapter
def main()->None:
    parser=argparse.ArgumentParser(); parser.add_argument("--artifact-dir",type=Path,required=True); parser.add_argument("--expected-question-count",type=int,default=1012); a=parser.parse_args()
    print(json.dumps(validate_explicit_ticker_exact_cell_adapter(a.artifact_dir,expected_question_count=a.expected_question_count),ensure_ascii=False,indent=2,sort_keys=True))
if __name__=="__main__": main()
