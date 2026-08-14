"""Offline uniform-lane re-grade of the committed refactor record. Zero API.

The released model runs were graded in an environment without ``lizard``, so
``uniform_quality`` is null on every committed item and the paper reports no
cross-language quality number. The raw completions are committed, so this is
recoverable offline: run THIS script under the metrics venv
(``~/.venvs/spaghetti-metrics/bin/python bench/uniform_regrade.py``) and the
uniform lane activates.

Two products, written to ``bench/out/uniform_quality.json``:

1. ``ceilings``: the MEASURED scaffold-inclusive clean reference per language.
   For every committed refactor item, the engine's ``clean``-profile render
   (``bench.dataset.clean_source``: idiomatic body + always-on scaffold + JSON
   epilogue) is pushed through the FULL semantic gate via
   ``grade.grade_refactor_one`` and aggregated per language. This replaces the
   old algebraic-identity "ceiling" with a measurement (the identity is kept in
   ``baseline_panel`` under its own key). NOT an upper bound on model scores:
   the scaffold itself carries branches/tokens the uniform facets count, and a
   model rewrite that strips the scaffold legitimately scores higher. The
   hard measured claim is the gate: every clean render compiles, runs, and
   matches the oracle in all five languages (1500/1500).
2. ``models``: per-model per-language mean ``uniform_quality`` of the
   committed completions at k=1 (draw[0], the ablation convention), via the
   FAST PATH: gate from the ablation grade cache (ann1) times
   ``uniform_lane.quality`` on the extracted code — exactly the
   ``grade_refactor_one`` assembly (verified by the sample cross-check below).

Kill-switches:
- lizard must be importable (else exit: run under the metrics venv);
- the fast path must equal the full ``grade_refactor_one`` output on a seeded
  100-item subsample to 1e-9 (compile-run included);
- every clean ceiling render must pass the semantic gate (a failing clean
  render is an engine bug, not a statistic).

Scope note: the ANNOTATED (released) corpus only. The unannotated arm's
uniform facets would need unannotated spaghetti sources in a stripped process
(bw_density reads comments), and no published claim needs that number; scoped
out rather than silently mixed.
"""

from __future__ import annotations

import json
import os
import random
import sys
from concurrent.futures import ThreadPoolExecutor

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bench.suite_common import (  # noqa: E402
    MODELS,
    SUITE_SEED,
    all_grades,
    artifact_header,
    item_key,
    load_annotated,
    mean,
    write_artifact,
)
from bench import dataset as D  # noqa: E402
from bench import grade as G  # noqa: E402
from bench import tasks as T  # noqa: E402
from bench import uniform_lane as U  # noqa: E402
from eval import metrics as M  # noqa: E402

OUT = os.path.join(_HERE, "out", "uniform_quality.json")
LANGS = ("python", "javascript", "go", "java", "cpp")
XCHECK_N = 100


def _require_lizard() -> None:
    if not U.available():
        sys.exit("lizard is not importable: run under the metrics venv "
                 "(~/.venvs/spaghetti-metrics/bin/python)")


def _fast_uq(rec: dict, gate: float):
    """The grade_refactor_one uniform_quality assembly, without re-running the
    semantic gate (taken from the ablation cache instead)."""
    if gate == 0:
        return None
    it = T._rebuild_refactor_item(rec)
    model_src = G.extract_code(rec["raw_outputs"][0])
    uq = U.quality(it.language, model_src, it.spaghetti_src,
                   M.clean_baseline_static(it.program))
    return (gate * uq) if uq is not None else None


def _crosscheck(recs: list, gates: dict) -> int:
    """Full grade_refactor_one vs the fast path on a seeded subsample."""
    rng = random.Random(SUITE_SEED)
    sample = rng.sample(recs, min(XCHECK_N, len(recs)))

    def one(rec):
        it = T._rebuild_refactor_item(rec)
        full = G.grade_refactor_one(it.language, rec["raw_outputs"][0],
                                    it.spaghetti_src, it.program)
        key = item_key(rec)
        fast = _fast_uq(rec, gates[key])
        if full["semantic_ok"] != gates[key]:
            sys.exit(f"gate mismatch vs cache at {key}: "
                     f"{full['semantic_ok']} != {gates[key]}")
        a, b = full["uniform_quality"], fast
        if (a is None) != (b is None) or (a is not None and abs(a - b) > 1e-9):
            sys.exit(f"fast-path mismatch at {key}: full={a} fast={b}")
        return 1

    with ThreadPoolExecutor(max_workers=min(os.cpu_count() or 4, 10)) as ex:
        return sum(ex.map(one, sample))


