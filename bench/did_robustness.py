"""DiD robustness re-analyses on the committed grade cache. Zero API.

Post-hoc addendum (ANALYSIS_PLAN.md; estimation only, no confirmatory family
members). Three parts, all on shared base-IR cluster resamples:

(a) Difficulty-stratified DiD. The stratification rule is stated here, before
    results are read: strata are family x scale band, where a family with a
    scale knob (agg_stats W, allowlist L, config_resolver N, threshold_select
    T) is cut at its dev-split scale terciles and knobless families form one
    stratum each. Two interiority rules, both stated before results are read:
    (i) STRICT: every model's annotated_k1 stratum mean lies in [0.10, 0.90]
    (this can be nearly empty precisely because the strongest model saturates,
    which is itself reported); (ii) PER-CONTRAST: for a given pairwise DiD,
    only the two models in the contrast need stratum means inside
    [0.05, 0.95] -- the natural interiority for a pairwise contrast. The DiD
    of the extreme models and of weakest-vs-Llama-3.3-70B is recomputed
    pooled over each rule's interior strata. This answers the
    ceiling-compression alternative with data, rather than with the pairwise
    contrasts alone.

(b) Rescue/spoil rates per model: P(annotated pass | unannotated fail) and
    P(annotated fail | unannotated pass) on the paired items -- the
    composition-effect alternative (annotation rescues failures at a constant
    per-item rate and weak models merely fail more) predicts a flat rescue
    rate across models.

(c) Paired adjacent-rung tests: for each adjacent ladder pair and each arm,
    the cluster-bootstrap CI of the mean per-item score DIFFERENCE on shared
    draws (the direct paired statistic; the published rung-separation count
    thresholds on CI non-overlap, which is more conservative).

Kill-switches (frozen numbers; a mismatch means THIS script is wrong): the
full-corpus extreme DiD points must equal ablation_did.json exactly, and every
paired mean difference must equal the difference of the committed per-model
annotated_k1/unannotated_k1 means exactly.
"""

