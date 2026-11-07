"""Distinct-render census of the incidental knob, clean included. Zero API.

Registered 2026-11-07 (post hoc, estimation only), prompted by corrections to
the paper's description of the knob. ``provenance_figures.py`` counts distinct
renders over the five non-clean profiles ({4: 320, 2: 180}); the paper's rung
counts include ``clean``, so comparing them against that histogram mixes bases.
This script records the clean-inclusive counts and three related facts:

(a) distinct_renders: distinct renders per (item, language) cell over the six
    knob levels, with the render groupings per family. Clean-inclusive
    histogram {5: 306, 4: 14, 3: 180}. The 180 are the dispatch cells (clean /
    minimal / light=standard=heavy=max); the 14 are Go ``threshold_select``
    cells, where clean and minimal coincide.
(b) spec_21_stability: cells whose render changes between the default spec 2.0
    and the opt-in spec 2.1, per profile. Minimal and light never change.
(d) comment_free_exposure: the share of the clean reference's non-comment lines
    that appear verbatim in the program, with comments (the published 0.7198) and
    in the same code with every comment removed (0.0). The exposure is carried
    entirely by the comments.
(e) code_lines: mean non-blank code lines per family and level, Python, comment-free
    (descriptive, for the paper's figure).
(f) transform_effect_at_max: how many of the 500 cells change at max when one transform is
    removed from the level. Four of the eleven have no effect under the default spec.
(g) tier_c_structures: how many Tier C structures use an operation chain the public split
    has, and how many keep the same literals under another seed.
(h) tier_c_cutoff: per Tier C structure, the share of the strongest model's refactoring
    draws with no closing code fence (bench/out/g3 archive; those outputs hit max_tokens).
(i) variants: input variants whose inputs repeat their base program, and those that change
    the list length.
(c) drop_duplicate: the mean per-sample Spearman of each Python anchor against
    the knob, under the published convention (distinct x-ranks 0..5, the
    light/standard tie living in the metric values) and with the duplicate
    standard rung dropped. Cyclomatic, cognitive and the readability proxy rise
    in magnitude; the maintainability index does not.

Inputs: bench/data/dev/*.json (frozen), config/anti_patterns_db.json,
bench/out/anchor.json (committed per-sample anchor rows),
bench/out/provenance_figures.json, and the committed Tier C completions in
bench/out/g3/.

Output: bench/out/rung_census.json (deterministic; no RNG, no clock).

Kill-switches (a mismatch means THIS script is wrong): engine renders equal the
frozen sources on all 2500 non-clean cells; the non-clean histogram equals
provenance_figures.json; published-convention means equal anchor.json (1e-9);
exposure with comments equals provenance_figures.json (1e-9).
"""

from __future__ import annotations

import collections
import copy
import glob
import gzip
import json
import os
import sys
import tempfile
from statistics import mean

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bench.suite_common import artifact_header, write_artifact  # noqa: E402
from eval import gen_samples  # noqa: E402
from eval.metrics import clean_baseline_static  # noqa: E402
from src.engine import Engine  # noqa: E402
from src.nodes.parser import parse  # noqa: E402

OUT = os.path.join(_HERE, "out", "rung_census.json")
DB = os.path.join(_ROOT, "config", "anti_patterns_db.json")
DEV_GLOB = os.path.join(_HERE, "data", "dev", "*.json")

KNOB = ["clean", "minimal", "light", "standard", "heavy", "max"]
ENGINE_PROFILES = KNOB[1:]
LANGS = ["python", "javascript", "go", "java", "cpp"]

EXPECT_WITH_CLEAN = {3: 180, 4: 14, 5: 306}


def _load_dev():
    records = [json.load(open(p)) for p in sorted(glob.glob(DEV_GLOB))]
    if len(records) != 100:
        sys.exit(f"KILL: expected 100 frozen dev records, found {len(records)}")
    return records


