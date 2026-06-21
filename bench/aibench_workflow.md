# Workflow Prompt — Building a Contamination-Resistant, Ground-Truth AI Code Benchmark on Spaghetti Architect

> **How to use this file.** Hand it verbatim to a Claude Code agent running at the
> root of the Spaghetti-Architect repository (the directory that contains `src/`,
> `config/`, and `eval/`). It is a complete, self-contained workflow. The agent
> turns the existing anti-optimization transpiler into a **benchmark generator for
> evaluating LLM code abilities**, closes the project's publication/architecture
> gaps, and emits a paper draft — **without compiling it**. The expensive, noisy,
> context-polluting work (LLM calls, grading model-produced code, literature
> search) is delegated to **Opus 4.8 subagents**; the main agent only ever ingests
> compact JSON, so its context stays clean.
>
> **This run builds and dry-runs the entire harness but makes no real LLM API
> calls and spends nothing.** Everything is wired so that, later, dropping an API
> key and model names into one config file (`bench/config.json`) is the *only* step
> between the built harness and a live benchmark run.

---

## Role & objective

Spaghetti Architect is an **anti-optimization transpiler**: a clean JSON IR in,
deliberately awful-but-provably-correct code (Python, JavaScript, Go, Java, C++)
out, with an oracle-gated validator. On its own that reads as a novelty tool. Your
job is to make it a **scientific instrument**: a generator of *ground-truth,
tunable, contamination-resistant* code benchmarks, and to use it to measure how
LLMs behave on three tasks where the tool's guarantees give us exact labels:

1. **Refactoring** — recover clean code from incidental complexity, *auto-graded
   for semantic equivalence by the existing validator* and for simplification
   against the *known-optimal* clean baseline.
2. **Quality judgment (LLM-as-judge)** — is an LLM judge *monotone and sensitive*
   to ground-truth-controlled, semantics-preserving degradation?
3. **Comprehension / execution** — does *incidental* complexity (logic held fixed)
   degrade a model's ability to predict what code computes?

**The thesis that makes this a paper, not a leaderboard.** Spaghetti Architect
exposes two *orthogonal* difficulty axes:

- **Intrinsic complexity** — `#operations`, `N` (cascade arms), `L` (list length):
  the logic genuinely grows.
- **Incidental complexity** — `profile` (`minimal`→`standard`→`max`): identical
  semantics, more spaghetti, **zero** logic change.

Varying them independently answers a crisp question no hand-collected corpus can:
*do models fail because the problem got bigger, or because the presentation got
messier?* That orthogonal decomposition, plus **contamination resistance**
(fresh instances minted from held-out seeds), is the spine of the contribution.

This workflow also repairs the project-level gaps that would otherwise sink a
submission: missing related work, no construct-validity anchor for the metrics,
an untracked/undocumented `eval/`, honest dependency/version statements, and basic
artifact-evaluation readiness.

---

## Scope of THIS run — build the harness, do not bill the API

**Build everything; run nothing that costs money.** Stop before any real model
query. Concretely:

- **In scope now (zero API spend):** Phase 1 (repo hardening), Phase 2 (dataset +
  held-out *minting code*), Phase 3 (tasks, prompts, graders, model client, **and
  the config file**), Phase 4 *plumbing* + `--selftest` (mock model) + `--dry-run`,
  Phase 5 (related-work survey — needs no LLM-under-test), Phase 6 (non-LLM metric
  anchor — needs no LLM), and Phase 7/8 *scaffolding* (figure code + paper template
  that read `results.json`, with clear `AWAITING LIVE RUN` placeholders).
- **Deferred until the user flips the config:** the real `--batch` runs fanned out
  to Opus 4.8 subagents, and the final population of figures/paper with real numbers.
- **The one thing between built and live:** fill in `bench/config.json` (API key +
  model names). Nothing else should need editing.

`run_bench.py --batch` must **refuse to make a real call** while the config holds
placeholder/empty credentials — print `configure bench/config.json, then re-run`
and exit non-zero. `--selftest` and `--dry-run` use the **mock model** and need no
key, so they always work.

---

## Operating constraints (hard rules)

