"""logging_setup.py — single telemetry dispatcher for LoOper.

Every LoOper process funnels all ``logging`` output through the root logger
configured here:

  * colorized console handler  — EVERY record is rendered as its own
    level-coded box (a Rich panel on the live terminal, an ASCII box
    otherwise): DEBUG dim, INFO default, WARNING yellow, ERROR/CRITICAL red,
    tracebacks inside the box — no record ever prints as a naked line;
  * rotating text log          — ``logs/automation.log`` (20 MB x 3 backups);
  * per-instance Markdown log  — ``logs/session_<timestamp>.md``, appended
    and flushed on every record (populated live while the app runs) and
    closed with a session footer on exit.

Paths are anchored to the LoOper package directory in source mode and to
the durable runtime dir (``AI.runtime_paths.get_runtime_dir()``) in frozen
builds — never cwd, which is what scattered ``automation.log`` across three
directories before.  An ``ARROW_LOGS_DIR`` env var overrides the location
(for tests / portable installs).

Child processes that inherit ``ARROW_SESSION_ID`` (e.g. the API subprocess)
append to the parent's session file instead of creating their own.
"""

import atexit
import ctypes
import logging
import os
import re
import sys
import tempfile
import threading
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Optional

_INSTALLED = False
_SESSION_MD_PATH = None
_MD_HANDLER = None
_OWNER = False
_LAST_STATS = {}
_RICH_CONSOLE = None
_RICH_CHECKED = False
_SESSION_STARTED = time.time()
_STDOUT_BRIDGE = None
_STDERR_BRIDGE = None

# Block-content truncation: the live terminal stays lean, the Markdown
# session log keeps the full payload, and the text log is the raw archive.
_CONSOLE_BLOCK_MAX = int(os.environ.get("ARROW_CONSOLE_BLOCK_MAX", "2500") or "2500")
_MD_BLOCK_MAX = int(os.environ.get("ARROW_MD_BLOCK_MAX", "40000") or "40000")


def _truncate(text: str, max_chars: int) -> str:
    if max_chars and len(text) > max_chars:
        return text[:max_chars] + f"\n...[{len(text) - max_chars} chars truncated]"
    return text


# Reusable plain formatter for traceback text in the Markdown handler.
_EXC_FORMATTER = logging.Formatter()


def resolve_logs_dir() -> str:
    """Return the directory where all telemetry files live."""
    override = os.environ.get("ARROW_LOGS_DIR")
    if override:
        return os.path.abspath(override)
    if getattr(sys, "frozen", False):
        try:
            from AI.runtime_paths import get_runtime_dir

            return os.path.join(get_runtime_dir(), "logs")
        except Exception:
            pass
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")


def _fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


# ---------------------------------------------------------------------------
# Console formatting — ANSI level codes, red tracebacks
# ---------------------------------------------------------------------------


def _enable_windows_vt() -> bool:
    """Turn on VT processing for the console so ANSI codes render."""
    try:
        h = ctypes.windll.kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not ctypes.windll.kernel32.GetConsoleMode(h, ctypes.byref(mode)):
            return False
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        ctypes.windll.kernel32.SetConsoleMode(
            h, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
        )
        return True
    except Exception:
        return False


def _console_supports_color() -> bool:
    if os.environ.get("ARROW_NO_COLOR"):
        return False
    try:
        is_tty = sys.stdout.isatty()
    except Exception:
        is_tty = False
    if not is_tty:
        # The app relaunches itself elevated (ShellExecuteW runas): the child's
        # stdout may not report as a tty even though it owns a real console
        # window.  Treat a live console window as a terminal.
        if sys.platform == "win32":
            try:
                if not ctypes.windll.kernel32.GetConsoleWindow():
                    return False
            except Exception:
                return False
        else:
            return False
    if sys.platform == "win32":
        return _enable_windows_vt()
    return True


