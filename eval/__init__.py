"""Spaghetti Architect metric library (the ``eval`` package).

Exposes the two reusable, standard-library-only modules that ``bench/`` imports:

    from eval import metrics as M      # pure, clock-free metric lanes
    from eval import gen_samples       # deterministic, seeded sample set

Both also run as scripts. This package init adds no import side effects, so the
script and ``-m`` entry styles keep working. See ``eval/README.md``.
"""
