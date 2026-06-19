"""CLI panel + validation (blueprint §17).

Loads an IR (from an argument or the bundled ``examples/`` suite), runs the engine,
and renders each language's validation status into a pure-stdlib box panel. With
``--source`` it also prints the generated code. Exit code is non-zero if any
language FAILs (SKIP never fails), so CI can pick it up.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Tuple

from .engine import Engine
from .nodes.validator import oracle, validate

_LANG_ORDER = ["python", "javascript", "go", "java", "cpp"]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Spaghetti Architect transpiler")
    ap.add_argument("ir", nargs="?", help="path to IR JSON; omit to run the built-in example suite")
    ap.add_argument("--profile", default="max", choices=["minimal", "standard", "max"])
    ap.add_argument("--lang", action="append",
                    help="only output the given language(s) (repeatable)")
    ap.add_argument("--source", action="store_true", help="also print the generated source")
    args = ap.parse_args(argv)

    here = os.path.dirname(os.path.abspath(__file__))
    db = os.path.join(here, "..", "config", "anti_patterns_db.json")
    engine = Engine(db, args.profile)

    try:
        cases = _load_cases(args.ir, here)
    except (OSError, ValueError) as ex:
        print(f"error loading IR: {ex}", file=sys.stderr)
        return 2

    # Phase 1 — generate every case sequentially (generation mutates shared
    # generator state, so it must not overlap; it is instant anyway).
    generated: List[Tuple[str, Optional[dict], Optional[Exception]]] = []
    for name, raw in cases:
        try:
            generated.append((name, engine.generate(raw), None))
        except Exception as ex:  # noqa: BLE001 - IRValidationError and friends
            generated.append((name, None, ex))

    # Phase 2 — validate every (case, language) pair concurrently. This is the
    # slow, subprocess-bound part; overlapping it across cases *and* languages
    # collapses the wall-clock to roughly the single slowest compile/run. Results
    # are keyed by (case, language), so rendering stays fully deterministic.
    tasks = [
        (name, lang, src, out["program"])
        for name, out, _ in generated if out is not None
        for lang, src in out["sources"].items()
    ]
    validations = {}
    if tasks:
        workers = min(len(tasks), (os.cpu_count() or 4) * 2)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                (name, lang): pool.submit(validate, lang, src, program)
                for name, lang, src, program in tasks
            }
            validations = {key: fut.result() for key, fut in futures.items()}

    # Phase 3 — render in the original case order.
    overall_ok = True
    for name, out, err in generated:
        if err is not None:
            print(f"[{name}] transpile error: {err}", file=sys.stderr)
            overall_ok = False
            continue
        out = {**out, "validation": {lang: validations[(name, lang)] for lang in out["sources"]}}
        _render_panel(name, out, engine.profile, only=args.lang)
        if args.source:
            _print_sources(out, only=args.lang)
        for res in out["validation"].values():
            if res.status == "FAIL":
                overall_ok = False

    return 0 if overall_ok else 1


# --------------------------------------------------------------------------- #
# case loading
# --------------------------------------------------------------------------- #
def _load_cases(ir: Optional[str], here: str) -> List[Tuple[str, dict]]:
    if ir:
        with open(ir, encoding="utf-8") as f:
            return [(_case_name(ir), json.load(f))]
    examples = os.path.join(here, "..", "examples")
    paths = sorted(glob.glob(os.path.join(examples, "*.json")))
    if not paths:
        raise ValueError(f"no examples found in {examples}")
    cases = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            cases.append((_case_name(p), json.load(f)))
    return cases


def _case_name(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def _langs(out: dict, only: Optional[List[str]]) -> List[str]:
    present = [l for l in _LANG_ORDER if l in out["validation"]]
    if only:
        present = [l for l in present if l in only]
    return present


def _summary(lang: str, status: str, detail: str, expected: dict) -> str:
    if status != "PASS":
        return detail
    if lang == "python":
        return "  ".join(f"{k}={expected[k]!r}" for k in expected)
    return json.dumps(expected, separators=(",", ":"))


def _render_panel(name: str, out: dict, profile: str, only: Optional[List[str]]) -> None:
    expected = oracle(out["program"])
    rows = []
    for lang in _langs(out, only):
        res = out["validation"][lang]
        summ = _summary(lang, res.status, res.detail, expected)
        rows.append(f"{lang:<11}{res.status:<5} {summ}")

    left = f"─ case: {name} "
    right = f" profile: {profile} ─"
    body_w = max([len(r) for r in rows] + [0]) + 1     # +1 leading space
    width = max(body_w, len(left) + len(right))

    fill = width - len(left) - len(right)
    print("┌" + left + "─" * fill + right + "┐")
    for r in rows:
        print("│ " + r.ljust(width - 1) + "│")
    print("└" + "─" * width + "┘")


def _print_sources(out: dict, only: Optional[List[str]]) -> None:
    for lang in _langs(out, only):
        src = out["sources"][lang]
        print()
        print(f"===== {lang} =====")
        print(src, end="" if src.endswith("\n") else "\n")


if __name__ == "__main__":
    sys.exit(main())
