"""Desktop substrate: OCR label harvest + the Handle actuator."""

import re

from .common import (
    logger,
)


class FormFillerDesktopMixin:
    """Desktop substrate: OCR label harvest + the Handle actuator."""

    def _ff_enumerate_desktop(self, cfg, stop_flag):
        """Harvest candidate field LABELS from the screen via OCR.

        ponytail: no dedicated UI-element detector exists in-repo, so desktop
        enumeration harvests short OCR text lines as candidate field labels and
        the WRITE step grounds each label with Handle (LocateAnything) to get
        its coordinates.  Ceiling: this is best-effort for labeled forms; a
        YOLO + LayoutLMv3 field detector is the upgrade path (see
        utils/layoutlmv3_layer.py).
        """
        try:
            from .....computer_vision import TemplateMatching
        except Exception as exc:
            logger.error("[FORM] Desktop OCR unavailable: %s", exc)
            return []
        try:
            results = TemplateMatching.ocr_extract_all_text(stop_flag=stop_flag) or []
        except Exception as exc:
            logger.error("[FORM] Desktop OCR failed: %s", exc)
            return []
        fields = []
        seen = set()
        for item in results:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text or len(text) > 60:
                continue
            # Drop pure numbers / pure punctuation (values, not labels)
            if re.fullmatch(r"[\W\d_]+", text):
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            fields.append({"id": f"ocr_{len(fields)}", "label": text, "tag": "desktop"})
        return fields

    def _ff_write_desktop(self, field, value, cfg, stop_flag):
        label = field.get("label") or ""
        try:
            api_url = self._resolve_api_url()
            screenshot_b64, img_w, img_h, _ = self._take_screenshot_b64()
            if not screenshot_b64:
                logger.error("[FORM] Desktop write: no screenshot")
                return False
            coords = self._vlm_get_coordinates(
                api_url, screenshot_b64, img_w, img_h, "click",
                f"input field labeled '{label}'", stop_flag,
            )
            if not coords:
                logger.warning("[FORM] Could not ground field '%s'", label)
                return False
            self._execute_action("click", coords, stop_flag)
            ah = getattr(self, "action_handlers", None)
            if ah and hasattr(ah, "handle_type_string_action"):
                try:
                    import pyautogui
                    pyautogui.hotkey("ctrl", "a")  # clear existing content
                except Exception:
                    pass
                ah.handle_type_string_action(0, {
                    "text": value,
                    "force_typing": True,
                    "batch_size": cfg["typing_batch_size"],
                    "batch_delay": cfg["typing_batch_delay"],
                    "char_by_char": False,
                    "keystroke_delay": 0.001,
                }, stop_flag)
                return True
            logger.error("[FORM] Desktop write: no action handler available")
            return False
        except Exception as exc:
            logger.error("[FORM] Desktop write failed for '%s': %s", label, exc)
            return False
