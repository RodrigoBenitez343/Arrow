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
    ephemeral: Optional[bool] = Field(False, description="Unload model after request")
    cleanup_after: Optional[bool] = Field(True, description="Run cleanup after response")


class GenerateRequest(BaseModel):
    model: str = Field(..., description="Model name to use for inference")
    prompt: str = Field(..., description="Text prompt")
    temperature: Optional[float] = Field(0.7, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(512, ge=1, le=4096)
    stream: Optional[bool] = Field(False, description="Enable streaming response")
    use_comorag: Optional[bool] = Field(False, description="Enable ComoRAG reasoning")
    ephemeral: Optional[bool] = Field(False, description="Unload model after request")
    cleanup_after: Optional[bool] = Field(True, description="Run cleanup after response")


class ComoRAGRequest(BaseModel):
    query: str = Field(..., description="Query for ComoRAG reasoning")
    context: Optional[str] = Field(None, description="Additional context")
    model: Optional[str] = Field(None, description="Model to use for reasoning")
    max_cycles: Optional[int] = Field(5, ge=1, le=20, description="Maximum reasoning cycles")
    memory_size: Optional[int] = Field(1000, ge=100, le=10000, description="Memory pool size")
    images: Optional[List[str]] = Field(None, description="Base64 encoded images for vision analysis")


class ComoRAGAddMemoryRequest(BaseModel):
    content: str = Field(..., description="Memory content")
    source: str = Field("external", description="Source of memory")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")
    image_data: Optional[str] = Field(None, description="Base64 encoded image data")


class EmbeddingsRequest(BaseModel):
    model: str = Field(..., description="Model name")
    inputs: List[str] = Field(..., description="List of input texts")
    normalize: Optional[bool] = None
    pooling: Optional[str] = None
    ephemeral: Optional[bool] = Field(False, description="Unload model after request")
    cleanup_after: Optional[bool] = Field(True, description="Run cleanup after response")

# Cleanup helpers
import requests as _rq
import subprocess as _sp
import json as _json
import time as _tm
from fastapi.responses import StreamingResponse
def _ow_base_url():
    host = os.getenv("OLLAMA_HOST", "localhost")
    port = os.getenv("OLLAMA_PORT", "11434")
    return f"http://{host}:{port}"
def _ow_list_http():
    try:
        r = _rq.get(f"{_ow_base_url()}/api/ps", headers={"Connection": "close"}, timeout=4)
        if r.status_code != 200:
            return []
        data = r.json()
        items = data.get("models") if isinstance(data, dict) else (data if isinstance(data, list) else [])
        out = []
        for m in items if isinstance(items, list) else []:
            name = (m.get("model") or m.get("name")) if isinstance(m, dict) else None
            mid = m.get("id") if isinstance(m, dict) else None
            if name or mid:
                out.append({"name": name, "id": mid})
        return out
    except Exception:
        return []
def _ow_stop_http(name=None, mid=None):
    try:
        payload = {}
        if name:
            payload["model"] = name
        if mid:
            payload["id"] = mid
        if not payload:
            return False
        _rq.post(f"{_ow_base_url()}/api/stop", json=payload, headers={"Connection": "close"}, timeout=3)
        return True
    except Exception:
        return False
def _ow_list_cli():
    try:
        def _find_ollama_bin():
            cands = []
            for k in ("OLLAMA_BIN", "OLLAMA_EXE"):
                v = os.getenv(k)
                if v:
                    cands.append(v)
            la = os.getenv("LOCALAPPDATA")
            if la:
                cands.append(os.path.join(la, "Programs", "Ollama", "ollama.exe"))
            cands.append(r"C:\\Program Files\\Ollama\\ollama.exe")
            cands.append("ollama")
            return cands
        env = os.environ.copy()
        data = None
        cflags = 0x08000000 if os.name == "nt" else 0
        for cmd in _find_ollama_bin():
            try:
                p = _sp.run([cmd, "ps", "--json"], capture_output=True, text=True, timeout=4, env=env, creationflags=cflags)
                if p.returncode != 0:
                    continue
                s = (p.stdout or "").strip()
                if not s:
                    data = {"models": []}
                    break
                data = _json.loads(s)
                break
            except Exception:
                continue
        if data is None:
            return []
        items = data.get("models") if isinstance(data, dict) else (data if isinstance(data, list) else [])
        res = []
        if isinstance(items, list):
            for m in items:
                name = (m.get("model") or m.get("name")) if isinstance(m, dict) else None
                if name:
                    res.append({"name": name})
        return res
    except Exception:
        return []
def _ow_stop_cli(name):
    try:
        if not name:
            return False
        def _find_ollama_bin():
            cands = []
            for k in ("OLLAMA_BIN", "OLLAMA_EXE"):
                v = os.getenv(k)
                if v:
                    cands.append(v)
            la = os.getenv("LOCALAPPDATA")
            if la:
                cands.append(os.path.join(la, "Programs", "Ollama", "ollama.exe"))
            cands.append(r"C:\\Program Files\\Ollama\\ollama.exe")
            cands.append("ollama")
            return cands
        env = os.environ.copy()
        cflags = 0x08000000 if os.name == "nt" else 0
        for cmd in _find_ollama_bin():
            try:
                _sp.run([cmd, "stop", name], capture_output=True, text=True, timeout=4, env=env, creationflags=cflags)
                return True
            except Exception:
                continue
        return False
    except Exception:
        return False
def _ow_list_tags():
    try:
        r = _rq.get(f"{_ow_base_url()}/api/tags", headers={"Connection": "close"}, timeout=4)
        if r.status_code != 200:
            return []
        data = r.json()
        models = data.get("models") if isinstance(data, dict) else []
        names = []
        for m in models if isinstance(models, list) else []:
            n = m.get("name") if isinstance(m, dict) else None
            if n:
                names.append(n)
        return names
    except Exception:
        return []
def _ow_candidate_names():
    names = set()
    for x in _ow_list_http():
        if x.get("name"):
            names.add(x.get("name"))
    for x in _ow_list_cli():
        if x.get("name"):
            names.add(x.get("name"))
    for n in _ow_list_tags():
        names.add(n)
    return list(names)
def _ow_cleanup_all(max_passes=3, wait_s=0.5):
    ok = False
    for _ in range(max_passes):
        for r in _ow_list_http():
            _ow_stop_http(r.get("name"), r.get("id"))
        for r in _ow_list_cli():
            _ow_stop_cli(r.get("name"))
        for name in _ow_candidate_names():
            _ow_stop_http(name, None)
            _ow_stop_cli(name)
        _tm.sleep(wait_s)
        if not _ow_list_http() and not _ow_list_cli():
            ok = True
            break
    return ok
def _ow_cleanup_model(name, max_passes=2, wait_s=0.3):
    ok = False
    for _ in range(max_passes):
        _ow_stop_http(name, None)
        _ow_stop_cli(name)
        _tm.sleep(wait_s)
        names = [x.get("name") for x in _ow_list_http()] + [x.get("name") for x in _ow_list_cli()]
        if name not in names:
            ok = True
            break
    return ok
def _ow_cleanup_after_task(model):
    try:
        _ow_cleanup_model(model, max_passes=3, wait_s=0.5)
    except Exception:
        pass


class EmbeddingsResponse(BaseModel):
    embeddings: List[List[float]]


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
async def chat_completion(request: ChatRequest, background_tasks: BackgroundTasks):
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
                stream=request.stream,
                ephemeral=bool(request.ephemeral)
            )
        if request.ephemeral:
            try:
                await ollama_engine.unload_model(request.model)
            except Exception:
                pass
        if request.cleanup_after:
            background_tasks.add_task(_ow_cleanup_after_task, request.model)
        return response
    except Exception as e:
        logger.error(f"Chat completion failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/embeddings", response_model=EmbeddingsResponse)
