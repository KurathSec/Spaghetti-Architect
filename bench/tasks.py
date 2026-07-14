"""Phase 3 — task & item construction for the three benchmark tasks.

Each task turns the dataset's axes into concrete items, pairs every item with its
frozen prompt and its *oracle-backed mock answer* (so ``--selftest``/``--dry-run``
exercise the full pipeline at zero spend), and exposes a ``score_*`` function that
queries the model ``k`` times, grades locally (reusing ``oracle``/``validate``/
``metrics`` via :mod:`bench.grade`), and returns the compact per-item record.

* **Refactor** — recover clean code from a rendered spaghetti source; graded for
  semantic equivalence (gate) and simplification toward the known-optimal clean
  baseline. One item per (sample, profile, language, variant). The variant set of
  each representative supplies the differential inputs that defeat guessing.
* **Judge** — rate maintainability across the incidental knob (``clean`` for
  Python, then ``minimal``/``standard``/``max``); pointwise monotonicity +
  sensitivity, and pairwise accuracy vs the knob order.
* **Comprehend** — predict the result variables from a rendered source;
  exact-match vs the oracle over base + variants.

Refactor and comprehend fan out **per family** (they compile/run or are graded per
instance); judge is one batch per model (cheap grading, no compilation).
"""

from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import dataclass, field
from itertools import combinations
from typing import Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bench import dataset as D  # noqa: E402
from bench import grade as G  # noqa: E402
from bench import models  # noqa: E402
from bench import prompts as P  # noqa: E402
from eval import metrics as M  # noqa: E402
from src.engine import Engine  # noqa: E402
from src.nodes.validator import oracle  # noqa: E402

PROFILES = D.PROFILES
LANGS = D.LANGS
DB = D.DB
TASKS = ["refactor", "judge", "comprehend"]

# Which incidental profiles each per-instance task sweeps. The judge (cheap, no
# compilation) uses the full 6-point axis (clean + all of PROFILES) for monotonicity
# power; the per-instance compile/run tasks (refactor compiles untrusted code in five
# languages) use a cost-bounded 3-point subset by default. Configurable so a live run
# can trade matrix size for cost.
REFACTOR_PROFILES = ["minimal", "standard", "max"]
COMPREHEND_PROFILES = ["minimal", "standard", "max"]


# --------------------------------------------------------------------------- #
# rendering cache (deterministic; generation is instant, compilation is not)
# --------------------------------------------------------------------------- #
_ENGINES: Dict[tuple, Engine] = {}
# Engine.generate stashes per-call state (``self._inputs``) on the SHARED generator
# singletons (src.generators.REGISTRY), so its own docstring requires generation to run
# "one thread at a time". The finalize/regrade path grades items in a thread pool and
# every grade rebuilds sources via ``_sources`` -> ``Engine.generate``; without
# serialization two threads clobber each other's ``_inputs`` and a generator then reads a
# collection name absent from the clobbering IR -> non-deterministic KeyError (e.g.
# 'allow_list'). This lock makes the bench generation entry point honor that contract;
# generation is instant, so the cost is negligible and the output stays byte-identical.
_GEN_LOCK = threading.Lock()


# Ablation switch. The generator annotates its own output (module header, a per-operation
# comment stating the operation's CLEAN form, and inline SPAGH_* markers), and every task
# prompt interpolates the source verbatim, so by default the model is shown the answer:
# 72% of the clean baseline's code lines appear verbatim in its own input. Set
# BENCH_STRIP_ANNOTATIONS=1 to mint the unannotated control corpus (byte-identical code,
# every comment removed; oracle-validated 600/600). Stamped into the env block and the batch
# filename so every artifact records which condition produced it. The stamp is DESCRIPTIVE,
# not enforced: --regrade rebuilds sources under whatever this env var says NOW, so an
# unannotated batch must only ever be re-graded with BENCH_STRIP_ANNOTATIONS=1 (and the
# annotated arm only with it unset). bench/annotation_ablation.py refuses to start if it is
# set, for exactly this reason.
STRIP_ANNOTATIONS = os.environ.get("BENCH_STRIP_ANNOTATIONS", "") not in ("", "0", "false")


