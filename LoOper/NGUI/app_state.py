"""app_state.py — the editor picks up where it was left.

Closing the app used to lose the open chain (including edits that were never
saved to disk), whether the Builder chat was open, and where the canvas was
panned and zoomed — so every morning began with re-opening a chain.

This module writes that state on close and restores it on start:

    {"version": 1,
     "chain_file":   "D:\\Arrow\\LoOper\\workspaces\\job-hunt\\chains\\X.json",
     "chain_config": {...the live chain, exactly as the editor held it...},
     "workspace":    "job-hunt",
     "builder_panel_open": true,
     "view": {"zoom": 0.9, "center": [x, y]},
     "ui": {"logs_metrics": true}}

``ui`` holds small UI preferences (a dialog toggle) that must survive a
restart but belong to no chain: :func:`ui_flag` / :func:`set_ui_flag` read and
write one key at a time and preserve everything else in the file.

The LIVE chain config is stored, not just the file path: a chain that was never
saved, or was edited after the last save, resumes with those edits.  Restoring
is convenience, so a state file that cannot be used is REPORTED and skipped —
it never stops the app from starting.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

VERSION = 1
_FILENAME = 'app_state.json'
#: Small UI preferences: ``{name: bool}``.
UI_KEY = 'ui'
#: Node lists a restored chain may carry; an empty one restores nothing.
_NODE_KEYS = ('sequences', 'web_sequences', 'conditional_nodes', 'llm_nodes',
              'chain_import_nodes', 'form_filler_nodes', 'code_nodes',
              'container_nodes', 'context_nodes', 'input_nodes',
              'handle_nodes', 'mcp_nodes', 'output_nodes')


class AppStateError(Exception):
    """The saved state could not be read, written or applied."""


def path():
    """The state file (raises when the runtime dir is unavailable)."""
    from AI.runtime_paths import get_runtime_dir
    base = get_runtime_dir()
    if not base:
        raise AppStateError('the app runtime directory is unavailable')
    return os.path.join(base, _FILENAME)


def has_chain(config):
    """True when a chain config holds at least one node."""
    if not isinstance(config, dict):
        return False
    return any(isinstance(config.get(key), list) and config[key]
               for key in _NODE_KEYS)


# ------------------------------------------------------------------ save

def _canvas_view(graph_view):
    """``{'zoom', 'center'}`` of the canvas, or {} when it cannot be read."""
    try:
        node_graph = graph_view.node_graph
        viewer = node_graph.viewer()
        center = viewer.mapToScene(viewer.viewport().rect().center())
        return {'zoom': float(node_graph.get_zoom()),
                'center': [float(center.x()), float(center.y())]}
    except Exception:
        logger.debug('canvas view not readable, not saved', exc_info=True)
        return {}


def _geometry(window):
    """The window frame as hex, or '' when it cannot be read."""
    try:
        return bytes(window.saveGeometry().toHex()).decode('ascii')
    except Exception:
        logger.debug('window geometry not readable, not saved', exc_info=True)
        return ''


def apply_geometry(window):
    """Put the window back at its last size/position (call BEFORE show)."""
    try:
        state = load()
        hexed = str(state.get('geometry') or '')
        if not hexed:
            return False
        from PyQt5.QtCore import QByteArray
        return bool(window.restoreGeometry(QByteArray.fromHex(hexed.encode())))
    except Exception:
        logger.warning('the saved window geometry could not be applied',
                       exc_info=True)
        return False


def snapshot(window):
    """The state to persist for *window* (no IO)."""
    graph_view = getattr(window, 'graph_view', None)
    config = {}
    chain_file = ''
    if graph_view is not None:
        cfg = getattr(graph_view, 'config_manager', None)
        if cfg is not None:
            try:
                config = cfg.get_chain_config() or {}
            except Exception:
                logger.warning('could not harvest the chain for the state file',
                               exc_info=True)
                config = dict(getattr(cfg, 'chain_config', {}) or {})
            chain_file = str(getattr(cfg, '_current_chain_file', '') or '')
    panel = getattr(window, 'builder_panel', None)
    return {
        'version': VERSION,
        'chain_file': chain_file,
        'chain_config': dict(config or {}),
        'builder_panel_open': bool(panel is not None and panel.isVisible()),
        'workspace': (str(getattr(graph_view, 'workspace_slug',
                                 lambda: '')() or '')
                      if graph_view is not None else ''),
        'view': _canvas_view(graph_view) if graph_view is not None else {},
        'geometry': _geometry(window),
    }


# ------------------------------------------------------- ui preferences

def load_ui():
    """The ``ui`` block of the state file ({} when there is none)."""
    try:
        target = path()
        if not os.path.isfile(target):
            return {}
        with open(target, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, ValueError, AppStateError):
        return {}
    block = data.get(UI_KEY) if isinstance(data, dict) else None
    return dict(block) if isinstance(block, dict) else {}


def ui_flag(name, default=False):
    """One boolean UI preference (the Logs metrics toggle, ...)."""
    value = load_ui().get(str(name))
    return default if value is None else bool(value)


def set_ui_flag(name, value):
    """Store one UI preference; False when it could not be written.

    The rest of the state file is read first and written back, so setting a
    flag never costs the chain the editor is holding.
    """
    try:
        target = path()
        data = {}
        if os.path.isfile(target):
            with open(target, 'r', encoding='utf-8') as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                data = loaded
        block = data.get(UI_KEY)
        if not isinstance(block, dict):
            block = {}
        block[str(name)] = bool(value)
        data[UI_KEY] = block
        data.setdefault('version', VERSION)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        temporary = target + '.tmp'
        with open(temporary, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=1)
        os.replace(temporary, target)
        logger.debug('ui flag %s=%s saved', name, bool(value))
        return True
    except (OSError, ValueError, AppStateError):
        logger.warning('could not persist the UI flag %r', name, exc_info=True)
        return False


def save(window):
    """Write the window's state (raises ``AppStateError`` on failure)."""
    data = snapshot(window)
    data[UI_KEY] = load_ui()
    target = path()
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        temporary = target + '.tmp'
        with open(temporary, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=1)
        os.replace(temporary, target)
    except OSError as exc:
        raise AppStateError(
            'could not write %s: %s' % (target, exc)) from exc
    logger.info('App state saved: %d node(s), panel=%s',
                sum(len(data['chain_config'].get(key) or [])
                    for key in _NODE_KEYS),
                data['builder_panel_open'])
    return data


