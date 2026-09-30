"""Stateless parsing / workspace helpers for the Code Node Studio agent.

Pure functions only (no Qt): reply splitting, SEARCH/REPLACE hunks, GREP over
the workspace, path safety, reasoning-tag collapse, context-window probing.
"""
import ast
import os
import re

from NGUI.i18n import _
from player.code_agent_ops.constants import (
    MAIN_FILE,
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
    '_is_gui_like', '_looks_like_shell_command', '_collapse_reasoning',
    '_parse_hunks', '_apply_hunks', '_run_grep', '_file_digest',
    '_is_question',
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


def _is_gui_like(code):
    """True when the code opens a window or runs a persistent loop."""
    low = (code or '').lower()
    return any(h in low for h in _GUI_HINTS)


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
    """True when a request is genuinely interrogative (a text answer is OK)."""
    g = str(goal or '').strip().lower()
    if not g:
        return False
    if '?' in g:
        return True
    heads = ('what', 'why', 'how', 'when', 'which', 'who', 'where',
             'explain', 'is there', 'does ', 'can you', 'do you',
             'tell me', 'should ', 'is it ', 'are you')
    return any(g.startswith(h) or (' ' + h.rstrip()) in g for h in heads)
