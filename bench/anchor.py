#!/usr/bin/env python3
"""Phase 6 — construct-validity anchor (non-LLM, **quarantined**).

This is the only place an *external* third-party tool is used, and it is kept off
the zero-dependency core: run it with a throwaway virtualenv that has ``radon``
installed, e.g.

    python3 -m venv /tmp/anchor && /tmp/anchor/bin/pip install radon
    /tmp/anchor/bin/python bench/anchor.py

It scores the **public dev** Python sources with radon's independent
Maintainability Index (MI) and cyclomatic complexity (CC), then asks two questions
that turn the bespoke metrics from "trust us" into "anchored to an external
reference":

1. **Does the ground-truth knob move an independent metric monotonically?**
   Spearman(profile rank, radon CC) should be strongly positive and
   Spearman(profile rank, radon MI) strongly negative — over ``clean`` →
   ``minimal`` → ``standard`` → ``max`` at fixed semantics.
2. **Is our own metric lane anchored to the external one?** Spearman(our
   cyclomatic, radon CC) and Spearman(our MI, radon MI) across all sources.

It also emits a per-source table so ``--aggregate`` can later correlate **LLM
judge** ratings against radon MI (judge-vs-anchor, Phase 7 Figure 4). If ``radon``
is not importable, it writes an honest ``SKIP`` record (never a fake number).
"""

from __future__ import annotations

import json
import os
import sys
import textwrap

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bench import dataset as D  # noqa: E402
from bench import grade as G  # noqa: E402
from eval import metrics as M  # noqa: E402

ANCHOR_PATH = os.path.join(_HERE, "out", "anchor.json")
KNOB_RANK = {"clean": 0, "minimal": 1, "standard": 2, "max": 3}


def _radon():
    try:
        import radon  # noqa: F401
        from radon.complexity import cc_visit
        from radon.metrics import mi_visit
        return radon, cc_visit, mi_visit
    except Exception:  # noqa: BLE001
        return None, None, None


def _wrap_for_cc(src: str) -> str:
    """radon CC analyses functions; our generated Python is a flat script, so wrap
    it in a function (radon is static, so undefined free names are harmless)."""
    return "def _spaghetti():\n" + textwrap.indent(src if src.strip() else "pass", "    ")


def _radon_cc(cc_visit, src: str) -> float:
    blocks = cc_visit(_wrap_for_cc(src))
    return float(max((b.complexity for b in blocks), default=1))


def _radon_mi(mi_visit, src: str) -> float:
    return float(mi_visit(src, multi=False))


def compute() -> dict:
    radon, cc_visit, mi_visit = _radon()
    if radon is None:
        return {"status": "SKIP", "tool": "radon",
                "reason": "radon not importable; run bench/anchor.py with a venv "
                          "that has radon installed (it is quarantined off the core)",
                "fallback": "the bespoke eval.metrics lane (cyclomatic/MI) still "
                            "applies; this anchor adds an external cross-check only"}

    sp = D.mint("dev")
    rows = []  # one per (base sample, knob level)
    for it in sp.items:
        if it.is_variant:
            continue
        prog = sp.program(it.stem)
        levels = {"clean": M.clean_baseline_static(prog)}
        for p in D.PROFILES:
            levels[p] = sp.sources(it.stem, p)["python"]
        for knob, src in levels.items():
            rows.append({
                "sample": it.stem, "family": it.family, "knob": knob,
                "rank": KNOB_RANK[knob], "scale": it.scale,
                "radon_cc": _radon_cc(cc_visit, src),
                "radon_mi": _radon_mi(mi_visit, src),
                "our_cc": float(M.cyclomatic(src)),
                "our_mi": float(M.maintainability_index(src)),
            })

    # (1) incidental monotonicity: per sample, Spearman(rank, radon_*)
    by_sample = {}
    for r in rows:
        by_sample.setdefault(r["sample"], []).append(r)
    inc_cc, inc_mi = [], []
    per_sample = {}
    for s, rs in by_sample.items():
        rs = sorted(rs, key=lambda r: r["rank"])
        ranks = [r["rank"] for r in rs]
        cc_rho = G.spearman(ranks, [r["radon_cc"] for r in rs])
        mi_rho = G.spearman(ranks, [r["radon_mi"] for r in rs])
        inc_cc.append(cc_rho)
        inc_mi.append(mi_rho)
        per_sample[s] = {"cc_vs_knob_spearman": cc_rho, "mi_vs_knob_spearman": mi_rho}

    # (2) intrinsic: config_resolver N -> radon CC at max
    cfg_rows = sorted([r for r in rows
                       if r["family"] == "config_resolver" and r["knob"] == "max"],
                      key=lambda r: r["scale"])
    cfg_intrinsic = (G.spearman([r["scale"] for r in cfg_rows],
                                 [r["radon_cc"] for r in cfg_rows])
                     if len(cfg_rows) >= 2 else None)

    # cross-check: our lane vs the external lane across all sources
    cc_x = G.spearman([r["our_cc"] for r in rows], [r["radon_cc"] for r in rows])
    mi_x = G.spearman([r["our_mi"] for r in rows], [r["radon_mi"] for r in rows])

    return {
        "status": "OK", "tool": "radon", "radon_version": radon.__version__,
        "n_sources": len(rows),
        "correlations": {
            "incidental_cc_spearman_mean": G.mean(inc_cc),
            "incidental_mi_spearman_mean": G.mean(inc_mi),
            "config_resolver_N_cc_spearman": cfg_intrinsic,
            "crosscheck_our_cc_vs_radon_cc_spearman": cc_x,
            "crosscheck_our_mi_vs_radon_mi_spearman": mi_x,
        },
        "per_sample": per_sample,
        "rows": rows,   # for judge-vs-anchor correlation in --aggregate
    }


def main() -> int:
    rec = compute()
    os.makedirs(os.path.dirname(ANCHOR_PATH), exist_ok=True)
    with open(ANCHOR_PATH, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=2)
        f.write("\n")
    if rec["status"] == "SKIP":
        print(f"anchor SKIP ({rec['reason']}) -> {ANCHOR_PATH}")
        return 0
    c = rec["correlations"]
    print(f"anchor OK (radon {rec['radon_version']}, {rec['n_sources']} sources) -> {ANCHOR_PATH}")
    print(f"  incidental: CC vs knob rho={c['incidental_cc_spearman_mean']:+.3f} "
          f"(expect +), MI vs knob rho={c['incidental_mi_spearman_mean']:+.3f} (expect -)")
    print(f"  intrinsic:  config_resolver N vs radon CC rho={c['config_resolver_N_cc_spearman']}")
    print(f"  crosscheck: our CC vs radon CC rho={c['crosscheck_our_cc_vs_radon_cc_spearman']:+.3f}; "
          f"our MI vs radon MI rho={c['crosscheck_our_mi_vs_radon_mi_spearman']:+.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
