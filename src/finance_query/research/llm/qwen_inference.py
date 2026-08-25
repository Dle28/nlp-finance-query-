"""Optional Qwen inference adapter kept outside base dependencies."""

from __future__ import annotations

import json
from typing import Any


class QwenGenerator:
    """Generate one JSON response from Qwen without importing it at package load.

    Parameters
    ----------
    model_name:
        Hugging Face model id or local snapshot path.
    max_new_tokens:
        Upper bound for generated tokens.
    device:
        ``"auto"`` (default), ``"cuda"``, or ``"cpu"``.  ``"auto"`` probes
        ``torch.cuda.is_available()`` and chooses ``"cuda"`` when available.
    load_in_4bit:
        ``True`` forces NF4 4-bit quantization (requires CUDA + bitsandbytes).
        ``False`` forces full-precision loading.  ``None`` (default) means
        ``True`` on CUDA and ``False`` on CPU — so CPU smoke tests do not
        require bitsandbytes or a GPU.
    """

    def __init__(
        self,
        model_name: str,
        max_new_tokens: int,
        *,
        device: str = "auto",
        load_in_4bit: bool | None = None,
    ) -> None:
        if not model_name.strip():
            raise ValueError("model_name must not be empty")
        if max_new_tokens <= 0:
            raise ValueError("max_new_tokens must be positive")
        self.model_name = model_name
        self.max_new_tokens = max_new_tokens

        if device == "auto":
            try:
                import torch as _torch

                device = "cuda" if _torch.cuda.is_available() else "cpu"
            except ImportError:
                device = "cpu"
        if device not in {"cuda", "cpu"}:
            raise ValueError(f"Unsupported device {device!r}; expected 'cuda' or 'cpu' or 'auto'")
        self.device = device

        if load_in_4bit is None:
            load_in_4bit = device == "cuda"
        self.load_in_4bit = load_in_4bit

        if load_in_4bit and device != "cuda":
            raise ValueError("4-bit quantization requires device='cuda'")

        if load_in_4bit:
            try:
                import torch
                from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
            except ImportError as error:  # pragma: no cover - environment specific
                raise RuntimeError(
                    "Qwen 4-bit inference needs transformers, accelerate and bitsandbytes; "
                    "install the optional llm dependencies first: pip install -e '.[llm]'."
                ) from error
            self.torch = torch
            self.tokenizer = AutoTokenizer.from_pretrained(model_name)
            quantization = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.float16,
            )
            self.model = AutoModelForCausalLM.from_pretrained(
                model_name,
                device_map="auto",
                torch_dtype=torch.float16,
                quantization_config=quantization,
            )
        else:
            try:
                import torch
                from transformers import AutoModelForCausalLM, AutoTokenizer
            except ImportError as error:  # pragma: no cover - environment specific
                raise RuntimeError(
                    "Qwen inference needs torch and transformers; "
                    "install the base dependencies first: pip install -e ."
                ) from error
            self.torch = torch
            self.tokenizer = AutoTokenizer.from_pretrained(model_name)
            kwargs: dict[str, Any] = {"torch_dtype": "auto"}
            if device == "cuda":
                kwargs["device_map"] = "auto"
            self.model = AutoModelForCausalLM.from_pretrained(model_name, **kwargs)

    def __call__(self, prompt: str) -> dict[str, Any]:
        messages = [{"role": "user", "content": prompt}]
        inputs = self.tokenizer.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
        ).to(self.model.device)
        with self.torch.inference_mode():
            output = self.model.generate(
                **inputs,
                do_sample=False,
                max_new_tokens=self.max_new_tokens,
                pad_token_id=self.tokenizer.eos_token_id,
            )
        generated = output[0][inputs["input_ids"].shape[-1] :]
        return parse_json_object(self.tokenizer.decode(generated, skip_special_tokens=True).strip())


def parse_json_object(text: str) -> dict[str, Any]:
    """Extract first valid JSON object without greedily joining two objects."""
    decoder = json.JSONDecoder()
    for start, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("LLM did not return a JSON object")
