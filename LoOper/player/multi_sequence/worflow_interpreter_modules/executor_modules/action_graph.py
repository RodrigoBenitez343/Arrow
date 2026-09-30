"""action_graph.py — derived action graph of a chain file (shadow substrate).

What the executor READS is a precise instruction set: web-sequence actions are
recorded verb + literal target ("click" on text "Easy Apply"), a sequence node
names a recorded routine, a handle node carries its goal, input/llm/
conditional/mcp/form/code nodes carry labels.  This module renders that
instruction set — deterministically, from the chain JSON (plus the recorded
web-sequence files it references) — into typed ENTITIES plus the threads
between them:

  * entities: one per executable node — ``line`` is the rendered action,
    with kind / modality / verbs / objects split out for matching;
  * edges: the executor thread (output-port connections between rendered
    nodes; tool ports are router-invoked and excluded), plus a deterministic
    ``order`` (topological, declaration order as the tie-break);
  * imports: chain-import nodes are the router-invoked tools — collapsed ONE
    level (the child's own rendered lines ride as ``child_lines``; cycles and
    self-imports are guarded by a visited set).

Nothing routes on this graph: the orchestrator logs a SHADOW summary per
activation (coverage + rendered lines), and the plan projection logs every
planned directive against these action lines (matched or UNMATCHED) — both
are measurement only, nothing consumes the graph for routing yet.  Graphs
are content-hash cached — a file's graph is rebuilt only when its bytes
change.

Kill switch: LOOPER_ORCH_GRAPH=off silences the orchestrator's shadow block
(this module stays importable either way).
"""

import hashlib
import json
import logging
import os

logger = logging.getLogger(__name__)

_MAX_CACHE = 32          # content-hash entries kept (insertion-ordered evict)
_ASSET_CAP = 12          # rendered action lines kept per web sequence
_SUMMARY_LINES = 12      # entity lines per graph in the shadow log

_CACHE = {}

# Chain-JSON node lists that carry an EXECUTABLE action (context/output/
# container/orchestrator nodes are structure, never steps) -> entity kind.
_EXEC_LISTS = (
    ('sequences', 'sequence'),
    ('web_sequences', 'web_sequence'),
    ('handle_nodes', 'handle'),
    ('input_nodes', 'input'),
    ('llm_nodes', 'llm'),
    ('conditional_nodes', 'conditional'),
    ('mcp_nodes', 'mcp'),
    ('form_filler_nodes', 'form_filler'),
    ('code_nodes', 'code'),
)

# Ports that carry ROUTER edges (tool/brains/chains), not the executor thread.
_TOOL_PORTS = ('tools', 'brains', 'chains')


# --------------------------------------------------------------- helpers

def _tokens(text):
    """Lowercase word tokens (letters/digits, in-word apostrophe)."""
    out = []
    buf = []
    for ch in str(text or '').lower():
        if ch.isalnum() or ch == "'":
            buf.append(ch)
        elif buf:
            out.append(''.join(buf))
            buf = []
    if buf:
        out.append(''.join(buf))
    return [t for t in out if len(t) > 1]


def _first_text(data, keys, cap=120):
    for key in keys:
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()[:cap]
    return ''


def _read_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _md5(path):
    try:
        with open(path, 'rb') as f:
            return hashlib.md5(f.read()).hexdigest()
    except Exception:
        return ''


def _looper_root():
    """LoOper/ root (the folder that holds chains/ and web_sequences/)."""
    try:
        here = os.path.abspath(__file__)
        for _ in range(5):
            here = os.path.dirname(here)
        return here
    except Exception:
        return ''


