import sys
import os
from typing import Dict, Optional, Any

from ...config import logger
from .config_utils import get_default_api_url
from .prompt_utils import substitute_variables, assemble_enhanced_prompt
from .inputs import get_input_text
from .rag_utils import build_rag_context
from .vision_utils import prepare_vision_images


class LLMExecutor:
    """Executes LLM nodes in workflow graphs (modularized)."""

    def __init__(self, variables: Optional[Dict[str, object]] = None):
        self.variables: Dict[str, object] = variables or {}
        self.context_collector = None
        self.context_db = None
        self._initialize_context_collector()
        self._initialize_context_database()

    def _initialize_context_collector(self) -> None:
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            if project_root not in sys.path:
                sys.path.append(project_root)
            from AI.context_collector import ContextCollector  # noqa: F401
            self.context_collector = ContextCollector() if 'ContextCollector' in globals() else None
        except Exception:
            self.context_collector = None

    def _initialize_context_database(self) -> None:
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            if project_root not in sys.path:
                sys.path.append(project_root)
            from AI.consult import OllamaClient
        except Exception as e:
            logger.error(f"Failed to import OllamaClient: {e}")
            return None

        llm_config = llm_data.get('llm_configuration', {}) or llm_data
        logger.info(f"LLM configuration: {llm_config}")

        model = llm_config.get('model', 'llama3.2')
        api_url = llm_config.get('api_url', get_default_api_url())
        system_message = llm_config.get('system_message', '')
        prompt = llm_config.get('prompt', '')
        temperature = llm_config.get('temperature', 0.7)
        max_tokens = None
        output_variable = llm_config.get('output_variable', 'llm_output')
        write_text = llm_config.get('write_text', True)
        use_vision = llm_config.get('use_vision', False)
        screenshot_enabled = llm_config.get('screenshot_enabled', True)

        # Auto-detect concatenation: if this node has any inputs in the workflow graph,
        # treat them as "previous" source and enable direct RAG unless explicitly disabled.
        node_inputs = node.get('inputs', []) or []
        has_input_connection = bool(node_inputs)
        connected_ids = []
        try:
            connected_ids = [inp.get('from_node') for inp in node_inputs if isinstance(inp, dict) and inp.get('from_node')]
        except Exception:
            connected_ids = []
        has_previous_llm_output = any(f"node_{cid}_output" in self.variables for cid in connected_ids)

        configured_input_source = (llm_config.get('input_source', 'none') or 'none')
        input_source = configured_input_source
        if has_input_connection and has_previous_llm_output and configured_input_source.lower() in ('none', '', 'null'):
            input_source = 'previous'
            logger.info("Auto-enabled input_source='previous' due to detected input connection for concatenation")

        ocr_confidence = llm_config.get('ocr_confidence', 60)
        logger.info(f"Input source: {input_source}, OCR confidence: {ocr_confidence}")

        use_comorag = str(llm_config.get('use_comorag', 'true')).lower() in ('true', '1', 'yes', 'on')
        max_cycles = int(llm_config.get('max_cycles', 5))
        memory_size = int(llm_config.get('memory_size', 1000))

        use_direct_rag = str(llm_config.get('use_direct_rag', 'false')).lower() in ('true', '1', 'yes', 'on')
        if has_input_connection and has_previous_llm_output and not use_direct_rag and input_source in ('previous', 'previous_node'):
            use_direct_rag = True
            logger.info("Auto-enabled direct RAG (embeddings) for concatenated LLM node")
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

        logger.info(f"Creating OllamaClient with base_url: {api_url}")
        try:
            client = OllamaClient(base_url=api_url, debug=True, use_comorag=use_comorag)
            if use_comorag and hasattr(client, 'set_comorag_config'):
                client.set_comorag_config(max_cycles=max_cycles, memory_size=memory_size)
                logger.info(f"ComoRAG enabled with max_cycles={max_cycles}, memory_size={memory_size}")
            else:
                logger.info("Using regular chat endpoint (ComoRAG disabled)")
        except Exception as e:
            logger.error(f"Failed to create OllamaClient: {e}")
            return None

        rag_context_text = None
        if use_direct_rag and input_source in ('previous', 'previous_node') and input_text and input_text.strip():
            rag_context_text, updated_prompt = build_rag_context(
                client,
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
            )
            if updated_prompt:
                prompt = updated_prompt

        is_vision_model = client.is_vision_model(model)
        should_use_vision = use_vision or is_vision_model
        images, _ocr_text = prepare_vision_images(should_use_vision, screenshot_enabled, stop_flag)

        # Combine RAG context if available
        if rag_context_text:
            prompt = f"{rag_context_text}\n\n{prompt}"

        if stop_flag and stop_flag():
            logger.info("LLM node execution stopped by user request before API call")
            return None

        # Perform generation
        logger.info("Sending request to LLM API")
        try:
            if use_comorag:
                result = client.generate(
                    model=model,
                    prompt=prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    system=system_message or None,
                    images=images,
                    context=None,
                    async_request=False,
                )
            else:
                result = client.chat(
                    model=model,
                    prompt=prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    system=system_message or None,
                    images=images,
                )
        except Exception as e:
            logger.error(f"LLM request failed: {e}")
            return None

        # Normalize response
        response_text = None
        if isinstance(result, dict):
            response_text = (
                result.get('response')
                or result.get('text')
                or (result.get('message') or {}).get('content')
            )
        if not response_text:
            logger.warning(f"LLM returned unexpected format: {result}")
            response_text = ""

        # Store outputs
        try:
            self.variables[output_variable] = response_text
            node_id = llm_data.get('node_id') or llm_data.get('id') or node.get('id')
            if node_id:
                self.variables[f"node_{node_id}_output"] = response_text
        except Exception:
            pass

        # Optional: write text via handlers
        try:
            if write_text and action_handlers and hasattr(action_handlers, 'handle_type_string_action') and response_text:
                # Minimal integration: type the response where focus is
                logger.info("Writing LLM output via action handlers")
                action_handlers.handle_type_string_action(
                    idx=0,
                    action={"text": response_text, "use_clipboard": True},
                    stop_flag=stop_flag,
                )
        except Exception as e:
            logger.warning(f"Failed to write text via action handlers: {e}")

        # Determine next node
        next_node = llm_data.get('next_node') or node.get('next')
        if not next_node:
            try:
                connections = node.get('connections', {})
                output_conns = connections.get('output', [])
                if output_conns:
                    next_node = output_conns[0].get('node_id')
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
        
