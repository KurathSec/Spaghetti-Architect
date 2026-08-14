"""Difference-in-differences reframing of the annotation ablation. Zero API.

The published headline characterized differential inflation as a RATIO of the
extreme models' deltas, which explodes as the denominator nears zero (the 73x
leave-one-family-out artifact). This script recomputes the same effect as a
difference-in-differences (DiD = delta_weakest - delta_strongest) with a
base-IR-clustered bootstrap on SHARED draws, so every model and both arms are
evaluated on the same resamples; it also replaces the thresholded
"rungs separated" count with the continuous bootstrap concordance
P(stronger model's mean > weaker model's mean) per adjacent pair per arm.

Inputs: the same grade source as bench/annotation_ablation.py (cache when
present, else a full offline re-grade of the committed gz archives).
Output: bench/out/ablation_did.json.

Verification kill-switches (the numbers are frozen; a mismatch means THIS
script is wrong): the DiD point must equal the committed per-model delta
difference exactly; the ratio point must equal the committed
weak_over_strong_ratio; every committed separated=True adjacent pair must show
concordance >= 0.95 on the shared draws.

The secondary formal test (cluster-robust logistic regression with a
model x condition interaction) runs when statsmodels is importable (the
quarantined metrics venv); otherwise it is recorded as an honest SKIP and the
bootstrap DiD stands alone, mirroring bench/analysis.py's fallback pattern.
"""

from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bench.suite_common import (  # noqa: E402
    BOOTSTRAP,
    ORDER,
    STRONGEST,
    SUITE_SEED,
    WEAKEST,
    all_grades,
    artifact_header,
    cluster_scores,
    draw_means,
    mean,
    percentile_ci95,
    shared_cluster_draws,
    two_sided_bootstrap_p,
    write_artifact,
)

OUT = os.path.join(_HERE, "out", "ablation_did.json")
COMMITTED = os.path.join(_HERE, "out", "annotation_ablation.json")

TASKS = ["refactor", "comprehend"]
RATIO_SIGNFLIP_MAX = 0.01   # >1% sign-flipped draws => ratio CI is a quotient by noise
RATIO_NEAR_ZERO = 0.005


def _common_scores(g_task: dict) -> dict:
    """{model: {'ann': {key: y}, 'un': {key: y}, 'delta': {key: d}}} on common keys."""
    out = {}
    key_sets = []
    for model in ORDER:
        a1, u1 = g_task[model]["ann1"], g_task[model]["un1"]
        common = sorted(set(a1) & set(u1))
        if not common:
            sys.exit(f"no overlapping items for {model}")
        out[model] = {
            "ann": {k: a1[k] for k in common},
            "un": {k: u1[k] for k in common},
            "delta": {k: u1[k] - a1[k] for k in common},
        }
        key_sets.append(set(common))
    if not all(s == key_sets[0] for s in key_sets):
        sys.exit("item sets differ across models; expected identical 1500-item grids")
    return out


def _task_report(task: str, g_task: dict, committed_task: dict) -> dict:
    scores = _common_scores(g_task)
    clusters = {m: {arm: cluster_scores(scores[m][arm]) for arm in ("ann", "un")}
                for m in ORDER}
    delta_clusters = {m: cluster_scores(scores[m]["delta"]) for m in ORDER}

    cluster_keys = set(delta_clusters[WEAKEST])
    for m in ORDER:
        if set(delta_clusters[m]) != cluster_keys:
            sys.exit(f"cluster space differs for {m}")
    draws = shared_cluster_draws(cluster_keys, BOOTSTRAP, SUITE_SEED)

    # Per-draw means, every model and arm on the SAME draws.
    dm = {m: {arm: draw_means(clusters[m][arm], draws) for arm in ("ann", "un")}
          for m in ORDER}
    d_delta = {m: draw_means(delta_clusters[m], draws) for m in ORDER}

    # Point estimates (full sample, unweighted over items).
    point_delta = {m: mean(list(scores[m]["delta"].values())) for m in ORDER}

    # --- DiD ---
    did_draws = [d_delta[WEAKEST][b] - d_delta[STRONGEST][b] for b in range(BOOTSTRAP)]
    did_point = point_delta[WEAKEST] - point_delta[STRONGEST]
    did = {
        "definition": f"delta({WEAKEST}) - delta({STRONGEST}), delta = unannotated - annotated",
        "point": did_point,
        "ci95": percentile_ci95(did_draws),
        "p_bootstrap": two_sided_bootstrap_p(did_draws),
        "n_items_per_model": len(scores[WEAKEST]["delta"]),
        "n_clusters": len(cluster_keys),
    }

    # Kill-switch: the DiD point must equal the committed delta difference.
    comm = {m: committed_task["per_model"][m]["delta"] for m in (WEAKEST, STRONGEST)}
    committed_did = comm[WEAKEST] - comm[STRONGEST]
    if abs(did_point - committed_did) > 1e-9:
        sys.exit(f"{task}: DiD point {did_point} != committed {committed_did}")

    # --- Ratio, with the sign-flip guard ---
    signflips = sum(1 for b in range(BOOTSTRAP) if d_delta[STRONGEST][b] >= 0)
    near_zero = sum(1 for b in range(BOOTSTRAP)
                    if abs(d_delta[STRONGEST][b]) < RATIO_NEAR_ZERO)
    ratio_point = (point_delta[WEAKEST] / point_delta[STRONGEST]
                   if point_delta[STRONGEST] != 0 else None)
    unstable = signflips > RATIO_SIGNFLIP_MAX * BOOTSTRAP
    ratio = {
        "point": ratio_point,
        "ci95": None if unstable else percentile_ci95(
            [d_delta[WEAKEST][b] / d_delta[STRONGEST][b] for b in range(BOOTSTRAP)]),
        "unstable": unstable,
        "n_signflip_draws": signflips,
        "n_near_zero_draws": near_zero,
        "note": "CI nulled when >1% of shared draws sign-flip the denominator "
                "(a quotient by noise); the DiD is the quotable statistic.",
    }
    committed_ratio = committed_task.get("weak_over_strong_ratio")
    if committed_ratio is not None and ratio_point is not None:
        if abs(abs(ratio_point) - committed_ratio) > 1e-6 * max(1.0, committed_ratio):
            sys.exit(f"{task}: ratio point {ratio_point} != committed {committed_ratio}")

    # --- Bootstrap concordance per adjacent pair per arm ---
    concordance = {}
    for arm in ("ann", "un"):
        arm_name = "annotated" if arm == "ann" else "unannotated"
        for weaker, stronger in zip(ORDER, ORDER[1:]):
            frac = mean([1.0 if dm[stronger][arm][b] > dm[weaker][arm][b] else 0.0
                         for b in range(BOOTSTRAP)])
            concordance[f"{arm_name}:{weaker}<{stronger}"] = frac

    # Kill-switch: committed separated pairs must be near-certain under shared draws.
    checks = []
    rungs = committed_task.get("rungs_separated", {})
    for arm_key in ("annotated", "unannotated"):
        for pair in rungs.get(arm_key, {}).get("pairs", []):
            a, b = [s.strip() for s in pair["pair"].split("<")]
            c = concordance[f"{arm_key}:{a}<{b}"]
            checks.append({"pair": pair["pair"], "arm": arm_key,
                           "committed_separated": pair["separated"],
                           "concordance": c})
            if pair["separated"] and c < 0.95:
                sys.exit(f"{task}: committed separated pair {pair['pair']} ({arm_key}) "
                         f"has concordance {c}")

    return {
        "per_model_delta_point": point_delta,
        "did": did,
        "ratio": ratio,
        "concordance": concordance,
        "concordance_vs_committed_rungs": checks,
    }


