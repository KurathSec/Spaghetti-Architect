#!/usr/bin/env python3
"""Phase G — construct-validity anchors (non-LLM, **quarantined, multi-anchor**).

This is the only place *external* third-party tools are used, and they are kept off
the zero-dependency core: run this with a throwaway virtualenv that has the anchor
tools installed, e.g.

    python3 -m venv /tmp/anchorvenv
    /tmp/anchorvenv/bin/pip install radon lizard cognitive_complexity
    /tmp/anchorvenv/bin/python bench/anchor.py

It scores the **public dev** Python sources with several *independent* complexity /
maintainability metrics — convergent witnesses for the by-construction knob — then
asks, for each available anchor:

1. **Does the ground-truth incidental knob move the external metric monotonically?**
   Per base sample, Spearman(knob rank, metric) over ``clean`` -> ``minimal`` ->
   ``light`` -> ``standard`` -> ``heavy`` -> ``max`` at fixed semantics. We report
   the mean over samples. Complexity metrics should rise with the knob (rho -> +1)
   and maintainability metrics should fall (rho -> -1).
2. **Does the intrinsic knob move it?** Spearman(``config_resolver`` N, metric) at a
   fixed profile.
3. **Is our own metric lane anchored to the external one?** Spearman(our metric,
   external metric) across all sources (the cross-check).

The anchors:

* **radon** — Maintainability Index (MI) + cyclomatic complexity (CC).
* **lizard** — cross-language cyclomatic complexity (its own implementation).
* **cognitive** — ``cognitive_complexity`` (SonarSource-style cognitive complexity
  over an ``ast`` FunctionDef).
* **readability** — the *published human-validated code-readability models*
  (Buse--Weimer / Scalabrino / Dorn). These are research artifacts (Weka/Java
  tooling), **not** pip-installable, so this anchor records an honest ``SKIP`` with a
  precise reason. We do **not** relabel a generic proxy (or the unrelated PyPI
  ``readability`` prose/Flesch package) as one of those models.

Each anchor is guarded so a missing tool yields ``{"status":"SKIP","reason":...}``
for that anchor only; the script still exits 0 (a missing tool must never fail the
build). It also emits a per-source table (``rows``) so ``--aggregate`` can later
correlate **LLM judge** ratings against the anchor metrics (judge-vs-anchor).

For backward compatibility the top-level ``status``/``tool``/``radon_version`` and the
original radon ``correlations`` keys are preserved (``run_bench.py`` reads them); the
generalized per-tool view lives under ``anchors``.
"""

from __future__ import annotations

import ast
import json
import os
import sys
import textwrap
from typing import Callable, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bench import dataset as D  # noqa: E402
from bench import grade as G  # noqa: E402
from eval import metrics as M  # noqa: E402

ANCHOR_PATH = os.path.join(_HERE, "out", "anchor.json")
# Derive from the dataset's incidental order so new profiles (light/heavy) are
# picked up automatically (clean=0 .. max=5). Do NOT hardcode the level count.
KNOB_RANK = {k: i for i, k in enumerate(D.KNOB_RANK)}


# --------------------------------------------------------------------------- #
# wrapping: radon CC / lizard / cognitive all analyse *functions*; our generated
# Python is a flat script, so wrap it in a function. The tools are static, so
# undefined free names are harmless.
# --------------------------------------------------------------------------- #
def _wrap_for_cc(src: str) -> str:
    return "def _spaghetti():\n" + textwrap.indent(src if src.strip() else "pass", "    ")


