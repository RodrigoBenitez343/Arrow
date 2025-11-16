from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import json
import base64
import os
import sys
from typing import List, Optional, Dict, Any, Union
import uvicorn
sys.path.append(os.path.dirname(__file__))
from comorag_engine import ComoRAGEngine

app = FastAPI(
    title="Ollama API Gateway",
    description="API gateway to access Ollama models over Tailscale network",
    version="1.0.0"
)

# Configure CORS for Tailscale network access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for Tailscale network access
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ollama server configuration - use environment variables for Tailscale support
def get_ollama_base_url():
    """Get Ollama base URL from environment variables"""
    ollama_host = os.getenv("OLLAMA_HOST", "localhost")
    ollama_port = os.getenv("OLLAMA_PORT", "11434")
    return f"http://{ollama_host}:{ollama_port}"

OLLAMA_BASE_URL = get_ollama_base_url()

# Vision model detection
VISION_MODELS = [
    'llava', 'bakllava', 'moondream', 'llava-llama3', 'llava-phi3', 'minicpm-v'
]

VISION_CAPABLE_CACHE: Dict[str, bool] = {}

def _is_vision_model_static(model_name: str) -> bool:
    ml = model_name.lower()
    return any(v in ml for v in VISION_MODELS)

def is_vision_model(model_name: str) -> bool:
    if model_name in VISION_CAPABLE_CACHE:
        return VISION_CAPABLE_CACHE[model_name]
    base = get_ollama_base_url()
    try:
        url = f"{base}/api/show"
        resp = requests.get(url, params={"name": model_name}, timeout=5)
        if resp.status_code != 200:
            resp = requests.post(url, json={"name": model_name}, timeout=5)
        if resp.status_code == 200:
            data = resp.json() if isinstance(resp.json(), dict) else {}
            template = str(data.get("template", "")).lower()
            modalities = str(data.get("modality", "")).lower()
            model_info = data.get("model") or {}
            if isinstance(model_info, dict):
                m_mod = str(model_info.get("modality", "")).lower()
                if any(x in m_mod for x in ("image", "vision", "multimodal")):
                    VISION_CAPABLE_CACHE[model_name] = True
                    return True
            if any(x in modalities for x in ("image", "vision", "multimodal")):
                VISION_CAPABLE_CACHE[model_name] = True
                return True
            if any(x in template for x in ("image", "images")):
                VISION_CAPABLE_CACHE[model_name] = True
                return True
        result = _is_vision_model_static(model_name)
        VISION_CAPABLE_CACHE[model_name] = result
        return result
    except Exception:
        result = _is_vision_model_static(model_name)
        VISION_CAPABLE_CACHE[model_name] = result
        return result

def process_images(images: List[str]) -> List[str]:
    """Process images from file paths or base64 strings to base64 format"""
    processed_images = []
    print(f"[DEBUG] Processing {len(images)} images...")
    
    for i, image in enumerate(images):
        try:
            # Check if it's a file path that exists
            if os.path.exists(image):
                # File path - read and encode to raw base64
                print(f"[DEBUG] Image {i+1}: Processing file path: {image}")
                with open(image, 'rb') as img_file:
                    img_data = img_file.read()
                    base64_img = base64.b64encode(img_data).decode('utf-8')
                    processed_images.append(base64_img)
                    print(f"[DEBUG] Image {i+1}: Encoded to base64 ({len(base64_img)} chars)")
            elif image.startswith('data:image/'):
                # Data URL format - extract base64 part
                print(f"[DEBUG] Image {i+1}: Processing data URL format")
                base64_part = image.split(',', 1)[1] if ',' in image else image
                processed_images.append(base64_part)
                print(f"[DEBUG] Image {i+1}: Extracted base64 part ({len(base64_part)} chars)")
            else:
                # Assume it's already raw base64 encoded
                print(f"[DEBUG] Image {i+1}: Assuming raw base64 ({len(image)} chars)")
                processed_images.append(image)
        except Exception as e:
            print(f"[ERROR] Failed to process image {image}: {str(e)}")
            raise HTTPException(
                status_code=400,
                detail=f"Failed to process image {image}: {str(e)}"
            )
    
    print(f"[DEBUG] Successfully processed {len(processed_images)} images for Ollama")
    return processed_images

