#!/usr/bin/env python3
"""Phase 1 — build harder IR samples for the de-optimization evaluation.

The engine supports four composable operations: ``MEMBERSHIP_CHECK``
(``result = target in collection``), ``KEY_VALUE_LOOKUP``
(``result = pairs.get(key, default)``), ``AGGREGATE`` (``sum``/``min``/``max`` over
an int collection) and ``CONDITIONAL_SELECT`` (an int-comparison branch). "Harder"
therefore means *more operations, larger inputs, and a scale knob* that makes a
metric move. This module is the single source of truth for the sample set and the
batch layout; ``run_eval.py`` imports :func:`load` so generation and aggregation
never drift.

The benchmark (``bench/dataset.py``) calls :func:`build` with ``extended=True`` for
a larger, more diverse mint (finer intrinsic steps + two extra families exercising
``AGGREGATE``/``CONDITIONAL_SELECT``); the default ``extended=False`` path is left
**byte-identical** to the original so the committed ``eval/samples`` reproduce.

Every emitted IR is validated by the real parser (:func:`src.nodes.parser.parse`)
*before* it is written, so a sample can never be structurally invalid. All
content is deterministic: any pseudo-arbitrary value (list contents, probe
choices) is drawn from a single seeded RNG, and the seed is recorded so a
re-run reproduces every byte. See ``deopt_eval_prompt.md`` Phase 1.
"""

from __future__ import annotations

import json
import os
import random
import sys
from typing import Dict, List, Tuple

# --- make `src` importable whether run as a script or as `-m eval.gen_samples` ---
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.nodes.parser import parse  # noqa: E402

# Recorded seed: every pseudo-arbitrary draw below comes from random.Random(SEED),
# so the whole sample set is reproducible. Surfaced into metrics.json.
SEED = 20260619

SAMPLES_DIR = os.path.join(_HERE, "samples")

# Authoring helpers keep the parser's §5.2 constraints obviously satisfied:
#   * input names are valid identifiers,
#   * arrays/maps are homogeneous,
#   * a MEMBERSHIP target shares the collection element type,
#   * a LOOKUP key_var is a string and default shares the pairs value type,
#   * the result derives from `pairs`; we mirror `pairs` into the map_name input.


def _ir(module: str, inputs: dict, operations: list) -> dict:
    return {
        "version": "1.0",
        "module_name": module,
        "inputs": inputs,
        "operations": operations,
    }


def _membership(collection: str, target: str, result: str) -> dict:
    return {
        "operation": "MEMBERSHIP_CHECK",
        "collection_name": collection,
        "target_var": target,
        "result_var": result,
    }


def _lookup(map_name: str, key_var: str, result: str, pairs: dict, default) -> dict:
    return {
        "operation": "KEY_VALUE_LOOKUP",
        "map_name": map_name,
        "key_var": key_var,
        "result_var": result,
        "pairs": dict(pairs),
        "default_value": default,
    }


def _aggregate(collection: str, mode: str, result: str) -> dict:
    return {
        "operation": "AGGREGATE",
        "mode": mode,                 # sum / min / max
        "collection_name": collection,
        "result_var": result,
    }


def _conditional(subject: str, comparator: str, compare_value: int,
                 then_value, else_value, result: str) -> dict:
    return {
        "operation": "CONDITIONAL_SELECT",
        "subject_var": subject,
        "comparator": comparator,
        "compare_value": compare_value,
        "then_value": then_value,
        "else_value": else_value,
        "result_var": result,
    }