async def get_embeddings(request: EmbeddingsRequest, background_tasks: BackgroundTasks):
    """Get embeddings from Ollama"""
    try:
        if not ollama_engine:
            raise HTTPException(status_code=503, detail="Ollama engine not initialized")
            
        embeddings = await ollama_engine.embeddings(
            model=request.model,
            inputs=request.inputs,
            normalize=request.normalize,
            pooling=request.pooling
        )
        if request.ephemeral:
            try:
                await ollama_engine.unload_model(request.model)
            except Exception:
                pass
        if request.cleanup_after:
            background_tasks.add_task(_ow_cleanup_after_task, request.model)
        return {"embeddings": embeddings}
    except Exception as e:
        logger.error(f"Embeddings generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate")
async def generate_text(request: GenerateRequest, background_tasks: BackgroundTasks):
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
                stream=request.stream,
                ephemeral=bool(request.ephemeral)
            )
        if request.ephemeral:
            try:
                await ollama_engine.unload_model(request.model)
            except Exception:
                pass
        if request.cleanup_after:
            background_tasks.add_task(_ow_cleanup_after_task, request.model)
        return response
    except Exception as e:
        logger.error(f"Text generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/generate_stream")
async def generate_stream(request: GenerateRequest):
    try:
        base = getattr(ollama_engine, 'base_url', 'http://localhost:11434') if ollama_engine else 'http://localhost:11434'
        payload = {
            "model": request.model,
            "prompt": request.prompt,
            "stream": True,
            "keep_alive": 0,
            "options": {
                "temperature": request.temperature if request.temperature is not None else 0.7,
                "num_predict": request.max_tokens if request.max_tokens is not None else -1
            }
        }
        r = _rq.post(f"{base}/api/generate", json=payload, headers={"Connection": "close"}, stream=True)
        r.raise_for_status()
        def _iter():
            try:
                accumulated = ""
                for line in r.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    try:
                        obj = _json.loads(line)
                        delta = obj.get("response") or obj.get("text") or (obj.get("message", {}) or {}).get("content")
                        if delta:
                            accumulated += delta
                            yield _json.dumps({"text": accumulated}) + "\n"
                    except Exception:
                        continue
            finally:
                try:
                    _rq.post(f"{base}/api/stop", json={"model": request.model}, headers={"Connection": "close"}, timeout=3)
                except Exception:
                    pass
        return StreamingResponse(_iter(), media_type="application/x-ndjson")
    except Exception as e:
        logger.error(f"Streaming generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/cleanup")