# Pydantic models for request/response
class ChatRequest(BaseModel):
    model: str
    prompt: str
    stream: bool = False
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None
    system: Optional[str] = None
    images: Optional[List[str]] = None  # List of base64 encoded images or file paths

class ChatResponse(BaseModel):
    response: str
    model: str
    created_at: str
    done: bool

class EmbeddingsRequest(BaseModel):
    model: str
    inputs: List[str]
    # Optional request tuning, kept simple for now
    normalize: Optional[bool] = None
    pooling: Optional[str] = None  # e.g., "mean"

class EmbeddingsResponse(BaseModel):
    embeddings: List[List[float]]

class ModelInfo(BaseModel):
    name: str
    size: int
    digest: str
    modified_at: str

@app.get("/")
async def root():
    """Root endpoint with API information"""
    tailscale_ip = os.getenv("TAILSCALE_IP", "100.125.184.115")
    port = os.getenv("API_PORT", "8000")
    
    return {
        "message": "Ollama API Gateway",
        "version": "1.0.0",
        "network": "Tailscale",
        "server_info": {
            "host": tailscale_ip,
            "port": port,
            "access_url": f"http://{tailscale_ip}:{port}" if tailscale_ip != "0.0.0.0" else f"http://[TAILSCALE_IP]:{port}"
        },
        "environment_variables": {
            "TAILSCALE_IP": "Set this to your Tailscale machine IP (server side)",
            "TAILSCALE_SERVER_IP": "Set this to the server's Tailscale IP (client side)",
            "API_PORT": "API server port (default: 8000)"
        },
        "endpoints": {
            "/models": "GET - List available models",
            "/vision-models": "GET - List available vision-enabled models",
            "/chat": "POST - Chat with a model (supports images for vision models)",
            "/generate": "POST - Generate text with a model (supports images for vision models)",
            "/health": "GET - Check Ollama server health"
        },
        "vision_support": {
            "description": "Vision models can process images along with text prompts",
            "supported_formats": ["base64 encoded images", "local file paths"],
            "example_models": ["llava", "bakllava", "moondream", "llava-llama3", "llava-phi3"]
        }
    }

@app.get("/health")
async def health_check():
    """Check if Ollama server is running"""
    try:
        response = requests.get(f"{get_ollama_base_url()}/api/tags")
        if response.status_code == 200:
            return {"status": "healthy", "ollama_server": "running"}
        else:
            return {"status": "unhealthy", "ollama_server": "not responding"}
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=503,
            detail=f"Ollama server is not accessible: {str(e)}"
        )

@app.get("/models", response_model=List[ModelInfo])
async def list_models():
    """List all available Ollama models"""
    try:
        response = requests.get(f"{get_ollama_base_url()}/api/tags", timeout=8)
        response.raise_for_status()
        
        data = response.json()
        models: List[ModelInfo] = []

        def to_model_info(m: Any) -> ModelInfo:
            if isinstance(m, dict):
                return ModelInfo(
                    name=str(m.get("name", "Unknown")),
                    size=int(m.get("size", 0) or 0),
                    digest=str(m.get("digest", "")),
                    modified_at=str(m.get("modified_at", ""))
                )
            return ModelInfo(name=str(m), size=0, digest="", modified_at="")

        raw_models = data.get("models") if isinstance(data, dict) else data
        if isinstance(raw_models, list):
            for m in raw_models:
                try:
                    models.append(to_model_info(m))
                except Exception:
                    try:
                        name = m.get("name") if isinstance(m, dict) else str(m)
                    except Exception:
                        name = "Unknown"
                    models.append(ModelInfo(name=name, size=0, digest="", modified_at=""))
        else:
            models = []

        return models
    
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=503,
            detail=f"Failed to fetch models from Ollama: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )

