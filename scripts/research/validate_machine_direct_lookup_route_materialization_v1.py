#!/usr/bin/env python3
"""Validate candidate-only direct-lookup route revisions."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
REPO_ROOT=Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:sys.path.insert(0,str(REPO_ROOT / "src"))
from finance_query.research.machine_direct_lookup_route_materialization import validate_machine_direct_lookup_route_materialization
def main()->None:
 parser=argparse.ArgumentParser();parser.add_argument("--artifact-dir",type=Path,required=True);parser.add_argument("--expected-question-count",type=int,default=1012);a=parser.parse_args()
 print(json.dumps(validate_machine_direct_lookup_route_materialization(a.artifact_dir,expected_question_count=a.expected_question_count),ensure_ascii=False,indent=2,sort_keys=True))
if __name__=="__main__":main()
