#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
REPO_ROOT=Path(__file__).resolve().parents[2]
if str(REPO_ROOT/"src") not in sys.path:sys.path.insert(0,str(REPO_ROOT/"src"))
from finance_query.research.vietnamese_percent_change_semantics import validate_split
def main()->None:
    p=argparse.ArgumentParser();p.add_argument("--artifact-dir",type=Path,required=True);a=p.parse_args();print(json.dumps(validate_split(a.artifact_dir),indent=2,sort_keys=True))
if __name__=="__main__":main()
