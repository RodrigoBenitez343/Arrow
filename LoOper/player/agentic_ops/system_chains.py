"""system_chains.py — the LEGACY "System chain" reader (kept for migration).

A chain whose ``collection`` was ``"System"`` used to be an agent brain, and
agent mode listed those chains as coworkers.  Workspaces replaced that
(``player/workspace.py``): one workspace is one agent plus its own chains, and
a workspace can still pin one chain as its ``legacy_brain``.

``read_chains`` is still the plain reader of a chains folder (agent mode's
catalogue and the workspace migration both need it).  ``is_system_chain`` /
``read_system_chains`` exist only to recognise the old tag while those chains
are migrated; nothing new should branch on a collection again.
"""

import json
import os

from player import chain_brief

SYSTEM_COLLECTION = "system"


def _norm(collection) -> str:
    """Normalise a collection value for case/space-insensitive comparison."""
    return str(collection or "").strip().lower()


def read_chains(chains_dir) -> list:
    """Read every ``*.json`` chain in *chains_dir* into a list of dicts.

    Each entry: ``{id, name, description, path, collection}``.  ``id`` is the
    filename without the ``.json`` extension, ``name`` falls back to ``id``
    when the file has no ``name`` field, and ``description`` is the chain's
    DERIVED routing line (``player/chain_brief.py``) — never hand-written
    prose, so a stale sentence cannot steer a router.
    Invalid/unreadable files are skipped.
    """
    chains = []
    if not chains_dir or not os.path.isdir(chains_dir):
        return chains
    for fname in sorted(os.listdir(chains_dir)):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(chains_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        chain_id = fname[:-5]
        flow = data.get("flow") if isinstance(data.get("flow"), dict) else {}
        summary = str((flow or {}).get("summary") or "").strip()
        chains.append({
            "id": chain_id,
            "name": (data.get("name") or chain_id),
            "description": chain_brief.brief(data),
            "flow_summary": summary,
            "path": fpath,
            "collection": (data.get("collection") or ""),
            # ``is_default`` marked the old root brain (retired; a workspace
            # pins a brain with ``agent.legacy_brain`` instead).
            "is_default": bool(data.get("is_default", False)),
        })
    return chains


def is_system_chain(chain) -> bool:
    """True when *chain* (a dict with a ``collection`` key) is a System chain."""
    return _norm(chain.get("collection")) == SYSTEM_COLLECTION


def read_system_chains(chains_dir) -> list:
    """Every System chain in *chains_dir* — one entry per agent persona."""
    return [c for c in read_chains(chains_dir) if is_system_chain(c)]
