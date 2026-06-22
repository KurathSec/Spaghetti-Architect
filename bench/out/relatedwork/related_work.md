# Related Work and Positioning

> **v3 reframing (EACL via ARR).** The paper now *leads* with the LLM-as-judge angle:
> the by-construction `SPAGH_*` inclusion order is a **ground-truth quality order**, so
> we measure **judge faithfulness** to a known ordering (a thing MT-Bench/jury work
> lacks). Related-work order in the emitted paper is NLP-first — code-LLM benchmarks →
> judges+missing-ground-truth (incl. **ReCode**, the closest semantics-preserving-
> perturbation robustness line, and **BigCodeBench**) → contamination → prompt
> sensitivity → *then* the obfuscation/mutation/generator provenance. The two added
> refs (`wang-etal-2023-recode`, `zhuo-etal-2024-bigcodebench`) are in `refs.bib`. Prose below is the
> original adopt-and-add survey and is retained as scaffolding.

Spaghetti Architect is a *correctness-preserving* code generator repurposed as a
benchmark instrument: a clean JSON IR is compiled into deliberately awful but
provably-equivalent code across five languages, gated by an oracle validator, and
exposed along two orthogonal difficulty axes — *intrinsic* complexity (logic
genuinely grows: `#operations`, cascade arms `N`, list length `L`) and *incidental*
complexity (the `minimal`→`standard`→`max` profile adds spaghetti with **zero**
semantic change). Fresh instances minted from a held-out seed give contamination
resistance. We situate this against six neighboring lines of work: obfuscation,
mutation testing, correctness-by-construction generators, code/reasoning/refactor
benchmarks, LLM-as-judge and metric validity, and the contamination /
prompt-robustness literature.

## Stance: we adopt the field's mitigations and add the one knob it lacks

We do not claim to out-engineer the established defenses; we **adopt** them and
**add** a single new affordance on top. For *contamination* we adopt the field's
move to freshly generated, time-gated, or canary-protected instances —
LiveCodeBench's live time-gating [@jain-etal-2025-livecodebench], the templated
re-sampling of GSM-Symbolic [@mirzadeh-etal-2024-gsm] and the graph-templated
regeneration of DyVal [@zhu-etal-2024-dyval], and Jacovi et al.'s encryption/canary
hygiene [@jacovi-etal-2023-stop] (with detection probes such as Min-K% Prob
[@shi-etal-2024-detecting] as an audit) — by re-minting every literal from a private
held-out seed. For *judge validity* we adopt position-swapping, reference-guided
grading, and human-agreement calibration from MT-Bench [@zheng-etal-2023-judging] and
the panel-of-evaluators / jury design (PoLL) of Verga et al. [@verga-etal-2024-replacing]
to suppress single-judge and self-preference bias. For *refactoring* we adopt
functional differential testing rather than reference-string matching, following
EvalPlus's test-augmented functional grading [@liu-etal-2023-code] and
RefactorBench's executable refactor checks [@gautam-etal-2025-refactorbench]. For
*prompt sensitivity* we adopt a single canonical prompt reported alongside a
robustness sweep over plausible formats, following HELM's standardized scenarios
[@liang-etal-2023-holistic], PromptBench's adversarial-prompt perturbations
[@zhu-etal-2023-promptrobust], and FormatSpread's spurious-format interval reporting
[@sclar-etal-2024-quantifying]. What none of these provide, and what we add, is a
**controlled stimulus with two orthogonal knobs** — *intrinsic* complexity vs.
semantics-preserving *incidental* complexity — together with a **provably
reachable optimum** (the known-optimal clean baseline that produced each
instance), so that "the model got worse" can be attributed to one knob with the
other held fixed, and progress is measured against a ground-truth target rather
than another model's opinion.

## Comparison

