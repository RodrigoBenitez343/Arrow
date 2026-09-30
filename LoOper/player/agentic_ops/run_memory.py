"""run_memory.py — linear, timestamped activity memory for agent mode.

ONE append-only timeline per install (SQLite): every chain run, every node
that actually executed (inputs / outputs / llm / conditions / code / form /
web / tools), every agent chat turn, and schedule lifecycle — each with a
captured timestamp.  Events carry a ``scope`` (agent | manual | schedule) so
agent-mode activity and manual executions stay separable while sharing the
one linear timeline.  Temporal questions ("what did we do yesterday") are
answered by FILTERING this record in Python; the model never computes dates
and only reads timestamped lines, so "yesterday" comes from the store, not
from recall.  The memory never drives execution — it is asked about work
that already happened.

ponytail: one ``events`` table + LIKE search.  A graph/vector projection can
be layered later over the same rows; none of it is required to answer
"what happened".

Kill switch: ``LOOPER_RUN_MEMORY=off`` (default on).
DB override: ``LOOPER_RUN_MEMORY_DB`` (tests / portable installs).
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_TEXT_CAP = 800          # chars kept per event text
_PORT_CAP = 500          # chars kept per port excerpt inside a node event
_PROMPT_EVENTS = 220     # max events handed to the answering model
DEFAULT_MODEL = "SmolLM3-Q4_K_M.gguf"

# node_type -> timeline category (the user-facing strata)
_CATEGORY = {
    'input': 'inputs',
    'output': 'outputs',
    'llm': 'llm',
    'conditional': 'condition',
    'code': 'code',
    'form_filler': 'form',
    'web_sequence': 'web',
    'context': 'context',
    'chain_import': 'tool',
    'orchestrator': 'orchestrator',
}
# Ports carrying the interesting value, in priority order.
_PORT_PRIORITY = (
    'output', 'data', 'context', 'input_context', 'trace',
    'decision', 'ctx_out',
)
# run source -> memory scope (manual vs agent-mode separation)
_SCOPE = {'agent': 'agent', 'schedule': 'schedule'}

_local = threading.local()


# --------------------------------------------------------------- plumbing

def _enabled() -> bool:
    return str(os.environ.get("LOOPER_RUN_MEMORY", "")).strip().lower() not in (
        'off', '0', 'false', 'no',
    )


def _db_path() -> str:
    override = str(os.environ.get("LOOPER_RUN_MEMORY_DB") or "").strip()
    if override:
        return override
    import sys
    if getattr(sys, 'frozen', False):
        # Frozen builds: _MEIPASS is ephemeral — use the durable runtime dir.
        try:
            from AI.runtime_paths import get_runtime_dir
            base = str(get_runtime_dir())
        except Exception:
            import tempfile
            base = os.path.join(tempfile.gettempdir(), "Arrow")
    else:
        from pathlib import Path
        base = str(Path(__file__).resolve().parent.parent.parent / 'data')
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, 'run_memory.db')


def _conn():
    """Thread-local SQLite connection (WAL); reopens when the path changes."""
    path = _db_path()
    cached = getattr(_local, 'conn', None)
    if cached is not None and getattr(_local, 'path', None) == path:
        return cached
    try:
        if cached is not None:
            cached.close()
    except Exception:
        pass
    conn = sqlite3.connect(path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS events ("
        " seq INTEGER PRIMARY KEY AUTOINCREMENT,"
        " ts REAL NOT NULL,"
        " kind TEXT NOT NULL DEFAULT '',"
        " run_id TEXT NOT NULL DEFAULT '',"
        " chain_id TEXT NOT NULL DEFAULT '',"
        " node_id TEXT NOT NULL DEFAULT '',"
        " node_type TEXT NOT NULL DEFAULT '',"
        " category TEXT NOT NULL DEFAULT '',"
        " scope TEXT NOT NULL DEFAULT '',"
        " text TEXT NOT NULL DEFAULT '',"
        " meta TEXT NOT NULL DEFAULT '')"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id)")
    try:
        # Migration for DBs created before scopes existed.
        conn.execute(
            "ALTER TABLE events ADD COLUMN scope TEXT NOT NULL DEFAULT ''")
    except Exception:
        pass
    conn.commit()
    _local.conn = conn
    _local.path = path
    return conn


def _clip(text, limit: int) -> str:
    s = str(text)
    if 'base64,' in s[:200]:
        return "<binary content omitted>"
    s = ' '.join(s.split())
    if len(s) > limit:
        s = s[:limit] + '…'
    return s


def record(kind: str, text: str = "", *, run_id: str = "", chain_id: str = "",
           node_id: str = "", node_type: str = "", category: str = "",
           scope: str = "", meta=None) -> None:
    """Append one event to the timeline.  Never raises."""
    if not _enabled():
        return
    try:
        conn = _conn()
        conn.execute(
            "INSERT INTO events (ts, kind, run_id, chain_id, node_id, node_type,"
            " category, scope, text, meta) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (time.time(), str(kind), str(run_id), str(chain_id), str(node_id),
             str(node_type), str(category), str(scope), _clip(text, _TEXT_CAP),
             json.dumps(meta, ensure_ascii=False) if meta else ""),
        )
        conn.commit()
    except Exception as e:
        logger.debug("run_memory write failed: %s", e)


# ------------------------------------------------------------ run tracking

def begin_run(chain_path: str, source: str = "play", query: str = "") -> str:
    """Open a run on the timeline; returns its id (attaches node events)."""
    run_id = uuid.uuid4().hex[:12]
    scope = _SCOPE.get(str(source or '').strip().lower(), 'manual')
    try:
        stack = getattr(_local, 'run_stack', None)
        if stack is None:
            stack = []
            _local.run_stack = stack
        stack.append((run_id, scope))
        chain_id = os.path.splitext(os.path.basename(str(chain_path)))[0]
        _q = _clip(str(query).strip(), 200) if query else ""
        text = f"{chain_id} ({source or 'play'})"
        if _q:
            text += f" — request: {_q}"
        record('run_start', text, run_id=run_id, chain_id=chain_id,
               category='run', scope=scope,
               meta={'path': str(chain_path), 'source': source, 'query': _q})
        # An agent-mode run triggered by a chat turn: remember it so the
        # chat record (written right AFTER the chain returns, same thread,
        # same query string) can LINK the turn to the run it produced.
        if scope == 'agent' and _q:
            _local.last_agent_run_id = run_id
            _local.last_agent_query = _q
    except Exception:
        pass
    return run_id


def end_run(run_id: str, ok: bool = True, stopped: bool = False) -> None:
    """Close a run: status + duration since its run_start."""
    try:
        stack = getattr(_local, 'run_stack', None)
        scope = ''
        if stack:
            for i, entry in enumerate(stack):
                if entry[0] == run_id:
                    scope = entry[1]
                    del stack[i]
                    break
        duration = None
        try:
            conn = _conn()
            row = conn.execute(
                "SELECT ts FROM events WHERE run_id=? AND kind='run_start' "
                "ORDER BY seq ASC LIMIT 1", (str(run_id),),
            ).fetchone()
            if row:
                duration = round(time.time() - float(row[0]), 1)
        except Exception:
            pass
        status = 'stopped' if stopped else ('ok' if ok else 'failed')
        text = f"finished: {status}"
        if duration is not None:
            text += f" ({duration}s)"
        record('run_end', text, run_id=run_id, category='run', scope=scope,
               meta={'ok': bool(ok), 'stopped': bool(stopped),
                     'duration_s': duration})
    except Exception:
        pass


def current_run_id() -> str:
    try:
        stack = getattr(_local, 'run_stack', None)
        return stack[-1][0] if stack else ""
    except Exception:
        return ""


def current_scope() -> str:
    try:
        stack = getattr(_local, 'run_stack', None)
        return stack[-1][1] if stack else ""
    except Exception:
        return ""


# --------------------------------------------------------------- capture

def record_node(*, chain_id, node_id, node_type, label="", ports=None,
                conditional_result=None) -> None:
    """Record one executed node with its interesting port values.

    Only nodes that actually ran reach this (it is called on completion):
    skipped branches simply have no events — absence is the record.
    """
    if not _enabled():
        return
    try:
        run_id = current_run_id()
        if not run_id:
            return  # outside a tracked run (ad-hoc executor use)
        category = _CATEGORY.get(str(node_type or ''), str(node_type or ''))
        name = str(label or '').strip() or str(node_id)
        parts = []
        for port in _PORT_PRIORITY:
            val = (ports or {}).get(port)
            if val is None:
                continue
            if port == 'decision':
                parts.append(f"decision={bool(val)}")
                continue
            if isinstance(val, (bytes, bytearray)):
                parts.append(f"{port}=<binary>")
                continue
            s = str(val).strip()
            if not s:
                continue
            parts.append(f"{port}={_clip(s, _PORT_CAP)}")
        if conditional_result is not None:
            category = 'condition'
            parts.append(f"branch={'true' if conditional_result else 'false'}")
        # Output nodes may carry PREDEFINED static text (used to mark dead
        # ends / errors).  It rides in output_meta, not in the output value
        # (which can be empty on those branches) — record it so the marker
        # lands on the timeline whenever the node executes.
        _meta = (ports or {}).get('output_meta')
        if isinstance(_meta, dict):
            note = str(_meta.get('static_text') or '').strip()
            if not note:
                _spoken = str(_meta.get('spoken_text') or '').strip()
                _out = str((ports or {}).get('output') or '').strip()
                note = _spoken if _spoken and _spoken != _out else ''
            if note and note != str((ports or {}).get('output') or '').strip():
                parts.append(f"note={_clip(note, _PORT_CAP)}")
        text = f"[{name}] " + ("; ".join(parts) if parts else "ran")
        record('node', text, run_id=run_id, chain_id=str(chain_id),
               node_id=str(node_id), node_type=str(node_type or ''),
               category=category, scope=current_scope())
    except Exception:
        pass


def record_chat(query: str, response: str = "", scope: str = 'agent') -> None:
    """Record one agent chat turn (user + final reply) on the timeline.

    When the turn triggered an agent-mode chain run, both events carry the
    run's id in their meta — verified by comparing the run's recorded
    request text with this turn's query, so a stale marker can never link
    the wrong run (the graph draws chat -> run from it).
    """
    try:
        link = ''
        try:
            rid = str(getattr(_local, 'last_agent_run_id', '') or '')
            expected = str(getattr(_local, 'last_agent_query', '') or '')
            # Consume the marker: each turn links at most once.
            _local.last_agent_run_id = ''
            _local.last_agent_query = ''
            if rid and query and expected \
                    and _clip(str(query).strip(), 200) == expected:
                link = rid
        except Exception:
            link = ''
        if query:
            record('chat', f"User: {str(query).strip()}", category='chat',
                   scope=scope, meta={'role': 'user', 'run_id': link})
        if response:
            cleaned = re.sub(
                r'<!--\s*goal_id:[^\s>]+\s*-->', '', str(response),
            ).strip()
            if cleaned:
                record('chat', f"Agent: {cleaned}", category='chat',
                       scope=scope, meta={'role': 'agent', 'run_id': link})
    except Exception:
        pass


def record_schedule(schedule_id: str, chain_path: str, action: str) -> None:
    """Record schedule lifecycle (fired / completed) on the timeline."""
    try:
        chain_id = os.path.splitext(os.path.basename(str(chain_path)))[0]
        record('schedule', f"{chain_id}: {action}", chain_id=chain_id,
               category='schedule', scope='schedule',
               meta={'schedule_id': str(schedule_id)})
    except Exception:
        pass


# ---------------------------------------------------- time-range resolving

def _epoch(dt: datetime) -> float:
    return dt.timestamp()


def resolve_range(day=None, since=None, until=None, ref=None):
    """(start_epoch, end_epoch) for a day keyword / ISO date, else (None, None).

    The date math lives HERE (Python), against the real local clock — the
    model never derives "yesterday" by itself.
    """
    if since is not None or until is not None:
        s = _epoch(since) if isinstance(since, datetime) else (
            float(since) if since is not None else None)
        e = _epoch(until) if isinstance(until, datetime) else (
            float(until) if until is not None else None)
        return s, e
    raw = str(day or '').strip().lower()
    if not raw:
        return None, None
    ref = ref or datetime.now()

    def _midnight(dt):
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)

    today = _midnight(ref)
    if raw == 'today':
        return _epoch(today), _epoch(today + timedelta(days=1))
    if raw == 'yesterday':
        return _epoch(today - timedelta(days=1)), _epoch(today)
    if raw in ('the day before yesterday', 'day before yesterday'):
        return _epoch(today - timedelta(days=2)), _epoch(today - timedelta(days=1))
    if raw == 'this week':
        start = today - timedelta(days=ref.weekday())
        return _epoch(start), _epoch(today + timedelta(days=1))
    if raw == 'last week':
        start = today - timedelta(days=ref.weekday() + 7)
        return _epoch(start), _epoch(start + timedelta(days=7))
    m = re.match(r'^last (\d+) (hours?|days?|weeks?)$', raw)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        delta = (timedelta(hours=n) if unit.startswith('hour')
                 else timedelta(days=n * 7) if unit.startswith('week')
                 else timedelta(days=n))
        return _epoch(ref - delta), _epoch(ref + timedelta(minutes=1))
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})$', raw)
    if m:
        start = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return _epoch(start), _epoch(start + timedelta(days=1))
    return None, None


def range_from_question(question, ref=None):
    """Parse a question's temporal words into an explicit window."""
    q = str(question or '').lower()
    if 'day before yesterday' in q:
        return resolve_range('the day before yesterday', ref=ref)
    if 'yesterday' in q:
        return resolve_range('yesterday', ref=ref)
    if 'today' in q:
        return resolve_range('today', ref=ref)
    if 'last week' in q:
        return resolve_range('last week', ref=ref)
    if 'this week' in q:
        return resolve_range('this week', ref=ref)
    m = re.search(r'\blast (\d+) (hours?|days?|weeks?)\b', q)
    if m:
        return resolve_range(f"last {m.group(1)} {m.group(2)}", ref=ref)
    m = re.search(r'\b(\d{4}-\d{2}-\d{2})\b', q)
    if m:
        return resolve_range(m.group(1), ref=ref)
    return None, None


