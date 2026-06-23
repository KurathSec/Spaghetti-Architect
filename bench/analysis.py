#!/usr/bin/env python3
"""Phase 4 — the pre-registered inferential pipeline (stdlib-core).

The benchmark's headline question is whether an LLM judge's rating *tracks the
by-construction knob rank* (rank 0 = cleanest). This module is the pre-registered
inferential test the paper references: a mixed-effects model of

    rating ~ knob_rank        with random intercepts for {family, sample, language}

fit per model-under-test, with **multiple-comparison correction across models**
(Benjamini--Hochberg FDR and Holm--Bonferroni). It loads the merged
``bench/out/results.json`` -> reconstructs the long-form judge observations
``(knob_rank, rating, family, sample, language, model)`` from the judge per-item
records -> fits the model -> writes a compact ``bench/out/analysis.json``.

**Optional anchor dependency.** Like ``bench/anchor.py``'s radon/lizard, the
mixed-effects fit uses ``statsmodels`` only if it is importable (install it in a
throwaway venv, e.g. ``python3 -m venv /tmp/anchorvenv &&
/tmp/anchorvenv/bin/pip install statsmodels`` — it is kept off the zero-dependency
core). When statsmodels is absent the module does **not** fail: it records an honest
``{"status": "SKIP_statsmodels", ...}`` for the parametric fit AND computes a
**cluster-bootstrap fallback** — the slope of rating-on-knob_rank, with a 95% CI
obtained by resampling whole *clusters* (the (family, sample) groups), so the
between-cluster dependence is respected rather than ignored. The slope point estimate
is the cluster-robust OLS slope on the full sample; the CI is the cluster bootstrap.

Everything here is pure stdlib + :func:`bench.grade.ci95_bootstrap` (imported, not
re-implemented) + the optional statsmodels anchor. Deterministic given a seed.
"""

from __future__ import annotations

import json
import os
import random
import sys
from typing import Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Reuse the project's bootstrap (do NOT duplicate it). grade.py is stdlib + eval.metrics.
from bench.grade import ci95_bootstrap  # noqa: E402

DEFAULT_RESULTS = os.path.join(_HERE, "out", "results.json")
ANALYSIS_PATH = os.path.join(_HERE, "out", "analysis.json")
_BOOT_ITERS = 2000
_BOOT_SEED = 0


# --------------------------------------------------------------------------- #
# 1) Benjamini--Hochberg FDR (pure stdlib)
# --------------------------------------------------------------------------- #
def bh_fdr(pvalues: List[float], q: float = 0.05) -> dict:
    """Benjamini--Hochberg step-up FDR control at level ``q``.

    Returns ``{"q", "n", "reject": [bool...], "adjusted": [float...],
    "n_significant"}`` with ``reject``/``adjusted`` in the *input* order. The adjusted
    p-values are the standard BH-monotone q-values (enforced non-decreasing in rank).
    ``None`` entries (missing/unfittable models) are passed through as non-significant
    with adjusted ``None`` and excluded from the rank denominator."""
    idx = [i for i, p in enumerate(pvalues) if p is not None]
    m = len(idx)
    reject = [False] * len(pvalues)
    adjusted: List[Optional[float]] = [None] * len(pvalues)
    if m == 0:
        return {"q": q, "n": 0, "reject": reject, "adjusted": adjusted, "n_significant": 0}
    order = sorted(idx, key=lambda i: pvalues[i])            # ascending p
    # BH q-values: q_(k) = min over j>=k of (p_(j) * m / j), enforced monotone.
    running = 1.0
    for rank in range(m, 0, -1):                             # k = m..1 (1-based)
        i = order[rank - 1]
        val = min(1.0, pvalues[i] * m / rank)
        running = min(running, val)
        adjusted[i] = running
    # rejection: largest k with p_(k) <= (k/m) q; reject all ranks <= that k.
    kmax = 0
    for rank in range(1, m + 1):
        i = order[rank - 1]
        if pvalues[i] <= (rank / m) * q:
            kmax = rank
    for rank in range(1, kmax + 1):
        reject[order[rank - 1]] = True
    return {"q": q, "n": m, "reject": reject, "adjusted": adjusted,
            "n_significant": sum(reject)}


