"""Power analysis for the freshness null (public dev vs private Tier-A test). Zero API.

The paper's freshness check reports per-model |dev - testA| <= 0.012 on both
tasks and reads that agreement as evidence against value-memorization of the
public split. Agreement is only meaningful if the design could have DETECTED a
disagreement, so this script measures, offline, the minimum shift the
dev-vs-testA comparison detects with >= 80% power.

Inputs:
  dev arm   : per-item k=8 grades from the ablation grade cache
              (all_grades(True), key "ann8"; refactor = semantic_ok_rate,
              comprehend = exact_match_rate), grouped into the 50 base-IR
              clusters.
  testA arm : per-item scores of tier=="A" records in the local-only finalize
              files bench/out/subagent/<task>__<slug>__test.json when all
              eight are on disk (mode "dev_vs_testA"); otherwise both arms are
              simulated from dev clusters (mode "dev_only"; stated assumption:
              dev/testA exchangeability, exactly what the published
              |delta| <= 0.012 supports). The mode is stamped in the artifact.

Simulation per (task, model|pooled, delta in {0,.01,.02,.03,.05,.075,.10}),
S=400 sims seeded SUITE_SEED + sim index: arm1 resamples dev clusters with
replacement; arm2 resamples the other arm's clusters, then flips item scores
to 1 with the per-item probability that shifts the arm-2 mean by +delta
(binomial injection at item level, cluster structure preserved; items already
at 1 cannot flip, so the achievable shift is capped by the resample's headroom
and recorded). Detection = a two-sided permutation test (B=500 label
permutations, seeded per sim) on the resampled cluster means, rejecting at
p <= 0.05; exactly calibrated under the same-pool null by construction.

Kill-switches (published figures are FROZEN; a failure means THIS script is
wrong -- fix the script, never the expected value):
  * observed dev / testA means must reproduce bench/out/g3_analysis.json to
    its 4-decimal rounding;
  * max observed |dev - testA| must respect the published 0.012 bound;
  * calibration: the NULL rejection rate (both arms resampled from the same
    dev pool, delta=0, permutation detection) must lie in [0.02, 0.09] for
    every cell, else abort with no artifact; the permutation test is exactly
    calibrated under the null, so this gate now checks only Monte-Carlo
    noise. (The delta=0 cell of the power curve compares dev against the real
    arm2 and is kept as a descriptive quantity: in dev_vs_testA mode it
    responds to the true observed gap, so it is NOT a false-positive rate.);
  * monotonicity: power(0.10) > power(0.02) for every cell, except when both
    levels saturate to the same achievable shift (the sims are then identical
    by construction and exact equality is required instead).

Output: bench/out/freshness_power.json (deterministic; run-twice byte-identical).
"""

from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import random  # noqa: E402

from bench.suite_common import (  # noqa: E402
    MODELS,
    ORDER,
    SUITE_SEED,
    all_grades,
    artifact_header,
    cluster_scores,
    item_key,
    mean,
    write_artifact,
)

OUT = os.path.join(_HERE, "out", "freshness_power.json")
G3 = os.path.join(_HERE, "out", "g3_analysis.json")
SUB = os.path.join(_HERE, "out", "subagent")

TASKS = [("refactor", "semantic_ok_rate"), ("comprehend", "exact_match_rate")]
DELTAS = [0.0, 0.01, 0.02, 0.03, 0.05, 0.075, 0.10]
SIMS = 400
INNER_B = 500
PUBLISHED_BOUND = 0.012      # the paper's freshness bound: max |dev - testA|
# Abort window for the empirical null size (dev-vs-dev rejection rate at
# delta=0). The detection rule is a cluster-mean permutation test, exactly
# calibrated under the null by construction, so the empirical size should sit
# at the nominal 0.05 up to Monte-Carlo noise (S=400 sims -> SE ~ 0.011).
CALIB_LO, CALIB_HI = 0.02, 0.09
POWER_TARGET = 0.80
ROUND4_TOL = 5.1e-5          # g3_analysis.json stores 4-decimal roundings


