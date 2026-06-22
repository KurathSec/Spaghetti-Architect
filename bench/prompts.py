"""Phase 3 / v2 Phase F — frozen, versioned, **paraphrase-ensembled** prompts.

Each task asks for **one job** and constrains the *graded artifact* to a single
parseable shape, so no chain-of-thought leaks into what the grader parses:

* **refactor** returns *only* a fenced code block;
* **judge (pointwise)** returns *only* an integer 1..10;
* **judge (pairwise)** returns *only* a single token ``A`` or ``B``;
* **comprehension** returns *only* a JSON object of the result variables.

Prompt-robustness as a measured axis (HELM + FormatSpread): every task has a
**canonical** prompt (``variant=0``, used for the headline numbers) plus ``>=2``
frozen **paraphrases** that vary *wording / role framing / order only* while holding
the **output-format instruction byte-identical**, so grading is unaffected and the
between-prompt spread is a clean robustness metric. The paraphrases are frozen and
hashed (:func:`prompt_set_hash`); they were authored before any results existed and
are never tuned on results.

``PROMPT_VERSION`` is stamped into the manifest and every result record, so a prompt
change is always traceable. Bump it whenever a template below changes.
"""

from __future__ import annotations

import hashlib
from typing import List

PROMPT_VERSION = "bench-prompts-v2"
N_PARAPHRASES = 3                       # variant 0 = canonical, 1..2 = paraphrases
PARAPHRASE_VARIANTS = list(range(N_PARAPHRASES))

# Language display names + the fence tag used in the returned code block.
_LANG_NAME = {"python": "Python", "javascript": "JavaScript (ES5)", "go": "Go",
              "java": "Java", "cpp": "C++"}
_FENCE = {"python": "python", "javascript": "javascript", "go": "go",
          "java": "java", "cpp": "cpp"}


def _v(variant: int, options: List[str]) -> str:
    return options[variant % len(options)]


# --------------------------------------------------------------------------- #
# Task A — Refactor (output format constant: a single fenced code block)
# --------------------------------------------------------------------------- #
def refactor(language: str, source: str, result_vars: List[str], variant: int = 0) -> tuple:
    """``(system, user)`` asking for a clean, behavior-preserving rewrite. The
    grader-relevant contract — keep the input values and the ``result_var`` names,
    preserve the final result-printing line(s), and return ONLY a fenced block — is
    **identical across paraphrases**; only the framing/wording varies."""
    name, fence = _LANG_NAME[language], _FENCE[language]
    rv = ", ".join(result_vars)
    keep = (  # constant across variants (grading depends on it)
        "Keep the same input values and the same result variable names "
        f"({rv}). Preserve the final line(s) that print the result"
        + (" to stdout as JSON" if language != "python" else "")
        + " so the program's observable output is unchanged."
    )
    fmt = (f"Return ONLY the rewritten {name} program in a single ```{fence} code "
           "block, with no explanation before or after.")
    payload = f"```{fence}\n{source}\n```"

    system = _v(variant, [
        "You are an expert software engineer who simplifies over-engineered code "
        "into the cleanest idiomatic form that preserves exact behavior. You output "
        "only code, never commentary.",
        "You are a senior code reviewer who rewrites convoluted code into its "
        "simplest, most readable idiomatic form without changing what it computes. "
        "You reply with code only.",
        "You refactor needlessly complex programs into clean, minimal, idiomatic "
        "code with identical behavior, and you respond with code and nothing else.",
    ])
    intro = _v(variant, [
        f"The following {name} code is correct but deliberately over-complicated. "
        "Rewrite it to be as clean, simple, and idiomatic as possible while computing "
        "exactly the same result.",
        f"Below is working but needlessly convoluted {name} code. Produce the "
        "simplest idiomatic version that behaves identically.",
        f"Simplify this over-engineered {name} program into clean idiomatic code that "
        "computes exactly the same result.",
    ])
    return system, f"{intro} {keep}\n\n{fmt}\n\n{payload}"


