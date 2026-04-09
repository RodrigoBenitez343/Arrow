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
from .config import logger, CV2_AVAILABLE
from .computer_vision import TemplateMatching
from .screenshot_cleanup import register_temporal_screenshot, cleanup_screenshot

if CV2_AVAILABLE:
    import cv2
    import numpy as np


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
                ok = response.status == 200
            if not ok:
                raise RuntimeError(f"Agent HTTP status {getattr(response, 'status', None)}")
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
                        return Image.open(io.BytesIO(img_data)).convert("RGB")
            except Exception as e:
                logger.error(f"Agent screenshot failed: {e}")
                raise
        return pyautogui.screenshot()

    def _tap_key(self, key):
        """Helper to press and release a key with minimal delay."""
        agent_url = getattr(self.bot, 'sandbox_agent_url', None)
        key_str = key.name if hasattr(key, 'name') else str(key)
        
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

        if not action.get('screenshot'):
            logger.error(f"Action {idx} is a click without a screenshot. Aborting playback.")
            raise Exception(f"Action {idx} is a click without a screenshot.")

        screenshot_path = action['screenshot']
        # Resolve screenshot path robustly across common project layouts
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
        element_key = os.path.abspath(screenshot_path)
        stop_on_all = bool(action.get('stop_when_all_clicked', True))
        state = self.bot.selection_memory.get(element_key, {})
        if stop_on_all and state.get('all_clicked'):
            action['all_clicked'] = True
            return True

        target_coords = None
        screenshot_found = False
        
        # Get retry configuration from action
        retry_attempts = action.get('fallback_retry_attempts', 3)
        retry_delay = action.get('fallback_retry_delay', 0.05)  # Reduced from 0.3

        # Capture single screenshot for all search attempts to improve efficiency
        current_screen_cv = None
        temporal_screenshot_path = None
        current_screen_pil = None
        try:
            current_screen_pil = self._get_screen_pil()
            if CV2_AVAILABLE:
                # Capture once and convert for OpenCV operations
                screen_np = np.array(current_screen_pil)
                if len(screen_np.shape) == 3 and screen_np.shape[2] == 4:
                    screen_np = cv2.cvtColor(screen_np, cv2.COLOR_RGBA2RGB)
                current_screen_cv = cv2.cvtColor(screen_np, cv2.COLOR_RGB2BGR)
                
                # Save temporal screenshot for debugging
                temporal_screenshot_path = f"temporal_screen_{idx}_{int(time.time())}.png"
                current_screen_pil.save(temporal_screenshot_path)
                # Register for automatic cleanup
                register_temporal_screenshot(temporal_screenshot_path)
                logger.debug(f"Single screenshot captured and converted for all search methods")
            else:
                # Fallback: capture screenshot for pyautogui methods
                temporal_screenshot_path = f"temporal_screen_{idx}_{int(time.time())}.png"
                current_screen_pil.save(temporal_screenshot_path)
                # Register for automatic cleanup
                register_temporal_screenshot(temporal_screenshot_path)
                logger.debug(f"Temporal screenshot captured for pyautogui methods")
        except Exception as e:
            logger.debug(f"Failed to capture screenshot: {e}")

        for attempt in range(retry_attempts):
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
                                    screenshot_path,
                                    current_screen_pil,
                                    confidence=strategy['confidence'],
                                    grayscale=strategy['grayscale']
                                ))
                            else:
                                boxes = list(pyautogui.locateAllOnScreen(
                                    screenshot_path,
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
                                element_state = self.bot.selection_memory.get(element_key, {})
                                last_instance_count = element_state.get('instance_count', 0)
                                last_clicked_index = element_state.get('last_index', -1)
                                last_coords = element_state.get('last_coords')
                                clicked_coords = element_state.get('clicked_coords', [])
                                clicked_indices = element_state.get('clicked_indices', [])
                                prev_radius = element_state.get('cluster_radius')
                                current_instance_count = len(centers)

                                # Compute relative spacing to derive a dynamic cluster radius
                                def dist(a, b):
                                    return math.hypot(a[0] - b[0], a[1] - b[1])

                                nn_dists = []
                                try:
                                    for i, c in enumerate(centers):
                                        dlist = [dist(c, centers[j]) for j in range(len(centers)) if j != i]
                                        if dlist:
                                            nn_dists.append(min(dlist))
                                except Exception:
                                    nn_dists = []

                                spacing = sorted(nn_dists)[len(nn_dists)//2] if nn_dists else 12.0
                                cluster_radius = max(5.0, spacing * 0.35)

                                # If instance count changed significantly, prune clicked history to avoid mismatch
                                if last_instance_count != current_instance_count:
                                    if prev_radius:
                                        filtered = []
                                        for pc in clicked_coords:
                                            for c in centers:
                                                if dist(pc, c) <= prev_radius:
                                                    filtered.append(pc)
                                                    break
                                        clicked_coords = filtered
                                        # rebuild clicked_indices by matching filtered coords to nearest centers
                                        new_clicked_indices = []
                                        for pc in clicked_coords:
                                            dists = [dist(pc, c) for c in centers]
                                            if dists:
                                                new_clicked_indices.append(int(dists.index(min(dists))))
                                        clicked_indices = new_clicked_indices
                                    else:
                                        clicked_coords = []
                                        clicked_indices = []

                                hint_x = action.get('x')
                                hint_y = action.get('y')

                                # Build list of unclicked centers based on dynamic radius
                                def is_clicked(c):
                                    for pc in clicked_coords:
                                        if dist(c, pc) <= cluster_radius:
                                            return True
                                    return False

                                unclicked = [(i, centers[i]) for i in range(len(centers)) if not is_clicked(centers[i])]
                                if stop_on_all and not unclicked and clicked_coords:
                                    total_instances = current_instance_count
                                    visited_count = len(clicked_coords)
                                    self.bot.selection_memory[element_key] = {
                                        'instance_count': current_instance_count,
                                        'last_index': last_clicked_index,
                                        'last_coords': last_coords,
                                        'clicked_coords': clicked_coords,
                                        'clicked_indices': clicked_indices,
                                        'cluster_radius': cluster_radius,
                                        'total_instances': total_instances,
                                        'visited_count': visited_count,
                                        'all_clicked': visited_count >= total_instances
                                    }
                                    action['all_clicked'] = True
                                    return True

                                # Choose next by nearest neighbor relative to last click or hint
                                chosen_index = 0
                                if last_coords and isinstance(last_coords, (list, tuple)) and len(last_coords) == 2:
                                    if unclicked:
                                        chosen_index = min(
                                            [ (i, dist(c, (last_coords[0], last_coords[1]))) for (i, c) in unclicked ],
                                            key=lambda x: x[1]
                                        )[0]
                                    else:
                                        # All seen; pick overall nearest to last_coords
                                        dists = [dist(centers[i], (last_coords[0], last_coords[1])) for i in range(len(centers))]
                                        chosen_index = int(dists.index(min(dists)))
                                elif isinstance(hint_x, (int, float)) and isinstance(hint_y, (int, float)):
                                    try:
                                        hx = int(hint_x)
                                        hy = int(hint_y)
                                        base = (hx, hy)
                                        if unclicked:
                                            chosen_index = min(
                                                [ (i, dist(c, base)) for (i, c) in unclicked ],
                                                key=lambda x: x[1]
                                            )[0]
                                        else:
                                            dists = [dist(centers[i], base) for i in range(len(centers))]
                                            chosen_index = int(dists.index(min(dists)))
                                    except Exception:
                                        chosen_index = 0
                                else:
                                    # Fallback to previous index bounds if available
                                    chosen_index = max(0, min(last_clicked_index, current_instance_count - 1))

                                # Update memory with dynamic radius and visited path
                                chosen_coords = (centers[chosen_index][0], centers[chosen_index][1])
                                # Deduplicate if within radius of existing clicked points
                                new_clicked = clicked_coords[:]
                                already = False
                                for pc in new_clicked:
                                    if dist(chosen_coords, pc) <= cluster_radius:
                                        already = True
                                        break
                                if not already:
                                    new_clicked.append(chosen_coords)

                                visited_count = len(new_clicked)
                                total_instances = current_instance_count
                                new_clicked_indices = list(set(clicked_indices + [chosen_index]))
                                self.bot.selection_memory[element_key] = {
                                    'instance_count': current_instance_count,
                                    'last_index': chosen_index,
                                    'last_coords': chosen_coords,
                                    'clicked_coords': new_clicked,
                                    'clicked_indices': new_clicked_indices,
                                    'cluster_radius': cluster_radius,
                                    'total_instances': total_instances,
                                    'visited_count': visited_count,
                                    'all_clicked': visited_count >= total_instances
                                }
                                action['all_clicked'] = visited_count >= total_instances

                                location = chosen_coords
                            else:
                                location = None
                        else:
                            # Fallback to single best match if multiple candidates not found
                            if current_screen_pil:
                                try:
                                    box = pyautogui.locate(
                                        screenshot_path,
                                        current_screen_pil,
                                        confidence=strategy['confidence'],
                                        grayscale=strategy['grayscale']
                                    )
                                    location = pyautogui.center(box) if box else None
                                except Exception:
                                    location = None
                            else:
                                location = pyautogui.locateCenterOnScreen(
                                    screenshot_path,
                                    confidence=strategy['confidence'],
                                    grayscale=strategy['grayscale']
                                )
                    
                    elif strategy['method'] == 'multiscale':
                        # Multi-scale template matching with shared screenshot
                        location = self.template_matcher.multiscale_template_match(
                            screenshot_path, 
                            strategy['confidence'],
                            strategy['scales'],
                            strategy['grayscale'],
                            current_screen_cv
                        )
                    
                    elif strategy['method'] == 'region':
                        # Region-based search with original coordinates as hint and shared screenshot
                        location = self.template_matcher.region_based_search(
                            screenshot_path,
                            action['x'],
                            action['y'],
                            strategy['confidence'],
                            strategy['expand_factor'],
                            strategy['grayscale'],
                            current_screen_cv
                        )
                    
                    if location:
                        screen_width, screen_height = pyautogui.size()
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
        
        # Clean up temporal screenshot using the cleanup utility
        if temporal_screenshot_path:
            cleanup_screenshot(temporal_screenshot_path)

        if target_coords:
            button = action.get('button', 'left')
            agent_url = getattr(self.bot, 'sandbox_agent_url', None)
            modifier_key = None
            if action.get('type') == 'ctrl_click':
                modifier_key = keyboard.Key.ctrl_l
            elif action.get('type') == 'shift_click':
                modifier_key = keyboard.Key.shift_l
            
            with self.typing_lock:
                if agent_url:
                    self._send_agent_action({"action": "click", "x": target_coords[0], "y": target_coords[1], "button": button})
                    if action.get('type') == 'double_click':
                        time.sleep(0.1)
                        self._send_agent_action({"action": "click", "x": target_coords[0], "y": target_coords[1], "button": button})
                    logger.info(f"Agent Clicked ({button}) at {target_coords}")
                elif self.vnc_bridge:
                    self.vnc_bridge.click(target_coords[0], target_coords[1], button=1 if button == 'left' else 3)
                    logger.info(f"VNC Clicked ({button}) at {target_coords}")
                else:
                    pressed_modifier = False
                    try:
                        if modifier_key is not None:
                            self.keyboard_controller.press(modifier_key)
                            pressed_modifier = True

                        self.bot.human_mouse_move(target_coords[0], target_coords[1])
                        time.sleep(0.01)
                        if action.get('type') == 'double_click':
                            self.bot.human_click(button=button, double=True)
                            logger.info(f"Double-clicked ({button}) at {target_coords}")
                        else:
                            self.bot.human_click(button=button, double=False)
                            logger.info(f"Clicked ({button}) at {target_coords}")
                    finally:
                        if pressed_modifier:
                            try:
                                self.keyboard_controller.release(modifier_key)
                            except Exception:
                                pass
            
            self.bot.last_click_time = time.time()
            try:
                state = self.bot.selection_memory.get(element_key, {})
                clicked = state.get('clicked_coords', [])
                radius = state.get('cluster_radius', 12.0)
                total_instances = state.get('total_instances', None)

                def dist(a, b):
                    return math.hypot(a[0] - b[0], a[1] - b[1])

                new_clicked = clicked[:]
                already = False
                for pc in new_clicked:
                    if dist((target_coords[0], target_coords[1]), pc) <= radius:
                        already = True
                        break
                if not already:
                    new_clicked.append((target_coords[0], target_coords[1]))

                visited_count = len(new_clicked)
                all_clicked = False
                if isinstance(total_instances, int) and total_instances > 0:
                    all_clicked = visited_count >= total_instances

                self.bot.selection_memory[element_key] = {
                    'instance_count': state.get('instance_count', 0),
                    'last_index': state.get('last_index', -1),
                    'last_coords': (target_coords[0], target_coords[1]),
                    'clicked_coords': new_clicked,
                    'cluster_radius': radius,
                    'total_instances': total_instances,
                    'visited_count': visited_count,
                    'all_clicked': all_clicked
                }
            except Exception:
                pass
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
                self._send_agent_action({"action": "click", "x": int(x), "y": int(y), "button": button})
                self.bot.last_click_time = time.time()
                logger.info(f"Agent Absolute Clicked ({button}) at ({int(x)}, {int(y)})")
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
                    self._send_agent_action({"action": "type", "text": text})
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
                    self._send_agent_action({"action": "drag_start", "x": abs_x, "y": abs_y})
                else:
                    self.bot.human_mouse_move(abs_x, abs_y)
                    pyautogui.mouseDown()
            logger.info(f"Started drag at ({abs_x}, {abs_y})")

        elif action['type'] == 'drag_end':
            abs_x = action['coordinates']['x']
            abs_y = action['coordinates']['y']
            with self.typing_lock:
                if agent_url:
                    self._send_agent_action({"action": "drag_end", "x": abs_x, "y": abs_y})
                else:
                    self.bot.human_mouse_move(abs_x, abs_y)
                    pyautogui.mouseUp()
            logger.info(f"Ended drag at ({abs_x}, {abs_y})")

        elif action['type'] == 'drag_drop':
            start_x, start_y = action['from']['x'], action['from']['y']
            end_x, end_y = action['to']['x'], action['to']['y']
            with self.typing_lock:
                if agent_url:
                    self._send_agent_action({
                        "action": "drag_drop", 
                        "start_x": start_x, "start_y": start_y, 
                        "end_x": end_x, "end_y": end_y
                    })
                else:
                    self.bot.human_mouse_move(start_x, start_y)
                    pyautogui.mouseDown()
                    self.bot.human_mouse_move(end_x, end_y)
                    pyautogui.mouseUp()
            logger.info(f"Performed drag_drop from ({start_x}, {start_y}) to ({end_x}, {end_y})")
