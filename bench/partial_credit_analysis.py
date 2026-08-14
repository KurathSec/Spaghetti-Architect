"""Partial-credit unfolding of the agg_stats exact-match cliff. Zero API.

The published scaling table (bench/out/ladder_scaling.json) grades agg_stats
comprehension as all-or-nothing exact match, which collapses to 0.0 for most
models past W=16. This script re-reads the SAME committed raw completions
(bench/out/ladder/comprehend__<slug>.jsonl.gz, agg_stats records only) and
scores the "total" field with partial credit, per draw:

- pred  = bench.grade.extract_json_obj(raw)["total"] (None / missing /
  non-numeric predictions are COUNTED as unusable, never imputed);
- gold  = src.nodes.validator.oracle(program)["total"], with the program
  rebuilt through bench.tasks._rebuild_comprehend_item (the harness's own
  offline rebuild; asserted gold["total"] > 0).

Per (model, W) cell: median / IQR / 5%-winsorized mean of the relative error
|pred - gold| / gold over usable draws; the order-of-magnitude rate (usable
draws with pred > 0 and |log10(pred / gold)| <= 0.5; non-positive predictions
counted separately); and the exact-total rate over ALL draws (unusable draws
count as misses, so it is directly comparable to exact match).

Kill-switches (the published figures are FROZEN; a failure means THIS script
is wrong):
  KILL 1: per cell, the exact-total rate must be >= the recomputed exact
          match (mechanically: EM additionally requires peak/trough) and >=
          the published EM minus half a rounding ulp (5e-4; the published
          table is rounded to 3 decimals).
  KILL 2: exact match recomputed per cell from the same records via
          bench.grade.grade_comprehend_one must reproduce the published
          bench/out/ladder_scaling.json agg_stats value exactly (after the
          same round(., 3)) for every (model, W) that file has.

Cluster discipline: every agg_stats W cell is a SINGLE base IR (W16/W32 add
input variants v0..v4 of the same program), so every cell carries
n_clusters and is flagged "illustration_only" when n_clusters < 5 --
mirroring the paper's single-programme caveat. No bootstrap CIs are drawn
from single-cluster cells.

Output: bench/out/agg_partial_credit.json (deterministic; byte-identical on
re-run).

Usage:  python3 bench/partial_credit_analysis.py
"""

from __future__ import annotations

import json
import math
import os
import sys
from typing import Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bench.suite_common import (  # noqa: E402
    MODELS,
    artifact_header,
    base_ir,
    family_of,
    load_annotated,
    write_artifact,
)

from bench import grade as G  # noqa: E402
from bench import tasks as T  # noqa: E402
from src.nodes.validator import oracle  # noqa: E402

OUT = os.path.join(_HERE, "out", "agg_partial_credit.json")
PUBLISHED = os.path.join(_HERE, "out", "ladder_scaling.json")

W_SET = {8, 12, 16, 24, 32, 48, 64, 96, 128, 160}
K_DRAWS = 8               # every ladder record persists exactly 8 completions
MIN_CLUSTERS = 5          # below this a cell is illustration-only
ROUND_ULP = 5e-4          # published table is round(., 3)

# Program cache: one _rebuild_comprehend_item per dataset stem (the program is
# profile/language-independent, so 20 rebuilds cover all 1200 agg records).
_SPLIT = None
_PROG: Dict[str, object] = {}
_GOLD_TOTAL: Dict[str, int] = {}


def _program_for(rec: dict):
    """The oracle program for a record, via the harness's own offline rebuild."""
    global _SPLIT
    if _SPLIT is None:
        from bench import dataset as D
        _SPLIT = D.load("dev")
    stem = T._stem_for(_SPLIT, rec["sample"], rec.get("variant", "base"))
    if stem not in _PROG:
        prog = T._rebuild_comprehend_item(rec).program
        gold = oracle(prog)
        if "total" not in gold:
            sys.exit(f"{stem}: oracle output has no 'total' key: {sorted(gold)}")
        if not (gold["total"] > 0):
            sys.exit(f"{stem}: gold total {gold['total']!r} is not > 0; the "
                     "relative-error and log10 metrics assume a positive gold")
        _PROG[stem] = prog
        _GOLD_TOTAL[stem] = gold["total"]
    return stem


