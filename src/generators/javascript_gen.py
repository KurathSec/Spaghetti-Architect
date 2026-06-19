"""JavaScript (ES5) generator (blueprint §14.2).

- emitter: ``brace_style=True``.
- anti-patterns: explicit ``for`` index loop for membership; ``switch``-case for lookup.
- safety: ``typeof``/null check + ``try { } catch (e) { }``; ``var`` (ES5).
- output: ``console.log(JSON.stringify(...))`` so the validator can parse one JSON line.
"""

from __future__ import annotations

import json
from contextlib import contextmanager

from ..emitter import CodeEmitter
from ..ir_models import MembershipCheck, Pattern
from .base import BaseGenerator


class JavaScriptGenerator(BaseGenerator):
    language = "javascript"
    extension = ".js"

    def new_emitter(self) -> CodeEmitter:
        return CodeEmitter(brace_style=True)

    def lit(self, value: object) -> str:
        # JSON is a syntactic subset of JS literals for scalars/arrays/objects.
        return json.dumps(value)

    # ---- file structure ----
    def emit_file_prologue(self, e, program) -> None:
        e.comment(f"Spaghetti Architect — generated module: {program.module_name}")
        e.comment("Deliberately redundant, but syntactically correct and crash-free.")

    def emit_inputs(self, e, inputs) -> None:
        e.line()
        e.comment("--- run fixtures (inputs) ---")
        for name, value in inputs.items():
            e.line(f"var {name} = {self.lit(value)};")

    def emit_file_epilogue(self, e, program) -> None:
        pairs = ", ".join(f"{name}: {name}" for name, _ in self.result_specs(program))
        e.line()
        e.comment("emit result_vars as one JSON line for the validator")
        e.line("console.log(JSON.stringify({" + pairs + "}));")

    def declare_result_default(self, e, op, pol) -> None:
        e.line(f"var {op.result_var} = {self.lit(self.fallback_value(op, pol))};")

    # ---- safety: try + null guard + fallback ----
    @contextmanager
    def safety_scope(self, e, op, pol):
        target = op.collection_name if isinstance(op, MembershipCheck) else op.map_name
        fb = f"{op.result_var} = {self.lit(self.fallback_value(op, pol))};"
        with e.block("try"):
            with e.block(f"if ({target} !== null && {target} !== undefined)"):
                yield e
            with e.block("else"):
                e.line(fb)
        with e.block("catch (e)"):
            e.line(fb)

    # ---- MEMBERSHIP_CHECK ----
    def emit_membership(self, e, op, patterns, pol) -> None:
        coll, tgt, res = op.collection_name, op.target_var, op.result_var

        if Pattern.DEIDIOMATIZE not in patterns:
            self._assign_bool(e, res, f"({coll}.indexOf({tgt}) !== -1)", patterns)
            return

        e.comment("SPAGH_001/006: explicit index loop instead of indexOf")
        e.line("var _idx = 0;")
        if Pattern.REDUNDANT_RECOMP in patterns:
            e.comment("SPAGH_010: recompute .length every iteration (de-hoisted)")
            bound = f"{coll}.length"
        else:
            e.line(f"var _n = {coll}.length;")
            bound = "_n"
        e.line("var _match_flag = false;")
        with e.block(f"for (_idx = 0; _idx < {bound}; _idx++)"):
            if Pattern.REDUNDANT_TEMPS in patterns:
                e.line(f"var _current = {coll}[_idx];")
                current = "_current"
            else:
                current = f"{coll}[_idx]"
            self._emit_match(e, current, tgt, patterns)
        self._assign_bool(e, res, "_match_flag", patterns)

    def _match_cmp(self, current, tgt, patterns) -> str:
        # SPAGH_011: Yoda flips to `constant === variable`.
        lhs, rhs = (tgt, current) if Pattern.YODA_CONDITIONS in patterns else (current, tgt)
        return f"{lhs} === {rhs}"

    def _emit_match(self, e, current, tgt, patterns) -> None:
        cmp = self._match_cmp(current, tgt, patterns)
        if Pattern.OPAQUE_PREDICATE in patterns:
            e.comment("SPAGH_009: opaque predicate (always true: n*(n+1) is even)")
            with e.block("if ((_idx * (_idx + 1)) % 2 === 0)"):
                self._match_body(e, cmp, patterns)
        else:
            self._match_body(e, cmp, patterns)

    def _match_body(self, e, cmp, patterns) -> None:
        with e.block(f"if ({cmp})"):
            e.line("_match_flag = true;")
        if Pattern.DEAD_CODE in patterns:
            with e.block("else"):
                e.line("_match_flag = _match_flag;")              # SPAGH_004 no-op

    def _assign_bool(self, e, res, expr, patterns) -> None:
        if Pattern.BOOLEAN_VERBOSITY in patterns:
            with e.block(f"if ({expr} === true)"):                # SPAGH_003
                e.line(f"{res} = true;")
            with e.block("else"):
                e.line(f"{res} = false;")
        else:
            e.line(f"{res} = {expr};")

    # ---- KEY_VALUE_LOOKUP ----
    def emit_lookup(self, e, op, patterns, pol) -> None:
        m, key, res = op.map_name, op.key_var, op.result_var
        default_lit = self.lit(op.default_value)

        cascade = Pattern.DEIDIOMATIZE in patterns or Pattern.CASCADING_COND in patterns
        if not cascade:
            e.line(f"{res} = ({m}[{key}] !== undefined ? {m}[{key}] : {default_lit});")
            return

        e.comment("SPAGH_005: switch enumerating every known key")
        e.line("var _resolved = false;")
        if Pattern.REDUNDANT_TEMPS in patterns:
            e.line(f"var _key = {key};")
            k = "_key"
        else:
            k = key

        with e.block(f"switch ({k})"):
            for pk, pv in op.pairs.items():
                e.line(f"case {self.lit(pk)}:")
                with e.indented():
                    e.line(f"{res} = {self.lit(pv)};")
                    e.line("_resolved = true;")
                    e.line("break;")
            e.line("default:")
            with e.indented():
                e.line("_resolved = false;")
                e.line("break;")

        with e.block("if (_resolved === false)"):
            e.line(f"{res} = {default_lit};")
