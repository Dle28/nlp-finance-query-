#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
if str(ROOT/"src") not in sys.path:sys.path.insert(0,str(ROOT/"src"))
from finance_query.research.advisory_intent_pre_unseen import validate_pre_unseen
def main()->None:
 p=argparse.ArgumentParser();p.add_argument("receipt",type=Path);a=p.parse_args();r=validate_pre_unseen(a.receipt,ROOT);print(json.dumps(r,indent=2,sort_keys=True));raise SystemExit(0 if r["status"]=="PASS" else 1)
if __name__=="__main__":main()
