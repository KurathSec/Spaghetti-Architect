"""Go generator (blueprint §14.3).

- emitter: ``brace_style=True``; package name ``main``.
- anti-patterns: explicit slice-index loop (no ``range``) for membership; ``switch``
  for lookup.
- safety: ``if s != nil`` guard; a ``func(){ defer/recover }()`` wrapper to simulate
  try/catch.
- output: a single JSON line via ``fmt``.

Go-specific traps handled here (blueprint §21.2): unused variables/imports are
**compile errors**, and ``}`` followed by ``else`` on a new line breaks under
automatic semicolon insertion. So: every input is touched with ``_ = name``, the
only import (``fmt``) is always used by the JSON print, and no ``else`` is ever
emitted (a pre-set default value covers the negative branch instead).
"""

from __future__ import annotations

import json
from contextlib import contextmanager

from ..emitter import CodeEmitter
from ..ir_models import Pattern

from .base import BaseGenerator

_GO_TYPE = {"bool": "bool", "int": "int", "float": "float64", "str": "string"}
_GO_VERB = {"bool": "%v", "int": "%d", "float": "%g", "str": "%q"}


class GoGenerator(BaseGenerator):
    language = "go"
    extension = ".go"

    def new_emitter(self) -> CodeEmitter:
        return CodeEmitter(brace_style=True)

    def lit(self, value: object) -> str:
        tag = _tag(value)
        if tag == "bool":
            return "true" if value else "false"
        if tag == "str":
            return json.dumps(value)          # Go string literals match JSON for ASCII
        if tag == "null":
            return "nil"
        return repr(value)                    # int / float

    # ---- file structure ----
    def emit_file_prologue(self, e, program) -> None:
        e.comment(f"Spaghetti Architect — generated module: {program.module_name}")
        e.comment("Deliberately redundant, but syntactically correct and crash-free.")
        e.line("package main")
        e.line()
        e.line('import "fmt"')
        e.line()
        e.open_brace("func main()")

    def emit_inputs(self, e, inputs) -> None:
        e.comment("--- run fixtures (inputs) ---")
        for name, value in inputs.items():
            e.line(f"{name} := {self._typed_lit(value)}")
        for name in inputs:
            e.line(f"_ = {name}")              # keep every input "used" for the Go compiler

    def emit_file_epilogue(self, e, program) -> None:
        specs = self.result_specs(program)
        parts = [f'\\"{name}\\": {_GO_VERB[tag]}' for name, tag in specs]
        args = ", ".join(name for name, _ in specs)
        fmt_str = "{" + ", ".join(parts) + "}"
        e.line()
        e.comment("emit result_vars as one JSON line for the validator")
        e.line(f'fmt.Println(fmt.Sprintf("{fmt_str}", {args}))')
        e.close_brace()                        # close func main

    def declare_result_default(self, e, op, pol) -> None:
        e.line(f"{op.result_var} := {self.lit(self.fallback_value(op, pol))}")

    # ---- safety: defer/recover IIFE + nil guard (no else; default pre-set) ----
    @contextmanager
    def safety_scope(self, e, op, pol):
        target = self.guard_target(op)
        fb_val = self.lit(self.fallback_value(op, pol))
        with e.raw_block("func() {", "}()"):
            with e.raw_block("defer func() {", "}()"):
                with e.block("if r := recover(); r != nil"):
                    e.line(f"{op.result_var} = {fb_val}")
            if pol.needs_null_guard and target is not None:
                with e.block(f"if {target} != nil"):
                    yield e
            else:
                yield e

    # ---- MEMBERSHIP_CHECK ----
    def emit_membership(self, e, op, patterns, pol) -> None:
        coll, tgt, res = op.collection_name, op.target_var, op.result_var

        if Pattern.DEIDIOMATIZE not in patterns:
            e.line(f"{res} = false")
            with e.block(f"for _, _v := range {coll}"):
                with e.block(f"if _v == {tgt}"):
                    e.line(f"{res} = true")
            return

        e.comment("SPAGH_001/006: manual index loop instead of range")
        e.line("_idx := 0")
        if Pattern.REDUNDANT_RECOMP in patterns:
            e.comment("SPAGH_010: recompute len() every iteration (de-hoisted)")
            bound = f"len({coll})"
        else:
            e.line(f"_n := len({coll})")
            bound = "_n"
        e.line("_match_flag := false")
        with e.block(f"for _idx < {bound}"):
            if Pattern.REDUNDANT_TEMPS in patterns:
                e.line(f"_current := {coll}[_idx]")
                current = "_current"
            else:
                current = f"{coll}[_idx]"
            self._emit_match(e, current, tgt, patterns)
            e.line("_idx = _idx + 1")
        self._assign_bool(e, res, "_match_flag", patterns)

    def _match_cmp(self, current, tgt, patterns) -> str:
        # SPAGH_011: Yoda flips to `constant == variable`.
        lhs, rhs = (tgt, current) if Pattern.YODA_CONDITIONS in patterns else (current, tgt)
        return f"{lhs} == {rhs}"

    def _emit_match(self, e, current, tgt, patterns) -> None:
        cmp = self._match_cmp(current, tgt, patterns)
        if Pattern.OPAQUE_PREDICATE in patterns:
            e.comment("SPAGH_009: opaque predicate (always true: n*(n+1) is even)")
            with e.block("if (_idx * (_idx + 1)) % 2 == 0"):
                self._match_body(e, cmp, patterns)
        else:
            self._match_body(e, cmp, patterns)

    def _match_body(self, e, cmp, patterns) -> None:
        with e.block(f"if {cmp}"):
            e.line("_match_flag = true")
        if Pattern.DEAD_CODE in patterns:
            e.line("_ = _match_flag")                            # SPAGH_004 no-op (ASI-safe)

    def _assign_bool(self, e, res, expr, patterns) -> None:
        if Pattern.BOOLEAN_VERBOSITY in patterns:
            e.line(f"{res} = false")
            with e.block(f"if {expr} == true"):                 # SPAGH_003 (no else: ASI)
                e.line(f"{res} = true")
        else:
            e.line(f"{res} = {expr}")

    # ---- KEY_VALUE_LOOKUP ----
    def emit_lookup(self, e, op, patterns, pol) -> None:
        m, key, res = op.map_name, op.key_var, op.result_var
        default_lit = self.lit(op.default_value)

        cascade = Pattern.DEIDIOMATIZE in patterns or Pattern.CASCADING_COND in patterns
        if not cascade:
            with e.block(f"if _v, _ok := {m}[{key}]; _ok"):
                e.line(f"{res} = _v")
            return

        e.comment("SPAGH_005: switch enumerating every known key")
        e.line("_resolved := false")
        if Pattern.REDUNDANT_TEMPS in patterns:
            e.line(f"_key := {key}")
            k = "_key"
        else:
            k = key

        with e.block(f"switch {k}"):
            for pk, pv in op.pairs.items():
                e.line(f"case {self.lit(pk)}:")
                with e.indented():
                    e.line(f"{res} = {self.lit(pv)}")
                    e.line("_resolved = true")
            e.line("default:")
            with e.indented():
                e.line("_resolved = false")

        with e.block("if _resolved == false"):
            e.line(f"{res} = {default_lit}")

    # ---- AGGREGATE (Go has no else: dead-code is an ASI-safe `_ =` no-op) ----
    def emit_aggregate(self, e, op, patterns, pol) -> None:
        coll, res, mode = op.collection_name, op.result_var, op.mode
        acc_init = "0" if mode == "sum" else f"{coll}[0]"

        if Pattern.DEIDIOMATIZE not in patterns:
            # Go 1.16 has no slice sum/min/max builtin; a clean range loop is the idiom.
            e.line(f"_acc := {acc_init}")
            with e.block(f"for _, _v := range {coll}"):
                self._reduce_body(e, mode, "_v", frozenset())
            e.line(f"{res} = _acc")
            return

        e.comment(f"SPAGH_001/006/008: manual {mode} reduction instead of a range loop")
        e.line("_idx := 0")
        if Pattern.REDUNDANT_RECOMP in patterns:
            e.comment("SPAGH_010: recompute len() every iteration (de-hoisted)")
            bound = f"len({coll})"
        else:
            e.line(f"_n := len({coll})")
            bound = "_n"
        e.line(f"_acc := {acc_init}")
        with e.block(f"for _idx < {bound}"):
            if Pattern.REDUNDANT_TEMPS in patterns:
                e.line(f"_current := {coll}[_idx]")
                current = "_current"
            else:
                current = f"{coll}[_idx]"
            self._emit_reduce(e, mode, current, patterns)
            e.line("_idx = _idx + 1")
        e.line(f"{res} = _acc")

    def _emit_reduce(self, e, mode, current, patterns) -> None:
        if Pattern.OPAQUE_PREDICATE in patterns:
            e.comment("SPAGH_009: opaque predicate (always true: n*(n+1) is even)")
            with e.block("if (_idx * (_idx + 1)) % 2 == 0"):
                self._reduce_body(e, mode, current, patterns)
        else:
            self._reduce_body(e, mode, current, patterns)

    def _reduce_body(self, e, mode, current, patterns) -> None:
        if mode == "sum":
            e.line(f"_acc = _acc + {current}")
            if Pattern.DEAD_CODE in patterns:
                e.line("_ = _acc")                            # SPAGH_004 no-op (ASI-safe)
            return
        with e.block(f"if {self.reduce_cmp(mode, current, patterns)}"):
            e.line(f"_acc = {current}")
        if Pattern.DEAD_CODE in patterns:
            e.line("_ = _acc")                                # SPAGH_004 no-op (ASI-safe)

    # ---- CONDITIONAL_SELECT (no ternary / no else in Go: default holds `else`) ----
    def emit_conditional(self, e, op, patterns, pol) -> None:
        res = op.result_var
        then_lit = self.lit(op.then_value)
        cond = self.select_cond(op, patterns)

        e.comment("SPAGH_001/005: explicit if; the pre-set default carries the else branch")
        if Pattern.REDUNDANT_TEMPS in patterns:
            e.line(f"_cond := {cond}")
            cond = "_cond"
        with e.block(f"if {cond}"):
            e.line(f"{res} = {then_lit}")
        if Pattern.DEAD_CODE in patterns:
            e.line(f"_ = {res}")                              # SPAGH_004 no-op (ASI-safe)

    # ---- typed literals for declarations ----
    def _typed_lit(self, value: object) -> str:
        if isinstance(value, list):
            t = _GO_TYPE[self.array_elem_tag(value)]
            items = ", ".join(self.lit(v) for v in value)
            return f"[]{t}{{{items}}}"
        if isinstance(value, dict):
            t = _GO_TYPE[self.map_value_tag(value)]
            items = ", ".join(f"{json.dumps(k)}: {self.lit(v)}" for k, v in value.items())
            return f"map[string]{t}{{{items}}}"
        return self.lit(value)


def _tag(value: object) -> str:
    from ..ir_models import scalar_tag
    return scalar_tag(value)
