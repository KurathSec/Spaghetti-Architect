"""Three estimation-only extras over the committed rep1 campaign. Zero API.

Post-hoc addendum (ANALYSIS_PLAN.md addenda; outside every confirmatory
family), prompted by a projected-review pass on the integrated draft:

(a) PER-LANGUAGE replication refactoring DiD: the same-week k=8
    unannotated-minus-annotated deltas and their weakest-minus-strongest DiD,
    stratified by rendering language (300 items each), base-IR-clustered
    bootstrap per language -- where does the replication differential live?
(b) LYING COST ON DEMONSTRATED COMPETENCE: per model, the lying-minus-
    unannotated score difference restricted to the items that model solves
    with no annotations at all (unannotated k=8 mean grade > 0.5): active
    misdirection of existing competence versus withdrawal of assistance.
(c) PER-LANGUAGE PAIRED PAYLOAD COPY-GAIN on the replication arms: the
    annotated-minus-unannotated payload copy rate (whitespace-normalized
    clean-form substring match, draw 0 of each arm, mirroring
    copy_rate_analysis.py's protocol) stratified by language: does the
    copying channel operate on the non-Python lanes that carry the
    per-language DiD of (a)?

Inputs (all committed; nothing is fetched):
  bench/out/.rep1_grades.json                       campaign offline k=8 grades
  bench/out/rep1_results.json                       kill-switch cross-checks
  bench/out/ablation_v2/refactor__{annotated,unannotated}__<slug>.jsonl.gz
  the dev split re-render for clean-form payloads (via copy_rate_analysis)

Kill-switches (a failure means THIS script is wrong):
  * per-language per-model deltas recompose (equal-weight mean over the five
    languages) to rep1_results.json did.refactor.unannotated.per_model_delta
    within 1e-9;
  * every condition mean recomputed here equals the committed
    rep1_results.json `conditions` block exactly;
  * each replication archive holds exactly 1500 records per model per arm,
    300 per language, and every graded key has a cached k=8 grade;
  * clean-form payload extraction inherits copy_rate_analysis.py's own kills
    (code-part identity across arms; payload count == operation count).
"""

from __future__ import annotations

import json
import os
import sys
from typing import Dict, List

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bench.annotation_ablation import MODELS, ORDER  # noqa: E402
from bench.copy_rate_analysis import (  # noqa: E402
    _frac_contained,
    _ws_norm,
    render_annotations,
)
from bench.suite_common import (  # noqa: E402
    BOOTSTRAP,
    SUITE_SEED,
    artifact_header,
    cluster_scores,
    draw_means,
    mean,
    percentile_ci95,
    read_gz,
    shared_cluster_draws,
    two_sided_bootstrap_p,
    write_artifact,
)
from bench import dataset as D  # noqa: E402
from bench import grade as G  # noqa: E402

GRADES = os.path.join(_HERE, "out", ".rep1_grades.json")
RESULTS = os.path.join(_HERE, "out", "rep1_results.json")
ARCHIVES = os.path.join(_HERE, "out", "ablation_v2")
OUT = os.path.join(_HERE, "out", "rep1_extras2.json")

WEAKEST, STRONGEST = ORDER[0], ORDER[-1]
LANGS = D.LANGS
N_PER_LANG = 300  # 100 dev items x 3 profiles
PASS_THRESHOLD = 0.5  # "solves": k=8 mean grade strictly above


def _lang(key: str) -> str:
    return key.split("|")[2]


def _stat(scores: Dict[str, float]) -> dict:
    """Point + clustered bootstrap CI/p of the mean of an {item_key: value} map."""
    clusters = cluster_scores(scores)
    draws = shared_cluster_draws(set(clusters), BOOTSTRAP, SUITE_SEED)
    dm = draw_means(clusters, draws)
    return {"point": mean(list(scores.values())), "ci95": percentile_ci95(dm),
            "p_bootstrap": two_sided_bootstrap_p(dm), "n_items": len(scores),
            "n_clusters": len(clusters)}


