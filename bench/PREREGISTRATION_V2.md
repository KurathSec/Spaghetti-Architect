# Pre-registration: the rep1 campaign (same-week ablation replication, annotation-channel decomposition, veracity manipulation, CoT-permitted width sweep)

**Status: FROZEN at the commit that introduces this file. The commit predates
every API call of the campaign; the git hash is the timestamp. Nothing in this
file may change after the run starts; deviations are reported as deviations.**

This campaign is separate from, and adds nothing to, the published frozen
analysis plan (`ANALYSIS_PLAN.md`): the published six-member confirmatory
family and every published number stay exactly as they are. The campaign has
its OWN confirmatory family (H1–H6 below, Holm-corrected within this family,
alpha = .05) and its own analysis script, committed in the same pre-run commit.

## Design

Four open models (`meta-llama/Meta-Llama-3.1-8B-Instruct`,
`mistralai/Mistral-Small-3.2-24B-Instruct-2506`,
`meta-llama/Llama-3.3-70B-Instruct-Turbo`, `deepseek-ai/DeepSeek-V4-Flash`;
weakest-to-strongest ladder order as published), public `dev` split (100
instances × 3 profiles × 5 languages = 1500 items/task), temperature 0,
**k = 8 for every condition**, one batch window (target: one day; the window's
start/end timestamps are recorded in the artifact). All batches run under
`BENCH_RUN_TAG=rep1`, which namespaces every output beside (never over) the
published artifacts.

Conditions (renderings validated pre-run: code parts byte-identical to the
annotated corpus over all 1500 cells per condition; the channel pair
partitions the full annotation set exactly; the falsified intent lines differ
from the true ones in 3855/3855 op instances; oracle sample 150/150 PASS):

| Condition | Renders | Tasks |
|---|---|---|
| `annotated` (re-fetch) | header + intent comments + SPAGH markers (the released corpus) | refactor, comprehend |
| `unannotated` | no comments | refactor, comprehend |
| `markers_only` | header + markers + scaffolding, NO intent comments | refactor, comprehend |
| `comments_only` | intent comments ONLY | refactor, comprehend |
| `lying` | full rendering, intent payload deterministically falsified (aggregate mode cycled, membership negated, conditional branches swapped, lookup fallback replaced), code untouched | refactor |
| CoT lane | annotated corpus, `comprehend_cot` prompt (`bench-prompts-cot-v1`), `agg_stats` family only, max_tokens 4096 | comprehend |

## Confirmatory hypotheses (evaluated mechanically by `bench/ablation_v2_analysis.py`)

All deltas are same-week paired per-item differences (condition minus
annotated re-fetch, k=8 means), aggregated with base-IR-clustered bootstrap on
shared draws (B = 2000, seed 20260814), exactly the published construction.
"DiD" = delta(weakest) − delta(strongest).

- **H1** Same-week refactoring DiD of the unannotated condition < 0, 95% CI
  excluding 0.
- **H2** Same-week comprehension DiD of the unannotated condition < 0, 95% CI
  excluding 0.
- **H3** Unannotated incidental-knob slopes (knob rank on minimal<standard<max,
  same clustering) negative in sign in >= 6 of 8 model × task cells, including
  all four comprehension cells.
- **H4** (intent dominance) The weakest model scores HIGHER under
  `comments_only` than under `markers_only` (same-week paired per-item
  difference > 0, 95% CI excluding 0), on both tasks: the condition that
  retains the reference-stating intent comment retains more of the
  annotation benefit than the condition that retains only header+markers.
- **H5** (the naming-only channel is real) The weakest model scores HIGHER
  under `markers_only` than under `unannotated` on refactoring (paired
  difference > 0, 95% CI excluding 0): header+markers still inflate without
  any intent comment. Genuinely uncertain; a null here NARROWS the paper's
  self-documentation framing to reference-stating comments.
- **H6** The weakest model's lying-condition refactoring score < its
  unannotated score (95% CI of the paired difference excluding 0), AND the
  weakest model's lying-minus-unannotated drop exceeds the strongest model's
  (point estimates).

