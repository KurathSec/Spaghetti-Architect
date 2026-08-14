"""Anchor construct-validity statistics on degenerate vs non-degenerate samples.

Zero API. Re-aggregates the committed per-source anchor table
(``bench/out/anchor.json``: ``rows`` + ``bw_readability_rows``, 300 rows =
50 base samples x 6 knob levels each) three ways:

(a) reproduces the committed full-set per-sample Spearman(knob rank, metric)
    means for radon_cc / lizard_cc / cognitive_cognitive / radon_mi / bw_proxy
    via the exact ``bench/anchor.py::_incidental_spearman_stats`` convention
    (bench.grade.spearman per sample, mean over samples, ci95_bootstrap with
    its default iters=2000/seed=0 over the per-sample rhos in row order);
(b) recomputes the same statistics on the degenerate base-sample subset (from
    ``bench/out/provenance_figures.json``) and its complement, recording the
    per-sample distinct-metric-level resolution of each subset;
(c) adds Kendall tau-b (pure stdlib, full tie corrections) under the same
    per-sample aggregation for the full set and both subsets.

Inputs: bench/out/anchor.json (committed), bench/out/provenance_figures.json
(run bench/provenance_figures.py first).
Output: bench/out/anchor_subsets.json (deterministic; byte-identical re-runs).

Kill-switches (the committed figures are frozen; a mismatch means THIS script
is wrong): the five full-set Spearman means must reproduce the published
+0.75 / +0.75 / +0.80 / -0.79 / +0.39 to +-0.005 AND match the committed
anchor.json stats (mean/CI/n) to 1e-9; the two subsets must partition the 50
base samples (n_deg + n_nondeg == 50); every degenerate sample must show at
most 3 distinct metric levels across its 6 knob rows (clean + 2 distinct
spaghetti renders); the tau-b implementation must pass hand-computed tied
cases before the main run; and on any sample without ties sign(tau_b) must
equal sign(spearman rho).
"""

from __future__ import annotations

import json
import math
import os
import sys
from typing import Dict, List, Sequence, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bench.suite_common import (  # noqa: E402
    artifact_header,
    mean,
    write_artifact,
)

# bench.anchor -> bench.grade -> bench.uniform_lane -> bench.anchor is a cycle
# when anchor is the first of the three imported; loading grade first defuses it.
from bench import grade as G  # noqa: E402
from bench.anchor import _incidental_spearman_stats  # noqa: E402

OUT = os.path.join(_HERE, "out", "anchor_subsets.json")
ANCHOR = os.path.join(_HERE, "out", "anchor.json")
PROVENANCE = os.path.join(_HERE, "out", "provenance_figures.json")

N_BASE = 50
N_KNOBS = 6
# Published full-set figures (frozen): metric -> (rows key, rounded mean).
PUBLISHED = {
    "radon_cc": ("rows", +0.75),
    "lizard_cc": ("rows", +0.75),
    "cognitive_cognitive": ("rows", +0.80),
    "radon_mi": ("rows", -0.79),
    "bw_proxy": ("bw_readability_rows", +0.39),
}
PUBLISHED_TOL = 0.005
EXACT_TOL = 1e-9
# Committed anchor.json location of each metric's full-set stats block.
_COMMITTED_PATH = {
    "radon_cc": ("anchors", "radon", "metrics", "cc"),
    "lizard_cc": ("anchors", "lizard", "metrics", "cc"),
    "cognitive_cognitive": ("anchors", "cognitive", "metrics", "cognitive"),
    "radon_mi": ("anchors", "radon", "metrics", "mi"),
    "bw_proxy": ("anchors", "bw_readability", "aggregate_density_proxy"),
}
DEG_MAX_LEVELS = 3  # clean render + at most 2 distinct spaghetti renders


# --------------------------------------------------------------------------- #
# Kendall tau-b: pure stdlib, full tie corrections, O(n^2) over 6 points.
# --------------------------------------------------------------------------- #
def kendall_tau_b(x: Sequence[float], y: Sequence[float]) -> float:
    """tau_b = sum(sign(dx)*sign(dy)) / sqrt((n0-n1)(n0-n2)); n1/n2 count
    pairs tied in x/y (a pair tied in both counts in both). 0.0 when either
    variable is constant (degenerate denominator)."""
    n = len(x)
    if n < 2 or len(y) != n:
        return 0.0
    num = 0
    ties_x = 0
    ties_y = 0
    for i in range(n):
        for j in range(i + 1, n):
            dx = (x[i] > x[j]) - (x[i] < x[j])
            dy = (y[i] > y[j]) - (y[i] < y[j])
            if dx == 0:
                ties_x += 1
            if dy == 0:
                ties_y += 1
            num += dx * dy
    n0 = n * (n - 1) // 2
    den = math.sqrt((n0 - ties_x) * (n0 - ties_y))
    return num / den if den > 0 else 0.0


