import atexit
import base64
import functools
import json
import logging
import os
import re
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Union

import requests
import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# Ensure project root is in sys.path so 'from AI.xxx' imports work
# When api.py runs as a subprocess, sys.path[0] is the AI/ directory
# but we need LoOper/ (the parent) to resolve AI.config_loader etc.
_api_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_api_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# Unified telemetry: records propagate to the root dispatch (see
# logging_setup.py).  When api.py runs as its own subprocess it installs the
# dispatch with the parent's session id (ARROW_SESSION_ID), so API logs join
# the same session md instead of a separate file.
_api_logger = logging.getLogger("api.llamacpp")
_api_logger.setLevel(logging.DEBUG)
try:
    from logging_setup import setup_logging, uvicorn_log_config

    setup_logging()
except Exception:
    pass


_RE_CHAT_CONTROL = re.compile(
    r"""
    (?: <\|im_end\|>
      | <\|im_start\|>
      | </s>
      | <\|endoftext\|>
      | <\|eot_id\|>
      | <\|end\|>
    )
    \s*
    $
    """,
    re.VERBOSE,
)


def _sanitize_llamacpp_response(text: str) -> str:
    if not text:
        return text
    prev = None
    result = text
    while result != prev:
        prev = result
        result = _RE_CHAT_CONTROL.sub("", result).rstrip()
    return result


def _subprocess_no_window_kwargs():
    if os.name != "nt":
        return {}
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
    startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
    return {"creationflags": creationflags, "startupinfo": startupinfo}


# Use absolute package imports instead of path hacking
# OllamaEngine from OverWatch (optional — depends on aiohttp, psutil)
try:
    from AI.OverWatch.api.ollama_engine import OllamaEngine
except ImportError:
    try:
        from OverWatch.api.ollama_engine import OllamaEngine
    except ImportError:
        print("[WARNING] OverWatch OllamaEngine not available (missing deps)")
        OllamaEngine = None

# Import llama.cpp engine (optional, graceful fallback)
try:
    from AI.llama_cpp_engine import LlamaCppEngine
    from AI.llama_cpp_engine import LlamaCppManager as llama_cpp_manager
    from AI.llama_cpp_engine import LLAMA_BIN_NAMES, pid_image_name
except ImportError:
    try:
        from llama_cpp_engine import LlamaCppEngine
        from llama_cpp_engine import LlamaCppManager as llama_cpp_manager
        from llama_cpp_engine import LLAMA_BIN_NAMES, pid_image_name
    except ImportError:
        # llama.cpp not available, create stub classes
        print("[WARNING] llama.cpp engine not available, using stub")
        LLAMA_BIN_NAMES = set()

        def pid_image_name(pid: int) -> str:
            return ""

        class LlamaCppEngine:
            def __init__(self, config=None):
                self.config = config or {}
                self.model_path = (config or {}).get("model_path", "")
                self.is_running = False
                self.server_url = "http://127.0.0.1:8081"

            def start_server(self, blocking=False):
                return False

            def stop_server(self):
                pass

            def generate(self, *args, **kwargs):
                return {"error": "llama.cpp not available", "response": ""}

            def chat(self, *args, **kwargs):
                return {"error": "llama.cpp not available", "response": ""}

            def get_server_info(self):
                return {"status": "not_available"}

        class llama_cpp_manager:
            @staticmethod
            def create_engine(*args, **kwargs):
                return None

            @staticmethod
            def get_engine(*args, **kwargs):
                return None

            @staticmethod
            def stop_all():
                pass


import asyncio
import threading
from typing import Set

# Serializes the WHOLE llama.cpp engine lifecycle: every entry point that
# ensures (stop/start/reinit), generates, or schedules the model unload takes
# this lock, plus the background unloader itself.  The architecture is strictly
# sequential load -> use -> unload — a reinit can never interleave with an
# in-flight generation, and the unloader can never stop a server a running
# request still needs.  Without it, concurrent bursts made model instances race
# each other and blew the RAM budget (see the lifecycle section below).
_LLAMACPP_LIFECYCLE_LOCK = threading.RLock()


def _engine_locked(fn):
    """Acquire the engine lifecycle lock for the whole coroutine.

    Serializes ensure -> generate -> unload-scheduling against the background
    unloader and against the other engine entry points (/llamacpp/start,
    /llamacpp/stop, /llamacpp/status, node /llamacpp/generate calls).
    """

    @functools.wraps(fn)
    async def _wrapper(*args, **kwargs):
        with _LLAMACPP_LIFECYCLE_LOCK:
            return await fn(*args, **kwargs)

    return _wrapper