# --------------------------------------------------------------------------- #
# 2) Holm--Bonferroni (pure stdlib)
# --------------------------------------------------------------------------- #
def holm_bonferroni(pvalues: List[float], alpha: float = 0.05) -> dict:
    """Holm--Bonferroni step-down FWER control at level ``alpha``.

    Returns ``{"alpha", "n", "reject": [bool...], "adjusted": [float...],
    "n_significant"}`` in input order. Adjusted p-value of the k-th smallest (1-based)
    is ``(m-k+1) * p``, enforced non-decreasing along the step-down order and clamped
    to 1. Step-down stops rejecting at the first failure. ``None`` entries pass through
    as non-significant and are excluded from the family size ``m``."""
    idx = [i for i, p in enumerate(pvalues) if p is not None]
    m = len(idx)
    reject = [False] * len(pvalues)
    adjusted: List[Optional[float]] = [None] * len(pvalues)
    if m == 0:
        return {"alpha": alpha, "n": 0, "reject": reject, "adjusted": adjusted,
                "n_significant": 0}
    order = sorted(idx, key=lambda i: pvalues[i])            # ascending p
    running = 0.0
    still_rejecting = True
    for k, i in enumerate(order):                            # k = 0..m-1
        adj = min(1.0, (m - k) * pvalues[i])
        running = max(running, adj)                          # monotone non-decreasing
        adjusted[i] = running
        if still_rejecting and pvalues[i] <= alpha / (m - k):
            reject[i] = True
        else:
            still_rejecting = False
    return {"alpha": alpha, "n": m, "reject": reject, "adjusted": adjusted,
            "n_significant": sum(reject)}


# --------------------------------------------------------------------------- #
# 3) bootstrap CI — reuse grade.ci95_bootstrap (re-exported, not duplicated)
# --------------------------------------------------------------------------- #
def bootstrap_ci(xs: List[float], iters: int = _BOOT_ITERS, seed: int = _BOOT_SEED) -> List[float]:
    """Deterministic percentile bootstrap 95% CI of the mean. Thin re-export of
    :func:`bench.grade.ci95_bootstrap` so callers of ``analysis`` have one import."""
    return ci95_bootstrap(xs, iters=iters, seed=seed)


# --------------------------------------------------------------------------- #
# OLS slope + cluster bootstrap (stdlib; the statsmodels-absent fallback)
# --------------------------------------------------------------------------- #
def _ols_slope(xs: List[float], ys: List[float]) -> Optional[float]:
    """Simple-regression slope of y on x (least squares). ``None`` if x has no
    variance or there are <2 points."""
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    sxy = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    return sxy / sxx


def cluster_bootstrap_slope(observations: List[dict], iters: int = _BOOT_ITERS,
                            seed: int = _BOOT_SEED,
                            cluster_keys: Tuple[str, ...] = ("family", "sample")) -> dict:
    """Cluster-robust slope of ``rating ~ knob_rank`` with a 95% CI by resampling
    whole clusters (default: the ``(family, sample)`` groups, the random-intercept
    grouping the mixed model would absorb). The point estimate is the OLS slope on the
    full data; the CI is the percentile cluster bootstrap. Deterministic given ``seed``.

    Returns ``{"slope", "ci95": [lo, hi], "n_obs", "n_clusters", "iters", "method"}``
    (slope/ci95 ``None`` when unfittable)."""
    pts = [(float(o["knob_rank"]), float(o["rating"])) for o in observations
           if o.get("knob_rank") is not None and o.get("rating") is not None]
    if len(pts) < 2:
        return {"slope": None, "ci95": [None, None], "n_obs": len(pts),
                "n_clusters": 0, "iters": iters, "method": "cluster_bootstrap"}
    point = _ols_slope([p[0] for p in pts], [p[1] for p in pts])

    # group observations into clusters
    clusters: Dict[tuple, List[Tuple[float, float]]] = {}
    for o in observations:
        if o.get("knob_rank") is None or o.get("rating") is None:
            continue
        key = tuple(o.get(k) for k in cluster_keys)
        clusters.setdefault(key, []).append((float(o["knob_rank"]), float(o["rating"])))
    keys = list(clusters)
    n_clusters = len(keys)
    if n_clusters < 2 or point is None:
        return {"slope": point, "ci95": [point, point] if point is not None else [None, None],
                "n_obs": len(pts), "n_clusters": n_clusters, "iters": iters,
                "method": "cluster_bootstrap"}

    rng = random.Random(seed)
    slopes: List[float] = []
    for _ in range(iters):
        xs: List[float] = []
        ys: List[float] = []
        for _ in range(n_clusters):                         # resample clusters w/ replacement
            for x, y in clusters[keys[rng.randrange(n_clusters)]]:
                xs.append(x)
                ys.append(y)
        s = _ols_slope(xs, ys)
        if s is not None:
            slopes.append(s)
    if not slopes:
        return {"slope": point, "ci95": [point, point], "n_obs": len(pts),
                "n_clusters": n_clusters, "iters": iters, "method": "cluster_bootstrap"}
    slopes.sort()
    lo = slopes[int(0.025 * len(slopes))]
    hi = slopes[min(len(slopes) - 1, int(0.975 * len(slopes)))]
    return {"slope": point, "ci95": [lo, hi], "n_obs": len(pts),
            "n_clusters": n_clusters, "iters": iters, "method": "cluster_bootstrap"}


