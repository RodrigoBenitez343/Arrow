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
from typing import Optional, List, Dict, Any
from urllib.parse import urljoin

class OllamaClient:
    """Client for interacting with Ollama API Gateway"""
    
    def __init__(self, base_url: str = None, debug: bool = False):
        """Initialize the client with the API base URL"""
        # Use environment variable for Tailscale IP, fallback to localhost
        if base_url is None:
            tailscale_ip = os.getenv("TAILSCALE_SERVER_IP", "localhost")
            port = os.getenv("API_PORT", "8000")
            base_url = f"http://{tailscale_ip}:{port}"
        
        self.base_url = base_url.rstrip('/')
        self.debug = debug
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        })
    
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
            return response.json()
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
                f"{self.base_url}/chat",
                json=payload,
                timeout=120  # 2 minutes timeout
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            return {"error": str(e)}
    
    def generate(self, model: str, prompt: str, 
                temperature: Optional[float] = None,
                max_tokens: Optional[int] = None,
                system: Optional[str] = None,
                images: Optional[List[str]] = None) -> Dict[str, Any]:
        """Generate text using the specified model (alias for chat)"""
        return self.chat(model, prompt, temperature, max_tokens, system, images)
    
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