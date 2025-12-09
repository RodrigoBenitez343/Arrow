#!/usr/bin/env python3
"""
ComoRAG Engine Module

Implements Cognitive-Inspired Memory-Organized RAG for stateful long narrative reasoning.
Based on the ComoRAG paper: https://arxiv.org/abs/2508.10419
"""

import asyncio
import logging
import json
import time
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict
from collections import deque
import numpy as np
from datetime import datetime

try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False
    SentenceTransformer = None

try:
    import faiss
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False
    faiss = None

logger = logging.getLogger(__name__)


@dataclass
class MemoryItem:
    """Individual memory item in the global memory pool"""
    id: str
    content: str
    embedding: Optional[np.ndarray]
    timestamp: float
    relevance_score: float = 0.0
    access_count: int = 0
    source: str = "unknown"  # query, retrieval, reasoning
    metadata: Dict[str, Any] = None
    image_data: Optional[str] = None  # Base64 encoded image data
    image_description: Optional[str] = None  # AI-generated description of the image
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class ReasoningCycle:
    """Represents one reasoning cycle in ComoRAG"""
    cycle_id: int
    query: str
    probing_queries: List[str]
    retrieved_evidence: List[MemoryItem]
    reasoning_output: str
    confidence_score: float
    timestamp: float


