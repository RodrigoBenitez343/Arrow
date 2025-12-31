from ....config import logger
import os
import sys
import venv
import time
import subprocess as _sp

class CodeMixin:
    def _execute_code_node(self, node, stop_flag):
        """
        Executes a Code node and returns the next node ID.
        
        Args:
            node (dict): The Code node from the workflow graph
            stop_flag (callable): Stop flag function
            
        Returns:
            str: Next node ID to execute, or None if workflow should end
        """
        # Lazy initialization: Only set up runtime environment when actually needed
        if not self._runtime_initialized:
            self._initialize_chain_runtime()
            self._runtime_initialized = True
        
        code_data = node.get('data', {})
        node_id = node.get('id') or node.get('node_id') or code_data.get('node_id') or code_data.get('id')
        if node_id:
            try:
                node['id'] = node_id
            except Exception:
                pass
        logger.info(f"Executing Code node: {node_id}")
        
        # Get properties
        code = code_data.get('code', '')
        file_path = code_data.get('file_path', '')
        output_variable = code_data.get('output_variable', 'result')
        
        # Resolve inputs
        input_data = []
        args = {}
        runtime_code = None
        code_src_llm_id = None
        
        inputs = node.get('inputs', [])
        for inp in inputs:
            from_node_id = inp.get('from_node')
            input_port = str(inp.get('input_port'))
            output_type = str(inp.get('output_type') or inp.get('output_port') or 'output')
            
            # Get value from previous node respecting source output port
            if output_type == 'context':
                val = self.llm_executor.get_variable(f"node_{from_node_id}_context")
            else:
                val = self.llm_executor.get_variable(f"node_{from_node_id}_output")
            
            if input_port == 'input':
                input_data.append(val)
            elif input_port == 'args':
                if isinstance(val, dict):
                    args.update(val)
            elif input_port == 'code':
                if val:
                    runtime_code = str(val)
                    code_src_llm_id = from_node_id
                    
                    # Strip markdown code blocks if present (LLM output often contains these)
                    if "```" in runtime_code:
                        try:
                            import re
                            # Find ALL code blocks, not just the first one
                            # This handles cases where LLM splits code into multiple blocks (e.g. imports, then functions, then main)
                            matches = re.findall(r"```(?:python)?\s*(.*?)```", runtime_code, re.DOTALL | re.IGNORECASE)
                            if matches:
                                # Join all blocks with newlines
                                runtime_code = "\n\n".join(m.strip() for m in matches)
                                logger.info(f"Extracted {len(matches)} code blocks from LLM output")
                            else:
                                # Fallback: Just strip first and last line if they look like fences
                                # This handles cases where regex might fail or format is slightly off
                                lines = runtime_code.strip().split('\n')
                                if lines and lines[0].strip().startswith("```"):
                                    lines = lines[1:]
                                if lines and lines[-1].strip().startswith("```"):
                                    lines = lines[:-1]
                                runtime_code = '\n'.join(lines)
                        except Exception as e:
                            logger.warning(f"Failed to strip markdown code blocks: {e}")
                    # Extract alias from code comments if present
                    try:
                        import re as _re
                        alias = None
                        for line in (runtime_code.splitlines() if runtime_code else []):
                            m = _re.match(r"\s*#\s*ALIAS\s*:\s*(.+)", line, _re.IGNORECASE)
                            if m:
                                alias = m.group(1).strip()
                                break
                            m2 = _re.match(r"\s*ALIAS\s*:\s*(.+)", line, _re.IGNORECASE)
                            if m2:
                                alias = m2.group(1).strip()
                                break
                        if alias:
                            node_alias = alias
                            try:
                                node_id = node.get('id') or node.get('node_id')
                                self.llm_executor.set_variable(f"node_{node_id}_module_alias", node_alias)
                            except Exception:
                                pass
                    except Exception:
                        pass

                    # If file_path is set, save the code to file
                    if file_path:
                        try:
                            # Resolve path again to be safe
                            save_path = resolved_path if 'resolved_path' in locals() and resolved_path else file_path
                
                            real_save_path = file_path
                            if hasattr(self.fallback_handler, 'path_resolver') and self.fallback_handler.path_resolver:
                                if hasattr(self.fallback_handler.path_resolver, 'resolve_code_path'):
                                    try:
                                        real_save_path = self.fallback_handler.path_resolver.resolve_code_path(file_path)
                                    except:
                                        pass

                            logger.info(f"Saving generated code to: {real_save_path}")
                            with open(real_save_path, 'w', encoding='utf-8') as f:
                                f.write(runtime_code)
                            
                        except Exception as e:
                            logger.error(f"Failed to save code to {file_path}: {e}")
                            # Continue with runtime execution even if save fails
        
        # Simplify input_data if single item
        if len(input_data) == 1:
            input_data = input_data[0]
        elif len(input_data) == 0:
            input_data = None

        # Determine code to run
        code_to_run = ""
        if runtime_code:
            code_to_run = runtime_code
        elif file_path:
            # Use path resolver if available
            resolved_path = None
            if hasattr(self.fallback_handler, 'path_resolver') and self.fallback_handler.path_resolver:
                # Use resolve_code_path if available, otherwise resolve_path
                if hasattr(self.fallback_handler.path_resolver, 'resolve_code_path'):
                    resolved_path = self.fallback_handler.path_resolver.resolve_code_path(file_path)
                else:
                    resolved_path = self.fallback_handler.path_resolver.resolve_path(file_path)
            
            # Fallback to direct check
            if not resolved_path and os.path.exists(file_path):
                resolved_path = file_path
                
            if resolved_path and os.path.exists(resolved_path):
                try:
                    logger.info(f"Reading code from file: {resolved_path}")
                    with open(resolved_path, 'r', encoding='utf-8') as f:
                        code_to_run = f.read()
                except Exception as e:
                    logger.error(f"Failed to read code file {resolved_path}: {e}")
                    self._set_code_node_error(node, str(e))
                    return self._get_code_next_node(node, 'error')
            else:
                 logger.warning(f"Code file not found: {file_path}")
                 # If code content is provided in node, use it as fallback?
                 # Current logic prefers file, but if file fails, should it use 'code'?
                 # The 'else' block below handles 'code' if runtime_code and file_path are NOT used.
                 # But here we are inside 'elif file_path'.
                 # Let's fallback to inline code if file not found but inline code exists
                 if code:
                     logger.info("Fallback to inline code")
                     code_to_run = code
                 else:
                     self._set_code_node_error(node, f"File not found: {file_path}")
                     return self._get_code_next_node(node, 'error')
        else:
            code_to_run = code

        if not code_to_run:
             logger.warning("No code to execute in Code node")
             return self._get_code_next_node(node, 'output')

        if not node_id:
            node_id = node.get('id') or node.get('node_id') or code_data.get('node_id') or code_data.get('id')
        try:
            base_dir = self._chain_runtime_dir or os.path.join(os.getcwd(), 'runtime')
            tmp_dir = os.path.join(base_dir, 'code_nodes', str(node_id))
            os.makedirs(tmp_dir, exist_ok=True)
            script_path = os.path.join(tmp_dir, 'script.py')
            with open(script_path, 'w', encoding='utf-8') as _f:
                _f.write(code_to_run)
            self.llm_executor.set_variable(f"node_{node_id}_code_path", script_path)
            alias_candidate = None
            desc_candidate = None
            try:
                if code_src_llm_id:
                    alias_candidate = self.llm_executor.get_variable(f"node_{code_src_llm_id}_artifact_alias")
                    desc_candidate = self.llm_executor.get_variable(f"node_{code_src_llm_id}_artifact_desc")
            except Exception:
                alias_candidate = None
                desc_candidate = None
            try:
                safe_alias = self._sanitize_alias(alias_candidate or f"module_{node_id}")
                if not safe_alias.lower().startswith("module_"):
                    safe_alias = f"module_{safe_alias}"
                self.llm_executor.set_variable(f"node_{node_id}_module_alias", safe_alias)
            except Exception:
                self.llm_executor.set_variable(f"node_{node_id}_module_alias", f"module_{node_id}")
            # Update a global index of available code modules
            try:
                idx = self.llm_executor.get_variable('code_modules') or []
                alias = self.llm_executor.get_variable(f"node_{node_id}_module_alias") or f"module_{node_id}"
                desc = desc_candidate or ""
                entry = {'id': str(node_id), 'alias': str(alias), 'path': str(script_path), 'desc': str(desc)}
                # de-duplicate by id
                if not any(e.get('id') == str(node_id) for e in (idx if isinstance(idx, list) else [])):
                    (idx if isinstance(idx, list) else []).append(entry)
                else:
                    # update existing
                    for e in (idx if isinstance(idx, list) else []):
                        if e.get('id') == str(node_id):
                            e.update(entry)
                self.llm_executor.set_variable('code_modules', idx)
                try:
                    # Update preferred framework for the chain based on this code
                    fw = self._detect_framework(code_to_run)
                    if fw:
                        prev = self.llm_executor.get_variable('chain_framework') or ''
                        if not prev:
                            self.llm_executor.set_variable('chain_framework', fw)
                        used = self.llm_executor.get_variable('chain_frameworks_used') or []
                        if isinstance(used, list):
                            if fw not in used:
                                used.append(fw)
                                self.llm_executor.set_variable('chain_frameworks_used', used)
                        else:
                            self.llm_executor.set_variable('chain_frameworks_used', [fw])
                except Exception:
                    pass
                try:
                    lines = []
                    for e in (idx if isinstance(idx, list) else []):
                        a = str(e.get('alias') or '')
                        d = str(e.get('desc') or '')
                        p = str(e.get('path') or '')
                        lines.append(f"- {a}: {d} [{p}]")
                    hdr = ""
                    try:
                        cf = self.llm_executor.get_variable('chain_framework') or ''
                        cu = self.llm_executor.get_variable('chain_frameworks_used') or []
                        if cf:
                            hdr += f"Preferred Framework: {cf}\n"
                        if cu:
                            if isinstance(cu, list) and cu:
                                hdr += "Frameworks seen: " + ", ".join([str(x) for x in cu]) + "\n"
                    except Exception:
                        pass
                    ctx = (hdr + "AVAILABLE MODULES:\n" + "\n".join(lines)).strip()
                    self.llm_executor.set_variable('chain_modules_context', ctx)
                except Exception:
                    pass
            except Exception:
                pass
        except Exception:
            pass

        try:
            self._ensure_chain_venv()
            if self._venv_site_packages and os.path.isdir(self._venv_site_packages):
                if self._venv_site_packages not in sys.path:
                    sys.path.insert(0, self._venv_site_packages)
            dep_report = self._install_code_dependencies(code_to_run)
            try:
                if node_id and dep_report:
                    self.llm_executor.set_variable(f"node_{node_id}_deps", dep_report)
            except Exception:
                pass
        except Exception:
            dep_report = None
        try:
            for inp in (inputs or []):
                src_id = inp.get('from_node')
                p = self.llm_executor.get_variable(f"node_{src_id}_code_path")
                if p:
                    d = os.path.dirname(str(p))
                    if d and os.path.isdir(d) and (d not in sys.path):
                        sys.path.insert(0, d)
        except Exception:
            pass

        # Execute (choose strategy: in-process or subprocess for persistent loops)
        import io
        import contextlib
        
        persistent = self._is_persistent_loop(code_to_run)
        timeout_s = 0
        try:
            timeout_s = int(code_data.get('timeout', 30) or 30)
        except Exception:
            timeout_s = 30

        if persistent:
            leave_running = False
            try:
                leave_running = bool(self._is_last_code_node(node))
            except Exception:
                leave_running = False
            ok, result, stdout_str, stderr_str, terminal_output, pid = self._run_persistent_script(script_path, timeout_s, os.path.dirname(script_path), leave_running=leave_running)
            if ok:
                if not node_id:
                    node_id = node.get('id') or node.get('node_id') or code_data.get('node_id') or code_data.get('id')
                self.llm_executor.set_variable(f"node_{node_id}_output", result)
                self.llm_executor.set_variable(f"node_{node_id}_result", result)
                self.llm_executor.set_variable(f"node_{node_id}_stdout", stdout_str)
                self.llm_executor.set_variable(f"node_{node_id}_stderr", stderr_str)
                self.llm_executor.set_variable(f"node_{node_id}_terminal_output", terminal_output)
                try:
                    if pid:
                        self.llm_executor.set_variable(f"node_{node_id}_pid", pid)
                except Exception:
                    pass
                if leave_running:
                    logger.info("Persistent loop script started and left running.")
                else:
                    logger.info("Persistent loop script executed and closed successfully.")
                try:
                    node['last_output_port'] = 'output'
                except Exception:
                    pass
                try:
                    code_text = ""
                    if os.path.exists(script_path):
                        with open(script_path, 'r', encoding='utf-8') as _f:
                            code_text = _f.read()
                    ctx_success = ("TERMINAL OUTPUT:\n" + terminal_output + "\n\n" + "CODE FILE: " + str(script_path) + "\n```python\n" + code_text + "\n```")
                    self.llm_executor.set_variable(f"node_{node_id}_context", ctx_success)
                except Exception:
                    pass
                return self._get_code_next_node(node, 'output')
            try:
                if not node_id:
                    node_id = node.get('id') or node.get('node_id') or code_data.get('node_id') or code_data.get('id')
                msg = terminal_output or stderr_str or "Persistent script failed to start"
                self._set_code_node_error(node, msg)
                self.llm_executor.set_variable(f"node_{node_id}_stdout", stdout_str)
                self.llm_executor.set_variable(f"node_{node_id}_stderr", stderr_str)
                self.llm_executor.set_variable(f"node_{node_id}_terminal_output", terminal_output)
                try:
                    node['last_output_port'] = 'error'
                except Exception:
                    pass
            except Exception:
                pass
            return self._get_code_next_node(node, 'error')

        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()

        try:
            # Prepare scope
            exec_scope = self.llm_executor.variables
            try:
                exec_scope['input_data'] = input_data
                exec_scope['args'] = args
                exec_scope['os'] = os
                exec_scope['print'] = print
                exec_scope['__name__'] = '__main__'
            except Exception:
                pass
            try:
                self._preload_chain_modules(exec_scope)
            except Exception:
                pass
            
            # Execute code with capture
            # Use llm_executor.variables as globals to allow scripts to share state (aggregation)
            with contextlib.redirect_stdout(stdout_capture), contextlib.redirect_stderr(stderr_capture):
                exec(code_to_run, exec_scope, exec_scope)
            
            # Get captured output
            stdout_str = stdout_capture.getvalue()
            stderr_str = stderr_capture.getvalue()
            terminal_output = stdout_str + stderr_str
            
            if stdout_str:
                logger.info(f"Code node stdout:\n{stdout_str}")
            if stderr_str:
                logger.warning(f"Code node stderr:\n{stderr_str}")

            # Extract result
            result = exec_scope.get(output_variable)
            if result is None and output_variable == 'result':
                 result = exec_scope.get('output')
            
            # If result is still None, use terminal_output as the main output
            if result is None:
                result = terminal_output
            
            # Store result and terminal output
            node_id = node.get('id') or node.get('node_id')
            self.llm_executor.set_variable(f"node_{node_id}_output", result)
            self.llm_executor.set_variable(f"node_{node_id}_result", result)
            self.llm_executor.set_variable(f"node_{node_id}_stdout", stdout_str)
            self.llm_executor.set_variable(f"node_{node_id}_stderr", stderr_str)
            self.llm_executor.set_variable(f"node_{node_id}_terminal_output", terminal_output)
            
            logger.info(f"Code node executed successfully.")
            try:
                node['last_output_port'] = 'output'
            except Exception:
                pass
            # Provide success context payload for downstream LLMs connected via 'context'
            try:
                code_text = ""
                script_path = self.llm_executor.get_variable(f"node_{node_id}_code_path") or ""
                if script_path and os.path.exists(script_path):
                    with open(script_path, 'r', encoding='utf-8') as _f:
                        code_text = _f.read()
                ctx_success = ("TERMINAL OUTPUT:\n" + terminal_output + "\n\n" + "CODE FILE: " + str(script_path) + "\n```python\n" + code_text + "\n```")
                self.llm_executor.set_variable(f"node_{node_id}_context", ctx_success)
            except Exception:
                pass
            
            return self._get_code_next_node(node, 'output')
            
        except Exception as e:
            # Capture what we have so far
            stdout_str = stdout_capture.getvalue()
            stderr_str = stderr_capture.getvalue()
            
            if stdout_str:
                logger.info(f"Code node stdout (before error):\n{stdout_str}")
            if stderr_str:
                logger.warning(f"Code node stderr (before error):\n{stderr_str}")

            logger.error(f"Code execution failed: {e}")
            import traceback
            error_trace = traceback.format_exc()
            logger.error(error_trace)
            
            # Combined error output
            full_error_output = f"{stdout_str}\n{stderr_str}\nError:\n{error_trace}"
            
            if not node_id:
                node_id = node.get('id') or node.get('node_id') or code_data.get('node_id') or code_data.get('id')
            self._set_code_node_error(node, error_trace)
            self.llm_executor.set_variable(f"node_{node_id}_terminal_output", full_error_output)
            self.llm_executor.set_variable(f"node_{node_id}_stdout", stdout_str)
            self.llm_executor.set_variable(f"node_{node_id}_stderr", stderr_str + "\n" + error_trace)
            try:
                script_path = self.llm_executor.get_variable(f"node_{node_id}_code_path") or ""
                code_text = ""
                if script_path and os.path.exists(script_path):
                    with open(script_path, 'r', encoding='utf-8') as _f:
                        code_text = _f.read()
                ctx = ("TERMINAL OUTPUT:\n" + full_error_output + "\n\n" + "CODE FILE: " + str(script_path) + "\n```python\n" + code_text + "\n```")
                self.llm_executor.set_variable(f"node_{node_id}_context", ctx)
            except Exception:
                pass
            try:
                node['last_output_port'] = 'error'
            except Exception:
                pass
            
            # Check if error output is connected
            # Retry control: allow up to 3 patch cycles
            try:
                if not node_id:
                    node_id = node.get('id') or node.get('node_id') or code_data.get('node_id') or code_data.get('id')
                cnt = int(self._code_retry_counts.get(node_id, 0) or 0)
                cnt += 1
                self._code_retry_counts[node_id] = cnt
                if cnt > 3:
                    logger.error(f"Code node {node_id} exceeded max retries (3). Failing workflow.")
                    return None
            except Exception:
                pass
            next_node = self._get_code_next_node(node, 'error')
            if next_node and next_node != "__done__":
                return next_node
            return None # Fail workflow

    def _get_code_next_node(self, node, port):
        connections = node.get('connections', {})
        if port in connections and connections[port]:
             return connections[port][0]['node_id']
        if port == 'output':
            return "__done__"
        return None

    def _set_code_node_error(self, node, error_msg):
        code_data = node.get('data', {}) if isinstance(node, dict) else {}
        node_id = None
        try:
            node_id = node.get('id') or node.get('node_id') or code_data.get('node_id') or code_data.get('id')
        except Exception:
            node_id = code_data.get('node_id') or code_data.get('id')
        self.llm_executor.set_variable(f"node_{node_id}_error", error_msg)

    def _sanitize_alias(self, alias):
        try:
            import re
            s = str(alias or '').strip()
            s = re.sub(r"\W+", "_", s)
            if not s:
                return "mod"
            if not re.match(r"^[A-Za-z_]", s):
                s = "mod_" + s
            return s
        except Exception:
            return "mod"

    def _preload_chain_modules(self, scope):
        try:
            idx = self.llm_executor.get_variable('code_modules') or []
            import importlib.util as _ilu
            import sys as _sys
            for entry in (idx if isinstance(idx, list) else []):
                try:
                    p = entry.get('path')
                    a = entry.get('alias') or entry.get('id')
                    sid = entry.get('id')
                    safe = self._sanitize_alias(a or sid)
                    varname = safe if str(safe).startswith("module_") else f"module_{safe}"
                    if not p or not os.path.exists(p):
                        continue
                    spec = _ilu.spec_from_file_location(varname, p)
                    if not spec or not spec.loader:
                        continue
                    mod = _ilu.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    scope[varname] = mod
                    try:
                        _sys.modules[varname] = mod
                    except Exception:
                        pass
                    try:
                        self.llm_executor.set_variable(f"module_{safe}_path", p)
                    except Exception:
                        pass
                except Exception:
                    continue
        except Exception:
            pass

    def _is_persistent_loop(self, code_str):
        try:
            s = (code_str or '')
            sl = s.lower()
            # Broad GUI detection
            gui_indicators = (
                'import tkinter', 'from tkinter', 'tkinter.', 'Tk(', 'mainloop(',
                'import pyqt5', 'from pyqt5', 'pyside2', 'pyside6', 'qtcore', 'qtwidgets', 'exec_',
                'import wx', 'from wx', 'wx.App(',
                'import pygame', 'from pygame', 'pygame.display',
                'import dearpygui', 'from dearpygui', 'dpg.create_context',
                'import kivy', 'from kivy', 'kivy.app',
                'matplotlib.pyplot.show(', 'plt.show(',
                'cefpython', 'pywebview'
            )
            for g in gui_indicators:
                if g in sl:
                    return True
            # Generic infinite loop hints
            if ('while True' in s) or ('while\tTrue' in s):
                return True
            return False
        except Exception:
            return False

    def _run_persistent_script(self, script_path, timeout_s, cwd, leave_running=False):
        try:
            # Use the venv python if available, otherwise find a valid python executable
            python_exe = self._venv_python
            if not python_exe or not os.path.exists(python_exe):
                self._ensure_chain_venv()
                python_exe = self._venv_python

            # CRITICAL: In frozen mode (PyInstaller), sys.executable points to LoOper.exe.
            # We have updated LoOper's main.py to act as a Python interpreter if passed a .py file.
            # This allows us to run persistent scripts even if a separate venv isn't available.
            if getattr(sys, 'frozen', False):
                if not python_exe or not os.path.exists(python_exe):
                    # Use LoOper.exe itself as the interpreter
                    python_exe = sys.executable
            
            cmd = [python_exe or sys.executable, script_path]
            env = os.environ.copy()
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            CREATE_NO_WINDOW = 0x08000000
            proc = _sp.Popen(cmd, cwd=cwd, stdout=_sp.PIPE, stderr=_sp.PIPE, text=True, creationflags=CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW)
            if leave_running:
                start = time.time()
                rc = None
                while (time.time() - start) < 1.0:
                    try:
                        rc = proc.poll()
                    except Exception:
                        rc = None
                    if rc is not None:
                        break
                    try:
                        time.sleep(0.05)
                    except Exception:
                        break
                if rc is not None:
                    ok = (rc == 0)
                    stdout_str = ''
                    stderr_str = ''
                    try:
                        out, err = proc.communicate(timeout=0.2)
                        stdout_str = out or ''
                        stderr_str = err or ''
                    except Exception:
                        pass
                    terminal_output = (stdout_str or '') + (stderr_str or '')
                    result = terminal_output or ''
                    try:
                        return ok, result, stdout_str, stderr_str, terminal_output, None
                    except Exception:
                        return ok, result, stdout_str, stderr_str, terminal_output
                try:
                    pid = proc.pid
                except Exception:
                    pid = None
                try:
                    return True, '', '', '', (f"Process started PID {pid}" if pid else ''), pid
                except Exception:
                    return True, '', '', '', '', pid
            start = time.time()
            stdout_chunks = []
            stderr_chunks = []
            ok = False
            # Allow short warm-up, then close if still running (treat as success)
            while True:
                rc = proc.poll()
                if rc is not None:
                    ok = (rc == 0)
                    break
                if (time.time() - start) >= min(2.0, float(timeout_s or 2.0)):
                    # Consider persistent; close it and mark success
                    try:
                        proc.terminate()
                        try:
                            proc.wait(timeout=0.5)
                        except Exception:
                            pass
                        if proc.poll() is None:
                            proc.kill()
                            try:
                                # Windows fallback: kill process tree
                                # Use CREATE_NO_WINDOW on Windows to prevent console popup
                                cflags = 0x08000000 if sys.platform == "win32" else 0
                                _sp.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, text=True, timeout=2, creationflags=cflags)
                            except Exception:
                                pass
                    except Exception:
                        pass
                    ok = True
                    break
                try:
                    time.sleep(0.05)
                except Exception:
                    break
            stdout_str = ''
            stderr_str = ''
            try:
                out, err = proc.communicate(timeout=0.2)
                stdout_str = out or ''
                stderr_str = err or ''
            except Exception:
                # Fallback: read whatever is available
                try:
                    if proc.stdout:
                        stdout_str = (proc.stdout.read() or '')
                except Exception:
                    pass
                try:
                    if proc.stderr:
                        stderr_str = (proc.stderr.read() or '')
                except Exception:
                    pass
            terminal_output = (stdout_str or '') + (stderr_str or '')
            result = terminal_output or ''
            try:
                return ok, result, stdout_str, stderr_str, terminal_output, None
            except Exception:
                return ok, result, stdout_str, stderr_str, terminal_output
        except Exception:
            try:
                return False, '', '', 'subprocess failed', 'subprocess failed', None
            except Exception:
                return False, '', '', 'subprocess failed', 'subprocess failed'

    def _initialize_chain_runtime(self):
        try:
            root = os.path.join(os.getcwd(), 'runtime')
            self._runtime_root = root
            lbl = None
            try:
                base = getattr(self.sequence_executor, 'chain_file_dir', None)
                if base:
                    lbl = os.path.basename(str(base))
            except Exception:
                lbl = None
            if not lbl:
                lbl = 'chain'
            self._chain_label = lbl
            chain_dir = os.path.join(root, 'chains', lbl)
            venv_dir = os.path.join(root, 'venvs', lbl)
            os.makedirs(chain_dir, exist_ok=True)
            os.makedirs(venv_dir, exist_ok=True)
            self._chain_runtime_dir = chain_dir
            self._venv_dir = venv_dir
        except Exception:
            pass

    def _ensure_chain_venv(self):
        try:
            if not self._venv_dir:
                self._initialize_chain_runtime()
            py_exe = os.path.join(self._venv_dir, 'Scripts', 'python.exe')
            if not os.path.exists(py_exe):
                logger.info(f"Creating new venv at {self._venv_dir}")
                builder = venv.EnvBuilder(with_pip=True)
                builder.create(self._venv_dir)
            self._venv_python = os.path.join(self._venv_dir, 'Scripts', 'python.exe')
            self._venv_site_packages = os.path.join(self._venv_dir, 'Lib', 'site-packages')
        except Exception as e:
            logger.error(f"Failed to ensure chain venv: {e}")
            # Fallback to system python if venv creation fails (e.g. in frozen app)
            # This allows execution but dependency installation might fail or affect global state
            # which is better than crashing
            if getattr(sys, 'frozen', False):
                 self._venv_python = sys.executable
            else:
                 self._venv_python = sys.executable

    def _is_last_code_node(self, node):
        """
        Determines if a code node should be left running after execution.
        
        For GUI applications and persistent processes, we want to keep them running
        even if there are subsequent nodes, so those nodes can interact with the UI.
        """
        try:
            # Get the code content to check if it's a GUI application
            code_data = node.get('data', {})
            code = code_data.get('code', '')
            
            # If it's a GUI application, always keep it running regardless of subsequent nodes
            if self._is_persistent_loop(code):
                logger.info(f"Code node {node.get('id') or node.get('node_id')} detected as GUI/persistent, will keep running")
                return True
            
            # Original logic for non-GUI code
            nid = node.get('id') or node.get('node_id')
            for tid, tnode in (self.workflow_graph.items() if isinstance(self.workflow_graph, dict) else []):
                for inp in (tnode.get('inputs', []) or []):
                    if str(inp.get('from_node')) == str(nid):
                        # If the consumer is a code/tts or anything non-LLM, this node is upstream and should not be left running
                        if tnode.get('type') != 'llm':
                            return False
            
            # Otherwise, inspect explicit output connections: if they exist and point only to LLMs, it's last
            connections = node.get('connections', {}) or {}
            outs = connections.get('output') or []
            if not outs:
                # No explicit output edges; if nobody consumes it in inputs, treat as last
                return True
            for c in outs:
                tid = c.get('node_id')
                if not tid:
                    continue
                t = self.workflow_graph.get(tid, {})
                if t.get('type') and t.get('type') != 'llm':
                    return False
            return True
        except Exception:
            return False

    def _detect_framework(self, code_str):
        try:
            s = (code_str or '').lower()
            if 'tkinter' in s:
                return 'tkinter'
            if ('pyqt5' in s) or ('from pyqt5' in s) or ('qtwidgets' in s):
                return 'pyqt5'
            if 'kivy' in s:
                return 'kivy'
            if ('wxpython' in s) or ('import wx' in s):
                return 'wxpython'
            if 'pywebview' in s:
                return 'pywebview'
            return ''
        except Exception:
            return ''

    def _install_code_dependencies(self, code_str):
        """
        Analyzes the code for imports and installs missing packages in the chain's venv.
        """
        try:
            self._ensure_chain_venv()
            if not self._venv_python:
                return {"installed": [], "failed": [], "skipped": [], "pip_stdout": "", "pip_stderr": ""}
            
            # Use AST for robust import parsing
            import ast
            pkgs = set()
            try:
                tree = ast.parse(code_str)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            pkgs.add(alias.name.split('.')[0])
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            pkgs.add(node.module.split('.')[0])
            except Exception as e:
                logger.warning(f"Failed to parse code imports with AST: {e}")
                # Fallback to simple regex if AST fails (e.g. syntax error in code)
                import re
                for line in (code_str or '').splitlines():
                    m1 = re.match(r"\s*import\s+([a-zA-Z0-9_\., ]+)", line)
                    if m1:
                        names = [n.strip() for n in m1.group(1).split(',') if n.strip()]
                        for n in names:
                            base = n.split(' as ')[0].strip()
                            pkgs.add(base.split('.')[0].strip())
                        continue
                    m2 = re.match(r"\s*from\s+([a-zA-Z0-9_\.]+)\s+import\s+", line)
                    if m2:
                        pkgs.add(m2.group(1).split('.')[0])

            stdlib = {
                'os','sys','json','re','typing','time','subprocess','pathlib','math','random','datetime','itertools','collections','functools','threading','asyncio','tkinter',
                'io', 'contextlib', 'shutil', 'glob', 'platform', 'warnings', 'logging', 'uuid', 'socket', 'urllib', 'http', 'email', 'base64', 'hashlib', 'hmac', 'struct', 'pickle', 'copy', 'weakref', 'enum', 'types', 'inspect', 'traceback', 'gc', 'abc', 'numbers', 'decimal', 'fractions', 'statistics', 'operator', 'unittest', 'doctest', 'pdb', 'profile', 'cProfile', 'timeit', 'venv', 'ensurepip', 'zipfile', 'tarfile', 'csv', 'sqlite3', 'zlib', 'gzip', 'bz2', 'lzma', 'xml', 'html', 'mimetypes', 'argparse', 'optparse', 'getopt', 'getpass', 'curses', 'ctypes', 'multiprocessing', 'concurrent', 'queue', 'select', 'mmap', 'signal', 'termios', 'tty', 'pty', 'fcntl', 'pipes', 'resource', 'nis', 'syslog', 'site', 'builtins'
            }
            
            wanted = [p for p in pkgs if p and p not in stdlib]
            
            if not wanted:
                return {"installed": [], "failed": [], "skipped": [], "pip_stdout": "", "pip_stderr": ""}
                
            # Check what's installed
            installed_output = ""
            try:
                # Use list_installed_packages via subprocess to be safe
                if hasattr(self, '_venv_python') and self._venv_python:
                    cmd = [self._venv_python, "-m", "pip", "list", "--format=json"]
                    if os.name == 'nt':
                        p = _sp.Popen(cmd, stdout=_sp.PIPE, stderr=_sp.PIPE, text=True, creationflags=_sp.CREATE_NO_WINDOW)
                    else:
                        p = _sp.Popen(cmd, stdout=_sp.PIPE, stderr=_sp.PIPE, text=True)
                    out, err = p.communicate()
                    installed_output = out
            except Exception:
                pass
                
            installed_names = set()
            try:
                import json
                data = json.loads(installed_output)
                for item in data:
                    installed_names.add(item['name'].lower())
            except Exception:
                pass
                
            to_install = []
            skipped = []
            
            # Mapping for common import names vs package names
            pkg_map = {
                'cv2': 'opencv-python',
                'PIL': 'Pillow',
                'bs4': 'beautifulsoup4',
                'sklearn': 'scikit-learn',
                'yaml': 'PyYAML',
                'dotenv': 'python-dotenv',
                'dateutil': 'python-dateutil',
                'jwt': 'PyJWT',
                'telegram': 'python-telegram-bot',
                'discord': 'discord.py',
                'google': 'google-api-python-client',
                'googleapiclient': 'google-api-python-client',
                'youtube_dl': 'youtube_dl',
                'yt_dlp': 'yt-dlp',
                'git': 'GitPython',
                'usb': 'pyusb',
                'serial': 'pyserial',
                'win32api': 'pywin32',
                'win32con': 'pywin32',
                'win32gui': 'pywin32',
                'cx_Oracle': 'cx_Oracle',
                'psycopg2': 'psycopg2-binary',
                'mysqldb': 'mysqlclient',
                'dns': 'dnspython',
                'jose': 'python-jose',
                'xlrd': 'xlrd',
                'xlsxwriter': 'XlsxWriter',
                'openpyxl': 'openpyxl',
                'pandas': 'pandas',
                'numpy': 'numpy',
                'requests': 'requests',
                'flask': 'Flask',
                'django': 'Django',
                'fastapi': 'fastapi',
                'uvicorn': 'uvicorn',
                'websockets': 'websockets',
                'engineio': 'python-engineio',
                'socketio': 'python-socketio',
                'plotly': 'plotly',
                'matplotlib': 'matplotlib',
                'seaborn': 'seaborn',
                'streamlit': 'streamlit',
                'gradio': 'gradio',
                'kivy': 'Kivy',
                'pygame': 'pygame',
                'pyglet': 'pyglet',
                'moviepy': 'moviepy',
                'pydub': 'pydub',
                'speech_recognition': 'SpeechRecognition',
                'gtts': 'gTTS',
                'playsound': 'playsound',
                'pyttsx3': 'pyttsx3',
                'openai': 'openai',
                'anthropic': 'anthropic',
                'google.generativeai': 'google-generativeai',
                'boto3': 'boto3',
                'docx': 'python-docx',
                'pptx': 'python-pptx',
                'fitz': 'pymupdf',
                'frontend': 'frontend', # Generic, but sometimes used
                'backend': 'backend'
            }
            
            # Check for explicit requirements in comments
            # Format: # requirements: package1, package2==1.0.0, package3
            import re
            for line in (code_str or '').splitlines():
                m_req = re.match(r"\s*#\s*requirements\s*:\s*(.+)", line, re.IGNORECASE)
                if m_req:
                    reqs = [r.strip() for r in m_req.group(1).split(',') if r.strip()]
                    for r in reqs:
                        # Add to wanted directly, bypassing stdlib check as these are explicit user requests
                        # We still resolve via pkg_map if possible, but usually these are pkg names
                        # Handle version specifiers
                        base_pkg = r.split('==')[0].split('>=')[0].split('<=')[0].split('>')[0].split('<')[0].strip()
                        resolved = pkg_map.get(base_pkg, base_pkg)
                        # Re-attach version if present (simple approach)
                        if base_pkg != r:
                            # It has version info, use as is but map the name part if needed?
                            # This is complex. Let's just assume user knows the pkg name if specifying version.
                            # But if they wrote "cv2==4.5", we want "opencv-python==4.5"
                            if base_pkg in pkg_map:
                                r = r.replace(base_pkg, pkg_map[base_pkg], 1)
                        else:
                            r = resolved
                        
                        if r not in wanted:
                             wanted.append(r)
            
            # Heuristics for implicit dependencies
            code_lower = (code_str or '').lower()
            
            # Pandas Excel support (openpyxl is required for read_excel/to_excel with .xlsx)
            if 'pandas' in pkgs:
                 if 'read_excel' in code_lower or '.xlsx' in code_lower or 'to_excel' in code_lower:
                     if 'openpyxl' not in wanted:
                         wanted.append('openpyxl')
            
            # Matplotlib often needs pillow for image operations
            if 'matplotlib' in pkgs and ('image' in code_lower or 'imshow' in code_lower):
                if 'Pillow' not in wanted:
                    wanted.append('Pillow')

            for w in wanted:
                real_pkg = pkg_map.get(w, w)
                # Simple heuristic: if import name is in installed list (case insensitive), skip
                # This is imperfect but saves time
                if real_pkg.lower() in installed_names:
                    skipped.append(real_pkg)
                else:
                    to_install.append(real_pkg)
            
            if not to_install:
                 return {"installed": [], "failed": [], "skipped": skipped, "pip_stdout": "", "pip_stderr": ""}
                 
            logger.info(f"Installing code dependencies: {to_install}")
            
            cmd = [self._venv_python, "-m", "pip", "install"] + to_install
            
            if os.name == 'nt':
                process = _sp.Popen(cmd, stdout=_sp.PIPE, stderr=_sp.PIPE, text=True, creationflags=_sp.CREATE_NO_WINDOW)
            else:
                process = _sp.Popen(cmd, stdout=_sp.PIPE, stderr=_sp.PIPE, text=True)
                
            stdout, stderr = process.communicate()
            
            installed = []
            failed = []
            
            if process.returncode == 0:
                installed = to_install
            else:
                # Try to determine which failed? For now assume all failed if non-zero
                failed = to_install
                logger.error(f"Pip install failed: {stderr}")
                
            return {
                "installed": installed,
                "failed": failed,
                "skipped": skipped,
                "pip_stdout": stdout,
                "pip_stderr": stderr
            }

        except Exception as e:
            logger.error(f"Dependency installation error: {e}")
            return {"installed": [], "failed": [], "skipped": [], "pip_stdout": "", "pip_stderr": str(e)}
