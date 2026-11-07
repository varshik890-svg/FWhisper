"""Run F Whisper only while Handy is running (auto-started at login via the HKCU "Run" key).

Every few seconds: Handy running and F Whisper not -> start it (start_hidden.pyw).
Handy gone -> stop it (frees the VRAM). If F Whisper is stopped while Handy runs
(stop.bat), it stays off until Handy is closed and opened again.
Log: logs/handy_link.log.    Test: pythonw handy_link.pyw --watch notepad.exe
"""
import ctypes
import os
import subprocess
import sys
import time
import urllib.request
from ctypes import wintypes

ROOT = os.path.dirname(os.path.abspath(__file__))
WATCH = sys.argv[sys.argv.index("--watch") + 1].lower() if "--watch" in sys.argv else "handy.exe"
POLL_S = 3

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateMutexW.restype = wintypes.HANDLE
k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE

# one watcher per login session (checked before touching the log)
_mutex = k32.CreateMutexW(None, False, "Local\\FWhisperHandyLink-" + WATCH)
if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
    sys.exit(0)

os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
_log = open(os.path.join(ROOT, "logs", "handy_link.log"), "w", encoding="utf-8", buffering=1)
sys.stdout = sys.stderr = _log


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg)


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


def process_running(name: str) -> bool:
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if snap in (None, wintypes.HANDLE(-1).value):
        return False
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            if e.szExeFile.lower() == name:
                return True
            ok = k32.Process32NextW(snap, ctypes.byref(e))
        return False
    finally:
        k32.CloseHandle(snap)


def fwhisper_up() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8790/health", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


def main() -> None:
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    child, held = None, False  # held: user stopped F Whisper; wait for Handy to restart
    log(f"watching {WATCH}")
    while True:
        if process_running(WATCH):
            if child is not None and child.poll() is not None:
                log("F Whisper was stopped (stop.bat?) - staying off until Handy restarts")
                child, held = None, True
            elif child is None and not held and not fwhisper_up():
                log(f"{WATCH} running -> starting F Whisper")
                child = subprocess.Popen([pythonw, os.path.join(ROOT, "start_hidden.pyw")], cwd=ROOT)
        else:
            if held:
                log(f"{WATCH} closed -> will start F Whisper next time it opens")
            held = False
            if child is not None:
                if child.poll() is None:
                    log(f"{WATCH} closed -> stopping F Whisper (frees VRAM)")
                    child.terminate()  # its job object also kills llama-server
                    child.wait()
                child = None
        time.sleep(POLL_S)


try:
    main()
except Exception as e:  # never die silently
    log(f"ERROR: {type(e).__name__}: {e}")
    raise