| Approach | Ground-truth labels? | Tunable *intrinsic* knob? | Tunable *incidental* knob? | Auto-checkable semantic equivalence? | Contamination resistance? | Multi-language? |
|---|---|---|---|---|---|---|
| **Spaghetti Architect (ours)** | **Yes** (oracle + known-optimal clean baseline) | **Yes** (`N`, `L`, `n_ops`) | **Yes** (profile, logic fixed) | **Yes** (validator: differential testing vs. oracle) | **Yes** (held-out seed re-mints literals) | **Yes** (Py/JS/Go/Java/C++ from one IR) |
| Obfuscators — Collberg–Thomborson taxonomy [@collberg-etal-1997-taxonomy; @collberg-etal-1998-manufacturing], Tigress [@collberg-2015-tigress], OLLVM [@junod-etal-2015-obfuscator], `javascript-obfuscator` [@kachalov-2016-javascript] | No (no quality label; goal is RE-resistance) | Partial (input program size) | Partial (transform strength, not orthogonalized) | Implicit (transforms *claim* equivalence; not graded against an oracle per item) | No (transforms fixed seeds, not a benchmark) | Per-tool (each tied to C/LLVM-IR or JS) |
| Mutation testing [@demillo-etal-1978-hints; @jia-harman-2011-analysis] | Yes (mutant kill labels) | n/a | n/a | **Inverted**: mutants *change* semantics by design | No | Tool-specific |
| Program generators — Csmith [@yang-etal-2011-finding], YARPGen [@livinskii-etal-2020-random] | Differential (oracle = other compilers) | Yes (program size/feature mix) | No (no readability axis) | Yes (cross-compiler differential) | Yes (random each run) | C / C++ |
| Correctness benchmarks — HumanEval [@chen-etal-2021-evaluating], MBPP [@austin-etal-2021-program] | Yes (unit tests) | No (fixed hand-written tasks) | No | Yes (tests) | **No** (static, public, contaminated) | Python |
| Reasoning / refactor benchmarks — CRUXEval [@gu-etal-2024-cruxeval], SWE-bench [@jimenez-etal-2024-swe], RefactorBench [@gautam-etal-2025-refactorbench] | Yes | Partial (task difficulty) | No (no semantics-fixed mess knob) | Yes (exec / tests / AST checks) | Partial (CRUXEval self-regenerates; SWE/Refactor are static GitHub corpora) | Mostly Python |
| LLM-as-judge / metric validity — MT-Bench [@zheng-etal-2023-judging], juries/PoLL [@verga-etal-2024-replacing], Maintainability Index critique [@vandeursen-2014-think] | No (human/LLM preference; no controlled stimulus) | No | No | No | n/a | n/a |
| Functional grading — EvalPlus [@liu-etal-2023-code] | Yes (augmented unit tests) | No | No | Yes (functional, not reference-match) | No (augments static HumanEval/MBPP) | Python |
| Dynamic/templated regeneration — GSM-Symbolic [@mirzadeh-etal-2024-gsm], DyVal [@zhu-etal-2024-dyval] | Yes (templated answer keys) | Partial (DyVal depth/width) | No (no readability axis) | Yes (templated checks) | **Yes** (re-sampled per run) | Reasoning/math (text) |
| Contamination detection/mitigation — [@jacovi-etal-2023-stop; @ravaut-etal-2024-comprehensive], Min-K% Prob [@shi-etal-2024-detecting], LiveCodeBench [@jain-etal-2025-livecodebench] | varies | No | No | varies | **Yes** (live/encrypted/time-gated; probe-detectable) | Code (single-lang focus) |
| Prompt robustness — HELM [@liang-etal-2023-holistic], PromptBench [@zhu-etal-2023-promptrobust], FormatSpread [@sclar-etal-2024-quantifying] | n/a (axis is the prompt, not the task label) | No | No | n/a | n/a | n/a |
| Human-validated readability anchors — Buse–Weimer [@buse-weimer-2010-learning], Scalabrino et al. [@scalabrino-etal-2018-comprehensive], Dorn [@dorn-2012-general] | Yes (human readability scores) | No | No | n/a (rating models, not generators) | n/a | Java-centric corpora |

## Positioning

**Obfuscation vs. anti-optimization.** The eleven `SPAGH_*` transforms descend
directly from the Collberg–Thomborson taxonomy — opaque predicates (`SPAGH_009`)
are theirs verbatim [@collberg-etal-1998-manufacturing] — and tools like Tigress
[@collberg-2015-tigress], OLLVM [@junod-etal-2015-obfuscator], and `javascript-obfuscator`
[@kachalov-2016-javascript] apply structurally similar moves (control-flow
flattening, dead-code injection). But their objective is *reverse-engineering
resistance*, optimized to be hard for a deobfuscator, not for a human reader, and
they emit neither a quality label nor a known-optimal target. The project's own
`eval/` already quantifies this divergence: against `javascript-obfuscator` on a
clean-JS baseline, Spaghetti amplifies *control flow in proportion to the logic*
whereas the obfuscator adds a roughly constant dispatcher plus lexical noise — a
different objective. We reuse obfuscation primitives as a *maintainability-degradation
knob* with ground truth, which the obfuscation literature does not provide.