# -------------------------------------------------------------- retrieval

def _fetch(kind=None, chain=None, category=None, text_contains='',
           since=None, until=None, limit=300, scope=None, run_id=None,
           seq=None):
    if not _enabled():
        return []
    try:
        conn = _conn()
        q = ("SELECT seq, ts, kind, run_id, chain_id, node_type, category, text,"
             " scope FROM events WHERE 1=1")
        args = []
        if kind:
            q += " AND kind=?"
            args.append(str(kind))
        if chain:
            q += " AND chain_id LIKE ?"
            args.append(f"%{chain}%")
        if category:
            q += " AND category=?"
            args.append(str(category))
        if scope:
            q += " AND scope=?"
            args.append(str(scope))
        if run_id:
            q += " AND run_id=?"
            args.append(str(run_id))
        if seq is not None:
            q += " AND seq=?"
            args.append(int(seq))
        if text_contains:
            q += " AND text LIKE ?"
            args.append(f"%{text_contains}%")
        if since is not None:
            q += " AND ts>=?"
            args.append(float(since))
        if until is not None:
            q += " AND ts<?"
            args.append(float(until))
        q += " ORDER BY seq DESC LIMIT ?"
        args.append(int(limit))
        rows = conn.execute(q, args).fetchall()
        return list(reversed(rows))
    except Exception as e:
        logger.debug("run_memory fetch failed: %s", e)
        return []


