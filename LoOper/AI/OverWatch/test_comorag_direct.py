#!/usr/bin/env python3
"""
Direct ComoRAG Test Script

This script tests ComoRAG functionality directly, demonstrating memory management 
and reasoning capabilities using a mock Ollama engine for independence.
"""

import sys
import os
import asyncio
import json
import logging
from unittest.mock import MagicMock

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from api.comorag_engine import ComoRAGEngine
from api.ollama_engine import OllamaEngine

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def test_comorag_direct():
    """Test ComoRAG engine directly"""
    print("🧠 Testing ComoRAG Engine Directly")
    print("=" * 50)
    
    # Mock settings
    class MockSettings:
        class OllamaSettings:
            base_url = "http://localhost:11434"
            default_models = []
        ollama = OllamaSettings()
        comorag_memory_size = 1000
        class ComoRAGSettings:
            enable_vision_analysis = False
        comorag = ComoRAGSettings()
        
    settings = MockSettings()
    
    # Mock OllamaEngine
    # We mock it because we want to test ComoRAG logic, not Ollama connectivity here.
    # For real integration tests, we'd use the real engine.
    ollama_engine = MagicMock(spec=OllamaEngine)
    ollama_engine.is_ready.return_value = True
    # Mock generate response
    ollama_engine.generate = AsyncMock(return_value={"text": "Mocked response", "finish_reason": "stop"})
    ollama_engine.get_loaded_models.return_value = ["mock-model"]
    
    # Initialize ComoRAG engine
    print("📦 Initializing ComoRAG engine...")
    comorag = ComoRAGEngine(settings=settings, ollama_engine=ollama_engine)
    await comorag.initialize()
    print("✅ ComoRAG engine initialized successfully!")
    
    # Test memory operations
    print("\n🧠 Testing Memory Operations:")
    
    # Add some test memories
    test_memories = [
        "The user likes to work with Python programming.",
        "The user is interested in AI and machine learning.",
        "The user prefers clear documentation and examples.",
        "The user is working on a project called OverWatch."
    ]
    
    print("\n📝 Adding test memories...")
    for i, memory in enumerate(test_memories, 1):
        await comorag.add_memory(memory, source="test")
        print(f"   {i}. Added: {memory}")
    
    # Get memory state
    state = await comorag.get_memory_state()
    print(f"\n📊 Memory Items: {state['total_items']}")
    
    # Test memory search (retrieval)
    print("\n🔍 Testing Evidence Retrieval:")
    search_query = "What is the user working on?"
    print(f"Query: '{search_query}'")
    
    # We access the internal method to test retrieval directly
    evidence = await comorag._retrieve_evidence(search_query)
    
    if evidence:
        print(f"✅ Found {len(evidence)} evidence items:")
        for i, item in enumerate(evidence, 1):
            print(f"   {i}. {item.content} (Score: {item.relevance_score:.3f})")
    else:
        print("❌ No evidence found (Check if sentence-transformers is installed).")
    
    # Test reasoning flow (mocked)
    print("\n🤔 Testing Reasoning Flow (Mocked LLM):")
    result = await comorag.reason(
        query="What languages does the user use?",
        model="mock-model",
        max_cycles=1
    )
    
    print(f"✅ Reasoning completed. Response: {result['response']}")
    print(f"   Cycles: {result['reasoning_cycles']}")
    print(f"   Confidence: {result['confidence']}")
    
    print("\n✅ ComoRAG Direct Test Completed Successfully!")

# Helper for async mocks
from unittest.mock import AsyncMock

if __name__ == "__main__":
    try:
        asyncio.run(test_comorag_direct())
    except KeyboardInterrupt:
        print("\n\n👋 Test interrupted by user.")
    except Exception as e:
        print(f"\n❌ Error during test: {e}")
        import traceback
        traceback.print_exc()