async def cleanup_endpoint(body: Dict[str, Any]):
    try:
        all_flag = bool(body.get("all", False))
        if all_flag:
            success = _ow_cleanup_all()
        else:
            model = body.get("model")
            if model:
                success = _ow_cleanup_model(model)
            else:
                success = False
        return {"status": "success" if success else "failed"}
    except Exception as e:
        logger.error(f"Cleanup failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ComoRAG Specific Endpoints
@app.post("/comorag/reason")
async def comorag_reason(request: ComoRAGRequest):
    """Perform ComoRAG reasoning"""
    try:
        global comorag_engine, ollama_engine, settings
        if not comorag_engine:
            try:
                if not ollama_engine:
                    ollama_engine = OllamaEngine(settings.ollama)
                    await ollama_engine.initialize()
                comorag_engine = ComoRAGEngine(settings=settings, ollama_engine=ollama_engine)
                await comorag_engine.initialize()
            except Exception as e:
                raise HTTPException(status_code=503, detail=f"ComoRAG engine not initialized: {e}")
        
        result = await comorag_engine.reason(
            query=request.query,
            context=request.context,
            model=request.model,
            max_cycles=request.max_cycles,
            memory_size=request.memory_size,
            images=request.images
        )
        return result
    except Exception as e:
        logger.error(f"ComoRAG reasoning failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/comorag/reason/stream")
async def comorag_reason_stream(request: ComoRAGRequest):
    """Perform ComoRAG reasoning with streaming output"""
    try:
        if not comorag_engine:
            raise HTTPException(status_code=503, detail="ComoRAG engine not initialized")
        
        async def event_generator():
            try:
                async for step in comorag_engine.reason_stream(
                    query=request.query,
                    context=request.context,
                    model=request.model,
                    max_cycles=request.max_cycles,
                    memory_size=request.memory_size,
                    images=request.images
                ):
                    yield _json.dumps(step) + "\n"
            except Exception as e:
                logger.error(f"Stream error: {e}")
                yield _json.dumps({"type": "error", "message": str(e)}) + "\n"

        return StreamingResponse(event_generator(), media_type="application/x-ndjson")
    except Exception as e:
        logger.error(f"ComoRAG streaming failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/comorag/memory")
async def comorag_add_memory(request: ComoRAGAddMemoryRequest):
    """Add memory to ComoRAG"""
    try:
        global comorag_engine, ollama_engine, settings
        if not comorag_engine:
            try:
                if not ollama_engine:
                    ollama_engine = OllamaEngine(settings.ollama)
                    await ollama_engine.initialize()
                comorag_engine = ComoRAGEngine(settings=settings, ollama_engine=ollama_engine)
                await comorag_engine.initialize()
            except Exception as e:
                raise HTTPException(status_code=503, detail=f"ComoRAG engine not initialized: {e}")
        
        await comorag_engine.add_memory(
            content=request.content,
            source=request.source,
            metadata=request.metadata,
            image_data=request.image_data
        )
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Failed to add memory to ComoRAG: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/comorag/memory/state")
async def comorag_memory_state():
    """Get ComoRAG memory state"""
    try:
        global comorag_engine, ollama_engine, settings
        if not comorag_engine:
            try:
                if not ollama_engine:
                    ollama_engine = OllamaEngine(settings.ollama)
                    await ollama_engine.initialize()
                comorag_engine = ComoRAGEngine(settings=settings, ollama_engine=ollama_engine)
                await comorag_engine.initialize()
            except Exception as e:
                raise HTTPException(status_code=503, detail=f"ComoRAG engine not initialized: {e}")
        
        return await comorag_engine.get_memory_state()
    except Exception as e:
        logger.error(f"Failed to get memory state: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/comorag/memory")
async def comorag_clear_memory():
    """Clear ComoRAG memory"""
    try:
        global comorag_engine, ollama_engine, settings
        if not comorag_engine:
            try:
                if not ollama_engine:
                    ollama_engine = OllamaEngine(settings.ollama)
                    await ollama_engine.initialize()
                comorag_engine = ComoRAGEngine(settings=settings, ollama_engine=ollama_engine)
                await comorag_engine.initialize()
            except Exception as e:
                raise HTTPException(status_code=503, detail=f"ComoRAG engine not initialized: {e}")
        
        await comorag_engine.clear_memory()
        return {"status": "success", "message": "Memory cleared"}
    except Exception as e:
        logger.error(f"Failed to clear memory: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/comorag/status")
async def comorag_status(init_if_needed: bool = True):
    """Check ComoRAG readiness"""
    global comorag_engine, ollama_engine, settings
    if init_if_needed:
        try:
            if not comorag_engine:
                if not ollama_engine:
                    ollama_engine = OllamaEngine(settings.ollama)
                    await ollama_engine.initialize()
                comorag_engine = ComoRAGEngine(settings=settings, ollama_engine=ollama_engine)
            if comorag_engine and (not comorag_engine.is_ready()):
                await comorag_engine.initialize()
        except Exception:
            pass
    ready = bool(comorag_engine) and comorag_engine.is_ready()
    return {"ready": ready}


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
