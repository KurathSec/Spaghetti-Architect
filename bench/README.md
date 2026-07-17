# `bench/` — A Construction-Labeled Benchmark for LLM Code-Quality Judgment

> **What this is, in one sentence.** `bench/` turns the Spaghetti Architect
> anti-optimization transpiler into a **benchmark generator** that measures whether
> an LLM judge tracks a code-quality ordering that is known *by construction* — plus
> two companion tasks (refactoring, output-prediction comprehension) on the same
> labels. The transpiler is used here only as a **controlled-stimulus generator**:
> it emits semantically-identical programs along a strictly-nested "messiness" knob,
> so a quality ordering exists *independently of any rater*.
>
> This README is the authoritative architecture anchor for the benchmark layer. The
> near-term write-up is a DMLR resource paper (single-blind, under review), maintained and
> published separately from this repo; the LLM-as-judge study is future work (its deferred
> seed draft is regenerated locally by `run_bench.py --report`). The design freeze is recorded
> in the (private) pre-registration. Where this README and any older note disagree, **this
> file wins**.

## Mental model (one IR → many labelled programs → three tasks)

```
                         eval.gen_samples.build(extended=True)        ← ONE generator, reused
                                      │  (no second sampler)
        clean JSON IR  ──────────────▼──────────────────────────── bench/dataset.py
   (4 ops: MEMBERSHIP_CHECK,                 dev split (public, committed)
    KEY_VALUE_LOOKUP, AGGREGATE,             test split (private, held-out seed)
    CONDITIONAL_SELECT)                              │
                                                     ▼
   incidental knob (6 nested levels)        render via the project Engine
   clean ⊂ minimal ⊂ light ⊂ standard ⊂      (Python/JS/Go/Java/C++)            ← src/ pipeline, reused
   heavy ⊂ max   = quality order            + oracle + validator (equivalence)
   × intrinsic scale (N/L/W/T)                       │
   × language × input variant (v0..v4)               ▼
                                          ┌───── three tasks (bench/tasks.py) ─────┐
                                          │  REFACTOR    JUDGE       COMPREHEND     │
                                          └──────────────┬───────────────────────-─┘
                                                         ▼
                                          graders (bench/grade.py) → per-item JSON
                                                         ▼
                                  --aggregate → out/results.json → --report → paper .tex
                          (side channels: bench/anchor.py = construct validity;
                                          bench/analysis.py = pre-registered inference)
```

**Two orthogonal difficulty axes.** *Incidental* complexity = the 6-level messiness
knob at **identical semantics** (the by-construction quality order). *Intrinsic*
complexity = the logic itself growing (cascade arms `N`, list length `L`, window `W`,
threshold count `T`, operation count). Varying them independently is what lets the
benchmark say whether a model failed because *the problem got bigger* or because
*the presentation got messier*.

## Dependency model (read this before "zero dependencies" confuses you)

- **The benchmark core is Python standard-library only** — `dataset`, `models`,
  `prompts`, `tasks`, `grade`, `run_bench`, and the reused `src/` transpiler import
  nothing third-party. Sample generation and every static metric are
  byte-deterministic given a seed.
- **Two side channels use *optional, quarantined* third-party packages**, never the
  core: `bench/anchor.py` (construct validity) wants `radon`/`lizard`/
  `cognitive_complexity`, and `bench/analysis.py` (inferential stats) wants
  `statsmodels`. Each is imported defensively: if absent the module records an honest
  `SKIP` (anchor) or falls back to a stdlib cluster-bootstrap (analysis) — it never
  crashes and never silently fabricates. Install them in a venv so the core stays clean
  (recipe below). The Buse–Weimer readability-feature anchor is pure stdlib and always runs.
- **⚠️ Run metric-dependent steps with that venv's `python`, not the base `python3`.**
  `bench/anchor.py` *and* `bench/run_bench.py --baselines` (plus any refactor grading that uses the
  cross-language `uniform_lane`) need `lizard`/`radon`/`cognitive_complexity` **at run time**; with
  the base interpreter the anchors `SKIP` and `uniform_quality` comes back `null`. Use one
  **persistent** venv instead of recreating throwaway ones:
  ```
  python3 -m venv ~/.venvs/spaghetti-metrics
  ~/.venvs/spaghetti-metrics/bin/pip install radon lizard cognitive_complexity statsmodels
  ```
  then call those steps as `~/.venvs/spaghetti-metrics/bin/python bench/...` (see commands below).

## Modules (what each part *is*)

