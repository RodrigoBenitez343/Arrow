"""Web substrate: enumerate, write/read a field, the execution overlay."""

import json
import logging
import sys
import time

from .common import (
    _ff_na_value,
    _ff_option_exact,
    _ff_placeholder_value,
    log_block,
    log_table,
    logger,
)


class FormFillerWebMixin:
    """Web substrate: enumerate, write/read a field, the execution overlay."""

    # Settle gate: how long to wait for the picked container / open popup to
    # HOLD a field, and how long the DOM must be mutation-quiet first (a wizard
    # that clicked 'next' swaps its body for a spinner; the fields return within
    # a second or two).
    _FF_SETTLE_TIMEOUT = 10.0
    _FF_SETTLE_QUIET = 0.35

    # How long to wait for a typeahead's popup to render after this node opens
    # it while reading a combobox's option list (see _ff_combo_options).
    _FF_COMBO_OPTIONS_TIMEOUT = 3.0

    def _ff_scan_web(self, driver, web_actions, scope_selectors, in_frame=False):
        """Run the field enumerator once; parsed field dicts ([] on failure).

        ``in_frame`` roots the scope search at the CURRENT document - the caller
        has entered the picked container's iframe, and the plain root climb
        would otherwise look it up in the base document.
        """
        try:
            raw = driver.execute_script(
                web_actions.JS_ENUMERATE_FORM_FIELDS,
                list(scope_selectors or []),
                bool(in_frame),
            )
        except Exception as exc:
            log_block(logger, logging.ERROR, "Form Enumerate",
                      "field enumeration failed: %s" % exc)
            return []
        try:
            fields = json.loads(raw) if isinstance(raw, str) else (raw or [])
        except Exception as exc:
            log_block(logger, logging.ERROR, "Form Enumerate",
                      "field enumeration returned invalid JSON: %s" % exc)
            return []
        return [f for f in fields if isinstance(f, dict)]

    def _ff_dom_quiet(self, driver, web_actions, stop_flag, quiet_s, budget_s):
        """True once the page has not mutated for ``quiet_s`` (best-effort).

        True when the state cannot be measured (no observer, script error), so
        an unmeasurable page is never blocked.  False when ``budget_s`` elapsed
        while the page was STILL mutating.
        """
        deadline = time.time() + max(0.0, budget_s)
        while True:
            try:
                age = int(driver.execute_script(web_actions.JS_SETTLE_READ))
            except Exception:
                return True
            if age < 0 or age >= quiet_s * 1000:
                return True
            if self._ff_halt(stop_flag) or time.time() >= deadline:
                return False
            time.sleep(0.05)

    def _ff_wait_and_scan(self, driver, web_actions, scope_selectors, in_frame,
                          stop_flag):
        """GATE: wait for the page to settle, then enumerate.

        The chain clicks 'next', the modal re-renders, and the very next
        enumeration used to run mid-transition: the popup held no field yet and
        the scan reached the global 'Search' box BEHIND the modal.  The gate
        therefore (1) waits for the DOM to stop mutating and (2), while a PICKED
        container may still be loading, keeps re-scanning it until it HOLDS a
        field.

        With a scope picked there is NO whole-page fallback: the pick is where
        the user said the form is, so an empty scope yields an empty field list
        (a control outside it must never be acted on).  Without a scope the
        scan is exactly the unscoped one.  Nothing here blocks past
        ``_FF_SETTLE_TIMEOUT`` or an ESC.
        """
        try:
            driver.execute_script(web_actions.JS_SETTLE_ARM)
        except Exception:
            pass
        timeout = float(getattr(self, "_FF_SETTLE_TIMEOUT", 6.0) or 6.0)
        deadline = time.time() + timeout
        settled = True
        while True:
            # (1) Wait for the page to stop mutating before reading it.
            settled = self._ff_dom_quiet(
                driver, web_actions, stop_flag, self._FF_SETTLE_QUIET,
                min(0.5, max(0.1, deadline - time.time())),
            )
            # (2) Scope-major: the first candidate that actually CONTAINS fields
            # wins (a candidate resolving to the wrong / empty node must not win
            # just because it matched something).
            for sel in scope_selectors:
                got = self._ff_scan_web(driver, web_actions, [sel], in_frame)
                if got:
                    return got, sel
            if not scope_selectors:
                return (self._ff_scan_web(driver, web_actions, [], in_frame),
                        "whole page")
            # A page that has SETTLED yet still holds no field means the picked
            # container is stale/wrong - keep waiting only while it may still be
            # loading (the DOM is mutating).
            if self._ff_halt(stop_flag) or settled or time.time() >= deadline:
                break
            time.sleep(0.1)
        # A PICKED scope is WHERE the user said the form is.  If it holds no
        # field, do NOT scan the whole page: that is exactly what reached the
        # site's global 'Search' box behind the modal (live: 'Resolved via:
        # whole page' -> 'Form Fields to Fill (1): Search').  An empty pass is
        # correct here - the chain's own loop re-runs this node.
        log_block(
            logger, logging.WARNING, "Form Scope",
            "the picked container held no field (%s) - NOT scanning the whole "
            "page (a control outside the picked scope, e.g. a global 'Search' "
            "box behind the modal, must never be acted on); leaving this pass "
            "empty" % ("page settled" if settled else "waited %.1fs" % timeout),
        )
        return [], "(picked scope held no field)"

    def _ff_enumerate_web(self, cfg, stop_flag):
        if self._ff_halt(stop_flag):
            return []
        try:
            from .....web import actions as web_actions
        except Exception as exc:
            log_block(logger, logging.ERROR, "Form Enumerate",
                      "web modules unavailable: %s" % exc)
            return []
        self._ff_scope_ctx = self._ff_scope_frame(cfg.get("web_scope"))
        driver = self._ff_shared_driver()
        if driver is None:
            log_block(logger, logging.ERROR, "Form Enumerate",
                      "no shared browser available")
            return []
        scope_selectors = self._ff_scope_selectors(cfg.get("web_scope"))
        # A new pass reads the DOM again, so the tree resolved for the previous
        # pass - and its markers, which are node ORDINALS - must not survive
        # into this one.
        self._ff_forget_tree()
        in_frame = bool(getattr(self, "_ff_scope_in_frame", False))
        # Settle GATE: enumerate only once the page has stopped mutating and the
        # target layer holds a field - never from behind an open popup.
        fields, via = self._ff_wait_and_scan(
            driver, web_actions, scope_selectors, in_frame, stop_flag,
        )
        ctx = getattr(self, "_ff_scope_ctx", None) or {}
        log_table(
            logger, logging.INFO, "Form Scope",
            [
                ("Scope candidates", scope_selectors or "(whole page)"),
                ("Resolved via", via),
                ("Window / frame", "window #%s, %d frame level(s)%s" % (
                    ctx.get("window_ordinal"),
                    len(ctx.get("frame_path") or []),
                    " (entered)" if in_frame else "")),
                ("Fields found", len(fields)),
            ],
        )
        return fields

    def _ff_shared_driver(self):
        """The ONE browser session for this run (resolved on first use).

        Every `_web_driver()` call re-attaches - and re-spawns - a browser:
        ~5s and a reusing/spawn/state-saved log trio each.  Doing that per
        OPERATION (2 per field plus 1 per repair target) streams those trios
        with no other output, which is indistinguishable from an endless loop.
        Resolved LAZILY so a run with nothing to write never opens a browser.
        """
        driver = getattr(self, "_ff_web_driver", None)
        if driver is None:
            try:
                from ....llm_executor_resources.inputs import _web_driver
                driver = _web_driver()
            except Exception as exc:
                log_block(logger, logging.WARNING, "Form Browser",
                          "shared browser unavailable: %s" % exc)
                driver = None
            try:
                self._ff_web_driver = driver
            except Exception:
                pass
        # Applied on EVERY resolve (self-latched): a driver pre-seeded on the
        # harness, or resolved later, still gets the picked window/iframe.
        self._ff_apply_scope_context(driver)
        if driver is not None:
            self._ff_enable_overlay(driver)
        return driver

    def _ff_apply_scope_context(self, driver):
        """Enter the picked container's WINDOW / IFRAME once per run.

        A container picked inside a POPUP WINDOW (``_window_ordinal``) or an
        IFRAME (``frame_path``) is not reachable from the base document, so it
        must be entered before any op - otherwise the popup is ignored and the
        fields of the page UNDER it are enumerated.  Mirrors web-sequence
        replay (``switch_to_window_ordinal`` + ``enter_recorded_frame``).
        Best-effort: a stale path leaves the driver on ``default_content`` and
        the whole-page fallback still runs.
        """
        if driver is None or getattr(self, "_ff_scope_applied", False):
            return
        self._ff_scope_applied = True
        ctx = getattr(self, "_ff_scope_ctx", None)
        if not ctx:
            return
        try:
            from .....web import actions as web_actions
        except Exception:
            return
        ordinal = ctx.get("window_ordinal")
        if ordinal:
            try:
                web_actions.switch_to_window_ordinal(driver, ordinal, wait_s=0.5)
            except Exception as exc:
                logger.debug("[FORM] Popup window entry failed: %s", exc)
        if ctx.get("frame_path"):
            import types
            evt = types.SimpleNamespace(
                frame_path=ctx["frame_path"], frame_index_path=[])
            try:
                self._ff_scope_in_frame = bool(
                    web_actions.enter_recorded_frame(driver, evt, timeout=3.0))
            except Exception as exc:
                self._ff_scope_in_frame = False
                logger.debug("[FORM] Popup iframe entry failed: %s", exc)
            log_block(
                logger, logging.INFO, "Form Scope",
                "picked container lives in an iframe: entered %d level(s) "
                "(%s, cross_origin=%s)"
                % (len(ctx["frame_path"]),
                   "entered" if self._ff_scope_in_frame else
                   "NOT reachable - searching the top document",
                   ctx.get("cross_origin_frame")),
            )

    def _ff_enable_overlay(self, driver):
        """Turn on the in-page EXECUTION overlay ONCE per run.

        The same orange box + virtual-cursor trail every web node shows during
        playback (``player.web.exec_overlay``), so a form fill is visible in the
        page instead of happening invisibly.  Best-effort throughout.
        """
        if getattr(self, "_ff_overlay_on", False):
            return
        self._ff_overlay_on = True
        try:
            from .....web import exec_overlay
            exec_overlay.enable(driver)
        except Exception as exc:
            logger.debug("[FORM] Execution overlay unavailable: %s", exc)

    def _ff_disable_overlay(self):
        """Remove the in-page overlay when the node is done (best-effort)."""
        driver = getattr(self, "_ff_web_driver", None)
        if driver is None or not getattr(self, "_ff_overlay_on", False):
            return
        self._ff_overlay_on = False
        try:
            from .....web import exec_overlay
            exec_overlay.disable(driver)
        except Exception as exc:
            logger.debug("[FORM] Execution overlay cleanup failed: %s", exc)

    def _ff_mark(self, driver, field, caption, selectors=None):
        """Box this field in the page's execution overlay before touching it.

        Reuses the engine's own ``JS_EXEC_MARK`` with the field's selector
        ladder (the runtime's identity for the control), so the overlay, the
        write and the repair all agree on WHICH element is being acted on.
        """
        if driver is None:
            return
        try:
            from .....web import exec_overlay
            driver.execute_script(
                exec_overlay.JS_EXEC_MARK,
                selectors if selectors is not None
                else self._ff_field_selectors(field),
                int(field.get("type_index") or 0),
                str(caption or ""),
            )
        except Exception as exc:
            logger.debug("[FORM] Execution overlay mark failed: %s", exc)

    def _ff_mark_processing(self, field, label):
        """Name the field being WORKED ON in the overlay (best-effort).

        The overlay used to be marked only at WRITE time, so for the whole
        (slow) probe/extract of a field the banner kept showing the PREVIOUS
        field - it looked like the wrong question was being processed.
        Uses only the ALREADY-memoised session: a run with no browser open
        must not be forced to acquire one just to draw a label.
        """
        driver = getattr(self, "_ff_web_driver", None)
        if driver is not None:
            self._ff_mark(driver, field, "form: %s" % (label or ""))

    def _ff_write_web(self, field, value, stop_flag, driver=None):
        try:
            from .....web import actions as web_actions
            from ....llm_executor_resources.inputs import _web_driver
        except Exception as exc:
            log_block(logger, logging.ERROR, "Form Write",
                      "web modules unavailable: %s" % exc)
            return False
        # One shared browser session for the run (see _ff_shared_driver)
        driver = driver or self._ff_shared_driver()
        if driver is None:
            return False
        if self._ff_halt(stop_flag):
            return False
        selectors = self._ff_field_selectors(field)
        want = int(field.get("type_index") or 0)
        kind = (field.get("kind") or "text").lower()
        # Box the field in the in-page execution overlay BEFORE the value lands,
        # exactly like a web node boxes the element it is about to hit.
        self._ff_mark(
            driver, field,
            "form: %s" % (field.get("label") or field.get("id") or ""),
            selectors=selectors,
        )
        try:
            if kind in ("choice", "switch"):
                ok = driver.execute_script(
                    web_actions.JS_SET_CHOICE, selectors, want, value,
                )
            else:
                ok = driver.execute_script(
                    web_actions.JS_SET_FIELD, selectors, want, value, kind,
                )
            if not ok:
                # PROVENANCE: name the module file that actually served this call
                # and whether the ladder came from the field or was computed.  A
                # stale process / another copy otherwise looks identical to a
                # logic bug (a ladder this code cannot produce).
                try:
                    _mod = sys.modules.get(type(self).__module__)
                    _src = getattr(_mod, "__file__", "?")
                except Exception:
                    _src = "?"
                log_block(
                    logger, logging.WARNING, "Form Write: %s" % field.get("label"),
                    "target unresolved or not editable (kind=%s, "
                    "selectors=%s, pre=%s, want=%s, src=%s)"
                    % (kind, selectors, field.get("selectors"), want, _src),
                )
            # A write re-renders the form, and the tree's markers are node
            # ORDINALS: the next field must re-read the page, not reuse them.
            self._ff_forget_tree()
            return bool(ok)
        except Exception as exc:
            log_block(
                logger, logging.ERROR, "Form Write: %s" % field.get("label"),
                "write failed (selectors=%s want=%s): %s"
                % (selectors, want, exc),
            )
            # Drop a dead session so the next write re-attaches instead of
            # failing every remaining field.
            self._ff_web_driver = None
            self._ff_forget_tree()
            return False

    def _ff_field_state(self, field, stop_flag, driver=None):
        """One field's ``{found, value, valid, message}`` (None if unreachable).

        ``valid``/``message`` are the field's NATIVE constraint state
        (type=email/date/number, pattern, min/max/step, required), so a value the
        page REJECTS - a date answered '2017', an out-of-range number, a missing
        required field - is visible to the repair pass.
        """
        try:
            from .....web import actions as web_actions
        except Exception:
            return None
        driver = driver or self._ff_shared_driver()
        if driver is None:
            return None
        if self._ff_halt(stop_flag):
            return None
        try:
            raw = driver.execute_script(
                web_actions.JS_FIELD_STATE,
                self._ff_field_selectors(field),
                int(field.get("type_index") or 0),
                (field.get("kind") or "text").lower(),
            )
        except Exception as exc:
            logger.debug("[FORM] Field state failed for '%s': %s", field.get("label"), exc)
            return None
        try:
            data = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def _ff_read_web(self, field, stop_flag, driver=None):
        state = self._ff_field_state(field, stop_flag, driver)
        if state is None or not state.get("found", True):
            return None
        return "" if state.get("value") is None else str(state.get("value"))

    def _ff_current_value(self, field, stop_flag, driver=None, cfg=None):
        """The value the field ALREADY holds, or "" when empty / unknown.

        Skipping is the HARNESS's decision, never the model's: a field the page
        already fills is left untouched - no probe, no model call, no write.
        Unknown (no browser, unreadable state) reads as empty so the field is
        handled normally rather than silently left blank.

        A PROMPT / placeholder value is NOT an answer, so it reads as empty and
        the field is FILLED: a <select> shows "Selecciona una opción" until the
        user picks something, and "N/A" / "-" / the field's own placeholder
        text is filler, not content.  Treating those as real values is what made
        the harness skip exactly the fields that needed filling - the dominant
        dropdown failure (observed: 4 of 6 fields skipped on a form whose selects
        all sat on their prompt option).
        """
        state = self._ff_field_state(field, stop_flag, driver)
        if not state or not state.get("found", True):
            return ""
        value = state.get("value")
        text = "" if value is None else str(value).strip()
        # The field holding the N/A this policy writes IS ANSWERED: the node
        # opted into N/A as a legitimate value, and re-answering it on every
        # pass is exactly the "it fails to understand the form has been filled
        # already" failure (a text box asking for a photo can never hold a
        # better answer).  Only under the policy - with it off, N/A stays a
        # leftover filler and the field is filled normally.
        if (text and cfg is not None and cfg.get("answer_na")
                and text.upper() == _ff_na_value(field.get("options")).upper()):
            return text
        if not text or _ff_placeholder_value(field, text):
            return ""
        # A COMBOBOX can only HOLD one of its OWN options: a value the list does
        # not contain is uncommitted free text the page never accepted.  Read as
        # empty, so the field is REFILLED - otherwise every later pass skipped it
        # as "already filled" and the field became un-salvageable (observed: a
        # city combo left holding 'riverside' was skipped forever).
        opts = field.get("options") or []
        if (opts and (field.get("kind") or "").lower() == "combo"
                and not _ff_option_exact(text, opts)):
            log_block(
                logger, logging.INFO, "Form Field: %s" % field.get("label"),
                "the field holds %r, which is NOT one of its %d option(s) - "
                "treating it as empty and filling it again"
                % (text, len(opts)),
            )
            return ""
        return text

    def _ff_combo_options(self, field, stop_flag, driver=None):
        """Read a combobox's OPTION LIST, OPENING the popup when not rendered.

        A typeahead renders its options only once OPEN, so enumeration saw none
        (the field table read ``Options: (free text)``) and the model answered
        free text the page refused to submit.  This asks the page to peek, polls
        until the list renders, then closes the popup again.  Best-effort: no
        browser, no popup or an unreadable list returns [] (nothing changes).
        """
        try:
            from .....web import actions as web_actions
        except Exception:
            return []
        driver = driver or self._ff_shared_driver()
        if driver is None or self._ff_halt(stop_flag):
            return []
        selectors = self._ff_field_selectors(field)
        want = int(field.get("type_index") or 0)
        timeout = float(getattr(self, "_FF_COMBO_OPTIONS_TIMEOUT", 3.0) or 3.0)
        deadline = time.time() + timeout
        opts = []
        open_once = True
        while True:
            try:
                raw = driver.execute_script(
                    web_actions.JS_COMBO_OPTIONS, selectors, want, open_once,
                )
            except Exception as exc:
                logger.debug("[FORM] Combo option read failed: %s", exc)
                break
            try:
                got = json.loads(raw) if isinstance(raw, str) else (raw or [])
            except Exception:
                got = []
            opts = [str(o) for o in (got or []) if str(o).strip()]
            if opts or self._ff_halt(stop_flag) or time.time() >= deadline:
                break
            open_once = False
            time.sleep(0.15)
        # Close the popup this call may have opened - never leave it hanging
        # over the form for the next field's write.
        try:
            driver.execute_script(web_actions.JS_KEY, "Escape", [])
        except Exception:
            pass
        try:
            driver.execute_script(
                "var a=document.activeElement; if(a&&a.blur)a.blur();"
                "return true;")
        except Exception:
            pass
        return opts
