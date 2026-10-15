"""Builder panel smoke test — construction, toggle, and the needle-less
legacy fallback path (no live canvas / event-loop plumbing).

Run:  python -m pytest LoOper/tests/test_builder_panel_smoke.py
"""

import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

# Import order matters: NGUI.widgets.chains_library pulls collapsible_toolbar,
# which reaches back into graph_elements; loading graph_elements first keeps the
# (pre-existing) cycle from ImportError-ing when a test imports the library
# before anything else in this module.
import NGUI.graph_elements  # noqa: E402,F401


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _FakeGraphManager:
    def __init__(self):
        self.nodes = {}
        self._n = 0

    def create_node(self, node_type, name=None, pos=None):
        self._n += 1
        node = _FakeNode('0x%x' % self._n, node_type, name or '')
        self.nodes[node.id] = node
        return node

    def get_node_by_id(self, nid):
        return self.nodes.get(nid)

    def get_all_nodes(self):
        return list(self.nodes.values())


class _FakePort:
    def __init__(self, name):
        self._name = name

    def name(self):
        return self._name

    def connected_ports(self):
        return []


class _FakeNode:
    def __init__(self, nid, ntype, name=''):
        self.id = nid
        self.type_ = ntype
        self._name = name
        self._props = {}

    def name(self):
        return self._name

    def input_ports(self):
        return []

    def output_ports(self):
        return []

    def get_property(self, key):
        return self._props.get(key)

    def set_property(self, key, value):
        self._props[key] = value


class _FakeConfigManager:
    def __init__(self):
        self._current_chain_collection = ''
        self._current_chain_description = ''


class _FakeGraphView:
    def __init__(self):
        self.graph_manager = _FakeGraphManager()
        self.config_manager = _FakeConfigManager()
        self.chains_folder = ''


class _FakeMainWindow:
    def __init__(self):
        self.graph_view = _FakeGraphView()


def test_panel_constructs_as_a_side_dock_card(qapp):
    from NGUI.widgets.builder_panel import BuilderPanel, PANEL_WIDTH
    from PyQt5.QtWidgets import QSizePolicy
    panel = BuilderPanel(_FakeMainWindow())
    assert panel.width() == PANEL_WIDTH
    assert panel.sizePolicy().horizontalPolicy() == QSizePolicy.Fixed
    assert panel._card is not None
    assert panel._chat_layout is not None


def test_position_hint_spreads_nodes_in_a_grid(qapp):
    from NGUI.widgets.builder_panel import BuilderPanel
    panel = BuilderPanel(_FakeMainWindow())
    first = panel._position_hint()
    second = panel._position_hint()
    assert first != second
    assert len(first) == 2 and all(isinstance(v, float) for v in first)


def test_send_resolves_a_pending_ask_without_starting_a_turn(qapp):
    from NGUI.widgets.builder_panel import BuilderPanel
    panel = BuilderPanel(_FakeMainWindow())
    evt = threading.Event()
    holder = [None]
    panel._pending_ask = ('answer', evt, holder)
    panel._input.setText('linkedin.com')
    panel._on_send()
    assert evt.is_set()
    assert holder[0] == 'linkedin.com'
    assert panel._worker is None


def test_send_empty_does_nothing(qapp):
    from NGUI.widgets.builder_panel import BuilderPanel
    panel = BuilderPanel(_FakeMainWindow())
    panel._input.setText('   ')
    panel._on_send()
    assert panel._worker is None


def test_legacy_turn_executes_marker_directives(qapp):
    from NGUI.widgets.builder_panel import _BuilderWorker
    mgr = _FakeGraphManager()
    ctx = {
        'graph_manager': mgr,
        'config_manager': _FakeConfigManager(),
        'state': {},
        'ask': None,
        'chains_dir': '',
    }
    worker = _BuilderWorker(
        ctx,
        '### BUILDER: create_node type=input label=goal\n'
        '### BUILDER: create_node type=llm label=brain\n',
        None,
        threading.Event(),
    )
    out = worker._legacy_turn()
    assert 'created input node' in out
    assert 'created llm node' in out
    assert len(mgr.nodes) == 2
    assert ctx['state']['last_node_id'] is not None


