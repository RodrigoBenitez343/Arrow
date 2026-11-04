"""CRAG external-knowledge stage, backed by the app's own stores.

Upstream (`external_knowledge_preparation.py`) handles the "retrieval was
wrong" action by rewriting the question into keywords, searching the open
web, scraping the result pages into paragraph strips, and rescoring those
strips with the same evaluator.  This app has no web search (by design), and
its equivalent of an outside store is what the user already wired in:

* the context pool's OTHER streams - learned answers, human-verified
  records and prior-turn history (a long-term store the attached documents
  are not part of),
* another retrieval pass over the attached documents with the REWRITTEN
  query (the query rewrite is what makes it external: a different question
  reaches different material than the one that just failed).

The stage itself is the ported one: gather candidate text from every store,
then decompose and rescore it exactly like the internal branch.  When no
store yields anything, the stage returns nothing and the field escalates to
the user instead of being answered from material CRAG judged wrong.
"""

import logging
import re
from typing import Callable, List, Optional, Sequence

logger = logging.getLogger(__name__)

# Upstream keeps the 5 best strips of the external knowledge.
DEFAULT_TOP_N_EXTERNAL = 5
# Upstream reads at most 5 result pages per query.
DEFAULT_MAX_BLOCKS = 5
# A block shorter than this is a label, not knowledge (upstream dropped page
# paragraphs under 10 chars; a form answer can be short, so keep it low).
_MIN_BLOCK_CHARS = 10

_WORD_RE = re.compile(r"[a-z0-9]{3,}")


def _terms(text: str) -> List[str]:
    return _WORD_RE.findall(str(text or "").lower())


class ExternalStore:
    """A source of candidate text for the corrective branch."""

    name = "store"

    def fetch(self, query: str, limit: int = DEFAULT_MAX_BLOCKS) -> List[str]:
        """Candidate blocks for *query*, best first ('' when none)."""
        raise NotImplementedError


class PoolStreams(ExternalStore):
    """The context pool's non-document streams, filtered by the query.

    The blocks arrive already split by the caller (learned answers, verified
    records, turn history), so this module never parses the pool format.  A
    block is a candidate when it shares at least one content term with the
    query - the cheap "search" step that precedes the evaluator, mirroring
    how the web stage had to pick pages before it could read them.
    """

    name = "pool"

    def __init__(self, blocks: Sequence[str]):
        self._blocks = [str(b).strip() for b in (blocks or [])
                        if str(b or "").strip()]

    def fetch(self, query: str, limit: int = DEFAULT_MAX_BLOCKS) -> List[str]:
        terms = set(_terms(query))
        scored = []
        for block in self._blocks:
            overlap = len(terms & set(_terms(block)))
            if overlap:
                scored.append((overlap, len(block), block))
        scored.sort(key=lambda row: (row[0], -row[1]), reverse=True)
        return [row[2] for row in scored[:max(0, int(limit))]]


class ChunkStore(ExternalStore):
    """The attached corpus, re-retrieved with the rewritten query."""

    name = "documents"

    def __init__(self, retrieve: Optional[Callable[[str], Sequence[str]]] = None,
                 max_chunks: int = DEFAULT_MAX_BLOCKS):
        self._retrieve = retrieve
        self._max_chunks = max(0, int(max_chunks))

    def available(self) -> bool:
        return callable(self._retrieve) and self._max_chunks > 0

    def fetch(self, query: str, limit: int = DEFAULT_MAX_BLOCKS) -> List[str]:
        if not callable(self._retrieve):
            return []
        try:
            chunks = list(self._retrieve(query) or [])
        except Exception as exc:  # noqa: BLE001 - a store failure is not fatal
            logger.debug("CRAG external retrieval failed: %s", exc)
            return []
        return [str(c) for c in chunks if str(c or "").strip()][
            :min(int(limit), self._max_chunks)]


class CRAGExternalKnowledge:
    """Every store's candidates for one rewritten query."""

    def __init__(self, stores: Sequence[ExternalStore] = (),
                 max_blocks: int = DEFAULT_MAX_BLOCKS):
        self.stores = list(stores or [])
        self.max_blocks = max(1, int(max_blocks))

    def usable(self) -> bool:
        return bool(self.stores)

    def visit(self, query: str) -> List[str]:
        """The candidate blocks, ordered by store then store-internal rank."""
        out: List[str] = []
        seen = set()
        for store in self.stores:
            try:
                blocks = store.fetch(query, self.max_blocks)
            except Exception as exc:  # noqa: BLE001
                logger.debug("CRAG external store %r failed: %s",
                             getattr(store, "name", "?"), exc)
                continue
            for block in blocks or []:
                text = str(block or "").strip()
                if len(text) < _MIN_BLOCK_CHARS:
                    continue
                key = text[:200].lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(text)
                if len(out) >= self.max_blocks:
                    return out
        return out


__all__ = [
    "CRAGExternalKnowledge",
    "ChunkStore",
    "ExternalStore",
    "PoolStreams",
    "DEFAULT_MAX_BLOCKS",
    "DEFAULT_TOP_N_EXTERNAL",
]
