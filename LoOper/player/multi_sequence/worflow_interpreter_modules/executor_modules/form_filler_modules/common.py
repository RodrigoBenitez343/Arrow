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


def _ff_pool_body(text):
    """The pool text WITHOUT its learned-answer sentinel blocks.

    Both the auto-learned and the human-verified blocks are a KEYED memory, not
    source material: each answer belongs to ITS question.  Retrieving them as
    evidence let one field be answered (or asked) from another field's answer -
    observed, the JS/TS experience question pulled in the neighbouring "## have
    you led engineering teams ... / No" block and returned No for a resume that
    states the skill.  The current field's own record still rides via the
    per-field learned hint.
    """
    s = str(text or "")
    if _FF_CORR_START not in s and _FF_VERIFIED_START not in s:
        return s.strip()
    return _POOL_BLOCK_RE.sub("", s).strip()


def _ff_parse_learned_answers(text):
    """The ``## <label>`` blocks of a learned-answers document, by label.

    The form filler APPENDS every user answer as ``## <label>`` + its answer
    (see ``_ff_learn``), so the file IS a label -> answer map.  The field loop
    consults it BEFORE probing: a field the user already answered is used
    VERBATIM instead of being re-derived - which is what let the SAME question
    be asked again on a later pass (live: the stored 'Ezeiza, Buenos Aires
    Province, Argentina' came back from the model as a lossy 'Ezeiza', no
    option won the choice rail, and the field was re-asked).

    A stored answer is PRUNED of markdown on the way out: an answer learned as
    ``**1**`` was written into every later form verbatim, and a control that
    only takes digits (the site's own validation, not the DOM's) rejected it
    forever (live: '¿Cuántos años de experiencia tienes con Modelo de datos?'
    holding '**1**' while the page refused to advance).
    """
    out, cur = {}, None
    for line in str(text or "").splitlines():
        m = _QA_HEADING_RE.match(line)
        if m:
            cur = _ff_norm(m.group(1)).strip("*? .:")
            if cur:
                # A label can appear in SEVERAL accumulated blocks (the pool is
                # append-only): the NEWEST block wins.  Appending to an existing
                # list made the value BOTH answers at once ("Yes No", "6000
                # 6000"), which matched no option, so the field was re-asked and
                # re-learned on every pass.
                out[cur] = []
            continue
        if cur is not None and line.strip():
            out[cur].append(line.strip())
    return {k: _ff_plain_text(" ".join(v)) for k, v in out.items()}


# Sentinels wrapping the learned answers INSIDE the context pool text.
# A form-filler node owns NO corrections file: the answers it learns live in the
# Context node the user wired.  The block is unambiguous even when the pool also
# carries the source documents (a resume's own '##' headings can never be read
# as learned Q&A).
_FF_CORR_START = "[[FORM_CORRECTIONS]]"
_FF_CORR_END = "[[/FORM_CORRECTIONS]]"

# A SECOND block carries the answers a HUMAN fixed (added or edited in the
# Context node's dialog).  Same shape, different provenance: the pipeline treats
# a verified entry as truth and never re-derives or overrides it, while an
# auto-learned entry is still revisable.  Without the split the two were
# indistinguishable, so an engine verdict could discard a hand-corrected answer
# and the user had to correct the same field again on the next run.
_FF_VERIFIED_START = "[[FORM_VERIFIED]]"
_FF_VERIFIED_END = "[[/FORM_VERIFIED]]"
_POOL_BLOCK_RE = re.compile(
    re.escape(_FF_CORR_START) + r".*?" + re.escape(_FF_CORR_END)
    + r"|" + re.escape(_FF_VERIFIED_START) + r".*?" + re.escape(_FF_VERIFIED_END),
    re.DOTALL,
)

# Section headers the Context node puts on its served pool.  They are ONE pool
# - documents, stored rows, skills, history turns and the learned block are all
# part of it - so a consumer must read them ALL, not only the documents.  The
# headers name the streams only where a stream needs DIFFERENT handling (the
# documents get weighted like attached files; the learned block is a keyed
# memory).  Shared here so the producer and the parser cannot drift apart.
_FF_DOCS_HEADER = "## Documents"
_FF_SKILLS_HEADER = "## Skills"
_FF_HISTORY_HEADER = "## History"


