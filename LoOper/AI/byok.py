"""byok.py — BYOK provider runtime, ported from ``utils/byok-runtime`` (TS).

One Python module for user-owned cloud keys, local model servers and CLI
tools, behind a single generation API.  Ported behavior (not shape):

- ``PROVIDER_MANIFEST`` — the provider table from ``provider-manifest.ts``:
  base URLs, auth style, model normalization, env-var key fallbacks.
- Every cloud provider (Anthropic included) speaks the OpenAI-compatible
  chat-completions subset the TS runtime uses; Ollama speaks its native
  ``/api/generate`` wire; CLI providers (Claude/Codex) spawn local commands.
- 429 rate-limit retries (2, ``Retry-After`` honored, backoff capped at 10s),
  friendly error messages, model listing and connection tests.
- Host-owned storage (the TS runtime deliberately persists nothing): settings
  live in ``byok.json`` inside :func:`AI.runtime_paths.get_runtime_dir`, are
  sanitized on load exactly like ``stored-settings.ts``, and API keys are
  never logged and never written into ``AI/config.json``.

The ``llamacpp`` entry is a pseudo-provider owned by the local gateway (the
built-in engine); the ``pool`` entry is the future P2P compute pool, present
so the UI and roles can show it, but disabled ("coming soon").
"""

import json
import logging
import os
import re
import subprocess
import tempfile
import time
from typing import Any, Dict, Iterator, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# --------------------------------------------------------------- exceptions


class ByokProviderError(Exception):
    """A provider call failed with a user-readable message."""


class ByokRateLimitError(ByokProviderError):
    """The provider said "slow down"; ``retry_after_ms`` when reported."""

    def __init__(self, message: str, retry_after_ms: Optional[int] = None):
        super().__init__(message)
        self.retry_after_ms = retry_after_ms


# ---------------------------------------------------------------- manifest

#: Local pseudo-provider served by the gateway's built-in engine.
LOCAL_ENGINE = "llamacpp"

#: Future P2P compute pool — visible but disabled until it ships.
POOL_PROVIDER = "pool"

POOL_COMING_SOON_MSG = (
    "Compute Pool is coming soon — it is not available in this build yet."
)

#: Cloud provider ids (OpenAI-compatible chat completions + /models).
CLOUD_PROVIDER_IDS = (
    "anthropic",
    "openai",
    "google",
    "xai",
    "openrouter",
    "groq",
    "mistral",
    "deepseek",
    "deepinfra",
    "together",
    "fireworks",
)

#: Local model-server ids.
LOCAL_SERVER_IDS = ("ollama", "lm-studio")

#: Local CLI ids (spawn a command; no HTTP).
CLI_PROVIDER_IDS = ("codex-cli", "claude-cli")

#: Manifest order mirrors ``provider-manifest.ts``.
PROVIDER_MANIFEST: Dict[str, Dict[str, Any]] = {
    "anthropic": {
        "family": "cloud",
        "label": "Anthropic (Claude)",
        "vendor": "Anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "auth": "anthropic-api-key",
        "model_normalization": "default",
        "api_key_env_vars": ("ANTHROPIC_API_KEY",),
    },
    "openai": {
        "family": "cloud",
        "label": "OpenAI (ChatGPT)",
        "vendor": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "auth": "bearer",
        "model_normalization": "default",
        "api_key_env_vars": ("OPENAI_API_KEY",),
    },
    "google": {
        "family": "cloud",
        "label": "Google (Gemini)",
        "vendor": "Google",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "auth": "bearer",
        "model_normalization": "default",
        "api_key_env_vars": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    },
    "xai": {
        "family": "cloud",
        "label": "xAI (Grok)",
        "vendor": "xAI",
        "base_url": "https://api.x.ai/v1",
        "auth": "bearer",
        "model_normalization": "default",
        "api_key_env_vars": ("XAI_API_KEY",),
    },
    "openrouter": {
        "family": "cloud",
        "label": "OpenRouter",
        "vendor": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "auth": "bearer",
        "model_normalization": "name-fallback",
        "api_key_env_vars": ("OPENROUTER_API_KEY",),
    },
    "groq": {
        "family": "cloud",
        "label": "Groq",
        "vendor": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "auth": "bearer",
        "model_normalization": "default",
        "api_key_env_vars": ("GROQ_API_KEY",),
    },
    "mistral": {
        "family": "cloud",
        "label": "Mistral",
        "vendor": "Mistral",
        "base_url": "https://api.mistral.ai/v1",
        "auth": "bearer",
        "model_normalization": "default",
        "api_key_env_vars": ("MISTRAL_API_KEY",),
    },
    "deepseek": {
        "family": "cloud",
        "label": "DeepSeek",
        "vendor": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "auth": "bearer",
        "model_normalization": "default",
        "api_key_env_vars": ("DEEPSEEK_API_KEY",),
    },
    "deepinfra": {
        "family": "cloud",
        "label": "DeepInfra",
        "vendor": "DeepInfra",
        "base_url": "https://api.deepinfra.com/v1/openai",
        "auth": "bearer",
        "model_normalization": "default",
        "api_key_env_vars": ("DEEPINFRA_TOKEN",),
    },
    "together": {
        "family": "cloud",
        "label": "Together AI",
        "vendor": "Together AI",
        "base_url": "https://api.together.ai/v1",
        "auth": "bearer",
        "model_normalization": "default",
        "model_listing_direct": True,
        "api_key_env_vars": ("TOGETHER_API_KEY",),
    },
    "fireworks": {
        "family": "cloud",
        "label": "Fireworks AI",
        "vendor": "Fireworks AI",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "auth": "bearer",
        "model_normalization": "default",
        "api_key_env_vars": ("FIREWORKS_API_KEY",),
    },
    "ollama": {
        "family": "local-server",
        "label": "Ollama",
        "vendor": "Ollama",
        "base_url": "http://localhost:11434",
        "auth": "none",
        "model_normalization": "default",
    },
    "lm-studio": {
        "family": "local-server",
        "label": "LM Studio",
        "vendor": "LM Studio",
        "base_url": "http://localhost:1234/v1",
        "auth": "none",
        "model_normalization": "default",
    },
    "codex-cli": {
        "family": "cli",
        "label": "Codex CLI",
        "vendor": "Codex CLI",
        "base_url": "",
        "auth": "none",
        "model_normalization": "default",
    },
    "claude-cli": {
        "family": "cli",
        "label": "Claude CLI",
        "vendor": "Claude CLI",
        "base_url": "",
        "auth": "none",
        "model_normalization": "default",
    },
    POOL_PROVIDER: {
        "family": "pool",
        "label": "Compute Pool (P2P)",
        "vendor": "Compute Pool",
        "base_url": "",
        "auth": "none",
        "model_normalization": "default",
        "coming_soon": True,
    },
}

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_LM_STUDIO_URL = "http://localhost:1234/v1"
LM_STUDIO_API_KEY = "lm-studio"

