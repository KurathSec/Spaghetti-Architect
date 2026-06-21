"""IR validation & parsing (blueprint §8).

Validates the raw ``dict`` (from ``json.load``) and constructs an
:class:`IRProgram`. **All** field constraints from §5.2 are enforced here so that
a bad IR can never reach a generator (which would risk crashing generated code
and breaking the core promise).
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Set

from ..ir_models import (
    Aggregate,
    ConditionalSelect,
    IRProgram,
    IRValidationError,
    KeyValueLookup,
    MembershipCheck,
    Operation,
    scalar_tag,
)

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SUPPORTED_VERSIONS = {"1.0"}

_SCALAR_TYPES = (str, int, float, bool, type(None))
_AGGREGATE_MODES = {"sum", "min", "max"}
_COMPARATORS = {"==", "!=", "<", "<=", ">", ">="}


def parse(raw: dict) -> IRProgram:
    _require(isinstance(raw, dict), "IR root must be an object")

    version = raw.get("version", "1.0")
    _require(version in SUPPORTED_VERSIONS, f"unsupported version: {version}")

    module = raw.get("module_name", "generated")
    _require(isinstance(module, str) and bool(_IDENT.match(module)),
             f"illegal module_name: {module!r}")

    inputs = raw.get("inputs", {})
    _require(isinstance(inputs, dict), "inputs must be an object")
    _validate_inputs(inputs)

    ops_raw = raw.get("operations")
    _require(isinstance(ops_raw, list) and len(ops_raw) > 0,
             "operations must be a non-empty array")

    declared: Set[str] = set(inputs)            # grows as result_vars are produced
    operations: List[Operation] = []
    for i, op in enumerate(ops_raw):
        operations.append(_parse_op(op, i, inputs, declared))

    return IRProgram(version, module, inputs, operations)


# --------------------------------------------------------------------------- #
# inputs
# --------------------------------------------------------------------------- #
def _validate_inputs(inputs: Dict[str, object]) -> None:
    for name, value in inputs.items():
        _require(bool(_IDENT.match(name)), f"input name is not a valid identifier: {name!r}")
        if isinstance(value, list):
            _homogeneous_list_tag(value, f"input {name!r}")
        elif isinstance(value, dict):
            _require_str_keys(value, f"input {name!r}")
            _homogeneous_value_tag(value, f"input {name!r}")
        else:
            _require(_is_scalar(value),
                     f"input {name!r} has unsupported type {type(value).__name__}")


def _is_scalar(value: object) -> bool:
    # bool is a subclass of int, so it is already covered by _SCALAR_TYPES.
    return isinstance(value, _SCALAR_TYPES)


def _homogeneous_list_tag(values: list, where: str) -> Optional[str]:
    """Validate a list is scalar + homogeneous; return its element tag (or None if empty)."""
    tag: Optional[str] = None
    for v in values:
        _require(_is_scalar(v), f"{where}: array elements must be scalars")
        t = scalar_tag(v)
        if tag is None:
            tag = t
        else:
            _require(t == tag, f"{where}: array must be homogeneous (saw {tag} and {t})")
    return tag


def _homogeneous_value_tag(mapping: dict, where: str) -> Optional[str]:
    """Validate a map's values are scalar + homogeneous; return the value tag."""
    tag: Optional[str] = None
    for v in mapping.values():
        _require(_is_scalar(v), f"{where}: map values must be scalars")
        t = scalar_tag(v)
        if tag is None:
            tag = t
        else:
            _require(t == tag, f"{where}: map values must be homogeneous (saw {tag} and {t})")
    return tag


def _require_str_keys(mapping: dict, where: str) -> None:
    for k in mapping:
        _require(isinstance(k, str), f"{where}: map keys must be strings")


