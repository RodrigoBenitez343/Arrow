#!/usr/bin/env python3
"""
Script to download a Llama model from Hugging Face Hub for testing the OverWatch API.
This script downloads the Llama-3.1-8B-Instruct model which is optimized for instruction following.
"""

import os
from huggingface_hub import snapshot_download
from pathlib import Path

def download_llama_model():
    """
    Download Llama-3.1-8B-Instruct model from Hugging Face Hub.
    This model is optimized for instruction following and provides excellent performance.
    """
    
    # Create models directory if it doesn't exist
    models_dir = Path("./models")
    models_dir.mkdir(exist_ok=True)
    
    print("Starting download of Llama-3.1-8B-Instruct model...")
    print("This model is optimized for instruction following.")
    print("Model info: https://huggingface.co/meta-llama/Llama-3.1-8B-Instruct")
    print("Note: This model requires accepting the license terms on Hugging Face.")
    
    try:
        # Download the model to local models directory
        model_path = snapshot_download(
            repo_id="meta-llama/Llama-3.1-8B-Instruct",
            cache_dir="./models",
            # Only download essential files to save space
            allow_patterns=["*.json", "*.safetensors", "*.txt", "*.md", "*.model", "tokenizer.model"]
        )
        
        print(f"\nModel downloaded successfully!")
        print(f"Model path: {model_path}")
        print(f"\nYou can now test the model with the OverWatch API.")
        
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