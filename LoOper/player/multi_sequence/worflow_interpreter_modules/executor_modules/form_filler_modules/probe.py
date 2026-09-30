"""Per-field retrieval: the document head, the ComoRAG hooks and the probe."""

import logging
import re

from .common import (
    _FFEmbedClient,
    _ff_drop_foreign_qa,
    _ff_finding_lines,
    _ff_norm,
    _strip_think,
    log_block,
    logger,
)

try:
    # The engine's grounding rule — ONE implementation, shared.  A composed
    # candidate is verified with the same function the engine uses to verify
    # a pooled fact, so "grounded" cannot mean two different things.
    from AI.comorag_engine import grounding_violations as _ff_grounding_violations
except Exception:  # pragma: no cover - the engine drives every caller
    def _ff_grounding_violations(text, evidence, allow=""):
        # Only the engine's own side-channel candidate needs this guard, and
        # the engine cannot call this module when it is unimportable.
        return []


def _ff_exact_value_token(token):
    """True for a token that must appear VERBATIM in the source.

    A paraphrase can never legitimately transform a URL, an email or a phone
    number - those are exact values, so a term the excerpts do not spell out is
    an INVENTION.  Everything else (a count computed from dates, a capitalized
    word, a synonym) may be a correct INFERENCE and is left to the dynamic
    verdict.
    """
    t = str(token or "")
    if re.search(r"@|/|://|www\.", t):
        return True
    digits = sum(1 for ch in t if ch.isdigit())
    return len(t) >= 7 and digits >= 5


# The FINDING label as composers actually emit it - bare ("Key Finding:"),
# markdown-bolded ("**Key Finding:**") or headed ("## Key Findings").  A bare
# startswith("key finding") missed the bolded form this small model produces
# (its probes read "**Question 1:** ..."), so EVERY finding was dropped and the
# fact pool stayed empty (observed: "the responder produced nothing" on 2 of 3
# fields, 0 composed facts).  Same shape the engine's own parser uses
# (``_FINDING_LABEL_RE``), so a finding is read ONE way everywhere.
_FF_FINDING_LABEL_RE = re.compile(
    r"^\s*[\*\-#>\s]*(?:\d+[.)]?\s*)?(?:key\s+)?findings?\s*\d*\s*[\*\s]*:",
    re.IGNORECASE | re.MULTILINE,
)
_FF_SUPPORT_SPLIT_RE = re.compile(
    r"(?im)^\s*[\*\-#>\s]*support\s*[\*\s]*:"
)

# The composer's only job is a claim plus a COPIED quote; a block with no
# Support line is unusable.  What the Support TEXT reads like is left to the
# relevance verdict and the model - a list of reasoning phrases is a language,
# not a rule.


def _ff_clean_findings(out, query=""):
    """Keep only USABLE findings: a claim plus a Support line.

    Drops a block with no Support line and a claim that merely restates the
    retrieval query that fetched it (a restated query is not a finding).  The
    FINDING label and the Support marker are matched with the engine's own
    tolerant shapes (bold / heading), so a composer that marks them up
    ("**Key Finding:**") is not dropped for its markers alone.
    """
    q = _ff_norm(query)
    text = str(out or "").strip()
    if not text:
        return ""
    starts = [m.start() for m in _FF_FINDING_LABEL_RE.finditer(text)]
    blocks = (
        [text[i:j].strip()
         for i, j in zip(starts, starts[1:] + [len(text)])]
        if starts else [text]
    )
    kept = []
    for b in blocks:
        b = b.strip()
        if not b or not _FF_SUPPORT_SPLIT_RE.search(b):
            continue
        claim = _FF_FINDING_LABEL_RE.sub(
            "", _FF_SUPPORT_SPLIT_RE.split(b, 1)[0], count=1,
        ).strip()
        if q and claim and _ff_norm(claim) in q:
            continue
        kept.append(b)
    return "\n\n".join(kept)


