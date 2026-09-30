"""Unit tests for RAG utilities:
- player/multi_sequence/llm_executor_resources/rag_utils.py
- AI/comorag_engine.py (chunking, cosine similarity, enhanced-intent probes)
"""

import logging

import pytest

from player.multi_sequence.llm_executor_resources import rag_utils
from AI import comorag_engine as comorag


# ---------------------------------------------------------------------------
# rag_utils
# ---------------------------------------------------------------------------


def test_chunk_text_smart_returns_chunks():
    text = " ".join(f"word{i}" for i in range(500))
    chunks = rag_utils._chunk_text_smart(text, size=100, overlap=20)
    assert len(chunks) > 1
    assert all(isinstance(c, str) and c for c in chunks)


def test_chunk_text_chars():
    text = "a" * 1000
    chunks = rag_utils._chunk_text_chars(text, size=300, overlap=50)
    assert len(chunks) == 4
    assert all(len(c) <= 300 for c in chunks)


def test_cap_chunks_by_chars():
    chunks = ["a" * 100, "b" * 100, "c" * 100]
    capped = rag_utils._cap_chunks_by_chars(chunks, 250)
    assert sum(len(c) for c in capped) <= 250


def test_dedup_chunks():
    chunks = ["same", "same", "diff", "same"]
    deduped = rag_utils._dedup_chunks(chunks)
    assert deduped == ["same", "diff"]


