"""Phase 3 — graders. Pure functions of (model output, program, ground truth),
clock-free and network-free, so they are trivially testable under ``--selftest``.

Reuse, never reinvent: semantic equivalence comes from the project's own
``oracle``/``validate``; structural simplification from ``eval.metrics``. The one
thing added here is the **untrusted-execution discipline** the validator's
in-process Python path deliberately does not provide:

* model **Python** is never ``exec``'d in-process — it is written to a throwaway
  tempdir and run in a subprocess with ``-I`` isolated mode, a wall-clock timeout,
  a sanitized environment, and (when the kernel allows) a private network
  namespace via ``unshare`` so it cannot reach the network;
* model **JS/Go/Java/C++** reuse ``validate()`` (already subprocess + tempdir +
  timeout; it inherits the multi-JDK ``java`` fix), per the workflow's "reuse the
  validator's compile/run discipline for the other languages".

Correctness gates quality: a refactor's ``simplification_quality`` counts only if
it is semantically equivalent on the item's inputs.
"""

from __future__ import annotations

import ast
import json
import operator
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from itertools import combinations
from typing import Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from eval import metrics as M  # noqa: E402
from src.nodes.validator import oracle, validate  # noqa: E402

_TIMEOUT = 20  # seconds, per untrusted run

# Recovery metric facets per lane (workflow Task A): Python-AST rigorous, else proxy.
# `readability` (Python maintainability index, higher = better) is the anti-gaming
# axis: terse-but-cryptic code games SIZE but not readability. The recovery formula
# is direction-agnostic (fraction of the spaghetti->clean gap closed), so a
# higher-is-better facet works with the same _recovery().
_PY_FACETS = ("cyclomatic", "ast_nodes", "effort", "readability")
_PROXY_FACETS = ("branch_keywords", "brace_depth", "tokens")
# Size facets (lower = better): a model can *undershoot* the clean floor on these
# (fewer nodes than idiomatic) = removed structure = over-golfed, flagged not rewarded.
_SIZE_FACETS = ("cyclomatic", "ast_nodes", "effort", "branch_keywords", "brace_depth",
                "tokens")
_BAND_EPS = 0.15   # within 15% of the clean floor = inside the optimality band


# --------------------------------------------------------------------------- #
# untrusted execution
# --------------------------------------------------------------------------- #
_NET_ISOLATION: Optional[List[str]] = None


def network_isolation_prefix() -> List[str]:
    """``['unshare','-rn','--']`` if an unprivileged user+network namespace works
    on this host (drops the network for untrusted code), else ``[]``. Probed once."""
    global _NET_ISOLATION
    if _NET_ISOLATION is None:
        _NET_ISOLATION = []
        if shutil.which("unshare"):
            try:
                p = subprocess.run(["unshare", "-rn", "--", "true"],
                                   capture_output=True, timeout=10)
                if p.returncode == 0:
                    _NET_ISOLATION = ["unshare", "-rn", "--"]
            except Exception:  # noqa: BLE001
                _NET_ISOLATION = []
    return _NET_ISOLATION


def _sanitized_env() -> dict:
    keep = {}
    path = os.environ.get("PATH")
    if path:
        keep["PATH"] = path
    keep["HOME"] = "/nonexistent"
    return keep


def _py_epilogue(result_vars: List[str]) -> str:
    """Append a JSON print of the result variables so an isolated run is readable
    the same way the compiled targets are (last stdout line = JSON)."""
    keys = ", ".join(f'{v!r}: globals().get({v!r})' for v in result_vars)
    return ("\n\nimport json as _spaghetti_json\n"
            f"print(_spaghetti_json.dumps({{{keys}}}, default=str))\n")


