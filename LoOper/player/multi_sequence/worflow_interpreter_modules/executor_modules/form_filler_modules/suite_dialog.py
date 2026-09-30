"""The node dialog and chain save/load round-trips."""



def test_dialog_is_themed_with_model_dropdown_and_documents():
    from PyQt5.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])

    from NGUI.dialogs.base_dialog import ModernDialog
    from NGUI.dialogs.form_filler_dialog import FormFillerDialog

    cfg = {
        "mode": "desktop", "engine": "ollama", "model": "llama3.2:latest",
        "instruction": "fill", "fields_include": "email",
        "probe_top_k": 4, "max_fields": 12, "verify": False,
        "rag_documents": ["/tmp/a.pdf", "/tmp/b.docx"],
    }
    dlg = FormFillerDialog(None, cfg)

    # Themed base + the LLM-node-style dropdown + a Documents section.
    assert isinstance(dlg, ModernDialog)
    assert dlg.mode_combo.currentText() == "desktop"
    assert dlg.engine_combo.currentText() == "Ollama"
    assert dlg.rag_documents_list.count() == 2

    out = dlg.get_config()
    assert out["engine"] == "ollama"
    assert out["model"] == "llama3.2:latest"
    assert out["mode"] == "desktop"
    assert out["probe_top_k"] == 4
    assert out["max_fields"] == 12
    assert out["verify"] is False
    assert out["rag_documents"] == ["/tmp/a.pdf", "/tmp/b.docx"]


def test_dialog_round_trips_cycles_and_answer_no():
    from PyQt5.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])

    from NGUI.dialogs.form_filler_dialog import FormFillerDialog

    dlg = FormFillerDialog(None, {"probe_cycles": 5, "answer_no": False,
                               "answer_na": False})
    out = dlg.get_config()
    assert out["probe_cycles"] == 5
    assert out["answer_no"] is False
    assert out["answer_na"] is False


def test_dialog_round_trips_ask_user_without_any_path_property():
    from PyQt5.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])

    from NGUI.dialogs.form_filler_dialog import FormFillerDialog

    dlg = FormFillerDialog(None, {"ask_user": True})
    out = dlg.get_config()
    assert out["ask_user"] is True
    # Default off: an unanswered question must never be asked by surprise.
    assert FormFillerDialog(None, {}).get_config()["ask_user"] is False
    # The corrections document is node-owned: there is NO path property at all.
    assert "knowledge_file" not in out


def test_dialog_shows_the_node_owned_corrections_path_not_an_editor():
    """The dialog must NOT ask the user to point at a file: the corrections
    document is owned by the node, so the row is READ-ONLY (no line edit, no
    Browse), and with nothing set in the chain it names where the node keeps
    it."""
    from PyQt5.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])

    from NGUI.dialogs.form_filler_dialog import FormFillerDialog

    owned = r"C:\chains\ff1_corrections.md"
    dlg = FormFillerDialog(None, {}, corrections_path=owned)
    assert not hasattr(dlg, "knowledge_file_edit")
    assert owned in dlg.corrections_label.text()
    assert "knowledge_file" not in dlg.get_config()
    # No chain saved yet: it still names the node-owned location.
    dlg2 = FormFillerDialog(None, {})
    assert dlg2.corrections_label.text()


def test_chain_save_and_load_keep_new_form_properties():
    """Regression: the save/load allowlists must carry EVERY form property.

    probe_cycles/answer_no were silently dropped (the same class of bug that
    lost web_scope), so a configured node reverted to defaults on reload.
    """
    from NGUI.graph_elements import config_manager as cm
    from NGUI.graph_elements.config_manager import ConfigManager

    cfg = {
        "mode": "web", "instruction": "fill", "fields_include": "",
        "fields_skip": "date", "probe_top_k": 1, "probe_char_budget": 1500,
        "probe_context_chars": 6000, "consolidate": False,
        "probe_cycles": 7, "verify": True,
        "repair": True, "repair_attempts": 3,
        "answer_no": False, "answer_na": False, "max_fields": 40,
        "engine": "ollama",
        "model": "m", "temperature": 0.1, "max_tokens": 256,
        "context_size": 4096,
        "typing_batch_size": 20, "typing_batch_delay": 0.05,
        "rag_documents": [], "web_scope": "",
        "ask_user": True,
    }

    class _Node:
        id = "ff1"

        def get_form_filler_config(self):
            return cfg

        def pos(self):
            return (0, 0)

    class _Save:
        chain_config = {"form_filler_nodes": []}

        def _get_node_connections(self, node):
            return []

    saver = _Save()
    ConfigManager._save_form_filler_node(saver, _Node())
    saved = saver.chain_config["form_filler_nodes"][0]
    assert saved["probe_cycles"] == 7
    assert saved["consolidate"] is False
    assert saved["answer_no"] is False
    assert saved["answer_na"] is False
    assert saved["ask_user"] is True
    assert saved["repair"] is True and saved["repair_attempts"] == 3
    assert saved["context_size"] == 4096
    assert saved["fields_skip"] == "date"

    # Every saved key must also be restored on load (no silent omissions).
    src = open(cm.__file__, encoding="utf-8").read()
    loader = src[src.index("def _create_form_filler_nodes"):]
    loader = loader[: loader.index("def _create_code_nodes")]
    for key in ("consolidate", "probe_cycles", "answer_no", "answer_na",
                "repair", "repair_attempts", "context_size", "fields_skip",
                "max_tokens", "ask_user"):
        assert f'ff_data.get("{key}"' in loader, key


def test_form_filler_clipboard_whitelist_covers_web_scope():
    """Copy/paste must carry EVERY property the node serialises: an omitted one
    is silently reset to its default on paste.  A missing 'web_scope' turned a
    scoped node into a whole-page one."""
    from NGUI.graph_elements.clipboard_manager import ClipboardManager

    wl = ClipboardManager.get_property_whitelist(
        None, "form_filler.FormFillerNode")
    for key in ("web_scope", "probe_cycles", "consolidate", "repair",
                "repair_attempts", "answer_no", "answer_na", "context_size",
                "ask_user"):
        assert key in wl, key
