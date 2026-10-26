"""Report the foreground app and idle time. Pause by creating a file named paused next to this script."""

from __future__ import annotations

import argparse
import ctypes
import os
import time
import urllib.parse
import urllib.request
from ctypes import wintypes
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAUSE_FILE = HERE / "paused"
TOKEN_FILE = HERE / "pace.token"

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def foreground_window() -> str:
    hwnd = user32.GetForegroundWindow()
    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value.strip()


def foreground_name() -> str:
    hwnd = user32.GetForegroundWindow()
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)
    if not handle:
        return "unknown"
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return "unknown"
        return os.path.basename(buffer.value) or "unknown"
    finally:
        kernel32.CloseHandle(handle)


def idle_seconds() -> int:
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 0
    elapsed = kernel32.GetTickCount() - info.dwTime
    if elapsed < 0:
        elapsed += 2**32
    return int(elapsed / 1000)


def send(host: str, token: str) -> None:
    paused = PAUSE_FILE.exists()
    query = urllib.parse.urlencode(
        {
            "app": foreground_name(),
            "window": "" if paused else foreground_window(),
            "idle_seconds": idle_seconds(),
            "paused": "true" if paused else "false",
            "token": token,
        }
    )
    url = f"http://{host}/api/activity?{query}"
    with urllib.request.urlopen(url, data=b"", timeout=5) as response:
        response.read()


def main() -> None:
    parser = argparse.ArgumentParser(description="Pace desktop companion")
    parser.add_argument("--host", default="127.0.0.1:7462")
    parser.add_argument("--token", default="")
    parser.add_argument("--toggle-pause", action="store_true")
    args = parser.parse_args()
    if args.toggle_pause:
        if PAUSE_FILE.exists():
            PAUSE_FILE.unlink()
            print("Observation on")
        else:
            PAUSE_FILE.write_text("paused", encoding="utf-8")
            print("Observation paused")
        return
    token = args.token or (TOKEN_FILE.read_text(encoding="utf-8").strip() if TOKEN_FILE.exists() else "")
    print(f"Pace companion sending to {args.host}. Create {PAUSE_FILE.name} to pause.")
    while True:
        try:
            send(args.host, token)
        except Exception as exc:
            print(f"send failed: {exc}")
        time.sleep(5)


if __name__ == "__main__":
    main()
