"""devtools.py — deterministic dev tools for the Code Node Studio agent.

Scope: the code node's ONE script (script.py).  No workspace browsing, no
other code nodes — the agent operates on the isolated script stored in the
node, exactly as the user request demands.

Two layers:

- ``TOOL_SCHEMAS``: the strict JSON-schema catalogue.  Needle 2 compiles a
  byte-level grammar from these schemas, so a returned call is always
  well-formed (deterministic protocol, not free-text markers).
- ``execute_call()``: the deterministic Python execution of one returned
  call against the live script (search_code / read_code / run_node).

``NeedleRouter`` drives a short investigation chain (query -> call ->
execute -> result -> next query...) against the bundled ``needle.exe``
(--serve on an ephemeral port, terminated afterwards) and returns the flat
transcript the coding model consumes on its next turn.  Everything degrades
gracefully when the engine binary is missing: callers fall back to the
legacy text protocol.

App root = the LoOper/ directory (top-level packages NGUI/AI/player).
"""
import json
import logging
import os
import re
import socket
import subprocess
import sys
import tempfile

from player.code_agent_ops.parsing import (
    _clip,
    _is_gui_like,
)

logger = logging.getLogger(__name__)

__all__ = [
    'TOOL_SCHEMAS',
    'find_needle_exe',
    'execute_call',
    'NeedleRouter',
]

# Needle 2 binary SHA-256 (windows-x86_64/needle.exe, HF repo
# Cactus-Compute/needle2) — recorded so the bundled file can be verified.
NEEDLE2_SHA256 = '93EA7AE8C9EA92B746F41668D87597463BEE0D6F6AE8AB4E855EC8B396556740'

SEARCH_LIMIT = 60        # max matching lines returned per search_code call
READ_CODE_LIMIT = 800    # max lines returned by read_code

# Confidence floor: below it a returned call is treated as a guess — the
# result is still executed but the low-confidence marker rides along so the
# coding model can rephrase when it matters.
NEEDLE_CONF_THRESHOLD = 0.35

# The deterministic tool catalogue.  Schemas stay deliberately minimal:
# optional fields are omitted (the engine flags ungrounded defaults), and a
# single required 'pattern' keeps the grammar tight.
TOOL_SCHEMAS = [
    {
        'name': 'search_code',
        'description': (
            'Search the code node\'s script.py for a pattern and return the '
            'matching lines with line numbers. Use it to find where an '
            'identifier, error name, assignment or import appears in the '
            'code before editing.'),
        'parameters': {
            'type': 'object',
            'properties': {
                'pattern': {
                    'type': 'string',
                    'minLength': 1,
                    'description': "the exact text to find, e.g. 'result', "
                                   "'NameError', 'def calculate'",
                },
            },
            'required': ['pattern'],
        },
    },
    {
        'name': 'read_code',
        'description': (
            'Return the full current script.py with line numbers.'),
        'parameters': {'type': 'object', 'properties': {}, 'required': []},
    },
    {
        'name': 'run_node',
        'description': (
            'Execute the node\'s script.py headlessly and return its stdout, '
            'stderr, traceback and result. Use it to verify behavior or to '
            'reproduce an error after editing.'),
        'parameters': {'type': 'object', 'properties': {}, 'required': []},
    },
]

_KNOWN_TOOLS = {t['name'] for t in TOOL_SCHEMAS}


def find_needle_exe():
    """Locate needle.exe — source tree first, then every frozen layout.

    Returns an existing path or None (callers fall back to the text
    protocol when the engine is not bundled/installed)."""
    rel = os.path.join('needle2', 'needle.exe')
    candidates = []
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        for prefix in ('', 'AI', '_internal',
                       os.path.join('_internal', 'AI'),
                       os.path.join('_internal', 'LoOper', 'AI')):
            candidates.append(os.path.join(exe_dir, prefix, 'AI', 'models', rel))
            candidates.append(os.path.join(exe_dir, prefix, 'models', rel))
        meipass = getattr(sys, '_MEIPASS', None)
        if meipass:
            for sub in ('', 'LoOper'):
                candidates.append(os.path.join(meipass, sub, 'AI', 'models', rel))
    try:
        from AI.config_loader import get_models_dir
        md = get_models_dir()
        candidates.append(os.path.join(md, rel))
        candidates.append(os.path.join(md, 'needle.exe'))   # flat fallback
    except Exception:
        pass
    # Source mode direct sibling: <project>/LoOper/AI/models/needle2/needle.exe
    candidates.append(os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        'AI', 'models', rel))
    seen = set()
    for c in candidates:
        c = os.path.normpath(c)
        if c in seen:
            continue
        seen.add(c)
        if os.path.isfile(c):
            return c
    return None


