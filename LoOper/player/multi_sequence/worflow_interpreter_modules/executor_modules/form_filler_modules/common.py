"""Shared helpers for the Form Filling node."""

import logging
import re


logger = logging.getLogger(__name__)


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


def _as_bool(v, default=False):
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "1", "yes", "on")

def _as_int(v, default):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default

def _as_float(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default

def _split_list(text):
    if not text:
        return []
    if isinstance(text, (list, tuple)):
        parts = [str(x) for x in text]
    else:
        parts = str(text).split(",")
    return [p.strip().lower() for p in parts if p and p.strip()]

_PLACEHOLDER_RE = re.compile(
    # A template MARKER in brackets - "[Company]", "[Your Name]", "[specific
    # goal]".  The keyword list alone missed every marker it did not name, so a
    # "summary" field was filled with the source's own template text ("...how I
    # can help [Company] with [specific goal]").  A markdown link/image is NOT a
    # marker: its "]" is followed by "(" or "[", so it is excluded.
    r"\[\s*[^\[\]()\n]{1,40}?\s*\](?!\s*[\(\[])"
    r"|\{\{?[^}\n]{1,40}\}?\}",
    re.IGNORECASE,
)

_IDENTIFIER_RE = re.compile(
    r'https?://[^\s\)\]\}]+|www\.[^\s\)\]\}]+|[\w.+-]+@[\w-]+\.[\w.-]+',
    re.IGNORECASE,
)

# A markdown heading, and a "## <question>" block holding its answer below it.
_QA_HEADING_RE = re.compile(r"^\s{0,3}#{1,4}\s*(\S.*?)\s*$")


def _ff_drop_foreign_qa(text, label):
    """Drop the source's own Q&A blocks for OTHER questions.

    A learned-answers / questionnaire source holds ``## <question>`` blocks with
    their answer under them - including this node's OWN ``_ff_learn`` entries
    ("## %s" % label).  Retrieval pulls such a block in for a similarly-worded
    question, and the composer then answers FROM another question's answer
    (observed: 'How many years of work experience do you have with Artificial
    Intelligence (AI)?' answered '4 years' - the value learned for Solution
    Architecture, whose block sat in the excerpt; the same Presales 0 /
    Solution Architecture 4 blocks kept reappearing in every consolidation
    cycle).  A block is KEPT only when its question IS this field's label, so a
    field's own remembered answer still grounds it.
    """
    lab = _ff_norm(label).strip("*? .:")
    if not lab or not text:
        return text
    out = []
    dropping = False
    for line in str(text).splitlines():
        m = _QA_HEADING_RE.match(line)
        if m and m.group(1).strip().endswith("?"):
            dropping = _ff_norm(m.group(1)).strip("*? .:") != lab
            if not dropping:
                out.append(line)
            continue
        if dropping:
            # The block runs until a blank line (the next heading re-evaluates).
            if not line.strip():
                dropping = False
            continue
        out.append(line)
    return "\n".join(out)


def _ff_parse_learned_answers(text):
    """The ``## <label>`` blocks of a learned-answers document, by label.

    The form filler APPENDS every user answer as ``## <label>`` + its answer
    (see ``_ff_learn``), so the file IS a label -> answer map.  The field loop
    consults it BEFORE probing: a field the user already answered is used
    VERBATIM instead of being re-derived - which is what let the SAME question
    be asked again on a later pass (live: the stored 'Ezeiza, Buenos Aires
    Province, Argentina' came back from the model as a lossy 'Ezeiza', no
    option won the choice rail, and the field was re-asked).
    """
    out, cur = {}, None
    for line in str(text or "").splitlines():
        m = _QA_HEADING_RE.match(line)
        if m:
            cur = _ff_norm(m.group(1)).strip("*? .:")
            if cur:
                out.setdefault(cur, [])
            continue
        if cur is not None and line.strip():
            out[cur].append(line.strip())
    return {k: " ".join(v).strip() for k, v in out.items()}


def _ff_finding_lines(entries):
    """Composed findings with their Support quotes stripped and repeats dropped.

    A finding's ``Support:`` line is a VERBATIM copy of the very excerpts
    shipped beside it, so keeping both printed the same sentences TWICE - the
    duplicated evidence in the field block - and spent window the small model
    needs for the answer.  The excerpts are the ground truth; the findings
    contribute only the composed answer.

    Repeats are dropped too: the composer answers each cycle with UP TO FIVE
    findings and re-emits the earlier ones, so the pool carried the same
    finding several times over (the engine's own dedupe only inspects the
    FIRST finding of a multi-finding entry).
    """
    out = []
    seen = set()
    for entry in entries or []:
        keep = []
        for line in str(entry).splitlines():
            s = line.strip()
            # A markdown-bolded composer emits "**Support:**" - strip the marker
            # before the prefix test, or the quote leaks into the distilled block.
            if s.lower().lstrip("*#>- \t").startswith(("support:", "[cycle")):
                continue
            keep.append(s)
        text = "\n".join(x for x in keep if x).strip()
        if not text:
            continue
        key = re.sub(r"\W+", "", text).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out

def _ff_ungrounded_identifier(value, ground):
    """The first URL/email in *value* that the source does not contain.

    Such a token was INVENTED, not retrieved (observed: the extractor wrote
    'https://www.linkedin.com/in/yourname' into the profile field while the
    source held '.../alex-doe-1234567890').  The comparison is on the
    IDENTITY, not the spelling - a source often holds the bare host/path
    while the page wants the full URL - so a differently-written-but-real
    value still passes.  Returns the offending token, or ``None`` when every
    identifier is grounded, none is present, or there is no source text.
    """
    if not value or not ground:
        return None
    g = str(ground).lower()
    for tok in _IDENTIFIER_RE.findall(str(value)):
        core = re.sub(r"^https?://", "", tok.strip().rstrip(".,;:)]}'\""))
        if core.startswith("www."):
            core = core[4:]
        core = core.lstrip("/").rstrip("/").lower()
        if len(core) >= 8 and core not in g:
            return tok
    return None


def _ff_fragment_of_evidence(value, evidence):
    """True when *value* occurs in the evidence ONLY as part of a longer word.

    A truncation hallucination is a real source word CUT SHORT - the model
    emitted 'iversid' for 'Riverside' and 'locat' for 'location'.  Both are plain
    substrings of the source token, so a substring grounding test passes them
    (grounding is blind to a bare lowercase token too: it only inspects
    capitalized words and digit-bearing values).  A value must therefore be
    found at a WORD BOUNDARY to count as copied; an occurrence that is only ever
    inside a longer alphanumeric run is a fragment and is dropped.  A value the
    evidence does not contain AT ALL is left to the callers' other guards - it
    may be a legitimate derived answer (counted years, a short summary), so this
    never blocks inference.
    """
    v = _ff_dense(value)
    if len(v) < 3 or not evidence:
        return False
    ev = _ff_dense(evidence)
    if not ev or v not in ev:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(v) + r"(?![a-z0-9])", ev) is None


