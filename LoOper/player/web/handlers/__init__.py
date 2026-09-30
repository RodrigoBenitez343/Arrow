"""Registry of web replay action handlers (one class per action type).

Extend the architecture by adding a handler module + one registration line
here; the engine dispatches every recorded action type through this registry.
"""

from .base import BaseHandler
from .chrome import ChromeHandler
from .focus import FocusHandler, SelectHandler, SubmitHandler
from .keyboard import KeyHandler, TypeHandler
from .navigation import NavigateHandler, ScrollHandler
from .pointer import (
    ClickHandler,
    ContextMenuHandler,
    DblClickHandler,
    DragHandler,
    HoverHandler,
)

HANDLERS: dict[str, BaseHandler] = {
    "navigate": NavigateHandler(),
    "click": ClickHandler(),
    "dblclick": DblClickHandler(),
    "contextmenu": ContextMenuHandler(),
    "hover": HoverHandler(),
    "drag": DragHandler(),
    "focus": FocusHandler(),
    "type": TypeHandler(),
    "key": KeyHandler(),
    "chrome": ChromeHandler(),
    "scroll": ScrollHandler(),
    "select": SelectHandler(),
    "submit": SubmitHandler(),
}

__all__ = [
    "HANDLERS",
    "BaseHandler",
    "NavigateHandler",
    "ScrollHandler",
    "ClickHandler",
    "DblClickHandler",
    "ContextMenuHandler",
    "HoverHandler",
    "DragHandler",
    "KeyHandler",
    "TypeHandler",
    "FocusHandler",
    "SelectHandler",
    "SubmitHandler",
    "ChromeHandler",
]
