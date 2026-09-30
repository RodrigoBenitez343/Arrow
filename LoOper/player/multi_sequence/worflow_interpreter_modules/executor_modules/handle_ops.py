"""Execution logic for Handle nodes.

Straightforward pipeline:
1. Take screenshot
2. LocateAnything-3B returns normalized coordinates via special tokens
3. The action is executed on screen

Two actions are supported:
- click: one grounding pass locates the element, then click
- drag: two grounding passes locate the source object and the target
  destination, then mouse-down / move / mouse-up between them

The goal text used for grounding comes from the Handle node's own
``goal_description``, or from a connected upstream Input node's value when
present (the Input supplies the dynamic prompt), or from the user via the
agent-adaptive ask — in that order.

Web mode (``web_mode`` = click only): the goal text is resolved against the
LIVE browser page instead of a screenshot — the shared browser's clickable
elements are enumerated, the embedded Laya engine picks the element the
request asks for, and the web click ladder (native first, shadow-piercing JS
dispatch as fallback) performs the click.  LocateAnything / the screen are
never touched in this mode.
"""

import base64
import io
import json
import logging
import os
import re
import subprocess
import tempfile
import time

logger = logging.getLogger(__name__)


class HandleMixin:
    """Execution logic for Handle nodes with direct LocateAnything grounding."""

    VALID_ACTIONS = {
        'click',
        'drag',
    }

    _API_URL = "http://127.0.0.1:8000"
    _LOCATE_MODEL = "LocateAnything-3B-Q8_0.gguf"

    def _resolve_api_url(self):
        """Resolve the app's API URL.

        Always returns the FastAPI gateway URL (http://127.0.0.1:8000).
        The ``_llamacpp_server_url`` variable points to a raw llama.cpp
        server which does NOT have the ``/llamacpp/generate`` endpoint —
        that endpoint lives on the FastAPI gateway which handles model
        switching across SmolLM3, LFM2.5-VL, and LocateAnything-3B.
        """
        # The FastAPI server is started by main.py at app launch.
        # In development mode it runs as a subprocess (port 8000).
        return self._API_URL

    def _call_llamacpp_api(self, api_url, model, prompt, system="",
                           images=None, temperature=0.2, max_tokens=256,
                           context_size=0, log_calls=True, max_rounds=None):
        """Call llama.cpp via the API gateway, matching the LLM node's format exactly.

        Uses ``/llamacpp/generate`` with ``prompt`` + ``system`` format (not ``messages``),
        plus standard params (gpu_layers, threads, context_size) that the LLM nodes use.

        Args:
            api_url: Base URL of the app's API (e.g., http://127.0.0.1:8000)
            model: GGUF model path (relative to models dir, or absolute)
            prompt: User message text
            system: System prompt text
            images: Optional list of base64-encoded images
            temperature: Sampling temperature
            max_tokens: Max tokens to generate
            context_size: Explicit context window; 0 = let the engine pick
                (RAM-aware, which can cap HARD on a low-RAM box and then reject
                every prompt with a 400 exceed_context_size_error)
            log_calls: Log the call + response telemetry.  A caller that
                renders its OWN boxy per-operation view (the Form Filling node)
                turns this off so its log is not split by plain [HANDLE] lines.

        Returns:
            str: The response text, or empty string on failure.
        """
        import requests
        payload = {
            "model": model,
            "prompt": prompt or "",
            "system": system,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
            "use_llamacpp": True,
            "gpu_layers": 0,
            "threads": -1,
            "context_size": context_size or 0,
            # Continuation rounds for a truncated generation: a caller that
            # asks for a short structured reply sends 1; None keeps the
            # engine default.
            "max_rounds": max_rounds,
        }
        if images:
            payload["images"] = images

        url = f"{api_url}/llamacpp/generate"
        if log_calls:
            logger.info(
                "[HANDLE] Calling %s (model=%s, images=%s, max_tokens=%d, "
                "ctx=%s)",
                url, model, bool(images), max_tokens, context_size or "auto",
            )
        try:
            resp = requests.post(url, json=payload, timeout=None)
            if resp.status_code == 200:
                data = resp.json()
                text = data.get("response", "") or ""
                if log_calls:
                    logger.info(
                        "[HANDLE] Response %d chars from %s", len(text), model,
                    )
                return text
            else:
                # Honor ``log_calls`` on the FAILURE path too.  A caller that
                # turned telemetry off (the Form Filling node renders its own
                # per-field view) must not have its errors relabelled as
                # Handle-node failures: that is exactly what made a WEB form
                # filler - which never touches the Handle actuator - log
                # "[HANDLE] API returned 500".  The caller owns the report
                # (its own retry / document-ladder lines say why).
                if log_calls:
                    logger.warning("[HANDLE] API returned %d for %s: %s",
                                   resp.status_code, model, resp.text[:200])
                return ""
        except Exception as e:
            if log_calls:
                logger.error("[HANDLE] API call to %s failed: %s", url, e)
            return ""

    def _execute_handle_node(self, node, stop_flag):
        """Execute a Handle node — ground goal_text to screen coordinates then act.

        Pipeline:
        1. Take screenshot
        2. LocateAnything-3B returns normalized coordinates
        3. The action is executed on screen

        For drag actions, two grounding passes are performed: the first
        locates the source object, the second locates the target destination.

        Args:
            node (dict): The Handle node from the workflow graph.
            stop_flag (callable): Stop flag function.

        Returns:
            str: Next node ID to execute, or ``__done__`` if no output.
        """
        node_data = node.get('data', {})
        node_id = (node.get('id') or node.get('node_id')
                   or node_data.get('node_id') or node_data.get('id'))

        action_type = node_data.get('action_type', 'click') or 'click'
        goal_description = node_data.get('goal_description', '') or ''
        target_description = node_data.get('target_description', '') or ''
        web_mode = str(node_data.get('web_mode', False)).strip().lower() in (
            'true', '1', 'yes', 'on')

        logger.info(
            "[HANDLE] Handle node %s (action=%s, web_mode=%s, goal='%s', "
            "target='%s') executing",
            node_id, action_type, web_mode, goal_description,
            target_description,
        )

        # Validate action type
        if action_type not in self.VALID_ACTIONS:
            logger.warning(
                "[HANDLE] Invalid action type '%s' — falling back to 'click'",
                action_type,
            )
            action_type = 'click'

        # ── Determine grounding prompts: agent mode vs manual ──
        grounding_prompt = goal_description
        target_prompt = target_description
        agent_adaptive = node_data.get('agent_adaptive', False)

        # An Input node wired into this Handle node IS the prompt source:
        # whatever it carried (passthrough value, user's chat answer, or a
        # resolved default) is the goal text to ground on.  The static
        # goal_description remains a fallback when the Input resolves nothing.
        _input_goal = self._resolve_connected_input_goal(node)
        if _input_goal:
            grounding_prompt = _input_goal
            goal_description = _input_goal
            logger.info(
                "[HANDLE] Handle node %s using connected Input value as goal "
                "(%d chars): %.120s",
                node_id, len(_input_goal), _input_goal,
            )

        # Resolve the ask-user callback the same way Input nodes do
        # (stored as a variable on llm_executor in agent mode).
        ask_cb = None
        try:
            ask_cb = self.llm_executor.get_variable("_ask_user_callback")
        except Exception:
            pass
        if ask_cb is None:
            ask_cb = getattr(self, '_ask_user_callback', None)

        # Handle nodes use fixed descriptions, a connected Input value, or
        # prompt the user — never _chain_input_context directly.
        if agent_adaptive and not grounding_prompt:
            try:
                if ask_cb:
                    question = (
                        "What object should I drag? (source object)"
                        if action_type == 'drag'
                        else ("What would you like me to click on the page?"
                              if web_mode else
                              "What would you like me to click on this screen?")
                    )
                    user_goal = ask_cb(question)
                    if user_goal:
                        grounding_prompt = str(user_goal)
                        goal_description = str(user_goal)
                        logger.info("[HANDLE] User-provided goal: '%s'", goal_description)
            except Exception:
                pass

        # Drag needs a destination too — ask for it when missing
        if action_type == 'drag' and agent_adaptive and not target_prompt:
            try:
                if ask_cb:
                    user_target = ask_cb(
                        "Where should I drop the dragged object? (target location)"
                    )
                    if user_target:
                        target_prompt = str(user_target)
                        target_description = str(user_target)
                        logger.info(
                            "[HANDLE] User-provided target: '%s'", target_description
                        )
            except Exception:
                pass

        # Run the pipeline.  Web mode resolves the request against the live
        # DOM (Laya pick + web click ladder) instead of grounding a
        # screenshot; it is a click feature - drag keeps the desktop path.
        if web_mode and action_type == 'click':
            result = self._execute_handle_web(
                node_id, grounding_prompt, stop_flag,
            )
        else:
            if web_mode:
                logger.warning(
                    "[HANDLE] Web mode is click-only — action '%s' runs the "
                    "desktop grounding pipeline", action_type,
                )
            result = self._execute_handle_pipeline(
                node_id, action_type, grounding_prompt, stop_flag,
                target_description=target_prompt,
            )
        if not result:
            if stop_flag and stop_flag():
                logger.info("[HANDLE] Handle pipeline stopped by user")
                return self._get_handle_next_node(node, 'output')
            logger.error(
                "[HANDLE] Handle pipeline returned empty — marking node as "
                "FAILED (see the [HANDLE] error lines above)"
            )
            return None

        # Publish the result on the 'data' port (base 'output' is exec-only).
        try:
            self.port_store.set_output(self.chain_id, node_id, 'data', result)
        except Exception:
            pass
        self.llm_executor.set_variable(f"node_{node_id}_data", result)

        logger.info("[HANDLE] Handle node %s done", node_id)
        return self._get_handle_next_node(node, 'output')

    def _resolve_connected_input_goal(self, node):
        """Resolve the text carried by an Input node connected to this Handle node.

        A Handle node wired after an Input node treats that Input's resolved
        value as its goal prompt — the Input is the "what to do" source, and
        its text overrides the static ``goal_description`` when present.  Only
        Input-type upstreams (or upstreams flagged as passthrough) count:
        other nodes connecting on the execution ``input`` port are flow
        ordering, not prompt data.

        Returns:
            str: the upstream Input text, or ``""`` when none is connected
            or it resolved empty.
        """
        try:
            for inp in (node.get('inputs') or []):
                # The Handle node receives Input text on its 'data' input port
                # (base 'input' is execution ordering only).
                if str(inp.get('input_port') or '') != 'data':
                    continue
                up_id = inp.get('from_node')
                if not up_id:
                    continue
                out_port = str(inp.get('output_type')
                               or inp.get('output_port') or 'output')
                # Strict data semantics: a Handle consumes an Input's text ONLY
                # through an incoming edge from the Input's 'data' output port.
                # Base execution-port edges are flow ordering, not prompt data.
                if out_port != 'data':
                    continue
                try:
                    up_type = (self.workflow_graph or {}).get(up_id, {}).get('type')
                except Exception:
                    up_type = None
                try:
                    is_passthrough = bool(self.llm_executor.get_variable(
                        f"node_{up_id}_is_input_passthrough"))
                except Exception:
                    is_passthrough = False
                if up_type != 'input' and not is_passthrough:
                    continue
                val = None
                try:
                    val = self.port_store.get_output(
                        self.chain_id, up_id, 'data')
                except Exception:
                    val = None
                if val is None:
                    try:
                        val = self.llm_executor.get_variable(
                            f"node_{up_id}_data")
                    except Exception:
                        val = None
                if val is not None and str(val).strip():
                    return str(val).strip()
        except Exception:
            pass
        return ""

    # ------------------------------------------------------------------
    # Web mode: request text -> DOM candidates -> Laya pick -> DOM click
    # ------------------------------------------------------------------

    # ponytail: 16 is the engine's option slots (max_opts) - candidates
    # beyond it are silently unscorable, so the top 16 by lexical rank are
    # offered; a wider pick would need paged choice questions.
    _WEB_SCAN_MAX = 80      # candidates the page scan labels + marks
    _WEB_OPTION_MAX = 16    # candidates offered to Laya (engine max_opts)
    _WEB_STATE_MAX = 300    # request chars kept in the Laya state

    def _execute_handle_web(self, node_id, goal, stop_flag):
        """Resolve *goal* against the shared browser's DOM and click the pick.

        Pipeline:
        1. Scan the live page for visible clickable elements (each candidate
           is labelled and marked with a ``data-wvp-handle`` ordinal).
        2. Rank candidates by overlap with the request and ask the embedded
           Laya engine to pick (lexical best match when Laya is off/down).
        3. Click the picked element through the engine's own click ladder
           (trusted native first, shadow-piercing JS dispatch as fallback).

        Returns the node output summary (JSON), or ``""`` on failure.
        """
        goal = str(goal or '').strip()
        if not goal:
            logger.error(
                "[HANDLE][WEB] No request text — set a goal description or "
                "connect an Input node"
            )
            return ""

        try:
            from ....web import actions as web_actions
        except Exception as exc:
            logger.error("[HANDLE][WEB] web modules unavailable: %s", exc)
            return ""
        try:
            from ...llm_executor_resources.inputs import _web_driver
        except Exception as exc:
            logger.error("[HANDLE][WEB] browser accessor unavailable: %s", exc)
            return ""
        driver = _web_driver()
        if driver is None:
            logger.error("[HANDLE][WEB] No shared browser available")
            return ""

        logger.info(
            "[HANDLE][WEB] Handle node %s resolving '%s' against the page",
            node_id, goal,
        )
        candidates = self._web_scan_candidates(driver, web_actions)
        if not candidates:
            logger.error(
                "[HANDLE][WEB] No clickable element found on the page"
            )
            return ""

        try:
            picked, how = self._web_pick_candidate(goal, candidates)
            if picked is None:
                return ""
            if stop_flag and stop_flag():
                return ""
            ok, detail = self._web_click_marked(driver, web_actions, picked)
        finally:
            # Best-effort: drop the scan's markers once they are not needed.
            self._web_clear_marks(driver, web_actions)
        if not ok:
            logger.error(
                "[HANDLE][WEB] Cannot click '%s': %s", picked['label'], detail
            )
            return ""

        logger.info(
            "[HANDLE][WEB] Clicked '%s' (%s, %s) via %s",
            picked['label'], picked['tag'], how, detail,
        )
        return json.dumps({
            "action": "click",
            "mode": "web",
            "goal": goal,
            "element": picked['label'],
            "tag": picked['tag'],
            "picked_by": how,
            "method": detail,
            "candidates": len(candidates),
        })

    def _web_pick_candidate(self, goal, candidates):
        """The candidate the request asks for: ``(candidate, how)``.

        Candidates are ranked by word overlap with the request; the top ones
        (never more than the engine's option slots) are offered to the Laya
        engine, whose pick wins.  Laya off/down leaves the best lexical match
        as the pick; a request that matches NOTHING returns ``(None, None)`` —
        clicking a random element is worse than failing the node.
        """
        ranked = sorted(
            ((self._web_match_score(goal, c['label']), c) for c in candidates),
            key=lambda row: -row[0],
        )
        if ranked[0][0] <= 0:
            logger.error(
                "[HANDLE][WEB] No page element matches '%s' (%d candidates "
                "checked) — not clicking anything",
                goal, len(candidates),
            )
            return None, None

        options = ranked[:self._WEB_OPTION_MAX]
        logger.debug(
            "[HANDLE][WEB] Top candidates: %s",
            [(c['label'], score) for score, c in options],
        )
        picked_label = self._web_laya_pick(
            goal, [c['label'] for _s, c in options]
        )
        if picked_label is not None:
            for _s, c in options:
                if c['label'] == picked_label:
                    return c, "laya"
        logger.info(
            "[HANDLE][WEB] Laya gave no pick — using best lexical match '%s'",
            options[0][1]['label'],
        )
        return options[0][1], "lexical"

    def _web_clear_marks(self, driver, web_actions):
        """Best-effort removal of the scan's candidate markers."""
        try:
            driver.execute_script(web_actions.JS_CLEAR_HANDLE_MARKS)
        except Exception:
            pass

    def _web_scan_candidates(self, driver, web_actions):
        """Label + mark every visible clickable element on the page.

        Returns ``[{i, tag, label}]`` ([] on failure) where *i* is the marker
        ordinal the element carries in ``data-wvp-handle`` — that attribute
        is the click handle the web ladder resolves later.
        """
        try:
            raw = driver.execute_script(
                web_actions.JS_ENUMERATE_CLICKABLES, self._WEB_SCAN_MAX
            )
        except Exception as exc:
            logger.error("[HANDLE][WEB] Page scan failed: %s", exc)
            return []
        out = []
        for item in (raw or []):
            if not isinstance(item, dict):
                continue
            label = str(item.get('label') or '').strip()
            if not label:
                continue
            out.append({
                'i': int(item.get('i', len(out)) or 0),
                'tag': str(item.get('tag') or ''),
                'label': label,
            })
        logger.info(
            "[HANDLE][WEB] Scanned %d clickable element(s)", len(out)
        )
        return out

    @staticmethod
    def _web_words(text):
        """Lowercased alphanumeric words (2+ chars) of a text."""
        return [w for w in re.split(r'[^a-z0-9]+', str(text or '').lower())
                if len(w) > 1]

    @classmethod
    def _web_match_score(cls, goal, label):
        """How many words of *goal* the *label* carries (containment-tolerant).

        A verbatim word scores 2; a partially overlapping one (3+ letters on
        BOTH sides, e.g. the request 'login button' vs the label 'Log in':
        'log' inside 'login') scores 1.  The 3-letter floor keeps a stray
        fragment from claiming a match — 'invoice' must not match 'in'.  The
        score ranks the page's candidates for the picker AND is the pick
        itself when Laya is unavailable.
        """
        hay = cls._web_words(label)
        hay_set = set(hay)
        score = 0
        for word in cls._web_words(goal):
            if word in hay_set:
                score += 2
            elif len(word) >= 3 and any(
                len(other) >= 3 and (word in other or other in word)
                for other in hay
            ):
                score += 1
        return score

    def _web_laya_pick(self, goal, labels):
        """The label the embedded Laya engine picks for *goal*, or None.

        One typed ``choice`` forward over the candidate labels (never more
        than the engine's option slots).  None when the engine is unavailable
        or off (``LOOPER_LAYA=off``), so the caller keeps the lexical best
        match instead of failing the node.
        """
        if not labels:
            return None
        try:
            from AI import laya_client
        except Exception as exc:
            logger.info("[HANDLE][WEB] Laya unavailable: %s", exc)
            return None
        try:
            picked = laya_client.choice(
                "Request: %s" % goal[:self._WEB_STATE_MAX],
                "Which element on the page should be clicked to fulfil the "
                "request? Pick the element whose label answers the request.",
                {label: "" for label in labels},
            )
        except Exception as exc:
            logger.warning("[HANDLE][WEB] Laya pick failed: %s", exc)
            return None
        return picked if picked in labels else None

    def _web_click_marked(self, driver, web_actions, picked):
        """Click a marked candidate through the web click ladder.

        Returns ``(ok, method_or_error)``.  The marker attribute IS the
        locator: it resolves natively in the light DOM and through the
        shadow-piercing JS dispatch inside shadow roots, so the engine's own
        native-first ladder (scroll into view, clickable-ancestor retry, JS
        fallback) does the actuation — never a screenshot coordinate.
        """
        try:
            from ....web import exec_overlay
            from ....web.engine import ReplayConfig
            from ....web.events import Event, Locator
            from ....web.handlers import HANDLERS
        except Exception as exc:
            return False, "web click modules unavailable: %s" % exc
        selector = '[%s="%d"]' % (web_actions.MARKER_ATTR, picked['i'])
        event = Event(
            type="click", ts=time.time(), locator=Locator(css=selector)
        )
        try:
            # Box the element about to be hit (the same execution overlay a
            # web node shows); purely cosmetic — never fail the click on it.
            exec_overlay.enable(driver)
            exec_overlay.mark(
                driver, event, "handle: %s" % picked['label']
            )
        except Exception:
            pass
        try:
            result = HANDLERS["click"].execute(
                driver, event, ReplayConfig()
            )
        except Exception as exc:
            return False, str(exc)
        if not result.get("ok"):
            return False, result.get("error") or "click ladder failed"
        return True, result.get("method") or "click"

    # ------------------------------------------------------------------
    # Pipeline: screenshot -> LocateAnything -> execute
    # ------------------------------------------------------------------

    def _execute_handle_pipeline(
        self, node_id, action_type, goal_description, stop_flag,
        target_description=None,
    ):
        """Run the simplified pipeline: screenshot, ground via LocateAnything, execute.

        Drag actions perform two grounding passes — one for the source
        object and one for the target destination.
        """
        logger.info("[HANDLE] Starting grounding pipeline for node %s", node_id)

        llamacpp_url = self._resolve_api_url()

        # Step 1: Take a fresh screenshot, record its pixel dimensions
        screenshot_b64, img_w, img_h, _ = self._take_screenshot_b64()
        if not screenshot_b64:
            logger.error("[HANDLE] Failed to take screenshot — aborting")
            return ""

        if stop_flag and stop_flag():
            return ""

        if action_type == 'drag':
            # ── Drag: two-stage grounding ──
            # Stage 1: locate the source object being dragged
            src_coords = self._vlm_get_coordinates(
                llamacpp_url, screenshot_b64, img_w, img_h, action_type,
                goal_description, stop_flag,
            )
            if not src_coords:
                logger.error("[HANDLE] Failed to locate drag source — aborting")
                return ""
            logger.info(
                "[HANDLE] Drag source '%s' grounded at %s",
                goal_description, src_coords,
            )

            if stop_flag and stop_flag():
                return ""

            # Stage 2: locate the target destination
            dst_prompt = target_description or goal_description
            dst_coords = self._vlm_get_coordinates(
                llamacpp_url, screenshot_b64, img_w, img_h, action_type,
                dst_prompt, stop_flag,
            )
            if not dst_coords:
                logger.error("[HANDLE] Failed to locate drag target — aborting")
                return ""
            logger.info(
                "[HANDLE] Drag target '%s' grounded at %s",
                dst_prompt, dst_coords,
            )

            if stop_flag and stop_flag():
                return ""

            # Step 3: Execute the drag (mouse down, move, mouse up)
            self._handle_drag(src_coords, dst_coords, stop_flag)

            # Return a summary for downstream
            return json.dumps({
                "action": action_type,
                "source": src_coords,
                "target": dst_coords,
                "goal": goal_description,
                "target_description": dst_prompt,
            })

        # ── Click: single grounding pass ──
        coordinates = self._vlm_get_coordinates(
            llamacpp_url, screenshot_b64, img_w, img_h, action_type,
            goal_description, stop_flag,
        )

        if stop_flag and stop_flag():
            return ""

        if not coordinates:
            logger.error("[HANDLE] Failed to get coordinates — aborting")
            return ""

        # Step 3: Execute the action
        self._execute_action(action_type, coordinates, stop_flag)

        # Return a summary for downstream
        return json.dumps({
            "action": action_type,
            "coordinates": coordinates,
            "goal": goal_description,
        })



    def _find_mtmd_cli(self):
        """Find the llama-mtmd-cli executable (used by the API for grounding models)."""
        candidates = []

        # Check from AI/models dir up to utils/llama.cpp/build/...
        try:
            from AI.config_loader import get_models_dir
            md = get_models_dir()
            candidates.append(
                os.path.normpath(
                    os.path.join(md, "..", "..", "..", "utils", "llama.cpp", "build-vulkan", "bin", "Release", "llama-mtmd-cli.exe")
                )
            )
            candidates.append(
                os.path.normpath(
                    os.path.join(md, "..", "..", "..", "utils", "llama.cpp", "build", "bin", "Release", "llama-mtmd-cli.exe")
                )
            )
        except Exception:
            pass

        # PyInstaller frozen: bundled alongside the exe
        import sys as _sys
        if getattr(_sys, 'frozen', False):
            _exe_dir = os.path.dirname(_sys.executable)
            candidates.append(os.path.join(_exe_dir, "bin", "llama-mtmd-cli.exe"))
            candidates.append(os.path.join(_exe_dir, "llama-mtmd-cli.exe"))

        for c in candidates:
            if os.path.exists(c):
                return c
        return None

    def _run_llama_cli(self, model_relative_path, prompt, image_b64, max_tokens=128, temperature=0.1):
        """Run llama-mtmd-cli locally as subprocess (fallback when server API fails).

        mtmd-cli already emits special/control tokens by default — no ``--special`` flag needed.
        """
        try:
            from AI.config_loader import get_models_dir
            models_dir = get_models_dir()
            model_path = os.path.normpath(os.path.join(models_dir, model_relative_path))
            if not os.path.exists(model_path):
                logger.warning("[HANDLE] Model not found: %s", model_path)
                return ""

            # Auto-discover mmproj in same directory
            mmproj_path = ""
            model_dir = os.path.dirname(model_path)
            if os.path.isdir(model_dir):
                for fname in os.listdir(model_dir):
                    if fname.lower().startswith("mmproj-") and fname.lower().endswith(".gguf"):
                        mmproj_path = os.path.join(model_dir, fname)
                        break

            # Locate llama-mtmd-cli.exe (multimodal CLI for grounding models)
            cli_path = self._find_mtmd_cli()
            if not cli_path:
                logger.warning("[HANDLE] llama-mtmd-cli.exe not found")
                return ""

            # Save image to temp file
            fd, img_path = tempfile.mkstemp(suffix=".png", prefix="locate_")
            os.close(fd)
            with open(img_path, "wb") as f:
                f.write(base64.b64decode(image_b64))

            cmd = [
                cli_path,
                "--model", model_path,
                "--image", img_path,
                "-p", prompt,
                "--temp", str(temperature),
                "-n", str(max_tokens),
            ]
            # NOTE: --no-display-prompt is NOT passed — llama-mtmd-cli
            # (unlike llama-cli) does not support that flag.
            if mmproj_path:
                cmd.extend(["--mmproj", mmproj_path])
            logger.info("[HANDLE] Running mtmd-cli: %s --model %s --image <temp> %s",
                         os.path.basename(cli_path), os.path.basename(model_path),
                         f"--mmproj {os.path.basename(mmproj_path)}" if mmproj_path else "(no mmproj)")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,
            )

            # Clean up temp image
            try:
                os.remove(img_path)
            except Exception:
                pass

            output = result.stdout.strip()
            if output:
                logger.info("[HANDLE] mtmd-cli output: %.200s", output)
                return output
            if result.stderr:
                logger.warning("[HANDLE] mtmd-cli stderr: %.200s", result.stderr)
            return ""

        except Exception as e:
            logger.error("[HANDLE] mtmd-cli subprocess failed: %s", e)
            return ""

    def _get_sandbox_url(self):
        """Return the sandbox agent URL when the Handle node runs sandboxed."""
        try:
            ah = getattr(self, 'action_handlers', None)
            if ah is not None and getattr(ah, 'bot', None) is not None:
                u = getattr(ah.bot, 'sandbox_agent_url', None)
                if u:
                    return str(u)
        except Exception:
            pass
        try:
            from ....computer_vision import get_sandbox_agent_url
            u = get_sandbox_agent_url()
            if u:
                return str(u)
        except Exception:
            pass
        return None

    def _send_agent_action(self, payload):
        """POST an action to the sandbox agent; raise on agent-side errors."""
        url = self._get_sandbox_url()
        if not url:
            return False
        import urllib.request
        req = urllib.request.Request(
            f"{str(url).rstrip('/')}/action",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if isinstance(data, dict) and data.get("status") == "error":
            raise RuntimeError(data.get("message") or "Sandbox action failed")
        return True

    def _validate_coordinates(self, x, y):
        """Validate coordinates are within screen bounds before clicking.

        In sandbox mode the bounds come from the RDP agent's screen size;
        otherwise pyautogui.size(). Returns True if safe to click.
        """
        import pyautogui as _pg
        try:
            sw, sh = getattr(self, '_sandbox_screen_size', None) or _pg.size()
        except Exception:
            sw, sh = _pg.size()
        if x < 0 or y < 0 or x >= sw or y >= sh:
            logger.error(
                "[HANDLE] Coordinates (%d, %d) out of bounds (%dx%d) — skipping",
                x, y, sw, sh,
            )
            return False
        return True


    def _parse_locate_anything_output(self, text, img_w, img_h):
        """Parse LocateAnything-3B special-token output into pixel coordinates.

        The model outputs normalized coordinates (tokens 0-999) relative to
        the input image dimensions.  The conversion uses pyautogui.size()
        (logical screen resolution) because pyautogui.click() operates in
        logical coordinates — this handles HiDPI where the screenshot
        physical pixels may differ from logical screen pixels.

        Output format: <ref>label</ref><box><X1><Y1><X2><Y2></box>
        where <0>..<999> are normalized coordinate tokens (0-999)
        Returns (pixel_x, pixel_y) or None.
        """
        import pyautogui as _pg
        import re
        text = text.strip()

        # The model normalises its output to the image it sees; convert using
        # the image dimensions the screenshot helper returned (already DPI-
        # normalized to the click coordinate space, including in sandbox mode).
        # Fall back to pyautogui.size() only if the dims are unknown.
        sw, sh = int(img_w or 0), int(img_h or 0)
        if sw <= 0 or sh <= 0:
            sw, sh = _pg.size()

        # Sanitise for logging: replace non-ASCII chars to avoid
        # UnicodeEncodeError when writing to cp1252 console.
        log_safe = text.encode('ascii', errors='replace').decode('ascii')
        logger.info(
            "[HANDLE] Parse: screenshot=(%d,%d) pyautogui.size()=(%d,%d) "
            "model_text=[%.120s]",
            img_w, img_h, sw, sh, log_safe,
        )

        # Detect explicit failure: model returned <box>None</box>
        if re.search(r'<box>\s*None\s*</box>', text, re.IGNORECASE):
            logger.warning(
                "[HANDLE] Parse FAILED: model returned <box>None</box> — "
                "could not find element matching prompt"
            )
            return None

        # Try <box><X1><Y1><X2><Y2></box> format
        # Allow for optional whitespace between tokens since model output
        # formatting can vary between server and local CLI.
        box_match = re.search(
            r'<box>\s*<\s*(\d+)\s*>\s*<\s*(\d+)\s*>\s*<\s*(\d+)\s*>\s*<\s*(\d+)\s*>\s*</box>',
            text, re.IGNORECASE,
        )
        if box_match:
            try:
                x1 = int(box_match.group(1))
                y1 = int(box_match.group(2))
                x2 = int(box_match.group(3))
                y2 = int(box_match.group(4))
                # Use center of bounding box
                cx_norm = (x1 + x2) / 2
                cy_norm = (y1 + y2) / 2
                # Convert from normalized 0-999 to screen pixels
                px = int(cx_norm / 999 * sw)
                py = int(cy_norm / 999 * sh)
                logger.info(
                    "[HANDLE] Parse OK (box): norm=(%.1f,%.1f) -> pixel=(%d,%d)",
                    cx_norm, cy_norm, px, py,
                )
                # ponytail: full-screen box = mmproj likely missing
                if x1 <= 1 and y1 <= 1 and x2 >= 998 and y2 >= 998:
                    logger.warning(
                        "[HANDLE] Model returned full-screen bounding box — "
                        "mmproj projector file is likely missing. "
                        "Ensure mmproj-LocateAnything-3B-BF16.gguf is in the models directory."
                    )
                return (px, py)
            except (ValueError, IndexError):
                pass

        # Try <point><X><Y></point> format (if model outputs point directly)
        point_match = re.search(
            r'<point>\s*<\s*(\d+)\s*>\s*<\s*(\d+)\s*>\s*</point>',
            text, re.IGNORECASE,
        )
        if point_match:
            try:
                nx = int(point_match.group(1))
                ny = int(point_match.group(2))
                px = int(nx / 999 * sw)
                py = int(ny / 999 * sh)
                logger.info(
                    "[HANDLE] Parse OK (point): norm=(%d,%d) -> pixel=(%d,%d)",
                    nx, ny, px, py,
                )
                return (px, py)
            except (ValueError, IndexError):
                pass

        # Fallback: extract plain numbers from text — only use if they look
        # like valid normalized coordinates (0-999) so we don't accidentally
        # pick up metadata or error text as click coordinates.
        nums = re.findall(r'\b(\d{1,5})\b', text)
        if len(nums) >= 2:
            px = int(nums[0])
            py = int(nums[1])
            sw_int, sh_int = int(sw), int(sh)
            # Discard if numbers are way out of screen range (likely garbage)
            if (px > sw_int * 2 or py > sh_int * 2):
                logger.warning(
                    "[HANDLE] Parse FALLBACK rejected (%s,%s) — out of plausible range",
                    nums[0], nums[1],
                )
            else:
                logger.warning(
                    "[HANDLE] Parse FALLBACK (plain numbers): (%s,%s) -> pixel=(%d,%d)",
                    nums[0], nums[1], px, py,
                )
                return (px, py)

        logger.warning("[HANDLE] Parse FAILED: %.150s", text)
        return None


    def _vlm_get_coordinates(
        self, api_url, screenshot_b64, img_w, img_h, action_type,
        goal_description, stop_flag,
    ):
        """Use LocateAnything-3B to get specific screen pixel coordinates.

        The goal_description is used directly as the grounding prompt.
        Tries API gateway first, falls back to local llama-cli subprocess.
        Output format: <ref>label</ref><box><x1><y1><x2><y2></box>
        Coordinates are normalized 0-999, converted to screen pixels.
        """
        # LocateAnything uses a Qwen2.5-VL chat template:
        #   <|im_start|>user\n{media_marker}...<|im_end|>\n
        #   <|im_start|>assistant\n<ref>label</ref><box>...</box>
        #
        # Prompt template MUST match what the model was trained on.
        # From the official locateanything_worker.py:
        #   ground_gui    → "Locate the region that matches the following
        #                    description: {phrase}."
        #   ground_single → "Locate a single instance that matches the
        #                    following description: {phrase}."
        # Generic "Locate X" was never in the training set and causes the
        # model to fall back to dense OCR/grounding of all text on screen.
        prompt = (
            f"Locate the region that matches the following"
            f" description: {goal_description}."
        )

        text = self._call_llamacpp_api(
            api_url=api_url,
            model=self._LOCATE_MODEL,
            prompt=prompt,
            images=[screenshot_b64],
            temperature=0.1,
            max_tokens=64,
        )

        # If API returned empty, try local llama-cli subprocess
        if not text:
            logger.info("[HANDLE] API returned empty, trying local llama-cli...")
            text = self._run_llama_cli(
                model_relative_path=self._LOCATE_MODEL,
                prompt=prompt,
                image_b64=screenshot_b64,
                max_tokens=64,
                temperature=0.1,
            )

        if not text:
            return None

        return self._parse_locate_anything_output(text, img_w, img_h)

    # ------------------------------------------------------------------
    # Action execution
    # ------------------------------------------------------------------

    def _execute_action(self, action_type, coordinates, stop_flag):
        """Execute the action at the given coordinates."""
        x, y = coordinates

        try:
            if action_type == 'click':
                self._handle_click(x, y, stop_flag)
            else:
                logger.warning("[HANDLE] Unknown action: %s", action_type)
        except Exception as e:
            logger.error("[HANDLE] Action execution failed: %s", e)

    def _handle_click(self, x, y, stop_flag):
        """Click at the given coordinates.

        Validates bounds before clicking to prevent accidental
        corner clicks from invalid parsed coordinates.
        """
        if not self._validate_coordinates(x, y):
            logger.error("[HANDLE] Invalid coordinates (%d, %d) — aborting click", x, y)
            return
        logger.info("[HANDLE] Click at (%d, %d)", x, y)
        if self._get_sandbox_url():
            # Route through the RDP agent so the click lands in the sandbox
            # session instead of moving the host cursor.
            self._send_agent_action({"action": "click", "x": x, "y": y, "duration": 0.15})
            return
        if hasattr(self, 'action_handlers') and self.action_handlers:
            ah = self.action_handlers
            if hasattr(ah, 'bot'):
                ah.bot.human_mouse_move(x, y)
                ah.bot.human_click(button="left")
            else:
                import pyautogui
                pyautogui.moveTo(x, y, duration=0.2)
                pyautogui.click(x, y)
        else:
            import pyautogui
            pyautogui.moveTo(x, y, duration=0.2)
            pyautogui.click(x, y)

    def _handle_drag(self, src, dst, stop_flag):
        """Drag from the source coordinates to the target coordinates.

        Sequence: navigate to the source object, hold the mouse click
        down, move to the target location, release the mouse click.
        """
        sx, sy = src
        tx, ty = dst
        if not self._validate_coordinates(sx, sy):
            logger.error(
                "[HANDLE] Invalid drag source (%d, %d) — aborting", sx, sy
            )
            return
        if not self._validate_coordinates(tx, ty):
            logger.error(
                "[HANDLE] Invalid drag target (%d, %d) — aborting", tx, ty
            )
            return
        logger.info(
            "[HANDLE] Drag from (%d, %d) to (%d, %d)", sx, sy, tx, ty
        )

        if self._get_sandbox_url():
            self._send_agent_action({
                "action": "drag_drop",
                "start_x": sx, "start_y": sy,
                "end_x": tx, "end_y": ty,
                "duration": 0.2,
            })
            return

        import pyautogui

        if hasattr(self, 'action_handlers') and self.action_handlers:
            ah = self.action_handlers
            if hasattr(ah, 'bot'):
                # Navigate to source, hold, move to target, release
                ah.bot.human_mouse_move(sx, sy)
                pyautogui.mouseDown()
                ah.bot.human_mouse_move(tx, ty)
                pyautogui.mouseUp()
                return

        # Fallback: plain pyautogui drag
        pyautogui.moveTo(sx, sy, duration=0.2)
        pyautogui.mouseDown()
        pyautogui.moveTo(tx, ty, duration=0.3)
        pyautogui.mouseUp()

    # ------------------------------------------------------------------
    # Screenshot helper
    # ------------------------------------------------------------------

    def _take_screenshot_b64(self):
        """Take a screenshot of the PRIMARY monitor and return
        (base64, width_px, height_px, pil_image).

        In sandbox mode the screenshot comes from the RDP agent and is DPI-
        normalized to the agent's logical screen size, so the returned
        dimensions always match the coordinate space clicks are sent in.

        Captures ONLY the primary monitor (region (0,0,sw,sh)) so the image
        dimensions always match pyautogui.size().  Without the region clamp,
        pyautogui.screenshot() grabs the entire virtual desktop (all monitors)
        while pyautogui.size() reports only the primary — causing the model's
        normalised coordinates to be converted against the wrong width/height
        on multi-monitor setups.

        Returns ("", 0, 0, None) on failure.
        """
        try:
            url = self._get_sandbox_url()
            if url:
                import urllib.request
                import json as _json
                from PIL import Image as _PIL_Image
                req = urllib.request.Request(
                    f"{str(url).rstrip('/')}/action",
                    data=_json.dumps({"action": "screenshot"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=15.0) as resp:
                    payload = _json.loads(resp.read().decode("utf-8"))
                if isinstance(payload, dict) and payload.get("status") == "error":
                    raise RuntimeError(payload.get("message") or "sandbox screenshot failed")
                img_b64 = payload.get("image")
                if not img_b64:
                    raise RuntimeError("no image in sandbox screenshot response")
                screenshot = _PIL_Image.open(io.BytesIO(base64.b64decode(img_b64))).convert("RGB")
                sw = int(payload.get("screen_width") or screenshot.width)
                sh = int(payload.get("screen_height") or screenshot.height)
                if (screenshot.width, screenshot.height) != (sw, sh):
                    screenshot = screenshot.resize((sw, sh), _PIL_Image.LANCZOS)
                self._sandbox_screen_size = (sw, sh)
                width, height = screenshot.size
                logger.debug(
                    "[DPI][sandbox] screenshot=%dx%d  agent.size()=%dx%d",
                    width, height, sw, sh,
                )
                buf = io.BytesIO()
                screenshot.save(buf, format="PNG")
                b64_str = base64.b64encode(buf.getvalue()).decode("utf-8")
                return b64_str, width, height, screenshot
        except Exception as e:
            logger.error("[HANDLE] Sandbox screenshot failed: %s", e)
            return "", 0, 0, None
        try:
            import pyautogui
            sw, sh = pyautogui.size()
            # Capture ONLY the primary monitor so screenshot dims == pyautogui.size()
            screenshot = pyautogui.screenshot(region=(0, 0, sw, sh))
            width, height = screenshot.size
            logger.debug(
                "[DPI] screenshot=%dx%d  pyautogui.size()=%dx%d",
                width, height, sw, sh,
            )
            buf = io.BytesIO()
            screenshot.save(buf, format="PNG")
            b64_str = base64.b64encode(buf.getvalue()).decode("utf-8")
            return b64_str, width, height, screenshot
        except Exception as e:
            logger.error("[HANDLE] Screenshot failed: %s", e)
            return "", 0, 0, None

    # ------------------------------------------------------------------
    # Next node helper
    # ------------------------------------------------------------------

    def _get_handle_next_node(self, node, port):
        """Get the next node ID from the given port connections."""
        connections = node.get('connections', {})
        if port in connections and connections[port]:
            return connections[port][0].get('node_id')
        if port == 'output':
            return "__done__"
        return None
