"""Compression-based static-validity anchor (stdlib zlib + ast only). Zero API.

A mechanistically distinct anchor family for the incidental "messiness" knob:
instead of complexity metrics (radon/lizard/cognitive share the decision-count
construct), this script asks whether plain information-theoretic measures move
with the knob on the FROZEN dev renders (bench/data/dev/<stem>.json sources;
base samples only, variants skipped):

* ``zlib_redundancy`` = 1 - len(zlib.compress(src, 9)) / len(src). Orientation
  note (investigated, not assumed): the naive expectation "messier -> less
  compressible per byte" comes out INVERTED on this generator, because the knob
  injects textual redundancy (duplicated scaffolding, repeated boilerplate,
  marker comments), and longer files also amortize the zlib header; the raw
  compressed/original ratio therefore FALLS monotonically with the knob. The
  correctly oriented anchor is redundancy (1 - ratio), direction "+": more
  mess -> more compressible -> higher redundancy. The raw ratio means per knob
  level are published in ``level_means`` so the inversion is visible.
* ``ncd`` = (C(xy) - min(Cx, Cy)) / max(Cx, Cy) against the sample's clean
  reference text (eval.metrics.clean_baseline_static), direction "+": more
  mess -> further from clean. The reference is the PYTHON clean baseline for
  every language; that python-reference/language mismatch is a fixed constant
  within each (sample, language) knob series, so the within-series Spearman is
  unaffected by it (stated again in the artifact).
* python only: AST max depth via a recursive walk of ``ast.parse`` (guarded),
  reported next to ``eval.metrics.max_nesting``.

Both compression metrics are also computed on comment-stripped text
(``eval.metrics._code_part``) as an annotation-sensitivity check: the knob
correlation must not be an artifact of the SPAGH_* marker comments.

Knob levels: clean -> minimal -> light -> standard -> heavy -> max for Python;
the ``clean`` idiomatic floor exists only for Python (mirroring bench/anchor.py),
so the other four languages use the five engine profiles (rank alignment is
per-(sample, language), which Spearman is invariant to).

Aggregation per language: per-base-sample Spearman(knob rank, metric) via
bench.grade.spearman, mean over the 50 base samples, bench.grade.ci95_bootstrap.

Inputs: bench/data/dev/*.json (frozen renders + IR). No model, no Engine.
Output: bench/out/compression_anchor.json.

Kill-switches (a failure means THIS SCRIPT is wrong; fix it, never the data):
* exactly 50 base samples, each with the 5 engine profiles x 5 languages;
* every python cell parses and AST max depth >= eval.metrics.max_nesting;
* raw-variant mean Spearman vs knob is strictly positive in EVERY language for
  both zlib_redundancy and ncd (orientation investigated above).
"""

from __future__ import annotations

import ast
import json
import os
import sys
import zlib
from typing import Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bench.suite_common import (  # noqa: E402
    artifact_header,
    mean,
    write_artifact,
)
from bench import dataset as D  # noqa: E402  (constants only: PROFILES/KNOB_RANK/LANGS)
from bench import grade as G  # noqa: E402
from eval import metrics as M  # noqa: E402
from src.nodes.parser import parse  # noqa: E402

DEV_DIR = os.path.join(_HERE, "data", "dev")
OUT = os.path.join(_HERE, "out", "compression_anchor.json")

RANK = {k: i for i, k in enumerate(D.KNOB_RANK)}  # clean=0 .. max=5
N_BASE_EXPECTED = 50

# All four metric variants are oriented "+": higher with more mess.
METRICS = ("zlib_redundancy_raw", "zlib_redundancy_stripped",
           "ncd_raw", "ncd_stripped")
RAW_KILL_METRICS = ("zlib_redundancy_raw", "ncd_raw")


# --------------------------------------------------------------------------- #
# cell-level measures
# --------------------------------------------------------------------------- #
def _clen(text: str) -> int:
    return len(zlib.compress(text.encode("utf-8"), 9))


def _zlib_ratio(text: str) -> float:
    b = text.encode("utf-8")
    if not b:
        sys.exit("empty source cell: cannot form a compression ratio")
    return _clen(text) / len(b)


