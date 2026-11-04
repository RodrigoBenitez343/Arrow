"""workspace.py — a workspace is one agent and the chains it may use.

Collections used to be a loose string tag on a chain file: the library grouped
by it, and agent mode decided "this chain is an agent brain" with
``collection == "System"``.  That coupled three unrelated things — a folder, an
agent persona and the routing tier.

A **workspace** is now the unit::

    <project>/workspaces/
        index.json                     {"active": "<slug>", "migrated": true}
        <slug>/
            workspace.json             the agent that owns this workspace
            chains/*.json              every chain it may see or route over
            plans.json                 plans the agent learned (agent.py)

One workspace = one agent by default.  ``workspace.json``::

    {"version": 1, "slug": "job-hunt", "name": "Job hunt",
     "agent": {"name": "Scout", "avatar": 0, "model": "",
               "legacy_brain": "",      # a chain path = one fixed brain
               "policy": {"max_steps": 6}},
     "created": "...", "updated": "..."}

``legacy_brain`` is the bridge: with a path there the agent behaves exactly
like the old System chain (one fixed chain answers).  Empty means the agent
composes chains itself (``player/agentic_ops/agent.py``).

Nothing here touches Qt, and nothing deletes a chain file: ``migrate()`` MOVES
files into their workspace (``os.replace``) and reports every move, raising
instead of overwriting anything that already exists.
"""

import json
import logging
import os
import re
import shutil

logger = logging.getLogger(__name__)

VERSION = 1
WORKSPACE_FILE = 'workspace.json'
INDEX_FILE = 'index.json'
CHAINS_DIRNAME = 'chains'
UNASSIGNED_SLUG = 'unassigned'
UNASSIGNED_NAME = 'Unassigned'

#: Chain-file keys that hold a path to another chain file.
PATH_KEYS = ('chain_file_path', 'shared_context_chain_file', 'chain_file',
             'sub_chain_path')
#: Files that live next to chains but are not chains.
_IGNORED_FILES = ('chain_collections.json', 'sequence_collections.json')
_NODE_KEYS = ('sequences', 'web_sequences', 'conditional_nodes', 'llm_nodes',
              'chain_import_nodes', 'form_filler_nodes', 'code_nodes',
              'container_nodes', 'context_nodes', 'input_nodes',
              'handle_nodes', 'mcp_nodes', 'output_nodes')


class WorkspaceError(Exception):
    """A workspace could not be read, created, moved into or migrated."""


# ------------------------------------------------------------------ paths

def root_for_chains(chains_dir):
    """The workspaces directory that belongs to *chains_dir*.

    The legacy layout keeps chains in ``<project>/chains``; workspaces live in
    ``<project>/workspaces`` so both derive from one place.
    """
    if not chains_dir:
        raise WorkspaceError('no chains directory was given')
    return os.path.join(os.path.dirname(os.path.abspath(chains_dir)),
                        'workspaces')


def slugify(name):
    """A filesystem-safe slug: 'Job hunt 2!' -> 'job-hunt-2'."""
    slug = re.sub(r'[^a-z0-9]+', '-', str(name or '').strip().lower())
    return slug.strip('-') or UNASSIGNED_SLUG


def workspace_dir(root, slug):
    return os.path.join(root, slug)


def chains_dir(root, slug):
    return os.path.join(workspace_dir(root, slug), CHAINS_DIRNAME)


def chain_files(root, slug):
    """Every chain file of one workspace (sorted, missing dir = none)."""
    folder = chains_dir(root, slug)
    if not os.path.isdir(folder):
        return []
    return sorted(os.path.join(folder, name) for name in os.listdir(folder)
                  if name.lower().endswith('.json'))


# --------------------------------------------------------------- manifests

def _read_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise WorkspaceError('could not read %s: %s' % (path, exc)) from exc
    if not isinstance(data, dict):
        raise WorkspaceError('%s holds %r, not an object'
                             % (path, type(data).__name__))
    return data


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + '.tmp'
    with open(temporary, 'w', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=False, indent=1)
    os.replace(temporary, path)


