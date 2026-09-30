"""Run / shell workers for the Code Node Studio.

``_run_node_via`` replays the node exactly like a chain run: a fresh
WorkflowExecutor (studio namespace, so the live player is never mutated)
seeded with the retained player's port-store entries and producer legacy
variables, plus the chain venv environment.  Everything runs off the UI
thread through small QThread workers.
"""
import logging
import os
import subprocess
import tempfile
import traceback

from PyQt5.QtCore import QThread, pyqtSignal

from NGUI.i18n import _
from player.code_agent_ops.constants import RESERVED_INPUT_PORTS
from player.code_agent_ops.parsing import (
    _clip,
    _digest_line,
    _resolve_chain_runtime_dir,
)

logger = logging.getLogger(__name__)

__all__ = [
    '_StudioLLM',
    '_run_node_via',
    '_CodeRunWorker',
    '_ShellWorker',
    '_ModelRefreshWorker',
]


class _StudioLLM:
    """Minimal llm_executor stand-in: a flat variables dict like LLMExecutor."""

    def __init__(self):
        self.variables = {}
        self.workflow_graph = None

    def get_variable(self, name, default=None):
        return self.variables.get(name, default)

    def set_variable(self, name, value):
        self.variables[name] = value

    def get_all_variables(self):
        return dict(self.variables)


# ---------------------------------------------------------------------------
# Run worker
# ---------------------------------------------------------------------------

def _run_node_via(player, node, stop_ref, chain_root=None):
    """Execute a code-node dict against real upstream data (see module doc)."""
    from player.multi_sequence.worflow_interpreter_modules.executor_modules.core import WorkflowExecutor

    llm = _StudioLLM()
    exe = WorkflowExecutor({}, None, llm, object())
    exe.chain_id = 'code_studio'
    exe._runtime_initialized = True

    src = None
    if player is not None and hasattr(player, 'workflow_executor'):
        src = player.workflow_executor

    mode = 'replay' if src is not None else 'solo'
    if src is not None:
        for attr in ('_chain_runtime_dir', '_venv_dir', '_venv_python',
                     '_venv_site_packages'):
            try:
                setattr(exe, attr, getattr(src, attr, None))
            except Exception:
                pass
        # Seed the studio port store with every value from the real run, so
        # upstream ctx_out / named-output lookups resolve exactly as in-chain.
        try:
            for (_ch, _nid, _port), _val in list(src.port_store._store.items()):
                exe.port_store.set_output('code_studio', _nid, _port, _val)
        except Exception:
            pass
        # Producers that only wrote legacy variables (node_{from}_*) also need
        # to resolve: copy every such key for the referenced upstream ids.
        try:
            from_ids = {str(i.get('from_node') or '')
                        for i in (node.get('inputs') or [])}
            for _k, _v in list(getattr(src.llm_executor, 'variables',
                                       {}).items()):
                if any(str(_k).startswith(f'node_{fid}_')
                       for fid in from_ids if fid):
                    llm.variables[_k] = _v
        except Exception:
            pass
    else:
        solo_chain = None
        try:
            solo_chain = chain_root or _resolve_chain_runtime_dir(None)
            os.makedirs(solo_chain, exist_ok=True)
        except Exception:
            pass
        try:
            exe._chain_runtime_dir = solo_chain or tempfile.mkdtemp(
                prefix='code_studio_')
        except Exception:
            exe._chain_runtime_dir = tempfile.mkdtemp(prefix='code_studio_')
        # GUI/persistent code (tkinter app, mainloop, ...) needs a REAL venv
        # interpreter plus auto-installed deps to actually launch. Plain code
        # keeps the lightweight in-process stub path.
        _persistent_code = False
        try:
            _code_src = (node.get('data') or {}).get('code') or ''
            _persistent_code = bool(exe._is_persistent_loop(_code_src))
        except Exception:
            pass
        if not _persistent_code:
            exe._ensure_chain_venv = lambda: None
            exe._install_code_dependencies = lambda s: {
                'installed': [], 'failed': [], 'skipped': [],
                'pip_stdout': '', 'pip_stderr': '',
            }
        else:
            # Pin the venv beside the pinned chain folder
            # (runtime/venvs/<label>) so the GUI app boots from the same
            # interpreter real chain runs use.
            try:
                _root = os.path.dirname(
                    os.path.dirname(exe._chain_runtime_dir))
                exe._chain_label = os.path.basename(exe._chain_runtime_dir)
                exe._venv_dir = os.path.join(_root, 'venvs',
                                             exe._chain_label)
                os.makedirs(exe._venv_dir, exist_ok=True)
            except Exception:
                pass

    try:
        exe._execute_code_node(node,
                               stop_flag=lambda: bool(stop_ref.get('flag')))
    except Exception:
        nid = node.get('id') or node.get('node_id') or 'studio_code'
        llm.variables[f'node_{nid}_error'] = traceback.format_exc()

    nid = node.get('id') or node.get('node_id') or 'studio_code'

    def gv(key):
        try:
            return llm.variables.get(key)
        except Exception:
            return None

    error = gv(f'node_{nid}_error')
    result = gv(f'node_{nid}_result')
    stdout = gv(f'node_{nid}_stdout') or ''
    stderr = gv(f'node_{nid}_stderr') or ''
    terminal = gv(f'node_{nid}_terminal_output') or ''

    digest = []
    for inp in (node.get('inputs') or []):
        pname = inp.get('input_port')
        pname = str(pname) if pname is not None else ''
        if not pname or pname in RESERVED_INPUT_PORTS or not pname.isidentifier():
            continue
        src_txt = f"{inp.get('from_node')}:{inp.get('output_type')}"
        digest.append(_digest_line(pname, llm.variables.get(pname), src_txt))

    return {
        'ok': error is None,
        'result': result,
        'stdout': stdout,
        'stderr': stderr,
        'terminal': terminal,
        'error': error,
        'mode': mode,
        'digest': digest,
    }


