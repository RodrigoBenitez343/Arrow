"""Describing a chain ONE CONNECTION AT A TIME.

A whole-canvas prompt does not fit a small local model, and the point of the
staged pass is that nothing has to fit: nodes are read from their own settings,
and the model is asked one wire at a time — source node, target node, ports.

These tests pin the contract:

* one model call per wire (plus one for the summary), never one for the chain;
* every wire prompt is small and carries exactly one WIRE line;
* every node still gets its sentence, with no model at all;
* a box's note is the sentences of the wires inside it;
* an unanswered wire is reported, and the rest of the pass still lands.

Run:  python -m pytest LoOper/tests/test_builder_staged_describe.py
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


def _chain(count=4):
    """A linear chain of *count* input nodes, wired exec -> exec."""
    from player.builder import ports
    from player.builder.snapshot import Edge, NodeView, PortView, Snapshot

    nodes, edges = {}, []
    for index in range(count):
        node_id = '0x%02d' % index
        nodes[node_id] = NodeView(
            id=node_id, type='input', name='step %d' % index, role='VALUE',
            position=(0.0, float(index * 100)),
            properties={'label': 'value %d' % index, 'passthrough': True},
            inputs=[PortView('input', 'in', ports.EXEC_IN, 'run', [])],
            outputs=[PortView('output', 'out', ports.EXEC_OUT, 'hand on', [])],
        )
        if index:
            edges.append(Edge(src_id='0x%02d' % (index - 1), src_port='output',
                              src_role=ports.EXEC_OUT, dst_id=node_id,
                              dst_port='input', dst_role=ports.EXEC_IN,
                              kind='exec'))
    return Snapshot(nodes=nodes, edges=edges, order=list(nodes))


class _Model:
    """A stand-in for the model: records every prompt, answers in turn."""

    def __init__(self, fail_on=None):
        self.prompts = []
        self.systems = []
        self._fail_on = fail_on or ()

    def __call__(self, prompt, system, settings=None):
        self.prompts.append(prompt)
        self.systems.append(system)
        for needle in self._fail_on:
            if needle in prompt:
                raise RuntimeError('gateway is down')
        return 'Outcome %d of the wire.' % len(self.prompts)


def test_one_model_call_per_connection_never_one_for_the_chain():
    from player.builder import staged

    snap = _chain(4)                      # 3 execution wires
    model = _Model()
    result = staged.describe(snap, model)

    assert result['total'] == 3
    assert result['done'] == 3
    assert result['failed'] == []
    # 3 wires + 1 summary. A whole-chain prompt would have been ONE call.
    assert len(model.prompts) == 4
    assert 'THE CHAIN, IN BOXES' in model.prompts[-1]

    for prompt in model.prompts[:-1]:
        assert prompt.count('WIRE:') == 1, prompt
        assert prompt.count('SOURCE ') == 1 and prompt.count('TARGET ') == 1
        # Small on purpose: two node summaries and the wire between them.
        assert len(prompt) < 1200, len(prompt)


def test_every_node_says_what_it_does_with_no_model_at_all():
    from player.builder import staged

    snap = _chain(4)
    result = staged.describe(snap, _Model())

    described = {entry['node']: entry['does'] for entry in result['flow']['nodes']}
    assert set(described) == set(snap.nodes)
    assert all(text for text in described.values()), described
    assert 'value 2' in described['0x02'], described['0x02']


def test_a_box_note_is_the_wires_that_run_inside_it():
    """The boxes come from the topology; their BODY is the connection
    sentences, which is the whole point of asking per wire."""
    from player.builder import staged

    snap = _chain(4)
    result = staged.describe(snap, _Model())
    boxes = result['flow']['sections']

    assert boxes, 'a chain always gets its boxes'
    covered = [nid for box in boxes for nid in box['nodes']]
    assert set(covered) == set(snap.nodes)
    for box in boxes:
        assert box['note'].startswith('Outcome '), box['note']
    # The hand-off to the next box is part of what this one produces.
    assert 'Outcome 3 of the wire.' in boxes[-1]['note'] or \
        len(boxes) == 1, boxes[-1]['note']


def test_the_summary_gets_the_whole_chain_not_only_the_boxes():
    """A summary built from the box notes alone recites them: it clicks Apply,
    then it fills the form… and never says what the automation is FOR.  The
    call gets the order, one line per node and the wiring's own defects."""
    from player.builder import staged

    model = _Model()
    result = staged.describe(_chain(4), model)
    prompt = model.prompts[-1]

    assert 'CHAIN ORDER: ' in prompt
    assert 'WHAT EACH NODE DOES:' in prompt
    assert 'step 2' in prompt, prompt          # a per-node line, not just a box
    assert 'Outcome 1 of the wire.' in prompt  # ...and the box notes
    assert result['flow']['summary'] == 'Outcome 4 of the wire.'
    # Still a small prompt: 4 wires' worth of boxes, no canvas dump.
    assert len(prompt) < 6000, len(prompt)


def test_an_unanswered_wire_is_reported_and_the_pass_continues():
    from player.builder import staged

    snap = _chain(4)
    model = _Model(fail_on=('0x01.out',))
    result = staged.describe(snap, model)

    assert result['done'] == 2, result
    assert len(result['failed']) == 1
    label, error = result['failed'][0]
    assert '0x01' in label and 'gateway is down' in error
    # The nodes are still described, and the boxes still exist.
    assert {entry['node'] for entry in result['flow']['nodes']} == set(snap.nodes)
    assert result['flow']['sections']


def test_the_produced_flow_is_valid_metadata():
    """What the pass hands the panel must survive the same validation a model
    answer goes through, or the chain would be left undescribed."""
    from player.builder import flow as bflow
    from player.builder import staged

    metadata = bflow.normalise(staged.describe(_chain(4), _Model())['flow'])

    assert metadata['summary']
    assert metadata['sections'] and metadata['nodes']
    assert len(metadata['sections']) <= bflow.MAX_SECTIONS


def test_cancelling_stops_the_pass_between_wires():
    from player.builder import staged

    model = _Model()
    seen = {'calls': 0}

    def _cancel():
        return seen['calls'] >= 1

    def _generate(prompt, system, settings=None):
        seen['calls'] += 1
        return model(prompt, system, settings)

    result = staged.describe(_chain(4), _generate, cancel=_cancel)
    assert len(model.prompts) == 1, model.prompts
    assert result['done'] == 1
    assert result['flow']['nodes'], 'the node pass runs before any model call'