def _normalise(manifest, slug):
    """Fill what a hand-written manifest may omit; never invents a name."""
    agent = manifest.get('agent')
    if not isinstance(agent, dict):
        agent = {}
    agent.setdefault('name', manifest.get('name') or slug)
    agent.setdefault('avatar', 0)
    agent.setdefault('model', '')
    agent.setdefault('legacy_brain', '')
    policy = agent.get('policy')
    agent['policy'] = dict(policy) if isinstance(policy, dict) else {}
    agent['policy'].setdefault('max_steps', 6)
    manifest['agent'] = agent
    manifest['version'] = int(manifest.get('version') or VERSION)
    manifest['slug'] = slug
    manifest.setdefault('name', slug)
    return manifest


def load(root, slug):
    """One workspace manifest (raises when it is missing or unreadable)."""
    path = os.path.join(workspace_dir(root, slug), WORKSPACE_FILE)
    if not os.path.isfile(path):
        raise WorkspaceError('workspace %r has no %s' % (slug, WORKSPACE_FILE))
    return _normalise(_read_json(path), slug)


def save(root, manifest):
    """Write a manifest, stamping ``updated`` (raises on a bad slug)."""
    import datetime

    slug = str(manifest.get('slug') or '').strip()
    if not slug:
        raise WorkspaceError('a workspace needs a slug')
    manifest = _normalise(dict(manifest), slug)
    manifest['updated'] = datetime.datetime.now().isoformat(timespec='seconds')
    manifest.setdefault('created', manifest['updated'])
    _write_json(os.path.join(workspace_dir(root, slug), WORKSPACE_FILE),
                manifest)
    return manifest


def list_workspaces(root):
    """Every workspace manifest, sorted by name (missing root = none)."""
    if not os.path.isdir(root):
        return []
    out = []
    for name in sorted(os.listdir(root)):
        folder = os.path.join(root, name)
        if not os.path.isdir(folder):
            continue
        if not os.path.isfile(os.path.join(folder, WORKSPACE_FILE)):
            continue
        try:
            out.append(load(root, name))
        except WorkspaceError:
            logger.warning('workspace %r is unreadable, skipped', name,
                           exc_info=True)
    return sorted(out, key=lambda item: str(item.get('name') or '').lower())


def unique_slug(root, name):
    """A slug for *name* that no workspace uses yet."""
    base = slugify(name)
    slug, index = base, 2
    while os.path.exists(workspace_dir(root, slug)):
        slug = '%s-%d' % (base, index)
        index += 1
    return slug


def create(root, name, agent_name='', model=''):
    """Create a workspace (one agent + its own chains folder) and return it."""
    name = str(name or '').strip()
    if not name:
        raise WorkspaceError('a workspace needs a name')
    slug = unique_slug(root, name)
    manifest = {
        'version': VERSION,
        'slug': slug,
        'name': name,
        'agent': {'name': str(agent_name or name).strip(), 'avatar': 0,
                  'model': str(model or ''), 'legacy_brain': '',
                  'policy': {'max_steps': 6}},
    }
    os.makedirs(chains_dir(root, slug), exist_ok=True)
    manifest = save(root, manifest)
    logger.info('Workspace created: %s (%s)', name, slug)
    return manifest


def set_agent_model(root, slug, model):
    """Pin the model the workspace agent's planner runs on.

    The agent composes chains itself and only a request no learned plan or
    Laya verdict covers reaches the planner model (``agentic_ops/agent.py``),
    so this is the one setting that turns "the planner is not configured" into
    a working agent.  ``''`` clears it (back to Laya-only routing).  Raises when
    the workspace does not exist: a model saved nowhere is worse than an error.
    """
    manifest = load(root, slug)
    agent = manifest.get('agent') or {}
    agent['model'] = str(model or '').strip()
    manifest['agent'] = agent
    saved = save(root, manifest)
    logger.info('Workspace %r: agent model %r', slug,
                saved['agent']['model'] or '(none)')
    return saved


# ------------------------------------------------------------------- index

def _index_path(root):
    return os.path.join(root, INDEX_FILE)


def index(root):
    """``index.json`` content ({} when there is none; never raises)."""
    try:
        return _read_json(_index_path(root))
    except WorkspaceError:
        return {}


def active_slug(root):
    """The workspace the app was last in ('' when none is set)."""
    slug = str(index(root).get('active') or '').strip()
    if slug and os.path.isdir(workspace_dir(root, slug)):
        return slug
    return ''


def set_active_slug(root, slug):
    """Remember the active workspace ('' clears the selection)."""
    if slug and not os.path.isdir(workspace_dir(root, slug)):
        raise WorkspaceError('workspace %r does not exist' % slug)
    data = index(root)
    data['version'] = VERSION
    data['active'] = str(slug or '')
    _write_json(_index_path(root), data)
    return data


