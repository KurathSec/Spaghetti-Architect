"""Language-agnostic safety policy (blueprint §11).

Redefined role (improvement #1): this module only describes *what* protections are
needed; it touches no source strings. Each generator's safety hook consumes a
:class:`SafetyPolicy` to emit the language-specific guards (the *how*).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..ir_models import (
    Aggregate,
    ConditionalSelect,
    KeyValueLookup,
    MembershipCheck,
    Operation,
)


@dataclass(frozen=True)
class SafetyPolicy:
    needs_null_guard: bool      # collection/map non-null check
    needs_bounds_guard: bool    # index bounds check
    wrap_in_try: bool           # overall try/except|catch
    fallback_expr_kind: str     # how result_var falls back on error / failed guard


def policy_for(op: Operation) -> SafetyPolicy:
    if isinstance(op, MembershipCheck):
        return SafetyPolicy(
            needs_null_guard=True,
            needs_bounds_guard=True,
            wrap_in_try=True,
            fallback_expr_kind="false",
        )
    if isinstance(op, KeyValueLookup):
        return SafetyPolicy(
            needs_null_guard=True,
            needs_bounds_guard=False,
            wrap_in_try=True,
            fallback_expr_kind="default",
        )
    if isinstance(op, Aggregate):
        return SafetyPolicy(
            needs_null_guard=True,
            needs_bounds_guard=True,
            wrap_in_try=True,
            fallback_expr_kind="zero",
        )
    if isinstance(op, ConditionalSelect):
        # Reads scalars only: nothing nullable to guard, but still wrapped in try
        # and pre-set to the else branch so it is crash-free by construction.
        return SafetyPolicy(
            needs_null_guard=False,
            needs_bounds_guard=False,
            wrap_in_try=True,
            fallback_expr_kind="else",
        )
    raise TypeError(op)