#: HTTP timeout (connect, read) — generous: local GGUFs generate slowly.
HTTP_TIMEOUT = (15, 600)
STATUS_TIMEOUT = 30
CLI_TIMEOUT = 120
CLI_STATUS_TIMEOUT = 20

#: 429 handling (matches the TS retry loop).
RATE_LIMIT_RETRIES = 2
RATE_LIMIT_RETRY_MS = 1000
MAX_RATE_LIMIT_RETRY_MS = 10_000

#: Role keys: which provider serves each capability (cross-device routing).
ROLE_KEYS = ("chat", "embeddings", "vision")

#: Providers the builder can pick for a role (manifest order + local engine).
ROLE_PROVIDER_IDS = (LOCAL_ENGINE, "ollama", "lm-studio") + CLOUD_PROVIDER_IDS + \
    CLI_PROVIDER_IDS


def is_provider_id(value: Any) -> bool:
    """A known manifest id (``pool`` included; ``llamacpp`` excluded — it is
    an engine, not a BYOK provider, and the caller resolves it itself)."""
    return isinstance(value, str) and value in PROVIDER_MANIFEST


def is_resolvable_provider_id(value: Any) -> bool:
    """Ids valid in a model prefix / role: manifest ids + ``llamacpp``."""
    return value == LOCAL_ENGINE or is_provider_id(value)


def is_local_engine(value: Any) -> bool:
    """True for the built-in llama.cpp engine's spellings (the in-process
    GGUF path).  Everything else (``ollama``, cloud ids, ...) is served by a
    server — the gateway for BYOK providers."""
    return _as_str(value).strip().lower() in (
        "", LOCAL_ENGINE, "llama.cpp", "llama_cpp")


def split_model_ref(model: Any) -> Tuple[str, str]:
    """``('<provider>', '<bare model>')`` when the model carries a provider
    prefix.

    Only known provider ids count, so ``llama3.2:1b`` and Windows GGUF
    paths (``D:\\...gguf``) pass through untouched.
    """
    text = _as_str(model)
    if ":" not in text:
        return "", text
    head, rest = text.split(":", 1)
    head = head.strip()
    if is_resolvable_provider_id(head):
        return head, rest.strip()
    return "", text


def join_model_ref(provider_id: Any, model: Any) -> str:
    """Route a model to a provider by prefix (idempotent).

    The gateway dispatches on ``provider:model``, so an engine selection
    persists as that prefix — the engine is then derivable from the model
    and no extra stored field is needed.  A model that already names a
    provider keeps its own (more specific) prefix.
    """
    provider = _as_str(provider_id).strip()
    text = _as_str(model)
    if not provider or provider == LOCAL_ENGINE or not text:
        return text
    existing, _rest = split_model_ref(text)
    if existing:
        return text
    return "%s:%s" % (provider, text)


def normalize_engine_model(engine: Any, model: Any) -> Tuple[str, str]:
    """``(engine, model)`` with the engine following the model's prefix.

    A model like ``openai:gpt-4o-mini`` self-routes: engine consumers that
    branch on the engine (llama.cpp vs the gateway leg) get the provider so
    the prefixed model reaches the provider through the local gateway.
    """
    prefix, _bare = split_model_ref(model)
    if prefix:
        return prefix, _as_str(model)
    return _as_str(engine).strip().lower(), _as_str(model)


def provider_choices(include_disabled: bool = True) -> List[Dict[str, Any]]:
    """UI list: (id, label, family, coming_soon) in pick order."""
    choices: List[Dict[str, Any]] = [
        {"id": LOCAL_ENGINE, "label": "llama.cpp (local engine)",
         "family": "local-server", "coming_soon": False},
    ]
    for pid, entry in PROVIDER_MANIFEST.items():
        coming = bool(entry.get("coming_soon"))
        if coming and not include_disabled:
            continue
        choices.append({
            "id": pid, "label": entry.get("label", pid),
            "family": entry.get("family", "cloud"), "coming_soon": coming,
        })
    return choices


# ----------------------------------------------------------------- settings

_SETTINGS_FILENAME = "byok.json"

PROVIDER_DEFAULTS: Dict[str, Any] = {
    "api_key": "",
    "credential_saved": False,
    "url": "",
    "command": "",
    "model": "",
    "available_models": [],
    "has_fetched_models": False,
    "model_refresh_message": "",
    #: Explicit parallelism switch: enabled providers are all live at once.
    "enabled": False,
}


def default_settings() -> Dict[str, Any]:
    return {
        "selected_provider": "ollama",
        "providers": {},
        "roles": {k: {"provider": "", "model": ""} for k in ROLE_KEYS},
        "verification": {},
    }


def settings_path() -> str:
    """``byok.json`` inside the durable runtime dir (never the install dir)."""
    try:
        from AI.runtime_paths import get_runtime_dir
        base = get_runtime_dir()
    except Exception:
        base = tempfile.gettempdir()
    return os.path.join(base, _SETTINGS_FILENAME)


def _as_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _str_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, str)]