def _ff_value_rejection(label, value, evidence):
    """Why a value must NOT be written into the field ('' when it is fine).

    Three guards, all independent of the model's cooperation: an identifier the
    source does not contain is an invention, a value that is only a FRAGMENT of
    a source word is a truncation ('iversid' for 'Riverside'), and a bracketed
    TEMPLATE MARKER is the source's own placeholder text rather than an answer
    (the same marker rule ``_ff_repair_reason`` applies, so the fill pass can
    never write what the repair pass immediately flags).  The field is then
    SKIPped - never filled with a fabricated value.

    Nothing here matches the LABEL against a word list: what a field's question
    ASKS for is settled by the page's own control (its ``type`` / ``pattern`` /
    native validity) and by the model.  A keyword list is what let a wrong-kind
    answer pass in one language and killed a correct one in another.
    """
    tok = _ff_ungrounded_identifier(value, evidence)
    if tok:
        return ("the value contains %r, which does not appear anywhere in "
                "the retrieved source text" % tok)
    if _ff_fragment_of_evidence(value, evidence):
        return ("the value only appears inside a longer word in the source "
                "text (a truncated / fragmentary answer, not the real value)")
    m = _PLACEHOLDER_RE.search(str(value or ""))
    if m:
        return ("the value is placeholder text (%r), not an answer"
                % m.group(0))
    return ""


