"""The node dialog and chain save/load round-trips."""



def test_dialog_is_themed_with_the_model_dropdowns():
    from PyQt5.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])

    from NGUI.dialogs.base_dialog import ModernDialog
    from NGUI.dialogs.form_filler_dialog import FormFillerDialog

    cfg = {
        "mode": "desktop", "engine": "ollama", "model": "llama3.2:latest",
        "instruction": "fill", "fields_include": "email",
        "crag_top_k": 4, "max_fields": 12, "verify": False,
    }
    dlg = FormFillerDialog(None, cfg)

    # Themed base + the LLM-node-style dropdown.  The DOCUMENTS are the Context
    # node's now (the pool the filler is wired to), so the dialog has no
    # document list of its own.
    assert isinstance(dlg, ModernDialog)
    assert dlg.mode_combo.currentText() == "desktop"
    assert dlg.engine_combo.currentText() == "Ollama"
    assert not hasattr(dlg, "rag_documents_list")

    out = dlg.get_config()
    assert out["engine"] == "ollama"
    assert out["model"] == "llama3.2:latest"
    assert out["mode"] == "desktop"
    assert out["crag_top_k"] == 4
    assert out["max_fields"] == 12
    assert out["verify"] is False
    assert "rag_documents" not in out


def test_dialog_round_trips_cycles_and_answer_no():
    from PyQt5.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])

    from NGUI.dialogs.form_filler_dialog import FormFillerDialog

    dlg = FormFillerDialog(None, {"crag_top_n_external": 2,
                                 "answer_no": False, "answer_na": False})
    out = dlg.get_config()
    assert out["crag_top_n_external"] == 2
    assert out["answer_no"] is False
    assert out["answer_na"] is False


def test_dialog_round_trips_ask_user_without_any_path_property():
    from PyQt5.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])

    from NGUI.dialogs.form_filler_dialog import FormFillerDialog

    dlg = FormFillerDialog(None, {"ask_user": False})
    out = dlg.get_config()
    assert out["ask_user"] is False
    # UNSURE -> ASK is the DEFAULT: a field the pipeline cannot decide is the
    # user's to answer, so a fresh node asks unless it is switched off.
    assert FormFillerDialog(None, {}).get_config()["ask_user"] is True
    # The corrections document is node-owned: there is NO path property at all.
    assert "knowledge_file" not in out


def test_dialog_offers_no_knowledge_file_row():
    """Learned answers live in the Context pool the user wires, so the dialog
    offers NO file to point at: no read-only corrections row, no path editor,
    and no knowledge_file key in the config it round-trips."""
    from PyQt5.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])

    from NGUI.dialogs.form_filler_dialog import FormFillerDialog

    dlg = FormFillerDialog(None, {})
    assert not hasattr(dlg, "knowledge_file_edit")
    assert not hasattr(dlg, "corrections_label")
    assert "knowledge_file" not in dlg.get_config()


def test_chain_save_and_load_keep_new_form_properties():
    """Regression: the save/load allowlists must carry EVERY form property.

    crag_top_n_external/answer_no were silently dropped (the same class of
    bug that lost web_scope), so a configured node reverted to defaults.
    """
    from NGUI.graph_elements import config_manager as cm
    from NGUI.graph_elements.config_manager import ConfigManager

    cfg = {
        "mode": "web", "instruction": "fill", "fields_include": "",
        "fields_skip": "date", "crag_top_k": 1, "crag_char_budget": 1500,
        "crag_max_strips": 12, "crag_enabled": False,
        "crag_top_n_external": 7, "verify": True,
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
    assert saved["crag_top_n_external"] == 7
    assert saved["crag_enabled"] is False
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
    for key in ("crag_enabled", "crag_top_n_external", "answer_no",
                "answer_na",
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
    for key in ("web_scope", "crag_top_k", "crag_enabled", "crag_evaluator",
                "crag_upper_threshold", "crag_lower_threshold",
                "crag_decompose_mode", "repair",
                "repair_attempts", "answer_no", "answer_na", "context_size",
                "ask_user"):
        assert key in wl, key