def parse_settings(value: Any) -> Dict[str, Any]:
    """Sanitize any stored blob into the settings shape (never raises).

    Mirrors ``parseByokStoredSettings``: unknown providers are dropped,
    every field falls back to its default, roles accept only known ids.
    """
    record = value if isinstance(value, dict) else {}
    out = default_settings()

    selected = record.get("selected_provider")
    out["selected_provider"] = selected if is_provider_id(selected) else "ollama"

    providers_in = record.get("providers")
    if isinstance(providers_in, dict):
        for pid, entry in providers_in.items():
            if not is_provider_id(pid) or not isinstance(entry, dict):
                continue
            prov = dict(PROVIDER_DEFAULTS)
            prov["api_key"] = _as_str(entry.get("api_key"))
            prov["credential_saved"] = bool(entry.get("credential_saved"))
            # ``enabled`` is the explicit parallelism switch.  Configs written
            # before it existed fall back to "looks configured", so a stored
            # key/model keeps working unchanged (no forced re-toggle).
            if "enabled" in entry:
                prov["enabled"] = bool(entry.get("enabled"))
            else:
                prov["enabled"] = bool(
                    entry.get("credential_saved") or entry.get("api_key")
                    or entry.get("available_models") or entry.get("model"))
            prov["url"] = _as_str(entry.get("url"))
            prov["command"] = _as_str(entry.get("command"))
            prov["model"] = _as_str(entry.get("model"))
            prov["available_models"] = _str_list(entry.get("available_models"))
            prov["has_fetched_models"] = bool(entry.get("has_fetched_models"))
            prov["model_refresh_message"] = _as_str(
                entry.get("model_refresh_message"))
            out["providers"][pid] = prov

    roles_in = record.get("roles")
    if isinstance(roles_in, dict):
        for key in ROLE_KEYS:
            entry = roles_in.get(key)
            if not isinstance(entry, dict):
                continue
            provider = entry.get("provider")
            out["roles"][key] = {
                "provider": provider if is_resolvable_provider_id(provider) else "",
                "model": _as_str(entry.get("model")),
            }

    verification_in = record.get("verification")
    if isinstance(verification_in, dict):
        for pid, entry in verification_in.items():
            if not is_provider_id(pid) or not isinstance(entry, dict):
                continue
            out["verification"][pid] = {
                "ok": bool(entry.get("ok")),
                "message": _as_str(entry.get("message")),
                "model_id": _as_str(entry.get("model_id")),
                "tested_at": _as_str(entry.get("tested_at")),
            }

    return out


def load_settings() -> Dict[str, Any]:
    """Read ``byok.json`` (sanitized); missing/corrupt → defaults."""
    try:
        with open(settings_path(), "r", encoding="utf-8") as f:
            return parse_settings(json.load(f))
    except Exception:
        return default_settings()


def save_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize + persist; returns the sanitized dict (never raises)."""
    clean = parse_settings(settings)
    try:
        path = settings_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(clean, f, indent=2, ensure_ascii=False)
    except Exception:
        logger.warning("Failed to save byok settings", exc_info=True)
    return clean


def get_provider_settings(settings: Optional[Dict[str, Any]],
                          provider_id: str) -> Dict[str, Any]:
    """One provider's stored fields (defaults when absent)."""
    settings = settings if isinstance(settings, dict) else {}
    stored = (settings.get("providers") or {}).get(provider_id)
    prov = dict(PROVIDER_DEFAULTS)
    if isinstance(stored, dict):
        prov.update(stored)
    return prov


def get_role(settings: Optional[Dict[str, Any]], role_key: str) -> Dict[str, str]:
    """``{"provider": ..., "model": ...}`` for a role ('' when unset)."""
    settings = settings if isinstance(settings, dict) else {}
    entry = (settings.get("roles") or {}).get(role_key)
    if not isinstance(entry, dict):
        return {"provider": "", "model": ""}
    return {
        "provider": _as_str(entry.get("provider")),
        "model": _as_str(entry.get("model")),
    }


def resolve_model(provider_id: str, model: str,
                  settings: Optional[Dict[str, Any]] = None) -> str:
    """Explicit model wins; else the provider's stored model; else ''."""
    settings = load_settings() if settings is None else settings
    m = _as_str(model).strip()
    if m:
        return m
    return _as_str(get_provider_settings(settings, provider_id).get("model")).strip()


def prefixed_model_names(settings: Optional[Dict[str, Any]] = None) -> List[str]:
    """``"<provider>:<model>"`` ids for the app's model pickers.

    Every model dropdown in the app reads the gateway's ``/models`` (via the
    model cache); listing the configured providers' models there makes each
    picker a provider+model selector, and the gateway's ``provider:model``
    prefix dispatch routes whatever the user picked.
    """
    settings = load_settings() if settings is None else settings
    names: List[str] = []
    for pid, entry in PROVIDER_MANIFEST.items():
        if entry.get("coming_soon"):
            continue
        prov = get_provider_settings(settings, pid)
        # Enabled providers are all available in parallel; there is no single
        # "default" provider any more.
        if not prov.get("enabled"):
            continue
        models = [str(m) for m in (prov.get("available_models") or []) if m]
        stored = _as_str(prov.get("model")).strip()
        if stored and stored not in models:
            models.append(stored)
        for mid in models:
            name = f"{pid}:{mid}"
            if name not in names:
                names.append(name)
    for role_key in ROLE_KEYS:
        role = get_role(settings, role_key)
        pid, model = role["provider"], role["model"]
        if pid and pid != LOCAL_ENGINE and model:
            name = f"{pid}:{model}"
            if name not in names:
                names.append(name)
    return names


# ------------------------------------------------------------------ helpers


def _join_url(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


def _normalize_url(url: str, default: str, vendor: str,
                   ensure_v1: bool = False) -> str:
    """Port of ``normalizeOllamaUrl`` / ``normalizeLmStudioBaseUrl``."""
    candidate = (url or "").strip() or default
    if not re.match(r"^https?://", candidate, re.I):
        raise ByokProviderError(f"{vendor} URL must be a valid http(s) URL.")
    parsed = re.sub(r"/+$", "", candidate)
    if ensure_v1:
        # LM Studio: a bare host gets /v1 appended.
        tail = re.sub(r"^https?://[^/]+", "", parsed)
        if tail in ("", "/"):
            parsed += "/v1"
    return parsed


def _describe_error(vendor: str, message: str) -> str:
    """Port of ``describeError``: actionable, user-readable failures."""
    msg = str(message or "")
    if re.search(r"api[\s_-]?key|authenticat|401|403", msg, re.I):
        return (f"{vendor} rejected the API key. Check the API key in "
                f"AI Settings.")
    if re.search(r"429|rate.?limit|quota", msg, re.I):
        return f"{vendor} rate limit hit. Wait a moment and try again."
    if re.search(r"network|fetch|ENOTFOUND|ECONN|timeout|timed out|"
                 r"connection|refused", msg, re.I):
        return f"Could not reach {vendor}. Check your connection."
    return f"{vendor} request failed: {msg}"


def _retry_after_ms(response: requests.Response) -> Optional[int]:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0, int(float(raw) * 1000))
    except Exception:
        return None


