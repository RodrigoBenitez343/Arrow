"""Agent Web Server — local transport API for Agent Mode.

The chat UI now ships inside the phone client app (LoOper/phone_client), which
loads it from its own assets and drives this server over the Pear/Bare pipe.

HOW IT WORKS
  1. Starts an aiohttp HTTP + WebSocket server (bound to 0.0.0.0) exposing the
     agent protocol: /ws (chat + panels), /stt, /tts, /status.
  2. Reports a remote access key - ``pear://<pubkey>`` when the Pear transport
     sidecar is running, else ``http://<tailscale-or-lan-ip>:<port>`` - so the
     agent-mode panel can show a scannable key code to remote clients.
  3. The bundled phone UI connects to /ws through the sidecar pipe.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from typing import Callable

logger = logging.getLogger(__name__)

# Use explicit IPv4 loopback for the local-only URL / fallback display.  The
# server itself binds 0.0.0.0 (see AgentWebServer) so remote clients on the
# same LAN or Tailnet can reach it.
_HOST = "127.0.0.1"

# ---------------------------------------------------------------------------
# Dependency checks
# ---------------------------------------------------------------------------
MISSING: list[str] = []
try:
    from aiohttp import web, WSMsgType
except ImportError:
    MISSING.append("aiohttp")

# qrcode imported lazily inside _qr_data_uri so the server can start
# even without the package (QR generation will simply be skipped).

if MISSING:
    logger.error("Missing packages for Agent Web Server: %s", " ".join(MISSING))

# ---------------------------------------------------------------------------
# Global state (per-server-instance)
# ---------------------------------------------------------------------------

class ServerState:
    """Mutable state for one server instance."""
    def __init__(self) -> None:
        self.active: bool = False
        self.local_url: str | None = None   # http://127.0.0.1:<port>
        self.remote_url: str | None = None  # http://<ip>:<port> when an IP is set
        self.handler: Callable[[str], str] | None = None
        self.connected_clients: int = 0
        # WebSocket channel for sending async responses from worker threads.
        # ws_send broadcasts to every connected socket: a single slot used to be
        # nulled by one client's disconnect, silently dropping messages for a
        # client that was still connected (e.g. the phone reconnecting).
        self.ws_loop: asyncio.AbstractEventLoop | None = None
        self.ws_send: Callable[[str], None] | None = None
        self._ws_sockets: list = []


# ---------------------------------------------------------------------------
# Remote-access helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Pear sidecar transport (replaces Tailscale remote access)
# ---------------------------------------------------------------------------
# The sidecar publishes an Ed25519 public key instead of an IP: a phone running
# a Pear client reaches the aiohttp server by key and speaks the SAME /ws chat
# protocol.  LoOper spawns the sidecar as a child process and reads its control
# lines (one JSON object per line) from stdout; sidecar logs go to stderr.
#
# It runs on Node.js (already present with LoOper) or Bare - the module only
# uses node-compatible APIs.  Its deps (hyperdht, b4a) live beside it in
# LoOper/agent_sidecar/node_modules.

_SIDECAR_DIRNAME = "agent_sidecar"
_SIDECAR_FILENAME = "looper-agent-sidecar.js"


def _sidecar_dir() -> str:
    """LoOper/agent_sidecar - the script plus its own node_modules."""
    widgets = os.path.dirname(os.path.abspath(__file__))       # LoOper/NGUI/widgets
    looper = os.path.dirname(os.path.dirname(widgets))         # LoOper
    return os.path.join(looper, _SIDECAR_DIRNAME)


def _sidecar_path() -> str:
    """Absolute path to the host transport sidecar script."""
    return os.path.join(_sidecar_dir(), _SIDECAR_FILENAME)


def _default_identity_path() -> str:
    """Persistent sidecar key store (stable pubkey => stable QR)."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "LoOper", "agent-key.json")


def _find_js_runtime() -> str | None:
    """Locate a JS runtime for the sidecar: Node.js (preferred) or Bare."""
    import shutil
    for name in ("node", "node.exe", "bare", "bare.exe"):
        exe = shutil.which(name)
        if exe:
            return exe
    for cand in (r"C:\Program Files\nodejs\node.exe",
                 os.path.join(_sidecar_dir(), "bare.exe")):
        if os.path.isfile(cand):
            return cand
    return None


