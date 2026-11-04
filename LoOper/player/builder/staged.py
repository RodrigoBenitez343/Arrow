"""staged.py — describe a chain ONE CONNECTION AT A TIME.

A single turn over a whole canvas needs the whole canvas in one prompt (the
digest, the index, the selection: ~34k characters), and a small local model
refuses it or drowns in it.  This module asks the same questions in passes, so
every prompt stays a few hundred tokens:

1. **Nodes** (no model).  ``describe.does_of`` reads each node's own settings,
   artifact and type, so every node already has its sentence.
2. **Index** (no model).  Those sentences are clipped and attached to the node
   identity the model needs (id, type, name) plus the chain's execution order —
   the "what exists and in what order" the model reasons from.
3. **Connections** (one model call each).  For every wire — execution and data,
   in walk order — the model receives only the source node, the target node and
   the ports that wire joins, and answers with one sentence: what that
   connection hands on and what the target does with it.
4. **Assembly** (no model).  Sections are the topology's phases; each box's note
   is the sentences of the wires inside it plus the one it hands to the next
   box.  The summary is one last small call over those notes.

Slow by design: N wires means N calls.  Nothing is truncated, so nothing has to
be guessed — the point is that a small model can finish the job.
"""

from . import describe as _describe
from . import flow as _flow
from . import ports as _ports

#: A node sentence as the MODEL sees it.  The full sentence is what gets
#: stored; this is the summary it is described from (recordings run long).
MAX_LINE = 260
#: One sentence per connection is the contract; a runaway answer is clipped.
MAX_ANSWER = 400
#: Wires beyond this are not a chain a per-wire pass can describe.
MAX_CONNECTIONS = 200

_SYSTEM = (
    "You describe ONE wire of an automation chain at a time. You are given what "
    "the source node does, what the target node does and the ports the wire "
    "joins. Answer with ONE sentence of at most 40 words, third person, present "
    "tense: what this connection hands from the source to the target, and what "
    "the target does with it. Say what it ACHIEVES — do not restate the two "
    "sentences you were given. Never mention node names, recording file names "
    "or node ids; name only actions and values. No lists, no headings, no "
    "quotes, no preamble. Name only values the nodes you were given actually "
    "mention."
)

#: The summary is the ONE call that needs the whole picture: a paragraph built
#: only from the box notes recites the boxes ("it clicks Apply, then it fills
#: the form…) and never says what the automation is FOR.  These are its bounds.
SUMMARY_NODES_CHARS = 2600
SUMMARY_NOTE_CHARS = 300
SUMMARY_FACTS = 6

_SUMMARY_SYSTEM = (
    "You explain an automation chain to the colleague who will maintain it. "
    "From the parts below, write ONE paragraph of at most 100 words. Say first "
    "what the automation is FOR — its goal, in plain language, in the first "
    "sentence — then how it works in execution order: the loop it repeats, the "
    "branches that skip or leave, what makes it move on. Then what it produces "
    "or leaves behind. Do not recite the wiring, do not list the nodes, do not "
    "repeat a sentence you were given verbatim. No headings, no lists, no "
    "preamble. If a fact below is a defect (a dead end, an open branch), the "
    "chain still has a purpose: say the purpose and note the gap in passing."
)


def _clip(text, limit):
    """Whitespace-collapsed text, never longer than *limit* characters."""
    text = ' '.join(str(text or '').split())
    if len(text) <= limit:
        return text
    return text[:max(0, limit - 1)] + '…'


def prompt_summary(text):
    """A stored node sentence as the MODEL sees it.

    A recording's sentence ends with the URLs it opens (sometimes two of them).
    The URLs are worth keeping in the file and worthless in a prompt: one of
    them ate the whole budget, and a summary written from it talked about a
    query string (``the text "Senior Gen AI" appears``) instead of an action.
    Everything from ``opens:`` to the end goes.
    """
    import re

    text = re.sub(r'\s*[—-]?\s*opens:.*$', '', str(text or ''))
    return _clip(text, MAX_LINE)


# ------------------------------------------------------------ stage 1 + 2

def node_lines(snapshot):
    """``{node id: its full sentence}`` — stage 1, deterministic."""
    return {node.id: _describe.does_of(snapshot, node)
            for node in snapshot.nodes.values()}


def order_of(snapshot):
    """The execution order of the chain (ids), from the digest's own walk."""
    _lines, order, _visited, _steps = _describe.walk(snapshot)
    return list(order)


def order_line(snapshot, order):
    """The chain's shape in one line: ``ask → draft → check``.

    Names, not ids: the model echoes what it is given, and an id-laden order
    line came back as prose full of hex ("check 0x245756429e0 triggers…").
    """
    names = []
    for node_id in order:
        node = snapshot.node(node_id)
        if node is not None:
            names.append(_clip(node.name or node.type or node.id, 40))
    return _clip(' → '.join(names) or '(nothing runs)', 400)


