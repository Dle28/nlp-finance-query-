"""Minimal runtime configuration for the isolated open-weight diagnostic lane."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InferenceConfig:
    """Resolve only devices supported by :class:`QwenGenerator`.

    This deliberately does not carry retrieval, router or answer-pipeline
    settings.  The diagnostic lane is an optional consumer of an already
    prepared, non-authorizing packet.
    """

    device: str = "auto"

    def resolved_device(self) -> str:
        if self.device in {"cpu", "cuda"}:
            return self.device
        if self.device != "auto":
            raise ValueError("unsupported inference device")
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda"
        except ImportError:
            pass
        return "cpu"
