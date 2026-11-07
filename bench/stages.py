"""Per-stage latency of your REAL dictations: record stop -> ASR -> LLM cleanup -> paste.

    python bench/stages.py            # last 20 dictations
    python bench/stages.py --last 50

Reads Handy's log (%LOCALAPPDATA%/com.pais.handy/logs/handy.log) and F Whisper's
logs/timings.csv, and matches them by timestamp (within 3 s).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import re
import statistics as st

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HANDY_LOG = os.path.join(os.environ.get("LOCALAPPDATA", ""), "com.pais.handy", "logs", "handy.log")
FW_LOG = os.path.join(ROOT, "logs", "timings.csv")

TS = re.compile(r"^\[(\d{4}-\d{2}-\d{2})\]\[(\d{2}:\d{2}:\d{2})\]")
PATTERNS = {
    "audio": re.compile(r"recording stopped after ([\d.]+) s"),
    "stop_ms": re.compile(r"Recording stopped and samples retrieved in ([\d.]+)ms"),
    "asr_ms": re.compile(r"actions\]\[DEBUG\] Transcription completed in ([\d.]+)ms"),
    "paste_ms": re.compile(r"Text pasted successfully in ([\d.]+)ms"),
}


def parse_handy(path: str) -> list[dict]:
    runs, cur = [], {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = TS.match(line)
            if not m:
                continue
            for key, rx in PATTERNS.items():
                hit = rx.search(line)
                if hit:
                    if key == "audio":
                        # Handy logs in UTC; timings.csv uses local time -> convert to local
                        utc = dt.datetime.fromisoformat(f"{m[1]} {m[2]}").replace(tzinfo=dt.timezone.utc)
                        cur = {"time": utc.astimezone().replace(tzinfo=None)}
                    cur[key] = float(hit[1])
                    if key == "paste_ms" and "asr_ms" in cur:
                        runs.append(cur)
                        cur = {}
    return runs


def parse_fw(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [{"time": dt.datetime.fromisoformat(r["time"]), "llm_ms": float(r["total_ms"]),
                 "path": r["path"], "words": int(r["words"])} for r in csv.DictReader(f)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--last", type=int, default=20)
    a = ap.parse_args()
    if not os.path.exists(HANDY_LOG):
        raise SystemExit(f"Handy log not found: {HANDY_LOG}")
    handy, fw = parse_handy(HANDY_LOG)[-a.last:], parse_fw(FW_LOG)
    rows = []
    for h in handy:
        match = min(fw, key=lambda r: abs((r["time"] - h["time"]).total_seconds()), default=None)
        if match and abs((match["time"] - h["time"]).total_seconds()) <= 3:
            h |= {"llm_ms": match["llm_ms"], "path": match["path"], "words": match["words"]}
        else:
            h |= {"llm_ms": 0.0, "path": "no cleanup", "words": 0}
        h["total"] = h.get("stop_ms", 0) + h["asr_ms"] + h["llm_ms"] + h["paste_ms"]
        rows.append(h)

    print(f"{'time':<9} {'speech':>7} {'stop':>6} {'ASR':>6} {'cleanup':>8} {'paste':>6} {'TOTAL':>7}  path")
    for r in rows:
        print(f"{r['time']:%H:%M:%S} {r.get('audio', 0):6.1f}s {r.get('stop_ms', 0):5.0f}ms {r['asr_ms']:5.0f}ms "
              f"{r['llm_ms']:7.0f}ms {r['paste_ms']:5.0f}ms {r['total']:6.0f}ms  {r['path']}")
    if rows:
        print(f"\nmedian TOTAL release->pasted: {st.median(r['total'] for r in rows):.0f} ms over {len(rows)} dictations")


if __name__ == "__main__":
    main()