def _get_rich_console():
    """A Rich console for the app's own terminal (elevated console included).

    Uses an explicit file handle that is never the stdout bridge, and forces
    terminal mode whenever the process owns a console window even if stdout
    does not report as a tty (the elevated relaunch case) — Rich's native
    Windows console color fallback then renders colors without VT.
    """
    global _RICH_CONSOLE, _RICH_CHECKED
    if _RICH_CHECKED:
        return _RICH_CONSOLE
    _RICH_CHECKED = True
    if os.environ.get("ARROW_NO_COLOR"):
        return None
    try:
        from rich.console import Console
    except Exception:
        return None
    target = _unwrap_stream(sys.stdout)
    try:
        is_tty = bool(target is not None and target.isatty())
    except Exception:
        is_tty = False
    has_console = False
    if sys.platform == "win32":
        try:
            has_console = bool(ctypes.windll.kernel32.GetConsoleWindow())
        except Exception:
            has_console = False
    if not (is_tty or has_console):
        # No terminal to render to — headless or piped, e.g. the API
        # subprocess spawned with CREATE_NO_WINDOW and PIPE stdout.  Building
        # a Rich console there hangs the process on its first panel write
        # (before the server can bind its port), so this process falls back
        # to the plain boxed formatter instead.
        return None
    _enable_windows_vt()
    try:
        _RICH_CONSOLE = Console(file=target, force_terminal=True)
    except Exception:
        _RICH_CONSOLE = None
    return _RICH_CONSOLE


def _has_console_window() -> bool:
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.kernel32.GetConsoleWindow())
    except Exception:
        return False


class _ConsoleFormatter(logging.Formatter):
    """Every record boxed with ASCII rules and level-coded — the plain-console
    fallback for the Rich panel the live terminal shows."""

    _STYLES = {
        logging.DEBUG: "\x1b[2;37m",
        logging.INFO: "\x1b[36m",
        logging.WARNING: "\x1b[33m",
        logging.ERROR: "\x1b[31m",
        logging.CRITICAL: "\x1b[1;31m",
    }
    _RESET = "\x1b[0m"

    def __init__(self, use_color: bool):
        super().__init__(fmt="%(message)s")
        self._use_color = use_color

    @staticmethod
    def _short_name(name: str) -> str:
        """Last segment only — the terminal stays scannable (core, session,
        code_ops, chain_run...) while the text log keeps full names."""
        return name.split(".")[-1]

    def format(self, record):
        block = getattr(record, "looper_block", None)
        if block is not None:
            return self._format_block(record, block[0], block[1])
        msg = record.getMessage()
        lines = self._wrap(msg)
        exc_text = (
            self.formatException(record.exc_info) if record.exc_info else ""
        )
        if exc_text:
            lines += self._wrap(exc_text.rstrip())
        return self._box(record, self._head(record), lines)

    @staticmethod
    def _wrap(text, width=118):
        """Wrap long lines so a box never becomes one giant border rule."""
        import textwrap
        out = []
        for raw in (str(text).splitlines() or [""]):
            out.extend(textwrap.wrap(raw, width) or [""])
        return out

    def _head(self, record) -> str:
        when = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        name = self._short_name(record.name)
        return f"{when} [{record.levelname:<8}] {name}:"

    def _box(self, record, head, body_lines):
        """Wrap ONE record in an ASCII box — the plain-console fallback for
        the Rich panel the live terminal shows.  No record may print naked."""
        lines = [head] + list(body_lines)
        width = max((len(ln) for ln in lines), default=0)
        top = "+" + "-" * (width + 2) + "+"
        out = [top]
        for ln in lines:
            out.append("| " + ln + " " * (width - len(ln)) + " |")
        out.append(top)
        text = "\n".join(out)
        if self._use_color:
            style = self._STYLES.get(record.levelno, "")
            if style:
                text = "".join(
                    f"{style}{ln}{self._RESET}"
                    for ln in text.splitlines(True)
                )
        return text

    def _format_block(self, record, title, lang):
        """Render a verbose payload (prompt, response, sources...) boxed,
        with the title as the box heading and the payload as its body."""
        msg = record.getMessage()
        content = msg.split("\n", 1)[1] if "\n" in msg else ""
        head = self._head(record) + f" === {title} ==="
        body = self._wrap(_truncate(content, _CONSOLE_BLOCK_MAX))
        return self._box(record, head, body)


