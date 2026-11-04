"""The Builder turn must FIT the model, not merely fit per-part caps.

Field failure: asking the Builder "show me the flow of this chain" ended with
the provider's 400 — ``Please reduce the length of the messages`` — from an 8k
Groq model.  Every block was inside its own ceiling and their SUM was 34.4k
characters (system 14.2k + digest 9k + index 4k + selection 5k + history 2.2k).

So: the system prompt is compact, and the turn is clipped as a WHOLE, by
importance (the digest answers the question; the chat tail is a courtesy).

Run:  python -m pytest LoOper/tests/test_builder_prompt_budget.py
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def assistant():
    from player.builder import assistant as module

    return module


def test_the_system_prompt_fits_a_small_model(assistant):
    prompt = assistant._system_prompt()

    # 14.2k before: role 7k + protocol 2k + registry 5.2k.
    assert len(prompt) < 10000, len(prompt)


def test_a_turn_is_clipped_as_a_whole(assistant):
    turn = assistant._format_turn(
        query='show me the flow of this chain',
        digest='NODE CONTENT\n' + ('node line; ' * 3000),      # ~30k
        index='port line; ' * 3000,                            # ~30k
        selection='selected; ' * 3000,                         # ~27k
        history=[{'role': 'user', 'text': 'earlier question ' * 300}],
        results=['tool result ' * 2000],
        flow_text='FLOW METADATA\n' + ('stored; ' * 2000))

    assert len(turn) <= assistant.PROMPT_TURN_CHARS + 200, len(turn)
    # The question always survives, and it is always last.
    assert turn.rstrip().endswith('show me the flow of this chain'), turn[-200:]
    # The evidence that answers it is served first.
    assert turn.startswith('RECENT CHAT') or 'CHAIN TOPOLOGY' in turn[:400]


def test_the_digest_wins_the_budget_over_the_chat_tail(assistant):
    turn = assistant._format_turn(
        query='what does this chain do?',
        digest='CHAIN TOPOLOGY HEAD ' + ('walk; ' * 3000),
        index='index; ' * 1000,
        selection='selection; ' * 1000,
        history=[{'role': 'user', 'text': 'an old question ' * 200}],
        results=[])

    assert 'CHAIN TOPOLOGY HEAD' in turn
    assert 'cut to fit the model' in turn, 'the cut is stated, never silent'
    # The chat tail is the first thing to go.
    assert 'an old question' not in turn, turn[:300]


def test_a_normal_turn_is_untouched(assistant):
    turn = assistant._format_turn(
        query='what does the check decide?',
        digest='EXECUTION WALK\n  step 0x1 -> 0x2',
        index='0x1 input: out(exec_out)',
        selection='0x1 input "ask"',
        history=[{'role': 'user', 'text': 'hi'}],
        results=[])

    assert 'cut to fit the model' not in turn
    assert 'EXECUTION WALK' in turn and '0x1 input: out' in turn
    assert 'SELECTED NODES' in turn and 'RECENT CHAT' in turn


def test_a_context_length_rejection_is_not_advice_to_buy_a_bigger_model():
    """Arrow is deterministic-first: the answer must not depend on the model
    being big.  The error says what happened, never "pick a bigger model"."""
    from player.builder import reasoning
    from player.builder.errors import BuilderModelError

    class _Response:
        status_code = 502
        text = ('{"detail":"Groq request failed: HTTP 400: '
                '{\\"error\\":{\\"code\\":\\"context_length_exceeded\\"}}"}}')

    with pytest.raises(BuilderModelError) as err:
        reasoning.raise_for_status(_Response(), 'http://localhost/generate_stream')

    message = str(err.value)
    assert "bigger than this model's context window" in message
    assert 'stored description' in message
    assert 'larger window' not in message and 'bigger model' not in message


def test_a_prose_answer_is_still_an_answer():
    """A small model that cannot wrap its reply in JSON still answered."""
    from player.builder import assistant

    reply, actions, flow_obj, walk = assistant.extract_turn(
        'The chain clicks Easy Apply, fills the form and submits it.')

    assert reply == 'The chain clicks Easy Apply, fills the form and submits it.'
    assert actions == [] and flow_obj == {} and walk is False


def test_prose_with_a_fence_comes_out_clean():
    from player.builder import assistant

    reply, _actions, _flow, _walk = assistant.extract_turn(
        '```\nIt asks, then checks.\n```')

    assert reply == 'It asks, then checks.'


def test_the_json_envelope_still_wins_when_the_model_gives_one():
    from player.builder import assistant

    reply, actions, flow_obj, walk = assistant.extract_turn(
        '{"reply": "done", "actions": [{"name": "read_node", '
        '"arguments": {"node": "0x1"}}], "walk": true}')

    assert reply == 'done' and actions[0]['name'] == 'read_node'
    # The highlight is the walk button's job; a turn does not trigger it.
    assert walk is False


def test_any_other_rejection_still_carries_the_body():
    from player.builder import reasoning
    from player.builder.errors import BuilderModelError

    class _Response:
        status_code = 502
        text = '{"detail":"the provider key is wrong"}'

    with pytest.raises(BuilderModelError) as err:
        reasoning.raise_for_status(_Response(), 'http://localhost/generate_stream')

    assert 'the provider key is wrong' in str(err.value)


def test_the_registry_no_longer_carries_authoring_advice():
    from player.builder import runbook

    registry = runbook.capability_registry()

    assert len(registry) < 3000, len(registry)
    assert 'WHEN:' not in registry and 'MODES:' not in registry
    # ...but the reading facts stay.
    assert 'in(' in registry and 'wire' in registry.lower()


# ------------------------------------- answering instead of re-describing

def _chain():
    """input -> conditional, with a memory pool feeding the input."""
    from player.builder import ports
    from player.builder.snapshot import Edge, NodeView, PortView, Snapshot

    def node(nid, ntype, name, role):
        return NodeView(id=nid, type=ntype, name=name, role=role,
                        position=(0.0, 0.0), properties={'label': nid},
                        inputs=[], outputs=[])

    nodes = {'0x1': node('0x1', 'input', 'ask', 'VALUE'),
             '0x2': node('0x2', 'conditional', 'check', 'CHECK')}
    nodes['0x1'].outputs.append(PortView('output', 'out', ports.EXEC_OUT,
                                         '', []))
    nodes['0x2'].inputs.append(PortView('input', 'in', ports.EXEC_IN, '', []))
    edges = [Edge('0x1', 'output', ports.EXEC_OUT, '0x2', 'input',
                  ports.EXEC_IN, 'exec')]
    return Snapshot(nodes=nodes, edges=edges, order=list(nodes))


STORED = {
    'summary': 'Asks for a topic and checks it.',
    'sections': [{'title': 'P1 ask → check', 'note': 'The user is asked, then '
                  'the answer is checked.', 'nodes': ['0x1', '0x2'],
                  'hue': '#22D3EE'}],
    'nodes': [{'node': '0x1', 'does': 'long per-node sentence'},
              {'node': '0x2', 'does': 'another long sentence'}],
    'steps': [],
}


def test_an_undescribed_chain_omits_only_the_node_lines():
    from player.builder import flow

    text = flow.digest(STORED, include_nodes=False)

    assert 'SUMMARY: Asks for a topic' in text
    assert 'the answer is checked' in text
    assert 'NODES (' not in text and 'long per-node sentence' not in text


def test_the_topology_block_carries_order_and_branches_but_no_content():
    from player.builder import describe

    text = describe.structure(_chain())

    assert 'EXECUTION WALK' in text
    assert 'BRANCHES' in text
    assert 'NODE CONTENT' not in text


def test_answering_does_not_carry_the_authoring_instructions():
    """Describing is the Describe button's job: the chat prompt teaches no
    metadata format and never asks for a 'flow' object."""
    from player.builder import assistant

    system = assistant._system_prompt()

    assert 'FLOW METADATA' not in system
    assert "'flow'" in system, 'it says explicitly that flow is not the turn’s'
    assert 'Describe button' in system
    assert 'FLOW METADATA — the chain describes itself' not in system


def _route_calls(monkeypatch, stored, fail_calls=0):
    """Run one chat turn against *stored* and report what the model was sent.

    ``fail_calls`` is how many model calls reject for context length before the
    fake answers (use 99 to make every call refuse).
    """
    from player.builder import assistant, reasoning, tools

    snap = _chain()
    seen = []

    monkeypatch.setattr(tools, 'read_snapshot', lambda ctx: snap)
    monkeypatch.setattr(tools, 'stored_flow', lambda ctx: dict(stored))
    monkeypatch.setattr(tools, 'read_graph',
                        lambda ctx, limit=0: 'CANVAS INDEX SENTINEL')
    monkeypatch.setattr(tools, 'selection_text',
                        lambda ctx, snapshot=None, limit=0: '')

    def _generate(prompt, system, settings, on_delta=None, cancel=None):
        seen.append({'prompt': prompt, 'system': system})
        if len(seen) <= fail_calls:
            raise reasoning.BuilderModelError(
                "the prompt is bigger than this model's context window")
        return '{"reply": "it asks, then checks"}'

    monkeypatch.setattr(reasoning, 'generate', _generate)
    assistant.BuilderAssistant({'stop': lambda: False, 'history': []},
                               {'model': 'test'}).route('how does it flow?')
    return seen


def test_a_described_chain_is_answered_from_its_metadata(monkeypatch):
    seen = _route_calls(monkeypatch, STORED)

    prompt, system = seen[0]['prompt'], seen[0]['system']
    # The chain's own words, not a canvas dump.
    assert 'Asks for a topic and checks it.' in prompt
    assert 'the answer is checked' in prompt
    assert 'long per-node sentence' not in prompt, 'read_node does that'
    assert 'CANVAS INDEX SENTINEL' not in prompt
    assert 'EXECUTION WALK' in prompt
    # ...and nothing invites it to write the description again.
    assert 'FLOW METADATA — the chain describes itself' not in system
    assert 'Describe button' in system


def test_an_undescribed_chain_still_reads_the_canvas(monkeypatch):
    seen = _route_calls(monkeypatch, {})

    prompt, system = seen[0]['prompt'], seen[0]['system']
    assert 'CANVAS INDEX SENTINEL' in prompt, 'nothing else to answer from'
    # ...but the turn still cannot write a description of it.
    assert 'Describe button' in system
    assert 'FLOW METADATA — the chain describes itself' not in system


def test_a_window_that_is_too_small_gets_one_smaller_retry(monkeypatch):
    """A described chain can still be answered from its goal and boxes, so a
    model that refuses the full turn is asked something smaller — once."""
    from player.builder import assistant

    stored = dict(STORED, summary='Asks for a topic and checks it.')
    monkeypatch.setattr(assistant, 'PROMPT_STRUCTURE_CHARS', 2500)
    seen = _route_calls(monkeypatch, stored, fail_calls=1)

    assert len(seen) == 2, 'exactly one retry'
    assert 'Asks for a topic and checks it.' in seen[1]['prompt'], 'goal survives'
    assert 'EXECUTION WALK' not in seen[1]['prompt'], 'the topology block goes'
    assert len(seen[1]['system']) < len(seen[0]['system']), 'registry dropped'


def test_a_model_that_can_hold_nothing_still_gets_an_answer(monkeypatch):
    """Deterministic-first: the chain's own description answers, no model."""
    from player.builder import assistant

    seen = _route_calls(monkeypatch, STORED, fail_calls=99)
    assert len(seen) == 2, 'the small retry was tried once'

    result = assistant.BuilderAssistant(
        {'stop': lambda: False, 'history': []},
        {'model': 'test'}).route('how does it flow?')

    reply = result['reply']
    assert 'Asks for a topic and checks it.' in reply, reply
    assert 'The user is asked, then the answer is checked.' in reply, reply
    assert result['flow'] == {}, 'a question does not rewrite the description'
    assert result['walk'] is False


def test_an_undescribed_chain_falls_back_to_its_shape(monkeypatch):
    from player.builder import assistant

    _route_calls(monkeypatch, {}, fail_calls=99)
    result = assistant.BuilderAssistant(
        {'stop': lambda: False, 'history': []},
        {'model': 'test'}).route('how does it flow?')

    assert 'no stored description yet' in result['reply']
    assert 'EXECUTION WALK' in result['reply']
