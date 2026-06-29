"""Analyze the G3 run: comprehend x test (contamination / generalization by novelty tier)
and refactor x {dev,test} (structure-vs-computation contrast).

Zero new API. Reads the finalized batch files written by run_bench.py:

* comprehend dev   : bench/out/ladder_comprehend.json  (already re-graded from out/ladder/)
* comprehend test  : bench/out/subagent/comprehend__<slug>__test.json
* refactor dev     : bench/out/subagent/refactor__<slug>.json
* refactor test    : bench/out/subagent/refactor__<slug>__test.json

The private test split carries three novelty tiers:
  A = re-minted dev families (public structure, secret values)  -> value-memorization probe
  B = held-out op-type SEQUENCES (never public)                 -> structural generalization
  C = out-of-distribution scale / nesting depth                 -> distribution shift

Contamination reading: if dev(overall) ~= test(A) but >> test(B/C), the model handles known
structures and degrades only on genuinely novel ones -- the expected, non-contaminated shape.
A dev >> test(A) gap would instead flag memorization of specific public instances.

Usage: python3 bench/g3_analysis.py
"""
from __future__ import annotations

import collections
import gzip
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:  # so the lazy gz re-grade can import bench.tasks
    sys.path.insert(0, _ROOT)

SUB = os.path.join(_HERE, "out", "subagent")
OUT = os.path.join(_HERE, "out")
G3_DATA = os.path.join(_HERE, "out", "g3")  # committed compact gzipped raw completions

# label -> on-disk slug (model id with '/' -> '-')
LADDER = [
    ("Llama-3.1-8B", "meta-llama-Meta-Llama-3.1-8B-Instruct"),
    ("Mistral-24B", "mistralai-Mistral-Small-3.2-24B-Instruct-2506"),
    ("Llama-3.3-70B", "meta-llama-Llama-3.3-70B-Instruct-Turbo"),
    ("DeepSeek-V4-Flash", "deepseek-ai-DeepSeek-V4-Flash"),
]
_SCALE_TOK = re.compile(r"_[A-Za-z]+\d+$")  # trailing scale token e.g. _W128, _N16, _L8, _D12


def _family(sample: str) -> str:
    return _SCALE_TOK.sub("", sample)


def _tier(rec: dict) -> str:
    t = rec.get("tier")
    return t if t in ("A", "B", "C") else "A"  # untiered dev families == Tier A


def _metric(rec: dict):
    """Per-item score field. comprehend -> exact_match_rate; refactor uses the same key
    (mean pass over k samples) in this harness."""
    for k in ("exact_match_rate", "pass_rate", "equiv_rate", "score"):
        if k in rec and rec[k] is not None:
            return rec[k]
    return None


def _mean(v):
    return round(sum(v) / len(v), 4) if v else None


def _load_final(task: str, slug: str, split: str):
    suff = "" if split == "dev" else f"__{split}"
    f = os.path.join(SUB, f"{task}__{slug}{suff}.json")
    if not os.path.exists(f):
        return None
    return json.load(open(f)).get("items", [])


def _by_tier(items):
    """tier -> list of per-item scores; plus per-(family,scale) for agg_stats."""
    tier = collections.defaultdict(list)
    fam = collections.defaultdict(list)
    agg_W = collections.defaultdict(list)  # W -> scores for agg_stats family
    for r in items:
        m = _metric(r)
        if m is None:
            continue
        tier[_tier(r)].append(m)
        fam[_family(r.get("sample", "?"))].append(m)
        if _family(r.get("sample", "")) == "agg_stats":
            W = (r.get("intrinsic") or {}).get("W")
            if W is not None:
                agg_W[W].append(m)
    return tier, fam, agg_W


def comprehend_report():
    dev = json.load(open(os.path.join(OUT, "ladder_comprehend.json")))
    rows = {}
    for label, slug in LADDER:
        items = _load_final("comprehend", slug, "test")
        d = dev.get(label, {})
        row = {"dev_overall": d.get("overall_exact_match"), "dev_agg_stats": d.get("per_family", {}).get("agg_stats")}
        if items:
            tier, fam, _ = _by_tier(items)
            allm = [m for v in tier.values() for m in v]
            row.update({
                "test_overall": _mean(allm), "n_test": len(allm),
                "test_A": _mean(tier.get("A", [])), "test_B": _mean(tier.get("B", [])),
                "test_C": _mean(tier.get("C", [])),
                "test_agg_stats": _mean(fam.get("agg_stats", [])),
            })
            da, ta = row["dev_overall"], row["test_A"]
            row["dev_minus_testA"] = round(da - ta, 4) if (da is not None and ta is not None) else None
            row["testA_minus_testB"] = round(ta - row["test_B"], 4) if (ta is not None and row["test_B"] is not None) else None
            row["testA_minus_testC"] = round(ta - row["test_C"], 4) if (ta is not None and row["test_C"] is not None) else None
        rows[label] = row
    return rows