# ---------------------------------------------------------------------------
# Markdown session log — timeline narration, live-appended, closed with a
# summary + footer (logs/session_<ts>.md)
# ---------------------------------------------------------------------------


class _MarkdownHandler(logging.FileHandler):
    """Per-instance Markdown session log rendered as a chronological
    timeline: bullets carry time + level + component, verbose payloads are
    fenced blocks, chain_run milestones become sub-headings, and the footer
    closes with a session summary (duration, per-level counts, per-component
    activity)."""

    def __init__(self, path: str, meta: dict, owner: bool = True):
        super().__init__(path, mode="a", encoding="utf-8")
        self._meta = meta
        self._owner = owner
        self._header_written = False
        self._footer_written = False
        self._started = time.time()
        self._counts = {}
        self._comp_counts = {}

    def write_header(self):
        if not self._owner or self._header_written:
            return
        self._header_written = True
        m = self._meta
        lines = [
            "# LoOper Session Log",
            "",
            f"- **Started**: {m['started']}",
            f"- **PID**: {m['pid']}",
            f"- **Mode**: {'frozen build' if m['frozen'] else 'source'}",
            f"- **CWD**: `{m['cwd']}`",
            f"- **Command**: `{m['cmd']}`",
            f"- **Text log**: `{m['log_file']}`",
            "",
            "---",
            "",
            "## Timeline",
            "",
        ]
        try:
            self.stream.write("".join(l + "\n" for l in lines))
            self.flush()
        except Exception:
            pass

    def emit(self, record):
        try:
            if self.stream is None or getattr(self.stream, "closed", False):
                return
            self.write_header()
            self._counts[record.levelname] = (
                self._counts.get(record.levelname, 0) + 1
            )
            name = record.name.split(".")[-1]
            self._comp_counts[name] = self._comp_counts.get(name, 0) + 1
            when = datetime.fromtimestamp(record.created).strftime(
                "%H:%M:%S.%f"
            )[:-3]
            msg = record.getMessage()
            block = getattr(record, "looper_block", None)
            table = getattr(record, "looper_table", None)
            if table is not None:
                # Structured key/value data -> Markdown table.
                lines = ["", f"### {block[0]}", "", "| Field | Value |", "|---|---|"]
                lines += [f"| {str(k)} | {str(v)} |" for k, v in table]
            elif block is not None:
                # Verbose payload: sub-heading + fenced block only (the bullet
                # would just duplicate the flattened content).
                content = msg.split("\n", 1)[1] if "\n" in msg else ""
                content = _truncate(content, _MD_BLOCK_MAX).rstrip()
                lines = ["", f"### {block[0]}", "", f"```{block[1]}", content, "```"]
            elif name == "chain_run" and msg.strip().startswith("==="):
                # Chain milestones become narrative sub-headings.
                lines = ["", f"### {msg.strip().strip('=').strip()}", ""]
            else:
                safe = msg.replace("`", "'").replace("\n", " ")
                lines = [
                    f"- `{when}` **[{record.levelname}]** `{name}` {safe}"
                ]
                if record.exc_info:
                    tb = _EXC_FORMATTER.formatException(record.exc_info)
                    lines += ["```text", tb, "```"]
            self.stream.write("\n".join(lines) + "\n")
            self.flush()
        except Exception:
            _safe_fallback(record)

    def write_footer(self, reason: str = "app exit"):
        if self._footer_written:
            return
        self._footer_written = True
        if self.stream is None or getattr(self.stream, "closed", False):
            return
        duration = time.time() - self._started
        counts = (
            ", ".join(f"{k}={v}" for k, v in sorted(self._counts.items()))
            or "none"
        )
        top_comps = sorted(
            self._comp_counts.items(), key=lambda kv: kv[1], reverse=True
        )[:8]
        comps = ", ".join(f"{k}={v}" for k, v in top_comps) or "none"
        lines = [
            "",
            "---",
            "",
            "## Session Summary",
            "",
            f"- **Duration**: {_fmt_duration(duration)}",
            f"- **Records**: {counts}",
            f"- **Components**: {comps}",
            "",
            "## Session ended",
            "",
            f"- **Ended**: {datetime.now():%Y-%m-%d %H:%M:%S}",
            f"- **Exit reason**: {reason}",
            "",
        ]
        try:
            self.stream.write("\n".join(lines))
            self.flush()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Setup / lifecycle
