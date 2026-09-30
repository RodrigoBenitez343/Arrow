// In-page EXECUTION overlay for web replay - the orange twin of recorder.js's
// recording overlay.  Shown WHILE a web sequence replays: an orange box + name
// over the element each action is about to hit, a virtual cursor trail to that
// element, a ring pulsed at the action point, and a top-left action banner.
//
// The recorder's overlay is driven by pointermove; replay dispatches DOM events
// WITHOUT moving the OS cursor, so this one is driven by explicit calls from the
// Python engine (window.__wvpExecMark).  Every node is pointer-events:none (and
// tagged data-wvp-exec) so it never intercepts or re-records anything.
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

  var canvas = null;
  var banner = null;
  var pts = [];              // {x, y, t} cursor trail
  var marks = [];            // {x, y, w, h, t, label, click}
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
    prune(now);
    // Keep animating only while there is something to show (marks / trail fade
    // out after TTL).  A permanent 60 fps loop on every replayed page is pure
    // interference - __wvpExecMark re-arms it when a new action arrives.
    if (pts.length || marks.length) raf = window.requestAnimationFrame(draw);
  }

  // Called once per replayed action from the Python engine: box the element the
  // action is about to hit and extend the virtual cursor trail to its center.
  window.__wvpExecMark = function (rect, label, act) {
    if (act) action = String(act);
    var now = Date.now();
    if (rect && rect.w > 0 && rect.h > 0) {
      prune(now);
      pts.push({ x: rect.x + rect.w / 2, y: rect.y + rect.h / 2, t: now });
      marks.push({ x: rect.x, y: rect.y, w: rect.w, h: rect.h, t: now,
                   label: label || '', click: true });
    }
    ensure();
  };

  window.__wvpExecOn = function () {
    try { sessionStorage.setItem(ON_KEY, 'on'); } catch (e) {}
    pts = []; marks = []; action = '';
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
