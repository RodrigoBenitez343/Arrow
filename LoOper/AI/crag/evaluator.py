"""CRAG retrieval evaluator: one relevance SCORE per (query, passage).

Port of the evaluator stage of `utils/crag` (`CRAG_Inference.py:inference()`
and `:process_flag()`): every retrieved passage is scored against the query,
each score is bucketed into correct / ambiguous / incorrect by two
thresholds, and the per-query bucket decides the corrective action.

Upstream ships a fine-tuned T5-large sequence classifier whose ``logits``
are the score.  That model is not in `utils/crag` (the weights are an
external download), so the score comes from the app's own decision engine:
Laya answers one ``noul`` question per (state=passage, instruction=<query>)
in a single encoder pass and returns a CALIBRATED probability — the same
"one number per (query, passage) pair" the classifier emitted, on a 0..1
scale instead of a raw logit.  The threshold semantics are unchanged; only
the scale moves (see ``DEFAULT_THRESHOLDS``).

``EmbeddingEvaluator`` and ``LLMEvaluator`` are the fallbacks used when the
engine is unavailable (``LOOPER_LAYA=off``, missing binary/model) and when
a caller explicitly asks for them.
"""

import hashlib
import logging
import re
from typing import Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# ── the three knowledge buckets (upstream: identification_flag 2 / 1 / 0) ──
CORRECT = "correct"
AMBIGUOUS = "ambiguous"
INCORRECT = "incorrect"

_FLAG_CORRECT = "2"
_FLAG_AMBIGUOUS = "1"
_FLAG_INCORRECT = "0"

_FLAG_TO_ACTION = {
    _FLAG_CORRECT: CORRECT,
    _FLAG_AMBIGUOUS: AMBIGUOUS,
    _FLAG_INCORRECT: INCORRECT,
}

# One threshold pair per backend, in that backend's own score space.  The
# upstream defaults (`--upper_threshold 10 / --lower_threshold -10`) were
# placeholders for fine-tuning; these are the calibrated starting points for
# each backend and are overridden by the node's own two sliders.
DEFAULT_THRESHOLDS = {
    "laya": (0.60, 0.40),      # calibrated probability
    "embed": (0.60, 0.40),     # cosine similarity
    "llm": (0.60, 0.40),       # verbalized 0-10 score, normalised to 0..1
}

# The statement Laya is asked about each passage.  Short and typed: the
# encoder answers "does this passage answer this query", which is the same
# question the T5 classifier was fine-tuned on.
_LAYA_STATEMENT = "This passage contains the answer to: {query}"


class CRAGUnavailable(RuntimeError):
    """No evaluator backend could score the passages."""


