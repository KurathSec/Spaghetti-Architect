# G3 run — comprehend×test + refactor×{dev,test} on the 4-model open ladder

Compact, gzipped raw model completions (the same packaging as `../ladder/`), committed so the
graded results can be re-derived without re-querying any model. Each line is one item:
`{sample, variant, profile, language, intrinsic, tier, raw_outputs}` — raw completions only, no grades.

Models (capability order): Meta-Llama-3.1-8B, Mistral-Small-3.2-24B, Llama-3.3-70B, DeepSeek-V4-Flash.
Grid: comprehend on the private **test** split (3 held-out tiers), refactor on **dev** and **test**.

Reproduce. The **dev**-split reference numbers re-grade with **zero API** from version control:
`python3 bench/ladder_analysis.py` re-grades the committed comprehend `../ladder/*.jsonl.gz`, and
`python3 bench/g3_analysis.py` re-grades the **refactor×dev** ladder from the committed
`refactor_dev__*.jsonl.gz` here (it reads the large finalize files under `bench/out/subagent/` when
present, else falls back to re-grading these raw completions — `recovered`/`semantic_ok` reproduce on
the base interpreter; `uniform_quality` additionally needs the metrics venv). The **private-test**
numbers (the Tier-A contamination check, the A/B/C tiers, and the n_ops-matched tier decomposition)
require the held-out seed `BENCH_HELDOUT_SEED` to regenerate the test oracle: by the contamination
design they are reproducible by the maintainer (who holds the seed) and regenerable-equivalent by
anyone from a fresh seed, not byte-reproducible from version control alone.

**Disclosure trade-off (deliberate).** These archives include the raw completions on the private
**test** split, and a refactoring completion reproduces most of its instance's input literals, so
committing them discloses the literal content of the *current* minted test instances. That is the
price of making the published private-test numbers auditable without re-querying any model. The
contamination defense is the re-mint protocol, not the secrecy of this particular split: any
future scored use of the held-out split starts from a fresh re-mint with `BENCH_HELDOUT_SEED`
(never committed). The published dev-vs-Tier-A agreement is a record of runs completed before
these archives were published.

## Verified findings (adversarial 8-agent re-derivation, 2026-06-29)

Every headline number was independently re-derived from the raw per-item grades (not via the
analysis script) and stress-tested for confounds. Outcomes:

**Confirmed, robust:**
- **No contamination.** comprehend dev EM ≈ test Tier-A EM for all 4 models (|Δ|≤0.012); Tier A is a
  faithful re-mint of the dev families (identical family mix, fresh secret values — all prompt hashes
  differ). refactor `semantic_ok` dev ≈ test_A across the full ladder (DeepSeek Δ=−0.0001 at 0.987).
- **The arithmetic-aggregation cliff is genuine computation, not a parsing artifact.** agg_stats outputs
  are well-formed JSON; `peak`/`trough` (max/min) are correct and constant across k=8 — only the `total`
  (sum) field is wrong, emitting a *constant-but-wrong, perfectly formatted* value at high W. EM is ~1.0
  exactly where the W-sum is small and collapses as W grows. The cliff reproduces on the private split.

**Reframed after verification (earlier framings were confounded):**
- **The tier gap is op-type composition, not novelty.** test_A > test_B/C reproduces, but Tier A is 54%
  trivial single-op items (Tier B has none); matched on `n_ops` the A−B gap shrinks and *reverses* at
  n_ops=3. The clean novelty controls — OOD scale on 1-op lookup/membership, OOD depth on conditional
  cascades — show ~zero degradation. Models **generalize over structure (depth/scale/cascade)**; the
  apparent tier gap is the agg_stats computation tax surfacing where aggregate items are over-represented.
- **structure-vs-computation uses `semantic_ok`, not `recovered_rate`.** The "flat 0.20" recovered_rate is
  a Python-AST-lane artifact (recovered = 1.0 for python, 0.0 for the other four languages → mean 1/5).
  The honest, all-language signal: refactor agg_stats `semantic_ok` ≈ 1.0 and flat across W=8..160 (the
  model only RESTRUCTURES) while comprehend agg_stats EM collapses with W (the model must COMPUTE the sum).
- **A held-out re-ranking (Mistral > 70B on Tier B) is suggestive, not significant.** The ~210 Tier-B
  items are only 14 distinct held-out programs ×15 replicates; family-level 95% CI [−0.04, 0.22] includes
  zero, sign-test p=0.18, and one family drives the gap.
- `semantic_ok` is an equivalence **gate** (it credits unchanged-but-equivalent echoes, ~8–11%), not a
  refactoring-quality score — read `recovery`/`simplification_quality` for quality.

**Corrected headline:** models generalize over structural axes but fail one specific computation — list
aggregation/summation — and that single scale- and distribution-invariant failure is what produces the
apparent tier gaps. It is a computation tax, not a novelty penalty.
