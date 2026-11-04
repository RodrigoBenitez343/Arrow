"""CRAG decompose-then-recompose: passages -> strips -> the most relevant.

Direct port of `utils/crag/scripts/internal_knowledge_preparation.py`
(`extract_strips_from_psg`) and `utils/crag/scripts/utils.py`
(`select_relevants`):

* ``fixed_num``  - fixed 50-word windows, a <10-word tail is merged into the
  previous strip;
* ``excerption`` - split on ``?`` then ``. ``, drop a sentence shorter than
  6 words by merging it into the previous one, then concatenate 3 strips;
* ``selection``  - the passage is not divided at all.

``select_relevants`` scores every strip against the query, sorts them
descending and keeps the ``top_n`` strongest, joined with ``'; '``.  A strip
shorter than 4 words scores ``-1.0`` WITHOUT a model forward - an upstream
rule that also doubles as a budget guard here.
"""

from typing import List, Optional, Sequence, Tuple

from .evaluator import RetrievalEvaluator

# Upstream's fixed window, in words.
_FIXED_WINDOW = 50
# A tail shorter than this many words is merged into the previous window.
_FIXED_TAIL_MERGE = 10
# Upstream concatenates this many sentences per excerption strip.
_EXCERPT_GROUP = 3
# A sentence with 5 words or fewer is glued to the previous one.
_EXCERPT_MIN_WORDS = 5
# Strips shorter than this are not even scored (upstream: score -1.0).
_MIN_SCORED_WORDS = 4


def extract_strips_from_psg(psg: str, mode: str = "excerption") -> List[str]:
    """`extract_strips_from_psg`, unchanged apart from the mode test."""
    text = str(psg or "")
    if mode == "fixed_num":
        final_strips: List[str] = []
        words = text.split(" ")
        buf: List[str] = []
        for w in words:
            buf.append(w)
            if len(buf) == _FIXED_WINDOW:
                final_strips.append(" ".join(buf))
                buf = []
        if buf:
            if len(buf) < _FIXED_TAIL_MERGE and final_strips:
                final_strips[-1] += (" " + " ".join(buf))
            else:
                final_strips.append(" ".join(buf))
        return final_strips
    if mode == "excerption":
        question_strips = text.split("?")
        origin_strips: List[str] = []
        for qs in question_strips:
            origin_strips += qs.split(". ")
        strips: List[str] = []
        for s in origin_strips:
            if s in strips:
                continue
            if not strips:
                strips.append(s)
            else:
                if len(s.split()) > _EXCERPT_MIN_WORDS:
                    strips.append(s)
                else:
                    strips[-1] += s
        final_strips = []
        buf = []
        for strip in strips:
            buf.append(strip)
            if len(buf) == _EXCERPT_GROUP:
                final_strips.append(" ".join(buf))
                buf = []
        if buf:
            final_strips.append(" ".join(buf))
        return final_strips
    if mode == "selection":
        return [text]
    return extract_strips_from_psg(text, "excerption")


def decompose(passages: Sequence[str], mode: str = "excerption") -> List[str]:
    """Every passage's strips, in passage order (upstream's flattening)."""
    strips: List[str] = []
    for psg in passages or []:
        strips += extract_strips_from_psg(psg, mode=mode)
    return strips


def score_strip(strip: str, query: str,
                evaluator: RetrievalEvaluator) -> float:
    """One strip's relevance; short strips score -1.0 for free (upstream)."""
    text = str(strip or "")
    if len(text.split()) < _MIN_SCORED_WORDS:
        return -1.0
    score = evaluator.score(query, text)
    return -1.0 if score is None else float(score)


def select_relevants(strips: Sequence[str], query: str,
                     evaluator: RetrievalEvaluator, top_n: int = 5
                     ) -> Tuple[str, List[str], List[Tuple[float, str, int]]]:
    """`select_relevants`: score, sort descending, keep the top *top_n*.

    Returns ``(joined, idxs, ranked)`` - ``joined`` is the ``'; '``-joined
    knowledge string upstream writes to the ``correct`` file, ``idxs`` the
    original positions in ranked order, ``ranked`` the ``(score, strip, idx)``
    rows for diagnostics.
    """
    ranked: List[Tuple[float, str, int]] = []
    for i, strip in enumerate(strips or []):
        ranked.append((score_strip(strip, query, evaluator), str(strip), i))
    ranked.sort(key=lambda row: row[0], reverse=True)
    keep = ranked[:max(0, int(top_n))]
    ctxs = [row[1] for row in keep]
    idxs = [str(row[2]) for row in ranked]
    return "; ".join(ctxs), idxs, ranked


def strip_scores(ranked: Sequence[Tuple[float, str, int]]) -> List[float]:
    """The scores of a :func:`select_relevants` result, in kept order."""
    return [float(row[0]) for row in ranked or []]


class StripPlanner:
    """Decompose one query's passages inside a fixed strip budget.

    Upstream decomposes every retrieved passage and scores every strip.  One
    Laya forward costs ~2 s, so the strip set is bounded: passages are
    decomposed highest-chunk-score first and scoring stops at ``max_strips``.
    Ordering by the passage score keeps the bounded set the most promising
    one instead of an arbitrary prefix.
    """

    def __init__(self, mode: str = "excerption", max_strips: int = 12):
        self.mode = (mode or "excerption").strip().lower()
        self.max_strips = max(1, int(max_strips))

    def strips(self, passages: Sequence[str],
               passage_scores: Optional[Sequence[float]] = None) -> List[str]:
        order = list(range(len(passages or [])))
        if passage_scores and len(passage_scores) == len(order):
            order.sort(
                key=lambda i: (-1e9 if passage_scores[i] is None
                               else float(passage_scores[i])),
                reverse=True,
            )
        out: List[str] = []
        for i in order:
            for strip in extract_strips_from_psg(passages[i], mode=self.mode):
                if strip.strip():
                    out.append(strip)
                if len(out) >= self.max_strips:
                    return out
        return out


def run_select(strips: Sequence[str], query: str,
               evaluator: RetrievalEvaluator, top_n: int) -> str:
    """Convenience used by tests and the offline scripts: the joined string."""
    joined, _idxs, _ranked = select_relevants(strips, query, evaluator, top_n)
    return joined


__all__ = [
    "decompose",
    "extract_strips_from_psg",
    "score_strip",
    "select_relevants",
    "strip_scores",
    "run_select",
    "StripPlanner",
]
