#!/usr/bin/env python3
"""
Ollama API Client
A client script to interact with the Ollama API Gateway over Tailscale network
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
    """Client for interacting with Ollama API Gateway with queue system for ComoRAG requests"""
    
    def __init__(self, base_url: str = None, debug: bool = False, use_comorag: bool = True, max_queue_size: int = 10, timeout: Optional[int] = 0):
        """Initialize the client with the API base URL"""
        if base_url is None:
            env_overwatch = os.getenv("OVERWATCH_API_URL")
            if env_overwatch and str(env_overwatch).strip():
                base_url = env_overwatch.strip()
            else:
                tailscale_ip = os.getenv("TAILSCALE_SERVER_IP", "127.0.0.1")
                port = os.getenv("API_PORT", "8000")
                if use_comorag and str(port).strip() == "11434":
                    port = "8000"
                base_url = f"http://{tailscale_ip}:{port}"
        
        self.base_url = base_url.rstrip('/')
        self.debug = debug
        self.use_comorag = use_comorag
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
        self.comorag_config = {
            'max_cycles': 5,
            'memory_size': 1000
        }
        self._honcho = None
        self._honcho_session = None
        self._honcho_user_peer = None
        self._honcho_assistant_peer = None
        self._honcho_session_id = os.getenv("LOOPER_HONCHO_SESSION_ID", "looper-agent-session")
        self._honcho_user_peer_id = os.getenv("LOOPER_HONCHO_USER_PEER_ID", "user")
        self._honcho_assistant_peer_id = os.getenv("LOOPER_HONCHO_ASSISTANT_PEER_ID", "looper_agent")
        self._honcho_workspace_id = os.getenv("HONCHO_WORKSPACE_ID", "looper")
        self._honcho_base_url = os.getenv("LOOPER_HONCHO_URL") or os.getenv("HONCHO_URL") or "http://localhost:8000"
        self._honcho_api_key = os.getenv("HONCHO_API_KEY")
        self._initialize_honcho()
        
        self.request_queue = queue.Queue(maxsize=max_queue_size)
        self.queue_processing = False
        self.queue_lock = threading.Lock()
        self.queue_thread = None
        self.active_requests = {}
        self._shutdown_event = threading.Event()
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
    
    def comorag_reason(self, query: str, context: Optional[str] = None, model: Optional[str] = None, max_cycles: int = 5, images: Optional[List[str]] = None) -> Dict[str, Any]:
        """Perform a synchronous ComoRAG reason request"""
        payload = {
            "query": query,
            "max_cycles": max_cycles
        }
        if context:
            payload["context"] = context
        if model:
            payload["model"] = model
        if images:
            # Check if images need to be converted to base64
            b64_images = []
            for img in images:
                if os.path.exists(img):
                    try:
                        with open(img, "rb") as f:
                            b64_images.append(base64.b64encode(f.read()).decode('utf-8'))
                    except Exception as e:
                        if self.debug:
                            print(f"Error encoding image {img}: {e}")
                else:
                    b64_images.append(img)
            if b64_images:
                payload["images"] = b64_images

        result = self._post_json('/comorag/reason', payload)
        return result

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
        """Generate text using the specified model with Honcho-backed memory integration
        
        Args:
            model: Model name to use
            prompt: Text prompt
            temperature: Temperature for generation
            max_tokens: Maximum tokens to generate
            system: System message
            images: List of image paths or base64 encoded images
            context: Additional context items for ComoRAG
            async_request: Whether to process the request asynchronously using the queue
            callback: Function to call when async request completes (receives request_id and result)
            
        Returns:
            If async_request is False: The generation result
            If async_request is True: A dict with request_id and status
        """
        # Use Honcho-backed memory when enabled
        if self.use_comorag:
            if async_request:
                return self._queue_comorag_request(model, prompt, temperature, max_tokens, 
                                                 system, images, context, callback)
            else:
                return self._honcho_reason(model, prompt, temperature, max_tokens, 
                                           system, images, context)
        
        # For non-ComoRAG requests, we don't use the queue system
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

        attempt_count = 0
        last_error: Optional[Exception] = None
        while attempt_count < 2:
            try:
                # IMPORTANT: If OverWatch is enabled and we are hitting its /embeddings wrapper,
                # we must send a payload that matches its API schema (inputs, not input)
                payload["model"] = model
                payload["inputs"] = inputs if isinstance(inputs, list) else [inputs]
                
                # Try standard OverWatch endpoint first
                data = self._post_json('/embeddings', payload)
                
                # Check if it failed (e.g. 404 on direct Ollama connection) and try native Ollama endpoints
                if isinstance(data, dict) and "error" in data:
                    err_msg = str(data["error"]).lower()
                    if "404" in err_msg or "not found" in err_msg or "failed" in err_msg:
                        if self.debug:
                            print(f"OverWatch /embeddings failed ({err_msg}), trying local Ollama fallback", file=sys.stderr)
                        
                        # Prepare Ollama native payload
                        ollama_payload = {"model": model, "input": inputs}
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
                                    legacy_payload = {"model": model, "prompt": inputs}
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
                    if len(vecs) != len(inputs):
                        return False
                    for v in vecs:
                        if not isinstance(v, list) or len(v) == 0:
                            return False
                    return True

                if not _valid(vectors):
                    if self.debug:
                        print(f"Batch embeddings failed validation. Falling back to per-input processing for {len(inputs)} inputs.")
                    
                    per_vectors: List[List[float]] = []
                    for idx, t in enumerate(inputs):
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
                        print(f"Embeddings fallback failed. Inputs: {len(inputs)}, Valid vectors: {valid_count}")
                        
                    raise ValueError(f"Empty embedding vectors (valid: {valid_count}/{len(inputs)})")

                try:
                    # Unload embedding model after use via cleanup endpoint
                    # self._post_json('/cleanup', {"model": model, "retries": 3, "wait": 0.5})
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
            print("Embeddings API did not return a usable result; no local fallback will be used.")
        # Attempt to unload embedding model to keep memory clean
        try:
            # self._post_json('/cleanup', {"model": model, "retries": 3, "wait": 0.5})
            pass
        except Exception:
            pass
        return []

    
    def _queue_comorag_request(self, model: str, prompt: str,
                              temperature: Optional[float] = None,
                              max_tokens: Optional[int] = None,
                              system: Optional[str] = None,
                              images: Optional[List[str]] = None,
                              context: Optional[List[Dict]] = None,
                              callback: Optional[Callable[[str, Dict[str, Any]], None]] = None) -> Dict[str, Any]:
        """Queue a ComoRAG request for asynchronous processing
        
        Args:
            model: Model name to use
            prompt: Text prompt
            temperature: Temperature for generation
            max_tokens: Maximum tokens to generate
            system: System message
            images: List of image paths or base64 encoded images
            context: Additional context items for ComoRAG
            callback: Function to call when request completes
            
        Returns:
            Dict with request_id and status
        """
        # Generate a unique request ID
        request_id = f"req_{int(time.time())}_{threading.get_ident()}_{len(self.active_requests)}"
        
        # Create request object
        request = {
            "id": request_id,
            "model": model,
            "prompt": prompt,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "system": system,
            "images": images.copy() if images else None,
            "context": context.copy() if context else None,
            "callback": callback,
            "timestamp": time.time()
        }
        
        # Add to queue
        try:
            self.request_queue.put(request, block=False)
            self.active_requests[request_id] = {"status": "queued", "timestamp": time.time()}
            
            if self.debug:
                print(f"Queued ComoRAG request {request_id} (queue size: {self.request_queue.qsize()})")
            
            # Start queue processing if not already running
            self._ensure_queue_processing()
            
            return {"request_id": request_id, "status": "queued"}
        except queue.Full:
            if self.debug:
                print(f"ComoRAG request queue full, processing synchronously")
            # If queue is full, process synchronously
            result = self._honcho_reason(model, prompt, temperature, max_tokens, system, images, context)
            return result
    
    def _ensure_queue_processing(self):
        """Ensure the queue processing thread is running"""
        with self.queue_lock:
            if not self.queue_processing and not self._shutdown_event.is_set():
                self.queue_processing = True
                self.queue_thread = threading.Thread(target=self._process_queue, daemon=True)
                self.queue_thread.start()
                if self.debug:
                    print("Started ComoRAG queue processing thread")
    
    def _process_queue(self):
        while True:
            try:
                # Get next request from queue with timeout
                try:
                    request = self.request_queue.get(timeout=1.0)
                except queue.Empty:
                    # Check if we should continue processing
                    with self.queue_lock:
                        if self._shutdown_event.is_set() or self.request_queue.empty():
                            self.queue_processing = False
                            if self.debug:
                                print("ComoRAG queue empty, stopping processing thread")
                            break
                        continue
                
                # Update request status
                request_id = request["id"]
                self.active_requests[request_id] = {"status": "processing", "timestamp": time.time()}
                
                if self.debug:
                    print(f"Processing ComoRAG request {request_id}")
                
                # Process the request
                try:
                    result = self._honcho_reason(
                        request["model"], 
                        request["prompt"],
                        request["temperature"],
                        request["max_tokens"],
                        request["system"],
                        request["images"],
                        request["context"]
                    )
                    
                    # Update request status
                    self.active_requests[request_id] = {"status": "completed", "timestamp": time.time()}
                    
                    # Call callback if provided
                    if request["callback"]:
                        try:
                            request["callback"](request_id, result)
                        except Exception as e:
                            if self.debug:
                                print(f"Error in callback for request {request_id}: {e}")
                    
                    if self.debug:
                        print(f"Completed ComoRAG request {request_id}")
                        
                except Exception as e:
                    # Update request status on error
                    self.active_requests[request_id] = {"status": "error", "error": str(e), "timestamp": time.time()}
                    
                    if self.debug:
                        print(f"Error processing ComoRAG request {request_id}: {e}")
                    
                    # Call callback with error if provided
                    if request["callback"]:
                        try:
                            request["callback"](request_id, {"error": str(e)})
                        except Exception as cb_error:
                            if self.debug:
                                print(f"Error in callback for request {request_id}: {cb_error}")
                
                # Mark task as done
                self.request_queue.task_done()
                
            except Exception as e:
                if self.debug:
                    print(f"Error in ComoRAG queue processing: {e}")
                time.sleep(1)  # Prevent tight loop on persistent errors

    def shutdown(self, wait: float = 2.0):
        try:
            self._shutdown_event.set()
            with self.queue_lock:
                thread = self.queue_thread
                self.queue_processing = False
            try:
                while True:
                    req = self.request_queue.get_nowait()
                    rid = req.get("id")
                    if rid:
                        self.active_requests[rid] = {"status": "cancelled", "timestamp": time.time()}
                    self.request_queue.task_done()
            except queue.Empty:
                pass
            if thread and thread.is_alive():
                try:
                    thread.join(timeout=max(0.0, wait))
                except Exception:
                    pass
            try:
                if hasattr(self, "session") and self.session:
                    self.session.close()
            except Exception:
                pass
            try:
                self.active_requests.clear()
            except Exception:
                pass
        except Exception:
            pass

    def close(self):
        self.shutdown()

    def __del__(self):
        try:
            self.shutdown(wait=0.5)
        except Exception:
            pass
    
    def get_request_status(self, request_id: str) -> Dict[str, Any]:
        """Get the status of a queued or processing request
        
        Args:
            request_id: The request ID returned from _queue_comorag_request
            
        Returns:
            Dict with status information
        """
        if request_id in self.active_requests:
            return self.active_requests[request_id]
        return {"status": "not_found"}
    
    def get_queue_status(self) -> Dict[str, Any]:
        """Get the status of the request queue
        
        Returns:
            Dict with queue status information
        """
        return {
            "queue_size": self.request_queue.qsize(),
            "active_requests": len(self.active_requests),
            "is_processing": self.queue_processing
        }
    
    def clear_queue(self) -> Dict[str, Any]:
        """Clear all pending requests from the queue
        
        Returns:
            Dict with operation status
        """
        # Create a new queue to replace the old one
        with self.queue_lock:
            old_size = self.request_queue.qsize()
            self.request_queue = queue.Queue(maxsize=self.request_queue.maxsize)
            
            # Update status for queued requests
            for req_id, status in self.active_requests.items():
                if status["status"] == "queued":
                    self.active_requests[req_id] = {"status": "cancelled", "timestamp": time.time()}
        
        return {"status": "success", "cleared_requests": old_size}

    def _initialize_honcho(self) -> None:
        try:
            repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            sdk_path = os.path.join(repo_root, "utils", "honcho", "sdks", "python", "src")
            if os.path.isdir(sdk_path):
                if sdk_path not in sys.path:
                    sys.path.append(sdk_path)
            from honcho import Honcho

            self._honcho = Honcho(
                api_key=self._honcho_api_key,
                base_url=self._honcho_base_url,
                workspace_id=self._honcho_workspace_id,
            )
            self._honcho_session = self._honcho.session(self._honcho_session_id)
            self._honcho_user_peer = self._honcho.peer(self._honcho_user_peer_id)
            self._honcho_assistant_peer = self._honcho.peer(self._honcho_assistant_peer_id)
        except Exception:
            self._honcho = None
            self._honcho_session = None
            self._honcho_user_peer = None
            self._honcho_assistant_peer = None

    def _format_honcho_context(self, session_context) -> str:
        try:
            parts = []
            summary = getattr(session_context, "summary", None)
            if summary and getattr(summary, "content", None):
                parts.append(f"[Summary]\n{summary.content}".strip())
            messages = getattr(session_context, "messages", None) or []
            if messages:
                lines = []
                for m in messages:
                    peer_id = getattr(m, "peer_id", "") or ""
                    content = getattr(m, "content", "") or ""
                    if not content:
                        continue
                    lines.append(f"{peer_id}: {content}")
                if lines:
                    parts.append("[Messages]\n" + "\n".join(lines))
            return "\n\n".join([p for p in parts if p]).strip()
        except Exception:
            return ""

    def _honcho_reason(self, model: str, prompt: str,
                       temperature: Optional[float] = None,
                       max_tokens: Optional[int] = None,
                       system: Optional[str] = None,
                       images: Optional[List[str]] = None,
                       context: Optional[List[Dict]] = None) -> Dict[str, Any]:
        context_str = ""
        if context or self.context_data:
            context_items = context or self.context_data
            context_parts = []
            for item in context_items:
                if isinstance(item, dict):
                    if 'content' in item:
                        content = item['content']
                        source = item.get('source', 'unknown')
                        item_type = item.get('type', 'context')
                        context_parts.append(f"[{source} - {item_type}]: {content}")
                    elif 'ocr_text' in item:
                        ocr_text = item['ocr_text']
                        if ocr_text and ocr_text.strip():
                            context_parts.append(f"[Screen Text - OCR]: {ocr_text}")
                    elif 'text' in item:
                        context_parts.append(f"[Context]: {item['text']}")
                    elif 'workflow_state' in item:
                        workflow_info = item['workflow_state']
                        context_parts.append(f"[Workflow State]: Current node: {workflow_info.get('current_node', 'unknown')}, Variables: {workflow_info.get('variables', {})}")
                    elif 'screenshot_path' in item:
                        screenshot_info = item.get('description', 'Screenshot captured')
                        context_parts.append(f"[Screenshot Context]: {screenshot_info}")
                elif isinstance(item, str):
                    context_parts.append(f"[Context]: {item}")
            if context_parts:
                context_str = "\n\n".join(context_parts)

        honcho_context_text = ""
        honcho_meta: Dict[str, Any] = {"backend": "honcho"}
        if self._honcho_session:
            try:
                tokens = int(self.comorag_config.get("memory_size", 1000) or 1000)
                session_context = self._honcho_session.context(
                    summary=True,
                    tokens=tokens,
                    search_query=prompt or "",
                )
                honcho_context_text = self._format_honcho_context(session_context)
                honcho_meta["memory_messages"] = len(getattr(session_context, "messages", []) or [])
                honcho_meta["has_summary"] = bool(getattr(session_context, "summary", None))
            except Exception:
                honcho_context_text = ""

        system_parts = []
        if system:
            system_parts.append(system)
        if context_str:
            system_parts.append(f"[Runtime Context]\n{context_str}")
        if honcho_context_text:
            system_parts.append(f"[Honcho Memory]\n{honcho_context_text}")
        final_system = "\n\n".join([p for p in system_parts if p]).strip() or None

        res = self.chat(model, prompt, temperature, max_tokens, final_system, images, stream=False)

        response_text = ""
        try:
            if isinstance(res, dict):
                response_text = (
                    res.get("response")
                    or res.get("text")
                    or (res.get("message") or {}).get("content")
                    or ""
                )
        except Exception:
            response_text = ""

        if self._honcho_session and self._honcho_user_peer and self._honcho_assistant_peer:
            try:
                msgs = []
                if prompt is not None:
                    msgs.append(
                        self._honcho_user_peer.message(
                            str(prompt),
                            metadata={"looper_type": "turn", "model": str(model)},
                        )
                    )
                if response_text:
                    msgs.append(
                        self._honcho_assistant_peer.message(
                            str(response_text),
                            metadata={"looper_type": "turn", "model": str(model)},
                        )
                    )
                if msgs:
                    self._honcho_session.add_messages(msgs)
            except Exception:
                pass

        if isinstance(res, dict):
            res["comorag_metadata"] = {
                "confidence": 0.0,
                "reasoning_cycles": 1,
                "memory_items_used": honcho_meta.get("memory_messages", 0),
                "reasoning_history": [],
                "vision_analysis": None,
                "memory_backend": "honcho",
            }
        return res if isinstance(res, dict) else {"response": str(res)}
    
    def _comorag_reason(self, model: str, prompt: str, 
                       temperature: Optional[float] = None,
                       max_tokens: Optional[int] = None,
                       system: Optional[str] = None,
                       images: Optional[List[str]] = None,
                       context: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """Use ComoRAG reasoning endpoint for enhanced context-aware generation"""
        
        # Prepare context string from context items
        context_str = ""
        if context or self.context_data:
            context_items = context or self.context_data
            context_parts = []
            
            for item in context_items:
                if isinstance(item, dict):
                    # Handle different context item formats
                    if 'content' in item:
                        content = item['content']
                        source = item.get('source', 'unknown')
                        item_type = item.get('type', 'context')
                        context_parts.append(f"[{source} - {item_type}]: {content}")
                    elif 'ocr_text' in item:
                        ocr_text = item['ocr_text']
                        if ocr_text and ocr_text.strip():
                            context_parts.append(f"[Screen Text - OCR]: {ocr_text}")
                    elif 'text' in item:
                        context_parts.append(f"[Context]: {item['text']}")
                    elif 'workflow_state' in item:
                        # Handle workflow state context
                        workflow_info = item['workflow_state']
                        context_parts.append(f"[Workflow State]: Current node: {workflow_info.get('current_node', 'unknown')}, Variables: {workflow_info.get('variables', {})}")
                    elif 'screenshot_path' in item:
                        # Handle screenshot context
                        screenshot_info = item.get('description', 'Screenshot captured')
                        context_parts.append(f"[Screenshot Context]: {screenshot_info}")
                elif isinstance(item, str):
                    context_parts.append(f"[Context]: {item}")
            
            if context_parts:
                context_str = "\n\n".join(context_parts)
        
        # Add system message to context if provided
        if system:
            full_context = f"System: {system}\n\n{context_str}" if context_str else f"System: {system}"
        else:
            full_context = context_str
        
        # ComoRAG API expects 'query' not 'prompt'
        payload = {
            "query": prompt,
            "model": model,
            "max_cycles": self.comorag_config.get('max_cycles', 5),
            "memory_size": self.comorag_config.get('memory_size', 1000)
        }

        if temperature is not None:
            payload["temperature"] = temperature
        
        # Add context if available
        if full_context:
            payload["context"] = full_context
        
        # Process and add images for vision analysis
        if images:
            processed_images = self._process_images(images)
            if processed_images:
                payload["images"] = processed_images
                if self.debug:
                    print(f"Added {len(processed_images)} images to ComoRAG request")
        
        if self.debug:
            print(f"ComoRAG request payload: {json.dumps(payload, indent=2)}")
        
        try:
            # First ensure engine is initialized
            try:
                st = self.session.get(f"{self.base_url.rstrip('/')}/comorag/status?init_if_needed=true", timeout=8)
                if st.status_code == 200:
                    js = st.json()
                    if not js.get("ready", False):
                        if self.debug:
                            print("ComoRAG status NOT ready. Attempting explicit initialization...")
                        init_payload = {"memory_size": int(self.comorag_config.get("memory_size", 1000))}
                        _ = self._post_json('/comorag/init', init_payload)
                else:
                    if self.debug:
                        print(f"ComoRAG status check failed with code {st.status_code}. Attempting explicit initialization...")
                    init_payload = {"memory_size": int(self.comorag_config.get("memory_size", 1000))}
                    _ = self._post_json('/comorag/init', init_payload)
            except Exception as e:
                if self.debug:
                    print(f"Error during reasoning status check/init: {e}")
                pass
            
            result = self._post_json('/comorag/reason', payload)
            
            if self.debug:
                print(f"ComoRAG response: {json.dumps(result, indent=2)}")
            
            # Transform ComoRAG response to match expected format
            if 'response' in result:
                return {
                    'response': result['response'],
                    'comorag_metadata': {
                        'confidence': result.get('confidence', 0.0),
                        'reasoning_cycles': result.get('reasoning_cycles', 0),
                        'memory_items_used': result.get('memory_items_used', 0),
                        'reasoning_history': result.get('reasoning_history', []),
                        'vision_analysis': result.get('vision_analysis', None)
                    }
                }
            elif 'choices' in result and len(result['choices']) > 0:
                # Handle OpenAI-compatible response format
                choice = result['choices'][0]
                response_text = choice.get('message', {}).get('content', '') or choice.get('text', '')
                return {
                    'response': response_text,
                    'comorag_metadata': result.get('comorag_metadata', {})
                }
            else:
                # If no expected response format, fall back to regular chat
                if self.debug:
                    print(f"ComoRAG endpoint returned unexpected format: {result}")
                    print("Falling back to regular chat endpoint")
                res = self.chat(model, prompt, temperature, max_tokens, system, images)
                if isinstance(res, dict):
                    res['comorag_fallback'] = True
                    res['comorag_error'] = f"Unexpected format: {result}"
                return res
                
        except requests.exceptions.RequestException as e:
            if self.debug:
                print(f"ComoRAG request failed: {e}")
                print("Falling back to regular chat endpoint")
            # Fall back to regular chat if ComoRAG fails
            res = self.chat(model, prompt, temperature, max_tokens, system, images)
            if isinstance(res, dict):
                res['comorag_fallback'] = True
                res['comorag_error'] = str(e)
            return res

    def _post_json(self, path: str, payload: Dict[str, Any]) -> Any:
        if self._shutdown_event.is_set():
            return {"error": "client_shutdown"}
        url = f"{self.base_url}{path if path.startswith('/') else '/' + path}"
        attempts = 0
        last_error: Optional[Exception] = None
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
                    return {"error": f"404 Client Error: Not Found for url: {url}"}
                if response.status_code == 503:
                    return {"error": f"503 Service Unavailable: ComoRAG not ready for url: {url}"}
                if response.status_code == 500:
                    return {"error": f"500 Internal Server Error for url: {url}"}
                
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

    def _delete(self, path: str) -> Any:
        if self._shutdown_event.is_set():
            return {"error": "client_shutdown"}
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
        if self._shutdown_event.is_set():
            return {"error": "client_shutdown"}
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
                    accumulated += delta
                    if on_delta:
                        on_delta(delta)
            except requests.exceptions.RequestException:
                try:
                    for delta in self._post_stream_iter('/api/generate', payload):
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
                                    accumulated += delta
                                    if on_delta:
                                        on_delta(delta)
                            except Exception:
                                continue
        except requests.exceptions.RequestException as e:
            return {"error": str(e)}
        return {"response": accumulated}
    
    def add_context(self, context_item: Dict[str, Any]) -> None:
        """Add a context item to the ComoRAG context data"""
        self.context_data.append(context_item)
    
    def clear_context(self) -> None:
        """Clear all context data"""
        self.context_data.clear()
    
    def get_context(self) -> List[Dict[str, Any]]:
        """Get current context data"""
        return self.context_data.copy()
    
    def set_comorag_config(self, max_cycles: int = None, memory_size: int = None) -> None:
        """Set ComoRAG configuration parameters"""
        if max_cycles is not None:
            self.comorag_config['max_cycles'] = max_cycles
        if memory_size is not None:
            self.comorag_config['memory_size'] = memory_size

    def add_memory(self, content: str, source: str = "external", metadata: Dict[str, Any] = None, image_data: str = None) -> bool:
        """Add memory to ComoRAG engine
        
        Args:
            content: Text content to add to memory
            source: Source of the memory (e.g., "vision", "user", etc.)
            metadata: Additional metadata
            image_data: Base64 encoded image data
            
        Returns:
            True if successful, False otherwise
        """
        if not self.use_comorag:
            if self.debug:
                print("ComoRAG is disabled. Cannot add memory.")
            return False
            
        payload = {
            "content": content,
            "source": source,
            "metadata": metadata,
            "image_data": image_data
        }
        
        if self.debug:
            print(f"Adding memory to ComoRAG: source={source}, content_len={len(content)}")
        
        try:
            # First ensure engine is initialized
            try:
                st = self.session.get(f"{self.base_url.rstrip('/')}/comorag/status?init_if_needed=true", timeout=8)
                if st.status_code == 200:
                    js = st.json()
                    if not js.get("ready", False):
                        if self.debug:
                            print("ComoRAG status NOT ready. Attempting explicit initialization...")
                        init_payload = {"memory_size": int(self.comorag_config.get("memory_size", 1000))}
                        _ = self._post_json('/comorag/init', init_payload)
                else:
                    if self.debug:
                        print(f"ComoRAG status check failed with code {st.status_code}. Attempting explicit initialization...")
                    init_payload = {"memory_size": int(self.comorag_config.get("memory_size", 1000))}
                    _ = self._post_json('/comorag/init', init_payload)
            except Exception as e:
                if self.debug:
                    print(f"Error during status check/init: {e}")
                pass
            
            # Now add memory
            result = self._post_json('/comorag/memory', payload)
            
            if isinstance(result, dict) and "error" not in result:
                if self.debug:
                    print("Successfully added memory to ComoRAG")
                return True
            else:
                if self.debug:
                    err = result.get("error") if isinstance(result, dict) else "Unknown error"
                    print(f"Failed to add memory: {err}")
                return False
                
        except Exception as e:
            if self.debug:
                print(f"Error adding memory to ComoRAG: {e}")
            return False

    def clear_memory(self) -> bool:
        """Clear all ComoRAG memory.
        
        Returns:
            True if successful, False otherwise
        """
        if not self.use_comorag:
            return True
            
        try:
            if self.debug:
                print("Clearing ComoRAG memory...")
                
            result = self._delete('/comorag/memory')
            
            if isinstance(result, dict) and "error" not in result:
                if self.debug:
                    print("Successfully cleared ComoRAG memory")
                return True
            else:
                if self.debug:
                    err = result.get("error") if isinstance(result, dict) else "Unknown error"
                    print(f"Failed to clear memory: {err}")
                return False
        except Exception as e:
            if self.debug:
                print(f"Error clearing ComoRAG memory: {e}")
            return False

    
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
        description="Ollama API Client - Interact with Ollama models via Tailscale API"
    )
    # Get default URL from environment variables
    default_tailscale_ip = os.getenv("TAILSCALE_SERVER_IP", "localhost")
    default_port = os.getenv("API_PORT", "8000")
    default_url = f"http://{default_tailscale_ip}:{default_port}"
    
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

if __name__ == "__main__":
    main()
