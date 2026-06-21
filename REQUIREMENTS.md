# Requirements & supported versions

Spaghetti Architect is three layers with **different** runtime requirements. The
engine is deliberately portable; the measurement layers trade that for newer
language features. This file is the single, honest statement of what each needs.

| Layer | Path | Python | Third-party Python | Other |
|-------|------|--------|--------------------|-------|
| **Engine** (transpiler) | `src/`, `config/`, `examples/`, `tests/` | **3.8+** | **none** (stdlib only) | optional toolchains to *validate* non-Python targets |
| **Evaluation harness** | `eval/` | **3.12+** | **none** (stdlib only) | same optional toolchains |
| **Benchmark generator** | `bench/` | **3.12+** | **none in the core** (stdlib only) | **network + an LLM API key**; optional non-LLM anchor tools (`radon`) are quarantined |

## Why the split

* **Engine — Python 3.8+, zero pip.** The transpiler (`src/`) is pure standard
  library and runs on CPython 3.8 and up. This is the portability promise the
  README and the agent skill make, and it is real: nothing in `src/` uses a
  feature newer than 3.8.

* **`eval/` & `bench/` — Python 3.12+.** Both measure *executed work* with
  [`sys.monitoring`](https://docs.python.org/3/library/sys.monitoring.html)
  (PEP 669), which only exists on **CPython 3.12+**. On older interpreters the
  per-instruction op-count (`eval.metrics.count_work`) cannot fire, so the harness
  as a whole requires 3.12+. Developed and tested on 3.14.

## Optional language toolchains (validation only)

The four non-Python backends are *compiled and run* only if their toolchain is
present; otherwise that language **SKIP**s (never a silent pass):

| Language | Tools |
|----------|-------|
| JavaScript | `node` |
| Go | `go` |
| Java | `javac` **and** `java` |
| C++ | `g++` |

Python is always validated in-process via `exec()` (for *trusted* engine output);
the benchmark runs *untrusted* model output in a subprocess (see below).

## `bench/` — network, key, and the one allowed dependency boundary

The benchmark core stays stdlib-only. Its single outside touch is reaching an LLM
API, implemented in `bench/models.py` with **stdlib `urllib.request`** — no SDK,
no `pip install`. It reads credentials from `bench/config.json` (gitignored) or
the environment variable it names. Without a resolved key the client is in
*placeholder state*: `--selftest`/`--dry-run` still work (mock model, zero spend),
`--batch` refuses.

The Phase-6 construct-validity anchor may use an external non-LLM complexity tool
(e.g. `radon`). It is **optional and quarantined** in a throwaway virtualenv so it
never enters the engine or the metric lanes.
