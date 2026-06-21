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

# Which incidental profiles each per-instance task sweeps. Configurable so a live
# run can trade matrix size for cost; the defaults give the full incidental axis.
REFACTOR_PROFILES = list(PROFILES)
COMPREHEND_PROFILES = list(PROFILES)


# --------------------------------------------------------------------------- #
# rendering cache (deterministic; generation is instant, compilation is not)
# --------------------------------------------------------------------------- #
_ENGINES: Dict[str, Engine] = {}


def _engine(profile: str) -> Engine:
    if profile not in _ENGINES:
        _ENGINES[profile] = Engine(DB, profile)
    return _ENGINES[profile]


def _sources(ir: dict, profile: str) -> Dict[str, str]:
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

    def prompt(self):
        return P.refactor(self.language, self.spaghetti_src, self.result_vars)


@dataclass
class JudgeItem:
    sample: str
    language: str
    family: str
    # levels: list of (label, rank, source); rank 0 = cleanest
    levels: List[tuple]

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

    def prompt(self):
        return P.comprehend(self.language, self.source, self.result_vars)


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
                    mock_gold=gold,
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
            levels: List[tuple] = []
            rank = 0
            if lang == "python":  # clean baseline exists rigorously for Python
                levels.append(("clean", rank, clean_static))
                rank += 1
            for p in PROFILES:
                levels.append((p, rank, rendered[p][lang]))
                rank += 1
            items.append(JudgeItem(sample=it.sample, language=lang,
                                   family=it.family, levels=levels))
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
                    mock_gold=gold,
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
# scorers (query the model k times, grade locally, emit the compact record)
# --------------------------------------------------------------------------- #
def score_refactor_item(item: RefactorItem, model: str, cfg, k: int) -> dict:
    system, user = item.prompt()
    outs = models.sample_k(model, system, user, k, cfg=cfg, mock_gold=item.mock_gold)
    per = [G.grade_refactor_one(item.language, o, item.spaghetti_src, item.program)
           for o in outs]
    agg = G.aggregate_refactor(per)
    rec = {"sample": item.sample, "profile": item.profile, "language": item.language,
           "variant": item.variant, "intrinsic": item.intrinsic,
           "prompt_hash": models.prompt_hash(system, user)}
    rec.update(agg)
    return rec


def score_judge_item(item: JudgeItem, model: str, cfg, k: int) -> dict:
    L = len(item.levels)
    # pointwise: k ratings per level
    ratings_by_level: List[List[Optional[int]]] = []
    for label, rank, src in item.levels:
        system, user = P.judge_pointwise(item.language, src)
        gold = str(_mock_rating(rank, L))
        outs = models.sample_k(model, system, user, k, cfg=cfg, mock_gold=gold)
        ratings_by_level.append([G.extract_int(o) for o in outs])
    pointwise = G.grade_judge_pointwise(ratings_by_level, item.ranks())
    # pairwise: every unordered pair of levels, A=first/B=second in level order
    picks: List[Optional[str]] = []
    correct: List[str] = []
    for (la, ra, sa), (lb, rb, sb) in combinations(item.levels, 2):
        system, user = P.judge_pairwise(item.language, sa, sb)
        gold = "A" if ra < rb else "B"          # less-degraded (lower rank) wins
        outs = models.sample_k(model, system, user, k, cfg=cfg, mock_gold=gold)
        # majority vote across k for the item-level pick
        labs = [G.extract_label(o) for o in outs]
        picks.append(_majority(labs))
        correct.append(gold)
    pairwise = G.grade_judge_pairwise(picks, correct)
    return {"sample": item.sample, "language": item.language,
            "levels": [lvl[0] for lvl in item.levels],
            "monotonicity": pointwise["monotonicity"],
            "sensitivity": pointwise["sensitivity"],
            "inversions": pointwise["inversions"],
            "rating_by_level": pointwise["rating_by_level"],
            "pairwise_acc": pairwise["pairwise_acc"],
            "n_pairs": pairwise["n_pairs"]}


def score_comprehend_item(item: ComprehendItem, model: str, cfg, k: int) -> dict:
    system, user = item.prompt()
    outs = models.sample_k(model, system, user, k, cfg=cfg, mock_gold=item.mock_gold)
    per = [G.grade_comprehend_one(o, item.program) for o in outs]
    agg = G.aggregate_comprehend(per)
    rec = {"sample": item.sample, "profile": item.profile, "language": item.language,
           "variant": item.variant, "intrinsic": item.intrinsic,
           "prompt_hash": models.prompt_hash(system, user)}
    rec.update(agg)
    return rec


def score_item(task: str, item, model: str, cfg, k: int) -> dict:
    if task == "refactor":
        return score_refactor_item(item, model, cfg, k)
    if task == "judge":
        return score_judge_item(item, model, cfg, k)
    if task == "comprehend":
        return score_comprehend_item(item, model, cfg, k)
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
