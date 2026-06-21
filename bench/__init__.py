"""Spaghetti Architect benchmark generator (the ``bench`` package).

Turns the anti-optimization transpiler into a ground-truth, contamination-resistant
LLM code benchmark (refactor / judge / comprehend). The **core** (``dataset``,
``tasks``, ``grade``, ``prompts``, ``models``, ``run_bench``) is standard-library
only; the single network/key boundary is ``models.py`` (stdlib ``urllib``), and the
optional non-LLM construct-validity anchor (``anchor.py``, ``radon``) is quarantined
in a throwaway virtualenv. See ``bench/aibench_workflow.md`` and ``REQUIREMENTS.md``.
"""
