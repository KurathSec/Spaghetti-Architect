# ablation_v2/ -- the rep1 campaign raw archives

Pre-registered same-week replication + annotation-channel decomposition +
veracity manipulation + CoT-permitted width sweep. Protocol and hypotheses:
`bench/PREREGISTRATION_V2.md` (frozen in the pre-run commit); analysis:
`bench/ablation_v2_analysis.py` -> `bench/out/rep1_results.json`.

40 archives: {refactor x [annotated, unannotated, markers_only, comments_only,
lying], comprehend x [annotated, unannotated, markers_only, comments_only,
cot]} x 4 models, dev split, temperature 0, k=8. Each line is one item:
{sample, variant, profile, language, intrinsic, [tier], raw_outputs[8]}
(+ "prompt_mode": "cot" on the CoT lane), raw completions only, no grades --
every number re-derives offline.

Campaign window (finalize env timestamps, UTC):
  first batch finalized 2026-08-14T20:12:46.980638+00:00
  last batch finalized  2026-08-15T05:48:57.122120+00:00
(The window includes a pause after a provider-side user-set spending limit
tripped mid-campaign (HTTP 402); interrupted batches resumed from per-item
checkpoints, so every item was fetched exactly once. Five items additionally
retried after read timeouts under a raised request timeout; all conditions
completed with zero error stubs.)
