"""Stability analysis of the committed k=8 archives. Zero API.

Three parts, all re-derived offline from committed artifacts:

(a) ``nondeterminism_report`` — within-item disagreement across the k=8
    temperature-0 draws of the ANNOTATED archives. Fast path: the shared grade
    cache (``ann8`` is the mean of 8 binary outcomes, so a value strictly
    inside (0,1) means the draws disagreed). Authoritative cross-check for
    comprehend: every draw of every ladder record is re-graded individually
    (``grade_comprehend_one`` on a rebuilt item; no compiling) and the
    mixed-item sets must agree with the cache route exactly. Refactor per-draw
    re-grading would compile untrusted code in five languages, so refactor uses
    the cache route only (stated in the artifact). Also reports draw0-vs-rest
    on comprehend (is draw[0] biased vs the mean of draws 1..7), which is the
    stability premise behind the k=1 ablation protocol.

(b) ``drift_diagnostics`` — per model per arm (annotated draw[0] from
    ladder/g3 vs unannotated draw[0] from the ablation archives, same items):
    completion length deciles (chars + lines), empty / unterminated code-fence
    counts (refactor, via ``extract_code``), JSON-parse failure counts
    (comprehend, via ``extract_json_obj``). DESCRIPTIVE ONLY: the arms differ
    by treatment AND ~2 weeks of serving-side drift, so no test is computed.

(c) ``variant_sensitivity`` — from the cache ``ann8`` grids (comprehend
    exact-match, refactor semantic-ok): for each (base representative,
    profile, language) cell with >= 2 variants present, the across-variant
    sample SD of the per-item rate; per-model mean SD with a base-IR-clustered
    bootstrap CI on shared draws. discovery_pipeline v0 records are excluded
    entirely (verified byte-identical duplicate of base).

Inputs: bench/out/{ladder,g3,ablation}/*.jsonl.gz, the shared grade cache,
bench/out/ladder_comprehend.json (reference for the pooled-EM sanity check).
Output: bench/out/stability.json (deterministic; byte-identical on re-run).

Kill-switches (the published figures are FROZEN; a mismatch means THIS script
is wrong — fix the script, never the expected value):
  * pooled mixed-outcome fraction: refactor within 0.049 +- 0.002 and
    comprehend within 0.0165 +- 0.002 (the published 4.9% / 1.7%);
  * the comprehend per-draw re-grade must reproduce the cache route's
    mixed-item set exactly, per model, and every re-derived k=8 mean must
    equal the cached ``ann8`` value;
  * pooling all per-item comprehend EMs per model, rounded to 4 decimals
    (the ``ladder_comprehend.json`` convention — its generator stores
    ``round(mean, 4)``), must equal that file's ``overall_exact_match``
    to 1e-9.
"""

from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bench.suite_common import (  # noqa: E402
    BOOTSTRAP,
    MODELS,
    ORDER,
    SUITE_SEED,
    all_grades,
    artifact_header,
    cluster_scores,
    draw_means,
    family_of,
    item_key,
    load_annotated,
    load_unannotated,
    mean,
    percentile_ci95,
    shared_cluster_draws,
    two_sided_bootstrap_p,
    write_artifact,
)
from bench import grade as G  # noqa: E402
from bench import tasks as T  # noqa: E402

OUT = os.path.join(_HERE, "out", "stability.json")
LADDER_REF = os.path.join(_HERE, "out", "ladder_comprehend.json")

TASKS = ["refactor", "comprehend"]
PROFILES = ["minimal", "standard", "max"]
LANGS = ["cpp", "go", "java", "javascript", "python"]
VARIANTS = ["v0", "v1", "v2", "v3", "v4"]

# Frozen published pooled mixed-outcome fractions (4.9% / 1.7%), with tolerance.
EXPECT_MIXED = {"refactor": (0.049, 0.002), "comprehend": (0.0165, 0.002)}


def _is_mixed(rate: float) -> bool:
    """Mixed outcome across the k=8 draws: the k=8 mean of a binary grade is
    strictly inside (0,1). ann8 values are exact multiples of 1/8, so the
    float comparison is exact."""
    return 0.0 < rate < 1.0


