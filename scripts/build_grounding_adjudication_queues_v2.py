#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path
from finance_query.grounding_adjudication_v2 import build
def main():
 p=argparse.ArgumentParser();p.add_argument("--audit",type=Path,required=True);p.add_argument("--period-manifest",type=Path,required=True);p.add_argument("--document-metadata",type=Path,required=True);p.add_argument("--taxonomy-candidates",type=Path,required=True);p.add_argument("--output",type=Path,required=True);a=p.parse_args();print(build(audit_path=a.audit,period_manifest=a.period_manifest,document_metadata=a.document_metadata,taxonomy_candidates=a.taxonomy_candidates,output=a.output)["manifest_path"])
if __name__=="__main__":main()
