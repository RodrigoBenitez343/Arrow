"""flow.py — the chain's OWN description, stored inside the chain file.

A loose chain used to describe itself only through whatever the user typed in
``description``, so an agent reading it back got a naive one-liner.  This
module defines the **permanent flow metadata** a chain can carry instead::

    "flow": {
      "summary":  "what the chain does, in one paragraph",
      "sections": [{"title": "...", "note": "...", "nodes": ["0x1", ...],
                    "hue": "#22D3EE"}],
      "steps":    [{"node": "0x1", "does": "what this step does"}]
    }

``sections`` are the BOXES the canvas draws around parts of the chain;
``nodes`` is what each node does (one line per node, the chain's own words
where the Builder wrote them, derived from the canvas where it did not) and
``steps`` is the same thing in execution order, which the visual walkthrough
speaks.  ``stamp`` fingerprints what was described, so a reader can tell
whether the chain changed since (`player/chain_refresh.py` rewrites exactly
those files, with no model and no canvas).

Two ways in: ``complete(snapshot, flow)`` from a live canvas, and
``complete_static(chain, flow)`` from the file alone — both derive the same
lines through ``describe.acting``, so a chain reads the same whichever side
touched it last.

Nothing here talks to a model or to Qt: this module is pure data plus the
deterministic step list built from the resolved canvas (``snapshot.py``), so
the walkthrough says the same thing every run.  Malformed metadata raises
``FlowMetadataError`` — it is never silently repaired.
"""

import hashlib
import json
import re
from types import SimpleNamespace

from . import describe as _describe
from . import ports as _ports
from .errors import BuilderCanvasError, FlowMetadataError

#: Top-level key in the chain file.
FLOW_KEY = 'flow'

#: Bounds.  A section list longer than this is not a section list.
MAX_SECTIONS = 16
MAX_STEPS = 400
MAX_NODES = 400
MAX_SUMMARY = 2000
MAX_TITLE = 80
#: Prose limits.  These are display ceilings, not validation gates: a stored
#: sentence that runs long is CLIPPED (``_text(..., clip=True)``), because a
#: description one character over the line must never make a chain
#: undescribable — that is exactly how a walkthrough died on a 601-char line.
MAX_NOTE = 4000
MAX_DOES = 4000

#: Box hues cycled when the metadata names none (readable on the dark plane).
HUES = ('#22D3EE', '#4A90E2', '#A78BFA', '#F59E0B', '#34D399', '#F472B6')

#: Bumped whenever ``describe`` learns to say something better about a node.
#: It is part of every stamp, so a sentence written by an OLDER describer reads
#: as stale and is derived again.  Without it a stored line stood forever: the
#: node had not changed, so a better describer could never reach a chain that
#: had already been described (measured: every check kept saying "the element it
#: was picked for this check" after the checks learned to name their target).
DERIVATION = 2

#: A chain file's node lists -> the builder's short TYPE name.  ``acting`` keys
#: on the type, so a chain can be described WITHOUT a canvas (an agent reload,
#: a workspace sweep) from the file the editor already wrote.
CHAIN_NODE_TYPES = (
    ('input_nodes', 'input'),
    ('sequences', 'sequence'),
    ('web_sequences', 'web_sequence'),
    ('llm_nodes', 'llm'),
    ('conditional_nodes', 'conditional'),
    ('chain_import_nodes', 'chain_import'),
    ('form_filler_nodes', 'form_filler'),
    ('code_nodes', 'code'),
    ('container_nodes', 'container'),
    ('context_nodes', 'context'),
    ('handle_nodes', 'handle'),
    ('mcp_nodes', 'mcp'),
    ('output_nodes', 'output'),
)