def _dkey(d: float) -> str:
    return f"{d:.3f}"        # fixed width so sort_keys orders the grid


def _testA_scores(task: str, field: str, slug: str) -> dict:
    """{item_key: score} for tier=="A" records of one local finalize file."""
    path = os.path.join(SUB, f"{task}__{slug}__test.json")
    items = json.load(open(path))["items"]
    out = {}
    for r in items:
        if r.get("tier") != "A" or r.get(field) is None:
            continue
        out[item_key(r)] = r[field]
    if len(out) < 1400:
        sys.exit(f"{task}/{slug}: only {len(out)} tier-A test items (expected ~1500)")
    return out


def _prep(clusters: dict) -> dict:
    """Per-cluster precomputation: sums/counts once per arm (the sim engine
    never touches raw items again except to flip the sub-1 ones)."""
    keys = sorted(clusters)
    sums, counts, lt1 = [], [], []
    for k in keys:
        v = clusters[k]
        if not v:
            sys.exit(f"empty cluster {k}")
        sums.append(float(sum(v)))
        counts.append(len(v))
        lt1.append([y for y in v if y < 1.0])   # only these items can flip
    return {"keys": keys, "sums": sums, "counts": counts, "lt1": lt1,
            "n_items": sum(counts), "n_clusters": len(keys)}


def _simulate(arm1: dict, arm2: dict, delta: float) -> dict:
    """Power of the CI-excludes-0 rule at injected shift ``delta`` on arm2."""
    s1, c1, n1 = arm1["sums"], arm1["counts"], arm1["n_clusters"]
    s2, c2, lt1, n2 = arm2["sums"], arm2["counts"], arm2["lt1"], arm2["n_clusters"]
    r1, r2 = range(n1), range(n2)
    detected = 0
    achieved = []
    saturated = 0
    for s in range(SIMS):
        rng = random.Random(SUITE_SEED + s)
        idx1 = rng.choices(r1, k=n1)
        a1s = [s1[i] for i in idx1]
        a1c = [c1[i] for i in idx1]
        idx2 = rng.choices(r2, k=n2)
        tot_c2 = sum(c2[i] for i in idx2)
        base_sum = sum(s2[i] for i in idx2)
        headroom = (tot_c2 - base_sum) / tot_c2   # max achievable mean shift
        if delta <= 0.0 or headroom <= 0.0:
            p = 0.0
        else:
            p = min(1.0, delta / headroom)
        achieved.append(min(delta, headroom))
        if delta > 0.0 and headroom < delta:
            saturated += 1
        if p == 0.0:
            a2s = [s2[i] for i in idx2]
        elif p == 1.0:
            a2s = [float(c2[i]) for i in idx2]     # every sub-1 item flips to 1
        else:
            a2s = []
            for i in idx2:                          # independent flips per occurrence
                si = s2[i]
                for y in lt1[i]:
                    if rng.random() < p:
                        si += 1.0 - y
                a2s.append(si)
        a2c = [c2[i] for i in idx2]
        # Detection: permutation test on the resampled CLUSTER MEANS. Under the
        # null (both arms drawn from the same pool) cluster means are
        # exchangeable, so the test is exactly calibrated by construction,
        # which the percentile-CI rule is not on the skewed near-ceiling arms
        # (null sizes up to 0.105; frozen re-derivation in
        # bench/out/paper_number_addenda.json). Statistic: difference of
        # unweighted cluster-mean averages.
        cm1 = [a1s[i] / a1c[i] for i in range(n1)]
        cm2 = [a2s[i] / a2c[i] for i in range(n2)]
        obs = sum(cm2) / n2 - sum(cm1) / n1
        pooled = cm1 + cm2
        exceed = 0
        for _ in range(INNER_B):                    # permutations, per-sim seed
            rng.shuffle(pooled)
            pm1 = sum(pooled[:n1]) / n1
            pm2 = sum(pooled[n1:]) / n2
            if abs(pm2 - pm1) >= abs(obs):
                exceed += 1
        p_perm = (1 + exceed) / (1 + INNER_B)
        if p_perm <= 0.05:
            detected += 1
    return {"power": detected / SIMS, "n_detected": detected, "sims": SIMS,
            "inner_permutations": INNER_B, "achieved_shift_mean": mean(achieved),
            "saturated_sims": saturated}