# ---------------------------------------------------------------------------
# Deterministic tool execution
# ---------------------------------------------------------------------------

def _run_search(code, pattern):
    """Regex search over the script text (literal fallback on bad regex)."""
    code = str(code or '')
    try:
        rx = re.compile(str(pattern))
    except re.error:
        rx = re.compile(re.escape(str(pattern)))
    hits = []
    for i, line in enumerate(code.splitlines(), 1):
        if rx.search(line):
            hits.append((i, line))
    if not hits:
        return '(no matches for pattern: %s)' % _clip(pattern, 120)
    out = []
    for i, line in hits[:SEARCH_LIMIT]:
        out.append('%d: %s' % (i, line.strip()[:200]))
    if len(hits) > SEARCH_LIMIT:
        out.append('... %d more matches' % (len(hits) - SEARCH_LIMIT))
    return '%d match(es) for pattern %s:\n%s' % (
        len(hits), _clip(pattern, 120), '\n'.join(out))


def _read_code_text(code):
    code = str(code or '')
    lines = code.splitlines()
    if len(lines) > READ_CODE_LIMIT:
        lines = lines[:READ_CODE_LIMIT]
        tail = '\n... (file truncated at %d lines)' % READ_CODE_LIMIT
    else:
        tail = ''
    body = '\n'.join('%4d | %s' % (i, ln) for i, ln in enumerate(lines, 1))
    return body + tail


def _format_run_record(record):
    """Deterministic terminal-style rendering of a node run (mirrors the
    Run block the chat shows the user)."""
    lines = []
    for d in (record.get('digest') or [])[:6]:
        lines.append('$ inputs  ' + d)
    out = record.get('stdout') or ''
    err = record.get('stderr') or ''
    error = record.get('error') or ''
    if out:
        lines.append('$ stdout')
        lines.append(_clip(out, 1500))
    if err:
        lines.append('$ stderr')
        lines.append(_clip(err, 3000))
    if error:
        lines.append('$ error')
        lines.append(_clip(error, 3000))
    if record.get('result') is not None:
        lines.append('$ result')
        lines.append(str(_clip(record.get('result'), 800)))
    return '\n'.join(lines) if lines else '(no output)'


def execute_call(name, arguments, ctx):
    """Run ONE deterministic tool call against the live node script.

    ``ctx`` provides the read-only access the single-file scope needs:
      ctx['code']            current script.py source (str)
      ctx['run_node_dict']   callable() -> node dict for the executor
      ctx['player']          callable() -> retained player (or None)
      ctx['chain_root']      callable() -> chain runtime root dir
    Returns the formatted result text.  Never raises — every failure becomes
    a deterministic error string."""
    try:
        args = arguments or {}
        if name == 'search_code':
            return _run_search(ctx['code'], args.get('pattern') or '')
        if name == 'read_code':
            return _read_code_text(ctx['code'])
        if name == 'run_node':
            code = ctx['code']
            if _is_gui_like(code):
                return ('(code opens a window or runs a persistent loop - '
                        'auto-run is skipped. Press Run to launch it.)')
            return _execute_run_node(ctx)
        return '(unknown tool: %s)' % name
    except Exception as e:                      # never raise into the router
        logger.exception('devtool %s failed', name)
        return '(tool %s failed: %s)' % (name, e)


def _execute_run_node(ctx):
    try:
        from player.code_agent_ops.execution import _run_node_via
        node = ctx['run_node_dict']()
        player = ctx['player']() if ctx.get('player') else None
        record = _run_node_via(player, node, {'flag': False},
                               chain_root=ctx.get('chain_root'))
        return _format_run_record(record)
    except Exception as e:
        return '(run_node failed: %s)' % e


# ---------------------------------------------------------------------------
# Needle 2 investigation router
# ---------------------------------------------------------------------------

