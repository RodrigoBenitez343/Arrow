"""Stateless parsing / workspace helpers for the Code Node Studio agent.

Pure functions only (no Qt): reply splitting, SEARCH/REPLACE hunks, GREP over
the workspace, path safety, reasoning-tag collapse, context-window probing.
"""
import ast
import importlib.util
import json
import os
import re
import sys

from NGUI.i18n import _
from player.code_agent_ops.constants import (
    MAIN_FILE,
    PROJECT_MAX_FILE_BYTES,
    PROJECT_MAX_TOTAL_BYTES,
    PROJECT_SKIP_DIRS,
    PROJECT_SKIP_EXTS,
    _GUI_HINTS,
    _MAX_DIGEST_HEAD,
    _MAX_DIGEST_TAIL,
    _SHELL_CMD_RE,
)

__all__ = [
    '_clip', '_digest_line', '_ast_outline', '_venv_packages',
    '_resolve_chain_runtime_dir', '_resolve_venv_site_packages',
    '_resolve_workspace', '_safe_join', '_effective_ctx_tokens',
    '_parse_agent_reply', '_extract_fenced', '_list_workspace_files',
    '_collect_project_files', '_materialize_project_files',
    '_is_gui_like', '_looks_like_shell_command', '_collapse_reasoning',
    '_project_module_names', '_unresolved_imports',
    '_parse_hunks', '_apply_hunks', '_run_grep', '_file_digest',
    '_is_question', '_looks_like_tool_request',
]


def _clip(text, limit):
    text = str(text or '')
    if len(text) <= limit:
        return text
    return text[:limit] + f'... [{len(text)} chars total]'


def _digest_line(name, value, src=''):
    """One-line digest of a resolved input value - never the raw payload."""
    kind = type(value).__name__
    length = ''
    try:
        length = f', len={len(value)}' if hasattr(value, '__len__') else ''
    except Exception:
        pass
    head = _clip(value, _MAX_DIGEST_HEAD).replace('\n', '\\n')
    tail = ''
    try:
        raw = str(value)
        if len(raw) > _MAX_DIGEST_HEAD:
            tail = ' tail=' + _clip(raw, _MAX_DIGEST_TAIL).replace('\n', '\\n')
    except Exception:
        tail = ''
    src_txt = f'  <- {src}' if src else ''
    return f'{name} ({kind}{length}): {head}{tail}{src_txt}'


