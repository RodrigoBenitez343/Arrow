"""The CRAG pipeline: evaluate -> correct / ambiguous / incorrect -> recompose.

Port of `utils/crag/scripts/CRAG_Inference.py:main()`.  Upstream reads the
evaluator's per-query flag and swaps the context wholesale:

    flag 0 (incorrect) -> the external ("web") knowledge
    flag 1 (ambiguous) -> Knowledge1: <internal> [sep] Knowledge2: <external>
    flag 2 (correct)   -> the refined internal knowledge

This class runs exactly that decision, with the two substitutions the app
needs (Laya as the evaluator, the context pool as the external store - see
``evaluator.py`` and ``external.py``) and one budget rule: the strip set that
gets scored is bounded, because one evaluator forward is a model pass.

Nothing here knows about forms, Qt or the player: the caller supplies the
passages, the retrieval callable and the model callable.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence

from .decompose import StripPlanner, select_relevants
from .evaluator import (AMBIGUOUS, CORRECT, INCORRECT, CRAGUnavailable,
                        RetrievalEvaluator, classify)
from .external import CRAGExternalKnowledge
from .rewrite import rewrite_query

logger = logging.getLogger(__name__)

# Upstream's combined knowledge file joins the two sources like this.
_KNOWLEDGE_1 = "Knowledge1: %s"
_KNOWLEDGE_2 = "Knowledge2: %s"
_COMBINED_SEP = " [sep] "


@dataclass
class CRAGConfig:
    """Every knob the corrective loop reads (node config -> here)."""

    upper_threshold: float = 0.60
    lower_threshold: float = 0.40
    decompose_mode: str = "excerption"
    top_k: int = 6
    max_strips: int = 12
    top_n_internal: int = 6
    top_n_external: int = 5
    external: bool = True
    rewrite: bool = True
    char_budget: int = 4000
    max_blocks: int = 5

    def internal_top_n(self) -> int:
        """Upstream keeps 3 strips in ``selection`` mode, 6 otherwise."""
        if (self.decompose_mode or "").strip().lower() == "selection":
            return min(self.top_n_internal, 3)
        return self.top_n_internal


@dataclass
class CRAGResult:
    """One query's corrected knowledge, plus the numbers behind the verdict."""

    action: str = INCORRECT
    query: str = ""
    material: str = ""
    internal: str = ""
    external: str = ""
    rewritten: str = ""
    chunk_scores: List[float] = field(default_factory=list)
    internal_scores: List[float] = field(default_factory=list)
    external_scores: List[float] = field(default_factory=list)
    # The strips that were KEPT, in ranked order, with their scores.  The
    # knowledge string upstream writes is `'; '.join(strips)`; a caller that
    # wants provenance (which passage a strip came from) reads these instead.
    strips: List[str] = field(default_factory=list)
    strip_scores: List[float] = field(default_factory=list)
    # ... and the same lists split by the store they came from, so a caller can
    # keep upstream's `Knowledge1 / Knowledge2` labelling.
    internal_strips: List[str] = field(default_factory=list)
    external_strips: List[str] = field(default_factory=list)
    kept: int = 0
    dropped: int = 0
    forwards: int = 0
    evaluator: str = ""
    elapsed: float = 0.0
    aborted: bool = False

    @property
    def top_score(self) -> float:
        values = [s for s in (self.chunk_scores or []) if s is not None]
        return max(values) if values else 0.0

    @property
    def used_external(self) -> bool:
        return bool(self.external)

    def summary(self) -> str:
        """One log line / route label for the node's table."""
        return ("CRAG: %s, %d kept strip(s) of %d scored, top %.2f, "
                "%d forward(s), %.1fs" % (
                    self.action, self.kept, self.kept + self.dropped,
                    self.top_score, self.forwards, self.elapsed))


