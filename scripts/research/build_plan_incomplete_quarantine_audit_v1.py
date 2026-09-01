#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path: sys.path.insert(0, str(REPO_ROOT / "src"))
from finance_query.research.plan_incomplete_quarantine_audit import build_plan_incomplete_quarantine_audit
def main() -> None:
    p=argparse.ArgumentParser()
    for name in ("triage", "plans", "temporal-probe-dir", "output-dir"): p.add_argument(f"--{name}",type=Path,required=True)
    p.add_argument("--expected-question-count",type=int,default=1012);a=p.parse_args()
    print(json.dumps(build_plan_incomplete_quarantine_audit(triage_path=a.triage,plans_path=a.plans,temporal_probe_dir=a.temporal_probe_dir,output_dir=a.output_dir,expected_question_count=a.expected_question_count),ensure_ascii=False,indent=2,sort_keys=True))
if __name__ == "__main__": main()
