import os
import time
import glob
from .config import logger

class ScreenshotCleanup:
    """Centralized utility for managing temporal screenshot cleanup"""
    
    def __init__(self):
        self.temporal_screenshots = []
        self.cleanup_patterns = [
            'temporal_screen_*.png',
            'llm_vision_*.png'
        ]
    
    def register_temporal_screenshot(self, screenshot_path):
        """Register a temporal screenshot for cleanup"""
        if screenshot_path and screenshot_path not in self.temporal_screenshots:
            self.temporal_screenshots.append(screenshot_path)
            logger.debug(f"Registered temporal screenshot: {screenshot_path}")
    
    def cleanup_screenshot(self, screenshot_path):
        """Clean up a specific screenshot file"""
        if screenshot_path and os.path.exists(screenshot_path):
            try:
                os.remove(screenshot_path)
                logger.debug(f"Cleaned up screenshot: {screenshot_path}")
                if screenshot_path in self.temporal_screenshots:
                    self.temporal_screenshots.remove(screenshot_path)
                return True
            except Exception as e:
                logger.debug(f"Failed to clean up screenshot {screenshot_path}: {e}")
                return False
        return False
    
    def cleanup_all_registered(self):
        """Clean up all registered temporal screenshots"""
        cleaned_count = 0
        for screenshot_path in self.temporal_screenshots.copy():
            if self.cleanup_screenshot(screenshot_path):
                cleaned_count += 1
        
        if cleaned_count > 0:
            logger.info(f"Cleaned up {cleaned_count} temporal screenshots")
        
        self.temporal_screenshots.clear()
        return cleaned_count
    
    def cleanup_old_temporal_screenshots(self, directory="screenshots", max_age_minutes=30):
        """Clean up old temporal screenshots based on age"""
        if not os.path.exists(directory):
            return 0
        
        current_time = time.time()
        max_age_seconds = max_age_minutes * 60
        cleaned_count = 0
        
        for pattern in self.cleanup_patterns:
            pattern_path = os.path.join(directory, pattern)
            for file_path in glob.glob(pattern_path):
                try:
                    file_age = current_time - os.path.getmtime(file_path)
                    if file_age > max_age_seconds:
                        os.remove(file_path)
                        logger.debug(f"Cleaned up old temporal screenshot: {file_path}")
                        cleaned_count += 1
                except Exception as e:
                    logger.debug(f"Failed to clean up old screenshot {file_path}: {e}")
        
        if cleaned_count > 0:
            logger.info(f"Cleaned up {cleaned_count} old temporal screenshots")
        
        return cleaned_count
    
    def cleanup_by_prefix(self, directory="screenshots", prefix="temporal_screen_"):
        """Clean up screenshots by prefix pattern"""
        if not os.path.exists(directory):
            return 0
        
        pattern = os.path.join(directory, f"{prefix}*.png")
        cleaned_count = 0
        
        for file_path in glob.glob(pattern):
            try:
                os.remove(file_path)
                logger.debug(f"Cleaned up screenshot by prefix: {file_path}")
                cleaned_count += 1
            except Exception as e:
                logger.debug(f"Failed to clean up screenshot {file_path}: {e}")
        
        if cleaned_count > 0:
            logger.info(f"Cleaned up {cleaned_count} screenshots with prefix '{prefix}'")
        
        return cleaned_count
    
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