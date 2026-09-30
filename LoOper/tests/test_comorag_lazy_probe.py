"""Probe-driven lazy ingestion tests for AI/comorag_engine.py.

Guards the ComoRAG ingestion invariant: the consolidation loop must NEVER
embed the whole context corpus in one shot.  Chunks are embedded lazily —
only the chunks a probe's lexical pre-select picks — and each chunk text is
embedded at most once per process (per-chunk vector cache), so repeat node
executions reuse the vectors.
"""

import math
import re

import pytest

from AI import comorag_engine as comorag


@pytest.fixture(autouse=True)
def _clear_vec_cache():
    comorag._CHUNK_VEC_CACHE.clear()


class _FakeEmbed:
    """Deterministic pseudo-embedder that records every batch it sees."""

    def __init__(self):
        self.calls = []  # batch sizes
        self.counts = {}  # text -> times embedded

    def embed(self, texts):
        self.calls.append(len(texts))
        for t in texts:
            self.counts[str(t)] = self.counts.get(str(t), 0) + 1
        return [
            [math.sin((sum(ord(c) for c in str(t)) % 997) + i * 1.7)
             for i in range(16)]
            for t in texts
        ]


def _big_corpus(topics: int = 26) -> str:
    parts = []
    for n in range(topics):
        parts.append(
            f"Topic {n}: the python {n} module implements pipeline {n} "
            f"with classes {n} and errors {n}. " * 3
        )
    return "\n\n".join(parts)


def _make(fake, **over):
    kwargs = dict(
        api_url="http://x", embed_client=fake,
        chunk_size=180, overlap=20, top_k=2,
        char_budget=500, max_probes=3, min_facts=1,
        probe_context_chars=1200, probe_max_tokens=32,
    )
    kwargs.update(over)
    return comorag.ProbeBasedConsolidator(**kwargs)


def _grounded_reply(finding, evidence):
    """A composer reply in the shape both real responders produce.

    The engine requires a finding's ``Support`` quote to actually occur in
    the probe's evidence (``comorag._is_grounded_finding``), so a fake
    composer that omits the quote would be correctly rejected as fabricated
    and no cycle would ever complete.
    """
    return "Key Finding: %s\nSupport: %s" % (
        finding, str(evidence).strip()[:60])


def test_consolidation_never_embeds_whole_corpus_in_one_shot():
    fake = _FakeEmbed()
    cons = _make(fake)
    out = cons.consolidate(
        prompt="how many errors does the python 7 module raise?",
        system_prompt="",
        context_sources={"documents": [_big_corpus()]},
    )
    n_chunks = len(cons._cached_chunks)
    assert n_chunks > 40, "corpus must exceed any single candidate pool"
    assert fake.calls, "expected at least one embed call"
    # The defect being fixed: one HTTP embed call containing ALL chunks.
    assert max(fake.calls) <= 40, f"one-shot embed happened (batch {max(fake.calls)})"
    # Per-chunk cache: no chunk text embedded twice within one run.
    chunk_dups = [
        fake.counts.get(c, 0) for c in set(cons._cached_chunks)
    ]
    assert max(chunk_dups) == 1, "a chunk was embedded more than once (cache miss)"
    assert out  # consolidation still produced grounded content


def test_per_chunk_vectors_reused_across_consolidator_instances():
    fake1 = _FakeEmbed()
    cons1 = _make(fake1)
    corpus = _big_corpus()
    cons1.consolidate(
        prompt="how many errors does the python 7 module raise?",
        system_prompt="",
        context_sources={"documents": [corpus]},
    )
    chunk_set = set(cons1._cached_chunks)
    first_chunk_embeds = sum(fake1.counts.get(c, 0) for c in chunk_set)
    # Sanity: run 1 embedded the chunks its probes actually selected — each at
    # most ONCE (per-chunk cache).  It deliberately does NOT embed the whole
    # corpus: with the document-order fallback gone, only chunks that share a
    # content token with a probe are candidates.
    assert first_chunk_embeds > 0
    # Every CACHED CHUNK was embedded at most once (per-chunk cache).  Query
    # texts (the probe, the request anchor) are not chunks and may repeat.
    assert max(fake1.counts.get(c, 0) for c in chunk_set) == 1

    fake2 = _FakeEmbed()
    cons2 = _make(fake2)
    cons2.consolidate(
        prompt="how many errors does the python 7 module raise?",
        system_prompt="",
        context_sources={"documents": [corpus]},
    )
    # A brand-new consolidator on the same corpus embeds NO chunk vectors —
    # the module-level per-chunk cache serves every one.
    second_chunk_embeds = sum(
        fake2.counts.get(c, 0) for c in set(cons2._cached_chunks)
    )
    assert second_chunk_embeds == 0, "per-chunk cache not reused across runs"


