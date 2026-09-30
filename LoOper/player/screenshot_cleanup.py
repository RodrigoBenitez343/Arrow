import glob
import logging
import os
import time
logger = logging.getLogger(__name__)

class ScreenshotCleanup:
    """Centralized utility for managing runtime screenshot cleanup.

    Producers (LLM vision nodes, OCR inputs, LLM conditionals) write full-
    screen screenshots to disk while a node needs them, then must delete
    them once the node is done so they never accumulate in the repo.
    Registration tracks exactly those written files; every cleanup method
    deletes the tracked files from disk (missing files are tolerated so
    repeated cleanup is safe).
    """
    
    def __init__(self):
        self.temporal_screenshots = []
        self.cleanup_patterns = [
            'temporal_screen_*.png',
            'llm_vision_*.png',
            'llm_ocr_*.png'
        ]
    
    def register_temporal_screenshot(self, screenshot_path):
        """Register a screenshot file written to disk for later cleanup"""
        if screenshot_path and screenshot_path not in self.temporal_screenshots:
            self.temporal_screenshots.append(screenshot_path)
            logger.debug(f"Registered temporal screenshot: {screenshot_path}")
    
    def cleanup_screenshot(self, screenshot_path):
        """Delete a specific screenshot file from disk and drop its registration.

        Missing files are skipped silently so cleanup is idempotent.
        """
        if screenshot_path:
            try:
                if os.path.isfile(screenshot_path):
                    os.remove(screenshot_path)
                    logger.debug(f"Deleted screenshot file: {screenshot_path}")
            except OSError as e:
                logger.warning(f"Failed to delete screenshot {screenshot_path}: {e}")
            if screenshot_path in self.temporal_screenshots:
                self.temporal_screenshots.remove(screenshot_path)
        return True
    
    def cleanup_all_registered(self):
        """Delete every registered screenshot file and clear the registry.

        Returns the number of files actually deleted.
        """
        removed = 0
        for path in list(self.temporal_screenshots):
            try:
                if os.path.isfile(path):
                    os.remove(path)
                    removed += 1
                    logger.debug(f"Deleted registered screenshot: {path}")
            except OSError as e:
                logger.warning(f"Failed to delete screenshot {path}: {e}")
        self.temporal_screenshots.clear()
        return removed
    
    @staticmethod
    def _resolve_sweep_dirs(directory):
        """Return the directories to sweep for temporal screenshots.

        When no explicit directory is given, scan the locations every
        runtime producer writes to: LoOper/player/screenshots (LLM nodes,
        OCR inputs) and LoOper/screenshots (GUI node test runs).
        """
        if directory and directory != "screenshots":
            return [directory]
        here = os.path.dirname(os.path.abspath(__file__))
        return [
            os.path.join(here, "screenshots"),
            os.path.join(os.path.dirname(here), "screenshots"),
        ]
    
    def cleanup_old_temporal_screenshots(self, directory="screenshots", max_age_minutes=30):
        """Delete temporal screenshots older than max_age_minutes from disk.

        Returns the number of files deleted.
        """
        removed = 0
        now = time.time()
        for d in self._resolve_sweep_dirs(directory):
            if not os.path.isdir(d):
                continue
            for pattern in self.cleanup_patterns:
                try:
                    for fpath in glob.glob(os.path.join(d, pattern)):
                        try:
                            if now - os.path.getmtime(fpath) > max_age_minutes * 60:
                                os.remove(fpath)
                                removed += 1
                                logger.debug(
                                    f"Deleted old temporal screenshot: {fpath}"
                                )
                        except OSError:
                            pass
                except Exception:
                    pass
        return removed
    
    def cleanup_by_prefix(self, directory="screenshots", prefix="temporal_screen_"):
        """Delete screenshots matching a filename prefix from disk.

        Returns the number of files deleted.
        """
        removed = 0
        for d in self._resolve_sweep_dirs(directory):
            if not os.path.isdir(d):
                continue
            try:
                for fpath in glob.glob(os.path.join(d, f"{prefix}*.png")):
                    try:
                        os.remove(fpath)
                        removed += 1
                        logger.debug(f"Deleted screenshot by prefix: {fpath}")
                    except OSError:
                        pass
            except Exception:
                pass
        return removed
    
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