def _ncd(x: str, y: str) -> float:
    cx, cy, cxy = _clen(x), _clen(y), _clen(x + y)
    return (cxy - min(cx, cy)) / max(cx, cy)


def _strip_comments(src: str, lang: str) -> str:
    """Line-comment-stripped text via the string-aware eval.metrics._code_part."""
    prefix = M._LINE_COMMENT[lang]
    return "\n".join(M._code_part(ln, prefix) for ln in src.splitlines()) + "\n"


def _ast_max_depth(py_src: str) -> Optional[int]:
    """Max node depth of the parse tree (Module at depth 0), or None on failure.

    Recursive walk with an iterative fallback so a pathologically deep tree
    cannot crash the script (the fallback computes the identical number).
    """
    try:
        tree = ast.parse(py_src)
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return None

    def rec(node: ast.AST) -> int:
        return 1 + max((rec(c) for c in ast.iter_child_nodes(node)), default=0)

    try:
        return rec(tree) - 1
    except RecursionError:
        best, stack = 0, [(tree, 0)]
        while stack:
            node, d = stack.pop()
            best = max(best, d)
            for c in ast.iter_child_nodes(node):
                stack.append((c, d + 1))
        return best


# --------------------------------------------------------------------------- #
# aggregation helpers
# --------------------------------------------------------------------------- #
def _rho_stats(rhos: List[float]) -> dict:
    """Mean + bootstrap 95% CI + n of a per-sample Spearman distribution."""
    return {
        "mean": mean(rhos),                                # raises on empty: n-guard
        "ci95": G.ci95_bootstrap(rhos) if len(rhos) > 1 else None,
        "n_base_samples": len(rhos),
    }


def _load_base_records() -> Dict[str, dict]:
    recs: Dict[str, dict] = {}
    for name in sorted(os.listdir(DEV_DIR)):
        if not name.endswith(".json"):
            continue
        rec = json.load(open(os.path.join(DEV_DIR, name)))
        if rec.get("variant") != "base":
            continue
        if list(rec["sources"].keys()) != D.PROFILES:
            sys.exit(f"{name}: profiles {list(rec['sources'])} != {D.PROFILES}")
        for p in D.PROFILES:
            if list(rec["sources"][p].keys()) != D.LANGS:
                sys.exit(f"{name}: languages under {p} != {D.LANGS}")
        recs[rec["stem"]] = rec
    if len(recs) != N_BASE_EXPECTED:
        sys.exit(f"expected {N_BASE_EXPECTED} base samples, found {len(recs)}")
    return recs


