# G3 run — comprehend×test + refactor×{dev,test} on the 4-model open ladder

Compact, gzipped raw model completions (the same packaging as `../ladder/`) so the tables
reproduce with **zero API calls** from version control. Each line is one item:
`{sample, variant, profile, language, intrinsic, tier, raw_outputs}`.

Models (capability order): Meta-Llama-3.1-8B, Mistral-Small-3.2-24B, Llama-3.3-70B, DeepSeek-V4-Flash.
Grid: comprehend on the private **test** split (4 novelty tiers), refactor on **dev** and **test**.

Reproduce: `python3 bench/g3_analysis.py` → re-aggregates the per-item grades into
`bench/out/g3_analysis.json` and prints the tables below.

## Verified findings (adversarial 8-agent re-derivation, 2026-06-29)

Every headline number was independently re-derived from the raw per-item grades (not via the
analysis script) and stress-tested for confounds. Outcomes:

**Confirmed, robust:**
- **No contamination.** comprehend dev EM ≈ test Tier-A EM for all 4 models (|Δ|≤0.011); Tier A is a
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
