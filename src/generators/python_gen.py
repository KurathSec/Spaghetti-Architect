"""Python generator (blueprint §14.1 / §19.1-2).

- emitter: ``brace_style=False`` (pure indentation).
- anti-patterns: ``while`` + manual indexing for membership; ``if/elif/else``
  cascade for lookup.
- safety: ``try/except Exception``; ``if x is not None``; fallback assigns the
  default directly.
- literals: Python ``repr()`` safely renders scalars / lists / dicts.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager

from ..emitter import CodeEmitter
from ..ir_models import Pattern
from .base import BaseGenerator, V21_NEST_CAP


class PythonGenerator(BaseGenerator):
    language = "python"
    extension = ".py"

    def new_emitter(self, annotate: bool = True, mode=None) -> CodeEmitter:
        return CodeEmitter(brace_style=False, annotate=annotate, mode=mode)

    def lit(self, value: object) -> str:
        return repr(value)

    # ---- file structure ----
    def emit_file_prologue(self, e, program) -> None:
        e.comment(f"Spaghetti Architect — generated module: {program.module_name}", kind="header")
        e.comment("Deliberately redundant, but syntactically correct and crash-free.", kind="header")

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
        target = self.guard_target(op)
        fb = f"{op.result_var} = {self.lit(self.fallback_value(op, pol))}"
        with e.block("try"):
            if pol.needs_null_guard and target is not None:
                with e.block(f"if {target} is not None"):
                    yield e
                with e.block("else"):
                    e.line(fb)
            else:
                yield e
        with e.block("except Exception"):
            e.line(fb)

    # ---- v2.1 helpers (additive; never reached at spec 2.0) ----
    def _overguard(self, patterns) -> bool:
        return self._v21 and Pattern.OVER_GUARDING in patterns

    def _nested(self, patterns) -> bool:
        return self._v21 and Pattern.CASCADING_COND in patterns

    # ---- MEMBERSHIP_CHECK ----
    def emit_membership(self, e, op, patterns, pol) -> None:
        coll, tgt, res = op.collection_name, op.target_var, op.result_var

        if Pattern.DEIDIOMATIZE not in patterns:
            self._assign_bool(e, res, f"({tgt} in {coll})", patterns)
            return

        e.comment("SPAGH_001/006/008: manual index loop instead of `in`", kind="marker")
        e.line("_idx = 0")
        if Pattern.REDUNDANT_RECOMP in patterns:
            e.comment("SPAGH_010: recompute len() every iteration (de-hoisted)", kind="marker")
            bound = f"len({coll})"
        else:
            e.line(f"_n = len({coll})")
            bound = "_n"
        e.line("_match_flag = False")
        with e.block(f"while _idx < {bound}"):
            def body() -> None:
                if Pattern.REDUNDANT_TEMPS in patterns:
                    e.line(f"_current = {coll}[_idx]")
                    current = "_current"
                else:
                    current = f"{coll}[_idx]"
                self._emit_match(e, current, tgt, patterns)
            if self._overguard(patterns):
                e.comment("SPAGH_007: redundant bounds re-check before use", kind="marker")
                with e.block(f"if _idx >= 0 and _idx < {bound}"):
                    body()
            else:
                body()
            e.line("_idx = _idx + 1")
        self._assign_bool(e, res, "_match_flag", patterns)

    def _match_cmp(self, current, tgt, patterns) -> str:
        # SPAGH_011: Yoda flips to `constant == variable`.
        lhs, rhs = (tgt, current) if Pattern.YODA_CONDITIONS in patterns else (current, tgt)
        return f"{lhs} == {rhs}"

    def _emit_match(self, e, current, tgt, patterns) -> None:
        cmp = self._match_cmp(current, tgt, patterns)
        if Pattern.OPAQUE_PREDICATE in patterns:
            e.comment("SPAGH_009: opaque predicate (always true: n*(n+1) is even)", kind="marker")
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

        e.comment("SPAGH_005: cascade enumerating every known key", kind="marker")
        e.line("_resolved = False")
        if Pattern.REDUNDANT_TEMPS in patterns:
            e.line(f"_key = {key}")
            k = "_key"
        else:
            k = key

        def emit_cascade() -> None:
            if self._nested(patterns):
                e.comment("SPAGH_005: nested cascade (one else-scope per key)", kind="marker")
                self._emit_nested_cascade(e, k, list(op.pairs.items()), res)
                return
            first = True
            for pk, pv in op.pairs.items():
                header = (f"if {k} == {self.lit(pk)}" if first
                          else f"elif {k} == {self.lit(pk)}")
                with e.block(header):
                    e.line(f"{res} = {self.lit(pv)}")
                    e.line("_resolved = True")
                first = False
            with e.block("else"):
                e.line("_resolved = False")

        if self._overguard(patterns):
            e.comment("SPAGH_007: redundant key re-check before use", kind="marker")
            with e.block(f"if {k} is not None"):
                emit_cascade()
        else:
            emit_cascade()

        with e.block("if _resolved == False"):
            e.line(f"{res} = {default_lit}")

    def _emit_nested_cascade(self, e, k, items, res) -> None:
        """Genuinely nested else-scopes in groups of ``V21_NEST_CAP``; groups
        chain FLAT through ``if _resolved == False`` links so depth stays
        bounded no matter how many keys the map enumerates."""
        for gstart in range(0, len(items), V21_NEST_CAP):
            group = items[gstart:gstart + V21_NEST_CAP]
            if gstart == 0:
                self._emit_nested_group(e, k, group, res)
            else:
                with e.block("if _resolved == False"):
                    self._emit_nested_group(e, k, group, res)

    def _emit_nested_group(self, e, k, group, res) -> None:
        with ExitStack() as stack:
            for i, (pk, pv) in enumerate(group):
                if i > 0:
                    stack.enter_context(e.block("else"))
                with e.block(f"if {k} == {self.lit(pk)}"):
                    e.line(f"{res} = {self.lit(pv)}")
                    e.line("_resolved = True")
                if i == len(group) - 1:
                    with e.block("else"):
                        e.line("_resolved = False")

    # ---- AGGREGATE ----
    def emit_aggregate(self, e, op, patterns, pol) -> None:
        coll, res, mode = op.collection_name, op.result_var, op.mode

        if Pattern.DEIDIOMATIZE not in patterns:
            e.line(f"{res} = {mode}({coll})")
            return

        e.comment(f"SPAGH_001/006/008: manual {mode} reduction instead of {mode}()", kind="marker")
        e.line("_idx = 0")
        if Pattern.REDUNDANT_RECOMP in patterns:
            e.comment("SPAGH_010: recompute len() every iteration (de-hoisted)", kind="marker")
            bound = f"len({coll})"
        else:
            e.line(f"_n = len({coll})")
            bound = "_n"
        e.line(f"_acc = {'0' if mode == 'sum' else f'{coll}[0]'}")
        with e.block(f"while _idx < {bound}"):
            def body() -> None:
                if Pattern.REDUNDANT_TEMPS in patterns:
                    e.line(f"_current = {coll}[_idx]")
                    current = "_current"
                else:
                    current = f"{coll}[_idx]"
                self._emit_reduce(e, mode, current, patterns)
            if self._overguard(patterns):
                e.comment("SPAGH_007: redundant bounds re-check before use", kind="marker")
                with e.block(f"if _idx >= 0 and _idx < {bound}"):
                    body()
            else:
                body()
            e.line("_idx = _idx + 1")
        e.line(f"{res} = _acc")

    def _emit_reduce(self, e, mode, current, patterns) -> None:
        if Pattern.OPAQUE_PREDICATE in patterns:
            e.comment("SPAGH_009: opaque predicate (always true: n*(n+1) is even)", kind="marker")
            with e.block("if (_idx * (_idx + 1)) % 2 == 0"):
                self._reduce_body(e, mode, current, patterns)
        else:
            self._reduce_body(e, mode, current, patterns)

    def _reduce_body(self, e, mode, current, patterns) -> None:
        if mode == "sum":
            e.line(f"_acc = _acc + {current}")
            if Pattern.DEAD_CODE in patterns:
                e.line("_acc = _acc")                         # SPAGH_004 no-op
            return
        with e.block(f"if {self.reduce_cmp(mode, current, patterns)}"):
            e.line(f"_acc = {current}")
        if Pattern.DEAD_CODE in patterns:
            with e.block("else"):
                e.line("_acc = _acc")                         # SPAGH_004 no-op

    # ---- CONDITIONAL_SELECT ----
    def emit_conditional(self, e, op, patterns, pol) -> None:
        res = op.result_var
        then_lit, else_lit = self.lit(op.then_value), self.lit(op.else_value)
        cond = self.select_cond(op, patterns)

        branch = Pattern.DEIDIOMATIZE in patterns or Pattern.CASCADING_COND in patterns
        if not branch:
            e.line(f"{res} = {then_lit} if {cond} else {else_lit}")
            return

        e.comment("SPAGH_001/005: expand the ternary into an explicit if/else", kind="marker")
        if Pattern.REDUNDANT_TEMPS in patterns:
            e.line(f"_cond = {cond}")
            cond = "_cond"

        if self._nested(patterns):
            e.comment("SPAGH_005: two-stage dispatch through a branch selector", kind="marker")
            e.line("_branch = 0")
            if self._overguard(patterns):
                e.comment("SPAGH_007: redundant condition re-check before use", kind="marker")
                with e.block(f"if {cond}"):
                    with e.block(f"if {cond}"):
                        e.line("_branch = 1")
            else:
                with e.block(f"if {cond}"):
                    e.line("_branch = 1")
            with e.block("if _branch == 1"):
                e.line(f"{res} = {then_lit}")
            with e.block("else"):
                e.line(f"{res} = {else_lit}")
                if Pattern.DEAD_CODE in patterns:
                    e.line(f"{res} = {res}")                  # SPAGH_004 no-op
            return

        with e.block(f"if {cond}"):
            e.line(f"{res} = {then_lit}")
        with e.block("else"):
            e.line(f"{res} = {else_lit}")
            if Pattern.DEAD_CODE in patterns:
                e.line(f"{res} = {res}")                      # SPAGH_004 no-op
