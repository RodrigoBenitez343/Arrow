from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import json
import base64
import os
import sys
from typing import List, Optional, Dict, Any, Union
import uvicorn
import subprocess
import time
sys.path.append(os.path.dirname(__file__))
from comorag_engine import ComoRAGEngine
import asyncio
from typing import Set
import threading

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

# --- Model bootstrap and auto-pull helpers ---
HARDCODED_REQUIRED_EMBED_MODELS: List[str] = [
    'nomic-embed-text'
]
HARDCODED_REQUIRED_LLM_MODELS: List[str] = [
    'llama3.1:8b-instruct-q8_0'
]
HARDCODED_REQUIRED_VISION_MODELS: List[str] = [
    'minicpm-v:latest'
]

def _list_ollama_models() -> Set[str]:
    try:
        resp = requests.get(f"{get_ollama_base_url()}/api/tags")
        resp.raise_for_status()
        data = resp.json()
        names = set()
        models = data.get("models") if isinstance(data, dict) else data
        if isinstance(models, list):
            for m in models:
                n = m.get("name") if isinstance(m, dict) else str(m)
                if n:
                    names.add(n)
        return names
    except Exception:
        return set()

def _pull_ollama_model(model_name: str) -> bool:
    try:
        print(f"[BOOTSTRAP] Pulling model '{model_name}' from Ollama registry...")
        r = requests.post(f"{get_ollama_base_url()}/api/pull", json={"name": model_name}, stream=True)
        if r.status_code != 200:
            # Some older servers only accept GET with params
            r = requests.get(f"{get_ollama_base_url()}/api/pull", params={"name": model_name}, stream=True)
        if r.status_code != 200:
            print(f"[BOOTSTRAP] Failed to start pull for '{model_name}': {r.status_code}")
            return False
        # Stream progress (non-critical)
        try:
            for line in r.iter_lines():
                if not line:
                    continue
                try:
                    msg = json.loads(line.decode())
                    status = msg.get("status") or msg.get("detail")
                    if status:
                        print(f"[BOOTSTRAP] {model_name}: {status}")
                except Exception:
                    pass
        except Exception:
            pass
        # Verify availability
        names = _list_ollama_models()
        present = any(n.startswith(model_name) or model_name.startswith(n) for n in names)
        if present:
            print(f"[BOOTSTRAP] Model '{model_name}' available.")
            return True
        print(f"[BOOTSTRAP] Model '{model_name}' not listed after pull; continuing.")
        return True
    except Exception as e:
        print(f"[BOOTSTRAP] Exception pulling '{model_name}': {e}")
        return False

def ensure_model_present(model_name: str) -> None:
    if not model_name:
        return
    names = _list_ollama_models()
    present = any(n.startswith(model_name) or model_name.startswith(n) for n in names)
    if present:
        return
    _pull_ollama_model(model_name)

def _parse_required_models_env(var_name: str, defaults: List[str]) -> List[str]:
    raw = os.getenv(var_name)
    if not raw:
        return defaults
    parts = [p.strip() for p in raw.split(',') if p.strip()]
    return parts or defaults

def _collect_required_models() -> List[str]:
    all_required: List[str] = []
    all_required.extend(HARDCODED_REQUIRED_EMBED_MODELS)
    all_required.extend(HARDCODED_REQUIRED_LLM_MODELS)
    all_required.extend(HARDCODED_REQUIRED_VISION_MODELS)

    seen = set()
    deduped: List[str] = []
    for m in all_required:
        key = (m or '').strip().lower()
        if key and key not in seen:
            seen.add(key)
            deduped.append(m)
    return deduped

# Bootstrap state for health/status endpoints
BOOTSTRAP_STATE: Dict[str, Any] = {
    'in_progress': False,
    'completed': False,
    'required': [],
    'missing': [],
    'errors': []
}
_BOOTSTRAP_LOCK = threading.Lock()

def _refresh_missing(required: List[str]) -> List[str]:
    names = _list_ollama_models()
    missing: List[str] = []
    for m in required:
        present = any(n.startswith(m) or m.startswith(n) for n in names)
        if not present:
            missing.append(m)
    return missing