1. **Reuse the real pipeline; never re-implement it.**
   - `from src.engine import Engine` — `Engine(db, profile).generate(raw_ir)` →
     `{"program": IRProgram, "sources": {lang: src}}`.
   - `from src.nodes.validator import oracle, validate` — `oracle(program)` is the
     ground truth; `validate(lang, src, program)` compiles/runs `src` and compares
     to the oracle (`.status` ∈ {PASS, SKIP, FAIL}). **This is the refactor
     semantic grader — do not write a second one.**
   - `from eval import metrics as M`, `from eval import gen_samples` — the metric
     library (`python_ast_metrics`, `count_work`, `clean_baseline_runnable`,
     `deoptimization_index`, `inflation_ratios`, …) and the deterministic sample
     builder (`build()`, `load()`, `SEED`).

2. **Zero-pip *core*; the benchmark client is the one allowed exception, and it is
   isolated.** The engine and metric lanes stay standard-library only. The only
   new outside touch is reaching an LLM API: implement it in `bench/models.py` with
   **stdlib `urllib.request`** (no `pip install`, no SDK), reading the key from an
   env var. Mark `bench/` explicitly as "stdlib + network + API key". The external
   non-LLM judges in Phase 6 (e.g. `radon`) are optional and likewise quarantined.

3. **Determinism has two regimes — be honest about the boundary.** Sample
   generation and all static metrics are byte-deterministic (seeded); reproduce
   them exactly. **LLM outputs are *not* deterministic.** Pin `temperature=0`,
   record the exact model id and every sampling parameter, draw **k samples per
   item** (default `k=5`), and report **mean ± stdev / bootstrap CI**. Never claim
   model results are byte-reproducible; claim *protocol* reproducibility.

