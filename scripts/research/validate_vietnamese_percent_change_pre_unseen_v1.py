#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
REPO_ROOT=Path(__file__).resolve().parents[2]
if str(REPO_ROOT/"src") not in sys.path:sys.path.insert(0,str(REPO_ROOT/"src"))
from finance_query.research.vietnamese_percent_change_pre_unseen import validate_pre_unseen
def main()->None:
    p=argparse.ArgumentParser();p.add_argument("receipt",type=Path);a=p.parse_args();result=validate_pre_unseen(a.receipt,REPO_ROOT);print(json.dumps(result,indent=2,sort_keys=True));raise SystemExit(0 if result["status"]=="PASS" else 1)
if __name__=="__main__":main()
