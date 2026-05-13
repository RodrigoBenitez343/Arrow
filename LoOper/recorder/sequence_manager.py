# sequence_manager.py

import json
import time
import os
import hashlib

# Import cache invalidation function
try:
    from ..player.json_cache import invalidate_file, clear_cache
except ImportError:
    # Fallback if import fails
    def invalidate_file(file_path):
        pass
    def clear_cache():
        pass


class SequenceManager:
    """Manages the recording and saving of action sequences"""
    
    def __init__(self):
        self.recorded_actions = []
        self.start_time = time.time()

    def _get_active_app_context(self):
        try:
            import pygetwindow as gw
        except Exception:
            gw = None

        try:
            import psutil
        except Exception:
            psutil = None

        try:
            import win32gui
            import win32process
        except Exception:
            win32gui = None
            win32process = None

        hwnd = None
        title = ""
        rect = {}
        try:
            if gw is not None:
                w = gw.getActiveWindow()
                if w is not None:
                    title = str(getattr(w, "title", "") or "")
                    hwnd = getattr(w, "_hWnd", None)
                    rect = {
                        "left": w.left,
                        "top": w.top,
                        "width": w.width,
                        "height": w.height
                    }
        except Exception:
            pass

        try:
            if hwnd is None and win32gui is not None:
                hwnd = win32gui.GetForegroundWindow()
                title = str(win32gui.GetWindowText(hwnd) or "")
                if not rect:
                     l, t, r, b = win32gui.GetWindowRect(hwnd)
                     rect = {
                        "left": l,
                        "top": t,
                        "width": r - l,
                        "height": b - t
                     }
        except Exception:
            pass

        pid = None
        try:
            if hwnd and win32process is not None:
                _tid, pid = win32process.GetWindowThreadProcessId(int(hwnd))
        except Exception:
            pid = None

        proc_name = ""
        exe = ""
        try:
            if psutil is not None and pid:
                p = psutil.Process(int(pid))
                try:
                    proc_name = str(p.name() or "")
                except Exception:
                    proc_name = ""
                try:
                    exe = str(p.exe() or "")
                except Exception:
                    exe = ""
        except Exception:
            proc_name = ""
            exe = ""

        ctx = {
            "title": title,
            "process_name": proc_name,
            "exe": exe,
            "pid": int(pid) if pid else None,
            "rect": rect
        }
        if not any([ctx.get("title"), ctx.get("process_name"), ctx.get("exe"), ctx.get("pid")]):
            return None
        return ctx

    def _attach_app_context(self, action):
        try:
            if not isinstance(action, dict):
                return action
            if action.get("app_context"):
                return action
            ctx = self._get_active_app_context()
            if not ctx:
                return action
            action["app_context"] = ctx
            return action
        except Exception:
            return action
    
    def add_action(self, action):
        """
        Add an action to the recorded sequence
        
        Args:
            action (dict or list): Action dictionary or list of actions to add
        """
        if isinstance(action, list):
            for a in action:
                self.recorded_actions.append(self._attach_app_context(a))
        elif action is not None:
            self.recorded_actions.append(self._attach_app_context(action))
    
    def save_sequence(self, filename='sequence.json'):
        """
        Save all recorded actions to JSON file with embedded screenshot data.
        Screenshots are converted to base64 and stored inline in the JSON,
        making the sequence file self-contained and portable.
        
        Args:
            filename (str): Name of the output file
        """
        # Embed screenshot images as base64 data before serializing
        images_map = {}  # hash -> base64 data (deduplicated)
        try:
            from ..player.image_utils import read_image_to_base64

            for action in self.recorded_actions:
                if isinstance(action, dict) and action.get('screenshot') and not action.get('screenshot_data'):
                    screenshot_path = action['screenshot']
                    # Resolve relative paths for screenshot files
                    if not os.path.isabs(screenshot_path):
                        base_dir = os.path.dirname(filename) if filename != 'sequence.json' else os.getcwd()
                        candidates = [
                            os.path.join(base_dir, screenshot_path),
                            os.path.join(os.getcwd(), screenshot_path),
                            os.path.join(os.getcwd(), 'sequences', screenshot_path),
                        ]
                        for cand in candidates:
                            if os.path.exists(cand):
                                screenshot_path = cand
                                break
                    
                    if os.path.exists(screenshot_path):
                        b64_data = read_image_to_base64(screenshot_path)
                        if b64_data:
                            action['screenshot_data'] = b64_data
                            print(f"Embedded screenshot {os.path.basename(screenshot_path)} as base64 data")

            # Deduplicate images using content hash
            for action in self.recorded_actions:
                if isinstance(action, dict) and action.get('screenshot_data'):
                    raw_data = action['screenshot_data']
                    data_hash = hashlib.md5(raw_data.encode('utf-8')).hexdigest()
                    if data_hash not in images_map:
                        images_map[data_hash] = raw_data
                    action['screenshot_hash'] = data_hash
                    del action['screenshot_data']  # Remove inline data, replaced by hash reference
                    
        except Exception as e:
            print(f"Warning: Failed to process screenshots: {e}")

        output = {
            'metadata': {
                'created_at': time.strftime("%Y-%m-%d %H:%M:%S"),
                'total_actions': len(self.recorded_actions),
                'duration_sec': round(time.time() - self.start_time, 2),
                'mode': 'desktop_only'
            },
            'actions': self.recorded_actions
        }
        
        # Add deduplicated images map if we have any embedded screenshots
        if images_map:
            output['images'] = images_map
        
        os.makedirs(os.path.dirname(filename) or ".", exist_ok=True)
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(output, f, indent=4)
        
        # Clear entire cache to ensure all chains are updated immediately
        print(f"DEBUG: Clearing entire cache after saving sequence: {filename}")
        clear_cache()
        
        print(f"\n\u2705 Saved to {filename} | {len(self.recorded_actions)} actions")
    
    def get_action_count(self):
        """
        Get the number of recorded actions
        
        Returns:
            int: Number of recorded actions
        """
        return len(self.recorded_actions)
    
    def clear_actions(self):
        """Clear all recorded actions and reset start time"""
        self.recorded_actions = []
        self.start_time = time.time()
