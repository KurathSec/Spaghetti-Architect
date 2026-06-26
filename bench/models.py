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
import random
import socket
import sys
import threading
import time
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


# --------------------------------------------------------------------------- #
# token-usage accumulator (thread-safe; concurrency-ready actual-$ accounting)
# --------------------------------------------------------------------------- #
# A real run fans out across a ThreadPoolExecutor, so the per-call ``usage`` blocks
# the providers return are summed into ONE lock-guarded module-level dict instead of
# threading a return value through every call site. The mock never reaches a provider
# parser, so it contributes nothing. ``run_batch`` snapshots+resets this once per
# batch and turns it into a true ``est_usd`` (the model under test is a REASONING model
# whose hidden reasoning tokens dominate cost, so projected per-call cost is unreliable
# and only the returned usage tells the truth).
_USAGE_LOCK = threading.Lock()


def _zero_usage() -> Dict[str, int]:
    return {"prompt_tokens": 0, "completion_tokens": 0,
            "reasoning_tokens": 0, "n_usage_calls": 0}


_USAGE: Dict[str, int] = _zero_usage()


def _accumulate_usage(prompt: int, completion: int, reasoning: int) -> None:
    """Add one call's token counts to the shared accumulator (thread-safe)."""
    with _USAGE_LOCK:
        _USAGE["prompt_tokens"] += int(prompt or 0)
        _USAGE["completion_tokens"] += int(completion or 0)
        _USAGE["reasoning_tokens"] += int(reasoning or 0)
        _USAGE["n_usage_calls"] += 1


def reset_usage() -> Dict[str, int]:
    """Atomically read the accumulated usage and reset it to zero. ``run_batch`` calls
    this once at the end of a batch to record that batch's true token totals."""
    with _USAGE_LOCK:
        snap = dict(_USAGE)
        _USAGE.update(_zero_usage())
    return snap


def _record_anthropic_usage(body: dict) -> None:
    u = body.get("usage") or {}
    # Anthropic counts input/output; extended-thinking tokens are billed as output and
    # are included in output_tokens (no separate reasoning field), so reasoning=0 here.
    _accumulate_usage(u.get("input_tokens", 0), u.get("output_tokens", 0), 0)


def _record_openai_usage(body: dict) -> None:
    u = body.get("usage") or {}
    details = u.get("completion_tokens_details") or {}
    _accumulate_usage(u.get("prompt_tokens", 0), u.get("completion_tokens", 0),
                      details.get("reasoning_tokens", 0))


def _record_google_usage(body: dict) -> None:
    u = body.get("usageMetadata") or {}
    # Gemini reports prompt/candidates/total + (for thinking models) thoughtsTokenCount.
    # candidatesTokenCount excludes thoughts, so completion = candidates + thoughts to
    # keep the "all generated tokens" sense consistent with the OpenAI lane.
    thoughts = u.get("thoughtsTokenCount", 0)
    cand = u.get("candidatesTokenCount", 0)
    _accumulate_usage(u.get("promptTokenCount", 0), (cand or 0) + (thoughts or 0),
                      thoughts)


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
    # transient-failure retry (exponential backoff w/ jitter; see _post_json).
    # Defaulted so legacy/back-compat construction without these stays valid.
    max_retries: int = 5
    retry_base_s: float = 2.0
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
        max_retries=int(raw.get("max_retries", 5)),
        retry_base_s=float(raw.get("retry_base_s", 2.0)),
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
# Statuses that warrant a retry during a high-volume run: rate-limit (429) and
# transient server/gateway errors. Everything else (400/401/403/404/422, …) is a
# permanent error we surface immediately — retrying it only burns time and money.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    """Parse a ``Retry-After`` header into seconds. Supports the integer-seconds
    form and the HTTP-date form; returns ``None`` if absent/unparseable so the
    caller falls back to the computed backoff. Never raises."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))  # delta-seconds form
    except ValueError:
        pass
    try:  # HTTP-date form (RFC 7231): seconds until that instant, floored at 0
        from email.utils import parsedate_to_datetime  # noqa: PLC0415 (stdlib, lazy)
        when = parsedate_to_datetime(value)
        if when is None:
            return None
        return max(0.0, when.timestamp() - time.time())
    except (TypeError, ValueError, OverflowError):
        return None