def _ff_pool_corrections(text):
    """The learned-answer text carried inside the context pool.

    Concatenates the inner text of every sentinel block found in *text* (the
    blocks accumulate in the append-only pool, so every answer ever learned is
    returned).  '' when the pool carries none.
    """
    s = str(text or "")
    if _FF_CORR_START not in s:
        return ""
    parts, idx = [], 0
    while True:
        a = s.find(_FF_CORR_START, idx)
        if a < 0:
            break
        b = s.find(_FF_CORR_END, a + len(_FF_CORR_START))
        if b < 0:
            parts.append(s[a + len(_FF_CORR_START):])
            break
        parts.append(s[a + len(_FF_CORR_START):b])
        idx = b + len(_FF_CORR_END)
    return "\n".join(p.strip() for p in parts if p.strip()).strip()


def _ff_render_corrections(pairs):
    """Wrap this pass's NEW ``(label, answer)`` pairs in the pool sentinel block.

    '' when there is nothing to publish (so an unchanged pass adds no block).
    The answer is PRUNED of markdown before it is published: the block is the
    node's own memory, and a '**1**' stored there is replayed into every later
    form (see ``_ff_plain_text``).
    """
    pairs = [(lab, _ff_plain_text(ans)) for lab, ans in (pairs or [])]
    body = "\n".join("## %s\n%s" % (lab, ans) for lab, ans in pairs
                     if str(ans or "").strip())
    if not body.strip():
        return ""
    return "%s\n%s\n%s" % (_FF_CORR_START, body, _FF_CORR_END)


def _ff_split_corrections(text):
    """Split a pool value into (text WITHOUT the sentinel blocks, {label: answer}).

    The CONTEXT node stores each learned answer as its OWN entry (so the audit
    dialog edits ONE answer at a time), so the block must be lifted OUT of the
    stored value; the parsed map comes back for that.  ``(<text>, {})`` when no
    block is present.  The VERIFIED block is left in place - see
    :func:`_ff_split_verified`, which is applied to the same value right after.
    """
    s = str(text or "")
    if _FF_CORR_START not in s:
        return s, {}
    pairs, clean = {}, s
    while _FF_CORR_START in clean:
        a = clean.find(_FF_CORR_START)
        b = clean.find(_FF_CORR_END, a + len(_FF_CORR_START))
        if b < 0:
            inner = clean[a + len(_FF_CORR_START):]
            clean = clean[:a]
        else:
            inner = clean[a + len(_FF_CORR_START):b]
            clean = clean[:a] + clean[b + len(_FF_CORR_END):]
        pairs.update(_ff_parse_learned_answers(inner))
    return clean.strip(), pairs


def _ff_question_words(label):
    """Content words of a field label, crudely singularised (len >= 4)."""
    words = []
    for w in re.split(r"[^a-z0-9]+", _ff_norm(label or "")):
        if len(w) < 4:
            continue
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        words.append(w)
    return words


def _ff_dupe_score(a, b):
    """Cheap lexical similarity (0..1) used to PREFILTER duplicate questions.

    "how much experience you have with cloud providers like aws?" vs "how many
    years of experience do you have with aws?" share only a few words, so the
    score is CONTAINMENT of the shorter label (every word of the shorter one
    that the longer one carries) rather than a symmetric Jaccard - and it is
    only a prefilter: the model decides, because "with aws" vs "with azure"
    looks identical to any lexical or embedding score.
    """
    wa = set(_ff_question_words(a))
    wb = set(_ff_question_words(b))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / float(min(len(wa), len(wb)))


def _ff_render_verified(pairs):
    """The HUMAN-verified answers as their own sentinel block ('' when none)."""
    pairs = [(lab, _ff_plain_text(ans)) for lab, ans in (pairs or [])]
    body = "\n".join("## %s\n%s" % (lab, ans) for lab, ans in pairs
                     if str(ans or "").strip())
    if not body.strip():
        return ""
    return "%s\n%s\n%s" % (_FF_VERIFIED_START, body, _FF_VERIFIED_END)


def _ff_pool_verified(text):
    """The human-verified answer text carried inside the context pool."""
    s = str(text or "")
    if _FF_VERIFIED_START not in s:
        return ""
    parts, idx = [], 0
    while True:
        a = s.find(_FF_VERIFIED_START, idx)
        if a < 0:
            break
        b = s.find(_FF_VERIFIED_END, a + len(_FF_VERIFIED_START))
        if b < 0:
            parts.append(s[a + len(_FF_VERIFIED_START):])
            break
        parts.append(s[a + len(_FF_VERIFIED_START):b])
        idx = b + len(_FF_VERIFIED_END)
    return "\n".join(p.strip() for p in parts if p.strip()).strip()


