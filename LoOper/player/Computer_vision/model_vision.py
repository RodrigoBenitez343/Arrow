import os
import sys

# Fix for Intel OpenMP runtime duplicate error
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

# Ensure DLL load path on Windows
if os.name == 'nt' and hasattr(os, 'add_dll_directory'):
    try:
        if getattr(sys, 'frozen', False):
            meipass = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
            internal_dir = meipass
            if os.path.basename(internal_dir).lower() != '_internal':
                candidate_internal = os.path.join(os.path.dirname(sys.executable), '_internal')
                if os.path.isdir(candidate_internal):
                    internal_dir = candidate_internal

            ort_capi = os.path.join(internal_dir, 'onnxruntime', 'capi')
            if os.path.isdir(ort_capi):
                try:
                    _dll_dir_handles = []
                    _dll_dir_handles.append(os.add_dll_directory(ort_capi))
                    os.environ['PATH'] = ort_capi + os.pathsep + os.environ['PATH']
                except Exception:
                    pass
                try:
                    import ctypes as _ctypes
                    _ctypes.CDLL(os.path.join(ort_capi, 'onnxruntime_providers_shared.dll'))
                    _ctypes.CDLL(os.path.join(ort_capi, 'onnxruntime.dll'))
                except Exception:
                    pass

        import site
        packages = []
        try:
            packages.extend(site.getsitepackages())
        except Exception:
            pass
        try:
            if hasattr(site, 'getusersitepackages'):
                packages.append(site.getusersitepackages())
        except Exception:
            pass
        # Also check current venv specifically
        try:
            venv_path = os.path.dirname(os.path.dirname(sys.executable)) 
            site_pkg = os.path.join(venv_path, 'Lib', 'site-packages')
            if os.path.isdir(site_pkg):
                packages.append(site_pkg)
        except Exception:
            pass
            
        for p in packages:
            torch_lib = os.path.join(p, 'torch', 'lib')
            if os.path.isdir(torch_lib):
                try:
                    os.add_dll_directory(torch_lib)
                    # Also prepend to PATH as a fallback for some DLL loaders
                    os.environ['PATH'] = torch_lib + os.pathsep + os.environ['PATH']
                except Exception:
                    pass
    except Exception:
        pass

from ultralytics import YOLO
import numpy as np
import cv2
import win32gui
import win32con
import win32api
import keyboard
from mss import mss
from PIL import Image, ImageOps
import json
import time
import logging
import multiprocessing
import threading
from concurrent.futures import ThreadPoolExecutor
import base64
import asyncio
import argparse
import gc
import re
import os
import object
from shapely.geometry import Polygon
from huggingface_hub import hf_hub_download
try:
    from player.ocr_engine import extract_text, extract_text_elements
except Exception:
    try:
        from ..ocr_engine import extract_text, extract_text_elements
    except Exception:
        try:
            from LoOper.player.ocr_engine import extract_text, extract_text_elements
        except Exception:
            def extract_text(image, min_confidence=0.0, language="eng"):
                return ""
            def extract_text_elements(image, min_confidence=0.0, language="eng"):
                return []

class StableContextTracker:
    
    def __init__(self, confirm_hits=2, max_misses=4, iou_threshold=0.45, ema_alpha=0.6, ocr_grid=24):
        self.confirm_hits = int(max(1, confirm_hits))
        self.max_misses = int(max(1, max_misses))
        self.iou_threshold = float(iou_threshold)
        self.ema_alpha = float(ema_alpha)
        self.ocr_grid = int(max(4, ocr_grid))
        self._next_id = 1
        self._lock = threading.Lock()
        self._items = {}
        self._ocr_items = {}
        self._last_frame_roi = None

    def _iou(self, a, b):
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        ix1 = max(ax1, bx1)
        iy1 = max(ay1, by1)
        ix2 = min(ax2, bx2)
        iy2 = min(ay2, by2)
        iw = max(0.0, ix2 - ix1)
        ih = max(0.0, iy2 - iy1)
        inter = iw * ih
        if inter <= 0.0:
            return 0.0
        aa = max(1.0, (ax2 - ax1) * (ay2 - ay1))
        ba = max(1.0, (bx2 - bx1) * (by2 - by1))
        return inter / (aa + ba - inter)

    def _ema_coords(self, old_c, new_c):
        a = self.ema_alpha
        return [
            float(old_c[0]) * (1.0 - a) + float(new_c[0]) * a,
            float(old_c[1]) * (1.0 - a) + float(new_c[1]) * a,
            float(old_c[2]) * (1.0 - a) + float(new_c[2]) * a,
            float(old_c[3]) * (1.0 - a) + float(new_c[3]) * a,
        ]

    def _quant_key(self, coords):
        x1, y1, x2, y2 = coords
        cx = int((x1 + x2) / 2.0)
        cy = int((y1 + y2) / 2.0)
        return (int(cx / self.ocr_grid), int(cy / self.ocr_grid))

    def update(self, pruned, now_ts=None):
        now_ts = float(now_ts if now_ts is not None else time.time())
        frame_roi = pruned.get("frame_roi")
        if frame_roi and len(frame_roi) == 4:
            self._last_frame_roi = [float(frame_roi[0]), float(frame_roi[1]), float(frame_roi[2]), float(frame_roi[3])]
        new_items = []
        context_items = pruned.get("context_items") or []
        if context_items:
            for it in context_items:
                c = it.get("coords")
                if not c or len(c) != 4:
                    continue
                kind = str(it.get("kind") or it.get("type") or "unknown")
                label = it.get("field_label")
                if not label:
                    label = it.get("label")
                if not label and kind.lower() == "text":
                    label = it.get("text")
                new_items.append({
                    "kind": kind,
                    "coords": [float(c[0]), float(c[1]), float(c[2]), float(c[3])],
                    "label": str(label or ""),
                    "value_text": str(it.get("value_text") or ""),
                    "filled": bool(it.get("filled")) if "filled" in it else False,
                })
        else:
            for f in pruned.get("fields") or []:
                c = f.get("coords")
                if not c or len(c) != 4:
                    continue
                new_items.append({
                    "kind": str(f.get("type") or "input"),
                    "coords": [float(c[0]), float(c[1]), float(c[2]), float(c[3])],
                    "label": str(f.get("label") or ""),
                    "value_text": str(f.get("value_text") or ""),
                    "filled": bool(f.get("filled")),
                })
            for t in pruned.get("texts") or []:
                c = t.get("coords")
                if not c or len(c) != 4:
                    continue
                new_items.append({
                    "kind": "text",
                    "coords": [float(c[0]), float(c[1]), float(c[2]), float(c[3])],
                    "label": str(t.get("label") or ""),
                    "value_text": "",
                    "filled": False,
                })

        new_ocr = []
        for t in pruned.get("ocr_texts") or []:
            c = t.get("coords")
            if not c or len(c) != 4:
                continue
            c = [float(c[0]), float(c[1]), float(c[2]), float(c[3])]
            new_ocr.append({
                "coords": c,
                "text": str(t.get("text") or ""),
            })

        with self._lock:
            for it in self._items.values():
                it["misses"] += 1

            used_existing = set()
            for n in new_items:
                best_id = None
                best_iou = 0.0
                for sid, s in self._items.items():
                    if sid in used_existing:
                        continue
                    if s.get("kind") != n.get("kind"):
                        continue
                    iou = self._iou(s["coords"], n["coords"])
                    if iou >= self.iou_threshold and iou > best_iou:
                        best_iou = iou
                        best_id = sid
                if best_id is not None:
                    s = self._items[best_id]
                    s["coords"] = self._ema_coords(s["coords"], n["coords"])
                    if n.get("label"):
                        s["label"] = n["label"]
                    if n.get("value_text"):
                        s["value_text"] = n["value_text"]
                    s["filled"] = bool(n.get("filled"))
                    s["hits"] += 1
                    s["misses"] = 0
                    s["last_seen"] = now_ts
                    used_existing.add(best_id)
                else:
                    sid = self._next_id
                    self._next_id += 1
                    self._items[sid] = {
                        "id": sid,
                        "kind": n["kind"],
                        "coords": n["coords"],
                        "label": n.get("label") or "",
                        "value_text": n.get("value_text") or "",
                        "filled": bool(n.get("filled")),
                        "hits": 1,
                        "misses": 0,
                        "last_seen": now_ts,
                    }

            drop_ids = [sid for sid, s in self._items.items() if int(s.get("misses", 0)) >= self.max_misses]
            for sid in drop_ids:
                self._items.pop(sid, None)

            for v in self._ocr_items.values():
                v["misses"] += 1
            for t in new_ocr:
                key = (self._quant_key(t["coords"]), (t.get("text") or "")[:40])
                if key in self._ocr_items:
                    v = self._ocr_items[key]
                    v["coords"] = self._ema_coords(v["coords"], t["coords"])
                    v["hits"] += 1
                    v["misses"] = 0
                    v["last_seen"] = now_ts
                else:
                    self._ocr_items[key] = {
                        "coords": t["coords"],
                        "text": t.get("text") or "",
                        "hits": 1,
                        "misses": 0,
                        "last_seen": now_ts,
                    }
            drop_ocr = [k for k, v in self._ocr_items.items() if int(v.get("misses", 0)) >= self.max_misses]
            for k in drop_ocr:
                self._ocr_items.pop(k, None)

    def snapshot(self):
        with self._lock:
            items = []
            for s in self._items.values():
                if int(s.get("hits", 0)) < self.confirm_hits:
                    continue
                c = s.get("coords")
                if not c:
                    continue
                items.append({
                    "id": int(s.get("id")),
                    "kind": str(s.get("kind") or ""),
                    "coords": [float(c[0]), float(c[1]), float(c[2]), float(c[3])],
                    "label": str(s.get("label") or ""),
                    "value_text": str(s.get("value_text") or ""),
                    "filled": bool(s.get("filled")),
                })
            items.sort(key=lambda it: (int(it["coords"][1]), int(it["coords"][0]), int(it["id"])))

            ocr_items = []
            for v in self._ocr_items.values():
                if int(v.get("hits", 0)) < self.confirm_hits:
                    continue
                c = v.get("coords")
                if not c:
                    continue
                ocr_items.append({
                    "coords": [float(c[0]), float(c[1]), float(c[2]), float(c[3])],
                    "text": str(v.get("text") or ""),
                })
            return {"items": items, "ocr_texts": ocr_items, "frame_roi": self._last_frame_roi}

