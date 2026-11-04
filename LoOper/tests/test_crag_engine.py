"""The CRAG port itself: strips, scoring, routing, correction, generation.

No Laya, no network, no torch: every backend here is a stub, so the tests
measure the PORTED LOGIC (upstream `utils/crag`) and nothing else.
"""

from AI.crag import (CRAGConfig, CRAGExternalKnowledge, CRAGPipeline,
                     ChunkStore, EmbeddingEvaluator, PoolStreams,
                     RetrievalEvaluator, classify, decompose,
                     evidence_block, extract_strips_from_psg, postprocess_answer,
                     process_flag, rewrite_query, select_relevants)
from AI.crag.decompose import StripPlanner
from AI.crag.evaluator import cache as score_cache


class _Stub(RetrievalEvaluator):
    """One score per (query, passage) from a substring table."""

    name = "stub"
    namespace = "stub"

    def __init__(self, table=None, default=0.0, cache_enabled=False):
        super().__init__(cache_enabled=cache_enabled)
        self.table = table or {}
        self.default = default
        self.seen = []

    def _score_one(self, query, text):
        self.seen.append(text)
        hits = [v for k, v in self.table.items() if k in text]
        return hits[0] if hits else self.default


# ── decompose-then-recompose (internal_knowledge_preparation) ─────────────
def test_fixed_num_windows_are_fifty_words_and_merge_a_short_tail():
    words = ["w%d" % i for i in range(105)]
    strips = extract_strips_from_psg(" ".join(words), mode="fixed_num")
    # 105 words = 50 + 50 + a 5-word tail; a tail under 10 words is merged
    # into the LAST strip rather than becoming a fragment of its own.
    assert len(strips) == 2
    assert len(strips[0].split()) == 50
    assert len(strips[1].split()) == 55


def test_excerption_groups_three_sentences_and_glues_short_ones():
    text = (
        "one two three four five six seven. "
        "eight nine ten eleven twelve thirteen fourteen. "
        "fifteen sixteen seventeen eighteen nineteen twenty. "
        "short one. "
        "twentyone twentytwo twentythree twentyfour twentyfive twentysix."
    )
    strips = extract_strips_from_psg(text, mode="excerption")
    assert len(strips) == 2                 # three sentences + the remainder
    assert "fifteen sixteen" in strips[0]
    assert "short one" in strips[0]         # glued to the previous sentence
    assert "twentyone" in strips[1]


def test_selection_keeps_the_passage_whole():
    psg = "First sentence here. Second sentence here too."
    assert extract_strips_from_psg(psg, mode="selection") == [psg]


def test_decompose_flattens_every_passage_in_order():
    strips = decompose(["a b c d", "e f g h"], mode="selection")
    assert strips == ["a b c d", "e f g h"]


def test_a_short_strip_scores_minus_one_without_a_forward():
    """Upstream's rule (`len(p.split()) < 4 -> -1.0`) also guards the budget."""
    ev = _Stub()
    _joined, _idxs, ranked = select_relevants(
        ["too short", "long enough passage here"], "q", ev, top_n=2)
    assert ev.forwards == 1                 # the short strip cost nothing
    assert ranked[-1][0] == -1.0            # and sorts last


def test_select_relevants_sorts_keeps_top_n_and_joins_with_semicolons():
    ev = _Stub({"alpha": 0.9, "beta": 0.5, "gamma": 0.1})
    joined, idxs, ranked = select_relevants(
        ["beta passage text here", "gamma passage text here",
         "alpha passage text here"],
        "q", ev, top_n=2)
    assert joined == "alpha passage text here; beta passage text here"
    assert [row[2] for row in ranked] == [2, 0, 1]
    assert idxs == ["2", "0", "1"]


def test_strip_planner_respects_the_budget_and_prefers_strong_passages():
    planner = StripPlanner("selection", max_strips=2)
    strips = planner.strips(["weak passage one", "strong passage two",
                             "other passage three"], [0.1, 0.9, 0.2])
    assert strips == ["strong passage two", "other passage three"]


