"""Copy-rate mechanism analysis behind differential annotation inflation. Zero API.

The annotation ablation established THAT stripping the generator's self-annotations
deflates weaker models far more than stronger ones. This script asks WHY: the
per-operation annotation comment states the operation's clean form ("OPNAME: <expr>"
via BaseGenerator.describe), so a model can copy the stated answer instead of
deriving it. Measured here, per refactor completion (draw[0] of each stored record):

  (i)  payload copy rate — the fraction of the item's clean-form comment payloads
       appearing, whitespace-normalized, as substrings of the extracted code;
  (ii) clean-static copy rate (Python items only) — the fraction of
       eval.metrics.clean_baseline_static lines appearing likewise.

Both are stratified by model x arm (annotated/unannotated) x semantic_ok (k=1),
and the annotated-minus-unannotated copy-rate gap per model gets a base-IR
cluster bootstrap CI on shared draws (paired per item where both arms yield code).

Inputs (all committed; nothing is fetched):
  bench/out/g3/refactor_dev__<slug>.jsonl.gz          annotated arm (draw[0] of k=8)
  bench/out/ablation/refactor_unannotated__<slug>.jsonl.gz  unannotated arm (k=1)
  bench/out/.annotation_ablation_grades.json          semantic_ok at k=1 (via all_grades)
  bench/out/annotation_ablation.json                  committed per-model ablation deltas
  config/anti_patterns_db.json + the minted dev split renders (both annotate arms)

Kill-switches (frozen facts; a failure means THIS script is wrong):
  * for every (item, profile, language) cell, the annotated and unannotated renders
    are byte-identical after comment stripping (eval.metrics._code_part per line);
  * every cell of a stem yields the same clean-form payload set, and its size equals
    the stem's operation count (describe() emits exactly one line per operation);
  * each arm's archive holds exactly 1500 dev records per model, every record has a
    cached k=1 grade, and the committed ablation report carries a refactor delta for
    all four models.

Output: bench/out/copy_rate.json (deterministic; re-runs are byte-identical).
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Dict, List, Tuple

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
    base_ir,
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
from eval import metrics as M  # noqa: E402
from src.engine import Engine  # noqa: E402

OUT = os.path.join(_HERE, "out", "copy_rate.json")
COMMITTED = os.path.join(_HERE, "out", "annotation_ablation.json")

PROFILES = ["minimal", "standard", "max"]  # the scored incidental subset
LANGS = D.LANGS
DB = D.DB
N_DEV_RECORDS = 1500  # 100 dev items x 3 profiles x 5 languages

# The describe() grammar: one comment line per operation stating its clean form.
_DESCRIBE_RE = re.compile(
    r"^\s*(?:#|//)\s(MEMBERSHIP_CHECK|KEY_VALUE_LOOKUP|AGGREGATE|CONDITIONAL_SELECT): (.+)$"
)


def _ws_norm(s: str) -> str:
    """Whitespace-normalize for substring matching: drop ALL whitespace."""
    return "".join(s.split())


def _code_only(src: str, lang: str) -> str:
    """The render with every comment stripped: per-line _code_part (string-aware),
    trailing whitespace removed, blank/comment-only lines dropped."""
    prefix = M._LINE_COMMENT[lang]
    kept = []
    for ln in src.splitlines():
        code = M._code_part(ln, prefix).rstrip()
        if code.strip():
            kept.append(code)
    return "\n".join(kept)


# --------------------------------------------------------------------------- #
# (a) + (b): render both arms, isolate annotation lines, extract payloads
# --------------------------------------------------------------------------- #
def render_annotations(sp) -> Tuple[Dict[str, List[dict]], dict]:
    """Render every (dev item, profile) with Engine(annotate=True/False) directly,
    one engine per (profile, annotate), sequential rendering. Returns
    ({stem: [{"op":..., "payload":...}]}, summary) and enforces the (a)/(b) kills."""
    engines = {(p, ann): Engine(DB, p, annotate=ann)
               for p in PROFILES for ann in (True, False)}
    payloads_by_stem: Dict[str, List[dict]] = {}
    line_counts: Dict[str, List[int]] = {f"{p}/{lang}": []
                                         for p in PROFILES for lang in LANGS}
    n_cells = 0
    for it in sp.items:
        ir = sp.ir(it.stem)
        for profile in PROFILES:
            src_a = engines[(profile, True)].generate(ir)["sources"]
            src_u = engines[(profile, False)].generate(ir)["sources"]
            for lang in LANGS:
                n_cells += 1
                a, u = src_a[lang], src_u[lang]
                # KILL (a): comment-stripped renders must be byte-identical.
                if _code_only(a, lang) != _code_only(u, lang):
                    sys.exit(f"code-part mismatch: {it.stem}/{profile}/{lang} — the "
                             "annotated and unannotated renders differ beyond comments; "
                             "this script's stripping is wrong")
                ann_lines = sorted(set(a.splitlines()) - set(u.splitlines()))
                line_counts[f"{profile}/{lang}"].append(len(ann_lines))
                cell = sorted(
                    ({"op": m.group(1), "payload": m.group(2)}
                     for ln in ann_lines if (m := _DESCRIBE_RE.match(ln)) is not None),
                    key=lambda d: (d["op"], d["payload"]))
                # KILL (b): the payload set is a stem property — identical across the
                # stem's 15 cells, exactly one payload per operation.
                if it.stem not in payloads_by_stem:
                    if len(cell) != it.n_operations:
                        sys.exit(f"{it.stem}/{profile}/{lang}: {len(cell)} clean-form "
                                 f"payloads != n_operations {it.n_operations}")
                    payloads_by_stem[it.stem] = cell
                elif cell != payloads_by_stem[it.stem]:
                    sys.exit(f"{it.stem}/{profile}/{lang}: payload set differs across "
                             "cells; describe() is language/profile-agnostic, so this "
                             "script's extraction is wrong")
    summary = {
        "n_cells": n_cells,
        "code_part_identity": "PASS: all cells byte-identical after comment stripping",
        "annotation_lines_mean_by_cell": {
            k: mean(v) for k, v in sorted(line_counts.items())},
        "payloads_by_op": dict(sorted(
            _count_ops(payloads_by_stem).items())),
    }
    return payloads_by_stem, summary


def _count_ops(payloads_by_stem: Dict[str, List[dict]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for cell in payloads_by_stem.values():
        for d in cell:
            counts[d["op"]] = counts.get(d["op"], 0) + 1
    return counts


# --------------------------------------------------------------------------- #
# (c): per-completion copy rates, stratified, plus the per-model paired gap
# --------------------------------------------------------------------------- #
def _clean_static_lines(sp) -> Dict[str, List[str]]:
    """Per stem: clean_baseline_static lines, blank/#-comment lines dropped."""
    out: Dict[str, List[str]] = {}
    for it in sp.items:
        lines = [ln for ln in M.clean_baseline_static(sp.program(it.stem)).splitlines()
                 if ln.strip() and not ln.strip().startswith("#")]
        if not lines:
            sys.exit(f"{it.stem}: empty clean_baseline_static")
        out[it.stem] = lines
    return out