#: What a saved node entry carries that is NOT part of what it does: identity,
#: placement, wires and the nested sub-chains of an import node.  Two files
#: that differ only here describe the same behaviour, so the stamp ignores it.
#: ``description`` belongs here too: it is the SENTENCE about the node (the
#: Builder writes it, the dialog edits it), so editing the wording must not
#: make the sentence look stale and get it rewritten.
_BOOKKEEPING = frozenset((
    'type', 'node_id', 'id', 'name', 'position', 'connections', 'custom',
    'description',
    'sequences', 'conditional_nodes', 'llm_nodes', 'tts_nodes',
    'chain_import_nodes', 'input_nodes', 'output_nodes', 'data_output_nodes',
))

_HEX = re.compile(r'^#[0-9a-fA-F]{6}$')


# ------------------------------------------------------------- validating

def empty():
    """The metadata of a chain that has never been described."""
    return {'summary': '', 'sections': [], 'steps': [], 'nodes': [],
            'stamp': ''}


def _entries(raw_list, label, limit):
    """``[{'node', 'does'}]`` from a raw ``nodes`` / ``steps`` list."""
    if raw_list is None:
        return []
    if not isinstance(raw_list, (list, tuple)):
        raise FlowMetadataError('"flow.%s" must be a list' % label)
    if len(raw_list) > limit:
        raise FlowMetadataError('%d %s; the limit is %d'
                                % (len(raw_list), label, limit))
    out = []
    for index, raw in enumerate(raw_list):
        if not isinstance(raw, dict):
            raise FlowMetadataError('%s %d is not an object'
                                    % (label[:-1], index + 1))
        node_id = str(raw.get('node') or raw.get('id') or '').strip()
        if not node_id:
            raise FlowMetadataError('%s %d names no node'
                                    % (label[:-1], index + 1))
        out.append({
            'node': node_id,
            'does': _text(raw.get('does'), MAX_DOES,
                          '%s %r text' % (label[:-1], node_id), clip=True),
            # Which version of the node the sentence describes.  Without it a
            # refresh could not tell authored words from words that predate an
            # edit, and would keep describing the node that no longer exists.
            'stamp': _text(raw.get('stamp'), 64,
                           '%s %r stamp' % (label[:-1], node_id)),
        })
    return out


def _text(value, limit, label, required=False, clip=False):
    """Normalised text; PROSE is clipped to *limit*, structure must fit.

    A title, a node id or a stamp that overflows is a broken file and raises.
    A sentence is prose written from the canvas: clipping it loses nothing a
    reader can act on, while raising made an over-long description fatal.
    """
    text = ' '.join(str(value or '').split())
    if required and not text:
        raise FlowMetadataError('%s is empty' % label)
    if len(text) > limit:
        if not clip:
            raise FlowMetadataError(
                '%s is %d chars; the limit is %d' % (label, len(text), limit))
        text = text[:max(0, limit - 1)] + '…'
    return text


def _nodes(value, label):
    if not isinstance(value, (list, tuple)):
        raise FlowMetadataError('%s must be a list of node ids' % label)
    ids = []
    for item in value:
        node_id = str(item or '').strip()
        if not node_id:
            raise FlowMetadataError('%s carries an empty node id' % label)
        if node_id not in ids:
            ids.append(node_id)
    if not ids:
        raise FlowMetadataError('%s lists no node ids' % label)
    return ids


