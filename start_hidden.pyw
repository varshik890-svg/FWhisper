"""Start F Whisper with no window (used for auto-start at Windows login).

Run by pythonw.exe from the HKCU "Run" registry key (same mechanism Handy uses).
Output goes to logs/fwhisper.log. Stop it with stop.bat (frees the VRAM).
Does nothing if F Whisper is already running (e.g. start.bat is open).
"""
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
log = open(os.path.join(ROOT, "logs", "fwhisper.log"), "w", encoding="utf-8", buffering=1)
sys.stdout = sys.stderr = log


def already_running() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8790/health", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


if already_running():
    print("F Whisper is already running - nothing to do.")
    sys.exit(0)

from fwhisper import run  # noqa: E402

sys.argv = [sys.argv[0]]
for attempt in range(1, 4):  # right after login the GPU driver may not be ready yet
    try:
        print(time.strftime("%Y-%m-%d %H:%M:%S"), f"starting (attempt {attempt})")
        run.main()
        break
    except Exception as e:
        print("ERROR:", type(e).__name__, e)
        time.sleep(15)
