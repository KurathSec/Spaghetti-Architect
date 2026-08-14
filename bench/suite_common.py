"""Shared substrate for the zero-API analysis suite (the 2026-08 round).

Every script in the suite re-derives its numbers offline from the committed
archives (``bench/out/{ladder,g3,ablation}/*.jsonl.gz``) or the frozen dev
data; none of them queries a model. Common rules enforced here:

- Refuse to run with ``BENCH_STRIP_ANNOTATIONS`` set: suite scripts that grade
  arm-sensitive metrics do so on the ANNOTATED arm in a default-env process
  (the unannotated arm, where needed, runs in a separate subprocess).
- Paired bootstrap statistics (difference-in-differences, concordance) must
  evaluate every model and both arms on the SAME cluster resamples; use
  :func:`shared_cluster_draws` and :func:`draw_means`.
- Artifacts carry :func:`artifact_header` and NO wall-clock timestamp, so a
  re-run is byte-identical (the run-twice byte-compare is part of the suite's
  verification protocol).
"""

from __future__ import annotations

import collections
import json
import os
import random
import sys
from typing import Dict, Iterable, List, Sequence

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

if os.environ.get("BENCH_STRIP_ANNOTATIONS", "") not in ("", "0", "false") \
        or os.environ.get("BENCH_CORPUS", "") not in ("", "annotated"):
    sys.exit("refusing to run with a non-annotated corpus condition "
             "(BENCH_STRIP_ANNOTATIONS / BENCH_CORPUS): suite scripts grade "
             "the annotated arm by rebuilding sources from the dataset. Unset them; "
             "other-corpus work belongs in an isolated subprocess.")

# Reuse the ablation harness's single source of truth for the ladder order, the
# item/cluster keying, the gz loaders, and the grade cache.
from bench.annotation_ablation import (  # noqa: E402
    CACHE as GRADES_CACHE,
    MODELS,
    ORDER,
    _all_grades as all_grades,
    _base_ir as base_ir,
    _family as family_of,
    _gz as read_gz,
    _key as item_key,
    _load_annotated as load_annotated,
    _load_unannotated as load_unannotated,
)

SUITE_SEED = 20260814
BOOTSTRAP = 2000
OUT_DIR = os.path.join(_HERE, "out")

WEAKEST = ORDER[0]     # Llama-3.1-8B
STRONGEST = ORDER[-1]  # DeepSeek-V4-Flash


def cluster_scores(scores: Dict[str, float]) -> Dict[str, List[float]]:
    """Group an ``{item_key: score}`` dict into base-IR clusters."""
    clusters: Dict[str, List[float]] = collections.defaultdict(list)
    for k, v in scores.items():
        clusters[base_ir(k.split("|")[0])].append(v)
    return dict(clusters)


def shared_cluster_draws(cluster_keys: Iterable[str], iters: int = BOOTSTRAP,
                         seed: int = SUITE_SEED) -> List[List[str]]:
    """Pre-generate ``iters`` cluster resamples (with replacement).

    Paired statistics must evaluate every model/arm on the SAME draw list;
    generate once, reuse everywhere. Keys are sorted first so the stream is
    independent of dict iteration order.
    """
    keys = sorted(set(cluster_keys))
    if not keys:
        raise ValueError("no clusters to resample")
    rng = random.Random(seed)
    n = len(keys)
    return [[rng.choice(keys) for _ in range(n)] for _ in range(iters)]


def draw_means(clusters: Dict[str, List[float]],
               draws: Sequence[Sequence[str]]) -> List[float]:
    """Pooled mean per shared draw (unweighted over items, cluster-resampled).

    Raises on an empty pooled draw rather than defaulting: an empty mean is a
    bug, never a statistic.
    """
    out: List[float] = []
    for d in draws:
        vals = [v for c in d for v in clusters.get(c, ())]
        if not vals:
            raise ValueError("empty pooled draw: cluster space mismatch")
        out.append(sum(vals) / len(vals))
    return out


def percentile_ci95(draws: Sequence[float]) -> List[float]:
    s = sorted(draws)
    n = len(s)
    return [s[int(0.025 * n)], s[int(0.975 * n)]]


def two_sided_bootstrap_p(draws: Sequence[float], null: float = 0.0) -> float:
    """Twice the smaller tail mass at ``null`` (the bench/analysis.py convention)."""
    n = len(draws)
    ge = sum(1 for d in draws if d >= null) / n
    le = sum(1 for d in draws if d <= null) / n
    return min(1.0, 2.0 * min(ge, le))


def mean(xs: Sequence[float]) -> float:
    if not xs:
        raise ValueError("mean of empty sequence")
    return sum(xs) / len(xs)


def artifact_header(script: str, inputs: Sequence[str], **extra) -> dict:
    """Provenance stamp for a suite artifact. Deliberately timestamp-free."""
    h = {
        "generated_by": f"bench/{script}",
        "suite_seed": SUITE_SEED,
        "bootstrap_iters": BOOTSTRAP,
        "inputs": sorted(inputs),
        "zero_api": True,
    }
    h.update(extra)
    return h


def write_artifact(path: str, obj: dict) -> None:
    with open(path, "w") as fh:
        json.dump(obj, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(f"wrote {os.path.relpath(path, _ROOT)}")