# --------------------------------------------------------------------------- #
# operations
# --------------------------------------------------------------------------- #
def _parse_op(op: object, i: int, inputs: Dict[str, object], declared: Set[str]) -> Operation:
    _require(isinstance(op, dict), f"operations[{i}] must be an object")
    kind = op.get("operation")

    if kind == "MEMBERSHIP_CHECK":
        col = _field(op, "collection_name", i)
        tgt = _field(op, "target_var", i)
        res = _field(op, "result_var", i)
        _require(isinstance(inputs.get(col), list),
                 f"[{i}] collection_name {col!r} must point to an array input")
        _require(tgt in inputs, f"[{i}] target_var {tgt!r} not declared in inputs")
        _require(_is_scalar(inputs[tgt]), f"[{i}] target_var {tgt!r} must be a scalar")
        elem_tag = _homogeneous_list_tag(inputs[col], f"[{i}] collection {col!r}")
        if elem_tag is not None:
            _require(scalar_tag(inputs[tgt]) == elem_tag,
                     f"[{i}] target_var type ({scalar_tag(inputs[tgt])}) must match "
                     f"collection element type ({elem_tag})")
        _check_new_ident(res, declared, i)
        declared.add(res)
        return MembershipCheck(col, tgt, res)

    if kind == "KEY_VALUE_LOOKUP":
        m = _field(op, "map_name", i)
        key = _field(op, "key_var", i)
        res = _field(op, "result_var", i)
        _require("pairs" in op, f"[{i}] missing field: pairs")
        _require("default_value" in op, f"[{i}] missing field: default_value")
        pairs, default = op["pairs"], op["default_value"]
        _require(isinstance(inputs.get(m), dict),
                 f"[{i}] map_name {m!r} must point to an object input")
        _require(key in inputs, f"[{i}] key_var {key!r} not declared in inputs")
        _require(isinstance(inputs[key], str), f"[{i}] key_var {key!r} must be a string scalar")
        _require(isinstance(pairs, dict) and len(pairs) > 0,
                 f"[{i}] pairs must be a non-empty object")
        _require_str_keys(pairs, f"[{i}] pairs")
        _require(_is_scalar(default), f"[{i}] default_value must be a scalar")
        value_tag = _homogeneous_value_tag(pairs, f"[{i}] pairs")
        if value_tag is not None:
            _require(scalar_tag(default) == value_tag,
                     f"[{i}] default_value type ({scalar_tag(default)}) must match "
                     f"pairs value type ({value_tag})")
        _check_new_ident(res, declared, i)
        declared.add(res)
        return KeyValueLookup(m, key, res, dict(pairs), default)

    if kind == "AGGREGATE":
        mode = op.get("mode")
        _require(mode in _AGGREGATE_MODES,
                 f"[{i}] AGGREGATE mode must be one of {sorted(_AGGREGATE_MODES)}, got {mode!r}")
        col = _field(op, "collection_name", i)
        res = _field(op, "result_var", i)
        _require(isinstance(inputs.get(col), list),
                 f"[{i}] collection_name {col!r} must point to an array input")
        _require(len(inputs[col]) > 0,
                 f"[{i}] AGGREGATE collection {col!r} must be non-empty")
        elem_tag = _homogeneous_list_tag(inputs[col], f"[{i}] collection {col!r}")
        _require(elem_tag == "int",
                 f"[{i}] AGGREGATE requires an int collection (got element type {elem_tag})")
        _check_new_ident(res, declared, i)
        declared.add(res)
        return Aggregate(col, mode, res)

    if kind == "CONDITIONAL_SELECT":
        subj = _field(op, "subject_var", i)
        comparator = op.get("comparator")
        _require(comparator in _COMPARATORS,
                 f"[{i}] comparator must be one of {sorted(_COMPARATORS)}, got {comparator!r}")
        res = _field(op, "result_var", i)
        for fld in ("compare_value", "then_value", "else_value"):
            _require(fld in op, f"[{i}] missing field: {fld}")
        cmp_val, then_val, else_val = op["compare_value"], op["then_value"], op["else_value"]
        _require(subj in inputs, f"[{i}] subject_var {subj!r} not declared in inputs")
        _require(scalar_tag(inputs[subj]) == "int",
                 f"[{i}] subject_var {subj!r} must be an int scalar")
        _require(_is_scalar(cmp_val) and scalar_tag(cmp_val) == "int",
                 f"[{i}] compare_value must be an int scalar")
        _require(_is_scalar(then_val) and _is_scalar(else_val),
                 f"[{i}] then_value and else_value must be scalars")
        _require(scalar_tag(then_val) == scalar_tag(else_val),
                 f"[{i}] then_value ({scalar_tag(then_val)}) and else_value "
                 f"({scalar_tag(else_val)}) must have the same type")
        _check_new_ident(res, declared, i)
        declared.add(res)
        return ConditionalSelect(subj, comparator, cmp_val, then_val, else_val, res)

    raise IRValidationError(f"operations[{i}] unknown operation: {kind!r}")


def _field(op: dict, name: str, i: int) -> str:
    _require(name in op, f"[{i}] missing field: {name}")
    val = op[name]
    _require(isinstance(val, str), f"[{i}] field {name} must be a string")
    return val


def _check_new_ident(res: str, declared: Set[str], i: int) -> None:
    _require(isinstance(res, str) and bool(_IDENT.match(res)),
             f"[{i}] result_var is not a valid identifier: {res!r}")
    _require(res not in declared, f"[{i}] result_var {res!r} collides with an existing variable")


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise IRValidationError(msg)
