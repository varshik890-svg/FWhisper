"""OpenAI-compatible endpoint that Handy's "Custom" post-processing provider calls.

Handy sends the transcript (its prompt is just ${output}); we return the cleaned
text as a normal chat completion. Run:  python -m fwhisper.server
"""
from __future__ import annotations

import csv
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .cleaner import ROOT, Cleaner, load_config

CFG = load_config()
CLEANER = Cleaner(CFG)
LOG_PATH = os.path.join(ROOT, "logs", "timings.csv")
_log_lock = threading.Lock()


def log_timing(res, raw: str) -> None:
    if not CFG.get("log_timings", True):
        return
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    new = not os.path.exists(LOG_PATH)
    with _log_lock, open(LOG_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "words", "path", "llm_ms", "prompt_ms", "gen_ms", "out_tokens", "total_ms"])
        w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), len(Cleaner.words(raw)), res.path,
                    round(res.llm_ms), round(res.prompt_ms), round(res.gen_ms), res.out_tokens,
                    round(res.total_ms)])


def extract_transcript(body: dict) -> str:
    """Last user message. Strips Handy's <transcript> wrapper if the default prompt is still used."""
    msgs = [m for m in body.get("messages", []) if m.get("role") == "user"]
    if not msgs:
        return ""
    content = msgs[-1].get("content", "")
    if isinstance(content, list):  # OpenAI content-parts format
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    if "<transcript>" in content and "</transcript>" in content:
        content = content.split("<transcript>", 1)[1].split("</transcript>", 1)[0]
    return content.strip()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep the console quiet
        pass

    def _send(self, code: int, obj: dict) -> None:
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            self._send(200, {"object": "list", "data": [{"id": "fwhisper-clean", "object": "model"}]})
        elif self.path.rstrip("/") in ("", "/health"):
            self._send(200, {"status": "ok"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(400, {"error": "bad json"})
            return
        raw = extract_transcript(body)
        res = CLEANER.clean(raw)
        log_timing(res, raw)
        print(f"[{res.path}] {res.total_ms:.0f} ms ({len(Cleaner.words(raw))} words)", flush=True)
        self._send(200, {
            "id": "fw-" + str(int(time.time() * 1000)),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "fwhisper-clean",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": res.text}}],
        })


def main() -> None:
    host, port = CFG["cleanup_server"]["host"], int(CFG["cleanup_server"]["port"])
    print("Warming up LLM prompt cache...", flush=True)
    CLEANER.warmup()
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"F Whisper cleanup server on http://{host}:{port}/v1", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