# --------------------------------------------------------------------------- #
# Anchor probes. Each returns either a dict of metric callables (str -> float),
# or None when the tool is not importable. Higher CC = worse; higher MI = better;
# higher cognitive = worse. We tag each metric's expected knob direction so the
# headline Spearman can be interpreted without hardcoding tool names downstream.
# --------------------------------------------------------------------------- #
# direction: "+" => metric increases with spaghetti (CC, cognitive),
#            "-" => metric decreases with spaghetti (maintainability index).
def _probe_radon():
    try:
        import radon  # noqa: F401
        from radon.complexity import cc_visit
        from radon.metrics import mi_visit
    except Exception:  # noqa: BLE001
        return None
    def cc(src: str) -> float:
        blocks = cc_visit(_wrap_for_cc(src))
        return float(max((b.complexity for b in blocks), default=1))
    def mi(src: str) -> float:
        return float(mi_visit(src, multi=False))
    return {
        "version": getattr(radon, "__version__", "unknown"),
        "metrics": {
            "cc": {"fn": cc, "direction": "+"},
            "mi": {"fn": mi, "direction": "-"},
        },
        # which metric is the "headline" knob witness for the compact summary
        "headline": "cc",
    }


def _probe_lizard():
    try:
        import lizard  # noqa: F401
    except Exception:  # noqa: BLE001
        return None
    def cc(src: str) -> float:
        res = lizard.analyze_file.analyze_source_code("_spaghetti.py", _wrap_for_cc(src))
        return float(max((f.cyclomatic_complexity for f in res.function_list), default=1))
    return {
        "version": getattr(lizard, "version", "unknown"),
        "metrics": {"cc": {"fn": cc, "direction": "+"}},
        "headline": "cc",
    }


