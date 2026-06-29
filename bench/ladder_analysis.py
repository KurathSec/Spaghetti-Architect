"""Reproduce the capability-ladder paper tables from the persisted raw model completions.

This regenerates, with ZERO API calls, the two numbers tables in the baselines section:

* ``ladder_comprehend.json``  -- overall + per-family comprehension exact match per model
  (paper Table ``tab:ladder``), and
* ``ladder_scaling.json``     -- comprehension exact match as a function of the intrinsic
  scale knob per family per model (paper Table ``tab:scaling``).

It re-grades the persisted ``raw_outputs`` in ``bench/out/subagent/comprehend__<model>.json``
(or the ``*.partial.jsonl`` checkpoint) against the oracle, using the SAME grader the live
harness uses (``tasks._grade_comprehend_outputs`` -> ``grade.grade_comprehend_one``). It was
validated to match the harness grader exactly on a clean batch (DeepSeek-V4-Flash: 0.8385 ==
0.8385); it deliberately bypasses the harness ``_rebuild_comprehend_item`` source-regen so it
is unaffected by the (now-fixed) live-run finalize bugs.

Usage:  python3 bench/ladder_analysis.py
"""
from __future__ import annotations

import collections
import gzip
import json
import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bench import dataset as D  # noqa: E402
from bench import tasks as T  # noqa: E402

SUBAGENT = os.path.join(_HERE, "out", "subagent")
LADDER_DATA = os.path.join(_HERE, "out", "ladder")  # committed compact gzipped raw outputs
OUT = os.path.join(_HERE, "out")

# Ladder in capability order (label -> on-disk model-id slug, '/' replaced by '-').
LADDER = [
    ("Llama-3.1-8B", "meta-llama-Meta-Llama-3.1-8B-Instruct"),
    ("Mistral-24B", "mistralai-Mistral-Small-3.2-24B-Instruct-2506"),
    ("Llama-3.3-70B", "meta-llama-Llama-3.3-70B-Instruct-Turbo"),
    ("DeepSeek-V4-Flash", "deepseek-ai-DeepSeek-V4-Flash"),
]
SCALE_FAMILIES = ["agg_stats", "config_resolver", "allowlist", "threshold_select"]


def _records_for(slug: str) -> list:
    """All persisted comprehend records for a model. Primary source is the committed,
    compact, gzipped raw-output artifact under ``out/ladder/`` (so the paper tables
    reproduce with zero API from version control); if absent, fall back to a fresh run's
    transient output under ``out/subagent/`` (final file, else partial checkpoint)."""
    g = os.path.join(LADDER_DATA, f"comprehend__{slug}.jsonl.gz")
    if os.path.exists(g):
        with gzip.open(g, "rt") as fh:
            return [json.loads(l) for l in fh if l.strip()]
    p = os.path.join(SUBAGENT, f"comprehend__{slug}.partial.jsonl")
    f = os.path.join(SUBAGENT, f"comprehend__{slug}.json")
    recs = [json.loads(l) for l in open(p) if l.strip()] if os.path.exists(p) else []
    if len(recs) < 1500 and os.path.exists(f):
        recs = json.load(open(f)).get("items", recs)
    return recs


def _scale_of(rec: dict):
    intr = rec.get("intrinsic", {})
    ks = [k for k in intr if k != "n_ops"]
    return intr[ks[0]] if ks else None


def _cluster_ci95(clusters, n_boot=4000, seed=20260619):
    """Base-IR-clustered bootstrap 95% CI for the overall exact match. Each cluster is one
    (sample, variant) program logic; its profile x language renderings are correlated, so we
    resample whole clusters (cluster-robust) rather than items, which would understate the
    interval. Deterministic given the seed."""
    groups = [g for g in clusters.values() if g]
    if not groups:
        return None
    rng = random.Random(seed)
    n = len(groups)
    means = []
    for _ in range(n_boot):
        pool = []
        for _ in range(n):
            pool.extend(groups[rng.randrange(n)])
        means.append(sum(pool) / len(pool))
    means.sort()
    return [round(means[int(0.025 * n_boot)], 4), round(means[int(0.975 * n_boot)], 4)]


def main() -> int:
    sp = D.load("dev")
    stem_fam = {it.stem: it.family for it in sp.items}
    prog_cache: dict = {}

    def grade(rec):
        stem = T._stem_for(sp, rec["sample"], rec.get("variant", "base"))
        if stem not in prog_cache:
            prog_cache[stem] = sp.program(stem)
        em = T._grade_comprehend_outputs(rec["raw_outputs"], prog_cache[stem])["exact_match_rate"]
        return em, stem_fam.get(stem, "?")

    overall: dict = {}
    scaling: dict = {}
    for label, slug in LADDER:
        recs = _records_for(slug)
        if not recs:
            print(f"{label}: NO DATA"); continue
        per_fam = collections.defaultdict(list)
        per_lang = collections.defaultdict(list)
        per_profile = collections.defaultdict(list)
        by_fam_scale = collections.defaultdict(lambda: collections.defaultdict(list))
        clusters = collections.defaultdict(list)  # (sample,variant) -> item EMs (cluster bootstrap)
        all_em = []
        for rec in recs:
            raw = rec.get("raw_outputs")
            if not raw or raw == ["<mock>"]:
                continue
            em, fam = grade(rec)
            per_fam[fam].append(em); all_em.append(em)
            clusters[(rec.get("sample"), rec.get("variant", "base"))].append(em)
            per_lang[rec.get("language", "?")].append(em)
            per_profile[rec.get("profile", "?")].append(em)
            sc = _scale_of(rec)
            if fam in SCALE_FAMILIES and sc is not None:
                by_fam_scale[fam][sc].append(em)
        _mean = lambda v: round(sum(v) / len(v), 4)
        overall[label] = {
            "slug": slug, "n": len(all_em),
            "overall_exact_match": _mean(all_em) if all_em else None,
            "overall_exact_match_ci95": _cluster_ci95(clusters),
            "per_family": {f: _mean(v) for f, v in sorted(per_fam.items())},
            "by_language": {L: _mean(v) for L, v in sorted(per_lang.items())},
            "by_profile": {p: _mean(per_profile[p])
                           for p in ("minimal", "standard", "max") if p in per_profile},
        }
        scaling[label] = {fam: {s: round(sum(by_fam_scale[fam][s]) / len(by_fam_scale[fam][s]), 3)
                                for s in sorted(by_fam_scale[fam])}
                          for fam in SCALE_FAMILIES}
        print(f"{label:18s} n={overall[label]['n']:5d} overall={overall[label]['overall_exact_match']}")

    json.dump(overall, open(os.path.join(OUT, "ladder_comprehend.json"), "w"), indent=2)
    json.dump(scaling, open(os.path.join(OUT, "ladder_scaling.json"), "w"), indent=2)
    print("wrote out/ladder_comprehend.json + out/ladder_scaling.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
