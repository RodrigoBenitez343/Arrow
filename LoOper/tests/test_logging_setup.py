"""Tests for logging_setup.py — the unified telemetry dispatcher.

Covers: idempotent installation, the per-instance Markdown session log
(header / grouped sections / fenced tracebacks / footer with counts),
session joining via ARROW_SESSION_ID, log_block fenced rendering, and the
console formatter fallback when no TTY / color is available.
"""

import logging
import os
import sys

import pytest

_LOOPER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LOOPER_DIR not in sys.path:
    sys.path.insert(0, _LOOPER_DIR)

import logging_setup  # noqa: E402


@pytest.fixture
def telemetry(tmp_path, monkeypatch):
    """A fresh dispatch rooted at a temp logs dir, cleaned up after use.
    Color is forced off so block rendering is deterministic in tests."""
    monkeypatch.setenv("ARROW_LOGS_DIR", str(tmp_path))
    monkeypatch.setenv("ARROW_NO_COLOR", "1")
    monkeypatch.delenv("ARROW_SESSION_ID", raising=False)
    logging_setup.reset_logging_for_tests()
    try:
        yield tmp_path
    finally:
        logging_setup.reset_logging_for_tests()


def _telemetry_handlers():
    return [
        h
        for h in logging.getLogger().handlers
        if getattr(h, "_looper_telemetry", False)
    ]


def _strip_ansi(s):
    import re as _re
    return _re.sub(r"\x1b\[[0-9;]*m", "", s)


def test_setup_is_idempotent(telemetry):
    md1 = logging_setup.setup_logging("sess-1")
    md2 = logging_setup.setup_logging("sess-2")  # guarded -> same session
    assert md1 == md2
    assert os.path.basename(md1) == "session_sess-1.md"
    # text + console + markdown handlers, installed exactly once
    assert len(_telemetry_handlers()) == 3


def test_session_md_header_sections_footer_counts(telemetry):
    md = logging_setup.setup_logging("sess-abc")
    log = logging.getLogger("tests.dispatch")
    log.info("plain info line")
    log.info("second info line")
    log.warning("a warning line")
    try:
        raise RuntimeError("kaboom")
    except RuntimeError:
        log.error("an error line", exc_info=True)
    logging_setup.close_session("unit-test-done")

    content = open(md, encoding="utf-8").read()
    assert "# LoOper Session Log" in content
    assert "## Timeline" in content
    # chronological bullets with level + component
    assert "**plain info line**" not in content
    assert "**[INFO]**" in content and "plain info line" in content
    assert "second info line" in content
    assert "**[WARNING]**" in content and "a warning line" in content
    assert "**[ERROR]**" in content and "an error line" in content
    assert "`dispatch`" in content  # short component name
    # traceback inside a fenced block
    assert "```text" in content and "Traceback (most recent call last):" in content
    assert "kaboom" in content
    # summary + footer with per-level counts
    assert "## Session Summary" in content
    assert "INFO=2" in content and "ERROR=1" in content
    assert "## Session ended" in content
    assert "unit-test-done" in content
    assert logging_setup.get_session_stats().get("ERROR") == 1


def test_chain_milestones_become_subheadings(telemetry):
    md = logging_setup.setup_logging("chain-sess")
    chain = logging.getLogger("chain_run")
    chain.info("=== CHAIN RUN STARTED ===")
    logging_setup.close_session("done")
    content = open(md, encoding="utf-8").read()
    assert "### CHAIN RUN STARTED" in content
    assert content.count("### CHAIN RUN STARTED") == 1


def test_child_process_joins_parent_session(telemetry, monkeypatch):
    md = logging_setup.setup_logging("owner-sess")
    # Simulate the API subprocess: env var already set -> joiner.
    monkeypatch.setenv("ARROW_SESSION_ID", "owner-sess")
    logging_setup.reset_logging_for_tests()
    md2 = logging_setup.setup_logging()
    assert md2 == md

    logging.getLogger("tests.join").info("joined record")
    logging_setup.close_session("child-exit")

    content = open(md, encoding="utf-8").read()
    assert "joined record" in content
    assert content.count("# LoOper Session Log") == 1  # no re-written header
    assert "## Session ended" not in content  # joiner never writes the footer


