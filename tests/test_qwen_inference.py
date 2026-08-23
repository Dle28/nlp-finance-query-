from __future__ import annotations

from contextlib import nullcontext
from types import ModuleType, SimpleNamespace
import sys
import unittest
from unittest.mock import patch

from finance_query.config import ModelConfig
from finance_query.qwen_inference import QwenGenerator, parse_json_object


class _FakeBatch(dict):
    def __init__(self) -> None:
        super().__init__(input_ids=SimpleNamespace(shape=(1, 3)))
        self.target_device = None

    def to(self, device):
        self.target_device = device
        return self


def _fake_modules(*, cuda_available: bool) -> tuple[ModuleType, ModuleType, dict]:
    calls: dict = {}
    torch = ModuleType("torch")
    torch.cuda = SimpleNamespace(is_available=lambda: cuda_available)
    torch.float16 = "float16"
    torch.inference_mode = nullcontext

    class AutoTokenizer:
        @classmethod
        def from_pretrained(cls, model_name):
            calls["tokenizer_model"] = model_name
            return SimpleNamespace(
                eos_token_id=7,
                apply_chat_template=lambda *args, **kwargs: _FakeBatch(),
                decode=lambda tokens, **kwargs: 'prefix {"verdict":"supported"} suffix',
            )

    class FakeModel:
        device = "cuda:0" if cuda_available else "cpu"

        def generate(self, **kwargs):
            calls["generate_kwargs"] = kwargs
            return [[10, 11, 12, 13, 14]]

    class AutoModelForCausalLM:
        @classmethod
        def from_pretrained(cls, model_name, **kwargs):
            calls["model_name"] = model_name
            calls["model_kwargs"] = kwargs
            return FakeModel()

    class BitsAndBytesConfig:
        def __init__(self, **kwargs):
            calls["quantization_kwargs"] = kwargs

    transformers = ModuleType("transformers")
    transformers.AutoModelForCausalLM = AutoModelForCausalLM
    transformers.AutoTokenizer = AutoTokenizer
    transformers.BitsAndBytesConfig = BitsAndBytesConfig
    return torch, transformers, calls


class QwenInferenceTests(unittest.TestCase):
    def test_parse_json_object_uses_first_valid_object(self):
        self.assertEqual(
            parse_json_object('noise {bad} {"first": 1} {"second": 2}'),
            {"first": 1},
        )

    def test_parse_json_object_rejects_non_object_output(self):
        with self.assertRaisesRegex(ValueError, "did not return a JSON object"):
            parse_json_object('noise [1, 2, 3]')

    def test_invalid_arguments_fail_before_model_import(self):
        with self.assertRaisesRegex(ValueError, "model_name"):
            QwenGenerator(" ", 10)
        with self.assertRaisesRegex(ValueError, "max_new_tokens"):
            QwenGenerator("model", 0)
        with self.assertRaisesRegex(ValueError, "Unsupported device"):
            QwenGenerator("model", 10, device="mps")
        with self.assertRaisesRegex(ValueError, "requires device='cuda'"):
            QwenGenerator("model", 10, device="cpu", load_in_4bit=True)

    def test_auto_cpu_loads_without_quantization_and_generates_json(self):
        torch, transformers, calls = _fake_modules(cuda_available=False)
        with patch.dict(sys.modules, {"torch": torch, "transformers": transformers}):
            generator = QwenGenerator("local/model", 24)
            result = generator("select literal evidence")

        self.assertEqual(generator.device, "cpu")
        self.assertFalse(generator.load_in_4bit)
        self.assertEqual(calls["model_kwargs"], {"torch_dtype": "auto"})
        self.assertEqual(result, {"verdict": "supported"})
        self.assertFalse(calls["generate_kwargs"]["do_sample"])
        self.assertEqual(calls["generate_kwargs"]["max_new_tokens"], 24)

    def test_auto_cuda_defaults_to_nf4_quantization(self):
        torch, transformers, calls = _fake_modules(cuda_available=True)
        with patch.dict(sys.modules, {"torch": torch, "transformers": transformers}):
            generator = QwenGenerator("remote/model", 32)

        self.assertEqual(generator.device, "cuda")
        self.assertTrue(generator.load_in_4bit)
        self.assertEqual(
            calls["quantization_kwargs"],
            {
                "load_in_4bit": True,
                "bnb_4bit_quant_type": "nf4",
                "bnb_4bit_compute_dtype": "float16",
            },
        )
        self.assertEqual(calls["model_kwargs"]["device_map"], "auto")

    def test_inference_device_auto_does_not_return_unsupported_mps(self):
        torch, _, _ = _fake_modules(cuda_available=False)
        torch.backends = SimpleNamespace(mps=SimpleNamespace(is_available=lambda: True))
        with patch.dict(sys.modules, {"torch": torch}):
            config = ModelConfig()
            self.assertEqual(config.resolved_device(), "mps")
            self.assertEqual(config.resolved_inference_device(), "cpu")


if __name__ == "__main__":
    unittest.main()