def _usable_total(pred: Optional[dict]):
    """The predicted total iff it is a finite JSON number; else None (unusable)."""
    if pred is None or "total" not in pred:
        return None
    v = pred["total"]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def _pctl(sorted_xs: List[float], q: float) -> float:
    """Percentile with linear interpolation (numpy 'linear'); input pre-sorted."""
    n = len(sorted_xs)
    if n == 0:
        raise ValueError("percentile of empty list")
    pos = q * (n - 1)
    lo = int(pos)
    frac = pos - lo
    if lo + 1 >= n or frac == 0.0:
        return sorted_xs[lo]
    return sorted_xs[lo] * (1.0 - frac) + sorted_xs[lo + 1] * frac


def _winsorized_mean(xs: List[float], frac: float = 0.05) -> float:
    """Mean after clipping the lowest/highest floor(frac*n) values inward."""
    if not xs:
        raise ValueError("winsorized mean of empty list")
    s = sorted(xs)
    n = len(s)
    k = int(frac * n)
    if k:
        s = [s[k]] * k + s[k:n - k] + [s[n - 1 - k]] * k
    return sum(s) / n


def _cell(recs: List[dict], published_em: float, label: str, w: int) -> dict:
    """All partial-credit statistics for one (model, W) cell + its kill-switches."""
    rel_errs: List[float] = []
    n_draws = n_unusable = n_nonpositive = n_oom = n_exact_total = 0
    em_rates: List[float] = []            # per-record exact-match rate (published defn)
    exact_rates: List[float] = []         # per-record exact-total rate (same aggregation)
    clusters = set()

    for rec in recs:
        raw = rec["raw_outputs"]
        if not raw or raw == ["<mock>"]:
            sys.exit(f"{label} W={w}: mock/empty raw_outputs in the committed "
                     "archive; refusing to grade")
        if len(raw) != K_DRAWS:
            sys.exit(f"{label} W={w}: record {rec['sample']} has {len(raw)} draws, "
                     f"expected {K_DRAWS} (pooled and per-record rates would diverge)")
        stem = _program_for(rec)
        prog, gold_total = _PROG[stem], _GOLD_TOTAL[stem]
        clusters.add(base_ir(rec["sample"]))

        rec_em = rec_exact = 0
        for out in raw:
            n_draws += 1
            # KILL 2 source: the harness's own per-draw exact-match grader.
            rec_em += G.grade_comprehend_one(out, prog)["exact_match"]
            pred_total = _usable_total(G.extract_json_obj(out))
            if pred_total is None:
                n_unusable += 1
                continue
            rel_errs.append(abs(pred_total - gold_total) / gold_total)
            if pred_total == gold_total:
                n_exact_total += 1
                rec_exact += 1
            if pred_total > 0:
                if abs(math.log10(pred_total / gold_total)) <= 0.5:
                    n_oom += 1
            else:
                n_nonpositive += 1
        em_rates.append(rec_em / K_DRAWS)
        exact_rates.append(rec_exact / K_DRAWS)

    n_usable = n_draws - n_unusable
    if not em_rates:
        sys.exit(f"{label} W={w}: no records in cell")
    em_unrounded = sum(em_rates) / len(em_rates)
    recomputed_em = round(em_unrounded, 3)        # the published table's rounding
    exact_total_rate = sum(exact_rates) / len(exact_rates)  # == n_exact_total / n_draws

    # KILL 2: recomputed EM must reproduce the frozen published value exactly.
    if recomputed_em != published_em:
        sys.exit(f"KILL 2 [{label} W={w}]: recomputed EM {recomputed_em} "
                 f"(unrounded {em_unrounded}) != published {published_em}; "
                 "this script's grading path is wrong")
    # KILL 1: exact-total can only relax EM (EM additionally needs peak/trough).
    if exact_total_rate < em_unrounded - 1e-12:
        sys.exit(f"KILL 1 [{label} W={w}]: exact-total rate {exact_total_rate} < "
                 f"recomputed EM {em_unrounded}; grading paths disagree")
    if exact_total_rate < published_em - ROUND_ULP:
        sys.exit(f"KILL 1 [{label} W={w}]: exact-total rate {exact_total_rate} < "
                 f"published EM {published_em} (beyond rounding); script is wrong")

    cell = {
        "n_records": len(recs),
        "n_draws": n_draws,
        "n_unusable": n_unusable,
        "n_usable": n_usable,
        "n_nonpositive_pred": n_nonpositive,
        "n_clusters": len(clusters),
        "illustration_only": len(clusters) < MIN_CLUSTERS,
        "exact_total_rate": exact_total_rate,
        "order_of_magnitude_rate": (n_oom / n_usable) if n_usable else None,
        "published_em": published_em,
        "recomputed_em": recomputed_em,
        "recomputed_em_unrounded": em_unrounded,
    }
    if rel_errs:
        s = sorted(rel_errs)
        q25, q75 = _pctl(s, 0.25), _pctl(s, 0.75)
        cell.update({
            "rel_err_median": _pctl(s, 0.5),
            "rel_err_q25": q25,
            "rel_err_q75": q75,
            "rel_err_iqr": q75 - q25,
            "rel_err_winsorized_mean_5pct": _winsorized_mean(rel_errs),
        })
    else:  # n-guard: never average an empty list
        cell.update({"rel_err_median": None, "rel_err_q25": None,
                     "rel_err_q75": None, "rel_err_iqr": None,
                     "rel_err_winsorized_mean_5pct": None})
    return cell


