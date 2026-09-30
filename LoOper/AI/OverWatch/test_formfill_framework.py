import os
import sys
import json
import time
import uuid
import base64
import logging
import threading
import subprocess
import socket
from typing import Any, Dict, List, Optional, Tuple

import requests


class OverWatchTestFramework:
    def __init__(
        self,
        base_dir: Optional[str] = None,
        api_host: str = "127.0.0.1",
        api_port: int = 8000,
        ollama_host: str = "127.0.0.1",
        ollama_port: int = 11434,
        chain_path: Optional[str] = None,
    ):
        self.base_dir = base_dir or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.api_host = api_host
        self.api_port = int(api_port)
        self.ollama_host = ollama_host
        self.ollama_port = int(ollama_port)
        self.api_base_url = f"http://{self.api_host}:{self.api_port}"
        self.ollama_base_url = f"http://{self.ollama_host}:{self.ollama_port}"
        self.vision_dir = os.path.join(self.base_dir, "vision_output")
        self.chain_path = chain_path or os.path.join(self.base_dir, "chains", "formfillertest.json")
        self._server = None
        self._server_thread = None
        self._client = None
        self.log = logging.getLogger("OverWatchTestFramework")

    def ensure_api(self) -> None:
        if self._ping_api():
            self._set_env_api()
            return
        if self._is_port_available(self.api_port) and self._start_uvicorn(self.api_port):
            self._set_env_api()
            return
        free_port = self._find_free_port()
        if free_port and self._start_uvicorn(free_port):
            self.api_port = free_port
            self.api_base_url = f"http://{self.api_host}:{self.api_port}"
            self._set_env_api()
            return
        try:
            if self.base_dir not in sys.path:
                sys.path.append(self.base_dir)
            from AI import api as overwatch_api
            from fastapi.testclient import TestClient
            self._client = TestClient(overwatch_api.app)
            self._set_env_api()
            return
        except Exception as e:
            raise RuntimeError(f"Failed to start OverWatch API: {e}")

    def shutdown_api(self) -> None:
        if self._server is not None:
            try:
                self._server.should_exit = True
            except Exception:
                pass
    
    def _start_uvicorn(self, port: int) -> bool:
        try:
            import uvicorn
            if self.base_dir not in sys.path:
                sys.path.append(self.base_dir)
            from AI import api as overwatch_api
            config = uvicorn.Config(overwatch_api.app, host=self.api_host, port=int(port), log_level="info")
            self._server = uvicorn.Server(config)
            self._server_thread = threading.Thread(target=self._server.run, daemon=True)
            self._server_thread.start()
            self._wait_for_api()
            return True
        except Exception:
            return False
    
    def _find_free_port(self) -> Optional[int]:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((self.api_host, 0))
                return int(s.getsockname()[1])
        except Exception:
            return None
    
    def _is_port_available(self, port: int) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((self.api_host, int(port)))
                return True
        except Exception:
            return False
    
    def _set_env_api(self) -> None:
        try:
            os.environ["OVERWATCH_API_URL"] = self.api_base_url
            os.environ["API_PORT"] = str(self.api_port)
        except Exception:
            pass

    def embeddings(self, model: str, inputs: List[str]) -> List[List[float]]:
        payload = {"model": model, "inputs": inputs}
        resp = self._request("post", "/embeddings", payload)
        if resp.status_code == 404:
            return self._ollama_embed(model, inputs)
        if resp.status_code >= 400:
            raise RuntimeError(f"OverWatch embeddings failed: {resp.status_code} {resp.text}")
        data = resp.json()
        if isinstance(data, dict) and "error" in data:
            msg = str(data["error"]).lower()
            if "404" in msg or "not found" in msg:
                return self._ollama_embed(model, inputs)
            raise RuntimeError(f"OverWatch embeddings error: {data['error']}")
        if isinstance(data, dict):
            return data.get("embeddings") or []
        return []

    def load_vision_outputs(self) -> Dict[str, Any]:
        pruned_path = os.path.join(self.vision_dir, "latest_pruned.json")
        screen_desc_json_path = os.path.join(self.vision_dir, "latest_screen_description.json")
        screen_desc_txt_path = os.path.join(self.vision_dir, "latest_screen_description.txt")
        masked_path = os.path.join(self.vision_dir, "latest_masked.png")
        layout_path = os.path.join(self.vision_dir, "latest_layoutlmv3_vis.png")
        screen_path = os.path.join(self.vision_dir, "latest_screen.png")
        if not os.path.exists(pruned_path):
            raise FileNotFoundError(pruned_path)
        if not os.path.exists(screen_desc_json_path):
            raise FileNotFoundError(screen_desc_json_path)
        if not os.path.exists(screen_desc_txt_path):
            raise FileNotFoundError(screen_desc_txt_path)
        with open(pruned_path, "r", encoding="utf-8") as f:
            pruned = json.load(f)
        with open(screen_desc_json_path, "r", encoding="utf-8") as f:
            screen_desc_json = json.load(f)
        with open(screen_desc_txt_path, "r", encoding="utf-8") as f:
            screen_desc_txt = f.read()
        return {
            "pruned": pruned,
            "screen_desc_json": screen_desc_json,
            "screen_desc_txt": screen_desc_txt,
            "masked_path": masked_path,
            "layout_path": layout_path,
            "screen_path": screen_path,
            "pruned_path": pruned_path,
        }

    def build_knowledge_graph(self, outputs: Dict[str, Any]) -> Dict[str, Any]:
        pruned = outputs["pruned"]
        graph: Dict[str, Any] = {
            "nodes": {},
            "edges": [],
            "indexes": {"by_kind": {}, "by_label": {}},
            "assets": {
                "masked_path": outputs["masked_path"],
                "layout_path": outputs["layout_path"],
                "screen_path": outputs["screen_path"],
                "pruned_path": outputs["pruned_path"],
            },
        }
        def add_node(node_id: str, kind: str, label: str = "", data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
            node = {"id": node_id, "kind": kind, "label": label, "data": data or {}}
            graph["nodes"][node_id] = node
            graph["indexes"].setdefault("by_kind", {}).setdefault(kind, []).append(node_id)
            if label:
                graph["indexes"].setdefault("by_label", {}).setdefault(label.lower(), []).append(node_id)
            return node
        screen_node = add_node("screen", "screen", "current_screen")
        add_node("masked_image", "image", "latest_masked", {"path": outputs["masked_path"]})
        add_node("layout_image", "image", "latest_layout", {"path": outputs["layout_path"]})
        add_node("screen_image", "image", "latest_screen", {"path": outputs["screen_path"]})
        add_node("screen_description_text", "screen_description", "latest_screen_description", {"text": outputs["screen_desc_txt"]})
        add_node("screen_description_json", "screen_description", "latest_screen_description_json", {"payload": outputs["screen_desc_json"]})
        context_items = pruned.get("context_items") or []
        if context_items:
            for it in context_items:
                cid = it.get("context_id")
                node_id = f"ctx_{cid}" if cid is not None else f"ctx_{uuid.uuid4().hex}"
                label = it.get("label") or it.get("field_label") or ""
                coords = it.get("coords")
                node = add_node(node_id, str(it.get("kind") or it.get("type") or "unknown"), label, {
                    "coords": coords,
                    "value_text": it.get("value_text"),
                    "context_id": cid,
                    "field_label": it.get("field_label"),
                })
                graph["edges"].append({"from": screen_node["id"], "to": node_id, "type": "contains"})
        else:
            for f in pruned.get("fields") or []:
                cid = f.get("index") or f.get("context_id") or uuid.uuid4().hex
                node_id = f"field_{cid}"
                label = f.get("label") or ""
                node = add_node(node_id, str(f.get("type") or "input"), label, {
                    "coords": f.get("coords"),
                    "value_text": f.get("value_text"),
                    "filled": f.get("filled"),
                })
                graph["edges"].append({"from": screen_node["id"], "to": node_id, "type": "contains"})
        for idx, tb in enumerate(pruned.get("text_blocks") or []):
            bid = tb.get("block_id") or idx
            node_id = f"tb_{bid}"
            node = add_node(node_id, "text_block", str(tb.get("text") or ""), {"coords": tb.get("coords")})
            graph["edges"].append({"from": screen_node["id"], "to": node_id, "type": "contains"})
        for idx, o in enumerate(pruned.get("ocr_texts") or []):
            node_id = f"ocr_{idx}"
            node = add_node(node_id, "ocr_text", str(o.get("text") or ""), {"coords": o.get("coords")})
            graph["edges"].append({"from": screen_node["id"], "to": node_id, "type": "contains"})
        sections = self._build_sections(graph)
        for section in sections:
            graph["nodes"][section["id"]] = section
        graph["edges"].extend([{"from": screen_node["id"], "to": s["id"], "type": "contains"} for s in sections])
        return graph

    def _build_sections(self, graph: Dict[str, Any]) -> List[Dict[str, Any]]:
        items = []
        for node in graph["nodes"].values():
            coords = node.get("data", {}).get("coords")
            if coords and len(coords) == 4:
                cx = (float(coords[0]) + float(coords[2])) / 2.0
                cy = (float(coords[1]) + float(coords[3])) / 2.0
                items.append((cy, cx, node))
        items.sort(key=lambda x: x[0])
        sections = []
        current = []
        last_y = None
        for cy, cx, node in items:
            if last_y is None:
                current.append((cy, cx, node))
                last_y = cy
                continue
            if abs(cy - last_y) > 120:
                sections.append(self._make_section(current, len(sections)))
                current = []
            current.append((cy, cx, node))
            last_y = cy
        if current:
            sections.append(self._make_section(current, len(sections)))
        return sections

    def _make_section(self, nodes: List[Tuple[float, float, Dict[str, Any]]], idx: int) -> Dict[str, Any]:
        ys = [n[0] for n in nodes]
        xs = [n[1] for n in nodes]
        labels = [n[2].get("label") for n in nodes if n[2].get("label")]
        name = labels[0] if labels else f"section_{idx}"
        return {
            "id": f"section_{idx}",
            "kind": "section",
            "label": name,
            "data": {
                "bounds": [min(xs), min(ys), max(xs), max(ys)],
                "node_ids": [n[2]["id"] for n in nodes],
            },
        }

    def create_action_crops(self, graph: Dict[str, Any]) -> Dict[str, str]:
        from PIL import Image
        screen_path = graph["assets"].get("screen_path")
        if not screen_path or not os.path.exists(screen_path):
            return {}
        img = Image.open(screen_path)
        out_dir = os.path.join(self.vision_dir, "action_crops")
        os.makedirs(out_dir, exist_ok=True)
        crop_paths: Dict[str, str] = {}
        for node in graph["nodes"].values():
            kind = str(node.get("kind") or "")
            if kind not in {"button", "input", "select", "checkbox", "radio", "text"}:
                continue
            coords = node.get("data", {}).get("coords")
            if not coords or len(coords) != 4:
                continue
            x1, y1, x2, y2 = [int(float(v)) for v in coords]
            x1 = max(0, x1)
            y1 = max(0, y1)
            x2 = min(img.width, x2)
            y2 = min(img.height, y2)
            if x2 <= x1 or y2 <= y1:
                continue
            crop = img.crop((x1, y1, x2, y2))
            out_path = os.path.join(out_dir, f"{node['id']}.png")
            crop.save(out_path)
            node["data"]["screenshot_path"] = out_path
            crop_paths[node["id"]] = out_path
        return crop_paths

    def build_context_text(self, pruned: Dict[str, Any]) -> str:
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
        def area(c: List[float]) -> float:
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
        key_items = key_items[:60]
        top_ocr = []
        seen_text = set()
        for t in ocr_texts:
            txt = str(t.get("text") or "").strip()
            c = t.get("coords")
            if not txt or not c or len(c) != 4:
                continue
            k = txt[:120]
            if k in seen_text:
                continue
            seen_text.add(k)
            top_ocr.append({"text": txt, "coords": c})
            if len(top_ocr) >= 60:
                break
        top_text_blocks = []
        for tb in text_blocks:
            txt = str(tb.get("text") or "").strip()
            c = tb.get("coords")
            if not txt or not c:
                continue
            top_text_blocks.append({"block_id": tb.get("block_id"), "text": txt, "coords": c})
            if len(top_text_blocks) >= 30:
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

    def plan_actions(self, context_text: str, rag_context: Optional[str], images: List[str], model: str, max_steps: int = 10) -> Dict[str, Any]:
        query = "Create a plan to fill the job recruiting form using the UI elements and the provided document context. Output only action commands in order. Use CLICK, TYPE, SCROLL, WAIT. Provide between 1 and 10 actions."
        attempts = 0
        last_response = ""
        while attempts < 3:
            payload = {
                "query": query,
                "model": model,
                "max_cycles": 3,
                "memory_size": 1000,
                "context": context_text + ("\n\nDOCUMENT_CONTEXT:\n" + rag_context if rag_context else ""),
                "images": images,
            }
            resp = self._request("post", "/generate", payload)
            if resp.status_code >= 400:
                raise RuntimeError(f"Generate failed: {resp.status_code} {resp.text}")
            data = resp.json()
            last_response = (data or {}).get("response") or ""
            actions = self._parse_actions(last_response)
            if 1 <= len(actions) <= max_steps:
                return {"raw": last_response, "actions": actions}
            attempts += 1
            query = "The previous plan was invalid. Produce between 1 and 10 action commands only."
        return {"raw": last_response, "actions": self._parse_actions(last_response)}

    def build_symbolic_sequence(self, actions: List[Dict[str, Any]], graph: Dict[str, Any]) -> Dict[str, Any]:
        seq_actions = []
        for a in actions:
            if a["type"] == "click":
                node = self._nearest_node(graph, a.get("x"), a.get("y"))
                screenshot_path = None
                if node:
                    screenshot_path = node.get("data", {}).get("screenshot_path")
                action = {
                    "type": "click",
                    "button": "left",
                    "coordinates": {"x": a["x"], "y": a["y"]},
                    "screenshot": screenshot_path or "",
                    "timestamp": time.time(),
                }
                seq_actions.append(action)
            elif a["type"] == "type":
                seq_actions.append({"type": "type", "text": a["text"], "timestamp": time.time()})
            elif a["type"] == "scroll":
                seq_actions.append({"type": "scroll", "total_delta": a["amount"], "steps": 1, "duration_sec": 0.0})
        return {
            "metadata": {
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "total_actions": len(seq_actions),
                "duration_sec": 0.0,
                "mode": "desktop_only",
            },
            "actions": seq_actions,
        }

    def run_chain(self, chain_path: str) -> bool:
        if self.base_dir not in sys.path:
            sys.path.append(self.base_dir)
        from player.agentic_ops.chain_executor import ChainExecutor
        runner = ChainExecutor()
        return runner.run_chain(chain_path)

    def run_full_test(self) -> Dict[str, Any]:
        self.ensure_api()
        outputs = self.load_vision_outputs()
        graph = self.build_knowledge_graph(outputs)
        self.create_action_crops(graph)
        rag_context, rag_meta = self._build_rag_context_from_chain(self.chain_path)
        context_text = self.build_context_text(outputs["pruned"])
        images = []
        for p in [outputs["screen_path"], outputs["masked_path"], outputs["layout_path"]]:
            if p and os.path.exists(p):
                images.append(self._b64_from_path(p))
        model = rag_meta.get("model") or "minicpm-v:latest"
        plan = self.plan_actions(context_text, rag_context, images, model=model, max_steps=10)
        seq = self.build_symbolic_sequence(plan["actions"], graph)
        seq_path = self._save_sequence(seq)
        chain_path = self._build_chain_for_sequence(seq_path)
        chain_run_ok = self.run_chain(chain_path)
        formfill_ok = self.run_chain(self.chain_path)
        return {
            "plan": plan,
            "sequence_path": seq_path,
            "sequence_chain_path": chain_path,
            "sequence_chain_ok": chain_run_ok,
            "formfill_chain_ok": formfill_ok,
        }

    def _save_sequence(self, seq: Dict[str, Any]) -> str:
        seq_dir = os.path.join(self.base_dir, "sequences")
        os.makedirs(seq_dir, exist_ok=True)
        seq_name = f"formfill_plan_{int(time.time())}.json"
        seq_path = os.path.join(seq_dir, seq_name)
        with open(seq_path, "w", encoding="utf-8") as f:
            json.dump(seq, f, ensure_ascii=False, indent=2)
        return seq_path

    def _build_chain_for_sequence(self, seq_path: str) -> str:
        seq_file = os.path.basename(seq_path)
        chain_config = {
            "sequences": [
                {
                    "name": seq_file,
                    "sequence_file": seq_file,
                    "loop_count": 1,
                    "extra_delay": 0.0,
                    "actions": [],
                    "node_id": f"seq_{uuid.uuid4().hex}",
                    "position": [0.0, 0.0],
                    "connections": [],
                }
            ],
            "conditional_nodes": [],
            "llm_nodes": [],
            "tts_nodes": [],
            "chain_import_nodes": [],
            "form_filler_nodes": [],
            "code_nodes": [],
            "description": "Symbolic form filling plan chain",
        }
        out_dir = os.path.join(self.base_dir, "chains")
        os.makedirs(out_dir, exist_ok=True)
        chain_path = os.path.join(out_dir, f"formfill_plan_chain_{int(time.time())}.json")
        with open(chain_path, "w", encoding="utf-8") as f:
            json.dump(chain_config, f, ensure_ascii=False, indent=2)
        return chain_path

    def _build_rag_context_from_chain(self, chain_path: str) -> Tuple[Optional[str], Dict[str, Any]]:
        if self.base_dir not in sys.path:
            sys.path.append(self.base_dir)
        with open(chain_path, "r", encoding="utf-8") as f:
            chain = json.load(f)
        llm_nodes = chain.get("llm_nodes") or []
        if not llm_nodes:
            return None, {}
        node = llm_nodes[0]
        documents = node.get("rag_documents") or []
        embedding_model = node.get("rag_embedding_model") or "nomic-embed-text"
        chunk_size = int(node.get("rag_chunk_size") or 500)
        overlap = int(node.get("rag_overlap") or 100)
        top_k = int(node.get("rag_top_k") or 3)
        max_chars = int(node.get("rag_max_chars") or 1500)
        prompt = node.get("prompt") or ""
        class _EmbClient:
            def __init__(self, outer):
                self._outer = outer
            def embeddings(self, model, inputs):
                return self._outer.embeddings(model, inputs)
        from player.multi_sequence.llm_executor_resources.rag_utils import build_rag_context_from_docs
        client = _EmbClient(self)
        rag_context = build_rag_context_from_docs(
            client=client,
            prompt=prompt,
            documents=documents,
            embedding_model=embedding_model,
            chunk_size=chunk_size,
            overlap=overlap,
            top_k=top_k,
            max_chars=max_chars,
        )
        return rag_context, {"model": node.get("model")}

    def _parse_actions(self, text: str) -> List[Dict[str, Any]]:
        actions = []
        for line in (text or "").splitlines():
            s = line.strip()
            if not s:
                continue
            u = s.upper()
            if u.startswith("CLICK:"):
                coords = s.split(":", 1)[1].strip()
                parts = coords.replace(",", " ").split()
                if len(parts) >= 2:
                    try:
                        x = int(float(parts[0]))
                        y = int(float(parts[1]))
                        actions.append({"type": "click", "x": x, "y": y})
                    except Exception:
                        continue
            elif u.startswith("TYPE:"):
                val = s.split(":", 1)[1].strip()
                actions.append({"type": "type", "text": val})
            elif u.startswith("SCROLL:"):
                val = s.split(":", 1)[1].strip()
                try:
                    amt = int(float(val))
                    actions.append({"type": "scroll", "amount": amt})
                except Exception:
                    continue
            elif u.startswith("WAIT:"):
                val = s.split(":", 1)[1].strip()
                try:
                    sec = float(val)
                    actions.append({"type": "wait", "seconds": sec})
                except Exception:
                    continue
        return actions

    def _nearest_node(self, graph: Dict[str, Any], x: Optional[int], y: Optional[int]) -> Optional[Dict[str, Any]]:
        if x is None or y is None:
            return None
        best = None
        best_d = None
        for node in graph["nodes"].values():
            coords = node.get("data", {}).get("coords")
            if not coords or len(coords) != 4:
                continue
            cx = (float(coords[0]) + float(coords[2])) / 2.0
            cy = (float(coords[1]) + float(coords[3])) / 2.0
            d = (cx - x) ** 2 + (cy - y) ** 2
            if best_d is None or d < best_d:
                best_d = d
                best = node
        return best

    def _b64_from_path(self, p: str, max_dim: int = 1024) -> str:
        try:
            from PIL import Image
            img = Image.open(p)
            w, h = img.size
            scale = 1.0
            if max(w, h) > int(max_dim):
                scale = float(max_dim) / float(max(w, h))
            if scale != 1.0:
                img = img.resize((int(w * scale), int(h * scale)))
            from io import BytesIO
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return base64.b64encode(buf.getvalue()).decode("utf-8")
        except Exception:
            with open(p, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")

    def _request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None):
        if self._client is not None:
            return self._client.request(method.upper(), path, json=payload)
        url = self.api_base_url.rstrip("/") + path
        return requests.request(method.upper(), url, json=payload, timeout=30)

    def _ollama_embed(self, model: str, inputs: List[str]) -> List[List[float]]:
        payload = {"model": model, "input": inputs}
        resp = requests.post(f"{self.ollama_base_url}/api/embed", json=payload, timeout=30)
        if resp.status_code == 404:
            payload = {"model": model, "prompt": inputs}
            resp = requests.post(f"{self.ollama_base_url}/api/embeddings", json=payload, timeout=30)
        if resp.status_code >= 400:
            raise RuntimeError(f"Ollama embeddings failed: {resp.status_code} {resp.text}")
        data = resp.json()
        if isinstance(data, dict):
            return data.get("embeddings") or []
        return []

    def _ping_api(self) -> bool:
        try:
            r = requests.get(self.api_base_url + "/health", timeout=2)
            return r.status_code == 200
        except Exception:
            return False

    def _wait_for_api(self) -> None:
        deadline = time.time() + 10
        while time.time() < deadline:
            if self._ping_api():
                return
            time.sleep(0.2)
        raise RuntimeError("OverWatch API did not start")


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    _setup_logging()
    framework = OverWatchTestFramework()
    result = framework.run_full_test()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
