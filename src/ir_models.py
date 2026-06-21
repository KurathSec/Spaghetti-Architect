"""Core data models for the Spaghetti Architect engine (blueprint §7).

Pure :mod:`dataclasses`, immutable (``frozen=True``), zero third-party deps.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Union

# A JSON scalar. ``inputs`` values are scalars, scalar arrays, or string->scalar maps.
Scalar = Union[str, int, float, bool, None]


class IRValidationError(ValueError):
    """Raised by the parser when the IR is invalid."""


# --------------------------------------------------------------------------- #
# Operations
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MembershipCheck:
    """``result_var = target_var in collection_name`` (result is a bool)."""

    collection_name: str
    target_var: str
    result_var: str
    op: str = "MEMBERSHIP_CHECK"


@dataclass(frozen=True)
class KeyValueLookup:
    """``result_var = map_name.get(key_var, default_value)`` via a known-key cascade."""

    map_name: str
    key_var: str
    result_var: str
    pairs: Dict[str, Scalar]
    default_value: Scalar
    op: str = "KEY_VALUE_LOOKUP"


@dataclass(frozen=True)
class Aggregate:
    """``result_var = sum|min|max(collection_name)`` over an int collection (an int).

    A loop-reduction over a numeric list. Restricted to ``int`` elements so the
    result formats byte-identically across all five languages (floats print with
    language-specific precision, which would break the oracle equivalence check).
    """

    collection_name: str
    mode: str                    # "sum" | "min" | "max"
    result_var: str
    op: str = "AGGREGATE"


@dataclass(frozen=True)
class ConditionalSelect:
    """``result_var = then_value if (subject_var <comparator> compare_value) else else_value``.

    A branch-select. ``subject_var``/``compare_value`` are ``int`` (so the six
    comparators have identical cross-language semantics); ``then_value`` and
    ``else_value`` are same-typed scalars and are the only values emitted.
    """

    subject_var: str
    comparator: str              # == != < <= > >=
    compare_value: Scalar
    then_value: Scalar
    else_value: Scalar
    result_var: str
    op: str = "CONDITIONAL_SELECT"


Operation = Union[MembershipCheck, KeyValueLookup, Aggregate, ConditionalSelect]


@dataclass(frozen=True)
class IRProgram:
    version: str
    module_name: str
    inputs: Dict[str, object]          # scalar / scalar array / string->scalar map
    operations: List[Operation]


# --------------------------------------------------------------------------- #
# Planner output (blueprint §7 / §9)
# --------------------------------------------------------------------------- #
class Pattern(str, Enum):
    """The eleven composable anti-patterns (``SPAGH_001..011``)."""

    DEIDIOMATIZE = "SPAGH_001"
    REDUNDANT_TEMPS = "SPAGH_002"
    BOOLEAN_VERBOSITY = "SPAGH_003"
    DEAD_CODE = "SPAGH_004"
    CASCADING_COND = "SPAGH_005"
    MANUAL_INDEXING = "SPAGH_006"
    OVER_GUARDING = "SPAGH_007"
    VERBOSE_DECOMP = "SPAGH_008"
    OPAQUE_PREDICATE = "SPAGH_009"
    REDUNDANT_RECOMP = "SPAGH_010"
    YODA_CONDITIONS = "SPAGH_011"


@dataclass(frozen=True)
class OpPlan:
    operation: Operation
    patterns: "frozenset[Pattern]"     # anti-patterns enabled for this operation


@dataclass(frozen=True)
class TransformPlan:
    per_op: List[OpPlan]


# --------------------------------------------------------------------------- #
# Language-agnostic semantic helpers
# --------------------------------------------------------------------------- #
def scalar_tag(value: object) -> str:
    """Classify a JSON scalar into a language-agnostic type tag.

    ``bool`` must be checked before ``int`` because ``bool`` subclasses ``int``.
    """
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if value is None:
        return "null"
    raise IRValidationError(f"unsupported scalar type: {type(value).__name__}")