def _engine(profile: str) -> Engine:
    key = (profile, not STRIP_ANNOTATIONS)
    if key not in _ENGINES:
        _ENGINES[key] = Engine(DB, profile, annotate=not STRIP_ANNOTATIONS)
    return _ENGINES[key]


def _sources(ir: dict, profile: str) -> Dict[str, str]:
    with _GEN_LOCK:                       # serialize shared-generator state (see above)
        return _engine(profile).generate(ir)["sources"]


# --------------------------------------------------------------------------- #
# items
# --------------------------------------------------------------------------- #
@dataclass
class RefactorItem:
    sample: str
    variant: str
    profile: str
    language: str
    intrinsic: Dict[str, int]
    family: str
    program: object
    spaghetti_src: str
    result_vars: List[str]
    mock_gold: str
    tier: str = "A"            # novelty tier (A dev/salted, B structural, C shift)

    def prompt(self, variant: int = 0):
        return P.refactor(self.language, self.spaghetti_src, self.result_vars, variant)


@dataclass
class JudgeItem:
    sample: str
    language: str
    family: str
    # levels: list of (label, rank, source); rank 0 = cleanest
    levels: List[tuple]
    tier: str = "A"

    def ranks(self) -> List[int]:
        return [lvl[1] for lvl in self.levels]


@dataclass
class ComprehendItem:
    sample: str
    variant: str
    profile: str
    language: str
    intrinsic: Dict[str, int]
    family: str
    program: object
    source: str
    result_vars: List[str]
    mock_gold: str
    tier: str = "A"

    def prompt(self, variant: int = 0):
        return P.comprehend(self.language, self.source, self.result_vars, variant)


# --------------------------------------------------------------------------- #
# builders
# --------------------------------------------------------------------------- #
def _filter(items, family: Optional[str]):
    return [it for it in items if family is None or it.family == family]


def build_refactor_items(split: D.Split, family: Optional[str] = None) -> List[RefactorItem]:
    items: List[RefactorItem] = []
    for it in split.items:
        ir = split.ir(it.stem)
        prog = split.program(it.stem)
        rvars = list(oracle(prog))
        clean_runnable = M.clean_baseline_runnable(prog)
        for profile in REFACTOR_PROFILES:
            srcs = _sources(ir, profile)
            minimal_srcs = _sources(ir, "minimal")
            for lang in LANGS:
                # oracle-backed mock: Python -> the known optimum; others -> the
                # engine's own minimal output (guaranteed to pass validate()).
                gold = clean_runnable if lang == "python" else minimal_srcs[lang]
                items.append(RefactorItem(
                    sample=it.sample, variant=it.variant, profile=profile,
                    language=lang, intrinsic=it.intrinsic, family=it.family,
                    program=prog, spaghetti_src=srcs[lang], result_vars=rvars,
                    mock_gold=gold, tier=it.tier,
                ))
    return _filter(items, family)


def build_judge_items(split: D.Split, family: Optional[str] = None) -> List[JudgeItem]:
    items: List[JudgeItem] = []
    # judge the base samples only (one clear quality gradient per sample)
    bases = [it for it in split.items if not it.is_variant]
    for it in bases:
        ir = split.ir(it.stem)
        prog = split.program(it.stem)
        rendered = {p: _sources(ir, p) for p in PROFILES}
        clean_static = M.clean_baseline_static(prog)
        for lang in LANGS:
            # Ordered candidate levels (clean is rigorous for Python only), then
            # DEDUPE by source text: several incidental profiles can render
            # identically for a given family (e.g. lookup-only families), and the
            # judge must never be asked to compare byte-identical code. Distinct
            # renders keep their nested order, so the rank is still ground truth.
            ordered = ([("clean", clean_static)] if lang == "python" else [])
            ordered += [(p, rendered[p][lang]) for p in PROFILES]
            levels: List[tuple] = []
            seen: set = set()
            for label, src in ordered:
                if src in seen:
                    continue
                seen.add(src)
                levels.append((label, len(levels), src))   # contiguous rank 0..n-1
            if len(levels) >= 2:   # need >=2 distinct levels to score monotonicity
                items.append(JudgeItem(sample=it.sample, language=lang,
                                       family=it.family, levels=levels, tier=it.tier))
    return _filter(items, family)


