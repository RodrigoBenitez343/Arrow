#!/usr/bin/env python3
"""
Model Cache Module

Provides a global cache for storing and accessing preloaded models
to prevent blocking UI operations when fetching models from the API.
"""

import threading
import time
from typing import List, Optional
from AI.consult import OllamaClient
import os

class ModelCache:
    """Global cache for storing available models"""
    
    def __init__(self):
        self._models: List[str] = []
        self._loading = False
        self._loaded = False
        self._lock = threading.Lock()
        self._fallback_models = ["llama3.2", "llama3.1", "codellama", "mistral", "phi3"]
    
    def get_default_api_url(self) -> str:
        """Get the default API URL from environment variables"""
        tailscale_ip = os.getenv('TAILSCALE_SERVER_IP', 'localhost')
        api_port = os.getenv('API_PORT', '8000')
        return f"http://{tailscale_ip}:{api_port}"
    
    def load_models_async(self, api_url: Optional[str] = None) -> None:
        """Load models asynchronously in a separate thread"""
        if self._loading or self._loaded:
            return
            
        self._loading = True
        thread = threading.Thread(target=self._fetch_models, args=(api_url,))
        thread.daemon = True
        thread.start()
    
    def _fetch_models(self, api_url: Optional[str] = None) -> None:
        """Internal method to fetch models from the API"""
        if api_url is None:
            api_url = self.get_default_api_url()
            
        try:
            print(f"Loading models from {api_url}...")
            client = OllamaClient(api_url)
            models = client.list_models()
            
            with self._lock:
                if models:
                    self._models = [model['name'] for model in models]
                    print(f"Successfully loaded {len(self._models)} models: {self._models}")
                else:
                    self._models = self._fallback_models.copy()
                    print(f"No models returned from API, using fallback models: {self._models}")
                    
                self._loaded = True
                self._loading = False
                
        except Exception as e:
            print(f"Error loading models from API: {e}")
            with self._lock:
                self._models = self._fallback_models.copy()
                self._loaded = True
                self._loading = False
                print(f"Using fallback models due to error: {self._models}")
    
    def get_models(self, timeout: float = 5.0) -> List[str]:
        """Get the cached models, waiting up to timeout seconds if still loading"""
        start_time = time.time()
        
        while self._loading and (time.time() - start_time) < timeout:
            time.sleep(0.1)
        
        with self._lock:
            if self._loaded:
                return self._models.copy()
            else:
                # If still loading or failed to load, return fallback models
                print("Models not yet loaded, returning fallback models")
                return self._fallback_models.copy()
    
    def is_loaded(self) -> bool:
        """Check if models have been loaded"""
        with self._lock:
            return self._loaded
    
    def is_loading(self) -> bool:
        """Check if models are currently being loaded"""
        return self._loading
    
    def refresh_models(self, api_url: Optional[str] = None) -> None:
        """Force refresh the model cache"""
        with self._lock:
            self._loaded = False
            self._loading = False
            self._models = []
        
        self.load_models_async(api_url)

# Global instance
_model_cache = ModelCache()

def get_model_cache() -> ModelCache:
    """Get the global model cache instance"""
    return _model_cache

def get_cached_models(timeout: float = 5.0) -> List[str]:
    """Convenience function to get cached models"""
    return _model_cache.get_models(timeout)

def load_models_at_startup(api_url: Optional[str] = None) -> None:
    """Load models at application startup"""
    print("Starting model preload at application startup...")
    _model_cache.load_models_async(api_url)