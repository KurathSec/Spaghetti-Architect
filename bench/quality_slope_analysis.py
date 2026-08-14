"""Does the incidental (messiness) knob move MODEL refactoring quality? Zero API.

The paper's construct validity shows the knob moves static metrics; its
baselines showed comprehension exact match flat across the knob, and the
model-behavioral payoff of the incidental axis was declared "enabled but not
demonstrated". This script asks the remaining question directly: regress the
Python-lane semantic-gated simplification-quality of the committed ANNOTATED
refactor completions on the knob rank (minimal < standard < max, the three
scored levels).

Three outcomes per model, so the semantic gate cannot masquerade as quality
drift:
  - gate:          semantic_ok rate (equivalence gate)
  - quality_cond:  simplification_quality conditional on semantic_ok == 1
  - quality_uncond: unconditional simplification_quality (zero-inflated by the
                    gate; reported, not confirmatory)
Only the four quality_cond slopes enter the suite's confirmatory p-value
family (see ANALYSIS_PLAN.md).

Grading is arm-sensitive (spaghetti_src is the recovery denominator), so this
script grades the ANNOTATED arm only and refuses to run in a stripped process
(suite_common guard). Python lane only: no toolchain beyond python needed.

Output: bench/out/quality_slope.json.
Kill-switch: the per-item semantic_ok rate recomputed here must equal the
ablation grade cache (ann8) on every Python item.
"""

from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bench.suite_common import (  # noqa: E402
    GRADES_CACHE,
    MODELS,
    artifact_header,
    base_ir,
    family_of,
    item_key,
    load_annotated,
    mean,
    write_artifact,
)
from bench import analysis as A  # noqa: E402
from bench import grade as G  # noqa: E402
from bench import tasks as T  # noqa: E402

OUT = os.path.join(_HERE, "out", "quality_slope.json")
KNOB_RANK = {"minimal": 0, "standard": 1, "max": 2}


def _grade_python_items(slug: str) -> list:
    """Per-item mean-over-draws outcomes for the Python refactor records."""
    recs = [r for r in load_annotated("refactor", slug)
            if r.get("language") == "python" and r.get("raw_outputs")]

    def one(rec):
        it = T._rebuild_refactor_item(rec)
        per = [G.grade_refactor_one(it.language, out, it.spaghetti_src, it.program)
               for out in rec["raw_outputs"]]
        gates = [p["semantic_ok"] for p in per]
        quals = [p["simplification_quality"] for p in per]
        ok_quals = [q for q, g_ok in zip(quals, gates) if g_ok and q is not None]
        return {
            "key": item_key(rec),
            "sample": rec["sample"],
            "profile": rec["profile"],
            "gate": mean([1.0 if g_ok else 0.0 for g_ok in gates]),
            "quality_uncond": mean([q if q is not None else 0.0 for q in quals]),
            "quality_cond": mean(ok_quals) if ok_quals else None,
            "n_draws": len(per),
            "n_ok_draws": len(ok_quals),
        }

    with ThreadPoolExecutor(max_workers=min(os.cpu_count() or 4, 10)) as ex:
        return list(ex.map(one, recs))


def _slopes(items: list, outcome: str) -> dict:
    obs = [{"family": family_of(i["sample"]), "sample": base_ir(i["sample"]),
            "knob_rank": KNOB_RANK[i["profile"]], "rating": i[outcome]}
           for i in items if i[outcome] is not None]
    res = A.cluster_bootstrap_slope(obs)
    res["p_bootstrap"] = A._bootstrap_pvalue(obs, res["slope"])
    res["n_dropped_none"] = len(items) - len(obs)
    # Per-knob means for readability (n-guarded).
    by_knob = {}
    for p, r in KNOB_RANK.items():
        vals = [i[outcome] for i in items if i["profile"] == p and i[outcome] is not None]
        by_knob[p] = {"mean": mean(vals) if vals else None, "n": len(vals)}
    res["by_knob"] = by_knob
    return res


def _crosscheck_gate(items: list, slug: str, short: str) -> None:
    """Per-item gate must equal the ablation cache's ann8 semantic_ok rate."""
    if not os.path.exists(GRADES_CACHE):
        print("  (grade cache absent; skipping gate cross-check)")
        return
    cache = json.load(open(GRADES_CACHE))
    ann8 = cache["refactor"][short]["ann8"]
    for i in items:
        want = ann8.get(i["key"])
        if want is None:
            continue
        if abs(i["gate"] - want) > 1e-9:
            sys.exit(f"gate mismatch vs cache at {i['key']}: {i['gate']} != {want}")


def main() -> int:
    report = {"_meta": artifact_header(
        "quality_slope_analysis.py",
        ["bench/out/g3/refactor_dev__*.jsonl.gz"],
        lane="python", knob_levels=list(KNOB_RANK),
        outcomes=["gate", "quality_cond", "quality_uncond"],
        confirmatory="quality_cond slopes only")}
    for short, slug in MODELS:
        items = _grade_python_items(slug)
        if len(items) != 300:
            sys.exit(f"{short}: expected 300 Python refactor items, got {len(items)}")
        _crosscheck_gate(items, slug, short)
        report[short] = {o: _slopes(items, o)
                         for o in ("gate", "quality_cond", "quality_uncond")}
        qc = report[short]["quality_cond"]
        print(f"{short:18s} quality_cond slope {qc['slope']:+.4f} {qc['ci95']} "
              f"p={qc['p_bootstrap']}  by_knob "
              f"{[round(qc['by_knob'][p]['mean'], 3) for p in KNOB_RANK]}")
    write_artifact(OUT, report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
