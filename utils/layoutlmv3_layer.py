import torch
from transformers import LayoutLMv3Processor, LayoutLMv3Model
from PIL import Image
import numpy as np

class LayoutLMv3Layer:
    def __init__(self):
        print("Loading LayoutLMv3 Layer...")
        self.processor = LayoutLMv3Processor.from_pretrained("microsoft/layoutlmv3-base", apply_ocr=False)
        self.model = LayoutLMv3Model.from_pretrained("microsoft/layoutlmv3-base")
        self.model.eval()
        print("LayoutLMv3 Layer Loaded.")

    def normalize_bbox(self, bbox, width, height):
        x1, y1, x2, y2 = bbox
        nx1 = max(0, min(1000, int(1000 * (x1 / width))))
        ny1 = max(0, min(1000, int(1000 * (y1 / height))))
        nx2 = max(0, min(1000, int(1000 * (x2 / width))))
        ny2 = max(0, min(1000, int(1000 * (y2 / height))))
        
        # Ensure x2 > x1 and y2 > y1 for LayoutLM
        if nx2 <= nx1:
            nx2 = min(1000, nx1 + 1)
        if ny2 <= ny1:
            ny2 = min(1000, ny1 + 1)
            
        return [nx1, ny1, nx2, ny2]

    def relate_elements_and_text(self, image_np, ocr_texts, ui_elements):
        """
        Uses LayoutLMv3 to generate embeddings for OCR texts and UI elements,
        and finds the best matching text label for each UI element based on layout context.
        """
        if not ocr_texts or not ui_elements:
            return ui_elements

        height, width = image_np.shape[:2]
        
        # Convert BGR (OpenCV) to RGB for PIL
        if len(image_np.shape) == 3 and image_np.shape[2] == 3:
            import cv2
            image_rgb = cv2.cvtColor(image_np, cv2.COLOR_BGR2RGB)
        else:
            image_rgb = image_np
            
        image_pil = Image.fromarray(image_rgb)

        words = []
        boxes = []
        element_indices = [] # Keep track of which words are actually UI elements

        # Add OCR texts
        for t in ocr_texts:
            words.append(t.get("text", ""))
            boxes.append(self.normalize_bbox(t.get("coords", [0,0,0,0]), width, height))

        ocr_count = len(words)

        # Add UI elements as special tokens
        for i, el in enumerate(ui_elements):
            el_type = el.get("type", "input").upper()
            words.append(f"[{el_type}]")
            boxes.append(self.normalize_bbox(el.get("coords", [0,0,0,0]), width, height))
            element_indices.append(ocr_count + i)

        try:
            encoding = self.processor(
                image_pil, 
                words, 
                boxes=boxes, 
                return_tensors="pt", 
                truncation=True, 
                max_length=512
            )
            
            with torch.no_grad():
                outputs = self.model(**encoding)

            # Get token-level embeddings
            last_hidden_states = outputs.last_hidden_state[0] # (seq_len, hidden_size)

            # We need to map word indices to token indices
            word_ids = encoding.word_ids(batch_index=0)
            
            # Aggregate token embeddings into word embeddings
            word_embeddings = []
            for word_idx in range(len(words)):
                token_indices = [i for i, w in enumerate(word_ids) if w == word_idx]
                if token_indices:
                    # Average pooling for the word
                    word_emb = last_hidden_states[token_indices].mean(dim=0)
                else:
                    # Fallback if truncated
                    word_emb = torch.zeros(self.model.config.hidden_size)
                word_embeddings.append(word_emb)

            # Now find the best OCR text for each UI element
            for i, el_idx in enumerate(element_indices):
                el_emb = word_embeddings[el_idx]
                if el_emb.sum() == 0:
                    continue # Truncated
                
                best_score = -float('inf')
                best_text = ""
                
                # Compare with all OCR text embeddings
                for ocr_idx in range(ocr_count):
                    ocr_emb = word_embeddings[ocr_idx]
                    if ocr_emb.sum() == 0:
                        continue
                        
                    # Compute cosine similarity
                    sim = torch.nn.functional.cosine_similarity(el_emb, ocr_emb, dim=0).item()
                    
                    # Add spatial heuristic to LayoutLMv3's semantic+spatial similarity
                    el_box = boxes[el_idx]
                    ocr_box = boxes[ocr_idx]
                    # Encourage text that is above or to the left
                    is_above = (ocr_box[3] <= el_box[1] + 50) and abs((ocr_box[0]+ocr_box[2])/2 - (el_box[0]+el_box[2])/2) < 200
                    is_left = (ocr_box[2] <= el_box[0] + 50) and abs((ocr_box[1]+ocr_box[3])/2 - (el_box[1]+el_box[3])/2) < 100
                    
                    if is_above or is_left:
                        sim += 0.5 # Boost logical candidates
                        
                    if sim > best_score:
                        best_score = sim
                        best_text = words[ocr_idx]
                
                # Assign the best text as the related label
                if best_text:
                    ui_elements[i]["layoutlm_label"] = best_text

        except Exception as e:
            print(f"LayoutLMv3 error: {e}")

        return ui_elements

    def render_layout_visualization(self, image_np, ui_elements, ocr_texts):
        """
        Renders a visualization of the LayoutLMv3 matches onto the image using a unified mask style.
        Draws red bounding boxes around UI elements and displays the assigned label alongside its index.
        """
        import cv2
        
        # Start with a dark background to match the unified mask format
        h, w = image_np.shape[:2]
        vis_img = np.zeros((h, w, 3), dtype=np.uint8)
        vis_img[:] = (32, 32, 32)
        
        # Draw the original image patches for the UI elements
        for el in ui_elements:
            coords = el.get("coords")
            if not coords:
                continue
            x1, y1, x2, y2 = map(int, coords)
            x1 = max(0, min(w-1, x1))
            x2 = max(0, min(w, x2))
            y1 = max(0, min(h-1, y1))
            y2 = max(0, min(h, y2))
            try:
                patch = image_np[y1:y2, x1:x2]
                vis_img[y1:y2, x1:x2] = patch
            except Exception:
                pass
        
        # Overlay the unified bounding boxes and labels
        for el in ui_elements:
            coords = el.get("coords")
            label = el.get("layoutlm_label", "")
            el_type = el.get("type", "unknown")
            index = el.get("index", "?")
            
            if not coords:
                continue
                
            x1, y1, x2, y2 = map(int, coords)
            
            # Match the text color to the element type color from model_vision
            color = (0, 0, 255) # Default Red
            if el_type == "input": color = (0, 255, 255) # Yellow
            elif el_type == "button": color = (255, 0, 0) # Blue
            elif el_type == "text": color = (0, 255, 0) # Green
            
            cv2.rectangle(vis_img, (x1, y1), (x2, y2), color, 2)
            
            # Draw index and label text
            display_text = f"[{index}] {el_type}: {label}" if label else f"[{index}] {el_type}"
            
            # Add a slight dark background to the text for better readability
            (text_w, text_h), _ = cv2.getTextSize(display_text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            cv2.rectangle(vis_img, (x1, max(0, y1 - text_h - 10)), (x1 + text_w, max(0, y1)), (0, 0, 0), -1)
            cv2.putText(vis_img, display_text, (x1, max(0, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
            
        # Draw OCR texts in Green as requested
        for text_item in ocr_texts:
            coords = text_item.get("coords")
            if not coords:
                continue
            x1, y1, x2, y2 = map(int, coords)
            cv2.rectangle(vis_img, (x1, y1), (x2, y2), (0, 255, 0), 2)
            
        return vis_img
