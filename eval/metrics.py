#!/usr/bin/env python3
"""Phase 2 — the metric library (importable, pure, clock-free).

Every public function here is a pure function of source text (or an IRProgram);
**no function reads the wall clock**. Two measurement lanes:

* **Python-AST lane** (rigorous) — stdlib :mod:`ast` over the generated *Python*
  source: cyclomatic / cognitive / nesting / Halstead / maintainability.
* **language-agnostic lane** (proxy) — text/regex counting that works on all five
  sources, so cross-language claims (sloc, tokens, branch-keyword cyclomatic
  proxy, brace/indent depth) hold uniformly.

Plus amplification ratios against a synthesized clean baseline (C), anti-pattern
density/coverage from emitted ``SPAGH_*`` markers (D), a deterministic op-count
"work" measure via bytecode-opcode tracing (E), and the composite Deoptimization
Index (F). See ``deopt_eval_prompt.md`` Phase 2 and the cheat-sheet.

Design notes that matter for fidelity:
* ``elif`` chains are *flattened* in the cognitive/nesting walkers (SonarSource's
  own rule): an N-arm Python cascade is syntactically N nested ``orelse`` Ifs, but
  conceptually flat. Flattening keeps ``cyclomatic`` scaling with N (each arm is a
  branch) while ``max_nesting``/``cognitive`` track real structural depth.
* Halstead operator/operand harvesting from the AST is an approximation, but it is
  applied *identically* to baseline and output, so the inflation ratio is sound
  even though the absolute volume is model-dependent (see report Threats).
"""

from __future__ import annotations

import ast
import math
import re
import sys
from typing import Dict, List

# --------------------------------------------------------------------------- #
# SPAGH_* marker extraction (D)
# --------------------------------------------------------------------------- #
# Markers appear in comments either standalone ("SPAGH_010") or grouped
# ("SPAGH_001/006/008") where the parts after "/" are bare 3-digit ids.
_MARKER_RE = re.compile(r"SPAGH_\d{3}(?:/\d{3})*")
ALL_PATTERNS = [f"SPAGH_{i:03d}" for i in range(1, 12)]  # SPAGH_001 .. SPAGH_011
N_PATTERNS = 11

_LINE_COMMENT = {"python": "#", "javascript": "//", "go": "//", "java": "//", "cpp": "//"}


def spagh_markers(src: str) -> List[str]:
    """Every SPAGH id occurrence (grouped ids expanded), with multiplicity."""
    ids: List[str] = []
    for m in _MARKER_RE.finditer(src):
        parts = m.group(0).split("/")
        ids.append(parts[0])                 # "SPAGH_001"
        for p in parts[1:]:
            ids.append("SPAGH_" + p)         # "006" -> "SPAGH_006"
    return ids


def distinct_markers(src: str) -> set:
    return set(spagh_markers(src))


# --------------------------------------------------------------------------- #
# Language-agnostic lane helpers
# --------------------------------------------------------------------------- #
def _code_part(line: str, prefix: str) -> str:
    """Return the code portion of a line, dropping a line-comment that starts
    outside a string literal. String-aware so a comment prefix inside a literal
    (e.g. a format string containing ``//`` or ``#``) is not mistaken for a
    comment. Handles ', ", and ` (Go raw) quotes with backslash escapes."""
    i, n, q = 0, len(line), None
    while i < n:
        c = line[i]
        if q is not None:
            if c == "\\" and q != "`":
                i += 2
                continue
            if c == q:
                q = None
            i += 1
            continue
        if c in ("'", '"', "`"):
            q = c
            i += 1
            continue
        if line.startswith(prefix, i):
            return line[:i]
        i += 1
    return line


def sloc(src: str, lang: str) -> int:
    """Non-blank, non-comment physical lines."""
    prefix = _LINE_COMMENT[lang]
    return sum(1 for ln in src.splitlines() if _code_part(ln, prefix).strip())