def test_legacy_turn_without_directives_explains(qapp):
    from NGUI.widgets.builder_panel import _BuilderWorker
    worker = _BuilderWorker(
        {'graph_manager': _FakeGraphManager(), 'config_manager': None,
         'state': {}, 'ask': None, 'chains_dir': ''},
        'just prose, no directives',
        None,
        threading.Event(),
    )
    out = worker._legacy_turn()
    assert out.startswith('(Needle 2 engine not found')
    assert '### BUILDER:' in out


def test_dock_row_exposes_three_toggle_buttons(qapp):
    """The dock row holds one button per panel and reports the clicked key."""
    from NGUI.widgets.float_dock import FloatDockBar, DOCK_ITEMS

    dock = FloatDockBar()
    keys = []
    dock.panel_requested.connect(keys.append)
    assert set(dock._buttons) == {k for k, _i, _l in DOCK_ITEMS}
    dock._buttons['nodes'].click()
    assert keys == ['nodes']
    dock.set_active('nodes')
    assert dock._buttons['nodes'].isChecked()
    assert not dock._buttons['library'].isChecked()
    dock.set_active(None)
    assert not any(b.isChecked() for b in dock._buttons.values())


def test_toggle_panel_slides_one_panel_down_and_closes_the_last(qapp):
    """Accordion: a clicked panel drops under the dock row, and the
    previously open one closes."""
    from PyQt5.QtWidgets import QWidget
    from NGUI.graph_elements.ui_components import UIComponents
    from NGUI.widgets.float_dock import FloatDockBar

    host = QWidget()
    host.resize(1600, 900)
    host.show()
    dock = FloatDockBar(host)
    dock.show()
    library = QWidget(host)
    nodes = QWidget(host)

    ui = UIComponents.__new__(UIComponents)
    ui.parent_widget = host
    host._float_dock = dock
    host._builder_reserved_width = 0
    ui._panels = {'library': library, 'nodes': nodes}
    ui._panel_caps = {}
    ui._open_panel_key = None
    ui._panel_anim = None
    ui._laying_out = False

    ui.toggle_panel('library')
    assert ui._open_panel_key == 'library'
    assert library.isVisible()
    assert dock._buttons['library'].isChecked()
    x, y, w, h = ui._panel_layout('library')
    assert x == 12                      # left-anchored
    assert y > dock.height()             # dropped under the dock row
    assert int(ui._panel_anim.endValue()) == h  # slides open to its height

    ui.toggle_panel('nodes')
    assert ui._open_panel_key == 'nodes'
    assert nodes.isVisible()
    assert not library.isVisible()       # accordion closed the first one
    assert dock._buttons['nodes'].isChecked()

    ui.toggle_panel('nodes')             # clicking the open one closes it
    assert ui._open_panel_key is None
    assert int(ui._panel_anim.endValue()) == 0
    assert not any(b.isChecked() for b in dock._buttons.values())

    # Regression: open -> close -> open must not leave the panel 0px tall.
    # (Animating maximumHeight only ever shrinks a manual-geometry widget, so
    # a close used to clamp every later open to zero height.)
    ui.toggle_panel('library')
    ui.toggle_panel('library')           # animated close
    assert library.maximumHeight() > 1000   # no stale clamp left behind
    ui.toggle_panel('library')
    assert ui._open_panel_key == 'library'
    full_h = ui._panel_layout('library')[3]
    assert full_h > 0
    assert int(ui._panel_anim.endValue()) == full_h


def test_layout_keeps_the_open_panel_out_of_the_builder_column(qapp):
    """The dock row and its open panel never cross into the chat's column."""
    from PyQt5.QtWidgets import QWidget
    from NGUI.graph_elements.ui_components import UIComponents
    from NGUI.widgets.float_dock import FloatDockBar

    host = QWidget()
    host.resize(1600, 900)
    host.show()
    dock = FloatDockBar(host)
    dock.show()
    pane = QWidget(host)
    host._float_dock = dock
    host._delete_zone = None

    ui = UIComponents.__new__(UIComponents)
    ui.parent_widget = host
    ui._panels = {'library': pane}
    ui._open_panel_key = 'library'
    ui._panel_anim = None
    ui._laying_out = False

    host._builder_reserved_width = 0
    pane.show()
    ui.layout_floating_bars()
    assert pane.geometry().right() <= host.width() - 12

    host._builder_reserved_width = 536
    ui.layout_floating_bars()
    assert pane.geometry().right() <= host.width() - 536