# ── the evaluator's bucketing (CRAG_Inference.process_flag) ───────────────
def test_process_flag_any_correct_wins_then_any_ambiguous():
    assert process_flag([0.9, 0.1, 0.1], 3, 0.6, 0.4) == [2]
    assert process_flag([0.5, 0.1, 0.1], 3, 0.6, 0.4) == [1]
    assert process_flag([0.1, 0.2, 0.1], 3, 0.6, 0.4) == [0]
    # Two queries in one score list are grouped per n_docs, as upstream does.
    assert process_flag([0.9, 0.1, 0.1, 0.7, 0.1, 0.1], 3,
                        0.6, 0.4) == [2, 2]


def test_classify_uses_the_two_thresholds():
    assert classify([0.61], 0.60, 0.40) == "correct"
    assert classify([0.60, 0.10], 0.60, 0.40) == "correct"
    assert classify([0.55, 0.10], 0.60, 0.40) == "ambiguous"
    assert classify([0.40], 0.60, 0.40) == "ambiguous"
    assert classify([0.39], 0.60, 0.40) == "incorrect"
    assert classify([], 0.60, 0.40) == "incorrect"


def test_the_score_cache_pays_once_per_pair():
    score_cache().clear()
    ev = _Stub({"alpha": 0.9}, cache_enabled=True)
    assert ev.score_many("q", ["alpha passage"]) == [0.9]
    forwards = ev.forwards
    assert ev.score_many("q", ["alpha passage"]) == [0.9]
    assert ev.forwards == forwards           # cache hit: no second forward
    # A different query is a different pair.
    ev.score_many("other q", ["alpha passage"])
    assert ev.forwards == forwards + 1


def test_the_embedding_evaluator_scores_a_batch_in_one_call():
    calls = []

    def _embed(texts):
        calls.append(len(texts))
        return [[1.0, 0.0] if i == 0 else [0.8, 0.6]
                for i in range(len(texts))]

    score_cache().clear()
    ev = EmbeddingEvaluator(_embed, cache_enabled=False)
    scores = ev.score_many("q", ["a", "b", "c"])
    assert calls == [4]                      # query + three passages, one call
    assert all(0.7 < s <= 1.0 for s in scores)


# ── the corrective action (CRAG_Inference.main routing) ───────────────────
def _pipeline(scores, blocks=None, config=None):
    ev = _Stub(scores)
    external = CRAGExternalKnowledge(
        [ChunkStore(retrieve=lambda q: blocks or [])])
    return CRAGPipeline(ev, external=external,
                        rewrite_llm=lambda p, s: "rewritten query",
                        config=config or CRAGConfig(external=True))


def test_a_correct_verdict_ships_the_refined_internal_knowledge():
    score_cache().clear()
    result = _pipeline({"Buenos Aires": 0.9}).run(
        "Location (city)", chunks=["Location: Buenos Aires, Argentina."])
    assert result.action == "correct"
    assert "Buenos Aires" in result.material
    assert result.rewritten == ""            # no corrective work at all


def test_an_ambiguous_verdict_combines_both_knowledge_sources():
    score_cache().clear()
    pipeline = _pipeline(
        {"Buenos Aires": 0.5, "Available April": 0.7},
        blocks=["Available April 2026, able to start within two weeks."])
    result = pipeline.run("Start date",
                          chunks=["Location: Buenos Aires, Argentina."])
    assert result.action == "ambiguous"
    assert "Knowledge1:" in result.material
    assert "Knowledge2:" in result.material
    assert "Available April" in result.material


def test_an_incorrect_verdict_is_replaced_by_external_knowledge_only():
    score_cache().clear()
    pipeline = _pipeline(
        {"Buenos Aires": 0.05, "Available April": 0.8},
        blocks=["Available April 2026, able to start within two weeks."])
    result = pipeline.run("Start date",
                          chunks=["Location: Buenos Aires, Argentina."])
    assert result.action == "incorrect"
    assert result.rewritten == "rewritten query"
    assert "Available April" in result.material
    assert "Buenos Aires" not in result.material