def _probe_cognitive():
    try:
        from cognitive_complexity.api import get_cognitive_complexity
    except Exception:  # noqa: BLE001
        return None
    try:
        import cognitive_complexity as _cc_mod
        version = getattr(_cc_mod, "__version__", "unknown")
    except Exception:  # noqa: BLE001
        version = "unknown"
    def cognitive(src: str) -> float:
        tree = ast.parse(_wrap_for_cc(src))
        funcs = [n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        return float(max((get_cognitive_complexity(f) for f in funcs), default=0))
    return {
        "version": version,
        "metrics": {"cognitive": {"fn": cognitive, "direction": "+"}},
        "headline": "cognitive",
    }


def _probe_readability():
    """Published human-validated *code*-readability models (Buse--Weimer 2008,
    Scalabrino 2018, Dorn 2012). These ship as Weka/Java research artifacts and are
    not pip-installable; the unrelated PyPI ``readability`` package is a *prose*
    (Flesch/Kincaid) library, not a code model. We therefore SKIP honestly rather
    than relabel a proxy. (If a faithful Python port of one of these models is ever
    vendored into the venv, wire it here and name it precisely.)"""
    return None


# Registry: order is the canonical anchor order in the output.
_PROBES: Dict[str, Callable[[], Optional[dict]]] = {
    "radon": _probe_radon,
    "lizard": _probe_lizard,
    "cognitive": _probe_cognitive,
    "readability": _probe_readability,
}

# Honest SKIP reasons per anchor (used when the probe returns None).
_SKIP_REASON = {
    "radon": "radon not importable; install it in the quarantined venv "
             "(pip install radon). It is kept off the zero-dependency core.",
    "lizard": "lizard not importable; install it in the quarantined venv "
              "(pip install lizard).",
    "cognitive": "cognitive_complexity not importable; install it in the "
                 "quarantined venv (pip install cognitive_complexity).",
    "readability": "no published human-validated code-readability model is "
                   "importable. Buse-Weimer / Scalabrino / Dorn are Weka/Java "
                   "research artifacts, not pip-installable; the PyPI 'readability' "
                   "package is a prose (Flesch) library, not a code model. We refuse "
                   "to relabel a proxy as one of these models, so this anchor is a "
                   "deliberate, honest SKIP (the construct-validity triad still rests "
                   "on radon/lizard/cognitive convergence + the by-construction knob).",
}


# --------------------------------------------------------------------------- #
# correlation helpers (shared with grade.py)
# --------------------------------------------------------------------------- #
def _incidental_spearman_mean(rows: List[dict], col: str) -> Optional[float]:
    """Mean over base samples of Spearman(knob rank, metric column)."""
    by_sample: Dict[str, List[dict]] = {}
    for r in rows:
        by_sample.setdefault(r["sample"], []).append(r)
    rhos: List[float] = []
    for rs in by_sample.values():
        rs = sorted(rs, key=lambda r: r["rank"])
        if len(rs) < 2:
            continue
        rhos.append(G.spearman([r["rank"] for r in rs], [r[col] for r in rs]))
    return G.mean(rhos) if rhos else None


def _config_resolver_spearman(rows: List[dict], col: str, knob: str = "max") -> Optional[float]:
    """Spearman(config_resolver N, metric) at a fixed profile."""
    cfg = sorted([r for r in rows
                  if r["family"] == "config_resolver" and r["knob"] == knob
                  and r["scale"] is not None],
                 key=lambda r: r["scale"])
    if len(cfg) < 2:
        return None
    return G.spearman([r["scale"] for r in cfg], [r[col] for r in cfg])


def _crosscheck_spearman(rows: List[dict], ext_col: str, our_col: str) -> Optional[float]:
    """Spearman(our-lane metric, external metric) across all sources."""
    xs = [r[our_col] for r in rows if r.get(ext_col) is not None]
    ys = [r[ext_col] for r in rows if r.get(ext_col) is not None]
    if len(xs) < 2:
        return None
    return G.spearman(xs, ys)


# Map an external metric name -> the our-lane column it is anchored against.
_OUR_LANE = {"cc": "our_cc", "mi": "our_mi", "cognitive": "our_cognitive"}


# --------------------------------------------------------------------------- #
# main compute
# --------------------------------------------------------------------------- #
def compute() -> dict:
    # 1) probe every anchor; build the set of available metric callables.
    probed: Dict[str, Optional[dict]] = {name: probe() for name, probe in _PROBES.items()}
    # external_metric_cols: col_name -> (anchor, metric, spec) for available tools.
    metric_cols: Dict[str, dict] = {}
    for anchor, info in probed.items():
        if not info:
            continue
        for metric, spec in info["metrics"].items():
            col = f"{anchor}_{metric}"
            metric_cols[col] = {"anchor": anchor, "metric": metric,
                                "fn": spec["fn"], "direction": spec["direction"]}

    # 2) score every (base sample, knob level) source with our lane + every anchor.
    sp = D.mint("dev")
    rows: List[dict] = []
    for it in sp.items:
        if it.is_variant:
            continue
        prog = sp.program(it.stem)
        levels = {"clean": M.clean_baseline_static(prog)}
        for p in D.PROFILES:
            levels[p] = sp.sources(it.stem, p)["python"]
        for knob, src in levels.items():
            row = {
                "sample": it.stem, "family": it.family, "knob": knob,
                "rank": KNOB_RANK[knob], "scale": it.scale,
                # our-lane metrics (stdlib eval.metrics) — the anchors cross-check these.
                "our_cc": float(M.cyclomatic(src)),
                "our_mi": float(M.maintainability_index(src)),
                "our_cognitive": float(M.cognitive(src)),
            }
            for col, spec in metric_cols.items():
                row[col] = spec["fn"](src)
            rows.append(row)

    # 3) per-anchor correlations.
    anchors: Dict[str, dict] = {}
    for anchor in _PROBES:
        info = probed[anchor]
        if not info:
            anchors[anchor] = {"status": "SKIP", "tool": anchor,
                               "reason": _SKIP_REASON[anchor]}
            continue
        per_metric: Dict[str, dict] = {}
        for metric, spec in info["metrics"].items():
            col = f"{anchor}_{metric}"
            our_col = _OUR_LANE.get(metric)
            per_metric[metric] = {
                "direction": spec["direction"],  # expected knob sign (+ worse, - better)
                "incidental_knob_spearman_mean": _incidental_spearman_mean(rows, col),
                "config_resolver_N_spearman": _config_resolver_spearman(rows, col),
                "crosscheck_vs_our_lane_spearman":
                    (_crosscheck_spearman(rows, col, our_col) if our_col else None),
            }
        anchors[anchor] = {
            "status": "OK", "tool": anchor, "version": info["version"],
            "headline_metric": info["headline"],
            "metrics": per_metric,
        }

    # 4) per-sample radon view (kept for backward compat / judge-vs-anchor) +
    #    legacy top-level radon correlations that run_bench.py reads.
    per_sample: Dict[str, dict] = {}
    radon_ok = probed["radon"] is not None
    if radon_ok:
        by_sample: Dict[str, List[dict]] = {}
        for r in rows:
            by_sample.setdefault(r["sample"], []).append(r)
        for s, rs in by_sample.items():
            rs = sorted(rs, key=lambda r: r["rank"])
            ranks = [r["rank"] for r in rs]
            per_sample[s] = {
                "cc_vs_knob_spearman": G.spearman(ranks, [r["radon_cc"] for r in rs]),
                "mi_vs_knob_spearman": G.spearman(ranks, [r["radon_mi"] for r in rs]),
            }

    n_anchors_ok = sum(1 for a in anchors.values() if a["status"] == "OK")
    rec: dict = {
        # generalized status: OK if at least one external anchor is available.
        "status": "OK" if n_anchors_ok else "SKIP",
        "n_sources": len(rows),
        "n_base_samples": len({r["sample"] for r in rows}),
        "knob_rank": D.KNOB_RANK,
        "anchors": anchors,
        "anchors_ok": sorted(a for a, v in anchors.items() if v["status"] == "OK"),
        "anchors_skipped": sorted(a for a, v in anchors.items() if v["status"] == "SKIP"),
        "per_sample": per_sample,
        "rows": rows,  # for judge-vs-anchor correlation in --aggregate
    }

    # Backward-compatible top-level keys for run_bench.py (radon as the primary tool).
    if radon_ok:
        rad = anchors["radon"]["metrics"]
        rec["tool"] = "radon"
        rec["radon_version"] = anchors["radon"]["version"]
        rec["correlations"] = {
            "incidental_cc_spearman_mean": rad["cc"]["incidental_knob_spearman_mean"],
            "incidental_mi_spearman_mean": rad["mi"]["incidental_knob_spearman_mean"],
            "config_resolver_N_cc_spearman": rad["cc"]["config_resolver_N_spearman"],
            "crosscheck_our_cc_vs_radon_cc_spearman": rad["cc"]["crosscheck_vs_our_lane_spearman"],
            "crosscheck_our_mi_vs_radon_mi_spearman": rad["mi"]["crosscheck_vs_our_lane_spearman"],
        }
    else:
        # radon absent but maybe other anchors present: keep the legacy keys honest.
        rec["tool"] = "radon"
        rec["radon_version"] = None
        rec["correlations"] = {}

    return rec


def _fmt(v: Optional[float]) -> str:
    return f"{v:+.3f}" if isinstance(v, (int, float)) else str(v)


def main() -> int:
    rec = compute()
    os.makedirs(os.path.dirname(ANCHOR_PATH), exist_ok=True)
    with open(ANCHOR_PATH, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=2)
        f.write("\n")

    ok = rec["anchors_ok"]
    skip = rec["anchors_skipped"]
    print(f"anchor {rec['status']} ({rec['n_sources']} sources, "
          f"{rec['n_base_samples']} base samples) -> {ANCHOR_PATH}")
    print(f"  available: {ok or '(none)'}   skipped: {skip or '(none)'}")
    for anchor in ok:
        a = rec["anchors"][anchor]
        hm = a["headline_metric"]
        spec = a["metrics"][hm]
        sign = spec["direction"]
        print(f"  {anchor} ({a['version']}): {hm} vs knob "
              f"rho={_fmt(spec['incidental_knob_spearman_mean'])} (expect {sign}1); "
              f"crosscheck rho={_fmt(spec['crosscheck_vs_our_lane_spearman'])}")
    for anchor in skip:
        print(f"  {anchor}: SKIP")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
