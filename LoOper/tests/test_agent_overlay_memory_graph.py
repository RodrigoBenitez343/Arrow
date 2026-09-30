"""Integration test: the agent overlay's memory knowledge-graph panel.

Locks the header-"G" button -> panel swap (same column-swap contract as the
Scheduler panel: explicit hidden flags, one view at a time) and that a
refresh renders the recorded events into the canvas.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from PyQt5.QtCore import QSize  # noqa: E402
from PyQt5.QtGui import QResizeEvent  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

import player.agentic_ops.run_memory as rm  # noqa: E402
from NGUI.scheduler_service import SchedulerService  # noqa: E402
from NGUI.widgets.agent_overlay import AgentOverlay  # noqa: E402


def _reset_thread_state():
    """Drop the thread-local conn so the next call binds to the env DB."""
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


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_toggle_swaps_chat_and_panel(qapp):
    overlay = AgentOverlay()
    assert overlay._memory_graph_panel.isHidden()

    overlay._toggle_memory_graph()
    assert not overlay._memory_graph_panel.isHidden()
    assert overlay._chat_scroll.isHidden()
    assert overlay._memory_graph_btn.isChecked()

    overlay._toggle_memory_graph()
    assert overlay._memory_graph_panel.isHidden()
    assert not overlay._chat_scroll.isHidden()
    assert not overlay._memory_graph_btn.isChecked()


def test_mutual_exclusion_with_scheduler(qapp, tmp_path):
    overlay = AgentOverlay()
    overlay.set_scheduler_service(
        SchedulerService(project_root=str(tmp_path), check_interval_seconds=5))

    overlay._toggle_memory_graph()
    assert not overlay._memory_graph_panel.isHidden()

    overlay._toggle_scheduler()
    assert overlay._memory_graph_panel.isHidden()   # graph yields to scheduler
    assert not overlay._scheduler_panel.isHidden()
    assert not overlay._memory_graph_btn.isChecked()

    overlay._toggle_memory_graph()
    assert overlay._scheduler_panel.isHidden()      # ...and back
    assert not overlay._memory_graph_panel.isHidden()


def test_panel_renders_recorded_events(qapp, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOPER_RUN_MEMORY", "on")
    monkeypatch.setenv("LOOPER_RUN_MEMORY_DB", str(tmp_path / "mem.db"))
    _reset_thread_state()
    try:
        run_id = rm.begin_run("/x/orchestrator.json", source="agent")
        rm.record_node(chain_id="orchestrator", node_id="o1",
                       node_type="orchestrator", label="Route",
                       ports={"output": "final"})
        rm.record_node(chain_id="linkedin_system_chain", node_id="b1",
                       node_type="chain_import", label="linkedin",
                       ports={"output": "opened"})
        rm.end_run(run_id, ok=True)
        rm.record_chat("open linkedin", "done")

        overlay = AgentOverlay()
        panel = overlay._memory_graph_panel
        panel.refresh()

        assert panel._scene.items(), "graph should render nodes for the events"
        assert "run" in panel._count_label.text()
        assert panel._items_by_node.get(f"run:{run_id}") is not None
        assert panel._items_by_node.get("chain:linkedin_system_chain") is not None
        # Edges state their meaning: the legend names every encoding (chat ->
        # run it produced, run -> chain used), and run->chain edges carry
        # direction arrowheads.
        assert "chat turn" in panel._legend.text()
        assert "run \u2192 chain" in panel._legend.text()
        from PyQt5.QtWidgets import QGraphicsPolygonItem
        assert any(isinstance(i, QGraphicsPolygonItem)
                   for i in panel._scene.items())
        # Node labels carry counts (primary + secondary lines).
        labels = [i.text() for i in panel._scene.items()
                  if hasattr(i, 'text')]
        assert any('event' in t for t in labels)   # chain activity counts
        assert any('nodes' in t for t in labels)   # run summary on day->run
        # Relationship facts ride the LINES: 'used' labels on run->chain
        # links, and the run/chat text follows their links too.
        assert any('used' in t for t in labels)
        assert any('User: open linkedin' in t for t in labels)

        panel.select("run", run_id)
        assert "RUN_START" in panel._details.toPlainText()
        # Selection highlight is applied without touching other items.
        item = panel._items_by_node[f"run:{run_id}"]
        assert item.pen().widthF() == pytest.approx(2.0)

        # Regression (app-killing): PyQt5 calls qFatal on ANY exception
        # escaping a Qt virtual override — the canvas resizeEvent used to
        # touch a missing attribute and crashed the whole app with no
        # traceback (0xc0000409 in Qt5Core.dll) the moment the overlay laid
        # out.  Force the event and prove both the guard and the fit.
        panel._canvas.resize(200, 150)
        QApplication.sendEvent(
            panel._canvas,
            QResizeEvent(QSize(200, 150), QSize(200, 150)),
        )
        panel._canvas.fit_all()
        assert 0.0 < panel._canvas.transform().m11() < 1.0
    finally:
        _reset_thread_state()


def test_layout_centers_the_orchestrator_hub(qapp):
    """Temporal radial layout: the orchestrator chain is pinned at the centre
    and the rest of the graph grows outward from it (not left-to-right)."""
    overlay = AgentOverlay()
    panel = overlay._memory_graph_panel
    nodes = [
        {'id': 'chain:ORCHESTRATOR', 'kind': 'chain', 'key': 'ORCHESTRATOR',
         'label': 'ORCHESTRATOR', 'events': 5},
        {'id': 'chain:linkedin', 'kind': 'chain', 'key': 'linkedin',
         'label': 'linkedin', 'events': 3},
        {'id': 'run:r1', 'kind': 'run', 'key': 'r1', 'label': 'ORCHESTRATOR',
         'ts': 1000.0, 'events': 4, 'date': '2026-09-23'},
        {'id': 'day:2026-09-23', 'kind': 'day', 'key': '2026-09-23',
         'label': '2026-09-23', 'runs': 1, 'chats': 0},
    ]
    edges = [
        {'source': 'run:r1', 'target': 'chain:ORCHESTRATOR', 'kind': 'run-chain',
         'count': 2},
        {'source': 'run:r1', 'target': 'chain:linkedin', 'kind': 'run-chain',
         'count': 1},
        {'source': 'day:2026-09-23', 'target': 'run:r1', 'kind': 'day-run'},
    ]

    pos = panel._layout_radial(nodes, edges)

    assert pos['chain:ORCHESTRATOR'] == (0.0, 0.0)  # the root stays centred
    assert set(pos) == {n['id'] for n in nodes}
    # everything else sits at a real distance from the hub
    for nid, (x, y) in pos.items():
        if nid != 'chain:ORCHESTRATOR':
            assert (x * x + y * y) ** 0.5 > 20.0
    # the day marker lives on the outer arc, beyond its run's ring
    run_d = (pos['run:r1'][0] ** 2 + pos['run:r1'][1] ** 2) ** 0.5
    day_d = (pos['day:2026-09-23'][0] ** 2
             + pos['day:2026-09-23'][1] ** 2) ** 0.5
    assert day_d > run_d


def test_layout_date_segments_keep_same_day_dots_apart(qapp):
    """Each date owns an angular segment; within it, dots are spread by time
    so two events of the same day can never stack on top of each other."""
    import math

    overlay = AgentOverlay()
    panel = overlay._memory_graph_panel
    nodes = [
        {'id': 'chain:ORCHESTRATOR', 'kind': 'chain', 'key': 'ORCHESTRATOR',
         'label': 'ORCHESTRATOR', 'events': 1},
        {'id': 'day:2026-09-22', 'kind': 'day', 'key': '2026-09-22',
         'label': '2026-09-22', 'runs': 1, 'chats': 0},
        {'id': 'day:2026-09-23', 'kind': 'day', 'key': '2026-09-23',
         'label': '2026-09-23', 'runs': 2, 'chats': 0},
        {'id': 'run:r1', 'kind': 'run', 'key': 'r1', 'label': 'A',
         'ts': 1000.0, 'date': '2026-09-22'},
        {'id': 'run:r2', 'kind': 'run', 'key': 'r2', 'label': 'A',
         'ts': 2000.0, 'date': '2026-09-23'},
        {'id': 'run:r3', 'kind': 'run', 'key': 'r3', 'label': 'A',
         'ts': 3000.0, 'date': '2026-09-23'},
    ]
    edges = []

    pos = panel._layout_radial(nodes, edges)

    from NGUI.widgets.memory_graph_panel import _day_wedges
    wedges = {w[0]: w for w in _day_wedges(
        [n for n in nodes if n['kind'] == 'day'])}

    def _adist(a, b):
        d = abs(a - b) % (2.0 * math.pi)
        return min(d, 2.0 * math.pi - d)

    # each run sits inside ITS day's segment: closer to its own arc mid
    # than to the other day's mid (wrap-safe angular distance)
    for rid, own, other in (
            ('run:r1', 'day:2026-09-22', 'day:2026-09-23'),
            ('run:r2', 'day:2026-09-23', 'day:2026-09-22'),
            ('run:r3', 'day:2026-09-23', 'day:2026-09-22')):
        p = pos[rid]
        a = math.atan2(p[1], p[0])
        assert _adist(a, wedges[own][3]) < _adist(a, wedges[other][3])
    # same-day dots keep real distance between them (no pile-up)
    dx = pos['run:r2'][0] - pos['run:r3'][0]
    dy = pos['run:r2'][1] - pos['run:r3'][1]
    assert (dx * dx + dy * dy) ** 0.5 > 20.0


def _graph(days, runs, chats):
    """Craft a graph_data()-shaped payload for panel rendering tests."""
    nodes = [{'id': 'chain:HUB', 'kind': 'chain', 'key': 'HUB',
              'label': 'HUB', 'events': 9}]
    edges = []
    for i, d in enumerate(days):
        nodes.append({'id': f'day:{d}', 'kind': 'day', 'key': d, 'label': d,
                      'runs': 0, 'chats': 0})
    for i, (rid, date, ts) in enumerate(runs):
        nodes.append({'id': f'run:{rid}', 'kind': 'run', 'key': rid,
                      'label': 'HUB', 'ts': ts, 'date': date, 'events': 2,
                      'status': 'ok'})
        edges.append({'source': f'day:{date}', 'target': f'run:{rid}',
                      'kind': 'day-run'})
        edges.append({'source': f'run:{rid}', 'target': 'chain:HUB',
                      'kind': 'run-chain', 'count': 2})
    for i, (seq, date, ts) in enumerate(chats):
        nodes.append({'id': f'chat:{seq}', 'kind': 'chat', 'key': str(seq),
                      'label': 'hello', 'ts': ts, 'date': date,
                      'role': 'user'})
        edges.append({'source': f'day:{date}', 'target': f'chat:{seq}',
                      'kind': 'day-chat'})
    for n in nodes:
        if n['kind'] == 'day':
            n['runs'] = sum(1 for r in runs if r[1] == n['key'])
            n['chats'] = sum(1 for c in chats if c[1] == n['key'])
    return {'nodes': nodes, 'edges': edges,
            'counts': {'runs': len(runs), 'runs_shown': len(runs),
                       'chains': 1, 'chats_shown': len(chats)}}


def test_single_day_draws_no_arc_but_keeps_fan(qapp, monkeypatch):
    """One day = no boundary exists: no ring is drawn around the graph
    (regression: a full-circle arc read as a stray line), and the date
    still fans out to its own run and chat."""
    import player.agentic_ops.run_memory as rm
    monkeypatch.setattr(rm, 'graph_data', lambda **k: _graph(
        ['2026-09-23'],
        [('r1', '2026-09-23', 1000.0)],
        [(1, '2026-09-23', 1001.0)],
    ))
    overlay = AgentOverlay()
    panel = overlay._memory_graph_panel
    panel._layout_combo.setCurrentIndex(1)  # Radial
    panel.refresh()

    from PyQt5.QtWidgets import QGraphicsLineItem, QGraphicsPathItem
    items = panel._scene.items()
    assert not any(isinstance(i, QGraphicsPathItem) for i in items)
    lines = [i for i in items if isinstance(i, QGraphicsLineItem)]
    # day->run + day->chat + run->chain must all be drawn
    assert len(lines) >= 3


def test_two_days_bounded_by_gapped_arcs(qapp, monkeypatch):
    """With several days the arcs come back — one per date, as boundaries
    with gaps (never one seamless circle)."""
    import player.agentic_ops.run_memory as rm
    monkeypatch.setattr(rm, 'graph_data', lambda **k: _graph(
        ['2026-09-22', '2026-09-23'],
        [('r1', '2026-09-22', 1000.0), ('r2', '2026-09-23', 2000.0)],
        [],
    ))
    overlay = AgentOverlay()
    panel = overlay._memory_graph_panel
    panel._layout_combo.setCurrentIndex(1)  # Radial
    panel.refresh()

    from PyQt5.QtWidgets import QGraphicsPathItem
    arcs = [i for i in panel._scene.items() if isinstance(i, QGraphicsPathItem)]
    assert len(arcs) == 2


def test_columns_layout_reads_left_to_right_with_time(qapp):
    """Default layout: DAY column -> its runs+chats stacked in time ->
    CHAINS; a chat sits right above the run it produced."""
    overlay = AgentOverlay()
    panel = overlay._memory_graph_panel
    nodes = [
        {'id': 'chain:ORCHESTRATOR', 'kind': 'chain', 'key': 'ORCHESTRATOR',
         'label': 'ORCHESTRATOR', 'events': 5},
        {'id': 'chain:linkedin', 'kind': 'chain', 'key': 'linkedin',
         'label': 'linkedin', 'events': 3},
        {'id': 'day:2026-09-23', 'kind': 'day', 'key': '2026-09-23',
         'label': '2026-09-23', 'runs': 1, 'chats': 1},
        {'id': 'chat:1', 'kind': 'chat', 'key': '1', 'label': 'hello',
         'ts': 900.0, 'date': '2026-09-23', 'role': 'user', 'run': 'r1'},
        {'id': 'run:r1', 'kind': 'run', 'key': 'r1', 'label': 'ORCHESTRATOR',
         'ts': 1000.0, 'date': '2026-09-23', 'events': 4, 'status': 'ok'},
    ]
    edges = [
        {'source': 'day:2026-09-23', 'target': 'run:r1', 'kind': 'day-run'},
        {'source': 'day:2026-09-23', 'target': 'chat:1', 'kind': 'day-chat'},
        {'source': 'chat:1', 'target': 'run:r1', 'kind': 'chat-run'},
        {'source': 'run:r1', 'target': 'chain:ORCHESTRATOR',
         'kind': 'run-chain', 'count': 2},
    ]

    pos = panel._layout_columns(nodes, edges)

    # columns: day | members | chains, strictly increasing x
    assert pos['day:2026-09-23'][0] < pos['run:r1'][0] < pos['chain:ORCHESTRATOR'][0]
    # members stack in chronological order: the chat above its run
    assert pos['chat:1'][1] < pos['run:r1'][1]


def test_columns_layout_draws_chat_run_link(qapp, monkeypatch):
    """In the default layout the recorded chat -> run tie is drawn (the
    relationship that used to be missing between a question and the chains
    it executed)."""
    import player.agentic_ops.run_memory as rm
    data = _graph(['2026-09-23'], [('r1', '2026-09-23', 1000.0)], [])
    data['nodes'].append(
        {'id': 'chat:1', 'kind': 'chat', 'key': '1', 'label': 'hello',
         'ts': 900.0, 'date': '2026-09-23', 'role': 'user', 'run': 'r1'})
    data['edges'].append({'source': 'chat:1', 'target': 'run:r1',
                          'kind': 'chat-run'})
    monkeypatch.setattr(rm, 'graph_data', lambda **k: data)

    overlay = AgentOverlay()
    panel = overlay._memory_graph_panel
    panel.refresh()

    from PyQt5.QtWidgets import QGraphicsLineItem
    lines = [i for i in panel._scene.items()
             if isinstance(i, QGraphicsLineItem)]
    # day->run + run->chain + chat->run
    assert len(lines) >= 3
    assert any('produced run' in (i.toolTip() or '') for i in lines)


def test_chain_spacing_respects_node_radii(qapp):
    """The chain column spreads by REAL radii — the old fixed 46px gap let
    the biggest hubs (r up to 24) touch.  Node overlap must not happen."""
    overlay = AgentOverlay()
    panel = overlay._memory_graph_panel
    nodes = [{'id': 'day:2026-09-23', 'kind': 'day', 'key': '2026-09-23',
              'label': '2026-09-23', 'runs': 1, 'chats': 0},
             {'id': 'run:r1', 'kind': 'run', 'key': 'r1', 'label': 'A',
              'ts': 1000.0, 'date': '2026-09-23', 'status': 'ok'}]
    edges = [{'source': 'day:2026-09-23', 'target': 'run:r1',
              'kind': 'day-run'}]
    for i in range(3):
        nodes.append({'id': f'chain:c{i}', 'kind': 'chain', 'key': f'c{i}',
                      'label': f'c{i}', 'events': 400})  # r = 24
        edges.append({'source': 'run:r1', 'target': f'chain:c{i}',
                      'kind': 'run-chain', 'count': 3})

    pos = panel._layout_columns(nodes, edges)
    ys = sorted(pos[f'chain:c{i}'][1] for i in range(3))
    # adjacent hubs: radius_a + radius_b + 14 padding
    assert ys[1] - ys[0] >= 24 + 24 + 14 - 0.01
    assert ys[2] - ys[1] >= 24 + 24 + 14 - 0.01


def test_panel_empty_state(qapp, tmp_path):
    """No records in the window -> the empty message, never a crash."""
    overlay = AgentOverlay()
    panel = overlay._memory_graph_panel
    panel.refresh()  # conftest keeps memory OFF here -> empty projection
    assert panel._empty.isVisibleTo(panel)
    assert panel._canvas.isHidden()