def test_lexical_excerpt_is_embedding_free():
    fake = _FakeEmbed()
    cons = _make(fake)
    chunks = cons._build_chunks([_big_corpus()], "")
    excerpt = cons._lexical_excerpt("how many errors does python 7 raise?", chunks)
    assert excerpt  # goal-overlapping text found without any embed call
    assert not fake.calls, "lexical excerpt must not embed anything"


def test_probe_key_ignores_list_numbering():
    assert comorag._probe_key(
        "1. Who is the person in this document context?"
    ) == comorag._probe_key("Who is the person in this document context?")


# ---------------------------------------------------------------------------
# Probe rejection: WHY a probe gets rejected, and what it costs
# ---------------------------------------------------------------------------


def test_repeat_probe_does_not_burn_the_retry_ladder(caplog):
    """A generator with nothing NEW to ask must be detected on the first
    re-ask and fall straight to the document ladder: retrying it only re-emits
    the same question (observed: 4 retries x ~17s before the ladder was tried),
    and that rejection branch used to be silent."""
    fake = _FakeEmbed()
    cons = _make(fake, max_probes=2)
    asks = []

    def _gen(intent, facts):
        asks.append(1)
        return "what errors does the python 7 module raise during init?"

    def _respond(probe, ev):
        return _grounded_reply(
            "the python module raises %d errors." % len(asks), ev)

    caplog.set_level("INFO")
    out = cons.consolidate(
        prompt="how many errors does the python 7 module raise?",
        system_prompt="",
        context_sources={"documents": [_big_corpus()]},
        probe_generator=_gen,
        responder=_respond,
    )
    # ONE question per cycle: cycle 1 is accepted, cycle 2 repeats it and the
    # ladder stops there (the old behaviour burned 3 retries = 4 calls/cycle).
    assert len(asks) == 2, asks
    assert "already asked" in caplog.text
    assert out  # the document ladder still produced grounded content


def test_probe_echo_of_the_goal_is_reported_why(caplog):
    """A probe that just rephrases the GOAL is rejected WITH its reason — for a
    form field the label IS the query, so the caller frames the goal as a task
    sentence to keep its natural probe valid."""
    fake = _FakeEmbed()
    cons = _make(fake, max_probes=1, probe_retry_attempts=0)
    caplog.set_level("INFO")
    cons.consolidate(
        prompt="what errors does the python 7 module raise?",
        system_prompt="",
        context_sources={"documents": [_big_corpus()]},
        probe_generator=lambda intent, facts: (
            "what errors does the python 7 module raise?"
        ),
    )
    assert "Rejected because" in caplog.text
    assert "echo of the user request" in caplog.text


def test_empty_probe_generation_is_logged_and_not_retried(caplog):
    """An empty generation is not fixable by retrying either — it is reported
    and the document ladder takes over."""
    fake = _FakeEmbed()
    cons = _make(fake, max_probes=2)
    calls = []

    def _gen(intent, facts):
        calls.append(1)
        return ""

    def _respond(probe, ev):
        return _grounded_reply("fact number %d." % len(calls), ev)

    caplog.set_level("INFO")
    out = cons.consolidate(
        prompt="how many errors does the python 7 module raise?",
        system_prompt="",
        context_sources={"documents": [_big_corpus()]},
        probe_generator=_gen,
        responder=_respond,
    )
    assert len(calls) == 2, calls     # one attempt per cycle, not four
    assert "generator returned nothing" in caplog.text
    assert out


# ---------------------------------------------------------------------------
# Sweep discipline: an unvalidated plan, section probes, an unbounded field
# and a cross-backend vector cache are what made fields fail and re-run
# ---------------------------------------------------------------------------