# ------------------------------------------------------------- connections

def _branch(role):
    return {_ports.BRANCH_TRUE: ' (TRUE branch)',
            _ports.BRANCH_FALSE: ' (FALSE branch)',
            _ports.LOOP_OUT: ' (loop-back output)'}.get(role, '')


def _wire_label(edge):
    if edge.kind == 'exec':
        return '%s.%s%s --execution--> %s.%s' % (
            edge.src_id, edge.src_port, _branch(edge.src_role),
            edge.dst_id, edge.dst_port)
    return '%s.%s --data--> %s.%s' % (edge.src_id, edge.src_port,
                                      edge.dst_id, edge.dst_port)


def connections(snapshot, order=None):
    """Every wire, as one describe job: execution first, in walk order."""
    order = order if order is not None else order_of(snapshot)
    rank = {node_id: index for index, node_id in enumerate(order)}
    far = len(rank) + 1
    branch_rank = {_ports.EXEC_OUT: 0, _ports.BRANCH_TRUE: 1,
                   _ports.BRANCH_FALSE: 2, _ports.LOOP_OUT: 3}
    edges = list(snapshot.edges)
    exec_edges = sorted(
        (e for e in edges if e.kind == 'exec'),
        key=lambda e: (rank.get(e.src_id, far), branch_rank.get(e.src_role, 9),
                       rank.get(e.dst_id, far)))
    data_edges = sorted((e for e in edges if e.kind != 'exec'),
                        key=lambda e: (rank.get(e.src_id, far),
                                       rank.get(e.dst_id, far)))
    jobs = []
    for edge in exec_edges + data_edges:
        jobs.append({'src': edge.src_id, 'dst': edge.dst_id,
                     'kind': edge.kind, 'label': _wire_label(edge)})
        if len(jobs) >= MAX_CONNECTIONS:
            break
    return jobs


def prompt_for(job, snapshot, short, order_text):
    """The whole prompt for ONE wire: two nodes, one wire, one question."""
    src, dst = snapshot.node(job['src']), snapshot.node(job['dst'])
    lines = ['CHAIN ORDER: ' + order_text,
             'SOURCE %s (%s "%s"): %s' % (src.id, src.type,
                                          _clip(src.name, 40),
                                          short.get(src.id, '')),
             'TARGET %s (%s "%s"): %s' % (dst.id, dst.type,
                                          _clip(dst.name, 40),
                                          short.get(dst.id, '')),
             'WIRE: ' + job['label'],
             'In one sentence: what does this wire hand from the source to the '
             'target, and what does the target do with it?']
    return '\n'.join(lines)


def answer_text(reply):
    """One usable sentence from a model reply ('' when there is none)."""
    text = ' '.join(str(reply or '').split())
    text = text.lstrip('-*·>#').strip().strip('"\'“”')
    if ':' in text[:24]:
        head, rest = text.split(':', 1)
        if len(head.split()) <= 3 and rest.strip():
            text = rest.strip()          # "Wire: ..." / "Outcome: ..."
    return _clip(text, MAX_ANSWER)


# --------------------------------------------------------------- assembly

def _summary_text(reply):
    """The paragraph a summary call returned, clipped as a SUMMARY.

    ``answer_text`` clips at one sentence's budget (``MAX_ANSWER``), which cut
    every summary mid-word -- a chain described as "...and finally run…".
    """
    text = ' '.join(str(reply or '').split())
    return _clip(text.lstrip('-*·>#').strip().strip('"\'“”'),
                 _flow.MAX_SUMMARY)


def _cover(snapshot, boxes):
    """Give every node a box: an unboxed node joins the box that uses it.

    A pool or a node the walk never reaches (a context consulted through its
    data ports) used to sit outside every box, so the chain's description had
    a hole where it was.  Serving boxes of their own made a frame across the
    canvas, so the node joins the box of a node it is WIRED to — a pool belongs
    to what it feeds — and only a node wired to nothing gets a box alone.
    """
    placed = {nid for box in boxes for nid in box['nodes']}
    for node in sorted(snapshot.nodes.values(), key=lambda n: n.position):
        if node.id in placed:
            continue
        neighbours = {e.dst_id for e in snapshot.data_out_edges(node.id)}
        neighbours |= {e.src_id for e in snapshot.data_in_edges(node.id)}
        neighbours |= {e.dst_id for e in snapshot.exec_out_edges(node.id)}
        neighbours |= {e.src_id for e in snapshot.exec_in_edges(node.id)}
        host = next((box for box in boxes
                     if set(box['nodes']) & neighbours), None)
        if host is None:
            host = {'title': 'P%d %s' % (len(boxes) + 1,
                                         _clip(node.name or node.id, 32)),
                    'note': '', 'nodes': [],
                    'hue': _flow.HUES[len(boxes) % len(_flow.HUES)]}
            boxes.append(host)
        host['nodes'].append(node.id)
        placed.add(node.id)
    return boxes