# --------------------------------------------------------------------------- #
# Family builders
# --------------------------------------------------------------------------- #
def _config_resolver(n: int) -> Tuple[dict, List[str]]:
    """1x LOOKUP with N known keys -> cascade SPAGH_005, cyclomatic proportional to N."""
    pairs = {f"r{i:02d}": f"host_{i:02d}" for i in range(n)}
    keys = list(pairs)
    base_key = keys[n // 2]  # a key that hits a cascade arm
    ir = _ir(
        f"config_resolver_N{n}",
        {"config_db": dict(pairs), "lookup_key": base_key},
        [_lookup("config_db", "lookup_key", "resolved", pairs, "DEFAULT_HOST")],
    )
    return ir, keys


def _allowlist(length: int, rng: random.Random) -> Tuple[dict, List[int]]:
    """1x MEMBERSHIP over a length-L int list -> manual index 006 + len-recompute 010."""
    lst = sorted(rng.sample(range(1, length * 5), length))
    base_probe = lst[length // 2]  # present
    ir = _ir(
        f"allowlist_L{length}",
        {"allow_list": list(lst), "probe": base_probe},
        [_membership("allow_list", "probe", "is_allowed")],
    )
    return ir, lst


def _status_router() -> Tuple[dict, List[str]]:
    """1x LOOKUP, ~24 keys -> a realistic large switch-cascade (fixed size)."""
    table = {
        "200": "success", "201": "success", "202": "success", "204": "success",
        "301": "redirect", "302": "redirect", "303": "redirect", "304": "redirect",
        "307": "redirect", "308": "redirect",
        "400": "client_error", "401": "client_error", "403": "client_error",
        "404": "client_error", "405": "client_error", "409": "client_error",
        "410": "client_error", "422": "client_error", "429": "client_error",
        "500": "server_error", "501": "server_error", "502": "server_error",
        "503": "server_error", "504": "server_error",
    }  # 4 + 6 + 9 + 5 = 24 keys
    ir = _ir(
        "status_router",
        {"status_table": dict(table), "incoming": "404"},
        [_lookup("status_table", "incoming", "route", table, "unrouted")],
    )
    return ir, list(table)


def _discovery_pipeline(rng: random.Random) -> dict:
    """6-8 chained ops (mix) -> multi-op program size & SPAGH density."""
    ports = sorted(rng.sample(range(1, 9000), 12))
    codes = sorted(rng.sample(range(100, 600), 10))
    hosts = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"]
    flags = ["beta", "canary", "dark_launch", "gradual", "internal"]
    service_map = {"auth": "auth-svc", "cache": "redis", "db": "postgres",
                   "queue": "rabbit", "search": "elastic"}
    env_map = {"dev": "10.0.0.1", "staging": "10.0.1.1", "prod": "10.0.2.1",
               "canary": "10.0.3.1"}
    inputs = {
        "ports": ports, "port_probe": ports[3],
        "hosts": hosts, "host_probe": "charlie",
        "codes": codes, "code_probe": codes[2],
        "feature_flags": flags, "flag_probe": "canary",
        "service_map": dict(service_map), "svc_key": "db", "svc_key2": "search",
        "env_map": dict(env_map), "env_key": "prod",
    }
    operations = [
        _membership("ports", "port_probe", "port_open"),
        _lookup("service_map", "svc_key", "service_name", service_map, "unknown_svc"),
        _membership("hosts", "host_probe", "host_known"),
        _lookup("env_map", "env_key", "env_host", env_map, "0.0.0.0"),
        _membership("feature_flags", "flag_probe", "flag_on"),
        _membership("codes", "code_probe", "code_seen"),
        _lookup("service_map", "svc_key2", "service_name2", service_map, "unknown_svc"),
    ]
    return _ir("discovery_pipeline", inputs, operations)


def _fsm_transition() -> dict:
    """3-4 chained LOOKUPs -> a state-transition table modelled as lookups."""
    tbl_start = {"idle": "running", "paused": "running", "stopped": "running"}
    tbl_pause = {"running": "paused"}
    tbl_resume = {"paused": "running"}
    tbl_stop = {"idle": "stopped", "running": "stopped", "paused": "stopped"}
    inputs = {
        "current_state": "idle",
        "tbl_start": dict(tbl_start), "tbl_pause": dict(tbl_pause),
        "tbl_resume": dict(tbl_resume), "tbl_stop": dict(tbl_stop),
    }
    operations = [
        _lookup("tbl_start", "current_state", "next_start", tbl_start, "ERR"),
        _lookup("tbl_pause", "current_state", "next_pause", tbl_pause, "ERR"),
        _lookup("tbl_resume", "current_state", "next_resume", tbl_resume, "ERR"),
        _lookup("tbl_stop", "current_state", "next_stop", tbl_stop, "ERR"),
    ]
    return _ir("fsm_transition", inputs, operations)


# --- extended families (v2 Phase C): exercise AGGREGATE & CONDITIONAL_SELECT --- #
def _agg_stats(w: int, rng: random.Random) -> Tuple[dict, List[int]]:
    """AGGREGATE family (knob ``W`` = collection length): sum/max/min over a
    length-W int list -> manual-index (006) + len-recompute (010) scale with W. The
    list is RNG-drawn, so a fresh seed re-mints it (Tier A numeric novelty)."""
    readings = [rng.randint(1, w * 10) for _ in range(w)]
    ir = _ir(
        f"agg_stats_W{w}",
        {"readings": list(readings)},
        [_aggregate("readings", "sum", "total"),
         _aggregate("readings", "max", "peak"),
         _aggregate("readings", "min", "trough")],
    )
    return ir, readings


def _threshold_select(t: int, rng: random.Random) -> Tuple[dict, List[str]]:
    """CONDITIONAL_SELECT family (knob ``T`` = number of int-comparison branches):
    a chain of T independent threshold selects -> cascading (005) + yoda (011)
    scale with T. The subject ints are RNG-drawn (Tier A numeric novelty); each
    branch keeps a distinct then/else label so backends render T real branches."""
    inputs: Dict[str, int] = {}
    ops: List[dict] = []
    for i in range(t):
        name = f"metric_{i:02d}"
        inputs[name] = rng.randint(0, 100)
        ops.append(_conditional(name, ">=", 50, f"high_{i:02d}", f"low_{i:02d}",
                                f"verdict_{i:02d}"))
    ir = _ir(f"threshold_select_T{t}", inputs, ops)
    return ir, list(inputs)


# --------------------------------------------------------------------------- #
# Variants: same structure, different input *scalar(s)* to exercise branches
# (early arm / late arm / default-miss / present / absent). Feeds the Phase 2.A
# differential-robustness gate. Pairs/maps/lists are never changed, so the only
# source delta between variants is the one input literal -> a clean branch probe.
# --------------------------------------------------------------------------- #
def _variant(base_ir: dict, stem: str, overrides: Dict[str, object]) -> dict:
    ir = json.loads(json.dumps(base_ir))  # deep copy via JSON round-trip
    ir["module_name"] = stem
    for name, value in overrides.items():
        assert name in ir["inputs"], f"{stem}: override targets unknown input {name!r}"
        ir["inputs"][name] = value
    return ir


def build(seed: int = SEED, extended: bool = False) -> Tuple[Dict[str, dict], dict]:
    """Return ``(samples, meta)``.

    ``samples`` maps a file stem -> IR dict (base samples *and* ``*_v0..v4``
    variant files). ``meta`` describes families, batches, base list, scale knobs
    and the variant groupings so ``run_eval`` can aggregate without guessing.

    ``seed`` defaults to the public :data:`SEED`, which reproduces the committed
    dev sample set byte-for-byte. Passing a different seed re-draws every
    RNG-sourced literal (the ``allowlist`` integer lists and ``discovery_pipeline``
    port/code lists and their probes) — the basis for the benchmark's private,
    contamination-resistant held-out split (see ``bench/dataset.py``). The
    RNG-free families (``config_resolver``/``status_router``/``fsm_transition``)
    are seed-independent here; ``bench/`` re-mints *their* string literals with a
    structure-preserving salt so the held-out split is novel across all families.

    ``extended=True`` (used by the benchmark, v2 Phase C) mints a larger, more
    diverse set: finer intrinsic steps for ``config_resolver``/``allowlist`` and two
    extra families (``agg_stats`` exercising AGGREGATE, ``threshold_select``
    exercising CONDITIONAL_SELECT). The default ``extended=False`` path is left
    byte-identical so the committed ``eval/samples`` reproduce.
    """
    rng = random.Random(seed)
    samples: Dict[str, dict] = {}

    # --- config_resolver: N (deterministic, no RNG) ---
    cfg_keys: Dict[str, List[str]] = {}
    cfg_bases: List[str] = []
    cfg_scales: Dict[str, int] = {}
    cfg_N = (4, 8, 12, 16, 24, 32) if extended else (4, 8, 16, 32)
    for n in cfg_N:
        ir, keys = _config_resolver(n)
        stem = f"config_resolver_N{n}"
        samples[stem] = ir
        cfg_keys[stem] = keys
        cfg_bases.append(stem)
        cfg_scales[stem] = n

    # --- allowlist: L (RNG, drawn in fixed order) ---
    allow_lists: Dict[str, List[int]] = {}
    allow_bases: List[str] = []
    allow_scales: Dict[str, int] = {}
    allow_L = (8, 16, 32, 64, 128) if extended else (8, 32, 128)
    for length in allow_L:
        ir, lst = _allowlist(length, rng)
        stem = f"allowlist_L{length}"
        samples[stem] = ir
        allow_lists[stem] = lst
        allow_bases.append(stem)
        allow_scales[stem] = length

    # --- single-sample families ---
    status_ir, _status_keys = _status_router()
    samples["status_router"] = status_ir
    samples["discovery_pipeline"] = _discovery_pipeline(rng)
    samples["fsm_transition"] = _fsm_transition()

    # --- extended-only families: AGGREGATE (agg_stats) + CONDITIONAL_SELECT
    #     (threshold_select). Drawn AFTER the default families so the default RNG
    #     sequence (hence eval/samples) is untouched. ---
    agg_bases: List[str] = []
    agg_scales: Dict[str, int] = {}
    thr_bases: List[str] = []
    thr_scales: Dict[str, int] = {}
    thr_names: Dict[str, List[str]] = {}
    agg_lists: Dict[str, List[int]] = {}
    if extended:
        for w in (8, 32):
            ir, vals = _agg_stats(w, rng)
            stem = f"agg_stats_W{w}"
            samples[stem] = ir
            agg_lists[stem] = vals
            agg_bases.append(stem)
            agg_scales[stem] = w
        for t in (2, 4):
            ir, names = _threshold_select(t, rng)
            stem = f"threshold_select_T{t}"
            samples[stem] = ir
            thr_names[stem] = names
            thr_bases.append(stem)
            thr_scales[stem] = t

    # --- variants for one representative per family ---
    variants: Dict[str, List[str]] = {}

    def add_variants(repr_stem: str, override_list: List[Dict[str, object]]) -> None:
        stems: List[str] = []
        for i, ov in enumerate(override_list):
            vstem = f"{repr_stem}_v{i}"
            samples[vstem] = _variant(samples[repr_stem], vstem, ov)
            stems.append(vstem)
        variants[repr_stem] = stems

    # config_resolver_N8: first arm / mid arm / last arm / default-miss / another hit
    k8 = cfg_keys["config_resolver_N8"]
    add_variants("config_resolver_N8", [
        {"lookup_key": k8[0]},
        {"lookup_key": k8[3]},
        {"lookup_key": k8[-1]},
        {"lookup_key": "r99"},        # not a known key -> default
        {"lookup_key": k8[5]},
    ])

    # allowlist_L32: present early / mid / last / absent-below / absent-above
    l32 = allow_lists["allowlist_L32"]
    add_variants("allowlist_L32", [
        {"probe": l32[0]},
        {"probe": l32[len(l32) // 2]},
        {"probe": l32[-1]},
        {"probe": 0},                 # below the pool [1, L*5) -> absent
        {"probe": 32 * 5 + 1},        # above the pool -> absent
    ])

    # status_router: success / client_error / server_error / miss / redirect
    add_variants("status_router", [
        {"incoming": "200"},
        {"incoming": "404"},
        {"incoming": "503"},
        {"incoming": "600"},          # unknown status -> default
        {"incoming": "301"},
    ])

    # discovery_pipeline: all-hit / all-miss / three mixed branch profiles
    add_variants("discovery_pipeline", [
        {},  # v0 == base inputs (all hitting)
        {"port_probe": 999999, "host_probe": "zzz", "code_probe": -1,
         "flag_probe": "none", "svc_key": "none", "svc_key2": "none",
         "env_key": "none"},                                   # v1 all miss
        {"host_probe": "zzz", "svc_key": "auth", "env_key": "dev"},  # v2 mix
        {"flag_probe": "internal", "svc_key2": "none", "env_key": "canary"},  # v3 mix
        {"svc_key": "queue", "svc_key2": "cache"},             # v4 mix
    ])

    # fsm_transition: idle / running / paused / stopped / unknown-state(miss)
    add_variants("fsm_transition", [
        {"current_state": "idle"},
        {"current_state": "running"},
        {"current_state": "paused"},
        {"current_state": "stopped"},
        {"current_state": "galaxy"},  # not in any table -> default everywhere
    ])

    if extended:
        # agg_stats_W32: reading distributions that move sum/min/max differentially
        base = agg_lists["agg_stats_W32"]
        add_variants("agg_stats_W32", [
            {"readings": list(reversed(base))},      # same multiset (sum/min/max equal)
            {"readings": sorted(base)},              # sorted (same multiset)
            {"readings": base + [max(base) * 3]},    # a spike -> max & sum jump
            {"readings": [base[0]] * len(base)},     # flat -> min == max == base[0]
            {"readings": [v + 1 for v in base]},     # shifted -> sum/min/max all move
        ])
        # threshold_select_T4: flip branches around the >=50 threshold
        t4 = thr_names["threshold_select_T4"]
        add_variants("threshold_select_T4", [
            {t4[0]: 49},                 # just below -> low
            {t4[0]: 50},                 # exactly at -> high (>=)
            {t4[-1]: 0},                 # far below
            {t4[-1]: 100},               # far above
            {n: 50 for n in t4},         # all at the boundary -> all high
        ])

    families = {
        "config_resolver": {"bases": cfg_bases, "repr": "config_resolver_N8",
                            "knob": "N", "scales": cfg_scales},
        "allowlist": {"bases": allow_bases, "repr": "allowlist_L32",
                      "knob": "L", "scales": allow_scales},
        "status_router": {"bases": ["status_router"], "repr": "status_router",
                          "knob": None, "scales": {}},
        "discovery_pipeline": {"bases": ["discovery_pipeline"],
                               "repr": "discovery_pipeline", "knob": None, "scales": {}},
        "fsm_transition": {"bases": ["fsm_transition"], "repr": "fsm_transition",
                           "knob": None, "scales": {}},
    }
    family_order = ["config_resolver", "allowlist", "status_router",
                    "discovery_pipeline", "fsm_transition"]
    if extended:
        families["agg_stats"] = {"bases": agg_bases, "repr": "agg_stats_W32",
                                 "knob": "W", "scales": agg_scales}
        families["threshold_select"] = {"bases": thr_bases,
                                        "repr": "threshold_select_T4",
                                        "knob": "T", "scales": thr_scales}
        family_order = family_order + ["agg_stats", "threshold_select"]

    meta = {
        "seed": seed,
        "family_order": family_order,
        "families": families,
        "variants": variants,
    }
    # batches == families; each batch validates its bases + the repr's variants.
    meta["batches"] = {
        fam: fmeta["bases"] + variants.get(fmeta["repr"], [])
        for fam, fmeta in meta["families"].items()
    }
    return samples, meta


def build_heldout_tiers(seed: int) -> Tuple[Dict[str, dict], Dict[str, str]]:
    """Mint **private** held-out structures (never committed) for the contamination
    novelty axis (v2 Phase C). Two tiers beyond Tier A (literal re-mint):

    * **Tier B — structural held-out:** op-chain *shapes* the public families never
      use (e.g. membership co-chained with aggregate on one list), so an adversary
      who memorised the public structures still cannot have seen these.
    * **Tier C — distribution shift:** compositions outside the public distribution
      — a far deeper op-chain and out-of-range scales (``N=256``/``L=512`` vs the
      public ``N<=32``/``L<=128``).

    All literals are RNG-drawn from the held-out ``seed`` (decorrelated from
    :func:`build`'s stream). Returns ``(samples, tier_of)``; the caller
    (:func:`bench.dataset.mint`) validates every IR with ``parse()``."""
    rng = random.Random((seed ^ 0x5BD1E995) & 0xFFFFFFFF)
    samples: Dict[str, dict] = {}
    tier: Dict[str, str] = {}

    # ---- Tier B: novel op-chain shapes ---------------------------------- #
    vals = sorted(rng.sample(range(1, 400), 16))
    samples["tierB_member_agg"] = _ir(
        "tierB_member_agg", {"series": list(vals), "needle": vals[rng.randrange(16)]},
        [_membership("series", "needle", "present"),
         _aggregate("series", "sum", "series_sum"),
         _aggregate("series", "max", "series_max")])
    tier["tierB_member_agg"] = "B"

    rewards = {"gold": "premium", "silver": "standard", "bronze": "basic"}
    samples["tierB_select_lookup"] = _ir(
        "tierB_select_lookup",
        {"level": rng.randint(0, 100), "tier_key": "gold", "rewards": dict(rewards)},
        [_conditional("level", ">=", rng.randint(20, 80), "high", "low", "band"),
         _lookup("rewards", "tier_key", "reward", rewards, "none")])
    tier["tierB_select_lookup"] = "B"

    nums = sorted(rng.sample(range(1, 500), 10))
    regions = {"us": "use1", "eu": "euw1", "ap": "apse1"}
    samples["tierB_quad"] = _ir(
        "tierB_quad",
        {"data": list(nums), "x": nums[rng.randrange(10)], "score": rng.randint(0, 100),
         "regions": dict(regions), "rk": "eu"},
        [_aggregate("data", "min", "lo"),
         _membership("data", "x", "seen"),
         _conditional("score", ">", 50, "hi", "lo2", "scoreband"),
         _lookup("regions", "rk", "zone", regions, "unknown")])
    tier["tierB_quad"] = "B"

    # ---- Tier C: distribution shift ------------------------------------- #
    deep = sorted(rng.sample(range(1, 2000), 24))
    regions2 = {"a": "ra", "b": "rb", "c": "rc", "d": "rd"}
    samples["tierC_deep_chain"] = _ir(
        "tierC_deep_chain",
        {"nums": list(deep), "p1": deep[rng.randrange(24)], "p2": 999999,
         "s1": rng.randint(0, 100), "s2": rng.randint(-50, 50), "s3": rng.randint(0, 200),
         "regions": dict(regions2), "k1": "a", "k2": "d"},
        [_aggregate("nums", "sum", "r_sum"),
         _aggregate("nums", "max", "r_max"),
         _aggregate("nums", "min", "r_min"),
         _membership("nums", "p1", "r_m1"),
         _membership("nums", "p2", "r_m2"),
         _conditional("s1", ">=", 50, "a1", "b1", "r_c1"),
         _conditional("s2", ">", 0, "pos", "neg", "r_c2"),
         _conditional("s3", "<", 100, "lt", "ge", "r_c3"),
         _lookup("regions", "k1", "r_l1", regions2, "none"),
         _lookup("regions", "k2", "r_l2", regions2, "none"),
         _aggregate("nums", "sum", "r_sum2"),
         _membership("nums", "p1", "r_m3")])
    tier["tierC_deep_chain"] = "C"

    cfg_ir, _ = _config_resolver(256)          # far beyond public N<=32
    cfg_ir["module_name"] = "tierC_huge_cfg"
    samples["tierC_huge_cfg"] = cfg_ir
    tier["tierC_huge_cfg"] = "C"

    allow_ir, _ = _allowlist(512, rng)         # far beyond public L<=128
    allow_ir["module_name"] = "tierC_huge_allow"
    samples["tierC_huge_allow"] = allow_ir
    tier["tierC_huge_allow"] = "C"

    return samples, tier


def load(seed: int = SEED) -> Tuple[Dict[str, dict], dict]:
    """Public accessor used by run_eval (build is deterministic, so this is cheap)."""
    return build(seed)


def sample_path(stem: str) -> str:
    return os.path.join(SAMPLES_DIR, f"{stem}.json")


def main() -> int:
    os.makedirs(SAMPLES_DIR, exist_ok=True)
    samples, meta = build()
    n_ok = 0
    for stem, ir in samples.items():
        parse(ir)  # raises IRValidationError if invalid -> never write a bad sample
        with open(sample_path(stem), "w", encoding="utf-8") as f:
            json.dump(ir, f, indent=2)
            f.write("\n")
        n_ok += 1
    bases = sum(len(m["bases"]) for m in meta["families"].values())
    varc = sum(len(v) for v in meta["variants"].values())
    print(f"seed={meta['seed']}  wrote {n_ok} IR files to {SAMPLES_DIR}")
    print(f"  base samples: {bases}   variant files: {varc}   total: {n_ok}")
    for fam, members in meta["batches"].items():
        print(f"  batch {fam:18s} {len(members):2d} samples: {', '.join(members)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