Multiplicity: Holm over the six CI/sign statements above (each hypothesis'
p is the shared-draw bootstrap two-sided p of its primary contrast; sign-count
H3 is assessed as stated, its p taken as the largest of its four comprehension
slope p-values). Everything else the analysis reports (channel sub-additivity,
level shifts, rung separations under both criteria, wrong-form copy rates,
CoT table) is estimation with intervals, no p-claims.

## Magnitude bands (context, not pass/fail)

- Replication DiD expected in [−0.28, −0.04] (published −0.156; attenuation
  tolerated).
- Per-model annotated-arm level shift vs the published run: |shift| <= 0.05
  expected; larger shifts are disclosed as serving drift and the paired
  same-week contrasts remain the primary statistics.

## Protocol notes (fixed pre-run)

- Live batch aggregates are never used; every number re-derives offline from
  the persisted raw completions (`bench/ablation_v2_analysis.py`), with the
  live per-item grades used only as a cross-check.
- CoT lane grading: `grade.extract_json_obj` (first balanced object) is wrong
  under chain-of-thought and is NOT used; the analysis script's scorer
  extracts the JSON inside the last `<answer>...</answer>` tag pair (fallback:
  last balanced JSON object), then applies the published `_match` comparison.
  Completions with no closing tag are counted as `truncated_or_noncompliant`;
  batch-level `finish_reason` tallies corroborate. CoT batches run with
  `--parse-floor 0.05` (the live parse signal uses first-JSON and is not
  meaningful for this lane).
- The lying condition is prompted identically to `annotated` (the prompt never
  mentions comments); its grading is the standard semantic gate. A wrong-form
  copy analysis (the falsified payloads as needles, the copy_rate machinery)
  is estimation.
- Endpoint pre-flight: 3-item k=1 pilots per model (write nothing). If a model
  slug is no longer served, the substitution is recorded HERE by an amendment
  commit BEFORE the main run and disclosed.
- Hard caps: per-batch `--max-cost`; campaign stop at cumulative $220.

## Deviations

Recorded after the campaign (none touches a hypothesis, a statistic, or the
analysis script):

1. Operational per-batch `--max-cost` was raised from the runner's initial $8
   to $12 (and $16 for DeepSeek-V4-Flash batches) after the harness's
   worst-case projections refused the pricier models' batches. The
   pre-registered cap is the cumulative $220 stop; actual campaign spend was
   about $151 (the DeepInfra dashboard is the authoritative ledger; resumed
   batches overwrite their finalize `est_usd` with the resume's marginal cost).
2. A provider-side user-set spending limit tripped mid-campaign (HTTP 402).
   Per-item checkpoints held; after the account limit was raised, the four
   affected batches resumed and refetched only their error stubs (no item was
   fetched twice).
3. Six items (five Llama CoT/lying cells, one more on retry) failed on read
   timeouts; they were refetched under a temporarily raised
   `request_timeout_s` (300s, then 420s for one Llama-3.3-70B CoT item),
   restored to 120s afterwards.
4. The campaign window is 2026-08-14T20:12Z to 2026-08-15T05:48Z (9.6 hours,
   within the same-day target; window and interruption history are also in
   `bench/out/ablation_v2/README.md`).

## Outcome (mechanical verdicts from bench/out/rep1_results.json)

H1 CONFIRMED (refactor DiD -0.096 [-0.164, -0.038]); H2 not confirmed
(comprehension DiD -0.043 [-0.141, +0.052]; note the weakest model's
annotated comprehension arm shifted -0.112 vs the published run, the one
level shift outside the expected band -- disclosed as serving drift, and the
reason the paired same-week contrast is the primary statistic); H3 CONFIRMED
(unannotated knob slopes negative in sign in 8/8 cells, 7/8 with intervals
excluding zero, all four comprehension cells significant; the criterion is
sign, as frozen above); H4 refactor leg significant (comments_only beats markers_only
for the weakest, +0.043 [+0.013, +0.078]), comprehension leg directionally
positive but n.s., so H4 as pre-registered (both tasks) NOT confirmed; H5
CONFIRMED (+0.043 [+0.010, +0.076], Holm-adjusted p = .04: the naming-only
channel is real); H6 not confirmed at the CI criterion (weakest lying minus
unannotated -0.106 [-0.213, +0.002], p = .056; the capability ordering holds
and the contrast is reported as estimation).