def test_intent_plan_without_plan_sections_is_dropped(caplog):
    """A malformed plan must NOT become the base for every probe.

    Observed live: the planner (a raw /completion call on a small model)
    returned a Python function.  It was used as-is, so probes derived from
    code identifiers AND the relevance gate took its vocabulary — which
    dropped correct findings as off-topic.
    """
    fake = _FakeEmbed()
    cons = _make(fake)
    asks = []

    def _planner(intent):
        asks.append(1)
        return ('"""Pipeline to find the value."""\n'
                "def extract(text):\n    return None")

    caplog.set_level("INFO")
    cons.consolidate(
        prompt="how many errors does the python 7 module raise?",
        system_prompt="",
        context_sources={"documents": [_big_corpus()]},
        intent_planner=_planner,
    )
    assert len(asks) == 2, "an invalid plan must be retried exactly once"
    assert "no usable plan" in caplog.text
    assert "INTENT PLAN (base for probing)" not in caplog.text


def test_partial_intent_plan_is_accepted(caplog):
    """ONE plan label is enough — the generator only needs the SEARCH PLAN
    items, so a partial reply must not be discarded."""
    fake = _FakeEmbed()
    cons = _make(fake)
    caplog.set_level("INFO")
    cons.consolidate(
        prompt="how many errors does the python 7 module raise?",
        system_prompt="",
        context_sources={"documents": [_big_corpus()]},
        intent_planner=lambda intent: "SEARCH PLAN:\n- find the errors\n",
    )
    assert "no usable plan" not in caplog.text


def test_probe_fallback_never_probes_document_sections(caplog):
    """A dead probe generator must fall back to the GOAL anchor, never to a
    document SECTION heading: a heading is not a question about the field, so
    it retrieves unrelated material the composer then answers from (observed:
    'Skills' and 'Presentation Letter (Cover Letter)' used as probes)."""
    fake = _FakeEmbed()
    cons = _make(fake, max_probes=2)
    corpus = ("## Skills\npython tooling and pipelines.\n\n"
              "## Presentation Letter (Cover Letter)\nDear team, hello.\n\n"
              "## Experience\nthe python 7 module raises its errors on init.")
    caplog.set_level("INFO")
    cons.consolidate(
        prompt="how many errors does the python 7 module raise?",
        system_prompt="",
        context_sources={"documents": [corpus]},
        probe_generator=lambda intent, facts: "",
    )
    assert "Probe (goal anchor" in caplog.text
    assert "Probe (section" not in caplog.text


def test_chunk_vector_cache_is_dropped_when_the_backend_changes():
    """The chunk cache is process-global and keyed on chunk TEXT alone.  Two
    embedding backends in one process (embeddinggemma for llama.cpp nodes,
    nomic-embed-text for Ollama ones) are the SAME dimension, so reusing the
    other backend's vectors ranks silently meaningless."""
    corpus = _big_corpus()
    fake_a = _FakeEmbed()
    cons_a = _make(fake_a, cache_namespace="llamacpp:embeddinggemma")
    cons_a.consolidate(
        prompt="how many errors does the python 7 module raise?",
        system_prompt="",
        context_sources={"documents": [corpus]},
    )
    assert fake_a.calls, "first backend embedded its candidates"

    fake_b = _FakeEmbed()
    cons_b = _make(fake_b, cache_namespace="ollama:nomic-embed-text")
    cons_b.consolidate(
        prompt="how many errors does the python 7 module raise?",
        system_prompt="",
        context_sources={"documents": [corpus]},
    )
    assert fake_b.calls, "a different backend must NOT reuse the cached vectors"