def _request_with_retry(
    make_request,
    vendor: str,
    retries: int = RATE_LIMIT_RETRIES,
) -> requests.Response:
    """Two 429 retries with ``Retry-After`` honored (TS parity)."""
    last: Optional[requests.Response] = None
    for attempt in range(retries + 1):
        try:
            response = make_request()
        except requests.exceptions.RequestException as e:
            raise ByokProviderError(_describe_error(vendor, str(e)))
        if response.status_code != 429:
            return response
        last = response
        if attempt >= retries:
            break
        wait_ms = _retry_after_ms(response) or RATE_LIMIT_RETRY_MS * (2 ** attempt)
        time.sleep(min(wait_ms, MAX_RATE_LIMIT_RETRY_MS) / 1000.0)
    wait_ms = _retry_after_ms(last) if last is not None else None
    raise ByokRateLimitError(
        _describe_error(vendor, "429 rate limit"),
        wait_ms,
    )


def _detail_from(response: requests.Response) -> str:
    try:
        return (response.text or "").strip()[:400]
    except Exception:
        return ""


def _api_key_for(provider_id: str,
                 settings: Optional[Dict[str, Any]]) -> str:
    """Stored key → env-var fallback (TS ``BYOK_API_KEY_ENV_VARS``) → ''."""
    stored = _as_str(get_provider_settings(settings, provider_id).get("api_key"))
    if stored.strip():
        return stored.strip()
    entry = PROVIDER_MANIFEST.get(provider_id) or {}
    for var in entry.get("api_key_env_vars", ()) or ():
        value = os.environ.get(var)
        if value and value.strip():
            return value.strip()
    return ""


def _require_api_key(provider_id: str,
                     settings: Optional[Dict[str, Any]]) -> str:
    entry = PROVIDER_MANIFEST.get(provider_id) or {}
    if entry.get("family") == "local-server":
        return LM_STUDIO_API_KEY if provider_id == "lm-studio" else ""
    key = _api_key_for(provider_id, settings)
    if not key:
        vendor = entry.get("vendor", provider_id)
        raise ByokProviderError(
            f"Paste the {vendor} API key in AI Settings (providers tab).")
    return key


def _openai_headers(provider_id: str, api_key: str) -> Dict[str, str]:
    entry = PROVIDER_MANIFEST.get(provider_id) or {}
    if entry.get("auth") == "anthropic-api-key":
        return {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _base_url_for(provider_id: str, prov: Dict[str, Any]) -> str:
    entry = PROVIDER_MANIFEST.get(provider_id) or {}
    if provider_id == "ollama":
        return _normalize_url(prov.get("url", ""), DEFAULT_OLLAMA_URL, "Ollama")
    if provider_id == "lm-studio":
        return _normalize_url(prov.get("url", ""), DEFAULT_LM_STUDIO_URL,
                              "LM Studio", ensure_v1=True)
    return str(entry.get("base_url") or "")


def _require_model(provider_id: str, model: str) -> str:
    if model:
        return model
    entry = PROVIDER_MANIFEST.get(provider_id) or {}
    raise ByokProviderError(
        f"Choose a {entry.get('label', provider_id)} model in AI Settings.")


# ----------------------------------------------------- OpenAI-compatible wire


def _openai_messages(prompt: str, instructions: str,
                     images: Optional[List[str]]) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = []
    if instructions:
        messages.append({"role": "system", "content": instructions})
    if images:
        parts: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        for img in images:
            data = str(img)
            if not data.startswith("data:"):
                data = f"data:image/png;base64,{data}"
            parts.append({"type": "image_url", "image_url": {"url": data}})
        messages.append({"role": "user", "content": parts})
    else:
        messages.append({"role": "user", "content": prompt})
    return messages


def _prompt_with_schema(prompt: str, json_schema: str) -> str:
    if not json_schema:
        return prompt
    return (f"{prompt}\n\nRespond with ONLY a valid JSON object matching this "
            f"schema (no markdown fences, no extra text):\n{json_schema}")


def _openai_generate(
    provider_id: str,
    prov: Dict[str, Any],
    model: str,
    prompt: str,
    instructions: str,
    images: Optional[List[str]],
    json_schema: str,
    settings: Optional[Dict[str, Any]],
) -> str:
    entry = PROVIDER_MANIFEST.get(provider_id) or {}
    vendor = entry.get("vendor", provider_id)
    if images and entry.get("auth") == "anthropic-api-key":
        raise ByokProviderError(
            f"{vendor} does not accept images through this path yet.")
    api_key = _require_api_key(provider_id, settings)
    base = _base_url_for(provider_id, prov)
    body: Dict[str, Any] = {
        "model": model,
        "messages": _openai_messages(
            _prompt_with_schema(prompt, json_schema), instructions, images),
    }
    response = _request_with_retry(
        lambda: requests.post(
            _join_url(base, "chat/completions"),
            json=body,
            headers=_openai_headers(provider_id, api_key),
            timeout=HTTP_TIMEOUT,
        ),
        vendor,
    )
    if response.status_code >= 400:
        raise ByokProviderError(_describe_error(
            vendor,
            f"HTTP {response.status_code}: {_detail_from(response)}"))
    try:
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") for part in content
                if isinstance(part, dict) and part.get("type") == "text")
        return str(content or "")
    except ByokProviderError:
        raise
    except Exception as e:
        raise ByokProviderError(
            f"{vendor} returned an unexpected response shape ({e}).")


