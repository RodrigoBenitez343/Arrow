"""artifacts.py — the CONTENT a node points at, read for the builder.

A client-side recording is *the* behaviour of the chain that uses it: one
``web_sequence`` node and nothing else IS "open linkedin.com, scroll the feed,
summarise the posts".  The topology of such a chain is one node with no wires,
so a builder that reads only nodes and edges can say nothing honest about it —
it calls the chain "a stub with no effect" while the recording sitting next to
it says exactly what happens.

This module resolves the file a node replays (or runs) and summarises it::

    web_sequence   <session_file>  -> the recording's own name, its action
                                     count and kinds, and the URLs it opens
    sequence       <sequence_file> -> action count and kinds of the desktop
                                     recording
    chain_import   <chain_file_path> -> the imported chain's derived brief
                                     (``player/chain_brief.py``) and node count

Every read is best-effort in ONE direction only: a file that cannot be found or
parsed is REPORTED (``problem``), never silently skipped — "the node replays
nothing" and "the builder could not read the recording" are different facts and
the user fixes them differently.

No Qt, no model, no canvas: this module answers from the files on disk.
"""

import ast
import json
import logging
import os
import re
import sys

logger = logging.getLogger(__name__)

#: A recording larger than this is not parsed: the digest is a text aid, and a
#: huge JSON block would cost more than it explains.
MAX_RECORDING_BYTES = 24 * 1024 * 1024
#: How many action phrases are spelled out (runs are collapsed, then cut).
MAX_PHRASES = 8

_WEB_MARKER = 'web'


def search_roots():
    """Folders a bare recording name is resolved against, in order.

    Mirrors the runtime's own search (``sequence_executor``): a recording is
    stored by name in ``web_sequences/`` (web) or ``sequences/`` (desktop)
    beside the app, so the builder must look in the same places the executor
    does or it would report a file the run finds perfectly well.
    """
    roots = []
    here = os.path.abspath(os.path.dirname(__file__))
    # .../LoOper/player/builder -> .../LoOper -> the project root
    for parent in (os.path.dirname(os.path.dirname(here)),
                   os.path.dirname(os.path.dirname(os.path.dirname(here)))):
        if parent not in roots:
            roots.append(parent)
    for path in (os.getcwd(),
                 os.path.dirname(sys.executable) if getattr(sys, 'frozen', False)
                 else ''):
        if path and path not in roots:
            roots.append(path)
    return roots


def _candidates(name, folder, roots=None):
    """Every path a recording *name* may live at (absolute names first)."""
    name = str(name or '').strip()
    if not name:
        return []
    out = [name]
    extended = name if name.lower().endswith('.json') else name + '.json'
    if os.path.basename(name.replace('\\', '/')) == name:
        for root in (roots or search_roots()):
            out.append(os.path.join(root, folder, extended))
            out.append(os.path.join(root, extended))
    return out


def _find(name, folder, roots=None):
    for path in _candidates(name, folder, roots):
        try:
            if os.path.isfile(path):
                return path
        except OSError:
            continue
    return ''


def _load(path):
    """The decoded recording (raises ``ValueError``/``OSError``)."""
    size = os.path.getsize(path)
    if size > MAX_RECORDING_BYTES:
        raise ValueError('%.1f MB is too large to summarise'
                         % (size / (1024.0 * 1024.0)))
    with open(path, 'r', encoding='utf-8') as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError('it holds %s, not a recording object'
                         % type(data).__name__)
    return data


def _recording_name(data, fallback):
    """The recording's own intent line, or '' when its name is only a filename.

    The recorder stores the FILE NAME as the name (``next.json_copy`` after a
    copy), so returning it put a stale identity at the head of every sentence:
    readers and the describe loop saw ``replays the web recording
    "next.json_copy"`` and talked about the file instead of the click.  A name
    is a name only when it says something the filename does not; the filename
    itself is traceability, not behaviour.
    """
    metadata = data.get('metadata') if isinstance(data.get('metadata'), dict) else {}
    text = ' '.join(str(metadata.get('name') or '').split())
    stems = {os.path.splitext(os.path.basename(str(fallback)))[0],
             os.path.splitext(os.path.basename(str(fallback)))[0] + '.json'}
    if text:
        flat = text.replace('_copy', '').replace(' copy', '').rstrip('. _')
        if flat.lower() in {s.lower() for s in stems if s}:
            return ''
    else:
        text = os.path.splitext(os.path.basename(fallback))[0].replace('_', ' ')
        if not text.strip():
            return ''
    return text


def _maybe_dict(value):
    """A dict that a recorder stored as a string (Python repr or JSON)."""
    if isinstance(value, dict):
        return value
    text = str(value or '').strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        try:
            data = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return {}
    return data if isinstance(data, dict) else {}


def _element_name(locator):
    """What the action acted on, by the words a person reads on the screen.

    A locator carries a whole DOM dump (css, xpath, classes, viewport); the
    element's own text is the only part that says what the click MEANT, and it
    is the difference between "click x1" and `clicks "Easy Apply"`.
    """
    for key in ('text', 'link_text', 'aria_label', 'label_text', 'label',
                'name', 'placeholder', 'id'):
        value = str(locator.get(key) or '').strip()
        if value:
            return _squash(value, 60)
    return ''


