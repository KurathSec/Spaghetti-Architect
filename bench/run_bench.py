#!/usr/bin/env python3
"""Phase 4 — the benchmark runner.

Modes:

* ``--selftest`` — one item per task on one sample with the **mock** model: proves
  the graders and the compact-JSON contract at **zero API spend**. Must pass first.
* ``--dry-run [--task T] [--family F]`` — render the *real* prompts for the real
  dataset, grade the **mock** responses, and write the full
  ``out/subagent/<task>__mock[__<family>].json`` artifacts *in their final shape*.
  No API contact; records are marked ``"model":"mock"``.
* ``--batch <task> --model <id> [--family <fam>]`` — the **live subagent** entry
  point. Generates items, queries the configured model (k samples, temp 0), grades
  locally, writes ``out/subagent/<task>__<model>[__<fam>].json``, echoes one compact
  line. **Refuses while the config is in placeholder state.**
* ``--plan`` — print the exact ``(task, model, family)`` batches implied by the
  config (the subagent fan-out). Needs no key.
* ``--aggregate`` — merge subagent JSON into ``out/results.json`` (+ figure series +
  env block). Never reads bulk source.
* ``--report`` — emit ``paper/benchmark.tex`` (NOT compiled).

This run executes only ``--selftest`` / ``--dry-run`` / ``--plan`` (mock model). No
real model is queried and nothing is spent.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sys
from typing import Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bench import dataset as D  # noqa: E402
from bench import grade as G  # noqa: E402
from bench import models  # noqa: E402
from bench import prompts as P  # noqa: E402
from bench import tasks as T  # noqa: E402
from src.nodes.validator import TOOLCHAINS  # noqa: E402

OUT_DIR = os.path.join(_HERE, "out")
SUBAGENT_DIR = os.path.join(OUT_DIR, "subagent")
RESULTS_PATH = os.path.join(OUT_DIR, "results.json")
PAPER_DIR = os.path.join(_HERE, "paper")
PAPER_TEX = os.path.join(PAPER_DIR, "benchmark.tex")
PY_VERSION = sys.version.split()[0]

# Tasks that fan out per family (they compile/run or grade per instance); judge is
# one batch per model (cheap grading, no compilation).
PER_FAMILY_TASKS = ["refactor", "comprehend"]
GLOBAL_TASKS = ["judge"]


# --------------------------------------------------------------------------- #
# environment / provenance
# --------------------------------------------------------------------------- #
def toolchain_status() -> Dict[str, bool]:
    st = {"python": True}
    for lang, tools in TOOLCHAINS.items():
        if tools is None:
            continue
        st[lang] = all(shutil.which(t) is not None for t in tools)
    return st


def env_block(cfg: models.Config, split: str) -> dict:
    seed = D.SEED if split == "dev" else "PRIVATE (held-out, not stored)"
    return {
        "python_version": PY_VERSION,
        "toolchains": toolchain_status(),
        "network_isolation": bool(G.network_isolation_prefix()),
        "dataset": {"version": D.DATASET_VERSION, "split": split, "seed": seed},
        "prompt_version": P.PROMPT_VERSION,
        "sampling": {"k_samples": cfg.k_samples, "temperature": cfg.temperature,
                     "max_tokens": cfg.max_tokens},
        "models_under_test": cfg.models_under_test,
        "provider": cfg.provider,
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "note": "Static metrics & sample generation are byte-deterministic; LLM "
                "outputs are NOT — results are protocol-reproducible (temp 0 + k + CIs).",
    }


# --------------------------------------------------------------------------- #
# batch naming + headline aggregation
# --------------------------------------------------------------------------- #
def subagent_name(task: str, model: str, family: Optional[str]) -> str:
    safe_model = model.replace("/", "-")
    return f"{task}__{safe_model}" + (f"__{family}" if family else "") + ".json"


def subagent_path(task: str, model: str, family: Optional[str]) -> str:
    return os.path.join(SUBAGENT_DIR, subagent_name(task, model, family))


def _means(items: List[dict], key: str) -> List[float]:
    return [it[key] for it in items if it.get(key) is not None]


def headline_aggregate(task: str, items: List[dict]) -> dict:
    if task == "refactor":
        sem = _means(items, "semantic_ok_rate")
        sq = _means(items, "simplification_quality")
        fm = {b: sum(it.get("failure_modes", {}).get(b, 0) for it in items)
              for b in ("broke_equivalence", "no_change", "over_complicated")}
        return {"n_items": len(items),
                "semantic_ok_rate": G.mean(sem),
                "simplification_quality_mean": G.mean(sq),
                "simplification_quality_ci95": G.ci95_bootstrap(sq),
                "failure_modes": fm}
    if task == "judge":
        mono = _means(items, "monotonicity")
        sens = _means(items, "sensitivity")
        pw = _means(items, "pairwise_acc")
        inv = sum(it.get("inversions", 0) for it in items)
        return {"n_items": len(items),
                "monotonicity_mean": G.mean(mono),
                "monotonicity_ci95": G.ci95_bootstrap(mono),
                "sensitivity_mean": G.mean(sens),
                "pairwise_acc_mean": G.mean(pw),
                "inversions_total": inv}
    if task == "comprehend":
        em = _means(items, "exact_match_rate")
        return {"n_items": len(items),
                "exact_match_rate": G.mean(em),
                "exact_match_ci95": G.ci95_bootstrap(em)}
    raise ValueError(task)


# --------------------------------------------------------------------------- #
# the batch (subagent entry point + dry-run reuse)
# --------------------------------------------------------------------------- #
def run_batch(task: str, model: str, family: Optional[str], split: str = "dev",
              k: Optional[int] = None, write: bool = True, echo: bool = True) -> dict:
    cfg = models.load_config()
    if model != models.MOCK and cfg.is_placeholder:
        print("configure bench/config.json, then re-run", file=sys.stderr)
        raise SystemExit(2)
    if task not in T.TASKS:
        raise SystemExit(f"unknown task {task!r}; choose from {T.TASKS}")
    if k is None:
        k = 1 if model == models.MOCK else cfg.k_samples  # mock is deterministic

    sp = D.load(split)
    fam = None if task in GLOBAL_TASKS else family
    items_in = T.build_items(task, sp, fam)
    records = [T.score_item(task, it, model, cfg, k) for it in items_in]
    agg = headline_aggregate(task, records)

    payload = {
        "task": task, "model": model, "family": fam, "split": split, "k": k,
        "prompt_version": P.PROMPT_VERSION, "env": env_block(cfg, split),
        "aggregate": agg, "items": records,
    }
    if write:
        os.makedirs(SUBAGENT_DIR, exist_ok=True)
        with open(subagent_path(task, model, fam), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
    if echo:
        compact = {"task": task, "model": model, "family": fam, "split": split,
                   "k": k, "n_items": len(records),
                   "toolchains": payload["env"]["toolchains"], "aggregate": agg}
        print(json.dumps(compact, separators=(",", ":")))
    return payload


# --------------------------------------------------------------------------- #
# --plan (the subagent fan-out implied by the config)
# --------------------------------------------------------------------------- #
def plan(split: str = "dev") -> List[dict]:
    cfg = models.load_config()
    sp = D.load(split)
    families = list(sp.meta["batches"])
    batches: List[dict] = []
    for model in cfg.models_under_test:
        for task in GLOBAL_TASKS:
            batches.append({"task": task, "model": model, "family": None})
        for task in PER_FAMILY_TASKS:
            for fam in families:
                batches.append({"task": task, "model": model, "family": fam})
    return batches


def print_plan(split: str = "dev") -> int:
    cfg = models.load_config()
    batches = plan(split)
    state = "PLACEHOLDER (--batch will refuse)" if cfg.is_placeholder else "LIVE-READY"
    print(f"config: {len(cfg.models_under_test)} model(s), k={cfg.k_samples}, "
          f"temp={cfg.temperature}, state={state}")
    print(f"split={split}; {len(batches)} subagent batches "
          f"(one Opus 4.8 general-purpose subagent each):")
    for b in batches:
        fam = f" --family {b['family']}" if b["family"] else ""
        print(f"  python3 bench/run_bench.py --batch {b['task']} "
              f"--model {b['model']}{fam}")
    if cfg.is_placeholder:
        print("\nNOT fired: fill bench/config.json (api_key + models_under_test), then "
              "dispatch the batches above and run --aggregate && --report.")
    return 0


# --------------------------------------------------------------------------- #
# --selftest (mock; prove graders + compact-JSON contract)
# --------------------------------------------------------------------------- #
def selftest() -> int:
    cfg = models.load_config()
    sp = D.load("dev")
    ok = True
    checks: List[str] = []

    # refactor: one Python item (rigorous lane) + one compiled item (compile path)
    refs = T.build_refactor_items(sp, family="allowlist")
    py = next(it for it in refs if it.sample == "allowlist_L8"
              and it.profile == "max" and it.language == "python")
    go = next(it for it in refs if it.sample == "allowlist_L8"
              and it.profile == "max" and it.language == "go")
    rpy = T.score_refactor_item(py, models.MOCK, cfg, k=1)
    rgo = T.score_refactor_item(go, models.MOCK, cfg, k=1)
    c1 = rpy["semantic_ok_rate"] == 1.0 and rpy["simplification_quality"] > 0
    c2 = rgo["semantic_ok_rate"] in (1.0, None)  # PASS, or SKIP if go absent
    checks += [f"refactor/python semantic_ok=1 & simpl>0: {c1}",
               f"refactor/go compile path (PASS or SKIP): {c2}"]
    ok = ok and c1 and c2

    # judge: one Python item -> monotone, perfect pairwise
    ji = next(it for it in T.build_judge_items(sp, family="allowlist")
              if it.sample == "allowlist_L8" and it.language == "python")
    jr = T.score_judge_item(ji, models.MOCK, cfg, k=1)
    c3 = jr["monotonicity"] <= 0.0 and jr["pairwise_acc"] == 1.0
    checks.append(f"judge monotone(<=0) & pairwise=1: {c3}")
    ok = ok and c3

    # comprehend: one item -> exact match
    ci = next(it for it in T.build_comprehend_items(sp, family="allowlist")
              if it.sample == "allowlist_L8" and it.profile == "max"
              and it.language == "python")
    cr = T.score_comprehend_item(ci, models.MOCK, cfg, k=1)
    c4 = cr["exact_match_rate"] == 1.0
    checks.append(f"comprehend exact_match=1: {c4}")
    ok = ok and c4

    # compact-JSON contract: the per-item records must serialize
    try:
        json.dumps({"refactor": rpy, "judge": jr, "comprehend": cr})
        c5 = True
    except Exception:  # noqa: BLE001
        c5 = False
    checks.append(f"compact JSON serializes: {c5}")
    ok = ok and c5

    print(f"toolchains: {toolchain_status()}")
    print(f"network isolation: {bool(G.network_isolation_prefix())}")
    for c in checks:
        print("  " + c)
    print("SELFTEST", "OK" if ok else "FAILED")
    return 0 if ok else 1


# --------------------------------------------------------------------------- #
# --dry-run (mock over the real dataset -> final-shape artifacts)
# --------------------------------------------------------------------------- #
def dry_run(only_task: Optional[str], only_family: Optional[str], split: str = "dev") -> int:
    sp = D.load(split)
    families = list(sp.meta["batches"])
    tasks = [only_task] if only_task else T.TASKS
    n = 0
    for task in tasks:
        if task in GLOBAL_TASKS:
            fams = [None]
        else:
            fams = [only_family] if only_family else families
        for fam in fams:
            payload = run_batch(task, models.MOCK, fam, split=split, k=1,
                                write=True, echo=False)
            n += 1
            agg = payload["aggregate"]
            head = next(iter(agg)) if agg else "?"
            print(f"  wrote {subagent_name(task, models.MOCK, fam):42s} "
                  f"items={agg.get('n_items')}  ({_one_liner(task, agg)})")
    print(f"dry-run complete: {n} artifacts in {SUBAGENT_DIR} (model=mock, split={split})")
    return 0


def _one_liner(task: str, agg: dict) -> str:
    if task == "refactor":
        return (f"sem_ok={agg['semantic_ok_rate']:.2f} "
                f"simpl_q={agg['simplification_quality_mean']:.2f}")
    if task == "judge":
        return (f"mono={agg['monotonicity_mean']:.2f} "
                f"pairwise={agg['pairwise_acc_mean']:.2f}")
    return f"exact_match={agg['exact_match_rate']:.2f}"


# --------------------------------------------------------------------------- #
# --aggregate (merge subagent JSON -> results.json + figure series)
# --------------------------------------------------------------------------- #
def _load_subagents() -> List[dict]:
    if not os.path.isdir(SUBAGENT_DIR):
        return []
    payloads = []
    for fn in sorted(os.listdir(SUBAGENT_DIR)):
        if fn.endswith(".json"):
            with open(os.path.join(SUBAGENT_DIR, fn), encoding="utf-8") as f:
                payloads.append(json.load(f))
    return payloads


def _score_of(task: str, item: dict) -> Optional[float]:
    if task == "refactor":
        return item.get("simplification_quality")
    if task == "comprehend":
        return item.get("exact_match_rate")
    if task == "judge":
        return item.get("pairwise_acc")
    return None


def _grouped_mean(rows: List[tuple]) -> Dict:
    """rows: (key, value) -> {key: mean(values)} ignoring None."""
    acc: Dict[object, List[float]] = {}
    for k, v in rows:
        if v is not None:
            acc.setdefault(k, []).append(v)
    return {k: G.mean(v) for k, v in acc.items()}


def build_figures(payloads: List[dict]) -> dict:
    """The six figure series, computed from the per-item records (never source)."""
    # index items by (task, model, split)
    by_tms: Dict[tuple, List[dict]] = {}
    models_seen, splits_seen = set(), set()
    for pl in payloads:
        key = (pl["task"], pl["model"], pl["split"])
        by_tms.setdefault(key, []).extend(pl["items"])
        models_seen.add(pl["model"])
        splits_seen.add(pl["split"])

    figures: dict = {"models": sorted(models_seen), "splits": sorted(splits_seen)}

    # Fig 1 — incidental degradation: score vs profile, fixed logic (dev split)
    fig1: Dict[str, Dict[str, Dict[str, float]]] = {}
    for (task, model, split), items in by_tms.items():
        if split != "dev" or task == "judge":
            continue
        rows = [(it["profile"], _score_of(task, it)) for it in items if "profile" in it]
        fig1.setdefault(model, {})[task] = _grouped_mean(rows)
    figures["incidental_degradation"] = fig1

    # Fig 2 — intrinsic vs incidental decomposition (comprehend, families w/ a knob)
    fig2: Dict[str, dict] = {}
    for (task, model, split), items in by_tms.items():
        if split != "dev" or task != "comprehend":
            continue
        fam_rows: Dict[str, List[dict]] = {}
        for it in items:
            intr = it.get("intrinsic", {})
            knob = next((k for k in ("N", "L") if k in intr), None)
            if knob:
                fam_rows.setdefault(knob, []).append(it)
        dec = {}
        for knob, its in fam_rows.items():
            # incidental: score@max - score@minimal at the largest scale, fixed logic
            scales = sorted({it["intrinsic"][knob] for it in its})
            lo_s, hi_s = scales[0], scales[-1]
            def at(scale, prof):
                vs = [_score_of(task, it) for it in its
                      if it["intrinsic"][knob] == scale and it["profile"] == prof]
                return G.mean([v for v in vs if v is not None])
            dec[knob] = {
                "scales": scales,
                "delta_incidental": at(hi_s, "max") - at(hi_s, "minimal"),
                "delta_intrinsic": at(hi_s, "max") - at(lo_s, "max"),
                "surface": {str(s): {p: at(s, p) for p in T.PROFILES} for s in scales},
            }
        fig2.setdefault(model, {}).update(dec)
    figures["decomposition"] = fig2

    # Fig 3 — refactor 2-D: (semantic_ok_rate, simplification_quality) per model
    fig3: Dict[str, dict] = {}
    for (task, model, split), items in by_tms.items():
        if split != "dev" or task != "refactor":
            continue
        sem = G.mean([it["semantic_ok_rate"] for it in items
                      if it.get("semantic_ok_rate") is not None])
        sq = G.mean([it["simplification_quality"] for it in items
                     if it.get("simplification_quality") is not None])
        fig3[model] = {"semantic_ok_rate": sem, "simplification_quality": sq}
    figures["refactor_2d"] = fig3

    # Fig 4 — judge calibration: monotonicity per model (+ per language)
    fig4: Dict[str, dict] = {}
    for (task, model, split), items in by_tms.items():
        if split != "dev" or task != "judge":
            continue
        mono = G.mean([it["monotonicity"] for it in items
                       if it.get("monotonicity") is not None])
        by_lang = _grouped_mean([(it["language"], it.get("monotonicity")) for it in items])
        fig4[model] = {"monotonicity_mean": mono, "by_language": by_lang,
                       "pairwise_acc": G.mean([it["pairwise_acc"] for it in items])}
    figures["judge_calibration"] = fig4

    # Fig 5 — contamination control: dev vs test score per (model, task)
    fig5: Dict[str, dict] = {}
    for (task, model, split), items in by_tms.items():
        sc = G.mean([s for s in (_score_of(task, it) for it in items) if s is not None])
        fig5.setdefault(model, {}).setdefault(task, {})[split] = sc
    figures["contamination"] = fig5

    # Fig 6 — cross-language: score per language per (model, task)
    fig6: Dict[str, dict] = {}
    for (task, model, split), items in by_tms.items():
        if split != "dev":
            continue
        rows = [(it["language"], _score_of(task, it)) for it in items if "language" in it]
        fig6.setdefault(model, {})[task] = _grouped_mean(rows)
    figures["cross_language"] = fig6

    return figures


def aggregate() -> int:
    payloads = _load_subagents()
    if not payloads:
        print(f"no subagent artifacts in {SUBAGENT_DIR}; run --dry-run or dispatch "
              f"--batch first", file=sys.stderr)
        return 1

    per_model: Dict[str, dict] = {}
    for pl in payloads:
        per_model.setdefault(pl["model"], {}).setdefault(pl["task"], {})[
            pl.get("family") or "_all"] = pl["aggregate"]

    figures = build_figures(payloads)
    any_env = payloads[0]["env"]
    live = any(pl["model"] != models.MOCK for pl in payloads)

    record = {
        "env": any_env,
        "status": "LIVE" if live else "DRY-RUN (mock model; AWAITING LIVE RUN)",
        "models": sorted({pl["model"] for pl in payloads}),
        "splits": sorted({pl["split"] for pl in payloads}),
        "n_subagent_files": len(payloads),
        "per_model": per_model,
        "figures": figures,
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
        f.write("\n")
    print(f"aggregated {len(payloads)} subagent files -> {RESULTS_PATH}")
    print(f"status: {record['status']}; models: {record['models']}; "
          f"splits: {record['splits']}")
    return 0


# --------------------------------------------------------------------------- #
# --report (emit paper/benchmark.tex; NEVER compiled)
# --------------------------------------------------------------------------- #
_PAPER_TEMPLATE = r"""% Spaghetti Architect benchmark paper. Emitted by bench/run_bench.py --report.
% NOT compiled by the harness. Bibliography: refs.bib (copied from Phase 5).
\documentclass[11pt]{article}
\usepackage[margin=1in]{geometry}
\usepackage{amsmath,amssymb}
\usepackage{booktabs}
\usepackage{tikz}
\usepackage{pgfplots}
\pgfplotsset{compat=1.18}
\usepackage{listings}
\lstset{basicstyle=\ttfamily\footnotesize,breaklines=true,columns=fullflexible,
 frame=single,framesep=2pt,aboveskip=3pt,belowskip=2pt}
