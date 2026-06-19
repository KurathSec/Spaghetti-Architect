# Spaghetti Architect — Architecture

> This document describes the architecture of the **Spaghetti Architect** engine **as built**. It began
> life as an implementation blueprint (kept locally as `init.en.md` / `init.md`, untracked) and has been
> revised to match the shipped code: where the implementation refined or diverged from the original
> plan, the affected section carries an **As built** note.
> Audience: contributors and reviewers. For the user-facing quickstart, see [`README.md`](README.md).

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Design Principles](#2-design-principles)
3. [Improvements Over the Original Spec](#3-improvements-over-the-original-spec)
4. [Directory Structure](#4-directory-structure)
5. [IR Specification (Revised)](#5-ir-specification-revised)
6. [Overall Architecture & Data Flow](#6-overall-architecture--data-flow)
7. [Core Data Models `ir_models.py`](#7-core-data-models-ir_modelspy)
8. [`nodes/parser.py` — Validation & Parsing](#8-nodesparserpy--validation--parsing)
9. [`nodes/planner.py` — Anti-Optimization Planner (was optimizer)](#9-nodesplannerpy--anti-optimization-planner-was-optimizer)
10. [`config/anti_patterns_db.json` — The 11 Anti-Patterns](#10-configanti_patterns_dbjson--the-11-anti-patterns)
11. [`nodes/safety.py` — Safety Policy (language-agnostic)](#11-nodessafetypy--safety-policy-language-agnostic)
12. [`src/emitter.py` — Code Emitter (new, core)](#12-srcemitterpy--code-emitter-new-core)
13. [`generators/base.py` — Template Method + Safety Hooks](#13-generatorsbasepy--template-method--safety-hooks)
14. [Per-Language Generator Specs](#14-per-language-generator-specs)
15. [`nodes/validator.py` — Cross-Language Compile/Run Validation (new)](#15-nodesvalidatorpy--cross-language-compilerun-validation-new)
16. [`engine.py` — Orchestration](#16-enginepy--orchestration)
17. [`main.py` — CLI Panel + Validation](#17-mainpy--cli-panel--validation)
18. [Testing Strategy](#18-testing-strategy)
19. [Generated Output Examples](#19-generated-output-examples)
20. [Implementation Status](#20-implementation-status)
21. [Open Questions & Trade-offs](#21-open-questions--trade-offs)

---

## 1. Project Overview

Spaghetti Architect is a **transpiler**: it reads a language-agnostic Intermediate Representation (IR,
in JSON) describing "clean logic", runs it through an **anti-optimization compiler pipeline**, and emits
**deliberately redundant, fully flattened, yet 100% syntactically correct and crash-free** "spaghetti
code" in five target languages: **Python, JavaScript, Go, Java, and C++**.

Use cases: code-obfuscation teaching, anti-pattern demonstration, technical-debt engineering research.

**Supported scenarios:**

- `MEMBERSHIP_CHECK` — checks whether a target value exists within a collection.
- `KEY_VALUE_LOOKUP` — matches a dynamic key against a dictionary/map to retrieve a value, with a
  default fallback.

**Non-goals (explicitly out of scope):**

- Runtime performance. Inputs are tiny JSON; generator speed is irrelevant. Every "optimization" here
  targets **architecture, correctness guarantees, and maintainability**.
- General program-level transpilation (arbitrary control flow / expressions). Only composable sequences
  of the two scenarios above are supported.

---

## 2. Design Principles

1. **Correctness by construction, not by hand-care.**
   "100% syntactically correct and crash-free" is the core promise. Indentation/syntax cannot be
   guaranteed by hand-concatenating strings — a `CodeEmitter` must structurally enforce it (see §12).

2. **Safety is a generator responsibility, not post-hoc string wrapping.**
   The "what" of safety (the policy) is language-agnostic and lives in `safety.py`; the "how" (emitting
   try/except, guards) is language-specific and lives in each generator's hooks. **Never** post-process
   already-generated source strings to wrap/re-indent them.

3. **All five languages get validated, not just Python.**
   If the promise is that all five don't crash, then all five must be validated. Detect toolchains
   (`node`/`go`/`javac`/`g++`), SKIP gracefully when absent — but never pretend "correct".

4. **Zero third-party dependencies, but not handcuffed to four modules.**
   The original "only `json/abc/os/sys`" is too strict (not even enough for `subprocess`, needed to
   compile other languages). The real principle is **zero pip dependencies**. Any standard library is
   allowed: `json, abc, os, sys, dataclasses, typing, contextlib, subprocess, tempfile, shutil,
   textwrap, enum, argparse`. Therefore **Pydantic is not used**; IR models use `dataclasses`.

5. **Determinism.** The same IR always produces byte-for-byte identical output, to support golden-file
   regression testing. If random obfuscation is ever added, it must be `seed`-able.

6. **Data-driven anti-patterns.** The eleven `SPAGH_*` anti-patterns are **composable cross-cutting
   transforms**, described by `anti_patterns_db.json` and selected by the planner — not hard-coded
   one-to-one with scenarios.

---

## 3. Improvements Over the Original Spec

| # | Original spec | Problem | This blueprint's fix |
|---|---------------|---------|----------------------|
| 1 | `safety.py` as a separate stage that wraps already-generated code strings | Requires re-indenting / re-understanding each language's syntax; fragile and duplicates generator knowledge | Split safety into **policy (safety.py, language-agnostic) + emission hooks (inside generators)**; the pipeline no longer post-processes strings |
| 2 | Each generator hand-concatenates strings and manages indentation manually | Many paths (5×2×many patterns); indentation/syntax bugs nearly guaranteed | Add `src/emitter.py` with **CodeEmitter** (line buffer + indent stack + `block()` context manager), shared by all generators |
| 3 | Only `exec()`-validates Python | 1 of 5 targets checked; the other 4 unvalidated | Add `nodes/validator.py`: Python via `exec()`, JS/Go/Java/C++ compiled & run via `subprocess` after toolchain detection, all compared against a **reference oracle** |
| 4 | `ir_models.py` says "Pydantic or dataclasses" | Contradicts "only built-in modules" (Pydantic isn't built-in) | Standardize on `dataclasses` + hand-written validation in `parser.py` |
| 5 | IR expresses only one operation per input, and never says where input data comes from | `exec()` demo has no values for `data_list`/`config_db` | IR gains `inputs` (run fixtures) and `operations` (a list); see §5 |
| 6 | `optimizer.py` is misnamed and may be an `op→pattern_id` empty shell | Name is the opposite of "anti-optimize"; if real logic lives in generators, the stage adds no value | Rename to `nodes/planner.py`, doing **genuinely data-driven** anti-pattern selection & composition, producing a `TransformPlan` |
| 7 | `SPAGH_001..008` is eight, but only 2 scenarios | Suspicious ratio, looks like padding | Redefine the eight as **composable cross-cutting transforms** (flattening / redundant temporaries / dead code / cascading conditionals…), reused across scenarios; see §10 |
| 8 | No testing strategy defined | No regression safety | Add `tests/`: golden snapshots + a cross-language equivalency runner (§18) |

> **File-level summary of changes**: add `src/emitter.py`, `nodes/validator.py`, `examples/`, `tests/`;
> `optimizer.py` → `planner.py`; `safety.py` changes role from "string post-processor" to
> "language-agnostic policy provider".

---

## 4. Directory Structure

```text
Spaghetti-Architect/                   # repo root — also the project package container
│
├── README.md                      # user-facing quickstart
├── architecture.md                # this document
├── conftest.py                    # puts repo root on sys.path so bare `pytest` works
├── .gitignore                     # also ignores the untracked init.en.md / init.md sources
│
├── config/
│   └── anti_patterns_db.json      # SPAGH_001..011 metadata & profiles (data-driven)
│
├── examples/                      # IR samples you can feed the engine directly
│   ├── membership_check.json
│   ├── key_value_lookup.json
│   └── combined.json              # multi-op + inputs, for the run-equivalency demo
│
├── tests/
│   ├── __init__.py
│   ├── golden/                    # committed snapshots: <lang>/<case>.<ext> (5 langs × 3 cases)
│   │   └── python/ javascript/ go/ java/ cpp/
│   ├── test_golden.py             # snapshot regression
│   ├── test_equivalency.py        # cross-language run equivalency
│   ├── test_parser.py             # IR validation negatives
│   └── test_profiles.py           # profile matrix
│
└── src/                           # the `src` package — run via `python -m src.main`
    ├── __init__.py
    ├── ir_models.py               # frozen dataclasses: IRProgram / Operation / Pattern / plans
    ├── emitter.py                 # CodeEmitter: indent stack + block/raw_block/indented + open/close_brace
    ├── engine.py                  # orchestration: Parser → Planner → Generators(+safety) → Validator
    ├── main.py                    # entry: CLI panel + multi-language validation
    │
    ├── generators/
    │   ├── __init__.py            # REGISTRY: language → generator instance
    │   ├── base.py                # abstract base: template method + safety hooks
    │   ├── python_gen.py
    │   ├── javascript_gen.py      # ES5
    │   ├── go_gen.py
    │   ├── java_gen.py
    │   └── cpp_gen.py
    │
    └── nodes/
        ├── __init__.py
        ├── parser.py              # validate & parse input IR JSON → IRProgram
        ├── planner.py             # anti-pattern selection & composition → TransformPlan
        ├── safety.py              # language-agnostic safety policy (SafetyPolicy)
        └── validator.py           # cross-language compile/run validation + oracle comparison
```

---

## 5. IR Specification (Revised)

### 5.1 Top-level structure

```json
{
  "version": "1.0",
  "module_name": "demo",
  "inputs": {
    "data_list":  [10, 20, 30, 40],
    "search_val": 30,
    "config_db":  {"dev": "localhost", "prod": "10.0.0.1"},
    "input_key":  "dev"
  },
  "operations": [
    {
      "operation": "MEMBERSHIP_CHECK",
      "collection_name": "data_list",
      "target_var": "search_val",
      "result_var": "is_found"
    },
    {
      "operation": "KEY_VALUE_LOOKUP",
      "map_name": "config_db",
      "key_var": "input_key",
      "result_var": "out_val",
      "pairs": {"dev": "localhost", "prod": "10.0.0.1"},
      "default_value": "127.0.0.1"
    }
  ]
}
```

**Key changes (addressing improvement #5):**

- **`inputs`**: run fixtures. The generated code in every language first declares these variables and
  assigns these values, so that `exec()`/`node`/`go run`/`java`/`g++` have data to run with — that's
  what makes "demonstrate run equivalency" actually hold.
- **`operations`**: a **list** of operations, so one IR can execute several operations in sequence (truly
  "program"-like). The original single-operation spec is just the `len==1` special case.

### 5.2 Field constraints (enforced by parser)

Common:
- `version`: string, must be a supported version (currently `"1.0"`).
- `module_name`: valid identifier (`^[A-Za-z_][A-Za-z0-9_]*$`). Reused as Go package name / Java class
  name / C++ namespace.
- `inputs`: object; value types limited to JSON scalars, scalar arrays, and `string→scalar` objects
  (mapped to each language's list / map).
- `operations`: non-empty array.

`MEMBERSHIP_CHECK`:
- `collection_name`: must be an array present in `inputs`.
- `target_var`: must be a scalar present in `inputs`.
- `result_var`: valid identifier, must not collide with an existing variable.

`KEY_VALUE_LOOKUP`:
- `map_name`: must be an object present in `inputs`.
- `key_var`: a string scalar present in `inputs`.
- `pairs`: a non-empty `string→scalar` object (source of the cascade branches).
- `default_value`: a scalar.
- `result_var`: valid identifier.

> Anything that fails throws `IRValidationError` at the parser stage. **Never** let a bad IR flow to a
> generator (otherwise generated code might crash, violating the core promise).

> **As built** — the parser enforces more than the list above, to guarantee the statically-typed targets
> are well-formed: input *names* must be valid identifiers (they become variables in every language);
> arrays and map-values must be **homogeneous**; a `MEMBERSHIP_CHECK` `target_var` must share the
> collection's element type; a `KEY_VALUE_LOOKUP` `key_var` must be a string and `default_value` must
> share the `pairs` value type; `result_var` may collide with neither an input nor an earlier result.
> Operations reference **inputs** (not earlier results); the `declared` set exists to reject collisions.

---

## 6. Overall Architecture & Data Flow

```text
                ┌──────────────────────────────────────────────────────────────┐
   IR JSON ───▶ │  Parser        validate + parse → IRProgram (dataclasses)     │
                └──────────────────────────────────────────────────────────────┘
                                          │ IRProgram
                                          ▼
                ┌──────────────────────────────────────────────────────────────┐
                │  Planner       reads anti_patterns_db.json, selects composable │
                │  (anti-opt)    SPAGH_* transforms per operation → TransformPlan │
                └──────────────────────────────────────────────────────────────┘
                                          │ IRProgram + TransformPlan
                                          ▼
                ┌──────────────────────────────────────────────────────────────┐
                │  Generator     per language: CodeEmitter emits code; safety is │
                │  (×5, +safety) injected at generation time via hooks           │
                │                (reading SafetyPolicy)                          │
                └──────────────────────────────────────────────────────────────┘
                                          │ {lang: source_str}
                                          ▼
                ┌──────────────────────────────────────────────────────────────┐
                │  Validator     Python: exec(); others: detect toolchain →      │
                │                compile & run; all compared to reference oracle, │
                │                SKIP if toolchain missing                        │
                └──────────────────────────────────────────────────────────────┘
                                          │ {lang: ValidationResult}
                                          ▼
                                   main.py CLI panel
```

`engine.py` chains the four steps; `safety.py` provides policy and `validator.py` provides the oracle,
both called by the relevant stages.

---

## 7. Core Data Models `ir_models.py`

Pure `dataclasses`, immutable (`frozen=True`), zero third-party deps.

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Union, Dict, List

Scalar = Union[str, int, float, bool, None]

class IRValidationError(ValueError):
    """Raised by the parser when the IR is invalid."""

@dataclass(frozen=True)
class MembershipCheck:
    collection_name: str
    target_var: str
    result_var: str
    op: str = "MEMBERSHIP_CHECK"

@dataclass(frozen=True)
class KeyValueLookup:
    map_name: str
    key_var: str
    result_var: str
    pairs: Dict[str, Scalar]
    default_value: Scalar
    op: str = "KEY_VALUE_LOOKUP"

Operation = Union[MembershipCheck, KeyValueLookup]

@dataclass(frozen=True)
class IRProgram:
    version: str
    module_name: str
    inputs: Dict[str, object]          # scalar / scalar array / string→scalar map
    operations: List[Operation]
```

The planner's output:

```python
from enum import Enum

class Pattern(str, Enum):
    DEIDIOMATIZE      = "SPAGH_001"
    REDUNDANT_TEMPS   = "SPAGH_002"
    BOOLEAN_VERBOSITY = "SPAGH_003"
    DEAD_CODE         = "SPAGH_004"
    CASCADING_COND    = "SPAGH_005"
    MANUAL_INDEXING   = "SPAGH_006"
    OVER_GUARDING     = "SPAGH_007"
    VERBOSE_DECOMP    = "SPAGH_008"
    OPAQUE_PREDICATE  = "SPAGH_009"
    REDUNDANT_RECOMP  = "SPAGH_010"
    YODA_CONDITIONS   = "SPAGH_011"

@dataclass(frozen=True)
class OpPlan:
    operation: Operation
    patterns: frozenset[Pattern]       # anti-patterns enabled for this operation

@dataclass(frozen=True)
class TransformPlan:
    per_op: List[OpPlan]
```

---

## 8. `nodes/parser.py` — Validation & Parsing

Responsibility: validate the raw `dict` (from `json.load`) and construct an `IRProgram`. **All** field
constraints (§5.2) are enforced here.

```python
import re
from ..ir_models import (IRProgram, MembershipCheck, KeyValueLookup,
                         IRValidationError)

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SUPPORTED_VERSIONS = {"1.0"}

def parse(raw: dict) -> IRProgram:
    _require(isinstance(raw, dict), "IR root must be an object")
    version = raw.get("version", "1.0")
    _require(version in SUPPORTED_VERSIONS, f"unsupported version: {version}")

    module = raw.get("module_name", "generated")
    _require(bool(_IDENT.match(module)), f"illegal module_name: {module!r}")

    inputs = raw.get("inputs", {})
    _require(isinstance(inputs, dict), "inputs must be an object")
    _validate_inputs(inputs)

    ops_raw = raw.get("operations")
    _require(isinstance(ops_raw, list) and ops_raw, "operations must be a non-empty array")

    declared = set(inputs)                      # declared-variable set (grows with result_var)
    operations = []
    for i, op in enumerate(ops_raw):
        operations.append(_parse_op(op, i, inputs, declared))

    return IRProgram(version, module, inputs, operations)

def _parse_op(op, i, inputs, declared):
    _require(isinstance(op, dict), f"operations[{i}] must be an object")
    kind = op.get("operation")
    if kind == "MEMBERSHIP_CHECK":
        col, tgt, res = op["collection_name"], op["target_var"], op["result_var"]
        _require(isinstance(inputs.get(col), list), f"[{i}] collection_name must point to an array input")
        _require(tgt in inputs, f"[{i}] target_var not declared in inputs")
        _check_new_ident(res, declared, i)
        declared.add(res)
        return MembershipCheck(col, tgt, res)
    if kind == "KEY_VALUE_LOOKUP":
        m, key, res = op["map_name"], op["key_var"], op["result_var"]
        pairs, default = op["pairs"], op["default_value"]
        _require(isinstance(inputs.get(m), dict), f"[{i}] map_name must point to an object input")
        _require(key in inputs, f"[{i}] key_var not declared in inputs")
        _require(isinstance(pairs, dict) and pairs, f"[{i}] pairs must be a non-empty object")
        _check_new_ident(res, declared, i)
        declared.add(res)
        return KeyValueLookup(m, key, res, pairs, default)
    raise IRValidationError(f"operations[{i}] unknown operation: {kind!r}")

def _require(cond, msg):
    if not cond:
        raise IRValidationError(msg)
# _validate_inputs / _check_new_ident: implementation omitted, logic per §5.2
```

> Design note: the `declared` set lets later operations reference `result_var`s written by earlier
> operations — this is the key to supporting multi-operation sequences.

---

## 9. `nodes/planner.py` — Anti-Optimization Planner (was optimizer)

Responsibility: read `config/anti_patterns_db.json` and decide which `SPAGH_*` transforms to enable per
operation, producing a `TransformPlan`. This is **data-driven** (improvement #6): which patterns are
enabled, and which scenarios they apply to, all come from the DB — nothing hard-coded in code.

```python
import json, os
from ..ir_models import TransformPlan, OpPlan, Pattern

class Planner:
    def __init__(self, db_path: str, profile: str = "max"):
        with open(db_path, encoding="utf-8") as f:
            self._db = json.load(f)
        self._profile = profile          # strength level: see DB "profiles"

    def plan(self, program) -> TransformPlan:
        enabled = set(self._db["profiles"][self._profile])     # patterns on at this level
        per_op = []
        for op in program.operations:
            applicable = {
                Pattern(pid)
                for pid, meta in self._db["patterns"].items()
                if pid in enabled and op.op in meta["applies_to"]
            }
            per_op.append(OpPlan(op, frozenset(applicable)))
        return TransformPlan(per_op)
```

`profile` gives an "anti-optimization strength" level (e.g. `minimal` / `standard` / `max`), convenient
for testing and demos.

---

## 10. `config/anti_patterns_db.json` — The 11 Anti-Patterns

All eleven are **composable cross-cutting transforms**, reused across both scenarios (improvement #7).
`applies_to` declares which scenarios each affects; `profiles` declares which patterns each strength
level enables. `SPAGH_009..011` were added later, grounded in the Collberg–Thomborson obfuscation
taxonomy (opaque predicates) and classic code smells (Yoda conditions, loop-invariant de-hoisting);
they target `MEMBERSHIP_CHECK` and slot into the existing manual-index-loop path.

```json
{
  "schema_version": "1.0",
  "patterns": {
    "SPAGH_001": {
      "name": "De-idiomatization / Loop Flattening",
      "description": "Replace idiomatic constructs (in / range / map.get) with manual index loops or cascades.",
      "applies_to": ["MEMBERSHIP_CHECK", "KEY_VALUE_LOOKUP"],
      "category": "structural"
    },
    "SPAGH_002": {
      "name": "Redundant Temporaries",
      "description": "Introduce a single-use temporary variable for every sub-expression.",
      "applies_to": ["MEMBERSHIP_CHECK", "KEY_VALUE_LOOKUP"],
      "category": "verbosity"
    },
    "SPAGH_003": {
      "name": "Boolean Verbosity",
      "description": "Assign true/false explicitly via if/else; introduce redundant '== true' comparisons.",
      "applies_to": ["MEMBERSHIP_CHECK"],
      "category": "verbosity"
    },
    "SPAGH_004": {
      "name": "Dead Code / No-op Injection",
      "description": "Inject behavior-preserving no-op statements and unreachable branches.",
      "applies_to": ["MEMBERSHIP_CHECK", "KEY_VALUE_LOOKUP"],
      "category": "noise"
    },
    "SPAGH_005": {
      "name": "Cascading Conditionals",
      "description": "Expand a lookup into a long elif/switch/nested-if chain enumerating each known key.",
      "applies_to": ["KEY_VALUE_LOOKUP"],
      "category": "structural"
    },
    "SPAGH_006": {
      "name": "Manual Indexing / Pointer Arithmetic",
      "description": "Use *(ptr+i) / explicit slice indexing instead of iterators or [] semantics.",
      "applies_to": ["MEMBERSHIP_CHECK"],
      "category": "structural"
    },
    "SPAGH_007": {
      "name": "Defensive Over-Guarding",
      "description": "Wrap every access in redundant null/bounds/type checks.",
      "applies_to": ["MEMBERSHIP_CHECK", "KEY_VALUE_LOOKUP"],
      "category": "defensive"
    },
    "SPAGH_008": {
      "name": "Verbose Decomposition",
      "description": "Split a single expression into many statements: explicit accumulators, counters, flags.",
      "applies_to": ["MEMBERSHIP_CHECK", "KEY_VALUE_LOOKUP"],
      "category": "verbosity"
    },
    "SPAGH_009": {
      "name": "Opaque Predicates",
      "description": "Guard real code with an always-true predicate that looks data-dependent (n*(n+1) is even).",
      "applies_to": ["MEMBERSHIP_CHECK"],
      "category": "control"
    },
    "SPAGH_010": {
      "name": "Redundant Recomputation",
      "description": "Recompute the loop-invariant collection length on every iteration instead of caching it.",
      "applies_to": ["MEMBERSHIP_CHECK"],
      "category": "performance"
    },
    "SPAGH_011": {
      "name": "Yoda Conditions",
      "description": "Flip comparison operand order (constant == variable) in the match check.",
      "applies_to": ["MEMBERSHIP_CHECK"],
      "category": "layout"
    }
  },
  "profiles": {
    "minimal":  ["SPAGH_001"],
    "standard": ["SPAGH_001", "SPAGH_002", "SPAGH_005", "SPAGH_007"],
    "max":      ["SPAGH_001", "SPAGH_002", "SPAGH_003", "SPAGH_004",
                 "SPAGH_005", "SPAGH_006", "SPAGH_007", "SPAGH_008",
                 "SPAGH_009", "SPAGH_010", "SPAGH_011"]
  }
}
```

> Note: `SPAGH_006` (pointer arithmetic) has no "pointer" semantics in Python/JS/Go/Java, so the
> generator **downgrades** it to the closest expressible form in that language (e.g. explicit `arr[i]`
> indexing); only C++ truly emits `*(ptr+i)`. In other words, the planner decides "whether to enable",
> the generator decides "how to express it in this language" — exactly the what/how separation.

---

## 11. `nodes/safety.py` — Safety Policy (language-agnostic)

The redefined role (improvement #1): **it only describes "what protections are needed", and touches no
source strings.** Each generator's safety hook consumes it to emit language-specific guards.

```python
from dataclasses import dataclass
from ..ir_models import MembershipCheck, KeyValueLookup

@dataclass(frozen=True)
class SafetyPolicy:
    needs_null_guard: bool      # collection/map non-null check
    needs_bounds_guard: bool    # index bounds check
    wrap_in_try: bool           # overall try/except|catch
    fallback_expr_kind: str     # how result_var falls back on error / failed guard

def policy_for(op) -> SafetyPolicy:
    if isinstance(op, MembershipCheck):
        return SafetyPolicy(True, True, True, "false")
    if isinstance(op, KeyValueLookup):
        return SafetyPolicy(True, False, True, "default")
    raise TypeError(op)
```

How each language turns it into code:

| Policy | Python | JavaScript (ES5) | Go | Java | C++ |
|--------|--------|------------------|----|----|-----|
| null guard | `if x is not None:` | `if (x != null ...)` | `if x != nil` | `if (x != null)` | `if (p != nullptr)` |
| bounds guard | `idx < len(x)` | `i < x.length` | `idx < len(x)` | `i < x.length` | `idx < x_len && idx >= 0` |
| try wrap | `try/except Exception` | `try { } catch (e) {}` | `defer/recover` + error return | `try { } catch (Exception e)` | `try { } catch (...)` |
| fallback `false` | `result = False` | `result = false` | zero value `false` | `result = false` | `result = false` |
| fallback `default` | `result = <default>` | same | same | same | same |

> **As built** — try/catch + the null guard + the pre-set fallback are **always emitted**; they are the
> crash-free guarantee, *not* gated by any profile (so even `--profile minimal` is safe). The eleven
> `SPAGH_*` patterns layer extra spaghetti on top and are what profiles toggle. Guard *placement* is
> language-specific: in Python/JS/Java it lives in `safety_scope`; in **Go** the "try" is a
> `func(){ defer/recover }()` IIFE with **no `else`** (a pre-set default covers the negative branch —
> Go's automatic semicolon insertion forbids `}`-newline-`else`); in **C++** the bounds guard sits in the
> body (it depends on the pointer setup), so `safety_scope` there is just `try/catch`.

---

## 12. `src/emitter.py` — Code Emitter (new, core)

This is the key to turning "syntactically correct" from hand-care into a structural guarantee
(improvement #2). A language-agnostic line buffer + indent stack; block structure goes through the
`block()` context manager, which **auto-balances indentation and braces** on enter/exit.

```python
from contextlib import contextmanager

class CodeEmitter:
    def __init__(self, indent_unit: str = "    ",
                 brace_style: bool = False,
                 open_token: str = "{", close_token: str = "}",
                 header_suffix: str = ":"):
        self._lines: list[str] = []
        self._level = 0
        self._unit = indent_unit
        self._brace = brace_style          # True: C/JS/Go/Java/C++; False: Python
        self._open, self._close = open_token, close_token
        self._suffix = header_suffix       # the ":" for Python

    def line(self, text: str = "") -> "CodeEmitter":
        self._lines.append("" if text == "" else self._unit * self._level + text)
        return self

    def lines(self, *texts: str) -> "CodeEmitter":
        for t in texts:
            self.line(t)
        return self

    def comment(self, text: str) -> "CodeEmitter":
        prefix = "// " if self._brace else "# "
        return self.line(prefix + text)

    @contextmanager
    def block(self, header: str):
        """C-family: `header {` ... `}`; Python: `header:` ... (pure indentation)."""
        if self._brace:
            self.line(f"{header} {self._open}")
        else:
            self.line(f"{header}{self._suffix}")
        self._level += 1
        try:
            yield self
        finally:
            self._level -= 1
            if self._brace:
                self.line(self._close)

    @contextmanager
    def raw_block(self, open_line: str, close_line: str):
        """For non-if/for bare blocks, e.g. a C++ `{ ... }` scope or a try block."""
        self.line(open_line); self._level += 1
        try:
            yield self
        finally:
            self._level -= 1; self.line(close_line)

    def render(self) -> str:
        return "\n".join(self._lines) + "\n"
```

Usage example (emitting a Python while loop):

```python
e = CodeEmitter()                       # Python style (brace_style=False)
e.line("_idx = 0")
with e.block("while _idx < _n"):
    e.line("_current = data_list[_idx]")
    with e.block("if _current == search_val"):
        e.line("_match_flag = True")
    e.line("_idx = _idx + 1")
# Rendered output is naturally indented correctly, with no manual whitespace management
```

> **The over-abstraction red line**: the emitter only handles "lines + indentation + block balancing".
> It does **not** understand expressions and does no language semantics. Generators still write the text
> of each line — they just no longer worry about indentation and brace closing. This deliberately low
> abstraction avoids the "template hell" noted in §21.

> **As built** — three small helpers were added, all still "indentation + braces only" (no semantics):
> `indented()` bumps one level with no delimiter (for `case:`/`default:` bodies under a `switch`);
> `open_brace(header)` / `close_brace()` open and close a scope across *separate* calls, for file-spanning
> structures a single `with` cannot wrap (Java `class` + `main`, C++ `int main()`, Go `func main()`).
> `block()` and `raw_block()` remain the workhorses for ordinary nested blocks.

---

## 13. `generators/base.py` — Template Method + Safety Hooks

The template method defines the "algorithm skeleton" (identical for all languages) and leaves the
language-specific parts as abstract hooks (the "how" side of improvement #1 + cutting duplication).

```python
from abc import ABC, abstractmethod
from contextlib import contextmanager
from ..emitter import CodeEmitter
from ..ir_models import MembershipCheck, KeyValueLookup
from ..nodes.safety import policy_for, SafetyPolicy

class BaseGenerator(ABC):
    language: str          # "python" / "javascript" / ...
    extension: str         # ".py" / ".js" / ...

    # ---- Template method: the skeleton, do not override in subclasses ----
    def generate(self, program, plan) -> str:
        e = self.new_emitter()
        self.emit_file_prologue(e, program)
        self.emit_inputs(e, program.inputs)
        for op_plan in plan.per_op:
            e.line()
            self.emit_operation(e, op_plan)
        self.emit_file_epilogue(e, program)
        return e.render()

    def emit_operation(self, e, op_plan):
        op = op_plan.operation
        pol = policy_for(op)
        e.comment(self.describe(op))
        self.declare_result_default(e, op, pol)         # give result a safe default first
        with self.safety_scope(e, op, pol):             # try + guard (language-specific hook)
            if isinstance(op, MembershipCheck):
                self.emit_membership(e, op, op_plan.patterns, pol)
            elif isinstance(op, KeyValueLookup):
                self.emit_lookup(e, op, op_plan.patterns, pol)

    # ---- Abstract hooks: implemented per language ----
    @abstractmethod
    def new_emitter(self) -> CodeEmitter: ...
    @abstractmethod
    def emit_file_prologue(self, e, program): ...
    @abstractmethod
    def emit_file_epilogue(self, e, program): ...
    @abstractmethod
    def emit_inputs(self, e, inputs): ...
    @abstractmethod
    def declare_result_default(self, e, op, pol): ...
    @abstractmethod
    def safety_scope(self, e, op, pol): ...          # must be a @contextmanager
    @abstractmethod
    def emit_membership(self, e, op, patterns, pol): ...
    @abstractmethod
    def emit_lookup(self, e, op, patterns, pol): ...

    # ---- Shared helpers (language-agnostic) ----
    def describe(self, op) -> str:
        if isinstance(op, MembershipCheck):
            return f"MEMBERSHIP_CHECK: {op.result_var} = {op.target_var} in {op.collection_name}"
        return f"KEY_VALUE_LOOKUP: {op.result_var} = {op.map_name}[{op.key_var}] or {op.default_value!r}"
```

`generators/__init__.py` maintains the registry:

```python
from .python_gen import PythonGenerator
# ... import the rest
REGISTRY = {g.language: g for g in (
    PythonGenerator(), JavaScriptGenerator(), GoGenerator(),
    JavaGenerator(), CppGenerator(),
)}
```

> **As built** — `generate()` also stashes `program.inputs` on the instance so body hooks can resolve an
> operation's element/value *types* by name (operations carry names, not values). The base adds shared
> helpers `result_specs()`, `fallback_value()`, `collection_tag()` and input-kind classifiers.
> `safety_scope` is the only hook that is a `@contextmanager`, and per §11 the try-wrap is always present.

---

## 14. Per-Language Generator Specs

Each generator only implements the §13 hooks. Below are each language's anti-pattern forms, safety
forms, and literal-mapping notes.

### 14.1 `python_gen.py`
- emitter: `brace_style=False` (pure indentation).
- anti-patterns: `while` + manual indexing for membership; `if/elif/else` cascade for lookup.
- safety: `try/except Exception`; `if x is not None`; fallback assigns `False`/`default` directly.
- literals: Python `repr()` safely outputs scalars/lists/dicts.

### 14.2 `javascript_gen.py` (ES5)
- emitter: `brace_style=True`.
- anti-patterns: `for (_idx = 0; _idx < _n; _idx++)` for membership (idiomatic fallback uses `indexOf`);
  `switch`-case for lookup.
- safety: `x !== null && x !== undefined` guard + `try { } catch (e) { }`; `var` (ES5, not `let/const`).
- literals: `JSON.stringify` is a syntactic subset of JS literals, so `lit()` is simply `json.dumps`.
- entry: `console.log(JSON.stringify({…}))` at end of file for the validator to parse.

### 14.3 `go_gen.py`
- emitter: `brace_style=True`; package `main`, single `import "fmt"` (always used by the JSON print).
- anti-patterns: de-idiomatized → manual index loop (`for _idx < _n { … _idx = _idx + 1 }`, no `range`);
  idiomatic fallback uses `range`. Lookup is a `switch` (no `break`/fallthrough needed).
- safety: `if s != nil` guard inside a `func(){ defer func(){ recover() }() … }()` IIFE that simulates
  try/catch; **no `else`** anywhere (a pre-set default covers the negative branch — see §11 As built).
- entry: one JSON line via `fmt.Println(fmt.Sprintf("{…}", …))` (`%v` for bools, `%q` for strings).
- **As built** — the original "managed struct (`opResult`)" idea was dropped as unnecessary: the result
  variable lives in `main`'s scope and the IIFE closes over it directly. To satisfy Go's
  unused-symbol-is-a-compile-error rule (§21.2), every input is touched with `_ = name`.

### 14.4 `java_gen.py`
- emitter: `brace_style=True`; class name is `module_name` capitalized (the temp file is `<Class>.java`).
- anti-patterns: index / enhanced-for loop over the raw array for membership; nested `if` chain for
  lookup. Strings compare with `.equals`, primitives with `==` (an `_eq` helper picks per element type).
- safety: `x != null` guard + `try { } catch (Exception e) { }`.
- structure: `public class <Name> { public static void main(String[] args) { … } static String _q(…) }`;
  maps are built as `HashMap` with `put`s; scalars boxed in `Map<String, Integer|…>`.
- entry: `System.out.println("{…}")`, strings escaped by the emitted `_q` helper.

### 14.5 `cpp_gen.py`
- emitter: `brace_style=True`.
- anti-patterns: **pointer arithmetic** `*(list_ptr + i)` for membership (`SPAGH_006` truly lands here;
  idiomatic fallback uses `std::find`). Lookup is a nested `if` chain (`std::string == "literal"`).
- safety: bounds validation to prevent SegFaults (`if (list_ptr != nullptr && len >= 0)`, loop bounded by
  `len`); `try { } catch (...) { }`. Lookup needs no null guard (a `std::map` is never null), so the map
  fixture is touched with `(void)map;`.
- structure: `#include <bits/stdc++.h>`; a file-scope `_q` escaper; `int main()` with
  `std::cout << std::boolalpha`; collections are `std::vector` / `std::map`, element access via a pointer
  from `&vec[0]` + offset. (No namespace — names are written `std::`-qualified.)
- entry: `std::cout << "{…}" << std::endl;`.

> Literal emission across the five languages is centralized in each generator's private `lit(value)`
> method, ensuring `inputs` scalars/arrays/maps are correctly and safely turned into that language's
> literals (escaping, boolean case `true/True`, null/nil/nullptr, etc.).

---

## 15. `nodes/validator.py` — Cross-Language Compile/Run Validation (new)

Delivers the "all five languages don't crash" promise (improvement #3). Core: a **reference oracle**
(compute each `result_var`'s expected value in clean Python) + actually **running** each language and
comparing stdout.

```python
import json, os, shutil, subprocess, tempfile
from dataclasses import dataclass

@dataclass
class ValidationResult:
    language: str
    status: str          # "PASS" | "FAIL" | "SKIP"
    detail: str

# 1) oracle: clean implementation computing expected results
def oracle(program) -> dict:
    env = dict(program.inputs)
    out = {}
    for op in program.operations:
        if op.op == "MEMBERSHIP_CHECK":
            out[op.result_var] = env[op.target_var] in env[op.collection_name]
        else:  # KEY_VALUE_LOOKUP
            out[op.result_var] = env[op.map_name].get(env[op.key_var], op.default_value)
        env[op.result_var] = out[op.result_var]
    return out

# 2) per language: if toolchain exists, compile & run; generated code prints result_vars as JSON
TOOLCHAINS = {
    "python":     None,                       # built-in exec, see below
    "javascript": ["node"],
    "go":         ["go"],
    "java":       ["javac", "java"],
    "cpp":        ["g++"],
}

def validate(language, source, program) -> ValidationResult:
    expected = oracle(program)
    if language == "python":
        return _validate_python(source, expected)
    tools = TOOLCHAINS[language]
    missing = [t for t in tools if shutil.which(t) is None]
    if missing:
        return ValidationResult(language, "SKIP", f"missing toolchain: {', '.join(missing)}")
    try:
        actual = _compile_and_run(language, source)   # returns parsed dict
    except Exception as ex:
        return ValidationResult(language, "FAIL", f"compile/run failed: {ex}")
    ok = _equal(actual, expected)
    return ValidationResult(language, "PASS" if ok else "FAIL",
                            "results match" if ok else f"expected {expected} got {actual}")

def _validate_python(source, expected) -> ValidationResult:
    ns = {}
    try:
        exec(compile(source, "<spaghetti>", "exec"), ns, ns)   # controlled namespace
    except Exception as ex:
        return ValidationResult("python", "FAIL", f"exec raised: {ex}")
    actual = {k: ns.get(k) for k in expected}
    ok = _equal(actual, expected)
    return ValidationResult("python", "PASS" if ok else "FAIL",
                            "results match" if ok else f"expected {expected} got {actual}")
```

The `_compile_and_run` contract (one per language): write a temp file → compile → run → generated code
prints **result_vars as JSON** at the end → validator `json.loads(stdout)` → compare to oracle. For
example:

- JS: `node tmp.js`, code ends with `console.log(JSON.stringify({is_found: is_found, ...}))`
- Go: `go run` (temp module), `fmt.Println` one JSON line
- Java: `javac` + `java`, `System.out.println` one JSON line
- C++: `g++ -O0 -o bin tmp.cpp` + run, `std::cout` one JSON line

> This way "equivalency" is a **real comparison of all five languages against the same oracle**, not
> "Python ran, good enough". When toolchains are missing in CI, SKIP (and flag it prominently in the
> panel) — neither blocking nor lying.

> **As built** — two refinements. (1) The oracle resolves `KEY_VALUE_LOOKUP` against the operation's own
> `pairs` (`op.pairs.get(key, default)`) rather than the `map_name` fixture, because `pairs` is exactly
> what every generator's cascade enumerates — so equivalency tests *generation fidelity*, not whether the
> author kept `pairs` and the map in sync. (2) The Go runner writes a throwaway `go.mod` and runs
> `go run .`; C++ compiles with `g++ -O0 -std=c++17`; Python is validated in-process via `exec()` into a
> controlled namespace (no subprocess). Comparison is type-aware (`json.dumps` per value, so `true` ≠ `1`).

---

## 16. `engine.py` — Orchestration

```python
import json
from .nodes.parser import parse
from .nodes.planner import Planner
from .nodes.validator import validate
from .generators import REGISTRY

class Engine:
    def __init__(self, db_path, profile="max"):
        self._planner = Planner(db_path, profile)

    def transpile(self, raw_ir: dict) -> dict:
        program = parse(raw_ir)                       # 1. validate & parse
        plan = self._planner.plan(program)            # 2. anti-pattern planning
        sources = {                                   # 3. five-language generation (incl. safety)
            lang: gen.generate(program, plan)
            for lang, gen in REGISTRY.items()
        }
        results = {                                   # 4. cross-language validation
            lang: validate(lang, src, program)
            for lang, src in sources.items()
        }
        return {"program": program, "sources": sources, "validation": results}
```

---

## 17. `main.py` — CLI Panel + Validation

Entry point: load the IR (from an argument or from `examples/`), run the engine, render each language's
source + validation status into a pure-stdlib box panel; the Python column additionally shows the actual
variable values after `exec()`, intuitively demonstrating "run equivalency".

```python
import argparse, json, os, sys
from .engine import Engine

def main(argv=None):
    ap = argparse.ArgumentParser(description="Spaghetti Architect transpiler")
    ap.add_argument("ir", nargs="?", help="path to IR JSON; omit to run the built-in example suite")
    ap.add_argument("--profile", default="max",
                    choices=["minimal", "standard", "max"])
    ap.add_argument("--lang", action="append", help="only output the given language(s) (repeatable)")
    args = ap.parse_args(argv)

    here = os.path.dirname(__file__)
    db = os.path.join(here, "..", "config", "anti_patterns_db.json")
    engine = Engine(db, args.profile)

    cases = _load_cases(args.ir, here)        # single file or the whole examples/ suite
    overall_ok = True
    for name, raw in cases:
        out = engine.transpile(raw)
        _render_panel(name, out, only=args.lang)        # box CLI panel
        for res in out["validation"].values():
            if res.status == "FAIL":
                overall_ok = False
    return 0 if overall_ok else 1            # non-zero exit code, easy for CI to pick up

if __name__ == "__main__":
    sys.exit(main())
```

Panel sketch (box-drawing, pure stdlib):

```text
┌─ case: combined ───────────────────────────── profile: max ─┐
│ python      PASS  is_found=True  out_val='localhost'        │
│ javascript  PASS  {"is_found":true,"out_val":"localhost"}   │
│ go          SKIP  missing toolchain: go                     │
│ java        PASS  ...                                       │
│ cpp         PASS  ...                                       │
└─────────────────────────────────────────────────────────────┘
```

---

## 18. Testing Strategy

1. **Golden snapshots (`tests/test_golden.py`)** — for each `examples/*.json` × 5 languages, assert the
   generated source is byte-for-byte equal to `tests/golden/<lang>/<case>.<ext>`. Catches any
   unintended generation change. Relies on **determinism** (§2 principle 5). Update baselines via
   `UPDATE_GOLDEN=1 python -m pytest` (or a small script).

2. **Cross-language equivalency (`tests/test_equivalency.py`)** — run `validate()`, assert each language
   is `PASS` or `SKIP` (never `FAIL`). Languages with missing toolchains auto-SKIP, so it runs both
   locally and in CI.

3. **Parser negative tests** — feed invalid IR (missing fields, wrong types, references to undeclared
   variables, result_var collisions), assert `IRValidationError` is raised.

4. **Profile matrix** — run `minimal/standard/max` each, confirm the strength level actually changes
   output and still PASSes.

5. **Pure standard library**, `pytest` optional (`unittest` works too).

> **As built** — the suites are `tests/test_golden.py`, `tests/test_equivalency.py`,
> `tests/test_parser.py`, `tests/test_profiles.py` (25 tests total). A root `conftest.py` puts the repo
> root on `sys.path` so bare `pytest` works; `python -m unittest discover -s tests -t .` works too.
> Refresh snapshots with `UPDATE_GOLDEN=1 python -m unittest tests.test_golden`.

---

## 19. Generated Output Examples

Below is the expected output form under `profile=max` (verified syntactically correct).

> **As built** — the inline `# SPAGH_xxx` labels below are explanatory annotations. The generator emits
> one grouping comment per operation (e.g. `# SPAGH_001/006/008: manual index loop instead of \`in\``),
> not a label per line; the literal, byte-exact output is what `tests/golden/` locks in.

### 19.1 Python — MEMBERSHIP_CHECK

```python
# MEMBERSHIP_CHECK: is_found = search_val in data_list
is_found = False
try:
    if data_list is not None:                 # SPAGH_007 over-guard + safety
        _idx = 0                              # SPAGH_001/008 manual indexing
        _n = len(data_list)
        _match_flag = False
        while _idx < _n:
            _current = data_list[_idx]        # SPAGH_002 redundant temporary
            if _current == search_val:
                _match_flag = True
            else:
                _match_flag = _match_flag      # SPAGH_004 no-op
            _idx = _idx + 1
        if _match_flag == True:               # SPAGH_003 boolean verbosity
            is_found = True
        else:
            is_found = False
    else:
        is_found = False
except Exception:
    is_found = False                          # safety fallback
```

### 19.2 Python — KEY_VALUE_LOOKUP

```python
# KEY_VALUE_LOOKUP: out_val = config_db[input_key] or '127.0.0.1'
out_val = "127.0.0.1"
try:
    if config_db is not None:
        _resolved = False
        _key = input_key
        if _key == "dev":                     # SPAGH_005 cascading conditionals
            out_val = "localhost"
            _resolved = True
        elif _key == "prod":
            out_val = "10.0.0.1"
            _resolved = True
        else:
            _resolved = False
        if _resolved == False:
            out_val = "127.0.0.1"
    else:
        out_val = "127.0.0.1"
except Exception:
    out_val = "127.0.0.1"
```

### 19.3 C++ — MEMBERSHIP_CHECK (pointer arithmetic SPAGH_006)

```cpp
// MEMBERSHIP_CHECK: is_found = search_val in data_list
bool is_found = false;
try {
    int* list_ptr = data_list.empty() ? nullptr : &data_list[0];
    long data_list_len = (long)data_list.size();
    if (list_ptr != nullptr && data_list_len >= 0) {   // memory-bounds safety
        long _idx = 0;
        bool _match_flag = false;
        while (_idx < data_list_len) {
            int _current = *(list_ptr + _idx);          // SPAGH_006 pointer offset
            if (_current == search_val) {
                _match_flag = true;
            } else {
                _match_flag = _match_flag;               // SPAGH_004 no-op
            }
            _idx = _idx + 1;
        }
        is_found = (_match_flag == true);
    } else {
        is_found = false;
    }
} catch (...) {
    is_found = false;
}
```

---

## 20. Implementation Status

**All five milestones below are complete**, and the 25-test suite (golden + equivalency + parser +
profiles) is green. Validation coverage depends on the host: with every toolchain present all five
languages PASS; with none, Python is runtime-verified via `exec()` and JS/Go/Java/C++ **SKIP** (never
FAIL). The four compiled targets are otherwise locked in by golden snapshots + inspection.

| Milestone | Content | Verifiable acceptance |
|-----------|---------|-----------------------|
| **M1 core loop** | `ir_models` + `parser` + `emitter` + `python_gen` + the Python branch of `validator` + an `engine` skeleton | one IR generates Python and passes the oracle comparison via `exec()` |
| **M2 the other four languages** | `javascript_gen` / `go_gen` / `java_gen` / `cpp_gen` | with toolchains installed, all 4 PASS |
| **M3 data-driven anti-patterns** | `anti_patterns_db.json` + `planner` + wiring patterns/profile into each generator | switching `--profile` changes output accordingly and still PASSes |
| **M4 validation & panel** | `validator` for all languages + `main.py` box panel + non-zero exit code | `python -m src.main examples/combined.json` prints the full panel |
| **M5 tests & lock-in** | `examples/` + `tests/golden/` + the two test suites | golden + equivalency tests all green |

> This was the original build order (M1→M5): M1 was the closed loop, and each later milestone was "copy
> the hook pattern + add a language". It is retained here as a record of how the system came together.

---

## 21. Open Questions & Trade-offs

1. **The abstraction red line.** Template method + emitter already remove most duplication. **Do not**
   add a "generic expression AST / semantic layer" into base — that would make a single spaghetti line
   hard to read and isn't worth it. Keep the low abstraction of "emitter handles indentation, generator
   writes text lines".

2. **The "unused variable/import" trap in Go.** Go treats unused imports/variables as a **compile
   error** (Java/C++ only warn). Handled: the single `import "fmt"` is always used by the JSON print,
   every input is touched with `_ = name`, and the no-`else` discipline (§11 As built) avoids
   `}`-newline-`else` breaking under automatic semicolon insertion. The §15 validator backstops all this.

3. **C++ pointer-demo safety.** `*(ptr+i)` is a teaching "anti-pattern", but must be paired with full
   bounds checking (§19.3), or it will genuinely SegFault, violating the core promise. The pointer only
   offsets within the `vector`'s own memory range and never does any out-of-bounds writes.

4. **Missing toolchain = SKIP, not FAIL.** CI may not have all five toolchains installed. SKIP must be
   **prominently visible** in the panel and tests, to avoid the illusion of "green but actually
   untested".

5. **Keep the planner as a separate stage?** The implementation keeps it, because it is **genuinely
   data-driven** (profile/applies_to come from the DB). If the DB ever degrades into a pure lookup table
   with no logic, consider folding it into the generator to avoid an empty shell (the original intent of
   improvement #6).

6. **Random obfuscation (future).** Currently fully deterministic on purpose, to support golden tests.
   If random naming/reordering is introduced, it must be `--seed`-able, and golden tests should switch
   to "structural assertions" rather than byte-for-byte.

---

*This document tracks the architecture of the implemented system. If code and doc ever disagree, the
code is authoritative — fix the drift and update the affected section's **As built** note. The original
pre-implementation blueprint is preserved untracked as `init.en.md` / `init.md`.*
