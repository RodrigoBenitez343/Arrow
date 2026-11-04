"""CRAG (Corrective Retrieval Augmented Generation) — full port.

Every runtime stage of `utils/crag` is here, in the order the paper runs it:

    evaluate  -> evaluator.py   (CRAG_Inference.inference / process_flag)
    route     -> pipeline.py    (CRAG_Inference.main: correct/ambiguous/incorrect)
    decompose -> decompose.py   (internal_knowledge_preparation + select_relevants)
    correct   -> external.py    (external_knowledge_preparation, web replaced by
                                 the app's own context stores)
    generate  -> generate.py    (format_prompt / postprocess)

``README.md`` maps every upstream script to its port and names the two
deliberate substitutions (Laya as the retrieval evaluator, the context pool
as the external store).  ``offline/`` holds the torch-side tooling (dataset
building, evaluator fine-tuning, inference, evaluation, calibration) that is
run as scripts, never imported at runtime.
"""

from .decompose import (StripPlanner, decompose, extract_strips_from_psg,
                        select_relevants)
from .evaluator import (AMBIGUOUS, CORRECT, INCORRECT, CRAGUnavailable,
                        EmbeddingEvaluator, LayaEvaluator, LLMEvaluator,
                        RetrievalEvaluator, cache, classify, make_evaluator,
                        process_flag)
from .external import (ChunkStore, CRAGExternalKnowledge, ExternalStore,
                       PoolStreams)
from .generate import build_prompt, evidence_block, postprocess_answer
from .pipeline import CRAGConfig, CRAGPipeline, CRAGResult, default_config_from
from .rewrite import rewrite_query

__all__ = [
    # evaluator stage
    "AMBIGUOUS",
    "CORRECT",
    "INCORRECT",
    "CRAGUnavailable",
    "RetrievalEvaluator",
    "LayaEvaluator",
    "EmbeddingEvaluator",
    "LLMEvaluator",
    "make_evaluator",
    "classify",
    "process_flag",
    "cache",
    # decompose-then-recompose
    "decompose",
    "extract_strips_from_psg",
    "select_relevants",
    "StripPlanner",
    # corrective action
    "CRAGConfig",
    "CRAGPipeline",
    "CRAGResult",
    "default_config_from",
    "CRAGExternalKnowledge",
    "ExternalStore",
    "PoolStreams",
    "ChunkStore",
    "rewrite_query",
    # generation contract
    "build_prompt",
    "evidence_block",
    "postprocess_answer",
]
