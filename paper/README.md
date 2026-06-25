# `paper/` — the DMLR data-centric resource paper

This is the **current target paper**: a data-centric *resource* paper for
**DMLR — the Journal of Data-centric Machine Learning Research** (`data.mlr.press`), the
**rolling, no-deadline archival journal** in the JMLR family. (Do **not** confuse it with
the `dmlr.ai` ICLR/ICML *workshops*, which are deadline-based.)

> The prior-venue drafts are archived, not deleted:
> - `archived/msr-toolshowcase/` — the MSR Data & Tool Showcase engine/tool paper (superseded here).
> - `bench/paper/benchmark.tex` — the EACL benchmark paper, **kept in place** as the seed of the
>   *deferred* empirical / EMSE paper (the LLM-as-judge meta-evaluation). It is wired to
>   `run_bench.py --report`; do not move it.

## Scope (important)
This paper covers the **generator + the dataset + label-validity + reference baselines**.
It must **not** report the LLM-as-judge meta-evaluation *findings* — that is the separate,
deferred paper. Cite it only as a motivating use case.

## Build
```
pdflatex dmlr && bibtex dmlr && pdflatex dmlr && pdflatex dmlr
```
**TEMPLATE TODO:** `dmlr.tex` ships a self-contained `article` preamble so it compiles now.
Before submission, swap in the official DMLR/JMLR style and confirm on `data.mlr.press`:
(1) length norm (JMLR-family = flexible, not the workshop 8pp cap), (2) double-blind policy,
(3) whether a Croissant record / reproducibility statement is wanted in-band.

## Structure (one owner per file → parallel-safe)
| File | Owner | Content |
|------|-------|---------|
| `dmlr.tex` | (main) | preamble, title, abstract, `\input` glue, bib |
| `sections/intro.tex` | PROSE | ML-data-centric motivation + contributions |
| `sections/resource.tex` | PROSE | generator, ops/anti-patterns, two axes, contamination protocol |
| `sections/validity.tex` | ANCHOR | real radon/lizard/cognitive/BW numbers + honest reading |
| `sections/baselines.tex` | BASELINES | non-LLM panel + DeepSeek partial, reference framing |
| `sections/related.tex` | PROSE | datasheets, contamination, obfuscation/smells/readability |
| `sections/limitations.tex` | PROSE | scope, validity caveat, contamination asymmetry, dual-use |
| `sections/datasheet.tex` | DOCS | Gebru datasheet (appendix) |

Editorial macros `\todo{}` / `\owner{}` / `\port{}` mark open work; strip before camera-ready.
The workflow that fills these is in `bench/notes/dmlr-resource-paper-workflow.md` (private).
