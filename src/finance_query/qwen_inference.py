"""Optional 4-bit Qwen inference adapter kept outside base dependencies."""
from __future__ import annotations

import json
from typing import Any


class QwenGenerator:
    """Generate one JSON response from Qwen without importing it at package load."""

    def __init__(self, model_name: str, max_new_tokens: int) -> None:
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        except ImportError as error:  # pragma: no cover - environment specific
            raise RuntimeError(
                "Qwen 14B inference needs transformers, accelerate and bitsandbytes; "
                "install the optional Kaggle dependencies first."
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
        self.max_new_tokens = max_new_tokens

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
