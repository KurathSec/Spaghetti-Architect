"""Failure-stage decomposition of the annotation-ablation delta. Zero API.

Alternative explanation to exclude: the unannotated arm's deficit (and, after
unannotated_slopes.py, the unannotated arm's negative incidental-knob slope)
could be grader-side format brittleness rather than semantic failure -- without
the header and comments, models might emit prose or broken output that the
pipeline scores as a semantic miss. The committed raw completions decide this
offline.

Refactor: for every FAILING draw[0] completion in both arms, re-run the exact
semantic gate (bench.grade.semantic_ok on the rebuilt oracle program) keeping
the stage-bearing ``detail`` string, and classify:
  no_fenced_block (mechanical pre-class: no ``` fence in the raw text),
  timeout, runtime_error, no_stdout, unparseable_stdout (Python);
  compile_error vs run_error where the toolchain separates them (g++/javac vs
  the run step; ``go run`` conflates both and is reported as the merged stage
  go_compile_or_run); wrong_output (ran, mismatched values).
Comprehend (no execution): re-extract the predicted JSON, recompute gold from
the oracle, and classify parse_fail / wrong_keys / recognition (every wrong
variable's value is consistent with a WRONG operation's semantics: negated
membership, miss-vs-hit lookup confusion, swapped aggregate mode or len, the
other conditional branch) / near_miss (every wrong variable an AGGREGATE value
within 10% relative error) / mixed_semantic.

Everything is stratified by model x arm x knob profile (the stratum that
matters for the unannotated-slope finding) and by language, and the INCREMENTAL
failures (annotated pass -> unannotated fail), which carry the ablation delta,
are decomposed separately.

Post-hoc addendum (ANALYSIS_PLAN.md; estimation only). Kill-switches: the
re-derived gate/EM must equal the committed grade cache exactly (every
comprehend item both arms; every re-executed failing refactor item), and stage
counts must sum to the cache's failure counts.
"""

from __future__ import annotations

import collections
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bench import grade as G  # noqa: E402
from bench import tasks as T  # noqa: E402
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
from src.nodes.validator import oracle  # noqa: E402

CACHE = os.path.join(_HERE, "out", ".annotation_ablation_grades.json")
OUT = os.path.join(_HERE, "out", "failure_stages.json")
WORKERS = 4
NEAR_MISS_RELERR = 0.10


# --------------------------------------------------------------------------- #
# refactor stages
# --------------------------------------------------------------------------- #
def _classify_refactor(rec: dict, raw: str) -> dict:
    lang = rec["language"]
    fenced = "```" in raw
    item = T._rebuild_refactor_item(rec)
    src = G.extract_code(raw)
    ok, detail = G.semantic_ok(lang, src, item.program)
    if ok is None:
        stage = "toolchain_skip"
    elif ok:
        stage = "UNEXPECTED_PASS"
    elif lang == "python":
        if detail == "timeout":
            stage = "timeout"
        elif detail.startswith("exit "):
            stage = "runtime_error"
        elif detail == "no stdout":
            stage = "no_stdout"
        elif detail.startswith("unparseable stdout"):
            stage = "unparseable_stdout"
        elif detail == "ran":
            stage = "wrong_output"
        else:
            stage = "other"
    else:
        first = detail.splitlines()[0] if detail else ""
        if first.startswith("expected "):
            stage = "wrong_output"
        elif "compile/run failed" in first:
            if lang == "go":
                stage = "go_compile_or_run"
            elif "g++" in first or "javac" in first:
                stage = "compile_error"
            else:
                stage = "run_error"
        else:
            stage = "other"
    return {"ok": bool(ok) if ok is not None else None, "stage": stage,
            "fenced": fenced}


# --------------------------------------------------------------------------- #
# comprehend typology
# --------------------------------------------------------------------------- #
def _wrong_op_alternatives(program) -> dict:
    """{result_var: set of json-normalized values a WRONG operation would give}."""
    env = dict(program.inputs)
    gold = oracle(program)
    alts: dict = {}
    for op in program.operations:
        rv = op.result_var
        cand = set()
        if op.op == "MEMBERSHIP_CHECK":
            cand.add(json.dumps(not gold[rv]))
        elif op.op == "KEY_VALUE_LOOKUP":
            for v in list(op.pairs.values()) + [op.default_value]:
                cand.add(json.dumps(v, sort_keys=True))
        elif op.op == "AGGREGATE":
            vals = env[op.collection_name]
            for alt in (sum(vals), min(vals), max(vals), len(vals)):
                cand.add(json.dumps(alt))
        else:  # CONDITIONAL_SELECT
            for v in (op.then_value, op.else_value):
                cand.add(json.dumps(v, sort_keys=True))
        cand.discard(json.dumps(gold[rv], sort_keys=True))
        alts[rv] = cand
        env[rv] = gold[rv]
    return alts


def _agg_vars(program) -> set:
    return {op.result_var for op in program.operations if op.op == "AGGREGATE"}


def _norm(v) -> str:
    return json.dumps(v, sort_keys=True)


