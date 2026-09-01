#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
REPO_ROOT=Path(__file__).resolve().parents[2]
if str(REPO_ROOT/"src") not in sys.path:sys.path.insert(0,str(REPO_ROOT/"src"))
from finance_query.research.unique_provenance_pre_unseen import validate_unique_provenance_pre_unseen
def main()->None:
    p=argparse.ArgumentParser();p.add_argument("receipt",type=Path);a=p.parse_args();r=validate_unique_provenance_pre_unseen(a.receipt,REPO_ROOT);print(json.dumps(r,indent=2,sort_keys=True));raise SystemExit(0 if r["status"]=="PASS" else 1)
if __name__=="__main__":main()
