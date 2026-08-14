"""Post-hoc pairwise DiDs and a rung-separation seed sweep. Zero API.

Prompted by an internal review pass (2026-08-14), two questions the committed
record can answer without a single new query: (a) does the differential
inflation survive when the near-ceiling strongest model is excluded entirely,
i.e. is the headline carried by one near-ceiling cell, and (b) how stable is
the published 1/3 -> 3/3 rung-separation count under the bootstrap seed itself
(its leave-one-family-out sensitivity is already documented)?

These are POST-HOC ADDENDA: they were registered in ANALYSIS_PLAN.md after the
suite's results were read, they add no members to the pre-declared six-test
confirmatory family, and the paper reports them as estimation with intervals
only (p_bootstrap is recorded here for completeness, not for inference).

Part A recomputes the delta DiD for every model pair on the SAME shared
base-IR cluster resamples as bench/did_analysis.py, so all six contrasts are
paired and mutually comparable. Part B replicates
bench/annotation_ablation.py's exact published construction (three sequential
_boot calls per model from one rng: annotated, unannotated, delta; k=1 both
arms; adjacent-CI overlap) across 100 fresh bootstrap seeds.

Verification kill-switches (the published numbers are frozen; a mismatch means
THIS script is wrong): the extreme-pair DiD points must equal the committed
ablation_did.json points exactly, and re-running Part B's construction at the
published annotation_ablation SEED must reproduce the committed
annotation_ablation.json rungs_separated counts in both tasks and both arms.
"""

from __future__ import annotations

import collections
import json
import os
import random
import sys
from typing import Dict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bench.annotation_ablation import (  # noqa: E402
    MODELS,
    ORDER,
    SEED as PUBLISHED_SEED,
    TASKS,
    _all_grades,
    _boot,
    _rungs_separated,
)
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

OUT = os.path.join(_HERE, "out", "did_pairwise.json")
COMMITTED_DID = os.path.join(_HERE, "out", "ablation_did.json")
COMMITTED_ABLATION = os.path.join(_HERE, "out", "annotation_ablation.json")

N_SEEDS = 100
SEED_BASE = 1000003  # fresh stream, far from PUBLISHED_SEED and SUITE_SEED


def _per_item_deltas(G: dict, task: str) -> Dict[str, Dict[str, float]]:
    """{model: {item_key: unannotated_k1 - annotated_k1}} on the common items."""
    out: Dict[str, Dict[str, float]] = {}
    for short, _slug in MODELS:
        a1, u1 = G[task][short]["ann1"], G[task][short]["un1"]
        common = sorted(set(a1) & set(u1))
        if not common:
            sys.exit(f"no overlapping items for {task}/{short}")
        out[short] = {k: u1[k] - a1[k] for k in common}
    return out


def _pairwise_did(G: dict) -> dict:
    res: dict = {}
    for task, _key in TASKS:
        deltas = _per_item_deltas(G, task)
        clus = {m: cluster_scores(deltas[m]) for m in deltas}
        keys = sorted(set().union(*[set(c) for c in clus.values()]))
        draws = shared_cluster_draws(keys, BOOTSTRAP, SUITE_SEED)
        dmeans = {m: draw_means(clus[m], draws) for m in clus}
        points = {m: mean(list(deltas[m].values())) for m in deltas}
        pairs: dict = {}
        for i in range(len(ORDER)):
            for j in range(i + 1, len(ORDER)):
                weaker, stronger = ORDER[i], ORDER[j]
                dd = [dmeans[weaker][t] - dmeans[stronger][t]
                      for t in range(BOOTSTRAP)]
                pairs[f"{weaker} minus {stronger}"] = {
                    "point": points[weaker] - points[stronger],
                    "ci95": percentile_ci95(dd),
                    "p_bootstrap": two_sided_bootstrap_p(dd),
                }
        res[task] = {"n_clusters": len(keys), "pairs": pairs}
        print(f"pairwise DiDs: {task} done ({len(keys)} clusters)", flush=True)
    return res


