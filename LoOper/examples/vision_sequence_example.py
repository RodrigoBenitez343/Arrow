#!/usr/bin/env python3
"""
Vision Sequence Example
Demonstrates LoOper's vision capabilities with screen capture and context analysis
"""

import os
import sys
import time
import subprocess
import webbrowser
from pathlib import Path

# Add the parent directory to the path to import consult
sys.path.append(str(Path(__file__).parent.parent / "AI"))
from consult import OllamaClient

def open_browser_and_document():
    """Open a browser with a text document for vision analysis"""
    print("Opening browser with online text document...")
    
    # Open a simple online text editor (Google Docs alternative)
    url = "https://demo.hedgedoc.org/new"
    webbrowser.open(url)
    
    print(f"Opened: {url}")
    print("Please wait for the page to load completely...")
    time.sleep(5)  # Wait for page to load
    
    return url

def analyze_screen_context(client, model="minicpm-v:latest"):
    """Analyze the current screen context using vision model"""
    print(f"\nAnalyzing screen context with {model}...")
    
    # Context analysis prompt
    context_prompt = """
    Analyze the current screen and describe:
    1. What type of application or website is visible?
    2. What is the main purpose of this interface?
    3. What kind of content would be appropriate to write here?
    4. Should the content be long-form or short-form?
    5. What is the intended audience or use case?
    
    Provide a detailed analysis of the context to help understand what should be written.
    """
    
    response = client.chat_with_screen(
        model=model,
        prompt=context_prompt,
        capture_screen=True,
        screen_quality=85,
        max_screen_size=1920
    )
    
    return response.get('response', 'No analysis received')

def generate_content_based_on_context(client, context_analysis, model="minicpm-v:latest"):
    """Generate appropriate content based on the context analysis"""
    print(f"\nGenerating content based on context with {model}...")
    
    content_prompt = f"""
    Based on this context analysis:
    {context_analysis}
    
    And looking at the current screen, please write appropriate content for this interface.
    Consider:
    - The type of document or platform
    - The expected length and format
    - The target audience
    - The purpose of the content
    
    Generate content that would be suitable for this context.
    """
    
    response = client.chat_with_screen(
        model=model,
        prompt=content_prompt,
        capture_screen=True,
        screen_quality=85,
        max_screen_size=1920
    )
    
    return response.get('response', 'No content generated')

def main():
    """Main function to run the vision sequence example"""
    print("=== LoOper Vision Sequence Example ===")
    print("This example demonstrates vision-enhanced context analysis")
    
    # Initialize client
    client = OllamaClient(debug=True)
    
    # Check if vision model is available
    model = "minicpm-v:latest"
    if not client.is_vision_model(model):
        print(f"Warning: {model} may not support vision. Proceeding anyway...")
    
    # Check server health
    health = client.health_check()
    if health.get("status") != "healthy":
        print(f"API server is not healthy: {health.get('message', 'Unknown error')}")
        print("Please make sure OverWatch is running.")
        sys.exit(1)
    
    print(f"Using model: {model}")
    print(f"Server status: {health.get('status')}")
    
    try:
        # Step 1: Open browser and document
        url = open_browser_and_document()
        
        # Step 2: Analyze screen context
        print("\n" + "="*50)
        print("STEP 1: Context Analysis")
        print("="*50)
        
        context_analysis = analyze_screen_context(client, model)
        print("\nContext Analysis Result:")
        print("-" * 30)
        print(context_analysis)
        
        # Step 3: Generate appropriate content
        print("\n" + "="*50)
        print("STEP 2: Content Generation")
        print("="*50)
        
        generated_content = generate_content_based_on_context(client, context_analysis, model)
        print("\nGenerated Content:")
        print("-" * 30)
        print(generated_content)
        
        # Step 4: Summary
        print("\n" + "="*50)
        print("SEQUENCE COMPLETE")
        print("="*50)
        print(f"Successfully analyzed context and generated appropriate content for: {url}")
        print("The system understood the interface and created contextually appropriate content.")
        
    except KeyboardInterrupt:
        print("\n\nSequence interrupted by user.")
    except Exception as e:
        print(f"\nError during sequence: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()