"""
Llama.cpp Engine Integration for LoOper
Provides fast local inference using GGUF models via llama.cpp Python bindings
"""

import json
import logging
import os
import re
import subprocess
import sys
import time
from typing import Any, Dict, Generator, List, Optional

_eng_logger = logging.getLogger("api.llamacpp.engine")
_eng_logger.setLevel(logging.DEBUG)
# Records propagate to the unified telemetry dispatch (see logging_setup.py):
# colorized console + logs/automation.log + per-session md log.


# ---------------------------------------------------------------------------
# Port-cleanup safety: only ever kill processes this engine could have
# spawned.  The port-scoped cleanup below must NOT terminate an unrelated
# process that happens to listen on the same port — the agent web server
# binds 127.0.0.1:8081 (the engine's default port) inside the MAIN app
# process, and a blind taskkill /F on that PID took the whole desktop app
# down mid-chain (silent crash, no postprocess, terminal stays open).
# ---------------------------------------------------------------------------

# Image names (lowercase) of llama.cpp binaries that may legitimately hold
# the server port and are safe to kill when orphaned.
LLAMA_BIN_NAMES = {
    "llama-server.exe", "llama-server",
    "llama-embedding.exe", "llama-embedding",
    "llama-cli.exe", "llama-cli",
    "llama-mtmd-cli.exe", "llama-mtmd-cli",
    "llama-bench.exe", "llama-bench",
}


def _is_kv_cache_oom(stderr_text: str) -> bool:
    """True when the llama-server crash is a KV-cache/context-init allocation
    failure (CPU RAM) — the GPU/mmproj/mmap retry ladder cannot fix it; the
    direct remedy is shrinking --ctx-size."""
    _s = (stderr_text or "").lower()
    return any(
        _m in _s
        for _m in (
            "failed to allocate buffer for kv cache",
            "failed to initialize the context",
            "failed to create context",
            "kv cache",
        )
    )


def pid_image_name(pid: int) -> str:
    """Return the lowercase image name of *pid* ("" if not resolvable).

    Uses ``tasklist /FI "PID eq <pid>" /FO CSV /NH``, which prints one
    CSV row: ``"llama-server.exe","1234","Console","1","9,876 K"``.
    The "no tasks" info line is plain text (and localized), never CSV, so
    a row that does not start with a quote means the process is gone.
    """
    if os.name != "nt":
        return ""
    try:
        _out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5,
            creationflags=0x08000000 if os.name == "nt" else 0,
        ).stdout.strip()
    except Exception:
        return ""
    if not _out:
        return ""
    _first = _out.splitlines()[0].strip()
    if not _first.startswith('"'):
        return ""
    _name_parts = _first.split('"')
    return _name_parts[1].lower() if len(_name_parts) >= 2 else ""


# ---------------------------------------------------------------------------
# Response sanitisation — strip chat-template control tokens that llama.cpp
# sometimes leaks into the generated text (e.g. ``<|im_end|>``, ``</s>``).
# ---------------------------------------------------------------------------
_RE_CHAT_CONTROL = re.compile(
    r"""
    (?: <\|im_end\|>
      | <\|im_start\|>
      | </s>
      | <\|endoftext\|>
      | <\|eot_id\|>
      | <\|end\|>
    )
    \s*
    $
    """,
    re.VERBOSE,
)


def _sanitize_response(text: str) -> str:
    """Strip trailing chat-template control tokens from generated text."""
    if not text:
        return text
    # Keep stripping while the tail consists of only whitespace + control tokens
    prev = None
    result = text
    while result != prev:
        prev = result
        result = _RE_CHAT_CONTROL.sub("", result).rstrip()
    return result


# ---------------------------------------------------------------------------
# Reasoning-model support.
#
# Thinking models (DeepSeek-R1, Qwen3, ...) emit a chain of thought before
# the final answer.  With the bundled llama.cpp server (--reasoning-format
# auto, the default) those thoughts are split out of the response into
# ``message.reasoning_content``, leaving the FINAL ANSWER in
# ``message.content``.  Three things used to go wrong:
#   1. When ``content`` was empty (model cut off while still thinking) the
#      engine fell back to ``reasoning_content`` — the THINKING was returned
#      as the response.
#   2. ``finish_reason == "length"`` / ``truncated`` (max_tokens or context
#      exhausted) was ignored, so truncated generations were returned as-is.
#   3. The raw /completion paths returned the thinking tags inline.
#
# The helpers below fix all three: they always prefer the final answer,
# continue truncated generations until the model finishes naturally, and
# strip any thinking blocks that leak into the answer text.
# ---------------------------------------------------------------------------

_RE_THINK_BLOCK = re.compile(
    r"""\s*
    (?: <think\b.*?</think>
      | <\|start_think\|>.*?</\|end_think\|>
      | <\|start_thought\|>.*?</\|end_thought\|>
      | \[THINK\].*?\[/THINK\]
    )
    \s*""",
    re.DOTALL | re.VERBOSE,
)


def _strip_reasoning_blocks(text: str) -> str:
    """Remove thinking blocks (and their padding) from a raw completion.

    Keeps everything the model emitted AFTER its chain of thought — i.e. the
    final answer.  If the whole text is a thinking block, the result is
    empty, signalling that the model never produced an actual answer.
    """
    if not text:
        return text
    return _RE_THINK_BLOCK.sub("", text).strip()


# The caller's ``max_tokens`` is a budget for the FINAL ANSWER.  The server's
# ``n_predict`` / ``max_tokens``, however, counts EVERY generated token — the
# chain of thought included — so a thinking model spends the caller's budget
# on its reasoning and the answer is cut off mid-sentence (observed: 863 chars
# of reasoning consumed 215 of a 256-token budget, leaving ~40 tokens for a
# 149-char half-answer, with every composed "Key Finding"/"Support" pair
# logged truncated mid-word).  Reasoning therefore gets its own headroom ON
# TOP of the caller's budget.  A non-reasoning model is unaffected: the cap is
# a ceiling, not a target, so it still answers in one pass and stops at EOS.
_REASONING_HEADROOM = 1024