def bootstrap_required_models() -> None:
    required = _collect_required_models()
    with _BOOTSTRAP_LOCK:
        BOOTSTRAP_STATE['in_progress'] = True
        BOOTSTRAP_STATE['completed'] = False
        BOOTSTRAP_STATE['required'] = required
        BOOTSTRAP_STATE['missing'] = _refresh_missing(required)
        BOOTSTRAP_STATE['errors'] = []

    if not required:
        with _BOOTSTRAP_LOCK:
            BOOTSTRAP_STATE['in_progress'] = False
            BOOTSTRAP_STATE['completed'] = True
        print("[BOOTSTRAP] No required models configured.")
        return

    print(f"[BOOTSTRAP] Ensuring required models are available: {', '.join(required)}")
    for m in required:
        try:
            ensure_model_present(m)
        except Exception as e:
            print(f"[BOOTSTRAP] Failed ensuring model '{m}': {e}")
            with _BOOTSTRAP_LOCK:
                BOOTSTRAP_STATE['errors'].append(str(e))
        # Update missing after each attempt
        with _BOOTSTRAP_LOCK:
            BOOTSTRAP_STATE['missing'] = _refresh_missing(required)

    with _BOOTSTRAP_LOCK:
        BOOTSTRAP_STATE['in_progress'] = False
        BOOTSTRAP_STATE['completed'] = len(BOOTSTRAP_STATE['missing']) == 0
    if BOOTSTRAP_STATE['completed']:
        print("[BOOTSTRAP] All required models are present.")
    else:
        print(f"[BOOTSTRAP] Missing models after bootstrap: {', '.join(BOOTSTRAP_STATE['missing'])}")

def _is_vision_model_static(model_name: str) -> bool:
    ml = model_name.lower()
    return any(v in ml for v in VISION_MODELS)

def is_vision_model(model_name: str) -> bool:
    if model_name in VISION_CAPABLE_CACHE:
        return VISION_CAPABLE_CACHE[model_name]
    base = get_ollama_base_url()
    try:
        url = f"{base}/api/show"
        resp = requests.get(url, params={"name": model_name})
        if resp.status_code != 200:
            resp = requests.post(url, json={"name": model_name})
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

def _cleanup_ollama_runners(target_model: Optional[str] = None):
    try:
        r = requests.get(f"{get_ollama_base_url()}/api/ps", headers={"Connection": "close"}, timeout=4)
        if r.status_code != 200:
            try:
                _cleanup_ollama_runners_cli(target_model)
            except Exception:
                pass
            return
        data = r.json()
        items = data.get("models") if isinstance(data, dict) else (data if isinstance(data, list) else [])
        for m in items if isinstance(items, list) else []:
            pid = m.get("id") if isinstance(m, dict) else None
            name = (m.get("model") or m.get("name")) if isinstance(m, dict) else None
            
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
                requests.post(f"{get_ollama_base_url()}/api/stop", json=payload, headers={"Connection": "close"}, timeout=3)
            except Exception:
                pass
        try:
            _cleanup_ollama_runners_cli(target_model)
        except Exception:
            pass
    except Exception:
        pass

