"""chain_refresh.py — keep a chain's description current, without a canvas.

A chain's per-node lines used to be written only when someone asked the
Builder to describe it, so a chain edited afterwards kept routing on a
description of its old self.  This module closes that loop as a background
sweep instead of a per-request job:

* ``flow.complete_static`` re-derives the lines from the chain FILE's own node
  entries (same ``describe.acting`` the canvas uses, no model, no Qt);
* ``flow.is_stale`` compares the stored ``flow.stamp`` — a fingerprint of the
  chain's node types and settings, blind to ids and order — against the file;
* only the stale files are rewritten, atomically, and the derived
  ``description`` is refreshed with them.

A chain that was never described is left alone: there is no description to
update, and inventing one would make an undescribed chain routable without
anyone deciding that.

Nothing here raises at the caller: a sweep reports a broken file and moves on.
"""

import json
import logging
import os

from player import chain_brief
from player import workspace as ws
from player.builder import flow as flow_module
from player.builder.errors import FlowMetadataError

logger = logging.getLogger(__name__)

#: What ``refresh_file`` did, so a sweep can report precisely.
UPDATED = 'updated'
CURRENT = 'current'
SKIPPED = 'skipped'
FAILED = 'failed'


def _write(path, data):
    """Rewrite one chain file in the shape the editor writes (atomic)."""
    temporary = path + '.tmp'
    with open(temporary, 'w', encoding='utf-8') as handle:
        # Same formatting as ConfigManager.save_chain: a rewrite must not
        # reformat a file the editor will read back.
        json.dump(data, handle, indent=2)
    os.replace(temporary, path)


def refresh_file(path):
    """Bring one chain file's description up to date.

    Returns ``UPDATED`` (rewritten), ``CURRENT`` (already describes the chain),
    ``SKIPPED`` (never described — nothing to update) or ``FAILED`` (unreadable
    or malformed; logged, never raised).
    """
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        logger.warning('chain description not refreshed, unreadable: %s (%s)',
                       path, exc)
        return FAILED
    if not isinstance(data, dict):
        logger.warning('chain description not refreshed, not a chain: %s', path)
        return FAILED
    stored = data.get(flow_module.FLOW_KEY)
    if not isinstance(stored, dict) or flow_module.is_empty(stored):
        return SKIPPED
    if not flow_module.is_stale(data):
        return CURRENT
    try:
        updated = flow_module.complete_static(data, flow_module.normalise(stored))
    except FlowMetadataError as exc:
        logger.warning('chain description not refreshed, malformed flow: %s '
                       '(%s)', path, exc)
        return FAILED
    data[flow_module.FLOW_KEY] = updated
    # ``description`` is derived, never typed: it is refreshed with the flow so
    # the file never carries a sentence older than the metadata beside it.
    data['description'] = chain_brief.brief(data)
    try:
        _write(path, data)
    except OSError as exc:
        logger.warning('chain description not written: %s (%s)', path, exc)
        return FAILED
    logger.info('chain description refreshed: %s (%d node(s))', path,
                len(updated.get('nodes') or ()))
    return UPDATED


def _with_node_descriptions(chain, entries):
    """Copy each described line onto the node entry it describes.

    ``flow.nodes`` is where the chain says what each node does; the node's own
    ``description`` is where its dialog shows it (and where the loader reads it
    back from).  Written here so a chain described without a canvas still opens
    with its sentences on the nodes.  The entry lists are COPIED: mutating the
    input would make every refresh look like a change.
    """
    texts = {}
    for entry in entries or ():
        if isinstance(entry, dict):
            texts[str(entry.get('node') or '')] = str(entry.get('does') or '')
    for key, _ntype in flow_module.CHAIN_NODE_TYPES:
        saved = chain.get(key)
        if not isinstance(saved, list):
            continue
        chain[key] = [
            (dict(entry, description=texts[str(entry.get('node_id')
                                               or entry.get('id') or '')])
             if isinstance(entry, dict)
             and str(entry.get('node_id') or entry.get('id') or '') in texts
             else entry)
            for entry in saved]
    return chain