def _classify_comprehend(rec: dict, raw: str) -> dict:
    item = T._rebuild_comprehend_item(rec)
    gold = oracle(item.program)
    pred = G.extract_json_obj(raw)
    em = 1 if G._match(pred, gold) else 0
    if em:
        return {"em": 1, "label": "pass"}
    if pred is None:
        return {"em": 0, "label": "parse_fail"}
    if set(pred) != set(gold):
        return {"em": 0, "label": "wrong_keys"}
    alts = _wrong_op_alternatives(item.program)
    aggs = _agg_vars(item.program)
    wrong = [v for v in gold if _norm(pred[v]) != _norm(gold[v])]
    kinds = []
    for v in wrong:
        if _norm(pred[v]) in alts[v]:
            kinds.append("wrong_op")
        elif (v in aggs and isinstance(pred[v], (int, float))
              and not isinstance(pred[v], bool) and gold[v]
              and abs(pred[v] - gold[v]) / abs(gold[v]) <= NEAR_MISS_RELERR):
            kinds.append("near_miss")
        else:
            kinds.append("other")
    if all(k == "wrong_op" for k in kinds):
        label = "recognition"
    elif all(k in ("near_miss", "wrong_op") for k in kinds):
        label = "near_miss"
    else:
        label = "mixed_semantic"
    return {"em": 0, "label": label}


# --------------------------------------------------------------------------- #
def _tally(rows: list) -> dict:
    """rows: [{stage/label, profile, language, incremental}] -> nested counts."""
    out = {"by_stage": collections.Counter(),
           "by_stage_profile": collections.Counter(),
           "by_stage_language": collections.Counter(),
           "incremental_by_stage": collections.Counter(),
           "incremental_by_stage_profile": collections.Counter()}
    for r in rows:
        s = r["stage"]
        out["by_stage"][s] += 1
        out["by_stage_profile"][f"{s}|{r['profile']}"] += 1
        out["by_stage_language"][f"{s}|{r['language']}"] += 1
        if r["incremental"]:
            out["incremental_by_stage"][s] += 1
            out["incremental_by_stage_profile"][f"{s}|{r['profile']}"] += 1
    return {k: dict(sorted(v.items())) for k, v in out.items()}


def main() -> int:
    cache = json.load(open(CACHE))
    report: dict = {"refactor": {}, "comprehend": {}}

    # ---------------- refactor: re-execute failing draw[0] items ----------- #
    for short, slug in MODELS:
        ann1 = cache["refactor"][short]["ann1"]
        un1 = cache["refactor"][short]["un1"]
        for arm, loader in (("annotated", lambda s=slug:
                             load_annotated("refactor", s)),
                            ("unannotated", lambda s=slug:
                             load_unannotated("refactor", s))):
            grades = ann1 if arm == "annotated" else un1
            recs = [r for r in loader() if grades.get(item_key(r)) == 0.0]
            n_expected = sum(1 for v in grades.values() if v == 0.0)
            assert len(recs) == n_expected, (
                f"KILL: {short}/{arm} failing-record count {len(recs)} != "
                f"cache {n_expected}")

            def work(r):
                cls = _classify_refactor(r, r["raw_outputs"][0])
                assert cls["ok"] is False, (
                    f"KILL: {short}/{arm} {item_key(r)} re-graded "
                    f"{cls['ok']!r}, cache says fail")
                other = un1 if arm == "annotated" else ann1
                return {"stage": cls["stage"], "profile": r["profile"],
                        "language": r["language"], "fenced": cls["fenced"],
                        "incremental": other.get(item_key(r)) == 1.0}

            with ThreadPoolExecutor(max_workers=WORKERS) as ex:
                rows = list(ex.map(work, recs))
            tal = _tally(rows)
            tal["n_failures"] = len(rows)
            tal["n_no_fence"] = sum(1 for r in rows if not r["fenced"])
            report["refactor"].setdefault(short, {})[arm] = tal
            print(f"refactor {short:18s} {arm:11s} n={len(rows):4d} "
                  f"stages={tal['by_stage']}", flush=True)

    # ---------------- comprehend: no execution ----------------------------- #
    for short, slug in MODELS:
        ann1 = cache["comprehend"][short]["ann1"]
        un1 = cache["comprehend"][short]["un1"]
        for arm, loader in (("annotated", lambda s=slug:
                             load_annotated("comprehend", s)),
                            ("unannotated", lambda s=slug:
                             load_unannotated("comprehend", s))):
            grades = ann1 if arm == "annotated" else un1
            rows = []
            for r in loader():
                k = item_key(r)
                cls = _classify_comprehend(r, r["raw_outputs"][0])
                want = grades.get(k)
                assert want is not None and cls["em"] == int(want), (
                    f"KILL: {short}/{arm} {k} EM {cls['em']} != cache {want}")
                if cls["em"]:
                    continue
                other = un1 if arm == "annotated" else ann1
                rows.append({"stage": cls["label"], "profile": r["profile"],
                             "language": r["language"],
                             "incremental": other.get(k) == 1.0})
            tal = _tally(rows)
            tal["n_failures"] = len(rows)
            report["comprehend"].setdefault(short, {})[arm] = tal
            print(f"comprehend {short:18s} {arm:11s} n={len(rows):4d} "
                  f"labels={tal['by_stage']}", flush=True)

    report["_meta"] = artifact_header(
        "failure_stages.py",
        ["bench/out/.annotation_ablation_grades.json",
         "bench/out/g3/refactor_dev__*.jsonl.gz",
         "bench/out/ladder/comprehend__*.jsonl.gz",
         "bench/out/ablation/*.jsonl.gz"],
        estimation_not_family=True,
        near_miss_relerr=NEAR_MISS_RELERR,
        post_hoc=("registered 2026-08-14 after the suite's results were read; "
                  "excludes the grader-side-brittleness alternative for the "
                  "ablation delta and the unannotated knob slope "
                  "(ANALYSIS_PLAN.md post-hoc addenda)"),
    )
    write_artifact(OUT, report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