def _fmt(rows) -> str:
    lines = []
    for (_seq, ts, kind, _run_id, _chain_id, node_type, category, text,
         scope) in rows:
        when = datetime.fromtimestamp(float(ts)).strftime('%Y-%m-%d %H:%M')
        tag = (str(category or node_type).upper() if kind == 'node'
               else str(kind).upper())
        prefix = f"[{scope}] " if scope else ""
        lines.append(f"{when}  {prefix}{tag:<12} {text}")
    return "\n".join(lines)


def timeline(day=None, since=None, until=None, kind=None, chain=None,
             category=None, limit=300, scope=None, run_id=None) -> str:
    """Formatted chronological timeline for a window/filter ('' when empty)."""
    s, e = resolve_range(day, since=since, until=until)
    return _fmt(_fetch(kind=kind, chain=chain, category=category,
                       since=s, until=e, limit=limit, scope=scope,
                       run_id=run_id))


def query(text='', category=None, chain=None, day=None, since=None,
          until=None, limit=150, scope=None) -> str:
    """Keyword + category/chain/day filtered timeline ('' when empty)."""
    s, e = resolve_range(day, since=since, until=until)
    return _fmt(_fetch(text_contains=str(text or '').strip(), category=category,
                       chain=chain, since=s, until=e, limit=limit, scope=scope))