def _cleanup_ollama_runners_cli(target_model: Optional[str] = None) -> bool:
    try:
        def _find_ollama_bin() -> List[str]:
            cands: List[str] = []
            for k in ("OLLAMA_BIN", "OLLAMA_EXE"):
                v = os.getenv(k)
                if v:
                    cands.append(v)
            try:
                la = os.getenv("LOCALAPPDATA")
                if la:
                    p = os.path.join(la, "Programs", "Ollama", "ollama.exe")
                    cands.append(p)
            except Exception:
                pass
            cands.append(r"C:\\Program Files\\Ollama\\ollama.exe")
            cands.append("ollama")
            return cands
        env = os.environ.copy()
        data = None
        for cmd in _find_ollama_bin():
            try:
                p = subprocess.run([cmd, "ps", "--json"], capture_output=True, text=True, timeout=4, env=env)
                if p.returncode != 0:
                    continue
                out = (p.stdout or "").strip()
                if not out:
                    data = {"models": []}
                    break
                data = json.loads(out)
                break
            except Exception:
                continue
        if data is None:
            return False
        items = data.get("models") if isinstance(data, dict) else (data if isinstance(data, list) else [])
        if not isinstance(items, list):
            return True
        for m in items:
            name = None
            if isinstance(m, dict):
                name = (m.get("model") or m.get("name"))
            if not name:
                continue
                
            # Skip target model
            if target_model:
                if name == target_model or name.startswith(target_model + ":") or target_model.startswith(name + ":"):
                    continue

            for cmd in _find_ollama_bin():
                try:
                    subprocess.run([cmd, "stop", name], capture_output=True, text=True, timeout=4, env=env)
                    break
                except Exception:
                    continue
        return True
    except Exception:
        return False

def _list_http():
    try:
        r = requests.get(f"{get_ollama_base_url()}/api/ps", headers={"Connection": "close"}, timeout=4)
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

def _stop_http(name=None, mid=None):
    try:
        payload = {}
        if name:
            payload["model"] = name
        if mid:
            payload["id"] = mid
        if not payload:
            return False
        requests.post(f"{get_ollama_base_url()}/api/stop", json=payload, headers={"Connection": "close"}, timeout=3)
        return True
    except Exception:
        return False

def _list_cli():
    try:
        env = os.environ.copy()
        p = subprocess.run(["ollama", "ps", "--json"], capture_output=True, text=True, timeout=4, env=env)
        if p.returncode != 0:
            return []
        s = (p.stdout or "").strip()
        if not s:
            return []
        data = json.loads(s)
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

def _stop_cli(name):
    try:
        if not name:
            return False
        env = os.environ.copy()
        subprocess.run(["ollama", "stop", name], capture_output=True, text=True, timeout=4, env=env)
        return True
    except Exception:
        return False

def _list_tags():
    try:
        r = requests.get(f"{get_ollama_base_url()}/api/tags", headers={"Connection": "close"}, timeout=4)
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

def _candidate_names():
    names = set()
    for x in _list_http():
        if x.get("name"):
            names.add(x.get("name"))
    for x in _list_cli():
        if x.get("name"):
            names.add(x.get("name"))
    for n in _list_tags():
        names.add(n)
    return list(names)

def _cleanup_all_exact(max_passes=3, wait_s=0.5):
    ok = False
    for _ in range(max_passes):
        for r in _list_http():
            _stop_http(r.get("name"), r.get("id"))
        for r in _list_cli():
            _stop_cli(r.get("name"))
        for name in _candidate_names():
            _stop_http(name, None)
            _stop_cli(name)
        time.sleep(wait_s)
        if not _list_http() and not _list_cli():
            ok = True
            break
    return ok

def _cleanup_model_exact(name, max_passes=2, wait_s=0.3):
    ok = False
    for _ in range(max_passes):
        _stop_http(name, None)
        _stop_cli(name)
        time.sleep(wait_s)
        names = [x.get("name") for x in _list_http()] + [x.get("name") for x in _list_cli()]
        if name not in names:
            ok = True
            break
    return ok

def _background_cleanup_model(name: str):
    try:
        _cleanup_model_exact(name, max_passes=3, wait_s=0.5)
    except Exception:
        pass

