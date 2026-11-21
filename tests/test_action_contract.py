import unittest

from dashboard.action_contract import require_provider, require_account_ids


class ActionContractTests(unittest.TestCase):
    def test_rejects_unknown_provider_and_empty_targets(self):
        self.assertEqual(require_provider("anthropic"), "anthropic")
        self.assertEqual(require_provider("jev"), "jev")
        with self.assertRaises(ValueError):
            require_provider("custom:jev")
        self.assertEqual(require_account_ids(["acc-1"]), ["acc-1"])
        for targets in ([], [""], ["acc-1", "acc-1"], "acc-1"):
            with self.subTest(targets=targets), self.assertRaises(ValueError):
                require_account_ids(targets)