# --------------------------------------------------------------------------- #
# Task B — Judge (pointwise: integer 1..10; pairwise: a single A/B)
# --------------------------------------------------------------------------- #
def judge_pointwise(language: str, source: str, variant: int = 0) -> tuple:
    """Rate maintainability on an integer 1..10 (10 = best). The 'respond with only
    the integer' contract is constant across paraphrases."""
    name = _LANG_NAME[language]
    fmt = "Respond with ONLY the integer, nothing else."
    system = _v(variant, [
        "You are a meticulous code reviewer. You rate code maintainability on an "
        "integer scale from 1 (unmaintainable) to 10 (exemplary). You respond with "
        "only the integer, nothing else.",
        "You are an experienced software maintainer scoring how maintainable code is, "
        "from 1 (worst) to 10 (best), replying with the integer only.",
        "You assess code maintainability strictly, returning a single integer 1..10.",
    ])
    intro = _v(variant, [
        f"Rate the maintainability and readability of this {name} code on an integer "
        "scale from 1 to 10 (10 = best).",
        f"On a 1-to-10 scale (10 = most maintainable), how maintainable and readable "
        f"is this {name} code?",
        f"Give this {name} code a maintainability/readability score from 1 to 10 "
        "(10 = best).",
    ])
    return system, f"{intro} {fmt}\n\n```{_FENCE[language]}\n{source}\n```"


def judge_pairwise(language: str, source_a: str, source_b: str, variant: int = 0) -> tuple:
    """Pick the more maintainable of two versions; answer only ``A`` or ``B``. The
    A/B output contract is constant across paraphrases (so position-swap still works)."""
    name, fence = _LANG_NAME[language], _FENCE[language]
    fmt = "Answer with ONLY the single letter A or B."
    system = _v(variant, [
        "You compare two implementations and decide which is more maintainable. "
        "You answer with only a single letter: A or B.",
        "You judge which of two programs is the more maintainable and reply with one "
        "letter, A or B, only.",
        "Given two implementations, you state which is more maintainable using a "
        "single letter A or B and nothing else.",
    ])
    intro = _v(variant, [
        f"Here are two {name} programs that compute the same result. Which is more "
        "maintainable and readable?",
        f"The two {name} programs below are behaviorally identical. Which one is more "
        "maintainable and readable?",
        f"Compare these two equivalent {name} programs for maintainability and "
        "readability. Which is better?",
    ])
    return system, (f"{intro} {fmt}\n\n"
                    f"Program A:\n```{fence}\n{source_a}\n```\n\n"
                    f"Program B:\n```{fence}\n{source_b}\n```")


# --------------------------------------------------------------------------- #
# Task C — Comprehension / output prediction (output: a JSON object)
# --------------------------------------------------------------------------- #
def comprehend(language: str, source: str, result_vars: List[str], variant: int = 0) -> tuple:
    """Predict the final values of the result variables, as a JSON object. The JSON
    key contract is constant across paraphrases."""
    name = _LANG_NAME[language]
    keys = ", ".join(f'"{v}"' for v in result_vars)
    fmt = (f"Respond with ONLY a JSON object whose keys are exactly [{keys}] and whose "
           "values are the computed results (use JSON true/false for booleans, JSON "
           "strings for strings).")
    system = _v(variant, [
        "You are a precise interpreter. You mentally execute code and report the "
        "final values of the requested variables. You respond with only a single "
        "JSON object, nothing else.",
        "You act as a deterministic code executor, reporting the final values of the "
        "requested variables as a single JSON object only.",
        "You trace code execution exactly and output only a JSON object of the final "
        "requested variable values.",
    ])
    intro = _v(variant, [
        f"Mentally execute this {name} program and report the final value of each "
        "result variable.",
        f"Trace the execution of this {name} program and give the final value of each "
        "result variable.",
        f"Determine what this {name} program computes and report each result "
        "variable's final value.",
    ])
    return system, f"{intro} {fmt}\n\n```{_FENCE[language]}\n{source}\n```"


# --------------------------------------------------------------------------- #
# Prompt-set hash (pre-registration: prove the prompts were not tuned on results)
# --------------------------------------------------------------------------- #
def prompt_set_hash() -> str:
    """A content hash over **every** task x variant template, rendered on a fixed
    probe so a change to any prompt (canonical or paraphrase) changes the hash. Stamp
    it into the manifest/results so the frozen prompt set is auditable."""
    h = hashlib.sha256()
    probe_src = "x = 1\n"
    rvars = ["r"]
    for variant in PARAPHRASE_VARIANTS:
        for sysmsg, usr in (
            refactor("python", probe_src, rvars, variant),
            judge_pointwise("python", probe_src, variant),
            judge_pairwise("python", probe_src, probe_src, variant),
            comprehend("python", probe_src, rvars, variant),
        ):
            h.update(sysmsg.encode("utf-8"))
            h.update(b"\x00")
            h.update(usr.encode("utf-8"))
            h.update(b"\x01")
    return h.hexdigest()[:16]
