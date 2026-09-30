import json
import os
import time
from typing import Dict, Tuple, Optional

from ...screenshot_cleanup import register_temporal_screenshot, cleanup_screenshot
import logging
logger = logging.getLogger(__name__)

# Ports that ONLY drive execution — they carry NO data.  A value is data only
# when it flows over a NAMED (non-base) port.  Base 'output', the empty
# default, and the branch ports are exec-only.
EXEC_ONLY_OUTPUT_PORTS = frozenset(
    {'output', '', 'true', 'false', 'error', 'route'}
)


def upstream_value(variables, from_id, output_type=None):
    """Value a source node published for the given OUTPUT port.

    Data flows ONLY over a NAMED (non-base) output port: ``data`` reads
    ``node_<id>_data``; ``context``/``ctx_out`` read ``node_<id>_context``;
    any other named port reads ``node_<id>_output_<port>``.  The base
    ``output`` port, the empty default and the branch ports
    (``true``/``false``/``error``/``route``) drive execution ONLY — they
    carry no data, so a consumer reached through one sees ``None``.

    Returns ``None`` when the source did not publish a value for that port.
    """
    ot = str(output_type or 'output').lower()
    if ot == 'data':
        return variables.get(f"node_{from_id}_data")
    if ot in ('context', 'ctx_out'):
        return variables.get(f"node_{from_id}_context")
    # Base / branch ports drive execution ONLY — never a data source.
    if ot in EXEC_ONLY_OUTPUT_PORTS:
        return None
    # Named output port — try the raw name first (port names are
    # case-sensitive), then the lowered form.
    for _port in (str(output_type), ot):
        named = variables.get(f"node_{from_id}_output_{_port}")
        if named is not None:
            return named
    return None


def _record_output_type(inp) -> Optional[str]:
    try:
        return str(inp.get('output_type') or inp.get('output_port') or 'output')
    except Exception:
        return 'output'


def _crop_region(screen_cv, region):
    """Clip a BGR screenshot to [x, y, w, h] device px; pass through if invalid."""
    if not region:
        return screen_cv
    try:
        x, y, w, h = (int(v) for v in region)
        if w <= 0 or h <= 0:
            return screen_cv
        return screen_cv[max(0, y):y + h, max(0, x):x + w]
    except Exception:
        return screen_cv


def _ocr_input(ocr_confidence: int, stop_flag, region=None) -> str:
    """Capture a screenshot and extract text using OCR.

    ``region`` (x, y, w, h) in device px clips the capture to a picked area so
    OCR reads only what the user selected; None = full screen.
    """
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

        # Clip to the picked OCR region (if any) BEFORE OCR / save.
        screen_cv = _crop_region(screen_cv, region)
        try:
            logger.debug(f"OCR capture: type={type(screen_cv)} shape={getattr(screen_cv, 'shape', None)}")
        except Exception:
            pass

        screenshots_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "screenshots")
        os.makedirs(screenshots_dir, exist_ok=True)
        timestamp = int(time.time())
        screenshot_path = os.path.join(screenshots_dir, f"llm_ocr_{timestamp}.png")
        
        saved_screenshot = False
        if CV2_AVAILABLE:
            try:
                cv2.imwrite(screenshot_path, screen_cv)
                register_temporal_screenshot(screenshot_path)
                saved_screenshot = True
                logger.debug(f"Saved OCR screenshot to {screenshot_path}")
            except Exception as e:
                logger.warning(f"Failed to save debug screenshot: {e}")
        else:
            try:
                from PIL import Image
                if hasattr(screen_cv, "convert"):
                    img = screen_cv.convert("RGB")
                else:
                    try:
                        import numpy as np
                    except Exception:
                        np = None
                    if np is None:
                        img = None
                    else:
                        arr = np.array(screen_cv)
                        if arr is None or len(arr.shape) != 3 or arr.shape[2] < 3:
                            img = None
                        else:
                            rgb = arr[:, :, ::-1]
                            img = Image.fromarray(rgb.astype("uint8"), "RGB")
                if img is not None:
                    img.save(screenshot_path)
                    register_temporal_screenshot(screenshot_path)
                    saved_screenshot = True
                    logger.debug(f"Saved OCR screenshot to {screenshot_path}")
            except Exception as e:
                logger.warning(f"Failed to save debug screenshot: {e}")

        # Pass the captured image directly to OCR engine to avoid re-capture
        ocr_results = TemplateMatching.ocr_extract_all_text(
            min_confidence=ocr_confidence, 
            screen_cv=screen_cv, 
            stop_flag=stop_flag
        )
        if saved_screenshot:
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
            if port in ("tools", "context", "prompt", "input"):
                continue
            val = upstream_value(variables, connected_node_id,
                                 _record_output_type(inp))
            if val is not None and str(val).strip():
                collected.append(str(val).strip())
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
            val = upstream_value(variables, connected_node_id,
                                 _record_output_type(inp))
            if val is not None and str(val).strip():
                collected.append(str(val).strip())
        except Exception:
            continue
    if collected:
        joined = "\n\n".join(collected)
        logger.info(f"Retrieved context input: {joined[:200]}...")
        return joined
    logger.warning("No context available from connected inputs")
    return ""


