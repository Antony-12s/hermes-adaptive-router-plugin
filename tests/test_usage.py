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

    def test_tracker_counts_tokens_against_the_pool_account_of_the_session(self):
        from types import SimpleNamespace

        entry = SimpleNamespace(id="e1", label="work@example.com")
        pool = SimpleNamespace(provider="anthropic", current=lambda: entry)
        agent = SimpleNamespace(_credential_pool=pool, session_id="s-1", platform="desktop")  # noqa: F841 - found on the stack
        with tempfile.TemporaryDirectory() as directory, patch.object(harp, "_hermes_home", return_value=Path(directory)):
            harp.track_account(session_id="s-1", provider="anthropic", platform="desktop", usage={"total_tokens": 42})
            harp.track_account(session_id="s-1", provider="openai-codex", usage={"total_tokens": 99})  # fallback call
            harp.track_account(session_id="other", provider="anthropic", usage={"total_tokens": 7})
            activity = harp._sibling("pool_state").account_activity(Path(directory) / "harp-activity.sqlite3", "anthropic")
        self.assertEqual(activity, {"work@example.com": {"tokens_24h": 42, "used_by": ["desktop"]}})