class NeedleRouter:
    """Run one bounded investigation chain through needle.exe --serve.

    Every investigation spawns a fresh engine process on an ephemeral port
    and terminates it at the end, so sessions never leak between
    investigations (deterministic, no cross-talk).  The chain is:

        query -> complete() -> calls -> execute_call() each
              -> results fed back -> complete() ...  (max ``max_steps``)

    ``route(query)`` returns ``{'ok', 'empty', 'text', 'steps', 'error'}``
    where ``text`` is the flat tool transcript for the coding model and
    ``empty`` marks a respond-with-no-calls outcome (nothing tool-able)."""

    def __init__(self, ctx, exe_path=None, max_steps=3,
                 conf_threshold=NEEDLE_CONF_THRESHOLD):
        self._ctx = ctx
        self._exe = exe_path or find_needle_exe()
        self._max_steps = max_steps
        self._conf_threshold = conf_threshold

    def available(self):
        return bool(self._exe) and os.path.isfile(self._exe)

    def route(self, query):
        if not self.available():
            return {'ok': False, 'empty': True, 'text': '', 'steps': 0,
                    'error': 'needle2 engine not found'}
        if not str(query or '').strip():
            return {'ok': True, 'empty': True, 'text': '', 'steps': 0,
                    'error': None}

        tools_fd, tools_path = tempfile.mkstemp(
            prefix='needle_tools_', suffix='.json')
        try:
            with os.fdopen(tools_fd, 'w', encoding='utf-8') as f:
                json.dump(TOOL_SCHEMAS, f)
            return self._run_session(tools_path, query)
        finally:
            try:
                os.remove(tools_path)
            except Exception:
                pass

    # -- process plumbing ------------------------------------------------

    def _spawn(self, tools_path):
        port = self._free_port()
        flags = 0
        if os.name == 'nt':
            flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        proc = subprocess.Popen(
            [self._exe, '--tools', tools_path, '--serve',
             '--port', str(port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        return proc, port

    @staticmethod
    def _free_port():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('127.0.0.1', 0))
            return s.getsockname()[1]

    def _wait_ready(self, port, proc):
        import requests as _req
        url = 'http://127.0.0.1:%d/reset' % port
        last = None
        for _ in range(40):
            if proc.poll() is not None:
                return False, 'needle engine exited early (rc=%s)' % proc.returncode
            try:
                r = _req.post(url, timeout=0.5)
                if r.status_code == 200:
                    return True, None
                last = r.status_code
            except Exception as e:
                last = e
            import time
            time.sleep(0.25)
        return False, 'needle engine did not become ready: %s' % last

    def _complete(self, port, input_text):
        import requests as _req
        r = _req.post(
            'http://127.0.0.1:%d/complete' % port,
            json={'input': str(input_text)},
            timeout=90,
        )
        if r.status_code != 200:
            return None, 'needle /complete HTTP %d: %s' % (
                r.status_code, _clip(r.text, 400))
        try:
            obj = r.json()
        except Exception as e:
            return None, 'needle /complete bad JSON: %s' % e
        if not isinstance(obj, dict):
            return None, 'needle /complete unexpected payload'
        return obj, None

    # -- the chain --------------------------------------------------------

    def _run_session(self, tools_path, query):
        import time
        proc, port = self._spawn(tools_path)
        transcript = []
        steps = 0
        try:
            ok, err = self._wait_ready(port, proc)
            if not ok:
                return {'ok': False, 'empty': True, 'text': '',
                        'steps': 0, 'error': err}
            current = query
            for step in range(self._max_steps):
                steps = step + 1
                obj, err = self._complete(port, current)
                if err or obj is None:
                    return {'ok': False, 'empty': True, 'text': '',
                            'steps': steps, 'error': err}
                calls = obj.get('function_calls') or []
                conf = float(obj.get('confidence') or 0.0)
                if not calls:
                    # respond / nothing tool-able — end the chain.
                    transcript.append(self._respond_line(obj))
                    break
                executed = []
                for call in calls:
                    name = str(call.get('name') or '?')
                    args = call.get('arguments') or {}
                    if name not in _KNOWN_TOOLS:
                        executed.append('(unknown tool returned: %s)' % name)
                        continue
                    result = execute_call(name, args, self._ctx)
                    low = ''
                    if conf < self._conf_threshold:
                        low = ' [low confidence %.2f]' % conf
                    executed.append('[%s%s] %s' % (name, low, result))
                if not executed:
                    break
                block = '\n\n'.join(executed)
                transcript.append(block)
                current = block           # feed results back to the engine
                time.sleep(0.05)          # let the server breathe between turns
        finally:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        text = '\n\n'.join(t for t in transcript if t)
        empty = not any(t for t in transcript)
        return {'ok': True, 'empty': empty, 'text': text,
                'steps': steps, 'error': None}

    @staticmethod
    def _respond_line(obj):
        """Render a no-call respond outcome (never an error, always text)."""
        payload = {}
        for k in ('response', 'text', 'answer', 'message', 'reasoning',
                  'reason'):
            v = obj.get(k)
            if v:
                payload[k] = v
        if payload:
            return 'Tool engine: %s' % json.dumps(payload, ensure_ascii=False)
        return 'Tool engine: no tool needed for this request'
