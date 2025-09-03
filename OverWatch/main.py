#!/usr/bin/env python3
"""
OverWatch - vLLM API with ComoRAG Integration

A FastAPI server that provides vLLM inference capabilities with ComoRAG
(Cognitive-Inspired Memory-Organized RAG) for enhanced reasoning.
"""

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Dict, List, Optional, Any

import uvicorn
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Add current directory to path for local imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from api.ollama_engine import OllamaEngine
from api.comorag_engine import ComoRAGEngine
from config.settings import load_settings, setup_logging, validate_settings

# Load settings
settings = load_settings()

# Setup logging
setup_logging(settings)
logger = logging.getLogger(__name__)

# Validate settings
issues = validate_settings(settings)
if issues:
    logger.warning("Configuration issues found:")
    for issue in issues:
        logger.warning(f"  - {issue}")

# Global instances
ollama_engine: Optional[OllamaEngine] = None
comorag_engine: Optional[ComoRAGEngine] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan events"""
    global ollama_engine, comorag_engine
    
    # Startup
    logger.info("Starting OverWatch API server...")
    
    try:
        # Initialize Ollama engine
        logger.info("Initializing Ollama engine...")
        ollama_engine = OllamaEngine(settings.ollama)
        await ollama_engine.initialize()
        
        # Initialize ComoRAG engine
        logger.info("Initializing ComoRAG engine...")
        comorag_engine = ComoRAGEngine(settings=settings, ollama_engine=ollama_engine)
        await comorag_engine.initialize()
        
        # Store engines in app state
        app.state.ollama_engine = ollama_engine
        app.state.comorag_engine = comorag_engine
        app.state.settings = settings
        
        logger.info("OverWatch API server started successfully")
        
    except Exception as e:
        logger.error(f"Failed to initialize engines: {e}")
        raise
    
    yield
    
    # Shutdown
    logger.info("Shutting down OverWatch API server...")
    
    if comorag_engine:
        await comorag_engine.cleanup()
    
    if ollama_engine:
        await ollama_engine.cleanup()
    
    logger.info("OverWatch API server shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="OverWatch - Ollama API with ComoRAG",
    description="LLM inference API with cognitive-inspired memory-organized RAG",
    version="1.0.0",
    lifespan=lifespan,
    debug=settings.api.debug
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.api.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Request/Response Models
class ChatRequest(BaseModel):
    model: str = Field(..., description="Model name to use for inference")
    messages: List[Dict[str, str]] = Field(..., description="Chat messages")
    temperature: Optional[float] = Field(0.7, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(512, ge=1, le=4096)
    stream: Optional[bool] = Field(False, description="Enable streaming response")
    use_comorag: Optional[bool] = Field(False, description="Enable ComoRAG reasoning")


class GenerateRequest(BaseModel):
    model: str = Field(..., description="Model name to use for inference")
    prompt: str = Field(..., description="Text prompt")
    temperature: Optional[float] = Field(0.7, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(512, ge=1, le=4096)
    stream: Optional[bool] = Field(False, description="Enable streaming response")
    use_comorag: Optional[bool] = Field(False, description="Enable ComoRAG reasoning")


class ComoRAGRequest(BaseModel):
    query: str = Field(..., description="Query for ComoRAG reasoning")
    context: Optional[str] = Field(None, description="Additional context")
    model: Optional[str] = Field(None, description="Model to use for reasoning")
    max_cycles: Optional[int] = Field(5, ge=1, le=20, description="Maximum reasoning cycles")
    memory_size: Optional[int] = Field(1000, ge=100, le=10000, description="Memory pool size")


class HealthResponse(BaseModel):
    status: str
    ollama_status: str
    comorag_status: str
    models_loaded: int
    memory_usage: Dict[str, Any]


# API Endpoints
@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    try:
        ollama_status = "healthy" if ollama_engine and ollama_engine.is_ready() else "not_ready"
        comorag_status = "healthy" if comorag_engine and comorag_engine.is_ready() else "not_ready"
        
        models_loaded = len(ollama_engine.get_loaded_models()) if ollama_engine else 0
        
        # Get memory usage info
        memory_usage = {
            "ollama_memory": ollama_engine.get_memory_usage() if ollama_engine else {},
            "comorag_memory": comorag_engine.get_memory_usage() if comorag_engine else {}
        }
        
        overall_status = "healthy" if ollama_status == "healthy" and comorag_status == "healthy" else "degraded"
        
        return HealthResponse(
            status=overall_status,
            ollama_status=ollama_status,
            comorag_status=comorag_status,
            models_loaded=models_loaded,
            memory_usage=memory_usage
        )
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/models")
async def list_models():
    """List available models"""
    try:
        if not ollama_engine:
            raise HTTPException(status_code=503, detail="Ollama engine not initialized")
        
        models = await ollama_engine.list_models()
        return {"models": models}
    except Exception as e:
        logger.error(f"Failed to list models: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/models/load")
async def load_model(model_request: dict):
    """Load a model into Ollama engine"""
    try:
        if not ollama_engine:
            raise HTTPException(status_code=503, detail="Ollama engine not initialized")
        
        model_name = model_request.get("model_name")
        
        if not model_name:
            raise HTTPException(status_code=400, detail="model_name is required")
        
        success = await ollama_engine.load_model(model_name)
        
        if success:
            return {"message": f"Model {model_name} loaded successfully", "model_name": model_name}
        else:
            raise HTTPException(status_code=500, detail=f"Failed to load model {model_name}")
            
    except Exception as e:
        logger.error(f"Failed to load model: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/models/{model_name}")
async def unload_model(model_name: str):
    """Unload a model from Ollama engine"""
    try:
        if not ollama_engine:
            raise HTTPException(status_code=503, detail="Ollama engine not initialized")
        
        success = await ollama_engine.unload_model(model_name)
        
        if success:
            return {"message": f"Model {model_name} unloaded successfully"}
        else:
            raise HTTPException(status_code=404, detail=f"Model {model_name} not found")
            
    except Exception as e:
        logger.error(f"Failed to unload model: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/chat")
async def chat_completion(request: ChatRequest):
    """Chat completion endpoint"""
    try:
        if not ollama_engine:
            raise HTTPException(status_code=503, detail="Ollama engine not initialized")
        
        if request.use_comorag and comorag_engine:
            # Use ComoRAG for enhanced reasoning
            response = await comorag_engine.chat_with_reasoning(
                messages=request.messages,
                model=request.model,
                temperature=request.temperature,
                max_tokens=request.max_tokens
            )
        else:
            # Direct Ollama inference
            response = await ollama_engine.chat_completions(
                messages=request.messages,
                model=request.model,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                stream=request.stream
            )
        
        return response
    except Exception as e:
        logger.error(f"Chat completion failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate")
async def generate_text(request: GenerateRequest):
    """Text generation endpoint"""
    try:
        if not ollama_engine:
            raise HTTPException(status_code=503, detail="Ollama engine not initialized")
        
        if request.use_comorag and comorag_engine:
            # Convert prompt to messages format for ComoRAG
            messages = [{"role": "user", "content": request.prompt}]
            response = await comorag_engine.chat_with_reasoning(
                messages=messages,
                model=request.model,
                temperature=request.temperature,
                max_tokens=request.max_tokens
            )
        else:
            # Direct Ollama inference
            response = await ollama_engine.generate(
                prompt=request.prompt,
                model=request.model,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                stream=request.stream
            )
        
        return response
    except Exception as e:
        logger.error(f"Text generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/comorag/reason")
async def comorag_reasoning(request: ComoRAGRequest):
    """ComoRAG reasoning endpoint"""
    try:
        if not comorag_engine:
            raise HTTPException(status_code=503, detail="ComoRAG engine not initialized")
        
        response = await comorag_engine.reason(
            query=request.query,
            context=request.context,
            model=request.model,
            max_cycles=request.max_cycles,
            memory_size=request.memory_size
        )
        
        return response
    except Exception as e:
        logger.error(f"ComoRAG reasoning failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/comorag/memory")
async def get_comorag_memory():
    """Get ComoRAG memory state"""
    try:
        if not comorag_engine:
            raise HTTPException(status_code=503, detail="ComoRAG engine not initialized")
        
        memory_state = await comorag_engine.get_memory_state()
        return memory_state
    except Exception as e:
        logger.error(f"Failed to get ComoRAG memory: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/comorag/memory")
async def clear_comorag_memory():
    """Clear ComoRAG memory"""
    try:
        if not comorag_engine:
            raise HTTPException(status_code=503, detail="ComoRAG engine not initialized")
        
        await comorag_engine.clear_memory()
        return {"status": "memory_cleared"}
    except Exception as e:
        logger.error(f"Failed to clear ComoRAG memory: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    # Run the server
    # Use Tailscale IP if enabled, otherwise use API host
    if settings.tailscale.enabled and settings.tailscale.server_ip:
        host = settings.tailscale.server_ip
        port = settings.tailscale.api_port
        logger.info(f"Starting server with Tailscale configuration: {host}:{port}")
    else:
        host = settings.api.host
        port = settings.api.port
        logger.info(f"Starting server with standard configuration: {host}:{port}")
    
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=settings.api.debug,
        log_level=settings.logging.level.lower()
    )