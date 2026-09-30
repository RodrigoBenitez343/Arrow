import copy
import json
import logging
import os
import re
import shutil
import subprocess as _sp
import sys
import threading
import time
from typing import Any, Dict, Optional

import requests as _rq
logger = logging.getLogger(__name__)


def _resolve_tool_token(token, tool_ids, alias_map):
    """Resolve a Router-LLM tool token to a connected tool id (or None).

    Handles the shapes small models actually emit: the raw node id, the
    alias (<linkedin>) or its bare form, and near-misses built around the
    alias ("linkedin_job_search" for <linkedin>).  The near-miss case used
    to fall through to "tool id does not match any connected tool" — the
    node then finished with EMPTY output and the query was silently
    dropped even though the model made the right choice.  Near-miss
    matching is whole-word (the token is split on non-alphanumerics) and is
    accepted only when exactly ONE connected tool matches, so an ambiguous
    token stays unresolved instead of routing to a guess.
    """
    try:
        _val = str(token or '').strip()
        if not _val:
            return None
        if _val in tool_ids:
            return _val
        _hit = alias_map.get(_val)
        if _hit and _hit in tool_ids:
            return _hit
        _bare = _val.strip('<>')
        for _a, _tid in alias_map.items():
            if _tid in tool_ids and _a.strip('<>') == _bare:
                return _tid
        _words = {w for w in re.split(r'[^0-9a-z]+', _val.lower()) if len(w) > 1}
        if _words:
            _hits = {
                _tid for _a, _tid in alias_map.items()
                if _tid in tool_ids and _a.strip('<>').strip().lower() in _words
            }
            if len(_hits) == 1:
                return _hits.pop()
        return None
    except Exception:
        return None


def _reselect_tool_with_model(request_text, tool_ids, tool_desc_map,
                              alias_map, model_reply, llm_config, api_url,
                              use_llamacpp):
    """Ask the node's own model to pick ONE tool from the ACTUAL candidate list.

    The deterministic resolver handles the exact shapes (raw id, alias, bare
    alias, unambiguous near-miss).  Everything after it is a GUESS built on
    word sharing between the reply and a tool description, and a wrong pick
    costs the whole turn (the query is dropped when nothing is selected).
    So when NOTHING resolved, the model that made the choice is asked once
    more - this time against the list it was supposed to choose from.

    Returns a connected tool id, or None when the model is unavailable,
    answers NONE, or names something outside the list; the caller then keeps
    its heuristics unchanged.
    """
    try:
        if not use_llamacpp or not tool_ids:
            return None
        _alias_of = {}
        for _alias, _tid in (alias_map or {}).items():
            if _tid in tool_ids:
                _alias_of.setdefault(_tid, str(_alias))
        _lines = []
        for _tid in tool_ids:
            _name = _alias_of.get(_tid) or str(_tid)
            _desc = str((tool_desc_map or {}).get(_tid, "") or "").strip()
            _lines.append(
                "- %s: %s" % (_name, _desc[:200]) if _desc else "- %s" % _name
            )
        _prompt = (
            "Choose the ONE tool that serves the request below.\n"
            "Request:\n%s\n\n"
            "Tools:\n%s\n\n"
            "Answer with exactly one line, USE_TOOL:<name>, using a name from "
            "the list. When no tool fits, answer NONE.\n"
            "USE_TOOL:"
            % (str(request_text or "")[:600], "\n".join(_lines))
        )
        _raw = _llamacpp_raw_completion(
            _prompt, llm_config, api_url, max_tokens=32, temperature=0.0,
        )
        _token = ""
        for _line in str(_raw or "").splitlines():
            if _line.strip():
                _token = _line.strip()
                break
        for _marker in ("USE_TOOL:", "CALL_TOOL:", "TOOL:"):
            _token = _token.replace(_marker, "")
        _token = _token.strip().strip("<>").strip().strip(".,;:")
        if not _token or _token.upper() == "NONE":
            return None
        _hit = _resolve_tool_token(_token, tool_ids, alias_map or {})
        if _hit:
            logger.info(
                "Tool re-selection: %r -> %s (the reply %r resolved to no "
                "connected tool)",
                _token, _hit, str(model_reply or "")[:80],
            )
        return _hit
    except Exception as exc:
        logger.warning("Tool re-selection failed: %s", exc)
        return None


def _parse_relevance_lines(raw, n):
    """Parse '<n>: YES|NO' verdict lines into a list of *n* booleans.

    Returns None unless EVERY claim got a verdict, so a truncated or a
    skipped answer falls back to the lexical gate instead of silently
    dropping findings.
    """
    try:
        text = str(raw or "")
    except Exception:
        return None
    if not text.strip() or n <= 0:
        return None
    seen = {}
    for m in re.finditer(
        r"(?im)^\s*[-*.]?\s*(\d+)\s*[:.)]?\s*(yes|no|true|false)\b", text
    ):
        _idx = int(m.group(1))
        if 1 <= _idx <= n and _idx not in seen:
            seen[_idx] = m.group(2).lower() in ("yes", "true")
    if len(seen) < n:
        return None
    return [bool(seen[i]) for i in range(1, n + 1)]


def _tool_selection_text(selected_tool_id, tool_desc_map, args):
    """Readable text published in place of a raw routing command.

    ``USE_TOOL:<id>`` is execution metadata: the id routes the tool and is
    consumed right there — it must never surface in the LLM node's outputs.
    What gets published instead is the tool's DESCRIPTION (what the chain
    tool actually does) — the alias is only a name that describes nothing,
    so it is deliberately not used.  JSON arguments, when present, are
    appended as the provided input, e.g. ``does arithmetic (input: {"a": 1})``.
    """
    _tid = str(selected_tool_id or '').strip()
    try:
        _desc = str((tool_desc_map or {}).get(_tid, '') or '').strip()
    except Exception:
        _desc = ''
    _text = _desc or _tid
    if args:
        try:
            _text += f" (input: {json.dumps(args, ensure_ascii=False)})"
        except Exception:
            pass
    return _text


def _find_bundled_embedding_dir(model_name: str) -> Optional[str]:
    """Locate a bundled sentence-transformers model directory.

    Direct engine mode resolves the model from the bundle
    (exe_dir/models/<name>, exe_dir/AI/models/<name>, _MEIPASS/LoOper/
    AI/models/<name>) so the frozen agent never touches huggingface.co.
    Returns None when not bundled — callers must NOT fall back to a
    network download in direct mode.
    """
    base = os.path.basename(model_name)
    candidates = []
    models_roots = []
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        for prefix in (
            "",
            "models",
            "AI",
            "_internal",
            os.path.join("_internal", "AI"),
            os.path.join("_internal", "LoOper", "AI"),
        ):
            candidates.append(os.path.join(exe_dir, prefix, "models", base))
            models_roots.append(os.path.join(exe_dir, prefix, "models"))
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            for sub in ("", "LoOper"):
                candidates.append(
                    os.path.join(meipass, sub, "AI", "models", base)
                )
                models_roots.append(os.path.join(meipass, sub, "AI", "models"))
    try:
        from AI.config_loader import get_models_dir
        _md = get_models_dir()
        candidates.append(os.path.join(_md, base))
        models_roots.append(_md)
    except Exception:
        pass
    for candidate in candidates:
        if os.path.isdir(candidate) and os.path.isfile(
            os.path.join(candidate, "config.json")
        ):
            return candidate
    # ── Flattened-layout fallback (older builds) ──
    # PyInstaller directory datas used to flatten the model files directly
    # into the models dir (LoOper/AI/models/model.safetensors ... without the
    # all-MiniLM-L6-v2 subdir).  Rebuild a proper model dir in the runtime
    # dir from the whitelisted embedding files so those builds also work
    # fully offline.
    try:
        _EMBED_FILES = {
            "config.json", "config_sentence_transformers.json",
            "model.safetensors", "pytorch_model.bin", "modules.json",
            "tokenizer.json", "tokenizer_config.json", "vocab.txt",
            "special_tokens_map.json", "sentence_bert_config.json",
            "README.md",
        }
        for root in models_roots:
            if not os.path.isdir(root):
                continue
            if not (os.path.isfile(os.path.join(root, "model.safetensors"))
                    or os.path.isfile(os.path.join(root, "pytorch_model.bin"))):
                continue
            if not os.path.isfile(os.path.join(root, "modules.json")):
                continue
            # This models dir has flattened embedding files — assemble a
            # proper model dir under the durable runtime dir (never _MEIPASS).
            try:
                from AI.runtime_paths import get_runtime_dir
                model_dir = os.path.join(
                    get_runtime_dir(), "embedding_models", base
                )
            except Exception:
                model_dir = os.path.join(
                    os.environ.get("TEMP", "."), "embedding_models", base
                )
            os.makedirs(model_dir, exist_ok=True)
            for fname in os.listdir(root):
                if fname in _EMBED_FILES:
                    src = os.path.join(root, fname)
                    if os.path.isfile(src):
                        shutil.copy2(src, os.path.join(model_dir, fname))
                elif fname == "1_Pooling" and os.path.isdir(
                    os.path.join(root, fname)
                ):
                    shutil.copytree(
                        os.path.join(root, fname),
                        os.path.join(model_dir, fname),
                        dirs_exist_ok=True,
                    )
            if os.path.isfile(os.path.join(model_dir, "config.json")):
                logger.info(
                    "Embedding model reconstructed from flattened bundle "
                    "layout at %s", model_dir,
                )
                return model_dir
    except Exception as exc:
        logger.warning(
            "Flattened embedding fallback failed: %s", exc
        )
    return None


class _ServerEmbeddingClient:
    """Duck-typed embedding client backed by the lazy llama.cpp server.

    Mirrors the ``OllamaClient.embeddings(model, texts)`` interface using
    ``AI.embedding_server`` (embeddinggemma GGUF in --embeddings mode) —
    fully offline, no localhost:11434, no api_url fallback, no PyTorch.
    Every failure returns ``[]`` so callers fall back to lexical ranking.
    """

    def __init__(self, model_name: str = "embeddinggemma-300M-Q8_0.gguf"):
        self.debug = True
        self._model_name = model_name

    def embeddings(self, model_name, texts):
        """Return a list of embedding vectors for the given texts."""
        try:
            from AI import embedding_server

            vectors = embedding_server.embed_texts(list(texts))
        except Exception as exc:
            logger.debug("ServerEmbeddingClient: server unavailable: %s", exc)
            return []
        if not vectors or len(vectors) != len(list(texts)):
            logger.warning(
                "ServerEmbeddingClient: embedding server returned %s vectors "
                "for %d texts",
                len(vectors) if vectors else 0, len(list(texts)),
            )
            return []
        return vectors

    def close(self):
        pass


class _OllamaEmbedClient:
    """Adapter exposing ``embed(texts)`` over an OllamaClient's embeddings.

    Lets the ProbeBasedConsolidator use the node's Ollama embedding model
    (e.g. nomic-embed-text) for both chunk and probe vectors, keeping the
    retrieval in a single vector space for Ollama-based nodes.
    """

    def __init__(self, client, model_name: str):
        self._client = client
        self._model_name = model_name

    def embed(self, texts):
        if self._client is None:
            return None
        try:
            embs = self._client.embeddings(self._model_name, list(texts))
            return embs or None
        except Exception as exc:
            logger.debug("Ollama embed failed: %s", exc)
            return None


def _resolve_rag_embedding_model(llm_config, use_llamacpp: bool) -> str:
    """Map the node's ``rag_embedding_model`` to a concrete backend model.

    Empty / ``auto`` / ``default`` selects the engine-appropriate default:
    llama.cpp nodes → ``embeddinggemma-300M-Q8_0.gguf`` (local llama.cpp
    embedding server, fully offline); Ollama nodes → ``nomic-embed-text``.
    """
    raw = str(llm_config.get("rag_embedding_model") or "").strip()
    if raw.lower() in ("", "auto", "default"):
        return "embeddinggemma-300M-Q8_0.gguf" if use_llamacpp else "nomic-embed-text"
    if use_llamacpp and not raw.lower().endswith(".gguf"):
        # An Ollama-style embedding model name (e.g. nomic-embed-text) cannot
        # be loaded by the local llama.cpp embedding server.  Normalizing here
        # keeps consolidation/retrieval actually running on llamacpp nodes
        # (previously the stale Ollama name was echoed into the "disabled"
        # decision line and the node silently fell back to raw text passing).
        logger.warning(
            "RAG: embedding model '%s' is not a GGUF file but the engine is "
            "llama.cpp — using embeddinggemma-300M-Q8_0.gguf instead",
            raw,
        )
        return "embeddinggemma-300M-Q8_0.gguf"
    return raw


def _trim_to_context_budget(text: str, llm_config, reserve_chars: int = 0) -> str:
    """Trim *text* to fit the node's llama.cpp context window.

    Uses a rough 3.5 chars/token estimate and keeps the NEWEST tail (later
    context turns are the most relevant for the current question).  No-op
    when the node does not declare a context size (native / unknown window)
    or when the text already fits.
    """
    try:
        ctx = int(llm_config.get("llamacpp_context_size", 0) or 0)
        if ctx <= 0:
            return text
        budget = max(0, int(ctx * 3.5) - max(0, reserve_chars))
        if len(text) <= budget:
            return text
        kept = text[-budget:]
        logger.warning(
            "Context injection trimmed %d -> %d chars to fit llama.cpp "
            "context (%d tokens)",
            len(text), len(kept), ctx,
        )
        return kept
    except Exception:
        return text


from .tool_retriever import _RETRIEVER as _TOOL_RETRIEVER

try:
    from AI import inprocess_transport as _direct_transport
except Exception:
    # Non-fatal: transport is only required in direct engine mode.  Fall
    # back to RuntimeError so any missed failure still raises loudly.
    _direct_transport = None


from .config_utils import get_default_api_url
try:
    from logging_setup import log_block, log_table
except Exception:
    def log_block(logger, level, title, content, lang="text"):
        text = content if isinstance(content, str) else repr(content)
        logger.log(level, "=== %s ===\n%s", title, text)

    def log_table(logger, level, title, rows):
        if not rows:
            return
        text = "\n".join(f"{k}: {v}" for k, v in rows)
        logger.log(level, "=== %s ===\n%s", title, text)
from .inputs import (
    _context_input,
    _prompt_port_input,
    _record_output_type,
    get_input_text,
    upstream_value,
)

from .prompt_utils import (
    _HISTORY_INSERT_MARKER,
    assemble_enhanced_prompt,
    substitute_variables,
)
from .rag_utils import build_rag_context, build_rag_context_from_docs, build_graphrag_context, GraphRAGCursor
from .vision_utils import prepare_vision_images
from ...screenshot_cleanup import cleanup_screenshot


def _strip_think_tags(text):
    """
    Strip chain-of-thought/reasoning tags from LLM output.
    
    Handles common formats:
      <think>...</think>       - DeepSeek, Qwen, Granite, etc.
       ...     - Standard thought tags
      <reasoning>...</reasoning> - Reasoning tags
      <scratchpad>...</scratchpad> - Scratchpad tags
      [thinking]...[/thinking] - Bracket-style tags
    
    Also strips any content before the final <answer>...</answer> or [answer]...[/answer]
    if those tags are present (keeps only the answer block).
    
    Args:
        text: Raw LLM response text
        
    Returns:
        Cleaned text with think tags removed
    """
    if not text or not isinstance(text, str):
        return text
    
    # Try answer tags first — if present, keep only the answer content
    import re as _re
    answer_match = _re.search(r'<answer>(.*?)</answer>', text, _re.DOTALL | _re.IGNORECASE)
    if answer_match:
        return answer_match.group(1).strip()
    answer_match = _re.search(r'\[answer\](.*?)\[/answer\]', text, _re.DOTALL | _re.IGNORECASE)
    if answer_match:
        return answer_match.group(1).strip()
    
    # Remove standard think tags: <think>...</think>
    stripped = _re.sub(r'<think>.*?</think>', '', text, flags=_re.DOTALL | _re.IGNORECASE)
    # Handle dangling </think> tag (no matching opening tag).
    # Some models output reasoning text followed by a standalone </think> before the final answer,
    # e.g. "...yes or no...\n</think>\nNo." — we must strip everything before and including </think>
    # to prevent reasoning text (with words like "yes" or "no") from polluting the answer.
    dangle = _re.search(r'</think>', stripped, flags=_re.IGNORECASE)
    if dangle:
        stripped = stripped[dangle.end():].strip()
    # Remove '```think```' code block style
    stripped = _re.sub(r'```think\s*.*?```', '', stripped, flags=_re.DOTALL | _re.IGNORECASE)
    # Remove <reasoning>...</reasoning>
    stripped = _re.sub(r'<reasoning>.*?</reasoning>', '', stripped, flags=_re.DOTALL | _re.IGNORECASE)
    # Remove <scratchpad>...</scratchpad>
    stripped = _re.sub(r'<scratchpad>.*?</scratchpad>', '', stripped, flags=_re.DOTALL | _re.IGNORECASE)
    # Remove [thinking]...[/thinking]
    stripped = _re.sub(r'\[thinking\].*?\[/thinking\]', '', stripped, flags=_re.DOTALL | _re.IGNORECASE)
    # Remove standalone  tags (thinking block without closing tag, in some models)
    stripped = _re.sub(r'<\s*/?\s*think\s*>', '', stripped, flags=_re.DOTALL | _re.IGNORECASE)
    # Remove markdown think headers: ## Thinking
    stripped = _re.sub(r'#{1,3}\s*think(?:ing)?\s*', '', stripped, flags=_re.IGNORECASE)
    # Remove trailing/leading whitespace
    stripped = stripped.strip()
    
    return stripped


# One-shot probe-latency warning: raised once per process when an
# LLM-generated retrieval probe takes too long, so users can tune the
# probe budget without log spam on every slow call.
_PROBE_LATENCY_WARNED = False


def _generate_intent_plan(enhanced_intent, llm_config, api_url):
    """Create the INITIAL INTENT PLAN that drives all probe derivation.

    The pipeline must not "shoot" probes straight from the user prompt and
    context: it first synthesizes WHAT it needs to know, WHERE to look, and
    WHAT to expect (based on the goal + system + context excerpt), then
    every probe derives from that plan — so the loop goes back and forth
    to the context following the plan's search items.  Returns None on any
    failure so the consolidator falls back to raw enhanced-intent probing.
    """
    try:
        plan_prompt = (
            "You are planning a context search for a pipeline. Based on the "
            "GOAL and CONTEXT below, write what the pipeline needs to know "
            "before answering.\n\n"
            "Output exactly three sections:\n"
            "INTENT SUMMARY: <2-3 sentences synthesizing what the pipeline "
            "needs to know, based ONLY on the CONTEXT>\n"
            "SEARCH PLAN: <2-4 items, one per line starting with '-', each "
            "naming a concrete detail/entity to find AND where in the "
            "context to look>\n"
            "EXPECTED ANSWER SHAPE: <1-2 sentences describing what the final "
            "response should cover>\n\n"
            "Use ONLY terms that appear in the CONTEXT. Never invent "
            "details. Never repeat the GOAL verbatim.\n\n"
            f"Material:\n{str(enhanced_intent or '')[:2200]}\n\n"
            "Plan: "
        )
        # NO timeout: probing may be collecting context from huge sources
        # (and may sit behind a per-node model switch in multi-model chains)
        # — timing it out only makes the consolidation dumber.
        return _llamacpp_raw_completion(
            plan_prompt, llm_config, api_url,
            max_tokens=192, temperature=0.3,
        )
    except Exception as exc:
        logger.warning("Consolidation: intent plan generation failed: %s", exc)
        return None