def _sentence(text):
    """A wire answer as one clean sentence (capitalised, closed)."""
    text = _clip(text, MAX_ANSWER)
    if not text:
        return ''
    text = text[0].upper() + text[1:]
    return text if text[-1] in '.!?' else text + '.'


def _lower_first(text):
    return text[:1].lower() + text[1:] if text else text


def _section_notes(snapshot, boxes, outcomes):
    """Fill each box's note from the wires that touch it.

    Inside first: the sentences of the wires between the box's OWN members are
    what the stretch does.  Then OUT — every wire that leaves the box, one per
    branch — because that is the outcome a reader needs: a check hands the
    chain down two different paths and a note that keeps only one of them is
    wrong about half of the chain.

    A box with no wire between its members (one node, or a leaf) falls back to
    its members' own sentences: a note that talks only about the NEXT box never
    says what this one holds — that is how a box about "not interested" ended
    up describing the review click after it.
    """
    for box in boxes:
        member_ids = list(box['nodes'])
        members = set(member_ids)
        parts = [_sentence(text)
                 for (src, dst, _kind), text in outcomes.items()
                 if src in members and dst in members]
        if not parts:
            parts = [_sentence(prompt_summary(_describe.does_of(
                snapshot, snapshot.node(nid))))
                for nid in member_ids if snapshot.node(nid) is not None]
        exits = [_sentence(text)
                 for (src, dst, kind), text in outcomes.items()
                 if kind == 'exec' and src in members and dst not in members]
        if exits:
            parts.append('Then ' + _lower_first(exits[0]))
            parts.extend(exits[1:3])
        if parts:
            box['note'] = _clip(' '.join(p for p in parts if p),
                                _flow.MAX_NOTE)
    return boxes


def _summary_prompt(snapshot, boxes, lines, order, order_text):
    """Everything the summary call needs, and only that.

    The box notes are a transcript of the wires; a summary written from them
    alone is a transcript with the connections sanded off.  So this call also
    gets the execution order, one line per node and the wiring's own defects —
    enough to say what the chain is FOR, which is the only thing a reader
    actually wants.  Bounded by ``SUMMARY_NODES_CHARS``, so a long chain still
    fits a small model's window.
    """
    node_lines, used = [], 0
    # The walk skips a pool, and a pool the summary never heard of is a hole in
    # the explanation: the off-walk nodes are listed after the walk order.
    walked = set(order)
    sequence = list(order) + [node.id for node in
                              sorted(snapshot.nodes.values(),
                                     key=lambda n: n.position)
                              if node.id not in walked]
    total = len(sequence)
    for position, node_id in enumerate(sequence):
        node = snapshot.node(node_id)
        if node is None:
            continue
        line = '- %s: %s' % (_clip(node.name or node.type or node.id, 40),
                             prompt_summary(lines.get(node_id, '')))
        if used + len(line) > SUMMARY_NODES_CHARS:
            node_lines.append('- … %d more node(s)' % (total - position))
            break
        node_lines.append(line)
        used += len(line) + 1

    parts = ['CHAIN ORDER: ' + order_text,
             'WHAT EACH NODE DOES:\n' + ('\n'.join(node_lines) or '- (none)'),
             'THE PARTS, IN EXECUTION ORDER:\n' + '\n'.join(
                 '%s — %s' % (box['title'], _clip(box['note'],
                                                  SUMMARY_NOTE_CHARS))
                 for box in boxes)]
    facts = _facts(snapshot)
    if facts:
        parts.append('TOPOLOGY FACTS:\n' + '\n'.join('- ' + f for f in facts))
    parts.append('Write the summary paragraph for this chain.')
    return 'THE CHAIN, IN BOXES:\n' + '\n\n'.join(parts)


