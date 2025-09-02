#!/usr/bin/env python3
"""
Script to download a Llama model from Hugging Face Hub for testing ComoRAG API.
This script downloads the Llama-3.2-1B model which is suitable for CPU testing.
"""

import os
from huggingface_hub import snapshot_download
from pathlib import Path

def download_llama_model():
    """
    Download Llama-3.2-1B model from Hugging Face Hub.
    This is a smaller model suitable for CPU testing.
    """
    
    # Create models directory if it doesn't exist
    models_dir = Path("./models")
    models_dir.mkdir(exist_ok=True)
    
    print("Starting download of TinyLlama-1.1B-Chat model...")
    print("This is an open-source model that doesn't require gated access.")
    print("Model info: https://huggingface.co/TinyLlama/TinyLlama-1.1B-Chat-v1.0")
    
    try:
        # Download the model to local models directory
        model_path = snapshot_download(
            repo_id="TinyLlama/TinyLlama-1.1B-Chat-v1.0",
            cache_dir="./models",
            # Only download essential files to save space
            allow_patterns=["*.json", "*.safetensors", "*.txt", "*.md", "*.model"]
        )
        
        print(f"\nModel downloaded successfully!")
        print(f"Model path: {model_path}")
        print(f"\nYou can now test the model with ComoRAG API.")
        
        # Show directory structure
        print("\nDownloaded files:")
        for file in Path(model_path).iterdir():
            if file.is_file():
                size_mb = file.stat().st_size / (1024 * 1024)
                print(f"  {file.name} ({size_mb:.1f} MB)")
                
        return model_path
        
    except Exception as e:
        print(f"Error downloading model: {e}")
        print("\nPossible solutions:")
        print("1. Make sure you have accepted the license terms on Hugging Face")
        print("2. Login to Hugging Face: huggingface-cli login")
        print("3. Check your internet connection")
        return None

if __name__ == "__main__":
    download_llama_model()