def _generate_llamacpp_probe(enhanced_intent, consumed_facts, llm_config,
                             api_url, user_prompt=""):
    """Generate ONE TARGETED retrieval probe with the node's llamacpp model.

    The probe derives from the ENHANCED INTENT (goal + system + similarity
    context) plus the FACTS accumulated by previous cycles: the model must
    target ONE aspect NOT yet covered, using only terms present in the
    intent, and must never rephrase the user request or invent product
    names.  When the consolidator embeds rejected previous probes + the
    rejection reasons (PREVIOUS PROBE ATTEMPTS AND REJECTIONS section),
    the model first REFLECTS on why each failed and what a better probe
    would target, then writes a corrected question — same model, single
    call, feedback-driven refinement.  The consolidator validates the
    result and falls back to deterministic section probes when it fails.
    Synchronous, small token budget; ANY failure returns ``None``.  The
    node's gpu_layers/threads/context_size are passed through so the
    shared engine's params are not treated as a change (which would
    restart the server mid-run).
    """
    global _PROBE_LATENCY_WARNED
    try:
        use_llamacpp = str(llm_config.get("use_llamacpp", False)).lower() in (
            "true", "1", "yes", "on",
        )
        if not use_llamacpp:
            return None
        model_path = (llm_config.get("llamacpp_model_path") or "").strip()
        if not os.path.isabs(model_path):
            try:
                from AI.config_loader import get_models_dir
                model_path = os.path.normpath(
                    os.path.join(get_models_dir(), model_path)
                )
            except Exception:
                return None
        if not os.path.exists(model_path):
            return None

        # Guidance survives the prefix slice: split the engine's appended
        # INTENT PLAN / BASE CONTEXT / PREVIOUS PROBES / PREVIOUS PROBE
        # ATTEMPTS AND REJECTIONS sections off the intent, cap the MATERIAL
        # separately, then re-attach the guidance AFTER it so nothing the
        # model must see is truncated away as the fact-driven loop
        # accumulates probes and facts.  The EARLIEST marker wins.
        _ctx = str(enhanced_intent or "")
        # The judge's GAP section is appended at the END of the intent by
        # the consolidator — pull it out whole BEFORE the marker split so a
        # capped BASE CONTEXT slice can never truncate it away.
        _gap_text = ""
        _gi = _ctx.find("GAP TO CLOSE")
        if _gi != -1:
            _gap_text = _ctx[_gi:].strip()
            _ctx = _ctx[:_gi].rstrip()
            _gap_text = _gap_text[:400]
        _guidance = ""
        _split_at = -1
        for _m in ("INTENT PLAN (base for probing",
                   "BASE CONTEXT (source of truth",
                   "PREVIOUS PROBES (already used",
                   "PREVIOUS PROBE ATTEMPTS AND REJECTIONS"):
            _i = _ctx.find(_m)
            if _i != -1 and (_split_at == -1 or _i < _split_at):
                _split_at = _i
        if _split_at != -1:
            _guidance = _ctx[_split_at:]
            _ctx = _ctx[:_split_at]
        _material = _ctx[:1800]
        # The BASE CONTEXT is the SOURCE OF TRUTH — split it out of the
        # guidance so it survives the caps whole (bounded), while the
        # plan/probe-history guidance stays separate.
        _base_ctx = ""
        _i = _guidance.find("BASE CONTEXT (source of truth")
        if _i != -1:
            _base_ctx = _guidance[_i:][:6500]
            _guidance = _guidance[:_i]
        _plan_present = "INTENT PLAN (base for probing" in _guidance

        if _plan_present:
            # The pipeline planned what to search — probes cover the plan's
            # search items (ComoRAG entity-priority: up to 3 per cycle).
            probe_prompt = (
                "The pipeline has planned what to search (INTENT PLAN "
                "below). Write up to 3 DIFFERENT questions, ONE PER LINE, "
                "each targeting a DIFFERENT search item from the SEARCH PLAN "
                "that is NOT yet covered.\n"
                "Rules: use ONLY terms from the BASE CONTEXT and the "
                "INTENT PLAN; never repeat the GOAL; never mention "
                "sections, headings, or document structure; end with '?'."
            )
        else:
            probe_prompt = (
                "Write up to 3 DIFFERENT specific questions, ONE PER LINE, "
                "about DIFFERENT concrete details in the BASE CONTEXT below "
                "(people, roles, capabilities, features, benefits, or facts) "
                "that help answer the GOAL and are NOT yet covered.\n"
                "Rules: use ONLY terms from the BASE CONTEXT; never repeat "
                "the GOAL; never mention sections, headings, or document "
                "structure; end with '?'."
            )
        _critique_mode = "PREVIOUS PROBE ATTEMPTS AND REJECTIONS" in _guidance
        if _critique_mode:
            # The GOAL line anchors the model to echo the request — drop it
            # from the MATERIAL for critique retries so the corrected probe
            # must derive from the CONTEXT instead (retrying with the same
            # guide makes the model respond the same thing again).
            _g_idx = _material.find("GOAL (user request):")
            if _g_idx != -1:
                _after = _material[_g_idx:]
                _nl = _after.find("\n\n")
                _material = (
                    _material[:_g_idx] + _after[_nl + 2:]
                    if _nl != -1 else _material[:_g_idx]
                )
            probe_prompt = (
                "Your previous probes were rejected. Write a NEW, DIFFERENT "
                "probe that targets a concrete detail from the CONTEXT.\n\n"
                "REFLECT first (2-3 sentences):\n"
                "- Why was each rejected probe wrong (which rule did it "
                "violate)?\n"
                "- Which specific detail, term, or entity from the CONTEXT "
                "is not yet covered?\n"
                "- How will the new probe differ from the rejected ones?\n\n"
                "Then write up to 3 corrected probes, ONE PER LINE, each "
                "starting with 'Corrected probe:' — every one a DIFFERENT "
                "question ending with '?'.\n\n"
                "Rules:\n"
                "- Use ONLY terms that appear in the BASE CONTEXT.\n"
                "- Do NOT repeat the user request (word for word): "
                + repr(str(user_prompt or "")[:120])
                + "\n"
                "- Do NOT reuse the wording of your previous attempts — "
                "change BOTH the words and the target detail.\n"
                "- Never mention sections, headings, or document structure.\n"
            )
        if _gap_text and not _critique_mode:
            # Gap-driven mode (ComoRAG meta-loop): the judge found the
            # material insufficient and named the ONE missing detail — the
            # probe must target it, not a generic uncovered aspect.
            probe_prompt = (
                "The pipeline's judge found the material insufficient and "
                "named the missing detail (GAP TO CLOSE below). Write up to 3 "
                "DIFFERENT questions, ONE PER LINE, that together retrieve "
                "exactly that missing detail from the BASE CONTEXT.\n"
                "Rules: use ONLY terms from the BASE CONTEXT and the GAP; "
                "never repeat the GOAL; never mention sections, headings, "
                "or document structure; end with '?'."
            )
        _guidance_parts = []
        if consumed_facts and str(consumed_facts).strip():
            _guidance_parts.append(
                "FACTS SO FAR (findings already established — target what "
                "is still unknown):\n" + str(consumed_facts)[:1200]
            )
        if _guidance:
            # Room for plan + ENHANCED INTENT (facts tree) + probe history
            # + rejection feedback without truncating the newest guidance.
            _guidance_parts.append(_guidance[:3000])
        if _guidance_parts:
            probe_prompt += "\n\n" + "\n\n".join(_guidance_parts) + "\n\n"
        if _gap_text:
            probe_prompt += _gap_text + "\n\n"
        if _base_ctx:
            probe_prompt += _base_ctx + "\n\n"
        probe_prompt += (
            f"MATERIAL (system):\n{_material}\n\n"
            if _critique_mode else
            f"MATERIAL (goal + system):\n{_material}\n\n"
        )
        # Anchor the probe on the node's semantic description (when present):
        # a tiny model otherwise drifts from the user's intent and fabricates
        # off-topic queries (observed: "what could arrow do for this person?"
        # -> probe "what can superman do for a person?"), which retrieves
        # irrelevant evidence and wastes a full server call.
        _semantic_desc = str(
            llm_config.get("semantic_description") or ""
        ).strip()
        if _semantic_desc:
            probe_prompt += (
                "Domain scope (authoritative, stay within it):\n"
                + _semantic_desc[:400]
                + "\n\n"
            )
        # Anchor for raw completion mode (no chat template): the model
        # continues after "Probe question:" instead of starting a reasoning
        # block.
        probe_prompt += "Probe question: "

        # Floor 128: a 64-token budget truncates questions before the '?'
        # (observed on the resume+LinkedIn run) — generations below that
        # cannot complete a grounded question.
        max_tokens = max(
            int(llm_config.get("consolidation_max_tokens", 192) or 192), 128,
        )
        # Reflection-refined probes need room for the analysis + correction.
        if _critique_mode:
            max_tokens = max(max_tokens, 160)

        _t0 = time.time()
        text = _llamacpp_raw_completion(
            probe_prompt, llm_config, api_url,
            max_tokens=max_tokens,
            # Critique retries raise the temperature so the model does not
            # deterministically reproduce the same rejected probe.  NO
            # timeout: probing may collect context from huge sources or sit
            # behind a per-node model switch — timing out makes it dumber.
            temperature=0.6 if _critique_mode else 0.3,
        )
        _probe_elapsed = time.time() - _t0
        if _probe_elapsed > 10.0 and not _PROBE_LATENCY_WARNED:
            _PROBE_LATENCY_WARNED = True
            logger.warning(
                "[LLM] Probe generation took %.1fs (>10s) — consider "
                "lowering consolidation_max_probes or the probe max tokens",
                _probe_elapsed,
            )
        if not text:
            return None
        # ComoRAG entity-priority probing: keep EVERY question line (up to 3),
        # not just one — a cycle then sweeps several distinct evidence paths.
        _probes: List[str] = []
        _after_marker = False
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            low = line.lower()
            if low.startswith("corrected probe"):
                # Reflection template: take the text after the marker.
                _after_marker = True
                _rest = line.split(":", 1)[1].strip() if ":" in line else ""
                if _rest and _rest[:200] not in _probes:
                    _probes.append(_rest[:200])
                continue
            if _after_marker:
                if line[:200] not in _probes:
                    _probes.append(line[:200])
            elif line.endswith("?"):
                if line[:200] not in _probes:
                    _probes.append(line[:200])
        if _critique_mode and not _probes:
            # Reflection wrapped without the marker — last line wins.
            _probes = [text.splitlines()[-1].strip()[:200]]
        elif not _probes:
            # No '?'-terminated line: fall back to the first non-empty line
            # (a truncated interrogative is still valid — the engine's
            # validator accepts interrogative starts).
            _probes = [
                ln.strip()[:200] for ln in text.splitlines() if ln.strip()
            ][:1]
        _probes = [p for p in _probes if p][:3]
        if not _probes:
            return None
        probe = "\n".join(_probes)
        # Reject hallucinated/off-topic probes: a small model may continue the
        # narrative instead of emitting a retrieval query (observed: request
        # "what could arrow do for this person?" -> probe "what can superman
        # do for a person?").  Compare against the RAW user goal (not the
        # enhanced intent, which carries context headers); an unrelated probe
        # falls back to the lexical probe (request anchor).
        try:
            import difflib
            _anchor_src = str(user_prompt or enhanced_intent or "").lower()
            _probe_l = probe.lower()
            _anchor = " ".join(_anchor_src.split()[:12])
            _ratio = difflib.SequenceMatcher(None, _anchor, _probe_l).ratio()
            _anchor_words = _anchor.split()
            _overlap = (
                sum(1 for w in _anchor_words if w in _probe_l)
                / max(1, len(_anchor_words))
            )
            if _ratio < 0.2 and _overlap < 0.35:
                logger.warning(
                    "Consolidation: probe '%s' off-topic vs user request — "
                    "using lexical fallback", probe,
                )
                return None
        except Exception:
            pass
        logger.info("Consolidation: probe response: %s", probe)
        return probe
    except Exception:
        return None


def _llamacpp_raw_completion(
    prompt, llm_config, api_url, max_tokens=64, temperature=0.3,
    attempts=4, retry_delay=2.0,
) -> Optional[str]:
    """Call the node's own llamacpp model in raw-completion mode.

    Raw ``/completion`` (no chat template) so the model answers immediately
    instead of emitting a think block that the chat path strips.  Retries
    transient gateway errors (the engine may be starting or mid-restart).
    NO timeout: a local generation finishes when it finishes — probing must
    never time out because it may be collecting context from huge sources or
    waiting behind a per-node model switch in multi-model chains.  Returns
    the response text or None on any failure.
    """
    try:
        use_llamacpp = str(llm_config.get("use_llamacpp", False)).lower() in (
            "true", "1", "yes", "on",
        )
        if not use_llamacpp:
            return None
        model_path = (llm_config.get("llamacpp_model_path") or "").strip()
        if not os.path.isabs(model_path):
            try:
                from AI.config_loader import get_models_dir
                model_path = os.path.normpath(
                    os.path.join(get_models_dir(), model_path)
                )
            except Exception:
                return None
        if not os.path.exists(model_path):
            return None

        gpu_layers = int(llm_config.get("llamacpp_gpu_layers", 0) or 0)
        threads = int(llm_config.get("llamacpp_threads", -1) or -1)
        context_size = int(llm_config.get("llamacpp_context_size", 0) or 0)

        if _direct_transport is not None and _direct_transport.is_direct_mode():
            result = _direct_transport.generate(
                model=model_path,
                prompt=prompt,
                system="",
                max_tokens=max_tokens,
                temperature=temperature,
                stream=False,
                images=None,
                gpu_layers=gpu_layers,
                threads=threads,
                context_size=context_size,
                raw_completion=True,
            )
            if isinstance(result, dict):
                return (
                    result.get("response") or result.get("text") or ""
                ).strip() or None
            return None

        _base = (api_url or "").rstrip("/")
        if not _base:
            return None
        import requests as _rq
        _last = ""
        for _attempt in range(attempts):
            try:
                _resp = _rq.post(
                    f"{_base}/llamacpp/generate",
                    json={
                        "model": model_path,
                        "prompt": prompt,
                        "system": "",
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                        "stream": False,
                        "use_llamacpp": True,
                        "gpu_layers": gpu_layers,
                        "threads": threads,
                        "context_size": context_size,
                        "raw_completion": True,
                        # Part of the node's consolidation burst — the model
                        # must stay loaded for the next probe; the burst is
                        # released by one /llamacpp/release at node end.
                        "cleanup_after": False,
                        # One round only: a consolidation hook asks for a short
                        # structured reply, and continuing a rambling small
                        # model can run for minutes per call.
                        "max_rounds": 1,
                    },
                )
            except Exception as _exc:
                _last = str(_exc)
                logger.warning(
                    "Consolidation: request attempt %d failed (%s) — retrying",
                    _attempt + 1, _last,
                )
                time.sleep(retry_delay)
                continue
            if _resp.status_code == 200:
                _data = _resp.json()
                if isinstance(_data, dict):
                    return (
                        _data.get("response") or _data.get("text") or ""
                    ).strip() or None
                return None
            if _resp.status_code == 400:
                # Prompt too long for the engine's context — retrying the
                # same payload is pointless.  The caller (final
                # consolidation) shrinks the evidence and retries.
                logger.warning(
                    "Consolidation: request rejected (400) — prompt too long "
                    "for the engine context; caller will shrink and retry"
                )
                return None
            _last = _resp.text[:200]
            logger.warning(
                "Consolidation: request attempt %d returned %d: %s — retrying",
                _attempt + 1, _resp.status_code, _last,
            )
            time.sleep(retry_delay)
        logger.warning(
            "Consolidation: request failed after %d attempts (last: %s)",
            attempts, _last,
        )
        return None
    except Exception as exc:
        logger.warning("Consolidation: request failed: %s", exc)
        return None


def _fire_llamacpp_release(api_base: str) -> None:
    """Tell the API gateway the node's llamacpp burst is over.

    Fire-and-forget (3s timeout): the gateway schedules a seq-guarded unload
    under its lifecycle lock.  A lost release only delays the unload until the
    next request or app exit — never a correctness problem.
    """
    try:
        if not api_base:
            return
        import requests as _rq_rel
        _rq_rel.post(
            f"{api_base.rstrip('/')}/llamacpp/release",
            json={},
            timeout=3,
        )
    except Exception:
        pass


def _clean_ingested_text(text: str) -> str:
    """Strip binary/control residue from an ingested document.

    Raw PDFs and streams ("%PDF-1.4", NULs, "/Author (...)") previously
    reached chunking as-is and polluted every probe's evidence with junk
    (observed: a 52k-char resume PDF became 66 chunks of mostly binary
    noise; the model then answered from PDF metadata).  Keeps printable
    text + structural whitespace only, collapses blank runs.
    """
    try:
        if not text:
            return ""
        out = []
        for ch in text:
            if ch in "\n\r\t" or ch.isprintable():
                out.append(ch)
            else:
                out.append(" ")
        s = "".join(out)
        s = re.sub(r"[ \t]{2,}", " ", s)
        s = re.sub(r"\n{3,}", "\n\n", s)
        return s.strip()
    except Exception:
        return str(text or "")


def _context_port_payload(variables: Dict[str, Any], f_id: str,
                          output_type: Optional[str] = None) -> str:
    """Payload of an upstream node connected to this LLM node's context
    INPUT port, resolved by the SOURCE output port.

    Context-type and LLM nodes store ``node_<id>_context``; a ``data`` edge
    (Input node) stores ``node_<id>_data``; a named port reads
    ``node_<id>_output_<port>``.  Base/branch source ports drive execution
    only — they carry no data, so they yield "".
    """
    try:
        ot = str(output_type or '').lower()
        if ot == 'data':
            raw = variables.get(f"node_{f_id}_data", "") or ""
            return str(raw) if raw and str(raw).strip() else ""
        if ot in ('context', 'ctx_out'):
            raw = variables.get(f"node_{f_id}_context", "") or ""
            return str(raw) if raw and str(raw).strip() else ""
        # Base/branch ports carry no data.
        if ot in ('output', '', 'true', 'false', 'error', 'route'):
            return ""
        named = variables.get(f"node_{f_id}_output_{output_type}")
        if named is None:
            named = variables.get(f"node_{f_id}_output_{ot}")
        return str(named) if named is not None and str(named).strip() else ""
    except Exception:
        return ""


def _generate_fact_answer(probe, evidence, llm_config, api_url):
    """Respond to ONE probe by COMPOSING grounded facts from its evidence.

    ComoRAG cycle step: the answer's COMPOSED findings enter the curated
    fact pool.  A finding is a statement that follows FROM the Evidence and
    may combine multiple sentences/chunks — it is NOT a verbatim quote —
    but every composed fact must carry a VERBATIM ``Support`` quote and may
    never introduce a term absent from the Evidence (a small model
    otherwise absorbs probe-hallucinated terms like "LooperOS" into the
    facts).  Open-horizon workflows need composed facts, not copies:
    extraction alone cannot synthesize across chunks.  Returns None on any
    failure so the caller stores the raw evidence instead.
    """
    try:
        answer_prompt = (
            "You extract ONE answer from retrieved evidence. Answer the "
            "Question using ONLY the Evidence.\n"
            "Output exactly two lines:\n"
            "Key Finding: <the value or statement from the Evidence that "
            "answers the Question>\n"
            "Support: <the exact sentence from the Evidence containing it, "
            "copied word for word>\n"
            "The Support sentence MUST appear verbatim in the Evidence. "
            "Answer ONLY what the Question asks: if the Evidence does not "
            "answer the Question, output exactly 'Key Finding: NONE'.\n"
            "Never introduce a term that does not appear in the Evidence "
            "(the product is Arrow, NOT LooperOS).\n\n"
            f"Question:\n{str(probe or '')[:200]}\n\n"
            f"Evidence:\n{str(evidence or '')[-2500:]}\n\n"
            "Key Finding: "
        )
        # NO timeout: the responder may collect context from huge sources
        # or queue behind a per-node model switch (see intent-plan comment).
        return _llamacpp_raw_completion(
            answer_prompt, llm_config, api_url,
            max_tokens=192, temperature=0.2,
        )
    except Exception as exc:
        logger.warning("Consolidation: fact extraction failed: %s", exc)
        return None


def _generate_finding_relevance(question, claims, llm_config, api_url):
    """Verdict per finding: does this claim ANSWER the question?

    The consolidator's lexical gate keeps any finding that shares a term with
    the search plan - which also keeps a finding that reuses a focus noun for
    a DIFFERENT detail ("The applicant's date of birth is 01/02/1990" for a
    probe asking for a city).  Such a finding satisfies the fact quota AND
    steers the next probe off target, so the lexical survivors are put to the
    node's own model in ONE batched call per cycle.

    Returns a list of bools aligned with *claims*, or None on any failure -
    the caller then keeps the lexical result unchanged (fail open).
    """
    try:
        _claims = [str(c) for c in (claims or [])]
        if not _claims:
            return None
        _numbered = "\n".join(
            "%d. %s" % (i, c.strip()[:400])
            for i, c in enumerate(_claims, 1)
        )
        verdict_prompt = (
            "You check whether each claim ANSWERS the question. A claim that "
            "merely mentions the same topic, or answers with a different "
            "detail, a different person or a different field, is NOT an "
            "answer.\n\n"
            "Question:\n%s\n\n"
            "Claims:\n%s\n\n"
            "For EVERY claim write one line: '<number>: YES' when it answers "
            "the question, '<number>: NO' when it does not. Nothing else.\n"
            % (str(question or "")[:200], _numbered)
        )
        _raw = _llamacpp_raw_completion(
            verdict_prompt, llm_config, api_url,
            max_tokens=96, temperature=0.0,
        )
        return _parse_relevance_lines(_raw, len(_claims))
    except Exception as exc:
        logger.warning("Consolidation: relevance verdict failed: %s", exc)
        return None


