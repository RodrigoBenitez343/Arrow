import logging
logger = logging.getLogger(__name__)
# action_handlers.py
"""
Action execution handlers for different types of automation actions.
"""

import os
import threading
import time
import math
import pyautogui
import pyperclip
from pynput import keyboard
from numpy import random

# Disable PyAutoGUI failsafe to prevent interruption when mouse moves to corner
pyautogui.FAILSAFE = False
from .config import CV2_AVAILABLE
from .computer_vision import TemplateMatching
from .key_defs import MODIFIER_NAMES, key_to_name, pynput_key_for
from .screenshot_cleanup import register_temporal_screenshot
from .uia_locator import element_tiers, resolve_element, resolve_repeat

if CV2_AVAILABLE:
    import cv2
    import numpy as np


# Human-like click deviation: the physical click is nudged 5-10px off the match
# center in a random direction, clamped to the matched template (which is
# centered on the original click), so it can never leave the element.
CLICK_JITTER_MIN = 5.0
CLICK_JITTER_MAX = 10.0
_JITTER_EDGE_MARGIN = 3


def _template_pixel_size(template):
    """(width, height) of a replay template (PIL image or file path), or None."""
    try:
        if template is None:
            return None
        if isinstance(template, str):
            from PIL import Image
            with Image.open(template) as image:
                return image.size
        size = getattr(template, "size", None)
        if size and len(size) == 2:
            return int(size[0]), int(size[1])
    except Exception:
        return None
    return None


