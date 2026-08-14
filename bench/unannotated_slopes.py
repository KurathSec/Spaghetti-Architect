"""Incidental-knob slopes on the UNANNOTATED arm. Zero API.

The published knob-inertness negative result (comprehension flat, equivalence
gate flat, conditional quality non-declining) was estimated on the ANNOTATED
corpus, where every prompt carries the per-operation comment stating the clean
form, so incidental mess arguably cannot matter by construction: the prompt
explains the mess away. The committed unannotated k=1 completions cover all
three knob rungs in both tasks; this script asks whether the knob starts to
matter once the generator stops annotating its own output.

Post-hoc addendum (registered in ANALYSIS_PLAN.md after the suite's results
were read; estimation only, no confirmatory family members). Three parts, all
from the committed grade cache (bench/out/.annotation_ablation_grades.json,
draw[0] grades; item key = sample|profile|language|variant):

(a) per model x task x arm: OLS slope of the binary outcome (refactor
    semantic_ok / comprehend exact_match) on knob rank (minimal=0, standard=1,
    max=2), cluster bootstrap over (family, sample) as in bench/analysis.py;
(b) the arm-by-rung interaction: the per-item delta (unannotated - annotated)
    regressed on knob rank -- does the annotation's help dose-respond with the
    amount of mess it explains away?
(c) a mess-by-width slice on comprehend agg_stats: EM by (profile x W) per
    arm. Every width is a single base program, so per-width cells are marked
    illustration_only (n_clusters=1) and a pooled high-W (>=48) vs low-W
    (<=32) knob-slope contrast with sample-level clusters (10 base programs)
    is reported alongside.

Verification kill-switches (the published numbers are frozen; a mismatch means
THIS script is wrong): the per-model overall means recomputed from the cache
must equal the committed annotation_ablation.json annotated_k1 and
unannotated_k1 values exactly, for both tasks.
"""

from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bench.analysis as A  # noqa: E402
from bench.annotation_ablation import (  # noqa: E402
    MODELS,
    TASKS,
    _family as family_of,
)
from bench.suite_common import (  # noqa: E402
    artifact_header,
    mean,
    write_artifact,
)

CACHE = os.path.join(_HERE, "out", ".annotation_ablation_grades.json")
COMMITTED = os.path.join(_HERE, "out", "annotation_ablation.json")
OUT = os.path.join(_HERE, "out", "unannotated_slopes.json")

KNOB_RANK = {"minimal": 0, "standard": 1, "max": 2}
HIGH_W = 48   # pooled contrast: W >= 48 (high) vs W <= 32 (low)


def _parse_key(key: str) -> dict:
    sample, profile, language, variant = key.split("|")
    return {"sample": sample, "profile": profile, "language": language,
            "variant": variant, "family": family_of(sample)}


def _obs(grades: dict) -> list:
    out = []
    for key, v in grades.items():
        rec = _parse_key(key)
        rec["knob_rank"] = KNOB_RANK[rec["profile"]]
        rec["rating"] = v
        out.append(rec)
    return out


def _slope_block(obs: list) -> dict:
    res = A.cluster_bootstrap_slope(obs)
    res["p_bootstrap"] = A._bootstrap_pvalue(obs, res["slope"])
    by_knob = {}
    for p in KNOB_RANK:
        vals = [o["rating"] for o in obs if o["profile"] == p]
        by_knob[p] = {"mean": mean(vals) if vals else None, "n": len(vals)}
    res["by_knob"] = by_knob
    return res


def _width_of(sample: str) -> int:
    assert sample.startswith("agg_stats_W"), sample
    return int(sample.split("_W")[1])