_TOKEN_RE = re.compile(r"\w+|[^\w\s]+")  # identifier/number runs OR operator/punct runs


def tokens(src: str, lang: str) -> int:
    """Count of identifier/number runs and operator/punctuation runs (code only)."""
    prefix = _LINE_COMMENT[lang]
    return sum(len(_TOKEN_RE.findall(_code_part(ln, prefix))) for ln in src.splitlines())


_BRANCH_KW_RE = re.compile(r"\b(?:if|elif|else|for|while|switch|case|catch)\b")
_BRANCH_OP_RE = re.compile(r"&&|\|\|")


def branch_keywords(src: str, lang: str) -> int:
    """Cross-language cyclomatic proxy: branch keywords + short-circuit operators."""
    prefix = _LINE_COMMENT[lang]
    code = "\n".join(_code_part(ln, prefix) for ln in src.splitlines())
    return len(_BRANCH_KW_RE.findall(code)) + len(_BRANCH_OP_RE.findall(code))


def brace_depth(src: str, lang: str) -> int:
    """Max nesting depth. Python: by indentation (4-space unit, the emitter's);
    brace languages: by ``{``/``}`` balance, string-aware so literal braces in
    format strings do not perturb the count."""
    if lang == "python":
        mx = 0
        for ln in src.splitlines():
            code = _code_part(ln, "#")
            if not code.strip():
                continue
            indent = len(code) - len(code.lstrip(" "))
            mx = max(mx, indent // 4)
        return mx
    depth = mx = 0
    q = None
    for ln in src.splitlines():
        code = _code_part(ln, _LINE_COMMENT[lang])
        i, n = 0, len(code)
        while i < n:
            c = code[i]
            if q is not None:
                if c == "\\" and q != "`":
                    i += 2
                    continue
                if c == q:
                    q = None
                i += 1
                continue
            if c in ("'", '"', "`"):
                q = c
            elif c == "{":
                depth += 1
                mx = max(mx, depth)
            elif c == "}":
                depth = max(0, depth - 1)
            i += 1
        if q in ("'", '"'):   # single/double quotes do not span lines in our output
            q = None
    return mx


def agnostic_metrics(src: str, lang: str) -> dict:
    return {
        "sloc": sloc(src, lang),
        "tokens": tokens(src, lang),
        "branch_keywords": branch_keywords(src, lang),
        "brace_depth": brace_depth(src, lang),
    }


# --------------------------------------------------------------------------- #
# Python-AST lane (B)
# --------------------------------------------------------------------------- #
_DECISION = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler)
_NESTERS = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.ExceptHandler,
            ast.With, ast.AsyncWith, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def cyclomatic(py_src: str) -> int:
    """1 + decision points (If/For/While/ExceptHandler, comprehension ifs, and
    each BoolOp operand beyond the first)."""
    tree = ast.parse(py_src)
    dp = 0
    for node in ast.walk(tree):
        if isinstance(node, _DECISION):
            dp += 1
        elif isinstance(node, ast.comprehension):
            dp += len(node.ifs)
        elif isinstance(node, ast.BoolOp):
            dp += len(node.values) - 1
    return 1 + dp


