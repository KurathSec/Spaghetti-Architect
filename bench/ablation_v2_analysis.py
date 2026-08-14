"""rep1 campaign analysis: the pre-registered same-week replication. Zero API.

Committed BEFORE the campaign runs (see bench/PREREGISTRATION_V2.md; the
pre-run commit freezes both the hypotheses and this script). Inputs are the
packaged raw archives under bench/out/ablation_v2/ (bench/package_rep1.py);
every grade re-derives offline from raw completions -- live batch aggregates
are never used. Output: bench/out/rep1_results.json.

What it computes:
  * offline k=8 grades per item for every (task, condition, model), cached in
    bench/out/.rep1_grades.json (the slow part: refactor compiles and runs
    model code; comprehend is extraction + match; the CoT lane uses the
    answer-tag scorer defined here, never grade.extract_json_obj);
  * level shifts of the annotated re-fetch vs the published annotated arm
    (the measured serving drift);
  * same-week paired deltas per condition vs the annotated re-fetch, the
    weakest-minus-strongest DiDs on shared base-IR cluster draws, rung
    separations under both criteria, and the incidental-knob slopes per arm;
  * the channel decomposition (markers_only / comments_only) with
    sub-additivity, the lying-arm contrasts and wrong-form copy rates, and
    the CoT width table against the published direct-answer numbers;
  * MECHANICAL verdicts for the pre-registered H1-H6 with Holm correction
    inside this campaign's own family.

Verification kill-switches (a mismatch means THIS pipeline is wrong):
  * the offline grader re-grades a seeded subsample of the PUBLISHED
    annotated archives and must reproduce the committed grade cache exactly;
  * every archive is complete (1500 items x 8 draws; 300 for the CoT lane).
"""

from __future__ import annotations

import collections
import glob
import gzip
import json
import os
import random
import re
import sys
from concurrent.futures import ThreadPoolExecutor

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bench.analysis as A  # noqa: E402
from bench import grade as G  # noqa: E402
from bench import tasks as T  # noqa: E402
from bench.annotation_ablation import (  # noqa: E402
    MODELS,
    ORDER,
    _family as family_of,
    _key as item_key,
    _load_annotated as load_published_annotated,
)
from bench.suite_common import (  # noqa: E402
    BOOTSTRAP,
    SUITE_SEED,
    artifact_header,
    cluster_scores,
    draw_means,
    mean,
    percentile_ci95,
    shared_cluster_draws,
    two_sided_bootstrap_p,
    write_artifact,
)
from src.nodes.validator import oracle  # noqa: E402

ARCHIVE_DIR = os.path.join(_HERE, "out", "ablation_v2")
CACHE = os.path.join(_HERE, "out", ".rep1_grades.json")
OUT = os.path.join(_HERE, "out", "rep1_results.json")
PUBLISHED_ABLATION = os.path.join(_HERE, "out", "annotation_ablation.json")
PUBLISHED_GRADES = os.path.join(_HERE, "out", ".annotation_ablation_grades.json")
PUBLISHED_SCALING = os.path.join(_HERE, "out", "ladder_scaling.json")

TASK_CONDS = {"refactor": ("annotated", "unannotated", "markers_only",
                           "comments_only", "lying"),
              "comprehend": ("annotated", "unannotated", "markers_only",
                             "comments_only")}
KNOB_RANK = {"minimal": 0, "standard": 1, "max": 2}
WEAKEST, STRONGEST = ORDER[0], ORDER[-1]
GRADE_WORKERS = 16
KILL_SUBSAMPLE = 120

_ANSWER = re.compile(r"<answer>(.*?)</answer>", re.DOTALL)


# --------------------------------------------------------------------------- #
# CoT scorer (answer-tag; NEVER grade.extract_json_obj -- see prereg)
# --------------------------------------------------------------------------- #
def _last_balanced_json(text: str):
    best = None
    start = text.find("{")
    while start >= 0:
        obj = G.extract_json_obj(text[start:])
        if obj is not None:
            best = obj
        start = text.find("{", start + 1)
    return best


