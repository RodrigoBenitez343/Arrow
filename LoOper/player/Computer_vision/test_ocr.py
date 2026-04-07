import sys
import os
import cv2
import numpy as np

# Ensure we can import from LoOper.player
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from player import ocr_engine

def create_test_image(text="Hello, OCR World!", filename="test_ocr_image.png"):
    # Create a white image
    img = np.ones((200, 500, 3), dtype=np.uint8) * 255
    
    # Add text to the image
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 1
    color = (0, 0, 0) # Black text
    thickness = 2
    
    # Get text size
    (text_width, text_height), baseline = cv2.getTextSize(text, font, font_scale, thickness)
    
    # Center the text
    x = (img.shape[1] - text_width) // 2
    y = (img.shape[0] + text_height) // 2
    
    cv2.putText(img, text, (x, y), font, font_scale, color, thickness)
    
    cv2.imwrite(filename, img)
    print(f"Created test image: {filename}")
    return filename

def test_ocr():
    img_path = r"d:\LoOperV2\LoOper\vision_output\image.png"
    
    print(f"\n--- Testing OCR Engine on {img_path} ---")
    
    # Test text extraction directly from path
    print("Testing extract_text_elements_from_image_path...")
    elements = ocr_engine.extract_text_elements_from_image_path(img_path)
    
    if elements:
        print(f"Found {len(elements)} elements:")
        for el in elements:
            print(f"  Text: '{el.get('text')}', Confidence: {el.get('confidence'):.2f}")
    else:
        print("No text elements found or OCR not available.")
        
    # Test simple string extraction
    print("\nTesting extract_text...")
    from PIL import Image
    try:
        pil_img = Image.open(img_path)
        full_text = ocr_engine.extract_text(pil_img)
        print(f"Extracted full text: '{full_text}'")
    except Exception as e:
        print(f"Error testing extract_text: {e}")

if __name__ == "__main__":
    test_ocr()
