"""Windows Task Scheduler timer for HARP: `hermes harp tick` every 5 minutes (park schedules, usage history, alerts).

Three files in <Hermes home>/harp-timer/ (tick.cmd, tick.vbs, task.xml while registering)
and one per-user task. No admin rights; the VBS launcher keeps the window hidden.
"""

import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

TASK_NAME = "HARP Timer"
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
    """A Windows notification through PowerShell; the text travels in the environment, never the command line."""
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


def installed():
    return task_exists(TASK_NAME)


def install(home):
    """Write the files and register (or replace) the task. RuntimeError with schtasks' message."""
    if os.name != "nt":
        raise RuntimeError("The HARP schedule timer is available on Windows only.")
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
    if installed():
        code, out = _schtasks("/Delete", "/F", "/TN", TASK_NAME)
        if code != 0:
            raise RuntimeError(f"Could not remove the HARP timer: {out or code}")
