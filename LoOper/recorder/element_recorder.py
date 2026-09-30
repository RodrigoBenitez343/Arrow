# element_recorder.py
from .screenshot_manager import ScreenshotManager
from .scroll_manager import ScrollManager
from .keyboard_handler import KeyboardHandler
from .mouse_handler import MouseHandler
from .sequence_manager import SequenceManager
from pynput import keyboard
import time


def attach_click_offset(desc, x, y):
    """Copy of an element descriptor with the click's relative position in it.

    ``rel_x``/``rel_y`` (0..1) let replay reproduce WHERE inside the element the
    click landed (a specific canvas pixel), not just its center.  Returns the
    original dict when the rect is unusable, so the caller still records the
    element identity.
    """
    if not isinstance(desc, dict):
        return desc
    try:
        left, top, right, bottom = (float(v) for v in desc["rect"])
    except Exception:
        return desc
    if right - left <= 0 or bottom - top <= 0:
        return desc
    out = dict(desc)
    out["rel_x"] = round((float(x) - left) / (right - left), 4)
    out["rel_y"] = round((float(y) - top) / (bottom - top), 4)
    return out


# Actions that can be marked as a repeating element with Insert held.  Plain
# clicks and drags - the explicit escape hatches (absolute_click, relative_*)
# stay as recorded.
_MARKABLE_TYPES = frozenset((
    'click', 'double_click', 'ctrl_click', 'shift_click', 'drag_drop',
))

# Actions the right-Ctrl visual-match marker applies to.  Clicks only - a drag
# carries its own endpoint elements and stays entity-based.
_VISUAL_MATCH_TYPES = frozenset(('click', 'double_click'))


