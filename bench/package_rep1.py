"""Package the rep1 campaign's finalize payloads into durable raw archives.

Reads the tagged finalize JSONs (bench/out/subagent/*__rep1.json, transient by
design) and writes bench/out/ablation_v2/{task}__{condition}__{slug}.jsonl.gz
with the same six-field record projection as the published archives
({sample, variant, profile, language, intrinsic, raw_outputs}, plus "tier"
when present and "prompt_mode": "cot" for the CoT lane), records key-sorted
and ordered by (sample, profile, language, variant) so packaging is
deterministic. Verifies before writing: env.run_tag == "rep1", k == 8, the
env corpus stamp matches the filename condition, and item counts are complete
(1500 per full-corpus batch; 300 for the agg_stats CoT lane).

Committed BEFORE the campaign runs (bench/PREREGISTRATION_V2.md).
"""

from __future__ import annotations

import glob
import gzip
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
SUBAGENT_DIR = os.path.join(_HERE, "out", "subagent")
OUT_DIR = os.path.join(_HERE, "out", "ablation_v2")

_NAME = re.compile(
    r"^(?P<task>refactor|comprehend)__(?P<slug>.+?)"
    r"(?:__(?P<family>agg_stats))?"
    r"(?:__(?P<cond>unannotated|markers_only|comments_only|lying))?"
    r"(?P<cot>__cot)?__rep1\.json$")

KEEP = ("sample", "variant", "profile", "language", "intrinsic", "tier")


def main() -> int:
    paths = sorted(glob.glob(os.path.join(SUBAGENT_DIR, "*__rep1.json")))
    if not paths:
        sys.exit("no *__rep1.json finalize payloads found")
    os.makedirs(OUT_DIR, exist_ok=True)
    written = []
    for path in paths:
        fn = os.path.basename(path)
        m = _NAME.match(fn)
        if not m:
            sys.exit(f"unrecognized rep1 finalize name: {fn}")
        task, slug = m.group("task"), m.group("slug")
        cond = m.group("cond") or "annotated"
        cot = bool(m.group("cot"))
        payload = json.load(open(path, encoding="utf-8"))
        env = payload.get("env") or {}
        if env.get("run_tag") != "rep1":
            sys.exit(f"{fn}: env.run_tag is {env.get('run_tag')!r}, not 'rep1'")
        if payload.get("k") != 8:
            sys.exit(f"{fn}: k is {payload.get('k')!r}, not 8")
        if env.get("corpus_condition") != cond:
            sys.exit(f"{fn}: env corpus {env.get('corpus_condition')!r} "
                     f"!= filename condition {cond!r}")
        if cot and env.get("prompt_mode") != "cot":
            sys.exit(f"{fn}: __cot filename without prompt_mode stamp")
        items = payload.get("items") or []
        want = 300 if cot else 1500
        if len(items) != want:
            sys.exit(f"{fn}: {len(items)} items, expected {want}")
        recs = []
        for it in items:
            rec = {k: it[k] for k in KEEP if k in it}
            rec["raw_outputs"] = it["raw_outputs"]
            if len(rec["raw_outputs"]) != 8:
                sys.exit(f"{fn}: item {it.get('sample')} has "
                         f"{len(rec['raw_outputs'])} draws, expected 8")
            if cot:
                rec["prompt_mode"] = "cot"
            recs.append(rec)
        recs.sort(key=lambda r: (r["sample"], r["profile"], r["language"],
                                 r.get("variant", "base")))
        cname = "cot" if cot else cond
        out = os.path.join(OUT_DIR, f"{task}__{cname}__{slug}.jsonl.gz")
        with gzip.open(out, "wt", encoding="utf-8") as fh:
            for rec in recs:
                fh.write(json.dumps(rec, sort_keys=True) + "\n")
        written.append(os.path.basename(out))
        print(f"packaged {os.path.basename(out)} ({len(recs)} records)")
    print(f"{len(written)} archives written to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