def _traverse(py_src: str) -> dict:
    """Single walk computing cognitive complexity and max block nesting, with
    ``elif`` chains flattened (an ``elif`` increments cognitive but does not
    deepen nesting)."""
    tree = ast.parse(py_src)
    state = {"cognitive": 0, "max_nesting": 0}

    def at(depth: int) -> None:
        state["max_nesting"] = max(state["max_nesting"], depth)

    def visit(node: ast.AST, depth: int) -> None:
        if isinstance(node, ast.If):
            _visit_if(node, depth)
            return
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            state["cognitive"] += 1 + depth
            for child in node.body:
                at(depth + 1)
                visit(child, depth + 1)
            for child in node.orelse:
                at(depth + 1)
                visit(child, depth + 1)
            _scan_expr(node, depth)
            return
        if isinstance(node, ast.Try):
            for child in node.body:
                at(depth + 1)
                visit(child, depth + 1)
            for handler in node.handlers:
                state["cognitive"] += 1 + depth          # except = a control structure
                for child in handler.body:
                    at(depth + 1)
                    visit(child, depth + 1)
            for child in node.orelse + node.finalbody:
                at(depth + 1)
                visit(child, depth + 1)
            return
        # block-bearing but non-branching constructs deepen nesting only
        nests = isinstance(node, (ast.With, ast.AsyncWith, ast.FunctionDef,
                                  ast.AsyncFunctionDef, ast.ClassDef))
        for child in ast.iter_child_nodes(node):
            d = depth + 1 if nests else depth
            if nests and isinstance(child, ast.stmt):
                at(d)
            visit(child, d)

    def _visit_if(node: ast.If, depth: int) -> None:
        state["cognitive"] += 1 + depth                  # the `if`
        for child in node.body:
            at(depth + 1)
            visit(child, depth + 1)
        orelse = node.orelse
        while len(orelse) == 1 and isinstance(orelse[0], ast.If):   # flatten elif
            elif_node = orelse[0]
            state["cognitive"] += 1 + depth              # `elif` counts, same depth
            for child in elif_node.body:
                at(depth + 1)
                visit(child, depth + 1)
            orelse = elif_node.orelse
        for child in orelse:                             # trailing `else` block
            at(depth + 1)
            visit(child, depth + 1)

    def _scan_expr(node: ast.AST, depth: int) -> None:
        # descend into the loop's test/iter expressions for completeness (no
        # control structures live there in our output, but keep it general).
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, ast.stmt):
                visit(child, depth)

    for stmt in tree.body:
        visit(stmt, 0)
    return state


def cognitive(py_src: str) -> int:
    return _traverse(py_src)["cognitive"]


def max_nesting(py_src: str) -> int:
    return _traverse(py_src)["max_nesting"]


def halstead(py_src: str) -> dict:
    """Halstead metrics from AST-harvested operators/operands (a fixed, identical
    rule for baseline and output). V = N*log2(n); D = (n1/2)*(N2/n2); E = D*V."""
    tree = ast.parse(py_src)
    operators: List[str] = []
    operands: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp):
            operators.append(type(node.op).__name__)
        elif isinstance(node, ast.UnaryOp):
            operators.append(type(node.op).__name__)
        elif isinstance(node, ast.BoolOp):
            operators.extend([type(node.op).__name__] * (len(node.values) - 1))
        elif isinstance(node, ast.Compare):
            operators.extend(type(o).__name__ for o in node.ops)
        elif isinstance(node, ast.AugAssign):
            operators.append("Aug" + type(node.op).__name__)
        elif isinstance(node, ast.Assign):
            operators.append("=")
        elif isinstance(node, ast.Call):
            operators.append("()")
        elif isinstance(node, ast.Subscript):
            operators.append("[]")
        elif isinstance(node, ast.Attribute):
            operators.append(".")
            operands.append(node.attr)
        elif isinstance(node, ast.If):
            operators.append("if")
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            operators.append("for")
        elif isinstance(node, ast.While):
            operators.append("while")
        elif isinstance(node, ast.Try):
            operators.append("try")
        elif isinstance(node, ast.ExceptHandler):
            operators.append("except")
        elif isinstance(node, ast.Name):
            operands.append(node.id)
        elif isinstance(node, ast.Constant):
            operands.append(repr(node.value))
    n1, N1 = len(set(operators)), len(operators)
    n2, N2 = len(set(operands)), len(operands)
    n, N = n1 + n2, N1 + N2
    volume = N * math.log2(n) if n > 0 else 0.0
    difficulty = (n1 / 2) * (N2 / n2) if n2 > 0 else 0.0
    effort = difficulty * volume
    return {"n1": n1, "N1": N1, "n2": n2, "N2": N2,
            "volume": volume, "difficulty": difficulty, "effort": effort}