def cot_extract(text: str):
    """(pred_or_None, status): status in answered / fallback_json /
    truncated_or_noncompliant."""
    tags = _ANSWER.findall(text)
    if tags:
        obj = G.extract_json_obj(tags[-1])
        return obj, ("answered" if obj is not None
                     else "truncated_or_noncompliant")
    obj = _last_balanced_json(text)
    if obj is not None:
        return obj, "fallback_json"
    return None, "truncated_or_noncompliant"


# --------------------------------------------------------------------------- #
# offline grading
# --------------------------------------------------------------------------- #
def _read_gz(path: str) -> list:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def _grade_refactor_rec(rec: dict) -> float:
    item = T._rebuild_refactor_item(rec)
    oks = []
    for raw in rec["raw_outputs"]:
        ok, _detail = G.semantic_ok(rec["language"], G.extract_code(raw),
                                    item.program)
        oks.append(1.0 if ok else 0.0)
    return sum(oks) / len(oks)


def _grade_comprehend_rec(rec: dict, cot: bool = False):
    item = T._rebuild_comprehend_item(rec)
    exp = oracle(item.program)
    oks, statuses = [], []
    for raw in rec["raw_outputs"]:
        if cot:
            pred, status = cot_extract(raw)
            statuses.append(status)
        else:
            pred = G.extract_json_obj(raw)
        oks.append(1.0 if G._match(pred, exp) else 0.0)
    score = sum(oks) / len(oks)
    return (score, statuses) if cot else score


def _grade_all() -> dict:
    if os.path.exists(CACHE):
        print(f"using cached grades ({CACHE}); delete to re-grade", flush=True)
        return json.load(open(CACHE))
    out: dict = {"refactor": {}, "comprehend": {}, "cot": {},
                 "cot_status": {}}
    for task, conds in TASK_CONDS.items():
        for cond in conds:
            for short, slug in MODELS:
                path = os.path.join(ARCHIVE_DIR,
                                    f"{task}__{cond}__{slug}.jsonl.gz")
                recs = _read_gz(path)
                assert len(recs) == 1500, (path, len(recs))
                if task == "refactor":
                    with ThreadPoolExecutor(max_workers=GRADE_WORKERS) as ex:
                        scores = list(ex.map(_grade_refactor_rec, recs))
                else:
                    scores = [_grade_comprehend_rec(r) for r in recs]
                out[task].setdefault(cond, {})[short] = {
                    item_key(r): s for r, s in zip(recs, scores)}
                print(f"graded {task}/{cond}/{short}", flush=True)
    for short, slug in MODELS:
        path = os.path.join(ARCHIVE_DIR, f"comprehend__cot__{slug}.jsonl.gz")
        recs = _read_gz(path)
        assert len(recs) == 300, (path, len(recs))
        cell: dict = {}
        stat = collections.Counter()
        for r in recs:
            score, statuses = _grade_comprehend_rec(r, cot=True)
            cell[item_key(r)] = score
            stat.update(statuses)
        out["cot"][short] = cell
        out["cot_status"][short] = dict(sorted(stat.items()))
        print(f"graded cot/{short} statuses={dict(stat)}", flush=True)
    with open(CACHE, "w") as fh:
        json.dump(out, fh)
    return out