def _prompt_port_input(node: Dict, variables: Dict[str, object]) -> str:
    """Text wired to the LLM node's 'prompt' INPUT port ('' when none).

    The LLM executor REPLACES the node's prompt template with this text (the
    system message is untouched).  Values resolve by the SOURCE output port,
    like every other port reader here: a 'data' edge reads node_<id>_data, a
    'context' edge reads node_<id>_context, a named port reads
    node_<id>_output_<port>.
    """
    collected = []
    for inp in (node.get("inputs") or []):
        try:
            if not isinstance(inp, dict):
                continue
            if str(inp.get("input_port") or "") != "prompt":
                continue
            cid = inp.get("from_node")
            if not cid:
                continue
            val = upstream_value(variables, cid, _record_output_type(inp))
            if val is not None and str(val).strip():
                collected.append(str(val).strip())
        except Exception:
            continue
    return "\n\n".join(collected)


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


def _input_node_source(node: Dict, variables: Dict[str, object]) -> str:
    """Read text from connected Input nodes (passthrough / data-only mode).

    Works like ``_previous_input`` but is semantically scoped to Input-type
    upstream nodes so the LLM node configuration is explicit about where
    its data comes from.
    """
    inputs_list = node.get("inputs", [])
    if not inputs_list:
        logger.warning("[INPUT-SRC] No workflow inputs found for node")
        return ""

    collected = []
    for inp in inputs_list:
        try:
            connected_node_id = inp.get("from_node")
            if not connected_node_id:
                continue
            port = str(inp.get("input_port"))
            if port in ("tools", "context", "prompt", "input"):
                continue
            val = upstream_value(variables, connected_node_id,
                                 _record_output_type(inp))
            if val is not None and str(val).strip():
                collected.append(str(val).strip())
        except Exception:
            continue
    if collected:
        joined = "\n\n".join(collected)
        logger.info(f"[INPUT-SRC] Retrieved input-node output: {joined[:200]}...")
        return joined
    logger.warning("[INPUT-SRC] No input node output available from connected inputs")
    return ""


def _web_driver():
    """The chain's SHARED browser driver, or None when unavailable.

    Resolved straight through the shared workbench session (the same instance
    every web sequence / web conditional of the chain uses).
    """
    try:
        from ...web.session import SHARED_WEB_SCOPE, ensure_workbench_session
        return ensure_workbench_session(chain_key=SHARED_WEB_SCOPE)
    except Exception as exc:
        logger.error(f"Web mode: browser unavailable: {exc}")
        return None


def _save_web_screenshot(driver, element, prefix: str) -> Optional[str]:
    """Save a driver (or element) screenshot to a temporal path, or None."""
    try:
        screenshots_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "screenshots",
        )
        os.makedirs(screenshots_dir, exist_ok=True)
        path = os.path.join(screenshots_dir, f"{prefix}_{int(time.time())}.png")
        if element is not None:
            element.screenshot(path)
        else:
            driver.save_screenshot(path)
        if not os.path.exists(path):
            return None
        register_temporal_screenshot(path)
        return path
    except Exception as exc:
        logger.error(f"Web screenshot failed: {exc}")
        return None


