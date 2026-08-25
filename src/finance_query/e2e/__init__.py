"""Giao diện công khai cho luồng kiểm chứng đầu-cuối xác định.

Các thành phần toán học trong package này cũng được code lõi sử dụng. Vì vậy
facade replay được nạp lười để tránh vòng lặp import giữa orchestration và
`decimal_executor` / `decimal_sandbox`.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


_PUBLIC_REPLAY_NAMES = frozenset(
    {
        "DETERMINISTIC_E2E_PROTOCOL",
        "DeterministicReplayError",
        "DeterministicReplayInputs",
        "load_deterministic_replay_inputs",
        "run_deterministic_replay",
        "summarize_deterministic_replay",
    }
)


def __getattr__(name: str) -> Any:
    """Load the public replay facade only when a caller requests it."""

    if name not in _PUBLIC_REPLAY_NAMES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    replay = import_module(".deterministic_replay", __name__)
    return getattr(replay, name)


__all__ = sorted(_PUBLIC_REPLAY_NAMES)