def test_an_incorrect_verdict_with_unsupported_external_text_ships_nothing():
    """The corrective store must clear the LOWER threshold: replacing a wrong
    retrieval with equally wrong text would be worse than asking the user."""
    score_cache().clear()
    pipeline = _pipeline(
        {"Buenos Aires": 0.05},
        blocks=["A press release about an unrelated merger in the sector."])
    result = pipeline.run("Start date",
                          chunks=["Location: Buenos Aires, Argentina."])
    assert result.action == "incorrect"
    assert result.material == ""
    assert result.external == ""


def test_the_material_is_capped_by_the_char_budget():
    score_cache().clear()
    long_chunk = "benitezrodrigo169@gmail.com " + ("x" * 400)
    config = CRAGConfig(external=True, char_budget=40)
    result = _pipeline({"benitezrodrigo169@gmail.com": 0.9},
                       config=config).run("Email address",
                                          chunks=[long_chunk])
    assert len(result.material) == 40


def test_a_stop_request_aborts_before_the_refinement():
    score_cache().clear()
    result = _pipeline({"Buenos Aires": 0.9}).run(
        "Location (city)", chunks=["Location: Buenos Aires, Argentina."],
        stop_flag=lambda: True)
    assert result.aborted is True
    assert result.material == ""


def test_selection_mode_keeps_three_strips_like_upstream():
    cfg = CRAGConfig(decompose_mode="selection", top_n_internal=9)
    assert cfg.internal_top_n() == 3
    cfg = CRAGConfig(decompose_mode="excerption", top_n_internal=6)
    assert cfg.internal_top_n() == 6


# ── the corrective store adapters ─────────────────────────────────────────
def test_pool_streams_filter_by_query_terms_and_rank_by_overlap():
    store = PoolStreams([
        "Started as a data analyst, then moved to engineering.",
        "Python and Django experience across two companies.",
        "Unrelated line about a driver licence category.",
    ])
    blocks = store.fetch("How many years of Python experience")
    assert blocks
    assert "Python" in blocks[0]
    assert all("driver licence" not in b for b in blocks)


def test_chunk_store_uses_the_injected_retrieval_and_caps_it():
    store = ChunkStore(retrieve=lambda q: ["one chunk of text", "two chunk"],
                       max_chunks=1)
    assert store.fetch("q", limit=5) == ["one chunk of text"]


def test_the_external_stage_drops_fragments_that_are_only_a_label():
    stage = CRAGExternalKnowledge([
        PoolStreams(["n/a", "the weather forecast for the region is mild"])])
    assert stage.visit("weather forecast") == [
        "the weather forecast for the region is mild"]


# ── the query rewrite (utils.extract_keywords) ────────────────────────────
def test_the_rewrite_keeps_at_most_three_keywords():
    assert rewrite_query(
        "What is Henry Feilden's occupation?",
        lambda p, s: "Henry Feilden, occupation, London, extra",
    ) == "Henry Feilden, occupation, London"
    # A failed / empty rewrite leaves the caller on the original question.
    assert rewrite_query("q?", lambda p, s: "") == ""
    assert rewrite_query("q?", lambda p, s: (_ for _ in ()).throw(IOError())) == ""


# ── the generation contract ──────────────────────────────────────────────
def test_control_tokens_and_end_markers_never_reach_the_field():
    raw = ("[Retrieval]<paragraph>The answer is 42.</paragraph>"
           "[Utility:5]</s>")
    assert postprocess_answer(raw) == "The answer is 42."


def test_the_evidence_block_names_the_verdict_and_carries_the_text():
    block = evidence_block("correct", "Email: a@b.com", 0.91)
    assert "CRAG verdict: correct" in block
    assert "a@b.com" in block
    assert evidence_block("incorrect", "", 0.1) == ""