def _generate_sufficiency_judgment(goal, facts_text, evidence_text,
                                   llm_config, api_url):
    """ComoRAG meta-loop gate: decide whether the goal is answerable yet.

    Called after every probe that produced composed facts.  Outputs EXACTLY
    one line: ``ANSWER: <draft final answer>`` when the accumulated facts +
    verbatim evidence are enough to answer the goal (the consolidator stops
    probing and submits the draft to the final synthesis for verification),
    or ``MISSING: <the single most important fact/person/detail still
    missing>`` when they are not (the consolidator seeds the next probe
    with that gap).  Returns None on any failure so probing continues
    unchanged.
    """
    try:
        judge_prompt = (
            "You are the stop-judge of a context search pipeline. Decide "
            "whether the material below is ENOUGH to answer the GOAL. A "
            "stop is final, so continue ONLY while a missing detail could "
            "still be found in the sources.\n"
            "First write ONE short line naming what the material already "
            "answers and what is still missing. Then write exactly ONE "
            "decision line:\n"
            "ANSWER: <the value that answers the GOAL> — only when EVERY "
            "part of the GOAL can be answered from the material\n"
            "MISSING: <the single most important fact/detail still missing> "
            "— otherwise\n"
            "Never add anything else.\n\n"
            f"GOAL:\n{str(goal or '')[:400]}\n\n"
            f"FACTS FOUND SO FAR:\n{str(facts_text or '')[-1500:]}\n\n"
            f"EVIDENCE (verbatim source text):\n"
            f"{str(evidence_text or '')[-2000:]}\n\n"
            "Analysis: "
        )
        return _llamacpp_raw_completion(
            judge_prompt, llm_config, api_url,
            max_tokens=192, temperature=0.2,
        )
    except Exception as exc:
        logger.warning("Consolidation: sufficiency judge failed: %s", exc)
        return None


def _generate_consolidated_response(user_prompt, enhanced_intent, facts,
                                    evidence, llm_config, api_url):
    """Final synthesis pass (ComoRAG) — grounded on the verbatim SOURCES.

    Synthesizes ONE response for the initial query.  The Evidence
    (verbatim document chunks) is the only ground truth: if a fact
    conflicts with the Evidence or introduces a term absent from it, the
    fact is omitted — self-generated context must never override the
    actual sources.  Shrinks the evidence on 400 (engine context too
    small) so the synthesis always completes.  Returns None on any
    failure so the caller falls back to the accumulated facts.
    """
    try:
        max_tokens = int(
            llm_config.get("consolidation_response_max_tokens", 192) or 192
        )
        _req = str(user_prompt or "")[:300]
        _intent = str(enhanced_intent or "")
        _facts = str(facts or "")
        _evidence = str(evidence or "")
        # The relevance-filtered evidence pool decides how much the synthesis
        # sees: try the FULL accumulated facts + verbatim evidence first.  A
        # hard first-attempt cap would silently discard verified evidence on
        # engines that can fit it (SmolLM3's ~20k-token window holds ~10x the
        # per-cycle budget).  The ladder below only shrinks when the engine
        # rejects the prompt (400), so the synthesis still completes on
        # genuinely tight windows.
        for _cap in (None, 5000, 3000, 1500, 800, 400):
            _facts_part = _facts if _cap is None else _facts[-_cap:]
            _evidence_part = _evidence if _cap is None else _evidence[-_cap:]
            synthesize_prompt = (
                "You are synthesizing the final answer for a user request. "
                "The EVIDENCE below is VERBATIM text from the available "
                "documents — it is the only ground truth. Answer the goal "
                "using ONLY the Evidence: if any fact conflicts with it or "
                "introduces a product name/term not present in it, omit "
                "that fact. Never invent capabilities or details. Combine "
                "the relevant evidence into ONE coherent answer aligned "
                "with the goal and the person/context in the ENHANCED "
                "INTENT.\n\n"
                f"User request (goal):\n{_req}\n\n"
                f"Enhanced intent (goal + system + context):\n{_intent[:1800]}\n\n"
                f"Curated facts (must be validated against the evidence):\n{_facts_part}\n\n"
                f"Evidence (verbatim source text):\n{_evidence_part}\n\n"
                "Synthesis: "
            )
            text = _llamacpp_raw_completion(
                synthesize_prompt, llm_config, api_url,
                max_tokens=max_tokens, temperature=0.2,
            )
            if text:
                return text
        return None
    except Exception as exc:
        logger.warning("Consolidation: final synthesis failed: %s", exc)
        return None


