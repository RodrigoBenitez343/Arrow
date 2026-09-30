"""Web-mode LLM node: input dispatch and focused-field writing.

Pins the wiring added for LLM web mode:
- the OCR feeder reads ONLY a picked region (desktop) / a browser screenshot (web),
- page text comes from the chain's shared driver,
- auto-write lands in the browser's focused editable (never the desktop).

If any link breaks, web mode silently falls back to desktop screen behaviour.
"""
import json
import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

from player.multi_sequence.llm_executor_resources import inputs
from player.web import actions as web_actions


@pytest.fixture(autouse=True)
def _stub_consult(monkeypatch):
    """get_input_text imports AI.consult.OllamaClient; stub it so the tests do
    not pull the real client / ollama stack."""
    stub = types.ModuleType("AI.consult")
    stub.OllamaClient = object
    monkeypatch.setitem(sys.modules, "AI.consult", stub)


# ---------------------------------------------------------------------------
# OCR region crop
# ---------------------------------------------------------------------------


def test_crop_region_clips_and_passes_through_invalid():
    img = np.arange(10 * 20 * 3, dtype=np.uint8).reshape(10, 20, 3)
    assert inputs._crop_region(img, None) is img
    assert inputs._crop_region(img, []) is img
    assert inputs._crop_region(img, [1, 2, 3]) is img  # malformed -> untouched
    cropped = inputs._crop_region(img, [2, 1, 5, 4])
    assert cropped.shape == (4, 5, 3)
    assert np.array_equal(cropped, img[1:5, 2:7])


# ---------------------------------------------------------------------------
# get_input_text dispatch
# ---------------------------------------------------------------------------


def test_page_text_reads_the_driver(monkeypatch):
    class Driver:
        def execute_script(self, _script):
            return "page body text"

    monkeypatch.setattr(inputs, "_web_driver", lambda: Driver())
    text, _use = inputs.get_input_text(
        {"inputs": []}, {}, "page_text", 0.5, None, False, web_mode=True
    )
    assert text == "page body text"


def test_web_ocr_routes_to_the_browser_feeder(monkeypatch):
    seen = {}

    def fake_web_ocr(locator, confidence, stop_flag):
        seen["locator"] = locator
        return "web ocr text"

    monkeypatch.setattr(inputs, "_web_ocr_input", fake_web_ocr)
    text, _use = inputs.get_input_text(
        {"inputs": []}, {}, "ocr", 0.5, None, False,
        web_mode=True, web_ocr_locator='{"tag":"input"}',
    )
    assert text == "web ocr text"
    assert seen["locator"] == '{"tag":"input"}'


def test_desktop_ocr_receives_the_picked_region(monkeypatch):
    seen = {}

    def fake_ocr(confidence, stop_flag, region=None):
        seen["region"] = region
        return "desktop ocr text"

    monkeypatch.setattr(inputs, "_ocr_input", fake_ocr)
    text, _use = inputs.get_input_text(
        {"inputs": []}, {}, "ocr", 0.5, None, False,
        web_mode=False, region=[10, 20, 30, 40],
    )
    assert text == "desktop ocr text"
    assert seen["region"] == [10, 20, 30, 40]


# ---------------------------------------------------------------------------
# web OCR: shadow-DOM pick is cropped in-page (WebDriver cannot reach it)
# ---------------------------------------------------------------------------


