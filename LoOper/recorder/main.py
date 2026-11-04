# main.py - Entry point for the modularized recorder
#
# Global hooks come from pynput (one dependency, Windows and Linux/X11 alike)
# instead of the `mouse` package, which needs root on Linux.
from pynput import keyboard, mouse

from .element_recorder import ElementRecorder


def start_recording():
    """Initialize and start the recording system"""

    # Initialize recorder
    recorder = ElementRecorder()
    state = {"scroll_dx": 0, "scroll_dy": 0}

    def on_click(x, y, button, pressed):
        name = getattr(button, "name", str(button))
        if pressed:
            recorder.on_mouse_press(x, y, name, True)
        else:
            recorder.on_mouse_release(x, y, name)

    def on_scroll(x, y, dx, dy):
        # pynput reports fractional steps; keep the whole-notch shape the
        # recorder's scroll grouping expects.
        state["scroll_dx"] += dx
        state["scroll_dy"] += dy
        step_y = int(state["scroll_dy"]) or (1 if dy > 0 else -1 if dy < 0 else 0)
        step_x = int(state["scroll_dx"])
        if step_y or step_x:
            state["scroll_dx"] -= step_x
            state["scroll_dy"] -= step_y
            recorder.record_scroll(x, y, step_x, step_y)

    def on_move(x, y):
        try:
            recorder.on_mouse_move(x, y)
        except Exception:
            pass

    def on_press(key):
        if key == keyboard.Key.esc:
            recorder.save_sequence()
            mouse_listener.stop()
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

    # Start listeners
    mouse_listener = mouse.Listener(
        on_click=on_click, on_scroll=on_scroll, on_move=on_move)
    mouse_listener.start()

    keyboard_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    keyboard_listener.start()
    keyboard_listener.join()  # Will block until ESC


if __name__ == "__main__":
    start_recording()
