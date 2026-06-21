# Contributing to Spaghetti Architect

Thanks for your interest! This is a small, dependency-free research tool; the bar
for changes is that they keep the **core promise** intact: every generated
program, in every target language, **compiles, runs, and matches the oracle**.

## Development setup

No build step. Clone and run from the repository root:

```bash
git clone https://github.com/KurathSec/Spaghetti-Architect.git
cd Spaghetti-Architect
python3 -m src.main                      # runs the bundled example suite
```

Optionally install the console command (editable, so `config/` and `examples/`
stay resolvable in the tree):

```bash
pip install -e .
spaghetti-architect examples/analytics.json --profile max --source
```

Requires **Python 3.12+**. Install `node`, `go`, `javac`+`java`, and `g++` to
validate all five targets locally; any missing toolchain **SKIP**s (never fails).

## Running the tests

```bash
python3 -m unittest discover -s tests -t .      # or: python3 -m pytest
```

The suite covers golden snapshots, cross-language run-equivalency, parser
negatives, and the profile matrix. After an **intended** generator change, refresh
the byte-exact snapshots:

```bash
UPDATE_GOLDEN=1 python3 -m unittest tests.test_golden
```

Review the resulting `git diff` of `tests/golden/` to confirm the change is what
you meant — unexpected drift there is the first sign of a regression.

## Coding constraints (please preserve these)

- **Standard library only** in `src/`, `eval/`, and `bench/` cores — no
  `pip install`, no SDKs.
- **Deterministic**: the same IR must produce byte-identical output (golden
  snapshots depend on it). No timestamps, no hash-ordering, no randomness.
- **Crash-free by construction**: every operation is wrapped in always-on safety
  (try/catch + guards + a pre-set fallback). Never remove the safety scope.
- **Correctness by construction**: never hand-manage whitespace or braces — emit
  through `CodeEmitter`.

## Adding a new operation (the extension contract)

Every operation flows through the same six touch-points. Use the existing
`AGGREGATE` / `CONDITIONAL_SELECT` as templates:

1. **`src/ir_models.py`** — add a frozen dataclass and extend the `Operation` union.
2. **`src/nodes/parser.py`** — add a branch in `_parse_op` that validates every
   field (so a bad IR can never reach a generator).
3. **`src/nodes/safety.py`** — add a `policy_for` branch (null guard? fallback kind?).
4. **`src/generators/base.py`** — add the dispatch case, `describe`, `result_specs`,
   `guard_target`, `fallback_value`, and an abstract `emit_*` hook.
5. **All five generators** — implement the `emit_*` hook in each (mind the
   per-language traps: Go forbids `else`/unused vars; Java picks the class JDK; C++
   bounds-checks pointers; float formatting differs, so prefer integer results).
6. **`src/nodes/validator.py`** — add the oracle branch (the ground truth).

Then add an `examples/*.json`, regenerate golden, and add parser tests. Run the
full suite; all five languages must PASS (or SKIP) on the new example.

## Pull requests

Keep PRs focused, include tests, and make sure `python3 -m unittest discover -s
tests -t .` is green. By contributing you agree your work is licensed under the
project's [MIT License](LICENSE).