def build_comprehend_items(split: D.Split, family: Optional[str] = None) -> List[ComprehendItem]:
    items: List[ComprehendItem] = []
    for it in split.items:
        ir = split.ir(it.stem)
        prog = split.program(it.stem)
        rvars = list(oracle(prog))
        gold = json.dumps(oracle(prog))
        for profile in COMPREHEND_PROFILES:
            srcs = _sources(ir, profile)
            for lang in LANGS:
                items.append(ComprehendItem(
                    sample=it.sample, variant=it.variant, profile=profile,
                    language=lang, intrinsic=it.intrinsic, family=it.family,
                    program=prog, source=srcs[lang], result_vars=rvars,
                    mock_gold=gold, tier=it.tier,
                ))
    return _filter(items, family)


def build_items(task: str, split: D.Split, family: Optional[str] = None):
    if task == "refactor":
        return build_refactor_items(split, family)
    if task == "judge":
        return build_judge_items(split, family)
    if task == "comprehend":
        return build_comprehend_items(split, family)
    raise ValueError(f"unknown task: {task!r}")


# --------------------------------------------------------------------------- #
# stable per-item id (resumability / fault isolation / re-grade keying)
# --------------------------------------------------------------------------- #
def item_id(task: str, item) -> str:
    """A stable, content-free identifier for one benchmark item, used to key the
    incremental checkpoint (skip already-completed items on re-run) and to label
    error stubs. Built from the dataset axes that uniquely place the item *within a
    (task, model, family, split) batch* — deterministic across runs, so a crash +
    re-run skips exactly the items already written. NOT a hash of the prompt (the
    prompt is byte-stable for an item anyway); these axes are what the builders fan
    out over."""
    if task == "judge":
        # judge fans out per (sample, language) base; levels are derived
        return f"judge:{item.sample}:{item.language}"
    # refactor / comprehend fan out per (sample, variant, profile, language)
    return (f"{task}:{item.sample}:{getattr(item, 'variant', '?')}:"
            f"{item.profile}:{item.language}")


# --------------------------------------------------------------------------- #
# scorers (query the model k times, grade locally, emit the compact record)
# --------------------------------------------------------------------------- #
def _mock_raw(outs: List[str], model: str) -> List[str]:
    """Keep committed mock records SMALL: the mock raw output is the gold string, which
    is large (a whole clean source for refactor). Store a single sentinel for the mock so
    the committed JSONs stay tiny; the re-grade path treats the mock as non-regradable
    (it would just re-emit gold). Real-model raw outputs are stored verbatim."""
    if model == models.MOCK:
        return ["<mock>"]
    return list(outs)


def score_refactor_item(item: RefactorItem, model: str, cfg, k: int) -> dict:
    system, user = item.prompt()
    outs, snap = models.sample_k(model, system, user, k, cfg=cfg, mock_gold=item.mock_gold)
    rec = {"sample": item.sample, "profile": item.profile, "language": item.language,
           "variant": item.variant, "intrinsic": item.intrinsic, "snapshot": snap,
           "tier": item.tier, "prompt_hash": models.prompt_hash(system, user),
           # RAW model outputs are PERSISTED so a grading change can be re-applied
           # offline (--regrade) without re-paying. mock stores a sentinel (gold is
           # large + deterministic); real models store the k completions verbatim.
           "raw_outputs": _mock_raw(outs, model)}
    rec.update(_grade_refactor_outputs(item.language, outs, item.spaghetti_src, item.program))
    return rec


def _grade_refactor_outputs(language: str, outs: List[str], spaghetti_src: str,
                            program) -> dict:
    """Pure grader: k raw completions -> the aggregated refactor record fields. Shared by
    the live scorer and the offline re-grade path (no API contact)."""
    per = [G.grade_refactor_one(language, o, spaghetti_src, program) for o in outs]
    agg = G.aggregate_refactor(per)
    # running parse-success signal (semantic gate ran => the code block parsed/compiled
    # far enough to execute) for the batch early-abort + pilot report.
    scored = [p for p in per if not p.get("skip")]
    agg["_parse_ok"] = sum(1 for p in scored if p.get("semantic_ok") == 1)
    agg["_parse_n"] = len(scored)
    return agg