# --------------------------------------------------------------------------- #
# (a) nondeterminism
# --------------------------------------------------------------------------- #
def _comprehend_per_draw() -> dict:
    """Authoritative per-draw re-grade of the comprehend ladder archives:
    {model: {item_key: [em_draw0, ..., em_draw7]}}. Rebuilds each item from the
    dataset (no compiling) and grades every stored draw individually."""
    per_draw: dict = {}
    for short, slug in MODELS:
        recs = load_annotated("comprehend", slug)
        by_key: dict = {}
        for rec in recs:
            raw = rec.get("raw_outputs")
            if not raw or raw == ["<mock>"]:
                sys.exit(f"comprehend/{short}: record without real raw outputs "
                         f"({item_key(rec)})")
            it = T._rebuild_comprehend_item(rec)
            by_key[item_key(rec)] = [
                G.grade_comprehend_one(o, it.program)["exact_match"] for o in raw]
        per_draw[short] = by_key
        print(f"  per-draw re-graded comprehend/{short} "
              f"({len(by_key)} items)", flush=True)
    return per_draw


def _nondeterminism(grades: dict) -> dict:
    per_model: dict = {t: {} for t in TASKS}
    pooled: dict = {}
    for task in TASKS:
        n_all = n_mixed_all = 0
        for m in ORDER:
            a8 = grades[task][m]["ann8"]
            if not a8:
                sys.exit(f"{task}/{m}: empty ann8 grid")
            n = len(a8)
            n_mixed = sum(1 for v in a8.values() if _is_mixed(v))
            per_model[task][m] = {"n": n, "n_mixed": n_mixed,
                                  "mixed_fraction": n_mixed / n}
            n_all += n
            n_mixed_all += n_mixed
        frac = n_mixed_all / n_all
        pooled[task] = {"n": n_all, "n_mixed": n_mixed_all, "mixed_fraction": frac}
        exp, tol = EXPECT_MIXED[task]
        if abs(frac - exp) > tol:
            sys.exit(f"KILL {task}: pooled mixed fraction {frac:.4f} outside "
                     f"{exp} +- {tol}; the script is wrong, not the figure")

    # Authoritative comprehend cross-check: per-draw re-grade from the gz.
    per_draw = _comprehend_per_draw()
    crosscheck: dict = {}
    for m in ORDER:
        a8 = grades["comprehend"][m]["ann8"]
        pd = per_draw[m]
        if set(pd) != set(a8):
            sys.exit(f"KILL comprehend/{m}: re-grade item set != cache item set")
        for k, draws in pd.items():
            if len(draws) != 8:
                sys.exit(f"KILL comprehend/{m}: {k} has {len(draws)} draws, not 8")
            if sum(draws) / 8.0 != a8[k]:
                sys.exit(f"KILL comprehend/{m}: re-derived k=8 mean != cached "
                         f"ann8 for {k}")
        mixed_cache = {k for k, v in a8.items() if _is_mixed(v)}
        mixed_regrade = {k for k, d in pd.items() if len(set(d)) > 1}
        if mixed_cache != mixed_regrade:
            sys.exit(f"KILL comprehend/{m}: mixed-item sets disagree "
                     f"(cache {len(mixed_cache)} vs re-grade {len(mixed_regrade)})")
        crosscheck[m] = {"n_items": len(pd), "n_mixed": len(mixed_regrade),
                         "agrees_with_cache": True}

    # draw0 vs mean(draws 1..7), comprehend: the k=1 ablation protocol grades
    # draw[0] of the annotated arm; this asks whether draw[0] is biased.
    diff_scores = {m: {k: d[0] - mean(d[1:]) for k, d in per_draw[m].items()}
                   for m in ORDER}
    clusters = {m: cluster_scores(diff_scores[m]) for m in ORDER}
    ckeys = set(clusters[ORDER[0]])
    for m in ORDER:
        if set(clusters[m]) != ckeys:
            sys.exit(f"cluster space differs for {m} in draw0_vs_rest")
    draws = shared_cluster_draws(ckeys, BOOTSTRAP, SUITE_SEED)
    draw0_vs_rest: dict = {}
    for m in ORDER:
        dm = draw_means(clusters[m], draws)
        pd = per_draw[m]
        draw0_vs_rest[m] = {
            "n_items": len(pd),
            "n_clusters": len(ckeys),
            "mean_draw0": mean([d[0] for d in pd.values()]),
            "mean_rest": mean([mean(d[1:]) for d in pd.values()]),
            "diff_point": mean(list(diff_scores[m].values())),
            "diff_ci95": percentile_ci95(dm),
            "p_bootstrap": two_sided_bootstrap_p(dm),
        }

    return {
        "definition": "an item is MIXED when its k=8 binary grades disagree "
                      "across draws (k=8 mean strictly inside (0,1)); grades "
                      "are semantic_ok (refactor) / exact_match (comprehend) "
                      "on the annotated archives",
        "per_model_mixed": per_model,
        "pooled_mixed": pooled,
        "comprehend_per_draw_crosscheck": crosscheck,
        "refactor_route": "cache route only: refactor per-draw re-grading "
                          "compiles untrusted model code in five languages, so "
                          "no per-draw cross-check is run for it",
        "draw0_vs_rest_comprehend": draw0_vs_rest,
        "note": "the mixed fraction is a LOWER bound on serving-side "
                "nondeterminism: draws that fail identically (or succeed "
                "identically for different reasons) count as agreement. The "
                "k=1 ablation protocol additionally ASSUMES this within-item "
                "stability, measured on the annotated arm, propagates to the "
                "unannotated k=1 arm; that propagation is untested here.",
    }


