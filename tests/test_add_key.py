import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dashboard import api_keys  # noqa: E402

FAKE = "sk-fake-test-key-000"


class AddKeyTests(unittest.TestCase):
    def test_key_goes_to_hermes_adder_and_is_not_kept(self):
        adder = Mock()
        with patch.object(api_keys, "hermes_status", return_value={"configured": True, "key_source": "credential_pool:deepseek"}):
            status = api_keys.add_key("deepseek", f"  {FAKE}  ", adder=adder)
        adder.assert_called_once_with("deepseek", FAKE)
        self.assertEqual(status, {"configured": True, "key_source": "credential_pool:deepseek"})
        self.assertNotIn(FAKE, repr(status))

    def test_bad_provider_or_shape_is_refused_before_anything_is_stored(self):
        adder = Mock()
        for provider, key in (("nope", FAKE), ("deepseek", ""), ("deepseek", "short"), ("deepseek", "two words here xx"),
                              ("deepseek", None), ("deepseek", "x" * 301)):
            with self.assertRaises(ValueError):
                api_keys.add_key(provider, key, adder=adder)
        adder.assert_not_called()

    def test_hermes_failure_is_raised_without_the_key_text_leaking_into_our_message(self):
        def boom(provider, token):
            raise RuntimeError(f"bad token {token}")
        with self.assertRaises(RuntimeError) as caught:
            api_keys.add_key("zai", FAKE, adder=boom)
        self.assertIn(FAKE, str(caught.exception))   # the error object itself carries it; the endpoint must not echo it

    def test_the_default_adder_uses_hermes_helper_in_process_without_argv(self):
        pool = Mock()
        helper = Mock()
        with patch.dict("sys.modules", {"hermes_cli.auth_commands": SimpleNamespace(_add_api_key_credential=helper)}), \
                patch.object(api_keys, "hermes_status", return_value={"configured": True, "key_source": ""}), \
                patch.object(api_keys, "_load", return_value=pool):
            api_keys.add_key("alibaba", FAKE)
        args, kwargs = helper.call_args
        self.assertEqual(args[0].api_key, FAKE)
        self.assertEqual(args[0].label, "HARP")
        self.assertEqual(args[1], "alibaba")
        self.assertIs(args[2], pool)


if __name__ == "__main__":
    unittest.main()
