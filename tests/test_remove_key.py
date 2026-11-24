import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dashboard import api_keys  # noqa: E402


def entry(label, source):
    return SimpleNamespace(label=label, source=source)


class RemoveKeyTests(unittest.TestCase):
    def test_only_a_key_harp_added_is_removable(self):
        pool = Mock()
        pool.entries.return_value = [entry("personal", "env:DEEPSEEK_API_KEY"), entry("HARP", "manual")]
        self.assertTrue(api_keys.removable("deepseek", lambda p: pool))
        pool.entries.return_value = [entry("personal", "env:DEEPSEEK_API_KEY")]
        self.assertFalse(api_keys.removable("deepseek", lambda p: pool))

    def test_remove_takes_out_only_the_harp_entry(self):
        pool = Mock()
        pool.entries.return_value = [entry("personal", "env:X"), entry("HARP", "manual")]
        with unittest.mock.patch.object(api_keys, "hermes_status", return_value={"configured": True, "key_source": "env"}):
            api_keys.remove_key("deepseek", pool=pool)
        pool.remove_index.assert_called_once_with(2)   # 1-based, HARP entry is the second

    def test_nothing_to_remove_is_refused_and_keys_elsewhere_are_untouched(self):
        pool = Mock()
        pool.entries.return_value = [entry("personal", "env:X")]
        with self.assertRaises(ValueError):
            api_keys.remove_key("deepseek", pool=pool)
        pool.remove_index.assert_not_called()

    def test_unknown_provider_is_refused(self):
        with self.assertRaises(ValueError):
            api_keys.remove_key("nope", pool=Mock())

    def test_overview_marks_removable_rows(self):
        home = tempfile.mkdtemp()
        view = api_keys.overview(home, status=lambda p: {"configured": True, "key_source": "x"},
                                 price=lambda m, p: None, models=lambda p: [], now=0)
        self.assertTrue(all("removable" in row for row in view["providers"]))


if __name__ == "__main__":
    unittest.main()
