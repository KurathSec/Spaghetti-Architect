"""Java generator (blueprint §14.4).

- emitter: ``brace_style=True``; class name is ``module_name`` capitalized.
- anti-patterns: index/enhanced-for loop over a raw array for membership; nested
  ``if`` chain for lookup.
- safety: ``x != null`` guard + ``try { } catch (Exception e) { }``.
- output: ``System.out.println`` of one JSON line (strings escaped via ``_q``).
"""

from __future__ import annotations

import json
from contextlib import contextmanager

from ..emitter import CodeEmitter
from ..ir_models import Aggregate, KeyValueLookup, MembershipCheck, Pattern, scalar_tag
from .base import BaseGenerator

_JAVA_TYPE = {"bool": "boolean", "int": "int", "float": "double", "str": "String"}
_JAVA_BOX = {"bool": "Boolean", "int": "Integer", "float": "Double", "str": "String"}


class JavaGenerator(BaseGenerator):
    language = "java"
    extension = ".java"

    def new_emitter(self) -> CodeEmitter:
        return CodeEmitter(brace_style=True)

    @staticmethod
    def class_name(module_name: str) -> str:
        return module_name[:1].upper() + module_name[1:]

    def lit(self, value: object) -> str:
        tag = scalar_tag(value)
        if tag == "bool":
            return "true" if value else "false"
        if tag == "str":
            return json.dumps(value)          # JSON escaping is valid Java string escaping
        if tag == "null":
            return "null"
        return repr(value)

    # ---- file structure ----
    def emit_file_prologue(self, e, program) -> None:
        e.comment(f"Spaghetti Architect — generated class: {self.class_name(program.module_name)}")
        e.comment("Deliberately redundant, but syntactically correct and crash-free.")
        if any(isinstance(v, dict) for v in program.inputs.values()):
            e.line("import java.util.HashMap;")
            e.line("import java.util.Map;")
        e.open_brace(f"public class {self.class_name(program.module_name)}")
        e.open_brace("public static void main(String[] args)")

    def emit_inputs(self, e, inputs) -> None:
        e.comment("--- run fixtures (inputs) ---")
        for name, value in inputs.items():
            self._declare_input(e, name, value)

    def _declare_input(self, e, name, value) -> None:
        if isinstance(value, list):
            t = _JAVA_TYPE[self.array_elem_tag(value)]
            items = ", ".join(self.lit(v) for v in value)
            e.line(f"{t}[] {name} = {{{items}}};")
        elif isinstance(value, dict):
            box = _JAVA_BOX[self.map_value_tag(value)]
            e.line(f"Map<String, {box}> {name} = new HashMap<>();")
            for k, v in value.items():
                e.line(f"{name}.put({json.dumps(k)}, {self.lit(v)});")
        else:
            e.line(f"{_JAVA_TYPE[scalar_tag(value)]} {name} = {self.lit(value)};")

    def emit_file_epilogue(self, e, program) -> None:
        specs = self.result_specs(program)
        pieces = ['"{"']
        for i, (name, tag) in enumerate(specs):
            if i:
                pieces.append('", "')
            pieces.append('"\\"' + name + '\\": "')
            pieces.append(name if tag != "str" else f"_q({name})")
        pieces.append('"}"')
        e.line()
        e.comment("emit result_vars as one JSON line for the validator")
        e.line("System.out.println(" + " + ".join(pieces) + ");")
        e.close_brace()                                   # close main
        e.line()
        self._emit_q_helper(e)
        e.close_brace()                                   # close class

    def _emit_q_helper(self, e) -> None:
        e.comment("minimal JSON string escaper")
        e.open_brace("static String _q(String s)")
        e.line("StringBuilder b = new StringBuilder();")
        e.line("b.append('\"');")
        with e.block("for (int i = 0; i < s.length(); i++)"):
            e.line("char c = s.charAt(i);")
            with e.block("if (c == '\"' || c == '\\\\')"):
                e.line("b.append('\\\\');")
            e.line("b.append(c);")
        e.line("b.append('\"');")
        e.line("return b.toString();")
        e.close_brace()

    def declare_result_default(self, e, op, pol) -> None:
        if isinstance(op, MembershipCheck):
            rtype = "boolean"
        elif isinstance(op, KeyValueLookup):
            rtype = _JAVA_TYPE[scalar_tag(op.default_value)]
        elif isinstance(op, Aggregate):
            rtype = _JAVA_TYPE[self.array_elem_tag(self._inputs[op.collection_name])]
        else:  # ConditionalSelect — typed by its (same-typed) branch values
            rtype = _JAVA_TYPE[scalar_tag(op.then_value)]
        e.line(f"{rtype} {op.result_var} = {self.lit(self.fallback_value(op, pol))};")

    # ---- safety: try + null guard + fallback ----
    @contextmanager
    def safety_scope(self, e, op, pol):
        target = self.guard_target(op)
        fb = f"{op.result_var} = {self.lit(self.fallback_value(op, pol))};"
        with e.block("try"):
            if pol.needs_null_guard and target is not None:
                with e.block(f"if ({target} != null)"):
                    yield e
                with e.block("else"):
                    e.line(fb)
            else:
                yield e
        with e.block("catch (Exception e)"):
            e.line(fb)

    # ---- MEMBERSHIP_CHECK ----
    def emit_membership(self, e, op, patterns, pol) -> None:
        coll, tgt, res = op.collection_name, op.target_var, op.result_var
        etag = self.collection_tag(op)
        etype = _JAVA_TYPE[etag]

        if Pattern.DEIDIOMATIZE not in patterns:
            e.line(f"{res} = false;")
            with e.block(f"for ({etype} _v : {coll})"):
                with e.block(f"if ({self._eq('_v', tgt, etag)})"):
                    e.line(f"{res} = true;")
            return

        e.comment("SPAGH_001/006: index loop over the raw array")
        e.line("int _idx = 0;")
        if Pattern.REDUNDANT_RECOMP in patterns:
            e.comment("SPAGH_010: recompute .length every iteration (de-hoisted)")
            bound = f"{coll}.length"
        else:
            e.line(f"int _n = {coll}.length;")
            bound = "_n"
        e.line("boolean _match_flag = false;")
        with e.block(f"for (_idx = 0; _idx < {bound}; _idx++)"):
            if Pattern.REDUNDANT_TEMPS in patterns:
                e.line(f"{etype} _current = {coll}[_idx];")
                current = "_current"
            else:
                current = f"{coll}[_idx]"
            self._emit_match(e, current, tgt, etag, patterns)
        self._assign_bool(e, res, "_match_flag", patterns)

    @staticmethod
    def _eq(lhs: str, rhs: str, tag: str) -> str:
        # Java: reference types compare with .equals, primitives with ==.
        if tag == "str":
            return f"{lhs}.equals({rhs})"
        return f"{lhs} == {rhs}"

    def _match_cmp(self, current, tgt, etag, patterns) -> str:
        # SPAGH_011: Yoda flips to `constant == variable` (constant.equals(variable) for str).
        lhs, rhs = (tgt, current) if Pattern.YODA_CONDITIONS in patterns else (current, tgt)
        return self._eq(lhs, rhs, etag)

    def _emit_match(self, e, current, tgt, etag, patterns) -> None:
        cmp = self._match_cmp(current, tgt, etag, patterns)
        if Pattern.OPAQUE_PREDICATE in patterns:
            e.comment("SPAGH_009: opaque predicate (always true: n*(n+1) is even)")
            with e.block("if ((_idx * (_idx + 1)) % 2 == 0)"):
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
            with e.block(f"if ({expr} == true)"):                 # SPAGH_003
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
            e.line(f"{res} = {m}.getOrDefault({key}, {default_lit});")
            return

        e.comment("SPAGH_005: nested if chain enumerating every known key")
        e.line("boolean _resolved = false;")
        if Pattern.REDUNDANT_TEMPS in patterns:
            e.line(f"String _key = {key};")
            k = "_key"
        else:
            k = key

        first = True
        for pk, pv in op.pairs.items():
            header = f"if ({k}.equals({json.dumps(pk)}))" if first \
                else f"else if ({k}.equals({json.dumps(pk)}))"
            with e.block(header):
                e.line(f"{res} = {self.lit(pv)};")
                e.line("_resolved = true;")
            first = False
        with e.block("else"):
            e.line("_resolved = false;")

        with e.block("if (_resolved == false)"):
            e.line(f"{res} = {default_lit};")

    # ---- AGGREGATE ----
    def emit_aggregate(self, e, op, patterns, pol) -> None:
        coll, res, mode = op.collection_name, op.result_var, op.mode
        etype = _JAVA_TYPE[self.array_elem_tag(self._inputs[coll])]
        acc_init = "0" if mode == "sum" else f"{coll}[0]"

        if Pattern.DEIDIOMATIZE not in patterns:
            e.line(f"{etype} _acc = {acc_init};")
            with e.block(f"for ({etype} _v : {coll})"):
                self._reduce_body(e, mode, "_v", frozenset())
            e.line(f"{res} = _acc;")
            return

        e.comment(f"SPAGH_001/006/008: manual {mode} reduction over the raw array")
        e.line("int _idx = 0;")
        if Pattern.REDUNDANT_RECOMP in patterns:
            e.comment("SPAGH_010: recompute .length every iteration (de-hoisted)")
            bound = f"{coll}.length"
        else:
            e.line(f"int _n = {coll}.length;")
            bound = "_n"
        e.line(f"{etype} _acc = {acc_init};")
        with e.block(f"for (_idx = 0; _idx < {bound}; _idx++)"):
            if Pattern.REDUNDANT_TEMPS in patterns:
                e.line(f"{etype} _current = {coll}[_idx];")
                current = "_current"
            else:
                current = f"{coll}[_idx]"
            self._emit_reduce(e, mode, current, patterns)
        e.line(f"{res} = _acc;")

    def _emit_reduce(self, e, mode, current, patterns) -> None:
        if Pattern.OPAQUE_PREDICATE in patterns:
            e.comment("SPAGH_009: opaque predicate (always true: n*(n+1) is even)")
            with e.block("if ((_idx * (_idx + 1)) % 2 == 0)"):
                self._reduce_body(e, mode, current, patterns)
        else:
            self._reduce_body(e, mode, current, patterns)

    def _reduce_body(self, e, mode, current, patterns) -> None:
        if mode == "sum":
            e.line(f"_acc = _acc + {current};")
            if Pattern.DEAD_CODE in patterns:
                e.line("_acc = _acc;")                        # SPAGH_004 no-op
            return
        with e.block(f"if ({self.reduce_cmp(mode, current, patterns)})"):
            e.line(f"_acc = {current};")
        if Pattern.DEAD_CODE in patterns:
            with e.block("else"):
                e.line("_acc = _acc;")                        # SPAGH_004 no-op

    # ---- CONDITIONAL_SELECT ----
    def emit_conditional(self, e, op, patterns, pol) -> None:
        res = op.result_var
        then_lit, else_lit = self.lit(op.then_value), self.lit(op.else_value)
        cond = self.select_cond(op, patterns)

        branch = Pattern.DEIDIOMATIZE in patterns or Pattern.CASCADING_COND in patterns
        if not branch:
            e.line(f"{res} = ({cond}) ? {then_lit} : {else_lit};")
            return

        e.comment("SPAGH_001/005: expand the ternary into an explicit if/else")
        if Pattern.REDUNDANT_TEMPS in patterns:
            e.line(f"boolean _cond = {cond};")
            cond = "_cond"
        with e.block(f"if ({cond})"):
            e.line(f"{res} = {then_lit};")
        with e.block("else"):
            e.line(f"{res} = {else_lit};")
            if Pattern.DEAD_CODE in patterns:
                e.line(f"{res} = {res};")                     # SPAGH_004 no-op
