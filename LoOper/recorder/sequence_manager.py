# sequence_manager.py

import json
import time

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
    
    def add_action(self, action):
        """
        Add an action to the recorded sequence
        
        Args:
            action (dict or list): Action dictionary or list of actions to add
        """
        if isinstance(action, list):
            self.recorded_actions.extend(action)
        elif action is not None:
            self.recorded_actions.append(action)
    
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
        
        with open(filename, 'w') as f:
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