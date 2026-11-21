"""Relay `hermes auth add` to the HARP Desktop page.

Hermes performs the OAuth exchange and stores the account; this module only
forwards the sign-in link to the page and the pasted code back to the command.
It never reads or returns a token.
"""

import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
AUTHORIZE = re.compile(r"https://\S+?/authorize\?\S+")
DEVICE_URL = re.compile(r"(https://\S+/device)\b")
DEVICE_CODE = re.compile(r"Enter (?:this|the) code:?\s*\n\s*([A-Z0-9][A-Z0-9-]{3,})")


def parse(text):
    """What the page needs from the command output, or None until it is printed."""
    device_url, device_code = DEVICE_URL.search(text), DEVICE_CODE.search(text)
    if device_url and device_code:
        return {"kind": "device", "url": device_url.group(1), "user_code": device_code.group(1)}
    link = AUTHORIZE.search(text)
    if link and "authorization code" in text.lower():
        return {"kind": "code", "url": link.group(0)}
    return None


def check_code(url, code):
    """Return the trimmed pasted code if it belongs to this sign-in link."""
    code = (code or "").strip()
    if not code:
        raise ValueError("Paste the code shown after you authorize.")
    state = (urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("state") or [""])[0]
    if state and code.partition("#")[2] != state:
        raise ValueError("This code belongs to another sign-in. Copy the whole code from this link.")
    return code


class Relay:
    """One running `hermes auth add` child and its output so far."""

    def __init__(self, command, env):
        env = dict(env or os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", NO_COLOR="1", TERM="dumb")
        windows = sys.platform == "win32"
        options = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if windows \
            else {"start_new_session": True}
        self._windows = windows
        self._raw = ""
        self._done = False
        self._changed = threading.Condition()
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, env=env, **options)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        while True:
            try:
                chunk = self.process.stdout.read1(4096)
            except (OSError, ValueError):
                chunk = b""
            with self._changed:
                if not chunk:
                    self._done = True
                    self._changed.notify_all()
                    return
                self._raw += chunk.decode("utf-8", "replace")
                self._changed.notify_all()

    def output(self):
        return ANSI.sub("", self._raw).replace("\r", "")

    def wait_for(self, test, timeout):
        deadline = time.monotonic() + timeout
        with self._changed:
            while True:
                found = test(self.output())
                left = deadline - time.monotonic()
                if found or self._done or left <= 0:
                    return found
                self._changed.wait(min(left, 0.5))

    def send(self, line):
        self.process.stdin.write((line.strip() + "\n").encode("utf-8"))
        self.process.stdin.flush()

    def returncode(self, wait=0):
        try:
            return self.process.wait(timeout=wait) if wait else self.process.poll()
        except subprocess.TimeoutExpired:
            return None

    def reason(self):
        """Last output lines without links or codes: why the command stopped."""
        lines = [line.strip() for line in self.output().splitlines()]
        keep = [line for line in lines if line and "://" not in line and not DEVICE_CODE.search(line + "\n")]
        return " ".join(keep[-3:])[:300]

    def close(self):
        if self.process.poll() is None:
            if self._windows:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.process.pid)], capture_output=True, timeout=15)
            else:
                self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        for stream in (self.process.stdin, self.process.stdout):
            try:
                stream.close()
            except (OSError, ValueError):
                pass