def _model_block(short: str, slug: str, gates: dict) -> dict:
    recs = [r for r in load_annotated("refactor", slug) if r.get("raw_outputs")]
    per_lang: dict = {L: [] for L in LANGS}
    n_null = 0
    for rec in recs:
        uq = _fast_uq(rec, gates[item_key(rec)])
        if uq is None:
            n_null += 1
        else:
            per_lang[rec["language"]].append(uq)
    out = {}
    for L in LANGS:
        vals = per_lang[L]
        out[L] = {"mean": mean(vals) if vals else None, "n": len(vals),
                  "ci95": G.ci95_bootstrap(vals) if vals else None}
    pooled = [v for vs in per_lang.values() for v in vs]
    out["pooled"] = {"mean": mean(pooled), "n": len(pooled),
                     "ci95": G.ci95_bootstrap(pooled)}
    out["n_null"] = n_null
    return out


def _ceilings() -> dict:
    """Grade the clean render of every committed item through the full gate."""
    recs = [r for r in load_annotated("refactor", MODELS[-1][1])
            if r.get("raw_outputs")]

    def one(rec):
        it = T._rebuild_refactor_item(rec)
        clean_src = D.clean_source(it.program, it.language)
        g = G.grade_refactor_one(it.language, clean_src, it.spaghetti_src,
                                 it.program)
        return (rec["language"], g["semantic_ok"], g["simplification_quality"],
                g["uniform_quality"], item_key(rec))

    with ThreadPoolExecutor(max_workers=min(os.cpu_count() or 4, 10)) as ex:
        rows = list(ex.map(one, recs))

    gate_failures = [k for (_, ok, _, _, k) in rows if not ok]
    if gate_failures:
        sys.exit(f"{len(gate_failures)} clean renders FAILED the semantic gate "
                 f"(engine bug): {gate_failures[:5]}")

    out = {}
    for L in LANGS:
        sq = [s for (lang, _, s, _, _) in rows if lang == L and s is not None]
        uq = [u for (lang, _, _, u, _) in rows if lang == L and u is not None]
        out[L] = {
            "n": sum(1 for (lang, *_rest) in rows if lang == L),
            "semantic_ok_rate": 1.0,
            "simplification_quality_mean": mean(sq) if sq else None,
            "uniform_quality_mean": mean(uq) if uq else None,
            "uniform_quality_ci95": G.ci95_bootstrap(uq) if uq else None,
        }
    out["scaffold_inclusive"] = True
    out["not_an_upper_bound"] = ("the scaffold carries metric-countable "
                                 "structure; model rewrites that strip it can "
                                 "legitimately exceed these values")
    return out


def main() -> int:
    _require_lizard()
    cache = all_grades(use_cache=True)["refactor"]
    report = {"_meta": artifact_header(
        "uniform_regrade.py",
        ["bench/out/g3/refactor_dev__*.jsonl.gz",
         "bench/out/.annotation_ablation_grades.json (or gz re-grade)"],
        lane="uniform (lizard active)", k=1,
        scope="annotated (released) corpus only")}

    ds_gates = cache[MODELS[-1][0]]["ann1"]
    recs = [r for r in load_annotated("refactor", MODELS[-1][1])
            if r.get("raw_outputs")]
    n = _crosscheck(recs, ds_gates)
    report["fast_path_crosschecked_n"] = n
    print(f"fast path == full grade on {n} sampled items")

    report["models"] = {}
    for short, slug in MODELS:
        report["models"][short] = _model_block(short, slug, cache[short]["ann1"])
        p = report["models"][short]["pooled"]
        print(f"{short:18s} pooled uniform_quality {p['mean']:.4f} (n={p['n']})")

    print("grading clean ceilings (1500 gate runs)...")
    report["ceilings"] = _ceilings()
    for L in LANGS:
        c = report["ceilings"][L]
        print(f"ceiling {L:10s} uq={c['uniform_quality_mean']} "
              f"sq={c['simplification_quality_mean']}")

    write_artifact(OUT, report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
