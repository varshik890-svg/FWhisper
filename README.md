# F Whisper — local Wispr-Flow-style cleanup for Handy

Handy records (Ctrl+Space) and transcribes with Parakeet on your GPU. F Whisper adds the
missing piece: a small local LLM that turns the raw transcript into clean, punctuated,
formatted text before Handy pastes it. Fully offline.

```
Hold Ctrl+Space → Handy records → Parakeet (GPU) → POST localhost:8790  ← F Whisper cleanup server
                                                       │ 1. vocabulary replacements ("cloud code" → "Claude Code")
                                                       │ 2. 1–3 words? quick fix-up, skip the LLM
                                                       │ 3. llama-server (CUDA, model kept warm, system prompt cached),
                                                       │    transcript sent inside <transcript> tags
                                                       │ 4. guardrails: strip preamble, reject answers/dropped text,
                                                       │    reject "say X" → "X" (performed the command)
                                                       │ 5. too slow / error / suspicious → raw transcript (capitalized + punctuated)
                 Handy pastes at cursor  ◄─────────────┘
```

## Setup (once)

1. Double-click **`setup.bat`**. It downloads llama.cpp (CUDA 13.5 build for RTX 50-series)
   and the models listed in `config.json`. Needs only Python 3.10+ (no pip packages).
2. In **File Explorer**, double-click **`start.bat`** (opening it in VS Code only shows the
   file). Leave the window open. Within a few seconds it prints
   `F Whisper cleanup server on http://127.0.0.1:8790/v1`. Closing the window stops everything
   and frees the VRAM.
   *(Since auto-start is on, you normally don't need this; see "Auto-start" below.)*
3. Configure Handy (one time):
   - **Advanced:** set **Model Unload Timeout** to **Never** (keeps Parakeet warm in VRAM).
   - **Advanced:** turn **Experimental** on, then in the new *Experimental* group turn
     **Post-processing** on. A **Post Process** tab appears in the sidebar.
   - **General:** change **Transcribe Shortcut** from Ctrl+Space to something else
     (e.g. Ctrl+Alt+Space) to free up Ctrl+Space.
   - **Post Process tab:**
     - Provider: **Custom**
     - Base URL: `http://127.0.0.1:8790/v1`
     - Model: `fwhisper-clean` (click refresh and pick it). API key: leave empty.
     - Prompt: add a new prompt whose text is exactly `${output}` and select it.
       (The real cleanup prompt lives in `prompts/clean.txt`.)
     - Post-process shortcut: set to **Ctrl+Space**.

## Auto-start: F Whisper follows Handy (on)
F Whisper runs **hidden, only while Handy is running**. At login the registry entry
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run\FWhisper` starts a tiny watcher,
`handy_link.pyw`, which checks every 3 s:
- **Handy opens** (at login or manually) → F Whisper starts (`start_hidden.pyw`), ready in ~5 s.
- **Handy closes** → F Whisper stops and frees its ~3 GB of VRAM.
- **`stop.bat`** (e.g. before gaming) → F Whisper stays off until Handy is closed and reopened.

Other ways to run it:
- **`start.bat`**: visible window with per-dictation timings (run `stop.bat` first if the
  hidden one is up; starting a second copy does nothing).
- **Logs:** `logs/handy_link.log` (watcher), `logs/fwhisper.log` (hidden server).
- **Turn auto-start off:**
  `reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v FWhisper /f`
  (or Task Manager → Startup apps → disable "pythonw").

## Usage
Hold **Ctrl+Space**, pause ~0.5 s (the mic takes that long to open), speak, release.
The cleaned text is pasted at your cursor.
The `start.bat` window prints each request: `[llm] 412 ms (23 words)` or `[fallback:timeout] ...`.
Every request is also logged to `logs/timings.csv`.

## Editing the prompt
Edit `prompts/clean.txt` and save. Changes apply on the next dictation (no restart).
Then run `python bench/fulltest.py` (with `start.bat` running) to check nothing regressed.
Changes to the Python code (`fwhisper/*.py`) need a `start.bat` restart.

## Custom vocabulary
Edit `vocab.json` and save (applies on the next dictation):
- `terms`: correct spellings of names/jargon/brands; the LLM is told to use them.
- `replacements`: exact find → replace rules, case-insensitive, whole words, applied
  before and after the LLM. Example: `"cloud code": "Claude Code"`.

## Config (`config.json`)
| Key | Meaning |
|---|---|
| `llm.model_path` | GGUF model to load |
| `llm.extra_args` | extra llama-server flags |
| `timeout_ms` | LLM time budget; after this the raw transcript is pasted |
| `timeout_extra_ms_per_word` | extra budget per word above 40 words (long dictations) |
| `short_utterance_max_words` | at or below this many words, skip the LLM |
| `candidate_models` | models `setup.bat` downloads and the benchmark compares |

## Swapping models
1. Add an entry to `candidate_models` in `config.json` (name, file, Hugging Face URL), or
   point `file` at a GGUF you already have (e.g. from LM Studio) with `"url": null`.
2. `setup.bat` downloads it. `python bench/benchmark.py --models <name>` measures it.
3. Set `llm.model_path` to the winner and restart `start.bat`.

## Benchmark
- `python bench/benchmark.py` runs every candidate model through the real pipeline on
  `bench/samples.json` and writes `bench/results/<timestamp>.md` (timings + every output).
  Try llama-server flags with `--llama-args "..." --label "[name]"`.
- `python bench/stages.py` shows per-stage timings of your **real** dictations
  (record stop → ASR → cleanup → paste) from Handy's log + `logs/timings.csv`.
- `python bench/fulltest.py [--verbose]` runs 49 quality cases (your real dictations, commands
  that must not be obeyed, fillers, lists, numbers, vocab) against the running server.
  Exit code 0 = all passed. Doesn't touch `logs/timings.csv`.
- Results so far: `bench/results/SUMMARY.md`.

## Optional speed tweak: Handy's paste delay
Handy waits 60 ms before and 60 ms after pressing Ctrl+V (~240 ms paste in total). In the
Handy window press **Ctrl+Shift+D** to show the **Debug** tab, and try lowering both paste
delays to 20 ms. If some app sometimes misses the paste, raise them again.
