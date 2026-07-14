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
    Aggregate,
    ConditionalSelect,
    IRProgram,
    KeyValueLookup,
    MembershipCheck,
    Operation,
    OpPlan,
    Pattern,
    TransformPlan,
    scalar_tag,
)
from ..nodes.safety import SafetyPolicy, policy_for

# SPAGH_011 (Yoda) flips comparison operand order; the operators are identical in
# all five target languages, so the flip table is language-agnostic.
_FLIP_COMPARATOR = {"==": "==", "!=": "!=", "<": ">", "<=": ">=", ">": "<", ">=": "<="}


class BaseGenerator(ABC):
    language: str          # "python" / "javascript" / ...
    extension: str         # ".py" / ".js" / ...

    # ---- Template method: the skeleton, do not override in subclasses ----
    def generate(self, program: IRProgram, plan: TransformPlan,
                 annotate: bool = True) -> str:
        """``annotate=False`` emits the same code with every comment removed.

        The generators annotate their own output: a module header, a per-operation
        comment stating the operation's clean form, and inline ``SPAGH_*`` markers.
        That makes the corpus self-documenting, but it also hands the answer to any
        model prompted with it, so the unannotated rendering is the control condition.
        """
        # Stash inputs so body hooks can resolve element/value types by name
        # (the operation carries names, not values).
        self._inputs = dict(program.inputs)
        e = self.new_emitter(annotate=annotate)
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
            elif isinstance(op, Aggregate):
                self.emit_aggregate(e, op, op_plan.patterns, pol)
            elif isinstance(op, ConditionalSelect):
                self.emit_conditional(e, op, op_plan.patterns, pol)
            else:
                raise TypeError(op)

    # ---- Abstract hooks: implemented per language ----
    @abstractmethod
    def new_emitter(self, annotate: bool = True) -> CodeEmitter: ...
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
    @abstractmethod
    def emit_aggregate(self, e, op, patterns, pol) -> None: ...
    @abstractmethod
    def emit_conditional(self, e, op, patterns, pol) -> None: ...

    # ---- Shared helpers (language-agnostic) ----
    def describe(self, op: Operation) -> str:
        if isinstance(op, MembershipCheck):
            return f"MEMBERSHIP_CHECK: {op.result_var} = {op.target_var} in {op.collection_name}"
        if isinstance(op, KeyValueLookup):
            return (f"KEY_VALUE_LOOKUP: {op.result_var} = "
                    f"{op.map_name}[{op.key_var}] or {op.default_value!r}")
        if isinstance(op, Aggregate):
            return f"AGGREGATE: {op.result_var} = {op.mode}({op.collection_name})"
        return (f"CONDITIONAL_SELECT: {op.result_var} = {op.then_value!r} if "
                f"{op.subject_var} {op.comparator} {op.compare_value} else {op.else_value!r}")

    @staticmethod
    def guard_target(op: Operation):
        """The nullable container an operation reads, or ``None`` if it reads only
        scalars (so :meth:`safety_scope` knows whether a null guard applies)."""
        if isinstance(op, MembershipCheck):
            return op.collection_name
        if isinstance(op, KeyValueLookup):
            return op.map_name
        if isinstance(op, Aggregate):
            return op.collection_name
        return None  # ConditionalSelect: reads scalars only

    def result_specs(self, program: IRProgram) -> List[Tuple[str, str]]:
        """``[(result_var, type_tag)]`` in operation order, for typed declarations
        and JSON output in the statically-typed targets."""
        out: List[Tuple[str, str]] = []
        for op in program.operations:
            if isinstance(op, MembershipCheck):
                out.append((op.result_var, "bool"))
            elif isinstance(op, KeyValueLookup):
                out.append((op.result_var, scalar_tag(op.default_value)))
            elif isinstance(op, Aggregate):
                out.append((op.result_var, self.array_elem_tag(program.inputs[op.collection_name])))
            else:  # ConditionalSelect — typed by its (same-typed) branch values
                out.append((op.result_var, scalar_tag(op.then_value)))
        return out

    @staticmethod
    def fallback_value(op: Operation, pol: SafetyPolicy) -> object:
        """The Python value a result falls back to on a failed guard / caught error."""
        if pol.fallback_expr_kind == "false":
            return False
        if pol.fallback_expr_kind == "default":
            return op.default_value
        if pol.fallback_expr_kind == "zero":
            return 0
        if pol.fallback_expr_kind == "else":
            return op.else_value
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

    # ---- new-operation comparison builders (shared across all five languages) ----
    def reduce_cmp(self, mode: str, current: str, patterns) -> str:
        """The keep-condition for a manual ``min``/``max`` reduction over ``_acc``.

        ``min`` keeps the smaller element, ``max`` the larger; SPAGH_011 (Yoda)
        flips the operand order to put the accumulator on the left."""
        keep = "<" if mode == "min" else ">"
        if Pattern.YODA_CONDITIONS in patterns:
            return f"_acc {_FLIP_COMPARATOR[keep]} {current}"
        return f"{current} {keep} _acc"

    def select_cond(self, op: ConditionalSelect, patterns) -> str:
        """The boolean test of a CONDITIONAL_SELECT, Yoda-flipped under SPAGH_011.

        Uses the per-language ``lit`` so the constant renders correctly; the six
        comparators are spelled identically in every target."""
        val = self.lit(op.compare_value)
        if Pattern.YODA_CONDITIONS in patterns:
            return f"{val} {_FLIP_COMPARATOR[op.comparator]} {op.subject_var}"
        return f"{op.subject_var} {op.comparator} {val}"
