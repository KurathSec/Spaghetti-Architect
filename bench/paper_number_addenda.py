"""Three paper-number addenda with no prior standalone artifact. Zero API.

Post-hoc, estimation only (ANALYSIS_PLAN.md addenda). A mechanical
number-bindings audit of the paper (2026-08-22) found three printed numbers
whose only source was outside bench/out/*.json. This script re-derives each
from committed (or committed-archive-derived) data and freezes them:

(a) FSM PRIOR-CONFLICT DECOMPOSITION: within the fsm_transition family's
    comprehension misses, the fraction landing on result slots whose oracle
    value is the lookup DEFAULT ("ERR", a missing transition), i.e. where the
    plausible-next-state prior contradicts the code. Protocol: per
    (model, record, slot) over the committed ladder archives, a slot is a
    miss if MORE THAN HALF of the k=8 parsed right-keyed draws mismatch the
    oracle (majority vote). Published claim: 97% pooled, majority per model.
(b) DISCARDED PERCENTILE-CI RULE, NULL SIZE: the freshness power analysis
    replaced an inner percentile-CI detector with an exact permutation test;
    the discarded rule's null rejection rate is recomputed here on the same
    seeded null resamples (dev-vs-dev, delta=0, SIMS x refactor cells) with a
    500-draw inner cluster bootstrap. Published claim: null sizes up to 0.12
    on the skewed near-ceiling refactor arms.
(c) DROP-THE-DUPLICATE-RUNG COUNTERFACTUAL: the spec-2.0 Python lizard
    token-count Spearman vs the knob, recomputed with the inert standard rung
    excluded from the rank vector (5 levels instead of the tied 6).
    Published claim: +0.88 -> +0.92.

Inputs: bench/out/ladder/comprehend__*.jsonl.gz (committed), the dev split
(committed, rendered deterministically), the ablation grade cache
(re-derivable from committed archives), bench/out/anchor.json and
bench/out/g3_analysis.json (kill-switch cross-checks). Requires the metrics
venv for lizard (block c).

Kill-switches (a failure means THIS script is wrong):
  * (a) zero unattributable fsm draws; slot totals = 4 slots x 90 records
        per model; the per-model miss counts under the all-draws protocol
        must sum to the same misses the archives contain;
  * (b) the resampling stream reproduces freshness_power.py exactly
        (Random(SUITE_SEED + s), choices with k=n_clusters); dev cluster
        grids are 1500 items / 50 clusters per cell;
  * (c) the tied 6-level recomputation must reproduce anchor.json's
        published +0.8844 mean to 4 decimals before the counterfactual is
        reported; run twice, byte-identical.
"""

from __future__ import annotations

import gzip
import json
import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bench.annotation_ablation import MODELS  # noqa: E402
from bench.suite_common import (  # noqa: E402
    SUITE_SEED,
    all_grades,
    artifact_header,
    cluster_scores,
    mean,
    percentile_ci95,
    write_artifact,
)
from bench import dataset as D  # noqa: E402
from bench import grade as G  # noqa: E402

OUT = os.path.join(_HERE, "out", "paper_number_addenda.json")
SIMS = 400
INNER_B = 500


# --------------------------------------------------------------------------- #
# (a) fsm prior-conflict decomposition
# --------------------------------------------------------------------------- #
def fsm_prior_conflict(sp) -> dict:
    conflict, exp_of = {}, {}
    for it in sp.items:
        if not it.stem.startswith("fsm"):
            continue
        exp = G.oracle(sp.program(it.stem))
        ir = sp.ir(it.stem)
        dv = {op["result_var"]: op.get("default_value")
              for op in ir["operations"]
              if op["operation"] == "KEY_VALUE_LOOKUP"}
        conflict[it.stem] = {k: (exp[k] == dv.get(k)) for k in exp}
        exp_of[it.stem] = exp
    stem_of = {(it.sample, it.variant): it.stem for it in sp.items}

    per_model, pooled = {}, {"conflict": 0, "total": 0}
    n_unattributable = 0
    for short, slug in MODELS:
        path = os.path.join(_HERE, "out", "ladder",
                            f"comprehend__{slug}.jsonl.gz")
        conf = tot = n_rec = 0
        with gzip.open(path, "rt") as fh:
            for line in fh:
                r = json.loads(line)
                if not r["sample"].startswith("fsm"):
                    continue
                n_rec += 1
                stem = stem_of[(r["sample"], r.get("variant", "base"))]
                exp, cf = exp_of[stem], conflict[stem]
                slotmiss = {k: 0 for k in exp}
                n_ok = 0
                for raw in r["raw_outputs"]:
                    pred = G.extract_json_obj(raw)
                    if pred is None or set(pred) != set(exp):
                        n_unattributable += 1
                        continue
                    n_ok += 1
                    for k in exp:
                        if json.dumps(pred[k], sort_keys=True) != \
                                json.dumps(exp[k], sort_keys=True):
                            slotmiss[k] += 1
                for k in exp:
                    if n_ok and slotmiss[k] * 2 > n_ok:  # majority vote
                        tot += 1
                        conf += cf[k]
        if n_rec != 90:
            sys.exit(f"KILL: {short}: {n_rec} fsm records != 90")
        per_model[short] = {"conflict_misses": conf, "total_misses": tot,
                            "fraction": conf / tot if tot else None}
        pooled["conflict"] += conf
        pooled["total"] += tot
    if n_unattributable:
        sys.exit(f"KILL: {n_unattributable} unattributable fsm draws != 0")
    return {"protocol": "per (model, record, slot); miss = strict majority "
                        "of the k=8 parsed right-keyed draws mismatch the "
                        "oracle; conflicting slot = oracle value equals the "
                        "lookup default_value",
            "per_model": per_model,
            "pooled": {"conflict_misses": pooled["conflict"],
                       "total_misses": pooled["total"],
                       "fraction": pooled["conflict"] / pooled["total"]},
            "majority_for_every_model": all(
                m["fraction"] > 0.5 for m in per_model.values())}


