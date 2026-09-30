#!/usr/bin/env python3
"""
OverWatch API Client

Client for communicating with the OverWatch Ollama API server.
"""

import requests
import logging
import json
from typing import Dict, List, Any, Optional, Union
from dataclasses import dataclass
from urllib.parse import urljoin

logger = logging.getLogger(__name__)


@dataclass
class ChatMessage:
    """Chat message structure"""
    role: str  # 'user', 'assistant', 'system'
    content: str


class OverWatchAPIClient:
    """Client for OverWatch API communication"""
    
    def __init__(self, base_url: str, timeout: int = 30):
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.session = requests.Session()
        
        # Set default headers
        self.session.headers.update({
            'Content-Type': 'application/json',
            'User-Agent': 'OverWatch-Client/1.0.0'
        })
        
        logger.info(f"Initialized OverWatch API client for {self.base_url}")
    
    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Make HTTP request to API"""
        url = urljoin(self.base_url + '/', endpoint.lstrip('/'))
        
        try:
            if method.upper() == 'GET':
                response = self.session.get(url, params=params, timeout=self.timeout)
            elif method.upper() == 'POST':
                response = self.session.post(url, json=data, params=params, timeout=self.timeout)
            elif method.upper() == 'DELETE':
                response = self.session.delete(url, params=params, timeout=self.timeout)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
            
            # Check for HTTP errors
            response.raise_for_status()
            
            # Try to parse JSON response
            try:
                return response.json()
            except json.JSONDecodeError:
                return {'text': response.text, 'status_code': response.status_code}
                
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error to {url}: {e}")
            raise ConnectionError(f"Failed to connect to API at {url}")
        except requests.exceptions.Timeout as e:
            logger.error(f"Timeout error for {url}: {e}")
            raise TimeoutError(f"Request to {url} timed out")
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error for {url}: {e}")
            try:
                error_data = response.json()
                raise RuntimeError(f"API error: {error_data.get('detail', str(e))}")
            except json.JSONDecodeError:
                raise RuntimeError(f"HTTP {response.status_code}: {response.text}")
        except Exception as e:
            logger.error(f"Unexpected error for {url}: {e}")
            raise RuntimeError(f"Unexpected error: {str(e)}")
    
    def health_check(self) -> Dict[str, Any]:
        """Check API health status"""
        return self._make_request('GET', '/health')
    
    def list_models(self) -> List[str]:
        """Get list of available models"""
        response = self._make_request('GET', '/models')
        return response.get('models', [])
    
    def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 512,
        system: Optional[str] = None
    ) -> Dict[str, Any]:
        """Generate text using the API"""
        data = {
            'prompt': prompt,
            'temperature': temperature,
            'max_tokens': max_tokens
        }
        
        if model:
            data['model'] = model
        if system:
            data['system'] = system
        
        return self._make_request('POST', '/generate', data=data)
    
    def chat_completion(
        self,
        messages: List[Union[ChatMessage, Dict[str, str]]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 512
    ) -> Dict[str, Any]:
        """Perform chat completion"""
        # Convert ChatMessage objects to dicts if needed
        formatted_messages = []
        for msg in messages:
            if isinstance(msg, ChatMessage):
                formatted_messages.append({'role': msg.role, 'content': msg.content})
            elif isinstance(msg, dict):
                formatted_messages.append(msg)
            else:
                raise ValueError(f"Invalid message type: {type(msg)}")
        
        data = {
            'messages': formatted_messages,
            'temperature': temperature,
            'max_tokens': max_tokens
        }
        
        if model:
            data['model'] = model
        
        return self._make_request('POST', '/chat', data=data)
    
    def load_model(self, model_name: str) -> Dict[str, Any]:
        """Load a model (if API supports dynamic loading)"""
        data = {'model_name': model_name}
        return self._make_request('POST', '/models/load', data=data)
    
    def unload_model(self, model_name: str) -> Dict[str, Any]:
        """Unload a model (if API supports dynamic loading)"""
        data = {'model_name': model_name}
        return self._make_request('POST', '/models/unload', data=data)
    
    def get_model_info(self, model_name: str) -> Dict[str, Any]:
        """Get information about a specific model"""
        return self._make_request('GET', f'/models/{model_name}')
    
    def get_memory_usage(self) -> Dict[str, Any]:
        """Get system memory usage information"""
        return self._make_request('GET', '/memory')
    
    def test_connection(self) -> bool:
        """Test if connection to API is working"""
        try:
            response = self.health_check()
            return response.get('status') == 'healthy'
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return False
    
    def get_api_info(self) -> Dict[str, Any]:
        """Get API information and capabilities"""
        try:
            return self._make_request('GET', '/info')
        except Exception:
            # Fallback if /info endpoint doesn't exist
            return {
                'name': 'OverWatch API',
                'version': 'Unknown',
                'capabilities': ['chat', 'generate']
            }
    
    def set_timeout(self, timeout: int):
        """Set request timeout"""
        self.timeout = timeout
    
    def close(self):
        """Close the session"""
        if self.session:
            self.session.close()
            logger.info("API client session closed")


class AsyncOverWatchAPIClient:
    """Async version of OverWatch API client (for future use)"""
    
    def __init__(self, base_url: str, timeout: int = 30):
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        
        # Note: This would require aiohttp for async HTTP requests
        # For now, this is a placeholder for future async implementation
        logger.info(f"Initialized async OverWatch API client for {self.base_url}")
    
    async def health_check(self) -> Dict[str, Any]:
        """Async health check - placeholder for future implementation"""
        # This would use aiohttp for actual async implementation
        raise NotImplementedError("Async client not yet implemented")


def create_api_client(settings) -> OverWatchAPIClient:
    """Create API client from settings"""
    from ..config.settings import get_api_url
    
    api_url = get_api_url(settings)
    timeout = getattr(settings.api, 'timeout', 30)
    
    return OverWatchAPIClient(api_url, timeout=timeout)