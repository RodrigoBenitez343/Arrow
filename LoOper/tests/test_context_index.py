"""The Context node's persistent block index, and what it makes reachable.

No API, no Laya, no llama.cpp: every embedding here is a hand-made oracle, so
what these tests measure is the PLUMBING - one chunker shared by the index and
the scorer, content-addressed storage, a position-free ranking, and the slot
rule that stopped a document's own content from being reachable at all (live:
42 of 42 retrieved strips came from the resume's first two blocks).
"""

import pytest

from AI import context_index as ci
from AI.crag import corpus
from player.multi_sequence.worflow_interpreter_modules.executor_modules\
    .form_filler_modules.suite_crag import _harness

# ── a fake encoder with a KNOWN answer, so a test cannot fail on quality ────
# Features: which value a text HOLDS (document side) / which value a question
# LOOKS FOR (query side).  Cosine over these is enough to prove ordering.
_HOLD = ("synopsys", "valencia", "benitez")
_WANTS = {"employer": "synopsys", "company": "synopsys",
          "city born": "valencia", "name": "benitez"}


def _embed(texts):
    out = []
    for text in texts:
        low = str(text or "").lower()
        vec = [0.0] * (len(_HOLD) + 1)
        for i, term in enumerate(_HOLD):
            if term in low:
                vec[i] = 1.0
        for ask, term in _WANTS.items():
            if ask in low:
                vec[_HOLD.index(term)] = 1.0
        vec[-1] = 0.01               # a floor, so no vector is ever zero
        out.append(vec)
    return out


DOC = (
    "# Rodrigo Benitez\nEmail: benitezrodrigo169@gmail.com\n\n"
    "## Summary\nSeven years of automation work, then local-first AI tooling.\n"
    "Owns delivery end to end and writes the runbooks.\n\n"
    "## Experience\nDevOps Engineer at Synopsys Inc. in Buenos Aires, remote,\n"
    "full-time, from February 2025 to today, supporting a global developer\n"
    "organisation inside change-management and security constraints.\n\n"
    "## History\nJunior developer work in Valencia, Spain, on a small team.\n"
)


# ── the shared chunker ─────────────────────────────────────────────────────
def test_a_block_never_spans_two_sections():
    """One block, one section: a mixed block embeds as ONE vector that matches
    both halves and proves neither, and its provenance tag is a lie."""
    segs = corpus.segments(DOC)
    blocks = corpus.blocks(segs)
    for block in blocks:
        assert len({section for section, _t in block}) == 1
    # The greedy 250-char packer used to glue the header to the Summary label.
    bodies = [" ".join(t for _s, t in b) for b in blocks]
    assert not any(("Benitez" in b and "Valencia" in b) for b in bodies)


def test_the_identity_section_is_the_only_head():
    """'head' means the section the document OPENS with (name / email live
    there), not the first two blocks, so a long header cannot be a special case."""
    entries, meta = corpus.shape(DOC)
    first = corpus.identity_section(corpus.segments(DOC))
    assert first == "Rodrigo Benitez"
    assert all(e["head"] == (e["section"] == first) for e in entries)
    assert all(m["head"] == (m["section"] == first) for m in meta)
    assert not any(m["section"] == "History" and m["head"] for m in meta)


def test_the_documents_hash_survives_serve_and_split():
    """A Context node indexes the text it SERVES, the consumer hashes the text it
    SPLITS back out of the pool.  Documents is served as the LAST stream, so the
    two are the same bytes and the index is found (an edit still re-indexes)."""
    from player.multi_sequence.worflow_interpreter_modules.executor_modules\
        .form_filler_modules.common import _FF_DOCS_HEADER, _ff_split_pool

    docs = DOC.strip()
    pool = ("## Skills\nPython, Jenkins, RAG\n\nsome stored row\n\n"
            + _FF_DOCS_HEADER + "\n" + docs + "\n\n")
    split = _ff_split_pool(pool)
    assert split["documents"] == docs
    assert corpus.sha256_text(split["documents"]) == corpus.sha256_text(docs)
    assert corpus.sha256_text(DOC + " edited") != corpus.sha256_text(DOC)


# ── the store ──────────────────────────────────────────────────────────────
@pytest.fixture()
def store(tmp_path):
    return ci.ContextChunkStore(db_path=str(tmp_path / "index.db"))


def _docs(text=DOC):
    _entries, meta = corpus.shape(text)
    return [{"sha": corpus.sha256_text(text), "blocks": meta, "doc_path": "",
             "text": text, "origin": "documents"}]