def test_log_block_renders_fenced_in_md(telemetry):
    md = logging_setup.setup_logging("block-sess")
    log = logging.getLogger("tests.blocks")
    logging_setup.log_block(log, logging.INFO, "LLM Response", "hello\nworld")
    logging_setup.close_session("done")

    content = open(md, encoding="utf-8").read()
    assert "### LLM Response" in content
    assert "```text" in content and "hello" in content and "world" in content


def test_log_block_rich_panel_marks_record(telemetry, monkeypatch):
    """When a Rich console is available, log_block prints a panel and marks
    the record so the console handler does not duplicate the block."""

    class _FakeConsole:
        def __init__(self):
            self.printed = []

        def print(self, *args, **kwargs):
            self.printed.append((args, kwargs))

        def rule(self, *args, **kwargs):
            pass

    fake = _FakeConsole()
    monkeypatch.setattr(logging_setup, "_get_rich_console", lambda: fake)
    logging_setup.setup_logging("rich-sess")
    log = logging.getLogger("tests.rich")
    logging_setup.log_block(log, logging.INFO, "Panel Title", "panel body")

    # rich received a panel with the title + body (the session banner is a
    # panel too now, so pick the log_block one by its title).
    assert fake.printed
    panel_obj = next(
        a[0] for a, k in fake.printed
        if a and "Panel" in type(a[0]).__name__
        and "Panel Title" in str(a[0].title)
    )
    assert "panel body" in str(panel_obj.renderable)

    # the rich console handler skips the rich-rendered block record
    console = next(
        h for h in _telemetry_handlers()
        if isinstance(h, logging_setup._RichConsoleHandler)
    )
    before = len(fake.printed)
    rec = logging.LogRecord(
        "tests.rich", logging.INFO, "f.py", 1,
        "=== Panel Title ===\npanel body", (), None,
    )
    rec.looper_block = ("Panel Title", "text")
    rec.looper_rich = True
    console.handle(rec)
    assert len(fake.printed) == before  # skipped
    console.handle(
        logging.LogRecord(
            "tests.rich", logging.INFO, "f.py", 1, "plain line", (), None
        )
    )
    assert len(fake.printed) == before + 1  # rendered
    # ... and it is boxed (a Rich Panel), never a naked line.
    rendered = fake.printed[-1][0][0]
    assert "Panel" in type(rendered).__name__


def test_console_block_formatting_colored():
    fmt = logging_setup._ConsoleFormatter(use_color=True)
    rec = logging.LogRecord(
        "player.test", logging.INFO, "f.py", 1,
        "=== LLM Response ===\nline1\nline2", (), None,
    )
    rec.looper_block = ("LLM Response", "text")
    out = fmt.format(rec)
    assert "\x1b[" in out  # ANSI color codes present
    clean = _strip_ansi(out)
    assert "=== LLM Response ===" in clean
    assert "line1" in clean and "line2" in clean
    # Boxed, not a bare header + indented body.
    lines = clean.splitlines()
    assert lines[0].startswith("+") and lines[-1].startswith("+")
    assert all(ln.startswith("|") for ln in lines[1:-1])


def test_every_console_record_is_boxed():
    """The console fallback boxes EVERY record — info, warning, error — so
    not a single line prints naked."""
    fmt = logging_setup._ConsoleFormatter(use_color=False)
    for level in (
        logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR,
        logging.CRITICAL,
    ):
        rec = logging.LogRecord(
            "player.test", level, "f.py", 1, "a message", (), None,
        )
        out = fmt.format(rec)
        lines = out.splitlines()
        assert lines[0].startswith("+") and lines[-1].startswith("+"), out
        assert all(ln.startswith("|") for ln in lines[1:-1]), out
        assert "a message" in out


def test_error_box_carries_the_traceback_inside():
    fmt = logging_setup._ConsoleFormatter(use_color=False)
    try:
        raise ValueError("boom")
    except ValueError:
        rec = logging.LogRecord(
            "player.test", logging.ERROR, "f.py", 1, "failed", (),
            sys.exc_info(),
        )
    out = fmt.format(rec)
    lines = out.splitlines()
    assert lines[0].startswith("+") and lines[-1].startswith("+")
    assert "Traceback (most recent call last):" in out
    assert "boom" in out


