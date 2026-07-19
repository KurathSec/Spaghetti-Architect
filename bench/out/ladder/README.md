# Capability-ladder raw outputs (reproduction artifact)

Compact, gzipped raw model completions for the comprehension **capability ladder** reported
in the paper's baselines section (Tables `tab:ladder` and `tab:scaling`). One file per model,
`comprehend__<model-id-with-slashes-as-dashes>.jsonl.gz`: one JSON record per
`(sample, variant, profile, language)` item (1500 per file: the base rendering plus
input-perturbation variants `v0`-`v4` on a subset of programs), each with its `variant`,
`k=8` `raw_outputs`, and `intrinsic` scale.

Reproduce both tables with **zero API calls**:

```
python3 bench/ladder_analysis.py
#  -> bench/out/ladder_comprehend.json   overall + per-family exact match   (Table tab:ladder)
#  -> bench/out/ladder_scaling.json       exact match vs intrinsic scale     (Table tab:scaling)
```

Run: four open models served through one OpenAI-compatible endpoint (DeepInfra) —
`meta-llama/Meta-Llama-3.1-8B-Instruct`, `mistralai/Mistral-Small-3.2-24B-Instruct-2506`,
`meta-llama/Llama-3.3-70B-Instruct-Turbo`, `deepseek-ai/DeepSeek-V4-Flash` — `k=8`,
`temperature=0`, public `dev` split, all 7 families. Graded by exact match against the oracle
output (`bench/tasks.py::_grade_comprehend_outputs`); validated to match the live harness
grader on a clean batch (DeepSeek-V4-Flash: 0.8385 == 0.8385).
