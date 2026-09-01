"""Identity-echo shares and conditional-on-pass quality for the annotation ablation. Zero API.

The ablation's headline is a difference-in-differences on the semantic-equivalence
GATE, and an identity rewrite satisfies that gate. This script measures, per
refactor completion (draw[0] of each stored record, both arms):

  (i)   identity echo -- the completion's code equals the item's own input render,
        byte-identical after comment stripping and whitespace removal (the same
        normalization behind the copy-rate analysis's cross-arm render kill);
  (ii)  the gate DiD recomputed with echoes removed from the passes ("genuine
        pass" = gate AND not echo), paired per item, extremes DiD on shared
        cluster draws;
  (iii) simplification quality CONDITIONAL on passing the gate, per model x arm,
        on three lanes: the headline lane (AST/radon for Python, uniform for the
        other four languages, exactly as ``grade_refactor_one`` composes it), the
        Python AST lane alone, and the cross-language uniform lane alone
        (``None`` records excluded and counted); each lane gets the per-model
        unannotated-minus-annotated difference and the extremes DiD.

Post-hoc addendum (registered in ANALYSIS_PLAN.md after the suite's results were
read): estimation with intervals only, no new confirmatory family members.

Run under the metrics venv (``~/.venvs/spaghetti-metrics/bin/python``): the
uniform lane needs lizard, and the published non-Python quality was graded on
that lane. The script exits if lizard is absent.

Inputs (all committed; nothing is fetched):
  bench/out/g3/refactor_dev__<slug>.jsonl.gz            annotated arm (draw[0] of k=8)
  bench/out/ablation/refactor_unannotated__<slug>.jsonl.gz  unannotated arm (k=1)
  bench/out/.annotation_ablation_grades.json            cached k=1 gates
  bench/out/annotation_ablation.json                    committed per-model means
  bench/out/ablation_did.json                           committed extremes DiD
  config/anti_patterns_db.json + the minted dev renders (both arms, via Engine)

Kill-switches (frozen facts; a failure means THIS script is wrong):
  * each arm's archive holds exactly 1500 dev records per model, every record
    with a cached k=1 grade;
  * per-model cache means equal annotation_ablation.json annotated_k1 /
    unannotated_k1 exactly, and the recomputed extremes gate DiD equals
    ablation_did.json refactor.did.point to 1e-9;
  * for every (item, profile, language) cell the annotated and unannotated
    renders are byte-identical after comment stripping;
  * every detected echo carries a cached PASSING grade (an echo of an
    oracle-validated render cannot fail the oracle);
  * a seeded subsample of completions re-executes to exactly its cached gate.

Output: bench/out/echo_quality.json (deterministic; re-runs are byte-identical).
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

from bench.suite_common import (  # noqa: E402
    BOOTSTRAP,
    MODELS,
    ORDER,
    STRONGEST,
    SUITE_SEED,
    WEAKEST,
    all_grades,
    artifact_header,
    cluster_scores,
    draw_means,
    item_key,
    load_annotated,
    load_unannotated,
    mean,
    percentile_ci95,
    shared_cluster_draws,
    two_sided_bootstrap_p,
    write_artifact,
)
from bench import dataset as D  # noqa: E402
from bench import grade as G  # noqa: E402
from bench import uniform_lane as U  # noqa: E402
from bench.copy_rate_analysis import _code_only, _ws_norm  # noqa: E402
from eval import metrics as M  # noqa: E402
from src.engine import Engine  # noqa: E402

OUT = os.path.join(_HERE, "out", "echo_quality.json")
COMMITTED = os.path.join(_HERE, "out", "annotation_ablation.json")
DID = os.path.join(_HERE, "out", "ablation_did.json")

PROFILES = ["minimal", "standard", "max"]
LANGS = D.LANGS
DB = D.DB
N_DEV_RECORDS = 1500
N_REEXEC = 10  # seeded re-execution subsample per model x arm
ARMS = ("annotated", "unannotated")


# --------------------------------------------------------------------------- #
# renders: both arms via Engine, comment-strip identity kill, echo targets
# --------------------------------------------------------------------------- #
def render_arms(sp) -> Tuple[Dict[Tuple[str, str, str], str],
                             Dict[Tuple[str, str, str, str], str]]:
    """Render every (dev item, profile) with Engine(annotate=True/False).

    Returns (echo targets, per-arm sources): ``targets[(stem, profile, lang)]``
    is the whitespace-normalized comment-stripped render (arm-invariant by the
    kill), ``sources[(stem, profile, lang, arm)]`` the raw render each arm's
    prompt interpolated (the quality facets read it)."""
    engines = {(p, ann): Engine(DB, p, annotate=ann)
               for p in PROFILES for ann in (True, False)}
    targets: Dict[Tuple[str, str, str], str] = {}
    sources: Dict[Tuple[str, str, str, str], str] = {}
    for it in sp.items:
        ir = sp.ir(it.stem)
        for profile in PROFILES:
            src_a = engines[(profile, True)].generate(ir)["sources"]
            src_u = engines[(profile, False)].generate(ir)["sources"]
            for lang in LANGS:
                a_code = _code_only(src_a[lang], lang)
                u_code = _code_only(src_u[lang], lang)
                if a_code != u_code:  # KILL: arms differ only in comments
                    sys.exit(f"code-part mismatch: {it.stem}/{profile}/{lang}")
                targets[(it.stem, profile, lang)] = _ws_norm(u_code)
                sources[(it.stem, profile, lang, "annotated")] = src_a[lang]
                sources[(it.stem, profile, lang, "unannotated")] = src_u[lang]
    return targets, sources


# --------------------------------------------------------------------------- #
# quality lanes (static; the gate comes from the cached k=1 grades)
# --------------------------------------------------------------------------- #
def headline_geo(language: str, model_src: str, spaghetti_src: str, program) -> float:
    """The geometric recovery behind ``simplification_quality``, WITHOUT the gate
    factor (the caller conditions on the cached gate): the exact composition in
    ``grade_refactor_one`` -- facet triples, per-facet clamped recovery, geomean
    of the non-negative parts, 0.0 when no facet has a usable gap."""
    triples = G.refactor_facets(language, model_src, spaghetti_src, program)
    rec = {f: G._recovery(s, m, c) for f, (s, m, c) in triples.items()
           if None not in (s, m, c)}
    positives = [max(0.0, v) for v in rec.values() if v is not None]
    return M.geomean(positives) if positives else 0.0


def _ratio_draws(num: Dict[str, float], den: Dict[str, float],
                 draws) -> List[float]:
    """Per shared draw: sum(numerator)/sum(denominator) over the resampled
    clusters. Raises on an all-zero denominator draw (a bug, not a statistic)."""
    out = []
    for d in draws:
        n = sum(num.get(c, 0.0) for c in d)
        m = sum(den.get(c, 0.0) for c in d)
        if m == 0:
            raise ValueError("empty conditional draw: no passes in resample")
        out.append(n / m)
    return out


def _cluster_sums(per_item: Dict[str, float]) -> Dict[str, float]:
    return {c: sum(vs) for c, vs in cluster_scores(per_item).items()}


def _ci_block(point: float, dm: List[float], n: int) -> dict:
    return {"point": point, "ci95": percentile_ci95(dm),
            "p_bootstrap": two_sided_bootstrap_p(dm), "n": n}


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> int:
    try:
        import lizard  # noqa: F401
    except ImportError:
        sys.exit("lizard is not importable: run under the metrics venv "
                 "(~/.venvs/spaghetti-metrics/bin/python), as for uniform_regrade")

    sp = D.load("dev")
    if len(sp.items) != 100:
        sys.exit(f"dev split has {len(sp.items)} items, expected 100")
    stem_of = {(it.sample, it.variant): it.stem for it in sp.items}
    programs = {it.stem: sp.program(it.stem) for it in sp.items}
    targets, sources = render_arms(sp)
    print(f"renders: {len(targets)} cells, code parts arm-identical", flush=True)

    grades = all_grades(use_cache="--no-cache" not in sys.argv)["refactor"]
    committed = json.load(open(COMMITTED))["refactor"]["per_model"]
    did_committed = json.load(open(DID))["refactor"]["did"]

    per_model: Dict[str, dict] = {}
    genuine_delta: Dict[str, Dict[str, float]] = {}   # item -> un-ann genuine diff
    cond_sums: Dict[Tuple[str, str, str], Dict[str, float]] = {}
    cond_cnts: Dict[Tuple[str, str, str], Dict[str, float]] = {}
    points: Dict[Tuple[str, str, str], Tuple[float, int]] = {}
    reexec_log = []

    for short, slug in MODELS:
        arms = {"annotated": load_annotated("refactor", slug),
                "unannotated": load_unannotated("refactor", slug)}
        model_out: Dict[str, dict] = {}
        per_arm_items: Dict[str, dict] = {}
        for arm in ARMS:
            recs = arms[arm]
            if len(recs) != N_DEV_RECORDS:
                sys.exit(f"{short}/{arm}: {len(recs)} records != {N_DEV_RECORDS}")
            gmap = grades[short]["ann1" if arm == "annotated" else "un1"]
            if any(item_key(r) not in gmap for r in recs):
                sys.exit(f"{short}/{arm}: record without a cached k=1 grade")
            # KILL: cache means must equal the committed artifact exactly.
            want = committed[short]["annotated_k1" if arm == "annotated"
                                    else "unannotated_k1"]
            got = mean([gmap[item_key(r)] for r in recs])
            assert abs(got - want) < 1e-12, \
                f"KILL {short}/{arm}: cache mean {got!r} != committed {want!r}"

            info: Dict[str, dict] = {}
            n_no_code = 0
            for rec in recs:
                key = item_key(rec)
                stem = stem_of[(rec["sample"], rec.get("variant", "base"))]
                lang = rec["language"]
                sok = 1 if gmap[key] >= 0.5 else 0
                code = G.extract_code(rec["raw_outputs"][0])
                echo = 0
                q_head = q_ast = q_uni = None
                if not code:
                    n_no_code += 1
                else:
                    cell = (stem, rec["profile"], lang)
                    if _ws_norm(_code_only(code, lang)) == targets[cell]:
                        echo = 1
                        if sok != 1:  # KILL: an echo cannot fail the oracle
                            sys.exit(f"KILL {short}/{arm}/{key}: echo graded 0")
                    if sok:
                        spag = sources[(stem, rec["profile"], lang, arm)]
                        q_head = headline_geo(lang, code, spag, programs[stem])
                        if lang == "python":
                            q_ast = q_head
                        q_uni = U.quality(lang, code, spag,
                                          M.clean_baseline_static(programs[stem]))
                info[key] = {"sok": sok, "echo": echo, "code": code,
                             "q_head": q_head, "q_ast": q_ast, "q_uni": q_uni,
                             "lang": lang, "stem": stem,
                             "profile": rec["profile"]}
            per_arm_items[arm] = info

            n_pass = sum(v["sok"] for v in info.values())
            n_echo = sum(v["echo"] for v in info.values())
            n_echo_pass = sum(v["echo"] for v in info.values() if v["sok"])
            passes = [v for v in info.values() if v["sok"]]
            no_change = sum(1 for v in passes if v["q_head"] is not None
                            and v["q_head"] <= 1e-9)
            lanes = {}
            for lane, field in (("headline", "q_head"), ("ast_python", "q_ast"),
                                ("uniform", "q_uni")):
                vals = {k: v[field] for k, v in info.items()
                        if v["sok"] and v[field] is not None}
                n_excl = n_pass - len(vals)
                pt = mean(list(vals.values())) if vals else None
                cond_sums[(short, arm, lane)] = _cluster_sums(vals)
                cond_cnts[(short, arm, lane)] = _cluster_sums(
                    {k: 1.0 for k in vals})
                points[(short, arm, lane)] = (pt, len(vals))
                lanes[lane] = {"mean_given_pass": pt, "n_pass_scored": len(vals),
                               "n_pass_excluded": n_excl}
            model_out[arm] = {
                "n_records": len(recs), "n_no_code": n_no_code,
                "gate_pass": {"n": n_pass, "rate": n_pass / len(recs)},
                "identity_echo": {
                    "n": n_echo, "rate_all": n_echo / len(recs),
                    "n_among_passes": n_echo_pass,
                    "rate_among_passes": n_echo_pass / n_pass if n_pass else None},
                "no_measurable_change_among_passes": {
                    "n": no_change,
                    "rate": no_change / n_pass if n_pass else None},
                "quality_given_pass": lanes,
            }

            # seeded re-execution subsample (KILL: recomputed gate == cache)
            rng = random.Random(SUITE_SEED + (ORDER.index(short) * 2
                                              + ARMS.index(arm)))
            candidates = sorted(k for k, v in info.items() if v["code"])
            for key in rng.sample(candidates, N_REEXEC):
                v = info[key]
                ok, _detail = G.semantic_ok(v["lang"], v["code"],
                                            programs[v["stem"]])
                if ok is None:
                    sys.exit(f"KILL {short}/{arm}/{key}: re-execution SKIPped")
                if int(ok) != v["sok"]:
                    sys.exit(f"KILL {short}/{arm}/{key}: re-executed gate "
                             f"{int(ok)} != cached {v['sok']}")
                reexec_log.append(f"{short}|{arm}|{key}")

        # paired per-item genuine-pass delta (unannotated - annotated)
        a, u = per_arm_items["annotated"], per_arm_items["unannotated"]
        common = sorted(set(a) & set(u))
        if len(common) != N_DEV_RECORDS:
            sys.exit(f"{short}: {len(common)} common items != {N_DEV_RECORDS}")
        genuine_delta[short] = {
            k: (u[k]["sok"] * (1 - u[k]["echo"]))
            - (a[k]["sok"] * (1 - a[k]["echo"])) for k in common}
        per_model[short] = model_out
        print(f"{short:18s} echo-among-passes "
              f"ann {model_out['annotated']['identity_echo']['rate_among_passes']:.3f} "
              f"unann {model_out['unannotated']['identity_echo']['rate_among_passes']:.3f}",
              flush=True)

    # KILL: the recomputed gate DiD must equal the committed extremes DiD.
    gate_delta = {m: committed[m]["unannotated_k1"] - committed[m]["annotated_k1"]
                  for m in ORDER}
    gate_did = gate_delta[WEAKEST] - gate_delta[STRONGEST]
    assert abs(gate_did - did_committed["point"]) < 1e-9, \
        f"KILL: recomputed gate DiD {gate_did!r} != committed {did_committed['point']!r}"

    # shared draws over the 50 base-IR clusters
    clusters_by_model = {m: cluster_scores(genuine_delta[m]) for m in ORDER}
    cluster_keys = set(clusters_by_model[ORDER[0]])
    for m in ORDER:
        if set(clusters_by_model[m]) != cluster_keys:
            sys.exit(f"cluster space differs for {m}")
    draws = shared_cluster_draws(cluster_keys, BOOTSTRAP, SUITE_SEED)

    # (ii) genuine-pass DiD
    gdm = {m: draw_means(clusters_by_model[m], draws) for m in ORDER}
    genuine = {
        "definition": "outcome = gate AND not identity echo; delta = unannotated"
                      " - annotated, paired per item; DiD = delta(weakest) -"
                      " delta(strongest)",
        "per_model_delta": {
            m: _ci_block(mean(list(genuine_delta[m].values())), gdm[m],
                         N_DEV_RECORDS) for m in ORDER},
        "did_extremes": _ci_block(
            mean(list(genuine_delta[WEAKEST].values()))
            - mean(list(genuine_delta[STRONGEST].values())),
            [w - s for w, s in zip(gdm[WEAKEST], gdm[STRONGEST])],
            N_DEV_RECORDS),
        "gate_did_committed": did_committed["point"],
    }

    # (iii) conditional-quality deltas and DiDs per lane
    cond: Dict[str, dict] = {}
    for lane in ("headline", "ast_python", "uniform"):
        ratio = {(m, arm): _ratio_draws(cond_sums[(m, arm, lane)],
                                        cond_cnts[(m, arm, lane)], draws)
                 for m in ORDER for arm in ARMS}
        block: Dict[str, dict] = {"per_model_delta": {}}
        for m in ORDER:
            pu, nu = points[(m, "unannotated", lane)]
            pa, na = points[(m, "annotated", lane)]
            dm = [x - y for x, y in zip(ratio[(m, "unannotated")],
                                        ratio[(m, "annotated")])]
            block["per_model_delta"][m] = _ci_block(pu - pa, dm, min(nu, na))
        dd = [(ratio[(WEAKEST, "unannotated")][i] - ratio[(WEAKEST, "annotated")][i])
              - (ratio[(STRONGEST, "unannotated")][i] - ratio[(STRONGEST, "annotated")][i])
              for i in range(BOOTSTRAP)]
        block["did_extremes"] = _ci_block(
            block["per_model_delta"][WEAKEST]["point"]
            - block["per_model_delta"][STRONGEST]["point"], dd, BOOTSTRAP)
        cond[lane] = block

    report = {
        "_meta": artifact_header(
            "echo_quality_analysis.py",
            ["bench/out/g3/refactor_dev__*.jsonl.gz",
             "bench/out/ablation/refactor_unannotated__*.jsonl.gz",
             "bench/out/annotation_ablation.json",
             "bench/out/.annotation_ablation_grades.json",
             "bench/out/ablation_did.json",
             "config/anti_patterns_db.json"],
            estimation_not_family=True,
            post_hoc=("registered 2026-09-01 after the suite's results were "
                      "read (ANALYSIS_PLAN.md post-hoc addenda); prompted by "
                      "external review of the gate metric"),
            echo_definition=("extracted completion code equals the item's own "
                             "input render, byte-identical after per-line "
                             "comment stripping and whitespace removal"),
            profiles=PROFILES, languages=LANGS, models=ORDER,
            n_reexec_per_cell=N_REEXEC),
        "per_model": per_model,
        "genuine_pass": genuine,
        "quality_given_pass": cond,
        "reexec_verified": sorted(reexec_log),
    }
    g = genuine["did_extremes"]
    print(f"genuine-pass DiD {g['point']:+.3f} "
          f"[{g['ci95'][0]:+.3f}, {g['ci95'][1]:+.3f}]  "
          f"(gate DiD committed {did_committed['point']:+.3f})", flush=True)
    for lane in cond:
        d = cond[lane]["did_extremes"]
        print(f"quality-given-pass DiD [{lane:10s}] {d['point']:+.3f} "
              f"[{d['ci95'][0]:+.3f}, {d['ci95'][1]:+.3f}]", flush=True)
    write_artifact(OUT, report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
