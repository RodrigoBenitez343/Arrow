"""comorag_engine.py — Embedding-Based Context Retrieval for LLM Nodes.

Inspired by ComoRAG (AAAI 2026): "A Cognitive-Inspired Memory-Organized RAG
for Stateful Long Narrative Reasoning".

When an LLM node has access to large context sources, instead of injecting raw
text into the prompt, this engine treats the embedding space as a detached
sparse attention layer:

  1. INTENT: Compares the user prompt with the docs BY SIMILARITY (top-N
     chunks closest to the goal), composes an ENHANCED INTENT prompt:
     user goal + system prompt + that grounded context
  2. PROBE: Derives TARGETED probing questions from the enhanced intent
     and validates them against the available context (a probe that
     rephrases the request or invents terms is rejected); the intent is
     re-enhanced each cycle with the accumulated facts
  3. CHUNK + EMBED: Splits context into chunks, embeds into vectors (keys)
  4. RETRIEVE: Embeds each probe (query), retrieves top-k chunks via cosine sim
  5. SYNTHESIZE: The final response is generated from the accumulated
     facts while staying anchored to the enhanced intent and grounded on
     the VERBATIM source chunks

The embedding vectors are the attention mechanism — retrieval selects the
evidence; the probes derive from grounded intent so retrieval stays on-topic
for small models.

GROUNDING CONTRACT — every stage is VERIFIED, not merely retrieved:
  1. RETRIEVAL FAILS CLOSED.  A chunk becomes evidence only when it shares a
     content token with the probe AND wins the cosine ranking.  There is no
     document-order sweep and no unranked text: a probe that matches nothing
     contributes NO evidence and its cycle is reported as invalid.
  2. FACTS ARE TRACEABLE.  A composed finding is pooled only when its
     Support quote occurs in that probe's evidence AND its claim asserts
     nothing the evidence lacks (``grounding_violations``).  A rejected
     finding falls back to the retrieved chunk, so the CLUE it was built on
     survives the fabrication.
  3. THE OUTPUT IS VERIFIED.  The judge's draft and the final synthesis are
     checked against the evidence; an ungrounded synthesis is discarded and
     the grounded facts are returned in its place.
  4. OFF-TOPIC FACTS ARE PURGED ONCE, at the final synthesis (the relevance
     verdict) — never per cycle, so the sweep accumulates freely and the
     curation verdict cannot steer a probe.
  5. AUDITABLE.  Each cycle reports kept / rejected counts, so a run says
     what it grounded instead of only what it retrieved.

Diagnostics:
  - Embedding failure -> EmbeddingFailureError is raised: a HARD BREAK that
    stops chain execution so the cause is visible (never keyword-ranked raw
    text — small LLM context windows must stay clean for the instruction)
  - Other errors -> silent skip of context
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from collections import OrderedDict
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# The boxy renderers every node logs through (Rich panel + Rich table, Markdown
# fence and raw text in the other sinks): the consolidation trace is a section
# of a node's output, so it must read like the rest of it.
try:
    from logging_setup import log_block, log_table
except Exception:  # pragma: no cover - bare/relative import context
    def log_block(logger, level, title, content, lang="text"):
        text = content if isinstance(content, str) else repr(content)
        logger.log(level, "=== %s ===\n%s", title, text)

    def log_table(logger, level, title, rows):
        if not rows:
            return
        logger.log(
            level, "=== %s ===\n%s", title,
            "\n".join(f"{k}: {v}" for k, v in rows),
        )


class EmbeddingFailureError(RuntimeError):
    """Raised when embedding-based retrieval cannot produce context.

    A hard chain-stopping signal: embeddings are the ONLY retrieval path
    (no lexical fallback), so their failure means context cannot be
    provided at all.  The message carries the diagnostic reason so the
    failure is visible in the overlay/logs.
    """


# Function words (>=5 chars) ignored when validating that a probe uses
# only terms present in the available context.
_STOPWORDS = frozenset({
    "about", "above", "after", "again", "being", "below", "could",
    "does", "doing", "every", "first", "from", "going", "have",
    "into", "just", "might", "more", "most", "only", "other",
    "over", "some", "such", "than", "that", "their", "them",
    "then", "there", "these", "they", "this", "those", "under",
    "very", "were", "what", "when", "where", "which", "while",
    "will", "with", "would", "your", "person", "people", "work",
    "using", "used", "does", "need", "needs", "make", "made",
    # Adverbs/temporal fillers a small model adds when rephrasing a
    # goal-derived probe — never hallucinated entities, so they must not
    # trip the anti-hallucination check (observed: "currently" rejecting
    # "What is the user currently doing on screen?" and chaining failures).
    "currently", "recently", "usually", "typically", "actually",
    "probably", "possibly", "generally", "really", "simply",
    "sometimes", "today",
})

# Common English content words (>=5 chars) a probe may legitimately use
# even when the available context never spells them out — terse OCR screen
# text, compact doc metadata, or code output rarely contain generic words
# like "current", "displayed" or "system".  Those are ordinary probe
# vocabulary, NOT hallucinated entities: only rare or entity-like terms
# must be grounded verbatim in the context (observed: "current"/"displayed"
# rejecting the valid probe "What is the current time displayed on the
# system message?" and chaining 4 failures that skipped consolidation).
_COMMON_WORDS = frozenset({
    # Descriptive adjectives
    "current", "displayed", "shown", "visible", "active", "available",
    "specific", "particular", "general", "overall", "primary", "various",
    "different", "certain", "additional", "following", "mentioned",
    "described", "related", "relevant", "correct", "expected", "actual",
    "recent", "latest", "previous", "complete", "entire", "partial",
    "simple", "complex", "basic", "advanced", "automatic", "possible",
    "likely", "apparent", "clear", "important", "necessary", "required",
    "missing", "internal", "external", "digital", "physical", "selected",
    "highlighted", "corresponding", "appropriate", "available",
    # Verbs
    "display", "appears", "appear", "interacting", "interaction",
    "indicate", "indicates", "meaning", "means", "contain", "contains",
    "include", "includes", "providing", "provide", "provides", "describe",
    "mentions", "mention", "refer", "refers", "represent", "represents",
    "suggest", "suggests", "showing", "stating", "states", "stated",
    "telling", "says", "said", "shows", "works", "working", "operates",
    "operation", "running", "using", "asking", "looks", "looking",
    "finding", "found", "located", "location", "presents", "happen",
    "happens", "happening", "occurs", "occurring", "populated",
    "generated", "entered", "written", "printed", "rendered", "opened",
    "closed", "maximized", "minimized", "pressed", "clicked", "typed",
    "contains", "discusses", "explains", "describes",
    # Screen/UI/system nouns
    "system", "systems", "message", "messages", "screen", "window",
    "windows", "terminal", "console", "desktop", "interface",
    "application", "applications", "software", "program", "programs",
    "document", "documents", "folder", "folders", "directory",
    "directories", "page", "pages", "button", "buttons", "icon", "icons",
    "menu", "menus", "option", "options", "setting", "settings",
    "feature", "features", "function", "functions", "action", "actions",
    "task", "tasks", "process", "processes", "information", "content",
    "contents", "context", "section", "sections", "detail", "details",
    "item", "items", "entry", "entries", "field", "fields", "value",
    "values", "number", "numbers", "string", "strings", "label",
    "labels", "title", "titles", "status", "state", "states", "result",
    "results", "output", "outputs", "input", "inputs", "response",
    "responses", "question", "questions", "answer", "answers", "example",
    "examples", "method", "methods", "approach", "approaches", "purpose",
    "purposes", "goal", "goals", "company", "companies", "product",
    "products", "service", "services", "customer", "customers", "client",
    "clients", "manager", "managers", "employee", "employees", "worker",
    "workers", "role", "roles", "minute", "minutes", "second", "seconds",
    "month", "months", "version", "versions", "release", "releases",
    "update", "updates", "change", "changes", "support", "supports",
    "requirement", "requirements", "capability", "capabilities",
    "performance", "security", "access", "account", "accounts",
    "password", "passwords", "username", "usernames", "address",
    "addresses", "picture", "pictures", "image", "images", "thumbnail",
    "thumbnails", "header", "headers", "footer", "footers", "sidebar",
    "toolbar", "toolbars", "notification", "notifications", "overlay",
    "overlays", "dialog", "dialogs", "prompt", "prompts", "content",
})


def _normalize_probe(raw: Any) -> str:
    """Extract the actual probe question from a raw generator output.

    Small models sometimes wrap the probe in the intent-plan's structured
    format (``{"user_query": "...", "expected_answer": "..."}``) or in
    markdown code fences.  The wrapper is NOT a probe: validate and embed
    the inner question, never the JSON blob — its field names would be
    flagged as invented terms and pollute the retrieval embedding.
    """
    _s = str(raw or "").strip()
    if not _s:
        return _s
    # Strip ```json ... ``` / ``` ... ``` fences.
    if _s.startswith("```"):
        _s = re.sub(r"^```[a-zA-Z0-9]*\s*", "", _s)
        _s = re.sub(r"\s*```$", "", _s).strip()
    if _s.startswith("{"):
        try:
            _data = json.loads(_s)
            if isinstance(_data, dict):
                for _k in ("user_query", "probe", "query", "question"):
                    _v = _data.get(_k)
                    if isinstance(_v, str) and _v.strip():
                        return _v.strip()
        except Exception:
            pass
        # Truncated/unbalanced JSON (the log only keeps the head): pull the
        # query out by pattern instead of failing the whole probe.
        _m = re.search(r'"user_query"\s*:\s*"([^"]+)"', _s)
        if _m:
            return _m.group(1).strip()
    return _s


# Interrogative sentence starters.  A truncated probe that begins with one
# of these is still a question even when the generator ran out of tokens
# before the '?' (observed: 64-token probe budget cutting "...potential
# employer based on co" mid-question and the '?' rule rejecting it).
_INTERROGATIVE_STARTS = (
    "who", "what", "which", "whose", "whom", "when", "where",
    "why", "how", "is", "are", "was", "were", "does", "do", "did",
    "will", "would", "can", "could", "should", "shall", "may",
    "might", "has", "have", "had", "am",
)


def _starts_interrogative(text: Any) -> bool:
    """True when *text* begins like a question (interrogative + word
    boundary) and is long enough to be a real probe, not a fragment."""
    low = str(text or "").lstrip().lower()
    for w in _INTERROGATIVE_STARTS:
        if low.startswith(w) and (
            len(low) == len(w) or not low[len(w)].isalpha()
        ):
            return len(low) >= 12
    return False


# The judge's verdict line as models actually emit it — markdown-bolded,
# headed or bulleted ("**ANSWER:** x", "## MISSING: y").  A bare
# ``startswith`` missed every decorated form, so a real verdict was silently
# discarded as unparseable.
_JUDGE_LABEL_RE = re.compile(
    r"^[\s\*\-#>•]*(answer|missing)\s*[\*\s]*:", re.IGNORECASE,
)


_INTENT_PLAN_SECTIONS = ("INTENT SUMMARY", "SEARCH PLAN", "EXPECTED ANSWER SHAPE")


def _is_valid_intent_plan(plan: Any) -> bool:
    """True when *plan* has at least one section label the planner was asked for.

    Without this guard a malformed reply became the base for EVERY probe AND
    the vocabulary of the relevance gate: observed, the "plan" came back as a
    Python function, so probes derived from code identifiers and correct
    findings were dropped as off-topic.  A PARTIAL plan is legitimate (the
    generator only needs the SEARCH PLAN items), so ONE label is enough — what
    must be rejected is prose/code with no plan structure at all.
    """
    text = str(plan or "").upper()
    return any(s in text for s in _INTENT_PLAN_SECTIONS)


def _parse_judge_verdict(text: Any) -> Tuple[Optional[str], str]:
    """Parse a sufficiency-judge verdict: 'ANSWER: <draft>' or
    'MISSING: <gap>'.

    Returns ``(kind, payload)`` where kind is ``"answer"``/``"missing"``
    or ``None`` when unparseable — probing then continues unchanged, which is
    the fail-closed direction (an unreadable verdict is NOT an answer).
    Payload is capped so a runaway gap never floods the next probe prompt.
    """
    _lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
    # Stop-RAG-style verdicts carry an "Analysis:" line before the label, so
    # scan a few more lines; the label is anchored at LINE START, so prose
    # cannot fake one.
    for _line in _lines[:6]:
        _m = _JUDGE_LABEL_RE.match(_line)
        if _m:
            # The closing decoration of a bolded label sits AFTER the colon
            # ("**ANSWER:** 42"), so it is stripped off the payload too — a
            # verdict must never be discarded over its formatting.
            _payload = _line[_m.end():].strip().strip("*_`# ").strip()
            return _m.group(1).lower(), _payload[:220]
    return None, ""


def _coerce_probes(raw: Any, limit: int = 3) -> List[str]:
    """Normalize a probe-generator result into up to *limit* distinct probes.

    ComoRAG derives up to 3 NON-OVERLAPPING retrieval probes per cycle
    (entity-priority: distinct people/objects/places the request touches) so
    one cycle sweeps several evidence paths instead of a single one — the
    mechanism behind its recall gain.  Generators may return a list, a JSON
    object (``{"probe_1": ...}``), or a plain/multi-line string; the inner
    QUESTIONS are extracted and de-duplicated.  One question in, one probe
    out — the single-probe contract is unchanged.
    """
    _items: List[Any]
    if isinstance(raw, (list, tuple)):
        _items = list(raw)
    elif isinstance(raw, dict):
        _items = [
            v for k, v in sorted(raw.items())
            if str(k).startswith("probe")
        ] or list(raw.values())
    elif raw is not None:
        _items = [raw]
    else:
        _items = []
    out: List[str] = []
    for item in _items:
        _s = _normalize_probe(item)
        if not _s:
            continue
        # A multi-line generation carries one question per line (the probe
        # prompts ask for one per line): keep the QUESTION lines so trailing
        # commentary or a reflection preamble never becomes a probe.
        _lines = [ln.strip() for ln in _s.splitlines() if ln.strip()]
        _qs = [
            ln for ln in _lines
            if ln.endswith("?") or _starts_interrogative(ln)
        ]
        for _c in (_qs or _lines):
            _c = _c[:200].strip()
            if _c and _c not in out:
                out.append(_c)
            if len(out) >= max(1, int(limit)):
                return out
    return out


def _plan_focus_tokens(plan: Any) -> "set[str]":
    """The SPECIFIC terms the intent plan set out to find (SEARCH PLAN).

    Relevance vocabulary for composed findings: a finding sharing no
    specific term with what the pipeline set out to find is off-topic.  A
    resume's "date of birth" for a probe asking for a city is grounded (its
    quote is verbatim) yet worthless — and it satisfied the per-cycle fact
    quota on every cycle, so the loop never saturated and the pool shipped
    resume trivia as distilled facts.  Generic vocabulary (_COMMON_WORDS) is
    excluded, so the match needs a real entity/attribute rather than
    "application"/"details".
    """
    _s = str(plan or "")
    if not _s:
        return set()
    _m = re.search(
        r"SEARCH\s+PLAN\s*:?\s*(.*?)(?:\n\s*\n|\n[A-Z][A-Z ]{3,}:|$)",
        _s, re.DOTALL | re.IGNORECASE,
    )
    if not _m:
        return set()
    return {
        t for t in _lex_tokens(_m.group(1)) if t not in _COMMON_WORDS
    }


# ── Local embedding via sentence-transformers (already a dependency) ──
# Lazy-loaded so the module can be imported without triggering model download.
_EMBED_MODEL = None
_EMBED_AVAILABLE = False


def _bundled_embedding_path(model_name: str = "all-MiniLM-L6-v2") -> Optional[str]:
    """Locate a bundled sentence-transformers model directory.

    Direct engine mode resolves the model from the bundle
    (exe_dir/models/<name>, _MEIPASS/LoOper/AI/models/<name>, ...) so the
    frozen agent never touches huggingface.co.  Returns None when not
    bundled — callers must NOT fall back to a network download.
    """
    base = os.path.basename(model_name)
    candidates = []
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
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            for sub in ("", "LoOper"):
                candidates.append(
                    os.path.join(meipass, sub, "AI", "models", base)
                )
    for candidate in candidates:
        if os.path.isdir(candidate) and os.path.isfile(
            os.path.join(candidate, "config.json")
        ):
            return candidate
    return None


def _get_local_embedder():
    """Return the process-wide llama.cpp embedding engine (lazy).

    Replaces the old sentence-transformers all-MiniLM singleton: embeddings
    now come from ``AI.embedding_server`` (embeddinggemma GGUF, offline).
    Returns None when the server cannot start so callers skip context
    injection.
    """
    global _EMBED_MODEL, _EMBED_AVAILABLE
    if _EMBED_MODEL is not None:
        return _EMBED_MODEL
    if _EMBED_AVAILABLE is False:
        return None
    try:
        from AI import embedding_server

        client = embedding_server.get_embed_client()
        if client is None:
            _EMBED_AVAILABLE = False
            logger.warning(
                "Local embedder: llama.cpp embedding server unavailable"
            )
            return None
        _EMBED_MODEL = client
        _EMBED_AVAILABLE = True
        logger.info("Local embedder: llama.cpp embedding server ready")
        return _EMBED_MODEL
    except Exception as exc:
        _EMBED_AVAILABLE = False
        logger.debug("Local embedder not available: %s", exc)
        return None

# ---------------------------------------------------------------------------
#  Ingestion-side helpers: probe identity + fact-pool dedupe + embed cache
# ---------------------------------------------------------------------------
# These encode the ComoRAG digest invariant: probes never re-ask what is
# already covered, and the fact pool never re-stores the same finding — the
# loop must make NEW progress or stop (observed stall: one LLM node re-added
# the same "who is the AI software engineer" finding every ~3-4 min cycle for
# 17 minutes before finishing with a vague answer).


def _probe_key(probe: str) -> str:
    """Normalize a probe so reworded repeats (list numbering, case,
    punctuation, spacing) collapse to the same identity."""
    s = re.sub(r"\W+", "", str(probe or "")).lower()
    # "1. Who ..." and "Who ..." are the same probe — drop leading list
    # numbering (digits that survived the non-word strip).
    s = re.sub(r"^\d+", "", s)
    return s


def _probe_repeats(probe: str, seen_probes) -> bool:
    """True when *probe* normalizes to an already-used probe."""
    k = _probe_key(probe)
    if not k:
        return False
    return any(_probe_key(p) == k for p in seen_probes)


def _probe_similar(a: str, b: str) -> bool:
    """True when two probes target the same thing in other words.

    A cycle accepts up to three probes (one retrieval + one composer call
    each), and a generator that answers "where does the applicant live?" with
    three phrasings makes the cycle re-retrieve and re-derive the SAME value
    three times — the rumination the stop rule cannot see, because it all
    happens inside ONE round.  Compared on specific terms only
    (4+ chars, generic probe vocabulary is not a target).

    ponytail: 0.6 of the SHORTER probe's specific terms (overlap, not
    Jaccard — short probes never reach 0.6 Jaccard on a paraphrase), no
    embeddings.  Two questions on the same entity but different attributes
    ("... live in?" vs "... work in?") can be folded together; embed the
    probes if that shows up in a real run.
    """
    _a = {t for t in _lex_tokens(a) if len(t) >= 4} - _COMMON_WORDS
    _b = {t for t in _lex_tokens(b) if len(t) >= 4} - _COMMON_WORDS
    if not _a or not _b:
        return False
    return len(_a & _b) / min(len(_a), len(_b)) >= 0.6


def _fact_core(entry: str) -> str:
    """The FINDING part of a composed fact — probe attribution, cycle tags,
    verbatim Support quotes and echoed instructions are noise for duplicate
    detection (Support differs per evidence chunk even when the finding is
    the same)."""
    s = str(entry or "")
    lines = s.splitlines()
    # Drop the probe-attribution first line ("[cycle N] probe: ...").
    if lines and lines[0].lstrip().lower().startswith("[cycle"):
        s = "\n".join(lines[1:]).strip()
    low = s.lower()
    i = low.find("key finding")
    if i == -1:
        i = low.find("finding")
    if i != -1:
        s = s[i:]
        low = s.lower()
        j = low.find("support")
        if j != -1:
            s = s[:j]
    core = re.sub(r"\W+", "", s).lower()
    return re.sub(r"^\d+", "", core)


def _dedupe_fact_entries(entries, existing):
    """Drop composed facts whose finding repeats an already-composed one.

    Raw-evidence tags (``evidence:``) are never duplicates and always pass
    through.  Only EXACT / contained repeats are dropped — reworded findings
    are caught upstream by probe normalization (a repeated probe is rejected
    and re-generated), so no speculative fuzzy matching that could starve
    the fact pool of genuinely new findings.
    """
    existing_cores = [
        _fact_core(e) for e in existing
        if not str(e).lstrip().lower().startswith("evidence:")
    ]
    existing_cores = [c for c in existing_cores if c]
    kept = []
    for e in entries:
        if str(e).lstrip().lower().startswith("evidence:"):
            kept.append(e)
            continue
        core = _fact_core(e)
        if not core:
            kept.append(e)
            continue
        if any(core == c or core in c or c in core for c in existing_cores):
            continue
        kept.append(e)
        existing_cores.append(core)
    return kept


# A composed "finding" that merely states the value is ABSENT carries no
# knowledge.  Counting it as a scored fact closed the cycle, and — worse —
# it was injected into the consumer as a distilled fact ("... is not
# provided in the evidence"), telling the model to leave the field empty
# (observed: the LinkedIn-URL field returned nothing on a resume that holds
# the URL, while the pool carried "Key Finding: ... is not provided").
_NEGATIVE_FINDING_RE = re.compile(
    r"\b(?:not|no|never|none|n/?a)\b[^.]{0,60}?"
    r"(?:provided|mentioned|stated|specified|listed|found|present|"
    r"available|contained|included|given|disclosed|known)\b"
    r"|\b(?:does\s+not|doesn't|did\s+not|cannot|can't|is\s+not|are\s+not)\b"
    r"[^.]{0,40}?(?:contain|include|mention|state|specify|list|provide|"
    r"hold|answer)\b"
    r"|\bno\s+(?:information|details?|mention|evidence|value)\b"
    r"|\bnot\s+(?:in|among)\s+the\s+evidence\b",
    re.IGNORECASE,
)


# The FINDING label as composers actually emit it — markdown-bolded
# ("**Key Finding:** ...") or headed ("## Key Findings").  A bare
# ``startswith("key finding")`` missed the bolded form, so a composed
# "**Key Finding:** No specific city ... can be determined" was pooled as a
# scored fact instead of being read as an absence (observed).
_FINDING_LABEL_RE = re.compile(
    # An optional list number and/or the markdown markers a composer emits
    # ("**Key Finding 1:**", "## Key Findings :", "1. Key Finding:").  A bare
    # "key finding" prefix missed the numbered/bolded forms, so a multi-finding
    # answer was read as ONE block - and one ungrounded sibling then dropped the
    # whole answer instead of just itself.
    r"^\s*[\*\-#>\s]*(?:\d+[.)]?\s*)?(?:key\s+)?findings?\s*\d*\s*[\*\s]*:",
    re.IGNORECASE | re.MULTILINE,
)


def _is_negative_finding(entry: Any) -> bool:
    """True when a composed finding only reports the value is ABSENT.

    Only the FINDING lines are inspected: the verbatim ``Support:`` quote is
    source text and routinely contains "no"/"not" without meaning the field
    is unanswerable.
    """
    for line in str(entry or "").splitlines():
        m = _FINDING_LABEL_RE.match(line)
        if m and _NEGATIVE_FINDING_RE.search(line[m.end():]):
            return True
    return False


def _split_findings(answer: Any) -> List[str]:
    """Split one responder answer into its individual findings.

    The composer answers ONE probe with UP TO FIVE findings.  Treating the
    whole answer as a single entry let a fabricated finding ride in beside a
    grounded one AND hid it from the guards that inspect only the first
    "Key Finding" line (negative detection, digest dedupe).
    """
    text = str(answer or "").strip()
    if not text:
        return []
    starts = [m.start() for m in _FINDING_LABEL_RE.finditer(text)]
    if not starts:
        return [text]
    blocks = [
        text[i:j].strip()
        for i, j in zip(starts, starts[1:] + [len(text)])
    ]
    return [b for b in blocks if b]


def _norm_ground(text: Any) -> str:
    """Whitespace/punctuation-insensitive form for verbatim comparison."""
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def _finding_support(entry: Any) -> str:
    """The verbatim ``Support:`` quote(s) of one composed finding.

    Returns "" when the finding carries none — the composer's own contract
    ("a finding you cannot support with a quoted sentence must be OMITTED")
    makes a supportless finding unverifiable, so it is treated as fabricated.
    """
    parts = re.split(r"(?im)^\s*[\*\-#>\s]*support\s*[\*\s]*:", str(entry or ""))
    if len(parts) < 2:
        return ""
    return "\n".join(p.strip() for p in parts[1:] if p.strip())


def _finding_body(entry: Any) -> str:
    """The CLAIM part of a composed finding (everything before ``Support:``).

    The Support quote is source text; the claim is what the composer ASSERTS
    about it.  Both must be checked — a quote lifted from one chunk does not
    license inventing a value in the claim.
    """
    parts = re.split(
        r"(?im)^\s*[\*\-#>\s]*support\s*[\*\s]*:", str(entry or "")
    )
    head = parts[0] if parts else str(entry or "")
    m = _FINDING_LABEL_RE.search(head)
    return head[m.end():] if m else head


# ── The grounding rule (single implementation) ──────────────────────────
# The pipeline's job is to GROUND, not merely to retrieve: every claim it
# emits — a pooled fact, a judge draft, the final synthesis — must be
# traceable to the verbatim evidence it was retrieved from.  These primitives
# are called by the engine AND by every composer hook, so "grounded" means
# exactly one thing everywhere and a claim cannot be laundered through a
# stage that checks less.

# An email address or URL: always an exact value, always verbatim or wrong.
_EMAIL_OR_URL_RE = re.compile(
    r"[\w.+-]+@[\w-]+\.[\w.]+|https?://\S+|www\.\S+"
)
# A capitalized word (>=3 chars) — a proper noun / product / place.  The same
# signal ``_validate_probe`` already uses to catch invented entities, so a
# probe and a fact are held to one standard.
_CAP_TOKEN_RE = re.compile(r"[A-Z][A-Za-z0-9'\u2019._-]{2,}")
# Anything carrying digits: a number, date, price, phone, version, year.
_VALUE_TOKEN_RE = re.compile(r"\S*\d\S*")
_DIGIT_RE = re.compile(r"\d")


def grounding_violations(
    text: Any, evidence: Any, allow: Any = "",
) -> List[str]:
    """Terms *text* asserts that do NOT occur in *evidence*.

    Returns ``[]`` when the text is grounded.  ``allow`` is extra permitted
    vocabulary (the goal / system / intent plan): the user's own words are
    not source claims and must not trip the check.  Sentence-initial words are
    skipped (capitalized by grammar) and ordinary probe vocabulary is exempt
    for the same reason ``_validate_probe`` exempts it.
    """
    s = str(text or "")
    if not s.strip():
        return []
    ev = _norm_ground(evidence)
    if not ev:
        return []
    ok = set(_norm_ground(allow).split())
    ev_dense = re.sub(r"[^a-z0-9]+", "", str(evidence or "").lower())
    _first = ""
    _m0 = re.match(r"\W*([A-Za-z][\w'\u2019.-]*)", s)
    if _m0:
        _first = _m0.group(1).lower()
    bad: List[str] = []

    def _add(token: str) -> None:
        low = token.lower()
        if not token or low in ok or low in _COMMON_WORDS or low in _STOPWORDS:
            return
        if _norm_ground(token) in ev:
            return
        # A number may be written with or without separators ("4,000" in the
        # answer vs "4000" in the source), so a digit-bearing token is also
        # compared unseparated — otherwise a correctly-copied value would be
        # rejected for its formatting.
        if _DIGIT_RE.search(token) and (
            re.sub(r"[^a-z0-9]+", "", low) in ev_dense
        ):
            return
        if token not in bad:
            bad.append(token)

    for _m in _EMAIL_OR_URL_RE.finditer(s):
        _add(_m.group(0).rstrip(".,;)"))
    for _m in _CAP_TOKEN_RE.finditer(s):
        if _m.group(0).lower() != _first:
            _add(_m.group(0))
    for _m in _VALUE_TOKEN_RE.finditer(s):
        _tok = _m.group(0).strip("()[]{}\"'.,;:")
        if len(_tok) >= 2:
            _add(_tok)
    return bad


def grounding_window(text: Any, max_chars: int) -> str:
    """Bounded view of *text* that keeps BOTH ends.

    A tail-only slice (``text[-N:]``) silently discarded the beginning of the
    evidence — where a resume keeps the contact/address block — before the
    composer ever saw it (observed: 'city = riverside' sat in the first half of a
    6.5k-char evidence pool while the composer was handed only the last 2.5k,
    so it answered from the cover-letter prose instead).  A composer that must
    ground its claims cannot ground them on text it never received.
    """
    s = str(text or "")
    n = int(max_chars)
    if n <= 0 or len(s) <= n:
        return s
    tail = n // 2
    return s[: n - tail] + "\n[...]\n" + s[-tail:]


def _is_grounded_finding(entry: Any, evidence: Any, allow: Any = "") -> bool:
    """True when a composed finding is TRACEABLE to the retrieved evidence.


    (a normalized substring, a verbatim run of 8+ words to absorb punctuation
    drift, or a paraphrase tha    Both halves must hold: (a) the Support quote is traceable to the evidencet invents no entity/value) AND (b) the claim
    itself asserts nothing the evidence does not contain.  Anything else is a
    FABRICATED claim that must never enter the
    fact pool — the pool is what the consumer reads, and a fabricated value
    there beats the real one (observed: 'Location (city)' pooled 'Metro City,
    CA' — absent from the source — beside the real 'city = riverside',
    and the synthesis answered 'San Francisco').  Dropping is safe: the
    verbatim chunk stays in the evidence pool.
    """
    support = _finding_support(entry)
    if not support:
        return False
    ev = _norm_ground(evidence)
    sup = _norm_ground(support)
    if not ev or not sup:
        return False
    if sup not in ev:
        words = sup.split()
        verbatim = any(
            " ".join(words[i:i + 8]) in ev
            for i in range(0, max(0, len(words) - 8) + 1)
        )
        # A quote shorter than 9 words gets no window at all (the range holds a
        # single offset), so ``verbatim`` is False for every paraphrase however
        # faithful it is.  Fall back to the SAME rule the claim is held to: a
        # paraphrase that invents no entity and no value is still traceable.
        # Demanding a literal quote here was stricter than the claim check and
        # dropped correct findings wholesale — reported as "ungrounded" while
        # the claim itself had ZERO violations (observed: Support "The
        # candidate lives in Canada" for the probe "Do you currently live
        # in Canada?").  Invented entities still fail: "Metro City" or
        # "San Francisco" in the quote is flagged as before.
        if not verbatim and grounding_violations(support, evidence, allow):
            return False
    return not grounding_violations(_finding_body(entry), evidence, allow)


def _is_relevant_finding(entry: Any, focus_tokens: "set[str]") -> bool:
    """True when a finding's CLAIM touches what the search is after.

    Grounding is not enough: a finding can quote the source word for word
    and still answer another question.  Relevance here is lexical — the
    claim must share a SPECIFIC term with what the pipeline set out to find
    (the plan's search terms).  The PROBE's own wording is deliberately NOT
    part of the vocabulary: a correct answer rarely echoes the probe ("What
    is the location (city)?" -> "Riverside"), so matching on it rejected real
    answers while letting a probe's generic nouns through.  With no plan the
    gate fails OPEN (nothing to judge relevance against).

    Only CURATION is gated, and only ONCE: the relevance purge at the final
    synthesis drops the off-topic facts from the pool while the chunk they
    came from stays in the verbatim evidence pool the synthesis grounds on.

    This is a PREFILTER, not the verdict: at the final synthesis the
    consolidator runs ``relevance_judge`` (one batched model verdict over the
    finished pool) on the findings that pass here, and only that verdict may
    reject one for being off-topic.  Token overlap cannot see a claim that
    reuses a focus noun for a DIFFERENT detail ("the applicant's date of
    birth is 01/02/1990" for a probe asking for a city), which is the case
    this gate let through.
    """
    if not focus_tokens:
        return True
    return bool(_lex_tokens(_finding_body(entry)) & focus_tokens)


# Lazy per-chunk vector cache (probe-driven retrieval): a chunk is embedded
# ONLY when a probe's lexical pre-select actually picks it, and its vector is
# then reused across probes AND node executions — the corpus is never encoded
# in one upfront shot (observed: 160 chunks / ~127k chars embedded before a
# single probe ran).  Bounded (each vector is ~1024 floats).  Keyed by chunk
# text only — safe because a process uses one embedding backend per node
# config; mixing models for the same text is not a supported pattern.
_CHUNK_VEC_CACHE: "OrderedDict[str, List[float]]" = OrderedDict()
_CHUNK_VEC_CACHE_MAX = 4000
# Which embedding backend filled the cache above.  Its key is the chunk TEXT
# alone, so a process that switches backend (embeddinggemma for llama.cpp
# nodes, nomic-embed-text for Ollama ones) would serve vectors from the other
# space: same dimension, no shape error, silently meaningless rankings.
_CHUNK_VEC_CACHE_NAMESPACE = ""

# Common words that carry no retrieval signal — ignored by the lexical
# candidate pre-selector so probes and chunks meet on content words.
_LEX_STOP = frozenset({
    "the", "and", "for", "with", "what", "who", "when", "where", "why",
    "which", "how", "this", "that", "these", "those", "from", "have",
    "has", "had", "are", "was", "were", "does", "did", "do", "is", "not",
    "you", "your", "would", "could", "should", "will", "can", "into",
    "about", "than", "then", "them", "they", "their", "there", "also",
    "any", "all", "one", "such", "its", "may", "might", "must", "etc",
    "like", "just", "because", "been", "being", "get", "got", "make",
})


def _lex_tokens(text: str) -> "set[str]":
    """Lowercased alnum tokens minus stop words — the cheap lexical bridge
    used to pre-select candidate chunks BEFORE embedding (probe-driven
    retrieval never embeds the whole corpus)."""
    try:
        return {
            w for w in re.findall(r"[a-z0-9]{2,}", str(text).lower())
            if w not in _LEX_STOP
        }
    except Exception:
        return set()


# ---------------------------------------------------------------------------
#  Chunking helpers (character-based, no dependencies)
# ---------------------------------------------------------------------------

def _chunk_text(text: str, size: int, overlap: int) -> List[str]:
    """Split *text* into overlapping character chunks."""
    chunks: List[str] = []
    if size <= 0:
        return [text]
    step = max(1, size - max(0, overlap))
    i = 0
    while i < len(text):
        chunk = text[i : i + size]
        if chunk:
            chunks.append(chunk)
        i += step
    return chunks


def _cap_chunks_chars(chunks: List[str], max_chars: int) -> List[str]:
    """Greedily keep whole chunks while total chars <= max_chars.

    A single chunk larger than the budget is truncated to the budget (the
    per-cycle evidence cap must hold even for one oversized chunk).
    """
    if max_chars <= 0 or not chunks:
        return chunks
    selected: List[str] = []
    total = 0
    for c in chunks:
        if total + len(c) > max_chars:
            if not selected:
                # First chunk alone exceeds the budget — cut it down.
                selected.append(c[:max_chars])
            break
        selected.append(c)
        total += len(c)
    return selected


def _cosine_sim(a: List[float], b: List[float]) -> float:
    """Cosine similarity between two vectors."""
    try:
        import math
        if len(a) != len(b):
            logger.warning(
                "Cosine similarity dimension mismatch: %d vs %d — "
                "returning 0 (mixed embedding models?)",
                len(a), len(b),
            )
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na == 0.0 or nb == 0.0:
            return 0.0
        return dot / (na * nb)
    except Exception:
        return 0.0


def _local_embed(texts: List[str]) -> Optional[List[List[float]]]:
    """Embed texts via the llama.cpp embedding server (embeddinggemma).

    Returns a list of embedding vectors, or None if the server is
    unavailable (callers skip context injection).  The server is
    authoritative — no sentence-transformers/PyTorch fallback remains.
    """
    model = _get_local_embedder()
    if model is None:
        return None
    try:
        from AI import embedding_server

        vectors = embedding_server.embed_texts(texts)
        if vectors is None or len(vectors) != len(texts):
            return None
        return vectors
    except Exception as exc:
        logger.debug("Local embedding failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
#  ProbeBasedConsolidator
# ---------------------------------------------------------------------------

class ProbeBasedConsolidator:
    """Embedding-based context retrieval for LLM node context sources.

    Uses chunk embeddings as a detached sparse attention layer:
    chunks become keys (vectors), probes become queries (vectors),
    cosine similarity selects the top-k relevant chunks.
    No model-generated text is required — with no responder/synthesis hooks,
    retrieval IS the consolidation.  When hooks ARE supplied, every hooked
    output must pass the grounding contract in the module docstring: the
    engine verifies it against the retrieved evidence before it is pooled or
    returned.

    Args:
        api_url: The OverWatch gateway URL.
        chunk_size: Characters per chunk when splitting context sources.
        overlap: Character overlap between chunks.
        top_k: Number of chunks to retrieve per probe (lower = less text).
        auto_threshold: Minimum context chars to trigger consolidation.
    """

    def __init__(
        self,
        api_url: str = "http://localhost:8000",
        chunk_size: int = 1000,
        overlap: int = 200,
        top_k: int = 3,
        embed_client: Any = None,
        probe_max_tokens: int = 64,
        probe_temperature: float = 0.3,
        char_budget: int = 1500,
        max_probes: int = 6,
        min_facts: int = 3,
        probe_retry_attempts: int = 3,
        probe_context_chars: int = 6000,
        responder_evidence_chars: int = 2500,
        judge_facts_chars: int = 1500,
        judge_evidence_chars: int = 2000,
        synth_facts_chars: int = 3000,
        synth_evidence_chars: int = 4000,
        cache_namespace: str = "",
    ):
        self._api_url = api_url.rstrip("/")
        self._chunk_size = max(100, chunk_size)
        self._overlap = max(0, min(overlap, self._chunk_size - 1))
        self._top_k = max(1, top_k)
        # Embedding client: either a callable ``(texts) -> vectors`` or an
        # object with an ``embed(texts)`` method (e.g. the llama.cpp engine).
        # When None, the shared lazy ``AI.embedding_server`` client is used.
        self._embed_client = embed_client
        self._probe_max_tokens = max(1, probe_max_tokens)
        self._probe_temperature = probe_temperature
        self._char_budget = max(100, char_budget)
        # Hard cap on VALID cycles — a CEILING only (Stop-RAG: the loop ends
        # when retrieving stops adding value, not when a quota was served).
        self._max_probes = max(1, min(max_probes, 12))
        # Fact-driven cycle quota: a cycle only counts as DONE once it has
        # accumulated ``min_facts`` COMPOSED facts (raw-evidence entries
        # tagged ``evidence:`` never count).  ``probe_retry_attempts``
        # bounds the critique-driven re-invocations of the LLM probe
        # generator per cycle before the deterministic ladder takes over.
        self._min_facts = max(1, min(min_facts, 8))
        self._probe_retry_attempts = max(0, min(probe_retry_attempts, 4))
        # Chars of the FULL BASE CONTEXT (context nodes, docs, code/LLM
        # outputs) exposed to the probe generator per call.  The intent plan
        # is the guide (WHAT to look for); the base context is the source of
        # truth (the actual terms/details) — probes must derive from the
        # COMBINATION, never from the intent excerpt alone.
        self._probe_context_chars = max(1000, min(probe_context_chars, 12000))
        # Cached for re-retrieval during recursive generation
        self._cached_chunks: List[str] = []
        self._cached_embeddings: Optional[List[List[float]]] = None
        # Track which chunk indices have already been retrieved (prevents repetition)
        self._retrieved_indices: set = set()
        # Curated fact pool from the last ``consolidate()`` run — exposed as
        # a property so the executor can store it as workflow variables.
        self._facts_pool: List[str] = []
        # VERBATIM source chunks retrieved during the last ``consolidate()``
        # run.  Facts are a PARAPHRASE: an exact value (a URL, a date, a
        # number) survives only here, so a consumer that needs exact values
        # grounds on this pool instead of the composed facts.
        self._evidence_pool: List[str] = []
        # Reason for the last embedding failure (captured by
        # ``_llamacpp_embed``/``_compute_embeddings``) — carried into the
        # EmbeddingFailureError message for diagnostics.
        self._last_embed_error: str = ""
        # ── Hook view sizes (the grounding-window contract) ──
        # Every hook receives a HEAD+TAIL window of the facts/evidence, sized
        # to the tail slice the hook itself applies, so that slice becomes a
        # no-op and the truncation policy lives HERE alone.  A tail-only view
        # silently cut the head of the evidence — where a resume keeps the
        # contact/address block (observed: the composer answered from the
        # cover-letter prose while "city = riverside" sat in the part it was
        # never handed).  A composer cannot ground a value it never saw.
        self._responder_evidence_chars = max(500, int(responder_evidence_chars))
        self._judge_facts_chars = max(500, int(judge_facts_chars))
        self._judge_evidence_chars = max(500, int(judge_evidence_chars))
        self._synth_facts_chars = max(500, int(synth_facts_chars))
        self._synth_evidence_chars = max(500, int(synth_evidence_chars))
        # Separates the PROCESS-GLOBAL chunk vector cache per embedding backend
        # (see _embed_chunks_cached).
        self._cache_namespace = str(cache_namespace or "")

    # ------------------------------------------------------------------
    #  Public API
    # ------------------------------------------------------------------

    def consolidate(
        self,
        prompt: str,
        context_sources: Dict[str, Any],
        graphrag_context: str = "",
        system_prompt: str = "",
        intent_planner: Optional[Callable[[str], Optional[str]]] = None,
        probe_generator: Optional[Callable[[str, str], Optional[str]]] = None,
        responder: Optional[Callable[[str, str], Optional[str]]] = None,
        response_generator: Optional[Callable[[str, str, str], Optional[str]]] = None,
        judge: Optional[Callable[[str, str, str], Optional[str]]] = None,
        relevance_judge: Optional[Callable[[str, List[str]], Optional[List[bool]]]] = None,
        stop_flag: Optional[Callable[[], bool]] = None,
    ) -> str:
        """ComoRAG-style iterative probing cycles with a memory pool.

        Implements the polishment loop + evidence accumulation from ComoRAG
        (arXiv:2508.10419): narrative reasoning is an evolving interplay
        between new evidence acquisition and past knowledge consolidation.

        Flow per configured cycle:
          0. INTENT: the user prompt + system prompt + a bounded excerpt of
             the available context are composed into an ENHANCED INTENT
             prompt (goal + context).  Probes derive from it, so they are
             grounded in the available context — never invented from the
             bare request.
          1. PROBE: the primary probes are TARGETED questions derived from
             the enhanced intent (goal + system + similarity-ranked context)
             and validated against the available context — a probe that
             rephrases the user request or uses terms absent from the
             sources is rejected (small LLMs otherwise echo the query and
             invent product names).  Fact-grounded follow-ups keep the
             cycle budget alive when probes repeat or fail validation.
          2. RETRIEVE: the probe queries the memory workspace (embedding
             retrieval over the chunked context — GraphRAG access); the
             top-k chunks become this cycle's evidence, capped by
             ``char_budget`` PER CYCLE.
          3. RESPOND + EXTRACT: ``responder`` makes the LLM answer the
             probe using that evidence; the answer's FACTS enter the
             memory pool (raw evidence is used when no responder is
             available or it fails).
          4. The memory pool feeds the next probe.
        Stop rule (Stop-RAG-style retrieval control): retrieving continues
        only while it still HAS value — one round at a time, ending the
        moment the sufficiency judge returns a GROUNDED answer (the goal is
        covered) or the sweep saturates (a round adds no new on-topic fact).
        ``max_probes`` is the safety CEILING, never a minimum the sweep must
        serve first: a confidence-driven stop after round 1 is the intended
        outcome, not a premature one, because the answer itself was verified
        against the evidence.

        After the cycles, ``response_generator`` SYNTHESIZES all the
        accumulated facts into one coherent response for the initial query.

        Args:
            prompt: The LLM node's prompt (the initial query).
            context_sources: Dict with keys ``context_nodes``,
                ``documents``, ``code_outputs``, ``llm_outputs``.
            graphrag_context: Optional GraphRAG context string.
            probe_generator: Optional callable ``(enhanced_intent,
                facts_so_far) -> probe text | None``.  ``enhanced_intent``
                is the goal + system + context excerpt prompt built by this
                engine; ``facts_so_far`` accumulates across cycles.  When
                provided, each cycle asks it for the next probe (informed
                by the accumulated facts); otherwise lexical probes are
                used.  A ``None``/empty/repeated result falls back to
                lexical probes.
            responder: Optional callable ``(probe, evidence_text)
                -> facts | None``.  Makes the LLM answer each probe with
                its retrieved evidence; the answer's facts are accumulated
                into the memory pool.  Each finding is pooled only after the
                grounding check (Support quote present AND traceable, claim
                asserting nothing the evidence lacks); the rest fall back to
                the verbatim chunk they were retrieved from.
            response_generator: Optional callable ``(enhanced_intent,
                facts_text, evidence_text) -> synthesis | None``.
                Synthesizes ONE final response anchored to the enhanced
                intent and grounded on the VERBATIM source chunks
                (evidence_text is ground truth; self-generated facts must
                be validated against it).  ``None``/empty returns the
                facts.
            judge: Optional callable ``(goal, facts_text, evidence_text)
                -> verdict | None`` (ComoRAG meta-loop gate).  Called
                after every probe that produced composed facts; the
                verdict must be one line: ``ANSWER: <draft>`` when the
                accumulated material already answers the goal (stops the
                probe loop; the draft must itself pass the grounding check
                before it is recorded as a candidate for the synthesis) or
                ``MISSING: <gap>`` when
                it does not (the gap seeds the next probe so it targets
                the model's own identified blind spot).  ``None``/
                unparseable keeps probing unchanged.
            intent_planner: Optional callable ``(enhanced_intent) -> plan
                | None``.  Creates the INITIAL INTENT PLAN — a synthesized
                statement of WHAT the pipeline needs to know, WHERE to
                look, and WHAT to expect (based on the goal + system +
                context excerpt) — that becomes the base from which every
                probe derives.  Without it the pipeline would shoot probes
                straight from the user prompt and context; with it, the
                probing loop goes back and forth to the context following
                the plan's search items.  ``None``/empty falls back to
                probing from the raw enhanced intent.
            stop_flag: Optional callable ``() -> bool`` — the caller's user
                abort (ESC).  Checked at every cycle boundary and before the
                final synthesis, so a stop ends the consolidation instead of
                being ignored until the node finishes.

        Returns:
            The synthesized response (when a response generator succeeds),
            otherwise the accumulated facts, or an empty string when
            nothing was retrieved.

        Raises:
            EmbeddingFailureError: when the embedding computation fails —
            a hard break so the chain stops with the failure visible.
        """
        # -- Flatten all context --
        all_texts: List[str] = []
        for key in ("context_nodes", "documents", "code_outputs", "llm_outputs"):
            items = context_sources.get(key, []) or []
            if isinstance(items, str):
                items = [items]
            for item in items:
                if item and isinstance(item, str) and item.strip():
                    all_texts.append(item)

        if not all_texts:
            return ""

        total_chars = sum(len(t) for t in all_texts)

        # -- Stage 1: Chunk (cheap) — NO full-corpus embedding --
        # Probe-driven ingestion (ComoRAG): the corpus is chunked once, but
        # chunks are embedded ONLY when a probe actually selects them as
        # candidates (see _retrieve_for_probe) — never the whole context in
        # one shot (observed: 160 chunks / ~127k chars embedded upfront cost
        # minutes before a single probe ran).  Initial probes derive from the
        # enhanced intent below; each probe diverts its own search path.
        all_chunks = self._build_chunks(all_texts, graphrag_context)
        if not all_chunks:
            return ""

        chunk_embs = None  # probe-driven mode: vectors are embedded on demand

        # Cache for re-retrieval during recursive generation
        self._cached_chunks = all_chunks
        self._cached_embeddings = chunk_embs

        # -- Stage 0: Enhanced intent (goal + system + grounded context) --
        # The intent is grounded on the top chunks that LEXICALLY overlap the
        # goal — embedding-free, so no initial full-corpus ingestion is ever
        # needed.  The probing queries derive from this grounded intent; the
        # corpus stays reachable through per-probe retrieval below.
        context_excerpt = self._lexical_excerpt(prompt, all_chunks)
        if not context_excerpt:
            context_excerpt = self._build_context_excerpt(all_texts)
        intent_prompt = self._build_enhanced_intent(
            prompt, system_prompt, context_excerpt,
        )
        log_table(
            logger, logging.INFO, "ComoRAG Corpus",
            [
                ("Context chars", total_chars),
                ("Sources", len(all_texts)),
                ("Chunks", "%d (lazy per-probe embed)" % len(all_chunks)),
                ("Intent excerpt", "%d chars (grounded, embedding-free)"
                 % len(context_excerpt)),
                ("Cycles allowed", self._max_probes),
            ],
        )

        # -- Stage 1: INITIAL INTENT PLAN (drives probe derivation) --
        # The pipeline must not shoot probes straight from the user prompt:
        # the model first synthesizes WHAT it needs to know, WHERE to look,
        # and WHAT to expect (goal + system + context excerpt), then every
        # probe derives from that plan.  The plan stays the roadmap across
        # cycles — the accumulated facts tell the probe generator which
        # plan items are still uncovered.  Falls back to the raw enhanced
        # intent when no planner is provided or it fails.
        _plan = None
        if intent_planner is not None:
            for _ in range(2):
                try:
                    _cand = intent_planner(intent_prompt)
                except Exception:
                    _cand = None
                if _is_valid_intent_plan(_cand):
                    _plan = _cand
                    break
            if _plan:
                intent_prompt = (
                    intent_prompt
                    + "\n\nINTENT PLAN (base for probing):\n"
                    + str(_plan).strip()
                )
                log_block(
                    logger, logging.INFO, "ComoRAG Intent Plan",
                    str(_plan).strip(),
                )
            else:
                # Dropping a plan is SAFE: no plan means the relevance gate
                # fails OPEN.  Accepting an unusable one is not — it poisoned
                # both the probes and that gate.
                log_block(
                    logger, logging.WARNING, "ComoRAG Intent Plan",
                    "no usable plan after 2 attempt(s) — probing from the raw "
                    "enhanced intent (relevance gate fails OPEN)",
                )

        # -- Relevance vocabulary (what the pipeline set out to FIND) --
        # Composed findings enter the DISTILLED pool only when they touch
        # this vocabulary.  Grounding alone let unrelated-but-verbatim trivia
        # satisfy the per-cycle quota on every cycle, so the loop never
        # saturated and the pool shipped non-answers as distilled facts.
        _focus_tokens = _plan_focus_tokens(_plan)

        # -- Stage 2: Probes (LLM-generated when a generator is provided,
        #    lexical otherwise) --
        lexical_probes = self._extract_probes(prompt) or [prompt[:200]]
        # Full available context, lowercased — the grounding check for probe
        # validation: a targeted probe may only use terms present here.
        _context_text = "\n\n---\n\n".join(all_texts)

        # -- Probe derivation context: INTENT (guide) + FULL BASE CONTEXT --
        # The intent plan says WHAT to look for; the base context (context
        # nodes, docs, code outputs, previous LLM outputs) is the SOURCE OF
        # TRUTH and supplies the actual terms/details.  Probes derive from
        # the combination — the intent alone (built on a similarity excerpt)
        # would blind them to everything outside the excerpt.  Bounded by
        # the small model's window; retrieval remains the access path for
        # the final evidence.
        _probe_base_ctx = (
            intent_prompt
            + "\n\nBASE CONTEXT (source of truth — use ONLY terms from "
            "here):\n"
            + self._spread_excerpt(_context_text, self._probe_context_chars)
        )

        # -- Stage 3: Iterative probing cycles (ComoRAG) --
        # SOURCES vs FACTS: the sources (context nodes + docs) live in the
        # indexed graph and are ONLY accessed via embedding retrieval.  The
        # FACT POOL is the curated temporal context created by this
        # mechanism — it drives probe generation and the final synthesis.
        # Facts are facts, sources are sources: raw source chunks are never
        # fed to the probe generator.  The char budget caps EACH cycle's
        # source evidence.
        #
        # FACT-DRIVEN loop: a cycle only counts as DONE once it accumulates
        # ``min_facts`` COMPOSED facts (raw-evidence entries tagged
        # ``evidence:`` never count).  A cycle that produces no facts is
        # INVALID — it does not advance; the next probe (critique-refined
        # when the LLM generator was rejected) retries the same slot until
        # the quota is met, the evidence space is exhausted, or the
        # zero-progress guard trips.  This is how huge context databases are
        # ingested by small models: the corpus is swept over many cycles and
        # layered into a compact fact pool — never truncated.
        facts: List[str] = []          # the curated fact pool (composed + evidence-tagged)
        evidence_pool: List[str] = []  # VERBATIM source chunks per cycle
        # Terms the PIPELINE itself introduced (goal / system / plan).  They
        # are not source claims, so the grounding check permits them while
        # still rejecting anything the sources do not contain.
        _verify_allow = "\n".join(
            str(_x or "") for _x in (prompt, system_prompt, _plan)
        )
        seen_probes: set = set()
        _facts_text = ""
        _evidence_text = ""
        completed_cycles = 0
        consecutive_invalid = 0
        _stalled_cycles = 0
        # Uncertainty gate (ComoRAG meta-loop / Stop-RAG stop decider): the
        # judge's last gap seeds the next probe; a GROUNDED ANSWER records its
        # draft and ends the sweep immediately (the draft is submitted to the
        # final synthesis for verification).
        _pending_gap = ""
        _answer_draft = ""
        while completed_cycles < self._max_probes:
            # User abort (ESC): checked at every cycle boundary so a stop during
            # a long consolidation ends it instead of being ignored.
            if stop_flag is not None and stop_flag():
                log_block(
                    logger, logging.WARNING, "ComoRAG",
                    "stopped by user after %d cycle(s)" % completed_cycles,
                )
                break
            facts_before = len(self._counted_facts(facts))

            # -- Probe selection with critique-driven retry --
            # On rejection (rephrase / invented terms / repeat / empty) the
            # generator is re-invoked with the rejection reason as FEEDBACK
            # (the executor folds critique + correction into ONE model call),
            # up to ``probe_retry_attempts`` per cycle.  Only then the
            # deterministic ladder (fact-grounded follow-up -> goal anchor)
            # takes over.
            probe = None
            cycle_probes: List[str] = []
            feedback = ""
            _attempt_log: List[Tuple[str, str]] = []
            if probe_generator is not None:
                for _attempt in range(self._probe_retry_attempts + 1):
                    _probe_ctx = _probe_base_ctx
                    if _facts_text:
                        # ENHANCED INTENT (drift path): each cycle the intent
                        # is re-enhanced toward the MOST RECENTLY discovered
                        # facts — the current drift path to explore next —
                        # while the full accumulated fact tree still flows via
                        # the generator's facts argument.  The knowledge
                        # evolves as a tree of facts over cycles, and each
                        # cycle contributes MIXED context facts that the final
                        # synthesis combines.
                        _probe_ctx += (
                            "\n\nENHANCED INTENT (current drift path — "
                            "facts discovered most recently; extend this "
                            "knowledge tree, do not re-ask):\n"
                            + _facts_text[-600:]
                        )
                    if seen_probes:
                        _probe_ctx += (
                            "\n\nPREVIOUS PROBES (already used — analyze "
                            "coverage, do NOT repeat):\n"
                            + "\n".join(f"- {p}" for p in seen_probes)
                        )
                    if feedback:
                        _probe_ctx += "\n\n" + feedback
                    if _pending_gap:
                        # Gap-driven probe: the judge said the material is
                        # insufficient and named the missing detail — the
                        # next probe must target exactly that gap.
                        _probe_ctx += (
                            "\n\nGAP TO CLOSE (target ONLY this gap — "
                            "write ONE question that would find the missing "
                            "detail):\n" + _pending_gap
                        )
                    try:
                        _candidate = probe_generator(_probe_ctx, _facts_text)
                    except Exception:
                        _candidate = None
                    _cands = _coerce_probes(_candidate)
                    if not _cands:
                        _attempt_log.append(
                            ("<empty>", "the generator returned nothing usable")
                        )
                        log_block(
                            logger, logging.WARNING, "ComoRAG Probe",
                            "the generator returned nothing usable — using the "
                            "goal anchor",
                        )
                        break
                    # ComoRAG entity-priority probing: keep EVERY accepted
                    # candidate (up to 3) so this cycle sweeps several
                    # evidence paths instead of one.  A repeat is skipped,
                    # not fatal, so a 3-probe answer carrying one stale
                    # question still contributes its two fresh probes.
                    _accepted: List[str] = []
                    _rejected: List[str] = []
                    _repeats = 0
                    for _cand in _cands:
                        if _probe_repeats(_cand, seen_probes):
                            _rejected.append("%r was already asked" % _cand[:80])
                            _repeats += 1
                            continue
                        if any(_probe_similar(_cand, a) for a in _accepted):
                            # Same target, other words: keep this cycle's
                            # sweep DIVERSE instead of paying a retrieval + a
                            # composer call per phrasing.
                            _rejected.append(
                                "%r targets the same thing as an accepted "
                                "probe" % _cand[:80]
                            )
                            continue
                        _ok, _reason = self._validate_probe(
                            _cand, prompt, _context_text,
                        )
                        if _ok:
                            _accepted.append(_cand)
                        else:
                            _rejected.append(
                                "%r rejected: %s" % (_cand[:80], _reason)
                            )
                    if _accepted:
                        cycle_probes = _accepted
                        seen_probes.update(_accepted)
                        _pending_gap = ""  # these probes target the gap
                        log_block(
                            logger, logging.INFO,
                            "ComoRAG Probes (LLM attempt %d/%d)"
                            % (_attempt + 1, self._probe_retry_attempts + 1),
                            "\n".join("- " + p for p in _accepted),
                        )
                        if _rejected:
                            log_block(
                                logger, logging.INFO,
                                "ComoRAG Probes (skipped)",
                                "\n".join("- " + r for r in _rejected),
                            )
                        break
                    if _repeats and _repeats == len(_cands):
                        # Nothing NEW to ask: re-invoking the generator only
                        # reproduces the same question (observed: a small model
                        # re-emitted the field's own question on all 4 retries,
                        # ~17s each, before the fallback was even tried).  Break
                        # straight to the deterministic fallback instead of
                        # burning the retries, and SAY SO.
                        _attempt_log.append(
                            (repr(_cands)[:120],
                             "it repeats an already-used probe")
                        )
                        log_block(
                            logger, logging.INFO, "ComoRAG Probe",
                            "%r was already asked — the generator has no new "
                            "question, using the goal anchor"
                            % _cands[0][:120],
                        )
                        break
                    _attempt_log.append(
                        (repr(_cands)[:120], "; ".join(_rejected)[:200])
                    )
                    log_block(
                        logger, logging.WARNING,
                        "ComoRAG Probes rejected (attempt %d/%d)"
                        % (_attempt + 1, self._probe_retry_attempts + 1),
                        "%s\n\nRejected because: %s"
                        % ("\n".join(_cands)[:200],
                           "; ".join(_rejected)[:200]),
                    )
                    # Reflection feedback: the FULL failure history (chained
                    # failures must not repeat the same mistake) plus what to
                    # do better — the executor renders it as a structured
                    # REFLECTION template.
                    feedback = (
                        "PREVIOUS PROBE ATTEMPTS AND REJECTIONS (analyze the "
                        "pattern — do NOT repeat these mistakes):\n"
                        + "\n".join(
                            f"- attempt {i + 1}: {p!r} — rejected: {r}"
                            for i, (p, r) in enumerate(_attempt_log)
                        )
                        + "\n\nReflect on why these probes failed and what a "
                        "better probe would target, then write a corrected "
                        "question using only terms from the material, "
                        "ending with '?'."
                    )
            if not cycle_probes:
                # LLM probes unavailable or rejected — fall back to the
                # GOAL/fact ANCHOR only.  Document SECTIONS are deliberately
                # NOT probed: a section heading is not a question about the
                # field, so it retrieves unrelated material that the composer
                # then answers from (observed: 'Skills' and 'Presentation
                # Letter (Cover Letter)' as probes for unrelated fields).  The
                # goal sentence already IS the right retrieval query, and
                # every cycle is anchored on the request anyway.
                followup = self._extract_followup_probe(_facts_text)
                if followup and not _probe_repeats(followup, seen_probes):
                    probe = followup
                    seen_probes.add(probe)
                    log_block(
                        logger, logging.INFO,
                        "ComoRAG Probe (fact-grounded follow-up)", probe,
                    )
                else:
                    probe = next(
                        (p for p in lexical_probes
                         if not _probe_repeats(p, seen_probes)),
                        None,
                    )
                    if probe is not None:
                        seen_probes.add(probe)
                        log_block(
                            logger, logging.INFO,
                            "ComoRAG Probe (goal anchor, no generator)", probe,
                        )
                if probe is not None:
                    cycle_probes = [probe]
            if not cycle_probes:
                # Nothing left to probe — no progress possible.
                consecutive_invalid += 1
                if consecutive_invalid >= 2:
                    log_block(
                        logger, logging.WARNING, "ComoRAG Probe",
                        "no probes remain — stopping after %d consecutive "
                        "invalid cycles (%d composed fact(s))"
                        % (consecutive_invalid,
                           len(self._counted_facts(facts))),
                    )
                    break
                completed_cycles += 1
                continue

            # Retrieve evidence for EVERY probe of this cycle (ComoRAG
            # entity-priority probes: one search path each), keeping per-probe
            # attributions so the responder composes findings per probe.
            relevant: List[str] = []
            per_probe_evidence: List[Tuple[str, List[str]]] = []
            for _p in cycle_probes:
                _rel = self._retrieve_for_probe(
                    _p, all_chunks, chunk_embs,
                    exclude_indices=self._retrieved_indices,
                )
                _rel_new = [c for c in _rel if c not in relevant]
                if not _rel_new:
                    continue
                relevant.extend(_rel_new)
                per_probe_evidence.append((_p, _rel_new))
            # Anchor EVERY cycle to the ACTUAL user request: retrieve with
            # the request as well, so the evidence is never hostage to a
            # poor probe — the facts stay grounded in the user's situation
            # even when a cycle-1 probe misfires.
            _req_text = str(prompt or "").strip()
            if _req_text and all(
                _req_text[:500] != str(_p)[:500] for _p in cycle_probes
            ):
                _anchor = self._retrieve_for_probe(
                    _req_text[:500], all_chunks, chunk_embs,
                    exclude_indices=self._retrieved_indices,
                )
                _anchor_new = [a for a in _anchor if a not in relevant]
                if _anchor_new:
                    relevant.extend(_anchor_new)
                    # The request-anchored evidence is a RETRIEVAL PATH, not
                    # just filler: the composer and the verbatim harvest must
                    # see it, otherwise no fact is ever grounded on the user's
                    # own request when the probe misfires — which is the one
                    # thing this anchor exists to guarantee.  Un-attributed,
                    # the cycle also did real work while producing no facts,
                    # so the loop kept re-running it.
                    per_probe_evidence.append((_req_text[:500], _anchor_new))
            if not relevant:
                # Invalid cycle: no evidence cleared the probe's relevance gate
                # — do NOT count it toward the fact quota, but DO consume
                # budget.  Retrieval now fails closed (no document-order
                # fallback), so a probe with no lexical bridge to the corpus
                # retrieves nothing by design; without consuming budget the
                # loop would spin the same empty cycle until the probes ran
                # out.  Two in a row mean the sweep has drifted off the corpus.
                consecutive_invalid += 1
                log_block(
                    logger, logging.WARNING, "ComoRAG Evidence",
                    "no evidence for %r — no chunk shares a content token "
                    "with the probe (cycle not counted)" % str(_p)[:80],
                )
                if consecutive_invalid >= 2:
                    log_block(
                        logger, logging.WARNING, "ComoRAG Cycle",
                        "%d consecutive cycles retrieved no relevant evidence "
                        "— stopping the probe loop" % consecutive_invalid,
                    )
                    break
                completed_cycles += 1
                continue
            consecutive_invalid = 0
            # Mark retrieved chunk indices (index map: chunks are unique).
            _chunk_index = {ch: idx for idx, ch in enumerate(all_chunks)}
            for ch in relevant:
                _idx = _chunk_index.get(ch)
                if _idx is not None:
                    self._retrieved_indices.add(_idx)

            # Per-cycle char budget: cap THIS cycle's evidence only, but
            # accumulate the VERBATIM chunks across cycles — the final
            # synthesis is grounded on this source pool, never on
            # self-generated facts alone.
            # The budget is a CEILING for oversized chunks, never a cap on
            # how many of the retrieved chunks survive: with chunk_size 1000
            # and the 1500-char default it kept ONE chunk, so top_k=3 and
            # top_k=1 retrieved identically and the value-bearing chunk
            # (which often ranks 2nd/3rd) never reached the consumer
            # (observed: the LinkedIn URL sat in the header chunk while
            # retrieval returned the chunk that merely mentioned "LinkedIn").
            _cycle_budget = max(self._char_budget, self._top_k * self._chunk_size)
            _cycle_evidence = _cap_chunks_chars(relevant, _cycle_budget)
            # Dedupe: a later cycle that re-picks an earlier chunk must not
            # append it AGAIN.  The pool is handed to consumers as the verbatim
            # ground truth, so repeats both waste the window and inflate the
            # evidence (observed: the pool growing 675 -> 1682 -> 2689 chars
            # over 3 cycles, past its own per-cycle budget).
            _fresh: List[str] = []
            for _c in _cycle_evidence:
                if _c not in evidence_pool and _c not in _fresh:
                    _fresh.append(_c)
            evidence_pool.extend(_fresh)
            if _fresh:
                _fresh_text = "\n\n---\n\n".join(_fresh)
                _evidence_text = (
                    _evidence_text + "\n\n---\n\n" + _fresh_text
                    if _evidence_text else _fresh_text
                )
            log_table(
                logger, logging.INFO, "ComoRAG Evidence",
                [
                    ("Probes", "; ".join(p[:60] for p in cycle_probes)[:160]),
                    ("Chunks this cycle", "%d (%d new)" % (
                        len(_cycle_evidence), len(_fresh))),
                    ("Evidence pool", "%d chars accumulated (this cycle "
                                      "<= %d)"
                     % (len(_evidence_text), _cycle_budget)),
                ],
            )

            # The LLM RESPONDS to each probe feeding from THAT probe's
            # retrieved SOURCES; the answer's COMPOSED facts enter the curated
            # FACT POOL.  Raw evidence is stored only as a tagged fallback —
            # it never counts toward the quota and is excluded from the
            # exposed fact-pool variable.
            new_fact_entries: List[str] = []
            _kept_composed = 0
            _rejected_findings = 0
            # Did the composer actually answer this cycle?  Only THEN can
            # "no new composed fact" mean a stall: with NO responder the sweep
            # is retrieval-only and fresh evidence IS the progress (the corpus
            # is consumed until it is exhausted).
            _responder_ran = False
            # DYNAMIC CUT (Stop-RAG): the goal-answerable check runs after
            # EVERY probe — not once per cycle — so the sweep ends the moment
            # the accumulated material can answer, and never generates probes
            # for facts it no longer needs.  A grounded judge ANSWER cuts the
            # whole sweep; a MISSING verdict seeds the next probe with the gap.
            _cut = False

            def _facts_view():
                _parts = []
                if _facts_text:
                    _parts.append(_facts_text)
                if new_fact_entries:
                    _parts.append("\n\n---\n\n".join(new_fact_entries))
                return "\n\n---\n\n".join(_parts)

            def _judge_cut():
                nonlocal _answer_draft, _pending_gap, _cut
                if judge is None or _answer_draft:
                    return False
                _view = _facts_view()
                if not _view.strip():
                    return False
                try:
                    _verdict = judge(
                        str(prompt or "")[:400],
                        grounding_window(_view, self._judge_facts_chars),
                        grounding_window(
                            _evidence_text, self._judge_evidence_chars,
                        ),
                    )
                except Exception:
                    _verdict = None
                _kind, _payload = _parse_judge_verdict(_verdict)
                if _kind == "answer":
                    if (not _payload.strip()
                            or _NEGATIVE_FINDING_RE.search(_payload)):
                        # An "answer" that carries no value — or is itself an
                        # absence ("not provided") — is not an answer.
                        log_block(
                            logger, logging.WARNING, "ComoRAG Judge",
                            "the verdict claimed an answer but carried no "
                            "value — continuing to probe",
                        )
                        return False
                    _draft_viol = grounding_violations(
                        _payload, _evidence_text, _verify_allow,
                    )
                    if _draft_viol:
                        # GROUNDING: a draft asserting terms the evidence does
                        # not contain is not an answer.
                        log_block(
                            logger, logging.WARNING, "ComoRAG Judge",
                            "the verdict claimed an answer but asserts "
                            "terms absent from the evidence (%s) — "
                            "continuing to probe"
                            % ", ".join(_draft_viol[:6]),
                        )
                        return False
                    # STOP (Stop-RAG decision): a grounded answer that covers
                    # the goal ends the sweep HERE, at the FIRST probe that
                    # produced it — no cycle floor, no extra rounds.
                    _answer_draft = _payload
                    log_block(
                        logger, logging.INFO, "ComoRAG Judge",
                        "goal answerable after %d composed fact(s) — "
                        "cutting the sweep (cycle %d/%d)\nDraft: %s"
                        % (len(self._counted_facts(facts)),
                           completed_cycles + 1, self._max_probes,
                           _payload[:300]),
                    )
                    _cut = True
                    return True
                if _kind == "missing":
                    _pending_gap = _payload
                    if _payload:
                        log_block(
                            logger, logging.INFO, "ComoRAG Judge",
                            "gap to close -> %s" % _payload[:300],
                        )
                return False

            if responder is not None:
                for _p, _p_chunks in per_probe_evidence:
                    # The hook's view of its own evidence: head+tail, so the
                    # hook's trailing tail-slice is a no-op and nothing it must
                    # ground on is silently missing from the head.
                    _p_evidence = grounding_window(
                        "\n\n---\n\n".join(_p_chunks),
                        self._responder_evidence_chars,
                    )
                    try:
                        _answer = responder(_p, _p_evidence)
                    except Exception:
                        _answer = None
                    if _answer and str(_answer).strip():
                        _responder_ran = True
                        # GROUNDING GUARD: each finding is admitted to the
                        # fact pool ONLY when it is traceable to THIS probe's
                        # evidence.  A finding that reports the value is ABSENT
                        # is not knowledge, and one without a Support quote (or
                        # whose quote is not actually in the evidence) is a
                        # FABRICATED claim that would out-shout the real value
                        # in the consumer's distilled-fact block.  Both kinds
                        # fall back to the retrieved chunks as tagged
                        # (non-counted) facts, so the CLUE the probe found is
                        # never lost along with the fabrication and the loop
                        # keeps sweeping instead of closing on an empty finding.
                        _grounded: List[str] = []
                        _absent = 0
                        _fabricated = 0
                        _dropped: List[str] = []
                        # Only ABSENCE and FABRICATION are judged per cycle —
                        # they decide what ENTERS the pool.  Topicality is
                        # judged ONCE at the final synthesis (see the purge
                        # below), so the sweep accumulates freely and the
                        # curation verdict never steers the next probe.
                        for _finding in _split_findings(_answer):
                            if _is_negative_finding(_finding):
                                _absent += 1
                                _dropped.append(_finding)
                            elif not _is_grounded_finding(
                                _finding, _p_evidence, _verify_allow,
                            ):
                                _fabricated += 1
                                _dropped.append(_finding)
                            else:
                                _grounded.append(_finding)
                        if _absent or _fabricated:
                            # The rejected text is logged (head) — a
                            # discarded answer is otherwise invisible,
                            # which is exactly what made "every finding
                            # was ungrounded" impossible to diagnose.  It is
                            # logged even when NOTHING survived the gates.
                            log_block(
                                logger, logging.WARNING, "ComoRAG Facts",
                                "dropped %d absent and %d ungrounded "
                                "finding(s) for probe %r\n%s"
                                % (_absent, _fabricated,
                                   str(_p)[:80],
                                   "\n".join(
                                       d[:200] for d in _dropped[:3]
                                   )),
                            )
                        if _grounded:
                            _kept_composed += len(_grounded)
                            # Probe-attributed COMPOSED finding (ComoRAG memory
                            # fusion format: "probe : X\nFinding : Y"),
                            # tagged with the cycle that produced it so the fact
                            # pool grows as a knowledge TREE over time — each
                            # cycle branches from the facts of the previous one.
                            for _finding in _grounded:
                                new_fact_entries.append(
                                    f"[cycle {completed_cycles + 1}] "
                                    f"probe: {_p}\n{_finding}"
                                )
                            log_block(
                                logger, logging.INFO,
                                "ComoRAG Facts (cycle %d)"
                                % (completed_cycles + 1),
                                "%s\n%s" % (str(_p)[:120],
                                              "\n\n".join(_grounded)),
                            )
                        else:
                            _rejected_findings += (_absent + _fabricated)
                            new_fact_entries.extend(
                                f"evidence: {_ev}" for _ev in _p_chunks
                            )
                            log_block(
                                logger, logging.WARNING, "ComoRAG Facts",
                                "every finding for probe %r was absent, "
                                "unsupported or ungrounded (%d absent, %d "
                                "ungrounded) — the retrieved evidence is kept "
                                "as tagged (non-counted) facts and probing "
                                "continues\n%s"
                                % (str(_p)[:80], _absent, _fabricated,
                                   "\n".join(d[:200] for d in _dropped[:3])),
                            )
                    else:
                        new_fact_entries.extend(
                            f"evidence: {_ev}" for _ev in _p_chunks
                        )
                        log_block(
                            logger, logging.WARNING, "ComoRAG Facts",
                            "the responder produced nothing for probe %r — "
                            "the retrieved evidence is kept as tagged "
                            "(non-counted) facts" % str(_p)[:80],
                        )
                    # DYNAMIC CUT: judge THIS probe's accumulated material
                    # before spending the next probe/cycle on it.
                    if _judge_cut():
                        break
            else:
                new_fact_entries.extend(
                    f"evidence: {_ev}" for _ev in _cycle_evidence
                )
            if new_fact_entries:
                # Digest-to-facts dedupe (ComoRAG memory pool): a probe that
                # only re-confirms an already-composed finding must NOT grow
                # the fact pool — repeated findings previously diluted the
                # synthesis and burned a full LLM generation per repeat.
                _composed_new = [
                    e for e in new_fact_entries
                    if not str(e).lstrip().lower().startswith("evidence:")
                ]
                kept_entries = _dedupe_fact_entries(new_fact_entries, facts)
                if kept_entries:
                    facts.extend(kept_entries)
                    _new_text = "\n\n---\n\n".join(kept_entries)
                    _facts_text = (
                        _facts_text + "\n\n---\n\n" + _new_text
                        if _facts_text else _new_text
                    )
                elif _composed_new:
                    log_block(
                        logger, logging.WARNING, "ComoRAG Facts",
                        "the responder's findings only repeat already-composed "
                        "facts — this cycle adds no new knowledge",
                    )
            if _cut:
                # A per-probe judge ANSWER ended the sweep inside the loop;
                # the facts composed so far are already pooled.
                break
            new_facts = len(self._counted_facts(facts)) - facts_before
            # Stall guard: consecutive cycles where the composer ANSWERED but
            # added NO new composed fact mean the corpus has nothing more to say
            # for this goal — stop instead of re-sweeping until the cycle
            # ceiling.  It has its OWN counter: ``consecutive_invalid`` is reset
            # by a successful retrieval, so a field whose findings were rejected
            # wholesale (every entry tagged ``evidence:``, the common case on a
            # weak model) reset it on every cycle and kept probing until the
            # ceiling burned the whole budget.  A retrieval-only sweep (no
            # responder) is NOT a stall.  There is deliberately NO `continue`
            # here: the evidence-exhaustion check below must still run, or an
            # exhausted corpus is swept again.
            if responder is not None and _responder_ran and new_facts == 0:
                _stalled_cycles += 1
                if _stalled_cycles >= 2:
                    log_block(
                        logger, logging.WARNING, "ComoRAG Cycle",
                        "%d consecutive cycles added no new composed fact "
                        "— stopping the probe loop" % _stalled_cycles,
                    )
                    break
            else:
                _stalled_cycles = 0
            # The goal-answerable check now runs after EVERY probe inside the
            # responder loop (see _judge_cut above), so no judge call is needed
            # at the cycle boundary — the sweep is cut the moment it can answer.
            verdict = "continuing (below the %d-fact quota)" % self._min_facts
            if new_facts >= self._min_facts:
                completed_cycles += 1
                verdict = "DONE"
            elif self._remaining_chunks() <= 0:
                verdict = "evidence pool exhausted — stopping"
            log_table(
                logger, logging.INFO,
                "ComoRAG Cycle %d/%d" % (completed_cycles or 1,
                                         self._max_probes),
                [
                    ("Composed facts this cycle", new_facts),
                    ("Fact pool", "%d entry/entries (%d chars)" % (
                        len(self._counted_facts(facts)), len(_facts_text))),
                    ("Evidence pool", "%d chunk(s)" % len(evidence_pool)),
                    ("Verdict", verdict),
                    # GROUNDING ACCOUNTING: a run must be auditable — what
                    # the composer's findings were kept and what was refused.
                    ("Grounding", "%d composed kept, %d rejected"
                     % (_kept_composed, _rejected_findings)),
                ],
            )
            if new_facts >= self._min_facts:
                pass
            elif self._remaining_chunks() <= 0:
                break
            # else: 0 < new_facts < min_facts -> stay in the same cycle
            # slot; the next probe deepens this cycle's fact layer.
        else:
            # Ceiling reached without a verdict/saturation stop.
            log_block(
                logger, logging.INFO, "ComoRAG Cycle",
                "cycle ceiling reached (%d) — stopping the probe loop "
                "(%d composed fact(s))"
                % (self._max_probes, len(self._counted_facts(facts))),
            )

        # -- Stage 3.5: PURGE off-topic facts ONCE, at the synthesis --
        # The relevance verdict runs HERE, on the FINISHED pool, instead of
        # per cycle: the sweep accumulates freely and curation never steers a
        # probe.  Purging before the synthesis and before the exposed fact
        # pool means nothing off-topic is shipped.  A cheap lexical gate runs
        # first; the semantic verdict then judges the survivors (fail open on
        # an unavailable/unparseable verdict — never drop a real fact).
        if facts:
            _composed_idx = [
                _i for _i, _e in enumerate(facts)
                if not str(_e).lstrip().lower().startswith("evidence:")
            ]
            if _composed_idx:
                _claims = {
                    _i: (_finding_body(facts[_i]) or facts[_i])
                    for _i in _composed_idx
                }
                _survivors = [
                    _i for _i in _composed_idx
                    if _is_relevant_finding(_claims[_i], _focus_tokens)
                ]
                _drop = set(_composed_idx) - set(_survivors)
                if relevance_judge and _survivors:
                    try:
                        _keep = relevance_judge(
                            str(prompt or ""),
                            [_claims[_i] for _i in _survivors],
                        )
                    except Exception as _rel_exc:
                        logger.warning(
                            "ComoRAG relevance verdict failed: %s", _rel_exc,
                        )
                        _keep = None
                    if (isinstance(_keep, (list, tuple))
                            and len(_keep) == len(_survivors)):
                        _drop |= {
                            _i for _i, _k in zip(_survivors, _keep)
                            if not _k
                        }
                if _drop:
                    facts = [
                        _e for _i, _e in enumerate(facts)
                        if _i not in _drop
                    ]
                    _facts_text = "\n\n---\n\n".join(facts)
                    log_block(
                        logger, logging.INFO, "ComoRAG Purge",
                        "purged %d off-topic fact(s) at synthesis (%d kept)"
                        % (len(_drop), len(facts)),
                    )

        if not facts:
            # No facts at all, but sources were retrievable — fall back to
            # the intent-anchored excerpt so the node never runs with zero
            # context (the corpus is still accessed through retrieval, never
            # truncated).
            log_block(
                logger, logging.WARNING, "ComoRAG",
                "no facts were accumulated — falling back to intent-anchored "
                "retrieval",
            )
            _fallback = self._lexical_excerpt(prompt, all_chunks)
            if _fallback:
                facts.append(f"evidence: {_fallback}")
                _facts_text = _fallback
        if not facts:
            self._facts_pool = []
            self._evidence_pool = list(evidence_pool)
            return ""

        # The curated fact pool (workflow variables) holds COMPOSED facts
        # only — evidence-tagged fallback entries stay out of it (they still
        # ground the synthesis via ``_evidence_text``).
        self._facts_pool = [
            f for f in facts
            if not str(f).lstrip().lower().startswith("evidence:")
        ]
        self._evidence_pool = list(evidence_pool)

        # -- Stage 4: Final synthesis of ALL accumulated facts (ComoRAG) --
        if response_generator is not None and not (
            stop_flag is not None and stop_flag()
        ):
            try:
                log_table(
                    logger, logging.INFO, "ComoRAG Synthesis",
                    [
                        ("Accumulated facts", len(facts)),
                        ("Evidence chars", len(_evidence_text)),
                        ("Judge candidate", "yes" if _answer_draft else "no"),
                    ],
                )
                _synth_facts = _facts_text
                if _answer_draft:
                    # The judge's draft rides into the synthesis as a
                    # LABELED candidate: the synthesizer validates every
                    # fact against the verbatim evidence and omits anything
                    # unsupported, so a premature/confident-but-wrong draft
                    # cannot leak into the final response.
                    _synth_facts = (
                        _facts_text
                        + "\n\n---\n\nJUDGE CANDIDATE ANSWER (verify "
                        "against the evidence before adopting; omit if "
                        "unsupported):\n" + _answer_draft
                    )
                _synth_evidence = grounding_window(
                    _evidence_text, self._synth_evidence_chars,
                )
                consolidated = response_generator(
                    intent_prompt,
                    grounding_window(_synth_facts, self._synth_facts_chars),
                    _synth_evidence,
                )
                if consolidated and str(consolidated).strip():
                    _final = str(consolidated).strip()
                    # Verified against the SAME view the synthesizer saw: a
                    # claim taken from text it never received is unverifiable.
                    _synth_viol = grounding_violations(
                        _final, _synth_evidence, _verify_allow,
                    )
                    if _synth_viol:
                        # GROUNDING (fail-closed): the synthesis is the value
                        # the CONSUMER reads.  A synthesis that asserts terms
                        # the verbatim evidence does not contain is not
                        # returned at all — the grounded facts/evidence are
                        # returned instead, so an invented value can never
                        # travel downstream under a trustworthy label.
                        log_block(
                            logger, logging.WARNING, "ComoRAG Synthesis",
                            "the synthesis asserted %d term(s) absent from "
                            "the verbatim evidence (%s) — discarding it and "
                            "returning the grounded facts"
                            % (len(_synth_viol), ", ".join(_synth_viol[:6])),
                        )
                        return _facts_text
                    log_block(
                        logger, logging.INFO,
                        "ComoRAG Final Synthesis (%d chars)" % len(_final),
                        _final,
                    )
                    return _final
                log_block(
                    logger, logging.WARNING, "ComoRAG Synthesis",
                    "the synthesis produced nothing — returning the "
                    "accumulated facts",
                )
            except Exception as exc:
                log_block(
                    logger, logging.WARNING, "ComoRAG Synthesis",
                    "the synthesis failed (%s) — returning the accumulated "
                    "facts" % exc,
                )

        return _facts_text

    # ------------------------------------------------------------------
    #  Curated fact pool (workflow variables)
    # ------------------------------------------------------------------

    @property
    def facts_pool(self) -> List[str]:
        """The curated fact pool from the last ``consolidate()`` run.

        Facts are variables, sources are sources: this pool holds the
        distilled, doc-grounded facts that the executor stores as workflow
        variables so the LLM can use them outside the injected context too
        (and keep them even when the raw context is long).
        """
        return list(self._facts_pool)

    @property
    def evidence_pool(self) -> List[str]:
        """The VERBATIM source chunks retrieved during the last run.

        Sources are sources: a composed fact is a paraphrase, so an exact
        value (URL, email, date, number) is only guaranteed here.  Consumers
        that need exact values ground on this pool; ``facts_pool`` stays the
        distilled summary.
        """
        return list(self._evidence_pool)

    def _counted_facts(self, facts: List[str]) -> List[str]:
        """Composed facts only — raw-evidence entries (tagged ``evidence:``)
        never count toward the per-cycle fact quota."""
        return [
            f for f in facts
            if not str(f).lstrip().lower().startswith("evidence:")
        ]

    def _remaining_chunks(self) -> int:
        """Number of chunk indices not yet retrieved (excluded)."""
        return max(0, len(self._cached_chunks) - len(self._retrieved_indices))

    # ------------------------------------------------------------------
    #  Stage 1: Probe extraction
    # ------------------------------------------------------------------

    def _extract_probes(self, prompt: str) -> List[str]:
        """Extract 1-3 probing queries from the LLM prompt via lexical analysis.
        No model calls — pure keyword/pattern extraction.
        """
        if not prompt or not prompt.strip():
            return []

        probes: List[str] = []
        # Extract explicit questions (ending with ?)
        for line in prompt.split("\n"):
            line = line.strip()
            if "?" in line:
                q_idx = line.rfind("?")
                question = line[max(0, line.rfind(".", 0, q_idx) + 1) : q_idx + 1].strip()
                if len(question) > 5:
                    probes.append(question)

        # If no questions found, use key sentences containing what/why/how
        if not probes:
            for line in prompt.split("\n"):
                low = line.lower()
                for kw in ("what ", "why ", "how ", "which ", "where "):
                    if kw in low:
                        probes.append(line.strip()[:150])
                        break

        # Last resort: use the first 200 chars of the prompt
        if not probes:
            probes = [prompt[:200]]

        return probes[:3]

    def _extract_followup_probe(self, facts_text: str) -> Optional[str]:
        """Derive a fact-grounded follow-up probe from the accumulated facts.

        Used when the LLM probe generator returns nothing or repeats: the
        next probe comes from the most recent composed fact's ``Finding:``
        line (or the key statement of an evidence-tagged entry), so probing
        continues toward what was just learned instead of collapsing the
        cycle budget.
        """
        if not facts_text or not str(facts_text).strip():
            return None
        last_fact = str(facts_text).strip().split("\n\n---\n\n")[-1].strip()
        _lines = last_fact.splitlines()
        # Knowledge-tree entries carry a "[cycle N]" tag — drop it (and any
        # probe-attribution line) so the derived probe targets the content.
        if _lines and re.match(r"^\[cycle \d+\]", _lines[0].strip().lower()):
            last_fact = "\n".join(_lines[1:]).strip() or last_fact
        _lines = last_fact.splitlines()
        if _lines and _lines[0].lower().startswith("probe"):
            last_fact = "\n".join(_lines[1:]).strip() or last_fact
        # Evidence-tagged fallback entries: drop the "evidence:" tag so the
        # derived probe targets the chunk content, not the tag (a single-line
        # entry must not fall back to the tagged text).
        _lines = last_fact.splitlines()
        if _lines and _lines[0].lower().startswith("evidence:"):
            last_fact = _lines[0][len("evidence:"):].strip() or last_fact
        _lines = last_fact.splitlines()
        # Composed entries carry "Finding: <statement>" — prefer the first
        # finding's claim as the next probe target.
        for line in _lines:
            _m = re.match(r"^\s*(?:key\s+)?finding\s*:\s*(.+)$", line, re.IGNORECASE)
            if _m and len(_m.group(1).strip()) > 8:
                return _m.group(1).strip()[:160]
        # Evidence-tagged or bare entries: first statement carries the claim.
        for sep in (". ", ".\n", "\n"):
            idx = last_fact.find(sep)
            if 8 < idx < 160:
                head = last_fact[: idx + 1].strip()
                if len(head) > 8:
                    return head
        head = last_fact[:120].strip()
        return head if len(head) > 8 else None

    def _validate_probe(self, probe, goal, context_text) -> Tuple[bool, str]:
        """Reject probes that rephrase the user query or invent terms.

        Returns ``(accepted, reason)``: a TARGETED probe must (1) be a
        question, (2) not be a near-paraphrase of the goal, and (3) not
        invent entity-like terms absent from the available context.  A
        small LLM otherwise echoes the request ("what could arrow do for
        this person?") or invents product names ("LooperOS") — both get
        rejected here and the reason feeds the critique-driven retry (the
        same model re-probes with feedback).  Ordinary lowercase probe
        vocabulary is exempt by design: requiring it verbatim false-
        rejects valid probes against raw PDFs, OCR text and foreign-
        language feeds (observed: "potential employer based on contextual
        information" rejected 4/4 on a resume + LinkedIn run even though
        the targeted person was present in the context).
        """
        _probe_l = str(probe or "").lower().strip()
        _goal_l = str(goal or "").lower()
        _probe_orig = str(probe or "")
        if not _probe_l:
            return False, "the probe is empty"
        # 0) LLM probes must be questions — a fragment like "Target entity
        #    or section:" (an echoed prompt header) is never a probe.  A
        #    truncated generation that still starts with an interrogative
        #    ("Who ... based on co") is accepted: the '?' was cut by the
        #    token budget, not absent by intent.
        if "?" not in _probe_l and not _starts_interrogative(_probe_l):
            return False, "the probe is not a question (missing '?')"
        # Tokenize for a fair overlap comparison: punctuation stripped so
        # "screen?" matches "screen", token-set membership instead of
        # substring ("the" must not match "other").  Goal words keep
        # short function words so a near-rephrase of the request is caught.
        def _tokens(text):
            return [w for w in re.findall(r"[a-z0-9]+", text) if len(w) >= 3]

        _probe_tokens = _tokens(_probe_l)
        _goal_tokens = _tokens(_goal_l)
        # 1) Anti-rephrase: reject only a near-verbatim ECHO of the goal —
        #    high overlap AND no new content detail.  Goal-derived probes
        #    that reuse the request's wording are the backbone of ComoRAG
        #    probing (observed regression: a 50%-overlap probe like "What
        #    is the user currently doing on screen?" was rejected, chaining
        #    failures that skipped consolidation entirely).
        if _goal_tokens:
            _overlap = sum(1 for w in _goal_tokens if w in _probe_tokens) / len(
                _goal_tokens
            )
            _new_detail = [
                w for w in _probe_tokens
                if w not in _goal_tokens and w not in _STOPWORDS and len(w) >= 5
            ]
            if _overlap >= 0.8 and not _new_detail:
                return (
                    False,
                    "it is a near-verbatim echo of the user request (word "
                    "overlap %.0f%%, no new detail)" % (_overlap * 100),
                )
        # 2) Anti-hallucination: only ENTITY-LIKE new terms — capitalized
        #    in the original probe (proper nouns / product names such as
        #    "LooperOS") — must appear verbatim in the context.  Ordinary
        #    lowercase English probe vocabulary is exempt: raw PDFs, OCR
        #    screen text and foreign-language feeds rarely spell out words
        #    like "potential"/"employer", yet they are legitimate probe
        #    language.  The sentence-initial word is skipped (interrogatives
        #    like "Who" are capitalized by grammar, not because they are
        #    entities); a lowercase hallucination may slip through but only
        #    wastes one cycle — it retrieves no evidence and the responder
        #    validates facts against what was actually retrieved.
        _ctx_l = str(context_text or "").lower()
        _content_words = [
            w for w in _probe_tokens
            if w not in _STOPWORDS and w not in _COMMON_WORDS
            and len(w) >= 5 and w not in _goal_tokens
        ]
        if _content_words:
            _missing = [w for w in _content_words if w not in _ctx_l]
            _first_tok = ""
            _m0 = re.match(r"\W*([A-Za-z][A-Za-z0-9'-]*)", _probe_orig)
            if _m0:
                _first_tok = _m0.group(1).lower()
            _entity_like = {
                w.lower()
                for w in re.findall(r"[A-Z][A-Za-z0-9'-]*", _probe_orig)
                if w.lower() != _first_tok
            }
            _entity_missing = [w for w in _missing if w in _entity_like][:5]
            if _entity_missing:
                return (
                    False,
                    "it invents terms absent from the context: %s"
                    % ", ".join(_entity_missing),
                )
        return True, ""

    def _spread_excerpt(self, text: str, max_chars: int,
                        segments: int = 6) -> str:
        """Bounded excerpt that samples the WHOLE source, not just its head.

        The probe generator's base context used to be a raw PREFIX, so on a
        document longer than the budget every term past the cut stayed
        invisible to probing (observed: a 20k-char resume exposed only its
        first 6k, so probes for late fields could not use its vocabulary).
        Evenly-spaced windows keep the late sections (experience, application
        details) reachable while the size stays bounded.
        """
        s = str(text or "")
        if max_chars <= 0 or len(s) <= max_chars or segments <= 1:
            return s
        seg_len = max(1, int(max_chars) // segments)
        step = len(s) / float(segments)
        parts = [
            s[int(i * step): int(i * step) + seg_len]
            for i in range(segments)
        ]
        return "\n[...]\n".join(parts)

    def _build_context_excerpt(
        self, texts: List[str], max_chars: int = 2000,
        per_source: int = 700, tail_chars: int = 200,
    ) -> str:
        """Bounded excerpt of the available context for intent grounding.

        The full context is too large for a small probe model; the head
        (title/intro) and tail (conclusion) of each source carry the topic
        and entity signal needed to derive grounded probes.  Retrieval over
        the full chunk set remains the complete-context access path.
        """
        if not texts:
            return ""
        parts: List[str] = []
        total = 0
        for idx, text in enumerate(texts):
            s = str(text).replace("\x00", "").strip()
            if not s:
                continue
            head = s[:per_source]
            tail = s[-tail_chars:] if len(s) > per_source + tail_chars else ""
            excerpt = head + (f"\n[...]\n{tail}" if tail else "")
            room = max_chars - total
            if room <= 0:
                break
            if len(excerpt) > room:
                excerpt = excerpt[:room]
            parts.append(f"[source {idx + 1}]\n{excerpt}")
            total += len(excerpt)
            if total >= max_chars:
                break
        return "\n\n---\n\n".join(parts)

    def _build_enhanced_intent(
        self, prompt: str, system_prompt: str, context_excerpt: str,
    ) -> str:
        """Compose the enhanced intent prompt: goal + system + context.

        Probing queries derive from this intent so they stay grounded in
        the available context instead of drifting from the bare prompt.
        """
        parts: List[str] = []
        _sys = str(system_prompt or "").strip()
        _goal = str(prompt or "").strip()
        if _sys:
            parts.append(f"SYSTEM:\n{_sys[:600]}")
        if _goal:
            parts.append(f"GOAL (user request):\n{_goal[:800]}")
        if context_excerpt:
            parts.append(f"CONTEXT (available sources):\n{context_excerpt}")
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    #  Stage 2: Chunking + Embedding
    # ------------------------------------------------------------------

    def _build_chunks(
        self, texts: List[str], graphrag_context: str
    ) -> List[str]:
        """Chunk all context sources + optional GraphRAG context."""
        chunks: List[str] = []

        for text in texts:
            pieces = _chunk_text(text, self._chunk_size, self._overlap)
            for p in pieces:
                cleaned = p.replace("\x00", "").strip()
                if cleaned and len(cleaned) > 20:
                    chunks.append(cleaned)

        if graphrag_context and graphrag_context.strip():
            # Chunk the GraphRAG context like the rest of the sources so a
            # huge context never bypasses the per-cycle char budget (a single
            # unchunked blob previously leaked its full length into every
            # cycle's evidence).
            cleaned_grag = graphrag_context.replace("\x00", "").strip()
            if cleaned_grag:
                for p in _chunk_text(cleaned_grag, self._chunk_size, self._overlap):
                    cleaned_p = p.strip()
                    if cleaned_p and len(cleaned_p) > 20:
                        chunks.append(f"[GraphRAG Context]: {cleaned_p}")

        # Deduplicate near-identical chunks
        seen: set[str] = set()
        unique: List[str] = []
        for c in chunks:
            key = c[:100]
            if key not in seen:
                seen.add(key)
                unique.append(c)

        # Index the ENTIRE corpus — the ComoRAG protocol never caps the
        # retrieval pool: probes must be free to retrieve evidence from
        # anywhere in the attached documents.  A cap silently hides whole
        # sections from every probe (observed: a 44k-char / 22-page doc
        # reduced to 20 chunks left ~18 pages unreachable, so every probe
        # hit the intro/TOC).
        return unique

    def _llamacpp_embed(self, texts: List[str]) -> Optional[List[List[float]]]:
        """Embed *texts* via the llama.cpp embedding server (or injected client).

        Returns ``None`` on any failure so callers try the shared embedding
        server, then hard-break (EmbeddingFailureError) — the underlying
        reason is captured in ``self._last_embed_error``.  The injected
        client is either a callable ``(texts) -> vectors`` or an object
        with an ``embed(texts)`` method (e.g. the ``LlamaCppEngine`` in
        embedding mode).
        """
        client = self._embed_client
        if client is None:
            try:
                from AI import embedding_server
                client = embedding_server.get_embed_client()
            except Exception as exc:
                self._last_embed_error = (
                    f"embedding server client unavailable: {exc}"
                )
                return None
        if client is None:
            self._last_embed_error = (
                "no embedding client — llama.cpp embedding server not running"
            )
            return None
        try:
            if callable(client):
                return client(texts)
            return client.embed(texts)
        except Exception as exc:
            self._last_embed_error = f"{type(exc).__name__}: {exc}"
            logger.debug("llama.cpp embedding failed: %s", exc)
            return None

    def _compute_embeddings(
        self, chunks: List[str]
    ) -> Optional[List[List[float]]]:
        """Compute embeddings for all chunks.

        Priority:
          1. The node's embedding backend: the llama.cpp embedding server
             (embeddinggemma GGUF) or the injected Ollama client.
          2. The shared embedding server (retry path).
          3. None → caller raises EmbeddingFailureError (hard break) —
             embeddings are the only retrieval path.

        Ollama is never reached implicitly here: Ollama-engine nodes inject
        their client via ``embed_client`` (see the executor), so hardcoded
        ``nomic-embed-text`` fallbacks would mix vector spaces.
        """
        if not chunks:
            return None

        # Fresh failure-reason slate for this computation.
        self._last_embed_error = ""

        # ── Priority 1: Node embedding backend (server / injected client) ──
        llamacpp_embs = self._llamacpp_embed(chunks)
        if llamacpp_embs is not None and len(llamacpp_embs) == len(chunks):
            # Per-batch telemetry: kept at DEBUG so it does not break up the
            # boxy consolidation story (the ComoRAG Evidence table carries the
            # chunk counts that matter).
            logger.debug(
                "Consolidation: embedded %d chunks via llama.cpp server",
                len(chunks),
            )
            return llamacpp_embs

        # ── Priority 2: Shared embedding server (retry path) ─────────
        local = _local_embed(chunks)
        if local is not None and len(local) == len(chunks):
            logger.debug(
                "Consolidation: embedded %d chunks via local model",
                len(chunks),
            )
            return local

        # Compose the full reason chain for the hard-break diagnostic.
        self._last_embed_error = (
            f"{self._last_embed_error or 'node embedding backend returned no vectors'}"
            " | shared embedding server (embeddinggemma) returned no vectors"
        )
        return None

    def _embed_one(self, text: str) -> Optional[List[float]]:
        """Embed a single query text (probe / intent)."""
        for fn in (self._llamacpp_embed, _local_embed):
            try:
                embs = fn([text])
            except Exception:
                embs = None
            if embs and len(embs) == 1:
                return embs[0]
        return None

    def _embed_chunks_cached(
        self, texts: List[str],
    ) -> Optional[List[List[float]]]:
        """Embed *texts* lazily through the per-chunk vector cache.

        Only chunks never embedded before are sent to the backend (one
        batched call); every other chunk is a cache hit, so the same corpus
        touched by many probes (and many node executions) is encoded once
        per distinct chunk — never in one upfront shot.  Returns None on
        backend failure so callers treat it like any embedding failure.
        """
        global _CHUNK_VEC_CACHE_NAMESPACE
        if not texts:
            return []
        if self._cache_namespace != _CHUNK_VEC_CACHE_NAMESPACE:
            # A different embedding backend: its vectors are not comparable
            # with the cached ones, so the cache is dropped rather than
            # mixed.
            _CHUNK_VEC_CACHE.clear()
            _CHUNK_VEC_CACHE_NAMESPACE = self._cache_namespace
        try:
            import hashlib
        except Exception:
            hashlib = None
        keys: List[str] = []
        missing: List[str] = []
        out: List[Optional[List[float]]] = []
        for t in texts:
            k = (
                hashlib.md5(str(t).encode("utf-8", errors="ignore")).hexdigest()
                if hashlib else str(t)
            )
            keys.append(k)
            v = _CHUNK_VEC_CACHE.get(k)
            if v is None:
                missing.append(t)
                out.append(None)
            else:
                out.append(v)
        if missing:
            new_vecs = self._compute_embeddings(missing)
            if new_vecs is None or len(new_vecs) != len(missing):
                return None
            mi = 0
            for i in range(len(out)):
                if out[i] is None:
                    vec = new_vecs[mi]
                    mi += 1
                    _CHUNK_VEC_CACHE[keys[i]] = vec
                    _CHUNK_VEC_CACHE.move_to_end(keys[i])
                    while len(_CHUNK_VEC_CACHE) > _CHUNK_VEC_CACHE_MAX:
                        _CHUNK_VEC_CACHE.popitem(last=False)
                    out[i] = vec
        return out

    def _lexical_candidates(
        self, query: str, chunks: List[str],
        exclude_indices: Optional[set], limit: int,
    ) -> List[int]:
        """Cheap embedding-free candidate pre-select for one probe.

        Scores every not-yet-retrieved chunk by lexical token overlap with
        the probe and returns the top *limit* indices.

        GROUNDING: when NO chunk shares a content token with the probe there
        is no lexical bridge, and the pre-select returns NOTHING.  It used to
        return "the first *limit* remaining chunks" so probing would "explore"
        — that is retrieval by document POSITION, i.e. evidence with no
        relation to the probe at all, and the composer then grounded claims on
        it.  A probe that matches nothing retrieves nothing, and the cycle is
        reported as invalid instead of inventing a connection.
        """
        if not chunks or limit <= 0:
            return []
        q = _lex_tokens(query)
        if not q:
            return []
        scored: List[Tuple[int, int]] = []
        for idx, ch in enumerate(chunks):
            if exclude_indices is not None and idx in exclude_indices:
                continue
            ov = sum(1 for w in _lex_tokens(ch) if w in q)
            if ov > 0:
                scored.append((ov, idx))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [i for _, i in scored[:limit]]

    def _lexical_excerpt(
        self, query: str, chunks: List[str],
        max_chars: int = 2000, top_n: int = 5,
    ) -> str:
        """Top chunks by lexical overlap with the goal, joined within a
        char budget — the embedding-free intent-grounding excerpt."""
        cands = self._lexical_candidates(query, chunks, None, top_n * 4)
        picked: List[str] = []
        total = 0
        for i in cands:
            total += len(chunks[i])
            if picked and total > max_chars:
                break
            picked.append(chunks[i])
        return "\n\n---\n\n".join(picked) if picked else ""

    def _rank_vectors(
        self, q_vec: List[float], texts: List[str],
        vecs: List[List[float]], exclude_indices: Optional[set],
        top_k: int,
    ) -> List[str]:
        """Cosine-rank *texts* against the query vector, returning top_k.

        Vectorized path (numpy) with the scalar cosine fallback on any
        shape mismatch (ragged/object arrays).
        """
        try:
            _arr = np.asarray(vecs, dtype=np.float64)
            _q = np.asarray(q_vec, dtype=np.float64)
            if _arr.ndim != 2 or _q.ndim != 1 or _arr.shape[1] != _q.shape[0]:
                raise ValueError("embedding shape mismatch")
            _norms = np.linalg.norm(_arr, axis=1)
            _qnorm = np.linalg.norm(_q)
            _denom = _norms * _qnorm
            if _qnorm == 0.0 or not np.any(_denom > 0.0):
                return []
            _scores = np.divide(
                _arr @ _q, _denom,
                out=np.zeros(_arr.shape[0], dtype=np.float64),
                where=_denom > 0.0,
            )
            _order = np.argsort(-_scores)
            picked: List[str] = []
            for _pos in _order:
                _idx = int(_pos)
                if exclude_indices is not None and _idx in exclude_indices:
                    continue
                picked.append(texts[_idx])
                if len(picked) >= top_k:
                    break
            return picked
        except Exception:
            sims = [
                (_cosine_sim(q_vec, ce), idx)
                for idx, ce in enumerate(vecs)
                if (exclude_indices is None or idx not in exclude_indices)
            ]
            sims.sort(key=lambda x: x[0], reverse=True)
            return [texts[idx] for _, idx in sims[:top_k]]

    def _retrieve_for_probe(
        self,
        probe: str,
        chunks: List[str],
        chunk_embs: Optional[List[List[float]]],
        exclude_indices: Optional[set] = None,
    ) -> List[str]:
        """Retrieve top-k chunks for a single probe — WITHOUT embedding the
        whole corpus.

        Two modes:
          * ``chunk_embs`` provided (full precomputed index): cosine over
            everything (unchanged fast path for small/legacy corpora).
          * ``chunk_embs`` None (probe-driven, the DEFAULT): the probe's
            candidates are pre-selected by cheap lexical overlap, ONLY those
            chunks are embedded (cached per chunk), and cosine ranks them.
            This is the ComoRAG ingestion path: no initial full-context
            embedding — each probe diverts its own search to the evidence it
            needs and repeated chunks cost nothing (per-chunk cache).

        There is NO lexical-only fallback for the final evidence: the
        candidates are ranked by embedding, and when embeddings are
        unavailable the probe retrieves nothing (unranked context text never
        reaches the small model's prompt).
        """
        if not chunks:
            return []

        remaining = len(chunks) - len(exclude_indices) if exclude_indices else len(chunks)
        if remaining <= 0:
            return []

        q_vec = self._embed_one(probe)
        if q_vec is None:
            if chunk_embs is None:
                # Probe-driven mode: embeddings are the ONLY retrieval path —
                # an unavailable backend means the probe retrieves nothing and
                # the node would silently run without context.  Hard-break
                # exactly like the old upfront-ingest failure did.
                _why = str(getattr(self, "_last_embed_error", "") or "").strip()
                raise EmbeddingFailureError(
                    "Probe embedding failed — consolidation cannot retrieve "
                    "context without embeddings (no lexical fallback). "
                    + (f"Reason: {_why}" if _why else "Reason unknown.")
                )
            log_block(
                logger, logging.WARNING, "ComoRAG Probe",
                "probe embedding unavailable — no retrieval for probe %r "
                "(no lexical fallback by design)" % probe[:80],
            )
            return []

        # Full precomputed index (small corpus / legacy callers).
        if chunk_embs is not None and len(chunk_embs) == len(chunks):
            return self._rank_vectors(
                q_vec, chunks, chunk_embs, exclude_indices, self._top_k,
            )

        # Probe-driven lazy mode: lexical pre-select -> embed only candidates.
        pool = max(24, self._top_k * 6)
        cands = self._lexical_candidates(probe, chunks, exclude_indices, pool)
        if not cands:
            return []
        cand_texts = [chunks[i] for i in cands]
        cand_embs = self._embed_chunks_cached(cand_texts)
        if cand_embs is None or len(cand_embs) != len(cand_texts):
            _why = str(getattr(self, "_last_embed_error", "") or "").strip()
            raise EmbeddingFailureError(
                "Candidate embedding failed — consolidation cannot retrieve "
                "context without embeddings (no lexical fallback). "
                + (f"Reason: {_why}" if _why else "Reason unknown.")
            )
        return self._rank_vectors(q_vec, cand_texts, cand_embs, None, self._top_k)

    # ------------------------------------------------------------------
    #  Re-retrieval for recursive generation
    # ------------------------------------------------------------------

    def retrieve_for_text(self, text: str, top_k: int = 3) -> str:
        """Retrieve fresh context for arbitrary text (e.g., continuation).

        Uses cached chunks + embeddings from the initial ``consolidate()``
        pass. Extracts probes from only the *tail* of *text* (last 500 chars),
        re-retrieves top-k chunks per probe (excluding already-retrieved
        indices), and returns them joined. This lets each recursive generation
        step see different, contextually relevant vectors without repetition.

        Args:
            text: The text to extract probes from (e.g. latest generated chunk).
            top_k: Number of chunks to retrieve per probe.

        Returns:
            Joined relevant chunks, or empty string if nothing cached.
        """
        if not self._cached_chunks:
            return ""
        if not text or not text.strip():
            return ""

        # Extract probes from only the last 500 chars to avoid repeating old probes
        tail = text[-500:] if len(text) > 500 else text
        probes = self._extract_probes(tail)
        if not probes:
            probes = [tail[:200]]

        total_excluded = len(self._retrieved_indices)
        retrieved: List[str] = []
        for probe in probes[:3]:
            relevant = self._retrieve_for_probe(
                probe, self._cached_chunks, self._cached_embeddings,
                exclude_indices=self._retrieved_indices,
            )
            if relevant:
                retrieved.extend(relevant[:top_k])

        # Track newly retrieved indices for future exclusion
        pruned = 0
        for idx, ch in enumerate(self._cached_chunks):
            if ch in retrieved and idx not in self._retrieved_indices:
                self._retrieved_indices.add(idx)
            elif ch in retrieved and idx in self._retrieved_indices:
                pruned += 1

        if pruned > 0:
            logger.info(
                "Dedup: pruned %d already-retrieved chunks from re-retrieval", pruned
            )

        if not retrieved:
            return ""

        result = "\n\n---\n\n".join(retrieved)
        logger.info(
            "Re-retrieval: %d chunks (%d chars) for text probe (excluded %d prior)",
            len(retrieved), len(result), total_excluded,
        )
        return result

    # ------------------------------------------------------------------
    #  Cleanup
    # ------------------------------------------------------------------

    def reset_retrieved(self) -> None:
        """Clear all cached state for a fresh consolidation pass.

        Call after completing all recursive generation steps for a single
        LLM node so the next node execution starts with a clean slate.
        """
        self._retrieved_indices.clear()
        self._cached_chunks = []
        self._cached_embeddings = None

    # ------------------------------------------------------------------
    #  Stage 3: No generation — retrieval IS the consolidation.
    #  The embedding vectors (keys) and probe vectors (queries) form a
    #  detached sparse attention layer. Retrieved chunks are the attended
    #  values. No model text generation is used.
    # ------------------------------------------------------------------