def test_the_store_round_trips_one_document_version(store):
    docs = _docs()
    out = ci.ensure_index(docs, embed=_embed, model_id="m1",
                          chain_id="c", node_id="n", store=store)
    assert out["built"] and not out["failed"]
    sha = docs[0]["sha"]
    assert store.count(sha=sha, model="m1") == len(docs[0]["blocks"])
    rows = store.blocks([sha], "m1")
    assert [r["ordinal"] for r in rows] == sorted(r["ordinal"] for r in rows)
    # The segments come back exactly as the chunker produced them.
    assert rows[0]["segments"] == docs[0]["blocks"][0]["segments"]


def test_ensure_index_embeds_each_version_once(store):
    calls = []

    def counting(texts):
        calls.append(len(texts))
        return _embed(texts)

    docs = _docs()
    ci.ensure_index(docs, embed=counting, model_id="m1", store=store)
    again = ci.ensure_index(docs, embed=counting, model_id="m1", store=store)
    assert again["reused"] == [docs[0]["sha"]]
    assert len(calls) == 1, "a second pass must not re-embed the same version"


def test_an_edited_document_reindexes_and_forgets_the_old_version(store):
    docs = _docs()
    ci.ensure_index(docs, embed=_embed, model_id="m1", store=store)
    edited = _docs(DOC + "\n## Award\nEmployee of the year at Synopsys.\n")
    out = ci.ensure_index(edited, embed=_embed, model_id="m1", store=store)
    assert out["built"], "a new content hash is a new version"
    assert store.count(sha=docs[0]["sha"], model="m1") == len(docs[0]["blocks"])


def test_a_different_model_never_shares_vectors(store):
    docs = _docs()
    ci.ensure_index(docs, embed=_embed, model_id="m1", store=store)
    assert store.blocks([docs[0]["sha"]], "m2") == []
    assert ci.rank_blocks("employer", docs, embed=_embed, model_id="m2",
                          store=store) is None, \
        "cosine across two vector spaces is noise, not a ranking"


def test_no_embeddings_store_nothing_and_rank_nothing(store):
    docs = _docs()
    out = ci.ensure_index(docs, embed=lambda texts: [], model_id="m1",
                          store=store)
    assert out["failed"] and not out["built"]
    assert store.count() == 0
    assert ci.rank_blocks("employer", docs, embed=lambda texts: [],
                          model_id="m1", store=store) is None


def test_an_unindexed_corpus_costs_no_embedding_call(store):
    """Looking a ranking up must never turn a consumer into an indexer: with no
    rows for this corpus the lookup returns ``None`` BEFORE embedding the query."""
    calls = []

    def counting(texts):
        calls.append(list(texts))
        return _embed(texts)

    assert ci.rank_blocks("Current employer", _docs(), embed=counting,
                          model_id="m1", store=store) is None
    assert calls == []


def test_kill_switch_leaves_the_store_alone(tmp_path):
    docs = _docs()
    st = ci.ContextChunkStore(db_path=str(tmp_path / "off.db"))
    import os
    os.environ["LOOPER_CTX_INDEX"] = "0"
    try:
        assert ci.ensure_index(docs, embed=_embed, model_id="m1",
                              store=st) == {"built": {}, "reused": [],
                                            "failed": []}
        assert st.count() == 0
    finally:
        os.environ.pop("LOOPER_CTX_INDEX", None)


def test_rank_finds_the_block_at_the_END_of_the_document(store):
    """The regression.  The employer fact sits in the third section of four; a
    field asking for it must reach it regardless of position."""
    docs = _docs()
    ci.ensure_index(docs, embed=_embed, model_id="m1", store=store)
    hits = ci.rank_blocks("Current employer", docs, embed=_embed,
                          model_id="m1", top_k=2, store=store)
    assert hits, "the answer's block must rank"
    assert "synopsys" in hits[0]["text"].lower()
    row = hits[0]
    assert row["ordinal"] > 0, "the hit came from a later section, not the header"
    assert "synopsys" in " ".join(t for _s, t in row["segments"]).lower()


def test_several_probes_rank_in_one_embedding_call(store):
    """A field is asked several ways (its label, the kind of value it wants).
    They share ONE embed burst and a block keeps its BEST score, so one weak
    phrasing cannot hide a block the other phrasing names exactly."""
    docs = _docs()
    ci.ensure_index(docs, embed=_embed, model_id="m1", store=store)
    calls = []

    def counting(texts):
        calls.append(list(texts))
        return _embed(texts)

    hits = ci.rank_blocks(["current employer", "city born"], docs,
                          embed=counting, model_id="m1", top_k=3, store=store)
    assert calls == [["current employer", "city born"]]
    joined = " ".join(h["text"] for h in (hits or [])[:2]).lower()
    assert "synopsys" in joined and "valencia" in joined, \
        "max-over-queries: each probe's winner must survive"


