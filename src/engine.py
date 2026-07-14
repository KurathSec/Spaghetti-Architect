"""Orchestration (blueprint §16): Parser -> Planner -> Generators(+safety) -> Validator."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Dict

from .generators import REGISTRY
from .nodes.parser import parse
from .nodes.planner import Planner
from .nodes.validator import ValidationResult, validate


class Engine:
    def __init__(self, db_path: str, profile: str = "max",
                 annotate: bool = True) -> None:
        self._planner = Planner(db_path, profile)
        self._annotate = annotate

    @property
    def profile(self) -> str:
        return self._planner.profile

    @property
    def annotate(self) -> bool:
        """False renders the same programs with every comment stripped.

        The default output is self-annotated: a module header, a per-operation
        comment naming the operation's clean form, and inline ``SPAGH_*`` markers.
        Anyone prompting a model with these sources is handing it the answer, so the
        unannotated rendering is the control condition (see the datasheet).
        """
        return self._annotate

    def generate(self, raw_ir: dict) -> dict:
        """Parse + plan + emit all five languages. No validation.

        Generation mutates per-call state on the shared generator instances, so
        this must run on one thread at a time — callers that fan out across many
        IRs should generate sequentially (it is instant) and parallelise only the
        validation step, which is the subprocess-bound part.
        """
        program = parse(raw_ir)                       # 1. validate & parse
        plan = self._planner.plan(program)            # 2. anti-pattern planning
        sources: Dict[str, str] = {                   # 3. five-language generation (incl. safety)
            lang: gen.generate(program, plan, annotate=self._annotate)
            for lang, gen in REGISTRY.items()
        }
        return {"program": program, "sources": sources}

    def transpile(self, raw_ir: dict) -> dict:
        out = self.generate(raw_ir)
        program, sources = out["program"], out["sources"]
        # 4. cross-language validation. Each language compiles/runs in its own
        # external toolchain (subprocess-bound, independent), so validate them
        # concurrently — threads suffice since the work happens in subprocesses.
        # Results are keyed by language (not completion order), so the output is
        # deterministic regardless of how the threads interleave.
        with ThreadPoolExecutor(max_workers=len(sources) or 1) as pool:
            futures = {
                lang: pool.submit(validate, lang, src, program)
                for lang, src in sources.items()
            }
            results: Dict[str, ValidationResult] = {
                lang: fut.result() for lang, fut in futures.items()
            }
        return {"program": program, "sources": sources, "validation": results}
