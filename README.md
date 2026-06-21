# 🍝 Spaghetti Architect

> An **anti-optimization transpiler**: feed it clean logic, get back deliberately
> redundant, fully flattened — yet syntactically correct and **crash-free** —
> "spaghetti code" in **Python, JavaScript, Go, Java and C++**.

It reads a language-agnostic Intermediate Representation (IR, JSON), runs it
through an anti-optimization compiler pipeline, and emits one program per target
language — then *actually compiles and runs each one* and checks the results
against a reference oracle.

Built for **code-obfuscation teaching, anti-pattern demonstration, and
technical-debt engineering research**. Zero third-party dependencies.

---

## Show me

**You write this IR** (`examples/membership_check.json`):

```json
{
  "module_name": "membership",
  "inputs": { "data_list": [10, 20, 30, 40], "search_val": 30 },
  "operations": [
    { "operation": "MEMBERSHIP_CHECK", "collection_name": "data_list",
      "target_var": "search_val", "result_var": "is_found" }
  ]
}
```

That is just `is_found = search_val in data_list`. **Spaghetti Architect emits this**
(Python, `--profile max`) — and it still computes the exact same answer:

```python
# MEMBERSHIP_CHECK: is_found = search_val in data_list
is_found = False
try:
    if data_list is not None:
        # SPAGH_001/006/008: manual index loop instead of `in`
        _idx = 0
        # SPAGH_010: recompute len() every iteration (de-hoisted)
        _match_flag = False
        while _idx < len(data_list):
            _current = data_list[_idx]
            # SPAGH_009: opaque predicate (always true: n*(n+1) is even)
            if (_idx * (_idx + 1)) % 2 == 0:
                if search_val == _current:
                    _match_flag = True
                else:
                    _match_flag = _match_flag
            _idx = _idx + 1
        if _match_flag == True:
            is_found = True
        else:
            is_found = False
    else:
        is_found = False
except Exception:
    is_found = False
```

That single block folds in nine anti-patterns — manual indexing (001/006), a
redundant temporary (002), len() recomputed every iteration (010), an always-true
opaque predicate (009), a Yoda comparison `search_val == _current` (011), a no-op
`else` (004), boolean verbosity (003), verbose decomposition (008) — all wrapped in
always-on safety (try + null guard + fallback). The `# SPAGH_…` comments and the
structure are exactly what the generator emits; nothing is hand-edited.

…plus equivalent spaghetti in JavaScript (`switch`/`for`), Go (`defer`/`recover`
IIFE), Java (`.equals` chains), and C++ (**pointer arithmetic** `*(ptr + i)` with
full bounds-checking). Every target is compiled, run, and verified to agree with
the oracle:

```
┌─ case: combined ──────────────────── profile: max ─┐
│ python     PASS  is_found=True  out_val='localhost'│
│ javascript PASS  {"is_found":true,"out_val":"localhost"}
│ go         PASS  {"is_found":true,"out_val":"localhost"}
│ java       PASS  {"is_found":true,"out_val":"localhost"}
│ cpp        PASS  {"is_found":true,"out_val":"localhost"}
└────────────────────────────────────────────────────┘
```

(Any target whose toolchain is absent **SKIP**s instead — never a silent pass.)

---

## How it works

```
IR JSON ─▶ Parser ─▶ Planner ─▶ Generators (×5, +safety) ─▶ Validator ─▶ CLI panel
          validate   pick the    a CodeEmitter structurally  compile & run
          → IRProgram SPAGH_*     guarantees indentation;     each target,
                      transforms  safety injected via hooks   compare to oracle
```

- **Correctness by construction** — a `CodeEmitter` (indent stack + block context
  managers) makes valid syntax structural, not hand-managed.
- **Safety is always on** — try/catch + null guards + a pre-set fallback wrap every
  operation; that is the crash-free guarantee, independent of how much spaghetti
  the profile adds.
- **Data-driven anti-patterns** — the eleven `SPAGH_*` transforms and the strength
  `profiles` live in `config/anti_patterns_db.json`, not in code.
- **Deterministic** — same IR ⇒ byte-identical output, so golden snapshots work.

### The eleven anti-patterns (`SPAGH_001..011`)

| | Pattern | | Pattern |
|---|---|---|---|
| 001 | De-idiomatization / loop flattening | 007 | Defensive over-guarding |
| 002 | Redundant temporaries | 008 | Verbose decomposition |
| 003 | Boolean verbosity | 009 | Opaque predicates *(control)* |
| 004 | Dead code / no-op injection | 010 | Redundant recomputation *(de-hoisting)* |
| 005 | Cascading conditionals | 011 | Yoda conditions *(layout)* |
| 006 | Manual indexing / pointer arithmetic | | |