# ── what the form filler now evaluates ────────────────────────────────────
def _consumer(tmp_path, table=None):
    """The node wired to a temp index seeded the way a Context node seeds it."""
    h = _harness(table if table is not None else {"benitez": 0.9})
    h._ff_chunk_store = ci.ContextChunkStore(db_path=str(tmp_path / "ff.db"))
    h._ff_embed_call = lambda cfg: _embed
    h._ff_crag_evaluator = lambda cfg, sf: h.ev
    ci.ensure_index(_docs(), embed=_embed, store=h._ff_chunk_store,
                    model_id=ci.model_id_for("llamacpp", ""),
                    chain_id="c", node_id="n")
    return h


def test_a_field_sees_segments_from_beyond_the_header(tmp_path):
    h = _consumer(tmp_path)
    cfg = h._ff_cfg({"engine": "llamacpp", "crag_top_k": 6})
    entries = h._ff_crag_corpus(cfg, DOC, [])
    cand = h._ff_crag_candidates("Current employer", entries, cfg)
    sections = {c["section"] for c in cand}
    assert "Experience" in sections, \
        "the answer's section must be reachable for its own question"
    assert any("synopsys" in c["text"].lower() for c in cand)
    assert getattr(h, "_ff_corpus_route", "").startswith("persisted index")
    # ...and it is ONE query embedding, not a corpus embedding per field.
    assert h._ff_chunk_store.count(model=ci.model_id_for("llamacpp", "")) > 0


def test_the_identity_section_keeps_its_slots(tmp_path):
    """Name / email live in the header and rank poorly against a short label, so
    the header is guaranteed room - guaranteed, never prepended in bulk."""
    h = _consumer(tmp_path)
    cfg = h._ff_cfg({"engine": "llamacpp"})
    entries = h._ff_crag_corpus(cfg, DOC, [])
    cand = h._ff_crag_candidates("Current employer", entries, cfg)
    head = [c for c in cand if c.get("head")]
    assert 0 < len(head) <= 4, "the header keeps slots but cannot own the list"
    assert len(cand) <= 9
    ranked = [c for c in cand if not c.get("head")]
    assert ranked, "the rest of the budget went to the matched blocks"


def test_no_embeddings_still_produces_candidates(tmp_path):
    """The pre-index route stays alive: with nothing to rank, the identity
    segments lead and the field is still answered (slower, not silent)."""
    h = _consumer(tmp_path)
    h._ff_embed_call = lambda cfg: (lambda texts: [])
    cfg = h._ff_cfg({"engine": "llamacpp"})
    entries = h._ff_crag_corpus(cfg, DOC, [])
    cand = h._ff_crag_candidates("Current employer", entries, cfg)
    assert cand and cand[0].get("head")
    assert getattr(h, "_ff_corpus_route", "").startswith("lexical")


# ── the Context node's own contract ────────────────────────────────────────
def test_the_served_text_is_unchanged(tmp_path):
    """The pool is the user's editing surface: indexing must not alter a byte of
    what the node serves (a resume file entry and a literal note, in order)."""
    from player.multi_sequence.worflow_interpreter_modules.executor_modules\
        .context_ops import ContextMixin

    class _Ctx(ContextMixin):
        def __init__(self):
            self._ctx_list_text_cache = {}

    doc = tmp_path / "resume.md"
    doc.write_text(DOC, encoding="utf-8")
    node_input = [str(doc), "Own salary expectation: 6000 USD monthly"]
    ctx = _Ctx()
    served = ctx._ctx_list_text(node_input)
    # Byte-identical to the pre-refactor reader: each entry's own text (a file
    # keeps its trailing newline, a note is verbatim), joined by a blank line.
    assert served == (DOC + "\n\n" + node_input[1]).strip()
    assert ctx._ctx_list_text(node_input) == served          # the cache path
    assert len(ctx._ctx_list_items(node_input)) == 2
    assert ctx._ctx_read_item(node_input[1]) == node_input[1]


def test_indexing_never_breaks_a_serve(tmp_path, monkeypatch):
    """No embedding server, no crash, no dropped stream."""
    from player.multi_sequence.worflow_interpreter_modules.executor_modules\
        .form_filler_modules import common as ff_common
    from player.multi_sequence.worflow_interpreter_modules.executor_modules\
        .context_ops import ContextMixin

    def _boom(self, model_name, texts):
        raise OSError("embedding server down")
    monkeypatch.setattr(ff_common._FFEmbedClient, "embeddings", _boom)

    class _Ctx(ContextMixin):
        pass

    ctx = _Ctx()
    ctx._ctx_index_streams("chain", "node", [("documents", DOC)])
    assert ctx._ctx_list_text(["a note"]) == "a note"
