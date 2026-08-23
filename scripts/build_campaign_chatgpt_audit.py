#!/usr/bin/env python3
"""Build a numeric-literal-free, hash-bound ChatGPT campaign audit."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.campaign_chatgpt_audits import build_campaign_chatgpt_audit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff-manifest", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = build_campaign_chatgpt_audit(
        handoff_manifest=args.handoff_manifest.resolve(),
        config_path=args.config.resolve(),
        output_dir=args.output_dir.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
