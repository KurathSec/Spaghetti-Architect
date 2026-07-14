# Annotation ablation: raw completions

The generator annotates its own output: a module header declaring it deliberately
redundant, a per-operation comment stating that operation's **clean** form, and inline
`SPAGH_*` markers on six of the eleven transforms. Every task prompt interpolates the
source verbatim, so 72% of the labelled optimum's computation reaches the model inside its
own input.

These are the raw completions for the **control** arm: the same programs re-rendered with
every comment removed (`BENCH_STRIP_ANNOTATIONS=1`, i.e. `Engine(annotate=False)`). The
code is byte-identical modulo comments and passes the oracle in all five languages.

* 4 models x 2 tasks, public `dev` split, 1500 items each, k=1, 0 API errors, 0 dropped items.
* Same packaging as `../ladder/` and `../g3/`: gzipped JSONL, **raw completions only**, no
  graded fields. Every number in the paper is re-derived offline from these.

The annotated arm is not duplicated here. It is re-graded from the already-committed
`../ladder/comprehend__*.jsonl.gz` and `../g3/refactor_dev__*.jsonl.gz`.

Reproduce the paper's Table `tab:annotation`, including the leave-one-family-out robustness
sweep (zero API):

    python3 bench/annotation_ablation.py

## What the result is, and what it is not

On refactoring, the annotations inflate every model, and inflate the weakest model in the
panel an order of magnitude more than the strongest (semantic-equivalence delta -0.173
against -0.017, every interval excluding zero). Because that inflation is differential
rather than uniform it compresses the ladder: on the full corpus the annotated refactoring
baselines resolve 1 of 3 adjacent model pairs, and the unannotated ones resolve 3 of 3.

Two things are **not** claimed, and the leave-one-family-out sweep is why:

* The strict monotone ordering of the four deltas is **not** family-robust. It breaks in 3
  of the 7 leave-one-out fits (dropping `agg_stats`, `config_resolver` or
  `threshold_select`).
* The 1/3 to 3/3 rung-separation gain is a **whole-corpus** effect, not a law, and it is the
  least stable number here. Rung separation is a threshold on overlapping intervals, so near
  the boundary it moves with the bootstrap draw itself. Dropping `allowlist` erases the gain
  entirely. Only its *direction* is stable: no leave-one-out fit makes the unannotated ladder
  resolve *fewer* rungs than the annotated one.

What survives every leave-one-out is the ratio between the extremes: the weakest model's
inflation stays 6.5x to 73x the strongest model's.

Two design caveats. The ablation is at k=1 (temperature is 0, and re-grading the released
k=8 runs at k=1 reproduces every published rung to 0.003 or better, so we take the arms to
be comparable, but this is an argument and not a k=8 replication). And the two arms were
queried about two weeks apart, so serving-side drift at the hosted endpoints is not
controlled.