# ---------------------------------------------------------- memory graph

def graph_data(day=None, scope=None, limit_runs=80, limit_chats=150):
    """Nodes + edges projection of the recorded activity (the memory graph).

    A pure VIEW over the same ``events`` table — the timeline stays the
    source of truth.  DAY nodes group RUN nodes (episodes, status-colored)
    and CHAT nodes (turns); RUN\u2192CHAIN edges mark every chain that actually
    executed inside a run, so recurring chains read as knowledge hubs.
    Nothing that was not recorded gets a node.
    """
    out = {'nodes': [], 'edges': [], 'counts': {}}
    if not _enabled():
        return out
    try:
        conn = _conn()
        since, until = resolve_range(day)
        base, args = "1=1", []
        if scope:
            base += " AND scope=?"
            args.append(str(scope))
        if since is not None:
            base += " AND ts>=?"
            args.append(float(since))
        if until is not None:
            base += " AND ts<?"
            args.append(float(until))

        run_rows = conn.execute(
            "SELECT run_id, chain_id, MIN(ts), text FROM events "
            f"WHERE {base} AND kind='run_start' GROUP BY run_id "
            "ORDER BY MIN(ts) DESC LIMIT ?",
            (*args, int(limit_runs))).fetchall()
        # SQLite's bare-column rule: with MIN(ts), ``text`` is the run_start
        # text (it carries "... — request: <query>" when the run had one).
        rid_set = {str(r[0]) for r in run_rows}

        statuses = {}
        for rid, meta in conn.execute(
                f"SELECT run_id, meta FROM events WHERE {base} AND kind='run_end'",
                args).fetchall():
            if str(rid) in rid_set:
                try:
                    statuses[str(rid)] = json.loads(meta or '{}')
                except Exception:
                    statuses[str(rid)] = {}

        run_chains = {}
        chain_events, chain_runs = {}, {}
        for rid, cid, cnt in conn.execute(
                "SELECT run_id, chain_id, COUNT(*) FROM events "
                f"WHERE {base} AND kind='node' GROUP BY run_id, chain_id",
                args).fetchall():
            rid, cid, cnt = str(rid), str(cid), int(cnt)
            if rid not in rid_set or not cid:
                continue
            run_chains.setdefault(rid, {})[cid] = cnt
            chain_events[cid] = chain_events.get(cid, 0) + cnt
            chain_runs.setdefault(cid, set()).add(rid)
        # A run whose root chain recorded no node events still exists as a
        # chain (e.g. it failed before executing anything).
        for row in run_rows:
            if str(row[1]):
                chain_events.setdefault(str(row[1]), 0)

        chat_rows = conn.execute(
            "SELECT seq, ts, text, meta FROM events "
            f"WHERE {base} AND kind='chat' ORDER BY seq DESC LIMIT ?",
            (*args, int(limit_chats))).fetchall()
        chain_last = {}
        for cid, ts in conn.execute(
                "SELECT chain_id, MAX(ts) FROM events "
                f"WHERE {base} AND chain_id != '' GROUP BY chain_id",
                args).fetchall():
            chain_last[str(cid)] = float(ts)

        def _day_of(ts):
            return datetime.fromtimestamp(float(ts)).strftime('%Y-%m-%d')

        nodes, edges, days = [], [], {}

        def _day_node(d):
            if d not in days:
                days[d] = {'id': f"day:{d}", 'kind': 'day', 'key': d,
                           'label': d, 'runs': 0, 'chats': 0}
                nodes.append(days[d])
            return days[d]

        for rid, cid, ts, run_text in sorted(run_rows, key=lambda r: float(r[2])):
            rid, cid = str(rid), str(cid)
            request = ''
            _m = re.search(r'— request:\s*(.+)$', str(run_text or ''))
            if _m:
                request = _m.group(1).strip()[:160]
            st = statuses.get(rid) or {}
            node = {
                'id': f"run:{rid}", 'kind': 'run', 'key': rid, 'label': cid,
                'ts': float(ts), 'date': _day_of(ts),
                'status': ('stopped' if st.get('stopped')
                           else 'ok' if st.get('ok', True) else 'failed'),
                'duration_s': st.get('duration_s'),
                'events': sum(run_chains.get(rid, {}).values()),
                'chains': sorted(run_chains.get(rid, {}).keys()),
                'request': request,
            }
            nodes.append(node)
            d = _day_node(node['date'])
            d['runs'] += 1
            edges.append({'source': d['id'], 'target': node['id'],
                          'kind': 'day-run'})

        for cid in sorted(chain_events, key=lambda c: (-chain_events[c], c)):
            nodes.append({'id': f"chain:{cid}", 'kind': 'chain', 'key': cid,
                          'label': cid, 'events': chain_events[cid],
                          'runs': len(chain_runs.get(cid, ())),
                          'last_ts': chain_last.get(cid)})
        for rid, chains in run_chains.items():
            for cid, cnt in chains.items():
                # ``count`` = how many node events that chain contributed to
                # the run — the graph renders it as edge strength.
                edges.append({'source': f"run:{rid}", 'target': f"chain:{cid}",
                              'kind': 'run-chain', 'count': int(cnt)})

        chat_count = 0
        for seq, ts, text, meta in sorted(chat_rows, key=lambda r: float(r[1])):
            role = ''
            run_link = ''
            try:
                parsed = json.loads(meta or '{}')
                role = str(parsed.get('role') or '')
                run_link = str(parsed.get('run_id') or '')
            except Exception:
                role = ''
                run_link = ''
            node = {'id': f"chat:{seq}", 'kind': 'chat', 'key': str(seq),
                    'label': _clip(text, 80), 'ts': float(ts),
                    'date': _day_of(ts), 'role': role or 'user',
                    'run': run_link}
            nodes.append(node)
            chat_count += 1
            d = _day_node(node['date'])
            d['chats'] += 1
            edges.append({'source': d['id'], 'target': node['id'],
                          'kind': 'day-chat'})
            # The turn PRODUCED this run (recorded + verified at write time).
            if run_link and run_link in rid_set:
                edges.append({'source': node['id'],
                              'target': f"run:{run_link}",
                              'kind': 'chat-run'})

        total_runs = conn.execute(
            f"SELECT COUNT(DISTINCT run_id) FROM events "
            f"WHERE {base} AND kind='run_start'", args).fetchone()[0]
        out = {'nodes': nodes, 'edges': edges,
               'counts': {'runs': int(total_runs or 0),
                          'runs_shown': len(rid_set),
                          'chats_shown': chat_count,
                          'chains': len(chain_events)}}
    except Exception as e:
        logger.debug("run_memory graph_data failed: %s", e)
    return out