def _groups(renders: dict) -> str:
    """Render groupings as a canonical string, e.g. 'clean|minimal|light=max'."""
    by_text = collections.defaultdict(list)
    for prof in KNOB:
        by_text[renders[prof]].append(prof)
    return "|".join("=".join(g) for g in by_text.values())


def distinct_renders(records) -> dict:
    engines = {p: Engine(DB, p) for p in KNOB}
    with_clean = collections.Counter()
    without_clean = collections.Counter()
    per_family = collections.defaultdict(collections.Counter)
    collapsed_clean_cells = []
    for rec in records:
        rendered = {p: engines[p].generate(rec["ir"])["sources"] for p in KNOB}
        for prof in ENGINE_PROFILES:
            for lang in LANGS:
                if rendered[prof][lang] != rec["sources"][prof][lang]:
                    sys.exit(f"KILL: {rec['stem']} {prof} {lang} drifted from "
                             "the frozen render")
        for lang in LANGS:
            cell = {p: rendered[p][lang] for p in KNOB}
            with_clean[len(set(cell.values()))] += 1
            without_clean[len({cell[p] for p in ENGINE_PROFILES})] += 1
            per_family[rec["family"]][f"{lang}: {_groups(cell)}"] += 1
            if cell["clean"] == cell["minimal"]:
                collapsed_clean_cells.append(
                    {"stem": rec["stem"], "family": rec["family"], "lang": lang})
    return {
        "n_cells": sum(with_clean.values()),
        "histogram_with_clean": {str(k): with_clean[k] for k in sorted(with_clean)},
        "histogram_without_clean": {str(k): without_clean[k]
                                    for k in sorted(without_clean)},
        "groupings_per_family": {f: dict(sorted(c.items()))
                                 for f, c in sorted(per_family.items())},
        "n_clean_equals_minimal": len(collapsed_clean_cells),
        "clean_equals_minimal_cells": collapsed_clean_cells,
    }


def spec_21_stability(records) -> dict:
    default = {p: Engine(DB, p) for p in ENGINE_PROFILES}
    opt_in = {p: Engine(DB, p, spec="2.1") for p in ENGINE_PROFILES}
    changed = {p: 0 for p in ENGINE_PROFILES}
    for rec in records:
        for prof in ENGINE_PROFILES:
            a = default[prof].generate(rec["ir"])["sources"]
            b = opt_in[prof].generate(rec["ir"])["sources"]
            changed[prof] += sum(a[lang] != b[lang] for lang in LANGS)
    return {"cells_per_profile": 500, "cells_changed": changed}


def comment_free_exposure(records) -> dict:
    scored = ["minimal", "standard", "max"]
    commented = {p: Engine(DB, p) for p in scored}
    stripped = {p: Engine(DB, p, annotate=False) for p in scored}
    hits_with = hits_without = total = 0
    for rec in records:
        lines = [ln.strip() for ln in clean_baseline_static(parse(rec["ir"])).splitlines()
                 if ln.strip() and not ln.strip().startswith("#")]
        for prof in scored:
            a = commented[prof].generate(rec["ir"])["sources"]
            b = stripped[prof].generate(rec["ir"])["sources"]
            for lang in LANGS:
                hits_with += sum(ln in a[lang] for ln in lines)
                hits_without += sum(ln in b[lang] for ln in lines)
                total += len(lines)
    prov = json.load(open(os.path.join(_HERE, "out", "provenance_figures.json")))
    want = prov["exposure_report"]["exposure_static_mean"]
    if abs(hits_with / total - want) > 1e-9:
        sys.exit(f"KILL: exposure {hits_with / total:.6f} != provenance_figures {want:.6f}")
    return {"n_line_slots": total, "with_comments": hits_with / total,
            "without_comments": hits_without / total}


