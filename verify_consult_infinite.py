
import sys
import os
import unittest
from unittest.mock import MagicMock, patch
import requests

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), 'LoOper')))

from AI.consult import OllamaClient

class TestOllamaClientInfiniteTimeout(unittest.TestCase):
    def test_timeout_none_handled(self):
        # Initialize client with timeout=None
        client = OllamaClient(base_url="http://localhost:8000", timeout=None)
        self.assertIsNone(client.timeout)
        print("Verified: timeout=None accepted")

    def test_timeout_zero_handled(self):
        # Initialize client with timeout=0
        client = OllamaClient(base_url="http://localhost:8000", timeout=0)
        self.assertIsNone(client.timeout)
        print("Verified: timeout=0 converted to None")

    def test_timeout_negative_handled(self):
        # Initialize client with timeout=-1
        client = OllamaClient(base_url="http://localhost:8000", timeout=-1)
        self.assertIsNone(client.timeout)
        print("Verified: timeout=-1 converted to None")

    def test_post_json_infinite(self):
        client = OllamaClient(base_url="http://localhost:8000", timeout=0)
        
        with patch.object(client.session, 'post') as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"status": "ok"}
            mock_post.return_value = mock_response
            
            client._post_json("/test", {"data": 1})
            
            # Verify passed timeout is None
            mock_post.assert_called_with(
                "http://localhost:8000/test",
                json={"data": 1},
                timeout=None
            )
            print("Verified: _post_json uses timeout=None")

if __name__ == '__main__':
    unittest.main()
