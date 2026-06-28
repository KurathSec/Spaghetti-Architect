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
import json
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
SUB = os.path.join(_HERE, "out", "subagent")
OUT = os.path.join(_HERE, "out")

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


def refactor_report():
    rows = {}
    for label, slug in LADDER:
        row = {}
        for split in ("dev", "test"):
            suff = "" if split == "dev" else f"__{split}"
            f = os.path.join(SUB, f"refactor__{slug}{suff}.json")
            if not os.path.exists(f):
                continue
            full = json.load(open(f))
            items = full.get("items", [])
            rm = full.get("run_meta", {})
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
        items = _load_final("refactor", slug, "dev")
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

    json.dump({"comprehend": comp, "refactor": refac, "structure_vs_computation": svc},
              open(os.path.join(OUT, "g3_analysis.json"), "w"), indent=2)
    print("\nwrote out/g3_analysis.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