def _self_test_tau() -> None:
    """Hand-computed 6-point cases; a failure means the implementation is wrong."""
    # Ties in y only: x=0..5, y=[10,10,20,20,30,30]. n0=15; n1=0; three tied-y
    # pairs -> n2=3; the remaining 12 pairs are all concordant, 0 discordant.
    # tau_b = 12 / sqrt(15 * 12) = 12 / sqrt(180).
    cases = [
        ([0, 1, 2, 3, 4, 5], [10, 10, 20, 20, 30, 30], 12.0 / math.sqrt(180.0)),
        # Ties in both: x=[1,1,2,2,3,3], y=[1,2,1,2,3,3]. Pairwise by hand:
        # concordant 9, discordant 1 (points (1,2)-(2,1)) -> num=8; tied-x pairs
        # {12,34,56} -> n1=3; tied-y pairs {13,24,56} -> n2=3.
        # tau_b = 8 / sqrt(12 * 12) = 2/3.
        ([1, 1, 2, 2, 3, 3], [1, 2, 1, 2, 3, 3], 2.0 / 3.0),
        # Monotone no-tie sanity: perfect agreement / reversal.
        ([0, 1, 2, 3, 4, 5], [3, 5, 8, 9, 14, 20], 1.0),
        ([0, 1, 2, 3, 4, 5], [20, 14, 9, 8, 5, 3], -1.0),
        # Constant y: degenerate denominator collapses to 0.
        ([0, 1, 2, 3, 4, 5], [7, 7, 7, 7, 7, 7], 0.0),
    ]
    for x, y, expected in cases:
        got = kendall_tau_b(x, y)
        if abs(got - expected) > 1e-12:
            sys.exit(f"tau_b self-test failed: tau_b({x}, {y}) = {got}, "
                     f"expected {expected}")


# --------------------------------------------------------------------------- #
# per-sample series (the bench/anchor.py grouping convention: first-appearance
# order over rows, each sample's 6 rows sorted by knob rank)
# --------------------------------------------------------------------------- #
def _per_sample_series(rows: List[dict], col: str) -> List[Tuple[str, List[int], List[float]]]:
    by_sample: Dict[str, List[dict]] = {}
    for r in rows:
        by_sample.setdefault(r["sample"], []).append(r)
    out = []
    for sample, rs in by_sample.items():
        rs = sorted(rs, key=lambda r: r["rank"])
        if len(rs) < 2:
            continue
        out.append((sample, [r["rank"] for r in rs], [r[col] for r in rs]))
    return out


def _tau_stats(rows: List[dict], col: str) -> dict:
    """Mean + bootstrap CI + n of per-sample tau_b, mirroring the Spearman
    aggregation (and its CI convention: grade.ci95_bootstrap defaults)."""
    taus = [kendall_tau_b(xs, ys) for _, xs, ys in _per_sample_series(rows, col)]
    if not taus:
        sys.exit(f"no per-sample series for column {col}: empty aggregate")
    return {
        "mean": mean(taus),
        "ci95": (G.ci95_bootstrap(taus) if len(taus) > 1 else None),
        "n_base_samples": len(taus),
    }


def _distinct_levels(rows: List[dict], col: str) -> Dict[str, int]:
    """Per-sample count of distinct metric values across the 6 knob rows."""
    return {sample: len(set(ys))
            for sample, _, ys in _per_sample_series(rows, col)}


def _dig(obj: dict, path: Tuple[str, ...]) -> dict:
    for k in path:
        obj = obj[k]
    return obj


