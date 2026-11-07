"""Benchmark candidate LLMs on the sample transcripts.

    python bench/benchmark.py                       # all candidate_models present on disk
    python bench/benchmark.py --models qwen3.5-2b   # just one
    python bench/benchmark.py --runs 5

For each model: starts llama-server exactly like production, warms the prompt
cache, runs every sample N times through the real cleanup pipeline (same
prompt, vocab, guardrails, timeout), and writes bench/results/<timestamp>.md
with per-stage timings, pass/fail checks, and every output for quality review.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as st
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fwhisper.cleaner import ROOT, Cleaner, load_config, resolve  # noqa: E402
from fwhisper.run import start_llama  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def check(sample: dict, out: str) -> list[str]:
    c, fails = sample.get("check", {}), []
    for s in c.get("present", []):
        if s not in out:
            fails.append(f"missing '{s}'".replace("\n", "\\n"))
    for s in c.get("absent", []):
        if s.lower() in out.lower():
            fails.append(f"contains '{s}'")
    if "max_ratio" in c:
        r = len(Cleaner.words(out)) / max(1, len(Cleaner.words(sample["text"])))
        if r > c["max_ratio"]:
            fails.append(f"length ratio {r:.2f}")
    return fails


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))] if xs else 0


def bench_model(cfg, model, samples, runs):
    path = resolve(model["file"])
    if not os.path.exists(path):
        print(f"skip {model['name']}: {path} not found")
        return None
    print(f"\n=== {model['name']} ({os.path.getsize(path) / 2**30:.2f} GB) ===", flush=True)
    t = time.perf_counter()
    proc = start_llama(cfg, path, log_name=f"bench-{model['name']}.log")
    load_s = time.perf_counter() - t
    try:
        cl = Cleaner(cfg)
        cl.warmup()
        cl.warmup()
        rows = []
        for s in samples:
            res_list = [cl.clean(s["text"]) for _ in range(runs)]
            r = res_list[-1]
            totals = [x.total_ms for x in res_list]
            tps = (r.out_tokens / (r.gen_ms / 1000)) if r.gen_ms else 0
            fails = check(s, r.text)
            if r.path.startswith("fallback"):
                fails.insert(0, r.path)
            rows.append({"id": s["id"], "words": len(Cleaner.words(s["text"])), "path": r.path,
                         "median_ms": st.median(totals), "max_ms": max(totals),
                         "prompt_ms": r.prompt_ms, "gen_ms": r.gen_ms, "tok": r.out_tokens,
                         "tps": tps, "fails": fails, "out": r.text, "raw_llm": r.raw_llm})
            print(f"  #{s['id']:>2} {r.path:<18} {st.median(totals):6.0f} ms  "
                  f"{'OK' if not fails else 'FAIL: ' + '; '.join(fails)}", flush=True)
        return {"name": model["name"], "size_gb": os.path.getsize(path) / 2**30,
                "load_s": load_s, "rows": rows}
    finally:
        proc.kill()
        proc.wait()


def report(results, runs) -> str:
    L = [f"# LLM benchmark - {time.strftime('%Y-%m-%d %H:%M')}", "",
         f"Runs per sample: {runs} (median reported). Times are cleanup-server stage only "
         "(Handy ASR + paste are measured separately).", "",
         "## Summary", "",
         "| Model | Size | Typical (#1-8) median | p90 | Long #10 | Gen tok/s | Checks passed | Fallbacks |",
         "|---|---|---|---|---|---|---|---|"]
    for r in results:
        typical = [x["median_ms"] for x in r["rows"] if x["id"] <= 8]
        long_ = next((x["median_ms"] for x in r["rows"] if x["id"] == 10), 0)
        tps = [x["tps"] for x in r["rows"] if x["tps"]]
        passed = sum(not x["fails"] for x in r["rows"])
        fb = sum(x["path"].startswith("fallback") for x in r["rows"])
        L.append(f"| {r['name']} | {r['size_gb']:.2f} GB | {st.median(typical):.0f} ms | "
                 f"{pct(typical, 90):.0f} ms | {long_:.0f} ms | {st.median(tps) if tps else 0:.0f} | "
                 f"{passed}/{len(r['rows'])} | {fb} |")
    for r in results:
        L += ["", f"## {r['name']}", "", "| # | Words | Path | Median ms | Prompt ms | Gen ms | Tokens | Checks |",
              "|---|---|---|---|---|---|---|---|"]
        for x in r["rows"]:
            L.append(f"| {x['id']} | {x['words']} | {x['path']} | {x['median_ms']:.0f} | {x['prompt_ms']:.0f} | "
                     f"{x['gen_ms']:.0f} | {x['tok']} | {'OK' if not x['fails'] else '; '.join(x['fails'])} |")
        L += ["", "### Outputs", ""]
        for x in r["rows"]:
            L += [f"**#{x['id']}** ({x['path']})", "```", x["out"], "```"]
            if x["path"].startswith("fallback") and x["raw_llm"]:
                L += ["LLM said (rejected):", "```", x["raw_llm"], "```"]
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="*", help="candidate names from config.json")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--samples", default=os.path.join(HERE, "samples.json"))
    ap.add_argument("--llama-args", default=None,
                    help='override llm.extra_args, e.g. "--spec-type ngram-simple"')
    ap.add_argument("--label", default="", help="suffix for model names in the report")
    a = ap.parse_args()
    cfg = load_config()
    if a.llama_args is not None:
        cfg["llm"]["extra_args"] = a.llama_args.split()
    with open(a.samples, encoding="utf-8") as f:
        samples = json.load(f)
    models = [m for m in cfg["candidate_models"] if not a.models or m["name"] in a.models]
    if a.label:
        models = [m | {"name": f"{m['name']} {a.label}"} for m in models]
    results = [r for m in models if (r := bench_model(cfg, m, samples, a.runs))]
    if not results:
        sys.exit("No models found on disk - run setup.bat first.")
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    out = os.path.join(HERE, "results", time.strftime("%Y%m%d-%H%M%S") + ".md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(report(results, a.runs))
    print(f"\nReport: {out}")


if __name__ == "__main__":
    main()
