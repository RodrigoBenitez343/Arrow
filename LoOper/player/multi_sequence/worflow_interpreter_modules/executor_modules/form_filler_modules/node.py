"""The node shell: the field loop, the per-field table and the output."""

import json
import logging
import re
import time

from .common import (
    _FF_LIST_KINDS,
    _FFEmbedClient,
    _as_int,
    _ff_compound_unstated,
    _ff_cos,
    _ff_dense,
    _ff_dupe_score,
    _ff_fragment_of_evidence,
    _ff_na_value,
    _ff_norm,
    _ff_option_exact,
    _ff_option_match,
    _ff_parse_learned_answers,
    _ff_plain_text,
    _ff_pool_body,
    _ff_pool_corrections,
    _ff_pool_verified,
    _ff_render_corrections,
    _ff_render_verified,
    _ff_scalarize,
    _ff_ungrounded_identifier,
    log_block,
    log_table,
    logger,
)


class FormFillerNodeMixin:
    """The node shell: the field loop, the per-field table and the output."""

    def _ff_log_field(self, field, label, cfg, status, value="", options=None,
                      read_back=None, note=""):
        """Per-field diagnostic TABLE - the boxy view of ONE fill.

        Emitted for EVERY outcome (filled / skipped / failed) with the same
        shape as the LLM node's parameter table, so a run can be analysed
        field by field: which route retrieval took, what the model actually
        replied, what was written and what the page read back.
        """
        kind = (field.get("kind") or "text").lower()
        rows = [
            ("Field", label),
            ("Control", "%s / %s" % (field.get("tag") or "input", kind)),
            ("Options", ", ".join(str(o) for o in (options or []))
             or "(free text)"),
            ("Format", self._ff_format_spec(field) or "(unconstrained)"),
            ("Retrieval route", getattr(self, "_ff_last_route", "") or "(none)"),
            ("CRAG verdict / strips", "%s / %s kept of %s scored" % (
                getattr(self, "_ff_last_action", "") or "(no verdict)",
                getattr(self, "_ff_last_kept", 0),
                getattr(self, "_ff_last_scored", 0),
            )),
            ("Raw reply", getattr(self, "_ff_last_raw", "") or "(empty)"),
            ("Value", value or "(none)"),
            ("Read-back", read_back if read_back is not None else "(not read)"),
            ("Status", status),
        ]
        if note:
            rows.append(("Note", note))
        level = (logging.WARNING if status in ("skipped", "failed")
                 else logging.INFO)
        log_table(logger, level, "Form Field: %s" % label, rows)

    def _ff_negative_option(self, label, options):
        """The option that asserts the NEGATIVE, when nothing grounds a choice.

        "No evidence -> no", decided by MEANING: Laya scores the page's own
        options (measured: No 0.94 against Yes 0.71 and Sí 0.27), so no word
        list is involved.  Returns '' when no option reads as the negative, and
        the caller then asks / writes N-A instead of inventing a pick.
        """
        opts = [str(o) for o in (options or []) if str(o).strip()]
        if not opts:
            return ""
        try:
            from AI.laya_hooks import decide_choice
            idx, why = decide_choice(label, "", opts, material_states=False)
        except Exception as exc:  # noqa: BLE001 - a fault must not guess
            if self._ff_laya_fault(exc):
                raise
            return ""
        if idx is not None and 0 <= idx < len(opts):
            log_block(
                logger, logging.INFO, "Form Field: %s" % label,
                "nothing grounds this field - the negative option %r (%s)"
                % (opts[idx], why),
            )
            return opts[idx]
        return ""

    @staticmethod
    def _ff_choice_landed(read_back, value, options):
        """True when the read-back PROVES the answered option was selected.

        A choice reads back as the label of the CHECKED option, so it can be
        compared with the option the answer named: a click that resolved to a
        different control - the page holds several with the same label - leaves
        the field wrong while a non-empty read-back looks like success.  A lone
        boolean control reports 'on'/'off'/'checked'/'unchecked' instead of an
        option label, and those states always count as landed.
        """
        if not options or not str(value or "").strip():
            return True
        text = str(read_back or "").strip()
        if text.lower() in ("on", "off", "checked", "unchecked"):
            return True
        return bool(_ff_option_match(text, [value]))

    # --- mode-aware rails: the WEB twin drives the page's DOM, the DESKTOP
    # twin drives Windows UI Automation.  The field loop above, the verify rail
    # and the repair pass talk to these three names only, so one code path
    # serves both substrates.

    def _ff_mode(self):
        return str((getattr(self, "_ff_pass_cfg", None) or {}).get("mode")
                   or "web").strip().lower()

    def _ff_write(self, field, value, stop_flag, driver=None):
        """Write one value into one field (substrate picked by the mode)."""
        if self._ff_mode() == "desktop":
            return self._ff_write_desktop(field, value, stop_flag, driver)
        return self._ff_write_web(field, value, stop_flag, driver)

    def _ff_read(self, field, stop_flag, driver=None):
        """Read one field back; None when the control cannot be read."""
        if self._ff_mode() == "desktop":
            return self._ff_read_desktop(field, stop_flag, driver)
        return self._ff_read_web(field, stop_flag, driver)

    def _ff_state(self, field, stop_flag, driver=None):
        """One field's {found, value, valid, message} for the repair pass."""
        if self._ff_mode() == "desktop":
            return self._ff_state_desktop(field, stop_flag, driver)
        return self._ff_field_state(field, stop_flag, driver)

    @staticmethod
    def _ff_prior_from_pool(text):
        """Per-field outcomes from a PRIOR pass, carried by the context pool.

        The node's own output is a JSON summary ``{"mode": ..., "fields":
        [{"label": ..., "status": ...}, ...]}``; when the chain wires
        ``ctx_out`` back through a Context node, that summary arrives inside the
        upstream text on the next entry.  Find the first such summary and map
        ``label -> status`` so an already-handled field is not re-processed.
        Returns ``{}`` when the pool carries none.
        """
        s = str(text or "")
        if '"fields"' not in s:
            return {}
        import json as _json
        for m in re.finditer(r"\{", s):
            i = m.start()
            if '"fields"' not in s[i:i + 300]:
                continue
            depth = 0
            for j in range(i, len(s)):
                c = s[j]
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            obj = _json.loads(s[i:j + 1])
                        except Exception:
                            obj = None
                        if isinstance(obj, dict) and isinstance(
                                obj.get("fields"), list):
                            out = {}
                            for f in obj["fields"]:
                                if isinstance(f, dict) and f.get("label"):
                                    out[str(f["label"])] = str(
                                        f.get("status") or "")
                            return out
                        break
        return {}

    # Cosine floor for the SEMANTIC learned-fact gate (see _ff_learned_match):
    # a stored fact whose LABEL embedding is this close to the field's counts
    # as the same question.  Tuned conservatively - raise it if the gate ever
    # reuses a subtly-different question's answer.
    _FF_FACT_GATE = 0.82

    def _ff_fact_index(self, learned_answers, cfg):
        """The pool's learned facts as ``[{label, value, vec}]``.

        Built ONCE per pass so the stored labels are embedded a single time,
        not once per field.  Empty when embeddings are unavailable, in which
        case only the EXACT label match applies.
        """
        cached = getattr(self, "_ff_fact_index_cache", None)
        if cached is not None:
            return cached
        idx = []
        items = [(k, str(v)) for k, v in (learned_answers or {}).items()
                 if str(v).strip()]
        if items:
            try:
                emb = _FFEmbedClient(cfg.get("engine"), cfg.get("model"))
                vecs = emb.embeddings(cfg.get("model") or "",
                                      [k for k, _ in items])
                if vecs and len(vecs) == len(items):
                    idx = [{"label": k, "value": v, "vec": vec}
                           for (k, v), vec in zip(items, vecs)]
            except Exception as exc:
                logger.debug("[FORM] learned-fact index unavailable: %s", exc)
        self._ff_fact_index_cache = idx
        return idx

    def _ff_learned_match(self, label, learned_answers, cfg, manual_keys=None):
        """The pool's cached value for *label*: EXACT label, else SEMANTIC gate.

        The context pool is a knowledge base of questions the system already
        tagged factually-correct (or the user answered).  A field whose question
        matches a stored one is filled MECHANICALLY from the cache - no probe,
        no ComoRAG - so a correct answer is reused across pages and runs.
        Exact normalized-label match first (free); otherwise the stored label
        whose EMBEDDING is closest to the field's (>= ``_FF_FACT_GATE``).
        ``manual_keys`` (labels the USER verified) add a WORD-OVERLAP rung
        before the embedding gate: the same question reworded or translated
        ("salary expectations" vs "remuneración bruta pretendida") misses pure
        cosine, and a hand-corrected answer must never be asked again.
        Returns ``(value, matched_label)`` or ``("", "")``.
        """
        if not label or not learned_answers:
            return "", ""
        exact = _ff_norm(label).strip("*? .:")
        if exact in learned_answers:
            self._ff_last_match_conf = 1.0
            return learned_answers[exact], exact
        manual_keys = manual_keys or set()
        if manual_keys:
            _words = {w for w in _ff_norm(label).split() if len(w) >= 4}
            if _words:
                best, best_hit = "", 0.0
                for _key in manual_keys:
                    _rec = {w for w in _key.split() if len(w) >= 4}
                    if not _rec:
                        continue
                    _hit = len(_words & _rec) / float(len(_rec))
                    if _hit > best_hit:
                        best, best_hit = _key, _hit
                if best and best_hit >= 0.6:
                    self._ff_last_match_conf = 1.0
                    return learned_answers.get(best, ""), best
        idx = self._ff_fact_index(learned_answers, cfg)
        if not idx:
            return "", ""
        try:
            emb = _FFEmbedClient(cfg.get("engine"), cfg.get("model"))
            vecs = emb.embeddings(cfg.get("model") or "", [label])
            if not vecs:
                return "", ""
            base = vecs[0]
            best, best_s = None, 0.0
            for item in idx:
                s = _ff_cos(base, item["vec"])
                if s > best_s:
                    best, best_s = item, s
            if best is not None and best_s >= self._FF_FACT_GATE:
                self._ff_last_match_conf = best_s
                return best["value"], best["label"]
        except Exception as exc:
            logger.debug("[FORM] learned-fact gate unavailable: %s", exc)
        return "", ""

    # A free-text control that only accepts a NUMBER (the learned band cannot
    # be committed to it).
    _FF_NUMERIC_TYPES = ("number", "range")

    def _ff_reuse_confidence(self, label, learned, cfg, context, manual=False):
        """Confidence (0..1) that a stored record may be reused for this field.

        Starts from the match confidence (exact label = 1.0, semantic = the
        embedding cosine).  A ``manual`` record (added/edited by the user in the
        Context node dialog) is AUTHORITATIVE: it returns 1.0 and is never put
        to the engine - the verdict that once discarded a hand-corrected answer
        was produced by the same model the user had just corrected, which is how
        a corrected field came back wrong on the next run.
        With ``confidence_source == 'laya'`` a calibrated entailment verdict is
        consulted against the POOL for an AUTO record: a verdict that the record
        does NOT answer this field drops the confidence to 0, so the record is
        not reused verbatim and a fresh answer is fabricated from the whole pool
        instead.  ``None`` (engine off) keeps the cosine verdict.
        A record the pool holds VERBATIM is reused before the engine is asked:
        the verdict cannot override a literal copy of the source.

        The LEARNED / VERIFIED streams are not part of that comparison: they ARE
        the record, so counting them as "the source" would let any stored answer
        verify itself.  The ground is the pool's material - documents, stored
        rows, skills, history.
        """
        conf = float(getattr(self, "_ff_last_match_conf", 0.0) or 0.0)
        if not learned:
            return 0.0
        if manual:
            log_block(
                logger, logging.INFO, "Form Field: %s" % label,
                "using the answer the user verified (%r) - no engine verdict, "
                "no re-derivation" % str(learned)[:80],
            )
            return 1.0
        # A record the source holds LITERALLY is correct by definition: the
        # calibrated verdict never gets to override a verbatim copy.  Live,
        # 'benitezrodrigo169@gmail.com' sat in the trusted document at char 228
        # while the noul statement scored p=0.416 - the markdown links around
        # the address alone moved the same judgement to 0.55 - and the correct
        # record was discarded, re-probed and lost.  Distinctive values only
        # (>= 8 chars at a word boundary, no identifier the source lacks): a
        # short word ('Yes', 'N/A') occurs everywhere in an 85 k-char pool and
        # proves nothing, so those still go to the engine.
        ground = _ff_pool_body(context) or ""
        try:
            _dv = _ff_dense(learned)
            if (ground and len(_dv) >= 8
                    and not _ff_ungrounded_identifier(learned, ground)
                    and _dv in _ff_dense(ground)
                    and not _ff_fragment_of_evidence(learned, ground)):
                log_block(
                    logger, logging.INFO, "Form Field: %s" % label,
                    "the stored answer %r appears VERBATIM in the trusted "
                    "source - reusing it without an engine verdict" % learned,
                )
                return 1.0
        except Exception:
            pass
        # A COMBINATION the source never states is not an answer, whatever the
        # entailment verdict says: every fragment is real, so
        # `value_accuracy` passes it and the field keeps a value the document
        # never states (live: 'Location (city)*' -> 'Buenos Aires, Saint Paul'
        # - the contact city plus a 2018 employer's city).
        if ground and _ff_compound_unstated(learned, ground):
            log_block(
                logger, logging.INFO, "Form Field: %s" % label,
                "the stored answer %r is a combination the source never states "
                "- not reusing it; answering from the source instead"
                % str(learned)[:80],
            )
            return 0.0
        # The label matching only proves the QUESTION is the same, never that the
        # stored value is still true.  An AUTO record nobody can point at in the
        # source is stale (live: 'Photo' -> 'PDF resume_rodrigo_benitez.pdf', a
        # filename the pool never held): it may ride as context, it is not the
        # answer.  A USER-verified record is exempt - that word is authoritative.
        if ground and not self._ff_record_supported(learned, "", ground):
            log_block(
                logger, logging.INFO, "Form Field: %s" % label,
                "the stored answer %r is nowhere in the source - not reusing "
                "it; answering from the source instead" % str(learned)[:80],
            )
            return 0.0
        if str(cfg.get("confidence_source") or "laya").lower() != "laya":
            return conf
        try:
            from AI.laya_hooks import value_accuracy_or
            _v = value_accuracy_or(label, learned, ground or context)
        except Exception:
            _v = None
        if _v is False:
            log_block(
                logger, logging.INFO, "Form Field: %s" % label,
                "the stored answer %r does not fit this field (Laya) - "
                "fabricating a fresh answer from the whole pool" % learned,
            )
            return 0.0
        if _v is True:
            return max(conf, 0.99)
        return conf

    @staticmethod
    def _ff_record_supported(record, evidence, source_text):
        """True when the source (or the strips) HOLDS the record as written.

        An AUTO record is a value some earlier pass derived from material that
        may since have changed, or that the pass itself got wrong.  Repeating it
        into a field is only honest when the pool still states it: a value that
        is nowhere in the source (live: 'PDF resume_rodrigo_benitez.pdf' for a
        'Photo' field) is stale, and the field is better left alone than given a
        value nobody can point at.  A SHORT record ('Yes', 'N/A') occurs
        everywhere and proves nothing either way, so it is never blocked here.
        """
        dense = _ff_dense(record)
        if not dense:
            return False
        if len(dense) < 8:
            return True
        ground = _ff_dense(_ff_pool_body(source_text) or source_text)
        return dense in _ff_dense(evidence or "") or dense in ground

    # Control shapes that can hold a record ONLY after it is re-worded.
    _FF_STRICT_TYPES = frozenset((
        "number", "range", "date", "datetime-local", "time", "month",
        "week", "email", "tel", "url", "password", "color",
    ))

    def _ff_fit_record(self, label, learned, field, kind):
        """The stored record, when THIS control can take it as it stands.

        A record answers the QUESTION; it does not certify a string for every
        occurrence of that question - the same ask is a textarea on one form,
        a number-only box on the next, and a length-capped input on a third.
        """
        val = str(learned or "").strip()
        if not val:
            return ""
        # A band / numeric answer is fitted deterministically (no model).
        _adapted = self._ff_adapt_learned(val, field)
        if _adapted:
            return _adapted
        itype = str(field.get("type") or "").strip().lower()
        constrained = (
            str(field.get("tag") or "").strip().lower() == "textarea"
            or str(kind or "").strip().lower() in ("combo", "switch")
            or itype in self._FF_STRICT_TYPES
            or bool(field.get("pattern") or field.get("minlength")
                    or field.get("maxlength"))
            or "\n" in val
            or len(val) > 120
        )
        if constrained:
            log_block(
                logger, logging.INFO, "Form Field: %s" % label,
                "the record %r is the ANSWER, not a string to paste: this "
                "control constrains the wording, so it is re-fitted"
                % val[:80],
            )
            return ""
        return val

    def _ff_adapt_learned(self, learned, field):
        """Adapt a learned answer to THIS field's control ('' = keep as-is).

        A choice answer learned as a band ("2-5 years") is right for the CHOICE
        question, but the SAME question can appear as a free-text control that
        only accepts a single number; writing the band there is rejected (or
        fails the site's review), and the field then sticks on the stored value
        forever.  For a numeric control the band is scalarized to its lower
        bound instead of being repeated verbatim.
        """
        val = str(learned or "").strip()
        if not val:
            return ""
        itype = str(field.get("type") or "").strip().lower()
        # A BAND ("2-5 years", "1 to 3", "5+") is unmistakably a CHOICE-shape
        # answer: for a free-text control that is not a textarea it is scalarized
        # even when the page hides the numeric requirement (type=text, no pattern).
        _is_band = bool(re.search(r"\d\s*(?:-|\u2013|\u2014|to)\s*\d|\d\s*\+",
                                  val, re.IGNORECASE))
        _free_text = (not field.get("options")
                      and str(field.get("tag") or "").lower() != "textarea")
        if itype in self._FF_NUMERIC_TYPES or (_is_band and _free_text):
            _sc = _ff_scalarize(val)
            if _sc and _ff_norm(_sc) != _ff_norm(val):
                log_block(
                    logger, logging.INFO,
                    "Form Field: %s" % (field.get("label") or ""),
                    "learned answer %r adapted to this control -> %r"
                    % (val, _sc),
                )
                return _sc
        return ""

    def _ff_value_accurate(self, label, value, evidence):
        """Is the value ALREADY on the page the RIGHT one for this field?

        Judged against the node's TRUSTED SOURCE - the document set on the node
        dialog (swappable), the root truth:

        True  -> the source supports the value -> skip the field.
        False -> the value is wrong / unrelated (stale, or a read that resolved
                 a SIBLING control) -> refill it.
        None  -> no source / no engine -> caller keeps its existing skip rule.

        A value the source LITERALLY contains is accepted at once (identifiers
        such as a URL / email by identity, plain text at a word boundary) with
        no engine needed; otherwise Laya judges it against the source.
        """
        if not label or not value:
            return None
        ev = str(evidence or "").strip()
        try:
            if ev and not _ff_ungrounded_identifier(value, ev):
                _dv = _ff_dense(value)
                if len(_dv) >= 3 and _dv in _ff_dense(ev):
                    return True
        except Exception:
            pass
        try:
            from AI.laya_hooks import value_accuracy_or
            return value_accuracy_or(label, value, ev)
        except Exception as exc:
            logger.debug("[FORM] Laya accuracy verdict unavailable: %s", exc)
            return None

    def _ff_resolve_combo_option(self, field, value, evidence, label, stop_flag):
        """Pin a combo's PARTIAL answer onto one of its REAL options ('' if none).

        A typeahead renders its options ONLY once text is typed, so the scan (and
        the first option peek) saw none, the model answered partial free text, and
        the page never committed it - the field read back 'not an option' on every
        later pass and the chain looped on the SAME field (live: 'Location (city)'
        answered 'Buenos Aires' for the full 'Buenos Aires, Buenos Aires Province,
        Argentina').  This TYPES the answer to reveal the list, reads it, then pins
        the answer to a real option.  Rails, in order: the answer IS an option
        verbatim; the SOURCE picks the most accurate option (the full entry over
        the partial one); the deterministic short-label rail; else '' (the harness
        leaves the field alone rather than write a wrong pick).
        """
        opts = self._ff_combo_options(field, stop_flag, type_text=value)
        if not opts:
            return ""
        if _ff_option_exact(value, opts):
            return _ff_option_match(value, opts)
        try:
            from AI.laya_hooks import choose_option_by_source
            # A LEARNED answer arrives with NO evidence: the learned fast-path
            # skips the probe (`if learned_value: evidence = ""`), so
            # `_ff_source_slice("")` returns "" and the option scorer gets
            # nothing to score against - it returns no pick, the raw learned
            # text is typed into the typeahead and the dropdown never commits a
            # selection (live: the location field).  When there is no evidence,
            # the learned VALUE itself is the source of truth for its own field.
            _slice = self._ff_source_slice(evidence, value) or str(value or "")
            _idx, _answered = choose_option_by_source(label, _slice, opts)
            if _idx is not None and 0 <= _idx < len(opts):
                log_block(
                    logger, logging.INFO, "Form Resolve: %s" % label,
                    "the source %r picks the option %r"
                    % (_slice[:80], str(opts[_idx])),
                )
                return str(opts[_idx])
        except Exception as exc:
            logger.debug("[FORM] Combo source pick failed: %s", exc)
        return _ff_option_match(value, opts)

    # ── Ask the user / learn the answer (opt-in) ────────────────────────────

    @staticmethod
    def _ff_ask_request(label, options, format_spec, kind):
        """The ask-user v2 REQUEST for one field - the CONTROL picks the format.

        A form can ask in more than one shape, so the prompt must match the
        control the page renders: a list-backed field (a select, a radio group,
        a combo with its own options) OFFERS its own options to pick from, a
        toggle is a yes/no question, and everything else is free text.  Asking a
        dropdown as plain text made the user type a value by hand while the page
        only accepts one of its options.
        """
        opts = [str(o).strip() for o in (options or []) if str(o).strip()]
        lines = ['The context has no information for the form field "%s".' % label]
        if format_spec:
            lines.append("Required format: " + str(format_spec))
        if opts:
            ask_kind = "choice"
            choices = [{"label": o, "value": o} for o in opts]
            lines.append('Choose the value for "%s":' % label)
        elif str(kind or "").lower() == "switch":
            ask_kind = "yes_no"
            choices = []
            lines.append('Answer "%s":' % label)
        else:
            ask_kind = "text"
            choices = []
            lines.append('Enter the value for "%s":' % label)
        return {"question": "\n".join(lines), "kind": ask_kind,
                "choices": choices, "accepts": {}, "default": ""}

    @staticmethod
    def _ff_render_ask(request):
        """Flatten an ask request into TEXT for the plain-callback channel."""
        lines = [str(request.get("question") or "")]
        kind = str(request.get("kind") or "text")
        choices = list(request.get("choices") or [])
        if kind == "yes_no":
            lines.append("(Reply yes or no)")
        elif kind == "choice" and choices:
            for i, c in enumerate(choices):
                lines.append("%d) %s" % (i + 1, c.get("label") or c.get("value")))
            lines.append("(Reply with the number or the option text)")
        return "\n".join(p for p in lines if p)

    @staticmethod
    def _ff_pick_option(answer, options):
        """Map the user's reply onto the page's OWN option text ('' if none).

        The prompt numbers the field's options and a list-backed control can
        only HOLD one of its own options, so a reply of '2' (or a partial
        'Spain') resolves to the option the page really offers instead of being
        written as typed.
        """
        text = str(answer or "").strip()
        opts = [str(o) for o in (options or []) if str(o).strip()]
        if not text or not opts:
            return ""
        m = re.match(r"^\(?(\d{1,3})\)?[.)]?$", text)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(opts):
                return opts[idx]
        return _ff_option_match(text, opts)

    @staticmethod
    def _ff_answer_text(raw):
        """The value out of whatever an ask channel handed back.

        A channel may return the v2 PAIR ``(value, attachments)`` - the app's
        rich callback is wired to the very function ``ask_question`` returns -
        and stringifying that wrote the literal '(None, [])' into the field as
        a "value" (live: ``Value (None, [])``, ``Status failed``).
        """
        if isinstance(raw, (tuple, list)):
            raw = raw[0] if raw else ""
        return str(raw or "")

    def _ff_ask_user(self, label, options, format_spec, cfg, stop_flag, kind=""):
        """Ask the user for a field nothing grounds, in the CONTROL's format.

        Uses the RICH ask channel Input nodes use first - numbered options in the
        chat in agent mode, a real chooser (buttons for a choice / yes-no) on the
        GUI thread in manual mode - falling back to the plain text callback.  The
        fill PAUSES for the whole wait - that is the point of the toggle.
        """
        if not cfg.get("ask_user") or self._ff_halt(stop_flag):
            return ""
        if getattr(self, "_ff_no_ask", False):
            # The field already has a LEARNED record - the node decides from it
            # instead of asking the user the SAME question again.
            return ""
        request = self._ff_ask_request(label, options, format_spec, kind)
        try:
            rich = self.llm_executor.get_variable("_ask_user_v2_callback")
        except Exception:
            rich = None
        try:
            legacy = self.llm_executor.get_variable("_ask_user_callback")
        except Exception:
            legacy = None
        if legacy is None:
            legacy = getattr(self, "_ask_user_callback", None)
        answer = ""
        try:
            log_block(logger, logging.INFO, "Form Filler Ask",
                      "nothing grounded '%s' (%s) - asking the user (blocking)"
                      % (label, request["kind"]))
            if rich:
                response = rich(request) or {}
                if isinstance(response, dict):
                    answer = str(response.get("value") or "")
                else:
                    answer = self._ff_answer_text(response)
            elif legacy:
                answer = self._ff_answer_text(
                    legacy(self._ff_render_ask(request)))
            else:
                # Manual mode has no callback: the same themed prompt an Input
                # node shows, on the GUI thread.
                try:
                    from .....qt_input import ask_question
                except Exception:
                    from player.qt_input import ask_question
                text, _files = ask_question(request)
                answer = str(text or "")
        except Exception as exc:
            log_block(logger, logging.WARNING, "Form Filler Ask",
                      "could not ask the user for '%s': %s" % (label, exc))
            return ""
        answer = answer.strip()
        if answer and options:
            # A list-backed control holds one of its OWN options: resolve a
            # numbered / partial reply onto the page's real option text.
            answer = self._ff_pick_option(answer, options) or answer
        log_block(logger, logging.INFO, "Form Filler Ask: %s" % label,
                  answer or "(no answer - left to the default rule)")
        return answer

    # ── ONE question = ONE record ───────────────────────────────────────────
    # The SAME question reaches a form in several wordings ("how much
    # experience you have with cloud providers like aws?" / "how many years of
    # experience do you have with aws?").  Keyed by the raw label, each wording
    # became its own record with its OWN answer, so the store grew duplicate
    # entries that disagreed with each other.  A new wording is therefore
    # resolved to the record that already asks the same thing and is stored
    # under THAT key - or not stored at all when the answers conflict.
    _FF_DUPE_PREFILTER = 0.55

    def _ff_canonical_label(self, label, known, cfg, stop_flag=None,
                            answer=None):
        """The existing key that asks the SAME question as *label* ('' = new).

        Exact match first (free), then a lexical prefilter, then the node's own
        model - the only judge that can tell "with aws" from "with azure",
        which no lexical or embedding score separates.  A failed/unreadable
        verdict means NEW (keeping two records is recoverable; merging two
        different questions is not).

        A question re-asked in ANOTHER LANGUAGE shares no word with this one,
        so the lexical prefilter can never surface it: any record already
        holding the same *answer* is a candidate too, which is exactly what the
        engine's undecided band (where a translation lands) is lifted for.
        """
        key = _ff_norm(label or "").strip("*? .:")
        if not key:
            return ""
        cache = getattr(self, "_ff_canon_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._ff_canon_cache = cache
        if key in cache:
            return cache[key]
        known = known or {}
        if key in known:
            cache[key] = key
            return key
        _wanted = _ff_norm(answer or "").strip()
        cands, _same_answer = [], []
        for other in known:
            if not other:
                continue
            _score = _ff_dupe_score(key, other)
            if _score >= self._FF_DUPE_PREFILTER:
                cands.append((_score, other))
            elif _wanted and _ff_norm(known.get(other) or "").strip() == _wanted:
                _same_answer.append((_score, other))
        cands.sort(reverse=True)
        cands = cands[:4] + _same_answer[:4]
        if not cands:
            cache[key] = ""
            return ""
        if len(cands) == 1 and cands[0][0] >= 0.85:
            cache[key] = cands[0][1]
            return cands[0][1]
        # LAYA FIRST: a calibrated same-question verdict costs one encoder pass
        # per candidate and never invents a value.  The node's model is only the
        # fallback (engine off/down), because it is the expensive part.
        labels = [c for _s, c in cands[:4]]

        def _agree(cand):
            return bool(_wanted) and \
                _ff_norm(known.get(cand) or "").strip() == _wanted

        try:
            from AI.laya_hooks import same_question_or
            chosen = same_question_or(
                key, labels,
                fallback=lambda: self._ff_same_question(key, labels, cfg,
                                                        stop_flag) or "",
                agree=_agree)
        except Exception:
            chosen = self._ff_same_question(key, labels, cfg, stop_flag)
        cache[key] = chosen or ""
        return cache[key]

    def _ff_same_question(self, label, candidates, cfg, stop_flag=None):
        """Which candidate asks the SAME question as *label* ('' = none do)."""
        if not candidates:
            return ""
        _list = "\n".join("%d. %s" % (i + 1, c[:160])
                          for i, c in enumerate(candidates))
        prompt = (
            "Two form fields ask about the SAME information when a single "
            "answer fills both. Wording, language and missing words do not "
            "matter; a different subject (AWS vs Azure), a different attribute "
            "(years vs level) or a different scope (company vs candidate) DOES "
            "matter.\n\n"
            f"NEW FIELD:\n{str(label or '')[:200]}\n\n"
            f"EXISTING RECORDS:\n{_list}\n\n"
            "Reply with ONLY the number of the record that asks the same "
            "question, or NONE when none of them does. No other text.\n\n"
            "Answer:"
        )
        try:
            out = self._ff_llm_call(prompt, "", dict(cfg), stop_flag)
        except Exception:
            return ""
        _line = next((ln.strip() for ln in str(out or "").splitlines()
                      if ln.strip()), "")
        _m = re.search(r"\d+", _line)
        if not _m:
            return ""
        try:
            idx = int(_m.group(0)) - 1
        except (TypeError, ValueError):
            return ""
        if 0 <= idx < len(candidates):
            log_block(
                logger, logging.INFO, "Form Filler Knowledge",
                "'%s' is the SAME question as '%s' - one record, one answer"
                % (str(label or "")[:80], candidates[idx][:80]),
            )
            return candidates[idx]
        return ""

    def _ff_learn(self, label, answer, source_text, manual=False, known=None):
        """Learn an answered field and return the source text with it folded in.

        The answer is (a) returned inside ``source_text`` so the fields AFTER
        this one can retrieve it THIS run, and (b) collected in
        ``self._ff_new_learned`` (``self._ff_verified`` when ``manual``) so the
        caller publishes it into the CONTEXT POOL on this pass's output.  The
        node owns NO file: the pool (a Context node the user wired) is the
        store.  ``manual`` marks an answer the USER gave - it is published in
        the verified block, which outranks every auto record for that question.

        ONE QUESTION = ONE RECORD: a new WORDING is resolved to the record that
        already asks the same thing (see ``_ff_canonical_label``) and is stored
        under THAT key.  When the answers disagree the AUTO answer is dropped
        (the pool keeps its own) and a USER answer replaces it - so the same
        question can never accumulate two conflicting answers.

        The answer is PRUNED of markdown before it is remembered: a record
        learned as '**1**' is replayed into every later form, and the page's own
        validation (a digits-only question) rejects it for good.
        """
        answer = _ff_plain_text(answer)
        if not answer:
            return source_text
        known = known
        if known is None:
            known = getattr(self, "_ff_known_answers", None)
        _canon = ""
        if known:
            _canon = self._ff_canonical_label(
                label, known, getattr(self, "_ff_pass_cfg", None) or {},
                getattr(self, "_ff_pass_stop", None), answer=answer)
        if _canon:
            _stored = str(known.get(_canon) or "").strip()
            if _stored and _ff_norm(_stored) == _ff_norm(answer):
                # Already answered, differently worded: nothing to learn.
                return source_text
            if _stored and not manual:
                log_block(
                    logger, logging.INFO, "Form Filler Knowledge",
                    "'%s' is already answered as %r - keeping that answer "
                    "instead of learning %r"
                    % (str(label or "")[:80], _stored[:60], answer[:60]),
                )
                return source_text
            # The user just corrected it: the SAME record takes the new value.
            label = _canon
        entry = "## %s\n%s\n" % (label, answer)
        try:
            if manual:
                self._ff_verified.append((label, answer))
            else:
                self._ff_new_learned.append((label, answer))
        except Exception:
            pass
        log_block(logger, logging.INFO, "Form Filler Knowledge",
                  "learned '%s' (%s) - published to the context pool"
                  % (label, "verified by the user" if manual else "derived"))
        return ("%s\n\n%s" % (source_text, entry)).strip() if source_text else entry

    def _execute_form_filling_node(self, node, stop_flag):
        if not self._runtime_initialized:
            try:
                self._initialize_chain_runtime()
                self._runtime_initialized = True
            except Exception:
                pass

        data = node.get("data", {}) or {}
        cfg = self._ff_cfg(data)
        # The PASS-WIDE cfg: the desktop rails (write / read / state) and the
        # repair pass act from ANY mode, so they read the config from here
        # instead of threading it through every call.
        self._ff_pass_cfg = cfg
        node_id = self._ff_node_id(node)
        mode = cfg["mode"]
        started = time.time()

        log_table(
            logger, logging.INFO, "Form Filler Parameters",
            [
                ("Node", node_id),
                ("Mode", mode),
                ("Engine / model", "%s / %s" % (
                    cfg["engine"], cfg["model"] or "(default)")),
                ("Max tokens / context size", "%s / %s" % (
                    cfg["max_tokens"],
                    cfg["context_size"] or "auto (engine RAM-aware)")),
                ("Retrieval (CRAG)", "%s | top-k %s, %s strip(s), %.2f / "
                 "%.2f, %s" % (
                     cfg["crag_evaluator"], cfg["crag_top_k"],
                     cfg["crag_max_strips"], cfg["crag_upper_threshold"],
                     cfg["crag_lower_threshold"],
                     cfg["crag_decompose_mode"])),
                ("Corrective store / rewrite", "%s / %s | %s chars" % (
                    cfg["crag_external"], cfg["crag_rewrite"],
                    cfg["crag_char_budget"])),
                ("Consolidation cycles", "%s per field (ceiling)" % (
                    cfg.get("crag_cycles", 1),)),
                ("Verify / repair", "%s / %s (%s attempt(s))" % (
                    cfg["verify"], cfg["repair"], cfg["repair_attempts"])),
                ("Answer 'No' with no evidence", cfg["answer_no"]),
                ("Answer 'N/A' when unanswerable", cfg["answer_na"]),
                ("Ask user (fills + learns)", cfg["ask_user"]),
                ("Include / skip labels", "%s / %s" % (
                    cfg["fields_include"] or "(all)",
                    cfg["fields_skip"] or "(none)")),
                ("Max fields", cfg["max_fields"]),
                ("Web scope", str(data.get("web_scope") or "(whole page)")[:300]),
            ],
        )
        log_block(
            logger, logging.INFO, "Form Filler Instruction",
            cfg["instruction"] or "(none)",
        )

        # 1. ENUMERATE
        if mode == "desktop":
            fields = self._ff_enumerate_desktop(cfg, stop_flag)
        else:
            fields = self._ff_enumerate_web(cfg, stop_flag)
        # 1b. MERGE radio/checkbox controls sharing a group into one field
        # (the model answers the QUESTION, not each option).
        if mode != "desktop":
            fields = self._ff_merge_choices(fields)
            # 1c. READ a combobox's options when its popup was closed at scan
            # time: a typeahead renders its list only once OPENED, so the model
            # got no options and typed free text the page refused to submit
            # (observed: 'Location (city)' answered 'Springfield, United States'
            # - not an option - and every later pass then skipped the field as
            # 'already filled').  With the list in hand the answer is anchored
            # to it by ``_ff_choice_answer``.
            for _f in fields:
                if (str(_f.get("kind") or "").lower() == "combo"
                        and not _f.get("options")
                        and not self._ff_halt(stop_flag)):
                    _got = self._ff_combo_options(_f, stop_flag)
                    if _got:
                        _f["options"] = _got
                    elif not self._ff_halt(stop_flag):
                        # A combobox with NO list is free text on this page -
                        # not every one is a typeahead.  Say so: a silent
                        # 'Options: (free text)' left it unclear whether the
                        # rail was missing or the control simply has none, and
                        # on a genuinely free-text combo a blended value is
                        # indistinguishable from a value the page accepted.
                        log_block(
                            logger, logging.INFO,
                            "Form Field: %s" % (_f.get("label")
                                                or _f.get("id") or "?"),
                            "the control is a combobox but offers NO option "
                            "list - its value is free text here, not a "
                            "selection",
                        )
        # 2. FILTER
        fields = self._ff_filter(fields, cfg)
        _lines = []
        for _i, _f in enumerate(fields, 1):
            _opts = _f.get("options") or []
            _lines.append(
                "%d. [%s] %s%s" % (
                    _i, _f.get("kind") or "text",
                    _f.get("label") or _f.get("id") or "?",
                    ("  options: " + ", ".join(str(o) for o in _opts))
                    if _opts else "",
                )
            )
        log_block(
            logger, logging.INFO,
            "Form Fields to Fill (%d)" % len(fields),
            "\n".join(_lines) or "(none)",
        )

        results = []
        field_map = {}
        # Fresh run = fresh stop latch (a stale ESC must not abort this node).
        self._ff_abort = False
        # ...and a fresh popup-scope latch: the picked window / iframe is
        # re-entered ONCE, before the first web op (see _ff_apply_scope_context).
        self._ff_scope_applied = False
        self._ff_scope_in_frame = False
        # The picked page-scope's selector ladder - the SAME confinement the
        # enumeration used is applied to every field WRITE / READ-BACK, so the
        # repair pass resolves from the user's container instead of the whole
        # page.  [] when no scope was picked (whole document, as before).
        self._ff_run_scope_sel = self._ff_scope_selectors(cfg.get("web_scope"))

        # Source context: the UPSTREAM knowledge pool (a Context node wired to
        # ctx_in, holding the documents + history) plus upstream node outputs.
        # The node no longer attaches documents to itself.
        documents = []
        source_text = self._ff_gather_source_text(node)
        # LEARNED ANSWERS ride the CONTEXT POOL too: the answers the user gives
        # for fields nothing could ground are written into the pool on this
        # pass's output and read back from it on the next (the node owns NO
        # file).  They are carried inside a sentinel block so the pool's source
        # documents can never be mistaken for learned Q&A.
        _corr_text = _ff_pool_corrections(source_text)
        learned_answers = _ff_parse_learned_answers(_corr_text)
        # HUMAN-VERIFIED answers (added/edited in the Context node's dialog) are
        # a SEPARATE, authoritative tier: they win over an auto-learned record
        # for the same question and are never re-derived or engine-vetoed.
        _verified = _ff_parse_learned_answers(_ff_pool_verified(source_text))
        manual_keys = set(_verified)
        learned_answers.update(_verified)
        # This pass's NEW answers, published into the pool on the output below.
        self._ff_new_learned = []
        # ...and the ones the USER gave this pass (published as verified).
        self._ff_verified = []
        # ONE QUESTION = ONE RECORD: the merged pool the canonicalisation reads,
        # plus the pass-scoped memory of what it already resolved.
        self._ff_known_answers = learned_answers
        self._ff_pass_cfg = cfg
        self._ff_pass_stop = stop_flag
        self._ff_canon_cache = {}
        # The pool's learned facts (label -> value), embedded ONCE for the
        # semantic gate below (see _ff_fact_index).
        self._ff_fact_index_cache = None
        log_table(
            logger, logging.INFO, "Form Filler Sources",
            [("Document", str(_d)) for _d in documents]
            + [("Learned answers (pool)", "%d" % len(learned_answers))]
            + [("Verified by the user", "%d" % len(_verified))]
            + [("Upstream context", "%d chars" % len(source_text or ""))],
        )

        aborted = False

        # Idempotence across re-entries, carried by the CONTEXT POOL: this
        # node's own prior output (a JSON summary with `fields`) is wired back
        # through a Context node, so on the next entry it arrives inside the
        # upstream text.  Fields already VALIDATED on a previous pass are
        # skipped below instead of being re-read / re-probed / re-written.  No
        # node-local shortcut: the memory lives in the pool the user wired.
        _prior = self._ff_prior_from_pool(source_text)
        if _prior:
            log_block(
                logger, logging.INFO, "Form Filler Memory",
                "%d field(s) already handled on a previous pass (pool):\n%s"
                % (len(_prior),
                   "\n".join("- %s: %s" % (k, v)
                             for k, v in _prior.items())),
            )

        # The TRUSTED SOURCE for the "already filled?" check: the knowledge
        # pool the node was wired to (documents + history) - the root truth.
        _ground = str(source_text or "")

        # The retrieval itself happens PER FIELD, inside the loop below (one
        # CRAG pass per question, memoized on the node) - there is no
        # pass-level pre-sweep any more: it swept every field up front,
        # including the ones the loop was about to skip.

        for field in fields:
            if self._ff_halt(stop_flag):
                aborted = True
                break
            label = field.get("label") or f"field_{field.get('id')}"
            options = field.get("options") or None
            kind = (field.get("kind") or "text").lower()
            # A textarea holds the free-text "little longer" answers and must
            # keep the whole reply; a single-line input keeps its first line.
            multiline = str(field.get("tag") or "").lower() == "textarea"
            format_spec = self._ff_format_spec(field)
            field_map[field.get("id")] = field
            # Per-field diagnostics, reset so the table below can never show a
            # previous field's route/reply.
            self._ff_last_route = ""
            self._ff_last_raw = ""
            self._ff_last_action = ""
            self._ff_last_kept = 0
            # Why the last extraction DISCARDED its answer (an invented /
            # truncated / borrowed value).  A discard is not "the model grounded
            # nothing": the evidence is there, so the N/A note must say which
            # of the two happened.
            self._ff_last_reject = ""
            # Set when the field is answered N/A because the sources held
            # NOTHING for it (keeps the per-field memory honest, see below).
            na_from_empty = False
            # Name THIS field in the overlay for the WHOLE reasoning pass, not
            # just at the end when its value lands.
            if mode != "desktop":
                self._ff_mark_processing(field, label)

            # 2-pre. POOL MEMORY: a field the previous pass already VALIDATED
            # (filled / skipped / repaired) is left untouched - no resolve, no
            # probe, no write.  This is what stops the chain loop from
            # re-processing correct fields forever; the memory rides the Context
            # node the user wired, not the node itself.
            if _prior.get(label) in ("filled", "skipped", "repaired"):
                results.append({"id": field.get("id"), "label": label,
                                "value": "", "status": "skipped"})
                self._ff_log_field(
                    field, label, cfg, "skipped", options=options,
                    note="validated on a previous pass (pool memory) - "
                         "left unchanged",
                )
                continue

            # 2a. WHERE this field lives.  The Laya-navigated page tree resolves
            # the target element BEFORE anything touches it, and the winning
            # marker becomes the field's FIRST selector rung - so the skip read,
            # the write and the verify all act on the SAME element even when the
            # recorded ladder resolves nothing (rotated ids / hashed classes).
            # Degrades to the ladder on its own (engine off/down, no tree, no
            # pick), so this never becomes a new failure mode.
            if mode != "desktop":
                self._ff_resolve_target(field, cfg, stop_flag)

            # 2b. SKIP is the HARNESS's call, never the model's.  A field the
            # page ALREADY fills is left untouched (no probe, no model call, no
            # write) - EXCEPT a LIST-BACKED control: a <select> shows its FIRST
            # option until someone picks, so a pre-filled value there is usually
            # the page's own DEFAULT, not an answer - and skipping it left the
            # default in place while the sources named another (observed:
            # "Which location are you applying for?" sat on 'Colombia' while the
            # resume says Argentina).  A list-backed control is re-resolved
            # through the choice rail and only rewritten when the answer DIFFERS.
            # The read is mode-aware: the web twin reads the DOM, the desktop
            # twin reads the control's ValuePattern / displayed text.
            current = self._ff_current_value(field, stop_flag, cfg=cfg)
            # A page value that still carries MARKDOWN is not an answer: a
            # learned '**1**' was written verbatim, read back verbatim (the
            # control is a text input, so it ACCEPTS the markers), and then
            # kept forever - the semantic rail below agrees that the value
            # answers the field, so the field was skipped on every later
            # pass while the site's own numeric validation rejected it
            # (live: '¿Cuántos años de experiencia...' with '**1**' in it).
            if current and _ff_plain_text(current) != current.strip():
                log_block(
                    logger, logging.INFO, "Form Field: %s" % label,
                    "the page holds %r - refilling it with its plain "
                    "form %r" % (current[:60],
                                 _ff_plain_text(current)[:60]),
                )
                current = ""
            if current and kind not in _FF_LIST_KINDS:
                # The field already holds a value - but is it the RIGHT one?
                # A read can resolve a SIBLING control (bringing back an
                # unrelated value) and the page can hold a stale answer.
                # Ask Laya SEMANTICALLY whether the value actually answers
                # THIS field: accurate -> skip (no probe, no model call);
                # not accurate -> fall through and refill; no engine ->
                # keep the existing skip.
                _verdict = self._ff_value_accurate(label, current, _ground)
                if _verdict is False:
                    log_block(
                        logger, logging.INFO, "Form Field: %s" % label,
                        "the page holds %r, which does not answer this "
                        "field - refilling it" % current,
                    )
                else:
                    results.append({"id": field.get("id"), "label": label,
                                    "value": current, "status": "skipped"})
                    self._ff_log_field(
                        field, label, cfg, "skipped", value=current,
                        options=options,
                        note=("already filled - verified by Laya"
                              if _verdict else
                              "already filled - left unchanged"),
                    )
                    continue

            # 2c. ALREADY LEARNED: a field the user answered ONCE is stored in
            # this node's own corrections file, keyed by its LABEL, and is
            # resolved WITHOUT the model where it can be:
            #   * a free-text field takes the record verbatim;
            #   * a choice field is mapped onto one of the page's OWN options by
            #     LAYA (the semantic picker) - the model is bypassed, so the
            #     lossy re-derivation that once made the choice rail refuse the
            #     record (live: stored 'Ezeiza, Buenos Aires Province,
            #     Argentina' came back as 'Ezeiza', no option won, and the
            #     field was re-asked) cannot happen.
            # A record that pins NO option is not discarded: the field passes
            # through the usual pipeline with the record as its context - and
            # is still NEVER asked again, the record decides.
            # A CACHE HIT (exact label or the semantic embedding gate) answers
            # the field MECHANICALLY - the retrieval below is skipped, so CRAG
            # never runs for a question the pool already tagged correct.
            learned, _learned_key = self._ff_learned_match(
                label, learned_answers, cfg, manual_keys=manual_keys)
            _is_manual = bool(_learned_key) and _learned_key in manual_keys
            # A record whose VALUE is its own question is NOT an answer: it is
            # the echo an earlier run learned when it wrote a question into the
            # field (the option list had collapsed to the question).  Reusing it
            # kept every later run writing the question back - so a self-answer
            # is treated as NO record and the field is answered afresh.
            if learned and (_ff_norm(learned).strip("*? .:")
                            == _ff_norm(label).strip("*? .:")):
                learned, _learned_key = "", ""
            # CONFIDENCE GATE - a stored answer is reused VERBATIM only when it
            # is a CONFIDENT match AND a semantic verdict (when available) does
            # not say it fails this field.  A low-confidence or rejected record
            # is NOT forced through: it rides as CONTEXT so a fresh answer is
            # FABRICATED from the WHOLE pool (documents, learned, turns) - the
            # stored value must never be repeated forever just because the
            # field already "has an answer".
            _conf = self._ff_reuse_confidence(
                label, learned, cfg, source_text, manual=_is_manual)
            _reuse_ok = bool(learned) and _conf >= float(
                cfg.get("reuse_confidence", 0.82))
            learned_value = ""
            if learned and _reuse_ok:
                if options:
                    if _ff_option_exact(learned, options):
                        learned_value = _ff_option_match(learned, options) or learned
                    else:
                        from AI.laya_hooks import choose_option
                        _idx, _answered = choose_option(label, learned,
                                                       [str(o) for o in options])
                        if _idx is not None and 0 <= _idx < len(options):
                            learned_value = str(options[_idx])
                else:
                    # A stored record is the ANSWER this question GOT, not a
                    # string to paste: the same question can appear here as a
                    # textarea, a number-only box or a length-capped input,
                    # and the record's own wording belongs to the occurrence
                    # it was written for.  It is committed as it stands only
                    # where this control cannot tell the difference; otherwise
                    # it rides as CONTEXT and the extractor re-fits it.
                    learned_value = self._ff_fit_record(label, learned, field,
                                                        kind)
            # A field that asks for a SELECTION besides free text (a typeahead /
            # combo, or any list-backed control) can hold only one of the page's
            # OWN options.  The learned record must still be USED - but as a
            # SELECTION, not as free text the control cannot commit.  Read the
            # list (typing the record opens a typeahead) and pin the record onto
            # a real option before it is written.
            if (learned and _reuse_ok and not learned_value
                    and (kind == "combo" or options)):
                learned_value = self._ff_resolve_combo_option(
                    field, learned, "", label, stop_flag)

            # A field with a record is answered from it - never re-asked.
            self._ff_no_ask = bool(learned)
            if learned_value:
                self._ff_last_route = (
                    "learned fact: reused a correct answer (no CRAG)")
            elif learned:
                self._ff_last_route = (
                    "learned (confidence %.2f < %.2f): fabricating from the "
                    "whole pool" % (_conf, float(
                        cfg.get("reuse_confidence", 0.82))))

            # 3a. PROBE (only the relevant slice).  A field whose PREVIOUS pass
            # found no evidence in UNCHANGED sources is not swept again: the
            # probe is skipped so the existing "nothing retrieved" path asks
            # the user (or skips) instead of paying another full sweep for a
            # known-empty result.
            if learned_value:
                evidence = ""
            elif _prior.get(label) == "no_evidence":
                evidence = ""
                self._ff_last_route = "memory: no evidence on the last pass"
            else:
                self._ff_learned_hint = learned if not _reuse_ok else ""
                evidence = self._ff_retrieve(
                    label, cfg, source_text, documents, stop_flag,
                    field=field,
                )
            # The strips alone: the record's own hint is appended below, and a
            # hint is not evidence - grounding the answer against it would make
            # any record prove itself.
            evidence_own = evidence
            if learned and not learned_value:
                # The record did not decide this field on its own: it rides as
                # CONTEXT (the pool's learned stream) so the pipeline can reuse
                # or ADAPT it - never repeat it blindly.
                evidence = ("The user already answered this field before: %s"
                            "\n\n%s" % (learned, evidence or "")).strip()
                self._ff_last_route = "learned (unpinned): decided from the record"
            if self._ff_halt(stop_flag):
                aborted = True
                break
            log_block(
                logger, logging.INFO,
                "Form Field Evidence: %s (%d chars)"
                % (label, len(evidence or "")),
                evidence or "(nothing retrieved)",
            )
            if not evidence and not learned_value:
                # The sources held NOTHING for this field, whatever value the
                # branch below writes - the per-field memory must record that,
                # or the next pass pays a full CRAG pass again.
                na_from_empty = True
                # 3a-bis. ASK THE USER (opt-in): nothing in the context grounds
                # this field, so ask - the answer fills it and is learned into
                # the knowledge file for later runs.
                note = ""
                value = self._ff_ask_user(label, options, format_spec, cfg,
                                          stop_flag, kind=kind)
                if value:
                    note = "no context - answered by the user"
                    # A user answer is VERIFIED: it outranks auto records.
                    source_text = self._ff_learn(label, value, source_text,
                                                 manual=True)
                # Nothing grounded the field.  A CHOICE field with nothing
                # behind it takes the option that asserts the negative ("no
                # evidence -> no"), decided by MEANING: handing it to the model
                # let it pick from an option list that had collapsed to the
                # question, and the question was written into the page.  A free
                # text control still asks the model for the control's own
                # none / no wording - the harness never classifies the question.
                if (not value and cfg["answer_no"]
                        and not self._ff_halt(stop_flag)):
                    value = (self._ff_negative_option(label, options)
                             if options else
                             self._ff_extract(
                                 label, "", cfg, stop_flag, options=options,
                                 multiline=multiline, format_hint=format_spec,
                                 max_chars=_as_int(field.get("maxlength"), 0),
                                 kind=kind,
                                 retry_note=(
                                     "The sources hold NOTHING for this field. "
                                     "If the control offers an answer meaning "
                                     "none / no / not applicable, return it "
                                     "EXACTLY as the control spells it; "
                                     "otherwise reply exactly N/A.\n"
                                 ),
                             ))
                    if value:
                        note = "nothing was retrieved - answered %r" % value
                # A field that CANNOT be answered at all gets N/A (the last
                # resort); everything else is SKIPped (left alone).
                if not value and cfg["answer_na"] and _ff_na_value(options):
                    value = _ff_na_value(options)
                    note = ("nothing was retrieved and the field cannot be "
                            "answered - wrote %r" % value)
                if not value:
                    results.append({"id": field.get("id"), "label": label,
                                    "value": "", "status": "skipped"})
                    self._ff_log_field(
                        field, label, cfg, "skipped", options=options,
                        note="nothing was retrieved for this field",
                    )
                    continue
            else:
                # 3b. EXTRACT one value - unless the user ALREADY answered this
                # exact field once: the learned answer is authoritative, so it
                # is used as-is instead of being re-derived by the model.
                note = ""
                if learned_value:
                    value = learned_value
                    note = ("already answered for this field before - used the "
                            "learned answer")
                else:
                    value = self._ff_extract(
                        label, evidence, cfg, stop_flag, options=options,
                        multiline=multiline, format_hint=format_spec,
                        max_chars=_as_int(field.get("maxlength"), 0),
                        kind=kind,
                    )
                    # ...and a record that rode in as CONTEXT must not be
                    # COPIED BACK when the source does not hold it: the extractor
                    # echoes what it is shown, so an unsupported record returns
                    # as if it were grounded (live: 'Photo' -> a filename the
                    # pool never contained).
                    if (value and learned and not _reuse_ok
                            and _ff_norm(value) == _ff_norm(learned)
                            and not self._ff_record_supported(
                                learned, evidence_own, source_text)):
                        log_block(
                            logger, logging.WARNING, "Form Filler Knowledge",
                            "the record %r is not in the source and the answer "
                            "simply repeated it - discarded"
                            % str(value)[:80],
                        )
                        value = ""
                if self._ff_halt(stop_flag):
                    aborted = True
                    break
                # The model's unanswerable verdict is exactly the case the user
                # wants to OWN: with ask_user on, the question goes to the user
                # BEFORE the last-resort N/A is written - the last resort never
                # beats a real answer the user can give.
                if (value and _ff_na_value(options) == value
                        and cfg["ask_user"] and not self._ff_halt(stop_flag)):
                    _asked = self._ff_ask_user(label, options, format_spec,
                                               cfg, stop_flag, kind=kind)
                    if _asked:
                        value = _asked
                        note = ("the model could not answer - answered by the "
                                "user")
                        source_text = self._ff_learn(label, value, source_text)
                if value and _ff_na_value(options) == value:
                    note = "the field could not be answered - wrote %r" % value
                if not value:
                    # The context held something but grounded no value: ask the
                    # user before skipping / answering No.
                    value = self._ff_ask_user(label, options, format_spec, cfg,
                                              stop_flag, kind=kind)
                    if value:
                        note = "nothing grounded - answered by the user"
                        source_text = self._ff_learn(label, value, source_text)
                if not value and cfg["answer_na"] and _ff_na_value(options):
                    value = _ff_na_value(options)
                    note = ("the model grounded nothing and the field cannot "
                            "be answered - wrote %r" % value)
                    _why = getattr(self, "_ff_last_reject", "")
                    if _why:
                        note = ("the answer was discarded (%s) and nothing "
                                "grounded replaced it - wrote %r" % (_why, value))
                if (not value and learned
                        and self._ff_record_supported(learned, evidence_own,
                                                      source_text)):
                    # The record pinned no option and the model found nothing:
                    # decide from the user's own record, never ask again.
                    value = (_ff_option_match(learned, options) if options
                             else learned)
                    if value:
                        note = "used the record for this field: %r" % value
                if not value:
                    results.append({"id": field.get("id"), "label": label,
                                    "value": "", "status": "skipped"})
                    self._ff_log_field(
                        field, label, cfg, "skipped", options=options,
                        note="no value could be grounded - left unchanged",
                    )
                    continue

            # 3c-0. TYPEAHEAD RESOLUTION: a combo whose popup was closed at scan
            # time (options empty) renders its list only once text is typed, so
            # the model answered PARTIAL free text.  Type it to reveal the list,
            # read it, and pin the answer onto a REAL option (the source picks
            # the full 'City, Province, Country' over the partial 'City').  A
            # partial value the page never commits is what made the SAME field
            # loop forever (read back 'not an option' every pass).
            if (mode == "web" and kind == "combo" and value
                    and not _ff_option_exact(value, options)):
                _full = self._ff_resolve_combo_option(
                    field, value, evidence, label, stop_flag)
                if _full:
                    log_block(
                        logger, logging.INFO, "Form Field: %s" % label,
                        "the partial answer %r was pinned to the option %r"
                        % (value, _full),
                    )
                    value = _full

            # 3b-bis. INVARIANT: a CHOICE control can only HOLD one of the
            # page's OWN options.  A value that is not one of them - a question
            # the pool echoed back, a poisoned learned record, a prose reply -
            # has NO control to land in: the write aims at whatever the ladder
            # (or a hijacked marker) resolves, the read-back then echoes what
            # was written, the field is reported "filled" while the page holds
            # nonsense, and that value is LEARNED so every later run replays it
            # (live: a multi-choice field written with its own question, and
            # with ANOTHER question's text).  Snap it onto an option; when none
            # fits, leave the field ALONE.
            if options and kind == "choice":
                if not _ff_option_exact(value, options):
                    _snap = _ff_option_match(value, options)
                    if not _snap:
                        results.append({"id": field.get("id"), "label": label,
                                        "value": "", "status": "skipped"})
                        self._ff_log_field(
                            field, label, cfg, "skipped", options=options,
                            note=("the answer %r is not one of this field's "
                                  "options - left unchanged" % value))
                        continue
                    value = _snap

            # 3c-pre. A LIST-BACKED field the page ALREADY holds the SAME
            # answer for is left alone: re-writing an identical option costs a
            # write + a re-ask for nothing, and a big country select would churn.
            # A COMBO/typeahead is the EXCEPTION: holding the option TEXT is not
            # a COMMITTED selection - the widget validates against its own list -
            # so the form can look filled and still refuse to advance (live: a
            # learned city left in a typeahead, 'Next' did nothing).  Commit the
            # selection (type -> click the option) FIRST; a control that will not
            # commit falls through to the normal write instead of being skipped.
            if (current and kind in _FF_LIST_KINDS
                    and _ff_norm(value) == _ff_norm(current)):
                _committed = True
                if kind == "combo" and mode == "web":
                    _picked = self._ff_commit_combo(field, current, stop_flag)
                    if _picked:
                        value = _picked
                    else:
                        _committed = False
                if _committed:
                    results.append({"id": field.get("id"), "label": label,
                                    "value": current, "status": "skipped"})
                    self._ff_log_field(
                        field, label, cfg, "skipped", value=current,
                        options=options, note="already filled - left unchanged",
                    )
                    continue

            # 3c. WRITE into the field's OWN target (runtime-owned binding)
            wrote = self._ff_write(field, value, stop_flag)
            # Read-back only feeds the verify pass — skip it when disabled.
            read_back = (
                self._ff_read(field, stop_flag)
                if (wrote and cfg["verify"]) else None
            )

            # 3d. VERIFY (default on): a read-back that is EMPTY - or, for a
            # choice, that is NOT the option the answer named - re-asks THIS
            # field once.  A click can resolve to the wrong control (the page
            # holds several with the same label), and the resulting non-empty
            # read-back would otherwise be reported as a correct selection.
            # A DESKTOP widget whose value cannot be read returns None instead
            # of "": re-asking there would report a landed field as a failure.
            if (cfg["verify"] and wrote
                    and (mode == "web" or read_back is not None)):
                landed = (read_back or "").strip()
                if landed and not self._ff_choice_landed(landed, value, options):
                    landed = ""
                if not landed:
                    value2 = self._ff_extract(
                        label, evidence, cfg, stop_flag, options=options,
                        multiline=multiline, format_hint=format_spec,
                        max_chars=_as_int(field.get("maxlength"), 0),
                        retry_note="Your previous answer did not land. Return ONLY the value.\n",
                        kind=kind,
                    )
                    log_block(
                        logger, logging.INFO,
                        "Form Field Re-ask: %s (read-back was %s)" % (
                            label,
                            "empty" if not (read_back or "").strip()
                            else "not the option that was answered"),
                        value2 or "(empty)",
                    )
                    if value2:
                        wrote = self._ff_write(field, value2, stop_flag) or wrote
                        value = value2
                        read_back = self._ff_read(field, stop_flag)
                        landed = (read_back or "").strip()
                        if (not landed
                                or not self._ff_choice_landed(landed, value,
                                                              options)):
                            wrote = False
                    elif options and kind == "choice":
                        # Nothing was re-asked AND the option never landed: a
                        # choice holding none of its own options is NOT filled.
                        # An empty read-back must never be reported as success -
                        # a hijacked marker can aim the write at another
                        # question's free-text control, which accepts anything
                        # and reads back exactly what was written.
                        wrote = False

            status = "filled" if wrote else "failed"
            _res = {"id": field.get("id"), "label": label,
                    "value": value, "status": status}
            if na_from_empty:
                # Answered N/A because the sources held NOTHING for it: the
                # per-field memory must still record "no evidence", or the next
                # pass pays a full sweep again for a known-empty field.
                _res["na_from_empty"] = True
            results.append(_res)
            self._ff_log_field(
                field, label, cfg, status, value=value, options=options,
                read_back=read_back, note=note,
            )

        # The per-field state is NOT kept node-locally: it rides the node's own
        # output (the `fields` summary above), which a Context node wired to
        # ctx_out stores and serves back on the next entry.

        # 4. REPAIR: re-check every written field's NATIVE validity and correct
        # the rejects with the same CRAG pass - a date answered '2017' is
        # corrected instead of silently left wrong.  Never after an abort: ESC
        # must end the node, not start a second pass.
        repaired = 0
        if aborted:
            log_block(
                logger, logging.WARNING, "Form Filler",
                "STOPPED BY USER (ESC) - %d of %d field(s) handled, no repair "
                "pass" % (len(results), len(fields)),
            )
        elif cfg["repair"] and results:
            repaired = self._ff_repair_fields(
                field_map, results, cfg, source_text, documents, stop_flag,
            )

        # 5. OUTPUT (counts derived from the results, so repairs are counted)
        filled = sum(1 for r in results
                     if r.get("status") in ("filled", "repaired"))
        skipped = sum(1 for r in results if r.get("status") == "skipped")
        failed = sum(1 for r in results if r.get("status") == "failed")
        # How many fields were UNANSWERABLE (written the N/A literal).  Logged
        # so the gate stays auditable: a large number means the source really
        # lacks the answers - or the policy has become a lazy way out.
        na = sum(1 for r in results
                 if str(r.get("value") or "").strip().upper() == "N/A")
        summary = {
            "mode": mode,
            "fields_total": len(fields),
            "filled": filled,
            "skipped": skipped,
            "failed": failed,
            "answered_na": na,
            "repaired": repaired,
            "aborted": aborted,
            "fields": results,
        }
        # FACTS the system RESOLVED CORRECTLY this pass become POOL KNOWLEDGE:
        # a later page/run with the same question is filled MECHANICALLY from the
        # cache (no retrieval, no CRAG).  A field we WROTE (filled/repaired) counts,
        # and so does one the PAGE already held and Laya verified (skipped) -
        # otherwise a run where every field was pre-filled (the common re-entry
        # case) learns NOTHING.  Only non-empty, non-N/A values are tagged;
        # failures are never cached, and the user can prune any entry in the
        # Context node's audit dialog.
        try:
            _known = {_ff_norm(_l).strip("*? .:")
                      for _l, _ in getattr(self, "_ff_new_learned", [])}
            # A question the pool ALREADY holds an answer for is never learned
            # again.  Re-publishing it every pass grew the append-only pool with
            # a freshly derived (and slightly different) answer per run, so the
            # same field flip-flopped between values instead of keeping the one
            # it had - and the store the user audits filled up with duplicates.
            _known.update(_ff_norm(_k).strip("*? .:") for _k in learned_answers)
            for _r in results:
                _st = _r.get("status")
                _lab = str(_r.get("label") or "")
                # Pruned BEFORE it is cached: a page value that still carries
                # markdown (a '**1**' verified by Laya) is not an answer to
                # learn - it would poison every later form.
                _val = _ff_plain_text(str(_r.get("value") or "").strip())
                if (_st not in ("filled", "repaired", "skipped") or not _val
                        or _lab.startswith("field_")
                        or _val.upper() == "N/A"):
                    continue
                # A CHOICE field can only hold one of its OWN options, so an
                # off-list value is NOT an answer to learn: caching one (the
                # question the pool echoed back, a prose reply) taught the node
                # a WRONG answer that every later run reused verbatim - the loop
                # that kept writing a question into the field.
                _fld = field_map.get(_r.get("id")) or {}
                _fopts = _fld.get("options") or []
                if (_fopts and (_fld.get("kind") or "").lower() == "choice"
                        and not _ff_option_exact(_val, _fopts)):
                    continue
                _k = _ff_norm(_lab).strip("*? .:")
                if _k and _k not in _known:
                    # A reworded question must not start a SECOND record: resolve
                    # it to the one that already asks the same thing, and keep
                    # the pool's answer when the two disagree.
                    _canon = self._ff_canonical_label(
                        _lab, learned_answers, cfg, stop_flag)
                    if _canon:
                        _stored = str(learned_answers.get(_canon) or "").strip()
                        if _stored and _ff_norm(_stored) != _ff_norm(_val):
                            continue
                        _lab, _k = _canon, _canon
                        if _k in _known:
                            continue
                    self._ff_new_learned.append((_lab, _val))
                    _known.add(_k)
        except Exception:
            pass

        payload = json.dumps(summary, ensure_ascii=False)
        try:
            if node_id:
                self.llm_executor.set_variable(f"node_{node_id}_output", payload)
                self.llm_executor.set_variable(f"node_{node_id}_context", payload)
                # Publish on the context output port so a Context node wired to
                # ctx_out stores this pass's per-field state - the memory the
                # NEXT entry reads back through the pool.
                try:
                    _cid = (getattr(self, 'chain_id', None)
                            or getattr(self, 'chain_file', ''))
                    _corr = _ff_render_corrections(
                        getattr(self, '_ff_new_learned', []))
                    _ver = _ff_render_verified(
                        getattr(self, '_ff_verified', []))
                    _blocks = "\n\n".join(b for b in (_corr, _ver) if b)
                    _ctx_out = (payload + "\n\n" + _blocks) if _blocks else payload
                    self.port_store.set_output(_cid, node_id, 'ctx_out', _ctx_out)
                except Exception:
                    pass
                # PASSIVE push: a Context node never runs (its edges are
                # data-only), so persist this pass's material into the pool it
                # is wired to RIGHT NOW - otherwise the learned answers never
                # reach the store (the audit dialog then shows no Learned
                # entries even though the node recalls them within the run).
                try:
                    for _c in ((node.get("connections", {}) or {}).get("ctx_out") or []):
                        _tgt = _c.get("node_id") or _c.get("target_node_id")
                        if _tgt:
                            self._ctx_serve(_tgt)
                except Exception:
                    pass
        except Exception as exc:
            log_block(logger, logging.WARNING, "Form Filler",
                      "failed to store the node output: %s" % exc)

        log_table(
            logger, logging.INFO, "Form Filler Summary",
            [
                ("Node", node_id),
                ("Mode", mode),
                ("Fields found", len(fields)),
                ("Filled", filled),
                ("Skipped", skipped),
                ("Failed", failed),
                ("Answered 'N/A'", na),
                ("Repaired", repaired),
                ("Elapsed", "%.1fs" % (time.time() - started)),
            ],
        )
        # A per-field failure that lives ONLY inside the summary table is a
        # SILENT failure: the node still reports "completed successfully", so
        # nobody notices the page kept its old value.  Surface it as a warning
        # naming the field(s) that were answered but never landed.
        if failed:
            log_block(
                logger, logging.WARNING, "Form Filler",
                "%d field(s) answered but NOT written - the page kept its old "
                "value:\n%s" % (
                    failed,
                    "\n".join(
                        "- %s (answered %r)"
                        % (r.get("label"), r.get("value"))
                        for r in results if r.get("status") == "failed"
                    ),
                ),
            )
        if na:
            log_block(
                logger, logging.WARNING, "Form Filler",
                "%d field(s) could not be answered and were written 'N/A' - "
                "check that the source really lacks them:\n%s" % (
                    na,
                    "\n".join(
                        "- %s" % r.get("label") for r in results
                        if str(r.get("value") or "").strip().upper() == "N/A"
                    ),
                ),
            )
        log_block(logger, logging.INFO, "Form Filler Output", payload)
        # Take the in-page overlay down with the node (the web engine does the
        # same at the end of a replay run).
        if mode != "desktop":
            self._ff_disable_overlay()

        # STALL CAP: the chain re-enters this node whenever a field does not
        # land (typically a required field the page keeps rejecting), and with
        # no bound that loop runs for HOURS (live: 51 identical re-entries on
        # one LinkedIn modal burned ~10.7h).  When the per-field OUTCOME is
        # identical pass after pass the node is making NO progress and the
        # wizard will never advance - so end the chain instead of looping.  The
        # signature is (label -> status): stable across passes even when the
        # page's generated ids rotate, yet DIFFERENT for a genuine next wizard
        # step (which shows different fields), so a real multi-step wizard is
        # never cut short.
        _stall_limit = _as_int(cfg.get("max_stall_passes"), 6)
        if _stall_limit > 0 and node_id and results:
            _out_sig = "|".join(sorted(
                "%s=%s" % (r.get("label"), r.get("status")) for r in results))
            _stall_key = f"node_{node_id}_form_stall"
            try:
                _prev_stall = self.llm_executor.get_variable(_stall_key) or {}
            except Exception:
                _prev_stall = {}
            _streak = ((_prev_stall.get("streak", 0) + 1)
                       if _prev_stall.get("sig") == _out_sig else 1)
            try:
                self.llm_executor.set_variable(
                    _stall_key, {"sig": _out_sig, "streak": _streak})
            except Exception:
                pass
            if _streak >= _stall_limit:
                log_block(
                    logger, logging.ERROR, "Form Filler",
                    "STALLED: the same %d field(s) produced an IDENTICAL "
                    "outcome on %d consecutive passes (no progress) - ending "
                    "the chain to stop an unbounded re-entry loop.  A required "
                    "field that never lands cannot advance the wizard; check "
                    "its target resolution in the 'Form Resolve' / 'Form "
                    "Write' entries above." % (len(results), _streak),
                )
                return "__done__"

        return self._ff_next_node(node)
