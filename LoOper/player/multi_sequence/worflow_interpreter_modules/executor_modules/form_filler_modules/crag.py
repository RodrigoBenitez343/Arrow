"""Per-field retrieval: the CRAG port (evaluate -> correct -> recompose).

The node no longer probes: it retrieves once, SCORES every passage against the
field's own question, and lets the verdict decide what the extractor reads
(correct knowledge / corrected knowledge / nothing at all).  This module is
the form-side half of `AI/crag`; the algorithm itself lives there and knows
nothing about forms.

The retrieval unit is the SEGMENT - one line of the source, or one ``·`` part
of a line - not a fixed-size chunk.  Measured with the real resume and the
real evaluator (Laya's typed-question encoder): the SAME sentence scores 0.67
for its own field standing alone and 0.14 once it is padded into a 250-char
window, because the probability decays as unrelated text follows the value in
the premise.  Chunk-sized passages therefore made every contact field flip to
`incorrect` at random (the header scored 0.14 for 'Last name', 0.82 for
'First name'), the corrective stage then replaced good material with junk and
the field was asked or guessed.  At segment size the same scorer separates
cleanly (name 0.67, email 0.82, phone 0.94, city 0.89, everything wrong
<= 0.36), which is what the two CRAG thresholds assume.

Every segment carries the SECTION it came from, and the pack shows it, so a
job-history city ('Saint Paul, Minnesota, USA' under 'Professional Experience')
is never confused with the candidate's own contact block.
"""

import logging
import os
import re
import time

from AI.crag import (CRAGPipeline, CRAGUnavailable, ChunkStore,
                     CRAGExternalKnowledge, PoolStreams, default_config_from,
                     make_evaluator)
from AI.crag import corpus as _corpus
from AI.crag.external import DEFAULT_MAX_BLOCKS
from AI.crag.generate import evidence_block

from .common import (
    _FFEmbedClient,
    _ff_cos,
    _ff_drop_foreign_qa,
    _ff_norm,
    _ff_source_sig,
    _ff_split_pool,
    log_block,
    log_table,
    logger,
)

# The chunker lives with the CRAG port (AI/crag/corpus.py) because the Context
# node indexes with it and this node scores with it: the stored vector and the
# text it describes must come from ONE segmentation.  These names stay as the
# form-side spelling so the rest of the module (and its suites) read unchanged.
_ff_plain_source = _corpus.plain_source
_ff_segments = _corpus.segments
_ff_blocks = _corpus.blocks

# Segments that reach the evaluator per field.  One Laya forward is ~2.7 s on
# 6 threads, so this is the per-field time budget as much as a quality choice:
# the live run paid 13.6 forwards per field (1283 s of a 44 min session) to
# score nine strips whose best six were header lines.  Four scored strips answer
# a single-value field as well as nine did, for less than half the forwards.
_MAX_CANDIDATE_SEGMENTS = 4
# Segments GUARANTEED to the document's identity section (the block that names
# its SUBJECT - a person, an account, an issuer).  Guaranteed, not prepended:
# those values embed poorly against a short field label, but they must never
# consume the whole budget - when every candidate slot went to the header, the
# rest of the document became unreachable for every question (live: 42 of 42
# retrieved strips came from the first two blocks, and only the header's own
# fields could be answered from the document at all).  Two of four: half the
# budget for the header would leave the document reachable only by accident.
_IDENTITY_SEGMENT_SLOTS = 2
# How many segments one ranked block may contribute, so a single long section
# cannot take the budget either.  Half the budget, so one block can never BE
# the budget.
_MAX_SEGMENTS_PER_BLOCK = 2


def _ff_metrics():
    """The shared time/compute recorder, or None when it cannot load."""
    try:
        from AI import engine_metrics
        return engine_metrics
    except Exception:
        return None


def _ff_question_head(label, limit=300):
    """The QUESTION a field label carries, without the page's boilerplate.

    A consent paragraph is a label too -
        "We want everyone to have a fair chance to succeed. If you have a
         disability, condition, or specific need that requires adjustments to
         our recruitment process, please let us know below so we are able to
         best support you (if you do not require any adjustment, please type
         \"N/A\")."
    - and handed to a retrieval step as one query it is mostly the page
    talking to the user.  A label that ASKS a question keeps the question
    sentence; anything else keeps its own text, because the retriever still
    has to find it.  This shapes the QUERY only: the field's label (the
    learned-record key and the log row) is untouched.
    """
    s = " ".join(str(label or "").split())
    if not s:
        return ""
    # A parenthetical is an instruction to the user, never the ask.
    s = " ".join(re.sub(r"\([^()]{0,400}\)", " ", s).split()).strip(" .:;")
    # The page's REQUIRED marker is not part of the question: 'Last name*'
    # hands the evaluator a stray wildcard.
    s = re.sub(r"[\s*:✱]+$", "", s).strip()
    m = re.search(r"^(.{3,%d}?\?)" % limit, s)
    if m:
        return m.group(1).strip()
    return s[:limit].strip()


# Identity lines scored per field.  The document's FIRST section is its
# SUBJECT block (a resume's contact header, an invoice's issuer block), and
# which of those lines answers a question - or whether the block answers it at
# all - is a semantic judgement: Laya scores it (``_ff_answer_scores``).  This
# file therefore holds no vocabulary of field names, languages or value
# shapes; a form in any language, in either mode, is read the same way.
# Capped because every line costs one forward pass.  The header of a resume
# holds a dozen lines and its best four are always among them; scoring the rest
# bought nothing but forwards.
_MAX_IDENTITY_PROBES = 4
# Body sections compared with the subject block before a single-valued field
# is answered from one of them.  Each one is a forward pass, and the best few
# are all a document needs: the rest rank below them anyway.
_MAX_SECTION_PROBES = 2
# Probes per field.  The question, plus the control's own words when it has
# any - more than that buys nothing and every probe is text the embedding
# server has to encode.
_MAX_PROBES = 2
# (question, line) answer scores kept for the pass.  One form asks ~20
# questions over ~170 segments, so this holds a whole page plus its repairs
# and still bounds the memory.
_ANSWER_CACHE_LIMIT = 4096


