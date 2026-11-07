"""Transcript cleanup pipeline: replacements -> short-utterance fast path -> LLM -> guardrails.

Standard library only. Always returns text within the time budget: if the LLM
errors, is empty, is too slow, or produces something suspicious, the raw
transcript (with vocabulary replacements applied) is returned instead.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import socket
import time
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_config(path: str | None = None) -> dict:
    with open(path or os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
        return json.load(f)


def resolve(p: str) -> str:
    p = os.path.expanduser(p)  # allows "~/..." paths in config.json
    return p if os.path.isabs(p) else os.path.join(ROOT, p)


@dataclass
class Result:
    text: str
    path: str                 # "empty" | "short" | "llm" | "fallback:<reason>"
    llm_ms: float = 0.0
    total_ms: float = 0.0
    out_tokens: int = 0
    prompt_ms: float = 0.0
    gen_ms: float = 0.0
    raw_llm: str = ""


class _FileCache:
    """Re-reads a file only when its mtime changes, so edits apply without a restart."""

    def __init__(self, path: str, parse):
        self.path, self.parse, self.mtime, self.value = path, parse, None, None

    def get(self):
        m = os.path.getmtime(self.path)
        if m != self.mtime:
            with open(self.path, encoding="utf-8") as f:
                self.value = self.parse(f.read())
            self.mtime = m
        return self.value


def _parse_vocab(txt: str) -> dict:
    data = json.loads(txt)
    terms = [t for t in data.get("terms", []) if t.strip()]
    rules = []
    for src, dst in data.get("replacements", {}).items():
        if src.startswith("_"):
            continue
        rules.append((re.compile(r"(?<!\w)" + re.escape(src) + r"(?!\w)", re.IGNORECASE), dst))
    # longest patterns first so "cloud code" wins over "cloud"
    rules.sort(key=lambda r: -len(r[0].pattern))
    return {"terms": terms, "rules": rules}


_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_PREAMBLE = re.compile(
    r"^\s*(?:sure[,!.]?\s*)?(?:here(?:'s| is) (?:the |your )?(?:cleaned|edited|corrected|revised)[^\n:]*:|"
    r"(?:cleaned|edited|corrected) (?:text|transcript|version)\s*:|output\s*:)\s*",
    re.IGNORECASE,
)
_WORD = re.compile(r"[A-Za-z0-9']+")
_QWORDS = ("what", "why", "how", "when", "where", "who", "which", "can", "could",
           "would", "should", "is", "are", "do", "does", "did", "will")
# If a dictation starts with one of these and the LLM output drops it, the LLM probably
# performed the command ("say X" -> "X") instead of cleaning it -> fall back.
_COMMAND_VERBS = {
    "say", "tell", "write", "translate", "summarize", "summarise", "repeat", "rewrite", "rephrase",
    "convert", "explain", "list", "give", "ignore", "answer", "spell", "fix", "create", "generate",
    "describe", "draft", "make", "show", "help", "find", "compose", "reply", "respond", "pretend",
    "act", "forget", "print", "output",
}
_LEAD_WORDS = {"um", "uh", "so", "okay", "ok", "like", "hey", "please", "can", "could", "would",
               "will", "you", "just", "now", "and"}
_TAGS = re.compile(r"</?transcript>", re.IGNORECASE)


class Cleaner:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.prompt = _FileCache(resolve(cfg["prompt_file"]), str.strip)
        self.vocab = _FileCache(resolve(cfg["vocab_file"]), _parse_vocab)
        self.llm_host = "127.0.0.1"
        self.llm_port = int(cfg["llm"]["port"])

    # ---------- helpers ----------
    def system_prompt(self) -> str:
        p = self.prompt.get()
        terms = self.vocab.get()["terms"]
        if terms:
            p += "\n\nAlways spell these names/terms exactly like this: " + ", ".join(terms) + "."
        return p

    def replace(self, text: str) -> str:
        for rx, dst in self.vocab.get()["rules"]:
            text = rx.sub(dst, text)
        return text

    @staticmethod
    def words(text: str) -> list[str]:
        return _WORD.findall(text)

    @staticmethod
    def basic_fix(text: str) -> str:
        t = text.strip()
        if not t:
            return t
        t = t[0].upper() + t[1:]
        if t[-1] not in ".?!:;\"')":
            first = t.split()[0].lower().strip(",")
            t += "?" if first in _QWORDS and len(t.split()) > 1 else "."
        return t

    def budget_ms(self, n_words: int) -> float:
        extra = max(0, n_words - 40) * float(self.cfg.get("timeout_extra_ms_per_word", 0))
        return float(self.cfg["timeout_ms"]) + extra

    def sanitize(self, out: str, raw: str) -> str:
        out = _TAGS.sub("", _THINK.sub("", out)).strip()
        out = _PREAMBLE.sub("", out, count=1).strip()
        if len(out) >= 2 and out[0] == out[-1] and out[0] in "\"'`" and raw.strip()[:1] not in "\"'`":
            out = out[1:-1].strip()
        return "\n".join(line.rstrip() for line in out.splitlines())  # no markdown "  " line breaks

    def suspicious(self, out: str, raw: str) -> str | None:
        n_in, n_out = len(self.words(raw)), len(self.words(out))
        if n_out == 0:
            return "empty"
        if n_out > n_in * 1.4 + 6:      # it answered / wrote something new
            return "too_long"
        if n_out < n_in * 0.35:         # it dropped content
            return "too_short"
        first = next((w for w in (x.lower() for x in self.words(raw)) if w not in _LEAD_WORDS), "")
        if first in _COMMAND_VERBS and first not in {w.lower() for w in self.words(out)}:
            return "dropped_command"    # it performed the command instead of cleaning it
        return None

    # ---------- LLM call ----------
    def call_llm(self, transcript: str, timeout_s: float) -> tuple[str, dict]:
        n_words = len(self.words(transcript))
        body = {
            "messages": [
                {"role": "system", "content": self.system_prompt()},
                # tags make it clear this is text to edit, not a request to the model
                {"role": "user", "content": f"<transcript>\n{transcript}\n</transcript>"},
            ],
            "temperature": 0,
            "top_k": 1,
            "max_tokens": int(n_words * 2.2) + 48,
            "cache_prompt": True,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        conn = http.client.HTTPConnection(self.llm_host, self.llm_port, timeout=timeout_s)
        try:
            conn.request("POST", "/v1/chat/completions", json.dumps(body),
                         {"Content-Type": "application/json"})
            resp = conn.getresponse()
            data = json.loads(resp.read())
        finally:
            conn.close()
        if resp.status != 200:
            raise RuntimeError(f"llama-server HTTP {resp.status}: {str(data)[:200]}")
        text = data["choices"][0]["message"].get("content") or ""
        return text, data.get("timings", {}) | {"usage": data.get("usage", {})}

    # ---------- main entry ----------
    def clean(self, raw: str) -> Result:
        t0 = time.perf_counter()
        raw = raw.strip()
        if not raw:
            return Result("", "empty")
        replaced = self.replace(raw)
        n_words = len(self.words(replaced))

        if n_words <= int(self.cfg.get("short_utterance_max_words", 0)):
            return Result(self.basic_fix(replaced), "short",
                          total_ms=(time.perf_counter() - t0) * 1000)

        budget_s = self.budget_ms(n_words) / 1000.0
        t1 = time.perf_counter()
        try:
            out, timings = self.call_llm(replaced, budget_s)
        except (socket.timeout, TimeoutError):
            return self._fallback(replaced, "timeout", t0, t1)
        except Exception as e:  # connection refused, bad JSON, HTTP error...
            return self._fallback(replaced, f"error:{type(e).__name__}", t0, t1)
        llm_ms = (time.perf_counter() - t1) * 1000

        cleaned = self.replace(self.sanitize(out, replaced))
        bad = self.suspicious(cleaned, replaced)
        if bad:
            r = self._fallback(replaced, bad, t0, t1)
            r.raw_llm = out
            return r
        return Result(cleaned, "llm", llm_ms=llm_ms,
                      total_ms=(time.perf_counter() - t0) * 1000,
                      out_tokens=timings.get("predicted_n", 0),
                      prompt_ms=timings.get("prompt_ms", 0.0),
                      gen_ms=timings.get("predicted_ms", 0.0), raw_llm=out)

    def _fallback(self, text: str, reason: str, t0: float, t1: float) -> Result:
        now = time.perf_counter()
        return Result(self.basic_fix(text), f"fallback:{reason}", llm_ms=(now - t1) * 1000,
                      total_ms=(now - t0) * 1000)

    def warmup(self) -> None:
        """Process the system prompt once so it sits in llama-server's prompt cache."""
        try:
            self.call_llm("this is a warm up sentence", 30)  # call_llm adds the tags
        except Exception:
            pass