def default_workspace(root):
    """The active workspace, else the first on disk, else created on demand."""
    workspaces = list_workspaces(root)
    slug = active_slug(root)
    if slug:
        return load(root, slug)
    if workspaces:
        return workspaces[0]
    return create(root, UNASSIGNED_NAME, agent_name=UNASSIGNED_NAME)


# ------------------------------------------------------------------ chains

def workspace_of(root, chain_path):
    """The slug whose ``chains/`` holds *chain_path* ('' when outside)."""
    target = os.path.abspath(str(chain_path or ''))
    if not os.path.isdir(root):
        return ''
    for name in os.listdir(root):
        folder = os.path.abspath(chains_dir(root, name))
        if target.startswith(folder + os.sep):
            return name
    return ''


def _rewrite_value(value, moves):
    """*value* with a moved chain path substituted (by basename)."""
    if not isinstance(value, str) or not value.strip():
        return value
    base = os.path.basename(value.replace('\\', '/'))
    new_path = moves.get(base)
    return new_path or value


def _rewrite_tree(node, moves):
    """Recursively patch every chain-path field in a decoded chain."""
    changed = 0
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if key in PATH_KEYS and isinstance(value, str):
                replaced = _rewrite_value(value, moves)
                if replaced != value:
                    node[key] = replaced
                    changed += 1
            else:
                changed += _rewrite_tree(value, moves)
    elif isinstance(node, list):
        for item in node:
            changed += _rewrite_tree(item, moves)
    return changed


def rewrite_references(root, moves):
    """Point every chain in *root* at its imported chains' new locations.

    *moves* maps ``basename`` -> new absolute path.  Returns the number of
    files whose content changed.  A chain that imports another one stores an
    absolute path, so a move without this rewrite silently breaks the import.
    """
    if not moves or not os.path.isdir(root):
        return 0
    touched = 0
    for name in sorted(os.listdir(root)):
        for path in chain_files(root, name) or []:
            try:
                data = _read_json(path)
            except WorkspaceError:
                continue
            if _rewrite_tree(data, moves):
                _write_json(path, data)
                touched += 1
    return touched


def move_chain(root, chain_path, slug):
    """Move one chain file into workspace *slug* and fix the imports.

    The file is MOVED, never copied: two chains with the same name in two
    workspaces would make basename rewriting ambiguous.  Raises when the
    target already holds a chain with that name (nothing is overwritten).
    """
    source = os.path.abspath(str(chain_path or ''))
    if not os.path.isfile(source):
        raise WorkspaceError('chain file not found: %s' % source)
    if slug and not os.path.isdir(workspace_dir(root, slug)):
        raise WorkspaceError('workspace %r does not exist' % slug)
    target_dir = chains_dir(root, slug) if slug else ''
    if not target_dir:
        raise WorkspaceError('a chain must belong to a workspace')
    target = os.path.join(target_dir, os.path.basename(source))
    if os.path.abspath(target) == source:
        return source
    if os.path.exists(target):
        raise WorkspaceError(
            'workspace %r already holds %r' % (slug, os.path.basename(source)))
    os.makedirs(target_dir, exist_ok=True)
    if os.path.exists(source):
        shutil.move(source, target)
    basename = os.path.basename(target)
    rewrite_references(root, {basename: target})
    logger.info('Chain moved into workspace %r: %s', slug, basename)
    return target


# --------------------------------------------------------------- migration