def normalise(data):
    """Validate and clip one ``flow`` object (raises ``FlowMetadataError``).

    ``None`` / ``{}`` are a valid empty metadata; anything else must be an
    object with the documented shape, or this raises — a half-written flow
    would draw boxes around the wrong nodes.
    """
    if not data:
        return empty()
    if not isinstance(data, dict):
        raise FlowMetadataError(
            '"flow" must be an object with summary / sections / steps, got %s'
            % type(data).__name__)

    raw_sections = data.get('sections') or []
    if not isinstance(raw_sections, (list, tuple)):
        raise FlowMetadataError('"flow.sections" must be a list')
    if len(raw_sections) > MAX_SECTIONS:
        raise FlowMetadataError(
            '%d sections; the limit is %d — group more nodes per section'
            % (len(raw_sections), MAX_SECTIONS))
    sections = []
    for index, raw in enumerate(raw_sections):
        if not isinstance(raw, dict):
            raise FlowMetadataError('section %d is not an object' % (index + 1))
        title = _text(raw.get('title'), MAX_TITLE,
                      'section %d title' % (index + 1), required=True)
        hue = str(raw.get('hue') or '').strip() or HUES[
            index % len(HUES)]
        if not _HEX.match(hue):
            raise FlowMetadataError(
                'section %r hue %r is not #RRGGBB' % (title, hue))
        sections.append({
            'title': title,
            'note': _text(raw.get('note'), MAX_NOTE, 'section %r note' % title,
                          clip=True),
            'nodes': _nodes(raw.get('nodes'), 'section %r' % title),
            'hue': hue.upper(),
        })

    raw_steps = data.get('steps') or []
    if not isinstance(raw_steps, (list, tuple)):
        raise FlowMetadataError('"flow.steps" must be a list')
    if len(raw_steps) > MAX_STEPS:
        raise FlowMetadataError(
            '%d steps; the limit is %d' % (len(raw_steps), MAX_STEPS))
    steps = []
    for index, raw in enumerate(raw_steps):
        if not isinstance(raw, dict):
            raise FlowMetadataError('step %d is not an object' % (index + 1))
        node_id = str(raw.get('node') or raw.get('id') or '').strip()
        if not node_id:
            raise FlowMetadataError('step %d names no node' % (index + 1))
        steps.append({
            'node': node_id,
            'does': _text(raw.get('does'), MAX_DOES,
                          'step %r text' % node_id, clip=True),
        })

    return {
        'summary': _text(data.get('summary'), MAX_SUMMARY, 'summary',
                         clip=True),
        'sections': sections,
        'steps': steps,
        'nodes': _entries(data.get('nodes'), 'nodes', MAX_NODES),
        'stamp': _text(data.get('stamp'), 64, 'stamp'),
    }


def is_empty(flow):
    """True when the metadata says nothing about the chain."""
    return not any((flow or {}).get(key) for key in ('summary', 'sections',
                                                     'steps', 'nodes'))


def _resolve(snapshot, ref, where):
    """The node a reference names (by id OR by display name).

    Ids reach the stored metadata from the digest, so they are the live canvas
    ids; a chain FILE carries ids of its own, and the panel translates those to
    the live ones before anything here runs (``ConfigManager.get_flow`` keeps
    the loader's id map).  A name is accepted as a fallback, for a human
    writing metadata by hand.
    """
    try:
        return _describe.resolve(snapshot, ref)
    except BuilderCanvasError as exc:
        raise FlowMetadataError(
            '%s names %r, which is not on this canvas (%s) — re-describe the '
            'chain if the node was deleted or the chain was rebuilt'
            % (where, ref, exc)) from exc


def check_nodes(snapshot, flow):
    """Validate every reference and return them as LIVE node ids.

    Raises ``FlowMetadataError`` when a section or step names nothing on this
    canvas (a hallucinated id would draw a box around empty space).  A name is
    accepted where an id is, and is stored as the node's id.
    """
    sections = []
    for section in (flow or {}).get('sections') or ():
        where = 'section %r' % section['title']
        ids = []
        for ref in section['nodes']:
            node_id = _resolve(snapshot, ref, where).id
            if node_id not in ids:
                ids.append(node_id)
        sections.append(dict(section, nodes=ids))
    steps = []
    for step in (flow or {}).get('steps') or ():
        node_id = _resolve(snapshot, step['node'], 'a step').id
        steps.append(dict(step, node=node_id))
    nodes = []
    for entry in (flow or {}).get('nodes') or ():
        node_id = _resolve(snapshot, entry['node'], 'a node entry').id
        nodes.append(dict(entry, node=node_id))
    return {'summary': (flow or {}).get('summary', ''),
            'sections': sections,
            'steps': steps,
            'nodes': nodes,
            'stamp': (flow or {}).get('stamp', '')}