def _nops(rec) -> object:
    return (rec.get("intrinsic") or {}).get("n_ops")


def _tier_nops_cells(items):
    """(tier -> n_ops -> [per-item EM]) for the comprehend test items."""
    cell = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in items:
        m, n = _metric(r), _nops(r)
        if m is None or n is None:
            continue
        cell[_tier(r)][n].append(m)
    return cell


def _matched_gap(cell_a: dict, cell_b: dict) -> dict:
    """The op-count-matched (directly standardized) Tier-A minus Tier-B accuracy gap.

    The raw A-B gap is confounded: the tiers do not share an n_ops mix (Tier A is mostly
    trivial single-op items; Tier B has none). We re-weight Tier-A's per-n_ops EM by Tier-B's
    own n_ops distribution over the strata both tiers populate, then subtract Tier-B's mean on
    those strata. The result isolates "is A more accurate at a FIXED op count" from "does A
    just contain easier op counts". ``coverage`` is the fraction of Tier-B items that fall in
    shared strata (the standardization's support)."""
    shared = sorted(set(cell_a) & set(cell_b), key=lambda x: (x is None, x))
    per, a_std, b_obs, nb = {}, 0.0, 0.0, 0
    for n in shared:
        a, b = cell_a[n], cell_b[n]
        ma, mb = sum(a) / len(a), sum(b) / len(b)
        per[str(n)] = {"A": round(ma, 4), "B": round(mb, 4), "gap": round(ma - mb, 4),
                       "nA": len(a), "nB": len(b)}
        a_std += len(b) * ma
        b_obs += len(b) * mb
        nb += len(b)
    nb_total = sum(len(v) for v in cell_b.values())
    return {
        "per_nops": per,
        "A_std_under_B_mix": round(a_std / nb, 4) if nb else None,
        "B_mean_shared": round(b_obs / nb, 4) if nb else None,
        "matched_gap": round((a_std - b_obs) / nb, 4) if nb else None,
        "coverage": round(nb / nb_total, 4) if nb_total else 0.0,
    }


def _tier_family_cells(items):
    """tier -> family -> [per-item EM] for the comprehend test items."""
    cell = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in items:
        m = _metric(r)
        if m is None:
            continue
        cell[_tier(r)][_family(r.get("sample", "?"))].append(m)
    return cell


def tier_nops_matched():
    """Decompose the comprehend test A>B/C tier gaps to show they are operation-mix
    composition, NOT a broad novelty penalty (the 'shown evidence' the resource/limitations
    sections lean on). Per model emits: the raw per-tier EM and gaps, the Tier-A single-op
    fraction, the per-(tier, n_ops) EM grid, the n_ops-matched A-B / A-C gaps (see
    _matched_gap), and the per-(tier, family) EM grid.

    On the matched evidence: n_ops standardization shrinks the raw A-B gap by ~60-90% and
    reverses it at n_ops=3. NOTE we deliberately do NOT emit a single 'aggregation tax'
    residual: Tier A and Tier B share no families, and 'contains an aggregate op' does not
    cleanly predict failure (e.g. aggregate-then-lookup compositions score ~1.0 because the
    downstream op masks the exact sum), so a forced agg-vs-non-agg gap would be a confound.
    The per-(tier, family) grid is released instead as inspectable evidence: most held-out
    Tier-B/C structures score ~1.0 (no novelty penalty) while the failures concentrate in
    aggregation-bearing and very-deep compositions -- the same computation tax seen on dev.

    Sourced from the graded comprehend test finalize files; these require the private held-out
    seed to re-derive from raw, per the contamination design."""
    rep = {}
    for label, slug in LADDER:
        items = _load_final("comprehend", slug, "test")
        if not items:
            continue
        cell = _tier_nops_cells(items)
        fam = _tier_family_cells(items)
        raw = {t: _mean([m for v in cell[t].values() for m in v]) for t in cell}
        n_a = sum(len(v) for v in cell.get("A", {}).values())
        n_a1 = len(cell.get("A", {}).get(1, []))
        rep[label] = {
            "raw_tier_EM": {t: raw[t] for t in sorted(raw)},
            "raw_gap_AB": round(raw["A"] - raw["B"], 4) if {"A", "B"} <= set(raw) else None,
            "raw_gap_AC": round(raw["A"] - raw["C"], 4) if {"A", "C"} <= set(raw) else None,
            "tierA_frac_single_op": round(n_a1 / n_a, 4) if n_a else None,
            "per_tier_nops_EM": {t: {str(n): {"em": _mean(cell[t][n]), "n": len(cell[t][n])}
                                     for n in sorted(cell[t])} for t in sorted(cell)},
            "matched_AB": _matched_gap(dict(cell.get("A", {})), dict(cell.get("B", {}))),
            "matched_AC": _matched_gap(dict(cell.get("A", {})), dict(cell.get("C", {}))),
            "per_tier_family_EM": {t: {f: {"em": _mean(fam[t][f]), "n": len(fam[t][f])}
                                       for f in sorted(fam[t])} for t in sorted(fam)},
        }
    return rep