def _ff_split_verified(text):
    """Split a pool value into (text WITHOUT verified blocks, {label: answer})."""
    s = str(text or "")
    if _FF_VERIFIED_START not in s:
        return s, {}
    pairs, clean = {}, s
    while _FF_VERIFIED_START in clean:
        a = clean.find(_FF_VERIFIED_START)
        b = clean.find(_FF_VERIFIED_END, a + len(_FF_VERIFIED_START))
        if b < 0:
            inner = clean[a + len(_FF_VERIFIED_START):]
            clean = clean[:a]
        else:
            inner = clean[a + len(_FF_VERIFIED_START):b]
            clean = clean[:a] + clean[b + len(_FF_VERIFIED_END):]
        pairs.update(_ff_parse_learned_answers(inner))
    return clean.strip(), pairs


# THIS node's own per-field summary row: `[<key>] {'mode': ..., 'fields': [...]}`.
# It rides the pool whenever the chain wires the node's output back through a
# Context node, and it is NOT source material - retrieved as evidence it makes
# the model answer a field with ANOTHER field's text (live: one question
# returned as the answer to a different question, and a question echoed back as
# its own answer once a bad value had been learned).  The rows stay IN the pool
# (the prior-pass statuses are read from them, see `_ff_prior_from_pool`); they
# are only kept OUT of the material a probe may retrieve.
#
# The bracket holds the node's DESCRIPTION, so its length is unbounded: a
# 120-char cap let the live row through -
#   [form_filler/fills a web form with the user's data — "Fill the form using
#    the provided context." — answers written by "Qwen3.8-2B-Q8_0.gguf" —
#    verifies what it typed] {'mode': 'web', ...}
# - and the intent planner then wrote probes about "the answers written by
# Qwen3.8-2B-Q8_0.gguf" instead of about the field.
_FF_OWN_ROW_RE = re.compile(
    r"^\s*\[[^\]\n]+\]\s*[\{\[].*['\"]fields['\"]", re.MULTILINE)


def _ff_strip_own_output(text):
    """Drop THIS node's own summary rows from the material a probe retrieves."""
    return "\n".join(
        ln for ln in str(text or "").splitlines()
        if not _FF_OWN_ROW_RE.match(ln)
    ).strip()


def _ff_split_pool(text):
    """Split a Context node's served pool into learned vs the rest.

    The served blob is ONE pool: documents, stored rows, skills, history turns
    and the learned-answer sentinel block all belong to it, and retrieval must
    see them ALL.  This separates only the pieces that need DIFFERENT handling:
    the learned answers (a keyed memory the node reuses/adapts) from everything
    else (the material a probe retrieves).  The documents section is split off
    only when the node marked it, so the engine can weight it like an attached
    file.  Never drops anything - an unmarked stream stays in ``context``.
    """
    s = str(text or "")
    learned_text = _ff_pool_corrections(s)
    learned = _ff_parse_learned_answers(learned_text)
    verified_text = _ff_pool_verified(s)
    verified = _ff_parse_learned_answers(verified_text)
    body = _ff_pool_body(s)
    documents, context = "", body
    if _FF_DOCS_HEADER in body:
        before, after = body.split(_FF_DOCS_HEADER, 1)
        documents = after.strip()
        context = before.strip()
    # The node's OWN output is not a document: see _FF_OWN_ROW_RE.
    documents = _ff_strip_own_output(documents)
    context = _ff_strip_own_output(context)
    return {
        "documents": documents,
        "context": context,
        "learned": learned,
        "learned_text": learned_text,
        "verified": verified,
        "verified_text": verified_text,
    }


def _ff_scalarize(value):
    """The first number inside *value*, '' when it holds none.

    A choice answer learned as a band ("2-5 years", "5+ years") cannot be
    written into a free-text control that only accepts a single number: the
    page rejects it or the site's review does.  The LOWER bound is the honest
    scalar for a "how many" question.  Returns '' when the value carries no
    digit (then the caller keeps the value as-is).
    """
    nums = re.findall(r"\d+(?:\.\d+)?", str(value or ""))
    return nums[0] if nums else ""


def _ff_cos(a, b):
    """Cosine similarity of two vectors (0.0 when either is empty/zero)."""
    try:
        import math
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        return dot / (na * nb) if na and nb else 0.0
    except Exception:
        return 0.0


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


