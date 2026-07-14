#!/usr/bin/env python3
"""Reproduce the annotation ablation (Table `tab:annotation`) from committed raw outputs.

ZERO API. Both arms are re-graded here from persisted completions; nothing is fetched and
no aggregate written by the live harness is trusted (that path has had bugs, so every
number in the paper is re-derived offline).

WHAT IS BEING ABLATED
---------------------
The generator annotates its own output: a module header declaring it deliberately
redundant, a per-operation comment stating that operation's CLEAN form, and inline
``SPAGH_*`` markers on six of the eleven transforms. Every task prompt interpolates the
source verbatim, so 72% of the labelled optimum's non-comment lines reach the model inside
its own input. ``BENCH_STRIP_ANNOTATIONS=1`` re-renders the identical code with every
comment removed (see ``src/emitter.py``); that is the control corpus.

ARMS
----
annotated    the released runs, re-graded here:
               refactor   bench/out/g3/refactor_dev__<model>.jsonl.gz    (k=8 raw)
               comprehend bench/out/ladder/comprehend__<model>.jsonl.gz  (k=8 raw)
unannotated  bench/out/ablation/<task>_unannotated__<model>.jsonl.gz     (k=1 raw)

WHY k=1 IS COMPARABLE TO THE PUBLISHED k=8
------------------------------------------
Decoding is at temperature 0, so the k=8 draws capture serving-side nondeterminism rather
than independent samples: pooled over the four models, only 4.9% (refactor) / 1.7%
(comprehend) of items have a mixed outcome across their 8 draws. This script therefore grades
the annotated arm at k=1 as well (draw[0] of each stored record) so the arms are matched, and
prints the k=8 rung beside it. They agree to 0.003 or better on every rung, an order of
magnitude below the effect measured.

Not controlled: the annotated arm is the RELEASED run, queried about two weeks before the
unannotated arm, so serving-side drift at the hosted endpoints is a confound. Removing it
would mean re-fetching the annotated arm, which would cost API money and has not been done.

WHAT SURVIVES, AND WHAT DOES NOT
--------------------------------
The whole-corpus REFACTOR result is that the annotation inflates every model, and inflates
weaker models far more than stronger ones. The leave-one-family-out sweep below is the honesty
check on that, and it is not decoration. Three things it kills:

  * the strict monotone ordering of the four refactor deltas is NOT family-robust (dropping
    agg_stats, config_resolver or threshold_select breaks it);
  * the headline rung-separation gain (1/3 -> 3/3) is a whole-corpus effect that dropping
    ALLOWLIST erases entirely (1/3 -> 1/3); three other drops reduce it to 1/3 -> 2/3. It is
    also a threshold on overlapping intervals, so near the boundary it moves with the seed;
  * on COMPREHEND nothing transfers: the weak/strong ratio is 3.0x on the full corpus and
    INVERTS to 0.85x when config_resolver is dropped, and monotonicity fails in 7 of 7 fits.

What IS robust is the refactor gap between the extremes: 6.5x to 9.9x in six of the seven
fits. The seventh (dropping agg_stats) prints 73x, but there the strongest model's delta is
-0.003 with an interval spanning zero, so that ratio is a quotient by noise; the script
reports `all_significant` per fit so this cannot be quoted by accident. Report accordingly.

STATISTICS
----------
The arms score the SAME items, so the comparison is paired. Intervals are a base-IR-clustered
bootstrap (the _vN variants of one IR are correlated), matching the paper's other intervals.

    python3 bench/annotation_ablation.py            # -> bench/out/annotation_ablation.json
    python3 bench/annotation_ablation.py --no-cache # ignore the grade cache and re-grade
"""
from __future__ import annotations

import collections
import gzip
import json
import os
import random
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

if os.environ.get("BENCH_STRIP_ANNOTATIONS", "") not in ("", "0", "false"):
    sys.exit("refusing to run with BENCH_STRIP_ANNOTATIONS set: the annotated arm is "
             "re-graded by rebuilding sources from the dataset, and it needs the "
             "ANNOTATED corpus. Unset it and re-run.")

from bench import tasks as T  # noqa: E402

assert not T.STRIP_ANNOTATIONS, "bench.tasks came up in stripped mode"

