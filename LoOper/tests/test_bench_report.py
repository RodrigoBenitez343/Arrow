"""The metrics HTML report must render from a log, and survive an empty one.

``benchmarks/report.py`` is what turns ``engine_metrics.jsonl`` into something
a human can look at, so it is tested like any other output surface: every
recorded kind gets a section, the SVG is balanced, and a missing log produces a
valid page that says why it is empty instead of raising.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BENCH = os.path.join(ROOT, "benchmarks")
if BENCH not in sys.path:
    sys.path.insert(0, BENCH)

import report  # noqa: E402


def _row(kind, t, **fields):
    row = {"kind": kind, "t": t}
    row.update(fields)
    return row


SAMPLES = [
    _row("engine_load", 100.0, engine_code=2, ms=3200.0),
    _row("embed", 101.0, engine_code=2, ms=90.0, count=3),
    _row("forward", 102.0, engine_code=1, ms=1800.0),
    _row("context_trim", 103.0, turns_dropped=1, chars_before=3000,
         chars_after=2400),
    _row("context_trim", 104.0, turns_dropped=2, chars_before=3400,
         chars_after=2400),
    _row("engine_start", 105.0, engine_code=3, ms=3600.0, ctx=2048,
         kv_bytes=50331648),
    _row("form_field", 106.0, ms=5200.0, forwards=6, kept=3, action=2),
    _row("index_purge", 107.0, blocks=12, ms=1.0),
]


def test_empty_log_is_still_a_page():
    page = report.build([])
    assert page.startswith("<!DOCTYPE html>")
    assert "No samples in the metrics log yet" in page
    assert page.count("<svg") == page.count("</svg>") == 0


def test_every_recorded_kind_gets_its_section():
    page = report.build(SAMPLES, "log.jsonl")
    for heading in ("Summary", "Cold load vs warm use", "Latency over sample",
                    "Form field cost", "Context budget",
                    "KV cache at engine start", "Recent samples"):
        assert "<h2>%s</h2>" % heading in page, heading
    assert page.count("<svg") == page.count("</svg>")
    assert "nan" not in page.lower()


def test_numbers_reach_the_page():
    page = report.build(SAMPLES, "log.jsonl")
    # engine labels come from the numeric code, so a reader can name the engine
    assert "laya" in page and "embed" in page and "llama" in page
    # the load/use ratio: 3200 ms load vs 90 ms use
    assert "35.6x" in page
    # context budget totals: 6400 - 4800 chars, 3 turns
    assert "1,600 chars removed" in page
    assert "2 trims" in page
    # KV bytes land as MB
    assert "48" in page


def test_broken_lines_are_skipped(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text('{"kind":"embed","t":1,"ms":5}\nnot json\n\n{"broken":1}\n',
                    encoding="utf-8")
    rows = report.load_rows(str(path))
    assert len(rows) == 1
    assert rows[0]["kind"] == "embed"


def test_missing_file_is_an_empty_series(tmp_path):
    assert report.load_rows(str(tmp_path / "absent.jsonl")) == []


def test_chart_geometry_stays_inside_the_viewbox():
    chart = report.line_chart([0.0, 10.0, 5.0], width=400, height=120)
    assert 'viewBox="0 0 400 120"' in chart
    for point in chart.split('points="')[1].split('"')[0].split():
        x, y = (float(v) for v in point.split(","))
        assert 0 <= x <= 400 and 0 <= y <= 120