def complete(snapshot, flow, force=False):
    """Fill a description for EVERY node on the canvas, then return the flow.

    A model writes prose about what it was shown; it will not enumerate every
    node of a long chain, and it may describe the wiring and forget the action.
    So the stored metadata is completed deterministically: one entry per node,
    ``describe.does_of`` (own settings, artifact, then the type's purpose) with
    the AUTHORED text always winning.  That is what makes a chain's metadata
    say what each thing does even when the model answered in one paragraph.

    This is the AUTHORING side: a sentence with no stamp was just written about
    the canvas in front of it, so it is kept as is.  A stored refresh
    (``complete_static``) is stricter — there, a sentence stands only for the
    node version its stamp names.

    ``force`` ignores BOTH: every sentence is derived again from the node it
    describes, so asking for a description of an unchanged chain still rewrites
    what is stored (the stamps would otherwise say "already current").
    """
    flow = dict(flow or {})
    authored = {}
    for entry in (() if force else (flow.get('nodes') or ())):
        node_id = _resolve(snapshot, entry['node'], 'a node entry').id
        text = ' '.join(str(entry.get('does') or '').split())
        if text:
            authored[node_id] = (text, str(entry.get('stamp') or ''))
    nodes = []
    for node in sorted(snapshot.nodes.values(), key=lambda n: n.position):
        current = node_stamp(node.type, node.properties)
        text, written_for = authored.get(node.id, ('', ''))
        if not text or (written_for and written_for != current):
            # Authored words only stand while they describe THIS version of the
            # node; otherwise the sentence is re-derived from its settings.
            text = _describe.does_of(snapshot, node)
        nodes.append({'node': node.id, 'does': _clip(text, MAX_DOES),
                      'stamp': current})
    flow['nodes'] = nodes
    flow['stamp'] = fingerprint(
        [(node.type, node.properties) for node in snapshot.nodes.values()])
    return flow


def _canonical(properties):
    """The part of a node's config that shapes what it DOES (drops identity,
    placement, wires and nested sub-chains)."""
    out = {}
    for key, value in (properties or {}).items():
        if key in _BOOKKEEPING or value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, tuple, dict)) and not value:
            continue
        out[str(key)] = value
    return out


def _file_nodes(chain):
    """``[(type, canonical properties)]`` for every node a chain file carries."""
    out = []
    for key, ntype in CHAIN_NODE_TYPES:
        entries = (chain or {}).get(key)
        if not isinstance(entries, (list, tuple)):
            continue
        for entry in entries:
            if isinstance(entry, dict):
                out.append((ntype, _canonical(entry)))
    return out


def fingerprint(nodes):
    """A short hash of what a chain is made of (order and ids excluded).

    Node ids are regenerated on every load and reordering a list changes
    nothing about behaviour, so both are left out: the stamp changes only when
    a node was added, removed, or reconfigured — which is when a description
    can no longer be trusted.
    """
    payload = sorted(
        json.dumps([ntype, props], sort_keys=True, default=str)
        for ntype, props in nodes)
    payload.append('derivation:%d' % DERIVATION)
    return hashlib.sha1('\n'.join(payload).encode('utf-8')).hexdigest()[:16]


def node_stamp(node_type, properties):
    """Fingerprint of ONE node's type and settings (what its sentence says)."""
    return fingerprint([(node_type, _canonical(properties))])


def stamp_of(chain):
    """The fingerprint of a chain FILE's node entries."""
    return fingerprint(_file_nodes(chain))


