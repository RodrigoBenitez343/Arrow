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
        self._using_fallback = False
    
    def get_default_api_url(self) -> str:
        """Get the default API URL from environment variables"""
        api_port = os.getenv('API_PORT', '8000')
        if not api_port or not str(api_port).strip():
            api_port = "8000"
        return f"http://localhost:{api_port}"
    
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
                    # models can be list of dicts (from gateway) or list of strings (from Ollama fallback)
                    names = []
                    for m in models:
                        if isinstance(m, dict) and 'name' in m:
                            names.append(m['name'])
                        elif isinstance(m, str):
                            names.append(m)
                    self._models = names
                    print(f"Successfully loaded {len(self._models)} models: {self._models}")
                    self._using_fallback = False
                else:
                    self._models = self._fallback_models.copy()
                    print(f"No models returned from API, using fallback models: {self._models}")
                    self._using_fallback = True
                    
                self._loaded = True
                self._loading = False
                
        except Exception as e:
            print(f"Error loading models from API: {e}")
            with self._lock:
                self._models = self._fallback_models.copy()
                self._loaded = True
                self._loading = False
                self._using_fallback = True
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
        print(f"Force refreshing models from API: {api_url}")
        with self._lock:
            self._loaded = False
            self._loading = True
            self._models = []
            self._using_fallback = False
        
        # Run synchronously to ensure models are loaded before returning
        try:
            import asyncio
            # Try to get existing loop, create new one if needed
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # If loop is running, we need to run in a thread
                    import threading
                    import concurrent.futures
                    
                    def sync_load():
                        new_loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(new_loop)
                        try:
                            return new_loop.run_until_complete(self._fetch_models_async(api_url))
                        finally:
                            new_loop.close()
                    
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future = executor.submit(sync_load)
                        models = future.result(timeout=5.0)
                else:
                    models = loop.run_until_complete(self._fetch_models_async(api_url))
            except RuntimeError:
                # No event loop, create one
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    models = loop.run_until_complete(self._fetch_models_async(api_url))
                finally:
                    loop.close()
            
            with self._lock:
                if models:
                    self._models = models
                    print(f"Successfully refreshed {len(models)} models: {models}")
                    self._using_fallback = False
                else:
                    self._models = self._fallback_models
                    print("Failed to fetch models, using fallback")
                    self._using_fallback = True
                self._loaded = True
                self._loading = False
                
        except Exception as e:
            print(f"Error during model refresh: {e}")
            with self._lock:
                self._models = self._fallback_models
                self._loaded = True
                self._loading = False
                self._using_fallback = True

    def _fetch_models_return_list(self, api_url: Optional[str] = None) -> List[str]:
        if api_url is None:
            api_url = self.get_default_api_url()
        try:
            client = OllamaClient(api_url)
            raw = client.list_models()
            if isinstance(raw, list):
                names = []
                for m in raw:
                    if isinstance(m, dict) and 'name' in m:
                        names.append(m['name'])
                    elif isinstance(m, str):
                        names.append(m)
                return names
            return []
        except Exception:
            return []

    async def _fetch_models_async(self, api_url: Optional[str] = None) -> List[str]:
        import asyncio
        return await asyncio.to_thread(self._fetch_models_return_list, api_url)

    def is_using_fallback(self) -> bool:
        """Return True if the current cache is using hardcoded fallback models"""
        with self._lock:
            return self._using_fallback

    def get_real_models(self, timeout: float = 5.0) -> List[str]:
        """Get cached models only if they are real (non-fallback). Otherwise return an empty list."""
        models = self.get_models(timeout)
        with self._lock:
            if self._using_fallback:
                return []
        return models
# Global instance
_model_cache = ModelCache()

def get_model_cache() -> ModelCache:
    """Get the global model cache instance"""
    return _model_cache

def get_cached_models(timeout: float = 5.0) -> List[str]:
    """Convenience function to get cached models"""
    return _model_cache.get_models(timeout)

def get_real_cached_models(timeout: float = 5.0) -> List[str]:
    """Convenience function to get cached models without falling back to hardcoded defaults"""
    return _model_cache.get_real_models(timeout)

def load_models_at_startup(api_url: Optional[str] = None) -> None:
    """Load models at application startup"""
    print("Starting model preload at application startup...")
    _model_cache.load_models_async(api_url)
