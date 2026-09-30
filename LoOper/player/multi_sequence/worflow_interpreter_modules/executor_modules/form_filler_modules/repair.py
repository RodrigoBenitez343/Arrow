"""Post-pass: native validity, content problems and the repair loop."""

import logging
import re

from .common import (
    _PLACEHOLDER_RE,
    _as_int,
    _ff_na_value,
    _ff_placeholder_value,
    _ff_ungrounded_identifier,
    log_block,
    log_table,
    logger,
)


class FormFillerRepairMixin:
    """Post-pass: native validity, content problems and the repair loop."""

    _TYPE_FORMATS = {
        "date": "a date as YYYY-MM-DD (e.g. 2024-01-15)",
        "datetime-local": "a date and time as YYYY-MM-DDTHH:MM",
        "month": "a month as YYYY-MM",
        "week": "a week as YYYY-Www",
        "time": "a time as HH:MM",
        "number": "a plain number using digits only (no symbols or spaces)",
        "range": "a number",
        "email": "an email address like user@example.com",
        "tel": "a phone number using digits and +",
        "url": "a full URL starting with https://",
    }

    @classmethod
    def _ff_format_spec(cls, field):
        """The format the field's input type demands ('' when unconstrained)."""
        bits = []
        itype = str(field.get("type") or "").strip().lower()
        fmt = cls._TYPE_FORMATS.get(itype)
        if fmt:
            bits.append("the field expects %s" % fmt)
        if field.get("pattern"):
            bits.append("the value must match the pattern %s" % field["pattern"])
        for key in ("min", "max", "step"):
            if field.get(key):
                bits.append("%s=%s" % (key, field[key]))
        for key, name in (("minlength", "minimum length"),
                          ("maxlength", "maximum length")):
            if field.get(key):
                bits.append("%s %s" % (name, field[key]))
        if field.get("required"):
            bits.append("the field is required")
        return "; ".join(bits)

    @classmethod
    def _ff_format_hint(cls, field, state=None, reason=""):
        """Feedback describing WHY a value must be replaced (repair prompt).

        It names the offending value and demands a DIFFERENT one: a model
        merely told "not accepted" tends to re-emit the same value, the page
        rejects it again, and the field never changes - the correction appears
        to be "thought about" but never applied.  ``reason`` overrides the
        page's own message for a value the page ACCEPTS but that is still
        wrong, so the feedback never blames the page for it.
        """
        bits = []
        msg = (state or {}).get("message")
        if reason:
            bits.append(reason)
        elif msg:
            bits.append("the page says: %s" % msg)
        current = str((state or {}).get("value") or "").strip()
        if current:
            bits.append("the value %r was NOT accepted" % current)
        spec = cls._ff_format_spec(field)
        if spec:
            bits.append(spec)
        if not bits:
            bits.append("the value is not in the format the field accepts")
        return ("Your previous answer was rejected - " + "; ".join(bits)
                + ". Return a DIFFERENT value in the required format.")

    @classmethod
    def _ff_repair_reason(cls, field, value, state, source_text=""):
        """Why a written field must still be corrected ('' when it is fine).

        Native validity only sees what the PAGE rejects, so a small model's
        content mistakes sailed through: a template placeholder it copied
        instead of answering ('[Company]') and an INVENTED identifier - both
        accepted by the page and therefore never repaired.  They are flagged
        here so the SAME re-probe/re-answer loop corrects them.  Whether an
        answer EXPLAINS the question instead of answering it is the semantic
        verdict's call (see ``_ff_semantic_reasons``), not a phrase list.
        """
        if state is not None and state.get("found") is False:
            # The control could not be RESOLVED, so the page has no verdict to
            # reject: 'field not found' says nothing about the VALUE.  Read as a
            # rejection it made the pass re-probe and re-answer fields the form
            # had already accepted (live: "Form Repair: First name* field not
            # found (attempt 1/4)" - a full ComoRAG sweep plus an engine
            # restart per attempt, on a good field).
            return ""
        if state is not None and not state.get("valid", True):
            return state.get("message") or "the page rejected the value"
        text = str(value or "").strip()
        if not text:
            return "the answer was left empty"
        # A filler value the page happily ACCEPTS - the field's own placeholder
        # text, or a value left empty by the page - is not an answer, so it is
        # corrected like a page rejection.
        if _ff_placeholder_value(field, text):
            return ("the answer is still placeholder text %r instead of the "
                    "real value" % text[:60])
        m = _PLACEHOLDER_RE.search(text)
        if m:
            return ("the answer still contains placeholder text %r instead of "
                    "the real value" % m.group(0))
        # A value longer than the field's own limit can NEVER be accepted, so it
        # is a reject reason in its own right: the page showed "Invalid input
        # 133/20" while the repair pass reported nothing to correct and the
        # field kept a rejected value forever (observed: a 133-character answer
        # in a 20-character field, then skipped as 'already filled').
        max_chars = _as_int(field.get("maxlength"), 0)
        if max_chars and len(text) > max_chars:
            return ("the answer is %d characters but the field accepts at most "
                    "%d" % (len(text), max_chars))
        # A URL / email the source does not contain is INVENTED, not retrieved.
        tok = _ff_ungrounded_identifier(text, source_text)
        if tok:
            return ("the answer contains the identifier %r, which does "
                    "not appear anywhere in the source documents" % tok)
        return ""

    # Fields put to the semantic verdict in ONE call.  Beyond this the batch
    # is capped, so a 40-field page never becomes a single 40-line answer.
    _FF_SEMANTIC_BATCH = 20

    def _ff_semantic_reasons(self, targets, field_map, cfg, stop_flag):
        """ONE batched verdict: which answers do not fit their question?

        The rules in ``_ff_repair_reason`` only see native validity,
        placeholders, self-explanations and invented identifiers, so a
        format-valid answer to the WRONG question sails through: a spoken
        language given as a programming language, a job title in a city
        field.  The site's own review step is then the first thing that
        notices - long after the run reported success.

        So the written values go to the node's own model as a numbered list,
        one line back per field.  Returns ``{result_id: reason}`` for the
        flagged fields only; an unavailable model, a truncated answer or an
        unparseable line flags NOTHING (fail open), so this pass can only add
        corrections, never lose one.
        """
        pairs = []
        for res in targets:
            if len(pairs) >= self._FF_SEMANTIC_BATCH:
                break
            # Only what THIS run wrote.  A field the page already held is
            # still a rule-based repair target below, but its value is the
            # PAGE's, not an answer of ours to judge.
            if res.get("status") != "filled":
                continue
            value = str(res.get("value") or "").strip()
            field = field_map.get(res.get("id")) or {}
            label = str(res.get("label") or field.get("label") or "").strip()
            if not value or not label:
                continue
            # A choice widget is judged by its option list, not by meaning.
            if field.get("options"):
                continue
            # A boolean control's answer is settled by the control itself, not
            # by meaning: asking the model to re-judge it would spend a call on
            # a settled answer.
            if str(field.get("kind") or "") == "switch":
                continue
            # A TYPED input states the answer kind in its format rules (a date
            # field is not a city field, a URL field is not a phone number),
            # so only answers whose correctness is purely SEMANTIC are left.
            if str(field.get("type") or "").strip().lower() \
                    in self._TYPE_FORMATS or field.get("pattern"):
                continue
            pairs.append((res.get("id"), label, value))
        if not pairs:
            return {}
        numbered = "\n".join(
            "%d. Question: %s. Answer: %s"
            % (i, label[:160], value[:160])
            for i, (_rid, label, value) in enumerate(pairs, 1)
        )
        prompt = (
            "You check whether each ANSWER answers its QUESTION on a form.\n"
            "A correct format is not enough: an answer can be real text and "
            "still belong to another field, describe the question instead "
            "of answering it, or state that the information is missing. "
            "Judge MEANING only, never length or formatting.\n\n"
            "%s\n\n"
            "For EVERY item write one line: '<number>: OK' when the answer "
            "answers that question, '<number>: NO <short reason>' when it "
            "does not. Nothing else.\n" % numbered
        )
        raw = self._ff_llm_call(prompt, "", cfg, stop_flag)
        verdicts = {}
        for m in re.finditer(
            r"(?im)^\s*[-*.]?\s*(\d+)\s*[:.)]?\s*(ok|no)\b[:\-]?\s*(.*)$",
            raw or "",
        ):
            idx = int(m.group(1))
            if not 1 <= idx <= len(pairs):
                continue
            if m.group(2).lower() == "ok":
                continue
            verdicts.setdefault(
                idx,
                (m.group(3) or "").strip()[:120]
                or "it does not answer the question",
            )
        if not verdicts:
            return {}

        def _clean_reason(t):
            """A reason, with the prompt's OWN template placeholder rejected.

            The judge is asked for '<number>: NO <short reason>', and a small
            model echoes the literal '<short reason>' - which then travelled as
            the repair hint and told the extractor nothing.
            """
            t = (t or "").strip()
            if not t or re.fullmatch(r"<[^<>]{1,40}>", t):
                return "it does not answer the question"
            return t[:120]

        out = {}
        for idx, reason in verdicts.items():
            rid = pairs[idx - 1][0]
            if rid:
                out[rid] = ("the answer does not fit the field: %s"
                            % _clean_reason(reason))
        return out

    def _ff_repair_fields(self, field_map, results, cfg, source_text,
                          documents, stop_flag, driver=None):
        """Post-pass: re-check every written field and CORRECT the rejects.

        After the first pass the page knows what the model could not: a native
        constraint can REJECT the value we wrote, or the widget reformats it.
        Every offending field is re-probed through the SAME ComoRAG probe and
        re-answered WITH the browser's rejection message as feedback, then
        re-checked - so a date answered '2017' is corrected instead of silently
        left wrong.  Returns the number of fields repaired.

        The pass logs its PROGRESS: it re-reads every written field before it
        finds anything to correct, so without a trace the run looks hung.
        """
        repaired = 0
        flagged = 0
        # Ground truth for the invented-identifier check: everything the model
        # was allowed to ground on (documents + upstream text).
        try:
            ground = "\n".join(
                [str(source_text or "")]
                + [str(t) for t in self._ff_doc_texts(documents)]
            )
        except Exception:
            ground = str(source_text or "")
        # A field the harness LEFT ALONE because the page already held a value
        # is a repair target TOO.  Many forms only reveal their rejections once
        # something submits them (a "Review"/"Next" click, or a chain that
        # clicks and loops back), and on that re-entry EVERY field is skipped as
        # "already filled" - so a written-only target list came back empty and
        # the repair pass could never fire on exactly the pass that exists to
        # fix those fields (observed: a 10-field page checked 4, Flagged 0, on a
        # loop whose only purpose was to surface those errors).
        targets = []
        for r in results:
            val = str(r.get("value") or "").strip()
            if not (r.get("status") in ("filled", "failed")
                    or (r.get("status") == "skipped" and r.get("id") and val)):
                continue
            # The N/A this policy writes is a DELIBERATE answer, not a leftover
            # filler to correct - left in, the pass would "repair" the node's
            # own N/A into a fabricated value.
            fld = field_map.get(r.get("id")) or {}
            if (cfg.get("answer_na") and val
                    and val.upper() == _ff_na_value(fld.get("options")).upper()):
                continue
            targets.append(r)
        attempts = max(1, cfg["repair_attempts"])
        total = len(targets)
        if total:
            log_block(
                logger, logging.INFO, "Form Repair Pass",
                "checking %d field(s) for rejected values and wrong "
                "content:\n%s"
                % (total, "\n".join(
                    "%d. %s" % (i, r.get("label") or r.get("id") or "?")
                    for i, r in enumerate(targets, 1))),
            )
        # ONE semantic verdict over the written values, before the per-field
        # walk: the content rules cannot tell a right-format answer to the
        # WRONG question from a correct one.
        semantic = {}
        try:
            semantic = self._ff_semantic_reasons(
                targets, field_map, cfg, stop_flag)
        except Exception as exc:
            log_block(logger, logging.WARNING, "Form Repair Pass",
                      "semantic check skipped: %s" % exc)
        if semantic:
            log_block(
                logger, logging.WARNING, "Form Repair Pass",
                "%d answer(s) do not fit their field:\n%s"
                % (len(semantic), "\n".join(
                    "%s" % (r.get("label") or r.get("id") or "?")
                    for r in targets
                    if r.get("id") in semantic)),
            )
        for idx, res in enumerate(targets, 1):
            if stop_flag and stop_flag():
                log_block(logger, logging.WARNING, "Form Repair Pass",
                          "stopped by user")
                break
            field = field_map.get(res.get("id"))
            if not field:
                continue
            label = res.get("label") or field.get("label") or ""
            self._ff_mark_processing(field, label)
            # A correction must act on the SAME element the fill aimed at, and
            # this pass can run long after a re-render rotated the markers: walk
            # the page tree again (cheap when nothing moved) before reading the
            # field's state or writing to it.
            self._ff_resolve_target(field, cfg, stop_flag)
            state = self._ff_field_state(field, stop_flag, driver)
            reason = (self._ff_repair_reason(field, res.get("value"), state,
                                            ground)
                      or semantic.get(res.get("id"), ""))
            if not reason:
                continue
            flagged += 1
            hint = self._ff_format_hint(field, state, reason=reason)
            # The retrieval probe gets the required FORMAT only, never the
            # rejection TEXT: the log showed the model reading "return a
            # different value" as a search for "a date that is NOT September
            # 2026 or May 2023", so the rejected value became a probe target
            # and the plan chased it instead of the field's real answer.
            spec = self._ff_format_spec(field)
            for attempt in range(attempts):
                if stop_flag and stop_flag():
                    break
                log_block(
                    logger, logging.WARNING, "Form Repair: %s" % label,
                    "%s (attempt %d/%d)"
                    % (reason, attempt + 1, attempts),
                )
                evidence = self._ff_probe(
                    label, cfg, source_text, documents, stop_flag,
                    repair_hint=spec,
                )
                if not evidence:
                    break
                value = self._ff_extract(
                    label, evidence, cfg, stop_flag,
                    options=field.get("options") or None,
                    multiline=str(field.get("tag") or "").lower() == "textarea",
                    format_hint=spec,
                    retry_note=hint + "\n",
                    max_chars=_as_int(field.get("maxlength"), 0),
                    kind=field.get("kind"),
                )
                if not value:
                    break
                if value == str(res.get("value") or "").strip():
                    # The model repeated the SAME answer, so there is nothing to
                    # correct: re-writing it changes nothing, yet the pass went
                    # on to report 'repaired' (live: "attempt 1/4 corrected it
                    # -> '[No photo provided]'"), and the field was flagged
                    # again on the next pass - a retry storm of full ComoRAG
                    # sweeps over an answer that never moves.
                    log_block(
                        logger, logging.WARNING, "Form Repair: %s" % label,
                        "the re-answered value is unchanged (%r) - nothing to "
                        "correct" % value,
                    )
                    break
                if not self._ff_write_web(field, value, stop_flag, driver):
                    # The page REFUSED this value (a typed input clears it) -
                    # keep the honest status and let the next attempt try again.
                    log_block(
                        logger, logging.WARNING, "Form Repair: %s" % label,
                        "the page refused %r (attempt %d/%d)"
                        % (value, attempt + 1, attempts),
                    )
                    res["status"] = "failed"
                    continue
                res["value"] = value
                state = self._ff_field_state(field, stop_flag, driver)
                still = self._ff_repair_reason(field, value, state, ground)
                if still:
                    # Say WHAT was tried: without it a failed correction is
                    # unanalyzable (the attempt header only carries the reason
                    # the pass STARTED from).
                    log_block(
                        logger, logging.WARNING, "Form Repair: %s" % label,
                        "attempt %d/%d wrote %r - still rejected: %s"
                        % (attempt + 1, attempts, value, still),
                    )
                    res["status"] = "failed"
                    continue
                res["status"] = "repaired"
                log_block(
                    logger, logging.INFO, "Form Repair: %s" % label,
                    "attempt %d/%d corrected it -> %r"
                    % (attempt + 1, attempts, value),
                )
                repaired += 1
                break
        if total:
            log_table(
                logger, logging.INFO, "Form Repair Summary",
                [("Checked", total), ("Flagged", flagged),
                 ("Corrected", repaired)],
            )
        return repaired