def _openai_list_models(provider_id: str, prov: Dict[str, Any],
                        settings: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
    entry = PROVIDER_MANIFEST.get(provider_id) or {}
    vendor = entry.get("vendor", provider_id)
    api_key = _require_api_key(provider_id, settings)
    base = _base_url_for(provider_id, prov)
    response = _request_with_retry(
        lambda: requests.get(
            _join_url(base, "models"),
            headers=_openai_headers(provider_id, api_key),
            timeout=STATUS_TIMEOUT,
        ),
        vendor,
    )
    if response.status_code >= 400:
        raise ByokProviderError(_describe_error(
            vendor, f"HTTP {response.status_code}: {_detail_from(response)}"))
    try:
        payload = response.json()
        raw = payload.get("data", payload) if isinstance(payload, dict) else payload
        options: List[Dict[str, str]] = []
        name_fallback = entry.get("model_normalization") == "name-fallback"
        for item in raw or []:
            if not isinstance(item, dict):
                continue
            model_id = _as_str(item.get("id")).strip()
            if not model_id:
                continue
            if name_fallback:
                label = _as_str(item.get("name")).strip() or model_id
            else:
                label = (_as_str(item.get("display_name")).strip()
                         or _as_str(item.get("name")).strip() or model_id)
            options.append({"id": model_id, "label": label})
        return options
    except ByokProviderError:
        raise
    except Exception as e:
        raise ByokProviderError(f"{vendor} model listing failed: {e}")


def _openai_stream(
    provider_id: str,
    prov: Dict[str, Any],
    model: str,
    prompt: str,
    instructions: str,
    settings: Optional[Dict[str, Any]],
) -> Iterator[str]:
    entry = PROVIDER_MANIFEST.get(provider_id) or {}
    vendor = entry.get("vendor", provider_id)
    api_key = _require_api_key(provider_id, settings)
    base = _base_url_for(provider_id, prov)
    body = {
        "model": model,
        "messages": _openai_messages(prompt, instructions, None),
        "stream": True,
    }
    try:
        response = requests.post(
            _join_url(base, "chat/completions"),
            json=body,
            headers=_openai_headers(provider_id, api_key),
            timeout=HTTP_TIMEOUT,
            stream=True,
        )
    except requests.exceptions.RequestException as e:
        raise ByokProviderError(_describe_error(vendor, str(e)))
    if response.status_code >= 400:
        raise ByokProviderError(_describe_error(
            vendor, f"HTTP {response.status_code}: {_detail_from(response)}"))
    try:
        for line in response.iter_lines():
            if not line:
                continue
            text = line.decode("utf-8", "ignore").strip()
            if not text.startswith("data:"):
                continue
            payload = text[5:].strip()
            if payload == "[DONE]":
                return
            try:
                obj = json.loads(payload)
                delta = (obj.get("choices") or [{}])[0].get("delta") or {}
                chunk = delta.get("content")
                if chunk:
                    yield str(chunk)
            except Exception:
                continue
    finally:
        try:
            response.close()
        except Exception:
            pass


# ------------------------------------------------------------- Ollama wire


def _ollama_generate(
    prov: Dict[str, Any],
    model: str,
    prompt: str,
    instructions: str,
    json_schema: str,
    settings: Optional[Dict[str, Any]],
) -> str:
    base = _base_url_for("ollama", prov)
    body: Dict[str, Any] = {
        "model": model,
        "prompt": _prompt_with_schema(prompt, json_schema),
        "stream": False,
    }
    if instructions:
        body["system"] = instructions
    if json_schema:
        body["format"] = "json"
    try:
        response = requests.post(
            _join_url(base, "api/generate"), json=body, timeout=HTTP_TIMEOUT)
    except requests.exceptions.RequestException as e:
        raise ByokProviderError(
            "Ollama server unreachable. Check the URL and that Ollama is "
            "running.")
    if response.status_code >= 400:
        hint = ""
        if response.status_code == 404:
            hint = " — model not found; run `ollama pull <model>`"
        elif response.status_code == 500:
            hint = (" — server error; the model may have failed to load "
                    "(often out of memory)")
        raise ByokProviderError(
            f"Ollama request failed (HTTP {response.status_code}){hint}: "
            f"{_detail_from(response)}")
    try:
        body_out = response.json()
        text = body_out.get("response")
        if not isinstance(text, str):
            raise ValueError("missing 'response'")
        return text
    except Exception:
        raise ByokProviderError("Ollama returned an unexpected response shape.")


def _ollama_stream(
    prov: Dict[str, Any],
    model: str,
    prompt: str,
    instructions: str,
    settings: Optional[Dict[str, Any]],
) -> Iterator[str]:
    base = _base_url_for("ollama", prov)
    body: Dict[str, Any] = {"model": model, "prompt": prompt, "stream": True}
    if instructions:
        body["system"] = instructions
    try:
        response = requests.post(
            _join_url(base, "api/generate"), json=body,
            timeout=HTTP_TIMEOUT, stream=True)
    except requests.exceptions.RequestException as e:
        raise ByokProviderError(
            "Ollama server unreachable. Check the URL and that Ollama is "
            "running.")
    if response.status_code >= 400:
        raise ByokProviderError(
            f"Ollama request failed (HTTP {response.status_code}): "
            f"{_detail_from(response)}")
    try:
        for line in response.iter_lines():
            if not line:
                continue
            try:
                chunk = json.loads(line.decode("utf-8", "ignore"))
            except Exception:
                continue
            piece = chunk.get("response")
            if piece:
                yield str(piece)
    finally:
        try:
            response.close()
        except Exception:
            pass


def _ollama_list_models(prov: Dict[str, Any],
                        settings: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
    base = _base_url_for("ollama", prov)
    try:
        response = requests.get(_join_url(base, "api/tags"), timeout=STATUS_TIMEOUT)
    except requests.exceptions.RequestException as e:
        raise ByokProviderError(
            "Ollama server unreachable. Check the URL and that Ollama is "
            "running.")
    if response.status_code >= 400:
        raise ByokProviderError(
            f"Ollama model listing failed (HTTP {response.status_code}).")
    try:
        payload = response.json()
        names = [str(m.get("name")) for m in (payload.get("models") or [])
                 if isinstance(m, dict) and m.get("name")]
        return [{"id": n, "label": n} for n in names]
    except Exception as e:
        raise ByokProviderError(f"Ollama model listing failed: {e}")


# ---------------------------------------------------------------- CLI wire


_CLAUDE_CLI_ENV = {
    "CLAUDE_CODE_DISABLE_AGENT_VIEW": "1",
    "CLAUDE_CODE_DISABLE_ARTIFACT": "1",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    "CLAUDE_CODE_DISABLE_BUNDLED_SKILLS": "1",
    "CLAUDE_CODE_DISABLE_WORKFLOWS": "1",
    "CLAUDE_CODE_SAFE_MODE": "1",
    "CLAUDE_CODE_SKIP_PROMPT_HISTORY": "1",
    "DISABLE_AUTOUPDATER": "1",
}

_CLAUDE_CLI_AUTH_MESSAGE = (
    "Claude CLI is not authenticated. Run `claude auth login` in your "
    "terminal, then try again."
)

_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


def _subprocess_flags() -> Dict[str, Any]:
    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}


def _run_cli(command: str, args: List[str], prompt: str,
             timeout: int, env_extra: Optional[Dict[str, str]] = None) -> str:
    """Run a local CLI with the prompt on stdin; returns stdout."""
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    try:
        proc = subprocess.run(
            [command] + args,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=tempfile.gettempdir(),
            env=env,
            **_subprocess_flags(),
        )
    except FileNotFoundError:
        raise ByokProviderError(
            f"{command} was not found. Install it (or set its full path in "
            f"AI Settings).")
    except subprocess.TimeoutExpired:
        raise ByokProviderError(f"{command} timed out after {timeout}s.")
    except Exception as e:
        raise ByokProviderError(f"{command} failed to start: {e}")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:400]
        raise ByokProviderError(
            f"{command} exited with code {proc.returncode}"
            f"{': ' + detail if detail else '.'}")
    return proc.stdout or ""


