"""What the Builder draws on the canvas: section frames, corner labels, node notes.

The frame is a section's footprint with a small reserved strip underneath.
Its title and note sit INSIDE the frame's top-left corner, and every member
node carries its own sentence in that strip, so a reader sees both the gist of
the section and what each step in it does.

Run:  python -m pytest LoOper/tests/test_flow_label_placement.py
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

NOTE = ("Ask the user for a job query, then search the board and open the "
        "first matching listing. The check afterwards decides whether the "
        "listing was reachable and, when it was, the report step writes the "
        "findings back to the answer variable.")

DOES = {
    'a': 'Takes the query phrase the user typed.',
    'b': 'Opens the board and clicks the first result.',
}


@pytest.fixture(scope="module")
def qapp():
    from PyQt5.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class _View:
    def __init__(self, rect):
        self._rect = rect

    def sceneBoundingRect(self):
        return self._rect


class _Node:
    def __init__(self, node_id, rect):
        self.id = node_id
        self.view = _View(rect)


class _Canvas:
    """A canvas of fixed node rects (what the overlay actually measures)."""

    def __init__(self, rects):
        from PyQt5.QtCore import QRectF
        self._nodes = {nid: _Node(nid, QRectF(*rect))
                       for nid, rect in rects.items()}
        self._scene = None

    def all_nodes(self):
        return list(self._nodes.values())

    def get_node_by_id(self, node_id):
        return self._nodes.get(node_id)

    def scene(self):
        if self._scene is None:
            from PyQt5.QtWidgets import QGraphicsScene
            self._scene = QGraphicsScene()
        return self._scene

    def rect(self, node_id):
        return self._nodes[node_id].view.sceneBoundingRect()


def _draw(rects, groups, note=NOTE, node_texts=None):
    from NGUI.graph_elements.flow_overlay import FlowOverlay

    canvas = _Canvas(rects)
    overlay = FlowOverlay(canvas)
    overlay.show_sections(
        [{'title': title, 'note': note, 'hue': '#22D3EE', 'nodes': ids}
         for title, ids in groups],
        node_texts=DOES if node_texts is None else node_texts)
    return canvas, overlay


def test_the_section_label_sits_inside_the_frame_top_left(qapp):
    """Title + note live in the frame's corner, not in unrelated canvas."""
    from NGUI.graph_elements.flow_overlay import _PAD

    canvas, overlay = _draw(
        {'a': [0, 0, 180, 90], 'b': [260, 0, 180, 90]},
        [('ask', ['a', 'b'])])

    _section, frame, label, _notes = overlay._boxes[0]
    frame_rect = frame.boundingRect()
    spot = label.boundingRect()

    assert frame_rect.contains(spot), 'the label is inside its own frame'
    # Top-left: the label's corner hugs the frame's corner (within inset).
    assert abs(spot.left() - frame_rect.left()) <= 14
    assert abs(spot.top() - frame_rect.top()) <= 14
    assert spot.left() < frame_rect.center().x()
    assert spot.top() < frame_rect.center().y()
    assert _PAD > 0


def test_every_member_node_gets_its_own_note_under_it(qapp):
    """A node's sentence is drawn just below that node, inside the frame."""
    canvas, overlay = _draw(
        {'a': [0, 0, 180, 90], 'b': [260, 0, 180, 90]},
        [('ask', ['a', 'b'])])

    _section, frame, _label, notes = overlay._boxes[0]

    assert set(notes) == {'a', 'b'}, 'one note per member node'
    for node_id, note in notes.items():
        node_rect = canvas.rect(node_id)
        spot = note.boundingRect()
        assert note.isVisible(), node_id
        assert spot.top() >= node_rect.bottom(), 'below its node, never on it'
        assert spot.top() - node_rect.bottom() <= 10, 'hugging its node'
        assert not spot.intersects(node_rect)
        assert frame.boundingRect().contains(spot), \
            'the note stays inside the section it belongs to'
        assert note._text == DOES[node_id], 'prints that node own sentence'


