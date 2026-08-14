# Changelog

## v0.3.0 (unreleased)

Three version axes now coexist and must not be conflated: **software** (this
release, 0.3.0), **dataset** (2.0 = the frozen published split, untouched;
2.1 = additive new content), and **engine rendering spec** (2.0 = the published
rendering, the default everywhere; 2.1 = opt-in additive fixes). Every frozen
artifact — `bench/data/dev/`, `bench/data/manifest.json`, the committed raw
completions under `bench/out/`, and every published number — is byte-untouched
by this release.

### Engine

- **Dual-version rendering spec.** `Engine(..., spec="2.0"|"2.1")` (default
  `"2.0"`, byte-identical to the published rendering; enforced by
  `tests/test_frozen_dev.py`, a 2500-cell sweep against the frozen dev split).
  Spec `"2.1"` activates the two previously-inert transforms ADDITIVELY:
  - `SPAGH_005` (cascading conditionals) now has an observable effect
    independent of `SPAGH_001`: nested else-scope cascades (bounded groups of
    8, flat group links, so CPython's indentation limit is never approached)
    and a two-stage dispatch for `CONDITIONAL_SELECT` (including Go, which
    gains its first `SPAGH_005` consult site).
  - `SPAGH_007` (over-guarding) is implemented for the first time: redundant
    always-true re-checks inside the op body. The minimal crash-free safety
    scaffold is untouched.
  Under spec 2.1, `light` and `standard` render distinctly (the published
  spec-2.0 tie is documented in the datasheet). `minimal`/`light` renders are
  byte-identical across specs by profile-set construction.
- CLI: `python3 -m src.main --spec {2.0,2.1}`; bench: `BENCH_ENGINE_SPEC`
  (read at import, stamped into every run's env block as `engine_spec`).
- `bench/anchor.py --spec 2.1` recomputes the static-metric validity
  correlations under the fixed engine into `bench/out/anchor_v21.json`; the
  published `bench/out/anchor.json` is guarded against accidental overwrite.

### Analysis (zero API)

- **The re-analysis suite** (`bench/suite_common.py` + 11 scripts; registry and
  the fixed 6-member confirmatory family in `ANALYSIS_PLAN.md`): headline
  difference-in-differences reframing of the annotation ablation with shared
  cluster resamples and bootstrap concordance; serving-nondeterminism and
  arm-drift diagnostics; the copy-rate mechanism analysis; agg_stats partial
  credit; incidental-knob quality slopes (an explicit negative result);
  variant sensitivity; freshness power (permutation detection; minimum
  detectable uplift); the leakage-resistant judge pair subset; committed
  provenance scripts for the previously script-less published figures;
  degenerate-subset and Kendall tau-b validity stratification; a
  compression/NCD anchor family.
- `~/.venvs/spaghetti-metrics` now includes `statsmodels` (the documented
  recipe installs it).

### Datasets and protocol

- **Clean profile.** `"clean": []` joins the profile chain: the previously
  unreachable idiomatic branches now render runnable, oracle-verified idiomatic
  source in all five languages (Go's `CONDITIONAL_SELECT` keeps its explicit
  `if`, a documented asymmetry). `grade.baseline_panel` now grades a MEASURED
  scaffold-inclusive clean reference per language through the full semantic
  gate (1500/1500 pass; `bench/uniform_regrade.py` -> `out/uniform_quality.json`
  also recovers `uniform_quality` for the committed completions, previously
  null because the run environment lacked lizard). The old algebraic-identity
  "ceiling" is kept under `clean_ceiling_metric_reachability`.
- **Dataset 2.1 (additive).** `bench/data/dev_v2.1/` + `manifest_v2.1.json`:
  107 instances = the 2.0 families with `status_router` (K in {8,16,32}) and
  `discovery_pipeline` (P in {4,10}) rebalanced, plus the `fsm_twin`
  prior-probe pair (identical structure; only the miss default differs).
  Rendered under spec 2.1, per-instance canaries stamped. The 2.0 split is
  byte-untouched and re-freezing it is guarded (`--force-overwrite-frozen`).
  No model numbers exist for 2.1 (minting is free; evaluation is not).
- **Sidecar annotation mode.** `--annotate {full,none,sidecar}`: sidecar keeps
  the module header in-source and diverts every other comment into
  line-aligned `*.sidecar.json` structures (re-insertion reproduces the full
  render byte-identically). Bench corpus condition is ternary
  (`BENCH_CORPUS`), NEW batches default to the unannotated corpus per the
  annotation-ablation result, and the corpus stamp is now ENFORCED at regrade
  (`--regrade` is also dry-run unless `--write`).
- **Hardened held-out tiers (2.1).** `build_heldout_tiers(seed,
  tiers_version="2.1")` draws the Tier-B/C op-chain SHAPES from a shape space
  using the private seed (rejection-sampled against every public shape), so a
  repository reader no longer learns the held-out structures; the fixed 2.0
  enumeration stays byte-reproducible for the published test numbers.
- **Per-instance canary + detector.** Dataset-2.1 instances carry
  `trace_id = HMAC-SHA256(GUID, "2.1:<stem>")[:16]` as an inert input rendered
  into every language (survives all annotation modes);
  `scripts/canary_scan.py` detects both the release GUID and the derived
  family and names the exact ingested instance.
- **Planned primitives spec.** `architecture.md` §24 specifies fixed-point
  `mean_x1000` AGGREGATE, `STRING_JOIN`, and ASCII `CASE_MAP` with the
  byte-identical single-oracle argument (design only; no implementation).

### Tests

- `tests/test_frozen_dev.py` (frozen-split byte-identity tripwire),
  `tests/test_stream_stability.py` (RNG-stream hashes for the frozen mints).