def _resolve_asset(chain_dir, ref, subdir, require_mode=None):
    """First existing candidate for a bare/relative asset ref ('' when none).

    Bare names search the asset folder FIRST (mirroring the executor's own
    order for web sessions), then the chain dir — a chain file that happens
    to share a web sequence's name must never win (measured 2026-09-25:
    'linkedin.json' resolved to the CHAIN chains/linkedin.json and every
    child line degraded to the bare fallback).  ``require_mode`` pins the
    executor's own guard: a web ref only resolves inside a file carrying
    that mode marker.  A miss degrades to the bare name in the rendered
    line — the graph never invents a path.
    """
    ref = str(ref or '').strip()
    if not ref:
        return ''
    base = os.path.basename(ref)
    is_bare = (base == ref and '/' not in ref and '\\' not in ref)
    cands = []
    if os.path.isabs(ref):
        cands.append(ref)
    elif is_bare and subdir:
        root = _looper_root()
        if root:
            cands.append(os.path.join(root, subdir, base))
        cands.append(os.path.join(chain_dir, subdir, base))
        cands.append(os.path.join(os.getcwd(), subdir, base))
        cands.append(os.path.join(chain_dir, ref))
    else:
        cands.append(os.path.join(chain_dir, ref))
        if subdir:
            cands.append(os.path.join(chain_dir, subdir, base))
    for cand in cands:
        try:
            if not cand or not os.path.isfile(cand):
                continue
            if require_mode:
                data = _read_json(cand)
                if not isinstance(data, dict) \
                        or str(data.get('mode') or '') != require_mode:
                    continue
            return cand
        except Exception:
            continue
    return ''


# -------------------------------------------------------------- rendering

def render_web_sequence_actions(seq_path, cap=_ASSET_CAP):
    """(lines, verbs) from a recorded web sequence's OWN action list.

    The executor replays exactly these: type + the locator's literal text —
    the richest routing material this library has.
    """
    cfg = _read_json(seq_path) or {}
    lines, verbs = [], []
    for act in list(cfg.get('actions') or [])[:cap]:
        if not isinstance(act, dict):
            continue
        verb = str(act.get('type') or '').strip().lower()
        if not verb:
            continue
        loc = act.get('locator') if isinstance(act.get('locator'), dict) else {}
        target = ''
        for key in ('text', 'name', 'placeholder', 'aria_label'):
            val = loc.get(key)
            if isinstance(val, str) and val.strip():
                target = val.strip()[:80]
                break
        lines.append(f'{verb} "{target}"' if target else verb)
        verbs.append(verb)
    return lines, verbs


def _render_node(ntype, data, chain_dir, fallback_id):
    """One chain node -> typed action entity (None when it carries nothing)."""
    node_id = str(data.get('node_id') or data.get('id') or '') or fallback_id
    line = ''
    verbs = []
    modality = ntype
    steps = []
    if ntype == 'web_sequence':
        ref = _first_text(data, ('session_file', 'file', 'name'), 200)
        stem = os.path.splitext(os.path.basename(ref))[0] if ref else ''
        seq_path = (_resolve_asset(chain_dir, ref, 'web_sequences',
                                   require_mode='web') if ref else '')
        if seq_path:
            steps, verbs = render_web_sequence_actions(seq_path)
        if steps:
            line = '; '.join(steps)[:200]
        elif stem:
            line = f'run web sequence "{stem}"'
            verbs = ['run']
        modality = 'web'
    elif ntype == 'sequence':
        ref = _first_text(data, ('sequence_file', 'name', 'label'), 200)
        stem = os.path.splitext(os.path.basename(ref))[0] if ref else ''
        if not stem:
            return None
        line = f'run sequence "{stem}"'
        verbs = ['run']
        modality = 'desktop'
    elif ntype == 'handle':
        verb = _first_text(data, ('action_type',), 40).lower() or 'click'
        target = _first_text(data, ('goal_description', 'label'), 120)
        line = f'{verb} "{target}"' if target else f'{verb} (vision)'
        verbs = [verb]
        modality = 'vision'
    elif ntype == 'input':
        label = _first_text(data, ('label',), 120)
        if not label:
            return None
        line = f'needs "{label}"'
        verbs = ['needs']
    elif ntype == 'llm':
        label = _first_text(data, ('label',), 120)
        source = _first_text(data, ('input_source',), 40).lower()
        if label:
            line = f'answer "{label}"'
            verbs = ['answer']
        elif source and source not in ('none', '0'):
            line = f'answer ({source} input)'
            verbs = ['answer']
        else:
            return None
        if source == 'ocr':
            modality = 'ocr'
    elif ntype == 'conditional':
        name = _first_text(data, ('label', 'condition_type'), 80)
        if not name:
            return None
        line = f'check {name}'
        verbs = ['check']
    elif ntype == 'mcp':
        tool = _first_text(data, ('tool_name', 'label'), 80)
        if not tool:
            return None
        line = f'call mcp "{tool}"'
        verbs = ['call']
    elif ntype == 'form_filler':
        name = _first_text(data, ('label', 'mode'), 80)
        if not name:
            return None
        line = f'fill form "{name}"'
        verbs = ['fill']
    elif ntype == 'code':
        label = _first_text(data, ('label',), 80)
        if not label:
            return None
        line = f'compute "{label}"'
        verbs = ['compute']
    if not line:
        return None
    objects = [t for t in _tokens(line) if t not in set(verbs)]
    ent = {'id': node_id, 'kind': ntype, 'line': line,
           'verbs': verbs, 'objects': objects, 'modality': modality}
    if steps:
        ent['steps'] = steps
    return ent