def _claude_cli_output(stdout: str) -> str:
    """Port of ``extractClaudeCliOutput`` (structured JSON → text)."""
    trimmed = (stdout or "").strip()
    if not trimmed:
        return ""

    def _from_content(value: Any) -> str:
        if isinstance(value, str):
            return value
        if not isinstance(value, list):
            return ""
        parts: List[str] = []
        for item in value:
            if isinstance(item, str) and item:
                parts.append(item)
            elif isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]))
        return "\n".join(parts)

    def _normalize_quoted(value: str) -> str:
        t = value.strip()
        if len(t) < 2 or t[0] not in ("'", '"') or t[-1] != t[0]:
            return value
        inner = t[1:-1].strip()
        if inner.startswith("{") or inner.startswith("["):
            return inner
        unescaped = inner.replace('\\"', '"')
        return unescaped if unescaped.startswith(("{", "[")) else value

    def _from_value(value: Any) -> str:
        if isinstance(value, str) and value.strip():
            return _normalize_quoted(value)
        if isinstance(value, dict):
            return json.dumps(value)
        return ""

    try:
        record = json.loads(trimmed)
    except Exception:
        return _normalize_quoted(trimmed)
    if isinstance(record, str):
        # JSON string output: the de-escaped string IS the answer.
        return record if record.strip() else ""
    if not isinstance(record, dict):
        return trimmed
    for key in ("structured_output", "structuredOutput"):
        text = _from_value(record.get(key))
        if text.strip():
            return text
    text = _from_value(record.get("result"))
    if text.strip():
        return text
    for key in ("output", "response", "text", "message"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return _normalize_quoted(value)
    text = _from_content(record.get("content"))
    if text.strip():
        return _normalize_quoted(text)
    return _normalize_quoted(trimmed)


def _claude_cli_args(model: str, json_schema: str,
                     instructions: str) -> List[str]:
    args = [
        "-p", "--output-format", "json", "--input-format", "text",
        "--no-session-persistence", "--no-chrome", "--safe-mode",
        "--setting-sources", "user", "--permission-mode", "dontAsk",
        "--tools", "",
    ]
    if instructions:
        args += ["--append-system-prompt", instructions]
    if json_schema:
        args += ["--json-schema", json_schema]
    if model:
        args += ["--model", model]
    return args


def _codex_cli_args(model: str, instructions: str) -> List[str]:
    args = ["exec", "--skip-git-repo-check", "--sandbox", "read-only", "--json"]
    if instructions:
        args += ["-c", f"developer_instructions={json.dumps(instructions)}"]
    if model:
        args += ["--model", model]
    return args


def _cli_generate(provider_id: str, prov: Dict[str, Any], model: str,
                  prompt: str, instructions: str, json_schema: str) -> str:
    entry = PROVIDER_MANIFEST.get(provider_id) or {}
    vendor = entry.get("vendor", provider_id)
    command = (_as_str(prov.get("command")).strip()
               or ("claude" if provider_id == "claude-cli" else "codex"))
    if provider_id == "claude-cli":
        args = _claude_cli_args(model, json_schema, instructions)
        stdout = _run_cli(command, args, prompt, CLI_TIMEOUT, _CLAUDE_CLI_ENV)
        output = _claude_cli_output(stdout)
        combined = f"{stdout}\n{output}".lower()
        if re.search(r"not\s+(logged|authenticated)|unauthenticated|"
                     r"login required|no active account", combined):
            raise ByokProviderError(_CLAUDE_CLI_AUTH_MESSAGE)
    elif provider_id == "codex-cli":
        args = _codex_cli_args(model, instructions)
        stdout = _run_cli(command, args, prompt, CLI_TIMEOUT)
        output = _codex_cli_output(stdout)
        if re.search(r"not\s+(logged|authenticated)|unauthenticated|"
                     r"login required", output, re.I):
            raise ByokProviderError(
                "Codex CLI is not logged in. Run `codex login` and try again.")
    else:
        raise ByokProviderError(f"Unknown CLI provider '{provider_id}'.")
    if not output.strip():
        raise ByokProviderError(f"{vendor} returned an empty response.")
    return output


def _codex_cli_output(stdout: str) -> str:
    """Port of ``extractCodexCliOutput`` (last JSON event wins)."""
    trimmed = (stdout or "").strip()
    if not trimmed:
        return ""

    def _from_event(value: Any) -> str:
        record = value if isinstance(value, dict) else None
        if record is None:
            return ""
        for key in ("result", "output", "final_output", "response", "text"):
            text = record.get(key)
            if isinstance(text, str) and text.strip():
                return text
        content = record.get("content")
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            joined = "\n".join(
                item.get("text", "") if isinstance(item, dict)
                else (item if isinstance(item, str) else "")
                for item in content)
            if joined.strip():
                return joined
        message = record.get("message")
        if isinstance(message, str) and message.strip():
            return message
        nested = _from_event(message)
        if nested.strip():
            return nested
        return _from_event(record.get("item"))

    lines = [line for line in re.split(r"\r?\n", trimmed) if line.strip()]
    if len(lines) > 1:
        for line in reversed(lines):
            try:
                text = _from_event(json.loads(line))
                if text.strip():
                    return text
            except Exception:
                continue
    try:
        text = _from_event(json.loads(trimmed))
        if text.strip():
            return text
    except Exception:
        pass
    return stdout


def _claude_cli_models() -> List[Dict[str, str]]:
    """Anthropic model ids via OpenRouter's public listing (TS parity)."""
    try:
        response = requests.get(_OPENROUTER_MODELS_URL, timeout=STATUS_TIMEOUT)
        response.raise_for_status()
        data = (response.json() or {}).get("data") or []
    except Exception as e:
        raise ByokProviderError(f"Claude CLI model fetch failed: {e}")

    def _normalize(model: str) -> str:
        model = re.sub(r"^~?anthropic/", "", model.strip())
        parts = []
        for token in model.split("-"):
            parts.append(token.replace(".", "-")
                         if re.match(r"^\d+(?:\.\d+)+$", token) else token)
        return "-".join(parts)

    def _version_tokens(token: str) -> Optional[List[int]]:
        if re.match(r"^\d{8}$", token):
            return None
        if not re.match(r"^\d+(?:\.\d+)*$", token):
            return None
        return [int(p) for p in token.split(".")]

    best: Dict[str, Tuple[Dict[str, str], List[int]]] = {}
    order: List[str] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = _as_str(item.get("id"))
        marker = model_id.find("anthropic/")
        if marker == -1:
            continue
        claude_id = _normalize(model_id[marker + len("anthropic/"):])
        if claude_id.endswith("-latest") or not claude_id:
            continue
        family_parts: List[str] = []
        version: List[int] = []
        for token in claude_id.split("-"):
            tokens = _version_tokens(token)
            if tokens is not None:
                version.extend(tokens)
                continue
            if re.match(r"^\d{8}$", token):
                continue
            family_parts.append(token)
        family = "-".join(family_parts) or claude_id
        existing = best.get(family)
        if existing is None:
            order.append(family)
            best[family] = ({"id": claude_id, "label": claude_id}, version)
        else:
            current = existing[1]
            longer = max(len(version), len(current))
            padded_new = version + [0] * (longer - len(version))
            padded_old = current + [0] * (longer - len(current))
            if padded_new > padded_old:
                best[family] = ({"id": claude_id, "label": claude_id}, version)
    return [best[fam][0] for fam in order if fam in best]


def _codex_cli_models(prov: Dict[str, Any]) -> List[Dict[str, str]]:
    command = _as_str(prov.get("command")).strip() or "codex"
    stdout = _run_cli(command, ["debug", "models"], "", CLI_STATUS_TIMEOUT)
    try:
        parsed = json.loads((stdout or "").strip() or "null")
    except Exception:
        return []
    raw = parsed if isinstance(parsed, list) else (
        (parsed or {}).get("models") or (parsed or {}).get("data")
        if isinstance(parsed, dict) else None)
    options: List[Dict[str, str]] = []
    seen = set()
    for item in raw or []:
        if isinstance(item, str):
            model_id = item.strip()
        elif isinstance(item, dict):
            model_id = _as_str(item.get("slug") or item.get("id")
                               or item.get("name")).strip()
        else:
            continue
        if model_id and model_id not in seen:
            seen.add(model_id)
            options.append({"id": model_id, "label": model_id})
    return options


# ------------------------------------------------------------------- façade


def generate_text(
    provider_id: str,
    model: str,
    prompt: str,
    instructions: str = "",
    images: Optional[List[str]] = None,
    json_schema: str = "",
    settings: Optional[Dict[str, Any]] = None,
) -> str:
    """One non-streaming generation through any provider. Raises
    :class:`ByokProviderError` / :class:`ByokRateLimitError` with
    user-readable messages."""
    provider_id = _as_str(provider_id).strip()
    if provider_id == POOL_PROVIDER:
        raise ByokProviderError(POOL_COMING_SOON_MSG)
    if provider_id == LOCAL_ENGINE:
        raise ByokProviderError(
            "llamacpp is served by the local engine, not the BYOK runtime.")
    if not is_provider_id(provider_id):
        raise ByokProviderError(f"Unknown provider '{provider_id}'.")

    settings = load_settings() if settings is None else settings
    prov = get_provider_settings(settings, provider_id)
    model = _require_model(
        provider_id, resolve_model(provider_id, model, settings))
    prompt = prompt or ""

    entry = PROVIDER_MANIFEST.get(provider_id) or {}
    family = entry.get("family")
    if family == "cloud":
        return _openai_generate(provider_id, prov, model, prompt,
                                instructions, images, json_schema, settings)
    if provider_id == "lm-studio":
        return _openai_generate(provider_id, prov, model, prompt,
                                instructions, images, json_schema, settings)
    if provider_id == "ollama":
        if images:
            # Vision goes through the gateway's mtmd engine, not this wire.
            raise ByokProviderError(
                "Ollama does not accept images through this path.")
        return _ollama_generate(prov, model, prompt, instructions,
                                json_schema, settings)
    if family == "cli":
        return _cli_generate(provider_id, prov, model, prompt, instructions,
                             json_schema)
    raise ByokProviderError(f"Unknown provider '{provider_id}'.")


def generate_stream(
    provider_id: str,
    model: str,
    prompt: str,
    instructions: str = "",
    settings: Optional[Dict[str, Any]] = None,
) -> Iterator[str]:
    """Text deltas for cloud/local-server providers; CLI is buffered."""
    provider_id = _as_str(provider_id).strip()
    if provider_id == POOL_PROVIDER:
        raise ByokProviderError(POOL_COMING_SOON_MSG)
    settings = load_settings() if settings is None else settings
    prov = get_provider_settings(settings, provider_id)
    model = _require_model(
        provider_id, resolve_model(provider_id, model, settings))
    entry = PROVIDER_MANIFEST.get(provider_id) or {}
    family = entry.get("family")
    if family == "cloud" or provider_id == "lm-studio":
        return _openai_stream(provider_id, prov, model, prompt,
                              instructions, settings)
    if provider_id == "ollama":
        return _ollama_stream(prov, model, prompt, instructions, settings)
    if family == "cli":
        text = _cli_generate(provider_id, prov, model, prompt, instructions, "")
        return iter([text])
    raise ByokProviderError(f"Unknown provider '{provider_id}'.")


def embed_texts(
    provider_id: str,
    model: str,
    inputs: List[str],
    settings: Optional[Dict[str, Any]] = None,
) -> List[List[float]]:
    """Embeddings: OpenAI-wire ``/embeddings`` or Ollama ``/api/embed``."""
    provider_id = _as_str(provider_id).strip()
    if provider_id == POOL_PROVIDER:
        raise ByokProviderError(POOL_COMING_SOON_MSG)
    entry = PROVIDER_MANIFEST.get(provider_id) or {}
    vendor = entry.get("vendor", provider_id)
    if not entry or entry.get("family") == "cli":
        raise ByokProviderError(
            f"{entry.get('label', provider_id)} does not support embeddings.")
    settings = load_settings() if settings is None else settings
    prov = get_provider_settings(settings, provider_id)

    if provider_id == "ollama":
        base = _base_url_for("ollama", prov)
        try:
            response = requests.post(
                _join_url(base, "api/embed"),
                json={"model": model, "input": inputs},
                timeout=HTTP_TIMEOUT)
            if response.status_code == 404:
                # Legacy endpoint: one prompt string only.
                response = requests.post(
                    _join_url(base, "api/embeddings"),
                    json={"model": model, "prompt": "\n\n".join(inputs)},
                    timeout=HTTP_TIMEOUT)
        except requests.exceptions.RequestException:
            raise ByokProviderError(
                "Ollama server unreachable. Check the URL and that Ollama "
                "is running.")
        if response.status_code >= 400:
            raise ByokProviderError(
                f"Ollama embeddings failed (HTTP {response.status_code}): "
                f"{_detail_from(response)}")
        try:
            payload = response.json()
        except Exception:
            raise ByokProviderError(
                "Ollama returned an unexpected embeddings shape.")
        vectors = payload.get("embeddings")
        if isinstance(vectors, list) and vectors:
            return [list(v) for v in vectors]
        legacy = payload.get("embedding")
        if isinstance(legacy, list):
            return [list(legacy)]
        raise ByokProviderError(
            "Ollama returned an unexpected embeddings shape.")

    api_key = _require_api_key(provider_id, settings)
    base = _base_url_for(provider_id, prov)
    model = _require_model(
        provider_id, resolve_model(provider_id, model, settings))
    response = _request_with_retry(
        lambda: requests.post(
            _join_url(base, "embeddings"),
            json={"model": model, "input": inputs},
            headers=_openai_headers(provider_id, api_key),
            timeout=HTTP_TIMEOUT,
        ),
        vendor,
    )
    if response.status_code >= 400:
        raise ByokProviderError(_describe_error(
            vendor, f"HTTP {response.status_code}: {_detail_from(response)}"))
    try:
        payload = response.json()
        rows = payload.get("data")
        if isinstance(rows, list) and rows:
            return [list(r.get("embedding") or []) for r in rows
                    if isinstance(r, dict)]
        vectors = payload.get("embeddings")
        if isinstance(vectors, list) and vectors:
            return [list(v) for v in vectors]
    except Exception:
        pass
    raise ByokProviderError(
        f"{vendor} returned an unexpected embeddings shape.")


def list_models(
    provider_id: str,
    settings: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, str]]:
    """Live model list for a provider (``[{id, label}]``)."""
    provider_id = _as_str(provider_id).strip()
    if provider_id == POOL_PROVIDER:
        raise ByokProviderError(POOL_COMING_SOON_MSG)
    settings = load_settings() if settings is None else settings
    prov = get_provider_settings(settings, provider_id)
    entry = PROVIDER_MANIFEST.get(provider_id) or {}
    family = entry.get("family")
    if family == "cloud" or provider_id == "lm-studio":
        return _openai_list_models(provider_id, prov, settings)
    if provider_id == "ollama":
        return _ollama_list_models(prov, settings)
    if provider_id == "claude-cli":
        return _claude_cli_models()
    if provider_id == "codex-cli":
        return _codex_cli_models(prov)
    raise ByokProviderError(f"Unknown provider '{provider_id}'.")


