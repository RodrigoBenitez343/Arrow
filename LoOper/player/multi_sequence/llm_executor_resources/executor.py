import sys
import os
import threading
import time
import re
from typing import Dict, Optional, Any
import requests as _rq
import subprocess as _sp

from ...config import logger
from .config_utils import get_default_api_url
from .prompt_utils import substitute_variables, assemble_enhanced_prompt
from .inputs import get_input_text, _context_input
from .rag_utils import build_rag_context, build_rag_context_from_docs
from .vision_utils import prepare_vision_images


class LLMExecutor:
    """Executes LLM nodes in workflow graphs (modularized)."""

    def __init__(self, variables: Optional[Dict[str, object]] = None):
        self.variables: Dict[str, object] = variables or {}
        self.context_collector = None
        self.context_db = None
        self._initialize_context_collector()
        self._initialize_context_database()
        self._rich_console = None
        try:
            from rich.console import Console
            self._rich_console = Console()
        except Exception:
            self._rich_console = None

    def _initialize_context_collector(self) -> None:
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            if project_root not in sys.path:
                sys.path.append(project_root)
            from AI.context_collector import ContextCollector  # noqa: F401
            self.context_collector = ContextCollector() if 'ContextCollector' in globals() else None
        except Exception:
            self.context_collector = None

    def _initialize_context_database(self) -> None:
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            if project_root not in sys.path:
                sys.path.append(project_root)
            from AI.context_database import ContextDatabase  # noqa: F401
            self.context_db = ContextDatabase() if 'ContextDatabase' in globals() else None
        except Exception:
            self.context_db = None

    def execute_llm_node(self, node: Dict, stop_flag, action_handlers=None) -> Optional[str]:
        """Execute an LLM node and return the next node ID to run."""
        llm_data = node.get('data', {})
        logger.info("=== STARTING LLM NODE EXECUTION ===")
        logger.info(f"Executing LLM node: {llm_data.get('name', llm_data.get('id', llm_data.get('node_id', 'Unknown')))}")
        logger.info(f"Full node data: {node}")

        if stop_flag and stop_flag():
            logger.info("LLM node execution stopped by user request")
            return None

        try:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            if project_root not in sys.path:
                sys.path.append(project_root)
            from AI.consult import OllamaClient
        except Exception as e:
            logger.error(f"Failed to import OllamaClient: {e}")
            return None

        llm_config = llm_data.get('llm_configuration', {}) or llm_data
        logger.info(f"LLM configuration: {llm_config}")

        model = llm_config.get('model', 'llama3.2')
        logger.info(f"Resolved model for execution: '{model}' (Default: 'llama3.2')")
        api_url = llm_config.get('api_url', get_default_api_url())
        system_message = llm_config.get('system_message', '')
        prompt = llm_config.get('prompt', '')
        temperature = llm_config.get('temperature', 0.7)
        max_tokens = int(llm_config.get('max_tokens', 4096) or 4096)
        output_variable = llm_config.get('output_variable', 'llm_output')
        write_text = llm_config.get('write_text', True)
        use_vision = llm_config.get('use_vision', False)
        use_advanced_vision = str(llm_config.get('use_advanced_vision', 'false')).lower() in ('true', '1', 'yes', 'on')
        screenshot_enabled = llm_config.get('screenshot_enabled', True)

        # Auto-detect concatenation: if this node has any inputs in the workflow graph,
        # treat them as "previous" source and enable direct RAG unless explicitly disabled.
        node_inputs = node.get('inputs', []) or []
        main_inputs = []
        try:
            for _inp in node_inputs:
                if isinstance(_inp, dict) and str(_inp.get('input_port')) in ('tools', 'context'):
                    continue
                main_inputs.append(_inp)
        except Exception:
            main_inputs = node_inputs
        has_input_connection = bool(main_inputs)
        connected_ids = []
        try:
            connected_ids = [inp.get('from_node') for inp in main_inputs if isinstance(inp, dict) and inp.get('from_node')]
        except Exception:
            connected_ids = []
        has_previous_llm_output = any(f"node_{cid}_output" in self.variables for cid in connected_ids)

        tool_inputs = []
        try:
            tool_inputs = [inp for inp in node_inputs if isinstance(inp, dict) and str(inp.get('input_port','')) == 'tools']
        except Exception:
            tool_inputs = []
        tool_ids = []
        try:
            tool_ids = [inp.get('from_node') for inp in tool_inputs if inp.get('from_node')]
        except Exception:
            tool_ids = []
        tool_desc_map = {}
        try:
            import json as _json
            raw_td = llm_config.get('tool_descriptions') or {}
            if isinstance(raw_td, str):
                raw_td = _json.loads(raw_td)
            if isinstance(raw_td, dict):
                tool_desc_map = raw_td
        except Exception:
            tool_desc_map = {}
        if tool_ids:
            try:
                lines = ["Available Tools:"]
                for tid in tool_ids:
                    desc = tool_desc_map.get(tid, '')
                    if desc:
                        lines.append(f"- {tid}: {desc}")
                    else:
                        lines.append(f"- {tid}")
                lines.append("Tool Usage Guide:")
                lines.append("- Choose exactly one tool id.")
                lines.append("- Respond ONLY with 'USE_TOOL:<id>' with no other text.")
                lines.append("- Output must be exactly one line: USE_TOOL:<id>.")
                lines.append("- Do not include quotes, markdown, code blocks, punctuation, or extra lines.")
                lines.append("- Do not explain, reason, or justify the choice.")
                extra = "\n".join(lines)
                system_message = (system_message + "\n\n" + extra).strip()
            except Exception:
                pass

        # Detect context connections and whether upstream context is available
        context_inputs = []
        try:
            context_inputs = [inp for inp in node_inputs if isinstance(inp, dict) and str(inp.get('input_port','')) == 'context']
        except Exception:
            context_inputs = []
        context_ids = []
        try:
            context_ids = [inp.get('from_node') for inp in context_inputs if inp.get('from_node')]
        except Exception:
            context_ids = []
        has_context_connection = bool(context_ids)
        has_upstream_context_value = any(f"node_{cid}_context" in self.variables for cid in context_ids)

        configured_input_source = (llm_config.get('input_source', 'none') or 'none')
        input_source = configured_input_source
        if configured_input_source.lower() in ('none', '', 'null'):
            if has_context_connection and has_upstream_context_value:
                input_source = 'context'
                logger.info("Auto-enabled input_source='context' due to detected context connection")
            elif has_input_connection and has_previous_llm_output:
                input_source = 'previous'
                logger.info("Auto-enabled input_source='previous' due to detected input connection for concatenation")

        ocr_confidence = llm_config.get('ocr_confidence', 60)
        logger.info(f"Input source: {input_source}, OCR confidence: {ocr_confidence}")

        use_comorag = str(llm_config.get('use_comorag', 'true')).lower() in ('true', '1', 'yes', 'on')
        max_cycles = int(llm_config.get('max_cycles', 5))
        memory_size = int(llm_config.get('memory_size', 1000))

        use_direct_rag = str(llm_config.get('use_direct_rag', 'false')).lower() in ('true', '1', 'yes', 'on')
        # Respect explicit user choice for RAG; do not auto-enable on concatenation.
        # Allow explicit selection of previous/context input mode overriding use_direct_rag
        prev_mode = (llm_config.get('previous_input_mode') or llm_config.get('context_input_mode') or '').strip().lower()
        if input_source.lower() in ('previous', 'previous_node', 'context') and prev_mode in ('raw', 'embed'):
            use_direct_rag = (prev_mode == 'embed')
        rag_embedding_model = llm_config.get('rag_embedding_model', 'nomic-embed-text')
        rag_chunk_size = int(llm_config.get('rag_chunk_size', 500))
        rag_overlap = int(llm_config.get('rag_overlap', 100))
        rag_top_k = int(llm_config.get('rag_top_k', 3))
        rag_include_raw_input = str(llm_config.get('rag_include_raw_input', 'false')).lower() in ('true', '1', 'yes', 'on')
        rag_max_chars = int(llm_config.get('rag_max_chars', 1500))

        logger.info("LLM Parameters:")
        logger.info(f"  Model: {model}")
        logger.info(f"  API URL: {api_url}")
        logger.info(f"  System Message: {system_message}")
        logger.info(f"  Original Prompt: {prompt}")
        logger.info(f"  Temperature: {temperature}")
        logger.info(f"  Max Tokens: {max_tokens}")
        logger.info(f"  Output Variable: {output_variable}")
        logger.info(f"  Write Text: {write_text}")
        logger.info(f"  Use Vision: {use_vision}")
        logger.info(f"  Screenshot Enabled: {screenshot_enabled}")
        try:
            if self._rich_console:
                from rich.table import Table
                from rich.panel import Panel
                self._rich_console.rule("[bold yellow]LLM Request")
                info = Table(title="LLM Node", show_header=True, header_style="bold magenta")
                info.add_column("Field", style="bold")
                info.add_column("Value")
                info.add_row("Model", str(model))
                info.add_row("API", str(api_url))
                info.add_row("Input Source", str(input_source))
                info.add_row("Prev/Context Mode", str(prev_mode or ""))
                info.add_row("Direct RAG", "true" if use_direct_rag else "false")
                info.add_row("RAG Model", str(rag_embedding_model))
                info.add_row("Top K", str(rag_top_k))
                self._rich_console.print(info)
                self._rich_console.print(Panel((system_message or ""), title="System Message", border_style="cyan"))
                self._rich_console.print(Panel(prompt, title="Prompt", border_style="cyan"))
                if tool_ids:
                    tools_tbl = Table(title="Available Tools", show_header=True, header_style="bold cyan")
                    tools_tbl.add_column("ID", style="bold")
                    tools_tbl.add_column("Description")
                    for tid in tool_ids:
                        d = tool_desc_map.get(tid, '')
                        tools_tbl.add_row(str(tid), str(d or ""))
                    self._rich_console.print(tools_tbl)
        except Exception:
            pass

        if stop_flag and stop_flag():
            logger.info("LLM node execution stopped by user request during configuration")
            return None

        prompt = substitute_variables(prompt, self.variables)
        logger.info(f"Prompt after variable substitution: {prompt}")

        if stop_flag and stop_flag():
            logger.info("LLM node execution stopped by user request before input processing")
            return None

        input_text, use_input_text_in_prompt = get_input_text(
            node,
            self.variables,
            input_source,
            ocr_confidence,
            stop_flag,
            use_direct_rag,
        )

        if input_text is not None:
            if use_input_text_in_prompt:
                prompt = assemble_enhanced_prompt(prompt, input_source, input_text, include_raw=True)
                logger.info(f"Enhanced prompt with {input_source} input")
            else:
                logger.info("Direct RAG enabled; skipping raw previous text injection into prompt")
        if use_comorag and input_text and not use_input_text_in_prompt:
            prompt = f"{input_text}\n\n{prompt}"
        try:
            if self._rich_console:
                from rich.panel import Panel
                ctx_text = _context_input(node, self.variables)
                routes = []
                for inp in (node.get('inputs', []) or []):
                    if str(inp.get('input_port')) == 'context':
                        routes.append(str(inp.get('from_node')))
                ctx_info = f"Sources: {', '.join(routes) or 'None'}\n\n" + (ctx_text[:800] or "")
                self._rich_console.print(Panel(ctx_info, title="Context Routing", border_style="green"))
        except Exception:
            pass

        logger.info(f"Preparing Ollama request thread for base_url: {api_url}")
        # Always attempt to stop running models to ensure clean state for the new one
        # unless explicitly disabled via OLLAMA_PRE_STOP=false
        try:
            _pre_stop = str(os.getenv('OLLAMA_PRE_STOP','true')).lower() not in ('false','0','no','off')
            if _pre_stop:
                logger.info("Checking for running Ollama models to unload...")
                _base = f"http://{os.getenv('OLLAMA_HOST','localhost')}:{os.getenv('OLLAMA_PORT','11434')}"
                try:
                    _ps = _rq.get(f"{_base}/api/ps", headers={"Connection": "close"}, timeout=4)
                    if _ps.status_code == 200:
                        _data = _ps.json() if hasattr(_ps, 'json') else {}
                        _items = _data.get("models") if isinstance(_data, dict) else (_data if isinstance(_data, list) else [])
                        
                        if isinstance(_items, list) and _items:
                            logger.info(f"Found {len(_items)} running models.")
                            for _m in _items:
                                _pid = _m.get("id") if isinstance(_m, dict) else None
                                _name = (_m.get("model") or _m.get("name")) if isinstance(_m, dict) else None
                                
                                # Optimization: Don't stop the model we are about to use
                                # improved matching to handle tags (e.g. llama3.2 vs llama3.2:latest)
                                if _name == model or (_name and model and (_name.startswith(model + ":") or model.startswith(_name + ":"))):
                                    logger.info(f"Model {_name} matches target {model}. Keeping it.")
                                    continue

                                _payload = {}
                                if _pid:
                                    _payload["id"] = _pid
                                if _name:
                                    _payload["model"] = _name
                                
                                if _payload:
                                    try:
                                        logger.info(f"Stopping competing model: {_name or _pid}")
                                        _rq.post(f"{_base}/api/stop", json=_payload, headers={"Connection": "close"}, timeout=3)
                                    except Exception as e:
                                        logger.warning(f"Failed to stop model {_name}: {e}")
                        else:
                             logger.debug("No running models found.")
                except Exception as e:
                    logger.warning(f"Failed to query/stop Ollama models: {e}")
        except Exception:
            pass

        rag_context_text = None
        rag_docs_context = None
        emb_client = None
        try:
            emb_client = OllamaClient(base_url=api_url, debug=True, use_comorag=use_comorag)
        except Exception:
            emb_client = None
        if use_direct_rag and input_source in ('previous', 'previous_node') and input_text and input_text.strip():
            pre_chunks = None
            pre_embs = None
            try:
                if connected_ids:
                    prev_id = connected_ids[0]
                    store = self.variables.get(f"node_{prev_id}_vectorstore")
                    if isinstance(store, dict):
                        pre_chunks = store.get('chunks')
                        pre_embs = store.get('embeddings')
            except Exception:
                pass
            rag_context_text, updated_prompt = build_rag_context(
                emb_client,
                prompt,
                input_text,
                rag_embedding_model,
                rag_chunk_size,
                rag_overlap,
                rag_top_k,
                rag_include_raw_input,
                rag_max_chars,
                input_source,
                use_input_text_in_prompt,
                pre_chunks,
                pre_embs,
            )
            if updated_prompt:
                prompt = updated_prompt

        try:
            import json as _json
            rag_docs = llm_config.get('rag_documents', []) or []
            if isinstance(rag_docs, str):
                rag_docs = _json.loads(rag_docs)
        except Exception:
            rag_docs = []
        if rag_docs and use_direct_rag and emb_client is not None:
            rag_docs_context = build_rag_context_from_docs(
                emb_client,
                prompt,
                rag_docs,
                rag_embedding_model,
                rag_chunk_size,
                rag_overlap,
                rag_top_k,
                rag_max_chars,
            )

        if use_advanced_vision:
            logger.info("Advanced Vision enabled. Starting analysis...")
            try:
                # Pre-check for torch to avoid WinError 1114 hard crash
                try:
                    import torch
                except ImportError:
                    logger.warning("Torch not found. Skipping Advanced Vision to avoid crash.")
                    raise Exception("Torch not found")
                except Exception as e:
                    logger.warning(f"Torch import failed ({e}). Skipping Advanced Vision to avoid crash.")
                    raise e

                # Calculate path to Computer_vision module
                current_dir = os.path.dirname(os.path.abspath(__file__))
                # executor.py is in LoOper/player/multi_sequence/llm_executor_resources/
                # We need LoOper/player/Computer_vision/
                # Up 3 levels to LoOper/player
                looper_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
                cv_dir = os.path.join(looper_root, "player", "Computer_vision")
                
                if cv_dir not in sys.path:
                    sys.path.append(cv_dir)
                
                import model_vision
                import json as _json
                
                model_path = os.path.join(cv_dir, "Computer_Vision_1.5.3.onnx")
                if not os.path.exists(model_path):
                    logger.warning(f"Vision model not found at {model_path}")
                else:
                    # Initialize vision
                    vision_instance = model_vision.Vision(model_path)
                    
                    # Analyze screen (using monitor 1 as default)
                    vision_data = vision_instance.analyze_single_frame(1)
                    
                    # Serialize to JSON
                    vision_text = _json.dumps(vision_data, indent=2)
                    
                    # Integrate with ComoRAG if enabled
                    vision_integrated = False
                    if use_comorag and emb_client:
                        logger.info("Injecting Advanced Vision data into ComoRAG memory...")
                        try:
                            # Create a descriptive content string
                            memory_content = f"Real-time Screen Analysis (YOLO+OCR):\n{vision_text}"
                            
                            # Add to memory
                            success = emb_client.add_memory(
                                content=memory_content,
                                source="advanced_vision",
                                metadata={
                                    "timestamp": time.time(), 
                                    "type": "yolo_ocr_analysis",
                                    "monitor": 1
                                }
                            )
                            
                            if success:
                                logger.info("Successfully added vision data to ComoRAG memory.")
                                vision_integrated = True
                            else:
                                logger.warning("Failed to add vision data to ComoRAG memory.")
                        except Exception as mem_err:
                            logger.error(f"Error adding to ComoRAG memory: {mem_err}")
                    
                    if not vision_integrated:
                        # Fallback to prompt injection
                        prompt = f"Screen Analysis Data (Objects, Text, Interactions):\n{vision_text}\n\n{prompt}"
                        logger.info("Advanced Vision data injected into prompt.")
                    
            except Exception as e:
                logger.error(f"Advanced Vision execution failed: {e}")
                import traceback
                logger.error(traceback.format_exc())

        def _is_vision_model_name(name: str) -> bool:
            ml = str(name or "").lower()
            for v in ('llava', 'bakllava', 'moondream', 'llava-llama3', 'llava-phi3', 'minicpm-v'):
                if v in ml:
                    return True
            return False
        is_vision_model = _is_vision_model_name(model)
        should_use_vision = use_vision or is_vision_model
        images, _ocr_text = prepare_vision_images(should_use_vision, screenshot_enabled, stop_flag)
        try:
            if emb_client:
                emb_client.close()
        except Exception:
            pass

        if rag_context_text:
            prompt = f"{rag_context_text}\n\n{prompt}"
        if rag_docs_context:
            prompt = f"{rag_docs_context}\n\n{prompt}"
        if use_direct_rag and not rag_context_text and input_source in ('previous', 'previous_node') and input_text:
            prompt = assemble_enhanced_prompt(prompt, input_source, input_text, include_raw=True)

        if stop_flag and stop_flag():
            logger.info("LLM node execution stopped by user request before API call")
            return None

        def _task_runner(out_dict: Dict[str, Any]):
            if stop_flag and stop_flag():
                return
            try:
                from AI.consult import OllamaClient as _Client
                # Disable client request timeouts for local LLMs
                cli = _Client(base_url=api_url, debug=True, use_comorag=use_comorag, timeout=None)
                if use_comorag and hasattr(cli, 'set_comorag_config'):
                    cli.set_comorag_config(max_cycles=max_cycles, memory_size=memory_size)
                # Mark writing active for streaming typing paths
                _node_id = llm_data.get('node_id') or llm_data.get('id') or node.get('id')
                if use_comorag:
                    res = cli.generate(
                        model=model,
                        prompt=prompt,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        system=system_message or None,
                        images=images,
                        context=None,
                        async_request=False,
                    )
                    # Check for fallback
                    if isinstance(res, dict) and res.get('comorag_fallback'):
                        logger.warning(f"ComoRAG execution failed and fell back to standard generation: {res.get('comorag_error')}")
                    elif isinstance(res, dict) and isinstance(res.get('comorag_metadata'), dict):
                        # Log success stats
                        meta = res.get('comorag_metadata')
                        cycles = meta.get('reasoning_cycles', 0)
                        logger.info(f"ComoRAG execution completed with {cycles} reasoning cycles.")
                        if cycles == 0:
                            logger.warning("ComoRAG returned 0 reasoning cycles. Check OverWatch logs for details.")
                    out_dict["result"] = res
                else:
                    stream_flag = bool(llm_config.get('stream', True)) and (not tool_ids)
                    out_dict["stream_used"] = stream_flag
                    if stream_flag and write_text and action_handlers and hasattr(action_handlers, 'handle_type_string_action') and hasattr(cli, 'chat_stream'):
                        write_mode = str(llm_config.get('write_mode') or 'type').lower()
                        _accum = []
                        # Signal: upstream LLM is actively typing via stream
                        try:
                            if _node_id:
                                self.variables[f"node_{_node_id}_writing"] = True
                        except Exception:
                            pass
                        if write_mode == 'paste':
                            res = cli.chat_stream(
                                model=model,
                                prompt=prompt,
                                temperature=temperature,
                                max_tokens=max_tokens,
                                system=system_message or None,
                                images=images,
                                on_delta=None,
                            )
                        else:
                            _buf = []
                            _full_text_tracker = ""
                            _last_flush = time.time()
                            _FLUSH_CHARS = int(llm_config.get('stream_flush_chars', 120) or 120)
                            _FLUSH_MS = float(llm_config.get('stream_flush_ms', 0.25) or 0.25)
                            def _flush_buffer():
                                if stop_flag and stop_flag():
                                    return
                                if not _buf:
                                    return
                                try:
                                    chunk = "".join(_buf)
                                    _buf.clear()
                                    action_handlers.handle_type_string_action(0, {
                                        "text": chunk,
                                        "force_typing": True,
                                        "batch_size": int(llm_config.get('typing_batch_size', 5) or 5),
                                        "batch_delay": float(llm_config.get('typing_batch_delay', 0.01) or 0.01),
                                        "char_by_char": bool(llm_config.get('char_by_char', False)),
                                        "keystroke_delay": float(llm_config.get('keystroke_delay', 0.001) or 0.001),
                                    }, stop_flag)
                                except Exception:
                                    pass
                            def _on_delta(s: str):
                                nonlocal _full_text_tracker
                                if stop_flag and stop_flag():
                                    raise Exception("Stopped by user")
                                try:
                                    # Handle case where backend sends full text instead of deltas
                                    real_delta = s
                                    if len(_full_text_tracker) > 0 and len(s) > len(_full_text_tracker) and s.startswith(_full_text_tracker):
                                        real_delta = s[len(_full_text_tracker):]
                                        _full_text_tracker = s
                                    elif len(s) == len(_full_text_tracker) and s == _full_text_tracker:
                                        # Duplicate full text frame
                                        real_delta = ""
                                    else:
                                        # Standard delta behavior
                                        real_delta = s
                                        _full_text_tracker += s
                                    
                                    if not real_delta:
                                        return

                                    _accum.append(real_delta)
                                    _buf.append(real_delta)
                                    try:
                                        if _node_id:
                                            _cur = out_dict.get("_accum_text") or ""
                                            _cur += real_delta
                                            out_dict["_accum_text"] = _cur
                                            self.variables[f"node_{_node_id}_output"] = _cur
                                            self.variables[output_variable] = _cur
                                    except Exception:
                                        pass
                                    if sum(len(x) for x in _buf) >= _FLUSH_CHARS or (time.time() - _last_flush) >= _FLUSH_MS:
                                        _flush_buffer()
                                        _last_flush = time.time()
                                except Exception:
                                    pass
                            res = cli.chat_stream(
                                model=model,
                                prompt=prompt,
                                temperature=temperature,
                                max_tokens=max_tokens,
                                system=system_message or None,
                                images=images,
                                on_delta=_on_delta,
                            )
                            # Final flush for remaining buffered text
                            try:
                                _flush_buffer()
                            except Exception:
                                pass
                        # If streaming returned no result but we accumulated deltas, construct a response
                        if (not isinstance(res, dict)) or (isinstance(res, dict) and not (res.get('response') or res.get('text'))):
                            if _accum:
                                res = {"response": "".join(_accum)}
                        # Detect streaming failure and fallback to non-stream
                        if (not isinstance(res, dict)) or (isinstance(res, dict) and (res.get('error') or not res.get('response'))):
                            out_dict["stream_failed"] = True
                            try:
                                res2 = cli.chat(
                                    model=model,
                                    prompt=prompt,
                                    temperature=temperature,
                                    max_tokens=max_tokens,
                                    system=system_message or None,
                                    images=images,
                                    stream=False,
                                )
                                res = res2
                            except Exception:
                                pass
                        out_dict["result"] = res
                    else:
                        res = cli.chat(
                            model=model,
                            prompt=prompt,
                            temperature=temperature,
                            max_tokens=max_tokens,
                            system=system_message or None,
                            images=images,
                            stream=False,
                        )
                        out_dict["result"] = res
            except Exception as _e:
                logger.error(f"LLM task runner exception: {_e}", exc_info=True)
                out_dict["error"] = str(_e)
            finally:
                # Clear typing-active signal
                try:
                    if _node_id:
                        self.variables.pop(f"node_{_node_id}_writing", None)
                except Exception:
                    pass
                try:
                    _rq.post(f"{_base}/api/stop", json={"model": model}, headers={"Connection": "close"}, timeout=3)
                except Exception:
                    pass
                try:
                    def _find_ollama_bin():
                        cands = []
                        for k in ("OLLAMA_BIN", "OLLAMA_EXE"):
                            v = os.getenv(k)
                            if v:
                                cands.append(v)
                        la = os.getenv("LOCALAPPDATA")
                        if la:
                            cands.append(os.path.join(la, "Programs", "Ollama", "ollama.exe"))
                        cands.append(r"C:\\Program Files\\Ollama\\ollama.exe")
                        cands.append("ollama")
                        return cands
                    env = os.environ.copy()
                    for cmd in _find_ollama_bin():
                        try:
                            _sp.run([cmd, "stop", model], capture_output=True, text=True, timeout=4, env=env)
                            break
                        except Exception:
                            continue
                except Exception:
                    pass
                try:
                    cli.close()
                except Exception:
                    pass

        logger.info("Sending request to LLM API in thread")
        shared: Dict[str, Any] = {}
        th = threading.Thread(target=_task_runner, args=(shared,), daemon=True)
        th.start()
        
        # Disable request timeouts for local LLMs
        timeout_s = 0.0
        
        start_t = time.time()
        while th.is_alive():
            if stop_flag and stop_flag():
                logger.info("Stop flag detected. Attempting to stop LLM generation...")
                try:
                    for _ in range(2):
                        _rq.post(f"{_base}/api/stop", json={"model": model}, headers={"Connection": "close"}, timeout=2)
                        time.sleep(0.1)
                except Exception:
                    pass
                break
            # No timeout: allow long-running local models to complete
            try:
                time.sleep(0.05)
            except Exception:
                break
        try:
            th.join(timeout=0.2)
        except Exception:
            pass
        result = shared.get("result")
        if result is None and shared.get("error"):
            logger.error(f"LLM request failed: {shared.get('error')}")
            # Continue execution with empty response to avoid workflow stall
            result = {"response": ""}
        # If no explicit result but streaming accumulated text exists, use it
        if result is None and isinstance(shared.get("stream_used"), bool):
            # Attempt to salvage from a typed buffer stored via variables
            try:
                # Prefer node-specific output if already captured by action handler
                node_id = llm_data.get('node_id') or llm_data.get('id') or node.get('id')
                fallback_text = None
                if node_id:
                    fallback_text = self.variables.get(f"node_{node_id}_output")
                fallback_text = fallback_text or self.variables.get(output_variable)
                if fallback_text:
                    result = {"response": str(fallback_text)}
            except Exception:
                pass

        # Normalize response
        response_text = None
        if isinstance(result, dict):
            response_text = (
                result.get('response')
                or result.get('text')
                or (result.get('message') or {}).get('content')
            )
        # If tool selection is expected, extract and sanitize output to the marker only
        selected_tool_id = None
        if tool_ids and isinstance(response_text, str) and response_text:
            try:
                for marker in ("USE_TOOL:", "CALL_TOOL:", "TOOL:"):
                    idx = response_text.find(marker)
                    if idx >= 0:
                        val = response_text[idx+len(marker):].strip()
                        val = val.split()[0].strip()
                        while val and val[-1] in (":", ",", ";", ".", ")", "]", "}"):
                            val = val[:-1]
                        if val:
                            selected_tool_id = val
                            break
                # Accept bare tool id if model omitted marker
                if (not selected_tool_id) and (response_text.strip() in tool_ids):
                    selected_tool_id = response_text.strip()
                # Scan for any known tool id present anywhere in text
                if not selected_tool_id:
                    for tid in tool_ids:
                        if tid and (str(tid) in response_text):
                            selected_tool_id = tid
                            break
                # Scan by tool description keywords (e.g., open_browser/close_browser)
                if not selected_tool_id and isinstance(tool_desc_map, dict):
                    rt_lower = response_text.lower()
                    for tid, desc in tool_desc_map.items():
                        if not tid:
                            continue
                        d = str(desc or '')
                        if not d:
                            continue
                        d_lower = d.lower()
                        if d_lower and d_lower in rt_lower:
                            selected_tool_id = tid
                            break
                        tail = d.split(":")[-1].strip().lower()
                        if tail and tail in rt_lower:
                            selected_tool_id = tid
                            break
                if selected_tool_id and (selected_tool_id in tool_ids):
                    response_text = f"USE_TOOL:{selected_tool_id}"
            except Exception:
                pass
        # Semantic fallback based on previous/context input when no explicit selection detected
        if (not selected_tool_id) and tool_ids:
            try:
                sctx = input_text or _context_input(node, self.variables) or ""
                sl = str(sctx).lower()
                want_open = False
                want_close = False
                neg_indicators = ("no browser", "not open", "closed", "no visible", "none open")
                pos_indicators = ("browser is open", "is open", "open window", "visible browser")
                for w in neg_indicators:
                    if w in sl:
                        want_open = True
                        break
                if not want_open:
                    for w in pos_indicators:
                        if w in sl:
                            want_close = True
                            break
                # Prefer mapping via descriptions first
                if want_open and isinstance(tool_desc_map, dict):
                    for tid in tool_ids:
                        desc = str(tool_desc_map.get(tid, '')).lower()
                        if ("open_browser" in desc) or ("open browser" in desc) or (desc.endswith("open_browser")):
                            selected_tool_id = tid
                            break
                if (not selected_tool_id) and want_close and isinstance(tool_desc_map, dict):
                    for tid in tool_ids:
                        desc = str(tool_desc_map.get(tid, '')).lower()
                        if ("close_browser" in desc) or ("close browser" in desc) or (desc.endswith("close_browser")):
                            selected_tool_id = tid
                            break
                # Fallback by simple name heuristics if descriptions are missing
                if not selected_tool_id:
                    if want_open:
                        for tid in tool_ids:
                            nm = str(tool_desc_map.get(tid, '')).lower()
                            if "open" in nm and "close" not in nm:
                                selected_tool_id = tid
                                break
                    elif want_close:
                        for tid in tool_ids:
                            nm = str(tool_desc_map.get(tid, '')).lower()
                            if "close" in nm and "open" not in nm:
                                selected_tool_id = tid
                                break
                if selected_tool_id and (selected_tool_id in tool_ids):
                    response_text = f"USE_TOOL:{selected_tool_id}"
            except Exception:
                pass
        if not response_text:
            logger.error(f"LLM returned unexpected format or empty response: {result}")
            # Proceed with empty text to allow chaining
            response_text = ""
        try:
            if self._rich_console:
                from rich.panel import Panel
                self._rich_console.rule("[bold green]LLM Response")
                self._rich_console.print(Panel(response_text or "", title="Model Output", border_style="green"))
        except Exception:
            pass

        # Store outputs
        try:
            self.variables[output_variable] = response_text
            node_id = llm_data.get('node_id') or llm_data.get('id') or node.get('id')
            if node_id:
                self.variables[f"node_{node_id}_output"] = response_text
                self.variables[f"node_{node_id}_context"] = response_text
        except Exception:
            pass

        # Optional: write text via handlers when not streaming
        try:
            if write_text and action_handlers and hasattr(action_handlers, 'handle_type_string_action') and response_text and (not bool(llm_config.get('stream', True)) or bool(shared.get('stream_failed'))):
                logger.info("Writing LLM output via action handlers")
                
                # Prevent repetition if streaming partially succeeded but failed at the end
                text_to_write = response_text
                if bool(shared.get('stream_failed')) and bool(llm_config.get('stream', True)):
                    accumulated = shared.get("_accum_text", "")
                    if accumulated and text_to_write.startswith(accumulated):
                        text_to_write = text_to_write[len(accumulated):]
                        logger.info(f"Skipping {len(accumulated)} chars already typed during partial stream")
                    elif accumulated:
                        # Fallback: if not a clean prefix, we might have a conflict.
                        # For now, we write the full text to ensure correctness, 
                        # or we could try to find overlap.
                        # Given the user complaint about repetition, let's try to be smart.
                        # If the new text is just a continuation, we might see it.
                        pass

                if text_to_write:
                    write_mode = str(llm_config.get('write_mode') or 'type').lower()
                    if write_mode == 'paste':
                        try:
                            node_id = llm_data.get('node_id') or llm_data.get('id') or node.get('id')
                            if node_id:
                                self.variables[f"node_{node_id}_writing"] = True
                        except Exception:
                            pass
                        action = {"text": text_to_write, "use_clipboard": True}
                    else:
                        action = {
                            "text": text_to_write,
                            "force_typing": True,
                            "batch_size": int(llm_config.get('typing_batch_size', 5) or 5),
                            "batch_delay": float(llm_config.get('typing_batch_delay', 0.01) or 0.01),
                            "char_by_char": bool(llm_config.get('char_by_char', False)),
                            "keystroke_delay": float(llm_config.get('keystroke_delay', 0.001) or 0.001),
                        }
                    action_handlers.handle_type_string_action(0, action, stop_flag)
                    try:
                        node_id = llm_data.get('node_id') or llm_data.get('id') or node.get('id')
                        if node_id:
                            self.variables.pop(f"node_{node_id}_writing", None)
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"Failed to write text via action handlers: {e}")

        # Determine next node
        next_node = llm_data.get('next_node') or node.get('next')
        if not next_node:
            try:
                connections = node.get('connections', {})
                if isinstance(connections, dict):
                    output_conns = connections.get('output', [])
                    if output_conns:
                        next_node = output_conns[0].get('node_id')
                elif isinstance(connections, list) and connections:
                    # Handle list-style connections from chain JSON
                    next_node = connections[0].get('target_node_id') or connections[0].get('node_id')
            except Exception:
                pass
        try:
            if tool_ids and isinstance(response_text, str):
                sel = selected_tool_id
                if sel and (sel in tool_ids):
                    next_node = sel
                    logger.info(f"Tool selected by LLM: {sel}")
                    try:
                        if self._rich_console:
                            from rich.table import Table
                            from rich.panel import Panel
                            desc = tool_desc_map.get(sel, '')
                            t = Table(show_header=True, header_style="bold magenta")
                            t.add_column("Selected ID", style="bold")
                            t.add_column("Description")
                            t.add_row(str(sel), str(desc or ""))
                            self._rich_console.print(Panel(t, title="Tool Selected", border_style="magenta"))
                    except Exception:
                        pass
                else:
                    try:
                        if self._rich_console:
                            self._rich_console.print("[bold red]No tool selection marker found in response")
                    except Exception:
                        pass
        except Exception:
            pass
        # Ensure a non-None return to indicate successful execution to the workflow engine
        if not next_node:
            next_node = "__done__"
        logger.info(f"LLM node execution complete. Next node: {next_node}")
        return next_node

    # --- Variable helpers for compatibility ---
    def get_variable(self, name: str, default: Any = None) -> Any:
        try:
            return self.variables.get(name, default)
        except Exception:
            return default

    def set_variable(self, name: str, value: Any) -> None:
        try:
            self.variables[name] = value
        except Exception:
            pass

    def get_all_variables(self) -> Dict[str, Any]:
        try:
            return dict(self.variables)
        except Exception:
            return {}

    # --- TTS execution (standalone, no GUI) ---
    def execute_tts_standalone(self, tts_data: Dict[str, Any], context_variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute text-to-speech using pyttsx3, mirroring TTSNode behavior.

        Args:
            tts_data: TTS configuration (text, language, rate, volume, voice_id, save_to_file, output_file, wait_for_completion)
            context_variables: Optional variables for {var} substitution in text

        Returns:
            dict: Result with 'success' and 'message' or 'error'
        """
        try:
            import pyttsx3

            # Extract configuration with defaults aligned to TTSNode
            text = tts_data.get('text') or 'Hello, this is a text to speech message.'
            language = tts_data.get('language') or 'en'
            rate = tts_data.get('rate', 200)
            volume = tts_data.get('volume', 0.9)
            voice_id = tts_data.get('voice_id', 0)
            save_to_file = bool(tts_data.get('save_to_file', False))
            output_file = tts_data.get('output_file') or ''
            wait_for_completion = bool(tts_data.get('wait_for_completion', True))

            # Perform variable substitution using provided context and internal variables
            text_to_speak = str(text)
            # First apply explicit context variables
            if context_variables:
                for var_name, var_value in context_variables.items():
                    try:
                        text_to_speak = text_to_speak.replace(f"{{{var_name}}}", str(var_value))
                    except Exception:
                        continue
            # Then apply executor's variables for broader compatibility
            if self.variables:
                for var_name, var_value in self.variables.items():
                    try:
                        text_to_speak = text_to_speak.replace(f"{{{var_name}}}", str(var_value))
                    except Exception:
                        continue

            # Initialize TTS engine and configure
            engine = pyttsx3.init()
            try:
                engine.setProperty('rate', rate)
            except Exception:
                pass
            try:
                engine.setProperty('volume', volume)
            except Exception:
                pass

            # Select voice if available
            try:
                voices = engine.getProperty('voices')
                if voices and 0 <= int(voice_id) < len(voices):
                    engine.setProperty('voice', voices[int(voice_id)].id)
            except Exception:
                pass

            if save_to_file and output_file:
                # Save synthesized speech to a file
                engine.save_to_file(text_to_speak, output_file)
                if wait_for_completion:
                    engine.runAndWait()
                return {
                    'success': True,
                    'text': text_to_speak,
                    'saved_to_file': output_file,
                    'message': f'TTS saved to file: {output_file}'
                }
            else:
                # Speak out loud
                engine.say(text_to_speak)
                if wait_for_completion:
                    engine.runAndWait()
                preview = text_to_speak[:50]
                msg = f'TTS spoke: "{preview}..."' if len(text_to_speak) > 50 else f'TTS spoke: "{text_to_speak}"'
                return {
                    'success': True,
                    'text': text_to_speak,
                    'message': msg
                }

        except ImportError:
            return {
                'error': 'pyttsx3 library not installed. Please install it using: pip install pyttsx3'
            }
        except Exception as e:
            return {
                'error': f'TTS execution failed: {str(e)}'
            }
        
