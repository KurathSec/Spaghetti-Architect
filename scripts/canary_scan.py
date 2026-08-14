#!/usr/bin/env python3
"""Canary detector: has a text corpus / model output ingested this dataset?

Two marker families are scanned:

1. The release-level GUID (``bench.dataset.CANARY_GUID``), embedded in
   ``bench/data/CANARY.txt`` and the manifest: a hit proves ingestion of the
   released metadata.
2. The per-instance derived canaries (v0.3.0, dataset 2.1+): every newly
   minted instance carries ``trace_id = HMAC-SHA256(GUID, "<version>:<stem>")``
   truncated to ``sa-<16 hex>`` as an inert input rendered into the source of
   all five languages. A hit identifies the exact instance (and hence split)
   that was ingested.

Trust model: the derivation key is the PUBLIC GUID, so anyone holding the
repository can run detection over arbitrary text; the private test split's
values remain unguessable without its stems (the stems embed private-seed
content). Detection of private-split canaries therefore needs
``BENCH_HELDOUT_SEED`` (or an explicit --stems file) to recompute the family.

Usage:
    python3 scripts/canary_scan.py PATH [PATH ...]
        [--dataset-version 2.1] [--stems FILE] [--include-test]

PATH may be files or directories (recursed; binary-ish files are skipped).
Exit status: 0 = no hits, 2 = hits found (grep convention: findings are loud).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bench import dataset as D  # noqa: E402

_CANDIDATE_RE = re.compile(r"sa-[0-9a-f]{16}")
_SKIP_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".zip", ".gz",
                  ".whl", ".pyc", ".so", ".o", ".class"}


def known_trace_ids(dataset_version: str, extra_stems, include_test: bool) -> dict:
    """{trace_id: (origin, stem)} for every stem we can enumerate."""
    known = {}

    def add(origin, stems):
        for stem in stems:
            known[D.canary_trace_id(dataset_version, stem)] = (origin, stem)

    manifest = os.path.join(_HERE, "..", "bench", "data", "manifest.json")
    if os.path.exists(manifest):
        add("dev-manifest", json.load(open(manifest)).get("ground_truth", {}))
    for vname in ("manifest_v2.1.json",):
        p = os.path.join(_HERE, "..", "bench", "data", vname)
        if os.path.exists(p):
            add(vname, json.load(open(p)).get("ground_truth", {}))
    if extra_stems:
        add("stems-file", [ln.strip() for ln in open(extra_stems)
                           if ln.strip()])
    if include_test:
        try:
            sp = D.mint("test")
            add("private-test", sp.samples)
        except Exception as ex:  # noqa: BLE001
            print(f"note: cannot mint the private test split here ({ex}); "
                  f"test-split canaries not scanned", file=sys.stderr)
    return known


def iter_files(paths):
    for p in paths:
        if os.path.isfile(p):
            yield p
        else:
            for root, _dirs, files in os.walk(p):
                for f in files:
                    if os.path.splitext(f)[1].lower() not in _SKIP_SUFFIXES:
                        yield os.path.join(root, f)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--dataset-version", default="2.1")
    ap.add_argument("--stems", help="file with one stem per line to derive against")
    ap.add_argument("--include-test", action="store_true",
                    help="also derive the private test split's canaries "
                         "(needs BENCH_HELDOUT_SEED)")
    args = ap.parse_args()

    known = known_trace_ids(args.dataset_version, args.stems, args.include_test)
    hits = []
    for path in iter_files(args.paths):
        try:
            text = open(path, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        if D.CANARY_GUID in text:
            hits.append((path, "release-guid", D.CANARY_GUID[:40] + "..."))
        for cand in set(_CANDIDATE_RE.findall(text)):
            if cand in known:
                origin, stem = known[cand]
                hits.append((path, f"instance-canary [{origin}] {stem}", cand))

    if not hits:
        print(f"no canary hits ({len(known)} derived instance canaries checked)")
        return 0
    for path, kind, marker in hits:
        print(f"HIT {path}: {kind}: {marker}")
    print(f"{len(hits)} hit(s): this corpus contains released dataset content")
    return 2


if __name__ == "__main__":
    sys.exit(main())
