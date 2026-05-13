from .config import logger

class ScreenshotCleanup:
    """Centralized utility for managing temporal screenshot cleanup.
    
    Now operates in ephemeral mode: temporal screenshots are kept in memory
    only and never written to disk. All cleanup methods are preserved for
    API compatibility but operate as no-ops.
    """
    
    def __init__(self):
        self.temporal_screenshots = []
        self.cleanup_patterns = [
            'temporal_screen_*.png',
            'llm_vision_*.png'
        ]
    
    def register_temporal_screenshot(self, screenshot_path):
        """Register a temporal screenshot for cleanup (no-op in ephemeral mode)"""
        if screenshot_path and screenshot_path not in self.temporal_screenshots:
            self.temporal_screenshots.append(screenshot_path)
            logger.debug(f"Registered temporal screenshot (ephemeral): {screenshot_path}")
    
    def cleanup_screenshot(self, screenshot_path):
        """Clean up a specific screenshot file (no-op in ephemeral mode)"""
        if screenshot_path and screenshot_path in self.temporal_screenshots:
            self.temporal_screenshots.remove(screenshot_path)
        return True
    
    def cleanup_all_registered(self):
        """Clean up all registered temporal screenshots (no-op in ephemeral mode)"""
        if self.temporal_screenshots:
            logger.debug(f"Clearing {len(self.temporal_screenshots)} temporal screenshot registrations")
        self.temporal_screenshots.clear()
        return 0
    
    def cleanup_old_temporal_screenshots(self, directory="screenshots", max_age_minutes=30):
        """Clean up old temporal screenshots (no-op in ephemeral mode)"""
        return 0
    
    def cleanup_by_prefix(self, directory="screenshots", prefix="temporal_screen_"):
        """Clean up screenshots by prefix pattern (no-op in ephemeral mode)"""
        return 0
    
    def __enter__(self):
        """Context manager entry"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - cleanup all registered screenshots"""
        self.cleanup_all_registered()

# Global instance for easy access
_global_cleanup = ScreenshotCleanup()

def get_cleanup_manager():
    """Get the global screenshot cleanup manager"""
    return _global_cleanup

def register_temporal_screenshot(screenshot_path):
    """Convenience function to register a temporal screenshot"""
    _global_cleanup.register_temporal_screenshot(screenshot_path)

def cleanup_screenshot(screenshot_path):
    """Convenience function to clean up a specific screenshot"""
    return _global_cleanup.cleanup_screenshot(screenshot_path)

def cleanup_all_temporal():
    """Convenience function to clean up all registered temporal screenshots"""
    return _global_cleanup.cleanup_all_registered()

def cleanup_old_screenshots(directory="screenshots", max_age_minutes=30):
    """Convenience function to clean up old temporal screenshots"""
    return _global_cleanup.cleanup_old_temporal_screenshots(directory, max_age_minutes)