# `eval/` — the metric library reused by the benchmark

This directory ships the two standard-library-only modules that `bench/` reuses by
import (no second copy, single source of truth):

- **`metrics.py`** — a pure, clock-free metric library: a rigorous **Python-AST
  lane** (cyclomatic, cognitive, nesting, Halstead, maintainability index) and a
  **language-agnostic proxy lane** (sloc, tokens, a branch-keyword cyclomatic
  proxy, brace/indent depth), plus a clean-baseline synthesizer and a deterministic
  bytecode op-count (`count_work`, via `sys.monitoring`).
- **`gen_samples.py`** — the deterministic, seeded sample set
  (`build(seed)` / `load()` / `SEED`) and the family/variant layout; the single
  source of truth for sample logic. Every emitted IR is validated by the real
  parser before use.

> **Requires Python 3.12+** (PEP 669 `sys.monitoring`) for `count_work`. Zero
> third-party dependencies. See [`../REQUIREMENTS.md`](../REQUIREMENTS.md).

`bench/` imports these as `from eval import metrics, gen_samples` to build a
ground-truth, contamination-resistant LLM code benchmark; see [`../bench/`](../bench/).

> **Note.** `metrics.py` and `gen_samples.py` are the two modules of a larger
> de-optimization measurement study that are reused by `bench/`; the rest of that study
> (its driver, prompt, and report) is unpublished and not shipped here. Their docstrings
> therefore mention internal references (a `run_eval.py` driver, a `deopt_eval_prompt.md`
> with numbered phases, and a report) that resolve only inside that private study, not in
> this artifact. Nothing in `bench/` or the paper depends on those references.
