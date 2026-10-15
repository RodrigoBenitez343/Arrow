"""Engine selectors — the shared engine lists, provider persistence as a
model prefix, and the runtime legs that honor it.

Run:  python -m pytest LoOper/tests/test_engine_selectors.py
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QComboBox  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# --------------------------------------------------------------------------- #
#  The shared engine list
# --------------------------------------------------------------------------- #

def test_engine_items_cover_providers_and_disable_the_pool():
    from NGUI.dialogs.engine_options import engine_items

    items = {eid: (label, enabled) for eid, label, enabled in engine_items()}
    # Legacy engines keep their exact labels (existing comparisons rely on it).
    assert items["llamacpp"][0] == "llama.cpp"
    assert items["ollama"][0] == "Ollama"
    # BYOK providers are present and enabled; the P2P pool is listed disabled.
    assert items["openai"][0].startswith("OpenAI") and items["openai"][1] is True
    assert items["pool"][1] is False


def test_combo_roundtrip_and_pool_unpickable(qapp):
    from NGUI.dialogs.engine_options import (
        engine_id, fill_engine_combo, select_engine)

    combo = QComboBox()
    fill_engine_combo(combo)
    # Defaults to Ollama (first item), like the old two-item combos.
    assert engine_id(combo) == "ollama"

    select_engine(combo, "openai")
    assert engine_id(combo) == "openai"

    # The pool entry is visible but disabled.
    pool_row = next(i for i in range(combo.count())
                    if combo.itemData(i) == "pool")
    assert not combo.model().item(pool_row).isEnabled()


def test_store_and_resolve_engine_from_model_prefix():
    from NGUI.dialogs.engine_options import engine_from_config, store_model

    stored = store_model("openai", "gpt-4o-mini")
    assert stored == "openai:gpt-4o-mini"
    # The prefix IS the routing: the engine comes back from the model alone.
    assert engine_from_config("", False, stored) == "openai"
    # Legacy engines never prefix; legacy flags still resolve.
    assert store_model("ollama", "llama3.2:1b") == "llama3.2:1b"
    assert engine_from_config("", True, "m.gguf") == "llamacpp"
    assert engine_from_config("ollama", False, "") == "ollama"
    # An already-prefixed model keeps its own prefix.
    assert store_model("openai", "groq:llama-3.3") == "groq:llama-3.3"


def test_models_for_engine_filters_the_merged_list():
    """Refresh must bring the SELECTED engine's models only — never Ollama
    tags mixed with every provider's entries."""
    from NGUI.dialogs.engine_options import models_for_engine

    merged = ["llama3.2:1b", "nomic-embed-text:latest", "ollama:llama3.2:1b",
              "openai:gpt-4o-mini", "groq:llama-3.3"]
    assert models_for_engine(merged, "ollama") == \
        ["llama3.2:1b", "nomic-embed-text:latest"]
    assert models_for_engine(merged, "openai") == ["openai:gpt-4o-mini"]
    # Unknown/legacy engines pass the list through untouched.
    assert models_for_engine(merged, "llamacpp") == merged


# --------------------------------------------------------------------------- #
#  LLM dialog: pick a provider engine, persist it, load it back
# --------------------------------------------------------------------------- #

def test_llm_dialog_provider_engine_round_trip(qapp):
    import NGUI.graph_elements  # noqa: F401
    from NGUI.dialogs.llm_dialogs import LLMPropertiesDialog
    from NGUI.dialogs.engine_options import engine_id, select_engine

    # Loading a stored provider choice: the engine follows the model prefix.
    dlg = LLMPropertiesDialog(
        None, {"use_llamacpp": False, "model": "openai:gpt-4o-mini"})
    assert engine_id(dlg.engine_combo) == "openai"
    assert dlg.model_combo.currentText() == "openai:gpt-4o-mini"
    # Models/GGUF visibility follows the engine (no crash, model shown).
    assert dlg.model_combo.isVisible() or not dlg.isVisible()

    # Saving with a provider engine prefixes a bare model.
    select_engine(dlg.engine_combo, "openai")
    dlg.model_combo.setCurrentText("gpt-4o-mini")
    cfg = dlg.get_config()
    assert cfg["use_llamacpp"] is False
    assert cfg["model"] == "openai:gpt-4o-mini"

    # Saving a legacy llama.cpp node stays byte-identical to before.
    dlg2 = LLMPropertiesDialog(
        None, {"use_llamacpp": True, "llamacpp_model_path": "m.gguf"})
    cfg2 = dlg2.get_config()
    assert cfg2["use_llamacpp"] is True and cfg2["model"] == "llamacpp"


# --------------------------------------------------------------------------- #
#  Form filler: provider engines route through the gateway
# --------------------------------------------------------------------------- #

from player.multi_sequence.worflow_interpreter_modules.executor_modules.form_filler_modules.extract import (  # noqa: E402,E501
    FormFillerExtractMixin,
)


class _FF(FormFillerExtractMixin):
    """FormFillerExtractMixin with the engine legs stubbed out."""

    def _ff_halt(self, stop_flag):
        return False

    def _resolve_api_url(self):
        return "http://gateway.test"

    def _ff_ollama_call(self, prompt, system, cfg):
        return "OLLAMA"

    def _ff_provider_call(self, prompt, system, cfg, engine):
        return "PROVIDER:" + engine

    def _call_llamacpp_api(self, **kw):
        return "LLAMACPP"


def test_form_filler_llm_call_dispatches_by_engine():
    ff = _FF()
    base = {"model": "m", "temperature": 0.1, "max_tokens": 16}

    assert ff._ff_llm_call("p", "s", dict(base, engine="ollama"), None) == \
        "OLLAMA"
    assert ff._ff_llm_call("p", "s", dict(base, engine="llamacpp"), None) == \
        "LLAMACPP"
    assert ff._ff_llm_call("p", "s", dict(base, engine="openai"), None) == \
        "PROVIDER:openai"


def test_form_filler_embed_client_routes_providers_via_gateway(monkeypatch):
    import AI.consult as consult
    from player.multi_sequence.llm_executor_resources import config_utils
    from player.multi_sequence.worflow_interpreter_modules.executor_modules.form_filler_modules.common import (  # noqa: E501
        _FFEmbedClient,
    )

    calls = {}

    class _Client:
        def __init__(self, base_url=None, debug=False, **kw):
            calls["base_url"] = base_url

        def embeddings(self, model, texts):
            calls["model"] = model
            return [[0.1]]

    monkeypatch.setattr(consult, "OllamaClient", _Client)
    monkeypatch.setattr(config_utils, "get_default_api_url",
                        lambda: "http://gateway.test")

    emb = _FFEmbedClient("openai", "text-embedding-3-small")
    out = emb.embeddings("text-embedding-3-small", ["hi"])
    assert out == [[0.1]]
    assert calls["base_url"] == "http://gateway.test"
    assert calls["model"] == "openai:text-embedding-3-small"