def _web_ocr_input(web_ocr_locator, ocr_confidence: int, stop_flag) -> str:
    """OCR a headless browser screenshot (optionally a picked element's region).

    A picked region inside a shadow root / shadow-hosted frame cannot be
    returned by WebDriver, so its box is resolved in-page (deep search) and the
    screenshot is cropped to it - otherwise the region pick would silently
    degrade to OCRing the whole viewport.
    """
    try:
        from ...computer_vision import TemplateMatching
        from ...config import CV2_AVAILABLE

        if stop_flag and stop_flag():
            logger.info("Stopped before web OCR capture")
            return ""
        driver = _web_driver()
        if driver is None:
            return ""

        # Reuse the web-conditional locator plumbing + resolver: the element is
        # located by the same identity-aware ladder a web sequence would use.
        element = None
        rect = None
        if web_ocr_locator:
            try:
                from ..conditional_fallback_resources import web_conditions
                from ...web import actions as web_actions

                event = web_conditions._locator_event(web_ocr_locator, "ocr")
                if event is not None:
                    if web_actions._needs_dom_dispatch(event):
                        # Shadow-DOM pick (a form popup like LinkedIn's
                        # interop-outlet): WebDriver cannot return the element,
                        # so crop to its in-page box instead of OCRing the page.
                        rect = web_actions.deep_element_rect(driver, event)
                        if rect is None:
                            logger.warning(
                                "Web OCR: shadow element not found; OCRing full page"
                            )
                    else:
                        element = web_actions.wait_for_element(
                            driver, event, web_conditions._Config(5.0)
                        )
            except Exception as exc:
                logger.warning(f"Web OCR: element resolve failed: {exc}")
                element = None

        path = _save_web_screenshot(driver, element, "llm_ocr_web")
        if not path:
            return ""

        try:
            if CV2_AVAILABLE:
                import cv2
                screen_cv = cv2.imread(path)
            else:
                from PIL import Image
                import numpy as np
                screen_cv = np.array(Image.open(path).convert("RGB"))[:, :, ::-1]
        except Exception as exc:
            logger.error(f"Web OCR: could not load screenshot: {exc}")
            cleanup_screenshot(path)
            return ""
        if screen_cv is None:
            cleanup_screenshot(path)
            return ""

        # Crop to the picked shadow region (None passes through untouched).
        screen_cv = _crop_region(screen_cv, rect)

        ocr_results = TemplateMatching.ocr_extract_all_text(
            min_confidence=ocr_confidence, screen_cv=screen_cv, stop_flag=stop_flag
        )
        cleanup_screenshot(path)

        if not ocr_results:
            logger.warning("Web OCR: no text extracted")
            return ""
        filtered = [
            item["text"] for item in ocr_results
            if item.get("confidence", 0) >= ocr_confidence
        ]
        if filtered:
            text = "\n".join(filtered)
            logger.info(f"Web OCR extracted {len(filtered)} text blocks")
            return text
        logger.warning(f"Web OCR: no text with confidence >= {ocr_confidence}")
        return ""
    except Exception as e:
        logger.error(f"Web OCR processing failed: {e}")
        return ""


def _web_page_text(stop_flag) -> str:
    """Visible text of the chain's shared browser, shadow roots and same-origin
    iframes included (``document.body.innerText`` alone misses a form/panel
    rendered in a shadow root or an embedded frame)."""
    if stop_flag and stop_flag():
        return ""
    driver = _web_driver()
    if driver is None:
        return ""
    try:
        from ...web import actions as web_actions
        return driver.execute_script(web_actions.JS_VISIBLE_TEXT) or ""
    except Exception as exc:
        logger.error(f"Web page text failed: {exc}")
        return ""


def get_input_text(
    node: Dict,
    variables: Dict[str, object],
    input_source: str,
    ocr_confidence: int,
    stop_flag,
    use_direct_rag: bool,
    workflow_graph: Optional[Dict] = None,
    web_mode: bool = False,
    web_ocr_locator: str = "",
    region=None,
) -> Tuple[Optional[str], bool]:
    """Return input text and whether to inject raw input into prompt.

    For 'previous' source with direct RAG enabled, we avoid injecting raw text.
    """
    input_source = (str(input_source) or "none").lower()
    use_input_text_in_prompt = True
    input_text: Optional[str] = None

    from AI.consult import OllamaClient

    # Check if a refresh is forced by the adversarial loop
    _node_id = node.get('id', node.get('node_id', 'Unknown'))
    force_refresh = variables.get(f"node_{_node_id}_force_refresh", False)
    if force_refresh:
        logger.info(f"Force refresh triggered for node {_node_id}, clearing stale vision context.")
        variables[f"node_{_node_id}_force_refresh"] = False # reset flag

    if input_source in ("ocr", "1"):
        if web_mode:
            input_text = _web_ocr_input(web_ocr_locator, ocr_confidence, stop_flag)
        else:
            input_text = _ocr_input(ocr_confidence, stop_flag, region=region)
    elif input_source == "page_text":
        input_text = _web_page_text(stop_flag)
    elif input_source in ("previous", "previous_node", "2"):
        input_text = _previous_input(node, variables)
        if use_direct_rag:
            use_input_text_in_prompt = False
            # Check if any upstream is an Input passthrough node — if so,
            # force direct injection and let the caller skip RAG processing.
            for inp in node.get("inputs", []):
                cid = inp.get("from_node")
                if cid and variables.get(f"node_{cid}_is_input_passthrough"):
                    use_input_text_in_prompt = True
                    break
    elif input_source == "context":
        input_text = _context_input(node, variables)
        if use_direct_rag:
            use_input_text_in_prompt = False
    elif input_source == "clipboard" or input_source == "3":
        input_text = _clipboard_input()
    elif input_source in ("input", "input_node", "4"):
        input_text = _input_node_source(node, variables)
    else:
        # Explicit "none" means no text injection.  An Input node's value
        # reaches this node ONLY through an explicit edge from its 'data'
        # output port (consumed via input_source 'input' / 'previous'); base
        # ports only drive execution.  No graph walk, no auto-detection: the
        # graph topology decides how far an Input's value propagates.
        input_text = None
        use_input_text_in_prompt = False

    return input_text, use_input_text_in_prompt
