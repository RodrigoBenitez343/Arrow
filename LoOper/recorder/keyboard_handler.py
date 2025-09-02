# keyboard_handler.py
import time
from pynput import keyboard


class KeyboardHandler:
    """Handles keyboard events and modifier key tracking"""
    
    def __init__(self):
        self.modifiers = {'ctrl': False, 'shift': False, 'alt': False}
        self.current_string = ""
    
    def handle_keypress(self, key):
        """
        Handle keyboard press events
        
        Args:
            key: The pressed key from pynput
            
        Returns:
            dict or None: Action dictionary if an action should be recorded, None otherwise
        """
        try:
            # --- Get char and vk safely ---
            key_char = None
            key_vk = None

            if hasattr(key, 'char') and key.char is not None:
                key_char = key.char.lower()
            elif hasattr(key, 'vk'):
                key_vk = key.vk

            # --- Handle modifier keys ---
            if key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
                self.modifiers['ctrl'] = True
                return None
            elif key == keyboard.Key.shift_l or key == keyboard.Key.shift_r:
                self.modifiers['shift'] = True
                return None
            elif key == keyboard.Key.alt_l or key == keyboard.Key.alt_r:
                self.modifiers['alt'] = True
                return None

            # --- Handle Ctrl+X, Ctrl+C, Ctrl+V, Ctrl+A ---
            if self.modifiers['ctrl']:
                detected_key = None

                # Check for ASCII control characters first (most reliable)
                if hasattr(key, 'char') and key.char is not None:
                    ctrl_code = ord(key.char)
                    if ctrl_code == 3:    # Ctrl+C = ASCII 3 (ETX)
                        detected_key = 'c'
                    elif ctrl_code == 24: # Ctrl+X = ASCII 24 (CAN)
                        detected_key = 'x'
                    elif ctrl_code == 22: # Ctrl+V = ASCII 22 (SYN)
                        detected_key = 'v'
                    elif ctrl_code == 1:  # Ctrl+A = ASCII 1 (SOH)
                        detected_key = 'a'

                # Fallback to virtual key codes
                elif key_vk is not None:
                    if key_vk == 67:   # VK_C
                        detected_key = 'c'
                    elif key_vk == 88: # VK_X
                        detected_key = 'x'
                    elif key_vk == 86: # VK_V
                        detected_key = 'v'
                    elif key_vk == 65: # VK_A
                        detected_key = 'a'

                if detected_key:
                    # Flush current string first
                    string_action = self.flush_current_string()
                    
                    action = {
                        'type': 'clipboard',
                        'operation': detected_key,
                        'timestamp': time.time()
                    }

                    if detected_key == 'c':
                        print("\n📋 COPY (Ctrl+C)")
                    elif detected_key == 'v':
                        print("\n📎 PASTE (Ctrl+V)")
                    elif detected_key == 'x':
                        print("\n✂️ CUT (Ctrl+X)")
                    elif detected_key == 'a':
                        print("\n🅰 SELECT ALL (Ctrl+A)")

                    return [string_action, action] if string_action else action

            # --- Regular typing: only printable characters ---
            if hasattr(key, 'char') and key.char is not None:
                char_ord = ord(key.char)
                if char_ord < 32 or char_ord == 127:  # Control characters
                    string_action = self.flush_current_string()
                    action = {
                        'type': 'keystroke',
                        'key': str(key).replace('Key.', ''),
                        'timestamp': time.time()
                    }
                    print(f"⌨ Control Key: {action['key']}")
                    return [string_action, action] if string_action else action

                # Safe printable character
                self.current_string += key.char
                return None
            else:
                string_action = self.flush_current_string()
                key_name = str(key).replace('Key.', '')
                action = {
                    'type': 'keystroke',
                    'key': key_name,
                    'timestamp': time.time()
                }
                print(f"⌨ Key: {key_name}")
                return [string_action, action] if string_action else action

        except Exception as e:
            print(f"Error in handle_keypress: {e}")
            return None

    def handle_keyrelease(self, key):
        """
        Handle keyboard release events
        
        Args:
            key: The released key from pynput
        """
        if key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            self.modifiers['ctrl'] = False
        elif key in (keyboard.Key.shift_l, keyboard.Key.shift_r):
            self.modifiers['shift'] = False
        elif key in (keyboard.Key.alt_l, keyboard.Key.alt_r):
            self.modifiers['alt'] = False

    def flush_current_string(self):
        """
        Flush the current string buffer and create a type action
        
        Returns:
            dict or None: Type action if there was text to flush, None otherwise
        """
        if self.current_string:
            action = {
                'type': 'type_string',
                'text': self.current_string,
                'timestamp': time.time()
            }
            print(f"🔤 Typed: '{self.current_string}'")
            self.current_string = ""
            return action
        return None