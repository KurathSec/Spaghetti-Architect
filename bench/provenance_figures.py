"""Committed reproduction of three previously script-less published figures. Zero API.

Three number groups quoted in the report never had a checked-in derivation:

(a) exposure_report — the annotated arm leaks the clean form: per-operation
    comments name the idiomatic line, so a fraction of the clean-baseline lines
    is present verbatim inside every rendered source. Recomputes the published
    72% (pooled line-level exposure of ``clean_baseline_static`` across the 100
    dev items x {minimal,standard,max} x 5 languages) and the published 87%
    (``clean_baseline_runnable`` vs the 300 Python (item, profile) cells).
(b) inert_cells_report — profile collapse in the frozen renders: light and
    standard render identically on all 500 (item, language) cells, and 36/100
    items are degenerate (only 2 distinct renders across all 5 profiles, on
    every language).
(c) inert_signature_crosscheck — the manifest's per-profile ``enabled_spagh``
    sets, projected through the empirically-inert ids (ids whose presence never
    changes any render between adjacent profiles of that item), predict per-item
    degeneracy in full agreement with the render route. The projection is
    required: the raw heavy/max sets are equal on 0/100 items, yet the renders
    coincide for every degenerate item; the render route is authoritative.

Inputs: bench/data/dev/*.json (frozen records) and bench/data/manifest.json.
Sources route: the FROZEN ``record["sources"][profile][lang]`` texts are used
directly instead of re-rendering through ``src.engine.Engine(DB, profile)``;
tests/test_frozen_dev.py proves the default annotated engine reproduces every
one of the 2500 cells byte-for-byte, so the two routes are equivalent.
Clean baselines are recomputed live: ``src.nodes.parser.parse`` on
``record["ir"]``, then ``eval.metrics.clean_baseline_static/runnable``.

Output: bench/out/provenance_figures.json (deterministic; no RNG, no clock).

Kill-switches (the published figures are frozen; a mismatch means THIS script
is wrong): static exposure 0.7198 +- 0.0005; per-language spread < 0.005;
families at 100% exposure exactly {agg_stats, allowlist, threshold_select};
runnable-Python exposure 0.8737 +- 0.0005; light==standard on 500/500 cells;
distinct-render histogram {4: 320, 2: 180}; 36/100 degenerate items (180/500
cells); heavy==max raw signature equality on 0/100 items; projection agreement
100/100.
"""

from __future__ import annotations

import collections
import glob
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bench.suite_common import (  # noqa: E402
    artifact_header,
    base_ir,
    write_artifact,
)
from eval.metrics import clean_baseline_runnable, clean_baseline_static  # noqa: E402
from src.nodes.parser import parse  # noqa: E402

OUT = os.path.join(_HERE, "out", "provenance_figures.json")
DEV_GLOB = os.path.join(_HERE, "data", "dev", "*.json")
MANIFEST = os.path.join(_HERE, "data", "manifest.json")

PROFILES = ["minimal", "light", "standard", "heavy", "max"]
SCORED_PROFILES = ["minimal", "standard", "max"]   # the scored subset
LANGS = ["python", "javascript", "go", "java", "cpp"]

# Frozen published values (see module docstring): never adjust these.
EXPECT_STATIC = 0.7198
EXPECT_RUNNABLE = 0.8737
EXPECT_TOL = 0.0005
EXPECT_LANG_SPREAD_MAX = 0.005
EXPECT_FAMILIES_100 = {"agg_stats", "allowlist", "threshold_select"}
EXPECT_HIST = {4: 320, 2: 180}
EXPECT_DEGENERATE_ITEMS = 36


def _load_dev():
    paths = sorted(glob.glob(DEV_GLOB))
    if len(paths) != 100:
        sys.exit(f"expected 100 frozen dev records, found {len(paths)}")
    records = []
    for p in paths:
        rec = json.load(open(p))
        if sorted(rec["sources"]) != sorted(PROFILES):
            sys.exit(f"{rec['stem']}: profile set drifted")
        for prof in PROFILES:
            if sorted(rec["sources"][prof]) != sorted(LANGS):
                sys.exit(f"{rec['stem']}: language set drifted")
        records.append(rec)
    return records