def regrade_refactor_record(rec: dict) -> dict:
    """Re-grade a stored refactor record from its persisted ``raw_outputs`` (ZERO API).
    Rebuilds the (sample, variant, profile, language) item from the dataset to recover
    the oracle program + spaghetti source, then re-runs the grader. Mock records (sentinel
    raw) are returned unchanged. Returns a NEW record with the same identity keys."""
    if not _regradable(rec):
        return rec
    it = _rebuild_refactor_item(rec)
    fresh = {k: rec[k] for k in ("sample", "profile", "language", "variant",
                                 "intrinsic", "snapshot", "tier", "prompt_hash",
                                 "raw_outputs") if k in rec}
    fresh.update(_grade_refactor_outputs(it.language, rec["raw_outputs"],
                                         it.spaghetti_src, it.program))
    return fresh


def score_judge_item(item: JudgeItem, model: str, cfg, k: int) -> dict:
    L = len(item.levels)
    # pointwise: k RAW rating strings per level (persisted; graded below + on --regrade)
    raw_pointwise: List[List[str]] = []
    snap = models.MOCK_SNAPSHOT
    for label, rank, src in item.levels:
        system, user = P.judge_pointwise(item.language, src)
        gold = str(_mock_rating(rank, L))
        outs, snap = models.sample_k(model, system, user, k, cfg=cfg, mock_gold=gold)
        raw_pointwise.append(_mock_raw(outs, model))
    # pairwise (PRIMARY): every unordered pair in BOTH orders; store the RAW AB/BA label
    # strings + clean_is_a so the position-swap grading is reproducible offline.
    raw_pairwise: List[dict] = []
    for (la, ra, sa), (lb, rb, sb) in combinations(item.levels, 2):
        clean_is_a = ra < rb                     # source a (first) cleaner iff lower rank
        sys1, usr1 = P.judge_pairwise(item.language, sa, sb)        # AB order
        o1, snap = models.sample_k(model, sys1, usr1, k, cfg=cfg,
                                   mock_gold=("A" if clean_is_a else "B"))
        sys2, usr2 = P.judge_pairwise(item.language, sb, sa)        # BA order (swapped)
        o2, snap = models.sample_k(model, sys2, usr2, k, cfg=cfg,
                                   mock_gold=("B" if clean_is_a else "A"))
        raw_pairwise.append({"ab": _mock_raw(o1, model), "ba": _mock_raw(o2, model),
                             "clean_is_a": clean_is_a})
    rec = {"sample": item.sample, "language": item.language, "snapshot": snap,
           "tier": item.tier, "levels": [lvl[0] for lvl in item.levels],
           "ranks": item.ranks(),
           # RAW per-level rating strings and RAW per-pair AB/BA labels are PERSISTED
           # so a judge grading change re-applies offline (--regrade) with no spend.
           "raw_pointwise": raw_pointwise, "raw_pairwise": raw_pairwise}
    rec.update(_grade_judge_outputs(raw_pointwise, raw_pairwise, item.ranks(), model))
    return rec