_ANSWER_NUMBER_RE = re.compile(r"\d")


def _ff_answer_shaped(texts, options=()):
    """True when the kept strips carry an ANSWER, not just the subject.

    The verdict scores "is this passage about the question", and a small
    encoder says yes to a passage that merely shares its subject: live, six
    strips about 'Junior Developer (Intern) ... Python codebase' came back
    ``correct`` (0.63) for "How many years of professional experience do you
    have building high-scale backend systems with Python?" - no number, no date
    and none of the control's own options - and the extractor then invented
    '4-6 years' for a resume that says 8+.

    A CHOICE field's answer IS one of its options, so a strip that shows none of
    them cannot be `correct`.  Everything else is answer-shaped when it carries
    a value shape: a number/date/currency, an email or a URL.  Free text that
    carries none of those (a name, a city, a sentence) is not downgraded - there
    is no shape to test for, and the extractor still reads the strip.
    """
    body = " ".join(str(t or "") for t in (texts if isinstance(texts, (list, tuple))
                                           else [texts]))
    if not body.strip():
        return False
    if _ANSWER_NUMBER_RE.search(body) or "@" in body or "://" in body:
        return True
    opts = [o for o in (options or ()) if str(o).strip()]
    if not opts:
        return True
    hay = _ff_norm(body)
    for option in opts:
        needle = _ff_norm(option)
        if len(needle) >= 2 and needle in hay:
            return True
    return False


def _ff_identity_rows(entries):
    """The header lines worth SCORING for a field: the name and the CONTACT RUN.

    A resume header is the name, a run of taglines, then its contact run - the
    last lines before the first section, where the city, phone and email sit.
    Scoring in document order spent the whole four-forward budget on taglines
    and a 'Location (city)' field never saw its own city (live: the header's
    first four lines were two taglines and a project note).  So the budget goes
    to the first line and the END of the block, which is where a value lives.
    """
    rows = [e for e in entries if e.get("head")]
    cap = _MAX_IDENTITY_PROBES
    if len(rows) <= cap:
        return rows
    picked = []
    for entry in rows[:1] + rows[-(cap - 1):]:
        if entry not in picked:
            picked.append(entry)
    return picked[:cap]


def _ff_identity_order(identity, scores):
    """Identity segments, best answer to THIS question first.

    ``scores`` is Laya's answer score per segment, aligned with *identity*.
    Document order stands when there is nothing to sort by; the sort is
    stable, so equal scores keep the document's own order.  Taking the lines
    in document order is what handed 'Location (city)' a run of taglines while
    the city line itself never entered the candidate set.
    """
    rows = list(identity or [])
    if not scores or len(scores) != len(rows):
        return rows
    order = sorted(range(len(rows)), key=lambda i: -float(scores[i] or 0.0))
    return [rows[i] for i in order]


def _ff_next_probes(attempt, question, current):
    """The queries for the NEXT consolidation cycle.

    The query the corrective stage REWROTE is the next thing to ask: it is the
    pipeline's own statement of what it could not find.  A sweep that rewrote
    nothing keeps the question and simply goes deeper - the caller hands it
    what was already scored, so "deeper" is a part of the document nothing has
    looked at yet.
    """
    probes = [str(question or "").strip()]
    rewritten = str((attempt or {}).get("rewritten") or "").strip()
    if rewritten and _ff_norm(rewritten) != _ff_norm(question):
        probes.append(rewritten[:400])
    if _ff_norm(" ".join(probes)) == _ff_norm(" ".join(current or [])):
        return list(current or probes)
    return probes


def _ff_section_text(group):
    """One body section as the text a question is asked against."""
    head = (group or [{}])[0]
    name = str(head.get("section") or "").strip()
    body = str(head.get("block_text") or head.get("text") or "").strip()
    return ("%s %s" % (name, body)).strip()[:220]


def _ff_field_words(field):
    """The CONTROL's own words (placeholder / name / aria / title), '' if none."""
    parts = []
    for key in ("placeholder", "name", "aria", "title", "description"):
        value = str((field or {}).get(key) or "").strip()
        if value:
            parts.append(value)
    return " ".join(re.sub(r"[_\-\s]+", " ", p) for p in parts).strip()[:80]


def _ff_crag_probes(question, field=None):
    """The query texts ONE field is ranked with (the question always first).

    A field label is 1-2 words ('Location (city)', 'Photo') and embeds poorly
    against a line of contact details.  The control's OWN printed words ride
    along, because they often name the answer's format the label omits.
    Nothing else is added: what kind of value a question wants is decided by
    SCORING the material against the question, never by a list of keywords.
    """
    probes = [str(question or "").strip()]
    words = _ff_field_words(field)
    if words:
        probes.append("%s %s" % (question, words))
    out, seen = [], set()
    for probe in probes:
        key = _ff_norm(probe)
        if key and key not in seen:
            seen.add(key)
            out.append(probe.strip())
    return out[:_MAX_PROBES]


