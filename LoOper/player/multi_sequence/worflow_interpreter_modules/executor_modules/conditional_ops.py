from ....config import logger
import os

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
        import time
        try:
            import pyautogui
        except Exception:
            pyautogui = None
        try:
            from pynput import keyboard
        except Exception:
            keyboard = None

        image_path = conditional_node.get('image_path', '') or ''
        if not str(image_path).strip():
            return False

        try:
            confidence = float(conditional_node.get('threshold', conditional_node.get('confidence', 0.8)))
        except Exception:
            confidence = 0.8

        try:
            timeout_s = float(conditional_node.get('timeout', 10) or 10)
        except Exception:
            timeout_s = 10.0
        if timeout_s <= 0:
            timeout_s = 10.0

        try:
            max_attempts = int(conditional_node.get('max_attempts', 30) or 30)
        except Exception:
            max_attempts = 30
        max_attempts = max(1, max_attempts)

        try:
            attempt_delay = float(conditional_node.get('delay_between_attempts', 0.05) or 0.05)
        except Exception:
            attempt_delay = 0.05

        try:
            scroll_amount = int(conditional_node.get('scroll_amount', 400) or 400)
        except Exception:
            scroll_amount = 400
        scroll_amount = max(1, scroll_amount)

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

        expected_x = conditional_node.get('target_x')
        expected_y = conditional_node.get('target_y')
        expected_w = conditional_node.get('target_w')
        expected_h = conditional_node.get('target_h')
        use_expected_position = False
        try:
            expected_w_i = int(float(expected_w))
            expected_h_i = int(float(expected_h))
            use_expected_position = expected_w_i > 0 and expected_h_i > 0
        except Exception:
            use_expected_position = False
        if use_expected_position:
            try:
                expected_x_i = int(float(expected_x))
                expected_y_i = int(float(expected_y))
            except Exception:
                use_expected_position = False
                expected_x_i = 0
                expected_y_i = 0
        else:
            expected_x_i = 0
            expected_y_i = 0

        try:
            position_tolerance = int(conditional_node.get('position_tolerance', conditional_node.get('tolerance', 6)) or 6)
        except Exception:
            position_tolerance = 6
        position_tolerance = max(0, position_tolerance)
        try:
            region_expand_factor = float(conditional_node.get('region_expand_factor', 2.0) or 2.0)
        except Exception:
            region_expand_factor = 2.0
        region_expand_factor = max(1.0, region_expand_factor)

        def _check_match(screen_cv=None):
            if stop_flag and stop_flag():
                return False
            try:
                if use_expected_position and hasattr(self.fallback_handler.template_matcher, 'region_based_search'):
                    loc = self.fallback_handler.template_matcher.region_based_search(
                        resolved_image_path,
                        expected_x_i,
                        expected_y_i,
                        confidence,
                        region_expand_factor,
                        True,
                        screen_cv
                    )
                    if not loc:
                        loc = self.fallback_handler.template_matcher.region_based_search(
                            resolved_image_path,
                            expected_x_i,
                            expected_y_i,
                            confidence,
                            region_expand_factor,
                            False,
                            screen_cv
                        )
                else:
                    loc = None
                if not loc:
                    loc = self.fallback_handler.template_matcher.multiscale_template_match(
                        resolved_image_path,
                        confidence=confidence,
                        scales=[1.0],
                        grayscale=True,
                        screen_cv=screen_cv
                    )
                    if not loc:
                        loc = self.fallback_handler.template_matcher.multiscale_template_match(
                            resolved_image_path,
                            confidence=confidence,
                            scales=[1.0],
                            grayscale=False,
                            screen_cv=screen_cv
                        )
            except Exception:
                loc = None
            if not loc:
                return False
            if not use_expected_position:
                return True
            try:
                dx = abs(int(loc[0]) - expected_x_i)
                dy = abs(int(loc[1]) - expected_y_i)
                return dx <= position_tolerance and dy <= position_tolerance
            except Exception:
                return False

        def _do_scroll(amount):
            ah = getattr(self, 'action_handlers', None)
            if ah is not None and getattr(getattr(ah, 'bot', None), 'sandbox_agent_url', None):
                try:
                    ah._send_agent_action({"action": "scroll", "amount": int(amount)})
                    return
                except Exception:
                    return
            if pyautogui is None:
                return
            try:
                pyautogui.scroll(int(amount))
            except Exception:
                return

        def _do_hscroll(amount):
            ah = getattr(self, 'action_handlers', None)
            if ah is not None and getattr(getattr(ah, 'bot', None), 'sandbox_agent_url', None):
                try:
                    reps = max(1, abs(int(amount)) // 100)
                except Exception:
                    reps = 1
                key = 'right' if amount > 0 else 'left'
                for _ in range(reps):
                    if stop_flag and stop_flag():
                        return
                    try:
                        ah._send_agent_action({"action": "hotkey", "keys": [key]})
                    except Exception:
                        return
                return
            if pyautogui is None:
                return
            try:
                if hasattr(pyautogui, 'hscroll'):
                    pyautogui.hscroll(int(amount))
                    return
            except Exception:
                pass
            try:
                pyautogui.keyDown('shift')
                pyautogui.scroll(int(amount))
                pyautogui.keyUp('shift')
            except Exception:
                try:
                    pyautogui.keyUp('shift')
                except Exception:
                    pass

        def _do_zoom(zoom_in):
            ah = getattr(self, 'action_handlers', None)
            if ah is not None:
                if getattr(getattr(ah, 'bot', None), 'sandbox_agent_url', None):
                    try:
                        ah._send_agent_action({"action": "hotkey", "keys": ["ctrl", "=" if zoom_in else "-"]})
                        return
                    except Exception:
                        try:
                            ah._send_agent_action({"action": "hotkey", "keys": ["ctrl", "+" if zoom_in else "-"]})
                            return
                        except Exception:
                            return
                if keyboard is not None:
                    try:
                        ah._press_key_combo([keyboard.Key.ctrl], '=' if zoom_in else '-')
                        return
                    except Exception:
                        pass
            if pyautogui is None:
                return
            try:
                pyautogui.hotkey('ctrl', '=' if zoom_in else '-')
            except Exception:
                try:
                    pyautogui.hotkey('ctrl', '+' if zoom_in else '-')
                except Exception:
                    return

        deadline = time.time() + timeout_s

        try:
            screen_cv0 = self.fallback_handler.template_matcher.capture_screen()
        except Exception:
            screen_cv0 = None
        if _check_match(screen_cv0):
            return True
        try:
            import random
        except Exception:
            random = None

        try:
            zoom_prob = float(conditional_node.get('zoom_prob', 0.12) or 0.12)
        except Exception:
            zoom_prob = 0.12
        zoom_prob = max(0.0, min(1.0, zoom_prob))

        try:
            hscroll_prob = float(conditional_node.get('hscroll_prob', 0.10) or 0.10)
        except Exception:
            hscroll_prob = 0.10
        hscroll_prob = max(0.0, min(1.0, hscroll_prob))

        try:
            flip_prob = float(conditional_node.get('scroll_flip_prob', 0.06) or 0.06)
        except Exception:
            flip_prob = 0.06
        flip_prob = max(0.0, min(1.0, flip_prob))

        try:
            hold_steps = int(conditional_node.get('direction_hold_steps', 12) or 12)
        except Exception:
            hold_steps = 12
        hold_steps = max(1, hold_steps)

        try:
            scroll_step_min = int(conditional_node.get('scroll_step_min', 30) or 30)
        except Exception:
            scroll_step_min = 30
        scroll_step_min = max(1, scroll_step_min)
        try:
            scroll_step_max = int(conditional_node.get('scroll_step_max', scroll_amount) or scroll_amount)
        except Exception:
            scroll_step_max = scroll_amount
        scroll_step_max = max(scroll_step_min, scroll_step_max)

        try:
            settle_min = float(conditional_node.get('settle_min', attempt_delay) or attempt_delay)
        except Exception:
            settle_min = attempt_delay
        settle_min = max(0.01, settle_min)
        try:
            settle_max = float(conditional_node.get('settle_max', max(settle_min, 0.18)) or max(settle_min, 0.18))
        except Exception:
            settle_max = max(settle_min, 0.18)
        settle_max = max(settle_min, settle_max)

        if attempt_delay <= 0:
            attempt_delay = 0.02

        try:
            min_steps_from_timeout = int((timeout_s / max(0.02, attempt_delay)) * 3)
        except Exception:
            min_steps_from_timeout = max_attempts
        max_attempts = max(max_attempts, min_steps_from_timeout)

        vdir = 1
        hdir = 1
        if random is not None:
            try:
                vdir = 1 if random.random() < 0.5 else -1
                hdir = 1 if random.random() < 0.5 else -1
            except Exception:
                vdir = 1
                hdir = 1
        v_hold = 0
        h_hold = 0

        step = 0
        while step < max_attempts and time.time() <= deadline:
            if stop_flag and stop_flag():
                return False

            do_zoom = False
            if random is None:
                do_zoom = (step % 9 == 0)
            else:
                try:
                    do_zoom = random.random() < zoom_prob
                except Exception:
                    do_zoom = False

            if do_zoom:
                zoom_in = True
                if random is not None:
                    try:
                        zoom_in = random.random() < 0.5
                    except Exception:
                        zoom_in = True
                _do_zoom(bool(zoom_in))
            else:
                do_h = False
                if random is not None:
                    try:
                        do_h = random.random() < hscroll_prob
                    except Exception:
                        do_h = False

                amt = scroll_step_max
                if random is not None:
                    try:
                        amt = random.randint(scroll_step_min, scroll_step_max)
                    except Exception:
                        amt = scroll_step_max

                if do_h:
                    if h_hold >= hold_steps and random is not None:
                        try:
                            if random.random() < flip_prob:
                                hdir *= -1
                                h_hold = 0
                        except Exception:
                            pass
                    _do_hscroll(int(hdir * amt))
                    h_hold += 1
                else:
                    if v_hold >= hold_steps and random is not None:
                        try:
                            if random.random() < flip_prob:
                                vdir *= -1
                                v_hold = 0
                        except Exception:
                            pass
                    _do_scroll(int(vdir * amt))
                    v_hold += 1

            settle = settle_min
            if random is not None:
                try:
                    settle = random.uniform(settle_min, settle_max)
                except Exception:
                    settle = settle_min
            elapsed = 0.0
            while elapsed < settle:
                if stop_flag and stop_flag():
                    return False
                st = min(0.05, settle - elapsed)
                time.sleep(st)
                elapsed += st

            try:
                screen_cv = self.fallback_handler.template_matcher.capture_screen()
            except Exception:
                screen_cv = None
            if _check_match(screen_cv):
                return True

            step += 1

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