# The literal a field receives when it genuinely CANNOT be answered.  Distinct
# from a skip (which leaves the page blank): the page gets a real value, so a
# required unanswerable field stops the wizard from re-entering the node
# forever.  It is the ONE token the extraction prompt itself names, so the
# model's verdict is matched against ITS OWN protocol - never against a list of
# synonyms, which is a language, not a rule.
_FF_NA_VALUE = "N/A"


def _ff_placeholder_value(field, text):
    """True when a field's PRE-FILLED value is NOT an answer (fill it instead).

    Two signals, both from the PAGE itself: the field is empty, or it holds
    exactly its own ``placeholder`` text.  Which option TEXT means "not an
    answer" is not guessed from a word list - an option whose value attribute
    is empty, or is disabled, is dropped by the enumerator, and anything else
    is the page's own state (a <select> sitting on its prompt entry reads back
    with that entry's value, which the harness fills over).
    """
    low = _ff_norm(text)
    if not low:
        return True
    ph = _ff_norm(field.get("placeholder"))
    return bool(ph) and ph == low


def _ff_norm(text):
    """Collapse whitespace and case - the comparison form for option matching."""
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _ff_dense(text):
    """Words-only form (lowercase, punctuation -> space) for verbatim checks."""
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


# The control families that hold ONE of the PAGE's OWN options (a <select>, an
# autocomplete, a merged radio/checkbox group).  A pre-filled value here is
# usually the page's own DEFAULT - a <select> shows its FIRST option until
# someone picks - so the skip rule must not treat it as an answer.
_FF_LIST_KINDS = ("select", "combo", "choice")


def _ff_option_match(value, options):
    """The option LABEL *value* answers with, '' when it answers with none.

    A choice field has one CONTROL PER OPTION (a radio group, a select, a
    multiple-choice list), so an off-list answer has nothing to click: the
    write bounces, the field is re-asked, and the wizard around it re-runs the
    WHOLE form (observed: LinkedIn's multiple-choice answered 'Full
    professional proficiency' against a CEFR list A1..C2 and the same 8-field
    page was re-filled 11 identical times).  Matching is deliberately
    conservative - the option's own text, then its short label (before ':' or
    '('), then containment - so a near miss is never turned into a WRONG
    selection: an unmatched answer leaves the field to the harness instead.
    """
    val = _ff_norm(value)
    if not val:
        return ""
    opts = [str(o) for o in (options or []) if str(o).strip()]
    for o in opts:
        if _ff_norm(o) == val:
            return o
    for o in opts:
        short = _ff_norm(re.split(r"[:(,]", o, 1)[0])
        if short and short == val:
            return o
    for o in opts:
        o_n = _ff_norm(o)
        if len(val) < 3:
            continue
        if val in o_n or (len(o_n) >= 3 and o_n in val):
            return o
        short = _ff_norm(re.split(r"[:(,]", o, 1)[0])
        if len(short) >= 3 and short in val:
            return o
    return ""


def _ff_option_exact(value, options):
    """True when *value* IS one of *options* (normalized, EXACT equality).

    A combo/select can only HOLD one of its OWN options, so a value the list
    does not contain is uncommitted free text a previous pass typed - and
    skipping it as "already filled" made the field un-salvageable (observed: a
    city combo left holding 'riverside' was skipped on every later pass,
    forever).  The test is EXACT, never the lenient containment of
    ``_ff_option_match``: a typed 'riverside' is a SUBSTRING of the option
    'Riverside, Illinois, United States' and must not count as landed.
    """
    val = _ff_norm(value)
    if not val:
        return False
    return any(_ff_norm(o) == val for o in (options or []) if str(o).strip())


def _ff_na_value(options=None):
    """The literal to WRITE for an unanswerable field ('' when it cannot hold
    one).

    A free-text control takes the literal as-is.  A list-backed control can only
    HOLD one of its own options, so '' here leaves it alone - the option comes
    from the page's own list through the choice rail (the model picks, pinned to
    what the page offers), never from the harness recognising which option WORD
    means "none".
    """
    return "" if options else _FF_NA_VALUE