def jittered_click_target(cx, cy, template_size,
                          drift_min=CLICK_JITTER_MIN, drift_max=CLICK_JITTER_MAX):
    """Nudge a matched click center by a random ``drift_min``-``drift_max`` px.

    ``template_size`` is the matched crop's ``(width, height)``.  The nudge is
    capped at the template's inner radius so a small target never gets a click
    outside the element.  Both bounds come from the sequence's "Click Drift"
    properties (0-0 clicks the exact centre).
    """
    lo, hi = max(0.0, float(drift_min)), max(0.0, float(drift_max))
    if hi < lo:
        lo, hi = hi, lo
    radius = float(random.uniform(lo, hi))
    angle = float(random.uniform(0.0, 2.0 * math.pi))
    tw, th = (template_size or (35, 35))
    limit = min(max(0, int(tw) // 2 - _JITTER_EDGE_MARGIN),
                max(0, int(th) // 2 - _JITTER_EDGE_MARGIN))
    if limit < radius:
        radius = float(max(0, limit))
    return (int(round(cx + radius * math.cos(angle))),
            int(round(cy + radius * math.sin(angle))))


def _repeat_key(repeat):
    """Stable key for a repeating set's processed-cursor in selection_memory.

    The cursor must SURVIVE loop boundaries so each pass advances to the next
    row (like the web entity cursor); it is reset only when a fresh chain run
    starts (MultiSequencePlayer.play_chain clears selection memory).
    """
    try:
        container = repeat.get('container') or {}
        kind = repeat.get('kind') or {}
        return "repeat:%s|%s|%s|%s|%s" % (
            container.get('automation_id') or '',
            container.get('class_name') or '',
            kind.get('control_type') or '',
            kind.get('class_name') or '',
            # The marked row's name: two sets in ONE container that differ only
            # by file type (PDFs vs folders on the desktop) must not share a
            # cursor, or clicking one set would exhaust the other.
            kind.get('row_name') or '',
        )
    except Exception:
        return "repeat:unknown"


def _resolve_point(desc):
    """Entity-first live point for an element descriptor, or None.

    Used for a drag's endpoints so a moved window / reflowed layout still drags
    the right thing to the right place.  Never raises; None means "fall back to
    the recorded absolute coordinates".
    """
    if not isinstance(desc, dict):
        return None
    try:
        point = resolve_element(desc)
    except Exception:
        return None
    if not point:
        return None
    return {"x": int(point[0]), "y": int(point[1])}


def _tier_count(desc):
    """How many UIA identity tiers a recorded element offers (0 = none)."""
    if not isinstance(desc, dict):
        return 0
    try:
        return len(element_tiers(desc))
    except Exception:
        return 0


def _with_row_parity(repeat, action):
    """Repeat block carrying the ROW's box + name, backfilled for old records.

    Extent parity (see ``uia_locator.enumerate_repeat``) needs the marked row's
    box to keep a too-high container's toolbar children out of the set, and
    file-type parity needs its display name to resolve the row's extension on a
    shell file list.  New recordings store both in the ``repeat`` block; older
    ones only have the clicked ``element``, which IS the row - so they are taken
    from there rather than forcing a re-record.
    """
    element = action.get("element") or {}
    kind = dict(repeat.get("kind") or {})
    out = repeat
    if not repeat.get("row_rect") and element.get("rect"):
        out = dict(out)
        out["row_rect"] = [int(v) for v in element["rect"]]
    if not kind.get("row_name") and element.get("name"):
        kind["row_name"] = str(element["name"])
        if out is repeat:
            out = dict(out)
        out["kind"] = kind
    return out


def _use_element_first(action):
    """Element-first replay applies unless the click was recorded as visual.

    ``match == "visual"`` (right-Ctrl gesture at record time) must replay
    through the legacy template match: the stored element can resolve to a
    container region whose subtree re-find lands the click in the wrong spot.
    """
    return bool(action.get('element')) and action.get('match') != 'visual'


class ActionHandlers:
    """
    Contains methods for handling different types of automation actions.
    """
    
    def __init__(self, bot_instance):
        """
        Initialize with reference to the bot instance for accessing shared state.
        
        Args:
            bot_instance: Reference to the SeleniumBot instance
        """
        self.bot = bot_instance
        self.template_matcher = TemplateMatching()
        self.typing_lock = threading.Lock()
        self.keyboard_controller = keyboard.Controller()
        self.vnc_bridge = None
        # Canonical names of keys currently held down by key_event playback
        # (used for modifier reconciliation and stuck-key cleanup).
        self._held_keys = set()
        
    def _send_agent_action(self, payload):
        agent_url = getattr(self.bot, 'sandbox_agent_url', None)
        if not agent_url: return False
        try:
            import urllib.request
            import json
            # Ensure the base url doesn't have a trailing slash before appending /action
            base_url = agent_url.rstrip('/')
            
            # Print the actual URL being requested to debug 404s
            target_url = f"{base_url}/action"
            
            req = urllib.request.Request(
                target_url,
                data=json.dumps(payload).encode('utf-8'),
                headers={'Content-Type': 'application/json'}
            )
            timeout_s = float(getattr(self.bot, "sandbox_agent_timeout_s", 10.0) or 10.0)
            with urllib.request.urlopen(req, timeout=timeout_s) as response:
                if response.status != 200:
                    raise RuntimeError(f"Agent HTTP status {response.status}")
                # The agent always answers HTTP 200 and signals failures in the
                # body — surface them instead of letting the workflow continue
                # believing a silently-failed action happened.
                body = json.loads(response.read().decode('utf-8'))
            if isinstance(body, dict) and body.get("status") == "error":
                raise RuntimeError(body.get("message") or "Agent action failed")
            return True
        except Exception as e:
            logger.error(f"Agent request failed: {e}")
            raise
            
    def _get_screen_pil(self):
        agent_url = getattr(self.bot, 'sandbox_agent_url', None)
        if agent_url:
            import urllib.request
            import json, base64, io
            from PIL import Image
            try:
                base_url = agent_url.rstrip('/')
                req = urllib.request.Request(
                    f"{base_url}/action",
                    data=json.dumps({"action": "screenshot"}).encode('utf-8'),
                    headers={'Content-Type': 'application/json'}
                )
                timeout_s = float(getattr(self.bot, "sandbox_agent_timeout_s", 10.0) or 10.0)
                with urllib.request.urlopen(req, timeout=timeout_s) as response:
                    resp = json.loads(response.read().decode('utf-8'))
                    if isinstance(resp, dict) and resp.get("status") == "error":
                        raise RuntimeError(resp.get("message") or "Agent screenshot failed")
                    if "image" in resp:
                        img_data = base64.b64decode(resp["image"])
                        img = Image.open(io.BytesIO(img_data)).convert("RGB")
                        try:
                            # Remember the agent's logical screen size so bounds
                            # checks on RDP coordinates use the RDP resolution.
                            sw = int(resp.get("screen_width") or img.width)
                            sh = int(resp.get("screen_height") or img.height)
                            self._last_agent_screen_size = (sw, sh)
                        except Exception:
                            pass
                        # DPI-normalize to the agent's logical size (mirrors the
                        # host capture path) so template-match coordinates are in
                        # the same space the agent's clicks use.
                        try:
                            lw = int(resp.get("screen_width") or 0)
                            lh = int(resp.get("screen_height") or 0)
                            if lw > 0 and lh > 0 and (img.width != lw or img.height != lh):
                                img = img.resize((lw, lh), Image.LANCZOS)
                        except Exception:
                            pass
                        return img
            except Exception as e:
                logger.error(f"Agent screenshot failed: {e}")
                raise
        # Keep the cosmetic execution overlay out of the frame: the replay
        # matches its recorded templates against THIS image.
        try:
            from .execution_overlay_bus import bus as _exec_bus
            _exec_bus.hide_overlay_for_capture()
        except Exception:
            pass
        return pyautogui.screenshot()

    def _get_agent_position(self):
        """Return the RDP agent's current cursor position, or None on failure."""
        agent_url = getattr(self.bot, 'sandbox_agent_url', None)
        if not agent_url:
            return None
        try:
            import urllib.request
            import json as _json
            base_url = str(agent_url).rstrip('/')
            req = urllib.request.Request(
                f"{base_url}/action",
                data=_json.dumps({"action": "get_position"}).encode('utf-8'),
                headers={'Content-Type': 'application/json'}
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = _json.loads(resp.read().decode('utf-8'))
            if isinstance(data, dict) and data.get("status") != "error" and "x" in data:
                try:
                    # Cache the agent's logical screen size — absolute workflow
                    # coordinates are scaled by this ratio before being sent.
                    sw = int(data.get("screen_width") or 0)
                    sh = int(data.get("screen_height") or 0)
                    if sw > 0 and sh > 0:
                        self._last_agent_screen_size = (sw, sh)
                except Exception:
                    pass
                return (int(data["x"]), int(data["y"]))
        except Exception as e:
            logger.warning(f"Agent get_position failed: {e}")
        return None

    def _agent_coord_scale(self):
        """Return (scale_x, scale_y) mapping host logical coords -> agent logical
        coords. The RDP session resolution/DPI can differ from the host's, so
        absolute workflow coordinates must be scaled before being sent."""
        try:
            aw, ah = getattr(self, '_last_agent_screen_size', None) or (0, 0)
            hw, hh = pyautogui.size()
            if aw > 0 and ah > 0 and hw > 0 and hh > 0:
                return (aw / hw, ah / hh)
        except Exception:
            pass
        return (1.0, 1.0)

    def _scale_abs(self, x, y):
        """Scale host-space absolute coordinates into the agent's space."""
        try:
            sx, sy = self._agent_coord_scale()
            return (int(x * sx), int(y * sy))
        except Exception:
            return (int(x), int(y))

    def _tap_key(self, key):
        """Helper to press and release a key with minimal delay."""
        agent_url = getattr(self.bot, 'sandbox_agent_url', None)
        key_str = key_to_name(key)
        
        if agent_url:
            self._send_agent_action({"action": "hotkey", "keys": [key_str]})
            return
            
        if self.vnc_bridge:
            self.vnc_bridge.key_press(key_str)
            time.sleep(0.005)
            return

        self.keyboard_controller.press(key)
        time.sleep(0.005) # Micro delay for reliability
        self.keyboard_controller.release(key)
        # Keep the local held-state in sync: the tap released this key, so it
        # is no longer held from the player's perspective.
        self._held_keys.discard(key_to_name(key))

    def _press_key_combo(self, modifiers, key):
        agent_url = getattr(self.bot, 'sandbox_agent_url', None)
        if agent_url:
            keys = [mod.name if hasattr(mod, 'name') else str(mod) for mod in modifiers] + [str(key)]
            self._send_agent_action({"action": "hotkey", "keys": keys})
            return
            
        pressed_mods = []
        try:
            for mod in modifiers:
                self.keyboard_controller.press(mod)
                pressed_mods.append(mod)
            self.keyboard_controller.press(key)
            self.keyboard_controller.release(key)
        finally:
            for mod in reversed(pressed_mods):
                self.keyboard_controller.release(mod)
        # Keep the local held-state in sync: the combo released everything it
        # pressed, so none of these keys is held anymore. Without this, a
        # legacy clipboard action (e.g. Ctrl+C) would leave 'ctrl' marked as
        # held and corrupt the modifier state of subsequent key_event chords.
        for mod in modifiers:
            self._held_keys.discard(key_to_name(mod))
        self._held_keys.discard(key_to_name(key))

    def handle_click_action(self, idx, action, fallback_callback=None, stop_flag=None):
        """
        Handles click actions with visual template matching.
        
        Args:
            idx (int): Action index
            action (dict): Action configuration
            fallback_callback (callable): Optional fallback callback
            stop_flag (callable): Optional stop flag function
            
        Returns:
            bool: True if successful, False if fallback was triggered
        """
        self.bot.random_delay(0.001, 0.01)  # Reduced from 0.01-0.1 - Add a small random delay before clicking.

        # Log current screen dimensions to help diagnose DPI/coordinate issues
        try:
            _sw, _sh = pyautogui.size()
            logger.debug(
                "[DPI] pyautogui.size() = %dx%d", _sw, _sh,
            )
        except Exception:
            pass

        if not action.get('screenshot') and not action.get('screenshot_data'):
            logger.error(f"Action {idx} is a click without a screenshot. Aborting playback.")
            raise Exception(f"Action {idx} is a click without a screenshot.")

        screenshot_path = action.get('screenshot', '')
        screenshot_data = action.get('screenshot_data')
        
        # Resolve the screenshot to a usable template (path string, PIL Image, or base64)
        screenshot_template = None
        element_key = None
        
        # Priority 1: Embedded base64 image data
        if screenshot_data:
            from .image_utils import is_embedded_image, base64_to_pil
            if is_embedded_image(screenshot_data):
                screenshot_template = base64_to_pil(screenshot_data)
                if screenshot_template:
                    # Use screenshot path for element_key if available, otherwise hash the data
                    if screenshot_path:
                        element_key = f"embedded:{os.path.normpath(screenshot_path)}"
                    else:
                        import hashlib
                        data_hash = hashlib.md5(screenshot_data.encode('utf-8')).hexdigest()[:16]
                        element_key = f"embedded:{data_hash}"
                    logger.debug(f"Using embedded screenshot data for action {idx}")
        
        # Priority 2: File path (resolve and load from disk)
        if screenshot_template is None and screenshot_path:
            cand_paths = []
            norm_rel = os.path.normpath(screenshot_path)
            if os.path.isabs(norm_rel):
                cand_paths.append(os.path.normpath(norm_rel))
            else:
                cwd = os.getcwd()
                project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                cand_paths.extend([
                    os.path.normpath(os.path.join(cwd, norm_rel)),
                    os.path.normpath(os.path.join(project_root, norm_rel)),
                    os.path.normpath(os.path.join(project_root, 'sequences', norm_rel)),
                    os.path.normpath(os.path.join(project_root, 'sequences', 'screenshots', os.path.basename(norm_rel))),
                    os.path.normpath(os.path.join(project_root, 'sequences', 'sequences', 'screenshots', os.path.basename(norm_rel))),
                ])

            resolved_path = None
            for cand in cand_paths:
                if os.path.exists(cand):
                    resolved_path = cand
                    break

            if not resolved_path:
                logger.error(f"Screenshot file for action {idx} does not exist: {cand_paths[0]}")
                raise Exception(f"Screenshot file not found: {cand_paths[0]}")
            
            screenshot_path = resolved_path
            screenshot_template = screenshot_path  # Use path string for template matching
            element_key = os.path.abspath(screenshot_path)
        
        if element_key is None:
            logger.error(f"Could not resolve screenshot for action {idx}")
            raise Exception(f"Could not resolve screenshot for action {idx}")

        target_coords = None
        screenshot_found = False

        # ── Repeating sibling set (Insert-marked at record time) ──
        # Explicit repetition, the desktop twin of the web entity cursor: walk
        # the recorded sibling set and act on the next UNCLICKED row each pass.
        # This is the ONLY repetition path - the old implicit image-cluster
        # auto-repeat is gone.  `processed` lives in selection_memory and
        # SURVIVES loop boundaries, so each pass advances to the NEXT row (like
        # the web entity cursor); it resets once per chain run.
        repeat = action.get('repeat')
        if repeat:
            repeat = _with_row_parity(repeat, action)
            repeat_key = _repeat_key(repeat)
            repeat_state = self.bot.selection_memory.setdefault(repeat_key, {})
            processed = repeat_state.setdefault('processed', set())
            info = resolve_repeat(repeat, processed)
            if info is None:
                logger.info(f"Repeating set exhausted for action {idx}; nothing to click")
                return True
            if not info.get('missing'):
                processed.add(info['key'])
                target_coords = (int(info['point'][0]), int(info['point'][1]))
                screenshot_found = True
                logger.info(f"Repeating set: action {idx} -> row '{info['key']}' "
                            f"({len(processed)}/{info.get('total')}) "
                            f"kind={repeat.get('kind')}")
            else:
                # A repeating action must NEVER fall back to the recorded
                # element: that is the single row the marks were made on, so
                # every pass would click it again and the set would never
                # advance.  Skip it loudly and leave the set open instead.
                logger.error(f"Repeating set no longer matches for action {idx}; "
                             f"skipping instead of re-clicking the recorded element")
                return True

        # Element-first: re-find the recorded UIA element and click its live box,
        # so the click survives a moved window / changed scale / scrolled list -
        # cases that break template matching.  Falls through to the image
        # cascade below when the element is absent, unresolvable or unverified,
        # and is skipped entirely for clicks recorded as visual (right Ctrl
        # held) - those replay the legacy template match only.
        if target_coords is None and _use_element_first(action):
            try:
                resolved = resolve_element(action['element'])
            except Exception as element_err:
                resolved = None
                logger.debug(f"Element resolve error for action {idx}: {element_err}")
            if resolved:
                target_coords = (int(resolved[0]), int(resolved[1]))
                screenshot_found = True
                logger.info(f"Element-first match at {target_coords} for action {idx}")

        # Get retry configuration from action
        retry_attempts = action.get('fallback_retry_attempts', 3)
        retry_delay = action.get('fallback_retry_delay', 0.05)  # Reduced from 0.3

        # Capture single screenshot for all search attempts to improve efficiency -
        # skipped when the element path already resolved the target.
        current_screen_cv = None
        current_screen_pil = None
        if target_coords is None:
            try:
                current_screen_pil = self._get_screen_pil()
                if CV2_AVAILABLE:
                    # Capture once and convert for OpenCV operations - keep in memory only
                    screen_np = np.array(current_screen_pil)
                    if len(screen_np.shape) == 3 and screen_np.shape[2] == 4:
                        screen_np = cv2.cvtColor(screen_np, cv2.COLOR_RGBA2RGB)
                    current_screen_cv = cv2.cvtColor(screen_np, cv2.COLOR_RGB2BGR)
                    logger.debug(f"Single screenshot captured (in-memory) for all search methods")
                else:
                    logger.debug(f"In-memory screenshot captured for pyautogui methods")
            except Exception as e:
                logger.debug(f"Failed to capture screenshot: {e}")

        for attempt in range(0 if target_coords is not None else retry_attempts):
            # Check stop flag before each retry attempt
            if stop_flag and stop_flag():
                logger.info(f"Click action {idx} stopped by user request during retry attempt {attempt + 1}")
                return True
                
            logger.info(f"Attempting to find button for action {idx} via screenshot: {os.path.basename(screenshot_path)} (Attempt {attempt + 1}/{retry_attempts})")
            
            # Optimized search strategies with improved confidence thresholds
            search_strategies = [
                # High-confidence template matching first
                {'method': 'template', 'confidence': 0.85, 'grayscale': True, 'name': 'High-confidence grayscale (0.85)'},
                {'method': 'template', 'confidence': 0.85, 'grayscale': False, 'name': 'High-confidence color (0.85)'},
                
                # Multi-scale matching for size variations (prioritized)
                {'method': 'multiscale', 'confidence': 0.8, 'grayscale': True, 'scales': [0.9, 1.0, 1.1], 'name': 'Fine multi-scale (0.8)'},
                {'method': 'multiscale', 'confidence': 0.75, 'grayscale': True, 'scales': [0.8, 1.0, 1.2], 'name': 'Standard multi-scale (0.75)'},
                
                # Region-based search with reasonable expansion
                {'method': 'region', 'confidence': 0.8, 'grayscale': True, 'expand_factor': 2.5, 'name': 'Focused region search (0.8)'},
                {'method': 'region', 'confidence': 0.75, 'grayscale': True, 'expand_factor': 3.5, 'name': 'Expanded region search (0.75)'},
                
                # Medium confidence fallbacks
                {'method': 'template', 'confidence': 0.75, 'grayscale': True, 'name': 'Medium-confidence grayscale (0.75)'},
                {'method': 'multiscale', 'confidence': 0.7, 'grayscale': True, 'scales': [0.7, 1.0, 1.3], 'name': 'Wide multi-scale (0.7)'},
                
                # Lower confidence as last resort
                {'method': 'template', 'confidence': 0.65, 'grayscale': True, 'name': 'Low-confidence grayscale (0.65)'},
                {'method': 'region', 'confidence': 0.65, 'grayscale': True, 'expand_factor': 4.0, 'name': 'Large region search (0.65)'},
            ]

            for strategy in search_strategies:
                # Check stop flag before each search strategy
                if stop_flag and stop_flag():
                    logger.info(f"Click action {idx} stopped by user request during search strategy: {strategy['name']}")
                    return True
                    
                try:
                    logger.info(f"Attempting match with: {strategy['name']}")
                    
                    # Allow screen to stabilize
                    time.sleep(0.05)
                    
                    location = None
                    
                    if strategy['method'] == 'template':
                        # Template matching with multiple candidates to avoid random clicks
                        try:
                            if current_screen_pil:
                                boxes = list(pyautogui.locateAll(
                                    screenshot_template,
                                    current_screen_pil,
                                    confidence=strategy['confidence'],
                                    grayscale=strategy['grayscale']
                                ))
                            else:
                                boxes = list(pyautogui.locateAllOnScreen(
                                    screenshot_template,
                                    confidence=strategy['confidence'],
                                    grayscale=strategy['grayscale']
                                ))
                        except Exception:
                            boxes = []

                        if boxes:
                            centers = []
                            for b in boxes:
                                try:
                                    left = getattr(b, 'left', b[0])
                                    top = getattr(b, 'top', b[1])
                                    width = getattr(b, 'width', b[2])
                                    height = getattr(b, 'height', b[3])
                                except Exception:
                                    continue
                                cx = int(left + width / 2)
                                cy = int(top + height / 2)
                                centers.append((cx, cy))

                            if centers:
                                # Single best match: the instance nearest the
                                # recorded coordinates.  Repetition is the
                                # explicit, Insert-marked `repeat` cursor above -
                                # the old implicit multi-instance selection is gone.
                                coords = action.get('coordinates', {})
                                hint_x = coords.get('x') if isinstance(coords, dict) else action.get('x')
                                hint_y = coords.get('y') if isinstance(coords, dict) else action.get('y')
                                if isinstance(hint_x, (int, float)) and isinstance(hint_y, (int, float)):
                                    location = min(
                                        centers,
                                        key=lambda c: math.hypot(c[0] - hint_x, c[1] - hint_y),
                                    )
                                else:
                                    location = centers[0]
                            else:
                                location = None
                        else:
                            # Fallback to single best match if multiple candidates not found
                            if current_screen_pil:
                                try:
                                    box = pyautogui.locate(
                                        screenshot_template,
                                        current_screen_pil,
                                        confidence=strategy['confidence'],
                                        grayscale=strategy['grayscale']
                                    )
                                    location = pyautogui.center(box) if box else None
                                except Exception:
                                    location = None
                            else:
                                location = pyautogui.locateCenterOnScreen(
                                    screenshot_template,
                                    confidence=strategy['confidence'],
                                    grayscale=strategy['grayscale']
                                )
                    
                    elif strategy['method'] == 'multiscale':
                        # Multi-scale template matching with shared screenshot
                        location = self.template_matcher.multiscale_template_match(
                            screenshot_template, 
                            strategy['confidence'],
                            strategy['scales'],
                            strategy['grayscale'],
                            current_screen_cv
                        )
                    
                    elif strategy['method'] == 'region':
                        # Region-based search with original coordinates as hint and shared screenshot
                        coords = action.get('coordinates', {})
                        hint_x = coords.get('x', 0) if isinstance(coords, dict) else action.get('x', 0)
                        hint_y = coords.get('y', 0) if isinstance(coords, dict) else action.get('y', 0)
                        location = self.template_matcher.region_based_search(
                            screenshot_template,
                            hint_x,
                            hint_y,
                            strategy['confidence'],
                            strategy['expand_factor'],
                            strategy['grayscale'],
                            current_screen_cv
                        )
                    
                    if location:
                        screen_width, screen_height = pyautogui.size()
                        try:
                            # In sandbox mode the location is in RDP pixel space;
                            # validate against the RDP screen, not the host's.
                            if getattr(self.bot, 'sandbox_agent_url', None) and getattr(self, '_last_agent_screen_size', None):
                                screen_width, screen_height = self._last_agent_screen_size
                        except Exception:
                            pass
                        target_x, target_y = int(location[0]), int(location[1])

                        if 0 <= target_x <= screen_width and 0 <= target_y <= screen_height:
                            target_coords = (target_x, target_y)
                            logger.info(f"Element found at {target_coords} using {strategy['name']}")
                            screenshot_found = True
                            break
                        else:
                            logger.warning(f"Found coordinates {location} are outside screen bounds")
                except Exception as e:
                    logger.debug(f"{strategy['name']} failed for action {idx}: {e}")

            if screenshot_found:
                break
            
            if attempt < retry_attempts - 1:
                logger.info(f"Element not found for action {idx}, retrying in {retry_delay}s...")
                # Sleep in small increments to allow for interruption
                elapsed = 0
                while elapsed < retry_delay:
                    if stop_flag and stop_flag():
                        logger.info(f"Click action {idx} stopped by user request during retry delay")
                        return True
                    sleep_time = min(0.05, retry_delay - elapsed)  # Reduced from 0.1
                    time.sleep(sleep_time)
                    elapsed += sleep_time
        
        if target_coords:
            # Human-like deviation for plain clicks only - drags and moves stay
            # exact.  target_coords stays the exact match centre; only the
            # physical click point is nudged.
            click_coords = target_coords
            if action.get('type') in ('click', 'double_click'):
                click_coords = jittered_click_target(
                    target_coords[0], target_coords[1],
                    _template_pixel_size(screenshot_template),
                    getattr(self.bot, 'click_drift_min', CLICK_JITTER_MIN),
                    getattr(self.bot, 'click_drift_max', CLICK_JITTER_MAX),
                )
            button = action.get('button', 'left')
            agent_url = getattr(self.bot, 'sandbox_agent_url', None)
            modifier_key = None
            if action.get('type') == 'ctrl_click':
                modifier_key = keyboard.Key.ctrl_l
            elif action.get('type') == 'shift_click':
                modifier_key = keyboard.Key.shift_l
            
            with self.typing_lock:
                if agent_url:
                    click_payload = {
                        "action": "click",
                        "x": click_coords[0], "y": click_coords[1],
                        "button": button,
                        "duration": 0.1,
                    }
                    if modifier_key is not None:
                        click_payload["modifiers"] = [key_to_name(modifier_key)]
                    try:
                        # Send the human-like curved waypoints so in-session
                        # cursor travel matches the graceful host movement.
                        pos = self._get_agent_position()
                        if pos is not None and hasattr(self.bot, '_build_human_path'):
                            click_payload["points"] = self.bot._build_human_path(
                                click_coords[0], click_coords[1], start=pos)
                    except Exception:
                        pass
                    self._send_agent_action(click_payload)
                    if action.get('type') == 'double_click':
                        time.sleep(0.1)
                        self._send_agent_action(click_payload)
                    logger.info(f"Agent Clicked ({button}) at {click_coords}")
                elif self.vnc_bridge:
                    self.vnc_bridge.click(click_coords[0], click_coords[1], button=1 if button == 'left' else 3)
                    logger.info(f"VNC Clicked ({button}) at {click_coords}")
                else:
                    pressed_modifier = False
                    try:
                        if modifier_key is not None:
                            self.keyboard_controller.press(modifier_key)
                            pressed_modifier = True

                        self.bot.human_mouse_move(click_coords[0], click_coords[1])
                        time.sleep(0.01)
                        if action.get('type') == 'double_click':
                            self.bot.human_click(button=button, double=True)
                            logger.info(f"Double-clicked ({button}) at {click_coords}")
                        else:
                            self.bot.human_click(button=button, double=False)
                            logger.info(f"Clicked ({button}) at {click_coords}")
                    finally:
                        if pressed_modifier:
                            try:
                                self.keyboard_controller.release(modifier_key)
                            except Exception:
                                pass
            
            self.bot.last_click_time = time.time()
            return True
        else:
            logger.warning(f"Visual match failed for action {idx} after all attempts.")
            
            if action.get('fallback_sequence') and fallback_callback:
                fallback_path = action['fallback_sequence']
                if not os.path.isabs(fallback_path):
                    fallback_path = os.path.join(os.getcwd(), fallback_path)
                
                logger.info(f"Triggering fallback sequence for action {idx}: {fallback_path}")
                fallback_callback(fallback_path, idx)
                return False  # Indicate fallback was triggered
            else:
                logger.error(f"Visual match for action {idx} failed and no fallback is configured. Aborting playback.")
                raise Exception("Visual match failed and no fallback configured.")

    def handle_move_to_action(self, idx, action, stop_flag=None):
        if stop_flag and stop_flag():
            logger.info("Stop flag detected before move_to - aborting move_to action")
            return True

        coords = action.get('coordinates')
        if isinstance(coords, dict):
            x = coords.get('x')
            y = coords.get('y')
        elif isinstance(coords, (list, tuple)) and len(coords) >= 2:
            x, y = coords[0], coords[1]
        else:
            x = action.get('x')
            y = action.get('y')

        if x is None or y is None:
            logger.warning(f"move_to action {idx} missing coordinates, skipping")
            return True

        agent_url = getattr(self.bot, 'sandbox_agent_url', None)
        if agent_url:
            # Send move action to RDP agent with human-like curved waypoints,
            # scaling absolute coordinates into the session's coordinate space
            # (session resolution/DPI may differ from the host's).
            sx, sy = self._scale_abs(int(x), int(y))
            payload = {"action": "move", "x": sx, "y": sy, "duration": 0.25}
            try:
                pos = self._get_agent_position()
                if pos is not None and hasattr(self.bot, '_build_human_path'):
                    payload["points"] = self.bot._build_human_path(sx, sy, start=pos)
            except Exception:
                pass
            self._send_agent_action(payload)
            logger.info(f"Agent Moved to ({sx}, {sy})")
            return True
        if self.vnc_bridge:
            return True

        self.bot.human_mouse_move(int(x), int(y))
        return True

    def handle_absolute_click_action(self, idx, action, stop_flag=None):
        if stop_flag and stop_flag():
            logger.info("Stop flag detected before absolute_click - aborting absolute_click action")
            return True

        coords = action.get('coordinates')
        if isinstance(coords, dict):
            x = coords.get('x')
            y = coords.get('y')
        elif isinstance(coords, (list, tuple)) and len(coords) >= 2:
            x, y = coords[0], coords[1]
        else:
            x = action.get('x')
            y = action.get('y')

        if x is None or y is None:
            logger.warning(f"absolute_click action {idx} missing coordinates, skipping")
            return True

        button = action.get('button', 'left')
        with self.typing_lock:
            agent_url = getattr(self.bot, 'sandbox_agent_url', None)
            if agent_url:
                # Refresh the agent's screen size, then scale the recorded
                # absolute coordinates into the session's coordinate space.
                self._get_agent_position()
                cx, cy = self._scale_abs(int(x), int(y))
                self._send_agent_action({"action": "click", "x": cx, "y": cy, "button": button, "duration": 0.15})
                self.bot.last_click_time = time.time()
                logger.info(f"Agent Absolute Clicked ({button}) at ({cx}, {cy})")
                return True

            if self.vnc_bridge:
                self.vnc_bridge.click(int(x), int(y), button=1 if button == 'left' else 3)
                self.bot.last_click_time = time.time()
                logger.info(f"VNC Absolute Clicked ({button}) at ({int(x)}, {int(y)})")
                return True

            self.bot.human_mouse_move(int(x), int(y))
            time.sleep(0.01)
            self.bot.human_click(button=button, double=False)
            self.bot.last_click_time = time.time()
            logger.info(f"Absolute Clicked ({button}) at ({int(x)}, {int(y)})")
            return True

    def handle_relative_click_action(self, idx, action, stop_flag=None):
        if stop_flag and stop_flag():
            logger.info("Stop flag detected before relative_click - aborting")
            return True

        dx = action.get('dx', 0)
        dy = action.get('dy', 0)
        button = action.get('button', 'left')

        agent_url = getattr(self.bot, 'sandbox_agent_url', None)
        cur_x, cur_y = pyautogui.position()
        if agent_url:
            # The RDP session has its own cursor; derive the offset from the
            # agent's position, not the host's, and scale the recorded offsets
            # into the session's coordinate space.
            agent_pos = self._get_agent_position()
            if agent_pos is not None:
                cur_x, cur_y = agent_pos
            dx, dy = self._scale_abs(dx, dy)
        target_x = int(cur_x + dx)
        target_y = int(cur_y + dy)

        with self.typing_lock:
            if agent_url:
                self._send_agent_action({"action": "click", "x": target_x, "y": target_y, "button": button, "duration": 0.15})
                self.bot.last_click_time = time.time()
                logger.info(f"Agent Relative Clicked ({button}) at offset ({dx}, {dy}) -> ({target_x}, {target_y})")
                return True

            if self.vnc_bridge:
                self.vnc_bridge.click(target_x, target_y, button=1 if button == 'left' else 3)
                self.bot.last_click_time = time.time()
                logger.info(f"VNC Relative Clicked ({button}) at offset ({dx}, {dy}) -> ({target_x}, {target_y})")
                return True

            self.bot.human_mouse_move(target_x, target_y)
            time.sleep(0.01)
            self.bot.human_click(button=button, double=False)
            self.bot.last_click_time = time.time()
            logger.info(f"Relative Clicked ({button}) at offset ({dx}, {dy}) -> ({target_x}, {target_y})")
            return True

    def handle_type_string_action(self, idx, action, stop_flag=None):
        """
        Handles typing text actions with optimized speed and completion tracking.
        
        Args:
            idx (int): Action index
            action (dict): Action configuration
            stop_flag (callable): Function to check if execution should stop
            
        Returns:
            bool: True when typing is complete
        """
        if 'text' in action:
            with self.typing_lock:
                if stop_flag and stop_flag():
                    logger.info("Stop flag detected before typing - aborting type string action")
                    return False

                try:
                    post_type_delay = float(action.get('post_type_delay', 0.02))
                except Exception:
                    post_type_delay = 0.02

                if 'delay_after_click' in action:
                    time_since_click = time.time() - self.bot.last_click_time
                    required_delay = action['delay_after_click']
                    if time_since_click < required_delay:
                        wait_time = required_delay - time_since_click
                        logger.debug(f"Waiting {wait_time:.2f}s after click before typing")
                        time.sleep(wait_time)

                text = action['text']
                if not isinstance(text, str):
                    try:
                        text = str(text)
                        logger.warning(f"Converted non-string text to string: {type(action['text'])} -> {text[:50]}...")
                    except Exception as str_error:
                        logger.error(f"Failed to convert text to string: {str_error}")
                        return False

                agent_url = getattr(self.bot, 'sandbox_agent_url', None)
                if agent_url:
                    # Match the direct-path typing contract: same default cadence,
                    # and Unicode-safe clipboard paste for non-ASCII / long text.
                    contains_non_ascii = any(ord(c) > 126 for c in text)
                    force_clipboard = bool(action.get('use_clipboard', False))
                    if force_clipboard or contains_non_ascii or len(text) > 1000:
                        self._send_agent_action({"action": "clipboard_paste", "text": text})
                    else:
                        interval = action.get('keystroke_delay', 0.001)
                        self._send_agent_action({"action": "type", "text": text, "interval": interval})
                    if action.get('press_enter', False):
                        self._send_agent_action({"action": "hotkey", "keys": ['enter']})
                    if post_type_delay > 0:
                        time.sleep(post_type_delay)
                    action['completed'] = True
                    return True
                elif self.vnc_bridge:
                    self.vnc_bridge.type_text(text)
                    if action.get('press_enter', False):
                        self.vnc_bridge.key_press('enter')
                    if post_type_delay > 0:
                        time.sleep(post_type_delay)
                    action['completed'] = True
                    return True

                try:
                    force_clipboard = bool(action.get('use_clipboard', False))
                except Exception:
                    force_clipboard = False

                contains_non_ascii = any(ord(c) > 126 for c in text)
                force_typing = bool(action.get('force_typing', False))
                use_clipboard = False if force_typing else (force_clipboard or contains_non_ascii or len(text) > 1000)

                if use_clipboard:
                    try:
                        pyperclip.copy(text)
                        time.sleep(0.01)
                        self._press_key_combo([keyboard.Key.ctrl], 'v')
                        logger.info(f"Pasted text via clipboard (Unicode-safe), length={len(text)}")

                        try:
                            paste_settle = min(1.0, max(post_type_delay, 0.02 + (len(text) * 0.0002)))
                        except Exception:
                            paste_settle = max(post_type_delay, 0.02)
                        if paste_settle > 0:
                            time.sleep(paste_settle)

                        action['completed'] = True
                        return True
                    except Exception as clip_error:
                        logger.warning(f"Clipboard paste failed, falling back to keyboard typing: {clip_error}")

                char_by_char = action.get('char_by_char', False)

                keystroke_delay = action.get('keystroke_delay', 0.001)

                if char_by_char:
                    batch_size = 1
                    batch_delay = action.get('char_delay', 0.01)
                    logger.info(f"Typing text character-by-character with delay={batch_delay}s, keystroke_delay={keystroke_delay}s")
                else:
                    batch_size = action.get('batch_size', 5)
                    batch_delay = action.get('batch_delay', 0.01)
                    logger.info(f"Typing text with batch_size={batch_size}, batch_delay={batch_delay}, keystroke_delay={keystroke_delay}s")

                for i in range(0, len(text), batch_size):
                    if stop_flag and stop_flag():
                        logger.info("Stop flag detected during typing - aborting type string action")
                        return False

                    batch = text[i:i+batch_size]

                    try:
                        filtered_batch = ''.join(c for c in batch if c.isprintable() or c in '\n\t\r ')

                        if filtered_batch != batch:
                            logger.warning(f"Filtered out non-printable characters from batch")

                        if '\n' in filtered_batch:
                            segments = filtered_batch.split('\n')
                            for j, segment in enumerate(segments):
                                if stop_flag and stop_flag():
                                    logger.info("Stop flag detected during line break typing - aborting type string action")
                                    return False

                                if segment:
                                    for char in segment:
                                        self.keyboard_controller.type(char)
                                        if keystroke_delay > 0:
                                            time.sleep(keystroke_delay)
                                if j < len(segments) - 1:
                                    time.sleep(0.01)
                                    self._tap_key(keyboard.Key.enter)
                                    time.sleep(0.01)
                        else:
                            for char in filtered_batch:
                                self.keyboard_controller.type(char)
                                if keystroke_delay > 0:
                                    time.sleep(keystroke_delay)
                    except Exception as write_error:
                        logger.error(f"Error writing batch: {write_error}")
                        logger.error(f"Problematic batch content: {repr(batch)}")
                        return False

                    if i + batch_size < len(text):
                        time.sleep(batch_delay)

                if action.get('press_enter', False):
                    self._tap_key(keyboard.Key.enter)

                if post_type_delay > 0:
                    time.sleep(post_type_delay)

                logger.info(f"Typed text ({len(text)} chars) using optimized batching")
                action['completed'] = True
                return True
        
        return False

    def handle_keystroke_action(self, idx, action, stop_flag=None):
        """
        Handles special keystroke actions (Enter, Tab, etc.).
        
        Args:
            idx (int): Action index
            action (dict): Action configuration
            stop_flag (callable): Function to check if execution should stop
        """
        with self.typing_lock:
            if 'delay_after_click' in action:
                time_since_click = time.time() - self.bot.last_click_time
                required_delay = action['delay_after_click']
                if time_since_click < required_delay:
                    wait_time = required_delay - time_since_click
                    logger.debug(f"Waiting {wait_time:.2f}s after click before keystroke")
                    elapsed = 0.0
                    while elapsed < wait_time:
                        if stop_flag and stop_flag():
                            logger.info("Stop flag detected before keystroke - aborting keystroke action")
                            return
                        sleep_time = min(0.05, wait_time - elapsed)
                        time.sleep(sleep_time)
                        elapsed += sleep_time

            if stop_flag and stop_flag():
                logger.info("Stop flag detected before keystroke - aborting keystroke action")
                return

            key = action['key'].replace('Key.', '')
            key_mapping = {
                'space': keyboard.Key.space,
                'enter': keyboard.Key.enter,
                'backspace': keyboard.Key.backspace,
                'tab': keyboard.Key.tab,
                'esc': keyboard.Key.esc,
                'up': keyboard.Key.up,
                'down': keyboard.Key.down,
                'left': keyboard.Key.left,
                'right': keyboard.Key.right,
                'delete': keyboard.Key.delete,
                'shift': keyboard.Key.shift,
                'ctrl': keyboard.Key.ctrl,
                'alt': keyboard.Key.alt
            }
            # Extend with the shared canonical key table so legacy recordings
            # of F-keys, media keys, etc. also resolve. Unknown names stay
            # unresolved and are skipped (backward-compatible no-op).
            if key not in key_mapping:
                mapped = pynput_key_for(key)
                if mapped is not None:
                    key_mapping[key] = mapped
            if key in key_mapping:
                keystroke_delay = action.get('keystroke_delay', 0.01)

                agent_url = getattr(self.bot, 'sandbox_agent_url', None)
                if agent_url:
                    self._send_agent_action({"action": "hotkey", "keys": [key]})
                elif self.vnc_bridge:
                    self.vnc_bridge.key_press(key)
                else:
                    self._tap_key(key_mapping[key])

                logger.info(f"Pressed special key: {key}")

                if keystroke_delay > 0:
                    elapsed = 0.0
                    while elapsed < keystroke_delay:
                        if stop_flag and stop_flag():
                            logger.info("Stop flag detected after keystroke - aborting keystroke action")
                            return
                        sleep_time = min(0.05, keystroke_delay - elapsed)
                        time.sleep(sleep_time)
                        elapsed += sleep_time

    def _press_key_name(self, name):
        """Press and locally track a canonical key name (idempotent)."""
        if name in self._held_keys:
            return
        key = pynput_key_for(name)
        if key is None:
            return
        self.keyboard_controller.press(key)
        self._held_keys.add(name)

    def _release_key_name(self, name):
        """Release a canonical key name if locally held (idempotent)."""
        if name not in self._held_keys:
            return
        key = pynput_key_for(name)
        if key is not None:
            try:
                self.keyboard_controller.release(key)
            except Exception:
                pass
        self._held_keys.discard(name)

    def release_all_keys(self):
        """Release every key still held by key_event playback (stuck-key safety)."""
        with self.typing_lock:
            if getattr(self.bot, 'sandbox_agent_url', None):
                # RDP session keys are held by the agent process.
                self._send_agent_action({"action": "release_all_keys"})
                return
            for name in list(self._held_keys):
                self._release_key_name(name)

    def handle_key_event_action(self, idx, action, stop_flag=None):
        """
        Replays a single raw keyboard event recorded by the recorder.

        Each key_event carries the canonical key name, the press/release
        state, a snapshot of the modifiers held at that moment, and the delta
        since the previous key event. Playback reconciles the local
        held-modifier state against the snapshot so arbitrary chords
        (Ctrl+Alt+Shift+Esc, Win+R, ...) are reproduced faithfully and
        stuck-modifier edge cases self-heal.
        """
        key_name = action.get('key')
        state = action.get('state', 'down')
        mods = list(action.get('modifiers') or [])

        # Pace the event by the recorded inter-event timing (capped so a
        # pause mid-recording cannot stall playback indefinitely).
        delta = 0.0
        try:
            delta = float(action.get('delta', 0) or 0)
        except (TypeError, ValueError):
            delta = 0.0
        delta = max(0.0, min(delta, 5.0))
        if delta > 0:
            elapsed = 0.0
            while elapsed < delta:
                if stop_flag and stop_flag():
                    return True
                sleep_time = min(0.05, delta - elapsed)
                time.sleep(sleep_time)
                elapsed += sleep_time

        with self.typing_lock:
            agent_url = getattr(self.bot, 'sandbox_agent_url', None)
            if agent_url:
                # Agent backend now has real press/release semantics: forward
                # each event so holds and stuck-key cleanup behave like the
                # direct path.
                if state == 'down':
                    for m in mods:
                        if m != key_name:
                            self._send_agent_action({"action": "key_down", "key": m})
                    self._send_agent_action({"action": "key_down", "key": key_name})
                else:
                    self._send_agent_action({"action": "key_up", "key": key_name})
                return True

            if self.vnc_bridge:
                if state == 'down':
                    for m in mods:
                        if m != key_name:
                            self.vnc_bridge.key_press(m)
                    self.vnc_bridge.key_press(key_name)
                return True

            if state == 'down':
                if key_name in MODIFIER_NAMES:
                    self._press_key_name(key_name)
                else:
                    # Reconcile modifiers: press what the snapshot says should
                    # be held, release anything locally held that is no longer
                    # part of the chord (heals stuck modifiers from truncated
                    # recordings), then hold the main key until its 'up' event.
                    for name in mods:
                        self._press_key_name(name)
                    for name in list(self._held_keys):
                        if name in MODIFIER_NAMES and name not in mods:
                            self._release_key_name(name)
                    self._press_key_name(key_name)
            else:
                # Release the event's own key; idempotent if already released.
                self._release_key_name(key_name)
        return True

    def handle_scroll_action(self, idx, action, stop_flag=None):
        """
        Handles scroll actions.
        
        Args:
            idx (int): Action index
            action (dict): Action configuration
            stop_flag (callable): Function to check if execution should stop
        """
        # Check stop flag before starting scroll
        if stop_flag and stop_flag():
            logger.info("Stop flag detected before scroll - aborting scroll action")
            return True
        # Extract vertical scroll amount from correct field
        dy = action.get('total_delta', 0)  # Use total_delta from recordings
        
        # Fallback to old 'delta' structure for backward compatibility
        if dy == 0 and 'delta' in action and isinstance(action['delta'], dict):
            dy = action['delta'].get('y', 0)
        
        if dy == 0:
            logger.info("Scroll action with zero delta, skipping.")
            return True

        # Get step count and duration from recording
        num_steps = max(1, int(action.get('steps', 1)))  # Ensure at least 1 step
        total_duration = action.get('duration_sec', 0.0)
        
        # Calculate per-step scroll amount and timing
        step_dy = dy / num_steps
        step_duration = total_duration / num_steps if total_duration > 0 else 0
        
        logger.info(f"Simulating scroll: total_delta={dy}, steps={num_steps}, duration={total_duration:.3f}s")
        
        # Execute scroll in small steps (mimics real user behavior)
        accumulated = 0.0
        for i in range(num_steps):
            # Allow immediate cancellation in long scrolls
            if stop_flag and stop_flag():
                logger.info("Stop flag detected during scroll - aborting scroll action")
                return True
            # Calculate exact step amount (avoids precision loss)
            current_target = (i + 1) * step_dy
            rounded_target = round(current_target)
            step_amount = int(rounded_target - accumulated)
            accumulated = rounded_target
            
            if step_amount != 0:
                with self.typing_lock:
                    agent_url = getattr(self.bot, 'sandbox_agent_url', None)
                    if agent_url:
                        self._send_agent_action({"action": "scroll", "amount": step_amount})
                    elif self.vnc_bridge:
                        clicks = max(1, abs(step_amount) // 100)
                        if step_amount > 0:
                            for _ in range(clicks):
                                self.vnc_bridge.key_press('up')
                        else:
                            for _ in range(clicks):
                                self.vnc_bridge.key_press('down')
                    else:
                        pyautogui.scroll(step_amount)
            
            # Pause between steps to match original timing
            if i < num_steps - 1 and step_duration > 0:
                elapsed = 0.0
                while elapsed < step_duration:
                    if stop_flag and stop_flag():
                        logger.info("Stop flag detected during scroll delay - aborting scroll action")
                        return True
                    sleep_time = min(0.05, step_duration - elapsed)
                    time.sleep(sleep_time)
                    elapsed += sleep_time

    def handle_clipboard_action(self, idx, action):
        """
        Handles clipboard actions (copy, paste, cut, select all).
        
        Args:
            idx (int): Action index
            action (dict): Action configuration
        """
        operation = None
        # Determine the operation (e.g., 'copy', 'paste').
        if action['type'] == 'clipboard':
            operation = action.get('operation')
        else:
            operation = action['type']
        
        with self.typing_lock:
            if operation == 'copy' or operation == 'c':
                time.sleep(0.01)
                self._press_key_combo([keyboard.Key.ctrl], 'c')
                logger.info("Performed Ctrl+C operation")
            elif operation == 'paste' or operation == 'v':
                time.sleep(0.01)
                self._press_key_combo([keyboard.Key.ctrl], 'v')
                logger.info("Performed Ctrl+V operation")
            elif operation == 'cut' or operation == 'x':
                time.sleep(0.01)
                self._press_key_combo([keyboard.Key.ctrl], 'x')
                logger.info("Performed Ctrl+X operation")
            elif operation == 'select_all' or operation == 'a':
                time.sleep(0.01)
                self._press_key_combo([keyboard.Key.ctrl], 'a')
                logger.info("Performed Ctrl+A operation")
            elif operation == 'undo' or operation == 'z':
                time.sleep(0.01)
                self._press_key_combo([keyboard.Key.ctrl], 'z')
                logger.info("Performed Ctrl+Z operation")
            elif operation == 'redo' or operation == 'y':
                time.sleep(0.01)
                self._press_key_combo([keyboard.Key.ctrl], 'y')
                logger.info("Performed Ctrl+Y operation")

    def handle_drag_actions(self, idx, action):
        """
        Handles drag and drop actions.
        
        Args:
            idx (int): Action index
            action (dict): Action configuration
        """
        agent_url = getattr(self.bot, 'sandbox_agent_url', None)
        
        if action['type'] == 'drag_start':
            abs_x = action['coordinates']['x']
            abs_y = action['coordinates']['y']
            with self.typing_lock:
                if agent_url:
                    self._get_agent_position()
                    sx2, sy2 = self._scale_abs(abs_x, abs_y)
                    self._send_agent_action({"action": "drag_start", "x": sx2, "y": sy2, "duration": 0.1})
                else:
                    self.bot.human_mouse_move(abs_x, abs_y)
                    pyautogui.mouseDown()
            logger.info(f"Started drag at ({abs_x}, {abs_y})")

        elif action['type'] == 'drag_end':
            abs_x = action['coordinates']['x']
            abs_y = action['coordinates']['y']
            with self.typing_lock:
                if agent_url:
                    self._get_agent_position()
                    sx2, sy2 = self._scale_abs(abs_x, abs_y)
                    self._send_agent_action({"action": "drag_end", "x": sx2, "y": sy2, "duration": 0.1})
                else:
                    self.bot.human_mouse_move(abs_x, abs_y)
                    pyautogui.mouseUp()
            logger.info(f"Ended drag at ({abs_x}, {abs_y})")

        elif action['type'] == 'drag_drop':
            start = action.get('from') or {}
            end = action.get('to') or {}
            repeat = action.get('repeat')
            if repeat and repeat.get('mode') == 'drag':
                # Repeating drag: walk the recorded sibling set, dragging each
                # row to the recorded (entity-based) target.
                repeat_key = _repeat_key(repeat)
                state = self.bot.selection_memory.setdefault(repeat_key, {})
                processed = state.setdefault('processed', set())
                info = resolve_repeat(repeat, processed)
                if info is None:
                    logger.info(f"Repeating drag set exhausted for action {idx}")
                    return
                if info.get('missing'):
                    # Never drag the recorded row again - see the click path.
                    logger.error(f"Repeating drag set no longer matches for action "
                                 f"{idx}; skipping instead of re-dragging the "
                                 f"recorded element")
                    return
                processed.add(info['key'])
                start = {'x': int(info['point'][0]), 'y': int(info['point'][1])}
                logger.info(f"Repeating drag: action {idx} -> row '{info['key']}'")
                # The drop target is itself an element: resolve it every pass so
                # a moved target still receives the drop (absolute `to` is backup).
                end = (_resolve_point(repeat.get('to_element'))
                       or repeat.get('to') or end)
            elif not action.get('absolute'):
                # Entity-based drag: re-find BOTH endpoints, so a moved window or
                # reflowed layout still drags the right thing to the right place.
                # `absolute` (shift(R)) and a plain miss keep the recorded coords.
                from_element = action.get('from_element')
                to_element = action.get('to_element')
                resolved_start = _resolve_point(from_element)
                resolved_end = _resolve_point(to_element)
                if ((from_element or to_element) and resolved_start is None
                        and resolved_end is None):
                    # The drag was recorded ELEMENT-based but neither endpoint
                    # re-found its element, so it silently runs on the recorded
                    # ABSOLUTE coordinates (brittle across a moved window).
                    # Log it, with the identity tiers each endpoint offered, so
                    # an unwanted fallback is never mistaken for a recorded-
                    # absolute drag nor for the modifier (Shift(R)+drag) path.
                    logger.warning(
                        "drag_drop action %s: entity endpoints did not resolve "
                        "(from_element=%s tiers=%s, to_element=%s tiers=%s); "
                        "using the recorded absolute coordinates",
                        idx, bool(from_element), _tier_count(from_element),
                        bool(to_element), _tier_count(to_element))
                start = resolved_start or start
                end = resolved_end or end
            start_x, start_y = start.get('x'), start.get('y')
            end_x, end_y = end.get('x'), end.get('y')
            if start_x is None or start_y is None or end_x is None or end_y is None:
                logger.warning(f"drag_drop action {idx} missing coordinates, skipping")
                return
            with self.typing_lock:
                if agent_url:
                    self._get_agent_position()
                    s1x, s1y = self._scale_abs(start_x, start_y)
                    e1x, e1y = self._scale_abs(end_x, end_y)
                    self._send_agent_action({
                        "action": "drag_drop", 
                        "start_x": s1x, "start_y": s1y, 
                        "end_x": e1x, "end_y": e1y,
                        "duration": 0.2
                    })
                else:
                    self.bot.human_mouse_move(start_x, start_y)
                    pyautogui.mouseDown()
                    self.bot.human_mouse_move(end_x, end_y)
                    pyautogui.mouseUp()
            logger.info(f"Performed drag_drop from ({start_x}, {start_y}) to ({end_x}, {end_y})")

        elif action['type'] == 'relative_drag_drop':
            from_dx = action.get('from_dx', 0)
            from_dy = action.get('from_dy', 0)
            to_dx = action.get('to_dx', 0)
            to_dy = action.get('to_dy', 0)
            cur_x, cur_y = pyautogui.position()
            if agent_url:
                # Derive offsets from the RDP cursor, not the host's, and scale
                # the recorded offsets into the session's coordinate space.
                agent_pos = self._get_agent_position()
                if agent_pos is not None:
                    cur_x, cur_y = agent_pos
                from_dx, from_dy = self._scale_abs(from_dx, from_dy)
                to_dx, to_dy = self._scale_abs(to_dx, to_dy)
            abs_from_x = int(cur_x + from_dx)
            abs_from_y = int(cur_y + from_dy)
            abs_to_x = int(cur_x + to_dx)
            abs_to_y = int(cur_y + to_dy)
            with self.typing_lock:
                if agent_url:
                    self._send_agent_action({
                        "action": "drag_drop", 
                        "start_x": abs_from_x, "start_y": abs_from_y,
                        "end_x": abs_to_x, "end_y": abs_to_y,
                        "duration": 0.2
                    })
                else:
                    self.bot.human_mouse_move(abs_from_x, abs_from_y)
                    pyautogui.mouseDown()
                    self.bot.human_mouse_move(abs_to_x, abs_to_y)
                    pyautogui.mouseUp()
            logger.info(f"Performed relative drag from offset ({from_dx},{from_dy}) -> ({abs_from_x},{abs_from_y}) to offset ({to_dx},{to_dy}) -> ({abs_to_x},{abs_to_y})")
