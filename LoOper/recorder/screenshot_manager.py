# screenshot_manager.py
import os
import time
import pyautogui

# Disable PyAutoGUI failsafe to prevent interruption when mouse moves to corner
pyautogui.FAILSAFE = False


class ScreenshotManager:
    """Handles screenshot capture for recorded actions"""
    
    def __init__(self, screenshots_dir='sequences/screenshots'):
        self.screenshots_dir = screenshots_dir
        self.screenshot_count = 0
        
        # Create screenshots directory
        if not os.path.exists(self.screenshots_dir):
            os.makedirs(self.screenshots_dir)
    
    def capture_click_screenshot(self, x, y, region_size=35):
        """
        Capture a screenshot around the click coordinates
        
        Args:
            x (int): X coordinate of click
            y (int): Y coordinate of click
            region_size (int): Size of the screenshot region (default: 35px)
            
        Returns:
            str: Path to saved screenshot or None if failed
        """
        screenshot_path = None
        try:
            left = max(0, x - region_size // 2)
            top = max(0, y - region_size // 2)
            
            screenshot = pyautogui.screenshot(region=(left, top, region_size, region_size))
            
            self.screenshot_count += 1
            screenshot_filename = f"click_{self.screenshot_count}_{int(time.time())}.png"
            screenshot_path = os.path.join(self.screenshots_dir, screenshot_filename)
            screenshot.save(screenshot_path)
            print(f"📸 Screenshot saved to {screenshot_path}")

        except Exception as e:
            print(f"Error taking screenshot: {e}")
            
        return screenshot_path