# ----------------------------------------------------------------- graph

def _node_connections(node):
    """(target_node_id, input_port) pairs from a chain node's connections.

    Both chain-JSON shapes are accepted: the flat list the files carry, and
    the port-dict the built graph uses.  Tool ports are excluded — those
    edges are the ROUTER's, not the executor thread's.
    """
    conns = node.get('connections')
    out = []
    if isinstance(conns, dict):
        for port, items in conns.items():
            if str(port) in _TOOL_PORTS:
                continue
            for c in (items or []):
                if isinstance(c, dict) and c.get('node_id'):
                    out.append((str(c.get('node_id')), str(port)))
    elif isinstance(conns, list):
        for c in conns:
            if not isinstance(c, dict):
                continue
            if str(c.get('input_port') or '') in _TOOL_PORTS:
                continue
            tgt = c.get('target_node_id')
            if tgt:
                out.append((str(tgt), str(c.get('input_port') or '')))
    return out


def _thread_order(ids, edges):
    """Deterministic executor order: topological; declaration order breaks ties."""
    incoming = {i: 0 for i in ids}
    adj = {i: [] for i in ids}
    for a, b in edges:
        if a in incoming and b in incoming:
            adj[a].append(b)
            incoming[b] += 1
    order = [i for i in ids if incoming[i] == 0]
    seen = set(order)
    idx = 0
    while idx < len(order):
        cur = order[idx]
        idx += 1
        for nxt in adj.get(cur, []):
            incoming[nxt] -= 1
            if incoming[nxt] == 0 and nxt not in seen:
                order.append(nxt)
                seen.add(nxt)
    for i in ids:  # unreached (cycles / isolated): declaration order
        if i not in seen:
            order.append(i)
            seen.add(i)
    return order


