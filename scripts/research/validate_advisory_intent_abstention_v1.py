#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
if str(ROOT/"src") not in sys.path:sys.path.insert(0,str(ROOT/"src"))
from finance_query.research.advisory_intent_abstention import validate_split
def main()->None:
 p=argparse.ArgumentParser();p.add_argument("--artifact-dir",type=Path,required=True);a=p.parse_args();print(json.dumps(validate_split(a.artifact_dir),indent=2,sort_keys=True))
if __name__=="__main__":main()