def _imports(data):
    """Basenames of every chain a chain file imports (any depth)."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in PATH_KEYS and isinstance(value, str) and value.strip():
                    base = os.path.basename(value.replace('\\', '/'))
                    if base and base not in found:
                        found.append(base)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return found


def _looks_like_chain(data):
    return any(isinstance(data.get(key), list) and data[key]
               for key in _NODE_KEYS)


def _legacy_chains(legacy_chains_dir):
    """Chain files of the old flat folder, grouped by their collection."""
    groups = {}
    if not os.path.isdir(legacy_chains_dir):
        return groups
    for name in sorted(os.listdir(legacy_chains_dir)):
        path = os.path.join(legacy_chains_dir, name)
        if not os.path.isfile(path) or not name.lower().endswith('.json'):
            continue
        if name in _IGNORED_FILES or name.endswith('.bak'):
            continue
        try:
            data = _read_json(path)
        except WorkspaceError:
            logger.warning('not a readable chain, left alone: %s', path)
            continue
        if not _looks_like_chain(data):
            continue
        collection = str(data.get('collection') or '').strip()
        groups.setdefault(collection, []).append((path, data))
    return groups


def migrated(root):
    return bool(index(root).get('migrated'))


def migrate(root, legacy_chains_dir):
    """Turn the old flat chains folder + collections into workspaces.

    Runs once (``index.json`` records it).  Every ordinary chain is MOVED into
    the workspace named after its collection; every import path is rewritten by
    basename, which also repairs the stale absolute paths older builds wrote.

    A ``collection: System`` chain was a hand-built brain.  System chains are
    retired — a workspace's agent learns its own orchestrator — so those files
    are not turned into a "System" workspace: each one is absorbed as an
    ORDINARY chain into the workspace that holds most of the chains it imports
    (else the default workspace).  Nothing is deleted and nothing is pinned:
    ``agent.legacy_brain`` stays a manual escape hatch only.

    Returns a report ``{workspaces, brains, moves, imports}``; raises
    ``WorkspaceError`` when a target already holds the same file name.
    """
    if migrated(root):
        return {'workspaces': {}, 'brains': {}, 'moves': 0, 'imports': 0,
                'skipped': True}
    groups = _legacy_chains(legacy_chains_dir)
    if not groups:
        os.makedirs(root, exist_ok=True)
        data = index(root)
        data.update({'version': VERSION, 'migrated': True})
        _write_json(_index_path(root), data)
        return {'workspaces': {}, 'brains': {}, 'moves': 0, 'imports': 0}

    report = {'workspaces': {}, 'brains': {}, 'moves': 0, 'imports': 0}
    moves = {}            # basename -> new path
    homes = {}            # basename -> workspace slug

    ordinary, brains = {}, []
    for name, entries in groups.items():
        if name.strip().lower() == 'system':
            brains.extend(entries)
        else:
            ordinary[name] = entries

    for collection, entries in sorted(ordinary.items()):
        name = collection or UNASSIGNED_NAME
        existing = next((item for item in list_workspaces(root)
                         if item['name'].lower() == name.lower()), None)
        manifest = existing or create(root, name)
        slug = manifest['slug']
        os.makedirs(chains_dir(root, slug), exist_ok=True)
        for path, _data in entries:
            target = os.path.join(chains_dir(root, slug), os.path.basename(path))
            if os.path.exists(target):
                raise WorkspaceError(
                    'workspace %r already holds %r' % (slug,
                                                       os.path.basename(path)))
            shutil.move(path, target)
            data = _read_json(target)
            data['workspace'] = slug
            data.pop('collection', None)
            data.pop('is_default', None)
            _write_json(target, data)
            moves[os.path.basename(target)] = target
            homes[os.path.basename(target)] = slug
            report['workspaces'].setdefault(slug, []).append(
                os.path.basename(target))
            report['moves'] += 1

    # Legacy brains, absorbed in the workspace whose chains they drive.
    fallback = next(iter(report['workspaces']), '') or create(
        root, UNASSIGNED_NAME)['slug']
    for path, data in brains:
        base = os.path.basename(path)
        counted = {}
        for imported in _imports(data):
            slug = homes.get(imported)
            if slug and slug != fallback:
                counted[slug] = counted.get(slug, 0) + 1
        slug = (max(counted, key=counted.get) if counted else fallback)
        target = os.path.join(chains_dir(root, slug), base)
        if os.path.exists(target):
            raise WorkspaceError(
                'workspace %r already holds %r' % (slug, base))
        shutil.move(path, target)
        stored = _read_json(target)
        stored['workspace'] = slug
        stored.pop('collection', None)
        stored.pop('is_default', None)
        _write_json(target, stored)
        moves[base] = target
        homes[base] = slug
        report['brains'][base] = slug
        report['moves'] += 1

    report['imports'] = rewrite_references(root, moves)
    data = index(root)
    data.update({'version': VERSION, 'migrated': True})
    if not data.get('active'):
        first = list_workspaces(root)
        data['active'] = first[0]['slug'] if first else ''
    _write_json(_index_path(root), data)
    logger.info('Workspace migration: %d chain(s), %d import path(s), %d '
                'workspace(s), %d legacy brain(s) absorbed',
                report['moves'], report['imports'],
                len(report['workspaces']), len(report['brains']))
    return report
