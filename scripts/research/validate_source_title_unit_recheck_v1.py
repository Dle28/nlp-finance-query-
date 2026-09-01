#!/usr/bin/env python3
"""Validate a source-title unit recheck artifact."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
REPO_ROOT=Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:sys.path.insert(0,str(REPO_ROOT / "src"))
from finance_query.research.source_title_unit_recheck import validate_source_title_unit_recheck
def main()->None:
 parser=argparse.ArgumentParser();parser.add_argument("--artifact-dir",type=Path,required=True);args=parser.parse_args()
 print(json.dumps(validate_source_title_unit_recheck(args.artifact_dir),ensure_ascii=False,indent=2,sort_keys=True))
if __name__=="__main__":main()