@app.post("/chat")
async def chat_with_model(request: ChatRequest):
    """Chat with an Ollama model"""
    try:
        used_chat = False
        if request.images and len(request.images) > 0:
            if is_vision_model(request.model):
                processed_images = process_images(request.images)
                message = {"role": "user", "content": request.prompt, "images": processed_images}
                messages = []
                if request.system:
                    messages.append({"role": "system", "content": request.system})
                messages.append(message)
                payload = {"model": request.model, "messages": messages, "stream": request.stream, "options": {"num_predict": -1}}
                if request.temperature is not None:
                    payload["options"]["temperature"] = request.temperature
                response = requests.post(f"{get_ollama_base_url()}/api/chat", json=payload)
                used_chat = True
            else:
                payload = {"model": request.model, "prompt": request.prompt, "stream": request.stream, "options": {"num_predict": -1}}
                if request.temperature is not None:
                    payload["options"]["temperature"] = request.temperature
                if request.system is not None:
                    payload["system"] = request.system
                response = requests.post(f"{get_ollama_base_url()}/api/generate", json=payload)
        else:
            payload = {"model": request.model, "prompt": request.prompt, "stream": request.stream, "options": {"num_predict": -1}}
            if request.temperature is not None:
                payload["options"]["temperature"] = request.temperature
            if request.system is not None:
                payload["system"] = request.system
            response = requests.post(f"{get_ollama_base_url()}/api/generate", json=payload)
        
        response.raise_for_status()
        
        result = response.json()
        if used_chat:
            message = result.get("message", {})
            response_text = (message.get("content") or result.get("response") or result.get("text") or "")
        else:
            response_text = (result.get("response") or result.get("text") or "")
        if not response_text:
            try:
                if used_chat:
                    gp = {"model": request.model, "prompt": request.prompt, "stream": False, "options": {"num_predict": -1}}
                    if request.temperature is not None:
                        gp["options"]["temperature"] = request.temperature
                    if request.system is not None:
                        gp["system"] = request.system
                    fr = requests.post(f"{get_ollama_base_url()}/api/generate", json=gp)
                    fr.raise_for_status()
                    fres = fr.json()
                    response_text = fres.get("response") or fres.get("text") or ""
                else:
                    msg = [{"role": "user", "content": request.prompt}]
                    if request.system:
                        msg.insert(0, {"role": "system", "content": request.system})
                    cp = {"model": request.model, "messages": msg, "stream": False, "options": {"num_predict": -1}}
                    if request.temperature is not None:
                        cp["options"]["temperature"] = request.temperature
                    fr = requests.post(f"{get_ollama_base_url()}/api/chat", json=cp)
                    fr.raise_for_status()
                    fres = fr.json()
                    response_text = (fres.get("message", {}) or {}).get("content") or fres.get("response") or fres.get("text") or ""
            except Exception:
                response_text = response_text
        
        return {
            "response": response_text,
            "model": result.get("model", request.model),
            "created_at": result.get("created_at", ""),
            "done": result.get("done", True),
            "total_duration": result.get("total_duration"),
            "load_duration": result.get("load_duration"),
            "prompt_eval_count": result.get("prompt_eval_count"),
            "eval_count": result.get("eval_count")
        }
    except HTTPException as e:
        # Preserve explicit HTTP errors raised above (e.g., 400 for unsupported vision)
        raise e
    except requests.exceptions.Timeout:
        raise HTTPException(
            status_code=408,
            detail="Request timeout - model took too long to respond"
        )
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=503,
            detail=f"Failed to communicate with Ollama: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )

@app.post("/generate")
async def generate_text(request: ChatRequest):
    """Generate text with an Ollama model (alias for chat endpoint)"""
    return await chat_with_model(request)

