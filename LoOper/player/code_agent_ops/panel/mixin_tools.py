"""CodeNodePanel part: Needle 2 dev-tool routing.

When the coding agent replies with no code and no question answer, its plain
request ("search for 'items'", "show the whole code", "run the node") is
routed through the bundled Needle 2 engine: the engine returns structured,
grammar-constrained tool calls which the panel executes deterministically
against the node's single script, and the results feed the next agent turn.
When the engine binary is not bundled, a strict legacy "### GREP:" marker
fallback keeps the loop working offline.

App root = the LoOper/ directory (top-level packages NGUI/AI/player).
"""
import logging
import os
import re

from PyQt5.QtCore import QThread, pyqtSignal

from NGUI.i18n import _
from player.code_agent_ops.constants import MAX_TOOL_TURNS
from player.code_agent_ops.devtools import (
    NeedleRouter,
    execute_call,
)
from player.code_agent_ops.parsing import _clip
from player.code_agent_ops.prompts import no_tool_text, tool_results_text

logger = logging.getLogger(__name__)


class _InvestigationWorker(QThread):
    """Run one Needle 2 investigation chain off the UI thread."""

    finished_investigation = pyqtSignal(object)

    def __init__(self, router, query):
        super().__init__()
        self._router = router
        self._query = query

    def run(self):
        try:
            payload = self._router.route(self._query)
        except Exception as e:
            logger.exception('needle investigation failed')
            payload = {'ok': False, 'empty': True, 'text': '', 'steps': 0,
                       'error': str(e)}
        payload['query'] = self._query
        self.finished_investigation.emit(payload)


class ToolsMixin(object):
    """Deterministic dev tools (single-script scope) for the agent loop."""

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def _handle_investigation(self, clean):
        """Consume a no-code agent reply as an investigation request.

        Returns True when the reply was consumed (a Needle chain started, or
        the legacy marker fallback ran).  Returns False when the reply is not
        tool-able so the caller can treat it as a stall/answer."""
        text = str(clean or '').strip()
        if not text:
            return False
        if self._loop_aborted or not self._request_goal:
            return False
        if getattr(self, '_invest_worker', None) is not None \
                and self._invest_worker.isRunning():
            return True                      # busy - ignore until it finishes
        if self._tool_turns >= MAX_TOOL_TURNS:
            return False
        router = NeedleRouter(self._tool_ctx())
        if router.available():
            self._tool_turns += 1
            self._append_chat('execution',
                              _('Tool request: {}').format(_clip(text, 180)))
            self._invest_worker = _InvestigationWorker(router, text)
            self._invest_worker.token = self._node_token
            self._invest_worker.owner_id = self._loaded_node_id
            self._invest_worker.finished_investigation.connect(
                self._on_investigation_finished)
            self._invest_worker.start()
            return True
        return self._legacy_search(text)

    def _on_investigation_finished(self, payload):
        sender = self.sender()
        # Only clear the reference when the FINISHING worker is the current
        # one - a stale worker from a switched-away node must not null a
        # newer investigation that is still running.
        if self._invest_worker is sender:
            self._invest_worker = None
        if (sender is not None
                and getattr(sender, 'token', None) != self._node_token):
            return          # investigation belongs to a switched-away node
        # A new request may have started (or Stop was pressed) while the
        # engine was decoding - never inject stale results into it.
        if not self._request_goal or self._loop_aborted:
            self._append_console(_('Tool results discarded - request ended.'))
            return
        query = payload.get('query') or ''
        if not payload.get('ok'):
            err = payload.get('error') or _('unknown tool engine error')
            self._append_chat('error', _('Tool engine error: {}').format(err))
            self._append_console(_('Tool engine error: {}').format(err))
            self._stall_no_code(query)
            return
        text = payload.get('text') or ''
        if not text or payload.get('empty'):
            # Nothing tool-able: the coding model must restate or produce code.
            self._retry_turn(_('The request did not match any dev tool.'),
                             no_tool_text(), show_protocol=True)
            return
        self._last_tool_results = text
        self._append_chat('execution',
                          '### Dev tools\n' + _clip(text, 6000))
        self._append_console('### Dev tools\n' + _clip(text, 4000))
        self._await_main = True
        self._send_agent_turn(tool_results_text(), show_protocol=True)

    # ------------------------------------------------------------------
    # Context for the tool executor (single-script snapshot)
    # ------------------------------------------------------------------

    def _tool_ctx(self):
        """Read-only view of the node script at route time."""
        code = self._main_code          # snapshot: never mutate mid-chain
        ws = self._workspace_root()
        node_id = str(getattr(self._node, 'id', '') or 'studio_code')

        def node_dict():
            cfg = dict(self._config)
            cfg['code'] = code
            cfg['input_vars'] = self._collect_rows(self._in_rows)
            cfg['output_vars'] = self._collect_rows(self._out_rows)
            return {
                'type': 'code',
                'id': node_id,
                'node_id': node_id,
                'data': cfg,
                'inputs': self._runtime_inputs_for(node_id),
            }

        return {
            'code': code,
            'run_node_dict': node_dict,
            'player': self._last_player,
            'chain_root': os.path.dirname(os.path.dirname(ws)),
        }

    # ------------------------------------------------------------------
    # Legacy marker fallback (runs when needle.exe is not bundled)
    # ------------------------------------------------------------------

    def _legacy_search(self, text):
        """Strict '### GREP:' marker over the single script (loose anchor:
        the marker may appear anywhere in the reply)."""
        markers = re.findall(r'###\s*GREP\s*:\s*(.+)', str(text or ''),
                             re.IGNORECASE)
        if not markers:
            return False
        if self._tool_turns >= MAX_TOOL_TURNS:
            return False
        self._tool_turns += 1
        out = []
        for m in markers[:4]:
            pattern = m.strip().strip('`').strip()
            if pattern:
                out.append('## search for: ' + pattern + '\n' +
                           execute_call('search_code', {'pattern': pattern},
                                        self._tool_ctx()))
        if not out:
            return False
        res = '\n\n'.join(out)
        self._last_tool_results = res
        self._append_chat('execution', '### Dev tools\n' + _clip(res, 6000))
        self._append_console('### Dev tools\n' + _clip(res, 4000))
        self._await_main = True
        self._send_agent_turn(tool_results_text(), show_protocol=True)
        return True
