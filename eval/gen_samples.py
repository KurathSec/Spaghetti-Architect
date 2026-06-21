#!/usr/bin/env python3
"""Phase 1 — build harder IR samples for the de-optimization evaluation.

The engine supports exactly two composable operations: ``MEMBERSHIP_CHECK``
(``result = target in collection``) and ``KEY_VALUE_LOOKUP``
(``result = pairs.get(key, default)``). "Harder" therefore means *more
operations, larger inputs, and a scale knob* that makes a metric move. This
module is the single source of truth for the sample set and the batch layout;
``run_eval.py`` imports :func:`load` so generation and aggregation never drift.

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


def build(seed: int = SEED) -> Tuple[Dict[str, dict], dict]:
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
    """
    rng = random.Random(seed)
    samples: Dict[str, dict] = {}

    # --- config_resolver: N in {4,8,16,32} (deterministic, no RNG) ---
    cfg_keys: Dict[str, List[str]] = {}
    cfg_bases: List[str] = []
    cfg_scales: Dict[str, int] = {}
    for n in (4, 8, 16, 32):
        ir, keys = _config_resolver(n)
        stem = f"config_resolver_N{n}"
        samples[stem] = ir
        cfg_keys[stem] = keys
        cfg_bases.append(stem)
        cfg_scales[stem] = n

    # --- allowlist: L in {8,32,128} (RNG, drawn in fixed order) ---
    allow_lists: Dict[str, List[int]] = {}
    allow_bases: List[str] = []
    allow_scales: Dict[str, int] = {}
    for length in (8, 32, 128):
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

    meta = {
        "seed": seed,
        "family_order": ["config_resolver", "allowlist", "status_router",
                         "discovery_pipeline", "fsm_transition"],
        "families": {
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
        },
        "variants": variants,
    }
    # batches == families; each batch validates its bases + the repr's variants.
    meta["batches"] = {
        fam: fmeta["bases"] + variants.get(fmeta["repr"], [])
        for fam, fmeta in meta["families"].items()
    }
    return samples, meta


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
