#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
REPO_ROOT=Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path: sys.path.insert(0,str(REPO_ROOT / "src"))
from finance_query.research.compiler_program_plan_reclassification import build_compiler_program_plan_reclassification
def main() -> None:
 p=argparse.ArgumentParser()
 for name in ("base-plans","base-plans-manifest","review-items","entity-aliases","output-dir"):p.add_argument(f"--{name}",type=Path,required=True)
 p.add_argument("--expected-question-count",type=int,default=1012);a=p.parse_args()
 print(json.dumps(build_compiler_program_plan_reclassification(base_plans_path=a.base_plans,base_plans_manifest_path=a.base_plans_manifest,review_items_path=a.review_items,entity_aliases_path=a.entity_aliases,output_dir=a.output_dir,expected_question_count=a.expected_question_count),ensure_ascii=False,indent=2,sort_keys=True))
if __name__=="__main__":main()