def _refactor_agg(items):
    """tier -> {recovered, semok, uq} score lists; plus agg_stats W -> {recovered, semok}.
    The refactor headline is recovered_rate (equivalent AND within the optimality band);
    semantic_ok_rate is the looser compile-run-equivalent gate; uniform_quality is the
    cross-language-poolable simplification score."""
    tier = collections.defaultdict(lambda: {"recovered": [], "semok": [], "uq": []})
    agg_W = collections.defaultdict(lambda: {"recovered": [], "semok": []})
    for r in items:
        rr, so = r.get("recovered_rate"), r.get("semantic_ok_rate")
        if rr is None and so is None:   # fully skipped (toolchain absent for the language)
            continue
        t = _tier(r)
        if rr is not None:
            tier[t]["recovered"].append(rr)
        if so is not None:
            tier[t]["semok"].append(so)
        uq = r.get("uniform_quality")
        if uq is not None:
            tier[t]["uq"].append(uq)
        if _family(r.get("sample", "")) == "agg_stats":
            W = (r.get("intrinsic") or {}).get("W")
            if W is not None:
                if rr is not None:
                    agg_W[W]["recovered"].append(rr)
                if so is not None:
                    agg_W[W]["semok"].append(so)
    return tier, agg_W


def _regrade_refactor_gz(path: str) -> list:
    """Re-grade refactor raw completions from a committed out/g3/*.jsonl.gz with ZERO API
    (it re-runs the validator/oracle on the persisted model outputs). Mirrors
    ladder_analysis.py's gz re-grade for the comprehend ladder. ``recovered_rate`` and
    ``semantic_ok_rate`` reproduce on the base interpreter; ``uniform_quality`` needs the
    metrics venv (lizard/radon) and is otherwise null -- the same scope ladder_analysis has."""
    from bench import tasks as T  # lazy: pulls in the engine + grader stack only when needed
    items = []
    with gzip.open(path, "rt") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            rec.setdefault("split", "dev")
            items.append(T.regrade_refactor_record(rec))
    return items


def _load_refactor(slug: str, split: str):
    """(items, run_meta) for one model's refactor split. Prefer the already-graded finalize
    JSON under out/subagent/ (fast); if absent and split == 'dev', re-grade the committed raw
    completions under out/g3/ so the refactor x dev ladder reproduces from version control
    without the large finalize JSONs. refactor x test is NOT VC-reproducible: rebuilding its
    oracle needs the private held-out seed (the contamination design, see out/g3/README.md),
    so for test we only read an existing finalize file."""
    suff = "" if split == "dev" else f"__{split}"
    f = os.path.join(SUB, f"refactor__{slug}{suff}.json")
    if os.path.exists(f):
        full = json.load(open(f))
        return full.get("items", []), full.get("run_meta", {})
    if split == "dev":
        g = os.path.join(G3_DATA, f"refactor_dev__{slug}.jsonl.gz")
        if os.path.exists(g):
            its = _regrade_refactor_gz(g)
            return its, {"n_errors": 0, "n_items": len(its), "source": "gz-regrade"}
    return None, None