\usepackage[numbers,sort&compress]{natbib}
\usepackage{hyperref}
\newcommand{\awaiting}[1]{\begin{center}\fbox{\parbox{0.82\textwidth}{\centering
 \textbf{[AWAITING LIVE RUN]}\\[3pt]\small #1}}\end{center}}

\title{Spaghetti Architect: A Ground-Truth, Contamination-Resistant Benchmark for
 LLM Code Refactoring, Quality Judgment, and Comprehension}
\author{Placeholder Author\\\texttt{placeholder@affiliation}}
\date{}

\begin{document}
\maketitle

\begin{abstract}
We present a benchmark generator for evaluating large language models on code, built
on \emph{Spaghetti Architect}, a correctness-preserving ``anti-optimization''
transpiler that compiles a clean JSON intermediate representation (IR) into
deliberately awful but provably-equivalent code in five languages (Python,
JavaScript, Go, Java, C++), gated by an oracle validator. Because every instance
carries an exact semantic label \emph{by construction}, we obtain ground truth for
three tasks where hand-collected corpora cannot: (i) \textbf{refactoring},
auto-graded for semantic equivalence by the validator and for simplification against
a \emph{known-optimal} clean baseline; (ii) \textbf{quality judgment}
(LLM-as-judge), tested for monotonicity and sensitivity to ground-truth-controlled
degradation; and (iii) \textbf{comprehension}, output prediction defeated neither by
guessing (differential variants) nor by memorization (fresh instances). The
benchmark exposes two \emph{orthogonal} difficulty axes --- \emph{intrinsic}
complexity (problem size: cascade arms $N$, list length $L$, operation count) and
\emph{incidental} complexity (a \texttt{minimal}$\to$\texttt{standard}$\to$\texttt{max}
spaghetti profile that changes presentation at \textbf{zero} semantic cost) --- so we
can ask a question no fixed corpus can: do models fail because the problem got
bigger, or because the presentation got messier? Instances are minted fresh from a
held-out seed for contamination resistance. This paper documents the design and the
reusable harness; model results populate from a live run (status: \textbf{@@STATUS@@}).
\end{abstract}

\section{Introduction}
An optimizing compiler holds semantics fixed and minimizes a cost. Spaghetti
Architect is its mirror image: it holds semantics fixed and \emph{maximizes}
incidental complexity, composing eleven named anti-patterns under a strength profile.
That inversion is useful precisely because it yields \textbf{labels}: the output is
correct by construction (the validator compiles and runs all five targets against a
reference oracle), and the clean IR is a \emph{known-optimal} reference. This is the
``Csmith-for-badness'' framing --- where Csmith~\citep{yang2011csmith} mints random
\emph{valid} programs to stress a compiler, we mint semantically-labelled \emph{bad}
programs with a tunable degradation knob to stress a \emph{model}.

Hand-collected bad-code corpora lack three things this construction provides: exact
semantic labels, independently tunable difficulty knobs, and contamination control.
The central scientific move is to separate two axes that are conflated everywhere
else. \emph{Intrinsic} complexity grows the logic (more cascade arms, longer lists,
more operations). \emph{Incidental} complexity grows the mess at \emph{identical}
semantics (the profile). Varying them independently decomposes a model's failure into
``the problem got harder'' versus ``the presentation got worse'' --- the latter being
a robustness property that matters in the wild and that no benchmark we know of
isolates.

\section{Related Work}
\paragraph{Obfuscation vs.\ anti-optimization.} The eleven \texttt{SPAGH\_*}
transforms descend from the Collberg--Thomborson taxonomy~\citep{collberg1997taxonomy}
--- opaque predicates are theirs~\citep{collberg1998manufacturing} --- and tools like
Tigress~\citep{collberg2015tigress}, OLLVM~\citep{junod2015ollvm}, and
\texttt{javascript-obfuscator}~\citep{kachalov2016jsobfuscator} apply structurally
similar moves. But their objective is reverse-engineering resistance, and they emit
neither a quality label nor a known-optimal target; we reuse the primitives as a
\emph{maintainability-degradation knob with ground truth}.
\paragraph{Mutation testing.} Reviewers will raise the analogy, so we draw the line
sharply: mutation testing~\citep{demillo1978hints,jia2011mutation} \emph{injects faults}
(semantics change, kill-labels follow), whereas we \emph{preserve} semantics and label
the structural mess --- the sign-flipped problem.
\paragraph{Program generators.} Csmith~\citep{yang2011csmith} and
YARPGen~\citep{livinskii2020yarpgen} are the closest siblings: they mint random
\emph{valid} programs to stress a compiler via differential testing. We extend that idea
with explicit per-instance labels, a clean-baseline \emph{optimum}, and an orthogonal
\emph{incidental} knob that they do not have.
\paragraph{Benchmarks, judges, contamination.} Correctness
benches~\citep{chen2021humaneval,austin2021mbpp} and reasoning/refactor
suites~\citep{gu2024cruxeval,jimenez2024swebench,gautam2025refactorbench} grade
correctness but fix presentation and (mostly) cannot regenerate; LLM-as-judge
work~\citep{zheng2023judging} and maintainability-metric
critiques~\citep{vandeursen2014maintainability} lack a controlled stimulus; and the
contamination literature~\citep{jacovi2023contamination,ravaut2024contamination,jain2025livecodebench}
motivates fresh-minted instances but does not supply orthogonal complexity knobs. Our
contribution is to combine all of these properties at once.
\begin{table}[t]\centering\footnotesize
\begin{tabular}{lcccccc}
\toprule
Approach & GT label & Intrinsic & Incidental & Auto-equiv. & Contam. & Multi-lang \\
\midrule
\textbf{Spaghetti Architect (ours)} & \checkmark & \checkmark & \checkmark & \checkmark & \checkmark & \checkmark \\
Obfuscators~\citep{collberg1997taxonomy,collberg2015tigress,junod2015ollvm,kachalov2016jsobfuscator}
 & $\times$ & $\circ$ & $\circ$ & $\circ$ & $\times$ & $\circ$ \\
Mutation testing~\citep{demillo1978hints,jia2011mutation}
 & \checkmark & --- & --- & inv. & $\times$ & $\circ$ \\
Program generators~\citep{yang2011csmith,livinskii2020yarpgen}
 & $\circ$ & \checkmark & $\times$ & \checkmark & \checkmark & $\circ$ \\
Correctness benches~\citep{chen2021humaneval,austin2021mbpp}
 & \checkmark & $\times$ & $\times$ & \checkmark & $\times$ & $\times$ \\
Reasoning/refactor~\citep{gu2024cruxeval,jimenez2024swebench,gautam2025refactorbench}
 & \checkmark & $\circ$ & $\times$ & \checkmark & $\circ$ & $\circ$ \\
Judge / metric validity~\citep{zheng2023judging,vandeursen2014maintainability}
 & $\times$ & $\times$ & $\times$ & $\times$ & --- & --- \\
Contamination~\citep{jacovi2023contamination,ravaut2024contamination,jain2025livecodebench}
 & varies & $\times$ & $\times$ & varies & \checkmark & $\circ$ \\
\bottomrule
\end{tabular}
\caption{Positioning ($\checkmark$ yes, $\circ$ partial, $\times$ no, inv.\ inverted).
No neighbor offers ground-truth labels \emph{and} orthogonal intrinsic/incidental
knobs \emph{and} auto-checkable equivalence \emph{and} contamination resistance
across five languages from one IR.}
\end{table}

\section{The Generator}
Spaghetti Architect is a four-stage pipeline (parser $\to$ planner $\to$ five
language backends $\to$ validator); see the project's \texttt{architecture.md}. The
IR supports two composable operations, \texttt{MEMBERSHIP\_CHECK} ($r = t \in c$) and
\texttt{KEY\_VALUE\_LOOKUP} ($r = \text{pairs}.\text{get}(k,d)$), chained into
programs. A data-driven planner selects eleven \texttt{SPAGH\_*} transforms by
profile; each backend lowers them idiomatically (only C++ emits genuine pointer
arithmetic for the manual-index pattern). \textbf{Correctness by construction} is the
property that yields labels: a \texttt{CodeEmitter} makes valid syntax structural,
always-on safety (try/catch + guards + a preset fallback) makes the output crash-free,
and the validator compiles and runs every target, comparing JSON stdout to the oracle
(missing toolchains \textsc{skip}, never silently pass). The benchmark reuses this
pipeline verbatim --- \texttt{Engine.generate}, \texttt{oracle}, \texttt{validate},
and the metric library --- adding no second generator or grader.

\section{Benchmark Design}
\paragraph{Axes.} We cross \emph{incidental} profile $\in\{$\texttt{minimal},
\texttt{standard}, \texttt{max}$\}$ (plus \texttt{clean} as the zero point) $\times$
\emph{intrinsic} scale (the \texttt{config\_resolver} $N\in\{4,8,16,32\}$ and
\texttt{allowlist} $L\in\{8,32,128\}$ families, plus \texttt{status\_router},
\texttt{discovery\_pipeline}, \texttt{fsm\_transition}) $\times$ language $\in$ five
backends $\times$ input variant ($\texttt{*\_v0..v4}$, exercising early/late cascade
arms, present/absent membership, default-misses). The public \texttt{dev} split has
@@NDEV@@ instances (@@NBASE@@ bases + variants), seed \texttt{@@SEED@@}.

\paragraph{Contamination control.} A private \texttt{test} split is minted from an
uncommitted seed (\texttt{BENCH\_HELDOUT\_SEED}): it re-draws every RNG-sourced
numeric literal and applies a structure-preserving \emph{string salt} to every string
literal, so instances are novel across all families while staying structurally
identical (every minted IR is re-validated by the parser). The held-out seed is never
stored; we report the \texttt{dev}$-$\texttt{test} score gap as the memorization probe.

\paragraph{Tasks and grading (reusing the validator + metrics).}
\emph{Refactor:} the model rewrites a rendered spaghetti source; we grade semantic
equivalence by running its output \textbf{untrusted} (model Python in a subprocess
with \texttt{-I} isolation, a wall-clock timeout, and @@NETISO@@; the other four
languages via the project validator's compile/run path) and comparing to the oracle.
Simplification is scored only if equivalent: $\text{recovery}(m)=\mathrm{clamp}\!\big(
(m_{\text{spag}}-m_{\text{model}})/(m_{\text{spag}}-m_{\text{clean}}),-1,1\big)$ over a
rigorous Python-AST lane (cyclomatic, AST size, Halstead effort) and an agnostic proxy
lane for the other languages, and $\text{simpl\_q}=\text{semantic\_ok}\cdot
\mathrm{geomean}(\text{positive recoveries})$.
\emph{Judge:} pointwise ratings across the incidental knob give monotonicity (Spearman
$\rho$, expected strongly negative) and sensitivity; pairwise comparisons give accuracy
versus the knob order.
\emph{Comprehend:} predict the result variables; exact-match versus the oracle over
base $+$ variants. Matrix sizes (dev): refactor @@NREFAC@@, comprehend @@NCOMP@@, judge
@@NJUDGE@@ items.

\paragraph{Protocol and determinism.} Sample generation and all static metrics are
byte-deterministic (seeded). LLM outputs are \emph{not}: we pin
\texttt{temperature=@@TEMP@@}, record the exact model id and parameters, draw $k=$@@K@@
samples per item, and report mean with bootstrap 95\% CIs. We claim \emph{protocol}
reproducibility, not byte reproducibility, for model results. Prompts are frozen and
versioned (\texttt{@@PROMPTVER@@}); each returns a single parseable artifact (a code
block / an integer / \texttt{A}|\texttt{B} / a JSON object) so no reasoning leaks into
the graded output.

\section{Results}
\emph{Status: \textbf{@@STATUS@@}.} Model figures populate from \texttt{out/results.json}
after a live run; until then each is a wired placeholder.

\begin{figure}[h]\centering @@FIG1@@
\caption{\textbf{Incidental-complexity degradation.} Task score vs.\ profile with logic
held fixed --- the headline: does mess alone hurt?}\end{figure}

\begin{figure}[h]\centering @@FIG2@@
\caption{\textbf{Intrinsic vs.\ incidental decomposition.} $\Delta$score from growing
$N/L$ (fixed profile) vs.\ from growing the profile (fixed logic) --- the scientific
core.}\end{figure}

\begin{figure}[h]\centering @@FIG3@@
\caption{\textbf{Refactor 2-D.} Semantic-ok rate vs.\ simplification quality per model;
the clean baseline is the $(1,1)$ upper bound.}\end{figure}

\begin{figure}[h]\centering @@FIG4@@
\caption{\textbf{Judge calibration.} Rating-vs-knob monotonicity per model, with the
non-LLM radon anchor (\S\ref{sec:cv}) as the external reference.}\end{figure}

\begin{figure}[h]\centering @@FIG5@@
\caption{\textbf{Contamination control.} Public \texttt{dev} vs.\ private \texttt{test}
(fresh seed); a gap flags memorization.}\end{figure}

\begin{figure}[h]\centering @@FIG6@@
\caption{\textbf{Cross-language.} Task score per language (same logic), exposing harder
backends.}\end{figure}

\section{Construct Validity}\label{sec:cv}
To anchor the bespoke metrics and the judge to an \emph{external} reference, we score
the \texttt{dev} Python sources with \texttt{radon} (status: \textbf{@@ANCHORSTATUS@@},
v\,@@RADONVER@@), a standard non-LLM complexity tool, off the zero-dependency core (a
quarantined virtualenv). Two findings (this is real, model-independent data):
\textbf{(1) the ground-truth knob moves an independent metric monotonically} ---
Spearman of the incidental rank against radon cyclomatic is $\rho=@@ANCINCCC@@$
(more mess, more complexity) and against radon maintainability $\rho=@@ANCINCMI@@$
(more mess, lower maintainability), with the intrinsic $N$ knob giving
$\rho=@@ANCNCC@@$ against radon CC; and \textbf{(2) our lane is anchored to the
external one} --- our cyclomatic vs.\ radon CC $\rho=@@ANCXCC@@$ and our
maintainability vs.\ radon MI $\rho=@@ANCXMI@@$ across all sources. The same radon MI
is the reference against which Figure~4 calibrates LLM judges, so a judge is measured
against an external metric, not only against other judges.

\section{Threats to Validity}
\paragraph{Construct.} The IR has two operations: this is a deliberate \emph{controlled
stimulus}, not a claim of generality --- it is exactly what lets us hold semantics fixed
while varying mess. Equivalence is established by differential testing over base $+$
variants, not by proof; the rigorous simplification lane is Python-only (the other four
use a text/regex proxy with a language-neutral clean floor).
\paragraph{Conclusion.} LLM outputs are nondeterministic; we mitigate with
\texttt{temperature=0}, $k$ samples, and CIs, and never claim byte reproducibility for
model results. Prompt sensitivity is a known risk; prompts are frozen and versioned so
the protocol is at least reproducible.
\paragraph{External.} The default models-under-test are one vendor's capability
gradient (a result in itself); the client has a hook for an external provider. A missing
toolchain \textsc{skip}s rather than failing, and is recorded, never counted as a pass.

\section{Conclusion}
By treating an anti-optimizer as a labelled benchmark generator, we obtain ground truth,
two orthogonal difficulty knobs, auto-checkable equivalence, and contamination
resistance --- together, not separately --- across five languages from one IR. The
orthogonal decomposition turns ``the model got it wrong'' into the sharper ``the model
is not robust to incidental complexity,'' which is the property the benchmark is built
to measure.

\appendix
\section{Exact commands}
\begin{lstlisting}
python3 bench/dataset.py --freeze         # public dev split + manifest
python3 bench/run_bench.py --selftest     # mock plumbing (zero spend)
python3 bench/run_bench.py --dry-run      # mock over the real dataset
python3 bench/run_bench.py --plan         # the (task x model x family) fan-out
# go live (after filling bench/config.json): one Opus 4.8 subagent per batch
python3 bench/run_bench.py --batch refactor   --model <id> --family <fam>
python3 bench/run_bench.py --batch comprehend --model <id> --family <fam>
python3 bench/run_bench.py --batch judge      --model <id>
python3 bench/run_bench.py --aggregate && python3 bench/run_bench.py --report
\end{lstlisting}

\section{Toolchains and held-out protocol}
\begin{table}[h]\centering\begin{tabular}{ll}\toprule tool & availability \\\midrule
@@TOOLROWS@@ \bottomrule\end{tabular}\caption{Validation toolchains on the run host.}
\end{table}
The held-out \texttt{test} split is minted from \texttt{BENCH\_HELDOUT\_SEED} (an
environment variable, never committed) by re-drawing numeric literals and salting
string literals; only the public \texttt{dev} split and the generator are committed.

\bibliographystyle{plainnat}
\bibliography{refs}
\end{document}
"""


def _is_live(results: Optional[dict]) -> bool:
    return bool(results) and str(results.get("status", "")).startswith("LIVE")


def _awaiting(hint: str) -> str:
    return (r"\awaiting{" + hint + r"}")


def _anchor_facts() -> dict:
    path = os.path.join(OUT_DIR, "anchor.json")
    if not os.path.exists(path):
        return {"status": "ABSENT"}
    return json.load(open(path, encoding="utf-8"))


# --- six figure emitters: real pgfplots when live, else AWAITING placeholder ----
def _fig_incidental(results, live):
    hint = ("Task score vs.\\ incidental profile (minimal$\\to$max) at fixed logic, "
            "one line per model. Populates from \\texttt{figures.incidental\\_degradation}.")
    if not live:
        return _awaiting(hint)
    fig = results["figures"]["incidental_degradation"]
    xs = {"minimal": 1, "standard": 2, "max": 3}
    plots = []
    for model, tasks_ in sorted(fig.items()):
        for task, series in sorted(tasks_.items()):
            coords = " ".join(f"({xs[p]},{series[p]:.4f})" for p in xs if p in series)
            plots.append(f"\\addplot coordinates {{{coords}}};"
                         f"\\addlegendentry{{{_texesc(model)}/{task}}}")
    return ("\\begin{tikzpicture}\\begin{axis}[width=0.7\\textwidth,height=5cm,"
            "xtick={1,2,3},xticklabels={minimal,standard,max},xlabel={incidental profile},"
            "ylabel={task score},ymin=0,ymax=1,legend pos=south west,grid=major]\n"
            + "\n".join(plots) + "\n\\end{axis}\\end{tikzpicture}")


def _fig_decomposition(results, live):
    hint = ("$\\Delta$score from growing $N/L$ (fixed profile) vs.\\ growing profile "
            "(fixed logic). Populates from \\texttt{figures.decomposition}.")
    if not live:
        return _awaiting(hint)
    fig = results["figures"]["decomposition"]
    rows = []
    for model, knobs in sorted(fig.items()):
        for knob, d in sorted(knobs.items()):
            rows.append(f"{_texesc(model)} & {knob} & {d['delta_intrinsic']:+.3f} & "
                        f"{d['delta_incidental']:+.3f} \\\\")
    return ("\\begin{tabular}{llrr}\\toprule model & knob & $\\Delta$intrinsic & "
            "$\\Delta$incidental \\\\\\midrule\n" + "\n".join(rows)
            + "\n\\bottomrule\\end{tabular}")


def _fig_refactor2d(results, live):
    hint = ("Per model: semantic-ok rate ($x$) vs.\\ simplification quality ($y$); "
            "clean baseline $=(1,1)$, no-op $=(*,0)$. From \\texttt{figures.refactor\\_2d}.")
    if not live:
        return _awaiting(hint)
    fig = results["figures"]["refactor_2d"]
    pts = "\n".join(
        f"\\node[circle,fill=blue!60,inner sep=2pt,label=right:{{{_texesc(m)}}}] at "
        f"({d['semantic_ok_rate']:.3f},{d['simplification_quality']:.3f}) {{}};"
        for m, d in sorted(fig.items()))
    return ("\\begin{tikzpicture}[x=8cm,y=6cm]\n"
            "\\draw[->] (0,0)--(1.05,0) node[right]{semantic-ok};"
            "\\draw[->] (0,0)--(0,1.05) node[above]{simplification};"
            "\\node[star,fill=green!60,inner sep=2pt,label=above:{clean}] at (1,1) {};\n"
            + pts + "\n\\end{tikzpicture}")


def _fig_judge(results, live):
    hint = ("Judge monotonicity (Spearman $\\rho$ of rating vs.\\ knob) per model, with "
            "the radon anchor overlaid. From \\texttt{figures.judge\\_calibration} + anchor.")
    if not live:
        return _awaiting(hint)
    fig = results["figures"]["judge_calibration"]
    rows = "\n".join(f"{_texesc(m)} & {d['monotonicity_mean']:+.3f} & {d['pairwise_acc']:.3f} \\\\"
                     for m, d in sorted(fig.items()))
    return ("\\begin{tabular}{lrr}\\toprule model & monotonicity $\\rho$ & pairwise acc \\\\"
            "\\midrule\n" + rows + "\n\\bottomrule\\end{tabular}")


def _fig_contamination(results, live):
    hint = ("Public \\texttt{dev} vs.\\ private \\texttt{test} (fresh seed) score per model; "
            "a gap flags memorization, $\\approx 0$ supports the anti-contamination claim. "
            "Requires a \\texttt{--split test} run. From \\texttt{figures.contamination}.")
    if not live:
        return _awaiting(hint)
    fig = results["figures"]["contamination"]
    rows = []
    for model, tasks_ in sorted(fig.items()):
        for task, sd in sorted(tasks_.items()):
            dev, test = sd.get("dev"), sd.get("test")
            gap = (f"{dev - test:+.3f}" if dev is not None and test is not None else "---")
            rows.append(f"{_texesc(model)} & {task} & "
                        f"{dev if dev is None else f'{dev:.3f}'} & "
                        f"{test if test is None else f'{test:.3f}'} & {gap} \\\\")
    return ("\\begin{tabular}{llrrr}\\toprule model & task & dev & test & gap \\\\\\midrule\n"
            + "\n".join(rows) + "\n\\bottomrule\\end{tabular}")


def _fig_crosslang(results, live):
    hint = ("Task score per language (same logic) — where a backend is harder for models. "
            "From \\texttt{figures.cross\\_language}.")
    if not live:
        return _awaiting(hint)
    fig = results["figures"]["cross_language"]
    langs = D.LANGS
    rows = []
    for model, tasks_ in sorted(fig.items()):
        for task, sd in sorted(tasks_.items()):
            cells = " & ".join(f"{sd[l]:.3f}" if l in sd else "---" for l in langs)
            rows.append(f"{_texesc(model)} & {task} & {cells} \\\\")
    head = " & ".join(langs)
    return ("\\begin{tabular}{ll" + "r" * len(langs) + "}\\toprule model & task & "
            + head + " \\\\\\midrule\n" + "\n".join(rows) + "\n\\bottomrule\\end{tabular}")


def _texesc(s) -> str:
    return (str(s).replace("\\", r"\textbackslash{}").replace("_", r"\_")
            .replace("%", r"\%").replace("&", r"\&").replace("#", r"\#"))


def _render_paper(results: Optional[dict]) -> str:
    live = _is_live(results)
    cfg = models.load_config()
    anchor = _anchor_facts()
    manifest_path = os.path.join(D.DATA_DIR, "manifest.json")
    manifest = json.load(open(manifest_path, encoding="utf-8")) if os.path.exists(manifest_path) else {}
    ndev = manifest.get("splits", {}).get("dev", {}).get("n_items", len(D.load("dev").items))
    nbase = len([s for s in manifest.get("ground_truth", {}).values()
                 if s.get("variant") == "base"]) or 10

    def anc(key, fmt="{:+.3f}", absent="AWAITING"):
        if anchor.get("status") != "OK":
            return absent
        v = anchor.get("correlations", {}).get(key)
        return absent if v is None else fmt.format(v)

    status = (results or {}).get("status", "AWAITING LIVE RUN (no results.json yet)")
    tool_rows = "\n".join(f"{_texesc(k)} & {'present' if v else 'ABSENT (SKIP)'} \\\\"
                          for k, v in toolchain_status().items())

    repl = {
        "@@STATUS@@": _texesc(status),
        "@@SEED@@": str(D.SEED),
        "@@PYVER@@": _texesc(PY_VERSION),
        "@@K@@": str(cfg.k_samples),
        "@@TEMP@@": str(cfg.temperature),
        "@@PROMPTVER@@": _texesc(P.PROMPT_VERSION),
        "@@NDEV@@": str(ndev),
        "@@NBASE@@": str(nbase),
        "@@NLANG@@": str(len(D.LANGS)),
        "@@NPROF@@": str(len(D.PROFILES)),
        "@@NREFAC@@": str(ndev * len(D.PROFILES) * len(D.LANGS)),
        "@@NCOMP@@": str(ndev * len(D.PROFILES) * len(D.LANGS)),
        "@@NJUDGE@@": str(nbase * len(D.LANGS)),
        "@@NETISO@@": ("a private network namespace (\\texttt{unshare -rn})"
                       if G.network_isolation_prefix()
                       else "\\texttt{-I} isolation + a sanitized environment"),
        "@@ANCHORSTATUS@@": _texesc(anchor.get("status", "ABSENT")),
        "@@RADONVER@@": _texesc(anchor.get("radon_version", "n/a")),
        "@@ANCINCCC@@": anc("incidental_cc_spearman_mean"),
        "@@ANCINCMI@@": anc("incidental_mi_spearman_mean"),
        "@@ANCNCC@@": anc("config_resolver_N_cc_spearman"),
        "@@ANCXCC@@": anc("crosscheck_our_cc_vs_radon_cc_spearman"),
        "@@ANCXMI@@": anc("crosscheck_our_mi_vs_radon_mi_spearman"),
        "@@TOOLROWS@@": tool_rows,
        "@@FIG1@@": _fig_incidental(results, live),
        "@@FIG2@@": _fig_decomposition(results, live),
        "@@FIG3@@": _fig_refactor2d(results, live),
        "@@FIG4@@": _fig_judge(results, live),
        "@@FIG5@@": _fig_contamination(results, live),
        "@@FIG6@@": _fig_crosslang(results, live),
    }
    tex = _PAPER_TEMPLATE
    for k, v in repl.items():
        tex = tex.replace(k, v)
    return tex


def build_tex(results: Optional[dict]) -> str:
    """Render paper/benchmark.tex. Figures/tables populate from ``results`` when a
    live run exists, else show ``AWAITING LIVE RUN`` placeholders. NOT compiled."""
    return _render_paper(results)


def write_report() -> int:
    os.makedirs(PAPER_DIR, exist_ok=True)
    results = (json.load(open(RESULTS_PATH, encoding="utf-8"))
               if os.path.exists(RESULTS_PATH) else None)
    with open(PAPER_TEX, "w", encoding="utf-8") as f:
        f.write(build_tex(results))
    # ship the real Phase-5 bibliography next to the paper so it is self-contained
    src_bib = os.path.join(OUT_DIR, "relatedwork", "refs.bib")
    if os.path.exists(src_bib):
        shutil.copyfile(src_bib, os.path.join(PAPER_DIR, "refs.bib"))
    print(f"wrote {PAPER_TEX} (NOT compiled)")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Spaghetti Architect LLM benchmark runner")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--selftest", action="store_true", help="mock plumbing check")
    g.add_argument("--dry-run", action="store_true", help="mock over the real dataset")
    g.add_argument("--batch", metavar="TASK", help="live subagent entry (refuses if placeholder)")
    g.add_argument("--plan", action="store_true", help="print the subagent fan-out")
    g.add_argument("--aggregate", action="store_true", help="merge subagent JSON -> results.json")
    g.add_argument("--report", action="store_true", help="emit paper/benchmark.tex (not compiled)")
    ap.add_argument("--model", help="model id for --batch")
    ap.add_argument("--family", help="family for --batch/--dry-run")
    ap.add_argument("--task", help="restrict --dry-run to one task")
    ap.add_argument("--split", default="dev", choices=["dev", "test"], help="dataset split")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.dry_run:
        return dry_run(args.task, args.family, split=args.split)
    if args.plan:
        return print_plan(split=args.split)
    if args.aggregate:
        return aggregate()
    if args.report:
        return write_report()
    if args.batch:
        if not args.model:
            ap.error("--batch requires --model")
        run_batch(args.batch, args.model, args.family, split=args.split)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