def _host(url):
    """``linkedin.com`` — a recording's page without the query string.

    Only an http(s) page has a host worth naming: ``about:new-tab-page`` or a
    bare token is where the browser STARTS, not a site the recording uses.
    """
    text = str(url or '').strip()
    if not re.search(r'^https?://', text, re.I):
        return ''
    text = re.sub(r'^[a-z]+://', '', text, flags=re.I)
    return text.split('/', 1)[0].split('?', 1)[0]


def _phrase(action):
    """One action as a phrase a reader understands (never a kind count)."""
    kind = str(action.get('type') or '').strip().lower()
    locator = _maybe_dict(action.get('locator'))
    target = _element_name(locator)
    if kind in ('click', 'double_click', 'right_click', 'dblclick'):
        if target:
            return 'clicks "%s"' % target
        desktop = _element_name(_maybe_dict(action.get('element')))
        if desktop:
            return 'clicks "%s"' % desktop
        point = _maybe_dict(action.get('coordinates'))
        if point.get('x') is not None:
            return 'clicks at (%s, %s)' % (point.get('x'), point.get('y'))
        return 'clicks the screen'
    if kind in ('type', 'fill', 'input', 'text', 'send_keys', 'type_string'):
        value = str(action.get('value') or locator.get('value')
                    or action.get('text') or '').strip()
        where = (' into "%s"' % target) if target else ''
        return ('types "%s"%s' % (_squash(value, 40), where)) if value \
            else ('types%s' % where if where else 'types')
    if kind in ('scroll', 'wheel'):
        # The recording's own geometry says WHICH way and how far: a plain
        # "scrolls the page" threw that away, and two opposite scrolls read
        # like one down-scroll.
        scroll = _maybe_dict(action.get('scroll'))
        delta = scroll.get('total_delta')
        try:
            delta = float(delta)
        except (TypeError, ValueError):
            delta = 0
        if delta:
            return ('scrolls %s the page (%d px)'
                    % ('down' if delta > 0 else 'up', abs(delta)))
        return 'scrolls the page'
    if kind in ('navigate', 'goto', 'open', 'url'):
        return 'opens ' + (_host(action.get('url') or locator.get('href'))
                           or 'a page')
    if kind in ('key', 'press', 'hotkey', 'keypress', 'keystroke'):
        return 'presses %s' % _squash(action.get('key')
                                      or action.get('value') or 'a key', 20)
    if kind in ('drag_drop', 'drag'):
        start = _element_name(_maybe_dict(action.get('from_element')))
        end = _element_name(_maybe_dict(action.get('to_element')))
        if start and end:
            return 'drags "%s" onto "%s"' % (start, end)
        return 'drags across the screen'
    if kind in ('wait', 'sleep', 'delay'):
        return 'waits'
    if kind == 'hover':
        return ('hovers "%s"' % target) if target else 'hovers'
    if kind == 'select':
        return ('selects "%s"' % target) if target else 'picks an option'
    if kind == 'upload':
        return 'uploads a file'
    return _squash(kind, 30) or 'acts'


def _phrases_text(actions):
    """``scrolls the page ×2, clicks "Submit application"`` for one recording."""
    phrases = [_phrase(action) for action in actions or []
               if isinstance(action, dict)]
    runs = []
    for phrase in phrases:
        if runs and runs[-1][0] == phrase:
            runs[-1][1] += 1
        else:
            runs.append([phrase, 1])
    shown = [phrase if count == 1 else '%s ×%d' % (phrase, count)
             for phrase, count in runs[:MAX_PHRASES]]
    text = ', '.join(shown)
    if len(runs) > MAX_PHRASES:
        text += ', … %d more action(s)' % (len(runs) - MAX_PHRASES)
    return text


def _windows(actions):
    """The desktop windows the recording worked in (a recording is per app)."""
    titles = []
    for action in actions or ():
        if not isinstance(action, dict):
            continue
        title = str(_maybe_dict(action.get('app_context')).get('title')
                    or '').strip()
        if title and title not in titles:
            titles.append(_squash(title, 40))
            if len(titles) >= 3:
                break
    return titles


def _summary_for_recording(path, data, what):
    """One line for a recording, ACTIONS FIRST.

    The head used to be ``replays the web recording "<name>" on <host>``: the
    type's boilerplate, the file's identity and the recording's START state —
    three facts, none of them the behaviour.  The action phrases ARE what the
    node does, so they lead; the host follows when no action already names it;
    the recorder's intent line (a real one) closes the sentence.
    """
    actions = data.get('actions') or []
    metadata = data.get('metadata') if isinstance(data.get('metadata'), dict) else {}
    phrases = _phrases_text(actions)
    name = _recording_name(data, path)
    head = phrases or '%s "%s"' % (what, (name or os.path.basename(path))[:200])
    parts = [head]
    host = _host(metadata.get('start_url')
                 or next((str(a.get('url') or '') for a in actions
                          if isinstance(a, dict) and a.get('url')), ''))
    # A navigate action already says "opens <host>"; the start_url is the same
    # page, and naming it twice squeezed the verbs out of the model's budget.
    action_hosts = set()
    for action in actions:
        if isinstance(action, dict):
            action_hosts.add(_host(action.get('url')
                                   or _maybe_dict(action.get('locator')).get('href')))
    if host and host not in action_hosts:
        parts.append('on ' + host)
    windows = _windows(actions)
    if windows and 'desktop' in what:
        parts.append('in %s' % ', '.join('"%s"' % title for title in windows))
    if name and name != head:
        parts.append('the recording calls itself "%s"' % name[:200])
    return ' — '.join(parts)


