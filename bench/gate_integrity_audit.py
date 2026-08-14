"""Perturbation audit of the refactor semantic gate. Zero API.

The semantic gate certifies equivalence by compiling and running the model's
rewrite against the oracle on the program's SINGLE embedded input fixture, so
a rewrite that hardcodes the expected output (a printed JSON literal in the
compiled languages; constant module-level assignments in Python) would pass
without computing anything. The committed completions are public, so anyone
can run this attack; this script runs it first.

For every PASSING draw[0] refactor completion in both arms:
1. Build a deterministically perturbed fixture for the item's IR (seeded RNG):
   integer scalars and integer-list elements get nonzero offsets; string-map
   VALUES are salted (coordinated identically with the op's ``pairs``, per the
   dataset invariant); string keys/probes are left unchanged so key-matching
   logic is untouched. The oracle is recomputed on the perturbed IR; result
   variables whose expected value did not change are counted uninformative.
2. Substitute the perturbed literals into the model's extracted code:
   name-anchored replacement (locate the fixture identifier, verify the
   original ordered literal sequence next to it modulo whitespace, rewrite the
   elements in place); quoted map-value strings are replaced globally (they
   are distinctive salted tokens). Items where any required literal region
   cannot be located and verified are reported as an explicit
   ``unsubstitutable`` stratum, never silently dropped.
3. Re-run through the SAME execution paths as grading (run_python_untrusted /
   validator.validate) against the perturbed oracle, and classify:
   ``perturbed_pass`` (recomputes correctly: certified),
   ``hardcode_signal`` (output equals the OLD expectation: the cheat the gate
   cannot see), ``perturbed_fail_other`` (neither: fragile rewrite or
   substitution damage; the ``got`` payload is recorded for inspection).

Post-hoc addendum (ANALYSIS_PLAN.md; estimation only). Kill-switch: UNperturbed
re-execution of a seeded subsample must reproduce the committed grade cache
exactly (a mismatch means THIS script's rebuild/execution path is wrong).

POLICY: nonzero hardcode rates implicate published numbers; if found, stop and
report before any paper change.
"""

from __future__ import annotations

import copy
import json
import os
import random
import re
import sys
import zlib
from concurrent.futures import ThreadPoolExecutor

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bench import dataset as D  # noqa: E402
from bench import grade as G  # noqa: E402
from bench.annotation_ablation import (  # noqa: E402
    MODELS,
    _key as item_key,
    _load_annotated as load_annotated,
    _load_unannotated as load_unannotated,
)
from bench.suite_common import (  # noqa: E402
    artifact_header,
    write_artifact,
)
from src.nodes.parser import parse  # noqa: E402
from src.nodes.validator import oracle, validate  # noqa: E402

CACHE = os.path.join(_HERE, "out", ".annotation_ablation_grades.json")
OUT = os.path.join(_HERE, "out", "gate_integrity.json")
AUDIT_SEED = 20260814
KILL_SUBSAMPLE = 200
WORKERS = 4
SALT = "_px"


# --------------------------------------------------------------------------- #
# fixture perturbation
# --------------------------------------------------------------------------- #
_CMP = {"==": lambda a, b: a == b, "!=": lambda a, b: a != b,
        "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
        ">": lambda a, b: a > b, ">=": lambda a, b: a >= b}


def perturb_ir(ir: dict, rng: random.Random) -> dict:
    """Deterministically perturbed deep copy; maps coordinated with pairs.

    Beyond value offsets/salts, the recipe FLIPS boolean-bearing ops through
    their named scalar inputs so the expected output changes for as many
    result variables as possible: a MEMBERSHIP_CHECK probe is moved into or
    out of the (perturbed) collection, and a CONDITIONAL_SELECT subject is
    moved across its comparator boundary. Only named INPUTS are touched, so
    code substitution stays name-anchored."""
    out = copy.deepcopy(ir)
    for name, value in list(out["inputs"].items()):
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            out["inputs"][name] = value + rng.randrange(3, 97)
        elif isinstance(value, list) and all(
                isinstance(v, int) and not isinstance(v, bool)
                for v in value):
            out["inputs"][name] = [v + rng.randrange(3, 97) for v in value]
        elif isinstance(value, list) and all(isinstance(v, str)
                                             for v in value):
            out["inputs"][name] = [v + SALT for v in value]
        elif isinstance(value, dict):
            out["inputs"][name] = {
                k: (v + rng.randrange(3, 97)
                    if isinstance(v, int) and not isinstance(v, bool)
                    else (v + SALT if isinstance(v, str) else v))
                for k, v in value.items()}
    # op-aware flips + literal coordination
    for op in out["operations"]:
        kind = op.get("operation")
        if kind == "MEMBERSHIP_CHECK":
            coll = out["inputs"].get(op["collection_name"])
            tgt = op["target_var"]
            old_probe = ir["inputs"].get(tgt)
            if (isinstance(coll, list) and coll and tgt in out["inputs"]
                    and not isinstance(old_probe, bool)):
                was_in = ir["inputs"][tgt] in ir["inputs"].get(
                    op["collection_name"], [])
                if was_in:  # flip to a guaranteed miss
                    out["inputs"][tgt] = (max(coll) + rng.randrange(3, 97)
                                          if isinstance(coll[0], int)
                                          else "absent" + SALT)
                else:       # flip to a hit in the perturbed collection
                    out["inputs"][tgt] = coll[rng.randrange(len(coll))]
        elif kind == "KEY_VALUE_LOOKUP":
            op["pairs"] = copy.deepcopy(out["inputs"][op["map_name"]])
            if isinstance(op.get("default_value"), str):
                op["default_value"] = op["default_value"] + SALT
        elif kind == "CONDITIONAL_SELECT":
            for fld in ("then_value", "else_value"):
                if isinstance(op.get(fld), str):
                    op[fld] = op[fld] + SALT
            sv, cv = op["subject_var"], op["compare_value"]
            old_subject = ir["inputs"].get(sv)
            if (sv in out["inputs"] and isinstance(old_subject, int)
                    and not isinstance(old_subject, bool)
                    and isinstance(cv, int)):
                was = _CMP[op["comparator"]](old_subject, cv)
                if op["comparator"] in ("==", "!="):
                    flip = (cv if (was == (op["comparator"] == "!="))
                            else cv + rng.randrange(3, 97))
                else:
                    flip = (cv - rng.randrange(1, 50)
                            if _CMP[op["comparator"]](cv - 1, cv) != was
                            else cv + rng.randrange(1, 50))
                    if _CMP[op["comparator"]](flip, cv) == was:
                        flip = 2 * cv - flip
                out["inputs"][sv] = flip
    return out