def test_actions_panel_is_an_icon_only_column(qapp):
    """The old top bar drops down as a narrow icon-only column (the labels
    live in the tooltips), not a wide strip across the canvas."""
    from PyQt5.QtWidgets import QWidget
    from NGUI.graph_elements.ui_components import UIComponents
    from NGUI.widgets.actions_toolbar import ActionsToolbar
    from NGUI.widgets.float_dock import FloatDockBar

    host = QWidget()
    host.resize(1600, 900)
    host.show()
    dock = FloatDockBar(host)
    dock.show()
    acts = QWidget(host)
    acts.show()
    host._float_dock = dock
    host._builder_reserved_width = 0

    ui = UIComponents.__new__(UIComponents)
    ui.parent_widget = host
    ui._panels = {'actions': acts}
    ui._panel_anim = None
    ui._laying_out = False

    x, y, w, h = ui._panel_layout('actions')
    assert x == 12
    assert y > dock.height()          # under the dock row
    assert w == 56                    # an icon-only column, not a strip
    assert h > w                      # taller than it is wide

    # Compact mode: every labelled button loses its text but keeps a tooltip.
    # (Constructed as a child of the widget it treats as the graph view, the
    # way the real canvas hosts it: its toggle math maps between the two.)
    bar = ActionsToolbar(host, host)
    bar.set_compact(True)
    assert bar._label_buttons
    for btn, _label in bar._label_buttons:
        assert btn.text() == ''
        assert btn.property('icononly') is True
        assert btn.toolTip()