def is_stale(chain):
    """True when the stored description predates what the chain contains.

    A chain that was never described is NOT stale: there is no description to
    update (and turning one on automatically would make an undescribed chain
    routable without anyone deciding that).  A described chain whose stamp is
    missing or differs — the file predates this feature, or a node changed —
    is stale, which is what ``player/chain_refresh.py`` rewrites.
    """
    flow = (chain or {}).get(FLOW_KEY)
    if not isinstance(flow, dict) or is_empty(flow):
        return False
    return str(flow.get('stamp') or '') != stamp_of(chain)


def complete_static(chain, flow=None):
    """``complete`` for a chain FILE: no canvas, no model, same lines.

    A refresh, not an authoring pass: a stored sentence is kept ONLY while its
    ``stamp`` still matches the node it describes, so an edited node is the
    only one whose line is rewritten.  Unchanged nodes keep the wording they
    already had — including the Builder's — which is what makes a one-node
    edit a one-line change instead of a rewrite of the whole chain.

    Node ids are regenerated by every load, so when an id no longer matches,
    the sentence is looked up by its node fingerprint instead: the same node in
    a re-saved file is recognised even though its id changed.
    """
    flow = dict(flow if flow is not None
                else ((chain or {}).get(FLOW_KEY) or {}))
    authored = {}
    by_stamp = {}
    for entry in flow.get('nodes') or ():
        if not isinstance(entry, dict):
            continue
        ref = str(entry.get('node') or '').strip()
        text = ' '.join(str(entry.get('does') or '').split())
        written_for = str(entry.get('stamp') or '')
        if not (ref and text and written_for):
            continue           # unstamped: cannot be tied to a node version
        authored[ref] = (text, written_for)
        by_stamp.setdefault(written_for, []).append(text)
    nodes = []
    for key, ntype in CHAIN_NODE_TYPES:
        for entry in (chain or {}).get(key) or ():
            if not isinstance(entry, dict):
                continue
            node_id = str(entry.get('node_id') or entry.get('id') or '').strip()
            if not node_id:
                continue
            props = _canonical(entry)
            current = node_stamp(ntype, entry)
            text, written_for = authored.get(node_id, ('', ''))
            if not text:
                # Same node, new id: accept the stored sentence only when
                # exactly one node version matches it.
                candidates = by_stamp.get(current) or []
                if len(candidates) == 1:
                    text = candidates[0]
                    written_for = current
            if not text or written_for != current:
                node = SimpleNamespace(type=ntype,
                                       name=str(entry.get('name') or ''),
                                       properties=props)
                text = _describe.does_of(None, node)
            nodes.append({'node': node_id, 'does': _clip(text, MAX_DOES),
                          'stamp': current})
    flow['nodes'] = nodes
    flow['stamp'] = stamp_of(chain)
    return flow


# ---------------------------------------------------------------- digest

def digest(flow, include_nodes=True):
    """The stored metadata as prompt text ('' when the chain has none).

    ``include_nodes=False`` leaves the per-node lines out: a question about how
    the chain flows is answered by the summary and the box notes, and one line
    per node is the bulk of this text (the node's own sentence, on demand,
    is ``read_node``'s job).
    """
    flow = flow or {}
    if is_empty(flow):
        return ''
    lines = ["FLOW METADATA (stored in this chain — the author's own words, "
             "written by an earlier builder turn; treat it as intent, the "
             "canvas as fact):"]
    if flow.get('summary'):
        lines.append('SUMMARY: ' + flow['summary'])
    if flow.get('sections'):
        lines.append('SECTIONS (the boxes the canvas draws):')
        for section in flow['sections']:
            lines.append('  %r — nodes %s%s'
                         % (section['title'], ', '.join(section['nodes']),
                            (' — ' + section['note']) if section['note']
                            else ''))
    if include_nodes and flow.get('nodes'):
        lines.append('NODES (what each node does, one line each):')
        for entry in flow['nodes']:
            lines.append('  %s: %s' % (entry['node'], entry['does']))
    if flow.get('steps'):
        lines.append('AUTHORED STEPS:')
        for step in flow['steps']:
            lines.append('  %s: %s' % (step['node'], step['does']))
    return '\n'.join(lines)