def _sidecar_node_modules() -> str | None:
    """A node_modules holding hyperdht, used as a NODE_PATH fallback."""
    candidates = (
        os.path.join(_sidecar_dir(), "node_modules"),
        os.path.join(os.path.dirname(_sidecar_dir()), "phone_client", "node_modules"),
    )
    for path in candidates:
        if os.path.isdir(os.path.join(path, "hyperdht")):
            return os.path.normpath(path)
    return None


def start_sidecar(local_port: int, timeout: float = 20.0) -> dict | None:
    """Spawn the Pear sidecar and return ``{"pubkey", "proc"}`` or None.

    The sidecar byte-pipes hyperdht peers to ``127.0.0.1:<local_port>`` (the
    existing aiohttp server) and prints ``{"type":"ready","pubkey":...}`` on
    stdout once its Ed25519 server is listening.  Returns None when the runtime,
    the script or its dependencies are missing, so callers fall back to LAN.
    """
    import subprocess as _sp

    runtime = _find_js_runtime()
    script = _sidecar_path()
    if not runtime or not os.path.isfile(script):
        logger.warning(
            "Agent Pear sidecar unavailable (runtime=%s, script=%s)",
            runtime or "no node/bare",
            script if os.path.isfile(script) else "missing",
        )
        return None
    node_modules = _sidecar_node_modules()
    if node_modules is None:
        logger.warning(
            "Agent Pear sidecar deps missing - run Arrow_setup.bat "
            "(or: npm install in %s)", _sidecar_dir(),
        )
        return None

    # Expose the sidecar's modules to the child (NODE_PATH as a fallback path).
    env = dict(os.environ)
    existing = env.get("NODE_PATH", "")
    env["NODE_PATH"] = node_modules + (os.pathsep + existing if existing else "")

    try:
        proc = _sp.Popen(
            [runtime, script,
             "--port", str(local_port),
             "--identity", _default_identity_path()],
            stdout=_sp.PIPE, stderr=_sp.PIPE, stdin=_sp.PIPE,
            text=True, cwd=_sidecar_dir(), env=env,
            creationflags=0x08000000 if os.name == "nt" else 0,  # CREATE_NO_WINDOW
        )
    except Exception:
        logger.exception("Agent Pear sidecar failed to spawn")
        return None

    deadline = time.time() + timeout
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            logger.debug("sidecar stdout (non-JSON): %s", line[:120])
            continue
        if msg.get("type") == "ready":
            return {"pubkey": str(msg.get("pubkey", "")), "proc": proc}
        if msg.get("type") == "error":
            logger.error("Agent Pear sidecar error: %s", msg.get("msg"))
            break
    # Surface why it failed - module errors land on stderr.
    try:
        _, err = proc.communicate(timeout=2)
        if err:
            logger.warning("Agent Pear sidecar stderr: %s", err.strip()[:400])
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass
    logger.warning("Agent Pear sidecar did not report ready in %.1fs", timeout)
    return None


def _free_port(port: int) -> None:
    """Kill any process holding *port* on 0.0.0.0 or 127.0.0.1.

    Uses ``netstat -ano`` to find the offender and ``taskkill /F`` to
    terminate it.  Skips our own PID so we never shoot ourselves in the
    foot.  Silently ignores inaccessible (elevated) processes.
    """
    import subprocess as _sp
    try:
        our_pid = os.getpid()
        output = _sp.check_output(
            ["netstat", "-ano"],
            shell=False,
            text=True,
            timeout=5,
        )
        target_pids: set[int] = set()
        for line in output.splitlines():
            # Match lines like:
            #   TCP    0.0.0.0:8081   0.0.0.0:0    LISTENING    12345
            #   TCP    [::]:8081      [::]:0        LISTENING    12345
            #   TCP    127.0.0.1:8081 0.0.0.0:0    LISTENING    12345
            if f":{port}" not in line:
                continue
            parts = line.rsplit(None, 1)
            if len(parts) != 2:
                continue
            try:
                pid = int(parts[1])
            except ValueError:
                continue
            if pid == 0 or pid == our_pid:
                continue
            target_pids.add(pid)

        if not target_pids:
            return

        for pid in target_pids:
            logger.warning(
                "AgentWebServer: freeing port %d — killing PID %d",
                port, pid,
            )
            try:
                _sp.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True,
                    timeout=5,
                )
            except Exception:
                logger.debug(
                    "AgentWebServer: could not kill PID %d (access denied or already dead)",
                    pid,
                )
    except FileNotFoundError:
        logger.debug("AgentWebServer: netstat not found — skipping port cleanup")
    except Exception:
        logger.debug("AgentWebServer: port cleanup error (non-fatal)")





