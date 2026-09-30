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

from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QColor, QTextCharFormat, QTextCursor

from NGUI.constants import ACCENT_COLOR
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
    _is_gui_like,
    _is_question,
    _looks_like_shell_command,
    _parse_agent_reply,
    _parse_hunks,
)
from player.code_agent_ops.prompts import (
    SYSTEM_AGENT,
    dup_reply_text,
    edit_retry_text,
    empty_reply_text,
    first_turn_text,
    fix_error_text,
    protocol_card,
    truncated_text,
)

logger = logging.getLogger(__name__)


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
            self._chat_view.clear()
            self._reset_loop()
        else:
            self._append_chat('error',
                              _('Unknown command: {} (try /help)').format(text))

    def _start_request(self, text):
        """Begin one flat agent request."""
        if self._chat_thread is not None and self._chat_thread.isRunning():
            self._append_chat('error', _('A request is already running.'))
            return
        self._reset_loop()
        self._request_goal = text
        self._await_main = not _is_question(text)
        self._append_chat('user', text)
        self._chat_history.append({'role': 'user', 'content': text})
        self._chat_history = self._chat_history[-MAX_CHAT_TURNS:]
        self._send_agent_turn(first_turn_text(text), show_protocol=True)

    def _send_agent_turn(self, instruction, show_protocol=False):
        """Assemble the context card + guidance + instruction and stream."""
        if self._chat_thread is not None and self._chat_thread.isRunning():
            return
        parts = self._context_parts()
        txt = '\n\n'.join(parts)
        if show_protocol:
            txt += '\n\n' + protocol_card()
        txt += '\n\n' + instruction
        self._last_sent = (instruction, show_protocol)
        self._tok_lbl.setText(f'~{len(txt) // 4} tok')

        model, use_llamacpp = self._chat_model()
        if not model:
            model = 'llama3.2:latest'
        _in_chars, _tok = self._budget()
        if len(txt) > _in_chars:
            txt = txt[-_in_chars:]      # keep the instruction; trim context tail
        worker = _ChatStreamWorker(
            txt, model,
            use_llamacpp=use_llamacpp,
            system=SYSTEM_AGENT,
            max_tokens=_tok,
            temperature=self._steer['temp'],
            repeat_penalty=self._steer['penalty'],
        )
        # The owning node id: if the user switches nodes mid-stream these
        # signals are dropped by the handlers before they touch the new node.
        worker.token = self._node_token
        worker.owner_id = self._loaded_node_id
        worker.delta_stream.connect(self._on_stream_delta)
        worker.finished_stream.connect(self._on_stream_finished)
        worker.error_stream.connect(self._on_chat_error)
        self._chat_thread = worker
        self._start_stream_ui()
        worker.start()

    # ------------------------------------------------------------------
    # Context card (single-file view shown to the model every turn)
    # ------------------------------------------------------------------

    def _context_parts(self):
        parts = []
        nid = str(getattr(self._node, 'id', '') or '?')
        parts.append(f'### Code node (id={nid}) - {MAIN_FILE} is the whole '
                     'script (single self-contained file)')
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
        c = self._chat_view.textCursor()
        c.movePosition(QTextCursor.End)
        c.insertBlock()                       # own paragraph for the live text
        self._stream_start_pos = c.position()
        if self._stream_timer is None:
            self._stream_timer = QTimer(self)
            self._stream_timer.setInterval(350)
            self._stream_timer.timeout.connect(self._on_stream_tick)
        self._live_write(_('Working') + ' ', '#8A97A5')
        self._stream_timer.start()

    def _on_stream_tick(self):
        self._tick += 1
        if self._stream_started:
            return
        self._live_clear()
        self._live_write(_('Working') + ' ' + '.' * (1 + self._tick % 4),
                         '#8A97A5')

    def _live_write(self, text, color='#FFD9A3'):
        c = self._chat_view.textCursor()
        c.movePosition(QTextCursor.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        c.insertText(text, fmt)
        sb = self._chat_view.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())

    def _live_clear(self):
        pos = self._stream_start_pos
        if pos is None:
            return
        c = self._chat_view.textCursor()
        c.setPosition(pos)
        c.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)
        c.removeSelectedText()

    def _finish_live(self):
        if self._stream_timer is not None and self._stream_timer.isActive():
            self._stream_timer.stop()
        self._live_clear()
        if self._stream_start_pos is not None:
            c = self._chat_view.textCursor()
            c.setPosition(self._stream_start_pos)
            c.deletePreviousChar()
            self._stream_start_pos = None
        self._stream_text = ''
        self._stream_started = False

    def _on_stream_delta(self, inc):
        sender = self.sender()
        if (sender is not None
                and getattr(sender, 'token', None) != self._node_token):
            return          # stream belongs to a node we switched away from
        if self._stream_timer is not None and self._stream_timer.isActive():
            self._stream_timer.stop()
        if not self._stream_started:
            self._stream_started = True
            self._live_clear()          # replace the 'Working…' pulse
        self._stream_text += inc
        self._live_write(inc)
        self._tok_lbl.setText(f'~{len(self._stream_text) // 4} tok')

    def _on_stream_finished(self, full):
        sender = self.sender()
        if (sender is not None
                and getattr(sender, 'token', None) != self._node_token):
            return          # late finish of a stream from a switched-away node
        self._finish_live()
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
        self._tok_lbl.setText('')
        self._last_fail_kind = _failure_kind(msg)
        # Failure-adaptive retry: every aborted/errored reply is classified and
        # the sampler knobs move before the retry, so a re-asked turn never
        # replays the same token sequence.
        _adapt_sampler(self._steer, self._last_fail_kind)
        if (self._request_goal and not self._loop_aborted
                and not self._pending_run):
            instruction, show_protocol = self._last_sent or (
                empty_reply_text(), True)
            self._tool_turns += 1
            if self._tool_turns > MAX_TOOL_TURNS:
                self._append_chat('error', _('Stopped - the agent did not '
                                             'produce an implementation.'))
                self._reset_loop()
                return
            self._append_chat('execution', _('The reply could not be '
                                             'completed - retrying.'))
            self._send_agent_turn(instruction, show_protocol=show_protocol)

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
                             truncated_text(), show_protocol=False)
            return

        main_code, files = _parse_agent_reply(clean)

        # No code at all: either an answer to a question or an investigation
        # request the tools must serve.
        if main_code is None and not files:
            self._on_no_code_reply(clean)
            return

        # Single-file scope: any FILE section other than script.py is ignored.
        ignored = [rel for rel in (files or {}) if rel != MAIN_FILE]
        if ignored:
            self._append_console(_('Ignored extra file(s) - this node holds '
                                   'ONE self-contained script: {}')
                                 .format(', '.join(ignored)))
        if main_code is None and MAIN_FILE in files:
            main_code = files.get(MAIN_FILE)      # FILE: script.py counts
        hunk_mode = bool(main_code and '<<<<<<< SEARCH' in main_code)

        if main_code is not None and hunk_mode:
            try:
                hunks = _parse_hunks(main_code)
            except ValueError as e:
                self._append_chat('error', _('Malformed edit block: {}')
                                  .format(str(e)))
                self._retry_turn(_('Malformed SEARCH/REPLACE block.'),
                                 edit_retry_text(), show_protocol=False)
                return
            main_code, ok_h, h_err = _apply_hunks(self._main_code, hunks)
            if not ok_h:
                self._append_chat('error', _('Edit did not apply: {}')
                                  .format(h_err))
                self._retry_turn(_('The SEARCH side did not match the file.'),
                                 edit_retry_text(h_err), show_protocol=False)
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
                             dup_reply_text(), show_protocol=False)
            return

        # Code text existed but produced no change and no files.
        self._on_no_code_reply(clean)

    def _handle_applied_main(self):
        """A main-code change was applied: run it (headless) or finish."""
        self._rounds += 1
        self._last_applied_main = self._main_code
        self._await_main = False
        if (_is_gui_like(self._main_code)
                or _looks_like_shell_command(self._main_code)):
            self._append_chat('execution', _('The code opens a window or runs '
                'a persistent loop - auto-run is skipped. Press Run to launch '
                'it, then describe any issue here.'))
            self._reset_loop()
            return
        self._start_agent_run()
        if not self._pending_run:
            # A run was already in flight; the change is saved, the loop stops.
            self._append_chat('info', _('The change is saved. Press Run to '
                                        'test it, then ask for fixes here.'))
            self._reset_loop()

    def _agent_run_finished(self, record):
        """Route of an agent auto-run: clean run ends the request, a failed
        run asks for a fix (bounded)."""
        ok = bool(record.get('ok'))
        if ok:
            self._append_run_block(record, _('Run \u2713 ok'))
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
        self._send_agent_turn(fix_error_text(_clip(err, _MAX_RUN_STDERR)),
                              show_protocol=False)

    # ------------------------------------------------------------------
    # No-code replies: answer, or route to the dev tools
    # ------------------------------------------------------------------

    def _on_no_code_reply(self, clean):
        has_text = bool(str(clean or '').strip())
        # A genuine prose answer to a question ends the request.
        if (has_text and self._request_goal
                and _is_question(self._request_goal) and not self._await_main):
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
        self._retry_turn(_('No code in the reply.'),
                         empty_reply_text(), show_protocol=True)

    # ------------------------------------------------------------------
    # Retry plumbing
    # ------------------------------------------------------------------

    def _retry_turn(self, note, retry_text, show_protocol):
        self._tool_turns += 1
        if self._loop_aborted:
            self._reset_loop(clear_abort=False)
            return
        if self._tool_turns > MAX_TOOL_TURNS:
            self._append_chat('error', _('Stopped - the agent did not produce '
                                         'an implementation.'))
            self._reset_loop()
            return
        self._append_chat('execution', note)
        self._send_agent_turn(retry_text, show_protocol=show_protocol)

    def _reset_loop(self, clear_abort=True):
        """End the current agent request."""
        self._request_goal = None
        self._rounds = 0
        self._tool_turns = 0
        self._last_tool_results = None
        self._last_applied_main = None
        self._pending_run = False
        self._await_main = False
        self._last_sent = None
        self._steer = {'penalty': STEER_PENALTY_BASE,
                       'temp': STEER_TEMP_BASE, 'steers': 0,
                       'maxtok': 1.0, 'tries': 0}
        if clear_abort:
            self._loop_aborted = False

    # ------------------------------------------------------------------
    # Chat rendering
    # ------------------------------------------------------------------

    def _append_chat(self, role, text):
        theme = {
            'user':       ('#0E2B28', '#BDF0E7', ACCENT_COLOR, _('You')),
            'error':      ('#2A1010', '#FFB4B4', '#EF4444', _('Error')),
            'execution':  ('#0F1A22', '#9FB3C0', '#5E81AC', _('Execution')),
            'info':       ('#101B22', '#8AA0B5', '#3B82F6', _('Info')),
        }
        bg, fg, accent, label = theme.get(role, theme['info'])
        safe = (str(text).replace('&', '&amp;').replace('<', '&lt;')
                .replace('>', '&gt;').replace('\n', '<br>'))
        bubble = (
            f'<div style="background:{bg}; border-left:3px solid {accent}; '
            f'border-radius:6px; padding:6px 8px; margin:6px 2px;">'
            f'<span style="color:{fg}; font-size:10px; font-weight:600;">'
            f'{label}</span><br>'
            f'<span style="color:{fg};">{safe}</span></div>'
        )
        self._chat_view.append(bubble)
        sb = self._chat_view.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())

    def _append_assistant(self, text):
        """Final assistant bubble: reasoning blocks collapse to one dim line."""
        clean, note = _collapse_reasoning(text)
        safe = (clean.replace('&', '&amp;').replace('<', '&lt;')
                .replace('>', '&gt;').replace('\n', '<br>'))
        note_html = ''
        if note:
            note_html = ('<span style="color:#9A8270; font-size:9px; '
                         'font-style:italic;">'
                         + note.replace('&', '&amp;').replace('<', '&lt;')
                               .replace('>', '&gt;') + '</span><br>')
        bubble = (
            f'<div style="background:#2A1C0B; border-left:3px solid #FFB347; '
            f'border-radius:6px; padding:6px 8px; margin:6px 2px;">'
            f'<span style="color:#FFD9A3; font-size:10px; font-weight:600;">'
            f'{_("Assistant")}</span><br>'
            + note_html +
            f'<span style="color:#FFD9A3;">{safe}</span></div>'
        )
        self._chat_view.append(bubble)
        sb = self._chat_view.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())
        return clean