def main() -> int:
    _self_test_tau()  # also guards library callers that bypass __main__

    if not os.path.exists(PROVENANCE):
        sys.exit("missing bench/out/provenance_figures.json: run "
                 "bench/provenance_figures.py first (it defines the degenerate "
                 "base-sample subset this script conditions on)")
    anchor = json.load(open(ANCHOR))
    prov = json.load(open(PROVENANCE))

    tables = {"rows": anchor["rows"],
              "bw_readability_rows": anchor["bw_readability_rows"]}
    for name, rows in tables.items():
        if len(rows) != N_BASE * N_KNOBS:
            sys.exit(f"{name}: expected {N_BASE * N_KNOBS} rows, got {len(rows)}")
    all_samples = set(r["sample"] for r in tables["rows"])
    if all_samples != set(r["sample"] for r in tables["bw_readability_rows"]):
        sys.exit("rows and bw_readability_rows cover different sample sets")
    if len(all_samples) != N_BASE:
        sys.exit(f"expected {N_BASE} base samples, got {len(all_samples)}")

    deg_list = prov.get("degenerate_base_samples")
    if deg_list is None:
        deg_list = prov["inert_cells_report"]["degenerate_base_samples"]
    deg = set(deg_list)
    if not deg <= all_samples:
        sys.exit(f"degenerate samples not in the anchor table: "
                 f"{sorted(deg - all_samples)}")
    nondeg = all_samples - deg
    # KILL: the two subsets must partition the 50 base samples.
    if len(deg) + len(nondeg) != N_BASE:
        sys.exit(f"subset partition broken: {len(deg)} + {len(nondeg)} != {N_BASE}")

    subset_samples = {"full": all_samples, "degenerate": deg,
                      "non_degenerate": nondeg}

    spearman_by_metric: Dict[str, dict] = {}
    tau_b_by_metric: Dict[str, dict] = {}
    published_check: Dict[str, dict] = {}
    levels_by_subset: Dict[str, dict] = {s: {} for s in ("degenerate",
                                                         "non_degenerate")}
    sign_property = {"n_samples_checked": 0, "n_sign_mismatches": 0}

    for metric, (table_key, published) in PUBLISHED.items():
        rows = tables[table_key]

        # --- (a) full set: reproduce the committed figures -----------------
        full = _incidental_spearman_stats(rows, metric)
        if full["n_base_samples"] != N_BASE:
            sys.exit(f"{metric}: full-set n {full['n_base_samples']} != {N_BASE}")
        if abs(full["mean"] - published) > PUBLISHED_TOL:
            sys.exit(f"{metric}: recomputed Spearman mean {full['mean']:+.4f} "
                     f"is not within {PUBLISHED_TOL} of the published "
                     f"{published:+.2f} -- this script is wrong, fix it")
        committed = _dig(anchor, _COMMITTED_PATH[metric])
        if abs(full["mean"] - committed["incidental_knob_spearman_mean"]) > EXACT_TOL:
            sys.exit(f"{metric}: mean {full['mean']} != committed "
                     f"{committed['incidental_knob_spearman_mean']}")
        c_ci = committed["incidental_knob_spearman_ci95"]
        if any(abs(a - b) > EXACT_TOL for a, b in zip(full["ci95"], c_ci)):
            sys.exit(f"{metric}: ci95 {full['ci95']} != committed {c_ci}")
        if full["n_base_samples"] != committed["incidental_knob_spearman_n"]:
            sys.exit(f"{metric}: n mismatch vs committed")
        published_check[metric] = {
            "published_rounded": published,
            "recomputed_mean": full["mean"],
            "abs_diff": abs(full["mean"] - published),
            "committed_mean": committed["incidental_knob_spearman_mean"],
            "matches_committed": True,
        }

        # --- (b) subsets ----------------------------------------------------
        sp = {"full": full}
        tb = {"full": _tau_stats(rows, metric)}
        for sub in ("degenerate", "non_degenerate"):
            sub_rows = [r for r in rows if r["sample"] in subset_samples[sub]]
            sp[sub] = _incidental_spearman_stats(sub_rows, metric)
            tb[sub] = _tau_stats(sub_rows, metric)
            levels = _distinct_levels(sub_rows, metric)
            hist: Dict[str, int] = {}
            for n in levels.values():
                hist[str(n)] = hist.get(str(n), 0) + 1
            levels_by_subset[sub][metric] = {
                "histogram": hist,
                "min": min(levels.values()),
                "max": max(levels.values()),
            }
            # KILL: degenerate samples have at most 3 distinct metric levels
            # (clean + 2 distinct spaghetti renders per provenance_figures).
            if sub == "degenerate" and max(levels.values()) > DEG_MAX_LEVELS:
                worst = max(levels, key=levels.get)
                sys.exit(f"{metric}: degenerate sample {worst} shows "
                         f"{levels[worst]} distinct levels (> {DEG_MAX_LEVELS})")
        spearman_by_metric[metric] = sp
        tau_b_by_metric[metric] = tb

        # --- (c) sign-property check on tie-free samples --------------------
        # x is the knob rank 0..5 (never tied); a sample is tie-free iff its 6
        # metric values are all distinct. sign(tau_b) must equal sign(rho)
        # there. On this data every sample carries at least one tied pair
        # (light == standard renders byte-identically), so the set is empty;
        # the check still runs so a future anchor re-run is covered.
        for sample, xs, ys in _per_sample_series(rows, metric):
            if len(set(ys)) < len(ys):
                continue
            sign_property["n_samples_checked"] += 1
            t = kendall_tau_b(xs, ys)
            r = G.spearman([float(v) for v in xs], [float(v) for v in ys])
            st = (t > 1e-12) - (t < -1e-12)
            sr = (r > 1e-12) - (r < -1e-12)
            if st != sr:
                sign_property["n_sign_mismatches"] += 1
                sys.exit(f"{metric}/{sample}: tie-free sample has "
                         f"sign(tau_b)={st} but sign(rho)={sr}")

    deg_max_levels = max(v["max"] for v in levels_by_subset["degenerate"].values())
    nondeg_max_levels = max(v["max"]
                            for v in levels_by_subset["non_degenerate"].values())

    report = {
        "_meta": artifact_header(
            "anchor_subset_analysis.py",
            ["bench/out/anchor.json", "bench/out/provenance_figures.json"],
            subset_source=("provenance_figures.json "
                           "inert_cells_report.degenerate_base_samples"),
            ci_convention=("bench.grade.ci95_bootstrap defaults (iters=2000, "
                           "seed=0) over per-sample coefficients in row order, "
                           "matching bench/anchor.py _incidental_spearman_stats"),
        ),
        "published_check": published_check,
        "spearman_by_metric": spearman_by_metric,
        "tau_b_by_metric": tau_b_by_metric,
        "subsets": {
            "degenerate": {
                "n_base_samples": len(deg),
                "samples": sorted(deg),
                "low_resolution": deg_max_levels <= DEG_MAX_LEVELS,
                "max_distinct_levels_any_metric": deg_max_levels,
                "n_distinct_levels": levels_by_subset["degenerate"],
                "note": ("low-resolution subset: each sample's 6 knob rows "
                         "collapse to at most 3 distinct metric levels (clean "
                         "+ 2 distinct spaghetti renders), so its per-sample "
                         "rank correlations are dominated by tie corrections "
                         "and quantize to a few attainable values"),
            },
            "non_degenerate": {
                "n_base_samples": len(nondeg),
                "samples": sorted(nondeg),
                "low_resolution": nondeg_max_levels <= DEG_MAX_LEVELS,
                "max_distinct_levels_any_metric": nondeg_max_levels,
                "n_distinct_levels": levels_by_subset["non_degenerate"],
            },
        },
        "tau_b_self_test": {
            "hand_cases_passed": True,
            "sign_property": dict(
                sign_property,
                note=("checked on tie-free samples only (knob ranks never "
                      "tie; a sample qualifies iff its 6 metric values are "
                      "distinct); empty on this data because light==standard "
                      "renders byte-identically, so every sample has a tie"),
            ),
        },
    }

    for metric in PUBLISHED:
        sp = spearman_by_metric[metric]
        tb = tau_b_by_metric[metric]
        print(f"{metric:>20}: rho full {sp['full']['mean']:+.4f} | "
              f"deg {sp['degenerate']['mean']:+.4f} (n={sp['degenerate']['n_base_samples']}) | "
              f"nondeg {sp['non_degenerate']['mean']:+.4f} (n={sp['non_degenerate']['n_base_samples']})   "
              f"tau_b full {tb['full']['mean']:+.4f} | deg {tb['degenerate']['mean']:+.4f} | "
              f"nondeg {tb['non_degenerate']['mean']:+.4f}")
    write_artifact(OUT, report)
    return 0


if __name__ == "__main__":
    _self_test_tau()  # hand-computed tied cases must pass before the main run
    sys.exit(main())