# Pydantic models for request/response
class ChatRequest(BaseModel):
    model: str
    prompt: Optional[str] = None
    messages: Optional[List[Dict[str, str]]] = None
    stream: bool = False
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None
    system: Optional[str] = None
    images: Optional[List[str]] = None  # List of base64 encoded images or file paths
    cleanup_after: Optional[bool] = True

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
    cleanup_after: Optional[bool] = True

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
        models_status = "running" if response.status_code == 200 else "not responding"
        # Report bootstrap state and missing models
        with _BOOTSTRAP_LOCK:
            bs = {
                'in_progress': BOOTSTRAP_STATE['in_progress'],
                'completed': BOOTSTRAP_STATE['completed'],
                'required': BOOTSTRAP_STATE['required'],
                'missing': BOOTSTRAP_STATE['missing'],
                'errors': BOOTSTRAP_STATE['errors'],
            }
        return {
            "status": "healthy" if response.status_code == 200 else "unhealthy",
            "ollama_server": models_status,
            "models_ready": bs['completed'] and len(bs['missing']) == 0,
            "bootstrap": bs,
        }
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
async def chat_with_model(request: ChatRequest, background_tasks: BackgroundTasks):
    """Chat with an Ollama model"""
    try:
        try:
            _cleanup_ollama_runners(request.model)
        except Exception:
            pass
        # Ensure requested model is available
        try:
            ensure_model_present(request.model)
        except Exception:
            pass
        used_chat = False
        if request.images and len(request.images) > 0:
            if is_vision_model(request.model):
                processed_images = process_images(request.images)
                message = {"role": "user", "content": request.prompt, "images": processed_images}
                messages = []
                if request.system:
                    messages.append({"role": "system", "content": request.system})
                messages.append(message)
                payload = {"model": request.model, "messages": messages, "stream": request.stream, "keep_alive": 60, "options": {"num_predict": -1}}
                if request.temperature is not None:
                    payload["options"]["temperature"] = request.temperature
                response = requests.post(f"{get_ollama_base_url()}/api/chat", json=payload, headers={"Connection": "close"})
                used_chat = True
            else:
                payload = {"model": request.model, "prompt": request.prompt, "stream": request.stream, "keep_alive": 60, "options": {"num_predict": -1}}
                if request.temperature is not None:
                    payload["options"]["temperature"] = request.temperature
                if request.system is not None:
                    payload["system"] = request.system
                response = requests.post(f"{get_ollama_base_url()}/api/generate", json=payload, headers={"Connection": "close"})
        else:
            payload = {"model": request.model, "prompt": request.prompt, "stream": request.stream, "keep_alive": 60, "options": {"num_predict": -1}}
            if request.temperature is not None:
                payload["options"]["temperature"] = request.temperature
            if request.system is not None:
                payload["system"] = request.system
            response = requests.post(f"{get_ollama_base_url()}/api/generate", json=payload, headers={"Connection": "close"})
        
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
                    gp = {"model": request.model, "prompt": request.prompt, "stream": False, "keep_alive": 60, "options": {"num_predict": -1}}
                    if request.temperature is not None:
                        gp["options"]["temperature"] = request.temperature
                    if request.system is not None:
                        gp["system"] = request.system
                    fr = requests.post(f"{get_ollama_base_url()}/api/generate", json=gp, headers={"Connection": "close"})
                    fr.raise_for_status()
                    fres = fr.json()
                    response_text = fres.get("response") or fres.get("text") or ""
                else:
                    msg = [{"role": "user", "content": request.prompt}]
                    if request.system:
                        msg.insert(0, {"role": "system", "content": request.system})
                    cp = {"model": request.model, "messages": msg, "stream": False, "keep_alive": 60, "options": {"num_predict": -1}}
                    if request.temperature is not None:
                        cp["options"]["temperature"] = request.temperature
                    fr = requests.post(f"{get_ollama_base_url()}/api/chat", json=cp, headers={"Connection": "close"})
                    fr.raise_for_status()
                    fres = fr.json()
                    response_text = (fres.get("message", {}) or {}).get("content") or fres.get("response") or fres.get("text") or ""
            except Exception:
                response_text = response_text
        
        resp_obj = {
            "response": response_text,
            "model": result.get("model", request.model),
            "created_at": result.get("created_at", ""),
            "done": result.get("done", True),
            "total_duration": result.get("total_duration"),
            "load_duration": result.get("load_duration"),
            "prompt_eval_count": result.get("prompt_eval_count"),
            "eval_count": result.get("eval_count")
        }
        if request.cleanup_after:
            background_tasks.add_task(_background_cleanup_model, request.model)
        return resp_obj
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
async def generate_text(request: ChatRequest, background_tasks: BackgroundTasks):
    """Generate text with an Ollama model (alias for chat endpoint)"""
    return await chat_with_model(request, background_tasks)

