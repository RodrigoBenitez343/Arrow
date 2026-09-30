import os
import json
import base64
from typing import Dict, List, Any, Optional
from datetime import datetime
import glob
from pathlib import Path

# Import OCR functionality
try:
    from player.ocr_engine import extract_text_elements_from_image_path, ocr_available
    OCR_AVAILABLE = ocr_available()
except ImportError:
    OCR_AVAILABLE = False
    extract_text_elements_from_image_path = None

class ContextCollector:
    """Collects context data for LLM integration including screenshots, automation data, and workflow state"""
    
    def __init__(self, workspace_path: str = None):
        self.workspace_path = workspace_path or os.getcwd()
        self.context_data = []
        self.max_context_items = 50  # Limit context size
        
    def collect_screenshot_context(self, screenshot_path: str, action_description: str = None, extract_ocr: bool = True) -> Dict[str, Any]:
        """Collect screenshot context with optional action description and OCR text extraction"""
        if not os.path.exists(screenshot_path):
            return None
            
        try:
            with open(screenshot_path, 'rb') as img_file:
                img_data = base64.b64encode(img_file.read()).decode('utf-8')
            
            # Extract OCR text if enabled and available
            ocr_text = None
            ocr_elements = []
            if extract_ocr and OCR_AVAILABLE:
                try:
                    ocr_text, ocr_elements = self._extract_ocr_text(screenshot_path)
                except Exception as ocr_error:
                    print(f"OCR extraction failed for {screenshot_path}: {ocr_error}")
                
            context_item = {
                "type": "screenshot",
                "timestamp": datetime.now().isoformat(),
                "image_data": img_data,
                "file_path": screenshot_path,
                "action_description": action_description or "Screen capture",
                "ocr_text": ocr_text,
                "ocr_elements": ocr_elements,
                "metadata": {
                    "file_size": os.path.getsize(screenshot_path),
                    "file_name": os.path.basename(screenshot_path),
                    "has_ocr": ocr_text is not None,
                    "ocr_element_count": len(ocr_elements) if ocr_elements else 0
                }
            }
            return context_item
        except Exception as e:
            print(f"Error collecting screenshot context: {e}")
            return None
    
    def collect_automation_json_context(self, json_path: str) -> Dict[str, Any]:
        """Collect automation JSON file context"""
        if not os.path.exists(json_path):
            return None
            
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                json_data = json.load(f)
                
            context_item = {
                "type": "automation_json",
                "timestamp": datetime.now().isoformat(),
                "file_path": json_path,
                "content": json_data,
                "metadata": {
                    "file_name": os.path.basename(json_path),
                    "file_size": os.path.getsize(json_path),
                    "automation_type": json_data.get("type", "unknown"),
                    "steps_count": len(json_data.get("steps", [])) if isinstance(json_data.get("steps"), list) else 0
                }
            }
            return context_item
        except Exception as e:
            print(f"Error collecting automation JSON context: {e}")
            return None
    
    def collect_workflow_state_context(self, workflow_data: Dict[str, Any]) -> Dict[str, Any]:
        """Collect current workflow state context"""
        try:
            context_item = {
                "type": "workflow_state",
                "timestamp": datetime.now().isoformat(),
                "workflow_data": workflow_data,
                "metadata": {
                    "current_step": workflow_data.get("current_step", 0),
                    "total_steps": workflow_data.get("total_steps", 0),
                    "workflow_name": workflow_data.get("name", "unknown"),
                    "status": workflow_data.get("status", "unknown")
                }
            }
            return context_item
        except Exception as e:
            print(f"Error collecting workflow state context: {e}")
            return None
    
    def collect_recent_screenshots(self, screenshots_dir: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Collect recent screenshots from a directory"""
        if not os.path.exists(screenshots_dir):
            return []
            
        try:
            # Find recent screenshot files
            screenshot_patterns = ['*.png', '*.jpg', '*.jpeg', '*.bmp']
            screenshot_files = []
            
            for pattern in screenshot_patterns:
                screenshot_files.extend(glob.glob(os.path.join(screenshots_dir, pattern)))
            
            # Sort by modification time (most recent first)
            screenshot_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
            
            contexts = []
            for screenshot_path in screenshot_files[:limit]:
                context = self.collect_screenshot_context(screenshot_path)
                if context:
                    contexts.append(context)
                    
            return contexts
        except Exception as e:
            print(f"Error collecting recent screenshots: {e}")
            return []
    
    def _extract_ocr_text(self, image_path: str, min_confidence: float = 0.6) -> tuple[str, List[Dict[str, Any]]]:
        """Extract text from image using OCR
        
        Args:
            image_path: Path to the image file
            min_confidence: Minimum confidence threshold for text extraction
            
        Returns:
            tuple: (combined_text, list_of_text_elements)
        """
        if not OCR_AVAILABLE:
            return None, []
            
        try:
            if not extract_text_elements_from_image_path:
                return None, []

            text_elements = extract_text_elements_from_image_path(
                image_path=image_path, min_confidence=min_confidence, language="eng"
            )
            combined_text = " ".join([e["text"] for e in text_elements if e.get("text")]) if text_elements else None
            
            print(f"OCR extracted {len(text_elements)} text elements from {os.path.basename(image_path)}")
            return combined_text, text_elements
            
        except Exception as e:
            print(f"OCR text extraction failed: {e}")
            return None, []
    
    def collect_recent_automations(self, automations_dir: str, limit: int = 3) -> List[Dict[str, Any]]:
        """Collect recent automation JSON files from a directory"""
        if not os.path.exists(automations_dir):
            return []
            
        try:
            json_files = glob.glob(os.path.join(automations_dir, '*.json'))
            json_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
            
            contexts = []
            for json_path in json_files[:limit]:
                context = self.collect_automation_json_context(json_path)
                if context:
                    contexts.append(context)
                    
            return contexts
        except Exception as e:
            print(f"Error collecting recent automations: {e}")
            return []
    
    def add_context_item(self, context_item: Dict[str, Any]) -> None:
        """Add a context item to the collection"""
        if context_item:
            self.context_data.append(context_item)
            
            # Maintain size limit
            if len(self.context_data) > self.max_context_items:
                self.context_data = self.context_data[-self.max_context_items:]
    
    def get_context_for_llm(self, include_images: bool = True, max_items: int = 10) -> List[Dict[str, Any]]:
        """Get formatted context data for LLM consumption"""
        context_for_llm = []
        
        # Get most recent items
        recent_items = self.context_data[-max_items:] if self.context_data else []
        
        for item in recent_items:
            llm_item = {
                "type": item["type"],
                "timestamp": item["timestamp"],
                "metadata": item.get("metadata", {})
            }
            
            if item["type"] == "screenshot":
                if include_images:
                    llm_item["image_data"] = item["image_data"]
                llm_item["description"] = item.get("action_description", "Screen capture")
                
            elif item["type"] == "automation_json":
                llm_item["automation_data"] = item["content"]
                llm_item["file_name"] = item["metadata"].get("file_name", "unknown")
                
            elif item["type"] == "workflow_state":
                llm_item["workflow_data"] = item["workflow_data"]
                
            context_for_llm.append(llm_item)
            
        return context_for_llm
    
    def clear_context(self) -> None:
        """Clear all collected context data"""
        self.context_data.clear()
    
    def get_context_summary(self) -> Dict[str, Any]:
        """Get a summary of collected context data"""
        summary = {
            "total_items": len(self.context_data),
            "types": {},
            "oldest_timestamp": None,
            "newest_timestamp": None
        }
        
        if self.context_data:
            # Count by type
            for item in self.context_data:
                item_type = item["type"]
                summary["types"][item_type] = summary["types"].get(item_type, 0) + 1
            
            # Get timestamp range
            timestamps = [item["timestamp"] for item in self.context_data]
            summary["oldest_timestamp"] = min(timestamps)
            summary["newest_timestamp"] = max(timestamps)
            
        return summary
