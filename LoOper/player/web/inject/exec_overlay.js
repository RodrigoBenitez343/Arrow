// In-page EXECUTION overlay for web replay - the orange twin of recorder.js's
// recording overlay.  Shown WHILE a web sequence replays: an orange box + name
// over the element each action is about to hit, a VIRTUAL CURSOR that walks to
// that element, a ring pulsed at the action point, and a top-left action banner.
//
// The recorder's overlay is driven by pointermove; replay dispatches DOM events
// WITHOUT moving the OS cursor, so this one is driven by explicit calls from the
// Python engine (window.__wvpExecMark).  Every node is pointer-events:none (and
// tagged data-wvp-exec) so it never intercepts or re-records anything.
//
// The cursor is the whole point: the browser owns its own pointer here, so a web
// sequence never drags the user's real mouse (and the desktop player's animated
// cursor with it) across the screen.  Its glide carries the same human read the
// desktop path has - a curved, eased, distance-timed path (player/human_cursor).
//
// Gated on a sessionStorage flag (per-origin, survives same-origin navigation)
// exactly like the recording overlay, and re-asserted on every page / DOM
// change so a navigation or SPA re-render never loses it.
(function () {
  'use strict';
  // Idempotent, like locators.js (__wvpLoc) and recorder.js (__wvpRecorder).
  // The Python side evaluates this payload into the CURRENT document on every
  // enable() - and engine.run() calls it once per replay pass - and also
  // registers it through CDP for every future document.  Without this guard
  // each evaluation appends ANOTHER full-screen canvas and ANOTHER corner
  // banner at the same fixed position and stacks them, so the overlay looks
  // like it never clears.  The API (__wvpExecOn/Mark/Off) stays reachable: the
  // first evaluation defines it, and every later one just returns.
  if (window.__wvpExec) return;
  window.__wvpExec = true;
  var ON_KEY = 'looper.web.execOverlay';
  var TTL = 5000;            // ms a mark / trail point stays visible
  var COLOR = '255,140,0';   // orange
  // Virtual-cursor glide, mirroring player/human_cursor.py: a nearby target
  // still reads as motion, a far one never reads as "scripted slow".
  var GLIDE_MIN_MS = 180;
  var GLIDE_MAX_MS = 550;
  var GLIDE_PX_PER_S = 1600;
  var TRAIL_SAMPLE_MS = 45;  // how often the walked path drops a trail point

  var canvas = null;
  var banner = null;
  var pts = [];              // {x, y, t} cursor trail
  var marks = [];            // {x, y, w, h, t, label, click}
  var cursor = null;         // {x, y} where the virtual pointer rests
  var glide = null;          // in-flight walk to the action's target
  var trailedAt = 0;
  var action = '';
  var raf = null;
  var observed = false;

  function wanted() {
    try { return sessionStorage.getItem(ON_KEY) === 'on'; } catch (e) { return false; }
  }

  function el(tag, css) {
    var e = document.createElement(tag);
    try { e.setAttribute('data-wvp-exec', '1'); } catch (err) {}
    try { e.style.cssText = css; } catch (err) {}
    return e;
  }

  // The execution overlay must live in the browser's TOP LAYER: an open
  // <dialog showModal()> popup paints ABOVE every z-index, so the orange box /
  // banner stayed INVISIBLE while a form fill or replay worked on a modal.
  // The Popover API lifts it into that same layer; feature-detected, so a page
  // without popover support is untouched.
  function raiseTopLayer(node) {
    try {
      if (!node || node.popover === undefined) return;
      if (!node.hasAttribute('popover')) node.setAttribute('popover', 'manual');
      if (!node.showPopover) return;
      if (node.matches && node.matches(':popover-open')) return;
      // showPopover() REFUSES an element whose computed display is none, so a
      // hidden overlay silently failed to enter the top layer.  Make it
      // displayable for the call; the top-layer entry survives the restore.
      var was = node.style.display;
      if (was === 'none') node.style.display = 'block';
      try { node.showPopover(); } finally { node.style.display = was; }
    } catch (err) {}
  }

  function ensure() {
    if (!wanted()) return;
    var root = document.documentElement;
    if (!root) return;                       // injected before <html> - retried
    observeRoot();
    if (!canvas || !canvas.isConnected) {
      // A top-layer popover inherits the UA stylesheet's opaque `background:
      // Canvas` (WHITE) and a border - on a 100%x100% canvas that painted the
      // WHOLE PAGE WHITE for every action, so both are reset explicitly.
      canvas = el('canvas',
        'position:fixed;inset:auto;margin:0;left:0;top:0;width:100%;height:100%;' +
        'background:transparent;border:0;padding:0;overflow:hidden;' +
        'z-index:2147483646;pointer-events:none;');
      try { root.appendChild(canvas); } catch (e) { canvas = null; return; }
      raiseTopLayer(canvas);
    }
    if (!banner || !banner.isConnected) {
      banner = el('div',
        'position:fixed;inset:auto;margin:0;left:12px;top:12px;z-index:2147483647;pointer-events:none;' +
        'background:rgba(40,20,0,0.82);color:#ffd9a0;font:12px/1.5 monospace;' +
        'padding:8px 12px;border-radius:6px;border:1px solid rgba(255,140,0,0.60);' +
        'white-space:nowrap;');
      banner.textContent = 'EXECUTING';
      try { root.appendChild(banner); } catch (e) { banner = null; }
      raiseTopLayer(banner);
    }
    start();
  }

  function observeRoot() {
    if (observed || !document.documentElement) return;
    observed = true;
    try {
      new MutationObserver(reassert)
        .observe(document.documentElement, { childList: true });
    } catch (e) { observed = false; }
  }

  function reassert() {
    if (!wanted()) return;
    if (canvas && canvas.isConnected && banner && banner.isConnected) return;
    ensure();
  }

  function remove() {
    try { if (canvas && canvas.parentNode) canvas.parentNode.removeChild(canvas); } catch (e) {}
    try { if (banner && banner.parentNode) banner.parentNode.removeChild(banner); } catch (e) {}
    canvas = banner = null;
    // The walk dies with the canvas: a later enable() must not resume it from a
    // position this page no longer shows.
    cursor = glide = null;
  }

  function start() {
    if (raf === null) raf = window.requestAnimationFrame(draw);
  }
  function halt() {
    if (raf !== null) { try { cancelAnimationFrame(raf); } catch (e) {} raf = null; }
  }

  function prune(now) {
    while (pts.length && now - pts[0].t > TTL) pts.shift();
    while (marks.length && now - marks[0].t > TTL) marks.shift();
  }

  function easeInOut(t) {
    return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
  }

  // Start walking the virtual cursor to (x, y) and return the duration in ms.
  // The path is a quadratic Bezier through a jittered midpoint - the same
  // "noisy hand" read human_cursor.glide_os_cursor gives, just drawn in-page.
  function startGlide(x, y) {
    var from = cursor || {
      x: Math.round(window.innerWidth / 2),
      y: Math.round(window.innerHeight / 2),
    };
    var dx = x - from.x, dy = y - from.y;
    var dist = Math.sqrt(dx * dx + dy * dy);
    if (dist < 1) { cursor = { x: x, y: y }; return 0; }
    var dur = Math.min(GLIDE_MAX_MS,
                       Math.max(GLIDE_MIN_MS, dist / GLIDE_PX_PER_S * 1000));
    glide = {
      x0: from.x, y0: from.y,
      cx: (from.x + x) / 2 + (Math.random() - 0.5) * 0.24 * dist,
      cy: (from.y + y) / 2 + (Math.random() - 0.5) * 0.16 * dist,
      x1: x, y1: y, t0: Date.now(), dur: dur,
    };
    return dur;
  }

  // Advance the walk; returns the cursor position for THIS frame.
  function stepGlide(now) {
    if (!glide) return cursor;
    var u = Math.min(1, (now - glide.t0) / Math.max(1, glide.dur));
    var e = easeInOut(u), m = 1 - e;
    cursor = {
      x: m * m * glide.x0 + 2 * m * e * glide.cx + e * e * glide.x1,
      y: m * m * glide.y0 + 2 * m * e * glide.cy + e * e * glide.y1,
    };
    // Sample the path as it is WALKED, so the trail is the cursor's own line
    // instead of one straight hop between action points.
    if (now - trailedAt > TRAIL_SAMPLE_MS) {
      trailedAt = now;
      pts.push({ x: cursor.x, y: cursor.y, t: now });
    }
    if (u >= 1) glide = null;
    return cursor;
  }

  function drawCursor(ctx, x, y) {
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.lineTo(x, y + 17);
    ctx.lineTo(x + 4.5, y + 12.5);
    ctx.lineTo(x + 7.6, y + 19);
    ctx.lineTo(x + 10.6, y + 17.2);
    ctx.lineTo(x + 7.6, y + 11);
    ctx.lineTo(x + 13, y + 11);
    ctx.closePath();
    ctx.fillStyle = 'rgba(255,255,255,0.95)';
    ctx.fill();
    ctx.lineWidth = 1.4;
    ctx.lineJoin = 'round';
    ctx.strokeStyle = 'rgba(' + COLOR + ',0.95)';
    ctx.stroke();
    ctx.restore();
  }

  function draw() {
    raf = null;                       // idle until re-armed at the tail
    if (!canvas || !canvas.isConnected) return;
    var dpr = window.devicePixelRatio || 1;
    var w = window.innerWidth, h = window.innerHeight;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
    }
    var ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    var now = Date.now();
    var pen = stepGlide(now);

    ctx.lineWidth = 2;
    ctx.lineCap = 'round';
    for (var i = 1; i < pts.length; i++) {
      var age = now - pts[i].t;
      if (age > TTL) continue;
      ctx.strokeStyle = 'rgba(' + COLOR + ',' + (0.85 * (1 - age / TTL)).toFixed(3) + ')';
      ctx.beginPath();
      ctx.moveTo(pts[i - 1].x, pts[i - 1].y);
      ctx.lineTo(pts[i].x, pts[i].y);
      ctx.stroke();
    }

    for (var j = 0; j < marks.length; j++) {
      var m = marks[j];
      var a = now - m.t;
      if (a > TTL) continue;
      var alpha = 0.92 * (1 - a / TTL);
      ctx.strokeStyle = 'rgba(' + COLOR + ',' + alpha.toFixed(3) + ')';
      ctx.lineWidth = 2;
      ctx.strokeRect(m.x, m.y, m.w, m.h);
      if (m.click) {
        var r = 6 + 12 * Math.min(1, a / 600);
        ctx.beginPath();
        ctx.arc(m.x + m.w / 2, m.y + m.h / 2, r, 0, Math.PI * 2);
        ctx.stroke();
      }
      if (m.label) {
        ctx.font = '11px monospace';
        var tw = ctx.measureText(m.label).width;
        var ly = m.y - 16;
        if (ly < 0) ly = m.y + m.h + 2;
        ctx.fillStyle = 'rgba(' + COLOR + ',' + Math.max(0.3, alpha).toFixed(3) + ')';
        ctx.fillRect(m.x, ly, tw + 8, 15);
        ctx.fillStyle = 'rgba(0,32,26,' + alpha.toFixed(3) + ')';
        ctx.fillText(m.label, m.x + 4, ly + 11);
      }
    }

    // The action sits BESIDE the EXECUTING tag (one line), not under it.
    if (banner) banner.textContent = 'EXECUTING  \u00b7  ' + (action || '...');
    if (pen) drawCursor(ctx, pen.x, pen.y);
    prune(now);
    // Keep animating only while there is something to show (marks / trail fade
    // out after TTL, the cursor keeps walking until it reaches its target).
    // A permanent 60 fps loop on every replayed page is pure interference -
    // __wvpExecMark re-arms it when a new action arrives.
    if (pts.length || marks.length || glide) raf = window.requestAnimationFrame(draw);
  }

  // Called once per replayed action from the Python engine: box the element the
  // action is about to hit and walk the virtual cursor onto its center.  Returns
  // the walk's duration in ms so the caller can pace the action behind it (a web
  // sequence reads like a desktop one without touching the real pointer).
  window.__wvpExecMark = function (rect, label, act) {
    if (act) action = String(act);
    var now = Date.now();
    var dur = 0;
    if (rect && rect.w > 0 && rect.h > 0) {
      prune(now);
      var tx = rect.x + rect.w / 2, ty = rect.y + rect.h / 2;
      marks.push({ x: rect.x, y: rect.y, w: rect.w, h: rect.h, t: now,
                   label: label || '', click: true });
      // The overlay is gated on the sessionStorage flag: with it OFF nothing is
      // drawn, so parking the cursor (no walk) is the honest state.
      if (wanted()) {
        dur = startGlide(tx, ty);
      } else {
        cursor = { x: tx, y: ty };
        pts.push({ x: tx, y: ty, t: now });
      }
    }
    ensure();
    return dur;
  };

  window.__wvpExecOn = function () {
    try { sessionStorage.setItem(ON_KEY, 'on'); } catch (e) {}
    pts = []; marks = []; action = ''; cursor = null; glide = null;
    try { reassert(); } catch (e) {}
  };
  window.__wvpExecOff = function () {
    try { halt(); remove(); } catch (e) {}
    try { sessionStorage.setItem(ON_KEY, 'off'); } catch (e) {}
  };

  ensure();
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', reassert);
  }
  document.addEventListener('readystatechange', reassert);
  window.addEventListener('load', reassert);
  window.addEventListener('pageshow', reassert);
  try { new MutationObserver(reassert).observe(document, { childList: true }); } catch (e) {}
})();
