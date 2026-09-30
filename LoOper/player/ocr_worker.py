"""OCR worker subprocess — paddle inference runs HERE, isolated from the app.

A native paddle crash (WerFault / 0xc0000005 access violation during OCR
inference) used to kill the whole app: paddle runs in-process and a native
fault has no Python exception to catch.  In this worker the crash kills only
this process; the parent OCR caller detects the non-zero exit (or timeout)
and falls back to an empty result — the app survives.

Usage: python ocr_worker.py <image_path> <min_confidence> <language> <out_json>
The JSON result is written to <out_json> (never stdout — paddle's own logs
would pollute it).  Format: {"elements": [{"text", "confidence", "box",
"center"}]}.
"""

import json
import os
import sys

# Make the LoOper package importable when run as a bare script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Force-disable MKLDNN before paddle loads (OneDnnContext native errors).
os.environ["FLAGS_use_mkldnn"] = "0"
os.environ["FLAGS_enable_mkldnn"] = "0"
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

# Load torch FIRST, before any wheel that bundles its own older msvcp140.dll
# (opencv ships one in cv2/, paddle ships one in paddle/libs/).  If one of
# those loads before torch, torch's shm.dll binds to the OLDER runtime and
# dies with WinError 127 ("procedure could not be found") — the exact
# failure seen when the worker imported cv2 before paddle.  Loading torch
# first lets it bind the matching runtime from torch/lib.
import torch  # noqa: F401  (intentional DLL preload)


def _element(text: str, score: float, box) -> dict:
    xs = [p[0] for p in box] if box else []
    ys = [p[1] for p in box] if box else []
    center = (0, 0)
    if xs and ys:
        center = (sum(xs) / len(xs), sum(ys) / len(ys))
    return {"text": str(text), "confidence": float(score), "box": box, "center": center}


def _main() -> None:
    image_path, min_conf, lang, out_path = (
        sys.argv[1], float(sys.argv[2]), sys.argv[3], sys.argv[4],
    )

    import cv2
    image = cv2.imread(image_path)
    if image is None:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"elements": []}, f)
        return

    from player.ocr_engine import _get_paddle_ocr  # reuse the bundled-model loader
    ocr = _get_paddle_ocr(lang)
    result = ocr.ocr(image)
    if not result:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"elements": []}, f)
        return

    # PaddleOCR 2.x: result = [ [ [box4x2], (text, conf) ], ... ] (batch 1).
    # PaddleX dict format: {"rec_texts": [...], "rec_scores": [...], "rec_polys": [...]}.
    flat = result
    if (isinstance(result, list) and len(result) > 0
            and isinstance(result[0], list) and len(result[0]) > 0
            and isinstance(result[0][0], list)):
        flat = result[0]

    elements = []
    for line in flat or []:
        if not line:
            continue
        if isinstance(line, dict):
            rec_texts = line.get("rec_texts") or []
            rec_scores = line.get("rec_scores") or []
            rec_polys = line.get("rec_polys") or []
            for i, text in enumerate(rec_texts):
                if not text:
                    continue
                score = rec_scores[i] if i < len(rec_scores) else 0.0
                if float(score) < min_conf:
                    continue
                poly = rec_polys[i] if i < len(rec_polys) else None
                if poly is None:
                    continue
                box = poly.tolist() if hasattr(poly, "tolist") else poly
                elements.append(_element(text, float(score), box))
            continue
        try:
            box, pair = line[0], line[1]
            text, score = pair[0], float(pair[1])
        except Exception:
            continue
        if not text or score < min_conf:
            continue
        elements.append(_element(text, score, box))

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"elements": elements}, f)


if __name__ == "__main__":
    try:
        _main()
    except Exception as exc:
        # Never crash silently: write the failure so the parent can log it.
        print(f"OCR worker error: {exc}", file=sys.stderr)
        try:
            with open(sys.argv[4], "w", encoding="utf-8") as f:
                json.dump({"elements": [], "error": str(exc)}, f)
        except Exception:
            pass
