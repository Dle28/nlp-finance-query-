#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
REPO_ROOT=Path(__file__).resolve().parents[2]
if str(REPO_ROOT/"src") not in sys.path:sys.path.insert(0,str(REPO_ROOT/"src"))
from finance_query.research.vietnamese_percent_change_semantics import freeze_split
def main()->None:
    p=argparse.ArgumentParser();p.add_argument("--hypothesis",type=Path,required=True);p.add_argument("--train",type=Path,required=True);p.add_argument("--validation",type=Path,required=True);p.add_argument("--test",type=Path,required=True);p.add_argument("--finqa-root",type=Path,required=True);p.add_argument("--output-dir",type=Path,required=True);a=p.parse_args();print(json.dumps(freeze_split(hypothesis_path=a.hypothesis,train_path=a.train,validation_path=a.validation,test_path=a.test,finqa_root=a.finqa_root,output_dir=a.output_dir),ensure_ascii=False,indent=2,sort_keys=True))
if __name__=="__main__":main()
