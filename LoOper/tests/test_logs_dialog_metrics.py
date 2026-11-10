"""The Logs dialog's live metrics toggle: parsing, persistence, point->line.

Three things are worth locking down without a real run:

  * the toggle survives a restart (``app_state.set_ui_flag`` round-trip, and it
    must not clobber the chain the editor is holding),
  * a chart click lands on the log line written at that moment
    (``nearest_record`` + the dialog's jump), and
  * the panel reads the metrics JSONL incrementally, so a growing log is
    charted without re-reading it.
"""

import json
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from NGUI import app_state  # noqa: E402
from NGUI.dialogs import logs_dialog  # noqa: E402
from NGUI.widgets import metrics_panel  # noqa: E402

BULLET = ("- `{stamp}` **[INFO]** `player.execution` engine embed ready")


# ------------------------------------------------------------- app_state ---

def test_ui_flag_round_trip(tmp_path, monkeypatch):
    target = tmp_path / "app_state.json"
    monkeypatch.setattr(app_state, "path", lambda: str(target))

    assert app_state.ui_flag("logs_metrics", False) is False
    assert app_state.set_ui_flag("logs_metrics", True) is True
    assert app_state.ui_flag("logs_metrics", False) is True
    assert app_state.set_ui_flag("logs_metrics", False) is True
    assert app_state.ui_flag("logs_metrics", True) is False


def test_setting_a_flag_keeps_the_rest_of_the_state(tmp_path, monkeypatch):
    target = tmp_path / "app_state.json"
    target.write_text(json.dumps({
        "version": 1, "chain_file": "X.json",
        "chain_config": {"input_nodes": [{"node_id": "n1"}]}}), encoding="utf-8")
    monkeypatch.setattr(app_state, "path", lambda: str(target))

    assert app_state.set_ui_flag("logs_metrics", True)
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["chain_file"] == "X.json"
    assert data["chain_config"]["input_nodes"] == [{"node_id": "n1"}]
    assert data["ui"] == {"logs_metrics": True}


# --------------------------------------------------------------- parsing ---

def test_bullets_keep_their_timestamp():
    records = logs_dialog._parse_records(
        [BULLET.format(stamp="14:30:05.123"), "### a section"])
    assert records[0]["stamp"] == "14:30:05.123"
    assert records[0]["level"] == "INFO"
    assert records[1]["kind"] == "section"
    assert "stamp" not in records[1]


def test_seconds_parses_a_stamp():
    assert logs_dialog._seconds("00:00:01.500") == pytest.approx(1.5)
    assert logs_dialog._seconds("nope") is None


def test_nearest_record_picks_the_closest_line():
    records = logs_dialog._parse_records([
        BULLET.format(stamp="10:00:00.000"),
        BULLET.format(stamp="10:00:10.000"),
        BULLET.format(stamp="10:00:20.000"),
    ])
    picked, delta = logs_dialog.nearest_record(records, 10 * 3600 + 12)
    assert picked["stamp"] == "10:00:10.000"
    assert delta == pytest.approx(-2.0)


def test_nearest_record_survives_midnight():
    records = logs_dialog._parse_records([BULLET.format(stamp="23:59:59.000")])
    picked, delta = logs_dialog.nearest_record(records, 1.0)
    assert picked["stamp"] == "23:59:59.000"
    assert delta == pytest.approx(-2.0)


def test_nearest_record_without_stamps_is_none():
    assert logs_dialog.nearest_record([{"kind": "heading"}], 5) is None


# ----------------------------------------------------------- metrics file ---

def test_samples_are_read_incrementally(tmp_path):
    path = tmp_path / "engine_metrics.jsonl"
    path.write_text(json.dumps({"kind": "embed", "t": 1, "ms": 90}) + "\n",
                    encoding="utf-8")
    lines, offset = metrics_panel._read_between(str(path), 0)
    assert [json.loads(line)["kind"] for line in lines] == ["embed"]

    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"kind": "forward", "t": 2, "ms": 7}) + "\n")
    lines, offset = metrics_panel._read_between(str(path), offset)
    assert [json.loads(line)["kind"] for line in lines] == ["forward"]


