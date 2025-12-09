import os
import time
import json
import asyncio
import requests
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from collections import deque
try:
    import numpy as np
    NP_AVAILABLE = True
except Exception:
    NP_AVAILABLE = False
    np = None

try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except Exception:
    SENTENCE_TRANSFORMERS_AVAILABLE = False
    SentenceTransformer = None

try:
    import faiss
    FAISS_AVAILABLE = True
except Exception:
    FAISS_AVAILABLE = False
    faiss = None

VISION_MODELS = [
    'llava', 'bakllava', 'moondream', 'llava-llama3', 'llava-phi3', 'minicpm-v'
]

def get_ollama_base_url() -> str:
    host = os.getenv("OLLAMA_HOST", "localhost")
    port = os.getenv("OLLAMA_PORT", "11434")
    return f"http://{host}:{port}"

def is_vision_model(model_name: str) -> bool:
    ml = model_name.lower()
    return any(v in ml for v in VISION_MODELS)

@dataclass
class MemoryItem:
    id: str
    content: str
    embedding: Optional[List[float]]
    timestamp: float
    relevance_score: float = 0.0
    access_count: int = 0
    source: str = "unknown"
    metadata: Dict[str, Any] = None
    image_data: Optional[str] = None
    image_description: Optional[str] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}

class DynamicMemoryWorkspace:
    def __init__(self, max_size: int = 1000, embedding_dim: int = 384):
        self.max_size = max_size
        self.memory_pool: Dict[str, MemoryItem] = {}
        self.access_history = deque(maxlen=max_size)
        self.embedding_dim = embedding_dim
        if FAISS_AVAILABLE and NP_AVAILABLE:
            self.index = faiss.IndexFlatIP(embedding_dim)
            self.id_to_index: Dict[str, int] = {}
            self.index_to_id: Dict[int, str] = {}
        else:
            self.index = None

    def add_memory(self, item: MemoryItem) -> None:
        if len(self.memory_pool) >= self.max_size:
            self._evict_memory()
        self.memory_pool[item.id] = item
        self.access_history.append(item.id)
        if self.index is not None and item.embedding is not None and NP_AVAILABLE:
            try:
                vec = np.array(item.embedding, dtype=np.float32).reshape(1, -1)
                self.index.add(vec)
                pos = self.index.ntotal - 1
                self.id_to_index[item.id] = pos
                self.index_to_id[pos] = item.id
            except Exception:
                pass

    def _evict_memory(self) -> None:
        counts = {mid: it.access_count for mid, it in self.memory_pool.items()}
        sorted_items = sorted(counts.items(), key=lambda x: x[1])
        num_to_remove = max(1, len(self.memory_pool) // 10)
        for i in range(num_to_remove):
            iid = sorted_items[i][0]
            if iid in self.memory_pool:
                del self.memory_pool[iid]

    def _string_similarity(self, a: str, b: str) -> float:
        ta = set([t for t in a.lower().split() if t.isalpha() or t.isalnum()])
        tb = set([t for t in b.lower().split() if t.isalpha() or t.isalnum()])
        if not ta or not tb:
            return 0.0
        inter = len(ta & tb)
        union = len(ta | tb)
        return inter / union if union > 0 else 0.0

    def search_similar(self, query_text: str, top_k: int = 5) -> List[MemoryItem]:
        if self.index is not None and self.index.ntotal > 0 and NP_AVAILABLE:
            try:
                # Use embedding for similarity if available
                # Fallback to string similarity if query embedding is None
                q_embed = None
                return self._search_with_embeddings(query_text, top_k)
            except Exception:
                pass
        sims: List[tuple] = []
        for it in self.memory_pool.values():
            s = self._string_similarity(query_text, it.content)
            sims.append((s, it))
        sims.sort(key=lambda x: x[0], reverse=True)
        results: List[MemoryItem] = []
        for s, it in sims[:top_k]:
            it.relevance_score = float(s)
            it.access_count += 1
            results.append(it)
        return results

    def get_recent_memories(self, source: str = None, item_type: str = None, limit: int = 5, seconds: float = 60.0) -> List[MemoryItem]:
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

    def _search_with_embeddings(self, query_text: str, top_k: int) -> List[MemoryItem]:
        # This method expects the caller to convert text to embedding; used when engine has embeddings
        # Here we approximate by averaging word vectors, but since we don't have them, keep interface
        # The engine will call FAISS directly with the computed embedding
        raise NotImplementedError

class ComoRAGEngine:
    def __init__(self):
        self.memory_workspace = None
        self.embedding_model = None
        self.reasoning_history: List[Dict[str, Any]] = []
        self._initialized = False
        self.confidence_threshold = 0.8

    async def initialize(self, memory_size: int = 1000) -> None:
        emb_dim = 384
        if SENTENCE_TRANSFORMERS_AVAILABLE:
            try:
                self.embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
                try:
                    emb_dim = int(self.embedding_model.get_sentence_embedding_dimension())
                except Exception:
                    emb_dim = 384
            except Exception:
                self.embedding_model = None
        self.memory_workspace = DynamicMemoryWorkspace(max_size=memory_size, embedding_dim=emb_dim)
        self._initialized = True

    def _get_embedding(self, text: str) -> Optional[List[float]]:
        if self.embedding_model is None:
            return None
        try:
            vec = self.embedding_model.encode(text, normalize_embeddings=True)
            return [float(x) for x in vec.tolist()]
        except Exception:
            return None

    def _ollama_generate(self, model: str, prompt: str, temperature: Optional[float] = None) -> str:
        """Generate text using Ollama API with robust model unloading"""
        try:
            # Stop OTHER running models first to free VRAM
            try:
                logger.info(f"Checking for running models before loading {model}...")
                r = requests.get(f"{get_ollama_base_url()}/api/ps", headers={"Connection": "close"}, timeout=4)
                if r.status_code == 200:
                    data = r.json()
                    items = data.get("models") if isinstance(data, dict) else (data if isinstance(data, list) else [])
                    
                    for m in items if isinstance(items, list) else []:
                        pid = m.get("id") if isinstance(m, dict) else None
                        name = (m.get("model") or m.get("name")) if isinstance(m, dict) else None
                        
                        # Don't stop the model we want to use if it's already loaded
                        # improved matching to handle tags
                        if name == model or (name and model and (name.startswith(model + ":") or model.startswith(name + ":"))):
                            continue
                            
                        payload = {}
                        if pid: payload["id"] = pid
                        if name: payload["model"] = name
                        
                        if payload:
                            try:
                                logger.info(f"Stopping competing model: {name}")
                                requests.post(f"{get_ollama_base_url()}/api/stop", json=payload, headers={"Connection": "close"}, timeout=3)
                            except Exception as e:
                                logger.warning(f"Failed to stop model {name}: {e}")
            except Exception as e:
                logger.warning(f"Error checking running models: {e}")

            # Generate
            payload = {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "keep_alive": 60,  # Keep model alive for 60s to prevent thrashing
                "options": {
                    "num_predict": -1 
                }
            }
            if temperature is not None:
                payload["options"]["temperature"] = temperature

            logger.info(f"Generating with model: {model}")
            r = requests.post(f"{get_ollama_base_url()}/api/generate", json=payload, headers={"Connection": "close"})
            r.raise_for_status()
            data = r.json()

            return data.get("response") or data.get("text") or ""
            
        except Exception as e:
            logger.error(f"Ollama generation failed: {e}")
            # Fallback or re-raise
            return ""

    def _ollama_chat(self, model: str, prompt: str, images: Optional[List[str]] = None, temperature: Optional[float] = None) -> str:
        """Chat using Ollama API with robust model unloading"""
        try:
             # Stop OTHER running models first
            try:
                logger.info(f"Checking for running models before loading {model}...")
                r = requests.get(f"{get_ollama_base_url()}/api/ps", headers={"Connection": "close"}, timeout=4)
                if r.status_code == 200:
                    data = r.json()
                    items = data.get("models") if isinstance(data, dict) else (data if isinstance(data, list) else [])
                    
                    for m in items if isinstance(items, list) else []:
                        pid = m.get("id") if isinstance(m, dict) else None
                        name = (m.get("model") or m.get("name")) if isinstance(m, dict) else None
                        
                        # Don't stop the model we want to use if it's already loaded
                        # improved matching to handle tags
                        if name == model or (name and model and (name.startswith(model + ":") or model.startswith(name + ":"))):
                            continue
                            
                        payload = {}
                        if pid: payload["id"] = pid
                        if name: payload["model"] = name
                        
                        if payload:
                            try:
                                logger.info(f"Stopping competing model: {name}")
                                requests.post(f"{get_ollama_base_url()}/api/stop", json=payload, headers={"Connection": "close"}, timeout=3)
                            except Exception as e:
                                logger.warning(f"Failed to stop model {name}: {e}")
            except Exception as e:
                logger.warning(f"Error checking running models: {e}")

            # Chat
            messages = [{"role": "user", "content": prompt}]
            if images:
                messages[0]["images"] = images
                
            payload = {
                "model": model,
                "messages": messages,
                "stream": False,
                "keep_alive": 60,
                "options": {
                    "num_predict": -1 
                }
            }
            if temperature is not None:
                payload["options"]["temperature"] = temperature

            logger.info(f"Chatting with model: {model}")
            r = requests.post(f"{get_ollama_base_url()}/api/chat", json=payload, headers={"Connection": "close"})
            r.raise_for_status()
            data = r.json()

            if "message" in data:
                return data["message"].get("content", "")
            return data.get("response") or ""
            
        except Exception as e:
            logger.error(f"Ollama chat failed: {e}")
            return ""

    async def _generate_text(self, model: str, prompt: str, images: Optional[List[str]] = None, temperature: Optional[float] = None) -> str:
        if images and is_vision_model(model):
            return await asyncio.to_thread(self._ollama_chat, model, prompt, images, temperature)
        return await asyncio.to_thread(self._ollama_generate, model, prompt, temperature)

    async def _generate_probing_queries(self, query: str, context: str, model: str, cycle: int) -> List[str]:
        prompt = (
            "Given the main query and current context, generate 3-5 specific probing questions as a JSON list of strings.\n\n"
            f"Main Query: {query}\n\n"
            f"Current Context: {context}\n\n"
            f"Cycle: {cycle + 1}\n\n"
            "Probing Questions:"
        )
        text = await self._generate_text(model, prompt, None, 0.7)
        try:
            arr = json.loads(text.strip())
            if isinstance(arr, list):
                return [str(x) for x in arr[:5]]
        except Exception:
            pass
        lines = [ln.strip('- ').strip() for ln in text.strip().split('\n') if ln.strip()]
        return lines[:5] if lines else [query]

    async def _retrieve_evidence(self, probe: str) -> List[MemoryItem]:
        sims = []
        if self.memory_workspace:
            if self.embedding_model is not None and self.memory_workspace.index is not None and NP_AVAILABLE:
                try:
                    qv = np.array(self._get_embedding(probe) or [], dtype=np.float32)
                    if qv.size > 0:
                        scores, idxs = self.memory_workspace.index.search(qv.reshape(1, -1), 3)
                        for score, idx in zip(scores[0], idxs[0]):
                            item_id = self.memory_workspace.index_to_id.get(int(idx))
                            if item_id and item_id in self.memory_workspace.memory_pool:
                                it = self.memory_workspace.memory_pool[item_id]
                                it.relevance_score = float(score)
                                it.access_count += 1
                                sims.append(it)
                except Exception:
                    pass
            if not sims:
                sims = self.memory_workspace.search_similar(probe, top_k=3)
        q_item = MemoryItem(
            id=f"probe_{int(time.time()*1000)}",
            content=probe,
            embedding=self._get_embedding(probe),
            timestamp=time.time(),
            source="retrieval",
            metadata={"type": "probe"}
        )
        self.memory_workspace.add_memory(q_item)
        return sims

    async def _consolidate_evidence(self, evidence: List[MemoryItem], context: str, model: str) -> str:
        if not evidence:
            return context
        evidence_text = "\n".join([f"- {it.content}" for it in evidence])
        prompt = (
            "Consolidate the following evidence with the existing context to create a coherent understanding.\n\n"
            f"Existing Context:\n{context}\n\n"
            f"New Evidence:\n{evidence_text}\n\n"
            "Consolidated Context:"
        )
        consolidated = await self._generate_text(model, prompt, None, 0.3)
        mem = MemoryItem(
            id=f"consolidated_{int(time.time()*1000)}",
            content=consolidated.strip(),
            embedding=self._get_embedding(consolidated),
            timestamp=time.time(),
            source="reasoning",
            metadata={"type": "consolidation"}
        )
        self.memory_workspace.add_memory(mem)
        return consolidated.strip()

    async def _generate_reasoning_output(self, query: str, context: str, model: str) -> str:
        prompt = (
            "Based on the comprehensive context provided, provide a detailed and well-reasoned response to the query.\n\n"
            f"Query: {query}\n\n"
            f"Context: {context}\n\n"
            "Reasoned Response:"
        )
        return (await self._generate_text(model, prompt, None, 0.5)).strip()

    async def _calculate_confidence(self, reasoning_output: str, context: str, model: str) -> float:
        prompt = (
            "Evaluate the confidence level of the reasoning output based on the available context and return a number between 0.0 and 1.0.\n\n"
            f"Context: {context}\n\n"
            f"Reasoning Output: {reasoning_output}\n\n"
            "Confidence Score:"
        )
        text = await self._generate_text(model, prompt, None, 0.1)
        import re
        m = re.search(r"([0-9]*\.?[0-9]+)", text)
        if not m:
            return 0.5
        try:
            v = float(m.group(1))
            return max(0.0, min(1.0, v))
        except Exception:
            return 0.5

    async def _generate_exploratory_query(self, original_query: str, current_output: str, model: str) -> str:
        prompt = (
            "Based on the original query and current reasoning output, generate a new exploratory question that helps deepen understanding.\n\n"
            f"Original Query: {original_query}\n\n"
            f"Current Output: {current_output}\n\n"
            "New Exploratory Question:"
        )
        return (await self._generate_text(model, prompt, None, 0.8)).strip() or original_query

    async def reason(self, query: str, context: Optional[str], model: Optional[str], max_cycles: int, memory_size: Optional[int], images: Optional[List[str]]) -> Dict[str, Any]:
        if not self._initialized:
            await self.initialize(memory_size or 1000)
        if memory_size and self.memory_workspace and self.memory_workspace.max_size != memory_size:
            self.memory_workspace.max_size = memory_size
        current_query = query
        global_context = context or ""
        query_embedding = self._get_embedding(query)
        initial_memory = MemoryItem(
            id=f"query_{int(time.time()*1000)}",
            content=query,
            embedding=query_embedding,
            timestamp=time.time(),
            source="query",
            metadata={"type": "initial_query"},
            image_data=images[0] if images else None
        )
        self.memory_workspace.add_memory(initial_memory)
        cycles: List[Dict[str, Any]] = []
        for cycle in range(max_cycles):
            try:
                probing = await self._generate_probing_queries(current_query, global_context, model, cycle)
                evidence: List[MemoryItem] = []
                
                # Force include recent vision data in the first cycle
                if cycle == 0 and self.memory_workspace:
                    vision_items = self.memory_workspace.get_recent_memories(source="advanced_vision", limit=1, seconds=120.0)
                    if vision_items:
                        evidence.extend(vision_items)
                        # Mark as accessed
                        for item in vision_items:
                            item.access_count += 1

                for pq in probing:
                    ev = await self._retrieve_evidence(pq)
                    evidence.extend(ev)
                consolidated_context = await self._consolidate_evidence(evidence, global_context, model)
                output_text = await self._generate_reasoning_output(current_query, consolidated_context, model)
                confidence_score = await self._calculate_confidence(output_text, consolidated_context, model)
                cycles.append({
                    "cycle_id": cycle,
                    "probing_queries": probing,
                    "confidence_score": confidence_score,
                    "evidence_count": len(evidence)
                })
                if confidence_score >= self.confidence_threshold:
                    break
                global_context = consolidated_context
                if cycle < max_cycles - 1:
                    current_query = await self._generate_exploratory_query(query, output_text, model)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Error in reasoning cycle {cycle}: {e}", exc_info=True)
                break
        final_text = output_text if cycles else ""
        final_conf = cycles[-1]["confidence_score"] if cycles else 0.0
        return {
            "query": query,
            "response": final_text,
            "confidence": final_conf,
            "reasoning_cycles": len(cycles),
            "memory_items_used": len(self.memory_workspace.memory_pool) if self.memory_workspace else 0,
            "reasoning_history": cycles
        }

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

    async def clear_memory(self) -> None:
        if self.memory_workspace:
            self.memory_workspace.memory_pool.clear()
            self.memory_workspace.access_history.clear()
        self.reasoning_history.clear()
        self._initialized = False

    def is_ready(self) -> bool:
        return self._initialized and self.memory_workspace is not None