def _build(path, digest, visited, collapse):
    cfg = _read_json(path)
    if not isinstance(cfg, dict):
        return None
    chain_dir = os.path.dirname(os.path.abspath(path))
    entities = []
    skipped = {}
    node_ids = {}
    for key, ntype in _EXEC_LISTS:
        for i, node in enumerate(cfg.get(key) or []):
            if not isinstance(node, dict):
                continue
            data = node.get('data') if isinstance(node.get('data'), dict) else node
            ent = _render_node(ntype, data, chain_dir, f'{ntype}#{i}')
            if ent is None:
                skipped[ntype] = skipped.get(ntype, 0) + 1
                continue
            entities.append(ent)
            raw_id = str(data.get('node_id') or data.get('id') or '')
            if raw_id:
                node_ids[raw_id] = ent['id']
    edges = []
    for key, ntype in _EXEC_LISTS:
        for node in (cfg.get(key) or []):
            if not isinstance(node, dict):
                continue
            data = node.get('data') if isinstance(node.get('data'), dict) else node
            src = node_ids.get(str(data.get('node_id') or data.get('id') or ''))
            if not src:
                continue
            for tgt_raw, _port in _node_connections(node):
                tgt = node_ids.get(tgt_raw)
                if tgt and (src, tgt) not in edges:
                    edges.append((src, tgt))
    order = _thread_order([e['id'] for e in entities], edges)
    imports = []
    for i, node in enumerate(cfg.get('chain_import_nodes') or []):
        if not isinstance(node, dict):
            continue
        data = node.get('data') if isinstance(node.get('data'), dict) else node
        name = _first_text(data, ('prefix', 'label', 'name'), 80) or f'import#{i}'
        ref = _first_text(data, ('chain_file_path', 'chain_file'), 400)
        child_file = _resolve_asset(chain_dir, ref, '') if ref else ''
        child_lines = []
        try:
            child_abs = os.path.abspath(child_file) if child_file else ''
            if (collapse and child_abs and os.path.isfile(child_abs)
                    and child_abs not in visited):
                child = _build(child_abs, '', visited | {child_abs}, False)
                if child:
                    child_lines = [e['line'][:120]
                                   for e in child['entities'][:_ASSET_CAP]]
        except Exception as e:
            logger.debug("action graph: import collapse failed for %s: %s", path, e)
        imports.append({
            'id': f'import#{i}', 'name': name, 'file': child_file,
            'line': f'call "{name}"', 'child_lines': child_lines,
        })
    return {
        'schema': 1,
        'chain_file': os.path.abspath(path),
        'content_hash': digest,
        'entities': entities,
        'edges': [[a, b] for a, b in edges],
        'order': order,
        'imports': imports,
        'coverage': {
            'nodes': len(entities) + sum(skipped.values()),
            'rendered': len(entities),
            'skipped_by_kind': skipped,
        },
    }


def build_action_graph(chain_file, collapse_imports=True):
    """Content-hash cached action graph for *chain_file* (None when unreadable)."""
    chain_file = str(chain_file or '')
    if not chain_file or not os.path.isfile(chain_file):
        return None
    digest = _md5(chain_file)
    if not digest:
        return None
    cached = _CACHE.get(digest)
    if cached is not None and cached.get('collapse_imports') == bool(collapse_imports):
        return cached
    graph = _build(chain_file, digest, {os.path.abspath(chain_file)},
                   bool(collapse_imports))
    if graph is None:
        return None
    graph['collapse_imports'] = bool(collapse_imports)
    _CACHE[digest] = graph
    while len(_CACHE) > _MAX_CACHE:
        _CACHE.pop(next(iter(_CACHE)))
    return graph


def graph_summary_lines(graph, max_lines=_SUMMARY_LINES):
    """Compact log lines for the shadow summary (header + entities + imports)."""
    if not graph:
        return []
    cov = graph.get('coverage') or {}
    lines = [f"{os.path.basename(graph.get('chain_file') or '?')}: "
             f"{cov.get('rendered', 0)}/{cov.get('nodes', 0)} node(s) rendered, "
             f"{len(graph.get('imports') or [])} import(s)"]
    skipped = cov.get('skipped_by_kind') or {}
    if skipped:
        lines.append('  skipped: ' + ', '.join(
            f'{k} x{v}' for k, v in sorted(skipped.items())))
    for ent in (graph.get('entities') or [])[:max_lines]:
        lines.append(f"  - [{ent['kind']}] {ent['line'][:100]}")
    for imp in (graph.get('imports') or [])[:4]:
        lines.append(f"  -> call \"{imp['name']}\" "
                     f"({len(imp.get('child_lines') or [])} child line(s))")
    return lines


def graph_candidate_lines(graph):
    """Flat routing-visible lines: own entities + one-level import children.

    What a directive can actually be matched against when this graph is the
    worker: the rendered action lines of the chain itself, plus the collapsed
    child lines its imports reach — a nested worker whose own nodes render
    nothing (a lone label-less input) still exposes its leaf actions here.
    """
    if not graph:
        return []
    out = [e['line'] for e in (graph.get('entities') or [])]
    for imp in (graph.get('imports') or []):
        for line in (imp.get('child_lines') or []):
            out.append(f"{imp.get('name')}: {line}")
    return out