class LLMExecutor:
    """Executes LLM nodes in workflow graphs (modularized)."""

    def __init__(self, variables: Optional[Dict[str, object]] = None):
        # Store initial variables to support resetting state between runs
        # Use deepcopy to ensure we have a true independent snapshot
        try:
            self._initial_variables = copy.deepcopy(variables) if variables else {}
        except Exception:
            # Fallback to shallow copy if deepcopy fails (e.g. non-pickleable objects)
            self._initial_variables = variables.copy() if variables else {}

        try:
            self.variables: Dict[str, object] = copy.deepcopy(self._initial_variables)
        except Exception:
            self.variables = self._initial_variables.copy()

        self.context_collector = None
        self.context_db = None
        self._initialize_context_collector()
        self._initialize_context_database()
        # Verbose output flows through the unified telemetry dispatch (see
        # logging_setup.log_block): colored panels in the console, fenced
        # blocks in the session md.  A private rich Console is NOT created
        # here — its stdout would be the wrapped bridge and loop back.
        self._rich_console = None

    def reset(self) -> None:
        """Resets the executor state to initial configuration."""
        try:
            self.variables = copy.deepcopy(self._initial_variables)
        except Exception:
            self.variables = self._initial_variables.copy()
        logger.info("LLMExecutor state reset")

    def _initialize_context_collector(self) -> None:
        try:
            project_root = os.path.dirname(
                os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                )
            )
            if project_root not in sys.path:
                sys.path.append(project_root)
            from AI.context_collector import ContextCollector  # noqa: F401

            self.context_collector = (
                ContextCollector() if "ContextCollector" in globals() else None
            )
        except Exception:
            self.context_collector = None

    def _initialize_context_database(self) -> None:
        try:
            project_root = os.path.dirname(
                os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                )
            )
            if project_root not in sys.path:
                sys.path.append(project_root)
            from AI.context_database import ContextDatabase  # noqa: F401

            self.context_db = (
                ContextDatabase() if "ContextDatabase" in globals() else None
            )
        except Exception:
            self.context_db = None


    def execute_llm_node(
        self, node: Dict, stop_flag, action_handlers=None
    ) -> Optional[str]:
        """Execute an LLM node and return the next node ID to run."""
        llm_data = node.get("data", {})
        _node_id = (
            node.get("id")
            or node.get("node_id")
            or llm_data.get("node_id")
            or llm_data.get("id")
            or "Unknown"
        )
        # Defensive clear BEFORE the run: the end-of-run clear below is skipped
        # by the early returns (stop flag, EmbeddingFailureError re-raise), so a
        # failed run used to leave its fact pool behind for the REST OF THE
        # SESSION.  Clearing up front makes leakage impossible regardless of
        # how the previous run ended.
        try:
            self.variables.pop(f"node_{_node_id}_fact_pool", None)
            self.variables.pop(f"node_{_node_id}_consolidated_facts", None)
        except Exception:
            pass
        logger.info("=== STARTING LLM NODE EXECUTION ===")
        logger.info(
            f"Executing LLM node: {llm_data.get('name', llm_data.get('id', llm_data.get('node_id', 'Unknown')))}"
        )
        logger.info(f"Full node data: {node}")

        if stop_flag and stop_flag():
            logger.info("LLM node execution stopped by user request")
            return None

        try:
            project_root = os.path.dirname(
                os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                )
            )
            if project_root not in sys.path:
                sys.path.append(project_root)
            from AI.consult import OllamaClient
        except Exception as e:
            logger.error(f"Failed to import OllamaClient: {e}")
            return None

        llm_config_raw = llm_data.get("llm_configuration", {}) or {}
        if llm_config_raw:
            # Merge: top-level fields fill gaps in llm_configuration
            llm_config = dict(llm_data)
            llm_config.update(llm_config_raw)
        else:
            llm_config = llm_data
        logger.info(f"LLM configuration: {llm_config}")

        # ── Direct clipboard capture for context timeline ──
        # Capture raw clipboard content BEFORE any input processing or context supplement
        _raw_clipboard_for_context = ""
        try:
            _configured_source = (
                llm_config.get("input_source", "none") if llm_config else "none"
            )
            if str(_configured_source).strip().lower() == "clipboard":
                import pyperclip

                _raw_clipboard_for_context = pyperclip.paste() or ""
                logger.info(
                    f"Direct clipboard capture for context: {len(_raw_clipboard_for_context)} chars"
                )
            elif str(_configured_source).strip().lower() in ("ocr", "screen"):
                _raw_clipboard_for_context = (
                    "[OCR/screen input - captured at processing time]"
                )
            elif str(_configured_source).strip().lower() == "previous":
                # For 'previous' source, capture from upstream node output
                for inp in node.get("inputs", []):
                    upstream_id = inp.get("from_node")
                    if upstream_id:
                        upstream_val = self.variables.get(
                            f"node_{upstream_id}_output", ""
                        )
                        if upstream_val:
                            _raw_clipboard_for_context = str(upstream_val)[:500]
                            break
        except Exception as e:
            logger.warning(f"Direct clipboard capture failed: {e}")

        # Store it immediately under an unambiguous variable name
        if _node_id:
            self.variables[f"node_{_node_id}_raw_user_input"] = (
                _raw_clipboard_for_context
            )
            logger.info(
                f"Stored raw_user_input for {_node_id}: {len(_raw_clipboard_for_context)} chars, preview: {_raw_clipboard_for_context[:100]}"
            )

        model = (llm_config.get("model") or "").strip()
        # Single source of truth: when use_llamacpp is True the effective
        # model is the bundled GGUF — stray Ollama model names (e.g.
        # llama3.2:latest in chain JSON) are ignored for llama.cpp nodes.
        # The default node property is empty: llama.cpp nodes run their GGUF
        # and never carry an Ollama model name.
        try:
            _use_llamacpp_flag = llm_config.get("use_llamacpp", False)
            if isinstance(_use_llamacpp_flag, str):
                _use_llamacpp_flag = _use_llamacpp_flag.lower() in ("true", "1", "yes", "on")
            if _use_llamacpp_flag:
                _gguf_name = (llm_config.get("llamacpp_model_path") or "").strip()
                if _gguf_name:
                    model = os.path.basename(_gguf_name)
            elif not model:
                # Ollama engine with no explicit model: fall back here ONLY —
                # llama.cpp nodes never reach this branch.
                model = "llama3.2:latest"
        except Exception:
            pass
        logger.info(f"Resolved model for execution: '{model}'")

        def _normalize_api_url(value):
            raw = str(value or "").strip()
            if not raw:
                return None
            if raw.startswith("http://") or raw.startswith("https://"):
                try:
                    from urllib.parse import urlparse

                    parsed = urlparse(raw)
                    if not parsed.hostname:
                        return None
                except Exception:
                    return None
                return raw.rstrip("/")
            return f"http://{raw}".rstrip("/")

        api_url = _normalize_api_url(llm_config.get("api_url"))
        if not api_url:
            try:
                api_url = get_default_api_url()
            except RuntimeError as _de:
                # Direct engine mode (ARROW_DIRECT_ENGINE=1): no HTTP API —
                # the llamacpp branch routes through AI.inprocess_transport.
                logger.info(
                    "Direct engine mode: no API URL (%s)", _de
                )
                api_url = ""
        system_message = llm_config.get("system_message", "")
        prompt = llm_config.get("prompt", "")
        # ── 'prompt' input port ──
        # Text wired to the node's 'prompt' port REPLACES the static prompt
        # template (the system message is untouched).  This keeps a real
        # prompt a prompt instead of folding it into context.  The 'context'
        # port is context/history only (see the context supplement below).
        try:
            _prompt_override = _prompt_port_input(node, self.variables)
            if _prompt_override:
                prompt = _prompt_override
                logger.info(
                    "[PROMPT_PORT] Prompt replaced (%d chars) via 'prompt' port",
                    len(prompt),
                )
        except Exception as _pe:
            logger.warning("Prompt-port resolution failed: %s", _pe)
        original_prompt = prompt  # Save for probe-based consolidation (before RAG injection)
        temperature = llm_config.get("temperature", 0.7)
        # No arbitrary output-token cap: the model generates until it finishes
        # (EOS) or the llama.cpp context window fills.  max_tokens=-1 is the
        # llama.cpp server's "unlimited" setting — the only real limits are the
        # model's trained context and available RAM/VRAM (llamacpp_context_size).
        max_tokens = -1
        output_variable = llm_config.get("output_variable", "llm_output")
        write_text = llm_config.get("write_text", True)
        use_vision = llm_config.get("use_vision", False)
        screenshot_enabled = llm_config.get("screenshot_enabled", True)
        # Web mode: input/vision/write run on the chain's shared browser instead
        # of the desktop (see LLMNode.web_mode / the dialog's Web Mode toggle).
        web_mode = str(llm_config.get("web_mode", False)).lower() in (
            "true", "1", "yes", "on",
        )

        def _is_vision_model_name(name: str) -> bool:
            ml = str(name or "").lower()
            for v in (
                "llava",
                "bakllava",
                "moondream",
                "llava-llama3",
                "llava-phi3",
                "minicpm-v",
            ):
                if v in ml:
                    return True
            # Also check for GGUF vision models by common keywords in the path/filename
            for v in ("vl-", "-vl", "vision", "multimodal", "vl."):
                if v in ml:
                    return True
            return False

        # Also check if the llama.cpp model path indicates a vision model
        _llamacpp_path = llm_config.get("llamacpp_model_path", "")
        _use_llamacpp = str(llm_config.get("use_llamacpp", False)).lower() in (
            "true",
            "1",
            "yes",
            "on",
        )
        is_vision_model = _is_vision_model_name(model) or (
            _use_llamacpp and _is_vision_model_name(_llamacpp_path)
        )
        should_use_vision = use_vision or is_vision_model
        images, _ocr_text = prepare_vision_images(
            should_use_vision, screenshot_enabled, stop_flag, web_mode=web_mode
        )

        def _cleanup_vision_images():
            """Delete the screenshots captured for this node once it is done."""
            if images:
                for _p in images:
                    try:
                        cleanup_screenshot(_p)
                    except Exception:
                        pass

        def _write_chunk(chunk_text, action=None):
            """Write generated text: focused browser editable in web mode, else desktop.

            Web mode has no desktop target - the text goes into whatever
            input/textarea/contenteditable the chain's browser has focused
            (see player/web/actions.type_into_focused); desktop mode keeps the
            existing keystroke injection via the action handlers.
            """
            if not chunk_text:
                return
            if web_mode:
                try:
                    from ...web import actions as _web_actions
                    from .inputs import _web_driver

                    _drv = _web_driver()
                    if _drv is not None:
                        _web_actions.type_into_focused(_drv, chunk_text)
                except Exception as _we:
                    logger.warning(f"Web write failed: {_we}")
                return
            if action_handlers and hasattr(action_handlers, "handle_type_string_action"):
                action_handlers.handle_type_string_action(
                    0, action or {"text": chunk_text, "force_typing": True}, stop_flag
                )

        # Auto-detect concatenation: if this node has any inputs in the workflow graph,
        # treat them as "previous" source and enable direct RAG unless explicitly disabled.
        node_inputs = node.get("inputs", []) or []
        main_inputs = []
        try:
            for _inp in node_inputs:
                if isinstance(_inp, dict) and str(_inp.get("input_port")) in (
                    "tools",
                    "context",
                    "prompt",
                ):
                    continue
                main_inputs.append(_inp)
        except Exception:
            main_inputs = node_inputs
        has_input_connection = bool(main_inputs)
        connected_ids = []
        try:
            connected_ids = [
                inp.get("from_node")
                for inp in main_inputs
                if isinstance(inp, dict) and inp.get("from_node")
            ]
        except Exception:
            connected_ids = []
        has_previous_llm_output = any(
            f"node_{cid}_output" in self.variables
            or f"node_{cid}_data" in self.variables
            for cid in connected_ids
        )

        tool_inputs = []
        try:
            tool_inputs = [
                inp
                for inp in node_inputs
                if isinstance(inp, dict) and str(inp.get("input_port", "")) == "tools"
            ]
        except Exception:
            tool_inputs = []
        tool_ids = []
        try:
            tool_ids = [
                inp.get("from_node") for inp in tool_inputs if inp.get("from_node")
            ]
        except Exception:
            tool_ids = []
        tool_desc_map = {}
        try:
            import json as _json

            raw_td = llm_config.get("tool_descriptions") or {}
            if isinstance(raw_td, str):
                raw_td = _json.loads(raw_td)
            if isinstance(raw_td, dict):
                tool_desc_map = raw_td
        except Exception:
            tool_desc_map = {}
        # Tool processing moved to after prompt substitution for better context awareness

        # Extract code tool IDs (tools that accept JSON arguments)
        code_tool_ids = []
        try:
            raw_ct = llm_config.get("_code_tool_ids") or llm_data.get("_code_tool_ids") or []
            if isinstance(raw_ct, list):
                code_tool_ids = [str(x) for x in raw_ct if x]
        except Exception:
            code_tool_ids = []

        # Extract MCP tool IDs (tools that REQUIRE JSON arguments)
        mcp_tool_ids = []
        try:
            raw_mt = llm_config.get("_mcp_tool_ids") or llm_data.get("_mcp_tool_ids") or []
            if isinstance(raw_mt, list):
                mcp_tool_ids = [str(x) for x in raw_mt if x]
        except Exception:
            mcp_tool_ids = []

        # Expand tool_ids with composite MCP tool IDs from tool_desc_map
        # Format: {node_id}__{tool_name} — these were injected by
        # _inject_mcp_descriptions() in llm_ops.py so each MCP sub-tool
        # is individually selectable by the LLM.
        try:
            _composite_mcp_ids = []
            for _key in list(tool_desc_map.keys()):
                if '__' in str(_key):
                    _parts = str(_key).split('__', 1)
                    if _parts[0] in tool_ids:
                        _composite_mcp_ids.append(_key)
            if _composite_mcp_ids:
                tool_ids.extend(_composite_mcp_ids)
                code_tool_ids.extend(_composite_mcp_ids)
                # Composite sub-tool IDs inherit the MCP usage rules of their
                # node, so the prompt treats them as argument-required tools.
                mcp_tool_ids.extend(
                    c for c in _composite_mcp_ids
                    if str(c).split('__', 1)[0] in mcp_tool_ids
                )
                logger.debug("Added %d composite MCP tool IDs to tool_ids", len(_composite_mcp_ids))
        except Exception:
            pass

        # Skills
        skills_raw = llm_config.get("skills", [])
        if isinstance(skills_raw, str):
            try:
                import json as _json_skills

                skills_raw = _json_skills.loads(skills_raw)
            except Exception:
                skills_raw = []
        enabled_skills = [s for s in skills_raw if s.get("enabled", True)]
        use_skill_routing = llm_config.get("use_skill_routing", True)
        if isinstance(use_skill_routing, str):
            use_skill_routing = use_skill_routing.lower() in ("true", "1", "yes")

        # Detect context connections and whether upstream context is available.
        # Values resolve by the SOURCE output port: ctx_out edges read
        # node_<id>_context; 'data' edges (Input nodes) read node_<id>_data —
        # never the base 'output' of an Input node.
        context_inputs = []
        try:
            context_inputs = [
                inp
                for inp in node_inputs
                if isinstance(inp, dict) and str(inp.get("input_port", "")) == "context"
            ]
        except Exception:
            context_inputs = []
        context_ids = []
        _context_source_type = {}
        try:
            for _inp in context_inputs:
                _cf = _inp.get("from_node")
                if not _cf:
                    continue
                context_ids.append(_cf)
                _context_source_type[_cf] = _record_output_type(_inp)
        except Exception:
            context_ids = []
        has_context_connection = bool(context_ids)
        has_upstream_context_value = False
        try:
            for _cid in context_ids:
                _val = upstream_value(
                    self.variables, _cid,
                    _context_source_type.get(_cid, 'context'),
                )
                if _val is not None and str(_val).strip():
                    has_upstream_context_value = True
                    break
        except Exception:
            has_upstream_context_value = False

        # CRITICAL FIX: Capture the raw input source before any auto-detection.
        # The user's explicit choice (clipboard, OCR, etc.) must be preserved.
        # The 'or none' fallback was causing valid values like 'clipboard' to be
        # lost when the config value was empty string or None, triggering unwanted
        # auto-detection that overwrote the user's choice with 'context'.
        raw_input_source = llm_config.get("input_source")
        if raw_input_source and str(raw_input_source).strip():
            configured_input_source = str(raw_input_source).strip().lower()
        else:
            configured_input_source = "none"

        # The 'context' port is context/history only now — a legacy
        # input_source="context" must NOT pull it into the prompt as input text.
        if configured_input_source == "context":
            configured_input_source = "none"

        input_source = configured_input_source

        # ponytail: no auto-detection.  Context flows only through explicit
        # context-port connections (handled in context supplement below).
        # Regular input-port connections are for execution ordering, not
        # data injection — the user controls what each node sees.

        # DEBUG: Log the final input_source to help diagnose issues
        logger.info(
            f"Final input_source resolved to: '{input_source}' (configured: '{configured_input_source}', raw: '{raw_input_source}')"
        )

        raw_conf = llm_config.get("ocr_confidence")
        if raw_conf is None:
            ocr_confidence = 0.6
        else:
            try:
                ocr_confidence = float(raw_conf)
                if ocr_confidence > 1.0:
                    ocr_confidence = ocr_confidence / 100.0
            except Exception:
                ocr_confidence = 0.6

        logger.info(f"Input source: {input_source}, OCR confidence: {ocr_confidence}")

        use_direct_rag = str(llm_config.get("use_direct_rag", "false")).lower() in (
            "true",
            "1",
            "yes",
            "on",
        )
        # Respect explicit user choice for RAG; do not auto-enable on concatenation.
        # Allow explicit selection of previous/context input mode overriding use_direct_rag
        prev_mode = (
            (
                llm_config.get("previous_input_mode")
                or llm_config.get("context_input_mode")
                or ""
            )
            .strip()
            .lower()
        )
        if input_source.lower() in (
            "previous",
            "previous_node",
            "context",
        ) and prev_mode in ("raw", "embed"):
            use_direct_rag = prev_mode == "embed"
        rag_embedding_model = _resolve_rag_embedding_model(
            llm_config, _use_llamacpp_flag
        )
        logger.info(
            "RAG: resolved embedding model '%s' (engine=%s)",
            rag_embedding_model,
            "llamacpp" if _use_llamacpp_flag else "ollama",
        )
        rag_chunk_size = int(llm_config.get("rag_chunk_size", 500))
        rag_overlap = int(llm_config.get("rag_overlap", 100))
        rag_top_k = int(llm_config.get("rag_top_k", 3))
        rag_include_raw_input = str(
            llm_config.get("rag_include_raw_input", "false")
        ).lower() in ("true", "1", "yes", "on")
        rag_max_chars = int(llm_config.get("rag_max_chars", 1500))

        log_table(
            logger,
            logging.INFO,
            "LLM Node Parameters",
            [
                ("Config model", f"{model} (engine: {'llamacpp' if _use_llamacpp else 'ollama'})"),
                ("API URL", api_url),
                ("Temperature", temperature),
                ("Max Tokens", max_tokens),
                ("Output Variable", output_variable),
                ("Write Text", write_text),
                ("Use Vision", use_vision),
                ("Screenshot Enabled", screenshot_enabled),
            ],
        )
        log_block(logger, logging.INFO, "Prompt Template", prompt)
        log_block(logger, logging.INFO, "System Message", system_message or "(none)")
        if tool_ids:
            log_table(
                logger, logging.INFO, "Available Tools",
                [(tid, tool_desc_map.get(tid, "")) for tid in tool_ids],
            )

        if stop_flag and stop_flag():
            logger.info(
                "LLM node execution stopped by user request during configuration"
            )
            _cleanup_vision_images()
            return None

        prompt = substitute_variables(prompt, self.variables)
        log_block(
            logger, logging.INFO,
            "Prompt (after variable substitution)", prompt,
        )

        # ── Inject upstream input port data into prompt ──
        # When an upstream node is connected via the `input` port, its output
        # flows through the graph connection.  Values resolve by the SOURCE
        # output port: only explicit 'data' edges carry an Input node's value
        # (node_<id>_data); exec-only edges (base 'output', branch ports)
        # yield nothing for Input sources.  Stale outputs from prior loop
        # iterations are already cleared at the loop boundary by the workflow
        # executor, so every value here is fresh.
        _connected_ids_produced_output = False
        if node.get("inputs"):
            _upstream_inputs = []
            for _inp in (node.get("inputs") or []):
                if not isinstance(_inp, dict):
                    continue
                if str(_inp.get('input_port', 'input')) in (
                    'tools', 'context', 'prompt', 'input'
                ):
                    continue
                _cid = _inp.get('from_node')
                if not _cid:
                    continue
                _val = upstream_value(
                    self.variables, _cid, _record_output_type(_inp)
                )
                if _val is not None and str(_val).strip():
                    _upstream_inputs.append(str(_val).strip())
            if _upstream_inputs:
                _connected_ids_produced_output = True
                _injected = "\n".join(_upstream_inputs)
                prompt = f"User input: {_injected}\n\n{prompt}"
                logger.info(
                    "[INPUT_PORT] Injected %d upstream node(s) output "
                    "(%d chars) into LLM prompt via input port connection",
                    len(_upstream_inputs), len(_injected),
                )

        # ── Resolve the node's configured input source ──
        # Text reaches this node only through an explicit connection (base
        # input port or the Input node's data port); input_source="none" means
        # no text injection and no graph walk.
        # Desktop OCR region (JSON [x,y,w,h]) - parsed here so the input helper
        # stays a simple value consumer.
        try:
            import json as _json_region
            _ocr_region = _json_region.loads(llm_config.get("ocr_region") or "null")
        except Exception:
            _ocr_region = None
        if not (isinstance(_ocr_region, (list, tuple)) and len(_ocr_region) == 4):
            _ocr_region = None
        input_text, use_input_text_in_prompt = get_input_text(
            node,
            self.variables,
            input_source,
            ocr_confidence,
            stop_flag,
            use_direct_rag,
            workflow_graph=getattr(self, 'workflow_graph', None),
            web_mode=web_mode,
            web_ocr_locator=llm_config.get("web_ocr_locator") or "",
            region=_ocr_region,
        )

        # --- Embedding-first tool ranking (filtered candidate list) ---
        # Tools are pre-filtered by a cosine threshold against the user intent,
        # then ranked; the Router LLM always makes the final choice from a
        # short context of 2-3 candidates so a small model stays focused:
        #   very high -> up to 2 candidates
        #   high/mid/low -> up to 3 candidates (low tier explicitly allows a
        #                   natural reply when nothing fits)
        original_tool_count = len(tool_ids)
        _allow_natural_reply = False
        if tool_ids:
            try:
                # Single source of truth per tool: alias entries (<brave>, ...)
                # are for response parsing only and never enter ranking/prompt.
                tool_desc_map = {
                    k: v for k, v in tool_desc_map.items()
                    if not str(k).startswith('<')
                }

                # Read configurable top_k_tools (default 10)
                top_k_tools = int(llm_config.get('top_k_tools', 10))
                if top_k_tools < 1:
                    top_k_tools = 10

                # Build/reuse the ToolRetriever index (content-addressed, skips
                # re-indexing if tool_desc_map hasn't changed).  The corpus
                # embeds each tool's DESCRIPTION — ranking is decided by what
                # the tool does, never by its alias/hex label (display names
                # are only a fallback token for tools lacking a description).
                _tool_alias_map = (
                    llm_config.get('_tool_alias_map')
                    or llm_data.get('_tool_alias_map')
                    or {}
                )
                if isinstance(_tool_alias_map, str):
                    try:
                        _tool_alias_map = json.loads(_tool_alias_map)
                    except Exception:
                        _tool_alias_map = {}
                if not isinstance(_tool_alias_map, dict):
                    _tool_alias_map = {}
                _display_names = {}
                for _alias, _tid in _tool_alias_map.items():
                    if _tid:
                        _display_names[str(_tid)] = str(_alias).strip('<>')
                _TOOL_RETRIEVER.build_index(tool_desc_map, display_names=_display_names)

                # Build query from user intent ONLY — not the static prompt
                # template, which dilutes the semantic signal.  Values resolve
                # by the source output port (data edges -> node_<id>_data).
                query_parts = []
                try:
                    for _inp in (node.get('inputs') or []):
                        if not isinstance(_inp, dict):
                            continue
                        if str(_inp.get('input_port', '')) == 'tools':
                            continue
                        _cid = _inp.get('from_node')
                        if not _cid:
                            continue
                        _val = upstream_value(
                            self.variables, _cid, _record_output_type(_inp)
                        )
                        if _val is not None and str(_val).strip():
                            query_parts.append(str(_val).strip())
                except Exception:
                    pass
                if not query_parts and prompt and prompt.strip():
                    query_parts.append(prompt.strip())

                if query_parts:
                    query_text = '\n\n'.join(query_parts)
                    # Real filtering threshold: tools scoring below it never
                    # reach the Router LLM context (default 0.30, configurable
                    # per node via tool_score_threshold).
                    _score_th = float(llm_config.get('tool_score_threshold', 0.30))
                    ranked = _TOOL_RETRIEVER.retrieve_scored(
                        query_text, top_k=top_k_tools, score_threshold=_score_th,
                    )
                    if ranked:
                        # Confidence-aware cap: the final LLM context carries at
                        # most 2-3 candidates; the LLM always makes the choice.
                        from .tool_retriever import select_candidates_by_confidence
                        tool_ids, _allow_natural_reply = select_candidates_by_confidence(
                            ranked,
                            top_k=top_k_tools,
                            very_high=float(llm_config.get('tool_conf_very_high', 0.75)),
                            high=float(llm_config.get('tool_conf_high', 0.55)),
                            mid=float(llm_config.get('tool_conf_mid', 0.35)),
                        )
                        logger.info(
                            "ToolRetriever: confidence ladder -> %d/%d "
                            "candidate(s) for Router LLM (threshold=%.2f, best=%.4f)",
                            len(tool_ids), original_tool_count, _score_th,
                            float(ranked[0][1]),
                        )
                    else:
                        # Nothing passed the threshold.  Distinguish a dead
                        # embedding server (passthrough entries score 0.0) from
                        # genuinely low relevance.
                        try:
                            _probe = _TOOL_RETRIEVER.retrieve_scored(
                                query_text, top_k=top_k_tools, score_threshold=-1.0,
                            )
                        except Exception:
                            _probe = []
                        if _probe and float(_probe[0][1]) == 0.0:
                            # Embedding model unavailable: bounded declaration-
                            # order subset (never zero, never the full catalog).
                            tool_ids = [t for t, _sc in _probe][: min(top_k_tools, 3)]
                            logger.info(
                                "ToolRetriever: embedding ranking unavailable — "
                                "passing %d tool(s) by declaration order for Router LLM",
                                len(tool_ids),
                            )
                        else:
                            tool_ids = []
                            logger.info(
                                "ToolRetriever: no tools above threshold %.2f — "
                                "Router LLM replies naturally (no tool list)",
                                _score_th,
                            )
                        _allow_natural_reply = True
            except Exception as e:
                logger.warning(f"Intelligent tool selection failed: {e}")

            # Zero candidates after filtering: keep the Router honest.  Never
            # leave an "always pick a tool" system prompt active over an empty
            # list — that forces a hallucinated tool id.
            if (not tool_ids) and original_tool_count > 0 and _allow_natural_reply:
                system_message = (
                    "Answer the user's request directly and concisely. "
                    "If no tool is needed or none fits, reply naturally — "
                    "never invent a tool id."
                )

            if tool_ids:
                # Re-generate typed tool instructions for the ranked candidates.
                # Tools are typed so the LLM knows HOW to call each one:
                #   - MCP tools: JSON arguments REQUIRED (omitting required
                #     params makes the server reject the call)
                #   - code utilities: JSON arguments optional
                #   - chain tools: no arguments at all
                try:
                    lines = ["Available Tools:"]
                    mcp_marked = False
                    code_marked = False
                    chain_marked = False
                    for tid in tool_ids:
                        desc = tool_desc_map.get(tid, "")
                        # Prefer the alias token (<brave>) as the selectable id:
                        # small models echo short names reliably, hex ids poorly.
                        _label = tid
                        try:
                            _am = (
                                llm_config.get('_tool_alias_map')
                                or llm_data.get('_tool_alias_map') or {}
                            )
                            if isinstance(_am, str):
                                _am = json.loads(_am)
                            if isinstance(_am, dict):
                                for _a, _tid in _am.items():
                                    if str(_tid) == str(tid):
                                        _label = _a
                                        break
                        except Exception:
                            pass
                        if tid in mcp_tool_ids:
                            mcp_marked = True
                            mark = " [MCP tool - JSON arguments REQUIRED]"
                        elif tid in code_tool_ids:
                            code_marked = True
                            mark = " [code utility - accepts optional JSON arguments]"
                        else:
                            chain_marked = True
                            mark = " [chain tool - no arguments]"
                        if desc:
                            lines.append(f"- {_label}: {desc}{mark}")
                        else:
                            lines.append(f"- {_label} (no description available){mark}")
                    lines.append("Tool Usage Guide:")
                    lines.append(
                        "- Decide by DESCRIPTION: pick the tool whose description "
                        "best matches the user's request; the <alias> is only a "
                        "label to reply with."
                    )
                    lines.append("- Reply with exactly one tool id.")
                    if mcp_marked:
                        lines.append(
                            "- For MCP tools: respond USE_TOOL:<id>{\"arg1\": \"value1\", ...} "
                            "passing a value for EVERY parameter marked (required) in the "
                            "tool's [params] list — omitting a required parameter fails the call."
                        )
                    if code_marked:
                        lines.append(
                            "- For code utilities: respond USE_TOOL:<id>{\"arg1\": \"value1\", ...}; "
                            "arguments are optional."
                        )
                    if chain_marked:
                        lines.append("- For chain tools: respond USE_TOOL:<id> with no other text.")
                    if _allow_natural_reply:
                        lines.append(
                            "- If NO listed tool actually fits the user's request, "
                            "reply naturally and concisely WITHOUT any USE_TOOL line."
                        )
                    if not mcp_marked and not code_marked:
                        lines.append("- Output must be exactly one line: USE_TOOL:<id>.")
                    lines.append(
                        "- Do not include quotes, markdown, code blocks, punctuation, or extra lines beyond the tool call."
                    )
                    lines.append("- Do not explain, reason, or justify the choice.")
                    extra = "\n".join(lines)
                    system_message = (system_message + "\n\n" + extra).strip()
                except Exception:
                    pass
        # ----------------------------------

        # ----------------------------------

        # ── Skill routing and injection ──
        if enabled_skills:
            if use_skill_routing and len(enabled_skills) > 1:
                # Gather context from connected Context nodes
                context_text = ""
                for cid in context_ids:
                    ctx_val = self.variables.get(f"node_{cid}_context", "")
                    if ctx_val:
                        context_text += str(ctx_val) + "\n"

                if context_text.strip():
                    try:
                        from AI import embedding_server
                        import numpy as np

                        skill_texts = [
                            f"{s.get('name', '')}: {s.get('description', '')} {s.get('trigger_keywords', '')}"
                            for s in enabled_skills
                        ]
                        query_emb = embedding_server.embed_texts([context_text])
                        skill_embs = embedding_server.embed_texts(skill_texts)
                        if (
                            query_emb
                            and skill_embs
                            and len(skill_embs) == len(skill_texts)
                        ):
                            # Server vectors are normalized — cosine is a dot
                            # product.
                            qv = np.asarray(query_emb[0], dtype=np.float32)
                            sv = np.asarray(skill_embs, dtype=np.float32)
                            scores = sv @ qv

                            selected = []
                            for idx, skill in enumerate(enabled_skills):
                                score = float(scores[idx])
                                if score > 0.15:
                                    selected.append((score, skill))

                            if selected:
                                selected.sort(
                                    key=lambda x: (-x[0], x[1].get("priority", 5))
                                )
                                enabled_skills = [s for _, s in selected]
                                logger.info(
                                    f"Skill routing: Selected {len(enabled_skills)} skills based on context relevance"
                                )
                            # If nothing matched, keep all enabled skills
                    except Exception as e:
                        logger.debug(f"Skill routing error: {e}")

            # Inject selected skills into system_message
            skill_block = "\n\n## Active Skills\n"
            for skill in enabled_skills:
                skill_block += f"\n### {skill.get('name', 'Unnamed')}\n{skill.get('instructions', '')}\n"
            system_message = system_message + skill_block
            logger.info(f"Injected {len(enabled_skills)} skills into system message")
        # ----------------------------------

        # Determine if we are using the Form Filling Preset
        def _is_form_filling_prompt(p: str) -> bool:
            pl = (p or "").strip().lower()
            if not pl:
                return False
            if "analyze the form fields provided" not in pl:
                return False
            if "next empty field" not in pl:
                return False
            return True

        is_form_filling_preset = _is_form_filling_prompt(prompt)

        if stop_flag and stop_flag():
            logger.info(
                "LLM node execution stopped by user request before input processing"
            )
            return None

        # Store the actual source input text and type so downstream nodes
        # (e.g. Context node) can capture what was really fed to the LLM,
        # not just the prompt template.
        # CRITICAL: This must happen BEFORE any prompt assembly or context supplement.
        try:
            if _node_id:
                # Store the RAW input text (clipboard, OCR, etc.) before any processing
                raw_input_to_store = input_text if input_text is not None else ""
                self.variables[f"node_{_node_id}_source_input"] = raw_input_to_store
                self.variables[f"node_{_node_id}_input_source"] = input_source
                # Backfill raw_user_input with the actual OCR/clipboard text
                # (the early capture at line 281 uses a placeholder for OCR;
                #  overwrite it now that get_input_text() has returned real data)
                if raw_input_to_store:
                    self.variables[f"node_{_node_id}_raw_user_input"] = raw_input_to_store
                # DEBUG: Log what we're storing to help diagnose issues
                preview = (
                    str(raw_input_to_store)[:200] if raw_input_to_store else "(empty)"
                )
                logger.info(
                    f"Stored source_input for node {_node_id}: input_source='{input_source}', content_preview='{preview}...'"
                )
        except Exception as e:
            logger.warning(f"Failed to store source_input for node {_node_id}: {e}")

        # Fallback OCR removed to respect explicit 'none' input source
        # if input_text is None and ocr_confidence > 0 and input_source.lower() in ('none', '', 'null'):
        #      logger.info(f"Input source is '{input_source}' but ocr_confidence={ocr_confidence}. Attempting fallback OCR.")
        #      ocr_text, _ = get_input_text(
        #         node,
        #         self.variables,
        #         "ocr",
        #         ocr_confidence,
        #         stop_flag,
        #         use_direct_rag,
        #      )
        #      if ocr_text:
        #          input_text = ocr_text
        #          # Update source for prompt assembly
        #          input_source = "SCREEN_CONTEXT"
        #          use_input_text_in_prompt = True
        #          logger.info("Fallback OCR successful. Using extracted text as input.")

        if input_text is not None:
            if is_form_filling_preset:
                # Add the form field data to the prompt context
                prompt = f"Current Form Layout & Fields:\n{input_text}\n\n{prompt}"
                logger.info(f"Enhanced prompt with Vision Form Field input")
            elif use_input_text_in_prompt:
                prompt = assemble_enhanced_prompt(
                    prompt, input_source, input_text, include_raw=True
                )
                logger.info(f"Enhanced prompt with {input_source} input")
            else:
                logger.info(
                    "Direct RAG enabled; skipping raw previous text injection into prompt"
                )

        # ── Context supplement (inserted between CURRENT INPUT and QUESTION sections) ──
        # The 'context' port is context/history ONLY: its upstream values are
        # always injected here (never as prompt input — see the 'prompt' port
        # for that).
        #
        # The prompt produced by assemble_enhanced_prompt() contains a
        # _HISTORY_INSERT_MARKER sentinel between CURRENT INPUT and QUESTION.
        # We replace that marker with the actual interaction-history block,
        # or strip it if no history is available.
        #
        # Context consolidation replaces raw injection entirely when the
        # node opts in (flag-only gate) — see the consolidation block below.

        # Whether this node uses embedding-based context consolidation.
        # Explicit true always enables it; llamacpp nodes with context sources
        # (context-node connection, upstream context value, or RAG documents)
        # default to ON so context travels through embeddinggemma retrieval
        # instead of raw text injection — raw injection overflowed the
        # RAM-capped context and bypassed the retrieval pipeline entirely.
        # Explicit false force-disables it (the raw fallback below is then
        # size-guarded so it cannot blow the context window).
        _explicit_consol = str(
            llm_config.get("use_context_consolidation", "")
        ).strip().lower()
        _use_llamacpp_gate = str(llm_config.get("use_llamacpp", False)).lower() in (
            "true", "1", "yes", "on",
        )
        _has_rag_docs_gate = bool(llm_config.get("rag_documents") or [])
        _ctx_consolidation_enabled = _explicit_consol in ("true", "1")
        if not _ctx_consolidation_enabled and _explicit_consol not in ("false", "0"):
            _ctx_consolidation_enabled = bool(
                _use_llamacpp_gate
                and (
                    has_context_connection
                    or has_upstream_context_value
                    or _has_rag_docs_gate
                )
            )
            if _ctx_consolidation_enabled:
                logger.info(
                    "Context Consolidation: auto-enabled for llamacpp node "
                    "with context sources (use_context_consolidation unset)"
                )

        # Collect and measure context content first
        _context_text_parts = []
        _context_total_chars = 0
        if has_context_connection and has_upstream_context_value:
            for cid in context_ids:
                ctx_val = upstream_value(
                    self.variables, cid, _context_source_type.get(cid, 'context')
                )
                if ctx_val is None:
                    ctx_val = ""
                if str(ctx_val).strip():
                    text = str(ctx_val)
                    _context_text_parts.append(text)
                    _context_total_chars += len(text)

        if _context_text_parts:
            # Flag-only gate: when consolidation is ON, the raw dump is
            # skipped here and the embedding-based retrieval below replaces
            # it.  When OFF, the accumulated context is injected as-is.
            if _ctx_consolidation_enabled:
                # Flag on — skip raw dump, consolidation will retrieve relevant chunks
                logger.info(
                    "Skipping raw context injection (%d chars from %d context node(s)) — "
                    "deferring to embedding-based consolidation below",
                    _context_total_chars, len(_context_text_parts),
                )
                # Strip the marker to avoid leaving it in the prompt
                if _HISTORY_INSERT_MARKER in prompt:
                    prompt = prompt.replace(_HISTORY_INSERT_MARKER, "")
            else:
                # Direct injection of the accumulated context — size-guarded
                # against the node's llama.cpp context window so a long
                # session summary cannot overflow the server context (a
                # guaranteed 400 that was previously retried verbatim and
                # killed the whole workflow).
                history_section = _trim_to_context_budget(
                    "\n".join(_context_text_parts),
                    llm_config,
                    reserve_chars=(
                        len(prompt) + len(str(system_message or "")) + 512
                    ),
                )

                if _HISTORY_INSERT_MARKER in prompt:
                    prompt = prompt.replace(_HISTORY_INSERT_MARKER, history_section)
                    logger.info(
                        f"Inserted context from {len(_context_text_parts)} context node(s) into prompt (via marker)"
                    )
                else:
                    # Fallback: prepend history before the prompt
                    prompt = history_section + "\n\n" + prompt
                    logger.info(
                        f"Prepended context from {len(_context_text_parts)} context node(s) to prompt (fallback)"
                    )
                # Debug: log a preview of the context being injected
                for i, part in enumerate(_context_text_parts):
                    logger.info(f"  Context part {i + 1} preview: {part[:300]}...")

        # Strip the marker if it wasn't replaced (no context available)
        if _HISTORY_INSERT_MARKER in prompt:
            prompt = prompt.replace(_HISTORY_INSERT_MARKER, "")

        # Debug: log context detection state for diagnostics
        logger.info(
            f"Context detection: has_connection={has_context_connection}, has_value={has_upstream_context_value}, context_ids={context_ids}, input_source={input_source}"
        )
        for cid in context_ids:
            v = self.variables.get(f"node_{cid}_context", "<NOT SET>")
            logger.info(
                f"  node_{cid}_context: {'<EMPTY>' if v == '' else (str(v)[:200] + '...') if len(str(v)) > 200 else v}"
            )

        try:
            ctx_text = _context_input(node, self.variables)
            routes = []
            for inp in node.get("inputs", []) or []:
                if str(inp.get("input_port")) == "context":
                    routes.append(str(inp.get("from_node")))
            ctx_info = f"Sources: {', '.join(routes) or 'None'}\n\n" + (
                ctx_text[:800] or ""
            )
            log_block(logger, logging.INFO, "Context Routing", ctx_info)
        except Exception:
            pass

        # Determine engine before logging — avoid misleading "Ollama" messages for llama.cpp
        _use_llamacpp_flag = str(llm_config.get("use_llamacpp", False)).lower() in (
            "true",
            "1",
            "yes",
            "on",
        )
        if _use_llamacpp_flag:
            logger.info(f"Preparing llama.cpp request thread for base_url: {api_url}")
        else:
            logger.info(f"Preparing Ollama request thread for base_url: {api_url}")
        # Always attempt to stop running models to ensure clean state for the new one
        # unless explicitly disabled via OLLAMA_PRE_STOP=false
        _base = f"http://{os.getenv('OLLAMA_HOST', 'localhost')}:{os.getenv('OLLAMA_PORT', '11434')}"
        # Only query/stop Ollama models when using the Ollama engine.
        # llama.cpp manages its own model lifecycle in-process.
        if not _use_llamacpp_flag:
            try:
                _pre_stop = str(os.getenv("OLLAMA_PRE_STOP", "true")).lower() not in (
                    "false",
                    "0",
                    "no",
                    "off",
                )
                if _pre_stop:
                    logger.info("Checking for running Ollama models to unload...")
                    try:
                        _ps = _rq.get(
                            f"{_base}/api/ps", headers={"Connection": "close"}, timeout=4
                        )
                        if _ps.status_code == 200:
                            _data = _ps.json() if hasattr(_ps, "json") else {}
                            _items = (
                                _data.get("models")
                                if isinstance(_data, dict)
                                else (_data if isinstance(_data, list) else [])
                            )

                            if isinstance(_items, list) and _items:
                                logger.info(f"Found {len(_items)} running models.")
                                for _m in _items:
                                    _pid = _m.get("id") if isinstance(_m, dict) else None
                                    _name = (
                                        (_m.get("model") or _m.get("name"))
                                        if isinstance(_m, dict)
                                        else None
                                    )

                                    # Optimization: Don't stop the model we are about to use
                                    # improved matching to handle tags (e.g. llama3.2 vs llama3.2:latest)
                                    if _name == model or (
                                        _name
                                        and model
                                        and (
                                            _name.startswith(model + ":")
                                            or model.startswith(_name + ":")
                                        )
                                    ):
                                        logger.info(
                                            f"Model {_name} matches target {model}. Keeping it."
                                        )
                                        continue

                                    # Optimization: Don't stop the embedding model if we are about to use it for RAG
                                    if (
                                        use_direct_rag
                                        and rag_embedding_model
                                        and _name
                                        and (
                                            _name == rag_embedding_model
                                            or _name.startswith(rag_embedding_model + ":")
                                            or rag_embedding_model.startswith(_name + ":")
                                        )
                                    ):
                                        logger.info(
                                            f"Model {_name} matches RAG embedding model {rag_embedding_model}. Keeping it."
                                        )
                                        continue

                                    _payload = {}
                                    if _pid:
                                        _payload["id"] = _pid
                                    if _name:
                                        _payload["model"] = _name

                                    if _payload:
                                        try:
                                            logger.info(
                                                f"Stopping competing model: {_name or _pid}"
                                            )
                                            _rq.post(
                                                f"{_base}/api/stop",
                                                json=_payload,
                                                headers={"Connection": "close"},
                                                timeout=3,
                                            )
                                        except Exception as e:
                                            logger.warning(
                                                f"Failed to stop model {_name}: {e}"
                                            )
                            else:
                                logger.debug("No running models found.")
                    except Exception as e:
                        logger.warning(f"Failed to query/stop Ollama models: {e}")
            except Exception:
                pass

        rag_context_text = None
        rag_docs_context = None
        emb_client = None
        _direct_active = (
            _direct_transport is not None
            and _direct_transport.is_direct_mode()
        )

        def _ensure_emb_client():
            """Build the RAG embedding client ONLY when RAG actually runs.

            Previously constructed on every LLM node execution (even for
            llama.cpp-only chains with empty rag_documents), which logged
            spurious "Connecting to embedding client ... (Direct Ollama)"
            lines and probed Ollama for nothing.
            """
            nonlocal emb_client
            if emb_client is not None:
                return emb_client
            if _direct_active or _use_llamacpp_flag:
                # llama.cpp node (or standalone direct engine): the lazy
                # llama.cpp embedding server (embeddinggemma GGUF, offline).
                # The node's rag_embedding_model is only cosmetic here —
                # the server is authoritative and never falls back to
                # Ollama/PyTorch embedders.
                logger.info(
                    "RAG: node engine is llama.cpp — using local embedding "
                    "server (model='%s', offline)",
                    rag_embedding_model,
                )
                emb_client = _ServerEmbeddingClient(rag_embedding_model)
            else:
                try:
                    # Direct Ollama connection for embeddings avoids OverWatch
                    # queue/cleanup deadlocks.
                    ollama_host = os.getenv("OLLAMA_HOST", "localhost")
                    ollama_port = os.getenv("OLLAMA_PORT", "11434")
                    ollama_base_url = f"http://{ollama_host}:{ollama_port}"
                    logger.info(
                        f"RAG: Connecting to embedding client at {ollama_base_url} (Direct Ollama)"
                    )
                    emb_client = OllamaClient(
                        base_url=ollama_base_url, debug=True
                    )
                except Exception:
                    logger.warning(
                        "RAG: Failed to connect to direct Ollama, falling back to API URL"
                    )
                    emb_client = OllamaClient(
                        base_url=api_url, debug=True
                    )
            return emb_client
        if (
            use_direct_rag
            and input_source in ("previous", "previous_node")
            and input_text
            and input_text.strip()
            and _ctx_consolidation_enabled
        ):
            # The previous output is already in the consolidation context
            # (llm_outputs) — re-chunking it via direct RAG is redundant.
            logger.info(
                "Direct RAG on previous output skipped for node %s — "
                "context consolidation handles it via embedding retrieval",
                _node_id,
            )
        if (
            use_direct_rag
            and input_source in ("previous", "previous_node")
            and input_text
            and input_text.strip()
            and not _ctx_consolidation_enabled
        ):
            # ── Check if upstream is an Input passthrough node — skip RAG ──
            _skip_rag_for_input_node = False
            for inp in node.get("inputs", []):
                cid = inp.get("from_node")
                if cid and self.variables.get(f"node_{cid}_is_input_passthrough"):
                    _skip_rag_for_input_node = True
                    break

            if _skip_rag_for_input_node:
                # Input node data was already injected directly into prompt
                # via get_input_text() + use_input_text_in_prompt=True above.
                # Just prevent RAG processing and the fallback injection.
                logger.info(
                    "Direct RAG skipped for Input passthrough node — "
                    "raw input already in prompt"
                )
                rag_context_text = None
                input_text = None
            else:
                # RAG is actually going to run — build the embedding client now.
                emb_client = _ensure_emb_client()
                pre_chunks = None
                pre_embs = None
                try:
                    if connected_ids:
                        # iterate in reverse to find the most recent/relevant predecessor with a vectorstore
                        for prev_id in reversed(connected_ids):
                            store = self.variables.get(f"node_{prev_id}_vectorstore")
                            if isinstance(store, dict):
                                pre_chunks = store.get("chunks")
                                pre_embs = store.get("embeddings")
                                if pre_chunks and pre_embs:
                                    logger.info(
                                        f"Using precomputed vectorstore from node {prev_id}"
                                    )
                                    break
                except Exception:
                    pass
                _node_id = llm_data.get("node_id") or llm_data.get("id") or node.get("id")
                rag_context_text, updated_prompt, vector_store = build_rag_context(
                    emb_client,
                    prompt,
                    input_text,
                    rag_embedding_model,
                    rag_chunk_size,
                    rag_overlap,
                    rag_top_k,
                    rag_include_raw_input,
                    rag_max_chars,
                    input_source,
                    use_input_text_in_prompt,
                    pre_chunks,
                    pre_embs,
                )
                if updated_prompt:
                    prompt = updated_prompt
                try:
                    if vector_store and _node_id:
                        self.variables[f"node_{_node_id}_vectorstore"] = vector_store
                except Exception:
                    pass

        try:
            import json as _json

            rag_docs = llm_config.get("rag_documents", []) or []
            if isinstance(rag_docs, str):
                rag_docs = _json.loads(rag_docs)
        except Exception:
            rag_docs = []

        # ── GraphRAG-based document ingestion ──
        # Rebuild chunks from rag_documents ranked by relevance to the
        # current user question on every execution.  No cursor state is
        # carried across graph loop iterations — each question gets its
        # own independent RAG retrieval.
        rag_cursor = None
        if rag_docs and use_direct_rag and not _ctx_consolidation_enabled:
            # Document ingestion is handled ENTIRELY by context consolidation
            # when it is enabled: GraphRAG would re-chunk the same documents
            # (double embedding cost) and could inject raw chunks if
            # consolidation produced nothing.
            logger.info(
                "GraphRAG skipped for node %s — document ingestion deferred "
                "to context consolidation (embedding-only)", _node_id,
            )
            # Include the user's actual question in the ranking query, not just
            # the static prompt template (which may be empty for demo.json).
            _rag_query = (input_text or "") + "\n" + prompt if prompt else (input_text or "")
            # GraphRAG embeddings follow the node's engine: llama.cpp →
            # embedding server (embeddinggemma), Ollama → the node's
            # embedding model via direct Ollama (e.g. nomic-embed-text).
            if _direct_active or _use_llamacpp_flag:
                def _graphrag_embed(texts):
                    from AI import embedding_server

                    vectors = embedding_server.embed_texts(list(texts))
                    if vectors is None:
                        logger.warning(
                            "GraphRAG: embedding server returned no vectors — "
                            "document context will be skipped (no lexical "
                            "fallback by design)"
                        )
                    return vectors
            else:
                def _graphrag_embed(texts):
                    cl = _ensure_emb_client()
                    if cl is None:
                        return None
                    try:
                        embs = cl.embeddings(rag_embedding_model, list(texts))
                        return embs or None
                    except Exception:
                        return None
            try:
                rag_cursor = build_graphrag_context(
                    rag_docs,
                    _rag_query,
                    chunk_size=rag_chunk_size,
                    overlap=rag_overlap,
                    max_chunks=max(1, rag_top_k * 5),
                    max_chars=rag_max_chars,
                    embed_fn=_graphrag_embed,
                )
                if rag_cursor:
                    logger.info(
                        "GraphRAG cursor created: %d chunks total",
                        rag_cursor._total,
                    )
            except Exception as e:
                from AI.comorag_engine import EmbeddingFailureError
                if isinstance(e, EmbeddingFailureError):
                    # Hard break: embeddings are the only retrieval path —
                    # the failure must stop the chain, not fall back silently.
                    raise
                logger.warning(f"GraphRAG cursor creation failed: {e}, falling back to one-shot")
                rag_cursor = None

            # Fallback to one-shot embedding-based RAG if GraphRAG unavailable
            if rag_cursor is None:
                emb_client = _ensure_emb_client()
                if emb_client is not None:
                    rag_docs_context = build_rag_context_from_docs(
                        emb_client,
                        prompt,
                        rag_docs,
                        rag_embedding_model,
                        rag_chunk_size,
                        rag_overlap,
                        rag_top_k,
                        rag_max_chars,
                    )

        try:
            if emb_client:
                emb_client.close()
        except Exception:
            pass

        # ── Context Consolidation (ComoRAG-inspired) ──
        # Flag-only gate: use_context_consolidation=true means this node reads
        # its accumulated context (context node / docs / outputs) through
        # embedding retrieval instead of raw injection.  No thresholds.
        consolidated_context = None
        # Hooks are wired ONLY for the llama.cpp engine (see the _intent_planner
        # / _probe_gen / _fact_gen / _judge_gen / _resp_gen block below).  An
        # Ollama node with the flag on used to run the engine with EVERY hook
        # unset, so the "multi-cycle sweep" degenerated into the deterministic
        # ladder over raw chunks — strictly worse than single-pass retrieval.
        use_consolidation = _ctx_consolidation_enabled and _use_llamacpp_flag
        if _ctx_consolidation_enabled and not _use_llamacpp_flag:
            logger.warning(
                "Context Consolidation disabled for node %s: requested, but "
                "the ollama engine wires no consolidation hooks (planner / "
                "prober / composer / judge / synthesis) — using single-pass "
                "retrieval instead of a sweep without hooks",
                _node_id,
            )
        logger.info(
            "Context Consolidation: %s for node %s (embedding model='%s', engine=%s)",
            "ENABLED" if use_consolidation else "disabled",
            _node_id, rag_embedding_model,
            "llamacpp" if _use_llamacpp_flag else "ollama",
        )
        try:
            if use_consolidation:
                # Gather all context sources
                ctx_sources = {
                    "context_nodes": [],
                    "documents": [],
                    "code_outputs": [],
                    "llm_outputs": [],
                }

                # 1. Data inputs feed consolidation by PORT — no node-type
                # gate (mirrors inputs.py: _context_input reads the context
                # port, _previous_input reads the base input's output for
                # ANY upstream type).  A web-sequence node sharing text/HTML
                # is just an upstream producing output.  Context INPUT port:
                # Context/LLM nodes store node_<id>_context; other node
                # types store only node_<id>_output — fall back to it so the
                # shared text is never dropped.
                seen_ctx = set()
                for inp in node.get("inputs", []) or []:
                    if not isinstance(inp, dict):
                        continue
                    port = str(inp.get("input_port", ""))
                    f_id = inp.get("from_node")
                    if not f_id or f_id in seen_ctx:
                        continue
                    if port == "context":
                        payload = _context_port_payload(
                            self.variables, f_id, _record_output_type(inp)
                        )
                    else:
                        # base input / tools edges: resolve by the source output
                        # port — 'data' edges read node_<id>_data, base edges
                        # read node_<id>_output.
                        payload = upstream_value(
                            self.variables, f_id, _record_output_type(inp)
                        )
                        if payload is None:
                            payload = ""
                    if payload and str(payload).strip():
                        seen_ctx.add(f_id)
                        ctx_sources["context_nodes"].append(str(payload))
                try:
                    docs = llm_config.get("rag_documents", []) or []
                    if isinstance(docs, str):
                        import json as _json
                        docs = _json.loads(docs)
                    # Read document contents — proper per-extension
                    # extraction (PDF/DOCX via rag_utils), never a raw
                    # byte dump: the GraphRAG path already extracts; the
                    # consolidation path used to read the file as UTF-8
                    # text, chunking PDF binary junk straight into the
                    # evidence pool.
                    try:
                        from . import rag_utils as _rag_utils_mod
                    except Exception:
                        _rag_utils_mod = None
                    for doc_path in docs:
                        if doc_path and isinstance(doc_path, str) and os.path.exists(doc_path):
                            _doc_text = ""
                            try:
                                _ext = os.path.splitext(doc_path)[1].lower()
                                if _rag_utils_mod is not None:
                                    if _ext in (".txt", ".md", ".csv", ".json", ".log"):
                                        _doc_text = _rag_utils_mod._read_text_file(doc_path)
                                    elif _ext == ".docx":
                                        _doc_text = _rag_utils_mod._extract_docx_text(doc_path)
                                    elif _ext == ".pdf":
                                        _doc_text = _rag_utils_mod._extract_pdf_text(doc_path)
                                if not _doc_text:
                                    # Fallback: raw read (plain-text loaders
                                    # under arbitrary extensions).
                                    with open(doc_path, "r", encoding="utf-8") as _df:
                                        _doc_text = _df.read()
                            except Exception:
                                try:
                                    with open(doc_path, "r", encoding="latin-1") as _df:
                                        _doc_text = _df.read()
                                except Exception:
                                    _doc_text = ""
                            _doc_text = _clean_ingested_text(_doc_text)
                            if _doc_text:
                                ctx_sources["documents"].append(_doc_text)
                except Exception:
                    pass

                # Check if any consolidation-relevant sources exist
                total_ctx_chars = sum(
                    len(str(v))
                    for values in ctx_sources.values()
                    for v in (values if isinstance(values, list) else [])
                )

                # Structured view of every gathered context source — LOG
                # PREVIEW ONLY (never injected): tight per-source cap so the
                # console shows what was collected without dumping whole
                # documents (the doc only enters the prompt via embedding
                # retrieval + consolidation).
                _src_parts = []
                for _kind, _values in ctx_sources.items():
                    for _i, _v in enumerate(_values or []):
                        _s = str(_v)
                        if len(_s) > 400:
                            _s = _s[:400] + f"...[{len(str(_v)) - 400} chars omitted]"
                        _src_parts.append(f"[{_kind} #{_i + 1}]\n{_s}")
                log_block(
                    logger, logging.INFO,
                    f"Context Sources ({total_ctx_chars} chars total)",
                    "\n\n---\n\n".join(_src_parts)
                    if _src_parts else "no context sources",
                )

                if total_ctx_chars > 0:
                    # Create and run consolidator
                    from AI.comorag_engine import ProbeBasedConsolidator

                    # Embedding backend follows the node's engine: llama.cpp →
                    # the local embedding server; Ollama → the node's
                    # embedding model (e.g. nomic-embed-text) so chunk and
                    # probe vectors stay in one space.
                    _consol_embed_client = None
                    if not _direct_active and not _use_llamacpp_flag:
                        _consol_embed_client = _OllamaEmbedClient(
                            _ensure_emb_client(), rag_embedding_model,
                        )
                    # The chunk vector cache is process-global and keyed on
                    # chunk text alone: namespace it so vectors from the OTHER
                    # embedding backend are never reused as comparable.
                    _consol_ns = "%s:%s" % (
                        "llamacpp" if (_use_llamacpp_flag or _direct_active)
                        else "ollama",
                        rag_embedding_model,
                    )
                    consolidator = ProbeBasedConsolidator(
                        api_url=api_url,
                        chunk_size=int(
                            llm_config.get("consolidation_chunk_size", 1000) or 1000
                        ),
                        overlap=int(
                            llm_config.get("consolidation_overlap", 200) or 200
                        ),
                        top_k=int(
                            llm_config.get("consolidation_top_k", 3) or 3
                        ),
                        char_budget=int(
                            llm_config.get("rag_max_chars", 1500) or 1500
                        ),
                        # Hard cap on VALID (fact-satisfying) cycles.
                        max_probes=int(
                            llm_config.get("consolidation_max_probes", 6) or 6
                        ),
                        # Fact quota: a cycle only counts as done once it
                        # accumulates this many COMPOSED facts.
                        min_facts=int(
                            llm_config.get("consolidation_min_facts", 3) or 3
                        ),
                        # Chars of the FULL base context exposed to the probe
                        # generator (source of truth for probe vocabulary).
                        probe_context_chars=int(
                            llm_config.get("consolidation_probe_context_chars", 6000) or 6000
                        ),
                        probe_max_tokens=int(
                            llm_config.get("consolidation_max_tokens", 64) or 64
                        ),
                        embed_client=_consol_embed_client,
                        cache_namespace=_consol_ns,
                    )

                    # Probe generator: the LLM node's own llamacpp model
                    # derives each next probe from the ENHANCED INTENT prompt
                    # (goal + system + context excerpt, composed by the
                    # consolidator) plus the facts accumulated so far — so
                    # the probes are grounded in the available context, not
                    # invented from the bare request.  Any failure falls
                    # back to lexical.
                    _probe_gen = (
                        (lambda intent, r: _generate_llamacpp_probe(
                            intent, r, llm_config, api_url,
                            user_prompt=str(original_prompt or ""),
                        ))
                        if _use_llamacpp_flag else None
                    )
                    # Per-cycle responder: the LLM answers each probe using
                    # its retrieved evidence — the answer's facts enter the
                    # memory pool (ComoRAG cycle step).
                    _fact_gen = (
                        (lambda q, ev: _generate_fact_answer(
                            q, ev, llm_config, api_url,
                        ))
                        if _use_llamacpp_flag else None
                    )
                    # Sufficiency judge (ComoRAG meta-loop gate): after each
                    # probe's facts it decides whether the goal is
                    # answerable yet (ANSWER -> stop the probe loop early)
                    # or reports it as still incomplete (MISSING -> keep
                    # probing).  LAYA FIRST BY DEFAULT: the embedded local
                    # decision engine makes the assessment (typed noul
                    # question, no prose parsing); the node's own model is
                    # consulted only when the engine is unavailable
                    # (``LOOPER_LAYA=off`` restores the LLM judge everywhere).
                    def _judge_gen(g, f, ev):
                        from AI.laya_hooks import sufficiency_or
                        return sufficiency_or(
                            g, f, ev,
                            fallback=(
                                (lambda: _generate_sufficiency_judgment(
                                    g, f, ev, llm_config, api_url))
                                if _use_llamacpp_flag else None
                            ),
                        )
                    # Relevance verdict (one call per finding): the
                    # lexical topicality gate keeps a finding that reuses a
                    # focus noun for a DIFFERENT detail, and such a finding
                    # fills the fact quota while steering the next probe off
                    # target.  Laya-first relevance by default; the LLM
                    # evaluator is the fallback when the engine is down.
                    def _rel_judge(q, claims):
                        from AI.laya_hooks import relevance_or
                        return relevance_or(
                            q, claims,
                            fallback=(
                                (lambda: _generate_finding_relevance(
                                    q, claims, llm_config, api_url))
                                if _use_llamacpp_flag else None
                            ),
                        )
                    # Final synthesis (ComoRAG): ONE response from ALL the
                    # accumulated facts, anchored to the enhanced intent and
                    # grounded on the VERBATIM evidence chunks (sources win
                    # over self-generated facts).
                    _resp_gen = (
                        (lambda intent, f, ev: _generate_consolidated_response(
                            str(original_prompt or ""), intent, f, ev,
                            llm_config, api_url,
                        ))
                        if _use_llamacpp_flag else None
                    )
                    # Initial INTENT PLAN: the node's own model synthesizes
                    # WHAT the pipeline needs to know / WHERE to look / WHAT
                    # to expect from the enhanced intent — the base from
                    # which every probe derives (never shot straight from
                    # the user prompt).  Any failure falls back to raw
                    # enhanced-intent probing.
                    _intent_planner = (
                        (lambda intent: _generate_intent_plan(
                            intent, llm_config, api_url,
                        ))
                        if _use_llamacpp_flag else None
                    )
                    consolidated_context = consolidator.consolidate(
                        prompt=original_prompt,  # Use original prompt (before RAG injection) for clean probe extraction
                        system_prompt=str(system_message or ""),
                        context_sources=ctx_sources,
                        intent_planner=_intent_planner,
                        probe_generator=_probe_gen,
                        responder=_fact_gen,
                        response_generator=_resp_gen,
                        judge=_judge_gen,
                        relevance_judge=_rel_judge,
                    )

                    if consolidated_context:
                        # Facts become workflow VARIABLES: the curated fact
                        # pool is stored so the LLM can use it outside the
                        # injected context too, and it persists even when the
                        # raw context is long (facts are compact, sources are
                        # not).
                        try:
                            _pool = consolidator.facts_pool
                            self.variables[
                                f"node_{_node_id}_fact_pool"
                            ] = "\n\n---\n\n".join(
                                _pool
                            ) if _pool else consolidated_context
                            self.variables[
                                f"node_{_node_id}_consolidated_facts"
                            ] = consolidated_context
                            logger.info(
                                "Consolidation: stored %d curated fact(s) as "
                                "variables (node_%s_fact_pool, "
                                "node_%s_consolidated_facts)",
                                len(_pool), _node_id, _node_id,
                            )
                        except Exception:
                            pass

                    if consolidated_context:
                        logger.info(
                            "Context Consolidation: retrieved %d chars via embedding attention",
                            len(consolidated_context),
                        )
                        log_block(
                            logger, logging.INFO,
                            "Retrieved Context (consolidated)", consolidated_context,
                        )
                    else:
                        logger.info(
                            "Context Consolidation: skipped (below threshold or no relevant content)"
                        )
        except Exception as cons_exc:
            from AI.comorag_engine import EmbeddingFailureError
            if isinstance(cons_exc, EmbeddingFailureError):
                # Hard break: embeddings are the only retrieval path, so an
                # embedding failure must stop the chain loudly (the overlay
                # shows the engine error) instead of silently skipping.
                logger.error(
                    "Context Consolidation HARD STOP — embedding failure: %s",
                    cons_exc,
                )
                raise
            logger.warning(
                "Context Consolidation failed (skipping context injection — "
                "no raw fallback by design): %s",
                cons_exc,
            )
            consolidated_context = None

        if consolidated_context:
            # Retrieved context via embedding attention — replaces raw text
            # dump and the GraphRAG doc chunks (same documents re-chunked).
            prompt = f"[Retrieved Context]:\n{consolidated_context}\n\n{prompt}"
            rag_docs_context = None  # Documents already covered by consolidation
        else:
            # Consolidation off, failed, or returned nothing.  Embeddings mode
            # NEVER falls back to the raw context dump — the node runs without
            # the skipped context rather than silently switching to raw text.
            if _ctx_consolidation_enabled and _context_text_parts:
                logger.warning(
                    "Context Consolidation produced no output — skipping "
                    "context injection entirely (%d chars from %d context "
                    "node(s) NOT injected; raw fallback disabled by design)",
                    _context_total_chars, len(_context_text_parts),
                )
            # Direct-RAG retrieval of the previous node output is a different
            # source than consolidation (context nodes/documents) — keep it.
            if rag_context_text:
                prompt = (
                    _trim_to_context_budget(
                        rag_context_text,
                        llm_config,
                        reserve_chars=(
                            len(prompt) + len(str(system_message or "")) + 512
                        ),
                    )
                    + "\n\n"
                    + prompt
                )

        # ── GraphRAG refined chunk injection ──
        # All refined chunks are injected in one shot so the LLM sees the
        # full relevant context for the user's question. Small models
        # (~4092 tokens) can handle 10-20 refined chunks of ~250-500 chars
        # each without overflow (~2500-5000 total chars). The cursor is still
        # used for serialization but drained entirely in a single pass.
        if rag_cursor and rag_cursor.has_more and not consolidated_context:
            chunks_text = []
            while rag_cursor.has_more:
                chunk, _ = rag_cursor.next_chunk()
                if chunk:
                    chunks_text.append(chunk)
            if chunks_text:
                _chunks_joined = _trim_to_context_budget(
                    "\n\n".join(chunks_text),
                    llm_config,
                    reserve_chars=(
                        len(prompt) + len(str(system_message or "")) + 512
                    ),
                )
                prompt = _chunks_joined + "\n\n" + prompt
                self.variables[f"node_{_node_id}_rag_exhausted"] = True
                logger.info(
                    "GraphRAG: injected %d refined chunks (%.0f chars total)",
                    len(chunks_text), sum(len(c) for c in chunks_text),
                )
        elif rag_docs_context:
            # Fallback: one-shot embedding-based RAG (legacy path)
            prompt = f"{rag_docs_context}\n\n{prompt}"

        if stop_flag and stop_flag():
            logger.info("LLM node execution stopped by user request before API call")
            _cleanup_vision_images()
            return None

        # Debug: log the final prompt that will be sent to the LLM
        log_block(
            logger, logging.INFO,
            f"Final Prompt (sent to LLM, {len(prompt)} chars)", prompt,
        )

        def _task_runner(out_dict: Dict[str, Any]):
            if stop_flag and stop_flag():
                return

            _node_id = llm_data.get("node_id") or llm_data.get("id") or node.get("id")

            try:
                # Check if using llama.cpp
                use_llamacpp = llm_config.get("use_llamacpp", False)
                if isinstance(use_llamacpp, str):
                    use_llamacpp = use_llamacpp.lower() in ("true", "1", "yes", "on")

                if use_llamacpp:
                    # Use llama.cpp engine
                    logger.info("Using llama.cpp engine for LLM execution")
                    llamacpp_model_path = llm_config.get("llamacpp_model_path", "")
                    # CPU-first design: gpu_layers defaults to 0 (pure CPU —
                    # the bundled build has no GPU backend), threads -1 lets
                    # the server auto-detect all cores, and context_size 0
                    # uses the model's native training context instead of
                    # forcing a small 4096 window.
                    llamacpp_gpu_layers = int(
                        llm_config.get("llamacpp_gpu_layers", 0) or 0
                    )
                    llamacpp_threads = int(
                        llm_config.get("llamacpp_threads", -1) or -1
                    )
                    llamacpp_context_size = int(
                        llm_config.get("llamacpp_context_size", 0) or 0
                    )

                    if not llamacpp_model_path:
                        logger.error("llama.cpp model path not specified")
                        out_dict["error"] = "llama.cpp model path not specified"
                        return

                    # Resolve relative model path against AI/models/
                    if not os.path.isabs(llamacpp_model_path):
                        try:
                            from AI.config_loader import get_models_dir
                            resolved_path = os.path.normpath(
                                os.path.join(get_models_dir(), llamacpp_model_path)
                            )
                            logger.debug(
                                f"Resolved llama.cpp model path: {llamacpp_model_path} -> {resolved_path}"
                            )
                            llamacpp_model_path = resolved_path
                        except Exception as e:
                            logger.warning(
                                f"Failed to resolve relative model path: {e}"
                            )

                    if not os.path.exists(llamacpp_model_path):
                        logger.error(
                            f"llama.cpp model not found: {llamacpp_model_path}"
                        )
                        out_dict["error"] = f"Model not found: {llamacpp_model_path}"
                        return

                    # Build llama.cpp API payload
                    try:
                        import requests as llm_req
                        from AI import inprocess_transport as _direct_transport

                        direct_mode = _direct_transport.is_direct_mode()
                        llamacpp_api_url = api_url.rstrip("/") if api_url else ""

                        payload = {
                            "model": llamacpp_model_path,
                            "prompt": effective_prompt
                            if "effective_prompt" in locals()
                            else prompt,
                            "system": system_message or "",
                            "temperature": temperature,
                            "max_tokens": max_tokens,
                            "stream": False,
                            "use_llamacpp": True,
                            "gpu_layers": llamacpp_gpu_layers,
                            "threads": llamacpp_threads,
                            "context_size": llamacpp_context_size,
                            # Same node burst as the consolidation calls above:
                            # keep the model loaded; /llamacpp/release fires
                            # when the burst (incl. recursion) is done.
                            "cleanup_after": False,
                        }
                        # Include images for vision-capable GGUF models
                        # images is captured from the outer closure (prepare_vision_images)
                        if images is not None:
                            payload["images"] = images
                            logger.info(
                                f"Including {len(images)} image(s) in llama.cpp payload"
                            )
                        else:
                            logger.info(
                                "No images in llama.cpp payload (images is None)"
                            )

                        def _llamacpp_call(_payload):
                            """POST to the API gateway, or call the in-process
                            engine directly when ARROW_DIRECT_ENGINE=1."""
                            if direct_mode:
                                logger.info(
                                    "Direct engine transport: in-process llama.cpp call"
                                )
                                return _direct_transport.generate(
                                    model=_payload.get("model", ""),
                                    prompt=_payload.get("prompt", ""),
                                    system=_payload.get("system") or "",
                                    messages=_payload.get("messages"),
                                    max_tokens=_payload.get("max_tokens"),
                                    temperature=_payload.get("temperature"),
                                    stream=False,
                                    images=_payload.get("images"),
                                    gpu_layers=_payload.get("gpu_layers"),
                                    threads=_payload.get("threads"),
                                    context_size=_payload.get("context_size"),
                                )
                            logger.info(
                                f"Sending request to llama.cpp endpoint: "
                                f"{llamacpp_api_url}/llamacpp/generate"
                            )
                            _resp = llm_req.post(
                                f"{llamacpp_api_url}/llamacpp/generate",
                                json=_payload,
                                timeout=None,  # No timeout — wait for model to complete
                            )
                            if _resp.status_code != 200:
                                raise _direct_transport.LLMTransportError(
                                    f"llama.cpp API error: "
                                    f"{_resp.status_code} - {_resp.text}"
                                )
                            return _resp.json()

                        result = _llamacpp_call(payload)

                        # --- Recursive generation: continue if model hit max_tokens ---
                        _base_prompt = payload["prompt"]
                        _recursive_parts = []
                        _first_text = (
                            result.get("response", "")
                            or result.get("text", "")
                            or ""
                        )
                        _recursive_parts.append(_first_text)

                        # Recursive continuation only when the engine reported a
                        # TRUNCATED generation (max_tokens/context exhausted) AND
                        # consolidation is EXPLICITLY enabled by the user (not
                        # auto-enabled by context size).  The engine already
                        # continues truncated responses internally (thinking
                        # models included), so this loop is a safety net for the
                        # rare case the engine gave up (e.g. context full).
                        # Auto-consolidation triggers on large context but
                        # recursive continuation makes simple conversational
                        # responses way too long.
                        _was_truncated = bool(
                            result.get("truncated")
                            or (result.get("_debug") or {}).get("truncated")
                        )
                        _should_recursive = bool(
                            _first_text
                            and len(_first_text) > 50
                            and _was_truncated
                            and use_consolidation
                        )

                        _MAX_RECURSIVE = 3
                        _ri = 0
                        while _should_recursive and _ri < _MAX_RECURSIVE:
                            _ri += 1
                            _combined_so_far = "".join(_recursive_parts)

                            # --- Re-retrieve fresh context based on latest generated text ---
                            _fresh_ctx = ""
                            try:
                                _fresh_ctx = consolidator.retrieve_for_text(
                                    _combined_so_far, top_k=2
                                )
                            except NameError:
                                pass  # consolidator never created (below threshold)
                            if not _fresh_ctx and (
                                getattr(consolidator, "_cached_chunks", None)
                                and consolidator._remaining_chunks() <= 0
                            ):
                                # Pool exhausted — fall back to non-exclusive
                                # retrieval so long generations keep accessing
                                # the corpus (the layered back-and-forth over
                                # the context, never a truncation).
                                logger.warning(
                                    "Consolidation: recursive re-retrieval pool "
                                    "exhausted — falling back to non-exclusive "
                                    "retrieval"
                                )
                                _saved = set(consolidator._retrieved_indices)
                                try:
                                    consolidator._retrieved_indices = set()
                                    _fresh_ctx = consolidator.retrieve_for_text(
                                        _combined_so_far, top_k=2
                                    )
                                finally:
                                    consolidator._retrieved_indices = _saved

                            # Use only the tail of previous output for continuity
                            _tail = _combined_so_far[-300:] if len(_combined_so_far) > 300 else _combined_so_far
                            _cont_prompt = _base_prompt
                            if _fresh_ctx:
                                _cont_prompt += (
                                    f"\n\n[Fresh Context]:\n{_fresh_ctx}"
                                )
                            _cont_prompt += (
                                f"\n\n[Previous output (tail only):]\n"
                                f"...{_tail}\n\n"
                                f"[Continue. Write NEW content only. Never repeat or "
                                f"summarize previous output. If nothing new to add, "
                                f"output [DONE].]"
                            )
                            payload["prompt"] = _cont_prompt
                            logger.info(
                                f"Recursive generation #{_ri}: continuing from "
                                f"{len(_combined_so_far)} chars (tail: {len(_tail)})"
                                + (f", fresh context: {len(_fresh_ctx)} chars" if _fresh_ctx else "")
                            )
                            try:
                                _cj = _llamacpp_call(payload)
                            except _direct_transport.LLMTransportError as _rec_err:
                                logger.warning(
                                    f"Recursive generation #{_ri} failed: {_rec_err}"
                                )
                                break
                            _new_t = (
                                _cj.get("response", "")
                                or _cj.get("text", "")
                                or ""
                            )
                            # Honor explicit [DONE] signal from model for clean self-steering
                            if "[DONE]" in _new_t:
                                _new_t = _new_t.replace("[DONE]", "").strip()
                                if _new_t:
                                    _recursive_parts.append(_new_t)
                                break
                            if len(_new_t) < 20:
                                break
                            _recursive_parts.append(_new_t)
                        # end while loop

                        # Clean up consolidator state for next chain node execution
                        try:
                            consolidator.reset_retrieved()
                        except (NameError, AttributeError):
                            pass

                        _full_text = "".join(_recursive_parts)
                        out_dict["result"] = {
                            "response": _full_text,
                            "_debug": result.get("_debug", {}),
                            "usage": result.get("usage", {}),
                        }
                        logger.info(
                            f"llama.cpp generation complete: {len(_full_text)} chars "
                            f"({len(_recursive_parts)} recursive chunk(s)), "
                            f"_debug={result.get('_debug', {})}"
                        )
                    except _direct_transport.LLMTransportError as _lte:
                        error_msg = str(_lte)
                        logger.error(error_msg)
                        out_dict["error"] = error_msg
                    except Exception as llamacpp_err:
                        error_msg = f"llama.cpp execution failed: {str(llamacpp_err)}"
                        logger.error(error_msg, exc_info=True)
                        out_dict["error"] = error_msg

                    # Node burst complete (main generation + consolidation
                    # probes + recursion): release the model NOW instead of
                    # leaving it to an idle clock — the burst held it via
                    # cleanup_after=False on every call.  Fire-and-forget: a
                    # lost release only delays the unload until the next
                    # request or app exit.
                    if not direct_mode:
                        try:
                            threading.Thread(
                                target=_fire_llamacpp_release,
                                args=(llamacpp_api_url,),
                                daemon=True,
                            ).start()
                        except Exception:
                            pass
                    return

                # Otherwise use Ollama (existing code)
                from AI.consult import OllamaClient as _Client

                # Disable client request timeouts for local LLMs
                cli = _Client(
                    base_url=api_url, debug=True, timeout=None
                )

                # Pre-flight API health check for better error diagnostics
                try:
                    _health = cli.health_check()
                    if isinstance(_health, dict):
                        _hstatus = _health.get("status", "")
                        if _hstatus != "healthy":
                            _hdetail = (
                                _health.get("message")
                                or _health.get("detail")
                                or _health.get("ollama_server", "")
                            )
                            logger.warning(
                                f"API health check: {_hstatus} - {_hdetail}"
                            )
                        else:
                            logger.debug("API health check: healthy")
                except Exception:
                    pass

                # --- Vision Pass Logic ---
                effective_prompt = prompt
                effective_images = images

                # Mark writing active for streaming typing paths
                stream_flag = bool(llm_config.get("stream", True)) and (
                    not tool_ids
                )
                out_dict["stream_used"] = stream_flag
                if (
                    stream_flag
                    and write_text
                    and action_handlers
                    and hasattr(action_handlers, "handle_type_string_action")
                    and hasattr(cli, "chat_stream")
                ):
                    write_mode = str(llm_config.get("write_mode") or "type").lower()
                    _accum = []
                    # Signal: upstream LLM is actively typing via stream
                    try:
                        if _node_id:
                            self.variables[f"node_{_node_id}_writing"] = True
                    except Exception:
                        pass
                    if write_mode == "paste":
                        res = cli.chat_stream(
                            model=model,
                            prompt=effective_prompt,
                            temperature=temperature,
                            max_tokens=max_tokens,
                            system=system_message or None,
                            images=effective_images,
                            on_delta=None,
                        )
                    else:
                        _buf = []
                        _full_text_tracker = ""
                        _last_raw_chunk = None
                        _last_flush = time.time()
                        _FLUSH_CHARS = int(
                            llm_config.get("stream_flush_chars", 120) or 120
                        )
                        _FLUSH_MS = float(
                            llm_config.get("stream_flush_ms", 0.25) or 0.25
                        )

                        def _flush_buffer():
                            if stop_flag and stop_flag():
                                return
                            if not _buf:
                                return
                            try:
                                chunk = "".join(_buf)
                                _buf.clear()
                                _write_chunk(
                                    chunk,
                                    {
                                        "text": chunk,
                                        "force_typing": True,
                                        "batch_size": int(
                                            llm_config.get("typing_batch_size", 5)
                                            or 5
                                        ),
                                        "batch_delay": float(
                                            llm_config.get(
                                                "typing_batch_delay", 0.01
                                            )
                                            or 0.01
                                        ),
                                        "char_by_char": bool(
                                            llm_config.get("char_by_char", False)
                                        ),
                                        "keystroke_delay": float(
                                            llm_config.get("keystroke_delay", 0.001)
                                            or 0.001
                                        ),
                                    },
                                )
                            except Exception:
                                pass

                        def _on_delta(s: str):
                            nonlocal _full_text_tracker, _last_raw_chunk
                            if stop_flag and stop_flag():
                                raise Exception("Stopped by user")
                            try:
                                # Handle case where backend sends full text instead of deltas
                                real_delta = s

                                # Determine if we are in Full Text mode (s extends previous chunk)
                                is_full_text = False
                                if (
                                    _last_raw_chunk is not None
                                    and len(s) > len(_last_raw_chunk)
                                    and s.startswith(_last_raw_chunk)
                                ):
                                    is_full_text = True
                                elif (
                                    len(_full_text_tracker) > 0
                                    and len(s) > len(_full_text_tracker)
                                    and s.startswith(_full_text_tracker)
                                ):
                                    is_full_text = True

                                if is_full_text:
                                    # Full Text Mode
                                    if _last_raw_chunk and s.startswith(
                                        _last_raw_chunk
                                    ):
                                        real_delta = s[len(_last_raw_chunk) :]
                                        # We must update tracker to match s because we trust s is the new full text
                                        _full_text_tracker = s
                                    elif s.startswith(_full_text_tracker):
                                        real_delta = s[len(_full_text_tracker) :]
                                        _full_text_tracker = s
                                    else:
                                        # Fallback (should be covered by elif above)
                                        real_delta = ""
                                else:
                                    # Delta Mode or Duplicate
                                    if s == _full_text_tracker or (
                                        _last_raw_chunk and s == _last_raw_chunk
                                    ):
                                        # Duplicate
                                        real_delta = ""
                                    elif (
                                        _full_text_tracker
                                        and _full_text_tracker.startswith(s)
                                    ):
                                        # Regression / Prefix Duplicate (ignore)
                                        real_delta = ""
                                    else:
                                        # Standard Delta
                                        real_delta = s
                                        _full_text_tracker += s

                                _last_raw_chunk = s

                                if not real_delta:
                                    return

                                _accum.append(real_delta)
                                _buf.append(real_delta)
                                try:
                                    if _node_id:
                                        _cur = out_dict.get("_accum_text") or ""
                                        _cur += real_delta
                                        out_dict["_accum_text"] = _cur
                                        self.variables[
                                            f"node_{_node_id}_output"
                                        ] = _cur
                                        self.variables[output_variable] = _cur
                                except Exception:
                                    pass
                                if (
                                    sum(len(x) for x in _buf) >= _FLUSH_CHARS
                                    or (time.time() - _last_flush) >= _FLUSH_MS
                                ):
                                    _flush_buffer()
                                    _last_flush = time.time()
                            except Exception:
                                pass

                        res = cli.chat_stream(
                            model=model,
                            prompt=effective_prompt,
                            temperature=temperature,
                            max_tokens=max_tokens,
                            system=system_message or None,
                            images=effective_images,
                            on_delta=_on_delta,
                        )
                        # Final flush for remaining buffered text
                        try:
                            _flush_buffer()
                        except Exception:
                            pass
                    # If streaming returned no result but we accumulated deltas, construct a response
                    if (not isinstance(res, dict)) or (
                        isinstance(res, dict)
                        and not (res.get("response") or res.get("text"))
                    ):
                        if _accum:
                            res = {"response": "".join(_accum)}
                    # Detect streaming failure and fallback to non-stream
                    if (not isinstance(res, dict)) or (
                        isinstance(res, dict)
                        and (res.get("error") or not res.get("response"))
                    ):
                        out_dict["stream_failed"] = True
                        try:
                            res2 = cli.chat(
                                model=model,
                                prompt=effective_prompt,
                                temperature=temperature,
                                max_tokens=max_tokens,
                                system=system_message or None,
                                images=effective_images,
                                stream=False,
                            )
                            res = res2
                        except Exception:
                            pass
                    out_dict["result"] = res

                    # --- Recursive generation for Ollama (streaming path) ---
                    _ollama_res_text = None
                    if isinstance(res, dict):
                        _ollama_res_text = res.get("response") or res.get("text") or ""
                    elif isinstance(res, str):
                        _ollama_res_text = res
                    if _ollama_res_text and len(_ollama_res_text) > 50 and use_consolidation:
                        _ollama_parts = [_ollama_res_text]
                        _ollama_recursive = True
                        _ollama_ri = 0
                        _ollama_base = effective_prompt
                        while _ollama_recursive and _ollama_ri < 3:
                            _ollama_ri += 1
                            _ollama_combined = "".join(_ollama_parts)

                            # --- Re-retrieve fresh context based on latest generated text ---
                            _ollama_fresh = ""
                            try:
                                _ollama_fresh = consolidator.retrieve_for_text(
                                    _ollama_combined, top_k=2
                                )
                            except NameError:
                                pass

                            _ollama_tail = _ollama_combined[-300:] if len(_ollama_combined) > 300 else _ollama_combined
                            _cont = _ollama_base
                            if _ollama_fresh:
                                _cont += f"\n\n[Fresh Context]:\n{_ollama_fresh}"
                            _cont += (
                                f"\n\n[Previous output (tail only):]\n"
                                f"...{_ollama_tail}\n\n"
                                f"[Continue. Write NEW content only. Never repeat or "
                                f"summarize previous output. If nothing new to add, "
                                f"output [DONE].]"
                            )
                            try:
                                _cr = cli.chat(
                                    model=model, prompt=_cont,
                                    temperature=temperature,
                                    max_tokens=max_tokens,
                                    system=system_message or None,
                                    stream=False,
                                )
                                _nt = (
                                    _cr.get("response") or _cr.get("text") or ""
                                ) if isinstance(_cr, dict) else (
                                    _cr if isinstance(_cr, str) else ""
                                )
                                if "[DONE]" in _nt:
                                    _nt = _nt.replace("[DONE]", "").strip()
                                    if _nt:
                                        _ollama_parts.append(_nt)
                                    break
                                if len(_nt) < 20:
                                    break
                                _ollama_parts.append(_nt)
                            except Exception:
                                break
                        # Clean up consolidator state for next chain node execution
                        try:
                            consolidator.reset_retrieved()
                        except (NameError, AttributeError):
                            pass
                        _ollama_full = "".join(_ollama_parts)
                        out_dict["result"] = {"response": _ollama_full}
                        logger.info(
                            f"Ollama generation complete: {len(_ollama_full)} chars "
                            f"({len(_ollama_parts)} recursive chunk(s))"
                        )
                else:
                    res = cli.chat(
                        model=model,
                        prompt=effective_prompt,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        system=system_message or None,
                        images=effective_images,
                        stream=False,
                    )
                    out_dict["result"] = res

                    # --- Recursive generation for Ollama (non-streaming) ---
                    _ollama_res_text2 = None
                    if isinstance(res, dict):
                        _ollama_res_text2 = res.get("response") or res.get("text") or ""
                    elif isinstance(res, str):
                        _ollama_res_text2 = res
                    if _ollama_res_text2 and len(_ollama_res_text2) > 50 and use_consolidation:
                        _ollama_parts2 = [_ollama_res_text2]
                        _ollama_recursive2 = True
                        _ollama_ri2 = 0
                        _ollama_base2 = effective_prompt
                        while _ollama_recursive2 and _ollama_ri2 < 3:
                            _ollama_ri2 += 1
                            _ollama_combined2 = "".join(_ollama_parts2)

                            # --- Re-retrieve fresh context based on latest generated text ---
                            _ollama_fresh2 = ""
                            try:
                                _ollama_fresh2 = consolidator.retrieve_for_text(
                                    _ollama_combined2, top_k=2
                                )
                            except NameError:
                                pass

                            _ollama_tail2 = _ollama_combined2[-300:] if len(_ollama_combined2) > 300 else _ollama_combined2
                            _cont2 = _ollama_base2
                            if _ollama_fresh2:
                                _cont2 += f"\n\n[Fresh Context]:\n{_ollama_fresh2}"
                            _cont2 += (
                                f"\n\n[Previous output (tail only):]\n"
                                f"...{_ollama_tail2}\n\n"
                                f"[Continue. Write NEW content only. Never repeat or "
                                f"summarize previous output. If nothing new to add, "
                                f"output [DONE].]"
                            )
                            try:
                                _cr2 = cli.chat(
                                    model=model, prompt=_cont2,
                                    temperature=temperature,
                                    max_tokens=max_tokens,
                                    system=system_message or None,
                                    stream=False,
                                )
                                _nt2 = (
                                    _cr2.get("response") or _cr2.get("text") or ""
                                ) if isinstance(_cr2, dict) else (
                                    _cr2 if isinstance(_cr2, str) else ""
                                )
                                if "[DONE]" in _nt2:
                                    _nt2 = _nt2.replace("[DONE]", "").strip()
                                    if _nt2:
                                        _ollama_parts2.append(_nt2)
                                    break
                                if len(_nt2) < 20:
                                    break
                                _ollama_parts2.append(_nt2)
                            except Exception:
                                break
                        # Clean up consolidator state for next chain node execution
                        try:
                            consolidator.reset_retrieved()
                        except (NameError, AttributeError):
                            pass
                        _ollama_full2 = "".join(_ollama_parts2)
                        out_dict["result"] = {"response": _ollama_full2}
                        logger.info(
                            f"Ollama generation complete: {len(_ollama_full2)} chars "
                            f"({len(_ollama_parts2)} recursive chunk(s))"
                        )
            except Exception as _e:
                logger.error(f"LLM task runner exception: {_e}", exc_info=True)
                out_dict["error"] = str(_e)
            finally:
                # Clear typing-active signal
                try:
                    if _node_id:
                        self.variables.pop(f"node_{_node_id}_writing", None)
                except Exception:
                    pass

                # Only stop Ollama models when using the Ollama engine.
                # When using llama.cpp, skip entirely — the model is managed
                # by the in-process llama.cpp engine, not Ollama.
                if not use_llamacpp:
                    try:
                        _rq.post(
                            f"{_base}/api/stop",
                            json={"model": model},
                            headers={"Connection": "close"},
                            timeout=3,
                        )
                    except Exception:
                        pass
                    try:

                        def _find_ollama_bin():
                            cands = []
                            for k in ("OLLAMA_BIN", "OLLAMA_EXE"):
                                v = os.getenv(k)
                                if v:
                                    cands.append(v)
                            la = os.getenv("LOCALAPPDATA")
                            if la:
                                cands.append(
                                    os.path.join(la, "Programs", "Ollama", "ollama.exe")
                                )
                            cands.append(r"C:\\Program Files\\Ollama\\ollama.exe")
                            cands.append("ollama")
                            return cands

                        env = os.environ.copy()
                        cflags = 0x08000000 if os.name == "nt" else 0
                        for cmd in _find_ollama_bin():
                            try:
                                _sp.run(
                                    [cmd, "stop", model],
                                    capture_output=True,
                                    text=True,
                                    timeout=4,
                                    env=env,
                                    creationflags=cflags,
                                )
                                break
                            except Exception:
                                continue
                    except Exception:
                        pass

                    try:
                        cli.close()
                    except Exception:
                        pass

        shared: Dict[str, Any] = {}
        # ── LLM request — tool arbitration always goes through the LLM ──
        # Embedding-based direct tool execution was removed (plan W1): the
        # ToolRetriever only RANKS candidates; the Router LLM always names the
        # tool.  The former auto-select block (embedding argmax > threshold
        # synthesizing USE_TOOL without ever calling the LLM) is deleted.
        logger.info("Sending request to LLM API in thread")
        th = threading.Thread(target=_task_runner, args=(shared,), daemon=True)
        th.start()

        # Disable request timeouts for local LLMs
        timeout_s = 0.0

        start_t = time.time()
        while th.is_alive():
            if stop_flag and stop_flag():
                logger.info(
                    "Stop flag detected. Attempting to stop LLM generation..."
                )
                try:
                    for _ in range(2):
                        _rq.post(
                            f"{_base}/api/stop",
                            json={"model": model},
                            headers={"Connection": "close"},
                            timeout=2,
                        )
                        time.sleep(0.1)
                except Exception:
                    pass
                break
            # No timeout: allow long-running local models to complete
            try:
                time.sleep(0.05)
            except Exception:
                break
        try:
            th.join(timeout=0.2)
        except Exception:
            pass
        result = shared.get("result")
        if result is None and shared.get("error"):
            logger.error(f"LLM request failed: {shared.get('error')}")
            # Transport/engine failure — NEVER fabricate a successful node
            # with an empty response (this previously converted every engine
            # error into {user_input, response:''} and a silent STALL).
            # Fail the node loudly so the chain terminates with a visible
            # FAILED reason instead.
            _cleanup_vision_images()
            if _direct_transport is not None:
                raise _direct_transport.LLMTransportError(
                    str(shared.get("error"))
                )
            raise RuntimeError(f"LLM request failed: {shared.get('error')}")
        # If no explicit result but streaming accumulated text exists, use it
        if result is None and isinstance(shared.get("stream_used"), bool):
            # Attempt to salvage from a typed buffer stored via variables
            try:
                # Prefer node-specific output if already captured by action handler
                node_id = (
                    llm_data.get("node_id") or llm_data.get("id") or node.get("id")
                )
                fallback_text = None
                if node_id:
                    fallback_text = self.variables.get(f"node_{node_id}_output")
                fallback_text = fallback_text or self.variables.get(output_variable)
                if fallback_text:
                    result = {"response": str(fallback_text)}
            except Exception:
                pass

        # Normalize response
        response_text = None
        if isinstance(result, dict):
            # Try various keys, and explicit dict lookups
            if "response" in result:
                response_text = result["response"]
            elif "text" in result:
                response_text = result["text"]
            elif "message" in result and isinstance(result["message"], dict):
                response_text = result["message"].get("content")

            # Convert non-string responses safely
            if response_text is not None and not isinstance(response_text, str):
                response_text = str(response_text)
        # If tool selection is expected, extract and sanitize output to the marker only
        selected_tool_id = None
        selected_tool_args = {}
        _invalid_tool_attempt = False
        if tool_ids and isinstance(response_text, str) and response_text:
            try:
                for marker in ("USE_TOOL:", "CALL_TOOL:", "TOOL:"):
                    idx = response_text.find(marker)
                    if idx >= 0:
                        rest = response_text[idx + len(marker):].strip()
                        # Extract tool ID (first whitespace-delimited token)
                        parts = rest.split(None, 1)
                        val = parts[0].strip() if parts else rest.strip()
                        # Separate potential JSON args from the ID token
                        json_candidate = ""
                        brace_pos = val.find("{")
                        if brace_pos >= 0:
                            json_candidate = val[brace_pos:]
                            val = val[:brace_pos]
                        # Clean trailing punctuation from ID
                        while val and val[-1] in (":", ",", ";", ".", ")", "]"):
                            val = val[:-1]
                        if val:
                            selected_tool_id = val
                            # Parse JSON args if present
                            if json_candidate:
                                try:
                                    selected_tool_args = json.loads(json_candidate)
                                    if not isinstance(selected_tool_args, dict):
                                        selected_tool_args = {}
                                except (json.JSONDecodeError, ValueError):
                                    selected_tool_args = {}
                            elif len(parts) > 1:
                                # JSON might be in the remainder after the ID token
                                remainder = parts[1].strip()
                                brace_pos2 = remainder.find("{")
                                if brace_pos2 >= 0:
                                    json_str = remainder[brace_pos2:]
                                    try:
                                        selected_tool_args = json.loads(json_str)
                                        if not isinstance(selected_tool_args, dict):
                                            selected_tool_args = {}
                                    except (json.JSONDecodeError, ValueError):
                                        selected_tool_args = {}
                            break
                # Resolve any alias token (<brave>, brave) the Router LLM used
                # instead of the raw hex id — alias map injected by llm_ops.
                try:
                    _amap = (llm_config.get('_tool_alias_map')
                             or llm_data.get('_tool_alias_map') or {})
                    if isinstance(_amap, str):
                        _amap = json.loads(_amap)
                except Exception:
                    _amap = {}
                if not isinstance(_amap, dict):
                    _amap = {}

                def _resolve_alias(_val):
                    return _resolve_tool_token(_val, tool_ids, _amap)

                # Accept bare tool id if model omitted marker
                if (not selected_tool_id) and (response_text.strip() in tool_ids):
                    selected_tool_id = response_text.strip()
                # Immediate alias normalization for the marker-parsed token
                if selected_tool_id:
                    _raw = _resolve_alias(selected_tool_id)
                    if _raw:
                        logger.info(
                            "Resolved alias %s → node %s (LLM output path)",
                            selected_tool_id, _raw,
                        )
                        selected_tool_id = _raw
                # Scan for any known tool id or alias token present anywhere
                if not selected_tool_id:
                    _scan_terms = list(tool_ids)
                    _scan_terms += list(_amap.keys())
                    _scan_terms += [str(_a).strip('<>') for _a in _amap.keys()]
                    # Longest terms first: '<CLOSE>' must win over 'CLOSE'
                    _scan_terms = sorted(set(_scan_terms), key=len, reverse=True)
                    for _term in _scan_terms:
                        if _term and str(_term) in response_text:
                            selected_tool_id = _resolve_alias(_term)
                            break
                # MODEL RE-SELECTION before guessing from description words:
                # every step above is exact, the token-overlap fallback below
                # is not (it takes the FIRST description sharing >=2 content
                # words), and a wrong pick costs the turn.  Reached only when
                # nothing resolved, so the happy path pays nothing.
                if not selected_tool_id and response_text:
                    _reselected = _reselect_tool_with_model(
                        input_text or _context_input(node, self.variables) or "",
                        tool_ids, tool_desc_map, _amap, response_text,
                        llm_config, api_url, _use_llamacpp_flag,
                    )
                    if _reselected:
                        selected_tool_id = _reselected
                # Scan by tool description keywords — token-level overlap
                # (≥2 content-word matches AND >20% of description tokens).
                # This replaces the old full-substring fallback which caused false
                # positives when descriptions contained common words like "window".
                if not selected_tool_id and isinstance(tool_desc_map, dict):
                    rt_lower = response_text.lower()
                    rt_tokens = set(rt_lower.split())
                    for tid, desc in tool_desc_map.items():
                        if not tid:
                            continue
                        d = str(desc or "")
                        if not d:
                            continue
                        desc_tokens = set(d.lower().split())
                        content_overlap = {t for t in (desc_tokens & rt_tokens) if len(t) > 3}
                        if len(content_overlap) >= 2 and len(content_overlap) > len(desc_tokens) * 0.2:
                            selected_tool_id = tid
                            break
                # Normalize any leftover alias token before validity check
                if selected_tool_id:
                    _raw = _resolve_alias(selected_tool_id)
                    if _raw:
                        selected_tool_id = _raw
                if selected_tool_id and (selected_tool_id in tool_ids):
                    response_text = f"USE_TOOL:{selected_tool_id}"
                elif selected_tool_id:
                    # The model emitted a tool token that resolves to NO
                    # connected tool (hallucinated id / unknown alias).  This is
                    # NOT a tool call: drop the selection, neutralize the reply
                    # when it is nothing but the bogus call, and let the node
                    # finish naturally — never route to a ghost tool and never
                    # report a tool as selected.
                    _invalid_tool_attempt = True
                    logger.warning(
                        "Tool id %r does not match any connected tool — "
                        "treated as natural reply, no tool executed",
                        selected_tool_id,
                    )
                    _rt = str(response_text or "").strip()
                    _rt_up = _rt.upper()
                    if (
                        _rt_up.startswith(("USE_TOOL:", "CALL_TOOL:", "TOOL:"))
                        and "\n" not in _rt
                        and " " not in _rt
                    ):
                        response_text = ""
                    selected_tool_id = None
            except Exception:
                pass
        # Semantic fallback based on previous/context input when no explicit selection detected
        if (not selected_tool_id) and tool_ids and not _invalid_tool_attempt:
            try:
                sctx = input_text or _context_input(node, self.variables) or ""
                sl = str(sctx).lower()
                want_open = False
                want_close = False
                neg_indicators = (
                    "no browser",
                    "not open",
                    "closed",
                    "no visible",
                    "none open",
                )
                pos_indicators = (
                    "browser is open",
                    "is open",
                    "open window",
                    "visible browser",
                )
                for w in neg_indicators:
                    if w in sl:
                        want_open = True
                        break
                if not want_open:
                    for w in pos_indicators:
                        if w in sl:
                            want_close = True
                            break
                # Prefer mapping via descriptions first
                if want_open and isinstance(tool_desc_map, dict):
                    for tid in tool_ids:
                        desc = str(tool_desc_map.get(tid, "")).lower()
                        if (
                            ("open_browser" in desc)
                            or ("open browser" in desc)
                            or (desc.endswith("open_browser"))
                        ):
                            selected_tool_id = tid
                            break
                if (
                    (not selected_tool_id)
                    and want_close
                    and isinstance(tool_desc_map, dict)
                ):
                    for tid in tool_ids:
                        desc = str(tool_desc_map.get(tid, "")).lower()
                        if (
                            ("close_browser" in desc)
                            or ("close browser" in desc)
                            or (desc.endswith("close_browser"))
                        ):
                            selected_tool_id = tid
                            break
                # Fallback by simple name heuristics if descriptions are missing
                if not selected_tool_id:
                    if want_open:
                        for tid in tool_ids:
                            nm = str(tool_desc_map.get(tid, "")).lower()
                            if "open" in nm and "close" not in nm:
                                selected_tool_id = tid
                                break
                    elif want_close:
                        for tid in tool_ids:
                            nm = str(tool_desc_map.get(tid, "")).lower()
                            if "close" in nm and "open" not in nm:
                                selected_tool_id = tid
                                break
                # Task 6: Resolve alias IDs (<brave>, <CLOSE>, etc.) to hex node IDs
                if selected_tool_id and selected_tool_id.startswith('<'):
                    alias_desc = tool_desc_map.get(selected_tool_id, '')
                    for tid in tool_ids:
                        if (not str(tid).startswith('<')
                            and alias_desc
                            and tool_desc_map.get(tid, '') == alias_desc):
                            logger.info(
                                "Resolved alias %s → node %s (semantic fallback path)",
                                selected_tool_id, tid,
                            )
                            selected_tool_id = tid
                            break
                if selected_tool_id and (selected_tool_id in tool_ids):
                    response_text = f"USE_TOOL:{selected_tool_id}"
            except Exception:
                pass
        if (
            is_form_filling_preset
            and (not tool_ids)
            and isinstance(response_text, str)
            and response_text
        ):
            try:
                import re as _re

                def _sanitize_action_commands(t: str) -> str:
                    raw = (t or "").strip()
                    if not raw:
                        return ""
                    m_done = _re.search(r"\bDONE\b", raw, flags=_re.IGNORECASE)
                    if m_done:
                        return "DONE"

                    patt = _re.compile(
                        r"(CLICK:\s*\d+\s*,\s*\d+|TYPE:\s*[^\r\n]+|SCROLL:\s*-?\d+|WAIT:\s*\d+(?:\.\d+)?)",
                        flags=_re.IGNORECASE,
                    )
                    matches = [(m.start(), m.group(1)) for m in patt.finditer(raw)]
                    if not matches:
                        lines = []
                        for ln in raw.splitlines():
                            ln = ln.strip()
                            if ln:
                                lines.append(ln)
                        return "\n".join(lines[:3])

                    cmds = []
                    for _, s in sorted(matches, key=lambda x: x[0]):
                        s2 = s.strip()
                        up = s2.upper()
                        if up.startswith("CLICK:"):
                            s2 = "CLICK:" + s2.split(":", 1)[1]
                        elif up.startswith("TYPE:"):
                            s2 = "TYPE:" + s2.split(":", 1)[1]
                        elif up.startswith("SCROLL:"):
                            s2 = "SCROLL:" + s2.split(":", 1)[1]
                        elif up.startswith("WAIT:"):
                            s2 = "WAIT:" + s2.split(":", 1)[1]
                        cmds.append(s2.strip())

                    final = []
                    saw_click = False
                    saw_type = False
                    # Allow multiple clicks/types/scrolls in a plan (up to 10)
                    for c in cmds:
                        cup = c.upper()
                        if (
                            cup.startswith("CLICK:")
                            or cup.startswith("TYPE:")
                            or cup.startswith("SCROLL:")
                            or cup.startswith("WAIT:")
                        ):
                            final.append(c)

                        # Keep it to a reasonable plan size
                        if len(final) >= 10:
                            break

                    return "\n".join(final).strip()

                response_text = _sanitize_action_commands(response_text)
            except Exception:
                pass
        if not response_text:
            _debug_info = {}
            _usage = {}
            if isinstance(result, dict):
                _debug_info = result.get("_debug", {})
                _usage = result.get("usage", {})
                # A result dict carrying an error is a transport failure —
                # never treat it as a successful empty completion.
                if result.get("error"):
                    if _direct_transport is not None:
                        raise _direct_transport.LLMTransportError(
                            str(result["error"])
                        )
                    raise RuntimeError(f"LLM request failed: {result['error']}")
            # Genuinely empty-but-successful model output: proceed with empty
            # text, but log a prominent WARNING so the empty completion is
            # never mistaken for a silent transport failure.
            logger.warning(
                f"LLM node {_node_id} returned EMPTY text (model={model}) — "
                f"continuing with empty output: {result}"
            )
            if _debug_info:
                logger.warning(
                    f"  API _debug: images_received={_debug_info.get('images_received')}, "
                    f"images_processed={_debug_info.get('images_processed')}, "
                    f"multimodal_messages={_debug_info.get('multimodal_messages')}"
                )
            if _usage:
                logger.warning(
                    f"  API usage: prompt_tokens={_usage.get('prompt_tokens')}, "
                    f"completion_tokens={_usage.get('completion_tokens')}"
                )
            # Proceed with empty text to allow chaining (guarded empty-success)
            response_text = ""

        log_block(
            logger, logging.INFO,
            f"LLM Response ({len(response_text or '')} chars)", response_text or "",
        )

        # Strip think tags from response before storing as output
        try:
            response_text = _strip_think_tags(response_text)
        except Exception:
            pass

        # A routing command ('USE_TOOL:<id>') is execution metadata: it was
        # consumed by the tool dispatch above and must never surface in the
        # node's outputs.  Publish the selected tool's readable description
        # instead, so the overlay, output nodes and context turns read a
        # clear record of which tool ran — never a raw routing key.
        _publish_text = response_text
        if selected_tool_id:
            try:
                _publish_text = _tool_selection_text(
                    selected_tool_id, tool_desc_map, selected_tool_args,
                )
            except Exception:
                _publish_text = response_text

        # Store outputs and artifact metadata
        try:
            self.variables[output_variable] = _publish_text
            node_id = llm_data.get("node_id") or llm_data.get("id") or node.get("id")
            if node_id:
                self.variables[f"node_{node_id}_output"] = _publish_text
                # ── Task 1/2: Structured context with user_input + response ──
                try:
                    import json as _json
                    import re as _re
                    _input_for_ctx = self.variables.get(f"node_{node_id}_source_input", "") or ""
                    _ctx_dict = {
                        "user_input": str(_input_for_ctx),
                        "response": str(_publish_text or ""),
                    }
                    # Enrich with tool id + description when response is a USE_TOOL command
                    # so downstream LLMs have actual context about which tool was selected
                    try:
                        _tool_match = _re.search(r'USE_TOOL:\s*(\S+)', str(response_text or ""))
                        if _tool_match:
                            _tid = _tool_match.group(1).strip()
                            # Only surface a tool in the context payload when the
                            # token resolved to a real connected tool — a bogus /
                            # hallucinated id must not make the overlay claim a
                            # tool was selected.
                            if _tid in tool_ids:
                                _ctx_dict["tool_id"] = _tid
                                _ctx_dict["tool_description"] = (
                                    tool_desc_map.get(_tid, "") or ""
                                )
                    except Exception:
                        pass
                    _ctx_payload = _json.dumps(_ctx_dict, ensure_ascii=False)
                    self.variables[f"node_{node_id}_context"] = _ctx_payload
                except Exception:
                    self.variables[f"node_{node_id}_context"] = _publish_text or ""

                # ── Unified input_context for all node types (Task 2) ──
                try:
                    _label = llm_data.get("name") or llm_data.get("label") or ""
                    _inp_ctx = {
                        "type": "llm",
                        "input": _input_for_ctx,
                        "output": _publish_text or "",
                        "label": _label,
                    }
                    self.variables[f"node_{node_id}_input_context"] = _inp_ctx
                except Exception:
                    pass
        except Exception:
            pass

        # Optional: write text via handlers when not streaming
        try:
            # Check if streaming was actually used.
            # We rely on the runtime flag 'stream_used' from the task runner.
            was_streamed = shared.get("stream_used", False)

            # --- Parse and execute Form Filling Preset Actions ---
            form_action_taken = False
            if is_form_filling_preset and action_handlers and response_text:
                logger.info("Parsing LLM response for Form Filling actions...")
                lines = response_text.split("\n")
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue

                    if stop_flag and stop_flag():
                        break

                    try:
                        if line.startswith("CLICK:"):
                            coords = line[6:].strip().split(",")
                            if len(coords) >= 2:
                                x = int(float(coords[0].strip()))
                                y = int(float(coords[1].strip()))
                                action_handlers.bot.human_mouse_move(x, y)
                                action_handlers.bot.human_click(button="left")
                                logger.info(f"LLM clicked at {x}, {y}")
                                form_action_taken = True
                        elif line.startswith("TYPE:"):
                            text = line[5:].strip()
                            action = {
                                "text": text,
                                "force_typing": True,
                                "batch_size": 5,
                                "batch_delay": 0.01,
                                "char_by_char": False,
                                "keystroke_delay": 0.001,
                            }
                            action_handlers.handle_type_string_action(
                                0, action, stop_flag
                            )
                            form_action_taken = True
                        elif line.startswith("SCROLL:"):
                            delta = int(float(line[7:].strip()))
                            action = {
                                "total_delta": delta,
                                "steps": max(1, abs(delta) // 100),
                                "duration_sec": 0.2,
                            }
                            action_handlers.handle_scroll_action(0, action, stop_flag)
                            form_action_taken = True
                        elif line.startswith("WAIT:"):
                            delay = float(line[5:].strip())
                            time.sleep(delay)
                            form_action_taken = True
                    except Exception as e:
                        logger.warning(
                            f"Failed to execute form filling action '{line}': {e}"
                        )
            # --- Standard text writing ---
            elif (
                write_text
                and action_handlers
                and hasattr(action_handlers, "handle_type_string_action")
                and response_text
                and (not was_streamed or bool(shared.get("stream_failed")))
            ):
                logger.info("Writing LLM output via action handlers")

                # Prevent repetition if streaming partially succeeded but failed at the end
                text_to_write = response_text
                if bool(shared.get("stream_failed")) and bool(
                    llm_config.get("stream", True)
                ):
                    accumulated = shared.get("_accum_text", "")
                    if accumulated and text_to_write.startswith(accumulated):
                        text_to_write = text_to_write[len(accumulated) :]
                        logger.info(
                            f"Skipping {len(accumulated)} chars already typed during partial stream"
                        )
                    elif accumulated:
                        # Fallback: if not a clean prefix, we might have a conflict.
                        # For now, we write the full text to ensure correctness,
                        # or we could try to find overlap.
                        # Given the user complaint about repetition, let's try to be smart.
                        # If the new text is just a continuation, we might see it.
                        pass

                if text_to_write:
                    write_mode = str(llm_config.get("write_mode") or "type").lower()
                    if write_mode == "paste":
                        try:
                            node_id = (
                                llm_data.get("node_id")
                                or llm_data.get("id")
                                or node.get("id")
                            )
                            if node_id:
                                self.variables[f"node_{node_id}_writing"] = True
                        except Exception:
                            pass
                        action = {"text": text_to_write, "use_clipboard": True}
                    else:
                        action = {
                            "text": text_to_write,
                            "force_typing": True,
                            "batch_size": int(
                                llm_config.get("typing_batch_size", 5) or 5
                            ),
                            "batch_delay": float(
                                llm_config.get("typing_batch_delay", 0.01) or 0.01
                            ),
                            "char_by_char": bool(llm_config.get("char_by_char", False)),
                            "keystroke_delay": float(
                                llm_config.get("keystroke_delay", 0.001) or 0.001
                            ),
                        }
                    _write_chunk(text_to_write, action)
                    try:
                        node_id = (
                            llm_data.get("node_id")
                            or llm_data.get("id")
                            or node.get("id")
                        )
                        if node_id:
                            self.variables.pop(f"node_{node_id}_writing", None)
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"Failed to write text via action handlers: {e}")

        # Determine next node
        next_node = llm_data.get("next_node") or node.get("next")
        if not next_node:
            try:
                connections = node.get("connections", {})
                if isinstance(connections, dict):
                    output_conns = connections.get("output", [])
                    if output_conns:
                        next_node = output_conns[0].get("node_id")
                elif isinstance(connections, list) and connections:
                    # Handle list-style connections from chain JSON
                    next_node = connections[0].get("target_node_id") or connections[
                        0
                    ].get("node_id")
            except Exception:
                pass
        try:
            if tool_ids and isinstance(response_text, str):
                sel = selected_tool_id
                # Resolve alias IDs (<brave>, brave) to raw node ids via the
                # injected alias map so the caller can look up the tool in the
                # workflow graph.
                try:
                    _am2 = (llm_config.get('_tool_alias_map')
                            or llm_data.get('_tool_alias_map') or {})
                    if isinstance(_am2, str):
                        _am2 = json.loads(_am2)
                except Exception:
                    _am2 = {}
                if sel and sel not in tool_ids and isinstance(_am2, dict):
                    _raw2 = _am2.get(str(sel)) or _am2.get(
                        f"<{str(sel).strip('<>')}>"
                    )
                    if not _raw2:
                        _bare2 = str(sel).strip('<>')
                        for _a, _tid in _am2.items():
                            if _tid in tool_ids and _a.strip('<>') == _bare2:
                                _raw2 = _tid
                                break
                    if _raw2 and _raw2 in tool_ids:
                        logger.info(
                            "Resolved alias %s → node %s (return path)",
                            sel, _raw2,
                        )
                        sel = _raw2
                if sel and (sel in tool_ids):
                    # Decompose composite MCP tool ID: {node_id}__{tool_name}
                    _routing_id = sel
                    if sel and '__' in str(sel):
                        _parts = str(sel).split('__', 1)
                        _routing_id = _parts[0]
                        # Inject tool_name into args so MCP executor can use it
                        if _parts[1] and isinstance(selected_tool_args, dict) and 'tool_name' not in selected_tool_args:
                            selected_tool_args['tool_name'] = _parts[1]
                            logger.info("[MCP] Decomposed composite tool ID %s \u2192 node=%s tool=%s", sel, _routing_id, _parts[1])
                    next_node = _routing_id
                    # Store extracted tool arguments for the executor to pick up
                    try:
                        self.variables[f"_tool_args_{_routing_id}"] = selected_tool_args
                        if selected_tool_args:
                            logger.info(f"Tool arguments for {_routing_id}: {selected_tool_args}")
                    except Exception:
                        pass
                    logger.info(f"Tool selected by LLM: {sel} (routing to {_routing_id})")
                    try:
                        desc = tool_desc_map.get(sel, "")
                        log_block(
                            logger, logging.INFO,
                            "Tool Selected", f"{sel}: {desc or '(no description)'}",
                        )
                    except Exception:
                        pass
                else:
                    # No valid tool selected: the LLM answered naturally, or the
                    # marker token was unresolvable (already neutralized above).
                    # Return '__done__' for tool-enabled nodes so the atomic loop
                    # ends cleanly instead of mistaking the node's output
                    # connection for a "selected tool".
                    if _invalid_tool_attempt:
                        try:
                            logger.warning(
                                "Invalid tool call handled — node finishing "
                                "without executing a tool"
                            )
                        except Exception:
                            pass
                    else:
                        try:
                            logger.warning(
                                "No tool selection marker found in response"
                            )
                        except Exception:
                            pass
                    next_node = "__done__"
        except Exception:
            pass

        # Ensure a non-None return to indicate successful execution to the workflow engine
        if not next_node:
            next_node = "__done__"

        # Adversarial loop for form filling: verify each step recursively
        if (
            is_form_filling_preset
            and "form_action_taken" in locals()
            and form_action_taken
        ):
            _node_id = llm_data.get("node_id") or llm_data.get("id") or node.get("id")

            # Keep track of loop count to prevent infinite loops
            loop_key = f"form_fill_loop_{_node_id}"
            loop_count = self.variables.get(loop_key, 0)

            if loop_count < 10:
                self.variables[loop_key] = loop_count + 1
                logger.info(
                    f"Form action taken (Step {loop_count + 1}/10). Looping back to same node for recursive verification."
                )

                # Force a fresh screen capture instead of using stale context
                self.variables[f"node_{_node_id}_force_refresh"] = True

                if _node_id:
                    next_node = _node_id
            else:
                logger.warning(
                    f"Form filling reached maximum adversarial loop limit (10 steps) for node {_node_id}. Forcing progression."
                )
                self.variables[loop_key] = 0  # reset for future executions

        # The consolidation fact variables are per-node execution artifacts:
        # clear them when the node finally ends its process so they never
        # leak into later runs/turns (facts are variables, but only for the
        # duration of this node's execution).
        try:
            _fnid = llm_data.get("node_id") or llm_data.get("id") or node.get("id")
            if _fnid:
                self.variables.pop(f"node_{_fnid}_fact_pool", None)
                self.variables.pop(f"node_{_fnid}_consolidated_facts", None)
                logger.info(
                    "Consolidation: cleared fact variables for node %s", _fnid,
                )
        except Exception:
            pass

        logger.info(f"LLM node execution complete. Next node: {next_node}")
        _cleanup_vision_images()
        return next_node

    # --- Variable helpers for compatibility ---
    def get_variable(self, name: str, default: Any = None) -> Any:
        try:
            return self.variables.get(name, default)
        except Exception:
            return default

    def set_variable(self, name: str, value: Any) -> None:
        try:
            self.variables[name] = value
        except Exception:
            pass

    def get_all_variables(self) -> Dict[str, Any]:
        try:
            return dict(self.variables)
        except Exception:
            return {}
