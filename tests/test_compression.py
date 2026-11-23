import unittest

from dashboard import compression


class SettingsTests(unittest.TestCase):
    def test_reads_threshold_and_enabled_with_hermes_defaults(self):
        self.assertEqual(compression.parse_settings({}), {"enabled": True, "threshold": 0.5, "presets": [0.5, 0.6, 0.75, 0.85]})
        self.assertEqual(compression.parse_settings({"compression": {"threshold": 0.75}})["threshold"], 0.75)
        self.assertFalse(compression.parse_settings({"compression": {"enabled": False}})["enabled"])

    def test_bad_config_values_fall_back_instead_of_breaking_the_card(self):
        self.assertEqual(compression.parse_settings({"compression": {"threshold": "high"}})["threshold"], 0.5)
        self.assertEqual(compression.parse_settings({"compression": {"threshold": True}})["threshold"], 0.5)
        self.assertEqual(compression.parse_settings(None)["threshold"], 0.5)

    def test_writes_only_presets_or_off(self):
        self.assertEqual(compression.settings_change({"threshold": 0.75}),
                         [("compression.enabled", "true"), ("compression.threshold", "0.75")])
        self.assertEqual(compression.settings_change({"threshold": 0.6}), [("compression.enabled", "true"), ("compression.threshold", "0.6")])
        self.assertEqual(compression.settings_change({"enabled": False}), [("compression.enabled", "false")])

    def test_rejects_anything_off_the_presets(self):
        for body in ({"threshold": 0.7}, {"threshold": 0.9}, {"threshold": "0.75"}, {"threshold": True}, {}, None, ["0.75"]):
            with self.subTest(body=body), self.assertRaises(ValueError):
                compression.settings_change(body)


class ContextViewTests(unittest.TestCase):
    def test_uses_hermes_percent_and_counts_compressions(self):
        view = compression.context_view({"context_used": 124000, "context_max": 200000, "context_percent": 62, "compressions": 2})
        self.assertEqual(view, {"used": 124000, "max": 200000, "percent": 62, "compressions": 2})

    def test_computes_percent_when_hermes_omits_it_and_clamps_it(self):
        self.assertEqual(compression.context_view({"context_used": 50, "context_max": 200})["percent"], 25)
        self.assertEqual(compression.context_view({"context_used": 250, "context_max": 200, "context_percent": 180})["percent"], 100)

    def test_no_window_means_no_card_numbers(self):
        for usage in (None, {}, {"context_used": 0, "context_max": 0}, {"context_used": 5, "context_max": None}):
            with self.subTest(usage=usage):
                self.assertIsNone(compression.context_view(usage))

    def test_compact_token_counts(self):
        self.assertEqual(compression.format_tokens(124000), "124K")
        self.assertEqual(compression.format_tokens(200000), "200K")
        self.assertEqual(compression.format_tokens(1500000), "1.5M")
        self.assertEqual(compression.format_tokens(2000000), "2M")
        self.assertEqual(compression.format_tokens(850), "850")


if __name__ == "__main__":
    unittest.main()