class CRAGPipeline:
    """The corrective retrieval pipeline for ONE query at a time."""

    def __init__(self, evaluator: RetrievalEvaluator,
                 external: Optional[CRAGExternalKnowledge] = None,
                 rewrite_llm: Optional[Callable[[str, str], str]] = None,
                 config: Optional[CRAGConfig] = None):
        self.evaluator = evaluator
        self.external = external or CRAGExternalKnowledge()
        self.rewrite_llm = rewrite_llm
        self.config = config or CRAGConfig()

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _halted(stop_flag) -> bool:
        try:
            return bool(stop_flag and stop_flag())
        except Exception:
            return False

    def _cap(self, text: str) -> str:
        budget = int(self.config.char_budget or 0)
        body = str(text or "")
        if budget > 0 and len(body) > budget:
            return body[:budget]
        return body

    def _refine(self, query: str, blocks: Sequence[str],
                scores: Sequence[Optional[float]], top_n: int):
        """Decompose -> score -> keep the top_n strips (upstream refinement)."""
        planner = StripPlanner(self.config.decompose_mode,
                               self.config.max_strips)
        strips = planner.strips(blocks, scores)
        joined, _idxs, ranked = select_relevants(
            strips, query, self.evaluator, top_n)
        return joined, ranked, ranked[:max(0, int(top_n))]

    # -- the pipeline ------------------------------------------------------
    def run(self, query: str, chunks: Optional[Sequence[str]] = None,
            retrieve: Optional[Callable[[str], Sequence[str]]] = None,
            stop_flag=None) -> CRAGResult:
        started = time.time()
        cfg = self.config
        body = str(query or "").strip()
        result = CRAGResult(query=body, evaluator=self.evaluator.name)
        if not body:
            result.elapsed = time.time() - started
            return result

        passages = [str(c) for c in (chunks or []) if str(c or "").strip()]
        if not passages and callable(retrieve):
            try:
                passages = [str(c) for c in (retrieve(body) or [])
                            if str(c or "").strip()]
            except Exception as exc:  # noqa: BLE001 - retrieval is best effort
                logger.debug("CRAG retrieval failed: %s", exc)
                passages = []
        passages = passages[:max(1, int(cfg.top_k))]

        if not passages:
            result.elapsed = time.time() - started
            return result

        # 1. EVALUATE the retrieved passages (upstream: evaluator inference).
        chunk_scores = self.evaluator.score_many(body, passages)
        scored = [s for s in chunk_scores if s is not None]
        if not scored:
            raise CRAGUnavailable(
                "the %s evaluator produced no score" % self.evaluator.name)
        result.chunk_scores = [(-1.0 if s is None else float(s))
                               for s in chunk_scores]

        # 2. BUCKET them into the corrective action (upstream: process_flag).
        result.action = classify(
            result.chunk_scores, cfg.upper_threshold, cfg.lower_threshold)

        if self._halted(stop_flag):
            result.aborted = True
            result.forwards = self.evaluator.forwards
            result.elapsed = time.time() - started
            return result

        # 3. REFINE the internal knowledge (decompose-then-recompose).
        internal, ranked, kept_rows = self._refine(
            body, passages, result.chunk_scores, cfg.internal_top_n())
        result.internal = internal
        result.internal_scores = [float(row[0]) for row in ranked]
        result.strips = [str(row[1]) for row in kept_rows]
        result.strip_scores = [float(row[0]) for row in kept_rows]
        result.internal_strips = list(result.strips)
        result.kept = len(kept_rows)
        result.dropped = max(0, len(ranked) - result.kept)

        if result.action == CORRECT:
            result.material = self._cap(internal)
            result.forwards = self.evaluator.forwards
            result.elapsed = time.time() - started
            return result

        # 4. CORRECTIVE action: rewrite, then the external store.
        if not cfg.external or not self.external.usable():
            # The corrective stage is off or empty.  An INCORRECT verdict still
            # escalates (empty material), while an AMBIGUOUS one keeps the
            # refined internal knowledge - upstream would have combined it with
            # external knowledge that this app does not have.
            result.material = ("" if result.action == INCORRECT
                               else self._cap(internal))
            result.forwards = self.evaluator.forwards
            result.elapsed = time.time() - started
            return result

        rewritten = ""
        if cfg.rewrite:
            # The guard gets the text the rewritten query will actually search,
            # so a keyword the corpus never mentions (a stranger's name the
            # model invented out of the few-shot) cannot become the query.
            rewritten = rewrite_query(body, self.rewrite_llm,
                                      document=" ".join(chunks or ()))
        result.rewritten = rewritten
        external_query = rewritten or body

        blocks = self.external.visit(external_query)
        if blocks:
            block_scores = self.evaluator.score_many(external_query, blocks)
            known = [(-1.0 if s is None else float(s)) for s in block_scores]
            joined, ext_ranked, ext_kept = self._refine(
                external_query, blocks, known, cfg.top_n_external)
            # Only strips the evaluator did not reject may become knowledge:
            # an "incorrect" retrieval must not be replaced by equally wrong
            # external text (upstream simply trusted the web corpus).
            supported = [row for row in ext_kept
                         if float(row[0]) >= cfg.lower_threshold]
            result.external_scores = [float(row[0]) for row in ext_ranked]
            if supported:
                keep_text = [row[1] for row in supported]
                result.external = "; ".join(keep_text)
                result.external_strips = [str(row[1]) for row in supported]
                if result.action == INCORRECT:
                    # The internal knowledge is DISCARDED for this verdict, so
                    # the counters describe the corrective material only.
                    result.kept = len(supported)
                    result.dropped = max(0, len(ext_ranked) - len(supported))
                    result.strips = [str(row[1]) for row in supported]
                    result.strip_scores = [float(row[0]) for row in supported]
                else:
                    result.kept += len(supported)
                    result.dropped += max(0, len(ext_ranked) - len(supported))
                    result.strips += [str(row[1]) for row in supported]
                    result.strip_scores += [float(row[0]) for row in supported]

        if not result.external:
            # Nothing outside supports the field: hand it back empty so the
            # caller asks the user instead of writing a wrong value.
            result.material = "" if result.action == INCORRECT else self._cap(internal)
        elif result.action == AMBIGUOUS and internal:
            result.material = self._cap(_COMBINED_SEP.join([
                _KNOWLEDGE_1 % internal, _KNOWLEDGE_2 % result.external]))
        else:
            result.material = self._cap(result.external)

        result.forwards = self.evaluator.forwards
        result.elapsed = time.time() - started
        return result