class ElementRecorder:
    """Main recorder class that coordinates all recording functionality"""
    
    def __init__(self, sequence_name="new_sequence", screenshots_dir=None,
                 element_rect_provider=None, element_provider=None):
        # Initialize all managers
        self.screenshot_manager = ScreenshotManager(screenshots_dir or 'sequences/screenshots')
        self.scroll_manager = ScrollManager()
        self.keyboard_handler = KeyboardHandler()
        self.mouse_handler = MouseHandler()
        self.sequence_manager = SequenceManager()
        self.sequence_name = sequence_name
        # Callable returning the hovered element's bbox (l, t, r, b) in screen
        # pixels, or None.  Supplied by the recording overlay (UI Automation);
        # lets the click template be the whole element instead of a 35px patch.
        self._element_rect_provider = element_rect_provider
        # Callable returning the hovered element's full UIA descriptor (identity
        # + ancestor chain + rect), or None.  Stored on each click action so
        # replay can re-find the element directly instead of template matching.
        self._element_provider = element_provider
        # First Insert-marked click/drag, buffered until a sibling mark pairs it
        # into a single repeating action (see _emit_action).
        self._repeat_anchor = None
        # The drag SOURCE element, captured at press time (see on_mouse_press).
        self._drag_from_element = None
        self._right_ctrl_move_capture = None
        self._relative_click_reference = None
        
        print("Desktop recorder started. Drag, copy, paste now reliable.")

    def _element_rect(self):
        """Hovered element bbox (l, t, r, b) from the overlay, or None."""
        provider = self._element_rect_provider
        if provider is None:
            return None
        try:
            return provider()
        except Exception:
            return None

    def _element_descriptor(self, x, y):
        """UIA element descriptor at (x, y) with this click's offset, or None."""
        provider = self._element_provider
        if provider is None:
            return None
        try:
            desc = provider(x, y)
        except Exception:
            return None
        if not isinstance(desc, dict) or not desc.get("rect"):
            return None
        return attach_click_offset(desc, x, y)

    def _attach_drag_elements(self, action):
        """Attach the UIA element at BOTH drag endpoints to the action.

        A drag used to be pure absolute coordinates, so a moved window or a
        reflowed layout dropped in the wrong place.  Storing both endpoints as
        elements lets replay re-find them (the same entity-first path clicks
        use); the absolute ``from``/``to`` stay as the fallback.

        The SOURCE uses the element captured at press time - by release a
        move-drag has already taken it away from that point.
        """
        for point_key, element_key in (('from', 'from_element'),
                                       ('to', 'to_element')):
            if point_key == 'from' and self._drag_from_element is not None:
                action['from_element'] = self._drag_from_element
                continue
            point = action.get(point_key) or {}
            px, py = point.get('x'), point.get('y')
            if px is None or py is None:
                continue
            element = self._element_descriptor(px, py)
            if element:
                action[element_key] = element
        self._drag_from_element = None

    def _marker_active(self):
        """True while the Insert repeating-element marker is held."""
        try:
            return bool(self.keyboard_handler.marker_active())
        except Exception:
            return False

    def visual_marker_active(self):
        """True while the right-Ctrl visual-match marker is held.

        Public: the recording overlay reads this (through the app's provider
        hook) to caption the template-crop preview.
        """
        try:
            return bool(self.keyboard_handler.visual_active())
        except Exception:
            return False

    def _emit_action(self, action, mark_x, mark_y):
        """Record an action, applying the Insert repeating-element marker.

        With Insert held, a click/drag is a repeating-element CANDIDATE: the
        first is buffered, and a second on a sibling row turns the pair into ONE
        action carrying a verified ``repeat`` set (the first is dropped).
        Unmarked, unmarkable or element-less actions are recorded as-is, after
        flushing any pending candidate.
        """
        if (mark_x is not None and mark_y is not None
                and self._marker_active()
                and action.get('type') in _MARKABLE_TYPES):
            desc = self._element_descriptor(mark_x, mark_y)
            if desc is not None and self._pair_or_buffer(action, desc):
                return
        self._flush_repeat_anchor()
        self.sequence_manager.add_action(action)

    def _pair_or_buffer(self, action, desc):
        """Pair ``action`` with a buffered candidate, else buffer it.

        Returns True when the action was consumed (buffered or paired) and must
        not be recorded on its own.
        """
        anchor = self._repeat_anchor
        if anchor is not None:
            try:
                from ..player.uia_locator import sibling_kind_from_pair
            except ImportError:
                # `recorder` is also importable as a top-level package (tests,
                # standalone entry points) where `..player` escapes the root.
                try:
                    from player.uia_locator import sibling_kind_from_pair
                except Exception:
                    sibling_kind_from_pair = None
            except Exception:
                sibling_kind_from_pair = None
            repeat = (sibling_kind_from_pair(anchor['desc'], desc)
                      if sibling_kind_from_pair else None)
            if repeat:
                # The SECOND mark carries the verified set; the first is dropped.
                repeat['rel_x'] = desc.get('rel_x', 0.5)
                repeat['rel_y'] = desc.get('rel_y', 0.5)
                if action.get('type') == 'drag_drop':
                    repeat['mode'] = 'drag'
                    # Entity-based drop target: resolved live at replay so a moved
                    # target still receives the drop.  Absolute `to` stays as the
                    # fallback.
                    repeat['to_element'] = action.get('to_element')
                    repeat['to'] = action.get('to')
                else:
                    repeat['mode'] = 'click'
                paired = dict(action)
                paired['repeat'] = repeat
                self._repeat_anchor = None
                self.sequence_manager.add_action(paired)
                print("\n[MARK] Repeating set defined - replay clicks each sibling row")
                return True
            # Not a sibling pair: flush the old candidate, buffer this one.
            self._flush_repeat_anchor()
        self._repeat_anchor = {'action': action, 'desc': desc}
        print("\n[MARK] Marked a repeating row - Insert+click a sibling row to define the set")
        return True

    def _flush_repeat_anchor(self):
        """Record a buffered candidate that never found its sibling pair."""
        anchor = self._repeat_anchor
        if anchor is None:
            return
        self._repeat_anchor = None
        self.sequence_manager.add_action(anchor['action'])
        print("\n[MARK] Marked click recorded normally (no sibling row paired)")

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
        screenshot_data = self.screenshot_manager.capture_click_screenshot(
            x, y, element_rect=self._element_rect())

        # Create action with embedded base64 image data
        action = {
            'type': 'click',
            'button': button,
            'coordinates': {'x': x, 'y': y},
            'screenshot_data': screenshot_data,
            'timestamp': self.sequence_manager.start_time
        }
        element = self._element_descriptor(x, y)
        if element:
            action['element'] = element
        
        self._emit_action(action, x, y)
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
        self._drag_from_element = None
        if pressed and self.mouse_handler.drag_start is not None:
            # Capture the SOURCE element NOW: by release a move-drag has already
            # taken it away from that point.
            self._drag_from_element = self._element_descriptor(x, y)

    def on_mouse_release(self, x, y, button):
        """Handle mouse release events"""
        action = self.mouse_handler.handle_mouse_release(x, y, button)
        if action:
            # Right Ctrl held marks the click as VISUAL: replay must use the
            # legacy template match, never the recorded UIA element (the
            # element can resolve to a container region whose subtree re-find
            # lands the click in the wrong spot).
            visual_match = (self.visual_marker_active()
                            and action.get('type') in _VISUAL_MATCH_TYPES)
            if action.get('button') == 'left' or action.get('type') == 'drag_drop':
                mods = self.keyboard_handler.modifiers or {}
                if visual_match:
                    action['match'] = 'visual'
                    # The click consumes the hover capture: the marker key also
                    # drives "right Ctrl + move", and moving to the target with
                    # it held must not ALSO record a trailing move_to.
                    self._right_ctrl_move_capture = None
                    print("\n[VISUAL] Click recorded as visual match (UIA element ignored at replay)")
                elif mods.get('alt_l') and action.get('type') == 'click':
                    action['type'] = 'relative_click'
                    action.pop('needs_screenshot', None)
                    # Offset: mouse position at Alt-press (reference) to click target
                    if self._relative_click_reference:
                        ref_x, ref_y = self._relative_click_reference
                        action['dx'] = x - ref_x
                        action['dy'] = y - ref_y
                        self._relative_click_reference = None
                    else:
                        action['dx'] = 0
                        action['dy'] = 0
                    if 'coordinates' in action:
                        del action['coordinates']
                elif mods.get('alt_l') and action.get('type') == 'drag_drop':
                    action['type'] = 'relative_drag_drop'
                    if self._relative_click_reference:
                        ref_x, ref_y = self._relative_click_reference
                        action['from_dx'] = action['from']['x'] - ref_x
                        action['from_dy'] = action['from']['y'] - ref_y
                        action['to_dx'] = action['to']['x'] - ref_x
                        action['to_dy'] = action['to']['y'] - ref_y
                        self._relative_click_reference = None
                    else:
                        action['from_dx'] = 0
                        action['from_dy'] = 0
                        action['to_dx'] = 0
                        action['to_dy'] = 0
                    del action['from']
                    del action['to']
                    print(f"✅ Relative DRAG: from offset ({action['from_dx']},{action['from_dy']}) to ({action['to_dx']},{action['to_dy']})")
                elif mods.get('shift_r') and action.get('type') == 'drag_drop':
                    # Absolute drag: keep it a DRAG (the old code renamed it to
                    # absolute_click, losing the drag entirely) and just skip the
                    # entity resolution.
                    action['absolute'] = True
                elif mods.get('shift_r'):
                    action['type'] = 'absolute_click'
                    action.pop('needs_screenshot', None)
                elif mods.get('ctrl_l'):
                    action['type'] = 'ctrl_click'
                elif mods.get('shift_l'):
                    action['type'] = 'shift_click'

            # Handle screenshot for click actions
            if action.get('needs_screenshot'):
                cx = action['coordinates']['x']
                cy = action['coordinates']['y']
                # A visual-match click keeps the LEGACY cursor crop (element_rect
                # = None): the element-sized template belongs to the element-first
                # path this click is opting out of, and a whole-row crop matches
                # poorly.  The UIA element is NOT stored either - so even an old
                # build replaying the sequence replays it as a plain visual click.
                action['screenshot_data'] = self.screenshot_manager.capture_click_screenshot(
                    cx,
                    cy,
                    element_rect=None if visual_match else self._element_rect()
                )
                if not visual_match:
                    element = self._element_descriptor(cx, cy)
                    if element:
                        action['element'] = element
                del action['needs_screenshot']  # Remove the flag
                print(f"\n🖱 Click at ({cx}, {cy})")
            
            # Entity-based drag: capture BOTH endpoints' elements so replay can
            # re-find them after a move / reflow.
            if action.get('type') == 'drag_drop':
                self._attach_drag_elements(action)

            mark_x = mark_y = None
            if action.get('type') == 'drag_drop' and isinstance(action.get('from'), dict):
                mark_x, mark_y = action['from'].get('x'), action['from'].get('y')
            elif isinstance(action.get('coordinates'), dict):
                mark_x, mark_y = action['coordinates'].get('x'), action['coordinates'].get('y')
            self._emit_action(action, mark_x, mark_y)
            if action.get('type') in ('drag_drop', 'relative_drag_drop'):
                print(f"  → action count: {self.sequence_manager.get_action_count()}")

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
        # Capture mouse position on FIRST Alt press as reference for relative_click offset
        if key == keyboard.Key.alt_l and self._relative_click_reference is None:
            import pyautogui
            x, y = pyautogui.position()
            self._relative_click_reference = (x, y)

    def handle_keyrelease(self, key):
        """Handle keyboard release events"""
        action = self.keyboard_handler.handle_keyrelease(key)
        if action:
            self.sequence_manager.add_action(action)
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
        # Release any keys still held at stop time so playback never starts
        # with stuck modifiers (e.g. user held Ctrl while pressing ESC).
        key_up_events = self.keyboard_handler.flush_pending_key_events()
        if key_up_events:
            self.sequence_manager.add_action(key_up_events)
        self.flush_current_string()
        # A marked click still waiting for its sibling pair is recorded normally.
        self._flush_repeat_anchor()
        # ponytail: 50ms settle so last mouse-release handler finishes add_action
        # before save reads recorded_actions. ESC + mouse-up on same ms race otherwise.
        time.sleep(0.05)
        
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
