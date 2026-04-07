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

# Rich imports for enhanced visualization
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
    from rich.live import Live
    from rich.markdown import Markdown
    from rich.table import Table
    from rich.layout import Layout
    RICH_AVAILABLE = True
    console = Console()
except ImportError:
    RICH_AVAILABLE = False
    console = None

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
        self.internal_reasoning_model = "ministral-3:3b"

    def _log_step(self, message: str, style: str = "bold blue"):
        """Log a step with Rich if available, otherwise standard logging"""
        if RICH_AVAILABLE and console:
            console.print(f"[{style}]{message}[/{style}]")
        logger.info(message)

    async def initialize(self, memory_size: Optional[int] = None):
        """Initialize ComoRAG engine"""
        if self._initialized:
            logger.info("ComoRAG engine already initialized")
            return
            
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
        if memory_size is None:
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

    async def reason_stream(
        self,
        query: str,
        context: Optional[str] = None,
        model: Optional[str] = None,
        max_cycles: int = 5,
        memory_size: Optional[int] = None,
        images: Optional[List[str]] = None
    ):
        """Perform ComoRAG reasoning and stream steps"""
        self._log_step(f"🧠 Starting ComoRAG reasoning...", "bold magenta")
        yield {"type": "log", "message": "🧠 Starting ComoRAG reasoning...", "style": "bold magenta"}
        
        if RICH_AVAILABLE and console:
            console.print(Panel(Markdown(f"**Query:** {query}"), title="Input Query", border_style="blue"))

        try:
            if not self._initialized:
                logger.error("ComoRAG engine not initialized")
                raise RuntimeError("ComoRAG engine not initialized")
            
            # Use specified model for final output, but default internal reasoning to ministral-3:3b
            internal_model = self.internal_reasoning_model
            final_output_model = model

            if final_output_model is None:
                logger.info("Model not specified, checking loaded models...")
                available_models = self.ollama_engine.get_loaded_models()
                if not available_models:
                    logger.error("No models loaded in Ollama engine")
                    raise RuntimeError("No models loaded in Ollama engine")
                final_output_model = available_models[0]
                logger.info(f"Selected model: {final_output_model}")
            
            self._log_step(f"🤖 Internal Reasoning Model: {internal_model}", "cyan")
            yield {"type": "log", "message": f"🤖 Internal Reasoning Model: {internal_model}", "style": "cyan"}
            self._log_step(f"🗣️ Final Output Model: {final_output_model}", "cyan")
            yield {"type": "log", "message": f"🗣️ Final Output Model: {final_output_model}", "style": "cyan"}

            # Adjust memory size if requested
            if memory_size and memory_size != self.memory_workspace.max_size:
                self.memory_workspace.max_size = memory_size
            
            # Initialize reasoning state
            reasoning_cycles = []
            current_query = query
            global_context = context or ""
            
            # --- NEW: Chunk Massive JSON Context into Memory Pool ---
            # If the context is a massive JSON string, we should pre-process it into individual memory items
            # so the RAG retrieval can actually find specific elements without relying on the model's context window.
            if global_context.startswith("JSON_SCREEN_MAP:"):
                self._log_step("🗃️ Parsing structured JSON context into memory pool...", "dim")
                yield {"type": "log", "message": "🗃️ Parsing structured JSON context into memory pool...", "style": "dim"}
                try:
                    # Extract the JSON blob
                    json_str = global_context.split("JSON_SCREEN_MAP:\n")[1].split("\n\nFLATTENED_SCREEN_MAP:")[0]
                    screen_map = json.loads(json_str)
                    
                    # Add context items
                    for item in screen_map.get("context_items", []):
                        item_str = f"Context ID: {item.get('context_id')}, Type: {item.get('kind')}, Label: {item.get('label') or item.get('field_label') or 'N/A'}, Value: {item.get('value_text') or 'N/A'}, Coordinates: {item.get('coords')}"
                        self.memory_workspace.add_memory(MemoryItem(
                            id=f"ctx_{item.get('context_id')}_{int(time.time()*1000)}",
                            content=item_str,
                            embedding=self._get_embedding(item_str),
                            timestamp=time.time(),
                            source="structured_context"
                        ))
                        
                    # Add text blocks
                    for tb in screen_map.get("text_blocks_sample", []):
                        tb_str = f"Text Block [{tb.get('block_id')}]: '{tb.get('text')}', Coordinates: {tb.get('coords')}"
                        self.memory_workspace.add_memory(MemoryItem(
                            id=f"tb_{tb.get('block_id')}_{int(time.time()*1000)}",
                            content=tb_str,
                            embedding=self._get_embedding(tb_str),
                            timestamp=time.time(),
                            source="structured_context"
                        ))
                        
                    self._log_step(f"✅ Added {len(screen_map.get('context_items', [])) + len(screen_map.get('text_blocks_sample', []))} items to memory pool.", "green")
                except Exception as e:
                    logger.warning(f"Failed to parse structured context into memory: {e}")
            # --------------------------------------------------------

            # Process images if provided and vision is enabled
            vision_context = ""
            if images and getattr(self.settings.comorag, 'enable_vision_analysis', False):
                self._log_step("👁️ Processing images with vision analysis...", "yellow")
                yield {"type": "log", "message": "👁️ Processing images with vision analysis...", "style": "yellow"}
                try:
                    vision_context = await self._analyze_images_with_vision(images, query, final_output_model)
                    if vision_context:
                        global_context = f"{global_context}\n\nVision Analysis:\n{vision_context}" if global_context else f"Vision Analysis:\n{vision_context}"
                        self._log_step("✅ Vision analysis completed", "green")
                        yield {"type": "log", "message": "✅ Vision analysis completed", "style": "green"}
                        yield {"type": "vision_analysis", "content": vision_context}
                    else:
                        logger.warning("Vision analysis returned empty result")
                except Exception as e:
                    logger.error(f"Vision analysis failed: {e}", exc_info=True)
            
            # Add initial query to memory
            self._log_step("💾 Adding initial query to memory...", "dim")
            yield {"type": "log", "message": "💾 Adding initial query to memory...", "style": "dim"}
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
            self._log_step(f"🔄 Entering reasoning loop (Max Cycles: {max_cycles})", "bold green")
            yield {"type": "log", "message": f"🔄 Entering reasoning loop (Max Cycles: {max_cycles})", "style": "bold green"}
            
            # Use Rich Live display for the reasoning process
            with Live(console=console, refresh_per_second=4) if RICH_AVAILABLE else range(1) as live_display:
                for cycle in range(max_cycles):
                    cycle_status = f"Cycle {cycle + 1}/{max_cycles}"
                    self._log_step(f"\n--- {cycle_status} ---", "bold white on blue")
                    yield {"type": "cycle_start", "cycle": cycle + 1, "total": max_cycles}
                    
                    try:
                        # Generate probing queries
                        self._log_step("❓ Generating probing queries...", "cyan")
                        yield {"type": "log", "message": "❓ Generating probing queries...", "style": "cyan"}
                        probing_queries = await self._generate_probing_queries(
                            current_query, global_context, internal_model, cycle
                        )
                        
                        if not probing_queries:
                            logger.warning(f"Cycle {cycle + 1}: No probing queries generated")
                            probing_queries = [current_query]
                        
                        yield {"type": "probing_queries", "queries": probing_queries}
                        
                        if RICH_AVAILABLE and console:
                            console.print(Panel("\n".join([f"- {q}" for q in probing_queries]), title="Probing Queries", border_style="cyan"))
                        
                        # Retrieve evidence for each probing query
                        retrieved_evidence = []
                        
                        # Force include recent vision data in the first cycle
                        if cycle == 0 and self.memory_workspace:
                            vision_items = self.memory_workspace.get_recent_memories(source="advanced_vision", limit=1, seconds=120.0)
                            if vision_items:
                                self._log_step(f"👁️ Found {len(vision_items)} recent vision items", "green")
                                yield {"type": "log", "message": f"👁️ Found {len(vision_items)} recent vision items", "style": "green"}
                                retrieved_evidence.extend(vision_items)
                                for item in vision_items:
                                    item.access_count += 1
        
                        if RICH_AVAILABLE:
                            with Progress(
                                SpinnerColumn(),
                                TextColumn("[progress.description]{task.description}"),
                                BarColumn(),
                                console=console,
                                transient=True
                            ) as progress:
                                task = progress.add_task(f"🔎 Retrieving evidence for {len(probing_queries)} queries...", total=len(probing_queries))
                                for probe_query in probing_queries:
                                    evidence = await self._retrieve_evidence(probe_query)
                                    retrieved_evidence.extend(evidence)
                                    progress.advance(task)
                        else:
                            for probe_query in probing_queries:
                                evidence = await self._retrieve_evidence(probe_query)
                                retrieved_evidence.extend(evidence)
                        
                        # Deduplicate evidence
                        unique_evidence = {item.id: item for item in retrieved_evidence}.values()
                        retrieved_evidence = list(unique_evidence)
                        
                        if not retrieved_evidence:
                            self._log_step("⚠️ No evidence retrieved", "yellow")
                            yield {"type": "log", "message": "⚠️ No evidence retrieved", "style": "yellow"}
                        else:
                            self._log_step(f"📄 Retrieved {len(retrieved_evidence)} evidence items", "green")
                            yield {"type": "log", "message": f"📄 Retrieved {len(retrieved_evidence)} evidence items", "style": "green"}
                            yield {"type": "evidence", "count": len(retrieved_evidence), "items": [item.content for item in retrieved_evidence]}
                        
                        # Consolidate evidence into global memory
                        self._log_step("🧩 Consolidating evidence...", "magenta")
                        yield {"type": "log", "message": "🧩 Consolidating evidence...", "style": "magenta"}
                        consolidated_context = await self._consolidate_evidence(
                            retrieved_evidence, global_context, internal_model
                        )
                        
                        # Generate reasoning output
                        self._log_step("🤔 Generating reasoning output...", "yellow")
                        yield {"type": "log", "message": "🤔 Generating reasoning output...", "style": "yellow"}
                        reasoning_output = await self._generate_reasoning_output(
                            current_query, consolidated_context, internal_model
                        )
                        
                        yield {"type": "reasoning_output", "output": reasoning_output}
                        
                        if RICH_AVAILABLE and console:
                            console.print(Panel(Markdown(reasoning_output), title="Internal Reasoning", border_style="yellow"))
                        
                        # Calculate confidence score
                        self._log_step("⚖️ Calculating confidence score...", "blue")
                        yield {"type": "log", "message": "⚖️ Calculating confidence score...", "style": "blue"}
                        confidence_score = await self._calculate_confidence(
                            reasoning_output, consolidated_context, internal_model
                        )
                        self._log_step(f"📊 Confidence: {confidence_score:.3f}", "bold green" if confidence_score >= self.confidence_threshold else "bold red")
                        yield {"type": "confidence", "score": confidence_score}
                        
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
                            self._log_step(f"✨ Reasoning converged! (Score: {confidence_score:.3f} >= {self.confidence_threshold})", "bold green blink")
                            yield {"type": "log", "message": f"✨ Reasoning converged! (Score: {confidence_score:.3f} >= {self.confidence_threshold})", "style": "bold green blink"}
                            break
                        
                        # Update context for next cycle
                        global_context = consolidated_context
                        
                        # Generate new exploratory query if needed
                        if cycle < max_cycles - 1:
                            self._log_step("🔭 Generating exploratory query...", "cyan")
                            yield {"type": "log", "message": "🔭 Generating exploratory query...", "style": "cyan"}
                            current_query = await self._generate_exploratory_query(
                                query, reasoning_output, internal_model
                            )
                            self._log_step(f"Next Query: {current_query}", "italic cyan")
                            yield {"type": "next_query", "query": current_query}
                        
                    except Exception as e:
                        logger.error(f"Error in reasoning cycle {cycle}: {e}", exc_info=True)
                        self._log_step(f"❌ Error in cycle {cycle}: {e}", "bold red")
                        yield {"type": "error", "message": f"Error in cycle {cycle}: {e}"}
                        if not reasoning_cycles:
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
            self._log_step("🏁 Finalizing response iteratively...", "bold magenta")
            yield {"type": "log", "message": "🏁 Finalizing response iteratively...", "style": "bold magenta"}
            
            final_context = global_context

            # Generate the final response using the PRIMARY model, passing the cycles
            final_response = await self._generate_final_response(query, final_context, final_output_model, reasoning_cycles)
            
            final_confidence = reasoning_cycles[-1].confidence_score if reasoning_cycles else 0.0
            
            self._log_step(f"✅ ComoRAG reasoning completed. Cycles: {len(reasoning_cycles)}", "bold green")
            yield {"type": "log", "message": f"✅ ComoRAG reasoning completed. Cycles: {len(reasoning_cycles)}", "style": "bold green"}
            
            if RICH_AVAILABLE and console:
                console.print(Panel(Markdown(final_response), title="Final Response", border_style="green"))
            
            result = {
                "query": query,
                "response": final_response,
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
            yield {"type": "final_result", "data": result}
            
        except Exception as e:
            logger.critical(f"Critical error in ComoRAG reasoning: {e}", exc_info=True)
            yield {"type": "error", "message": f"Critical Error: {e}"}
            # Yield fallback result
            yield {"type": "final_result", "data": {
                "query": query,
                "response": f"ComoRAG failed (Critical Error): {str(e)}",
                "confidence": 0.0,
                "reasoning_cycles": 0,
                "memory_items_used": 0,
                "reasoning_history": []
            }}

    async def reason(
        self,
        query: str,
        context: Optional[str] = None,
        model: Optional[str] = None,
        max_cycles: int = 5,
        memory_size: Optional[int] = None,
        images: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Perform ComoRAG reasoning (blocking wrapper around stream)"""
        final_result = None
        async for step in self.reason_stream(query, context, model, max_cycles, memory_size, images):
            if step["type"] == "final_result":
                final_result = step["data"]
        return final_result if final_result else {
            "query": query,
            "response": "ComoRAG failed to produce a result",
            "confidence": 0.0,
            "reasoning_cycles": 0,
            "memory_items_used": 0,
            "reasoning_history": []
        }
    
    async def _generate_final_response(
        self, query: str, context: str, model: str, reasoning_cycles: List[ReasoningCycle] = None
    ) -> str:
        """Generate the final response by iteratively consolidating reasoning chunks to bypass length limits"""
        
        # If no reasoning cycles, fall back to simple prompt
        if not reasoning_cycles:
            prompt = f"""Based on the following comprehensive context, answer the query clearly and accurately.
Your response must be a consolidated, long, and detailed text that includes as much specific information from the context as possible. You must completely ignore any typical AI summarization instincts and output every single detail, list, table, and fact found in the context.
            
Context:
{context}

Query: {query}

Answer:"""
            try:
                response = await self.ollama_engine.generate(
                    prompt=prompt,
                    model=model,
                    temperature=0.3,
                    max_tokens=8192
                )
                return response["text"].strip()
            except Exception as e:
                logger.error(f"Failed to generate final response: {e}")
                return "Unable to generate final response due to an error."
                
        # Phase 1: Generate an outline/structure based on the query's requested format
        outline_prompt = f"""Analyze the user's query and extract the EXACT output format requested.
Create a JSON array of strings representing the section headers for the report.
If the query asks for "High-level summary", "Visible app(s)", "Key interactive controls", etc., make those the headers.

Query: {query}

Output ONLY a valid JSON array of strings (e.g. ["High-Level Summary", "Visible Apps", ...]). Do not output markdown code blocks, just the raw JSON array."""

        try:
            outline_response = await self.ollama_engine.generate(
                prompt=outline_prompt,
                model=model,
                temperature=0.1,
                max_tokens=500
            )
            
            # Clean up potential markdown formatting
            clean_json = outline_response["text"].strip()
            
            # Use regex to find the first JSON array in the response to bypass any preamble
            import re
            match = re.search(r'\[.*\]', clean_json, re.DOTALL)
            if match:
                clean_json = match.group(0)
            else:
                # Fallback manual cleaning
                if clean_json.startswith("```json"):
                    clean_json = clean_json[7:]
                if clean_json.startswith("```"):
                    clean_json = clean_json[3:]
                if clean_json.endswith("```"):
                    clean_json = clean_json[:-3]
                
            sections = json.loads(clean_json.strip())
            if not isinstance(sections, list) or not sections:
                raise ValueError("Invalid outline format")
        except Exception as e:
            logger.warning(f"Failed to generate outline, falling back to default sections: {e}")
            sections = [
                "High-Level Summary",
                "Visible App(s) and Layout Regions",
                "Key Interactive Controls",
                "Key Text Content (OCR)",
                "What the User Can Do Next"
            ]

        # Phase 2: Recursively populate each section using smaller chunks of evidence to avoid context overflow
        accumulated_response = ""
        
        # Determine a safe context snippet length (lower than before to leave room for evidence)
        # 3000 chars allows the JSON structure to be partially visible without blowing up the context window
        safe_context_snippet = context[:3000]
        
        for i, section in enumerate(sections):
            # We will build the section text iteratively if there are many reasoning cycles
            section_text = ""
            
            # Chunk the reasoning cycles into batches of 1 to ensure prompt size stays small
            chunk_size = 1
            cycle_chunks = [reasoning_cycles[i:i + chunk_size] for i in range(0, len(reasoning_cycles), chunk_size)]
            
            # If no reasoning cycles, we just do one pass
            if not cycle_chunks:
                cycle_chunks = [[]]
                
            for chunk_idx, chunk in enumerate(cycle_chunks):
                chunk_evidence = "\n\n".join([f"Cycle {c.cycle_id+1} Discovery:\n{c.reasoning_output}" for c in chunk])
                
                if chunk_idx == 0:
                    section_prompt = f"""You are generating a highly detailed, comprehensive report section by section.
Your task is to write ONLY the section titled: "{section}".

CRITICAL INSTRUCTIONS:
- DO NOT output any meta-commentary, introductory remarks, or conversational filler (e.g. "Here is the section", "Based on the context", "I will now list...").
- DO NOT refer to yourself, the system, the prompt, or the context.
- START IMMEDIATELY with the data requested.
- Include every specific detail, coordinate, list item, table row, and fact discovered that belongs in this section.
- DO NOT summarize or condense. If there is a large table of interactive controls, write out every single row.
- DO NOT write an introduction or conclusion for the whole report, ONLY write the content for "{section}".

Original Query Instructions:
{query}

Context Data Snippet:
{safe_context_snippet}...

Reasoning Discoveries:
{chunk_evidence}

Section to write: {section}
Output format: Start with the header **{section}**, followed immediately by the highly detailed content."""
                else:
                    section_prompt = f"""You are continuing to build the highly detailed section titled: "{section}".
Your task is to seamlessly integrate the new discoveries into the existing section text.

CRITICAL INSTRUCTIONS:
- DO NOT output any meta-commentary, introductory remarks, or conversational filler.
- DO NOT refer to yourself, the system, the prompt, or the context.
- DO NOT summarize or condense. Keep all existing tables, coordinates, and details, and add the new ones.
- DO NOT write an introduction or conclusion.
- output ONLY the updated text.

Original Query Instructions:
{query}

Existing Text for this Section:
{section_text}

New Reasoning Discoveries to integrate:
{chunk_evidence}

Updated Section Content (Start with **{section}**):"""

                try:
                    response = await self.ollama_engine.generate(
                        prompt=section_prompt,
                        model=model,
                        temperature=0.2, # Very low temp for factual extraction
                        max_tokens=2048
                    )
                    
                    section_text = response["text"].strip()
                except Exception as e:
                    logger.error(f"Failed to generate chunk {chunk_idx} for section '{section}': {e}")
                    if chunk_idx == 0:
                        section_text = f"**{section}**\nFailed to generate this section. Error: {str(e)}"
            
            accumulated_response += ("\n\n" if accumulated_response else "") + section_text

        return accumulated_response

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
            
        try:
            c = (context or "").lstrip()
            if (c.startswith("{") or "JSON_SCREEN_MAP:" in c or "FLATTENED_SCREEN_MAP:" in c) and ("\"context_items\"" in c or "\"context_items_sample\"" in c or "\"ocr_texts_sample\"" in c or "AVAILABLE_CONTEXT_IDS" in c):
                return context
        except Exception:
            pass
        
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