def _did(deltas: Dict[str, Dict[str, float]], a: str, b: str) -> dict:
    """Weakest-minus-strongest DiD on shared base-IR cluster draws
    (same construction as rep1_extras.py)."""
    keys = sorted(set().union(*[set(cluster_scores(d)) for d in
                                deltas.values()]))
    draws = shared_cluster_draws(keys, BOOTSTRAP, SUITE_SEED)
    dm = {m: draw_means(cluster_scores(deltas[m]), draws) for m in deltas}
    dd = [dm[a][t] - dm[b][t] for t in range(BOOTSTRAP)]
    pts = {m: mean(list(deltas[m].values())) for m in deltas}
    return {"point": pts[a] - pts[b], "ci95": percentile_ci95(dd),
            "p_bootstrap": two_sided_bootstrap_p(dd),
            "per_model_delta": {m: pts[m] for m in ORDER}}


def per_language_did(Gc: dict, committed: dict) -> dict:
    un, ann = Gc["refactor"]["unannotated"], Gc["refactor"]["annotated"]
    out: dict = {}
    lang_delta_means: Dict[str, List[float]] = {m: [] for m in ORDER}
    for L in LANGS:
        deltas = {}
        for m in ORDER:
            keys = [k for k in un[m] if _lang(k) == L]
            if len(keys) != N_PER_LANG:
                sys.exit(f"KILL: {m}/{L}: {len(keys)} items != {N_PER_LANG}")
            deltas[m] = {k: un[m][k] - ann[m][k] for k in keys}
            lang_delta_means[m].append(mean(list(deltas[m].values())))
        out[L] = _did(deltas, WEAKEST, STRONGEST)
        out[L]["weakest_means"] = {
            "annotated": mean([ann[WEAKEST][k] for k in deltas[WEAKEST]]),
            "unannotated": mean([un[WEAKEST][k] for k in deltas[WEAKEST]])}
    # KILL: equal-weight recomposition to the committed overall deltas.
    want = committed["did"]["refactor"]["unannotated"]["per_model_delta"]
    for m in ORDER:
        got = mean(lang_delta_means[m])
        if abs(got - want[m]) > 1e-9:
            sys.exit(f"KILL: {m} language-mean recomposition {got!r} != "
                     f"committed {want[m]!r}")
    print(f"KILL passed: per-language deltas recompose to the committed "
          f"per-model deltas (<=1e-9) for all {len(ORDER)} models")
    return out


def lying_on_solved(Gc: dict) -> dict:
    un, lie = Gc["refactor"]["unannotated"], Gc["refactor"]["lying"]
    out = {}
    for m in ORDER:
        solved = [k for k in un[m] if un[m][k] > PASS_THRESHOLD]
        if not solved:
            sys.exit(f"KILL: {m}: no unannotated-solved items")
        out[m] = _stat({k: lie[m][k] - un[m][k] for k in solved})
    return out


