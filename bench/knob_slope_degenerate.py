"""Incidental-knob slopes with the degenerate items excluded. Zero API.

36 of the 100 dev items render only two distinct sources across the five
profiles (the committed inert-cells report), so the scored knob carries little
or no variation there. This re-estimates the knob-rank slopes on both arms
(and the per-item arm delta) with those 36 items excluded, and, as a coarser
variant, with the 16 metric-degenerate base samples excluded, using exactly
the estimation of ``bench/unannotated_slopes.py`` (its ``_obs``/``_slope_block``
are imported, not re-implemented). If the published all-eight-cells-negative
pattern is carried by the degenerate items, it disappears here; the summary
counts report whatever happens.

Post-hoc addendum (registered in ANALYSIS_PLAN.md after the suite's results
were read; estimation only, no confirmatory family members). Prompted by
external review: the masking claim's independent variable loses variation on
about a third of items.

Inputs (all committed; nothing is fetched):
  bench/out/.annotation_ablation_grades.json     cached k=1 grades, both arms
  bench/out/annotation_ablation.json             committed per-model means
  bench/out/unannotated_slopes.json              committed full-set slopes
  bench/out/provenance_figures.json              the degenerate item/base lists
  bench/out/anchor_subsets.json                  the same base list, cross-check

Kill-switches (frozen facts; a failure means THIS script is wrong):
  * cache means equal annotation_ablation.json annotated_k1/unannotated_k1
    exactly, both tasks, all four models;
  * the full-set slopes recomputed here equal unannotated_slopes.json exactly
    (annotated_k1, unannotated_k1, delta_un_minus_ann; all eight cells);
  * the committed lists are internally consistent: 36 items whose base
    samples are exactly the 16 degenerate base samples, and that base list
    equals anchor_subsets.json subsets.degenerate.samples.

Output: bench/out/knob_slope_degenerate.json (deterministic; re-runs are
byte-identical).
"""

from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bench.annotation_ablation import (  # noqa: E402
    MODELS,
    TASKS,
    _base_ir as base_ir,
)
from bench.suite_common import (  # noqa: E402
    artifact_header,
    mean,
    write_artifact,
)
from bench.unannotated_slopes import _obs, _slope_block  # noqa: E402

CACHE = os.path.join(_HERE, "out", ".annotation_ablation_grades.json")
COMMITTED = os.path.join(_HERE, "out", "annotation_ablation.json")
FULLSET = os.path.join(_HERE, "out", "unannotated_slopes.json")
PROVENANCE = os.path.join(_HERE, "out", "provenance_figures.json")
ANCHOR_SUBSETS = os.path.join(_HERE, "out", "anchor_subsets.json")
OUT = os.path.join(_HERE, "out", "knob_slope_degenerate.json")

ARMS = ("annotated_k1", "unannotated_k1", "delta_un_minus_ann")


def _load_lists() -> tuple:
    rep = json.load(open(PROVENANCE))["inert_cells_report"]
    items = sorted(rep["degenerate_items"])
    bases = sorted(rep["degenerate_base_samples"])
    if len(items) != rep["n_degenerate_items"] or len(items) != 36:
        sys.exit(f"KILL: {len(items)} degenerate items, expected 36")
    if sorted({base_ir(s) for s in items}) != bases or len(bases) != 16:
        sys.exit("KILL: degenerate items do not collapse to the 16 committed "
                 "base samples")
    anchor = sorted(json.load(open(ANCHOR_SUBSETS))
                    ["subsets"]["degenerate"]["samples"])
    if anchor != bases:
        sys.exit("KILL: anchor_subsets.json degenerate base list differs from "
                 "provenance_figures.json")
    return set(items), set(bases)


def _arm_grades(cache: dict, task: str, short: str) -> dict:
    ann1 = cache[task][short]["ann1"]
    un1 = cache[task][short]["un1"]
    common = sorted(set(ann1) & set(un1))
    if len(common) != 1500:
        sys.exit(f"{task}/{short}: {len(common)} common items != 1500")
    return {
        "annotated_k1": {k: ann1[k] for k in common},
        "unannotated_k1": {k: un1[k] for k in common},
        "delta_un_minus_ann": {k: un1[k] - ann1[k] for k in common},
    }