def _frac_contained(needles: List[str], code_norm: str) -> float:
    if not needles:
        raise ValueError("no needles: payload extraction failed upstream")
    return sum(1 for s in needles if _ws_norm(s) in code_norm) / len(needles)


def _stratum(vals: List[float]) -> dict:
    return {"n": len(vals), "mean": (mean(vals) if vals else None)}


def copy_rate_report(sp, payloads_by_stem: Dict[str, List[dict]], grades: dict) -> dict:
    stem_of = {(it.sample, it.variant): it.stem for it in sp.items}
    clean_lines = _clean_static_lines(sp)
    committed = json.load(open(COMMITTED))["refactor"]["per_model"]
    for m in ORDER:
        if m not in committed or "delta" not in committed[m]:
            sys.exit(f"committed ablation report lacks a refactor delta for {m}")

    per_model: Dict[str, dict] = {}
    copy_by_arm: Dict[Tuple[str, str], Dict[str, float]] = {}
    for short, slug in MODELS:
        arms = {"annotated": load_annotated("refactor", slug),
                "unannotated": load_unannotated("refactor", slug)}
        model_out: Dict[str, dict] = {}
        for arm, recs in arms.items():
            if len(recs) != N_DEV_RECORDS:
                sys.exit(f"{short}/{arm}: {len(recs)} records != {N_DEV_RECORDS}")
            gkey = "ann1" if arm == "annotated" else "un1"
            gmap = grades["refactor"][short][gkey]
            strata = {0: [], 1: []}
            py_strata = {0: [], 1: []}
            per_item: Dict[str, float] = {}
            n_missing_raw = n_no_code = 0
            for rec in recs:
                raw = rec.get("raw_outputs")
                if not raw or raw == ["<mock>"]:
                    n_missing_raw += 1
                    continue
                key = item_key(rec)
                if key not in gmap:
                    sys.exit(f"{short}/{arm}: no cached k=1 grade for {key}")
                code = G.extract_code(raw[0])
                if not code:
                    n_no_code += 1
                    continue
                sok = 1 if gmap[key] >= 0.5 else 0
                code_norm = _ws_norm(code)
                stem = stem_of[(rec["sample"], rec.get("variant", "base"))]
                frac = _frac_contained(
                    [d["payload"] for d in payloads_by_stem[stem]], code_norm)
                strata[sok].append(frac)
                per_item[key] = frac
                if rec["language"] == "python":
                    py_strata[sok].append(
                        _frac_contained(clean_lines[stem], code_norm))
            copy_by_arm[(short, arm)] = per_item
            model_out[arm] = {
                "n_records": len(recs),
                "n_missing_raw": n_missing_raw,
                "n_no_code": n_no_code,
                "payload_copy_rate": {f"semantic_ok_{s}": _stratum(strata[s])
                                      for s in (0, 1)},
                "clean_static_copy_rate_python": {
                    f"semantic_ok_{s}": _stratum(py_strata[s]) for s in (0, 1)},
            }
        per_model[short] = model_out

    # ---- paired annotated-minus-unannotated gap, cluster bootstrap on shared draws
    diffs = {}
    for short in ORDER:
        a, u = copy_by_arm[(short, "annotated")], copy_by_arm[(short, "unannotated")]
        common = sorted(set(a) & set(u))
        if not common:
            sys.exit(f"no paired items for {short}")
        diffs[short] = {k: a[k] - u[k] for k in common}
    diff_clusters = {m: cluster_scores(diffs[m]) for m in ORDER}
    cluster_keys = set(diff_clusters[ORDER[0]])
    for m in ORDER:
        if set(diff_clusters[m]) != cluster_keys:
            sys.exit(f"cluster space differs for {m}; expected identical base-IR sets")
    draws = shared_cluster_draws(cluster_keys, BOOTSTRAP, SUITE_SEED)

    gap = {}
    for m in ORDER:
        dm = draw_means(diff_clusters[m], draws)
        gap[m] = {
            "point": mean(list(diffs[m].values())),
            "ci95": percentile_ci95(dm),
            "p_bootstrap": two_sided_bootstrap_p(dm),
            "n_pairs": len(diffs[m]),
            "n_clusters": len(cluster_keys),
        }

    # ---- deliverable claim: inspect, do not force
    correct_ann = {}
    for m in ORDER:
        s = per_model[m]["annotated"]["payload_copy_rate"]["semantic_ok_1"]
        if s["n"] == 0:
            sys.exit(f"{m}: no semantically-correct annotated completions")
        correct_ann[m] = s["mean"]
    monotone = all(correct_ann[a] > correct_ann[b] for a, b in zip(ORDER, ORDER[1:]))
    align = {m: {"copy_rate_gap": gap[m]["point"],
                 "ablation_delta": committed[m]["delta"]} for m in ORDER}
    gap_rank = sorted(ORDER, key=lambda m: -gap[m]["point"])
    delta_rank = sorted(ORDER, key=lambda m: -abs(committed[m]["delta"]))

    return {
        "per_model": per_model,
        "gap_annotated_minus_unannotated": {
            "definition": "per-item paired payload-copy-rate difference "
                          "(annotated - unannotated), items where both arms "
                          "yielded extractable code",
            "per_model": gap,
        },
        "claim_inspection": {
            "copy_rate_correct_annotated_by_model": correct_ann,
            "monotone_decreasing_weakest_to_strongest": monotone,
            "gap_vs_ablation_delta": align,
            "gap_rank": gap_rank,
            "delta_magnitude_rank": delta_rank,
            "gap_rank_matches_delta_magnitude_rank": gap_rank == delta_rank,
            "note": "reported, not enforced: the deliverable asks whether copying "
                    "explains the differential inflation, whatever the answer",
        },
    }