def _grade_judge_outputs(raw_pointwise, raw_pairwise, ranks, model) -> dict:
    """Pure grader: raw judge strings -> the aggregated judge record fields. Mock raw is a
    sentinel; for the mock we re-derive the perfectly-monotone ratings/labels from the
    ranks so the dry-run record matches the live shape without storing big strings."""
    L = len(ranks)
    if model == models.MOCK:
        ratings_by_level = [[_mock_rating(r, L)] for r in ranks]
        pair_data = []
        for (i, ra), (j, rb) in combinations(list(enumerate(ranks)), 2):
            clean_is_a = ra < rb
            pair_data.append(("A" if clean_is_a else "B",
                              "B" if clean_is_a else "A", clean_is_a))
    else:
        ratings_by_level = [[G.extract_int(o) for o in lvl] for lvl in raw_pointwise]
        pair_data = [(_majority([G.extract_label(o) for o in p["ab"]]),
                      _majority([G.extract_label(o) for o in p["ba"]]),
                      p["clean_is_a"]) for p in raw_pairwise]
    pointwise = G.grade_judge_pointwise(ratings_by_level, ranks)
    pairwise = G.grade_judge_pairwise(pair_data)
    # parse-success signal: a pair is "parsed" when BOTH orders yielded a label.
    parsed = sum(1 for ab, ba, _ in pair_data if ab is not None and ba is not None)
    return {"pairwise_acc": pairwise["pairwise_acc"],            # primary
            "position_consistency": pairwise["position_consistency"],
            "n_consistent": pairwise["n_consistent"],
            "n_pairs": pairwise["n_pairs"],
            "monotonicity": pointwise["monotonicity"],          # secondary (Spearman)
            "sensitivity": pointwise["sensitivity"],
            "inversions": pointwise["inversions"],
            "rating_by_level": pointwise["rating_by_level"],
            "_parse_ok": parsed, "_parse_n": len(pair_data)}


def regrade_judge_record(rec: dict) -> dict:
    """Re-grade a stored judge record from its persisted raw strings (ZERO API)."""
    if "raw_pairwise" not in rec or "ranks" not in rec:
        return rec
    model = models.MOCK if _is_mock_judge_raw(rec) else "_real"
    fresh = {k: rec[k] for k in ("sample", "language", "snapshot", "tier", "levels",
                                 "ranks", "raw_pointwise", "raw_pairwise") if k in rec}
    fresh.update(_grade_judge_outputs(rec.get("raw_pointwise", []),
                                      rec.get("raw_pairwise", []), rec["ranks"], model))
    return fresh


def _is_mock_judge_raw(rec: dict) -> bool:
    rp = rec.get("raw_pairwise") or []
    return bool(rp and rp[0].get("ab") == ["<mock>"])


def score_comprehend_item(item: ComprehendItem, model: str, cfg, k: int) -> dict:
    system, user = item.prompt()
    outs, snap = models.sample_k(model, system, user, k, cfg=cfg, mock_gold=item.mock_gold)
    rec = {"sample": item.sample, "profile": item.profile, "language": item.language,
           "variant": item.variant, "intrinsic": item.intrinsic, "snapshot": snap,
           "tier": item.tier, "prompt_hash": models.prompt_hash(system, user),
           # RAW completions PERSISTED (offline --regrade re-applies grading, no spend).
           "raw_outputs": _mock_raw(outs, model)}
    rec.update(_grade_comprehend_outputs(outs, item.program))
    return rec


def _grade_comprehend_outputs(outs: List[str], program) -> dict:
    """Pure grader: k raw completions -> the aggregated comprehend record fields."""
    per = [G.grade_comprehend_one(o, program) for o in outs]
    agg = G.aggregate_comprehend(per)
    agg["_parse_ok"] = sum(1 for p in per if p.get("parsed"))
    agg["_parse_n"] = len(per)
    return agg


def regrade_comprehend_record(rec: dict) -> dict:
    """Re-grade a stored comprehend record from its persisted ``raw_outputs`` (ZERO API)."""
    if not _regradable(rec):
        return rec
    it = _rebuild_comprehend_item(rec)
    fresh = {k: rec[k] for k in ("sample", "profile", "language", "variant",
                                 "intrinsic", "snapshot", "tier", "prompt_hash",
                                 "raw_outputs") if k in rec}
    fresh.update(_grade_comprehend_outputs(rec["raw_outputs"], it.program))
    return fresh


def score_item(task: str, item, model: str, cfg, k: int) -> dict:
    if task == "refactor":
        return score_refactor_item(item, model, cfg, k)
    if task == "judge":
        return score_judge_item(item, model, cfg, k)
    if task == "comprehend":
        return score_comprehend_item(item, model, cfg, k)
    raise ValueError(f"unknown task: {task!r}")


