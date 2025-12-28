from functools import lru_cache
from typing import Any, Dict, List, Optional

from .config import OCR_AVAILABLE, OCR_ENGINE
import os

# Force disable MKLDNN to avoid OneDnnContext errors
# This must be done before importing paddle
os.environ["FLAGS_use_mkldnn"] = "0"
os.environ["FLAGS_enable_mkldnn"] = "0"


def ocr_available() -> bool:
    return bool(OCR_AVAILABLE and OCR_ENGINE)


def ocr_engine() -> Optional[str]:
    return OCR_ENGINE


def _normalize_language_for_paddle(language: str) -> str:
    language = (language or "").strip().lower()
    if language in {"eng", "en", "english"}:
        return "en"
    if language in {"ch", "chi", "zh", "zho", "cn", "chinese"}:
        return "ch"
    return language or "en"


@lru_cache(maxsize=4)
def _get_paddle_ocr(lang: str):
    import os

    # Force disable MKLDNN to avoid OneDnnContext errors
    os.environ["FLAGS_use_mkldnn"] = "0"
    os.environ["FLAGS_enable_mkldnn"] = "0"
    
    # Also try to set flags via paddle API directly if possible
    try:
        import paddle
        if hasattr(paddle, "set_flags"):
            paddle.set_flags({'FLAGS_use_mkldnn': False})
    except ImportError:
        pass
    except Exception:
        pass

    from paddleocr import PaddleOCR

    # Try to pass enable_mkldnn=False if supported, otherwise rely on env vars
    try:
        from .config import logger
        logger.debug(f"Initializing PaddleOCR with lang={lang}")
        ocr = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False, enable_mkldnn=False)
        logger.debug("PaddleOCR initialized successfully")
        return ocr
    except TypeError:
        # Fallback if the installed version doesn't support enable_mkldnn arg
        logger.debug("PaddleOCR initialized with fallback (no enable_mkldnn arg)")
        return PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)


def _ensure_numpy():
    import numpy as np

    return np


def _as_numpy_image(image: Any):
    np = _ensure_numpy()
    if hasattr(image, "convert"):
        arr = np.array(image.convert("RGB"))
        return arr[:, :, ::-1]
    return np.array(image)


def _ensure_color_bgr(image_arr):
    np = _ensure_numpy()
    if image_arr is None:
        return None
    if len(image_arr.shape) == 2:
        return np.stack([image_arr, image_arr, image_arr], axis=-1)
    if len(image_arr.shape) == 3 and image_arr.shape[2] == 4:
        return image_arr[:, :, :3]
    return image_arr


