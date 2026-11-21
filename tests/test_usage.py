import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

import __init__ as harp


class UsageTests(unittest.TestCase):
    def test_records_tokens_by_tag_and_keeps_fallback_provider_unassigned(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "harp.json"
            path.write_text(json.dumps({
                "provider": "primary", "primary_model": "main",
                "fast_model": "fast", "max_input_chars": 100,
                "daily_token_quotas": {"work": 1000},
            }), encoding="utf-8")
            ctx = Mock()
            with patch.dict(os.environ, HARP_CONFIG=str(path),
                            HARP_ACCOUNT="work", HARP_USAGE_TAG="qa"):
                harp.register(ctx)
            event, callback = ctx.register_hook.call_args.args
            self.assertEqual(event, "post_api_request")
            callback(provider="primary", model="main", response_model="fast",
                     usage={"prompt_tokens": 11, "completion_tokens": 9})
            callback(provider="primary", model="fast", usage=None)
            callback(provider="fallback", model="backup", usage={"total_tokens": 7})
            with closing(sqlite3.connect(path.with_suffix(".usage.sqlite3"))) as db:
                rows = db.execute("""
                    SELECT account, tag, model, tokens, requests, unmetered
                    FROM daily_usage ORDER BY account
                """).fetchall()
        self.assertEqual(rows, [
            ("Unassigned", "qa", "backup", 7, 1, 0),
            ("work", "qa", "fast", 20, 2, 1),
        ])

    def test_invalid_or_incomplete_token_counts_are_unmetered(self):
        for usage in (None, {"total_tokens": True}, {"input_tokens": 5}):
            with self.subTest(usage=usage):
                self.assertIsNone(harp.reported_tokens(usage))
