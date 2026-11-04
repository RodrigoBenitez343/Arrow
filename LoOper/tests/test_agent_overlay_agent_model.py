"""Agent mode's MODEL control: the planner model is set from the overlay.

The failing turn ended in "no planner model is configured for this agent", and
there was nowhere in agent mode to configure one.  The model is a property of
the WORKSPACE (``workspace.json`` -> ``agent.model``), read fresh by the router
on every request, so the overlay only has to write it — and it has to show the
ACTIVE agent's model, not another agent's.

Run:  python -m pytest LoOper/tests/test_agent_overlay_agent_model.py
"""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from PyQt5.QtWidgets import QApplication, QDialog  # noqa: E402

from NGUI.widgets.agent_overlay import AgentOverlay  # noqa: E402
from player import workspace as ws  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _Controller:
    """The slice of SimpleChainRouter the model control talks to."""

    def __init__(self, root, slug):
        self._workspaces_root = root
        self._slug = slug

    # the callback registrations set_chat_controller performs
    def set_ask_user_callback(self, _callback):
        return None

    def set_ask_user_v2_callback(self, _callback):
        return None

    def set_output_display_callback(self, _callback):
        return None

    def active_agent(self):
        return self._slug

    def list_system_chains(self):
        return [{"id": ws.load(self._workspaces_root, slug)["slug"],
                 "name": slug,
                 "model": (ws.load(self._workspaces_root, slug)["agent"]
                           .get("model") or "")}
                for slug in [self._slug]]


@pytest.fixture
def overlay(qapp, tmp_path):
    root = str(tmp_path / "workspaces")
    manifest = ws.create(root, "Job hunt", agent_name="Scout")
    panel = AgentOverlay()
    panel._chat_controller = _Controller(root, manifest["slug"])
    panel.set_chat_controller(panel._chat_controller)
    panel.root = root
    panel.slug = manifest["slug"]
    return panel


def _stub_selector(monkeypatch, chosen):
    """Replace the modal model selector with one that returns *chosen*.

    The real dialog is a modal ``exec_()``; a headless test must not block on
    it, and the dialog's own behaviour is pinned by
    ``test_agent_model_selector.py``.
    """
    from NGUI.widgets import agent_model_dialog as mod

    class _Stub:
        def __init__(self, *args, **kwargs):
            pass

        def exec_(self):
            return QDialog.Accepted

        def model(self):
            return chosen

    monkeypatch.setattr(mod, "AgentModelDialog", _Stub)


def test_the_button_shows_the_active_agents_model(overlay):
    """"not set" is a state the user can see, not an invisible default."""
    assert "not set" in overlay._model_btn.text()

    overlay._update_model_button([{"id": "other", "model": "should-not-show"},
                                  {"id": overlay.slug, "model": "llama3.2:1b"}],
                                 overlay.slug)
    assert "llama3.2:1b" in overlay._model_btn.text()
    assert "should-not-show" not in overlay._model_btn.text()


def test_setting_the_model_writes_the_workspace(overlay, monkeypatch):
    """One pick in the selector, one file: the router reads it on the run."""
    _stub_selector(monkeypatch, "llama3.2:1b")

    overlay._edit_agent_model()

    stored = json.load(open(
        os.path.join(ws.workspace_dir(overlay.root, overlay.slug),
                     ws.WORKSPACE_FILE), encoding="utf-8"))
    assert stored["agent"]["model"] == "llama3.2:1b"
    assert "llama3.2:1b" in overlay._model_btn.text()


def test_cancelling_changes_nothing(overlay, monkeypatch):
    from NGUI.widgets import agent_model_dialog as mod

    class _Cancel:
        def __init__(self, *args, **kwargs):
            pass

        def exec_(self):
            return QDialog.Rejected

        def model(self):
            raise AssertionError('a cancelled dialog must not be read')

    monkeypatch.setattr(mod, "AgentModelDialog", _Cancel)
    overlay._edit_agent_model()

    stored = ws.load(overlay.root, overlay.slug)
    assert stored["agent"]["model"] == ""


def test_clearing_the_model_is_stored_as_no_choice(overlay, monkeypatch):
    """There is no implicit default: 'use no model' really means none."""
    ws.set_agent_model(overlay.root, overlay.slug, "llama3.2:1b")
    _stub_selector(monkeypatch, "")

    overlay._edit_agent_model()

    assert ws.load(overlay.root, overlay.slug)["agent"]["model"] == ""
    assert "not set" in overlay._model_btn.text()


def test_no_agent_selected_says_so_instead_of_crashing(overlay):
    overlay._chat_controller._slug = ""
    overlay._edit_agent_model()
    assert overlay._model_btn.text()
