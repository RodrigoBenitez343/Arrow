import os


def get_default_api_url() -> str:
    """Get default API URL using environment variables.

    In direct engine mode (ARROW_DIRECT_ENGINE=1) no HTTP API exists —
    raise loudly so any missed HTTP call fails instead of silently hitting
    a dead port.  Callers in direct mode must route through
    AI.inprocess_transport instead.
    """
    if os.getenv("ARROW_DIRECT_ENGINE") == "1":
        raise RuntimeError(
            "Direct engine mode (ARROW_DIRECT_ENGINE=1): no HTTP API is "
            "running; route LLM calls through AI.inprocess_transport instead"
        )
    try:
        from AI.config_loader import get_api_url
        return get_api_url()
    except Exception:
        pass
    env_overwatch = os.getenv("OVERWATCH_API_URL")
    if env_overwatch and str(env_overwatch).strip():
        return env_overwatch.strip().rstrip("/")
    port = os.getenv("API_PORT", "8000")
    if not port or not str(port).strip():
        port = "8000"
    return f"http://localhost:{port}"


def get_default_llm_model(engine: str = "llamacpp") -> str:
    """App-default model for nodes that configure none (and have no chain
    LLM node to borrow from): the orchestrator's picker/synthesis calls and
    the Input node's decision evaluator.

    LOCAL PRIORITY: an explicit local engine with a configured GGUF never
    yields to the chat role — the build-time provider must not leak into
    built artifacts (measured 2026-10-09: a composer-built brain ran
    deepseek because the role filled its empty model).

    BYOK role fallback: only when the local engine has no GGUF (or the
    engine is not the local one), a non-engine chat role supplies its
    ``provider:model``.  ollama: the first cached Ollama model, else "".
    """
    engine = str(engine or "llamacpp").strip().lower()
    if engine in ("llamacpp", "llama.cpp", "llama_cpp"):
        try:
            from AI.config_loader import get_llamacpp_config
            path = str(get_llamacpp_config().get("model_path") or "").strip()
            if path:
                return path
        except Exception:
            pass
    try:
        from AI import byok
        role = byok.get_role(byok.load_settings(), "chat")
        provider = str(role.get("provider") or "").strip()
        if provider and provider != byok.LOCAL_ENGINE:
            model = str(role.get("model") or "").strip() or \
                byok.resolve_model(provider, "")
            if model:
                return f"{provider}:{model}"
    except Exception:
        pass
    if engine in ("llamacpp", "llama.cpp", "llama_cpp"):
        return ""            # local engine, no GGUF, no role fill
    # An explicit BYOK provider engine: its configured model, prefixed so
    # every caller routes there through the gateway.
    try:
        from AI import byok
        if engine != "ollama" and byok.is_provider_id(engine):
            model = str(byok.resolve_model(engine, "")).strip()
            return byok.join_model_ref(engine, model) if model else ""
    except Exception:
        pass
    try:
        from AI.model_cache import get_cached_models
        models = get_cached_models() or []
        return str(models[0]).strip() if models else ""
    except Exception:
        return ""


def resolve_llamacpp_model_path(model: str) -> str:
    """Resolve a relative GGUF name against ``AI/models`` (mirrors the LLM
    executor's resolution).  Absolute paths and unknown names pass through."""
    m = str(model or "").strip()
    if not m or os.path.isabs(m):
        return m
    try:
        from AI.config_loader import get_models_dir
        path = os.path.normpath(os.path.join(get_models_dir(), m))
        return path if os.path.exists(path) else m
    except Exception:
        return m
