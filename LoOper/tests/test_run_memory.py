"""run_memory — linear timeline, scopes (agent vs manual), day ranges, chat.

Run:  python -m pytest LoOper/tests/test_run_memory.py
"""

import os
from datetime import datetime

import pytest

import player.agentic_ops.run_memory as rm


def _reset_thread_state():
    """Drop the thread-local conn/stack so the next call binds to the env DB."""
    try:
        conn = getattr(rm._local, 'conn', None)
        if conn is not None:
            conn.close()
    except Exception:
        pass
    for attr in ('conn', 'path', 'run_stack'):
        try:
            delattr(rm._local, attr)
        except Exception:
            pass


@pytest.fixture()
def mem(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOPER_RUN_MEMORY", "on")
    monkeypatch.setenv("LOOPER_RUN_MEMORY_DB", str(tmp_path / "mem.db"))
    _reset_thread_state()
    yield rm
    _reset_thread_state()


# --------------------------------------------------------------- kill switch

def test_kill_switch_blocks_writes(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOPER_RUN_MEMORY", "off")
    db = str(tmp_path / "mem.db")
    monkeypatch.setenv("LOOPER_RUN_MEMORY_DB", db)
    _reset_thread_state()
    try:
        rm.record('chat', 'User: hi')
        assert rm.timeline() == ""
        assert not os.path.exists(db)
    finally:
        _reset_thread_state()


# ------------------------------------------------------------------ capture

def test_run_capture_order_and_scopes(mem):
    run_a = mem.begin_run('/x/linkedin.json', source='agent',
                          query='open linkedin')
    mem.record_node(chain_id='linkedin', node_id='n1', node_type='input',
                    label='Search', ports={'data': 'easy apply'})
    mem.record_node(chain_id='linkedin', node_id='n2', node_type='form_filler',
                    label='Form', ports={'output': '{"name": "John"}'})
    mem.end_run(run_a, ok=True)

    run_m = mem.begin_run('/x/jobsearch.json', source='play')
    mem.record_node(chain_id='jobsearch', node_id='m1', node_type='code',
                    label='Parse', ports={'output': 'parsed 3 postings'})
    mem.end_run(run_m, ok=False)

    out = mem.timeline()
    assert 'RUN_START' in out and 'RUN_END' in out
    assert '[agent]' in out and '[manual]' in out
    assert 'INPUTS' in out and '[Search]' in out and 'data=easy apply' in out
    assert 'FORM' in out and 'name' in out
    assert 'finished: ok' in out and 'finished: failed' in out
    # The linear order: run start, its node, run end.
    assert out.index('RUN_START') < out.index('INPUTS') < out.index('RUN_END')

    # Manual vs agent separation on the shared timeline.
    agent_out = mem.timeline(scope='agent')
    manual_out = mem.timeline(scope='manual')
    assert 'linkedin' in agent_out and 'jobsearch' not in agent_out
    assert 'jobsearch' in manual_out and 'linkedin' not in manual_out


def test_node_outside_run_is_ignored(mem):
    mem.record_node(chain_id='x', node_id='n', node_type='llm',
                    ports={'output': 'hi'})
    assert mem.timeline() == ''


def test_conditional_branch_recorded(mem):
    run_id = mem.begin_run('/x/branch.json', source='agent')
    mem.record_node(chain_id='branch', node_id='c1', node_type='conditional',
                    label='Check', ports={'output': True},
                    conditional_result=False)
    mem.end_run(run_id, ok=True)
    cond = mem.query(category='condition')
    assert '[Check]' in cond and 'branch=false' in cond
    # Sibling nodes on the skip path simply have no events — absence is the
    # honest record (never executed != returned false).
    assert 'never_ran_node' not in mem.timeline()


def test_chat_records_strip_goal_id(mem):
    mem.record_chat('hi', 'done<!-- goal_id:abc123 -->')
    out = mem.timeline()
    assert 'goal_id' not in out
    assert 'User: hi' in out and 'Agent: done' in out


def test_chat_links_to_the_run_it_produced(mem):
    """A chat turn triggered by an agent run is linked to it (verified
    against the run's recorded request) — the graph draws chat -> run."""
    rid = mem.begin_run('/x/ORCHESTRATOR.json', source='agent',
                        query='open linkedin and tell me what you see')
    mem.record_node(chain_id='ORCHESTRATOR', node_id='o1',
                    node_type='orchestrator', ports={'output': 'done'})
    mem.end_run(rid, ok=True)
    mem.record_chat('open linkedin and tell me what you see', 'LinkedIn is open.')

    data = mem.graph_data()
    chat_nodes = [n for n in data['nodes'] if n['kind'] == 'chat']
    assert chat_nodes and all(n['run'] == rid for n in chat_nodes)
    assert any(e['kind'] == 'chat-run' and e['target'] == f'run:{rid}'
               for e in data['edges'])


def test_chat_link_is_query_verified(mem):
    """A turn whose query does not match the recorded run request never
    links (a stale marker must not fabricate a relationship), and each turn
    consumes the marker at most once."""
    rid = mem.begin_run('/x/ORCHESTRATOR.json', source='agent',
                        query='the real request')
    mem.end_run(rid, ok=True)
    # Different query -> no link, and the marker is consumed either way.
    mem.record_chat('some other question', 'answer')
    data = mem.graph_data()
    assert not any(e['kind'] == 'chat-run' for e in data['edges'])
    # The matching query arriving later must NOT reuse the stale run.
    mem.record_chat('the real request', 'late answer')
    data = mem.graph_data()
    assert not any(e['kind'] == 'chat-run' for e in data['edges'])


def test_static_output_text_recorded_even_with_empty_output(mem):
    """Predefined output text (dead-end / error markers) lands in memory
    even when the collected output value is empty (no upstream data)."""
    run_id = mem.begin_run('/x/deadend.json', source='agent')
    mem.record_node(
        chain_id='deadend', node_id='o1', node_type='output',
        label='Dead end',
        ports={'output': '', 'output_meta': {
            'static_text': 'DEAD END: login failed',
            'spoken_text': 'DEAD END: login failed',
        }},
    )
    mem.end_run(run_id, ok=False)
    out = mem.timeline()
    assert 'note=DEAD END: login failed' in out


def test_static_output_text_recorded_alongside_collected_value(mem):
    run_id = mem.begin_run('/x/err.json', source='agent')
    mem.record_node(
        chain_id='err', node_id='o2', node_type='output', label='Error',
        ports={'output': 'partial data collected', 'output_meta': {
            'static_text': 'ERROR: element not found',
            'spoken_text': 'ERROR: element not found',
        }},
    )
    mem.end_run(run_id, ok=True)
    out = mem.timeline()
    assert 'output=partial data collected' in out
    assert 'note=ERROR: element not found' in out


def test_no_note_when_text_is_just_the_collected_output(mem):
    run_id = mem.begin_run('/x/plain.json', source='agent')
    mem.record_node(
        chain_id='plain', node_id='o3', node_type='output', label='Plain',
        ports={'output': 'collected', 'output_meta': {
            'static_text': '', 'spoken_text': 'collected',
        }},
    )
    mem.end_run(run_id, ok=True)
    out = mem.timeline()
    assert 'note=' not in out


# ------------------------------------------------------------- day ranges

def test_resolve_range_keywords():
    ref = datetime(2026, 9, 22, 15, 30)
    s, e = rm.resolve_range('yesterday', ref=ref)
    assert datetime.fromtimestamp(s) == datetime(2026, 9, 21, 0, 0)
    assert datetime.fromtimestamp(e) == datetime(2026, 9, 22, 0, 0)
    s, e = rm.resolve_range('today', ref=ref)
    assert datetime.fromtimestamp(s) == datetime(2026, 9, 22, 0, 0)
    s, e = rm.resolve_range('2026-09-20', ref=ref)
    assert datetime.fromtimestamp(s) == datetime(2026, 9, 20, 0, 0)
    assert rm.resolve_range('', ref=ref) == (None, None)


def test_range_from_question():
    ref = datetime(2026, 9, 22, 15, 30)
    s, _e = rm.range_from_question('what did we do yesterday?', ref=ref)
    assert datetime.fromtimestamp(s) == datetime(2026, 9, 21, 0, 0)
    assert rm.range_from_question('what was filled today', ref=ref)[0] is not None
    assert rm.range_from_question('summarize everything', ref=ref) == (None, None)


def test_timeline_window_filters_by_timestamp(mem):
    conn = mem._conn()
    conn.execute(
        "INSERT INTO events (ts, kind, text, category, scope) VALUES (?,?,?,?,?)",
        (datetime(2026, 9, 21, 10, 0).timestamp(), 'chat', 'old event',
         'chat', 'agent'))
    conn.commit()
    in_window = mem.timeline(since=datetime(2026, 9, 21, 0, 0),
                             until=datetime(2026, 9, 22, 0, 0))
    assert 'old event' in in_window
    out_window = mem.timeline(since=datetime(2026, 9, 22, 0, 0),
                              until=datetime(2026, 9, 23, 0, 0))
    assert 'old event' not in out_window


# ------------------------------------------------------------- memory chat

def test_answer_question_uses_records_and_model(mem, monkeypatch):
    run_id = mem.begin_run('/x/linkedin.json', source='agent',
                           query='open linkedin')
    mem.record_node(chain_id='linkedin', node_id='n1', node_type='output',
                    label='Result', ports={'output': '1 posting found'})
    mem.end_run(run_id, ok=True)
    mem.record_chat('open linkedin', 'done — 1 posting found')

    captured = {}

    def _fake_call(prompt, system, model, max_tokens=220):
        captured['prompt'] = prompt
        return 'Yesterday you opened LinkedIn and 1 posting was found.'

    import player.agentic_ops.description_repair as dr
    monkeypatch.setattr(dr, '_call_local_model', _fake_call)

    out = mem.answer_question('what did the agent do?')
    assert 'opened LinkedIn' in out
    assert 'ACTIVITY RECORD' in captured['prompt']
    assert '1 posting found' in captured['prompt']
    assert 'Current local time' in captured['prompt']


def test_answer_question_no_records(mem):
    assert mem.answer_question('what have we done?') == "No recorded activity yet."


def test_answer_question_empty_window_is_honest(mem, monkeypatch):
    mem.record('chat', 'User: hello', category='chat', scope='agent')
    import player.agentic_ops.description_repair as dr
    monkeypatch.setattr(
        dr, '_call_local_model',
        lambda *a, **k: pytest.fail('model must not be called for empty windows'),
    )
    out = mem.answer_question('what happened on 2020-01-01?')
    assert 'No activity recorded in that period' in out
    assert 'hello' in out  # recent records shown instead of hallucinating


def test_answer_question_model_down_returns_records(mem, monkeypatch):
    mem.record('chat', 'User: status', category='chat', scope='agent')
    import player.agentic_ops.description_repair as dr

    def _boom(*a, **k):
        raise RuntimeError('api down')

    monkeypatch.setattr(dr, '_call_local_model', _boom)
    out = mem.answer_question('status?')
    assert 'Local model unavailable' in out
    assert 'status' in out


# ------------------------------------------------------------ memory graph

def test_graph_data_shapes(tmp_path, mem):
    """Graph projection: day groups runs + chats; run->chain edges mark the
    chains that actually executed inside the run."""
    run_id = mem.begin_run('/x/orchestrator.json', source='agent',
                           query='open linkedin and look')
    mem.record_node(chain_id='orchestrator', node_id='o1',
                    node_type='orchestrator', label='Route',
                    ports={'output': 'final'})
    # A brain executed inside the same run on the timeline.
    mem.record_node(chain_id='linkedin_system_chain', node_id='b1',
                    node_type='chain_import', label='linkedin',
                    ports={'output': 'opened'})
    mem.end_run(run_id, ok=True)
    mem.record_chat('open linkedin', 'done')

    data = mem.graph_data()
    kinds = {n['kind'] for n in data['nodes']}
    assert kinds == {'day', 'run', 'chain', 'chat'}

    # run node carries its root chain, status, duration, inner chains and
    # the request that started it (memory: which question drove the run).
    run_node = next(n for n in data['nodes'] if n['kind'] == 'run')
    assert run_node['label'] == 'orchestrator'
    assert run_node['status'] == 'ok'
    assert run_node['request'] == 'open linkedin and look'
    assert set(run_node['chains']) == {'orchestrator', 'linkedin_system_chain'}

    # both chains are hubs; edges connect the run to each of them
    chain_keys = {n['key'] for n in data['nodes'] if n['kind'] == 'chain'}
    assert {'orchestrator', 'linkedin_system_chain'} <= chain_keys
    edges = {(e['source'], e['target'], e['kind']) for e in data['edges']}
    assert (run_node['id'], 'chain:orchestrator', 'run-chain') in edges
    assert (run_node['id'], 'chain:linkedin_system_chain', 'run-chain') in edges
    # Run->chain edges carry the chain's activity count inside that run
    # (rendered as edge strength: thicker = more of the chain ran).
    rc = next(e for e in data['edges'] if e['kind'] == 'run-chain'
              and e['target'] == 'chain:linkedin_system_chain')
    assert rc['count'] == 1

    # day groups the run and both chat turns
    day_node = next(n for n in data['nodes'] if n['kind'] == 'day')
    assert day_node['runs'] == 1 and day_node['chats'] == 2
    assert ((day_node['id'], run_node['id'], 'day-run') in edges)
    assert data['counts']['runs_shown'] == 1
    assert data['counts']['chains'] == 2


def test_graph_data_scope_separation(mem):
    r1 = mem.begin_run('/x/a.json', source='agent')
    mem.record_node(chain_id='a', node_id='n1', node_type='code',
                    ports={'output': 'x'})
    mem.end_run(r1, ok=True)
    r2 = mem.begin_run('/x/b.json', source='play')
    mem.record_node(chain_id='b', node_id='n2', node_type='code',
                    ports={'output': 'y'})
    mem.end_run(r2, ok=False)

    agent = mem.graph_data(scope='agent')
    chain_keys = {n['key'] for n in agent['nodes'] if n['kind'] == 'chain'}
    assert chain_keys == {'a'}
    run_node = next(n for n in agent['nodes'] if n['kind'] == 'run')
    assert run_node['status'] == 'ok'

    manual = mem.graph_data(scope='manual')
    assert {n['key'] for n in manual['nodes'] if n['kind'] == 'chain'} == {'b'}
    assert next(n for n in manual['nodes'] if n['kind'] == 'run')['status'] == 'failed'


def test_graph_data_disabled_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setenv('LOOPER_RUN_MEMORY', 'off')
    monkeypatch.setenv('LOOPER_RUN_MEMORY_DB', str(tmp_path / 'mem.db'))
    _reset_thread_state()
    try:
        data = rm.graph_data()
        assert data == {'nodes': [], 'edges': [], 'counts': {}}
    finally:
        _reset_thread_state()


def test_graph_detail_audits_each_node(mem):
    run_id = mem.begin_run('/x/linkedin.json', source='agent')
    mem.record_node(chain_id='linkedin', node_id='n1', node_type='input',
                    label='Search', ports={'data': 'easy apply'})
    mem.end_run(run_id, ok=True)
    mem.record_chat('hi', 'done')

    run_out = mem.graph_detail('run', run_id)
    assert 'RUN_START' in run_out and '[Search]' in run_out
    chain_out = mem.graph_detail('chain', 'linkedin')
    assert 'easy apply' in chain_out
    data = mem.graph_data()
    chat_node = next(n for n in data['nodes'] if n['kind'] == 'chat')
    detail = mem.graph_detail('chat', chat_node['key'])
    assert 'User: hi' in detail or 'Agent: done' in detail
    assert 'no recorded events' in mem.graph_detail('run', 'nope')
