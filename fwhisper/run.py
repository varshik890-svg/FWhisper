"""Start llama-server (model kept warm on the GPU) and then the cleanup server.

    python -m fwhisper.run              # uses llm.model_path from config.json
    python -m fwhisper.run --model X    # override model file
"""
from __future__ import annotations

import argparse
import atexit
import os
import subprocess
import sys
import time
import urllib.request

from .cleaner import ROOT, load_config, resolve


def llama_args(cfg: dict, model_path: str) -> list[str]:
    llm = cfg["llm"]
    return [
        resolve(llm["server_exe"]),
        "-m", resolve(model_path),
        "--host", "127.0.0.1",
        "--port", str(llm["port"]),
        "-ngl", str(llm["gpu_layers"]),
        "-c", str(llm["ctx_size"]),
        "-np", "1",            # one slot: the system prompt always stays cached
        "-fa", "on",           # flash attention
        "--jinja",
        "--no-webui",
        *llm.get("extra_args", []),
    ]


def _kill_with_parent(proc: subprocess.Popen) -> None:
    """Windows: put the child in a Job Object that is killed when this process dies,
    so closing the console window never leaves llama-server holding VRAM."""
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.OpenProcess.restype = wintypes.HANDLE

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

    class EXT(ctypes.Structure):
        _fields_ = [("Basic", BASIC), ("Io", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    job = k32.CreateJobObjectW(None, None)
    info = EXT()
    info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
    h = k32.OpenProcess(0x0101, False, proc.pid)  # PROCESS_SET_QUOTA | PROCESS_TERMINATE
    k32.AssignProcessToJobObject(job, h)
    _kill_with_parent.jobs.append(job)  # keep the handle open for our lifetime


_kill_with_parent.jobs = []


def start_llama(cfg: dict, model_path: str, log_name: str = "llama-server.log") -> subprocess.Popen:
    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    log = open(os.path.join(ROOT, "logs", log_name), "w", encoding="utf-8")
    proc = subprocess.Popen(
        llama_args(cfg, model_path), stdout=log, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    _kill_with_parent(proc)
    url = f"http://127.0.0.1:{cfg['llm']['port']}/health"
    deadline = time.time() + 180
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"llama-server exited (code {proc.returncode}); see logs/{log_name}")
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status == 200:
                    return proc
        except Exception:
            pass
        time.sleep(0.25)
    proc.kill()
    raise RuntimeError("llama-server did not become ready within 180 s")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help="GGUF path (default: llm.model_path in config.json)")
    args = ap.parse_args()
    cfg = load_config()
    model = args.model or cfg["llm"]["model_path"]
    print(f"Loading {os.path.basename(model)} on GPU...", flush=True)
    proc = start_llama(cfg, model)
    atexit.register(proc.kill)
    from . import server  # imported after llama-server is up so warmup works
    server.main()


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        print("ERROR:", e, file=sys.stderr)
        sys.exit(1)