# ---------------------------------------------------------------------------


class _SafeStreamHandler(logging.StreamHandler):
    """Console handler that skips records its stream cannot serve: closed
    streams (late atexit logging) and blocks that Rich already rendered as a
    colored panel (``looper_rich`` records)."""

    def emit(self, record):
        try:
            if self.stream is None or getattr(self.stream, "closed", False):
                return
            if getattr(record, "looper_rich", False):
                return
            super().emit(record)
        except Exception:
            _safe_fallback(record)


class _SafeRotatingFileHandler(RotatingFileHandler):
    """Rotating text handler with the same closed-stream guard."""

    def emit(self, record):
        try:
            if self.stream is None or getattr(self.stream, "closed", False):
                return
            super().emit(record)
        except Exception:
            _safe_fallback(record)


class _RichConsoleHandler(logging.Handler):
    """Renders every record through the shared Rich console — a live,
    color-coded dashboard of the app's continuous output:

    * EACH record is its own panel, border- and title-styled by level
      (DEBUG dim, INFO default, WARNING yellow, ERROR/CRITICAL red);
    * chain_run milestones, node parameter dumps, warnings, errors and
      tracebacks all live inside that panel — nothing prints naked;
    * blocks Rich already rendered as panels (``looper_rich``) are skipped
      so a log_block/log_table payload is never drawn twice.
    """

    _STYLE = {
        logging.DEBUG: "dim",
        logging.INFO: "cyan",
        logging.WARNING: "yellow",
        logging.ERROR: "red",
        logging.CRITICAL: "bold red",
    }

    def __init__(self, console):
        super().__init__()
        self._console = console

    def emit(self, record):
        try:
            if getattr(record, "looper_rich", False):
                return
            from rich.panel import Panel
            from rich.text import Text

            con = self._console
            name = record.name.split(".")[-1]
            when = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
            style = self._STYLE.get(record.levelno, "default")
            msg = record.getMessage()
            # EVERY record is its own color-coded box — a chain milestone, a
            # node parameter dump, a warning, an error+traceback: nothing
            # prints as a naked line.
            title = Text()
            title.append(f"{when} ", style="dim")
            title.append(f"[{record.levelname}] ", style=style)
            title.append(name, style=style)
            body = Text(msg, style=style)
            if record.exc_info:
                tb = _EXC_FORMATTER.formatException(record.exc_info)
                body.append("\n" + tb.rstrip(), style="red")
            con.print(
                Panel(body, title=title, border_style=style, expand=False)
            )
        except Exception:
            _safe_fallback(record)


# Re-entries of our own rendered log lines are dropped by the stdout bridge:
# the plain formatter's "HH:MM:SS [LEVEL] component: ..." header, the ASCII box
# top ("+---") and the Rich panel corner (╭/┌/╔).  They only occur when some
# console output leaks back into the wrapped stream, which would otherwise
# loop forever.
_FEEDBACK_RE = re.compile(
    r"^\s*\d{2}:\d{2}:\d{2} \[(?:DEBUG|INFO|WARNING|ERROR|CRITICAL)\s*\]"
    r"|^\s*[+\u250c\u250f\u2554\u2557\u256d][-\u2500]"
)