# --------------------------------------------------------------------------- #
# literal substitution in model code
# --------------------------------------------------------------------------- #
_NUM = r"-?\d+"


def _sub_scalar_int(src: str, name: str, old: int, new: int):
    """Replace the first integer literal equal to ``old`` in the statement
    that introduces ``name``."""
    pat = re.compile(rf"(\b{re.escape(name)}\b[^;\n]*?)(?<![\w.]){old}\b")
    out, n = pat.subn(lambda m: m.group(1) + str(new), src, count=1)
    return (out, True) if n == 1 else (src, False)


def _sub_int_list(src: str, name: str, old: list, new: list):
    """Locate the ordered element sequence of ``old`` after ``name`` and
    rewrite it elementwise (whitespace/newline tolerant)."""
    idx = src.find(name)
    if idx < 0:
        return src, False
    body = re.compile(r"[\s,]+".join(rf"(?<![\w.]){v}\b" for v in old))
    m = body.search(src, idx)
    if not m:
        return src, False
    seg = m.group(0)
    parts = re.split(r"([\s,]+)", seg)
    vals = iter(new)
    rebuilt = "".join(str(next(vals)) if re.fullmatch(_NUM, p) else p
                      for p in parts)
    return src[:m.start()] + rebuilt + src[m.end():], True


def _sub_scalar_str(src: str, name: str, old: str, new: str):
    """Replace the quoted string ``old`` in the statement introducing
    ``name``."""
    pat = re.compile(rf"(\b{re.escape(name)}\b[^;\n]*?)(['\"])"
                     rf"{re.escape(old)}\2")
    out, n = pat.subn(lambda m: m.group(1) + m.group(2) + new + m.group(2),
                      src, count=1)
    return (out, True) if n == 1 else (src, False)


def _sub_strings(src: str, olds_to_news: dict):
    """Global quoted-string replacement (map values are distinctive)."""
    ok = True
    for old, new in olds_to_news.items():
        found = False
        for q in ('"', "'"):
            if q + old + q in src:
                src = src.replace(q + old + q, q + new + q)
                found = True
        ok = ok and found
    return src, ok


def substitute(src: str, ir: dict, pert: dict):
    """Apply all input perturbations to the model source; return
    (new_src, ok, n_missed)."""
    missed = 0
    str_map: dict = {}
    for name, old in ir["inputs"].items():
        new = pert["inputs"][name]
        if old == new:
            continue
        if isinstance(old, bool):
            continue
        if isinstance(old, int):
            src, ok = _sub_scalar_int(src, name, old, new)
            missed += 0 if ok else 1
        elif isinstance(old, str):
            src, ok = _sub_scalar_str(src, name, old, new)
            missed += 0 if ok else 1
        elif isinstance(old, list) and old and isinstance(old[0], str):
            for ov, nv in zip(old, new):
                if ov != nv:
                    str_map[ov] = nv
        elif isinstance(old, list):
            src, ok = _sub_int_list(src, name, old, new)
            missed += 0 if ok else 1
        elif isinstance(old, dict):
            for k in old:
                ov, nv = old[k], new[k]
                if ov == nv:
                    continue
                if isinstance(ov, str):
                    str_map[ov] = nv
                else:
                    src, ok = _sub_scalar_int(src, k, ov, nv) if False \
                        else (src, False)
                    # int map values: replace by quoted-key anchor is
                    # unreliable; fall back to counting as missed
                    missed += 1
    # op-literal strings (then/else, default) also need renaming in the code
    for op, pop in zip(ir["operations"], pert["operations"]):
        for fld in ("then_value", "else_value", "default_value"):
            ov, nv = op.get(fld), pop.get(fld)
            if isinstance(ov, str) and isinstance(nv, str) and ov != nv:
                str_map[ov] = nv
    if str_map:
        src, ok = _sub_strings(src, str_map)
        missed += 0 if ok else 1
    return src, missed == 0, missed