def test_a_half_written_line_is_left_for_the_next_pass(tmp_path):
    path = tmp_path / "engine_metrics.jsonl"
    path.write_text('{"kind":"embed","t":1,"ms":90}\n{"kind":"emb',
                    encoding="utf-8")
    lines, offset = metrics_panel._read_between(str(path), 0)
    assert len(lines) == 1
    with open(path, "a", encoding="utf-8") as handle:
        handle.write('ed","t":2,"ms":3}\n')
    lines, _offset = metrics_panel._read_between(str(path), offset)
    assert [json.loads(line)["ms"] for line in lines] == [3]


def test_numeric_fields_are_discovered_per_kind_and_unit():
    rows = metrics_panel.parse_lines([
        '{"kind":"embed","t":1,"ms":90}',
        '{"kind":"embed","t":2,"ms":80,"count":3}',
        '{"kind":"embed","t":3,"ms":70,"count":4}',
        '{"kind":"engine_unload","t":4}',
        'not json',
    ])
    # One plot per unit: milliseconds never share an axis with a count.
    assert metrics_panel.numeric_fields(rows) == [
        ("embed \u00b7 ms", "embed", ["ms"]),
        ("embed \u00b7 count", "embed", ["count"]),
    ]


def test_fields_of_one_unit_share_an_axis():
    rows = metrics_panel.parse_lines([
        '{"kind":"context_trim","t":1,"chars_before":3000,"chars_after":2400}',
        '{"kind":"context_trim","t":2,"chars_before":3400,"chars_after":2500}',
    ])
    assert metrics_panel.numeric_fields(rows) == [
        ("context_trim", "context_trim", ["chars_before", "chars_after"]),
    ]


def test_a_constant_field_is_not_charted():
    # engine_code is the same number on every forward sample: a flat line that
    # only steals height from `ms`.
    rows = metrics_panel.parse_lines([
        '{"kind":"forward","t":1,"ms":1800,"engine_code":1}',
        '{"kind":"forward","t":2,"ms":1900,"engine_code":1}',
    ])
    assert metrics_panel.numeric_fields(rows) == [("forward", "forward", ["ms"])]


def test_a_kind_without_a_number_gets_no_plot():
    rows = metrics_panel.parse_lines([
        '{"kind":"engine_unload","t":1}',
        '{"kind":"engine_unload","t":2}',
    ])
    assert metrics_panel.numeric_fields(rows) == []


def test_panel_charts_whatever_the_log_holds(tmp_path):
    path = tmp_path / "engine_metrics.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in [
        {"kind": "engine_load", "t": 10, "ms": 3200, "engine_code": 1},
        {"kind": "forward", "t": 11, "ms": 1800, "engine_code": 1},
        {"kind": "context_trim", "t": 12, "chars_before": 3000,
         "chars_after": 2400},
    ]) + "\n", encoding="utf-8")

    panel = metrics_panel.MetricsPanel(path=str(path))
    assert panel.refresh() == 3
    assert panel.refresh() == 0                     # nothing new
    assert set(panel.kinds()) == {"engine_load", "forward", "context_trim"}
    panel.set_live(False)


def test_a_click_maps_to_the_nearest_sample(tmp_path):
    path = tmp_path / "engine_metrics.jsonl"
    path.write_text("\n".join(json.dumps({"kind": "embed", "t": 100 + i,
                                           "ms": 50 + i})
                               for i in range(5)) + "\n", encoding="utf-8")
    panel = metrics_panel.MetricsPanel(path=str(path))
    panel.refresh()
    plot = panel._plots["embed"]["plot"]
    plot.resize(440, metrics_panel._PLOT_H)
    rect = plot._rect()
    assert plot._index_at(rect.left()) == 0
    assert plot._index_at(rect.right()) == 4
    assert plot._index_at(rect.center().x()) == 2
    assert plot._index_at(-100) == 0                  # clamped, never negative
    assert plot._index_at(10 ** 5) == 4

    picked = []
    panel.sample_clicked.connect(lambda epoch, kind: picked.append((epoch, kind)))
    panel._on_pick("embed", 3)
    assert picked == [(103.0, "embed")]
    assert panel.selected("embed") == 3


