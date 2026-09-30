# scroll_manager.py
import time

class ScrollManager:
    """Manages scroll burst tracking and finalization"""
    
    def __init__(self):
        self._current_scroll_burst = None
        self.SCROLL_TIMEOUT = 0.3   # Max time between scrolls to stay in burst (seconds)
        self.SCROLL_EPSILON = 0     # Don't ignore any scroll movements
        self.last_scroll_position = 0  # Cumulative scroll position in pixels
    
    def process_scroll(self, x, y, dx, dy):
        """
        Process a scroll event and manage scroll bursts
        
        Args:
            x (int): X coordinate of scroll event
            y (int): Y coordinate of scroll event
            dx (float): Horizontal scroll delta
            dy (float): Vertical scroll delta
            
        Returns:
            dict or None: Finalized scroll action if burst is complete, None otherwise
        """
        now = time.time()
        pixel_delta = int(dy * 50)  # Standard: ~50px per mouse wheel notch

        # Don't filter out any scrolls - capture all scroll events
        # if abs(pixel_delta) < self.SCROLL_EPSILON:
        #     return None

        # Check if we should continue current burst or finalize it
        if (self._current_scroll_burst is not None and
            now - self._current_scroll_burst['last_time'] <= self.SCROLL_TIMEOUT and
            ((pixel_delta > 0) == (self._current_scroll_burst['total_delta'] > 0))):  # Same direction
            # Extend current burst
            self._current_scroll_burst['total_delta'] += pixel_delta
            self._current_scroll_burst['end'] = {'x': x, 'y': y}
            self._current_scroll_burst['last_time'] = now
            self._current_scroll_burst['steps'] += 1
            # Update cumulative scroll position
            self.last_scroll_position += pixel_delta
            self._current_scroll_burst['final_position'] = round(self.last_scroll_position)
            return None

        else:
            # Finalize previous burst if exists
            finalized_action = self.finalize_scroll_burst()

            # Start new burst
            self.last_scroll_position += pixel_delta
            self._current_scroll_burst = {
                'type': 'scroll',
                'total_delta': pixel_delta,
                'start': {'x': x, 'y': y},
                'end': {'x': x, 'y': y},
                'start_time': now,
                'last_time': now,
                'final_position': round(self.last_scroll_position),
                'steps': 1
            }
            
            return finalized_action

    def finalize_scroll_burst(self):
        """
        Finalize and return the current scroll burst as one action
        
        Returns:
            dict or None: Finalized scroll action or None if no burst exists
        """
        if self._current_scroll_burst is None:
            return None

        burst = self._current_scroll_burst
        start_pos = burst['final_position'] - burst['total_delta']

        action = {
            'type': 'scroll',
            'total_delta': burst['total_delta'],
            'direction': 'up' if burst['total_delta'] > 0 else 'down',
            'start': burst['start'],
            'end': burst['end'],
            'start_position': round(start_pos),
            'final_position': burst['final_position'],
            'duration_sec': burst['last_time'] - burst['start_time'],
            'steps': burst['steps'],
            'timestamp': burst['start_time']
        }

        print(f"\n🡅 SCROLLED {action['direction'].upper()}: {action['total_delta']}px "
              f"→ pos={action['final_position']} "
              f"[{action['steps']} notches, {action['duration_sec']:.2f}s]")

        self._current_scroll_burst = None
        return action