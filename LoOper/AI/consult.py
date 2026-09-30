#!/usr/bin/env python3
"""
Ollama API Client
A client script to interact with the local Ollama API Gateway
"""

import requests
import json
import argparse
import sys
import base64
import os
import time
import io
import queue
import threading
from typing import Optional, List, Dict, Any, Tuple, Callable
from urllib.parse import urljoin
from typing import cast
from requests.adapters import HTTPAdapter

try:
    from PIL import Image, ImageGrab
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False
    Image = None
    ImageGrab = None

class OllamaClient:
    """Client for interacting with Ollama API Gateway"""
    
    def __init__(self, base_url: str = None, debug: bool = False, max_queue_size: int = 10, timeout: Optional[int] = 0):
        """Initialize the client with the API base URL"""
        if base_url is None:
            env_overwatch = os.getenv("OVERWATCH_API_URL")
            if env_overwatch and str(env_overwatch).strip():
                base_url = env_overwatch.strip()
            else:
                port = os.getenv("API_PORT", "8000")
                base_url = f"http://localhost:{port}"
        
        self.base_url = base_url.rstrip('/')
        self.debug = debug
        # If timeout is None or <= 0, we treat it as infinite (None for requests)
        self.timeout = None
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Connection': 'close'
        })
        try:
            adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10)
            self.session.mount('http://', adapter)
            self.session.mount('https://', adapter)
        except Exception:
            pass
        self.context_data = []
        
        self._fail_counts = 0
        self._breaker_until = None
    
    def health_check(self) -> Dict[str, Any]:
        """Check if the API server and Ollama are healthy"""
        try:
            response = self.session.get(f"{self.base_url}/health")
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            return {"status": "error", "message": str(e)}
    
    def list_models(self) -> List[Dict[str, Any]]:
        """Get list of available models"""
        models = []
        # Try Gateway /models first
        try:
            response = self.session.get(f"{self.base_url}/models")
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict) and 'models' in data:
                models = data['models']
            elif isinstance(data, list):
                models = data
        except Exception as e:
            if self.debug:
                print(f"Gateway list_models failed: {e}")
        
        # If no models found, try local Ollama /api/tags
        if not models:
            try:
                host = os.getenv('OLLAMA_HOST', 'localhost')
                port = os.getenv('OLLAMA_PORT', '11434')
                base = f"http://{host}:{port}"
                response = self.session.get(f"{base}/api/tags")
                response.raise_for_status()
                data = response.json()
                if isinstance(data, dict) and 'models' in data:
                    # Convert /api/tags format to simple list or compatible format
                    # /api/tags returns list of objects with 'name' field
                    models = [m['name'] for m in data['models']]
            except Exception as e:
                if self.debug:
                    print(f"Local Ollama list_models failed: {e}")
                    
        return models
    
    def chat(self, model: str, prompt: str, 
             temperature: Optional[float] = None,
             max_tokens: Optional[int] = None,
             system: Optional[str] = None,
             images: Optional[List[str]] = None,
             stream: bool = False) -> Dict[str, Any]:
        """Send a chat request to the specified model
        
        Args:
            model: Model name to use
            prompt: Text prompt
            temperature: Temperature for generation
            max_tokens: Maximum tokens to generate
            system: System message
            images: List of image paths or base64 encoded images
        """
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": bool(stream),
            "ephemeral": True
        }
        
        if temperature is not None:
            payload["temperature"] = temperature
        if system is not None:
            payload["system"] = system
        if images is not None:
            payload["images"] = self._process_images(images)
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        if stream:
            return self._post_stream_text('/generate', payload)
        result = self._post_json('/generate', payload)
        # Fallback to direct Ollama if gateway is unavailable or returned an error/invalid format
        try:
            if (not isinstance(result, dict)) or (isinstance(result, dict) and result.get('error')):
                host = os.getenv('OLLAMA_HOST', 'localhost')
                port = os.getenv('OLLAMA_PORT', '11434')
                base = f"http://{host}:{port}"
                headers = {'Connection': 'close', 'Content-Type': 'application/json', 'Accept': 'application/json'}
                # Vision models require /api/chat with images; otherwise use /api/generate
                use_chat = bool(images)
                if use_chat:
                    msg = {"role": "user", "content": prompt}
                    proc_images = self._process_images(images)
                    if proc_images:
                        msg["images"] = proc_images
                    msgs = []
                    if system:
                        msgs.append({"role": "system", "content": system})
                    msgs.append(msg)
                    payload2 = {
                        "model": model,
                        "messages": msgs,
                        "stream": False,
                        "keep_alive": 60,
                        "options": {"num_predict": -1}
                    }
                    if temperature is not None:
                        payload2["options"]["temperature"] = temperature
                    r = self.session.post(f"{base}/api/chat", json=payload2, headers=headers)
                else:
                    payload2 = {
                        "model": model,
                        "prompt": prompt,
                        "stream": False,
                        "keep_alive": 60,
                        "options": {"num_predict": -1}
                    }
                    if temperature is not None:
                        payload2["options"]["temperature"] = temperature
                    if system is not None:
                        payload2["system"] = system
                    r = self.session.post(f"{base}/api/generate", json=payload2, headers=headers)
                r.raise_for_status()
                data = r.json() if hasattr(r, 'json') else {}
                if isinstance(data, dict):
                    if 'response' in data:
                        return {"response": data.get('response'), "model": data.get('model') or model}
                    if 'message' in data and isinstance(data.get('message'), dict):
                        return {"response": data['message'].get('content', ''), "model": data.get('model') or model}
                # If still unusual, return raw text
                try:
                    return {"response": r.text}
                except Exception:
                    return {"error": "fallback_failed"}
        except Exception:
            pass
        if isinstance(result, dict):
            if 'text' in result and 'response' not in result:
                result['response'] = result['text']
        return result if isinstance(result, dict) else {"error": "request_failed"}
    
    def generate(self, model: str, prompt: str, 
                temperature: Optional[float] = None,
                max_tokens: Optional[int] = None,
                system: Optional[str] = None,
                images: Optional[List[str]] = None,
                context: Optional[List[Dict]] = None,
                async_request: bool = False,
                callback: Optional[Callable[[str, Dict[str, Any]], None]] = None) -> Dict[str, Any]:
        """Generate text using the specified model
        
        Args:
            model: Model name to use
            prompt: Text prompt
            temperature: Temperature for generation
            max_tokens: Maximum tokens to generate
            system: System message
            images: List of image paths or base64 encoded images
            context: Additional context items
            async_request: Whether to process the request asynchronously
            callback: Function to call when async request completes (receives request_id and result)
            
        Returns:
            The generation result
        """
        return self.chat(model, prompt, temperature, max_tokens, system, images, stream=False)

    def embeddings(self, model: str, inputs: List[str],
                   normalize: Optional[bool] = None,
                   pooling: Optional[str] = None) -> List[List[float]]:
        # Rely on server-side to ensure embedding model presence
        # Disable cleanup_after to prevent unloading the model, allowing reuse in chained nodes
        payload: Dict[str, Any] = {"model": model, "inputs": inputs, "ephemeral": True}
        if normalize is not None:
            payload["normalize"] = normalize
        if pooling is not None:
            payload["pooling"] = pooling

        if not inputs or all((not s) or (isinstance(s, str) and s.strip() == "") for s in inputs):
            if self.debug:
                print("Embeddings skipped: no valid input texts provided.", file=sys.stderr)
            return []

        if self.debug:
            print(f"Sending {len(inputs)} inputs to embeddings endpoint at {self.base_url}/embeddings", file=sys.stderr)
            # Print first input preview
            if inputs:
                print(f"Input 0 preview: {inputs[0][:100]}...", file=sys.stderr)

        inputs_list = inputs if isinstance(inputs, list) else [inputs]

        # ── Priority 1: Local llama.cpp embedding server (offline) ──
        if self.debug:
            print(f"Embeddings: trying local embedding server for {len(inputs_list)} texts...", file=sys.stderr)
        try:
            local_embs = _local_embed(inputs_list)
            if local_embs and len(local_embs) == len(inputs_list):
                if self.debug:
                    print(f"Local embedding succeeded for {len(local_embs)} texts", file=sys.stderr)
                return local_embs
            if self.debug:
                print(f"Local embedding returned {len(local_embs) if local_embs else 0}/{len(inputs_list)} vectors, falling back to remote API", file=sys.stderr)
        except Exception as exc:
            if self.debug:
                print(f"Local embedding failed: {exc}, falling back to remote API", file=sys.stderr)

        # ── Priority 2: Remote API (OverWatch / Ollama) ──
        attempt_count = 0
        last_error: Optional[Exception] = None
        while attempt_count < 2:
            try:
                # IMPORTANT: If OverWatch is enabled and we are hitting its /embeddings wrapper,
                # we must send a payload that matches its API schema (inputs, not input)
                payload["model"] = model
                payload["inputs"] = inputs_list
                
                # Try standard OverWatch endpoint first
                data = self._post_json('/embeddings', payload)
                
                # Check if it failed (e.g. 404 on direct Ollama connection) and try native Ollama endpoints
                if isinstance(data, dict) and "error" in data:
                    err_msg = str(data["error"]).lower()
                    if "404" in err_msg or "not found" in err_msg or "failed" in err_msg:
                        if self.debug:
                            print(f"OverWatch /embeddings failed ({err_msg}), trying local Ollama fallback", file=sys.stderr)
                        
                        # Prepare Ollama native payload
                        ollama_payload = {"model": model, "input": inputs_list}
                        if normalize is not None:
                            ollama_payload["normalize"] = normalize
                        
                        # Send directly to Ollama port (11434) to avoid looping back into the broken wrapper
                        try:
                            host = os.getenv('OLLAMA_HOST', '127.0.0.1')
                            port = os.getenv('OLLAMA_PORT', '11434')
                            ollama_base = f"http://{host}:{port}"
                            ollama_resp = self.session.post(f"{ollama_base}/api/embed", json=ollama_payload, timeout=10)
                            if ollama_resp.status_code == 200:
                                data = ollama_resp.json()
                            else:
                                data = {"error": f"{ollama_resp.status_code} {ollama_resp.text}"}
                        except Exception as e:
                            data = {"error": str(e)}
                        
                        # If that failed with 404, try /api/embeddings (Old Ollama)
                        if isinstance(data, dict) and "error" in data:
                            err_msg2 = str(data["error"]).lower()
                            if "404" in err_msg2 or "failed" in err_msg2:
                                if self.debug:
                                    print(f"Ollama /api/embed failed ({err_msg2}), trying legacy /api/embeddings", file=sys.stderr)
                                try:
                                    # Legacy /api/embeddings expects 'prompt' as a single string, not an array
                                    legacy_prompt = "\n\n".join(inputs_list)
                                    legacy_payload = {"model": model, "prompt": legacy_prompt}
                                    ollama_resp = self.session.post(f"{ollama_base}/api/embeddings", json=legacy_payload, timeout=10)
                                    if ollama_resp.status_code == 200:
                                        data = ollama_resp.json()
                                    else:
                                        data = {"error": f"{ollama_resp.status_code} {ollama_resp.text}"}
                                except Exception as e:
                                    data = {"error": str(e)}

                if not isinstance(data, dict) and not isinstance(data, list):
                    raise ValueError("Invalid embeddings response")

                if self.debug:
                    print(f"Embeddings response keys: {list(data.keys()) if isinstance(data, dict) else 'not a dict'}", file=sys.stderr)

                vectors: List[List[float]] = []
                if isinstance(data, dict):
                    if "error" in data:
                         raise ValueError(f"Embeddings API error: {data['error']}")
                    if "embeddings" in data and isinstance(data["embeddings"], list):
                        vectors = cast(List[List[float]], data["embeddings"])
                    elif "embedding" in data and isinstance(data["embedding"], list):
                        vectors = [cast(List[float], data["embedding"])]
                elif isinstance(data, list):
                    if data and isinstance(data[0], float):
                        vectors = [cast(List[float], data)]
                    elif data and isinstance(data[0], list):
                        vectors = cast(List[List[float]], data)
                else:
                    if self.debug:
                        print(f"Unexpected embeddings response: {data}")
                    raise ValueError("Invalid embeddings response")

                def _valid(vecs: List[List[float]]) -> bool:
                    if len(vecs) != len(inputs_list):
                        return False
                    for v in vecs:
                        if not isinstance(v, list) or len(v) == 0:
                            return False
                    return True

                if not _valid(vectors):
                    if self.debug:
                        print(f"Batch embeddings failed validation. Falling back to per-input processing for {len(inputs_list)} inputs.")
                    
                    per_vectors: List[List[float]] = []
                    for idx, t in enumerate(inputs_list):
                        try:
                            # Send directly to local Ollama API for fallback per-input instead of routing back through OverWatch wrapper
                            try:
                                host = os.getenv('OLLAMA_HOST', '127.0.0.1')
                                port = os.getenv('OLLAMA_PORT', '11434')
                                ollama_base = f"http://{host}:{port}"
                                sub_payload = {"model": model, "input": t}
                                sub_resp = self.session.post(f"{ollama_base}/api/embed", json=sub_payload, timeout=10)
                                if sub_resp.status_code == 200:
                                    d2 = sub_resp.json()
                                else:
                                    # Fallback to legacy
                                    leg_payload = {"model": model, "prompt": t}
                                    sub_resp = self.session.post(f"{ollama_base}/api/embeddings", json=leg_payload, timeout=10)
                                    d2 = sub_resp.json() if sub_resp.status_code == 200 else {}
                            except Exception as e:
                                d2 = {"error": str(e)}
                            
                            v = []
                            if isinstance(d2, dict):
                                if "embeddings" in d2 and isinstance(d2["embeddings"], list) and len(d2["embeddings"]) == 1:
                                    v = cast(List[float], d2["embeddings"][0])
                                elif "embedding" in d2 and isinstance(d2["embedding"], list):
                                    v = cast(List[float], d2["embedding"])
                            elif isinstance(d2, list) and d2 and isinstance(d2[0], float):
                                v = cast(List[float], d2)
                                
                            per_vectors.append(v if v else [])
                        except Exception as e:
                            if self.debug:
                                print(f"Per-input embedding failed for item {idx}: {e}")
                            per_vectors.append([])

                    if _valid(per_vectors):
                        return per_vectors
                    
                    valid_count = sum(1 for v in per_vectors if isinstance(v, list) and len(v) > 0)
                    if self.debug:
                        print(f"Embeddings fallback failed. Inputs: {len(inputs_list)}, Valid vectors: {valid_count}")
                        
                    raise ValueError(f"Empty embedding vectors (valid: {valid_count}/{len(inputs_list)})")

                try:
                    pass
                except Exception:
                    pass
                return vectors
            except requests.exceptions.RequestException as e:
                last_error = e
                attempt_count += 1
                if self.debug:
                    print(f"Embeddings request failed (attempt {attempt_count}): {e}")
                try:
                    time.sleep(0.3)
                except Exception:
                    pass
            except Exception as e:
                last_error = e
                attempt_count += 1
                if self.debug:
                    print(f"Embeddings processing error (attempt {attempt_count}): {e}")
                try:
                    time.sleep(0.2)
                except Exception:
                    pass

        if self.debug:
            print("Embeddings: remote API failed, no fallback available.")
        return []

    
    def shutdown(self, wait: float = 2.0):
        try:
            if hasattr(self, "session") and self.session:
                self.session.close()
        except Exception:
            pass

    def close(self):
        self.shutdown()

    def __del__(self):
        try:
            self.shutdown(wait=0.5)
        except Exception:
            pass

    def _post_json(self, path: str, payload: Dict[str, Any]) -> Any:
        url = f"{self.base_url}{path if path.startswith('/') else '/' + path}"
        attempts = 0
        last_error: Optional[Exception] = None
        _retry_503 = 0
        while attempts < 4:
            if self._breaker_until and time.time() < float(self._breaker_until):
                try:
                    time.sleep(0.3)
                except Exception:
                    pass
                attempts += 1
                continue
            try:
                response = self.session.post(url, json=payload)
                # Fail fast on 404 (Not Found) - usually means wrong endpoint, retrying won't help
                if response.status_code == 404:
                    detail = self._read_error_detail(response)
                    return {"error": f"404 Client Error: Not Found for url: {url}" + (f" - {detail}" if detail else "")}
                if response.status_code == 503:
                    # Extract API error detail from FastAPI JSON response
                    detail = self._read_error_detail(response)
                    if _retry_503 < 2:
                        _retry_503 += 1
                        if self.debug:
                            print(f"503 from {url} (retry {_retry_503}/2): {detail or 'no detail'}")
                        try:
                            time.sleep(1.0 * _retry_503)
                        except Exception:
                            pass
                        attempts += 1
                        continue
                    return {"error": f"503 Service Unavailable for url: {url}" + (f" - {detail}" if detail else "")}
                if response.status_code == 500:
                    detail = self._read_error_detail(response)
                    return {"error": f"500 Internal Server Error for url: {url}" + (f" - {detail}" if detail else "")}
                
                response.raise_for_status()
                self._fail_counts = 0
                self._breaker_until = None
                return response.json()
            except requests.exceptions.ConnectionError as e:
                last_error = e
                attempts += 1
                self._fail_counts += 1
                if self.debug:
                    print(f"Connection error (attempt {attempts}): {e}")
                if self._fail_counts >= 3:
                    self._breaker_until = time.time() + 2.0
                try:
                    time.sleep(min(1.5, 0.4 * attempts))
                except Exception:
                    pass
                try:
                    self.session.close()
                    self.session = requests.Session()
                    self.session.headers.update({'Content-Type': 'application/json', 'Accept': 'application/json'})
                    adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10)
                    self.session.mount('http://', adapter)
                    self.session.mount('https://', adapter)
                except Exception:
                    pass
            except requests.exceptions.RequestException as e:
                last_error = e
                attempts += 1
                if self.debug:
                    print(f"Request error (attempt {attempts}): {e}")
                try:
                    time.sleep(min(1.0, 0.3 * attempts))
                except Exception:
                    pass
        return {"error": str(last_error) if last_error else "request_failed"}

    @staticmethod
    def _read_error_detail(response) -> str:
        """Extract error detail from a FastAPI error response body."""
        try:
            body = response.json()
            if isinstance(body, dict):
                return str(body.get("detail") or body.get("message") or "")
        except Exception:
            pass
        try:
            text = (response.text or "").strip()
            if text and len(text) < 500:
                return text
        except Exception:
            pass
        return ""

    def _delete(self, path: str) -> Any:
        url = f"{self.base_url}{path if path.startswith('/') else '/' + path}"
        try:
            response = self.session.delete(url)
            response.raise_for_status()
            try:
                return response.json()
            except Exception:
                return {"status": "deleted"}
        except requests.exceptions.RequestException as e:
            if self.debug:
                print(f"DELETE {url} failed: {e}")
            return {"error": str(e)}

    def _post_stream_text(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}{path if path.startswith('/') else '/' + path}"
        try:
            with self.session.post(url, json=payload, stream=True) as response:
                response.raise_for_status()
                accumulated = ""
                for line in response.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        # OverWatch streaming returns {text: ...}, Ollama returns {response: ...}
                        chunk = obj.get('text') or obj.get('response') or obj.get('message', {}).get('content')
                        if chunk:
                            accumulated += chunk
                    except Exception:
                        continue
                return {"response": accumulated}
        except requests.exceptions.RequestException as e:
            return {"error": str(e)}

    def _post_stream_iter(self, path: str, payload: Dict[str, Any]):
        url = f"{self.base_url}{path if path.startswith('/') else '/' + path}"
        with self.session.post(url, json=payload, stream=True) as response:
            response.raise_for_status()
            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    delta = obj.get('response') or obj.get('text') or (obj.get('message', {}) or {}).get('content')
                    if delta:
                        yield delta
                except Exception:
                    continue

    def chat_stream(self, model: str, prompt: str,
                    temperature: Optional[float] = None,
                    max_tokens: Optional[int] = None,
                    system: Optional[str] = None,
                    images: Optional[List[str]] = None,
                    on_delta: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": True,
            "ephemeral": True,
            "keep_alive": 0
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if system is not None:
            payload["system"] = system
        if images is not None:
            payload["images"] = self._process_images(images)
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        accumulated = ""
        try:
            try:
                for delta in self._post_stream_iter('/generate_stream', payload):
                    # Detect full-text mode: some API servers return progressively
                    # longer strings instead of true deltas. If the chunk starts with
                    # the accumulated text, it's full-text — extract only the new part.
                    if accumulated and len(delta) > len(accumulated) and delta.startswith(accumulated):
                        accumulated = delta
                    elif delta == accumulated:
                        pass  # Duplicate chunk, skip
                    else:
                        accumulated += delta
                    if on_delta:
                        on_delta(delta)
            except requests.exceptions.RequestException:
                try:
                    for delta in self._post_stream_iter('/api/generate', payload):
                        if accumulated and len(delta) > len(accumulated) and delta.startswith(accumulated):
                            accumulated = delta
                        elif delta == accumulated:
                            pass
                        else:
                            accumulated += delta
                        if on_delta:
                            on_delta(delta)
                except requests.exceptions.RequestException:
                    host = os.getenv('OLLAMA_HOST', 'localhost')
                    port = os.getenv('OLLAMA_PORT', '11434')
                    base = f"http://{host}:{port}"
                    url = f"{base}/api/generate"
                    with self.session.post(url, json=payload, stream=True) as response:
                        response.raise_for_status()
                        for line in response.iter_lines(decode_unicode=True):
                            if not line:
                                continue
                            try:
                                obj = json.loads(line)
                                delta = obj.get('response') or obj.get('text') or (obj.get('message', {}) or {}).get('content')
                                if delta:
                                    if accumulated and len(delta) > len(accumulated) and delta.startswith(accumulated):
                                        accumulated = delta
                                    elif delta == accumulated:
                                        pass
                                    else:
                                        accumulated += delta
                                    if on_delta:
                                        on_delta(delta)
                            except Exception:
                                continue
        except requests.exceptions.RequestException as e:
            return {"error": str(e)}
        return {"response": accumulated}
    
    def add_context(self, context_item: Dict[str, Any]) -> None:
        """Add a context item to the context data"""
        self.context_data.append(context_item)
    
    def clear_context(self) -> None:
        """Clear all context data"""
        self.context_data.clear()
    
    def get_context(self) -> List[Dict[str, Any]]:
        """Get current context data"""
        return self.context_data.copy()
    
    def _process_images(self, images: List[str]) -> List[str]:
        """Process images for API request
        
        Args:
            images: List of image paths or base64 encoded images
            
        Returns:
            List of base64 encoded images
        """
        processed_images = []
        
        if self.debug:
            print(f"Processing {len(images)} images...")
        
        for i, image in enumerate(images):
            try:
                # Check if it's a file path that exists
                if os.path.exists(image):
                    # File path - read and encode to raw base64
                    if self.debug:
                        print(f"Image {i+1}: Processing file path: {image}")
                    with open(image, 'rb') as img_file:
                        img_data = img_file.read()
                        img_base64 = base64.b64encode(img_data).decode('utf-8')
                        processed_images.append(img_base64)
                        if self.debug:
                            print(f"Image {i+1}: Encoded to base64 ({len(img_base64)} chars)")
                elif image.startswith('data:image/'):
                    # Data URL format - extract base64 part
                    if self.debug:
                        print(f"Image {i+1}: Processing data URL format")
                    base64_part = image.split(',', 1)[1] if ',' in image else image
                    processed_images.append(base64_part)
                    if self.debug:
                        print(f"Image {i+1}: Extracted base64 part ({len(base64_part)} chars)")
                else:
                    # Assume it's already raw base64 encoded
                    if self.debug:
                        print(f"Image {i+1}: Assuming raw base64 ({len(image)} chars)")
                    processed_images.append(image)
            except Exception as e:
                print(f"Error processing image {image}: {e}")
                continue
        
        if self.debug:
            print(f"Successfully processed {len(processed_images)} images")
                
        return processed_images
    
    def is_vision_model(self, model_name: str) -> bool:
        """Check if a model supports vision capabilities
        
        Args:
            model_name: Name of the model to check
            
        Returns:
            True if the model supports vision, False otherwise
        """
        # Common vision-enabled models
        vision_models = [
            'llava', 'bakllava', 'llava-llama3', 'llava-phi3', 'llava-vicuna',
            'moondream', 'cogvlm', 'minicpm-v', 'qwen-vl', 'internvl'
        ]
        
        model_lower = model_name.lower()
        return any(vision_model in model_lower for vision_model in vision_models)
    
    def capture_screen(self, quality: int = 85, max_size: Optional[int] = None) -> Optional[str]:
        """Capture the current screen and return as base64 encoded string
        
        Args:
            quality: JPEG quality (1-100)
            max_size: Maximum width/height in pixels (maintains aspect ratio)
            
        Returns:
            Base64 encoded image string or None if capture fails
        """
        if not PILLOW_AVAILABLE:
            if self.debug:
                print("PIL/Pillow not available. Install with: pip install Pillow")
            return None
            
        try:
            # Capture the screen
            screenshot = ImageGrab.grab()
            
            # Resize if max_size is specified
            if max_size and (screenshot.width > max_size or screenshot.height > max_size):
                screenshot.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
                if self.debug:
                    print(f"Resized screenshot to {screenshot.width}x{screenshot.height}")
            
            # Convert to JPEG and encode to base64
            buffer = io.BytesIO()
            screenshot.save(buffer, format='JPEG', quality=quality, optimize=True)
            img_data = buffer.getvalue()
            img_base64 = base64.b64encode(img_data).decode('utf-8')
            
            if self.debug:
                print(f"Screen captured: {screenshot.width}x{screenshot.height}, {len(img_base64)} chars")
            
            return img_base64
            
        except Exception as e:
            if self.debug:
                print(f"Screen capture failed: {e}")
            return None
    
    def chat_with_screen(self, model: str, prompt: str,
                        temperature: Optional[float] = None,
                        max_tokens: Optional[int] = None,
                        system: Optional[str] = None,
                        capture_screen: bool = True,
                        screen_quality: int = 85,
                        max_screen_size: Optional[int] = None,
                        additional_images: Optional[List[str]] = None) -> Dict[str, Any]:
        """Chat with automatic screen capture for vision models
        
        Args:
            model: Model name to use
            prompt: The prompt/question
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            system: System message
            capture_screen: Whether to capture screen automatically
            screen_quality: JPEG quality for screen capture (1-100)
            max_screen_size: Maximum screen capture size in pixels
            additional_images: Additional images to include
            
        Returns:
            API response dictionary
        """
        images = additional_images or []
        
        # Auto-capture screen for vision models
        if capture_screen and self.is_vision_model(model):
            screen_image = self.capture_screen(screen_quality, max_screen_size)
            if screen_image:
                images = [screen_image] + images
                if self.debug:
                    print(f"Added screen capture to images (total: {len(images)})")
            elif self.debug:
                print("Screen capture failed, proceeding without it")
        
        return self.chat(model, prompt, temperature, max_tokens, system, images)

def format_model_info(models: List[Dict[str, Any]]) -> None:
    """Format and display model information"""
    if not models:
        print("No models available.")
        return
    
    print(f"\n{'Model Name':<30} {'Size (GB)':<12} {'Modified':<20}")
    print("-" * 65)
    
    for model in models:
        size_gb = model['size'] / (1024**3)  # Convert bytes to GB
        modified = model['modified_at'][:19].replace('T', ' ')  # Format datetime
        print(f"{model['name']:<30} {size_gb:<12.2f} {modified:<20}")
    print()

def interactive_mode(client: OllamaClient) -> None:
    """Run in interactive mode for continuous conversation"""
    print("\n=== Ollama Interactive Mode ===")
    print("Type 'quit', 'exit', or 'q' to exit")
    print("Type 'models' to list available models")
    print("Type 'help' for commands\n")
    
    # Get available models
    models = client.list_models()
    if not models:
        print("No models available. Please check your Ollama installation.")
        return
    
    # Select model
    print("Available models:")
    for i, model in enumerate(models, 1):
        print(f"{i}. {model['name']}")
    
    while True:
        try:
            choice = input("\nSelect a model (number or name): ").strip()
            if choice.isdigit():
                model_idx = int(choice) - 1
                if 0 <= model_idx < len(models):
                    selected_model = models[model_idx]['name']
                    break
            else:
                # Check if model name exists
                model_names = [m['name'] for m in models]
                if choice in model_names:
                    selected_model = choice
                    break
            print("Invalid selection. Please try again.")
        except (ValueError, KeyboardInterrupt):
            print("\nExiting...")
            return
    
    print(f"\nSelected model: {selected_model}")
    print("You can now start chatting!\n")
    
    while True:
        try:
            user_input = input("You: ").strip()
            
            if user_input.lower() in ['quit', 'exit', 'q']:
                print("Goodbye!")
                break
            elif user_input.lower() == 'models':
                format_model_info(models)
                continue
            elif user_input.lower() == 'help':
                print("\nCommands:")
                print("  models - List available models")
                print("  quit/exit/q - Exit the program")
                print("  help - Show this help message\n")
                continue
            elif not user_input:
                continue
            
            print("AI: ", end="", flush=True)
            
            # Send request to API
            response = client.chat(selected_model, user_input)
            
            if "error" in response:
                print(f"Error: {response['error']}")
            else:
                print(response.get('response', 'No response received'))
                
        except KeyboardInterrupt:
            print("\n\nExiting...")
            break
        except Exception as e:
            print(f"\nError: {e}")

def main():
    """Main function to handle command line arguments"""
    parser = argparse.ArgumentParser(
        description="Ollama API Client - Interact with Ollama models via the local API"
    )
    # Get default URL from environment variables
    default_port = os.getenv("API_PORT", "8000")
    default_url = f"http://localhost:{default_port}"
    
    parser.add_argument(
        "--url", 
        default=default_url,
        help=f"API server URL (default: {default_url})"
    )
    parser.add_argument(
        "--model", 
        help="Model name to use for generation"
    )
    parser.add_argument(
        "--prompt", 
        help="Prompt to send to the model"
    )
    parser.add_argument(
        "--temperature", 
        type=float,
        help="Temperature for generation (0.0 to 1.0)"
    )
    parser.add_argument(
        "--max-tokens", 
        type=int,
        help="Maximum number of tokens to generate"
    )
    parser.add_argument(
        "--system", 
        help="System message to set context"
    )
    parser.add_argument(
        "--list-models", 
        action="store_true",
        help="List available models and exit"
    )
    parser.add_argument(
        "--health", 
        action="store_true",
        help="Check API server health and exit"
    )
    parser.add_argument(
        "--interactive", "-i",
        action="store_true",
        help="Run in interactive mode"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug output for image processing"
    )
    parser.add_argument(
        "--images",
        nargs="*",
        help="Image file paths to include with the prompt"
    )
    parser.add_argument(
        "--capture-screen",
        action="store_true",
        help="Automatically capture screen for vision models"
    )
    parser.add_argument(
        "--screen-quality",
        type=int,
        default=85,
        help="JPEG quality for screen capture (1-100, default: 85)"
    )
    parser.add_argument(
        "--max-screen-size",
        type=int,
        help="Maximum screen capture size in pixels (maintains aspect ratio)"
    )
    
    args = parser.parse_args()
    
    # Initialize client
    client = OllamaClient(args.url, debug=args.debug)
    
    # Health check
    if args.health:
        health = client.health_check()
        print(json.dumps(health, indent=2))
        return
    
    # List models
    if args.list_models:
        models = client.list_models()
        format_model_info(models)
        return
    
    # Interactive mode
    if args.interactive or (not args.model and not args.prompt):
        # Check if server is healthy first
        health = client.health_check()
        if health.get("status") != "healthy":
            print(f"API server is not healthy: {health.get('message', 'Unknown error')}")
            print("Please make sure the API server is running and Ollama is accessible.")
            sys.exit(1)
        
        interactive_mode(client)
        return
    
    # Single request mode
    if not args.model or not args.prompt:
        print("Error: Both --model and --prompt are required for single request mode.")
        print("Use --interactive for interactive mode or --help for usage information.")
        sys.exit(1)
    
    # Send single request
    if args.capture_screen:
        response = client.chat_with_screen(
            args.model,
            args.prompt,
            args.temperature,
            args.max_tokens,
            args.system,
            capture_screen=True,
            screen_quality=args.screen_quality,
            max_screen_size=args.max_screen_size,
            additional_images=args.images
        )
    else:
        response = client.chat(
            args.model, 
            args.prompt, 
            args.temperature, 
            args.max_tokens, 
            args.system,
            args.images
        )
    
    if "error" in response:
        print(f"Error: {response['error']}")
        sys.exit(1)
    else:
        print(response.get('response', 'No response received'))

def _local_embed(texts: List[str]) -> Optional[List[List[float]]]:
    """Embed texts via the lazy llama.cpp embedding server (embeddinggemma).

    Used as a fallback when Ollama's embedding API is unavailable — the
    sentence-transformers/all-MiniLM PyTorch fallback has been removed.
    """
    try:
        from AI import embedding_server

        vectors = embedding_server.embed_texts(list(texts))
        if vectors is not None and len(vectors) == len(list(texts)):
            return vectors
    except Exception:
        pass
    return None


if __name__ == "__main__":
    main()