# --------------------------------------------------------------- dialog ---

@pytest.fixture()
def dialog(tmp_path, monkeypatch):
    state = tmp_path / "app_state.json"
    monkeypatch.setattr(app_state, "path", lambda: str(state))
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "session_test.md").write_text(
        BULLET.format(stamp="14:30:05.123") + "\n", encoding="utf-8")
    monkeypatch.setattr(logs_dialog, "_logs_dir", lambda: str(logs))
    monkeypatch.setattr(metrics_panel.MetricsPanel, "source_path",
                        lambda self: str(tmp_path / "engine_metrics.jsonl"))
    made = logs_dialog.LogsDialog()
    made.metrics_btn.setChecked(False)
    yield made, state
    made.close()


def test_toggle_starts_hidden_and_persists_being_on(dialog):
    made, state = dialog
    assert made.metrics_btn.isChecked() is False
    assert made.metrics.isHidden()

    made.metrics_btn.setChecked(True)
    # isVisible() is False for a child of a dialog that was never shown, so the
    # shown state is read from the widget itself.
    assert not made.metrics.isHidden()
    assert made.metrics.live()
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["ui"]["logs_metrics"] is True

    made.metrics_btn.setChecked(False)
    assert not made.metrics.live()
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["ui"]["logs_metrics"] is False


def test_reopening_restores_the_toggle(dialog, tmp_path, monkeypatch):
    made, state = dialog
    made.metrics_btn.setChecked(True)
    made.close()

    again = logs_dialog.LogsDialog()
    try:
        assert again.metrics_btn.isChecked() is True
        assert again.metrics.live()
    finally:
        again.close()


def test_clicking_a_point_jumps_to_the_log_line(dialog):
    made, _state = dialog
    today = time.localtime()
    epoch = time.mktime((today.tm_year, today.tm_mon, today.tm_mday,
                         14, 30, 6, 0, 0, -1))
    assert made._jump_to_time(epoch, "embed") is True
    assert "jumped to 14:30:05.123" in made.status.text()
    assert "+0.9" in made.status.text() or "-0.9" in made.status.text()


def test_a_click_moves_the_log_cursor_to_that_line(tmp_path, monkeypatch):
    """The jump is a real document search, not just a status message."""
    state = tmp_path / "app_state.json"
    monkeypatch.setattr(app_state, "path", lambda: str(state))
    logs = tmp_path / "logs"
    logs.mkdir()
    body = [BULLET.format(stamp="09:00:%02d.%03d" % (i, i)) for i in range(40)]
    (logs / "session_long.md").write_text("\n".join(body) + "\n",
                                           encoding="utf-8")
    monkeypatch.setattr(logs_dialog, "_logs_dir", lambda: str(logs))
    monkeypatch.setattr(metrics_panel.MetricsPanel, "source_path",
                        lambda self: str(tmp_path / "absent.jsonl"))

    made = logs_dialog.LogsDialog()
    try:
        made.view.show()
        before = made.view.textCursor().position()
        today = time.localtime()
        epoch = time.mktime((today.tm_year, today.tm_mon, today.tm_mday,
                             9, 0, 37, 0, 0, -1))
        assert made._jump_to_time(epoch, "forward") is True
        after = made.view.textCursor().position()
        assert after > before, "the cursor did not move to the record"
        assert "09:00:37.037" in made.view.textCursor().selectedText()
    finally:
        made.close()


def test_a_hidden_line_is_reported_not_silently_missed(dialog):
    made, _state = dialog
    made._levels["INFO"] = False          # the only bullet is an INFO line
    made._render()
    today = time.localtime()
    epoch = time.mktime((today.tm_year, today.tm_mon, today.tm_mday,
                         14, 30, 5, 0, 0, -1))
    assert made._jump_to_time(epoch, "embed") is False
    assert "hidden" in made.status.text()