def _backoff_seconds(attempt: int, base: float) -> float:
    """Exponential backoff with full jitter for retry ``attempt`` (0-indexed):
    ``base * 2**attempt`` plus a random fraction of one base interval, capped so a
    large ``base``/attempt cannot wedge the run. Deterministic only the cap is."""
    span = base * (2.0 ** attempt)
    return min(span + random.uniform(0.0, base), 60.0)


def _post_json(url: str, payload: dict, headers: Dict[str, str], timeout: int,
               *, max_retries: int = 5, retry_base_s: float = 2.0,
               model: str = "?") -> dict:
    """POST ``payload`` as JSON and return the decoded response.

    Transient failures (HTTP 429/5xx in :data:`RETRYABLE_STATUS`, connection
    errors, socket timeouts) are retried with exponential backoff + jitter, up to
    ``max_retries`` attempts; a ``Retry-After`` header on a 429 overrides the
    computed sleep. Permanent errors (4xx other than 429) are surfaced at once.
    The API key lives only in ``headers``/``url`` and is **never** placed into any
    log line or exception. ``model`` is used solely for the stderr retry trace.
    """
    data = json.dumps(payload).encode("utf-8")
    attempts = max(1, int(max_retries))
    last_exc: Optional[BaseException] = None
    for attempt in range(attempts):
        # Build a fresh Request per attempt (urllib mutates/consumes them).
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("content-type", "application/json")
        for h, v in headers.items():
            req.add_header(h, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as ex:  # status known; key never in message
            detail = ex.read().decode("utf-8", "replace")[:500]
            err = RuntimeError(f"API HTTP {ex.code}: {detail}")
            if ex.code not in RETRYABLE_STATUS or attempt >= attempts - 1:
                raise err from None  # permanent, or out of attempts
            retry_after = (_parse_retry_after(ex.headers.get("Retry-After"))
                           if ex.code == 429 else None)
            sleep_s = retry_after if retry_after is not None \
                else _backoff_seconds(attempt, retry_base_s)
            _log_retry(model, attempt + 1, attempts, f"HTTP {ex.code}", sleep_s)
            last_exc = err
        except (urllib.error.URLError, socket.timeout, TimeoutError) as ex:
            # Connection reset / DNS / read timeout: transient, retry. URLError
            # wraps the cause; .reason carries no secret (url/key are elsewhere).
            reason = getattr(ex, "reason", ex)
            err = RuntimeError(f"API connection error: {reason}")
            if attempt >= attempts - 1:
                raise err from None
            sleep_s = _backoff_seconds(attempt, retry_base_s)
            _log_retry(model, attempt + 1, attempts, f"conn:{reason}", sleep_s)
            last_exc = err
        time.sleep(sleep_s)
    # Unreachable in practice (loop returns or raises), but keep the invariant
    # explicit so a future refactor can't silently fall through to None.
    raise last_exc or RuntimeError("API request failed after retries")


def _log_retry(model: str, attempt: int, total: int, status: str,
               sleep_s: float) -> None:
    """One-line stderr trace per retry: model id, attempt, status/reason, sleep.
    Deliberately carries **no** API key and **no** request/response payload."""
    print(f"[models] retry model={model} attempt={attempt}/{total} "
          f"status={status} sleep={sleep_s:.2f}s", file=sys.stderr, flush=True)


# --- per-provider request + response parsers -------------------------------- #
def _anthropic_messages(model, system, user, cfg, spec, key) -> Tuple[str, str]:
    payload = {"model": model, "max_tokens": cfg.max_tokens,
               "temperature": cfg.temperature, "system": system,
               "messages": [{"role": "user", "content": user}]}
    headers = {"x-api-key": key,
               "anthropic-version": spec.get("anthropic_version", cfg.anthropic_version)}
    body = _post_json(spec["base_url"], payload, headers, cfg.request_timeout_s,
                      max_retries=cfg.max_retries, retry_base_s=cfg.retry_base_s,
                      model=model)
    _record_anthropic_usage(body)
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
    body = _post_json(spec["base_url"], payload, headers, cfg.request_timeout_s,
                      max_retries=cfg.max_retries, retry_base_s=cfg.retry_base_s,
                      model=model)
    _record_openai_usage(body)
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
    body = _post_json(url, payload, {}, cfg.request_timeout_s,
                      max_retries=cfg.max_retries, retry_base_s=cfg.retry_base_s,
                      model=model)
    _record_google_usage(body)
    cands = body.get("candidates") or [{}]
    parts = ((cands[0].get("content") or {}).get("parts")) or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    return text, str(body.get("modelVersion", model))
