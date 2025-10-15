from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import json
import base64
import os
from typing import List, Optional, Dict, Any, Union
import uvicorn

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
    'llava', 'llava:latest', 'llava:7b', 'llava:13b', 'llava:34b',
    'bakllava', 'bakllava:latest', 'bakllava:7b',
    'moondream', 'moondream:latest', 'moondream:1.8b',
    'llava-llama3', 'llava-llama3:latest', 'llava-llama3:8b',
    'llava-phi3', 'llava-phi3:latest', 'llava-phi3:3.8b',
    # MiniCPM vision variants
    'minicpm-v', 'minicpm-v:latest', 'minicpm-v2', 'minicpm-v4', 'openbmb/minicpm-v4'
]

def is_vision_model(model_name: str) -> bool:
    """Check if the model supports vision capabilities"""
    model_lower = model_name.lower()
    return any(vision_model in model_lower for vision_model in VISION_MODELS)

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
        response = requests.get(f"{get_ollama_base_url()}/api/tags")
        response.raise_for_status()
        
        data = response.json()
        models = []
        
        for model in data.get("models", []):
            models.append(ModelInfo(
                name=model["name"],
                size=model["size"],
                digest=model["digest"],
                modified_at=model["modified_at"]
            ))
        
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
        # Check if this is a vision model with images
        if request.images and len(request.images) > 0:
            if not is_vision_model(request.model):
                raise HTTPException(
                    status_code=400,
                    detail=f"Model {request.model} does not support vision capabilities"
                )
            
            # Use chat API format for vision models
            processed_images = process_images(request.images)
            
            # Prepare message for chat API
            message = {
                "role": "user",
                "content": request.prompt,
                "images": processed_images
            }
            
            # Add system message if provided
            messages = []
            if request.system:
                messages.append({"role": "system", "content": request.system})
            messages.append(message)
            
            ollama_payload = {
                "model": request.model,
                "messages": messages,
                "stream": request.stream,
                "options": {}
            }
            
            # Add optional parameters
            if request.temperature is not None:
                ollama_payload["options"]["temperature"] = request.temperature
            if request.max_tokens is not None:
                ollama_payload["options"]["num_predict"] = request.max_tokens
            
            # Send request to Ollama chat API
            response = requests.post(
                f"{get_ollama_base_url()}/api/chat",
                json=ollama_payload
            )
        else:
            # Use generate API for text-only requests
            ollama_payload = {
                "model": request.model,
                "prompt": request.prompt,
                "stream": request.stream,
                "options": {}
            }
            
            # Add optional parameters
            if request.temperature is not None:
                ollama_payload["options"]["temperature"] = request.temperature
            if request.max_tokens is not None:
                ollama_payload["options"]["num_predict"] = request.max_tokens
            if request.system is not None:
                ollama_payload["system"] = request.system
            
            # Send request to Ollama generate API
            response = requests.post(
                f"{get_ollama_base_url()}/api/generate",
                json=ollama_payload
            )
        
        response.raise_for_status()
        
        # Parse response
        result = response.json()
        
        # Handle different response formats
        if request.images and len(request.images) > 0:
            # Chat API response format
            message = result.get("message", {})
            response_text = message.get("content", "")
        else:
            # Generate API response format
            response_text = result.get("response", "")
        
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
    
    # Run the server
    uvicorn.run(
        "api:app",
        host=tailscale_ip,  # Listen on Tailscale IP or all interfaces
        port=port,
        reload=True,
        log_level="info"
    )