def _budget_for_final_answer(max_tokens: Any) -> Any:
    """Widen a final-answer token budget to leave the chain of thought room.

    ``-1`` / ``0`` / ``None`` already mean "no cap" and pass through untouched.
    """
    try:
        budget = int(max_tokens)
    except (TypeError, ValueError):
        return max_tokens
    if budget <= 0:
        return max_tokens
    return budget + max(_REASONING_HEADROOM, budget)


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
                - gpu_layers: Number of layers to offload to GPU (default: 0 = CPU-only)
                - threads: Number of CPU threads (default: -1 = auto-detect)
                - context_size: Context size in tokens (default: 0 = model's native
                  training context)
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
        self.threads = int(self.config.get("threads", -1))
        self.context_size = int(self.config.get("context_size", 0))
        self.batch_size = int(self.config.get("batch_size", 2048))
        # Physical batch (--ubatch-size).  Embedding inputs cannot be split
        # across ubatches, so the embedding server must set this >= the
        # largest single input it will ever receive (0 = server default 512).
        self.ubatch_size = int(self.config.get("ubatch_size", 0))
        # Embedding-server mode (--embeddings flag): serves POST /embedding
        # for vector retrieval.  A separate engine instance is used for this
        # so the generation server keeps serving completions with its model.
        self._embedding_mode = bool(self.config.get("embedding_mode", False))

        self.server_process = None
        self.server_url = f"http://127.0.0.1:{self.server_port}"
        self.is_running = False
        # True only after start_server() actually spawned a child.  The port-
        # release scan in stop_server() is gated on this so engines that never
        # started (test doubles, failed starts) never kill unrelated processes
        # listening on the port.
        self._server_started = False

        # Model capability descriptor (optional, set by caller)
        self.capability = None

    def _find_server_executable(self) -> Optional[str]:
        """Find the llama-server executable"""
        candidates = []

        if os.name == "nt":  # Windows
            # Bundled in MSI: [INSTALLFOLDER]/bin/llama-server.exe
            _exe_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else None
            # PyInstaller _MEIPASS (the _internal directory) — highest priority
            # because all required DLLs (vcruntime140, etc.) live there
            _meipass = getattr(sys, '_MEIPASS', None)
            if _meipass:
                candidates[:0] = [
                    os.path.join(_meipass, "LoOper", "AI", "bin", "llama-server.exe"),
                    os.path.join(_meipass, "bin", "llama-server.exe"),
                    os.path.join(_meipass, "llama-server.exe"),
                ]
            # Assembly order (highest priority first):
            #   frozen bundle (MSI/_MEIPASS)
            #   -> fork rebuilt with Vulkan (build-vulkan, keeps fork features)
            #   -> WinGet official Vulkan release
            #   -> local CPU-only fork build
            #   -> exe_dir/llama-server.exe -> model directory
            _vulkan_fork = os.path.join(
                self.llama_cpp_path, "build-vulkan", "bin", "Release",
                "llama-server.exe",
            )
            _cpu_fork_candidates = [
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
            # WinGet official release (Vulkan + CPU backends) — preferred over
            # the plain CPU-only fork build when no build-vulkan exists, so
            # iGPU acceleration works out of the box in source mode.
            _winget_candidates = []
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
                                    _winget_candidates.append(exe)
                                    break
            except Exception:
                pass
            candidates.append(_vulkan_fork)
            candidates.extend(_winget_candidates)
            candidates.extend(_cpu_fork_candidates)
            if _exe_dir:
                candidates.append(os.path.join(_exe_dir, "llama-server.exe"))
            # Also search alongside the model file (AI/models subdirectories)
            if self.model_path:
                model_dir = os.path.dirname(self.model_path)
                candidates.append(os.path.join(model_dir, "llama-server.exe"))
        else:  # Linux/Mac
            candidates.extend(
                [
                    os.path.join(self.llama_cpp_path, "build", "bin", "llama-server"),
                    os.path.join(self.llama_cpp_path, "llama-server"),
                ]
            )

        for candidate in candidates:
            if os.path.exists(candidate):
                _eng_logger.info("Found llama-server.exe at: %s", candidate)
                return candidate
        return None

    def _find_mtmd_cli(self):
        """Find the llama-mtmd-cli executable"""
        candidates = []
        if os.name == "nt":
            # Bundled in MSI: [INSTALLFOLDER]/bin/llama-mtmd-cli.exe
            _exe_dir_mtmd = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else None
            _meipass_mtmd = getattr(sys, '_MEIPASS', None)
            if _meipass_mtmd:
                candidates[:0] = [
                    os.path.join(_meipass_mtmd, "LoOper", "AI", "bin", "llama-mtmd-cli.exe"),
                    os.path.join(_meipass_mtmd, "bin", "llama-mtmd-cli.exe"),
                    os.path.join(_meipass_mtmd, "llama-mtmd-cli.exe"),
                ]
            candidates.extend(
                [
                    os.path.join(_exe_dir_mtmd, "bin", "llama-mtmd-cli.exe") if _exe_dir_mtmd else "",
                    # Fork rebuilt with Vulkan (build-vulkan, keeps fork features)
                    # - mirrors _find_server_executable's preference order.
                    os.path.join(
                        self.llama_cpp_path,
                        "build-vulkan",
                        "bin",
                        "Release",
                        "llama-mtmd-cli.exe",
                    ),
                    os.path.join(
                        self.llama_cpp_path, "build-vulkan", "bin", "llama-mtmd-cli.exe"
                    ),
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
                ] + ([os.path.join(_exe_dir_mtmd, "llama-mtmd-cli.exe")] if _exe_dir_mtmd else [])
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

        # --jinja: enable jinja chat template engine so the model's
        # built-in Qwen2.5-VL template wraps the prompt with
        #   <|im_start|>user\n<image>{prompt}<|im_end|>\n<|im_start|>assistant\n
        # Without this the model receives raw text and doesn't understand
        # the conversation format, causing it to repeat the prompt or
        # output garbage.
        # NOTE: mtmd-cli defaults use_jinja=false for LLAMA_EXAMPLE_MTMD
        # (common/arg.cpp line 1050-1051), so we must explicitly pass --jinja.
        cap = getattr(self, 'capability', None)
        _needs_jinja = cap is not None and cap.needs_special_tokens
        if _needs_jinja:
            cmd.append("--jinja")
            _eng_logger.info("Enabled --jinja for mtmd-cli (grounding model)")
        # Do NOT pass --special: the flag is not valid for LLAMA_EXAMPLE_MTMD
        # (it throws std::invalid_argument and causes mtmd-cli to exit with
        # empty output). mtmd-cli already emits special/control tokens by
        # default because it calls common_token_to_piece() with special=true.
        _eng_logger.info(f"Running mtmd-cli: {' '.join(cmd)}")
        try:
            subprocess_kwargs = {}
            if os.name == "nt":
                subprocess_kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=kwargs.get("timeout", 600),
                encoding="utf-8",
                errors="replace",
                **subprocess_kwargs,
            )
            output = result.stdout.strip()
            if not output:
                _eng_logger.warning(
                    "mtmd-cli produced empty output. returncode=%d, stderr: %s",
                    result.returncode,
                    result.stderr[:2000] if result.stderr else "(empty)",
                )
            return {
                "response": _sanitize_response(output),
                "model": self.model_path,
                "done": True,
            }
        except subprocess.TimeoutExpired:
            return {"error": "mtmd-cli timed out", "response": ""}
        except Exception as e:
            return {"error": f"mtmd-cli error: {e}", "response": ""}

    def _is_safe_port_owner(self, pid: int) -> bool:
        """True only if *pid* is a llama.cpp binary (a safe kill target).

        The port-scoped cleanup must never terminate an unrelated process
        that happens to listen on the server port — the agent web server
        binds the engine's default port (8081) inside the MAIN app
        process, and killing that PID took the whole desktop app down
        mid-chain.  A PID that no longer exists ("" image name) is also
        skipped: the kernel frees its sockets on its own.
        """
        _img = pid_image_name(pid)
        if not _img:
            _eng_logger.info(
                "Port %d: PID %d already gone — skipping kill",
                self.server_port, pid,
            )
            return False
        if _img not in LLAMA_BIN_NAMES:
            _eng_logger.warning(
                "Port %d: refusing to kill PID %d (image %r is not a "
                "llama.cpp binary)",
                self.server_port, pid, _img,
            )
            return False
        return True

    def _kill_process_on_port(self):
        """Kill the process (if any) listening on ``self.server_port``.

        Port-scoped replacement for the blanket ``taskkill /IM
        llama-server.exe`` sweep: with a second (embedding) llama-server
        running on another port, an image-name sweep would kill it too.
        Only the PID holding OUR port is killed.
        """
        try:
            _net = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True, text=True, timeout=5,
                creationflags=0x08000000 if os.name == "nt" else 0,
            )
            _killed = False
            _pids_to_kill = set()
            for _line in _net.stdout.splitlines():
                _parts = _line.strip().split()
                if len(_parts) >= 2 and f":{self.server_port}" in _parts[1]:
                    _pid = _parts[-1]
                    # Skip non-digit PIDs, our own PID, and PID 0: netstat
                    # reports TIME_WAIT/CLOSE_WAIT sockets whose owner is
                    # already gone as PID 0 — taskkill /PID 0 is a no-op that
                    # merely spams "Killing orphan PID 0" dozens of times.
                    # Those entries expire on their own; only real owning
                    # processes can be (and need to be) killed.
                    if not _pid.isdigit() or int(_pid) <= 0:
                        continue
                    _pid_int = int(_pid)
                    if _pid_int == os.getpid():
                        continue
                    _pids_to_kill.add(_pid_int)
            for _pid_int in sorted(_pids_to_kill):
                if not self._is_safe_port_owner(_pid_int):
                    continue
                _eng_logger.info(
                    "Killing orphan PID %d holding port %d",
                    _pid_int, self.server_port,
                )
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(_pid_int)],
                    capture_output=True, timeout=5,
                    creationflags=0x08000000 if os.name == "nt" else 0,
                )
                _killed = True
            if _killed:
                print(
                    f"[LLAMA.CPP] Killed orphaned llama-server.exe on port "
                    f"{self.server_port} — waiting for port release...",
                    flush=True,
                )
                time.sleep(1.0)  # Wait for the port to fully release
        except subprocess.TimeoutExpired:
            _eng_logger.warning(
                "netstat scan timed out during port-scoped kill"
            )
        except Exception as _kill_exc:
            _eng_logger.debug(
                "Port-scoped kill skipped: %s", _kill_exc
            )

    def _ensure_port_free(self):
        """Wait until the server port is free (no process listening)."""
        import socket as _sock
        import time as _t
        # Reduce retries from 30 to 15 to cap worst-case delay at ~15s.
        # The preemptive port-scoped kill (_kill_process_on_port, called
        # before this) handles the common orphan case; this loop is just a
        # safety net.
        _retries = 15
        while _retries > 0:
            _s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
            _s.settimeout(1)
            try:
                _result = _s.connect_ex(('127.0.0.1', self.server_port))
                _s.close()
                if _result != 0:
                    _eng_logger.info(
                        f"Port {self.server_port} is free"
                    )
                    return True  # port is free
            except:
                _s.close()
                return True
            # Still in use — might be old server shutting down
            print(
                f"[LLAMA.CPP] Port {self.server_port} occupied — "
                f"trying to free ({_retries} retries left)",
                flush=True,
            )
            _eng_logger.info(
                f"Waiting for port {self.server_port} to be freed... "
                f"({_retries} retries left)"
            )
            # Try brute-force kill on Windows
            if os.name == "nt":
                try:
                    _pid_line = subprocess.run(
                        ["netstat", "-ano"],
                        capture_output=True, text=True, timeout=5,
                        creationflags=0x08000000 if os.name == "nt" else 0,
                    )
                    _pids_to_kill = set()
                    for _line in _pid_line.stdout.splitlines():
                        # Match port in the LOCAL address column (index 1),
                        # in ANY state, not just LISTENING.
                        # A killed server's port may be in TIME_WAIT which
                        # is not "LISTENING" but still blocks binding.
                        _parts = _line.strip().split()
                        if len(_parts) >= 2 and f":{self.server_port}" in _parts[1]:
                            _pid = _parts[-1]
                            # Skip non-digit PIDs, our own PID, and PID 0
                            # (TIME_WAIT rows owned by a dead process — the
                            # kernel expires them; taskkill /PID 0 is a no-op).
                            if not _pid.isdigit() or int(_pid) <= 0:
                                continue
                            _pid_int = int(_pid)
                            if _pid_int == os.getpid():
                                continue
                            _pids_to_kill.add(_pid_int)
                    for _pid_int in sorted(_pids_to_kill):
                        if not self._is_safe_port_owner(_pid_int):
                            continue
                        _eng_logger.info(
                            "Killing PID %d holding port %d",
                            _pid_int, self.server_port,
                        )
                        subprocess.run(
                            ["taskkill", "/F", "/PID", str(_pid_int)],
                            capture_output=True, timeout=5,
                            creationflags=0x08000000 if os.name == "nt" else 0,
                        )
                except Exception:
                    pass
            _t.sleep(1)
            _retries -= 1
        _eng_logger.warning(
            f"Port {self.server_port} still in use after timeout"
        )
        print(
            f"[LLAMA.CPP] FATAL: port {self.server_port} still in use "
            "after 15s — giving up",
            flush=True,
        )
        return False

    @staticmethod
    def _find_runtime_dll_dir():
        """Locate the directory containing vcruntime140.dll loaded by THIS process.

        The PyInstaller bootloader may load vcruntime140.dll from _internal/.
        That DLL is KNOWN to be loadable (since the parent process uses it).
        By putting this directory at the front of the child's PATH, we ensure
        the child resolves to the same compatible DLL.
        """
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            hModule = kernel32.GetModuleHandleW("vcruntime140.dll")
            if hModule:
                buf = ctypes.create_unicode_buffer(4096)
                kernel32.GetModuleFileNameW(hModule, buf, 4096)
                dll_path = buf.value
                if dll_path and os.path.exists(dll_path):
                    _eng_logger.debug(
                        "vcruntime140.dll loaded from: %s", dll_path
                    )
                    return os.path.dirname(dll_path)
        except Exception as e:
            _eng_logger.debug("_find_runtime_dll_dir ctypes failed: %s", e)
        # Fallback: check System32 (standard location)
        sys32 = os.path.join(
            os.environ.get("SystemRoot", r"C:\Windows"), "System32"
        )
        candidate = os.path.join(sys32, "vcruntime140.dll")
        if os.path.exists(candidate):
            _eng_logger.debug("vcruntime140.dll found in System32: %s", candidate)
            return sys32
        return None

    @staticmethod
    def _build_minimal_child_env():
        """Build a minimal environment dict for spawning native subprocesses.

        PyInstaller's bootloader extensively modifies os.environ (PATH
        injected with _internal dirs, PYTHON* vars set, etc.).  These
        modifications are designed for the Python runtime, NOT for native
        binaries like llama-server.exe.

        Returns a dict with only essential system variables plus the
        directory of the already-loaded vcruntime140.dll.
        """
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        sys32 = os.path.join(system_root, "System32")

        # Start with the absolute essentials
        path_entries = [
            sys32,
            system_root,
            os.path.join(sys32, "Wbem"),
        ]

        # Prepend the directory containing the KNOWN-GOOD vcruntime140.dll.
        # This ensures the child loads the same CRT that the parent verified.
        rt_dir = LlamaCppEngine._find_runtime_dll_dir()
        if rt_dir and rt_dir not in path_entries:
            path_entries.insert(0, rt_dir)

        minimal = {
            "SystemRoot": system_root,
            "PATH": os.pathsep.join(path_entries),
            "TEMP": os.environ.get("TEMP", os.path.join(system_root, "Temp")),
            "TMP": os.environ.get("TMP", os.path.join(system_root, "Temp")),
            "USERPROFILE": os.environ.get("USERPROFILE", ""),
            "ALLUSERSPROFILE": os.environ.get("ALLUSERSPROFILE", ""),
            "COMPUTERNAME": os.environ.get("COMPUTERNAME", ""),
            "HOMEDRIVE": os.environ.get("HOMEDRIVE", ""),
            "HOMEPATH": os.environ.get("HOMEPATH", ""),
        }
        # Filter out empty values
        return {k: v for k, v in minimal.items() if v}

    def start_server(self, blocking: bool = False) -> bool:
        """
        Start llama.cpp server

        Args:
            blocking: If True, block until server is ready

        Returns:
            bool: True if server started successfully
        """
        if self.is_running:
            # Verify the server is actually alive — is_running can be stale
            # if the subprocess crashed silently (STATUS_ACCESS_VIOLATION)
            # or was orphaned after the parent process restarted.
            # Without this check, HTTP POST to the dead server returns
            # [Errno 22] Invalid argument (WSAEINVAL) on Windows.
            import requests as _health_req
            try:
                _health_resp = _health_req.get(
                    f"{self.server_url}/health", timeout=2
                )
                if _health_resp.status_code == 200:
                    return True
            except Exception:
                pass
            _eng_logger.warning(
                "Server marked as running but health check failed — restarting"
            )
            self.stop_server()

        if not self.model_path or not os.path.exists(self.model_path):
            _eng_logger.error(f"Model not found: {self.model_path}")
            return False

        # ── ONE model per memory pool (load -> use -> unload) ────────────
        # The Laya scorer is a burst resource too; measured 2026-09-23: a
        # resident Laya starved this startup (native context collapsed
        # 262144 -> 1024 tokens) and OOM'd the Vulkan KV-cache allocation.
        # Release it BEFORE the RAM-aware cap below reads available memory —
        # Laya relaunches (~2 s) on its next use.
        try:
            from AI import laya_client as _laya_client
            _laya_client.release(wait=5.0)
        except Exception:
            pass

        # ── RAM-aware native-context resolution ──────────────────────────
        # context_size <= 0 means "model's native training context".  For
        # large-context models on low-RAM machines the native window can
        # OOM the server at startup (KV cache scales linearly with ctx).
        # Estimate the KV cache from GGUF metadata and cap the effective
        # context to what fits in available RAM.  An explicit positive
        # context is honored, but a warning is logged when it looks unsafe.
        try:
            from AI.gguf_model_info import (
                read_model_context_size,
                read_model_kv_params,
                fit_context_to_ram,
                is_moe_model,
            )
            _native_ctx = read_model_context_size(self.model_path)
            _kv_params = read_model_kv_params(self.model_path)
            if self.context_size <= 0 and _native_ctx:
                if _kv_params is not None:
                    _eff_ctx, _capped = fit_context_to_ram(
                        _native_ctx, self.model_path, _kv_params
                    )
                    if _capped:
                        _eng_logger.warning(
                            "Native context %d exceeds available RAM — "
                            "capping effective context to %d (set a manual "
                            "context size in the LLM node UI to override)",
                            _native_ctx, _eff_ctx,
                        )
                        print(
                            f"[LLAMA.CPP] Context capped {_native_ctx} -> "
                            f"{_eff_ctx} tokens (RAM-aware)",
                            flush=True,
                        )
                    self.context_size = _eff_ctx
                else:
                    # KV geometry unknown (unreadable metadata) — the native
                    # window can be huge (e.g. 128k) and OOM the KV cache at
                    # startup.  Cap conservatively; the crash-retry ladder
                    # halves further if even this cannot allocate.
                    _cap_ctx = min(_native_ctx, 8192)
                    _eng_logger.warning(
                        "KV geometry unknown for %s — capping effective "
                        "context to %d tokens (set llamacpp_context_size to "
                        "override)",
                        self.model_path, _cap_ctx,
                    )
                    self.context_size = _cap_ctx
            elif self.context_size > 0 and _kv_params is not None:
                from AI.gguf_model_info import estimate_kv_cache_bytes
                try:
                    import psutil
                    _avail = psutil.virtual_memory().available
                    _est = (
                        estimate_kv_cache_bytes(self.context_size, _kv_params)
                        + os.path.getsize(self.model_path)
                    )
                    if _est > _avail * 0.8:
                        _eng_logger.warning(
                            "Requested context %d may exceed available RAM "
                            "(est. %d MB vs %d MB available) — startup may "
                            "fail; reduce the context size in the LLM node UI",
                            self.context_size,
                            _est // (1024 * 1024),
                            _avail // (1024 * 1024),
                        )
                except Exception:
                    pass
        except Exception as _ctx_exc:
            _eng_logger.debug(
                "RAM-aware context resolution skipped: %s", _ctx_exc
            )

        # ── Preemptive kill: orphaned llama-server.exe on OUR port ──
        # Windows allows orphaned child processes to survive after the parent
        # exits.  These hold the server port, causing _ensure_port_free to
        # timeout for 15+ seconds.  Kill only the process listening on
        # self.server_port (port-scoped) — a blanket `taskkill /IM
        # llama-server.exe` would kill EVERY llama-server, including a
        # concurrently running embedding server on another port.
        if os.name == "nt":
            self._kill_process_on_port()

        # Ensure port is free before starting (old server might still hold it)
        if not self._ensure_port_free():
            _eng_logger.error(
                f"Cannot start server — port {self.server_port} is still in use"
            )
            print(
                f"[LLAMA.CPP] FATAL: port {self.server_port} still in use "
                "after cleanup — aborting",
                flush=True,
            )
            return False

        print(
            f"[LLAMA.CPP] Searching for llama-server.exe binary...",
            flush=True,
        )
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
        # Embedding inputs must fit in a single physical batch (the server
        # refuses to split them); raise --ubatch-size so long inputs work.
        if self.ubatch_size > 0:
            cmd.extend(["--ubatch-size", str(self.ubatch_size)])

        # MoE models (qwen3moe, deepseek2, ...): when offloading to the GPU,
        # keep the expert weights in CPU RAM (--cpu-moe).  Experts dominate
        # an MoE model (~85% of the file), so without this the device-memory
        # budget must hold nearly the whole model — on iGPUs with a small
        # dedicated VRAM (shared system RAM) that OOMs the server at load
        # time (ErrorOutOfDeviceMemory).  With --cpu-moe the device heap
        # only holds attention layers + KV cache.
        _cap = getattr(self, 'capability', None)
        _moe_on_cpu = bool(
            getattr(_cap, 'is_moe', False)
            if _cap is not None
            else is_moe_model(self.model_path)
        )
        if _moe_on_cpu:
            _eng_logger.info(
                "MoE model detected — keeping expert weights on CPU (--cpu-moe)"
            )

        # GPU offload if specified
        if self.gpu_layers > 0:
            cmd.extend(["--gpu-layers", str(self.gpu_layers)])
        if _moe_on_cpu and self.gpu_layers > 0:
            cmd.append("--cpu-moe")

        # Multimodal projector for vision-language models.
        # Only attach mmproj if the model is actually multimodal/grounding —
        # a text-only model with an incompatible mmproj from a sibling model
        # will crash the server with an n_embd mismatch.
        _needs_mmproj = (
            self.mmproj_path
            and os.path.exists(self.mmproj_path)
            and _cap is not None
            and _cap.model_type in ("multimodal", "grounding")
        )
        if _needs_mmproj:
            cmd.extend(["--mmproj", self.mmproj_path])
            _eng_logger.info(f"Using mmproj: {self.mmproj_path}")

        # --special: emit special/control tokens in responses (needed for
        # grounding/LocateAnything models that output <ref>, <box>, <0>..<999> tokens).
        # Only enable for grounding models — unconditionally forcing it on the new
        # mtmd-grounders fork causes text-only + VLM /chat/completions to hang.
        _needs_special = getattr(self, 'capability', None) is not None and self.capability.needs_special_tokens
        if _needs_special:
            cmd.append("--special")
            _eng_logger.info("Enabled --special (grounding model)")

        # Embedding server mode: expose POST /embedding for vector retrieval.
        # Used by a SEPARATE engine instance running an embedding GGUF so the
        # generation server keeps serving completions with its own model.
        if self._embedding_mode:
            cmd.append("--embeddings")
            _eng_logger.info(
                "Enabled --embeddings (embedding server mode, port %d)",
                self.server_port,
            )

        # Subprocess kwargs for Windows (hidden console window)
        subprocess_kwargs = {}
        if os.name == "nt":
            # CREATE_NEW_CONSOLE gives the process valid console handles (needed
            # for CRT initialization), while STARTF_USESHOWWINDOW+SW_HIDE prevents
            # any window from appearing. CREATE_NO_WINDOW zeroes out handles and
            # crashes certain CRT versions with STATUS_ACCESS_VIOLATION.
            creationflags = subprocess.CREATE_NEW_CONSOLE
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            subprocess_kwargs = {
                "creationflags": creationflags,
                "startupinfo": startupinfo,
            }

        try:
            _eng_logger.info(f"Starting server on port {self.server_port}...")
            _eng_logger.info(f"Model: {self.model_path}")
            _eng_logger.info(f"GPU Layers: {self.gpu_layers}, Threads: {self.threads}")
        
            # Capture stderr to a temp file so we can diagnose startup failures
            import tempfile as _tmpf
            _stderr_fd, _stderr_path = _tmpf.mkstemp(
                suffix=".txt", prefix="llama_server_stderr_"
            )
            os.close(_stderr_fd)
            _stderr_file = open(_stderr_path, "wb", buffering=0)
        
            # --- Pre-flight: can the binary even start? ---
            if getattr(sys, 'frozen', False) and os.name == 'nt':
                try:
                    _eng_logger.info("Pre-flight: %s --version", server_exe)
                    _pf_result = subprocess.run(
                        [server_exe, "--version"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    _pf_rc = _pf_result.returncode
                    _pf_out = (_pf_result.stdout or _pf_result.stderr).strip()
                    if _pf_rc == 0:
                        _eng_logger.info(
                            "Pre-flight PASSED (rc=%d): %s",
                            _pf_rc,
                            _pf_out[:300],
                        )
                    else:
                        _eng_logger.error(
                            "Pre-flight FAILED (rc=%d / 0x%08X): %s",
                            _pf_rc,
                            _pf_rc & 0xFFFFFFFF,
                            _pf_out[:300] if _pf_out else "(no output — CRT crash)",
                        )
                        _eng_logger.error(
                            "DLL conflict — llama-server.exe cannot start from "
                            "the compiled environment. vcruntime140_dir=%s, "
                            "System32=%s",
                            LlamaCppEngine._find_runtime_dll_dir() or "NOT FOUND",
                            os.path.join(
                                os.environ.get("SystemRoot", r"C:\Windows"),
                                "System32",
                            ),
                        )
                except FileNotFoundError:
                    _eng_logger.error(
                        "Pre-flight FAILED: binary not found at %s", server_exe
                    )
                except Exception as _pf_exc:
                    _eng_logger.error("Pre-flight FAILED (exception): %s", _pf_exc)
        
            # Build a minimal environment for the child process.
            # PyInstaller's bootloader poisons os.environ with _internal/
            # directories and PYTHON* vars that confuse DLL resolution
            # for native subprocesses.  We give the child ONLY essential
            # system paths plus the directory of the already-loaded CRT.
            _child_env = None
            _child_creationflags = 0
            if getattr(sys, 'frozen', False) and os.name == 'nt':
                # Inherit the parent environment so the server can load bundled
                # VC++ / OpenSSL DLLs from PyInstaller's _internal/ directory.
                # A minimal env (only System32 + vcruntime140 directory) causes
                # STATUS_ACCESS_VIOLATION (0xC0000005) on startup.
                _child_env = None  # inherit — this is what works
                # NOTE: previously _child_creationflags was 0 here, which meant
                # the subprocess_kwargs (CREATE_NEW_CONSOLE + STARTF_USESHOWWINDOW)
                # assembled at lines 587-590 were NEVER passed to Popen.
                # Without CREATE_NEW_CONSOLE the server lacks valid console
                # handles for CRT initialization, which on Windows can cause
                # the server's HTTP parser to fail on POST bodies while
                # succeeding on trivial GET /health — manifesting as
                #   [Errno 22] Invalid argument
                # on the client side when the server RSTs the connection.
                # Restoring the flags here ensures the subprocess gets proper
                # console handles regardless of frozen/source mode.
                _child_creationflags = creationflags if os.name == "nt" else 0

            _spawn_kw = {
                "stdout": subprocess.DEVNULL,
                "stderr": _stderr_file,
            }
            if _child_env is not None:
                _spawn_kw["env"] = _child_env
            if _child_creationflags:
                _spawn_kw["creationflags"] = _child_creationflags

            # Merge the console-hiding flags (startupinfo) so the server window
            # is hidden.  On Windows, this also provides valid console handles
            # for CRT init (via CREATE_NEW_CONSOLE) that are needed whether or
            # not the build is frozen.
            _spawn_kw.update(subprocess_kwargs)

            _eng_logger.info(
                "Starting llama-server (inherit env): %s",
                server_exe,
            )
            self.server_process = subprocess.Popen(cmd, **_spawn_kw)
            self._server_started = True
        
            if blocking:
                # Wait for server to be ready
                import requests
        
                max_wait = 600  # seconds (10 min for large models to load)
                start_time = time.time()
                _server_verified = False
                _last_progress_log = 0.0
                _crash_retry_count = 0
                _max_crash_retries = 3
        
                while time.time() - start_time < max_wait:
                    elapsed = int(time.time() - start_time)
        
                    # Check if server process died
                    _retcode = self.server_process.poll()
                    if _retcode is not None:
                        _stderr_file.flush()
                        _stderr_text = ""
                        try:
                            with open(
                                _stderr_path, "r", encoding="utf-8", errors="replace"
                            ) as _sf:
                                _stderr_text = _sf.read()[-4000:]
                        except Exception:
                            pass
                        msg = (
                            f"llama-server exited with code {_retcode}. "
                            f"Stderr:\n{_stderr_text}"
                        )
                        _eng_logger.error(msg)
                        print(f"[LLAMA.CPP] SERVER DIED (code={_retcode})", flush=True)
                        print(_stderr_text[-1000:], flush=True)
        
                        # Retry with progressively stripped config when the
                        # crash looks recoverable:
                        #   - STATUS_ACCESS_VIOLATION (0xC0000005 = 3221225477):
                        #     DLL/env-related startup crash.
                        #   - device-memory allocation failures (Vulkan/CUDA),
                        #     e.g. "vk::Device::allocateMemory: ErrorOutOfDeviceMemory"
                        #     or "alloc_tensor_range: failed to allocate Vulkan0
                        #     buffer of size ...".  These exit with code 1, so
                        #     gate on the stderr markers instead of the code.
                        _stderr_lower = _stderr_text.lower()
                        _oom_crash = any(
                            _m in _stderr_lower
                            for _m in (
                                "outofdevicememory",
                                "alloc_tensor_range",
                                "failed to allocate",
                                "unable to allocate",
                                "out of memory",
                                "cuda error",
                            )
                        )
                        if (
                            _retcode == 3221225477 or _oom_crash
                        ) and _crash_retry_count < _max_crash_retries:
                            _crash_retry_count += 1
                            # Close old stderr capture
                            _stderr_file.close()
                            try:
                                os.remove(_stderr_path)
                            except Exception:
                                pass
                            # Create new stderr capture
                            _stderr_fd2, _stderr_path = _tmpf.mkstemp(
                                suffix=".txt", prefix="llama_server_stderr_"
                            )
                            os.close(_stderr_fd2)
                            _stderr_file = open(_stderr_path, "wb", buffering=0)
        
                            # Decide what to strip based on retry count
                            # A KV-cache/context-init allocation failure (CPU
                            # RAM) is NOT fixed by GPU/mmproj/mmap stripping —
                            # the direct fix is shrinking --ctx-size.  The
                            # RAM-aware startup cap can still miss when memory
                            # is tight at load time (embedding server, browser
                            # and other models already resident), so each
                            # retry halves the window until it fits.
                            if (
                                _is_kv_cache_oom(_stderr_text)
                                and self.context_size > 512
                            ):
                                _eng_logger.warning(
                                    f"KV cache allocation failed at context "
                                    f"{self.context_size} — retrying with a "
                                    "smaller context"
                                )
                                print(
                                    "[LLAMA.CPP] KV cache OOM — halving "
                                    f"context to "
                                    f"{max(512, self.context_size // 2)}",
                                    flush=True,
                                )
                                self.context_size = max(
                                    512, self.context_size // 2
                                )
                            elif _crash_retry_count == 1 and self.gpu_layers > 0:
                                # Attempt 1: disable GPU layers
                                _eng_logger.warning(
                                    f"GPU layers ({self.gpu_layers}) caused crash, "
                                    "retrying with --gpu-layers 0"
                                )
                                print(
                                    "[LLAMA.CPP] GPU crash \u2014 retrying without GPU layers",
                                    flush=True,
                                )
                                self.gpu_layers = 0
                            elif _crash_retry_count == 2 and hasattr(self, 'mmproj_path') and self.mmproj_path:
                                # Attempt 2: disable mmproj (projector weights may cause AV)
                                _eng_logger.warning(
                                    "Still crashing after GPU fallback, "
                                    "retrying without mmproj"
                                )
                                print(
                                    "[LLAMA.CPP] Crash persists \u2014 retrying without mmproj",
                                    flush=True,
                                )
                                self.mmproj_path = ""
                            elif _crash_retry_count == 3:
                                # Attempt 3: disable memory-mapped loading
                                _eng_logger.warning(
                                    "Still crashing after mmproj fallback, "
                                    "retrying with --no-mmap"
                                )
                                print(
                                    "[LLAMA.CPP] Crash persists \u2014 retrying with --no-mmap",
                                    flush=True,
                                )
        
                            # Rebuild command based on current config
                            cmd = [server_exe, "--model", self.model_path,
                                   "--port", str(self.server_port),
                                   "--threads", str(self.threads),
                                   "--ctx-size", str(self.context_size),
                                   "--batch-size", str(self.batch_size),
                                   "--host", "127.0.0.1"]
                            if self.ubatch_size > 0:
                                cmd.extend(["--ubatch-size", str(self.ubatch_size)])
                            if self._embedding_mode:
                                cmd.append("--embeddings")
                            if self.gpu_layers > 0:
                                cmd.extend(["--gpu-layers", str(self.gpu_layers)])
                            if _moe_on_cpu and self.gpu_layers > 0:
                                cmd.append("--cpu-moe")
                            _cap = getattr(self, 'capability', None)
                            _needs_mmproj = (
                                self.mmproj_path
                                and os.path.exists(self.mmproj_path)
                                and _cap is not None
                                and _cap.model_type in ("multimodal", "grounding")
                            )
                            if _needs_mmproj:
                                cmd.extend(["--mmproj", self.mmproj_path])
                            if _cap is not None and _cap.needs_special_tokens:
                                cmd.append("--special")
                            if _crash_retry_count >= 3:
                                cmd.append("--no-mmap")
        
                            _eng_logger.info(
                                f"Restarting server (attempt {_crash_retry_count + 1})..."
                            )
                            # Spawn strategy progression:
                            #   retry 1: inherit env (no flags) — proven to work
                            #   retry 2: shell=True + inherit env
                            #   retry 3: minimal env + DETACHED (last resort)
                            _retry_cmd = cmd
                            _retry_kw = {}
                            _retry_env = None  # inherit
                            _retry_flags = 0
                            if _crash_retry_count == 0:
                                _eng_logger.info(
                                    "Retry %d: inherit env (no flags)",
                                    _crash_retry_count + 1,
                                )
                            elif _crash_retry_count == 1:
                                _retry_cmd = subprocess.list2cmdline(cmd) if os.name == 'nt' else cmd
                                _retry_kw = {'shell': True} if os.name == 'nt' else {}
                                _eng_logger.info(
                                    "Retry %d: shell=True + inherit env",
                                    _crash_retry_count + 1,
                                )
                            else:
                                _retry_cmd = subprocess.list2cmdline(cmd) if os.name == 'nt' else cmd
                                _retry_kw = {'shell': True} if os.name == 'nt' else {}
                                _retry_env = LlamaCppEngine._build_minimal_child_env()
                                _retry_flags = 0
                                _eng_logger.info(
                                    "Retry %d: shell=True + minimal env (last resort)",
                                    _crash_retry_count + 1,
                                )
                            _popen_args = {
                                "stdout": subprocess.DEVNULL,
                                "stderr": _stderr_file,
                            }
                            if _retry_env is not None:
                                _popen_args["env"] = _retry_env
                            if _retry_flags and not _retry_kw.get('shell'):
                                _popen_args["creationflags"] = _retry_flags
                            _popen_args.update(_retry_kw)
                            self.server_process = subprocess.Popen(
                                _retry_cmd, **_popen_args
                            )
                            self._server_started = True
                            # Reset start_time so we give each attempt a full chance
                            start_time = time.time()
                            continue
                        else:
                            break
        
                    try:
                        # Fresh session + Connection: close — same rationale
                        # as generate() to avoid stale pooled sockets causing
                        # [Errno 22] on Windows.
                        import requests as _req_health
                        _health_session = _req_health.Session()
                        _health_session.headers["Connection"] = "close"
                        response = _health_session.get(
                            f"{self.server_url}/health", timeout=2
                        )
                        _health_session.close()
                        if response.status_code == 200:
                            _model_fname = os.path.basename(self.model_path)
                            loaded_model = response.text.strip()[:200]
                            # /health returns {"status":"ok"} in current llama-server
                            # — it does NOT include the model name.
                            # The port lifecycle (_ensure_port_free + stop_server)
                            # already guarantees no stale server is on this port,
                            # so a 200 OK is sufficient to confirm it's our server.
                            if _model_fname.lower() in loaded_model.lower():
                                _eng_logger.info(
                                    f"Server ready at {self.server_url} "
                                    f"(model: {_model_fname})"
                                )
                                print(
                                    f"[LLAMA.CPP] Server ready ({_model_fname})",
                                    flush=True,
                                )
                            else:
                                _eng_logger.info(
                                    f"Server ready at {self.server_url} "
                                    f"(model: {_model_fname}, "
                                    f"health: {loaded_model})"
                                )
                                print(
                                    f"[LLAMA.CPP] Server ready ({_model_fname})",
                                    flush=True,
                                )
                            self.is_running = True
                            _server_verified = True
                            # Cleanup stderr capture on success
                            _stderr_file.close()
                            try:
                                os.remove(_stderr_path)
                            except Exception:
                                pass

                            # Stabilization delay: the health check confirms the
                            # server is listening, but the internal model-loading
                            # thread may still be initialising.  A tiny pause
                            # reduces the risk that the first POST arrives before
                            # /completion is ready, which on Windows can manifest
                            # as [Errno 22] Invalid argument.
                            time.sleep(0.5)
                            return True
                    except requests.exceptions.ConnectionError:
                        # Connection refused \u2014 server hasn't started listening yet
                        if elapsed - _last_progress_log >= 15:
                            _last_progress_log = elapsed
                            _eng_logger.info(
                                f"Waiting for server to start listening... "
                                f"({elapsed}s elapsed)"
                            )
                            print(
                                f"[LLAMA.CPP] Waiting for server... ({elapsed}s)",
                                flush=True,
                            )
                    except requests.exceptions.Timeout:
                        # Server is there but too busy to respond
                        if elapsed - _last_progress_log >= 15:
                            _last_progress_log = elapsed
                            _eng_logger.warning(
                                f"Server not responding yet (timeout, {elapsed}s)"
                            )
                            # Surface the server's own stderr so a model-load
                            # hang (e.g. LFM2.5-VL "incomplete KV geometry" /
                            # mmproj stall after a per-node model switch) is
                            # visible while it happens — never a silent
                            # 600s wait.  The last 400 chars usually carry
                            # the load stage.
                            try:
                                with open(
                                    _stderr_path, "r", encoding="utf-8",
                                    errors="replace",
                                ) as _sf:
                                    _tail = _sf.read()[-400:].strip()
                                if _tail:
                                    _eng_logger.warning(
                                        "llama-server stderr tail: %s", _tail,
                                    )
                            except Exception:
                                pass
                    except Exception as e:
                        if elapsed - _last_progress_log >= 30:
                            _last_progress_log = elapsed
                            _eng_logger.warning(
                                f"Health check error at {elapsed}s: {e}"
                            )
        
                    time.sleep(1)
        
                # Cleanup stderr capture
                _stderr_file.close()
                try:
                    with open(
                        _stderr_path, "r", encoding="utf-8", errors="replace"
                    ) as _sf:
                        _final_stderr = _sf.read()[-2000:]
                except Exception:
                    _final_stderr = ""
                try:
                    os.remove(_stderr_path)
                except Exception:
                    pass
        
                if not _server_verified:
                    _eng_logger.error(
                        f"Server failed to start within {max_wait} seconds. "
                        f"Final stderr:\n{_final_stderr}"
                    )
                    print(
                        f"[LLAMA.CPP] Server FAILED after {max_wait}s. "
                        f"Stderr:\n{_final_stderr[-1000:]}",
                        flush=True,
                    )
                    return False
            else:
                # Non-blocking - assume it will start
                self.is_running = True
                return True

        except Exception as e:
            _eng_logger.error(f"Failed to start server: {e}")
            return False

    def stop_server(self, quick: bool = False):
        """Stop llama.cpp server and ensure the port is released

        Args:
            quick: If True, only terminate this engine's own tracked process
                and skip the port-release sweep.  Used by the background
                per-node model unload, where the next request's
                start_server() performs its own port cleanup — the sweep
                would otherwise race a concurrently starting server on the
                same port and kill it.
        """
        # Only an engine that actually spawned a server via start_server() can
        # hold our port.  Engines that never started (test doubles, failed
        # starts) have no port to release — skipping the scan below avoids a
        # multi-attempt socket+netstat round-trip (~30s on slow machines) and
        # prevents killing unrelated processes that happen to listen on the
        # port.
        _was_started = self._server_started
        if self.server_process:
            try:
                _eng_logger.info("Stopping server...")
                self.server_process.terminate()
                self.server_process.wait(timeout=10)
            except Exception as e:
                _eng_logger.error(f"Error stopping server: {e}")
                try:
                    self.server_process.kill()
                except Exception:
                    pass
            finally:
                self.server_process = None
                self.is_running = False
                self._server_started = False
        else:
            self.is_running = False
            self._server_started = False

        # On Windows, use taskkill as a brute-force backup to ensure the port is freed.
        # Skipped for quick stops (background per-node unload): the next
        # start_server() -> _ensure_port_free() handles any leftover process.
        if os.name == "nt" and _was_started and not quick:
            import time as _t
            _retries = 0
            while _retries < 15:
                # Check if something is still listening on the port
                import socket as _sock
                _s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
                _s.settimeout(1)
                try:
                    _result = _s.connect_ex(('127.0.0.1', self.server_port))
                    _s.close()
                    if _result != 0:
                        break  # port is free
                except:
                    _s.close()
                    break
                # Port still occupied — forcefully kill any process on it
                try:
                    _pid_line = subprocess.run(
                        ["netstat", "-ano"],
                        capture_output=True, text=True, timeout=5,
                        creationflags=0x08000000 if os.name == "nt" else 0,
                    )
                    for _line in _pid_line.stdout.splitlines():
                        if f":{self.server_port}" in _line and "LISTENING" in _line.upper():
                            _parts = _line.strip().split()
                            if _parts:
                                _pid = _parts[-1]
                                # NEVER kill the current process
                                if _pid == str(os.getpid()):
                                    _eng_logger.warning(
                                        "stop_server: port %d held by current "
                                        "process (PID %s) — skipping self-kill",
                                        self.server_port, _pid,
                                    )
                                    continue
                                # Only kill llama.cpp binaries — never an
                                # unrelated process on the port (e.g. the
                                # agent web server in the main app process).
                                if not _pid.isdigit() or not self._is_safe_port_owner(int(_pid)):
                                    continue
                                _eng_logger.info(
                                    "stop_server: killing PID %s holding port %d",
                                    _pid, self.server_port,
                                )
                                subprocess.run(
                                    ["taskkill", "/F", "/PID", _pid],
                                    capture_output=True, timeout=5,
                                    creationflags=0x08000000 if os.name == "nt" else 0,
                                )
                                _t.sleep(1)
                                break
                except Exception:
                    pass
                _retries += 1
                _t.sleep(1)

        _eng_logger.info(
            f"Server stopped (port {self.server_port} released)"
        )

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
        import requests as _req_lib

        if not self.is_running:
            return {"error": "Server not running", "response": ""}

        # ── Process health check ────────────────────────────────────────────
        # Before making any HTTP request, verify the server subprocess is still
        # alive.  If the server crashed silently (e.g. during model loading),
        # the port may be in TIME_WAIT and any attempt to connect will fail
        # with [Errno 22] Invalid argument on Windows.
        if self.server_process and self.server_process.poll() is not None:
            _rc = self.server_process.poll()
            _eng_logger.error(
                "Server process died before generate() (rc=%d)", _rc
            )
            self.is_running = False
            return {
                "error": f"Server process terminated (rc={_rc})",
                "response": "",
            }

        # Build request payload
        # Ollama-parity sampling: llama.cpp's server defaults differ from
        # Ollama's (top_p 0.95, repeat_penalty 1.0 = disabled) and the
        # difference shows up as repetition/hallucination on small quantized
        # models.  Always send the explicit set below.
        payload = {
            "prompt": prompt,
            # The caller's max_tokens budgets the ANSWER; the cap is widened so
            # the chain of thought cannot spend the answer's allowance.
            "n_predict": _budget_for_final_answer(kwargs.get("max_tokens", -1)),
            "temperature": kwargs.get("temperature", 0.7),
            "top_k": kwargs.get("top_k", 40),
            "top_p": kwargs.get("top_p", 0.9),
            "min_p": kwargs.get("min_p", 0.05),
            "repeat_penalty": kwargs.get("repeat_penalty", 1.1),
            "stream": kwargs.get("stream", False),
        }

        # Add stop sequences if provided
        if "stop" in kwargs and kwargs["stop"]:
            payload["stop"] = (
                kwargs["stop"] if isinstance(kwargs["stop"], list) else [kwargs["stop"]]
            )

        # Connection retry: the server may have just finished starting
        # and its socket is not yet fully accepting POST requests.
        # Retry up to 3 times with 1s delay.
        #
        # CRITICAL: Use a FRESH requests.Session with Connection: close for
        # each attempt.  The global requests session's connection pool can
        # retain stale sockets from the earlier health-check GET, and on
        # Windows, reusing a stale connection causes:
        #   [Errno 22] Invalid argument  (WSAEINVAL)
        # because the underlying socket is in a transitional state
        # (TIME_WAIT / CLOSE_WAIT).  Fresh sessions + Connection: close
        # prevent any pool reuse.
        #
        # See: https://github.com/psf/requests/issues/5437
        # See: https://learn.microsoft.com/en-us/windows/win32/api/winsock2/
        #      nf-winsock2-wsasend#return-value  (WSAEINVAL=10022)
        _last_exc = None
        _result = None
        _terminal_4xx = False
        for _attempt in range(3):
            _session = _req_lib.Session()
            _session.headers["Connection"] = "close"
            try:
                response = _session.post(
                    f"{self.server_url}/completion",
                    json=payload,
                    # No inference timeout — local models generate at ~10 tok/s
                    # on low-end hardware, so a 1024-token request can take
                    # 100s+.  A fixed 30s cap aborts mid-generation and every
                    # retry restarts from scratch, so the request NEVER
                    # completes.  Stalls are handled by the executor, which
                    # already POSTs with timeout=None.
                    timeout=None,
                )
                response.raise_for_status()
                _result = response.json()
                break

            except _req_lib.exceptions.Timeout:
                _last_exc = "Request timeout"
                print(
                    f"[LLAMA.CPP] /completion attempt {_attempt + 1} timed out — retrying...",
                    flush=True,
                )
                time.sleep(1)
            except _req_lib.exceptions.ConnectionError as _ce:
                _last_exc = str(_ce)
                print(
                    f"[LLAMA.CPP] /completion attempt {_attempt + 1} "
                    f"connection error ({_ce}) — retrying...",
                    flush=True,
                )
                time.sleep(1)
            except Exception as _e:
                _last_exc = str(_e)
                # Duck-typed 4xx detection (works with real requests and the
                # test-suite fake): HTTPError carries ``.response.status_code``.
                _resp_obj = getattr(_e, "response", None)
                _status = (
                    getattr(_resp_obj, "status_code", None)
                    if _resp_obj is not None
                    else None
                )
                if _status is not None and _status < 500:
                    # Deterministic client rejection (e.g. context overflow
                    # 400 "exceed_context_size_error").  Retrying the
                    # identical payload can never succeed — surface it
                    # immediately so the caller trims the prompt or falls
                    # back instead of burning 3 attempts on a no-op.
                    _terminal_4xx = True
                    break
                print(
                    f"[LLAMA.CPP] /completion attempt {_attempt + 1} "
                    f"error ({type(_e).__name__}: {_e}) — retrying...",
                    flush=True,
                )
                time.sleep(1)
            finally:
                _session.close()

        if _result is None and _terminal_4xx:
            # 4xx is a client error, not a server failure — do NOT clear
            # is_running (the server process is alive and healthy).
            _eng_logger.error(
                "/completion rejected (deterministic 4xx): %s", _last_exc
            )
            return {"error": _last_exc, "response": ""}

        if _result is None:
            _eng_logger.error(
                "/completion failed after 3 attempts: %s", _last_exc
            )
            print(
                f"[LLAMA.CPP] FATAL: /completion failed after 3 attempts: {_last_exc}",
                flush=True,
            )
            # The server is effectively unusable — clear is_running so a later
            # start_server() actually restarts instead of trusting the stale flag
            # (which previously caused [Errno 22] on every subsequent POST).
            self.is_running = False
            return {"error": _last_exc or "/completion failed", "response": ""}

        # First-round text.  Thinking models that hit the token budget are
        # continued below until they finish naturally (see the continuation
        # helper) — never return a half-written response.
        _resp_text = _result.get("content", "") or ""
        _truncated = bool(_result.get("truncated"))
        _usage = {
            "prompt_tokens": _result.get("tokens_evaluated", 0),
            "completion_tokens": _result.get("tokens_predicted", 0),
        }
        if _truncated and _resp_text:
            _cont_text, _cont_usage, _still_truncated = (
                self._completion_with_continuation(
                    payload["prompt"] + "\n" + _resp_text,
                    None,
                    kwargs.get("max_tokens", -1),
                    kwargs.get("temperature", 0.7),
                    kwargs.get("top_p", 0.9),
                    repeat_penalty=kwargs.get("repeat_penalty", 1.1),
                    # Short structured callers (consolidation hooks) cap this
                    # to 1 so a rambling small model is never continued for
                    # minutes; narrative callers keep the default.
                    max_rounds=int(kwargs.get("max_rounds") or 8),
                )
            )
            _resp_text += _cont_text
            try:
                _usage["completion_tokens"] = (
                    (_usage.get("completion_tokens") or 0)
                    + (_cont_usage.get("completion_tokens") or 0)
                )
            except Exception:
                pass
            _truncated = _still_truncated

        return {
            # Raw /completion does not parse reasoning — strip thinking
            # blocks so the FINAL ANSWER (never the chain of thought) is
            # what the caller receives.
            "response": _sanitize_response(
                _strip_reasoning_blocks(_resp_text)
            ),
            "model": self.model_path,
            "usage": _usage,
            "done": True,
            "truncated": _truncated,
        }

    # ------------------------------------------------------------------
    # Truncation-aware generation helpers (reasoning-model support)
    # ------------------------------------------------------------------

    def _completion_with_continuation(
        self,
        prompt: str,
        image_data: Optional[List[Dict[str, Any]]],
        max_tokens: Any,
        temperature: float,
        top_p: float,
        repeat_penalty: float = 1.1,
        max_rounds: int = 8,
    ) -> tuple:
        """POST /completion repeatedly, continuing truncated generations.

        The raw completion endpoint stops when ``n_predict`` (or the context)
        is exhausted.  Each continuation round re-sends the prompt with the
        accumulated output appended so the model picks up exactly where it
        stopped instead of restarting from scratch.

        Returns ``(content, usage, truncated)``.  ``content`` is the raw
        accumulated text — thinking blocks included, callers strip them.
        """
        acc = ""
        usage: Dict[str, Any] = {}
        truncated = False
        import requests as _req_cont

        for _round in range(1, max_rounds + 1):
            _payload = {
                "prompt": prompt,
                # ``max_tokens`` is the FINAL-ANSWER budget; n_predict counts
                # the chain of thought too, so the cap is widened to match.
                "n_predict": _budget_for_final_answer(max_tokens),
                "temperature": temperature,
                "top_k": 40,
                "top_p": top_p,
                "min_p": 0.05,
                "repeat_penalty": repeat_penalty,
                "stream": False,
            }
            if image_data:
                _payload["image_data"] = image_data
            try:
                _sess = _req_cont.Session()
                _sess.headers["Connection"] = "close"
                _resp = _sess.post(
                    f"{self.server_url}/completion",
                    json=_payload,
                    timeout=None,  # No inference timeout — see generate()
                )
                _sess.close()
                if _resp.status_code != 200:
                    _eng_logger.warning(
                        "/completion continuation round %d returned %d: %s",
                        _round, _resp.status_code, _resp.text[:200],
                    )
                    break
                _res = _resp.json()
            except Exception as _e:
                _eng_logger.warning(
                    "/completion continuation round %d failed: %s",
                    _round, _e,
                )
                break

            _chunk = ""
            for _key in ("content", "text", "response", "completion"):
                _val = _res.get(_key)
                if _val and isinstance(_val, str):
                    _chunk = _val
                    break
            _round_usage = {
                "prompt_tokens": _res.get("tokens_evaluated", 0),
                "completion_tokens": _res.get("tokens_predicted", 0),
            }
            if _round == 1:
                usage = _round_usage
            else:
                try:
                    usage["completion_tokens"] = (
                        (usage.get("completion_tokens") or 0)
                        + (_round_usage.get("completion_tokens") or 0)
                    )
                except Exception:
                    pass
            acc += _chunk
            truncated = bool(_res.get("truncated"))
            if not truncated:
                break
            if not _chunk:
                _eng_logger.warning(
                    "/completion truncated with no new text — stopping continuation"
                )
                break
            prompt = prompt + "\n" + _chunk
        return acc, usage, truncated

    def _post_chat_completion(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: Any,
        temperature: float,
        top_p: float,
        repeat_penalty: float = 1.1,
        continue_final: bool = False,
        chat_template_kwargs: dict | None = None,
    ) -> tuple:
        """Single POST /chat/completions.

        Returns ``(ok, result, content, reasoning, finish_reason)``.  ``ok``
        is False when the request itself failed (non-200 or exception), so
        callers can fall back to /completion.  ``reasoning`` is the model's
        chain of thought (``reasoning_content``) — NEVER the response.
        """
        _payload = {
            "messages": messages,
            # ``max_tokens`` is the FINAL-ANSWER budget; the server counts the
            # chain of thought too, so the cap is widened to match.
            "max_tokens": _budget_for_final_answer(max_tokens),
            "temperature": temperature,
            "top_k": 40,
            "top_p": top_p,
            "min_p": 0.05,
            "repeat_penalty": repeat_penalty,
            "stream": False,
        }
        if continue_final:
            # vLLM-compatible: continue from the (partial) assistant message
            # instead of starting a brand-new turn.  The bundled llama.cpp
            # server defaults add_generation_prompt to true and rejects the
            # combination (400 "Cannot set both...") — send it explicitly
            # false so truncated generations actually continue.
            _payload["continue_final_message"] = True
            _payload["add_generation_prompt"] = False
        if chat_template_kwargs:
            # e.g. {"enable_thinking": false} — strict one-token callers
            # (orchestrator picker, decision gates) must not spend the
            # budget on a reasoning block.
            _payload["chat_template_kwargs"] = dict(chat_template_kwargs)
        import requests as _req_cc

        _cc_session = _req_cc.Session()
        _cc_session.headers["Connection"] = "close"
        try:
            _resp = _cc_session.post(
                f"{self.server_url}/chat/completions",
                json=_payload,
                # No inference timeout — see generate() above.  A 30s cap
                # kills slow-but-progressing local generations and the
                # fallback retries restart from zero, never completing.
                timeout=None,
            )
            if _resp.status_code != 200:
                _eng_logger.warning(
                    "/chat/completions returned %d: %s",
                    _resp.status_code, _resp.text[:300],
                )
                return False, {}, "", "", None
            _result = _resp.json()
        except Exception as _e:
            _eng_logger.warning("/chat/completions post failed: %s", _e)
            return False, {}, "", "", None
        finally:
            _cc_session.close()

        _content = ""
        _reasoning = ""
        _finish_reason = None
        _choices = _result.get("choices")
        if _choices and isinstance(_choices, list) and len(_choices) > 0:
            _choice = _choices[0] if isinstance(_choices[0], dict) else {}
            _msg = _choice.get("message", {}) if isinstance(_choice.get("message"), dict) else {}
            _content = _msg.get("content", "") or ""
            _reasoning = _msg.get("reasoning_content", "") or ""
            _finish_reason = _choice.get("finish_reason")
        # Top-level content key (some server versions / OpenAI text compat)
        if not _content and not _reasoning:
            for _key in ("content", "text", "response", "completion"):
                _val = _result.get(_key)
                if _val and isinstance(_val, str):
                    _content = _val
                    break
        return True, _result, _content, _reasoning, _finish_reason

    def _chat_with_continuation(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: Any,
        temperature: float,
        top_p: float,
        repeat_penalty: float = 1.1,
        max_rounds: int = 8,
        chat_template_kwargs: dict | None = None,
    ) -> tuple:
        """Run /chat/completions, continuing truncated generations.

        Thinking models are frequently cut off while still reasoning or
        mid-answer when they hit the token budget (``finish_reason ==
        "length"``).  Each truncated round is continued with
        ``continue_final_message``, feeding the accumulated partial reasoning
        AND partial answer back so the model picks up exactly where it
        stopped.  ``message.reasoning_content`` (the thinking process) is
        NEVER used as the response — only ``message.content`` (the final
        answer) qualifies.

        Returns ``(ok, content, reasoning, usage, truncated)``.  ``ok`` is
        False when the first round failed (caller falls back to /completion).
        """
        _acc_content = ""
        _acc_reasoning = ""
        _usage: Dict[str, Any] = {}
        _truncated = False
        _ok = True
        _cont_messages = messages

        for _round in range(1, max_rounds + 1):
            _ok_r, _result, _content, _reasoning, _finish = (
                self._post_chat_completion(
                    _cont_messages,
                    max_tokens,
                    temperature,
                    top_p,
                    repeat_penalty=repeat_penalty,
                    continue_final=(_round > 1),
                    chat_template_kwargs=chat_template_kwargs,
                )
            )
            if not _ok_r:
                if _round == 1:
                    _ok = False
                break
            if _round == 1:
                _usage = dict(_result.get("usage", {}) or {})
            else:
                _u = _result.get("usage", {}) or {}
                try:
                    _usage["completion_tokens"] = (
                        (_usage.get("completion_tokens") or 0)
                        + (_u.get("completion_tokens") or 0)
                    )
                except Exception:
                    pass
            _acc_content += _content
            _acc_reasoning += _reasoning
            _eng_logger.info(
                "/chat/completions round %d: %d chars content, %d chars "
                "reasoning, finish_reason=%s",
                _round, len(_content), len(_reasoning), _finish,
            )
            if _finish != "length":
                _truncated = False
                break
            _truncated = True
            if not _content and not _reasoning:
                _eng_logger.warning(
                    "/chat/completions truncated with no new text — "
                    "stopping continuation"
                )
                break
            # Feed the partial output back as an assistant message so the
            # model continues its chain of thought / answer in place.
            _partial: Dict[str, Any] = {
                "role": "assistant",
                "content": _acc_content,
            }
            if _acc_reasoning:
                _partial["reasoning_content"] = _acc_reasoning
            _cont_messages = list(messages) + [_partial]

        return _ok, _acc_content, _acc_reasoning, _usage, _truncated

    def probe(self) -> bool:
        """Perform a real 1-token generation to verify the server can infer.

        A health check only proves the server is listening; a real completion
        proves the model is loaded and POST bodies are accepted.  Returns
        True when the probe succeeds; the caller decides how to surface a
        failure (e.g. restart the server or abort startup).
        """
        if not self.is_running:
            _eng_logger.warning("probe(): server not running")
            return False
        try:
            result = self.generate("ping", max_tokens=1, temperature=0.1)
        except Exception as exc:
            _eng_logger.error("probe(): exception: %s", exc)
            return False
        if result.get("error"):
            _eng_logger.error("probe(): generation failed: %s", result["error"])
            return False
        _eng_logger.info(
            "probe(): 1-token generation OK (model: %s)",
            os.path.basename(self.model_path or ""),
        )
        return True

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
            # Same final-answer rule as generate(): reasoning is not charged to
            # the caller's budget.
            "n_predict": _budget_for_final_answer(kwargs.get("max_tokens", -1)),
            "temperature": kwargs.get("temperature", 0.7),
            "top_k": kwargs.get("top_k", 40),
            "top_p": kwargs.get("top_p", 0.9),
            "min_p": kwargs.get("min_p", 0.05),
            "repeat_penalty": kwargs.get("repeat_penalty") or 1.1,
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

    def chat_stream(
        self, messages: List[Dict[str, Any]], **kwargs
    ) -> Generator[str, None, None]:
        """Stream chat completions via /chat/completions.

        Unlike :meth:`generate_stream` (raw /completion), this applies the
        model's chat template server-side, so system/user roles and special
        tokens are formatted correctly.  Used by the /llamacpp/generate_stream
        endpoint; the executor's non-streaming path is unaffected.

        Yields:
            Content deltas as they are generated.
        """
        import requests

        if not self.is_running:
            yield "[Error: Server not running]"
            return

        _payload = {
            "messages": messages,
            # Same final-answer rule as the non-streaming chat path.
            "max_tokens": _budget_for_final_answer(kwargs.get("max_tokens", -1)),
            "temperature": kwargs.get("temperature", 0.7),
            "top_k": kwargs.get("top_k", 40),
            "top_p": kwargs.get("top_p", 0.9),
            "min_p": kwargs.get("min_p", 0.05),
            "repeat_penalty": kwargs.get("repeat_penalty") or 1.1,
            "stream": True,
        }
        try:
            response = requests.post(
                f"{self.server_url}/chat/completions",
                json=_payload,
                stream=True,
                timeout=None,  # No timeout — wait for model to complete
            )
            response.raise_for_status()

            for line in response.iter_lines():
                if not line:
                    continue
                _line = line.decode("utf-8", errors="replace").strip()
                if not _line.startswith("data:"):
                    continue
                _data_str = _line[len("data:"):].strip()
                if _data_str == "[DONE]":
                    break
                try:
                    _data = json.loads(_data_str)
                except Exception:
                    continue
                # In-band server errors (context overflow, slot aborts, ...)
                # arrive as SSE "error" objects — raise so the caller sees a
                # real failure instead of a silently truncated reply.
                if isinstance(_data, dict) and _data.get("error"):
                    raise RuntimeError(str(_data.get("error")))
                try:
                    _choices = _data.get("choices") or []
                    if _choices:
                        _delta = (_choices[0].get("delta") or {}).get("content", "")
                        if _delta:
                            yield _delta
                        # finish_reason="length" means the model hit the token
                        # budget mid-reply — the caller must know it is truncated.
                        if (_choices[0].get("finish_reason") == "length"
                                and not _delta):
                            raise RuntimeError(
                                "max_tokens reached - response truncated"
                            )
                except Exception:
                    raise
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

        # Extract multimodal images if any message has content as a list.
        # Done BEFORE the is_running guard: grounding models (LocateAnything)
        # run their PBD inference in llama-mtmd-cli without a server at all,
        # so multimodal grounding requests must proceed when no server is
        # loaded (the per-node lifecycle unloads the server after each use).
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

        _cap = getattr(self, 'capability', None)
        _grounding_multimodal = bool(
            _cap is not None
            and _cap.needs_mtmd_cli
            and has_multimodal
            and image_base64_list
        )
        if not self.is_running and not _grounding_multimodal:
            return {"error": "Server not running", "response": ""}

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
        
            _cap = getattr(self, 'capability', None)
            _is_grounding = _cap is not None and _cap.needs_mtmd_cli
        
            # ------------------------------------------------------------------
            # Path A: /chat/completions with images
            #   Attempted ONLY for non-grounding multimodal models.
            #   Grounding models (LocateAnything) use PBD (Parallel Box
            #   Decoding) which is NOT supported by the server's standard
            #   /chat/completions endpoint — it outputs generic OCR labels
            #   instead of proper grounded detections.
            #   mtmd-cli is the only code path with PBD support, so we
            #   skip Path A entirely for grounding models.
            # ------------------------------------------------------------------
            if not _is_grounding:
                try:
                    _chat_payload = {
                        "messages": messages,
                        "max_tokens": _budget_for_final_answer(
                            kwargs.get("max_tokens", -1)
                        ),
                        "temperature": kwargs.get("temperature", 0.7),
                        "top_k": kwargs.get("top_k", 40),
                        "top_p": kwargs.get("top_p", 0.9),
                        "min_p": kwargs.get("min_p", 0.05),
                        "repeat_penalty": kwargs.get("repeat_penalty", 1.1),
                        "stream": False,
                    }
                    _chat_resp = requests.post(
                        f"{self.server_url}/chat/completions",
                        json=_chat_payload,
                        timeout=None,
                    )
                    if _chat_resp.status_code == 200:
                        _chat_result = _chat_resp.json()
                        _resp_text = ""
                        _reasoning_text = ""
                        _finish_reason = None
                        _choices = _chat_result.get("choices")
                        if _choices and isinstance(_choices, list) and len(_choices) > 0:
                            _msg = _choices[0].get("message", {}) if isinstance(_choices[0], dict) else {}
                            _resp_text = _msg.get("content", "") if isinstance(_msg, dict) else ""
                            # NEVER fall back to reasoning_content: the thinking
                            # process is not the answer.  Outputs cut off while
                            # still thinking fall through to Path C, which
                            # regenerates and strips any thinking from the text.
                            _reasoning_text = _msg.get("reasoning_content", "") if isinstance(_msg, dict) else ""
                            if isinstance(_choices[0], dict):
                                _finish_reason = _choices[0].get("finish_reason")
                        _resp_text = _strip_reasoning_blocks(_resp_text)
                        if _resp_text:
                            _eng_logger.info(
                                "/chat/completions multimodal success: %d chars "
                                "(reasoning: %d chars)",
                                len(_resp_text), len(_reasoning_text),
                            )
                            return {
                                "response": _sanitize_response(_resp_text),
                                "model": self.model_path,
                                "usage": _chat_result.get("usage", {}),
                                "done": True,
                                "truncated": _finish_reason == "length",
                            }
                        _eng_logger.info(
                            "/chat/completions multimodal returned empty content "
                            "(%d chars reasoning, finish_reason=%s) — falling "
                            "through to Path C",
                            len(_reasoning_text), _finish_reason,
                        )
                    else:
                        _eng_logger.warning("/chat/completions multimodal returned %d — falling through", _chat_resp.status_code)
                except Exception as _path_a_exc:
                    _eng_logger.warning("/chat/completions multimodal failed: %s — falling through", _path_a_exc)
            else:
                _eng_logger.info(
                    "Grounding model detected — skipping /chat/completions (Path A), "
                    "using mtmd-cli (Path B) for proper PBD grounding"
                )
        
            # ------------------------------------------------------------------
            # Path B: llama-mtmd-cli — only for grounding models
            # ------------------------------------------------------------------
            if _is_grounding:
                # Free the ~4.5 GB the server holds for this same model before
                # mtmd-cli loads it again as its own process: grounding output
                # comes exclusively from mtmd-cli (PBD), so a resident server
                # only doubles the memory footprint and OOM-crashes the
                # subprocess (blind full-screen box -> clicks at screen center).
                if self.is_running:
                    _eng_logger.info(
                        "Stopping llama-server before mtmd-cli grounding run "
                        "(frees the model's memory for the one-shot process)"
                    )
                    self.stop_server(quick=True)
                mtmd_cli_path = self._find_mtmd_cli()
                print(f"[ENGINE_DEBUG2] _find_mtmd_cli returned: {mtmd_cli_path}, exists: {os.path.exists(mtmd_cli_path) if mtmd_cli_path else 'N/A'}", flush=True)
                if mtmd_cli_path and os.path.exists(mtmd_cli_path):
                    _eng_logger.info("Using mtmd-cli for grounding model generation")
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
                            image_path=image_paths[0],
                            max_tokens=kwargs.get("max_tokens", -1),
                            temperature=kwargs.get("temperature", 0.7),
                        )
                        mtmd_text = result.get("response", "") or ""
                        if mtmd_text and not (
                            "Usage:" in mtmd_text or
                            "Experimental CLI" in mtmd_text or
                            mtmd_text.strip().startswith("D:")
                        ):
                            return result
                        _eng_logger.warning(
                            "mtmd-cli produced help/error text instead of generation: %s",
                            mtmd_text[:200] if mtmd_text else "(empty)",
                        )
                    finally:
                        for p in image_paths:
                            try:
                                os.remove(p)
                            except:
                                pass
            else:
                _eng_logger.info("VLM multimodal — skipped mtmd-cli (Path B), using /completion (Path C)")
        
            # ------------------------------------------------------------------
            # Path C: /completion with image_data (universal fallback)
            # ------------------------------------------------------------------
            image_data_objects = [
                {"data": img, "id": idx} for idx, img in enumerate(image_base64_list)
            ]
            try:
                response_text, cont_usage, cont_truncated = (
                    self._completion_with_continuation(
                        prompt_text,
                        image_data_objects,
                        kwargs.get("max_tokens", -1),
                        kwargs.get("temperature", 0.7),
                        kwargs.get("top_p", 0.9),
                        repeat_penalty=kwargs.get("repeat_penalty", 1.1),
                    )
                )
                if not response_text:
                    _eng_logger.warning(
                        "/completion with images returned no content ("
                        "status 200, continuation rounds finished)"
                    )
                    print(
                        f"[ENGINE_DEBUG] /completion 200 OK but empty content! "
                        f"(continuation rounds finished with no text)",
                        flush=True,
                    )
                else:
                    _eng_logger.info(
                        "/completion success: %d chars (truncated: %s)",
                        len(response_text), cont_truncated,
                    )
                return {
                    "response": _sanitize_response(
                        _strip_reasoning_blocks(response_text)
                    ),
                    "model": self.model_path,
                    "usage": cont_usage,
                    "done": True,
                    "truncated": cont_truncated,
                }
            except Exception as e:
                _eng_logger.error(f"Multimodal completion exception: {e}")
                return {"error": f"Multimodal completion error: {e}", "response": ""}

        # Text-only path: try /chat/completions, fall back to /completion
        prompt = prompt_text or self._messages_to_prompt(messages)
        try:
            _ok, _resp_text, _reasoning, _usage, _truncated = (
                self._chat_with_continuation(
                    messages,
                    kwargs.get("max_tokens", -1),
                    kwargs.get("temperature", 0.7),
                    kwargs.get("top_p", 0.9),
                    repeat_penalty=kwargs.get("repeat_penalty", 1.1),
                    # Short structured callers (consolidation hooks) cap this
                    # to 1 so a rambling small model is never continued for
                    # minutes; narrative callers keep the default.
                    max_rounds=int(kwargs.get("max_rounds") or 8),
                    chat_template_kwargs=kwargs.get("chat_template_kwargs"),
                )
            )
            if not _ok:
                _eng_logger.warning(
                    "/chat/completions text-only failed — falling back to /completion"
                )
                return self.generate(prompt, **kwargs)
            # Defensive: with --reasoning-format none/legacy the thinking blocks
            # are left inside content — strip them so the FINAL ANSWER is what
            # the caller receives, never the chain of thought.
            _resp_text = _strip_reasoning_blocks(_resp_text)
            if _resp_text:
                _eng_logger.info(
                    "/chat/completions success: %d chars "
                    "(reasoning: %d chars, truncated: %s)",
                    len(_resp_text), len(_reasoning), _truncated,
                )
            elif _reasoning:
                # The model produced ONLY a chain of thought (it was cut off
                # mid-thinking or deliberately stopped without answering).
                # Returning the thinking as the "response" is exactly what
                # this fix removes — surface the empty answer instead.
                _eng_logger.warning(
                    "/chat/completions returned ONLY reasoning (%d chars) and "
                    "no final answer — returning empty instead of the thinking "
                    "process (truncated=%s)",
                    len(_reasoning), _truncated,
                )
            else:
                _eng_logger.warning(
                    "/chat/completions returned 200 but no content in any known key."
                )
                print(
                    f"[ENGINE_DEBUG] /chat/completions 200 OK but empty content! "
                    f"(continuation rounds finished with no text)",
                    flush=True,
                )
            return {
                "response": _sanitize_response(_resp_text),
                "model": self.model_path,
                "usage": _usage,
                "done": True,
                "truncated": _truncated,
            }
        except Exception as e:
            _eng_logger.warning("/chat/completions text-only attempt failed: %s", e)

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

    def embed(self, texts: List[str]) -> List[List[float]]:
        """Return embedding vectors for *texts* via the ``/embedding`` endpoint.

        The server must have been started with ``embedding_mode=True``
        (the ``--embeddings`` flag).  Sends all texts in a single request;
        raises on any failure so callers can fall back to another embedder.
        """
        import requests
        if not texts:
            return []
        if not self.is_running:
            raise RuntimeError("Embedding server is not running")
        resp = requests.post(
            f"{self.server_url}/embedding",
            json={"content": list(texts)},
            timeout=None,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"llama.cpp /embedding error: {resp.status_code} - "
                f"{resp.text[:300]}"
            )
        data = resp.json()
        # The /embedding response shape varies across llama.cpp builds:
        #   - bare array:            [[...], [...]]
        #   - wrapped:               {"embedding": [[...], [...]]}
        #   - "embeddings" key:      {"embeddings": [[...], [...]]}
        #   - per-item objects:      [{"index": 0, "embedding": [[...]]}, ...]
        #   - single input, bare:    [0.1, 0.2, ...]  (flat vector)
        if isinstance(data, dict):
            embs = data.get("embedding") or data.get("embeddings")
            if embs is None and isinstance(data.get("data"), list):
                embs = [
                    item.get("embedding")
                    for item in data["data"]
                    if isinstance(item, dict)
                ]
        else:
            embs = data
        if (
            len(texts) == 1
            and isinstance(embs, list)
            and embs
            and not isinstance(embs[0], (list, tuple, dict))
        ):
            # Single-input responses may return the bare vector instead of
            # a one-element array.
            embs = [embs]
        if not isinstance(embs, list) or len(embs) != len(texts):
            raise RuntimeError(
                f"llama.cpp /embedding count mismatch: sent {len(texts)}, "
                f"got {len(embs) if isinstance(embs, list) else 'n/a'}"
            )
        # Per-item objects carry an "index" — honor it in case the server
        # returns results out of request order.
        if embs and isinstance(embs[0], dict) and "index" in embs[0]:
            embs = sorted(embs, key=lambda d: d.get("index", 0))
        vectors: List[List[float]] = []
        for e in embs:
            if isinstance(e, dict):  # per-item {"index": N, "embedding": [...]}
                e = e.get("embedding")
            if isinstance(e, list) and len(e) == 1 and isinstance(e[0], list):
                # This build wraps each vector in one extra list level:
                # {"embedding": [[...]]} — unwrap it.
                e = e[0]
            if not isinstance(e, (list, tuple)):
                raise RuntimeError(
                    f"llama.cpp /embedding unexpected vector type: "
                    f"{type(e).__name__}"
                )
            vectors.append([float(x) for x in e])
        return vectors

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
