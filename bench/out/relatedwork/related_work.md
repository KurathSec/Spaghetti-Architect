# Related Work and Positioning

Spaghetti Architect is a *correctness-preserving* code generator repurposed as a
benchmark instrument: a clean JSON IR is compiled into deliberately awful but
provably-equivalent code across five languages, gated by an oracle validator, and
exposed along two orthogonal difficulty axes — *intrinsic* complexity (logic
genuinely grows: `#operations`, cascade arms `N`, list length `L`) and *incidental*
complexity (the `minimal`→`standard`→`max` profile adds spaghetti with **zero**
semantic change). Fresh instances minted from a held-out seed give contamination
resistance. We situate this against five neighboring lines of work.

## Comparison

| Approach | Ground-truth labels? | Tunable *intrinsic* knob? | Tunable *incidental* knob? | Auto-checkable semantic equivalence? | Contamination resistance? | Multi-language? |
|---|---|---|---|---|---|---|
| **Spaghetti Architect (ours)** | **Yes** (oracle + known-optimal clean baseline) | **Yes** (`N`, `L`, `n_ops`) | **Yes** (profile, logic fixed) | **Yes** (validator: differential testing vs. oracle) | **Yes** (held-out seed re-mints literals) | **Yes** (Py/JS/Go/Java/C++ from one IR) |
| Obfuscators — Collberg–Thomborson taxonomy [@collberg1997taxonomy; @collberg1998manufacturing], Tigress [@collberg2015tigress], OLLVM [@junod2015ollvm], `javascript-obfuscator` [@kachalov2016jsobfuscator] | No (no quality label; goal is RE-resistance) | Partial (input program size) | Partial (transform strength, not orthogonalized) | Implicit (transforms *claim* equivalence; not graded against an oracle per item) | No (transforms fixed seeds, not a benchmark) | Per-tool (each tied to C/LLVM-IR or JS) |
| Mutation testing [@demillo1978hints; @jia2011mutation] | Yes (mutant kill labels) | n/a | n/a | **Inverted**: mutants *change* semantics by design | No | Tool-specific |
| Program generators — Csmith [@yang2011csmith], YARPGen [@livinskii2020yarpgen] | Differential (oracle = other compilers) | Yes (program size/feature mix) | No (no readability axis) | Yes (cross-compiler differential) | Yes (random each run) | C / C++ |
| Correctness benchmarks — HumanEval [@chen2021humaneval], MBPP [@austin2021mbpp] | Yes (unit tests) | No (fixed hand-written tasks) | No | Yes (tests) | **No** (static, public, contaminated) | Python |
| Reasoning / refactor benchmarks — CRUXEval [@gu2024cruxeval], SWE-bench [@jimenez2024swebench], RefactorBench [@gautam2025refactorbench] | Yes | Partial (task difficulty) | No (no semantics-fixed mess knob) | Yes (exec / tests / AST checks) | Partial (CRUXEval self-regenerates; SWE/Refactor are static GitHub corpora) | Mostly Python |
| LLM-as-judge / metric validity — MT-Bench [@zheng2023judging], Maintainability Index critique [@vandeursen2014maintainability] | No (human/LLM preference; no controlled stimulus) | No | No | No | n/a | n/a |
| Contamination work — [@jacovi2023contamination; @ravaut2024contamination], LiveCodeBench [@jain2025livecodebench] | varies | No | No | varies | **Yes** (live/encrypted/time-gated) | Code (single-lang focus) |

## Positioning

**Obfuscation vs. anti-optimization.** The eleven `SPAGH_*` transforms descend
directly from the Collberg–Thomborson taxonomy — opaque predicates (`SPAGH_009`)
are theirs verbatim [@collberg1998manufacturing] — and tools like Tigress
[@collberg2015tigress], OLLVM [@junod2015ollvm], and `javascript-obfuscator`
[@kachalov2016jsobfuscator] apply structurally similar moves (control-flow
flattening, dead-code injection). But their objective is *reverse-engineering
resistance*, optimized to be hard for a deobfuscator, not for a human reader, and
they emit neither a quality label nor a known-optimal target. The project's own
`eval/` already quantifies this divergence: against `javascript-obfuscator` on a
clean-JS baseline, Spaghetti amplifies *control flow in proportion to the logic*
whereas the obfuscator adds a roughly constant dispatcher plus lexical noise — a
different objective. We reuse obfuscation primitives as a *maintainability-degradation
knob* with ground truth, which the obfuscation literature does not provide.

**Mutation testing is the inverse, and reviewers will raise it.** Mutation testing
[@demillo1978hints; @jia2011mutation] injects small *faults* and measures whether a
test suite detects the resulting **behavioral change**. Our entire premise is the
opposite: every transform is **semantics-preserving by construction**, oracle-verified
on the base input *and* on `*_v0..v4` differential variants, so a "good" output is one
that stays equivalent while shedding incidental complexity. Mutants are graded by
*kill* (did semantics visibly change?); our refactors are graded by *equivalence plus
simplification toward a known optimum*. The analogy is real but the sign is flipped,
and that flip is what yields clean labels for refactoring and comprehension.

**Csmith is the closest sibling — we add labels and a degradation axis.** Csmith
[@yang2011csmith] and YARPGen [@livinskii2020yarpgen] are the canonical
"correctness-by-construction" random generators: they emit *valid* C/C++ to stress a
compiler, using cross-compiler *differential testing* as the oracle, and they are
inherently contamination-resistant because every run is fresh. We borrow that spine
(a generator whose outputs are correct by construction, freshly minted) but target a
different consumer — an **LLM under test, not a compiler** — and add two things they
lack: a *known-optimal clean baseline* per instance (so simplification is measurable,
not just validity), and an *incidental-complexity knob orthogonal to program size* (so
we can decompose "the problem grew" from "the presentation got messier"). Static
code benchmarks (HumanEval [@chen2021humaneval], MBPP [@austin2021mbpp], SWE-bench
[@jimenez2024swebench]) have correctness oracles but fixed, public, increasingly
*contaminated* tasks and no semantics-fixed mess axis; CRUXEval [@gu2024cruxeval]
pioneers execution/output-prediction and even sketches a self-regeneration recipe,
and RefactorBench [@gautam2025refactorbench] auto-checks refactors via AST tests, but
neither isolates incidental from intrinsic difficulty under a per-item ground-truth
oracle.

**Judges and contamination.** LLM-as-judge [@zheng2023judging] and software
maintainability metrics [@vandeursen2014maintainability] both lack a *controlled
stimulus*: a judge can only be checked against another judge or against humans, and the
Maintainability Index is empirically shaky. Our profile knob supplies exactly that
missing stimulus — a ground-truth-ordered degradation ladder against which a judge's
*monotonicity and sensitivity* become measurable, and to which an external non-LLM
metric (radon) can be anchored. On contamination, the literature converges on dynamic
or protected evaluation [@jacovi2023contamination; @ravaut2024contamination], with
LiveCodeBench [@jain2025livecodebench] time-gating live problems; we obtain the same
guarantee structurally — re-minting literals (list contents, key sets, probes) from a
private seed so the held-out `test` split is almost certainly unseen, while keeping the
*structure* identical so dev/test scores are directly comparable as a memorization
probe.

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