def _is_bridge(stream) -> bool:
    return bool(getattr(stream, "_is_looper_bridge", False))


def _unwrap_stream(stream):
    """Return the real stream behind our bridge (if wrapped)."""
    while _is_bridge(stream):
        stream = stream._stream
    return stream


def _safe_fallback(record) -> None:
    """ASCII-safe BOXED diagnostic written DIRECTLY to the real stderr —
    never through the bridge or another handler.  Handler rendering failures
    (e.g. the legacy Windows console choking on non-ASCII) must not re-enter
    the logger, which would loop forever and starve real processing.  It keeps
    the boxed look using ASCII rules only, so it cannot itself fail."""
    try:
        head = f"[looper-logging] {record.levelname} {record.name}:"
        body = " / ".join(str(record.getMessage()).splitlines()) or ""
        width = max(len(head), len(body))
        bar = "+" + "-" * (width + 2) + "+"
        text = (
            bar + "\n"
            + "| " + head.ljust(width) + " |\n"
            + "| " + body.ljust(width) + " |\n"
            + bar
        )
        _unwrap_stream(sys.stderr).write(
            text.encode("ascii", "replace").decode("ascii") + "\n"
        )
    except Exception:
        pass


def _utf8_reconfigure() -> None:
    """Ensure stdout/stderr can carry non-ASCII (box drawing, emoji, arrows)
    without crashing on the console code page (cp1252).  Critical for the API
    subprocess, whose pipes are never reconfigured by main.py — without this,
    Rich's legacy Windows renderer raises UnicodeEncodeError on every rule."""
    for _s in (sys.stdout, sys.stderr):
        try:
            if _s is not None and hasattr(_s, "reconfigure"):
                _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


class _StdoutBridge:
    """Routes stray ``print()`` / stderr text into the logging pipeline so
    nothing bypasses the rendered console, the text log, or the session md.

    Only intercepts writes that do NOT originate from our own logging
    handlers (thread-local re-entrancy guard + rendered-line filter):
    logging output keeps flowing to the real stream untouched.
    """

    def __init__(self, stream, level, name="console"):
        self._stream = stream
        self._level = level
        self._name = name
        self._buf = ""
        self._local = threading.local()
        self._is_looper_bridge = True

    @property
    def encoding(self):
        return getattr(self._stream, "encoding", "utf-8")

    @property
    def errors(self):
        return getattr(self._stream, "errors", "replace")

    def write(self, data):
        if getattr(self._local, "active", False):
            return self._stream.write(data)
        self._buf += data
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.rstrip("\r")
            if not line:
                continue
            # Feedback-loop guard: our own rendered log lines must never be
            # re-logged (they already reached the real stream via a handler).
            if _FEEDBACK_RE.match(line):
                continue
            if line.startswith("--- Logging error ---"):
                continue
            self._local.active = True
            try:
                logging.getLogger(self._name).log(self._level, line)
            except Exception:
                try:
                    self._stream.write(line + "\n")
                except Exception:
                    pass
            finally:
                self._local.active = False
        return len(data)

    def flush(self):
        if self._buf:
            self.write("\n")
        try:
            self._stream.flush()
        except Exception:
            pass

    def isatty(self):
        try:
            return self._stream.isatty()
        except Exception:
            return False

    def fileno(self):
        return self._stream.fileno()

    def writable(self):
        return True

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _install_stdout_bridge() -> None:
    """Wrap sys.stdout/sys.stderr once so every stray emission is captured."""
    global _STDOUT_BRIDGE, _STDERR_BRIDGE
    try:
        if _STDOUT_BRIDGE is None and sys.stdout is not None \
                and not _is_bridge(sys.stdout):
            _STDOUT_BRIDGE = _StdoutBridge(sys.stdout, logging.INFO)
            sys.stdout = _STDOUT_BRIDGE
        if _STDERR_BRIDGE is None and sys.stderr is not None \
                and not _is_bridge(sys.stderr):
            _STDERR_BRIDGE = _StdoutBridge(sys.stderr, logging.WARNING)
            sys.stderr = _STDERR_BRIDGE
    except Exception:
        pass


