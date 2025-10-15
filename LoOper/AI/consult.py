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

try:
    from PIL import Image, ImageGrab
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False
    Image = None
    ImageGrab = None

class OllamaClient:
    """Client for interacting with Ollama API Gateway with queue system for ComoRAG requests"""
    
    def __init__(self, base_url: str = None, debug: bool = False, use_comorag: bool = True, max_queue_size: int = 10, timeout: int = 300):
        """Initialize the client with the API base URL"""
        # Use environment variable for Tailscale IP, fallback to localhost
        if base_url is None:
            tailscale_ip = os.getenv("TAILSCALE_SERVER_IP", "localhost")
            port = os.getenv("API_PORT", "8000")
            base_url = f"http://{tailscale_ip}:{port}"
        
        self.base_url = base_url.rstrip('/')
        self.debug = debug
        self.use_comorag = use_comorag
        self.timeout = timeout  # Default timeout for requests (5 minutes for local models)
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        })
        # Set default timeout for all requests in this session
        self.session.timeout = timeout
        self.context_data = []  # Store context for ComoRAG
        self.comorag_config = {
            'max_cycles': 5,
            'memory_size': 1000
        }
        
        # Queue system for ComoRAG requests
        self.request_queue = queue.Queue(maxsize=max_queue_size)
        self.queue_processing = False
        self.queue_lock = threading.Lock()
        self.queue_thread = None
        self.active_requests = {}  # Track active requests by ID
    
    def health_check(self) -> Dict[str, Any]:
        """Check if the API server and Ollama are healthy"""
        try:
            response = self.session.get(f"{self.base_url}/health", timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            return {"status": "error", "message": str(e)}
    
    def list_models(self) -> List[Dict[str, Any]]:
        """Get list of available models"""
        try:
            response = self.session.get(f"{self.base_url}/models", timeout=10)
            response.raise_for_status()
            data = response.json()
            # Extract the models array from the response
            if isinstance(data, dict) and 'models' in data:
                return data['models']
            elif isinstance(data, list):
                # Handle case where API returns models directly as a list
                return data
            else:
                print(f"Unexpected response format: {data}")
                return []
        except requests.exceptions.RequestException as e:
            print(f"Error fetching models: {e}")
            return []
    
    def chat(self, model: str, prompt: str, 
             temperature: Optional[float] = None,
             max_tokens: Optional[int] = None,
             system: Optional[str] = None,
             images: Optional[List[str]] = None) -> Dict[str, Any]:
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
            "stream": False
        }
        
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if system is not None:
            payload["system"] = system
        if images is not None:
            payload["images"] = self._process_images(images)
        
        try:
            response = self.session.post(
                f"{self.base_url}/generate",
                json=payload,
                timeout=self.timeout  # Use configured timeout
            )
            response.raise_for_status()
            result = response.json()
            
            # Normalize response format - convert 'text' to 'response' if needed
            if 'text' in result and 'response' not in result:
                result['response'] = result['text']
            
            return result
        except requests.exceptions.RequestException as e:
            return {"error": str(e)}
    
    def generate(self, model: str, prompt: str, 
                temperature: Optional[float] = None,
                max_tokens: Optional[int] = None,
                system: Optional[str] = None,
                images: Optional[List[str]] = None,
                context: Optional[List[Dict]] = None,
                async_request: bool = False,
                callback: Optional[Callable[[str, Dict[str, Any]], None]] = None) -> Dict[str, Any]:
        """Generate text using the specified model with optional ComoRAG integration
        
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
        # Use ComoRAG if enabled (context is not required for ComoRAG)
        if self.use_comorag:
            if async_request:
                return self._queue_comorag_request(model, prompt, temperature, max_tokens, 
                                                 system, images, context, callback)
            else:
                return self._comorag_reason(model, prompt, temperature, max_tokens, 
                                           system, images, context)
        
        # For non-ComoRAG requests, we don't use the queue system
        return self.chat(model, prompt, temperature, max_tokens, system, images)
    
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
            result = self._comorag_reason(model, prompt, temperature, max_tokens, system, images, context)
            return result
    
    def _ensure_queue_processing(self):
        """Ensure the queue processing thread is running"""
        with self.queue_lock:
            if not self.queue_processing:
                self.queue_processing = True
                self.queue_thread = threading.Thread(target=self._process_queue, daemon=True)
                self.queue_thread.start()
                if self.debug:
                    print("Started ComoRAG queue processing thread")
    
    def _process_queue(self):
        """Process queued ComoRAG requests"""
        while True:
            try:
                # Get next request from queue with timeout
                try:
                    request = self.request_queue.get(timeout=1.0)
                except queue.Empty:
                    # Check if we should continue processing
                    with self.queue_lock:
                        if self.request_queue.empty():
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
                    result = self._comorag_reason(
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
            response = self.session.post(
                f"{self.base_url}/comorag/reason",
                json=payload,
                timeout=360  # 6 minutes timeout for ComoRAG reasoning
            )
            response.raise_for_status()
            result = response.json()
            
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
                return self.chat(model, prompt, temperature, max_tokens, system, images)
                
        except requests.exceptions.RequestException as e:
            if self.debug:
                print(f"ComoRAG request failed: {e}")
                print("Falling back to regular chat endpoint")
            # Fall back to regular chat if ComoRAG fails
            return self.chat(model, prompt, temperature, max_tokens, system, images)
    
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