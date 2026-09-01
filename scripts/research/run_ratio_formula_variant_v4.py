#!/usr/bin/env python3
"""Run the qualifier-aware strict exact-ratio treatment as protocol v4."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


MODULE_NAME = "_vifinqa_ratio_formula_variant_v2_for_v4"
VARIANT_PROTOCOL = "vifinqa_exact_ratio_formula_v4"
RATIO_TIER = "program_exact_ratio_formula_v4"


def _load_v2() -> Any:
    runner_path = Path(__file__).resolve().with_name("run_ratio_formula_variant_v2.py")
    spec = importlib.util.spec_from_file_location(MODULE_NAME, runner_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load v2 ratio runner: {runner_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V2 = _load_v2()
V2.VARIANT_PROTOCOL = VARIANT_PROTOCOL
V2.RATIO_TIER = RATIO_TIER
V2.V1.VARIANT_PROTOCOL = VARIANT_PROTOCOL
V2.V1.RATIO_TIER = RATIO_TIER


if __name__ == "__main__":
    V2.V1.main()