def _ff_fit_length(text, limit):
    """Shorten *text* to *limit* characters, at a word boundary when one is
    available.  A value longer than the page's own ``maxlength`` can NEVER be
    accepted, so the harness fits it instead of letting the page reject it on
    every attempt (observed: a 150-character field answered with 166
    characters on BOTH repair attempts, and the field ended 'failed').
    """
    s = str(text or "").strip()
    if limit <= 0 or len(s) <= limit:
        return s
    cut = s[:limit]
    for sep in (". ", "! ", "? ", "; ", ", ", " "):
        i = cut.rfind(sep)
        if i >= max(4, limit // 3):
            return cut[: i + (1 if sep != " " else 0)].strip()
    return cut.strip()


def _strip_fences(text):
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z0-9_]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()

def _strip_think(text):
    """Drop reasoning / think blocks and code fences from model output.

    Reasoning models whose chat template injects the OPENING tag emit reasoning
    followed by a STANDALONE ``</think>`` and only then the answer; that dangling
    close must be honoured - everything before it is reasoning, not the value.
    Ignoring it (the old behaviour) made a long "little longer answer" field
    receive its reasoning text, and a budget-truncated reply lose the answer
    entirely.  Mirrors the LLM node's stripper (executor._strip_think_tags).
    """
    if not text:
        return ""
    t = str(text)
    # A <answer> block, when present, IS the value.
    for pat in (r"(?is)<answer>(.*?)</answer>",
                r"(?is)\[answer\](.*?)\[/answer\]"):
        m = re.search(pat, t)
        if m and m.group(1).strip():
            return _strip_fences(m.group(1))
    t = re.sub(r"(?is) thinking.*?<｜end▁of▁thinking｜>", "", t)
    t = re.sub(r"(?is)<\|?think\|?>.*?<\|?/think\|?>", "", t)
    t = re.sub(r"(?is)<reasoning>.*?</reasoning>", "", t)
    t = re.sub(r"(?is)<scratchpad>.*?</scratchpad>", "", t)
    t = re.sub(r"(?is)\[thinking\].*?\[/thinking\]", "", t)
    # Dangling close: whatever precedes it is reasoning, not the answer.
    close = re.search(r"(?is)</think>", t)
    if close:
        t = t[close.end():]
    t = re.sub(r"(?is)<\s*/?\s*think\s*>", "", t)
    return _strip_fences(t)


def _ff_source_sig(documents, source_text):
    """Signature of a pass's SOURCES (attached docs + gathered context).

    The chain loops back into the form filler whenever a field does not land,
    so each pass used to re-run the FULL ComoRAG sweep for every field (one
    field burned 512s on repeat, four identical passes on the same page).
    Remembering per-field outcomes is only safe against the SAME sources: a
    learned answer changes the sources, and the field must be retried.
    """
    import hashlib
    h = hashlib.md5()
    for doc in documents or []:
        h.update(str(doc).encode("utf-8", "ignore"))
        h.update(b"\x00")
    h.update(str(source_text or "").encode("utf-8", "ignore"))
    return h.hexdigest()


class _FFEmbedClient:
    """Minimal duck-typed embedding client (``embeddings(model, texts)``).

    llama.cpp -> the lazy, offline embeddinggemma server; Ollama -> a direct
    connection.  Any failure returns ``[]`` so the caller degrades to no
    evidence (and the field is SKIPped) rather than fabricating a value.
    """

    def __init__(self, engine, model=""):
        self.debug = True
        self._engine = (engine or "llamacpp").lower()
        self._model = model or ""

    def embeddings(self, model_name, texts):
        texts = list(texts)
        if not texts:
            return []
        if self._engine == "ollama":
            try:
                import os
                from AI.consult import OllamaClient
                host = os.getenv("OLLAMA_HOST", "localhost")
                port = os.getenv("OLLAMA_PORT", "11434")
                client = OllamaClient(base_url=f"http://{host}:{port}", debug=False)
                return client.embeddings(model_name or self._model or "nomic-embed-text", texts) or []
            except Exception as exc:
                logger.debug("[FORM] Ollama embedding failed: %s", exc)
                return []
        try:
            from AI import embedding_server
            return embedding_server.embed_texts(texts) or []
        except Exception as exc:
            logger.debug("[FORM] Local embedding server failed: %s", exc)
            return []

    def close(self):
        pass