def _ff_states_verbatim(value, text):
    """True when *text* holds *value* AS WRITTEN (punctuation/spacing blind).

    A judgement call cannot override a copy: the semantic repair judge called
    'Buenos Aires' too specific for a 'Location (city)' field and the repair
    pass replaced a correct, verbatim-from-the-source answer with 'N/A'.  The
    match is at a WORD boundary, so 'Air' is not found inside 'Buenos Aires'.
    """
    v = _ff_dense(value)
    if len(v) < 4:
        return False
    body = _ff_dense(text)
    if not body or v not in body:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(v) + r"(?![a-z0-9])",
                     body) is not None


def _ff_compound_unstated(value, ground):
    """True when *value* is a COMBINATION the source never states as one.

    A stored answer learned from material that held TWO rows - 'Buenos Aires'
    from the contact line and 'Saint Paul' from a 2018 employer - replays
    forever: every fragment is real, so a substring grounding test and the
    entailment verdict both pass, and the field keeps a value the document never
    states (live: 'Location (city)*' answered 'Buenos Aires, Saint Paul').

    The test is structural, not a word list: a value with two or more parts
    separated by a comma / semicolon / pipe must appear AS WRITTEN.  A single
    part is left to the callers' other rails - it may be a legitimate derived
    answer (a counted number of years, a short summary).
    """
    text = str(value or "").strip()
    if not text or not ground:
        return False
    parts = [p.strip() for p in re.split(r"[,;|]", text) if len(p.strip()) >= 3]
    if len(parts) < 2:
        return False
    dense = _ff_dense(text)
    return bool(dense) and dense not in _ff_dense(ground)


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


def _ff_joined_strips(value, evidence, multiline=False):
    """True when *value* is several WHOLE source lines glued into one answer.

    A narrow field came back as the tagline line, the skills line and the NAME,
    comma-joined ('Current job title' -> 'Forward Deployed AI Engineer,
    Applied AI/ML (GenAI, RAG), Enterprise & Regulated Environments, Rodrigo
    David Benitez').  Every part is real, the combination is not a job title -
    that is the mixing this guard exists to stop.  Prose (a textarea) may
    legitimately quote several lines, so the rule is off for a multiline
    control.
    """
    if multiline:
        return False
    body = _ff_dense(value)
    if not body or len(body) > 400:
        return False
    strips = set()
    for line in str(evidence or "").splitlines():
        line = line.strip()
        if not line.startswith("-"):
            continue
        line = re.sub(r"^-\s*(\[[^\]]{0,60}\]\s*)?", "", line).strip()
        dense = _ff_dense(line)
        if len(dense) >= 8:
            strips.add(dense)
    if len(strips) < 2:
        return False
    return sum(1 for s in strips if s in body) >= 2


def _ff_value_rejection(label, value, evidence, multiline=False):
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
    if _ff_joined_strips(value, evidence, multiline):
        return ("the value is several DIFFERENT source lines joined together "
                "- a list, not an answer")
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


# Every form-filler prompt asks for plain text, and a small model answers
# ``**2**`` anyway - the markers then become part of the VALUE the page stores
# (observed: a years-of-experience input holding '**2**').  This rule is the
# prompt-side half, stated as a CONTRACT at the TOP of the system prompt (a
# small model follows a format rule it reads first); ``_ff_plain_text`` is the
# harness-side half, because a prompt is a suggestion, never a guarantee.
_FF_PLAIN_RULE = (
    "OUTPUT FORMAT - PLAIN TEXT ONLY, NEVER MARKDOWN. Your reply is typed "
    "into the field EXACTLY as written, so every marker becomes part of the "
    "answer: no **bold**, no *italics*, no `code`, no # headings, no - or 1. "
    "list markers, no tables, no [links](url). Asked 'how many years of "
    "experience do you have with X?', the whole reply is 2 - never **2**."
)

_MD_RULE_RE = re.compile(r"^\s{0,3}(?:[-*_])(?:\s*[-*_]){2,}\s*$")
_MD_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
_MD_QUOTE_RE = re.compile(r"^\s{0,3}>\s?")
_MD_BULLET_RE = re.compile(r"^\s{0,3}(?:[-*+•])\s+")
# A numbered list marker, but never a YEAR that starts a sentence.
_MD_NUMBER_RE = re.compile(r"^\s{0,3}(\d{1,3})[.)]\s+")
_MD_TABLE_EDGE_RE = re.compile(r"^\s{0,3}\|(.*)\|\s*$")
_MD_LINK_RE = re.compile(r"!?\[([^\]\n]*)\]\(\s*<?([^)\s>]*)>?[^)\n]*\)")
_MD_EMPHASIS_RES = (
    re.compile(r"\*\*\*(.+?)\*\*\*"),
    re.compile(r"\*\*(.+?)\*\*"),
    re.compile(r"(?<!\w)__(.+?)__(?!\w)"),
    re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])"),
    re.compile(r"(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)"),
)