class FormFillerCragMixin:
    """Per-field retrieval through the CRAG pipeline."""

    # ---------------------------------------------------------------- corpus
    def _ff_doc_texts(self, documents):
        """Extract the attached documents to plain text (cached per set)."""
        key = tuple(str(d) for d in (documents or []))
        cache = getattr(self, "_ff_doc_text_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            try:
                self._ff_doc_text_cache = cache
            except Exception:
                pass
        if key in cache:
            return cache[key]
        texts = []
        try:
            from ....llm_executor_resources import rag_utils as _rag
            for p in documents or []:
                if not p or not isinstance(p, str) or not os.path.exists(p):
                    continue
                ext = os.path.splitext(p)[1].lower()
                if ext in (".txt", ".md", ".csv", ".json", ".log"):
                    t = _rag._read_text_file(p)
                elif ext == ".docx":
                    t = _rag._extract_docx_text(p)
                elif ext == ".pdf":
                    t = _rag._extract_pdf_text(p)
                else:
                    t = ""
                if t:
                    texts.append(t)
        except Exception as exc:
            logger.debug("[FORM] document text unavailable: %s", exc)
        cache[key] = texts
        return texts

    def _ff_crag_corpus(self, cfg, source_text, documents):
        """``[{text, section, block, block_text, head, source, sha}]`` - segments.

        Two units, and the split matters: the RETRIEVAL unit is the BLOCK (one
        section, ~250 chars) because a short label needs surrounding context to
        find its region; the EVALUATION unit is the SEGMENT, because the scored
        probability collapses when unrelated text follows the value inside one
        premise.  Both come from `AI/crag/corpus.shape` - the same function the
        Context node indexes with, so a stored vector and the segments it
        describes can never disagree.

        Cached per (document set, pool text): the field loop asks the same corpus
        for every field of the page.
        """
        key = (_ff_source_sig(documents, source_text),)
        cache = getattr(self, "_ff_crag_corpus_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            try:
                self._ff_crag_corpus_cache = cache
            except Exception:
                pass
        if key in cache:
            return cache[key]
        pool = _ff_split_pool(source_text)
        sources = []
        for text in self._ff_doc_texts(documents):
            sources.append(("documents", text))
        if pool["documents"]:
            sources.append(("documents", pool["documents"]))
        if pool["context"]:
            sources.append(("context", pool["context"]))
        entries, index_docs = [], []
        for origin, text in sources:
            shaped, meta = _corpus.shape(text, origin=origin)
            entries.extend(shaped)
            if meta:
                index_docs.append({"sha": _corpus.sha256_text(text),
                                   "blocks": meta, "doc_path": "",
                                   "text": text, "origin": origin})
        # One document is often BOTH attached and pooled, so the same segment
        # arrives twice: keep the first (its block, section and sha included).
        seen, unique = set(), []
        for entry in entries:
            k = _ff_norm(entry["text"])
            if not k or k in seen:
                continue
            seen.add(k)
            unique.append(entry)
        # The per-document block metadata the persisted index keys on, kept for
        # the ranking stage (AI/context_index.py).  Set here rather than derived
        # later because the deduped segments have lost which source they came from.
        try:
            self._ff_crag_index_docs = index_docs
        except Exception:
            pass
        cache[key] = unique
        return unique

    # ------------------------------------------------------------- retrieval
    def _ff_embed_call(self, cfg):
        """``texts -> vectors`` for the embedding evaluator (None on failure)."""
        client = _FFEmbedClient(cfg["engine"], cfg["model"])

        def _embed(texts):
            return client.embeddings(cfg["model"], list(texts)) or []

        return _embed

    @staticmethod
    def _ff_lexical_rank(query, entries):
        """Token-overlap ranking, used when the embeddings endpoint is down."""
        terms = [w for w in re.findall(r"[a-z0-9]{3,}", _ff_norm(query))]
        if not terms:
            return list(entries)
        scored = []
        for pos, entry in enumerate(entries):
            body = _ff_norm(entry["text"])
            hits = sum(1 for t in set(terms) if t in body)
            scored.append((hits, -pos, entry))
        scored.sort(reverse=True, key=lambda row: (row[0], row[1]))
        return [row[2] for row in scored if row[0]] or list(entries)

    def _ff_crag_candidates(self, query, entries, cfg, field=None, skip=None):
        """The SEGMENTS CRAG evaluates for one question.

        SLOTS, not a prepend.  The document's identity section is guaranteed
        ``_IDENTITY_SEGMENT_SLOTS`` segments, ORDERED BY WHAT THE QUESTION
        ASKS FOR (a 'Location (city)' field takes the city line first, an
        'Email' field the address) - that is where a name, phone, email and
        city live and they rank poorly against a short field label.  The rest
        of the budget goes to the blocks the question ACTUALLY matches,
        wherever they sit, with no single block taking more than
        ``_MAX_SEGMENTS_PER_BLOCK``.

        ONE SECTION for a single-valued field.  A block is one section, so
        following the best block's section keeps the strip list inside the
        part of the document that answers THIS question: a candidate set that
        mixes the header with an employer's rows is what let the extractor glue
        'Buenos Aires' (own city) to 'Saint Paul' (a 2018 employer's city).  A
        multiline control asks for prose rather than one value and keeps the
        broad sweep.

        Prepending every identity segment is what made the document unreachable:
        the first two blocks of a resume hold 11 segments and the budget was 9, so
        the ranking of the other 51 blocks was computed and then cut off, for
        every field, deterministically - and only a field the header happened to
        answer could ever be grounded in the document.

        ``skip`` is what the consolidation cycle has ALREADY scored: those
        segments are not offered again, so the next sweep spends its budget on
        the part of the document nothing has looked at yet.
        """
        if not entries:
            return []
        used = set(str(s) for s in (skip or ()) if str(s))
        probes = ([query] if isinstance(query, str) else list(query or []))
        probes = [str(p).strip() for p in probes if str(p or "").strip()]
        if not probes:
            probes = [_ff_question_head((field or {}).get("label") or "")]
        ranked = self._ff_ranked_blocks(probes, entries, cfg) or []
        # The subject section's own lines, best answer first, and whether that
        # section is where this question is answered at all - both SCORED (see
        # `_ff_subject_choice`), so no keyword decides what a field wants.
        identity, answered_by_head = self._ff_subject_choice(
            " ".join(probes),
            _ff_identity_rows(entries),
            ranked)
        single = not bool((field or {}).get("multiline"))
        out, seen = [], set()

        def _take(pool, limit):
            n = 0
            for entry in pool or []:
                body = _ff_norm(entry.get("text") or "")
                if not body or body in seen or body in used:
                    continue
                if n >= limit or len(out) >= _MAX_CANDIDATE_SEGMENTS:
                    return
                seen.add(body)
                out.append(entry)
                n += 1

        _take(identity, _IDENTITY_SEGMENT_SLOTS)
        # A question the subject block ANSWERS is answered by the slots above;
        # any other ('Current company', 'Current job title') is answered by a
        # SECTION of the document, so the header is not a candidate for it -
        # otherwise the single-section rule locked the field to the taglines
        # and an employer could never be reached (live: 'Current company' ->
        # verdict incorrect, 0 chars of evidence).
        section = None
        for group in ranked:
            if not group:
                continue
            sect = group[0].get("section") or ""
            if single:
                if not sect:
                    continue
                # ONE subject per value: when the subject block answers, a
                # later section describes somebody else; when it does not, the
                # answer is a body section and the header is not it.
                if answered_by_head != bool(group[0].get("head")):
                    continue
                # A block that IS only the section's own heading carries no
                # value: it must not become the section a field answers from
                # ('## Professional Experience' is a label, the job row under
                # it is the employer).
                if len(group) < 2 and _ff_norm(group[0].get("text")) == _ff_norm(sect):
                    continue
                if section is None:
                    section = sect
                elif sect != section:
                    continue
            _take(group, _MAX_SEGMENTS_PER_BLOCK)
        if len(out) < _MAX_CANDIDATE_SEGMENTS:
            # A document with little else in it should not waste forwards it has
            # already paid for: spend what is left on the remaining material -
            # still inside the section a single-valued field is answering from.
            def _same_section(rows):
                if not single:
                    return rows
                if section is None:
                    # No body section matched: only the header answered.
                    return [e for e in rows if e.get("head")]
                return [e for e in rows if (e.get("section") or "") == section]
            _take(_same_section(identity), _MAX_CANDIDATE_SEGMENTS)
            _take(_same_section(entries), _MAX_CANDIDATE_SEGMENTS)
        return out[:_MAX_CANDIDATE_SEGMENTS]

    # ------------------------------------------------------- block ranking
    def _ff_index_model_id(self, cfg):
        """The vector space this node's embedding client will produce.

        A stored vector may only be compared with a query from the SAME encoder,
        so the identity comes from the client's own routing (the local embedding
        server for a llama.cpp engine, the node's model for Ollama / a provider).
        Anything this module cannot name returns ``None`` - which simply means no
        shared index, never a wrong comparison.
        """
        try:
            from AI import context_index
            return context_index.model_id_for(cfg.get("engine"),
                                              cfg.get("model"))
        except Exception:
            return None

    def _ff_block_groups(self, entries):
        """``[(entries of one block, (sha, ordinal)), ...]`` in document order."""
        groups, order = {}, []
        for entry in entries or []:
            key = (str(entry.get("sha") or ""), int(entry.get("block") or 0))
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(entry)
        return [(groups[k], k) for k in order]

    def _ff_ranked_blocks(self, probes, entries, cfg):
        """This corpus's blocks, best-matching first, as lists of their segments.

        ``probes`` is the list of queries ONE field is asked with (see
        ``_ff_crag_probes``): a block keeps its BEST score across them, so a
        one-word label does not have to carry the whole question alone.

        Preferred: cosine against the Context node's PERSISTED block vectors, so a
        field costs ONE embedding burst no matter how long the document is.
        When nothing is indexed for this corpus - no Context node served it, a
        different embedding model, the store offline - the whole corpus is
        embedded in one burst and ranked here, the older route that still
        answers.  ``None`` only when no ranking is possible at all, and then the
        caller falls back to document order.
        """
        probes = ([probes] if isinstance(probes, str) else list(probes or []))
        probes = [str(p).strip() for p in probes if str(p or "").strip()]
        query = probes[0] if probes else ""
        groups = self._ff_block_groups(entries)
        if len(groups) <= 1:
            self._ff_corpus_route = "one block (nothing to rank)"
            return [g[0] for g in groups]
        model_id = self._ff_index_model_id(cfg)
        docs = list(getattr(self, "_ff_crag_index_docs", []) or [])
        by_key = dict((k, g) for g, k in groups)
        if model_id and docs:
            try:
                from AI import context_index
                hits = context_index.rank_blocks(
                    probes, docs, embed=self._ff_embed_call(cfg),
                    model_id=model_id, top_k=len(groups),
                    store=getattr(self, "_ff_chunk_store", None))
            except Exception as exc:  # noqa: BLE001 - the index is an accelerator
                logger.debug("[FORM] block index ranking failed: %s", exc)
                hits = None
            ordered = []
            seen_keys = set()
            for h in (hits or []):
                key = (str(h.get("sha") or ""), int(h.get("ordinal") or 0))
                if key in by_key and key not in seen_keys:
                    seen_keys.add(key)
                    ordered.append(by_key[key])
            if ordered:
                # A source the Context node did not index (the pool's stored rows
                # / history) stays reachable: it follows the ranked blocks in
                # document order rather than vanishing from the candidate set.
                rest = [g for g, k in groups if k not in seen_keys]
                self._ff_corpus_route = ("persisted index | %d ranked block(s) "
                                         "+ %d unindexed, %d probe(s)"
                                         % (len(ordered), len(rest),
                                            len(probes)))
                return ordered + rest
        embed = self._ff_embed_call(cfg)
        ranked = None
        try:
            vectors = list(embed(probes + [(g[0][0].get("block_text") or "")
                                           for g in groups]) or [])
            if len(vectors) == len(probes) + len(groups) and any(vectors):
                qvs = [v for v in vectors[:len(probes)] if v]
                scored = [(max(float(_ff_cos(qv, vec)) for qv in qvs), -i,
                           groups[i][0])
                          for i, vec in enumerate(vectors[len(probes):])]
                scored.sort(reverse=True, key=lambda row: (row[0], row[1]))
                ranked = [row[2] for row in scored if row[0] > 0.0]
                self._ff_corpus_route = ("embedded per field | %d block(s), "
                                         "%d probe(s) (no Context-node index "
                                         "for this corpus)"
                                         % (len(groups), len(probes)))
        except Exception as exc:  # noqa: BLE001 - ranking is best effort
            logger.debug("[FORM] CRAG block ranking failed: %s", exc)
        if ranked is None:
            proxy = [{"text": grp[0].get("block_text") or "", "pos": i}
                     for i, (grp, _k) in enumerate(groups)]
            self._ff_corpus_route = "lexical (no embeddings available)"
            return [groups[row["pos"]][0]
                    for row in self._ff_lexical_rank(query, proxy)]
        return ranked or [g[0] for g in groups]

    def _ff_crag_external(self, cfg, source_text, entries, field=None,
                          probes=None):
        """The corrective stores: the pool's other streams, then a re-read.

        The learned / verified records ARE a store here, deliberately: CRAG
        only consults them when the attached material was ambiguous or wrong,
        and every candidate must clear the lower threshold with the evaluator
        scoring THIS field's question - a keyed record that answers a
        different question scores low and is dropped.
        """
        pool = _ff_split_pool(source_text)
        blocks = []
        if pool["verified_text"]:
            blocks.append(pool["verified_text"])
        if pool["learned_text"]:
            blocks.append(pool["learned_text"])
        for block in re.split(r"\n\s*\n", pool["context"] or ""):
            if block.strip():
                blocks.append(block.strip())
        stores = [PoolStreams(blocks)]
        stores.append(ChunkStore(
            retrieve=lambda q: [e["text"]
                                for e in self._ff_crag_candidates(
                                    probes or q, entries, cfg, field=field)],
            max_chunks=max(1, int(cfg.get("crag_top_k", 6)))))
        return CRAGExternalKnowledge(stores,
                                     max_blocks=DEFAULT_MAX_BLOCKS)

    # ---------------------------------------------------------- the pack
    @staticmethod
    def _ff_segment_of(strip, candidates):
        """The candidate entry a kept strip came from (``None`` when split)."""
        body = _ff_norm(strip)
        if not body:
            return None
        rows = [e for e in (candidates or []) if e.get("text")]
        for entry in rows:
            if _ff_norm(entry["text"]) == body:
                return entry
        for entry in rows:
            other = _ff_norm(entry["text"])
            if other and (other in body or body in other):
                return entry
        head = " ".join(body.split()[:6])
        for entry in rows:
            if head and head in _ff_norm(entry["text"]):
                return entry
        return None

    def _ff_crag_pack(self, result, candidates):
        """The knowledge block: one VERBATIM strip per line, section-tagged.

        CRAG hands back the strips it kept; the section is what keeps a
        history row apart from the candidate's own contact block once the
        extractor reads them (live: 'Location (city)' answered with
        'Buenos Aires, Saint Paul' - the header city merged with the city of a
        2018 employer, because the pack was one '; '-joined blob).  The
        upstream `Knowledge1 / Knowledge2` split is kept for an ambiguous
        verdict, where the two stores really are two sources.
        """
        if not str(getattr(result, "material", "") or "").strip():
            return ""

        def _bullets(strips):
            lines = []
            for strip in strips or []:
                text = str(strip).strip()
                if not text:
                    continue
                entry = self._ff_segment_of(text, candidates)
                section = (entry or {}).get("section") or ""
                lines.append("- [%s] %s" % (section, text) if section
                             else "- %s" % text)
            return "\n".join(lines)

        internal = _bullets(getattr(result, "internal_strips", []) or [])
        external = _bullets(getattr(result, "external_strips", []) or [])
        if result.action == "ambiguous" and internal and external:
            body = "Knowledge1:\n%s\n [sep] Knowledge2:\n%s" % (internal,
                                                                external)
        elif result.action == "incorrect" and external:
            body = external
        else:
            body = internal or _bullets(getattr(result, "strips", []) or [])
        if not body.strip():
            return evidence_block(result.action, result.material,
                                  result.top_score)
        head = (
            "[Retrieved knowledge - CRAG verdict: %s (top score %.2f). Each "
            "line is a VERBATIM source segment; the bracket names the part of "
            "the document it came from. Copy every exact value character-for-"
            "character, and answer from ONE line - a line tagged with a "
            "DIFFERENT section describes another subject, not this one.]" % (
                str(result.action or "unknown"), float(result.top_score or 0.0))
        )
        return evidence_block(result.action, body, result.top_score,
                              extra_heading=head)

    # -------------------------------------------------------- the answer scores
    def _ff_answer_scores(self, question, texts):
        """P(each text answers *question*), from Laya's own scoring.

        The only source of "what does this question want?" - so a form in any
        language is answered by meaning rather than by a keyword table.  An
        engine fault propagates (``LayaUnavailable``): a missing engine is
        never a reason to decide a meaning some other way.

        Cached per (question, LINE), not per batch: the same header line comes
        back in the next consolidation cycle inside a DIFFERENT batch (the
        subject block is scored first, then reappears among the candidates),
        and a batch key charged it a second forward every time.  Only the lines
        this call has not scored yet reach the engine.  Bounded FIFO, so a
        long form cannot grow the cache without limit.
        """
        rows = [str(t or "") for t in (texts or [])]
        if not rows:
            return []
        cache = getattr(self, "_ff_answer_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            try:
                self._ff_answer_cache = cache
            except Exception:
                pass
        ask = str(question or "")[:400]
        out = [None] * len(rows)
        missing = []
        for index, row in enumerate(rows):
            if (ask, row) in cache:
                out[index] = cache[(ask, row)]
            else:
                missing.append(index)
        if not missing:
            return out
        from AI.laya_hooks import answer_scores
        fresh = list(answer_scores(ask, [rows[i] for i in missing]) or [])
        for slot, index in enumerate(missing):
            score = fresh[slot] if slot < len(fresh) else None
            out[index] = score
            if score is not None:
                if len(cache) >= _ANSWER_CACHE_LIMIT:
                    for _k in list(cache.keys())[:max(1, _ANSWER_CACHE_LIMIT // 8)]:
                        cache.pop(_k, None)
                cache[(ask, rows[index])] = score
        return out

    def _ff_subject_choice(self, question, identity, ranked):
        """Is this question answered by the SUBJECT block?  ``(rows, answered)``.

        The block's own lines are scored (best answer first), and its best line
        is then compared with the best BODY sections: a question the subject
        block does not answer belongs to a later section, and a later section
        that answers it BETTER wins even when one of the block's lines scored
        above the engine's threshold on its own.
        """
        rows = list(identity or [])
        if not rows:
            return rows, False
        from AI.laya_hooks import ANSWER_THRESHOLD
        scores = self._ff_answer_scores(
            question, [str(e.get("text") or "") for e in rows])
        ordered = _ff_identity_order(rows, scores)
        best = max(scores) if scores else 0.0
        if best < ANSWER_THRESHOLD:
            return ordered, False
        body = [g for g in (ranked or [])
                if g and not g[0].get("head")][:_MAX_SECTION_PROBES]
        if body:
            body_scores = self._ff_answer_scores(
                question, [_ff_section_text(g) for g in body])
            if body_scores and max(body_scores) > best:
                return ordered, False
        return ordered, True

    # ------------------------------------------------------------- evaluator
    def _ff_crag_evaluator(self, cfg, stop_flag):
        """Resolve the scored backend (Laya by default, embeddings on fallback)."""
        return make_evaluator(
            cfg.get("crag_evaluator") or "auto",
            embed=self._ff_embed_call(cfg),
            llm=lambda prompt, system: self._ff_llm_call(
                prompt, system, cfg, stop_flag),
        )

    # -------------------------------------------------------------- the pass
    def _ff_learned_head(self, learned_hint):
        """The labeled head for one field's learned answer ('' when none)."""
        v = str(learned_hint or "").strip()
        if not v:
            return ""
        return ("[LEARNED - the user already answered this field; reuse it "
                "or ADAPT it to the exact format this field requires]:\n%s" % v)

    def _ff_retrieve(self, label, cfg, source_text, documents=None,
                     stop_flag=None, repair_hint="", learned_hint="",
                     field=None):
        """The material for ``label``: CRAG's evaluated knowledge, or ''.

        One retrieval, one scored pass over the passages, one verdict.  The
        pack is VERBATIM source sentences (CRAG's refinement keeps strips, it
        never paraphrases), so an exact value survives; and a verdict of
        ``incorrect`` with nothing external behind it returns '' - the field
        then takes the existing "nothing retrieved" path (ask the user), which
        is the whole point of the corrective action.  ``field`` is the page's
        own control dict: its kind, placeholder and words shape the QUERY (and
        the single-section rule), never the stored key or the answer.
        """
        if not label or (not source_text and not documents):
            return ""
        if self._ff_halt(stop_flag):
            return ""
        if not learned_hint:
            learned_hint = getattr(self, "_ff_learned_hint", "")
        self._ff_learned_hint = ""

        question = _ff_question_head(label)
        if not question:
            return ""
        # A repair run targets a FORMAT: the field's question still leads, the
        # required shape rides along so the retrieved strip carries it.
        query = question if not repair_hint else ("%s\n%s" % (question,
                                                             repair_hint))
        probes = _ff_crag_probes(query, field)
        memo = getattr(self, "_ff_pass_results", None)
        if not isinstance(memo, dict):
            memo = {}
            try:
                self._ff_pass_results = memo
            except Exception:
                pass
        memo_key = (_ff_norm(label), _ff_norm(repair_hint))
        if memo_key in memo and not learned_hint:
            cached = memo[memo_key]
            self._ff_last_route = cached.get("route", "")
            self._ff_last_action = cached.get("action", "")
            self._ff_last_kept = cached.get("kept", 0)
            return cached.get("pack", "")

        entries = self._ff_crag_corpus(cfg, source_text, documents)
        self._ff_corpus_route = ""
        learned = self._ff_learned_head(learned_hint)

        if not cfg.get("crag_enabled", True) or not entries:
            candidates = (self._ff_crag_candidates(probes, entries, cfg,
                                                  field=field)
                          if entries else [])
            passages = [e["text"] for e in candidates]
            pack = self._ff_single_pass(passages, learned)
            self._ff_last_route = "single-pass (CRAG off or no corpus)"
            self._ff_last_action = ""
            self._ff_last_kept = len(passages)
            memo[memo_key] = {"pack": pack, "route": self._ff_last_route,
                              "action": "", "kept": self._ff_last_kept}
            return pack

        evaluator = self._ff_crag_evaluator(cfg, stop_flag)
        _field_started = time.perf_counter()
        _forwards0 = int(getattr(evaluator, "forwards", 0) or 0)
        rewrite_llm = None
        if cfg.get("crag_rewrite", True):
            rewrite_llm = lambda prompt, system: self._ff_llm_call(
                prompt, system, cfg, stop_flag)
        try:
            cycles = max(1, int(cfg.get("crag_cycles") or 1))
        except (TypeError, ValueError):
            cycles = 1

        # CONSOLIDATION CYCLES (the loop ComoRAG ran, on this node's terms).
        # A sweep that comes back `incorrect`/`ambiguous` has only told us
        # where the answer is NOT; the next one is handed the material the
        # first already scored, so it searches where nothing has looked yet -
        # and the query the corrective stage REWROTE leads it there.  The
        # cycles stop the moment a sweep is `correct`, or when a sweep has no
        # fresh segment left (saturation): `crag_cycles` is a ceiling, never a
        # quota to serve.  Each cycle is scored on its OWN, and the best one
        # wins outright - strips are never merged across cycles, because a
        # merged pack is what let two sections answer one field.
        best = None
        seen = set()
        round_probes = probes
        try:
            for index in range(cycles):
                if self._ff_halt(stop_flag):
                    break
                attempt = self._ff_crag_cycle(
                    question, query, round_probes, entries, cfg, field=field,
                    source_text=source_text, stop_flag=stop_flag, seen=seen,
                    evaluator=evaluator, rewrite_llm=rewrite_llm,
                    cycle=(index + 1, cycles))
                if attempt is None:
                    if best is None:
                        log_block(logger, logging.INFO, "CRAG",
                                  "cycle %d has no fresh segment left - "
                                  "the sweep is saturated" % (index + 1))
                    break
                seen |= attempt["seen"]
                if best is None or attempt["score"] > best["score"]:
                    best = attempt
                if attempt["done"]:
                    break
                nxt = _ff_next_probes(attempt, question, round_probes)
                round_probes = nxt
                broken = str(attempt.get("rewritten") or "").strip()
                if broken and _ff_norm(broken) != _ff_norm(query):
                    query = "%s\n%s" % (question, broken)
        finally:
            # Laya and the embedding server are burst resources, but a FORM
            # PASS holds them (``AI/engine_residency.py``): releasing per field
            # is what made every field pay a cold load.  Only an unheld engine
            # is released here, so a standalone call still frees the pool.
            _held = False
            try:
                from AI import laya_client as _laya
                _held = bool(_laya.held())
            except Exception:
                _held = False
            if not _held:
                evaluator.release()
            recorder = _ff_metrics()
            if recorder is not None:
                _verdict = (str(best.get("action") or "").lower()
                            if isinstance(best, dict) else "")
                recorder.record(
                    "form_field",
                    ms=round((time.perf_counter() - _field_started) * 1000, 1),
                    forwards=max(0, int(getattr(evaluator, "forwards", 0) or 0)
                                 - _forwards0),
                    kept=int(best.get("kept", 0)) if isinstance(best, dict) else 0,
                    # 2 correct / 1 ambiguous / 0 incorrect / -1 unknown: the
                    # verdict as a NUMBER, because this log holds numbers only.
                    action={"correct": 2, "ambiguous": 1,
                            "incorrect": 0}.get(_verdict, -1),
                    held=1 if _held else 0)

        if best is None:
            pack = self._ff_single_pass([], learned)
            self._ff_last_route = "single-pass (nothing to score)"
            self._ff_last_action = ""
            self._ff_last_kept = 0
            memo[memo_key] = {"pack": pack, "route": self._ff_last_route,
                              "action": "", "kept": 0}
            return pack

        self._ff_last_action = best["action"]
        self._ff_last_kept = best["kept"]
        self._ff_last_scored = best.get("scored", best["kept"])
        self._ff_last_route = best["route"]
        pack = best["pack"]
        if not pack and best.get("plain"):
            # The evaluator itself failed: the sweep still hands over what it
            # retrieved, plainly marked as unscored material.
            pack = self._ff_single_pass(best["plain"], learned)
            learned = ""
        if learned and pack:
            pack = "%s\n\n%s" % (learned, pack)
        elif learned and not pack:
            pack = learned
        pack = _ff_drop_foreign_qa(pack, label) if pack else ""
        memo[memo_key] = {"pack": pack, "route": self._ff_last_route,
                          "action": best["action"], "kept": best["kept"]}
        return pack

    def _ff_crag_cycle(self, question, query, probes, entries, cfg, field=None,
                       source_text="", stop_flag=None, seen=None,
                       evaluator=None, rewrite_llm=None, cycle=(1, 1)):
        """ONE scored sweep: candidates -> CRAG verdict -> verbatim strips.

        Returns a dict describing the sweep (``None`` when nothing is left to
        score), so the caller can decide whether another one is worth its
        forwards: ``{pack, action, kept, score, done, rewritten, route, seen}``.
        """
        candidates = self._ff_crag_candidates(probes, entries, cfg, field=field,
                                              skip=seen)
        if not candidates:
            return None
        passages = [e["text"] for e in candidates]
        used = set(_ff_norm(p) for p in passages if _ff_norm(p))
        # The candidate stage ALREADY bounds the passage count (head blocks +
        # the best block, capped in segments).  `top_k` is upstream's retrieval
        # cap, and applying it again here silently dropped the tail of that
        # set - which is where the contact block's email/phone segments sit
        # when the field is not about the name (live: 'Email address' lost its
        # own address and came out `ambiguous`).
        pconfig = default_config_from(cfg)
        pconfig.top_k = max(len(passages), int(pconfig.top_k or 0))
        pipeline = CRAGPipeline(
            evaluator,
            external=self._ff_crag_external(cfg, source_text, entries,
                                            field=field, probes=probes),
            rewrite_llm=rewrite_llm,
            config=pconfig,
        )
        try:
            result = pipeline.run(query, chunks=passages, stop_flag=stop_flag)
        except CRAGUnavailable as exc:
            log_block(logger, logging.WARNING, "CRAG",
                      "the %s evaluator scored nothing (%s) - single-pass"
                      % (evaluator.name, exc))
            return {"pack": "", "action": "", "kept": len(passages),
                    "scored": len(passages), "score": 0.0, "done": True,
                    "rewritten": "", "seen": used,
                    "route": "single-pass (evaluator unavailable)",
                    "plain": passages}
        except Exception as exc:  # noqa: BLE001 - a fault must not eat the field
            if self._ff_laya_fault(exc):
                raise
            log_block(logger, logging.WARNING, "CRAG",
                      "the pipeline failed for '%s': %s"
                      % (question[:60], exc))
            return {"pack": "", "action": "", "kept": len(passages),
                    "scored": len(passages), "score": 0.0, "done": True,
                    "rewritten": "", "seen": used,
                    "route": "single-pass (CRAG failed)",
                    "plain": passages}

        action = str(result.action or "")
        if (action == "correct"
                and not (field or {}).get("multiline")
                and not _ff_answer_shaped(result.material,
                                         (field or {}).get("options"))):
            # `correct` is a claim that the strip ANSWERS the question, and the
            # encoder only measured topic overlap.  Downgrade instead of
            # handing the extractor strips that cannot hold the value, so the
            # consolidation cycle may still look elsewhere.
            log_block(
                logger, logging.WARNING, "CRAG",
                "verdict downgraded to ambiguous: the kept strips hold no "
                "answer shape (no number/date, none of the control's own "
                "options) for %r" % question[:60])
            action = "ambiguous"
            result.action = action

        rows = [
            ("Question", question[:200]),
            ("Cycle", "%d of %d (ceiling)" % (cycle[0], cycle[1])),
            ("Corpus", "%s | %d segment(s) of %d" % (
                getattr(self, "_ff_corpus_route", "") or "?",
                len(candidates), len(entries or []))),
            ("Evaluator", "%s (%d forward(s))" % (
                result.evaluator, result.forwards)),
            ("Verdict", "%s (>= %.2f correct, >= %.2f ambiguous)" % (
                action, cfg.get("crag_upper_threshold", 0.60),
                cfg.get("crag_lower_threshold", 0.40))),
            ("Passages", "%d scored, top %.3f" % (
                len(result.chunk_scores), result.top_score)),
            ("Refined", "%d strip(s) kept of %d scored | decompose: %s" % (
                result.kept, result.kept + result.dropped,
                cfg.get("crag_decompose_mode") or "excerption")),
            ("Corrective", (result.rewritten
                            and "rewrote -> %r" % result.rewritten[:120])
             or ("external knowledge used" if result.used_external
                 else "not needed" if result.action != "incorrect"
                 else "nothing external")),
            ("Elapsed", "%.1fs" % result.elapsed),
        ]
        log_table(logger, logging.INFO, "CRAG Retrieval", rows)
        upper = float(cfg.get("crag_upper_threshold", 0.60) or 0.60)
        return {
            "pack": self._ff_crag_pack(result, candidates),
            "action": action,
            "kept": result.kept,
            "scored": result.kept + result.dropped,
            "score": float(result.top_score or 0.0),
            "done": (action == "correct"
                     or float(result.top_score or 0.0) >= upper),
            "rewritten": str(result.rewritten or ""),
            "route": result.summary(),
            "seen": used,
            "plain": [],
        }

    def _ff_single_pass(self, chunks, learned=""):
        """Plain embedding retrieval: no verdict, just the best segments."""
        body = "\n".join("- %s" % str(c).strip()
                          for c in (chunks or []) if str(c).strip())
        if body:
            body = ("[Retrieved segments - VERBATIM source text, no CRAG "
                    "verdict]\n" + body)
        parts = [p for p in (learned, body) if p]
        return "\n\n".join(parts)

    # ----------------------------------------------------------------- rails
    def _ff_stop_requested(self, stop_flag):
        """True when the user pressed ESC (the shared keyboard monitor flag)."""
        try:
            return bool(stop_flag and stop_flag())
        except Exception:
            return False

    def _ff_halt(self, stop_flag):
        """Sticky "stop this node NOW" state (ESC).

        Latched by ``_ff_llm_call`` (the single funnel for every model call)
        and checked by the retrieval, the extract, the write and the field
        loop, so ESC does not wait for a whole field to finish.
        """
        if self._ff_stop_requested(stop_flag):
            self._ff_abort = True
        return bool(getattr(self, "_ff_abort", False))

    @staticmethod
    def _ff_laya_fault(exc):
        """True when *exc* is Laya refusing to answer.

        A run must NOT swallow that: the engine ships with the app, so an
        unreachable Laya is a fault the run has to surface, not a reason to
        carry on with a degraded decision.
        """
        try:
            from AI.laya_hooks import LayaUnavailable
            return isinstance(exc, LayaUnavailable)
        except Exception:
            return False