def _separation_counts(G: dict, task: str, seed: int) -> Dict[str, int]:
    """The published per-arm construction, verbatim: one rng, and per model
    three sequential _boot calls (annotated, unannotated, delta) so the RNG
    stream consumption matches bench/annotation_ablation.py exactly."""
    rng = random.Random(seed)
    ci_ann, ci_un = {}, {}
    for short, _slug in MODELS:
        a1, u1 = G[task][short]["ann1"], G[task][short]["un1"]
        common = sorted(set(a1) & set(u1))
        ci_ann[short] = _boot({k: a1[k] for k in common}, rng)
        ci_un[short] = _boot({k: u1[k] for k in common}, rng)
        _boot({k: u1[k] - a1[k] for k in common}, rng)  # delta interval, third in the published order
    return {"annotated": _rungs_separated(ci_ann)["n"],
            "unannotated": _rungs_separated(ci_un)["n"]}


def _seed_sweep(G: dict) -> dict:
    res: dict = {}
    for task, _key in TASKS:
        hist: collections.Counter = collections.Counter()
        direction_ok = 0
        for s in range(N_SEEDS):
            c = _separation_counts(G, task, SEED_BASE + s)
            hist[f"{c['annotated']}->{c['unannotated']}"] += 1
            direction_ok += c["unannotated"] >= c["annotated"]
            if (s + 1) % 20 == 0:
                print(f"seed sweep: {task} {s + 1}/{N_SEEDS}", flush=True)
        res[task] = {
            "n_seeds": N_SEEDS,
            "seed_base": SEED_BASE,
            "histogram_annotated_to_unannotated": dict(sorted(hist.items())),
            "frac_unannotated_ge_annotated": direction_ok / N_SEEDS,
        }
    return res


def main() -> int:
    G = _all_grades(True)
    committed_did = json.load(open(COMMITTED_DID))
    committed_abl = json.load(open(COMMITTED_ABLATION))

    pairwise = _pairwise_did(G)

    # KILL 1: the extreme-pair point must equal the committed DiD point exactly.
    extreme = f"{ORDER[0]} minus {ORDER[-1]}"
    for task, _key in TASKS:
        ours = pairwise[task]["pairs"][extreme]["point"]
        theirs = committed_did[task]["did"]["point"]
        assert ours == theirs, (
            f"KILL 1: {task} extreme DiD {ours!r} != committed {theirs!r}")
    print("KILL 1 passed: extreme-pair points equal ablation_did.json exactly")

    # KILL 2: the published construction at the published seed must reproduce
    # the committed rung-separation counts in both tasks and both arms.
    published_check: dict = {}
    for task, _key in TASKS:
        c = _separation_counts(G, task, PUBLISHED_SEED)
        for arm in ("annotated", "unannotated"):
            want = committed_abl[task]["rungs_separated"][arm]["n"]
            assert c[arm] == want, (
                f"KILL 2: {task}/{arm} at published seed: {c[arm]} != {want}")
        published_check[task] = c
    print("KILL 2 passed: published-seed counts equal annotation_ablation.json")

    sweep = _seed_sweep(G)

    report = {
        "_meta": artifact_header(
            "did_pairwise.py",
            ["bench/out/ablation/*.jsonl.gz",
             "bench/out/ablation_did.json",
             "bench/out/annotation_ablation.json",
             "bench/out/g3/refactor_dev__*.jsonl.gz",
             "bench/out/ladder/*.jsonl.gz"],
            shared_draws=True,
            estimation_not_family=True,
            post_hoc=("registered 2026-08-14 after the suite's results were "
                      "read, prompted by an internal review pass; adds no "
                      "confirmatory family members (ANALYSIS_PLAN.md)"),
        ),
        "pairwise_did": pairwise,
        "seed_sweep": sweep,
        "published_seed_reproduction": {
            "seed": PUBLISHED_SEED,
            "counts": published_check,
        },
    }
    write_artifact(OUT, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