# --------------------------------------------------------------------------- #
# (b) discarded percentile-CI rule: null size on the refactor cells
# --------------------------------------------------------------------------- #
def percentile_rule_null(grades) -> dict:
    cells = {}
    for short, _slug in MODELS:
        scores = grades["refactor"][short]["ann8"]
        if len(scores) != 1500:
            sys.exit(f"KILL: refactor/{short} dev grid {len(scores)} != 1500")
        cl = cluster_scores(scores)
        if len(cl) != 50:
            sys.exit(f"KILL: refactor/{short} {len(cl)} clusters != 50")
        keys = sorted(cl)
        sums = [float(sum(cl[k])) for k in keys]
        counts = [len(cl[k]) for k in keys]
        n = len(keys)
        rejected = 0
        for s in range(SIMS):
            rng = random.Random(SUITE_SEED + s)       # mirrors freshness_power
            idx1 = rng.choices(range(n), k=n)
            cm1 = [sums[i] / counts[i] for i in idx1]
            idx2 = rng.choices(range(n), k=n)
            cm2 = [sums[i] / counts[i] for i in idx2]
            irng = random.Random(SUITE_SEED * 1000 + s)
            diffs = []
            for _ in range(INNER_B):
                d1 = mean([cm1[irng.randrange(n)] for _ in range(n)])
                d2 = mean([cm2[irng.randrange(n)] for _ in range(n)])
                diffs.append(d2 - d1)
            lo, hi = percentile_ci95(diffs)
            if lo > 0.0 or hi < 0.0:
                rejected += 1
        cells[short] = {"null_size": rejected / SIMS, "sims": SIMS,
                        "inner_bootstrap": INNER_B}
    return {"detector": "95% percentile CI of the cluster-mean difference "
                        "over a 500-draw inner cluster bootstrap; reject if "
                        "0 excluded; null = dev-vs-dev resamples, "
                        "Random(SUITE_SEED + sim) as in freshness_power.py",
            "cells": cells,
            "max_null_size": max(c["null_size"] for c in cells.values())}


# --------------------------------------------------------------------------- #
# (c) drop-the-duplicate-rung token-size counterfactual (Python, lizard)
# --------------------------------------------------------------------------- #
def token_counterfactual(sp, committed_mean: float) -> dict:
    """Python lizard token count vs the knob, spec 2.0.

    Published convention (reproduces anchor.json to 4 dp): the five non-clean
    profiles with DISTINCT x-ranks 0..4; the light/standard tie lives in the
    data (byte-identical renders), which is what attenuates rho. The
    counterfactual drops the duplicate standard rung (4 ranks)."""
    import lizard  # metrics venv
    from src.engine import Engine
    profiles = ["minimal", "light", "standard", "heavy", "max"]
    engines = {k: Engine(D.DB, k) for k in profiles}

    def spearman(xs, ys):
        def rank(v):
            order = sorted(range(len(v)), key=lambda i: v[i])
            r = [0.0] * len(v)
            i = 0
            while i < len(order):
                j = i
                while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                    j += 1
                avg = (i + j) / 2.0 + 1.0
                for t in range(i, j + 1):
                    r[order[t]] = avg
                i = j + 1
            return r
        rx, ry = rank(xs), rank(ys)
        mx, my = mean(rx), mean(ry)
        num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
        dx = sum((a - mx) ** 2 for a in rx) ** 0.5
        dy = sum((b - my) ** 2 for b in ry) ** 0.5
        return num / (dx * dy) if dx and dy else 0.0

    base_stems = sorted({it.stem for it in sp.items
                         if it.variant in (None, "", "base")})
    if len(base_stems) != 50:
        sys.exit(f"KILL: {len(base_stems)} base stems != 50")
    rhos_tied, rhos_drop = [], []
    for stem in base_stems:
        ir = sp.ir(stem)
        toks = []
        for k in profiles:
            src = engines[k].generate(ir)["sources"]["python"]
            toks.append(lizard.analyze_file.analyze_source_code(
                "f.py", src).token_count)
        rhos_tied.append(spearman([0, 1, 2, 3, 4], toks))
        rhos_drop.append(spearman([0, 1, 2, 3],
                                  [toks[0], toks[1], toks[3], toks[4]]))
    tied = mean(rhos_tied)
    if abs(tied - committed_mean) > 5e-4:
        sys.exit(f"KILL: tied recomputation {tied:.4f} != committed "
                 f"{committed_mean:.4f} (anchor.json)")
    return {"n_base_samples": len(base_stems),
            "profiles": profiles,
            "tied_published_mean_rho": tied,
            "dropped_duplicate_mean_rho": mean(rhos_drop)}


