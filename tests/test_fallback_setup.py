import unittest
from types import SimpleNamespace

from dashboard import fallback_setup as fb
from dashboard import ladder


def pool(*statuses):
    return [SimpleNamespace(last_status=s) for s in statuses]


class FallbackPlanTests(unittest.TestCase):
    def test_adds_every_signed_in_oauth_provider_that_is_not_the_primary(self):
        pools = {"openai-codex": pool("ok"), "nous": pool(None), "anthropic": pool("ok")}
        chain = fb.plan("anthropic", [], pools.get)
        self.assertEqual(chain, [
            {"provider": "openai-codex", "model": ladder.RUNGS[ladder.CODEX][3][0]},
            {"provider": "nous", "model": fb.NOUS_MODEL},
        ])

    def test_keeps_the_existing_chain_and_never_duplicates_a_provider(self):
        mine = [{"provider": "openai-codex", "model": "gpt-6-sol"}]
        pools = {"openai-codex": pool("ok"), "nous": pool("ok")}
        self.assertEqual(fb.plan("anthropic", mine, pools.get), mine + [{"provider": "nous", "model": fb.NOUS_MODEL}])
        self.assertEqual(fb.plan("anthropic", fb.plan("anthropic", mine, pools.get), pools.get),
                         fb.plan("anthropic", mine, pools.get))

    def test_skips_providers_not_signed_in_or_dead_and_the_primary_itself(self):
        pools = {"openai-codex": pool("dead"), "nous": None}
        self.assertEqual(fb.plan("anthropic", [], pools.get), [])
        self.assertEqual(fb.plan("openai-codex", [], {"openai-codex": pool("ok")}.get), [])

    def test_a_codex_primary_gets_claude_as_its_fallback(self):
        chain = fb.plan("openai-codex", [], {"anthropic": pool("ok")}.get)
        self.assertEqual(chain, [{"provider": "anthropic", "model": ladder.RUNGS[ladder.ANTHROPIC][3][0]}])


class EnsureTimerTests(unittest.TestCase):
    def test_installs_once_when_needed_and_missing_and_never_raises(self):
        installs = []
        timer = SimpleNamespace(installed=lambda: False, install=installs.append)
        fb.ensure_timer(True, timer, "home")
        self.assertEqual(installs, ["home"])
        fb.ensure_timer(False, timer, "home")
        fb.ensure_timer(True, SimpleNamespace(installed=lambda: True, install=installs.append), "home")
        self.assertEqual(installs, ["home"])

        def refuse(home):
            raise RuntimeError("Task Scheduler refused")
        self.assertIs(fb.ensure_timer(True, SimpleNamespace(installed=lambda: False, install=refuse), "home"), False)


if __name__ == "__main__":
    unittest.main()