def main() -> int:
    cache = json.load(open(CACHE))
    committed = json.load(open(COMMITTED))

    report: dict = {"per_model": {}, "mess_by_width_comprehend": {}}

    for task, _key in TASKS:
        for short, _slug in MODELS:
            ann1 = cache[task][short]["ann1"]
            un1 = cache[task][short]["un1"]
            common = sorted(set(ann1) & set(un1))
            if len(common) != 1500:
                sys.exit(f"{task}/{short}: expected 1500 common items, "
                         f"got {len(common)}")

            # KILL: overall means must equal the committed artifact exactly.
            pm = committed[task]["per_model"][short]
            got_a = mean([ann1[k] for k in common])
            got_u = mean([un1[k] for k in common])
            for got, want, name in ((got_a, pm["annotated_k1"], "annotated_k1"),
                                    (got_u, pm["unannotated_k1"],
                                     "unannotated_k1")):
                assert abs(got - want) < 1e-12, (
                    f"KILL: {task}/{short} {name}: {got!r} != {want!r}")

            block = report["per_model"].setdefault(task, {}).setdefault(
                short, {})
            block["annotated_k1"] = _slope_block(
                _obs({k: ann1[k] for k in common}))
            block["unannotated_k1"] = _slope_block(
                _obs({k: un1[k] for k in common}))
            block["delta_un_minus_ann"] = _slope_block(
                _obs({k: un1[k] - ann1[k] for k in common}))

            # Robustness of the unannotated slope: leave-one-family-out and
            # per-language point estimates (no CIs; the full-set CI above is
            # the inferential statement, these show no single family or
            # language carries it).
            un_obs = _obs({k: un1[k] for k in common})
            fams = sorted({o["family"] for o in un_obs})
            langs = sorted({o["language"] for o in un_obs})
            block["unannotated_robustness"] = {
                "leave_one_family_out": {
                    f: A.cluster_bootstrap_slope(
                        [o for o in un_obs if o["family"] != f])["slope"]
                    for f in fams},
                "per_language": {
                    lang: A.cluster_bootstrap_slope(
                        [o for o in un_obs if o["language"] == lang])["slope"]
                    for lang in langs},
            }
            u = block["unannotated_k1"]
            print(f"{task:10s} {short:18s} unann slope "
                  f"{u['slope']:+.4f} {u['ci95']} p={u['p_bootstrap']}",
                  flush=True)
    print("KILL passed: cache-derived overall means equal "
          "annotation_ablation.json exactly", flush=True)

    # ---- (c) mess-by-width slice, comprehend agg_stats ----
    for short, _slug in MODELS:
        ann8 = cache["comprehend"][short]["ann8"]
        un1 = cache["comprehend"][short]["un1"]
        grid: dict = {}
        for arm_name, grades in (("annotated_k8", ann8),
                                 ("unannotated_k1", un1)):
            for key, v in grades.items():
                rec = _parse_key(key)
                if rec["family"] != "agg_stats":
                    continue
                w = _width_of(rec["sample"])
                cell = grid.setdefault(arm_name, {}).setdefault(
                    rec["profile"], {}).setdefault(str(w),
                                                   {"vals": [], "n": 0})
                cell["vals"].append(v)
                cell["n"] += 1
        out_grid: dict = {}
        for arm_name, by_profile in grid.items():
            out_grid[arm_name] = {}
            for profile, by_w in by_profile.items():
                out_grid[arm_name][profile] = {
                    w: {"mean": mean(c["vals"]), "n": c["n"],
                        "illustration_only": True}
                    for w, c in sorted(by_w.items(), key=lambda kv:
                                       int(kv[0]))}

        # pooled high-W vs low-W knob slopes (unannotated arm), 10 clusters
        strata = {}
        for key, v in un1.items():
            rec = _parse_key(key)
            if rec["family"] != "agg_stats":
                continue
            w = _width_of(rec["sample"])
            band = "high_w" if w >= HIGH_W else "low_w"
            rec["knob_rank"] = KNOB_RANK[rec["profile"]]
            rec["rating"] = v
            strata.setdefault(band, []).append(rec)
        contrast = {band: _slope_block(obs) for band, obs in
                    sorted(strata.items())}
        report["mess_by_width_comprehend"][short] = {
            "grid": out_grid,
            "unannotated_knob_slope_by_width_band": contrast,
            "band_rule": f"high_w: W >= {HIGH_W}; low_w: W <= 32",
        }

    report["_meta"] = artifact_header(
        "unannotated_slopes.py",
        ["bench/out/.annotation_ablation_grades.json",
         "bench/out/annotation_ablation.json"],
        estimation_not_family=True,
        post_hoc=("registered 2026-08-14 after the suite's results were read; "
                  "the published knob-inertness result used the annotated arm "
                  "only, this recomputes it on the unannotated arm "
                  "(ANALYSIS_PLAN.md post-hoc addenda)"),
        knob_rank=KNOB_RANK,
    )
    write_artifact(OUT, report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
