"""The agent's model is a CHOICE in the GUI, not a default.

Every other part of the app lets the user pick an engine (llama.cpp, Ollama,
each configured BYOK provider) and a model from it.  The agent's reasoner is now
the same selector, shared by the **MODEL** button in agent mode and the **New
workspace** dialog, and stored in ``workspace.json → agent.model``.

Contract pinned here:

* the dialog offers the engines the app knows and the models EACH serves;
* switching the engine filters the model list instead of mixing providers;
* a model the gateway does not list can still be typed (the combo is editable);
* "Use no model" clears the choice — there is no silent default;
* a gateway that cannot be listed is reported in the dialog, not replaced by a
  made-up list;
* nothing in this path pulls NodeGraphQt (the standalone agent build excludes
  it), which is why ``NGUI/dialogs/__init__.py`` resolves its classes lazily.

Run:  python -m pytest LoOper/tests/test_agent_model_selector.py
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from PyQt5.QtWidgets import QApplication  # noqa: E402

MODELS = ["llama3.2:1b", "qwen3:8b", "openai:gpt-4o-mini", "ollama:llama3.2:1b"]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def dialog(qapp, monkeypatch):
    """A selector with the gateway's list already delivered (no thread)."""
    from NGUI.widgets import agent_model_dialog as mod

    monkeypatch.setattr(mod.AgentModelDialog, "reload_models", lambda self: None)
    panel = mod.AgentModelDialog(None, model="llama3.2:1b")
    panel._models_ready(list(MODELS), "")
    return panel


def _models(dialog):
    return [dialog.model_combo.itemText(i)
            for i in range(dialog.model_combo.count())]


def test_the_engines_the_app_knows_are_offered(dialog):
    engines = [dialog.engine_combo.itemData(i)
               for i in range(dialog.engine_combo.count())]
    assert "llamacpp" in engines and "ollama" in engines
    assert any(e not in ("llamacpp", "ollama") for e in engines), \
        "configured BYOK providers must be selectable too"


def test_the_current_choice_is_preselected(dialog):
    assert dialog.model_combo.currentText() == "llama3.2:1b"


def test_switching_engine_filters_the_models(dialog):
    from NGUI.dialogs.engine_options import select_engine

    select_engine(dialog.engine_combo, "ollama")
    dialog._engine_changed(dialog.engine_combo.currentIndex())
    shown = _models(dialog)
    assert shown and all(not m.startswith("openai:") for m in shown), shown


def test_a_model_can_be_typed_when_the_gateway_lacks_it(dialog):
    dialog.model_combo.setEditText("my-own-model:7b")
    assert dialog.model() == "my-own-model:7b"


def test_use_no_model_clears_the_choice(dialog):
    dialog._clear()
    assert dialog.model() == ""


def test_a_gateway_error_is_reported_not_hidden(dialog):
    dialog._models_ready([], "connection refused")
    assert "connection refused" in dialog.status.text()
    assert dialog.model_combo.isEditable(), "typing must stay possible"


def test_importing_the_selector_does_not_load_the_graph_engine(qapp):
    """The standalone agent build excludes NodeGraphQt: keep it out."""
    for name in [n for n in sys.modules if n.startswith("NGUI.")]:
        if name.endswith("agent_model_dialog"):
            del sys.modules[name]
    import NGUI.widgets.agent_model_dialog  # noqa: F401
    assert "NodeGraphQt" not in sys.modules
