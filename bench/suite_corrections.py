"""Multiple-comparison corrections for the analysis suite. Zero API.

The suite's confirmatory p-value family is FIXED (see ANALYSIS_PLAN.md) at
exactly six members:
  - the refactor and comprehend difference-in-differences bootstrap p's
    (bench/out/ablation_did.json), and
  - the four per-model conditional-quality knob slopes
    (bench/out/quality_slope.json).
Everything else in the suite is estimation with intervals, and the regression
engines duplicate the DiD hypotheses as cross-checks (not family members).

A bootstrap p of 0.0 means "below the resolution of B=2000 draws"; for the
correction arithmetic it is floored at 1/B with the floor recorded, and it
should be reported as p < 0.001, never as zero.

Runs LAST, after did_analysis.py and quality_slope_analysis.py; writes the
separate artifact bench/out/suite_corrections.json (the source artifacts are
never mutated, so their run-twice byte-identity is preserved).
"""

from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bench.suite_common import BOOTSTRAP, ORDER, artifact_header, write_artifact  # noqa: E402
from bench.analysis import bh_fdr, holm_bonferroni  # noqa: E402

OUT = os.path.join(_HERE, "out", "suite_corrections.json")
DID = os.path.join(_HERE, "out", "ablation_did.json")
SLOPES = os.path.join(_HERE, "out", "quality_slope.json")

P_FLOOR = 1.0 / BOOTSTRAP


def main() -> int:
    for path in (DID, SLOPES):
        if not os.path.exists(path):
            sys.exit(f"missing {path}; run its script first")
    did = json.load(open(DID))
    slopes = json.load(open(SLOPES))

    family = []
    for task in ("refactor", "comprehend"):
        family.append({
            "hypothesis": f"{task} DiD (delta_weakest - delta_strongest) != 0",
            "source": "ablation_did.json",
            "p_raw": did[task]["did"]["p_bootstrap"],
        })
    for model in ORDER:
        family.append({
            "hypothesis": f"{model} conditional-quality slope on the knob != 0",
            "source": "quality_slope.json",
            "p_raw": slopes[model]["quality_cond"]["p_bootstrap"],
        })
    if len(family) != 6:
        sys.exit(f"expected exactly 6 family members, got {len(family)}")

    ps = []
    for h in family:
        p = h["p_raw"]
        if p is None:
            sys.exit(f"missing p for {h['hypothesis']}")
        h["p_floored"] = max(p, P_FLOOR)
        h["below_resolution"] = p < P_FLOOR
        ps.append(h["p_floored"])

    bh = bh_fdr(ps, q=0.05)
    holm = holm_bonferroni(ps, alpha=0.05)
    for h, q, brej, hrej in zip(family, bh["adjusted"], bh["reject"], holm["reject"]):
        h["bh_q"] = q
        h["bh_reject_at_05"] = brej
        h["holm_reject_at_05"] = hrej

    report = {
        "_meta": artifact_header(
            "suite_corrections.py",
            ["bench/out/ablation_did.json", "bench/out/quality_slope.json"],
            p_floor=P_FLOOR,
            family_size=6,
            policy="BH-FDR q=.05 primary, Holm sensitivity; family fixed in "
                   "ANALYSIS_PLAN.md before results were read"),
        "family": family,
        "n_bh_rejections": sum(h["bh_reject_at_05"] for h in family),
        "n_holm_rejections": sum(h["holm_reject_at_05"] for h in family),
    }
    write_artifact(OUT, report)
    for h in family:
        mark = "<" if h["below_resolution"] else "="
        print(f"  p{mark}{h['p_floored']:.4g}  q={h['bh_q']:.4g}  "
              f"BH={'Y' if h['bh_reject_at_05'] else 'n'} "
              f"Holm={'Y' if h['holm_reject_at_05'] else 'n'}  {h['hypothesis']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