def _print_session_banner(console, logs_dir: str, md_path: str, sid: str) -> None:
    """Dashboard header shown once when the session starts (boxed)."""
    try:
        from rich.panel import Panel
        from rich.text import Text

        console.print(
            Panel(
                Text(
                    f"PID {os.getpid()} | "
                    f"{'frozen' if getattr(sys, 'frozen', False) else 'source'} | "
                    f"logs: {logs_dir} | session md: {md_path}",
                    style="cyan",
                ),
                title=f"LoOper session {sid}",
                border_style="bold cyan",
                expand=False,
            )
        )
    except Exception:
        pass


def _print_session_summary(console, duration: str, stats: dict, reason: str) -> None:
    """Dashboard footer with per-level counts, printed when the session ends
    (boxed)."""
    try:
        from rich.panel import Panel
        from rich.text import Text

        counts = (
            "  ".join(f"{k}={v}" for k, v in sorted(stats.items()))
            or "no records"
        )
        console.print(
            Panel(
                Text(
                    f"Exit: {reason} | duration {duration} | {counts}",
                    style="cyan",
                ),
                title="Session ended",
                border_style="bold cyan",
                expand=False,
            )
        )
    except Exception:
        pass


def setup_logging(session_id: Optional[str] = None) -> str:
    """Install the unified telemetry dispatch once; return the session md path.

    ``session_id`` or the inherited ``ARROW_SESSION_ID`` env var select the
    session file.  A process that inherits the env var (the API subprocess)
    joins the parent session — it appends records but does not write its own
    header/footer.  The owner registers an atexit footer.

    The guard lives on the root logger (a process-wide singleton) so dual
    imports of the package (``player.config`` vs ``LoOper.player.config``)
    can never install a second dispatch or a second stdout bridge.
    """
    global _INSTALLED, _SESSION_MD_PATH, _MD_HANDLER, _OWNER, _SESSION_STARTED
    root = logging.getLogger()
    if getattr(root, "_looper_dispatch_installed", False):
        return getattr(root, "_looper_md_path", None) or _SESSION_MD_PATH
    if _INSTALLED:
        return _SESSION_MD_PATH
    # MUST run before any rich console / bridge is created: the API
    # subprocess's pipes default to the console code page (cp1252) and Rich's
    # legacy Windows renderer raises UnicodeEncodeError on box-drawing chars.
    _utf8_reconfigure()
    _SESSION_STARTED = time.time()

    logs_dir = resolve_logs_dir()
    try:
        os.makedirs(logs_dir, exist_ok=True)
    except Exception:
        logs_dir = os.path.join(os.path.expanduser("~"), "LoOperLogs")
        try:
            os.makedirs(logs_dir, exist_ok=True)
        except Exception:
            logs_dir = tempfile.gettempdir()

    inherited = bool(os.environ.get("ARROW_SESSION_ID"))
    sid = session_id or os.environ.get("ARROW_SESSION_ID")
    if not sid or any(ch in sid for ch in ("/", "\\", "..")):
        sid = datetime.now().strftime("%Y%m%d-%H%M%S")
    _OWNER = not inherited
    os.environ["ARROW_SESSION_ID"] = sid
    _SESSION_MD_PATH = os.path.join(logs_dir, f"session_{sid}.md")

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    text_handler = _SafeRotatingFileHandler(
        os.path.join(logs_dir, "automation.log"),
        maxBytes=20 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    text_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        )
    )
    text_handler._looper_telemetry = True  # noqa: SLF001 — marker for reset()

    # Console: Rich-rendered dashboard when a terminal is available, else the
    # plain ANSI/plain-text handler.  Streams are unwrapped so a console can
    # never be created against the stdout bridge (which would loop back).
    rich_console = _get_rich_console()
    if rich_console is not None:
        console_handler = _RichConsoleHandler(rich_console)
        if _OWNER:
            _print_session_banner(
                rich_console, logs_dir, _SESSION_MD_PATH, sid
            )
    else:
        stdout_target = _unwrap_stream(sys.stdout)
        console_handler = _SafeStreamHandler(stdout_target or None)
        console_handler.setFormatter(_ConsoleFormatter(_console_supports_color()))
    console_handler._looper_telemetry = True  # noqa: SLF001

    meta = {
        "started": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pid": os.getpid(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "cwd": os.getcwd(),
        "cmd": " ".join(sys.argv[:8]),
        "log_file": os.path.join(logs_dir, "automation.log"),
    }
    md_handler = _MarkdownHandler(_SESSION_MD_PATH, meta, owner=_OWNER)
    md_handler._looper_telemetry = True  # noqa: SLF001
    _MD_HANDLER = md_handler
    if _OWNER:
        md_handler.write_header()

    for h in (text_handler, console_handler, md_handler):
        root.addHandler(h)

    # Capture stray print()/stderr text (third-party warnings, leftover
    # prints...) so nothing bypasses the rendered console + text log + md.
    _install_stdout_bridge()

    root._looper_dispatch_installed = True
    root._looper_md_path = _SESSION_MD_PATH

    _INSTALLED = True
    if _OWNER:
        atexit.register(_finalize_session)
    try:
        logging.getLogger("logging_setup").debug(
            "console state: color=%s console_window=%s rich=%s logs_dir=%s",
            _console_supports_color(),
            _has_console_window(),
            rich_console is not None,
            logs_dir,
        )
    except Exception:
        pass
    return _SESSION_MD_PATH


