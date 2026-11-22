"""Run HARP tick every five minutes using the per-user OS scheduler."""

import json
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

TASK_NAME = "HARP Timer"
UNIT = "harp-tick"
LABEL = "ai.hermes.harp-tick"
NO_WINDOW = 0x08000000
_TOAST = (
    "$ErrorActionPreference='Stop';"
    "$null=[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime];"
    "$null=[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom.XmlDocument,ContentType=WindowsRuntime];"
    "$e=[System.Security.SecurityElement];$x=New-Object Windows.Data.Xml.Dom.XmlDocument;"
    "$x.LoadXml('<toast><visual><binding template=''ToastGeneric''><text>'+$e::Escape($env:HARP_TOAST_TITLE)"
    "+'</text><text>'+$e::Escape($env:HARP_TOAST_BODY)+'</text></binding></visual></toast>');"
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
    "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe')"
    ".Show([Windows.UI.Notifications.ToastNotification]::new($x))"
)


def toast(title, body):
    """Show a desktop notification, if the user session supports it."""
    if sys.platform.startswith("linux"):
        return _native(["notify-send", "--", title, body])[0] == 0
    if sys.platform == "darwin":
        script = "on run argv\n display notification (item 2 of argv) with title (item 1 of argv)\nend run"
        return _native(["osascript", "-e", script, title, body])[0] == 0
    import base64

    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if os.name != "nt" or not powershell.exists():
        return False
    script = base64.b64encode(_TOAST.encode("utf-16-le")).decode("ascii")
    try:
        result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                 "-EncodedCommand", script], env=dict(os.environ, HARP_TOAST_TITLE=title, HARP_TOAST_BODY=body),
                                capture_output=True, timeout=30, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def telegram(title, body):
    """Send to the Telegram bot's home channel with Hermes' own `hermes send`."""
    try:
        result = subprocess.run([hermes_alias(), "send", "-t", "telegram", "-q", f"{title}\n{body}"],
                                capture_output=True, text=True, timeout=60, creationflags=NO_WINDOW if os.name == "nt" else 0,
                                stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def sender(settings):
    """A send(title, body) for the enabled channels; returns which channels delivered."""
    def send(title, body):
        sent = []
        if settings.get("desktop") and toast(title, body):
            sent.append("desktop")
        if settings.get("telegram") and telegram(title, body):
            sent.append("telegram")
        return sent
    return send


def hermes_alias():
    """The per-user app alias survives Hermes updates; the versioned WindowsApps path does not."""
    if os.name != "nt":
        return shutil.which("hermes") or "hermes"
    alias = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps" / "hermes.exe"
    return str(alias)


def files(home):
    folder = Path(home) / "harp-timer"
    cmd = "\r\n".join(["@echo off", "rem HARP timer: park schedules, usage history and alerts; tick.vbs runs this every 5 minutes.",
                       'cd /d "%~dp0"', f'set "HERMES_HOME={home}"',
                       f'"{hermes_alias()}" harp tick >>tick.log 2>&1', "exit /b %ERRORLEVEL%"]) + "\r\n"
    vbs = "\r\n".join([
        "Option Explicit", "Dim sh, fso, dir, logf",
        'Set sh = CreateObject("WScript.Shell")', 'Set fso = CreateObject("Scripting.FileSystemObject")',
        "dir = fso.GetParentFolderName(WScript.ScriptFullName)", 'logf = dir & "\\tick.log"',
        "If fso.FileExists(logf) Then", "  If fso.GetFile(logf).Size > 1048576 Then fso.DeleteFile logf", "End If",
        "sh.CurrentDirectory = dir", 'WScript.Quit sh.Run("cmd.exe /d /c tick.cmd", 0, True)']) + "\r\n"
    return folder, cmd, vbs


def task_xml(vbs, start=None):
    start = (start or datetime.now() + timedelta(minutes=1)).replace(microsecond=0)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>HARP: park schedules, usage history and alerts every 5 minutes.</Description></RegistrationInfo>
  <Triggers><TimeTrigger>
    <Repetition><Interval>PT5M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
    <StartBoundary>{start.strftime("%Y-%m-%dT%H:%M:%S")}</StartBoundary><Enabled>true</Enabled>
  </TimeTrigger></Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable><ExecutionTimeLimit>PT10M</ExecutionTimeLimit><Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author"><Exec><Command>wscript.exe</Command><Arguments>//B //Nologo "{escape(str(vbs))}"</Arguments></Exec></Actions>
</Task>
"""


def _schtasks(*args):
    exe = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "schtasks.exe"
    result = subprocess.run([str(exe), *args], capture_output=True, text=True, timeout=60, creationflags=NO_WINDOW)
    return result.returncode, (result.stdout + result.stderr).strip()


def task_exists(name):
    return os.name == "nt" and _schtasks("/Query", "/TN", name)[0] == 0


def _native(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as error:
        return 1, str(error)
    return result.returncode, (result.stdout + result.stderr).strip()


def _checked(command):
    code, detail = _native(command)
    if code:
        raise RuntimeError(f"Could not configure HARP Timer with {command[0]}: {detail or code}")


def _linux_units():
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user"


def _mac_agent():
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _mac_domain():
    return f"gui/{os.getuid()}"


def _install_unix(home):
    executable = shutil.which("hermes")
    if not executable:
        raise RuntimeError("Hermes must be on PATH to install the HARP Timer.")
    home = str(Path(home).resolve())
    if sys.platform.startswith("linux"):
        folder = _linux_units()
        folder.mkdir(parents=True, exist_ok=True)
        launcher = Path(home) / "harp-timer" / "tick.sh"
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_text(f"export HERMES_HOME={shlex.quote(home)}\nexec {shlex.quote(executable)} harp tick\n", encoding="utf-8")
        argument = json.dumps(str(launcher).replace("%", "%%").replace("$", "$$"), ensure_ascii=False)
        (folder / f"{UNIT}.service").write_text(
            f"[Service]\nType=oneshot\nExecStart=/bin/sh {argument}\nTimeoutStartSec=10min\n", encoding="utf-8")
        (folder / f"{UNIT}.timer").write_text(
            "[Timer]\nOnActiveSec=1min\nOnUnitInactiveSec=5min\n[Install]\nWantedBy=timers.target\n", encoding="utf-8")
        _checked(["systemctl", "--user", "daemon-reload"])
        _checked(["systemctl", "--user", "enable", "--now", f"{UNIT}.timer"])
    elif sys.platform == "darwin":
        agent = _mac_agent()
        domain = _mac_domain()
        if installed():
            _checked(["launchctl", "bootout", f"{domain}/{LABEL}"])
        agent.parent.mkdir(parents=True, exist_ok=True)
        agent.write_bytes(plistlib.dumps({
            "Label": LABEL, "ProgramArguments": [executable, "harp", "tick"],
            "EnvironmentVariables": {"HERMES_HOME": home, "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
            "StartInterval": 300, "RunAtLoad": True,
        }))
        _checked(["launchctl", "bootstrap", domain, str(agent)])
    else:
        raise RuntimeError("HARP Timer requires Windows, Linux with systemd, or macOS.")


def installed():
    if os.name == "nt":
        return task_exists(TASK_NAME)
    if sys.platform.startswith("linux"):
        return _native(["systemctl", "--user", "is-active", "--quiet", f"{UNIT}.timer"])[0] == 0
    if sys.platform == "darwin":
        return _native(["launchctl", "print", f"{_mac_domain()}/{LABEL}"])[0] == 0
    return False


def install(home):
    """Write the files and register (or replace) the task. RuntimeError with schtasks' message."""
    if os.name != "nt":
        return _install_unix(home)
    folder, cmd, vbs = files(home)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "tick.cmd").write_text(cmd, encoding="utf-8", newline="")
    (folder / "tick.vbs").write_text(vbs, encoding="ascii", newline="")
    xml = folder / "task.xml"
    xml.write_text(task_xml(folder / "tick.vbs"), encoding="utf-16", newline="")
    try:
        code, out = _schtasks("/Create", "/F", "/TN", TASK_NAME, "/XML", str(xml))
        if code != 0:
            raise RuntimeError(f"Task Scheduler refused the HARP timer: {out or code}")
    finally:
        xml.unlink(missing_ok=True)


def remove():
    if sys.platform.startswith("linux"):
        folder = _linux_units()
        timer = folder / f"{UNIT}.timer"
        if timer.exists():
            _checked(["systemctl", "--user", "disable", "--now", f"{UNIT}.timer"])
            timer.unlink()
            (folder / f"{UNIT}.service").unlink(missing_ok=True)
            _checked(["systemctl", "--user", "daemon-reload"])
        return
    if sys.platform == "darwin":
        if installed():
            _checked(["launchctl", "bootout", f"{_mac_domain()}/{LABEL}"])
        _mac_agent().unlink(missing_ok=True)
        return
    if installed():
        code, out = _schtasks("/Delete", "/F", "/TN", TASK_NAME)
        if code != 0:
            raise RuntimeError(f"Could not remove the HARP timer: {out or code}")
