import io
import json
import unittest
import urllib.error

from dashboard import typesafe


class Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class CheckTests(unittest.TestCase):
    def test_valid_key_lists_models_and_sends_bearer(self):
        seen = {}

        def opener(request, timeout):
            seen["url"], seen["auth"], seen["timeout"] = request.full_url, request.get_header("Authorization"), timeout
            return Response(json.dumps({"models": [{"name": "jev-latest"}]}).encode())

        result = typesafe.check("k-1", opener=opener)
        self.assertEqual(seen, {"url": "https://api.typesafe.ai/v1/models", "auth": "Bearer k-1", "timeout": 15})
        self.assertEqual((result["ok"], result["status"], result["models"]), (True, 200, ["jev-latest"]))
        self.assertNotIn("k-1", json.dumps(result))

    def test_rejected_and_unreachable_keys(self):
        def rejected(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(b"{}"))

        def offline(request, timeout):
            raise urllib.error.URLError("no route")

        self.assertEqual({k: typesafe.check("bad", opener=rejected)[k] for k in ("ok", "status", "problem")},
                         {"ok": False, "status": 401, "problem": "rejected: invalid key"})
        self.assertEqual({k: typesafe.check("k", opener=offline)[k] for k in ("ok", "status", "problem")},
                         {"ok": False, "status": None, "problem": "unreachable"})

    def test_rate_limited_key_is_still_a_real_key(self):
        def limited(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 429, "Too Many", {}, io.BytesIO(b"{}"))

        result = typesafe.check("k", opener=limited)
        self.assertEqual((result["ok"], result["problem"]), (False, "rate-limited"))
        self.assertTrue(typesafe.key_accepted(result))
        self.assertFalse(typesafe.key_accepted({"ok": False, "status": 401}))