def test_a_node_without_a_sentence_gets_no_note(qapp):
    """An empty note would only add noise, so none is drawn."""
    _canvas, overlay = _draw(
        {'a': [0, 0, 180, 90], 'b': [260, 0, 180, 90]},
        [('ask', ['a', 'b'])],
        node_texts={'a': 'Only this one is described.'})

    _section, _frame, _label, notes = overlay._boxes[0]

    assert set(notes) == {'a'}


def test_dragging_a_node_moves_its_note_with_it(qapp):
    """The note follows the live node rect (the 300 ms re-fit)."""
    canvas, overlay = _draw(
        {'a': [0, 0, 180, 90], 'b': [260, 0, 180, 90]},
        [('ask', ['a', 'b'])])

    _section, _frame, _label, notes = overlay._boxes[0]
    before = notes['b'].boundingRect()

    canvas.get_node_by_id('b').view._rect.translate(0, 120)
    overlay._refit_boxes()
    after = notes['b'].boundingRect()

    assert after.top() > before.top(), 'the note moved down with its node'
    assert abs(after.left() - before.left()) < 1.0


def test_a_note_past_the_cap_is_elided_not_spilled(qapp):
    """Text the label cannot hold ends in '…' instead of bleeding over nodes.

    The frame's header grows to fit whatever the section says, so the cut only
    bites past the per-text cap — and there it must clip, never overflow onto
    the nodes below.
    """
    from NGUI.graph_elements.flow_overlay import _NOTE_CHARS

    _canvas, overlay = _draw(
        {'a': [0, 0, 180, 90], 'b': [260, 0, 180, 90]},
        [('ask', ['a', 'b'])], note=NOTE * 40)

    _section, frame, label, _notes = overlay._boxes[0]

    assert len(label._note) == _NOTE_CHARS, 'the stored text is capped'
    assert frame.boundingRect().contains(label.boundingRect())
    width = label.boundingRect().width()
    rows = label._rows(label._note, width, label.boundingRect().height())
    shown = label._elide(label._note, width, rows)
    assert shown != label._note, 'the test needs a note that does not fit'
    assert shown.endswith('…')


def test_adjacent_members_share_one_frame(qapp):
    """Side-by-side members are one island: one dashed rectangle."""
    _canvas, overlay = _draw(
        {'a': [0, 0, 180, 90], 'b': [260, 0, 180, 90]},
        [('ask', ['a', 'b'])])

    _section, frame, _label, _notes = overlay._boxes[0]

    assert len(frame.rects()) == 1


def test_a_frame_never_rounds_a_node_that_is_not_its_own(qapp):
    """A frame holds its members only, never a node that sits between them."""
    canvas, overlay = _draw(
        {'a': [0, 0, 180, 90], 'b': [700, 0, 180, 90],
         'between': [310, 0, 180, 90]},
        [('ask', ['a', 'b'])])

    _section, frame, _label, _notes = overlay._boxes[0]
    frames = frame.rects()

    assert len(frames) == 2, 'each member gets a frame of its own'
    for node_id in ('a', 'b'):
        node_rect = canvas.rect(node_id)
        assert any(part.contains(node_rect) for part in frames), node_id
    alien = canvas.rect('between')
    assert not any(part.contains(alien.center()) for part in frames), \
        'no frame surrounds a node of another part of the chain'


def test_the_label_sticks_to_the_top_most_island(qapp):
    """One title per section, on the island a reader reaches first."""
    canvas, overlay = _draw(
        {'a': [0, 400, 180, 90], 'b': [700, 0, 180, 90],
         'between': [310, 200, 180, 90]},
        [('ask', ['a', 'b'])])

    _section, frame, label, _notes = overlay._boxes[0]

    assert len(frame.rects()) == 2
    spot = label.boundingRect()
    nearest = min(frame.rects(),
                  key=lambda part: (part.top(), part.left()))
    assert nearest.contains(spot)