**Mutation testing is the inverse, and reviewers will raise it.** Mutation testing
[@demillo-etal-1978-hints; @jia-harman-2011-analysis] injects small *faults* and measures whether a
test suite detects the resulting **behavioral change**. Our entire premise is the
opposite: every transform is **semantics-preserving by construction**, oracle-verified
on the base input *and* on `*_v0..v4` differential variants, so a "good" output is one
that stays equivalent while shedding incidental complexity. Mutants are graded by
*kill* (did semantics visibly change?); our refactors are graded by *equivalence plus
simplification toward a known optimum*. The analogy is real but the sign is flipped,
and that flip is what yields clean labels for refactoring and comprehension.

**Csmith is the closest sibling — we add labels and a degradation axis.** Csmith
[@yang-etal-2011-finding] and YARPGen [@livinskii-etal-2020-random] are the canonical
"correctness-by-construction" random generators: they emit *valid* C/C++ to stress a
compiler, using cross-compiler *differential testing* as the oracle, and they are
inherently contamination-resistant because every run is fresh. We borrow that spine
(a generator whose outputs are correct by construction, freshly minted) but target a
different consumer — an **LLM under test, not a compiler** — and add two things they
lack: a *known-optimal clean baseline* per instance (so simplification is measurable,
not just validity), and an *incidental-complexity knob orthogonal to program size* (so
we can decompose "the problem grew" from "the presentation got messier"). Static
code benchmarks (HumanEval [@chen-etal-2021-evaluating], MBPP [@austin-etal-2021-program], SWE-bench
[@jimenez-etal-2024-swe]) have correctness oracles but fixed, public, increasingly
*contaminated* tasks and no semantics-fixed mess axis; CRUXEval [@gu-etal-2024-cruxeval]
pioneers execution/output-prediction and even sketches a self-regeneration recipe,
and RefactorBench [@gautam-etal-2025-refactorbench] auto-checks refactors via AST tests, but
neither isolates incidental from intrinsic difficulty under a per-item ground-truth
oracle.

**Judges and contamination.** LLM-as-judge [@zheng-etal-2023-judging] — even in its
bias-hardened jury form [@verga-etal-2024-replacing] — and software maintainability metrics
[@vandeursen-2014-think] both lack a *controlled stimulus*: a judge can only
be checked against another judge or against humans, and the Maintainability Index is
empirically shaky. Human-validated readability models [@buse-weimer-2010-learning;
@scalabrino-etal-2018-comprehensive; @dorn-2012-general] do anchor to real annotator
judgments, but they *rate* given snippets rather than *generate* a semantics-fixed
difficulty ladder; we reuse them as an external, zero-IRB construct-validity anchor for
the incidental axis. Our profile knob supplies exactly the missing stimulus — a
ground-truth-ordered degradation ladder against which a judge's *monotonicity and
sensitivity* become measurable, and to which an external non-LLM metric (radon) and
those human-derived readability scores can be anchored. On contamination, the
literature converges on dynamic or protected evaluation [@jacovi-etal-2023-stop;
@ravaut-etal-2024-comprehensive], with LiveCodeBench [@jain-etal-2025-livecodebench] time-gating live
problems and GSM-Symbolic [@mirzadeh-etal-2024-gsm] / DyVal [@zhu-etal-2024-dyval]
regenerating instances from templates; we obtain the same guarantee structurally —
re-minting literals (list contents, key sets, probes) from a private seed so the
held-out `test` split is almost certainly unseen, while keeping the *structure*
identical so dev/test scores are directly comparable as a memorization probe (and
detection probes such as Min-K% Prob [@shi-etal-2024-detecting] remain applicable as an
audit).

## How we mitigate vs. prior work

We close with an explicit threat-by-threat map. For each of the four threats to a
code-quality benchmark, we name the prior technique we **adopt** and the specific gap
we **add**; the adopted half keeps us honest, the added half is the contribution.

