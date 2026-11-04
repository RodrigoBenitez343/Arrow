"""CodeNodePanel part: the flat coding-agent loop (single-file node).

The agent works on ONE self-contained script (the code node's script.py).
One request = ONE bounded loop:

- The model produces code (full-block writes or SEARCH/REPLACE hunks) which
  is applied mechanically and auto-run; a clean run ends the request, a
  failed run feeds the error back as the next turn.
- When the model needs to inspect or run the code it replies with a short
  plain request; the panel routes it through the Needle 2 dev-tool engine
  (see panel.mixin_tools) which returns structured tool calls that are
  executed deterministically against the single script.
- Small-model safeguards: lean system prompt, guidance at the END of the
  message, bounded ring history, clipped dumps, failure-adaptive sampler,
  duplicate-code detection, truncation salvage.

App root = the LoOper/ directory (top-level packages NGUI/AI/player).
"""
import difflib
import json
import logging
import os
import re

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from NGUI.constants import (
    ACCENT_COLOR,
    DANGER_COLOR,
)
from NGUI.i18n import _
from player.code_agent_ops.chat_stream import (
    _ChatStreamWorker,
    _adapt_sampler,
    _failure_kind,
)
from player.code_agent_ops.constants import (
    MAIN_FILE,
    MAX_CHAT_TURNS,
    MAX_TOOL_TURNS,
    MAX_WORK_ROUNDS,
    STEER_PENALTY_BASE,
    STEER_TEMP_BASE,
    _MAX_CODE_CTX,
    _MAX_HELPER_CTX,
    _MAX_HELPERS_IN_CTX,
    _MAX_HISTORY_CHARS,
    _MAX_HISTORY_TURNS,
    _MAX_RUN_RESULT,
    _MAX_RUN_STDERR,
    _MAX_RUN_STDOUT,
)
from player.code_agent_ops.parsing import (
    _apply_hunks,
    _ast_outline,
    _clip,
    _collapse_reasoning,
    _collect_project_files,
    _is_gui_like,
    _is_question,
    _looks_like_shell_command,
    _list_workspace_files,
    _parse_agent_reply,
    _parse_hunks,
    _safe_join,
    _unresolved_imports,
)
from player.code_agent_ops.prompts import (
    SYSTEM_ANSWER,
    SYSTEM_EDIT,
    bad_imports_text,
    dup_reply_text,
    edit_retry_text,
    empty_reply_text,
    first_turn_text,
    fix_error_text,
    gui_report_text,
    truncated_text,
)

logger = logging.getLogger(__name__)

# A ``` fence's first line is a language tag (python/json/...) when it is one
# bare word; code like `x=1` or `print('hi')` never matches.
_LANG_TAG = re.compile(r'^[A-Za-z][A-Za-z0-9+#_.-]{0,15}$')


def _chat_segments(text):
    """Split a reply on ``` fences into ('text'|'code', chunk) segments so
    code renders as a boxed monospace preview instead of raw inline text."""
    out = []
    for i, part in enumerate(str(text).split('```')):
        if i % 2:
            lines = part.split('\n')
            if lines and _LANG_TAG.match(lines[0].strip()) and lines[0].strip():
                lines = lines[1:]
            s = '\n'.join(lines).strip('\n')
        else:
            s = part.strip('\n')
        if s:
            out.append(('code' if i % 2 else 'text', s))
    return out