def code_lines(records) -> dict:
    """Mean non-blank code lines per family and level (Python, comment-free)."""
    engines = {p: Engine(DB, p, annotate=False) for p in KNOB}
    totals = collections.defaultdict(lambda: collections.defaultdict(list))
    for rec in records:
        for prof in KNOB:
            src = engines[prof].generate(rec["ir"])["sources"]["python"]
            totals[rec["family"]][prof].append(sum(1 for ln in src.splitlines() if ln.strip()))
    return {fam: {p: mean(v) for p, v in levels.items()} for fam, levels in sorted(totals.items())}


def transform_effect_at_max(records) -> dict:
    db = json.load(open(DB))
    base_engine = Engine(DB, "max")
    base = {r["stem"]: base_engine.generate(r["ir"])["sources"] for r in records}
    report = {}
    for code in db["profiles"]["max"]:
        trimmed = copy.deepcopy(db)
        trimmed["profiles"]["max"] = [c for c in trimmed["profiles"]["max"] if c != code]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(trimmed, fh)
        engine = Engine(fh.name, "max")
        report[code] = sum(engine.generate(r["ir"])["sources"][lang] != base[r["stem"]][lang]
                           for r in records for lang in LANGS)
        os.unlink(fh.name)
    report["no_effect"] = sorted(c for c, n in report.items() if n == 0)
    return report


def tier_c_structures(records) -> dict:
    a, tiers = gen_samples.build_heldout_tiers(101, tiers_version="2.0")
    b, _ = gen_samples.build_heldout_tiers(202, tiers_version="2.0")
    chain = lambda ir: tuple(op["operation"] for op in ir["operations"])  # noqa: E731
    public = {chain(r["ir"]) for r in records}
    names = sorted(s for s, t in tiers.items() if t == "C")
    if len(names) != 10:
        sys.exit(f"KILL: {len(names)} Tier C structures != 10")
    return {"n": len(names),
            "n_new_chain": sum(chain(a[s]) not in public for s in names),
            "n_seed_dependent_literals": sum(a[s]["inputs"] != b[s]["inputs"] for s in names),
            "fixed_literals": [s for s in names if a[s]["inputs"] == b[s]["inputs"]]}


def tier_c_cutoff() -> dict:
    path = os.path.join(_HERE, "out", "g3", "refactor_test__deepseek-ai-DeepSeek-V4-Flash.jsonl.gz")
    draws = collections.Counter()
    unfenced = collections.Counter()
    for line in gzip.open(path, "rt"):
        rec = json.loads(line)
        if rec.get("tier") != "C":
            continue
        for text in rec["raw_outputs"]:
            draws[rec["sample"]] += 1
            unfenced[rec["sample"]] += text.count("```") < 2
    return {"unfenced_share": {s: unfenced[s] / draws[s] for s in sorted(draws)},
            "n_draws": sum(draws.values()), "n_unfenced": sum(unfenced.values())}


def variants(records) -> dict:
    base = {r["stem"]: r for r in records if r["variant"] in ("base", None, "")}
    repeats, lengths = [], []
    for rec in records:
        if rec["stem"] in base:
            continue
        parent = base[rec["sample"]]
        if rec["ir"]["inputs"] == parent["ir"]["inputs"]:
            repeats.append(rec["stem"])
        for key, value in rec["ir"]["inputs"].items():
            if isinstance(value, list) and len(value) != len(parent["ir"]["inputs"][key]):
                lengths.append(rec["stem"])
                break
    return {"n_repeat_base_inputs": len(repeats), "repeat_base_inputs": repeats,
            "change_list_length": lengths}