def test_web_ocr_crops_to_a_shadow_pick(monkeypatch, tmp_path):
    import cv2

    img = np.zeros((100, 200, 3), dtype=np.uint8)
    path = str(tmp_path / "shot.png")
    cv2.imwrite(path, img)

    class Driver:
        def save_screenshot(self, p):
            cv2.imwrite(p, img)
            return True

    # A shadow-DOM pick: shadow_hosts non-empty -> _needs_dom_dispatch is True.
    locator = json.dumps({
        "tag": "div",
        "text": "Contact info",
        "shadow_hosts": [{"tag": "div", "id": "interop-outlet"}],
    })

    monkeypatch.setattr(inputs, "_web_driver", lambda: Driver())
    monkeypatch.setattr(inputs, "_save_web_screenshot", lambda d, el, p: path)
    monkeypatch.setattr(
        web_actions, "deep_element_rect", lambda d, event: [10, 20, 30, 40]
    )

    def _boom(*_a, **_k):
        raise AssertionError("a shadow pick must not use wait_for_element")

    monkeypatch.setattr(web_actions, "wait_for_element", _boom)

    from player.computer_vision import TemplateMatching

    seen = {}

    def fake_ocr(min_confidence, screen_cv, stop_flag):
        seen["shape"] = screen_cv.shape
        return [{"text": "form text", "confidence": 1.0}]

    monkeypatch.setattr(TemplateMatching, "ocr_extract_all_text",
                        staticmethod(fake_ocr))

    assert inputs._web_ocr_input(locator, 0.5, None) == "form text"
    assert seen["shape"] == (40, 30, 3)  # cropped to the shadow box


def test_web_ocr_full_page_when_shadow_target_not_found(monkeypatch, tmp_path):
    """A shadow pick that cannot be re-found must NOT crop to a lookalike
    (e.g. the "0%" progress bar): it falls back to the whole page."""
    import cv2

    img = np.zeros((100, 200, 3), dtype=np.uint8)
    path = str(tmp_path / "shot.png")
    cv2.imwrite(path, img)

    class Driver:
        def save_screenshot(self, p):
            cv2.imwrite(p, img)
            return True

    locator = json.dumps({
        "tag": "div",
        "text": "Contact info",
        "shadow_hosts": [{"tag": "div", "id": "interop-outlet"}],
    })
    monkeypatch.setattr(inputs, "_web_driver", lambda: Driver())
    monkeypatch.setattr(inputs, "_save_web_screenshot", lambda d, el, p: path)
    monkeypatch.setattr(web_actions, "deep_element_rect", lambda d, event: None)

    from player.computer_vision import TemplateMatching

    seen = {}

    def fake_ocr(min_confidence, screen_cv, stop_flag):
        seen["shape"] = screen_cv.shape
        return [{"text": "full page", "confidence": 1.0}]

    monkeypatch.setattr(TemplateMatching, "ocr_extract_all_text",
                        staticmethod(fake_ocr))

    assert inputs._web_ocr_input(locator, 0.5, None) == "full page"
    assert seen["shape"] == (100, 200, 3)  # uncropped


def test_deep_element_rect_passes_record_and_returns_ints():
    from player.web.events import Locator

    calls = {}

    class Driver:
        def execute_script(self, script, selectors, text, tag):
            calls.update(script=script, selectors=selectors, text=text, tag=tag)
            return {"x": 1.9, "y": 2.2, "w": 30.5, "h": 40.0}

    event = types.SimpleNamespace(
        locator=Locator(tag="div", text="Contact info",
                        shadow_hosts=[{"tag": "div"}])
    )
    assert web_actions.deep_element_rect(Driver(), event) == [1, 2, 30, 40]
    assert calls["tag"] == "div" and calls["text"] == "Contact info"


# ---------------------------------------------------------------------------
# type_into_focused
# ---------------------------------------------------------------------------


class _FakeChain:
    def __init__(self, *args, **kwargs):
        self.sent = None

    def send_keys(self, text):
        self.sent = text
        return self

    def perform(self):
        pass


def test_type_into_focused_skips_when_nothing_is_focused(monkeypatch):
    calls = []

    class Driver:
        def execute_script(self, _script, *_a):
            return False  # no editable focused

    monkeypatch.setattr(web_actions, "ActionChains", lambda *a, **k: calls.append(a))
    assert web_actions.type_into_focused(Driver(), "hello") is False
    assert calls == []


def test_type_into_focused_types_into_the_focused_field(monkeypatch):
    made = []

    class Driver:
        def execute_script(self, _script, *_a):
            return True  # an editable is focused

    def _factory(*a, **k):
        chain = _FakeChain()
        made.append(chain)
        return chain

    monkeypatch.setattr(web_actions, "ActionChains", _factory)
    assert web_actions.type_into_focused(Driver(), "hello") is True
    assert made and made[0].sent == "hello"
