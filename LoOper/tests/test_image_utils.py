"""Unit tests for player/image_utils.py — image/base64 conversions, and
player/computer_vision.py TemplateMatching._load_template.
"""

import base64
import io

import pytest
from PIL import Image

from player import image_utils
from player.computer_vision import TemplateMatching


@pytest.fixture
def pil_image():
    img = Image.new("RGB", (8, 8), color=(255, 0, 0))
    return img


# ---------------------------------------------------------------------------
# pil <-> base64
# ---------------------------------------------------------------------------


def test_pil_to_base64_roundtrip(pil_image):
    b64 = image_utils.pil_to_base64(pil_image)
    assert b64.startswith(image_utils.DATA_IMAGE_PREFIX)
    decoded = image_utils.base64_to_pil(b64)
    assert decoded is not None
    assert decoded.size == (8, 8)
    assert decoded.mode == "RGB"


def test_pil_to_base64_none():
    assert image_utils.pil_to_base64(None) is None


def test_base64_to_pil_empty():
    assert image_utils.base64_to_pil("") is None
    assert image_utils.base64_to_pil(None) is None


def test_base64_to_pil_invalid():
    assert image_utils.base64_to_pil("not-base64-!!!") is None


def test_base64_to_pil_strips_prefixes(pil_image):
    b64 = image_utils.pil_to_base64(pil_image)
    raw = b64.split("base64,", 1)[1]
    assert image_utils.base64_to_pil(raw) is not None


def test_base64_to_cv2(pil_image):
    if not image_utils.CV2_AVAILABLE:
        pytest.skip("cv2 not available")
    b64 = image_utils.pil_to_base64(pil_image)
    arr = image_utils.base64_to_cv2(b64)
    assert arr is not None
    assert arr.shape == (8, 8, 3)


# ---------------------------------------------------------------------------
# is_embedded_image
# ---------------------------------------------------------------------------


def test_is_embedded_image():
    assert image_utils.is_embedded_image("data:image/png;base64,AAAA") is True
    assert image_utils.is_embedded_image("x" * 200) is True  # long base64-ish
    assert image_utils.is_embedded_image("short") is False
    assert image_utils.is_embedded_image("") is False
    assert image_utils.is_embedded_image(None) is False
    assert image_utils.is_embedded_image(123) is False


def test_is_embedded_image_long_strings_accepted():
    # base64.b64decode (without validate=True) silently ignores non-alphabet
    # characters, so any sufficiently long string is treated as embedded.
    assert image_utils.is_embedded_image("!" * 200) is True


def test_is_embedded_image_short_data_uri():
    # data URI prefix wins even for short payloads
    assert image_utils.is_embedded_image("data:image/png;base64,AA") is True


# ---------------------------------------------------------------------------
# load_template_from_field
# ---------------------------------------------------------------------------


def test_load_template_from_base64_data(pil_image):
    b64 = image_utils.pil_to_base64(pil_image)
    pil_out, cv2_out = image_utils.load_template_from_field(None, b64)
    assert pil_out is not None
    assert pil_out.size == (8, 8)


def test_load_template_from_file(tmp_path, pil_image):
    path = tmp_path / "tpl.png"
    pil_image.save(path)
    pil_out, cv2_out = image_utils.load_template_from_field(str(path), None)
    assert pil_out is not None
    assert pil_out.size == (8, 8)


def test_load_template_missing_both():
    assert image_utils.load_template_from_field(None, None) == (None, None)


def test_load_template_missing_file():
    assert image_utils.load_template_from_field("C:\\nope.png", None) == (None, None)


def test_load_cv2_from_field(pil_image):
    if not image_utils.CV2_AVAILABLE:
        pytest.skip("cv2 not available")
    b64 = image_utils.pil_to_base64(pil_image)
    arr = image_utils.load_cv2_from_field(None, b64)
    assert arr is not None


def test_read_image_to_base64(tmp_path, pil_image):
    path = tmp_path / "x.png"
    pil_image.save(path)
    b64 = image_utils.read_image_to_base64(str(path))
    assert b64.startswith(image_utils.DATA_IMAGE_PREFIX)


def test_read_image_to_base64_missing():
    assert image_utils.read_image_to_base64("C:\\nope.png") is None


# ---------------------------------------------------------------------------
# TemplateMatching._load_template
# ---------------------------------------------------------------------------


def test_load_template_numpy_passthrough():
    if not image_utils.CV2_AVAILABLE:
        pytest.skip("cv2 not available")
    import numpy as np
    arr = np.zeros((4, 4, 3), dtype=np.uint8)
    loaded = TemplateMatching._load_template(arr)
    assert loaded.shape == (4, 4, 3)


def test_load_template_pil_conversion(pil_image):
    if not image_utils.CV2_AVAILABLE:
        pytest.skip("cv2 not available")
    loaded = TemplateMatching._load_template(pil_image)
    assert loaded is not None
    assert loaded.shape == (8, 8, 3)


def test_load_template_from_file_path(tmp_path, pil_image):
    if not image_utils.CV2_AVAILABLE:
        pytest.skip("cv2 not available")
    path = tmp_path / "t.png"
    pil_image.save(path)
    loaded = TemplateMatching._load_template(str(path))
    assert loaded is not None


def test_load_template_invalid_path_returns_none():
    if not image_utils.CV2_AVAILABLE:
        pytest.skip("cv2 not available")
    assert TemplateMatching._load_template("C:\\missing\\x.png") is None


def test_clear_template_cache():
    TemplateMatching._template_cache["k"] = "v"
    TemplateMatching.clear_template_cache()
    assert TemplateMatching._template_cache == {}
