# Frozen analysis plan (the zero-API suite, 2026-08)

This file is the registry and constraint set for the statistical analyses that
re-derive numbers from the committed evaluation record. It was frozen alongside
the suite itself: the confirmatory family below was enumerated **before** the
suite's results were read, which is the closest available substitute for
pre-registration in a post-hoc setting, and we state that plainly rather than
claim more. It also binds **future** evaluations on this dataset: downstream
users who want comparable inference should follow the same scheme.

## Ground rules

- **Zero API.** Every suite number re-derives offline from the committed
  archives (`bench/out/{ladder,g3,ablation}/*.jsonl.gz`), the frozen dev data,
  or the committed analysis artifacts. Published numbers are frozen: every
  script embeds kill-switch assertions that reproduce the published figures and
  aborts on mismatch (a failing check means the script is wrong, never the
  published number).
- **Clustering.** The unit of resampling is the **base IR** (50 clusters on the
  dev split; a base sample and its `v0..v4` input variants are one cluster).
  Paired statistics evaluate every model and both corpus conditions on the
  **same** pre-generated cluster resamples (`bench/suite_common.py::
  shared_cluster_draws`, seed 20260814, B=2000).
- **Arm isolation.** `BENCH_STRIP_ANNOTATIONS` is read at import time by
  `bench.tasks`; the refactor metrics `recovery`, `simplification_quality`,
  `uniform_quality` and `spagh_removal` are arm-sensitive (they read the
  spaghetti source). Any analysis touching the unannotated arm with an
  arm-sensitive metric must run in a separate process with the variable set;
  suite scripts refuse to start in a stripped process.
- **Determinism.** Artifacts carry no timestamps and no unseeded randomness; a
  re-run must be byte-identical (part of verification).
- **n-guards.** No empty-list means; every aggregate records its `n`; cells
  below the cluster floor are flagged `illustration_only`, never silently
  pooled.

## Confirmatory family (fixed; exactly 6 p-values)

| # | Hypothesis | Source artifact |
|---|---|---|
| 1 | refactor DiD (Δ_weakest − Δ_strongest) ≠ 0 | `bench/out/ablation_did.json` |
| 2 | comprehend DiD ≠ 0 | `bench/out/ablation_did.json` |
| 3–6 | per-model conditional-quality slope on the incidental knob ≠ 0 (4 models) | `bench/out/quality_slope.json` |

Correction: Benjamini–Hochberg FDR at q = .05 (primary) with Holm–Bonferroni as
sensitivity, computed by `bench/suite_corrections.py` into
`bench/out/suite_corrections.json`. A bootstrap p of 0.0 is below the B=2000
resolution: floored at 1/B for the arithmetic and reported as p < .001.
Everything else in the suite is **estimation with intervals** (no p-values);
the cluster-robust regressions in `ablation_did.json` duplicate the DiD
hypotheses on two scales as cross-checks and are not family members.

## Primary statistics (and what they replace)

- **Differential inflation** = the difference-in-differences of the extreme
  models' ablation deltas, probability scale, clustered bootstrap CI. It
  replaces the ratio of deltas as the headline (the ratio explodes when the
  denominator nears zero; it is still reported, with its CI nulled whenever
  more than 1% of shared draws sign-flip the denominator).
