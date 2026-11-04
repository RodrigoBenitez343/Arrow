"""chain_brief.py — the ONE routing line a chain carries.

``description`` used to be typed by hand in the library, which meant the
router read prose written weeks earlier that had drifted from the topology.
The Builder now writes ``flow`` metadata into the chain (summary / section
boxes / authored steps), so the description can be DERIVED from it instead:

    brief(chain) -> "Searches job boards and keeps the matching postings;
                     search → collect → shortlist; needs search terms;
                     returns shortlist, count"

Deterministic, no model, no IO, no Qt: the same chain always yields the same
line, and every reader (catalog, agent routing, library tooltip) shows that
one line.  A chain with no flow metadata still gets an honest structural
brief — node counts plus what it needs and returns — so nothing falls back to
hand-written prose.

Callers hold a decoded chain dict; this module never opens files.
"""

import re

#: The line stays short: it is scored against the user's request.
BRIEF_LIMIT = 320
#: Per-part caps so one verbose summary cannot swallow the whole line.
MAX_SUMMARY = 150
MAX_SECTION_TITLES = 6
MAX_TRAIL = 4
MAX_TRAIL_CHARS = 48
MAX_LABELS = 4
MAX_LABEL_CHARS = 28

#: Node lists of a chain file, in the order the brief counts them.
_NODE_KEYS = (
    ('input_nodes', 'input'),
    ('sequences', 'sequence'),
    ('web_sequences', 'web'),
    ('llm_nodes', 'llm'),
    ('conditional_nodes', 'conditional'),
    ('chain_import_nodes', 'chain import'),
    ('form_filler_nodes', 'form filler'),
    ('code_nodes', 'code'),
    ('container_nodes', 'container'),
    ('context_nodes', 'context'),
    ('handle_nodes', 'handle'),
    ('mcp_nodes', 'mcp'),
    ('output_nodes', 'output'),
)

_SENTENCE = re.compile(r'(?<=[.!?])\s+')
_WS = re.compile(r'\s+')


def _clip(text, limit):
    text = _WS.sub(' ', str(text or '')).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rstrip()
    return cut.rsplit(' ', 1)[0] + '…' if ' ' in cut else cut + '…'


def first_sentence(text, limit=MAX_SUMMARY):
    """The summary's first sentence, clipped ('' when there is none)."""
    body = _WS.sub(' ', str(text or '')).strip()
    if not body:
        return ''
    head = _SENTENCE.split(body, 1)[0]
    return _clip(head, limit)


def _flow(chain):
    data = chain.get('flow') if isinstance(chain, dict) else None
    return data if isinstance(data, dict) else {}


def _labels(entries, keys=('label', 'variable_name')):
    """Distinct non-empty labels of a node list (input/output nodes)."""
    out = []
    for entry in entries or ():
        if not isinstance(entry, dict):
            continue
        for key in keys:
            text = _clip(entry.get(key), MAX_LABEL_CHARS)
            if text:
                if text not in out:
                    out.append(text)
                break
    return out[:MAX_LABELS]


def _structural(chain):
    """What a chain with no flow metadata still says about itself."""
    counts = []
    total = 0
    for key, name in _NODE_KEYS:
        entries = chain.get(key)
        if not isinstance(entries, (list, tuple)) or not entries:
            continue
        total += len(entries)
        counts.append('%d %s' % (len(entries), name))
    if not counts:
        return ''
    return '%d node(s): %s' % (total, ', '.join(counts))


def _section_titles(flow):
    titles = []
    for section in flow.get('sections') or ():
        if not isinstance(section, dict):
            continue
        title = _clip(section.get('title'), 32)
        if title:
            titles.append(title)
    return titles


def _node_trail(flow):
    """The chain's own node actions as a short ``a → b`` trail ([] if none).

    A chain without section titles has no other substance to offer: an atomic
    tool is ONE node, and its ``does`` line is the whole of what it does.
    """
    out = []
    for entry in flow.get('nodes') or ():
        if not isinstance(entry, dict):
            continue
        # The action only: a node entry also carries its settings and wiring
        # ("supplies X — asked for on the desktop"), which is prompt material
        # for the reasoner and noise in a one-line brief.
        text = _clip(str(entry.get('does') or '').split(' — ')[0],
                     MAX_TRAIL_CHARS)
        if text and text not in out:
            out.append(text)
        if len(out) >= MAX_TRAIL:
            break
    return out


def brief(chain, limit=BRIEF_LIMIT):
    """The chain's routing line, derived from its own metadata ('' if empty).

    Shape: ``<summary sentence>; <section flow, or the node trail>; needs
    <inputs>; returns <outputs>``.  A chain without flow metadata gets the
    structural form (node counts + needs/returns) rather than hand-written
    prose.
    """
    if not isinstance(chain, dict):
        return ''
    flow = _flow(chain)
    parts = []

    summary = first_sentence(flow.get('summary'))
    if summary:
        parts.append(summary)

    titles = _section_titles(flow)
    if titles:
        parts.append(' → '.join(titles[:MAX_SECTION_TITLES]))
    else:
        trail = _node_trail(flow)
        if trail:
            parts.append(' → '.join(trail))
        elif flow.get('steps'):
            steps = [step for step in flow.get('steps') or ()
                     if isinstance(step, dict)]
            if steps:
                parts.append('%d step(s)' % len(steps))

    if not parts:
        structural = _structural(chain)
        if structural:
            parts.append(structural)

    needs = _labels(chain.get('input_nodes'))
    returns = _labels(chain.get('output_nodes'))
    if needs:
        parts.append('needs ' + ', '.join(needs))
    if returns:
        parts.append('returns ' + ', '.join(returns))

    text = '; '.join(part for part in parts if part)
    return text if len(text) <= limit else text[:limit].rstrip() + '…'


def from_file(path, limit=BRIEF_LIMIT):
    """Brief for a chain file (raises when the file is not a chain)."""
    import json
    import os

    with open(path, 'r', encoding='utf-8') as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError('%s holds %r, not a chain object'
                         % (os.fspath(path), type(data).__name__))
    return brief(data, limit)