def ast_nodes(py_src: str) -> int:
    return sum(1 for _ in ast.walk(ast.parse(py_src)))


def statement_count(py_src: str) -> int:
    return sum(1 for n in ast.walk(ast.parse(py_src)) if isinstance(n, ast.stmt))


def maintainability_index(py_src: str) -> float:
    """MI = clamp_0_100((171 - 5.2 lnV - 0.23 CC - 16.2 lnSLOC) * 100/171)."""
    vol = max(halstead(py_src)["volume"], 1e-9)
    cc = cyclomatic(py_src)
    s = max(sloc(py_src, "python"), 1)
    mi = (171 - 5.2 * math.log(vol) - 0.23 * cc - 16.2 * math.log(s)) * 100 / 171
    return max(0.0, min(100.0, mi))


def python_ast_metrics(py_src: str) -> dict:
    tr = _traverse(py_src)
    return {
        "cyclomatic": cyclomatic(py_src),
        "cognitive": tr["cognitive"],
        "max_nesting": tr["max_nesting"],
        "halstead": halstead(py_src),
        "maintainability_index": maintainability_index(py_src),
        "ast_nodes": ast_nodes(py_src),
        "statements": statement_count(py_src),
    }


# --------------------------------------------------------------------------- #
# Clean baseline + amplification ratios (C)
# --------------------------------------------------------------------------- #
def clean_baseline_lines(program) -> List[str]:
    """One idiomatic Python line per operation, from oracle semantics — the
    known-optimal floor for all four IR operations."""
    lines: List[str] = []
    for op in program.operations:
        if op.op == "MEMBERSHIP_CHECK":
            lines.append(f"{op.result_var} = {op.target_var} in {op.collection_name}")
        elif op.op == "KEY_VALUE_LOOKUP":  # r = m.get(k, d)
            lines.append(
                f"{op.result_var} = {op.map_name}.get({op.key_var}, {op.default_value!r})"
            )
        elif op.op == "AGGREGATE":         # r = sum|min|max(collection)
            lines.append(f"{op.result_var} = {op.mode}({op.collection_name})")
        elif op.op == "CONDITIONAL_SELECT":  # r = then if subj <cmp> val else else_
            lines.append(
                f"{op.result_var} = {op.then_value!r} if {op.subject_var} "
                f"{op.comparator} {op.compare_value!r} else {op.else_value!r}"
            )
        else:  # pragma: no cover — defensive: a new op must add a baseline here
            raise ValueError(f"clean_baseline_lines: unknown operation {op.op!r}")
    return lines


def clean_baseline_static(program) -> str:
    """The minimal idiomatic expression of the logic (no input declarations);
    measured by the lane-B functions to form the inflation denominators."""
    return "\n".join(clean_baseline_lines(program)) + "\n"


def clean_baseline_runnable(program) -> str:
    """Idiomatic baseline with input declarations prepended, so it can be run for
    the op-count baseline. Uses ``map.get``/``in`` on the mirrored inputs, hence
    reproduces the oracle (callers should assert this)."""
    decls = [f"{name} = {value!r}" for name, value in program.inputs.items()]
    return "\n".join(decls + clean_baseline_lines(program)) + "\n"


def inflation_ratios(out_py: str, clean_py: str, n_operations: int) -> dict:
    return {
        "loc": sloc(out_py, "python") / max(1, sloc(clean_py, "python")),
        "ast": ast_nodes(out_py) / max(1, ast_nodes(clean_py)),
        "token": tokens(out_py, "python") / max(1, tokens(clean_py, "python")),
        "cc": cyclomatic(out_py) / max(1, cyclomatic(clean_py)),
        "statements_per_op": statement_count(out_py) / max(1, n_operations),
    }