SEED = 20260715
BOOTSTRAP = 2000
MODELS = [  # weakest to strongest; the ladder order the paper uses
    ("Llama-3.1-8B", "meta-llama-Meta-Llama-3.1-8B-Instruct"),
    ("Mistral-24B", "mistralai-Mistral-Small-3.2-24B-Instruct-2506"),
    ("Llama-3.3-70B", "meta-llama-Llama-3.3-70B-Instruct-Turbo"),
    ("DeepSeek-V4-Flash", "deepseek-ai-DeepSeek-V4-Flash"),
]
ORDER = [s for s, _ in MODELS]
TASKS = [("refactor", "semantic_ok_rate"), ("comprehend", "exact_match_rate")]
FAMILIES = ["config_resolver", "allowlist", "status_router", "discovery_pipeline",
            "fsm_transition", "agg_stats", "threshold_select"]
OUT = os.path.join(_HERE, "out", "annotation_ablation.json")
CACHE = os.path.join(_HERE, "out", ".annotation_ablation_grades.json")


def _family(sample: str) -> str:
    for f in sorted(FAMILIES, key=len, reverse=True):
        if sample.startswith(f):
            return f
    return "?"


def _base_ir(sample: str) -> str:
    """Bootstrap cluster: the _vN variants of one IR are not independent."""
    return re.sub(r"_v\d+$", "", sample)


def _key(rec: dict) -> str:
    return "|".join([rec["sample"], rec["profile"], rec["language"],
                     rec.get("variant", "base")])


def _grade(recs: List[dict], task: str, key: str, slug: str, k: int) -> Dict[str, float]:
    """Re-grade stored completions through the harness's own graders. No API."""
    fn = T.regrade_refactor_record if task == "refactor" else T.regrade_comprehend_record
    todo = []
    for r in recs:
        raw = r.get("raw_outputs")
        if not raw or raw == ["<mock>"]:
            continue
        r1 = dict(r)
        r1["raw_outputs"] = raw[:k]
        r1.setdefault("snapshot", slug)
        todo.append(r1)
    with ThreadPoolExecutor(max_workers=min(os.cpu_count() or 4, 10)) as ex:
        graded = list(ex.map(fn, todo))
    return {_key(g): g[key] for g in graded if g.get(key) is not None}


def _gz(path: str) -> List[dict]:
    with gzip.open(path, "rt") as fh:
        return [json.loads(line) for line in fh]


def _load_annotated(task: str, slug: str) -> List[dict]:
    if task == "comprehend":
        return _gz(os.path.join(_HERE, "out", "ladder", f"comprehend__{slug}.jsonl.gz"))
    return _gz(os.path.join(_HERE, "out", "g3", f"refactor_dev__{slug}.jsonl.gz"))


def _load_unannotated(task: str, slug: str) -> List[dict]:
    return _gz(os.path.join(_HERE, "out", "ablation",
                            f"{task}_unannotated__{slug}.jsonl.gz"))


def _all_grades(use_cache: bool) -> dict:
    """{task: {model: {'ann1':…, 'ann8':…, 'un1':…}}}. Grading compiles and runs the model's
    code in five languages, so it is the slow part; the cache makes a re-run instant."""
    if use_cache and os.path.exists(CACHE):
        print(f"using cached grades ({CACHE}); pass --no-cache to re-grade", flush=True)
        return json.load(open(CACHE))
    g: dict = {}
    for task, key in TASKS:
        g[task] = {}
        for short, slug in MODELS:
            ann = _load_annotated(task, slug)
            g[task][short] = {
                "ann1": _grade(ann, task, key, slug, k=1),
                "ann8": _grade(ann, task, key, slug, k=8),
                "un1": _grade(_load_unannotated(task, slug), task, key, slug, k=1),
            }
            print(f"  graded {task}/{short}", flush=True)
    with open(CACHE, "w") as fh:
        json.dump(g, fh)
    return g


def _boot(scores: Dict[str, float], rng: random.Random) -> Tuple[float, float, float]:
    clusters = collections.defaultdict(list)
    for k, v in scores.items():
        clusters[_base_ir(k.split("|")[0])].append(v)
    keys = list(clusters)
    draws = []
    for _ in range(BOOTSTRAP):
        vals = [v for k in (rng.choice(keys) for _ in keys) for v in clusters[k]]
        draws.append(sum(vals) / len(vals))
    draws.sort()
    n = sum(len(v) for v in clusters.values())
    mean = sum(sum(v) for v in clusters.values()) / n
    return mean, draws[int(.025 * BOOTSTRAP)], draws[int(.975 * BOOTSTRAP)]


def _rungs_separated(ci: Dict[str, Tuple[float, float, float]]) -> dict:
    pairs = []
    for a, b in zip(ORDER, ORDER[1:]):
        pairs.append({"pair": f"{a} < {b}", "separated": ci[a][2] < ci[b][1]})
    return {"n": sum(p["separated"] for p in pairs), "pairs": pairs}