def uvicorn_log_config() -> dict:
    """Minimal uvicorn dictConfig that lets uvicorn logs propagate to the
    unified dispatch (colorized console + text log + session md) instead of
    opening their own files/handlers."""
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"default": {"format": "%(levelname)s: %(message)s"}},
        "handlers": {},
        "loggers": {
            "uvicorn": {"level": "INFO", "propagate": True},
            "uvicorn.error": {"level": "INFO", "propagate": True},
            "uvicorn.access": {"level": "INFO", "propagate": True},
        },
    }


def get_session_md_path() -> Optional[str]:
    """Return the current instance's Markdown session log path."""
    return _SESSION_MD_PATH


def get_session_stats() -> dict:
    """Return per-level record counts for the current session."""
    if _MD_HANDLER is not None:
        return dict(_MD_HANDLER._counts)  # noqa: SLF001
    return dict(_LAST_STATS)


def log_block(logger, level, title, content, lang="text"):
    """Log a titled verbose payload (LLM prompt/response, context sources,
    code node stdout...) so the three sinks render it structurally:

    * console — a Rich colored panel (falls back to an indented dimmed block
      when no terminal/rich is available);
    * md      — ``### title`` + fenced code block (full, capped);
    * text log — title line + full content (raw archive).
    """
    if content is None:
        return
    text = content if isinstance(content, str) else repr(content)
    rich_shown = False
    con = _get_rich_console()
    if con is not None:
        try:
            from rich.panel import Panel

            style = {
                logging.DEBUG: "grey37",
                logging.INFO: "cyan",
                logging.WARNING: "yellow",
                logging.ERROR: "red",
                logging.CRITICAL: "bold red",
            }.get(level, "cyan")
            _rich_max = int(
                os.environ.get("ARROW_RICH_BLOCK_MAX", "8000") or "8000"
            )
            con.print(
                Panel(
                    _truncate(text, _rich_max),
                    title=title,
                    border_style=style,
                )
            )
            rich_shown = True
        except Exception:
            rich_shown = False
    extra = {"looper_block": (title, lang)}
    if rich_shown:
        extra["looper_rich"] = True
    try:
        logger.log(
            level, "=== %s ===\n%s", title, text, extra=extra,
        )
    except (KeyError, TypeError):
        logger.log(level, "=== %s ===\n%s", title, text)