| File | Role |
|------|------|
| `dataset.py` | Mints the benchmark **dataset**: the two orthogonal axes, the `dev` (public) and `test` (private, held-out-seed) splits, and the ground truth. Reuses `eval.gen_samples.build(extended=True)` — there is **no second generator**. |
| `models.py` | The pluggable **multi-provider LLM client** (Anthropic / OpenAI / Google / OpenAI-compatible gateway), per-model key resolution, dated snapshot ids, and the **`mock`** model used for all zero-spend plumbing. Pins `temperature=0`, `k=8`. |
| `prompts.py` | **Frozen, versioned, paraphrase-ensembled** prompts (`bench-prompts-v2`). One canonical prompt per task for headline numbers + 3 frozen paraphrases for the robustness sweep; the set is content-hashed (`prompt_set_hash`) so it cannot be tuned on results. |
| `tasks.py` | Builds the per-item work for the **three tasks** — `refactor` (rewrite the spaghetti), `judge` (rank by maintainability), `comprehend` (predict the output) — pairing each rendered program with its ground truth. |
| `grade.py` | The **graders**: semantic-equivalence gating (runs candidate code against the oracle), simplification quality (capped per-facet recoveries incl. a readability/MI axis + `over_golfed` guard + optimality band + per-`SPAGH_*` removal), judge pairwise accuracy under **position-swap** (+ jury / no-self-judge helpers + the non-LLM **metric-heuristic judge** baseline), comprehension exact-match, and `ci95_bootstrap`. Pure functions of `(output, program, ground_truth)`. |
| `anchor.py` | **Construct-validity anchors** (non-LLM, quarantined): `radon` CC/MI, `lizard` CC, cognitive complexity, and a stdlib **Buse–Weimer readability-feature** re-implementation. Shows the by-construction knob moves external metrics monotonically. |
| `analysis.py` | The **pre-registered inferential pipeline**: a mixed-effects model `rating ~ knob_rank` (random intercepts for family, sample, language) with Benjamini–Hochberg FDR + Holm correction; `statsmodels` if present, else an honest SKIP + stdlib cluster-bootstrap. |
| `run_bench.py` | The **orchestrator / CLI** that ties it together (self-test, dry-run, plan, live batch, baselines, sweep, aggregate, report). |

## How to call it (commands)

All commands run from the repo root.

```bash
# 1. Freeze the public dataset + manifest (byte-deterministic, seed 20260619)
python3 bench/dataset.py --freeze          # writes bench/data/dev/*.json + bench/data/manifest.json
python3 bench/dataset.py --summary         # human-readable axis/family summary

# 2. Prove the plumbing with the mock model (ZERO API spend)
python3 bench/run_bench.py --selftest      # fast mock plumbing check
python3 bench/run_bench.py --dry-run       # mock over the real dataset (optionally --task / --family)

# 3. Non-LLM results that need no API — RUN WITH THE METRICS VENV (see Dependency model above).
VENV=~/.venvs/spaghetti-metrics/bin/python  # created once; has lizard/radon/cognitive/statsmodels
$VENV bench/run_bench.py --baselines        # clean-ceiling / rule-based / metric-judge panel
                                            #   lizard => cross-language uniform_quality (base python => null)
$VENV bench/anchor.py                       # -> bench/out/anchor.json (radon/lizard/cognitive; else SKIP)

# 4. The live run (PAID; gated on filling bench/config.json — holds API keys, gitignored)
python3 bench/run_bench.py --plan                              # prints the (task × model × family) fan-out
python3 bench/run_bench.py --batch refactor   --model <id> --family <fam>
python3 bench/run_bench.py --batch comprehend --model <id> --family <fam>
python3 bench/run_bench.py --batch judge      --model <id>     # one out/subagent/*.json per batch
python3 bench/run_bench.py --sweep refactor   --model <id>     # prompt-robustness variance
#   contamination tiers: re-run --batch with --split test (needs BENCH_HELDOUT_SEED)

# 5. Merge + write up
python3 bench/run_bench.py --aggregate     # out/subagent/*.json -> bench/out/results.json
python3 bench/run_bench.py --report        # -> archived/benchmark-paper/benchmark.tex (NOT auto-compiled)
#   pre-registered inference (same metrics venv; statsmodels installed there):
$VENV bench/analysis.py                     # -> bench/out/analysis.json
```

`--batch` refuses to run for a model whose provider key is unresolved, so an
accidental paid call cannot happen from a fresh checkout.

## Data & output layout

```
bench/
  data/dev/        public split — committed IR instances (50 base + 50 variant, 7 families)
  data/test/       PRIVATE held-out split — gitignored, minted from BENCH_HELDOUT_SEED, never committed
  data/manifest.json   axes, knob ranks, canary, dataset version
  out/results.json     aggregated --aggregate output (mock; the real ladder/G3 numbers are in out/ladder + out/g3)
  out/subagent/*.json  per-batch raw outputs (mock + the real non-LLM baselines)
  out/anchor.json      construct-validity anchors (real)
  out/analysis.json    pre-registered inference output
  config.json          live API keys — GITIGNORED, never committed (see config.example.json)
```

## Status (so results are not misread)

The harness is complete and mock-proven, and a cost-capped **capability-ladder live run
has been done** (real results below; the full LLM-as-judge run remains future work). Also
real today and needing no API: the non-LLM **baseline panel** (`--baselines`), the
**construct-validity anchors** (`anchor.py`), and the executed **inferential pipeline**
(`analysis.py`). The `out/subagent/*mock*` files are regenerable `--selftest` fixtures
(now gitignored); their names say `mock`, so do not read their `1.0` values as model
performance.

**Real capability-ladder results now exist** and live in `bench/out/ladder/` and
`bench/out/g3/` (re-gradable with **zero API spend** via `bench/ladder_analysis.py` and
`bench/g3_analysis.py`); the `mock`/DRY-RUN note above refers only to the harness
self-test path, not to the project as a whole.

The **annotation ablation** (paper Table `tab:annotation`) re-grades the same way: its
control arm lives in `bench/out/ablation/`, and `bench/annotation_ablation.py` rebuilds the
whole table (both arms, the base-IR-clustered bootstrap, and the leave-one-family-out sweep)
at zero API from committed archives. Run it with `BENCH_STRIP_ANNOTATIONS` **unset**; the
script refuses to start otherwise, because that variable is read at import time and would
rebuild the annotated arm's sources as the wrong corpus.