def test_cosine_sim_identical_vectors():
    assert rag_utils._cosine_sim([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


def test_cosine_sim_orthogonal_vectors():
    assert rag_utils._cosine_sim([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_sim_zero_vector():
    assert rag_utils._cosine_sim([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_is_dense_content():
    assert rag_utils._is_dense_content("the quick brown fox jumps over the lazy dog") is True
    # Structural/formatting-only content (no 2+ char words) is NOT dense
    assert rag_utils._is_dense_content("---===---") is False
    assert rag_utils._is_dense_content("") is False


# ---------------------------------------------------------------------------
# comorag_engine
# ---------------------------------------------------------------------------


def test_comorag_chunk_text():
    text = " ".join(str(i) for i in range(100))
    chunks = comorag._chunk_text(text, size=50, overlap=10)
    assert len(chunks) >= 2
    assert "".join(chunks)  # non-empty


def test_build_chunks_covers_whole_document():
    consolidator = comorag.ProbeBasedConsolidator(
        api_url="http://x", chunk_size=500, overlap=50,
    )
    doc = " ".join(f"sectioncontent{i}" for i in range(1200))  # ~21k chars
    chunks = consolidator._build_chunks([doc], "")
    # The whole document is chunked — no silent truncation to the first N.
    assert len(chunks) >= 20
    assert sum(len(c) for c in chunks) >= 15000


def test_build_chunks_indexes_entire_corpus():
    # ComoRAG protocol: the FULL corpus is always indexed — probes must be
    # free to retrieve evidence from anywhere in the attached documents,
    # never from a capped prefix.
    consolidator = comorag.ProbeBasedConsolidator(
        api_url="http://x", chunk_size=200, overlap=20,
    )
    doc = " ".join(f"w{i}" for i in range(2000))
    chunks = consolidator._build_chunks([doc], "")
    assert len(chunks) >= 10
    assert "w0" in chunks[0]      # document head is reachable
    assert "w1999" in chunks[-1]  # document tail is reachable too


def test_comorag_cosine_sim():
    assert comorag._cosine_sim([1, 0], [1, 0]) == pytest.approx(1.0)
    assert comorag._cosine_sim([1, 0], [0, 1]) == pytest.approx(0.0)
    assert comorag._cosine_sim([], []) == 0.0


def test_extract_probes_falls_back_to_prompt_prefix():
    consolidator = comorag.ProbeBasedConsolidator(
        api_url="http://x", chunk_size=500, overlap=50, top_k=2)
    # No question marks / keywords -> the whole prompt becomes one probe
    probes = consolidator._extract_probes("q1\nq2\nq3")
    assert probes == ["q1\nq2\nq3"]


def test_extract_probes_questions():
    consolidator = comorag.ProbeBasedConsolidator(api_url="http://x")
    probes = consolidator._extract_probes("What is the capital of France? And why?")
    assert len(probes) >= 1
    assert all("?" in p for p in probes)


def test_extract_probes_empty():
    consolidator = comorag.ProbeBasedConsolidator(api_url="http://x")
    assert consolidator._extract_probes("") == []


def test_extract_followup_probe_from_facts():
    consolidator = comorag.ProbeBasedConsolidator(api_url="http://x")
    probe = consolidator._extract_followup_probe(
        "Arrow operates on a freemium model. Individual tier is free."
    )
    assert probe == "Arrow operates on a freemium model."


def test_extract_followup_probe_empty():
    consolidator = comorag.ProbeBasedConsolidator(api_url="http://x")
    assert consolidator._extract_followup_probe("") is None
    assert consolidator._extract_followup_probe("   ") is None


def test_validate_probe_rejects_goal_echo():
    consolidator = comorag.ProbeBasedConsolidator(api_url="http://x")
    goal = "what could arrow do for this person?"
    ctx = "Arrow automates desktop workflows for managers. " \
          "Carmen Gloria Villagra Aguilera is the HR manager."
    # A near-verbatim echo of the goal (no new detail) must be rejected.
    ok, reason = consolidator._validate_probe(
        "What could Arrow do for this person?", goal, ctx,
    )
    assert not ok
    assert "echo" in reason
    # Entity-targeted probes that reuse goal words are legitimate — they
    # carry a new detail grounded in the context.
    ok, reason = consolidator._validate_probe(
        "What could arrow do for Carmen Gloria Villagra Aguilera?",
        goal, ctx,
    )
    assert ok, reason


def test_validate_probe_accepts_goal_derived_probe():
    # Observed regression: a probe that reuses the user's own question
    # wording (50% token overlap) is a LEGITIMATE goal-derived probe — the
    # anti-rephrase rule must not reject it, or every attempt chains the
    # same rejection and consolidation is skipped.
    consolidator = comorag.ProbeBasedConsolidator(api_url="http://x")
    goal = ("Extract the following information from the text: "
            "what is the user doing on screen?")
    ctx = "the user is looking at a screenshot of the desktop"
    ok, reason = consolidator._validate_probe(
        "What is the user currently doing on screen?", goal, ctx,
    )
    assert ok, reason


def test_validate_probe_rejects_invented_terms():
    consolidator = comorag.ProbeBasedConsolidator(api_url="http://x")
    goal = "what could arrow do for this person?"
    ctx = "Arrow automates desktop workflows for managers. Arrow is an " \
          "on-device RPA platform that works 100% offline."
    # "LooperOS" does not exist in the context — must be rejected.
    ok, reason = consolidator._validate_probe(
        "What are the core features of LooperOS?", goal, ctx,
    )
    assert not ok
    assert "invents" in reason


def test_validate_probe_accepts_targeted_grounded_probe():
    consolidator = comorag.ProbeBasedConsolidator(api_url="http://x")
    goal = "what could arrow do for this person?"
    ctx = "Arrow automates desktop workflows for managers. Arrow offers " \
          "capabilities for HR automation and scheduling."
    ok, reason = consolidator._validate_probe(
        "What Arrow capabilities help HR managers with automation?",
        goal, ctx,
    )
    assert ok, reason


def test_validate_probe_accepts_generic_vocabulary():
    # Observed regression: "What is the current time displayed on the
    # system message?" was rejected because "current"/"displayed" do not
    # appear verbatim in terse OCR screen text.  Generic English vocabulary
    # is legitimate probe language — only rare/entity-like terms must be
    # grounded in the context.
    consolidator = comorag.ProbeBasedConsolidator(api_url="http://x")
    goal = "describe what the user is doing on screen"
    terse_ocr_ctx = "TIME 23:25:03  python.exe - main.py  FILE EXPLORER"
    ok, reason = consolidator._validate_probe(
        "What is the current time displayed on the system message?",
        goal, terse_ocr_ctx,
    )
    assert ok, reason


def test_validate_probe_still_rejects_invented_entities():
    # The exemption must NOT let real hallucinations through: invented
    # product names are still rejected even in a terse context.
    consolidator = comorag.ProbeBasedConsolidator(api_url="http://x")
    ok, reason = consolidator._validate_probe(
        "What is the LooperOS release timeline?",
        "describe what the user is doing on screen",
        "TIME 23:25:03  python.exe - main.py",
    )
    assert not ok
    assert "invents" in reason


def test_validate_probe_accepts_linkedin_resume_probe():
    # Observed false rejection (resume + LinkedIn feed run): the probe was
    # completely valid — "alex mason" IS in the context — but rejected
    # 4/4 because ordinary English vocabulary (potential, employer, based,
    # contextual) does not appear verbatim in a raw PDF + UI-chrome feed.
    consolidator = comorag.ProbeBasedConsolidator(api_url="http://x")
    goal = ("basing yourself on the context, give me a list of people in "
            "it that says who would be most likely to hire alex mason")
    raw_pdf_ctx = ("%PDF-1.4 1 0 obj << /Type /Pages /Count 2 >> "
                   "/Title (Alex David Mason Resume) /Author (alex "
                   "mason) endobj CTO @ Acme Corp building an edge AI "
                   "platform Springfield")
    ok, reason = consolidator._validate_probe(
        'Who is mentioned in the document context with the name "alex '
        'mason" and might be his potential employer based on contextual '
        "information?", goal, raw_pdf_ctx,
    )
    assert ok, reason


def test_validate_probe_accepts_truncated_interrogative():
    # A generation truncated by the token budget before the '?' is still a
    # question when it starts with an interrogative (observed: "...based on
    # co" cut mid-word at 64 tokens).
    consolidator = comorag.ProbeBasedConsolidator(api_url="http://x")
    goal = "who would hire alex mason"
    ctx = "Alex Mason CTO at Acme Corp in Springfield"
    ok, reason = consolidator._validate_probe(
        "Who is mentioned in the document context with the name \"alex "
        "mason\" and might be his potential employer based on co",
        goal, ctx,
    )
    assert ok, reason
    # A non-interrogative fragment is still rejected.
    ok, reason = consolidator._validate_probe(
        "Target entity or section:", goal, ctx,
    )
    assert not ok
    assert "question" in reason


def test_parse_judge_verdict():
    assert comorag._parse_judge_verdict("ANSWER: Carmen is the recruiter") \
        == ("answer", "Carmen is the recruiter")
    assert comorag._parse_judge_verdict(
        "MISSING: the company that posted the job offer",
    ) == ("missing", "the company that posted the job offer")
    # Payload is capped so a runaway gap cannot flood the next probe prompt.
    _kind, _payload = comorag._parse_judge_verdict("MISSING: " + "x" * 500)
    assert _kind == "missing" and len(_payload) <= 220
    # Anything else (or empty) keeps probing unchanged.
    assert comorag._parse_judge_verdict("") == (None, "")
    assert comorag._parse_judge_verdict("not sure yet") == (None, "")


def test_normalize_probe_extracts_json_wrapper():
    # A small model sometimes wraps the probe in the intent-plan's
    # structured format or in markdown fences — the inner question is the
    # probe, never the JSON blob (its field names would be flagged as
    # invented terms and pollute the embedding).
    full = ('{"user_query": "What is the current time displayed on the '
            'system message?", "expected_answer": "The time shown"}')
    assert comorag._normalize_probe(full) == \
        "What is the current time displayed on the system message?"
    # Truncated/unbalanced JSON still yields the query head (observed:
    # the model's raw completion cut mid-expected_answer).
    truncated = ('{"user_query": "What is the current time displayed on the '
                 'system message?", "expected_answer": "The user appears')
    assert comorag._normalize_probe(truncated) == \
        "What is the current time displayed on the system message?"
    # Markdown code fences are stripped.
    fenced = "```json\n" + full + "\n```"
    assert comorag._normalize_probe(fenced) == \
        "What is the current time displayed on the system message?"
    # Plain probes pass through untouched.
    assert comorag._normalize_probe("What time is it?") == "What time is it?"
    # A wrapper without a query key is left as-is for the validator to
    # reject (missing '?') — no silently empty probe.
    assert comorag._normalize_probe('{"expected_answer": "x"}') == \
        '{"expected_answer": "x"}'


def test_validate_probe_rejects_empty():
    consolidator = comorag.ProbeBasedConsolidator(api_url="http://x")
    ok, _ = consolidator._validate_probe("", "goal", "ctx")
    assert not ok
    ok, _ = consolidator._validate_probe("   ", "goal", "ctx")
    assert not ok


def test_validate_probe_returns_reason():
    consolidator = comorag.ProbeBasedConsolidator(api_url="http://x")
    # A grounded, on-topic probe returns (True, "").
    ok, reason = consolidator._validate_probe(
        "Which automation capabilities does Arrow have for HR scheduling?",
        "what could arrow do for this person?",
        "Arrow automates desktop workflows for managers. Arrow offers "
        "automation capabilities for HR scheduling.",
    )
    assert ok and reason == ""
    # An invented term returns the missing terms as the reason.
    ok, reason = consolidator._validate_probe(
        "What is LooperOS?", "what could arrow do for this person?",
        "Arrow automates desktop workflows for managers.",
    )
    assert not ok
    assert "looperos" in reason


def test_validate_probe_rejects_prompt_fragment():
    consolidator = comorag.ProbeBasedConsolidator(api_url="http://x")
    # A tiny LLM echoes prompt headers instead of writing a question.
    ok, _ = consolidator._validate_probe(
        "Target entity or section:", "what could arrow do for this person?",
        "Arrow automates desktop workflows for managers.",
    )
    assert not ok
    ok, _ = consolidator._validate_probe(
        "Avoid rephrasing goal:", "what could arrow do for this person?",
        "Arrow automates desktop workflows for managers.",
    )
    assert not ok


def test_consolidate_keeps_probing_with_fact_grounded_followups(monkeypatch):
    consolidator = comorag.ProbeBasedConsolidator(
        api_url="http://x", chunk_size=500, overlap=50, top_k=1,
        max_probes=3, char_budget=500,
    )
    calls = {"n": 0}
    probes_used = []

    def fake_probe(intent, retrieved):
        # Degenerate LLM probe generator: echoes the same grounded probe.
        calls["n"] += 1
        return "TOKEN1 TOKEN1?"

    monkeypatch.setattr(
        consolidator, "_compute_embeddings",
        lambda chunks: [[1.0, 0.0]] * len(chunks),
    )

    def _retrieve(probe, chunks, embs, exclude_indices=None):
        probes_used.append(probe)
        # Serve a fresh chunk each call so all cycles retrieve evidence.
        for i, ch in enumerate(chunks):
            if exclude_indices is None or i not in exclude_indices:
                return [ch]
        return []

    monkeypatch.setattr(consolidator, "_retrieve_for_probe", _retrieve)
    # Distinct TOKENi sections -> every chunk starts differently, so the
    # fact-grounded follow-up probes differ across cycles.
    sources = {
        "context_nodes": [" ".join(f"TOKEN{i} " * 20 for i in range(18))]
    }
    result = consolidator.consolidate(
        "What is X?", sources, probe_generator=fake_probe,
    )
    # The LLM probe is accepted once, then repeats: the repeat triggers
    # critique-feedback retries, then fact-grounded follow-ups keep ALL
    # cycles alive and consume the corpus instead of collapsing the budget.
    assert calls["n"] >= 2
    assert result  # evidence-tagged entries (no responder) still return text
    # Follow-ups are fact-derived (TOKEN-prefixed heads may start mid-token
    # when a chunk boundary cuts a token — every head still quotes the
    # TOKEN corpus), never the LLM echo.
    non_anchor = [p for p in probes_used if p != "What is X?"]
    assert non_anchor[0] == "TOKEN1 TOKEN1?"
    assert len(non_anchor) >= 3
    assert all("TOKEN" in p for p in non_anchor[1:])


def test_retrieve_for_text_with_mocked_retrieval(monkeypatch):
    consolidator = comorag.ProbeBasedConsolidator(api_url="http://x")
    consolidator._cached_chunks = ["chunk one", "chunk two"]
    consolidator._cached_embeddings = [[1.0], [1.0]]
    consolidator._retrieved_indices = set()

    monkeypatch.setattr(consolidator, "_retrieve_for_probe",
                        lambda probe, chunks, embs, exclude_indices=None: ["chunk one"])
    result = consolidator.retrieve_for_text("some query", top_k=1)
    assert "chunk one" in result
    assert consolidator._retrieved_indices == {0}


def test_retrieve_for_text_nothing_cached():
    consolidator = comorag.ProbeBasedConsolidator(api_url="http://x")
    assert consolidator.retrieve_for_text("query") == ""


def test_retrieve_for_text_empty_text():
    consolidator = comorag.ProbeBasedConsolidator(api_url="http://x")
    consolidator._cached_chunks = ["c"]
    consolidator._cached_embeddings = [[1.0]]
    assert consolidator.retrieve_for_text("") == ""


def test_reset_retrieved():
    consolidator = comorag.ProbeBasedConsolidator(api_url="http://x")
    consolidator._cached_chunks = ["c"]
    consolidator._cached_embeddings = [[1.0]]
    consolidator._retrieved_indices = {0}
    consolidator.reset_retrieved()
    assert consolidator._retrieved_indices == set()
    assert consolidator._cached_chunks == []


def test_consolidate_uses_probe_generator_and_budget(monkeypatch):
    consolidator = comorag.ProbeBasedConsolidator(
        api_url="http://x", chunk_size=500, overlap=50, top_k=1,
        max_probes=3, char_budget=500,
    )
    calls = {"n": 0}

    def fake_probe(prompt, retrieved):
        calls["n"] += 1
        return f"probe {calls['n']}"

    monkeypatch.setattr(
        consolidator, "_compute_embeddings",
        lambda chunks: [[1.0, 0.0]] * len(chunks),
    )

    def _retrieve(probe, chunks, embs, exclude_indices=None):
        for i, ch in enumerate(chunks):
            if exclude_indices is None or i not in exclude_indices:
                return [ch]
        return []

    monkeypatch.setattr(consolidator, "_retrieve_for_probe", _retrieve)
    sources = {"context_nodes": ["alpha beta gamma delta epsilon " * 20]}
    result = consolidator.consolidate(
        "What is X?", sources, probe_generator=fake_probe,
    )
    # Invalid probes (no '?') are critique-retried, then the deterministic
    # ladder takes over; the loop terminates when the pool is exhausted —
    # the budget (max_probes) is never silently skipped.
    assert calls["n"] >= 1
    assert "alpha beta gamma" in result


def _grounded_reply(finding, evidence):
    """A composer reply in the shape both real responders produce.

    The engine requires a finding's ``Support`` quote to actually occur in
    the probe's evidence (``comorag._is_grounded_finding``), so a fake
    composer that omits the quote would be correctly rejected as fabricated.
    """
    return "Key Finding: %s\nSupport: %s" % (
        finding, str(evidence).strip()[:60])


def test_consolidate_judge_gates_and_gap_seeds_probe(monkeypatch):
    # ComoRAG meta-loop gate: after every probe's facts the judge decides
    # "MISSING" (gap seeds the next probe prompt) and later "ANSWER"
    # (probe loop stops early; the draft rides into the synthesis as a
    # labeled candidate for evidence verification).
    consolidator = comorag.ProbeBasedConsolidator(
        api_url="http://x", chunk_size=100, overlap=20, top_k=1,
    )
    probes_ctx = []
    judge_calls = {"n": 0}
    probe_calls = {"n": 0}
    synth_args = {}

    def fake_probe(prompt, retrieved):
        probes_ctx.append(str(prompt))
        probe_calls["n"] += 1
        return f"probe {probe_calls['n']}?"

    def fake_responder(probe, evidence):
        return _grounded_reply(
            f"fact {probe_calls['n']} about alpha.", evidence)

    def fake_judge(goal, facts_text, evidence_text):
        judge_calls["n"] += 1
        if judge_calls["n"] == 1:
            return "MISSING: the beta company"
        return "ANSWER: gamma is the answer"

    def fake_synth(intent, facts, evidence):
        synth_args["facts"] = facts
        return "SYNTHESIZED"

    monkeypatch.setattr(
        consolidator, "_compute_embeddings",
        lambda chunks: [[1.0, 0.0]] * len(chunks),
    )

    def _retrieve(probe, chunks, embs, exclude_indices=None):
        for i, ch in enumerate(chunks):
            if exclude_indices is None or i not in exclude_indices:
                return [ch]
        return []

    monkeypatch.setattr(consolidator, "_retrieve_for_probe", _retrieve)
    sources = {"context_nodes": ["alpha beta gamma delta epsilon " * 40]}
    result = consolidator.consolidate(
        "What is X?", sources,
        probe_generator=fake_probe,
        responder=fake_responder,
        response_generator=fake_synth,
        judge=fake_judge,
    )
    assert result == "SYNTHESIZED"
    # Judge 1 said MISSING -> its gap seeded the SECOND probe prompt.
    assert judge_calls["n"] == 2
    assert probe_calls["n"] == 2          # ANSWER stopped the loop early
    assert "GAP TO CLOSE" not in probes_ctx[0]
    assert "GAP TO CLOSE" in probes_ctx[1]
    assert "beta company" in probes_ctx[1]
    # The ANSWER draft reached the synthesis as a labeled candidate.
    assert "JUDGE CANDIDATE ANSWER" in synth_args["facts"]
    assert "gamma is the answer" in synth_args["facts"]


def test_consolidate_stops_on_empty_probe(monkeypatch):
    consolidator = comorag.ProbeBasedConsolidator(api_url="http://x")
    monkeypatch.setattr(
        consolidator, "_compute_embeddings",
        lambda chunks: [[1.0]] * len(chunks),
    )
    monkeypatch.setattr(
        consolidator, "_retrieve_for_probe",
        lambda probe, chunks, embs, exclude_indices=None: [chunks[0]],
    )
    first = {"done": False}

    def fake_probe(prompt, retrieved):
        if first["done"]:
            return None
        first["done"] = True
        return "first probe"

    sources = {"context_nodes": ["some context content here"]}
    result = consolidator.consolidate("q?", sources, probe_generator=fake_probe)
    assert result  # first probe retrieved something, then loop stopped


def test_consolidate_raises_on_embedding_failure(monkeypatch):
    consolidator = comorag.ProbeBasedConsolidator(api_url="http://x")
    # Force embeddings unavailable (and block any embedder from touching
    # localhost) — consolidation must HARD BREAK with EmbeddingFailureError,
    # never silently return keyword-ranked raw text.
    monkeypatch.setattr(consolidator, "_llamacpp_embed", lambda texts: None)
    monkeypatch.setattr(comorag, "_local_embed", lambda texts: None)
    monkeypatch.setenv("ARROW_DIRECT_ENGINE", "1")

    sources = {"context_nodes": ["alpha beta gamma delta"]}
    with pytest.raises(comorag.EmbeddingFailureError) as exc_info:
        consolidator.consolidate("What about alpha?", sources)
    assert "embedding" in str(exc_info.value).lower()


def test_consolidate_embedding_failure_captures_reason(monkeypatch):
    consolidator = comorag.ProbeBasedConsolidator(api_url="http://x")
    # Block the shared-server retry path so the reason stays deterministic.
    monkeypatch.setattr(comorag, "_local_embed", lambda texts: None)

    class _BoomClient:
        def embed(self, texts):
            raise ConnectionError("embedding server unreachable")

    consolidator._embed_client = _BoomClient()
    sources = {"context_nodes": ["alpha beta gamma delta"]}
    with pytest.raises(comorag.EmbeddingFailureError) as exc_info:
        consolidator.consolidate("What about alpha?", sources)
    # The underlying cause is carried into the hard-break message.
    assert "embedding server unreachable" in str(exc_info.value)


def test_build_rag_context_from_docs_raises_on_embedding_failure(tmp_path, monkeypatch):
    doc = tmp_path / "doc.txt"
    doc.write_text("alpha beta gamma delta epsilon " * 40, encoding="utf-8")
    # Block the local embedding server retry path for determinism.
    monkeypatch.setattr(rag_utils, "_local_embed", lambda texts: None)

    class _DeadClient:
        def embeddings(self, model, inputs):
            return None

    with pytest.raises(comorag.EmbeddingFailureError):
        rag_utils.build_rag_context_from_docs(
            _DeadClient(), "query about alpha", [str(doc)],
            "nomic-embed-text", chunk_size=200, overlap=20, top_k=2,
            max_chars=1500,
        )


def test_build_graphrag_context_raises_on_embedding_failure(tmp_path):
    doc = tmp_path / "doc.txt"
    doc.write_text("alpha beta gamma delta epsilon " * 40, encoding="utf-8")
    with pytest.raises(comorag.EmbeddingFailureError):
        rag_utils.build_graphrag_context(
            [str(doc)], "query about alpha",
            chunk_size=100, overlap=10, max_chunks=3,
            embed_fn=lambda texts: None,
        )


def test_build_rag_context_raises_on_query_embedding_failure(monkeypatch):
    import tempfile, os as _os
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("alpha beta gamma delta " * 50)
        path = f.name
    try:
        class _Client:
            def embeddings(self, model, inputs):
                # Chunks succeed, query fails
                return [[1.0, 0.0]] * len(inputs) if len(inputs) > 1 else None

        with pytest.raises(comorag.EmbeddingFailureError):
            rag_utils.build_rag_context(
                _Client(), "query", "alpha beta gamma delta " * 50,
                "nomic-embed-text", chunk_size=200, overlap=20, top_k=2,
                include_raw_input=False, max_chars=1500,
                input_source="previous", use_input_text_in_prompt=True,
            )
    finally:
        _os.unlink(path)


def test_consolidate_char_budget_caps_retrieval_steps(monkeypatch):
    consolidator = comorag.ProbeBasedConsolidator(
        api_url="http://x", max_probes=5, char_budget=200, top_k=1,
    )
    big_chunk = "word " * 300  # single chunk > budget
    monkeypatch.setattr(
        consolidator, "_compute_embeddings",
        lambda chunks: [[1.0]] * len(chunks),
    )
    monkeypatch.setattr(
        consolidator, "_retrieve_for_probe",
        lambda probe, chunks, embs, exclude_indices=None: (
            [chunks[0]] if not (exclude_indices and 0 in exclude_indices) else []
        ),
    )
    sources = {"context_nodes": [big_chunk]}
    result = consolidator.consolidate("q", sources)
    # The single chunk is exhausted after the first cycle (exclusion-aware
    # retrieval), so the fact-grounded follow-ups keep the loop alive but
    # add no evidence: a single chunk, no separators.
    assert result
    assert "---" not in result


def test_negative_finding_is_not_a_fact(monkeypatch):
    # A composition that only reports the value is ABSENT carries no
    # knowledge: it must not count as a scored fact (which closed the cycle)
    # and must not reach the consumer as a distilled fact telling the model
    # the field is unanswerable.
    consolidator = comorag.ProbeBasedConsolidator(
        api_url="http://x", chunk_size=500, overlap=50, top_k=1,
        max_probes=1, min_facts=1,
    )
    monkeypatch.setattr(
        consolidator, "_compute_embeddings",
        lambda chunks: [[1.0, 0.0]] * len(chunks),
    )
    monkeypatch.setattr(
        consolidator, "_retrieve_for_probe",
        lambda probe, chunks, embs, exclude_indices=None: [chunks[0]],
    )

    def fake_responder(probe, evidence):
        return ("Key Finding: The LinkedIn Profile URL is not provided in "
                "the Evidence.\nSupport: Email or phone.")

    sources = {"context_nodes": [
        "a resume body long enough to survive the minimum chunk length"]}
    consolidator.consolidate(
        "LinkedIn URL?", sources, responder=fake_responder,
    )
    assert consolidator.facts_pool == []       # no bogus fact
    assert consolidator.evidence_pool          # the source is still shipped


def test_is_negative_finding_classifier():
    assert comorag._is_negative_finding(
        "Key Finding: The address is not provided in the evidence.")
    assert comorag._is_negative_finding(
        "Key Finding: the document does not contain the value.")
    # The verbatim Support quote routinely contains "no"/"not" - it is source
    # text, not a claim about the field.
    assert not comorag._is_negative_finding(
        "Key Finding: Python is the core language.\n"
        "Support: no other language is mentioned in the summary.")
    assert not comorag._is_negative_finding(
        "Key Finding: Paris is the capital of France.")


def test_is_negative_finding_classifier_tolerates_markdown_label():
    # Observed in a live run: the composer bolded the label, so the old
    # startswith("key finding") test missed the line and the absence was
    # pooled as a scored fact.
    assert comorag._is_negative_finding(
        "**Key Finding:** No specific city location can be determined from "
        "the provided evidence.")
    assert comorag._is_negative_finding(
        "## Key Finding: the document does not contain the value.")
    assert not comorag._is_negative_finding("## Key Findings")


def test_split_findings_separates_a_multi_finding_answer():
    answer = (
        "Key Finding: first claim.\n"
        "Support: alpha beta gamma delta epsilon zeta eta theta.\n"
        "**Key Finding:** second claim.\n"
        "Support: kappa lambda mu nu xi omicron pi rho.\n"
    )
    parts = comorag._split_findings(answer)
    assert len(parts) == 2
    assert parts[0].startswith("Key Finding: first claim.")
    assert "Support: kappa" in parts[1]
    # An answer without a finding label is one opaque block (its grounding is
    # then decided by the missing Support quote).
    assert comorag._split_findings("**Location (city):** Nowhere") == [
        "**Location (city):** Nowhere"]


def test_is_grounded_finding_rejects_fabricated_claims():
    # The exact evidence shape from the live run that produced a wrong answer:
    # the structured key/value block held 'city = riverside', yet the pool carried
    # 'San Francisco, CA' and the '[City Name]' template leftovers.
    evidence = (
        "location:\ncountry = canada\nstate = springfield\n"
        "city = riverside\nI am based in Springfield (UTC-5), work remotely."
    )
    # A Support quote that really is in the evidence -> grounded.
    assert comorag._is_grounded_finding(
        "Key Finding: the candidate works remotely.\n"
        "Support: I am based in Springfield (UTC-5), work remotely.",
        evidence,
    )
    # Punctuation/whitespace drift on a long verbatim run -> still grounded.
    assert comorag._is_grounded_finding(
        "Key Finding: based in Springfield.\n"
        "Support: I am based in Springfield (UTC-5) work remotely",
        evidence,
    )
    # No Support line at all -> fabricated ("San Francisco, CA", observed).
    assert not comorag._is_grounded_finding(
        "**Location (city):** San Francisco, CA", evidence)
    # A Support quote absent from the evidence -> fabricated (the "[City
    # Name]" template leftovers, observed).
    assert not comorag._is_grounded_finding(
        "Key Finding: [City Name]\n"
        "Support: The candidate's location is listed as \"[City Name]\" in "
        "their profile information.",
        evidence,
    )
    # A paraphrase passed off as a quote is not traceable either.
    assert not comorag._is_grounded_finding(
        "Key Finding: the candidate is based in San Francisco.\n"
        "Support: Alex is a San Francisco based engineer.",
        evidence,
    )


def test_is_grounded_finding_accepts_a_faithful_short_paraphrase():
    """A 2B composer paraphrases instead of quoting, and a quote shorter than
    9 words never produces a verbatim window — so requiring a literal quote
    rejected correct findings and reported them as "ungrounded" (observed:
    probe 'Do you currently live in Canada?' with ZERO violations in the
    claim).  A paraphrase that invents no entity/value is still traceable."""
    evidence = (
        "location:\ncountry = canada\nstate = springfield\n"
        "city = riverside\nI am based in Springfield (UTC-5), work remotely."
    )
    assert comorag._is_grounded_finding(
        "Key Finding: Yes, the candidate currently lives in Canada.\n"
        "Support: The candidate lives in Canada.",
        evidence,
    )
    # ...but an entity the evidence never names, inside the quote, still fails.
    assert not comorag._is_grounded_finding(
        "Key Finding: Yes, the candidate currently lives in Canada.\n"
        "Support: The candidate lives in Metro City, Canada.",
        evidence,
    )


def test_ungrounded_finding_is_not_a_fact(monkeypatch):
    # End-to-end through consolidate(): a probe whose only composed finding is
    # fabricated must leave the fact pool EMPTY and keep the retrieved chunk as
    # the tagged (non-counted) fallback, so the clue is not dropped with the
    # fabrication.
    consolidator = comorag.ProbeBasedConsolidator(
        api_url="http://x", chunk_size=500, overlap=50, top_k=1,
        max_probes=1, min_facts=1,
    )
    monkeypatch.setattr(
        consolidator, "_compute_embeddings",
        lambda chunks: [[1.0, 0.0]] * len(chunks),
    )
    monkeypatch.setattr(
        consolidator, "_retrieve_for_probe",
        lambda probe, chunks, embs, exclude_indices=None: [chunks[0]],
    )

    def fake_responder(probe, evidence):
        return "**Location (city):** San Francisco, CA"

    sources = {"context_nodes": [
        "location: city = riverside and a body long enough to chunk"]}
    consolidator.consolidate(
        "Where is the candidate located?", sources, responder=fake_responder,
    )
    assert consolidator.facts_pool == []          # no fabricated fact
    assert consolidator.evidence_pool             # the clue is still shipped


# ---------------------------------------------------------------------------
# Grounding contract: retrieval fails closed, claims are verified against the
# verbatim evidence, and the source's own keyed lines are harvested.
# ---------------------------------------------------------------------------


def test_retrieval_has_no_document_order_fallback():
    # A probe with no lexical bridge retrieves NOTHING: document position is
    # never evidence.  (It used to return "the first N remaining chunks" so
    # probing would "explore" — which is how a composer ended up grounding
    # claims on chunks with no relation to the probe.)
    consolidator = comorag.ProbeBasedConsolidator(api_url="http://x")
    chunks = ["alpha beta gamma", "delta epsilon zeta"]
    assert consolidator._lexical_candidates("zzzz qqqq", chunks, None, 5) == []
    assert consolidator._lexical_candidates("alpha", chunks, None, 5) == [0]


def test_grounding_violations_flags_terms_absent_from_evidence():
    evidence = (
        "location:\ncountry = canada\nstate = springfield\n"
        "city = riverside\nI am based in Springfield (UTC-5), work remotely.\n"
        "Desired salary: USD 4,000 per month. "
        "Contact alex@example.com"
    )
    # Grounded: every asserted term occurs in the evidence.
    assert comorag.grounding_violations(
        "The candidate lives in Springfield and works remotely.",
        evidence) == []
    # A number written with different separators is still the same value.
    assert comorag.grounding_violations("Desired salary is USD 4000.",
                                        evidence) == []
    # The observed fabrication: a place the source never names.
    assert "Francisco" in comorag.grounding_violations(
        "Location (city): San Francisco, CA", evidence)
    # Invented exact values are never grounded.
    assert comorag.grounding_violations("Reach him at fake@example.com",
                                        evidence)
    assert comorag.grounding_violations("Salary 9999 USD", evidence)
    # Vocabulary the PIPELINE introduced is permitted...
    assert comorag.grounding_violations(
        "Report the Location (city) value.", evidence,
        allow="Location (city)",
    ) == []
    # ...but it does not license an invented VALUE.
    assert comorag.grounding_violations(
        "Report the Location (city) San Francisco value.", evidence,
        allow="Location (city)",
    )


def test_grounding_window_keeps_both_ends():
    # The head of the evidence holds the contact/address block; a tail-only
    # slice silently removed it before the composer ever saw it.
    win = comorag.grounding_window("HEAD" + ("x" * 500) + "TAIL", 100)
    assert win.startswith("HEAD") and win.endswith("TAIL")
    assert len(win) <= 108
    assert "city = riverside" in comorag.grounding_window(
        "city = riverside" + ("x" * 500) + "end", 200)
    assert comorag.grounding_window("abc", 100) == "abc"


def test_parse_judge_verdict_tolerates_markdown():
    assert comorag._parse_judge_verdict("**ANSWER:** 42") == ("answer", "42")
    assert comorag._parse_judge_verdict("## MISSING: the city")[0] == "missing"
    # An unreadable verdict is NOT an answer (fail-closed).
    assert comorag._parse_judge_verdict("I think we are done") == (None, "")
    assert comorag._parse_judge_verdict("") == (None, "")


def test_consolidate_refuses_a_fabricated_finding(monkeypatch):
    # The composer fabricates a value absent from the evidence; it must NOT
    # enter the fact pool.  (Source keyed lines are no longer harvested into
    # the pool — the verbatim chunks stay in the evidence pool instead.)
    consolidator = comorag.ProbeBasedConsolidator(
        api_url="http://x", chunk_size=500, overlap=50, top_k=2,
        max_probes=1, min_facts=1,
    )
    monkeypatch.setattr(
        consolidator, "_compute_embeddings",
        lambda chunks: [[1.0, 0.0]] * len(chunks),
    )
    monkeypatch.setattr(
        consolidator, "_retrieve_for_probe",
        lambda probe, chunks, embs, exclude_indices=None: [chunks[0]],
    )

    def fake_responder(probe, evidence):
        return "Key Finding: Location (city): San Francisco, CA"

    sources = {"context_nodes": [
        "location:\ncountry = canada\ncity = riverside\n"
        "I am based in Springfield (UTC-5), work remotely."]}
    consolidator.consolidate(
        "Retrieve the city location for the form field", sources,
        probe_generator=lambda intent, facts: "Where is the city location?",
        responder=fake_responder,
    )
    pool = "\n".join(consolidator.facts_pool)
    assert "San Francisco" not in pool    # the fabrication is refused
    assert pool == ""                     # nothing composed was pooled


def test_judge_draft_must_be_grounded(monkeypatch):
    # An ANSWER draft asserting a value the evidence lacks cannot become the
    # synthesis candidate — it would arrive pre-labelled as "verify this".
    consolidator = comorag.ProbeBasedConsolidator(
        api_url="http://x", chunk_size=500, overlap=50, top_k=1,
        max_probes=2, min_facts=1,
    )
    monkeypatch.setattr(
        consolidator, "_compute_embeddings",
        lambda chunks: [[1.0, 0.0]] * len(chunks),
    )
    monkeypatch.setattr(
        consolidator, "_retrieve_for_probe",
        lambda probe, chunks, embs, exclude_indices=None: [chunks[0]],
    )
    seen = {}

    def fake_responder(probe, evidence):
        return ("Key Finding: the candidate works remotely.\n"
                "Support: %s" % str(evidence).strip()[:60])

    def fake_judge(goal, facts, evidence):
        return "**ANSWER:** San Francisco, CA"

    def fake_synth(intent, facts, evidence):
        seen["facts"] = facts
        return None

    sources = {"context_nodes": [
        "I am based in Springfield (UTC-5), work remotely."]}
    consolidator.consolidate(
        "Where is the candidate based?", sources,
        probe_generator=lambda intent, facts: "Where does the candidate work?",
        responder=fake_responder,
        judge=fake_judge,
        response_generator=fake_synth,
    )
    assert "JUDGE CANDIDATE ANSWER" not in seen.get("facts", "")


def test_ungrounded_synthesis_is_discarded(monkeypatch):
    # The synthesis is what the CONSUMER reads: one asserting a term the
    # verbatim evidence does not contain is not returned at all.
    consolidator = comorag.ProbeBasedConsolidator(
        api_url="http://x", chunk_size=500, overlap=50, top_k=1,
        max_probes=1, min_facts=1,
    )
    monkeypatch.setattr(
        consolidator, "_compute_embeddings",
        lambda chunks: [[1.0, 0.0]] * len(chunks),
    )
    monkeypatch.setattr(
        consolidator, "_retrieve_for_probe",
        lambda probe, chunks, embs, exclude_indices=None: [chunks[0]],
    )

    def fake_responder(probe, evidence):
        return ("Key Finding: the candidate works remotely.\n"
                "Support: %s" % str(evidence).strip()[:60])

    sources = {"context_nodes": [
        "I am based in Springfield (UTC-5), work remotely."]}
    out = consolidator.consolidate(
        "Where is the candidate based?", sources,
        probe_generator=lambda intent, facts: "Where does the candidate work?",
        responder=fake_responder,
        response_generator=lambda intent, facts, evidence: (
            "The candidate is based in San Francisco."),
    )
    assert out                                  # grounded material survives
    assert "San Francisco" not in out           # the invention does not
    assert "Springfield" in out


def test_request_anchor_evidence_reaches_the_composer(monkeypatch):
    # The request anchor exists so facts stay grounded in the user's own
    # request when a probe misfires — so it must reach the composer, not just
    # the evidence pool.
    consolidator = comorag.ProbeBasedConsolidator(
        api_url="http://x", chunk_size=500, overlap=50, top_k=1,
        max_probes=1, min_facts=1,
    )
    monkeypatch.setattr(
        consolidator, "_compute_embeddings",
        lambda chunks: [[1.0, 0.0]] * len(chunks),
    )
    # Only the request anchor ("zzz goal") retrieves; the probe finds nothing.
    monkeypatch.setattr(
        consolidator, "_retrieve_for_probe",
        lambda probe, chunks, embs, exclude_indices=None: (
            [chunks[0]] if "goal" in str(probe) else []),
    )
    seen = {}
    consolidator.consolidate(
        "zzz goal",
        {"context_nodes": ["anchor evidence about zzz" * 3]},
        probe_generator=lambda intent, facts: "zzz probe?",
        responder=lambda probe, evidence: seen.setdefault("probe", probe) and None,
    )
    assert seen.get("probe"), "the anchor's evidence never reached the composer"


def test_build_context_excerpt_head_and_tail():
    consolidator = comorag.ProbeBasedConsolidator(api_url="http://x")
    text = "HEAD_MARKER" + ("x" * 2000) + "_TAIL_MARKER"
    excerpt = consolidator._build_context_excerpt(
        [text], per_source=500, tail_chars=60, max_chars=100000,
    )
    assert excerpt.startswith("[source 1]\nHEAD_MARKER")
    assert "_TAIL_MARKER" in excerpt


def test_build_context_excerpt_bounded():
    consolidator = comorag.ProbeBasedConsolidator(api_url="http://x")
    excerpt = consolidator._build_context_excerpt(
        ["A" * 5000, "B" * 5000], max_chars=900, per_source=400,
    )
    # Stops pulling once the cap is reached; never exceeds cap + one source
    assert len(excerpt) <= 900 + 410
    assert "[source 1]" in excerpt


def test_build_context_excerpt_empty():
    consolidator = comorag.ProbeBasedConsolidator(api_url="http://x")
    assert consolidator._build_context_excerpt([]) == ""
    assert consolidator._build_context_excerpt(["   "]) == ""


def test_build_enhanced_intent_composes_goal_system_context():
    consolidator = comorag.ProbeBasedConsolidator(api_url="http://x")
    intent = consolidator._build_enhanced_intent(
        "user goal?", "system role", "context excerpt text",
    )
    assert "SYSTEM:\nsystem role" in intent
    assert "GOAL (user request):\nuser goal?" in intent
    assert "CONTEXT (available sources):\ncontext excerpt text" in intent


def test_build_enhanced_intent_skips_missing_parts():
    consolidator = comorag.ProbeBasedConsolidator(api_url="http://x")
    intent = consolidator._build_enhanced_intent("only goal", "", "")
    assert intent == "GOAL (user request):\nonly goal"


def test_consolidate_grounds_probes_on_available_context(monkeypatch):
    consolidator = comorag.ProbeBasedConsolidator(
        api_url="http://x", chunk_size=500, overlap=50, top_k=1,
        max_probes=1,
    )
    captured = {}

    def fake_probe(intent, retrieved):
        captured["intent"] = intent
        return "gloria chacon hr manager"

    monkeypatch.setattr(
        consolidator, "_compute_embeddings",
        lambda chunks: [[1.0, 0.0]] * len(chunks),
    )
    monkeypatch.setattr(
        consolidator, "_retrieve_for_probe",
        lambda probe, chunks, embs, exclude_indices=None: [chunks[0]],
    )
    sources = {
        "context_nodes": [
            "GLORIA PAULINA CHACÓN GARCÍA chacon.gloria@principal.com "
            "Gerente de RR.HH."
        ]
    }
    result = consolidator.consolidate(
        "what can arrow do for this person?", sources,
        system_prompt="You are an HR assistant.",
        probe_generator=fake_probe,
    )
    # The probe generator sees the goal AND the context — not the bare prompt.
    assert "what can arrow do for this person?" in captured["intent"]
    assert "GLORIA PAULINA CHACÓN" in captured["intent"]
    assert "You are an HR assistant." in captured["intent"]
    assert result  # evidence retrieved and returned


def test_consolidate_retries_probe_with_feedback_on_rejection(monkeypatch):
    # A rejected probe (invented term) must trigger a critique-retry with
    # the rejection reason embedded in the generator context — never a
    # silent fallback to the deterministic ladder.
    consolidator = comorag.ProbeBasedConsolidator(
        api_url="http://x", chunk_size=500, overlap=50, top_k=1,
        max_probes=2, min_facts=1, probe_retry_attempts=2,
    )
    calls = {"n": 0}
    seen_ctx = []

    def fake_probe(intent, retrieved):
        calls["n"] += 1
        seen_ctx.append(str(intent))
        if calls["n"] == 1:
            return "Based on the text snippet, what does the mention of LooperOS suggest?"
        return "What does the text say about desktop workflows?"

    monkeypatch.setattr(
        consolidator, "_compute_embeddings",
        lambda chunks: [[1.0, 0.0]] * len(chunks),
    )
    monkeypatch.setattr(
        consolidator, "_retrieve_for_probe",
        lambda probe, chunks, embs, exclude_indices=None: [chunks[0]],
    )

    def fake_responder(probe, evidence):
        return "Key Finding: Arrow automates desktop workflows."

    sources = {"context_nodes": ["Arrow automates desktop workflows for managers."]}
    result = consolidator.consolidate(
        "what can arrow do for this person?", sources,
        probe_generator=fake_probe, responder=fake_responder,
    )
    # The 2nd generator call carries the rejection feedback + reason.
    assert calls["n"] >= 2
    assert "PREVIOUS PROBE ATTEMPTS AND REJECTIONS" in seen_ctx[1]
    assert "looperos" in seen_ctx[1]
    assert "attempt 1" in seen_ctx[1]
    assert result
    assert "Arrow automates desktop workflows" in result


def test_consolidate_zero_fact_cycle_is_invalid(monkeypatch):
    # A cycle whose probe retrieves NO evidence must NOT count toward the
    # valid-cycle cap — the engine re-probes instead of advancing.
    consolidator = comorag.ProbeBasedConsolidator(
        api_url="http://x", chunk_size=500, overlap=50, top_k=1,
        max_probes=1, min_facts=1,
    )
    retrievals = {"n": 0}
    probes = iter(["Where is Paris located?", "What is the population of Paris?"])

    def fake_probe(intent, retrieved):
        try:
            return next(probes)
        except StopIteration:
            return None

    def fake_retrieve(probe, chunks, embs, exclude_indices=None):
        retrievals["n"] += 1
        if retrievals["n"] == 1:
            return []  # first probe: no evidence (invalid cycle)
        return [chunks[0]]

    monkeypatch.setattr(
        consolidator, "_compute_embeddings",
        lambda chunks: [[1.0, 0.0]] * len(chunks),
    )
    monkeypatch.setattr(consolidator, "_retrieve_for_probe", fake_retrieve)

    def fake_responder(probe, evidence):
        return "Key Finding: Paris is the capital of France."

    sources = {"context_nodes": [
        "Paris is the capital of France.",
        "The population of Paris is two million.",
    ]}
    result = consolidator.consolidate(
        "Tell me about Paris.", sources,
        probe_generator=fake_probe, responder=fake_responder,
    )
    # Probe 1 was invalid (no evidence) — probe 2 had to supply the facts.
    assert retrievals["n"] >= 2
    assert result


def test_consolidate_min_facts_gate_extends_cycle(monkeypatch):
    # min_facts=3 with a responder yielding one finding per call: the cycle
    # must NOT advance after the first probe — it stays in the SAME slot
    # and probes deeper until the probe sources are exhausted.
    consolidator = comorag.ProbeBasedConsolidator(
        api_url="http://x", chunk_size=500, overlap=50, top_k=1,
        max_probes=1, min_facts=3,
    )
    probes_used = []

    def fake_retrieve(probe, chunks, embs, exclude_indices=None):
        probes_used.append(probe)
        for i, ch in enumerate(chunks):
            if exclude_indices is None or i not in exclude_indices:
                return [ch]
        return []

    monkeypatch.setattr(
        consolidator, "_compute_embeddings",
        lambda chunks: [[1.0, 0.0]] * len(chunks),
    )
    monkeypatch.setattr(consolidator, "_retrieve_for_probe", fake_retrieve)

    def fake_responder(probe, evidence):
        return "Key Finding: Paris is the capital of France."

    sources = {"context_nodes": [
        "Paris is the capital of France.",
        "Paris is located on the river Seine.",
        "The population of Paris is two million.",
    ]}
    result = consolidator.consolidate(
        "Tell me about Paris.", sources, responder=fake_responder,
    )
    # Multiple probes were consumed in the SAME (unfinished) cycle slot.
    assert len(probes_used) >= 2
    assert result


def test_consolidate_composed_facts_and_evidence_tagging(monkeypatch):
    # Responder success -> composed "probe:/Finding:" entry in facts_pool;
    # responder failure -> evidence-tagged entry excluded from facts_pool.
    consolidator = comorag.ProbeBasedConsolidator(
        api_url="http://x", chunk_size=500, overlap=50, top_k=1,
        max_probes=2, min_facts=1,
    )
    responder_calls = {"n": 0}

    def fake_responder(probe, evidence):
        responder_calls["n"] += 1
        if responder_calls["n"] == 1:
            return (
                "Key Finding: Paris is the capital of France.\n"
                "Support: Paris is the capital of France."
            )
        return ""  # second cycle: responder fails -> evidence fallback

    monkeypatch.setattr(
        consolidator, "_compute_embeddings",
        lambda chunks: [[1.0, 0.0]] * len(chunks),
    )

    def _retrieve(probe, chunks, embs, exclude_indices=None):
        for i, ch in enumerate(chunks):
            if exclude_indices is None or i not in exclude_indices:
                return [ch]
        return []

    monkeypatch.setattr(consolidator, "_retrieve_for_probe", _retrieve)

    sources = {"context_nodes": [
        "Paris is the capital of France.",
        "The population of Paris is two million.",
    ]}
    result = consolidator.consolidate(
        "Tell me about Paris.", sources, responder=fake_responder,
    )
    # facts_pool exposes COMPOSED entries only — never raw evidence.
    pool = consolidator.facts_pool
    assert len(pool) == 1
    assert "Finding" in pool[0]
    assert not any(e.startswith("evidence:") for e in pool)
    # The returned context still carries the evidence-tagged fallback.
    assert "evidence:" in result


def test_consolidate_never_empty_when_sources_available(monkeypatch):
    # Retrieval yields nothing for every probe, but sources exist — the
    # engine falls back to intent-anchored retrieval instead of returning
    # "" (the node never runs with zero context).
    consolidator = comorag.ProbeBasedConsolidator(
        api_url="http://x", chunk_size=500, overlap=50, top_k=1,
        max_probes=2, min_facts=3,
    )
    monkeypatch.setattr(
        consolidator, "_compute_embeddings",
        lambda chunks: [[1.0, 0.0]] * len(chunks),
    )
    monkeypatch.setattr(
        consolidator, "_retrieve_for_probe",
        lambda probe, chunks, embs, exclude_indices=None: [],
    )
    # The intent-anchored fallback embeds the prompt itself.
    monkeypatch.setattr(
        consolidator, "_llamacpp_embed",
        lambda texts: [[1.0, 0.0]] * len(texts),
    )
    sources = {"context_nodes": ["alpha beta gamma delta epsilon"]}
    result = consolidator.consolidate("What is alpha?", sources)
    assert result
    assert "alpha" in result


def test_consolidate_stops_when_pool_exhausted(monkeypatch):
    # A single-chunk corpus exhausts the evidence pool after cycle 1 — the
    # fact-driven loop must terminate (no infinite spin), returning the
    # retrieved evidence even though the quota was never met.
    consolidator = comorag.ProbeBasedConsolidator(
        api_url="http://x", chunk_size=500, overlap=50, top_k=1,
        max_probes=12, min_facts=5,
    )
    monkeypatch.setattr(
        consolidator, "_compute_embeddings",
        lambda chunks: [[1.0]] * len(chunks),
    )
    monkeypatch.setattr(
        consolidator, "_retrieve_for_probe",
        lambda probe, chunks, embs, exclude_indices=None: (
            [chunks[0]] if not (exclude_indices and 0 in exclude_indices) else []
        ),
    )
    sources = {"context_nodes": ["alpha beta gamma delta epsilon"]}
    result = consolidator.consolidate("What is alpha?", sources)
    assert result


def test_consolidate_uses_intent_plan_for_probing(monkeypatch):
    # The pipeline first creates an INTENT PLAN (what to know / where to
    # look / what to expect) from the enhanced intent — probes derive from
    # that plan instead of shooting straight from the user prompt.
    consolidator = comorag.ProbeBasedConsolidator(
        api_url="http://x", chunk_size=500, overlap=50, top_k=1,
        max_probes=1, min_facts=1,
    )
    captured = {}
    plan = (
        "INTENT SUMMARY: what the user is doing on screen.\n"
        "SEARCH PLAN:\n- find the document the user is working on\n"
        "EXPECTED ANSWER SHAPE: a short action description."
    )

    def fake_planner(intent):
        captured["plan_input"] = str(intent)
        return plan

    def fake_probe(intent, retrieved):
        captured["probe_ctx"] = str(intent)
        return "What document is the user working on?"

    monkeypatch.setattr(
        consolidator, "_compute_embeddings",
        lambda chunks: [[1.0, 0.0]] * len(chunks),
    )
    monkeypatch.setattr(
        consolidator, "_retrieve_for_probe",
        lambda probe, chunks, embs, exclude_indices=None: [chunks[0]],
    )

    def fake_responder(probe, evidence):
        return "Key Finding: the user is working on a document."

    sources = {"context_nodes": ["the user is working on a document on screen"]}
    result = consolidator.consolidate(
        "what is the user doing on screen?", sources,
        intent_planner=fake_planner, probe_generator=fake_probe,
        responder=fake_responder,
    )
    # The planner saw the enhanced intent (goal + context).
    assert "what is the user doing on screen?" in captured["plan_input"]
    # The probe generator sees the INTENT PLAN as the probing base.
    assert "INTENT PLAN (base for probing)" in captured["probe_ctx"]
    assert "SEARCH PLAN" in captured["probe_ctx"]
    # ...combined with the FULL base context (source of truth).
    assert "BASE CONTEXT (source of truth" in captured["probe_ctx"]
    assert result


def test_consolidate_probe_ctx_includes_full_base_context(monkeypatch):
    # The probe generator must see the FULL base context (source of truth),
    # not just the intent excerpt — probes derive from intent + base context.
    consolidator = comorag.ProbeBasedConsolidator(
        api_url="http://x", chunk_size=500, overlap=50, top_k=1,
        max_probes=1, min_facts=1, probe_context_chars=12000,
    )
    captured = {}
    mid_marker = "MIDDLE_UNIQUE_MARKER_12345"
    source = ("HEAD " * 400) + mid_marker + ("TAIL " * 400)

    def fake_planner(intent):
        captured["plan_input"] = str(intent)
        return ("INTENT SUMMARY: find the middle detail.\n"
                "SEARCH PLAN:\n- find the middle marker\n"
                "EXPECTED ANSWER SHAPE: text.")

    def fake_probe(intent, retrieved):
        captured["probe_ctx"] = str(intent)
        return "What does the middle marker say?"

    monkeypatch.setattr(
        consolidator, "_compute_embeddings",
        lambda chunks: [[1.0, 0.0]] * len(chunks),
    )
    monkeypatch.setattr(
        consolidator, "_llamacpp_embed",
        lambda texts: [[1.0, 0.0]] * len(texts),
    )
    monkeypatch.setattr(
        consolidator, "_retrieve_for_probe",
        lambda probe, chunks, embs, exclude_indices=None: [chunks[0]],
    )

    def fake_responder(probe, evidence):
        return "Key Finding: the middle marker is present."

    sources = {"context_nodes": [source]}
    result = consolidator.consolidate(
        "what is the user doing on screen?", sources,
        intent_planner=fake_planner, probe_generator=fake_probe,
        responder=fake_responder,
    )
    # The intent excerpt (similarity top-k) does NOT carry the middle marker.
    assert mid_marker not in captured["plan_input"]
    # ...but the probe context carries the FULL base context (source of truth).
    assert "BASE CONTEXT (source of truth" in captured["probe_ctx"]
    assert mid_marker in captured["probe_ctx"]
    assert result


def test_consolidate_enhances_intent_with_facts_each_cycle(monkeypatch):
    # After cycle 1 the intent is ENHANCED with the discovered facts, and
    # the next cycle's probe derives from the evolved intent — knowledge
    # grows as a tree of facts over cycles.
    consolidator = comorag.ProbeBasedConsolidator(
        api_url="http://x", chunk_size=500, overlap=50, top_k=1,
        max_probes=2, min_facts=1,
    )
    seen = []

    def fake_probe(intent, retrieved):
        seen.append(str(intent))
        return "What does the document say?"

    monkeypatch.setattr(
        consolidator, "_compute_embeddings",
        lambda chunks: [[1.0, 0.0]] * len(chunks),
    )

    def _retrieve(probe, chunks, embs, exclude_indices=None):
        for i, ch in enumerate(chunks):
            if exclude_indices is None or i not in exclude_indices:
                return [ch]
        return []

    monkeypatch.setattr(consolidator, "_retrieve_for_probe", _retrieve)

    def fake_responder(probe, evidence):
        return _grounded_reply("the document mentions cycle facts.", evidence)

    sources = {"context_nodes": [
        "the document mentions cycle facts and more details",
        "the second chunk has extra details",
    ]}
    result = consolidator.consolidate(
        "Tell me about the document.", sources,
        probe_generator=fake_probe, responder=fake_responder,
    )
    # The intent seen by cycle 2 is ENHANCED toward the current drift path
    # (the most recently discovered facts) — the tree extends each cycle.
    assert len(seen) >= 2
    assert "ENHANCED INTENT (current drift path" in seen[1]
    assert "[cycle 1]" in seen[1]
    assert "cycle facts" in seen[1]
    # The fact pool grows as a tagged knowledge tree.
    pool = consolidator.facts_pool
    assert pool and "[cycle 1]" in pool[0]
    assert result


def test_coerce_probes_splits_multi_line_and_dedupes():
    # A generator may return one question, several lines, a list or a JSON
    # object; the engine keeps up to 3 DISTINCT questions (ComoRAG
    # entity-priority probes).
    assert comorag._coerce_probes("What is X?") == ["What is X?"]
    assert comorag._coerce_probes(
        "What is X?\n\nWhat is Y?\nWhat is X?\nWhat is Z?\nWhat is W?"
    ) == ["What is X?", "What is Y?", "What is Z?"]
    assert comorag._coerce_probes(["What is A?", "What is B?"]) == \
        ["What is A?", "What is B?"]
    assert comorag._coerce_probes({"probe_1": "What is A?",
                                   "probe_2": "What is B?"}) == \
        ["What is A?", "What is B?"]
    # Non-question commentary is dropped when question lines exist.
    assert comorag._coerce_probes(
        "I will look for:\nWhat is the value?\n"
    ) == ["What is the value?"]
    assert comorag._coerce_probes(None) == []
    assert comorag._coerce_probes("   ") == []


def test_probe_similar_collapses_paraphrases():
    # The observed cycle-1 trio: probes 1 and 3 ask the same thing in other
    # words (two retrievals + two composer calls for one answer), while the
    # country/city/state trio is genuinely distinct.
    assert comorag._probe_similar(
        "What city does Alex David Mason live in?",
        "Where is Alex David Mason located according to his profile?",
    )
    assert not comorag._probe_similar(
        "What is the country mentioned in the application details?",
        "What is the city mentioned in the application details?",
    )


def test_plan_focus_tokens_reads_search_plan_terms():
    plan = (
        "INTENT SUMMARY: what to find.\n"
        "SEARCH PLAN:\n- find the city value\n- find riverside\n"
        "EXPECTED ANSWER SHAPE: a short value.\n"
    )
    toks = comorag._plan_focus_tokens(plan)
    assert "city" in toks
    assert "riverside" in toks
    # Generic scaffolding vocabulary is not a search target.
    assert "value" not in toks and "details" not in toks
    assert comorag._plan_focus_tokens("") == set()
    assert comorag._plan_focus_tokens("INTENT SUMMARY: nothing to plan.") == set()


def test_consolidate_stops_on_confident_grounded_answer(monkeypatch):
    """A judge ANSWER whose draft is grounded ends the loop immediately.

    ``max_probes`` is a CEILING, never a minimum the sweep must serve first
    (Stop-RAG: retrieval continues only while it still adds value).

    Observed regression: the judge declared the goal answerable after one
    round, a cycle floor overrode it, and the extra rounds only re-derived
    the same value while the requested field value never surfaced.
    """
    consolidator = comorag.ProbeBasedConsolidator(
        api_url="http://x", chunk_size=200, overlap=20, top_k=1,
        max_probes=6, min_facts=1,
    )
    retrievals = {"n": 0}

    def fake_probe(intent, facts):
        return "What detail is number %d?" % (retrievals["n"] + 1)

    def fake_responder(probe, evidence):
        return _grounded_reply("detail alpha", evidence)

    def fake_retrieve(probe, chunks, embs, exclude_indices=None):
        retrievals["n"] += 1
        for i, ch in enumerate(chunks):
            if exclude_indices is None or i not in exclude_indices:
                return [ch]
        return []

    monkeypatch.setattr(
        consolidator, "_compute_embeddings",
        lambda chunks: [[1.0, 0.0]] * len(chunks),
    )
    monkeypatch.setattr(consolidator, "_retrieve_for_probe", fake_retrieve)
    sources = {"context_nodes": [
        " ".join("bullet%03d" % i for i in range(60))]}
    consolidator.consolidate(
        "What is the detail?", sources,
        probe_generator=fake_probe, responder=fake_responder,
        judge=lambda g, f, ev: "ANSWER: bullet001 is the detail",
    )
    # One evidence round plus the request-anchored retrieval of that round.
    assert retrievals["n"] <= 2
    assert consolidator.facts_pool


def test_off_topic_finding_is_not_a_fact(monkeypatch):
    """A grounded finding that answers ANOTHER question is not curated.

    Regression: resume trivia ("date of birth") shares no specific term with
    the planned search targets, yet it satisfied the per-cycle fact quota on
    every cycle — so the loop never saturated, kept re-probing, and shipped
    non-answers as distilled facts.
    """
    consolidator = comorag.ProbeBasedConsolidator(
        api_url="http://x", chunk_size=500, overlap=50, top_k=1,
        max_probes=1, min_facts=1,
    )
    monkeypatch.setattr(
        consolidator, "_compute_embeddings",
        lambda chunks: [[1.0, 0.0]] * len(chunks),
    )
    monkeypatch.setattr(
        consolidator, "_retrieve_for_probe",
        lambda probe, chunks, embs, exclude_indices=None: [chunks[0]],
    )

    def fake_responder(probe, evidence):
        return (
            "Key Finding: the applicant date of birth is 01/02/1990.\n"
            "Support: Applicant date of birth 01/02/1990.\n\n"
            "Key Finding: the location city is riverside.\n"
            "Support: Location (city) = riverside"
        )

    sources = {"context_nodes": [
        "Applicant date of birth 01/02/1990. Location (city) = riverside."]}
    consolidator.consolidate(
        "Fill the form field: Location (city)", sources,
        intent_planner=lambda intent: "SEARCH PLAN:\n- find the city value\n",
        probe_generator=lambda intent, facts: "What is the location (city)?",
        responder=fake_responder,
    )
    pool = "\n".join(consolidator.facts_pool)
    assert "riverside" in pool              # the on-topic finding is curated
    assert "date of birth" not in pool   # the off-topic one is not


def test_consolidate_uses_all_generated_probes_per_cycle(monkeypatch):
    """A generator returning 3 questions makes the cycle retrieve for ALL of
    them (ComoRAG entity-priority probing), not just the first."""
    consolidator = comorag.ProbeBasedConsolidator(
        api_url="http://x", chunk_size=200, overlap=20, top_k=1,
        max_probes=1, min_facts=1,
    )
    used = []

    def fake_probe(intent, facts):
        return ("What is alpha about?\n"
                "What is beta about?\n"
                "What is gamma about?\n")

    def fake_retrieve(probe, chunks, embs, exclude_indices=None):
        used.append(probe)
        for i, ch in enumerate(chunks):
            if exclude_indices is None or i not in exclude_indices:
                return [ch]
        return []

    monkeypatch.setattr(
        consolidator, "_compute_embeddings",
        lambda chunks: [[1.0, 0.0]] * len(chunks),
    )
    monkeypatch.setattr(consolidator, "_retrieve_for_probe", fake_retrieve)

    def fake_responder(probe, evidence):
        return "Key Finding: about %s" % probe[:24]

    sources = {"context_nodes": [
        "alpha alpha alpha\n\nbeta beta beta\n\ngamma gamma gamma"]}
    consolidator.consolidate(
        "Tell me about the letters.", sources,
        probe_generator=fake_probe, responder=fake_responder,
    )
    assert "What is alpha about?" in used
    assert "What is beta about?" in used
    assert "What is gamma about?" in used


def test_bundled_embedding_path():
    assert comorag._bundled_embedding_path("all-MiniLM-L6-v2") is None or isinstance(
        comorag._bundled_embedding_path("all-MiniLM-L6-v2"), str)
