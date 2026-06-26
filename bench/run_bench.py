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
import statistics
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def _dev_n_items() -> Optional[int]:
    path = os.path.join(D.DATA_DIR, "manifest.json")
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8"))["splits"]["dev"]["n_items"]
        except Exception:  # noqa: BLE001
            return None
    return None


def env_block(cfg: models.Config, split: str) -> dict:
    seed = D.SEED if split == "dev" else "PRIVATE (held-out, not stored)"
    return {
        "python_version": PY_VERSION,
        "toolchains": toolchain_status(),
        "network_isolation": bool(G.network_isolation_prefix()),
        "dataset": {"version": D.DATASET_VERSION, "split": split, "seed": seed,
                    "dev_n_items": _dev_n_items(), "canary": D.CANARY_GUID,
                    "novelty_tiers": {"A": "literal re-mint (salt + RNG)",
                                      "B": "structural held-out (private)",
                                      "C": "distribution shift (private)"}},
        "prompt_version": P.PROMPT_VERSION,
        "prompt_set_hash": P.prompt_set_hash(),
        "n_paraphrases": P.N_PARAPHRASES,
        "sampling": {"k_samples": cfg.k_samples, "temperature": cfg.temperature,
                     "max_tokens": cfg.max_tokens},
        "models_under_test": cfg.models_under_test,
        # cross-vendor provenance: each model's provider (single-vendor is no longer
        # assumed). Exact dated snapshot ids are recorded per call and aggregated into
        # results.json["snapshots"].
        "providers": {m: cfg.provider_of(m) for m in cfg.models_under_test},
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


# --------------------------------------------------------------------------- #
# raw-config access (prices / per-task k overrides are OPTIONAL config keys the
# typed models.Config does not carry; read them straight from the JSON, no API)
# --------------------------------------------------------------------------- #
def _raw_config() -> dict:
    src = (models.CONFIG_PATH if os.path.exists(models.CONFIG_PATH)
           else models.CONFIG_EXAMPLE_PATH)
    try:
        return json.load(open(src, encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _prices() -> Dict[str, dict]:
    """Optional per-model price table: ``{"prices": {"<model>": {"per_1k": <usd>,
    "est_tokens_per_call": <int>}}}``. Absent => cost projection prints call counts only."""
    p = _raw_config().get("prices", {})
    return p if isinstance(p, dict) else {}


# --------------------------------------------------------------------------- #
# cost projection — the REAL fan-out, per (task, model, split)
# --------------------------------------------------------------------------- #
def _judge_calls_per_item(n_levels: int, k: int) -> int:
    """Judge fan-out for ONE (sample, language) item: pointwise = levels*k, pairwise =
    C(levels,2) pairs * 2 orders * k. Levels are the DEDUPED distinct renders for that
    item (build_judge_items already collapses byte-identical profiles)."""
    from itertools import combinations
    n_pairs = len(list(combinations(range(n_levels), 2)))
    return n_levels * k + n_pairs * 2 * k


def project_calls(split: str = "dev") -> dict:
    """Projected API CALL count for the whole --plan, from the real fan-out:
    refactor/comprehend = items*k; judge = sum over items of (levels*k + C(levels,2)*2*k).
    Returns per-(task,model) detail + grand total + (if prices present) an est. $ cost."""
    cfg = models.load_config()
    sp = D.load(split)
    families = list(sp.meta["batches"])
    prices = _prices()
    # judge fan-out depends on per-item level counts (deduped); compute once.
    judge_items = T.build_judge_items(sp)
    judge_calls_1k = sum(_judge_calls_per_item(len(it.levels), 1) for it in judge_items)
    # per-family per-instance item counts (one model's worth)
    ref_items = T.build_refactor_items(sp)
    comp_items = T.build_comprehend_items(sp)
    n_ref = len(ref_items)
    n_comp = len(comp_items)

    per: Dict[str, Dict[str, dict]] = {}
    total_calls = 0
    total_cost = 0.0
    have_cost = bool(prices)
    for model in cfg.models_under_test:
        per[model] = {}
        km = resolve_k(cfg, "refactor")
        kc = resolve_k(cfg, "comprehend")
        kj = resolve_k(cfg, "judge")
        rcalls = n_ref * km
        ccalls = n_comp * kc
        jcalls = judge_calls_1k * kj
        for task, calls, kk, nit in (("refactor", rcalls, km, n_ref),
                                     ("comprehend", ccalls, kc, n_comp),
                                     ("judge", jcalls, kj, len(judge_items))):
            entry = {"calls": calls, "k": kk, "n_items": nit}
            pr = prices.get(model)
            if pr:
                tpc = pr.get("est_tokens_per_call", 0)
                usd = calls * tpc / 1000.0 * pr.get("per_1k", 0.0)
                entry["est_usd"] = round(usd, 2)
                total_cost += usd
            else:
                have_cost = False
            per[model][task] = entry
            total_calls += calls
    out = {"split": split, "per_model": per, "total_calls": total_calls,
           "n_families": len(families)}
    if have_cost:
        out["total_est_usd"] = round(total_cost, 2)
    return out


def _means(items: List[dict], key: str) -> List[float]:
    # SORT the extracted values: the seeded bootstrap CI (grade.ci95_bootstrap) indexes
    # into this list with an RNG, so an unsorted, order-varying input would yield a
    # different CI under concurrency. Sorting makes every aggregate (mean, stdev, CI)
    # byte-identical regardless of the order items were graded/written in. The mean and
    # stdev are order-invariant anyway, and no caller pairs two _means lists positionally.
    return sorted(it[key] for it in items if it.get(key) is not None)


def _refactor_headline_score(item: dict) -> Optional[float]:
    """The per-item refactor headline used by the 2-D figure / sweep: Python's
    rigorous AST/radon ``simplification_quality`` on the AST lane, and the uniform
    tool-backed ``uniform_quality`` for the four non-Python (uniform-lane) languages.
    Keeps the cross-language refactor figure populated while the AST-only *aggregate*
    mean stays Python-only. ``None`` only when neither lane scored the item."""
    if item.get("ast_lane", item.get("language") == "python"):
        sq = item.get("simplification_quality")
        if sq is not None:
            return sq
    return item.get("uniform_quality")


def _per_language_simpl(items: List[dict]) -> Dict[str, dict]:
    """Per-language simplification summary for the refactor panel: the
    cross-language-poolable ``uniform_quality`` (lizard+BW, commensurable across all
    five languages) plus the AST headline where present (Python only). Keyed by
    language so the paper can read commensurable per-language numbers directly."""
    by_lang: Dict[str, List[dict]] = {}
    for it in items:
        by_lang.setdefault(it.get("language", "?"), []).append(it)
    out: Dict[str, dict] = {}
    for lang, its in sorted(by_lang.items()):
        uq = _means(its, "uniform_quality")
        sq = _means(its, "simplification_quality")
        out[lang] = {
            "n": len(its),
            "uniform_quality": G.mean(uq) if uq else None,
            "uniform_quality_ci95": G.ci95_bootstrap(uq) if uq else None,
            "simplification_quality_ast": G.mean(sq) if sq else None,
        }
    return out


def headline_aggregate(task: str, items: List[dict]) -> dict:
    if task == "refactor":
        sem = _means(items, "semantic_ok_rate")
        # AST/radon `simplification_quality` is PYTHON-ONLY by design: average it over
        # AST-lane items only (`ast_lane` True, set by grade.aggregate_refactor), so the
        # four uniform-lane languages do not dilute the Python AST headline with 0.0s.
        ast_items = [it for it in items
                     if it.get("ast_lane", it.get("language") == "python")]
        sq = _means(ast_items, "simplification_quality")
        uq = _means(items, "uniform_quality")  # cross-language-poolable lane (all five)
        fm = {b: sum(it.get("failure_modes", {}).get(b, 0) for it in items)
              for b in ("broke_equivalence", "no_change", "over_complicated")}
        return {"n_items": len(items),
                "semantic_ok_rate": G.mean(sem),
                # Python-only AST/radon headline (n = the AST-lane items).
                "simplification_quality_mean": G.mean(sq),
                "simplification_quality_ci95": G.ci95_bootstrap(sq),
                "simplification_quality_n": len(sq),
                # single-methodology cross-language number (lizard CC + tokens + BW
                # density), pooled over all five languages; None if lizard absent.
                "uniform_quality_mean": G.mean(uq) if uq else None,
                "uniform_quality_ci95": G.ci95_bootstrap(uq) if uq else None,
                "failure_modes": fm}
    if task == "judge":
        mono = _means(items, "monotonicity")
        sens = _means(items, "sensitivity")
        pw = _means(items, "pairwise_acc")
        pc = _means(items, "position_consistency")
        inv = sum(it.get("inversions", 0) for it in items)
        return {"n_items": len(items),
                # pairwise (position-swap-controlled) is the PRIMARY judge metric
                "pairwise_acc_mean": G.mean(pw),
                "pairwise_acc_ci95": G.ci95_bootstrap(pw),
                "position_consistency_mean": G.mean(pc),
                # pointwise monotonicity is the secondary (Spearman) view
                "monotonicity_mean": G.mean(mono),
                "monotonicity_ci95": G.ci95_bootstrap(mono),
                "sensitivity_mean": G.mean(sens),
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
# task-k resolution (#8): the DEFAULT k is the pre-registered cfg.k_samples (8); an
# optional per-task override may be set in config["task_k"] or via --k, never silently.
def resolve_k(cfg: models.Config, task: str, k_override: Optional[int] = None) -> int:
    if k_override is not None:
        return k_override
    task_k = _raw_config().get("task_k", {})
    return int(task_k.get(task, cfg.k_samples))


def _checkpoint_path(task: str, model: str, family: Optional[str]) -> str:
    """Per-item JSONL checkpoint next to the subagent file: every completed item is
    appended as one line the instant it finishes, so a crash mid-batch loses nothing and
    a re-run skips the already-written items (keyed by the stable item id)."""
    return subagent_path(task, model, family)[:-len(".json")] + ".partial.jsonl"


def _load_checkpoint(path: str) -> Dict[str, dict]:
    """Read the JSONL checkpoint into {item_id: record}. Tolerates a torn last line (a
    crash mid-write): a line that does not parse is dropped, not fatal."""
    done: Dict[str, dict] = {}
    if not os.path.exists(path):
        return done
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue  # torn final line from a crash; ignore
            iid = rec.get("_item_id")
            if iid:
                done[iid] = rec
    return done


def _strip_internal(rec: dict) -> dict:
    """Drop the run-time-only keys (parse counters + checkpoint id + the Phase-A cheap
    format-parse signal) so the persisted per-item record stays clean; the aggregates/
    figures never read them."""
    return {k: v for k, v in rec.items()
            if k not in ("_parse_ok", "_parse_n", "_item_id",
                         "format_parse_ok", "format_parse_n")}


def _record_item_id(task: str, rec: dict) -> Optional[str]:
    """Re-derive the stable item id of a STORED record (mirrors tasks.item_id, which keys
    on the dataset axes the record carries). Used to detect completion on re-run of a
    finalized batch."""
    if rec.get("error") and "_item_id" not in rec:
        return None
    if "_item_id" in rec:
        return rec["_item_id"]
    if task == "judge":
        return f"judge:{rec.get('sample')}:{rec.get('language')}"
    return (f"{task}:{rec.get('sample')}:{rec.get('variant', '?')}:"
            f"{rec.get('profile')}:{rec.get('language')}")


def _completed_from_subagent(task: str, model: str, family: Optional[str]) -> Dict[str, dict]:
    """A previously FINALIZED subagent JSON re-keyed by stable item id, so re-running a
    fully-completed batch makes ZERO new API calls. Error stubs are NOT treated as
    complete (they are retried on re-run)."""
    path = subagent_path(task, model, family)
    if not os.path.exists(path):
        return {}
    try:
        pl = json.load(open(path, encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    done: Dict[str, dict] = {}
    for rec in pl.get("items", []):
        if rec.get("error"):
            continue
        iid = _record_item_id(task, rec)
        if iid:
            done[iid] = rec
    return done


def _batch_projected_calls(task: str, items_in: list, k: int) -> int:
    """Projected API calls for a SINGLE batch's item list (the cap is enforced against
    this before any call is made)."""
    if task == "judge":
        return sum(_judge_calls_per_item(len(it.levels), k) for it in items_in)
    return len(items_in) * k


def _item_calls(task: str, it, k: int) -> int:
    """API calls a SINGLE item costs (judge fans out over levels + pairs; the others
    are k flat). Used by the thread-safe Phase-A cap counter."""
    if task == "judge":
        return _judge_calls_per_item(len(it.levels), k)
    return k


# concurrency resolution (#1): network fan-out width = config["concurrency"][provider]
# (or ["default"]), overridable by --concurrency. GRADING is bounded separately (#2).
GRADE_WORKERS_CAP = 16


def _net_concurrency(cfg: models.Config, model: str,
                     override: Optional[int]) -> int:
    """Phase-A (network) worker count for THIS batch's single provider. ``--concurrency``
    wins; else config['concurrency'][provider] / ['default']; mock is forced to 1 so the
    deterministic mock path keeps its exact sequential ordering/output."""
    if model == models.MOCK:
        return 1
    if override is not None:
        return max(1, int(override))
    conc = _raw_config().get("concurrency", {})
    if not isinstance(conc, dict):
        return 8
    prov = cfg.provider_of(model)
    return max(1, int(conc.get(prov, conc.get("default", 8))))


def _grade_concurrency() -> int:
    """Phase-B (sandbox-subprocess grading) worker count: bounded by CPU count and a
    hard ceiling so a 2000-wide NETWORK fan-out never becomes 2000 concurrent
    ``unshare -rn`` grader subprocesses (the fork-storm guard)."""
    return min(os.cpu_count() or 4, GRADE_WORKERS_CAP)


def _usage_cost(model: str, usage: dict) -> Optional[float]:
    """ACTUAL $ for a batch from the returned token usage and config['prices'][model].
    Reasoning tokens are billed as completion/output, so prompt+completion already
    includes them; cost = (prompt+completion)/1000 * per_1k. None if no price set."""
    pr = _prices().get(model)
    if not pr:
        return None
    billable = usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
    return round(billable / 1000.0 * pr.get("per_1k", 0.0), 4)


def run_batch(task: str, model: str, family: Optional[str], split: str = "dev",
              k: Optional[int] = None, write: bool = True, echo: bool = True,
              allow_skips: bool = False, parse_floor: float = 0.5,
              resume: bool = True, max_calls: Optional[int] = None,
              max_cost: Optional[float] = None,
              concurrency: Optional[int] = None) -> dict:
    cfg = models.load_config()
    if model != models.MOCK and not cfg.model_is_live(model):
        print(f"configure bench/config.json: no API key for model {model!r} "
              f"(provider {cfg.provider_of(model)!r}), then re-run", file=sys.stderr)
        raise SystemExit(2)
    if task not in T.TASKS:
        raise SystemExit(f"unknown task {task!r}; choose from {T.TASKS}")
    if k is None:
        k = 1 if model == models.MOCK else resolve_k(cfg, task)  # mock is deterministic

    sp = D.load(split)
    fam = None if task in GLOBAL_TASKS else family
    items_in = T.build_items(task, sp, fam)

    # #4 hard cap at the batch level: REFUSE to start a batch that would exceed the cap
    # (the mock makes 0 paid calls, so it is exempt).
    if model != models.MOCK and (max_calls is not None or max_cost is not None):
        bcalls = _batch_projected_calls(task, items_in, k)
        if max_calls is not None and bcalls > max_calls:
            print(f"REFUSED {task}/{model}: batch would make {bcalls:,} calls > "
                  f"--max-calls {max_calls:,}. Restrict --family or lower --k.",
                  file=sys.stderr)
            raise SystemExit(4)
        pr = _prices().get(model)
        if max_cost is not None:
            if not pr:
                print(f"REFUSED {task}/{model}: --max-cost set but no price for "
                      f"{model!r} in config['prices'].", file=sys.stderr)
                raise SystemExit(4)
            usd = bcalls * pr.get("est_tokens_per_call", 0) / 1000.0 * pr.get("per_1k", 0)
            if usd > max_cost:
                print(f"REFUSED {task}/{model}: batch est ${usd:,.2f} > --max-cost "
                      f"${max_cost:,.2f}.", file=sys.stderr)
                raise SystemExit(4)

    # #6 toolchain pre-flight gate: refuse to PAY then SKIP. refactor/comprehend run the
    # model's code in the per-item languages; if a required toolchain is absent, abort
    # (unless --allow-skips). The mock and judge (no compilation) are exempt.
    if model != models.MOCK and task in PER_FAMILY_TASKS and not allow_skips:
        needed = sorted({it.language for it in items_in})
        st = toolchain_status()
        missing = [lang for lang in needed if not st.get(lang, lang == "python")]
        if missing:
            print(f"REFUSING {task}/{model}: required toolchain(s) absent: {missing} "
                  f"(present: {sorted(k for k, v in st.items() if v)}). A paid batch must "
                  f"not run then SKIP. Install them, restrict --family, or pass "
                  f"--allow-skips to score only the runnable languages.", file=sys.stderr)
            raise SystemExit(3)

    cp_path = _checkpoint_path(task, model, fam) if write else None
    done: Dict[str, dict] = {}
    if write and resume:
        # idempotency comes from TWO sources: (a) the in-progress JSONL checkpoint (a
        # crashed/aborted batch), and (b) a previously FINALIZED subagent JSON (a fully
        # completed batch re-run -> ZERO new calls). The checkpoint wins on conflict.
        done.update(_completed_from_subagent(task, model, fam))
        done.update(_load_checkpoint(cp_path))
        if done:
            print(f"resume: {len(done)} item(s) already completed; only missing items "
                  f"will call the API", file=sys.stderr)

    # Split the work up front (#3 idempotency): cached items make ZERO calls; only the
    # PENDING set is fetched. A fully-completed batch -> pending == [] -> 0 calls.
    cached: Dict[str, dict] = {}
    pending: List[tuple] = []   # (item_id, item)
    for it in items_in:
        iid = T.item_id(task, it)
        if iid in done:
            cached[iid] = _strip_internal(done[iid])
        else:
            pending.append((iid, it))

    if write and cp_path:
        os.makedirs(SUBAGENT_DIR, exist_ok=True)

    # --------------------------------------------------------------------- #
    # PHASE A — network fan-out (high concurrency = provider/--concurrency). Each
    # worker FETCHES one item's raw outputs (no grading -> no sandbox subprocess) and
    # the result is persisted to the JSONL checkpoint under a lock (thread-safe append
    # + flush + fsync). A thread-safe call counter enforces --max-calls (stop SUBMITTING
    # once the cap is hit); a thread-safe format-parse signal drives the early-abort.
    # --------------------------------------------------------------------- #
    net_workers = _net_concurrency(cfg, model, concurrency)
    models.reset_usage()  # clear any stale usage so this batch's token totals are clean
    cp_lock = threading.Lock()
    cnt_lock = threading.Lock()
    state = {"calls": 0, "fmt_ok": 0, "fmt_n": 0, "graded_seen": 0,
             "errors": 0, "abort": False, "cap_hit": False}
    cp_fh = (open(cp_path, "a" if resume else "w", encoding="utf-8")
             if (write and cp_path) else None)

    def _persist(rec: dict) -> None:
        if not cp_fh:
            return
        with cp_lock:
            cp_fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
            cp_fh.flush()
            os.fsync(cp_fh.fileno())

    def _reserve_calls(n: int) -> bool:
        """Thread-safe cap gate: claim ``n`` calls for an item iff that keeps the batch
        within --max-calls. Returns False (and trips ``cap_hit``) when it would exceed."""
        with cnt_lock:
            if state["abort"] or state["cap_hit"]:
                return False
            if max_calls is not None and state["calls"] + n > max_calls:
                state["cap_hit"] = True
                return False
            state["calls"] += n
            return True

    is_mock = (model == models.MOCK)

    def _fetch_one(iid: str, it) -> Optional[dict]:
        # honor an in-flight abort/cap before spending more (#4/#7): may overshoot
        # slightly by in-flight calls, which the spec accepts.
        n_calls = _item_calls(task, it, k)
        if not _reserve_calls(n_calls):
            return None
        try:
            # MOCK is deterministic + instant: grade INLINE (exactly the pre-concurrency
            # path) so --selftest/--dry-run stay byte-identical; the regrade path skips
            # mock sentinels by design, so Phase B must not re-touch a mock record.
            rec = (T.score_item(task, it, model, cfg, k) if is_mock
                   else T.fetch_item(task, it, model, cfg, k))
            rec["_item_id"] = iid
        except Exception as ex:  # noqa: BLE001  (#3 per-item fault isolation)
            rec = {"_item_id": iid, "error": True,
                   "error_msg": (type(ex).__name__ + ": " + str(ex))[:300],
                   "sample": getattr(it, "sample", "?"),
                   "language": getattr(it, "language", "?"),
                   "profile": getattr(it, "profile", None)}
            with cnt_lock:
                state["errors"] += 1
            print(f"  item {iid} FAILED ({rec['error_msg']}); recorded stub, "
                  f"continuing", file=sys.stderr)
            _persist(rec)
            return rec
        # cheap (no-subprocess) running format-parse signal for the early-abort gate.
        with cnt_lock:
            state["fmt_ok"] += rec.get("format_parse_ok", 0)
            state["fmt_n"] += rec.get("format_parse_n", 0)
            state["graded_seen"] += 1
            if (model != models.MOCK and state["graded_seen"] >= 10
                    and state["fmt_n"] > 0
                    and (state["fmt_ok"] / state["fmt_n"]) < parse_floor
                    and not state["abort"]):
                state["abort"] = True
                where = (f"Partial progress saved to {os.path.basename(cp_path)}; "
                         if cp_path else "")
                print(f"ABORTING {task}/{model}: format parse-success {state['fmt_ok']}/"
                      f"{state['fmt_n']} = {state['fmt_ok'] / state['fmt_n']:.2f} < floor "
                      f"{parse_floor:.2f} after {state['graded_seen']} items — model is "
                      f"systematically mis-formatting. {where}fix the prompt/parser and "
                      f"re-run to resume.", file=sys.stderr)
        _persist(rec)
        return rec

    fetched: Dict[str, dict] = {}
    try:
        if net_workers <= 1:
            # deterministic sequential path (mock / concurrency=1): preserves exact order.
            for iid, it in pending:
                if state["abort"] or state["cap_hit"]:
                    break
                rec = _fetch_one(iid, it)
                if rec is not None:
                    fetched[iid] = rec
        else:
            with ThreadPoolExecutor(max_workers=net_workers) as ex:
                futs = {}
                for iid, it in pending:
                    # stop SUBMITTING once an abort/cap has tripped (in-flight finish).
                    if state["abort"] or state["cap_hit"]:
                        break
                    futs[ex.submit(_fetch_one, iid, it)] = iid
                for fut in as_completed(futs):
                    rec = fut.result()
                    if rec is not None:
                        fetched[futs[fut]] = rec
    finally:
        if cp_fh:
            cp_fh.close()

    aborted = state["abort"]
    new_calls = state["calls"]

    # --------------------------------------------------------------------- #
    # PHASE B — grade from STORED raw (bounded concurrency = min(cpu, 16)). This is
    # exactly the --regrade path (T.regrade_record over the just-fetched records), so
    # the sandbox subprocess count is bounded REGARDLESS of the network width above.
    # Cached items were already graded on a prior run -> reused as-is (no re-grade).
    # --------------------------------------------------------------------- #
    def _grade(rec: dict) -> dict:
        """Grade one stored-raw record via the --regrade path. The grader embeds the
        authoritative parse counters (``_parse_ok``/``_parse_n``); keep them on the
        returned record transiently (stripped before the record is persisted) so the
        run_meta parse-success matches the pre-concurrency semantics exactly. MOCK
        records are already graded inline in Phase A (regrade skips mock sentinels), so
        they pass straight through."""
        if rec.get("error") or is_mock:
            return rec
        return T.regrade_record(task, dict(rec))  # carries _parse_ok/_parse_n

    graded_raw: Dict[str, dict] = {}
    fetched_list = [(iid, fetched[iid]) for iid, _ in pending if iid in fetched]
    gw = _grade_concurrency()
    if is_mock or gw <= 1 or len(fetched_list) <= 1:
        # mock grades inline (sequential, deterministic); real bounded-sequential when
        # there is <=1 item or a single grading worker.
        for iid, rec in fetched_list:
            graded_raw[iid] = _grade(rec)
    else:
        with ThreadPoolExecutor(max_workers=gw) as ex:
            futs = {ex.submit(_grade, rec): iid for iid, rec in fetched_list}
            for fut in as_completed(futs):
                graded_raw[futs[fut]] = fut.result()

    # parse-success (authoritative): the grader's own _parse_ok/_parse_n, mirroring the
    # pre-concurrency run_meta semantics exactly; then strip the internal keys. Error
    # records originate ONLY in Phase A (fetch failures) and pass through grading
    # unchanged, so counting them here once is authoritative (no separate fetch tally).
    parse_ok = parse_n = graded_items = n_errors = 0
    graded: Dict[str, dict] = {}
    for iid, rec in graded_raw.items():
        if rec.get("error"):
            n_errors += 1
        else:
            graded_items += 1
            parse_ok += rec.get("_parse_ok", 0)
            parse_n += rec.get("_parse_n", 0)
        graded[iid] = _strip_internal(rec)

    # #4 DETERMINISM: assemble the final item list sorted by stable item_id so the
    # subagent JSON is byte-identical regardless of the (nondeterministic) completion
    # order of the thread pool. Cached + freshly-graded items both key by item_id.
    by_id: Dict[str, dict] = dict(cached)
    by_id.update(graded)
    n_skipped_cached = len(cached)
    records = [by_id[iid] for iid in sorted(by_id)]

    usage = models.reset_usage()
    est_usd = _usage_cost(model, usage)

    agg = headline_aggregate(task, [r for r in records if not r.get("error")])
    parse_rate = (parse_ok / parse_n) if parse_n else None
    run_meta = {"n_items": len(records), "n_graded": graded_items,
                "n_resumed": n_skipped_cached, "n_errors": n_errors,
                "n_new_calls": new_calls, "parse_ok": parse_ok, "parse_n": parse_n,
                "parse_success_rate": parse_rate, "aborted": aborted,
                "net_concurrency": net_workers, "grade_concurrency": _grade_concurrency(),
                "usage": usage}
    if est_usd is not None:
        run_meta["est_usd"] = est_usd
    payload = {
        "task": task, "model": model, "family": fam, "split": split, "k": k,
        "prompt_version": P.PROMPT_VERSION, "env": env_block(cfg, split),
        "aggregate": agg, "items": records, "run_meta": run_meta,
    }
    if write and not aborted:
        with open(subagent_path(task, model, fam), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
        # batch finalized: the per-item checkpoint is now redundant with the subagent
        # JSON, so retire it. A clean re-run re-detects completion from the subagent file.
        if cp_path and os.path.exists(cp_path):
            os.remove(cp_path)
    if echo:
        compact = {"task": task, "model": model, "family": fam, "split": split,
                   "k": k, "n_items": len(records),
                   "n_errors": n_errors, "n_resumed": n_skipped_cached,
                   "parse_success_rate": (round(parse_rate, 3)
                                          if parse_rate is not None else None),
                   "aborted": aborted,
                   "usage": usage,
                   "toolchains": payload["env"]["toolchains"], "aggregate": agg}
        if est_usd is not None:
            compact["est_usd"] = est_usd
        print(json.dumps(compact, separators=(",", ":")))
    return payload


# --------------------------------------------------------------------------- #
# --regrade (re-apply graders to PERSISTED raw outputs; ZERO API)
# --------------------------------------------------------------------------- #
def regrade(only_task: Optional[str] = None, only_model: Optional[str] = None) -> int:
    """Re-read every ``out/subagent/*.json`` batch artifact, re-run the graders on the
    STORED raw model outputs, and rewrite each file's per-item records + aggregate IN
    PLACE — making ZERO API calls. This is the payoff of persisting raw outputs: a grading
    change (e.g. a new uniform_lane, a stricter band) is applied to an EXPENSIVE live run
    WITHOUT re-paying. Mock/baseline records (sentinel or no raw) and error stubs pass
    through unchanged; their aggregate is simply recomputed."""
    if not os.path.isdir(SUBAGENT_DIR):
        print(f"no subagent dir {SUBAGENT_DIR}; nothing to re-grade", file=sys.stderr)
        return 1
    n_files = n_regraded_items = n_passthrough = 0
    for fn in sorted(os.listdir(SUBAGENT_DIR)):
        if not fn.endswith(".json"):
            continue
        path = os.path.join(SUBAGENT_DIR, fn)
        pl = json.load(open(path, encoding="utf-8"))
        task = pl.get("task")
        if "items" not in pl or task not in T.TASKS:
            continue  # sweeps / non-batch artifacts have no per-item raw to re-grade
        if only_task and task != only_task:
            continue
        if only_model and pl.get("model") != only_model:
            continue
        new_items = []
        for rec in pl["items"]:
            rec2 = _strip_internal(T.regrade_record(task, dict(rec)))
            if rec2 != rec:
                n_regraded_items += 1
            else:
                n_passthrough += 1
            new_items.append(rec2)
        pl["items"] = new_items
        pl["aggregate"] = headline_aggregate(
            task, [r for r in new_items if not r.get("error")])
        pl.setdefault("run_meta", {})["regraded"] = True
        with open(path, "w", encoding="utf-8") as f:
            json.dump(pl, f, indent=2)
            f.write("\n")
        n_files += 1
        print(f"  re-graded {fn:42s} task={task} model={pl.get('model')} "
              f"items={len(new_items)}")
    print(f"regrade complete: {n_files} batch files; {n_regraded_items} item(s) "
          f"re-graded from stored raw, {n_passthrough} passthrough (mock/sentinel/"
          f"error). ZERO API calls. Re-run --aggregate to refresh results.json.")
    return 0


# --------------------------------------------------------------------------- #
# --pilot (tiny live smoke run: surface format/saturation/toolchain BEFORE spend)
# --------------------------------------------------------------------------- #
def pilot(task: Optional[str], model: Optional[str], family: Optional[str],
          limit: int = 1, split: str = "dev", k: int = 1,
          allow_skips: bool = True) -> int:
    """A tiny slice (default 1 model x 1 family x k=1, ``--limit N`` items) that reports
    parse-success rate, toolchain-SKIP rate, and the score distribution — so format
    failures / saturation / missing toolchains surface BEFORE the full spend. With real
    keys it WOULD call the API for real; with the mock/placeholder it just exercises the
    path. Writes nothing to out/subagent (it is a probe, not a result)."""
    cfg = models.load_config()
    if model is None:
        live = cfg.live_models()
        model = live[0] if live else models.MOCK
    if model != models.MOCK and not cfg.model_is_live(model):
        print(f"--pilot: model {model!r} not live (no key); using mock instead",
              file=sys.stderr)
        model = models.MOCK
    tasks = [task] if task else T.TASKS
    sp = D.load(split)
    families = list(sp.meta["batches"])
    print(f"PILOT: model={model} k={k} limit={limit} split={split}")
    overall_ok = True
    for tk in tasks:
        if tk in GLOBAL_TASKS:
            fams = [None]
        else:
            fams = [family] if family else families[:1]
        for fam in fams:
            items_in = T.build_items(tk, sp, fam)[:limit]
            if not items_in:
                continue
            parse_ok = parse_n = n_skip = n_err = 0
            scores: List[float] = []
            for it in items_in:
                try:
                    rec = T.score_item(tk, it, model, cfg, k)
                except Exception as ex:  # noqa: BLE001
                    n_err += 1
                    print(f"  {tk}/{fam or '-'} item ERROR: "
                          f"{type(ex).__name__}: {str(ex)[:120]}", file=sys.stderr)
                    continue
                parse_ok += rec.get("_parse_ok", 0)
                parse_n += rec.get("_parse_n", 0)
                if rec.get("skip"):
                    n_skip += 1
                s = _score_of(tk, _strip_internal(rec))
                if s is not None:
                    scores.append(s)
            pr = (parse_ok / parse_n) if parse_n else None
            skip_rate = (n_skip / len(items_in)) if items_in else 0.0
            dist = (f"min={min(scores):.2f} mean={statistics.fmean(scores):.2f} "
                    f"max={max(scores):.2f}" if scores else "no scores")
            pr_s = f"{pr:.2f}" if pr is not None else "n/a"
            print(f"  {tk:10s} fam={str(fam):16s} n={len(items_in)} "
                  f"parse_success={pr_s} toolchain_skip={skip_rate:.2f} "
                  f"errors={n_err}  scores[{dist}]")
            if pr is not None and pr < 0.5:
                overall_ok = False
                print(f"    WARNING: parse-success {pr_s} < 0.5 — check the output "
                      f"format / parser for {model} on {tk}", file=sys.stderr)
    print("PILOT", "OK" if overall_ok else "WARN (low parse-success on some task)")
    return 0 if overall_ok else 1


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


def print_plan(split: str = "dev", max_calls: Optional[int] = None,
               max_cost: Optional[float] = None) -> int:
    cfg = models.load_config()
    batches = plan(split)
    state = "PLACEHOLDER (--batch will refuse)" if cfg.is_placeholder else "LIVE-READY"
    proj = project_calls(split)
    km, kc, kj = (resolve_k(cfg, "refactor"), resolve_k(cfg, "comprehend"),
                  resolve_k(cfg, "judge"))
    k_note = (f"k={cfg.k_samples}" if km == kc == kj == cfg.k_samples
              else f"k(refactor={km},comprehend={kc},judge={kj}); default={cfg.k_samples}")
    print(f"config: {len(cfg.models_under_test)} model(s), {k_note}, "
          f"temp={cfg.temperature}, state={state}")
    print(f"split={split}; {len(batches)} subagent batches "
          f"(one Opus 4.8 general-purpose subagent each):")
    for b in batches:
        fam = f" --family {b['family']}" if b["family"] else ""
        print(f"  python3 bench/run_bench.py --batch {b['task']} "
              f"--model {b['model']}{fam}")

    # ---- #4 cost projection (the REAL fan-out) ------------------------------ #
    print(f"\nprojected API CALLS (fan-out: refactor/comprehend = items*k; "
          f"judge = per-item [levels*k + C(levels,2)*2*k]):")
    have_cost = "total_est_usd" in proj
    for model in sorted(proj["per_model"]):
        td = proj["per_model"][model]
        parts = []
        for task in ("refactor", "comprehend", "judge"):
            e = td[task]
            seg = f"{task}={e['calls']:,}(k{e['k']})"
            if "est_usd" in e:
                seg += f"/${e['est_usd']:,.0f}"
            parts.append(seg)
        msub = sum(td[t]["calls"] for t in td)
        line = f"  {model:28s} {'  '.join(parts)}  -> {msub:,} calls"
        if have_cost:
            line += f" / ${sum(td[t].get('est_usd', 0) for t in td):,.0f}"
        print(line)
    print(f"  {'TOTAL':28s} {proj['total_calls']:,} API calls"
          + (f"  /  ${proj['total_est_usd']:,.2f} est." if have_cost else
             "  (no price table in config['prices'] -> $ cost not estimated)"))

    # ---- #4 hard cap: REFUSE a plan that would exceed the cap -------------- #
    if max_calls is not None and proj["total_calls"] > max_calls:
        print(f"\nREFUSED: projected {proj['total_calls']:,} calls exceeds "
              f"--max-calls {max_calls:,}. Lower the matrix (fewer models/families, "
              f"smaller k) or raise the cap.", file=sys.stderr)
        return 4
    if max_cost is not None:
        if not have_cost:
            print(f"\nREFUSED: --max-cost {max_cost} given but config has no "
                  f"'prices' table to estimate cost against.", file=sys.stderr)
            return 4
        if proj["total_est_usd"] > max_cost:
            print(f"\nREFUSED: projected ${proj['total_est_usd']:,.2f} exceeds "
                  f"--max-cost ${max_cost:,.2f}.", file=sys.stderr)
            return 4

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

    # baseline panel: the clean ceiling must reach the optimality band (recovered=1)
    # and the rule-based mid must score (anti-gaming / non-triviality reference)
    panel = G.baseline_panel("python", py.spaghetti_src, py.program)
    c1b = (panel["clean_ceiling"].get("recovered") == 1
           and not panel["rule_based"].get("skip"))
    checks.append(f"baseline panel (clean ceiling recovered, rule-based scored): {c1b}")
    ok = ok and c1b

    # uniform cross-language lane (workflow): Python always carries a poolable
    # `uniform_quality` alongside its AST headline. For a non-Python language the
    # tool-backed lane is used when lizard is importable and degrades to the regex
    # proxy otherwise — either way the panel stays well-formed and commensurable.
    import bench.uniform_lane as U  # noqa: PLC0415 (optional, quarantined)
    liz = U.available()
    go_panel = G.baseline_panel("go", go.spaghetti_src, go.program)
    if liz:
        # tool-backed: Python item must expose a poolable uniform_quality, and the
        # non-Python clean-ceiling must reach the uniform metric optimum (~1).
        c1u = (rpy.get("uniform_quality") is not None
               and go_panel["clean_ceiling"].get("uniform_quality", 0) > 0.99)
        label = f"uniform lane ON (lizard {U.lizard_version()}): py poolable & go ceiling~1"
    else:
        # graceful fallback: no uniform_quality anywhere; the non-Python clean-ceiling
        # honestly SKIPs (no runnable per-language clean + no lizard), and the core
        # refactor scoring still runs via the regex proxy (rgo above already passed).
        c1u = (rpy.get("uniform_quality") is None
               and go_panel["clean_ceiling"].get("skip") is True)
        label = "uniform lane OFF (lizard absent): graceful regex-proxy fallback"
    checks.append(f"{label}: {c1u}")
    ok = ok and c1u

    # judge: one Python item -> monotone, perfect pairwise
    ji = next(it for it in T.build_judge_items(sp, family="allowlist")
              if it.sample == "allowlist_L8" and it.language == "python")
    jr = T.score_judge_item(ji, models.MOCK, cfg, k=1)
    c3 = (jr["monotonicity"] <= 0.0 and jr["pairwise_acc"] == 1.0
          and jr["position_consistency"] == 1.0)
    checks.append(f"judge monotone(<=0) & pairwise=1 & position-consistent: {c3}")
    ok = ok and c3
    # non-LLM metric-heuristic judge baseline (deterministic, zero-API): on the
    # clean->max ladder complexity is monotone with rank, so pairwise_acc is HIGH and
    # in [0,1]; rank-vs-complexity Spearman is strongly positive.
    hj = G.metric_heuristic_judge(ji)
    c3h = (0.0 <= hj["pairwise_acc"] <= 1.0 and hj["pairwise_acc"] >= 0.99
           and hj["spearman_rank_cc"] > 0.0)
    checks.append(f"metric-heuristic judge pairwise in [0,1] & high & rho>0: {c3h}")
    ok = ok and c3h
    # bias-control helpers (pure, testable): self-judge guard + jury majority
    c3b = (G.may_judge("anthropic", "engine") and not G.may_judge("openai", "openai")
           and G.jury_majority(["A", "A", "B"]) == "A"
           and G.jury_majority(["A", "B"]) is None)
    checks.append(f"judge bias controls (no-self-judge + jury majority): {c3b}")
    ok = ok and c3b

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
            # the mock dry-run always REGENERATES from scratch (it is deterministic and
            # cheap); resume=False so a stale finalized JSON never shadows fresh records.
            payload = run_batch(task, models.MOCK, fam, split=split, k=1,
                                write=True, echo=False, resume=False)
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
        return (f"pairwise={agg['pairwise_acc_mean']:.2f} "
                f"mono={agg['monotonicity_mean']:.2f}")
    return f"exact_match={agg['exact_match_rate']:.2f}"


# --------------------------------------------------------------------------- #
# --baselines (non-LLM reference panel; no API)
# --------------------------------------------------------------------------- #
def run_baselines(split: str = "dev") -> int:
    """Score the non-LLM baseline panel (formatter lower bound / rule-based mid /
    clean ceiling) over the refactor items for ALL FIVE languages and write
    per-baseline artifacts (``model=baseline_*``) so the panel lands alongside model
    results in the refactor figure. Proves the task is non-trivial and the optimum
    reachable. The cross-language number is the tool-backed ``uniform_quality`` lane
    (lizard CC + tokens + Buse--Weimer density), commensurable across the five
    languages; Python additionally keeps its rigorous AST ``simplification_quality``
    headline. No API spend (lizard, if present, is a static analyser)."""
    cfg = models.load_config()
    sp = D.load(split)
    items = T.build_refactor_items(sp)          # all five languages (was python-only)
    by_baseline: Dict[str, List[dict]] = {}
    for it in items:
        for name, g1 in G.baseline_panel(it.language, it.spaghetti_src, it.program).items():
            rec = {"sample": it.sample, "profile": it.profile, "language": it.language,
                   "variant": it.variant, "intrinsic": it.intrinsic, "snapshot": "baseline"}
            rec.update(G.aggregate_refactor([g1]))
            by_baseline.setdefault(name, []).append(rec)
    os.makedirs(SUBAGENT_DIR, exist_ok=True)
    written = 0
    for name, records in sorted(by_baseline.items()):
        scored = [r for r in records if not r.get("skip")]
        if not scored:  # all SKIP (tool absent) -> omit
            print(f"  baseline {name:14s} SKIP (tool unavailable on this host)")
            continue
        model = f"baseline_{name}"
        payload = {"task": "refactor", "model": model, "family": None, "split": split,
                   "k": 1, "prompt_version": P.PROMPT_VERSION, "env": env_block(cfg, split),
                   "aggregate": headline_aggregate("refactor", records), "items": records,
                   "per_language": _per_language_simpl(scored)}
        with open(subagent_path("refactor", model, None), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
        written += 1
        a = payload["aggregate"]
        uq = a.get("uniform_quality_mean")
        uq_s = f"{uq:.2f}" if uq is not None else "n/a"
        print(f"  baseline {name:14s} sem_ok={a['semantic_ok_rate']:.2f} "
              f"simpl_q(AST)={a['simplification_quality_mean']:.2f} "
              f"uniform_q={uq_s} n={a['n_items']}")
        for lang in sorted(payload["per_language"]):
            pl = payload["per_language"][lang]
            puq = pl["uniform_quality"]
            puq_s = f"{puq:.3f}" if puq is not None else "n/a"
            print(f"      {lang:11s} uniform_q={puq_s} (n={pl['n']})")
    print(f"baseline panel: {written} baselines written over {len(items)} "
          f"refactor items (5 languages) -> {SUBAGENT_DIR}")
    run_judge_baseline(split=split)
    return 0


def run_judge_baseline(split: str = "dev") -> int:
    """Non-LLM **metric-heuristic judge** baseline (paper \\S Benchmark Design): for
    every judge item, pick the lower static-complexity candidate of each pair
    (:func:`grade.metric_heuristic_judge`, ``eval.metrics``, zero API). Emits a
    ``judge`` payload with ``model=baseline_metric_judge`` in the SAME compact per-item
    shape ``aggregate()``/``build_figures`` already consume for judge (``pairwise_acc``,
    ``monotonicity``, per-language), so it lands alongside the LLM judges in
    Fig.~\\ref{fig:judge}. Deterministic; no spend."""
    cfg = models.load_config()
    sp = D.load(split)
    items_in = T.build_judge_items(sp)
    records: List[dict] = []
    for it in items_in:
        h = G.metric_heuristic_judge(it)
        # Mirror the LLM judge per-item record keys build_figures/headline_aggregate read.
        # The heuristic is pairwise + pointwise(rank-vs-complexity); it has no rating
        # ladder, so the monotonicity view is reported as the rank-vs-complexity Spearman
        # NEGATED (a faithful judge's rating-vs-rank rho is negative, and the heuristic's
        # complexity rises with rank, so -rho lands on the same "lower is better" axis as
        # the LLM monotonicity number it is plotted against).
        records.append({
            "sample": it.sample, "language": it.language, "snapshot": "baseline",
            "tier": it.tier, "levels": [lvl[0] for lvl in it.levels],
            "pairwise_acc": h["pairwise_acc"],
            "position_consistency": 1.0,          # deterministic: no position bias
            "n_consistent": h["n_scored"], "n_pairs": h["n_pairs"],
            "monotonicity": -h["spearman_rank_cc"],
            "sensitivity": None, "inversions": 0,
            "rating_by_level": None,
            "spearman_rank_cc": h["spearman_rank_cc"],
        })
    model = "baseline_metric_judge"
    payload = {"task": "judge", "model": model, "family": None, "split": split,
               "k": 1, "prompt_version": P.PROMPT_VERSION, "env": env_block(cfg, split),
               "aggregate": headline_aggregate("judge", records), "items": records}
    os.makedirs(SUBAGENT_DIR, exist_ok=True)
    with open(subagent_path("judge", model, None), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
    a = payload["aggregate"]
    print(f"  baseline {'metric_judge':14s} pairwise={a['pairwise_acc_mean']:.2f} "
          f"mono={a['monotonicity_mean']:.2f} n={a['n_items']} -> {SUBAGENT_DIR}")
    return 0


# --------------------------------------------------------------------------- #
# --sweep (prompt-robustness: paraphrase ensemble + variance decomposition)
# --------------------------------------------------------------------------- #
def _variance_decomposition(cells: List[List[List[float]]]) -> dict:
    """``cells`` = items -> paraphrases -> [k per-completion scores]. Split the total
    variance into within-prompt (k-sampling), between-prompt (paraphrasing), and
    between-item, and report the mean per-item between-prompt spread (the headline
    robustness metric)."""
    within, between_prompt, item_means, per_item_spread, all_means = [], [], [], [], []
    for paraphrases in cells:
        par_means = []
        for scores in paraphrases:
            if len(scores) >= 2:
                within.append(statistics.pvariance(scores))
            if scores:
                par_means.append(statistics.fmean(scores))
        if len(par_means) >= 2:
            between_prompt.append(statistics.pvariance(par_means))
            per_item_spread.append(statistics.pstdev(par_means))
        if par_means:
            item_means.append(statistics.fmean(par_means))
            all_means.extend(par_means)
    return {
        "n_items": len(cells),
        "overall_mean": statistics.fmean(all_means) if all_means else 0.0,
        "between_prompt_spread_mean": statistics.fmean(per_item_spread) if per_item_spread else 0.0,
        "variance_decomposition": {
            "within_prompt": statistics.fmean(within) if within else 0.0,
            "between_prompt": statistics.fmean(between_prompt) if between_prompt else 0.0,
            "between_item": statistics.pvariance(item_means) if len(item_means) >= 2 else 0.0,
        },
    }


def run_sweep(task: str, model: str, split: str = "dev") -> int:
    """FormatSpread-style robustness sweep: run every frozen paraphrase (held to the
    same output format) on the representatives at the ``max`` profile, and report the
    between-prompt spread + variance decomposition. The canonical prompt (variant 0)
    still drives the headline ``--batch`` numbers; this only measures sensitivity."""
    if task not in ("refactor", "comprehend"):
        raise SystemExit("--sweep supports refactor or comprehend")
    cfg = models.load_config()
    if model != models.MOCK and not cfg.model_is_live(model):
        print(f"configure bench/config.json: no API key for model {model!r} "
              f"(provider {cfg.provider_of(model)!r}), then re-run", file=sys.stderr)
        raise SystemExit(2)
    k = 1 if model == models.MOCK else cfg.k_samples
    sp = D.load(split)
    reps = set(sp.representatives())

    def _is_subset(it):
        return it.sample in reps and it.profile == "max" and it.variant == "base"

    cells: List[List[List[float]]] = []
    snap = models.MOCK_SNAPSHOT
    if task == "refactor":
        subset = [it for it in T.build_refactor_items(sp) if _is_subset(it)]
    else:
        subset = [it for it in T.build_comprehend_items(sp) if _is_subset(it)]
    for it in subset:
        per_par: List[List[float]] = []
        for v in P.PARAPHRASE_VARIANTS:
            if task == "refactor":
                system, user = P.refactor(it.language, it.spaghetti_src, it.result_vars, v)
                outs, snap = models.sample_k(model, system, user, k, cfg=cfg, mock_gold=it.mock_gold)
                scores = [g["simplification_quality"]
                          for g in (G.grade_refactor_one(it.language, o, it.spaghetti_src,
                                                         it.program) for o in outs)
                          if not g.get("skip")]
            else:
                system, user = P.comprehend(it.language, it.source, it.result_vars, v)
                outs, snap = models.sample_k(model, system, user, k, cfg=cfg, mock_gold=it.mock_gold)
                scores = [float(G.grade_comprehend_one(o, it.program)["exact_match"]) for o in outs]
            if scores:
                per_par.append(scores)
        if len(per_par) >= 2:
            cells.append(per_par)

    decomp = _variance_decomposition(cells)
    payload = {"task": task, "model": model, "split": split, "k": k, "mode": "sweep",
               "n_paraphrases": P.N_PARAPHRASES, "prompt_version": P.PROMPT_VERSION,
               "prompt_set_hash": P.prompt_set_hash(), "snapshot": snap,
               "env": env_block(cfg, split), "sweep": decomp}
    os.makedirs(SUBAGENT_DIR, exist_ok=True)
    name = f"sweep__{task}__{model.replace('/', '-')}.json"
    with open(os.path.join(SUBAGENT_DIR, name), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
    vd = decomp["variance_decomposition"]
    print(json.dumps({"mode": "sweep", "task": task, "model": model,
                      "n_items": decomp["n_items"], "n_paraphrases": P.N_PARAPHRASES,
                      "overall_mean": round(decomp["overall_mean"], 4),
                      "between_prompt_spread": round(decomp["between_prompt_spread_mean"], 4),
                      "variance": {kk: round(vv, 5) for kk, vv in vd.items()}},
                     separators=(",", ":")))
    return 0


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
        return _refactor_headline_score(item)
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
        # lane-aware headline (Python AST + non-Python uniform) so the 2-D figure
        # spans all five languages without the AST mean being diluted upstream.
        scores = [s for s in (_refactor_headline_score(it) for it in items)
                  if s is not None]
        sq = G.mean(scores)
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

    # Fig 5 — contamination control: dev vs test, graded by novelty tier A/B/C
    fig5: Dict[str, dict] = {}
    for (task, model, split), items in by_tms.items():
        if split == "dev":
            sc = G.mean([s for s in (_score_of(task, it) for it in items) if s is not None])
            fig5.setdefault(model, {}).setdefault(task, {})["dev"] = sc
        else:  # test: break out each novelty tier
            by_tier: Dict[str, List[dict]] = {}
            for it in items:
                by_tier.setdefault(it.get("tier", "A"), []).append(it)
            for tier, its in by_tier.items():
                sc = G.mean([s for s in (_score_of(task, it) for it in its) if s is not None])
                fig5.setdefault(model, {}).setdefault(task, {})[f"test_{tier}"] = sc
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

    # sweep artifacts have a different shape (no per-item records); keep them apart.
    batch = [pl for pl in payloads if "items" in pl]
    sweeps = [pl for pl in payloads if pl.get("mode") == "sweep"]
    if not batch:
        print("no batch artifacts (only sweeps?); run --dry-run or --batch first",
              file=sys.stderr)
        return 1

    per_model: Dict[str, dict] = {}
    for pl in batch:
        per_model.setdefault(pl["model"], {}).setdefault(pl["task"], {})[
            pl.get("family") or "_all"] = pl["aggregate"]

    figures = build_figures(batch)
    any_env = batch[0]["env"]
    # "live" = a real model ran; the mock and the non-LLM baselines do not count.
    live = any(pl["model"] != models.MOCK and not pl["model"].startswith("baseline")
               for pl in batch)

    robustness: Dict[str, dict] = {}
    for pl in sweeps:
        robustness.setdefault(pl["model"], {})[pl["task"]] = pl["sweep"]

    # exact dated snapshot ids the APIs returned, per model (closed-model repro threat)
    snaps: Dict[str, set] = {}
    for pl in batch:
        for it in pl["items"]:
            s = it.get("snapshot")
            if s:
                snaps.setdefault(pl["model"], set()).add(s)

    record = {
        "env": any_env,
        "status": "LIVE" if live else "DRY-RUN (mock model; AWAITING LIVE RUN)",
        "models": sorted({pl["model"] for pl in batch}),
        "splits": sorted({pl["split"] for pl in batch}),
        "snapshots": {m: sorted(v) for m, v in snaps.items()},
        "n_subagent_files": len(payloads),
        "per_model": per_model,
        "robustness": robustness,   # prompt-paraphrase sweep (Phase F)
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
% NOT compiled by the harness. Target venue: EACL (main or Findings) via ACL Rolling Review (ARR).
% Format: ACL (acl.sty + acl_natbib), anonymized for ARR double-blind review. The
% acl.sty / acl_natbib.bst files ship with the official ACL style-files repository;
% match the exact style file the target cycle mandates.
% VERIFY against the ARR/EACL CfP for the target cycle: long-paper limit (8 pp content
% + unlimited references), the current ARR anonymity / preprint policy, and the EACL
% commitment deadline. The Limitations section is MANDATORY and is NOT counted toward
% the page limit; the Responsible NLP checklist is filed in the submission portal, not
% in this PDF. Bibliography: refs.bib.
\documentclass[11pt]{article}
\usepackage[review]{acl}   % 'review' = anonymized + line numbers; switch to 'final' at camera-ready
\usepackage{times}
\usepackage{latexsym}
\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage{microtype}
\usepackage{graphicx}
\usepackage{amsmath,amssymb}
\usepackage{booktabs}
\usepackage{tikz}
\usepackage{pgfplots}
\pgfplotsset{compat=1.18}
\usepackage{listings}
\lstset{basicstyle=\ttfamily\footnotesize,breaklines=true,columns=fullflexible,
 frame=single,framesep=2pt,aboveskip=3pt,belowskip=2pt}
\newcommand{\awaiting}[1]{\begin{center}\fbox{\parbox{0.92\columnwidth}{\centering
 \textbf{[AWAITING LIVE RUN]}\\[3pt]\small #1}}\end{center}}

\title{Do LLM Judges Track Ground-Truth Code Quality? A Construction-Labeled,
 Contamination-Resistant Benchmark for Code Quality Judgment, Refactoring, and
 Comprehension}
\author{Anonymous ACL submission}

\begin{document}
\maketitle

\begin{abstract}
LLM-as-judge evaluations are validated against human preference or other models, so a
judge's error cannot be separated from rater noise: no code benchmark supplies a quality
ordering known \emph{independently} of raters. We build one. \emph{Spaghetti Architect}
is a correctness-preserving ``anti-optimization'' transpiler that compiles a clean JSON
intermediate representation (IR) into deliberately awful but provably-equivalent code in
five languages (Python, JavaScript, Go, Java, C++), gated by an oracle validator, along a
strictly-nested degradation knob. Because that knob \emph{is} a by-construction
maintainability order at fixed semantics, it yields a by-construction quality ordering ---
an \emph{operational} ground truth we validate with convergent complexity metrics and
published human-readability models rather than assert --- which we use to measure
\textbf{judge faithfulness}: do LLM judges rank code by that order? --- our headline task
(pairwise, position-swap, no self-judging, optional jury). Two
companion tasks reuse the same labels: \textbf{refactoring}, auto-graded for semantic
equivalence by the validator and for simplification toward a \emph{provably-reachable}
clean-complexity floor (with an optimality band, an over-golf guard, a readability axis,
and a per-anti-pattern removal check); and \textbf{comprehension} (output prediction),
defeated neither by guessing (differential variants) nor by memorization (fresh
instances). Crossing the incidental knob with an \emph{orthogonal intrinsic} size axis
(cascade arms $N$, list length $L$, operation count) lets us ask what no fixed corpus
can: do models fail because the problem got bigger, or because the presentation got
messier? We control contamination with a \emph{mint-after-cutoff} protocol, graded
novelty tiers (literal / structural / distribution-shift), and a canary; evaluate
\textbf{cross-vendor} models; treat prompt sensitivity as a \emph{measured} robustness
axis; and establish construct validity \textbf{without a new human-subjects study} via a
triad of by-construction ground truth, convergent automated complexity metrics
(\texttt{radon}, \texttt{lizard}, cognitive complexity), and published human-validated
readability models. This paper documents the design and the reusable harness; model
results populate from a live run (status: \textbf{@@STATUS@@}).
\end{abstract}

\section{Introduction}
Evaluating generated code increasingly relies on \emph{LLM-as-judge}: a model rates or
ranks candidate programs, and the rating stands in for quality. The method is only as
trustworthy as its gold standard, yet that gold is almost always \emph{human preference}
or \emph{another model} --- so a judge's mistakes are entangled with rater noise, and no
existing code benchmark supplies a quality ordering known \emph{independently} of raters.
We close that gap with a construction that emits the order \emph{by fiat}.

An optimizing compiler holds semantics fixed and minimizes a cost; Spaghetti Architect is
its mirror image, holding semantics fixed and \emph{maximizing} incidental complexity by
composing eleven named anti-patterns under a strength profile. The inversion is useful
because it yields \textbf{labels}: the output is correct by construction (the validator
compiles and runs all five targets against a reference oracle), the clean IR is a
\emph{known-optimal} reference, and --- crucially --- the strictly-nested profiles form a
\emph{by-construction quality ordering} at identical semantics --- one we treat as
\emph{operational} ground truth and corroborate externally (\S\ref{sec:cv}), not an axiom.
This is the ``Csmith-for-badness''
framing: where Csmith~\citep{yang-etal-2011-finding} mints random \emph{valid} programs to stress a
compiler, we mint semantically-labelled \emph{bad} programs with a tunable degradation
knob to stress --- and to \emph{audit the evaluators of} --- a model.

That ordering lets us separate two axes conflated everywhere else. \emph{Intrinsic}
complexity grows the logic (more cascade arms, longer lists, more operations);
\emph{incidental} complexity grows the mess at \emph{identical} semantics (the profile).
Varying them independently decomposes a model's failure into ``the problem got harder''
versus ``the presentation got worse'' --- the latter a robustness property that matters in
the wild and that no benchmark we know of isolates.

\paragraph{Contributions.} (1) A benchmark whose code-quality ordering is ground truth
\emph{by construction}, and the first measurement of LLM-judge \emph{faithfulness} to
such an order (position-swap, no self-judging, jury). (2) Two companion tasks on the same
labels --- semantically-gated refactoring toward a provably-reachable clean floor, and
output-prediction comprehension. (3) Orthogonal \emph{intrinsic} vs.\ \emph{incidental}
difficulty knobs that attribute a score change to one cause with the other held fixed.
(4) A contamination protocol (mint-after-cutoff $+$ graded novelty tiers $+$ canary), a
cross-vendor evaluation, a measured prompt-robustness axis, and a zero-IRB
construct-validity triad --- all in a reusable, stdlib-core harness.

\section{Related Work}
\paragraph{Code benchmarks and what they fix.} Correctness
suites~\citep{chen-etal-2021-evaluating,austin-etal-2021-program,zhuo-etal-2024-bigcodebench} and
reasoning/execution and repair
benchmarks~\citep{gu-etal-2024-cruxeval,jimenez-etal-2024-swe,gautam-etal-2025-refactorbench} grade
\emph{what} a model produces but fix \emph{presentation} and (mostly) cannot regenerate;
functional test-augmentation~\citep{liu-etal-2023-code} tightens equivalence checking but on
static tasks. None expose an incidental-complexity knob at fixed semantics, and none carry
a ground-truth \emph{quality} ordering.
\paragraph{LLM-as-judge and the missing ground truth.} MT-Bench and
juries~\citep{zheng-etal-2023-judging,verga-etal-2024-replacing} validate judges against human preference or
model panels, and maintainability-metric critiques~\citep{vandeursen-2014-think}
warn that automated scores are not quality itself. Judge benchmarks with \emph{objective}
ground truth do exist --- RewardBench~\citep{lambert-etal-2024-rewardbench} and
JudgeBench~\citep{tan-etal-2025-judgebench} score judges against \emph{verifiable
correctness} --- but correctness is binary and orthogonal to presentation; what is missing
is a \emph{graded quality} order at \emph{fixed} correctness, a controlled stimulus whose
ordering is known. The closest prior move is robustness to semantics-preserving
code transforms --- ReCode~\citep{wang-etal-2023-recode} perturbs problems and measures
correctness drop --- but it targets generation, not \emph{judgment}, and carries no graded
quality label. We supply the label and turn the judge itself into the measured object.
\paragraph{Contamination.} A public generator is defensible only under a protocol: we
adopt time-gating and protected
release~\citep{jacovi-etal-2023-stop,jain-etal-2025-livecodebench}, templated regeneration and
the robustness-under-resampling lesson~\citep{mirzadeh-etal-2024-gsm,zhu-etal-2024-dyval}, the
contamination-detection survey~\citep{ravaut-etal-2024-comprehensive}, and membership
probes~\citep{shi-etal-2024-detecting} as audits.
\paragraph{Prompt sensitivity.} Format and phrasing move
scores~\citep{liang-etal-2023-holistic,zhu-etal-2023-promptrobust,sclar-etal-2024-quantifying}; we treat that spread
as a \emph{measured} axis (a canonical prompt for headline numbers plus a frozen
paraphrase sweep with variance decomposition) rather than a confound.
\paragraph{Provenance of the transforms.} The eleven \texttt{SPAGH\_*} moves descend from
the Collberg--Thomborson obfuscation
taxonomy~\citep{collberg-etal-1997-taxonomy,collberg-etal-1998-manufacturing} and tools like
Tigress~\citep{collberg-2015-tigress}, OLLVM~\citep{junod-etal-2015-obfuscator}, and
\texttt{javascript-obfuscator}~\citep{kachalov-2016-javascript}; mutation
testing~\citep{demillo-etal-1978-hints,jia-harman-2011-analysis} is the sign-flipped sibling (it
\emph{injects faults}; we \emph{preserve} semantics and label the mess); and program
generators Csmith~\citep{yang-etal-2011-finding} and YARPGen~\citep{livinskii-etal-2020-random} mint random
\emph{valid} programs for differential testing. We reuse these primitives as a
\emph{maintainability-degradation knob with ground truth}; none of them emit a quality
label or a known-optimal target.
\paragraph{Adopt-and-add (our positioning).} We \emph{adopt} each field's mitigation and
\emph{add} the one affordance none supply: regeneration, canary, and time-gating for
contamination~\citep{jain-etal-2025-livecodebench,mirzadeh-etal-2024-gsm,zhu-etal-2024-dyval,jacovi-etal-2023-stop};
position-swap, reference-guided grading, and juries for
judges~\citep{zheng-etal-2023-judging,verga-etal-2024-replacing}; functional differential testing (not
reference matching) for refactoring~\citep{liu-etal-2023-code,gautam-etal-2025-refactorbench}; and a
canonical-prompt-plus-sweep design for prompt
sensitivity~\citep{liang-etal-2023-holistic,zhu-etal-2023-promptrobust,sclar-etal-2024-quantifying}. We \emph{add} a
controlled stimulus with orthogonal intrinsic/incidental knobs and a provably-reachable
optimum, so a score change is attributable to one knob with the other held fixed --- the
property the table below summarizes.
\begin{table*}[t]\centering\footnotesize
\begin{tabular}{lcccccc}
\toprule
Approach & GT label & Intrinsic & Incidental & Auto-equiv. & Contam. & Multi-lang \\
\midrule
\textbf{Spaghetti Architect (ours)} & \checkmark & \checkmark & \checkmark & \checkmark & \checkmark & \checkmark \\
Obfuscators~\citep{collberg-etal-1997-taxonomy,collberg-2015-tigress,junod-etal-2015-obfuscator,kachalov-2016-javascript}
 & $\times$ & $\circ$ & $\circ$ & $\circ$ & $\times$ & $\circ$ \\
Mutation testing~\citep{demillo-etal-1978-hints,jia-harman-2011-analysis}
 & \checkmark & --- & --- & inv. & $\times$ & $\circ$ \\
Program generators~\citep{yang-etal-2011-finding,livinskii-etal-2020-random}
 & $\circ$ & \checkmark & $\times$ & \checkmark & \checkmark & $\circ$ \\
Correctness benches~\citep{chen-etal-2021-evaluating,austin-etal-2021-program}
 & \checkmark & $\times$ & $\times$ & \checkmark & $\times$ & $\times$ \\
Reasoning/refactor~\citep{gu-etal-2024-cruxeval,jimenez-etal-2024-swe,gautam-etal-2025-refactorbench}
 & \checkmark & $\circ$ & $\times$ & \checkmark & $\circ$ & $\circ$ \\
Judge / metric validity~\citep{zheng-etal-2023-judging,vandeursen-2014-think}
 & $\times$ & $\times$ & $\times$ & $\times$ & --- & --- \\
Contamination~\citep{jacovi-etal-2023-stop,ravaut-etal-2024-comprehensive,jain-etal-2025-livecodebench}
 & varies & $\times$ & $\times$ & varies & \checkmark & $\circ$ \\
\bottomrule
\end{tabular}
\caption{Positioning ($\checkmark$ yes, $\circ$ partial, $\times$ no, inv.\ inverted).
No neighbor offers a by-construction ground-truth \emph{quality order} (the basis for our
judge-faithfulness task) \emph{and} orthogonal intrinsic/incidental knobs \emph{and}
auto-checkable equivalence \emph{and} contamination resistance across five languages from
one IR.}
\end{table*}

\section{The Generator}
Spaghetti Architect is a four-stage pipeline (parser $\to$ planner $\to$ five
language backends $\to$ validator); see the project's \texttt{architecture.md}. The
IR supports four composable operations --- \texttt{MEMBERSHIP\_CHECK} ($r = t \in c$),
\texttt{KEY\_VALUE\_LOOKUP} ($r = \text{pairs}.\text{get}(k,d)$), \texttt{AGGREGATE}
($\mathrm{sum}/\mathrm{min}/\mathrm{max}$ over an integer collection), and
\texttt{CONDITIONAL\_SELECT} (an integer-comparison branch) --- chained into
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
\paragraph{Axes.} We cross \emph{incidental} profile --- a six-point,
strictly-\emph{nested} ladder \texttt{clean}$\subset$\texttt{minimal}$\subset$%
\texttt{light}$\subset$\texttt{standard}$\subset$\texttt{heavy}$\subset$\texttt{max}
(\texttt{SPAGH\_*} set inclusion, which \emph{is} the by-construction maintainability
order; deduplicated to distinct renders per family) --- $\times$ \emph{intrinsic}
scale (\texttt{config\_resolver} $N\!\in\!\{4..32\}$, \texttt{allowlist}
$L\!\in\!\{8..128\}$, an AGGREGATE family \texttt{agg\_stats} $W\!\in\!\{8,32\}$ and a
CONDITIONAL\_SELECT family \texttt{threshold\_select} $T\!\in\!\{2,4\}$, plus
\texttt{status\_router}, \texttt{discovery\_pipeline}, \texttt{fsm\_transition})
$\times$ language $\in$ five backends $\times$ input variant ($\texttt{*\_v0..v4}$).
The public \texttt{dev} split has @@NDEV@@ instances (@@NBASE@@ bases + variants)
across seven families, seed \texttt{@@SEED@@}. The six-point knob restores statistical
power to judge monotonicity, which we analyse with a mixed-effects model pooling
families and items rather than a four-point per-series Spearman.

\paragraph{Contamination: a mint-after-cutoff protocol, not a proof.} A public
\emph{generator} is acceptable provided every \emph{scored} instance is minted
\emph{after} a model's training cutoff from a \textbf{private, rotating seed}, after
LiveCodeBench/GSM-Symbolic~\citep{jain-etal-2025-livecodebench,mirzadeh-etal-2024-gsm}: the
defense is the \emph{protocol}, not the artifact, and we make no
``contamination-proof'' claim. We report graded \textbf{novelty tiers} --- \textbf{A}
(literal-novel: re-mint numeric literals and salt strings, defeats verbatim
memorization), \textbf{B} (structural held-out: op-chain \emph{shapes} kept private,
defeats structure memorization), \textbf{C} (distribution shift: deeper chains and
out-of-range scales, defeats distribution learning) --- so degradation across
A$\to$B$\to$C is a \emph{measured} axis. A \textbf{canary} GUID is embedded in the
public release for training-set audits; the held-out seed and Tier B/C structures are
never committed (a deliberate trade-off, not an omission). Every minted IR is
re-validated by the parser.

\paragraph{Tasks and grading (reusing the validator + metrics).}
\emph{Refactor.} The model rewrites a rendered spaghetti source; we grade semantic
equivalence by running its output \textbf{untrusted} (model Python in a subprocess
with \texttt{-I} isolation, a wall-clock timeout, and @@NETISO@@; the other four
languages via the project validator) against the oracle. Simplification is scored only
if equivalent, as distance toward a \emph{provably-reachable clean-complexity floor}
(not a unique optimum): per-facet
$\mathrm{clamp}((m_{\text{spag}}\!-\!m_{\text{model}})/(m_{\text{spag}}\!-\!
m_{\text{clean}}),-1,1)$ over a rigorous Python-AST lane (cyclomatic, AST size,
Halstead effort, and a \emph{readability}/maintainability-index axis so
terse-but-cryptic code cannot game size) and an agnostic \emph{text} proxy lane for the
other four languages, capped at the floor (so the \textbf{headline refactor numbers are
Python-rigorous}; the other four are a proxy, reported separately and never pooled);
$\text{simpl\_q}=\text{semantic\_ok}\cdot
\mathrm{geomean}(\text{positive recoveries})$. We add an \emph{optimality band}
(equivalent and within $\varepsilon$ of the reachable clean $\Rightarrow$
``recovered''), an \texttt{over\_golfed} guard (undershooting the floor $=$ removed
structure, flagged not rewarded), and a per-\texttt{SPAGH\_*} \emph{removal} check.
A non-LLM \textbf{baseline panel} --- autoformatter (lower bound), a rule-based AST
simplifier (non-LLM mid), and the clean baseline ($(1,1)$ ceiling) --- establishes the
task is non-trivial and the optimum reachable (Fig.~\ref{fig:refactor}).
\emph{Judge.} The \emph{knob} is our \emph{operational} ground truth (the by-construction
order; validated, not asserted, in \S\ref{sec:cv}); external complexity metrics are
\emph{convergent witnesses}, not the definition of quality. We lead with \textbf{pairwise}
accuracy against the known order under MT-Bench \textbf{position-swap} (a pair counts
only if the choice is consistent across both orderings), with no self-judging and an
optional jury~\citep{zheng-etal-2023-judging,verga-etal-2024-replacing}; pointwise ratings give the
secondary monotonicity (Spearman) view. Every LLM judge is contrasted with a non-LLM
\textbf{metric-heuristic judge} --- pick the lower static-complexity candidate
(\texttt{eval.metrics}) --- so an LLM judge's value \emph{over a trivial complexity
heuristic} is explicit, not assumed.
\emph{Comprehend.} Predict the result variables; exact-match vs.\ the oracle over base
$+$ variants. Matrix sizes (dev): refactor @@NREFAC@@, comprehend @@NCOMP@@, judge
@@NJUDGE@@ items.

\paragraph{Prompt robustness as a measured axis.} Each task has a pre-registered
\emph{canonical} prompt (for the headline numbers) plus $@@NPARA@@$ frozen
\emph{paraphrases} that vary wording/role/order while holding the graded output format
byte-identical, after HELM/FormatSpread~\citep{liang-etal-2023-holistic,sclar-etal-2024-quantifying}. A
\texttt{--sweep} reports the mean and the between-prompt spread, and a variance
decomposition into within-prompt (sampling), between-prompt (phrasing), and
between-item components (Fig.~\ref{fig:robust}). The prompt set is content-hashed
(\texttt{@@PROMPTHASH@@}) and was fixed before any results existed.

\paragraph{Protocol and determinism.} Sample generation and all static metrics are
byte-deterministic (seeded). LLM outputs are \emph{not}: we pin
\texttt{temperature=@@TEMP@@}, record the exact model id and parameters, and draw $k=$@@K@@
samples per item. At this temperature the $k$ draws capture \emph{serving-side}
nondeterminism only, so per-item CIs are tight and the reported uncertainty is dominated by
the \emph{between-item} bootstrap; the pre-registered inferential test is a mixed-effects
model (judge rating vs.\ knob rank; random intercepts for family, sample, and language)
with Benjamini--Hochberg FDR and Holm correction across models, implemented in the released
\texttt{bench/analysis.py} (statsmodels in a quarantined env; an honest \textsc{skip} plus a
cluster-bootstrap fallback if absent). We claim \emph{protocol} reproducibility, not byte
reproducibility, for model results. Prompts are frozen and
versioned (\texttt{@@PROMPTVER@@}); each returns a single parseable artifact (a code
block / an integer / \texttt{A}|\texttt{B} / a JSON object) so no reasoning leaks into
the graded output.

\section{Results}
\emph{Status: \textbf{@@STATUS@@}.} Model figures populate from \texttt{out/results.json}
after a live run; until then each is a wired placeholder. The body carries the three
headline figures; the decomposition, refactor, cross-language, and robustness panels are
in Appendix~\ref{app:figs}.

\begin{figure*}[t]\centering @@FIG4@@
\caption{\textbf{Judge faithfulness (headline).} Pairwise accuracy under MT-Bench
position-swap and rating-vs-knob monotonicity per model, against the non-LLM
\textbf{metric-heuristic judge} and the \texttt{radon}/\texttt{lizard}/cognitive anchors
(\S\ref{sec:cv}) --- the knob (our operational ground truth), not a metric, is the
reference.}\label{fig:judge}\end{figure*}

\begin{figure*}[t]\centering @@FIG1@@
\caption{\textbf{Incidental-complexity degradation.} Task score vs.\ profile with logic
held fixed --- does mess alone hurt?}\end{figure*}

\begin{figure*}[t]\centering @@FIG5@@
\caption{\textbf{Contamination control (graded tiers).} Public \texttt{dev} vs.\ private
\texttt{test} by novelty tier A$\to$B$\to$C (literal / structural / distribution-shift);
the gap, graded by tier, is a measured memorization axis.}\end{figure*}

\section{Construct Validity (a zero-IRB triad)}\label{sec:cv}
For a benchmark-\emph{generator} the contribution is the instrument, and we establish
its validity \textbf{without a new human-subjects study} via three complementary layers,
none touching a participant. \textbf{(i) By-construction ordering (an assumption we test):}
the nested \texttt{SPAGH\_*} inclusion order is a \emph{by-construction} maintainability
ordering at fixed semantics; that set inclusion $\Rightarrow$ not-better is an
\emph{assumption} layers (ii)--(iii) exist to corroborate, not an axiom. \textbf{(ii)
Automated convergent validity (real, model-independent data):}
the knob moves several external complexity metrics monotonically
(\texttt{radon}: cyclomatic complexity and the maintainability index; \texttt{lizard}:
language-agnostic cyclomatic complexity; cognitive complexity: a nesting-aware readability
proxy). Scoring the @@NANCHOR@@ \texttt{dev} Python sources off the zero-dependency core (a
quarantined virtualenv), the incidental rank correlates with \texttt{radon} cyclomatic
at $\rho=@@ANCINCCC@@$, \texttt{lizard} cyclomatic at $\rho=@@ANCLIZ@@$, and cognitive
complexity at $\rho=@@ANCCOG@@$, and inversely with \texttt{radon} maintainability at
$\rho=@@ANCINCMI@@$; the intrinsic $N$ knob gives $\rho=@@ANCNCC@@$. Our own metric
lane is tightly anchored to the external ones (our CC vs.\ radon CC $\rho=@@ANCXCC@@$,
our MI vs.\ radon MI $\rho=@@ANCXMI@@$). These structural metrics are mutually
\emph{correlated} (overlapping notions of complexity), so they are \emph{consistent}
convergent evidence rather than statistically independent witnesses; the genuinely
independent grounding is layer (iii). \textbf{(iii) External human grounding:} we
anchor to \emph{published} human-validated readability models ---
Buse--Weimer~\citep{buse-weimer-2010-learning}, Scalabrino et
al.~\citep{scalabrino-etal-2018-comprehensive}, Dorn~\citep{dorn-2012-general}; using public,
de-identified prior data is \emph{not} new human-subjects research. Our run records
this anchor as \textbf{@@READSTATUS@@} (the models ship as Weka/Java artifacts --- an
honest SKIP, not a relabeled proxy). The same external metrics are the reference
against which the judge (Fig.~\ref{fig:judge}) is calibrated, so a judge is measured
against an independent witness, not only against other judges. A fresh controlled
human ranking would strengthen layer (iii) but is \emph{future work}; the triad stands
on its own.

\section{Reproducibility and Availability}
The generator, the public \texttt{dev} split, the metric/grader lanes, and the harness
(\texttt{--selftest}/\texttt{--dry-run}/\texttt{--plan}/\texttt{--baselines}/%
\texttt{--sweep}/\texttt{--aggregate}/\texttt{--report}) are released under the MIT
license; for double-blind review an anonymized snapshot is provided at
\textbf{@@ARTIFACT@@}, and the release carries a \textbf{canary} GUID for training-set
audits. To preserve the \emph{mint-after-cutoff} guarantee, the private held-out seed and
the Tier B/C structures are \textbf{deliberately withheld} --- a contamination trade-off,
not an omission: anyone can regenerate an equivalent private split from a fresh secret
seed with the released generator, and the public \texttt{dev} split plus the recorded
protocol (frozen prompts \texttt{@@PROMPTHASH@@}, $k$, temperature, dated model snapshots)
make every reported number protocol-reproducible. Generator and static metrics are
byte-deterministic; model results are \emph{protocol}-reproducible, not byte-reproducible.
The Responsible NLP / reproducibility checklist is filed with the submission.

\section{Conclusion}
By treating an anti-optimizer as a labelled benchmark generator, we obtain a
by-construction ground-truth quality order --- and with it the first direct measurement of
LLM-judge faithfulness --- alongside semantically-gated refactoring, output-prediction
comprehension, two orthogonal difficulty knobs, auto-checkable equivalence, and
contamination resistance, across five languages from one IR. The orthogonal decomposition
turns ``the model got it wrong'' into the sharper ``the model (or its judge) is not robust
to incidental complexity,'' which is the property the benchmark is built to measure.

\section*{Limitations}
\paragraph{No new human evaluation (the principal open problem).} Our quality ground truth
is by \emph{construction} --- the strictly-nested \texttt{SPAGH\_*} inclusion order --- and
we validate the \emph{instrument} through convergent automated metrics and \emph{published}
human-validated readability models rather than a fresh human study (\S\ref{sec:cv}). This
is a deliberate scope choice, and we are explicit that it is \emph{not} solved: we do not
collect human quality ratings on \emph{our} instances, so (i) the by-construction order,
though monotone in every external complexity metric we measured, is not re-confirmed by
fresh raters on these exact programs, and (ii) judge faithfulness is scored against the
construction, not against human consensus --- if human notions of ``messy'' diverged from
\texttt{SPAGH\_*} inclusion, a faithful judge could look unfaithful, or vice versa. Our
human grounding is therefore \emph{published, de-identified} readability datasets (layer
(iii) of \S\ref{sec:cv}) --- a deliberate zero-IRB design, not an oversight; a fresh
controlled human ranking on our instances would further strengthen (i)--(ii) and remains
optional future work. The convergent-metric triad mitigates but does not remove this
threat.
\paragraph{Construct and external validity.} The IR has a small, fixed operation set ---
a deliberate \emph{controlled stimulus}, not a generality claim; it is what holds semantics
fixed while varying mess. Our external validity is therefore \emph{bounded by design}: the
programs are small and synthetic over four operations, so we claim findings about
\emph{robustness to incidental complexity} and \emph{judge faithfulness on a controlled
stimulus}, \emph{not} that absolute scores transfer to real-world repositories --- and
scaling the item count does not widen this scope. Equivalence is established by differential
testing over base $+$ variants, not by proof; the rigorous simplification and
optimality-band lanes are Python-only (the other four languages use a text/regex proxy with
a language-neutral floor), so cross-language scores are a proxy and are \emph{not} pooled
without caveat.
\paragraph{Contamination.} We claim mint-after-cutoff, not contamination-proofing: a public
generator could synthesize in-distribution training data, which is exactly why we report
graded A/B/C novelty tiers and keep the held-out seed and Tier B/C structures private. Two
honest caveats: the tier gap is informative only \emph{if observed}, and releasing the
public \texttt{dev} split means it will itself be contaminated over time --- so the lasting
contribution is the \emph{protocol} (re-mint from a fresh private seed), not a permanently
clean artifact. The optimum is a provably-reachable \emph{floor}, not a unique best
refactoring; ``recovered'' means reaching that floor's band, and the \texttt{over\_golfed}
guard plus the readability axis defeat the obvious size-gaming strategy.
\paragraph{Conclusion validity and scope.} LLM outputs are nondeterministic; we mitigate
with \texttt{temperature=0}, $k$ samples, and bootstrap CIs, and claim \emph{protocol} ---
not byte --- reproducibility for model results, recording the exact dated snapshot id per
call since closed models drift. At temperature~0 the $k$ draws reflect serving-side jitter
only, so per-item CIs are narrow and the between-item bootstrap and the mixed-effects model
(\texttt{bench/analysis.py}) carry the inference. Output-prediction \emph{comprehension}
over small deterministic programs may \emph{saturate} for strong models; we mitigate with
differential variants and the deeper Tier-C chains and report any ceiling honestly rather
than masking it. Prompt sensitivity is \emph{measured} (canonical prompt plus a frozen
paraphrase sweep and variance decomposition), not assumed. The models-under-test are
\textbf{cross-vendor} (Anthropic, OpenAI, Google, and open-weights via an OpenAI-compatible
gateway); a missing toolchain \textsc{skip}s (recorded, never counted as a pass).

\section*{Ethics Statement}
This work introduces a \emph{measurement instrument}, not a deployment artifact. The
generator deliberately emits low-quality but \emph{semantically faithful} code; its moves
overlap with obfuscation (a dual-use concern), but it changes presentation at zero
semantic cost and is intended for evaluating and auditing models, not for shipping
degraded software. No human subjects, no personal or private data, and no annotation labor
are involved: every instance is synthesized and labelled by construction, and the
readability ``human grounding'' uses only \emph{published, de-identified} prior datasets
and models. We embed a canary GUID so future training sets can be audited for inclusion of
our public split, and we withhold the held-out seed to keep the scored split fresh. The
live evaluation consumes paid API and local compute; we report the model snapshots,
sampling budget ($k$), and toolchains so the cost and footprint are transparent.

% acl.sty already issues \bibliographystyle{acl_natbib}; adding another triggers a
% bibtex "another \bibstyle" error, so we deliberately omit it here.
\bibliography{refs}

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

\section{Additional figures}\label{app:figs}
\begin{figure*}[t]\centering @@FIG2@@
\caption{\textbf{Intrinsic vs.\ incidental decomposition.} $\Delta$score from growing
$N/L$ (fixed profile) vs.\ from growing the profile (fixed logic) --- the scientific
core.}\end{figure*}

\begin{figure*}[t]\centering @@FIG3@@
\caption{\textbf{Refactor 2-D $+$ baseline panel.} Semantic-ok rate vs.\ simplification
quality per model; the clean baseline is the $(1,1)$ ceiling and the non-LLM panel
(autoformatter lower bound, rule-based AST mid) marks the reachable range.}
\label{fig:refactor}\end{figure*}

\begin{figure*}[t]\centering @@FIG6@@
\caption{\textbf{Cross-language.} Task score per language (same logic), exposing harder
backends (a proxy across lanes; not pooled without caveat).}\end{figure*}

\begin{figure*}[t]\centering @@FIG7@@
\caption{\textbf{Prompt robustness.} Per-model between-prompt spread over the frozen
paraphrase ensemble, and the within-prompt / between-prompt / between-item variance
decomposition --- sensitivity is measured, not assumed.}\label{fig:robust}\end{figure*}
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
    def cell(v):
        return "---" if v is None else f"{v:.3f}"
    def gap(dev, v):
        return "---" if dev is None or v is None else f"{dev - v:+.3f}"
    rows = []
    for model, tasks_ in sorted(fig.items()):
        for task, sd in sorted(tasks_.items()):
            dev = sd.get("dev")
            ta, tb, tc = sd.get("test_A"), sd.get("test_B"), sd.get("test_C")
            rows.append(f"{_texesc(model)} & {task} & {cell(dev)} & {cell(ta)} & "
                        f"{cell(tb)} & {cell(tc)} & {gap(dev, tc)} \\\\")
    return ("\\begin{tabular}{ll rrrr r}\\toprule model & task & dev & test\\,A & "
            "test\\,B & test\\,C & dev$-$C \\\\\\midrule\n" + "\n".join(rows)
            + "\n\\bottomrule\\end{tabular}")


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


def _fig_robustness(results, live):
    hint = ("Per-model between-prompt spread over the frozen paraphrase ensemble, plus "
            "the within/between-prompt/between-item variance decomposition. From "
            "\\texttt{results.robustness} (the \\texttt{--sweep} artifacts).")
    rob = (results or {}).get("robustness", {})
    if not live or not rob:
        return _awaiting(hint)
    rows = []
    for model, tasks_ in sorted(rob.items()):
        for task, d in sorted(tasks_.items()):
            vd = d.get("variance_decomposition", {})
            rows.append(f"{_texesc(model)} & {task} & {d.get('overall_mean', 0):.3f} & "
                        f"{d.get('between_prompt_spread_mean', 0):.3f} & "
                        f"{vd.get('within_prompt', 0):.4f} & {vd.get('between_prompt', 0):.4f} & "
                        f"{vd.get('between_item', 0):.4f} \\\\")
    return ("\\begin{tabular}{llrrrrr}\\toprule model & task & mean & spread & "
            "$\\sigma^2_{\\text{within}}$ & $\\sigma^2_{\\text{prompt}}$ & "
            "$\\sigma^2_{\\text{item}}$ \\\\\\midrule\n" + "\n".join(rows)
            + "\n\\bottomrule\\end{tabular}")


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

    def anc_tool(name, metric, fmt="{:+.3f}", absent="AWAITING"):
        a = anchor.get("anchors", {}).get(name, {})
        if a.get("status") != "OK":
            return absent
        v = a.get("metrics", {}).get(metric, {}).get("incidental_knob_spearman_mean")
        return absent if v is None else fmt.format(v)

    read_status = anchor.get("anchors", {}).get("readability", {}).get("status", "AWAITING")
    try:                       # real, dedup'd judge item count (renders dev sources)
        njudge = len(T.build_judge_items(D.load("dev")))
    except Exception:          # noqa: BLE001
        njudge = nbase * len(D.LANGS)

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
        "@@NREFAC@@": str(ndev * len(T.REFACTOR_PROFILES) * len(D.LANGS)),
        "@@NCOMP@@": str(ndev * len(T.COMPREHEND_PROFILES) * len(D.LANGS)),
        "@@NJUDGE@@": str(njudge),
        "@@NPARA@@": str(P.N_PARAPHRASES),
        "@@PROMPTHASH@@": _texesc(P.prompt_set_hash()),
        "@@ARTIFACT@@": _texesc("anonymous.4open.science/r/spaghetti-architect-bench "
                                "(anonymized for review; MIT public repo + archival DOI at camera-ready)"),
        "@@NETISO@@": ("a private network namespace (\\texttt{unshare -rn})"
                       if G.network_isolation_prefix()
                       else "\\texttt{-I} isolation + a sanitized environment"),
        "@@ANCHORSTATUS@@": _texesc(anchor.get("status", "ABSENT")),
        "@@RADONVER@@": _texesc(anchor.get("radon_version", "n/a")),
        "@@NANCHOR@@": str(anchor.get("n_sources", "AWAITING")),
        "@@ANCINCCC@@": anc("incidental_cc_spearman_mean"),
        "@@ANCINCMI@@": anc("incidental_mi_spearman_mean"),
        "@@ANCLIZ@@": anc_tool("lizard", "cc"),
        "@@ANCCOG@@": anc_tool("cognitive", "cognitive"),
        "@@ANCNCC@@": anc("config_resolver_N_cc_spearman"),
        "@@ANCXCC@@": anc("crosscheck_our_cc_vs_radon_cc_spearman"),
        "@@ANCXMI@@": anc("crosscheck_our_mi_vs_radon_mi_spearman"),
        "@@READSTATUS@@": _texesc(read_status),
        "@@TOOLROWS@@": tool_rows,
        "@@FIG1@@": _fig_incidental(results, live),
        "@@FIG2@@": _fig_decomposition(results, live),
        "@@FIG3@@": _fig_refactor2d(results, live),
        "@@FIG4@@": _fig_judge(results, live),
        "@@FIG5@@": _fig_contamination(results, live),
        "@@FIG6@@": _fig_crosslang(results, live),
        "@@FIG7@@": _fig_robustness(results, live),
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
    generated = build_tex(results)
    # Once benchmark.tex exists it is HAND-MAINTAINED for submission (prose hardening
    # lives there, not in _PAPER_TEMPLATE). Never clobber it — that would silently
    # revert hand-edits. Write the regenerated draft to a side file so live-run figure
    # wiring is still available to diff/merge in by hand.
    if os.path.exists(PAPER_TEX):
        side = os.path.join(PAPER_DIR, "benchmark.generated.tex")
        with open(side, "w", encoding="utf-8") as f:
            f.write(generated)
        print(f"{PAPER_TEX} exists (hand-maintained); wrote regenerated draft to "
              f"{side} instead — diff/merge manually for the auto-wired figures.")
    else:
        with open(PAPER_TEX, "w", encoding="utf-8") as f:
            f.write(generated)
        print(f"wrote {PAPER_TEX} (NOT compiled)")
    # ship the bibliography next to the paper (only if absent — paper/refs.bib is
    # likewise hand-maintained once present; the two are kept in sync deliberately).
    dst_bib = os.path.join(PAPER_DIR, "refs.bib")
    src_bib = os.path.join(OUT_DIR, "relatedwork", "refs.bib")
    if os.path.exists(src_bib) and not os.path.exists(dst_bib):
        shutil.copyfile(src_bib, dst_bib)
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
    g.add_argument("--baselines", action="store_true",
                   help="score the non-LLM baseline panel (formatter/rule-based/clean); no API")
    g.add_argument("--sweep", metavar="TASK",
                   help="prompt-robustness paraphrase sweep (refactor|comprehend); needs --model")
    g.add_argument("--aggregate", action="store_true", help="merge subagent JSON -> results.json")
    g.add_argument("--report", action="store_true", help="emit paper/benchmark.tex (not compiled)")
    g.add_argument("--regrade", action="store_true",
                   help="re-run graders on STORED raw outputs in out/subagent/*.json (ZERO API)")
    g.add_argument("--pilot", action="store_true",
                   help="tiny live smoke run (parse-success/skip/score dist) before full spend")
    ap.add_argument("--model", help="model id for --batch/--pilot/--regrade")
    ap.add_argument("--family", help="family for --batch/--dry-run/--pilot")
    ap.add_argument("--task", help="restrict --dry-run/--regrade/--pilot to one task")
    ap.add_argument("--split", default="dev", choices=["dev", "test"], help="dataset split")
    ap.add_argument("--k", type=int, default=None,
                    help="override k for --batch/--pilot (default: pre-registered cfg.k_samples=8)")
    ap.add_argument("--limit", type=int, default=1, help="items per task for --pilot")
    ap.add_argument("--max-calls", type=int, default=None,
                    help="refuse a --plan/--batch projected to exceed N API calls")
    ap.add_argument("--max-cost", type=float, default=None,
                    help="refuse a --plan/--batch projected to exceed $X (needs config['prices'])")
    ap.add_argument("--allow-skips", action="store_true",
                    help="--batch: proceed even if a required toolchain is absent (else refuse)")
    ap.add_argument("--parse-floor", type=float, default=0.5,
                    help="--batch: abort early if running parse-success drops below this")
    ap.add_argument("--no-resume", action="store_true",
                    help="--batch: ignore any checkpoint / finalized JSON and re-grade afresh")
    ap.add_argument("--concurrency", type=int, default=None,
                    help="--batch: network fan-out width (overrides config['concurrency']"
                         "[provider]); grading stays bounded at min(cpu,16) regardless")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.dry_run:
        return dry_run(args.task, args.family, split=args.split)
    if args.plan:
        return print_plan(split=args.split, max_calls=args.max_calls,
                          max_cost=args.max_cost)
    if args.baselines:
        return run_baselines(split=args.split)
    if args.regrade:
        return regrade(only_task=args.task, only_model=args.model)
    if args.pilot:
        return pilot(args.task, args.model, args.family, limit=args.limit,
                     split=args.split, k=(args.k or 1))
    if args.sweep:
        if not args.model:
            ap.error("--sweep requires --model")
        return run_sweep(args.sweep, args.model, split=args.split)
    if args.aggregate:
        return aggregate()
    if args.report:
        return write_report()
    if args.batch:
        if not args.model:
            ap.error("--batch requires --model")
        run_batch(args.batch, args.model, args.family, split=args.split, k=args.k,
                  allow_skips=args.allow_skips, parse_floor=args.parse_floor,
                  resume=not args.no_resume, max_calls=args.max_calls,
                  max_cost=args.max_cost, concurrency=args.concurrency)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