def _rank(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for t in range(i, j + 1):
            ranks[order[t]] = (i + j) / 2.0
        i = j + 1
    return ranks


def _spearman(xs, ys):
    rx, ry = _rank(xs), _rank(ys)
    mx, my = mean(rx), mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else None


def drop_duplicate() -> dict:
    anchor = json.load(open(os.path.join(_HERE, "out", "anchor.json")))
    rows = collections.defaultdict(dict)
    for row in anchor["rows"]:
        rows[row["sample"]][row["knob"]] = row
    bw_rows = collections.defaultdict(dict)
    for row in anchor["bw_readability_rows"]:
        bw_rows[row["sample"]][row["knob"]] = row
    anchors = anchor["anchors"]
    published = {
        "radon_cc": (rows, anchors["radon"]["metrics"]["cc"]),
        "radon_mi": (rows, anchors["radon"]["metrics"]["mi"]),
        "cognitive_cognitive": (rows, anchors["cognitive"]["metrics"]["cognitive"]),
        "bw_proxy": (bw_rows, anchors["bw_readability"]["aggregate_density_proxy"]),
    }
    rank = {k: i for i, k in enumerate(KNOB)}
    report = {}
    for col, (source, committed) in published.items():
        full, dropped = [], []
        rose = fell = 0
        for by_knob in source.values():
            kept = [k for k in KNOB if k != "standard"]
            f = _spearman([rank[k] for k in KNOB], [by_knob[k][col] for k in KNOB])
            d = _spearman([rank[k] for k in kept], [by_knob[k][col] for k in kept])
            full.append(f)
            dropped.append(d)
            rose += abs(d) > abs(f) + 1e-9
            fell += abs(d) < abs(f) - 1e-9
        published_mean = mean(full)
        want = committed["incidental_knob_spearman_mean"]
        if abs(published_mean - want) > 1e-9:
            sys.exit(f"KILL: {col} recomputed {published_mean:.6f} != anchor.json "
                     f"{want:.6f}")
        report[col] = {
            "n_base_samples": len(full),
            "published_mean_rho": published_mean,
            "dropped_duplicate_mean_rho": mean(dropped),
            "samples_magnitude_rose": rose,
            "samples_magnitude_fell": fell,
        }
    return report


def main() -> int:
    records = _load_dev()
    census = distinct_renders(records)
    with_clean = {int(k): v for k, v in census["histogram_with_clean"].items()}
    if with_clean != EXPECT_WITH_CLEAN:
        sys.exit(f"KILL: clean-inclusive histogram {with_clean}")
    prov = json.load(open(os.path.join(_HERE, "out", "provenance_figures.json")))
    committed = {str(k): v for k, v in
                 prov["inert_cells_report"]["distinct_render_histogram"].items()}
    if census["histogram_without_clean"] != committed:
        sys.exit(f"KILL: non-clean histogram {census['histogram_without_clean']} "
                 f"!= provenance_figures {committed}")
    report = {
        "_meta": artifact_header(
            "rung_census.py",
            ["bench/data/dev/*.json", "config/anti_patterns_db.json",
             "bench/out/anchor.json", "bench/out/provenance_figures.json",
             "bench/out/g3/refactor_test__deepseek-ai-DeepSeek-V4-Flash.jsonl.gz"],
        ),
        "distinct_renders": census,
        "spec_21_stability": spec_21_stability(records),
        "drop_duplicate": drop_duplicate(),
        "comment_free_exposure": comment_free_exposure(records),
        "code_lines": code_lines(records),
        "transform_effect_at_max": transform_effect_at_max(records),
        "tier_c_structures": tier_c_structures(records),
        "tier_c_cutoff": tier_c_cutoff(),
        "variants": variants(records),
    }
    print(f"with clean {census['histogram_with_clean']}, "
          f"clean==minimal on {census['n_clean_equals_minimal']} cells")
    print(f"spec 2.1 changes {report['spec_21_stability']['cells_changed']}")
    for col, r in report["drop_duplicate"].items():
        print(f"{col}: {r['published_mean_rho']:+.4f} -> "
              f"{r['dropped_duplicate_mean_rho']:+.4f} "
              f"(rose {r['samples_magnitude_rose']}, fell {r['samples_magnitude_fell']})")
    write_artifact(OUT, report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
