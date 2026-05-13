from ....config import logger
import os
from ...llm_executor_resources.executor import _strip_think_tags

class ConditionalMixin:
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

    def _evaluate_conditional(self, conditional_node, stop_flag, node_inputs=None):
        """
        Evaluate a conditional node to determine which branch to take.
        
        Args:
            conditional_node (dict): The conditional node configuration
            stop_flag (callable): Stop flag function
            
        Returns:
            bool: True if condition is met, False otherwise
        """
        raw_trigger_type = conditional_node.get('trigger_type') or conditional_node.get('condition_type', 'presence')
        if str(raw_trigger_type).lower() in ('code', 'code_conditional', 'python'):
            return self._evaluate_code_condition(conditional_node, stop_flag, node_inputs=node_inputs)
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
                ok = self._ensure_app_open_and_focused(expected_app, stop_flag, timeout=float(expected_app.get("focus_timeout", 2.0)))
            except Exception:
                ok = True
            if not ok:
                logger.info("Conditional gated: expected app not open/focused")
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
                if output_type == 'context':
                    val = self.llm_executor.get_variable(f"node_{from_node_id}_context")
                else:
                    val = self.llm_executor.get_variable(f"node_{from_node_id}_output")
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
                    ok = self._ensure_app_open_and_focused(expected_app, stop_flag, timeout=float(expected_app.get("focus_timeout", 2.0)))
                except Exception:
                    ok = True
                if not ok:
                    logger.info("Conditional loop gated: expected app not open/focused")
                    return False
            
            condition_type = str(conditional_data.get('condition_type') or conditional_data.get('trigger_type') or '').strip().lower()
            loop_type = (conditional_data.get('loop_type') or '').strip()
            if not loop_type and condition_type in ('while_present', 'until_present', 'while_absent', 'until_absent'):
                loop_type = condition_type
            if not loop_type:
                loop_type = 'while_present'

            max_iterations_raw = (
                conditional_data.get('max_loops')
                if conditional_data.get('max_loops') is not None
                else conditional_data.get('max_iterations')
            )
            if max_iterations_raw is None:
                max_iterations_raw = conditional_data.get('max_attempts')
            try:
                max_iterations = int(max_iterations_raw if max_iterations_raw is not None else 10)
            except Exception:
                max_iterations = 10
            max_iterations = max(1, max_iterations)

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
            
            # Build loop configuration
            loop_config = {
                'type': loop_type,
                'max_iterations': max_iterations,
                'sequence_file': sequence_file,
                'iteration_delay': iteration_delay,
                'condition': condition
            }
            
            logger.info(f"Executing conditional loop: {loop_type}, sequence: {sequence_file}, max_iterations: {max_iterations}")
            
            # Set stop flag for the fallback handler
            self.fallback_handler.stop_flag = stop_flag
            
            # Execute the conditional loop using the fallback handler
            # Create a temporary SequencePlayer for loop execution with the sequence file
            sequence_file = loop_config.get('sequence_file', '')
            if not sequence_file:
                logger.error(f"No sequence file specified for conditional loop")
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
        
        # Determine which branch to follow based on result
        branch = 'true' if result else 'false'
        logger.info(f"Conditional result evaluated to: {result}, looking for branch: '{branch}'")
        
        # Get connections for this branch
        connections = node.get('connections', {})
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
            logger.info(f"Following '{branch}' branch to node: {target_node}")
            return target_node
        else:
            logger.info(f"No connection for '{branch}' branch, ending workflow path")
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
                
                # Check for dict-style connections (e.g. {'output': [{'node_id': '...'}]})
                if isinstance(conns, dict):
                    for _, lst in conns.items():
                        if not isinstance(lst, list):
                            continue
                        for c in lst:
                            tid = c.get('node_id') or c.get('target_node_id')
                            if tid and tid not in seen:
                                q.append(tid)
                
                # Check for list-style connections (e.g. [{'output_port': 'output', 'target_node_id': '...'}] from new JSON format)
                elif isinstance(conns, list):
                    for c in conns:
                        tid = c.get('target_node_id') or c.get('node_id')
                        if tid and tid not in seen:
                            q.append(tid)
                            
            return seen

        chosen_reach = _reachable(chosen_starts)
        unchosen_reach = _reachable(unchosen_starts)
        exclusive = unchosen_reach.difference(chosen_reach)
        exclusive = {n for n in exclusive if n not in completed_nodes}

        if exclusive:
            self.skipped_nodes.update(exclusive)
            logger.info(f"Marked {len(exclusive)} nodes as skipped from unchosen branch '{unchosen_port}' of {conditional_id}")
