#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path
from finance_query.grounding_repairs_v2 import materialize
def main():
 p=argparse.ArgumentParser();p.add_argument("--metadata",type=Path,required=True);p.add_argument("--routing",type=Path,required=True);p.add_argument("--period",type=Path,required=True);p.add_argument("--output-dir",type=Path,required=True);a=p.parse_args();print(materialize([a.metadata,a.routing,a.period],a.output_dir))
if __name__=="__main__":main()
