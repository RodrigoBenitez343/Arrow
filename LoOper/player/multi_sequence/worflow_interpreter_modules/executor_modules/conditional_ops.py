import logging
import os
logger = logging.getLogger(__name__)
try:
    from logging_setup import log_block
except Exception:
    def log_block(logger, level, title, content, lang="text"):
        text = content if isinstance(content, str) else repr(content)
        logger.log(level, "=== %s ===\n%s", title, text)
from ...llm_executor_resources.executor import _strip_think_tags
from ...llm_executor_resources.config_utils import get_default_api_url
from ....screenshot_cleanup import cleanup_screenshot

class ConditionalMixin:
    def _verify_app_running(self, app_context, timeout=2.0):
        """
        Passively verify that the expected app process is running, WITHOUT
        modifying window focus, z-order, or display resolution.
        
        Conditionals must only *observe* the screen — they must never bring
        windows to the foreground or call SetForegroundWindow / ShowWindow /
        BringWindowToTop, as those calls can trigger DPI scaling changes on
        Windows, especially in nested chains where last_app_context may leak
        from a parent chain.
        
        Returns True if the process is found running (or no context given),
        False if the process is not found within the timeout.
        """
        try:
            import time
            import psutil
        except Exception:
            return True

        exe = str((app_context or {}).get("exe") or "")
        proc_name = str((app_context or {}).get("process_name") or "")
        pid_hint = (app_context or {}).get("pid")
        sandboxed = bool((app_context or {}).get("sandboxed") or (getattr(self, "global_app_context_override", {}) or {}).get("sandboxed"))

        if sandboxed:
            return True

        if not exe and not proc_name and not pid_hint:
            return True

        exe_norm = os.path.normcase(exe) if exe else ""
        proc_name_norm = proc_name.lower() if proc_name else ""
        deadline = time.time() + float(timeout or 0.0)

        while time.time() <= deadline:
            try:
                if pid_hint:
                    try:
                        p = psutil.Process(int(pid_hint))
                        if p.is_running():
                            return True
                    except Exception:
                        pass
                for p in psutil.process_iter(["pid", "name", "exe"]):
                    try:
                        if exe_norm:
                            pexe = os.path.normcase(str(p.info.get("exe") or ""))
                            if pexe and pexe == exe_norm:
                                return True
                        if proc_name_norm:
                            pn = str(p.info.get("name") or "").lower()
                            if pn and pn == proc_name_norm:
                                return True
                    except Exception:
                        continue
            except Exception:
                pass
            time.sleep(0.1)

        logger.info(f"Conditional: expected app not running (exe={exe!r}, proc={proc_name!r}, pid={pid_hint!r})")
        return False

    def _get_expected_app_context(self, conditional_node):
        try:
            if isinstance(conditional_node, dict):
                ctx = conditional_node.get("app_context") or conditional_node.get("focus_app") or conditional_node.get("app")
                if isinstance(ctx, dict) and any(ctx.get(k) for k in ("exe", "process_name", "title", "pid")):
                    return ctx
        except Exception:
            pass

        try:
            fh = getattr(self, "fallback_handler", None)
            ctx = getattr(fh, "last_app_context", None) if fh is not None else None
            if isinstance(ctx, dict) and any(ctx.get(k) for k in ("exe", "process_name", "title", "pid")):
                return ctx
        except Exception:
            pass

        return None

    def _ensure_app_open_and_focused(self, app_context, stop_flag, timeout=2.0):
        try:
            import time
            import psutil
        except Exception:
            return True
        try:
            sandboxed = bool((app_context or {}).get("sandboxed") or (getattr(self, "global_app_context_override", {}) or {}).get("sandboxed"))
        except Exception:
            sandboxed = False
        if sandboxed:
            return True

        try:
            import pygetwindow as gw
        except Exception:
            gw = None

        try:
            import win32gui
            import win32process
            import win32con
        except Exception:
            win32gui = None
            win32process = None
            win32con = None

        try:
            from pywinauto import Application
        except Exception:
            Application = None

        exe = str((app_context or {}).get("exe") or "")
        proc_name = str((app_context or {}).get("process_name") or "")
        title_hint = str((app_context or {}).get("title") or "")
        pid_hint = (app_context or {}).get("pid")

        exe_norm = os.path.normcase(exe) if exe else ""
        proc_name_norm = proc_name.lower() if proc_name else ""

        deadline = time.time() + float(timeout or 0.0)

        def _foreground_hwnd():
            try:
                if gw is not None:
                    w = gw.getActiveWindow()
                    if w is not None:
                        h = getattr(w, "_hWnd", None)
                        if h:
                            return int(h)
            except Exception:
                pass
            try:
                if win32gui is not None:
                    return int(win32gui.GetForegroundWindow())
            except Exception:
                return None
            return None

        def _matches_process(p):
            try:
                if exe_norm:
                    pexe = ""
                    try:
                        pexe = os.path.normcase(str(p.info.get("exe") or ""))
                    except Exception:
                        pexe = ""
                    if pexe and pexe == exe_norm:
                        return True
                if proc_name_norm:
                    pn = str(p.info.get("name") or "").lower()
                    if pn and pn == proc_name_norm:
                        return True
                return False
            except Exception:
                return False

        def _candidate_pids():
            pids = []
            try:
                if pid_hint:
                    try:
                        p = psutil.Process(int(pid_hint))
                        if p.is_running():
                            pids.append(int(pid_hint))
                    except Exception:
                        pass
                for p in psutil.process_iter(["pid", "name", "exe"]):
                    if _matches_process(p):
                        pids.append(int(p.info["pid"]))
            except Exception:
                pass
            seen = set()
            out = []
            for p in pids:
                if p not in seen:
                    seen.add(p)
                    out.append(p)
            return out

        def _window_candidates_for_pid(pid):
            wins = []
            try:
                if gw is not None:
                    for w in gw.getAllWindows():
                        try:
                            hwnd = getattr(w, "_hWnd", None)
                            if not hwnd:
                                continue
                            hwnd = int(hwnd)
                            if win32process is None:
                                continue
                            _tid, wpid = win32process.GetWindowThreadProcessId(hwnd)
                            if int(wpid) != int(pid):
                                continue
                            if win32gui is not None and not win32gui.IsWindowVisible(hwnd):
                                continue
                            t = str(getattr(w, "title", "") or "")
                            wins.append((hwnd, t))
                        except Exception:
                            continue
            except Exception:
                wins = []

            if wins:
                return wins

            try:
                if win32gui is None or win32process is None:
                    return wins

                def cb(hwnd, _):
                    try:
                        if not win32gui.IsWindowVisible(hwnd):
                            return
                        _tid, wpid = win32process.GetWindowThreadProcessId(hwnd)
                        if int(wpid) != int(pid):
                            return
                        t = str(win32gui.GetWindowText(hwnd) or "")
                        if not t:
                            return
                        wins.append((int(hwnd), t))
                    except Exception:
                        return

                win32gui.EnumWindows(cb, None)
            except Exception:
                pass
            return wins

        def _pick_window(wins):
            if not wins:
                return None
            if title_hint:
                th = title_hint.strip().lower()
                if th:
                    for hwnd, t in wins:
                        if th in str(t or "").lower():
                            return int(hwnd)
            return int(wins[0][0])

        def _focus_hwnd(hwnd, pid):
            if not hwnd:
                return False
            try:
                if Application is not None:
                    try:
                        app = Application(backend="uia").connect(process=int(pid))
                        w = app.top_window()
                        try:
                            w.set_focus()
                        except Exception:
                            w.set_focus()
                    except Exception:
                        pass
            except Exception:
                pass

            try:
                if win32gui is not None:
                    try:
                        if win32con is not None:
                            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                    except Exception:
                        pass
                    try:
                        win32gui.SetForegroundWindow(hwnd)
                    except Exception:
                        try:
                            win32gui.BringWindowToTop(hwnd)
                        except Exception:
                            pass
            except Exception:
                pass

            fhwnd = _foreground_hwnd()
            if fhwnd and int(fhwnd) == int(hwnd):
                return True
            return False

        while time.time() <= deadline:
            if stop_flag and stop_flag():
                return False

            pids = _candidate_pids()
            if not pids:
                time.sleep(0.05)
                continue

            for pid in pids:
                if stop_flag and stop_flag():
                    return False
                wins = _window_candidates_for_pid(pid)
                hwnd = _pick_window(wins)
                if hwnd and _focus_hwnd(hwnd, pid):
                    return True

            time.sleep(0.05)

        return False

    @staticmethod
    def _is_web_conditional(conditional_node):
        """True when the node is a web-mode conditional (routes on the browser)."""
        value = conditional_node.get('web_mode')
        if isinstance(value, bool):
            return value
        if value is None:
            return str(conditional_node.get('condition_type') or '').strip().lower() == 'web'
        return str(value).strip().lower() in ('true', '1', 'yes', 'y', 'on')

    def _evaluate_web_condition(self, conditional_node, stop_flag, node_inputs=None):
        """Evaluate a web-mode conditional on the chain's shared browser.

        The browser is the SAME shared instance the web sequence nodes use, so
        the condition observes the page the last sequence left open - place the
        conditional after a web sequence that navigated to the page.

        Resolved straight through the shared workbench session: this graph
        executor owns no web driver (``_get_or_create_web_driver`` lives on the
        sequence executor), so asking it for one always raised and every web
        conditional in a chain run evaluated False.
        """
        try:
            from ...conditional_fallback_resources.web_conditions import evaluate as evaluate_web_condition
        except Exception as exc:
            logger.error(f"Web conditional module unavailable: {exc}")
            return False

        try:
            from ....web.session import SHARED_WEB_SCOPE, ensure_workbench_session
            driver = ensure_workbench_session(chain_key=SHARED_WEB_SCOPE)
        except Exception as exc:
            logger.error(f"Web conditional: browser unavailable: {exc}")
            return False
        if driver is None:
            logger.error(
                "Web conditional: no shared browser available - place the "
                "conditional after a web sequence that opened the page"
            )
            return False

        try:
            return bool(evaluate_web_condition(
                conditional_node, driver,
                llm_evaluator=self._evaluate_llm_condition,
                stop_flag=stop_flag,
            ))
        except Exception as exc:
            logger.error(f"Web conditional evaluation failed: {exc}")
            return False

    def _evaluate_conditional(self, conditional_node, stop_flag, node_inputs=None):
        """
        Evaluate a conditional node to determine which branch to take.
        
        Args:
            conditional_node (dict): The conditional node configuration
            stop_flag (callable): Stop flag function
            
        Returns:
            bool: True if condition is met, False otherwise
        """
        # Web mode: route on the chain's shared browser instead of the desktop
        # visual conditions (see conditional_fallback_resources/web_conditions.py).
        if self._is_web_conditional(conditional_node):
            return self._evaluate_web_condition(conditional_node, stop_flag, node_inputs=node_inputs)
        raw_trigger_type = conditional_node.get('trigger_type') or conditional_node.get('condition_type', 'presence')
        if str(raw_trigger_type).lower() in ('code', 'code_conditional', 'python'):
            return self._evaluate_code_condition(conditional_node, stop_flag, node_inputs=node_inputs)
        if str(raw_trigger_type).strip().lower() in ('llm', 'llm_conditional', 'llm_condition', 'ai_conditional'):
            return self._evaluate_llm_condition(conditional_node, stop_flag, node_inputs=node_inputs)
        if str(raw_trigger_type).strip().lower() in ('layout_match', 'layout_match_conditional', 'layout_match_trigger', 'layout_match_condition'):
            return self._evaluate_layout_match_conditional(conditional_node, stop_flag)
        condition_type_mapping = {
            'presence': 'presence_trigger',
            'absence': 'absence_trigger',
            'ocr': 'ocr_trigger',
            'presence_trigger': 'presence_trigger',
            'absence_trigger': 'absence_trigger',
            'ocr_trigger': 'ocr_trigger'
        }
        condition_type = condition_type_mapping.get(raw_trigger_type, 'presence_trigger')
        
        case_sensitive = conditional_node.get('case_sensitive', True)
        if isinstance(case_sensitive, str):
            case_sensitive = case_sensitive.strip().lower() in ('true', '1', 'yes', 'y')
        elif case_sensitive is None:
            case_sensitive = True
        else:
            case_sensitive = bool(case_sensitive)
        
        condition_config = {
            'image_path': conditional_node.get('image_path', ''),
            'image_data': conditional_node.get('image_data', ''),
            'confidence': float(conditional_node.get('threshold', conditional_node.get('confidence', 0.8))),
            'timeout': float(conditional_node.get('wait_time', conditional_node.get('timeout', 10))),
            'max_loops': int(conditional_node.get('max_loops', 10)),
            'target_text': conditional_node.get('ocr_text', '') or conditional_node.get('target_text', ''),
            'case_sensitive': case_sensitive,
            'language': conditional_node.get('language', 'eng'),
            'region': conditional_node.get('region'),
            'strict_validation': conditional_node.get('strict_validation', False)
        }
        
        # Also check if there's a nested condition_data (for backward compatibility)
        if 'condition_data' in conditional_node:
            nested_config = conditional_node['condition_data']
            condition_config.update(nested_config)
        
        logger.info(f"Evaluating conditional with raw trigger type: {raw_trigger_type} -> mapped to: {condition_type}")
        logger.debug(f"Condition config: {condition_config}")

        expected_app = self._get_expected_app_context(conditional_node)
        if hasattr(self, "global_app_context_override") and isinstance(self.global_app_context_override, dict) and self.global_app_context_override:
            try:
                if not expected_app:
                    expected_app = {}
                if isinstance(expected_app, dict):
                    expected_app.update(self.global_app_context_override)
            except Exception:
                pass
        if expected_app:
            try:
                ok = self._verify_app_running(expected_app, timeout=float(expected_app.get("focus_timeout", 2.0)))
            except Exception:
                ok = True
            if not ok:
                logger.info("Conditional gated: expected app not running")
                return False
        
        # Set stop flag for the fallback handler
        self.fallback_handler.stop_flag = stop_flag
        
        # Capture a single screenshot for visual trigger evaluations to improve performance
        screen_cv = None
        if condition_type in ['presence_trigger', 'absence_trigger']:
            screen_cv = self.fallback_handler.template_matcher.capture_screen()
        
        if condition_type == 'presence_trigger':
            return self.fallback_handler.check_presence_trigger(condition_config, screen_cv=screen_cv)
        elif condition_type == 'absence_trigger':
            timeout = float(condition_config.get('timeout', 10))
            return self.fallback_handler.check_absence_trigger(condition_config, timeout, screen_cv=screen_cv)
        elif condition_type == 'ocr_trigger':
            return self.fallback_handler.check_ocr_trigger(condition_config, stop_flag=stop_flag)
        else:
            logger.warning(f"Unknown condition type: {condition_type} (raw: {raw_trigger_type})")
            return False

    def _evaluate_layout_match_conditional(self, conditional_node, stop_flag):
        """
        Layout Match Conditional - Pixel-by-pixel alignment.
        
        Scans the screen line by line by scrolling one pixel at a time,
        trying to find the sample image at the exact expected position
        with no pixel tolerance.
        
        This is useful for aligning UI elements to specific positions
        to make automation tasks easier.
        """
        import time
        import os
        try:
            import pyautogui
        except Exception:
            pyautogui = None
        try:
            from PIL import Image
        except Exception:
            Image = None

        # Priority: embedded base64 image_data, then file path
        image_data = conditional_node.get('image_data', '') or ''
        image_path = conditional_node.get('image_path', '') or ''
        
        if not image_data and not str(image_path).strip():
            logger.warning("Layout match: no image data or path provided")
            return False

        if Image is None:
            logger.warning("Layout match: PIL not available")
            return False

        # Load template - Priority 1: embedded base64 data
        template_img = None
        if image_data:
            try:
                from ....player.image_utils import base64_to_pil
                template_img = base64_to_pil(image_data)
                if template_img:
                    logger.debug("Layout match: loaded template from base64 image_data")
            except Exception as e:
                logger.debug(f"Layout match: base64 load failed: {e}")

        # Load template - Priority 2: file path
        if template_img is None and str(image_path).strip():
            def _resolve_image(p):
                try:
                    pr = getattr(self.fallback_handler, 'path_resolver', None)
                    if pr is not None:
                        rp = pr.resolve_path(p, add_json=False)
                        if rp:
                            return rp
                except Exception:
                    pass
                return p

            resolved_image_path = _resolve_image(str(image_path))
            if not os.path.exists(resolved_image_path):
                logger.warning(f"Layout match: image file not found: {resolved_image_path}")
                return False
            try:
                template_img = Image.open(resolved_image_path).convert("RGB")
            except Exception as e:
                logger.warning(f"Layout match: failed to load template image: {e}")
                return False

        if template_img is None:
            logger.warning("Layout match: failed to load any template image")
            return False

        template_size = template_img.size
        try:
            template_bytes = template_img.tobytes()
        except Exception as e:
            logger.warning(f"Layout match: failed to get template bytes: {e}")
            return False

        expected_x = conditional_node.get('target_x')
        expected_y = conditional_node.get('target_y')
        expected_w = conditional_node.get('target_w')
        expected_h = conditional_node.get('target_h')

        try:
            expected_x_i = int(float(expected_x))
            expected_y_i = int(float(expected_y))
            expected_w_i = int(float(expected_w))
            expected_h_i = int(float(expected_h))
        except Exception:
            logger.warning("Layout match: invalid target coordinates")
            return False
        if expected_w_i <= 0 or expected_h_i <= 0:
            logger.warning("Layout match: invalid target dimensions")
            return False

        try:
            max_attempts = int(conditional_node.get('max_attempts', 500) or 500)
        except Exception:
            max_attempts = 500
        max_attempts = max(1, max_attempts)

        try:
            scroll_direction = int(conditional_node.get('scroll_direction', 1) or 1)  # 1 = up, -1 = down
        except Exception:
            scroll_direction = 1

        # Timeout is disabled by default (0) - search continues until match found
        # Set timeout > 0 to limit search time in seconds
        try:
            timeout = float(conditional_node.get('timeout', 0) or 0)
        except Exception:
            timeout = 0
        if timeout < 0:
            timeout = 0

        logger.info(f"Layout match: starting pixel-by-pixel alignment (size={template_size})")
        logger.info(f"Layout match: target position ({expected_x_i}, {expected_y_i}), size {template_size}")
        logger.info(f"Layout match: max_attempts={max_attempts}, scroll_direction={'up' if scroll_direction > 0 else 'down'}, timeout={'disabled' if timeout <= 0 else f'{timeout}s'}")

        def _capture_region():
            """Capture the screen region at the expected position."""
            try:
                screen = self.fallback_handler.template_matcher.capture_screen()
            except Exception:
                screen = None
            if screen is None:
                if pyautogui is None:
                    return None
                try:
                    screen = pyautogui.screenshot()
                except Exception:
                    return None

            try:
                if hasattr(screen, "convert"):
                    screen_img = screen.convert("RGB")
                else:
                    try:
                        import numpy as _np
                    except Exception:
                        _np = None
                    if _np is None:
                        return None
                    arr = _np.asarray(screen)
                    if arr is None or getattr(arr, "ndim", 0) < 2:
                        return None
                    if arr.ndim == 3 and arr.shape[2] >= 3:
                        rgb = arr[:, :, :3][:, :, ::-1]
                        screen_img = Image.fromarray(rgb.astype("uint8"), "RGB")
                    else:
                        return None
            except Exception:
                return None

            # Calculate region bounds centered on expected position
            left = int(expected_x_i - (expected_w_i // 2))
            top = int(expected_y_i - (expected_h_i // 2))
            right = int(left + expected_w_i)
            bottom = int(top + expected_h_i)

            if left < 0 or top < 0:
                return None
            try:
                sw, sh = screen_img.size
            except Exception:
                return None
            if right > sw or bottom > sh:
                return None

            try:
                region = screen_img.crop((left, top, right, bottom))
            except Exception:
                return None
            return region

        def _region_matches_exactly(region_img):
            """Check if region matches template exactly (no tolerance)."""
            if region_img is None:
                return False
            try:
                if region_img.size != template_size:
                    return False
                # Exact byte comparison - no tolerance
                return region_img.tobytes() == template_bytes
            except Exception:
                return False

        def _do_scroll_one_pixel():
            """Scroll one pixel in the configured direction."""
            # Scroll amount: positive = scroll up (content moves down), negative = scroll down
            # scroll_direction: 1 = up, -1 = down
            # pyautogui.scroll positive = scroll up, negative = scroll down
            scroll_amt = scroll_direction  # +1 for up, -1 for down
            
            ah = getattr(self, 'action_handlers', None)
            if ah is not None and getattr(getattr(ah, 'bot', None), 'sandbox_agent_url', None):
                try:
                    ah._send_agent_action({"action": "scroll", "amount": int(scroll_amt)})
                    return
                except Exception:
                    pass
            if pyautogui is None:
                return
            try:
                pyautogui.scroll(int(scroll_amt))
            except Exception:
                return

        # Main loop: scan line by line with 1-pixel scrolling at MAX SPEED
        # No artificial delays - the system's speed depends on host PC performance
        for attempt in range(max_attempts):
            if stop_flag and stop_flag():
                logger.info("Layout match: stopped by flag")
                return False

            # Check timeout if enabled
            if timeout > 0:
                elapsed = time.time() - start_time
                if elapsed >= timeout:
                    logger.info(f"Layout match: timeout ({timeout}s) reached after {attempt} attempts")
                    return False

            # Capture the region at expected position
            region = _capture_region()
            
            # Check for exact match
            if _region_matches_exactly(region):
                logger.info(f"Layout match: found exact match at attempt {attempt}")
                return True

            # Scroll one pixel and immediately retry - no delay between attempts
            _do_scroll_one_pixel()

        logger.info(f"Layout match: no match found after {max_attempts} attempts")
        return False

    def _evaluate_code_condition(self, conditional_node, stop_flag, node_inputs=None):
        import io
        import contextlib
        import threading
        import time

        code_text = conditional_node.get('code') or conditional_node.get('condition_code') or ''
        if not str(code_text or '').strip():
            return False

        input_data_list = []
        args = {}
        for inp in (node_inputs or []):
            try:
                from_node_id = inp.get('from_node')
                input_port = str(inp.get('input_port') or 'input')
                output_type = str(inp.get('output_type') or inp.get('output_port') or 'output')
                # Strict source-port resolution: 'data' edges carry the value
                # on node_<id>_data, ctx_out/context on node_<id>_context.
                # Exec-only edges (base output, true/false branches) read
                # node_<id>_output — Input nodes publish nothing there, so an
                # Input's value never leaks through control branches.
                ctx_val = None
                out_val = None
                if output_type == 'data':
                    out_val = self.llm_executor.get_variable(f"node_{from_node_id}_data")
                elif output_type in ('ctx_out', 'context'):
                    ctx_val = self.llm_executor.get_variable(f"node_{from_node_id}_context")
                else:
                    # Named output port only — base/branch ports carry no data.
                    named = None
                    if output_type not in ('output', '', 'true', 'false', 'error', 'route'):
                        named = self.llm_executor.get_variable(f"node_{from_node_id}_output_{output_type}")
                    out_val = named
                val = out_val if (out_val is not None and str(out_val).strip()) else ctx_val
                if input_port == 'args':
                    if isinstance(val, dict):
                        args.update(val)
                else:
                    input_data_list.append(val)
            except Exception:
                continue
                
        if len(input_data_list) == 1:
            input_data = input_data_list[0]
        elif len(input_data_list) == 0:
            input_data = None
        else:
            input_data = input_data_list

        # Strip chain-of-thought/reasoning tags from input_data
        # to ensure code conditionals evaluate against clean text
        try:
            if input_data is not None and isinstance(input_data, str):
                input_data = _strip_think_tags(input_data)
        except Exception:
            pass

        node_id = str(conditional_node.get('id') or conditional_node.get('node_id') or '')

        try:
            timeout_s = float(conditional_node.get('timeout', 5.0) or 5.0)
        except Exception:
            timeout_s = 5.0

        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()
        shared_vars = getattr(self.llm_executor, 'variables', {}) if hasattr(self, 'llm_executor') else {}

        try:
            if hasattr(self, '_runtime_initialized') and not getattr(self, '_runtime_initialized', False):
                if hasattr(self, '_initialize_chain_runtime'):
                    self._initialize_chain_runtime()
                try:
                    self._runtime_initialized = True
                except Exception:
                    pass
        except Exception:
            pass
        try:
            if hasattr(self, '_ensure_chain_venv'):
                self._ensure_chain_venv()
            sp = getattr(self, '_venv_site_packages', None)
            if sp and isinstance(sp, str) and sp not in __import__('sys').path and os.path.isdir(sp):
                __import__('sys').path.insert(0, sp)
        except Exception:
            pass
        try:
            if hasattr(self, '_install_code_dependencies'):
                self._install_code_dependencies(str(code_text))
        except Exception:
            pass

        exec_scope = shared_vars
        try:
            exec_scope['input_data'] = input_data
            exec_scope['args'] = args
            exec_scope['variables'] = shared_vars
            exec_scope['get_variable'] = getattr(self.llm_executor, 'get_variable', lambda n, d=None: shared_vars.get(n, d))
            exec_scope['set_variable'] = getattr(self.llm_executor, 'set_variable', lambda n, v: shared_vars.__setitem__(n, v))
            exec_scope['os'] = os
            exec_scope['__name__'] = '__main__'

            # Inject GraphRAG context query into conditional execution namespace
            try:
                _ctx_chain_id = os.path.splitext(
                    os.path.basename(
                        getattr(self, 'chain_id', None)
                        or getattr(self, 'chain_file', 'default_chain')
                    )
                )[0]
                exec_scope['context_query'] = lambda node_id="", question="", scope=None, chain_id=_ctx_chain_id: self._query_context_node(node_id, question, scope, chain_id)
                exec_scope['graphrag'] = self._get_graph_rag()
            except Exception:
                pass
        except Exception:
            pass

        out = {'result': None, 'error': None}

        def _runner():
            try:
                try:
                    exec_scope['result'] = None
                except Exception:
                    pass
                with contextlib.redirect_stdout(stdout_capture), contextlib.redirect_stderr(stderr_capture):
                    exec(str(code_text), exec_scope, exec_scope)
                res = exec_scope.get('result', None)
                if res is None and callable(exec_scope.get('condition')):
                    res = exec_scope.get('condition')()
                if res is None:
                    try:
                        candidates = {}
                        for k, v in dict(exec_scope).items():
                            if not k or str(k).startswith('_'):
                                continue
                            if not callable(v):
                                continue
                            if k in ('condition', 'get_variable', 'set_variable', 'print'):
                                continue
                            try:
                                if getattr(v, '__module__', None) not in (None, '__main__'):
                                    continue
                            except Exception:
                                pass
                            candidates[str(k)] = v
                        preferred = [
                            'check_system_language',
                            'evaluate',
                            'predicate',
                            'check',
                            'run',
                            'main'
                        ]
                        picked = None
                        for name in preferred:
                            if name in candidates:
                                picked = candidates[name]
                                break
                        if picked is None and len(candidates) == 1:
                            picked = next(iter(candidates.values()))
                        if picked is not None:
                            res = picked()
                    except Exception:
                        res = None
                out['result'] = res
            except Exception as e:
                out['error'] = e

        t = threading.Thread(target=_runner, daemon=True)
        t.start()

        start = time.time()
        while t.is_alive():
            if stop_flag and stop_flag():
                return False
            if timeout_s and timeout_s > 0 and (time.time() - start) > timeout_s:
                logger.warning("Code conditional timed out")
                return False
            time.sleep(0.01)

        if out.get('error') is not None:
            try:
                import traceback
                logger.error(f"Code conditional execution failed: {traceback.format_exc()}")
            except Exception:
                logger.error("Code conditional execution failed")
            return False

        res = out.get('result')
        if isinstance(res, bool):
            result_bool = res
        elif isinstance(res, str):
            s = res.strip().lower()
            if s in ('true', '1', 'yes', 'y', 'on'):
                result_bool = True
            elif s in ('false', '0', 'no', 'n', 'off', ''):
                result_bool = False
            else:
                result_bool = bool(s)
        else:
            result_bool = bool(res)

        try:
            if node_id:
                self.llm_executor.set_variable(f"node_{node_id}_conditional_result", result_bool)
        except Exception:
            pass

        return result_bool
    
    def _evaluate_llm_condition(self, conditional_node, stop_flag, node_inputs=None):
        """
        Evaluate an LLM-based conditional by calling a small LLM (Ollama or llama.cpp)
        with the upstream node's output as context and a user-defined prompt.
        
        When llm_use_vision is enabled (for VL models), captures a live screenshot
        and sends it alongside the prompt so the model can evaluate the screen visually.
        
        Args:
            conditional_node (dict): The conditional node configuration
            stop_flag (callable): Stop flag function
            node_inputs (list): List of input connection info from upstream nodes
            
        Returns:
            bool: True if LLM responds True, False otherwise
        """
        import threading
        import time
        import os
        
        llm_prompt = conditional_node.get('llm_prompt') or conditional_node.get('prompt') or ''
        if not str(llm_prompt or '').strip():
            logger.warning("LLM conditional: no prompt configured")
            return False
        
        llm_engine = str(conditional_node.get('llm_engine') or 'ollama').strip().lower()
        llm_model = str(conditional_node.get('llm_model') or conditional_node.get('model') or '').strip()
        if not llm_model:
            # The conditional dialog's model combo can save empty when the
            # model cache was cold at edit time.  Fall back to the chain's
            # own LLM node model (matching engine) so the conditional
            # actually evaluates instead of silently returning False with
            # "no model configured" every time.
            try:
                for _wnode in (self.workflow_graph or {}).values():
                    if _wnode.get('type') != 'llm':
                        continue
                    _wdata = _wnode.get('data', {}) or {}
                    _conf = _wdata.get('llm_configuration', {}) or {}
                    _merged = dict(_wdata)
                    _merged.update(_conf)
                    if llm_engine in ('llamacpp', 'llama.cpp', 'llama_cpp'):
                        _m = str(_merged.get('llamacpp_model_path') or '').strip()
                    else:
                        _m = str(_merged.get('model') or '').strip()
                    if _m:
                        llm_model = _m
                        logger.info(
                            "LLM conditional: no model configured — using "
                            "chain LLM node model %s", llm_model,
                        )
                        break
            except Exception:
                pass
        if not llm_model:
            logger.warning("LLM conditional: no model configured")
            return False
        
        try:
            timeout_s = float(conditional_node.get('llm_timeout') or conditional_node.get('timeout') or 10.0)
        except Exception:
            timeout_s = 10.0
        
        # Vision / screenshot support — reuse the same pipeline as LLM nodes
        use_vision_raw = conditional_node.get('llm_use_vision', 'false')
        use_vision = str(use_vision_raw).strip().lower() in ('true', '1', 'yes')
        images = None
        if use_vision:
            try:
                if stop_flag and stop_flag():
                    return False
                from ...llm_executor_resources.vision_utils import prepare_vision_images
                images, _ocr_text = prepare_vision_images(True, True, stop_flag)
                if images:
                    logger.info(f"LLM conditional: captured vision screenshot ({len(images)} image(s))")
                else:
                    logger.warning("LLM conditional: screenshot capture returned no images, falling back to text-only")
                    use_vision = False
            except Exception as e:
                logger.warning(f"LLM conditional: screenshot capture failed: {e}, falling back to text-only")
                use_vision = False

        def _cleanup_vision_images():
            """Delete the screenshots captured for this conditional once it is done."""
            if images:
                for _p in images:
                    try:
                        cleanup_screenshot(_p)
                    except Exception:
                        pass
        
        # Collect input_data from upstream nodes (same pattern as _evaluate_code_condition)
        input_data_list = []
        for inp in (node_inputs or []):
            try:
                from_node_id = inp.get('from_node')
                input_port = str(inp.get('input_port') or 'input')
                output_type = str(inp.get('output_type') or inp.get('output_port') or 'output')
                # Strict source-port resolution: 'data' edges carry the value
                # on node_<id>_data, ctx_out/context on node_<id>_context.
                # Exec-only edges (base output, true/false branches) read
                # node_<id>_output — Input nodes publish nothing there, so an
                # Input's value never leaks through control branches.
                ctx_val = None
                out_val = None
                if output_type == 'data':
                    out_val = self.llm_executor.get_variable(f"node_{from_node_id}_data")
                elif output_type in ('ctx_out', 'context'):
                    ctx_val = self.llm_executor.get_variable(f"node_{from_node_id}_context")
                else:
                    # Named output port only — base/branch ports carry no data.
                    named = None
                    if output_type not in ('output', '', 'true', 'false', 'error', 'route'):
                        named = self.llm_executor.get_variable(f"node_{from_node_id}_output_{output_type}")
                    out_val = named
                val = out_val if (out_val is not None and str(out_val).strip()) else ctx_val
                if input_port != 'args':
                    input_data_list.append(val)
            except Exception:
                continue
        
        # Deduplicate identical upstream values: a node's 'output' and
        # 'context' ports frequently resolve to the SAME text (both fall
        # back to node_<id>_output), so without this the context injected
        # into the classifier prompt is doubled.
        try:
            _seen_vals = set()
            _deduped = []
            for _v in input_data_list:
                _key = str(_v)
                if _key not in _seen_vals:
                    _seen_vals.add(_key)
                    _deduped.append(_v)
            input_data_list = _deduped
        except Exception:
            pass
        
        if len(input_data_list) == 1:
            input_data = input_data_list[0]
        elif len(input_data_list) == 0:
            input_data = None
        else:
            input_data = '\n---\n'.join([str(d) for d in input_data_list if d])
        
        # Strip CoT/reasoning tags from input_data
        try:
            if input_data is not None and isinstance(input_data, str):
                input_data = _strip_think_tags(input_data)
        except Exception:
            pass
        
        # Build the final prompt with context injected (skip context for vision-only evaluation)
        full_prompt = str(llm_prompt)
        if not use_vision and input_data is not None and str(input_data).strip():
            full_prompt = f"{full_prompt}\n\nContext:\n{str(input_data)}"
        
        node_id = str(conditional_node.get('id') or conditional_node.get('node_id') or '')
        
        out = {'result': None, 'error': None}
        
        def _runner():
            try:
                if llm_engine in ('llamacpp', 'llama.cpp', 'llama_cpp'):
                    # Use llama.cpp endpoint via API — no timeout, wait for model
                    # Same path as regular LLM nodes: chat template applied,
                    # truncated generations continued until the model finishes,
                    # and the engine strips the thinking so only the final
                    # answer reaches the parser.  The token budget must be large
                    # enough for thinking + answer (SmolLM3 burned a 32-token
                    # budget on a <think> block alone, which stripped to an
                    # empty response and defaulted the parser to False).
                    import requests as _requests
                    api_url = get_default_api_url()
                    api_url = api_url.rstrip('/')
                    payload = {
                        "model": llm_model,
                        "prompt": full_prompt,
                        "temperature": 0.1,
                        "max_tokens": 256,
                        "stream": False,
                        "use_llamacpp": True,
                        "gpu_layers": 0,
                        "threads": -1,
                        "context_size": 0,
                    }
                    if use_vision and images:
                        payload["images"] = images
                        logger.info(f"LLM conditional: including {len(images)} image(s) in llama.cpp payload")
                    resp = _requests.post(
                        f"{api_url}/llamacpp/generate",
                        json=payload,
                        timeout=None,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        text = data.get('response', '')
                    else:
                        out['error'] = f"llama.cpp error ({resp.status_code}): {resp.text[:200]}"
                        return
                else:
                    # Use Ollama via API — no timeout, wait for model
                    from AI.consult import OllamaClient
                    client = OllamaClient(base_url=get_default_api_url())
                    images_arg = images if use_vision and images else None
                    response = client.chat(
                        model=llm_model,
                        prompt=full_prompt,
                        temperature=0.1,
                        max_tokens=256,
                        images=images_arg,
                    )
                    if isinstance(response, dict):
                        text = response.get('response') or response.get('text') or ''
                    else:
                        text = str(response)
                
                # Parse the response for True/False — strict word-boundary matching
                text = (text or '').strip()
                # Show response on terminal for user visibility
                log_block(
                    logger, logging.INFO,
                    f"LLM Conditional Raw Response ({len(text)} chars)", text,
                )
                
                import re as _re
                text_lower = text.lower()
                
                # Strip CoT / think tags (models may wrap reasoning in <｜end▁of▁thinking｜> response tags)
                text_clean = _re.sub(r'<[^>]*think[^>]*>.*?</[^>]*think[^>]*>', '', text_lower, flags=_re.DOTALL)
                text_clean = text_clean.strip()
                if not text_clean:
                    text_clean = text_lower
                
                # Find all standalone occurrences of "true" and "false" (whole words)
                true_matches = list(_re.finditer(r'\btrue\b', text_clean))
                false_matches = list(_re.finditer(r'\bfalse\b', text_clean))
                
                result_value = None
                
                if true_matches and not false_matches:
                    result_value = True
                elif false_matches and not true_matches:
                    result_value = False
                elif true_matches and false_matches:
                    # Both appear — pick the LAST one (models tend to reason then conclude)
                    last_true = true_matches[-1].start()
                    last_false = false_matches[-1].start()
                    result_value = last_true > last_false
                    logger.info(
                        f"LLM conditional: both true/false found, using last occurrence "
                        f"(true at {last_true}, false at {last_false} -> {result_value})"
                    )
                else:
                    # Neither standalone word found — try first word
                    first_word = text_clean.split()[0] if text_clean.split() else ''
                    first_word = first_word.strip('.,;:!?\'"()[]{}')
                    if first_word in ('true', 'yes', 'y', '1'):
                        result_value = True
                    elif first_word in ('false', 'no', 'n', '0'):
                        result_value = False
                    else:
                        # No clear signal — default False and log warning
                        logger.warning(
                            f"LLM conditional: could not parse True/False from response. "
                            f"First 200 chars: {text[:200]}"
                        )
                        result_value = False
                
                out['result'] = result_value
                            
            except Exception as e:
                out['error'] = str(e)
        
        t = threading.Thread(target=_runner, daemon=True)
        t.start()
        
        # Wait for the thread to finish — no timeout, only stop via ESC
        while t.is_alive():
            if stop_flag and stop_flag():
                logger.warning("LLM conditional: stopped by flag")
                _cleanup_vision_images()
                return False
            time.sleep(0.05)
        
        if out.get('error') is not None:
            logger.error(f"LLM conditional execution failed: {out['error']}")
            _cleanup_vision_images()
            return False
        
        result_bool = bool(out.get('result'))
        
        try:
            if node_id:
                self.llm_executor.set_variable(f"node_{node_id}_conditional_result", result_bool)
        except Exception:
            pass
        
        logger.info(f"LLM conditional result: {result_bool}")
        _cleanup_vision_images()
        return result_bool
    
    def _execute_conditional_loop(self, conditional_data, stop_flag):
        """
        Execute a conditional loop by reconstructing the loop configuration and calling the fallback handler.
        
        Args:
            conditional_data (dict): The conditional node data containing loop configuration
            stop_flag (callable): Stop flag function
            
        Returns:
            bool: True if loop completed successfully, False otherwise
        """
        try:
            import json
            from ....sequence_player import SequencePlayer
            expected_app = self._get_expected_app_context(conditional_data)
            if expected_app:
                try:
                    ok = self._verify_app_running(expected_app, timeout=float(expected_app.get("focus_timeout", 2.0)))
                except Exception:
                    ok = True
                if not ok:
                    logger.info("Conditional loop gated: expected app not running")
                    return False
            
            condition_type = str(conditional_data.get('condition_type') or conditional_data.get('trigger_type') or '').strip().lower()
            loop_type = (conditional_data.get('loop_type') or '').strip()
            if not loop_type and condition_type in ('while_present', 'until_present', 'while_absent', 'until_absent'):
                loop_type = condition_type
            if not loop_type:
                loop_type = 'while_present'

            # No iteration cap: the loop runs until the condition is no longer
            # met, then the workflow continues via the single output port.

            chain_file = conditional_data.get('chain_file', '') or conditional_data.get('chain', '')
            sequence_file = conditional_data.get('sequence_file', '') or conditional_data.get('sequence', '')
            try:
                iteration_delay = float(
                    conditional_data.get('iteration_delay')
                    if conditional_data.get('iteration_delay') is not None
                    else conditional_data.get('delay_between_attempts', 0.05)
                )
            except Exception:
                iteration_delay = 0.05
            
            # Parse the stored loop condition
            loop_condition_json = conditional_data.get('loop_condition', '{}')
            try:
                condition = json.loads(loop_condition_json) if loop_condition_json else {}
            except (json.JSONDecodeError, TypeError):
                # Fallback to basic image condition if JSON parsing fails
                condition = {
                    'trigger_type': 'presence',
                    'image_path': conditional_data.get('image_path', ''),
                    'image_data': conditional_data.get('image_data', ''),
                    'confidence': float(conditional_data.get('threshold', 0.8))
                }
            if not isinstance(condition, dict):
                condition = {}

            if not condition:
                ocr_text = conditional_data.get('ocr_text', '') or conditional_data.get('target_text', '')
                if str(ocr_text or '').strip():
                    try:
                        conf = float(conditional_data.get('threshold', conditional_data.get('confidence', 0.8)))
                    except Exception:
                        conf = 0.8
                    case_sensitive = conditional_data.get('case_sensitive', True)
                    if isinstance(case_sensitive, str):
                        case_sensitive = case_sensitive.strip().lower() in ('true', '1', 'yes', 'y')
                    elif case_sensitive is None:
                        case_sensitive = True
                    else:
                        case_sensitive = bool(case_sensitive)
                    condition = {
                        'trigger_type': 'ocr',
                        'target_text': ocr_text,
                        'confidence': conf,
                        'case_sensitive': case_sensitive,
                    }
                else:
                    try:
                        conf = float(conditional_data.get('threshold', conditional_data.get('confidence', 0.8)))
                    except Exception:
                        conf = 0.8
                    condition = {
                        'trigger_type': 'presence',
                        'image_path': conditional_data.get('image_path', ''),
                        'image_data': conditional_data.get('image_data', ''),
                        'confidence': conf
                    }
            
            # Build loop configuration (no iteration cap — the loop ends only
            # when the condition is no longer met)
            loop_config = {
                'type': loop_type,
                'sequence_file': sequence_file,
                'chain_file': chain_file,
                'iteration_delay': iteration_delay,
                'condition': condition
            }
            
            logger.info(f"Executing conditional loop: {loop_type}, sequence: {sequence_file}, chain: {chain_file}")
            
            # Set stop flag for the fallback handler
            self.fallback_handler.stop_flag = stop_flag
            
            # Execute the conditional loop using the fallback handler
            # Create a temporary SequencePlayer for loop execution with the sequence file
            sequence_file = loop_config.get('sequence_file', '')
            if not sequence_file and not chain_file:
                logger.error(f"No sequence or chain file specified for conditional loop")
                return False
            
            temp_sequence_player = SequencePlayer(sequence_file)
            try:
                gctx = getattr(self, "global_app_context_override", None)
                if isinstance(gctx, dict):
                    aurl = gctx.get("sandbox_agent_url") or gctx.get("rdp_agent_url")
                    if aurl:
                        temp_sequence_player.sandbox_agent_url = aurl
                        if hasattr(temp_sequence_player, "action_handlers") and temp_sequence_player.action_handlers:
                            temp_sequence_player.action_handlers.bot.sandbox_agent_url = aurl
                        # Keep the computer-vision channel in sync so the loop
                        # condition and its body observe the same desktop.
                        from ....computer_vision import set_sandbox_agent_url
                        set_sandbox_agent_url(aurl)
            except Exception:
                pass
            # Share selection memory with temporary sequence player
            try:
                temp_sequence_player.selection_memory = self.selection_memory if self.selection_memory is not None else {}
            except Exception:
                pass
            return self.fallback_handler.execute_conditional_loop(loop_config, temp_sequence_player)
            
        except Exception as e:
            logger.error(f"Error executing conditional loop: {e}")
            return False
    
    def _get_conditional_branch_node(self, node, result):
        """
        Get the next node ID based on conditional result.
        
        Args:
            node (dict): The conditional node
            result (bool): The conditional evaluation result
            
        Returns:
            str: Next node ID, or None if no connection found
        """
        # Convert the result to a strict boolean first
        result = bool(result)
        
        # File-loop conditionals run an internal sequence/chain file until the
        # condition stops holding, then continue the workflow — they expose a
        # single 'output' port instead of true/false branches.
        data = node.get('data', {}) or {}
        loop_type = str(data.get('loop_type', '') or '').strip().lower()
        seq_file = str(data.get('sequence_file', '') or data.get('sequence', '') or '').strip()
        chain_file = str(data.get('chain_file', '') or data.get('chain', '') or '').strip()
        if str(node.get('type')) == 'orchestrator':
            # Fulfilment branch: an achieved goal continues via 'output'; an
            # unfulfilled run hands the goal to the next region via 'route'.
            branch = 'output' if result else 'route'
        elif loop_type in ('while_present', 'while_absent', 'until_present', 'until_absent') and (seq_file or chain_file):
            branch = 'output'
        else:
            # Determine which branch to follow based on result
            branch = 'true' if result else 'false'
        node_id = str(node.get('id') or node.get('node_id') or '')
        logger.info(f"Conditional result evaluated to: {result}, looking for branch: '{branch}'")
        
        # Get connections for this branch
        connections = node.get('connections', {})
        conn_type = type(connections).__name__
        if isinstance(connections, list):
            # Format from older saves or list-based connections
            branch_connections = [c for c in connections if str(c.get('output_port')).lower() == branch or str(c.get('output_port')).lower() == str(result).lower()]
        else:
            if isinstance(connections, str):
                import json
                try:
                    connections = json.loads(connections)
                except Exception:
                    connections = {}
            # Fallback check for flat array format sometimes saved by NGUI
            if isinstance(connections, list):
                 branch_connections = [c for c in connections if str(c.get('output_port')).lower() == branch or str(c.get('output_port')).lower() == str(result).lower()]
            else:
                 branch_connections = connections.get(branch, [])
                 if not branch_connections:
                     branch_connections = connections.get(str(result).lower(), [])
                 if not branch_connections:
                     branch_connections = connections.get(str(result).capitalize(), [])
                 # Additional check if connections are flat inside dict
                 if not branch_connections:
                     for c in connections:
                         if isinstance(c, dict) and (str(c.get('output_port')).lower() == branch or str(c.get('output_port')).lower() == str(result).lower()):
                             branch_connections.append(c)
            
        target_node = None
        if branch_connections:
            # Take the first connection in the branch
            conn = branch_connections[0]
            target_node = conn.get('target_node_id') or conn.get('node_id')
        
        if target_node:
            logger.info(f"[COND-BRANCH] Node {node_id}: following '{branch}' branch to node: {target_node} (conn_type={conn_type})")
            return target_node
        else:
            logger.info(f"[COND-BRANCH] Node {node_id}: No connection for '{branch}' branch (conn_type={conn_type}, conns={str(connections)[:200]})")
            return None

    def _mark_skipped(self, conditional_id, chosen_port, unchosen_port, completed_nodes=None):
        """
        Mark nodes in the unchosen branch as skipped.
        
        Args:
            conditional_id (str): The conditional node ID
            chosen_port (str): The chosen branch ('true' or 'false')
            unchosen_port (str): The unchosen branch ('true' or 'false')
            completed_nodes (set): Nodes already completed (optional)
        """
        import json
        from collections import deque

        if completed_nodes is None:
            completed_nodes = set()

        conditional_data = self.workflow_graph.get(conditional_id, {})
        connections = conditional_data.get('connections', {})
        if isinstance(connections, str):
            try:
                connections = json.loads(connections)
            except Exception:
                connections = {}

        def _starts_for(port):
            if isinstance(connections, dict):
                branch_conns = connections.get(port, []) or []
                return [c.get('node_id') or c.get('target_node_id') for c in branch_conns]
            if isinstance(connections, list):
                return [c.get('target_node_id') or c.get('node_id') for c in connections if str(c.get('output_port')).lower() == str(port).lower()]
            return []

        chosen_starts = [s for s in _starts_for(chosen_port) if s]
        unchosen_starts = [s for s in _starts_for(unchosen_port) if s]

        def _reachable(starts):
            seen = set()
            q = deque(starts)
            while q:
                nid = q.popleft()
                if not nid or nid in seen:
                    continue
                seen.add(nid)
                nd = self.workflow_graph.get(nid, {})
                conns = nd.get('connections', {}) or {}

                # Execution ports ONLY.  Data ports (ctx_out, tools, query,
                # ctx_in, context-to-non-context) carry data, never branch
                # membership.  'context' is followed only toward context-type
                # targets (mirrors _get_output_connected_nodes) so a Context
                # node fed via the LLM 'context' port is still reachable.
                EXEC_PORTS = ('output', 'true', 'false', 'error', 'route')

                # Check for dict-style connections (e.g. {'output': [{'node_id': '...'}]})
                if isinstance(conns, dict):
                    for port_name, lst in conns.items():
                        if not isinstance(lst, list):
                            continue
                        if port_name not in EXEC_PORTS:
                            if port_name != 'context':
                                continue
                        for c in lst:
                            tid = c.get('node_id') or c.get('target_node_id')
                            if not tid or tid in seen:
                                continue
                            if port_name == 'context' and self.workflow_graph.get(tid, {}).get('type') != 'context':
                                continue
                            q.append(tid)

                # Check for list-style connections (e.g. [{'output_port': 'output', 'target_node_id': '...'}] from new JSON format)
                elif isinstance(conns, list):
                    for c in conns:
                        op = str(c.get('output_port') or 'output').lower()
                        if op not in EXEC_PORTS:
                            if op != 'context':
                                continue
                        tid = c.get('target_node_id') or c.get('node_id')
                        if not tid or tid in seen:
                            continue
                        if op == 'context' and self.workflow_graph.get(tid, {}).get('type') != 'context':
                            continue
                        q.append(tid)

            return seen

        chosen_reach = _reachable(chosen_starts)
        unchosen_reach = _reachable(unchosen_starts)

        # ── Branch-gated computation ──
        # A node is branch-gated by THIS conditional when ALL of its exec input
        # sources are (transitively) gated by it.  Nodes whose exec input comes
        # from a non-gated node (a loop-shared router LLM, an Input node, a
        # merge point) execute regardless of the branch decision and must never
        # be swept.  This protects the BASE_SYSTEM_CHAIN Context node, whose
        # exec input is the router LLM (sibling of the conditional) while its
        # ctx_out data feeds the chosen branch.
        gated = {conditional_id}
        _changed = True
        while _changed:
            _changed = False
            for _nid, _nd in self.workflow_graph.items():
                if _nid in gated:
                    continue
                _inputs = _nd.get('inputs') or []
                _exec_sources = [
                    _inp.get('from_node') for _inp in _inputs
                    if str(_inp.get('input_port', 'input')) in ('input', '', None)
                ]
                if _exec_sources and all(_s in gated for _s in _exec_sources):
                    gated.add(_nid)
                    _changed = True

        def _may_sweep(nid):
            """True only for genuinely branch-exclusive nodes: a graph node
            (never a context node) whose every exec input source is gated by
            this conditional.  Nodes without exec inputs (starting/terminal
            nodes) or with shared exec sources are protected."""
            nd = self.workflow_graph.get(nid, {})
            if nd.get('type') == 'context':
                return False
            _inputs = nd.get('inputs') or []
            _exec_sources = [
                _inp.get('from_node') for _inp in _inputs
                if str(_inp.get('input_port', 'input')) in ('input', '', None)
            ]
            if not _exec_sources:
                return False
            return all(_s in gated for _s in _exec_sources)

        exclusive = unchosen_reach.difference(chosen_reach)
        exclusive = {n for n in exclusive
                     if n not in completed_nodes and _may_sweep(n)}

        if exclusive:
            self.skipped_nodes.update(exclusive)
            logger.info(f"[MARK-SKIP] Cond {conditional_id}: marked {len(exclusive)} nodes as skipped from unchosen branch '{unchosen_port}': {list(exclusive)[:10]}")
        else:
            logger.info(f"[MARK-SKIP] Cond {conditional_id}: no exclusive nodes to skip (chosen_reach={len(chosen_reach)}, unchosen_reach={len(unchosen_reach)})")

        # ── a7: also skip unchosen-branch tool-provider nodes ──
        # Tool providers (chain_import/code/mcp subroutines) of the unchosen
        # branch — even with zero exec inputs — are dead once the other branch
        # is chosen.  Their exec inputs (if any) come exclusively from the
        # unchosen branch, so they would stay 'remaining' forever and stall
        # the workflow.  Skipping them is safe: inline tool selection by an
        # LLM (llm_ops) does not consult skipped_nodes, so a shared tool can
        # still execute when explicitly selected.
        _extra_skip = set()
        for _nid in unchosen_reach:
            if _nid in completed_nodes or _nid in exclusive or _nid in self.skipped_nodes:
                continue
            _nd = self.workflow_graph.get(_nid, {})
            if _nd.get('type') not in ('chain_import', 'code', 'mcp'):
                continue
            _inputs = _nd.get('inputs') or []
            _has_exec_input = any(
                str(inp.get('input_port', 'input')) in ('input', '', None)
                for inp in _inputs
            )
            if not _has_exec_input:
                _extra_skip.add(_nid)
                continue
            # Exec inputs must all originate inside the unchosen branch
            _all_from_unchosen = True
            for _inp in _inputs:
                if str(_inp.get('input_port', 'input')) not in ('input', '', None):
                    continue
                _src = _inp.get('from_node')
                if (_src not in unchosen_reach
                        and _src not in unchosen_starts):
                    _all_from_unchosen = False
                    break
            if _all_from_unchosen:
                _extra_skip.add(_nid)
        if _extra_skip:
            self.skipped_nodes.update(_extra_skip)
            logger.info(
                f"[MARK-SKIP] Cond {conditional_id}: also skipped "
                f"{len(_extra_skip)} unchosen-branch tool provider(s): "
                f"{list(_extra_skip)[:10]}"
            )