def test_library_sub_dock_appears_with_the_library_and_switches_its_list(qapp):
    """The collection sub-dock depends on the Library panel: it shows with
    it, drives which list it shows, and hides with it."""
    from PyQt5.QtWidgets import QWidget
    from NGUI.graph_elements.ui_components import UIComponents
    from NGUI.widgets.float_dock import COLLECTION_ITEMS, FloatDockBar

    host = QWidget()
    host.resize(1600, 900)
    host.show()
    dock = FloatDockBar(host)
    dock.show()
    sub = FloatDockBar(host, items=COLLECTION_ITEMS)
    sub.hide()
    nodes = QWidget(host)

    class _FakeLib(QWidget):
        """A widget standing in for the library (its list-switch API)."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.key = 'chain'
            self.selected = []

        def active_tab_key(self):
            return self.key

        def select_tab(self, key):
            self.selected.append(key)
            self.key = key

        def set_tabs_visible(self, visible):
            pass

    lib = _FakeLib(host)
    lib.show()
    host._float_dock = dock
    host._sub_dock = sub

    ui = UIComponents.__new__(UIComponents)
    ui.parent_widget = host
    ui._panels = {'library': lib, 'nodes': nodes}
    ui._panel_caps = {}
    ui._open_panel_key = None
    ui._panel_anim = None
    ui._laying_out = False

    assert not sub.isVisible()        # nothing open -> no sub-dock

    ui.toggle_panel('library')
    assert sub.isVisible()            # depends on the Library panel
    assert sub._buttons['chain'].isChecked()  # mirrors the active list

    ui._on_sub_dock('llm')
    assert lib.selected == ['llm']
    assert sub._buttons['llm'].isChecked()
    assert not sub._buttons['chain'].isChecked()

    ui.toggle_panel('nodes')          # the sub-dock is Library-only
    assert not sub.isVisible()


def test_library_hides_its_tabs_and_selects_by_key(qapp):
    """The library can hide its own tab row and be driven by key."""
    from PyQt5.QtWidgets import QWidget
    from NGUI.widgets.chains_library import ChainsLibrary

    parent = QWidget()
    lib = ChainsLibrary(parent, parent)
    parent.resize(320, 860)
    parent.show()
    lib.show()
    lib.set_tabs_visible(False)
    assert not lib._tabs_bar.isVisible()

    lib.select_tab('llm')
    assert lib.active_tab_key() == 'llm'
    assert lib._llm_scroll_area.isVisible()
    assert not lib._scroll_area.isVisible()

    lib.set_tabs_visible(True)
    assert lib._tabs_bar.isVisible()


def test_library_collections_bar_stays_available_on_every_tab(qapp):
    """Collections can be created/renamed/removed from ANY tab: the bar and
    its buttons stay visible instead of vanishing outside chains/sequences."""
    from PyQt5.QtWidgets import QWidget
    from NGUI.widgets.chains_library import ChainsLibrary

    parent = QWidget()                # keep it alive: Qt parents don't ref-count
    lib = ChainsLibrary(parent, parent)
    parent.resize(320, 860)
    parent.show()
    lib.resize(320, 860)
    lib.show()
    for key in ('sequence', 'chain', 'llm', 'context', 'code', 'web_seq'):
        lib.select_tab(key)
        assert lib._collections_bar.isVisible(), key
        assert lib._collections_combo.isVisible(), key
        assert lib._add_coll_btn.isVisible(), key
        assert lib._rename_coll_btn.isVisible(), key
        assert lib._delete_coll_btn.isVisible(), key


def test_library_cards_fill_the_scroll_viewport(qapp):
    """Cards span the list's full width (viewport minus its own right margin):
    the old fixed 'viewport - 30' left a gap before the scrollbar."""
    from PyQt5.QtWidgets import QWidget
    from NGUI.widgets.chains_library import (
        ChainsLibrary, DraggableSequenceFileButton)

    parent = QWidget()
    lib = ChainsLibrary(parent, parent)
    parent.resize(320, 860)
    parent.show()
    lib.resize(320, 860)
    lib.show()
    qapp.processEvents()

    card = DraggableSequenceFileButton('demo', 'demo.json', lib.graph_view)
    lib._sequences_container_layout.insertWidget(0, card)
    qapp.processEvents()
    lib._sync_sequence_buttons()

    viewport_w = lib._sequences_scroll_area.viewport().width()
    margin = lib._sequences_container_layout.contentsMargins().right()
    assert viewport_w > 0
    assert card.width() == viewport_w - margin


def test_canvas_builder_button_rides_the_top_right_corner(qapp):
    """The Builder toggle is anchored at the canvas' top right corner: it
    never moves when the chat is toggled (the chat opens below it)."""
    from PyQt5.QtWidgets import QWidget
    from NGUI.graph_elements.ui_components import UIComponents
    from NGUI.widgets.float_dock import CanvasSideButton

    host = QWidget()
    host.resize(1600, 900)
    host.show()
    btn = CanvasSideButton('ROBOT', 'Builder', host)
    btn.show()
    host._builder_toggle_btn = btn

    ui = UIComponents.__new__(UIComponents)
    ui.parent_widget = host
    ui._panels = {}
    ui._open_panel_key = None
    ui._panel_anim = None
    ui._laying_out = False

    ui.layout_floating_bars()
    closed = (btn.x(), btn.y())
    assert btn.y() == 12                                 # top right corner
    assert btn.x() == host.width() - 12 - btn.width()    # canvas right edge

    host._builder_panel_visible = True                   # chat open
    host._builder_reserved_width = 536
    ui.layout_floating_bars()
    assert (btn.x(), btn.y()) == closed                  # anchored: no move

    # The studio takeover hides it with the dock, and restores it after.
    ui._floating_bars = [(btn, 'right')]
    ui.set_floating_bars_visible(False)
    assert not btn.isVisible()
    ui.set_floating_bars_visible(True)
    assert btn.isVisible()


def test_builder_chat_is_a_canvas_overlay_below_the_button(qapp):
    """The chat floats over the canvas' right side (like the panels), starts
    below the dock band so the anchored button is never covered, and hides at
    width 0."""
    from PyQt5.QtWidgets import QWidget
    from NGUI.graph_elements.ui_components import UIComponents
    from NGUI.widgets.float_dock import FloatDockBar

    host = QWidget()
    host.resize(1600, 900)
    host.show()
    dock = FloatDockBar(host)
    dock.show()
    host._float_dock = dock
    chat = QWidget(host)

    ui = UIComponents.__new__(UIComponents)
    ui.parent_widget = host
    ui._panels = {}
    ui._open_panel_key = None
    ui._panel_anim = None
    ui._laying_out = False

    ui.set_builder_panel(chat)
    assert chat.parent() is host          # canvas overlay, not a row member
    assert chat.isHidden()

    ui.set_builder_width(500)
    assert chat.isVisible()
    assert chat.width() == 500
    assert chat.x() == host.width() - 12 - 500      # right side of the canvas
    btn_top = 12 + 34                                # the anchored button band
    assert chat.y() >= btn_top                       # opens below the button
    assert chat.height() > 0

    ui.set_builder_width(0)
    assert not chat.isVisible()


def test_record_action_dispatches_to_the_chosen_recorder(qapp):
    """Record offers both kinds: the choice routes to the web session
    recorder or the desktop sequence recorder."""
    from PyQt5.QtWidgets import QWidget
    from NGUI.widgets.actions_toolbar import ActionsToolbar

    calls = []

    class _GV(QWidget):
        def record_web_session(self, node=None):
            calls.append(('web', node))

        def record_new_sequence(self):
            calls.append(('desktop', None))

    gv = _GV()
    gv.resize(400, 200)
    gv.show()
    bar = ActionsToolbar(gv, gv)

    # The Record button is the first card the bar builds.
    assert bar._label_buttons[0][0].toolTip()
    bar._record_kind_chosen('web')
    bar._record_kind_chosen('desktop')
    assert calls == [('web', None), ('desktop', None)]


def test_top_bar_cluster_has_no_builder_button(qapp):
    """The Builder toggle is the canvas-side floating button only; the top
    bar must NOT carry a second one (it was removed as redundant)."""
    from PyQt5.QtWidgets import QToolButton
    from NGUI.main_window import MainWindow

    class _TopBarStub:
        def __init__(self):
            self._agent_mode_btn = None
            self._graph_aux = None
            self._agent_nav = None
            self._agent_notch_btn = None
            self._agent_overlay = None
            self._builder_btn = None

        def open_scheduler_manager(self):
            pass

        def open_ai_settings(self):
            pass

        def open_logs(self):
            pass

        def collapse_agent_to_notch(self):
            pass

        def _on_agent_mode_btn(self):
            pass

        def _on_builder_btn(self):
            pass

    stub = _TopBarStub()
    stub._make_top_button = MainWindow._make_top_button.__get__(stub)
    stub._build_top_bar_left = MainWindow._build_top_bar_left.__get__(stub)
    bar = stub._build_top_bar_left()  # keep the reference: Qt objects live
    assert bar is not None
    assert stub._builder_btn is None
    tips = [b.toolTip() for b in bar.findChildren(QToolButton)]
    assert not any('Builder' in t for t in tips)


def test_gui_proxy_runs_inline_when_already_on_the_gui_thread(qapp):
    """Regression: calling the proxy from the GUI thread itself must NOT
    emit a blocking queued signal to its own thread (Qt's 'Dead lock
    detected while activating a BlockingQueuedConnection') and must
    return the real result instead of None."""
    from PyQt5.QtCore import QThread
    from NGUI.widgets.builder_panel import _GuiProxy

    proxy = _GuiProxy()  # constructed on the GUI thread
    assert QThread.currentThread() is proxy.thread()

    assert proxy.call(lambda: 21 * 2) == 42

    def _boom():
        raise ValueError('nope')

    with pytest.raises(ValueError):
        proxy.call(_boom)


def test_builder_status_spinner_tracks_live_steps(qapp):
    """The processing indicator shows a running spinner plus step text, and
    the router's real events drive the text."""
    from NGUI.widgets.builder_panel import BuilderPanel

    panel = BuilderPanel(_FakeMainWindow())
    assert panel._status_row.isHidden()          # idle: hidden

    panel._start_busy()
    assert not panel._status_row.isHidden()
    assert panel._spinner._timer.isActive()
    assert panel._status_label.text()

    panel._on_status('tool:create_node')
    assert 'node' in panel._status_label.text().lower()

    panel._on_status('thinking')
    assert 'reason' in panel._status_label.text().lower()
    panel._advance_phase()                       # sticky: step text stays put
    assert 'reason' in panel._status_label.text().lower()

    panel._stop_busy()
    assert panel._status_row.isHidden()
    assert not panel._spinner._timer.isActive()


def test_needle_route_forwards_progress_cb(qapp):
    """route(query, progress_cb=...) hands the callback to the session loop."""
    from player.code_agent_ops.devtools import NeedleRouter

    seen = []

    class _R(NeedleRouter):
        def available(self):
            return True

        def _run_session(self, tools_path, query, progress_cb=None):
            seen.append(progress_cb)
            return {'ok': True, 'empty': False, 'text': 'ok', 'steps': 1,
                    'error': None}

    router = _R({}, exe_path=__file__)
    out = router.route('hello', progress_cb=len)
    assert out['ok'] and out['text'] == 'ok'
    assert seen == [len]


def test_builder_settings_subpanel_persists(qapp, tmp_path, monkeypatch):
    """The gear opens the model/engine subpanel and edits persist."""
    from NGUI.widgets.builder_panel import BuilderPanel
    from player.builder import settings as bsettings

    monkeypatch.setattr(bsettings, 'path', lambda: str(tmp_path / 'b.json'))
    panel = BuilderPanel(_FakeMainWindow())
    assert panel._settings_row.isHidden()          # hidden until the gear

    panel._toggle_settings()
    assert not panel._settings_row.isHidden()

    panel._steps_spin.setValue(4)
    assert bsettings.load()['max_steps'] == 4
    panel._reasoning_check.setChecked(False)
    assert bsettings.load()['reasoning'] is False
    panel._engine_combo.setCurrentIndex(
        panel._engine_combo.findData('ollama'))
    assert bsettings.load()['engine'] == 'ollama'

    panel._toggle_settings()
    assert panel._settings_row.isHidden()


# ---------------------------------------------------------------------------
# Interactive teach / record cards
# ---------------------------------------------------------------------------

def _buttons_of(frame):
    from PyQt5.QtWidgets import QPushButton
    return {b.text(): b for b in frame.findChildren(QPushButton)}


def test_teach_card_buttons_resolve_the_ask(qapp):
    """The teach ask renders as a card (one button per route) and clicking
    one answers the pending ask with the route token."""
    from NGUI.widgets.builder_panel import BuilderPanel

    panel = BuilderPanel(_FakeMainWindow())
    evt = threading.Event()
    holder = [None]
    panel._begin_ask({'step': 'navigate to reddit and search'},
                     'teach_card', evt, holder)
    frame = panel._chat_layout.itemAt(
        panel._chat_layout.count() - 1).widget()
    buttons = set(_buttons_of(frame))
    assert {'Record web', 'Record desktop', 'Build LLM step',
            'Build a brain', 'Skip'} <= buttons
    assert not evt.is_set()

    _buttons_of(frame)['Record web'].click()
    assert evt.is_set()
    assert holder[0] == 'web'


def test_record_card_starts_and_advances_the_web_recording(qapp):
    """The record ask is a card: Start runs the web recorder on the
    placeholder node; the card auto-advances when session_file lands."""
    from NGUI.widgets.builder_panel import BuilderPanel

    calls = []

    class _GV(_FakeGraphView):
        def __init__(self):
            super().__init__()
            self.sequences_folder = ''

        def record_web_session(self, node):
            calls.append(node.id)

    class _MW:
        def __init__(self):
            self.graph_view = _GV()

    panel = BuilderPanel(_MW())
    node = panel.main_window.graph_view.graph_manager.create_node(
        'web_sequence.WebSequenceNode', 'navigate to reddit')
    panel._state['last_node_id'] = node.id
    evt = threading.Event()
    holder = [None]
    panel._pending_ask = ('record_web_sequence', evt, holder)

    panel._begin_record_card('Record: "navigate to reddit"',
                             'record_web_sequence')
    card = panel._record_card
    assert card is not None and card.edit is not None
    assert card.edit.text() == 'navigate to reddit'      # prefilled

    _buttons_of(card.frame)['Start recording'].click()
    assert calls == [node.id]                # recorder ran on the node
    assert panel._record_timer.isActive()
    assert node.get_property('description') == 'navigate to reddit'
    assert not evt.is_set()                  # still recording

    node.set_property('session_file', 'navigate to reddit.json')
    panel._poll_recording()
    assert evt.is_set()                      # auto-advanced
    assert not panel._record_timer.isActive()
    assert holder[0] == ''                   # path read by _take_ask_answer


def test_record_card_desktop_records_into_the_sequences_folder(
        qapp, tmp_path):
    """Desktop route: the card points the node at the sequence file, runs
    the desktop recorder, and advances when the file appears on disk."""
    from NGUI.widgets.builder_panel import BuilderPanel

    started = []

    class _GV(_FakeGraphView):
        def __init__(self):
            super().__init__()
            self.sequences_folder = str(tmp_path)

        def record_web_session(self, node):
            raise AssertionError('desktop route must not open a web session')

    class _MW:
        def __init__(self):
            self.graph_view = _GV()

        def start_recording(self, name):
            started.append(name)

    panel = BuilderPanel(_MW())
    node = panel.main_window.graph_view.graph_manager.create_node(
        'sequence.SequenceNode', 'scroll down on reddit')
    panel._state['last_node_id'] = node.id
    evt = threading.Event()
    panel._pending_ask = ('record_sequence', evt, [None])
    panel._begin_record_card('Record', 'record_sequence')

    card = panel._record_card
    _buttons_of(card.frame)['Start recording'].click()
    assert started == ['scroll_down_on_reddit']           # slugged name
    assert node.get_property('sequence_file') == \
        'scroll_down_on_reddit.json'
    watch = panel._record_hold['watch']
    assert watch.endswith('scroll_down_on_reddit.json')
    assert not evt.is_set()

    # The recorder saves the file -> the card auto-advances.
    with open(watch, 'w', encoding='utf-8') as f:
        f.write('{}')
    panel._poll_recording()
    assert evt.is_set()
    assert not panel._record_timer.isActive()


def test_record_card_cancel_resolves_without_recording(qapp):
    from NGUI.widgets.builder_panel import BuilderPanel

    panel = BuilderPanel(_FakeMainWindow())
    evt = threading.Event()
    holder = [None]
    panel._pending_ask = ('record_web_sequence', evt, holder)
    panel._begin_record_card('Record', 'record_web_sequence')
    _buttons_of(panel._record_card.frame)['I cannot record it'].click()
    assert evt.is_set()
    assert not panel._record_timer.isActive()
    assert panel._record_hold is None


# ---------------------------------------------------------------------------
# Building assistant wiring (selection + history context)
# ---------------------------------------------------------------------------

def test_panel_ctx_carries_selection_and_history(qapp):
    """The turn context hands the assistant the user's selection and the
    recent exchange tail (its focus + its memory)."""
    from NGUI.widgets.builder_panel import BuilderPanel

    panel = BuilderPanel(_FakeMainWindow())
    panel._history.append({'role': 'user', 'text': 'earlier'})
    ctx = panel._make_ctx()
    assert ctx['history'] == [{'role': 'user', 'text': 'earlier'}]
    # The fake graph view has no selection -> empty block, never an error.
    assert ctx['selection_text']() == ''


def test_panel_remember_trims_the_history(qapp):
    from NGUI.widgets.builder_panel import BuilderPanel

    panel = BuilderPanel(_FakeMainWindow())
    for i in range(14):
        panel._remember('user', 'm%d' % i)
    assert len(panel._history) == 10
    assert panel._history[-1]['text'] == 'm13'


def test_panel_router_is_the_building_assistant(qapp):
    """The panel's brain is the selection-aware assistant (the composer
    and teach flow stay off this path by design)."""
    from NGUI.widgets.builder_panel import BuilderPanel
    from player.builder.assistant import BuilderAssistant

    panel = BuilderPanel(_FakeMainWindow())
    router = panel._make_router(panel._make_ctx())
    assert isinstance(router, BuilderAssistant)


def test_turn_finish_remembers_the_assistant_reply(qapp):
    from NGUI.widgets.builder_panel import BuilderPanel

    panel = BuilderPanel(_FakeMainWindow())
    panel._on_turn_finished('built skill "x": 2 step(s)\n\nsecond line')
    assert panel._history[-1]['role'] == 'assistant'
    assert 'built skill' in panel._history[-1]['text']