@app.post("/generate_stream")
async def generate_stream(request: ChatRequest):
    try:
        payload = {"model": request.model, "prompt": request.prompt, "stream": True, "keep_alive": 60, "options": {"num_predict": -1}}
        if request.temperature is not None:
            payload["options"]["temperature"] = request.temperature
        if request.system is not None:
            payload["system"] = request.system
        r = requests.post(f"{get_ollama_base_url()}/api/generate", json=payload, headers={"Connection": "close"}, stream=True)
        r.raise_for_status()
        def _iter():
            accumulated = ""
            for line in r.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    chunk = obj.get("response") or obj.get("text") or (obj.get("message", {}) or {}).get("content")
                    if chunk:
                        accumulated += chunk
                        yield json.dumps({"text": accumulated}) + "\n"
                except Exception:
                    continue
        return StreamingResponse(_iter(), media_type="application/x-ndjson")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/embeddings", response_model=EmbeddingsResponse)
async def get_embeddings(request: EmbeddingsRequest, background_tasks: BackgroundTasks):
    """Return embeddings using Ollama's embeddings endpoint.

    Accepts a list of input strings and returns a list of embedding vectors.
    """
    try:
        try:
            _cleanup_ollama_runners(request.model)
        except Exception:
            pass
        # Ensure embedding model is available
        try:
            ensure_model_present(request.model)
        except Exception:
            pass

        # Sanitize inputs: ensure strings and strip whitespace
        inputs: List[str] = []
        for x in request.inputs:
            try:
                s = str(x)
            except Exception:
                s = ""
            s = s if s is not None else ""
            inputs.append(s)

        # If all inputs are empty, return empty embeddings
        if not inputs or all((not s) or (s.strip() == "") for s in inputs):
            print("[DEBUG] Embeddings: No valid inputs provided")
            return {"embeddings": []}
            
        print(f"[DEBUG] Embeddings: Processing {len(inputs)} inputs for model {request.model}")
        if inputs:
            print(f"[DEBUG] Embeddings: Input 0 preview: {inputs[0][:50]}...")

        # Prepare payload for Ollama embeddings API
        # Ollama expects { model: <model>, input: <string or [strings]> }
        multiple = len(inputs) > 1
        val = inputs if multiple else inputs[0]
        ollama_payload: Dict[str, Any] = {
            "model": request.model,
            "input": val,
            "keep_alive": 60,
        }
        # Optional fields if needed in future
        if request.normalize is not None:
            ollama_payload["normalize"] = request.normalize
        if request.pooling is not None:
            ollama_payload["pooling"] = request.pooling

        print(f"[DEBUG] Embeddings: Sending payload to Ollama: {json.dumps(ollama_payload)[:200]}...")

        # Try /api/embed first (newer endpoint)
        url = f"{get_ollama_base_url()}/api/embed"
        try:
            response = requests.post(url, json=ollama_payload, headers={"Connection": "close"})
            if response.status_code == 404:
                 # Fallback to /api/embeddings (legacy)
                 # Some older/custom Ollama versions require "prompt" instead of "input"
                 ollama_payload["prompt"] = val
                 url = f"{get_ollama_base_url()}/api/embeddings"
                 response = requests.post(url, json=ollama_payload, headers={"Connection": "close"})
        except requests.exceptions.RequestException:
            # If /api/embed fails completely (e.g. connection error), try /api/embeddings
             ollama_payload["prompt"] = val
             url = f"{get_ollama_base_url()}/api/embeddings"
             response = requests.post(url, json=ollama_payload, headers={"Connection": "close"})

        print(f"[DEBUG] Embeddings: Request to {url} returned status {response.status_code}")
        try:
            print(f"[DEBUG] Embeddings: Raw response body: {response.text[:500]}")
        except Exception:
            pass
            
        response.raise_for_status()
        data = response.json()
        
        if isinstance(data, dict):
             print(f"[DEBUG] Embeddings: Response keys: {list(data.keys())}")

        # Normalize Ollama response variants robustly
        def _normalize_response(payload_inputs_count: int, resp_data: Any) -> List[List[float]]:
            if isinstance(resp_data, dict):
                # Preferred batch format
                if "embeddings" in resp_data and isinstance(resp_data["embeddings"], list):
                    return resp_data["embeddings"]
                # Single-vector format
                if "embedding" in resp_data and isinstance(resp_data["embedding"], list):
                    # If we sent multiple inputs but received a single embedding,
                    # re-query per-input to ensure correct cardinality.
                    if payload_inputs_count > 1:
                        print("[DEBUG] Embeddings: Received single embedding for multiple inputs, falling back to per-input")
                        return _query_per_input(inputs, request.model)
                    return [resp_data["embedding"]]
                # Unexpected dict format
                print(f"[ERROR] Embeddings: Unexpected dict format: {resp_data.keys()}")
                raise HTTPException(status_code=500, detail="Unexpected embeddings dict format")
            # Some servers may return a list directly
            if isinstance(resp_data, list):
                if resp_data and isinstance(resp_data[0], float):
                    # Single embedding vector
                    if payload_inputs_count > 1:
                         print("[DEBUG] Embeddings: Received single list embedding for multiple inputs, falling back to per-input")
                         return _query_per_input(inputs, request.model)
                    return [resp_data]
                if resp_data and isinstance(resp_data[0], list):
                    return resp_data
            print(f"[ERROR] Embeddings: Invalid response type: {type(resp_data)}")
            raise HTTPException(status_code=500, detail="Invalid embeddings response type")

        def _query_per_input(texts: List[str], model_name: str) -> List[List[float]]:
            out: List[List[float]] = []
            for i, t in enumerate(texts):
                # Try /api/embed first
                sub_payload = {"model": model_name, "input": t, "keep_alive": 0}
                sub_url = f"{get_ollama_base_url()}/api/embed"
                sub_resp = requests.post(sub_url, json=sub_payload, headers={"Connection": "close"})
                
                if sub_resp.status_code == 404 or sub_resp.status_code != 200:
                     # Fallback
                     sub_payload["prompt"] = t
                     sub_url = f"{get_ollama_base_url()}/api/embeddings"
                     sub_resp = requests.post(sub_url, json=sub_payload, headers={"Connection": "close"})
                
                if sub_resp.status_code != 200:
                    print(f"[ERROR] Embeddings: Per-input request {i} failed: {sub_resp.status_code}")
                    raise HTTPException(status_code=500, detail=f"Per-input request failed: {sub_resp.status_code}")
                    
                sub_data = sub_resp.json()
                if isinstance(sub_data, dict):
                    if "embeddings" in sub_data and isinstance(sub_data["embeddings"], list) and sub_data["embeddings"]:
                         out.append(sub_data["embeddings"][0])
                    elif "embedding" in sub_data and isinstance(sub_data["embedding"], list):
                        out.append(sub_data["embedding"])
                    else:
                         print(f"[ERROR] Embeddings: Per-input {i} invalid dict: {sub_data.keys()}")
                         raise HTTPException(status_code=500, detail="Sub-embedding response format invalid")
                elif isinstance(sub_data, list) and sub_data and isinstance(sub_data[0], float):
                    out.append(sub_data)
                else:
                    print(f"[ERROR] Embeddings: Per-input {i} invalid type: {type(sub_data)}")
                    raise HTTPException(status_code=500, detail="Sub-embedding response format invalid")
            return out

        embeddings = _normalize_response(len(inputs), data)
        
        print(f"[DEBUG] Embeddings: Generated {len(embeddings)} vectors")
        if embeddings:
             print(f"[DEBUG] Embeddings: First vector length: {len(embeddings[0]) if embeddings[0] else 0}")
        
        # Ensure cardinality and non-empty vectors
        if len(embeddings) != len(inputs) or any((not v) or (isinstance(v, list) and len(v) == 0) for v in embeddings):
            print("[DEBUG] Embeddings: Validation failed (empty vectors or count mismatch), retrying per-input")
            embeddings = _query_per_input(inputs, request.model)
            # If still empty, raise error to indicate unusable embeddings
            if len(embeddings) != len(inputs) or any((not v) or (isinstance(v, list) and len(v) == 0) for v in embeddings):
                print(f"[ERROR] Embeddings: Final validation failed. Inputs: {len(inputs)}, Vectors: {len(embeddings)}")
                valid_count = sum(1 for v in embeddings if v and len(v) > 0)
                print(f"[ERROR] Embeddings: Valid vectors: {valid_count}")
                raise HTTPException(status_code=500, detail="Embeddings endpoint returned empty vectors")

        if request.cleanup_after:
            background_tasks.add_task(_background_cleanup_model, request.model)
        return {"embeddings": embeddings}
    except HTTPException as e:
        raise e
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=503, detail=f"Failed to fetch embeddings from Ollama: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.post("/cleanup")
async def cleanup_endpoint(body: Dict[str, Any]):
    try:
        all_flag = bool(body.get("all", False))
        model = body.get("model")
        retries = int(body.get("retries", 3))
        wait_s = float(body.get("wait", 0.5))
        if all_flag or not model:
            ok = _cleanup_all_exact(max_passes=retries, wait_s=wait_s)
            return {"ok": ok}
        else:
            ok = _cleanup_model_exact(model, max_passes=retries, wait_s=wait_s)
            return {"ok": ok, "model": model}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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

