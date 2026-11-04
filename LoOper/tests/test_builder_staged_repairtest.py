"""What the staged pass got WRONG on a real chain (repairtest.json).

Field report: the description "deviated from the actual goal" and the boxes
"did not mention their own nodes", whatever the model size.  Three defects, all
deterministic — they were in the assembly, not in the model:

* a web check's sentence was the raw LOCATOR JSON, so the node read as CSS and
  every wire prompt fed the model a selector instead of a condition;
* an OUTPUT whose execution port loops back was described as "reports the
  chain's result", so a repair loop read as a finished report;
* a box's note was the wire leaving it (a box that only had an exit described
  the NEXT box), and the pool consulted through data ports sat in no box.

Run:  python -m pytest LoOper/tests/test_builder_staged_repairtest.py
"""

import json
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


# ------------------------------------------------ what a node says it does

def _conditional(props):
    return SimpleNamespace(type='conditional', name='Web Conditional',
                           properties=dict(props))


def test_a_web_check_names_the_condition_and_the_element_not_the_locator():
    locator = json.dumps({'ancestor_css': 'html:nth-of-type(1) > body > div',
                          'css': 'div#root > dialog.dj9me5',
                          'classes': ['dj9l9a'], 'text': 'Review',
                          'tag': 'span', 'xpath': '/html[1]/body[1]'})
    text = describe_acting({
        'condition_type': 'web', 'web_condition_type': 'element_located',
        'web_element_locator': locator})

    # What is EVALUATED, in words: the element's own label, on the page.
    assert 'checks that the element "Review" is on the page' in text, text
    assert 'ancestor_css' not in text and '{' not in text, text


def test_a_web_check_still_reads_a_plain_text_target():
    text = describe_acting({'condition_type': 'web',
                            'web_condition_type': 'text_present',
                            'web_element_locator': '',
                            'web_target_text': 'your application was sent'})
    assert ('checks that the page shows the text "your application was sent"'
            in text), text


def describe_acting(props):
    from player.builder import describe

    return describe.acting(_conditional(props))


# ------------------------------------------------ a loop marker is not an end

def _wire_loop():
    """input -> output -> form filler: the output LOOPS the chain back."""
    from player.builder import ports
    from player.builder.snapshot import Edge, NodeView, PortView, Snapshot

    def node(nid, ntype, name, props=None):
        return NodeView(id=nid, type=ntype, name=name, role={'output': 'REPORT'}
                        .get(ntype, 'ACT'), position=(0.0, 0.0),
                        properties=dict(props or {}),
                        inputs=[PortView('input', 'in', ports.EXEC_IN, '', [])],
                        outputs=[PortView('output', 'out', ports.EXEC_OUT,
                                          '', [])])

    nodes = {'0x1': node('0x1', 'web_sequence', 'apply'),
             '0x2': node('0x2', 'output', 'report', {'label': 'output'}),
             '0x3': node('0x3', 'form_filler', '')}
    edges = [
        Edge('0x1', 'output', ports.EXEC_OUT, '0x2', 'input', ports.EXEC_IN,
             'exec'),
        Edge('0x2', 'output', ports.EXEC_OUT, '0x3', 'input', ports.EXEC_IN,
             'exec'),
    ]
    return Snapshot(nodes=nodes, edges=edges, order=list(nodes))


def test_an_output_that_loops_back_is_not_described_as_the_end_of_the_chain():
    from player.builder import describe

    snap = _wire_loop()
    text = describe.does_of(snap, snap.node('0x2'))

    assert 'does not end here' in text, text
    assert 'form_filler' in text, 'it names where execution goes instead'


def test_a_plain_output_is_still_the_report():
    from player.builder import describe
    from player.builder.snapshot import Snapshot

    snap = _wire_loop()
    terminal = Snapshot(nodes=dict(snap.nodes),
                        edges=[e for e in snap.edges if e.src_id != '0x2'],
                        order=list(snap.nodes))
    text = describe.does_of(terminal, terminal.node('0x2'))
    assert 'does not end here' not in text, text


