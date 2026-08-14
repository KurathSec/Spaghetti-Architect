"""Judge-task marker-leakage analysis. Zero API.

The judge stimulus is the ANNOTATED render, so every non-clean candidate carries
literal ``SPAGH_`` markers whose count grows with the incidental knob: a judge
could rank pairs by counting markers without reading any code. This script
quantifies that channel offline: (a) marker densities over the 250 dev judge
items, (b) a trivial regex judge (fewer literal ``SPAGH_`` substrings = cleaner),
(c) the repo's static metric-heuristic judge on the same pairs, and (d) the
leakage-resistant subset of pairs where the marker count is uninformative (tie)
or actively misleading (anti-ordered), on which any marker-counting judge is
capped at chance while a real complexity signal survives.

Marker count = literal ``src.count("SPAGH_")`` (NOT the grouped-id expansion of
``eval.metrics.spagh_markers``: a grouped comment like ``SPAGH_001/006/008`` is
ONE visible cue; only the literal count reproduces the published densities).

Inputs: the in-memory dev split (``bench.dataset.load('dev')``, deterministic),
``config/anti_patterns_db.json`` via the engine render, and the committed
baseline ``bench/out/subagent/judge__baseline_metric_judge.json`` (reference for
the heuristic-judge kill-switch).
Outputs: ``bench/out/judge_leakage.json`` + ``bench/out/judge_leakage_subset.json``.

Verification kill-switches (frozen figures; a mismatch means THIS script is
wrong): 250 items / 1268 unordered pairs / 50 zero-marker-candidate items (all
python); mean markers 2.03 on rank-0 and 6.00 on max-labelled candidates
(+-0.005); regex judge 0.6635 per-item / 0.7141 pooled (+-0.001; published 0.664
/ 0.714); heuristic judge per-item within +-0.005 of the committed baseline
aggregate; every anti-ordered pair (marker count OPPOSED to the ground-truth
rank order) is a regex-judge loss by construction, so regex accuracy on that
stratum is < 0.5 whenever it is non-empty. In the dev data the stratum is EMPTY:
the literal marker channel is perfectly monotone with rank (543 ordered pairs,
725 ties, 0 anti-ordered), which the published pooled figure itself certifies
(0.7141 = (543 + 0.5*725) / 1268 admits zero regex losses); the script asserts
the stratum-size = regex-loss-count identity instead of averaging an empty list.

Run with the metrics venv (~/.venvs/spaghetti-metrics/bin/python) so ``lizard``
backs the heuristic's non-Python lane, matching the committed baseline.
"""

from __future__ import annotations

import json
import os
import sys
from itertools import combinations

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bench.suite_common import (  # noqa: E402
    artifact_header,
    mean,
    write_artifact,
)

from bench import dataset as D  # noqa: E402
from bench import grade as G  # noqa: E402
from bench import tasks as T  # noqa: E402
from bench import uniform_lane as U  # noqa: E402

OUT_MAIN = os.path.join(_HERE, "out", "judge_leakage.json")
OUT_SUBSET = os.path.join(_HERE, "out", "judge_leakage_subset.json")
COMMITTED_BASELINE = os.path.join(_HERE, "out", "subagent",
                                  "judge__baseline_metric_judge.json")

# Frozen published figures (kill-switch targets; never adjust these).
EXP_N_ITEMS = 250
EXP_N_PAIRS = 1268
EXP_N_ZERO_MARKER_ITEMS = 50
EXP_MEAN_MARKERS_RANK0 = 2.03      # +- 0.005
EXP_MEAN_MARKERS_MAX = 6.00        # +- 0.005
EXP_REGEX_PER_ITEM = 0.6635        # +- 0.001 (published 0.664)
EXP_REGEX_POOLED = 0.7141          # +- 0.001 (published 0.714)
HEURISTIC_TOL = 0.005              # vs the committed baseline aggregate