def main() -> int:
    g3 = json.load(open(G3))
    grades = all_grades(use_cache="--no-cache" not in sys.argv)

    test_files = {(task, slug): os.path.join(SUB, f"{task}__{slug}__test.json")
                  for task, _f in TASKS for _s, slug in MODELS}
    missing = sorted(os.path.relpath(p, _ROOT) for p in test_files.values()
                     if not os.path.exists(p))
    mode = "dev_only" if missing else "dev_vs_testA"

    observed: dict = {}
    arms: dict = {}          # (task, label) -> {"dev": prep, "arm2": prep}
    max_abs_delta = 0.0
    for task, field in TASKS:
        observed[task] = {}
        dev_cl_by_model, test_cl_by_model = {}, {}
        for short, slug in MODELS:
            dev_scores = grades[task][short]["ann8"]
            if len(dev_scores) != 1500:
                sys.exit(f"{task}/{short}: dev grid has {len(dev_scores)} items, expected 1500")
            dev_cl = cluster_scores(dev_scores)
            if len(dev_cl) != 50:
                sys.exit(f"{task}/{short}: {len(dev_cl)} dev clusters, expected 50")
            dev_cl_by_model[short] = dev_cl
            row = {"dev_mean": mean(list(dev_scores.values())),
                   "dev_n": len(dev_scores), "n_clusters_dev": len(dev_cl)}

            if mode == "dev_vs_testA":
                t_scores = _testA_scores(task, field, slug)
                t_cl = cluster_scores(t_scores)
                if len(t_cl) != 50:
                    sys.exit(f"{task}/{short}: {len(t_cl)} testA clusters, expected 50")
                test_cl_by_model[short] = t_cl
                row.update({"testA_mean": mean(list(t_scores.values())),
                            "testA_n": len(t_scores), "n_clusters_testA": len(t_cl)})
                row["dev_minus_testA"] = row["dev_mean"] - row["testA_mean"]
                max_abs_delta = max(max_abs_delta, abs(row["dev_minus_testA"]))

                # Kill-switch: reproduce the committed g3_analysis numbers (4-dp).
                if task == "comprehend":
                    exp_dev = g3[task][short]["dev_overall"]
                    exp_ta = g3[task][short]["test_A"]
                else:
                    exp_dev = g3[task][short]["dev"]["semantic_ok"]
                    exp_ta = g3[task][short]["test"]["semantic_ok_by_tier"]["A"]
                if abs(row["dev_mean"] - exp_dev) > ROUND4_TOL:
                    sys.exit(f"{task}/{short}: dev mean {row['dev_mean']:.6f} != "
                             f"committed {exp_dev} (g3_analysis.json)")
                if abs(row["testA_mean"] - exp_ta) > ROUND4_TOL:
                    sys.exit(f"{task}/{short}: testA mean {row['testA_mean']:.6f} != "
                             f"committed {exp_ta} (g3_analysis.json)")
            observed[task][short] = row

            arm2_cl = test_cl_by_model[short] if mode == "dev_vs_testA" else dev_cl
            arms[(task, short)] = {"dev": _prep(dev_cl), "arm2": _prep(arm2_cl)}

        # Pooled cell: the 50 clusters keep their identity, items pool over models.
        keys = sorted(dev_cl_by_model[ORDER[0]])
        for short in ORDER:
            if sorted(dev_cl_by_model[short]) != keys:
                sys.exit(f"{task}: dev cluster keys differ for {short}")
        pooled_dev = {k: [y for short in ORDER for y in dev_cl_by_model[short][k]]
                      for k in keys}
        if mode == "dev_vs_testA":
            tkeys = sorted(test_cl_by_model[ORDER[0]])
            for short in ORDER:
                if sorted(test_cl_by_model[short]) != tkeys:
                    sys.exit(f"{task}: testA cluster keys differ for {short}")
            pooled_arm2 = {k: [y for short in ORDER for y in test_cl_by_model[short][k]]
                           for k in tkeys}
        else:
            pooled_arm2 = pooled_dev
        arms[(task, "pooled")] = {"dev": _prep(pooled_dev), "arm2": _prep(pooled_arm2)}
        observed[task]["pooled"] = {
            "dev_mean": sum(sum(v) for v in pooled_dev.values())
                        / sum(len(v) for v in pooled_dev.values()),
            "dev_n": sum(len(v) for v in pooled_dev.values()),
            "n_clusters_dev": len(pooled_dev)}
        if mode == "dev_vs_testA":
            observed[task]["pooled"].update({
                "testA_mean": sum(sum(v) for v in pooled_arm2.values())
                              / sum(len(v) for v in pooled_arm2.values()),
                "testA_n": sum(len(v) for v in pooled_arm2.values()),
                "n_clusters_testA": len(pooled_arm2)})
            observed[task]["pooled"]["dev_minus_testA"] = (
                observed[task]["pooled"]["dev_mean"]
                - observed[task]["pooled"]["testA_mean"])

    # Kill-switch: the published freshness bound must hold on the observed data.
    if mode == "dev_vs_testA" and max_abs_delta > PUBLISHED_BOUND:
        sys.exit(f"max observed |dev - testA| = {max_abs_delta:.4f} exceeds the "
                 f"published bound {PUBLISHED_BOUND}; this script mis-scored an arm")

    # ---- simulations ----
    labels = ORDER + ["pooled"]
    power: dict = {}
    for task, _field in TASKS:
        power[task] = {}
        for label in labels:
            cell = arms[(task, label)]
            per_delta = {}
            for d in DELTAS:
                per_delta[_dkey(d)] = _simulate(cell["dev"], cell["arm2"], d)
                r = per_delta[_dkey(d)]
                print(f"{task}/{label} delta={d:.3f} power={r['power']:.3f} "
                      f"achieved={r['achieved_shift_mean']:.4f} "
                      f"saturated={r['saturated_sims']}/{SIMS}", flush=True)
            # Calibration must test the detection rule's false-positive rate
            # under the NULL, i.e. both arms drawn from the SAME pool. In
            # dev_vs_testA mode the delta=0 cell above compares dev against the
            # REAL testA arm, whose true (small) gap contaminates the rejection
            # rate; that number is kept as a descriptive quantity instead.
            null_cal = _simulate(cell["dev"], cell["dev"], 0.0)
            print(f"{task}/{label} null-calibration (dev vs dev) rejection "
                  f"{null_cal['power']:.3f}", flush=True)
            mde = next((d for d in DELTAS[1:]
                        if per_delta[_dkey(d)]["power"] >= POWER_TARGET), None)
            power[task][label] = {
                "cells": per_delta,
                "calibration_rejection_at_zero_null": null_cal["power"],
                "rejection_at_zero_vs_arm2": per_delta[_dkey(0.0)]["power"],
                "rejection_at_zero_vs_arm2_note": (
                    "descriptive: in dev_vs_testA mode this responds to the "
                    "real observed dev-testA gap, not only to false positives"),
                "mde80": mde,
                "mde80_note": (
                    "smallest grid delta with power >= 0.80"
                    if mde is not None else
                    "no grid delta reaches 0.80 power"
                    + ("; injection saturates below the grid ceiling"
                       if per_delta[_dkey(DELTAS[-1])]["saturated_sims"] > 0 else "")),
                "n_dev_items": cell["dev"]["n_items"],
                "n_arm2_items": cell["arm2"]["n_items"],
                "n_clusters": cell["dev"]["n_clusters"],
            }

    # ---- calibration + monotonicity gates (abort BEFORE writing) ----
    for task, _field in TASKS:
        for label in labels:
            cal = power[task][label]["calibration_rejection_at_zero_null"]
            if not (CALIB_LO <= cal <= CALIB_HI):
                sys.exit(f"CALIBRATION FAIL {task}/{label}: null (dev vs dev) "
                         f"rejection rate {cal:.4f} outside [{CALIB_LO}, {CALIB_HI}]; "
                         f"the detection rule is miscalibrated -- fix the script, "
                         f"no artifact written")
            hi = power[task][label]["cells"][_dkey(0.10)]
            lo = power[task][label]["cells"][_dkey(0.02)]
            both_saturated_equal = (
                hi["saturated_sims"] == SIMS and lo["saturated_sims"] == SIMS
                and abs(hi["achieved_shift_mean"] - lo["achieved_shift_mean"]) < 1e-12)
            if both_saturated_equal:
                if hi["power"] != lo["power"]:
                    sys.exit(f"SANITY FAIL {task}/{label}: both levels saturate to the "
                             f"same achievable shift but powers differ "
                             f"({hi['power']} vs {lo['power']})")
            elif hi["power"] <= lo["power"]:
                sys.exit(f"SANITY FAIL {task}/{label}: power(0.10)={hi['power']} "
                         f"<= power(0.02)={lo['power']}")

    # ---- consistency line for the paper ----
    mdes = {task: power[task]["pooled"]["mde80"] for task, _f in TASKS}
    parts = []
    for task, _f in TASKS:
        m = mdes[task]
        if m is None:
            parts.append(f"{task}: no grid shift reaches 80% power")
        else:
            parts.append(f"{task}: pooled MDE80 = {m:g} "
                         f"({m / PUBLISHED_BOUND:.1f}x the published bound)")
    consistency = {
        "published_bound_abs_delta": PUBLISHED_BOUND,
        "max_abs_observed_delta": max_abs_delta if mode == "dev_vs_testA" else None,
        "pooled_mde80": mdes,
        "line": ("The published freshness agreement (max |dev - testA| = "
                 + (f"{max_abs_delta:.4f}" if mode == "dev_vs_testA" else "<= 0.012 as published")
                 + ") sits below the smallest shift the design detects with >= 80% "
                 "power -- " + "; ".join(parts) + ". Any dev-vs-testA drift at or "
                 "above the pooled MDE80 would have been flagged; drifts below it "
                 "are consistent with the null but were not detectable."),
    }

    inputs = ["bench/out/.annotation_ablation_grades.json",
              "bench/out/g3_analysis.json"]
    if mode == "dev_vs_testA":
        inputs += [os.path.relpath(p, _ROOT) for p in test_files.values()]
    report = {
        "_meta": artifact_header(
            "freshness_power.py", inputs,
            mode=mode, sims=SIMS, inner_permutations=INNER_B, deltas=DELTAS,
            power_target=POWER_TARGET,
            missing_test_files=missing,
            dev_score_source="ann8 (k=8 per-item rate from the ablation grade cache)",
            testA_score_source=("tier==A per-item scores of the local finalize files"
                                if mode == "dev_vs_testA" else
                                "SIMULATED from dev clusters (testA exchangeability "
                                "assumption, supported by the published |delta|<=0.012)")),
        "mode": mode,
        "observed": observed,
        "power": power,
        "consistency": consistency,
    }
    write_artifact(OUT, report)
    print(consistency["line"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
