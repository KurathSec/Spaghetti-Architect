"""C++ generator (blueprint §14.5 / §19.3).

- emitter: ``brace_style=True``.
- anti-patterns: pointer arithmetic ``*(ptr + i)`` for membership (SPAGH_006 truly
  lands here); nested ``if`` chain for lookup.
- safety: memory-bounds validation (``ptr != nullptr && idx >= 0 && idx < len``) so
  the pointer demo never SegFaults; ``try { } catch (...) { }``.
- output: ``std::cout`` of one JSON line (``std::boolalpha`` for bools, ``_q`` for strings).
"""

from __future__ import annotations

import json
from contextlib import contextmanager

from ..emitter import CodeEmitter
from ..ir_models import MembershipCheck, Pattern, scalar_tag
from .base import BaseGenerator

_CPP_TYPE = {"bool": "bool", "int": "int", "float": "double", "str": "std::string"}


class CppGenerator(BaseGenerator):
    language = "cpp"
    extension = ".cpp"

    def new_emitter(self) -> CodeEmitter:
        return CodeEmitter(brace_style=True)

    def lit(self, value: object) -> str:
        tag = scalar_tag(value)
        if tag == "bool":
            return "true" if value else "false"
        if tag == "str":
            return json.dumps(value)          # JSON escaping is valid C++ string escaping
        if tag == "null":
            return "nullptr"
        return repr(value)

    # ---- file structure ----
    def emit_file_prologue(self, e, program) -> None:
        e.comment(f"Spaghetti Architect — generated module: {program.module_name}")
        e.comment("Deliberately redundant, but syntactically correct and crash-free.")
        e.line("#include <bits/stdc++.h>")
        e.line()
        self._emit_q_helper(e)
        e.line()
        e.open_brace("int main()")
        e.line("std::cout << std::boolalpha;")

    def _emit_q_helper(self, e) -> None:
        e.comment("minimal JSON string escaper")
        e.open_brace("static std::string _q(const std::string& s)")
        e.line("std::string r;")
        e.line("r += '\"';")
        with e.block("for (size_t i = 0; i < s.size(); i++)"):
            e.line("char c = s[i];")
            with e.block("if (c == '\"' || c == '\\\\')"):
                e.line("r += '\\\\';")
            e.line("r += c;")
        e.line("r += '\"';")
        e.line("return r;")
        e.close_brace()

    def emit_inputs(self, e, inputs) -> None:
        e.comment("--- run fixtures (inputs) ---")
        for name, value in inputs.items():
            self._declare_input(e, name, value)

    def _declare_input(self, e, name, value) -> None:
        if isinstance(value, list):
            t = _CPP_TYPE[self.array_elem_tag(value)]
            items = ", ".join(self.lit(v) for v in value)
            e.line(f"std::vector<{t}> {name} = {{{items}}};")
        elif isinstance(value, dict):
            t = _CPP_TYPE[self.map_value_tag(value)]
            items = ", ".join(f"{{{json.dumps(k)}, {self.lit(v)}}}" for k, v in value.items())
            e.line(f"std::map<std::string, {t}> {name} = {{{items}}};")
        else:
            e.line(f"{_CPP_TYPE[scalar_tag(value)]} {name} = {self.lit(value)};")

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
        e.line("std::cout << " + " << ".join(pieces) + " << std::endl;")
        e.line("return 0;")
        e.close_brace()                                   # close main

    def declare_result_default(self, e, op, pol) -> None:
        if isinstance(op, MembershipCheck):
            rtype = "bool"
        else:
            rtype = _CPP_TYPE[scalar_tag(op.default_value)]
        e.line(f"{rtype} {op.result_var} = {self.lit(self.fallback_value(op, pol))};")

    # ---- safety: try/catch only; the bounds guard lives in the body (pointer setup) ----
    @contextmanager
    def safety_scope(self, e, op, pol):
        fb = f"{op.result_var} = {self.lit(self.fallback_value(op, pol))};"
        with e.block("try"):
            yield e
        with e.block("catch (...)"):
            e.line(fb)

    # ---- MEMBERSHIP_CHECK ----
    def emit_membership(self, e, op, patterns, pol) -> None:
        coll, tgt, res = op.collection_name, op.target_var, op.result_var
        etype = _CPP_TYPE[self.collection_tag(op)]

        if Pattern.DEIDIOMATIZE not in patterns:
            self._assign_bool(
                e, res,
                f"(std::find({coll}.begin(), {coll}.end(), {tgt}) != {coll}.end())",
                patterns,
            )
            return

        e.comment("SPAGH_006: pointer arithmetic with full bounds checking")
        e.line(f"{etype}* list_ptr = {coll}.empty() ? nullptr : &{coll}[0];")
        e.line(f"long {coll}_len = (long){coll}.size();")
        with e.block(f"if (list_ptr != nullptr && {coll}_len >= 0)"):
            e.line("long _idx = 0;")
            e.line("bool _match_flag = false;")
            if Pattern.REDUNDANT_RECOMP in patterns:
                e.comment("SPAGH_010: recompute .size() every iteration (de-hoisted)")
                bound = f"(long){coll}.size()"
            else:
                bound = f"{coll}_len"
            with e.block(f"while (_idx < {bound})"):
                if Pattern.REDUNDANT_TEMPS in patterns:
                    e.line(f"{etype} _current = *(list_ptr + _idx);")
                    current = "_current"
                else:
                    current = "*(list_ptr + _idx)"
                self._emit_match(e, current, tgt, patterns)
                e.line("_idx = _idx + 1;")
            self._assign_bool(e, res, "_match_flag", patterns)
        with e.block("else"):
            e.line(f"{res} = false;")

    def _match_cmp(self, current, tgt, patterns) -> str:
        # SPAGH_011: Yoda flips to `constant == variable`.
        lhs, rhs = (tgt, current) if Pattern.YODA_CONDITIONS in patterns else (current, tgt)
        return f"{lhs} == {rhs}"

    def _emit_match(self, e, current, tgt, patterns) -> None:
        cmp = self._match_cmp(current, tgt, patterns)
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
            e.line(f"auto _it = {m}.find({key});")
            with e.block(f"if (_it != {m}.end())"):
                e.line(f"{res} = _it->second;")
            return

        e.comment("SPAGH_005: nested if chain enumerating every known key")
        e.line("bool _resolved = false;")
        if Pattern.REDUNDANT_TEMPS in patterns:
            e.line(f"std::string _key = {key};")
            k = "_key"
        else:
            k = key

        first = True
        for pk, pv in op.pairs.items():
            header = f"if ({k} == {json.dumps(pk)})" if first \
                else f"else if ({k} == {json.dumps(pk)})"
            with e.block(header):
                e.line(f"{res} = {self.lit(pv)};")
                e.line("_resolved = true;")
            first = False
        with e.block("else"):
            e.line("_resolved = false;")

        with e.block("if (_resolved == false)"):
            e.line(f"{res} = {default_lit};")
        e.line(f"(void){m};")          # map is a fixture; touch it to avoid -Wunused