def _regression(task: str, g_task: dict) -> dict:
    """Secondary formal test: cluster-robust logit y ~ C(model)*C(cond)."""
    try:
        import pandas as pd
        import statsmodels.formula.api as smf
    except Exception:
        return {"engine": "SKIP_statsmodels",
                "note": "install statsmodels in the metrics venv to run"}

    from bench.suite_common import base_ir
    rows = []
    scores = _common_scores(g_task)
    for m in ORDER:
        for arm, arm_name in (("ann", "annotated"), ("un", "unannotated")):
            for k, y in scores[m][arm].items():
                rows.append({"y": int(round(y)), "model": m, "cond": arm_name,
                             "cluster": base_ir(k.split("|")[0])})
    df = pd.DataFrame(rows)
    df["model"] = pd.Categorical(df["model"],
                                 categories=[STRONGEST] + [m for m in ORDER
                                                          if m != STRONGEST])
    df["cond"] = pd.Categorical(df["cond"], categories=["annotated", "unannotated"])

    def _interactions(fit):
        inter = {}
        for name in fit.params.index:
            if ":" in name and "unannotated" in name:
                inter[name] = {"coef": float(fit.params[name]),
                               "p": float(fit.pvalues[name])}
        weak = next((v for k, v in inter.items() if WEAKEST in k), None)
        return inter, weak

    out = {"n_rows": len(df),
           "reference": {"model": STRONGEST, "cond": "annotated"}}
    # Probability scale (linear probability model): the scale the ladder lives
    # on. Absolute score gaps are what interval separation and benchmark
    # consumers compare, so this is the formalization of the DiD.
    lpm = smf.ols("y ~ C(model) * C(cond)", data=df).fit(
        cov_type="cluster", cov_kwds={"groups": df["cluster"]})
    inter, weak = _interactions(lpm)
    out["lpm_cluster_robust"] = {"interactions": inter,
                                 "weakest_x_unannotated": weak}
    # Log-odds scale: reported as a scale-dependence caveat. Near-ceiling
    # models compress on the probability scale, so a probability-scale
    # differential can be consistent with near-uniform log-odds shifts; the
    # differential-inflation claim is a probability-scale claim.
    try:
        logit = smf.logit("y ~ C(model) * C(cond)", data=df).fit(
            disp=0, cov_type="cluster", cov_kwds={"groups": df["cluster"]})
        inter, weak = _interactions(logit)
        out["logit_cluster_robust"] = {"interactions": inter,
                                       "weakest_x_unannotated": weak}
    except Exception as exc:  # separation / convergence
        out["logit_cluster_robust"] = {"skip": str(exc)}
    out["note"] = ("both engines duplicate the DiD hypothesis as cross-checks on "
                   "two scales; neither is part of the confirmatory p-value family")
    return out


def main() -> int:
    committed = json.load(open(COMMITTED))
    grades = all_grades(use_cache="--no-cache" not in sys.argv)
    report = {
        "_meta": artifact_header(
            "did_analysis.py",
            ["bench/out/annotation_ablation.json", "bench/out/ladder/*.jsonl.gz",
             "bench/out/g3/refactor_dev__*.jsonl.gz", "bench/out/ablation/*.jsonl.gz"],
            shared_draws=True),
    }
    for task in TASKS:
        report[task] = _task_report(task, grades[task], committed[task])
        report[task]["regression"] = _regression(task, grades[task])
        d = report[task]["did"]
        r = report[task]["ratio"]
        print(f"{task}: DiD {d['point']:+.4f} {d['ci95']} p={d['p_bootstrap']:.4g}  "
              f"ratio {r['point'] if r['point'] is None else round(r['point'], 3)} "
              f"(unstable={r['unstable']}, signflips={r['n_signflip_draws']})")
    write_artifact(OUT, report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