def per_language_copy_gain(sp, payloads_by_stem: dict) -> dict:
    stem_of = {(it.sample, it.variant): it.stem for it in sp.items}
    out: dict = {"_protocol": {
        "draw": 0,
        "definition": "paired per-item payload-copy-rate difference "
                      "(annotated - unannotated), replication arms, items "
                      "where both arms yielded extractable code",
        "whitespace_normalization": "strip-all"}}
    for short, slug in MODELS:
        per_arm: Dict[str, Dict[str, float]] = {}
        counts: Dict[str, dict] = {}
        for arm in ("annotated", "unannotated"):
            path = os.path.join(ARCHIVES, f"refactor__{arm}__{slug}.jsonl.gz")
            recs = read_gz(path)
            if len(recs) != 5 * N_PER_LANG:
                sys.exit(f"KILL: {short}/{arm}: {len(recs)} records != "
                         f"{5 * N_PER_LANG}")
            by_lang: Dict[str, int] = {}
            per_item: Dict[str, float] = {}
            n_no_code = 0
            for rec in recs:
                by_lang[rec["language"]] = by_lang.get(rec["language"], 0) + 1
                raw = rec.get("raw_outputs")
                if not raw or raw == ["<mock>"]:
                    sys.exit(f"KILL: {short}/{arm}: record without raw output")
                code = G.extract_code(raw[0])
                if not code:
                    n_no_code += 1
                    continue
                key = "|".join([rec["sample"], rec["profile"],
                                rec["language"], rec.get("variant", "base")])
                stem = stem_of[(rec["sample"], rec.get("variant", "base"))]
                per_item[key] = _frac_contained(
                    [d["payload"] for d in payloads_by_stem[stem]],
                    _ws_norm(code))
            for L in LANGS:
                if by_lang.get(L) != N_PER_LANG:
                    sys.exit(f"KILL: {short}/{arm}/{L}: {by_lang.get(L)} "
                             f"records != {N_PER_LANG}")
            per_arm[arm] = per_item
            counts[arm] = {"n_no_code": n_no_code, "n_scored": len(per_item)}
        common = sorted(set(per_arm["annotated"]) & set(per_arm["unannotated"]))
        gaps = {k: per_arm["annotated"][k] - per_arm["unannotated"][k]
                for k in common}
        out[short] = {"counts": counts,
                      "overall": _stat(gaps),
                      "by_language": {
                          L: _stat({k: v for k, v in gaps.items()
                                    if _lang(k) == L}) for L in LANGS}}
    return out


def main() -> int:
    Gc = json.load(open(GRADES))
    committed = json.load(open(RESULTS))

    # KILL: recomputed condition means equal rep1_results.json exactly.
    n = 0
    for task in ("refactor", "comprehend"):
        for cond, per in committed["conditions"][task].items():
            for m, _slug in MODELS:
                got = mean(list(Gc[task][cond][m].values()))
                want = per[m]["score"]["mean"]
                assert got == want, (
                    f"KILL: {task}/{cond}/{m} mean {got!r} != {want!r}")
                n += 1
    print(f"KILL passed: {n} condition means equal rep1_results.json exactly")

    sp = D.load("dev")
    if len(sp.items) != 100:
        sys.exit(f"dev split has {len(sp.items)} items, expected 100")
    payloads_by_stem, ann_summary = render_annotations(sp)
    print(f"payload extraction: {ann_summary['code_part_identity']}")

    report = {
        "per_language_replication_did": per_language_did(Gc, committed),
        "lying_on_unannotated_solved": {
            "pass_threshold": PASS_THRESHOLD,
            "per_model": lying_on_solved(Gc)},
        "per_language_copy_gain_replication": per_language_copy_gain(
            sp, payloads_by_stem),
    }

    for L in LANGS:
        d = report["per_language_replication_did"][L]
        print(f"DiD {L:10s} {d['point']:+.4f} [{d['ci95'][0]:+.3f},"
              f"{d['ci95'][1]:+.3f}] p={d['p_bootstrap']}")
    for m in ORDER:
        s = report["lying_on_unannotated_solved"]["per_model"][m]
        print(f"lying-on-solved {m:18s} {s['point']:+.4f} "
              f"[{s['ci95'][0]:+.3f},{s['ci95'][1]:+.3f}] n={s['n_items']}")
    for m in ORDER:
        row = report["per_language_copy_gain_replication"][m]["by_language"]
        print(f"copy-gain {m:18s} " + "  ".join(
            f"{L}:{row[L]['point']:+.3f}[{row[L]['ci95'][0]:+.3f},"
            f"{row[L]['ci95'][1]:+.3f}]" for L in LANGS))

    report["_meta"] = artifact_header(
        "rep1_extras2.py",
        ["bench/out/.rep1_grades.json", "bench/out/rep1_results.json",
         "bench/out/ablation_v2/refactor__{annotated,unannotated}__*.jsonl.gz",
         "config/anti_patterns_db.json"],
        shared_draws=True,
        estimation_not_family=True,
        post_hoc=("registered 2026-08-15 after the projected-review pass on "
                  "the integrated draft; estimation only "
                  "(ANALYSIS_PLAN.md addenda)"),
    )
    write_artifact(OUT, report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