@app.post("/embeddings", response_model=EmbeddingsResponse)
async def get_embeddings(request: EmbeddingsRequest):
    """Return embeddings using Ollama's embeddings endpoint.

    Accepts a list of input strings and returns a list of embedding vectors.
    """
    try:
        # Prepare payload for Ollama embeddings API
        # Ollama expects { model: <model>, input: <string or [strings]> }
        ollama_payload: Dict[str, Any] = {
            "model": request.model,
            "input": request.inputs if len(request.inputs) > 1 else request.inputs[0],
        }
        # Optional fields if needed in future
        if request.normalize is not None:
            ollama_payload["normalize"] = request.normalize
        if request.pooling is not None:
            ollama_payload["pooling"] = request.pooling

        response = requests.post(
            f"{get_ollama_base_url()}/api/embeddings",
            json=ollama_payload
        )
        response.raise_for_status()
        data = response.json()

        # Normalize Ollama response variants
        embeddings: List[List[float]] = []
        if isinstance(data, dict):
            if "embeddings" in data and isinstance(data["embeddings"], list):
                embeddings = data["embeddings"]
            elif "embedding" in data and isinstance(data["embedding"], list):
                embeddings = [data["embedding"]]
            else:
                # Some servers may return a list directly
                if isinstance(data, list) and data and isinstance(data[0], (list, float)):
                    # If it's a list of floats -> single embedding
                    if data and isinstance(data[0], float):
                        embeddings = [data]
                    else:
                        embeddings = data  # assume list of embeddings
                else:
                    raise HTTPException(status_code=500, detail="Unexpected embeddings response format")
        else:
            raise HTTPException(status_code=500, detail="Invalid embeddings response type")

        return {"embeddings": embeddings}
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=503, detail=f"Failed to fetch embeddings from Ollama: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.get("/vision-models")
async def list_vision_models():
    """List all available vision-enabled models"""
    try:
        # Get all models first
        response = requests.get(f"{OLLAMA_BASE_URL}/api/tags")
        response.raise_for_status()
        
        data = response.json()
        vision_models = []
        
        for model in data.get("models", []):
            model_name = model["name"]
            if is_vision_model(model_name):
                vision_models.append(ModelInfo(
                    name=model_name,
                    size=model["size"],
                    digest=model["digest"],
                    modified_at=model["modified_at"]
                ))
        
        return vision_models
    
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=503,
            detail=f"Failed to fetch models from Ollama: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )

if __name__ == "__main__":
    # Get Tailscale IP from environment variable, fallback to all interfaces
    tailscale_ip = os.getenv("TAILSCALE_IP", "0.0.0.0")
    port = int(os.getenv("API_PORT", "8000"))
    
    print(f"Starting Ollama API Gateway on {tailscale_ip}:{port}")
    print(f"Access via Tailscale: http://{tailscale_ip}:{port}")
    
    uvicorn.run(app, host=tailscale_ip, port=port, reload=False, log_level="info")
class ComoRAGReasonRequest(BaseModel):
    query: str
    model: str
    max_cycles: Optional[int] = 5
    memory_size: Optional[int] = 1000
    context: Optional[str] = None
    images: Optional[List[str]] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None

COMORAG_ENGINE: Optional[ComoRAGEngine] = None

@app.post("/comorag/reason")
async def comorag_reason(request: ComoRAGReasonRequest):
    try:
        global COMORAG_ENGINE
        if COMORAG_ENGINE is None or not COMORAG_ENGINE.is_ready():
            COMORAG_ENGINE = ComoRAGEngine()
            await COMORAG_ENGINE.initialize(memory_size=request.memory_size or 1000)
        ctx = request.context or ""
        imgs = process_images(request.images) if request.images else None
        result = await COMORAG_ENGINE.reason(
            query=request.query,
            context=ctx,
            model=request.model,
            max_cycles=int(request.max_cycles or 5),
            memory_size=request.memory_size,
            images=imgs
        )
        return result
    except HTTPException as e:
        raise e
    except requests.exceptions.Timeout:
        raise HTTPException(status_code=408, detail="Request timeout - ComoRAG took too long to respond")
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=503, detail=f"Failed to communicate with Ollama: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.get("/comorag/status")
async def comorag_status():
    try:
        global COMORAG_ENGINE
        ready = COMORAG_ENGINE is not None and COMORAG_ENGINE.is_ready()
        return {"ready": ready}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/comorag/memory")
async def comorag_memory():
    try:
        global COMORAG_ENGINE
        if COMORAG_ENGINE is None or not COMORAG_ENGINE.is_ready():
            return {"memory_items": 0, "max_memory_size": 0}
        ws = COMORAG_ENGINE.memory_workspace
        return {
            "memory_items": len(ws.memory_pool),
            "max_memory_size": ws.max_size,
            "recent_access": list(ws.access_history)[-10:]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/comorag/memory")
async def clear_comorag_memory():
    try:
        global COMORAG_ENGINE
        if COMORAG_ENGINE is None:
            COMORAG_ENGINE = ComoRAGEngine()
        await COMORAG_ENGINE.clear_memory()
        return {"status": "memory_cleared"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