- **Discrimination** = bootstrap concordance P(stronger model's mean > weaker
  model's mean) per adjacent pair per arm on shared draws (continuous),
  replacing the thresholded non-overlapping-interval count as the headline
  (the count remains as the interval-based operational view).
- **Scale note.** Differential inflation is a probability-scale claim (absolute
  score gaps are what interval separation and benchmark consumers compare).
  The log-odds-scale interaction is reported alongside as a scale-dependence
  caveat: near-ceiling models compress on the probability scale.

## Script registry

| Script | Artifact | Purpose | Embedded kill-switches (published figures) |
|---|---|---|---|
| `bench/did_analysis.py` | `ablation_did.json` | DiD, ratio guard, concordance, dual-scale regressions | DiD point = committed delta difference (1e-9); ratio = 10.0 / 3.049; separated pairs concord ≥ .95 |
| `bench/quality_slope_analysis.py` | `quality_slope.json` | incidental-knob slopes on gate / conditional / unconditional quality, Python lane | per-item gate = ablation grade cache |
| `bench/stability_analysis.py` | `stability.json` | k=8 nondeterminism, arm drift diagnostics (descriptive), variant sensitivity | pooled mixed fractions 4.9% / 1.7%; EM = `ladder_comprehend.json` |
| `bench/copy_rate_analysis.py` | `copy_rate.json` | completion↔annotation copy rates by model × arm × correctness | code parts byte-identical modulo comments |
| `bench/partial_credit_analysis.py` | `agg_partial_credit.json` | agg_stats relative error / order-of-magnitude curves over W | exact-total ≥ EM; EM = `ladder_scaling.json` |
| `bench/provenance_figures.py` | `provenance_figures.json` | committed reproduction of 72% / 87% exposure, 500/500 light≡standard, {4:320, 2:180}, 36/100 | all of them |
| `bench/judge_leakage_analysis.py` | `judge_leakage.json`, `judge_leakage_subset.json` | regex-judge leak figures + the leakage-resistant pair subset | 250 / 1268 / 50 / 2.03 / 6.00 / 0.664 / 0.714 / ~0.99 |
| `bench/anchor_subset_analysis.py` | `anchor_subsets.json` | validity correlations on degenerate vs non-degenerate subsets + Kendall τ-b | full-set ρ = anchor.json (+0.75/+0.80/−0.79/+0.39) |
| `bench/compression_anchor.py` | `compression_anchor.json` | zlib ratio + NCD + AST depth as a mechanically distinct anchor family | depth ≥ nesting; positive orientation |
| `bench/freshness_power.py` | `freshness_power.json` | minimum detectable contamination uplift at 80% power | δ=0 rejection ∈ [.02, .09] (miscalibration aborts) |
| `bench/suite_corrections.py` | `suite_corrections.json` | the 6-member family correction | family size = 6 |

Shared substrate: `bench/suite_common.py` (seed 20260814, B=2000, shared
draws, artifact headers, the strip guard).

## Constraints on future evaluations of this dataset

1. Cluster on base IRs; never treat items as independent.
2. Evaluate on the **unannotated** corpus by default (the annotation ablation's
   conclusion); the annotated rendering is the release format, not the
   evaluation format.
3. For the judge task, use the leakage-resistant pair subset
   (`bench/out/judge_leakage_subset.json`) as the standard configuration; the
   full pair set leaks gold rank through marker density.
4. Any scored use of the held-out split starts from a **fresh re-mint** (the
   current mint's literals are disclosed by the released audit completions).
5. Report the freshness comparison with its detectable-uplift bound
   (`freshness_power.json`), not as a bare null.
6. The planned human-calibration study (design in the companion paper's
   validity appendix) remains **designed, not executed**; static-metric
   validity claims stay scoped accordingly.

## Post-hoc addenda (2026-08-14, estimation only)

Registered AFTER the suite's results were read, prompted by an internal review
pass; the sections above are unchanged. These analyses add **no** confirmatory
family members: the family remains the six p-values enumerated above, and the
addenda are reported as estimation with intervals (any bootstrap p recorded in
their artifacts is for completeness, not inference).

| Script | Artifact | Purpose | Embedded kill-switches (published figures) |
|---|---|---|---|
| `bench/did_pairwise.py` | `did_pairwise.json` | pairwise delta DiDs on shared draws (incl. the off-ceiling weakest-vs-Llama-3.3-70B contrast) + a 100-seed sweep of the published rung-separation construction | extreme-pair points = `ablation_did.json` exactly; published-seed counts = `annotation_ablation.json` rungs (1→3 refactor, 0→0 comprehend) |
| `bench/unannotated_slopes.py` | `unannotated_slopes.json` | incidental-knob slopes re-estimated on BOTH arms (the published inertness used the annotated arm only), arm-by-rung interaction, mess-by-width slice, LOFO + per-language robustness | per-model overall means = `annotation_ablation.json` annotated_k1/unannotated_k1 exactly |
| `bench/failure_stages.py` | `failure_stages.json` | failure-stage decomposition of the ablation delta (refactor re-executed with stage detail; comprehend error typology incl. wrong-operation-consistent values), by profile and language | re-derived gate/EM = grade cache exactly (every comprehend item both arms; every re-executed failing refactor item); stage counts sum to cache failure counts |
| `bench/did_robustness.py` | `did_robustness.json` | difficulty-stratified DiDs (two pre-stated interiority rules), rescue/spoil rates, direct paired adjacent-rung tests | extremes DiD points = `ablation_did.json` exactly; paired means = committed per-model mean differences exactly |
| `bench/rep1_extras.py` | `rep1_extras.json` | leakage-free (markers_only) extremes DiDs + the replication's off-ceiling weakest-vs-70B contrast | condition means = `rep1_results.json` exactly |
| `bench/gate_integrity_audit.py` | `gate_integrity.json` | perturbation audit of the one-fixture semantic gate (flip-aware re-minted fixtures substituted into passing completions, re-executed against the perturbed oracle; hardcode/unsubstitutable/uninformative strata explicit) | unperturbed re-execution of a seeded subsample = grade cache exactly |
| `bench/rep1_extras2.py` | `rep1_extras2.json` | per-language replication refactoring DiDs; lying-minus-unannotated cost restricted to unannotated-solved items; per-language paired payload copy-gain on the replication arms (draw 0, `copy_rate_analysis.py` protocol) | condition means = `rep1_results.json` exactly; per-language deltas recompose to `rep1_results.json` per-model deltas (<=1e-9); payload extraction inherits `copy_rate_analysis.py` kills |
| `bench/paper_number_addenda.py` | `paper_number_addenda.json` | fsm prior-conflict miss decomposition (majority-vote slot protocol); discarded percentile-CI rule's null size re-derived on the seeded null resamples; drop-the-duplicate-rung Python token-size counterfactual | zero unattributable fsm draws, 90 records/model; resampling mirrors `freshness_power.py` (SUITE_SEED + sim); tied recomputation = `anchor.json` +0.8844 to 4dp before the counterfactual is reported |