class Vision:
    
    def __init__(self, structure_model_path):
        # Load Structure/Text Model
        print(f"Loading Structure Model: {structure_model_path}")
        self.structure_model = YOLO(structure_model_path, task="detect")
        
        # Tracking state
        self.tracked_objects = []
        
        # Load Icon Model
        print("Loading Icon Model...")
        try:
            icon_model_path = hf_hub_download(
                repo_id="macpaw-research/yolov11l-ui-elements-detection",
                filename="ui-elements-detection.pt"
            )
            self.icon_model = YOLO(icon_model_path)
        except Exception as e:
            print(f"Error loading icon model: {e}")
            self.icon_model = None

        # Load LayoutLMv3 Layer
        print("Loading LayoutLMv3 Layer...")
        try:
            from layoutlmv3_layer import LayoutLMv3Layer
            self.layout_analyzer = LayoutLMv3Layer()
        except Exception as e:
            print(f"Error loading LayoutLMv3 Layer: {e}")
            self.layout_analyzer = None

        print("Models loaded.")
        
    def find_matching_object(self, coords, label):
        """Find the closest matching object of the same class"""
        min_distance = float('inf')
        matching_object = None
        
        # Coords are [x1, y1, x2, y2]
        center_x = (coords[0] + coords[2]) / 2
        center_y = (coords[1] + coords[3]) / 2
        center = np.array([center_x, center_y])
        
        for obj in self.tracked_objects:
            if obj.get_label() == label:
                obj_coords = obj.get_coords()
                obj_center_x = (obj_coords[0] + obj_coords[2]) / 2
                obj_center_y = (obj_coords[1] + obj_coords[3]) / 2
                obj_center = np.array([obj_center_x, obj_center_y])
                
                distance = np.linalg.norm(center - obj_center)
                
                if distance < min_distance:
                    min_distance = distance
                    matching_object = obj
                    
        # Only match if the centers are reasonably close
        if min_distance > 50: 
            matching_object = None
            
        return matching_object

    def analyze_single_frame(self, monitor_index=1):
        """
        Perform a single pass of vision analysis on the specified monitor.
        Returns a dictionary containing detected objects, text, and interactions.
        """
        logging.getLogger('ultralytics').setLevel(logging.WARNING)
        
        sct = mss()
        monitors = sct.monitors
        if monitor_index >= len(monitors):
            monitor_index = 0 # Fallback
        monitor = monitors[monitor_index]
        
        # Capture screen
        screen = np.array(sct.grab(monitor))
        screen = screen[:, :, :3] # Remove alpha
        result = self.analyze_frame(screen)
        try:
            pruned = self.analyze_pruned_from_result(result, screen)
            save_paths = self.save_pruned(pruned)
            result["pruned_output_path"] = save_paths.get("latest_path")
            result["pruned_timestamp_path"] = save_paths.get("ts_path")
            masked_paths = self.save_masked(pruned, screen)
            result["masked_output_path"] = masked_paths.get("latest_masked_path")
            result["masked_timestamp_path"] = masked_paths.get("masked_ts_path")
        except Exception as e:
            import traceback
            traceback.print_exc()
        return result
    
    def analyze_pruned_from_screen(self, screen: np.ndarray):
        result = self.analyze_frame(screen)
        return self.analyze_pruned_from_result(result, screen)
    
    def analyze_pruned_from_result(self, result, screen: np.ndarray):
        pruned = self.prune_for_llm(result)
        try:
            modal = self.detect_focus_roi(screen) or self.detect_bright_panel(screen)
            if modal:
                pruned["frame_roi"] = modal
                try:
                    roi_pruned = self.detect_elements_in_roi(screen, {"frame_roi": modal})
                    for key in ["inputs", "selects", "checkboxes", "radios", "buttons", "texts"]:
                        pruned[key] = (pruned.get(key) or []) + (roi_pruned.get(key) or [])
                except Exception:
                    pass
        except Exception:
            pass
        pruned = self.compute_fields(pruned, screen)
        return pruned

    def analyze_frame(self, screen: np.ndarray):
        logging.getLogger('ultralytics').setLevel(logging.WARNING)

        if screen is None or not hasattr(screen, "shape"):
            return {"Object": [], "image_path": None}

        if len(screen.shape) == 2:
            screen = cv2.cvtColor(screen, cv2.COLOR_GRAY2BGR)
        elif screen.shape[2] == 4:
            screen = screen[:, :, :3]

        structure_labels = list(self.structure_model.names.values())
        icon_labels = list(self.icon_model.names.values()) if self.icon_model else []
        all_labels = list(set(structure_labels + icon_labels))

        label_objects = {label: "Interaction" for label in all_labels}

        try:
            json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "label_description.json")
            if os.path.exists(json_path):
                with open(json_path, "r") as f:
                    json_data = json.load(f)
                for label in all_labels:
                    if label in json_data:
                        label_objects[label] = json_data[label]
        except Exception as e:
            print(f"Error loading label description: {e}")

        data = {label: [] for label in all_labels}

        # --- Phase 1: Test Time Augmentation (TTA) Generation ---
        # 1. Raw screen
        screens = [screen]
        
        # 2. Grayscale + High Contrast
        gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
        gray_hc = cv2.convertScaleAbs(gray, alpha=1.5, beta=0)
        screen_gray = cv2.cvtColor(gray_hc, cv2.COLOR_GRAY2BGR)
        screens.append(screen_gray)
        
        # 3. Inverted Mask (helps with dark mode forms)
        screen_inv = cv2.bitwise_not(screen)
        screens.append(screen_inv)

        # --- Phase 2: Independent Engine Execution ---
        for s in screens:
            results_structure = self.structure_model(s, conf=0.25)
            for result in results_structure:
                for box in result.boxes:
                    label = self.structure_model.names[int(box.cls)]
                    data[label].append(box.xyxy.tolist()[0])

            if self.icon_model:
                results_icons = self.icon_model(s, conf=0.25)
                for result in results_icons:
                    for box in result.boxes:
                        label = self.icon_model.names[int(box.cls)]
                        if label not in data:
                            data[label] = []
                        data[label].append(box.xyxy.tolist()[0])

        # --- Phase 3: Non-Maximum Suppression (NMS) & Averaging ---
        def calculate_overlap(boxA, boxB):
            # box: [x1, y1, x2, y2]
            xA = max(boxA[0], boxB[0])
            yA = max(boxA[1], boxB[1])
            xB = min(boxA[2], boxB[2])
            yB = min(boxA[3], boxB[3])
            interArea = max(0, xB - xA) * max(0, yB - yA)
            if interArea == 0: return 0
            boxAArea = max(1, (boxA[2] - boxA[0]) * (boxA[3] - boxA[1]))
            boxBArea = max(1, (boxB[2] - boxB[0]) * (boxB[3] - boxB[1]))
            # Use intersection over minimum area to handle nested/shifted boxes better
            return interArea / min(boxAArea, boxBArea)

        def nms_average(boxes, overlap_thresh=0.4):
            if not boxes: return []
            # Group overlapping boxes
            groups = []
            for box in boxes:
                matched = False
                for group in groups:
                    # Check overlap with the first box in the group as representative
                    if calculate_overlap(box, group[0]) > overlap_thresh:
                        group.append(box)
                        matched = True
                        break
                if not matched:
                    groups.append([box])
            
            # Average coordinates for each group
            averaged = []
            for group in groups:
                avg_box = [sum(col)/len(col) for col in zip(*group)]
                averaged.append(avg_box)
            return averaged

        for label in data:
            data[label] = nms_average(data[label])

        # --- Phase 4: Sequential Subtraction (Masking & Re-running) ---
        masked_for_repass = screen.copy()
        has_large_elements = False
        for label in data:
            for box in data[label]:
                area = (box[2]-box[0]) * (box[3]-box[1])
                if area > 10000: # Threshold for "large" element
                    x1, y1, x2, y2 = map(int, box)
                    cv2.rectangle(masked_for_repass, (x1, y1), (x2, y2), (128, 128, 128), -1)
                    has_large_elements = True
                    
        if has_large_elements:
            results_structure_2 = self.structure_model(masked_for_repass, conf=0.20)
            for result in results_structure_2:
                for box in result.boxes:
                    label = self.structure_model.names[int(box.cls)]
                    data[label].append(box.xyxy.tolist()[0])
            
            if self.icon_model:
                results_icons_2 = self.icon_model(masked_for_repass, conf=0.20)
                for result in results_icons_2:
                    for box in result.boxes:
                        label = self.icon_model.names[int(box.cls)]
                        if label not in data:
                            data[label] = []
                        data[label].append(box.xyxy.tolist()[0])
                        
            # Re-apply NMS to merge new findings with old ones
            for label in data:
                data[label] = nms_average(data[label])

        objects = []

        obj_labels = [key for key in label_objects if label_objects[key] == "Object"]

        for label in obj_labels:
            if label in data:
                for coords in data[label]:
                    new_obj = object.Object(label=label, coords=coords)
                    objects.append(new_obj)

        property_labels = [key for key in label_objects if label_objects[key] == "Property"]

        for obj in objects:
            best_property = None
            max_area = 0

            for prop_label in property_labels:
                if prop_label in data:
                    for prop_coords in data[prop_label]:
                        area = self.calculate_intersection(prop_coords, obj.get_coords())
                        if area > max_area:
                            max_area = area
                            best_property = prop_label

            if best_property:
                obj.set_property(best_property)

        for obj in objects:
            obj_coords = obj.get_coords()

            for key, coords_list in data.items():
                if key not in label_objects:
                    continue
                type_ = label_objects[key]

                if type_ == "Interaction":
                    for coords in coords_list:
                        if self.is_box_inside_box(coords, obj_coords):
                            interaction = object.Interaction(label=key, coords=coords, object=obj, interaction_type=1)
                            obj.add_interaction(interaction)

                elif type_ == "Text":
                    for coords in coords_list:
                        if self.is_box_inside_box(coords, obj_coords):
                            try:
                                text_content = self.read_image(screen, coords)
                            except Exception:
                                text_content = ""

                            if text_content and text_content.strip():
                                text_item = object.Text(content=text_content, coords=coords, object=obj)
                                obj.add_text(text_item)

        output_data = {"Object": [], "RawData": data}
        for i, obj in enumerate(objects):
            obj_dict = {
                "index": f"{obj.get_label()}_{i}",
                "label": obj.get_label(),
                "property": obj.get_property(),
                "coords": obj.get_coords(),
                "text_content": [t.get_content() for t in obj.get_text() or []],
                "interactions": obj.interactions_to_list()
            }
            output_data["Object"].append(obj_dict)

        try:
            annotated_img = self.draw_annotations(screen, data, objects)
            timestamp = int(time.time())

            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            screenshots_dir = os.path.join(base_dir, "screenshots")
            if not os.path.exists(screenshots_dir):
                os.makedirs(screenshots_dir, exist_ok=True)

            image_filename = f"yolo_annotation_{timestamp}.png"
            image_path = os.path.join(screenshots_dir, image_filename)
            cv2.imwrite(image_path, annotated_img)
            output_data["image_path"] = image_path
            print(f"Annotated image saved to: {image_path}")
        except Exception as e:
            print(f"Error generating annotated image: {e}")
            output_data["image_path"] = None

        return output_data

    def draw_annotations(self, screen_or_shape, data, objects):
        if isinstance(screen_or_shape, tuple):
            h, w = screen_or_shape[:2]
            display_overlay = np.zeros((h, w, 3), dtype=np.uint8)
        else:
            display_overlay = screen_or_shape.copy()
        
        # Draw all raw data first (Green)
        for label, boxes in data.items():
            for coords in boxes:
                x1, y1, x2, y2 = map(int, coords)
                cv2.rectangle(display_overlay, (x1, y1), (x2, y2), (0, 255, 0), 2)
                cv2.putText(display_overlay, str(label), (x1, max(0, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

        # Draw objects (Blue)
        for obj in objects:
            x1, y1, x2, y2 = map(int, obj.get_coords())
            cv2.rectangle(display_overlay, (x1, y1), (x2, y2), (255, 0, 0), 2)
            cv2.putText(display_overlay, str(obj.get_label()), (x1, max(0, y1 - 20)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 0), 2)
            if obj.get_property():
                cv2.putText(display_overlay, str(obj.get_property()), (x1, max(0, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 0), 2)
            
            # Draw interactions inside objects (Orange)
            for inter in obj.get_interactions() or []:
                ix1, iy1, ix2, iy2 = map(int, inter.get_coords())
                cv2.rectangle(display_overlay, (ix1, iy1), (ix2, iy2), (0, 165, 255), 2)
                cv2.putText(display_overlay, str(inter.get_label()), (ix1, max(0, iy1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 165, 255), 2)
        
        return display_overlay

    def prune_for_llm(self, output_data):
        objects = output_data.get("Object", []) or []
        timestamp = int(time.time())
        pruned = {
            "timestamp": timestamp,
            "image_path": output_data.get("image_path"),
            "frame_roi": None,
            "inputs": [],
            "selects": [],
            "checkboxes": [],
            "radios": [],
            "buttons": [],
            "texts": []
        }
        windows = [o for o in objects if (o.get("label") or "").lower() == "window"]
        top_layer_windows = [o for o in windows if (o.get("property") or "") == "toplayer"]
        def area(c):
            if not c: return 0
            x1,y1,x2,y2 = c
            return max(0, x2-x1) * max(0, y2-y1)
        def wscore(w):
            score = 0
            for inter in w.get("interactions") or []:
                il = (inter.get("label") or "").lower()
                if "input" in il or "textarea" in il or "textfield" in il or "select" in il or "dropdown" in il or "combobox" in il or "text" in il:
                    score += 3
                elif "button" in il or "axlink" in il or "link" in il:
                    score += 1
            return score
        def inside(c, wcoords):
            return self.is_box_inside_box(c, wcoords)
        def center(c):
            return ((c[0] + c[2]) / 2.0, (c[1] + c[3]) / 2.0)
        def inputs_all():
            arr = []
            for o in objects:
                for inter in o.get("interactions") or []:
                    il = (inter.get("label") or "").lower()
                    if "input" in il or "textarea" in il or "textfield" in il or "select" in il or "dropdown" in il or "combobox" in il or "text" in il:
                        arr.append(inter.get("coords"))
            return arr
        def buttons_all():
            arr = []
            for o in objects:
                for inter in o.get("interactions") or []:
                    il = (inter.get("label") or "").lower()
                    if "button" in il or "axlink" in il or "link" in il:
                        arr.append(inter.get("coords"))
            return arr
        def screen_size():
            maxx = 0
            maxy = 0
            for w in top_layer_windows:
                c = w.get("coords") or [0,0,0,0]
                maxx = max(maxx, c[2])
                maxy = max(maxy, c[3])
            return maxx or 1, maxy or 1
        # Input-cluster ROI regardless of window boxes
        all_inputs = inputs_all()
        sx = 1
        sy = 1
        for o in objects:
            c = o.get("coords") or [0,0,0,0]
            sx = max(sx, c[2])
            sy = max(sy, c[3])
            for inter in o.get("interactions") or []:
                ci = inter.get("coords") or [0,0,0,0]
                sx = max(sx, ci[2])
                sy = max(sy, ci[3])
        if all_inputs and not pruned["frame_roi"]:
            mx = sum((c[0] + c[2]) / 2.0 for c in all_inputs) / len(all_inputs)
            my = sum((c[1] + c[3]) / 2.0 for c in all_inputs) / len(all_inputs)
            radius = max(100, int(min(sx, sy) * 0.2))
            cluster = []
            for c in all_inputs:
                cx = (c[0]+c[2]) / 2.0
                cy = (c[1]+c[3]) / 2.0
                d = ((cx - mx)**2 + (cy - my)**2) ** 0.5
                if d <= radius:
                    cluster.append(c)
            if not cluster:
                cluster = all_inputs
            x1 = min(c[0] for c in cluster)
            y1 = min(c[1] for c in cluster)
            x2 = max(c[2] for c in cluster)
            y2 = max(c[3] for c in cluster)
            margin = max(100, int(min(sx, sy) * 0.2))
            x1 = max(0, x1 - margin)
            y1 = max(0, y1 - margin)
            x2 = min(sx, x2 + margin)
            y2 = min(sy, y2 + margin)
            pruned["frame_roi"] = [x1, y1, x2, y2]
        if not pruned["frame_roi"]:
            centers = []
            for o in objects:
                for inter in o.get("interactions") or []:
                    il = (inter.get("label") or "").lower()
                    if "input" in il or "textarea" in il or "textfield" in il or "select" in il or "dropdown" in il or "combobox" in il or "text" in il or "button" in il:
                        cx = (inter["coords"][0] + inter["coords"][2]) / 2.0
                        cy = (inter["coords"][1] + inter["coords"][3]) / 2.0
                        centers.append((cx, cy))
            if centers and top_layer_windows:
                ax = sum(c[0] for c in centers) / len(centers)
                ay = sum(c[1] for c in centers) / len(centers)
                for w in top_layer_windows:
                    if self.is_box_inside_box([ax, ay, ax, ay], w.get("coords")):
                        pruned["frame_roi"] = w.get("coords")
                        break
        # Fallback: if ROI is too large, compute dense input cluster ROI
        try:
            sx, sy = screen_size()
            if pruned["frame_roi"]:
                ra = area(pruned["frame_roi"])
                sa = sx * sy
                if sa and ra / sa > 0.5:
                    inputs = inputs_all()
                    if inputs:
                        # 6x6 grid density
                        bins = {}
                        for c in inputs:
                            cx, cy = center(c)
                            ix = int(min(5, max(0, (cx / max(1, sx)) * 6)))
                            iy = int(min(5, max(0, (cy / max(1, sy)) * 6)))
                            bins.setdefault((ix, iy), []).append(c)
                        if bins:
                            key = max(bins.keys(), key=lambda k: len(bins[k]))
                            group = bins[key]
                            mx = sum((c[0]+c[2]) / 2.0 for c in group) / len(group)
                            my = sum((c[1]+c[3]) / 2.0 for c in group) / len(group)
                            radius = max(50, int(min(sx, sy) * 0.15))
                            def distc(c):
                                cx = (c[0]+c[2]) / 2.0
                                cy = (c[1]+c[3]) / 2.0
                                return ((cx - mx)**2 + (cy - my)**2) ** 0.5
                            cluster = [c for c in inputs if distc(c) <= radius]
                            if not cluster:
                                cluster = group
                            try:
                                sorted_by_width = sorted(inputs, key=lambda c: (c[2]-c[0]), reverse=True)
                                topk = sorted_by_width[:3]
                                if topk:
                                    cluster = list(set(cluster + topk))
                            except Exception:
                                pass
                            x1 = min(c[0] for c in cluster)
                            y1 = min(c[1] for c in cluster)
                            x2 = max(c[2] for c in cluster)
                            y2 = max(c[3] for c in cluster)
                            margin = max(100, int(min(sx, sy) * 0.2))
                            x1 = max(0, x1 - margin)
                            y1 = max(0, y1 - margin)
                            x2 = min(sx, x2 + margin)
                            y2 = min(sy, y2 + margin)
                            pruned["frame_roi"] = [x1, y1, x2, y2]
        except Exception:
            pass
        for obj in objects:
            label = (obj.get("label") or "").lower()
            coords = obj.get("coords")
            texts = obj.get("text_content") or []
            
            # Disable frame_roi filtering for inputs to let compute_fields match everything
            if texts:
                pruned["texts"].append({"coords": coords, "text": texts})
            if "button" in label:
                pruned["buttons"].append({"label": label, "coords": coords, "text": texts})
            elif "checkbox" in label:
                pruned["checkboxes"].append({"label": label, "coords": coords, "text": texts})
            elif "radio" in label:
                pruned["radios"].append({"label": label, "coords": coords, "text": texts})
            elif "select" in label or "dropdown" in label or "combobox" in label:
                pruned["selects"].append({"label": label, "coords": coords, "text": texts})
            elif "input" in label or "textbox" in label or "textarea" in label or "field" in label:
                pruned["inputs"].append({"label": label, "coords": coords, "text": texts})
                
            for inter in obj.get("interactions") or []:
                ilabel = (inter.get("label") or "").lower()
                icoords = inter.get("coords")
                itext = inter.get("text") or []
                
                if "button" in ilabel:
                    pruned["buttons"].append({"label": ilabel, "coords": icoords, "text": itext})
                elif "checkbox" in ilabel:
                    pruned["checkboxes"].append({"label": ilabel, "coords": icoords, "text": itext})
                elif "radio" in ilabel:
                    pruned["radios"].append({"label": ilabel, "coords": icoords, "text": itext})
                elif "select" in ilabel or "dropdown" in ilabel or "combobox" in ilabel:
                    pruned["selects"].append({"label": ilabel, "coords": icoords, "text": itext})
                elif "input" in ilabel or "textbox" in ilabel or "textarea" in ilabel or "field" in ilabel:
                    pruned["inputs"].append({"label": ilabel, "coords": icoords, "text": itext})
        raw_data = output_data.get("RawData", {})
        for key, coords_list in raw_data.items():
            ilabel = key.lower()
            for coords in coords_list:
                if "button" in ilabel:
                    pruned["buttons"].append({"label": ilabel, "coords": coords, "text": []})
                elif "checkbox" in ilabel:
                    pruned["checkboxes"].append({"label": ilabel, "coords": coords, "text": []})
                elif "radio" in ilabel:
                    pruned["radios"].append({"label": ilabel, "coords": coords, "text": []})
                elif "select" in ilabel or "dropdown" in ilabel or "combobox" in ilabel:
                    pruned["selects"].append({"label": ilabel, "coords": coords, "text": []})
                elif "input" in ilabel or "textbox" in ilabel or "textarea" in ilabel or "field" in ilabel:
                    pruned["inputs"].append({"label": ilabel, "coords": coords, "text": []})
        
        # Deduplicate inputs by coordinates (merge overlapping boxes)
        def calculate_overlap(a, b):
            xA = max(a[0], b[0])
            yA = max(a[1], b[1])
            xB = min(a[2], b[2])
            yB = min(a[3], b[3])
            interArea = max(0, xB - xA) * max(0, yB - yA)
            if interArea == 0: return 0
            boxAArea = max(1, (a[2] - a[0]) * (a[3] - a[1]))
            boxBArea = max(1, (b[2] - b[0]) * (b[3] - b[1]))
            return interArea / min(boxAArea, boxBArea)

        def dedup(lst):
            res = []
            for item in lst:
                c = item["coords"]
                merged = False
                for s in res:
                    sc = s["coords"]
                    if calculate_overlap(c, sc) > 0.6:
                        # Merge them by taking the bounding box
                        s["coords"] = [min(c[0], sc[0]), min(c[1], sc[1]), max(c[2], sc[2]), max(c[3], sc[3])]
                        merged = True
                        break
                if not merged:
                    res.append(item)
            return res
        
        pruned["inputs"] = dedup(pruned["inputs"])
        pruned["buttons"] = dedup(pruned["buttons"])
        pruned["selects"] = dedup(pruned["selects"])
        pruned["checkboxes"] = dedup(pruned["checkboxes"])
        pruned["radios"] = dedup(pruned["radios"])
        
        return pruned

    def save_pruned(self, pruned):
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        out_dir = os.path.join(base_dir, "vision_output")
        if not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)
        ts = pruned.get("timestamp") or int(time.time())
        ts_path = os.path.join(out_dir, f"pruned_{ts}.json")
        latest_path = os.path.join(out_dir, "latest_pruned.json")
        try:
            with open(ts_path, "w", encoding="utf-8") as f:
                json.dump(pruned, f, ensure_ascii=False, indent=2)
            with open(latest_path, "w", encoding="utf-8") as f:
                json.dump(pruned, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        return {"ts_path": ts_path, "latest_path": latest_path}

    def render_pruned_mask(self, screen, pruned):
        h, w = screen.shape[:2]
        
        # Use the original screen as the base image instead of a dark background
        img = screen.copy()
            
        # Define color scheme (BGR format for OpenCV)
        # Texts: Green (0, 255, 0)
        # Buttons: Blue (255, 0, 0)
        # Inputs: Yellow (0, 255, 255)
        # Selects/Checkboxes/Radios: Red (0, 0, 255)
        categories = [
            ("inputs", (0, 255, 255)),
            ("selects", (0, 0, 255)),
            ("checkboxes", (0, 0, 255)),
            ("radios", (0, 0, 255)),
            ("buttons", (255, 0, 0)),
            ("texts", (0, 255, 0)),
        ]
        
        for key, color in categories:
            for item in pruned.get(key) or []:
                coords = item.get("coords")
                if not coords:
                    continue
                x1, y1, x2, y2 = map(int, coords)
                x1 = max(0, min(w-1, x1)); x2 = max(0, min(w, x2))
                y1 = max(0, min(h-1, y1)); y2 = max(0, min(h, y2))
                cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
                
        # Also explicitly draw green boxes around OCR texts since they are now separate from YOLO texts
        # Draw all text blocks (wrappers) first with a thick purple line
        for tb in pruned.get("text_blocks") or []:
            coords = tb.get("coords")
            if not coords:
                continue
            x1, y1, x2, y2 = map(int, coords)
            x1 = max(0, min(w-1, x1)); x2 = max(0, min(w, x2))
            y1 = max(0, min(h-1, y1)); y2 = max(0, min(h, y2))
            cv2.rectangle(img, (x1, y1), (x2, y2), (255, 0, 255), 2)
            cv2.putText(img, f"[{tb.get('block_id', '')}]", (x1, max(10, y1-5)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 255), 2)

        # Draw individual words inside with a thin faded green line
        for text_item in pruned.get("ocr_texts") or []:
            coords = text_item.get("coords")
            if not coords:
                continue
            x1, y1, x2, y2 = map(int, coords)
            x1 = max(0, min(w-1, x1)); x2 = max(0, min(w, x2))
            y1 = max(0, min(h-1, y1)); y2 = max(0, min(h, y2))
            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 150, 0), 1)
                    
        # Draw the index number for all fields
        for field in pruned.get("fields") or []:
            c = field.get("coords")
            f_type = field.get("type", "unknown")
            
            # Match the text color to the element type color
            text_color = (0, 0, 255) # Default to Red
            if f_type == "input": text_color = (0, 255, 255) # Yellow
            elif f_type == "button": text_color = (255, 0, 0) # Blue
            elif f_type == "text": text_color = (0, 255, 0) # Green
            
            if not c:
                continue
            x1, y1, x2, y2 = map(int, c)
            
            # Draw the index number prominently
            idx_str = str(field.get("index"))
            cv2.putText(img, idx_str, (x1, max(0, y1-5)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, text_color, 2)
            
        return img

    def detect_bright_panel(self, screen):
        try:
            gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
            _, th = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25))
            th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, kernel)
            contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not contours:
                return None
            c = max(contours, key=cv2.contourArea)
            x, y, w, h = cv2.boundingRect(c)
            return [x, y, x + w, y + h]
        except Exception:
            return None

    def detect_focus_roi(self, screen):
        try:
            h, w = screen.shape[:2]
            gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
            gray = cv2.GaussianBlur(gray, (5, 5), 0)
            thr = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 31, -10)
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (17, 17))
            thr = cv2.morphologyEx(thr, cv2.MORPH_CLOSE, kernel)
            contours, _ = cv2.findContours(thr, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not contours:
                return None
            cx, cy = w / 2.0, h / 2.0
            best = None
            best_score = -1e9
            for c in contours:
                x, y, ww, hh = cv2.boundingRect(c)
                ar = ww / max(1, hh)
                area = ww * hh
                frac = area / float(w * h)
                if frac < 0.15 or frac > 0.9:
                    continue
                if ar < 0.6 or ar > 2.0:
                    continue
                mx, my = x + ww / 2.0, y + hh / 2.0
                dist = ((mx - cx) ** 2 + (my - cy) ** 2) ** 0.5
                dist_norm = dist / ((w + h) / 2.0)
                score = (1.0 - dist_norm) + (0.5 * (1.0 - abs(ar - 1.0))) + (0.3 * (frac))
                if score > best_score:
                    best_score = score
                    best = [x, y, x + ww, y + hh]
            return best
        except Exception:
            return None

    def refilter_pruned(self, pruned):
        roi = pruned.get("frame_roi")
        if not roi:
            return pruned
        def inside(c):
            if not c:
                return False
            return self.is_box_inside_box(c, roi)
        for key in ["inputs", "selects", "checkboxes", "radios", "buttons", "texts"]:
            pruned[key] = [item for item in (pruned.get(key) or []) if inside(item.get("coords"))]
        return pruned

    def detect_elements_in_roi(self, screen, pruned):
        roi = pruned.get("frame_roi")
        if not roi:
            return pruned
        x1r, y1r, x2r, y2r = map(int, roi)
        roi_img = screen[y1r:y2r, x1r:x2r].copy()
        structure_labels = list(self.structure_model.names.values())
        icon_labels = list(self.icon_model.names.values()) if self.icon_model else []
        all_labels = list(set(structure_labels + icon_labels))
        label_objects = {label: "Interaction" for label in all_labels}
        try:
            json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "label_description.json")
            if os.path.exists(json_path):
                with open(json_path, "r") as f:
                    json_data = json.load(f)
                for label in all_labels:
                    if label in json_data:
                        label_objects[label] = json_data[label]
        except Exception:
            pass
        data = {label: [] for label in all_labels}
        try:
            results_structure = self.structure_model(roi_img, conf=0.25)
            for result in results_structure:
                for box in result.boxes:
                    label = self.structure_model.names[int(box.cls)]
                    coords = box.xyxy.tolist()[0]
                    coords = [coords[0] + x1r, coords[1] + y1r, coords[2] + x1r, coords[3] + y1r]
                    data[label].append({"coords": coords, "sources": [{"model": "structure_yolo", "label": str(label)}]})
        except Exception:
            pass
        masked_roi = roi_img.copy()
        if "text" in data:
            for det in data["text"]:
                coords = det.get("coords")
                if not coords:
                    continue
                x1, y1, x2, y2 = map(int, [coords[0] - x1r, coords[1] - y1r, coords[2] - x1r, coords[3] - y1r])
                cv2.rectangle(masked_roi, (x1, y1), (x2, y2), (128, 128, 128), -1)
        try:
            if self.icon_model:
                results_icons = self.icon_model(masked_roi, conf=0.25)
                for result in results_icons:
                    for box in result.boxes:
                        label = self.icon_model.names[int(box.cls)]
                        coords = box.xyxy.tolist()[0]
                        coords = [coords[0] + x1r, coords[1] + y1r, coords[2] + x1r, coords[3] + y1r]
                        if label not in data:
                            data[label] = []
                        data[label].append({"coords": coords, "sources": [{"model": "icon_yolo", "label": str(label)}]})
        except Exception:
            pass
        def _overlap_min_area(a, b):
            xA = max(a[0], b[0])
            yA = max(a[1], b[1])
            xB = min(a[2], b[2])
            yB = min(a[3], b[3])
            inter = max(0.0, xB - xA) * max(0.0, yB - yA)
            if inter <= 0.0:
                return 0.0
            aa = max(1.0, (a[2] - a[0]) * (a[3] - a[1]))
            ba = max(1.0, (b[2] - b[0]) * (b[3] - b[1]))
            return inter / min(aa, ba)

        def _dedup_merge(dets, thr=0.45):
            groups = []
            for det in dets:
                c = det.get("coords")
                if not c:
                    continue
                sources = det.get("sources") or []
                merged = False
                for g in groups:
                    if _overlap_min_area(c, g["coords"]) >= thr:
                        g["coords_list"].append(c)
                        g["sources"].extend(sources)
                        merged = True
                        break
                if not merged:
                    groups.append({"coords": c, "coords_list": [c], "sources": list(sources)})
            out = []
            for g in groups:
                coords_list = g.get("coords_list") or []
                if not coords_list:
                    continue
                avg = [sum(col) / len(col) for col in zip(*coords_list)]
                uniq_sources = []
                seen = set()
                for s in g.get("sources") or []:
                    key = (s.get("model"), s.get("label"))
                    if key in seen:
                        continue
                    seen.add(key)
                    uniq_sources.append(s)
                out.append({"coords": avg, "sources": uniq_sources})
            return out

        for label in list(data.keys()):
            data[label] = _dedup_merge(data.get(label) or [], thr=0.45)

        def add_items(key, target):
            for det in data.get(key, []):
                coords = det.get("coords")
                if not coords:
                    continue
                target.append({"label": key.lower(), "coords": coords, "text": [], "sources": det.get("sources") or [{"model": "yolo", "label": str(key)}]})
        # Reset and repopulate
        pruned["inputs"] = []
        pruned["selects"] = []
        pruned["checkboxes"] = []
        pruned["radios"] = []
        pruned["buttons"] = []
        pruned["texts"] = []
        for label, coords_list in data.items():
            t = label_objects.get(label, "Interaction")
            for det in coords_list:
                coords = det.get("coords")
                if not coords:
                    continue
                item = {"label": (label or "").lower(), "coords": coords, "text": [], "sources": det.get("sources") or [{"model": "yolo", "label": str(label)}]}
                if t == "Text":
                    pruned["texts"].append(item)
                elif t == "Property":
                    pass
                elif t == "Object":
                    pass
                else:
                    lname = (label or "").lower()
                    if "button" in lname or "link" in lname:
                        pruned["buttons"].append(item)
                    elif "checkbox" in lname:
                        pruned["checkboxes"].append(item)
                    elif "radio" in lname:
                        pruned["radios"].append(item)
                    elif "select" in lname or "dropdown" in lname or "combobox" in lname:
                        pruned["selects"].append(item)
                    elif "input" in lname or "textbox" in lname or "textarea" in lname or "text" in lname or "field" in lname:
                        pruned["inputs"].append(item)
        return pruned

    def compute_fields(self, pruned, screen):
        inputs = pruned.get("inputs", [])
        selects = pruned.get("selects", [])
        checkboxes = pruned.get("checkboxes", [])
        radios = pruned.get("radios", [])
        buttons = pruned.get("buttons", [])
        
        # Merge all interactable elements into one list with a type identifier
        all_elements = []
        for it in inputs: all_elements.append({"type": "input", **it})
        for it in selects: all_elements.append({"type": "select", **it})
        for it in checkboxes: all_elements.append({"type": "checkbox", **it})
        for it in radios: all_elements.append({"type": "radio", **it})
        for it in buttons: all_elements.append({"type": "button", **it})
        for it in all_elements:
            if "sources" not in it or not it.get("sources"):
                it["sources"] = [{"model": "yolo", "label": str(it.get("label") or "")}]
            if "raw_labels" not in it or not it.get("raw_labels"):
                it["raw_labels"] = [str(it.get("label") or "")]

        # --- Global Deduplication ---
        # Prevent the same bounding box from appearing as both an input and a button
        def calculate_overlap_global(a, b):
            xA = max(a[0], b[0])
            yA = max(a[1], b[1])
            xB = min(a[2], b[2])
            yB = min(a[3], b[3])
            interArea = max(0, xB - xA) * max(0, yB - yA)
            if interArea == 0: return 0
            boxAArea = max(1, (a[2] - a[0]) * (a[3] - a[1]))
            boxBArea = max(1, (b[2] - b[0]) * (b[3] - b[1]))
            return interArea / min(boxAArea, boxBArea)
            
        deduped_elements = []
        for item in all_elements:
            c = item.get("coords")
            if not c: continue
            merged = False
            for s in deduped_elements:
                sc = s.get("coords")
                if calculate_overlap_global(c, sc) > 0.6:
                    s["coords"] = [min(c[0], sc[0]), min(c[1], sc[1]), max(c[2], sc[2]), max(c[3], sc[3])]
                    # Prefer input/select over button if there's a conflict
                    if item.get("type") in ["input", "select"] and s.get("type") == "button":
                        s["type"] = item.get("type")
                        s["label"] = item.get("label")
                    if item.get("sources"):
                        s_sources = s.get("sources") or []
                        seen = {(x.get("model"), x.get("label")) for x in s_sources if isinstance(x, dict)}
                        for x in item.get("sources") or []:
                            if not isinstance(x, dict):
                                continue
                            k = (x.get("model"), x.get("label"))
                            if k in seen:
                                continue
                            seen.add(k)
                            s_sources.append(x)
                        s["sources"] = s_sources
                    if item.get("raw_labels"):
                        s_raw = s.get("raw_labels") or []
                        seen_rl = set(s_raw)
                        for rl in item.get("raw_labels") or []:
                            if rl in seen_rl:
                                continue
                            seen_rl.add(rl)
                            s_raw.append(rl)
                        s["raw_labels"] = s_raw
                    merged = True
                    break
            if not merged:
                deduped_elements.append(item)
                
        all_elements = deduped_elements
        pruned["inputs"] = []
        pruned["selects"] = []
        pruned["checkboxes"] = []
        pruned["radios"] = []
        pruned["buttons"] = []
        for it in all_elements:
            out = {
                "label": it.get("label"),
                "coords": it.get("coords"),
                "text": it.get("text") or [],
                "sources": it.get("sources") or [],
                "raw_labels": it.get("raw_labels") or [],
            }
            t = it.get("type")
            if t == "input":
                pruned["inputs"].append(out)
            elif t == "select":
                pruned["selects"].append(out)
            elif t == "checkbox":
                pruned["checkboxes"].append(out)
            elif t == "radio":
                pruned["radios"].append(out)
            elif t == "button":
                pruned["buttons"].append(out)

        def y_then_x(c): 
            return (int(c[1]), int(c[0]))
            
        roi = pruned.get("frame_roi")
        # Extract text elements from the screen using OCR
        try:
            import sys, os
            # Ensure the path allows importing player.ocr_engine directly
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            if base_dir not in sys.path:
                sys.path.append(base_dir)
            from LoOper.player.ocr_engine import extract_text_elements_from_image_path
            
            # Save temporary image for OCR
            import tempfile
            temp_path = os.path.join(tempfile.gettempdir(), "temp_ocr_screen.png")
            cv2.imwrite(temp_path, screen)
            
            # Run OCR on the full screen so LayoutLMv3 has all the context to map labels
            ocr_texts_raw = extract_text_elements_from_image_path(temp_path)
            
            # Clean up temp file
            try:
                os.remove(temp_path)
            except:
                pass
                
            ocr_texts = []
            for t in ocr_texts_raw:
                bx = t.get("box")
                bbox = t.get("bbox")
                
                if bbox and len(bbox) == 4:
                    x, y, w, h = bbox
                    t["coords"] = [max(0, int(x)), max(0, int(y)), min(screen.shape[1], int(x+w)), min(screen.shape[0], int(y+h))]
                elif bx and len(bx) >= 4:
                    # Some OCR wrappers return coordinates scaled to a 1000x1000 grid instead of absolute pixels.
                    # Or they might be returned as floats that need proper scaling.
                    # Let's trust the `bbox` calculation from `extract_text_elements_from_image_path` 
                    # since that is explicitly formatting it to (x, y, w, h) based on the image size.
                    try:
                        if isinstance(bx[0], (int, float)):
                            # It's a flat array [x1, y1, x2, y2, x3, y3, x4, y4]
                            xs = bx[0::2]
                            ys = bx[1::2]
                        else:
                            # It's a nested list [[x1, y1], [x2, y2], ...]
                            xs = [p[0] for p in bx]
                            ys = [p[1] for p in bx]
                            
                        # If the coordinates are very small floats (e.g. 0.123), they are normalized and need to be multiplied by width/height
                        if max(xs) <= 1.0 and max(ys) <= 1.0:
                            x1 = max(0, int(min(xs) * screen.shape[1]))
                            y1 = max(0, int(min(ys) * screen.shape[0]))
                            x2 = min(screen.shape[1], int(max(xs) * screen.shape[1]))
                            y2 = min(screen.shape[0], int(max(ys) * screen.shape[0]))
                        else:
                            x1 = max(0, int(min(xs)))
                            y1 = max(0, int(min(ys)))
                            x2 = min(screen.shape[1], int(max(xs)))
                            y2 = min(screen.shape[0], int(max(ys)))
                        
                        t["coords"] = [x1, y1, x2, y2]
                    except Exception as e:
                        print(f"Error parsing polygon coordinates: {e}")
                
                if "coords" in t:
                    ocr_texts.append(t)
            print(f"Extracted {len(ocr_texts)} texts from full screen.")
        except Exception as e:
            print("OCR Error in compute_fields:", e)
            ocr_texts = []
            
        def get_value_inside(inp):
            x1, y1, x2, y2 = inp
            inside_texts = []
            for t in ocr_texts:
                if "coords" not in t: continue
                tx1, ty1, tx2, ty2 = t["coords"]
                cx = (tx1 + tx2) / 2
                cy = (ty1 + ty2) / 2
                # If text center is inside the input box
                if x1 <= cx <= x2 and y1 <= cy <= y2:
                    inside_texts.append(t)
                # Or if text bounding box is mostly inside the input box
                elif tx1 >= x1 - 5 and tx2 <= x2 + 5 and ty1 >= y1 - 5 and ty2 <= y2 + 5:
                    inside_texts.append(t)
            
            if inside_texts:
                # Sort texts left-to-right
                inside_texts.sort(key=lambda x: x["coords"][0])
                return " ".join([t["text"] for t in inside_texts])
            return ""

        def nearest_label(inp):
            x1, y1, x2, y2 = inp
            best_text = None
            best_score = 1e9
            
            # Use full image text array, without ROI shifting
            for t in ocr_texts:
                if "coords" not in t: continue
                tx1, ty1, tx2, ty2 = t["coords"]
                
                # Check for overlapping/inside text (like typed values). Do NOT use them as labels.
                # Tighten overlapping bounds so we don't accidentally ignore valid labels that just touch the box
                is_overlapping = (ty2 > y1 + 5) and (ty1 < y2 - 5) and (tx2 > x1 + 5) and (tx1 < x2 - 5)

                if is_overlapping:
                    continue # Ignore text inside the box (it's the value, not the label)
                
                # Calculate center distances
                cx_inp = (x1 + x2) / 2
                cy_inp = (y1 + y2) / 2
                cx_txt = (tx1 + tx2) / 2
                cy_txt = (ty1 + ty2) / 2
                
                # Distance metrics
                vertical_dist = y1 - ty2 # Positive if text is above input
                horizontal_dist = x1 - tx2 # Positive if text is left of input
                center_dist_x = abs(cx_inp - cx_txt)
                
                # A label is usually directly above the input box (bottom of text <= top of input)
                # Ensure the text is horizontally aligned with the input box.
                is_above = (vertical_dist >= -40 and vertical_dist < 60) and (tx2 > x1 - 100 and tx1 < x2 + 100)
                
                # Or it is directly to the left of the input box
                is_left = (horizontal_dist >= -10 and horizontal_dist < 150) and abs(cy_inp - cy_txt) < 30
                
                if is_above:
                    # Score based heavily on vertical distance (closer is better)
                    # We penalize horizontal offset to avoid grabbing text from another column
                    score = vertical_dist + (abs(x1 - tx1) * 0.5)
                    # We want to favor words that end with '*' or ':' if they are close
                    if t["text"].strip().endswith('*') or t["text"].strip().endswith(':'):
                        score -= 50
                        
                    if score < best_score:
                        best_score = score
                        best_text = t["text"]
                elif is_left and best_score > 500:
                    score = 500 + horizontal_dist
                    if score < best_score:
                        best_score = score
                        best_text = t["text"]
                        
            return best_text

        fields = []
        
        # Filter out tiny artifacts
        def is_valid_input(c):
            return (c[2] - c[0]) > 20 and (c[3] - c[1]) > 10
            
        valid_inputs = [it for it in all_elements if is_valid_input(it.get("coords"))]
        sorted_inputs = sorted(valid_inputs, key=lambda it: y_then_x(it.get("coords")))
        
        # Integrate LayoutLMv3 layer to relate visual layout
        if hasattr(self, 'layout_analyzer') and self.layout_analyzer:
            try:
                sorted_inputs = self.layout_analyzer.relate_elements_and_text(screen, ocr_texts, sorted_inputs)
                vis_img = self.layout_analyzer.render_layout_visualization(screen, sorted_inputs, ocr_texts)
                base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                out_dir = os.path.join(base_dir, "vision_output")
                layout_path = os.path.join(out_dir, "latest_layoutlmv3_vis.png")
                cv2.imwrite(layout_path, vis_img)
                pruned["layout_vis_path"] = layout_path
                print(f"LayoutLMv3 visualization saved to: {layout_path}")
            except Exception as e:
                print("LayoutLMv3 Error in compute_fields:", e)

        for idx, it in enumerate(sorted_inputs):
            coords = it.get("coords")
            element_type = it.get("type", "input")
            layout_label = it.get("layoutlm_label", "")
            label_text = layout_label if layout_label else (nearest_label(coords) or "")
            filled_text = get_value_inside(coords)
            
            filled = False
            if filled_text.strip():
                filled = True
            else:
                try:
                    x1, y1, x2, y2 = map(int, coords)
                    pad = 4
                    x1p = max(0, x1 + pad)
                    y1p = max(0, y1 + pad)
                    x2p = max(0, x2 - pad)
                    y2p = max(0, y2 - pad)
                    if x2p > x1p and y2p > y1p:
                        crop = screen[y1p:y2p, x1p:x2p]
                        if crop is not None and crop.size > 0:
                            g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                            std = float(np.std(g))
                            filled = std > 10.0
                except Exception:
                    pass
            
            fields.append({
                "index": idx + 1,
                "type": element_type,
                "coords": coords,
                "label": label_text.strip(),
                "filled": filled,
                "value_text": filled_text.strip()
            })
            
        pruned["fields"] = fields
        
        # --- NEW: Spatial Clustering Algorithm (Text Blocks) ---
        # Group individual OCR text chunks into logical blocks based on proximity
        text_blocks = []
        if ocr_texts:
            # Sort all text items top-to-bottom, then left-to-right
            sorted_texts = sorted(
                [t for t in ocr_texts if "coords" in t], 
                key=lambda x: (x["coords"][1], x["coords"][0])
            )
            
            blocks = []
            current_block = None
            
            # Thresholds for clustering
            Y_THRESHOLD = 20 # Max vertical gap between lines in a block
            X_THRESHOLD = 30 # Max horizontal gap between words in a line
            
            for t in sorted_texts:
                c = t["coords"]
                cx = (c[0] + c[2]) / 2
                cy = (c[1] + c[3]) / 2
                
                if current_block is None:
                    current_block = {
                        "type": "text_block",
                        "coords": list(c),
                        "text": t["text"],
                        "children": [t]
                    }
                else:
                    bc = current_block["coords"]
                    # Check if it belongs in the current block
                    # Condition 1: Same line (Y overlap or very close vertically) and horizontally close
                    same_line = abs(c[1] - bc[1]) < 15 and (c[0] - bc[2]) < X_THRESHOLD
                    # Condition 2: Next line down (vertical proximity) and left-aligned or center-aligned
                    next_line = (c[1] - bc[3]) < Y_THRESHOLD and c[1] >= bc[1] and (c[0] >= bc[0] - 20 and c[0] <= bc[2] + 20)
                    
                    if same_line or next_line:
                        # Merge bounding box
                        bc[0] = min(bc[0], c[0])
                        bc[1] = min(bc[1], c[1])
                        bc[2] = max(bc[2], c[2])
                        bc[3] = max(bc[3], c[3])
                        
                        # Append text smartly (space for same line, newline for next line)
                        if same_line:
                            current_block["text"] += " " + t["text"]
                        else:
                            current_block["text"] += "\n" + t["text"]
                            
                        current_block["children"].append(t)
                    else:
                        # Close current block and start a new one
                        blocks.append(current_block)
                        current_block = {
                            "type": "text_block",
                            "coords": list(c),
                            "text": t["text"],
                            "children": [t]
                        }
                        
            if current_block:
                blocks.append(current_block)
                
            # Assign block IDs
            for i, b in enumerate(blocks):
                b["block_id"] = f"tb_{i+1}"
                text_blocks.append(b)
                
        pruned["text_blocks"] = text_blocks
        # -------------------------------------------------------

        # Include all OCR texts directly in the pruned output for full context
        pruned["ocr_texts"] = ocr_texts
        
        # Build abstract relationships representation
        relationships = []
        for field in fields:
            rel = {
                "field_index": field["index"],
                "type": field["type"],
                "label": field["label"],
                "associated_ocr_text_indices": []
            }
            # Find which OCR texts are related to this field (either inside as value, or nearby as label)
            c = field["coords"]
            for i, t in enumerate(ocr_texts):
                if "coords" not in t: continue
                tc = t["coords"]
                # Inside check
                cx = (tc[0] + tc[2]) / 2
                cy = (tc[1] + tc[3]) / 2
                if (c[0] <= cx <= c[2] and c[1] <= cy <= c[3]) or \
                   (tc[0] >= c[0] - 5 and tc[2] <= c[2] + 5 and tc[1] >= c[1] - 5 and tc[3] <= c[3] + 5):
                    rel["associated_ocr_text_indices"].append({"ocr_index": i, "relation": "inside_value"})
                # Label match check
                elif t["text"].strip() == field["label"] and field["label"]:
                    # Basic check to see if it's spatially close to be considered the label source
                    if abs(tc[1] - c[1]) < 100 and abs(tc[0] - c[0]) < 200:
                        rel["associated_ocr_text_indices"].append({"ocr_index": i, "relation": "assigned_label"})
                        
            relationships.append(rel)
            
        pruned["abstract_relationships"] = relationships

        used_ocr = set()
        for rel in relationships:
            for r in rel.get("associated_ocr_text_indices") or []:
                oi = r.get("ocr_index")
                if isinstance(oi, int):
                    used_ocr.add(oi)

        def _center_in(box, other):
            cx = (other[0] + other[2]) / 2.0
            cy = (other[1] + other[3]) / 2.0
            return box[0] <= cx <= box[2] and box[1] <= cy <= box[3]

        context_items = []
        for it in all_elements:
            c = it.get("coords")
            if not c or len(c) != 4:
                continue
            kind = str(it.get("type") or "")
            context_items.append({
                "kind": kind,
                "coords": c,
                "sources": it.get("sources") or [],
                "raw_labels": it.get("raw_labels") or [],
            })

        def _overlap(a, b):
            xA = max(a[0], b[0])
            yA = max(a[1], b[1])
            xB = min(a[2], b[2])
            yB = min(a[3], b[3])
            inter = max(0.0, xB - xA) * max(0.0, yB - yA)
            if inter <= 0.0:
                return 0.0
            aa = max(1.0, (a[2] - a[0]) * (a[3] - a[1]))
            ba = max(1.0, (b[2] - b[0]) * (b[3] - b[1]))
            return inter / min(aa, ba)

        for field in fields:
            fc = field.get("coords")
            if not fc or len(fc) != 4:
                continue
            best_idx = None
            best_score = 0.0
            for i, it in enumerate(context_items):
                c = it.get("coords")
                if not c or len(c) != 4:
                    continue
                sc = _overlap(fc, c)
                if sc > best_score:
                    best_score = sc
                    best_idx = i
            if best_idx is not None and best_score >= 0.6:
                it = context_items[best_idx]
                it["field_index"] = field.get("index")
                it["field_label"] = field.get("label")
                it["value_text"] = field.get("value_text")
                it["filled"] = field.get("filled")
                it["type"] = field.get("type")
            else:
                context_items.append({
                    "kind": str(field.get("type") or ""),
                    "coords": fc,
                    "sources": [{"model": "layoutlm", "label": "field"}],
                    "raw_labels": [],
                    "field_index": field.get("index"),
                    "field_label": field.get("label"),
                    "value_text": field.get("value_text"),
                    "filled": field.get("filled"),
                    "type": field.get("type"),
                })

        for i, t in enumerate(ocr_texts):
            if i in used_ocr:
                continue
            tc = t.get("coords")
            if not tc or len(tc) != 4:
                continue
            text_val = str(t.get("text") or "").strip()
            if not text_val:
                continue
            inside_any = False
            for it in context_items:
                c = it.get("coords")
                if not c or len(c) != 4:
                    continue
                if _center_in(c, tc) or _overlap(c, tc) >= 0.35:
                    inside_any = True
                    break
            if inside_any:
                continue
            context_items.append({
                "kind": "text",
                "coords": tc,
                "text": text_val,
                "sources": [{"model": "ocr", "label": "text"}],
                "raw_labels": [],
            })

        for idx, it in enumerate(context_items, start=1):
            it["context_id"] = idx
        pruned["context_items"] = context_items
        
        return pruned

    def save_masked(self, pruned, screen):
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        out_dir = os.path.join(base_dir, "vision_output")
        if not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)
        ts = pruned.get("timestamp") or int(time.time())
        masked = self.render_pruned_mask(screen, pruned)
        masked_ts_path = os.path.join(out_dir, f"masked_{ts}.png")
        masked_latest_path = os.path.join(out_dir, "latest_masked.png")
        try:
            cv2.imwrite(masked_ts_path, masked)
            cv2.imwrite(masked_latest_path, masked)
        except Exception:
            pass
        return {"masked_ts_path": masked_ts_path, "latest_masked_path": masked_latest_path}

    def analyze_image_file(self, image_path):
        logging.getLogger('ultralytics').setLevel(logging.WARNING)
        img = cv2.imread(image_path)
        if img is None:
            return {"Object": [], "image_path": None}
        result = self.analyze_frame(img)
        try:
            pruned = self.analyze_pruned_from_result(result, img)
            
            save_paths = self.save_pruned(pruned)
            masked_paths = self.save_masked(pruned, img)
            result["pruned_output_path"] = save_paths.get("latest_path")
            result["pruned_timestamp_path"] = save_paths.get("ts_path")
            result["masked_output_path"] = masked_paths.get("latest_masked_path")
            result["masked_timestamp_path"] = masked_paths.get("masked_ts_path")
            
            # Save LayoutLMv3 visualization if available
            if "layout_vis_path" in pruned:
                result["layout_vis_path"] = pruned["layout_vis_path"]
                    
        except Exception as e:
            import traceback
            traceback.print_exc()
        return result


    def start_vision(self, num):

        logging.getLogger('ultralytics').setLevel(logging.WARNING)

        sct = mss()
        monitors = sct.monitors
        monitor = monitors[num]
        mon_left = int(monitor.get("left", 0))
        mon_top = int(monitor.get("top", 0))
        mon_width = int(monitor.get("width", 0))
        mon_height = int(monitor.get("height", 0))

        # Combine labels from both models
        structure_labels = list(self.structure_model.names.values())
        icon_labels = list(self.icon_model.names.values()) if self.icon_model else []
        
        all_labels = list(set(structure_labels + icon_labels))

        label_objects = {label: "Interaction" for label in all_labels} # Default to Interaction

        try:
            with open("label_description.json", "r") as f:
                json_data = json.load(f)
            
            for label in all_labels:
                if label in json_data:
                    label_objects[label] = json_data[label]
                else:
                    # If not in json, we keep the default "Interaction" (or whatever logic we want)
                    # For icons from the new model, they are likely interactions.
                    if label in icon_labels and label not in structure_labels:
                         label_objects[label] = "Interaction"
                    elif label not in json_data:
                         print(f"Warning: Label '{label}' not found in label_description.json. Defaulting to Interaction.")
                         
        except Exception as e:
            print(f"Error loading label description: {e}")
            print(f"Valid labels: {all_labels}")


        # Setup OpenCV window
        window_name = "Vision Overlay"
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        
        # Create a dummy black image to ensure window is created and sized
        dummy_img = np.zeros((100, 100, 3), dtype=np.uint8)
        cv2.imshow(window_name, dummy_img)
        cv2.waitKey(1) # Process events to actually create the window

        # Get Window Handle
        hwnd = win32gui.FindWindow(None, window_name)
        
        # Retry finding window if missed
        if not hwnd:
            time.sleep(0.1)
            hwnd = win32gui.FindWindow(None, window_name)

        if hwnd:
            print(f"Overlay Window Found: {hwnd}")
            
            # Set Always on Top, Transparent (Click-through), and Layered
            ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex_style | win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT | win32con.WS_EX_TOPMOST)
            
            # Use LWA_COLORKEY to make BLACK (0,0,0) pixels fully transparent
            # This removes the "dark background" and only shows drawn colors
            win32gui.SetLayeredWindowAttributes(hwnd, 0, 0, win32con.LWA_COLORKEY)
            
            # Exclude from Screen Capture (Fixes Infinite Mirroring)
            try:
                # WDA_EXCLUDEFROMCAPTURE = 0x00000011
                win32gui.SetWindowDisplayAffinity(hwnd, 0x00000011)
                print("Window excluded from capture.")
            except Exception as e:
                print(f"Could not set window affinity (capture exclusion): {e}")
            try:
                if mon_width > 0 and mon_height > 0:
                    win32gui.SetWindowPos(
                        hwnd,
                        win32con.HWND_TOPMOST,
                        mon_left,
                        mon_top,
                        mon_width,
                        mon_height,
                        win32con.SWP_SHOWWINDOW
                    )
            except Exception:
                pass
        else:
            print("Could not find Overlay Window!")
        
        # Set to Fullscreen
        cv2.setWindowProperty(window_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
        context_tracker = StableContextTracker(confirm_hits=2, max_misses=4, iou_threshold=0.45, ema_alpha=0.6, ocr_grid=24)
        executor = ThreadPoolExecutor(max_workers=1)
        analysis_future = None
        overlay_img = None
        trigger_key = "f8"
        trigger_prev = False
        last_status = f"{trigger_key.upper()} to capture"

        def render_overlay(shape_hw, snap, status_text):
            h, w = int(shape_hw[0]), int(shape_hw[1])
            img = np.zeros((h, w, 3), dtype=np.uint8)
            roi = snap.get("frame_roi")
            if roi and len(roi) == 4:
                x1, y1, x2, y2 = map(int, roi)
                x1 = max(0, min(w - 1, x1)); x2 = max(0, min(w, x2))
                y1 = max(0, min(h - 1, y1)); y2 = max(0, min(h, y2))
                cv2.rectangle(img, (x1, y1), (x2, y2), (255, 255, 255), 1)

            def kind_color(kind):
                k = (kind or "").lower()
                if k in ["input"]:
                    return (0, 255, 255)
                if k in ["select", "checkbox", "radio"]:
                    return (0, 0, 255)
                if k in ["button"]:
                    return (255, 0, 0)
                if k in ["text"]:
                    return (0, 255, 0)
                return (255, 255, 255)

            for it in snap.get("items") or []:
                c = it.get("coords")
                if not c or len(c) != 4:
                    continue
                x1, y1, x2, y2 = map(int, c)
                x1 = max(0, min(w - 1, x1)); x2 = max(0, min(w, x2))
                y1 = max(0, min(h - 1, y1)); y2 = max(0, min(h, y2))
                col = kind_color(it.get("kind"))
                cv2.rectangle(img, (x1, y1), (x2, y2), col, 2)
                sid = str(it.get("id") or "")
                if sid:
                    cv2.putText(img, sid, (x1, max(0, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, col, 2)

            for t in snap.get("ocr_texts") or []:
                c = t.get("coords")
                if not c or len(c) != 4:
                    continue
                x1, y1, x2, y2 = map(int, c)
                x1 = max(0, min(w - 1, x1)); x2 = max(0, min(w, x2))
                y1 = max(0, min(h - 1, y1)); y2 = max(0, min(h, y2))
                cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 1)
            if status_text:
                cv2.putText(img, str(status_text), (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
            return img

        def _b64_from_path(p, max_dim=1024):
            try:
                img = cv2.imread(p, cv2.IMREAD_UNCHANGED)
                if img is None:
                    with open(p, "rb") as f:
                        return base64.b64encode(f.read()).decode("utf-8")
                h, w = img.shape[:2]
                scale = 1.0
                if max(h, w) > int(max_dim):
                    scale = float(max_dim) / float(max(h, w))
                if scale != 1.0:
                    img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
                ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                if ok:
                    return base64.b64encode(buf.tobytes()).decode("utf-8")
            except Exception:
                pass
            with open(p, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")

        def _build_comorag_context(pruned):
            try:
                ctx_items = pruned.get("context_items") or []
                ocr_texts = pruned.get("ocr_texts") or []
                counts = {
                    "inputs": len(pruned.get("inputs") or []),
                    "buttons": len(pruned.get("buttons") or []),
                    "selects": len(pruned.get("selects") or []),
                    "checkboxes": len(pruned.get("checkboxes") or []),
                    "radios": len(pruned.get("radios") or []),
                    "texts": len(pruned.get("texts") or []),
                    "fields": len(pruned.get("fields") or []),
                    "ocr_texts": len(ocr_texts),
                    "context_items": len(ctx_items),
                }

                def area(c):
                    try:
                        return max(0.0, float(c[2]) - float(c[0])) * max(0.0, float(c[3]) - float(c[1]))
                    except Exception:
                        return 0.0

                key_items = []
                for it in ctx_items:
                    kind = str(it.get("kind") or it.get("type") or "")
                    if kind.lower() == "text":
                        continue
                    c = it.get("coords")
                    if not c or len(c) != 4:
                        continue
                    key_items.append(it)
                key_items.sort(key=lambda it: area(it.get("coords") or [0, 0, 0, 0]), reverse=True)
                key_items = key_items[:40]

                top_ocr = []
                seen_text = set()
                for t in ocr_texts:
                    txt = str(t.get("text") or "").strip()
                    c = t.get("coords")
                    if not txt or not c or len(c) != 4:
                        continue
                    k = txt[:80]
                    if k in seen_text:
                        continue
                    seen_text.add(k)
                    top_ocr.append({"text": txt, "coords": c})
                    if len(top_ocr) >= 40:
                        break

                compact = {
                    "timestamp": pruned.get("timestamp"),
                    "frame_roi": pruned.get("frame_roi"),
                    "counts": counts,
                    "context_items": [
                        {
                            "context_id": it.get("context_id"),
                            "kind": it.get("kind"),
                            "field_label": it.get("field_label"),
                            "label": it.get("label"),
                            "value_text": it.get("value_text"),
                            "coords": it.get("coords"),
                        }
                        for it in key_items
                    ],
                    "ocr_texts_sample": top_ocr,
                }
                return json.dumps(compact, ensure_ascii=False)
            except Exception:
                return json.dumps({"error": "failed_to_build_context"}, ensure_ascii=False)

        def _capture_analyze_and_describe(screen_bgr):
            ts = int(time.time())
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            out_dir = os.path.join(base_dir, "vision_output")
            os.makedirs(out_dir, exist_ok=True)

            screen_ts_path = os.path.join(out_dir, f"screen_{ts}.png")
            screen_latest_path = os.path.join(out_dir, "latest_screen.png")
            try:
                cv2.imwrite(screen_ts_path, screen_bgr)
                cv2.imwrite(screen_latest_path, screen_bgr)
            except Exception:
                pass

            pruned = self.analyze_pruned_from_screen(screen_bgr)
            pruned["timestamp"] = ts
            pruned["screen_path"] = screen_ts_path
            pruned["latest_screen_path"] = screen_latest_path

            save_paths = self.save_pruned(pruned)
            masked_paths = self.save_masked(pruned, screen_bgr)

            pruned_latest_path = save_paths.get("latest_path")
            masked_latest_path = masked_paths.get("latest_masked_path")
            layout_vis_path = pruned.get("layout_vis_path")

            try:
                if base_dir not in sys.path:
                    sys.path.append(base_dir)
                from AI.consult import OllamaClient
                engine = OllamaClient(base_url=f"http://127.0.0.1:{os.environ.get('API_PORT', '8000')}")
                context_text = _build_comorag_context(pruned)
                images = []
                for p in [screen_latest_path, masked_latest_path, layout_vis_path, pruned.get("image_path")]:
                    if p and os.path.exists(p):
                        images.append(_b64_from_path(p))

                model = os.environ.get("COMORAG_DESCRIBE_MODEL") or "llama3.2:3b"
                max_cycles = int(os.environ.get("COMORAG_MAX_CYCLES") or 1)
                memory_size = int(os.environ.get("COMORAG_MEMORY_SIZE") or 800)
                query = "You must produce a detailed, grounded screen description using BOTH the images and the JSON.\n\nHard constraints:\n- Only use context_id values that appear in the JSON. Do not invent ids.\n- If you cannot find any context_id, output the line: NO_CONTEXT_IDS_FOUND\n\nOutput format:\n1) High-level summary (2-4 sentences)\n2) Visible app(s) and layout regions (top bar, sidebars, main panels)\n3) Key interactive controls (buttons/inputs/links/icons) as a table with columns: context_id, kind, label/field_label, value_text (if any), coords\n4) Key text content (OCR) grouped by region\n5) What the user can do next (3-7 actions), each action referencing relevant context_id\n\nGuidance:\n- Prefer field_label/value_text when present.\n- If an element is uncertain, state uncertainty and cite the closest evidence.\n- Use the yolo annotation / masked / layout images to validate and correct JSON where it seems wrong."

                result = engine.comorag_reason(query=query, context=context_text, model=model, max_cycles=max_cycles, images=images)
                response_text = (result or {}).get("response") or ""

                desc_ts_path = os.path.join(out_dir, f"screen_description_{ts}.txt")
                desc_latest_path = os.path.join(out_dir, "latest_screen_description.txt")
                desc_json_ts_path = os.path.join(out_dir, f"screen_description_{ts}.json")
                desc_json_latest_path = os.path.join(out_dir, "latest_screen_description.json")
                try:
                    with open(desc_ts_path, "w", encoding="utf-8") as f:
                        f.write(response_text)
                    with open(desc_latest_path, "w", encoding="utf-8") as f:
                        f.write(response_text)
                    payload = {
                        "timestamp": ts,
                        "model": model,
                        "query": query,
                        "pruned_path": pruned_latest_path,
                        "screen_path": screen_latest_path,
                        "masked_path": masked_latest_path,
                        "layout_vis_path": layout_vis_path,
                        "result": result,
                    }
                    with open(desc_json_ts_path, "w", encoding="utf-8") as f:
                        json.dump(payload, f, ensure_ascii=False, indent=2)
                    with open(desc_json_latest_path, "w", encoding="utf-8") as f:
                        json.dump(payload, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass
            except Exception:
                response_text = ""

            return {"ts": ts, "pruned": pruned, "description": response_text}

        try:
            while True:
                if keyboard.is_pressed('esc'):
                    print("Escape pressed. Exiting...")
                    break

                screen = np.array(sct.grab(monitor))
                screen = screen[:, :, :3]
                h, w = screen.shape[:2]

                now = time.time()
                if analysis_future is not None and analysis_future.done():
                    try:
                        res = analysis_future.result()
                        pruned = res.get("pruned") if isinstance(res, dict) else None
                        if pruned:
                            last_status = f"{trigger_key.upper()} to capture"
                        context_tracker.update(pruned, now_ts=now)
                        snap = context_tracker.snapshot()
                        overlay_img = render_overlay((h, w), snap, last_status)
                    except Exception:
                        overlay_img = overlay_img if overlay_img is not None else np.zeros((h, w, 3), dtype=np.uint8)
                    analysis_future = None

                trigger_now = keyboard.is_pressed(trigger_key)
                if trigger_now and not trigger_prev and analysis_future is None:
                    last_status = "Processing..."
                    analysis_future = executor.submit(_capture_analyze_and_describe, screen.copy())
                trigger_prev = trigger_now

                if overlay_img is None or overlay_img.shape[0] != h or overlay_img.shape[1] != w:
                    overlay_img = render_overlay((h, w), context_tracker.snapshot(), last_status)

                try:
                    if hwnd and win32gui.IsWindow(hwnd):
                        win32gui.SetWindowPos(
                            hwnd,
                            win32con.HWND_TOPMOST,
                            0,
                            0,
                            0,
                            0,
                            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE
                        )
                    else:
                        hwnd = win32gui.FindWindow(None, window_name)
                except Exception:
                    pass

                cv2.imshow(window_name, overlay_img)
                cv2.waitKey(1)
                time.sleep(0.01)
        finally:
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                try:
                    executor.shutdown(wait=False)
                except Exception:
                    pass
            cv2.destroyAllWindows()

    def unload(self):
        try:
            try:
                if hasattr(self, "structure_model"):
                    try:
                        del self.structure_model
                    except Exception:
                        pass
                    self.structure_model = None
            except Exception:
                pass

            try:
                if hasattr(self, "icon_model"):
                    try:
                        del self.icon_model
                    except Exception:
                        pass
                    self.icon_model = None
            except Exception:
                pass

            try:
                if hasattr(self, "layout_analyzer"):
                    try:
                        del self.layout_analyzer
                    except Exception:
                        pass
                    self.layout_analyzer = None
            except Exception:
                pass

            try:
                import torch
                try:
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except Exception:
                    pass
            except Exception:
                pass

            try:
                gc.collect()
            except Exception:
                pass
        except Exception:
            pass
    

    def contains_key(self, data, outer_key, search_key):
        if outer_key in data:
    
            inner_dict = data[outer_key]
            if search_key in inner_dict[0]:
                return True
        return False
    
                            
    def read_image(self, image, coords):
        
        # Konvertiere die Koordinaten in Ganzzahlen
        coords = [int(x) for x in coords]
        x1, y1, x2, y2 = coords
            

        try:
            # Füge Puffer hinzu, um das Zuschneiden etwas großzügiger zu machen
            x1 -= 5
            y1 -= 5
            x2 += 5
            y2 += 5

            # Verwende die crop() Methode, um den Bereich des Bildes zu extrahieren
            image = image[y1:y2, x1:x2]
        except Exception as e:
            print(f"Error cropping image: {e}")
            x1, y1, x2, y2 = coords
            image = image[y1:y2, x1:x2]
            pass


        text = extract_text(image, language="eng")

        # Bereinige den Text, um nicht druckbare Zeichen zu entfernen
        text = re.sub(r'[^\x00-\x7F]+', '', text)
        text = text.replace('\n', '')

        return text
    
    def calculate_intersection(self, box1, box2):

        try:
            rect_1 = self.convert_xyxy_to_xyxyxyxy(coords=box1)
            rect_2 = self.convert_xyxy_to_xyxyxyxy(coords=box2)

            rect_1 = Polygon(rect_1)
            rect_2 = Polygon(rect_2)

            poly_intersection = rect_1.intersection(rect_2)

            return poly_intersection.area
        
        except:

            return 0

    def convert_xyxy_to_xyxyxyxy(self, coords):
        """Korrigiere die Reihenfolge der Punkte, um ein valides Polygon zu erstellen"""
        coords_1 = (coords[0], coords[1])  # (x1, y1)
        coords_2 = (coords[2], coords[1])  # (x2, y1)
        coords_3 = (coords[2], coords[3])  # (x2, y2)
        coords_4 = (coords[0], coords[3])  # (x1, y2)

        return_coords = [coords_1, coords_2, coords_3, coords_4]

        return return_coords
    
    def is_box_inside_box(self, box1, box2):

        x1, y1, x2, y2 = box1

        middle_x = (x2 + x1) / 2
        middle_y = (y2 + y1) / 2

        if middle_x > box2[0] and middle_x < box2[2]:

            if middle_y > box2[1] and middle_y < box2[3]:

                return True
            
        return False
        

class Api:

    def __init__(self) -> None:
        pass

    def get_data(self, label = None):
        with open("view_output.json") as f:
            data = json.load(f)
        
        if not label:
            return data
        else:
            
            new_data = {}

            for key in data:

                if key.split("_")[0] == label:
                    new_data[key] = data[key]
                
                
            return new_data

if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("--monitor", type=int, default=1)
    args = parser.parse_args()

    model_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Computer_Vision_1.5.3.onnx")
    vision = Vision(model_path)

    if args.describe:
        sct = mss()
        monitors = sct.monitors
        monitor_index = int(args.monitor)
        if monitor_index >= len(monitors):
            monitor_index = 0
        monitor = monitors[monitor_index]
        screen = np.array(sct.grab(monitor))
        screen = screen[:, :, :3]

        ts = int(time.time())
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        out_dir = os.path.join(base_dir, "vision_output")
        os.makedirs(out_dir, exist_ok=True)

        screen_ts_path = os.path.join(out_dir, f"screen_{ts}.png")
        screen_latest_path = os.path.join(out_dir, "latest_screen.png")
        try:
            cv2.imwrite(screen_ts_path, screen)
            cv2.imwrite(screen_latest_path, screen)
        except Exception:
            pass

        pruned = vision.analyze_pruned_from_screen(screen)
        pruned["timestamp"] = ts
        pruned["screen_path"] = screen_ts_path
        pruned["latest_screen_path"] = screen_latest_path

        save_paths = vision.save_pruned(pruned)
        masked_paths = vision.save_masked(pruned, screen)
        pruned_latest_path = save_paths.get("latest_path")
        masked_latest_path = masked_paths.get("latest_masked_path")
        layout_vis_path = pruned.get("layout_vis_path")

        vision.unload()

        try:
            if base_dir not in sys.path:
                sys.path.append(base_dir)
            from AI.consult import OllamaClient

            engine = OllamaClient(base_url=f"http://127.0.0.1:{os.environ.get('API_PORT', '8000')}")
            def _build_comorag_context(pruned):
                try:
                    ctx_items = pruned.get("context_items") or []
                    ocr_texts = pruned.get("ocr_texts") or []
                    text_blocks = pruned.get("text_blocks") or []
                    counts = {
                        "inputs": len(pruned.get("inputs") or []),
                        "buttons": len(pruned.get("buttons") or []),
                        "selects": len(pruned.get("selects") or []),
                        "checkboxes": len(pruned.get("checkboxes") or []),
                        "radios": len(pruned.get("radios") or []),
                        "texts": len(pruned.get("texts") or []),
                        "fields": len(pruned.get("fields") or []),
                        "ocr_texts": len(ocr_texts),
                        "text_blocks": len(text_blocks),
                        "context_items": len(ctx_items),
                    }

                    def area(c):
                        try:
                            return max(0.0, float(c[2]) - float(c[0])) * max(0.0, float(c[3]) - float(c[1]))
                        except Exception:
                            return 0.0

                    key_items = []
                    for it in ctx_items:
                        kind = str(it.get("kind") or it.get("type") or "")
                        if kind.lower() == "text":
                            continue
                        c = it.get("coords")
                        if not c or len(c) != 4:
                            continue
                        key_items.append(it)
                    key_items.sort(key=lambda it: area(it.get("coords") or [0, 0, 0, 0]), reverse=True)
                    key_items = key_items[:40]

                    top_ocr = []
                    seen_text = set()
                    for t in ocr_texts:
                        txt = str(t.get("text") or "").strip()
                        c = t.get("coords")
                        if not txt or not c or len(c) != 4:
                            continue
                        k = txt[:80]
                        if k in seen_text:
                            continue
                        seen_text.add(k)
                        top_ocr.append({"text": txt, "coords": c})
                        if len(top_ocr) >= 40:
                            break
                            
                    top_text_blocks = []
                    for tb in text_blocks:
                        txt = str(tb.get("text") or "").strip()
                        c = tb.get("coords")
                        if not txt or not c:
                            continue
                        top_text_blocks.append({
                            "block_id": tb.get("block_id"),
                            "text": txt,
                            "coords": c
                        })
                        if len(top_text_blocks) >= 20:
                            break

                    compact = {
                        "timestamp": pruned.get("timestamp"),
                        "frame_roi": pruned.get("frame_roi"),
                        "counts": counts,
                        "context_items": [
                            {
                                "context_id": it.get("context_id"),
                                "kind": it.get("kind"),
                                "field_label": it.get("field_label"),
                                "label": it.get("label"),
                                "value_text": it.get("value_text"),
                                "coords": it.get("coords"),
                            }
                            for it in key_items
                        ],
                        "text_blocks_sample": top_text_blocks,
                        "ocr_texts_sample": top_ocr,
                    }
                    json_blob = json.dumps(compact, ensure_ascii=False, indent=2)
                    lines = []
                    lines.append("COUNTS=" + json.dumps(counts, ensure_ascii=False))
                    ids = [it.get("context_id") for it in compact.get("context_items") or [] if isinstance(it.get("context_id"), int)]
                    lines.append("AVAILABLE_CONTEXT_IDS=" + json.dumps(ids, ensure_ascii=False))
                    lines.append("CONTEXT_ITEMS:")
                    for it in compact.get("context_items") or []:
                        lines.append(
                            f"- context_id={it.get('context_id')} kind={it.get('kind')} field_label={it.get('field_label')} label={it.get('label')} value_text={it.get('value_text')} coords={it.get('coords')}"
                        )
                    lines.append("TEXT_BLOCKS_SAMPLE:")
                    for tb in compact.get("text_blocks_sample") or []:
                        lines.append(f"- block_id={tb.get('block_id')} text={repr(tb.get('text'))} coords={tb.get('coords')}")
                    lines.append("OCR_TEXTS_SAMPLE:")
                    for t in compact.get("ocr_texts_sample") or []:
                        lines.append(f"- text={t.get('text')} coords={t.get('coords')}")
                    flat = "\n".join(lines)
                    return "JSON_SCREEN_MAP:\n" + json_blob + "\n\nFLATTENED_SCREEN_MAP:\n" + flat
                except Exception as e:
                    import traceback
                    print(f"DEBUG: Error in _build_comorag_context: {e}")
                    traceback.print_exc()
                    return json.dumps({"error": f"failed_to_build_context: {e}"}, ensure_ascii=False)

            context_text = _build_comorag_context(pruned)

            def _b64_from_path(p, max_dim=1024):
                try:
                    img = cv2.imread(p, cv2.IMREAD_UNCHANGED)
                    if img is None:
                        with open(p, "rb") as f:
                            return base64.b64encode(f.read()).decode("utf-8")
                    h, w = img.shape[:2]
                    scale = 1.0
                    if max(h, w) > int(max_dim):
                        scale = float(max_dim) / float(max(h, w))
                    if scale != 1.0:
                        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
                    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                    if ok:
                        return base64.b64encode(buf.tobytes()).decode("utf-8")
                except Exception:
                    pass
                with open(p, "rb") as f:
                    return base64.b64encode(f.read()).decode("utf-8")

            images = []
            for p in [screen_latest_path, masked_latest_path, layout_vis_path, pruned.get("image_path")]:
                if p and os.path.exists(p):
                    images.append(_b64_from_path(p))

            vision_model = os.environ.get("COMORAG_VISION_MODEL") or "qwen2-vl-2b-instruct"
            describe_model = os.environ.get("COMORAG_DESCRIBE_MODEL") or "llama3.2:3b"
            max_cycles = int(os.environ.get("COMORAG_MAX_CYCLES") or 1)
            memory_size = int(os.environ.get("COMORAG_MEMORY_SIZE") or 800)

            vision_query = f"""Task: Perform early vision-text fusion.
You have been provided with two images and a JSON Screen Map in your context. DO NOT say you lack access to them. Look at the images provided in this prompt.
Image 1 is the actual raw UI. Image 2 is the masked UI where the IDs are located.
Read the JSON Screen Map to see the coordinates and guessed labels for those IDs.
Output a unified scene graph listing every valid ID, its exact coordinates from the JSON, and a corrected visual description of what the element actually is in Image 1.

JSON Screen Map:
{context_text}
"""
            vision_result = engine.comorag_reason(query=vision_query, context=context_text, model=vision_model, max_cycles=1, images=images)
            vision_summary = (vision_result or {}).get("response") or ""

            query = "You must produce a detailed, grounded screen description using BOTH (A) the JSON screen map and (B) the vision summary derived from the screenshots.\n\nHard constraints:\n- Only use context_id values that appear in AVAILABLE_CONTEXT_IDS from the context. Do not invent ids.\n- When you cite coords, copy them exactly from the context.\n\nOutput format:\n1) High-level summary (2-4 sentences)\n2) Visible app(s) and layout regions (top bar, sidebars, main panels)\n3) Key interactive controls (buttons/inputs/links/icons) as a table with columns: context_id, kind, label/field_label, value_text (if any), coords\n4) Key text content (OCR) grouped by region\n5) What the user can do next (3-7 actions), each action referencing relevant context_id\n\nGuidance:\n- Prefer field_label/value_text when present.\n- If an element is uncertain, state uncertainty and cite the closest evidence.\n- If JSON and vision summary disagree, explain the conflict.\n\nVISION_SUMMARY:\n" + (vision_summary or "")

            result = engine.comorag_reason(query=query, context=context_text, model=describe_model, max_cycles=max_cycles, images=images)
            response_text = (result or {}).get("response") or ""

            desc_ts_path = os.path.join(out_dir, f"screen_description_{ts}.txt")
            desc_latest_path = os.path.join(out_dir, "latest_screen_description.txt")
            desc_json_ts_path = os.path.join(out_dir, f"screen_description_{ts}.json")
            desc_json_latest_path = os.path.join(out_dir, "latest_screen_description.json")
            try:
                with open(desc_ts_path, "w", encoding="utf-8") as f:
                    f.write(response_text)
                with open(desc_latest_path, "w", encoding="utf-8") as f:
                    f.write(response_text)
                payload = {
                    "timestamp": ts,
                    "vision_model": vision_model,
                    "describe_model": describe_model,
                    "vision_query": vision_query,
                    "query": query,
                    "vision_result": vision_result,
                    "pruned_path": pruned_latest_path,
                    "screen_path": screen_latest_path,
                    "masked_path": masked_latest_path,
                    "layout_vis_path": layout_vis_path,
                    "result": result,
                }
                with open(desc_json_ts_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                with open(desc_json_latest_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

            try:
                print(f"pruned_path={pruned_latest_path}")
                print(f"screen_path={screen_latest_path}")
                print(f"masked_path={masked_latest_path}")
                print(f"layout_vis_path={layout_vis_path}")
            except Exception:
                pass
            if vision_summary:
                print("=== VISION SUMMARY ===")
                print(vision_summary)
                print("=== END VISION SUMMARY ===")
            print(response_text)
        except Exception as e:
            print(f"Describe failed: {e}")
        sys.exit(0)

    vision.start_vision(int(args.monitor))
