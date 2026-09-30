"""Agent Web Server — local HTTP + HTML chat app for Agent Mode.

HOW IT WORKS
  1. Starts an aiohttp HTTP + WebSocket server on 0.0.0.0 so a phone on
     the same LAN or Tailnet can reach it.
  2. Reports a remote URL (http://<tailscale-or-lan-ip>:<port>) so the
     agent-mode panel can show a scannable QR code to remote clients.
  3. Serves an HTML chat page with message history, and a WebSocket that
     routes messages through a handler callable.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import tempfile
import threading
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
        # WebSocket channel for sending async responses from worker threads
        self.ws_loop: asyncio.AbstractEventLoop | None = None
        self.ws_send: Callable[[str], None] | None = None


# ---------------------------------------------------------------------------
# Remote-access helpers
# ---------------------------------------------------------------------------


def detect_tailscale_ip() -> str | None:
    """Return this machine's Tailscale IPv4 (``tailscale ip -4``), or None.

    Used to build the remote URL behind the agent-mode QR code.  Falls
    back through the standard Tailscale install locations so compiled
    builds find it too; returns None when Tailscale is not running, so
    callers can prompt for a LAN IP instead.
    """
    import shutil
    import subprocess as _sp

    exe = shutil.which("tailscale") or shutil.which("tailscale.exe")
    if not exe:
        for cand in (
            r"C:\Program Files\Tailscale\tailscale.exe",
            r"C:\Program Files (x86)\Tailscale\tailscale.exe",
        ):
            if os.path.isfile(cand):
                exe = cand
                break
    if not exe:
        return None
    try:
        out = _sp.run(
            [exe, "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=0x08000000,  # CREATE_NO_WINDOW (GUI app)
        ).stdout.strip()
    except Exception:
        return None
    return out.splitlines()[0] if out else None


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

CHAT_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
<title>Arrow Agent</title>
<style>
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
  body{
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    background:#0B0E12;color:#E6E8EB;height:100dvh;display:flex;
    flex-direction:column;overflow:hidden
  }
  /* top bar */
  .top-bar{
    background:#12161C;padding:8px 16px;display:flex;align-items:center;gap:8px;
    flex-shrink:0;min-height:44px;z-index:10
  }
  .top-bar h1{font-size:0.95rem;color:#E6E8EB;font-weight:700}
  .top-bar .dot{width:8px;height:8px;border-radius:50%;display:inline-block;flex-shrink:0;transition:background .3s}
  .top-bar .dot.connected{background:#22c55e;box-shadow:0 0 4px #22c55e}
  .top-bar .dot.disconnected{background:#ef4444}
  .top-bar .dot.connecting{background:#f59e0b}
  .top-bar .spacer{flex:1}
  .top-bar .stop-btn-small{
    background:rgba(239,68,68,0.12);color:#ef4444;border:1px solid rgba(239,68,68,0.25);
    border-radius:6px;padding:3px 10px;font-size:0.68rem;font-weight:600;cursor:pointer;
    display:none;font-family:inherit;white-space:nowrap;transition:background .15s
  }
  .top-bar .stop-btn-small:hover{background:rgba(239,68,68,0.22)}
  /* messages */
  #messages{
    flex:1;overflow-y:auto;padding:16px 16px 8px;display:flex;flex-direction:column;gap:10px;
    min-height:0;-webkit-overflow-scrolling:touch
  }
  .msg{max-width:85%;padding:10px 14px;border-radius:16px;font-size:0.9rem;line-height:1.5;word-wrap:break-word;overflow-wrap:break-word;word-break:break-word}
  .msg.user{background:#00C2A0;color:#0B0E12;align-self:flex-end;border-bottom-right-radius:4px;font-weight:500}
  .msg.agent{background:#161B21;color:#E6E8EB;align-self:flex-start;border-bottom-left-radius:4px}
  .msg .time{font-size:0.6rem;opacity:.3;margin-top:6px;text-align:right}
  .msg.agent .time{text-align:left}
  .msg.agent.typing{opacity:.6}
  /* input area */
  .input-area{padding:8px 16px 16px;flex-shrink:0;display:flex;flex-direction:column;gap:6px}
  .input-row{display:flex;gap:8px;align-items:flex-end}
  .input-wrap{
    flex:1;display:flex;align-items:center;background:#12161C;border:2px solid rgba(255,255,255,0.06);
    border-radius:16px;padding:2px;transition:border-color .2s;min-height:48px
  }
  .input-wrap:focus-within{border-color:#00C2A0}
  .input-wrap .mic-btn{
    width:36px;height:36px;background:transparent;color:rgba(255,255,255,0.3);border:none;
    border-radius:50%;font-size:1.15rem;cursor:pointer;flex-shrink:0;font-family:inherit;
    display:flex;align-items:center;justify-content:center;transition:all .15s;touch-action:manipulation
  }
  .input-wrap .mic-btn:active{background:rgba(255,255,255,0.05);transform:scale(.88)}
  .input-wrap .mic-btn.recording{color:#ef4444;animation:micPulse 1s infinite}
  @keyframes micPulse{0%,100%{text-shadow:0 0 0 transparent}50%{text-shadow:0 0 10px rgba(239,68,68,.5)}}
  .input-wrap textarea{
    flex:1;background:transparent;color:#EEEEEE;border:none;padding:8px 4px;font-size:0.9rem;
    resize:none;font-family:inherit;outline:none;min-height:24px;max-height:80px;line-height:1.4
  }
  .input-wrap textarea::placeholder{color:rgba(255,255,255,0.2)}
  .input-wrap .send-btn{
    width:36px;height:36px;background:#00C2A0;color:#0B0E12;border:none;border-radius:50%;
    font-size:1rem;font-weight:700;cursor:pointer;flex-shrink:0;font-family:inherit;
    display:none;align-items:center;justify-content:center;transition:all .15s;touch-action:manipulation
  }
  .input-wrap .send-btn.show{display:flex}
  .input-wrap .send-btn:active{transform:scale(.9)}
  .input-wrap .send-btn:disabled{opacity:.3;cursor:not-allowed}
  /* side controls */
  .side-controls{display:flex;gap:6px;flex-shrink:0;align-items:flex-end}
  .tts-toggle{
    width:40px;height:40px;background:transparent;color:rgba(255,255,255,0.18);border:1px solid rgba(255,255,255,0.06);
    border-radius:10px;font-size:1rem;cursor:pointer;transition:all .2s;font-family:inherit;
    display:flex;align-items:center;justify-content:center;flex-shrink:0;touch-action:manipulation
  }
  .tts-toggle.active{color:#22c55e;border-color:rgba(34,197,94,0.25);background:rgba(34,197,94,0.08)}
  /* vector icons (matching the desktop app's vectorial icon convention) */
  .input-wrap svg,.tts-toggle svg{width:20px;height:20px;display:block}
  .tts-toggle .ic-off{display:none}
  .tts-toggle:not(.active) .ic-on{display:none}
  .tts-toggle:not(.active) .ic-off{display:block}
  /* recording indicator */
  .recording-indicator{
    display:none;align-items:center;gap:6px;color:#ef4444;font-size:0.72rem;font-weight:500;padding:0 4px
  }
  .recording-indicator .bars{display:flex;align-items:center;gap:2px;margin-left:auto}
  .recording-indicator .bar{width:3px;height:12px;background:#ef4444;border-radius:2px;animation:barAnim .5s ease infinite alternate}
  .recording-indicator .bar:nth-child(2){animation-delay:.15s;height:18px}
  .recording-indicator .bar:nth-child(3){animation-delay:.3s;height:8px}
  @keyframes barAnim{0%{height:4px}100%{height:20px}}
  /* rating & feedback */
  .rating-row{display:flex;gap:2px;margin-top:6px;align-items:center}
  .rating-row .star{font-size:14px;color:rgba(255,255,255,0.35);font-weight:700;cursor:pointer;transition:transform .1s;user-select:none;padding:0 4px}
  .rating-row .star:hover{transform:scale(1.2)}
  .feedback-box{margin-top:6px;display:flex;flex-direction:column;gap:4px}
  .feedback-box textarea{background:#0B0E12;color:#EEEEEE;border:1px solid rgba(255,255,255,0.08);border-radius:8px;padding:6px 8px;font-size:0.78rem;resize:vertical;font-family:inherit;outline:none;min-height:36px;max-height:64px}
  .feedback-box button{align-self:flex-end;padding:4px 12px;background:#00C2A0;color:#0B0E12;border:none;border-radius:6px;font-size:0.75rem;cursor:pointer;font-weight:600}
  .feedback-box button:hover{background:#00c8a0}
  /* views + nav */
  .nav{display:flex;gap:2px;margin-left:4px}
  .nav button{background:transparent;color:rgba(255,255,255,0.45);border:1px solid transparent;border-radius:8px;padding:5px 9px;font:inherit;font-size:.72rem;font-weight:700;cursor:pointer}
  .nav button.active{color:#00C2A0;background:rgba(0,194,160,0.12);border-color:rgba(0,194,160,0.3)}
  .view{flex:1;min-height:0;display:none;flex-direction:column}
  .view.active{display:flex}
  .sub-bar{display:flex;align-items:center;gap:8px;padding:8px 16px 0;flex-shrink:0}
  .sub-bar label{font-size:.66rem;text-transform:uppercase;letter-spacing:.08em;color:rgba(255,255,255,0.35);font-weight:700}
  .sub-bar select{flex:1;background:#12161C;color:#E6E8EB;border:1px solid rgba(255,255,255,0.1);border-radius:8px;padding:6px 8px;font:inherit;font-size:.8rem;outline:none}
  .panel-head{display:flex;align-items:center;gap:8px;padding:12px 16px 8px;flex-shrink:0}
  .panel-head span{font-size:.7rem;color:rgba(255,255,255,0.4);font-weight:700}
  .panel-body{flex:1;overflow-y:auto;padding:0 16px 16px;display:flex;flex-direction:column;gap:6px;-webkit-overflow-scrolling:touch}
  .mini{background:transparent;color:rgba(255,255,255,0.72);border:1px solid rgba(255,255,255,0.15);border-radius:7px;padding:5px 10px;font:inherit;font-size:.72rem;font-weight:700;cursor:pointer}
  .mini:hover{background:rgba(255,255,255,0.08)}
  .mini.accent{color:#00C2A0;border-color:rgba(0,194,160,0.4)}
  .mini.danger{color:#e5484d;border-color:rgba(229,72,77,0.4)}
  .srow{background:#12161C;border:1px solid rgba(255,255,255,0.07);border-radius:10px;padding:10px 12px;display:flex;flex-direction:column;gap:6px}
  .srow .s1{display:flex;align-items:center;gap:8px}
  .srow .sname{font-size:.85rem;font-weight:700;color:#E6E8EB}
  .srow .smeta{font-size:.72rem;color:rgba(255,255,255,0.5)}
  .srow .sacts{display:flex;gap:6px;flex-wrap:wrap}
  .sched-form{display:none;flex-direction:column;gap:8px;padding:0 16px 16px;flex-shrink:0;overflow-y:auto}
  .sched-form.open{display:flex}
  .frow{display:flex;align-items:center;gap:8px}
  .frow>label{width:86px;font-size:.7rem;color:rgba(255,255,255,0.4);font-weight:700;flex-shrink:0}
  .frow input[type=text],.frow input[type=time],.frow input[type=datetime-local],.frow input[type=number],.frow select{flex:1;background:#0B0E12;color:#E6E8EB;border:1px solid rgba(255,255,255,0.1);border-radius:7px;padding:6px 8px;font:inherit;font-size:.78rem;outline:none}
  .days{display:flex;gap:6px;flex-wrap:wrap}
  .days label{font-size:.7rem;color:rgba(255,255,255,0.6);display:flex;align-items:center;gap:3px}
  .spectro{width:100%;height:36px;display:none;margin-top:6px}
  .spectro.on{display:block}
  .msg pre{margin:6px 0;padding:8px;background:#0B0E12;border:1px solid rgba(255,255,255,0.08);border-radius:8px;overflow-x:auto;font-size:.75rem}
  .msg code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.85em}
  .msg pre code{background:transparent}
  .msg .mdh{font-weight:700;margin:6px 0 2px}
  .msg ul{margin:4px 0 4px 18px}
  .msg a{color:#00C2A0}
</style>
</head>
<body>

<div class="top-bar">
  <span class="dot connecting" id="statusDot"></span>
  <h1>Arrow</h1>
  <div class="nav">
    <button id="navChat" class="active" onclick="showView('chat')">Chat</button>
    <button id="navSched" onclick="showView('schedules')">Schedules</button>
    <button id="navChains" onclick="showView('chains')">Chains</button>
  </div>
  <div class="spacer"></div>
  <button class="stop-btn-small" id="stopBtn" onclick="sendStop()">Stop</button>
</div>

<div class="view active" id="view-chat">
<div class="sub-bar">
  <label for="coworkerSel">Coworker</label>
  <select id="coworkerSel" onchange="selectCoworker(this.value)"></select>
  <button class="mini" onclick="sendWS({type:'list_coworkers'})">Refresh</button>
</div>
<div id="messages">
  <div class="msg agent typing">
    Connected to Arrow Agent.
    <div class="time">now</div>
  </div>
</div>

<canvas class="spectro" id="inSpectro"></canvas>

<div class="input-area">
  <div class="input-row">
    <div class="input-wrap">
      <button class="mic-btn" id="micBtn"
        onmousedown="startRecording(event)" onmouseup="stopRecording(event)" onmouseleave="stopRecording(event)"
        ontouchstart="startRecording(event)" ontouchend="stopRecording(event)" ontouchcancel="stopRecording(event)"
        title="Hold to record"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 2m0 3a3 3 0 0 1 3 -3h0a3 3 0 0 1 3 3v5a3 3 0 0 1 -3 3h0a3 3 0 0 1 -3 -3z"/><path d="M5 10a7 7 0 0 0 14 0"/><path d="M8 21l8 0"/><path d="M12 17l0 4"/></svg></button>
      <textarea id="inputBox" placeholder="Message" rows="1" autofocus></textarea>
      <button class="send-btn" id="sendBtn" onclick="sendMsg()" title="Send"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 14l11 -11"/><path d="M21 3l-6.5 18a0.55 .55 0 0 1 -1 0l-3.5 -7l-7 -3.5a0.55 .55 0 0 1 0 -1l18 -6.5"/></svg></button>
    </div>
    <div class="side-controls">
      <button class="tts-toggle active" id="ttsToggle" onclick="toggleTts()" title="Voice responses on"><svg class="ic-on" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 8a5 5 0 0 1 0 8"/><path d="M6 15h-2a1 1 0 0 1 -1 -1v-4a1 1 0 0 1 1 -1h2l3.5 -4.5a.8 .8 0 0 1 1.5 .5v14a.8 .8 0 0 1 -1.5 .5l-3.5 -4.5"/></svg><svg class="ic-off" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 15h-2a1 1 0 0 1 -1 -1v-4a1 1 0 0 1 1 -1h2l3.5 -4.5a.8 .8 0 0 1 1.5 .5v14a.8 .8 0 0 1 -1.5 .5l-3.5 -4.5"/><path d="M16 10l4 4m0 -4l-4 4"/></svg></button>
    </div>
  </div>
  <div class="recording-indicator" id="recordingIndicator">
    <span>Recording</span>
    <span class="bars"><span class="bar"></span><span class="bar"></span><span class="bar"></span></span>
  </div>
</div>
</div><!-- /view-chat -->

<div class="view" id="view-schedules">
  <div class="panel-head">
    <span id="schedCount"></span>
    <div class="spacer"></div>
    <button class="mini accent" onclick="openSchedForm(null)">Add</button>
    <button class="mini" onclick="loadSchedules()">Refresh</button>
  </div>
  <div class="panel-body" id="schedList"></div>
  <form class="sched-form" id="schedForm" onsubmit="return saveSchedule(event)">
    <input type="hidden" id="sf_id">
    <div class="frow"><label>Name</label><input id="sf_name" type="text" placeholder="Schedule name"></div>
    <div class="frow"><label>Type</label>
      <select id="sf_type" onchange="schedTypeChanged()">
        <option value="once">once</option>
        <option value="daily" selected>daily</option>
        <option value="weekly">weekly</option>
        <option value="interval">interval</option>
      </select></div>
    <div class="frow"><label>Chain</label><input id="sf_chain" type="text" list="chainOptions" placeholder="name.json">
      <datalist id="chainOptions"></datalist></div>
    <div class="frow" id="sf_once_row" style="display:none"><label>Date/time</label><input id="sf_oncedt" type="datetime-local"></div>
    <div class="frow" id="sf_daily_row"><label>Time</label><input id="sf_dailytime" type="time" value="09:00"></div>
    <div class="frow" id="sf_weekly_row" style="display:none"><label>Time</label><input id="sf_weeklytime" type="time" value="09:00"></div>
    <div class="frow" id="sf_days_row" style="display:none"><label>Days</label><div class="days" id="sf_daysbox"></div></div>
    <div class="frow" id="sf_interval_row" style="display:none"><label>Every (min)</label><input id="sf_interval" type="number" min="1" value="60"></div>
    <div class="frow"><label>Sandbox</label><input id="sf_sandbox" type="checkbox"></div>
    <div class="frow"><label></label>
      <div><button type="submit" class="mini accent">Save</button>
      <button type="button" class="mini" onclick="closeSchedForm()">Cancel</button></div></div>
  </form>
</div>

<div class="view" id="view-chains">
  <div class="panel-head">
    <span id="chainCount"></span>
    <div class="spacer"></div>
    <button class="mini" onclick="loadChains()">Refresh</button>
  </div>
  <div class="panel-body" id="chainList"></div>
</div>

<script>
  const WS_URL = "__WS_URL__";

  let ws = null;
  let reconnectTimer = null;
  let reconnectDelay = 1000;

  // Voice recording state
  let recordingStream = null;
  let isRecording = false;
  let ttsEnabled = true;

  const dot = document.getElementById("statusDot");

  const sendBtn = document.getElementById("sendBtn");
  const stopBtn = document.getElementById("stopBtn");
  const micBtn = document.getElementById("micBtn");
  const ttsToggle = document.getElementById("ttsToggle");
  const inputBox = document.getElementById("inputBox");
  const messages = document.getElementById("messages");
  const recordingIndicator = document.getElementById("recordingIndicator");
  let lastAgentMsgDiv = null;
  let agentBusy = false;
  let waitingForAnswer = false;  // true when agent sent an 'ask' prompt
  let askVoiceMode = false;      // true when the mic auto-started to answer an ask

  function setStatus(state) {
    dot.className = "dot " + state;
    sendBtn.disabled = (state !== "connected");
  }

  function addMsg(text, cls) {
    var div = document.createElement("div");
    div.className = "msg " + cls;
    div.textContent = text;
    var time = document.createElement("div");
    time.className = "time";
    time.textContent = new Date().toLocaleTimeString();
    div.appendChild(time);
    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
  }

  // ── Rendered output (v2 envelope: {v:2, kind:"output", mode, label, content, asset}) ──

  function sanitizeHtml(html) {
    var t = document.createElement("template");
    t.innerHTML = html;
    t.content.querySelectorAll("script, iframe, object, embed").forEach(function(el) { el.remove(); });
    t.content.querySelectorAll("*").forEach(function(el) {
      for (var i = el.attributes.length - 1; i >= 0; i--) {
        var attr = el.attributes[i];
        if (attr.name.toLowerCase().indexOf("on") === 0) el.removeAttribute(attr.name);
      }
    });
    return t.innerHTML;
  }

  // ── Rendering helpers (text / markdown / code / html) ──

  function escHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function inlineMd(t) {
    return t
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
      .replace(/\*([^*]+)\*/g, "<i>$1</i>")
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  }

  function mdToHtml(md) {
    var out = [];
    var lines = String(md || "").split(/\r?\n/);
    var inCode = false, codeBuf = [], inList = false;
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      if (/^\s*```/.test(line)) {
        if (inCode) {
          out.push("<pre><code>" + escHtml(codeBuf.join("\n")) + "</code></pre>");
          codeBuf = []; inCode = false;
        } else { inCode = true; }
        continue;
      }
      if (inCode) { codeBuf.push(line); continue; }
      var h = line.match(/^(#{1,4})\s+(.*)$/);
      if (h) { out.push('<div class="mdh">' + inlineMd(escHtml(h[2])) + '</div>'); continue; }
      var li = line.match(/^\s*[-*]\s+(.*)$/);
      if (li) {
        if (!inList) { out.push("<ul>"); inList = true; }
        out.push("<li>" + inlineMd(escHtml(li[1])) + "</li>");
        continue;
      }
      if (inList) { out.push("</ul>"); inList = false; }
      if (line.trim() === "") { continue; }
      out.push("<div>" + inlineMd(escHtml(line)) + "</div>");
    }
    if (inCode && codeBuf.length) {
      out.push("<pre><code>" + escHtml(codeBuf.join("\n")) + "</code></pre>");
    }
    if (inList) { out.push("</ul>"); }
    return out.join("");
  }

  function renderOutput(msg) {
    var div = document.createElement("div");
    div.className = "msg agent";
    var label = msg.label || "";
    var content = msg.content || "";

    function meta(t) {
      var l = document.createElement("div");
      l.className = "smeta";
      l.style.opacity = ".75";
      l.style.marginBottom = "5px";
      l.textContent = t;
      div.appendChild(l);
    }

    if (msg.mode === "image" && msg.asset) {
      if (label) { meta(label); }
      var img = document.createElement("img");
      img.src = msg.asset;
      img.style.maxWidth = "100%";
      img.style.borderRadius = "6px";
      div.appendChild(img);
    } else if (msg.mode === "html") {
      div.innerHTML = sanitizeHtml(content);
    } else if (msg.mode === "markdown") {
      div.innerHTML = mdToHtml(content);
    } else if (msg.mode === "code") {
      div.innerHTML = "<pre><code>" + escHtml(content) + "</code></pre>";
    } else if (msg.mode === "audio") {
      if (label) { meta(label); }
      var txt = document.createElement("div");
      txt.textContent = content || label;
      div.appendChild(txt);
      var canvas = document.createElement("canvas");
      canvas.className = "spectro";
      canvas.height = 40;
      div.appendChild(canvas);
      var btn = document.createElement("button");
      btn.textContent = "Play";
      btn.style.cssText = "margin-top:6px;padding:4px 10px;border-radius:6px;border:1px solid rgba(255,255,255,0.14);background:#12161C;color:#E6E8EB;cursor:pointer;";
      btn.onclick = function() { playTts(content || label, canvas); };
      div.appendChild(btn);
    } else {
      div.textContent = content || label;
    }

    var time = document.createElement("div");
    time.className = "time";
    time.textContent = new Date().toLocaleTimeString();
    div.appendChild(time);
    messages.appendChild(div);
    lastAgentMsgDiv = div;
    messages.scrollTop = messages.scrollHeight;
    // Note: rendering a bubble never changes run state — busy / answer mode
    // are driven by the server's lifecycle frames (agent_busy / agent_done /
    // ask / goal_complete).  Resetting them here used to drop the client out
    // of answer mode whenever an output bubble arrived mid-ask.
  }

  function renderHistory(list) {
    // Rebuild the transcript from the server's record (page reload / reconnect)
    if (!list) { return; }
    messages.innerHTML = "";
    for (var i = 0; i < list.length; i++) {
      var m = list[i];
      if (m.is_user) {
        addMsg(m.text || "", "user");
      } else if ((m.mode || "text") !== "text") {
        renderOutput({mode: m.mode, content: m.text || "", asset: m.asset || "", label: ""});
      } else {
        addMsg(m.text || "", "agent");
      }
    }
    messages.scrollTop = messages.scrollHeight;
  }

  // ── Send / Stop ──

  function sendMsg() {
    var text = inputBox.value.trim();
    if (!text) return;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      addMsg("Not connected - reconnecting ...", "agent");
      connectWS();
      return;
    }
    addMsg(text, "user");
    // If responding to an agent ask prompt, send JSON answer
    if (waitingForAnswer) {
      ws.send(JSON.stringify({type: "answer", text: text}));
      waitingForAnswer = false;
      endVoiceAnswer();
      inputBox.placeholder = "Message";
      agentBusy = true;
      stopBtn.style.display = "inline-block";
    } else {
      ws.send(text);
      agentBusy = true;
      stopBtn.style.display = "inline-block";
    }
    inputBox.value = "";
    inputBox.focus();
    sendBtn.classList.remove("show");
  }

  function sendStop() {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send("__STOP__");
    }
    stopBtn.style.display = "none";
    agentBusy = false;
    // A stopped turn is over: the next message is a NEW query, never an answer
    // to the question the chain was parked on.
    waitingForAnswer = false;
    inputBox.placeholder = "Message";
    endVoiceAnswer();
    inputBox.focus();
  }

  // ── Voice Recording (STT) ──

  // Use AudioContext to capture raw PCM and build a WAV blob
  let audioContext = null;
  let audioProcessor = null;
  let audioSource = null;
  let pcmChunks = [];

  function startRecording(event) {
    if (event) event.preventDefault();
    if (isRecording) return;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      addMsg("Microphone not available on this device/browser.", "agent");
      return;
    }
    // Allow recording while answering an ask prompt (voice reply).
    if (agentBusy && !waitingForAnswer) {
      addMsg("Please wait for the agent to finish before recording.", "agent");
      return;
    }
    isRecording = true;
    micBtn.classList.add("recording");
    recordingIndicator.style.display = "flex";
    pcmChunks = [];
    startInSpectro();

    navigator.mediaDevices.getUserMedia({ audio: true })
      .then(function(stream) {
        recordingStream = stream;
        audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
        audioSource = audioContext.createMediaStreamSource(stream);
        audioProcessor = audioContext.createScriptProcessor(4096, 1, 1);

        audioProcessor.onaudioprocess = function(e) {
          var input = e.inputBuffer.getChannelData(0);
          var pcm = new Int16Array(input.length);
          var sum = 0;
          for (var i = 0; i < input.length; i++) {
            var s = input[i];
            sum += s * s;
            pcm[i] = Math.max(-32768, Math.min(32767, s * 32768));
          }
          pcmChunks.push(pcm);
          // Feed the input spectrogram with this block's RMS level
          var rms = Math.sqrt(sum / Math.max(1, input.length));
          if (inLevels.length) {
            inLevels.shift();
            inLevels.push(Math.min(1, rms * 3.2));
          }
        };

        audioSource.connect(audioProcessor);
        audioProcessor.connect(audioContext.destination);
      })
      .catch(function(err) {
        addMsg("Microphone access denied: " + err.message, "agent");
        stopRecording();
      });
  }

  function stopRecording(event) {
    if (event) event.preventDefault();
    isRecording = false;
    micBtn.classList.remove("recording");
    recordingIndicator.style.display = "none";

    // Clean up audio resources
    if (audioProcessor) {
      try { audioProcessor.disconnect(); } catch(e) {}
      audioProcessor = null;
    }
    if (audioSource) {
      try { audioSource.disconnect(); } catch(e) {}
      audioSource = null;
    }
    if (audioContext) {
      try { audioContext.close(); } catch(e) {}
      audioContext = null;
    }
    if (recordingStream) {
      recordingStream.getTracks().forEach(function(t) { t.stop(); });
      recordingStream = null;
    }
    stopInSpectro();

    if (pcmChunks.length === 0) {
      addMsg("No audio captured.", "agent");
      return;
    }

    // Concatenate PCM chunks
    var totalLen = 0;
    for (var i = 0; i < pcmChunks.length; i++) totalLen += pcmChunks[i].length;
    var allPcm = new Int16Array(totalLen);
    var offset = 0;
    for (var i = 0; i < pcmChunks.length; i++) {
      allPcm.set(pcmChunks[i], offset);
      offset += pcmChunks[i].length;
    }

    // Build WAV blob
    var wavBlob = encodeWav(allPcm, 16000);
    sendAudioToStt(wavBlob);
  }

  function encodeWav(samples, sampleRate) {
    var numChannels = 1;
    var bitsPerSample = 16;
    var byteRate = sampleRate * numChannels * bitsPerSample / 8;
    var blockAlign = numChannels * bitsPerSample / 8;
    var dataSize = samples.length * (bitsPerSample / 8);
    var buffer = new ArrayBuffer(44 + dataSize);
    var view = new DataView(buffer);

    function writeString(offset, str) {
      for (var i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
    }

    writeString(0, "RIFF");
    view.setUint32(4, 36 + dataSize, true);
    writeString(8, "WAVE");
    writeString(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, numChannels, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, byteRate, true);
    view.setUint16(32, blockAlign, true);
    view.setUint16(34, bitsPerSample, true);
    writeString(36, "data");
    view.setUint32(40, dataSize, true);

    for (var i = 0; i < samples.length; i++) {
      view.setInt16(44 + i * 2, samples[i], true);
    }

    return new Blob([buffer], { type: "audio/wav" });
  }

  function sendAudioToStt(blob) {
    addMsg("Transcribing ...", "agent");
    var formData = new FormData();
    formData.append("audio", blob, "recording.wav");

    fetch("/stt", { method: "POST", body: formData })
      .then(function(r) { return r.json(); })
      .then(function(result) {
        if (result.success && result.text) {
          // Auto-fill and send the transcribed text
          inputBox.value = result.text;
          sendMsg();
        } else if (result.success && !result.text) {
          addMsg("No speech detected. Try again.", "agent");
        } else {
          addMsg("Transcription failed: " + (result.error || "unknown error"), "agent");
        }
      })
      .catch(function(err) {
        addMsg("STT request failed: " + err.message, "agent");
      });
  }

  // Push-to-talk: hold mic button to record, release to send

  // ── TTS Toggle ──

  function toggleTts() {
    ttsEnabled = !ttsEnabled;
    ttsToggle.classList.toggle("active", ttsEnabled);
    ttsToggle.title = ttsEnabled ? "Voice responses on" : "Toggle voice responses";
  }

  var outRaf = null;

  function playTts(text, canvas) {
    if (!ttsEnabled || !text) return;
    // Cache-busted audio element per play (mobile-friendly)
    var audio = new Audio("/tts?text=" + encodeURIComponent(text) + "&_=" + Date.now());
    if (canvas) {
      canvas.classList.add("on");
      var phase = 0;
      var draw = function () {
        phase += 0.25;
        var levels = [];
        for (var i = 0; i < 40; i++) {
          levels.push(0.22 + 0.78 * Math.abs(Math.sin(phase + i * 0.5)) * Math.abs(Math.sin(phase * 0.37 + i)));
        }
        drawSpectro(canvas, levels);
        outRaf = requestAnimationFrame(draw);
      };
      draw();
    }
    var stopAnim = function () {
      if (outRaf) { cancelAnimationFrame(outRaf); outRaf = null; }
      if (canvas) canvas.classList.remove("on");
    };
    audio.onended = stopAnim;
    audio.onerror = stopAnim;
    audio.play().catch(function (e) {
      // Auto-play may be blocked on mobile — stop the animation silently
      stopAnim();
      console.log("TTS auto-play blocked:", e.message);
    });
  }

  // ── Voice answers to ask prompts (talk directly to the question) ──

  function toggleAskRecording() {
    if (isRecording) { stopRecording(); } else { startRecording(); }
  }

  function restoreMicHandlers() {
    micBtn.onclick = null;
    micBtn.onmousedown = function(e) { startRecording(e); };
    micBtn.onmouseup = function(e) { stopRecording(e); };
    micBtn.onmouseleave = function(e) { stopRecording(e); };
    micBtn.ontouchstart = function(e) { startRecording(e); };
    micBtn.ontouchend = function(e) { stopRecording(e); };
    micBtn.ontouchcancel = function(e) { stopRecording(e); };
    micBtn.title = "Hold to record";
  }

  function beginVoiceAnswer() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return;
    askVoiceMode = true;
    // Tap-to-stop while the ask is pending (auto-started recording has no
    // pressed state to release, so click toggles instead).
    micBtn.onmousedown = null;
    micBtn.onmouseup = null;
    micBtn.onmouseleave = null;
    micBtn.ontouchstart = null;
    micBtn.ontouchend = null;
    micBtn.ontouchcancel = null;
    micBtn.onclick = toggleAskRecording;
    micBtn.title = "Tap to stop speaking";
    addMsg("Listening \u2014 speak your answer, tap the mic when done.", "agent");
    startRecording();
  }

  function endVoiceAnswer() {
    if (!askVoiceMode) return;
    askVoiceMode = false;
    if (isRecording) stopRecording();
    restoreMicHandlers();
  }

  // ── Rating & Feedback ──

  function addRatingRow(goalId) {
    if (!lastAgentMsgDiv) return;
    var row = document.createElement("div");
    row.className = "rating-row";
    for (var i = 1; i <= 5; i++) {
      var star = document.createElement("span");
      star.className = "star";
      star.textContent = String(i);
      star.dataset.idx = i;
      star.dataset.goalId = goalId;
      star.onclick = function() { rateStar(this); };
      row.appendChild(star);
    }
    lastAgentMsgDiv.appendChild(row);
  }

  function rateStar(el) {
    var idx = parseInt(el.dataset.idx);
    var goalId = el.dataset.goalId;
    var row = el.parentElement;
    var stars = row.querySelectorAll(".star");
    for (var j = 0; j < stars.length; j++) {
      stars[j].style.color = (j < idx) ? "#00C2A0" : "rgba(255,255,255,0.35)";
    }
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({type: "rating", stars: idx, goal_id: goalId}));
    }
    if (idx < 5) {
      showFeedbackBox(goalId, idx, row);
    }
  }

  function showFeedbackBox(goalId, stars, afterRow) {
    var fb = document.createElement("div");
    fb.className = "feedback-box";
    var ta = document.createElement("textarea");
    ta.placeholder = "What went wrong? What was OK?";
    var btn = document.createElement("button");
    btn.textContent = "Submit";
    btn.onclick = function() {
      var text = ta.value.trim();
      if (text && ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({type: "feedback", stars: stars, text: text, goal_id: goalId}));
      }
      fb.remove();
    };
    fb.appendChild(ta);
    fb.appendChild(btn);
    afterRow.parentElement.insertBefore(fb, afterRow.nextSibling);
  }

  // ── WebSocket ──

  function connectWS() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
    setStatus("connecting");
    ws = new WebSocket(WS_URL);
    ws.onopen = function () {
      setStatus("connected");
      reconnectDelay = 1000;
      sendWS({type: "list_coworkers"});
      sendWS({type: "list_chains"});
      sendWS({type: "list_schedules"});
    };
    ws.onmessage = function (evt) {
      if (evt.data === "__PONG__") return;
      // Try JSON parse for structured messages
      try {
        var msg = JSON.parse(evt.data);
        if (msg.type === "coworkers") { renderCoworkers(msg.coworkers || []); return; }
        if (msg.type === "chains") { renderChains(msg.chains || []); return; }
        if (msg.type === "schedules") { renderSchedules(msg.schedules || []); return; }
        if (msg.type === "notice") { addMsg(msg.text || "", "agent"); return; }
        if (msg.type === "goal_complete" && msg.goal_id) {
          addRatingRow(msg.goal_id);
          stopBtn.style.display = "none";
          agentBusy = false;
          waitingForAnswer = false;
          endVoiceAnswer();
          return;
        }
        if (msg.type === "agent_busy") {
          agentBusy = true;
          // A run started anywhere (desktop, phone, scheduler) shows the
          // chain-stop button on this client too.
          stopBtn.style.display = "inline-block";
          return;
        }
        if (msg.type === "agent_done") {
          agentBusy = false;
          stopBtn.style.display = "none";
          waitingForAnswer = false;
          endVoiceAnswer();
          return;
        }
        if (msg.type === "history") {
          renderHistory(msg.messages || []);
          return;
        }
        if (msg.type === "ask") {
          // Agent is asking the user for input mid-execution
          addMsg(msg.question, "agent");
          waitingForAnswer = true;
          inputBox.placeholder = "Your answer...";
          inputBox.focus();
          agentBusy = true;
          stopBtn.style.display = "inline-block";
          // Voice-first: start the mic so the user can answer by speaking.
          beginVoiceAnswer();
          return;
        }
        if (msg.kind === "output") {
          // Rendered output envelope (text/image/html/audio card)
          renderOutput(msg);
          return;
        }
      } catch(e) {
        // not JSON, treat as plain text
      }
      var div = document.createElement("div");
      div.className = "msg agent";
      div.textContent = evt.data;
      var time = document.createElement("div");
      time.className = "time";
      time.textContent = new Date().toLocaleTimeString();
      div.appendChild(time);
      messages.appendChild(div);
      lastAgentMsgDiv = div;
      messages.scrollTop = messages.scrollHeight;
      // If agent just responded, re-enable send
      stopBtn.style.display = "none";
      agentBusy = false;
      waitingForAnswer = false;
      // Auto-play TTS for agent responses
      playTts(evt.data);
    };
    ws.onclose = function () {
      setStatus("disconnected");
      scheduleReconnect();
    };
    ws.onerror = function () {};
  }

  function scheduleReconnect() {
    if (reconnectTimer) return;
    reconnectTimer = setTimeout(function () {
      reconnectTimer = null;
      connectWS();
      reconnectDelay = Math.min(reconnectDelay * 2, 30000);
    }, reconnectDelay);
  }

  // ── Init ──

  // Show/hide send button when typing
  inputBox.addEventListener("input", function () {
    sendBtn.classList.toggle("show", inputBox.value.trim().length > 0);
  });

  // Enter to send, Shift+Enter for newline
  inputBox.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMsg();
    }
  });

  // ── Views / panel plumbing ──

  function sendWS(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify(obj)); }
  }

  function showView(name) {
    ["chat", "schedules", "chains"].forEach(function (v) {
      var el = document.getElementById("view-" + v);
      if (el) el.classList.toggle("active", v === name);
      var nav = document.getElementById("nav" + v.charAt(0).toUpperCase() + v.slice(1));
      if (nav) nav.classList.toggle("active", v === name);
    });
    if (name === "schedules") loadSchedules();
    if (name === "chains") loadChains();
  }

  function renderCoworkers(list) {
    var sel = document.getElementById("coworkerSel");
    if (!sel) return;
    sel.innerHTML = "";
    list.forEach(function (c) {
      var o = document.createElement("option");
      o.value = c.id;
      o.textContent = c.name;
      if (c.active) o.selected = true;
      sel.appendChild(o);
    });
  }

  function selectCoworker(id) { if (id) sendWS({type: "select_coworker", id: id}); }

  // ── Chains panel ──

  function loadChains() { sendWS({type: "list_chains"}); }

  function renderChains(list) {
    var box = document.getElementById("chainList");
    var dl = document.getElementById("chainOptions");
    box.innerHTML = "";
    if (dl) dl.innerHTML = "";
    document.getElementById("chainCount").textContent = list.length + " chain(s)";
    if (!list.length) {
      box.innerHTML = '<div class="smeta" style="opacity:.6">No chains found.</div>';
      return;
    }
    list.forEach(function (c) {
      if (dl) {
        var op = document.createElement("option");
        op.value = c.rel;
        dl.appendChild(op);
      }
      var row = document.createElement("div");
      row.className = "srow";
      var nm = document.createElement("div");
      nm.className = "sname";
      nm.textContent = c.name;
      row.appendChild(nm);
      var acts = document.createElement("div");
      acts.className = "sacts";
      var run = document.createElement("button");
      run.className = "mini accent";
      run.textContent = "Run";
      run.onclick = function () { sendWS({type: "run_chain", rel: c.rel}); };
      acts.appendChild(run);
      row.appendChild(acts);
      box.appendChild(row);
    });
  }

  // ── Schedules panel ──

  var DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

  function loadSchedules() { sendWS({type: "list_schedules"}); }

  function whenText(s) {
    if (s.type === "once") return "once at " + (s.once_datetime || "?");
    if (s.type === "daily") return "daily at " + (s.daily_time || "?");
    if (s.type === "weekly") {
      var d = (s.days_of_week || []).map(function (i) { return DAYS[i] || "?"; }).join("/");
      return "weekly " + d + " at " + (s.weekly_time || "?");
    }
    if (s.type === "interval") return "every " + (s.interval_minutes || 0) + " min";
    return s.type || "?";
  }

  function renderSchedules(list) {
    var box = document.getElementById("schedList");
    box.innerHTML = "";
    document.getElementById("schedCount").textContent = list.length + " schedule(s)";
    if (!list.length) {
      box.innerHTML = '<div class="smeta" style="opacity:.6">No schedules yet.</div>';
      return;
    }
    list.forEach(function (s) {
      var row = document.createElement("div");
      row.className = "srow";
      var s1 = document.createElement("div");
      s1.className = "s1";
      var chk = document.createElement("input");
      chk.type = "checkbox";
      chk.checked = !!s.enabled;
      chk.onchange = function () { sendWS({type: "toggle_schedule", id: s.id, enabled: chk.checked}); };
      s1.appendChild(chk);
      var nm = document.createElement("div");
      nm.className = "sname";
      nm.textContent = s.name || String(s.id || "").slice(0, 8);
      s1.appendChild(nm);
      row.appendChild(s1);
      var meta = document.createElement("div");
      meta.className = "smeta";
      meta.textContent = whenText(s) + "  |  " + (s.chain_path || "") + "  |  next " + (s.next_run_readable || "-");
      row.appendChild(meta);
      var acts = document.createElement("div");
      acts.className = "sacts";
      var run = document.createElement("button");
      run.className = "mini";
      run.textContent = "Run";
      run.onclick = function () { sendWS({type: "run_schedule", id: s.id}); };
      var edit = document.createElement("button");
      edit.className = "mini";
      edit.textContent = "Edit";
      edit.onclick = function () { openSchedForm(s); };
      var del = document.createElement("button");
      del.className = "mini danger";
      del.textContent = "Delete";
      del.onclick = function () { sendWS({type: "delete_schedule", id: s.id}); };
      acts.appendChild(run);
      acts.appendChild(edit);
      acts.appendChild(del);
      row.appendChild(acts);
      box.appendChild(row);
    });
  }

  function schedTypeChanged() {
    var t = document.getElementById("sf_type").value;
    document.getElementById("sf_once_row").style.display = (t === "once") ? "flex" : "none";
    document.getElementById("sf_daily_row").style.display = (t === "daily") ? "flex" : "none";
    document.getElementById("sf_weekly_row").style.display = (t === "weekly") ? "flex" : "none";
    document.getElementById("sf_days_row").style.display = (t === "weekly") ? "flex" : "none";
    document.getElementById("sf_interval_row").style.display = (t === "interval") ? "flex" : "none";
  }

  function openSchedForm(s) {
    s = s || {};
    document.getElementById("sf_id").value = s.id || "";
    document.getElementById("sf_name").value = s.name || "";
    document.getElementById("sf_chain").value = s.chain_path || "";
    document.getElementById("sf_type").value = s.type || "daily";
    document.getElementById("sf_sandbox").checked = !!s.run_in_sandbox;
    if (s.once_datetime) document.getElementById("sf_oncedt").value = String(s.once_datetime).slice(0, 16);
    if (s.daily_time) document.getElementById("sf_dailytime").value = s.daily_time;
    if (s.weekly_time) document.getElementById("sf_weeklytime").value = s.weekly_time;
    if (s.interval_minutes) document.getElementById("sf_interval").value = s.interval_minutes;
    var days = s.days_of_week || [];
    var boxes = document.querySelectorAll(".sfday");
    for (var i = 0; i < boxes.length; i++) {
      boxes[i].checked = days.indexOf(parseInt(boxes[i].value, 10)) >= 0;
    }
    schedTypeChanged();
    document.getElementById("schedForm").classList.add("open");
  }

  function closeSchedForm() { document.getElementById("schedForm").classList.remove("open"); }

  function saveSchedule(ev) {
    ev.preventDefault();
    var t = document.getElementById("sf_type").value;
    var data = {
      name: document.getElementById("sf_name").value.trim(),
      type: t,
      chain_path: document.getElementById("sf_chain").value.trim(),
      enabled: true,
      run_in_sandbox: document.getElementById("sf_sandbox").checked,
      show_sandbox_window: true
    };
    var sid = document.getElementById("sf_id").value;
    if (sid) data.id = sid;
    if (!data.chain_path) { addMsg("Select a chain file for the schedule.", "agent"); return false; }
    if (t === "once") data.once_datetime = document.getElementById("sf_oncedt").value;
    if (t === "daily") data.daily_time = document.getElementById("sf_dailytime").value;
    if (t === "weekly") {
      data.weekly_time = document.getElementById("sf_weeklytime").value;
      data.days_of_week = [];
      var boxes = document.querySelectorAll(".sfday");
      for (var i = 0; i < boxes.length; i++) {
        if (boxes[i].checked) data.days_of_week.push(parseInt(boxes[i].value, 10));
      }
    }
    if (t === "interval") data.interval_minutes = parseInt(document.getElementById("sf_interval").value, 10) || 60;
    sendWS({type: "save_schedule", data: data});
    closeSchedForm();
    return false;
  }

  // ── Spectrogram ──

  function drawSpectro(canvas, levels) {
    if (!canvas) return;
    var dpr = window.devicePixelRatio || 1;
    var w = canvas.clientWidth || 320;
    if (canvas.width !== w * dpr) { canvas.width = w * dpr; canvas.height = 36 * dpr; }
    var ctx = canvas.getContext("2d");
    var W = canvas.width, H = canvas.height;
    ctx.clearRect(0, 0, W, H);
    var n = levels.length || 1;
    var bw = W / n;
    for (var i = 0; i < n; i++) {
      var v = Math.max(0, Math.min(1, levels[i]));
      var bh = v * (H - 2);
      var g = ctx.createLinearGradient(0, H, 0, H - bh);
      g.addColorStop(0, "rgba(0,194,160,0.25)");
      g.addColorStop(1, "rgba(0,194,160,0.95)");
      ctx.fillStyle = g;
      ctx.fillRect(i * bw + 1, H - bh, Math.max(1, bw - 2), bh);
    }
  }

  var inLevels = [];
  var inRaf = null;

  function startInSpectro() {
    var canvas = document.getElementById("inSpectro");
    if (!canvas) return;
    inLevels = new Array(40).fill(0);
    canvas.classList.add("on");
    var draw = function () {
      drawSpectro(canvas, inLevels);
      inRaf = requestAnimationFrame(draw);
    };
    draw();
  }

  function stopInSpectro() {
    if (inRaf) { cancelAnimationFrame(inRaf); inRaf = null; }
    var canvas = document.getElementById("inSpectro");
    if (canvas) {
      drawSpectro(canvas, new Array(40).fill(0));
      canvas.classList.remove("on");
    }
  }

  (function buildDayBoxes() {
    var box = document.getElementById("sf_daysbox");
    if (!box) return;
    DAYS.forEach(function (d, i) {
      var lb = document.createElement("label");
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.className = "sfday";
      cb.value = String(i);
      lb.appendChild(cb);
      lb.appendChild(document.createTextNode(d));
      box.appendChild(lb);
    });
  })();

  connectWS();
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Request handlers
# ---------------------------------------------------------------------------

def _make_handlers(state: ServerState):
    """Return request handler closures bound to *state*."""

    async def handle_index(request: web.Request) -> web.Response:
        # WebSocket URL follows the Host header so the page works whichever
        # address it was opened from (tailnet IP, LAN IP, localhost).
        host = request.headers.get("Host", "")
        if not host and state.local_url:
            host = state.local_url.split("://", 1)[-1]
        ws_url = "ws://" + (host.rstrip("/") or f"{_HOST}:8081") + "/ws"
        html = CHAT_HTML_TEMPLATE.replace("__WS_URL__", ws_url)
        return web.Response(text=html, content_type="text/html", charset="utf-8")

    async def handle_qr(request: web.Request) -> web.Response:
        url = state.remote_url or state.local_url or f"http://{_HOST}:8081"
        return web.json_response({
            "qr_data_uri": _qr_data_uri(url),
            "remote_url": state.remote_url,
            "active": state.active,
        })

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
        state.connected_clients += 1
        logger.info("WebSocket client connected (%d active)", state.connected_clients)

        # Store loop + send callback so worker threads can push responses
        loop = asyncio.get_event_loop()
        state.ws_loop = loop

        async def ws_send(text: str) -> None:
            try:
                await ws.send_str(text)
            except Exception:
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

        state.connected_clients -= 1
        state.ws_send = None
        state.ws_loop = None
        logger.info("WebSocket client disconnected (%d active)", state.connected_clients)
        return ws

    return handle_index, handle_qr, handle_status, handle_websocket


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
    hi, hq, hs, hw = _make_handlers(state)
    app = web.Application()
    app.router.add_get("/", hi)
    app.router.add_get("/qr", hq)
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
        server.set_remote_ip("100.64.0.5")  # optional: LAN/Tailscale IP for the QR
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
        self._actual_port: int | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._shutdown_event: asyncio.Event | None = None

    @property
    def state(self) -> ServerState:
        return self._state

    def set_remote_ip(self, ip: str) -> None:
        """Set the LAN/Tailscale IP used to build the remote URL + QR code."""
        self._remote_ip = (ip or "").strip()

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

        self._ready.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        # Wait for server to be ready (up to 15 seconds)
        ok = self._ready.wait(timeout=15.0)
        if not ok:
            logger.warning("AgentWebServer start timeout — port %d may be in use", self._port)
        return ok

    def stop(self) -> None:
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
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._serve())
        except Exception:
            logger.exception("AgentWebServer event loop crashed")
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
        state.remote_url = (
            f"http://{self._remote_ip}:{actual_port}" if self._remote_ip else None
        )
        state.active = True
        logger.info(
            "Agent Web Server -> %s (remote: %s)",
            state.local_url,
            state.remote_url or "none — set a Tailscale/LAN IP for remote access",
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
