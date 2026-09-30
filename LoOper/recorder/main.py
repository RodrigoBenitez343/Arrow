# main.py - Entry point for the modularized recorder
import mouse
from pynput import keyboard
from .element_recorder import ElementRecorder


def start_recording():
    """Initialize and start the recording system"""
    
    # Initialize recorder
    recorder = ElementRecorder()

    # Callback functions for listeners
    def on_click(event):
        if isinstance(event, mouse.ButtonEvent):
            # Get current mouse position since ButtonEvent doesn't have x,y attributes
            x, y = mouse.get_position()
            if event.event_type == mouse.DOWN:
                recorder.on_mouse_press(x, y, event.button, True)
            else:
                recorder.on_mouse_release(x, y, event.button)

    def on_scroll(event):
        """Handle scroll events from mouse library"""
        if isinstance(event, mouse.WheelEvent):
            # Get current mouse position for scroll event
            x, y = mouse.get_position()
            dy = event.delta  # WheelEvent has delta attribute
            dx = 0  # mouse library typically only provides vertical scroll
            recorder.record_scroll(x, y, dx, dy)

    def on_mouse_event(event):
        """Handle all mouse events from mouse library"""
        if isinstance(event, mouse.ButtonEvent):
            on_click(event)
        elif isinstance(event, mouse.WheelEvent):
            on_scroll(event)
        elif hasattr(mouse, 'MoveEvent') and isinstance(event, mouse.MoveEvent):
            try:
                x = getattr(event, 'x', None)
                y = getattr(event, 'y', None)
                if x is None or y is None:
                    x, y = mouse.get_position()
                recorder.on_mouse_move(x, y)
            except Exception:
                pass

    def on_press(key):
        if key == keyboard.Key.esc:
            recorder.save_sequence()
            mouse.unhook_all()  # Stop mouse listener (includes scroll)
            return False  # Stop keyboard listener
        recorder.handle_keypress(key)

    def on_release(key):
        recorder.handle_keyrelease(key)

    # Display instructions
    print("\n🟢 Recording. Try dragging now!")
    print("🖱 Drag >10px to trigger drag, else click")
    print("🡅 Scroll actions are grouped for accurate replay")
    print("⌨ Any key combo works (Ctrl+Alt+Shift+X, Win+R, F-keys...)")
    print("⏹ ESC to save and exit")

    # Start listeners - use mouse library for all mouse events (better compatibility)
    mouse.hook(on_mouse_event)  # Hook all mouse events including scroll
    
    keyboard_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    keyboard_listener.start()
    keyboard_listener.join()  # Will block until ESC


if __name__ == "__main__":
    start_recording()