@app.get("/bootstrap/status")
async def bootstrap_status():
    """Return current bootstrap state including required and missing models."""
    try:
        with _BOOTSTRAP_LOCK:
            return {
                'in_progress': BOOTSTRAP_STATE['in_progress'],
                'completed': BOOTSTRAP_STATE['completed'],
                'required': BOOTSTRAP_STATE['required'],
                'missing': BOOTSTRAP_STATE['missing'],
                'errors': BOOTSTRAP_STATE['errors'],
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class ComoRAGReasonRequest(BaseModel):
    query: str
    model: str
    max_cycles: Optional[int] = 5
    memory_size: Optional[int] = 1000
    context: Optional[str] = None
    images: Optional[List[str]] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None

class ComoRAGAddMemoryRequest(BaseModel):
    content: str
    source: str = "external"
    metadata: Optional[Dict[str, Any]] = None
    image_data: Optional[str] = None

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

@app.post("/comorag/memory")
async def add_comorag_memory(request: ComoRAGAddMemoryRequest):
    try:
        global COMORAG_ENGINE
        if COMORAG_ENGINE is None:
            COMORAG_ENGINE = ComoRAGEngine()
            await COMORAG_ENGINE.initialize()
        elif not COMORAG_ENGINE.is_ready():
            await COMORAG_ENGINE.initialize()
            
        await COMORAG_ENGINE.add_memory(
            content=request.content,
            source=request.source,
            metadata=request.metadata,
            image_data=request.image_data
        )
        return {"status": "memory_added"}
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

if __name__ == "__main__":
    # Get Tailscale IP from environment variable, fallback to all interfaces
    tailscale_ip = os.getenv("TAILSCALE_IP", "0.0.0.0")
    port = int(os.getenv("API_PORT", "8000"))
    
    print(f"Starting Ollama API Gateway on {tailscale_ip}:{port}")
    print(f"Access via Tailscale: http://{tailscale_ip}:{port}")
    # Start model bootstrap in background so API binds immediately
    def _run_bootstrap_async():
        try:
            print("[BOOTSTRAP] Starting background model bootstrap...")
            bootstrap_required_models()
            print("[BOOTSTRAP] Background model bootstrap completed.")
        except Exception as e:
            print(f"[BOOTSTRAP] Background bootstrap error: {e}")
    try:
        threading.Thread(target=_run_bootstrap_async, daemon=True).start()
    except Exception as e:
        print(f"[BOOTSTRAP] Failed to start background bootstrap thread: {e}")
    
    uvicorn.run(app, host=tailscale_ip, port=port, reload=False, log_level="info")
