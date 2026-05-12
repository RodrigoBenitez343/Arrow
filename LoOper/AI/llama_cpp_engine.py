"""
Llama.cpp Engine Integration for LoOper
Provides fast local inference using GGUF models via llama.cpp Python bindings
"""

import json
import logging
import os
import subprocess
import sys
import time
from typing import Any, Dict, Generator, List, Optional

_eng_logger = logging.getLogger("api.llamacpp.engine")
_eng_logger.setLevel(logging.DEBUG)
if not _eng_logger.handlers:
    # Use absolute log path: same dir as this file (AI/ dir) for reliability
    _log_dir = os.path.dirname(os.path.abspath(__file__))
    _log_path = os.path.join(_log_dir, "..", "..", "automation.log")
    try:
        _fh = logging.FileHandler(os.path.normpath(_log_path))
        _fh.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        _eng_logger.addHandler(_fh)
    except Exception:
        pass
    _eng_logger.propagate = False


class LlamaCppEngine:
    """Engine for running GGUF models using llama.cpp server"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize llama.cpp engine

        Args:
            config: Configuration dictionary with:
                - llama_cpp_path: Path to llama.cpp directory
                - model_path: Path to GGUF model file
                - server_port: Port for llama.cpp server (default: 8081)
                - gpu_layers: Number of layers to offload to GPU (default: 0)
                - threads: Number of CPU threads (default: 4)
                - context_size: Context size in tokens (default: 4096)
        """
        self.config = config or {}
        self.llama_cpp_path = self.config.get(
            "llama_cpp_path",
            os.path.join(
                os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                ),
                "utils",
                "llama.cpp",
            ),
        )
        self.model_path = self.config.get("model_path", "")
        self.mmproj_path = self.config.get("mmproj_path", "")
        self.server_port = int(self.config.get("server_port", 8081))
        self.gpu_layers = int(self.config.get("gpu_layers", 0))
        self.threads = int(self.config.get("threads", 4))
        self.context_size = int(self.config.get("context_size", 4096))
        self.batch_size = int(self.config.get("batch_size", 512))

        self.server_process = None
        self.server_url = f"http://127.0.0.1:{self.server_port}"
        self.is_running = False

    def _find_server_executable(self) -> Optional[str]:
        """Find the llama-server executable"""
        candidates = []

        if os.name == "nt":  # Windows
            candidates.extend(
                [
                    os.path.join(
                        self.llama_cpp_path,
                        "build",
                        "bin",
                        "Release",
                        "llama-server.exe",
                    ),
                    os.path.join(
                        self.llama_cpp_path, "build", "bin", "llama-server.exe"
                    ),
                    os.path.join(self.llama_cpp_path, "llama-server.exe"),
                ]
            )
            # Also check WinGet install location
            try:
                localappdata = os.environ.get("LOCALAPPDATA", "")
                if localappdata:
                    winget_root = os.path.join(
                        localappdata, "Microsoft", "WinGet", "Packages"
                    )
                    if os.path.isdir(winget_root):
                        for d in os.listdir(winget_root):
                            if d.startswith("ggml.llamacpp"):
                                exe = os.path.join(winget_root, d, "llama-server.exe")
                                if os.path.exists(exe):
                                    candidates.append(exe)
                                    break
            except Exception:
                pass
        else:  # Linux/Mac
            candidates.extend(
                [
                    os.path.join(self.llama_cpp_path, "build", "bin", "llama-server"),
                    os.path.join(self.llama_cpp_path, "llama-server"),
                ]
            )

        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate
        return None

    def _find_mtmd_cli(self):
        """Find the llama-mtmd-cli executable"""
        candidates = []
        if os.name == "nt":
            candidates.extend(
                [
                    os.path.join(
                        self.llama_cpp_path,
                        "build",
                        "bin",
                        "Release",
                        "llama-mtmd-cli.exe",
                    ),
                    os.path.join(
                        self.llama_cpp_path, "build", "bin", "llama-mtmd-cli.exe"
                    ),
                    os.path.join(self.llama_cpp_path, "llama-mtmd-cli.exe"),
                ]
            )
            try:
                localappdata = os.environ.get("LOCALAPPDATA", "")
                if localappdata:
                    winget_root = os.path.join(
                        localappdata, "Microsoft", "WinGet", "Packages"
                    )
                    if os.path.isdir(winget_root):
                        for d in os.listdir(winget_root):
                            if d.startswith("ggml.llamacpp"):
                                exe = os.path.join(winget_root, d, "llama-mtmd-cli.exe")
                                if os.path.exists(exe):
                                    candidates.append(exe)
                                    break
            except Exception:
                pass
        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate
        # Try PATH lookup as fallback
        import shutil
        found = shutil.which('llama-mtmd-cli')
        if found:
            candidates.append(found)
        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate
        return None

    def generate_mtmd(self, prompt, image_path, **kwargs):
        """Generate text using llama-mtmd-cli (for LFM and other multimodal models)."""
        mtmd_cli = self._find_mtmd_cli()
        if not mtmd_cli:
            return {"error": "llama-mtmd-cli not found", "response": ""}
        if not os.path.exists(image_path):
            return {"error": f"Image not found: {image_path}", "response": ""}
        cmd = [
            mtmd_cli,
            "--model",
            self.model_path,
            "--image",
            image_path,
            "-p",
            prompt,
            "--temp",
            str(kwargs.get("temperature", 0.7)),
            "-n",
            str(kwargs.get("max_tokens", 4096)),
        ]
        if self.mmproj_path and os.path.exists(self.mmproj_path):
            cmd.append("--mmproj")
            cmd.append(self.mmproj_path)
        _eng_logger.info(f"Running mtmd-cli: {' '.join(cmd)}")
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=kwargs.get("timeout", 600),
                encoding="utf-8",
                errors="replace",
            )
            output = result.stdout.strip()
            return {
                "response": output,
                "model": self.model_path,
                "done": True,
            }
        except subprocess.TimeoutExpired:
            return {"error": "mtmd-cli timed out", "response": ""}
        except Exception as e:
            return {"error": f"mtmd-cli error: {e}", "response": ""}

    def start_server(self, blocking: bool = False) -> bool:
        """
        Start llama.cpp server

        Args:
            blocking: If True, block until server is ready

        Returns:
            bool: True if server started successfully
        """
        if self.is_running:
            return True

        if not self.model_path or not os.path.exists(self.model_path):
            _eng_logger.error(f"Model not found: {self.model_path}")
            return False

        server_exe = self._find_server_executable()
        if not server_exe:
            print(
                f"[LLAMA.CPP] llama-server executable not found in {self.llama_cpp_path}"
            )
            print(
                f"[LLAMA.CPP] Please build llama.cpp first: cd {self.llama_cpp_path} && cmake -B build && cmake --build build --config Release"
            )
            return False

        # Build command
        cmd = [
            server_exe,
            "--model",
            self.model_path,
            "--port",
            str(self.server_port),
            "--threads",
            str(self.threads),
            "--ctx-size",
            str(self.context_size),
            "--batch-size",
            str(self.batch_size),
            "--host",
            "127.0.0.1",
        ]

        # GPU offload if specified
        if self.gpu_layers > 0:
            cmd.extend(["--gpu-layers", str(self.gpu_layers)])

        # Multimodal projector for vision-language models
        if self.mmproj_path and os.path.exists(self.mmproj_path):
            cmd.extend(["--mmproj", self.mmproj_path])
            _eng_logger.info(f"Using mmproj: {self.mmproj_path}")

        # Subprocess kwargs for Windows (no window)
        subprocess_kwargs = {}
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
            startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
            subprocess_kwargs = {
                "creationflags": creationflags,
                "startupinfo": startupinfo,
            }

        try:
            _eng_logger.info(f"Starting server on port {self.server_port}...")
            _eng_logger.info(f"Model: {self.model_path}")
            _eng_logger.info(f"GPU Layers: {self.gpu_layers}, Threads: {self.threads}")

            self.server_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **subprocess_kwargs,
            )

            if blocking:
                # Wait for server to be ready
                import requests

                max_wait = 600  # seconds (10 min for large models to load)
                start_time = time.time()

                while time.time() - start_time < max_wait:
                    try:
                        response = requests.get(f"{self.server_url}/health", timeout=2)
                        if response.status_code == 200:
                            self.is_running = True
                            _eng_logger.info(f"Server ready at {self.server_url}")
                            return True
                    except Exception:
                        pass
                    time.sleep(1)

                _eng_logger.error(f"Server failed to start within {max_wait} seconds")
                return False
            else:
                # Non-blocking - assume it will start
                self.is_running = True
                return True

        except Exception as e:
            _eng_logger.error(f"Failed to start server: {e}")
            return False

    def stop_server(self):
        """Stop llama.cpp server"""
        if self.server_process:
            try:
                _eng_logger.info("Stopping server...")
                self.server_process.terminate()
                self.server_process.wait(timeout=5)
            except Exception as e:
                _eng_logger.error(f"Error stopping server: {e}")
                try:
                    self.server_process.kill()
                except Exception:
                    pass
            finally:
                self.server_process = None
                self.is_running = False

    def generate(self, prompt: str, **kwargs) -> Dict[str, Any]:
        """
        Generate text using llama.cpp server

        Args:
            prompt: Input prompt
            **kwargs: Additional parameters:
                - max_tokens: Maximum tokens to generate
                - temperature: Sampling temperature
                - top_p: Top-p sampling
                - stop: Stop sequences
                - stream: Enable streaming (returns generator)

        Returns:
            Dict with 'response', 'model', 'usage' keys
        """
        import requests

        if not self.is_running:
            return {"error": "Server not running", "response": ""}

        # Build request payload
        payload = {
            "prompt": prompt,
            "n_predict": kwargs.get("max_tokens", -1),
            "temperature": kwargs.get("temperature", 0.7),
            "top_p": kwargs.get("top_p", 0.9),
            "stream": kwargs.get("stream", False),
        }

        # Add stop sequences if provided
        if "stop" in kwargs and kwargs["stop"]:
            payload["stop"] = (
                kwargs["stop"] if isinstance(kwargs["stop"], list) else [kwargs["stop"]]
            )

        try:
            response = requests.post(
                f"{self.server_url}/completion",
                json=payload,
                timeout=None,  # No timeout — wait for model to complete
            )
            response.raise_for_status()

            result = response.json()
            return {
                "response": result.get("content", ""),
                "model": self.model_path,
                "usage": {
                    "prompt_tokens": result.get("tokens_evaluated", 0),
                    "completion_tokens": result.get("tokens_predicted", 0),
                },
                "done": True,
            }

        except requests.exceptions.Timeout:
            return {"error": "Request timeout", "response": ""}
        except Exception as e:
            return {"error": str(e), "response": ""}

    def generate_stream(self, prompt: str, **kwargs) -> Generator[str, None, None]:
        """
        Stream text generation using llama.cpp server

        Args:
            prompt: Input prompt
            **kwargs: Additional parameters

        Yields:
            Text chunks as they're generated
        """
        import requests

        if not self.is_running:
            yield "[Error: Server not running]"
            return

        payload = {
            "prompt": prompt,
            "n_predict": kwargs.get("max_tokens", -1),
            "temperature": kwargs.get("temperature", 0.7),
            "top_p": kwargs.get("top_p", 0.9),
            "stream": True,
        }

        try:
            response = requests.post(
                f"{self.server_url}/completion",
                json=payload,
                stream=True,
                timeout=None,  # No timeout — wait for model to complete
            )
            response.raise_for_status()

            for line in response.iter_lines():
                if line:
                    try:
                        data = json.loads(line.decode("utf-8").replace("data: ", ""))
                        if "content" in data:
                            yield data["content"]
                    except Exception:
                        continue

        except Exception as e:
            yield f"[Error: {str(e)}]"

    def chat(self, messages: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
        """
        Chat completion using llama.cpp server

        Args:
            messages: List of message dicts with 'role' and 'content'.
                      Content can be a string (plain text) or a list of content
                      parts (multimodal: text + image_url).
            **kwargs: Additional parameters

        Returns:
            Dict with 'response', 'model', 'usage' keys
        """
        import requests

        if not self.is_running:
            return {"error": "Server not running", "response": ""}

        # Extract multimodal images if any message has content as a list
        image_base64_list = []
        prompt_text = ""
        has_multimodal = False
        for m in messages:
            content = m.get("content")
            if isinstance(content, list):
                has_multimodal = True
                for part in content:
                    if isinstance(part, dict):
                        if part.get("type") == "text":
                            prompt_text += part.get("text", "")
                        elif part.get("type") == "image_url":
                            url = (part.get("image_url") or {}).get("url", "")
                            if url:
                                # Strip the data URI prefix if present
                                if "," in url and url.startswith("data:"):
                                    url = url.split(",", 1)[1]
                                image_base64_list.append(url)
        if has_multimodal:
            _eng_logger.info(
                f"Engine chat: multimodal with {len(image_base64_list)} image(s), "
                f"prompt text length: {len(prompt_text)}"
            )
        else:
            _eng_logger.info("Engine chat: text-only (no multimodal)")

        if has_multimodal and image_base64_list:
            import shutil as _sh
            _has_mtmd = _sh.which('llama-mtmd-cli')
            print(f"[ENGINE_DEBUG] multimodal={has_multimodal}, images={len(image_base64_list)}, mtmd_found={_has_mtmd}", flush=True)
            # Try mtmd-cli first for multimodal models (LFM, etc.)
            mtmd_cli_path = self._find_mtmd_cli()
            print(f"[ENGINE_DEBUG2] _find_mtmd_cli returned: {mtmd_cli_path}, exists: {os.path.exists(mtmd_cli_path) if mtmd_cli_path else 'N/A'}", flush=True)
            if mtmd_cli_path and os.path.exists(mtmd_cli_path):
                _eng_logger.info("Using mtmd-cli for multimodal generation")
                # Save base64 images to temp files for the CLI
                import tempfile as _tmpf

                image_paths = []
                for idx, img_b64 in enumerate(image_base64_list):
                    suffix = ".png"
                    fd, path = _tmpf.mkstemp(
                        suffix=suffix * (idx + 1) if idx else suffix, prefix="llm_img_"
                    )
                    os.close(fd)
                    with open(path, "wb") as f:
                        import base64 as _b64; f.write(_b64.b64decode(img_b64))
                    image_paths.append(path)

                try:
                    result = self.generate_mtmd(
                        prompt=prompt_text,
                        image_path=image_paths[0],  # mtmd-cli takes one image
                        max_tokens=kwargs.get("max_tokens", -1),
                        temperature=kwargs.get("temperature", 0.7),
                    )
                    return result
                finally:
                    for p in image_paths:
                        try:
                            os.remove(p)
                        except:
                            pass

            # Fallback: Use /completion endpoint with image_data for vision models.
            # The /completion endpoint expects image_data as array of {data, id} objects
            image_data_objects = [
                {"data": img, "id": idx} for idx, img in enumerate(image_base64_list)
            ]
            payload = {
                "prompt": prompt_text,
                "image_data": image_data_objects,
                "n_predict": kwargs.get("max_tokens", -1),
                "temperature": kwargs.get("temperature", 0.7),
                "top_p": kwargs.get("top_p", 0.9),
                "stream": False,
            }
            try:
                response = requests.post(
                    f"{self.server_url}/completion",
                    json=payload,
                    timeout=None,
                )
                if response.status_code == 200:
                    result = response.json()
                    return {
                        "response": result.get("content", ""),
                        "model": self.model_path,
                        "usage": {
                            "prompt_tokens": result.get("tokens_evaluated", 0),
                            "completion_tokens": result.get("tokens_predicted", 0),
                        },
                        "done": True,
                    }
                else:
                    _eng_logger.warning(
                        f"/completion with images returned {response.status_code}"
                    )
                    return {
                        "error": f"Multimodal completion failed: {response.status_code}",
                        "response": "",
                    }
            except Exception as e:
                _eng_logger.error(f"Multimodal completion exception: {e}")
                return {"error": f"Multimodal completion error: {e}", "response": ""}

        # Text-only path: try /chat/completions, fall back to /completion
        prompt = prompt_text or self._messages_to_prompt(messages)
        try:
            payload = {
                "messages": messages,
                "max_tokens": kwargs.get("max_tokens", -1),
                "temperature": kwargs.get("temperature", 0.7),
                "top_p": kwargs.get("top_p", 0.9),
                "stream": False,
            }

            response = requests.post(
                f"{self.server_url}/chat/completions",
                json=payload,
                timeout=None,
            )

            if response.status_code == 200:
                result = response.json()
                return {
                    "response": result.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", ""),
                    "model": self.model_path,
                    "usage": result.get("usage", {}),
                    "done": True,
                }
        except Exception:
            pass

        return self.generate(prompt, **kwargs)

    def _messages_to_prompt(self, messages: List[Dict[str, str]]) -> str:
        """Convert chat messages to prompt format"""
        prompt_parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                prompt_parts.append(f"System: {content}")
            elif role == "user":
                prompt_parts.append(f"User: {content}")
            elif role == "assistant":
                prompt_parts.append(f"Assistant: {content}")

        prompt_parts.append("Assistant: ")
        return "\n".join(prompt_parts)

    def get_server_info(self) -> Dict[str, Any]:
        """Get server information"""
        import requests

        try:
            response = requests.get(f"{self.server_url}/health", timeout=2)
            return {
                "status": "running" if response.status_code == 200 else "error",
                "url": self.server_url,
                "model": self.model_path,
                "port": self.server_port,
            }
        except Exception as e:
            return {"status": "stopped", "url": self.server_url, "error": str(e)}

    def __del__(self):
        """Cleanup on deletion"""
        if self.is_running:
            self.stop_server()


class LlamaCppManager:
    """Manager for multiple llama.cpp engine instances"""

    def __init__(self):
        self.engines: Dict[str, LlamaCppEngine] = {}

    def create_engine(self, engine_id: str, config: Dict[str, Any]) -> LlamaCppEngine:
        """Create or get a llama.cpp engine"""
        if engine_id not in self.engines:
            self.engines[engine_id] = LlamaCppEngine(config)
        return self.engines[engine_id]

    def get_engine(self, engine_id: str) -> Optional[LlamaCppEngine]:
        """Get an existing engine"""
        return self.engines.get(engine_id)

    def stop_all(self):
        """Stop all engines"""
        for engine in self.engines.values():
            engine.stop_server()
        self.engines.clear()


# Global manager instance
llama_cpp_manager = LlamaCppManager()
