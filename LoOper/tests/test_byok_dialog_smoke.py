"""AI Settings dialog — Providers/Routing tab smoke + byok.json persistence.

Run:  python -m pytest LoOper/tests/test_byok_dialog_smoke.py
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def dialog(qapp, tmp_path, monkeypatch):
    """A dialog wired to a temp byok.json (never the real runtime dir)."""
    from AI import byok
    monkeypatch.setattr(byok, "settings_path",
                        lambda: str(tmp_path / "byok.json"))
    from NGUI.dialogs.ai_settings_dialog import AISettingsDialog
    dlg = AISettingsDialog(None)
    dlg.show()                 # children must report isVisible() truthfully
    dlg._tab_widget.setCurrentIndex(2)   # the Providers tab
    qapp.processEvents()
    yield dlg
    dlg.close()


def test_providers_tab_renders_every_provider_as_its_own_row(dialog):
    rows = dialog.provider_rows
    for pid in ("llamacpp", "openai", "ollama", "claude-cli", "pool"):
        assert pid in rows
    assert rows["pool"]["toggle"] is None        # not configurable yet
    assert rows["llamacpp"]["toggle"] is None    # engine-owned
    assert rows["openai"]["toggle"] is not None  # BYOK → has a toggle

    for key in ("chat", "embeddings", "vision"):
        role_combo = dialog.role_provider_combos[key]
        assert role_combo.itemData(0) == ""          # Auto (legacy) first
        idx = role_combo.findData("pool")
        assert idx >= 0
        assert role_combo.model().item(idx).isEnabled() is False


def test_provider_rows_show_only_the_relevant_fields(dialog):
    cloud = dialog.provider_rows["openai"]
    assert cloud["key"] is not None and cloud["model"] is not None
    assert cloud["url"] is None and cloud["command"] is None

    local = dialog.provider_rows["ollama"]
    assert local["url"] is not None and local["key"] is None

    cli = dialog.provider_rows["claude-cli"]
    assert cli["command"] is not None and cli["key"] is None

    engine = dialog.provider_rows["llamacpp"]
    assert engine["model"] is None and engine["test"] is None


def test_save_writes_byok_json_and_preserves_other_providers(qapp, tmp_path,
                                                             monkeypatch):
    from AI import byok
    monkeypatch.setattr(byok, "settings_path",
                        lambda: str(tmp_path / "byok.json"))
    byok.save_settings({"providers": {"groq": {"api_key": "gsk",
                                               "model": "llama-3.1",
                                               "enabled": True}}})
    from NGUI.dialogs.ai_settings_dialog import AISettingsDialog
    dlg = AISettingsDialog(None)

    row = dlg.provider_rows["openai"]
    row["toggle"].setChecked(True)
    row["key"].setText("sk-test")
    row["model"].setCurrentText("gpt-4o-mini")
    chat_combo = dlg.role_provider_combos["chat"]
    chat_combo.setCurrentIndex(chat_combo.findData("openai"))
    dlg.role_model_edits["chat"].setText("gpt-4o-mini")
    dlg._save_byok()

    saved = byok.load_settings()
    assert saved["providers"]["openai"]["api_key"] == "sk-test"
    assert saved["providers"]["openai"]["credential_saved"] is True
    assert saved["providers"]["openai"]["enabled"] is True
    assert saved["providers"]["groq"]["api_key"] == "gsk"      # preserved
    assert saved["roles"]["chat"] == {"provider": "openai",
                                      "model": "gpt-4o-mini"}


def test_provider_job_results_update_the_row(dialog):
    dialog._on_provider_job_done("models", "ollama",
                                 {"ok": True, "models": ["a:1", "b:2"]})
    row = dialog.provider_rows["ollama"]
    assert row["model"].count() == 2
    assert "2" in row["status"].text()

    row["model"].setCurrentText("a:1")
    dialog._on_provider_job_done("test", "ollama",
                                 {"ok": True, "message": "Connected."})
    assert "Connected." in row["status"].text()
    assert dialog._byok["verification"]["ollama"]["ok"] is True


def test_loading_restores_roles_and_toggles(qapp, tmp_path, monkeypatch):
    from AI import byok
    monkeypatch.setattr(byok, "settings_path",
                        lambda: str(tmp_path / "byok.json"))
    byok.save_settings({
        "providers": {"openai": {"api_key": "sk", "model": "gpt-4o",
                                 "enabled": True},
                      "groq": {"api_key": "gsk", "enabled": False}},
        "roles": {"embeddings": {"provider": "llamacpp", "model": ""},
                  "vision": {"provider": "openai", "model": "gpt-4o"}},
    })
    from NGUI.dialogs.ai_settings_dialog import AISettingsDialog
    dlg = AISettingsDialog(None)
    assert dlg.provider_rows["openai"]["toggle"].isChecked() is True
    assert dlg.provider_rows["groq"]["toggle"].isChecked() is False
    assert dlg.role_provider_combos["embeddings"].currentData() == "llamacpp"
    assert dlg.role_provider_combos["vision"].currentData() == "openai"
    assert dlg.role_model_edits["vision"].text() == "gpt-4o"
