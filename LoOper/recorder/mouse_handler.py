# mouse_handler.py
import time
from pynput import mouse


class MouseHandler:
    """Handles mouse events including clicks and drag operations"""
    
    def __init__(self):
        self.drag_start = None
        self.pressed_button = None  # Track which button was pressed
        self.last_click_time = None  # Track timing for double-click detection
        self.last_click_pos = None   # Track position for double-click detection
        self.last_click_button = None # Track button for double-click detection
        self.double_click_threshold = 0.5  # Max time between clicks for double-click (seconds)
        self.double_click_distance = 5     # Max pixel distance for double-click

    def _is_left_button(self, button):
        if button == mouse.Button.left:
            return True
        if isinstance(button, str) and button.lower() == 'left':
            return True
        return False
    
    def handle_mouse_press(self, x, y, button, pressed):
        """
        Handle mouse press events
        
        Args:
            x (int): X coordinate
            y (int): Y coordinate
            button: Mouse button from pynput
            pressed (bool): Whether button is pressed
            
        Returns:
            None: This method only sets up drag tracking
        """
        if pressed and self.pressed_button is None:  # Only start if no other button down
            self.pressed_button = button
            if self._is_left_button(button):
                self.drag_start = {'x': x, 'y': y}
                print(f"📍 Drag start set at ({x}, {y})")

    def handle_mouse_release(self, x, y, button):
        """
        Handle mouse release events
        
        Args:
            x (int): X coordinate
            y (int): Y coordinate
            button: Mouse button from pynput
            
        Returns:
            dict: Action dictionary for the completed mouse operation
        """
        if button == self.pressed_button:
            if self._is_left_button(button) and self.drag_start is not None:
                dx = x - self.drag_start['x']
                dy = y - self.drag_start['y']
                distance = (dx ** 2 + dy ** 2) ** 0.5

                # Only drag if moved more than 10 pixels
                if distance > 10:
                    action = {
                        'type': 'drag_drop',
                        'from': self.drag_start,
                        'to': {'x': x, 'y': y},
                        'timestamp': time.time()
                    }
                    print(f"\n✅ DRAGGED from {self.drag_start} to ({x}, {y}) [dist={distance:.1f}]")
                    self.drag_start = None
                    self.pressed_button = None
                    return action
                else:
                    # Treat as click
                    self.drag_start = None
                    self.pressed_button = None
                    return self._handle_click_with_double_detection(x, y, 'left', button)

            elif not self._is_left_button(button):
                # Handle right/middle clicks as simple clicks
                self.pressed_button = None
                button_name = self._get_button_name(button)
                return self._handle_click_with_double_detection(x, y, button_name, button)

            self.pressed_button = None  # Always reset
        
        return None

    def _get_button_name(self, button):
        """Convert pynput button to string name"""
        if isinstance(button, str):
            return button.lower()
        if button == mouse.Button.left:
            return 'left'
        elif button == mouse.Button.right:
            return 'right'
        elif button == mouse.Button.middle:
            return 'middle'
        else:
            return str(button).replace('Button.', '')
    
    def _handle_click_with_double_detection(self, x, y, button_name, button):
        """
        Handle click with double-click detection
        
        Args:
            x (int): X coordinate
            y (int): Y coordinate
            button_name (str): Button name string
            button: pynput button object
            
        Returns:
            dict: Click or double-click action dictionary
        """
        current_time = time.time()
        
        # Check if this could be a double-click
        if (self.last_click_time is not None and 
            self.last_click_button == button and
            current_time - self.last_click_time <= self.double_click_threshold):
            
            # Check distance between clicks
            if (self.last_click_pos is not None and
                abs(x - self.last_click_pos[0]) <= self.double_click_distance and
                abs(y - self.last_click_pos[1]) <= self.double_click_distance):
                
                # This is a double-click, reset tracking and return double-click action
                self.last_click_time = None
                self.last_click_pos = None
                self.last_click_button = None
                
                return {
                    'type': 'double_click',
                    'button': button_name,
                    'coordinates': {'x': x, 'y': y},
                    'timestamp': current_time,
                    'needs_screenshot': True
                }
        
        # This is a single click, update tracking
        self.last_click_time = current_time
        self.last_click_pos = (x, y)
        self.last_click_button = button
        
        return self._create_click_action(x, y, button_name)
    
    def _create_click_action(self, x, y, button):
        """
        Create a click action dictionary
        
        Args:
            x (int): X coordinate
            y (int): Y coordinate
            button (str): Button name
            
        Returns:
            dict: Click action dictionary
        """
        return {
            'type': 'click',
            'button': button,
            'coordinates': {'x': x, 'y': y},
            'timestamp': time.time(),
            'needs_screenshot': True  # Flag to indicate screenshot is needed
        }