class _CodeRunWorker(QThread):
    finished_run = pyqtSignal(object)

    def __init__(self, node, player_provider, stop_ref, chain_root=None):
        super().__init__()
        self._node = node
        self._provider = player_provider
        self._stop_ref = stop_ref
        self._chain_root = chain_root

    def run(self):
        try:
            player = self._provider()
        except Exception:
            player = None
        try:
            record = _run_node_via(player, self._node, self._stop_ref,
                                   chain_root=self._chain_root)
        except Exception as e:  # last-resort so the UI thread is never blocked
            record = {'ok': False, 'error': str(e), 'stdout': '', 'stderr': '',
                      'terminal': '', 'result': None, 'mode': 'error',
                      'digest': []}
        self.finished_run.emit(record)


# ---------------------------------------------------------------------------
# Terminal shell worker (runs one command in the node workspace/venv)
# ---------------------------------------------------------------------------

class _ShellWorker(QThread):
    finished_cmd = pyqtSignal(bool, str)

    def __init__(self, cmd, cwd, venv_scripts):
        super().__init__()
        self._cmd = cmd
        self._cwd = cwd
        self._venv_scripts = venv_scripts

    def run(self):
        try:
            env = dict(os.environ)
            if self._venv_scripts:
                env['PATH'] = (self._venv_scripts + os.pathsep +
                               env.get('PATH', ''))
            proc = subprocess.run(
                self._cmd, shell=True, cwd=self._cwd, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, text=True, errors='replace',
            )
            out = (proc.stdout or '').strip()
            self.finished_cmd.emit(proc.returncode == 0, out)
        except Exception as e:
            self.finished_cmd.emit(False, str(e))


class _ModelRefreshWorker(QThread):
    """Refresh the Ollama model list off the UI thread."""
    finished_models = pyqtSignal(list)

    def run(self):
        try:
            from AI.model_cache import get_model_cache, get_real_cached_models
            from NGUI.nodes_resources.base_node import get_default_api_url
            cache = get_model_cache()
            if cache is not None:
                cache.refresh_models(get_default_api_url())
            models = get_real_cached_models(timeout=5.0)
        except Exception:
            models = []
        self.finished_models.emit(list(models))