# --------------------------------------------------------------------------- #
# main compute
# --------------------------------------------------------------------------- #
def main() -> int:
    recs = _load_base_records()

    rhos: Dict[str, Dict[str, List[float]]] = {
        L: {m: [] for m in METRICS} for L in D.LANGS}
    level_cells: Dict[str, Dict[str, Dict[str, List[float]]]] = {
        L: {} for L in D.LANGS}
    ann_delta: Dict[str, List[float]] = {L: [] for L in D.LANGS}
    depth_rhos: List[float] = []
    nesting_rhos: List[float] = []
    py_depth_margin_min: Optional[int] = None
    n_py_cells = 0

    for stem in sorted(recs):
        rec = recs[stem]
        clean_src = M.clean_baseline_static(parse(rec["ir"]))
        ref_raw = clean_src
        ref_stripped = _strip_comments(clean_src, "python")

        for L in D.LANGS:
            knobs = (["clean"] if L == "python" else []) + list(D.PROFILES)
            ranks: List[int] = []
            vals: Dict[str, List[float]] = {m: [] for m in METRICS}
            py_depths: List[float] = []
            py_nestings: List[float] = []
            for knob in knobs:
                src = clean_src if knob == "clean" else rec["sources"][knob][L]
                stripped = _strip_comments(src, L)
                r_raw = _zlib_ratio(src)
                r_str = _zlib_ratio(stripped)
                cell = {
                    "zlib_redundancy_raw": 1.0 - r_raw,
                    "zlib_redundancy_stripped": 1.0 - r_str,
                    "ncd_raw": _ncd(src, ref_raw),
                    "ncd_stripped": _ncd(stripped, ref_stripped),
                }
                ranks.append(RANK[knob])
                for m in METRICS:
                    vals[m].append(cell[m])
                lv = level_cells[L].setdefault(knob, {
                    "zlib_ratio_raw": [], "zlib_ratio_stripped": [], "ncd_raw": []})
                lv["zlib_ratio_raw"].append(r_raw)
                lv["zlib_ratio_stripped"].append(r_str)
                lv["ncd_raw"].append(cell["ncd_raw"])
                ann_delta[L].append(cell["zlib_redundancy_stripped"]
                                    - cell["zlib_redundancy_raw"])

                if L == "python":
                    depth = _ast_max_depth(src)
                    if depth is None:
                        sys.exit(f"{stem}/{knob}: frozen python cell failed ast.parse "
                                 "-- the script (or its inputs) is wrong")
                    try:
                        nesting = M.max_nesting(src)
                    except SyntaxError:
                        sys.exit(f"{stem}/{knob}: max_nesting failed to parse "
                                 "a frozen python cell")
                    if depth < nesting:
                        sys.exit(f"KILL: AST depth {depth} < max_nesting {nesting} "
                                 f"at {stem}/{knob}; the depth walk is wrong")
                    margin = depth - nesting
                    py_depth_margin_min = (margin if py_depth_margin_min is None
                                           else min(py_depth_margin_min, margin))
                    n_py_cells += 1
                    py_depths.append(float(depth))
                    py_nestings.append(float(nesting))

            n_expected = len(D.PROFILES) + (1 if L == "python" else 0)
            if len(ranks) != n_expected:
                sys.exit(f"{stem}/{L}: {len(ranks)} knob levels, expected {n_expected}")
            for m in METRICS:
                rhos[L][m].append(G.spearman([float(r) for r in ranks], vals[m]))
            if L == "python":
                depth_rhos.append(G.spearman([float(r) for r in ranks], py_depths))
                nesting_rhos.append(G.spearman([float(r) for r in ranks], py_nestings))

    # ---------------- per-language aggregates + the sign kill-switch ---------
    per_language: Dict[str, dict] = {}
    for L in D.LANGS:
        entry: Dict[str, dict] = {}
        for m in METRICS:
            st = _rho_stats(rhos[L][m])
            st["direction"] = "+"
            st["n_levels"] = len(D.PROFILES) + (1 if L == "python" else 0)
            entry[m] = st
        per_language[L] = entry
        for m in RAW_KILL_METRICS:
            if not entry[m]["mean"] > 0:
                sys.exit(f"KILL: mean Spearman({m}, knob) = {entry[m]['mean']} "
                         f"<= 0 for {L}; re-investigate the metric orientation "
                         "-- do not publish this artifact")

    if n_py_cells != N_BASE_EXPECTED * len(D.KNOB_RANK):
        sys.exit(f"expected {N_BASE_EXPECTED * len(D.KNOB_RANK)} python cells, "
                 f"saw {n_py_cells}")

    level_means = {
        L: {knob: {**{f"{k}_mean": mean(v) for k, v in lv.items()},
                   "n": len(lv["zlib_ratio_raw"])}
            for knob, lv in level_cells[L].items()}
        for L in D.LANGS}

    annotation_sensitivity = {
        L: {
            "zlib_redundancy": {
                "raw_mean_rho": per_language[L]["zlib_redundancy_raw"]["mean"],
                "stripped_mean_rho":
                    per_language[L]["zlib_redundancy_stripped"]["mean"],
            },
            "ncd": {
                "raw_mean_rho": per_language[L]["ncd_raw"]["mean"],
                "stripped_mean_rho": per_language[L]["ncd_stripped"]["mean"],
            },
            "redundancy_level_shift_stripped_minus_raw_mean": mean(ann_delta[L]),
            "n_cells": len(ann_delta[L]),
        }
        for L in D.LANGS}

    report = {
        "_meta": artifact_header(
            "compression_anchor.py",
            ["bench/data/dev/*.json"],
            languages=list(D.LANGS),
            knob_rank=list(D.KNOB_RANK),
            n_base_samples=N_BASE_EXPECTED,
            tools="stdlib zlib (level 9) + stdlib ast only; no external anchors",
        ),
        "definitions": {
            "zlib_redundancy": "1 - len(zlib.compress(src,9))/len(src); "
                               "direction '+' (more mess -> more redundancy).",
            "ncd": "(C(xy)-min(Cx,Cy))/max(Cx,Cy), C = zlib level-9 compressed "
                   "length, y = the sample's PYTHON clean reference "
                   "(eval.metrics.clean_baseline_static); direction '+' (more "
                   "mess -> further from clean).",
            "stripped_variant": "same measures on line-comment-stripped text "
                                "(eval.metrics._code_part), the annotation-"
                                "sensitivity check: the knob correlation must "
                                "survive removing SPAGH_* marker comments.",
            "aggregation": "per-base-sample Spearman(knob rank, metric) via "
                           "bench.grade.spearman; mean over the 50 base samples "
                           "with bench.grade.ci95_bootstrap.",
        },
        "orientation_note": (
            "Investigated, not assumed: the naive 'messier -> less compressible "
            "per byte' expectation is INVERTED on this generator. The knob "
            "injects textual redundancy (duplicated scaffolding, repeated "
            "boilerplate, marker comments) and longer files amortize the zlib "
            "header, so the raw compressed/original ratio FALLS monotonically "
            "with the knob (see level_means). The anchor is therefore oriented "
            "as redundancy = 1 - ratio, direction '+'. NCD needs no flip: "
            "distance from the clean reference rises with the knob."),
        "reference_note": (
            "The NCD reference is the sample's PYTHON clean baseline for every "
            "language; the python-reference/language mismatch is a fixed "
            "constant within each (sample, language) knob series, so the "
            "within-series Spearman is unaffected by it. The clean floor "
            "itself is a knob level only for python (rank 0), mirroring "
            "bench/anchor.py; the other languages rank the five engine "
            "profiles."),
        "per_language": per_language,
        "annotation_sensitivity": annotation_sensitivity,
        "level_means": level_means,
        "python_ast_depth": {
            "definition": "max parse-tree node depth (Module at depth 0) via a "
                          "guarded recursive walk of ast.parse, reported next "
                          "to eval.metrics.max_nesting (block-nesting depth).",
            "kill": "depth >= max_nesting on every python cell (AST depth "
                    "counts expression levels too, so it must dominate)",
            "n_cells": n_py_cells,
            "min_depth_minus_nesting": py_depth_margin_min,
            "depth_vs_knob": _rho_stats(depth_rhos),
            "max_nesting_vs_knob": _rho_stats(nesting_rhos),
        },
        "kill_switches_passed": [
            f"{N_BASE_EXPECTED} base samples x {len(D.PROFILES)} profiles x "
            f"{len(D.LANGS)} languages present in the frozen dev split",
            f"AST depth >= max_nesting on all {n_py_cells} python cells "
            f"(min margin {py_depth_margin_min})",
            "raw-variant mean Spearman vs knob > 0 in every language for "
            "zlib_redundancy and ncd",
        ],
    }

    for L in D.LANGS:
        z = per_language[L]["zlib_redundancy_raw"]
        n = per_language[L]["ncd_raw"]
        print(f"{L:11s} zlib_redundancy rho={z['mean']:+.3f} {z['ci95']}  "
              f"ncd rho={n['mean']:+.3f} {n['ci95']}  (n={z['n_base_samples']})")
    d = report["python_ast_depth"]
    print(f"python AST depth vs knob rho={d['depth_vs_knob']['mean']:+.3f}  "
          f"max_nesting rho={d['max_nesting_vs_knob']['mean']:+.3f}  "
          f"min(depth-nesting)={d['min_depth_minus_nesting']}")
    write_artifact(OUT, report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