def graph_detail(kind, key, scope=None) -> str:
    """Readable audit text for one memory-graph node (the details pane)."""
    kind = str(kind or '').strip().lower()
    key = str(key or '').strip()
    scope = scope or None
    try:
        if kind == 'run':
            body = timeline(run_id=key, limit=250, scope=scope)
            head = f"Run {key}"
        elif kind == 'chain':
            body = timeline(chain=key, limit=200, scope=scope)
            head = f"Chain '{key}' — most recent events"
        elif kind == 'day':
            body = timeline(day=key, limit=300, scope=scope)
            head = f"Activity for {key}"
        elif kind == 'chat':
            body = _fmt(_fetch(seq=key, limit=1))
            head = "Chat turn"
        else:
            return "(unknown node)"
        return f"{head}\n\n{body or '(no recorded events)'}"
    except Exception as e:
        return f"(details unavailable: {e})"


# ----------------------------------------------------------- memory chat

def answer_question(question: str, stop_flag=None, model: str = "",
                    scope: str = "") -> str:
    """Answer a question strictly from the recorded activity (memory chat)."""
    question = str(question or '').strip()
    if not question:
        return "Ask me what happened and I'll answer from the recorded activity."
    if not _enabled():
        return "Run memory is disabled (LOOPER_RUN_MEMORY=off)."
    if stop_flag and stop_flag():
        return "Cancelled."

    if not scope:
        ql = question.lower()
        if 'manual' in ql or 'myself' in ql:
            scope = 'manual'
        elif 'agent' in ql or 'coworker' in ql:
            scope = 'agent'

    since, until = range_from_question(question)
    rows = _fetch(since=since, until=until, limit=_PROMPT_EVENTS, scope=scope)
    if not rows and (since is not None or until is not None):
        # The asked window is empty — say so, and show the latest records
        # instead of letting the model free-associate.
        recent = _fetch(limit=60, scope=scope)
        head = "No activity recorded in that period."
        if recent:
            head += " Most recent events:\n\n" + _fmt(recent)
        return head
    if not rows:
        return "No recorded activity yet."
    records = _fmt(rows)

    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    system = (
        "You answer questions strictly from the ACTIVITY RECORD below. Only "
        "state what the records show; never invent events, times, or results. "
        "If the answer is not in the records, say you don't have it. Mention "
        "the dates and times you cite."
    )
    scope_note = f" ({scope} only)" if scope else ""
    prompt = (
        f"Current local time: {now}\n\n"
        f"ACTIVITY RECORD (chronological{scope_note}):\n{records}\n\n"
        f"Question: {question}\nANSWER:"
    )
    try:
        from . import description_repair as _dr
        out = _dr._call_local_model(
            prompt, system, model or DEFAULT_MODEL, max_tokens=400,
        )
        out = str(out or '').strip()
        if out:
            return out
    except Exception as e:
        logger.warning("run_memory: answering model unavailable: %s", e)
    # Model down — the records themselves are still the honest answer.
    return "Local model unavailable — here are the records:\n\n" + records