from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bench.annotation_ablation import (  # noqa: E402
    MODELS,
    ORDER,
    TASKS,
    _family as family_of,
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

CACHE = os.path.join(_HERE, "out", ".annotation_ablation_grades.json")
COMMITTED_DID = os.path.join(_HERE, "out", "ablation_did.json")
COMMITTED_ABL = os.path.join(_HERE, "out", "annotation_ablation.json")
OUT = os.path.join(_HERE, "out", "did_robustness.json")

INTERIOR = (0.10, 0.90)
_SCALE_RE = re.compile(r"_(?:W|L|N|T)(\d+)$")


def _scale_of(sample: str):
    m = _SCALE_RE.search(sample)
    return int(m.group(1)) if m else None


def _stratum_of(sample: str, terciles: dict) -> str:
    fam = family_of(sample)
    s = _scale_of(sample)
    if s is None or fam not in terciles:
        return fam
    lo, hi = terciles[fam]
    band = "low" if s <= lo else ("high" if s > hi else "mid")
    return f"{fam}:{band}"


def _terciles(samples: set) -> dict:
    by_fam: dict = {}
    for s in samples:
        v = _scale_of(s)
        if v is not None:
            by_fam.setdefault(family_of(s), set()).add(v)
    out = {}
    for fam, vals in by_fam.items():
        sv = sorted(vals)
        out[fam] = (sv[max(0, len(sv) // 3 - 1)],
                    sv[max(0, 2 * len(sv) // 3 - 1)])
    return out


def main() -> int:
    cache = json.load(open(CACHE))
    committed_did = json.load(open(COMMITTED_DID))
    committed_abl = json.load(open(COMMITTED_ABL))
    report: dict = {"stratified_did": {}, "rescue_spoil": {},
                    "paired_rung_tests": {}}

    for task, _key in TASKS:
        ann = {m: cache[task][m]["ann1"] for m, _ in MODELS}
        un = {m: cache[task][m]["un1"] for m, _ in MODELS}
        common = sorted(set.intersection(*[set(v) for v in
                                           list(ann.values())
                                           + list(un.values())]))
        samples = {k.split("|")[0] for k in common}
        terc = _terciles(samples)

        # ---------- (a) stratified DiD ---------- #
        strata: dict = {}
        for k in common:
            strata.setdefault(_stratum_of(k.split("|")[0], terc),
                              []).append(k)
        interior, excluded = [], {}
        for st, keys in sorted(strata.items()):
            means = {m: mean([ann[m][k] for k in keys]) for m in ann}
            ok = all(INTERIOR[0] <= v <= INTERIOR[1] for v in means.values())
            (interior.append(st) if ok else
             excluded.__setitem__(st, {m: round(v, 3)
                                       for m, v in means.items()}))
        stratum_means = {st: {m: mean([ann[m][k] for k in keys])
                              for m in ann}
                         for st, keys in strata.items()}

        def _pooled_did(int_strata: list, wk: str, st_model: str) -> dict:
            int_keys = [k for s in int_strata for k in strata[s]]
            if not int_keys:
                return {"n_items": 0}
            deltas = {m: {k: un[m][k] - ann[m][k] for k in int_keys}
                      for m in (wk, st_model)}
            clus = {m: cluster_scores(deltas[m]) for m in deltas}
            keys = sorted(set().union(*[set(c) for c in clus.values()]))
            pts = {m: mean(list(deltas[m].values())) for m in deltas}
            out = {"point": pts[wk] - pts[st_model],
                   "n_items": len(int_keys),
                   "n_strata": len(int_strata),
                   "n_clusters": len(keys)}
            if len(keys) < 5:
                # single-programme strata cannot support a cluster CI
                # (partial_credit_analysis.py convention)
                out["illustration_only"] = True
                return out
            draws = shared_cluster_draws(keys, BOOTSTRAP, SUITE_SEED)
            dm = {m: draw_means(clus[m], draws) for m in clus}
            dd = [dm[wk][t] - dm[st_model][t] for t in range(BOOTSTRAP)]
            out["ci95"] = percentile_ci95(dd)
            out["p_bootstrap"] = two_sided_bootstrap_p(dd)
            return out

        strat_block: dict = {
            "rule_strict": ("family x scale terciles; interior = every "
                            f"model's annotated_k1 mean in {list(INTERIOR)}"),
            "rule_per_contrast": ("interior = BOTH contrast models' "
                                  "annotated_k1 stratum means in "
                                  "[0.05, 0.95]"),
            "interior_strata_strict": interior,
            "excluded_strata_annotated_means": excluded,
            "strict": {}, "per_contrast": {},
        }
        for name, wk, st_model in (("extremes", ORDER[0], ORDER[-1]),
                                   ("weakest_vs_llama70b", ORDER[0],
                                    ORDER[2])):
            strat_block["strict"][name] = _pooled_did(interior, wk, st_model)
            pc = [s for s in sorted(strata)
                  if all(0.05 <= stratum_means[s][m] <= 0.95
                         for m in (wk, st_model))]
            blk = _pooled_did(pc, wk, st_model)
            blk["interior_strata"] = pc
            strat_block["per_contrast"][name] = blk
        report["stratified_did"][task] = strat_block
        pc_ex = strat_block["per_contrast"]["extremes"]
        print(f"[{task}] strict interior: {interior}; per-contrast extremes: "
              f"{pc_ex.get('n_strata', 0)} strata, "
              f"{pc_ex.get('n_items', 0)} items", flush=True)

        # ---------- (b) rescue / spoil ---------- #
        rs = {}
        for m in ORDER:
            un_fail = [k for k in common if un[m][k] == 0.0]
            un_pass = [k for k in common if un[m][k] == 1.0]
            rs[m] = {
                "rescue_rate": (mean([ann[m][k] for k in un_fail])
                                if un_fail else None),
                "n_unannotated_fail": len(un_fail),
                "spoil_rate": (mean([1 - ann[m][k] for k in un_pass])
                               if un_pass else None),
                "n_unannotated_pass": len(un_pass),
            }
        report["rescue_spoil"][task] = rs

        # ---------- (c) paired adjacent-rung tests ---------- #
        pair_block = {}
        for arm_name, grades in (("annotated_k1", ann), ("unannotated_k1",
                                                         un)):
            arm_out = {}
            for a, b in zip(ORDER, ORDER[1:]):
                diffs = {k: grades[b][k] - grades[a][k] for k in common}
                clus = cluster_scores(diffs)
                keys = sorted(clus)
                draws = shared_cluster_draws(keys, BOOTSTRAP, SUITE_SEED)
                dm = draw_means(clus, draws)
                point = mean(list(diffs.values()))
                # KILL: paired mean == difference of committed means exactly
                fld = ("annotated_k1" if arm_name == "annotated_k1"
                       else "unannotated_k1")
                want = (committed_abl[task]["per_model"][b][fld]
                        - committed_abl[task]["per_model"][a][fld])
                assert abs(point - want) < 1e-12, (
                    f"KILL: {task}/{arm_name} {a}->{b}: paired mean "
                    f"{point!r} != committed difference {want!r}")
                arm_out[f"{a} -> {b}"] = {
                    "point": point,
                    "ci95": percentile_ci95(dm),
                    "p_bootstrap": two_sided_bootstrap_p(dm),
                }
            pair_block[arm_name] = arm_out
        report["paired_rung_tests"][task] = pair_block

        # KILL: full-corpus extremes DiD equals ablation_did.json
        deltas_all = {m: {k: un[m][k] - ann[m][k] for k in common}
                      for m in (ORDER[0], ORDER[-1])}
        got = (mean(list(deltas_all[ORDER[0]].values()))
               - mean(list(deltas_all[ORDER[-1]].values())))
        want = committed_did[task]["did"]["point"]
        assert got == want, f"KILL: {task} extremes DiD {got!r} != {want!r}"
    print("KILL passed: extremes DiD + paired means match committed artifacts",
          flush=True)

    report["_meta"] = artifact_header(
        "did_robustness.py",
        ["bench/out/.annotation_ablation_grades.json",
         "bench/out/ablation_did.json",
         "bench/out/annotation_ablation.json"],
        shared_draws=True,
        estimation_not_family=True,
        interior_band=list(INTERIOR),
        post_hoc=("registered 2026-08-14 after the suite's results were read "
                  "(ANALYSIS_PLAN.md post-hoc addenda)"),
    )
    write_artifact(OUT, report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
