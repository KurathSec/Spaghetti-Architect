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
* **bw_readability** — a faithful *re-implementation of the published Buse--Weimer
  (2010) readability **feature set*** (Fig. 6 of Buse & Weimer, "Learning a Metric
  for Code Readability", IEEE TSE 36(4):546-558, 2010), computed with the Python
  stdlib (``tokenize``/``keyword``) over the dev Python sources. This is the only
  anchor that is *readability*-flavoured rather than complexity-flavoured, so it is a
  more independent witness than radon/lizard/cognitive (which are mutually correlated
  complexity metrics). **It is NOT the original trained Weka classifier** — those
  trained coefficients remain unavailable offline (hence the ``readability`` SKIP
  above stays). We therefore report only (a) the per-feature Spearman against the knob
  and (b) a *documented surface-density aggregate* (mean of z-scored density/structure
  features, oriented so higher = denser = less readable, per the paper's qualitative
  finding that line length and identifier density most reduce readability). A fresh
  human readability study calibrated to this benchmark is explicitly future work.

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
import io
import json
import keyword
import os
import statistics
import sys
import textwrap
import tokenize
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
# Buse--Weimer (2010) readability FEATURE SET (Fig. 6).
#
# Source: R. P. L. Buse and W. R. Weimer, "Learning a Metric for Code
# Readability", IEEE Transactions on Software Engineering 36(4):546-558, 2010
# (TSE special issue on ISSTA 2008 best papers); preprint Fig. 6 enumerates the
# 25-feature vector. Each feature is "either an average value per line, or a
# maximum value for all lines" and is *size-independent by construction*. The
# eight features marked X/X in Fig. 6 contribute BOTH an average-per-line and a
# maximum-per-line column; the rest are average-per-line, and the final two
# (most-frequent character / identifier) are maxima over the snippet.
#
# This is a faithful re-implementation of those FEATURE DEFINITIONS only. It is
# NOT the trained Weka classifier (those coefficients are not available offline);
# we therefore never emit a single "Buse--Weimer readability score" -- we report
# the raw features and a clearly-labelled surface-density aggregate.
# --------------------------------------------------------------------------- #
_PY_KEYWORDS = frozenset(keyword.kwlist)
_ARITH_OPS = frozenset(["+", "-", "*", "/", "//", "%", "**", "@"])
_COMPARE_OPS = frozenset(["==", "!=", "<", ">", "<=", ">=", "<>"])
_ASSIGN_OPS = frozenset(["=", "+=", "-=", "*=", "/=", "//=", "%=", "**=",
                         "&=", "|=", "^=", ">>=", "<<=", "@=", ":="])
_BRANCH_KW = frozenset(["if", "elif"])
_LOOP_KW = frozenset(["for", "while"])

# The 25 Buse--Weimer feature names, in Fig. 6 order. Used to keep the JSON
# feature dict ordered and to drive the aggregate.
_BW_FEATURE_NAMES = (
    "avg_line_length", "max_line_length",
    "avg_identifiers", "max_identifiers",
    "avg_identifier_length", "max_identifier_length",
    "avg_indentation", "max_indentation",
    "avg_keywords", "max_keywords",
    "avg_numbers", "max_numbers",
    "avg_comments", "avg_periods", "avg_commas", "avg_spaces",
    "avg_parentheses", "avg_arithmetic_ops", "avg_comparison_ops",
    "avg_assignments", "avg_branches", "avg_loops", "avg_blank_lines",
    "max_char_occurrences", "max_identifier_occurrences",
)

# Documented surface-density aggregate: the subset of Fig. 6 features that are
# monotone in textual *density / structure* (more of them => less readable, per
# the paper's PCA, which finds avg line length and avg #identifiers the strongest
# negative readability factors). We deliberately exclude comments, blank lines,
# spaces and identifier *length* (which Fig. 9 finds weak or readability-neutral),
# so the aggregate is an honest "surface density" proxy, NOT the trained model.
_BW_DENSITY_FEATURES = (
    "avg_line_length", "max_line_length", "avg_identifiers", "max_identifiers",
    "avg_keywords", "max_keywords", "avg_numbers", "max_numbers",
    "avg_periods", "avg_commas", "avg_parentheses", "avg_arithmetic_ops",
    "avg_comparison_ops", "avg_assignments", "avg_branches", "avg_loops",
    "avg_indentation", "max_indentation",
    "max_char_occurrences", "max_identifier_occurrences",
)


def _bw_tokens_by_line(src: str) -> Dict[int, List]:
    """Map physical 1-based line number -> [(toktype, tokstr), ...].

    Uses the stdlib ``tokenize`` for an accurate lexical view (keywords vs.
    identifiers, numbers, comments, operator spelling). Robust to un-tokenizable
    fragments (e.g. the bare clean baseline) -- on error we fall back to an empty
    token map and the line-oriented features (length, indentation, blanks, raw
    character frequency) still compute. Layout-only tokens are dropped.
    """
    out: Dict[int, List] = {}
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        return out
    _skip = {tokenize.ENCODING, tokenize.ENDMARKER, tokenize.NEWLINE,
             tokenize.NL, tokenize.INDENT, tokenize.DEDENT}
    for tok in toks:
        if tok.type in _skip:
            continue
        out.setdefault(tok.start[0], []).append((tok.type, tok.string))
    return out


def bw_readability_features(src: str) -> Dict[str, float]:
    """Compute the 25 Buse--Weimer (2010) Fig. 6 readability features for ``src``.

    Deterministic and dependency-free (Python stdlib only). Faithful to the
    published *feature definitions*; this is **not** the trained Weka classifier.
    """
    lines = src.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]  # ignore the trailing newline's empty cell
    n_lines = max(1, len(lines))
    by_line = _bw_tokens_by_line(src)

    line_len: List[int] = []
    indent: List[int] = []
    ident_per_line: List[int] = []
    ident_len_per_line: List[int] = []   # max identifier length seen on the line
    kw_per_line: List[int] = []
    num_per_line: List[int] = []
    comments = periods = commas = spaces = parens = 0
    arith = compare = assign = branch = loop = blank = 0
    char_freq: Dict[str, int] = {}
    ident_freq: Dict[str, int] = {}

    for i, line in enumerate(lines, start=1):
        line_len.append(len(line))
        stripped = line.lstrip()
        indent.append(len(line) - len(stripped))
        spaces += line.count(" ")
        if stripped == "":
            blank += 1
        for ch in line:
            char_freq[ch] = char_freq.get(ch, 0) + 1

        ids_here = 0
        id_lens_here = [0]
        kw_here = num_here = 0
        for ttype, tstr in by_line.get(i, ()):
            if ttype == tokenize.NAME:
                if tstr in _PY_KEYWORDS:
                    kw_here += 1
                    if tstr in _BRANCH_KW:
                        branch += 1
                    elif tstr in _LOOP_KW:
                        loop += 1
                else:
                    ids_here += 1
                    id_lens_here.append(len(tstr))
                    ident_freq[tstr] = ident_freq.get(tstr, 0) + 1
            elif ttype == tokenize.NUMBER:
                num_here += 1
            elif ttype == tokenize.COMMENT:
                comments += 1
            elif ttype == tokenize.OP:
                if tstr == ",":
                    commas += 1
                elif tstr == ".":
                    periods += 1
                elif tstr in ("(", ")"):
                    parens += 1
                if tstr in _ASSIGN_OPS:
                    assign += 1
                elif tstr in _COMPARE_OPS:
                    compare += 1
                elif tstr in _ARITH_OPS:
                    arith += 1
        ident_per_line.append(ids_here)
        ident_len_per_line.append(max(id_lens_here))
        kw_per_line.append(kw_here)
        num_per_line.append(num_here)

    def _avg(xs: List[int]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    def _max(xs: List[int]) -> float:
        return float(max(xs)) if xs else 0.0

    return {
        "avg_line_length": _avg(line_len),
        "max_line_length": _max(line_len),
        "avg_identifiers": _avg(ident_per_line),
        "max_identifiers": _max(ident_per_line),
        "avg_identifier_length": _avg(ident_len_per_line),
        "max_identifier_length": _max(ident_len_per_line),
        "avg_indentation": _avg(indent),
        "max_indentation": _max(indent),
        "avg_keywords": _avg(kw_per_line),
        "max_keywords": _max(kw_per_line),
        "avg_numbers": _avg(num_per_line),
        "max_numbers": _max(num_per_line),
        "avg_comments": comments / n_lines,
        "avg_periods": periods / n_lines,
        "avg_commas": commas / n_lines,
        "avg_spaces": spaces / n_lines,
        "avg_parentheses": parens / n_lines,
        "avg_arithmetic_ops": arith / n_lines,
        "avg_comparison_ops": compare / n_lines,
        "avg_assignments": assign / n_lines,
        "avg_branches": branch / n_lines,
        "avg_loops": loop / n_lines,
        "avg_blank_lines": blank / n_lines,
        "max_char_occurrences": float(max(char_freq.values(), default=0)),
        "max_identifier_occurrences": float(max(ident_freq.values(), default=0)),
    }


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
# Buse--Weimer readability anchor: bespoke compute (does not flow through the
# complexity _PROBES loop, so the radon/lizard/cognitive output is byte-identical
# with or without this anchor). Always available -- pure stdlib.
# --------------------------------------------------------------------------- #
def _bw_aggregate_proxy(rows: List[dict]) -> None:
    """Attach an in-place ``bw_proxy`` column: the mean of z-scored density/
    structure features (Fig. 6 subset), oriented so higher = denser = less
    readable. Standardisation is over the full dev source set so per-feature
    scale differences do not let one feature dominate. Deterministic; population
    stdev, with a zero-variance feature contributing 0 (guarded denominator)."""
    means = {c: statistics.fmean(r[c] for r in rows) for c in _BW_DENSITY_FEATURES}
    sds = {c: statistics.pstdev(r[c] for r in rows) for c in _BW_DENSITY_FEATURES}
    for r in rows:
        zs = [((r[c] - means[c]) / sds[c]) if sds[c] > 0 else 0.0
              for c in _BW_DENSITY_FEATURES]
        r["bw_proxy"] = sum(zs) / len(zs)


def _compute_bw_readability(rows: List[dict]) -> dict:
    """Build the ``bw_readability`` anchor record from per-source feature rows.

    ``rows`` must already carry each ``_BW_FEATURE_NAMES`` column plus the
    ``bw_proxy`` aggregate (see :func:`_bw_aggregate_proxy`) and the standard
    ``sample``/``family``/``knob``/``rank``/``scale`` metadata.
    """
    per_feature: Dict[str, dict] = {}
    for feat in _BW_FEATURE_NAMES:
        per_feature[feat] = {
            "incidental_knob_spearman_mean": _incidental_spearman_mean(rows, feat),
            "config_resolver_N_spearman": _config_resolver_spearman(rows, feat),
        }
    aggregate = {
        # direction "+" => higher (denser) is expected with more spaghetti.
        "direction": "+",
        "incidental_knob_spearman_mean": _incidental_spearman_mean(rows, "bw_proxy"),
        "config_resolver_N_spearman": _config_resolver_spearman(rows, "bw_proxy"),
        "definition": "mean of z-scored Buse-Weimer density/structure features "
                      "(line length, identifiers, keywords, numbers, periods, "
                      "commas, parentheses, arithmetic/comparison/assignment ops, "
                      "branches, loops, indentation, and most-frequent "
                      "char/identifier), standardised over the dev source set; "
                      "higher = denser surface = less readable.",
    }
    # The single strongest feature witness (by |incidental rho|), reported as the
    # headline so the compact summary does not have to pick a feature by hand.
    ranked = sorted(
        ((f, m["incidental_knob_spearman_mean"]) for f, m in per_feature.items()
         if m["incidental_knob_spearman_mean"] is not None),
        key=lambda kv: abs(kv[1]), reverse=True,
    )
    return {
        "status": "OK",
        "tool": "bw_readability",
        "kind": "feature-reimplementation",
        "source": "Buse & Weimer, 'Learning a Metric for Code Readability', "
                  "IEEE TSE 36(4):546-558, 2010 (Fig. 6 feature set).",
        "honest_note": "Faithful re-implementation of the published Buse-Weimer "
                       "readability FEATURES (stdlib tokenize/keyword), NOT the "
                       "original trained Weka classifier (whose coefficients are "
                       "not available offline; see the separate 'readability' SKIP "
                       "anchor). We report per-feature and surface-aggregate "
                       "correlations only. A fresh human readability study "
                       "calibrated to this benchmark is future work.",
        "n_features": len(_BW_FEATURE_NAMES),
        "headline_metric": "aggregate_density_proxy",
        "strongest_feature": (
            {"name": ranked[0][0], "incidental_knob_spearman_mean": ranked[0][1]}
            if ranked else None),
        "aggregate_density_proxy": aggregate,
        "features": per_feature,
    }


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

    # 2b) Buse--Weimer readability features over the SAME sources, kept in a
    #     parallel row set so the serialized ``rows`` (and thus every existing
    #     complexity number / judge-vs-anchor consumer) is byte-identical whether
    #     or not this anchor runs. Pure stdlib, so it is always available.
    bw_rows: List[dict] = []
    for r in rows:
        # re-derive the exact source for this (sample, knob) cell deterministically.
        prog = sp.program(r["sample"])
        src = (M.clean_baseline_static(prog) if r["knob"] == "clean"
               else sp.sources(r["sample"], r["knob"])["python"])
        bw = bw_readability_features(src)
        bw.update({"sample": r["sample"], "family": r["family"], "knob": r["knob"],
                   "rank": r["rank"], "scale": r["scale"]})
        bw_rows.append(bw)
    _bw_aggregate_proxy(bw_rows)

    # 3) per-anchor correlations.
    anchors: Dict[str, dict] = {}
    for anchor in _PROBES:
        info = probed[anchor]
        if not info:
            # NB: the 'readability' probe is a permanent honest SKIP for the
            # *trained* Weka models (Buse-Weimer/Scalabrino/Dorn); the runnable
            # feature re-implementation is published below as 'bw_readability'.
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

    # 3b) the runnable Buse--Weimer readability FEATURE anchor (always OK; stdlib).
    anchors["bw_readability"] = _compute_bw_readability(bw_rows)

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
        # parallel readability-feature rows (kept separate so `rows` is unchanged).
        "bw_readability_rows": bw_rows,
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
        if anchor == "bw_readability":
            # distinct shape: readability feature re-implementation, no crosscheck.
            agg = a["aggregate_density_proxy"]
            sf = a.get("strongest_feature") or {}
            print(f"  {anchor} (BW2010 features, n={a['n_features']}; "
                  f"NOT the trained classifier): density proxy vs knob "
                  f"rho={_fmt(agg['incidental_knob_spearman_mean'])} (expect +); "
                  f"strongest feature {sf.get('name')} "
                  f"rho={_fmt(sf.get('incidental_knob_spearman_mean'))}")
            continue
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
