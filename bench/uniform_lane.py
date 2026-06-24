"""Uniform, tool-backed cross-language simplification lane (all five languages).

WHY THIS EXISTS
---------------
The refactor grader's Python lane is a rigorous per-language AST (cyclomatic +
ast_nodes + Halstead effort + radon-style maintainability index). The other four
languages (javascript/go/java/cpp) historically used a *text/regex proxy*
(``branch_keywords`` / ``brace_depth`` / ``tokens`` in ``eval.metrics``). Those two
lanes are not commensurable, so per-language simplification numbers could not be
pooled across languages (a reviewer point).

This module supplies ONE methodology usable for ALL FIVE languages, so the
cross-language number is single-methodology and poolable:

* **lizard cyclomatic complexity** — ``lizard`` is a real, language-agnostic CC
  implementation supporting python/javascript/go/java/c/cpp (the same tool the
  construct-validity anchor in ``bench/anchor.py`` already uses). Lower = simpler.
* **lizard token count** — the size facet (the source's lexical token total).
  Lower = smaller. (A model can *undershoot* the clean floor here = over-golfed;
  the grader's existing band/over-golf handling treats it as a size facet.)
* **Buse--Weimer surface-readability aggregate** — the *language-agnostic* feature
  extractor ``bw_readability_features`` from ``bench/anchor.py`` (operates on source
  text, stdlib only), reduced to a documented surface-DENSITY aggregate (sum of the
  density/structure feature subset). Higher = denser surface = less readable, so we
  keep it *lower = better* for the recovery formula by construction.

HONESTY BAR (do not overclaim)
------------------------------
This is "tool-backed cross-language complexity (lizard CC) + size (lizard tokens)
+ language-agnostic surface readability (Buse--Weimer density features)". It is
commensurable and poolable across all five languages. It is **NOT** a full
per-language AST with Halstead effort and a maintainability index — Python keeps
its deeper radon/AST lane as its headline. The uniform lane is the
cross-language-poolable view; we compute it for Python too so pooling is
apples-to-apples, but we never claim AST parity for the other four.

QUARANTINE
----------
``lizard`` is an optional third-party dependency kept OFF the zero-dependency core
(exactly like ``bench/anchor.py``). If it is not importable, :func:`available`
returns ``False`` and the grader degrades gracefully to the legacy regex proxy.
Install it in a throwaway venv to exercise this lane::

    python3 -m venv /tmp/lizvenv
    /tmp/lizvenv/bin/pip install lizard
    /tmp/lizvenv/bin/python bench/run_bench.py --baselines   # no API spend

CLEAN FLOOR
-----------
There is no per-language *idiomatic clean* renderer for js/go/java/cpp (only the
Python ``clean_baseline_static`` exists). Mirroring the legacy proxy's choice (it
compared non-Python sources against the Python clean floor as a language-neutral
"idiomatic code has ~0 branches/nesting" reference), the uniform lane uses the
**Python idiomatic clean baseline, measured by the same lizard+BW pipeline**, as
the neutral clean reference for every language. Measuring the floor with the very
same tools keeps the spaghetti->clean gap internally consistent per facet.
"""

from __future__ import annotations

import os
import sys
import textwrap
from typing import Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Reuse the language-agnostic Buse--Weimer feature extractor (do not reinvent).
from bench.anchor import bw_readability_features  # noqa: E402

# The three uniform facets, all oriented LOWER = better so the grader's existing
# direction-agnostic ``_recovery`` (and the size-facet over-golf handling) work
# unchanged. ``UNIFORM_FACETS`` is the public facet tuple; ``UNIFORM_SIZE_FACETS``
# marks which of them are size facets (a model can undershoot the floor on these).
UNIFORM_FACETS = ("liz_cc", "liz_tokens", "bw_density")
UNIFORM_SIZE_FACETS = ("liz_cc", "liz_tokens", "bw_density")

# lizard's file-extension dispatch: feed source under a filename it recognises.
_LIZARD_EXT = {"python": "py", "javascript": "js", "go": "go",
               "java": "java", "cpp": "cpp", "c": "c"}

# Buse--Weimer Fig. 6 *density/structure* feature subset (monotone-worse with mess;
# the same subset ``bench/anchor.py`` documents for its surface-density aggregate).
# We sum them raw for a single source (the anchor's z-scoring is corpus-relative
# and not meaningful for one source); the absolute scale is immaterial because the
# recovery formula only uses the spaghetti->clean *gap* per facet.
_BW_DENSITY_FEATURES = (
    "avg_line_length", "max_line_length", "avg_identifiers", "max_identifiers",
    "avg_keywords", "max_keywords", "avg_numbers", "max_numbers",
    "avg_periods", "avg_commas", "avg_parentheses", "avg_arithmetic_ops",
    "avg_comparison_ops", "avg_assignments", "avg_branches", "avg_loops",
    "avg_indentation", "max_indentation",
    "max_char_occurrences", "max_identifier_occurrences",
)


# --------------------------------------------------------------------------- #
# optional lizard probe (quarantined, exactly like anchor.py)
# --------------------------------------------------------------------------- #
_LIZARD = None          # cached module handle (or False once a probe has failed)