# --------------------------------------------------------------------------- #
# (b) drift diagnostics
# --------------------------------------------------------------------------- #
def _deciles(values: list) -> list:
    """Nearest-rank deciles 0%,10%,...,100% (11 points) of a non-empty list."""
    if not values:
        raise ValueError("deciles of empty list")
    s = sorted(values)
    n = len(s)
    return [s[int(round(q * (n - 1) / 10.0))] for q in range(0, 11)]


def _arm_diag(task: str, recs: list, model: str, arm: str) -> tuple:
    firsts = {}
    for rec in recs:
        raw = rec.get("raw_outputs")
        if not raw or raw == ["<mock>"]:
            sys.exit(f"{task}/{model}/{arm}: record without real raw outputs")
        firsts[item_key(rec)] = raw[0]
    if len(firsts) != len(recs):
        sys.exit(f"{task}/{model}/{arm}: duplicate item keys")
    texts = [firsts[k] for k in sorted(firsts)]
    out = {
        "n": len(texts),
        "chars_deciles": _deciles([len(t) for t in texts]),
        "lines_deciles": _deciles([len(t.splitlines()) for t in texts]),
    }
    if task == "refactor":
        out["n_extract_code_empty"] = sum(1 for t in texts
                                          if G.extract_code(t) == "")
        out["n_unterminated_fence"] = sum(
            1 for t in texts if "```" in t and G._FENCE_RE.search(t) is None)
    else:
        out["n_json_parse_fail"] = sum(1 for t in texts
                                       if G.extract_json_obj(t) is None)
    return out, set(firsts)


def _drift() -> dict:
    report: dict = {
        "note": "DESCRIPTIVE ONLY: the annotated arm (released run) and the "
                "unannotated arm (ablation run, ~2 weeks later) differ by both "
                "the treatment (annotations stripped) and serving-side drift "
                "at the hosted endpoints, so arm differences are "
                "treatment-confounded and no statistical test is computed. "
                "All diagnostics use draw[0] of each record. Deciles are "
                "nearest-rank at 0%,10%,...,100%.",
    }
    for task in TASKS:
        report[task] = {}
        for short, slug in MODELS:
            ann, ann_keys = _arm_diag(task, load_annotated(task, slug),
                                      short, "annotated")
            un, un_keys = _arm_diag(task, load_unannotated(task, slug),
                                    short, "unannotated")
            if ann_keys != un_keys:
                sys.exit(f"{task}/{short}: annotated and unannotated arms "
                         f"cover different items")
            report[task][short] = {"annotated": ann, "unannotated": un}
    return report


# --------------------------------------------------------------------------- #
# (c) variant sensitivity
# --------------------------------------------------------------------------- #
def _variant_cells(a8: dict) -> dict:
    """{(rep, profile, language): [rate per variant]} over v0..v4, excluding
    discovery_pipeline v0 (byte-identical duplicate of base)."""
    reps = sorted({k.split("|")[0] for k in a8 if k.split("|")[3] != "base"})
    cells: dict = {}
    for rep in reps:
        for prof in PROFILES:
            for lang in LANGS:
                vals = []
                for v in VARIANTS:
                    if v == "v0" and family_of(rep) == "discovery_pipeline":
                        continue
                    key = f"{rep}|{prof}|{lang}|{v}"
                    if key in a8:
                        vals.append(a8[key])
                if len(vals) >= 2:
                    cells[(rep, prof, lang)] = vals
    return cells


