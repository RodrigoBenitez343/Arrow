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

    def _evaluate_conditional(self, conditional_node, stop_flag):
        """
        Evaluate a conditional node to determine which branch to take.
        
        Args:
            conditional_node (dict): The conditional node configuration
            stop_flag (callable): Stop flag function
            
        Returns:
            bool: True if condition is met, False otherwise
        """
        raw_trigger_type = conditional_node.get('trigger_type') or conditional_node.get('condition_type', 'presence')
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
            
            # Reconstruct loop configuration from conditional node data
            loop_type = conditional_data.get('loop_type', 'while_present')
            max_iterations = int(conditional_data.get('max_loops', 10))
            sequence_file = conditional_data.get('sequence_file', '')
            iteration_delay = float(conditional_data.get('iteration_delay', 0.05))  # Reduced from 0.3
            
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
            return target_node
        else:
            logger.info(f"No connection for '{branch}' branch, ending workflow")
            return None

    def _mark_skipped(self, conditional_id, unchosen_port):
        """
        Mark nodes in the unchosen branch as skipped.
        Correctly handles merge points (nodes with inputs from multiple branches).
        
        Args:
            conditional_id (str): The conditional node ID
            unchosen_port (str): The unchosen branch ('true' or 'false')
        """
        import json
        from collections import deque
        
        new_skipped = set()
        conditional_data = self.workflow_graph[conditional_id]
        connections = conditional_data.get('connections', {})
        if isinstance(connections, str):
            connections = json.loads(connections)
        
        # Handle the connections structure - it's organized by branch (true/false)
        unchosen_starts = []
        if isinstance(connections, dict) and unchosen_port in connections:
            unchosen_starts = [conn['node_id'] for conn in connections[unchosen_port]]
        
        queue = deque(unchosen_starts)
        
        # We process nodes and their descendants. A node is only marked as skipped
        # if ALL paths leading to it are from skipped nodes or the unchosen branch.
        while queue:
            current = queue.popleft()
            if current in new_skipped:
                continue
                
            current_data = self.workflow_graph.get(current, {})
            inputs = current_data.get('inputs', [])
            
            all_inputs_skippable = True
            for inp in inputs:
                from_id = inp['from_node']
                from_port = inp.get('output_type', inp.get('output_port'))
                
                # If the input is from the SAME conditional that triggered this skip:
                if from_id == conditional_id:
                    if from_port == unchosen_port:
                        # This path is the one we are unchoosing
                        continue
                    else:
                        # This path is the ACTIVE path of the same conditional.
                        # This node is a merge point for the true/false branches of this conditional.
                        # It should NOT be skipped via this call.
                        all_inputs_skippable = False
                        break
                # If the input is already skipped (globally or in this traversal)
                elif from_id in new_skipped or from_id in self.skipped_nodes:
                    continue
                else:
                    # This input is from a node that is NOT skipped.
                    # It could be from the active branch or some other part of the graph.
                    all_inputs_skippable = False
                    break
            
            if all_inputs_skippable:
                new_skipped.add(current)
                # Add connected nodes to queue for re-evaluation
                current_connections = current_data.get('connections', {})
                if isinstance(current_connections, dict):
                    for output_type, output_list in current_connections.items():
                        if isinstance(output_list, list):
                            for conn in output_list:
                                queue.append(conn['node_id'])
        
        if new_skipped:
            self.skipped_nodes.update(new_skipped)
            logger.info(f"Marked {len(new_skipped)} nodes as skipped from unchosen branch {unchosen_port} of {conditional_id}: {new_skipped}")