def main() -> int:
    cache = json.load(open(CACHE))
    committed = json.load(open(COMMITTED))
    fullset = json.load(open(FULLSET))["per_model"]
    deg_items, deg_bases = _load_lists()

    report: dict = {"per_model": {}, "summary": {}}
    counts = {v: {"sign_negative": 0, "ci_excludes_zero": 0}
              for v in ("full", "excl_items", "excl_bases")}

    for task, _key in TASKS:
        for short, _slug in MODELS:
            grades = _arm_grades(cache, task, short)
            # KILL: cache means equal the committed artifact exactly.
            pm = committed[task]["per_model"][short]
            for arm, want_key in (("annotated_k1", "annotated_k1"),
                                  ("unannotated_k1", "unannotated_k1")):
                got = mean(list(grades[arm].values()))
                assert abs(got - pm[want_key]) < 1e-12, (
                    f"KILL: {task}/{short} {want_key} cache mean {got!r} "
                    f"!= committed {pm[want_key]!r}")

            block = report["per_model"].setdefault(task, {}).setdefault(
                short, {})
            for arm in ARMS:
                g = grades[arm]
                subsets = {
                    "full": g,
                    "excl_items": {k: v for k, v in g.items()
                                   if k.split("|")[0] not in deg_items},
                    "excl_bases": {k: v for k, v in g.items()
                                   if base_ir(k.split("|")[0])
                                   not in deg_bases},
                }
                arm_out = {}
                for name, sub in subsets.items():
                    sb = _slope_block(_obs(sub))
                    sb["n_obs"] = len(sub)
                    arm_out[name] = sb
                # KILL: the full-set slope must reproduce the committed one.
                want = fullset[task][short][arm]["slope"]
                got = arm_out["full"]["slope"]
                assert abs(got - want) < 1e-12, (
                    f"KILL: {task}/{short}/{arm} full slope {got!r} != "
                    f"unannotated_slopes.json {want!r}")
                block[arm] = arm_out
                if arm == "unannotated_k1":
                    for name, sb in arm_out.items():
                        if sb["slope"] < 0:
                            counts[name]["sign_negative"] += 1
                        if sb["ci95"][1] < 0:
                            counts[name]["ci_excludes_zero"] += 1
            u = block["unannotated_k1"]
            print(f"{task:10s} {short:18s} unann slope full "
                  f"{u['full']['slope']:+.4f} -> excl-36 "
                  f"{u['excl_items']['slope']:+.4f} {u['excl_items']['ci95']} "
                  f"(n {u['excl_items']['n_obs']})", flush=True)

    report["summary"] = {
        "unannotated_cells_of_8": counts,
        "exclusions": {
            "excl_items": {"rule": "drop the 36 degenerate items "
                                   "(2 distinct renders across the 5 "
                                   "profiles; all languages)",
                           "n_items_dropped": len(deg_items)},
            "excl_bases": {"rule": "drop every item of the 16 "
                                   "metric-degenerate base samples",
                           "n_base_samples_dropped": len(deg_bases)},
        },
    }
    report["_meta"] = artifact_header(
        "knob_slope_degenerate.py",
        ["bench/out/.annotation_ablation_grades.json",
         "bench/out/annotation_ablation.json",
         "bench/out/unannotated_slopes.json",
         "bench/out/provenance_figures.json",
         "bench/out/anchor_subsets.json"],
        estimation_not_family=True,
        post_hoc=("registered 2026-09-01 after the suite's results were read "
                  "(ANALYSIS_PLAN.md post-hoc addenda); prompted by external "
                  "review of the degenerate-item share"),
    )
    print("summary (unannotated, of 8 cells):", json.dumps(
        report["summary"]["unannotated_cells_of_8"]), flush=True)
    write_artifact(OUT, report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