def refactor_report():
    rows = {}
    for label, slug in LADDER:
        row = {}
        for split in ("dev", "test"):
            items, rm = _load_refactor(slug, split)
            if items is None:
                continue
            n_err, n_it = rm.get("n_errors", 0), rm.get("n_items", len(items))
            tier, agg_W = _refactor_agg(items)
            # A batch that ran out of API balance mid-run leaves mostly error stubs; flag
            # it incomplete instead of reporting a biased/empty aggregate.
            if n_err > 0.05 * max(n_it, 1) or not any(tier[t]["semok"] for t in tier):
                row[split] = {"incomplete": True, "n_errors": n_err, "n_items": n_it,
                              "n_valid": sum(len(tier[t]["semok"]) for t in tier),
                              "tiers_present": sorted(t for t in tier if tier[t]["semok"])}
                continue
            allrec = [m for d in tier.values() for m in d["recovered"]]
            allsem = [m for d in tier.values() for m in d["semok"]]
            row[split] = {
                "n": len(allrec), "recovered": _mean(allrec), "semantic_ok": _mean(allsem),
                "recovered_by_tier": {t: _mean(tier[t]["recovered"]) for t in sorted(tier)},
                "semantic_ok_by_tier": {t: _mean(tier[t]["semok"]) for t in sorted(tier)},
                # structure-vs-computation: recovered_rate should stay ~flat across W,
                # unlike comprehend agg_stats EM which collapses as W grows.
                "agg_stats_recovered_by_W": {str(W): _mean(agg_W[W]["recovered"]) for W in sorted(agg_W)},
            }
        if row:
            rows[label] = row
    return rows


def structure_vs_computation():
    """The HONEST structure-vs-computation contrast (the recovered_rate 'flat 0.20' is a
    Python-AST-lane artifact: recovered is 1.0 for python and 0.0 for the other 4 langs ->
    mean 1/5). The real, all-language signal is semantic_ok: for agg_stats the model must
    only RESTRUCTURE (semantic_ok ~1.0, flat across W) whereas comprehend must COMPUTE the
    sum (exact-match collapses as W grows). Computed on dev (full W grid, all 4 models)."""
    scaling = json.load(open(os.path.join(OUT, "ladder_scaling.json")))  # comprehend dev EM by W
    rep = {}
    for label, slug in LADDER:
        items, _ = _load_refactor(slug, "dev")
        if not items:
            continue
        agg = [it for it in items if _family(it.get("sample", "")) == "agg_stats"]
        semok_W, rec_lang = collections.defaultdict(list), collections.defaultdict(list)
        for it in agg:
            W = (it.get("intrinsic") or {}).get("W")
            if it.get("semantic_ok_rate") is not None and W is not None:
                semok_W[W].append(it["semantic_ok_rate"])
            if it.get("recovered_rate") is not None:
                rec_lang[it.get("language", "?")].append(it["recovered_rate"])
        rep[label] = {
            "comprehend_EM_by_W": scaling.get(label, {}).get("agg_stats", {}),
            "refactor_semok_by_W": {str(W): _mean(semok_W[W]) for W in sorted(semok_W)},
            "refactor_recovered_by_language": {L: _mean(rec_lang[L]) for L in sorted(rec_lang)},
        }
    return rep