# --------------------------------------------------------------------------- #
# 4) the mixed-effects fit (statsmodels optional; honest SKIP + fallback)
# --------------------------------------------------------------------------- #
def judge_mixed_effects(observations: List[dict], seed: int = _BOOT_SEED) -> dict:
    """Fit ``rating ~ knob_rank`` with random intercepts for family / sample /
    language for one model's judge observations.

    Uses ``statsmodels`` MixedLM **iff importable**; the grouping uses ``family`` with
    ``sample`` and ``language`` as variance components (statsmodels supports a single
    top-level group, so the finer clusters enter as VC groups). On success returns the
    parametric slope (fixed effect of ``knob_rank``), its standard error, z, and a
    two-sided p-value, with ``status="statsmodels"``.

    When statsmodels is **absent** (or the fit raises) it returns
    ``status="SKIP_statsmodels"`` AND a populated cluster-bootstrap fallback so the
    pipeline still yields a robust slope + 95% CI. ``p_value`` is then derived from the
    bootstrap (fraction of resampled slopes on the opposite side of 0, two-sided),
    which the multiple-comparison corrections consume uniformly.

    A faithful judge has a **negative** slope (rating falls as the mess/rank rises)."""
    fallback = cluster_bootstrap_slope(observations, seed=seed)

    n_obs = fallback["n_obs"]
    if n_obs < 2:
        return {"status": "INSUFFICIENT_DATA", "n_obs": n_obs, "slope": None,
                "p_value": None, "fallback": fallback}

    try:
        import statsmodels.api as sm           # noqa: F401
        import statsmodels.formula.api as smf
    except Exception:  # noqa: BLE001  -> honest SKIP + cluster-bootstrap fallback
        boot_p = _bootstrap_pvalue(observations, fallback["slope"], seed=seed)
        return {
            "status": "SKIP_statsmodels",
            "reason": "statsmodels not importable; install it in a quarantined venv "
                      "(pip install statsmodels). It is kept off the zero-dependency "
                      "core; this is the honest SKIP + cluster-bootstrap fallback.",
            "n_obs": n_obs,
            "slope": fallback["slope"],          # cluster-robust OLS slope
            "ci95": fallback["ci95"],            # cluster bootstrap CI
            "p_value": boot_p,                   # bootstrap two-sided p
            "fallback": fallback,
        }

    # statsmodels available: MixedLM with family as the group and sample/language as VCs.
    try:
        import pandas as pd
        rows = [{"rating": float(o["rating"]), "knob_rank": float(o["knob_rank"]),
                 "family": str(o.get("family")), "sample": str(o.get("sample")),
                 "language": str(o.get("language"))} for o in observations
                if o.get("knob_rank") is not None and o.get("rating") is not None]
        df = pd.DataFrame(rows)
        vc = {"sample": "0 + C(sample)", "language": "0 + C(language)"}
        model = smf.mixedlm("rating ~ knob_rank", df, groups=df["family"],
                            vc_formula=vc, re_formula="1")
        res = model.fit(reml=False, method="lbfgs", disp=False)
        slope = float(res.fe_params["knob_rank"])
        se = float(res.bse["knob_rank"])
        z = float(res.tvalues["knob_rank"])
        p = float(res.pvalues["knob_rank"])
        return {
            "status": "statsmodels",
            "statsmodels_version": _sm_version(),
            "n_obs": n_obs,
            "groups": {"top": "family", "variance_components": ["sample", "language"]},
            "slope": slope, "std_err": se, "z": z, "p_value": p,
            "converged": bool(getattr(res, "converged", True)),
            "fallback": fallback,                # cluster bootstrap kept for cross-check
        }
    except Exception as ex:  # noqa: BLE001 -> fit failed; degrade to the honest fallback
        boot_p = _bootstrap_pvalue(observations, fallback["slope"], seed=seed)
        return {
            "status": "SKIP_statsmodels",
            "reason": f"statsmodels present but the MixedLM fit failed ({type(ex).__name__}: "
                      f"{str(ex)[:160]}); using the cluster-bootstrap fallback.",
            "n_obs": n_obs,
            "slope": fallback["slope"], "ci95": fallback["ci95"], "p_value": boot_p,
            "fallback": fallback,
        }