def _chain_import(path):
    """The imported chain in one line: its own brief, or why it has none."""
    data = _load(path)
    from player import chain_brief
    nodes = sum(len(value) for key, value in data.items()
                if key.endswith('_nodes') and isinstance(value, list))
    brief = chain_brief.brief(data)
    return '%s — %d node(s)%s' % (os.path.basename(path), nodes,
                                  ('; ' + brief) if brief else '')


def describe(node_type, properties, roots=None):
    """``{'text': str, 'problem': str}`` for one node (both may be '').

    ``text`` is what the node actually replays or runs; ``problem`` is a fact
    the wiring cannot show — the file is missing, unreadable, or empty — so a
    lone node is never described as "nothing happens" when the truth is "the
    recording could not be read".
    """
    props = properties or {}
    ntype = str(node_type or '').strip().lower()
    if ntype == 'web_sequence':
        name = str(props.get('session_file') or props.get('sequence_file')
                   or '').strip()
        if not name:
            return {'text': '', 'problem': 'replays nothing: no session_file '
                                           'is set (attach a web recording)'}
        path = _find(name, 'web_sequences', roots)
        if not path:
            return {'text': '', 'problem': 'replays nothing: the web recording '
                                           '%r is not on disk' % name}
        try:
            data = _load(path)
        except (OSError, ValueError) as exc:
            return {'text': '', 'problem': 'replays %r, which cannot be read: %s'
                                          % (os.path.basename(path), exc)}
        mode = str(data.get('mode') or '').lower()
        problem = ''
        if mode and mode != _WEB_MARKER:
            problem = ('replays %r, which is a %s recording — a web_sequence '
                       'node cannot play it' % (os.path.basename(path), mode))
        text = _summary_for_recording(path, data, 'replays the web recording')
        if not problem and not (data.get('actions') or []):
            problem = ('replays %r, which holds no actions'
                       % os.path.basename(path))
        return {'text': text, 'problem': problem}

    if ntype == 'sequence':
        name = str(props.get('sequence_file') or '').strip()
        if not name:
            return {'text': '', 'problem': 'replays nothing: no sequence_file '
                                           'is set (attach a desktop recording)'}
        path = _find(name, 'sequences', roots)
        if not path:
            return {'text': '', 'problem': 'replays nothing: the desktop '
                                           'recording %r is not on disk' % name}
        try:
            data = _load(path)
        except (OSError, ValueError) as exc:
            return {'text': '', 'problem': 'replays %r, which cannot be read: %s'
                                          % (os.path.basename(path), exc)}
        text = _summary_for_recording(path, data, 'replays the desktop recording')
        problem = ('' if (data.get('actions') or []) else
                   'replays %r, which holds no actions'
                   % os.path.basename(path))
        return {'text': text, 'problem': problem}

    if ntype == 'chain_import':
        # A LIVE node calls it ``chain_file``; the chain FILE serialises the
        # same value as ``chain_file_path``.  Reading one spelling only made
        # every import look empty on the canvas side.
        name = str(props.get('chain_file_path')
                   or props.get('chain_file') or '').strip()
        if not name:
            return {'text': '', 'problem': 'imports nothing: no '
                                           'chain_file_path is set'}
        path = name if os.path.isabs(name) else _find(name, 'chains', roots)
        if not path or not os.path.isfile(path):
            return {'text': '', 'problem': 'imports %r, which is not on disk '
                                           '(the chain moved)' % name}
        try:
            text = _chain_import(path)
        except (OSError, ValueError, KeyError) as exc:
            return {'text': '', 'problem': 'imports %r, which cannot be read: %s'
                                          % (os.path.basename(path), exc)}
        return {'text': text, 'problem': ''}

    return {'text': '', 'problem': ''}


def one_line(node_type, properties, roots=None):
    """The node's behaviour as one sentence ('' when it has no artifact)."""
    described = describe(node_type, properties, roots)
    return described['text'] or described['problem']


def _squash(text, limit=200):
    """Collapse whitespace and clip (recordings carry long intent lines)."""
    text = re.sub(r'\s+', ' ', str(text or '')).strip()
    return text if len(text) <= limit else text[:max(0, limit - 1)] + '…'


def lines_for(node_type, properties, roots=None):
    """Digest lines for one node's artifact ([] when it points at nothing)."""
    described = describe(node_type, properties, roots)
    out = []
    if described['text']:
        out.append('artifact: ' + _squash(described['text'], 320))
    if described['problem']:
        out.append('artifact: ' + _squash(described['problem'], 320))
    return out
