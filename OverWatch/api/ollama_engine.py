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
        
        self.base_url = getattr(self.settings, 'base_url', 'http://localhost:11434')
        self.loaded_models: Dict[str, ModelConfig] = {}
        self._initialized = False
        self._session: Optional[aiohttp.ClientSession] = None
        
    async def initialize(self):
        """Initialize the Ollama engine"""
        logger.info("Initializing Ollama engine...")
        
        # Create HTTP session
        self._session = aiohttp.ClientSession()
        
        # Check if Ollama is running
        try:
            await self._check_ollama_status()
        except Exception as e:
            logger.error(f"Failed to connect to Ollama: {e}")
            raise RuntimeError(f"Ollama is not running or not accessible at {self.base_url}")
        
        # Load default models if specified
        default_models = getattr(self.settings, 'default_models', [])
        if default_models:
            for model_name in default_models:
                try:
                    await self.load_model(model_name)
                except Exception as e:
                    logger.error(f"Failed to load default model {model_name}: {e}")
        
        self._initialized = True
        logger.info("Ollama engine initialized successfully")
    
    async def _check_ollama_status(self):
        """Check if Ollama is running and accessible"""
        async with self._session.get(f"{self.base_url}/api/tags") as response:
            if response.status != 200:
                raise RuntimeError(f"Ollama returned status {response.status}")
            return await response.json()
    
    async def load_model(self, model_name: str, config: Optional[ModelConfig] = None) -> bool:
        """Load a model into Ollama"""
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
            
            # Test the model with a simple generation
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
        async with self._session.get(f"{self.base_url}/api/tags") as response:
            if response.status == 200:
                data = await response.json()
                return data.get('models', [])
            return []
    
    async def _pull_model(self, model_name: str):
        """Pull a model from Ollama registry"""
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
    
    async def unload_model(self, model_name: str) -> bool:
        """Unload a model from memory (Ollama handles this automatically)"""
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
        temperature: float = 0.7,
        max_tokens: int = 512,
        top_p: float = 0.9,
        top_k: int = 40,
        repeat_penalty: float = 1.1,
        stop: Optional[List[str]] = None,
        stream: bool = False
    ) -> Dict[str, Any]:
        """Generate text using Ollama"""
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
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
                "top_p": top_p,
                "top_k": top_k,
                "repeat_penalty": repeat_penalty
            }
        }
        
        if stop:
            payload["options"]["stop"] = stop
        
        if stream:
            return await self._generate_stream(payload)
        else:
            return await self._generate_single(payload)
    
    async def _generate_single(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Generate single response"""
        async with self._session.post(
            f"{self.base_url}/api/generate",
            json=payload
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
        async with self._session.post(
            f"{self.base_url}/api/generate",
            json=payload
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
        stream: bool = False
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
            stream=stream
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
        
        # Clear loaded models
        self.loaded_models.clear()
        
        self._initialized = False
        logger.info("Ollama engine cleanup complete")