def headline(flow):
    """The chain in its own words, as small as it goes: summary + boxes.

    The last-resort prompt for a model whose window cannot hold the full turn:
    what it needs to answer is the goal and the parts, not one line per node.
    """
    return digest(flow, include_nodes=False)


# -------------------------------------------------------------- sections

def _clip(text, limit):
    """Whitespace-collapsed text, never longer than *limit* characters."""
    text = ' '.join(str(text or '').split())
    if len(text) <= limit:
        return text
    return text[:max(0, limit - 1)] + '…'


#: How long a DERIVED section note may get.  The box prints it, and one line
#: per member node stops being a description and starts being the whole walk.
MAX_DERIVED_NOTE = 400


def _section_note(snapshot, node_ids):
    """What a section does, when it was written without a note.

    The box PRINTS the note, so a section that carries only a title is a
    headline with no body on the canvas.  The member nodes' own actions
    (``describe.does_of``) are the deterministic description of that stretch.
    """
    parts = []
    for node_id in node_ids:
        node = snapshot.node(node_id)
        if node is not None:
            parts.append(_clip(_describe.does_of(snapshot, node), 150))
    return _clip(' → '.join(part for part in parts if part), MAX_DERIVED_NOTE)


def _wired(snapshot, src_id, dst_id):
    """True when a wire runs from *src_id* to *dst_id* (either kind)."""
    if src_id is None:
        return False
    edges = (list(snapshot.exec_out_edges(src_id))
             + list(snapshot.data_out_edges(src_id)))
    return any(edge.dst_id == dst_id for edge in edges)


def _phase_groups(snapshot, order):
    """The walk cut into phases: every CHECK/LOOP closes one, and a phase
    never jumps between nodes without a wire.

    Topology, not geometry: a section is a stretch of execution ("ask, then
    draft, then check"), which is what a reader needs.  Splitting on canvas
    distance instead produced one box per node on a chain laid out wide.

    The walk is depth-first, so it leaves one branch's subtree and enters its
    SIBLING with no wire between the two: left unfixed, one box held both
    outcomes of a check (or a branch and an unrelated root) as if they ran
    together.  A wire is what keeps two steps in one box.

    The cut then leaves a branch's own LEAF alone (the check closes the phase,
    the leaf follows it with one wire and nothing after it), so every one-node
    phase joins the phase it is wired to — see ``_absorb_leaves``.
    """
    groups, current = [], []
    previous = None
    for node_id in order:
        node = snapshot.node(node_id)
        if node is None:
            raise FlowMetadataError(
                'the walk reached unknown node %s' % node_id)
        if current and not _wired(snapshot, previous, node_id):
            groups.append(current)
            current = []
        current.append(node)
        previous = node_id
        if node.role in ('CHECK', 'LOOP'):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return _absorb_leaves(snapshot, groups, order)


def _absorb_leaves(snapshot, groups, order):
    """Fold a one-node phase into the phase it is wired to.

    A check hands execution to two siblings, so the walk visits one branch and
    then the other: the wire cut closes a phase per leaf, and a leaf that its
    check feeds directly stood ALONE in a box — a box of its own for a node
    that shares a wire with the box beside it.  Nothing here joins nodes with
    no wire between them: a lone node keeps its own box when it is wired to
    nothing (an orphan on the canvas).
    """
    rank = {node_id: index for index, node_id in enumerate(order)}
    groups = [list(group) for group in groups]
    changed = True
    while changed:
        changed = False
        for index, group in enumerate(groups):
            if len(group) != 1:
                continue
            node = group[0]
            for host_index, host in enumerate(groups):
                if host_index == index:
                    continue
                if any(_wired(snapshot, node.id, member.id)
                       or _wired(snapshot, member.id, node.id)
                       for member in host):
                    groups[host_index] = sorted(
                        host + group, key=lambda n: rank.get(n.id, 0))
                    del groups[index]
                    changed = True
                    break
            if changed:
                break
    return groups


