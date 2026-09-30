/**
 * picker.js - dev-tools-style element picker for web conditional capture.
 *
 * Armed by the Python side (player/web/picker.py) on the chain's SHARED
 * browser: it draws a highlight box over the element under the cursor and, on
 * click, records that element's locator (reusing window.__wvpLoc.locatorFor)
 * plus its frame path into window.__wvpPickerResult.  ESC cancels.
 *
 * Load locators.js FIRST (the picker payload is locators.js + picker.js, just
 * like the recorder payload is locators.js + recorder.js).
 *
 * Every frame gets its own picker instance: the frame under the cursor draws
 * the highlight and, on click, resolves the INNERMOST element, then relays the
 * result to window.top via postMessage (so a pick inside a nested or
 * cross-origin iframe reaches the Python side, which polls the top frame).
 */
(function () {
  'use strict';
  if (window.__wvpPicker) return;
  window.__wvpPicker = true;

  var L = window.__wvpLoc;
  var isTop = window.self === window.top;
  var box = null;    // highlight rectangle
  var label = null;  // tag/name caption
  var armed = false;

  // Per-WINDOW id (frames report their top window's id), stamped on every pick.
  // A popup's pick is relayed to its opener, so the Python side sees it in the
  // OPENER - without this the recorded `_window_ordinal` named the opener and a
  // replay/form-fill never re-entered the popup.  The source window is found by
  // matching this id across the open windows.
  function windowId() {
    try {
      if (isTop) {
        if (!window.__wvpPickerWinId) {
          window.__wvpPickerWinId = String(Date.now()) + ':' + Math.random();
        }
        return window.__wvpPickerWinId;
      }
      if (window.top.__wvpPickerWinId) return window.top.__wvpPickerWinId;
    } catch (e) {}
    if (!window.__wvpPickerWinId) {
      window.__wvpPickerWinId = String(Date.now()) + ':' + Math.random();
    }
    return window.__wvpPickerWinId;
  }

  // Deepest element at a viewport point, piercing open shadow roots.
  function deepElementFromPoint(x, y) {
    var el = document.elementFromPoint(x, y);
    var guard = 0;
    while (el && el.shadowRoot && guard++ < 8) {
      var inner = el.shadowRoot.elementFromPoint(x, y);
      if (!inner || inner === el) break;
      el = inner;
    }
    return (el && el.nodeType === 1) ? el : null;
  }

  // Overlays must live in the browser's TOP LAYER: a <dialog showModal()>
  // popup (LinkedIn's Easy Apply, any modal library) paints ABOVE every
  // z-index, so a plain fixed overlay - even at z-index 2147483647 - is drawn
  // UNDERNEATH the popup and the picker looks like it never armed.  The
  // Popover API puts our highlight in that same top layer.  Feature-detected,
  // so a stub page (no popover support) is untouched.
  function raiseTopLayer(el) {
    try {
      if (!el || el.popover === undefined) return;
      if (!el.hasAttribute('popover')) el.setAttribute('popover', 'manual');
      if (!el.showPopover) return;
      if (el.matches && el.matches(':popover-open')) return;
      // showPopover() REFUSES an element whose computed display is none - and
      // the overlay starts hidden, so the call silently failed and the
      // highlight stayed UNDER the popup.  Make it displayable for the call;
      // the top-layer entry survives the restore and the caller shows it per
      // move.
      var was = el.style.display;
      if (was === 'none') el.style.display = 'block';
      try { el.showPopover(); } finally { el.style.display = was; }
    } catch (e) {}
  }

  function ensureOverlay() {
    // Re-create when the page DROPPED the nodes: a popup that opens (or an SPA
    // route change) can wipe html's children, and a stale `box` reference then
    // left the picker armed with NO highlight at all - it looked like the
    // picker never armed over the popup.
    if (box && !box.isConnected) { box = null; label = null; }
    if (box) return;
    box = document.createElement('div');
    box.setAttribute('data-wvp-picker', '1');
    box.style.cssText = 'position:fixed;inset:auto;margin:0;z-index:2147483647;pointer-events:none;' +
      'border:2px solid #00e0b8;background:rgba(0,224,184,0.12);' +
      'box-sizing:border-box;display:none;';
    label = document.createElement('div');
    label.setAttribute('data-wvp-picker', '1');
    label.style.cssText = 'position:fixed;inset:auto;margin:0;z-index:2147483647;pointer-events:none;' +
      'background:#00e0b8;color:#00201a;font:11px/1.4 monospace;padding:1px 4px;' +
      'border:0;border-radius:3px;display:none;white-space:nowrap;';
    document.documentElement.appendChild(box);
    document.documentElement.appendChild(label);
    raiseTopLayer(box);
    raiseTopLayer(label);
  }

  function removeOverlay() {
    if (box && box.parentNode) box.parentNode.removeChild(box);
    if (label && label.parentNode) label.parentNode.removeChild(label);
    box = null;
    label = null;
  }

  function describe(el) {
    var tag = (el.tagName || '').toLowerCase();
    var id = el.id ? ('#' + el.id) : '';
    var t = (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 40);
    return tag + id + (t ? '  "' + t + '"' : '');
  }

  function onMove(e) {
    if (!armed) return;
    ensureOverlay();          // self-heal: the page may have dropped the overlay
    // The overlay must be ABOVE whatever the pointer is over, so it is
    // re-raised on every move (a popup opened after arming is a new top-layer
    // entry and would otherwise cover the highlight).
    raiseTopLayer(box);
    raiseTopLayer(label);
    if (!armed || !box) return;
    var el = deepElementFromPoint(e.clientX, e.clientY);
    if (!el || el === document.body || el === document.documentElement) {
      box.style.display = 'none';
      label.style.display = 'none';
      return;
    }
    var r = el.getBoundingClientRect();
    box.style.display = 'block';
    box.style.left = r.left + 'px';
    box.style.top = r.top + 'px';
    box.style.width = r.width + 'px';
    box.style.height = r.height + 'px';
    label.style.display = 'block';
    label.textContent = describe(el);
    var ly = r.top - 18;
    if (ly < 0) ly = r.bottom + 2;
    label.style.left = Math.max(0, r.left) + 'px';
    label.style.top = ly + 'px';
  }

  function finish(result) {
    armed = false;
    removeOverlay();
    if (result && typeof result === 'object') {
      try { result.__win = windowId(); } catch (e) {}
    }
    if (isTop) {
      window.__wvpPickerResult = result;
    } else {
      // A frame (same- or cross-origin) hands the pick to the top document -
      // the Python side polls the top frame only.
      try { window.top.postMessage({ __wvpPick: 1, result: result }, '*'); } catch (e) {}
    }
    // A pick made in a POPUP must also reach the window the Python side polls
    // (the opener it armed).  Relaying here lets the poller stay on its own
    // window instead of switching to the popup each cycle - a switch would
    // raise the opener window mid-click and could swallow the user's pick.
    try {
      if (window.opener && !window.opener.closed) {
        window.opener.postMessage({ __wvpPick: 1, result: result }, '*');
      }
    } catch (e) {}
  }

  function onClick(e) {
    if (!armed) return;
    e.preventDefault();
    e.stopPropagation();
    ensureOverlay();
    raiseTopLayer(box);
    raiseTopLayer(label);
    var el = deepElementFromPoint(e.clientX, e.clientY);
    if (!el || el === document.body || el === document.documentElement) return; // keep picking
    var info = { path: [], crossOrigin: false };
    try { info = L.framePathFromTop(); } catch (err) {}
    var r = el.getBoundingClientRect();
    finish({
      locator: L.locatorFor(el),
      // Viewport-space box (getBoundingClientRect) - the layout-match target.
      rect: { x: Math.round(r.left), y: Math.round(r.top),
              w: Math.round(r.width), h: Math.round(r.height) },
      frame_path: info.path || [],
      cross_origin_frame: !!info.crossOrigin,
    });
  }

  function onKey(e) {
    if (!armed) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      finish({ cancelled: true });
    }
  }

  window.__wvpPickerStart = function () {
    ensureOverlay();
    armed = true;
    raiseTopLayer(box);
    raiseTopLayer(label);
  };
  // Re-asserted on EVERY Python poll: the highlight must exist and sit in the
  // current top layer, so a popup/frame that appeared since the last poll is
  // already covered when the user moves onto it.
  window.__wvpPickerRefresh = function () {
    if (!armed) return false;
    ensureOverlay();
    raiseTopLayer(box);
    raiseTopLayer(label);
    return true;
  };
  window.__wvpPickerStop = function () {
    armed = false;
    removeOverlay();
  };

  // Top document: collect a pick relayed from any frame (works cross-origin).
  if (isTop) {
    window.addEventListener('message', function (e) {
      var data = e && e.data;
      if (data && data.__wvpPick) {
        armed = false;
        removeOverlay();
        window.__wvpPickerResult = data.result;
      }
    }, false);
  }

  if (typeof MutationObserver === 'function') {
    try {
      new MutationObserver(function () {
        if (!armed) return;
        ensureOverlay();
        raiseTopLayer(box);
        raiseTopLayer(label);
      }).observe(document.documentElement, {childList: true});
    } catch (e) {}
  }

  // Top frame keeps no stale highlight when the pointer moves off into an
  // iframe (the frame under the cursor draws its own box instead).
  window.addEventListener('mouseout', function (e) {
    if (!armed || !box) return;
    var rt = e.relatedTarget;
    if (!rt || (rt.nodeType === 1 && (rt.tagName || '').toLowerCase() === 'iframe')) {
      box.style.display = 'none';
      if (label) label.style.display = 'none';
    }
  }, true);

  window.addEventListener('mousemove', onMove, true);
  window.addEventListener('click', onClick, true);
  window.addEventListener('keydown', onKey, true);
})();
