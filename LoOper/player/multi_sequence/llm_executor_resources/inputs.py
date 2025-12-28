import os
import time
from typing import Dict, Tuple, Optional

from ...config import logger
from ...screenshot_cleanup import register_temporal_screenshot, cleanup_screenshot


def _ocr_input(ocr_confidence: int, stop_flag) -> str:
    """Capture a screenshot and extract text using OCR."""
    try:
        from ...computer_vision import TemplateMatching
        from ...config import CV2_AVAILABLE
        
        if CV2_AVAILABLE:
            import cv2
            import numpy as np

        if stop_flag and stop_flag():
            logger.info("Stopped before OCR screenshot capture")
            return ""

        # Use TemplateMatching.capture_screen() which prefers mss (faster/more reliable)
        logger.debug("Taking screenshot for OCR input...")
        screen_cv = TemplateMatching.capture_screen()
        
        if screen_cv is None:
            logger.error("Failed to capture screen for OCR")
            return ""

        screenshots_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "screenshots")
        os.makedirs(screenshots_dir, exist_ok=True)
        timestamp = int(time.time())
        screenshot_path = os.path.join(screenshots_dir, f"llm_ocr_{timestamp}.png")
        
        # Save the EXACT image we are about to process
        if CV2_AVAILABLE:
            try:
                cv2.imwrite(screenshot_path, screen_cv)
                register_temporal_screenshot(screenshot_path)
                logger.debug(f"Saved OCR screenshot to {screenshot_path}")
            except Exception as e:
                logger.warning(f"Failed to save debug screenshot: {e}")
        else:
            # Fallback if cv2 is not available (e.g. using PIL if possible, but screen_cv is numpy array)
            # If CV2 is not available, TemplateMatching might return something else or fail?
            # Assuming if CV2_AVAILABLE is False, we might not have numpy either.
            # But TemplateMatching depends on CV2/Numpy.
            pass

        # Pass the captured image directly to OCR engine to avoid re-capture
        ocr_results = TemplateMatching.ocr_extract_all_text(
            min_confidence=ocr_confidence, 
            screen_cv=screen_cv, 
            stop_flag=stop_flag
        )
        if CV2_AVAILABLE:
            cleanup_screenshot(screenshot_path)

        if not ocr_results:
            logger.warning("No OCR text extracted")
            return ""

        filtered = [item["text"] for item in ocr_results if item.get("confidence", 0) >= ocr_confidence]
        if filtered:
            text = "\n".join(filtered)
            logger.info(f"OCR extracted {len(filtered)} text blocks with confidence >= {ocr_confidence}")
            logger.info(f"OCR text preview: {text[:200]}...")
            return text
        else:
            logger.warning(f"No OCR text found with confidence >= {ocr_confidence}")
            return ""
    except Exception as e:
        logger.error(f"OCR processing failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return ""


def _previous_input(node: Dict, variables: Dict[str, object]) -> str:
    inputs_list = node.get("inputs", [])
    if not inputs_list:
        logger.warning("No workflow inputs found for node or missing connection")
        return ""
    collected = []
    for inp in inputs_list:
        try:
            connected_node_id = inp.get("from_node")
            if not connected_node_id:
                continue
            port = str(inp.get("input_port"))
            if port in ("tools", "context"):
                continue
            key = f"node_{connected_node_id}_output"
            if key in variables:
                val = str(variables.get(key, ""))
                if val:
                    collected.append(val)
        except Exception:
            continue
    if collected:
        joined = "\n\n".join(collected)
        logger.info(f"Retrieved previous node output: {joined[:200]}...")
        return joined
    val = str(variables.get("llm_output", ""))
    if val:
        logger.warning("Node-specific output not found; using fallback 'llm_output'")
        return val
    logger.warning("No previous output available from connected inputs")
    return ""


def _context_input(node: Dict, variables: Dict[str, object]) -> str:
    inputs_list = node.get("inputs", [])
    if not inputs_list:
        logger.warning("No workflow inputs found for node or missing connection")
        return ""
    collected = []
    for inp in inputs_list:
        try:
            connected_node_id = inp.get("from_node")
            if not connected_node_id:
                continue
            port = str(inp.get("input_port"))
            if port != "context":
                continue
            key = f"node_{connected_node_id}_context"
            if key in variables:
                val = str(variables.get(key, ""))
                if val:
                    collected.append(val)
        except Exception:
            continue
    if collected:
        joined = "\n\n".join(collected)
        logger.info(f"Retrieved context input: {joined[:200]}...")
        return joined
    logger.warning("No context available from connected inputs")
    return ""


def _clipboard_input() -> str:
    try:
        import pyperclip
        val = pyperclip.paste() or ""
        if val:
            logger.info(f"Retrieved clipboard content: {val[:200]}...")
        else:
            logger.warning("Clipboard is empty")
        return val
    except ImportError:
        logger.error("pyperclip not installed. Install with: pip install pyperclip")
        return ""
    except Exception as e:
        logger.error(f"Failed to read clipboard: {e}")
        return ""


def get_input_text(
    node: Dict,
    variables: Dict[str, object],
    input_source: str,
    ocr_confidence: int,
    stop_flag,
    use_direct_rag: bool,
) -> Tuple[Optional[str], bool]:
    """Return input text and whether to inject raw input into prompt.

    For 'previous' source with direct RAG enabled, we avoid injecting raw text.
    """
    input_source = (input_source or "none").lower()
    use_input_text_in_prompt = True
    input_text: Optional[str] = None

    if input_source == "ocr":
        input_text = _ocr_input(ocr_confidence, stop_flag)
    elif input_source in ("previous", "previous_node"):
        input_text = _previous_input(node, variables)
        if use_direct_rag:
            use_input_text_in_prompt = False
    elif input_source == "context":
        input_text = _context_input(node, variables)
        if use_direct_rag:
            use_input_text_in_prompt = False
    elif input_source == "clipboard":
        input_text = _clipboard_input()
    else:
        input_text = None

    return input_text, use_input_text_in_prompt
