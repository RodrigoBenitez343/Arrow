"""Context index purge: a temporal corpus must not outlive its run.

The store keys every block by ``(sha256, model, ordinal)`` and records WHO
pointed at a version in ``ctx_chunk_owners``.  ``purge`` drops one node's
claims and only then the vectors nobody else claims - so a corpus scraped for
a single run costs no disk afterwards, and a shared one keeps its index.

Run:  python -m pytest LoOper/tests/test_context_index_purge.py
"""

import AI.context_index as ci
from AI.crag import corpus

DOC = ("# Rodrigo Benitez\nEmail: benitezrodrigo169@gmail.com\n\n"
       "## Experience\nDevOps Engineer at Synopsys Inc. in Buenos Aires.\n")


def _docs():
    _entries, meta = corpus.shape(DOC, origin="documents")
    return [{"sha": corpus.sha256_text(DOC), "blocks": meta, "doc_path": "",
             "text": DOC, "origin": "documents"}]


def _sha(docs):
    return docs[0]["sha"]


def _embed(texts):
    return [[1.0, 0.0, float(i)] for i, _ in enumerate(texts)]


def _store(tmp_path):
    return ci.ContextChunkStore(db_path=str(tmp_path / "ctx.db"))


def test_purge_removes_a_nodes_vectors_and_its_claims(tmp_path):
    store = _store(tmp_path)
    docs = _docs()
    model = "local:test"
    ci.ensure_index(docs, embed=_embed, model_id=model,
                    chain_id="c1", node_id="ctx_a", store=store)
    assert store.count(sha=_sha(docs), model=model) > 0

    removed = store.purge(chain_id="c1", node_id="ctx_a")
    assert removed > 0
    assert store.count(sha=_sha(docs), model=model) == 0
    assert store.purge(chain_id="c1", node_id="ctx_a") == 0, "idempotent"


def test_purge_keeps_a_shared_corpus(tmp_path):
    store = _store(tmp_path)
    docs = _docs()
    model = "local:test"
    ci.ensure_index(docs, embed=_embed, model_id=model,
                    chain_id="c1", node_id="ctx_a", store=store)
    ci.ensure_index(docs, embed=_embed, model_id=model,
                    chain_id="c1", node_id="ctx_b", store=store)

    removed = store.purge(chain_id="c1", node_id="ctx_a")
    assert removed == 0, "another node still claims these vectors"
    assert store.count(sha=_sha(docs), model=model) > 0

    assert store.purge(chain_id="c1", node_id="ctx_b") > 0
    assert store.count(sha=_sha(docs), model=model) == 0


def test_purge_without_a_target_is_a_no_op(tmp_path):
    store = _store(tmp_path)
    assert store.purge() == 0


def test_purge_by_sha(tmp_path):
    store = _store(tmp_path)
    docs = _docs()
    model = "local:test"
    ci.ensure_index(docs, embed=_embed, model_id=model,
                    chain_id="c1", node_id="ctx_a", store=store)
    assert store.purge(shas=[_sha(docs)]) > 0
    assert store.count(sha=_sha(docs), model=model) == 0