def main() -> int:
    use_cache = "--no-cache" not in sys.argv
    G = _all_grades(use_cache)
    report: dict = {}

    for task, _key in TASKS:
        rng = random.Random(SEED)
        print(f"\n{'='*78}\n{task.upper()}\n{'='*78}")
        per_model, ci_ann, ci_un = {}, {}, {}
        for short, _slug in MODELS:
            a1, a8, u1 = G[task][short]["ann1"], G[task][short]["ann8"], G[task][short]["un1"]
            common = sorted(set(a1) & set(u1))
            if not common:
                sys.exit(f"no overlapping items for {task}/{short}")
            deltas = {k: u1[k] - a1[k] for k in common}
            ci_ann[short] = _boot({k: a1[k] for k in common}, rng)
            ci_un[short] = _boot({k: u1[k] for k in common}, rng)
            d_mu, d_lo, d_hi = _boot(deltas, rng)
            k8 = sum(a8[k] for k in common if k in a8) / len(common)
            per_model[short] = {
                "n": len(common),
                "annotated_k1": ci_ann[short][0], "annotated_k1_ci95": list(ci_ann[short][1:]),
                "annotated_k8": k8, "k1_vs_k8_gap": abs(ci_ann[short][0] - k8),
                "unannotated_k1": ci_un[short][0], "unannotated_k1_ci95": list(ci_un[short][1:]),
                "delta": d_mu, "delta_ci95": [d_lo, d_hi],
                "delta_significant": d_hi < 0 or d_lo > 0,
            }
            print(f"  {short:18s} annot={ci_ann[short][0]:.3f} (k=8 {k8:.3f}, gap "
                  f"{abs(ci_ann[short][0]-k8):.4f})  unannot={ci_un[short][0]:.3f}  "
                  f"delta={d_mu:+.3f} [{d_lo:+.3f},{d_hi:+.3f}]"
                  f"{'' if (d_hi < 0 or d_lo > 0) else '   NOT SIGNIFICANT'}")

        sep = {"annotated": _rungs_separated(ci_ann), "unannotated": _rungs_separated(ci_un)}
        deltas_all = [per_model[m]["delta"] for m in ORDER]
        monotone = all(deltas_all[i] < deltas_all[i + 1] for i in range(3))
        print(f"\n  rungs separated: annotated {sep['annotated']['n']}/3  "
              f"unannotated {sep['unannotated']['n']}/3   |  deltas monotone: {monotone}")

        # ---- the honesty check: does any single family carry the result? ----
        print(f"\n  leave-one-family-out (is the whole-corpus result family-robust?)")
        print(f"    {'dropped':22s} {'rungs sep. (ann -> unann)':>26s} {'monotone':>9s} "
              f"{'weak/strong ratio':>18s}")
        lofo = {}
        for drop in FAMILIES:
            rng2 = random.Random(SEED)
            ca, cu, dd = {}, {}, {}
            for short, _slug in MODELS:
                a1, u1 = G[task][short]["ann1"], G[task][short]["un1"]
                common = [k for k in sorted(set(a1) & set(u1))
                          if _family(k.split("|")[0]) != drop]
                ca[short] = _boot({k: a1[k] for k in common}, rng2)
                cu[short] = _boot({k: u1[k] for k in common}, rng2)
                dd[short] = _boot({k: u1[k] - a1[k] for k in common}, rng2)
            ds = [dd[m][0] for m in ORDER]
            mono = all(ds[i] < ds[i + 1] for i in range(3))
            ratio = abs(ds[0] / ds[-1]) if ds[-1] else float("inf")
            sa, su = _rungs_separated(ca)["n"], _rungs_separated(cu)["n"]
            lofo[drop] = {"rungs_annotated": sa, "rungs_unannotated": su,
                          "monotone": mono, "weak_over_strong_ratio": ratio,
                          "deltas": {m: dd[m][0] for m in ORDER},
                          "delta_ci95": {m: list(dd[m][1:]) for m in ORDER},
                          "all_significant": all(dd[m][2] < 0 or dd[m][1] > 0 for m in ORDER)}
            print(f"    -{drop:21s} {sa}/3 -> {su}/3{'':>15s} {str(mono):>9s} "
                  f"{ratio:17.1f}x")

        report[task] = {"per_model": per_model, "rungs_separated": sep,
                        "deltas_monotone": monotone,
                        "weak_over_strong_ratio": abs(deltas_all[0] / deltas_all[-1]),
                        "leave_one_family_out": lofo}

    with open(OUT, "w") as fh:
        json.dump(report, fh, indent=2)
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
