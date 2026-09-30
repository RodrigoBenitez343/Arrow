"""CodeNodePanel part: workspace files, run, console and shell.

App root = the LoOper/ directory (top-level packages NGUI/AI/player).
"""
import logging
import os

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QInputDialog,
    QMessageBox,
    QTreeWidgetItem,
)

from NGUI.i18n import _
from player.code_agent_ops.constants import (
    MAIN_FILE,
    _MAX_RUN_RESULT,
    _MAX_RUN_STDERR,
    _MAX_RUN_STDOUT,
)
from player.code_agent_ops.execution import (
    _CodeRunWorker,
    _ShellWorker,
)
from player.code_agent_ops.parsing import (
    _clip,
    _looks_like_shell_command,
    _resolve_venv_site_packages,
    _resolve_workspace,
    _safe_join,
)

logger = logging.getLogger(__name__)


class FilesRunMixin(object):
    # ------------------------------------------------------------------
    # Panel close / workspace
    # ------------------------------------------------------------------

    def _close_studio(self):
        """Collapse back to the graph - saves pending manual edits first."""
        try:
            if self._active_file != MAIN_FILE:
                self._autosave_extra()
            self._persist_node()
        except Exception:
            pass
        mw = self._mw
        if mw is not None:
            hide = getattr(mw, '_hide_code_studio', None)
            if callable(hide):
                hide()
                return
            toggle = getattr(mw, '_toggle_code_studio', None)
            if callable(toggle):
                toggle(False)
        self.hide()

    def _workspace_root(self):
        node_id = str(getattr(self._node, 'id', '') or 'code')
        return _resolve_workspace(self._last_player(), node_id)

    def _refresh_files(self):
        self._files_tree.clear()
        root = self._workspace_root()
        root_item = QTreeWidgetItem(
            [os.path.basename(root.rstrip(os.sep)) or root])
        root_item.setData(0, Qt.UserRole, '')
        root_item.setToolTip(0, root)
        self._files_tree.addTopLevelItem(root_item)
        root_item.setExpanded(True)

        def walk(parent_item, dir_path):
            try:
                entries = sorted(
                    os.listdir(dir_path),
                    key=lambda n: (os.path.isfile(
                        os.path.join(dir_path, n)), n.lower()))
            except Exception:
                return
            for entry in entries:
                full = os.path.join(dir_path, entry)
                rel = os.path.relpath(full, root).replace('\\', '/')
                label = entry
                if rel == MAIN_FILE:
                    label = MAIN_FILE + '  (' + _('main') + ')'
                item = QTreeWidgetItem([label])
                item.setData(0, Qt.UserRole, rel)
                item.setToolTip(0, rel if not os.path.isdir(full)
                                else _('folder'))
                if os.path.isdir(full):
                    item.setIcon(0, self.style().standardIcon(
                        self.style().SP_DirIcon))
                else:
                    item.setIcon(0, self.style().standardIcon(
                        self.style().SP_FileIcon))
                parent_item.addChild(item)
                if os.path.isdir(full):
                    walk(item, full)

        walk(root_item, root)

    def _selected_rel(self):
        items = self._files_tree.selectedItems()
        if not items:
            return None
        return items[0].data(0, Qt.UserRole) or None

    def _workspace_path(self, rel):
        return _safe_join(self._workspace_root(), rel)

    def _new_file(self):
        name, ok = QInputDialog.getText(self, _('New file'),
                                        _('File name (may include '
                                          'subfolders):'))
        if not ok:
            return
        try:
            path = self._workspace_path(name.strip())
        except Exception:
            QMessageBox.warning(self, _('New file'), _('Invalid file name.'))
            return
        try:
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
            if os.path.exists(path):
                QMessageBox.information(self, _('New file'),
                                        _('Already exists.'))
            else:
                open(path, 'w', encoding='utf-8').close()
            self._refresh_files()
            self._open_file(name.strip().replace('\\', '/'))
        except Exception as e:
            QMessageBox.warning(self, _('New file'), str(e))

    def _new_folder(self):
        name, ok = QInputDialog.getText(self, _('New folder'),
                                        _('Folder name:'))
        if not ok:
            return
        try:
            path = self._workspace_path(name.strip())
            os.makedirs(path, exist_ok=True)
            self._refresh_files()
        except Exception:
            QMessageBox.warning(self, _('New folder'), _('Invalid name.'))

    def _open_selected(self):
        rel = self._selected_rel()
        if rel:
            self._open_file(rel)

    def _open_file(self, rel):
        """Open a workspace file in the Code page editor."""
        rel = (rel or '').replace('\\', '/')
        if not rel or os.path.isdir(self._workspace_path(rel)):
            return
        self._autosave_extra()
        if rel == MAIN_FILE:
            self._active_file = MAIN_FILE
            self._code_edit.blockSignals(True)
            self._code_edit.setPlainText(self._main_code)
            self._code_edit.blockSignals(False)
        else:
            try:
                with open(self._workspace_path(rel), 'r', encoding='utf-8',
                          errors='replace') as f:
                    content = f.read()
            except Exception as e:
                QMessageBox.warning(self, _('Open file'), str(e))
                return
            self._active_file = rel
            self._code_edit.blockSignals(True)
            self._code_edit.setPlainText(content)
            self._code_edit.blockSignals(False)
        self._update_file_label()
        self._switch_page('code')

    def _rename_selected(self):
        rel = self._selected_rel()
        if not rel:
            return
        if rel == MAIN_FILE:
            QMessageBox.information(self, _('Rename'),
                                    _('The main file cannot be renamed.'))
            return
        name, ok = QInputDialog.getText(self, _('Rename'), _('New name:'),
                                        text=os.path.basename(rel))
        if not ok:
            return
        try:
            new_path = self._workspace_path(name.strip())
            old_path = self._workspace_path(rel)
            d = os.path.dirname(new_path)
            if d:
                os.makedirs(d, exist_ok=True)
            if rel == self._active_file:
                self._active_file = MAIN_FILE
            os.rename(old_path, new_path)
            self._refresh_files()
        except Exception:
            QMessageBox.warning(self, _('Rename'), _('Rename failed.'))

    def _delete_selected(self):
        rel = self._selected_rel()
        if not rel:
            return
        if rel == MAIN_FILE:
            QMessageBox.information(self, _('Delete'),
                                    _('The main file cannot be deleted.'))
            return
        ans = QMessageBox.question(self, _('Delete'),
                                   _('Delete "{}"?').format(rel))
        if ans != QMessageBox.Yes:
            return
        try:
            path = self._workspace_path(rel)
            if os.path.isdir(path):
                import shutil
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)
            if rel == self._active_file:
                self._active_file = MAIN_FILE
                self._open_file(MAIN_FILE)
            self._refresh_files()
        except Exception as e:
            QMessageBox.warning(self, _('Delete'), str(e))

    def _autosave_extra(self):
        """Persist edits of a non-main file before switching away."""
        if self._active_file and self._active_file != MAIN_FILE:
            try:
                with open(self._workspace_path(self._active_file), 'w',
                          encoding='utf-8') as f:
                    f.write(self._code_edit.toPlainText())
            except Exception:
                pass

    def _save_open_file(self):
        """Save the open file: helpers to the workspace disk, main to node."""
        if self._active_file == MAIN_FILE:
            self._persist_node(quiet=False)
            return
        try:
            path = self._workspace_path(self._active_file)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(self._code_edit.toPlainText())
            self._append_console(_('Saved {}').format(self._active_file))
        except Exception as e:
            QMessageBox.warning(self, _('Save file'), str(e))

    def _on_editor_changed(self):
        if self._active_file == MAIN_FILE:
            self._main_code = self._code_edit.toPlainText()

    def _update_file_label(self):
        if self._active_file == MAIN_FILE:
            self._file_lbl.setText(
                MAIN_FILE + '  \u2014  ' +
                _('main (saved to node on agent apply / save)'))
        else:
            self._file_lbl.setText(self._active_file)

    # ------------------------------------------------------------------
    # Run / console / shell
    # ------------------------------------------------------------------

    def _last_player(self):
        try:
            return getattr(self._mw, '_last_player', None) if self._mw else None
        except Exception:
            return None

    def _current_node_dict(self, code=None):
        cfg = dict(self._config)
        cfg['code'] = code if code is not None else self._main_code
        cfg['input_vars'] = self._collect_rows(self._in_rows)
        cfg['output_vars'] = self._collect_rows(self._out_rows)
        node_id = str(getattr(self._node, 'id', '') or 'studio_code')
        return {
            'type': 'code',
            'id': node_id,
            'node_id': node_id,
            'data': cfg,
            'inputs': self._runtime_inputs_for(node_id),
        }

    def _on_run(self):
        if self._node is None:
            QMessageBox.information(self, _('Code Node Studio'),
                                    _('Select a Code node first.'))
            return
        if self._run_thread is not None and self._run_thread.isRunning():
            self._append_console(_('Already running - press Stop first.'))
            return
        ws = self._workspace_root()
        chain_root = os.path.dirname(os.path.dirname(ws))
        self._stop_ref = {'flag': False}
        self._stop_btn.setEnabled(True)
        self._run_btn.setEnabled(False)
        mode_hint = 'replay' if self._last_player() is not None else 'solo'
        self._append_console(_('Running (mode: {})...').format(mode_hint))
        worker = _CodeRunWorker(
            self._current_node_dict(),
            player_provider=self._last_player,
            stop_ref=self._stop_ref,
            chain_root=chain_root,
        )
        # Owning node: a run that finishes after a node switch is routed
        # into that node's saved session instead of the current UI.
        worker.token = self._node_token
        worker.owner_id = self._loaded_node_id
        worker.finished_run.connect(self._on_run_finished)
        self._run_thread = worker
        worker.start()

    def _start_agent_run(self):
        """Auto-run after an agent main-code change (headless code only)."""
        if self._node is None or not self._request_goal:
            return
        if self._run_thread is not None and self._run_thread.isRunning():
            self._append_chat('execution', _('A run is already in progress - '
                'press Run after it finishes to test this change.'))
            return
        self._pending_run = True
        self._on_run()

    def _on_stop(self):
        if self._stop_ref is not None:
            self._stop_ref['flag'] = True
            self._append_console(_('Stop requested (in-process exec finishes '
                                   'the current statement).'))
        self._loop_aborted = True
        self._request_goal = None
        self._append_console(_('Iteration loop stopped.'))
        self._stop_btn.setEnabled(False)

    def _run_block_text(self, record):
        """The raw terminal-style lines the run block renders - mirrors what
        the agent sees (input digests, stdout/stderr, error, result)."""
        lines = []
        digest = record.get('digest') or []
        if digest:
            lines.append(_('$ inputs'))
            for d in digest[:6]:
                lines.append('  ' + d)
        out = record.get('stdout') or ''
        err = record.get('stderr') or ''
        error = record.get('error') or ''
        if out:
            lines.append(_('$ stdout'))
            lines.append(_clip(out, 700))
        if err:
            lines.append(_('$ stderr'))
            lines.append(_clip(err, 900))
        if error:
            lines.append(_('$ error'))
            lines.append(_clip(error, 900))
        if record.get('result') is not None:
            lines.append(_('$ result'))
            lines.append(str(_clip(record.get('result'), _MAX_RUN_RESULT)))
        return '\n'.join(lines) if lines else _('(no output)')

    def _append_run_block(self, record, title):
        """Append an execution as an inline tool-style terminal block (like a
        terminal-tool call): monospace, dark, labelled, right in the chat."""
        ok = bool(record.get('ok'))
        accent = '#7AE0A0' if ok else '#FF8A8A'
        title_safe = str(title).replace('&', '&amp;').replace('<', '&lt;') \
            .replace('>', '&gt;')
        safe = self._run_block_text(record)
        safe = safe.replace('&', '&amp;').replace('<', '&lt;') \
            .replace('>', '&gt;').replace('\n', '<br>')
        body = (
            '<div style="background:#0B1116; border-left:3px solid ' + accent
            + '; border-radius:6px; padding:6px 8px; margin:6px 2px;">'
            '<span style="color:' + accent
            + '; font-size:10px; font-weight:700;">' + title_safe
            + '</span><br>'
            '<span style="color:#C8D3DC; font-family:Consolas; '
            'font-size:11px;">' + safe + '</span></div>'
        )
        self._chat_view.append(body)
        sb = self._chat_view.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())

    def _is_stale_worker(self, sender):
        """True when a worker belongs to a node we switched away from AND
        have not come back to.  A worker whose owner is the currently loaded
        node (the user returned while it was still running) is NOT stale -
        its result may be shown normally."""
        if sender is None:
            return False
        return (getattr(sender, 'token', None) != self._node_token
                and getattr(sender, 'owner_id', None)
                != self._loaded_node_id)

    def _on_run_finished(self, record):
        sender = self.sender()
        if self._is_stale_worker(sender):
            # A run started on a node we already switched away from: keep its
            # result in that node's saved session (console + last run) so it
            # is there when the user comes back, and touch nothing here.
            self._route_away_run(record, sender)
            return
        self._run_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._last_run_record = record
        self._append_console('\n'.join(self._run_console_lines(record)))
        self._refresh_files()

        if not self._pending_run:
            # Manual Run (header button / /run) - inline terminal block.
            ok = bool(record.get('ok'))
            self._append_run_block(
                record, _('Run \u2713 ok') if ok else _('Run \u2717 failed'))
            if (not ok and _looks_like_shell_command(self._main_code)):
                self._append_chat(
                    'error', _('The node code looks like a shell command '
                        '(e.g. "python file.py"), but Code nodes run pure '
                        'Python. Ask the agent for real code, or use the '
                        'Terminal tab to run shell commands.'))
            return

        self._pending_run = False
        self._agent_run_finished(record)

    def _run_console_lines(self, record):
        """The plain console rendering of a run record (also used to store
        results of a run whose node was switched away)."""
        lines = [f"mode: {record.get('mode')}", f"ok: {record.get('ok')}"]
        if record.get('stdout'):
            lines.append('--- stdout ---')
            lines.append(_clip(record.get('stdout'), _MAX_RUN_STDOUT))
        if record.get('stderr'):
            lines.append('--- stderr ---')
            lines.append(_clip(record.get('stderr'), _MAX_RUN_STDERR))
        if record.get('error'):
            lines.append('--- error ---')
            lines.append(_clip(record.get('error'), _MAX_RUN_STDERR))
        lines.append('--- result ---')
        lines.append(str(_clip(record.get('result'), _MAX_RUN_RESULT)))
        return lines

    def _route_away_run(self, record, worker):
        """Store a late-finished run into the saved session of the node that
        started it (it can no longer touch the live UI)."""
        owner = getattr(worker, 'owner_id', None)
        rec = self._sessions.get(owner) if owner else None
        if rec is None:
            return
        rec['last_run'] = dict(record)
        rec['console'] = ((rec.get('console') or '').rstrip() + '\n'
                          + '\n'.join(self._run_console_lines(record)))

    def _on_shell_send(self):
        cmd = self._term_input.text().strip()
        if not cmd:
            return
        if self._shell_thread is not None and self._shell_thread.isRunning():
            self._append_console(_('A shell command is already running.'))
            return
        self._term_input.clear()
        ws = self._workspace_root()
        sp = _resolve_venv_site_packages(self._last_player())
        scripts = os.path.join(os.path.dirname(sp), 'Scripts') if sp else ''
        self._append_console('$ ' + cmd)
        worker = _ShellWorker(cmd, ws, scripts if os.path.isdir(scripts) else '')
        worker.token = self._node_token
        worker.owner_id = self._loaded_node_id
        worker.finished_cmd.connect(self._on_shell_finished)
        self._shell_thread = worker
        worker.start()

    def _on_shell_finished(self, ok, out):
        sender = self.sender()
        if self._is_stale_worker(sender):
            # Shell command belongs to a switched-away node: store its output
            # in that node's saved console instead of the live one.
            owner = getattr(sender, 'owner_id', None)
            rec = self._sessions.get(owner) if owner else None
            if rec is not None:
                text = out if out else (_('(no output)') if ok else '')
                rec['console'] = ((rec.get('console') or '').rstrip() + '\n'
                                  + text)
            return
        self._append_console(out if out else (_('(no output)') if ok else ''))
        if not ok and not out:
            self._append_console(_('Command failed.'))
        self._refresh_files()

    def _append_console(self, text):
        self._console.appendPlainText(text)
        sb = self._console.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())