# --------------------------------------------------------------------------- #
# fetch-only scorers (Phase A of the concurrent runner): query the model k times
# and emit the SAME compact record MINUS the graded metrics — i.e. only the identity
# keys + the persisted ``raw_*`` outputs that the offline ``regrade_*_record`` graders
# consume. This cleanly separates NETWORK fan-out (here, high concurrency) from the
# sandboxed GRADING (the --regrade path, bounded concurrency), with no duplicated
# grading logic: a record produced here, fed to ``regrade_record``, is byte-identical
# to what ``score_item`` would have returned for the same raw outputs.
# ``format_parse_ok``/``format_parse_n`` are a CHEAP (no-subprocess) running
# format-parseable signal used solely to drive the parse-success early-abort during
# Phase A; the authoritative ``_parse_ok``/``_parse_n`` are recomputed by the grader.
# --------------------------------------------------------------------------- #
def fetch_refactor_item(item: RefactorItem, model: str, cfg, k: int) -> dict:
    system, user = item.prompt()
    outs, snap = models.sample_k(model, system, user, k, cfg=cfg, mock_gold=item.mock_gold)
    raw = _mock_raw(outs, model)
    fp = sum(1 for o in outs if G.extract_code(o)) if model != models.MOCK else k
    return {"sample": item.sample, "profile": item.profile, "language": item.language,
            "variant": item.variant, "intrinsic": item.intrinsic, "snapshot": snap,
            "tier": item.tier, "prompt_hash": models.prompt_hash(system, user),
            "raw_outputs": raw,
            "format_parse_ok": fp, "format_parse_n": len(outs)}


def fetch_comprehend_item(item: ComprehendItem, model: str, cfg, k: int) -> dict:
    system, user = item.prompt()
    outs, snap = models.sample_k(model, system, user, k, cfg=cfg, mock_gold=item.mock_gold)
    raw = _mock_raw(outs, model)
    fp = (sum(1 for o in outs if G.extract_json_obj(o) is not None)
          if model != models.MOCK else k)
    return {"sample": item.sample, "profile": item.profile, "language": item.language,
            "variant": item.variant, "intrinsic": item.intrinsic, "snapshot": snap,
            "tier": item.tier, "prompt_hash": models.prompt_hash(system, user),
            "raw_outputs": raw,
            "format_parse_ok": fp, "format_parse_n": len(outs)}


def fetch_judge_item(item: JudgeItem, model: str, cfg, k: int) -> dict:
    L = len(item.levels)
    raw_pointwise: List[List[str]] = []
    snap = models.MOCK_SNAPSHOT
    for label, rank, src in item.levels:
        system, user = P.judge_pointwise(item.language, src)
        gold = str(_mock_rating(rank, L))
        outs, snap = models.sample_k(model, system, user, k, cfg=cfg, mock_gold=gold)
        raw_pointwise.append(_mock_raw(outs, model))
    raw_pairwise: List[dict] = []
    fp = fn = 0
    for (la, ra, sa), (lb, rb, sb) in combinations(item.levels, 2):
        clean_is_a = ra < rb
        sys1, usr1 = P.judge_pairwise(item.language, sa, sb)
        o1, snap = models.sample_k(model, sys1, usr1, k, cfg=cfg,
                                   mock_gold=("A" if clean_is_a else "B"))
        sys2, usr2 = P.judge_pairwise(item.language, sb, sa)
        o2, snap = models.sample_k(model, sys2, usr2, k, cfg=cfg,
                                   mock_gold=("B" if clean_is_a else "A"))
        raw_pairwise.append({"ab": _mock_raw(o1, model), "ba": _mock_raw(o2, model),
                             "clean_is_a": clean_is_a})
        # cheap format signal: a pair "parses" when BOTH orders' majority label resolves.
        if model != models.MOCK:
            fn += 1
            if (_majority([G.extract_label(o) for o in o1]) is not None
                    and _majority([G.extract_label(o) for o in o2]) is not None):
                fp += 1
    if model == models.MOCK:
        fp = fn = len(raw_pairwise)
    return {"sample": item.sample, "language": item.language, "snapshot": snap,
            "tier": item.tier, "levels": [lvl[0] for lvl in item.levels],
            "ranks": item.ranks(),
            "raw_pointwise": raw_pointwise, "raw_pairwise": raw_pairwise,
            "format_parse_ok": fp, "format_parse_n": fn}