The last three (009–011) are grounded in the Collberg–Thomborson obfuscation
taxonomy and classic code smells. Composed via profiles: **`minimal`**
(de-idiomatize only) · **`standard`** · **`max`** (all eleven).

---

## Use as an agent skill

This repo ships a packaged **agent skill** so an AI agent can drive the engine
directly — author an IR for a user's logic, then emit **verifiable** spaghetti
that provably compiles and runs:

```
.claude/skills/spaghetti-architect/SKILL.md
```

A Claude Code agent auto-discovers it from the skill's `description` and invokes
it when asked for anti-pattern / obfuscation / technical-debt examples or
"messy-but-correct" sample code. The skill teaches the agent the IR schema and
this workflow: **author IR → `python3 -m src.main <ir.json> --profile max
--source` → read the per-language PASS/SKIP/FAIL panel as proof it builds.**
Because every target is compiled, run, and checked against the oracle, the agent
never has to guess whether its garbage code is correct — the validator answers.

---

## Requirements

- **Python 3.8+ to run the engine** — and nothing else: standard library only, no
  `pip install`, no virtualenv (developed on 3.14). The evaluation harness
  (`eval/`) and the benchmark generator (`bench/`) need **Python 3.12+** (they
  measure executed work with `sys.monitoring`, PEP 669), and `bench/`
  additionally needs network access plus an LLM API key. See
  [`REQUIREMENTS.md`](REQUIREMENTS.md) for the full per-layer breakdown.
- **Optional language toolchains**, used *only* to validate the non-Python targets:
  `node` (JavaScript), `go` (Go), `javac` + `java` (Java), `g++` (C++). Any that are
  absent simply **SKIP** — Python is always validated via `exec()`.

## Setup

No build or install step — clone the repo and run from its root:

```bash
git clone https://github.com/KurathSec/Spaghetti-Architect.git
cd Spaghetti-Architect
python3 -m src.main          # runs the bundled example suite
```

## Quickstart

```bash
# Run the bundled example suite — renders a per-language validation panel:
python3 -m src.main

# A single IR at a chosen strength, showing the generated code for every language:
python3 -m src.main examples/combined.json --profile max --source

# Just two languages:
python3 -m src.main examples/combined.json --lang python --lang cpp --source
```

Exit code is non-zero only if a language **FAILs** — a missing toolchain is
**SKIP**, not a failure.

## Validation & toolchains

The promise is that all five targets compile/run and agree with the oracle.
**Python is always validated** in-process via `exec()`. The other four are
compiled and run **only if their toolchain is present** (`node`, `go`,
`javac`+`java`, `g++`); otherwise they **SKIP** — surfaced prominently in the
panel, never silently treated as passing. Install the toolchains and re-run to
exercise all five.

## Test

```bash
python3 -m unittest discover -s tests -t .          # or: python3 -m pytest

# Refresh golden snapshots after an intended generator change:
UPDATE_GOLDEN=1 python3 -m unittest tests.test_golden
```

25 tests: golden snapshots, cross-language equivalency, parser negatives, and the
profile matrix.

## Project layout

```
.claude/skills/spaghetti-architect/SKILL.md   # agent-skill manifest (discovery + how to drive the engine)
config/anti_patterns_db.json   # the 11 SPAGH_* patterns + profiles (data-driven)
examples/*.json                # ready-to-run IR samples
src/emitter.py                 # CodeEmitter: the syntactic-correctness guarantee
src/engine.py · src/main.py    # orchestration + CLI panel
src/nodes/{parser,planner,safety,validator}.py
src/generators/{base,python,javascript,go,java,cpp}_gen.py
tests/                         # golden + equivalency + parser + profiles
```

## Supported operations

- `MEMBERSHIP_CHECK` — is a value present in a collection?
- `KEY_VALUE_LOOKUP` — resolve a key against a map, with a default fallback.

One IR may chain several operations. Out of scope: runtime performance, and
general control-flow transpilation.

## License

Released under the **MIT License** — see [`LICENSE`](LICENSE).
Copyright © 2026 Kurath.

---

📐 **Design deep-dive:** [`architecture.md`](architecture.md) documents the
architecture as built — the IR spec, the safety what/how split, the emitter, the
per-language generators, and the design trade-offs.

*No third-party dependencies — Python standard library only.*