def _variant_sensitivity(grades: dict) -> dict:
    ref = json.load(open(LADDER_REF))

    # KILL sanity first: the same ann8 grid, pooled over ALL items per model,
    # must reproduce the committed ladder_comprehend overall per model. That
    # file's generator stores round(mean, 4) (bench/ladder_analysis.py), so
    # its convention is matched by rounding the re-derived pooled mean.
    reference_check: dict = {}
    for m in ORDER:
        vals = list(grades["comprehend"][m]["ann8"].values())
        pooled = mean(vals)
        committed = ref[m]["overall_exact_match"]
        if abs(round(pooled, 4) - committed) > 1e-9:
            sys.exit(f"KILL comprehend/{m}: pooled EM round4({pooled}) != "
                     f"committed overall {committed}")
        reference_check[m] = {"n": len(vals), "pooled_exact_match": pooled,
                              "committed_overall": committed,
                              "convention": "round(mean, 4)"}

    out: dict = {
        "definition": "for each (base representative, profile, language) cell "
                      "with >= 2 variants present, the across-variant sample "
                      "SD (ddof=1) of the per-item k=8 rate (comprehend "
                      "exact-match / refactor semantic-ok, annotated arm); "
                      "per-model mean SD with a representative-clustered "
                      "bootstrap CI on shared draws",
        "excluded": "discovery_pipeline v0 records (verified byte-identical "
                    "duplicate of base)",
        "caveats": ["agg_stats v2 appends an element: W label confound",
                    "variants perturb one input literal, not a full Tier-A "
                    "re-mint"],
        "ladder_comprehend_reference_check": reference_check,
    }

    # Shared draws over the representative clusters, one draw list reused for
    # every model and both tasks (the rep sets are identical by construction).
    rep_sets = []
    cells_by = {}
    for task in TASKS:
        cells_by[task] = {m: _variant_cells(grades[task][m]["ann8"])
                          for m in ORDER}
        for m in ORDER:
            rep_sets.append({rep for (rep, _p, _l) in cells_by[task][m]})
    if not all(s == rep_sets[0] for s in rep_sets):
        sys.exit("representative sets differ across tasks/models")
    draws = shared_cluster_draws(rep_sets[0], BOOTSTRAP, SUITE_SEED)

    for task in TASKS:
        out[task] = {}
        for m in ORDER:
            cells = cells_by[task][m]
            if not cells:
                sys.exit(f"{task}/{m}: no variant cells")
            sds = {f"{rep}|{prof}|{lang}": G.stdev(vals)
                   for (rep, prof, lang), vals in cells.items()}
            # cluster_scores clusters on key.split("|")[0] = the representative
            clusters = cluster_scores(sds)
            dm = draw_means(clusters, draws)
            n_var = sorted({len(v) for v in cells.values()})
            out[task][m] = {
                "n_cells": len(cells),
                "n_representatives": len(rep_sets[0]),
                "variants_per_cell": n_var,
                "mean_sd": mean(list(sds.values())),
                "mean_sd_ci95": percentile_ci95(dm),
            }
    return out


# --------------------------------------------------------------------------- #
def main() -> int:
    grades = all_grades(use_cache="--no-cache" not in sys.argv)
    report = {
        "_meta": artifact_header(
            "stability_analysis.py",
            ["bench/out/ladder/*.jsonl.gz", "bench/out/g3/refactor_dev__*.jsonl.gz",
             "bench/out/ablation/*.jsonl.gz",
             "bench/out/.annotation_ablation_grades.json",
             "bench/out/ladder_comprehend.json"],
            shared_draws=True),
        "nondeterminism_report": _nondeterminism(grades),
        "drift_diagnostics": _drift(),
        "variant_sensitivity": _variant_sensitivity(grades),
    }
    nd = report["nondeterminism_report"]["pooled_mixed"]
    print(f"pooled mixed: refactor {nd['refactor']['mixed_fraction']:.4f}  "
          f"comprehend {nd['comprehend']['mixed_fraction']:.4f}  "
          f"(published 4.9% / 1.7%)")
    write_artifact(OUT, report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
