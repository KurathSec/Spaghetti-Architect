"""Anti-optimization planner (blueprint §9) — formerly ``optimizer``.

Reads ``config/anti_patterns_db.json`` and decides which ``SPAGH_*`` transforms to
enable per operation, producing a :class:`TransformPlan`. Genuinely data-driven
(improvement #6): which patterns are enabled and which scenarios they apply to all
come from the DB — nothing about pattern selection is hard-coded here.
"""

from __future__ import annotations

import json

from ..ir_models import IRProgram, OpPlan, Pattern, TransformPlan


class Planner:
    def __init__(self, db_path: str, profile: str = "max") -> None:
        with open(db_path, encoding="utf-8") as f:
            self._db = json.load(f)
        if profile not in self._db["profiles"]:
            raise ValueError(
                f"unknown profile {profile!r}; "
                f"available: {sorted(self._db['profiles'])}"
            )
        self._profile = profile          # strength level: see DB "profiles"

    @property
    def profile(self) -> str:
        return self._profile

    def available_profiles(self) -> list:
        return sorted(self._db["profiles"])

    def plan(self, program: IRProgram) -> TransformPlan:
        enabled = set(self._db["profiles"][self._profile])     # patterns on at this level
        per_op = []
        for op in program.operations:
            applicable = {
                Pattern(pid)
                for pid, meta in self._db["patterns"].items()
                if pid in enabled and op.op in meta["applies_to"]
            }
            per_op.append(OpPlan(op, frozenset(applicable)))
        return TransformPlan(per_op)
