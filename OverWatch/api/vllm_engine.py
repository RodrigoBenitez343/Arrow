#!/usr/bin/env python3
"""
vLLM Engine Module

Handles vLLM model loading, management, and inference for HuggingFace models.
"""

import asyncio
import logging
import os
import psutil
from typing import Dict, List, Optional, Any, AsyncGenerator
from dataclasses import dataclass

try:
    from vllm import LLM, SamplingParams
    from vllm.engine.arg_utils import AsyncEngineArgs
    from vllm.engine.async_llm_engine import AsyncLLMEngine
    VLLM_AVAILABLE = True
except ImportError:
    VLLM_AVAILABLE = False
    LLM = None
    SamplingParams = None
    AsyncEngineArgs = None
    AsyncLLMEngine = None

from transformers import AutoTokenizer
import torch

logger = logging.getLogger(__name__)


@dataclass
class ModelConfig:
    """Configuration for a loaded model"""
    name: str
    model_path: str
    max_model_len: int = 4096
    tensor_parallel_size: int = 1
    gpu_memory_utilization: float = 0.9
    trust_remote_code: bool = False
    dtype: str = "auto"
    quantization: Optional[str] = None


class VLLMEngine:
    """vLLM engine for model inference"""
    
    def __init__(self, vllm_settings=None):
        # Handle both old and new settings format for compatibility
        if hasattr(vllm_settings, 'gpu_memory_utilization'):
            # New VLLMSettings object
            self.settings = vllm_settings
        else:
            # Legacy settings dict or None
            from config.settings import VLLMSettings
            self.settings = VLLMSettings() if vllm_settings is None else vllm_settings
        
        self.engines: Dict[str, AsyncLLMEngine] = {}
        self.model_configs: Dict[str, ModelConfig] = {}
        self.tokenizers: Dict[str, Any] = {}
        self._initialized = False
        
        if not VLLM_AVAILABLE:
            logger.warning("vLLM not available. Install with: pip install vllm")
    
    async def initialize(self):
        """Initialize the vLLM engine"""
        if not VLLM_AVAILABLE:
            raise RuntimeError("vLLM is not available. Please install vllm package.")
        
        logger.info("Initializing vLLM engine...")
        
        # Check GPU availability
        if torch.cuda.is_available():
            gpu_count = torch.cuda.device_count()
            logger.info(f"Found {gpu_count} GPU(s)")
            for i in range(gpu_count):
                gpu_name = torch.cuda.get_device_name(i)
                gpu_memory = torch.cuda.get_device_properties(i).total_memory / 1024**3
                logger.info(f"GPU {i}: {gpu_name} ({gpu_memory:.1f} GB)")
        else:
            logger.warning("No GPU detected. vLLM will run on CPU (not recommended)")
        
        # Load default models if specified
        default_models = getattr(self.settings, 'default_models', [])
        if default_models:
            for model_name in default_models:
                try:
                    await self.load_model(model_name)
                except Exception as e:
                    logger.error(f"Failed to load default model {model_name}: {e}")
        
        self._initialized = True
        logger.info("vLLM engine initialized successfully")
    
    async def load_model(self, model_name: str, config: Optional[ModelConfig] = None) -> bool:
        """Load a model into vLLM"""
        if not VLLM_AVAILABLE:
            raise RuntimeError("vLLM is not available")
        
        if model_name in self.engines:
            logger.info(f"Model {model_name} already loaded")
            return True
        
        logger.info(f"Loading model: {model_name}")
        
        try:
            # Use provided config or create default
            if config is None:
                config = ModelConfig(
                    name=model_name,
                    model_path=model_name,  # HuggingFace model name
                    max_model_len=self.settings.max_model_len,
                    tensor_parallel_size=self.settings.tensor_parallel_size,
                    gpu_memory_utilization=self.settings.gpu_memory_utilization,
                    trust_remote_code=self.settings.trust_remote_code,
                    dtype=self.settings.dtype,
                    quantization=self.settings.quantization
                )
            
            # Create engine arguments
            engine_args = AsyncEngineArgs(
                model=config.model_path,
                max_model_len=config.max_model_len,
                tensor_parallel_size=config.tensor_parallel_size,
                gpu_memory_utilization=config.gpu_memory_utilization,
                trust_remote_code=config.trust_remote_code,
                dtype=config.dtype,
                quantization=config.quantization,
                disable_log_stats=False,
                enforce_eager=False,
            )
            
            # Create async engine
            engine = AsyncLLMEngine.from_engine_args(engine_args)
            
            # Load tokenizer
            tokenizer = AutoTokenizer.from_pretrained(
                config.model_path,
                trust_remote_code=config.trust_remote_code
            )
            
            # Store references
            self.engines[model_name] = engine
            self.model_configs[model_name] = config
            self.tokenizers[model_name] = tokenizer
            
            logger.info(f"Successfully loaded model: {model_name}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to load model {model_name}: {e}")
            return False
    
    async def unload_model(self, model_name: str) -> bool:
        """Unload a model from memory"""
        if model_name not in self.engines:
            logger.warning(f"Model {model_name} not loaded")
            return False
        
        try:
            # Clean up engine
            engine = self.engines[model_name]
            # Note: vLLM doesn't have explicit cleanup, rely on garbage collection
            
            # Remove references
            del self.engines[model_name]
            del self.model_configs[model_name]
            del self.tokenizers[model_name]
            
            # Force garbage collection
            import gc
            gc.collect()
            
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            
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
        top_k: int = -1,
        stream: bool = False
    ) -> Dict[str, Any]:
        """Generate text using vLLM"""
        if not self._initialized:
            raise RuntimeError("vLLM engine not initialized")
        
        if model not in self.engines:
            # Try to load the model
            success = await self.load_model(model)
            if not success:
                raise ValueError(f"Model {model} not available and failed to load")
        
        engine = self.engines[model]
        
        # Create sampling parameters
        sampling_params = SamplingParams(
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            top_k=top_k if top_k > 0 else None,
        )
        
        try:
            if stream:
                return await self._generate_stream(engine, prompt, sampling_params)
            else:
                return await self._generate_single(engine, prompt, sampling_params)
        except Exception as e:
            logger.error(f"Generation failed for model {model}: {e}")
            raise
    
    async def _generate_single(self, engine: AsyncLLMEngine, prompt: str, sampling_params: SamplingParams) -> Dict[str, Any]:
        """Generate single response"""
        results = []
        async for request_output in engine.generate(prompt, sampling_params, request_id=None):
            results.append(request_output)
        
        if not results:
            raise RuntimeError("No output generated")
        
        final_output = results[-1]
        generated_text = final_output.outputs[0].text
        
        return {
            "text": generated_text,
            "finish_reason": final_output.outputs[0].finish_reason,
            "prompt_tokens": len(final_output.prompt_token_ids),
            "completion_tokens": len(final_output.outputs[0].token_ids),
            "total_tokens": len(final_output.prompt_token_ids) + len(final_output.outputs[0].token_ids)
        }
    
    async def _generate_stream(self, engine: AsyncLLMEngine, prompt: str, sampling_params: SamplingParams) -> AsyncGenerator[Dict[str, Any], None]:
        """Generate streaming response"""
        async for request_output in engine.generate(prompt, sampling_params, request_id=None):
            for output in request_output.outputs:
                yield {
                    "text": output.text,
                    "finish_reason": output.finish_reason,
                    "prompt_tokens": len(request_output.prompt_token_ids),
                    "completion_tokens": len(output.token_ids),
                    "total_tokens": len(request_output.prompt_token_ids) + len(output.token_ids)
                }
    
    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 512,
        stream: bool = False
    ) -> Dict[str, Any]:
        """Chat completion using vLLM"""
        if model not in self.tokenizers:
            if model not in self.engines:
                success = await self.load_model(model)
                if not success:
                    raise ValueError(f"Model {model} not available")
        
        tokenizer = self.tokenizers[model]
        
        # Convert messages to prompt format
        if hasattr(tokenizer, 'apply_chat_template'):
            prompt = tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True
            )
        else:
            # Fallback for models without chat template
            prompt = self._format_chat_prompt(messages)
        
        # Generate response
        response = await self.generate(
            prompt=prompt,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream
        )
        
        if stream:
            return response  # Return generator for streaming
        
        # Format as OpenAI-compatible response
        return {
            "id": f"chatcmpl-{hash(prompt) % 1000000}",
            "object": "chat.completion",
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
    
    def _format_chat_prompt(self, messages: List[Dict[str, str]]) -> str:
        """Format messages as a simple chat prompt"""
        formatted_messages = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                formatted_messages.append(f"System: {content}")
            elif role == "user":
                formatted_messages.append(f"User: {content}")
            elif role == "assistant":
                formatted_messages.append(f"Assistant: {content}")
        
        formatted_messages.append("Assistant:")
        return "\n".join(formatted_messages)
    
    async def list_models(self) -> List[Dict[str, Any]]:
        """List loaded models"""
        models = []
        for name, config in self.model_configs.items():
            models.append({
                "name": name,
                "model_path": config.model_path,
                "max_model_len": config.max_model_len,
                "loaded": name in self.engines
            })
        return models
    
    def get_loaded_models(self) -> List[str]:
        """Get list of loaded model names"""
        return list(self.engines.keys())
    
    def is_ready(self) -> bool:
        """Check if engine is ready"""
        return self._initialized and VLLM_AVAILABLE
    
    def get_memory_usage(self) -> Dict[str, Any]:
        """Get memory usage information"""
        memory_info = {
            "system_memory": {
                "total": psutil.virtual_memory().total,
                "available": psutil.virtual_memory().available,
                "percent": psutil.virtual_memory().percent
            },
            "loaded_models": len(self.engines)
        }
        
        if torch.cuda.is_available():
            gpu_memory = []
            for i in range(torch.cuda.device_count()):
                gpu_memory.append({
                    "device": i,
                    "name": torch.cuda.get_device_name(i),
                    "total": torch.cuda.get_device_properties(i).total_memory,
                    "allocated": torch.cuda.memory_allocated(i),
                    "cached": torch.cuda.memory_reserved(i)
                })
            memory_info["gpu_memory"] = gpu_memory
        
        return memory_info
    
    async def cleanup(self):
        """Cleanup resources"""
        logger.info("Cleaning up vLLM engine...")
        
        # Unload all models
        model_names = list(self.engines.keys())
        for model_name in model_names:
            await self.unload_model(model_name)
        
        self._initialized = False
        logger.info("vLLM engine cleanup complete")