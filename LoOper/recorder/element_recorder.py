# element_recorder.py
from .screenshot_manager import ScreenshotManager
from .scroll_manager import ScrollManager
from .keyboard_handler import KeyboardHandler
from .mouse_handler import MouseHandler
from .sequence_manager import SequenceManager
from pynput import keyboard
import time


class ElementRecorder:
    """Main recorder class that coordinates all recording functionality"""
    
    def __init__(self, sequence_name="new_sequence", screenshots_dir=None):
        # Initialize all managers
        self.screenshot_manager = ScreenshotManager(screenshots_dir or 'sequences/screenshots')
        self.scroll_manager = ScrollManager()
        self.keyboard_handler = KeyboardHandler()
        self.mouse_handler = MouseHandler()
        self.sequence_manager = SequenceManager()
        self.sequence_name = sequence_name
        self._right_ctrl_move_capture = None
        
        print("Desktop recorder started. Drag, copy, paste now reliable.")

    def flush_current_string(self):
        """Flush any pending typed string"""
        action = self.keyboard_handler.flush_current_string()
        if action:
            self.sequence_manager.add_action(action)

    def record_click(self, x, y, button='left'):
        """
        Record a click action with screenshot
        
        Args:
            x (int): X coordinate
            y (int): Y coordinate
            button (str): Button name
        """
        self.flush_current_string()

        # Capture screenshot (ephemeral - base64 data)
        screenshot_data = self.screenshot_manager.capture_click_screenshot(x, y)

        # Create action with embedded base64 image data
        action = {
            'type': 'click',
            'button': button,
            'coordinates': {'x': x, 'y': y},
            'screenshot_data': screenshot_data,
            'timestamp': self.sequence_manager.start_time
        }
        
        self.sequence_manager.add_action(action)
        print(f"\nClick at ({x}, {y})")

    def record_scroll(self, x, y, dx, dy):
        """
        Record a scroll action
        
        Args:
            x (int): X coordinate
            y (int): Y coordinate
            dx (float): Horizontal scroll delta
            dy (float): Vertical scroll delta
        """
        print(f"📥 Recording scroll: x={x}, y={y}, dx={dx}, dy={dy}")
        action = self.scroll_manager.process_scroll(x, y, dx, dy)
        if action:
            print(f"✅ Scroll action recorded: {action}")
            self.sequence_manager.add_action(action)
        else:
            print("⏳ Scroll added to burst (not yet finalized)")

    def on_mouse_press(self, x, y, button, pressed):
        """Handle mouse press events"""
        self.mouse_handler.handle_mouse_press(x, y, button, pressed)

    def on_mouse_release(self, x, y, button):
        """Handle mouse release events"""
        action = self.mouse_handler.handle_mouse_release(x, y, button)
        if action:
            if action.get('type') == 'click' and action.get('button') == 'left':
                mods = self.keyboard_handler.modifiers or {}
                if mods.get('shift_r'):
                    action['type'] = 'absolute_click'
                    action.pop('needs_screenshot', None)
                elif mods.get('ctrl_l'):
                    action['type'] = 'ctrl_click'
                elif mods.get('shift_l'):
                    action['type'] = 'shift_click'

            # Handle screenshot for click actions
            if action.get('needs_screenshot'):
                action['screenshot_data'] = self.screenshot_manager.capture_click_screenshot(
                    action['coordinates']['x'], 
                    action['coordinates']['y']
                )
                del action['needs_screenshot']  # Remove the flag
                print(f"\n🖱 Click at ({action['coordinates']['x']}, {action['coordinates']['y']})")
            
            self.sequence_manager.add_action(action)

    def on_mouse_move(self, x, y):
        mods = self.keyboard_handler.modifiers or {}
        if mods.get('ctrl_r'):
            if not self._right_ctrl_move_capture:
                self._right_ctrl_move_capture = {'start': (x, y), 'last': (x, y)}
            else:
                self._right_ctrl_move_capture['last'] = (x, y)
        return None

    def handle_keypress(self, key):
        """Handle keyboard press events"""
        actions = self.keyboard_handler.handle_keypress(key)
        if actions:
            self.sequence_manager.add_action(actions)

    def handle_keyrelease(self, key):
        """Handle keyboard release events"""
        self.keyboard_handler.handle_keyrelease(key)
        if key == keyboard.Key.ctrl_r and self._right_ctrl_move_capture:
            start_x, start_y = self._right_ctrl_move_capture.get('start', (None, None))
            last_x, last_y = self._right_ctrl_move_capture.get('last', (None, None))
            self._right_ctrl_move_capture = None
            if last_x is None or last_y is None or start_x is None or start_y is None:
                return
            if int(last_x) != int(start_x) or int(last_y) != int(start_y):
                action = {
                    'type': 'move_to',
                    'coordinates': {'x': int(last_x), 'y': int(last_y)},
                    'timestamp': time.time()
                }
                self.sequence_manager.add_action(action)

    def save_sequence(self):
        """Save all recorded actions to JSON"""
        self.flush_current_string()
        
        # Finalize any ongoing scroll
        final_scroll = self.scroll_manager.finalize_scroll_burst()
        if final_scroll:
            self.sequence_manager.add_action(final_scroll)
        
        # Create filename with sequence name and save to sequences folder
        import os, sys
        base_dir = os.path.dirname(os.path.dirname(__file__))
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        sequences_dir = os.path.join(base_dir, 'sequences')
        os.makedirs(sequences_dir, exist_ok=True)
        filename = os.path.join(sequences_dir, f'{self.sequence_name}.json')
        self.sequence_manager.save_sequence(filename)