def test_stall_guard_stops_a_field_whose_findings_are_all_rejected(caplog):
    """A composer rejected wholesale (every entry tagged `evidence:`) used to
    reset the stall guard, so the field kept probing until the cycle ceiling.
    Two consecutive cycles with no composed fact must stop the sweep.
    """
    fake = _FakeEmbed()
    cons = _make(fake, max_probes=6)
    corpus = "\n\n".join(
        ("the python module %d raises its errors when the pipeline %d "
         "starts up and the classes %d load." % (n, n, n)) * 4
        for n in range(26)
    )
    asks = []

    def _gen(intent, facts):
        asks.append(1)
        return ("what errors does the python 7 module raise during init %d?"
                % len(asks))

    caplog.set_level("WARNING")
    cons.consolidate(
        prompt="how many errors does the python 7 module raise?",
        system_prompt="",
        context_sources={"documents": [corpus]},
        probe_generator=_gen,
        # No Support quote -> fabricated -> rejected on EVERY cycle.
        responder=lambda probe, ev: "Key Finding: San Francisco, CA",
    )
    assert len(asks) == 2, asks
    assert "added no new composed fact" in caplog.text


# ---------------------------------------------------------------------------
# Relevance verdict: a GROUNDED finding can still answer the wrong question
# ---------------------------------------------------------------------------


def _unkeyed_corpus(n=6):
    """Plain prose with no ``label: value`` lines — only the composer's
    findings can enter the pool."""
    return "\n\n".join(
        "Entry %d: the python 7 module raises its errors during init." % i
        for i in range(n)
    )


# A GROUNDED reply: the Support quote must occur in the evidence AND the claim
# may not introduce a term the evidence lacks (the fabrication guard rejects
# both), so both sides reuse the corpus wording.
_QUOTE = "the python 7 module raises its errors during init"
_GROUNDED = "Key Finding: %s\nSupport: %s." % (_QUOTE, _QUOTE)


def _kept_counts(text):
    """The 'N composed kept' counters of the per-cycle tables."""
    return [int(m.group(1)) for m in re.finditer(r"(\d+) composed kept", text)]


def _refused_counts(text):
    """How many facts the synthesis purge dropped ('N off-topic fact(s)')."""
    return sum(int(m.group(1)) for m in re.finditer(r"(\d+) off-topic", text))


def test_off_topic_findings_are_purged_at_synthesis(caplog):
    """The relevance verdict runs ONCE, at the final synthesis, over the
    finished pool — not per cycle.  A verdict that refuses every fact drops
    them from the pool before the synthesis and the exposed fact pool, and
    the purge is COUNTED and logged."""
    fake = _FakeEmbed()
    cons = _make(fake, max_probes=2)
    asked = []

    def _gen(intent, facts):
        asked.append(1)
        return ("what errors does the python 7 module raise during init %d?"
                % len(asked))

    caplog.set_level("INFO")                 # the kept counter logs at INFO
    cons.consolidate(
        prompt="how many errors does the python 7 module raise?",
        system_prompt="",
        context_sources={"documents": [_unkeyed_corpus()]},
        probe_generator=_gen,
        responder=lambda probe, ev: _GROUNDED,
        relevance_judge=lambda question, claims: [False] * len(claims),
    )
    # The cycle kept the finding, the synthesis purge dropped it ...
    assert max(_kept_counts(caplog.text) or [0]) >= 1
    assert _refused_counts(caplog.text) >= 1
    assert "ComoRAG Purge" in caplog.text
    # ... so the exposed fact pool holds nothing composed.
    assert cons.facts_pool == []


def test_unavailable_relevance_verdict_keeps_the_finding(caplog):
    """FAIL OPEN: a judge that cannot answer (None, or a verdict that does not
    line up with the claims) must never drop a real finding at the purge."""
    fake = _FakeEmbed()
    cons = _make(fake, max_probes=2)
    asked = []

    def _gen(intent, facts):
        asked.append(1)
        return ("what errors does the python 7 module raise during init %d?"
                % len(asked))

    # INFO: the per-cycle 'composed kept' counter is logged there.
    caplog.set_level("INFO")
    cons.consolidate(
        prompt="how many errors does the python 7 module raise?",
        system_prompt="",
        context_sources={"documents": [_unkeyed_corpus()]},
        probe_generator=_gen,
        responder=lambda probe, ev: _GROUNDED,
        # A judge that answers nothing is as unusable as a wrong-length one.
        relevance_judge=lambda question, claims: None,
    )
    assert _refused_counts(caplog.text) == 0            # nothing purged
    assert "ComoRAG Purge" not in caplog.text
    assert cons.facts_pool                              # the finding survived
