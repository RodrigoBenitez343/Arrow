#!/usr/bin/env python3
"""
Ollama Engine Module

Handles Ollama model loading, management, and inference for local LLM models.
Replaces vLLM functionality with Ollama backend.
"""

import asyncio
import logging
import os
import psutil
import json
import aiohttp
from typing import Dict, List, Optional, Any, AsyncGenerator
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ModelConfig:
    """Configuration for a loaded model"""
    name: str
    model_path: str
    context_length: int = 4096
    temperature: float = 0.7
    top_p: float = 0.9
    top_k: int = 40
    repeat_penalty: float = 1.1
    seed: Optional[int] = None


class OllamaEngine:
    """Ollama engine for model inference"""
    
    def __init__(self, ollama_settings=None):
        # Handle both old and new settings format for compatibility
        if hasattr(ollama_settings, 'base_url'):
            # New OllamaSettings object
            self.settings = ollama_settings
        else:
            # Legacy settings dict or None
            from config.settings import OllamaSettings
            self.settings = OllamaSettings() if ollama_settings is None else ollama_settings
        
        self.base_url = getattr(self.settings, 'base_url', 'http://127.0.0.1:11434').rstrip('/')
        self.loaded_models: Dict[str, ModelConfig] = {}
        self._initialized = False
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_loop: Optional[asyncio.AbstractEventLoop] = None
        
    async def initialize(self):
        """Initialize the Ollama engine"""
        logger.info("Initializing Ollama engine...")
        await self._ensure_session()
        
        # Check if Ollama is running
        try:
            await self._check_ollama_status()
        except Exception as e:
            logger.error(f"Failed to connect to Ollama: {e}")
            raise RuntimeError(f"Ollama is not running or not accessible at {self.base_url}")
        
        # Models are deliberately neither preloaded NOR pulled at startup.  The
        # lifecycle is strictly per node - load -> use -> unload - so booting
        # the gateway (which happens on every app launch, wherever a chain's
        # model-using nodes may sit) must not hold a model in memory/VRAM and
        # must not kick off a multi-GB download for models the run may never
        # touch.  Ollama loads a model on the first generate that names it and
        # the gateway's unload path releases it afterwards, so
        # ``settings.default_models`` is ignored here.
        
        self._initialized = True
        logger.info("Ollama engine initialized successfully")
    
    async def _ensure_session(self):
        try:
            loop = asyncio.get_running_loop()
        except Exception:
            loop = None
        if self._session is not None and getattr(self._session, "closed", False):
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None
            self._session_loop = None
        if self._session is None or (loop is not None and self._session_loop is not loop):
            timeout = aiohttp.ClientTimeout(total=None)
            self._session = aiohttp.ClientSession(timeout=timeout)
            self._session_loop = loop
        
    async def _cleanup_runners(self, target_model: Optional[str] = None):
        await self._ensure_session()
        try:
            async with self._session.get(f"{self.base_url}/api/ps") as r:
                if r.status != 200:
                    return
                data = await r.json()
                items = data.get("models") if isinstance(data, dict) else (data if isinstance(data, list) else [])
                if isinstance(items, list):
                    for m in items:
                        pid = None
                        name = None
                        try:
                            pid = m.get("id") if isinstance(m, dict) else None
                        except Exception:
                            pid = None
                        try:
                            name = (m.get("model") or m.get("name")) if isinstance(m, dict) else None
                        except Exception:
                            name = None
                        
                        # Skip target model
                        if target_model and name:
                            if name == target_model or name.startswith(target_model + ":") or target_model.startswith(name + ":"):
                                continue

                        payload: Dict[str, Any] = {}
                        if pid:
                            payload["id"] = pid
                        if name:
                            payload["model"] = name
                        if not payload:
                            continue
                        try:
                            await self._session.post(f"{self.base_url}/api/stop", json=payload, headers={"Connection": "close"})
                        except Exception:
                            pass
        except Exception:
            pass
    async def _check_ollama_status(self):
        """Check if Ollama is running and accessible"""
        await self._ensure_session()
        async with self._session.get(f"{self.base_url}/api/tags") as response:
            if response.status != 200:
                raise RuntimeError(f"Ollama returned status {response.status}")
            return await response.json()
    
    async def load_model(self, model_name: str, config: Optional[ModelConfig] = None) -> bool:
        """Load a model into Ollama"""
        await self._ensure_session()
        if model_name in self.loaded_models:
            logger.info(f"Model {model_name} already loaded")
            return True
        
        logger.info(f"Loading model: {model_name}")
        
        try:
            # Check if model exists in Ollama
            available_models = await self._list_available_models()
            model_exists = any(model['name'].startswith(model_name) for model in available_models)
            
            if not model_exists:
                # Try to pull the model
                logger.info(f"Model {model_name} not found locally, attempting to pull...")
                await self._pull_model(model_name)
            
            # Use provided config or create default
            if config is None:
                config = ModelConfig(
                    name=model_name,
                    model_path=model_name,
                    context_length=getattr(self.settings, 'context_length', 4096),
                    temperature=getattr(self.settings, 'temperature', 0.7),
                    top_p=getattr(self.settings, 'top_p', 0.9),
                    top_k=getattr(self.settings, 'top_k', 40),
                    repeat_penalty=getattr(self.settings, 'repeat_penalty', 1.1)
                )
            
            # Test the model appropriately (LLM vs embedding)
            is_embedding = False
            try:
                async with self._session.post(f"{self.base_url}/api/embed", json={"model": model_name, "input": "hello", "keep_alive": 60}) as resp:
                    is_embedding = resp.status in (200, 404)
            except Exception:
                is_embedding = False

            test_response = False
            if is_embedding:
                test_response = await self._embed_test(model_name)
            else:
                test_response = await self._generate_test(model_name)
            if test_response:
                self.loaded_models[model_name] = config
                logger.info(f"Successfully loaded model: {model_name}")
                return True
            else:
                logger.error(f"Model {model_name} failed test generation")
                return False
            
        except Exception as e:
            logger.error(f"Failed to load model {model_name}: {e}")
            return False
    
    async def _list_available_models(self) -> List[Dict[str, Any]]:
        """List available models in Ollama"""
        await self._ensure_session()
        async with self._session.get(f"{self.base_url}/api/tags") as response:
            if response.status == 200:
                data = await response.json()
                return data.get('models', [])
            return []
    
    async def _pull_model(self, model_name: str):
        """Pull a model from Ollama registry"""
        await self._ensure_session()
        payload = {"name": model_name}
        
        async with self._session.post(
            f"{self.base_url}/api/pull",
            json=payload
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"Failed to pull model {model_name}: {response.status}")
            
            # Stream the pull progress
            async for line in response.content:
                if line:
                    try:
                        progress = json.loads(line.decode())
                        if 'status' in progress:
                            logger.info(f"Pull progress: {progress['status']}")
                    except json.JSONDecodeError:
                        pass
            
    async def _generate_test(self, model_name: str) -> bool:
        """Test model with a simple generation"""
        await self._ensure_session()
        try:
            payload = {
                "model": model_name,
                "prompt": "Hello",
                "stream": False,
                "options": {
                    "num_predict": 5
                }
            }
            
            async with self._session.post(
                f"{self.base_url}/api/generate",
                json=payload
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return 'response' in result
                return False
        except Exception as e:
            logger.error(f"Test generation failed: {e}")
            return False

    async def _embed_test(self, model_name: str) -> bool:
        """Test model with a simple embedding call (for embedding-only models)"""
        await self._ensure_session()
        try:
            payload = {"model": model_name, "input": "hello", "keep_alive": 60}
            async with self._session.post(f"{self.base_url}/api/embed", json=payload) as response:
                if response.status == 404:
                    payload["prompt"] = "hello"
                    async with self._session.post(f"{self.base_url}/api/embeddings", json=payload) as legacy:
                        if legacy.status != 200:
                            return False
                        data = await legacy.json()
                        return bool((isinstance(data, dict) and (data.get("embedding") or data.get("embeddings"))) or isinstance(data, list))
                if response.status != 200:
                    return False
                data = await response.json()
                if isinstance(data, dict):
                    if data.get("embedding") and isinstance(data["embedding"], list):
                        return True
                    if data.get("embeddings") and isinstance(data["embeddings"], list):
                        return True
                if isinstance(data, list) and data and isinstance(data[0], float):
                    return True
                return False
        except Exception as e:
            logger.error(f"Test embedding failed: {e}")
            return False
    
    async def embeddings(self, model: str, inputs: List[str], normalize: Optional[bool] = None, pooling: Optional[str] = None, ephemeral: bool = False) -> List[List[float]]:
        """Generate embeddings using Ollama"""
        await self._ensure_session()
        if not self._initialized:
            raise RuntimeError("Ollama engine not initialized")
        
        # Check if the model is already running to avoid unnecessary cleanup/reloading
        is_running = False
        try:
            async with self._session.get(f"{self.base_url}/api/ps") as r:
                if r.status == 200:
                    data = await r.json()
                    items = data.get("models") if isinstance(data, dict) else (data if isinstance(data, list) else [])
                    if isinstance(items, list):
                        for m in items:
                            name = (m.get("model") or m.get("name")) if isinstance(m, dict) else None
                            if name and (name == model or name.startswith(model + ":") or model.startswith(name + ":")):
                                is_running = True
                                break
        except Exception:
            pass

        if not is_running:
            await self._cleanup_runners(model)
        
        # Ensure model is present/loaded
        # We try to load it if we track it, otherwise we assume Ollama handles it
        if model not in self.loaded_models:
             # Attempt to pull/load if not known, but don't fail strictly if it's just an embedding model not in our list
             # For now, we rely on Ollama to handle the model if it exists.
             pass

        # Sanitize inputs
        clean_inputs = []
        for x in inputs:
            s = str(x) if x is not None else ""
            clean_inputs.append(s)
            
        if not clean_inputs or all(not s.strip() for s in clean_inputs):
            return []

        # Prepare payload
        multiple = len(clean_inputs) > 1
        val = clean_inputs if multiple else clean_inputs[0]
        
        payload = {
            "model": model,
            "input": val,
            "keep_alive": 0,
        }
        if normalize is not None:
            payload["normalize"] = normalize
        if pooling is not None:
            payload["pooling"] = pooling

        logger.info(f"Sending embeddings request to Ollama: {json.dumps(payload)[:500]}...")
            
        try:
            async with self._session.post(f"{self.base_url}/api/embed", json=payload, headers={"Connection": "close"}) as response:
                if response.status == 404:
                     # Fallback to old /api/embeddings endpoint if /api/embed doesn't exist
                    logger.info("Endpoint /api/embed not found, falling back to /api/embeddings")
                     # Legacy endpoint might need 'prompt'
                    payload["prompt"] = val
                    async with self._session.post(f"{self.base_url}/api/embeddings", json=payload, headers={"Connection": "close"}) as response_old:
                         response = response_old
                         if response.status != 200:
                            text = await response.text()
                            logger.error(f"Embeddings failed with status {response.status}: {text}")
                            raise RuntimeError(f"Embeddings failed with status {response.status}")
                        
                         data = await response.json()
                elif response.status != 200:
                    text = await response.text()
                    logger.error(f"Embeddings failed with status {response.status}: {text}")
                    raise RuntimeError(f"Embeddings failed with status {response.status}")
                else:
                    text = await response.text()
                    logger.info(f"Raw Ollama response (first 500 chars): {text[:500]}")
                    try:
                        data = json.loads(text)
                    except json.JSONDecodeError:
                         logger.error("Failed to decode JSON from Ollama response")
                         raise

                # Normalize response
                embeddings = await self._normalize_embeddings_response(len(clean_inputs), data, clean_inputs, model)
                
                # Ensure cardinality and non-empty vectors
                if len(embeddings) != len(clean_inputs) or any((not v) or (isinstance(v, list) and len(v) == 0) for v in embeddings):
                    # Fallback to per-input
                    embeddings = await self._query_embeddings_per_input(clean_inputs, model)
                    
                if ephemeral:
                    try:
                        await self.unload_model(model)
                    except Exception:
                        pass

                return embeddings
                
        except Exception as e:
            logger.error(f"Failed to generate embeddings: {e}")
            raise

    async def _normalize_embeddings_response(self, input_count: int, resp_data: Any, inputs: List[str], model: str) -> List[List[float]]:
        """Normalize embeddings response to list of lists of floats"""
        if isinstance(resp_data, dict):
            # Preferred batch format
            if "embeddings" in resp_data and isinstance(resp_data["embeddings"], list):
                return resp_data["embeddings"]
            # Single-vector format
            if "embedding" in resp_data and isinstance(resp_data["embedding"], list):
                if input_count > 1:
                    logger.debug(f"Received single embedding for {input_count} inputs, triggering fallback")
                    return await self._query_embeddings_per_input(inputs, model)
                return [resp_data["embedding"]]
            
            logger.warning(f"Unexpected embeddings dict format: keys={list(resp_data.keys())}")
            raise RuntimeError("Unexpected embeddings dict format")
            
        # List format
        if isinstance(resp_data, list):
            if resp_data and isinstance(resp_data[0], float):
                # Single embedding vector
                if input_count > 1:
                    logger.debug(f"Received single flat embedding for {input_count} inputs, triggering fallback")
                    return await self._query_embeddings_per_input(inputs, model)
                return [resp_data]
            if resp_data and isinstance(resp_data[0], list):
                return resp_data
        
        logger.warning(f"Invalid embeddings response type: {type(resp_data)}")
        raise RuntimeError("Invalid embeddings response type")

    async def _query_embeddings_per_input(self, texts: List[str], model: str) -> List[List[float]]:
        """Query embeddings one by one (fallback)"""
        await self._ensure_session()
        out: List[List[float]] = []
        for t in texts:
            # Fix: Do not send 'prompt' parameter as it breaks newer Ollama versions
            payload = {"model": model, "input": t, "keep_alive": 0}
            
            # Try /api/embed first
            try:
                async with self._session.post(f"{self.base_url}/api/embed", json=payload) as response:
                    if response.status == 404:
                        # Fallback to /api/embeddings
                         # Ensure 'prompt' is set for legacy endpoint
                         payload["prompt"] = t
                         async with self._session.post(f"{self.base_url}/api/embeddings", json=payload) as response_old:
                             response = response_old
                             if response.status != 200:
                                 logger.warning(f"Per-input embedding failed (legacy): {response.status}")
                                 out.append([])
                                 continue
                             sub_data = await response.json()
                    elif response.status != 200:
                        logger.warning(f"Per-input embedding failed: {response.status}")
                        out.append([])
                        continue
                    else:
                        sub_data = await response.json()

                    if isinstance(sub_data, dict):
                        if "embeddings" in sub_data and isinstance(sub_data["embeddings"], list) and sub_data["embeddings"]:
                             # /api/embed returns list of lists even for single input
                             out.append(sub_data["embeddings"][0])
                        elif "embedding" in sub_data and isinstance(sub_data["embedding"], list):
                             out.append(sub_data["embedding"])
                        else:
                             out.append([])
                    elif isinstance(sub_data, list) and sub_data and isinstance(sub_data[0], float):
                        out.append(sub_data)
                    else:
                        out.append([])
                        
            except Exception as e:
                logger.error(f"Per-input embedding exception: {e}")
                out.append([])
                
        return out

    async def unload_model(self, model_name: str) -> bool:
        """Unload a model from memory (Ollama handles this automatically)"""
        await self._ensure_session()
        if model_name not in self.loaded_models:
            logger.warning(f"Model {model_name} not loaded")
            return False
        
        try:
            # Remove from our tracking
            del self.loaded_models[model_name]
            logger.info(f"Unloaded model: {model_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to unload model {model_name}: {e}")
            return False
    
    async def generate(
        self,
        prompt: str,
        model: str,
        images: Optional[List[str]] = None,
        temperature: float = 0.7,
        max_tokens: int = 512,
        top_p: float = 0.9,
        top_k: int = 40,
        repeat_penalty: float = 1.1,
        stop: Optional[List[str]] = None,
        stream: bool = False,
        ephemeral: bool = False
    ) -> Dict[str, Any]:
        """Generate text using Ollama"""
        await self._ensure_session()
        if not self._initialized:
            raise RuntimeError("Ollama engine not initialized")
        
        if model not in self.loaded_models:
            # Try to load the model automatically
            success = await self.load_model(model)
            if not success:
                raise RuntimeError(f"Model {model} not available")
        
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": stream,
            "keep_alive": 60,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
                "top_p": top_p,
                "top_k": top_k,
                "repeat_penalty": repeat_penalty
            }
        }
        
        if images:
            payload["images"] = images
        
        if stop:
            payload["options"]["stop"] = stop
        
        if stream:
            result = await self._generate_stream(payload)
        else:
            result = await self._generate_single(payload)
        if ephemeral:
            try:
                await self.unload_model(model)
            except Exception:
                pass
        return result
    
    async def _generate_single(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Generate single response"""
        await self._ensure_session()
        await self._cleanup_runners(payload.get("model"))
        async with self._session.post(
            f"{self.base_url}/api/generate",
            json=payload,
            headers={"Connection": "close"}
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"Generation failed with status {response.status}")
            
            result = await response.json()
            
            # Convert to vLLM-compatible format
            return {
                "text": result.get("response", ""),
                "finish_reason": "stop" if result.get("done", False) else "length",
                "prompt_tokens": result.get("prompt_eval_count", 0),
                "completion_tokens": result.get("eval_count", 0),
                "total_tokens": result.get("prompt_eval_count", 0) + result.get("eval_count", 0)
            }
    
    async def _generate_stream(self, payload: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        """Generate streaming response"""
        await self._ensure_session()
        await self._cleanup_runners(payload.get("model"))
        async with self._session.post(
            f"{self.base_url}/api/generate",
            json=payload,
            headers={"Connection": "close"}
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"Generation failed with status {response.status}")
            
            accumulated_text = ""
            async for line in response.content:
                if line:
                    try:
                        chunk = json.loads(line.decode())
                        if "response" in chunk:
                            accumulated_text += chunk["response"]
                            yield {
                                "text": accumulated_text,
                                "finish_reason": "stop" if chunk.get("done", False) else None,
                                "prompt_tokens": chunk.get("prompt_eval_count", 0),
                                "completion_tokens": chunk.get("eval_count", 0),
                                "total_tokens": chunk.get("prompt_eval_count", 0) + chunk.get("eval_count", 0)
                            }
                    except json.JSONDecodeError:
                        continue
    
    async def chat_completions(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 512,
        top_p: float = 0.9,
        stream: bool = False,
        ephemeral: bool = False
    ) -> Dict[str, Any]:
        """OpenAI-compatible chat completions endpoint"""
        # Convert messages to a single prompt
        prompt = self._messages_to_prompt(messages)
        
        # Generate response
        response = await self.generate(
            prompt=prompt,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            stream=stream,
            ephemeral=ephemeral
        )
        
        # Convert to OpenAI format
        return {
            "id": f"chatcmpl-{int(asyncio.get_event_loop().time())}",
            "object": "chat.completion",
            "created": int(asyncio.get_event_loop().time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": response["text"]
                },
                "finish_reason": response["finish_reason"]
            }],
            "usage": {
                "prompt_tokens": response["prompt_tokens"],
                "completion_tokens": response["completion_tokens"],
                "total_tokens": response["total_tokens"]
            }
        }
    
    def _messages_to_prompt(self, messages: List[Dict[str, str]]) -> str:
        """Convert chat messages to a single prompt"""
        prompt_parts = []
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content", "")
            
            if role == "system":
                prompt_parts.append(f"System: {content}")
            elif role == "user":
                prompt_parts.append(f"User: {content}")
            elif role == "assistant":
                prompt_parts.append(f"Assistant: {content}")
        
        prompt_parts.append("Assistant:")
        return "\n\n".join(prompt_parts)
    
    async def list_models(self) -> List[Dict[str, Any]]:
        """List loaded models"""
        models = []
        for name, config in self.loaded_models.items():
            models.append({
                "name": name,
                "model_path": config.model_path,
                "context_length": config.context_length,
                "loaded": True
            })
        return models
    
    def get_loaded_models(self) -> List[str]:
        """Get list of loaded model names"""
        return list(self.loaded_models.keys())
    
    def is_ready(self) -> bool:
        """Check if engine is ready"""
        return self._initialized and self._session is not None
    
    def get_memory_usage(self) -> Dict[str, Any]:
        """Get memory usage information"""
        memory_info = {
            "system_memory": {
                "total": psutil.virtual_memory().total,
                "available": psutil.virtual_memory().available,
                "percent": psutil.virtual_memory().percent
            },
            "loaded_models": len(self.loaded_models)
        }
        return memory_info
    
    async def cleanup(self):
        """Cleanup resources"""
        logger.info("Cleaning up Ollama engine...")
        
        # Close HTTP session
        if self._session:
            await self._session.close()
            self._session = None
            self._session_loop = None
        
        # Clear loaded models
        self.loaded_models.clear()
        
        self._initialized = False
        logger.info("Ollama engine cleanup complete")