- **Contamination (memorization of fixed instances).** *Adopt:* dynamic regeneration
  and protected/time-gated evaluation — LiveCodeBench's live time-gating
  [@jain-etal-2025-livecodebench], GSM-Symbolic's templated re-sampling
  [@mirzadeh-etal-2024-gsm], DyVal's graph-templated generation [@zhu-etal-2024-dyval], and
  Jacovi et al.'s canary/encryption hygiene [@jacovi-etal-2023-stop], with Min-K%
  Prob [@shi-etal-2024-detecting] as a contamination *probe*. We re-mint every literal from a
  private held-out seed and expose a dev/test split as a built-in memorization check.
  *Add:* contamination resistance is delivered while the *structural* difficulty knobs
  stay fixed across the split, so freshness does not confound the difficulty
  measurement — dev and test are the same instance modulo literals.

- **Judge validity (does the grader measure quality?).** *Adopt:* position-swapping,
  reference-guided grading, and human-agreement calibration from MT-Bench
  [@zheng-etal-2023-judging], plus the diverse-panel / jury design (PoLL) of Verga et al.
  [@verga-etal-2024-replacing] to cancel single-judge and self-preference bias. *Add:* a
  *controlled stimulus the judge literature lacks* — a ground-truth-ordered incidental
  ladder (`minimal`→`standard`→`max` at fixed semantics) that turns judge quality into
  a *measurable monotonicity/sensitivity test*, externally anchored to a non-LLM metric
  (radon) and to human readability models [@buse-weimer-2010-learning;
  @scalabrino-etal-2018-comprehensive; @dorn-2012-general].

- **Optimum / gaming (can "better" be defined and reached?).** *Adopt:* functional
  differential testing instead of reference-string matching — EvalPlus's
  test-augmented functional grading [@liu-etal-2023-code] and RefactorBench's executable
  refactor checks [@gautam-etal-2025-refactorbench], in the correctness-by-construction
  tradition of Csmith [@yang-etal-2011-finding] and YARPGen [@livinskii-etal-2020-random]. *Add:* a
  **provably reachable optimum** — the known-optimal clean baseline that *generated*
  each instance — so a refactor is scored as equivalence-preserving *simplification
  toward a known target*, not merely "passes tests"; the optimum exists by construction
  and cannot be gamed by reproducing a memorized gold string.

- **Prompt sensitivity (is the score an artifact of phrasing?).** *Adopt:* a single
  canonical prompt reported together with a robustness sweep over plausible formats —
  HELM's standardized scenarios [@liang-etal-2023-holistic], PromptBench's adversarial-prompt
  perturbations [@zhu-etal-2023-promptrobust], and FormatSpread's spurious-format interval
  reporting [@sclar-etal-2024-quantifying]. *Add:* because the *task semantics are fixed by
  the IR* and the target is an oracle (not a free-form reference answer), the prompt
  sweep varies presentation while the ground truth is invariant — so prompt-induced
  variance is cleanly separable from genuine capability, the same orthogonality that
  separates our intrinsic and incidental axes.

## Contribution delta

No neighbor offers these properties **together**. Obfuscators have transform-strength
knobs but no labels, no orthogonal-readability axis, and no per-item equivalence oracle.
Mutation testing has labels but *changes* semantics. Csmith/YARPGen are correct-by-
construction and contamination-resistant but emit no quality label, no clean-baseline
optimum, no incidental knob, and target compilers in one language. HumanEval/MBPP/
SWE-bench give correctness oracles on static, contaminable, single-axis, mostly-Python
tasks. CRUXEval and RefactorBench advance execution and refactor grading but do not
decompose intrinsic vs. incidental difficulty under a ground-truth oracle. LLM-judge
work has no controlled stimulus; contamination work has freshness but no complexity
knobs. **Spaghetti Architect is the first instrument to combine, in one generator:
(1) ground-truth labels (oracle outputs + a known-optimal clean baseline), (2) two
*orthogonal* difficulty knobs — intrinsic complexity vs. semantics-preserving
incidental complexity, (3) auto-checkable semantic equivalence via differential testing
against that oracle, and (4) contamination resistance through held-out-seed re-minting,
delivered across (5) five languages from a single IR.** That combination is what lets
us answer a question no prior corpus can: *do models fail because the problem got
bigger, or because the code got messier — at identical semantics?*