class AgentMixin(object):
    # ------------------------------------------------------------------
    # Chat entry
    # ------------------------------------------------------------------

    def _on_chat_send(self):
        text = self._chat_input.text().strip()
        if not text:
            return
        if self._node is None:
            self._append_chat('info', _('Select a Code node first.'))
            return
        self._chat_input.clear()
        if self._pending_run:
            self._append_chat('error', _('A run is in progress - wait for it '
                                         'to finish before sending a new '
                                         'request.'))
            return
        if (getattr(self, '_invest_worker', None) is not None
                and self._invest_worker.isRunning()):
            self._append_chat('error', _('A tool investigation is running - '
                                         'wait for its results first.'))
            return
        if text.startswith('/'):
            self._handle_command(text)
            return
        self._start_request(text)

    def _handle_command(self, text):
        cmd = text.lower()
        if cmd == '/help':
            self._append_chat('info', '/help — this list\n'
                                      '/run — run the node\n'
                                      '/clear — clear chat memory')
        elif cmd == '/run':
            self._append_chat('info', '/run')
            self._on_run()
        elif cmd == '/clear':
            self._chat_history.clear()
            self._chat_clear()
            self._reset_loop()
        else:
            self._append_chat('error',
                              _('Unknown command: {} (try /help)').format(text))

    def _start_request(self, text):
        """Begin one agent request.

        Laya decides whether this is a coding request (run the modular project
        pipeline) or a question about the code (one answering turn).  Keyword
        matching was the wrong authority: "can you build me an app?" is an
        instruction, "how does the parser work?" is a question.
        """
        if self._chat_thread is not None and self._chat_thread.isRunning():
            self._append_chat('error', _('A request is already running.'))
            return
        if (getattr(self, '_pipeline_thread', None) is not None
                and self._pipeline_thread.isRunning()):
            self._append_chat('error', _('A request is already running.'))
            return
        self._reset_loop()
        self._request_goal = text
        # Task boundary: the previous request's turns are NOT this task's
        # context.  They were embedded in every new turn (`_ring_block`), so a
        # fresh request started out carrying another task's conversation.
        # The project on disk (script + files) is the real state the next
        # task works from.
        self._chat_history.clear()
        # Stop must be reachable for the whole request, not only for a Run:
        # one flag dict shared with every stream worker this request spawns.
        self._chat_stop_ref = {'flag': False}
        self._stop_btn.setEnabled(True)
        self._run_btn.setEnabled(False)
        self._append_chat('user', text)
        self._chat_history.append({'role': 'user', 'content': text})
        self._chat_history = self._chat_history[-MAX_CHAT_TURNS:]
        self._classify_request(text)

    def _classify_request(self, text):
        """Ask Laya (off-thread) what this request is; heuristic as fallback."""
        try:
            from player.code_agent_ops.execution import _IntentWorker
        except Exception:
            self._on_intent(None)
            return
        worker = _IntentWorker(text)
        worker.token = self._node_token
        worker.finished_intent.connect(self._on_intent)
        self._intent_worker = worker
        worker.start()

    def _on_intent(self, verdict):
        sender = self.sender()
        if (sender is not None
                and getattr(sender, 'token', None) != self._node_token):
            return                  # classification belongs to a switched node
        self._intent_worker = None
        if not self._request_goal:
            return                  # Stop pressed while we were asking Laya
        if verdict is None:
            # Engine unavailable: the wording heuristic is the only signal left.
            verdict = not _is_question(self._request_goal)
        else:
            self._append_console(
                _('Laya intent: {}').format(
                    _('coding request') if verdict else _('question')))
        self._request_is_question = not bool(verdict)
        self._await_main = bool(verdict)
        if self._request_is_question:
            self._send_agent_turn(first_turn_text(self._request_goal))
            return
        self._start_project_pipeline(self._request_goal)

    # ------------------------------------------------------------------
    # Modular project pipeline (plan -> one call per module -> entry)
    # ------------------------------------------------------------------

    def _start_project_pipeline(self, text):
        """Build the node's project module by module."""
        try:
            from player.code_agent_ops.parsing import _collect_project_files
            from player.code_agent_ops.project_runner import (
                _ProjectPipelineWorker)
        except Exception as e:
            self._append_chat('error',
                              _('Project pipeline unavailable: {}').format(e))
            self._reset_loop()
            return
        model, use_llamacpp = self._chat_model()
        if not model:
            model = 'llama3.2:latest'
        try:
            ws = self._workspace_root()
            existing = _collect_project_files(ws)
        except Exception:
            ws, existing = '', {}
        worker = _ProjectPipelineWorker(
            request=text, model=model, use_llamacpp=use_llamacpp,
            workspace=ws,
            inputs=self._collect_rows(self._in_rows),
            outputs=self._collect_rows(self._out_rows),
            existing_files=existing, stop_ref=self._chat_stop_ref)
        worker.token = self._node_token
        worker.owner_id = self._loaded_node_id
        worker.stage.connect(self._on_pipeline_stage)
        worker.trace.connect(self._on_pipeline_trace)
        worker.tasks_planned.connect(self._on_tasks_planned)
        worker.task.connect(self._on_task)
        worker.module_done.connect(self._on_pipeline_module)
        worker.finished_pipeline.connect(self._on_pipeline_finished)
        self._pipeline_thread = worker
        worker.start()

    def _on_pipeline_stage(self, msg):
        sender = self.sender()
        if (sender is not None
                and getattr(sender, 'token', None) != self._node_token):
            return
        self._append_chat('execution', str(msg))

    def _on_tasks_planned(self, names):
        sender = self.sender()
        if (sender is not None
                and getattr(sender, 'token', None) != self._node_token):
            return
        self._tasks_start(list(names or []))

    def _on_task(self, name, state, detail=''):
        sender = self.sender()
        if (sender is not None
                and getattr(sender, 'token', None) != self._node_token):
            return
        self._task_update(name, state, detail)

    def _on_pipeline_trace(self, text):
        """One line of model traffic (request or reply) -> Terminal page."""
        sender = self.sender()
        if (sender is not None
                and getattr(sender, 'token', None) != self._node_token):
            return
        self._append_console(str(text))

    def _on_pipeline_module(self, rel, ok, note):
        sender = self.sender()
        if (sender is not None
                and getattr(sender, 'token', None) != self._node_token):
            return
        self._append_console(('\u2713 ' if ok else '\u2717 ') + f'{rel}: {note}')

    def _on_pipeline_finished(self, result):
        sender = self.sender()
        if (sender is not None
                and getattr(sender, 'token', None) != self._node_token
                and getattr(sender, 'owner_id', None)
                != self._loaded_node_id):
            return
        self._pipeline_thread = None
        result = result or {}
        if result.get('stopped'):
            self._append_chat('info', _('Stopped by user.'))
            self._reset_loop()
            return
        if result.get('error') and not result.get('main_code'):
            self._append_chat('error', str(result.get('error')))
            self._reset_loop()
            return
        main_code = result.get('main_code') or ''
        if main_code:
            self._main_code = main_code
            self._active_file = MAIN_FILE
            self._code_edit.blockSignals(True)
            self._code_edit.setPlainText(main_code)
            self._code_edit.blockSignals(False)
            self._update_file_label()
            self._persist_node()
        self._refresh_files()
        from player.code_agent_ops.project import pipeline_summary
        self._append_assistant(pipeline_summary(
            result.get('main_desc', ''), result.get('modules') or [],
            result.get('failed') or []))
        if main_code:
            self._handle_applied_main()
        else:
            self._reset_loop()

    def _send_agent_turn(self, instruction):
        """Context card + ONE task instruction, under the role template.

        The template rotates with the request kind (`SYSTEM_EDIT` for work,
        `SYSTEM_ANSWER` for a question) instead of stacking a system prompt, a
        protocol card and a fix text that repeat the same rules.
        """
        if self._chat_thread is not None and self._chat_thread.isRunning():
            return
        same_turn = (instruction == self._last_sent)
        txt = '\n\n'.join(self._context_parts() + [instruction])
        # Tool output lives for exactly ONE turn: the turn that consumes it.
        # A retry of the same turn keeps it; any new turn starts from the
        # project state instead of re-reading a stale tool dump.
        if not same_turn:
            self._last_tool_results = None
        self._last_sent = instruction
        self._tok_lbl.setText(f'~{len(txt) // 4} tok')

        model, use_llamacpp = self._chat_model()
        if not model:
            model = 'llama3.2:latest'
        _in_chars, _tok = self._budget()
        trimmed = len(txt) > _in_chars
        if trimmed:
            txt = txt[-_in_chars:]      # keep the instruction; trim context tail
        # Trace EVERY model call into the Terminal page: without this a run
        # that ends in "the agent did not produce anything" is undiagnosable.
        self._append_console(
            '\u226b request  {}{} | {:,} chars (~{} tok in, {} out)'.format(
                model, ' [llamacpp]' if use_llamacpp else ' [ollama]',
                len(txt), len(txt) // 4, _tok))
        if trimmed:
            self._append_console('  ! prompt trimmed to the context budget')
        self._append_console(txt)
        logger.info('[CODER] request model=%s chars=%d max_tokens=%d',
                    model, len(txt), _tok)
        worker = _ChatStreamWorker(
            txt, model,
            use_llamacpp=use_llamacpp,
            system=(SYSTEM_ANSWER if self._request_is_question
                    else SYSTEM_EDIT),
            max_tokens=_tok,
            temperature=self._steer['temp'],
            repeat_penalty=self._steer['penalty'],
            stop_ref=self._chat_stop_ref,
        )
        # The owning node id: if the user switches nodes mid-stream these
        # signals are dropped by the handlers before they touch the new node.
        worker.token = self._node_token
        worker.owner_id = self._loaded_node_id
        worker.delta_stream.connect(self._on_stream_delta)
        worker.finished_stream.connect(self._on_stream_finished)
        worker.error_stream.connect(self._on_chat_error)
        self._chat_thread = worker
        # A stream is running from here (request start, retry, or a fix turn
        # after an auto-run): keep Stop armed and Run locked for its duration.
        self._stop_btn.setEnabled(True)
        self._run_btn.setEnabled(False)
        self._start_stream_ui()
        worker.start()

    # ------------------------------------------------------------------
    # Context card (single-file view shown to the model every turn)
    # ------------------------------------------------------------------

    def _context_parts(self):
        parts = []
        nid = str(getattr(self._node, 'id', '') or '?')
        parts.append(f'### Code node (id={nid}) - {MAIN_FILE} is the entry '
                     'script; project files beside it import normally')
        ivars = self._collect_rows(self._in_rows)
        ovars = self._collect_rows(self._out_rows)
        parts.append('### Declared input ports: ' + json.dumps(ivars))
        parts.append('### Declared output ports: ' + json.dumps(ovars))
        if self._connected_inputs:
            lines = []
            for ci in self._connected_inputs:
                lines.append(f"- {ci.get('input_port')} <- "
                             f"{ci.get('from_node')}:{ci.get('output_type')}")
            parts.append('### Connected inputs (auto-injected variables):\n'
                         + '\n'.join(lines))

        # The one script, always shown (clipped); longer parts are reachable
        # through the search / read tools.
        body = _clip(self._main_code, _MAX_CODE_CTX)
        outline = '; '.join(_ast_outline(self._main_code))
        parts.append(f'### {MAIN_FILE} (current code)\n' + body)
        parts.append('### Code outline: ' + outline)

        # Project files beside the entry script (the run puts this folder on
        # sys.path).  Small helpers are shown outright; the rest is listed so
        # the agent can ask the tools to read them.
        try:
            ws = self._workspace_root()
            files = [f for f in _list_workspace_files(ws) if f != MAIN_FILE]
        except Exception:
            ws, files = '', []
        if files:
            parts.append('### Project files (relative to the node workspace)\n'
                         + '\n'.join(files[:80]))
        for rel in files[:_MAX_HELPERS_IN_CTX]:
            try:
                with open(_safe_join(ws, rel), 'r', encoding='utf-8',
                          errors='replace') as f:
                    helper = f.read()
            except Exception:
                continue
            parts.append(f'### Project file: {rel}\n'
                         + _clip(helper, _MAX_HELPER_CTX))

        run = self._last_run_record or {}
        if run:
            parts.append('### Last run')
            parts.append(f"- mode: {run.get('mode')}, ok: {run.get('ok')}")
            for d in (run.get('digest') or []):
                parts.append(f'- {d}')
            if run.get('stdout'):
                parts.append('- stdout tail: ' +
                             _clip(run.get('stdout'), _MAX_RUN_STDOUT))
            if run.get('stderr'):
                parts.append('- stderr tail: ' +
                             _clip(run.get('stderr'), _MAX_RUN_STDERR))
            if run.get('error'):
                parts.append('- traceback tail: ' +
                             _clip(run.get('error'), _MAX_RUN_STDERR))
            if run.get('result') is not None:
                parts.append('- result preview: ' +
                             str(_clip(run.get('result'), _MAX_RUN_RESULT)))
        else:
            parts.append('### Last run: none yet')

        if self._last_tool_results:
            parts.append('### Last tool results\n' +
                         _clip(self._last_tool_results, 6000))

        ring = self._ring_block()
        if ring:
            parts.append('### Prior conversation (bounded history)\n' + ring)
        return parts

    def _ring_block(self):
        turns = self._chat_history[-(_MAX_HISTORY_TURNS * 2):]
        if not turns:
            return ''
        out = []
        for t in turns:
            label = 'You' if t.get('role') == 'user' else 'Agent'
            out.append(f'{label}: {_clip(t.get("content", ""), _MAX_HISTORY_CHARS)}')
        return '\n'.join(out)

    # ------------------------------------------------------------------
    # Live streaming UI
    # ------------------------------------------------------------------

    def _start_stream_ui(self):
        self._stream_text = ''
        self._tick = 0
        self._stream_started = False
        # One live assistant bubble whose body label is rewritten in place as
        # tokens arrive; the final render replaces it with parsed code blocks.
        self._live_frame = self._chat_add_bubble(
            'assistant', _('Working') + ' .', caption=_('Assistant'))
        self._live_lbl = getattr(self._live_frame, '_chat_body', None)
        if self._stream_timer is None:
            self._stream_timer = QTimer(self)
            self._stream_timer.setInterval(350)
            self._stream_timer.timeout.connect(self._on_stream_tick)
        self._stream_timer.start()

    def _on_stream_tick(self):
        self._tick += 1
        if self._stream_started or self._live_lbl is None:
            return
        self._live_lbl.setText(_('Working') + ' ' + '.' * (1 + self._tick % 4))

    def _finish_live(self):
        if self._stream_timer is not None and self._stream_timer.isActive():
            self._stream_timer.stop()
        if self._live_frame is not None:
            row = self._live_frame.parent()
            if row is not None:
                row.setParent(None)     # hide now; deleteLater is not flushed
                row.deleteLater()
            self._live_frame = None
            self._live_lbl = None
        self._stream_text = ''
        self._stream_started = False

    def _on_stream_delta(self, inc):
        sender = self.sender()
        if (sender is not None
                and getattr(sender, 'token', None) != self._node_token):
            return          # stream belongs to a node we switched away from
        if self._stream_timer is not None and self._stream_timer.isActive():
            self._stream_timer.stop()
        self._stream_started = True
        self._stream_text += inc
        if self._live_lbl is not None:
            self._live_lbl.setText(self._stream_text)
            self._chat_scroll_bottom()
        self._tok_lbl.setText(f'~{len(self._stream_text) // 4} tok')

    def _on_stream_finished(self, full):
        sender = self.sender()
        if (sender is not None
                and getattr(sender, 'token', None) != self._node_token):
            return          # late finish of a stream from a switched-away node
        self._finish_live()
        # Stop pressed while this reply was streaming: it must not be parsed,
        # applied, or fed into another turn.
        if self._loop_aborted:
            self._tok_lbl.setText('')
            return
        self._append_console('\u226a reply   {:,} chars'.format(len(str(full))))
        self._append_console(str(full) if str(full).strip() else '(empty reply)')
        logger.info('[CODER] reply chars=%d', len(str(full)))
        self._empty_streak = 0
        # A clean reply decays the sampler back to baseline so escalation only
        # ever spans the retries of ONE degenerate step.
        if self._steer['steers']:
            self._steer = {'penalty': STEER_PENALTY_BASE,
                           'temp': STEER_TEMP_BASE, 'steers': 0,
                           'maxtok': 1.0,
                           'tries': self._steer.get('tries', 0)}
        self._on_chat_finished(full)

    def _on_chat_error(self, msg):
        sender = self.sender()
        if (sender is not None
                and getattr(sender, 'token', None) != self._node_token):
            return          # error belongs to a switched-away node
        self._finish_live()
        self._append_chat('error', str(msg))
        self._append_console('\u2717 call failed: ' + str(msg))
        logger.warning('[CODER] call failed: %s', msg)
        self._tok_lbl.setText('')
        # Stop pressed: no salvage, no sampler escalation, no retry.
        if self._loop_aborted or 'Stopped by user' in str(msg):
            self._loop_aborted = True
            self._reset_loop()
            return
        self._last_fail_kind = _failure_kind(msg)
        if self._last_fail_kind == 'empty':
            # One nudge, then stop: a model/provider that returns no text at
            # all will not start producing code on the sixth identical call.
            self._empty_streak = getattr(self, '_empty_streak', 0) + 1
            if self._empty_streak >= 2:
                model, _lp = self._chat_model()
                self._append_chat('error', _(
                    'Stopped - the model keeps returning empty replies ({}).\n'
                    'Pick another model in the header (the one in Terminal is '
                    'what was called), or check that its provider is '
                    'configured.').format(model))
                self._reset_loop()
                return
        else:
            self._empty_streak = 0
        # Failure-adaptive retry: every aborted/errored reply is classified and
        # the sampler knobs move before the retry, so a re-asked turn never
        # replays the same token sequence.
        _adapt_sampler(self._steer, self._last_fail_kind)
        if (self._request_goal and not self._loop_aborted
                and not self._pending_run):
            instruction = self._last_sent or empty_reply_text()
            self._tool_turns += 1
            if self._tool_turns > MAX_TOOL_TURNS:
                self._append_chat('error', _('Stopped - the agent did not '
                    'produce an implementation after {} turns. The Terminal '
                    'page has the full request and reply of every call.')
                    .format(MAX_TOOL_TURNS))
                self._reset_loop()
                return
            self._append_chat('execution', _('The reply could not be '
                                             'completed - retrying.'))
            self._send_agent_turn(instruction)

    # ------------------------------------------------------------------
    # Reply handling (the actual loop)
    # ------------------------------------------------------------------

    def _on_chat_finished(self, text):
        clean = self._append_assistant(text)
        if clean:
            self._chat_history.append({'role': 'assistant',
                                       'content': _clip(clean, 1200)})
            self._chat_history = self._chat_history[-MAX_CHAT_TURNS:]

        # An odd number of fences means the model stopped inside a code block
        # - never apply a half-written file silently.
        if str(text).count('```') % 2 == 1:
            self._append_chat('error', _('Reply stopped mid-code - the code '
                                         'block was cut off.'))
            self._retry_turn(_('The previous reply was cut off mid-code.'),
                             truncated_text())
            return

        main_code, files = _parse_agent_reply(clean)

        # No code at all: either an answer to a question or an investigation
        # request the tools must serve.
        if main_code is None and not files:
            self._on_no_code_reply(clean)
            return

        # A FILE: script.py section counts as the main file.
        if main_code is None and MAIN_FILE in files:
            main_code = files.get(MAIN_FILE)      # FILE: script.py counts
        # The rest of the ### FILE: sections ARE the project: write them
        # beside script.py so sibling modules import (the entry script runs
        # with the workspace on sys.path).
        changed_files = self._apply_project_files(files)
        hunk_mode = bool(main_code and '<<<<<<< SEARCH' in main_code)

        if main_code is not None and hunk_mode:
            try:
                hunks = _parse_hunks(main_code)
            except ValueError as e:
                self._append_chat('error', _('Malformed edit block: {}')
                                  .format(str(e)))
                self._retry_turn(_('Malformed SEARCH/REPLACE block.'),
                                 edit_retry_text())
                return
            main_code, ok_h, h_err = _apply_hunks(self._main_code, hunks)
            if not ok_h:
                self._append_chat('error', _('Edit did not apply: {}')
                                  .format(h_err))
                self._retry_turn(_('The SEARCH side did not match the file.'),
                                 edit_retry_text(h_err))
                return
            self._append_console(_('Applied SEARCH/REPLACE edits.'))

        if main_code is not None and main_code != self._main_code:
            old_code = self._main_code
            self._main_code = main_code
            if self._active_file != MAIN_FILE:
                self._autosave_extra()
            self._active_file = MAIN_FILE
            self._code_edit.blockSignals(True)
            self._code_edit.setPlainText(self._main_code)
            self._code_edit.blockSignals(False)
            self._update_file_label()
            self._persist_node()
            diff = ''.join(difflib.unified_diff(
                old_code.splitlines(keepends=True),
                main_code.splitlines(keepends=True),
                fromfile=_('current'), tofile=MAIN_FILE, n=1,
            ))
            self._append_console(_('Main code updated and saved to the node:'))
            self._append_console(_clip(diff, 2200))
            self._handle_applied_main()
            return

        if main_code is not None and main_code == self._main_code:
            # The model resent the exact code that already failed - changing
            # nothing cannot fix it.
            self._append_chat('error', _('The code is identical to the current '
                                         'file - nothing to apply.'))
            self._retry_turn(_('The previous code was sent unchanged.'),
                             dup_reply_text())
            return

        # Code text existed but produced no change and no files.
        if changed_files:
            self._append_console(_('Project files updated: {}')
                                 .format(', '.join(changed_files)))
            self._persist_project_files()
            self._refresh_files()
            self._handle_applied_main()
            return
        self._on_no_code_reply(clean)

    def _apply_project_files(self, files):
        """Write the reply's ``### FILE:`` sections into the workspace.

        Bodies may be whole files or SEARCH/REPLACE hunks against the current
        content of that file.  Returns the changed relative paths.
        """
        changed = []
        for rel, body in (files or {}).items():
            rel = str(rel or '').strip().replace('\\', '/')
            if not rel or rel == MAIN_FILE:
                continue
            try:
                path = _safe_join(self._workspace_root(), rel)
            except Exception:
                self._append_console(_('Rejected file path: {}').format(rel))
                continue
            body = str(body or '')
            if '<<<<<<< SEARCH' in body:
                try:
                    with open(path, 'r', encoding='utf-8',
                              errors='replace') as f:
                        current = f.read()
                except Exception:
                    current = ''
                try:
                    hunks = _parse_hunks(body)
                except ValueError as e:
                    self._append_chat('error', _('Malformed edit block for ' +
                                                 '{}: {}').format(rel, e))
                    continue
                new, ok, err = _apply_hunks(current, hunks)
                if not ok:
                    self._append_chat('error', _('Edit did not apply to ' +
                                                 '{}: {}').format(rel, err))
                    continue
                body = new
            try:
                d = os.path.dirname(path)
                if d:
                    os.makedirs(d, exist_ok=True)
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(body)
                changed.append(rel)
            except Exception as e:
                self._append_chat('error',
                                  _('Could not write {}: {}').format(rel, e))
        if changed:
            self._append_console(_('Project files written: {}')
                                 .format(', '.join(changed)))
        return changed

    def _handle_applied_main(self):
        """A main-code change was applied: run it (headless) or finish."""
        self._rounds += 1
        self._last_applied_main = self._main_code
        self._await_main = False
        # Pre-run check.  A small model happily invents modules ("parsers",
        # "task_manager") that look like pip packages, and the GUI/loop code
        # below is never auto-run - so without this the model never hears
        # about the failure at all.  Static and offline: nothing executed.
        problem = self._prerun_problem()
        if problem:
            name, text = problem
            self._append_chat('error', _('Pre-run check failed: {}')
                              .format(name))
            if self._rounds >= MAX_WORK_ROUNDS:
                self._append_chat('error', _('Stopped - the code still fails '
                    'after {} rounds.').format(MAX_WORK_ROUNDS))
                self._reset_loop()
                return
            self._await_main = True
            self._send_agent_turn(text)
            return
        if _looks_like_shell_command(self._main_code):
            self._append_chat('execution', _('The code looks like a shell '
                'command, but Code nodes run pure Python. Ask the agent for '
                'real code, or use the Terminal tab.'))
            self._reset_loop()
            return
        if _is_gui_like(self._main_code):
            # A window is never auto-run in-process, but it IS checked: launch
            # it, screenshot it, and let the vision model report what is on
            # screen.  Same bounded fix loop as headless code.
            self._start_gui_check()
            if not self._pending_run:
                self._append_chat('info', _('The change is saved. Press Run to '
                                            'launch it.'))
                self._reset_loop()
            return
        self._start_agent_run()
        if not self._pending_run:
            # A run was already in flight; the change is saved, the loop stops.
            self._append_chat('info', _('The change is saved. Press Run to '
                                        'test it, then ask for fixes here.'))
            self._reset_loop()

    def _prerun_problem(self):
        """(label, message) for a static blocker, or None.

        Catches what the run would catch too late (or never, when the code is
        GUI-like and auto-run is skipped): a syntax error, or imports that
        resolve to nothing - not stdlib, not a project file, not installed.
        """
        try:
            compile(self._main_code, MAIN_FILE, 'exec')
        except SyntaxError as e:
            return (f'{type(e).__name__}: {e.msg}',
                    fix_error_text(f'{type(e).__name__}: {e.msg} (line '
                                   f'{e.lineno})'))
        except Exception:
            pass
        try:
            files = _collect_project_files(self._workspace_root())
        except Exception:
            files = {}
        try:
            bad = _unresolved_imports(self._main_code, files)
        except Exception:
            bad = []
        if bad:
            return (', '.join(bad), bad_imports_text(bad))
        return None

    def _agent_run_finished(self, record):
        """Route of an agent auto-run: clean run ends the request, a failed
        run asks for a fix (bounded)."""
        ok = bool(record.get('ok'))
        if ok:
            self._append_run_block(record, _('Run \u2713 ok'))
            if record.get('mode') == 'gui':
                self._append_chat('execution', _('The app opened and the '
                    'vision check passed - request complete ({} round{}).')
                    .format(self._rounds, '' if self._rounds == 1 else 's'))
                self._reset_loop()
                return
            self._append_chat('execution', _('Code runs clean - request '
                'complete ({} round{}).').format(
                    self._rounds, '' if self._rounds == 1 else 's'))
            self._reset_loop()
            return
        self._append_run_block(record, _('Run \u2717 failed - fixing '
                                         '(round {})').format(self._rounds))
        if self._loop_aborted:
            self._append_chat('execution', _('Run stopped by user.'))
            self._reset_loop(clear_abort=False)
            return
        if self._rounds >= MAX_WORK_ROUNDS:
            self._append_chat('error', _('Stopped - the code still fails after '
                '{} rounds. The draft stays saved in the Code tab - fix it '
                'manually or adjust the request.').format(MAX_WORK_ROUNDS))
            self._reset_loop()
            return
        err = (record.get('error') or record.get('stderr')
               or _('unknown error'))
        self._append_chat('execution', _('Asking the agent to fix the error.'))
        self._await_main = True
        tail = _clip(err, _MAX_RUN_STDERR)
        if record.get('mode') == 'gui':
            self._send_agent_turn(gui_report_text(tail))
        else:
            self._send_agent_turn(fix_error_text(tail))

    # ------------------------------------------------------------------
    # No-code replies: answer, or route to the dev tools
    # ------------------------------------------------------------------

    def _on_no_code_reply(self, clean):
        has_text = bool(str(clean or '').strip())
        # A genuine prose answer to a question ends the request (Laya decided
        # this request IS a question when it started).
        if (has_text and self._request_goal
                and self._request_is_question and not self._await_main):
            self._append_chat('execution', _('Question answered - no code '
                                             'requested.'))
            self._reset_loop()
            return
        if has_text and not self._await_main:
            self._append_chat('execution', _('No code change requested.'))
            self._reset_loop()
            return
        # Awaiting code (build/fix): try the dev tools; only when nothing is
        # tool-able does the reply count as a stall.
        if self._handle_investigation(clean):
            return
        self._stall_no_code(clean)

    def _stall_no_code(self, clean):
        self._append_console(
            _('No code block in the reply ({} chars). It said: {}')
            .format(len(str(clean or '')),
                    _clip(clean, 300).replace('\n', ' ') or '(nothing)'))
        self._retry_turn(_('No code in the reply.'), empty_reply_text())

    # ------------------------------------------------------------------
    # Retry plumbing
    # ------------------------------------------------------------------

    def _retry_turn(self, note, retry_text):
        self._tool_turns += 1
        if self._loop_aborted:
            self._reset_loop(clear_abort=False)
            return
        if self._tool_turns > MAX_TOOL_TURNS:
            self._append_chat('error', _('Stopped - the agent did not produce '
                'an implementation after {} turns. The Terminal page has the '
                'full request and reply of every call.')
                .format(MAX_TOOL_TURNS))
            self._append_console(_('Stopped after {} tool turns.')
                                 .format(MAX_TOOL_TURNS))
            self._reset_loop()
            return
        self._append_chat('execution', note)
        self._send_agent_turn(retry_text)

    def _reset_loop(self, clear_abort=True):
        """End the current agent request."""
        self._tasks_finish()
        self._request_goal = None
        self._request_is_question = False
        self._empty_streak = 0
        self._rounds = 0
        self._tool_turns = 0
        self._last_tool_results = None
        self._last_applied_main = None
        self._pending_run = False
        self._await_main = False
        self._last_sent = None
        self._chat_stop_ref = None
        self._steer = {'penalty': STEER_PENALTY_BASE,
                       'temp': STEER_TEMP_BASE, 'steers': 0,
                       'maxtok': 1.0, 'tries': 0}
        if clear_abort:
            self._loop_aborted = False
        if not self._pending_run and not self._loop_aborted:
            # Request truly over: hand the header buttons back to the user.
            self._run_btn.setEnabled(True)
            self._stop_btn.setEnabled(False)

    # ------------------------------------------------------------------
    # Chat rendering (real widget bubbles; QTextEdit rich text cannot draw
    # rounded corners or cap width, so its CSS "bubbles" rendered flat)
    # ------------------------------------------------------------------

    _BUBBLE_FRAME = {
        'user': 'ChatBubbleUser',
        'assistant': 'ChatBubble',
        'error': 'ChatBubbleError',
        'execution': 'ChatBubbleInfo',
        'info': 'ChatBubbleInfo',
    }

    def _caption(self, role):
        return {'user': _('You'), 'assistant': _('Assistant'),
                'error': _('Error'), 'execution': _('Execution'),
                'info': _('Info')}.get(role, _('Info'))

    def _chat_add_bubble(self, role, text, footnote='', mono=False,
                         caption=None, label_color=None):
        """Build one bubble (right for user, left otherwise) as real widgets so
        the rounded background actually paints. Code fences become boxed
        monospace blocks (or the whole body with ``mono``). Returns the frame;
        its ``_chat_body`` is the first content label (the live stream
        rewrites that in place)."""
        row = QWidget()
        # Named so the studio sheet can keep it transparent: an unnamed row is
        # painted by the host window's inherited QWidget rule (GRAPH_PLANE,
        # a lighter grey) and shows as a band behind the bubble.
        row.setObjectName('ChatRow')
        rowlay = QHBoxLayout(row)
        rowlay.setContentsMargins(0, 0, 0, 0)
        rowlay.setSpacing(0)
        frame = QFrame()
        frame.setObjectName(self._BUBBLE_FRAME.get(role, 'ChatBubble'))
        box = QVBoxLayout(frame)
        box.setContentsMargins(10, 6, 10, 8)
        box.setSpacing(4)

        cap = QLabel(caption or self._caption(role))
        cap.setObjectName('ChatRole')
        if label_color:
            cap.setStyleSheet(f'color: {label_color}; background: transparent;')
        box.addWidget(cap)

        body = None
        natural = 0
        segs = [('code', str(text))] if mono else _chat_segments(text)
        for kind, seg in segs:
            lbl = QLabel()
            lbl.setObjectName('ChatCode' if kind == 'code' else 'ChatText')
            lbl.setTextFormat(Qt.PlainText)   # keep newlines / raw source
            lbl.setWordWrap(True)
            lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lbl.setText(seg)
            box.addWidget(lbl)
            if body is None:
                body = lbl
            # Widest rendered line: a word-wrapped QLabel's size hint is only
            # its widest WORD, which collapsed bubbles into narrow columns.
            fm = lbl.fontMetrics()
            for line in seg.split('\n'):
                natural = max(natural, fm.horizontalAdvance(line))
        if footnote:
            dim = QLabel(footnote)
            dim.setObjectName('ChatRole')
            dim.setWordWrap(True)
            box.addWidget(dim)
        frame._chat_body = body

        # Hug the text up to ~82% of the viewport: a min width stops the
        # word-wrap collapse, the max makes long replies wrap instead of
        # spanning the full pane. Measured once at insertion (no reflow on
        # window resize - acceptable for a chat log).
        vp = self._chat_scroll.viewport().width()
        max_w = int(vp * 0.82) if vp > 200 else 520
        frame.setMaximumWidth(max_w)
        frame.setMinimumWidth(min(max_w, max(180, natural + 40)))
        if role == 'user':
            rowlay.addStretch(1)
            rowlay.addWidget(frame, 0, Qt.AlignRight | Qt.AlignTop)
        else:
            rowlay.addWidget(frame, 0, Qt.AlignLeft | Qt.AlignTop)
            rowlay.addStretch(1)
        self._chat_lay.insertWidget(self._chat_lay.count() - 1, row)
        return frame

    def _chat_scroll_bottom(self):
        sb = self._chat_scroll.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())

    # ------------------------------------------------------------------
    # Task list card (whole plan of the running request, live states)
    # ------------------------------------------------------------------
    def _tasks_start(self, names):
        """Open the task card for a new request (one card per request)."""
        from .task_list import TaskListCard
        self._tasks_finish()
        card = TaskListCard()
        for raw in names or []:
            card.add(str(raw))
        self._chat_lay.insertWidget(self._chat_lay.count() - 1, card)
        self._task_card = card
        self._chat_scroll_bottom()

    def _task_update(self, name, state, detail=''):
        if not getattr(self, '_task_card', None):
            self._tasks_start([name])
        self._task_card.update(str(name), str(state), str(detail or ''))
        self._chat_scroll_bottom()

    def _tasks_finish(self):
        card = getattr(self, '_task_card', None)
        if card is not None:
            card.finish()
            self._task_card = None

    def _chat_clear(self):
        # Drop every row but keep the trailing stretch anchor.
        while self._chat_lay.count() > 1:
            item = self._chat_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)       # detach now; deleteLater is not
                w.deleteLater()         # flushed by processEvents
        self._chat_log = []

    def _append_chat(self, role, text):
        text = str(text)
        self._chat_add_bubble(role, text)
        self._chat_log.append(('chat', role, text))
        self._chat_log = self._chat_log[-600:]
        self._chat_scroll_bottom()

    def _append_assistant(self, text):
        """Final assistant bubble: reasoning blocks collapse to one dim line,
        code fences render as boxed monospace previews."""
        clean, note = _collapse_reasoning(text)
        self._chat_add_bubble('assistant', clean, footnote=note)
        self._chat_log.append(('assistant', clean, note))
        self._chat_log = self._chat_log[-600:]
        self._chat_scroll_bottom()
        return clean

    def _replay_chat_log(self):
        """Rebuild the bubbles from a restored ``_chat_log`` (the log itself
        is kept as-is so a later node switch snapshots the same entries)."""
        for e in list(self._chat_log):
            if e[0] == 'assistant':
                self._chat_add_bubble('assistant', e[1], footnote=e[2])
            elif e[0] == 'run':
                self._chat_add_bubble(
                    'execution', e[1], mono=True, caption=e[2],
                    label_color=ACCENT_COLOR if e[3] else DANGER_COLOR)
            else:
                self._chat_add_bubble(e[1], e[2])
        self._chat_scroll_bottom()
