"""Windows Task Scheduler timer for HARP park schedules: `hermes harp tick` every 5 minutes.

Three files in <Hermes home>/harp-timer/ (tick.cmd, tick.vbs, task.xml while registering)
and one per-user task. No admin rights; the VBS launcher keeps the window hidden.
"""

import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

TASK_NAME = "HARP Schedule"
NO_WINDOW = 0x08000000


def hermes_alias():
    """The per-user app alias survives Hermes updates; the versioned WindowsApps path does not."""
    alias = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps" / "hermes.exe"
    return str(alias)


def files(home):
    folder = Path(home) / "harp-timer"
    cmd = "\r\n".join(["@echo off", "rem HARP park schedules; tick.vbs runs this every 5 minutes.",
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
  <RegistrationInfo><Description>HARP: apply account park schedules every 5 minutes.</Description></RegistrationInfo>
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


def installed():
    return os.name == "nt" and _schtasks("/Query", "/TN", TASK_NAME)[0] == 0


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