def _clean_name(name):
    """A node name without recording bookkeeping: ``applybutton.stdout_copy``.

    A recording node is named after its file, so a box title read
    ``P1 applybutton.json_copy_copy → Web Conditional``.  The file name IS the
    identity, the extension and the ``_copy`` suffixes are not.
    """
    text = str(name or '').replace('.json', '')
    for suffix in ('_copy', ' copy'):
        while text.endswith(suffix):
            text = text[:-len(suffix)].rstrip()
    return text


def _box(index, group):
    """One derived box: a title from its ends, the note filled in later.

    ``name or type``: a form filler (and a few other types) carries no name of
    its own, and a title ending in "→ " says nothing at all.
    """
    first, last = group[0], group[-1]
    head = _clip(_clean_name(first.name) or first.type, 32)
    tail = _clip(_clean_name(last.name) or last.type, 32)
    title = ('P%d %s' % (index + 1, head)
             if first is last
             else 'P%d %s → %s' % (index + 1, head, tail))
    return {'title': title, 'note': '', 'nodes': [n.id for n in group],
            'hue': HUES[index % len(HUES)]}


def sections(snapshot, flow=None, force_notes=False):
    """The boxes to draw: the stored ones (refs resolved to LIVE ids), else
    the phases of the walk.

    The fallback exists so boxing works on a loose chain that has never been
    described: every CHECK/LOOP closes a phase, and a phase never crosses a
    hop with no wire (``_phase_groups``).  Every box leaves here with a note —
    the box draws it, so an empty note is a headline with no body.

    ``force_notes`` re-derives the note of every box from the nodes it holds,
    which is what an explicit "describe now" means: the grouping stays the
    author's, the wording is regenerated.

    No box is ever invented around nodes the stored sections do not name: a
    frame is the union of its members, so a guessed group reaches across the
    canvas and rounds whatever sits in between (the author's adjacency is not
    the canvas's layout).
    """
    stored = (flow or {}).get('sections') or []
    out = []
    if stored:
        for section in stored:
            where = 'section %r' % section['title']
            ids = []
            for ref in section['nodes']:
                node_id = _resolve(snapshot, ref, where).id
                if node_id not in ids:
                    ids.append(node_id)
            out.append(dict(section, nodes=ids))
    else:
        _lines, order, _visited, _steps = _describe.walk(snapshot)
        out = [_box(index, group) for index, group
               in enumerate(_phase_groups(snapshot, order))]

    for section in out:
        if force_notes or not section.get('note'):
            section['note'] = _section_note(snapshot, section['nodes'])
    return out


def node_texts(flow, snapshot=None):
    """``{node_id: sentence}`` for the canvas to print under each node.

    ``flow['nodes']`` is the metadata's own per-node list.  When a *snapshot*
    of the live canvas is given, a node's own ``description`` property wins:
    that property is the wording a node dialog edits, so an edited sentence
    shows up without re-describing the chain.
    """
    texts = {}
    for entry in (flow or {}).get('nodes') or ():
        if isinstance(entry, dict):
            text = ' '.join(str(entry.get('does') or '').split())
            if text:
                texts[str(entry.get('node') or '')] = text
    for node_id, node in (getattr(snapshot, 'nodes', None) or {}).items():
        described = ' '.join(
            str((getattr(node, 'properties', None) or {}).get('description')
                or '').split())
        if described:
            texts[str(node_id)] = described
    return texts