def main() -> int:
    published = json.load(open(PUBLISHED))
    report = {
        "_meta": artifact_header(
            "partial_credit_analysis.py",
            ["bench/out/ladder/comprehend__*.jsonl.gz",
             "bench/out/ladder_scaling.json"],
            family="agg_stats",
            definitions={
                "pred": "extract_json_obj(raw)['total']; None/missing/non-numeric "
                        "(incl. booleans, non-finite) -> n_unusable, never imputed",
                "gold": "src.nodes.validator.oracle(rebuilt program)['total'], "
                        "asserted > 0",
                "rel_err": "|pred - gold| / gold over USABLE draws; median/quartiles "
                           "use linear interpolation; winsorized mean clips "
                           "floor(0.05*n) values in each tail",
                "order_of_magnitude_rate": "usable draws with pred > 0 and "
                                           "|log10(pred/gold)| <= 0.5, over usable "
                                           "draws (non-positive preds counted in "
                                           "n_nonpositive_pred)",
                "exact_total_rate": "draws with usable pred == gold, over ALL draws "
                                    "(unusable draws are misses) -- comparable to "
                                    "exact match, which additionally requires "
                                    "peak/trough",
                "illustration_only": f"n_clusters < {MIN_CLUSTERS} base IRs in the "
                                     "cell (every agg_stats W is a single base "
                                     "program; W16/W32 variants share theirs), so "
                                     "no CI is drawn and the cell is descriptive",
            },
            kill_switches=[
                "KILL 1: exact_total_rate >= recomputed EM (exact) and >= "
                "published EM - 5e-4 (rounding ulp) per cell",
                "KILL 2: round(recomputed EM, 3) == published "
                "ladder_scaling.json agg_stats value per (model, W)",
            ]),
        "per_model": {},
    }

    for label, slug in MODELS:
        pub_fam = published.get(label, {}).get("agg_stats")
        if not pub_fam:
            sys.exit(f"{label}: no agg_stats block in {PUBLISHED}")
        recs = [r for r in load_annotated("comprehend", slug)
                if family_of(r["sample"]) == "agg_stats"]
        by_w: Dict[int, List[dict]] = {}
        for r in recs:
            w = r["intrinsic"]["W"]
            if w not in W_SET:
                sys.exit(f"{label}: unexpected W={w} for {r['sample']}")
            by_w.setdefault(w, []).append(r)
        if set(by_w) != W_SET:
            sys.exit(f"{label}: W coverage {sorted(by_w)} != expected {sorted(W_SET)}")
        if {int(k) for k in pub_fam} != W_SET:
            sys.exit(f"{label}: published W coverage {sorted(pub_fam)} != "
                     f"expected {sorted(W_SET)}")
        if len(recs) != 300:
            sys.exit(f"{label}: {len(recs)} agg_stats records, expected 300 "
                     "(20 stems x 3 profiles x 5 languages)")

        model_out = {}
        for w in sorted(W_SET):
            model_out[str(w)] = _cell(by_w[w], pub_fam[str(w)], label, w)
        report["per_model"][label] = model_out
        line = "  ".join(
            f"W{w}: em={model_out[str(w)]['recomputed_em']:.3f} "
            f"exact_total={model_out[str(w)]['exact_total_rate']:.3f} "
            f"oom={model_out[str(w)]['order_of_magnitude_rate']}"
            for w in sorted(W_SET))
        print(f"{label}: kill-switches OK\n  {line}")

    write_artifact(OUT, report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