def log_table(logger, level, title, rows):
    """Log structured key/value data (LLM node parameters, node execution
    info, available tools...) as a rendered table:

    * console — a Rich table (colored border by level);
    * md      — a Markdown table under ``### title``;
    * text log — ``key: value`` lines (raw archive).
    """
    if not rows:
        return
    text = "\n".join(f"{k}: {v}" for k, v in rows)
    rich_shown = False
    con = _get_rich_console()
    if con is not None:
        try:
            from rich.table import Table

            style = {
                logging.DEBUG: "grey37",
                logging.INFO: "cyan",
                logging.WARNING: "yellow",
                logging.ERROR: "red",
                logging.CRITICAL: "bold red",
            }.get(level, "cyan")
            t = Table(
                title=title, show_header=True, header_style="bold",
                border_style=style,
            )
            t.add_column("Field", style="bold")
            t.add_column("Value")
            for k, v in rows:
                t.add_row(str(k), str(v))
            con.print(t)
            rich_shown = True
        except Exception:
            rich_shown = False
    extra = {"looper_block": (title, "text"), "looper_table": list(rows)}
    if rich_shown:
        extra["looper_rich"] = True
    try:
        logger.log(level, "=== %s ===\n%s", title, text, extra=extra)
    except (KeyError, TypeError):
        logger.log(level, "=== %s ===\n%s", title, text)


def close_session(reason: str = "app exit") -> None:
    """Write the session footer (owner only), detach and close the md handler."""
    global _MD_HANDLER, _LAST_STATS
    if _MD_HANDLER is None:
        return
    try:
        _LAST_STATS = dict(_MD_HANDLER._counts)  # noqa: SLF001
        if _OWNER:
            _MD_HANDLER.write_footer(reason)
    finally:
        try:
            logging.getLogger().removeHandler(_MD_HANDLER)
        except Exception:
            pass
        try:
            _MD_HANDLER.close()
        except Exception:
            pass
        _MD_HANDLER = None


def _finalize_session() -> None:
    # No logging.shutdown() here: Python's own logging _exitHandler (registered
    # at logging import, i.e. before us) flushes every remaining handler last,
    # while our footer must be written while handlers are still alive.
    if _OWNER and _RICH_CONSOLE is not None:
        duration = _fmt_duration(time.time() - _SESSION_STARTED)
        _print_session_summary(
            _RICH_CONSOLE, duration, get_session_stats(), "app exit"
        )
    close_session("app exit")


def reset_logging_for_tests() -> None:
    """Remove telemetry handlers and reset state (test support only)."""
    global _INSTALLED, _SESSION_MD_PATH, _MD_HANDLER, _OWNER, _LAST_STATS
    global _RICH_CONSOLE, _RICH_CHECKED, _SESSION_STARTED
    global _STDOUT_BRIDGE, _STDERR_BRIDGE
    root = logging.getLogger()
    root._looper_dispatch_installed = False
    root._looper_md_path = None
    # Restore the real streams (identity-guarded so pytest capture is kept).
    if _STDOUT_BRIDGE is not None:
        if sys.stdout is _STDOUT_BRIDGE:
            sys.stdout = _STDOUT_BRIDGE._stream
        _STDOUT_BRIDGE = None
    if _STDERR_BRIDGE is not None:
        if sys.stderr is _STDERR_BRIDGE:
            sys.stderr = _STDERR_BRIDGE._stream
        _STDERR_BRIDGE = None
    _RICH_CONSOLE = None
    _RICH_CHECKED = False
    _SESSION_STARTED = time.time()
    root = logging.getLogger()
    for h in [
        h for h in root.handlers if getattr(h, "_looper_telemetry", False)
    ]:
        try:
            h.close()
        except Exception:
            pass
        root.removeHandler(h)
    _INSTALLED = False
    _SESSION_MD_PATH = None
    _MD_HANDLER = None
    _OWNER = False
    _LAST_STATS = {}