def prune(snapshot, flow):
    """``(flow, dropped)`` with the refs this canvas does not have removed.

    A description written by an older build (or a chain edited after it was
    described) can name node ids that no longer exist.  Drawing stops at the
    first unknown id, so ONE stale ref hid the boxes of the WHOLE chain; the
    rest of the metadata is still true, so it is kept and the stale refs are
    counted for the caller to report.  A section whose nodes are all gone is
    dropped: there is nothing to put a frame around.
    """
    out = dict(flow or {})
    dropped = [0]

    def _alive(ref):
        try:
            return _describe.resolve(snapshot, ref).id
        except BuilderCanvasError:
            dropped[0] += 1
            return None

    sections = []
    for section in out.get('sections') or []:
        ids = []
        for ref in section.get('nodes') or []:
            node_id = _alive(ref)
            if node_id and node_id not in ids:
                ids.append(node_id)
        if ids:
            sections.append(dict(section, nodes=ids))
    out['sections'] = sections

    steps = []
    for step in out.get('steps') or []:
        node_id = _alive(step.get('node'))
        if node_id:
            steps.append(dict(step, node=node_id))
    out['steps'] = steps

    nodes = []
    for entry in out.get('nodes') or []:
        node_id = _alive(entry.get('node'))
        if node_id:
            nodes.append(dict(entry, node=node_id))
    out['nodes'] = nodes
    return out, dropped[0]


# ----------------------------------------------------------------- steps

def _wiring(snapshot, node):
    """How this step connects: what enters it and what it hands on."""
    lines = []
    incoming = snapshot.exec_in_edges(node.id)
    if incoming:
        lines.append('entered by ' + ', '.join(
            '%s.%s' % (e.src_id, e.src_port) for e in incoming))
    outgoing = _describe._branch_order(node, snapshot.exec_out_edges(node.id))
    if outgoing:
        parts = []
        for edge in outgoing:
            label = {_ports.BRANCH_TRUE: 'true → ',
                     _ports.BRANCH_FALSE: 'false → ',
                     _ports.LOOP_OUT: 'output (loop) → '}.get(edge.src_role, '')
            parts.append('%s%s' % (label, edge.dst_id))
        lines.append('hands execution to ' + ', '.join(parts))
    data_in = snapshot.data_in_edges(node.id)
    if data_in:
        lines.append('reads ' + ', '.join(
            '%s.%s → %s' % (e.src_id, e.src_port, e.dst_port)
            for e in data_in))
    data_out = snapshot.data_out_edges(node.id)
    if data_out:
        lines.append('writes ' + ', '.join(
            '%s → %s.%s' % (e.src_port, e.dst_id, e.dst_port)
            for e in data_out))
    if not lines:
        lines.append('passive: consulted through its data ports, never run')
    return lines


def steps(snapshot, flow=None):
    """The visual walkthrough: one entry per executed node, in walk order.

    Deterministic — the order is the digest's execution walk and every line
    comes from the node's own config, its artifact and its wires
    (``describe.does_of``).  ``flow.steps`` / ``flow.nodes`` only add the
    author's sentence where the canvas says less.
    """
    _lines, order, _visited, _ordered = _describe.walk(snapshot)
    authored = {}
    for entry in ((flow or {}).get('nodes') or []):
        authored[_resolve(snapshot, entry['node'], 'a node entry').id] = \
            entry['does']
    for step in ((flow or {}).get('steps') or []):
        # A step is the walk's own wording, so it outranks the node entry.
        authored[_resolve(snapshot, step['node'], 'a step').id] = step['does']
    out = []
    for index, node_id in enumerate(order, 1):
        node = snapshot.node(node_id)
        if node is None:
            raise FlowMetadataError('the walk reached unknown node %s' % node_id)
        body = []
        does = authored.get(node_id) or _describe.does_of(snapshot, node)
        if does:
            body.append(_clip(does, 400))
        body.extend(_wiring(snapshot, node))
        out.append({
            'index': index,
            'id': node_id,
            'role': node.role or '?',
            'name': node.name,
            'title': '%s %s "%s"' % (node.id, node.type, _clip(node.name, 50)),
            'body': body,
        })
    return out