# ----------------------------------------------------------------- load

def load():
    """The saved state ({} when there is none; raises when it is corrupt)."""
    target = path()
    if not os.path.isfile(target):
        return {}
    try:
        with open(target, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise AppStateError(
            'could not read %s: %s' % (target, exc)) from exc
    if not isinstance(data, dict):
        raise AppStateError(
            '%s holds %r, not an object' % (target, type(data).__name__))
    if int(data.get('version') or 0) > VERSION:
        raise AppStateError(
            '%s was written by a newer version (%s > %s)'
            % (target, data.get('version'), VERSION))
    return data


# --------------------------------------------------------------- restore

def _apply_view(graph_view, view):
    try:
        zoom = float(view['zoom'])
        node_graph = graph_view.node_graph
        node_graph.set_zoom(zoom)
        center = view.get('center') or []
        if len(center) == 2:
            from PyQt5.QtCore import QPointF
            node_graph.viewer().centerOn(QPointF(float(center[0]),
                                                 float(center[1])))
        # A saved view can point at empty canvas (panned away last session, or
        # the chain changed): the chain is brought back in front of the user.
        ensure = getattr(graph_view, 'ensure_chain_visible', None)
        if callable(ensure):
            ensure()
        logger.info('Canvas view restored: zoom=%.2f center=%s', zoom, center)
    except Exception:
        logger.warning('the saved canvas view could not be applied',
                       exc_info=True)


def restore(window, state=None):
    """Rebuild what the last session held; returns what was restored.

    Raises ``AppStateError`` when the saved state cannot be applied; the
    caller reports it and keeps starting with an empty canvas.
    """
    if state is None:
        state = load()
    if not state:
        return {}
    graph_view = getattr(window, 'graph_view', None)
    if graph_view is None:
        raise AppStateError('the window has no canvas to restore into')
    cfg = getattr(graph_view, 'config_manager', None)
    if cfg is None:
        raise AppStateError('the canvas has no chain configuration')

    restored = {}
    # The workspace is restored FIRST: it decides which chains the library
    # lists and which folder the chain below belongs to.
    saved_workspace = str(state.get('workspace') or '').strip()
    if saved_workspace:
        try:
            graph_view.set_workspace(saved_workspace, remember=False)
            restored['workspace'] = saved_workspace
        except Exception:
            logger.warning('workspace %r could not be reopened',
                           saved_workspace, exc_info=True)
    # ``chain_config`` ABSENT means an older state file; PRESENT but empty
    # means the user emptied the canvas and that empty canvas is the state.
    legacy = state.get('chain_config') is None
    config = state.get('chain_config') or {}
    chain_file = str(state.get('chain_file') or '')
    if has_chain(config):
        # The live editor state wins: it may hold edits never saved to disk.
        cfg.chain_config = dict(config)
        cfg._current_chain_file = chain_file
        cfg._current_chain_description = config.get('description', '')
        cfg._current_chain_workspace = (config.get('workspace')
                                        or saved_workspace)
        cfg._current_chain_flow = config.get('flow') or {}
        cfg._current_chain_routing = config.get('routing') or {}
        cfg._current_chain_is_default = bool(config.get('is_default'))
        graph_view._chain_epoch += 1
        graph_view.build_graph_from_config(dict(config))
        restored['chain'] = chain_file or '(unsaved chain)'
        restored['nodes'] = sum(len(config.get(key) or []) for key in _NODE_KEYS)
    elif legacy and chain_file and os.path.isfile(chain_file):
        # Older state file (file path only): fall back to opening the chain.
        graph_view.load_chain(chain_file)
        restored['chain'] = chain_file
    if restored:
        logger.info('Resumed %s with %d node(s)',
                    restored.get('chain'), restored.get('nodes', 0))

    if state.get('builder_panel_open'):
        try:
            window._toggle_builder(True)
            restored['builder_panel'] = True
        except Exception:
            logger.warning('the Builder panel could not be reopened',
                           exc_info=True)

    view = state.get('view') or {}
    if view:
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(250, lambda: _apply_view(graph_view, view))

    return restored
