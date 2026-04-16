# Template Matching API

<cite>
**Referenced Files in This Document**
- [computer_vision.py](file://LoOper/player/computer_vision.py)
- [config.py](file://LoOper/player/config.py)
- [ocr_engine.py](file://LoOper/player/ocr_engine.py)
- [model_vision.py](file://LoOper/player/Computer_vision/model_vision.py)
- [object.py](file://LoOper/player/Computer_vision/object.py)
</cite>

## Table of Contents
1. [Introduction](#introduction)
2. [Project Structure](#project-structure)
3. [Core Components](#core-components)
4. [Architecture Overview](#architecture-overview)
5. [Detailed Component Analysis](#detailed-component-analysis)
6. [Dependency Analysis](#dependency-analysis)
7. [Performance Considerations](#performance-considerations)
8. [Troubleshooting Guide](#troubleshooting-guide)
9. [Conclusion](#conclusion)

## Introduction
This document describes the template matching subsystem used for automated UI interaction. It covers:
- Multiscale template matching with adaptive scaling
- Region-based search around hints
- Screen capture integration and optimization
- Confidence threshold management
- Coordinate calculation and result interpretation
- Guidelines for template preparation, performance tuning, and error handling

The primary class is TemplateMatching, which provides methods for:
- Loading templates efficiently
- Capturing the screen via optimized backends
- Performing multiscale template matching with adaptive strategies
- Performing region-based search around hint coordinates
- Optional OCR-based fallbacks and validation

## Project Structure
The template matching system is primarily implemented in the player module’s computer vision utilities, with supporting OCR and configuration modules.

```mermaid
graph TB
subgraph "Player Module"
TM["TemplateMatching<br/>computer_vision.py"]
CFG["Config<br/>config.py"]
OCR["OCR Engine<br/>ocr_engine.py"]
MV["Vision Pipeline<br/>Computer_vision/model_vision.py"]
OBJ["Object Model<br/>Computer_vision/object.py"]
end
TM --> CFG
TM --> OCR
MV --> OBJ
MV --> CFG
```

**Diagram sources**
- [computer_vision.py:47-606](file://LoOper/player/computer_vision.py#L47-L606)
- [config.py:11-31](file://LoOper/player/config.py#L11-L31)
- [ocr_engine.py:131-345](file://LoOper/player/ocr_engine.py#L131-L345)
- [model_vision.py:310-637](file://LoOper/player/Computer_vision/model_vision.py#L310-L637)
- [object.py:3-167](file://LoOper/player/Computer_vision/object.py#L3-L167)

**Section sources**
- [computer_vision.py:47-606](file://LoOper/player/computer_vision.py#L47-L606)
- [config.py:11-31](file://LoOper/player/config.py#L11-L31)
- [ocr_engine.py:131-345](file://LoOper/player/ocr_engine.py#L131-L345)
- [model_vision.py:310-637](file://LoOper/player/Computer_vision/model_vision.py#L310-L637)
- [object.py:3-167](file://LoOper/player/Computer_vision/object.py#L3-L167)

## Core Components
- TemplateMatching: Central class offering template loading, screen capture, multiscale matching, region-based search, and OCR-based text detection/extraction.
- Config flags: CV2_AVAILABLE and OCR_AVAILABLE gate optional functionality.
- OCR engine: Provides text extraction and element metadata for OCR-based workflows.

Key capabilities:
- Efficient template caching and loading
- Fast screen capture via mss or pyautogui fallback
- Multiscale matching with adaptive scale selection based on prior attempts
- Region-based search with dynamic expansion and boundary checks
- Optional grayscale conversion for robustness
- OCR-based fallbacks and validation

**Section sources**
- [computer_vision.py:47-606](file://LoOper/player/computer_vision.py#L47-L606)
- [config.py:11-31](file://LoOper/player/config.py#L11-L31)
- [ocr_engine.py:131-345](file://LoOper/player/ocr_engine.py#L131-L345)

## Architecture Overview
The template matching pipeline integrates OpenCV-based template matching with optional OCR and region constraints. The system adapts search scales based on historical attempt counts to balance speed and accuracy.

```mermaid
sequenceDiagram
participant Caller as "Caller"
participant TM as "TemplateMatching"
participant CV as "OpenCV"
participant SCR as "Screen Capture"
Caller->>TM : multiscale_template_match(template_path, confidence, scales, grayscale, screen_cv)
TM->>TM : _load_template(template_path)
alt screen_cv not provided
TM->>SCR : capture_screen()
SCR-->>TM : screen_cv
end
TM->>CV : resize/scale template if needed
TM->>CV : convert to grayscale if requested
loop for each scale
TM->>CV : matchTemplate(screen, template)
CV-->>TM : max correlation and location
end
TM-->>Caller : best center coordinates or None
```

**Diagram sources**
- [computer_vision.py:198-332](file://LoOper/player/computer_vision.py#L198-L332)

## Detailed Component Analysis

### TemplateMatching Class
The TemplateMatching class encapsulates all template matching operations. It manages:
- Template cache and loading
- Screen capture with multiple backends
- Multiscale matching with adaptive strategy
- Region-based search with hint coordinates
- OCR-based text detection and extraction

```mermaid
classDiagram
class TemplateMatching {
+clear_template_cache()
+capture_screen() numpy.ndarray
+multiscale_template_match(template_path, confidence, scales, grayscale, screen_cv) tuple
+region_based_search(template_path, hint_x, hint_y, confidence, expand_factor, grayscale, screen_cv) tuple
+ocr_text_detection(target_text, confidence, case_sensitive, screen_cv, stop_flag) dict
+ocr_extract_all_text(min_confidence, screen_cv, stop_flag) list
-_load_template(template_path) numpy.ndarray
-_debug_save_screenshot(screen_cv, prefix)
-_template_attempt_counts
-_adaptive_reset_threshold
}
```

**Diagram sources**
- [computer_vision.py:47-606](file://LoOper/player/computer_vision.py#L47-L606)

#### Method: multiscale_template_match
Purpose:
- Perform multiscale template matching with adaptive scaling based on previous attempts.

Parameters:
- template_path: Path to the template image file.
- confidence: Confidence threshold (0.0 to 1.0).
- scales: Optional list of scale factors to try; defaults to [1.0, 0.9, 1.1].
- grayscale: Boolean to convert template and screen to grayscale.
- screen_cv: Optional pre-captured screen image to avoid extra captures.

Behavior:
- Loads template and optionally converts to grayscale.
- Adapts scale set based on attempt history:
  - First few attempts: prioritize 1.0 scale for speed.
  - Medium attempts: reduced set [1.0, 0.9, 1.1].
  - Many attempts: full provided scales.
- Resets attempt counter after a threshold.
- Evaluates all scales to select the best match across scales.
- Returns center coordinates if best confidence meets threshold; otherwise None.

Notes:
- Sorting scales prioritizes 1.0 to improve responsiveness for frequent retries.
- Early exit was removed to ensure the globally best match is selected.

Example usage references:
- [computer_vision.py:198-332](file://LoOper/player/computer_vision.py#L198-L332)

**Section sources**
- [computer_vision.py:198-332](file://LoOper/player/computer_vision.py#L198-L332)

#### Method: region_based_search
Purpose:
- Search for a template within a region around a hint coordinate.

Parameters:
- template_path: Path to the template image file.
- hint_x, hint_y: Hint coordinates for the search region.
- confidence: Confidence threshold for matching.
- expand_factor: Factor to expand the search region relative to template size.
- grayscale: Boolean to convert to grayscale.
- screen_cv: Optional pre-captured screen region.

Behavior:
- Computes region size based on template dimensions and expand_factor with minimum constraints.
- Validates region fits on screen.
- Uses OpenCV matchTemplate on the cropped region if available and CV2 is enabled.
- Falls back to pyautogui locateCenterOnScreen if OpenCV is unavailable or region is invalid.
- Returns global center coordinates if found; otherwise None.

Example usage references:
- [computer_vision.py:333-451](file://LoOper/player/computer_vision.py#L333-L451)

**Section sources**
- [computer_vision.py:333-451](file://LoOper/player/computer_vision.py#L333-L451)

#### Method: capture_screen
Purpose:
- Capture the screen using the fastest available backend.

Behavior:
- If a sandbox agent URL is configured, requests a screenshot from the remote agent.
- Otherwise, prefers mss for fast capture; falls back to pyautogui.
- Converts to BGR format for OpenCV compatibility.

Example usage references:
- [computer_vision.py:92-178](file://LoOper/player/computer_vision.py#L92-L178)

**Section sources**
- [computer_vision.py:92-178](file://LoOper/player/computer_vision.py#L92-L178)

#### Method: ocr_text_detection and ocr_extract_all_text
Purpose:
- Detect specific text or extract all readable text using OCR.

Parameters:
- target_text/min_confidence: Text to find or minimum confidence threshold.
- case_sensitive: Case sensitivity for matching.
- screen_cv: Optional pre-captured screen.
- stop_flag: Optional callable to abort long-running operations.

Behavior:
- Validates OCR availability via OCR_AVAILABLE.
- Captures screen if not provided.
- Extracts text elements and computes bounding boxes.
- Returns best match by confidence or full list depending on method.

Example usage references:
- [computer_vision.py:452-606](file://LoOper/player/computer_vision.py#L452-L606)
- [ocr_engine.py:131-345](file://LoOper/player/ocr_engine.py#L131-L345)

**Section sources**
- [computer_vision.py:452-606](file://LoOper/player/computer_vision.py#L452-L606)
- [ocr_engine.py:131-345](file://LoOper/player/ocr_engine.py#L131-L345)

### Adaptive Multi-Scale Tracking Mechanism
The system tracks the number of attempts per template and dynamically adjusts the scale set to optimize performance.

```mermaid
flowchart TD
Start(["Start multiscale search"]) --> Load["Load template and screen"]
Load --> Count["Get attempt count for template"]
Count --> Decide{"Attempt count"}
Decide --> |0| Init["Use provided scales"]
Decide --> |1-3| OneOnly["Use [1.0] only"]
Decide --> |4-6| Reduced["Use reduced set [1.0, 0.9, 1.1]"]
Decide --> |>6| Full["Use full provided scales"]
Init --> Sort["Sort scales by proximity to 1.0"]
OneOnly --> Sort
Reduced --> Sort
Full --> Sort
Sort --> Loop["For each scale"]
Loop --> Eval["Resize template and match"]
Eval --> Update["Track best confidence and location"]
Update --> Loop
Loop --> Done(["Return best match if >= confidence"])
```

**Diagram sources**
- [computer_vision.py:198-332](file://LoOper/player/computer_vision.py#L198-L332)

**Section sources**
- [computer_vision.py:198-332](file://LoOper/player/computer_vision.py#L198-L332)

### Region-Based Search Logic
The region-based search constrains the search area around a hint coordinate and validates boundaries.

```mermaid
flowchart TD
RS_Start(["Start region search"]) --> LoadRS["Load template"]
LoadRS --> Compute["Compute region size from template and expand_factor"]
Compute --> Bounds["Clip region to screen bounds"]
Bounds --> Valid{"Region valid?"}
Valid --> |No| RS_None["Return None"]
Valid --> |Yes| Backend{"Backend available?"}
Backend --> |OpenCV| Crop["Crop region from screen"]
Crop --> Gray["Convert to grayscale if requested"]
Gray --> Match["matchTemplate on region"]
Match --> Check{"Confidence >= threshold?"}
Check --> |Yes| Center["Compute global center"]
Check --> |No| RS_None
Center --> RS_End(["Return coordinates"])
Backend --> |Fallback| Fallback["pyautogui locateCenterOnScreen"]
Fallback --> FDone{"Found?"}
FDone --> |Yes| RS_End
FDone --> |No| RS_None
```

**Diagram sources**
- [computer_vision.py:333-451](file://LoOper/player/computer_vision.py#L333-L451)

**Section sources**
- [computer_vision.py:333-451](file://LoOper/player/computer_vision.py#L333-L451)

## Dependency Analysis
TemplateMatching depends on:
- OpenCV/Numpy for image processing and template matching
- mss/pyautogui for fast screen capture
- OCR engine for text-based fallbacks
- Config flags to conditionally enable/disable features

```mermaid
graph LR
TM["TemplateMatching"] --> CV2["OpenCV/Numpy"]
TM --> MSS["mss"]
TM --> PG["pyautogui"]
TM --> OCR["OCR Engine"]
TM --> CFG["Config Flags"]
```

**Diagram sources**
- [computer_vision.py:47-606](file://LoOper/player/computer_vision.py#L47-L606)
- [config.py:11-31](file://LoOper/player/config.py#L11-L31)
- [ocr_engine.py:131-345](file://LoOper/player/ocr_engine.py#L131-L345)

**Section sources**
- [computer_vision.py:47-606](file://LoOper/player/computer_vision.py#L47-L606)
- [config.py:11-31](file://LoOper/player/config.py#L11-L31)
- [ocr_engine.py:131-345](file://LoOper/player/ocr_engine.py#L131-L345)

## Performance Considerations
- Prefer grayscale matching when templates and screens are not color-dependent; reduces computation.
- Use region-based search with appropriate expand_factor to limit search space.
- Leverage multiscale_template_match with default scales to quickly converge on 1.0 scale for frequent retries.
- Cache templates via the built-in cache to avoid repeated disk I/O.
- Use provided screen_cv to avoid redundant captures in tight loops.
- For OCR-based workflows, set min_confidence appropriately to reduce false positives.

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common issues and resolutions:
- OpenCV not available:
  - Symptom: Multiscale matching returns None.
  - Resolution: Install OpenCV and NumPy; verify CV2_AVAILABLE.
- OCR not available:
  - Symptom: OCR methods return None or empty results.
  - Resolution: Ensure PaddleOCR is installed and OCR_AVAILABLE is True.
- Template loading failures:
  - Symptom: Template not found or None returned.
  - Resolution: Verify template_path exists and is readable.
- Region too small:
  - Symptom: Region-based search returns None.
  - Resolution: Increase expand_factor or adjust hint coordinates.
- Low confidence matches:
  - Symptom: Best confidence below threshold.
  - Resolution: Adjust confidence threshold or use grayscale matching.

**Section sources**
- [config.py:11-31](file://LoOper/player/config.py#L11-L31)
- [computer_vision.py:56-84](file://LoOper/player/computer_vision.py#L56-L84)
- [computer_vision.py:333-451](file://LoOper/player/computer_vision.py#L333-L451)

## Conclusion
The TemplateMatching subsystem provides robust, adaptive template matching with region constraints and OCR fallbacks. By leveraging multiscale matching with adaptive scaling, region-based search, and efficient screen capture, it balances accuracy and performance. Proper template preparation, confidence tuning, and awareness of platform-specific constraints ensure reliable automation across diverse UIs.