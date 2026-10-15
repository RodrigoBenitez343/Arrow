"""engine_options.py — the shared engine lists for the node dialogs' engine
combo boxes.

One source of truth for every engine selector in the app (LLM nodes, code
nodes, conditionals, form filler, the Code Node Studio): the built-in
llama.cpp engine, Ollama, and every BYOK provider from ``AI.byok`` (the P2P
compute pool is listed but disabled — coming soon).

Selecting a provider persists as a ``"<provider>:<model>"`` prefix on the
model field, which the local gateway dispatches; the engine is therefore
DERIVABLE from the stored model on load and no new chain field is needed.

All helpers are fail-open: without ``AI.byok`` the two legacy engines remain
and models pass through unchanged.
"""

#: Legacy labels kept verbatim: existing dialogs and chain files compare
#: ``currentText() == "llama.cpp"`` — providers are matched by item data.
_LEGACY_LABELS = {"llamacpp": "llama.cpp", "ollama": "Ollama"}


def _byok():
    """Import AI.byok from either layout (source or frozen); None on failure."""
    try:
        from AI import byok
        return byok
    except Exception:
        return None


def engine_items():
    """``[(id, label, enabled)]`` in pick order (pool disabled, coming soon).

    The built-in engine first, then Ollama, then the manifest providers in
    their pick order.  Labels for the two legacy engines stay short so the
    dialogs' existing string comparisons keep working.
    """
    items = [("ollama", _LEGACY_LABELS["ollama"], True),
             ("llamacpp", _LEGACY_LABELS["llamacpp"], True)]
    b = _byok()
    if b is None:
        return items
    try:
        for choice in b.provider_choices(include_disabled=True):
            pid = str(choice.get("id") or "")
            if not pid or pid in _LEGACY_LABELS:
                continue
            items.append((pid, str(choice.get("label") or pid),
                          not bool(choice.get("coming_soon"))))
    except Exception:
        pass
    return items


def fill_engine_combo(combo, current_id=""):
    """Populate an engine combo (kept in sync with the AI Settings dialog).

    Each item carries its provider id in the user data; the coming-soon pool
    entry is added disabled so it is visible but unpickable.
    """
    combo.blockSignals(True)
    try:
        combo.clear()
        for eid, label, enabled in engine_items():
            combo.addItem(label, eid)
            index = combo.count() - 1
            try:
                if not enabled:
                    combo.model().item(index).setEnabled(False)
            except Exception:
                pass
        if current_id:
            select_engine(combo, current_id)
        if combo.currentIndex() < 0 and combo.count():
            combo.setCurrentIndex(0)
    finally:
        combo.blockSignals(False)


def engine_id(combo):
    """The selected engine's runtime id (never raises)."""
    try:
        data = combo.currentData()
        if isinstance(data, str) and data:
            return data
    except Exception:
        pass
    text = ""
    try:
        text = str(combo.currentText() or "")
    except Exception:
        return "llamacpp"
    for eid, label, _enabled in engine_items():
        if text == label:
            return eid
    low = text.strip().lower()
    if low in ("", "llama.cpp", "llama_cpp", "llamacpp"):
        return "llamacpp"
    if low in ("ollama",):
        return "ollama"
    return low


def select_engine(combo, engine_id_value):
    """Select an engine by id (falls back to llama.cpp when unknown)."""
    target = str(engine_id_value or "").strip().lower()
    if target in ("llama.cpp", "llama_cpp"):
        target = "llamacpp"
    combo.blockSignals(True)
    try:
        for i in range(combo.count()):
            if combo.itemData(i) == target:
                combo.setCurrentIndex(i)
                return
        combo.setCurrentIndex(0)
    finally:
        combo.blockSignals(False)


def engine_from_config(engine_hint="", use_llamacpp=False, model=""):
    """The engine a stored config means (never raises).

    Order: a provider prefix on the model wins (it IS the routing), then an
    explicit provider engine hint, then the legacy ``use_llamacpp`` flag.
    """
    hint = str(engine_hint or "").strip().lower()
    if hint in ("llama.cpp", "llama_cpp"):
        hint = "llamacpp"
    b = _byok()
    if b is not None:
        try:
            prefix, _bare = b.split_model_ref(model)
            if prefix:
                return prefix
        except Exception:
            pass
        if hint and hint not in ("", "ollama", "llamacpp"):
            try:
                if b.is_provider_id(hint):
                    return hint
            except Exception:
                pass
    if use_llamacpp or hint == "llamacpp":
        return "llamacpp"
    return "ollama"


def models_for_engine(models, engine_id_value):
    """Filter a merged model list to the models the selected engine serves.

    The gateway's ``/models`` merges Ollama tags with every configured
    provider's ``provider:model`` entries; a picker must not mix them.  A
    provider engine keeps only its own entries; Ollama keeps the bare tags
    (an ``ollama:`` prefix is shown without it); anything else passes
    through unchanged.  Used by every model Refresh and engine switch.
    """
    engine = str(engine_id_value or "").strip().lower()
    if engine in ("", "llamacpp"):
        return [str(m) for m in (models or [])]
    b = _byok()
    out = []
    for m in models or []:
        text = str(m)
        prefix, bare = "", text
        if b is not None:
            try:
                prefix, bare = b.split_model_ref(text)
            except Exception:
                prefix, bare = "", text
        if engine == "ollama":
            if not prefix:
                if text not in out:
                    out.append(text)
            elif prefix == "ollama" and bare and bare not in out:
                out.append(bare)
        elif prefix == engine and text not in out:
            out.append(text)
    return out


def store_model(engine_id_value, model):
    """The model string to persist for an engine choice (never raises).

    A provider engine prefixes the model (``openai:gpt-4o-mini``) so the
    gateway routes it; the legacy engines (and a model that already names a
    provider) pass through unchanged.
    """
    engine = str(engine_id_value or "").strip().lower()
    if engine in ("", "ollama", "llamacpp", "llama.cpp", "llama_cpp"):
        return model
    b = _byok()
    if b is None:
        return model
    try:
        return b.join_model_ref(engine, model)
    except Exception:
        return model


__all__ = [
    "engine_items", "fill_engine_combo", "engine_id", "select_engine",
    "engine_from_config", "store_model", "models_for_engine",
]
