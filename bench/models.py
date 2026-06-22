"""Phase 3 / v2 Phase B — the pluggable, **multi-provider** LLM client (stdlib only).

This is the **one** place the benchmark touches the network, and it is quarantined
here: no SDK, no ``pip install`` — just :mod:`urllib.request` against each vendor's
HTTP API, with keys and model names read from ``bench/config.json`` (never
hard-coded). Always ``temperature=0`` (from config); every call records the model,
the sampling parameters, and a content hash of the prompt (never the full
completion) for traceability, plus the **exact dated snapshot id** the API returns
(for the closed-model-reproducibility threat).

Cross-vendor routing (v2): each model in ``models_under_test`` is mapped by
``model_providers`` to a provider in ``providers``; the supported providers are
``anthropic`` (Messages), ``openai`` (chat/completions), ``google``
(generativelanguage ``generateContent``) and ``openai_compatible`` (OpenRouter-style
chat/completions for open-weights). Each is a thin ``urllib`` request + a
per-provider response parser. A model is *live* iff **its** provider's key resolves
(non-empty ``api_key`` for the default provider, else the env var named by that
provider's ``api_key_env``). When no model's key resolves the client is in
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
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_HERE, "config.json")
CONFIG_EXAMPLE_PATH = os.path.join(_HERE, "config.example.json")

MOCK = "mock"  # the built-in zero-spend model id
MOCK_SNAPSHOT = "mock"  # snapshot id recorded for mock completions

# Provider ids the client speaks.
ANTHROPIC, OPENAI, GOOGLE, OAI_COMPAT = "anthropic", "openai", "google", "openai_compatible"
SUPPORTED_PROVIDERS = (ANTHROPIC, OPENAI, GOOGLE, OAI_COMPAT)


class PlaceholderConfigError(RuntimeError):
    """Raised when a real (non-mock) call is attempted with no resolved API key."""


@dataclass(frozen=True)
class Config:
    # legacy single-provider fields (back-compat: the default provider, anthropic)
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
    # multi-provider routing (v2 Phase B)
    model_providers: Dict[str, str] = field(default_factory=dict)
    providers: Dict[str, dict] = field(default_factory=dict)

    # --- routing ---------------------------------------------------------- #
    def provider_of(self, model: str) -> str:
        """The provider id for a model: ``model_providers`` wins, else the legacy
        default ``provider`` (so a single-vendor config still works)."""
        return self.model_providers.get(model, self.provider)

    def provider_spec(self, provider: str) -> dict:
        """``{base_url, api_key_env, [anthropic_version]}`` for a provider. The
        legacy top-level fields synthesize the ``anthropic`` spec if ``providers``
        does not list it (back-compat)."""
        spec = dict(self.providers.get(provider, {}))
        if provider == ANTHROPIC and not spec:
            spec = {"base_url": self.base_url, "api_key_env": self.api_key_env,
                    "anthropic_version": self.anthropic_version}
        return spec

    def resolve_key(self, provider: str) -> str:
        """The resolved key for a provider: a non-empty top-level ``api_key`` wins
        **only for the default provider**; otherwise the env var named by that
        provider's ``api_key_env``; otherwise '' (unresolved)."""
        if provider == self.provider and self.api_key:
            return self.api_key
        env = self.provider_spec(provider).get("api_key_env", "")
        return os.environ.get(env, "") if env else ""

    def model_is_live(self, model: str) -> bool:
        """A model is live iff its provider's key resolves."""
        return bool(self.resolve_key(self.provider_of(model)))

    def resolved_key(self) -> str:
        """Legacy: the default provider's key (kept for back-compat callers)."""
        if self.api_key:
            return self.api_key
        return os.environ.get(self.api_key_env, "") or ""

    @property
    def is_placeholder(self) -> bool:
        """Global placeholder state = **no** model's provider key resolves (every
        key unresolved). ``--batch`` refuses in this state (directive 1); ``--plan``
        shows it. Per-model refusal uses :meth:`model_is_live`."""
        mut = self.models_under_test or []
        if not mut:
            return not self.resolved_key()
        return not any(self.model_is_live(m) for m in mut)

    def live_models(self) -> List[str]:
        return [m for m in self.models_under_test if self.model_is_live(m)]