def _ast_outline(code):
    """Imports / classes / function signatures of the node code."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return ['(code has a syntax error - run to see the traceback)']
    imports = []
    funcs = []
    classes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(f'import {alias.asname or alias.name}')
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ''
            names = ', '.join(a.asname or a.name for a in node.names)
            imports.append(f'from {mod} import {names}')
        elif isinstance(node, ast.FunctionDef) and node.col_offset == 0:
            args = [a.arg for a in node.args.args]
            funcs.append(f'def {node.name}({", ".join(args)})')
        elif isinstance(node, ast.ClassDef):
            classes.append(node.name)
    out = []
    if imports:
        out.append('imports: ' + '; '.join(imports))
    if funcs:
        out.append('functions: ' + '; '.join(funcs))
    if classes:
        out.append('classes: ' + ', '.join(classes))
    return out or ['(no top-level functions/imports)']


def _venv_packages(site_packages_dir):
    """Top-level package names installed in a code-node venv (cached by mtime)."""
    cache = getattr(_venv_packages, '_cache', None)
    if cache is None:
        cache = {}
        _venv_packages._cache = cache
    try:
        mtime = os.path.getmtime(site_packages_dir)
    except Exception:
        return []
    key = (site_packages_dir, mtime)
    if key in cache:
        return cache[key]
    names = []
    try:
        for entry in sorted(os.listdir(site_packages_dir)):
            if (entry.endswith('.dist-info') or entry.endswith('.egg-info')
                    or entry.endswith('.py') or entry.endswith('.pth')
                    or entry.endswith('.so') or '.dist-info' in entry):
                continue
            names.append(entry)
    except Exception:
        pass
    cache[key] = names[:200]
    return cache[key]


def _resolve_chain_runtime_dir(player):
    """Best-known chain runtime dir (retained player first, else newest chain)."""
    if player is not None:
        try:
            d = player.workflow_executor._chain_runtime_dir
            if d and os.path.isdir(d):
                return d
        except Exception:
            pass
    base = os.path.join(os.getcwd(), 'runtime', 'chains')
    try:
        if os.path.isdir(base):
            cands = []
            for name in os.listdir(base):
                p = os.path.join(base, name)
                if os.path.isdir(p):
                    cands.append((os.path.getmtime(p), p))
            if cands:
                return sorted(cands)[-1][1]
    except Exception:
        pass
    return base


def _resolve_venv_site_packages(player):
    """Best-known chain venv site-packages (retained player first, else newest)."""
    if player is not None:
        try:
            sp = player.workflow_executor._venv_site_packages
            if sp and os.path.isdir(sp):
                return sp
        except Exception:
            pass
    base = os.path.join(os.getcwd(), 'runtime', 'venvs')
    if os.path.isdir(base):
        cands = []
        for name in os.listdir(base):
            sp = os.path.join(base, name, 'Lib', 'site-packages')
            if os.path.isdir(sp):
                cands.append((os.path.getmtime(sp), sp))
        if cands:
            return sorted(cands)[-1][1]
    return ''


def _resolve_workspace(player, node_id):
    """Per-node workspace dir - created on demand, mirrors the runner layout."""
    node_id = str(node_id or 'code')
    ws = os.path.join(_resolve_chain_runtime_dir(player), 'code_nodes', node_id)
    try:
        os.makedirs(ws, exist_ok=True)
    except Exception:
        pass
    return ws


def _safe_join(root, name):
    """Join a relative workspace path to root, refusing escapes/absolutes.

    Raises ValueError for '..', absolute paths, drive letters, empty names.
    Nested relative paths (e.g. 'data/tags.json') are allowed.
    """
    name = (name or '').strip().replace('\\', '/')
    if not name or name.startswith('/') or re.match(r'^[A-Za-z]:', name):
        raise ValueError(_('Invalid file name'))
    parts = [p for p in name.split('/') if p not in ('', '.')]
    if not parts or any(p in ('..', '.') or '/' in p or '\\' in p
                        for p in parts):
        raise ValueError(_('Invalid file name'))
    joined = os.path.normpath(os.path.join(root, *parts))
    root_abs = os.path.abspath(root)
    if os.path.commonpath([root_abs, os.path.abspath(joined)]) != root_abs:
        raise ValueError(_('Invalid file name'))
    return joined


def _effective_ctx_tokens(model, use_llamacpp):
    """Native context window (tokens) of the ACTIVE chat engine - RAM-aware.

    llama.cpp: the engine's ``context_size`` once the server booted
    (``fit_context_to_ram`` already applied there); before boot, the model's
    native GGUF context length.  Ollama: the configured ``context_length``.
    Returns 0 when unknown so callers fall back to a safe default.
    """
    try:
        if use_llamacpp:
            try:
                from AI import api as _api
                eng = getattr(_api, 'LLAMACPP_ENGINE', None)
                ctx = int(getattr(eng, 'context_size', 0) or 0)
                if ctx > 0:
                    return ctx
            except Exception:
                pass
            path = str(model or '')
            if path.lower().endswith('.gguf') and os.path.isfile(path):
                try:
                    from AI.gguf_model_info import read_model_context_size
                    return int(read_model_context_size(path) or 0)
                except Exception:
                    return 0
            return 0
        try:
            from AI.settings import settings
            return max(int(getattr(settings.ollama, 'context_length', 0)
                           or 0), 0)
        except Exception:
            return 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Agent reply parsing
# ---------------------------------------------------------------------------

def _parse_agent_reply(text):
    """Split an agent reply into (main_code_or_None, {rel_name: code}).

    - A '### FILE: <name>' header followed by a fenced block targets the named
      workspace file (may repeat for many files).
    - Fenced blocks outside any FILE section target the main file (script.py).
    - A reply without any code block returns (None, {}) - no edit to apply.
    """
    files = {}
    main_code = None
    try:
        parts = re.split(r'(?im)^\s*###\s*FILE\s*:\s*([^\r\n#]+)[ \t]*\r?\n',
                         text)
    except Exception:
        parts = [text]
    if len(parts) < 3:          # no FILE headers at all
        code = _extract_fenced(text)
        return (code or None, {})
    preamble = parts[0]
    code = _extract_fenced(preamble)
    if code:
        main_code = code
    for i in range(1, len(parts), 2):
        name = parts[i].strip()
        body = _extract_fenced(parts[i + 1]) or parts[i + 1].strip()
        if name and body:
            files[name] = body
    return (main_code, files)


_TOOL_REQUEST_WORDS = (
    'search', 'find', 'grep', 'show', 'read', 'open', 'look', 'list',
    'run ', 'execute', 'inspect', 'check', 'print', 'cat ', 'where is',
    'what is in', 'display', 'view ', 'see the',
)


def _looks_like_tool_request(text):
    """True when a no-code reply is a short command to inspect/run the code.

    A small model answers a build request with prose ("Here's how I'd
    structure it...").  Routing that to the dev tools returns unrelated
    results, the model rambles again, and the whole tool budget burns until
    the loop reports "the agent did not produce an implementation".  Only a
    terse one-line instruction counts as a tool request; everything else is
    a stall that must be answered with "give me the code".
    """
    t = str(text or '').strip()
    if not t or '```' in t:
        return False
    if len(t) > 400 or '\n' in t:
        return False
    low = t.lower()
    return any(w in low for w in _TOOL_REQUEST_WORDS)


def _extract_fenced(text):
    """Content of the first fenced block (any language tag), else ''."""
    m = re.search(r'```[A-Za-z0-9_+-]*\s*\n(.*?)```', str(text or ''),
                  re.DOTALL)
    return m.group(1).strip() if m else ''


def _list_workspace_files(root):
    """Relative paths (posix style) of every file under root, sorted."""
    out = []
    for base, _dirs, names in os.walk(root):
        for n in names:
            full = os.path.join(base, n)
            rel = os.path.relpath(full, root).replace('\\', '/')
            out.append(rel)
    return sorted(out)


def _collect_project_files(root, skip=(MAIN_FILE,)):
    """Text files under ``root`` as ``{rel: content}`` for the node/chain.

    script.py is stored separately as the node 'code', so it is skipped by
    default.  Caches, VCS dirs, binaries and oversized files are dropped: a
    chain should carry source, not build output."""
    out = {}
    total = 0
    skip = {str(s).replace('\\', '/') for s in skip}
    try:
        for base, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if d not in PROJECT_SKIP_DIRS]
            for n in names:
                full = os.path.join(base, n)
                rel = os.path.relpath(full, root).replace('\\', '/')
                if rel in skip:
                    continue
                if os.path.splitext(n)[1].lower() in PROJECT_SKIP_EXTS:
                    continue
                try:
                    size = os.path.getsize(full)
                except Exception:
                    continue
                if size <= 0 or size > PROJECT_MAX_FILE_BYTES:
                    continue
                if total + size > PROJECT_MAX_TOTAL_BYTES:
                    continue
                try:
                    with open(full, 'r', encoding='utf-8') as f:
                        text = f.read()
                except (UnicodeDecodeError, OSError):
                    continue          # binary or unreadable
                out[rel] = text
                total += size
    except Exception:
        pass
    return out


def _materialize_project_files(root, files, overwrite=False):
    """Write a node's ``project_files`` dict into its workspace.

    *files* may also be the node property's raw JSON string (that is what a
    chain carries), so the node's helper modules really land beside script.py.
    Paths are sanitised through :func:`_safe_join` (no escapes), script.py is
    never touched (the node's 'code' owns it), and existing files are kept
    unless ``overwrite`` - a live workspace can hold newer unsaved edits
    than the node property.  Returns the list of written relative paths."""
    if isinstance(files, str):
        try:
            files = json.loads(files or '{}')
        except Exception:
            return []
    if not isinstance(files, dict):
        return []
    written = []
    for rel, content in files.items():
        rel = str(rel or '').replace('\\', '/')
        if not rel or rel == MAIN_FILE or not isinstance(content, str):
            continue
        try:
            path = _safe_join(root, rel)
        except Exception:
            continue
        if os.path.exists(path) and not overwrite:
            continue
        try:
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
            written.append(rel)
        except Exception:
            continue
    return written


def _is_gui_like(code):
    """True when the code opens a window or runs a persistent loop."""
    low = (code or '').lower()
    return any(h in low for h in _GUI_HINTS)


def _project_module_names(project_files):
    """Top-level import names the node's own project provides."""
    names = set()
    files = project_files
    if isinstance(files, str):
        try:
            files = json.loads(files or '{}')
        except Exception:
            files = {}
    for rel in (files or {}):
        parts = str(rel or '').replace('\\', '/').split('/')
        if not parts or not parts[0]:
            continue
        if len(parts) == 1:
            if parts[0].endswith('.py'):
                names.add(parts[0][:-3])
        else:
            names.add(parts[0])          # a package folder
    return names


def _unresolved_imports(code, project_files=None, extra_names=()):
    """Top-level imports that exist nowhere: not stdlib, not a project file,
    not importable here.

    A small model invents module names that LOOK like packages (``parsers``,
    ``task_manager``).  Nothing catches that until the chain runs, tries
    ``pip install parsers``, gets "no matching distribution" and dies on
    ModuleNotFoundError - long after the model saw the code.  This is the
    static, offline pre-run check: pure name resolution, nothing executed.
    """
    try:
        tree = ast.parse(code or '')
    except SyntaxError:
        return []
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split('.')[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and not getattr(node, 'level', 0):
            if node.module:
                names.add(node.module.split('.')[0])
    known = set(getattr(sys, 'stdlib_module_names', ()))
    known |= set(sys.builtin_module_names)
    known |= _project_module_names(project_files) | set(extra_names or ())
    missing = []
    for name in sorted(names):
        if not name or name in known or name in sys.modules:
            continue
        try:
            if importlib.util.find_spec(name) is not None:
                continue
        except Exception:
            pass
        missing.append(name)
    return missing


def _looks_like_shell_command(code):
    """Single-line shell launcher text that was never meant as Python."""
    txt = (code or '').strip()
    if not txt or '\n' in txt:
        return False
    return bool(_SHELL_CMD_RE.match(txt))


# ---------------------------------------------------------------------------
# SEARCH / REPLACE hunks (aider-style minimal edits)
# ---------------------------------------------------------------------------

def _parse_hunks(text):
    """Aider-style SEARCH/REPLACE hunks from a reply's main block.

    Returns a list of (search, replace) pairs, None when the text has no
    SEARCH markers.  Raises ValueError on an unbalanced block."""
    t = str(text or '')
    if '<<<<<<< SEARCH' not in t:
        return None
    pat = re.compile(
        r'<<<<<<< SEARCH\s*\n(.*?)^=======\s*\n(.*?)^>>>>>>> REPLACE\s*$',
        re.S | re.M)
    blocks = pat.findall(t)
    if not blocks:
        raise ValueError(_('SEARCH/REPLACE block must contain ======= and '
                           '>>>>>>> REPLACE lines'))
    hunks = []
    for search, replace in blocks:
        search = search.rstrip('\n')
        replace = replace.rstrip('\n')
        if not search.strip():
            raise ValueError(_('a SEARCH side cannot be empty'))
        hunks.append((search, replace))
    return hunks


def _apply_hunks(current, hunks):
    """Apply SEARCH/REPLACE pairs to ``current``. Returns (new, ok, error)."""
    new = str(current or '')
    for search, replace in hunks:
        count = new.count(search)
        if count != 1:
            head = [l.strip()[:80] for l in search.splitlines() if l.strip()]
            return (None, False,
                    _('SEARCH found %d times (must be exactly 1): %s') % (
                        count, _clip(' / '.join(head[:2]), 160)))
        new = new.replace(search, replace, 1)
    return new, True, ''


# ---------------------------------------------------------------------------
# GREP over the node workspace (aider-style file:line:text)
# ---------------------------------------------------------------------------

def _run_grep(pattern, filespec, workspace_root, main_code, rel_files):
    """Regex search over workspace text files.

    MAIN_FILE searches the live main code (``main_code``), not the last
    written script.py."""
    try:
        rx = re.compile(pattern)
    except re.error:
        rx = re.compile(re.escape(pattern))
    want = (filespec or '*').strip().lower()
    seen = set()
    cand = []
    if want == '*':
        cand = [MAIN_FILE] + [r for r in rel_files if r != MAIN_FILE]
    else:
        for r in list(rel_files) + [MAIN_FILE]:
            if r.lower() == want:
                cand.append(r)
    cand = [r for r in cand if not (r in seen or seen.add(r))]
    if not cand:
        return _('(no file named %s in the workspace)') % filespec
    out = []
    for rel in cand:
        if rel == MAIN_FILE:
            body = main_code or ''
        else:
            try:
                with open(os.path.join(workspace_root, rel), 'r',
                          encoding='utf-8', errors='replace') as f:
                    body = f.read()
            except Exception:
                continue
        for ln, line in enumerate(body.splitlines(), 1):
            if rx.search(line):
                out.append('%s:%d: %s' % (rel, ln, line.strip()[:200]))
                if len(out) >= 60:
                    break
        if len(out) >= 60:
            break
    if not out:
        return _('(no matches for pattern: %s)') % _clip(pattern, 120)
    return '\n'.join(out)


def _file_digest(body, limit=160):
    """First meaningful line(s) of a file - enough to recognize what it is."""
    lines = [l.strip() for l in str(body or '').splitlines() if l.strip()]
    head = ' / '.join(lines[:2]) if lines else '(empty)'
    return _clip(head, limit)


# ---------------------------------------------------------------------------
# Reasoning-tag collapse + question heuristic
# ---------------------------------------------------------------------------

_REASONING_PAIRS = (
    ('<think>', '</think>'), ('<reasoning>', '</reasoning>'),
    ('[think]', '[/think]'), ('[thinking]', '[/thinking]'),
    ('[reasoning]', '[/reasoning]'),
)


def _collapse_reasoning(text):
    """Strip model reasoning blocks (think/analysis tags) from a reply.

    Returns ``(clean_text, note)`` where note is a short dim label (or None)
    telling the user the reasoning was collapsed.  Handles unclosed openers
    (cut tail)."""
    t = str(text or '')
    collapsed = 0
    for _open, _close in _REASONING_PAIRS:
        pat = re.compile(re.escape(_open) + r'.*?' + re.escape(_close),
                         re.DOTALL | re.IGNORECASE)
        while True:
            m = pat.search(t)
            if not m:
                break
            collapsed += len(m.group(0))
            t = t[:m.start()] + t[m.end():]
    for _open, _close in _REASONING_PAIRS:
        idx = t.lower().find(_open.lower())
        if idx != -1:
            collapsed += len(t) - idx
            t = t[:idx]
            break
    t = t.strip()
    if not collapsed:
        return t, None
    return t, '\u2192 reasoning hidden ({} tok)'.format(max(1, collapsed // 4))


def _is_question(goal):
    """True only when a request is genuinely interrogative.

    A build request phrased politely ("can you make me a to-do app?") ends
    with '?' and used to be answered with one prose turn - the node was never
    built and the user's request was dropped.  An action word means "do it",
    regardless of the question mark or the polite opener.
    """
    g = str(goal or '').strip().lower()
    if not g:
        return False
    words = re.findall(r"[a-z']+", g)
    if not words:
        return False
    first = words[0]
    has_action = any(w in _ACTION_WORDS for w in words)
    polite = (first == 'please'
              or (first in ('can', 'could', 'would', 'will')
                  and len(words) > 1 and words[1] == 'you'))
    if polite and has_action:
        return False                    # "can you build me ..."
    if has_action and re.search(r'\b(can|could|would|will) you\b', g):
        return False                    # "why is it empty? can you fix it?"
    if first in _QUESTION_HEADS:
        return True                     # "how do i fix it", "does it build?"
    if has_action:
        return False                    # "something that watches a folder?"
    return g.endswith('?')


_ACTION_WORDS = frozenset((
    'write', 'create', 'make', 'build', 'add', 'fix', 'implement',
    'generate', 'refactor', 'change', 'update', 'remove', 'delete',
    'install', 'design', 'convert', 'rename', 'move', 'run', 'debug',
    'optimize', 'rewrite', 'extend', 'let', 'watch', 'track', 'show',
    'display', 'handle', 'parse', 'read', 'save', 'load', 'compute',
    'calculate', 'monitor', 'send', 'fetch', 'download', 'upload', 'play',
    'draw', 'plot', 'sort', 'filter', 'search', 'split', 'merge',
))

_QUESTION_HEADS = frozenset((
    'what', 'why', 'how', 'when', 'which', 'who', 'where', 'explain',
    'tell', 'is', 'are', 'does', 'do', 'did', 'any', 'can', 'could',
    'would', 'will', 'should',
))
