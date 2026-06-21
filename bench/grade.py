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
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from eval import metrics as M  # noqa: E402
from src.nodes.validator import oracle, validate  # noqa: E402

_TIMEOUT = 20  # seconds, per untrusted run

# Recovery metric facets per lane (workflow Task A): Python-AST rigorous, else proxy.
_PY_FACETS = ("cyclomatic", "ast_nodes", "effort")
_PROXY_FACETS = ("branch_keywords", "brace_depth", "tokens")


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


def refactor_recovery(language: str, model_src: str, spaghetti_src: str,
                      program) -> Dict[str, Optional[float]]:
    """Per-facet recovery toward the known-optimal clean baseline. Python uses the
    rigorous AST lane; the others use the agnostic proxy lane with a
    language-neutral clean floor (idiomatic code has ~0 branches and ~0 nesting)."""
    rec: Dict[str, Optional[float]] = {}
    if language == "python":
        clean = M.clean_baseline_static(program)
        for f in _PY_FACETS:
            s, m, c = _py_facet(spaghetti_src, f), _py_facet(model_src, f), _py_facet(clean, f)
            rec[f] = _recovery(s, m, c) if None not in (s, m, c) else None
    else:
        clean_floor = M.clean_baseline_static(program)  # idiomatic minimal (neutral floor)
        for f in _PROXY_FACETS:
            s = _proxy_facet(spaghetti_src, language, f)
            m = _proxy_facet(model_src, language, f)
            c = _proxy_facet(clean_floor, "python", f)
            rec[f] = _recovery(s, m, c)
    return rec


def grade_refactor_one(language: str, model_text: str, spaghetti_src: str,
                       program) -> dict:
    """Grade a single refactor completion: semantic gate + gated simplification."""
    model_src = extract_code(model_text)
    ok, detail = semantic_ok(language, model_src, program)
    if ok is None:  # SKIP (toolchain absent)
        return {"skip": True, "detail": detail}
    gate = 1 if ok else 0
    rec = refactor_recovery(language, model_src, spaghetti_src, program) if gate else {}
    positives = [max(0.0, v) for v in rec.values() if v is not None]
    geo = M.geomean(positives) if positives else 0.0
    quality = gate * geo
    # failure-mode bucketing
    fm = {"broke_equivalence": 0, "no_change": 0, "over_complicated": 0}
    if gate == 0:
        fm["broke_equivalence"] = 1
    else:
        nonneg = [v for v in rec.values() if v is not None]
        if nonneg and min(nonneg) < -1e-9:
            fm["over_complicated"] = 1
        elif geo <= 1e-9:
            fm["no_change"] = 1
    return {"skip": False, "semantic_ok": gate, "recovery": rec,
            "simplification_quality": quality, "failure_mode": fm, "detail": detail}


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
          for b in ("broke_equivalence", "no_change", "over_complicated")}
    return {
        "skip": False,
        "k": len(scored),
        "semantic_ok_rate": mean(oks),
        "recovery": rec_means,
        "simplification_quality": mean(qs),
        "simplification_quality_ci95": ci95_bootstrap(qs),
        "simplification_quality_stdev": stdev(qs),
        "failure_modes": fm,
    }


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


def grade_judge_pairwise(picks: List[Optional[str]], correct: List[str]) -> dict:
    """``picks[i]`` is the model's A/B answer for pair ``i``; ``correct[i]`` is the
    ground-truth less-degraded member."""
    valid = [(p, c) for p, c in zip(picks, correct) if p is not None]
    acc = mean([1.0 if p == c else 0.0 for p, c in valid]) if valid else 0.0
    return {"pairwise_acc": acc, "n_pairs": len(valid), "n_unparsed": len(picks) - len(valid)}


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