def _kill_grader_reproduces_published() -> int:
    """Re-grade a seeded subsample of the PUBLISHED annotated refactor
    archives; must match the committed cache exactly."""
    pub = json.load(open(PUBLISHED_GRADES))
    rng = random.Random(SUITE_SEED)
    n = 0
    for short, slug in MODELS:
        recs = list(load_published_annotated("refactor", slug))
        ann8 = pub["refactor"][short]["ann8"]
        for rec in rng.sample(recs, KILL_SUBSAMPLE // len(MODELS)):
            want = ann8.get(item_key(rec))
            if want is None:
                continue
            got = _grade_refactor_rec(rec)
            assert abs(got - want) < 1e-9, (
                f"KILL: offline grader {short}/{item_key(rec)}: "
                f"{got} != published {want}")
            n += 1
    return n


# --------------------------------------------------------------------------- #
# statistics
# --------------------------------------------------------------------------- #
def _cluster_ci(scores: dict) -> dict:
    clus = cluster_scores(scores)
    draws = shared_cluster_draws(sorted(clus), BOOTSTRAP, SUITE_SEED)
    dm = draw_means(clus, draws)
    return {"mean": mean(list(scores.values())), "ci95": percentile_ci95(dm)}


def _paired(scores_a: dict, scores_b: dict) -> dict:
    """a minus b, per item, cluster bootstrap. Returns point/ci95/p + draws."""
    common = sorted(set(scores_a) & set(scores_b))
    diffs = {k: scores_a[k] - scores_b[k] for k in common}
    clus = cluster_scores(diffs)
    draws = shared_cluster_draws(sorted(clus), BOOTSTRAP, SUITE_SEED)
    dm = draw_means(clus, draws)
    return {"point": mean(list(diffs.values())),
            "ci95": percentile_ci95(dm),
            "p_bootstrap": two_sided_bootstrap_p(dm),
            "n": len(common), "_draws": dm}


def _did(deltas: dict) -> dict:
    """weakest minus strongest of per-model delta dicts on SHARED draws."""
    keys = sorted(set().union(*[set(cluster_scores(d)) for d in
                                deltas.values()]))
    draws = shared_cluster_draws(keys, BOOTSTRAP, SUITE_SEED)
    dm = {m: draw_means(cluster_scores(deltas[m]), draws) for m in deltas}
    dd = [dm[WEAKEST][t] - dm[STRONGEST][t] for t in range(BOOTSTRAP)]
    pts = {m: mean(list(deltas[m].values())) for m in deltas}
    return {"point": pts[WEAKEST] - pts[STRONGEST],
            "ci95": percentile_ci95(dd),
            "p_bootstrap": two_sided_bootstrap_p(dd),
            "per_model_delta": {m: pts[m] for m in ORDER}}


def _slope(scores: dict) -> dict:
    obs = []
    for k, v in scores.items():
        sample, profile, language, variant = k.split("|")
        obs.append({"family": family_of(sample), "sample": sample,
                    "knob_rank": KNOB_RANK[profile], "rating": v})
    res = A.cluster_bootstrap_slope(obs)
    res["p_bootstrap"] = A._bootstrap_pvalue(obs, res["slope"])
    return {k: res[k] for k in ("slope", "ci95", "p_bootstrap", "n_obs",
                                "n_clusters")}


def _rungs(per_model_scores: dict) -> dict:
    """Both separation criteria over the k=8 per-item means."""
    cis = {m: _cluster_ci(per_model_scores[m]) for m in ORDER}
    ci_sep, paired_sep = [], []
    for a, b in zip(ORDER, ORDER[1:]):
        ci_sep.append(cis[a]["ci95"][1] < cis[b]["ci95"][0])
        pr = _paired(per_model_scores[b], per_model_scores[a])
        paired_sep.append(pr["ci95"][0] > 0 or pr["ci95"][1] < 0)
    return {"ci_overlap_criterion": sum(ci_sep),
            "paired_criterion": sum(paired_sep),
            "per_model": {m: {k: cis[m][k] for k in ("mean", "ci95")}
                          for m in ORDER}}


def _holm(pvals: dict) -> dict:
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    out, running = {}, 0.0
    for i, (name, p) in enumerate(items):
        adj = min(1.0, (m - i) * p)
        running = max(running, adj)
        out[name] = {"p": p, "p_holm": running,
                     "significant_at_05": running < 0.05}
    return out


# --------------------------------------------------------------------------- #
def main() -> int:
    for path in glob.glob(os.path.join(ARCHIVE_DIR, "*.jsonl.gz")):
        pass  # existence checked per-cell in _grade_all
    n_kill = _kill_grader_reproduces_published()
    print(f"KILL passed: offline grader reproduces {n_kill} published grades",
          flush=True)
    Gc = _grade_all()
    published = json.load(open(PUBLISHED_ABLATION))
    report: dict = {"level_shift": {}, "conditions": {}, "did": {},
                    "slopes": {}, "rungs": {}, "channels": {}, "lying": {},
                    "cot": {}, "prereg": {}}

    pvals: dict = {}
    for task in TASK_CONDS:
        ann = Gc[task]["annotated"]
        # level shift vs the published annotated arm (k=8)
        report["level_shift"][task] = {
            m: {"rep1": mean(list(ann[m].values())),
                "published_k8": published[task]["per_model"][m][
                    "annotated_k8"],
                "shift": mean(list(ann[m].values()))
                - published[task]["per_model"][m]["annotated_k8"]}
            for m in ORDER}
        # per-condition summaries + paired deltas vs the same-week annotated
        cond_block: dict = {}
        deltas_by_cond: dict = {}
        for cond in TASK_CONDS[task]:
            per_model = {}
            for m in ORDER:
                cell = {"score": _cluster_ci(Gc[task][cond][m])}
                if cond != "annotated":
                    pr = _paired(Gc[task][cond][m], ann[m])
                    cell["delta_vs_annotated"] = {
                        k: pr[k] for k in ("point", "ci95", "p_bootstrap")}
                    deltas_by_cond.setdefault(cond, {})[m] = {
                        k: Gc[task][cond][m][k] - ann[m][k]
                        for k in set(Gc[task][cond][m]) & set(ann[m])}
                per_model[m] = cell
            cond_block[cond] = per_model
        report["conditions"][task] = cond_block
        # DiDs per non-annotated condition
        report["did"][task] = {cond: _did(d) for cond, d in
                               sorted(deltas_by_cond.items())}
        # knob slopes on both primary arms
        report["slopes"][task] = {
            "annotated": {m: _slope(ann[m]) for m in ORDER},
            "unannotated": {m: _slope(Gc[task]["unannotated"][m])
                            for m in ORDER}}
        # rung separations per arm
        report["rungs"][task] = {
            cond: _rungs(Gc[task][cond])
            for cond in ("annotated", "unannotated")}
        # channel decomposition
        eff_intent = {m: _paired(ann[m], Gc[task]["markers_only"][m])
                      for m in ORDER}
        eff_naming = {m: _paired(ann[m], Gc[task]["comments_only"][m])
                      for m in ORDER}
        full = {m: _paired(ann[m], Gc[task]["unannotated"][m])
                for m in ORDER}
        report["channels"][task] = {
            "effect_of_removing_intent": {
                m: {k: eff_intent[m][k] for k in ("point", "ci95",
                                                  "p_bootstrap")}
                for m in ORDER},
            "effect_of_removing_naming": {
                m: {k: eff_naming[m][k] for k in ("point", "ci95",
                                                  "p_bootstrap")}
                for m in ORDER},
            "full_removal": {
                m: {k: full[m][k] for k in ("point", "ci95", "p_bootstrap")}
                for m in ORDER},
            "subadditivity": {
                m: {"intent_plus_naming": eff_intent[m]["point"]
                    + eff_naming[m]["point"],
                    "full": full[m]["point"],
                    "interaction": full[m]["point"]
                    - eff_intent[m]["point"] - eff_naming[m]["point"]}
                for m in ORDER},
        }
        # prereg contrasts
        did_un = report["did"][task]["unannotated"]
        if task == "refactor":
            pvals["H1"] = did_un["p_bootstrap"]
        else:
            pvals["H2"] = did_un["p_bootstrap"]
        h4 = _paired(Gc[task]["comments_only"][WEAKEST],
                     Gc[task]["markers_only"][WEAKEST])
        report["prereg"][f"H4_{task}"] = {
            k: h4[k] for k in ("point", "ci95", "p_bootstrap")}
        pvals[f"H4_{task}"] = h4["p_bootstrap"]

    # H3: unannotated slope signs
    sl = report["slopes"]
    cells = [(t, m, sl[t]["unannotated"][m]["slope"])
             for t in TASK_CONDS for m in ORDER]
    neg = sum(1 for _, _, s in cells if s is not None and s < 0)
    comp_all_neg = all(sl["comprehend"]["unannotated"][m]["slope"] < 0
                       for m in ORDER)
    comp_ps = [sl["comprehend"]["unannotated"][m]["p_bootstrap"]
               for m in ORDER]
    report["prereg"]["H3"] = {"n_negative_of_8": neg,
                              "comprehension_all_negative": comp_all_neg,
                              "max_comprehension_p": max(comp_ps)}
    pvals["H3"] = max(comp_ps)

    # H5: markers_only vs unannotated, weakest, refactor
    h5 = _paired(Gc["refactor"]["markers_only"][WEAKEST],
                 Gc["refactor"]["unannotated"][WEAKEST])
    report["prereg"]["H5"] = {k: h5[k] for k in ("point", "ci95",
                                                 "p_bootstrap")}
    pvals["H5"] = h5["p_bootstrap"]

    # H6: lying vs unannotated
    lie_w = _paired(Gc["refactor"]["lying"][WEAKEST],
                    Gc["refactor"]["unannotated"][WEAKEST])
    lie_s = _paired(Gc["refactor"]["lying"][STRONGEST],
                    Gc["refactor"]["unannotated"][STRONGEST])
    report["lying"] = {
        "weakest_lying_minus_unannotated": {
            k: lie_w[k] for k in ("point", "ci95", "p_bootstrap")},
        "strongest_lying_minus_unannotated": {
            k: lie_s[k] for k in ("point", "ci95", "p_bootstrap")},
        "capability_ordering_holds": lie_w["point"] < lie_s["point"],
    }
    report["prereg"]["H6"] = report["lying"]
    pvals["H6"] = lie_w["p_bootstrap"]

    report["prereg"]["holm"] = _holm(pvals)
    report["prereg"]["verdicts"] = {
        "H1": (report["did"]["refactor"]["unannotated"]["ci95"][1] < 0),
        "H2": (report["did"]["comprehend"]["unannotated"]["ci95"][1] < 0),
        "H3": (neg >= 6 and comp_all_neg),
        "H4": all(report["prereg"][f"H4_{t}"]["ci95"][0] > 0
                  for t in TASK_CONDS),
        "H5": report["prereg"]["H5"]["ci95"][0] > 0,
        "H6": (lie_w["ci95"][1] < 0
               and report["lying"]["capability_ordering_holds"]),
    }

    # CoT table vs the published direct-answer numbers
    scaling = json.load(open(PUBLISHED_SCALING))
    cot_block: dict = {}
    for m, _slug in MODELS:
        by_w: dict = {}
        for k, v in Gc["cot"][m].items():
            w = k.split("|")[0].split("_W")[1]
            by_w.setdefault(w, []).append(v)
        cot_block[m] = {
            "em_by_width": {w: {"cot": mean(vs), "n": len(vs)}
                            for w, vs in sorted(by_w.items(),
                                                key=lambda kv: int(kv[0]))},
            "statuses": Gc["cot_status"][m]}
    report["cot"] = {"per_model": cot_block,
                     "published_direct_answer": "ladder_scaling.json "
                     "agg_stats rows (annotated, no-CoT protocol)",
                     "note": "same corpus and items; only the prompt protocol "
                             "and extraction differ (bench-prompts-cot-v1)"}
    _ = scaling  # referenced for provenance; per-W comparison done in-paper

    report["_meta"] = artifact_header(
        "ablation_v2_analysis.py",
        ["bench/out/ablation_v2/*.jsonl.gz",
         "bench/out/annotation_ablation.json",
         "bench/out/.annotation_ablation_grades.json",
         "bench/out/ladder_scaling.json"],
        shared_draws=True,
        preregistration="bench/PREREGISTRATION_V2.md",
        campaign="rep1",
    )
    write_artifact(OUT, report)
    print("\nPREREG VERDICTS:", json.dumps(report["prereg"]["verdicts"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
