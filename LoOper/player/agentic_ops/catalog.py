"""catalog.py — what a workspace can do, read from the chains themselves.

An agent does not need a hand-built orchestrator chain: every chain already
describes itself.  That description is DERIVED, never typed: ``chain_brief``
turns the Builder's ``flow`` metadata (summary / section boxes / authored
steps) plus the chain's own input/output nodes into one routing line, so the
router never reads prose that drifted from the topology (the old manual
``description`` field is still written, but as that same derived line).

The catalog is the routing index::

    {'id': 'jobfinder', 'name': 'Job finder', 'path': '...\\jobfinder.json',
     'summary': 'searches job boards, collects postings, writes a shortlist',
     'sections': ['search', 'collect', 'shortlist'],
     'nodes': [{'id': '0x1', 'does': 'supplies the search terms the user typed'},
               {'id': '0x2', 'does': 'searches the boards with the model …'}],
     'needs': ['search terms'], 'returns': ['shortlist', 'count'],
     'examples': ['find me a job', 'look for postings'],
     'asks': False, 'reports': True, 'routable': True}

``routable`` is False when a chain says nothing about itself (no flow metadata
and no nodes worth counting): an agent that routes by description must never
be handed a nameless chain as a candidate.

``summary`` is for a MODEL to read.  ``task_labels`` is what the encoder
scores, and the two are deliberately different (see the measurement there).
``nodes`` is the per-node half: what each thing inside the chain does, so the
reasoner sees the chain's own contents instead of one sentence about them.

Nothing here executes or edits a chain.
"""

import json
import logging
import os

from player import chain_brief
from player import workspace as ws

logger = logging.getLogger(__name__)

#: Routing summaries stay short: they are scored against the user's query.
MAX_SUMMARY = chain_brief.BRIEF_LIMIT
#: Per-node descriptions the reasoner is shown, and how long each may be.  A
#: chain can hold hundreds of nodes; the prompt gets the ones that explain it.
MAX_NODES_SHOWN = 4
MAX_NODE_DOES = 140
MAX_NODE_LIST = 40


class CatalogError(Exception):
    """A chain file could not be read as a catalog entry."""


def read_chain(path):
    """One decoded chain file (raises when it is not a chain)."""
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise CatalogError('could not read %s: %s' % (path, exc)) from exc
    if not isinstance(data, dict):
        raise CatalogError('%s holds %r, not a chain object'
                           % (path, type(data).__name__))
    return data


def _labels(entries, field='label'):
    """Distinct non-empty texts of *entries* (input/output node labels)."""
    out = []
    for entry in entries or ():
        if not isinstance(entry, dict):
            continue
        text = str(entry.get(field) or '').strip()
        if not text:
            text = str(entry.get('variable_name') or '').strip()
        if text and text not in out:
            out.append(text)
    return out


def _clip(text, limit):
    """Clip at a word boundary (a truncated mid-word sentence reads as noise)."""
    text = str(text or '')
    if len(text) <= limit:
        return text
    cut = text[:limit].rstrip()
    return (cut.rsplit(' ', 1)[0] + '…') if ' ' in cut else cut + '…'


def _nodes(flow):
    """``[{'id', 'does'}]`` — what each node of the chain does (bounded)."""
    out = []
    for entry in flow.get('nodes') or ():
        if not isinstance(entry, dict):
            continue
        text = ' '.join(str(entry.get('does') or '').split())
        if not text:
            continue
        out.append({'id': str(entry.get('node') or '').strip(),
                    'does': _clip(text, MAX_NODE_DOES)})
        if len(out) >= MAX_NODE_LIST:
            break
    return out


