"""Phase 3 — frozen, versioned prompt templates (one per task x language facet).

Each prompt asks for **one job** and constrains the *graded artifact* to a single
parseable shape, so no chain-of-thought leaks into what the grader parses:

* **refactor** returns *only* a fenced code block;
* **judge (pointwise)** returns *only* an integer 1..10;
* **judge (pairwise)** returns *only* a single token ``A`` or ``B``;
* **comprehension** returns *only* a JSON object of the result variables.

``PROMPT_VERSION`` is stamped into the manifest and every result record, so a
prompt change is always traceable. Bump it whenever a template below changes.
"""

from __future__ import annotations

from typing import List

PROMPT_VERSION = "bench-prompts-v1"

# Language display names + the fence tag used in the returned code block.
_LANG_NAME = {"python": "Python", "javascript": "JavaScript (ES5)", "go": "Go",
              "java": "Java", "cpp": "C++"}
_FENCE = {"python": "python", "javascript": "javascript", "go": "go",
          "java": "java", "cpp": "cpp"}


# --------------------------------------------------------------------------- #
# Task A — Refactor
# --------------------------------------------------------------------------- #
def refactor(language: str, source: str, result_vars: List[str]) -> tuple:
    """Return ``(system, user)`` asking for a clean, behavior-preserving rewrite.

    The contract that makes the result auto-gradable: keep the same input
    variables and the same ``result_var`` names, and **keep the final
    result-printing line(s)** so the validator can read the outputs. For Python the
    result variables are read from the executed module; for the compiled languages
    the original JSON-printing epilogue must survive so stdout still parses.
    """
    name = _LANG_NAME[language]
    fence = _FENCE[language]
    rv = ", ".join(result_vars)
    keep = (
        "Keep the same input values and the same result variable names "
        f"({rv}). Preserve the final line(s) that print the result"
        + (" to stdout as JSON" if language != "python" else "")
        + " so the program's observable output is unchanged."
    )
    system = (
        "You are an expert software engineer who simplifies over-engineered code "
        "into the cleanest idiomatic form that preserves exact behavior. You output "
        "only code, never commentary."
    )
    user = (
        f"The following {name} code is correct but deliberately over-complicated. "
        "Rewrite it to be as clean, simple, and idiomatic as possible while computing "
        f"exactly the same result. {keep}\n\n"
        f"Return ONLY the rewritten {name} program in a single ```{fence} code block, "
        "with no explanation before or after.\n\n"
        f"```{fence}\n{source}\n```"
    )
    return system, user


# --------------------------------------------------------------------------- #
# Task B — Judge
# --------------------------------------------------------------------------- #
def judge_pointwise(language: str, source: str) -> tuple:
    """Rate maintainability/readability on an integer 1..10 (10 = best)."""
    name = _LANG_NAME[language]
    system = (
        "You are a meticulous code reviewer. You rate code maintainability on an "
        "integer scale from 1 (unmaintainable) to 10 (exemplary). You respond with "
        "only the integer, nothing else."
    )
    user = (
        f"Rate the maintainability and readability of this {name} code on an integer "
        "scale from 1 to 10 (10 = best). Respond with ONLY the integer.\n\n"
        f"```{_FENCE[language]}\n{source}\n```"
    )
    return system, user


def judge_pairwise(language: str, source_a: str, source_b: str) -> tuple:
    """Pick the more maintainable of two versions; answer only ``A`` or ``B``."""
    name = _LANG_NAME[language]
    fence = _FENCE[language]
    system = (
        "You compare two implementations and decide which is more maintainable. "
        "You answer with only a single letter: A or B."
    )
    user = (
        f"Here are two {name} programs that compute the same result. Which is more "
        "maintainable and readable? Answer with ONLY the single letter A or B.\n\n"
        f"Program A:\n```{fence}\n{source_a}\n```\n\n"
        f"Program B:\n```{fence}\n{source_b}\n```"
    )
    return system, user


# --------------------------------------------------------------------------- #
# Task C — Comprehension / output prediction
# --------------------------------------------------------------------------- #
def comprehend(language: str, source: str, result_vars: List[str]) -> tuple:
    """Predict the final values of the result variables, as a JSON object."""
    name = _LANG_NAME[language]
    keys = ", ".join(f'"{v}"' for v in result_vars)
    system = (
        "You are a precise interpreter. You mentally execute code and report the "
        "final values of the requested variables. You respond with only a single "
        "JSON object, nothing else."
    )
    user = (
        f"Mentally execute this {name} program and report the final value of each "
        f"result variable. Respond with ONLY a JSON object whose keys are exactly "
        f"[{keys}] and whose values are the computed results (use JSON true/false for "
        "booleans, JSON strings for strings).\n\n"
        f"```{_FENCE[language]}\n{source}\n```"
    )
    return system, user