def _ff_plain_text(text):
    """Prune MARKDOWN out of a model reply, keeping only what it SAYS.

    Fences, headings, quote markers, list bullets, emphasis pairs, inline-code
    ticks, table edges and link markup are dropped; the CONTENT they wrapped
    survives: ``**2**`` -> ``2``, ``- **Python**`` -> ``Python``,
    ``[CV](https://x)`` -> ``CV``.  A form value is written to the page
    VERBATIM, so markup left in it lands in the form (observed: a
    years-of-experience input holding '**2**').  A reply that was nothing but
    markup becomes '' - the caller then treats the field as unanswered instead
    of typing punctuation into it.
    """
    t = str(text or "")
    if not t.strip():
        return ""
    # A fenced block HOLDS the value; the fence itself is markup.
    t = re.sub(
        r"(?ms)^[ \t]*```[a-zA-Z0-9_+-]*[ \t]*\n?(.*?)\n?[ \t]*```[ \t]*$",
        r"\1", t,
    )
    t = re.sub(r"```[a-zA-Z0-9_+-]*", "", t)
    lines = []
    for line in t.splitlines():
        if _MD_RULE_RE.match(line):
            continue                     # a horizontal rule is pure markup
        line = _MD_HEADING_RE.sub("", line)
        line = _MD_QUOTE_RE.sub("", line)
        line = _MD_BULLET_RE.sub("", line)
        m = _MD_NUMBER_RE.match(line)
        if m and not re.fullmatch(r"(?:19|20)\d{2}", m.group(1)):
            line = line[m.end():]
        line = _MD_TABLE_EDGE_RE.sub(r"\1", line)
        lines.append(line.strip())
    t = "\n".join(lines)
    if not t.strip():
        return ""
    t = _MD_LINK_RE.sub(
        lambda m: m.group(1).strip() or m.group(2).strip(), t
    )
    t = t.replace("`", "")
    for rx in _MD_EMPHASIS_RES:
        t = rx.sub(r"\1", t)
    # Emphasis that never paired up (a truncated reply) is dangling markup.
    t = re.sub(r"\*{2,}", "", t)
    t = re.sub(r"(?<!\w)_{2,}(?!\w)", "", t)
    t = re.sub(r"(?m)^[ \t]*\*+[ \t]*", "", t)
    t = re.sub(r"(?m)[ \t]*\*+[ \t]*$", "", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t.strip()


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
        engine = str(self._engine or "llamacpp").lower()
        model = model_name or self._model or ""
        if engine == "ollama":
            try:
                import os
                from AI.consult import OllamaClient
                host = os.getenv("OLLAMA_HOST", "localhost")
                port = os.getenv("OLLAMA_PORT", "11434")
                client = OllamaClient(base_url=f"http://{host}:{port}", debug=False)
                return client.embeddings(model or "nomic-embed-text", texts) or []
            except Exception as exc:
                logger.debug("[FORM] Ollama embedding failed: %s", exc)
                return []
        try:
            from AI import byok
            is_local = byok.is_local_engine(engine)
        except Exception:
            is_local = engine in ("", "llamacpp", "llama.cpp", "llama_cpp")
        if not is_local:
            # BYOK provider engine: route through the local gateway — the
            # "provider:model" prefix dispatches (keys stay in byok.json).
            try:
                from AI import byok as _byok
                from AI.consult import OllamaClient
                from player.multi_sequence.llm_executor_resources.config_utils import (
                    get_default_api_url)
                model = _byok.join_model_ref(engine, model)
                if not model:
                    logger.debug("[FORM] provider embedding needs an explicit model")
                    return []
                client = OllamaClient(base_url=get_default_api_url(), debug=False)
                return client.embeddings(model, texts) or []
            except Exception as exc:
                logger.debug("[FORM] provider embedding failed: %s", exc)
                return []
        try:
            from AI import embedding_server
            return embedding_server.embed_texts(texts) or []
        except Exception as exc:
            logger.debug("[FORM] Local embedding server failed: %s", exc)
            return []

    def close(self):
        pass
