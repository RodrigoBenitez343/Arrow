"""CodeNodePanel part: Node load / models / IO ports / persist.

App root = the LoOper/ directory (top-level packages NGUI/AI/player).
"""
import json
import logging
import os

from PyQt5.QtWidgets import QMessageBox

from NGUI.i18n import _
from player.code_agent_ops.constants import MAIN_FILE
from player.code_agent_ops.execution import _ModelRefreshWorker
from player.code_agent_ops.parsing import (
    _effective_ctx_tokens,
    _safe_join,
)

logger = logging.getLogger(__name__)


class NodeMixin(object):
    # ------------------------------------------------------------------
    # Node binding + per-node agent sessions
    # ------------------------------------------------------------------

    def load_node(self, node):
        """Bind the panel to a Code node graph object.

        Each Code node is its own program and owns its own agent session:
        switching away snapshots the outgoing node's session (chat history,
        rendered chat, console, last run, tool results) under its node id,
        and switching back restores it.  An in-flight agent request on the
        outgoing node is aborted (same semantics as Stop) and its workers are
        detached, so they can never keep streaming into the newly loaded
        node.
        """
        prev_id = self._loaded_node_id
        new_id = str(getattr(node, 'id', '') or '')
        if prev_id and prev_id != new_id and self._node is not None:
            self._save_current_session()
        self._loaded_node_id = new_id
        self._node = node
        try:
            self._config = node.get_code_config()
        except Exception:
            self._config = {}
        try:
            name = getattr(node, 'name', None) or 'Code'
            nid = str(getattr(node, 'id', '') or '')
            self._title_lbl.setText(name)
            self._sub_lbl.setText(f'{nid}  \u2022  {_("code node")}')
        except Exception:
            self._title_lbl.setText('Code')
            self._sub_lbl.setText('')

        # Main code buffer: node.code is the source of truth for script.py.
        self._main_code = self._config.get('code', '')
        self._active_file = MAIN_FILE
        self._code_edit.blockSignals(True)
        self._code_edit.setPlainText(self._main_code)
        self._code_edit.blockSignals(False)
        self._update_file_label()

        self._clear_var_rows(True)
        self._clear_var_rows(False)
        for iv in (self._config.get('input_vars') or []):
            self._add_var_row(True, iv.get('name', ''), iv.get('type', 'string'))
        for ov in (self._config.get('output_vars') or []):
            self._add_var_row(False, ov.get('name', ''), ov.get('type', 'string'))

        self._refresh_connected_inputs()
        self._sync_model_ui()
        self._refresh_files()
        if prev_id != self._loaded_node_id:
            self._restore_incoming_session()
            self._switch_page('chat')

    # ------------------------------------------------------------------
    # Session save / restore
    # ------------------------------------------------------------------

    def _save_current_session(self):
        """Leave the current node: abort in-flight agent work and snapshot
        its session (chat history + rendered chat + console + last run +
        tool results) under the node id it belongs to."""
        # Persist any manual edits to a helper file that is open.
        if self._active_file != MAIN_FILE:
            self._autosave_extra()

        # Abort an active request (same semantics as pressing Stop): the
        # stream UI is finished and later stream signals are dropped by the
        # node-token guard before they can touch the next node.
        self._loop_aborted = True
        self._request_goal = None
        self._pending_run = False
        self._finish_live()

        # Detach still-running workers.  They keep executing in the
        # background but are firewalled: chat/tool signals are dropped, run
        # and shell results are routed into the saved session below.
        ct = self._chat_thread
        if ct is not None and ct.isRunning():
            self._detach_worker(ct)
        self._chat_thread = None
        if self._stop_ref is not None:
            self._stop_ref['flag'] = True      # stop the node run early
        rt = self._run_thread
        if rt is not None and rt.isRunning():
            self._detach_worker(rt)
        self._run_thread = None
        self._run_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._stop_ref = None
        it = getattr(self, '_invest_worker', None)
        if it is not None:
            if it.isRunning():
                self._detach_worker(it)
            self._invest_worker = None
        st = self._shell_thread
        if st is not None and st.isRunning():
            self._detach_worker(st)
        self._shell_thread = None

        # Snapshot the outgoing node's session, then reset to defaults for
        # the incoming node (the editor/code itself is bound below from the
        # node config, not from the session).
        sid = self._loaded_node_id
        pending = self._main_code if self._active_file == MAIN_FILE else None
        if pending == (self._config.get('code') or ''):
            pending = None          # nothing unsaved on the main file
        self._sessions[sid] = {
            'history': list(self._chat_history),
            'chat_html': self._chat_view.toHtml(),
            'last_run': (dict(self._last_run_record)
                         if self._last_run_record else None),
            'tool_results': self._last_tool_results,
            'console': self._console.toPlainText(),
            'pending_code': pending,
        }
        self._node_token += 1
        self._chat_history = []
        self._reset_loop()
        self._last_run_record = None
        self._last_tool_results = None
        self._chat_view.clear()
        self._console.clear()

    def _restore_incoming_session(self):
        """Restore the session stored for the newly loaded node id (history,
        chat view, console, last run, tool results), or greet a fresh node."""
        rec = self._sessions.get(self._loaded_node_id)
        if rec:
            self._chat_history = list(rec.get('history') or [])
            html = rec.get('chat_html') or ''
            if html:
                self._chat_view.setHtml(html)
            self._last_run_record = rec.get('last_run')
            self._last_tool_results = rec.get('tool_results')
            console = rec.get('console') or ''
            if console:
                self._console.setPlainText(console)
            # Restore an unsaved manual edit to the main file from before the
            # switch (still NOT persisted to the node - Save does that).
            pending = rec.get('pending_code')
            if (pending and self._active_file == MAIN_FILE
                    and pending != (self._config.get('code') or '')):
                self._main_code = pending
                self._code_edit.blockSignals(True)
                self._code_edit.setPlainText(pending)
                self._code_edit.blockSignals(False)
        if not self._chat_view.toPlainText().strip():
            self._append_chat('info', _('Node loaded. Just tell the agent what '
                                        'to change - accepted code is saved to '
                                        'the node automatically. Open Files to '
                                        'add helpers.'))
        sb = self._chat_view.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())

    def _detach_worker(self, worker):
        """Keep a still-running worker alive and self-deleting after a node
        switch without blocking the newly loaded node (its signals are still
        connected; every completion handler checks the node token first)."""
        if worker is None:
            return
        try:
            self._orphan_threads.append(worker)
            worker.finished.connect(worker.deleteLater)
            worker.finished.connect(
                lambda w=worker: self._release_orphan(w))
            if not worker.isRunning():
                # Finished between the check and the connect - the signals
                # were already emitted, so release it right away.
                self._release_orphan(worker)
                worker.deleteLater()
        except Exception:
            pass

    def _release_orphan(self, worker):
        try:
            self._orphan_threads.remove(worker)
        except ValueError:
            pass

    # ------------------------------------------------------------------
    # Model / engine selection (chat backend)
    # ------------------------------------------------------------------

    def _init_models(self):
        """Populate model combos once; keep current text if already set."""
        if self._model_combo.count() == 0:
            try:
                from AI.model_cache import get_real_cached_models, get_cached_models
                models = get_real_cached_models(timeout=2.0) or get_cached_models()
            except Exception:
                models = []
            self._model_combo.addItems(models or ['llama3.2:latest'])
        if self._gguf_combo.count() == 0:
            try:
                from NGUI.dialogs.llm_dialogs import get_llamacpp_models
                gguf = get_llamacpp_models()
            except Exception:
                gguf = []
            if gguf:
                self._gguf_combo.addItems(gguf)
            else:
                self._gguf_combo.setPlaceholderText(_('No GGUF models found'))

    def _sync_model_ui(self):
        """Reflect the loaded node's engine/model in the selectors."""
        self._init_models()
        use_llamacpp = self._config.get('use_llamacpp', False)
        if isinstance(use_llamacpp, str):
            use_llamacpp = use_llamacpp.lower() in ('true', '1', 'yes', 'on')
        self._engine_combo.setCurrentText('llama.cpp' if use_llamacpp
                                          else 'Ollama')
        if use_llamacpp:
            path = self._config.get('llamacpp_model_path') or ''
            if path:
                self._gguf_combo.setCurrentText(path)
        else:
            model = self._config.get('gen_model') or 'llama3.2:latest'
            if model and self._model_combo.findText(model) >= 0:
                self._model_combo.setCurrentText(model)
            elif model:
                self._model_combo.setCurrentText(model)
        self._apply_engine_visibility()

    def _on_engine_changed(self, engine):
        """Show the right model list for the selected engine."""
        use_llamacpp = (engine == 'llama.cpp')
        self._apply_engine_visibility()
        if use_llamacpp:
            current = self._gguf_combo.currentText()
            self._init_models()
            if current:
                self._gguf_combo.setCurrentText(current)

    def _apply_engine_visibility(self):
        use_llamacpp = (self._engine_combo.currentText() == 'llama.cpp')
        for w in (self._model_lbl, self._model_combo, self._refresh_btn):
            w.setVisible(not use_llamacpp)
        for w in (self._gguf_lbl, self._gguf_combo):
            w.setVisible(use_llamacpp)

    def _refresh_models(self):
        """Refresh the Ollama model list off the UI thread."""
        if getattr(self, '_refresh_worker', None) is not None:
            return
        self._refresh_btn.setEnabled(False)
        self._refresh_btn.setText('...')
        worker = _ModelRefreshWorker()
        worker.finished_models.connect(self._on_models_refreshed)
        self._refresh_worker = worker
        worker.start()

    def _on_models_refreshed(self, models):
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText('\u21bb')
        self._refresh_worker = None
        if not models:
            return
        current = self._model_combo.currentText()
        self._model_combo.clear()
        self._model_combo.addItems(models)
        if current in models:
            self._model_combo.setCurrentText(current)
        elif current:
            self._model_combo.setCurrentText(current)

    def _chat_model(self):
        if self._engine_combo.currentText() == 'llama.cpp':
            return self._gguf_combo.currentText(), True
        return self._model_combo.currentText(), False

    def _budget(self):
        """Per-turn budget DERIVED from the native (RAM-aware) context window
        of the active engine - never a fixed cap.  Output tokens scale with
        the real window; truncation retries grow the output budget
        (ctx-clamped) so a reply cut by max_tokens is never re-asked with the
        same ceiling that just failed."""
        ctx = _effective_ctx_tokens(*self._chat_model()) or 8192
        out_tok = max(2048, min(int(ctx * 0.5), 4096))
        mt = float(getattr(self, '_steer', {}).get('maxtok', 1.0) or 1.0)
        if mt > 1.0:
            out_tok = min(int(out_tok * mt), max(1024, ctx - 1024))
        in_chars = max(9000, min(int((ctx - out_tok - 512) * 3.2), 30000))
        return in_chars, out_tok

    # ------------------------------------------------------------------
    # IO rows / connected inputs
    # ------------------------------------------------------------------

    def _clear_var_rows(self, is_input):
        host = self._in_rows_host if is_input else self._out_rows_host
        rows = self._in_rows if is_input else self._out_rows
        try:
            while host.count() > 1:  # keep trailing stretch item
                item = host.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.deleteLater()
        except Exception:
            pass
        rows.clear()

    def _refresh_connected_inputs(self):
        self._connected_inputs = self._graph_connected_inputs(self._node)
        lines = []
        for ci in self._connected_inputs:
            lines.append(
                f"{ci.get('input_port')}  <-  {ci.get('from_node')}:"
                f"{ci.get('output_type')}")
        self._conn_view.setPlainText('\n'.join(lines) if lines
                                     else _('(no connected inputs)'))

    @staticmethod
    def _graph_connected_inputs(node):
        """Enumerate graph edges entering the node (from GUI port objects)."""
        out = []
        if node is None:
            return out
        try:
            ports = node.input_ports() or []
        except Exception:
            return out
        for p in ports:
            try:
                pname = str(p.name())
            except Exception:
                pname = str(getattr(p, 'name', ''))
            try:
                conns = p.connected_ports() or []
            except Exception:
                conns = []
            for src in conns:
                src_name = ''
                src_id = ''
                try:
                    src_name = str(src.name())
                except Exception:
                    pass
                try:
                    src_node = src.node()
                    src_id = getattr(src_node, 'id', '')
                except Exception:
                    pass
                out.append({'from_node': src_id, 'output_type': src_name,
                            'input_port': pname})
        return out

    def _runtime_inputs_for(self, node_id):
        """Prefer the real inputs recorded by the last chain run for this id."""
        player = self._last_player()
        if player is not None:
            try:
                wg = player.workflow_executor.workflow_graph or {}
            except Exception:
                wg = {}
            if node_id in wg:
                try:
                    return [dict(i) for i in (wg[node_id].get('inputs') or [])]
                except Exception:
                    pass
        return list(self._connected_inputs)

    # ------------------------------------------------------------------
    # Persist to the node (the single 'apply' path - agent edits call this)
    # ------------------------------------------------------------------

    def _persist_node(self, quiet=True):
        """Write the current main code + IO ports (+ engine/model) into the
        node and save the chain."""
        if self._node is None:
            return
        try:
            code = self._main_code
            use_llamacpp = (self._engine_combo.currentText() == 'llama.cpp')
            self._node.set_property('code', code)
            self._node.set_property(
                'input_vars', json.dumps(self._collect_rows(self._in_rows)))
            self._node.set_property(
                'output_vars', json.dumps(self._collect_rows(self._out_rows)))
            self._node.set_property(
                'output_variable', self._config.get('output_variable', 'result'))
            self._node.set_property('gen_model',
                                    self._model_combo.currentText())
            self._node.set_property('use_llamacpp',
                                    'true' if use_llamacpp else 'false')
            self._node.set_property(
                'llamacpp_model_path',
                self._gguf_combo.currentText() if use_llamacpp else '')
            try:
                self._node.rebuild_ports()
            except Exception:
                pass
            # Mirror the main file onto disk so the workspace tree is truthful.
            try:
                path = _safe_join(self._workspace_root(), MAIN_FILE)
                with open(path, 'w', encoding='utf-8') as _f:
                    _f.write(code)
            except Exception:
                pass
            # Persist to the live node only - never to the chain file.  The
            # graph is a temporal editing area: saving the chain file here
            # silently overwrote the user's loaded chain (including unrelated
            # node additions/deletions made on the canvas) without them ever
            # pressing Save.  The node already carries these properties, so
            # the explicit Save path serialises them.
            graph_view = getattr(self._mw, 'graph_view', None)
            cm = getattr(graph_view, 'config_manager', None)
            if cm is not None:
                try:
                    cm.save_current_state()
                except Exception:
                    pass
            self._refresh_connected_inputs()
            self._refresh_files()
            if not quiet:
                self._append_console(_('Saved to the node.'))
        except Exception as e:
            logger.error('Code Node Studio save failed: %s', e)
            if not quiet:
                QMessageBox.critical(self, _('Code Node Studio'),
                                     f'{_("Save failed")}: {e}')
