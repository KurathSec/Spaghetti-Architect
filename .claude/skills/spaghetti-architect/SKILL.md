---
name: spaghetti-architect
description: >-
  Generate deliberately redundant, fully-flattened, yet syntactically-correct
  and crash-free "spaghetti" code in Python, JavaScript, Go, Java, and C++ from
  a clean JSON IR. Every target is compiled, run, and checked against a
  reference oracle, so the bad code provably builds and computes the right
  answer. Use when asked to produce anti-pattern, obfuscation, or
  technical-debt teaching examples, "what bad code for X looks like" demos, or
  intentionally messy-but-correct sample / fixture code in those languages.
---

# Spaghetti Architect

An **anti-optimization transpiler**. You give it a clean, language-agnostic IR
(JSON) describing simple logic; it emits **semantically identical but
deliberately awful** code in five languages — and then **actually compiles and
runs each one** and checks the result against a reference oracle.

The point of this skill is that the garbage code is **verifiable**: it provably
builds and computes the correct answer. Do not hand-write spaghetti and hope it
compiles — author an IR, run it through the engine, and report the validation
panel as proof.

## When to use

- "Show what terrible / over-engineered code for *X* looks like."
- Teaching anti-patterns, code smells, or obfuscation (it embeds 11 named
  `SPAGH_*` transforms, three of them from the Collberg–Thomborson taxonomy).
- Generating intentionally messy-but-correct fixtures or before/after refactor
  exercises in Python, JavaScript, Go, Java, or C++.

If the user wants genuinely clean code, or logic outside the four supported
operations below, this skill does **not** apply.

## What it can express

Four composable operations. One IR may chain several of them.

- `MEMBERSHIP_CHECK` — `result = target in collection` (result is a bool).
- `KEY_VALUE_LOOKUP` — `result = map.get(key, default)` (a known-key cascade).
- `AGGREGATE` — `result = sum|min|max(collection)` over an **int** list (an int).
- `CONDITIONAL_SELECT` — `result = then_value if (subject <cmp> value) else else_value`,
  where `<cmp>` is one of `== != < <= > >=` and `subject`/`value` are **ints**.

Out of scope: arbitrary control flow, floating-point reduction, general
expressions, runtime performance. If the request can't be modelled as a sequence
of these four operations, say so rather than forcing it.

## Workflow (follow this order)

1. **Model the user's logic** as one or more of the four operations
   (`MEMBERSHIP_CHECK`, `KEY_VALUE_LOOKUP`, `AGGREGATE`, `CONDITIONAL_SELECT`).
2. **Author an IR JSON file** (schema below). Keep it in `examples/` or a temp
   path.
3. **Run the engine and validate** from the repository root (the directory that
   contains `src/` and `config/`; this skill ships inside that repo):

   ```bash
   python3 -m src.main <ir.json> --profile max --source
   ```

4. **Read the panel.** Each language reports `PASS` (compiled, ran, matched the
   oracle), `SKIP` (toolchain absent — not a failure), or `FAIL`. The process
   exit code is non-zero **only** on a `FAIL`.
5. **Report honestly.** Show the panel as evidence it builds; surface any `SKIP`
   (e.g. "Go skipped — `go` not installed") rather than implying all five ran.
   Never claim it builds without having run the validator.

### Useful invocations

```bash
python3 -m src.main                                   # run the bundled example suite
python3 -m src.main examples/combined.json --profile max --source
python3 -m src.main <ir.json> --lang python --lang cpp --source   # only some languages
```

Profiles: `minimal` (de-idiomatize only) · `light` · `standard` · `heavy` · `max` (all 11 patterns).
Use `max` unless the user wants a milder mess.

## IR schema (author these carefully — the parser rejects invalid IR)

```json
{
  "version": "1.0",
  "module_name": "demo",
  "inputs": {
    "data_list": [10, 20, 30, 40],
    "search_val": 30,
    "config_db": {"dev": "localhost", "prod": "10.0.0.1"},
    "input_key": "dev"
  },
  "operations": [
    { "operation": "MEMBERSHIP_CHECK",
      "collection_name": "data_list", "target_var": "search_val",
      "result_var": "is_found" },
    { "operation": "KEY_VALUE_LOOKUP",
      "map_name": "config_db", "key_var": "input_key",
      "result_var": "out_val",
      "pairs": {"dev": "localhost", "prod": "10.0.0.1"},
      "default_value": "127.0.0.1" }
  ]
}
```

**Rules the parser enforces** (break one and the IR is rejected):

- `version` must be `"1.0"`; `module_name` a valid identifier.
- Each `inputs` value is a **scalar**, a **homogeneous scalar array**, or a
  **`string → homogeneous-scalar` map**. (Scalars: string, int, float, bool,
  null.) Input names must be valid identifiers.
- `operations` is a **non-empty** array. Each operation reads from `inputs` and
  writes a **fresh** `result_var` (must be a new identifier — no collisions).
- `MEMBERSHIP_CHECK`: `collection_name` must point to an **array** input;
  `target_var` to a **scalar** input whose type **matches the array element
  type** (e.g. don't search a string in an int array).
- `KEY_VALUE_LOOKUP`: `map_name` must point to a **map** input; `key_var` to a
  **string** input. `pairs` is a non-empty `string → scalar` map and
  `default_value` a scalar **of the same value type** as `pairs`.
- `AGGREGATE`: `mode` is `sum`, `min`, or `max`; `collection_name` must point to a
  **non-empty int array**. The result is an int.
- `CONDITIONAL_SELECT`: `subject_var` and `compare_value` must be **ints**;
  `comparator` ∈ `== != < <= > >=`; `then_value` and `else_value` must be scalars
  of the **same type** (which becomes the result type).

**Correctness gotcha:** the result of a lookup is computed from **`pairs`**, not
from the `map_name` input (`pairs.get(key, default_value)` — the oracle and the
generated cascade both key off `pairs`). Make `pairs` hold the real key→value
mapping and `default_value` the fallback; mirror the same content into the
`map_name` input for realism. The operations are otherwise independent: each
reads its own inputs and produces a distinct result.

See `examples/membership_check.json`, `examples/key_value_lookup.json`,
`examples/combined.json`, `examples/aggregate.json`,
`examples/conditional_select.json`, and `examples/analytics.json` (all four
operations chained) for minimal authored IRs.

## Guarantees & limits

- **Correctness by construction** — valid syntax is structural, not
  hand-managed; safety (try/catch + null guards + a preset fallback) wraps every
  operation, so the output is crash-free regardless of profile strength.
- **Self-annotated by default** — every generated source carries a module header,
  a per-operation comment naming the clean form, and inline `SPAGH_*` markers.
  The CLI always emits this annotated form; a comment-free rendering of the same
  programs is available via the Python API (`Engine(db_path, profile, annotate=False)`)
  when a fixture must not disclose its own construction.
- **Deterministic** — same IR ⇒ byte-identical output (golden snapshots rely on
  this).
- **Python is always validated** in-process via `exec()`. JavaScript / Go /
  Java / C++ are compiled and run **only if their toolchain is present**
  (`node`, `go`, `javac`+`java`, `g++`); otherwise they `SKIP`.
- Zero-dependency core — Python 3.12+ standard library only.
