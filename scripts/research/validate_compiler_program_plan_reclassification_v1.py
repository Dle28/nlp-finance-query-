#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
REPO_ROOT=Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:sys.path.insert(0,str(REPO_ROOT / "src"))
from finance_query.research.compiler_program_plan_reclassification import validate_compiler_program_plan_reclassification
def main() -> None:
 p=argparse.ArgumentParser();p.add_argument("--artifact-dir",type=Path,required=True);p.add_argument("--expected-question-count",type=int,default=1012);a=p.parse_args()
 print(json.dumps(validate_compiler_program_plan_reclassification(a.artifact_dir,expected_question_count=a.expected_question_count),ensure_ascii=False,indent=2,sort_keys=True))
if __name__=="__main__":main()
