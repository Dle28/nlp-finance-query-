#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
if str(ROOT/"src") not in sys.path:sys.path.insert(0,str(ROOT/"src"))
from finance_query.research.advisory_intent_abstention import freeze_split
def main()->None:
 p=argparse.ArgumentParser();p.add_argument("--hypothesis",type=Path,required=True);p.add_argument("--dataset",type=Path,required=True);p.add_argument("--output-dir",type=Path,required=True);a=p.parse_args();print(json.dumps(freeze_split(hypothesis_path=a.hypothesis,dataset_path=a.dataset,output_dir=a.output_dir),indent=2,sort_keys=True))
if __name__=="__main__":main()
