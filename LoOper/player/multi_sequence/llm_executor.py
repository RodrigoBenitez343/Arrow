# llm_executor.py
"""
LLM node executor for multi-sequence automation.
Handles execution of LLM nodes in workflow graphs.
"""

import sys
import os
import time
from ..config import logger
from ..screenshot_cleanup import register_temporal_screenshot, cleanup_screenshot
import glob
import json


# Get default API URL from environment variables (same as consult.py)
def get_default_api_url():
    """Get default API URL using same logic as consult.py"""
    tailscale_ip = os.getenv("TAILSCALE_SERVER_IP", "localhost")
    port = os.getenv("API_PORT", "8000")
    return f"http://{tailscale_ip}:{port}"


class LLMExecutor:
    """Executes LLM nodes in workflow graphs"""
    
    def __init__(self, variables=None):
        self.variables = variables or {}
        self.context_collector = None
        self.context_db = None
        self._initialize_context_collector()
        self._initialize_context_database()
    
    def execute_llm_node(self, node, stop_flag, action_handlers=None):
        """
        Executes an LLM node and returns the next node ID.
        
        Args:
            node (dict): The LLM node from the workflow graph
            stop_flag (callable): Stop flag function
            action_handlers: Action handlers for text writing
            
        Returns:
            str: Next node ID to execute, or None if workflow should end
        """
        llm_data = node['data']
        logger.info(f"=== STARTING LLM NODE EXECUTION ===")
        logger.info(f"Executing LLM node: {llm_data.get('name', llm_data.get('id', llm_data.get('node_id', 'Unknown')))}")
        logger.info(f"Full node data: {node}")
        
        # Check stop flag at the beginning
        if stop_flag and stop_flag():
            logger.info("LLM node execution stopped by user request")
            return None
        
        try:
            # Add the project root to the path to import from AI module
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            if project_root not in sys.path:
                sys.path.append(project_root)
            
            from AI.consult import OllamaClient
            from AI.context_collector import ContextCollector
            
            # Check stop flag before configuration
            if stop_flag and stop_flag():
                logger.info("LLM node execution stopped by user request during configuration")
                return None
            
            # Get LLM configuration - handle both nested and direct formats
            llm_config = llm_data.get('llm_configuration', {})
            
            # If no nested configuration, use direct properties from llm_data
            if not llm_config:
                llm_config = llm_data
                
            logger.info(f"LLM configuration: {llm_config}")
            
            model = llm_config.get('model', 'llama3.2')
            api_url = llm_config.get('api_url', get_default_api_url())
            system_message = llm_config.get('system_message', '')
            prompt = llm_config.get('prompt', '')
            temperature = llm_config.get('temperature', 0.7)
            max_tokens = llm_config.get('max_tokens', 1000)
            output_variable = llm_config.get('output_variable', 'llm_output')
            write_text = llm_config.get('write_text', True)
            use_vision = llm_config.get('use_vision', False)
            screenshot_enabled = llm_config.get('screenshot_enabled', True)
            
            # New input source configuration
            input_source = llm_config.get('input_source', 'none')
            ocr_confidence = llm_config.get('ocr_confidence', 60)
            ocr_preprocessing = llm_config.get('ocr_preprocessing', False)
            
            logger.info(f"Input source: {input_source}, OCR confidence: {ocr_confidence}, OCR preprocessing: {ocr_preprocessing}")
            
            # ComoRAG-specific configuration
            use_comorag = str(llm_config.get('use_comorag', 'true')).lower() in ('true', '1', 'yes', 'on')
            max_cycles = int(llm_config.get('max_cycles', 5))
            memory_size = int(llm_config.get('memory_size', 1000))

            # Direct RAG configuration (embedding-based, simple and local)
            use_direct_rag = str(llm_config.get('use_direct_rag', 'false')).lower() in ('true', '1', 'yes', 'on')
            rag_embedding_model = llm_config.get('rag_embedding_model', 'nomic-embed-text')
            rag_chunk_size = int(llm_config.get('rag_chunk_size', 500))  # chars
            rag_overlap = int(llm_config.get('rag_overlap', 100))        # chars
            rag_top_k = int(llm_config.get('rag_top_k', 3))
            rag_include_raw_input = str(llm_config.get('rag_include_raw_input', 'false')).lower() in ('true', '1', 'yes', 'on')
            rag_max_chars = int(llm_config.get('rag_max_chars', 1500))
            
            logger.info(f"LLM Parameters:")
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
            
            # Check stop flag before variable substitution
            if stop_flag and stop_flag():
                logger.info("LLM node execution stopped by user request during variable substitution")
                return None
            
            # Replace context variables in prompt using previously stored variables
            original_prompt = prompt
            if self.variables:
                logger.info(f"Available variables for substitution: {self.variables}")
                for var_name, var_value in self.variables.items():
                    prompt = prompt.replace(f"{{{var_name}}}", str(var_value))
                logger.info(f"Prompt after variable substitution: {prompt}")
            else:
                logger.info("No variables available for substitution")
            
            # Check stop flag before input processing
            if stop_flag and stop_flag():
                logger.info("LLM node execution stopped by user request before input processing")
                return None
            
            # Handle input sources
            input_text = None
            # Flag to control whether raw input is injected into prompt
            use_input_text_in_prompt = True
            if input_source == 'ocr':
                logger.info("Processing OCR input source...")
                try:
                    from ..computer_vision import TemplateMatching
                    
                    # Check stop flag before screenshot
                    if stop_flag and stop_flag():
                        logger.info("LLM node execution stopped by user request during OCR screenshot")
                        return None
                    
                    # Take screenshot for OCR
                    import pyautogui
                    pyautogui.FAILSAFE = False
                    
                    screenshot = pyautogui.screenshot()
                    
                    # Create screenshots directory if it doesn't exist
                    screenshots_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'screenshots')
                    os.makedirs(screenshots_dir, exist_ok=True)
                    
                    # Save with timestamp
                    timestamp = int(time.time())
                    screenshot_path = os.path.join(screenshots_dir, f'llm_ocr_{timestamp}.png')
                    screenshot.save(screenshot_path)
                    
                    # Register for cleanup after use
                    register_temporal_screenshot(screenshot_path)
                    
                    # Extract all text with specified confidence
                    ocr_results = TemplateMatching.ocr_extract_all_text(min_confidence=ocr_confidence, stop_flag=stop_flag)
                    if ocr_results:
                        # Filter by confidence and join text
                        filtered_text = []
                        for item in ocr_results:
                            if item['confidence'] >= ocr_confidence:
                                filtered_text.append(item['text'])
                        
                        if filtered_text:
                            input_text = "\n".join(filtered_text)
                            logger.info(f"OCR extracted {len(filtered_text)} text blocks with confidence >= {ocr_confidence}")
                            logger.info(f"OCR text preview: {input_text[:200]}...")
                        else:
                            logger.warning(f"No OCR text found with confidence >= {ocr_confidence}")
                            input_text = ""
                    else:
                        logger.warning("No OCR text extracted")
                        input_text = ""
                        
                    # Clean up screenshot
                    cleanup_screenshot(screenshot_path)
                    
                except Exception as ocr_error:
                    logger.error(f"OCR processing failed: {ocr_error}")
                    input_text = ""
                    
            elif input_source in ('previous', 'previous_node'):
                logger.info("Processing previous node input source...")
                # Use workflow graph 'inputs' metadata rather than raw connections
                inputs_list = node.get('inputs', [])
                if inputs_list:
                    first_input = inputs_list[0]
                    connected_node_id = first_input.get('from_node')
                    if connected_node_id:
                        connected_output_var = f"node_{connected_node_id}_output"
                        if connected_output_var in self.variables:
                            input_text = self.variables[connected_output_var]
                            logger.info(f"Retrieved previous node output from {connected_output_var}: {input_text[:200]}...")
                        else:
                            # Fallback to common variable name if node-specific not found
                            input_text = self.variables.get('llm_output', '')
                            if input_text:
                                logger.warning(f"Node-specific output not found; using fallback 'llm_output'")
                            else:
                                logger.warning(f"No previous output available from connected node {connected_node_id}")
                                input_text = ""
                    else:
                        logger.warning("No connected node ID found in inputs")
                        input_text = ""
                else:
                    logger.warning("No workflow inputs found for node")
                    input_text = ""
                # If direct RAG is enabled, avoid injecting raw previous text
                if use_direct_rag:
                    use_input_text_in_prompt = False
                    
            elif input_source == 'clipboard':
                logger.info("Processing clipboard input source...")
                try:
                    import pyperclip
                    input_text = pyperclip.paste()
                    if input_text:
                        logger.info(f"Retrieved clipboard content: {input_text[:200]}...")
                    else:
                        logger.warning("Clipboard is empty")
                        input_text = ""
                except ImportError:
                    logger.error("pyperclip not installed. Install with: pip install pyperclip")
                    input_text = ""
                except Exception as clipboard_error:
                    logger.error(f"Failed to read clipboard: {clipboard_error}")
                    input_text = ""
            
            # If we have input text, enhance the prompt (unless direct RAG is enabled for previous input)
            if input_text is not None:
                if use_input_text_in_prompt:
                    if input_text.strip():
                        prompt = f"[Input Source: {input_source.upper()}]\n{input_text}\n\n[User Prompt:]\n{prompt}"
                        logger.info(f"Enhanced prompt with {input_source} input ({len(input_text)} chars)")
                    else:
                        prompt = f"[Input Source: {input_source.upper()} - No content found]\n\n[User Prompt:]\n{prompt}"
                        logger.info(f"Enhanced prompt with {input_source} input (no content)")
                else:
                    logger.info("Direct RAG enabled; skipping raw previous text injection into prompt")
            
            # Create OllamaClient and make request
            logger.info(f"Creating OllamaClient with base_url: {api_url}")
            try:
                # Disable ComoRAG by default to use regular chat endpoint
                # Use longer timeout for local model processing (5 minutes)
                client = OllamaClient(base_url=api_url, debug=True, use_comorag=False, timeout=300)
                
                # Set ComoRAG parameters if enabled (but we're disabling it for now)
                if use_comorag and hasattr(client, 'set_comorag_config'):
                    client.set_comorag_config(max_cycles=max_cycles, memory_size=memory_size)
                    logger.info(f"ComoRAG enabled with max_cycles={max_cycles}, memory_size={memory_size}")
                else:
                    logger.info("Using regular chat endpoint (ComoRAG disabled)")
                
                logger.info("OllamaClient created successfully")
            except Exception as client_error:
                logger.error(f"Failed to create OllamaClient: {client_error}")
                return None

            # Prepare optional RAG context from previous node output via embeddings
            rag_context_text = None
            if use_direct_rag and input_source in ('previous', 'previous_node') and input_text and input_text.strip():
                logger.info("Computing direct RAG context from previous node output using embeddings...")
                try:
                    # Helper: chunk text by characters with overlap
                    def _chunk_text_chars(text: str, size: int, overlap: int) -> List[str]:
                        chunks: List[str] = []
                        if size <= 0:
                            return [text]
                        step = max(1, size - max(0, overlap))
                        i = 0
                        while i < len(text):
                            chunk = text[i:i+size]
                            if chunk:
                                chunks.append(chunk)
                            i += step
                        return chunks

                    # Helper: cosine similarity
                    def _cosine_sim(a: List[float], b: List[float]) -> float:
                        try:
                            import math
                            dot = sum(x*y for x, y in zip(a, b))
                            na = math.sqrt(sum(x*x for x in a))
                            nb = math.sqrt(sum(y*y for y in b))
                            if na == 0.0 or nb == 0.0:
                                return 0.0
                            return dot / (na * nb)
                        except Exception:
                            return 0.0

                    # Chunk previous text
                    chunks = _chunk_text_chars(input_text, rag_chunk_size, rag_overlap)
                    logger.info(f"RAG: chunked previous output into {len(chunks)} chunks (size={rag_chunk_size}, overlap={rag_overlap})")

                    # Compute embeddings for chunks
                    chunk_embs = client.embeddings(rag_embedding_model, chunks)
                    if not chunk_embs or len(chunk_embs) != len(chunks):
                        logger.warning("RAG: embedding computation failed or mismatched; falling back to injecting raw previous text")
                        # If embeddings failed, revert to raw input injection behavior
                        if not use_input_text_in_prompt:
                            prompt = f"[Input Source: {input_source.upper()}]\n{input_text}\n\n[User Prompt:]\n{prompt}"
                        rag_context_text = None
                    else:
                        # Embed the current prompt as query
                        query_embs = client.embeddings(rag_embedding_model, [prompt])
                        if not query_embs or not query_embs[0]:
                            logger.warning("RAG: query embedding failed; reverting to raw input injection")
                            if not use_input_text_in_prompt:
                                prompt = f"[Input Source: {input_source.upper()}]\n{input_text}\n\n[User Prompt:]\n{prompt}"
                            rag_context_text = None
                        else:
                            q = query_embs[0]
                            sims = [(_cosine_sim(q, ce), idx) for idx, ce in enumerate(chunk_embs)]
                            sims.sort(key=lambda x: x[0], reverse=True)
                            top_indices = [idx for _, idx in sims[:max(1, rag_top_k)]]
                            top_texts = [chunks[i] for i in top_indices]
                            rag_context = "\n\n".join(top_texts)
                            if len(rag_context) > rag_max_chars:
                                rag_context = rag_context[:rag_max_chars]
                            rag_context_text = f"[RAG Context from previous node]:\n{rag_context}"
                            logger.info(f"RAG: selected top-{len(top_texts)} chunks for context (total chars={len(rag_context)})")
                            # Optionally include raw input as well if configured
                            if rag_include_raw_input:
                                rag_context_text = f"{rag_context_text}\n\n[Previous node raw output (truncated)]:\n{input_text[:min(len(input_text), rag_max_chars)]}"
                except Exception as rag_error:
                    logger.warning(f"Direct RAG computation failed: {rag_error}")
                    rag_context_text = None
            
            # Check if this is a vision model and handle screenshots
            images = None
            ocr_text = None
            is_vision_model = client.is_vision_model(model)
            should_use_vision = use_vision or is_vision_model
            
            if should_use_vision:
                logger.info(f"Vision model detected: {model}")
                if screenshot_enabled:
                    logger.info("Taking screenshot for vision analysis...")
                    try:
                        import pyautogui
                        
                        # Disable PyAutoGUI failsafe to prevent interruption when mouse moves to corner
                        pyautogui.FAILSAFE = False
                        
                        # Create screenshots directory if it doesn't exist
                        screenshots_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'screenshots')
                        os.makedirs(screenshots_dir, exist_ok=True)
                        
                        # Take screenshot
                        screenshot = pyautogui.screenshot()
                        
                        # Save with timestamp
                        timestamp = int(time.time())
                        screenshot_path = os.path.join(screenshots_dir, f'llm_vision_{timestamp}.png')
                        screenshot.save(screenshot_path)
                        
                        # Register for cleanup after use
                        register_temporal_screenshot(screenshot_path)
                        
                        images = [screenshot_path]
                        logger.info(f"Screenshot saved: {screenshot_path}")
                        
                        # Extract OCR text from screenshot for enhanced context
                        try:
                            from ..computer_vision import TemplateMatching
                            ocr_results = TemplateMatching.ocr_extract_all_text(min_confidence=0.6, stop_flag=stop_flag)
                            if ocr_results:
                                ocr_text = "\n".join([item['text'] for item in ocr_results if item['confidence'] > 60])
                                if ocr_text.strip():
                                    logger.info(f"Extracted OCR text ({len(ocr_text)} chars): {ocr_text[:200]}...")
                                    # Add OCR text to context for ComoRAG integration
                                    ocr_timestamp = time.time()
                                    ocr_context = {
                                        'ocr_text': ocr_text,
                                        'source': 'screen_capture',
                                        'type': 'ocr',
                                        'timestamp': ocr_timestamp
                                    }
                                    
                                    if hasattr(client, 'add_context'):
                                        client.add_context(ocr_context)
                                        logger.info(f"Added OCR text to ComoRAG context: {len(ocr_text)} characters")
                                    
                                    # Store OCR text in database for permanent storage
                                    if self.context_db:
                                        try:
                                            chain_id = node.get('chain_id') or node.get('data', {}).get('chain_id')
                                            sequence_id = node.get('sequence_id') or node.get('data', {}).get('sequence_id')
                                            node_id = node.get('id') or node.get('data', {}).get('id')
                                            
                                            # Add OCR text to database
                                            context_id = self.context_db.add_context_item(
                                                content=ocr_text,
                                                content_type="ocr_text",
                                                source="screen_capture",
                                                chain_id=chain_id,
                                                sequence_id=sequence_id,
                                                node_id=node_id,
                                                metadata={"screenshot_path": screenshot_path},
                                                timestamp=ocr_timestamp
                                            )
                                            logger.info(f"Stored OCR text in context database: context_id={context_id}")                                
                                        except Exception as db_error:
                                            logger.error(f"Failed to store OCR text in context database: {db_error}")
                                else:
                                    logger.info("No high-confidence OCR text extracted")
                            else:
                                logger.info("No OCR text extracted from screenshot")
                        except Exception as ocr_error:
                            logger.warning(f"OCR extraction failed: {ocr_error}")
                            ocr_text = None
                        
                        # Add screenshot context for ComoRAG integration and store in database
                        screenshot_timestamp = time.time()
                        screenshot_context = {
                            'screenshot_path': screenshot_path,
                            'description': f'Screen capture for vision analysis at {time.strftime("%Y-%m-%d %H:%M:%S")}',
                            'source': 'screen_capture',
                            'type': 'image',
                            'timestamp': screenshot_timestamp
                        }
                        
                        if hasattr(client, 'add_context'):
                            client.add_context(screenshot_context)
                            logger.info(f"Added screenshot to ComoRAG context: {screenshot_path}")
                        
                        # Store screenshot in database for permanent storage
                        if self.context_db:
                            try:
                                chain_id = node.get('chain_id') or node.get('data', {}).get('chain_id')
                                sequence_id = node.get('sequence_id') or node.get('data', {}).get('sequence_id')
                                node_id = node.get('id') or node.get('data', {}).get('id')
                                
                                # Add screenshot to database with metadata
                                context_id, image_id = self.context_db.add_context_with_image(
                                    content=f"Screenshot captured during LLM node execution: {node_id}",
                                    content_type="screenshot",
                                    source="llm_node",
                                    image_path=screenshot_path,
                                    chain_id=chain_id,
                                    sequence_id=sequence_id,
                                    node_id=node_id,
                                    metadata={"description": screenshot_context['description']},
                                    timestamp=screenshot_timestamp
                                )
                                logger.info(f"Stored screenshot in context database: context_id={context_id}, image_id={image_id}")                                
                            except Exception as db_error:
                                logger.error(f"Failed to store screenshot in context database: {db_error}")
                        
                    except Exception as screenshot_error:
                        logger.error(f"Failed to take screenshot: {screenshot_error}")
                        logger.warning("Continuing without screenshot")
                else:
                    logger.info("Screenshot disabled for vision model")
            else:
                logger.info(f"Text-only model: {model}")
            
            # Prepare messages (legacy code, not used in new API call)
            messages = []
            if system_message:
                messages.append({"role": "system", "content": system_message})
            if prompt:
                messages.append({"role": "user", "content": prompt})
            logger.info(f"Prepared messages (legacy): {messages}")

            # Collect minimal context data for ComoRAG integration (current context only)
            config = llm_config.get('comorag_config', {})
            context_data = self._collect_workflow_context(node, action_handlers, config=config)
            
            # Add only essential current workflow state context for ComoRAG
            workflow_timestamp = time.time()
            workflow_state = {
                'current_node': node.get('id', 'unknown'),
                'node_type': 'llm',
                'timestamp': workflow_timestamp
            }
            
            workflow_context = {
                'workflow_state': workflow_state,
                'source': 'current_execution',
                'type': 'state',
                'timestamp': workflow_timestamp
            }
            
            if hasattr(client, 'add_context'):
                client.add_context(workflow_context)
                logger.info(f"Added current workflow state to ComoRAG context")
                
            # Store workflow state in database for permanent storage
            if self.context_db:
                try:
                    chain_id = node.get('chain_id') or node.get('data', {}).get('chain_id')
                    sequence_id = node.get('sequence_id') or node.get('data', {}).get('sequence_id')
                    node_id = node.get('id') or node.get('data', {}).get('id')
                    
                    # Add workflow state to database
                    context_id = self.context_db.add_context_item(
                        content=json.dumps(workflow_state),
                        content_type="workflow_state",
                        source="current_execution",
                        chain_id=chain_id,
                        sequence_id=sequence_id,
                        node_id=node_id,
                        metadata={
                            "node_name": node.get('data', {}).get('name', 'unknown'),
                            "node_type": node.get('type', 'unknown')
                        },
                        timestamp=workflow_timestamp
                    )
                    logger.info(f"Stored workflow state in context database: context_id={context_id}")
                except Exception as db_error:
                    logger.error(f"Failed to store workflow state in context database: {db_error}")
            
            # Only add minimal context data (no historical data)
            if context_data and hasattr(client, 'add_context'):
                # Filter to only current execution context, skip historical data
                current_context_items = [item for item in context_data if item.get('type') == 'workflow_state']
                for context_item in current_context_items:
                    enhanced_context = dict(context_item)
                    if 'timestamp' not in enhanced_context:
                        enhanced_context['timestamp'] = time.time()
                    if 'source' not in enhanced_context:
                        enhanced_context['source'] = 'current_workflow'
                    client.add_context(enhanced_context)
                logger.info(f"Added {len(current_context_items)} current context items to ComoRAG client (historical data excluded)")
            
            # Current screenshot context is already handled above via OCR and vision analysis
            # No need to add additional screenshot context to avoid duplication
            logger.info("Current screenshot and OCR context handled via vision analysis and prompt enhancement")
            
            # Enhance prompt with optional RAG context and OCR text if available
            enhanced_prompt = prompt
            if rag_context_text:
                enhanced_prompt = f"{enhanced_prompt}\n\n{rag_context_text}"
                logger.info("Enhanced prompt with direct RAG context from previous node")
            if ocr_text and ocr_text.strip():
                enhanced_prompt = f"{prompt}\n\n[Current Screen Text (OCR):]\n{ocr_text}"
                logger.info(f"Enhanced prompt with OCR text ({len(ocr_text)} chars)")
            
            logger.info(f"Making LLM API request with {len(context_data)} context items...")
            logger.info(f"Request parameters: model={model}, prompt='{enhanced_prompt[:200]}...', system='{system_message}', temperature={temperature}, max_tokens={max_tokens}")
            
            # Check if async processing is enabled in the node configuration
            use_async = llm_config.get('use_async', False)
            logger.info(f"Async processing enabled: {use_async}")
            
            try:
                # Check stop flag before making API call
                if stop_flag and stop_flag():
                    logger.info("Stop flag detected before API call - aborting LLM execution")
                    return None
                
                # Test API connection first
                logger.info(f"Testing API connection to {api_url}...")
                try:
                    import requests
                    test_response = requests.get(f"{api_url}/health", timeout=5)
                    if test_response.status_code != 200:
                        logger.warning(f"API health check failed with status {test_response.status_code}")
                except requests.exceptions.RequestException as conn_error:
                    logger.error(f"API connection test failed: {conn_error}")
                    logger.error("The LLM API server is not running or not accessible.")
                    logger.error("Please ensure the main application is started properly to initialize the API server.")
                    
                    # Store error message in output variable
                    error_message = f"API connection failed: {str(conn_error)}. Please ensure the LLM API server is running."
                    self.variables[output_variable] = error_message
                    logger.error(f"Stored error message in variable '{output_variable}': {error_message}")
                    return None
                
                if use_async:
                    # Define callback function to handle async response
                    def async_response_callback(request_id, result):
                        logger.info(f"Received async response for request {request_id}")
                        # Store the result in a variable for later retrieval
                        self.variables[f"async_response_{request_id}"] = result
                        # Store the request ID for reference
                        self.variables[f"last_async_request_id"] = request_id
                        logger.info(f"Stored async response in variables: async_response_{request_id}")
                    
                    # Use generate method with async processing
                    queue_result = client.generate(
                        model=model,
                        prompt=enhanced_prompt,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        system=system_message if system_message else None,
                        images=images if images else None,
                        context=context_data if context_data else None,
                        async_request=True,
                        callback=async_response_callback
                    )
                    
                    # Check stop flag after async request queuing
                    if stop_flag and stop_flag():
                        logger.info("Stop flag detected after async request queuing - aborting LLM execution")
                        return None
                    
                    logger.info(f"Queued async request: {queue_result}")
                    
                    # Store the request ID in the output variable
                    if 'request_id' in queue_result:
                        request_id = queue_result['request_id']
                        self.variables[output_variable] = f"Async request queued with ID: {request_id}"
                        logger.info(f"Stored request ID in variable '{output_variable}': {request_id}")
                        
                        # Store request metadata in database
                        if self.context_db:
                            try:
                                chain_id = node.get('chain_id') or node.get('data', {}).get('chain_id')
                                sequence_id = node.get('sequence_id') or node.get('data', {}).get('sequence_id')
                                node_id = node.get('id') or node.get('data', {}).get('id')
                                
                                # Add async request info to database
                                context_id = self.context_db.add_context_item(
                                    content=f"Async LLM request queued with ID: {request_id}",
                                    content_type="async_request",
                                    source="llm_node",
                                    chain_id=chain_id,
                                    sequence_id=sequence_id,
                                    node_id=node_id,
                                    metadata={
                                        "request_id": request_id,
                                        "model": model,
                                        "status": queue_result.get('status', 'unknown'),
                                        "node_name": node.get('data', {}).get('name', 'unknown')
                                    },
                                    timestamp=time.time()
                                )
                                logger.info(f"Stored async request info in context database: context_id={context_id}")
                            except Exception as db_error:
                                logger.error(f"Failed to store async request info in context database: {db_error}")
                        
                        # For async requests, we return success immediately
                        # The actual response will be handled by the callback
                        response = queue_result
                    else:
                        # If no request_id, the request was processed synchronously due to queue being full
                        logger.info(f"Async request processed synchronously: {queue_result}")
                        response = queue_result
                else:
                    # Use generate method with context for ComoRAG integration (synchronous)
                    logger.info("Making synchronous API call with timeout handling...")
                    try:
                        response = client.generate(
                            model=model,
                            prompt=enhanced_prompt,
                            temperature=temperature,
                            max_tokens=max_tokens,
                            system=system_message if system_message else None,
                            images=images if images else None,
                            context=context_data if context_data else None
                        )
                    except Exception as sync_error:
                        logger.error(f"Synchronous API call failed: {sync_error}")
                        
                        # Check if it's a timeout error
                        if "timeout" in str(sync_error).lower() or "408" in str(sync_error):
                            error_message = f"API request timed out. The model may be taking too long to respond or the API server may be overloaded. Error: {str(sync_error)}"
                        else:
                            error_message = f"API request failed: {str(sync_error)}"
                        
                        self.variables[output_variable] = error_message
                        logger.error(f"Stored error message in variable '{output_variable}': {error_message}")
                        return None
                    
                    # Check stop flag after API call
                    if stop_flag and stop_flag():
                        logger.info("Stop flag detected after API call - aborting LLM execution")
                        return None
                    
                    logger.info(f"Raw API response: {response}")
                    logger.info(f"Response type: {type(response)}")
                
                # Clean up screenshot after API call
                if images:
                    for image_path in images:
                        cleanup_screenshot(image_path)
            except Exception as api_error:
                logger.error(f"API request failed: {api_error}")
                return None

            # Extract generated text from different possible response formats
            generated_text = None
            if response:
                # Check stop flag before processing response
                if stop_flag and stop_flag():
                    logger.info("Stop flag detected before processing response - aborting LLM execution")
                    return None
                
                logger.info("Processing API response...")
                if isinstance(response, dict):
                    logger.info(f"Response is dict with keys: {response.keys()}")
                    if 'response' in response:
                        generated_text = response['response']
                        logger.info(f"Extracted text from 'response' key: {generated_text}")
                    elif 'text' in response:
                        generated_text = response['text']
                        logger.info(f"Extracted text from 'text' key: {generated_text}")
                    elif 'message' in response and isinstance(response['message'], dict) and 'content' in response['message']:
                        generated_text = response['message']['content']
                        logger.info(f"Extracted text from 'message.content': {generated_text}")
                    elif 'choices' in response and isinstance(response['choices'], list) and len(response['choices']) > 0:
                        # Handle OpenAI-style response format
                        choice = response['choices'][0]
                        if isinstance(choice, dict) and 'message' in choice and 'content' in choice['message']:
                            generated_text = choice['message']['content']
                            logger.info(f"Extracted text from 'choices[0].message.content': {generated_text}")
                        elif isinstance(choice, dict) and 'text' in choice:
                            generated_text = choice['text']
                            logger.info(f"Extracted text from 'choices[0].text': {generated_text}")
                    else:
                        # Last resort: try to convert the entire response to a string
                        logger.warning(f"Could not find expected response format in: {response}")
                        try:
                            generated_text = str(response)
                            logger.info(f"Converted entire response to string: {generated_text[:100]}...")
                        except Exception as str_error:
                            logger.error(f"Failed to convert response to string: {str_error}")
                else:
                    # If response is not a dict, try to use it directly as a string
                    try:
                        generated_text = str(response)
                        logger.info(f"Response is not a dict, using as string: {type(response)} -> {generated_text[:100]}...")
                    except Exception as str_error:
                        logger.error(f"Failed to convert non-dict response to string: {str_error}")
            else:
                logger.error("No response received from API")

            if generated_text is not None:
                # Check stop flag before processing generated text
                if stop_flag and stop_flag():
                    logger.info("Stop flag detected before processing generated text - aborting LLM execution")
                    return None
                
                logger.info(f"=== LLM GENERATED TEXT ===")
                logger.info(f"Generated text length: {len(generated_text)}")
                # Log only first 100 characters to avoid terminal spam
                logger.info(f"Generated text (preview): {generated_text[:100]}{'...' if len(generated_text) > 100 else ''}")
                
                # Store the generated text in the variable system
                self.variables[output_variable] = generated_text
                logger.info(f"Stored LLM output in variable '{output_variable}': {generated_text}")
                logger.info(f"Current variables state: {self.variables}")
                
                # Also store output in a node-specific variable for chaining
                node_identifier = (
                    node.get('data', {}).get('node_id') or
                    node.get('data', {}).get('id') or
                    node.get('id') or
                    'unknown'
                )
                node_output_var = f"node_{node_identifier}_output"
                self.variables[node_output_var] = generated_text
                logger.info(f"Stored LLM output in node variable '{node_output_var}' for chaining")
                
                # Store LLM response in database for permanent storage
                if self.context_db:
                    try:
                        chain_id = node.get('chain_id') or node.get('data', {}).get('chain_id')
                        sequence_id = node.get('sequence_id') or node.get('data', {}).get('sequence_id')
                        node_id = node.get('id') or node.get('data', {}).get('id')
                        
                        # Add LLM response to database
                        context_id = self.context_db.add_context_item(
                            content=generated_text,
                            content_type="llm_response",
                            source="llm_node",
                            chain_id=chain_id,
                            sequence_id=sequence_id,
                            node_id=node_id,
                            metadata={
                                "model": model,
                                "prompt": prompt,
                                "system_message": system_message,
                                "temperature": temperature,
                                "max_tokens": max_tokens,
                                "use_comorag": use_comorag,
                                "node_name": node.get('data', {}).get('name', 'unknown')
                            },
                            timestamp=time.time()
                        )
                        logger.info(f"Stored LLM response in context database: context_id={context_id}")
                    except Exception as db_error:
                        logger.error(f"Failed to store LLM response in context database: {db_error}")

                # Check if LLM should write the text (default behavior for text models)
                should_write_text = write_text
                logger.info(f"Should write text: {should_write_text}")
                
                if should_write_text and generated_text and generated_text.strip():
                    # Check stop flag before writing text
                    if stop_flag and stop_flag():
                        logger.info("Stop flag detected before writing text - aborting LLM execution")
                        return None
                    
                    logger.info(f"=== ATTEMPTING TO WRITE TEXT ===")
                    
                    # Ensure generated_text is a string
                    if not isinstance(generated_text, str):
                        try:
                            generated_text = str(generated_text)
                            logger.warning(f"Converted non-string generated_text to string: {type(generated_text)}")
                        except Exception as str_error:
                            logger.error(f"Failed to convert generated_text to string: {str_error}")
                            generated_text = "Error: Could not convert LLM response to text"
                    
                    # Create a type_string action to write the generated text with optimized typing parameters
                    type_action = {
                        'type': 'type_string',
                        'text': generated_text,
                        'timestamp': time.time(),
                        # Use batch mode instead of character-by-character to prevent freezing
                        'char_by_char': False,  # Disable char-by-char to prevent app freeze
                        'char_delay': float(llm_config.get('char_delay', 0.01)),  # Minimal delay for faster typing
                        # Optimized batch parameters for better performance
                        'batch_size': int(llm_config.get('typing_batch_size', 50)),  # Larger batch size
                        'batch_delay': float(llm_config.get('typing_batch_delay', 0.1)),  # Shorter delay
                        # Add keystroke delay to prevent duplicate characters on slow machines
                        'keystroke_delay': float(llm_config.get('keystroke_delay', 0.01)),  # Default 10ms between keystrokes
                        # Allow forcing clipboard-based paste when needed (Unicode-heavy text)
                        'use_clipboard': bool(llm_config.get('use_clipboard', False))
                    }
                    logger.info(f"Created type_action with char_by_char=False, char_delay={type_action['char_delay']}")
                    logger.info(f"Batch parameters: batch_size={type_action['batch_size']}, batch_delay={type_action['batch_delay']}")
                    logger.info(f"Keystroke delay: {type_action['keystroke_delay']}s to prevent duplicate characters")
                    logger.info(f"Text length: {len(generated_text)} characters")
                    
                    # Check if action_handlers exists
                    if action_handlers:
                        logger.info(f"Action handlers available: {type(action_handlers)}")
                        try:
                            # Execute the typing action using action handlers with optimized batching
                            logger.info(f"Calling handle_type_string_action with text: '{generated_text[:50]}...'")
                            
                            # Typing parameters are already configured from LLM node settings
                            
                            # Execute typing action and get completion status
                            completed = action_handlers.handle_type_string_action(0, type_action, stop_flag)
                            
                            # Check completion flag
                            if completed:
                                logger.info(f"=== TEXT WRITING COMPLETED SUCCESSFULLY ===")
                            else:
                                logger.warning(f"Text writing was not completed successfully")
                                # We continue execution even if typing fails, as the variable is still set
                                
                        except Exception as write_error:
                            logger.error(f"Error during text writing: {write_error}")
                            import traceback
                            logger.error(f"Write error traceback: {traceback.format_exc()}")
                    else:
                        logger.error("No action_handlers available for text writing")
                else:
                    if not should_write_text:
                        logger.info("Text writing disabled by configuration")
                    elif not generated_text.strip():
                        logger.warning("Generated text is empty or whitespace only")
            else:
                logger.error("Failed to get valid response from LLM - no generated text")
                return None
                
        except Exception as e:
            logger.error(f"Error executing LLM node: {e}")
            import traceback
            logger.error(f"LLM node error traceback: {traceback.format_exc()}")
            return None
        
        # Find next node in the workflow
        connections = node['connections']
        if 'output' in connections and connections['output']:
            # Take the first connection
            next_connection = connections['output'][0]
            return next_connection['node_id']
        else:
            logger.info("No output connections found for LLM node, ending workflow")
            return None
    
    def get_variable(self, var_name):
        """Get a variable value"""
        return self.variables.get(var_name)
    
    def set_variable(self, var_name, value):
        """Set a variable value"""
        self.variables[var_name] = value
    
    def get_all_variables(self):
        """Get all variables"""
        return self.variables.copy()
    
    def execute_tts_standalone(self, tts_data, context_variables=None):
        """
        Execute TTS without GUI dependencies.
        
        Args:
            tts_data (dict): TTS configuration data
            context_variables (dict): Optional context variables for text substitution
            
        Returns:
            dict: Result dictionary with success status and message/error
        """
        logger.info(f"=== STARTING TTS STANDALONE EXECUTION ===")
        logger.info(f"TTS data: {tts_data}")
        logger.info(f"Context variables: {context_variables}")
        
        try:
            import pyttsx3
            
            # Extract TTS configuration from the data
            tts_config = tts_data.get('tts_configuration', {})
            
            # If no nested configuration, use direct properties from tts_data
            if not tts_config:
                tts_config = tts_data
            
            # Get TTS parameters with defaults
            text_to_speak = tts_config.get('text', 'Hello, this is a text to speech message.')
            language = tts_config.get('language', 'en')
            rate = tts_config.get('rate', 200)
            volume = tts_config.get('volume', 0.9)
            voice_id = tts_config.get('voice_id', 0)
            save_to_file = tts_config.get('save_to_file', False)
            output_file = tts_config.get('output_file', '')
            wait_for_completion = tts_config.get('wait_for_completion', True)
            
            logger.info(f"TTS Parameters:")
            logger.info(f"  Text: {text_to_speak}")
            logger.info(f"  Language: {language}")
            logger.info(f"  Rate: {rate}")
            logger.info(f"  Volume: {volume}")
            logger.info(f"  Voice ID: {voice_id}")
            logger.info(f"  Save to file: {save_to_file}")
            logger.info(f"  Output file: {output_file}")
            logger.info(f"  Wait for completion: {wait_for_completion}")
            
            # Replace context variables in text if provided
            if context_variables:
                logger.info(f"Replacing context variables in text")
                for var_name, var_value in context_variables.items():
                    text_to_speak = text_to_speak.replace(f'{{{var_name}}}', str(var_value))
                logger.info(f"Text after variable substitution: {text_to_speak}")
            
            # Also replace variables from the executor's stored variables
            if self.variables:
                logger.info(f"Replacing executor variables in text")
                for var_name, var_value in self.variables.items():
                    text_to_speak = text_to_speak.replace(f'{{{var_name}}}', str(var_value))
                logger.info(f"Text after executor variable substitution: {text_to_speak}")
            
            # Initialize TTS engine
            logger.info("Initializing TTS engine")
            engine = pyttsx3.init()
            
            # Configure TTS settings
            engine.setProperty('rate', rate)
            engine.setProperty('volume', volume)
            
            # Set voice if specified
            voices = engine.getProperty('voices')
            if voices and 0 <= voice_id < len(voices):
                engine.setProperty('voice', voices[voice_id].id)
                logger.info(f"Set voice to: {voices[voice_id].name}")
            else:
                logger.info(f"Using default voice (voice_id {voice_id} not available)")
            
            # Save to file if requested
            if save_to_file and output_file:
                logger.info(f"Saving TTS to file: {output_file}")
                engine.save_to_file(text_to_speak, output_file)
                if wait_for_completion:
                    engine.runAndWait()
                logger.info(f"TTS saved to file successfully")
                return {
                    'success': True,
                    'text': text_to_speak,
                    'saved_to_file': output_file,
                    'message': f'TTS saved to file: {output_file}'
                }
            else:
                # Speak the text
                logger.info(f"Speaking text: {text_to_speak}")
                engine.say(text_to_speak)
                if wait_for_completion:
                    engine.runAndWait()
                logger.info(f"TTS execution completed successfully")
                return {
                    'success': True,
                    'text': text_to_speak,
                    'message': f'TTS spoke: "{text_to_speak[:50]}..."' if len(text_to_speak) > 50 else f'TTS spoke: "{text_to_speak}"'
                }
                
        except ImportError as e:
            error_msg = 'pyttsx3 library not installed. Please install it using: pip install pyttsx3'
            logger.error(error_msg)
            return {
                'success': False,
                'error': error_msg
            }
        except Exception as e:
            error_msg = f'TTS execution failed: {str(e)}'
            logger.error(error_msg)
            return {
                'success': False,
                'error': error_msg
            }
    
    def _initialize_context_collector(self):
        """Initialize the context collector for ComoRAG integration"""
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            if project_root not in sys.path:
                sys.path.append(project_root)
            
            from AI.context_collector import ContextCollector
            self.context_collector = ContextCollector(workspace_path=project_root)
            logger.info("Context collector initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize context collector: {e}")
            self.context_collector = None
            
    def _initialize_context_database(self):
        """Initialize the context database for permanent storage"""
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            if project_root not in sys.path:
                sys.path.append(project_root)
            
            from AI.context_database import ContextDatabase
            self.context_db = ContextDatabase()
            logger.info("Context database initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize context database: {e}")
            self.context_db = None
    
    def _collect_workflow_context(self, node, action_handlers=None, config=None):
        """Collect context data for the current workflow state based on configuration
        
        Modified to limit context collection to only current execution context,
        avoiding loading all historical data from chains, screenshots, and sequences folders.
        """
        if not self.context_collector:
            return []
        
        # Default configuration - disabled historical data collection by default
        if config is None:
            config = {
                'collect_workflow_state': True,
                'collect_screenshots': False,  # Disabled: historical screenshots not needed
                'collect_automations': False,  # Disabled: historical automations not needed
                'max_context_items': 3,  # Reduced: only current context needed
                'screenshot_limit': 1,  # Only current screenshot if enabled
                'automation_limit': 0   # No historical automations
            }
        
        context_items = []
        max_items = config.get('max_context_items', 10)
        
        try:
            # Collect workflow state context if enabled
            if config.get('collect_workflow_state', True):
                workflow_data = {
                    "current_node": node.get('id', 'unknown'),
                    "node_type": node.get('type', 'unknown'),
                    "variables": self.variables.copy(),
                    "status": "executing",
                    "name": node.get('data', {}).get('name', 'unknown_workflow')
                }
                
                workflow_context = self.context_collector.collect_workflow_state_context(workflow_data)
                if workflow_context:
                    context_items.append(workflow_context)
            
            # Historical data collection disabled by default to avoid loading all context
            # Only collect if explicitly enabled in config (for backward compatibility)
            if config.get('collect_screenshots', False):
                logger.info("Historical screenshot collection is disabled by default for LLM nodes")
                logger.info("Current screenshot is captured separately and added via OCR and vision analysis")
            
            if config.get('collect_automations', False):
                logger.info("Historical automation data collection is disabled by default for LLM nodes")
                logger.info("LLM nodes now focus on current screen context and user query only")
                
            # Note: Current screenshot and OCR data are handled separately in execute_llm_node
            # This ensures LLM nodes only receive current context, not historical data
            
            # Limit total context items
            context_items = context_items[:max_items]
            
            logger.info(f"Collected {len(context_items)} context items for ComoRAG (historical data collection disabled)")
            return context_items
            
        except Exception as e:
            logger.error(f"Error collecting workflow context: {e}")
            return []