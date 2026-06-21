# Requirements & supported versions

Spaghetti Architect is three layers. They share one Python baseline but differ in
what else they touch (toolchains, network). This file is the single, honest
statement of what each needs.

| Layer | Path | Python | Third-party Python | Other |
|-------|------|--------|--------------------|-------|
| **Engine** (transpiler) | `src/`, `config/`, `examples/`, `tests/` | **3.12+** | **none** (stdlib only) | optional toolchains to *validate* non-Python targets |
| **Evaluation harness** | `eval/` | **3.12+** | **none** (stdlib only) | same optional toolchains |
| **Benchmark generator** | `bench/` | **3.12+** | **none in the core** (stdlib only) | **network + an LLM API key**; optional non-LLM anchor tools (`radon`) are quarantined |

## Why Python 3.12+

The whole project targets **CPython 3.12+**, developed and tested on 3.14.

* **`eval/` & `bench/`** measure *executed work* with
  [`sys.monitoring`](https://docs.python.org/3/library/sys.monitoring.html)
  (PEP 669), which only exists on **CPython 3.12+**. On older interpreters the
  per-instruction op-count (`eval.metrics.count_work`) cannot fire.

* **The engine** is pure standard library (no third-party imports, no
  `pip install` required) and holds itself to the same 3.12+ baseline so the
  whole repository runs and is tested on one interpreter version — that is what
  CI exercises (3.12 and 3.13).

## Optional editable install

The engine runs straight from a clone (`python3 -m src.main …`) with no install
step. For a console command, an **editable** install is supported:

```bash
pip install -e .        # adds the `spaghetti-architect` entry point
spaghetti-architect examples/analytics.json --profile max --source
```

It pulls in **no runtime dependencies** (`requirements.txt` is intentionally
empty). The editable install keeps `config/` and `examples/` in the source tree,
where the engine resolves them.

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
