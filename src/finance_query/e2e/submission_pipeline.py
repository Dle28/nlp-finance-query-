"""Public adapter for the primary best-effort submission builder.

The implementation remains in ``scripts/e2e`` because the same file is copied
into the Kaggle runtime bundle.  This adapter gives local operators one stable
CLI command without creating a second submission implementation.
"""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sysconfig
from types import ModuleType
from typing import Any


_BUILDER_RELATIVE_PATH = Path("share/finance-query/scripts/e2e/build_competition_submission_v1.py")


def _builder_path() -> Path:
    """Locate the one builder in source checkouts and installed wheels."""

    source_path = Path(__file__).resolve().parents[3] / "scripts/e2e/build_competition_submission_v1.py"
    installed_target_root = Path(__file__).resolve().parents[2]
    candidates = [
        source_path,
        installed_target_root / _BUILDER_RELATIVE_PATH,
    ]
    for base in (
        Path(sysconfig.get_path("data")),
        Path(sysconfig.get_path("purelib")).parent,
    ):
        candidates.append(base / _BUILDER_RELATIVE_PATH)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    # Preserve a deterministic error path for the adapter's loader while
    # retaining the source-checkout location as the most useful diagnostic.
    return source_path


def _load_builder() -> ModuleType:
    path = _builder_path()
    spec = spec_from_file_location("finance_query_primary_submission_builder", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load primary submission builder: {path}")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def configure_parser(parser: Any) -> Any:
    """Add the primary builder's arguments to an existing argparse parser."""
    return _load_builder().configure_parser(parser)


def build_primary_submission(args: Any) -> None:
    """Run the one primary submission implementation."""
    _load_builder().build(args)
