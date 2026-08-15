"""Two estimation-only extras over the committed rep1 grades. Zero API.

Post-hoc addendum (ANALYSIS_PLAN.md addenda; outside every confirmatory
family), prompted by an internal review pass on the integrated draft:

(a) the LEAKAGE-FREE differential: per-model same-week deltas of the
    markers_only rendering versus the unannotated one (header+markers present,
    no clean-form intent comment anywhere), and their weakest-minus-strongest
    DiD on shared base-IR cluster draws -- if this DiD is negative, the
    differential inflation is not reducible to reference-solution leakage;
    the comments_only analogue is reported for completeness;
(b) the OFF-CEILING replication DiD: weakest-vs-Llama-3.3-70B of the
    replication's unannotated-minus-annotated deltas (the same-week analogue
    of the published off-ceiling pairwise contrast in did_pairwise.json).

Inputs: bench/out/.rep1_grades.json (the campaign's offline k=8 grades).
Kill-switch: per-condition per-model means recomputed here must equal the
committed rep1_results.json `conditions` block exactly.
"""

from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bench.annotation_ablation import MODELS, ORDER  # noqa: E402
from bench.suite_common import (  # noqa: E402
    BOOTSTRAP,
    SUITE_SEED,
    artifact_header,
    cluster_scores,
    draw_means,
    mean,
    percentile_ci95,
    shared_cluster_draws,
    two_sided_bootstrap_p,
    write_artifact,
)

GRADES = os.path.join(_HERE, "out", ".rep1_grades.json")
RESULTS = os.path.join(_HERE, "out", "rep1_results.json")
OUT = os.path.join(_HERE, "out", "rep1_extras.json")

WEAKEST, STRONGEST = ORDER[0], ORDER[-1]


def _did(deltas: dict, a: str, b: str) -> dict:
    keys = sorted(set().union(*[set(cluster_scores(d)) for d in
                                deltas.values()]))
    draws = shared_cluster_draws(keys, BOOTSTRAP, SUITE_SEED)
    dm = {m: draw_means(cluster_scores(deltas[m]), draws) for m in deltas}
    dd = [dm[a][t] - dm[b][t] for t in range(BOOTSTRAP)]
    pts = {m: mean(list(deltas[m].values())) for m in deltas}
    return {"point": pts[a] - pts[b], "ci95": percentile_ci95(dd),
            "p_bootstrap": two_sided_bootstrap_p(dd),
            "per_model_delta": {m: pts[m] for m in ORDER}}


def main() -> int:
    G = json.load(open(GRADES))
    committed = json.load(open(RESULTS))

    # KILL: recomputed condition means equal rep1_results.json exactly.
    n = 0
    for task in ("refactor", "comprehend"):
        for cond, per in committed["conditions"][task].items():
            for m, _slug in MODELS:
                got = mean(list(G[task][cond][m].values()))
                want = per[m]["score"]["mean"]
                assert got == want, (
                    f"KILL: {task}/{cond}/{m} mean {got!r} != {want!r}")
                n += 1
    print(f"KILL passed: {n} condition means equal rep1_results.json exactly")

    report: dict = {"leakage_free_did": {}, "off_ceiling_replication": {}}
    for task in ("refactor", "comprehend"):
        un = G[task]["unannotated"]
        for cond in ("markers_only", "comments_only"):
            deltas = {m: {k: G[task][cond][m][k] - un[m][k]
                          for k in set(G[task][cond][m]) & set(un[m])}
                      for m in ORDER}
            report["leakage_free_did"].setdefault(task, {})[cond] = _did(
                deltas, WEAKEST, STRONGEST)
        rep_deltas = {m: {k: un[m][k] - G[task]["annotated"][m][k]
                          for k in set(un[m]) & set(G[task]["annotated"][m])}
                      for m in ORDER}
        report["off_ceiling_replication"][task] = _did(
            rep_deltas, WEAKEST, "Llama-3.3-70B")
        lf = report["leakage_free_did"][task]["markers_only"]
        oc = report["off_ceiling_replication"][task]
        print(f"{task:10s} markers_only DiD {lf['point']:+.4f} "
              f"[{lf['ci95'][0]:+.3f},{lf['ci95'][1]:+.3f}] p={lf['p_bootstrap']}"
              f" | off-ceiling repl DiD {oc['point']:+.4f} "
              f"[{oc['ci95'][0]:+.3f},{oc['ci95'][1]:+.3f}] p={oc['p_bootstrap']}")

    report["_meta"] = artifact_header(
        "rep1_extras.py",
        ["bench/out/.rep1_grades.json", "bench/out/rep1_results.json"],
        shared_draws=True,
        estimation_not_family=True,
        post_hoc=("registered 2026-08-15 after the integrated draft's review "
                  "pass; estimation only (ANALYSIS_PLAN.md addenda)"),
    )
    write_artifact(OUT, report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