# --------------------------------------------------------------------------- #
# Anti-pattern density / coverage / matrix (D)
# --------------------------------------------------------------------------- #
def spagh_density(src: str, lang: str) -> float:
    s = sloc(src, lang)
    return len(spagh_markers(src)) / s if s else 0.0


def pattern_coverage(sources: Dict[str, str]) -> float:
    seen: set = set()
    for src in sources.values():
        seen |= distinct_markers(src)
    return len(seen & set(ALL_PATTERNS)) / N_PATTERNS


def pattern_lang_matrix(sources: Dict[str, str]) -> Dict[str, Dict[str, int]]:
    per_lang = {lang: distinct_markers(src) for lang, src in sources.items()}
    return {
        pid: {lang: (1 if pid in ids else 0) for lang, ids in per_lang.items()}
        for pid in ALL_PATTERNS
    }


# --------------------------------------------------------------------------- #
# Op-count / work (E) — deterministic bytecode-opcode trace, never the clock
# --------------------------------------------------------------------------- #
def count_work(py_src: str, filename: str = "<spaghetti>") -> dict:
    """Execute the (self-contained) Python source and count work deterministically:

    * ``opcodes`` — bytecode instructions executed *in the generated module code*
      (library/builtin/wrapper frames are excluded by ``co_filename``), a clock-free
      model of interpreted work that scales with loop iterations (so SPAGH_010's
      de-hoisting shows up);
    * ``len_calls`` — calls to ``len`` (SPAGH_010's fingerprint: recomputed every
      iteration), via a counting wrapper installed in the run namespace.

    Uses :mod:`sys.monitoring` (PEP 669, the supported per-instruction mechanism on
    CPython 3.12+; the older ``settrace`` opcode hook no longer fires here). The
    callback returns ``None`` (never ``DISABLE``) so repeated executions of the same
    instruction — i.e. loop bodies — are each counted.
    """
    counters = {"opcodes": 0, "len_calls": 0}
    real_len = len

    def counting_len(x):
        counters["len_calls"] += 1
        return real_len(x)

    code = compile(py_src, filename, "exec")
    mon = sys.monitoring
    tool_id = mon.PROFILER_ID

    def on_instruction(code_obj, offset):
        if code_obj.co_filename == filename:
            counters["opcodes"] += 1
        return None  # NOT DISABLE: keep counting every (repeated) execution

    run_globals = {"len": counting_len}
    mon.use_tool_id(tool_id, "spaghetti-work")
    try:
        mon.register_callback(tool_id, mon.events.INSTRUCTION, on_instruction)
        mon.set_events(tool_id, mon.events.INSTRUCTION)
        exec(code, run_globals)
    finally:
        mon.set_events(tool_id, 0)
        mon.register_callback(tool_id, mon.events.INSTRUCTION, None)
        mon.free_tool_id(tool_id)
    return dict(counters)


# --------------------------------------------------------------------------- #
# Composite headline — Deoptimization Index (F)
# --------------------------------------------------------------------------- #
def geomean(values: List[float]) -> float:
    vals = list(values)
    if not vals:
        return 0.0
    prod = 1.0
    for v in vals:
        prod *= v
    return prod ** (1.0 / len(vals))


def nesting_ratio(out_nesting: int, clean_nesting: int) -> float:
    """+1-smoothed so a straight-line baseline (nesting 0) does not divide by zero."""
    return (out_nesting + 1) / (clean_nesting + 1)


def deoptimization_index(gate: int, cc_infl: float, effort_infl: float,
                         nest_ratio: float, ast_infl: float) -> float:
    """DI = gate * geomean(cc_inflation, halstead_effort_inflation, nesting_ratio,
    ast_inflation). The gate (0/1) zeroes any incorrect output."""
    return gate * geomean([cc_infl, effort_infl, nest_ratio, ast_infl])