# ------------------------------------------------------- boxes and notes

def _chain_with_pool():
    """input -> check; a memory pool feeds the input through a data port."""
    from player.builder import ports
    from player.builder.snapshot import Edge, NodeView, PortView, Snapshot

    def node(nid, ntype, name, role):
        return NodeView(id=nid, type=ntype, name=name, role=role,
                        position=(0.0, 0.0), properties={'label': nid},
                        inputs=[], outputs=[])

    nodes = {'0x1': node('0x1', 'input', 'ask', 'VALUE'),
             '0x2': node('0x2', 'conditional', 'check', 'CHECK'),
             '0x9': node('0x9', 'context', 'user information', 'POOL')}
    edges = [
        Edge('0x1', 'output', ports.EXEC_OUT, '0x2', 'input', ports.EXEC_IN,
             'exec'),
        Edge('0x9', 'ctx_out', ports.CONTEXT_OUT, '0x1', 'ctx_in',
             ports.CONTEXT_IN, 'data'),
    ]
    nodes['0x1'].inputs.append(PortView('ctx_in', 'in', ports.CONTEXT_IN, '',
                                        []))
    nodes['0x1'].outputs.append(PortView('output', 'out', ports.EXEC_OUT, '',
                                         []))
    nodes['0x2'].inputs.append(PortView('input', 'in', ports.EXEC_IN, '', []))
    nodes['0x9'].outputs.append(PortView('ctx_out', 'out', ports.CONTEXT_OUT,
                                         '', []))
    return Snapshot(nodes=nodes, edges=edges, order=list(nodes))


class _Model:
    def __init__(self, summary='A short summary.'):
        self._summary = summary

    def __call__(self, prompt, system, settings=None):
        if 'THE CHAIN, IN BOXES' in prompt:
            return self._summary
        return 'The ask is handed to the check, which branches.'


def test_a_node_the_walk_never_reaches_joins_the_box_that_uses_it():
    from player.builder import staged

    boxes = staged.describe(_chain_with_pool(), _Model())['flow']['sections']
    covered = {nid for box in boxes for nid in box['nodes']}

    assert covered == {'0x1', '0x2', '0x9'}, 'the pool is in a box, not outside'
    assert any('0x9' in box['nodes'] and '0x1' in box['nodes']
               for box in boxes), 'it joins the node it feeds'


def test_a_box_with_no_internal_wire_says_what_its_own_nodes_do():
    """The "not interested" box used to describe the review click that came
    AFTER it, because its only sentence was the wire leaving the box."""
    from player.builder import staged

    result = staged.describe(_chain_with_pool(), _Model())
    for box in result['flow']['sections']:
        assert box['note'].strip(), box['title']
        assert not box['note'].lower().startswith('then '), box['note']
        assert 'Then The' not in box['note'], box['note']


def test_the_summary_is_not_clipped_to_one_sentence():
    """A 600-char summary was cut at MAX_ANSWER (400) mid-word."""
    from player.builder import staged

    long_summary = 'The chain applies to a job. ' * 24
    result = staged.describe(_chain_with_pool(), _Model(long_summary))

    assert len(result['flow']['summary']) > 400, result['flow']['summary']
    assert result['flow']['summary'].rstrip().endswith('.')


def test_a_recording_url_is_not_what_the_model_reads():
    """The URL a recording opens ate the whole prompt budget, so the model
    described a query string instead of an action."""
    from player.builder import staged

    text = ('replays the web recording "apply.json" — 1 action(s) — '
            'kinds: click x1 — opens: https://www.linkedin.com/jobs/view/1?'
            'a=b&c=d')
    summary = staged.prompt_summary(text)
    assert 'linkedin.com' not in summary, summary
    assert 'click x1' in summary, summary