def _prune_sections(chain, described):
    """Drop section refs that name no node of THIS file.

    A description written on a canvas stores the ids of that session; the file
    saved afterwards carries its own, and the two match only by luck — that is
    how a chain ended up with sections naming nodes that no longer exist, so a
    box covered half of what its note described.  A ref the file does not
    contain can never be drawn, so it goes; a section left with none goes too.
    """
    known = set()
    for key, _ntype in flow_module.CHAIN_NODE_TYPES:
        for entry in chain.get(key) or ():
            if isinstance(entry, dict):
                for name in ('node_id', 'id', 'name'):
                    value = str(entry.get(name) or '')
                    if value:
                        known.add(value)
    sections = []
    for section in described.get('sections') or ():
        ids = [ref for ref in section.get('nodes') or ()
               if str(ref) in known]
        if ids:
            sections.append(dict(section, nodes=ids))
    described['sections'] = sections
    return described


def describe_file(path):
    """Describe one chain file that was just named or saved.

    ``refresh_file`` only REWRITES an existing description; a chain that was
    never described is deliberately left alone by the sweep.  Here the user has
    just named the chain, so the block is created: every node's line comes from
    ``flow.complete_static`` (the same ``describe.acting`` the canvas uses) and
    the routing line from ``chain_brief``.  The next run therefore routes on a
    described chain instead of discovering it cold.

    Returns ``UPDATED`` (written), ``CURRENT`` (already described, unchanged),
    ``SKIPPED`` (no node to describe) or ``FAILED`` (unreadable; logged, never
    raised — the caller has already saved the chain).
    """
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        logger.warning('chain not described, unreadable: %s (%s)', path, exc)
        return FAILED
    if not isinstance(data, dict):
        logger.warning('chain not described, not a chain: %s', path)
        return FAILED
    try:
        described = flow_module.complete_static(data)
    except FlowMetadataError as exc:
        logger.warning('chain not described, malformed flow: %s (%s)', path, exc)
        return FAILED
    if not (described.get('nodes') or []):
        return SKIPPED
    updated = dict(data)
    updated[flow_module.FLOW_KEY] = _prune_sections(data, described)
    updated = _with_node_descriptions(updated, described.get('nodes'))
    updated['description'] = chain_brief.brief(updated)
    if updated == data:
        return CURRENT
    try:
        _write(path, updated)
    except OSError as exc:
        logger.warning('chain description not written: %s (%s)', path, exc)
        return FAILED
    logger.info('chain described: %s (%d node(s))', path,
                len(described.get('nodes') or ()))
    return UPDATED


def refresh_paths(paths):
    """Sweep a list of chain files.

    ``current`` is the point of the sweep: those were checked and left alone.
    """
    report = {'checked': 0, 'updated': 0, 'current': 0, 'failed': 0,
              'skipped': 0}
    for path in paths or ():
        status = refresh_file(path)
        report['checked'] += 1
        if status == UPDATED:
            report['updated'] += 1
        elif status == CURRENT:
            report['current'] += 1
        elif status == FAILED:
            report['failed'] += 1
        else:
            report['skipped'] += 1
    return report


def refresh_workspace(root, slug):
    """Sweep one workspace's chains (the scope agent mode routes over)."""
    try:
        paths = ws.chain_files(root, slug)
    except Exception:
        logger.warning('chain descriptions not refreshed, workspace unreadable: '
                       '%s/%s', root, slug, exc_info=True)
        return {'checked': 0, 'updated': 0, 'current': 0, 'failed': 1,
                'skipped': 0}
    report = refresh_paths(paths)
    if report['updated'] or report['failed']:
        logger.info('Chain descriptions refreshed in workspace %r: %s',
                    slug, report)
    return report


def refresh_all(root):
    """Sweep every workspace (a router with no active agent still describes)."""
    try:
        manifests = ws.list_workspaces(root)
    except Exception:
        logger.warning('chain descriptions not refreshed, registry unreadable: '
                       '%s', root, exc_info=True)
        return {'checked': 0, 'updated': 0, 'current': 0, 'failed': 1,
                'skipped': 0}
    report = {'checked': 0, 'updated': 0, 'current': 0, 'failed': 0,
              'skipped': 0}
    for manifest in manifests:
        one = refresh_workspace(root, manifest.get('slug') or '')
        for key in report:
            report[key] += one.get(key, 0)
    return report
