"""Generator base: template method + safety hooks (blueprint §13).

The template method ``generate`` defines the algorithm skeleton (identical for all
languages); the language-specific parts are abstract hooks (the *how* side of the
safety split, and the bulk of the per-language anti-pattern emission).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Tuple

from ..emitter import CodeEmitter
from ..ir_models import (
    IRProgram,
    KeyValueLookup,
    MembershipCheck,
    Operation,
    OpPlan,
    TransformPlan,
    scalar_tag,
)
from ..nodes.safety import SafetyPolicy, policy_for


class BaseGenerator(ABC):
    language: str          # "python" / "javascript" / ...
    extension: str         # ".py" / ".js" / ...

    # ---- Template method: the skeleton, do not override in subclasses ----
    def generate(self, program: IRProgram, plan: TransformPlan) -> str:
        # Stash inputs so body hooks can resolve element/value types by name
        # (the operation carries names, not values).
        self._inputs = dict(program.inputs)
        e = self.new_emitter()
        self.emit_file_prologue(e, program)
        self.emit_inputs(e, program.inputs)
        for op_plan in plan.per_op:
            e.line()
            self.emit_operation(e, op_plan)
        self.emit_file_epilogue(e, program)
        return e.render()

    def emit_operation(self, e: CodeEmitter, op_plan: OpPlan) -> None:
        op = op_plan.operation
        pol = policy_for(op)
        e.comment(self.describe(op))
        self.declare_result_default(e, op, pol)          # give result a safe default first
        with self.safety_scope(e, op, pol):              # try + guard (language-specific hook)
            if isinstance(op, MembershipCheck):
                self.emit_membership(e, op, op_plan.patterns, pol)
            elif isinstance(op, KeyValueLookup):
                self.emit_lookup(e, op, op_plan.patterns, pol)
            else:
                raise TypeError(op)

    # ---- Abstract hooks: implemented per language ----
    @abstractmethod
    def new_emitter(self) -> CodeEmitter: ...
    @abstractmethod
    def emit_file_prologue(self, e: CodeEmitter, program: IRProgram) -> None: ...
    @abstractmethod
    def emit_file_epilogue(self, e: CodeEmitter, program: IRProgram) -> None: ...
    @abstractmethod
    def emit_inputs(self, e: CodeEmitter, inputs: dict) -> None: ...
    @abstractmethod
    def declare_result_default(self, e: CodeEmitter, op: Operation, pol: SafetyPolicy) -> None: ...
    @abstractmethod
    def safety_scope(self, e: CodeEmitter, op: Operation, pol: SafetyPolicy):
        """Must be a @contextmanager: opens try + guards, yields for the body,
        then emits the else/except fallback paths."""
    @abstractmethod
    def emit_membership(self, e, op, patterns, pol) -> None: ...
    @abstractmethod
    def emit_lookup(self, e, op, patterns, pol) -> None: ...

    # ---- Shared helpers (language-agnostic) ----
    def describe(self, op: Operation) -> str:
        if isinstance(op, MembershipCheck):
            return f"MEMBERSHIP_CHECK: {op.result_var} = {op.target_var} in {op.collection_name}"
        return (f"KEY_VALUE_LOOKUP: {op.result_var} = "
                f"{op.map_name}[{op.key_var}] or {op.default_value!r}")

    def result_specs(self, program: IRProgram) -> List[Tuple[str, str]]:
        """``[(result_var, type_tag)]`` in operation order, for typed declarations
        and JSON output in the statically-typed targets."""
        out: List[Tuple[str, str]] = []
        for op in program.operations:
            if isinstance(op, MembershipCheck):
                out.append((op.result_var, "bool"))
            else:
                out.append((op.result_var, scalar_tag(op.default_value)))
        return out

    @staticmethod
    def fallback_value(op: Operation, pol: SafetyPolicy) -> object:
        """The Python value a result falls back to on a failed guard / caught error."""
        if pol.fallback_expr_kind == "false":
            return False
        if pol.fallback_expr_kind == "default":
            return op.default_value
        raise ValueError(f"unknown fallback kind: {pol.fallback_expr_kind}")

    @staticmethod
    def input_kind(value: object) -> str:
        if isinstance(value, list):
            return "array"
        if isinstance(value, dict):
            return "map"
        return "scalar"

    @staticmethod
    def array_elem_tag(value: list) -> str:
        return scalar_tag(value[0]) if value else "int"

    @staticmethod
    def map_value_tag(value: dict) -> str:
        vals = list(value.values())
        return scalar_tag(vals[0]) if vals else "str"

    def collection_tag(self, op: MembershipCheck) -> str:
        """Element type tag of a MEMBERSHIP_CHECK collection (from stashed inputs)."""
        return self.array_elem_tag(self._inputs[op.collection_name])