def test_connection(
    provider_id: str,
    settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """``{"ok", "message", "models"}`` — never raises."""
    provider_id = _as_str(provider_id).strip()
    if provider_id == POOL_PROVIDER:
        return {"ok": False, "message": POOL_COMING_SOON_MSG, "models": []}
    settings = load_settings() if settings is None else settings
    entry = PROVIDER_MANIFEST.get(provider_id) or {}
    vendor = entry.get("vendor", provider_id)
    if provider_id not in PROVIDER_MANIFEST:
        return {"ok": False, "message": f"Unknown provider '{provider_id}'.",
                "models": []}

    if provider_id == "ollama":
        try:
            models = [m["id"] for m in list_models("ollama", settings)]
        except ByokProviderError as e:
            return {"ok": False, "message": str(e), "models": []}
        stored = resolve_model("ollama", "", settings)
        if stored and stored not in models:
            return {
                "ok": False,
                "message": (f"Connected, but model \"{stored}\" is not "
                            f"installed. Run: ollama pull {stored}"),
                "models": models,
            }
        return {
            "ok": True,
            "message": f"Connected to Ollama ({len(models)} model(s) available).",
            "models": models,
        }

    if provider_id == "codex-cli":
        prov = get_provider_settings(settings, provider_id)
        command = _as_str(prov.get("command")).strip() or "codex"
        try:
            out = _run_cli(command, ["login", "status"], "", CLI_STATUS_TIMEOUT)
        except ByokProviderError as e:
            return {"ok": False, "message": str(e), "models": []}
        if re.search(r"not\s+(logged|authenticated)|unauthenticated|"
                     r"login required", out, re.I):
            return {"ok": False,
                    "message": "Codex CLI is not logged in. Run `codex login` "
                               "and try again.", "models": []}
        model = resolve_model(provider_id, "", settings)
        suffix = f" ({model})" if model else ""
        return {"ok": True, "message": f"Connected to Codex CLI{suffix}.",
                "models": []}

    model = resolve_model(provider_id, "", settings)
    if not model:
        return {"ok": False,
                "message": f"Choose a {entry.get('label', provider_id)} model.",
                "models": []}
    try:
        text = generate_text(
            provider_id, model,
            'Reply with exactly this JSON object: {"ok": true}',
            settings=settings,
        )
    except ByokProviderError as e:
        return {"ok": False, "message": str(e), "models": []}
    if not str(text).strip():
        return {"ok": False,
                "message": f"{vendor} connected but returned an empty response.",
                "models": []}
    return {"ok": True, "message": f"Connected to {vendor} ({model}).",
            "models": []}


def record_verification(provider_id: str, result: Dict[str, Any],
                        model: str) -> Dict[str, Any]:
    """Persist a connection test outcome into the settings file."""
    settings = load_settings()
    settings.setdefault("verification", {})[provider_id] = {
        "ok": bool(result.get("ok")),
        "message": _as_str(result.get("message")),
        "model_id": _as_str(model),
        "tested_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    return save_settings(settings)
