"""Phase 3 — the pluggable LLM client (stdlib only, config-driven).

This is the **one** place the benchmark touches the network, and it is quarantined
here: no SDK, no ``pip install`` — just :mod:`urllib.request` against the Anthropic
Messages API, with the key and model names read from ``bench/config.json`` (never
hard-coded). Always ``temperature=0`` (from config); every call records the model,
the sampling parameters, and a content hash of the prompt (never the full
completion) for traceability.

A resolved key is a non-empty ``api_key`` in the config, else the environment
variable named by ``api_key_env``. When it resolves empty the client is in
**placeholder state**: real calls refuse, but the built-in :data:`MOCK` model still
works so ``--selftest`` / ``--dry-run`` exercise the whole pipeline at zero spend.

The mock is *oracle-backed*: callers pass the known-correct answer (``mock_gold``)
they computed from ``oracle``/the clean baseline, and the mock returns it. That
keeps the client dumb and leaves all ground-truth logic in the graders.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_HERE, "config.json")
CONFIG_EXAMPLE_PATH = os.path.join(_HERE, "config.example.json")

MOCK = "mock"  # the built-in zero-spend model id


class PlaceholderConfigError(RuntimeError):
    """Raised when a real (non-mock) call is attempted with no resolved API key."""


@dataclass(frozen=True)
class Config:
    provider: str
    base_url: str
    anthropic_version: str
    api_key: str
    api_key_env: str
    models_under_test: List[str]
    driver_subagent_model: str
    k_samples: int
    temperature: float
    max_tokens: int
    request_timeout_s: int

    def resolved_key(self) -> str:
        """Non-empty ``api_key`` wins; otherwise the env var named by
        ``api_key_env``; otherwise the empty string (placeholder state)."""
        if self.api_key:
            return self.api_key
        return os.environ.get(self.api_key_env, "") or ""

    @property
    def is_placeholder(self) -> bool:
        return not self.resolved_key()


def load_config(path: str = CONFIG_PATH) -> Config:
    """Load ``bench/config.json``; fall back to the committed example (which is in
    placeholder state) so the mock paths work even before a config is created."""
    src = path if os.path.exists(path) else CONFIG_EXAMPLE_PATH
    with open(src, encoding="utf-8") as f:
        raw = json.load(f)
    return Config(
        provider=raw.get("provider", "anthropic"),
        base_url=raw.get("base_url", "https://api.anthropic.com/v1/messages"),
        anthropic_version=raw.get("anthropic_version", "2023-06-01"),
        api_key=raw.get("api_key", "") or "",
        api_key_env=raw.get("api_key_env", "ANTHROPIC_API_KEY"),
        models_under_test=list(raw.get("models_under_test", [])),
        driver_subagent_model=raw.get("driver_subagent_model", "opus"),
        k_samples=int(raw.get("k_samples", 5)),
        temperature=float(raw.get("temperature", 0)),
        max_tokens=int(raw.get("max_tokens", 2048)),
        request_timeout_s=int(raw.get("request_timeout_s", 120)),
    )


def prompt_hash(system: str, user: str) -> str:
    """A short content hash of the exact prompt (for traceability without logging
    the prompt or the completion)."""
    h = hashlib.sha256()
    h.update(system.encode("utf-8"))
    h.update(b"\x00")
    h.update(user.encode("utf-8"))
    return h.hexdigest()[:16]


# --------------------------------------------------------------------------- #
# completion
# --------------------------------------------------------------------------- #
def complete(model: str, system: str, user: str, *, cfg: Optional[Config] = None,
             mock_gold: Optional[str] = None) -> str:
    """One completion. ``model == MOCK`` returns ``mock_gold`` (no network). A real
    model POSTs to the configured Messages API; it refuses in placeholder state."""
    if model == MOCK:
        if mock_gold is None:
            raise ValueError("mock model requires mock_gold (the known-correct answer)")
        return mock_gold

    cfg = cfg or load_config()
    key = cfg.resolved_key()
    if not key:
        raise PlaceholderConfigError(
            "no API key resolved (api_key empty and "
            f"${cfg.api_key_env} unset): configure bench/config.json, then re-run"
        )
    return _anthropic_messages(model, system, user, cfg, key)


def sample_k(model: str, system: str, user: str, k: int, *,
             cfg: Optional[Config] = None, mock_gold: Optional[str] = None) -> List[str]:
    """Draw ``k`` completions. Model outputs are **not** byte-deterministic even at
    ``temperature=0`` (serving-side variation), so the protocol records k samples
    and reports CIs over them; the mock is deterministic, so its k samples are
    identical (the pipeline shape is what --dry-run proves)."""
    return [complete(model, system, user, cfg=cfg, mock_gold=mock_gold) for _ in range(k)]


def _anthropic_messages(model: str, system: str, user: str, cfg: Config, key: str) -> str:
    payload = {
        "model": model,
        "max_tokens": cfg.max_tokens,
        "temperature": cfg.temperature,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(cfg.base_url, data=data, method="POST")
    req.add_header("content-type", "application/json")
    req.add_header("x-api-key", key)
    req.add_header("anthropic-version", cfg.anthropic_version)
    try:
        with urllib.request.urlopen(req, timeout=cfg.request_timeout_s) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as ex:  # surface status without leaking the key
        detail = ex.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"API HTTP {ex.code}: {detail}") from None
    except urllib.error.URLError as ex:
        raise RuntimeError(f"API connection error: {ex.reason}") from None
    return _extract_text(body)


def _extract_text(body: dict) -> str:
    """Concatenate the text blocks of an Anthropic Messages response."""
    parts = []
    for block in body.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)
