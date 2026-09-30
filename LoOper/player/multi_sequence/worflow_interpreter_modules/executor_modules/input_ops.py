import os
import logging
logger = logging.getLogger(__name__)


class InputMixin:
    """Execution logic for Input nodes.

    An Input node is a **value injector**: it resolves a value by one of the
    strategies below (in priority order), then the ``passthrough`` flag
    decides whether that value is typed on screen or forwarded downstream.
    Behavior is determined by topology and configuration — not by position
    in the chain.  ``passthrough`` works identically on any Input node,
    anywhere.

    Value resolution (priority order):
      **Agent-modifiable** (``agent_modifiable`` set, agent mode):
        The LLM reasons the value from the node label and chain context.

      **Default value** (``default_value`` set, not agent-modifiable, no
      user_prompt):
        The configured value is used as-is.

      **Pure carry node** (no default, no user_prompt, not
      agent-modifiable):
        Carries whatever is on the upstream ``input`` connection, or
        injects the shared ``_chain_input_context`` variable.  This is
        how every Input node feeds its connected nodes and how a
        loop-back re-injects earlier content — a general topology rule,
        independent of agent mode.  Output nodes are excluded as sources:
        they are sinks that expose results to the outside (agent overlay /
        popup) and produce no data for chain execution.

      **User prompt** (``user_prompt`` set):
        Asks the user for fresh input via the configured prompt —
        conversationally via ``_ask_user_callback`` in agent mode, via a
        ``QInputDialog`` popup in manual mode.  Falls back to
        ``default_value`` if the user replies empty.

    An Input node resolves by the priority cascade above; every configuration
    maps to a strategy.

    ``passthrough`` is independent of value resolution:
      - ``passthrough=False`` → the resolved value is typed on screen via
        ``action_handlers``.
      - ``passthrough=True`` → nothing is typed; the value is sent to the
        node connected to this node's output port.
    In both modes the value is stored as a variable for downstream
    consumption.

    A pure carry node that has no upstream connection value and no
    ``_chain_input_context`` to inject FAILS and stops the chain instead of
    silently producing an empty value.
    """

    def _execute_input_node(self, node, stop_flag):
        """Execute an Input node — resolve its value and type it on screen (or forward it).

        An Input node is a **value injector**: it resolves a value by one of
        the priority-ordered strategies below, then ``passthrough`` decides
        what happens to it.  Resolution is driven by topology + configuration,
        never by the node's position in the chain.

        Value resolution (priority order):
          1. ``agent_modifiable`` (agent mode) → LLM reasons the value.
          2. ``default_value`` set (not agent-modifiable, no user_prompt) →
             the configured default is used verbatim.
          3. Pure carry node (no default, no user_prompt, not
             agent-modifiable) → carry the upstream ``input`` connection,
             or inject the shared ``_chain_input_context``.
          4. ``user_prompt`` set → ask the user, falling back to
             ``default_value`` if they reply empty.

        ``passthrough`` is independent of value resolution.  It only controls
        the **write-vs-forward** gate:
          - ``passthrough=False`` → the resolved value is typed on screen.
          - ``passthrough=True`` → nothing is typed; the resolved value is
            sent downstream to the connected node via the output port.
        In both modes the value is also stored as a variable for downstream
        consumption.

        A pure carry node with no upstream connection value and no
        ``_chain_input_context`` to inject FAILS and stops the chain, rather
        than silently producing an empty value on screen or downstream.

        Args:
            node (dict): The Input node from the workflow graph.
            stop_flag (callable): Stop flag function.

        Returns:
            str: Next node ID to execute, or ``__done__`` if no output.
        """
        node_data = node.get('data', {})
        node_id = (node.get('id') or node.get('node_id')
                   or node_data.get('node_id') or node_data.get('id'))

        label = node_data.get('label', '') or ''
        default_value = node_data.get('default_value', '') or ''
        user_prompt = node_data.get('user_prompt', '') or ''
        passthrough = bool(node_data.get('passthrough', False))
        agent_modifiable = bool(node_data.get('agent_modifiable', False))
        # Web mode: type the resolved value into the chain's focused browser
        # editable instead of the desktop (mirrors the LLM node's web_mode).
        # Only meaningful when not passthrough — passthrough types nothing.
        web_mode = str(node_data.get('web_mode', False)).lower() in (
            'true', '1', 'yes', 'on',
        )
        # Input node v2 flags (all default off / text-only).
        decision_mode = str(node_data.get('decision_mode', False)).strip().lower() in ('true', '1', 'yes', 'on')
        route_on_answer = str(node_data.get('route_on_answer', False)).strip().lower() in ('true', '1', 'yes', 'on')
        question_mode = str(node_data.get('question_mode') or 'text').strip().lower()
        accepts = {
            'text': str(node_data.get('accept_text', True)).strip().lower() in ('true', '1', 'yes', 'on'),
            'images': str(node_data.get('accept_images', False)).strip().lower() in ('true', '1', 'yes', 'on'),
            'documents': str(node_data.get('accept_documents', False)).strip().lower() in ('true', '1', 'yes', 'on'),
        }
        logger.info(
            "[INPUT] Input node %s (label='%s', passthrough=%s) executing",
            node_id, label, passthrough,
        )

        # ── Determine mode: agent vs manual ──
        agent_mode = False
        try:
            agent_mode = self.llm_executor.get_variable("_ask_user_callback") is not None
        except Exception:
            agent_mode = False

        # Determine if this is a loop re-execution (node was previously completed)
        _loop_iteration = self._node_execution_count.get(node_id, 0) > 0
        logger.info(
            "[INPUT] Node %s: exec_count=%d, loop_iteration=%s",
            node_id, self._node_execution_count.get(node_id, 0), _loop_iteration,
        )

        # ── Resolve the value through the node's configured behavior. ──
        # Input nodes are value injectors: they resolve a value by one of
        # the strategies below (in priority order), then ``passthrough``
        # decides whether it is typed on screen or forwarded downstream.
        # Resolution is informed by topology (upstream connection) and the
        # shared ``_chain_input_context`` variable, which every input node
        # may inject into its connected nodes to support recursion.

        # 1) Agent-modifiable: the LLM reasons the value from the field
        #    label + chain context.  Runs on every execution so a looped
        #    node re-infers from the current context each iteration.
        if agent_modifiable and agent_mode:
            _ctx = ""
            try:
                _ctx = self._resolve_input_upstream_value(node)
                if _ctx is None:
                    _ctx = self.llm_executor.get_variable("_chain_input_context") or ""
            except Exception:
                try:
                    _ctx = self.llm_executor.get_variable("_chain_input_context") or ""
                except Exception:
                    _ctx = ""
            logger.info(
                "[INPUT] Node %s (label='%s') agent-modifiable → _reason_input_via_llm",
                node_id, label,
            )
            result = self._reason_input_via_llm(node, label, str(_ctx), stop_flag)

            # Grounded-or-ask: the context did not supply a value — never
            # invent one.  When the node carries its own question, surface it
            # in the agent chat (the orchestrator/brain run blocks exactly
            # like any other user-prompt Input node).
            if not str(result or '').strip() and user_prompt:
                logger.info(
                    "[INPUT] Node %s (label='%s') agent-modifiable found no "
                    "value in context — asking the user (%r)",
                    node_id, label, user_prompt,
                )
                self._maybe_speak_input_prompt(node_data, user_prompt)
                try:
                    result, _attachments = self._ask_user_v2(
                        node_data, user_prompt, question_mode, accepts, default_value,
                    )
                    if _attachments:
                        result = self._apply_input_attachments(
                            node_data, node_id, result, _attachments, accepts,
                        )
                except Exception as e:
                    logger.warning(
                        "[INPUT] ask-user failed for node %s: %s — using default",
                        node_id, e,
                    )
                    result = ""
                if not str(result or '').strip():
                    result = default_value or ""
                logger.info(
                    "[INPUT] Node %s (label='%s') user answered (%d chars)",
                    node_id, label, len(str(result or "")),
                )

            # No question to ask: carry the raw context instead of publishing
            # an EMPTY value.  Branch 3's carry only applies when the flag is
            # OFF, so flipping agent-modifiable on a plain injection node
            # (label = the input's purpose, user_prompt empty) would silently
            # empty it whenever the reasoning turn found nothing.
            if not str(result or '').strip() and not user_prompt:
                result = str(_ctx or '')
                if result.strip():
                    logger.info(
                        "[INPUT] Node %s (label='%s') agent-modifiable found "
                        "no value — carrying the raw context (%d chars)",
                        node_id, label, len(result),
                    )

        # 2) Value holder with a configured default: use whatever value is
        #    set in the dialog, verbatim (write/forward decided by passthrough).
        #    Re-uses the fixed value on every execution (loops included).
        elif default_value and not agent_modifiable and not user_prompt:
            logger.info(
                "[INPUT] Node %s (label='%s') using default value (%d chars)",
                node_id, label, len(default_value),
            )
            result = default_value

        # 3) Pure injection/carry node (no default, no prompt, not
        #    agent-modifiable): carry whatever is on the upstream input
        #    connection, or inject the shared ``_chain_input_context``.
        #    This is how every Input node feeds its connected nodes and
        #    how a loop-back re-injects earlier content — a general
        #    topology rule that applies identically on every execution,
        #    independent of agent mode.  Output-node sources are skipped:
        #    an Output node is a sink that exposes results to the outside
        #    (agent overlay / popup) — it produces no data for chain
        #    execution, so the connection is flow-only.
        elif not user_prompt and not agent_modifiable:
            _ctx = None
            # 3a) Pre-collected manual supply: the GUI run flow gathers values
            #     for entry Input nodes on the MAIN thread before execution
            #     starts (Qt dialogs cannot open from the worker thread).
            try:
                _pre_val = self.llm_executor.get_variable(f"_pre_collected_{node_id}")
                if _pre_val is not None and _pre_val != "":
                    _ctx = _pre_val
            except Exception:
                pass
            if _ctx is None:
                try:
                    _ctx = self._resolve_input_upstream_value(node)
                except Exception:
                    _ctx = None
            if _ctx is None:
                try:
                    _ctx = self.llm_executor.get_variable("_chain_input_context") or ""
                except Exception:
                    _ctx = ""
            result = str(_ctx) if _ctx else ""
            if not result and _ctx in (None, ""):
                # Nothing to carry: no pre-collected value, no upstream
                # connection, no ``_chain_input_context``.  A node feeding
                # ONLY Handle node(s) is not a dead end — the Handle grounds
                # on its own goal or asks the user (tests/test_handle_input_flow.py).
                # Anything else fails hard rather than injecting an empty value.
                if self._feeds_handle_only(node):
                    logger.info(
                        "[INPUT] Carry node %s (label=%r) feeds only Handle "
                        "node(s) — continuing without a value",
                        node_id, label,
                    )
                else:
                    logger.error(
                        "[INPUT] Carry node %s (label=%r) has no upstream value and no "
                        "_chain_input_context to inject — failing node",
                        node_id, label,
                    )
                    return None

        # 4) Agent-modifiable in a non-agent context: no LLM to reason with.
        #    Keep the strict contract (empty value; no GUI, no default).
        elif not user_prompt and agent_modifiable:
            result = ""

        # 5) User-prompt Input: a question is set — ask the user for fresh
        #    input via the configured prompt, re-asking on each loop iteration.
        elif user_prompt:
            if _loop_iteration:
                # Loop re-execution re-asks the user for each iteration.
                logger.info(
                    "[INPUT] Node %s (label='%s') loop iteration — asking user again",
                    node_id, label,
                )
            # ── Check for pre-collected input first (avoids Qt thread crash) ──
            try:
                _pre_val = self.llm_executor.get_variable(f"_pre_collected_{node_id}")
                if _pre_val is not None and _pre_val != "":
                    result = str(_pre_val)
                    logger.info(
                        "[INPUT] Using pre-collected input for node %s (%d chars)",
                        node_id, len(result),
                    )
                else:
                    raise ValueError("empty or None")
            except Exception:
                # Fall through to runtime callbacks
                ask_callback = None
                ask_rich = None
                try:
                    ask_callback = self.llm_executor.get_variable("_ask_user_callback")
                except Exception:
                    ask_callback = None
                try:
                    ask_rich = self.llm_executor.get_variable("_ask_user_v2_callback")
                except Exception:
                    ask_rich = None

                if ask_callback or ask_rich:
                    # Input nodes are USER-CONTROL GATES — no timeout.  Log the
                    # intentional block so the exported-agent log shows the
                    # wait is by design (the overlay asks the user in chat).
                    logger.info(
                        "[INPUT] Node %s (label='%s') BLOCKING for user input "
                        "(user_prompt=%r, mode=%s) — waiting for reply",
                        node_id, label, user_prompt, question_mode,
                    )
                    # Speak the question before presenting it (TTS, non-blocking).
                    self._maybe_speak_input_prompt(node_data, user_prompt)
                    result, _attachments = self._ask_user_v2(
                        node_data, user_prompt, question_mode, accepts, default_value,
                    )
                    if _attachments:
                        result = self._apply_input_attachments(
                            node_data, node_id, result, _attachments, accepts,
                        )
                    logger.info(
                        "[INPUT] Node %s (label='%s') resumed with %d chars "
                        "of user input",
                        node_id, label, len(str(result or "")),
                    )
                    if not result:
                        result = default_value or ""
                else:
                    # Manual GUI mode — show a dialog on the main thread.
                    logger.info(
                        "[INPUT] No ask_user channel found for node %s — "
                        "showing manual QInputDialog (user_prompt=%r, label=%r)",
                        node_id, user_prompt, label,
                    )
                    # Speak the question before showing the manual dialog.
                    self._maybe_speak_input_prompt(node_data, user_prompt)
                    try:
                        # Always-on-top: a browser window opened by the chain
                        # must not cover the prompt and swallow Enter/OK.
                        from ....qt_input import ask_question
                        _question = self._render_question_text(
                            user_prompt, question_mode, node_data,
                        )
                        _request = {
                            'question': _question,
                            'kind': question_mode if question_mode in ('text', 'yes_no', 'choice') else 'text',
                            'choices': self._parse_choices(node_data),
                            'accepts': dict(accepts),
                            'default': default_value or '',
                        }
                        _val, _files = ask_question(_request)
                        if _val is None and not _files:
                            logger.info("[INPUT] Manual input cancelled")
                            result = ""
                        elif _files:
                            result = self._apply_input_attachments(
                                node_data, node_id, _val, _files, accepts,
                            )
                        else:
                            _norm = self._normalize_answer(_val, question_mode, node_data)
                            result = _norm if _norm is not None else (_val or "")
                        logger.info("[INPUT] Manual input received (%d chars)", len(str(result or "")))
                    except Exception as e:
                        logger.warning("[INPUT] Failed to show input dialog: %s — using default", e)
                        result = default_value

        # ── Publish the value on the DATA port only ──
        # Base ports drive execution order; the resolved value is readable
        # EXCLUSIVELY by consumers that have an incoming edge from this node's
        # 'data' output port.  Nothing is written to node_<id>_output (nor the
        # 'output' port slot), so exec-only edges and any transparent node in
        # between can never see the value.
        self.port_store.set_output(self.chain_id, node_id, 'data', result)
        self.llm_executor.set_variable(f"node_{node_id}_data", result)
        # Marker kept for direct-upstream Input detection (data-port edges).
        self.llm_executor.set_variable(f"node_{node_id}_is_input_passthrough", True)

        # ── Unified input_context for all node types ──
        input_context = {
            "type": "input",
            "input": result,
            "output": result,
            "label": label,
        }
        self.port_store.set_output(self.chain_id, node_id, 'input_context', input_context)
        self.llm_executor.set_variable(f"node_{node_id}_input_context", input_context)

        # ── Decision routing: judge the resolved value and mark the branch ──
        # (core.py treats a branch-capable Input exactly like a conditional;
        # the true/false ports are only honoured when actually wired.)
        _branch_result = None
        if decision_mode:
            _branch_result = self._evaluate_input_decision(node_data, result, node_id, stop_flag)
        elif route_on_answer and question_mode == 'yes_no' and str(result or '').strip():
            _branch_result = self._normalize_yes_no(str(result))
        if _branch_result is not None:
            node['_branch_result'] = bool(_branch_result)
            try:
                self.port_store.set_output(self.chain_id, node_id, 'decision', bool(_branch_result))
                self.llm_executor.set_variable(f"node_{node_id}_decision", bool(_branch_result))
            except Exception:
                pass
            logger.info("[INPUT] Node %s routing decision: %s", node_id, bool(_branch_result))
        else:
            try:
                node.pop('_branch_result', None)
            except Exception:
                pass

        # ── Write the resolved value to its target.  passthrough writes none ──
        # Strict target rule — the toggle decides, nothing is inferred:
        #   web_mode ON  -> the chain's browser page, and only the page;
        #   web_mode OFF -> the desktop, and only the desktop;
        #   passthrough   -> no write at all.
        if _branch_result is not None:
            logger.info(
                "[INPUT] Node %s routes on decision=%s — skipping screen "
                "typing (value still stored for downstream nodes)",
                node_id, bool(_branch_result),
            )
        elif result and not passthrough:
            if web_mode:
                # The chain's browser has no desktop target, so the text goes
                # into whatever editable it has focused (see
                # player/web/actions.type_into_focused).
                try:
                    from ....web import actions as _web_actions
                    from ...llm_executor_resources.inputs import _web_driver

                    _drv = _web_driver()
                    if _drv is None:
                        # Silent before: the value was stored but nothing was
                        # typed, which read as "the text just vanished".
                        logger.warning(
                            "[INPUT] No browser driver available — %d chars not "
                            "typed (value still stored for downstream nodes)",
                            len(result),
                        )
                    else:
                        logger.info(
                            "[INPUT] Typing %d chars into the focused browser "
                            "element",
                            len(result),
                        )
                        if not _web_actions.type_into_focused(_drv, result):
                            logger.warning(
                                "[INPUT] No editable focused in the browser — "
                                "%d chars not typed (value still stored for "
                                "downstream nodes)",
                                len(result),
                            )
                except Exception as e:
                    logger.warning("[INPUT] Web typing failed: %s", e)
            elif hasattr(self, 'action_handlers') and self.action_handlers:
                try:
                    ah = self.action_handlers
                    if hasattr(ah, 'handle_type_string_action'):
                        logger.info("[INPUT] Typing %d chars on screen", len(result))
                        ah.handle_type_string_action(
                            0,
                            {
                                "text": result,
                                "force_typing": True,
                                "batch_size": 5,
                                "batch_delay": 0.01,
                            },
                            stop_flag,
                        )
                except Exception as e:
                    logger.warning("[INPUT] Failed to type text on screen: %s", e)
            else:
                logger.warning(
                    "[INPUT] Write target is the desktop but no desktop action "
                    "handler is available — %d chars not typed (value still "
                    "stored for downstream nodes)",
                    len(result),
                )
        elif passthrough:
            logger.info(
                "[INPUT] Passthrough mode — skipping screen typing, "
                "data stored for downstream nodes (%d chars)",
                len(result),
            )

        logger.info("[INPUT] Input node %s done", node_id)
        return self._get_input_next_node(node, 'output')

    def _maybe_speak_input_prompt(self, node_data, prompt_text):
        """Speak an Input node's question via tts_service when enabled.

        Speaks the configured ``tts_text`` fallback, else the ``user_prompt``
        itself.  Non-blocking: the workflow is already blocked waiting for
        the user's reply, so speech plays while the question is displayed.
        """
        if not node_data.get('tts_enabled'):
            return
        text = (node_data.get('tts_text') or prompt_text or '').strip()
        if not text:
            return
        try:
            from ....tts_service import submit as _tts_submit
        except ImportError:
            try:
                from LoOper.player.tts_service import submit as _tts_submit
            except ImportError:
                return
        try:
            logger.info("[INPUT] Speaking prompt (%d chars) via tts_service", len(text))
            _tts_submit(
                text,
                voice_model=node_data.get('tts_voice_model', '') or '',
                language=node_data.get('tts_language', 'en') or 'en',
                speed=node_data.get('tts_speed', 1.0),
                speaker_id=node_data.get('tts_speaker_id'),
                wait=False,
            )
        except Exception as e:
            logger.warning("[INPUT] TTS submission failed: %s", e)

    # ------------------------------------------------------------------
    # Decision routing + rich questions (Input node v2)
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_bool_token(text):
        """Strict first-token boolean parse (reasoning-strip, no prose scan)."""
        if text is None:
            return None
        clean = str(text)
        import re as _re
        try:
            clean = _re.sub(r'<[^>]*think[^>]*>.*?</[^>]*think[^>]*>', ' ', clean, flags=_re.DOTALL)
            clean = _re.sub(r'<[^>]*thinking[^>]*>.*?</[^>]*thinking[^>]*>', ' ', clean, flags=_re.DOTALL)
        except Exception:
            pass
        tokens = clean.strip().split()
        if not tokens:
            return None
        token = tokens[0].strip('.,;:!?\'"()[]{}*`#').lower()
        if token in ('true', 'yes', 'y', '1', 'ok', 'confirm', 'yeah'):
            return True
        if token in ('false', 'no', 'n', '0', 'cancel', 'nope'):
            return False
        return None

    def _normalize_yes_no(self, text):
        """Normalize a user answer to True/False/None (yes-no questions)."""
        return self._parse_bool_token(text)

    def _parse_choices(self, node_data):
        """The node's choice list: [{'label': ..., 'value': ...}, ...]."""
        import json as _json
        try:
            raw = node_data.get('choices') or '[]'
            data = _json.loads(raw) if isinstance(raw, str) else raw
            out = []
            for item in (data or []):
                if isinstance(item, dict) and str(item.get('label') or '').strip():
                    out.append({
                        'label': str(item.get('label')).strip(),
                        'value': str(item.get('value') or item.get('label')).strip(),
                    })
                elif isinstance(item, str) and item.strip():
                    out.append({'label': item.strip(), 'value': item.strip()})
            return out
        except Exception:
            return []

    def _render_question_text(self, question, question_mode, node_data):
        """Serialize a rich question for legacy text-only channels."""
        parts = [str(question or '')]
        if question_mode == 'yes_no':
            parts.append('(Answer yes or no)')
        elif question_mode == 'choice':
            choices = self._parse_choices(node_data)
            if choices:
                opts = '  '.join(f"{i + 1}) {c['label']}" for i, c in enumerate(choices))
                parts.append(f"Options: {opts}")
        return '\n'.join(p for p in parts if p)

    def _normalize_answer(self, raw, question_mode, node_data):
        """Normalize a raw answer per question mode. None = unusable."""
        if raw is None:
            return None
        text = str(raw).strip()
        if not text:
            return None
        if question_mode == 'yes_no':
            yn = self._parse_bool_token(text)
            if yn is None:
                return None
            return 'yes' if yn else 'no'
        if question_mode == 'choice':
            choices = self._parse_choices(node_data)
            if not choices:
                return text
            low = text.lower()
            # Exact label/value match (case-insensitive), then 1-based index.
            for c in choices:
                if low == c['label'].lower() or low == c['value'].lower():
                    return c['value']
            token = text.strip('.,;:!?\'"()[]{}').strip()
            if token.isdigit():
                idx = int(token) - 1
                if 0 <= idx < len(choices):
                    return choices[idx]['value']
            return None
        return text

    def _ask_user_v2(self, node_data, question, question_mode, accepts, default_value):
        """Ask the user via the richest available channel.

        Returns ``(value, attachments)``.  The rich ``_ask_user_v2_callback``
        (dict request -> dict response) is preferred; the legacy
        ``_ask_user_callback`` (text -> text) gets a serialized question and
        its reply is normalized against the mode.  ``(None, [])`` when no
        channel is available or the answer stays unusable after one re-ask.
        """
        rich = None
        legacy = None
        try:
            rich = self.llm_executor.get_variable("_ask_user_v2_callback")
        except Exception:
            rich = None
        try:
            legacy = self.llm_executor.get_variable("_ask_user_callback")
        except Exception:
            legacy = None

        if rich:
            request = {
                'question': str(question or ''),
                'kind': question_mode if question_mode in ('text', 'yes_no', 'choice') else 'text',
                'choices': self._parse_choices(node_data),
                'accepts': dict(accepts or {}),
                'default': default_value or '',
            }
            try:
                response = rich(request) or {}
            except Exception as e:
                logger.warning("[INPUT] ask-user v2 callback failed: %s", e)
                response = {}
            # A channel answers either as the v2 response DICT (agent overlay)
            # or as the v2 PAIR (value, attachments) — the manual-GUI channel
            # (_BgInputDialogHelper.ask_rich -> ask_question) returns the pair.
            # Stringifying a pair wrote the literal "(text, [])" as the value.
            if isinstance(response, dict):
                value = response.get('value')
                attachments = response.get('attachments') or []
            elif isinstance(response, (tuple, list)):
                value = response[0] if response else None
                attachments = (response[1] if len(response) > 1 else []) or []
            else:
                # A bare value is accepted as-is (defensive).
                value, attachments = response, []
            normalized = (
                self._normalize_answer(value, question_mode, node_data)
                if value is not None else None
            )
            return (normalized, attachments)

        if not legacy:
            return (None, [])
        text_q = self._render_question_text(question, question_mode, node_data)
        for attempt in (1, 2):
            try:
                raw = legacy(text_q if attempt == 1 else text_q + '\n(Answer only.)')
            except Exception as e:
                logger.warning("[INPUT] ask-user callback failed: %s", e)
                return (None, [])
            if raw is None:
                return (None, [])
            normalized = self._normalize_answer(raw, question_mode, node_data)
            if normalized is not None:
                return (normalized, [])
            logger.info(
                "[INPUT] Unusable answer %r for mode %s — re-asking",
                str(raw)[:80], question_mode,
            )
        return (None, [])

    def _extract_document_text(self, path):
        """Best-effort text extraction (pdf/docx/txt-family). Never raises."""
        if not path:
            return ''
        try:
            ext = os.path.splitext(str(path))[1].lower()
            if ext == '.pdf':
                from PyPDF2 import PdfReader
                reader = PdfReader(str(path))
                return '\n'.join((pg.extract_text() or '') for pg in reader.pages)[:20000]
            if ext == '.docx':
                import docx
                d = docx.Document(str(path))
                return '\n'.join(p.text for p in d.paragraphs)[:20000]
            if ext in ('.txt', '.md', '.csv', '.json', '.log'):
                with open(str(path), 'r', encoding='utf-8', errors='replace') as f:
                    return f.read()[:20000]
        except Exception as e:
            logger.warning("[INPUT] Document extraction failed for %s: %s", path, e)
        return ''

    def _apply_input_attachments(self, node_data, node_id, value, attachments, accepts):
        """Validate attachments against accepted media; merge text into value."""
        import json as _json
        files_meta = []
        extracted = []
        rejected = []
        for att in (attachments or []):
            if not isinstance(att, dict):
                continue
            kind = str(att.get('kind') or 'text').strip().lower()
            path = str(att.get('path') or '')
            if kind == 'image':
                if not accepts.get('images'):
                    rejected.append('image')
                    continue
                files_meta.append({'kind': 'image', 'path': path})
                if path:
                    extracted.append(f"[image: {path}]")
            elif kind == 'document':
                if not accepts.get('documents'):
                    rejected.append('document')
                    continue
                text = self._extract_document_text(path)
                files_meta.append({'kind': 'document', 'path': path, 'chars': len(text)})
                if text:
                    extracted.append(text)
            else:
                if not accepts.get('text'):
                    rejected.append('text')
                    continue
                files_meta.append({'kind': 'text'})
        if rejected:
            logger.warning(
                "[INPUT] Node %s rejected attachments outside its accepted media: %s",
                node_id, rejected,
            )
        if files_meta:
            try:
                payload = _json.dumps(
                    {'files': files_meta, 'text': str(value or '')}, ensure_ascii=False,
                )
                self.port_store.set_output(self.chain_id, node_id, 'files', payload)
                self.llm_executor.set_variable(f"node_{node_id}_files", payload)
            except Exception:
                pass
        parts = [str(value or '')] + extracted
        return '\n'.join(p for p in parts if p).strip()

    def _evaluate_input_decision(self, node_data, value, node_id, stop_flag):
        """Judge the resolved value against the criterion; fail-closed default."""
        default = str(node_data.get('decision_default', False)).strip().lower() in ('true', '1', 'yes', 'on')
        criterion = str(node_data.get('decision_criterion') or '').strip()
        if not criterion:
            logger.warning(
                "[INPUT] Node %s: decision_mode on but no criterion — fail-closed default",
                node_id,
            )
            return default
        detail = str(value or '')
        if not detail.strip():
            logger.info("[INPUT] Node %s: decision on an empty value — fail-closed default", node_id)
            return default
        evaluator = str(node_data.get('decision_evaluator') or 'llm').strip().lower()
        if evaluator == 'laya':
            decided = None
            try:
                from AI.laya_client import decision as _laya_decision
                decided = _laya_decision(detail, criterion)
            except Exception as e:
                logger.debug("[INPUT] Laya engine unavailable: %s", e)
            if decided is None:
                try:
                    from AI.laya_client import status as _laya_status
                    _why = _laya_status()
                except Exception:
                    _why = 'unavailable'
                if _why == 'loading':
                    logger.info(
                        "[INPUT] Node %s: Laya engine is still loading the model "
                        "— using the LLM evaluator for this decision", node_id,
                    )
                else:
                    logger.info(
                        "[INPUT] Node %s: Laya evaluator unavailable (%s) — "
                        "falling back to LLM", node_id, _why,
                    )
            else:
                logger.info("[INPUT] Node %s decision (laya): %s", node_id, bool(decided))
                return bool(decided)
        decided = self._evaluate_input_decision_llm(node_data, detail, criterion, node_id, stop_flag)
        if decided is None:
            logger.warning(
                "[INPUT] Node %s: decision unparseable — fail-closed default %s",
                node_id, default,
            )
            return default
        logger.info("[INPUT] Node %s decision (llm): %s", node_id, bool(decided))
        return bool(decided)

    def _evaluate_input_decision_llm(self, node_data, detail, criterion, node_id, stop_flag):
        """One small LLM turn: strict true/false contract, one stricter retry."""
        prompt = (
            "Decide whether the INPUT satisfies the CRITERION.\n"
            f"CRITERION: {criterion}\n"
            f"INPUT:\n{detail}\n\n"
            "Answer with exactly one word: true or false.\nANSWER:"
        )
        for attempt in (1, 2):
            raw = self._input_llm_call(
                prompt if attempt == 1 else prompt + "\n(One word only: true or false.)",
                node_data, stop_flag, max_tokens=16,
            )
            if raw is None:
                return None
            token = self._parse_bool_token(raw)
            if token is not None:
                return token
            logger.info("[INPUT] Decision answer %r unparseable (attempt %d)", str(raw)[:80], attempt)
        return None

    def _input_llm_call(self, prompt, node_data, stop_flag, max_tokens=64):
        """Short local LLM turn (threaded, no timeout). None on failure."""
        import threading
        import time
        engine = str(node_data.get('decision_engine') or 'llamacpp').strip().lower()
        model = str(node_data.get('decision_model') or '').strip()
        if not model:
            try:
                for wnode in (self.workflow_graph or {}).values():
                    if wnode.get('type') != 'llm':
                        continue
                    wdata = wnode.get('data', {}) or {}
                    conf = wdata.get('llm_configuration', {}) or {}
                    merged = dict(wdata)
                    merged.update(conf)
                    if engine in ('llamacpp', 'llama.cpp', 'llama_cpp'):
                        model = str(merged.get('llamacpp_model_path') or '').strip()
                    else:
                        model = str(merged.get('model') or '').strip()
                    if model:
                        break
            except Exception:
                pass
        if not model:
            try:
                from ...llm_executor_resources.config_utils import get_default_llm_model
                model = get_default_llm_model(engine)
                if model:
                    logger.info(
                        "[INPUT] No decision model configured — using the app default model %s",
                        model,
                    )
            except Exception:
                model = ''
        if not model:
            logger.warning("[INPUT] No decision model configured and no chain LLM node to borrow from")
            return None
        out = {'text': None, 'error': None}

        def _runner():
            try:
                if engine in ('llamacpp', 'llama.cpp', 'llama_cpp'):
                    import requests as _requests
                    from ...llm_executor_resources.config_utils import (
                        get_default_api_url, resolve_llamacpp_model_path,
                    )
                    api_url = (get_default_api_url() or '').rstrip('/')
                    payload = {
                        "model": resolve_llamacpp_model_path(model),
                        "prompt": prompt,
                        "system": "",
                        "temperature": 0.1,
                        "max_tokens": max_tokens,
                        "stream": False,
                        "use_llamacpp": True,
                        # Chat mode with thinking DISABLED: the decision must
                        # be a single token, not a reasoning block (thinking
                        # on returns empty content at small budgets).
                        "chat_template_kwargs": {"enable_thinking": False},
                        "gpu_layers": 0,
                        "threads": -1,
                        "context_size": 0,
                    }
                    resp = _requests.post(f"{api_url}/llamacpp/generate", json=payload, timeout=None)
                    if resp.status_code == 200:
                        out['text'] = (resp.json() or {}).get('response', '')
                    else:
                        out['error'] = f"llama.cpp error ({resp.status_code}): {resp.text[:200]}"
                else:
                    from AI.consult import OllamaClient
                    from ...llm_executor_resources.config_utils import get_default_api_url
                    client = OllamaClient(base_url=get_default_api_url())
                    response = client.chat(model=model, prompt=prompt, temperature=0.1, max_tokens=max_tokens)
                    if isinstance(response, dict):
                        out['text'] = response.get('response') or response.get('text') or ''
                    else:
                        out['text'] = str(response)
            except Exception as e:
                out['error'] = str(e)

        t = threading.Thread(target=_runner, daemon=True)
        t.start()
        while t.is_alive():
            if stop_flag and stop_flag():
                logger.info("[INPUT] Decision LLM call stopped by flag")
                return None
            time.sleep(0.05)
        if out['error'] is not None:
            logger.error("[INPUT] Decision LLM call failed: %s", out['error'])
            return None
        return (out.get('text') or '').strip() or None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _feeds_handle_only(self, node):
        """True when EVERY execution consumer of this node's 'output' is a
        Handle node (and there is at least one connection).

        An empty pure-carry Input feeding only Handle node(s) is not a dead
        end: the Handle grounds on its own goal or asks the user — see
        tests/test_handle_input_flow.py.  Mixed or non-Handle consumers keep
        the strict hard-fail.
        """
        try:
            conns = (node.get('connections') or {}).get('output') or []
        except Exception:
            conns = []
        targets = []
        for c in conns:
            if not isinstance(c, dict):
                continue
            tid = c.get('node_id') or c.get('target_node_id')
            if tid:
                targets.append(tid)
        if not targets:
            return False
        graph = self.workflow_graph or {}
        for tid in targets:
            t = graph.get(tid)
            if not t or t.get('type') != 'handle':
                return False
        return True

    def _resolve_input_upstream_value(self, node, port='input'):
        """Resolve the value carried on this node's upstream ``port`` connection.

        ``port`` defaults to the execution 'input' port; callers can read a
        named data port instead (e.g. the orchestrator's 'prompt' goal port).

        Output nodes are excluded as data sources: an Output node is a sink
        that exposes LLM/code results to the outside (agent overlay, popup) —
        it produces no information usable by workflow chain execution.  A
        connection from an Output node to an Input node is flow-only
        ("output results, then execute the Input node"), so it contributes
        no value here.

        Returns:
            Any: the upstream value, or ``None`` when there is no usable
            connection (no connection, unexecuted upstream, or the upstream
            is an Output node).
        """
        try:
            for inp in (node.get('inputs') or []):
                if str(inp.get('input_port', 'input')) != port:
                    continue
                from_node = inp.get('from_node')
                if not from_node:
                    continue
                # Output nodes are sinks — their stored result is for external
                # consumers only, never data for chain execution.
                try:
                    _up_type = (self.workflow_graph or {}).get(from_node, {}).get('type')
                except Exception:
                    _up_type = None
                if _up_type == 'output':
                    logger.debug(
                        "[INPUT] Skipping Output node %s as data source for input node %s "
                        "— flow-only connection",
                        from_node, node.get('id') or node.get('node_id'),
                    )
                    continue
                out_port = (inp.get('output_type')
                            or inp.get('output_port')
                            or 'output')
                # Base/branch ports drive execution only — they carry no data.
                if str(out_port or '') in (
                    'output', '', 'true', 'false', 'error', 'route'
                ):
                    continue
                val = self.port_store.get_output(self.chain_id, from_node, out_port)
                if val is not None:
                    return val
        except Exception:
            return None
        return None

    def _reason_input_via_llm(self, node_id, label, chain_context, stop_flag):
        """Ask the LLM to determine what text the InputNode should type.

        Uses the input field's ``label`` as the primary filter to extract the
        correct text from ``chain_context`` (which carries ``_chain_input_context``
        — the original input that reached the parent router).

        Prefers the already-running llama.cpp server (SmolLM3) when available
        through the ``_llamacpp_server_url`` variable. Falls back to
        ``OllamaClient`` via the API gateway otherwise.

        Example:
          Label: "Search query"
          Context: "search for 'milo j' on youtube please"
          LLM returns: "milo j"   (just the search term, extracted via label)
        """
        import requests

        # Build a focused reasoning prompt
        # Label is the PRIMARY signal — it defines what this field expects
        _user_question = ''
        try:
            _user_question = str(node_id.get('user_prompt') or '').strip()
        except Exception:
            _user_question = ''
        context_parts = []
        if label:
            context_parts.append(f"Input field label: {label}")
        if _user_question:
            context_parts.append(
                f"Input field question (what the field must answer): {_user_question}"
            )

        # Collect ONLY upstream context — not ALL executor variables
        context_lines = []
        upstream_ids = {}
        try:
            for inp in (node_id.get('inputs') or []):
                from_id = inp.get('from_node')
                if from_id:
                    upstream_ids[from_id] = inp.get('output_type') or inp.get('output_port') or 'output'
        except Exception:
            pass

        for upstream_id, port in upstream_ids.items():
            # Prefer port_store, fall back to legacy variable
            val = self.port_store.get_output(self.chain_id, upstream_id, port)
            if val is None:
                val = self.llm_executor.get_variable(f"node_{upstream_id}_context")
            if val is None or (isinstance(val, str) and not val.strip()):
                val = self.llm_executor.get_variable(f"node_{upstream_id}_output")
            if val is not None and (isinstance(val, str) and val.strip()):
                try:
                    sv = str(val)[:300]
                    context_lines.append(f"upstream({port}): {sv}")
                except Exception:
                    pass

        if context_lines:
            context_parts.append(
                "Chain execution context (direct upstream node outputs):\n"
                + "\n".join(context_lines[-5:])
            )

        # Inject the scoping context: when the orchestrator invoked this
        # worker it published a STRUCTURED goal + immediate step
        # (``_chain_goal`` / ``_chain_step``).  That pair is the tiny, clean
        # task the extractor needs; the raw composed prompt (trace included)
        # made small models paste question chunks instead of a value.
        _goal = _step = ''
        try:
            _goal = str(self.llm_executor.get_variable("_chain_goal") or '').strip()
            _step = str(self.llm_executor.get_variable("_chain_step") or '').strip()
        except Exception:
            pass
        if _goal or _step:
            if _goal:
                context_parts.append(f"Workflow goal: {_goal[:300]}")
            if _step:
                context_parts.append(f"Immediate task for this worker: {_step[:300]}")
        elif chain_context and str(chain_context).strip():
            # Fallback (manual runs / no orchestrator): the original user
            # request that drove tool selection.
            context_parts.append(
                "Original user input (from parent chain):\n"
                + str(chain_context).strip()[:500]
            )

        context_str = "\n\n".join(context_parts) if context_parts else ""

        system_msg = (
            "You are an input field value extractor for an automation workflow. "
            "Extract the EXACT text that should be typed or passed into an input "
            "field, given the field's label/question and the available context.\n\n"
            "Rules:\n"
            "1. Extract the value ONLY if the user's request explicitly states it. "
            "Never invent, guess, or repeat example phrases, tool descriptions, "
            "or instructions.\n"
            "2. The input field LABEL and QUESTION define which value to extract — "
            "they are the PRIMARY filter. E.g. if the field asks 'what do you "
            "want to apply to?', extract the position the user asked for.\n"
            "3. Output ONLY the raw text to type — no explanations, no quotes, "
            "no formatting, no markdown.\n"
            "4. If the user's request does not state a value for this field, "
            "output exactly: NONE"
        )

        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": f"Input field label: {label}"},
        ]
        if _user_question:
            messages.append({
                "role": "user",
                "content": f"Input field question: {_user_question}",
            })
        if context_str:
            messages.append({"role": "user", "content": context_str})
        messages.append({
            "role": "user",
            "content": (
                "Based on the field label/question and the context above, what "
                "EXACT text should be typed into this input field? "
                "Output ONLY the raw text. If the context does not state a value "
                "for this field, output exactly NONE."
            ),
        })

        logger.info(
            "[INPUT] Reasoning via LLM for node %s (%d chars prompt)",
            node_id, sum(len(str(m.get("content", ""))) for m in messages),
        )

        # ── Call the canonical inference path ──
        # Full app: HTTP POST to the FastAPI /llamacpp/generate gateway.
        # Exported agent (ARROW_DIRECT_ENGINE=1): in-process transport seam —
        # same payload semantics (SmolLM3, messages, 256 max tokens, 0.1 temp).
        try:
            from ...llm_executor_resources.config_utils import get_default_api_url

            payload = {
                "model": "SmolLM3-Q4_K_M.gguf",
                "messages": messages,
                "max_tokens": 256,
                "temperature": 0.1,
                "stream": False,
                "use_llamacpp": True,
            }
            try:
                from AI import inprocess_transport as _it
                direct_mode = _it.is_direct_mode()
            except Exception:
                _it = None
                direct_mode = False

            if direct_mode and _it is not None:
                logger.info(
                    "[INPUT] Reasoning via in-process direct engine (node %s)",
                    node_id,
                )
                _res = _it.chat(
                    messages=messages, max_tokens=256, temperature=0.1,
                )
                result = (_res.get("response") or "").strip()
            else:
                api_url = get_default_api_url().rstrip("/")
                logger.info(
                    "[INPUT] Calling FastAPI /llamacpp/generate at %s", api_url,
                )
                resp = requests.post(
                    f"{api_url}/llamacpp/generate", json=payload, timeout=120,
                )
                if resp.status_code == 200:
                    result = (resp.json().get("response") or "").strip()
                else:
                    logger.warning(
                        "[INPUT] /llamacpp/generate returned %d — returning empty",
                        resp.status_code,
                    )
                    result = ""

            result = result.strip().strip('"\'"').strip()
            if result.strip().upper() == 'NONE':
                logger.info(
                    "[INPUT] Node %s: context does not supply a value — NONE",
                    node_id,
                )
                result = ""
            if not result:
                logger.warning(
                    "[INPUT] LLM returned empty — returning empty string"
                )
                result = ""

            logger.info(
                "[INPUT] LLM-reasoned input (%d chars): %.120s",
                len(result), result,
            )
            return result

        except Exception as e:
            logger.error("[INPUT] LLM reasoning failed: %s — returning empty string", e)
            return ""

    def _get_input_next_node(self, node, port):
        connections = node.get('connections', {})
        if port in connections and connections[port]:
            result = connections[port][0].get('node_id')
            logger.info(
                "[INPUT-NEXT] Input node %s -> %s (connections type=%s, connections=%s)",
                node.get('id') or node.get('node_id'), result,
                type(connections).__name__, str(connections)[:200],
            )
            return result
        if port == 'output':
            logger.info(
                "[INPUT-NEXT] Input node %s: port '%s' not in connections, returning __done__ (connections=%s)",
                node.get('id') or node.get('node_id'), port,
                str(connections)[:200],
            )
            return "__done__"
        logger.info(
            "[INPUT-NEXT] Input node %s: no port match, returning None",
            node.get('id') or node.get('node_id'),
        )
        return None
