"""The node shell: the field loop, the per-field table and the output."""

import json
import logging
import os
import re
import time

from .common import (
    _FF_LIST_KINDS,
    _as_int,
    _ff_na_value,
    _ff_norm,
    _ff_option_exact,
    _ff_option_match,
    _ff_parse_learned_answers,
    _ff_source_sig,
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
            ("Facts / excerpts", "%s / %s" % (
                getattr(self, "_ff_last_facts", 0),
                getattr(self, "_ff_last_excerpts", 0),
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

    # ── Ask the user / learn the answer (opt-in) ────────────────────────────

    def _ff_knowledge_text(self, path):
        """Read the corrections file (the node's learned answers); '' if unreadable."""
        if not path:
            return ""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read().strip()
        except Exception:
            return ""

    def _ff_corrections_path(self, node_id):
        """The corrections document this node OWNS - always derived, never set.

        Stored beside the chain it belongs to (the same place a Code node keeps
        its own file); when no chain directory is available, under the app's
        durable runtime directory.  There is no user-set path and no override:
        a node that learns an answer always owns somewhere to keep it, and ''
        only if even the runtime directory cannot be resolved.
        """
        stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(node_id or "form")) or "form"
        try:
            base = getattr(self.sequence_executor, "chain_file_dir", "") or ""
        except Exception:
            base = ""
        if not base:
            try:
                from AI.runtime_paths import get_runtime_dir
                base = os.path.join(get_runtime_dir(), "corrections")
            except Exception:
                base = ""
        return os.path.join(base, "%s_corrections.md" % stem) if base else ""

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

    def _ff_learn(self, label, answer, path, source_text):
        """Learn an answered field into the corrections file and the sources.

        The entry is appended to the corrections document (persistent: read
        back on the next run) and returned inside the run's ``source_text``, so
        the fields AFTER this one can already retrieve it.
        """
        entry = "## %s\n%s\n" % (label, answer)
        if not path:
            log_block(logger, logging.INFO, "Form Filler Knowledge",
                      "no corrections file could be located - '%s' was used "
                      "for this run only" % label)
        else:
            try:
                _dir = os.path.dirname(path)
                if _dir:
                    os.makedirs(_dir, exist_ok=True)
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write("\n" + entry)
                log_block(logger, logging.INFO, "Form Filler Knowledge",
                          "learned '%s' into the node-owned corrections file: %s"
                          % (label, path))
            except Exception as exc:
                log_block(logger, logging.WARNING, "Form Filler Knowledge",
                          "could not write the corrections file %s: %s"
                          % (path, exc))
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
                ("Retrieval", "top-k %s, %s probe chars, %s base chars" % (
                    cfg["probe_top_k"], cfg["probe_char_budget"],
                    cfg["probe_context_chars"])),
                ("ComoRAG consolidation", "%s (%s cycle(s))" % (
                    cfg["consolidate"], cfg["probe_cycles"])),
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

        # Source context: attached documents (ComoRAG) + upstream node outputs.
        documents = data.get("rag_documents") or []
        if isinstance(documents, str):
            try:
                documents = json.loads(documents)
            except Exception:
                documents = []
        source_text = self._ff_gather_source_text(node)
        # The corrections document this node OWNS - always derived, there is no
        # user-set path: the answers the user gives for fields nothing could
        # ground are read back as a source so a field already answered is never
        # asked again (see _ff_corrections_path).
        corrections_path = self._ff_corrections_path(node_id)
        knowledge_text = self._ff_knowledge_text(corrections_path)
        if knowledge_text:
            source_text = ("%s\n\n%s" % (knowledge_text, source_text)).strip()
        # The learned answers keyed by field LABEL: a field the user already
        # answered once is resolved from here, so it is never asked again.
        learned_answers = _ff_parse_learned_answers(knowledge_text)
        log_table(
            logger, logging.INFO, "Form Filler Sources",
            [("Document", str(_d)) for _d in documents]
            + [("Corrections file", ("%s (%d chars)"
                % (corrections_path, len(knowledge_text)))
                if corrections_path else "(none)")]
            + [("Upstream context", "%d chars" % len(source_text or ""))],
        )

        aborted = False

        # Idempotence across re-entries: the chain loops back into this node
        # whenever a field does not land, and each pass used to re-run the FULL
        # ComoRAG sweep for every field — one field burned 512s, the same
        # 4-field page was handled four identical times, and the user's ESC
        # then discarded the whole sweep.  Outcomes are remembered per SOURCE
        # STATE: a field that already returned no evidence is not swept again
        # while the sources are unchanged, and whenever the sources change (a
        # learned answer lands in the knowledge file) the memory is dropped so
        # the field IS retried.
        # ponytail: keyed on the SOURCES, not the page — a DIFFERENT page with
        # the same sources and an unfilled field degrades to ask/skip (never to
        # a wrong value).  Add a page/field signature to the key if that is ever
        # observed.
        _state_key = f"node_{node_id}_form_state" if node_id else ""
        _sig = _ff_source_sig(documents, source_text)
        _prior = {}
        try:
            _mem = (self.llm_executor.get_variable(_state_key)
                    if _state_key else None)
            if isinstance(_mem, dict) and _mem.get("sig") == _sig:
                _prior = dict(_mem.get("attempted") or {})
        except Exception:
            _prior = {}
        if _prior:
            log_block(
                logger, logging.INFO, "Form Filler Memory",
                "%d field(s) already attempted against unchanged sources — "
                "re-using the outcome instead of re-sweeping:\n%s"
                % (len(_prior),
                   "\n".join("- %s: %s" % (k, v)
                             for k, v in _prior.items())),
            )

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
            self._ff_last_facts = 0
            self._ff_last_excerpts = 0
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
            current = ""
            if mode != "desktop":
                current = self._ff_current_value(field, stop_flag, cfg=cfg)
                if current and kind not in _FF_LIST_KINDS:
                    results.append({"id": field.get("id"), "label": label,
                                    "value": current, "status": "skipped"})
                    self._ff_log_field(
                        field, label, cfg, "skipped", value=current,
                        options=options,
                        note="already filled - left unchanged",
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
            learned = learned_answers.get(_ff_norm(label).strip("*? .:"), "")
            learned_value = ""
            if learned:
                if not options:
                    learned_value = learned
                elif _ff_option_exact(learned, options):
                    learned_value = _ff_option_match(learned, options) or learned
                else:
                    from AI.laya_hooks import choose_option
                    _idx, _answered = choose_option(label, learned,
                                                   [str(o) for o in options])
                    if _idx is not None and 0 <= _idx < len(options):
                        learned_value = str(options[_idx])
            # A field with a record is answered from it - never re-asked.
            self._ff_no_ask = bool(learned)
            if learned_value:
                self._ff_last_route = "learned: answered for this field before"

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
                evidence = self._ff_probe(
                    label, cfg, source_text, documents, stop_flag,
                )
            if learned and not learned_value:
                # The record pins NO option on its own: the field passes through
                # the usual pipeline with the RECORD as its deciding context.
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
                # or the next pass pays a full ComoRAG sweep again.
                na_from_empty = True
                # 3a-bis. ASK THE USER (opt-in): nothing in the context grounds
                # this field, so ask - the answer fills it and is learned into
                # the knowledge file for later runs.
                note = ""
                value = self._ff_ask_user(label, options, format_spec, cfg,
                                          stop_flag, kind=kind)
                if value:
                    note = "no context - answered by the user"
                    source_text = self._ff_learn(label, value, corrections_path,
                                                 source_text)
                # Nothing grounded the field: the MODEL reads the control (its
                # own options are handed to it) and returns the answer that
                # means none / no when the control has one.  The harness never
                # classifies the question itself - a verb list is what kept
                # this English-only.
                if (not value and cfg["answer_no"]
                        and not self._ff_halt(stop_flag)):
                    value = self._ff_extract(
                        label, "", cfg, stop_flag, options=options,
                        multiline=multiline, format_hint=format_spec,
                        max_chars=_as_int(field.get("maxlength"), 0),
                        kind=kind,
                        retry_note=(
                            "The sources hold NOTHING for this field. If the "
                            "control offers an answer meaning none / no / "
                            "not applicable, return it EXACTLY as the control "
                            "spells it; otherwise reply exactly N/A.\n"
                        ),
                    )
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
                        source_text = self._ff_learn(label, value,
                                                     corrections_path, source_text)
                if value and _ff_na_value(options) == value:
                    note = "the field could not be answered - wrote %r" % value
                if not value:
                    # The context held something but grounded no value: ask the
                    # user before skipping / answering No.
                    value = self._ff_ask_user(label, options, format_spec, cfg,
                                              stop_flag, kind=kind)
                    if value:
                        note = "nothing grounded - answered by the user"
                        source_text = self._ff_learn(label, value,
                                                     corrections_path, source_text)
                if not value and cfg["answer_na"] and _ff_na_value(options):
                    value = _ff_na_value(options)
                    note = ("the model grounded nothing and the field cannot "
                            "be answered - wrote %r" % value)
                    _why = getattr(self, "_ff_last_reject", "")
                    if _why:
                        note = ("the answer was discarded (%s) and nothing "
                                "grounded replaced it - wrote %r" % (_why, value))
                if not value and learned:
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

            # 3c-pre. A LIST-BACKED field the page ALREADY holds the SAME
            # answer for is left alone: re-writing an identical option costs a
            # write + a re-ask for nothing, and a big country select would churn.
            if (current and kind in _FF_LIST_KINDS
                    and _ff_norm(value) == _ff_norm(current)):
                results.append({"id": field.get("id"), "label": label,
                                "value": current, "status": "skipped"})
                self._ff_log_field(
                    field, label, cfg, "skipped", value=current,
                    options=options, note="already filled - left unchanged",
                )
                continue

            # 3c. WRITE into the field's OWN target (runtime-owned binding)
            if mode == "desktop":
                wrote = self._ff_write_desktop(field, value, cfg, stop_flag)
                read_back = None
            else:
                wrote = self._ff_write_web(field, value, stop_flag)
                # Read-back only feeds the verify pass — skip it when disabled.
                read_back = (
                    self._ff_read_web(field, stop_flag)
                    if (wrote and cfg["verify"]) else None
                )

            # 3d. VERIFY (default on): a read-back that is EMPTY - or, for a
            # choice, that is NOT the option the answer named - re-asks THIS
            # field once.  A click can resolve to the wrong control (the page
            # holds several with the same label), and the resulting non-empty
            # read-back would otherwise be reported as a correct selection.
            if cfg["verify"] and wrote and mode == "web":
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
                        wrote = self._ff_write_web(field, value2, stop_flag) or wrote
                        value = value2
                        read_back = self._ff_read_web(field, stop_flag)
                        landed = (read_back or "").strip()
                        if (not landed
                                or not self._ff_choice_landed(landed, value,
                                                              options)):
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

        # Remember this pass's per-field outcomes for the next entry into this
        # node (the chain loops back whenever a field does not land).
        try:
            _attempted = dict(_prior)
            for _r in results:
                _st = _r.get("status")
                if _st == "skipped" and not (_r.get("value") or ""):
                    _attempted[_r.get("label")] = "no_evidence"
                elif _r.get("na_from_empty"):
                    _attempted[_r.get("label")] = "no_evidence"
                elif _st in ("filled", "failed", "repaired"):
                    _attempted[_r.get("label")] = _st
            if _state_key:
                self.llm_executor.set_variable(
                    _state_key, {"sig": _sig, "attempted": _attempted},
                )
        except Exception as exc:
            log_block(logger, logging.DEBUG, "Form Filler Memory",
                      "could not store the per-field outcomes: %s" % exc)

        # 4. REPAIR: re-check every written field's NATIVE validity and correct
        # the rejects with the same ComoRAG probe - a date answered '2017' is
        # corrected instead of silently left wrong.  Never after an abort: ESC
        # must end the node, not start a second pass.
        repaired = 0
        if aborted:
            log_block(
                logger, logging.WARNING, "Form Filler",
                "STOPPED BY USER (ESC) - %d of %d field(s) handled, no repair "
                "pass" % (len(results), len(fields)),
            )
        elif mode != "desktop" and cfg["repair"] and results:
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
        payload = json.dumps(summary, ensure_ascii=False)
        try:
            if node_id:
                self.llm_executor.set_variable(f"node_{node_id}_output", payload)
                self.llm_executor.set_variable(f"node_{node_id}_context", payload)
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
        return self._ff_next_node(node)