# ---------------------------------------------------------------------------
# QR code helper
# ---------------------------------------------------------------------------

def _qr_data_uri(url: str) -> str:
    try:
        import qrcode
        qr = qrcode.QRCode(box_size=8, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except ImportError:
        logger.warning("qrcode package not installed; skipping QR generation")
        return ""
    except Exception as exc:
        logger.warning("QR generation failed: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# HTML chat page template
# ---------------------------------------------------------------------------

# NOTE: the chat UI now ships inside the phone client app
# (LoOper/phone_client/android/app/src/main/assets/web/index.html); this
# server exposes only the transport API: /ws, /stt, /tts, /status.


# ---------------------------------------------------------------------------
# Request handlers
# ---------------------------------------------------------------------------

def _make_handlers(state: ServerState):
    """Return request handler closures bound to *state*."""

    async def handle_status(request: web.Request) -> web.Response:
        if state.active and state.remote_url:
            message = "Scan QR code to connect from another device"
        elif state.active:
            message = "Server running locally only - set a Tailscale/LAN IP for remote access"
        else:
            message = "Server not started"
        body: dict[str, object] = {
            "active": state.active,
            "local_url": state.local_url,
            "remote_url": state.remote_url,
            "message": message,
            "connected_clients": state.connected_clients,
        }
        if state.remote_url:
            body["qr_data_uri"] = _qr_data_uri(state.remote_url)
        return web.json_response(body)

    async def handle_websocket(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        state._ws_sockets.append(ws)
        state.connected_clients += 1
        logger.info("WebSocket client connected (%d active)", state.connected_clients)

        # Store loop + send callback so worker threads can push responses to
        # every connected client.
        loop = asyncio.get_event_loop()
        state.ws_loop = loop

        async def ws_send(text: str) -> None:
            dead = []
            for sock in list(state._ws_sockets):
                try:
                    await sock.send_str(text)
                except Exception:
                    dead.append(sock)
            for sock in dead:
                try:
                    state._ws_sockets.remove(sock)
                except ValueError:
                    pass

        state.ws_send = ws_send

        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                text = msg.data.strip()
                if not text:
                    continue
                logger.info("[web] << %s", text[:80])
                try:
                    handler = state.handler
                    if handler:
                        # Fire-and-forget — the handler spins its own thread
                        # and sends the response back via state.ws_send.
                        loop.run_in_executor(None, handler, text)
                    else:
                        await ws.send_str("Agent handler not available.")
                except Exception as exc:
                    logger.exception("WebSocket handler error")
                    try:
                        await ws.send_str(f"Error: {exc}")
                    except Exception:
                        pass
            elif msg.type == WSMsgType.ERROR:
                logger.error("WebSocket error: %s", ws.exception())

        try:
            state._ws_sockets.remove(ws)
        except ValueError:
            pass
        state.connected_clients -= 1
        # Only tear down the channel once the LAST client is gone.
        if not state._ws_sockets:
            state.ws_send = None
            state.ws_loop = None
        logger.info("WebSocket client disconnected (%d active)", state.connected_clients)
        return ws

    return handle_status, handle_websocket


async def handle_stt(request: web.Request) -> web.Response:
    """Transcribe uploaded audio using Vosk STT.

    Accepts multipart/form-data with an ``audio`` field containing a
    WAV file, or raw POST body as WAV bytes.

    Returns JSON: ``{"text": "...", "success": true}`` or
    ``{"error": "..."}`` on failure.
    """
    import asyncio

    audio_data = None

    # Try multipart first
    try:
        reader = await request.multipart()
        async for part in reader:
            if part.name == "audio":
                audio_data = await part.read()
                break
    except Exception:
        pass

    # Fallback: raw body
    if audio_data is None:
        try:
            audio_data = await request.read()
        except Exception:
            pass

    if not audio_data or len(audio_data) < 44:
        return web.json_response(
            {"error": "No audio data received or file too small"},
            status=400,
        )

    try:
        from player.stt_engine import get_stt_engine

        # Save to temp file so we can read WAV header properly
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        try:
            tmp.write(audio_data)
            tmp.close()

            engine = get_stt_engine()
            result = engine.transcribe_file(tmp.name)
        finally:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass

        if result.get("success"):
            return web.json_response({
                "text": result["text"],
                "success": True,
                "partial": result.get("partial", False),
            })
        else:
            error_msg = result.get("error") or result.get("message", "Transcription failed")
            if "no speech" in error_msg.lower():
                return web.json_response({"text": "", "success": True, "partial": False})
            return web.json_response({"error": error_msg}, status=422)

    except ImportError as e:
        return web.json_response(
            {"error": f"STT engine not available: {e}"},
            status=503,
        )
    except Exception as e:
        logger.exception("STT handler error")
        return web.json_response({"error": f"STT error: {e}"}, status=500)


async def handle_tts(request: web.Request) -> web.Response:
    """Synthesize speech from text using Piper TTS.

    Query params:
        text (str): Text to speak (URL-encoded).

    Returns WAV audio bytes with ``Content-Type: audio/wav``.
    """
    text = request.query.get("text", "").strip()
    if not text or len(text) < 2:
        return web.json_response({"error": "Text too short"}, status=400)
    if len(text) > 500:
        text = text[:500] + "..."

    try:
        from player.tts_engine import synthesize

        result = synthesize(text)
        if result.get("success"):
            output_path = result["output_path"]
            try:
                with open(output_path, "rb") as f:
                    wav_bytes = f.read()
                return web.Response(
                    body=wav_bytes,
                    content_type="audio/wav",
                    headers={
                        "Content-Disposition": "inline",
                        "Cache-Control": "public, max-age=3600",
                    },
                )
            finally:
                try:
                    if result.get("_is_temp"):
                        os.unlink(output_path)
                except Exception:
                    pass
        else:
            return web.json_response(
                {"error": result.get("error", "TTS synthesis failed")},
                status=500,
            )
    except ImportError as e:
        return web.json_response(
            {"error": f"TTS engine not available: {e}"},
            status=503,
        )
    except Exception as e:
        logger.exception("TTS handler error")
        return web.json_response({"error": f"TTS error: {e}"}, status=500)


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_app(state: ServerState) -> web.Application:
    hs, hw = _make_handlers(state)
    app = web.Application()
    app.router.add_get("/status", hs)
    app.router.add_get("/ws", hw)
    app.router.add_post("/stt", handle_stt)
    app.router.add_get("/tts", handle_tts)
    return app


# ---------------------------------------------------------------------------
# Lifecycle (runs in a background thread)
# ---------------------------------------------------------------------------

class AgentWebServer:
    """Start/stop the local web server in a background thread.

    Usage::

        server = AgentWebServer(handler=my_handler_function)
        server.set_peer_key("<pubkey>")  # optional: Pear key for the QR
        server.start()
        # ... later ...
        server.stop()
    """

    def __init__(self, handler: Callable[[str], str] | None = None,
                 port: int = 8081, bind_host: str = "0.0.0.0") -> None:
        self._state = ServerState()
        self._state.handler = handler
        self._port = port
        self._bind_host = bind_host
        self._remote_ip = ""
        self._peer_key = ""
        self._actual_port: int | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._shutdown_event: asyncio.Event | None = None
        # Supervisor loop control: stop() latches _stopping so a crashed loop
        # is never resurrected; _restart_delay spaces out crash retries.
        self._stopping = False
        self._restart_delay = 2.0

    @property
    def state(self) -> ServerState:
        return self._state

    def set_remote_ip(self, ip: str) -> None:
        """Set the LAN/Tailscale IP used to build the remote URL + QR code."""
        self._remote_ip = (ip or "").strip()

    def set_peer_key(self, pubkey: str) -> None:
        """Set the Pear Ed25519 public key used to build the remote URL + QR.

        Replaces the IP-based remote access: the QR now carries
        ``pear://<pubkey>`` and a Bare/Pear phone client connects by key.
        """
        self._peer_key = (pubkey or "").strip()

    @property
    def actual_port(self) -> int | None:
        """The port the server actually bound to (may differ from *port* if fallback was used)."""
        return self._actual_port

    def start(self) -> bool:
        """Start the server in a background thread.

        Returns True if the server started successfully, False otherwise.
        """
        if self._thread and self._thread.is_alive():
            logger.warning("AgentWebServer already running")
            return True

        # Free the configured port range so lingering orphan processes
        # from previous sessions don't block binding.
        for p in range(self._port, self._port + 10):
            _free_port(p)

        self._stopping = False
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="AgentWebServer"
        )
        self._thread.start()
        # Wait for server to be ready (up to 15 seconds)
        ok = self._ready.wait(timeout=15.0)
        if not ok:
            logger.warning("AgentWebServer start timeout — port %d may be in use", self._port)
        return ok

    def stop(self) -> None:
        # Explicit stop is final: latch it so the supervisor loop in _run()
        # never re-serves after the user switched the server off.
        self._stopping = True
        # Signal the event loop to shut down gracefully so the
        # finally block in _serve() can run runner.cleanup().
        if self._shutdown_event is not None and self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(self._shutdown_event.set)
            except RuntimeError:
                pass
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def set_handler(self, handler: Callable[[str], str]) -> None:
        self._state.handler = handler

    @property
    def remote_url(self) -> str | None:
        return self._state.remote_url

    def _run(self) -> None:
        # Supervisor loop: the phone-facing server must stay up for hours.
        # A crashed event loop or a failed port bind is retried after a short
        # backoff; only an explicit stop() breaks the loop.
        while not self._stopping:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            try:
                loop.run_until_complete(self._serve())
            except Exception:
                logger.exception("AgentWebServer event loop crashed — restarting")
                # Re-arm for a fresh start on the next attempt.
                self._ready.clear()
                self._shutdown_event = None
                if self._stopping:
                    break
                time.sleep(self._restart_delay)
            finally:
                loop.close()
                self._loop = None

    async def _serve(self) -> None:
        state = self._state
        app = create_app(state)
        runner = web.AppRunner(app)
        await runner.setup()

        # Try to bind with port fallback (up to 10 ports)
        last_error: Exception | None = None
        actual_port = self._port
        for offset in range(10):
            try_port = self._port + offset
            try:
                site = web.TCPSite(runner, self._bind_host, try_port)
                await site.start()
                actual_port = try_port
                last_error = None
                break
            except OSError as e:
                logger.warning(
                    "AgentWebServer: port %d unavailable (%s), trying %d ...",
                    try_port, e.strerror, try_port + 1,
                )
                last_error = e
                continue

        if last_error is not None:
            await runner.cleanup()
            raise RuntimeError(
                f"AgentWebServer: no available port in range "
                f"{self._port}-{self._port + 9}"
            ) from last_error

        self._actual_port = actual_port
        state.local_url = f"http://{_HOST}:{actual_port}"
        # Prefer the Pear key (transport sidecar) over an IP: the QR then
        # carries ``pear://<pubkey>`` and the phone connects by key.
        if self._peer_key:
            state.remote_url = f"pear://{self._peer_key}"
        elif self._remote_ip:
            state.remote_url = f"http://{self._remote_ip}:{actual_port}"
        else:
            state.remote_url = None
        state.active = True
        logger.info(
            "Agent Web Server -> %s (remote: %s)",
            state.local_url,
            state.remote_url or "none — start the Pear transport or set a LAN IP",
        )
        self._ready.set()

        self._shutdown_event = asyncio.Event()
        try:
            await self._shutdown_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            state.active = False
            state.remote_url = None
            state.local_url = None
            try:
                await runner.cleanup()
            except Exception:
                pass
