"""Chain classification (Qt-free; shared by GUI and player).

A chain that contains an orchestrator — directly or through its imports — is a
BRAIN (it needs inference); everything else is a DETERMINISTIC chain, the kind
that belongs on the orchestrator's ``chains`` port.

Chain-import Output content is delivered DIRECTLY to the tool consumer (the
LLM / agent context): there are no per-Output data ports to map.
"""

import json
import os


def chain_contains_orchestrator(cfg, chain_dir=None, depth=4, _seen=None):
    """True when the chain (or anything it imports) has an orchestrator node.

    Transitive on purpose (cap ``depth``): a deterministic chain that imports
    a brain chain would re-enter inference, so it must classify as a brain.
    Unreadable/malformed imports are skipped — a broken file must never
    brick the editor or the runtime.
    """
    if _seen is None:
        _seen = set()
    if depth <= 0 or not isinstance(cfg, dict):
        return False
    if cfg.get('orchestrator_nodes'):
        return True
    # The Orchestrator is now an LLM-node switch (not a node type): a chain
    # that routes with an LLM node in orchestrator mode is a brain too.
    for node in (cfg.get('llm_nodes') or []):
        if not isinstance(node, dict):
            continue
        if bool(node.get('orchestrator_mode')) or str(
                node.get('mode') or '').strip().lower() in ('orchestrator', 'router'):
            return True
    for node in (cfg.get('chain_import_nodes') or []):
        if not isinstance(node, dict):
            continue
        raw = str(
            node.get('chain_file_path') or node.get('chain_file') or ''
        ).strip()
        if not raw:
            continue
        candidates = [raw, raw + '.json']
        if chain_dir:
            candidates.append(os.path.join(chain_dir, os.path.basename(raw)))
            candidates.append(
                os.path.join(chain_dir, os.path.basename(raw) + '.json'))
        path = ''
        for cand in candidates:
            try:
                if cand and os.path.exists(cand):
                    path = cand
                    break
            except Exception:
                continue
        if not path or path in _seen:
            continue
        _seen.add(path)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                child = json.load(f)
        except Exception:
            continue
        if chain_contains_orchestrator(
                child, os.path.dirname(path), depth - 1, _seen):
            return True
    return False


def classify_chain(cfg, chain_dir=None):
    """'brain' | 'chain' — the single rule behind ports, color and freeze."""
    return 'brain' if chain_contains_orchestrator(cfg, chain_dir) else 'chain'