def _baseline_lines(text: str):
    """Baseline lines to test for exposure: drop blank and '#'-prefixed lines."""
    return [ln.strip() for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


def _exposure_report(records) -> dict:
    """(a) Pooled verbatim-line exposure of the clean baseline in the annotated
    renders. A baseline line counts as present in a cell iff its
    whitespace-stripped text occurs verbatim inside the rendered source (this
    catches both literal code lines and the per-operation annotation comments
    that name the clean form). The published figure is the POOLED line-level
    fraction: total present line-slots / total line-slots over all cells."""
    hits = total = 0
    by_lang = {l: [0, 0] for l in LANGS}       # lang -> [hits, total]
    by_family = collections.defaultdict(lambda: [0, 0])
    cell_fracs = []                            # secondary: per-cell fractions
    run_hits = run_total = 0
    run_cell_fracs = []
    n_cells = n_run_cells = 0

    for rec in records:
        program = parse(rec["ir"])
        static_lines = _baseline_lines(clean_baseline_static(program))
        runnable_lines = _baseline_lines(clean_baseline_runnable(program))
        if not static_lines or not runnable_lines:
            sys.exit(f"{rec['stem']}: empty clean baseline")
        for prof in SCORED_PROFILES:
            for lang in LANGS:
                src = rec["sources"][prof][lang]
                h = sum(1 for b in static_lines if b in src)
                hits += h
                total += len(static_lines)
                by_lang[lang][0] += h
                by_lang[lang][1] += len(static_lines)
                fam = by_family[rec["family"]]
                fam[0] += h
                fam[1] += len(static_lines)
                cell_fracs.append(h / len(static_lines))
                n_cells += 1
            src_py = rec["sources"][prof]["python"]
            rh = sum(1 for b in runnable_lines if b in src_py)
            run_hits += rh
            run_total += len(runnable_lines)
            run_cell_fracs.append(rh / len(runnable_lines))
            n_run_cells += 1

    if total == 0 or run_total == 0:
        sys.exit("no baseline line-slots counted")
    static_pooled = hits / total
    runnable_pooled = run_hits / run_total
    lang_pooled = {l: h / t for l, (h, t) in by_lang.items()}
    fam_pooled = {f: h / t for f, (h, t) in sorted(by_family.items())}
    lang_spread = max(lang_pooled.values()) - min(lang_pooled.values())
    families_100 = {f for f, v in fam_pooled.items() if v == 1.0}

    # --- kill-switches -------------------------------------------------------
    if abs(static_pooled - EXPECT_STATIC) > EXPECT_TOL:
        sys.exit(f"exposure_static_mean {static_pooled:.6f} != published "
                 f"{EXPECT_STATIC} +- {EXPECT_TOL}: this script is wrong")
    if lang_spread >= EXPECT_LANG_SPREAD_MAX:
        sys.exit(f"per-language exposure spread {lang_spread:.6f} >= "
                 f"{EXPECT_LANG_SPREAD_MAX}: exposure must be language-invariant")
    if families_100 != EXPECT_FAMILIES_100:
        sys.exit(f"families at 100% exposure {sorted(families_100)} != "
                 f"{sorted(EXPECT_FAMILIES_100)}: this script is wrong")
    if abs(runnable_pooled - EXPECT_RUNNABLE) > EXPECT_TOL:
        sys.exit(f"exposure_runnable_python {runnable_pooled:.6f} != published "
                 f"{EXPECT_RUNNABLE} +- {EXPECT_TOL}: this script is wrong")

    return {
        "definition": ("fraction of clean-baseline lines whose whitespace-"
                       "stripped text occurs verbatim in the rendered annotated "
                       "source; pooled over line-slots (the published figure), "
                       "with the per-cell mean as a secondary view"),
        "exposure_static_mean": static_pooled,
        "n_line_slots": total,
        "n_cells": n_cells,
        "per_language": {l: {"exposure": lang_pooled[l], "n_line_slots": by_lang[l][1]}
                         for l in LANGS},
        "per_language_spread": lang_spread,
        "per_family": {f: {"exposure": fam_pooled[f], "n_line_slots": by_family[f][1]}
                       for f in sorted(by_family)},
        "families_at_100pct": sorted(families_100),
        "exposure_runnable_python": runnable_pooled,
        "n_runnable_line_slots": run_total,
        "n_runnable_cells": n_run_cells,
        "secondary_mean_of_cell_fractions": {
            "static": sum(cell_fracs) / len(cell_fracs),
            "runnable_python": sum(run_cell_fracs) / len(run_cell_fracs),
        },
    }


def _inert_cells_report(records) -> dict:
    """(b) Profile collapse in the frozen renders over ALL 5 profiles."""
    n_cells = 0
    light_eq_standard = 0
    hist = collections.Counter()
    degenerate_items = []
    for rec in records:
        per_lang_distinct = []
        for lang in LANGS:
            srcs = [rec["sources"][p][lang] for p in PROFILES]
            n_cells += 1
            if srcs[PROFILES.index("light")] == srcs[PROFILES.index("standard")]:
                light_eq_standard += 1
            per_lang_distinct.append(len(set(srcs)))
        for d in per_lang_distinct:
            hist[d] += 1
        if all(d == 2 for d in per_lang_distinct):
            degenerate_items.append(rec["stem"])

    # --- kill-switches -------------------------------------------------------
    if light_eq_standard != n_cells or n_cells != 500:
        sys.exit(f"light==standard on {light_eq_standard}/{n_cells} cells, "
                 f"expected 500/500: this script is wrong")
    if dict(hist) != EXPECT_HIST:
        sys.exit(f"distinct-render histogram {dict(hist)} != {EXPECT_HIST}: "
                 f"this script is wrong")
    if len(degenerate_items) != EXPECT_DEGENERATE_ITEMS:
        sys.exit(f"{len(degenerate_items)} degenerate items != "
                 f"{EXPECT_DEGENERATE_ITEMS}: this script is wrong")
    degenerate_cells = hist[2]
    if degenerate_cells != 5 * len(degenerate_items):
        sys.exit(f"{degenerate_cells} degenerate cells != 5 x "
                 f"{len(degenerate_items)} items: degeneracy must be "
                 f"language-invariant")

    stems = {rec["stem"]: rec for rec in records}
    degenerate_base = sorted({base_ir(stems[s]["sample"]) for s in degenerate_items})
    return {
        "definition": ("distinct-render count per (item, language) cell across "
                       "the 5 profiles of the frozen sources; a cell is "
                       "degenerate when only 2 renders are distinct"),
        "n_cells": n_cells,
        "light_equals_standard_cells": light_eq_standard,
        "distinct_render_histogram": {str(k): v for k, v in sorted(hist.items())},
        "n_degenerate_items": len(degenerate_items),
        "n_degenerate_cells": degenerate_cells,
        "degenerate_items": sorted(degenerate_items),
        "degenerate_base_samples": degenerate_base,
    }


def _inert_signature_crosscheck(records) -> dict:
    """(c) Project manifest enabled_spagh sets through drop-inert-ids and check
    the prediction of per-item degeneracy against the render route."""
    manifest = json.load(open(MANIFEST))
    gt = manifest["ground_truth"]
    dev_stems = {rec["stem"] for rec in records}
    if set(gt) != dev_stems:
        sys.exit("manifest ground_truth stems != dev stems")

    pairs = list(zip(PROFILES, PROFILES[1:]))
    agree = 0
    heavy_eq_max_raw = 0
    seen_ids, active_ids = set(), set()
    change_counts = {f"{a}->{b}": 0 for a, b in pairs}
    class_items = collections.defaultdict(list)
    disagreements = []

    for rec in records:
        stem = rec["stem"]
        enabled = {p: set(gt[stem]["enabled_spagh"][p]) for p in PROFILES}
        if enabled["heavy"] == enabled["max"]:
            heavy_eq_max_raw += 1
        inert = set()
        for a, b in pairs:
            if not enabled[a] <= enabled[b]:
                sys.exit(f"{stem}: enabled_spagh not monotone across {a}->{b}")
            diff = enabled[b] - enabled[a]
            changed = any(rec["sources"][a][l] != rec["sources"][b][l]
                          for l in LANGS)
            seen_ids |= diff
            if changed:
                change_counts[f"{a}->{b}"] += 1
                active_ids |= diff
                if not diff:
                    sys.exit(f"{stem}: render changed {a}->{b} with an empty "
                             f"signature diff; sets cannot explain the renders")
            else:
                inert |= diff
        class_items[tuple(sorted(inert))].append(stem)

        projected = [frozenset(enabled[p] - inert) for p in PROFILES]
        predicted_distinct = len(set(projected))
        render_distinct = {len({rec["sources"][p][l] for p in PROFILES})
                           for l in LANGS}
        if len(render_distinct) != 1:
            sys.exit(f"{stem}: distinct-render count differs across languages")
        rendered = render_distinct.pop()
        if predicted_distinct == rendered:
            agree += 1
        else:
            disagreements.append({"stem": stem, "predicted": predicted_distinct,
                                  "rendered": rendered})

    # --- kill-switches -------------------------------------------------------
    if heavy_eq_max_raw != 0:
        sys.exit(f"raw heavy==max signature equality on {heavy_eq_max_raw}/100 "
                 f"items, expected 0/100 (the projection is required): this "
                 f"script is wrong")
    if agree != 100:
        sys.exit(f"projection predicts degeneracy on {agree}/100 items only; "
                 f"disagreements: {disagreements[:5]}: this script is wrong")

    globally_inert = sorted(seen_ids - active_ids)
    families = {rec["stem"]: rec["family"] for rec in records}
    classes = [{"inert_ids": list(ids),
                "n_items": len(items),
                "families": sorted({families[s] for s in items})}
               for ids, items in sorted(class_items.items())]
    return {
        "definition": ("per item: ids appearing only in adjacent-profile "
                       "signature diffs whose renders are byte-identical on all "
                       "languages are empirically inert; dropping them from "
                       "each profile's enabled_spagh set must predict the "
                       "item's distinct-render count. The render route is "
                       "authoritative; globally_inert_ids lists ids never seen "
                       "in any render-changing diff on any item"),
        "adjacent_pairs": [f"{a}->{b}" for a, b in pairs],
        "items_changed_per_transition": change_counts,
        "raw_heavy_equals_max_items": heavy_eq_max_raw,
        "projection_agreement_items": agree,
        "n_items": len(records),
        "globally_inert_ids": globally_inert,
        "per_item_inert_classes": classes,
    }


def main() -> int:
    records = _load_dev()
    report = {
        "_meta": artifact_header(
            "provenance_figures.py",
            ["bench/data/dev/*.json", "bench/data/manifest.json"],
            sources_route=("frozen record['sources'] used directly; "
                           "byte-identical to a direct Engine(DB, profile, "
                           "annotate=True) render per tests/test_frozen_dev.py"),
        ),
        "exposure_report": _exposure_report(records),
        "inert_cells_report": _inert_cells_report(records),
        "inert_signature_crosscheck": _inert_signature_crosscheck(records),
    }
    e = report["exposure_report"]
    b = report["inert_cells_report"]
    c = report["inert_signature_crosscheck"]
    print(f"exposure: static {e['exposure_static_mean']:.4f} "
          f"(n={e['n_line_slots']}), runnable-py "
          f"{e['exposure_runnable_python']:.4f} (n={e['n_runnable_line_slots']})")
    print(f"inert cells: hist {b['distinct_render_histogram']}, "
          f"degenerate {b['n_degenerate_items']}/100 items")
    print(f"crosscheck: {c['projection_agreement_items']}/100 agree, "
          f"globally inert {c['globally_inert_ids']}")
    write_artifact(OUT, report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