def fetch_item(task: str, item, model: str, cfg, k: int) -> dict:
    """Phase-A network fetch: returns the raw (ungraded) record for one item."""
    if task == "refactor":
        return fetch_refactor_item(item, model, cfg, k)
    if task == "judge":
        return fetch_judge_item(item, model, cfg, k)
    if task == "comprehend":
        return fetch_comprehend_item(item, model, cfg, k)
    raise ValueError(f"unknown task: {task!r}")


# --------------------------------------------------------------------------- #
# offline re-grade (re-apply graders to PERSISTED raw outputs; ZERO API)
# --------------------------------------------------------------------------- #
def _regradable(rec: dict) -> bool:
    """A record is offline-regradable iff it carries real (non-sentinel) raw outputs.
    Mock records store ``["<mock>"]`` and re-grading would just re-emit gold, so they are
    passed through unchanged (the dry-run shape is already correct)."""
    raw = rec.get("raw_outputs")
    return bool(raw) and raw != ["<mock>"]


def _rebuild_refactor_item(rec: dict) -> RefactorItem:
    """Reconstruct the RefactorItem for a stored record from the dataset (re-derives the
    oracle program + spaghetti source for its sample/variant/profile/language). No API."""
    sp = D.load(rec.get("split", "dev"))
    stem = _stem_for(sp, rec["sample"], rec.get("variant", "base"))
    ir, prog = sp.ir(stem), sp.program(stem)
    srcs = _sources(ir, rec["profile"])
    return RefactorItem(
        sample=rec["sample"], variant=rec.get("variant", "base"), profile=rec["profile"],
        language=rec["language"], intrinsic=rec.get("intrinsic", {}),
        family=rec.get("family", "?"), program=prog, spaghetti_src=srcs[rec["language"]],
        result_vars=list(oracle(prog)), mock_gold="", tier=rec.get("tier", "A"))


def _rebuild_comprehend_item(rec: dict) -> ComprehendItem:
    sp = D.load(rec.get("split", "dev"))
    stem = _stem_for(sp, rec["sample"], rec.get("variant", "base"))
    ir, prog = sp.ir(stem), sp.program(stem)
    srcs = _sources(ir, rec["profile"])
    return ComprehendItem(
        sample=rec["sample"], variant=rec.get("variant", "base"), profile=rec["profile"],
        language=rec["language"], intrinsic=rec.get("intrinsic", {}),
        family=rec.get("family", "?"), program=prog, source=srcs[rec["language"]],
        result_vars=list(oracle(prog)), mock_gold="", tier=rec.get("tier", "A"))


def _stem_for(split: D.Split, sample: str, variant: str) -> str:
    """The dataset stem for a (sample, variant) pair (the records keep them separately)."""
    for it in split.items:
        if it.sample == sample and getattr(it, "variant", "base") == variant:
            return it.stem
    # fall back to matching by sample alone (records predating variant-keying)
    for it in split.items:
        if it.sample == sample:
            return it.stem
    raise KeyError(f"no dataset item for sample={sample!r} variant={variant!r}")


def regrade_record(task: str, rec: dict) -> dict:
    """Re-grade ONE stored record from its persisted raw outputs (no API). Error stubs and
    mock/sentinel records pass through unchanged."""
    if rec.get("error"):
        return rec
    if task == "refactor":
        return regrade_refactor_record(rec)
    if task == "judge":
        return regrade_judge_record(rec)
    if task == "comprehend":
        return regrade_comprehend_record(rec)
    raise ValueError(f"unknown task: {task!r}")


# --------------------------------------------------------------------------- #
# mock helpers
# --------------------------------------------------------------------------- #
def _mock_rating(rank: int, n_levels: int) -> int:
    """A perfectly monotone gold rating: rank 0 (cleanest) -> 10, last -> 1."""
    if n_levels <= 1:
        return 10
    return round(1 + (n_levels - 1 - rank) * 9 / (n_levels - 1))


def _majority(labels: List[Optional[str]]) -> Optional[str]:
    valid = [x for x in labels if x is not None]
    if not valid:
        return None
    return max(set(valid), key=valid.count)