def entry(path):
    """One catalog entry for a chain file (raises when it is unreadable)."""
    data = read_chain(path)
    chain_id = os.path.basename(path)[:-5]
    flow = data.get('flow') if isinstance(data.get('flow'), dict) else {}
    # The routing line is DERIVED from the chain (chain_brief): the manual
    # ``description`` is not read here at all, so a stale sentence written
    # months ago cannot steer the agent.
    line = chain_brief.brief(data)[:MAX_SUMMARY]
    # Routing needs the Builder's own metadata: a structural line (node counts)
    # is shown in the library but is not enough to route on.
    described = any(flow.get(key) for key in ('summary', 'sections', 'steps',
                                              'nodes'))
    sections = [str(section.get('title') or '').strip()
                for section in (flow.get('sections') or ())
                if isinstance(section, dict)]
    inputs = [item for item in (data.get('input_nodes') or ())
              if isinstance(item, dict)]
    outputs = [item for item in (data.get('output_nodes') or ())
               if isinstance(item, dict)]
    routing = data.get('routing') if isinstance(data.get('routing'), dict) else {}
    examples = [str(text).strip() for text in (routing.get('examples') or ())
                if str(text).strip()]
    return {
        'id': chain_id,
        'name': str(data.get('name') or chain_id),
        'path': os.path.abspath(path),
        'summary': line,
        'description': line,
        # Requests this chain already answered (written by the orchestrator
        # when a run succeeds).  For an atomic tool these are the sharpest
        # routing signal there is: they are user requests, like the new one.
        'examples': examples[:MAX_EXAMPLES],
        'sections': [title for title in sections if title],
        'steps': len(flow.get('steps') or ()),
        'nodes': _nodes(flow),
        'needs': _labels(inputs),
        'returns': _labels(outputs),
        # Does running it stop to ask the user, and does it produce output?
        # The agent records both per plan (when to ask / when to report).
        'asks': any(str(item.get('user_prompt') or '').strip()
                    or item.get('decision_mode')
                    or item.get('agent_modifiable') for item in inputs),
        'reports': bool(outputs),
        'described': bool(described),
        'routable': bool(line) and bool(described),
    }


def entries(root, slug):
    """Every chain of one workspace as a catalog entry.

    A chain that cannot be read is reported and skipped — one broken file must
    not take the whole agent down.
    """
    out = []
    for path in ws.chain_files(root, slug):
        try:
            out.append(entry(path))
        except CatalogError:
            logger.warning('chain skipped from the catalog: %s', path,
                           exc_info=True)
    return out


def routable(entries_in):
    """The entries an agent may route to (named chains only)."""
    return [item for item in entries_in if item.get('routable')]


# A chain file keeps up to 5; the router scores a bounded set.
MAX_EXAMPLES = 3


def summary_of(item):
    """The one line a MODEL reads (name, what it does, and its flags)."""
    text = item.get('summary') or item.get('description') or ''
    flags = []
    if item.get('asks'):
        flags.append('asks the user')
    if item.get('reports'):
        flags.append('reports')
    tail = (' [%s]' % ', '.join(flags)) if flags else ''
    return '%s: %s%s' % (item.get('name') or item.get('id'), text, tail)


def task_labels(item):
    """The SHORT texts the encoder scores: the name, then past requests.

    MEASURED with the bundled English encoder (noul, statement "This text asks
    for the same task as: <candidate>") on a workspace of one-step tools:

        request "go to linkedin.com"
            candidate = long brief      auto-apply loop 0.80, open linkedin 0.54
            candidate = chain name      open linkedin 0.85, others <= 0.60
        request "click on the jobs section"
            candidate = chain name      jobs 0.76, others <= 0.45

    An entailment score rises with the LENGTH of the candidate, so a chain that
    advertises a long brief outranks the atomic tool that actually matches.
    The name (and a past request, which is user text like the new request) do
    not have that bias, which is why routing scores THOSE and hands the long
    brief only to the planner model.
    """
    out = []
    name = str(item.get('name') or item.get('id') or '').strip()
    if name:
        out.append(name)
    for example in item.get('examples') or ():
        text = str(example).strip()
        if text and text not in out:
            out.append(text)
    return out


def digest(entries_in, limit=4000):
    """The catalog as a prompt block ('' when there is nothing routable)."""
    lines = []
    for item in routable(entries_in):
        line = '- %s (%s): %s' % (item['id'], item['name'], item['summary'])
        if item.get('examples'):
            line += ' | known requests: ' + ' / '.join(item['examples'][:2])
        if item.get('needs'):
            line += ' | needs: ' + ', '.join(item['needs'][:4])
        if item.get('returns'):
            line += ' | returns: ' + ', '.join(item['returns'][:4])
        # The chain's own contents: what each thing inside it does.  Without
        # this the reasoner only had one sentence ("a job search chain") and
        # had to guess what the steps actually do.
        does = [node['does'] for node in (item.get('nodes') or ())[:MAX_NODES_SHOWN]]
        if does:
            line += ' | its nodes: ' + ' → '.join(does)
        if item.get('asks'):
            line += ' | asks the user'
        lines.append(line)
    return '\n'.join(lines)[:limit]


def find(entries_in, chain_id):
    """The entry whose id or name is *chain_id* (None when there is none)."""
    wanted = str(chain_id or '').strip().lower()
    for item in entries_in or ():
        if item['id'].lower() == wanted or item['name'].lower() == wanted:
            return item
    return None