def run_python_untrusted(code: str, result_vars: List[str],
                         timeout: int = _TIMEOUT) -> Tuple[bool, object]:
    """Run untrusted model Python in an isolated subprocess; return
    ``(ok, value_dict_or_error_str)``. Never ``exec``'d in-process."""
    program = code + _py_epilogue(result_vars)
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "solution.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(program)
        cmd = network_isolation_prefix() + [sys.executable, "-I", "-S", "-B", path]
        try:
            proc = subprocess.run(cmd, cwd=d, env=_sanitized_env(),
                                  capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, "timeout"
        if proc.returncode != 0:
            return False, f"exit {proc.returncode}: {proc.stderr.strip()[:200]}"
        lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
        if not lines:
            return False, "no stdout"
        try:
            return True, json.loads(lines[-1])
        except Exception as ex:  # noqa: BLE001
            return False, f"unparseable stdout: {ex}"


def _match(actual: dict, expected: dict) -> bool:
    """Type-aware comparison over the same key set (mirrors validator._equal:
    JSON ``true`` != ``1``)."""
    if actual is None or set(actual) != set(expected):
        return False
    return all(
        json.dumps(actual.get(k), sort_keys=True) == json.dumps(expected[k], sort_keys=True)
        for k in expected
    )


def semantic_ok(language: str, model_src: str, program) -> Tuple[Optional[bool], str]:
    """Did the model's code reproduce the oracle on this program's inputs?
    Returns ``(ok_or_None, detail)``; ``None`` means SKIP (toolchain absent), never
    counted as a pass."""
    if language == "python":
        ok, val = run_python_untrusted(model_src, list(oracle(program)))
        if not ok:
            return False, str(val)
        return _match(val, oracle(program)), "ran"
    res = validate(language, model_src, program)  # subprocess compile/run, untrusted-style
    if res.status == "SKIP":
        return None, res.detail
    return (res.status == "PASS"), res.detail


# --------------------------------------------------------------------------- #
# code / answer extraction
# --------------------------------------------------------------------------- #
_FENCE_RE = re.compile(r"```[a-zA-Z0-9_+-]*\s*\n(.*?)```", re.DOTALL)


def extract_code(text: str) -> str:
    """The first fenced code block, else the whole text stripped."""
    m = _FENCE_RE.search(text)
    return (m.group(1) if m else text).strip("\n").strip("`").strip()


def extract_int(text: str, lo: int = 1, hi: int = 10) -> Optional[int]:
    m = re.search(r"-?\d+", text)
    if not m:
        return None
    return max(lo, min(hi, int(m.group(0))))


def extract_label(text: str) -> Optional[str]:
    m = re.search(r"\b([AB])\b", text.strip().upper())
    if m:
        return m.group(1)
    m = re.search(r"[AB]", text.strip().upper())
    return m.group(0) if m else None


def extract_json_obj(text: str) -> Optional[dict]:
    """First balanced ``{...}`` parsed as JSON (tolerates surrounding prose)."""
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                        return obj if isinstance(obj, dict) else None
                    except Exception:  # noqa: BLE001
                        break
        start = text.find("{", start + 1)
    return None


# --------------------------------------------------------------------------- #
# small statistics (k-sample CIs; model outputs are NOT byte-deterministic)
# --------------------------------------------------------------------------- #
def mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def stdev(xs: List[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5


def ci95_bootstrap(xs: List[float], iters: int = 2000, seed: int = 0) -> List[float]:
    """Deterministic percentile bootstrap 95% CI of the mean (reproducible given
    the k samples). Degenerate (k<2 or zero-variance) collapses to the point."""
    if not xs:
        return [0.0, 0.0]
    if len(xs) < 2 or len(set(xs)) == 1:
        return [xs[0], xs[0]]
    rng = random.Random(seed)
    n = len(xs)
    means = sorted(mean([xs[rng.randrange(n)] for _ in range(n)]) for _ in range(iters))
    lo = means[int(0.025 * iters)]
    hi = means[int(0.975 * iters) - 1]
    return [lo, hi]


def spearman(x: List[float], y: List[float]) -> float:
    """Spearman rank correlation (ties share mean rank)."""
    def rank(v: List[float]) -> List[float]:
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    if len(x) < 2:
        return 0.0
    rx, ry = rank(x), rank(y)
    n = len(x)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    dx = sum((rx[i] - mx) ** 2 for i in range(n)) ** 0.5
    dy = sum((ry[i] - my) ** 2 for i in range(n)) ** 0.5
    return num / (dx * dy) if dx > 0 and dy > 0 else 0.0


# --------------------------------------------------------------------------- #
# Task A — Refactor: semantic gate + simplification recovery
# --------------------------------------------------------------------------- #
def _py_facet(src: str, facet: str) -> Optional[float]:
    try:
        if facet == "cyclomatic":
            return float(M.cyclomatic(src))
        if facet == "ast_nodes":
            return float(M.ast_nodes(src))
        if facet == "effort":
            return float(M.halstead(src)["effort"])
        if facet == "readability":            # maintainability index (higher = better)
            return float(M.maintainability_index(src))
    except SyntaxError:
        return None
    return None


def _proxy_facet(src: str, lang: str, facet: str) -> Optional[float]:
    a = M.agnostic_metrics(src, lang)
    return float(a.get(facet, 0))


def _recovery(spag: float, model: float, clean: float) -> Optional[float]:
    denom = spag - clean
    if denom == 0:
        return None
    return max(-1.0, min(1.0, (spag - model) / denom))


def refactor_facets(language: str, model_src: str, spaghetti_src: str,
                    program) -> Dict[str, Tuple[Optional[float], Optional[float], Optional[float]]]:
    """Per-facet ``(spaghetti, model, clean)`` raw triples. Python uses the rigorous
    AST lane (incl. the readability/MI axis); the others use the agnostic proxy lane
    with a language-neutral clean floor (idiomatic code has ~0 branches/nesting)."""
    triples: Dict[str, Tuple] = {}
    if language == "python":
        clean = M.clean_baseline_static(program)
        for f in _PY_FACETS:
            triples[f] = (_py_facet(spaghetti_src, f), _py_facet(model_src, f),
                          _py_facet(clean, f))
    else:
        clean_floor = M.clean_baseline_static(program)  # idiomatic minimal (neutral floor)
        for f in _PROXY_FACETS:
            triples[f] = (_proxy_facet(spaghetti_src, language, f),
                          _proxy_facet(model_src, language, f),
                          _proxy_facet(clean_floor, "python", f))
    return triples


def _band_check(language: str, model_src: str, program) -> Tuple[Optional[bool], Optional[bool]]:
    """Optimality band on the rigorous Python lane: is the model within eps of the
    **reachable** idiomatic clean on every size facet? The reference is
    ``clean_baseline_runnable`` (declarations + idiomatic logic) — the model must
    keep the same inputs, so the decls are shared and only the logic is compared.
    Returns ``(in_band, over_golfed)``; ``(None, None)`` for non-Python (the proxy
    lane abstains from the band)."""
    if language != "python":
        return None, None
    clean = M.clean_baseline_runnable(program)
    in_band, over = True, False
    for f in ("cyclomatic", "ast_nodes", "effort"):
        m, c = _py_facet(model_src, f), _py_facet(clean, f)
        if m is None or c is None:
            continue
        if m > c * (1 + _BAND_EPS):
            in_band = False                # still over-complicated relative to clean
        if m < c * (1 - _BAND_EPS):
            in_band, over = False, True     # undershot the floor: removed structure
    return in_band, over


def _spagh_removal(model_src: str, spaghetti_src: str) -> Optional[float]:
    """RefactorBench-style operational check: the fraction of the spaghetti's
    distinct ``SPAGH_*`` anti-pattern markers that are ABSENT from the model output
    (a genuine refactor removes them). ``None`` when the source carried no markers.
    Non-gameable by metric-golfing: it asks whether the *named* anti-patterns went
    away, complementary to the metric distance."""
    spag = M.distinct_markers(spaghetti_src)
    if not spag:
        return None
    return len(spag - M.distinct_markers(model_src)) / len(spag)


def grade_refactor_one(language: str, model_text: str, spaghetti_src: str,
                       program) -> dict:
    """Grade one refactor completion: semantic gate + gated, anti-gamed recovery.

    Recovery is the fraction of the spaghetti->clean gap closed per facet, **capped
    at the clean floor** (clamped to 1). Anti-gaming guards: a ``readability`` (MI)
    axis so terse-but-cryptic code cannot game size alone; an ``over_golfed`` flag
    when the model undershoots the clean floor on a size facet (removed structure);
    an optimality **band** (``recovered`` = equivalent AND every size facet within
    eps of the floor) instead of exact match; and a per-``SPAGH_*`` removal score."""
    model_src = extract_code(model_text)
    ok, detail = semantic_ok(language, model_src, program)
    if ok is None:  # SKIP (toolchain absent)
        return {"skip": True, "detail": detail}
    gate = 1 if ok else 0
    triples = refactor_facets(language, model_src, spaghetti_src, program) if gate else {}
    rec = {f: _recovery(s, m, c) for f, (s, m, c) in triples.items()
           if None not in (s, m, c)}
    # optimality band + over-golf flag vs the reachable idiomatic clean (recovery is
    # already capped at 1, so over-golfing is flagged, never rewarded beyond the floor).
    in_band_flag, over_golfed_flag = _band_check(language, model_src, program) if gate \
        else (None, None)
    in_band, over_golfed = bool(in_band_flag), bool(over_golfed_flag)
    spagh_removal = _spagh_removal(model_src, spaghetti_src)
    positives = [max(0.0, v) for v in rec.values() if v is not None]
    geo = M.geomean(positives) if positives else 0.0
    quality = gate * geo
    # mutually-exclusive failure-mode bucketing (within the equivalent case)
    fm = {"broke_equivalence": 0, "no_change": 0, "over_complicated": 0, "over_golfed": 0}
    recovered = 0
    if gate == 0:
        fm["broke_equivalence"] = 1
    else:
        nonneg = [v for v in rec.values() if v is not None]
        if nonneg and min(nonneg) < -1e-9:
            fm["over_complicated"] = 1
        elif over_golfed:
            fm["over_golfed"] = 1
        elif geo <= 1e-9:
            fm["no_change"] = 1
        recovered = 1 if (in_band and not over_golfed) else 0
    return {"skip": False, "semantic_ok": gate, "recovery": rec,
            "simplification_quality": quality, "recovered": recovered,
            "in_band": int(in_band), "over_golfed": int(over_golfed),
            "spagh_removal": spagh_removal, "failure_mode": fm, "detail": detail}


def aggregate_refactor(k_results: List[dict]) -> dict:
    """Aggregate k completions for one (sample, profile, language, variant) item."""
    scored = [r for r in k_results if not r.get("skip")]
    if not scored:
        return {"skip": True, "semantic_ok_rate": None, "simplification_quality": None}
    oks = [r["semantic_ok"] for r in scored]
    qs = [r["simplification_quality"] for r in scored]
    facets = sorted({f for r in scored for f in r["recovery"]})
    rec_means = {f: mean([r["recovery"][f] for r in scored
                          if r["recovery"].get(f) is not None]) for f in facets}
    fm = {b: sum(r["failure_mode"][b] for r in scored)
          for b in ("broke_equivalence", "no_change", "over_complicated", "over_golfed")}
    removals = [r["spagh_removal"] for r in scored if r.get("spagh_removal") is not None]
    return {
        "skip": False,
        "k": len(scored),
        "semantic_ok_rate": mean(oks),
        "recovery": rec_means,
        "simplification_quality": mean(qs),
        "simplification_quality_ci95": ci95_bootstrap(qs),
        "simplification_quality_stdev": stdev(qs),
        "recovered_rate": mean([r["recovered"] for r in scored]),
        "in_band_rate": mean([r["in_band"] for r in scored]),
        "over_golfed_rate": mean([r["over_golfed"] for r in scored]),
        "spagh_removal_mean": mean(removals) if removals else None,
        "failure_modes": fm,
    }


# --------------------------------------------------------------------------- #
# Baseline panel (no API): proves the task is non-trivial AND the ceiling reachable
# --------------------------------------------------------------------------- #
_FOLD = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
         ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv,
         ast.BitAnd: operator.and_, ast.BitOr: operator.or_, ast.BitXor: operator.xor}


class _RuleBasedSimplifier(ast.NodeTransformer):
    """A conservative, semantics-preserving non-LLM simplifier: constant-fold numeric
    binops and drop branches whose test is a literal constant. Deliberately weak (no
    semantic reasoning) — it is the *non-LLM mid* reference, not a solver."""

    def visit_BinOp(self, node):
        self.generic_visit(node)
        op = _FOLD.get(type(node.op))
        L, R = node.left, node.right
        if (op and isinstance(L, ast.Constant) and isinstance(R, ast.Constant)
                and isinstance(L.value, (int, float)) and isinstance(R.value, (int, float))
                and not isinstance(L.value, bool) and not isinstance(R.value, bool)):
            try:
                return ast.copy_location(ast.Constant(op(L.value, R.value)), node)
            except Exception:  # noqa: BLE001 (e.g. div by zero) -> leave as-is
                return node
        return node

    def visit_If(self, node):
        self.generic_visit(node)
        if isinstance(node.test, ast.Constant):       # `if <literal>:` is dead-code
            return node.body if node.test.value else node.orelse
        return node


def rule_based_simplify_py(src: str) -> str:
    """Constant-fold + literal-dead-branch removal over Python source (stdlib only)."""
    try:
        tree = _RuleBasedSimplifier().visit(ast.parse(src))
        ast.fix_missing_locations(tree)
        return ast.unparse(tree)
    except Exception:  # noqa: BLE001 -> a no-op baseline on parse failure
        return src


def format_baseline(language: str, src: str) -> Optional[str]:
    """A pure autoformatter pass (semantics-preserving, removes NO anti-patterns) =
    the panel's LOWER bound. Quarantined external tool; ``None`` (SKIP) if absent."""
    tool = {"python": ["black", "-q", "-"], "go": ["gofmt"]}.get(language)
    if not tool or not shutil.which(tool[0]):
        return None
    try:
        p = subprocess.run(tool, input=src, capture_output=True, text=True, timeout=20)
        return p.stdout if p.returncode == 0 and p.stdout.strip() else None
    except Exception:  # noqa: BLE001
        return None


def baseline_panel(language: str, spaghetti_src: str, program) -> Dict[str, dict]:
    """Non-LLM reference panel, graded on the same axes as a model:

    * ``formatter`` — autoformat only (lower bound: removes no anti-patterns);
    * ``rule_based`` — the conservative AST simplifier (non-LLM mid; Python only);
    * ``clean_ceiling`` — the known-optimal clean baseline (the reachable (1,1) top).

    Absent tools / non-Python lanes SKIP. Establishes that the task is non-trivial
    (formatter scores low) and the optimum is reachable (clean scores ~1)."""
    panel: Dict[str, dict] = {}
    fmt = format_baseline(language, spaghetti_src)
    panel["formatter"] = (grade_refactor_one(language, fmt, spaghetti_src, program)
                          if fmt is not None else {"skip": True, "detail": "formatter absent"})
    if language == "python":
        panel["rule_based"] = grade_refactor_one(
            language, rule_based_simplify_py(spaghetti_src), spaghetti_src, program)
        panel["clean_ceiling"] = grade_refactor_one(
            language, M.clean_baseline_runnable(program), spaghetti_src, program)
    else:
        panel["rule_based"] = {"skip": True, "detail": "rule-based AST lane is Python-only"}
        panel["clean_ceiling"] = {"skip": True, "detail": "clean rendering rigorous for Python only"}
    return panel


# --------------------------------------------------------------------------- #
# Task B — Judge
# --------------------------------------------------------------------------- #
def grade_judge_pointwise(ratings_by_level: List[List[Optional[int]]],
                          ranks: List[int]) -> dict:
    """``ratings_by_level[i]`` are the k ratings for knob level ``ranks[i]``
    (0 = cleanest). Monotonicity should be strongly negative."""
    level_mean = [mean([r for r in rs if r is not None]) for rs in ratings_by_level]
    rho = spearman(level_mean, [float(r) for r in ranks])
    sens = level_mean[0] - level_mean[-1] if len(level_mean) >= 2 else 0.0
    # count rank-adjacent inversions (rating went UP as mess increased)
    order = sorted(range(len(ranks)), key=lambda i: ranks[i])
    inversions = sum(1 for a, b in zip(order, order[1:])
                     if level_mean[b] > level_mean[a] + 1e-9)
    return {"monotonicity": rho, "sensitivity": sens, "inversions": inversions,
            "rating_by_level": level_mean, "ranks": ranks}


def grade_judge_pairwise(pairs: List[Tuple[Optional[str], Optional[str], bool]]) -> dict:
    """**Pairwise-first** judge metric with the MT-Bench **position-swap** bias
    control. Each entry is ``(pick_ab, pick_ba, clean_is_a)`` where ``pick_ab`` is
    the model's ``A``/``B`` choice when the two sources are shown in one order,
    ``pick_ba`` its choice when the order is swapped, and ``clean_is_a`` is ``True``
    iff the ground-truth more-maintainable (lower-rank) source is shown as ``A`` in
    the AB order.

    A pair is **counted only if the model is position-consistent** — i.e. it picks
    the *same underlying source* in both orders (so a model that just always says
    "A" is not rewarded). Accuracy is the fraction of consistent pairs whose chosen
    source is the cleaner one; we also report the position-consistency rate (a bias
    diagnostic) separately."""
    n = len(pairs)
    consistent = correct = unparsed = 0
    for pick_ab, pick_ba, clean_is_a in pairs:
        if pick_ab is None or pick_ba is None:
            unparsed += 1
            continue
        chose_clean_ab = (pick_ab == "A") == clean_is_a      # 'A' is clean iff clean_is_a
        chose_clean_ba = (pick_ba == "B") == clean_is_a      # order swapped -> clean is 'B'
        if chose_clean_ab != chose_clean_ba:
            continue                                         # position-inconsistent -> drop
        consistent += 1
        if chose_clean_ab:
            correct += 1
    parsed = n - unparsed
    return {"pairwise_acc": (correct / consistent) if consistent else 0.0,
            "n_pairs": n, "n_consistent": consistent,
            "position_consistency": (consistent / parsed) if parsed else 0.0,
            "n_unparsed": unparsed}


def static_complexity(language: str, src: str) -> Optional[float]:
    """A single lower-is-better static-complexity scalar for the metric-heuristic
    judge, reusing the same ``eval.metrics`` lanes the refactor grader uses (no new
    metric code). Python is rigorous (cyclomatic + AST size, both size facets); the
    other four reuse the agnostic text proxy (branch-keyword cyclomatic proxy +
    nesting depth + tokens). The facets are summed on a comparable footing via a fixed
    additive blend; only the *ordering* matters for the pairwise pick, so the exact
    weights are immaterial as long as every facet is monotone-worse with mess.
    ``None`` if the Python source does not parse (caller treats unscored as a tie)."""
    if language == "python":
        cc = _py_facet(src, "cyclomatic")
        nodes = _py_facet(src, "ast_nodes")
        if cc is None or nodes is None:
            return None
        return cc + nodes
    a = M.agnostic_metrics(src, language)
    # proxy size facets (all lower = better): branch-keyword CC proxy + nesting + tokens
    return float(a.get("branch_keywords", 0) + a.get("brace_depth", 0) + a.get("tokens", 0))


def metric_heuristic_judge(item) -> dict:
    """Deterministic, **zero-API** non-LLM judge baseline: for a :class:`JudgeItem`
    (``levels`` = ``[(label, rank, src), ...]``, rank 0 = cleanest), score each level's
    source with the static ``eval.metrics`` lane (:func:`static_complexity`, lower =
    better) and, mirroring :func:`grade_judge_pairwise` / ``tasks.score_judge_item``,
    pick the **lower-complexity** source of every unordered pair as the predicted
    cleaner (lower-rank) candidate. It is correct when that pick equals the lower-rank
    source; an exact complexity **tie counts 0.5** (a coin flip).

    This is the contrast the paper draws: an LLM judge's value *over a trivial static
    complexity heuristic*. Because complexity is (by construction) monotone with the
    knob rank, this heuristic should score HIGH on the clean->max ladder.

    Returns ``pairwise_acc`` (+ deterministic bootstrap ``pairwise_acc_ci95``) and, as
    a secondary number, the pointwise rank-vs-complexity Spearman ``spearman_rank_cc``.
    ``n_pairs``/``n_scored`` report coverage (pairs where both sources scored)."""
    levels = list(item.levels)
    # pointwise complexity per level (None where unparseable)
    comp = [static_complexity(item.language, src) for (_lab, _rank, src) in levels]
    ranks = [rank for (_lab, rank, _src) in levels]

    pair_correct: List[float] = []
    for (ia, (la, ra, sa)), (ib, (lb, rb, sb)) in combinations(enumerate(levels), 2):
        ca, cb = comp[ia], comp[ib]
        if ca is None or cb is None:
            continue                                  # unscored pair -> abstain
        if ca == cb:
            pair_correct.append(0.5)                  # complexity tie -> coin flip
            continue
        heuristic_lower = ia if ca < cb else ib       # index the heuristic calls cleaner
        truth_lower = ia if ra < rb else ib           # index that is actually lower-rank
        pair_correct.append(1.0 if heuristic_lower == truth_lower else 0.0)

    pairwise_acc = mean(pair_correct) if pair_correct else 0.0
    # pointwise: rank vs complexity should be strongly POSITIVE (mess => higher cc)
    scored = [(float(r), c) for r, c in zip(ranks, comp) if c is not None]
    rho = (spearman([r for r, _ in scored], [c for _, c in scored])
           if len(scored) >= 2 else 0.0)
    return {"pairwise_acc": pairwise_acc,
            "pairwise_acc_ci95": ci95_bootstrap(pair_correct),
            "spearman_rank_cc": rho,
            "n_pairs": len(list(combinations(levels, 2))),
            "n_scored": len(pair_correct)}


def may_judge(judge_model_family: str, author_family: str) -> bool:
    """MT-Bench self-enhancement control: a model must not judge code authored by
    its **own** family. For the core judge task the stimulus is engine-authored
    (``author_family == 'engine'``), so this is always ``True`` there; it becomes a
    live filter when an LLM judges *model-authored* code (e.g. cross-model grading of
    refactor outputs), which is why the protocol carries it."""
    return judge_model_family != author_family


def jury_majority(votes: List[Optional[str]]) -> Optional[str]:
    """Panel-of-LLM-evaluators (PoLL) aggregation: the majority A/B vote across a
    jury (each juror already filtered by :func:`may_judge`). Ties / all-unparsed ->
    ``None``."""
    valid = [v for v in votes if v is not None]
    if not valid:
        return None
    a, b = valid.count("A"), valid.count("B")
    if a == b:
        return None
    return "A" if a > b else "B"


# --------------------------------------------------------------------------- #
# Task C — Comprehension
# --------------------------------------------------------------------------- #
def grade_comprehend_one(model_text: str, program) -> dict:
    pred = extract_json_obj(model_text)
    exp = oracle(program)
    return {"exact_match": 1 if _match(pred, exp) else 0,
            "parsed": pred is not None}


def aggregate_comprehend(k_results: List[dict]) -> dict:
    ems = [r["exact_match"] for r in k_results]
    return {"k": len(k_results), "exact_match_rate": mean(ems),
            "exact_match_ci95": ci95_bootstrap([float(e) for e in ems]),
            "unparsed": sum(0 if r["parsed"] else 1 for r in k_results)}