def _sm_version() -> str:
    try:
        import statsmodels
        return getattr(statsmodels, "__version__", "unknown")
    except Exception:  # noqa: BLE001
        return "unknown"


def _bootstrap_pvalue(observations: List[dict], point: Optional[float],
                      seed: int = _BOOT_SEED, iters: int = _BOOT_ITERS) -> Optional[float]:
    """Two-sided cluster-bootstrap p-value for H0: slope = 0. Approximated as twice the
    smaller bootstrap tail mass on the opposite side of 0 from the point estimate
    (percentile-bootstrap test), clamped to [0,1]. ``None`` when unfittable."""
    if point is None:
        return None
    clusters: Dict[tuple, List[Tuple[float, float]]] = {}
    for o in observations:
        if o.get("knob_rank") is None or o.get("rating") is None:
            continue
        key = (o.get("family"), o.get("sample"))
        clusters.setdefault(key, []).append((float(o["knob_rank"]), float(o["rating"])))
    keys = list(clusters)
    if len(keys) < 2:
        return None
    rng = random.Random(seed + 1)
    slopes: List[float] = []
    for _ in range(iters):
        xs: List[float] = []
        ys: List[float] = []
        for _ in range(len(keys)):
            for x, y in clusters[keys[rng.randrange(len(keys))]]:
                xs.append(x)
                ys.append(y)
        s = _ols_slope(xs, ys)
        if s is not None:
            slopes.append(s)
    if not slopes:
        return None
    n = len(slopes)
    # tail mass on the opposite side of 0 from the observed direction
    if point >= 0:
        tail = sum(1 for s in slopes if s <= 0) / n
    else:
        tail = sum(1 for s in slopes if s >= 0) / n
    return max(0.0, min(1.0, 2.0 * tail))


# --------------------------------------------------------------------------- #
# 5) long-form reconstruction + top-level analyze()
# --------------------------------------------------------------------------- #
def _family_of(sample_stem: str, stem2fam: Dict[str, str]) -> str:
    """Family for a judge item's sample. Authoritative dataset map first, else strip
    the trailing intrinsic-scale suffix ``_<KNOB><digits>`` (e.g. ``allowlist_L8`` ->
    ``allowlist``)."""
    if sample_stem in stem2fam:
        return stem2fam[sample_stem]
    import re
    return re.sub(r"_[A-Z]\d+$", "", sample_stem)


def _stem_to_family() -> Dict[str, str]:
    """Best-effort authoritative stem->family map from the dev split; ``{}`` if the
    dataset cannot be loaded (the regex fallback then applies)."""
    try:
        from bench import dataset as D
        return {it.stem: it.family for it in D.load("dev").items}
    except Exception:  # noqa: BLE001
        return {}


def _observations_from_judge_items(items: List[dict], model: str,
                                   stem2fam: Dict[str, str]) -> List[dict]:
    """One long-form observation per (judge item, knob level): each judge per-item
    record carries ``levels`` (label per rank, index 0 = cleanest) and
    ``rating_by_level`` (mean rating parallel to ``levels``), plus ``sample`` and
    ``language``; ``family`` is recovered from the sample stem. Emits
    ``{knob_rank, rating, family, sample, language, model}``."""
    obs: List[dict] = []
    for it in items:
        levels = it.get("levels") or []
        ratings = it.get("rating_by_level") or []
        sample = it.get("sample")
        language = it.get("language")
        family = _family_of(sample or "", stem2fam)
        for rank, rating in enumerate(ratings):
            if rating is None or rank >= len(levels):
                continue
            obs.append({"knob_rank": rank, "rating": float(rating),
                        "family": family, "sample": sample,
                        "language": language, "model": model})
    return obs