class ScoreCache:
    """Process-global (namespace, query, passage) -> score memory.

    One form page asks about the same chunks over and over (a retry, a repair
    pass, a second field that shares the header block), and every miss is a
    model forward.  Bounded FIFO: a run must not grow without limit.
    """

    def __init__(self, limit: int = 2048):
        self.limit = max(16, int(limit))
        self._data: Dict[str, float] = {}

    @staticmethod
    def key(namespace: str, query: str, text: str) -> str:
        h = hashlib.sha1()
        h.update(str(namespace or "").encode("utf-8", "ignore"))
        h.update(b"\x00")
        h.update(str(text or "").encode("utf-8", "ignore"))
        h.update(b"\x00")
        h.update(str(query or "").encode("utf-8", "ignore"))
        return h.hexdigest()

    def get(self, namespace: str, query: str, text: str) -> Optional[float]:
        return self._data.get(self.key(namespace, query, text))

    def put(self, namespace: str, query: str, text: str, score: float) -> None:
        if len(self._data) >= self.limit:
            for _k in list(self._data.keys())[: max(1, self.limit // 8)]:
                self._data.pop(_k, None)
        self._data[self.key(namespace, query, text)] = float(score)

    def clear(self) -> None:
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)


_CACHE = ScoreCache()


def cache() -> ScoreCache:
    """The process-global score cache (shared by every evaluator)."""
    return _CACHE


class RetrievalEvaluator:
    """Score (query, passage) pairs.  Subclasses implement ``_score_one``."""

    name = "base"
    namespace = "base"

    def __init__(self, cache_enabled: bool = True):
        # Forwards actually paid to the backend this run (cache hits are free).
        self.forwards = 0
        self._cache_enabled = bool(cache_enabled)

    # -- backend hooks -----------------------------------------------------
    def available(self) -> bool:
        return True

    def _score_one(self, query: str, text: str) -> Optional[float]:
        raise NotImplementedError

    def release(self) -> None:
        """Free whatever the backend holds (burst engines unload here)."""

    # -- public ------------------------------------------------------------
    def score_many(self, query: str,
                   texts: Sequence[str]) -> List[Optional[float]]:
        """Score every passage; ``None`` marks a passage the backend missed."""
        out: List[Optional[float]] = []
        for text in texts or []:
            body = str(text or "")
            if self._cache_enabled:
                hit = _CACHE.get(self.namespace, query, body)
                if hit is not None:
                    out.append(hit)
                    continue
            score = self._score_one(query, body)
            if score is not None:
                self.forwards += 1
                if self._cache_enabled:
                    _CACHE.put(self.namespace, query, body, score)
            out.append(score)
        return out

    def score(self, query: str, text: str) -> Optional[float]:
        return self.score_many(query, [text])[0]


class LayaEvaluator(RetrievalEvaluator):
    """The calibrated ``noul`` probability from the embedded Laya engine."""

    name = "laya"
    namespace = "laya"

    def available(self) -> bool:
        """True once the engine is up; starts it on demand (one burst load).

        ``laya_client.available()`` is only true AFTER the daemon loaded, so a
        cold engine would otherwise look unavailable and every form run would
        silently fall back to embeddings.  ``ensure_running`` is the client's
        own lazy-start path: it loads the model, or disables the engine for the
        session when the binary/model is missing.
        """
        try:
            from AI.laya_client import available as _ready
            from AI.laya_client import ensure_running
            return bool(_ready() or ensure_running())
        except Exception:
            return False

    def _score_one(self, query: str, text: str) -> Optional[float]:
        try:
            from AI.laya_client import noul
        except Exception:
            return None
        statement = _LAYA_STATEMENT.format(query=str(query or "")[:400])
        return noul(str(text or "")[:4000], statement)

    def release(self) -> None:
        """Stop the daemon: it shares one memory pool with llama-server."""
        try:
            from AI.laya_client import release as _release
            _release(wait=1.0)
        except Exception as exc:  # noqa: BLE001 - unload is best effort
            logger.debug("Laya release failed: %s", exc)


class EmbeddingEvaluator(RetrievalEvaluator):
    """Cosine fallback: no engine, no model prose, one embeddings round-trip."""

    name = "embed"
    namespace = "embed"

    def __init__(self, embed: Callable[[Sequence[str]], Sequence[Sequence[float]]],
                 cache_enabled: bool = True):
        super().__init__(cache_enabled=cache_enabled)
        self._embed = embed

    def available(self) -> bool:
        return callable(self._embed)

    def score_many(self, query: str,
                   texts: Sequence[str]) -> List[Optional[float]]:
        """One embeddings call for the query + every passage (not N calls)."""
        items = [str(t or "") for t in (texts or [])]
        if not items:
            return []
        result: List[Optional[float]] = [
            _CACHE.get(self.namespace, query, t) if self._cache_enabled else None
            for t in items
        ]
        missing = [(i, t) for i, t in enumerate(items) if result[i] is None]
        if not missing:
            return result
        try:
            vectors = list(self._embed(
                [str(query or "")] + [t for _i, t in missing]) or [])
        except Exception as exc:  # noqa: BLE001 - degrade, never crash a pass
            logger.debug("CRAG embedding evaluator failed: %s", exc)
            return result
        if len(vectors) != len(missing) + 1:
            return result
        qv, rest = vectors[0], vectors[1:]
        for (i, text), vec in zip(missing, rest):
            score = _cosine(qv, vec)
            self.forwards += 1
            if self._cache_enabled:
                _CACHE.put(self.namespace, query, text, score)
            result[i] = score
        return result


class LLMEvaluator(RetrievalEvaluator):
    """Verbalized score (0-10) from the node's own engine, one batch call.

    Last resort: it costs a completion per batch, so the pipeline only uses it
    when neither Laya nor embeddings are usable, or when a caller asks for it.
    """

    name = "llm"
    namespace = "llm"

    SYSTEM = (
        "You rate how well each passage answers a question. Answer with one "
        "score per passage and nothing else."
    )
    PROMPT = (
        "Question: {query}\n\n"
        "Passages:\n{passages}\n\n"
        "For each passage output one line '<n>: <score>' where <n> is the "
        "passage number and <score> is an integer 0-10 (10 = the passage "
        "contains the answer, 0 = unrelated). One line per passage, nothing "
        "else.\n\nScores:"
    )

    def __init__(self, llm: Callable[[str, str], str], batch: int = 12,
                 cache_enabled: bool = True):
        super().__init__(cache_enabled=cache_enabled)
        self._llm = llm
        self._batch = max(1, int(batch))

    def available(self) -> bool:
        return callable(self._llm)

    def score_many(self, query: str,
                   texts: Sequence[str]) -> List[Optional[float]]:
        out: List[Optional[float]] = []
        for text in texts or []:
            body = str(text or "")
            out.append(_CACHE.get(self.namespace, query, body)
                       if self._cache_enabled else None)
        # Score the misses in batches; a hit keeps its cached value.
        idx = [i for i, (t, v) in enumerate(zip(texts or [], out)) if v is None]
        for start in range(0, len(idx), self._batch):
            chunk_idx = idx[start:start + self._batch]
            body = "\n".join(
                "%d. %s" % (n + 1, str(texts[i] or "")[:600])
                for n, i in enumerate(chunk_idx)
            )
            scores = self._ask(query, body, len(chunk_idx))
            for n, i in enumerate(chunk_idx):
                score = scores.get(n + 1)
                if score is None:
                    # Missing line: not scored, not cached, treated as no hit.
                    out[i] = None
                    continue
                out[i] = score
                self.forwards += 1
                if self._cache_enabled:
                    _CACHE.put(self.namespace, query, str(texts[i] or ""), score)
        return out

    def _ask(self, query: str, passages: str, n: int) -> Dict[int, float]:
        try:
            reply = self._llm(self.PROMPT.format(
                query=str(query or "")[:400], passages=passages), self.SYSTEM)
        except Exception as exc:  # noqa: BLE001
            logger.debug("CRAG LLM evaluator failed: %s", exc)
            return {}
        scores: Dict[int, float] = {}
        for line in str(reply or "").splitlines():
            m = re.match(r"\s*(\d+)\s*[:.)-]\s*(\d+(?:\.\d+)?)", line)
            if not m:
                continue
            try:
                n_idx = int(m.group(1))
                value = float(m.group(2))
            except ValueError:
                continue
            if 1 <= n_idx <= n:
                scores[n_idx] = max(0.0, min(1.0, value / 10.0))
        return scores


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    try:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(float(x) * float(y) for x, y in zip(a, b))
        na = sum(float(x) * float(x) for x in a) ** 0.5
        nb = sum(float(y) * float(y) for y in b) ** 0.5
        if not na or not nb:
            return 0.0
        return dot / (na * nb)
    except Exception:
        return 0.0


# ── the upstream bucketing, ported verbatim ───────────────────────────────
def process_flag(scores: Sequence[float], n_docs: int,
                 threshold1: float, threshold2: float) -> List[int]:
    """`CRAG_Inference.process_flag`, unchanged.

    ``scores`` are bucketed into ``'2'`` (>= threshold1), ``'1'``
    (>= threshold2) or ``'0'``, then grouped every *n_docs* entries: a group
    holding any ``'2'`` is correct (2), elif any ``'1'`` ambiguous (1), else
    incorrect (0).
    """
    flags: List[str] = []
    for score in scores or []:
        try:
            value = float(score)
        except (TypeError, ValueError):
            value = float("-inf")
        if value >= threshold1:
            flags.append(_FLAG_CORRECT)
        elif value >= threshold2:
            flags.append(_FLAG_AMBIGUOUS)
        else:
            flags.append(_FLAG_INCORRECT)

    n = max(1, int(n_docs))
    identification_flag: List[int] = []
    tmp_flag: List[str] = []
    for i, flag in enumerate(flags):
        tmp_flag.append(flag)
        if i % n == n - 1:
            if _FLAG_CORRECT in tmp_flag:
                identification_flag.append(2)
            elif _FLAG_AMBIGUOUS in tmp_flag:
                identification_flag.append(1)
            else:
                identification_flag.append(0)
            tmp_flag = []
    if tmp_flag:
        if _FLAG_CORRECT in tmp_flag:
            identification_flag.append(2)
        elif _FLAG_AMBIGUOUS in tmp_flag:
            identification_flag.append(1)
        else:
            identification_flag.append(0)
    return identification_flag


def classify(scores: Sequence[float], upper_threshold: float,
             lower_threshold: float) -> str:
    """The corrective action for ONE query's passage scores."""
    if not scores:
        return INCORRECT
    flags = process_flag(scores, len(scores), upper_threshold, lower_threshold)
    flag = str(flags[0]) if flags else "0"
    return _FLAG_TO_ACTION.get(flag, INCORRECT)


def flags_per_score(scores: Sequence[float], upper_threshold: float,
                    lower_threshold: float) -> List[str]:
    """The per-passage bucket ('2' / '1' / '0') behind :func:`classify`."""
    out: List[str] = []
    for score in scores or []:
        try:
            value = float(score)
        except (TypeError, ValueError):
            value = float("-inf")
        if value >= upper_threshold:
            out.append(_FLAG_CORRECT)
        elif value >= lower_threshold:
            out.append(_FLAG_AMBIGUOUS)
        else:
            out.append(_FLAG_INCORRECT)
    return out


def make_evaluator(kind: str = "auto",
                   embed: Optional[Callable] = None,
                   llm: Optional[Callable] = None) -> RetrievalEvaluator:
    """Resolve the configured backend; ``auto`` = Laya, else embeddings.

    Unknown kinds and unusable backends degrade the same way — never to the
    old multi-cycle probe loop, which no longer exists.
    """
    want = str(kind or "auto").strip().lower()
    if want == "laya" or want == "auto":
        evaluator = LayaEvaluator()
        if evaluator.available():
            return evaluator
        if want == "laya":
            logger.info("CRAG: Laya requested but unavailable - using embeddings")
    if want == "llm" and callable(llm):
        evaluator = LLMEvaluator(llm)
        if evaluator.available():
            return evaluator
        logger.info("CRAG: LLM evaluator requested but no model call given")
    if callable(embed):
        return EmbeddingEvaluator(embed)
    logger.warning("CRAG: no evaluator backend available")
    return LayaEvaluator()  # every score returns None -> pipeline raises
