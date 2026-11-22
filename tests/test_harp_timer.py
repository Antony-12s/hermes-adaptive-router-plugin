import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dashboard import harp_timer


class HarpTimerTests(unittest.TestCase):
    def test_linux_creates_user_units_and_quotes_hermes_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "profile 50%"
            units = root / "units"
            with patch.object(harp_timer.sys, "platform", "linux"), \
                 patch.object(harp_timer, "_linux_units", return_value=units), \
                 patch.object(harp_timer.shutil, "which", return_value="/opt/Hermes app/bin/hermes"), \
                 patch.object(harp_timer, "_checked") as checked:
                harp_timer._install_unix(home)
                self.assertIn("OnUnitInactiveSec=5min", (units / "harp-tick.timer").read_text())
                self.assertIn("profile 50%%", (units / "harp-tick.service").read_text())
                launcher = (home / "harp-timer" / "tick.sh").read_text()
                self.assertIn("exec '/opt/Hermes app/bin/hermes' harp tick", launcher)
                checked.assert_called_with(["systemctl", "--user", "enable", "--now", "harp-tick.timer"])
                harp_timer.remove()
                self.assertFalse((units / "harp-tick.service").exists())
                self.assertFalse((units / "harp-tick.timer").exists())

    def test_macos_creates_launch_agent_with_profile_and_interval(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            agent = home / "LaunchAgents" / "harp.plist"
            with patch.object(harp_timer.sys, "platform", "darwin"), \
                 patch.object(harp_timer, "_mac_agent", return_value=agent), \
                 patch.object(harp_timer, "_mac_domain", return_value="gui/100"), \
                 patch.object(harp_timer.shutil, "which", return_value="/opt/hermes"), \
                 patch.object(harp_timer, "installed", return_value=False), \
                 patch.object(harp_timer, "_checked") as checked:
                harp_timer._install_unix(home)
                data = plistlib.loads(agent.read_bytes())
                self.assertEqual(data["ProgramArguments"], ["/opt/hermes", "harp", "tick"])
                self.assertEqual(data["StartInterval"], 300)
                self.assertEqual(data["EnvironmentVariables"]["HERMES_HOME"], str(home.resolve()))
                checked.assert_called_with(["launchctl", "bootstrap", "gui/100", str(agent)])
                harp_timer.remove()
                self.assertFalse(agent.exists())
