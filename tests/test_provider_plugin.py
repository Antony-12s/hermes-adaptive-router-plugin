import importlib.util
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

PROVIDER_DIR = Path(__file__).resolve().parents[1] / "provider"


@dataclass
class StubProfile:
    name: str
    display_name: str = ""
    description: str = ""
    env_vars: tuple = ()
    base_url: str = ""
    api_mode: str = "chat_completions"
    auth_type: str = "api_key"
    supports_model_listing: bool = True
    supports_health_check: bool = True
    fallback_models: tuple = ()
    default_aux_model: str = ""
    aliases: tuple = field(default=())


def load_provider():
    registered = []
    providers = ModuleType("providers")
    providers.register_provider = registered.append
    base = ModuleType("providers.base")
    base.ProviderProfile = StubProfile
    with patch.dict(sys.modules, {"providers": providers, "providers.base": base}):
        spec = importlib.util.spec_from_file_location("harp_provider_under_test", PROVIDER_DIR / "__init__.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module, registered


def completed(content="hi", tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls, reasoning=None, reasoning_content=None,
                              reasoning_details=None)
    return SimpleNamespace(id="r1", model="claude-haiku-5-5", usage={"total_tokens": 3},
                           choices=[SimpleNamespace(index=0, message=message, finish_reason="stop")])


class ProviderPluginTests(unittest.TestCase):
    def test_manifest_is_a_model_provider(self):
        manifest = (PROVIDER_DIR / "plugin.yaml").read_text(encoding="utf-8")
        self.assertIn("kind: model-provider", manifest)

    def test_registers_harp_with_its_three_modes_and_a_placeholder_key(self):
        module, registered = load_provider()
        (profile,) = registered
        self.assertEqual((profile.name, profile.display_name, profile.api_mode), ("harp", "HARP", "chat_completions"))
        self.assertEqual(profile.fallback_models, ("harp-save", "harp-balanced", "harp-best"))
        self.assertEqual(profile.env_vars, ("HARP_ROUTER_KEY",))
        self.assertFalse(profile.supports_model_listing or profile.supports_health_check)
        self.assertTrue(profile.base_url.startswith("harp://"))
        self.assertGreater(profile.get_model_context_length("harp-best"), 100_000)
        self.assertIsInstance(profile.create_client(api_key="local"), module.HarpFacade)

    def test_unbound_calls_go_to_the_cheap_rung(self):
        module, _ = load_provider()
        calls = []
        facade = module.HarpFacade(call=lambda **kw: calls.append(kw) or completed("aux ok"))
        response = facade.chat.completions.create(model="harp-best", messages=[{"role": "user", "content": "x"}],
                                                  max_tokens=20, temperature=0.2)
        self.assertEqual(response.choices[0].message.content, "aux ok")
        self.assertEqual((calls[0]["provider"], calls[0]["model"]), module.FACADE_ROUTE)
        self.assertEqual((calls[0]["max_tokens"], calls[0]["temperature"]), (20, 0.2))

    def test_a_streaming_request_gets_one_chunk_with_text_and_tool_calls(self):
        module, _ = load_provider()
        tool = SimpleNamespace(id="t1", type="function", function=SimpleNamespace(name="read_file", arguments="{}"))
        facade = module.HarpFacade(call=lambda **kw: completed("done", [tool]))
        (chunk,) = list(facade.chat.completions.create(model="harp-best", messages=[], stream=True))
        delta = chunk.choices[0].delta
        self.assertEqual((delta.content, delta.tool_calls[0].function.name, delta.tool_calls[0].index), ("done", "read_file", 0))
        self.assertEqual((chunk.choices[0].finish_reason, chunk.usage), ("stop", {"total_tokens": 3}))


if __name__ == "__main__":
    unittest.main()