def main() -> int:
    sp = D.load("dev")
    if len(sp.items) != 100:
        sys.exit(f"dev split has {len(sp.items)} items, expected 100")
    payloads_by_stem, ann_summary = render_annotations(sp)
    grades = all_grades(use_cache="--no-cache" not in sys.argv)
    report = {
        "_meta": artifact_header(
            "copy_rate_analysis.py",
            ["bench/out/g3/refactor_dev__*.jsonl.gz",
             "bench/out/ablation/refactor_unannotated__*.jsonl.gz",
             "bench/out/annotation_ablation.json",
             "bench/out/.annotation_ablation_grades.json",
             "config/anti_patterns_db.json"],
            profiles=PROFILES, languages=LANGS, models=ORDER,
            whitespace_normalization="strip-all"),
        "annotation_texts": ann_summary,
        "clean_form_payloads": {stem: payloads_by_stem[stem]
                                for stem in sorted(payloads_by_stem)},
        "copy_rates": copy_rate_report(sp, payloads_by_stem, grades),
    }
    ci = report["copy_rates"]["claim_inspection"]
    for m in ORDER:
        g = report["copy_rates"]["gap_annotated_minus_unannotated"]["per_model"][m]
        print(f"{m:18s} correct-annotated copy {ci['copy_rate_correct_annotated_by_model'][m]:.3f}  "
              f"gap {g['point']:+.3f} {['%.3f' % x for x in g['ci95']]}  "
              f"ablation delta {ci['gap_vs_ablation_delta'][m]['ablation_delta']:+.3f}")
    print(f"monotone decreasing (correct annotated): "
          f"{ci['monotone_decreasing_weakest_to_strongest']}  "
          f"gap rank == delta rank: {ci['gap_rank_matches_delta_magnitude_rank']}")
    write_artifact(OUT, report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
