# sequence_manager.py

import json
import time
import os

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
        Save all recorded actions to JSON file
        
        Args:
            filename (str): Name of the output file
        """
        output = {
            'metadata': {
                'created_at': time.strftime("%Y-%m-%d %H:%M:%S"),
                'total_actions': len(self.recorded_actions),
                'duration_sec': round(time.time() - self.start_time, 2),
                'mode': 'desktop_only'
            },
            'actions': self.recorded_actions
        }
        
        os.makedirs(os.path.dirname(filename) or ".", exist_ok=True)
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(output, f, indent=4)
        
        # Clear entire cache to ensure all chains are updated immediately
        print(f"DEBUG: Clearing entire cache after saving sequence: {filename}")
        clear_cache()
        
        print(f"\n✅ Saved to {filename} | {len(self.recorded_actions)} actions")
    
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