class FormFillerProbeMixin:
    """Per-field retrieval: the document head, the ComoRAG hooks and the probe."""

    def _ff_synth_supported(self, claim, evidence):
        """Laya's entailment verdict on a composed answer (None = unavailable)."""
        try:
            from AI.laya_hooks import grounding_or
            return grounding_or(claim, evidence)
        except Exception as exc:
            logger.debug("[FORM] Laya grounding verdict unavailable: %s", exc)
            return None

    def _ff_doc_texts(self, documents):
        """Extract the attached documents to plain text (cached per set)."""
        import os
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

    def _ff_doc_head(self, documents, limit):
        """Opening slice of the attached documents (cached per document set).

        Short form labels ('First name', 'City') embed poorly against a large
        chunk pool, so a document's HEADER chunk - where a resume keeps the
        name / contact / address - can rank far below the top-k window and never
        reach the model.  The head is cheap, bounded and always relevant, so it
        is prepended to the probed slices.
        """
        key = (tuple(str(d) for d in (documents or [])), int(limit))
        cache = getattr(self, "_ff_doc_head_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            try:
                self._ff_doc_head_cache = cache
            except Exception:
                pass
        if key in cache:
            return cache[key]
        texts = self._ff_doc_texts(documents)
        head = "\n\n".join(texts).strip()[: max(0, int(limit))]
        cache[key] = head
        return head

    def _ff_fact_answer(self, label, probe, evidence, cfg, stop_flag):
        """One ComoRAG cycle's RESPONDER: compose findings from the evidence.

        Mirrors the LLM node's ``_generate_fact_answer``: the responder ANSWERS
        the cycle's probe with COMPOSED findings so the cycle counts and the
        fact pool grows.  A respond-or-decline design (returning None whenever
        the retrieved slice did not directly answer the field) stalled the loop
        on almost every probe: the fact pool stayed empty, no cycle ever
        completed, and the engine swept ALL probes before giving up - which is
        what made the consolidation look bypassed.  ``None`` is returned only
        when the model produced nothing at all.
        """
        system = (
            "You compose grounded findings for ONE form field. Use ONLY terms "
            "that appear in the evidence; never write about anything the field "
            "is not asking for. The evidence may hold ANSWERS TO OTHER "
            "QUESTIONS (a previous form's questionnaire, a '## question' Q&A "
            "list): those are NOT evidence for this field - never restate one "
            "as a finding."
        )
        prompt = (
            "You are composing key findings from retrieved evidence. "
            "Compose up to 5 KEY FINDINGS that help answer the form field. A "
            "finding is a composed statement that follows from the Evidence - "
            "it is NOT a quote - and a finding about the document's general "
            "subject is USELESS: the point is the VALUE the field asks for "
            "(for 'LinkedIn Profile URL' that value is the URL itself, not a "
            "summary of the person). Every finding must carry a Support line "
            "quoting the exact sentence(s) it came from - a finding you cannot "
            "support with a quoted sentence must be OMITTED, never stated "
            "anyway. Greetings, signatures, 'thank you for your time', address "
            "blocks and note-to-self lines are NOT evidence of anything.\n"
            "Format per finding:\n"
            "Key Finding: <composed statement supported by the Evidence>\n"
            "Support: <verbatim sentence(s) from the Evidence>\n"
            "Any exact value - a URL, an email, a phone number, a date, a "
            "number, a company or person name - MUST be copied "
            "character-for-character from the Evidence: NEVER reconstruct, "
            "shorten, reformat or 'correct' it (a mangled value is worse than "
            "no finding at all).\n"
            "DISCARD everything in the Evidence that does not bear on the "
            "field, and never introduce a name or term absent from the "
            "Evidence. A finding that answers a DIFFERENT question is USELESS "
            "even when it is well supported - a '## <other question>' block "
            "with its answer is NOT this field's finding, and repeating it "
            "every cycle is the failure this rule exists to stop.\n"
            f"The field is '{label}': a TRUE fact that does not help fill THAT "
            "field is worthless here (a salary fact is NOT a finding for a "
            "location field).\n"
            "Write ONLY the findings - never your reasoning, doubts or "
            "commentary (no 'Wait', 'However', 'based on common knowledge', "
            "'it can be inferred', 'the evidence does not mention'). A Support "
            "line is COPIED from the Evidence, never composed.\n\n"
            f"Form field:\n{label}\n\n"
            f"Task: {cfg['instruction']}\n\n"
            # The retrieval query is WHY this evidence came back, never
            # content: fed as "Retrieval route" the composer RESTATED it as the
            # finding (observed: the claim WAS the goal sentence).
            f"Retrieval query (NOT evidence - never restate it as a finding): "
            f"{str(probe or '')[:200]}\n\n"
            f"Evidence:\n{str(evidence or '')[-2500:]}\n\n"
            "Key Findings:"
        )
        out = _strip_think(self._ff_llm_call(prompt, system, cfg, stop_flag))
        out = (out or "").strip()
        if not out or out.strip(" .").lower() in ("none", "n/a", "no findings"):
            return None
        out = _ff_clean_findings(out, probe)
        if not out:
            return None
        return out[:800]

    def _ff_intent_plan(self, intent, cfg, stop_flag):
        """INTENT PLANNER for one field (the LLM node's proven step).

        Without it the probes are shot straight from the field label; with
        it the model first writes WHAT it needs to find, WHERE in the
        material to look and WHAT the value should look like, and every
        probe then derives from that plan.  The LLM node's ComoRAG runs
        exactly this step, and it is what keeps its probing productive over
        noisy, oversized context.
        """
        plan_cfg = dict(cfg)
        # Budgets below are FINAL-ANSWER tokens; the engine adds the reasoning
        # headroom on top.  192/256 were too small for the structured replies
        # these steps ask for (the plan came back mid-sentence duplicated and
        # "Support:" quotes arrived truncated mid-word).
        plan_cfg["max_tokens"] = 384
        system = (
            "You plan a context search for a pipeline that fills ONE form "
            "field. Use ONLY terms that appear in the material."
        )
        prompt = (
            "Based on the GOAL and the MATERIAL below, write what the "
            "pipeline needs to find before answering.\n\n"
            "Output exactly three sections:\n"
            "INTENT SUMMARY: <2-3 sentences synthesizing what the pipeline "
            "needs to know, based ONLY on the MATERIAL>\n"
            "SEARCH PLAN: <2-4 items, one per line starting with '-', each "
            "naming a concrete detail/entity to find AND where in the "
            "material to look>\n"
            "EXPECTED ANSWER SHAPE: <1-2 sentences describing what the value "
            "should look like>\n\n"
            "Use ONLY terms that appear in the MATERIAL. Never invent "
            "details. Never repeat the GOAL verbatim.\n\n"
            f"Material:\n{str(intent or '')[:2200]}\n\n"
            "Plan:"
        )
        out = _strip_think(
            self._ff_llm_call(prompt, system, plan_cfg, stop_flag)
        )
        return (out or "").strip() or None

    def _ff_synthesize(self, label, intent, facts, evidence, cfg, stop_flag):
        """Final synthesis for one field (the LLM node's proven step).

        ONE coherent answer composed from ALL the accumulated facts and the
        VERBATIM evidence - the step that lets the LLM node's ComoRAG keep
        answering from messy context.  The evidence is the only ground
        truth: a fact that conflicts with it is ignored, exact values are
        copied character-for-character, and a field the evidence does not
        answer is reported as ``NOT IN EVIDENCE`` rather than paraphrased
        into a mangled URL or date.  Tries the FULL material first and
        shrinks only when the engine rejects the prompt.  The result is a
        CANDIDATE - the caller ships it AFTER the verbatim excerpts, so it
        can never override them.
        """
        synth_cfg = dict(cfg)
        synth_cfg["max_tokens"] = 512
        system = (
            "You compose ONE grounded answer for a single form field, using "
            "ONLY the evidence. Never invent a value."
        )
        _facts = str(facts or "")
        _evidence = str(evidence or "")
        for _cap in (None, 4000, 2000, 1000):
            _facts_part = _facts if _cap is None else _facts[-_cap:]
            _ev_part = _evidence if _cap is None else _evidence[-_cap:]
            prompt = (
                "You are composing the answer for ONE form field. The "
                "EVIDENCE below is VERBATIM text from the source documents - "
                "it is the only ground truth. In one or two sentences, say "
                "what the evidence holds for the field and give the value "
                "EXACTLY as it appears (copy a URL, email, phone number, "
                "date, number or name CHARACTER-FOR-CHARACTER). Ignore any "
                "fact that conflicts with the evidence or names something "
                "the evidence does not contain. If the evidence holds no "
                "value for the field, reply exactly: NOT IN EVIDENCE\n\n"
                f"Form field:\n{label}\n\n"
                f"Task: {cfg['instruction']}\n\n"
                f"Intent plan:\n{str(intent or '')[:1200]}\n\n"
                f"Curated facts (validate against the evidence):\n"
                f"{_facts_part}\n\n"
                f"Evidence (verbatim source text):\n{_ev_part}\n\n"
                "Answer:"
            )
            out = _strip_think(
                self._ff_llm_call(prompt, system, synth_cfg, stop_flag)
            )
            out = (out or "").strip()
            if out:
                if out.lower().startswith("not in evidence"):
                    # An absence is not an answer: never hand the extractor a
                    # statement telling it the field is unanswerable.
                    return None
                self._ff_last_synth = out[:600]
                return out[:600]
            if self._ff_halt(stop_flag):
                return None
        return None

    def _ff_probe_question(self, label, guidance, facts_text, cfg, stop_flag):
        """Probe GENERATOR for one field (mirrors the LLM node's).

        The consolidator asks this for the NEXT cycle's question.  Without a
        generator the engine falls back to probing whole DOCUMENT SECTIONS -
        which is exactly what drags unrelated material into the field's fact
        pool.  The question must stay on the field and use only terms from the
        material it is shown.
        """
        probe_cfg = dict(cfg)
        probe_cfg["max_tokens"] = 384
        system = (
            "You write retrieval questions that find the material for a "
            "single form field. Use ONLY terms that appear in the material."
        )
        prompt = (
            f"Goal: fill the form field '{label}'.\n"
            f"Task: {cfg['instruction']}\n\n"
            "FACTS ALREADY FOUND (do not re-ask these):\n"
            f"{str(facts_text or '')[:1200]}\n\n"
            f"{str(guidance or '')[:3500]}\n\n"
            f"Write up to 3 DIFFERENT questions that together retrieve the "
            f"value OF THIS FIELD ('{label}'). Each question must target a "
            "DIFFERENT detail or entity of that field and must never rephrase "
            "another — each one is a separate search path. A question about a "
            "DIFFERENT field wastes the whole cycle. If the material carries "
            "an INTENT PLAN, cover its SEARCH PLAN items that are NOT yet "
            "covered, one question each. Use ONLY terms that appear in the "
            "material above; never mention sections, headings or document "
            "structure. If the material has already answered the field, ask "
            "for the remaining details it is still missing.\n\n"
            f"Question about '{label}' (one per line):"
        )
        out = _strip_think(
            self._ff_llm_call(prompt, system, probe_cfg, stop_flag)
        )
        # ComoRAG entity-priority probing: keep EVERY question line (up to 3),
        # not just the first — one cycle then sweeps several distinct details
        # of the field instead of a single narrow slice.
        probes = []
        for line in (out or "").splitlines():
            line = line.strip()
            if line.endswith("?") and line[:200] not in probes:
                probes.append(line[:200])
            if len(probes) >= 3:
                break
        if probes:
            return "\n".join(probes)
        first = next(
            (ln.strip() for ln in (out or "").splitlines() if ln.strip()), ""
        )
        return first[:200] or None

    def _ff_field_judgment(self, label, facts_text, evidence_text, cfg,
                           stop_flag):
        """Sufficiency JUDGE for one field (ComoRAG meta-loop gate).

        Mirrors the LLM node's judge: after a cycle's facts it says whether the
        field is answerable yet (stopping the loop BEFORE the remaining cycles
        sweep unrelated material into this field's fact pool) or names the one
        detail still missing (the next probe then targets exactly that gap).
        Returns the raw verdict line for the engine to parse.
        """
        judge_cfg = dict(cfg)
        judge_cfg["max_tokens"] = 384
        system = (
            "You are the stop-judge of a context search pipeline that answers "
            "ONE form field."
        )
        prompt = (
            "Decide whether the material below is ENOUGH to answer the form "
            "field.\n"
            "Reply with exactly ONE line:\n"
            "ANSWER: <the value for the field> - when the material supports a "
            "confident answer\n"
            "MISSING: <the single most important detail still missing> - when "
            "it does not\n"
            "A value that is ABSENT from the material is NOT an answer: never "
            "reply 'not provided' / 'not stated' / 'unknown' - use MISSING "
            "instead, so the sweep keeps looking for the real value.\n"
            "Never add anything else.\n\n"
            f"FORM FIELD:\n{label}\n\n"
            f"Task: {cfg['instruction']}\n\n"
            f"FACTS FOUND SO FAR:\n{str(facts_text or '')[-1500:]}\n\n"
            f"EVIDENCE (verbatim source text):\n"
            f"{str(evidence_text or '')[-2000:]}\n\n"
            "Verdict:"
        )
        out = _strip_think(
            self._ff_llm_call(prompt, system, judge_cfg, stop_flag)
        )
        return (out or "").strip() or None

    def _ff_probe_consolidate(self, label, probe, cfg, source_text, documents,
                             stop_flag=None):
        """Multi-cycle ComoRAG consolidation for one field.
        Wires the SAME hooks the LLM node wires: an INTENT PLANNER
        (what to look for), a PROBE GENERATOR (so the extra cycles ask a
        question about the FIELD instead of sweeping document sections), a
        RESPONDER that composes facts with verbatim support, a JUDGE that
        stops the loop once the field is answerable, a RELEVANCE verdict that
        purges off-topic facts at the final SYNTHESIS, and the synthesis
        itself.  The judge and the relevance verdict are LAYA-FIRST by
        default (embedded local decision engine); the field's own LLM judge
        is the fallback when the engine is unavailable (``LOOPER_LAYA=off``).

        Returns the VERBATIM excerpts first (ground truth), then the composed
        FACTS, then the synthesized answer as a labelled candidate - a
        paraphrase drops the exact values (URL, email, date, number) a form
        field usually wants, so it can never lead.  Returns "" on
        failure/nothing so the caller falls back to single-pass.
        """
        from AI.laya_hooks import relevance_or, sufficiency_or
        self._ff_last_synth = None
        doc_texts = self._ff_doc_texts(documents)
        ctx = {}
        if doc_texts:
            ctx["documents"] = doc_texts
        if source_text:
            ctx["context_nodes"] = [source_text]
        if not ctx:
            return ""
        if self._ff_halt(stop_flag):
            return ""
        try:
            from AI.comorag_engine import ProbeBasedConsolidator
        except Exception as exc:
            log_block(logger, logging.WARNING, "ComoRAG",
                      "engine unavailable: %s" % exc)
            return ""
        embed_client = None
        if cfg["engine"] == "ollama":
            emb = _FFEmbedClient(cfg["engine"], cfg["model"])
            embed_client = lambda texts: emb.embeddings(cfg["model"], texts) or []  # noqa: E731
        try:
            api_url = self._resolve_api_url()
        except Exception:
            api_url = ""
        # The consolidator's GOAL must NOT be the bare label.  Its anti-echo
        # guard rejects a probe that merely rephrases the goal, and for a form
        # field the label IS the right retrieval query - a small model answers
        # "what would find this?" by repeating the field, so the probe was
        # rejected 4x as "a near-verbatim echo" (each retry 14s of reflection)
        # and the cycle fell back to probing a RANDOM document section.
        # Framing the goal as a task sentence lets the field-derived probe pass.
        goal = "Retrieve the source details that answer the form field: %s" % (
            probe,
        )
        try:
            con = ProbeBasedConsolidator(
                api_url=api_url or "http://localhost:8000",
                # Small chunks, MEASURED on a real resume: at 1000 chars the
                # header chunk that holds the contact block (name, email,
                # phone, LinkedIn URL) embeds as a "kitchen sink" and ranks
                # BELOW the chunk that merely mentions the topic
                # (0.705 vs 0.713) - so the exact value never reached the
                # extractor and the model invented one.  At 250 chars the
                # value chunk ranks first (0.755) and the experience chunk
                # second (0.605).
                chunk_size=250,
                overlap=50,
                # The value chunk often ranks 2nd-3rd, so fewer than three
                # chunks cannot ground a field: this knob raises the window,
                # it never starves it.
                top_k=max(3, cfg["probe_top_k"]),
                char_budget=cfg["probe_char_budget"],
                max_probes=cfg["probe_cycles"],
                min_facts=1,
                probe_context_chars=cfg["probe_context_chars"],
                embed_client=embed_client,
                # Namespace the process-global chunk vector cache so vectors
                # from the other embedding backend are never reused here.
                cache_namespace="%s:%s" % (cfg["engine"], cfg["model"]),
            )
            # The responder COMPOSES a finding per cycle (mirroring the LLM
            # node) and the generator asks the NEXT cycle about the FIELD (so
            # extra cycles do not sweep random document sections).
            con.consolidate(
                prompt=goal,
                system_prompt=cfg["instruction"],
                context_sources=ctx,
                stop_flag=stop_flag,
                # The SAME five hooks the LLM node wires: a planner that
                # states what to look for, a field-driven probe generator,
                # a composer, a sufficiency judge and a final synthesis.
                intent_planner=lambda intent: self._ff_intent_plan(
                    intent, cfg, stop_flag,
                ),
                probe_generator=lambda intent, facts: self._ff_probe_question(
                    label, intent, facts, cfg, stop_flag,
                ),
                responder=lambda probe, ev: self._ff_fact_answer(
                    label, probe, ev, cfg, stop_flag,
                ),
                response_generator=lambda intent, f, ev: self._ff_synthesize(
                    label, intent, f, ev, cfg, stop_flag,
                ),
                judge=lambda goal, facts, ev: sufficiency_or(
                    # The statement is phrased as a QUESTION about the
                    # field ("This text fully answers: what is the value ...")
                    # — a retrieval instruction (the raw goal) reads poorly
                    # as a statement to verify.
                    "what is the value for the form field '%s'?" % label,
                    facts, ev,
                    fallback=lambda: self._ff_field_judgment(
                        label, facts, ev, cfg, stop_flag,
                    ),
                ),
                # Fact creation: Laya curates the composed findings of this
                # field before they enter its pool (a finding that shares a
                # focus word but answers a different detail would otherwise
                # fill the quota and steer the next probe off target).
                relevance_judge=lambda probe_text, claims: relevance_or(
                    probe_text, claims,
                ),
            )
            facts = con.facts_pool
            raw = con.evidence_pool
            # Diagnostics for the per-field table (see _ff_log_field).
            self._ff_last_facts = len(_ff_finding_lines(facts))
            self._ff_last_excerpts = len(raw)
            # BOTH pools, LABELLED, VERBATIM FIRST: the composed facts are a
            # paraphrase, and a paraphrase can INVENT an exact value - the fact
            # pool held 'linkedin.com/in/alex-doe-0000000000' where the
            # source says '...-1234567890', and leading with the facts made the
            # extractor write the invented one.  The excerpts carry the real
            # value, so they lead and the facts are demoted to a summary aid.
            parts = []
            if raw:
                parts.append(
                    "[Verbatim excerpts from the source documents - GROUND "
                    "TRUTH, all exact values live here]\n"
                    + "\n\n".join(raw)
                )
            if facts:
                findings = _ff_finding_lines(facts)
                if findings:
                    parts.append(
                        "[Distilled facts - a PARAPHRASE of the excerpts "
                        "above. Never take an exact value from here when the "
                        "excerpts disagree.]\n"
                        + "\n\n".join(findings)
                    )
            _synth = getattr(self, "_ff_last_synth", None)
            if _synth:
                # GROUNDING: the synthesis is a PARAPHRASE the caller ships
                # labelled as an answer, so it is verified here against the
                # verbatim excerpts beside it.  A term the excerpts do not spell
                # out is either an INVENTION or a correct INFERENCE the token
                # check cannot see (a count computed from dates, a faithful
                # paraphrase) - which is why good facts were dropped as "not in
                # the source ground truth".  So the verdict is DYNAMIC: an EXACT
                # value (URL / email / phone) is never inferable and stays fatal,
                # and for the rest Laya's entailment verdict decides, falling
                # back to the token verdict when the engine is unavailable (fail
                # closed, exactly as before).
                _excerpts = "\n\n".join(raw)
                _viol = _ff_grounding_violations(
                    _synth, _excerpts,
                    "%s\n%s" % (label, cfg["instruction"]),
                )
                _fatal = [t for t in _viol if _ff_exact_value_token(t)]
                if (_viol and not _fatal
                        and self._ff_synth_supported(_synth, _excerpts) is True):
                    log_block(
                        logger, logging.INFO, "ComoRAG Synthesis",
                        "kept the composed answer for '%s' - the terms the "
                        "excerpts do not spell out (%s) are INFERRED from them"
                        % (label, ", ".join(_viol[:6])),
                    )
                    _viol = []
                if _viol:
                    log_block(
                        logger, logging.WARNING, "ComoRAG Synthesis",
                        "discarded the composed answer for '%s' - it asserts "
                        "terms absent from the excerpts (%s)"
                        % (label, ", ".join(_viol[:6])),
                    )
                    self._ff_last_synth = None
                else:
                    # LAST, never first: a synthesis is still a paraphrase, and
                    # one that leads the evidence is what made the extractor
                    # write an invented URL.  The excerpts above stay the only
                    # ground truth for exact values.
                    parts.append(
                        "[Composed answer for this field - a PARAPHRASE of the "
                        "excerpts above. Verify every exact value against them; "
                        "the excerpts win on any disagreement.]\n" + _synth
                    )
        except Exception as exc:
            log_block(logger, logging.WARNING, "ComoRAG",
                      "probe failed for '%s': %s" % (label, exc))
            return ""
        if not parts:
            self._ff_last_route = "single-pass (no ComoRAG result)"
            return ""
        return "\n\n".join(parts)

    def _ff_probe(self, label, cfg, source_text, documents=None, stop_flag=None,
                  repair_hint=""):
        """Retrieve the material for ``label`` - facts + verbatim excerpts.

        The field label (+ instruction) is the goal.  With ``consolidate`` on
        and ``probe_cycles`` > 1 the iterative ComoRAG engine sweeps the corpus
        over several cycles and returns the composed FACTS plus the VERBATIM
        excerpts they came from (an exact value survives only in the excerpt).
        Otherwise - or when it returns nothing - the single-pass embedding
        retrieval supplies the raw slice.
        ``repair_hint`` (a rejection message) is folded into the probe so a
        repair run retrieves for the FORMAT the field actually needs.
        Returns "" when there is nothing to ground on (the field is then
        SKIPped, or answered No for a yes/no question).
        """
        if not label or (not source_text and not documents):
            return ""
        if self._ff_halt(stop_flag):
            return ""
        # The probe is the field's question text; on a repair run the page's
        # rejection feedback rides along so the retrieval targets the format.
        probe = label if not repair_hint else (label + "\n" + repair_hint)

        # 0. FACTS-FIRST (ComoRAG, mirroring the LLM node): the consolidator
        # sweeps several probes and returns the COMPOSED facts only.  Raw
        # source text is the FALLBACK below, never the primary input.
        if cfg["consolidate"] and cfg["probe_cycles"] > 1:
            facts = self._ff_probe_consolidate(
                label, probe, cfg, source_text, documents, stop_flag,
            )
            if facts:
                self._ff_last_route = (
                    "comoRAG: %d composed fact(s) + %d verbatim excerpt(s)"
                    % (getattr(self, "_ff_last_facts", 0),
                       getattr(self, "_ff_last_excerpts", 0))
                )
                # The source's own Q&A blocks for OTHER questions (a learned
                # answers file) must not be answered from - see the helper.
                return _ff_drop_foreign_qa(facts, label)
        else:
            # Never silent: an off toggle used to look identical to a
            # working run in the log (no consolidator lines at all).
            self._ff_last_route = "single-pass (consolidate=%s, cycles=%s)" % (
                cfg["consolidate"], cfg["probe_cycles"],
            )

        # 1. Fallback: the raw slice.  The document HEAD is added LAST, and
        # only when the retrieved slice does not ALREADY contain it - prepending
        # it unconditionally handed the model the same header TWICE (the head,
        # plus the header chunk the probe returns), which is exactly what showed
        # up as duplicated evidence on every field.
        head = (self._ff_doc_head(documents, cfg["probe_char_budget"])
                if documents else "")
        parts = []

        try:
            from ....llm_executor_resources.rag_utils import (
                build_rag_context, build_rag_context_from_docs,
            )
        except Exception as exc:
            log_block(logger, logging.WARNING, "Form Probe",
                      "RAG utils unavailable: %s" % exc)
            return ("[Source document start]:\n" + head) if head else ""
        client = _FFEmbedClient(cfg["engine"], cfg["model"])

        # 1. Attached documents (the primary source; probed via ComoRAG).
        if documents:
            try:
                doc_ctx = build_rag_context_from_docs(
                    client=client,
                    prompt=probe,
                    documents=list(documents),
                    embedding_model=cfg["model"],
                    chunk_size=1000,
                    overlap=200,
                    top_k=cfg["probe_top_k"],
                    max_chars=cfg["probe_char_budget"],
                )
                if doc_ctx:
                    parts.append(doc_ctx)
            except Exception as exc:
                log_block(logger, logging.WARNING, "Form Probe",
                          "document probe failed for '%s': %s" % (label, exc))
        # 2. Upstream context text (LLM/Context/Input node outputs).
        if source_text:
            try:
                rag_text, _updated, _meta = build_rag_context(
                    client=client,
                    prompt=probe,
                    input_text=source_text,
                    embedding_model=cfg["model"],
                    chunk_size=1000,
                    overlap=200,
                    top_k=cfg["probe_top_k"],
                    include_raw_input=False,
                    max_chars=cfg["probe_char_budget"],
                    input_source="form_field",
                    use_input_text_in_prompt=True,
                )
                if rag_text:
                    parts.append(rag_text)
            except Exception as exc:
                log_block(logger, logging.WARNING, "Form Probe",
                          "context probe failed for '%s': %s" % (label, exc))
        if head and head[:120] not in "\n\n".join(parts):
            parts.insert(0, "[Source document start]:\n" + head)
        return _ff_drop_foreign_qa("\n\n".join(parts), label)

    def _ff_stop_requested(self, stop_flag):
        """True when the user pressed ESC (the shared keyboard monitor flag)."""
        try:
            return bool(stop_flag and stop_flag())
        except Exception:
            return False

    def _ff_halt(self, stop_flag):
        """Sticky "stop this node NOW" state (ESC).

        The consolidator SWALLOWS an exception raised inside one of its hooks
        (it records "no result" and keeps sweeping), so a stop cannot be
        propagated by raising: it is latched here and every step consults it.
        Latched by ``_ff_llm_call`` (the single funnel for every model call)
        and checked by the probe, the extract, the write and the field loop, so
        ESC no longer waits for a whole field (probe generation + extract +
        judge + repair) to finish before it does anything.
        """
        if self._ff_stop_requested(stop_flag):
            self._ff_abort = True
        return bool(getattr(self, "_ff_abort", False))
