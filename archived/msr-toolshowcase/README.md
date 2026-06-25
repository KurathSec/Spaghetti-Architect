# Paper — MSR Data and Tool Showcase (Reusable Tool)

This directory holds the **tool paper** for Spaghetti Architect, targeting the
**MSR Data and Tool Showcase** track (Reusable Tool submission).

- `spaghetti_architect.tex` — the paper (acmart / `sigconf`), a working skeleton
  with section scaffolding and clearly-marked `TODO`s.
- `refs.bib` — starter bibliography (verify/complete every entry).

> This is the **tool** paper. The empirical LLM-evaluation results belong in a
> separate paper; here, cite that study only as evidence of use.

## Track rules (verify against the current CFP)

- **Length:** max **4 pages + 1 page references**.
- **Template:** ACM **Primary Article Template**, `\documentclass[sigconf]{acmart}`.
- **Anonymity:** **single-anonymous** — author names *are* shown.
- **Review criteria:** value, usefulness, reusability.
- **Availability:** artifact available at submission; archive a **DOI** (e.g.
  Zenodo) and cite it in the camera-ready (FAIR).

MSR 2026's deadline (10 Nov 2025) has passed; the realistic target is **MSR 2027**
(Dublin). Confirm dates and the page limit on the current call before submitting.

## Building

`acmart.cls` is **not** bundled (track the upstream version). Get the ACM
"Primary Article Template" and place `acmart.cls` here (or use the Overleaf
template), then:

```bash
pdflatex spaghetti_architect
bibtex   spaghetti_architect
pdflatex spaghetti_architect
pdflatex spaghetti_architect
```

## Before submitting — checklist

- [ ] Fill the author block + `\email` (single-anonymous: real identity).
- [ ] Replace every `TODO` in the `.tex` and `.bib`.
- [ ] Add CCS concepts (https://dl.acm.org/ccs) and the ACM reference format.
- [ ] Mint the **Zenodo DOI** (`.zenodo.json` is ready) and insert it in `\S`Availability.
- [ ] Fill the preliminary-evaluation numbers (test count, toolchain versions,
      instance-space size) from a clean run of the test suite.
- [ ] Replace the pipeline prose in `\S`Tool Overview with a real figure.
- [ ] Keep it within 4 pages + 1 of references.
