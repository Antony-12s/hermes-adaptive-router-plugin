import sys
import unittest

from dashboard.signin_relay import Relay, check_code, parse


class SigninParseTests(unittest.TestCase):
    def test_reads_code_link_and_device_code(self):
        code_flow = "Open this link:\nhttps://claude.ai/oauth/authorize?code=true&state=abc\nPaste the authorization code: "
        self.assertEqual(parse(code_flow), {"kind": "code", "url": "https://claude.ai/oauth/authorize?code=true&state=abc"})
        device = "Visit https://auth.openai.com/codex/device\nEnter this code:\n  ABCD-1234\n"
        self.assertEqual(parse(device), {"kind": "device", "url": "https://auth.openai.com/codex/device", "user_code": "ABCD-1234"})
        self.assertIsNone(parse("Starting sign-in..."))

    def test_pasted_code_must_carry_the_link_state(self):
        url = "https://claude.ai/oauth/authorize?state=abc"
        self.assertEqual(check_code(url, " xyz#abc "), "xyz#abc")
        for bad in ("", "xyz#other"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                check_code(url, bad)

    def test_relay_relays_input_and_reports_exit(self):
        script = "import sys; print('Paste the authorization code:', flush=True); print('got ' + input())"
        relay = Relay([sys.executable, "-c", script], env=None)
        try:
            self.assertTrue(relay.wait_for(lambda out: "authorization code" in out, 10))
            relay.send("token#abc")
            self.assertEqual(relay.returncode(wait=10), 0)
            self.assertIn("got token#abc", relay.output())
        finally:
            relay.close()
