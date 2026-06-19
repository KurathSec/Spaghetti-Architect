"""Python generator (blueprint §14.1 / §19.1-2).

- emitter: ``brace_style=False`` (pure indentation).
- anti-patterns: ``while`` + manual indexing for membership; ``if/elif/else``
  cascade for lookup.
- safety: ``try/except Exception``; ``if x is not None``; fallback assigns the
  default directly.
- literals: Python ``repr()`` safely renders scalars / lists / dicts.
"""

from __future__ import annotations

from contextlib import contextmanager

from ..emitter import CodeEmitter
from ..ir_models import KeyValueLookup, MembershipCheck, Pattern
from .base import BaseGenerator


class PythonGenerator(BaseGenerator):
    language = "python"
    extension = ".py"

    def new_emitter(self) -> CodeEmitter:
        return CodeEmitter(brace_style=False)

    def lit(self, value: object) -> str:
        return repr(value)

    # ---- file structure ----
    def emit_file_prologue(self, e, program) -> None:
        e.comment(f"Spaghetti Architect — generated module: {program.module_name}")
        e.comment("Deliberately redundant, but syntactically correct and crash-free.")

    def emit_file_epilogue(self, e, program) -> None:
        # No output statement: the validator reads result_vars from the exec()
        # namespace directly (blueprint §15), so generated Python stays clean.
        return

    def emit_inputs(self, e, inputs) -> None:
        e.line()
        e.comment("--- run fixtures (inputs) ---")
        for name, value in inputs.items():
            e.line(f"{name} = {self.lit(value)}")

    def declare_result_default(self, e, op, pol) -> None:
        e.line(f"{op.result_var} = {self.lit(self.fallback_value(op, pol))}")

    # ---- safety: try + null guard + fallback (always on; the core promise) ----
    @contextmanager
    def safety_scope(self, e, op, pol):
        guard_target = op.collection_name if isinstance(op, MembershipCheck) else op.map_name
        fb = f"{op.result_var} = {self.lit(self.fallback_value(op, pol))}"
        with e.block("try"):
            with e.block(f"if {guard_target} is not None"):
                yield e
            with e.block("else"):
                e.line(fb)
        with e.block("except Exception"):
            e.line(fb)

    # ---- MEMBERSHIP_CHECK ----
    def emit_membership(self, e, op, patterns, pol) -> None:
        coll, tgt, res = op.collection_name, op.target_var, op.result_var

        if Pattern.DEIDIOMATIZE not in patterns:
            self._assign_bool(e, res, f"({tgt} in {coll})", patterns)
            return

        e.comment("SPAGH_001/006/008: manual index loop instead of `in`")
        e.line("_idx = 0")
        if Pattern.REDUNDANT_RECOMP in patterns:
            e.comment("SPAGH_010: recompute len() every iteration (de-hoisted)")
            bound = f"len({coll})"
        else:
            e.line(f"_n = len({coll})")
            bound = "_n"
        e.line("_match_flag = False")
        with e.block(f"while _idx < {bound}"):
            if Pattern.REDUNDANT_TEMPS in patterns:
                e.line(f"_current = {coll}[_idx]")
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
            e.line("_match_flag = True")
        if Pattern.DEAD_CODE in patterns:
            with e.block("else"):
                e.line("_match_flag = _match_flag")              # SPAGH_004 no-op

    def _assign_bool(self, e, res, expr, patterns) -> None:
        if Pattern.BOOLEAN_VERBOSITY in patterns:
            with e.block(f"if {expr} == True"):                  # SPAGH_003
                e.line(f"{res} = True")
            with e.block("else"):
                e.line(f"{res} = False")
        else:
            e.line(f"{res} = {expr}")

    # ---- KEY_VALUE_LOOKUP ----
    def emit_lookup(self, e, op, patterns, pol) -> None:
        m, key, res = op.map_name, op.key_var, op.result_var
        default_lit = self.lit(op.default_value)

        cascade = Pattern.DEIDIOMATIZE in patterns or Pattern.CASCADING_COND in patterns
        if not cascade:
            e.line(f"{res} = {m}.get({key}, {default_lit})")
            return

        e.comment("SPAGH_005: cascade enumerating every known key")
        e.line("_resolved = False")
        if Pattern.REDUNDANT_TEMPS in patterns:
            e.line(f"_key = {key}")
            k = "_key"
        else:
            k = key

        first = True
        for pk, pv in op.pairs.items():
            header = f"if {k} == {self.lit(pk)}" if first else f"elif {k} == {self.lit(pk)}"
            with e.block(header):
                e.line(f"{res} = {self.lit(pv)}")
                e.line("_resolved = True")
            first = False
        with e.block("else"):
            e.line("_resolved = False")

        with e.block("if _resolved == False"):
            e.line(f"{res} = {default_lit}")