CRITERION = (
    "unordered candidate pairs (a, b) with rank_a < rank_b (a is ground-truth "
    "cleaner) where sign(markers_a - markers_b) is 0 (stratum 'tie': equal "
    "literal SPAGH_ counts) or opposed to sign(rank_a - rank_b), i.e. +1 "
    "(stratum 'antiordered': the cleaner candidate carries MORE markers). On "
    "this subset a judge that ranks by counting literal SPAGH_ markers is "
    "capped at chance: it scores exactly 0.5 on ties by convention and is "
    "always wrong on anti-ordered pairs. (In the dev data the anti-ordered "
    "stratum is empty: marker count never decreases with rank, so the subset "
    "is exactly the tie stratum.)"
)


def _markers(src: str) -> int:
    """Literal marker density: count of 'SPAGH_' substrings, no id expansion."""
    return src.count("SPAGH_")


def main() -> int:
    committed = json.load(open(COMMITTED_BASELINE))
    committed_acc = committed["aggregate"]["pairwise_acc_mean"]
    lizard_available = U.available()
    if not lizard_available:
        sys.exit("lizard is not importable: run with "
                 "~/.venvs/spaghetti-metrics/bin/python so the heuristic judge "
                 "matches the committed tool-backed baseline")

    # ---- (a) items + marker densities -------------------------------------
    items = T.build_judge_items(D.load("dev"))
    if len(items) != EXP_N_ITEMS:
        sys.exit(f"expected {EXP_N_ITEMS} judge items, built {len(items)}")

    n_pairs_total = sum(len(list(combinations(it.levels, 2))) for it in items)
    if n_pairs_total != EXP_N_PAIRS:
        sys.exit(f"expected {EXP_N_PAIRS} unordered pairs, got {n_pairs_total}")

    zero_marker_items = [it for it in items
                         if any(_markers(src) == 0 for (_l, _r, src) in it.levels)]
    zero_langs = sorted({it.language for it in zero_marker_items})
    if len(zero_marker_items) != EXP_N_ZERO_MARKER_ITEMS or zero_langs != ["python"]:
        sys.exit(f"expected {EXP_N_ZERO_MARKER_ITEMS} zero-marker-candidate items "
                 f"(all python), got {len(zero_marker_items)} langs={zero_langs}")

    rank0_markers = [_markers(it.levels[0][2]) for it in items]
    max_markers = [_markers(src) for it in items
                   for (lab, _r, src) in it.levels if lab == "max"]
    m_rank0 = mean(rank0_markers)
    m_max = mean(max_markers)
    if abs(m_rank0 - EXP_MEAN_MARKERS_RANK0) > 0.005:
        sys.exit(f"mean rank-0 markers {m_rank0} != {EXP_MEAN_MARKERS_RANK0} +-0.005")
    if abs(m_max - EXP_MEAN_MARKERS_MAX) > 0.005:
        sys.exit(f"mean max-label markers {m_max} != {EXP_MEAN_MARKERS_MAX} +-0.005")

    # ---- (b) regex judge + (c) heuristic pair scores, one pass ------------
    per_item_regex = []          # macro: mean over items of per-item pair mean
    pooled_regex = []            # micro: every pair weighted equally
    per_item_heur = []           # metric_heuristic_judge convention (canonical path)
    pooled_heur = []             # scored pairs only (abstain = skip)
    n_heur_unscored = 0
    n_regex_ties = 0
    subset_pairs = []            # emitted rows for judge_leakage_subset.json
    subset_regex = {"tie": [], "antiordered": []}
    subset_heur = {"tie": [], "antiordered": []}
    subset_heur_unscored = {"tie": 0, "antiordered": 0}

    for it in items:
        comp = [G.static_complexity(it.language, src) for (_l, _r, src) in it.levels]
        marks = [_markers(src) for (_l, _r, src) in it.levels]
        item_regex = []
        for (ia, (la, ra, _sa)), (ib, (lb, rb, _sb)) in combinations(
                enumerate(it.levels), 2):
            # ranks are contiguous level indices, so ra < rb always: 'a' is the
            # ground-truth cleaner candidate of the pair.
            ma, mb = marks[ia], marks[ib]

            # regex judge: fewer literal markers = cleaner; exact tie = 0.5
            if ma == mb:
                s_regex = 0.5
                n_regex_ties += 1
            else:
                s_regex = 1.0 if ma < mb else 0.0
            item_regex.append(s_regex)
            pooled_regex.append(s_regex)

            # heuristic judge pair score (grade.metric_heuristic_judge rules):
            # unscored -> abstain, complexity tie -> 0.5, else lower-cc = cleaner
            ca, cb = comp[ia], comp[ib]
            if ca is None or cb is None:
                s_heur = None
                n_heur_unscored += 1
            elif ca == cb:
                s_heur = 0.5
                pooled_heur.append(s_heur)
            else:
                s_heur = 1.0 if ca < cb else 0.0
                pooled_heur.append(s_heur)

            # (d) leakage-resistant subset membership
            if ma >= mb:
                stratum = "tie" if ma == mb else "antiordered"
                subset_pairs.append({
                    "sample": it.sample, "language": it.language,
                    "level_a": la, "level_b": lb,
                    "rank_a": ra, "rank_b": rb,
                    "markers_a": ma, "markers_b": mb,
                    "stratum": stratum,
                })
                subset_regex[stratum].append(s_regex)
                if s_heur is None:
                    subset_heur_unscored[stratum] += 1
                else:
                    subset_heur[stratum].append(s_heur)

        per_item_regex.append(mean(item_regex))
        # Canonical code path for the committed-baseline kill-switch.
        per_item_heur.append(G.metric_heuristic_judge(it)["pairwise_acc"])

    regex_macro = mean(per_item_regex)
    regex_micro = mean(pooled_regex)
    if abs(regex_macro - EXP_REGEX_PER_ITEM) > 0.001:
        sys.exit(f"regex per-item acc {regex_macro} != {EXP_REGEX_PER_ITEM} +-0.001")
    if abs(regex_micro - EXP_REGEX_POOLED) > 0.001:
        sys.exit(f"regex pooled acc {regex_micro} != {EXP_REGEX_POOLED} +-0.001")

    heur_macro = mean(per_item_heur)
    if abs(heur_macro - committed_acc) > HEURISTIC_TOL:
        sys.exit(f"heuristic per-item acc {heur_macro} != committed "
                 f"{committed_acc} +-{HEURISTIC_TOL}")

    # ---- (d) subset strata -------------------------------------------------
    n_tie = len(subset_regex["tie"])
    n_anti = len(subset_regex["antiordered"])
    if n_tie + n_anti != len(subset_pairs):
        sys.exit("subset stratum counts do not partition the subset")
    regex_tie = mean(subset_regex["tie"]) if n_tie else None  # 0.5 by convention
    if regex_tie != 0.5:
        sys.exit(f"regex accuracy on ties is {regex_tie}, not the 0.5 convention")
    # The falsifiable stratum: on every anti-ordered pair the regex judge picks
    # the fewer-markers (= messier) candidate, i.e. it is WRONG by construction,
    # so its stratum accuracy is < 0.5 whenever the stratum is non-empty. Losses
    # (pair score 0.0) happen on anti-ordered pairs and nowhere else, so the
    # stratum size must equal the regex judge's pooled loss count — in the dev
    # data both are 0, which the published pooled 0.7141 certifies
    # ((543 wins + 0.5 * 725 ties) / 1268 leaves no room for a loss).
    n_regex_losses = sum(1 for s in pooled_regex if s == 0.0)
    if n_regex_losses != n_anti:
        sys.exit(f"stratum/loss identity broken: {n_anti} anti-ordered pairs "
                 f"but {n_regex_losses} regex losses")
    if n_anti:
        regex_anti = mean(subset_regex["antiordered"])
        if not regex_anti < 0.5:
            sys.exit(f"regex judge scores {regex_anti} on anti-ordered pairs; "
                     "a marker-counting judge must be below chance there")
    else:
        regex_anti = None

    def _stratum(scores, n_all, n_unscored):
        return {"acc": mean(scores) if scores else None,
                "n": n_all, "n_scored": len(scores), "n_unscored": n_unscored}

    subset_report = {
        "criterion": CRITERION,
        "n": len(subset_pairs),
        "n_tie": n_tie,
        "n_antiordered": n_anti,
        "antiordered_note": (
            "empty in dev: literal marker count never decreases with rank "
            "(543 ordered pairs, 725 ties, 0 anti-ordered); equivalently the "
            "regex judge has zero losses, which the published pooled 0.714 "
            "already implies. Accuracy on an empty stratum is null, never 0."
            if n_anti == 0 else
            "regex is wrong by construction on every anti-ordered pair"),
        "regex_judge": {
            "tie": {"acc": regex_tie, "n": n_tie},
            "antiordered": {"acc": regex_anti, "n": n_anti},
            "overall": {"acc": mean(subset_regex["tie"] + subset_regex["antiordered"]),
                        "n": len(subset_pairs)},
        },
        "heuristic_judge": {
            "tie": _stratum(subset_heur["tie"], n_tie,
                            subset_heur_unscored["tie"]),
            "antiordered": _stratum(subset_heur["antiordered"], n_anti,
                                    subset_heur_unscored["antiordered"]),
            "overall": _stratum(subset_heur["tie"] + subset_heur["antiordered"],
                                len(subset_pairs),
                                subset_heur_unscored["tie"]
                                + subset_heur_unscored["antiordered"]),
        },
    }

    inputs = ["config/anti_patterns_db.json",
              "bench/out/subagent/judge__baseline_metric_judge.json",
              "bench/dataset.py::load('dev') (in-memory deterministic mint)"]

    write_artifact(OUT_SUBSET, {
        "_meta": artifact_header("judge_leakage_analysis.py", inputs,
                                 lizard_available=lizard_available),
        "spec_version": 1,
        "criterion": CRITERION,
        "n": len(subset_pairs),
        "n_tie": n_tie,
        "n_antiordered": n_anti,
        "pairs": subset_pairs,
    })

    write_artifact(OUT_MAIN, {
        "_meta": artifact_header("judge_leakage_analysis.py", inputs,
                                 lizard_available=lizard_available),
        "marker_definition": "literal src.count('SPAGH_') per candidate (no "
                             "grouped-id expansion)",
        "items": {
            "n_items": len(items),
            "n_pairs_total": n_pairs_total,
            "n_zero_marker_candidate_items": len(zero_marker_items),
            "zero_marker_candidate_languages": zero_langs,
            "mean_markers_rank0": m_rank0,
            "n_rank0_candidates": len(rank0_markers),
            "mean_markers_max_label": m_max,
            "n_max_label_candidates": len(max_markers),
        },
        "regex_judge": {
            "definition": "per pair pick the fewer-literal-markers candidate as "
                          "cleaner; exact tie scores 0.5",
            "per_item_acc": regex_macro,
            "per_item_acc_published": 0.664,
            "pooled_acc": regex_micro,
            "pooled_acc_published": 0.714,
            "n_items": len(per_item_regex),
            "n_pairs": len(pooled_regex),
            "n_tie_pairs": n_regex_ties,
        },
        "heuristic_judge": {
            "definition": "bench.grade.static_complexity per candidate, lower = "
                          "cleaner; complexity tie 0.5; unscored pair abstains "
                          "(metric_heuristic_judge conventions)",
            "lizard_available": lizard_available,
            "per_item_acc": heur_macro,
            "per_item_acc_committed": committed_acc,
            "n_items": len(per_item_heur),
            "pooled_acc_scored": mean(pooled_heur),
            "n_pairs_scored": len(pooled_heur),
            "n_pairs_unscored": n_heur_unscored,
        },
        "leakage_resistant_subset": subset_report,
        "subset_artifact": "bench/out/judge_leakage_subset.json",
    })

    print(f"items={len(items)} pairs={n_pairs_total} "
          f"regex macro={regex_macro:.4f} micro={regex_micro:.4f} "
          f"heuristic={heur_macro:.4f} (committed {committed_acc:.4f}) "
          f"subset n={len(subset_pairs)} tie={n_tie} anti={n_anti} "
          f"regex_anti={regex_anti}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