def load_config(path: str = CONFIG_PATH) -> Config:
    """Load ``bench/config.json``; fall back to the committed example (in
    placeholder state) so the mock paths work even before a config is created."""
    src = path if os.path.exists(path) else CONFIG_EXAMPLE_PATH
    with open(src, encoding="utf-8") as f:
        raw = json.load(f)
    return Config(
        provider=raw.get("provider", ANTHROPIC),
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
        model_providers=dict(raw.get("model_providers", {})),
        providers=dict(raw.get("providers", {})),
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
# completion (returns text + the dated snapshot id the API reported)
# --------------------------------------------------------------------------- #
def complete(model: str, system: str, user: str, *, cfg: Optional[Config] = None,
             mock_gold: Optional[str] = None) -> Tuple[str, str]:
    """One completion as ``(text, snapshot_id)``. ``model == MOCK`` returns
    ``(mock_gold, 'mock')`` (no network). A real model routes to its provider; it
    refuses in placeholder state for that model."""
    if model == MOCK:
        if mock_gold is None:
            raise ValueError("mock model requires mock_gold (the known-correct answer)")
        return mock_gold, MOCK_SNAPSHOT

    cfg = cfg or load_config()
    provider = cfg.provider_of(model)
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"unsupported provider {provider!r} for model {model!r}; "
                         f"choose from {SUPPORTED_PROVIDERS}")
    key = cfg.resolve_key(provider)
    if not key:
        spec = cfg.provider_spec(provider)
        raise PlaceholderConfigError(
            f"no API key resolved for model {model!r} (provider {provider!r}, env "
            f"${spec.get('api_key_env', '?')}): configure bench/config.json, then re-run"
        )
    spec = cfg.provider_spec(provider)
    if provider == ANTHROPIC:
        return _anthropic_messages(model, system, user, cfg, spec, key)
    if provider in (OPENAI, OAI_COMPAT):
        return _openai_chat(model, system, user, cfg, spec, key)
    if provider == GOOGLE:
        return _google_generate(model, system, user, cfg, spec, key)
    raise ValueError(provider)  # unreachable (guarded above)


def sample_k(model: str, system: str, user: str, k: int, *,
             cfg: Optional[Config] = None,
             mock_gold: Optional[str] = None) -> Tuple[List[str], str]:
    """Draw ``k`` completions as ``(texts, snapshot_id)``. Model outputs are **not**
    byte-deterministic even at ``temperature=0`` (serving-side variation), so the
    protocol records k samples and reports CIs over them; the mock is deterministic,
    so its k samples are identical (the pipeline shape is what --dry-run proves).
    The snapshot id is the same for all k (a property of the served model)."""
    texts: List[str] = []
    snap = MOCK_SNAPSHOT
    for _ in range(k):
        t, snap = complete(model, system, user, cfg=cfg, mock_gold=mock_gold)
        texts.append(t)
    return texts, snap


# --------------------------------------------------------------------------- #
# HTTP plumbing (stdlib urllib; key never leaked into error text)
# --------------------------------------------------------------------------- #
def _post_json(url: str, payload: dict, headers: Dict[str, str], timeout: int) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("content-type", "application/json")
    for h, v in headers.items():
        req.add_header(h, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as ex:  # surface status without leaking the key
        detail = ex.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"API HTTP {ex.code}: {detail}") from None
    except urllib.error.URLError as ex:
        raise RuntimeError(f"API connection error: {ex.reason}") from None


# --- per-provider request + response parsers -------------------------------- #
def _anthropic_messages(model, system, user, cfg, spec, key) -> Tuple[str, str]:
    payload = {"model": model, "max_tokens": cfg.max_tokens,
               "temperature": cfg.temperature, "system": system,
               "messages": [{"role": "user", "content": user}]}
    headers = {"x-api-key": key,
               "anthropic-version": spec.get("anthropic_version", cfg.anthropic_version)}
    body = _post_json(spec["base_url"], payload, headers, cfg.request_timeout_s)
    parts = [b.get("text", "") for b in body.get("content", [])
             if isinstance(b, dict) and b.get("type") == "text"]
    return "".join(parts), str(body.get("model", model))


def _openai_chat(model, system, user, cfg, spec, key) -> Tuple[str, str]:
    """OpenAI and OpenAI-compatible (OpenRouter) chat/completions."""
    payload = {"model": model, "temperature": cfg.temperature,
               "max_tokens": cfg.max_tokens,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}]}
    headers = {"authorization": f"Bearer {key}"}
    body = _post_json(spec["base_url"], payload, headers, cfg.request_timeout_s)
    choices = body.get("choices") or [{}]
    text = (choices[0].get("message") or {}).get("content", "") or ""
    return text, str(body.get("model", model))


def _google_generate(model, system, user, cfg, spec, key) -> Tuple[str, str]:
    """Google generativelanguage ``models/<model>:generateContent`` (key in query)."""
    base = spec["base_url"].rstrip("/")
    url = f"{base}/{model}:generateContent?key={urllib.request.quote(key)}"
    payload = {"systemInstruction": {"parts": [{"text": system}]},
               "contents": [{"role": "user", "parts": [{"text": user}]}],
               "generationConfig": {"temperature": cfg.temperature,
                                    "maxOutputTokens": cfg.max_tokens}}
    body = _post_json(url, payload, {}, cfg.request_timeout_s)
    cands = body.get("candidates") or [{}]
    parts = ((cands[0].get("content") or {}).get("parts")) or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    return text, str(body.get("modelVersion", model))