# --------------------------------------------------------------------------- #
# execution + classification
# --------------------------------------------------------------------------- #
def _got_from_detail(detail: str):
    i = detail.find(" got ")
    return G.extract_json_obj(detail[i + 5:]) if i >= 0 else None


def run_and_classify(language: str, src: str, prog_new, exp_old: dict,
                     exp_new: dict) -> str:
    if language == "python":
        ok, val = G.run_python_untrusted(src, list(exp_new))
        if ok and G._match(val, exp_new):
            return "perturbed_pass"
        if ok and G._match(val, exp_old):
            return "hardcode_signal"
        return "perturbed_fail_other"
    res = validate(language, src, prog_new)
    if res.status == "PASS":
        return "perturbed_pass"
    got = _got_from_detail(res.detail or "")
    if got is not None and G._match(got, exp_old):
        return "hardcode_signal"
    return "perturbed_fail_other"


def main() -> int:
    cache = json.load(open(CACHE))
    sp = D.load("dev")
    from bench.tasks import _stem_for  # late import; needs the Split

    report: dict = {"per_model": {}}
    kill_done = 0

    for short, slug in MODELS:
        ann1 = cache["refactor"][short]["ann1"]
        un1 = cache["refactor"][short]["un1"]
        for arm, loader in (("annotated", lambda s=slug:
                             load_annotated("refactor", s)),
                            ("unannotated", lambda s=slug:
                             load_unannotated("refactor", s))):
            grades = ann1 if arm == "annotated" else un1
            recs = [r for r in loader() if grades.get(item_key(r)) == 1.0]

            def work(r):
                stem = _stem_for(sp, r["sample"], r.get("variant", "base"))
                ir = sp.ir(stem)
                tag = f"{stem}|{r['profile']}|{r['language']}"
                rng = random.Random(
                    (AUDIT_SEED << 1) ^ zlib.crc32(tag.encode()))
                pert = perturb_ir(ir, rng)
                prog_old, prog_new = parse(ir), parse(pert)
                exp_old, exp_new = oracle(prog_old), oracle(prog_new)
                informative = [v for v in exp_new
                               if json.dumps(exp_new[v], sort_keys=True)
                               != json.dumps(exp_old[v], sort_keys=True)]
                src0 = G.extract_code(r["raw_outputs"][0])
                src1, ok, missed = substitute(src0, ir, pert)
                if not informative:
                    return ("uninformative_perturbation", r["profile"])
                if not ok:
                    return ("unsubstitutable", r["profile"])
                return (run_and_classify(r["language"], src1, prog_new,
                                         exp_old, exp_new), r["profile"])

            with ThreadPoolExecutor(max_workers=WORKERS) as ex:
                rows = list(ex.map(work, recs))
            import collections
            by = collections.Counter(x[0] for x in rows)
            byp = collections.Counter(f"{x[0]}|{x[1]}" for x in rows)
            blk = report["per_model"].setdefault(short, {})
            blk[arm] = {"n_passing": len(recs),
                        "outcomes": dict(sorted(by.items())),
                        "outcomes_by_profile": dict(sorted(byp.items()))}
            print(f"{short:18s} {arm:11s} n={len(recs):4d} {dict(by)}",
                  flush=True)

        # KILL: unperturbed re-execution reproduces the cache on a subsample
        rng = random.Random(AUDIT_SEED)
        recs_all = list(load_annotated("refactor", slug))
        sample = rng.sample(recs_all, min(KILL_SUBSAMPLE // len(MODELS) + 1,
                                          len(recs_all)))
        from bench.tasks import _rebuild_refactor_item
        for r in sample:
            item = _rebuild_refactor_item(r)
            src = G.extract_code(r["raw_outputs"][0])
            ok, _detail = G.semantic_ok(r["language"], src, item.program)
            want = ann1.get(item_key(r))
            if ok is None or want is None:
                continue
            assert float(bool(ok)) == want, (
                f"KILL: unperturbed re-execution {item_key(r)}: "
                f"{ok} != cache {want}")
            kill_done += 1
    print(f"KILL passed: {kill_done} unperturbed re-executions match the "
          "cache", flush=True)

    report["_meta"] = artifact_header(
        "gate_integrity_audit.py",
        ["bench/out/.annotation_ablation_grades.json",
         "bench/out/g3/refactor_dev__*.jsonl.gz",
         "bench/out/ablation/refactor_unannotated__*.jsonl.gz"],
        estimation_not_family=True,
        audit_seed=AUDIT_SEED,
        salt=SALT,
        post_hoc=("registered 2026-08-14 after the suite's results were "
                  "read; certifies the one-fixture semantic gate against "
                  "hardcoded-output rewrites (ANALYSIS_PLAN.md post-hoc "
                  "addenda)"),
    )
    write_artifact(OUT, report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