def percentile_rule_vs_testA(grades) -> dict:
    """Same detector at delta=0 with arm2 = the tier-A re-mint (not a pure
    null: the arms genuinely differ). Recorded for completeness."""
    from bench.freshness_power import _testA_scores
    cells = {}
    for short, slug in MODELS:
        dev = cluster_scores(grades["refactor"][short]["ann8"])
        ta = cluster_scores(_testA_scores("refactor", "semantic_ok_rate",
                                         slug))
        k1, k2 = sorted(dev), sorted(ta)
        s1 = [float(sum(dev[k])) for k in k1]
        c1 = [len(dev[k]) for k in k1]
        s2 = [float(sum(ta[k])) for k in k2]
        c2 = [len(ta[k]) for k in k2]
        n1, n2 = len(k1), len(k2)
        rejected = 0
        for s in range(SIMS):
            rng = random.Random(SUITE_SEED + s)
            cm1 = [s1[i] / c1[i] for i in rng.choices(range(n1), k=n1)]
            cm2 = [s2[i] / c2[i] for i in rng.choices(range(n2), k=n2)]
            irng = random.Random(SUITE_SEED * 1000 + s)
            diffs = []
            for _ in range(INNER_B):
                d1 = mean([cm1[irng.randrange(n1)] for _ in range(n1)])
                d2 = mean([cm2[irng.randrange(n2)] for _ in range(n2)])
                diffs.append(d2 - d1)
            lo, hi = percentile_ci95(diffs)
            if lo > 0.0 or hi < 0.0:
                rejected += 1
        cells[short] = {"rejection_rate": rejected / SIMS}
    return {"cells": cells,
            "max_rejection": max(c["rejection_rate"]
                                 for c in cells.values())}


def main() -> int:
    sp = D.load("dev")
    if len(sp.items) != 100:
        sys.exit(f"dev split has {len(sp.items)} items, expected 100")
    anchor = json.load(open(os.path.join(_HERE, "out", "anchor.json")))
    committed = anchor["anchors"]["lizard"]["cross_language"][
        "per_language_tokens"]["python"]["incidental_knob_spearman_mean"]

    report = {"fsm_prior_conflict": fsm_prior_conflict(sp)}
    f = report["fsm_prior_conflict"]
    print(f"(a) fsm pooled {f['pooled']['conflict_misses']}/"
          f"{f['pooled']['total_misses']} = {f['pooled']['fraction']:.4f} "
          f"majority-per-model={f['majority_for_every_model']}")

    grades = all_grades(use_cache="--no-cache" not in sys.argv)
    report["percentile_rule_null"] = percentile_rule_null(grades)
    p = report["percentile_rule_null"]
    print(f"(b) null sizes {[c['null_size'] for c in p['cells'].values()]} "
          f"max={p['max_null_size']:.3f}")
    report["percentile_rule_vs_testA"] = percentile_rule_vs_testA(grades)
    print(f"(b') vs-testA max rejection "
          f"{report['percentile_rule_vs_testA']['max_rejection']:.3f}")

    report["token_counterfactual"] = token_counterfactual(sp, committed)
    t = report["token_counterfactual"]
    print(f"(c) tied {t['tied_published_mean_rho']:.4f} -> dropped "
          f"{t['dropped_duplicate_mean_rho']:.4f}")

    report["_meta"] = artifact_header(
        "paper_number_addenda.py",
        ["bench/out/ladder/comprehend__*.jsonl.gz",
         "bench/out/.annotation_ablation_grades.json",
         "bench/out/anchor.json", "config/anti_patterns_db.json"],
        estimation_not_family=True,
        post_hoc=("registered 2026-08-22 after the number-bindings audit; "
                  "estimation only (ANALYSIS_PLAN.md addenda)"),
    )
    write_artifact(OUT, report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