def default_config_from(node_cfg: Optional[dict]) -> CRAGConfig:
    """Build the pipeline config from the form node's ``crag_*`` keys."""
    data = node_cfg or {}

    def _f(key, default):
        try:
            return float(data.get(key, default))
        except (TypeError, ValueError):
            return float(default)

    def _i(key, default):
        try:
            return int(data.get(key, default))
        except (TypeError, ValueError):
            return int(default)

    def _b(key, default):
        raw = data.get(key, default)
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "on")

    return CRAGConfig(
        upper_threshold=_f("crag_upper_threshold", 0.60),
        lower_threshold=_f("crag_lower_threshold", 0.40),
        decompose_mode=str(data.get("crag_decompose_mode")
                           or "excerption").strip().lower(),
        top_k=_i("crag_top_k", 6),
        max_strips=_i("crag_max_strips", 12),
        top_n_internal=_i("crag_top_n_internal", 6),
        top_n_external=_i("crag_top_n_external", 5),
        external=_b("crag_external", True),
        rewrite=_b("crag_rewrite", True),
        char_budget=_i("crag_char_budget", 4000),
        max_blocks=_i("crag_max_blocks", 5),
    )


__all__ = [
    "CRAGConfig",
    "CRAGPipeline",
    "CRAGResult",
    "CRAGUnavailable",
    "default_config_from",
]