def _lizard():
    """Return the imported ``lizard`` module, or ``None`` if unavailable. Probed
    once and cached. Never raises — a missing tool must never break the core."""
    global _LIZARD
    if _LIZARD is None:
        try:
            import lizard  # noqa: F401
            _LIZARD = lizard
        except Exception:  # noqa: BLE001
            _LIZARD = False
    return _LIZARD or None


def available() -> bool:
    """True iff the tool-backed uniform lane can run (i.e. ``lizard`` imports)."""
    return _lizard() is not None


def lizard_version() -> Optional[str]:
    liz = _lizard()
    return getattr(liz, "version", "unknown") if liz else None


# --------------------------------------------------------------------------- #
# wrapping: lizard analyses *functions*. Go/Java/C++ already define functions
# (``func main`` / ``main`` / helpers), but our generated Python and JavaScript are
# flat top-level scripts, so wrap them in a single function (the same trick
# ``bench/anchor.py`` uses for the Python anchors). Static analysis tolerates the
# undefined free names.
# --------------------------------------------------------------------------- #
def _wrap_for_lizard(language: str, src: str) -> str:
    body = src if src.strip() else ("pass" if language == "python" else "")
    if language == "python":
        return "def _spaghetti():\n" + textwrap.indent(body, "    ")
    if language == "javascript":
        return "function _spaghetti(){\n" + body + "\n}\n"
    return src  # go / java / cpp / c: functions already present


def _lizard_facets(language: str, src: str) -> Optional[Dict[str, float]]:
    """``{'liz_cc': ..., 'liz_tokens': ...}`` via lizard, or ``None`` if the tool is
    absent. CC is summed over all functions lizard finds (the whole translation
    unit's decision count); tokens is the file-level lexical token total."""
    liz = _lizard()
    if liz is None:
        return None
    ext = _LIZARD_EXT.get(language, "txt")
    res = liz.analyze_file.analyze_source_code(f"_spaghetti.{ext}",
                                               _wrap_for_lizard(language, src))
    funcs = res.function_list
    cc = float(sum(f.cyclomatic_complexity for f in funcs)) if funcs else 1.0
    tokens = float(getattr(res, "token_count", 0) or 0)
    return {"liz_cc": cc, "liz_tokens": tokens}


def _bw_density(src: str) -> float:
    """Surface-density aggregate: raw sum of the documented Buse--Weimer density/
    structure features (higher = denser = less readable). Language-agnostic; reuses
    ``bench/anchor.py``'s extractor verbatim."""
    feats = bw_readability_features(src)
    return float(sum(feats.get(k, 0.0) for k in _BW_DENSITY_FEATURES))


def facets(language: str, src: str) -> Optional[Dict[str, float]]:
    """All three uniform facets for one source, or ``None`` if lizard is absent.

    Buse--Weimer density is stdlib-only and always computable, but the *lane* is
    only offered when lizard is present (so the lane is either fully tool-backed or
    cleanly skipped — we never mix lizard-present CC with a lizard-absent fallback
    inside one facet vector)."""
    lf = _lizard_facets(language, src)
    if lf is None:
        return None
    lf["bw_density"] = _bw_density(src)
    return lf


# --------------------------------------------------------------------------- #
# recovery (mirrors grade._recovery; kept here so callers can build the uniform
# poolable number without importing grade, avoiding a cycle)
# --------------------------------------------------------------------------- #
def _recovery(spag: float, model: float, clean: float) -> Optional[float]:
    denom = spag - clean
    if denom == 0:
        return None
    return max(-1.0, min(1.0, (spag - model) / denom))


def geomean(values: List[float]) -> float:
    vals = list(values)
    if not vals:
        return 0.0
    prod = 1.0
    for v in vals:
        prod *= v
    return prod ** (1.0 / len(vals))


def quality(language: str, model_src: str, spaghetti_src: str,
            clean_src: str) -> Optional[float]:
    """Single-methodology cross-language simplification quality in ``[0, 1]``:
    geometric mean over the uniform facets of the (clamped) fraction of the
    spaghetti->clean gap the model closed. ``clean_src`` is the neutral clean
    reference source (the Python idiomatic clean baseline; see module docstring).
    Returns ``None`` when lizard is absent or no facet has a usable gap.

    NB this is the *quality given semantic equivalence*; the caller multiplies by
    its own semantic gate, exactly as the headline ``simplification_quality`` does.
    """
    sf = facets(language, spaghetti_src)
    mf = facets(language, model_src)
    cf = facets(language, clean_src)
    if sf is None or mf is None or cf is None:
        return None
    recs = [_recovery(sf[f], mf[f], cf[f]) for f in UNIFORM_FACETS]
    pos = [max(0.0, v) for v in recs if v is not None]
    if not any(v is not None for v in recs):
        return None
    return geomean(pos) if pos else 0.0


def static_scalar(language: str, src: str) -> Optional[float]:
    """A single lower-is-better cross-language static-complexity scalar for the
    metric-heuristic judge, on the SAME uniform footing as the refactor lane
    (lizard CC + lizard tokens + Buse--Weimer surface density, additively blended).
    Only the *ordering* matters for the pairwise pick. ``None`` if lizard absent."""
    f = facets(language, src)
    if f is None:
        return None
    return f["liz_cc"] + f["liz_tokens"] + f["bw_density"]