class DynamicMemoryWorkspace:
    """Dynamic memory workspace for ComoRAG reasoning"""
    
    def __init__(self, max_size: int = 1000, embedding_dim: int = 384):
        self.max_size = max_size
        self.embedding_dim = embedding_dim
        self.memory_pool: Dict[str, MemoryItem] = {}
        self.access_history = deque(maxlen=max_size)
        
        # Initialize FAISS index for similarity search
        if FAISS_AVAILABLE:
            self.index = faiss.IndexFlatIP(embedding_dim)  # Inner product for cosine similarity
            self.id_to_index: Dict[str, int] = {}
            self.index_to_id: Dict[int, str] = {}
        else:
            self.index = None
            logger.warning("FAISS not available. Memory search will be slower.")
    
    def add_memory(self, item: MemoryItem):
        """Add a memory item to the workspace"""
        # Remove oldest items if at capacity
        if len(self.memory_pool) >= self.max_size:
            self._evict_memory()
        
        # Add to memory pool
        self.memory_pool[item.id] = item
        self.access_history.append(item.id)
        
        # Add to FAISS index if available
        if self.index is not None and item.embedding is not None:
            index_pos = self.index.ntotal
            self.index.add(item.embedding.reshape(1, -1))
            self.id_to_index[item.id] = index_pos
            self.index_to_id[index_pos] = item.id
    
    def _evict_memory(self):
        """Evict least recently used memory items"""
        # Find least recently accessed items
        access_counts = {item_id: item.access_count for item_id, item in self.memory_pool.items()}
        sorted_items = sorted(access_counts.items(), key=lambda x: x[1])
        
        # Remove 10% of items
        num_to_remove = max(1, len(self.memory_pool) // 10)
        for i in range(num_to_remove):
            item_id = sorted_items[i][0]
            self._remove_memory_item(item_id)
    
    def _remove_memory_item(self, item_id: str):
        """Remove a specific memory item"""
        if item_id in self.memory_pool:
            del self.memory_pool[item_id]
        
        # Remove from FAISS index (note: FAISS doesn't support removal, so we rebuild periodically)
        if item_id in self.id_to_index:
            del self.id_to_index[item_id]
            # Note: We don't remove from index_to_id as FAISS indices are immutable
    
    def search_similar(self, query_embedding: np.ndarray, top_k: int = 5) -> List[MemoryItem]:
        """Search for similar memory items"""
        if self.index is None or self.index.ntotal == 0:
            # Fallback to simple similarity search
            return self._fallback_similarity_search(query_embedding, top_k)
        
        try:
            # Search using FAISS
            scores, indices = self.index.search(query_embedding.reshape(1, -1), min(top_k, self.index.ntotal))
            
            results = []
            for score, idx in zip(scores[0], indices[0]):
                if idx in self.index_to_id:
                    item_id = self.index_to_id[idx]
                    if item_id in self.memory_pool:
                        item = self.memory_pool[item_id]
                        item.relevance_score = float(score)
                        item.access_count += 1
                        results.append(item)
            
            return results
        except Exception as e:
            logger.error(f"FAISS search failed: {e}")
            return self._fallback_similarity_search(query_embedding, top_k)
    
    def _fallback_similarity_search(self, query_embedding: np.ndarray, top_k: int) -> List[MemoryItem]:
        """Fallback similarity search without FAISS"""
        similarities = []
        
        for item in self.memory_pool.values():
            if item.embedding is not None:
                # Cosine similarity
                similarity = np.dot(query_embedding, item.embedding) / (
                    np.linalg.norm(query_embedding) * np.linalg.norm(item.embedding)
                )
                similarities.append((similarity, item))
        
        # Sort by similarity and return top_k
        similarities.sort(key=lambda x: x[0], reverse=True)
        results = []
        for similarity, item in similarities[:top_k]:
            item.relevance_score = float(similarity)
            item.access_count += 1
            results.append(item)
        
        return results
    
    def get_recent_memories(self, source: str = None, item_type: str = None, limit: int = 5, seconds: float = 60.0) -> List[MemoryItem]:
        """Get recent memory items matching criteria"""
        now = time.time()
        candidates = []
        for item in self.memory_pool.values():
            if now - item.timestamp > seconds:
                continue
            if source and item.source != source:
                continue
            if item_type and item.metadata.get('type') != item_type:
                continue
            candidates.append(item)
        
        # Sort by timestamp desc
        candidates.sort(key=lambda x: x.timestamp, reverse=True)
        return candidates[:limit]

    def get_memory_state(self) -> Dict[str, Any]:
        """Get current memory workspace state"""
        return {
            "total_items": len(self.memory_pool),
            "max_size": self.max_size,
            "recent_access": list(self.access_history)[-10:],  # Last 10 accessed items
            "memory_items": [
                {
                    "id": item.id,
                    "content": item.content[:100] + "..." if len(item.content) > 100 else item.content,
                    "timestamp": item.timestamp,
                    "relevance_score": item.relevance_score,
                    "access_count": item.access_count,
                    "source": item.source
                }
                for item in list(self.memory_pool.values())[:20]  # First 20 items
            ]
        }
    
    def clear(self):
        """Clear all memory"""
        self.memory_pool.clear()
        self.access_history.clear()
        
        if self.index is not None:
            self.index.reset()
            self.id_to_index.clear()
            self.index_to_id.clear()


class ComoRAGEngine:
    """ComoRAG engine for cognitive-inspired reasoning"""
    
    def __init__(self, settings, ollama_engine):
        self.settings = settings
        self.ollama_engine = ollama_engine
        self.memory_workspace = None
        self.embedding_model = None
        self.reasoning_history: List[ReasoningCycle] = []
        self._initialized = False
        
        # ComoRAG parameters
        self.max_reasoning_cycles = 10
        self.confidence_threshold = 0.8
        self.memory_consolidation_threshold = 0.7
    
    async def initialize(self):
        """Initialize ComoRAG engine"""
        logger.info("Initializing ComoRAG engine...")
        
        if not SENTENCE_TRANSFORMERS_AVAILABLE:
            logger.warning("sentence-transformers not available. Install with: pip install sentence-transformers")
            # Use a simple fallback
            self.embedding_model = None
        else:
            # Load embedding model
            try:
                self.embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
                embedding_dim = self.embedding_model.get_sentence_embedding_dimension()
                logger.info(f"Loaded embedding model with dimension: {embedding_dim}")
            except Exception as e:
                logger.error(f"Failed to load embedding model: {e}")
                self.embedding_model = None
                embedding_dim = 384  # Default dimension
        
        # Initialize memory workspace
        memory_size = getattr(self.settings, 'comorag_memory_size', 1000)
        embedding_dim = 384 if self.embedding_model is None else self.embedding_model.get_sentence_embedding_dimension()
        self.memory_workspace = DynamicMemoryWorkspace(max_size=memory_size, embedding_dim=embedding_dim)
        
        self._initialized = True
        logger.info("ComoRAG engine initialized successfully")
    
    def _get_embedding(self, text: str) -> Optional[np.ndarray]:
        """Get embedding for text"""
        if self.embedding_model is None:
            return None
        
        try:
            embedding = self.embedding_model.encode(text, normalize_embeddings=True)
            return embedding
        except Exception as e:
            logger.error(f"Failed to get embedding: {e}")
            return None
    
    async def add_memory(self, content: str, source: str = "external", metadata: Dict[str, Any] = None, image_data: str = None) -> None:
        """Add a memory item to the workspace"""
        if not self.memory_workspace:
            return
        
        embedding = None
        if self.embedding_model:
            try:
                # Run embedding in thread pool to avoid blocking
                loop = asyncio.get_running_loop()
                embedding = await loop.run_in_executor(
                    None, 
                    lambda: self.embedding_model.encode(content)
                )
            except Exception:
                embedding = None
                
        memory_item = MemoryItem(
            id=f"{source}_{int(time.time() * 1000)}",
            content=content,
            embedding=embedding,
            timestamp=time.time(),
            source=source,
            metadata=metadata or {},
            image_data=image_data
        )
        self.memory_workspace.add_memory(memory_item)

    async def reason(
        self,
        query: str,
        context: Optional[str] = None,
        model: Optional[str] = None,
        max_cycles: int = 5,
        memory_size: Optional[int] = None,
        images: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Perform ComoRAG reasoning"""
        logger.info(f"Starting ComoRAG reasoning for query: {query[:100]}... (max_cycles={max_cycles})")
        
        try:
            if not self._initialized:
                logger.error("ComoRAG engine not initialized")
                raise RuntimeError("ComoRAG engine not initialized")
            
            # Use default model if not specified
            if model is None:
                logger.info("Model not specified, checking loaded models...")
                available_models = self.ollama_engine.get_loaded_models()
                if not available_models:
                    logger.error("No models loaded in Ollama engine")
                    raise RuntimeError("No models loaded in Ollama engine")
                model = available_models[0]
                logger.info(f"Selected model: {model}")
            
            # Adjust memory size if requested
            if memory_size and memory_size != self.memory_workspace.max_size:
                self.memory_workspace.max_size = memory_size
            
            # Initialize reasoning state
            reasoning_cycles = []
            current_query = query
            global_context = context or ""
            
            # Process images if provided and vision is enabled
            vision_context = ""
            if images and getattr(self.settings.comorag, 'enable_vision_analysis', False):
                logger.info("Processing images with vision analysis...")
                try:
                    vision_context = await self._analyze_images_with_vision(images, query, model)
                    if vision_context:
                        global_context = f"{global_context}\n\nVision Analysis:\n{vision_context}" if global_context else f"Vision Analysis:\n{vision_context}"
                        logger.info("Vision analysis completed successfully")
                    else:
                        logger.warning("Vision analysis returned empty result")
                except Exception as e:
                    logger.error(f"Vision analysis failed: {e}", exc_info=True)
            
            # Add initial query to memory
            logger.info("Adding initial query to memory...")
            query_embedding = self._get_embedding(query)
            initial_memory = MemoryItem(
                id=f"query_{int(time.time() * 1000)}",
                content=query,
                embedding=query_embedding,
                timestamp=time.time(),
                source="query",
                image_data=images[0] if images else None,
                image_description=vision_context if vision_context else None
            )
            self.memory_workspace.add_memory(initial_memory)
            
            # Reasoning loop
            logger.info(f"Entering reasoning loop with max_cycles={max_cycles}")
            for cycle in range(max_cycles):
                logger.info(f"ComoRAG reasoning cycle {cycle + 1}/{max_cycles}")
                
                try:
                    # Generate probing queries
                    logger.debug(f"Generating probing queries for cycle {cycle + 1}...")
                    probing_queries = await self._generate_probing_queries(
                        current_query, global_context, model, cycle
                    )
                    
                    if not probing_queries:
                        logger.warning(f"Cycle {cycle + 1}: No probing queries generated")
                        # Fallback to current query if no probing queries
                        probing_queries = [current_query]
                    
                    logger.debug(f"Generated {len(probing_queries)} probing queries")
                    
                    # Retrieve evidence for each probing query
                    retrieved_evidence = []
                    
                    # Force include recent vision data in the first cycle
                    if cycle == 0 and self.memory_workspace:
                        vision_items = self.memory_workspace.get_recent_memories(source="advanced_vision", limit=1, seconds=120.0)
                        if vision_items:
                            logger.info(f"Found {len(vision_items)} recent vision items")
                            retrieved_evidence.extend(vision_items)
                            # Mark as accessed
                            for item in vision_items:
                                item.access_count += 1
    
                    for probe_query in probing_queries:
                        evidence = await self._retrieve_evidence(probe_query)
                        retrieved_evidence.extend(evidence)
                    
                    # Deduplicate evidence
                    unique_evidence = {item.id: item for item in retrieved_evidence}.values()
                    retrieved_evidence = list(unique_evidence)
                    
                    if not retrieved_evidence:
                        logger.warning(f"Cycle {cycle + 1}: No evidence retrieved")
                    
                    logger.debug(f"Retrieved {len(retrieved_evidence)} total evidence items")
                    
                    # Consolidate evidence into global memory
                    logger.debug("Consolidating evidence...")
                    consolidated_context = await self._consolidate_evidence(
                        retrieved_evidence, global_context, model
                    )
                    
                    # Generate reasoning output
                    logger.debug("Generating reasoning output...")
                    reasoning_output = await self._generate_reasoning_output(
                        current_query, consolidated_context, model
                    )
                    
                    # Calculate confidence score
                    logger.debug("Calculating confidence score...")
                    confidence_score = await self._calculate_confidence(
                        reasoning_output, consolidated_context, model
                    )
                    logger.info(f"Cycle {cycle + 1} confidence: {confidence_score}")
                    
                    # Create reasoning cycle record
                    cycle_record = ReasoningCycle(
                        cycle_id=cycle,
                        query=current_query,
                        probing_queries=probing_queries,
                        retrieved_evidence=retrieved_evidence,
                        reasoning_output=reasoning_output,
                        confidence_score=confidence_score,
                        timestamp=time.time()
                    )
                    reasoning_cycles.append(cycle_record)
                    
                    # Check if we have sufficient confidence
                    if confidence_score >= self.confidence_threshold:
                        logger.info(f"Reasoning converged with confidence {confidence_score:.3f}")
                        break
                    
                    # Update context for next cycle
                    global_context = consolidated_context
                    
                    # Generate new exploratory query if needed
                    if cycle < max_cycles - 1:
                        current_query = await self._generate_exploratory_query(
                            query, reasoning_output, model
                        )
                    
                except Exception as e:
                    logger.error(f"Error in reasoning cycle {cycle}: {e}", exc_info=True)
                    # Don't break immediately, try to recover or at least save partial results
                    if not reasoning_cycles:
                        # If failed on first cycle, ensure we have something
                        reasoning_cycles.append(ReasoningCycle(
                            cycle_id=cycle,
                            query=current_query,
                            probing_queries=[current_query],
                            retrieved_evidence=[],
                            reasoning_output=f"Error in cycle {cycle}: {str(e)}",
                            confidence_score=0.0,
                            timestamp=time.time()
                        ))
                    break
            
            # Store reasoning history
            self.reasoning_history.extend(reasoning_cycles)
            
            # Prepare final response
            final_output = reasoning_cycles[-1].reasoning_output if reasoning_cycles else "No reasoning output generated"
            final_confidence = reasoning_cycles[-1].confidence_score if reasoning_cycles else 0.0
            
            logger.info(f"ComoRAG reasoning completed. Cycles: {len(reasoning_cycles)}, Final Confidence: {final_confidence}")
            
            return {
                "query": query,
                "response": final_output,
                "confidence": final_confidence,
                "reasoning_cycles": len(reasoning_cycles),
                "memory_items_used": len(set(item.id for cycle in reasoning_cycles for item in cycle.retrieved_evidence)),
                "reasoning_history": [
                    {
                        "cycle_id": cycle.cycle_id,
                        "probing_queries": cycle.probing_queries,
                        "confidence_score": cycle.confidence_score,
                        "evidence_count": len(cycle.retrieved_evidence)
                    }
                    for cycle in reasoning_cycles
                ]
            }
            
        except Exception as e:
            logger.critical(f"Critical error in ComoRAG reasoning: {e}", exc_info=True)
            # Return a fallback response so the client doesn't hang or crash
            return {
                "query": query,
                "response": f"ComoRAG failed (Critical Error): {str(e)}",
                "confidence": 0.0,
                "reasoning_cycles": 0,
                "memory_items_used": 0,
                "reasoning_history": []
            }
    
    async def _generate_probing_queries(
        self, query: str, context: str, model: str, cycle: int
    ) -> List[str]:
        """Generate probing queries for evidence retrieval"""
        prompt = f"""Given the main query and current context, generate 3-5 specific probing questions that would help gather relevant evidence for reasoning.

Main Query: {query}

Current Context: {context}

Cycle: {cycle + 1}

Generate probing questions that explore different aspects, potential contradictions, or missing information. Format as a JSON list of strings.

Probing Questions:"""
        
        try:
            response = await self.ollama_engine.generate(
                prompt=prompt,
                model=model,
                temperature=0.7,
                max_tokens=200
            )
            
            # Try to parse as JSON
            try:
                queries = json.loads(response["text"].strip())
                if isinstance(queries, list):
                    return [str(q) for q in queries[:5]]  # Limit to 5 queries
            except json.JSONDecodeError:
                pass
            
            # Fallback: split by lines
            lines = response["text"].strip().split('\n')
            queries = [line.strip('- ').strip() for line in lines if line.strip()]
            return queries[:5]
            
        except Exception as e:
            logger.error(f"Failed to generate probing queries: {e}")
            return [query]  # Fallback to original query
    
    async def _retrieve_evidence(self, query: str) -> List[MemoryItem]:
        """Retrieve evidence from memory workspace"""
        query_embedding = self._get_embedding(query)
        if query_embedding is None:
            return []
        
        # Search for similar items in memory
        similar_items = self.memory_workspace.search_similar(query_embedding, top_k=3)
        
        # Add query to memory for future reference
        query_memory = MemoryItem(
            id=f"probe_{int(time.time() * 1000)}_{hash(query) % 10000}",
            content=query,
            embedding=query_embedding,
            timestamp=time.time(),
            source="retrieval"
        )
        self.memory_workspace.add_memory(query_memory)
        
        return similar_items
    
    async def _consolidate_evidence(
        self, evidence: List[MemoryItem], context: str, model: str
    ) -> str:
        """Consolidate retrieved evidence into coherent context"""
        if not evidence:
            return context
        
        evidence_text = "\n".join([f"- {item.content}" for item in evidence])
        
        prompt = f"""Consolidate the following evidence with the existing context to create a coherent, comprehensive understanding.

Existing Context:
{context}

New Evidence:
{evidence_text}

Consolidated Context:"""
        
        try:
            response = await self.ollama_engine.generate(
                prompt=prompt,
                model=model,
                temperature=0.3,
                max_tokens=4096
            )
            
            consolidated = response["text"].strip()
            
            # Add consolidated context to memory
            consolidated_embedding = self._get_embedding(consolidated)
            memory_item = MemoryItem(
                id=f"consolidated_{int(time.time() * 1000)}",
                content=consolidated,
                embedding=consolidated_embedding,
                timestamp=time.time(),
                source="reasoning",
                metadata={"evidence_count": len(evidence)}
            )
            self.memory_workspace.add_memory(memory_item)
            
            return consolidated
            
        except Exception as e:
            logger.error(f"Failed to consolidate evidence: {e}")
            return context + "\n" + evidence_text
    
    async def _generate_reasoning_output(
        self, query: str, context: str, model: str
    ) -> str:
        """Generate reasoning output based on query and context"""
        prompt = f"""Based on the comprehensive context provided, provide a detailed and well-reasoned response to the query.

Query: {query}

Context: {context}

Reasoned Response:"""
        
        try:
            response = await self.ollama_engine.generate(
                prompt=prompt,
                model=model,
                temperature=0.5,
                max_tokens=4096
            )
            
            return response["text"].strip()
            
        except Exception as e:
            logger.error(f"Failed to generate reasoning output: {e}")
            return "Unable to generate reasoning output due to an error."
    
    async def _calculate_confidence(
        self, reasoning_output: str, context: str, model: str
    ) -> float:
        """Calculate confidence score for the reasoning output"""
        prompt = f"""Evaluate the confidence level of the following reasoning output based on the available context. Consider factors like:
- Completeness of the answer
- Consistency with the context
- Logical coherence
- Presence of uncertainties or gaps

Context: {context}

Reasoning Output: {reasoning_output}

Provide a confidence score between 0.0 and 1.0, where 1.0 means completely confident.

Confidence Score:"""
        
        try:
            response = await self.ollama_engine.generate(
                prompt=prompt,
                model=model,
                temperature=0.1,
                max_tokens=50
            )
            
            # Extract numeric score
            text = response["text"].strip()
            import re
            match = re.search(r'([0-9]*\.?[0-9]+)', text)
            if match:
                score = float(match.group(1))
                return min(1.0, max(0.0, score))  # Clamp to [0, 1]
            
            return 0.5  # Default moderate confidence
            
        except Exception as e:
            logger.error(f"Failed to calculate confidence: {e}")
            return 0.5
    
    async def _generate_exploratory_query(
        self, original_query: str, current_output: str, model: str
    ) -> str:
        """Generate new exploratory query for next reasoning cycle"""
        prompt = f"""Based on the original query and current reasoning output, generate a new exploratory question that would help deepen understanding or address remaining uncertainties.

Original Query: {original_query}

Current Output: {current_output}

New Exploratory Question:"""
        
        try:
            response = await self.ollama_engine.generate(
                prompt=prompt,
                model=model,
                temperature=0.8,
                max_tokens=100
            )
            
            return response["text"].strip()
            
        except Exception as e:
            logger.error(f"Failed to generate exploratory query: {e}")
            return original_query  # Fallback to original
    
    async def chat_with_reasoning(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 512
    ) -> Dict[str, Any]:
        """Chat completion with ComoRAG reasoning"""
        # Extract the last user message as the main query
        user_messages = [msg for msg in messages if msg.get("role") == "user"]
        if not user_messages:
            raise ValueError("No user messages found")
        
        query = user_messages[-1].get("content", "")
        
        # Build context from conversation history
        context_parts = []
        for msg in messages[:-1]:  # Exclude the last message (current query)
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            context_parts.append(f"{role.title()}: {content}")
        
        context = "\n".join(context_parts) if context_parts else None
        
        # Perform ComoRAG reasoning
        reasoning_result = await self.reason(
            query=query,
            context=context,
            model=model,
            max_cycles=3  # Shorter cycles for chat
        )
        
        # Format as chat completion response
        return {
            "id": f"comorag-{int(time.time() * 1000)}",
            "object": "chat.completion",
            "model": model,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": reasoning_result["response"]
                },
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": len(query.split()),  # Rough estimate
                "completion_tokens": len(reasoning_result["response"].split()),
                "total_tokens": len(query.split()) + len(reasoning_result["response"].split())
            },
            "comorag_metadata": {
                "confidence": reasoning_result["confidence"],
                "reasoning_cycles": reasoning_result["reasoning_cycles"],
                "memory_items_used": reasoning_result["memory_items_used"]
            }
        }
    
    async def get_memory_state(self) -> Dict[str, Any]:
        """Get current memory workspace state"""
        if not self.memory_workspace:
            return {"error": "Memory workspace not initialized"}
        
        return self.memory_workspace.get_memory_state()
    
    async def clear_memory(self):
        """Clear memory workspace"""
        if self.memory_workspace:
            self.memory_workspace.clear()
        self.reasoning_history.clear()
        logger.info("ComoRAG memory cleared")
    
    def is_ready(self) -> bool:
        """Check if ComoRAG engine is ready"""
        return self._initialized and self.memory_workspace is not None
    
    def get_memory_usage(self) -> Dict[str, Any]:
        """Get memory usage information"""
        if not self.memory_workspace:
            return {}
        
        return {
            "memory_items": len(self.memory_workspace.memory_pool),
            "max_memory_size": self.memory_workspace.max_size,
            "reasoning_history_length": len(self.reasoning_history),
            "embedding_model_loaded": self.embedding_model is not None
        }
    
    async def _analyze_images_with_vision(self, images: List[str], query: str, model: str) -> str:
        """Analyze images using vision model to provide context for reasoning"""
        try:
            # Use vision model if available, otherwise fall back to default model
            vision_model = getattr(self.settings.ollama, 'vision_model', model)
            
            # Prepare vision analysis prompt
            vision_prompt = f"""
Analyze the provided image(s) in the context of this query: "{query}"

Provide a detailed description of:
1. What you see in the image(s)
2. How the visual content relates to the query
3. Any relevant context that might help answer the query
4. Key elements, text, or interface components visible

Be specific and focus on details that could be relevant to understanding the current context and providing an appropriate response.
"""
            
            # Generate vision analysis using the vision model
            try:
                response = await self.ollama_engine.generate(
                    model=vision_model,
                    prompt=vision_prompt,
                    images=images,
                    temperature=0.3,  # Lower temperature for more consistent analysis
                    max_tokens=1024
                )
                
                if isinstance(response, dict) and 'text' in response:
                    vision_analysis = response['text']
                elif isinstance(response, dict) and 'response' in response:
                    vision_analysis = response['response']
                else:
                    vision_analysis = str(response)
                
                logger.info(f"Vision analysis completed for {len(images)} image(s)")
                return vision_analysis
                
            except Exception as e:
                logger.error(f"Error in vision model analysis: {e}")
                return f"Vision analysis failed: {str(e)}"
                
        except Exception as e:
            logger.error(f"Error in vision analysis setup: {e}")
            return ""
    
    async def cleanup(self):
        """Cleanup resources"""
        logger.info("Cleaning up ComoRAG engine...")
        
        if self.memory_workspace:
            self.memory_workspace.clear()
        
        self.reasoning_history.clear()
        self._initialized = False
        
        logger.info("ComoRAG engine cleanup complete")