def extract_text_elements(image: Any, min_confidence: float = 0.6, language: str = "eng") -> List[Dict[str, Any]]:
    if not ocr_available():
        return []

    engine = ocr_engine()
    if engine == "paddle":
        np = _ensure_numpy()
        image_arr = _ensure_color_bgr(_as_numpy_image(image))
        if image_arr is None:
            return []

        lang = _normalize_language_for_paddle(language)
        ocr = _get_paddle_ocr(lang)
        elements: List[Dict[str, Any]] = []

        if hasattr(ocr, "ocr"):
            try:
                result = ocr.ocr(image_arr, cls=True)
                if not result:
                    return []
                
                # PaddleOCR result structure: [ [ [ [x,y], ... ], (text, conf) ], ... ]
                # Sometimes it returns a list of lists if multiple images (but we send one)
                # It usually returns [ [line1], [line2], ... ] where line = [box, (text, conf)]
                # If nothing found, it might return [None] or similar
                
                # Normalize result
                if isinstance(result, list) and len(result) > 0:
                    # Handle case where result is [ [lines] ] (batch size 1)
                    if isinstance(result[0], list) and len(result[0]) > 0 and isinstance(result[0][0], list):
                        # It's likely [ [line1, line2] ]
                        # We want the inner list
                        flat_result = result[0]
                    else:
                        flat_result = result
                else:
                    return []

                for line in flat_result:
                    if not line:
                        continue
                    # line structure: [ [[x1,y1],[x2,y2],[x3,y3],[x4,y4]], ("text", 0.99) ]
                    box, (text, score) = line
                    
                    # Calculate center
                    xs = [p[0] for p in box]
                    ys = [p[1] for p in box]
                    center_x = sum(xs) / len(xs)
                    center_y = sum(ys) / len(ys)
                    
                    elements.append({
                        "text": text,
                        "confidence": float(score),
                        "box": box,
                        "center": (center_x, center_y)
                    })
            except Exception as e:
                # Log error but don't crash
                # Use print/logging if possible, or just ignore
                # Since we are in a cached function, logging might be tricky if not imported
                # But we can try
                try:
                    from .config import logger
                    logger.error(f"PaddleOCR internal error: {e}")
                except:
                    pass
                return []
                
        return elements

        if hasattr(ocr, "predict"):
            results = ocr.predict(image_arr)
            if not results:
                return []

            for res in results:
                payload = None
                if hasattr(res, "json"):
                    payload = getattr(res, "json")
                elif isinstance(res, dict):
                    payload = res
                elif hasattr(res, "res"):
                    payload = getattr(res, "res")

                if isinstance(payload, dict) and "res" in payload and isinstance(payload["res"], dict):
                    payload = payload["res"]

                if not isinstance(payload, dict):
                    continue

                rec_texts = payload.get("rec_texts")
                rec_scores = payload.get("rec_scores")
                rec_polys = payload.get("rec_polys") or payload.get("dt_polys")

                if isinstance(rec_texts, list) and rec_texts:
                    if not isinstance(rec_scores, list):
                        rec_scores = [0.0] * len(rec_texts)

                    polys_list = []
                    if rec_polys is not None and hasattr(rec_polys, "tolist"):
                        polys_list = rec_polys.tolist()
                    elif isinstance(rec_polys, list):
                        polys_list = rec_polys

                    for i, text_raw in enumerate(rec_texts):
                        text = (str(text_raw) if text_raw is not None else "").strip()
                        conf = float(rec_scores[i] if i < len(rec_scores) else 0.0)
                        if not text or conf < float(min_confidence):
                            continue

                        poly = polys_list[i] if i < len(polys_list) else None
                        if not poly:
                            continue

                        xs = [p[0] for p in poly] if poly else []
                        ys = [p[1] for p in poly] if poly else []
                        if not xs or not ys:
                            continue

                        x1 = int(max(0, min(xs)))
                        y1 = int(max(0, min(ys)))
                        x2 = int(max(xs))
                        y2 = int(max(ys))
                        w = max(0, x2 - x1)
                        h = max(0, y2 - y1)
                        elements.append(
                            {
                                "text": text,
                                "confidence": conf,
                                "bbox": (x1, y1, w, h),
                                "center": (x1 + w // 2, y1 + h // 2),
                            }
                        )
                    continue

                rec_text = payload.get("rec_text")
                rec_score = payload.get("rec_score")
                if rec_text is None:
                    continue

                text = (str(rec_text) if rec_text is not None else "").strip()
                conf = float(rec_score or 0.0)
                if not text or conf < float(min_confidence):
                    continue

                poly = payload.get("rec_poly") or payload.get("dt_poly")
                if poly is not None and hasattr(poly, "tolist"):
                    poly = poly.tolist()
                if not poly:
                    continue

                xs = [p[0] for p in poly] if poly else []
                ys = [p[1] for p in poly] if poly else []
                if not xs or not ys:
                    continue

                x1 = int(max(0, min(xs)))
                y1 = int(max(0, min(ys)))
                x2 = int(max(xs))
                y2 = int(max(ys))
                w = max(0, x2 - x1)
                h = max(0, y2 - y1)
                elements.append(
                    {
                        "text": text,
                        "confidence": conf,
                        "bbox": (x1, y1, w, h),
                        "center": (x1 + w // 2, y1 + h // 2),
                    }
                )

            return elements

    return []


def extract_text(image: Any, min_confidence: float = 0.0, language: str = "eng") -> str:
    elements = extract_text_elements(image=image, min_confidence=min_confidence, language=language)
    if not elements:
        return ""
    return "\n".join([e["text"] for e in elements if e.get("text")])


def extract_text_elements_from_image_path(image_path: str, min_confidence: float = 0.6, language: str = "eng"):
    try:
        from PIL import Image
    except Exception:
        return []

    try:
        image = Image.open(image_path)
    except Exception:
        return []

    return extract_text_elements(image=image, min_confidence=min_confidence, language=language)
