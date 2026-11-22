import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from dashboard import delegation


JOB = {"name": "Review PR", "description": "review changes before merging",
       "provider": "openai-codex", "model": "gpt-6-sol", "effort": "medium"}


class DelegationTests(unittest.TestCase):
    def test_saved_job_is_selected_by_hermes_backend_and_recorded(self):
        with tempfile.TemporaryDirectory() as home:
            config = delegation.validate({"classifier": "local", "jobs": [JOB]})
            delegation.write(home, "harp-delegation.json", config)
            api = SimpleNamespace(DelegationProvider=object, ROUTE_KEYS={"model", "provider", "reasoning_effort"})
            with patch.dict(sys.modules, {"agent.delegation_provider": api}):
                backend = delegation.backend(lambda: SimpleNamespace(home=home))
            default = {"provider": "anthropic", "model": "sonnet"}
            self.assertEqual(backend.route_task({"goal": "Translate a paragraph"}, default), default)
            result = backend.route_task({"goal": "Review PR 42"}, default)
            self.assertEqual(result, {"provider": "openai-codex", "model": "gpt-6-sol", "reasoning_effort": "medium"})
            self.assertEqual(delegation.read(home, "harp-delegation-latest.json", None),
                             {**result, "job": "Review PR", "source": "local"})
            self.assertEqual(backend.task_parameters({})["harp_work"]["enum"], ["Review PR"])
            self.assertEqual(backend.route_task({"goal": "unrelated", "harp_work": "Review PR"}, default), result)
            self.assertEqual(delegation.read(home, "harp-delegation-latest.json", None)["source"], "manual")

    def test_jev_result_falls_back_to_local_if_unavailable(self):
        config = delegation.validate({"classifier": "jev", "jobs": [JOB]})
        engine = SimpleNamespace(classify_delegation=Mock(side_effect=TimeoutError))
        self.assertEqual(delegation.select("Review PR", config, engine), (JOB, "local"))
        engine.classify_delegation = Mock(return_value="job_0")
        self.assertEqual(delegation.select("Check this", config, engine), (JOB, "jev"))

    def test_duplicate_jobs_and_invalid_routes_are_rejected(self):
        for jobs in ([JOB, JOB], [{**JOB, "provider": "unknown"}], [{**JOB, "effort": "maximum"}]):
            with self.subTest(jobs=jobs), self.assertRaises(ValueError):
                delegation.validate({"classifier": "local", "jobs": jobs})