def main() -> int:
    comp = comprehend_report()
    nops = tier_nops_matched()
    refac = refactor_report()
    svc = structure_vs_computation()
    print("=== comprehend: dev vs private-test by novelty tier ===")
    hdr = "%-18s %8s %8s | %6s %6s %6s | %9s %9s" % (
        "model", "dev", "testAll", "A", "B", "C", "dev-A", "A-B")
    print(hdr)
    for label, _ in LADDER:
        r = comp.get(label, {})
        if "test_overall" not in r:
            print("%-18s %8s   (no test final yet)" % (label, r.get("dev_overall")))
            continue
        print("%-18s %8.4f %8.4f | %6s %6s %6s | %9s %9s" % (
            label, r["dev_overall"], r["test_overall"],
            r["test_A"], r["test_B"], r["test_C"], r["dev_minus_testA"], r["testA_minus_testB"]))
    if refac:
        print("\n=== refactor: recovered_rate (semantic_ok) by split/tier ===")
        for label, _ in LADDER:
            r = refac.get(label)
            if not r:
                continue
            for split in ("dev", "test"):
                if split not in r:
                    continue
                s = r[split]
                if s.get("incomplete"):
                    print("%-18s %-4s INCOMPLETE (%d/%d valid, %d errored; tiers=%s)" % (
                        label, split, s["n_valid"], s["n_items"], s["n_errors"],
                        ",".join(s["tiers_present"]) or "none"))
                    continue
                rt, st = s["recovered_by_tier"], s["semantic_ok_by_tier"]
                cell = lambda t: "%s(%s)" % (rt.get(t), st.get(t)) if t in rt else "-"
                print("%-18s %-4s recovered=%.3f semok=%.3f n=%d | A=%s B=%s C=%s" % (
                    label, split, s["recovered"], s["semantic_ok"], s["n"],
                    cell("A"), cell("B"), cell("C")))
    print("\n=== structure vs computation (agg_stats, dev): restructure is W-invariant, compute is not ===")
    for label, _ in LADDER:
        s = svc.get(label)
        if not s:
            continue
        em = s["comprehend_EM_by_W"]
        ws = sorted(em, key=int)
        comp_str = " ".join("W%s=%.2f" % (w, em[w]) for w in (ws[0], ws[len(ws) // 2], ws[-1])) if ws else "-"
        sk = s["refactor_semok_by_W"]
        sks = sorted(sk, key=int)
        sem_str = " ".join("W%s=%.2f" % (w, sk[w]) for w in (sks[0], sks[len(sks) // 2], sks[-1])) if sks else "-"
        print("%-18s comprehend EM: %-28s | refactor semok: %-28s" % (label, comp_str, sem_str))
        print("%-18s   (recovered_rate is a python-lane artifact: %s)" % ("", s["refactor_recovered_by_language"]))

    if nops:
        print("\n=== tier gap vs op-count composition (comprehend test): A>B is op-count, not novelty ===")
        print("%-18s %8s %8s %10s | %s" % ("model", "raw A-B", "matched", "A 1-op%", "per-n_ops A/B (gap)"))
        for label, _ in LADDER:
            r = nops.get(label)
            if not r:
                continue
            mab = r["matched_AB"]
            cells = " ".join("n%s:%.2f/%.2f(%+.2f)" % (n, c["A"], c["B"], c["gap"])
                             for n, c in mab["per_nops"].items())
            print("%-18s %8s %8s %9s%% | %s" % (
                label, r["raw_gap_AB"], mab["matched_gap"],
                round(100 * (r["tierA_frac_single_op"] or 0), 1), cells))
            # held-out failure concentration: most novel structures are handled (~1.0); the
            # failures are the aggregation-bearing / very-deep compositions, not novelty.
            fb = (r.get("per_tier_family_EM") or {}).get("B", {})
            if fb:
                hi = [f for f, c in fb.items() if (c["em"] or 0) >= 0.9]
                lo = [f for f, c in fb.items() if (c["em"] or 0) < 0.9]
                print("%-18s   Tier-B: %d/%d held-out families >=0.90 EM; failures: %s" % (
                    "", len(hi), len(fb), ", ".join(sorted(lo)) or "none"))

    payload = {"comprehend": comp, "tier_nops_matched": nops,
               "refactor": refac, "structure_vs_computation": svc}
    out_path = os.path.join(OUT, "g3_analysis.json")
    # The private-test sections (comprehend tiers, tier_nops_matched, refactor test) are only
    # computable from the held-out seed's finalize files. A run WITHOUT them (e.g. a fresh
    # third-party checkout) would otherwise clobber the committed released numbers with a
    # dev-only subset. Guard: only overwrite the committed artifact when the test sections are
    # present; otherwise write a .partial sidecar and leave the released file intact.
    complete = bool(nops) and all("test_overall" in comp.get(label, {}) for label, _ in LADDER)
    if complete or not os.path.exists(out_path):
        json.dump(payload, open(out_path, "w"), indent=2)
        print("\nwrote out/g3_analysis.json")
    else:
        part = os.path.join(OUT, "g3_analysis.partial.json")
        json.dump(payload, open(part, "w"), indent=2)
        print("\n[guard] private-test sections absent (no comprehend-test finalize files) -> "
              "wrote out/g3_analysis.partial.json and LEFT the committed out/g3_analysis.json intact.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
