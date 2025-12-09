import sys
import os
import asyncio
import logging
import json
from unittest.mock import MagicMock, AsyncMock

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("verify_comorag")

# Mock dependencies BEFORE importing the module
sys.modules["numpy"] = MagicMock()
sys.modules["sentence_transformers"] = MagicMock()
sys.modules["faiss"] = MagicMock()

# Mock paths and imports
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = current_dir
sys.path.append(project_root)

# Mock OverWatch engine
async def mock_comorag_test():
    print("--- Testing ComoRAG Engine Error Handling ---")
    
    # We need to import the engine class. 
    # Since we modified OverWatch/api/comorag_engine.py, we should try to import it.
    # We assume the file exists at OverWatch/api/comorag_engine.py relative to project root
    
    overwatch_dir = os.path.join(project_root, "OverWatch")
    if overwatch_dir not in sys.path:
        sys.path.append(overwatch_dir)
        
    try:
        from api.comorag_engine import ComoRAGEngine
        print("Successfully imported ComoRAGEngine")
    except ImportError as e:
        print(f"Failed to import ComoRAGEngine: {e}")
        return

    # Mock settings and ollama engine
    settings = MagicMock()
    settings.comorag.enable_vision_analysis = False
    
    ollama_engine = AsyncMock()
    ollama_engine.get_loaded_models.return_value = ["llama3.2"]
    ollama_engine.generate.return_value = {"text": '["Why is the sky blue?", "What is Rayleigh scattering?"]'}
    
    # We need to ensure the module uses our mocked numpy if it was imported inside the module
    # But since we mocked sys.modules["numpy"], it should be fine.
    
    engine = ComoRAGEngine(settings, ollama_engine)
    
    # Test 1: Run without initialization (should raise error but be caught by new try/except)
    print("\nTest 1: Run without initialization")
    try:
        result = await engine.reason("test query")
        print(f"Result: {json.dumps(result, indent=2)}")
        if result.get("reasoning_cycles") == 0 and "ComoRAG failed" in result.get("response", ""):
             print("SUCCESS: Caught initialization error correctly.")
        else:
             print("FAILURE: Did not catch initialization error as expected.")
    except Exception as e:
        print(f"FAILURE: Exception raised out of reason method: {e}")

    # Test 2: Initialize and run (simulated success)
    print("\nTest 2: Initialize and run")
    await engine.initialize()
    
    # Mock embedding model to avoid loading real one
    engine.embedding_model = MagicMock()
    # Mock numpy array return for embedding
    mock_array = MagicMock()
    mock_array.reshape.return_value = mock_array
    engine.embedding_model.encode.return_value = mock_array
    engine.embedding_model.get_sentence_embedding_dimension.return_value = 384
    
    # Mock internal methods to avoid complex logic that might rely on real numpy
    engine._generate_probing_queries = AsyncMock(return_value=["probe 1"])
    engine._retrieve_evidence = AsyncMock(return_value=[])
    engine._consolidate_evidence = AsyncMock(return_value="context")
    engine._generate_reasoning_output = AsyncMock(return_value="reasoning output")
    engine._calculate_confidence = AsyncMock(return_value=0.9) # High confidence to stop at cycle 0
    engine._generate_exploratory_query = AsyncMock(return_value="next query")
    
    # Also need to mock memory workspace stuff if it uses numpy
    if engine.memory_workspace:
        engine.memory_workspace.add_memory = MagicMock()
        engine.memory_workspace.search_similar = MagicMock(return_value=[])
        engine.memory_workspace.get_recent_memories = MagicMock(return_value=[])

    result = await engine.reason("test query", max_cycles=2)
    print(f"Result: {json.dumps(result, indent=2)}")
    
    if result.get("reasoning_cycles") == 1:
        print("SUCCESS: Ran 1 cycle and converged.")
    else:
        print(f"FAILURE: Expected 1 cycle, got {result.get('reasoning_cycles')}")

if __name__ == "__main__":
    asyncio.run(mock_comorag_test())