def test_console_formatter_plain_when_no_color(telemetry):
    logging_setup.setup_logging("plain-console")
    console = next(
        h for h in _telemetry_handlers()
        if isinstance(h, logging_setup._SafeStreamHandler)
    )
    assert console.formatter._use_color is False


def test_truncation_limits():
    assert logging_setup._truncate("short", 10) == "short"
    out = logging_setup._truncate("x" * 50, 10)
    assert out.startswith("x" * 10)
    assert "chars truncated" in out


def test_stdout_bridge_captures_stray_emissions(telemetry):
    """print() and stderr writes that bypass logging still land in the
    rendered console + session md as console/INFO and console/WARNING."""
    md = logging_setup.setup_logging("bridge-sess")
    print("stray print line here")
    sys.stderr.write("stray warning text\n")
    sys.stderr.flush()
    # logging still flows after the wrap (no recursion)
    logging.getLogger("tests.bridge").info("after wrap info")
    logging_setup.close_session("done")

    content = open(md, encoding="utf-8").read()
    assert "stray print line here" in content
    assert "**[INFO]**" in content and "`console`" in content
    assert "stray warning text" in content
    assert "**[WARNING]**" in content
    assert "after wrap info" in content


def test_bridge_drops_rendered_feedback_lines(telemetry):
    """A console-handler-rendered line leaking back into the bridge must be
    dropped, not re-logged — this is what caused the duplicated
    ``[INFO] console: <line>`` records in the elevated console."""
    md = logging_setup.setup_logging("fb-sess")
    sys.stdout.write("  23:34:23 [DEBUG   ] core: leaked rendered line\n")
    sys.stdout.flush()
    print("genuine stray line")
    logging_setup.close_session("done")

    content = open(md, encoding="utf-8").read()
    assert "leaked rendered line" not in content  # feedback dropped
    assert "genuine stray line" in content  # real stray output still captured


def test_log_table_renders_markdown_in_md(telemetry):
    """log_table renders a Markdown table in the session md and marks the
    record so the console handler skips the duplicate line."""
    md = logging_setup.setup_logging("table-sess")
    log = logging.getLogger("tests.tables")
    logging_setup.log_table(
        log, logging.INFO, "LLM Node Parameters",
        [("Model", "Qwen3.8-2B"), ("Max Tokens", 512)],
    )
    logging_setup.close_session("done")
    content = open(md, encoding="utf-8").read()
    assert "### LLM Node Parameters" in content
    assert "| Field | Value |" in content
    assert "| Model | Qwen3.8-2B |" in content
    assert "| Max Tokens | 512 |" in content


def test_api_subprocess_pipes_do_not_loop(tmp_path):
    """The API subprocess (CREATE_NO_WINDOW, cp1252 pipes) must not spin on
    UnicodeEncodeError when Rich renders rules/panels with non-ASCII content.
    This was the bug that starved LLM node execution (a 113k-line loop in the
    session md)."""
    import subprocess
    import sys as _sys

    script = tmp_path / "child.py"
    script.write_text(
        "import logging, os, sys\n"
        "sys.path.insert(0, " + repr(_LOOPER_DIR) + ")\n"
        "os.environ['ARROW_LOGS_DIR'] = " + repr(str(tmp_path)) + "\n"
        "import logging_setup\n"
        "logging_setup.setup_logging('child-sess')\n"
        "log = logging.getLogger('api.llamacpp')\n"
        "log.info('API server started on port 8000')\n"
        "log.error('Failed to process request: boom')\n"
        "log.error('non-ascii: emoji \\U0001F600 arrows \\u2192 box \\u2500\\u250C\\u2501')\n"
        "logging_setup.log_block(log, logging.INFO, 'LLM Response', "
        "'answer with \\U0001F600 \\u2192 \\u2500')\n"
        "print('DONE-MARKER')\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [_sys.executable, str(script)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=0x08000000,  # CREATE_NO_WINDOW, like the API subprocess
        timeout=60,
    )
    out = proc.stdout.decode("utf-8", "replace") + proc.stderr.decode(
        "utf-8", "replace"
    )
    assert proc.returncode == 0
    assert "DONE-MARKER" in out
    assert "UnicodeEncodeError" not in out
    assert "Logging error" not in out
    assert "[looper-logging]" not in out  # no handler fallback was needed
