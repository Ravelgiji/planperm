"""One OpenAI call site, with failover across models.

OpenAI's request allowance is **per model**, not per account: on the free tier
each model carries its own 50-requests-per-day cap. So a 429 on one model says
nothing about the next, and the cheapest recovery is simply to ask a different
one.

This module holds the chain and the memory of which models are spent. An agent
calls `complete()` or `parse()` and gets an answer from whichever model is
still willing, without needing to know that a fallback happened.

Two deliberate choices:

  * **Exhaustion is remembered**, so the first 429 costs one wasted request and
    every later call skips that model. Without this, a run of ten extractions
    would each pay the same failure.
  * **The order is configurable** through `PLANPERM_LLM_MODELS`, because which
    model to prefer is a cost and quality decision, not a code one.

State is process-local. Streamlit reruns the script on every interaction but
keeps the process, so a model marked spent stays spent for the session; a
restart re-probes, which is correct because the caps reset on their own.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from core.env import load_env

log = logging.getLogger(__name__)

# Preference order. GPT-5 leads because this account gets 500 requests a day
# on it against 50 on the 4.x models - a tenfold difference that matters more
# than any quality gap for this workload.
DEFAULT_CHAIN = (
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    "gpt-4o-mini",
    "gpt-4.1-mini",
    "gpt-4.1",
)

# How long to treat a model as spent before trying it again. The daily cap
# resets on a rolling window, so this only has to be long enough to stop the
# same call retrying it repeatedly within one session.
_COOLDOWN_SECONDS = 15 * 60

# model -> unix time when it may be retried
_exhausted: dict[str, float] = {}


def model_chain() -> list[str]:
    """The models to try, in order.

    `PLANPERM_LLM_MODELS` overrides the default as a comma-separated list.
    `PLANPERM_LLM_MODEL` still works and is honoured first, so an existing
    single-model configuration keeps behaving as before while gaining fallback.
    """
    load_env()

    configured = os.environ.get("PLANPERM_LLM_MODELS", "").strip()
    chain = (
        [m.strip() for m in configured.split(",") if m.strip()]
        if configured else list(DEFAULT_CHAIN)
    )

    preferred = os.environ.get("PLANPERM_LLM_MODEL", "").strip()
    if preferred:
        chain = [preferred] + [m for m in chain if m != preferred]

    return chain


def _available(model: str) -> bool:
    until = _exhausted.get(model)
    if until is None:
        return True
    if time.time() >= until:
        del _exhausted[model]
        return True
    return False


def mark_exhausted(model: str, seconds: float = _COOLDOWN_SECONDS) -> None:
    """Record that a model is rate-limited, so later calls skip it."""
    _exhausted[model] = time.time() + seconds
    log.info("Model %s is rate-limited; skipping it for %.0f min.", model, seconds / 60)


def active_model() -> str:
    """The model a call would use right now."""
    chain = model_chain()
    for model in chain:
        if _available(model):
            return model
    return chain[0] if chain else DEFAULT_CHAIN[0]


def status() -> list[dict[str, Any]]:
    """Per-model availability, for display in the UI."""
    rows = []
    now = time.time()
    for model in model_chain():
        until = _exhausted.get(model)
        rows.append({
            "model": model,
            "available": _available(model),
            "retry_in_minutes": max(0, round((until - now) / 60)) if until else 0,
        })
    return rows


def _client():
    """A configured OpenAI client, or None when no key is set."""
    load_env()
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None

    try:
        from openai import OpenAI
    except ImportError:
        log.warning("openai is not installed.")
        return None

    base_url = os.environ.get("PLANPERM_LLM_BASE_URL")
    try:
        return OpenAI(api_key=api_key, **({"base_url": base_url} if base_url else {}))
    except Exception as exc:                      # noqa: BLE001
        log.warning("Could not build the OpenAI client: %s", exc)
        return None


def _is_rate_limit(exc: Exception) -> bool:
    """Is this the kind of failure another model might not have?

    A 429 or an insufficient-quota error is worth retrying elsewhere. A bad
    request or an auth failure is not - every model would reject it, so trying
    the whole chain would just be slow.
    """
    status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status_code in (429, "429", "rate_limit_exceeded", "insufficient_quota"):
        return True

    text = str(exc).lower()
    return "rate limit" in text or "429" in text or "insufficient_quota" in text


def _call(kind: str, messages: list[dict[str, str]], **kwargs: Any) -> tuple[Any, str] | None:
    """Try each available model in turn. Returns (result, model) or None."""
    client = _client()
    if client is None:
        return None

    chain = model_chain()
    tried: list[str] = []

    for model in chain:
        if not _available(model):
            continue

        tried.append(model)
        try:
            if kind == "parse":
                completion = client.chat.completions.parse(
                    model=model, messages=messages, **kwargs
                )
                return completion.choices[0].message.parsed, model

            completion = client.chat.completions.create(
                model=model, messages=messages, **kwargs
            )
            return (completion.choices[0].message.content or ""), model

        except Exception as exc:                  # noqa: BLE001
            if _is_rate_limit(exc):
                mark_exhausted(model)
                continue
            # Not a quota problem, so the next model would fail the same way.
            log.warning("Model %s failed (not a rate limit): %s", model, exc)
            return None

    log.warning("Every model in the chain is rate-limited: %s", ", ".join(tried) or "none")
    return None


def complete(messages: list[dict[str, str]], **kwargs: Any) -> tuple[str, str] | None:
    """Free-form completion. Returns (text, model_used), or None if all failed."""
    result = _call("create", messages, **kwargs)
    if result is None:
        return None
    text, model = result
    return (text or ""), model


def parse(messages: list[dict[str, str]], response_format: Any, **kwargs: Any):
    """Structured output. Returns (parsed, model_used), or None if all failed."""
    return _call("parse", messages, response_format=response_format, **kwargs)


def label() -> str:
    """One line describing the backend, for the UI."""
    load_env()
    if not os.environ.get("OPENAI_API_KEY"):
        return "no API key - deterministic mode"

    spent = [row["model"] for row in status() if not row["available"]]
    line = active_model()
    if spent:
        line += f" ({len(spent)} model(s) rate-limited)"
    return line


# ── Model options for the UI ─────────────────────────────────────────────────

# Curated, with the daily request cap this account actually gets. The GPT-5
# family has ten times the allowance of the 4.x models, which is why it leads.
MODEL_OPTIONS = (
    ("gpt-5.4-mini", "GPT-5.4 mini - 500/day, recommended"),
    ("gpt-5.4-nano", "GPT-5.4 nano - 500/day, fastest"),
    ("gpt-5.5", "GPT-5.5 - 500/day, strongest"),
    ("gpt-4o-mini", "GPT-4o mini - 50/day"),
    ("gpt-4.1-mini", "GPT-4.1 mini - 50/day"),
    ("gpt-4.1", "GPT-4.1 - 50/day"),
)

# Models that reject `max_tokens` and want `max_completion_tokens` instead.
_NEW_PARAM_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def needs_completion_tokens(model: str) -> bool:
    return str(model).startswith(_NEW_PARAM_PREFIXES)


_compat_installed = False


def install_compatibility() -> None:
    """Translate `max_tokens` for models that no longer accept it.

    Every agent builds its own OpenAI client and passes `max_tokens`, which the
    GPT-5 family rejects outright with a 400 - it wants
    `max_completion_tokens`. Offering GPT-5 in the model picker would therefore
    break all seven call sites.

    Rather than edit each one, the SDK's `create` is wrapped once, here, and the
    parameter is renamed only for models that require it. A wrapper is a strong
    choice and deserves justifying: the alternative was seven near-identical
    edits across files a teammate is actively changing, which would have been
    more disruptive and no clearer. Everything else passes through untouched,
    and the wrapper is installed once and is idempotent.
    """
    global _compat_installed
    if _compat_installed:
        return

    try:
        from openai.resources.chat.completions import Completions
    except Exception as exc:                      # noqa: BLE001 - never break startup
        log.warning("Could not install model compatibility shim: %s", exc)
        return

    original = Completions.create

    def create(self, *args, **kwargs):
        model = kwargs.get("model", "")
        if needs_completion_tokens(model) and "max_tokens" in kwargs:
            kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
        return original(self, *args, **kwargs)

    Completions.create = create
    _compat_installed = True
    log.info("Model parameter compatibility shim installed.")
