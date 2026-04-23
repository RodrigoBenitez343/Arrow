from ....config import logger
import json
import logging
import datetime
import os


class ContextMixin:
    def _execute_context_node(self, node, stop_flag, input_overrides=None):
        """
        Execute a Context node: collect inputs, apply merge, output accumulated context.
        
        Args:
            node (dict): The Context node from the workflow graph
            stop_flag (callable): Stop flag function
            input_overrides (any): Optional input data to inject (bypasses graph inputs)
            
        Returns:
            str: Next node ID to execute, or None if workflow should end
        """
        node_data = node.get('data', {})
        node_id = (
            node.get('id')
            or node.get('node_id')
            or node_data.get('node_id')
            or node_data.get('id')
        )
        
        # Get node properties
        scope = node_data.get('scope', 'chain')
        default_merge_policy = node_data.get('default_merge_policy', 'append')
        keys_json = node_data.get('keys', '[]')
        key_policies_json = node_data.get('key_policies', '{}')
        max_entries = int(node_data.get('max_entries_per_key', '100') or '100')
        raw_auto = node_data.get('auto_capture_responses', 'true')
        if isinstance(raw_auto, str):
            auto_capture = raw_auto.strip().lower() in ('true', '1', 'yes', 'on')
        else:
            auto_capture = bool(raw_auto)
        output_format = node_data.get('output_format', 'structured')
        
        # Parse keys and key_policies
        try:
            keys = json.loads(keys_json) if keys_json else []
        except (json.JSONDecodeError, TypeError):
            keys = []
        
        try:
            key_policies = json.loads(key_policies_json) if key_policies_json else {}
        except (json.JSONDecodeError, TypeError):
            key_policies = {}
        
        # Resolve scope_id - CRITICAL FIX: Use chain filename, not temp path
        if scope == 'run':
            scope_id = getattr(self, 'run_id', 'default_run')
        elif scope == 'chain':
            # NEW: Check if this context node is linked to a shared chain file
            shared_chain_file = node_data.get('shared_context_chain_file', '')
            shared_context_node_id = node_data.get('shared_context_node_id', '')  # NEW: Specific node ID
            
            if shared_chain_file and os.path.exists(shared_chain_file):
                # Use both the chain file AND the specific node ID to create a unique scope
                # This allows multiple context nodes from the same chain to have separate databases
                chain_basename = os.path.basename(shared_chain_file)
                if shared_context_node_id:
                    # Use the specific context node's ID for isolation
                    scope_id = f"{chain_basename}::{shared_context_node_id}"
                    logger.info(f"Context node {node_id}: Using shared context from {chain_basename}, node {shared_context_node_id}")
                else:
                    # Fallback: use just the chain file (legacy behavior)
                    scope_id = chain_basename
                    logger.info(f"Context node {node_id}: Using shared context from chain file: {shared_chain_file}")
            else:
                # Use the original chain filename if available, otherwise fall back to chain_id
                chain_id_raw = getattr(self, 'chain_id', None) or getattr(self, 'chain_file', 'default_chain')
                
                # If it's a temp file path, extract the original chain name from the workflow_graph
                # The workflow_graph should have metadata about the original chain
                if chain_id_raw and 'temp_chain' in str(chain_id_raw):
                    # This is a temp file - try to find the original chain name
                    # For now, use 'persistent_chain' as a universal scope for chain-scoped context
                    # This ensures context persists across runs of the same chain
                    scope_id = 'persistent_chain'
                    logger.info(f"Context node {node_id}: Detected temp chain file, using persistent scope_id")
                else:
                    # Use the chain_id as-is (should be the original file path)
                    scope_id = chain_id_raw
        else:  # global
            scope_id = None
        
        logger.info(f"Executing Context node: {node_id} (scope: {scope}, scope_id: {scope_id})")
        
        # Get or create ContextDatabase
        context_db = getattr(self, '_context_db', None)
        if context_db is None:
            from LoOper.AI.context_database import ContextDatabase
            self._context_db = ContextDatabase()
            context_db = self._context_db
        
        # Collect inputs
        inputs = node.get('inputs', [])
        for inp in inputs:
            from_node_id = inp.get('from_node')
            input_port = str(inp.get('input_port', ''))
            output_type = str(inp.get('output_type') or inp.get('output_port') or 'output')
            
            # Skip query port inputs (these are for querying, not storing)
            if input_port == 'query':
                continue
            
            # Retrieve value from previous node
            # Try 'context' first, fall back to 'output'
            if output_type == 'context':
                val = self.llm_executor.get_variable(f"node_{from_node_id}_context")
            else:
                val = self.llm_executor.get_variable(f"node_{from_node_id}_output")
                if val is None:
                    val = self.llm_executor.get_variable(f"node_{from_node_id}_context")
            
            if val is None:
                continue
            
            # Determine source type by looking up upstream node
            source_type = 'unknown'
            upstream_data = {}
            if hasattr(self, 'workflow_graph') and self.workflow_graph:
                upstream_node = self.workflow_graph.get(from_node_id, {})
                source_type = upstream_node.get('type', 'unknown')
                upstream_data = upstream_node.get('data', {})
            
            # Determine target key
            target_key = None
            
            # NEW: Detect input source type for LLM nodes to create source-specific keys
            input_source_type = None
            if source_type == 'llm':
                input_source_type = self.llm_executor.get_variable(f"node_{from_node_id}_input_source")
                if input_source_type:
                    input_source_type = str(input_source_type).strip().lower()
                    logger.info(f"Context node {node_id}: Detected input source type for {from_node_id}: '{input_source_type}'")
            
            # Check if there's a specific key policy for this input
            if from_node_id in key_policies:
                policy_entry = key_policies[from_node_id]
                if isinstance(policy_entry, dict):
                    target_key = policy_entry.get('key')
                elif isinstance(policy_entry, str):
                    target_key = policy_entry
            
            # Auto-assign key based on source_type AND input_source if not specified
            # Use upstream node's description as key if available
            if target_key is None:
                if source_type == 'llm':
                    # Build semantic description from LLM config
                    llm_config = upstream_data.get('llm_configuration', {}) or upstream_data
                    semantic_desc = llm_config.get('semantic_description', '')
                    
                    if not semantic_desc:
                        # Use output_variable if meaningful (not generic 'llm_output')
                        out_var = llm_config.get('output_variable', '')
                        if out_var and out_var != 'llm_output':
                            semantic_desc = out_var
                    
                    # NEW: Create source-specific keys based on input source type
                    if input_source_type and input_source_type in ('clipboard', 'ocr', 'screen_context'):
                        # Source-specific key with run counter for history tracking
                        # Format: llm_{node_id}_{source_type}_history
                        target_key = f'llm_{from_node_id}_{input_source_type}_history'
                        logger.info(f"Context node {node_id}: Using source-specific key '{target_key}' for input_source='{input_source_type}'")
                    elif input_source_type and input_source_type in ('previous', 'previous_node'):
                        # Previous node output - use generic key
                        target_key = semantic_desc if semantic_desc else f'node_{from_node_id}_response'
                    else:
                        # Final fallback: use the upstream node ID (guaranteed unique per node)
                        target_key = semantic_desc if semantic_desc else f'node_{from_node_id}_response'
                elif source_type == 'code':
                    # Use description as key if available, otherwise fall back to generic
                    code_key = upstream_data.get('description', '')
                    target_key = code_key if code_key else 'code_outputs'
                elif source_type == 'conditional':
                    target_key = 'conditions'
                elif source_type == 'context':
                    target_key = 'context_data'
                elif source_type == 'sequence':
                    target_key = 'sequence_results'
                elif source_type == 'tts':
                    target_key = 'tts_outputs'
                elif source_type == 'chain_import':
                    target_key = 'chain_results'
                else:
                    target_key = 'inputs'
            
            # Get merge policy for this key
            key_merge = key_policies.get(target_key, {}).get('merge_policy', default_merge_policy) if isinstance(key_policies.get(target_key), dict) else default_merge_policy
            
            # Build timeline entry for structured storage with explicit input source tagging
            # DEFINITIVE FIX: Read the RAW user input (clipboard, OCR, etc.) — NOT the assembled prompt.
            # Priority: 1) raw_user_input (captured early in executor.py), 2) source_input (legacy), 3) fallback
            actual_input = ""
            source_input_type = self.llm_executor.get_variable(f"node_{from_node_id}_input_source")
            
            if source_type == 'llm':
                # Primary: use the dedicated raw_user_input variable (captured BEFORE any processing)
                actual_input = self.llm_executor.get_variable(f"node_{from_node_id}_raw_user_input") or ""
                if not actual_input:
                    # Fallback: try the old source_input variable (for backward compatibility)
                    actual_input = self.llm_executor.get_variable(f"node_{from_node_id}_source_input") or ""
                    if actual_input:
                        logger.info(f"Context node {node_id}: Using legacy source_input for {from_node_id}")
                else:
                    logger.info(f"Context node {node_id}: Using raw_user_input for {from_node_id}")
                
                # DEBUG: Log what we retrieved to help diagnose issues
                input_preview = str(actual_input)[:200] if actual_input else "(empty)"
                logger.info(f"Context node {node_id}: Reading input from node {from_node_id}: type='{source_input_type}', content_preview='{input_preview}...'")
                
                if not actual_input and source_input_type and str(source_input_type).lower() in ('previous', 'previous_node'):
                    # For 'previous' source, the input IS the upstream node output
                    actual_input = val
                    logger.info(f"Context node {node_id}: Reconstructed actual_input from 'previous' source using upstream output")
            else:
                # For non-LLM nodes, use the output value directly
                actual_input = val if val else ""
                logger.info(f"Context node {node_id}: Non-LLM node {from_node_id}, using output as input_received")
            
            # NEW: Include explicit input_source metadata in timeline entry
            # This allows the LLM to clearly see what type of input was used in each run
            resolved_input_source = 'unknown'
            if source_type == 'llm' and source_input_type:
                resolved_input_source = str(source_input_type).strip().lower()
            elif source_type == 'llm' and input_source_type:
                resolved_input_source = input_source_type
            
            timeline_entry = {
                "timestamp": datetime.datetime.now().isoformat(),
                "input_source": resolved_input_source,  # NEW: Explicit source tagging (clipboard, ocr, previous_node, etc.)
                "input_received": str(actual_input)[:500] if actual_input else "",
                "output_generated": str(val)[:1000] if val else "",
                "run_id": getattr(self, 'run_id', 'unknown')
            }
            
            # Apply merge via context_db.put
            context_db.put(
                scope=scope,
                scope_id=scope_id,
                node_id=node_id,
                key=target_key,
                value=json.dumps(timeline_entry),
                source_node_id=from_node_id,
                source_type=source_type,
                merge_policy=key_merge,
                max_entries=max_entries
            )
            
            logger.debug(f"Context node {node_id}: stored input from {from_node_id} under key '{target_key}'")
        
        # Handle input_overrides if provided
        if input_overrides is not None:
            target_key = 'manual_inputs'
            key_merge = key_policies.get(target_key, {}).get('merge_policy', default_merge_policy) if isinstance(key_policies.get(target_key), dict) else default_merge_policy
            
            timeline_entry = {
                "timestamp": datetime.datetime.now().isoformat(),
                "input_received": "",
                "output_generated": str(input_overrides)[:1000] if input_overrides else "",
                "run_id": getattr(self, 'run_id', 'unknown')
            }
            
            context_db.put(
                scope=scope,
                scope_id=scope_id,
                node_id=node_id,
                key=target_key,
                value=json.dumps(timeline_entry),
                source_node_id='manual',
                source_type='manual',
                merge_policy=key_merge,
                max_entries=max_entries
            )
        
        # Build output - ALWAYS use intelligent retrieval, never dump entire DB
        exported = context_db.export_context(scope=scope, scope_id=scope_id, node_id=node_id)
        
        # Retrieval mode - ENHANCED: Always use smart retrieval with source filtering
        use_semantic = node_data.get('use_semantic_retrieval', False)
        if isinstance(use_semantic, str):
            use_semantic = use_semantic.strip().lower() in ('true', '1', 'yes', 'on')
        retrieval_top_k = int(node_data.get('retrieval_top_k', 3) or 3)
        
        if exported and exported.get('keys'):
            # CRITICAL FIX: Never dump entire context - always retrieve selectively
            # Step 1: Detect current input source to filter by same source type
            current_input_source = self._detect_current_input_source(node_id)
            
            # Step 2: Filter entries by input source type (source-aware retrieval)
            source_filtered_entries = self._filter_entries_by_source(exported, current_input_source)
            
            logger.info(f"Context node {node_id}: Source-aware retrieval - {len(source_filtered_entries)} entries match input_source='{current_input_source}' (from {sum(len(v) if isinstance(v, list) else 1 for v in exported.get('keys', {}).values())} total)")
            
            if not source_filtered_entries:
                # No matching source entries - fall back to all entries
                logger.warning(f"Context node {node_id}: No entries found for input_source='{current_input_source}', using all entries")
                source_filtered_entries = self._flatten_entries(exported)
            
            # Step 3: Apply semantic or chronological retrieval on filtered entries
            if use_semantic and len(source_filtered_entries) > 0:
                # Semantic retrieval: find most relevant entries by embedding similarity
                llm_prompt = self._find_downstream_llm_prompt(node_id)
                if llm_prompt:
                    retrieved = self._semantic_retrieve(source_filtered_entries, llm_prompt, top_k=retrieval_top_k)
                    output_value = self._format_timeline(retrieved, top_k=retrieval_top_k)
                    logger.info(f"Context node {node_id}: semantic retrieval returned {len(retrieved)} entries from {len(source_filtered_entries)} source-filtered")
                else:
                    # No prompt available - use chronological on filtered entries
                    output_value = self._format_timeline(source_filtered_entries, top_k=retrieval_top_k)
                    logger.info(f"Context node {node_id}: chronological retrieval (no prompt) on {len(source_filtered_entries)} source-filtered entries")
            else:
                # Chronological retrieval on source-filtered entries (default)
                output_value = self._format_timeline(source_filtered_entries, top_k=retrieval_top_k)
                logger.info(f"Context node {node_id}: chronological retrieval on {len(source_filtered_entries)} source-filtered entries")
        else:
            # No data available - return empty string instead of raw JSON
            output_value = ""
        
        # Set variables
        self.llm_executor.set_variable(f"node_{node_id}_context", output_value)
        self.llm_executor.set_variable(f"node_{node_id}_output", output_value)
        
        logger.info(f"Context node {node_id} executed successfully. Output format: {output_format}, semantic: {use_semantic}")
        
        # Return next node
        return self._get_context_next_node(node, 'output')
    
    def _detect_current_input_source(self, context_node_id):
        """Detect the input source type of the LLM node that will receive this context.
        
        This allows source-aware retrieval - only comparing against entries from the same source.
        
        Returns:
            str: Input source type ('clipboard', 'ocr', 'previous', etc.) or 'unknown'
        """
        if not hasattr(self, 'workflow_graph') or not self.workflow_graph:
            return 'unknown'
        
        # Find LLM nodes connected to this context node's output
        node = self.workflow_graph.get(context_node_id, {})
        connections = node.get('connections', {})
        
        for port_name, port_connections in connections.items():
            for conn in port_connections:
                target_id = conn.get('node_id')
                if not target_id:
                    continue
                target_node = self.workflow_graph.get(target_id, {})
                if target_node.get('type') == 'llm':
                    # Found downstream LLM node - check its input source
                    input_source = self.llm_executor.get_variable(f"node_{target_id}_input_source")
                    if input_source:
                        source_type = str(input_source).strip().lower()
                        logger.info(f"Context node {context_node_id}: Detected downstream LLM {target_id} input_source='{source_type}'")
                        return source_type
        
        return 'unknown'
    
    def _filter_entries_by_source(self, exported_data, target_source):
        """Filter context entries to only include those matching the target input source.
        
        This implements source-aware retrieval - comparing clipboard vs clipboard, OCR vs OCR, etc.
        
        Args:
            exported_data: Dict from context_db.export_context()
            target_source: Input source type to filter by ('clipboard', 'ocr', etc.)
            
        Returns:
            List of entries that match the target source (or all entries if target is 'unknown')
        """
        if target_source == 'unknown':
            # No source detected - return all entries
            return self._flatten_entries(exported_data)
        
        filtered = []
        for key, values in exported_data.get('keys', {}).items():
            # Source-specific keys contain the source type in the key name
            # e.g., 'llm_{node_id}_clipboard_history'
            if target_source in key.lower():
                # This key matches the target source
                if isinstance(values, list):
                    for entry in values:
                        # Verify entry also has matching input_source field
                        if isinstance(entry, dict) and entry.get('input_source', '').lower() == target_source:
                            filtered.append(entry)
                        elif isinstance(entry, str):
                            try:
                                parsed = json.loads(entry)
                                if isinstance(parsed, dict) and parsed.get('input_source', '').lower() == target_source:
                                    filtered.append(parsed)
                                else:
                                    # Legacy entry without input_source - include it
                                    filtered.append(parsed if isinstance(parsed, dict) else {'input_received': str(entry), 'input_source': 'unknown'})
                            except (json.JSONDecodeError, TypeError):
                                filtered.append({'input_received': str(entry), 'input_source': 'unknown'})
                        else:
                            filtered.append(entry)
                else:
                    # Single value (not a list)
                    if isinstance(values, dict) and values.get('input_source', '').lower() == target_source:
                        filtered.append(values)
                    else:
                        filtered.append(values)
        
        return filtered
    
    def _flatten_entries(self, exported_data):
        """Flatten exported context data into a list of individual entries."""
        flat = []
        for key, values in exported_data.get('keys', {}).items():
            if isinstance(values, list):
                for v in values:
                    if isinstance(v, dict):
                        flat.append(v)
                    elif isinstance(v, str):
                        try:
                            parsed = json.loads(v)
                            flat.append(parsed if isinstance(parsed, dict) else {'input_received': str(v), 'input_source': 'unknown'})
                        except (json.JSONDecodeError, TypeError):
                            flat.append({'input_received': str(v), 'input_source': 'unknown'})
                    else:
                        flat.append(v)
            else:
                if isinstance(values, dict):
                    flat.append(values)
                elif isinstance(values, str):
                    try:
                        parsed = json.loads(values)
                        flat.append(parsed if isinstance(parsed, dict) else {'input_received': str(values), 'input_source': 'unknown'})
                    except (json.JSONDecodeError, TypeError):
                        flat.append({'input_received': str(values), 'input_source': 'unknown'})
                else:
                    flat.append(values)
        return flat

    def _semantic_retrieve(self, all_entries, query_text, top_k=3):
        """Rank stored context entries by semantic similarity to query, return top-k."""
        if not query_text or not all_entries:
            return all_entries[:top_k] if all_entries else []
        
        try:
            from sentence_transformers import SentenceTransformer, util
            model = SentenceTransformer('all-MiniLM-L6-v2')
            
            entry_texts = [json.dumps(e) if not isinstance(e, str) else e for e in all_entries]
            query_emb = model.encode(query_text, convert_to_tensor=True)
            entry_embs = model.encode(entry_texts, convert_to_tensor=True)
            scores = util.cos_sim(query_emb, entry_embs)[0]
            
            ranked = sorted(enumerate(all_entries), key=lambda x: float(scores[x[0]]), reverse=True)
            return [entry for _, entry in ranked[:top_k]]
        except Exception as e:
            logging.warning(f"Semantic retrieval failed, returning recent entries: {e}")
            return all_entries[-top_k:]  # Fallback: most recent entries

    def _format_timeline(self, entries, top_k=3):
        """Format stored entries as a chronological timeline.
        
        SIMPLIFIED for small models - just show previous inputs, nothing else.
        
        Args:
            entries: List of stored entries (may be JSON strings, dicts, or plain text)
            top_k: Number of most recent entries to include
            
        Returns:
            Formatted timeline string
        """
        parsed = []
        for entry in entries:
            try:
                if isinstance(entry, str):
                    try:
                        parsed_entry = json.loads(entry)
                        if isinstance(parsed_entry, dict) and 'timestamp' in parsed_entry:
                            parsed.append(parsed_entry)
                        else:
                            # Legacy entry without timeline format
                            parsed.append({"timestamp": "", "input_received": str(entry)})
                    except (json.JSONDecodeError, TypeError):
                        # Not JSON, treat as plain text legacy entry
                        parsed.append({"timestamp": "", "input_received": str(entry)})
                elif isinstance(entry, dict) and 'timestamp' in entry:
                    parsed.append(entry)
                else:
                    parsed.append({"timestamp": "", "input_received": str(entry)})
            except Exception:
                parsed.append({"timestamp": "", "input_received": str(entry)})
        
        # Sort by timestamp (empty strings sort first = oldest), take most recent top_k
        parsed.sort(key=lambda x: x.get('timestamp', ''))
        recent = parsed[-top_k:] if len(parsed) > top_k else parsed
        
        # SIMPLIFIED: Just list the previous inputs, no fancy formatting
        lines = []
        for i, e in enumerate(recent, 1):
            inp = e.get('input_received', '')
            if inp:
                lines.append(f"Previous run #{i}: {inp}")
        
        if lines:
            return "\n".join(lines)
        return ""

    def _find_downstream_llm_prompt(self, context_node_id):
        """Find the prompt of the LLM node connected to this context node's output."""
        if not hasattr(self, 'workflow_graph') or not self.workflow_graph:
            return ''
        
        node = self.workflow_graph.get(context_node_id, {})
        connections = node.get('connections', {})
        
        # Context nodes use 'context' port for output connections
        for port_name, port_connections in connections.items():
            for conn in port_connections:
                target_id = conn.get('node_id')
                if not target_id:
                    continue
                target_node = self.workflow_graph.get(target_id, {})
                if target_node.get('type') == 'llm':
                    data = target_node.get('data', {})
                    prompt = data.get('prompt') or data.get('llm_configuration', {}).get('prompt', '')
                    if prompt:
                        return prompt
        return ''

    def _get_context_next_node(self, node, port):
        """Get the next node ID from context node connections."""
        connections = node.get('connections', {})
        if port in connections and connections[port]:
            return connections[port][0].get('node_id')
        if port == 'output':
            return "__done__"
        return None

    def _pre_initialize_context_nodes(self):
        """Pre-load stored context from DB for all context nodes at workflow start.
        
        This makes node_{id}_context variables available BEFORE any node executes,
        enabling feedback loops where LLM nodes read context that was stored in previous runs.
        """
        if not hasattr(self, 'workflow_graph') or not self.workflow_graph:
            return
        
        for node_id, node in self.workflow_graph.items():
            if node.get('type') != 'context':
                continue
            
            try:
                node_data = node.get('data', {})
                scope = node_data.get('scope', 'chain')
                
                # Resolve scope_id - CRITICAL FIX: Use chain filename, not temp path
                if scope == 'run':
                    scope_id = getattr(self, 'run_id', None) or 'default_run'
                elif scope == 'chain':
                    # NEW: Check if this context node is linked to a shared chain file
                    shared_chain_file = node_data.get('shared_context_chain_file', '')
                    shared_context_node_id = node_data.get('shared_context_node_id', '')  # NEW
                    
                    if shared_chain_file and os.path.exists(shared_chain_file):
                        # Use both the chain file AND the specific node ID
                        chain_basename = os.path.basename(shared_chain_file)
                        if shared_context_node_id:
                            scope_id = f"{chain_basename}::{shared_context_node_id}"
                        else:
                            scope_id = chain_basename
                    else:
                        # Use the original chain filename if available, otherwise fall back to chain_id
                        chain_id_raw = getattr(self, 'chain_id', None) or getattr(self, 'chain_file', 'default_chain')
                        
                        # If it's a temp file path, use persistent scope
                        if chain_id_raw and 'temp_chain' in str(chain_id_raw):
                            scope_id = 'persistent_chain'
                        else:
                            scope_id = chain_id_raw
                else:
                    scope_id = None
                
                # Import and get context DB
                from LoOper.AI.context_database import ContextDatabase
                context_db = ContextDatabase()
                
                # Export whatever is already stored from previous runs
                exported = context_db.export_context(
                    scope=scope,
                    scope_id=scope_id,
                    node_id=node_id
                )
                
                if exported and exported.get('keys'):
                    import json
                    output_format = node_data.get('output_format', 'structured')
                    
                    # Retrieval mode - ENHANCED: Always use source-aware retrieval
                    use_semantic = node_data.get('use_semantic_retrieval', False)
                    if isinstance(use_semantic, str):
                        use_semantic = use_semantic.strip().lower() in ('true', '1', 'yes', 'on')
                    retrieval_top_k = int(node_data.get('retrieval_top_k', 3) or 3)
                    
                    # Detect current input source for source-aware filtering
                    current_input_source = self._detect_current_input_source(node_id)
                    source_filtered_entries = self._filter_entries_by_source(exported, current_input_source)
                    
                    logger.info(f"Pre-init context node {node_id}: {len(source_filtered_entries)} entries match input_source='{current_input_source}'")
                    
                    if not source_filtered_entries:
                        logger.warning(f"Pre-init context node {node_id}: No entries for source='{current_input_source}', using all")
                        source_filtered_entries = self._flatten_entries(exported)
                    
                    # Apply semantic or chronological retrieval on filtered entries
                    if use_semantic and len(source_filtered_entries) > 0:
                        llm_prompt = self._find_downstream_llm_prompt(node_id)
                        if llm_prompt:
                            retrieved = self._semantic_retrieve(source_filtered_entries, llm_prompt, top_k=retrieval_top_k)
                            output_value = self._format_timeline(retrieved, top_k=retrieval_top_k)
                            logger.info(f"Pre-init context node {node_id}: semantic retrieval returned {len(retrieved)} from {len(source_filtered_entries)} source-filtered")
                        else:
                            output_value = self._format_timeline(source_filtered_entries, top_k=retrieval_top_k)
                            logger.info(f"Pre-init context node {node_id}: chronological (no prompt) on {len(source_filtered_entries)} source-filtered")
                    else:
                        # Chronological retrieval on source-filtered entries (default)
                        output_value = self._format_timeline(source_filtered_entries, top_k=retrieval_top_k)
                        logger.info(f"Pre-init context node {node_id}: chronological retrieval on {len(source_filtered_entries)} source-filtered entries")
                    
                    self.llm_executor.set_variable(f"node_{node_id}_context", output_value)
                    self.llm_executor.set_variable(f"node_{node_id}_output", output_value)
                    preview = output_value[:300] if output_value else "(empty)"
                    logger.info(
                        f"Pre-initialized context node {node_id}: {len(exported.get('keys', {}))} keys loaded from DB (semantic: {use_semantic}), value_preview='{preview}...'"
                    )
                else:
                    # Set empty context so LLM knows the connection exists but no data yet
                    self.llm_executor.set_variable(f"node_{node_id}_context", "")
                    self.llm_executor.set_variable(f"node_{node_id}_output", "")
                    logger.info(
                        f"Pre-initialized context node {node_id}: empty (first run or no stored data)"
                    )
            except Exception as e:
                logger.warning(f"Failed to pre-initialize context node {node_id}: {e}")
