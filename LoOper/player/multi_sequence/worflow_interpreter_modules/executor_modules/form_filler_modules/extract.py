"""One value for one field: the extraction prompt and the engine calls."""

import logging
import re

from .common import (
    _FF_NA_VALUE,
    _as_int,
    _ff_fit_length,
    _ff_na_value,
    _ff_norm,
    _ff_option_exact,
    _ff_option_match,
    _ff_value_rejection,
    _strip_think,
    log_block,
    logger,
)


class FormFillerExtractMixin:
    """One value for one field: the extraction prompt and the engine calls."""

    def _ff_extract(self, label, evidence, cfg, stop_flag, retry_note="",
                    options=None, multiline=False, format_hint="",
                    max_chars=0, kind=""):
        """Ask the model for ONE value for ONE field, grounded on ``evidence``.

        ``options`` (a choice group) pins the answer to one of the allowed
        labels.  ``multiline`` keeps the whole reply (a textarea / free-text
        question wants the longer answer, not just its first line).  With
        ``answer_no`` on, a yes/no question the evidence cannot support a "Yes"
        for is answered NO (a real value).  ``max_chars`` is the field's own
        character limit (0 = none): an over-long answer is fitted to it, since
        the page can never accept it.  ``kind`` is the enumerated control
        family (switch / combo / choice / select / text): a non-native widget
        has no option list for a text answer to hit, so the model is told what
        the control IS.

        The model is never asked to SKIP: leaving a field alone is the
        HARNESS's decision (``_execute_form_filling_node`` skips only a field
        that already holds a value), so the prompt always demands a value.  A
        stray skip-word reply is still caught below and never typed into a
        field.
        """
        opts = [str(o).strip() for o in (options or []) if str(o).strip()]
        if self._ff_halt(stop_flag):
            return ""
        system = (
            "You fill ONE form field at a time from the user's source "
            "documents. Return ONLY the value to enter in the field - no "
            "explanation, no quotes, no field name. Derive the value from the "
            "evidence when it can be reasonably inferred (for example total "
            "years from the employment dates, or a short summary of the stated "
            "experience). A field may ask more than one QUESTION (e.g. "
            "'salary and earliest availability'): answer every question it "
            "asks, on the same line, separated by commas. If it asks for a "
            "NUMBER of years or months, count it from the dates in the "
            "evidence (earliest relevant start to the latest end, or to now), "
            "and answer with the NUMBER alone - no unit, no sentence. "
            "A list answer holds ONLY the items the evidence names - never "
            "extend or complete a list with anything the evidence does not "
            "contain, and never guess a plausible-looking value. "
            "The evidence may hold both distilled facts (a PARAPHRASE, "
            "labelled as such) and VERBATIM source excerpts (the ground "
            "truth): for an exact value - a URL, an email, a phone number, a "
            "date, a number, a name - copy it CHARACTER-FOR-CHARACTER from the "
            "verbatim excerpts, and when a distilled fact and an excerpt "
            "disagree the EXCERPT wins. Use the distilled facts only for "
            "summaries. "
            "Never write placeholder text such as [Company] or [Your Name], "
            "and never COPY a bracketed marker out of the evidence: the source "
            "may itself contain template text (for example '...help [Company] "
            "with [specific goal]'), so drop the marker and REWRITE that part "
            "in your own words instead of pasting it through. If a name is not "
            "in the evidence, phrase the answer so it is not needed. "
            "Return the value for the field. Never invent, complete or "
            "reconstruct a value to avoid an empty field: a plausible-looking "
            "answer (an example.com URL built out of a name, a guessed date, "
            "a borrowed fact about something else) is worse than none, and it "
            "is what a wrong value in the page looks like afterwards. Answer "
            "with what the evidence actually holds, and when the field asks "
            "for something the evidence does not carry (links where it only "
            "describes the projects, a referrer's email where it names nobody) "
            "answer with the evidence's own content for that field instead of "
            "inventing the missing form. Choosing to leave a field "
            "alone is the harness's job, never yours, so never reply with the "
            "sentinel word SKIP."
        )
        if opts:
            system += (
                " This field is a choice: answer with EXACTLY ONE of the "
                "allowed options, copied verbatim."
            )
        if multiline:
            system += (
                " This field expects a longer answer: write the complete "
                "answer in one or more sentences, drawn only from the evidence."
            )
        # A non-native control has no free-text target: the answer must fit what
        # the widget can actually hold, or the write bounces (observed: a toggle
        # and an autocomplete dropdown answered like a text field).
        if str(kind or "").lower() == "switch":
            system += (
                " This control is an ON/OFF toggle: answer YES when the "
                "evidence supports turning it on, otherwise NO."
            )
        elif str(kind or "").lower() == "combo":
            system += (
                " This control is a search/autocomplete dropdown: the answer "
                "must be one of its listed options."
            )
        if cfg.get("answer_no"):
            system += (
                " If the field is a yes/no question and the evidence does not "
                "support 'Yes', return NO."
            )
        # LAST RESORT: the model - not a keyword - judges unanswerable, and only
        # for a field nothing else could answer.  The rule is deliberately
        # narrow, so a thin-but-real source is still answered.
        if cfg.get("answer_na"):
            system += (
                " A field that genuinely CANNOT be answered must be answered "
                "with exactly N/A: the control cannot hold what the question "
                "asks for (a text box asking for a PHOTO or a FILE), every "
                "allowed option is off-topic for the question, or the evidence "
                "holds NOTHING about it and no value can be derived. N/A is a "
                "LAST RESORT, never a shortcut - a value you can derive or "
                "reasonably infer MUST be answered, and thin evidence is not a "
                "reason to refuse. An invented value is not an answer either: "
                "when the evidence holds no value for the field and none can "
                "be derived from it, reply exactly N/A instead of guessing "
                "one."
            )
        options_line = ""
        if opts:
            options_line = "Allowed options (choose one): " + " | ".join(opts) + "\n"
        format_line = ""
        if format_hint:
            format_line = "Required format: " + str(format_hint) + "\n"

        def _build(ev, note=None):
            return (
                f"Field: {label}\n"
                f"{options_line}"
                f"{format_line}"
                f"Task: {cfg['instruction']}\n"
                f"Evidence:\n{ev}\n\n"
                f"{retry_note if note is None else note}"
                f"Value for '{label}':"
            )

        raw = self._ff_llm_call(_build(evidence), system, cfg, stop_flag)
        if not (raw or "").strip():
            # A stop during the generation must not be answered with a second
            # generation - that is exactly what made ESC look ignored.
            if self._ff_halt(stop_flag):
                return ""
            # An empty reply is usually one of two things: the PROMPT overflowed
            # the engine's context (observed: n_ctx 512 against a 754-token
            # prompt -> HTTP 400 exceed_context_size_error), or a reasoning model
            # spent the whole generation budget.  One retry with a SHRUNK
            # evidence slice (and a slightly larger budget) - then give up,
            # never fabricate.
            ev = str(evidence or "")
            limit = max(400, int(len(ev) * 0.4))
            ev = ev[:limit] if len(ev) > limit else ev
            bigger = dict(cfg)
            # Doubling is the point of the retry, so the ceiling must stay above
            # the default budget or the "bigger" attempt is a no-op.
            bigger["max_tokens"] = min(
                max(512, _as_int(cfg.get("max_tokens"), 1024) * 2), 2048
            )
            log_block(
                logger, logging.WARNING, "Form Extract: %s" % label,
                "empty reply - retrying with a shrunk evidence slice "
                "(%d -> %d chars, max_tokens=%d, ctx=%s)"
                % (len(str(evidence or "")), len(ev), bigger["max_tokens"],
                   cfg.get("context_size") or "auto"),
            )
            raw = self._ff_llm_call(_build(ev), system, bigger, stop_flag)
        # Diagnostics for the per-field table (see _ff_log_field).
        self._ff_last_raw = str(raw) if raw is not None else ""
        value = _strip_think(raw)
        lines = [ln.strip() for ln in value.splitlines() if ln.strip()]
        # A single-line field takes the first line (a small model appends a
        # trailing note); a textarea keeps every line.  A CHOICE keeps every
        # line too: a small model often puts the QUESTION on line 1 and the
        # answer below, so line 1 is a question ECHO - taking it handed the
        # choice rail the echo and threw the real content away (observed: a
        # cloud-experience answer sat on line 2 and was lost).
        value = ("\n".join(lines) if (multiline or opts)
                 else (lines[0] if lines else ""))
        value = value.strip().strip('"').strip("'").strip()
        # Models slip the skip word in regardless of the prompt - it is the
        # HARNESS that decides a field is left alone, so a stray sentinel must
        # never be written into the field as its value.  Normalise brackets /
        # colon / punctuation before the token test, else '[Skip]' is treated as
        # a real value.
        norm = value.lower().strip('[](){}<>"\' .!:,;')
        first_word = norm.split(None, 1)[0].strip('[](){}:,.!') if norm else ""
        # An explicit N/A is the model's UNANSWERABLE verdict - a REAL value the
        # page RECEIVES, not the silent skip the harness uses for a field it
        # leaves alone.  Matched against the ONE literal the prompt itself
        # names (not a list of synonyms - a synonym list is a language).
        if norm == _FF_NA_VALUE.lower():
            if not cfg.get("answer_na"):
                return ""
            if opts:
                # A list-backed control can only HOLD one of its own options, so
                # "cannot be answered" is not a value to write here.  The
                # OPTION comes from the page's own list via the choice rail -
                # it pins the model to the real options (a proficiency select
                # is then answered 'None') instead of the harness recognising
                # which option WORD means "none".
                return self._ff_choice_answer(
                    label, value, evidence, cfg, stop_flag, opts, _build, system,
                    kind=kind,
                )
            # The model must not reach for N/A while it holds evidence: the
            # field is then usually answerable, and N/A is the one answer the
            # repair pass never revisits (observed: 'How many years of work
            # experience do you have with Data Engineering?' written 'N/A' with
            # ten excerpts of evidence beside it).  ONE confirmation against
            # that evidence - a genuine "the evidence says nothing about this"
            # still ends in N/A.
            if (not retry_note and str(evidence or "").strip()
                    and not self._ff_halt(stop_flag)):
                return self._ff_extract(
                    label, evidence, cfg, stop_flag,
                    retry_note=(
                        "You answered N/A, but the Evidence DOES hold "
                        "material about this field. Answer from it - a value "
                        "the Evidence supports, or one derived from it (a "
                        "count of years from the dates). Reply exactly N/A "
                        "only if the Evidence truly holds nothing about this "
                        "field.\n"
                    ),
                    options=options, multiline=multiline,
                    format_hint=format_hint, max_chars=max_chars, kind=kind,
                )
            return _ff_na_value()
        if (not norm or norm == self._SKIP_TOKEN
                or first_word == self._SKIP_TOKEN):
            return ""
        # A choice field can only be answered with one of its OWN options: the
        # page holds one control per option and nothing else.  The returned
        # option LABEL is the page's own text, so the invented-identifier guard
        # below must not run on it - it compares against the SOURCE documents.
        if opts:
            return self._ff_choice_answer(
                label, value, evidence, cfg, stop_flag, opts, _build, system,
                kind=kind,
            )
        if max_chars and len(value) > max_chars:
            # The page can never accept an over-long value, so fit it here
            # instead of letting the page reject it on every attempt.
            fitted = _ff_fit_length(value, max_chars)
            log_block(
                logger, logging.INFO, "Form Extract: %s" % label,
                "shortened the answer to the field's %d-character limit "
                "(%d -> %d chars): %r"
                % (max_chars, len(value), len(fitted), fitted),
            )
            value = fitted
        # A fabricated answer is worse than an empty field: an identifier the
        # source does not contain, or a value of the wrong KIND for the label,
        # is dropped here so the harness SKIPs the field instead of writing it
        # (observed: an invented LinkedIn URL, and a phone number written into
        # 'Postal' - both accepted by the page, so the repair pass is too late).
        reject = _ff_value_rejection(label, value, evidence)
        if reject:
            log_block(
                logger, logging.WARNING, "Form Extract: %s" % label,
                "discarded a value the source cannot support (%s): %r"
                % (reject, value),
            )
            if retry_note or self._ff_halt(stop_flag):
                # Bounded: the retry below always carries a note, so a second
                # discard ends here.  The reason is left for the caller's N/A
                # note - a discard is NOT "the model grounded nothing".
                self._ff_last_reject = reject
                return ""
            # A DISCARDED value is not "nothing was retrieved": the evidence is
            # right there, so the field IS answerable and the value was only
            # refused as fabricated.  Falling straight through to the
            # last-resort N/A punished a field the source could fill - observed:
            # a required 'share links to 3-5 applications' textarea answered
            # with invented example.com links was written 'N/A' while six
            # verbatim excerpts about those very applications sat beside it.
            # ONE corrected attempt, told WHY the answer was refused (an
            # ungrateful field still says N/A, per the prompt rule).
            return self._ff_extract(
                label, evidence, cfg, stop_flag,
                retry_note=(
                    "Your previous answer %r was REJECTED: %s. Answer with "
                    "what the Evidence ACTUALLY holds for this field, even "
                    "when the field asks for something more specific than the "
                    "Evidence carries: a field asking for LINKS is answered "
                    "by the applications the Evidence names (never an invented "
                    "URL), and a field asking WHO referred you is answered "
                    "from the people the Evidence names. Answer exactly N/A "
                    "only when the Evidence holds NOTHING about the field.\n"
                    % (value, reject)
                ),
                options=options, multiline=multiline,
                format_hint=format_hint, max_chars=max_chars, kind=kind,
            )
        return value

    @staticmethod
    def _ff_source_slice(evidence, value, limit=220):
        """The SOURCE's OWN phrasing of the answer - the line / sentence that
        carries it - so the option scorer reads the COMPLETE value the source
        states (the full 'City, Province, Country'), not the model's lossy
        paraphrase.  Falls back to the head of the evidence, then ''."""
        txt = str(evidence or "")
        if not txt:
            return ""
        key = _ff_norm(str(value or "").split(",")[0]).strip()
        if key:
            for seg in re.split(r"\r?\n|(?<=[.!?])\s+", txt):
                if key in _ff_norm(seg):
                    return seg.strip()[:limit]
        return txt.strip()[:limit]

    def _ff_choice_answer(self, label, value, evidence, cfg, stop_flag,
                          opts, build, system, kind=""):
        """Force a choice answer onto one of the field's options ('' if none).

        A choice field can only HOLD one of the page's OWN options, and the
        model usually answers a choice in PROSE ("I have strong professional
        experience with Python (including Django, FastAPI...)") that names no
        option literally.  The rails, in order:

        1. the answer IS an option VERBATIM - used as-is;
        2. LAYA picks the option the answer MEANS (``laya_hooks.choose_option``,
           the same semantic picker the Handle node uses).  When the engine
           ANSWERS but no option wins clearly, the field is left to the
           harness, which ASKS - a blind second guess is what pinned a keyword
           soup to whichever option shared a word (observed: a cloud-experience
           question the evidence answered "no" was filled with the MOST SENIOR
           option, "implementé y desplegué soluciones completas");
        3. the engine-OFF/DOWN fallback: the string rail, then ONE bounded
           re-ask pinned to the option list - a loop here would BE the infinite
           retry this rail exists to remove.

        An answer no rail can place is dropped (the harness skips the field)
        rather than written as a selection the page does not offer.
        """
        # 1. Verbatim echo: the model already answered with an option.
        if _ff_option_exact(value, opts):
            return _ff_option_match(value, opts)
        if self._ff_halt(stop_flag):
            return ""
        # 2. SEMANTIC pick (Laya), ahead of the containment rail.
        from AI.laya_hooks import choose_option
        _idx, _engine_answered = choose_option(label, value, opts)
        if _idx is not None and 0 <= _idx < len(opts):
            log_block(
                logger, logging.INFO, "Form Extract: %s" % label,
                "Laya matched the answer %r to the option %r"
                % (value, opts[_idx]),
            )
            return opts[_idx]
        if _engine_answered:
            # Laya ANSWERED but the model's VALUE could not be placed.  That
            # value is usually a LOSSY paraphrase ("Buenos Aires" for the
            # source's complete "Buenos Aires, Buenos Aires Province,
            # Argentina"), which cannot separate two close options.  So read the
            # SOURCE the node was configured with and score EVERY option against
            # it, taking the MOST ACCURATE - the city over the province when the
            # source spells out both.  Only if the source places no option is the
            # field left to the harness (live: the 'Location (city)*' combo never
            # landed because the model-answer rail refused on every pass and the
            # exact-only combo rule then refilled it forever).
            from AI.laya_hooks import choose_option_by_source
            _slice = self._ff_source_slice(evidence, value)
            _sidx, _s_answered = choose_option_by_source(label, _slice, opts)
            if _sidx is not None and 0 <= _sidx < len(opts):
                log_block(
                    logger, logging.INFO, "Form Extract: %s" % label,
                    "the source %r picks the option %r"
                    % (_slice[:80], opts[_sidx]),
                )
                return opts[_sidx]
            log_block(
                logger, logging.INFO, "Form Extract: %s" % label,
                "no option clearly matches %r - left to the user" % value,
            )
            return ""
        # 3. FALLBACK (engine OFF / DOWN): the string rail, then ONE pinned re-ask.
        hit = _ff_option_match(value, opts)
        if hit:
            return hit
        note = (
            "Your previous answer %r is NOT one of the allowed options and the "
            "page has no control for it. Reply with EXACTLY ONE of these, "
            "copied verbatim and nothing else:\n%s\n"
            % (value, "\n".join("- " + o for o in opts))
        )
        raw = self._ff_llm_call(build(evidence, note), system, cfg, stop_flag)
        lines = [ln.strip() for ln in _strip_think(raw).splitlines() if ln.strip()]
        w2 = lines[0] if lines else ""
        hit = _ff_option_match(w2, opts)
        if not hit:
            log_block(
                logger, logging.WARNING, "Form Extract: %s" % label,
                "the answer %r is not one of this field's options (%s) and no "
                "closer one could be chosen - leaving the field alone"
                % (value, " | ".join(opts)),
            )
            return ""
        log_block(
            logger, logging.INFO, "Form Extract: %s" % label,
            "the off-list answer %r was mapped to the option %r"
            % (value, hit),
        )
        return hit

    def _ff_llm_call(self, prompt, system, cfg, stop_flag):
        """One text completion via the node's configured engine.

        The shared llamacpp helper is called with ``log_calls=False``: this
        node renders its own boxy per-field view, so the plain ``[HANDLE]``
        call/response lines would only split that view apart.
        """
        if cfg["engine"] == "ollama":
            if self._ff_halt(stop_flag):
                return ""
            return self._ff_ollama_call(prompt, system, cfg)
        if self._ff_halt(stop_flag):
            return ""
        try:
            api_url = self._resolve_api_url()
            return self._call_llamacpp_api(
                api_url=api_url,
                model=cfg["model"],
                prompt=prompt,
                system=system,
                temperature=cfg["temperature"],
                max_tokens=cfg["max_tokens"],
                context_size=cfg.get("context_size") or 0,
                log_calls=False,
                # One round only: every form-filler call asks for a short
                # structured reply (a value, a question, a verdict), and
                # continuing a rambling small model can run for minutes.
                max_rounds=1,
            ) or ""
        except Exception as exc:
            log_block(logger, logging.ERROR, "Form LLM Call",
                      "call failed: %s" % exc)
            return ""

    def _ff_ollama_call(self, prompt, system, cfg):
        try:
            import os
            import requests
            host = os.getenv("OLLAMA_HOST", "localhost")
            port = os.getenv("OLLAMA_PORT", "11434")
            model = cfg["model"]
            if not model:
                log_block(logger, logging.WARNING, "Form Ollama Call",
                          "the engine requires an explicit model")
                return ""
            resp = requests.post(
                f"http://{host}:{port}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "system": system,
                    "stream": False,
                    # Extraction wants a direct short answer.  With a reasoning
                    # model and a small num_predict the thinking channel eats
                    # the whole budget and ``response`` comes back EMPTY (which
                    # surfaced as a silent SKIP).  Disable thinking so the
                    # budget is spent on the value itself.
                    "think": False,
                    "options": {
                        "temperature": cfg["temperature"],
                        "num_predict": cfg["max_tokens"],
                    },
                },
                timeout=None,
            )
            if resp.status_code == 200:
                out = (resp.json() or {}).get("response", "") or ""
                if out.strip():
                    return out
                log_block(logger, logging.WARNING, "Form Ollama Call",
                          "the engine returned an empty reply")
            else:
                log_block(logger, logging.WARNING, "Form Ollama Call",
                          "HTTP %s" % resp.status_code)
        except Exception as exc:
            log_block(logger, logging.ERROR, "Form Ollama Call",
                      "call failed: %s" % exc)
        return ""
