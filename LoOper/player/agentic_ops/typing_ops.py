"""
TypingAssistant — Simulates keyboard typing via pyautogui.
Minimal: just performs the physical typing, no LLM text generation.
"""

try:
    import pyautogui
except Exception:
    pyautogui = None


class TypingAssistant:
    """Types text on the desktop using pyautogui."""

    def __init__(self, on_minimize=None, on_restore=None):
        """
        Args:
            on_minimize: Callable to minimize the UI before typing.
            on_restore: Callable to restore the UI after typing.
        """
        self._on_minimize = on_minimize
        self._on_restore = on_restore

    def perform_typing(self, text):
        """Type the given text on the keyboard.

        Args:
            text: The exact string to type.

        Returns:
            True on success, False otherwise.
        """
        if not text or pyautogui is None:
            return False
        if self._on_minimize:
            try:
                self._on_minimize()
            except Exception:
                pass
        try:
            pyautogui.write(text, interval=0.05)
            return True
        except Exception:
            return False
        finally:
            if self._on_restore:
                try:
                    self._on_restore()
                except Exception:
                    pass