app = FastAPI(
    title="Ollama API Gateway",
    description="Local API gateway for Ollama models",
    version="1.0.0",
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Ollama server configuration - use environment variables
def get_ollama_base_url():
    """Get Ollama base URL from environment variables"""
    ollama_host = os.getenv("OLLAMA_HOST", "127.0.0.1")
    ollama_port = os.getenv("OLLAMA_PORT", "11434")
    return f"http://{ollama_host}:{ollama_port}".rstrip("/")


OLLAMA_BASE_URL = get_ollama_base_url()

# Vision model detection
VISION_MODELS = [
    "llava",
    "bakllava",
    "moondream",
    "llava-llama3",
    "llava-phi3",
    "minicpm-v",
]

VISION_CAPABLE_CACHE: Dict[str, bool] = {}

# --- Model bootstrap and auto-pull helpers ---
HARDCODED_REQUIRED_EMBED_MODELS: List[str] = []
HARDCODED_REQUIRED_LLM_MODELS: List[str] = []
HARDCODED_REQUIRED_VISION_MODELS: List[str] = []


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
        r = requests.post(
            f"{get_ollama_base_url()}/api/pull", json={"name": model_name}, stream=True
        )
        if r.status_code != 200:
            # Some older servers only accept GET with params
            r = requests.get(
                f"{get_ollama_base_url()}/api/pull",
                params={"name": model_name},
                stream=True,
            )
        if r.status_code != 200:
            print(
                f"[BOOTSTRAP] Failed to start pull for '{model_name}': {r.status_code}"
            )
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
        present = any(
            n.startswith(model_name) or model_name.startswith(n) for n in names
        )
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
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return parts or defaults


def _collect_required_models() -> List[str]:
    all_required: List[str] = []
    all_required.extend(HARDCODED_REQUIRED_EMBED_MODELS)
    all_required.extend(HARDCODED_REQUIRED_LLM_MODELS)
    all_required.extend(HARDCODED_REQUIRED_VISION_MODELS)

    seen = set()
    deduped: List[str] = []
    for m in all_required:
        key = (m or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            deduped.append(m)
    return deduped


# Bootstrap state for health/status endpoints
BOOTSTRAP_STATE: Dict[str, Any] = {
    "in_progress": False,
    "completed": False,
    "required": [],
    "missing": [],
    "errors": [],
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
        BOOTSTRAP_STATE["in_progress"] = True
        BOOTSTRAP_STATE["completed"] = False
        BOOTSTRAP_STATE["required"] = required
        BOOTSTRAP_STATE["missing"] = _refresh_missing(required)
        BOOTSTRAP_STATE["errors"] = []

    if not required:
        with _BOOTSTRAP_LOCK:
            BOOTSTRAP_STATE["in_progress"] = False
            BOOTSTRAP_STATE["completed"] = True
        print("[BOOTSTRAP] No required models configured.")
        return

    print(f"[BOOTSTRAP] Ensuring required models are available: {', '.join(required)}")
    for m in required:
        try:
            ensure_model_present(m)
        except Exception as e:
            print(f"[BOOTSTRAP] Failed ensuring model '{m}': {e}")
            with _BOOTSTRAP_LOCK:
                BOOTSTRAP_STATE["errors"].append(str(e))
        # Update missing after each attempt
        with _BOOTSTRAP_LOCK:
            BOOTSTRAP_STATE["missing"] = _refresh_missing(required)

    with _BOOTSTRAP_LOCK:
        BOOTSTRAP_STATE["in_progress"] = False
        BOOTSTRAP_STATE["completed"] = len(BOOTSTRAP_STATE["missing"]) == 0
    if BOOTSTRAP_STATE["completed"]:
        print("[BOOTSTRAP] All required models are present.")
    else:
        print(
            f"[BOOTSTRAP] Missing models after bootstrap: {', '.join(BOOTSTRAP_STATE['missing'])}"
        )


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
    

    for i, image in enumerate(images):
        try:
            # Check if it's a file path that exists
            if os.path.exists(image):
                # File path - read and encode to raw base64
                
                with open(image, "rb") as img_file:
                    img_data = img_file.read()
                    base64_img = base64.b64encode(img_data).decode("utf-8")
                    processed_images.append(base64_img)
                    print(
                        f"[DEBUG] Image {i + 1}: Encoded to base64 ({len(base64_img)} chars)"
                    )
            elif image.startswith("data:image/"):
                # Data URL format - extract base64 part
                
                base64_part = image.split(",", 1)[1] if "," in image else image
                processed_images.append(base64_part)
                print(
                    f"[DEBUG] Image {i + 1}: Extracted base64 part ({len(base64_part)} chars)"
                )
            else:
                # Assume it's already raw base64 encoded
                print(
                    f"[DEBUG] Image {i + 1}: Assuming raw base64 ({len(image)} chars)"
                )
                processed_images.append(image)
        except Exception as e:
            print(f"[ERROR] Failed to process image {image}: {str(e)}")
            raise HTTPException(
                status_code=400, detail=f"Failed to process image {image}: {str(e)}"
            )

    print(f"[DEBUG] Successfully processed {len(processed_images)} images ")
    return processed_images


def _cleanup_ollama_runners(target_model: Optional[str] = None):
    try:
        r = requests.get(
            f"{get_ollama_base_url()}/api/ps",
            headers={"Connection": "close"},
            timeout=4,
        )
        if r.status_code != 200:
            try:
                _cleanup_ollama_runners_cli(target_model)
            except Exception:
                pass
            return
        data = r.json()
        items = (
            data.get("models")
            if isinstance(data, dict)
            else (data if isinstance(data, list) else [])
        )
        for m in items if isinstance(items, list) else []:
            pid = m.get("id") if isinstance(m, dict) else None
            name = (m.get("model") or m.get("name")) if isinstance(m, dict) else None

            # Skip target model
            if target_model and name:
                if (
                    name == target_model
                    or name.startswith(target_model + ":")
                    or target_model.startswith(name + ":")
                ):
                    continue

            payload: Dict[str, Any] = {}
            if pid:
                payload["id"] = pid
            if name:
                payload["model"] = name
            if not payload:
                continue
            try:
                requests.post(
                    f"{get_ollama_base_url()}/api/stop",
                    json=payload,
                    headers={"Connection": "close"},
                    timeout=3,
                )
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
                p = subprocess.run(
                    [cmd, "ps", "--json"],
                    capture_output=True,
                    text=True,
                    timeout=4,
                    env=env,
                    **_subprocess_no_window_kwargs(),
                )
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
        items = (
            data.get("models")
            if isinstance(data, dict)
            else (data if isinstance(data, list) else [])
        )
        if not isinstance(items, list):
            return True
        for m in items:
            name = None
            if isinstance(m, dict):
                name = m.get("model") or m.get("name")
            if not name:
                continue

            # Skip target model
            if target_model:
                if (
                    name == target_model
                    or name.startswith(target_model + ":")
                    or target_model.startswith(name + ":")
                ):
                    continue

            for cmd in _find_ollama_bin():
                try:
                    subprocess.run(
                        [cmd, "stop", name],
                        capture_output=True,
                        text=True,
                        timeout=4,
                        env=env,
                        **_subprocess_no_window_kwargs(),
                    )
                    break
                except Exception:
                    continue
        return True
    except Exception:
        return False


def _list_http():
    try:
        r = requests.get(
            f"{get_ollama_base_url()}/api/ps",
            headers={"Connection": "close"},
            timeout=4,
        )
        if r.status_code != 200:
            return []
        data = r.json()
        items = (
            data.get("models")
            if isinstance(data, dict)
            else (data if isinstance(data, list) else [])
        )
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
        requests.post(
            f"{get_ollama_base_url()}/api/stop",
            json=payload,
            headers={"Connection": "close"},
            timeout=10,
        )
        return True
    except Exception:
        return False


def _list_cli():
    try:
        env = os.environ.copy()
        p = subprocess.run(
            ["ollama", "ps", "--json"],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            **_subprocess_no_window_kwargs(),
        )
        if p.returncode != 0:
            return []
        s = (p.stdout or "").strip()
        if not s:
            return []
        data = json.loads(s)
        items = (
            data.get("models")
            if isinstance(data, dict)
            else (data if isinstance(data, list) else [])
        )
        res = []
        if isinstance(items, list):
            for m in items:
                name = (
                    (m.get("model") or m.get("name")) if isinstance(m, dict) else None
                )
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
        subprocess.run(
            ["ollama", "stop", name],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            **_subprocess_no_window_kwargs(),
        )
        return True
    except Exception:
        return False


def _list_tags():
    try:
        r = requests.get(
            f"{get_ollama_base_url()}/api/tags",
            headers={"Connection": "close"},
            timeout=4,
        )
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
        names = [x.get("name") for x in _list_http()] + [
            x.get("name") for x in _list_cli()
        ]
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
    raw_completion: Optional[bool] = False  # bypass chat template (probe generation)
    chat_template_kwargs: Optional[Dict[str, object]] = None  # e.g. {"enable_thinking": False}
    cleanup_after: Optional[bool] = True
    # Llama.cpp specific parameters
    use_llamacpp: Optional[bool] = False
    gpu_layers: Optional[int] = None
    threads: Optional[int] = None
    context_size: Optional[int] = None
    repeat_penalty: Optional[float] = None  # forwarded to llama-server sampling
    # Continuation rounds for a truncated generation: short structured callers
    # (consolidation hooks) send 1 so a rambling small model is not continued
    # for minutes; None keeps the engine default.
    max_rounds: Optional[int] = None


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
    port = os.getenv("API_PORT", "8000")

    return {
        "message": "Ollama API Gateway",
        "version": "1.0.0",
        "server_info": {
            "host": "localhost",
            "port": port,
            "access_url": f"http://localhost:{port}",
        },
        "environment_variables": {
            "API_PORT": "API server port (default: 8000)",
        },
        "endpoints": {
            "/models": "GET - List available models",
            "/vision-models": "GET - List available vision-enabled models",
            "/chat": "POST - Chat with a model (supports images for vision models)",
            "/generate": "POST - Generate text with a model (supports images for vision models)",
            "/health": "GET - Check Ollama server health",
        },
        "vision_support": {
            "description": "Vision models can process images along with text prompts",
            "supported_formats": ["base64 encoded images", "local file paths"],
            "example_models": [
                "llava",
                "bakllava",
                "moondream",
                "llava-llama3",
                "llava-phi3",
            ],
        },
    }


@app.get("/health")
async def health_check():
    """Check if Ollama server is running"""
    try:
        response = requests.get(f"{get_ollama_base_url()}/api/tags", timeout=5)
        models_status = "running" if response.status_code == 200 else "not responding"
        # Report bootstrap state and missing models
        with _BOOTSTRAP_LOCK:
            bs = {
                "in_progress": BOOTSTRAP_STATE["in_progress"],
                "completed": BOOTSTRAP_STATE["completed"],
                "required": BOOTSTRAP_STATE["required"],
                "missing": BOOTSTRAP_STATE["missing"],
                "errors": BOOTSTRAP_STATE["errors"],
            }
        return {
            "status": "healthy" if response.status_code == 200 else "unhealthy",
            "ollama_server": models_status,
            "models_ready": bs["completed"] and len(bs["missing"]) == 0,
            "bootstrap": bs,
        }
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=503, detail=f"Ollama server is not accessible: {str(e)}"
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
                    modified_at=str(m.get("modified_at", "")),
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
                    models.append(
                        ModelInfo(name=name, size=0, digest="", modified_at="")
                    )
        else:
            models = []

        return models

    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=503, detail=f"Failed to fetch models from Ollama: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


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

        # Structured messages format → route to Ollama /api/chat
        if request.messages:
            payload = {
                "model": request.model,
                "messages": request.messages,
                "stream": request.stream,
                "keep_alive": 60,
                "options": {"num_predict": -1},
            }
            if request.temperature is not None:
                payload["options"]["temperature"] = request.temperature
            response = requests.post(
                f"{get_ollama_base_url()}/api/chat",
                json=payload,
                headers={"Connection": "close"},
            )
            used_chat = True

        elif request.images and len(request.images) > 0:
            if is_vision_model(request.model):
                processed_images = process_images(request.images)
                message = {
                    "role": "user",
                    "content": request.prompt,
                    "images": processed_images,
                }
                messages_list = []
                if request.system:
                    messages_list.append({"role": "system", "content": request.system})
                messages_list.append(message)
                payload = {
                    "model": request.model,
                    "messages": messages_list,
                    "stream": request.stream,
                    "keep_alive": 60,
                    "options": {"num_predict": -1},
                }
                if request.temperature is not None:
                    payload["options"]["temperature"] = request.temperature
                response = requests.post(
                    f"{get_ollama_base_url()}/api/chat",
                    json=payload,
                    headers={"Connection": "close"},
                )
                used_chat = True
            else:
                payload = {
                    "model": request.model,
                    "prompt": request.prompt,
                    "stream": request.stream,
                    "keep_alive": 60,
                    "options": {"num_predict": -1},
                }
                if request.temperature is not None:
                    payload["options"]["temperature"] = request.temperature
                if request.system is not None:
                    payload["system"] = request.system
                response = requests.post(
                    f"{get_ollama_base_url()}/api/generate",
                    json=payload,
                    headers={"Connection": "close"},
                )
        else:
            payload = {
                "model": request.model,
                "prompt": request.prompt,
                "stream": request.stream,
                "keep_alive": 60,
                "options": {"num_predict": -1},
            }
            if request.temperature is not None:
                payload["options"]["temperature"] = request.temperature
            if request.system is not None:
                payload["system"] = request.system
            response = requests.post(
                f"{get_ollama_base_url()}/api/generate",
                json=payload,
                headers={"Connection": "close"},
            )

        response.raise_for_status()

        result = response.json()
        if used_chat:
            message = result.get("message", {})
            response_text = (
                message.get("content")
                or result.get("response")
                or result.get("text")
                or ""
            )
        else:
            response_text = result.get("response") or result.get("text") or ""
        if not response_text:
            try:
                if used_chat:
                    gp = {
                        "model": request.model,
                        "prompt": request.prompt,
                        "stream": False,
                        "keep_alive": 60,
                        "options": {"num_predict": -1},
                    }
                    if request.temperature is not None:
                        gp["options"]["temperature"] = request.temperature
                    if request.system is not None:
                        gp["system"] = request.system
                    fr = requests.post(
                        f"{get_ollama_base_url()}/api/generate",
                        json=gp,
                        headers={"Connection": "close"},
                    )
                    fr.raise_for_status()
                    fres = fr.json()
                    response_text = fres.get("response") or fres.get("text") or ""
                else:
                    msg = [{"role": "user", "content": request.prompt}]
                    if request.system:
                        msg.insert(0, {"role": "system", "content": request.system})
                    cp = {
                        "model": request.model,
                        "messages": msg,
                        "stream": False,
                        "keep_alive": 60,
                        "options": {"num_predict": -1},
                    }
                    if request.temperature is not None:
                        cp["options"]["temperature"] = request.temperature
                    fr = requests.post(
                        f"{get_ollama_base_url()}/api/chat",
                        json=cp,
                        headers={"Connection": "close"},
                    )
                    fr.raise_for_status()
                    fres = fr.json()
                    response_text = (
                        (fres.get("message", {}) or {}).get("content")
                        or fres.get("response")
                        or fres.get("text")
                        or ""
                    )
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
            "eval_count": result.get("eval_count"),
        }
        if request.cleanup_after:
            background_tasks.add_task(_background_cleanup_model, request.model)
        return resp_obj
    except HTTPException as e:
        # Preserve explicit HTTP errors raised above (e.g., 400 for unsupported vision)
        raise e
    except requests.exceptions.Timeout:
        raise HTTPException(
            status_code=408, detail="Request timeout - model took too long to respond"
        )
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=503, detail=f"Failed to communicate with Ollama: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.post("/generate")
async def generate_text(request: ChatRequest, background_tasks: BackgroundTasks):
    """Generate text with an Ollama model (alias for chat endpoint)"""
    return await chat_with_model(request, background_tasks)


@app.post("/generate_stream")
async def generate_stream(request: ChatRequest):
    try:
        payload = {
            "model": request.model,
            "prompt": request.prompt,
            "stream": True,
            "keep_alive": 60,
            "options": {"num_predict": -1},
        }
        if request.temperature is not None:
            payload["options"]["temperature"] = request.temperature
        if request.system is not None:
            payload["system"] = request.system
        r = requests.post(
            f"{get_ollama_base_url()}/api/generate",
            json=payload,
            headers={"Connection": "close"},
            stream=True,
        )
        r.raise_for_status()

        def _iter():
            accumulated = ""
            for line in r.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    chunk = (
                        obj.get("response")
                        or obj.get("text")
                        or (obj.get("message", {}) or {}).get("content")
                    )
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

        print(
            f"[DEBUG] Embeddings: Processing {len(inputs)} inputs for model {request.model}"
        )
        if inputs:
            print(f"[DEBUG] Embeddings: Input 0 preview: {inputs[0][:50]}...")

        # Prepare payload  embeddings API
        # Ollama expects { model: <model>, input: <string or [strings]> }
        multiple = len(inputs) > 1
        val = inputs if multiple else inputs[0]
        ollama_payload: Dict[str, Any] = {
            "model": request.model,
            "input": val,
            "keep_alive": 0,
        }
        # Optional fields if needed in future
        if request.normalize is not None:
            ollama_payload["normalize"] = request.normalize
        if request.pooling is not None:
            ollama_payload["pooling"] = request.pooling

        print(
            f"[DEBUG] Embeddings: Sending payload to Ollama: {json.dumps(ollama_payload)[:200]}..."
        )

        # Try /api/embed first (newer endpoint)
        url = f"{get_ollama_base_url()}/api/embed"
        try:
            response = requests.post(
                url, json=ollama_payload, headers={"Connection": "close"}
            )
            if response.status_code == 404:
                # Fallback to /api/embeddings (legacy)
                # Some older/custom Ollama versions require "prompt" instead of "input"
                ollama_payload["prompt"] = val
                url = f"{get_ollama_base_url()}/api/embeddings"
                response = requests.post(
                    url, json=ollama_payload, headers={"Connection": "close"}
                )
        except requests.exceptions.RequestException:
            # If /api/embed fails completely (e.g. connection error), try /api/embeddings
            ollama_payload["prompt"] = val
            url = f"{get_ollama_base_url()}/api/embeddings"
            response = requests.post(
                url, json=ollama_payload, headers={"Connection": "close"}
            )

        print(
            f"[DEBUG] Embeddings: Request to {url} returned status {response.status_code}"
        )
        try:
            print(f"[DEBUG] Embeddings: Raw response body: {response.text[:500]}")
        except Exception:
            pass

        response.raise_for_status()
        data = response.json()

        if isinstance(data, dict):
            print(f"[DEBUG] Embeddings: Response keys: {list(data.keys())}")

        # Normalize Ollama response variants robustly
        def _normalize_response(
            payload_inputs_count: int, resp_data: Any
        ) -> List[List[float]]:
            if isinstance(resp_data, dict):
                # Preferred batch format
                if "embeddings" in resp_data and isinstance(
                    resp_data["embeddings"], list
                ):
                    return resp_data["embeddings"]
                # Single-vector format
                if "embedding" in resp_data and isinstance(
                    resp_data["embedding"], list
                ):
                    # If we sent multiple inputs but received a single embedding,
                    # re-query per-input to ensure correct cardinality.
                    if payload_inputs_count > 1:
                        print(
                            "[DEBUG] Embeddings: Received single embedding for multiple inputs, falling back to per-input"
                        )
                        return _query_per_input(inputs, request.model)
                    return [resp_data["embedding"]]
                # Unexpected dict format
                print(f"[ERROR] Embeddings: Unexpected dict format: {resp_data.keys()}")
                raise HTTPException(
                    status_code=500, detail="Unexpected embeddings dict format"
                )
            # Some servers may return a list directly
            if isinstance(resp_data, list):
                if resp_data and isinstance(resp_data[0], float):
                    # Single embedding vector
                    if payload_inputs_count > 1:
                        print(
                            "[DEBUG] Embeddings: Received single list embedding for multiple inputs, falling back to per-input"
                        )
                        return _query_per_input(inputs, request.model)
                    return [resp_data]
                if resp_data and isinstance(resp_data[0], list):
                    return resp_data
            print(f"[ERROR] Embeddings: Invalid response type: {type(resp_data)}")
            raise HTTPException(
                status_code=500, detail="Invalid embeddings response type"
            )

        def _query_per_input(texts: List[str], model_name: str) -> List[List[float]]:
            out: List[List[float]] = []
            for i, t in enumerate(texts):
                # Try /api/embed first
                sub_payload = {"model": model_name, "input": t, "keep_alive": 0}
                sub_url = f"{get_ollama_base_url()}/api/embed"

                try:
                    sub_resp = requests.post(
                        sub_url,
                        json=sub_payload,
                        headers={"Connection": "close"},
                        timeout=10,
                    )
                except requests.exceptions.RequestException:
                    sub_resp = type(
                        "obj", (object,), {"status_code": 500, "json": lambda: {}}
                    )()

                if sub_resp.status_code == 404 or sub_resp.status_code != 200:
                    # Fallback
                    sub_payload["prompt"] = t
                    # Ensure we don't send 'input' to legacy endpoint as it might break it
                    if "input" in sub_payload:
                        del sub_payload["input"]
                    sub_url = f"{get_ollama_base_url()}/api/embeddings"
                    try:
                        sub_resp = requests.post(
                            sub_url,
                            json=sub_payload,
                            headers={"Connection": "close"},
                            timeout=10,
                        )
                    except requests.exceptions.RequestException as e:
                        print(
                            f"[ERROR] Embeddings: Per-input fallback request failed: {e}"
                        )
                        raise HTTPException(
                            status_code=500,
                            detail=f"Per-input fallback request failed: {str(e)}",
                        )

                if sub_resp.status_code != 200:
                    print(
                        f"[ERROR] Embeddings: Per-input request {i} failed: {sub_resp.status_code}"
                    )
                    raise HTTPException(
                        status_code=500,
                        detail=f"Per-input request failed: {sub_resp.status_code}",
                    )

                sub_data = sub_resp.json()
                if isinstance(sub_data, dict):
                    if (
                        "embeddings" in sub_data
                        and isinstance(sub_data["embeddings"], list)
                        and sub_data["embeddings"]
                    ):
                        out.append(sub_data["embeddings"][0])
                    elif "embedding" in sub_data and isinstance(
                        sub_data["embedding"], list
                    ):
                        out.append(sub_data["embedding"])
                    else:
                        print(
                            f"[ERROR] Embeddings: Per-input {i} invalid dict: {sub_data.keys()}"
                        )
                        raise HTTPException(
                            status_code=500,
                            detail="Sub-embedding response format invalid",
                        )
                elif (
                    isinstance(sub_data, list)
                    and sub_data
                    and isinstance(sub_data[0], float)
                ):
                    out.append(sub_data)
                else:
                    print(
                        f"[ERROR] Embeddings: Per-input {i} invalid type: {type(sub_data)}"
                    )
                    raise HTTPException(
                        status_code=500, detail="Sub-embedding response format invalid"
                    )
            return out

        embeddings = _normalize_response(len(inputs), data)

        print(f"[DEBUG] Embeddings: Generated {len(embeddings)} vectors")
        if embeddings:
            print(
                f"[DEBUG] Embeddings: First vector length: {len(embeddings[0]) if embeddings[0] else 0}"
            )

        # Ensure cardinality and non-empty vectors
        if len(embeddings) != len(inputs) or any(
            (not v) or (isinstance(v, list) and len(v) == 0) for v in embeddings
        ):
            print(
                "[DEBUG] Embeddings: Validation failed (empty vectors or count mismatch), retrying per-input"
            )
            embeddings = _query_per_input(inputs, request.model)
            # If still empty, raise error to indicate unusable embeddings
            if len(embeddings) != len(inputs) or any(
                (not v) or (isinstance(v, list) and len(v) == 0) for v in embeddings
            ):
                print(
                    f"[ERROR] Embeddings: Final validation failed. Inputs: {len(inputs)}, Vectors: {len(embeddings)}"
                )
                valid_count = sum(1 for v in embeddings if v and len(v) > 0)
                print(f"[ERROR] Embeddings: Valid vectors: {valid_count}")
                raise HTTPException(
                    status_code=500, detail="Embeddings endpoint returned empty vectors"
                )

        if request.cleanup_after:
            try:
                _cleanup_model_exact(request.model, max_passes=3, wait_s=0.5)
            except Exception:
                pass
        return {"embeddings": embeddings}
    except HTTPException as e:
        raise e
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=503, detail=f"Failed to fetch embeddings from Ollama: {str(e)}"
        )
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
                vision_models.append(
                    ModelInfo(
                        name=model_name,
                        size=model["size"],
                        digest=model["digest"],
                        modified_at=model["modified_at"],
                    )
                )

        return vision_models

    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=503, detail=f"Failed to fetch models from Ollama: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.get("/bootstrap/status")
async def bootstrap_status():
    """Return current bootstrap state including required and missing models."""
    try:
        with _BOOTSTRAP_LOCK:
            return {
                "in_progress": BOOTSTRAP_STATE["in_progress"],
                "completed": BOOTSTRAP_STATE["completed"],
                "required": BOOTSTRAP_STATE["required"],
                "missing": BOOTSTRAP_STATE["missing"],
                "errors": BOOTSTRAP_STATE["errors"],
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ========== Llama.cpp Endpoints ==========


def _find_free_port(start: int = 9090, max_attempts: int = 100) -> int:
    """Find an available TCP port starting from `start`."""
    import socket
    for port in range(start, start + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(('127.0.0.1', port))
                return port
            except OSError:
                continue
    _api_logger.error(
        "No available port found for llama.cpp server (scanned %d-%d)",
        start, start + max_attempts - 1,
    )
    raise HTTPException(
        status_code=503,
        detail=f"No available port found for llama.cpp server"
    )


def _port_held_by_foreign_process(port: int) -> bool:
    """True if *port* is being listened on by a non-llama.cpp process.

    Guards old-port reuse during engine reinit.  A leftover llama-server
    is a legitimate kill target (the engine's port-scoped cleanup removes
    it), but a foreign process — most importantly the agent web server,
    which binds the engine's default port (8081) inside the main app
    process — must never be killed or reused over; we pick a fresh free
    port instead.
    """
    if os.name != "nt":
        return False
    try:
        _net = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=5,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
    except Exception:
        return False
    for _line in _net.stdout.splitlines():
        _parts = _line.strip().split()
        if len(_parts) >= 2 and f":{port}" in _parts[1]:
            _pid = _parts[-1]
            if _pid.isdigit() and int(_pid) > 0:
                _img = pid_image_name(int(_pid))
                if _img and _img not in LLAMA_BIN_NAMES:
                    return True
    return False


def _ensure_llamacpp_engine(
    model_path: str, gpu_layers: int = 0, threads: int = -1, context_size: int = 0,
    skip_server_if_grounding: bool = False,
) -> None:
    """
    Dynamically initialize or reinitialize the llama.cpp engine for the given model.

    Defaults follow the CPU-first design and llama.cpp's own tuned auto
    values: gpu_layers=0 (CPU-only), threads=-1 (auto-detect), context_size=0
    (model's native training context).

    Raises HTTPException on error.
    """
    global LLAMACPP_ENGINE

    path = (model_path or "").strip()
    if not path:
        raise HTTPException(status_code=400, detail="Model path not specified")

    # Resolve relative model path against AI/models directory
    if not os.path.isabs(path):
        from AI.config_loader import get_models_dir
        models_dir = get_models_dir()
        resolved = os.path.normpath(os.path.join(models_dir, path))
        if os.path.exists(resolved):
            path = resolved

    if not os.path.exists(path):
        raise HTTPException(status_code=400, detail=f"Model not found: {path}")

    # Guard: reject mmproj files passed as model path (common user mistake)
    # mmproj files contain CLIP vision encoder weights and cannot be loaded
    # as standalone language models.
    _basename = os.path.basename(path).lower()
    if _basename.startswith("mmproj-"):
        _api_logger.error(
            "Model path points to an mmproj file (%s) instead of a GGUF model. "
            "mmproj files are projector weights for vision-language models and "
            "cannot be loaded as a standalone model. Select the actual .gguf model "
            "file in the same directory.",
            path,
        )
        raise HTTPException(
            status_code=400,
            detail=(
                f"The selected file is an mmproj projector ({os.path.basename(path)}), "
                f"not a language model. Please select the actual .gguf model file "
                f"in the directory instead."
            ),
        )

    # Check if we need to (re)initialize the engine.  The engine is compared
    # on model path, requested context size, gpu_layers AND threads: in
    # multi-model pipelines every node must run with ITS OWN exact params —
    # keeping the running engine frozen when only params differ silently runs
    # the node on the wrong configuration (observed: multi-node chains
    # stalling while the frozen server fought the per-node model switch).
    # Correctness wins over the seconds saved on single-model chains.
    current_model = (
        getattr(LLAMACPP_ENGINE, "model_path", "")
        if LLAMACPP_ENGINE is not None
        else ""
    )
    _engine_ctx = getattr(LLAMACPP_ENGINE, "context_size", 0) or 0
    _want_ctx = int(context_size or 0)
    _engine_gpu = getattr(LLAMACPP_ENGINE, "gpu_layers", 0) or 0
    _want_gpu = int(gpu_layers or 0)
    _engine_thr = getattr(LLAMACPP_ENGINE, "threads", -1)
    _want_thr = int(threads if threads not in (None, "") else -1)
    # Only an EXPLICIT positive context is a reinit trigger: a native (0)
    # request must not restart the engine just because the running server
    # resolved a RAM-aware effective window (e.g. 512) at startup — that
    # would restart the server between every pair of native-context nodes.
    needs_reinit = (
        LLAMACPP_ENGINE is None
        or current_model != path
        or (_want_ctx > 0 and _engine_ctx != _want_ctx)
        or _engine_gpu != _want_gpu
        or _engine_thr != _want_thr
    )
    if needs_reinit and LLAMACPP_ENGINE is not None and current_model == path:
        _api_logger.info(
            "llamacpp engine reinit for the node's exact params "
            "(engine: gpu_layers=%d threads=%d; request: gpu_layers=%d "
            "threads=%d) — multi-model pipelines run each node with its own "
            "configuration",
            _engine_gpu, _engine_thr, _want_gpu, _want_thr,
        )

    if needs_reinit:
        # Save the old port BEFORE stopping so we can reuse it for the new
        # engine.  Reusing the old port ensures start_server() ->
        # _ensure_port_free() -> taskkill will kill any leftover process
        # on that port, preventing orphaned server stacking.
        old_port = None
        if LLAMACPP_ENGINE is not None:
            try:
                old_port = LLAMACPP_ENGINE.server_port
                # Call stop_server() UNCONDITIONALLY — the method is safe
                # to call even when is_running is False (it checks
                # self.server_process internally).  The old guard
                # "if self.is_running:" caused orphaned server processes
                # when the engine's state was out of sync with the actual
                # process (e.g. after a crash).
                LLAMACPP_ENGINE.stop_server()
            except Exception:
                pass

        # Auto-detect mmproj file for vision models (same dir, starts with mmproj-)
        # Prefer the mmproj whose quantization matches the model file name.
        mmproj_path = ""
        model_dir = os.path.dirname(path)
        if os.path.isdir(model_dir):
            # Extract model variant (e.g. Q8_0, F16, Q4_0) from the model filename
            model_variant = ""
            model_base = os.path.basename(path).lower()
            for variant in ("q8_0", "q4_0", "q4_k_m", "q5_k_m", "f16", "f32"):
                if variant in model_base:
                    model_variant = variant
                    break

            mmproj_candidates = []
            for fname in os.listdir(model_dir):
                if fname.lower().startswith("mmproj-") and fname.lower().endswith(
                    ".gguf"
                ):
                    candidate = os.path.join(model_dir, fname)
                    if os.path.exists(candidate):
                        # Score: matching variant = 0, non-matching = 1
                        match = model_variant and model_variant in fname.lower()
                        mmproj_candidates.append((0 if match else 1, candidate))

            if mmproj_candidates:
                # Sort by score (matching first), then alphabetically
                mmproj_candidates.sort(key=lambda x: (x[0], x[1]))
                mmproj_path = mmproj_candidates[0][1]
                _api_logger.info(
                    "Auto-detected mmproj: %s (match=%s)",
                    mmproj_path,
                    mmproj_candidates[0][0] == 0,
                )

        # Classify model capability (text-only, multimodal, grounding)
        # to set correct server flags and inference routing.
        try:
            from AI.gguf_model_info import classify_model
            model_capability = classify_model(path, mmproj_path)
            _api_logger.info(
                "Model capability: type=%s, arch=%s, special=%s, mtmd=%s, "
                "native_ctx=%s",
                model_capability.model_type,
                model_capability.architecture,
                model_capability.needs_special_tokens,
                model_capability.needs_mtmd_cli,
                model_capability.context_size or "unknown",
            )
        except Exception as _cap_exc:
            _api_logger.warning("Model capability detection failed: %s", _cap_exc)
            model_capability = None

        engine_config = {
            "model_path": path,
            "mmproj_path": mmproj_path,
            # Reuse the old port if available.  This prevents stacking:
            # an orphaned llama-server left on this port is killed by
            # start_server() → _ensure_port_free() → taskkill.  If the
            # port is held by a FOREIGN process (e.g. the agent web
            # server on the shared default 8081), never reuse it — the
            # engine would either fail to start or (pre-fix) taskkill the
            # main app process.  Pick a fresh free port instead.
            "server_port": (
                old_port
                if (old_port and not _port_held_by_foreign_process(old_port))
                else _find_free_port()
            ),
            "gpu_layers": gpu_layers,
            "threads": threads,
            # Context size: 0 means the model's native training context
            # (the engine resolves it and applies a RAM-aware cap at
            # start_server).  A positive caller value is capped by the
            # model's native context as a practical infrastructure limit
            # (memory, hardware constraints).
            "context_size": (
                min(context_size, model_capability.context_size)
                if (context_size and context_size > 0
                    and model_capability and model_capability.context_size > 0)
                else (context_size if context_size and context_size > 0 else 0)
            ),
            "batch_size": 2048,
        }
        _api_logger.info(
            "Allocated port %d for llama.cpp engine (model=%s, ctx=%d)",
            engine_config["server_port"],
            os.path.basename(path),
            engine_config["context_size"],
        )
        LLAMACPP_ENGINE = LlamaCppEngine(engine_config)
        if model_capability is not None:
            LLAMACPP_ENGINE.capability = model_capability

            # Safety: clear mmproj for text-only models.  An incompatible
            # mmproj from a sibling model in the same directory will crash
            # the server with an n_embd mismatch.
            if model_capability.model_type == "text" and LLAMACPP_ENGINE.mmproj_path:
                _api_logger.warning(
                    "Model is text-only — clearing incompatible mmproj: %s",
                    LLAMACPP_ENGINE.mmproj_path,
                )
                LLAMACPP_ENGINE.mmproj_path = ""

    # Start server if not running.  Models whose engine preference is
    # llama-mtmd-cli (grounding models like LocateAnything, and LFM vision
    # variants whose server load hangs) skip the server entirely: their
    # inference runs in llama-mtmd-cli as a one-shot process, so loading the
    # model into the server too would double the memory footprint and
    # OOM-crash the mtmd-cli subprocess.  The engine choice follows the
    # MODEL's own default preference (capability.needs_mtmd_cli) — it must
    # never depend on the chain's execution order.
    _cap = getattr(LLAMACPP_ENGINE, "capability", None)
    _skip_server = bool(
        skip_server_if_grounding
        and _cap is not None
        and _cap.needs_mtmd_cli
    )
    if _skip_server:
        _api_logger.info(
            "Model (%s) prefers llama-mtmd-cli — server skipped; "
            "inference via one-shot mtmd",
            getattr(_cap, "architecture", "?"),
        )
    elif not LLAMACPP_ENGINE.is_running:
        if not LLAMACPP_ENGINE.start_server(blocking=True):
            raise HTTPException(
                status_code=503, detail="Failed to start llama.cpp server"
            )


@app.post("/llamacpp/chat")
@_engine_locked
async def llamacpp_chat(request: ChatRequest, background_tasks: BackgroundTasks, raw_request: Request):
    """Chat with a model using llama.cpp engine.

    Dynamically initializes the engine from the request's `model` field,
    so only one model is loaded at a time (ideal for low-end PCs).

    Node-execution calls (/llamacpp/generate) follow a sequential load -> use
    -> unload lifecycle, serialized by the lifecycle lock: the executor keeps
    the model loaded through its whole burst (main generation + consolidation
    probes + recursion) via cleanup_after=False and releases it with one
    /llamacpp/release when the burst is done — never on a fixed idle clock, so
    a slow probe never gets its server unloaded underneath it.  The
    interactive /llamacpp/chat dialog keeps the model warm instead.
    """
    try:
        global LLAMACPP_ENGINE

        # Only the node-execution endpoint unloads after use.
        _unload_after = raw_request.url.path.rstrip("/") == "/llamacpp/generate"
        _req_seq = _bump_llamacpp_request_seq()

        _ensure_llamacpp_engine(
            model_path=request.model,
            gpu_layers=request.gpu_layers or 0,
            threads=request.threads or -1,
            context_size=request.context_size or 0,
            skip_server_if_grounding=bool(request.images),
        )

        # Initialize locals for all code paths (avoids UnboundLocalError
        # on older Python versions or edge-case control flow)
        messages = None
        processed_images = None

        # Raw completion mode: bypasses the chat template so no reasoning/
        # think block is generated — the model must output its answer
        # immediately.  Used by probe generation, where a small model
        # otherwise spends the whole token budget "thinking" and the chat
        # path then strips the thinking, returning an empty response.
        if request.raw_completion and not request.images:
            # Keep ``messages`` as an empty list (not None): the response
            # block below iterates it for the _debug payload.
            messages = []
            result = LLAMACPP_ENGINE.generate(
                prompt=request.prompt or "",
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                max_rounds=request.max_rounds,
            )
        # Build messages — include images for vision-capable GGUF models
        elif request.messages:
            messages = request.messages
            result = LLAMACPP_ENGINE.chat(
                messages=messages,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                max_rounds=request.max_rounds,
                chat_template_kwargs=request.chat_template_kwargs,
            )
        else:
            messages = []
            if request.system:
                messages.append({"role": "system", "content": request.system})

            # Process images if present
            if request.images:
                _api_logger.info(f"Received {len(request.images)} image(s) in request")
                try:
                    processed_images = process_images(request.images)
                    _api_logger.info(
                        f"Processed {len(processed_images)} image(s) to base64"
                    )
                    if processed_images:
                        _api_logger.info(
                            f"First image base64 length: {len(processed_images[0])}"
                        )
                except Exception as e:
                    _api_logger.error(f"Image processing failed: {e}")
            else:
                _api_logger.info("No images in request")

            if processed_images:
                # Multimodal content: text + images
                content_parts = [{"type": "text", "text": request.prompt or ""}]
                for img in processed_images:
                    content_parts.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{img}"},
                        }
                    )
                messages.append({"role": "user", "content": content_parts})
            else:
                messages.append({"role": "user", "content": request.prompt or ""})

            result = LLAMACPP_ENGINE.chat(
                messages=messages,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                max_rounds=request.max_rounds,
                chat_template_kwargs=request.chat_template_kwargs,
            )

        if "error" in result:
            _err_body = str(result["error"])
            if (
                "exceed_context_size_error" in _err_body
                or "exceeds the available" in _err_body
            ):
                # Deterministic prompt-overflow: the payload cannot fit the
                # effective context no matter how often it is retried.  Return
                # 4xx so callers trim/shrink the prompt instead of retrying
                # the identical body (previously mapped to 500, which every
                # retry layer treated as transient and re-sent verbatim).
                raise HTTPException(status_code=400, detail=_err_body)
            raise HTTPException(status_code=500, detail=_err_body)

        # Warn if engine returned empty response despite success
        resp_text = result.get("response") or ""
        if not resp_text:
            _api_logger.warning(
                "llamacpp_chat: engine returned empty response. "
                "Engine keys: %s, usage: %s, result: %s",
                list(result.keys()),
                result.get("usage", {}),
                str(result)[:500],
            )
            print(
                f"[API_DEBUG] llamacpp_chat empty response! "
                f"Keys: {list(result.keys())}, "
                f"usage: {result.get('usage', {})}, "
                f"result: {str(result)[:300]}",
                flush=True,
            )

        # Release the model once the response is done (load -> use -> unload)
        # — unless the client keeps its burst alive: the executor sends
        # cleanup_after=False on every call of a node's consolidation burst so
        # the model survives the whole burst (probe after probe) and is
        # released by one explicit /llamacpp/release when the burst ends,
        # instead of on a fixed idle clock.
        if (
            _unload_after
            and request.cleanup_after is not False
            and LLAMACPP_ENGINE is not None
        ):
            background_tasks.add_task(_unload_llamacpp_after_idle, _req_seq)

        return {
            "response": _sanitize_llamacpp_response(resp_text),
            "model": result.get("model", request.model),
            "_debug": {
                "images_received": bool(request.images),
                "images_count": len(request.images) if request.images else 0,
                "images_processed": bool(processed_images),
                "images_processed_count": len(processed_images)
                if processed_images
                else 0,
                "multimodal_messages": any(
                    isinstance(m.get("content"), list) for m in messages
                ),
                "image_data_in_result": "image_data" in str(result.keys())
                if isinstance(result, dict)
                else False,
            },
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "done": result.get("done", True),
            "truncated": result.get("truncated", False),
            "usage": result.get("usage", {}),
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"llama.cpp error: {str(e)}")


@app.post("/llamacpp/generate")
@_engine_locked
async def llamacpp_generate(request: ChatRequest, background_tasks: BackgroundTasks, raw_request: Request):
    """Generate text using llama.cpp engine (alias for chat) - node execution path"""
    return await llamacpp_chat(request, background_tasks, raw_request)


@app.post("/llamacpp/release")
async def llamacpp_release():
    """Burst-end release: unload the current model as soon as it is no longer
    needed, with NO fixed keep-alive clock.

    The node executor fires this once its whole call burst (main generation +
    consolidation probes + recursion) has finished.  The unload is
    seq-guarded under the lifecycle lock: if a newer inference request already
    claimed the engine, the model stays loaded for it.
    """
    with _LLAMACPP_LIFECYCLE_LOCK:
        _eng = LLAMACPP_ENGINE
        if _eng is not None and getattr(_eng, "is_running", False):
            _seq = _bump_llamacpp_request_seq()
            threading.Thread(
                target=_unload_llamacpp_after_idle,
                args=(_seq,),
                daemon=True,
            ).start()
    return {"status": "ok"}


@app.post("/llamacpp/generate_stream")
async def llamacpp_generate_stream(request: ChatRequest):
    """Stream text generation using llama.cpp engine"""
    try:
        global LLAMACPP_ENGINE

        # The lifecycle lock is held for the WHOLE stream (ensure -> generate
        # chunks -> unload scheduling), so a reinit from a concurrent request
        # can never stop the model out from under an active stream.
        async def stream_generator():
            with _LLAMACPP_LIFECYCLE_LOCK:
                _req_seq = _bump_llamacpp_request_seq()

                _ensure_llamacpp_engine(
                    model_path=request.model,
                    gpu_layers=request.gpu_layers or 0,
                    threads=request.threads or -1,
                    context_size=request.context_size or 0,
                )

                # Build messages so the model's chat template is applied
                # server-side (raw completion prompts produce garbled
                # instruction following).
                messages = []
                if request.system:
                    messages.append({"role": "system", "content": request.system})
                messages.append({"role": "user", "content": request.prompt or ""})

                accumulated = ""
                try:
                    for chunk in LLAMACPP_ENGINE.chat_stream(
                        messages=messages,
                        max_tokens=request.max_tokens,
                        temperature=request.temperature,
                        repeat_penalty=request.repeat_penalty,
                    ):
                        accumulated += chunk
                        yield json.dumps({"text": accumulated}) + "\n"
                finally:
                    # Unload the model once the stream ends (load -> use ->
                    # unload) unless the client keeps its burst alive via
                    # cleanup_after=False.
                    if (
                        request.cleanup_after is not False
                        and LLAMACPP_ENGINE is not None
                    ):
                        threading.Thread(
                            target=_unload_llamacpp_after_idle,
                            args=(_req_seq,),
                            daemon=True,
                        ).start()

        return StreamingResponse(stream_generator(), media_type="application/x-ndjson")
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/llamacpp/models")
async def llamacpp_list_models():
    """List available GGUF models"""
    try:
        from AI.config_loader import get_models_dir
        models_dir = get_models_dir()
        gguf_models = []

        if os.path.exists(models_dir):
            for root, dirs, files in os.walk(models_dir):
                for file in files:
                    if file.endswith(".gguf"):
                        model_path = os.path.join(root, file)
                        rel_path = os.path.relpath(model_path, models_dir)
                        file_size = os.path.getsize(model_path)
                        gguf_models.append(
                            {
                                "name": rel_path,
                                "path": model_path,
                                "size": file_size,
                                "type": "gguf",
                            }
                        )

        return {"models": gguf_models}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/llamacpp/start")
@_engine_locked
async def llamacpp_start_server(body: Dict[str, Any]):
    """Start llama.cpp server with specific model"""
    try:
        global LLAMACPP_ENGINE

        model_path = body.get("model_path", "")
        if not model_path or not os.path.exists(model_path):
            raise HTTPException(
                status_code=400, detail=f"Model not found: {model_path}"
            )

        # Guard: reject mmproj files passed as model path
        if os.path.basename(model_path).lower().startswith("mmproj-"):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"The selected file is an mmproj projector "
                    f"({os.path.basename(model_path)}), not a language model. "
                    f"Please select the actual .gguf model file instead."
                ),
            )

        config = {
            "model_path": model_path,
            "server_port": _find_free_port(),
            "gpu_layers": int(body.get("gpu_layers", 0)),
            "threads": int(body.get("threads", -1)),
            # Placeholder — will be overridden below after model detection
            "context_size": 0,
            "batch_size": int(body.get("batch_size", 2048)),
        }

        # Auto-detect mmproj for vision models
        mmproj_path = ""
        model_dir = os.path.dirname(model_path)
        if os.path.isdir(model_dir):
            for fname in os.listdir(model_dir):
                if fname.lower().startswith("mmproj-") and fname.lower().endswith(".gguf"):
                    candidate = os.path.join(model_dir, fname)
                    if os.path.exists(candidate):
                        mmproj_path = candidate
                        config["mmproj_path"] = mmproj_path
                        break

        LLAMACPP_ENGINE = LlamaCppEngine(config)

        # Classify model and set capability for proper server flags/routing
        try:
            from AI.gguf_model_info import classify_model
            model_capability = classify_model(model_path, mmproj_path)
            LLAMACPP_ENGINE.capability = model_capability
            _api_logger.info(
                "[/llamacpp/start] Model capability: type=%s, arch=%s",
                model_capability.model_type,
                model_capability.architecture,
            )

            # Safety: clear mmproj for text-only models
            if model_capability.model_type == "text" and LLAMACPP_ENGINE.mmproj_path:
                _api_logger.warning(
                    "[/llamacpp/start] Model is text-only - "
                    "clearing incompatible mmproj: %s",
                    LLAMACPP_ENGINE.mmproj_path,
                )
                LLAMACPP_ENGINE.mmproj_path = ""

            # Auto-detect context size from model metadata
            if model_capability.context_size > 0:
                caller_ctx = int(body.get("context_size", 0))
                # 0 (or unset) means the model's native training context —
                # passed through as 0 so the engine applies its RAM-aware
                # cap at start_server.  A positive caller value is capped
                # by the native context.
                effective_ctx = (
                    0
                    if not caller_ctx or caller_ctx <= 0
                    else min(caller_ctx, model_capability.context_size)
                )
                LLAMACPP_ENGINE.context_size = effective_ctx
                _api_logger.info(
                    "[/llamacpp/start] Context: %s (caller: %d, native: %d)",
                    "native (RAM-capped at start)" if effective_ctx == 0 else effective_ctx,
                    caller_ctx,
                    model_capability.context_size,
                )
        except Exception as _cap_exc:
            _api_logger.warning(
                "[/llamacpp/start] Capability detection failed: %s", _cap_exc
            )

        # Safeguard: if context_size was never set (detection failed),
        # fall back to the caller-provided value; 0 = native model context.
        if LLAMACPP_ENGINE.context_size <= 0:
            LLAMACPP_ENGINE.context_size = int(body.get("context_size", 0)) or 0

        if not LLAMACPP_ENGINE.start_server(blocking=True):
            raise HTTPException(
                status_code=503, detail="Failed to start llama.cpp server"
            )

        return {"status": "started", "url": LLAMACPP_ENGINE.server_url}
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/llamacpp/stop")
@_engine_locked
async def llamacpp_stop_server():
    """Stop llama.cpp server"""
    try:
        global LLAMACPP_ENGINE

        if LLAMACPP_ENGINE:
            LLAMACPP_ENGINE.stop_server()
            return {"status": "stopped"}
        return {"status": "not_running"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/llamacpp/status")
@_engine_locked
async def llamacpp_status():
    """Get llama.cpp server status"""
    try:
        global LLAMACPP_ENGINE

        if LLAMACPP_ENGINE:
            return LLAMACPP_ENGINE.get_server_info()
        return {"status": "not_initialized"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ========== End Llama.cpp Endpoints ==========


OLLAMA_ENGINE = None  # Optional[OllamaEngine] when available
LLAMACPP_ENGINE: Optional[LlamaCppEngine] = None
SETTINGS: Any = None

# ---------------------------------------------------------------------------
# llama.cpp model lifecycle: sequential load -> use -> unload (burst-scoped)
# ---------------------------------------------------------------------------
# Every inference request bumps this counter.  A scheduled unload only fires
# if NO newer request has started in the meantime, so a just-started request
# never gets its server killed out from under it.  The model is released when
# the BURST that used it finishes — not on a fixed idle clock:
#   - Node-execution clients keep the model through their whole burst by
#     sending cleanup_after=False on every call (the executor does this for a
#     node's consolidation probes + main generation + recursion), so a slow
#     probe never gets its server unloaded underneath it.
#   - When the burst is done the client fires one explicit /llamacpp/release,
#     which schedules the (seq-guarded) unload immediately.
#   - Clients that do not manage bursts keep the old per-call behavior: the
#     unload is scheduled right after the response and fires as soon as no
#     newer request claimed the engine (opt-in grace below).
# The old unconditional "keep the server resident forever" behavior
# OOM-crashed big-model workflows such as Handle nodes on low-RAM machines and
# leaked llama-server/mtmd-cli processes at app exit; the fixed 20s idle clock
# that replaced it unloaded MID-burst on slow/CPU-first runs (the consolidation
# pause between probe phases), forcing a reload that re-ran the RAM-aware
# context cap while the embedding server was resident — collapsing the context
# window to its 1024-token floor and 400-rejecting every subsequent probe.
_LLAMACPP_REQUEST_SEQ = 0
# Optional operator grace AFTER a request finishes before the model is unloaded
# (LLAMACPP_KEEP_ALIVE_SECONDS).  Default 0: unload as soon as the request that
# scheduled it is done and no newer request has claimed the engine.
_LLAMACPP_UNLOAD_IDLE_SECONDS = float(
    os.getenv("LLAMACPP_KEEP_ALIVE_SECONDS", "0") or 0
)


def _bump_llamacpp_request_seq() -> int:
    """Mark a new inference request; returns the seq the unloader compares."""
    global _LLAMACPP_REQUEST_SEQ
    _LLAMACPP_REQUEST_SEQ += 1
    return _LLAMACPP_REQUEST_SEQ


def _unload_llamacpp_after_idle(seq_at_schedule: int) -> None:
    """Stop the llama.cpp server once the request/burst that used it is done.

    Runs in a background thread (never blocks the event loop) and takes the
    lifecycle lock, so it can never race an in-flight generation or a reinit.
    Guards:
    - lock: serialized against ensure/generate/release — a running generation
      holds the lock for its whole duration, so the server is never stopped
      out from under it;
    - seq check: if a newer request started after this one was scheduled,
      the model stays loaded for it;
    - process-identity check: only the exact server process this request
      used is stopped — a server a newer request has already spawned is
      left alone;
    - quick stop: terminate only that process, never sweep the port (the
      next request's start_server() does its own port cleanup), so a
      concurrently starting server on the same port is never killed.
    """
    global LLAMACPP_ENGINE, _LLAMACPP_REQUEST_SEQ
    # Optional operator grace; 0 (default) unloads as soon as the burst ends.
    if _LLAMACPP_UNLOAD_IDLE_SECONDS > 0:
        time.sleep(_LLAMACPP_UNLOAD_IDLE_SECONDS)
    with _LLAMACPP_LIFECYCLE_LOCK:
        if _LLAMACPP_REQUEST_SEQ != seq_at_schedule:
            return  # a newer request is using the engine - keep it loaded
        engine = LLAMACPP_ENGINE
        if engine is None:
            return
        proc = getattr(engine, "server_process", None)
        if proc is None:
            return
        if getattr(engine, "server_process", None) is not proc:
            return  # a newer server took over - do not touch it
        try:
            engine.stop_server(quick=True)
            _api_logger.info(
                "[llamacpp] Model unloaded (load -> use -> unload per burst)"
            )
        except Exception as exc:
            _api_logger.warning("[llamacpp] Model unload failed: %s", exc)


def _shutdown_llamacpp() -> None:
    """Stop every llama.cpp server owned by this process at exit."""
    global LLAMACPP_ENGINE
    if LLAMACPP_ENGINE is not None:
        try:
            LLAMACPP_ENGINE.stop_server()
            _api_logger.info("[llamacpp] Engine stopped at exit")
        except Exception as exc:
            _api_logger.warning("[llamacpp] Engine stop failed at exit: %s", exc)
        finally:
            LLAMACPP_ENGINE = None
    try:
        llama_cpp_manager.stop_all()
    except Exception:
        pass


atexit.register(_shutdown_llamacpp)


@app.on_event("startup")
async def startup_event():
    global OLLAMA_ENGINE, LLAMACPP_ENGINE, SETTINGS

    # Initialize Ollama Engine (using settings from LoOper config or defaults)
    try:
        try:
            from AI.settings import OllamaSettings, Settings

            SETTINGS = Settings()
        except ImportError:
            # Fallback for frozen builds or flat structure
            from settings import OllamaSettings, Settings

            SETTINGS = Settings()
    except ImportError:
        # Fallback if settings.py isn't fully integrated yet
        class MockSettings:
            pass

        SETTINGS = MockSettings()
        SETTINGS.ollama = MockSettings()
        SETTINGS.ollama.base_url = os.getenv("OLLAMA_HOST", "http://localhost:11434")

    if OllamaEngine is not None:
        try:
            OLLAMA_ENGINE = OllamaEngine(
                SETTINGS.ollama if hasattr(SETTINGS, "ollama") else None
            )
        except Exception as e:
            print(f"Warning: Failed to create Ollama engine: {e}")
            OLLAMA_ENGINE = None
    else:
        OLLAMA_ENGINE = None

    # Initialize llama.cpp Engine
    try:
        print("[LLAMA.CPP] Reading llamacpp configuration...")
        # Use config_loader which handles frozen-build path resolution
        try:
            from AI.config_loader import get_llamacpp_config
        except ImportError:
            get_llamacpp_config = lambda: {
                "enabled": False, "model_path": "",
                "server_port": 9090, "gpu_layers": 0,
                "threads": -1, "context_size": 0, "batch_size": 2048,
            }
        llamacpp_config = get_llamacpp_config().copy()

        # Resolve model_path — it may be just a filename in bundled configs.
        # Absolute paths come from source-tree configs; basenames come from
        # patched build configs.  Both need to resolve to the actual file.
        model_path = llamacpp_config.get("model_path", "")
        if model_path and not os.path.isabs(model_path):
            from AI.config_loader import get_models_dir
            resolved = os.path.join(get_models_dir(), model_path)
            if os.path.exists(resolved):
                llamacpp_config["model_path"] = resolved
                _api_logger.info(
                    "Resolved model filename '%s' -> '%s'", model_path, resolved
                )

        if llamacpp_config.get("enabled", False) and llamacpp_config.get("model_path"):
            LLAMACPP_ENGINE = LlamaCppEngine(llamacpp_config)
            print(
                f"[LLAMA.CPP] Engine initialized with model: {llamacpp_config['model_path']}"
            )
        else:
            print("[LLAMA.CPP] Engine disabled or model not configured")
            LLAMACPP_ENGINE = None
    except Exception as e:
        print(f"Warning: Failed to create llama.cpp engine: {e}")
        LLAMACPP_ENGINE = None

    async def _init_engines_in_background():
        global OLLAMA_ENGINE
        try:
            if OLLAMA_ENGINE is not None:
                await OLLAMA_ENGINE.initialize()
        except Exception as e:
            print(f"Warning: Failed to initialize Ollama engine: {e}")

    try:
        asyncio.create_task(_init_engines_in_background())
    except Exception as e:
        print(f"Warning: Failed to start background engine initialization: {e}")


if __name__ == "__main__":
    bind_host = os.getenv("API_BIND_HOST") or "0.0.0.0"
    port = int(os.getenv("API_PORT", "8000"))

    print(f"Starting Ollama API Gateway on {bind_host}:{port}")

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

    # Unified telemetry: let uvicorn loggers propagate to the root dispatch
    # (colorized console + logs/automation.log + session md).
    uvicorn.run(
        app,
        host=bind_host,
        port=port,
        reload=False,
        log_level="info",
        log_config=uvicorn_log_config(),
    )
