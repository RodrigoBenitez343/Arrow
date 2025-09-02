#!/usr/bin/env python3
"""
Simple ComoRAG Chat Interface

This script provides a simple chat interface that uses ComoRAG's memory and reasoning
capabilities without requiring a loaded LLM model. It demonstrates how to interact
with the ComoRAG API for memory management and context-aware responses.
"""

import requests
import json
import sys
from typing import Dict, Any, Optional

class ComoRAGChat:
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.session = requests.Session()
        
    def check_health(self) -> bool:
        """Check if the ComoRAG API is available"""
        try:
            response = self.session.get(f"{self.base_url}/health")
            return response.status_code == 200
        except requests.RequestException:
            return False
    
    def get_memory_status(self) -> Dict[str, Any]:
        """Get current memory status"""
        try:
            response = self.session.get(f"{self.base_url}/comorag/memory")
            if response.status_code == 200:
                return response.json()
            else:
                return {"error": f"HTTP {response.status_code}: {response.text}"}
        except requests.RequestException as e:
            return {"error": str(e)}
    
    def clear_memory(self) -> Dict[str, Any]:
        """Clear ComoRAG memory"""
        try:
            response = self.session.delete(f"{self.base_url}/comorag/memory")
            if response.status_code == 200:
                return response.json()
            else:
                return {"error": f"HTTP {response.status_code}: {response.text}"}
        except requests.RequestException as e:
            return {"error": str(e)}
    
    def reason_with_context(self, query: str, context: str = "", max_cycles: int = 3) -> Dict[str, Any]:
        """Use ComoRAG reasoning with context (requires loaded model)"""
        try:
            payload = {
                "query": query,
                "context": context,
                "max_cycles": max_cycles,
                "memory_size": 1000
            }
            
            response = self.session.post(
                f"{self.base_url}/comorag/reason",
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                return {"error": f"HTTP {response.status_code}: {response.text}"}
        except requests.RequestException as e:
            return {"error": str(e)}
    
    def list_models(self) -> Dict[str, Any]:
        """List available models"""
        try:
            response = self.session.get(f"{self.base_url}/models")
            if response.status_code == 200:
                return response.json()
            else:
                return {"error": f"HTTP {response.status_code}: {response.text}"}
        except requests.RequestException as e:
            return {"error": str(e)}

def print_banner():
    """Print welcome banner"""
    print("\n" + "="*60)
    print("    ComoRAG Chat Interface")
    print("    Type 'help' for commands, 'quit' to exit")
    print("="*60 + "\n")

def print_help():
    """Print available commands"""
    print("\nAvailable commands:")
    print("  help          - Show this help message")
    print("  status        - Show API and memory status")
    print("  memory        - Show current memory status")
    print("  clear         - Clear ComoRAG memory")
    print("  models        - List available models")
    print("  reason <text> - Use ComoRAG reasoning (requires loaded model)")
    print("  quit/exit     - Exit the chat")
    print("  <message>     - Send message for reasoning (requires loaded model)")
    print()

def main():
    """Main chat loop"""
    chat = ComoRAGChat()
    
    print_banner()
    
    # Check if API is available
    if not chat.check_health():
        print("❌ ComoRAG API is not available at http://localhost:8000")
        print("   Please make sure the OverWatch server is running.")
        print("   Run: python main.py")
        sys.exit(1)
    
    print("✅ ComoRAG API is available!")
    
    # Show initial status
    models = chat.list_models()
    if "models" in models and models["models"]:
        print(f"📦 Loaded models: {', '.join(models['models'])}")
    else:
        print("⚠️  No models currently loaded. Use the /models/load endpoint to load a model.")
        print("   Example: curl -X POST http://localhost:8000/models/load -H 'Content-Type: application/json' -d '{\"model_name\": \"TinyLlama/TinyLlama-1.1B-Chat-v1.0\"}'")
    
    memory_status = chat.get_memory_status()
    if "total_items" in memory_status:
        print(f"🧠 Memory items: {memory_status['total_items']}")
    
    print("\nType 'help' for available commands.\n")
    
    # Main chat loop
    while True:
        try:
            user_input = input("You: ").strip()
            
            if not user_input:
                continue
            
            # Handle commands
            if user_input.lower() in ['quit', 'exit']:
                print("Goodbye! 👋")
                break
            
            elif user_input.lower() == 'help':
                print_help()
            
            elif user_input.lower() == 'status':
                health = chat.check_health()
                models = chat.list_models()
                memory = chat.get_memory_status()
                
                print(f"\n📊 Status Report:")
                print(f"   API Health: {'✅ Online' if health else '❌ Offline'}")
                
                if "models" in models:
                    model_list = models["models"]
                    if model_list:
                        print(f"   Loaded Models: {', '.join(model_list)}")
                    else:
                        print("   Loaded Models: None")
                else:
                    print(f"   Models Error: {models.get('error', 'Unknown')}")
                
                if "total_items" in memory:
                    print(f"   Memory Items: {memory['total_items']}")
                else:
                    print(f"   Memory Error: {memory.get('error', 'Unknown')}")
                print()
            
            elif user_input.lower() == 'memory':
                memory = chat.get_memory_status()
                print(f"\n🧠 Memory Status: {json.dumps(memory, indent=2)}\n")
            
            elif user_input.lower() == 'clear':
                result = chat.clear_memory()
                if "error" in result:
                    print(f"❌ Error clearing memory: {result['error']}")
                else:
                    print("✅ Memory cleared successfully!")
            
            elif user_input.lower() == 'models':
                models = chat.list_models()
                print(f"\n📦 Models: {json.dumps(models, indent=2)}\n")
            
            elif user_input.lower().startswith('reason '):
                query = user_input[7:].strip()
                if query:
                    print("🤔 Reasoning...")
                    result = chat.reason_with_context(query)
                    if "error" in result:
                        print(f"❌ Error: {result['error']}")
                    else:
                        print(f"🤖 ComoRAG: {json.dumps(result, indent=2)}")
                else:
                    print("❌ Please provide a query after 'reason'")
            
            else:
                # Treat as a reasoning query
                print("🤔 Reasoning...")
                result = chat.reason_with_context(user_input)
                if "error" in result:
                    print(f"❌ Error: {result['error']}")
                    if "No models loaded" in result["error"]:
                        print("   💡 Tip: Load a model first using the /models/load endpoint")
                else:
                    print(f"🤖 ComoRAG: {json.dumps(result, indent=2)}")
        
        except KeyboardInterrupt:
            print("\n\nGoodbye! 👋")
            break
        except Exception as e:
            print(f"❌ Unexpected error: {e}")

if __name__ == "__main__":
    main()