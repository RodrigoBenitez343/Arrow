#!/usr/bin/env python3
"""
Direct ComoRAG Test Script

This script tests ComoRAG functionality directly without requiring vLLM,
demonstrating memory management and reasoning capabilities.
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from api.comorag_engine import ComoRAGEngine
from config.settings import load_config
import asyncio
import json

async def test_comorag_direct():
    """Test ComoRAG engine directly"""
    print("🧠 Testing ComoRAG Engine Directly")
    print("=" * 50)
    
    # Load configuration
    config = load_config()
    
    # Initialize ComoRAG engine
    print("📦 Initializing ComoRAG engine...")
    comorag = ComoRAGEngine()
    await comorag.initialize()
    print("✅ ComoRAG engine initialized successfully!")
    
    # Test memory operations
    print("\n🧠 Testing Memory Operations:")
    
    # Get initial memory status
    memory_status = await comorag.get_memory_status()
    print(f"📊 Initial memory status: {json.dumps(memory_status, indent=2)}")
    
    # Add some test memories
    test_memories = [
        "The user likes to work with Python programming.",
        "The user is interested in AI and machine learning.",
        "The user prefers clear documentation and examples.",
        "The user is working on a project called OverWatch."
    ]
    
    print("\n📝 Adding test memories...")
    for i, memory in enumerate(test_memories, 1):
        await comorag.add_memory(f"test_memory_{i}", memory)
        print(f"   {i}. Added: {memory}")
    
    # Check memory status after adding
    memory_status = await comorag.get_memory_status()
    print(f"\n📊 Memory status after adding: {json.dumps(memory_status, indent=2)}")
    
    # Test memory search
    print("\n🔍 Testing Memory Search:")
    search_queries = [
        "Python programming",
        "AI projects",
        "documentation",
        "OverWatch project"
    ]
    
    for query in search_queries:
        print(f"\n🔎 Searching for: '{query}'")
        results = await comorag.search_memory(query, limit=3)
        if results:
            for j, result in enumerate(results, 1):
                print(f"   {j}. Score: {result.get('score', 'N/A'):.3f} - {result.get('content', 'N/A')}")
        else:
            print("   No results found.")
    
    # Test reasoning without LLM (will show structure)
    print("\n🤔 Testing Reasoning Structure (without LLM):")
    test_query = "What programming languages does the user work with?"
    print(f"Query: {test_query}")
    
    # Search for relevant memories
    relevant_memories = await comorag.search_memory(test_query, limit=5)
    print(f"\n📚 Found {len(relevant_memories)} relevant memories:")
    for i, memory in enumerate(relevant_memories, 1):
        print(f"   {i}. {memory.get('content', 'N/A')} (Score: {memory.get('score', 'N/A'):.3f})")
    
    # Test memory clearing
    print("\n🧹 Testing Memory Clearing:")
    await comorag.clear_memory()
    memory_status = await comorag.get_memory_status()
    print(f"📊 Memory status after clearing: {json.dumps(memory_status, indent=2)}")
    
    print("\n✅ ComoRAG Direct Test Completed Successfully!")
    print("\n💡 Note: This test demonstrates ComoRAG's memory and search capabilities.")
    print("   For full reasoning with LLM responses, a language model needs to be loaded.")

def main():
    """Main function"""
    try:
        asyncio.run(test_comorag_direct())
    except KeyboardInterrupt:
        print("\n\n👋 Test interrupted by user.")
    except Exception as e:
        print(f"\n❌ Error during test: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()