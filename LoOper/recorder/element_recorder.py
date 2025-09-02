# element_recorder.py
from .screenshot_manager import ScreenshotManager
from .scroll_manager import ScrollManager
from .keyboard_handler import KeyboardHandler
from .mouse_handler import MouseHandler
from .sequence_manager import SequenceManager


class ElementRecorder:
    """Main recorder class that coordinates all recording functionality"""
    
    def __init__(self, sequence_name="new_sequence"):
        # Initialize all managers
        self.screenshot_manager = ScreenshotManager()
        self.scroll_manager = ScrollManager()
        self.keyboard_handler = KeyboardHandler()
        self.mouse_handler = MouseHandler()
        self.sequence_manager = SequenceManager()
        self.sequence_name = sequence_name
        
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

        # Capture screenshot
        screenshot_path = self.screenshot_manager.capture_click_screenshot(x, y)

        # Create action
        action = {
            'type': 'click',
            'button': button,
            'coordinates': {'x': x, 'y': y},
            'screenshot': screenshot_path,
            'timestamp': self.sequence_manager.start_time
        }
        
        self.sequence_manager.add_action(action)
        print(f"\n🖱 Click at ({x}, {y})")

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
            # Handle screenshot for click actions
            if action.get('needs_screenshot'):
                action['screenshot'] = self.screenshot_manager.capture_click_screenshot(
                    action['coordinates']['x'], 
                    action['coordinates']['y']
                )
                del action['needs_screenshot']  # Remove the flag
                print(f"\n🖱 Click at ({action['coordinates']['x']}, {action['coordinates']['y']})")
            
            self.sequence_manager.add_action(action)

    def handle_keypress(self, key):
        """Handle keyboard press events"""
        actions = self.keyboard_handler.handle_keypress(key)
        if actions:
            self.sequence_manager.add_action(actions)

    def handle_keyrelease(self, key):
        """Handle keyboard release events"""
        self.keyboard_handler.handle_keyrelease(key)

    def save_sequence(self):
        """Save all recorded actions to JSON"""
        self.flush_current_string()
        
        # Finalize any ongoing scroll
        final_scroll = self.scroll_manager.finalize_scroll_burst()
        if final_scroll:
            self.sequence_manager.add_action(final_scroll)
        
        # Create filename with sequence name and save to sequences folder
        import os
        sequences_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'sequences')
        os.makedirs(sequences_dir, exist_ok=True)
        filename = os.path.join(sequences_dir, f'{self.sequence_name}.json')
        self.sequence_manager.save_sequence(filename)