def _facts(snapshot):
    """What the wiring itself says: branches, dead ends, illegal wires.

    Without this the summary guesses the strategy ("once the form fills, the
    triggering text appears") instead of reading it: a check hands execution
    down TWO named branches and they decide what the automation does next.
    """
    found = []
    for node_id in _describe.walk(snapshot)[1]:
        node = snapshot.node(node_id)
        if node is None or node.type != 'conditional':
            continue
        exits = []
        for role, word in ((_ports.BRANCH_TRUE, 'true'),
                           (_ports.BRANCH_FALSE, 'false'),
                           (_ports.LOOP_OUT, 'loop')):
            targets = [snapshot.node(e.dst_id) for e in snapshot.edges
                       if e.src_role == role and e.src_id == node_id
                       and e.kind == 'exec']
            names = [_clip(t.name or t.type or t.id, 34) for t in targets if t]
            if role == _ports.LOOP_OUT and not names:
                continue          # not a loop node: silence, not a dead end
            exits.append('%s → %s' % (word, ', '.join(names) if names
                                      else 'nothing wired'))
        found.append('check "%s": %s'
                     % (_clip(node.name or node.type or node.id, 34),
                        '; '.join(exits)))
        if len(found) >= SUMMARY_FACTS:
            break
    _lines, _order, visited, _steps = _describe.walk(snapshot)
    # What the branch list above already says is not repeated as a defect.
    found.extend(_clip(text, 160)
                 for text in _describe.violations(snapshot, visited)
                 if 'has no wire on its' not in text)
    return found[:SUMMARY_FACTS]


def _derived_summary(boxes):
    """The summary without a model: the box notes, in order."""
    return _clip(' '.join('%s: %s' % (box['title'], box['note'])
                          for box in boxes), _flow.MAX_SUMMARY)


def _cap(boxes):
    """At most ``flow.MAX_SECTIONS`` boxes: the tail is merged into the last.

    Storing more than that is not a section list (``flow.normalise`` refuses
    it), and a chain with twenty checkpoints would give twenty boxes — worse
    to read than one long one.
    """
    limit = _flow.MAX_SECTIONS
    if len(boxes) <= limit:
        return boxes
    tail = boxes[limit - 1:]
    merged = dict(tail[0])
    merged['title'] = _clip('%s (+%d more)' % (tail[0]['title'], len(tail) - 1),
                            _flow.MAX_TITLE)
    merged['nodes'] = [nid for box in tail for nid in box['nodes']]
    merged['note'] = _clip(' '.join(box['note'] for box in tail),
                           _flow.MAX_NOTE)
    return boxes[:limit - 1] + [merged]


def describe(snapshot, generate, settings=None, progress=None, cancel=None):
    """Describe *snapshot* wire by wire -> ``{'flow', 'total', 'done', 'failed'}``.

    ``generate(prompt, system, settings)`` is the model call
    (``reasoning.generate``).  A wire the model cannot answer is recorded in
    ``failed`` and the pass CONTINUES — one bad answer must not throw away the
    twenty good ones — but the caller is told exactly how many and which.
    """
    def _say(text):
        if callable(progress):
            progress(text)

    def _stop():
        return bool(callable(cancel) and cancel())

    lines = node_lines(snapshot)
    short = {node_id: prompt_summary(text) for node_id, text in lines.items()}
    order = order_of(snapshot)
    order_text = order_line(snapshot, order)
    jobs = connections(snapshot, order)

    outcomes, failed = {}, []
    for index, job in enumerate(jobs, 1):
        if _stop():
            break
        _say('connection %d/%d — %s' % (index, len(jobs), job['label']))
        try:
            reply = generate(prompt_for(job, snapshot, short, order_text),
                             _SYSTEM, settings)
        except Exception as exc:          # one wire, not the whole pass
            failed.append((job['label'], str(exc) or exc.__class__.__name__))
            continue
        text = answer_text(reply)
        if not text:
            failed.append((job['label'], 'the model answered nothing usable'))
            continue
        outcomes[(job['src'], job['dst'], job['kind'])] = text

    boxes = _cover(snapshot, _flow.sections(snapshot, {}))
    boxes = _section_notes(snapshot, boxes, outcomes)
    boxes = _cap(boxes)
    summary = ''
    if boxes and not _stop():
        _say('summary — %d box(es)' % len(boxes))
        try:
            summary = _summary_text(generate(
                _summary_prompt(snapshot, boxes, lines, order, order_text),
                _SUMMARY_SYSTEM, settings))
        except Exception as exc:
            failed.append(('the chain summary',
                           str(exc) or exc.__class__.__name__))
    if not summary:
        summary = _derived_summary(boxes)

    # Canvas order, not walk order: `flow.complete` reads these the same way.
    by_position = sorted(snapshot.nodes.values(), key=lambda n: n.position)
    flow = {
        'summary': _clip(summary, _flow.MAX_SUMMARY),
        'sections': boxes,
        'steps': [],
        'nodes': [{'node': node.id,
                   'does': _clip(lines[node.id], _flow.MAX_DOES)}
                  for node in by_position],
        'stamp': '',
    }
    return {'flow': flow, 'total': len(jobs), 'done': len(outcomes),
            'failed': failed}