4. **Model-produced code is untrusted.** Do **not** in-process `exec` model Python
   (the validator's in-process path is for *trusted* generated code only). Run
   every model artifact in a **subprocess with a wall-clock timeout, `-I`
   isolated mode, no network, in a throwaway tempdir**, then compare stdout to the
   oracle. Reuse the validator's compile/run discipline for the other languages.

5. **Correctness gates quality — mirror the project's own ethos.** A refactor's
   simplification score counts **only if it is semantically equivalent**. Bad-and-
   wrong, or simpler-but-broken, scores zero on the quality axis.

6. **Keep the held-out test split private.** Mint it from a seed that is **never
   committed**. Commit only the public dev split and the generator. Report the
   dev/test gap as the contamination control.

7. **Do NOT compile the paper.** Produce `bench/paper/benchmark.tex` and stop. No
   `pdflatex`/`latexmk`.

8. **Context discipline is the point of the subagents (see next section).** The
   main agent must never ingest prompts, completions, model-generated code, raw
   transcripts, or compiler logs **in bulk**. Only compact JSON crosses the
   subagent boundary.

Track progress with a todo list and work the phases in order.

---

## Subagent delegation protocol (the core mechanism)

Three classes of work are heavy and independent — fan each out to its own
**Opus 4.8 subagent** and keep only the compact return:

| Job | Why it must be a subagent | Returns |
|---|---|---|
| **Benchmark batch** `(task × model[ × family])` | thousands of tokens of prompts+completions, plus compiling/running untrusted model code | per-item scores + aggregates (JSON) |
| **Related-work survey** (Phase 5) | many web searches = large, noisy context | `refs.bib` + `related_work.md` |
| **External metric oracle** (Phase 6, optional) | installs/runs third-party tools | per-item metric scores (JSON) |

**Spawn spec.** Use the Agent tool with a `general-purpose` subagent **pinned to
Opus 4.8** (pass the model override `opus`). Long batches may run with
`run_in_background: true`; you will be notified on completion. Give each subagent
the *batch descriptor only* (task name, model id, the list of sample stems +
axes), never the prompts inline — it regenerates everything from the repo.

**The compact-return contract (verbatim rule for every benchmark subagent):**

> Run the assigned batch by calling `python3 bench/run_bench.py --batch <task>
> --model <id> [--family <fam>]`. That script generates items, queries the model,
> grades locally (reusing `oracle`/`validate`/`metrics`), writes the full record to
> `bench/out/subagent/<task>__<model>[__<fam>].json`, and echoes a single compact
> JSON line. **Return ONLY that one JSON line. Never paste model code, prompts,
> completions, transcripts, or compiler output into your reply.** Surface `SKIP`
> (missing toolchain / refused generation) honestly; never count it as a pass.

Compact schema each benchmark subagent returns (and writes):

```json
{"task":"refactor","model":"claude-opus-4-8","family":"allowlist",
 "k":5,"items":[
   {"sample":"allowlist_L32","profile":"max","language":"python","variant":"base",
    "intrinsic":{"L":32,"n_ops":1},
    "semantic_ok_rate":1.0,
    "recovery":{"cyclomatic":0.93,"ast":0.88,"effort":0.95},
    "simplification_quality":0.92,
    "failure_modes":{"broke_equivalence":0,"no_change":0,"over_complicated":0}}],
 "aggregate":{"semantic_ok_rate":0.98,"simplification_quality_mean":0.86,
              "simplification_quality_ci95":[0.81,0.90]}}
```

The main agent **only aggregates** these files (Phase 7); it never reads the
generated source or transcripts in bulk.

---

## Deliverables (create exactly this tree)

```
bench/
├── aibench_workflow.md      # this file
├── config.example.json      # Phase 3 — committed template: provider, API key (or env ref), model names, k, temp
├── config.json              # Phase 3 — your real config (GITIGNORED); fill api + names -> pipeline goes live
├── dataset.py               # Phase 2 — freeze public dev set + mint private held-out; manifest + ground truth
├── models.py                # Phase 3 — stdlib-urllib LLM client (reads config.json; pluggable, temp 0, k-samples)
├── tasks.py                 # Phase 3 — task/item construction (refactor / judge / comprehend)
├── prompts.py               # Phase 3 — prompt templates per task × language (frozen, versioned)
├── grade.py                 # Phase 3 — graders, reusing src.validator.oracle/validate + eval.metrics
├── run_bench.py             # Phase 4 — --batch (subagent), --aggregate (main), --report, --selftest
├── data/
│   ├── dev/                 # public frozen instances (IR + rendered sources + ground truth)
│   └── manifest.json        # axes, splits, per-item ground truth (held-out SEED value NOT stored here)
├── out/
│   ├── subagent/<task>__<model>[__<family>].json
│   ├── results.json         # Phase 7 aggregate
│   └── relatedwork/{refs.bib, related_work.md}
└── paper/
    └── benchmark.tex        # Phase 8 — the paper draft (NOT compiled)
```

Plus repo-level hardening produced in Phase 1:
`.github/workflows/ci.yml`, `CITATION.cff`, an honest requirements/version note,
a tracked-and-documented `eval/`, and a README correction.

---

## Phase 0 — Orientation & guardrails

Read `architecture.md`, `README.md`, `src/nodes/validator.py`, `eval/metrics.py`,
and `eval/gen_samples.py`. Confirm the reuse surface (above) by import, not by
copying code. Record the environment (`python --version`, toolchains present via
`shutil.which`) into `bench/out/results.json`'s `env` block later. **Do not** start
calling models before Phases 1–3 exist.

---

## Phase 1 — Architecture & repo hardening (make the whole thing publication-grade)

Independent of the benchmark, fix the cross-cutting gaps. These are mechanical and
low-context; the main agent does them directly.

1. **Honest dependency/version split.** The engine is stdlib and runs on **3.8+**,
   but `eval/metrics.count_work` and `eval/worklane` use `sys.monitoring`
   (**PEP 669 → CPython 3.12+**), and `bench/` needs 3.12+ too. Add a top-level
   `REQUIREMENTS.md` (or a section) stating: *engine 3.8+; eval & bench 3.12+;
   optional toolchains node/go/javac+java/g++; bench also needs network + an LLM
   API key*. **Fix the README's "Python 3.8+" claim** to scope it to the engine.
2. **Track and document `eval/`.** It is currently untracked and undocumented. Add
   `eval/README.md` (what it measures, how to run, the subagent fan-out) and remove
   the `eval/` ignore so the evaluation ships with the repo. Keep the *private
   held-out seed* out of version control.
3. **CI.** Add `.github/workflows/ci.yml` that runs `python -m unittest discover -s
   tests -t .` and `python3 eval/run_eval.py --selftest` on 3.12 and 3.13.
4. **Citation & reproducibility.** Add `CITATION.cff` (placeholder authors) and a
   `bench/out/results.json` `env` block (python version, toolchains, model ids +
   params, dataset seed, dev/test split sizes, timestamp).
5. **Scaffold `bench/`** with the tree above (empty modules with docstrings), commit
   `config.example.json`, and add `bench/config.json` (and any held-out-seed file)
   to `.gitignore`.

---

## Phase 2 — Benchmark dataset (`bench/dataset.py`) — orthogonal axes + contamination control

Build on `eval/gen_samples.build()`; do not duplicate sample logic.

- **Axes.** Cross `profile ∈ {minimal, standard, max}` (incidental) ×
  `scale/structure` (intrinsic: the `config_resolver_N{4,8,16,32}`,
  `allowlist_L{8,32,128}`, `discovery_pipeline`, `fsm_transition`, `status_router`
  families) × `language ∈ {python, javascript, go, java, cpp}` × the `*_v0..v4`
  input variants (for differential grading).
- **Two splits.** `dev` = the existing deterministic `SEED` (public, committed).
  `test` = a **fresh, private seed passed by env (`BENCH_HELDOUT_SEED`), never
  committed** — re-mint list contents / key sets / probes so the instances are
  almost certainly unseen in any training set. Same structure, new literals.
- **Ground-truth manifest** (`data/manifest.json`), per item: `oracle(program)`
  outputs; the `clean_baseline_runnable`/`clean_baseline_static` source (the known
  optimum); knob coordinates `(profile, intrinsic_knob, n_ops)`; the planner's
  enabled `SPAGH_*` set; and the rendered source per language. Validate every IR
  with `parse()` before writing (never emit a structurally invalid sample).

---

## Phase 3 — Tasks, prompts, graders, model client (pure & reusable)

### `bench/config.json` — the only thing you edit to go live
Ship a committed `config.example.json` template; the agent copies it to
`config.json` (gitignored) with placeholders. Fields:

```json
{
  "provider": "anthropic",
  "base_url": "https://api.anthropic.com/v1/messages",
  "api_key": "",
  "api_key_env": "ANTHROPIC_API_KEY",
  "models_under_test": ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
  "driver_subagent_model": "opus",
  "k_samples": 5,
  "temperature": 0,
  "max_tokens": 2048
}
```

A resolved key is a non-empty `api_key`, else the env var named by `api_key_env`.
When the key resolves empty, the client is in **placeholder state**: `--batch`
refuses, `--selftest`/`--dry-run` still work via the mock model. Filling `api_key`
(or the env var) and `models_under_test` is the entire "go live" step — the
default `models_under_test` is a capability gradient **within one family** (itself
a result); leave a hook for an external provider.

### `bench/models.py`
A thin, pluggable client that **reads `config.json`**. Default provider = Anthropic
via stdlib `urllib.request` to the Messages API; key and model names come from the
config (never hard-coded). Always `temperature=0` (from config), record `model`,
params, and a content hash of the prompt. Expose `complete(model, system, user) ->
str`, a `k`-sampling wrapper, and a built-in **`mock` model** (used by
`--selftest`/`--dry-run`) that returns the known clean baseline for refactor and
the correct label/number for judge/comprehend — so the whole pipeline is
exercisable end-to-end with zero spend. Never log full completions to stdout.

### `bench/prompts.py`
Frozen, versioned templates per task × language. One job each, no chain-of-thought
leakage into the graded artifact: refactor returns *only* a code block; judge
returns *only* a number (pointwise) or a single token `A`/`B` (pairwise);
comprehension returns *only* a JSON object of result-vars. Include a `prompt_version`
string in the manifest so prompt changes are traceable.

### `bench/grade.py` (reuse, don't reinvent)

**Task A — Refactor.**
- *Semantic*: write the model's code to a tempdir and grade with the existing
  validator path (untrusted-code rules from constraint 4). For **Python**, the
  model must keep the same `result_var` names; run it isolated and read those vars.
  For the other four, instruct the prompt to **preserve the JSON-print epilogue**
  (or append the original) so `validate(lang, src, program)` can parse stdout.
  Grade on the **base input and all `*_v0..v4` variants** (differential testing):
  `semantic_ok = 1` iff every input agrees with `oracle`.
- *Simplification*: measure the model's output with `eval.metrics` and score
  recovery toward the known optimum:
  `recovery(m) = clamp((m_spaghetti − m_model) / (m_spaghetti − m_clean), -1, 1)`
  for `m ∈ {cyclomatic, ast_nodes, halstead effort}` (Python-AST lane rigorous;
  other languages use the agnostic proxy lane). `simplification_quality =
  semantic_ok × geomean(positive recoveries)`. Bucket failure modes:
  `broke_equivalence` (semantic_ok=0), `no_change` (recovery≈0), `over_complicated`
  (recovery<0).

**Task B — Judge.**
- *Pointwise*: rating `r∈[1,10]` for "maintainability/readability". Score
  `monotonicity = Spearman(r, knob_rank)` across `{clean, minimal, standard, max}`
  (expect strongly negative); `sensitivity = r(clean) − r(max)`; flag inversions.
- *Pairwise*: show two versions, ask which is more maintainable; `pairwise_acc` vs
  the ground-truth knob order. Build a full ranking from pairwise wins and correlate
  with the knob.
- *Anchor* (set up here, run in Phase 6): the same items scored by a **non-LLM**
  judge (radon MI / cognitive complexity) so judges can be compared to a
  ground-truth-ish metric, not just to each other.

**Task C — Comprehension.**
- *Output prediction*: given inputs, return all `result_var`s; `exact_match` vs
  `oracle`, over base + variants (variants defeat guessing). Report accuracy vs
  `profile` (incidental) and vs `N`/`L`/`n_ops` (intrinsic), per language.
- *Equivalence judgment* (optional): equivalent pairs (same IR, different
  profile/language) vs non-equivalent pairs (one input mutated so the oracle
  output changes); accuracy.

All graders are **pure functions of (model output, program, ground truth)** —
clock-free, no network — so they are trivially testable in `--selftest`.

---

## Phase 4 — Build the runner & prove it (mock only; no real calls this run)

`run_bench.py` exposes:

- `--selftest` — one item per task on one sample using the **mock model**: proves
  the graders and the compact-JSON contract work with **zero API spend**. Must pass
  before anything else.
- `--dry-run [--task ...]` — render the real prompts for the real dataset, grade
  the **mock** responses, and write the full `bench/out/subagent/*.json` artifacts
  *in their final shape* (so you can see exactly what a live run will produce)
  without contacting any API. Records are marked `"model":"mock"`.
- `--batch <task> --model <id> [--family <fam>]` — the **live subagent** entry
  point. Generates items, queries the configured model (`k` samples, temp 0),
  grades locally, writes `bench/out/subagent/<task>__<model>[__<fam>].json`, echoes
  one compact line. **Refuses with a clear message while the config is in
  placeholder state.**
- `--plan` — print the exact list of `(task, model, family)` batches implied by the
  config (i.e. the subagent fan-out to dispatch when live). Needs no key.
- `--aggregate` — merge subagent JSON into `bench/out/results.json` (+ figure
  series); never reads bulk source.
- `--report` — emit `bench/paper/benchmark.tex` (NOT compiled).

**In this run, execute only `--selftest` and `--dry-run` (and `--plan` to show the
fan-out).** Do **not** spawn the real benchmark subagents and do **not** call any
model. Confirm the dry-run wrote artifacts in the documented schema, then stop.

### Going live later (the one-edit flow)

When the user wants real numbers, after they fill `bench/config.json`:

1. `python3 bench/run_bench.py --plan` lists the `(task × model × family)` batches.
2. For each batch, spawn one **Opus 4.8** `general-purpose` subagent
   (`driver_subagent_model`) under the compact-return contract; each runs
   `python3 bench/run_bench.py --batch <task> --model <id> [--family <fam>]` and
   returns only its one JSON line. (`refactor`/`comprehend` fan out per family
   because they compile/run.)
3. `python3 bench/run_bench.py --aggregate && python3 bench/run_bench.py --report`.

Record toolchain availability so a `SKIP` (model refused, or `go` absent) is
explained, never mistaken for a pass.

---

## Phase 5 — Related work & positioning (SUBAGENT)

Spawn one **Opus 4.8** subagent to survey and position against, returning a
`refs.bib` + a `related_work.md` (a positioning table + 3–4 paragraphs), **no raw
search dumps**:

- **Obfuscation / transformation**: Collberg–Thomborson taxonomy, Tigress, OLLVM,
  `javascript-obfuscator` (already compared in `eval/`).
- **Mutation testing** (semantics-preserving transforms — reviewers *will* raise
  the analogy; distinguish: mutants inject faults, we preserve semantics).
- **Program generators**: Csmith (the closest sibling — random *valid* programs to
  stress a consumer; ours adds *labels* + a *degradation knob*), fuzzers.
- **Code-quality / LLM-as-judge** validation, and **code-reasoning / execution**
  benchmarks (e.g. CRUXEval-style output prediction), and the **data-contamination**
  literature (why fresh-minted instances matter).

Write the contribution delta explicitly: *ground-truth + orthogonal intrinsic/
incidental knobs + auto-checkable equivalence + contamination resistance*, none of
which the neighbors offer together.

---

## Phase 6 — Construct validity (anchor the metrics to something external)

In the main agent (or a small optional subagent if a tool needs installing):
score the dev set with a **non-LLM** complexity tool (`radon` for the Python lane;
optionally `lizard` cross-language). Report (a) correlation of the tool's score
with the knob (does the ground-truth knob move an independent metric monotonically?)
and (b) correlation of **LLM judges** with that tool. This is what turns the
bespoke Deoptimization Index and the judge scores from "trust us" into "anchored to
an external, reproducible reference."

---

## Phase 7 — Aggregate, figures, analysis (`--aggregate`, pgfplots/TikZ)

Merge subagent JSON into `bench/out/results.json`, then emit these figures with
**real numbers** (TikZ/pgfplots inline, no image files):

1. **Incidental-complexity degradation** — task score vs `profile`
   (`minimal→max`) with **logic held fixed**; one line per model. The headline:
   does mess alone, at zero semantic change, hurt?
2. **Intrinsic vs incidental decomposition** — Δscore attributable to growing
   `N`/`L`/`n_ops` (fixed profile) vs growing `profile` (fixed logic). A grouped
   bar or 2-factor surface; this is the scientific core.
3. **Refactor 2-D** — semantic-ok rate (x) × simplification-quality (y) per model;
   annotate the upper bound (clean baseline = (1.0, 1.0)) and the no-op lower bound.
4. **Judge calibration** — rating vs knob with the Spearman ρ per model, and the
   non-LLM anchor overlaid.
5. **Contamination control** — score on public-`dev` vs private-`test` (fresh
   seed); a gap flags memorization, ≈0 supports the anti-contamination claim.
6. **Cross-language** — task score per language (same logic), exposing where a
   backend is harder for models.

Include per-model k-sample CIs everywhere. Re-state which lanes are rigorous
(Python-AST) vs proxy (other four).

---

## Phase 8 — Paper draft (`bench/paper/benchmark.tex`, NOT compiled)

A standalone `article` (`acmart`/`IEEEtran` if available, else `article`) wired to
`bench/out/results.json` and a **real bibliography** (`refs.bib` from Phase 5).
Until a live run populates results, figures/tables show `AWAITING LIVE RUN`
placeholders; the prose, related work, and threats are written now. Structure:

1. **Abstract** — a ground-truth, contamination-resistant benchmark for LLM code
   refactoring/judgment/comprehension, built on a correctness-preserving generator
   with orthogonal complexity knobs.
2. **Introduction** — the "Csmith-for-badness" framing; why hand-collected bad-code
   corpora lack labels, knobs, and contamination control; the orthogonal-axis
   question.
3. **Related work** — Phase 5 (with the explicit delta).
4. **The generator** — Spaghetti Architect in brief (IR → planner → 5 backends →
   oracle gate); cite `architecture.md`; emphasize *correctness by construction* as
   the property that yields labels.
5. **Benchmark design** — the three tasks, ground-truth grading (reusing the
   validator + metrics), the two splits, the axes, prompt protocol, k-sampling.
6. **Results** — the six figures + a per-model table; lead with the
   incidental-complexity and decomposition findings.
7. **Construct validity** — Phase 6 anchors.
8. **Threats to validity** — narrow IR (2 ops → framed as a *controlled stimulus*);
   equivalence-by-differential-testing not proof; Python-AST rigorous vs proxy
   lanes; LLM nondeterminism (mitigated by temp 0 + k + CIs); prompt sensitivity;
   single API vendor for targets.
9. **Conclusion** — what the orthogonal decomposition reveals about model
   robustness to incidental complexity.
10. **Appendix** — full per-(task × model × axis) tables; exact commands; the
    held-out protocol (seed kept private).

Placeholder authorship/affiliation for the human to fill. **Do not compile.**

---

## Acceptance criteria (the workflow's own "done")

- [ ] **No real LLM API call was made this run, and nothing was spent.** Only
      `--selftest`, `--dry-run`, and `--plan` (mock model) were executed.
- [ ] `bench/config.example.json` is committed with placeholders; `bench/config.json`
      is created, gitignored, and in placeholder state; `--batch` **refuses** while
      placeholder, with a clear "configure then re-run" message.
- [ ] `bench/run_bench.py --selftest` passes and `--dry-run` wrote
      `bench/out/subagent/*.json` in the final schema (model marked `mock`).
- [ ] Dataset has a public `dev` split (committed) and held-out *minting code* keyed
      on an uncommitted seed; every IR passes `parse()`; `manifest.json` carries
      per-item ground truth.
- [ ] The Opus 4.8 subagent fan-out is **wired and documented** (`--plan` lists it)
      but **not fired**; the live path is one config edit + dispatching those
      subagents.
- [ ] Refactor grading reuses `validate`/`oracle`; model code ran **untrusted**
      (subprocess, timeout, isolated, no network); simplification is **gated** on
      semantic equivalence.
- [ ] The runner is built to report `k`-sample CIs and to record an env block (model
      ids, params, seeds, toolchains, split sizes) — verified via `--dry-run`.
- [ ] `related_work.md` + `refs.bib` exist (from a subagent); a non-LLM metric anchor
      is computed (Phase 6).
- [ ] Repo hardening landed: README version claim fixed, `eval/` tracked +
      documented, CI workflow, `CITATION.cff`.
- [ ] `bench/paper/benchmark.tex` is scaffolded with all six figures wired to
      `results.json` (real data populates after live runs; placeholders marked
      `AWAITING LIVE RUN`), cites the Phase 5 bibliography, and **was not compiled**.
- [ ] No third-party Python dependency entered the engine or the metric lanes; the
      only network/key use is quarantined in `bench/models.py`.

---

## Appendix — metric & contract cheat-sheet

```
# ground truth (reused, never re-implemented)
oracle(program)                 -> {result_var: expected_value}
validate(lang, src, program)    -> .status in {PASS, SKIP, FAIL}   # refactor semantic grader
M.clean_baseline_runnable(prog) -> known-optimal source (recovery denominator)

# Task A — Refactor
semantic_ok       = 1 iff validate==PASS on base AND every *_v0..v4 variant
recovery(m)       = clamp((m_spaghetti - m_model)/(m_spaghetti - m_clean), -1, 1)
simplification_q  = semantic_ok * geomean(positive recoveries over {cc, ast, effort})

# Task B — Judge
monotonicity      = Spearman(rating, knob_rank)        # expect strongly negative
sensitivity       = rating(clean) - rating(max)
pairwise_acc      = P(judge picks the less-degraded member | pair)

# Task C — Comprehension
exact_match       = predicted result_vars == oracle (over base + variants)
degradation       = accuracy as a function of profile (incidental) / N,L,n_ops (intrinsic)

# headline decomposition
delta_incidental  = score(max) - score(minimal)        | logic fixed
delta_intrinsic   = score(large knob) - score(small)   | profile fixed
contamination_gap = score(dev_public) - score(test_private_seed)   # ~0 desired
```

```
RULES:
- Reuse oracle/validate/metrics; never write a second generator or grader.
- temperature=0, k samples, report CIs — model outputs are NOT byte-deterministic.
- Model code is untrusted: subprocess + timeout + isolated + no network.
- Simplification counts only if semantically correct (gate everything).
- Subagents (Opus 4.8) do all LLM/compile-heavy work and return ONLY compact JSON.
- Keep the held-out seed private. Do NOT compile the paper.
```