def _load_judge_items_per_model(results_path: str) -> Dict[str, List[dict]]:
    """Collect judge per-item records per model. Prefer the subagent judge payloads
    (they carry per-item rows); they live next to ``results.json`` under
    ``out/subagent/judge__*.json``. Falls back to any ``items`` embedded in
    results.json (older/inlined schema)."""
    out_dir = os.path.dirname(os.path.abspath(results_path))
    by_model: Dict[str, List[dict]] = {}

    subagent_dir = os.path.join(out_dir, "subagent")
    if os.path.isdir(subagent_dir):
        for fn in sorted(os.listdir(subagent_dir)):
            if not (fn.startswith("judge__") and fn.endswith(".json")):
                continue
            try:
                pl = json.load(open(os.path.join(subagent_dir, fn), encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if pl.get("task") != "judge":
                continue
            by_model.setdefault(pl.get("model", "?"), []).extend(pl.get("items", []))

    if by_model:
        return by_model

    # fallback: results.json with inlined per-item judge rows (defensive)
    try:
        results = json.load(open(results_path, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    for pl in results.get("subagents", []) if isinstance(results, dict) else []:
        if pl.get("task") == "judge":
            by_model.setdefault(pl.get("model", "?"), []).extend(pl.get("items", []))
    return by_model


def analyze(results_path: str = DEFAULT_RESULTS, q: float = 0.05,
            alpha: float = 0.05, seed: int = _BOOT_SEED,
            write: bool = True) -> dict:
    """Run the pre-registered judge analysis end-to-end.

    Loads the merged results next to ``results_path``, reconstructs the long-form judge
    observations per model, fits ``rating ~ knob_rank`` (mixed effects via statsmodels
    if importable, else SKIP + cluster-bootstrap fallback) for each model, applies
    Benjamini--Hochberg FDR and Holm corrections **across models**, and writes a compact
    ``out/analysis.json``. Returns the same record."""
    out_dir = os.path.dirname(os.path.abspath(results_path))
    by_model = _load_judge_items_per_model(results_path)
    stem2fam = _stem_to_family()

    per_model: Dict[str, dict] = {}
    models_order: List[str] = []
    for model in sorted(by_model):
        # The non-LLM metric-heuristic judge and the mock have no rating ladder shaped
        # for this inferential question only if they lack rating_by_level; include any
        # model whose judge items carry the ladder.
        obs = _observations_from_judge_items(by_model[model], model, stem2fam)
        fit = judge_mixed_effects(obs, seed=seed)
        per_model[model] = fit
        models_order.append(model)

    # multiple-comparison correction across models (skip None p-values gracefully)
    pvals = [per_model[m].get("p_value") for m in models_order]
    bh = bh_fdr(pvals, q=q)
    holm = holm_bonferroni(pvals, alpha=alpha)
    corrections = {"models": models_order, "raw_p": pvals,
                   "bh_fdr": {"q": q, "adjusted": bh["adjusted"],
                              "reject": bh["reject"], "n_significant": bh["n_significant"]},
                   "holm": {"alpha": alpha, "adjusted": holm["adjusted"],
                            "reject": holm["reject"], "n_significant": holm["n_significant"]}}

    any_sm = any(per_model[m].get("status") == "statsmodels" for m in models_order)
    record = {
        "analysis": "judge rating ~ knob_rank | random intercepts {family, sample, language}",
        "results_path": os.path.abspath(results_path),
        "engine": "statsmodels" if any_sm else "SKIP_statsmodels+cluster_bootstrap",
        "statsmodels_available": any_sm,
        "n_models": len(models_order),
        "seed": seed,
        "per_model": per_model,
        "corrections": corrections,
        "note": "Faithful judge => negative knob_rank slope (rating falls as rank/mess "
                "rises). statsmodels is an OPTIONAL anchor dep (quarantined venv); the "
                "module imports and runs without it via the cluster-bootstrap fallback.",
    }
    if write:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "analysis.json"), "w", encoding="utf-8") as f:
            json.dump(record, f, separators=(",", ":"))
            f.write("\n")
    return record


def _compact(record: dict) -> dict:
    """A tiny printable digest of an :func:`analyze` record (no bulk rows)."""
    return {
        "engine": record.get("engine"),
        "statsmodels_available": record.get("statsmodels_available"),
        "n_models": record.get("n_models"),
        "per_model": {m: {"status": d.get("status"), "slope": d.get("slope"),
                          "ci95": d.get("ci95") or d.get("fallback", {}).get("ci95"),
                          "p_value": d.get("p_value")}
                      for m, d in record.get("per_model", {}).items()},
        "bh_n_significant": record.get("corrections", {}).get("bh_fdr", {}).get("n_significant"),
        "holm_n_significant": record.get("corrections", {}).get("holm", {}).get("n_significant"),
    }


def main(argv=None) -> int:
    path = (argv or sys.argv[1:] or [DEFAULT_RESULTS])[0]
    rec = analyze(path)
    print(json.dumps(_compact(rec), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
