# node_executor.py
"""
Node executor module for conditional fallback handler.
Handles conditional node execution and workflow branching logic.
"""
import json
import os
from ...config import logger


class NodeExecutor:
    """Handles conditional node execution and workflow branching"""

    def _get_expected_app_context(self, conditional_node):
        try:
            if isinstance(conditional_node, dict):
                ctx = conditional_node.get("app_context") or conditional_node.get("focus_app") or conditional_node.get("app")
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
    
    def __init__(self, template_matcher, validation_cache, path_resolver, presence_trigger, absence_trigger, ocr_trigger, conditional_loop, stop_flag=None):
        """
        Initialize node executor.
        
        Args:
            template_matcher: TemplateMatching instance
            validation_cache: ValidationCache instance
            path_resolver: PathResolver instance
            presence_trigger: PresenceTrigger instance
            absence_trigger: AbsenceTrigger instance
            ocr_trigger: OCRTrigger instance
            conditional_loop: ConditionalLoop instance
            stop_flag: Function to check if execution should stop
        """
        self.template_matcher = template_matcher
        self.validation_cache = validation_cache
        self.path_resolver = path_resolver
        self.presence_trigger = presence_trigger
        self.absence_trigger = absence_trigger
        self.ocr_trigger = ocr_trigger
        self.conditional_loop = conditional_loop
        self.stop_flag = stop_flag
    
    def execute_conditional_node(self, node, stop_flag=None):
        """
        Executes a conditional node and returns the next node ID based on the result.
        
        Args:
            node (dict): The conditional node from the workflow graph
            stop_flag (callable): Function to check if execution should stop
            
        Returns:
            str: Next node ID to execute, or None if workflow should end
        """
        if stop_flag:
            self.stop_flag = stop_flag
            
        conditional_data = node['data']
        logger.info(f"Executing conditional node: {conditional_data.get('name', conditional_data['node_id'])}")
        
        # Check if this is a loop condition
        condition_type = conditional_data.get('condition_type', 'presence')
        if condition_type == 'loop':
            result = self.conditional_loop.execute_conditional_loop_from_node(conditional_data, stop_flag)
        else:
            result = self.evaluate_conditional_from_node(conditional_data, stop_flag)
        
        return self.get_conditional_branch_node(node, result)
    
    def evaluate_conditional_from_node(self, conditional_node, stop_flag=None):
        """
        Evaluate a conditional node using the appropriate trigger handler.
        
        Args:
            conditional_node (dict): The conditional node configuration
            stop_flag (callable): Function to check if execution should stop
        
        Returns:
            bool: True if condition is met, False otherwise
        """
        if stop_flag:
            self.stop_flag = stop_flag
            
        # Fix: The JSON stores 'trigger_type' with values like 'absence', 'presence', 'ocr'
        # We need to map these to the expected condition types for evaluation
        raw_trigger_type = conditional_node.get('trigger_type') or conditional_node.get('condition_type', 'presence')
        
        # Map the raw trigger type to the expected condition type format
        condition_type_mapping = {
            'presence': 'presence_trigger',
            'absence': 'absence_trigger', 
            'ocr': 'ocr_trigger',
            'presence_trigger': 'presence_trigger',  # backward compatibility
            'absence_trigger': 'absence_trigger',    # backward compatibility
            'ocr_trigger': 'ocr_trigger'             # backward compatibility
        }
        
        condition_type = condition_type_mapping.get(raw_trigger_type, 'presence_trigger')
        
        # Build condition_config from the conditional node properties
        # The JSON stores properties directly, not nested under 'condition_data'
        condition_config = {
            'image_path': conditional_node.get('image_path', ''),
            'confidence': float(conditional_node.get('threshold', 0.8)),
            'timeout': float(conditional_node.get('wait_time', 10)),
            'max_loops': int(conditional_node.get('max_loops', 10)),
            'target_text': conditional_node.get('ocr_text', '') or conditional_node.get('target_text', ''),
            'case_sensitive': conditional_node.get('case_sensitive', True),
            'language': conditional_node.get('language', 'eng'),
            'region': conditional_node.get('region')
        }
        
        # Also check if there's a nested condition_data (for backward compatibility)
        if 'condition_data' in conditional_node:
            nested_config = conditional_node['condition_data']
            condition_config.update(nested_config)
        
        logger.info(f"Evaluating conditional with raw trigger type: {raw_trigger_type} -> mapped to: {condition_type}")
        logger.debug(f"Condition config: {condition_config}")

        expected_app = self._get_expected_app_context(conditional_node)
        if expected_app:
            try:
                ok = self._ensure_app_open_and_focused(expected_app, stop_flag, timeout=float(expected_app.get("focus_timeout", 2.0)))
            except Exception:
                ok = True
            if not ok:
                logger.info("Conditional gated: expected app not open/focused")
                return False
        
        if condition_type == 'presence_trigger':
            timeout = float(condition_config.get('timeout', 10))
            return self.presence_trigger.check_presence_with_timeout(condition_config, timeout=timeout)
        elif condition_type == 'absence_trigger':
            timeout = float(condition_config.get('timeout', 10))
            return self.absence_trigger.check_absence_trigger(condition_config, timeout)
        elif condition_type == 'ocr_trigger':
            return self.ocr_trigger.check_ocr_trigger(condition_config, stop_flag=stop_flag)
        else:
            logger.warning(f"Unknown condition type: {condition_type} (raw: {raw_trigger_type})")
            return False
    
    def get_conditional_branch_node(self, node, result):
        """
        Get the next node ID based on conditional result.
        
        Args:
            node (dict): The conditional node
            result (bool): The conditional evaluation result
            
        Returns:
            str: Next node ID, or None if no connection found
        """
        # Determine which branch to follow based on result
        branch = 'true' if result else 'false'
        
        # Get connections for this branch
        connections = node.get('connections', {})
        branch_connections = connections.get(branch, [])
        
        target_node = None
        if branch_connections:
            # Take the first connection in the branch
            target_node = branch_connections[0].get('node_id')
        
        if target_node:
            logger.info(f"Following '{branch}' branch to node: {target_node}")
            return target_node
        else:
            logger.info(f"No connection for '{branch}' branch, ending workflow")
            return None
    
    def handle_until_present_condition(self, conditional_node, stop_flag=None):
        """
        Handle until present condition for backward compatibility.
        
        Args:
            conditional_node (dict): The conditional node configuration
            stop_flag (callable): Function to check if execution should stop
            
        Returns:
            bool: True if condition is met, False otherwise
        """
        if stop_flag:
            self.stop_flag = stop_flag
            
        logger.info("Handling until_present condition (backward compatibility)")
        
        # Convert to standard presence trigger format
        condition_config = {
            'image_path': conditional_node.get('image_path', ''),
            'confidence': float(conditional_node.get('threshold', 0.8)),
            'timeout': float(conditional_node.get('wait_time', 10))
        }
        
        # Use presence trigger with timeout
        return self.presence_trigger.check_presence_with_timeout(
            condition_config, 
            timeout=condition_config['timeout']
        )
    
    def handle_until_absent_condition(self, conditional_node, stop_flag=None):
        """
        Handle until absent condition for backward compatibility.
        
        Args:
            conditional_node (dict): The conditional node configuration
            stop_flag (callable): Function to check if execution should stop
            
        Returns:
            bool: True if condition is met, False otherwise
        """
        if stop_flag:
            self.stop_flag = stop_flag
            
        logger.info("Handling until_absent condition (backward compatibility)")
        
        # Convert to standard absence trigger format
        condition_config = {
            'image_path': conditional_node.get('image_path', ''),
            'confidence': float(conditional_node.get('threshold', 0.8)),
            'timeout': float(conditional_node.get('wait_time', 10))
        }
        
        # Use absence trigger with timeout
        return self.absence_trigger.check_absence_trigger(
            condition_config, 
            timeout=condition_config['timeout']
        )
    
    def validate_conditional_node(self, node):
        """
        Validate a conditional node for completeness and correctness.
        
        Args:
            node (dict): The conditional node to validate
            
        Returns:
            tuple: (is_valid, error_message)
        """
        try:
            if 'data' not in node:
                return False, "Node missing data section"
            
            data = node['data']
            
            # Check for required fields
            if 'node_id' not in data:
                return False, "Node missing node_id"
            
            # Check trigger type
            trigger_type = data.get('trigger_type') or data.get('condition_type')
            if not trigger_type:
                return False, "Node missing trigger_type or condition_type"
            
            valid_triggers = ['presence', 'absence', 'ocr', 'loop']
            if trigger_type not in valid_triggers:
                return False, f"Invalid trigger type: {trigger_type}. Must be one of: {valid_triggers}"
            
            # Validate based on trigger type
            if trigger_type in ['presence', 'absence']:
                image_path = data.get('image_path')
                if not image_path:
                    return False, f"Image path required for {trigger_type} trigger"
                # Only ensure a path is provided; screen presence is validated at runtime via OpenCV
                resolved_path = self.path_resolver.resolve_path(image_path, add_json=False)
                if not resolved_path:
                    return False, f"Invalid image path: {image_path}"
            
            elif trigger_type == 'ocr':
                target_text = data.get('ocr_text') or data.get('target_text')
                if not target_text:
                    return False, "Target text required for OCR trigger"
            
            elif trigger_type == 'loop':
                sequence_file = data.get('sequence_file')
                if not sequence_file:
                    return False, "Sequence file required for loop trigger"
                
                # Check if sequence file exists
                resolved_file = self.path_resolver.resolve_path(sequence_file, add_json=True)
                if not resolved_file:
                    return False, f"Sequence file not found: {sequence_file}"
            
            # Check connections
            connections = node.get('connections', {})
            if 'true' not in connections and 'false' not in connections:
                return False, "Node missing both true and false branch connections"
            
            return True, "Node is valid"
            
        except Exception as e:
            return False, f"Error validating node: {e}"
