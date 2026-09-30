"""system_chains.py — reads chain files and the System-chain ("coworker") list.

A **System chain** is a chain JSON whose ``collection`` is ``"System"`` (any
case).  Each one is an independent agent brain — coding, marketing, daily
tasks, anything the user designs — so the agent mode can offer several of them
in a left sidebar and let the user pick who to talk to.  Nothing here is
hardcoded: the list is whatever the user has in their chains directory.
"""

import json
import os

SYSTEM_COLLECTION = "system"


def _norm(collection) -> str:
    """Normalise a collection value for case/space-insensitive comparison."""
    return str(collection or "").strip().lower()


def read_chains(chains_dir) -> list:
    """Read every ``*.json`` chain in *chains_dir* into a list of dicts.

    Each entry: ``{id, name, description, path, collection}``.  ``id`` is the
    filename without the ``.json`` extension, ``name`` falls back to ``id``
    when the file has no ``name`` field.  Invalid/unreadable files are skipped.
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
        chains.append({
            "id": chain_id,
            "name": (data.get("name") or chain_id),
            "description": (data.get("description") or "").strip(),
            "path": fpath,
            "collection": (data.get("collection") or ""),
            # ``is_default`` marks the root brain (the Orchestrator chain)
            "is_default": bool(data.get("is_default", False)),
        })
    return chains


def is_system_chain(chain) -> bool:
    """True when *chain* (a dict with a ``collection`` key) is a System chain."""
    return _norm(chain.get("collection")) == SYSTEM_COLLECTION


def read_system_chains(chains_dir) -> list:
    """Every System chain in *chains_dir* — one entry per agent persona."""
    return [c for c in read_chains(chains_dir) if is_system_chain(c)]
