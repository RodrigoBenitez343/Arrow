/**
 * recorder.js - the LoOper in-page web recorder.
 *
 * Injected into every document (and every frame) by the Python side via CDP
 * Page.addScriptToEvaluateOnNewDocument, so it survives navigations and SPA
 * route changes. All capture listeners are registered on window (not
 * document): our script runs at document creation, BEFORE any page script, so
 * our window-capture listeners fire first and cannot be silenced by page code
 * that calls stopPropagation() on window. Raw DOM events are converted into
 * typed actions (click, hover, type, key, scroll, select, submit)
 * and aggregated into window.__webversionpw_events, which the Python side
 * polls and drains across every open tab.
 *
 * Cross-origin frames: each frame's recorder posts its events to window.top
 * via postMessage; the top-frame aggregator collects them. The Python side
 * settles for a short window after the stop signal so in-flight postMessages
 * land before the final drain.
 *
 * Headless-first: this produces DOM-only signals. Nothing here depends on
 * screenshots or vision; dom_snapshot is never written by default.
 */
(function () {
  'use strict';
  if (window.__wvpRecorder) return;
  window.__wvpRecorder = true;
  // Identify this build so the Python side can tell a stale in-page recorder
  // from a current one.  Deliberately AFTER the guard above: merely
  // re-evaluating the payload into a document that already runs an older
  // recorder must NOT mark it current (recorder._inject_or_refresh relies on
  // this to decide when to reload the page).
  window.__wvpRecorderFingerprint = window.__wvpRecorderPayloadFp || null;

  var L = window.__wvpLoc;

  var CFG = {
    scrollBurstTimeoutMs: 250, // LoOper-style same-direction scroll grouping
    keyFlushTimeoutMs: 600, // flush the typing buffer after this much idle
  };

  // Right Ctrl held + pointer moved = an intentional hover, mirroring the
  // desktop recorder's Right-Ctrl "move without clicking".  Hovers are never
  // captured by resting the pointer: that produced page-noise hovers (hover
  // menus, tooltips, tracking triggers) on every mouseover.
  var HOVER_HOLD_CODE = 'ControlRight';

  // Insert held while clicking marks that click as a REPEATING element (a
  // "kind"): the click is stored with a selector that matches the whole set so
  // replay can click each match in order.  Insert is a NON-modifier key, so the
  // browser never applies Ctrl/Shift/Alt/Meta click semantics to the marked
  // click.
  var ENTITY_HOLD_CODE = 'Insert';
  var entityKeyHeld = false;
  var entityKeyUpTs = 0; // last Insert release - a marker tap just before a click still counts

  // Match on BOTH code and key: a numpad Insert reports code 'Numpad0' (with
  // key 'Insert' when NumLock is off), so a code-only test would miss it.
  function isEntityKey(e) {
    return e.code === ENTITY_HOLD_CODE || e.key === ENTITY_HOLD_CODE;
  }

  var isTop = window.self === window.top;
  var frameInfo = L.framePathFromTop();
  // Every frame keeps its OWN local buffer (top and iframes alike): the
  // Python side drains each frame directly (WebDriver can switch into any
  // frame, including cross-origin ones), so interactions inside embeds are
  // recorded - not just the top document.
  var events = (window.__webversionpw_events = window.__webversionpw_events || []);

  // ------------------------------------------------------------------ persist

  // Session events are buffered IN MEMORY, which dies with the document: a
  // click that navigates (link, form submit, WebUI reload) destroys the page
  // with its own event - and everything since the last poll - still in the
  // buffer.  The TOP frame therefore mirrors every emitted event into
  // sessionStorage (append-only, capped): sessionStorage survives same-origin
  // navigation, so the Python drain reads the events back from the NEW
  // document and the click that caused the navigation is never lost.
  // WebUI pages (chrome://new-tab-page) block CDP new-document injection, so
  // this mirror is also what closes the post-reload recording gap there.
  var STORAGE_KEY = 'looper.web.events.top';
  var STORAGE_CAP = 1000;

  function persistEvents() {
    if (!isTop) return;
    try {
      var log = [];
      var v = sessionStorage.getItem(STORAGE_KEY);
      if (v) { try { log = JSON.parse(v); } catch (e) { log = []; } }
      if (!(log instanceof Array)) log = [];
      log = log.concat(events);
      if (log.length > STORAGE_CAP) log = log.slice(-STORAGE_CAP);
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(log));
    } catch (e) { /* storage blocked - memory-only capture still works */ }
  }

  // Settle everything that is still pending and persist, so a navigation
  // right after an interaction cannot lose the in-flight key buffer / press.
  function settleAndPersist() {
    flushKeyBuffer();
    var finished = finalizeScrollBurst();
    if (finished) emit(finished);
    if (pendingPress) {
      clearTimeout(pendingPress.timer);
      var p = pendingPress;
      pendingPress = null;
      if (p.el && p.el.nodeType === 1) {
        var cevt = baseFields('click', p.ts, p.el);
        cevt.button = '0';
        cevt.coordinates = p.coords;
        cevt.modifiers = p.mods || [];
        markEntityIfHeld(cevt, p.el); // a swallowed click still honours the marker
        emit(cevt);
      }
    }
    // The live buffer may already hold a pointerdown-emitted click (an armed
    // press) with no emit since - mirror the WHOLE buffer so a same-origin
    // navigation cannot lose it.  The Python drain dedupes by (type, ts), so
    // re-appending the already-mirrored events is safe.
    persistEvents();
  }

  // pagehide fires BEFORE the document is destroyed - the last chance to
  // flush pending state into the durable mirror (synchronous storage writes
  // are still allowed here).  The in-memory buffer dies with the document;
  // the Python drain reads the mirror from the next document instead.
  window.addEventListener('pagehide', function () {
    settleAndPersist();
  }, true);

  // ---------------------------------------------------------------- emit

  function emit(evt) {
    evt.url = location.href;
    evt.title = document.title;
    events.push(evt);
    persistEvents();
  }

  function baseFields(type, ts, el) {
    var evt = { type: type, ts: ts };
    if (el && el.nodeType === 1) {
      // Locator extraction must NEVER drop the user action: if the element
      // is in a state locatorFor cannot describe (exotic WebUI internals),
      // emit the event anyway with a minimal locator so replay still has the
      // recorded coordinates/geometry to fall back on.
      try {
        evt.locator = L.locatorFor(el);
      } catch (err) {
        try { console.warn('[looper] locatorFor failed:', err); } catch (e2) {}
        evt.locator = { tag: (el.tagName || '').toLowerCase() };
      }
    }
    evt.frame_path = frameInfo.path;
    evt.cross_origin_frame = frameInfo.crossOrigin;
    return evt;
  }

  // ------------------------------------------------------- typing buffer

  var keyBuffer = null; // {el, locator, text, startTs, lastTs, sensitive}

  function isEditable(el) {
    if (!el) return false;
    var tag = (el.tagName || '').toLowerCase();
    return tag === 'input' || tag === 'textarea' || el.isContentEditable;
  }

  function isSensitive(el) {
    if (!el) return false;
    var tag = (el.tagName || '').toLowerCase();
    if (tag === 'input' && (el.getAttribute('type') || '').toLowerCase() === 'password') return true;
    return !!(el.getAttribute && el.getAttribute('data-wvp-mask'));
  }

  function flushKeyBuffer() {
    if (!keyBuffer) return;
    var evt = baseFields('type', keyBuffer.startTs, keyBuffer.el);
    evt.value = keyBuffer.text;
    if (keyBuffer.sensitive) evt.sensitive = true;
    emit(evt);
    keyBuffer = null;
  }

  // ------------------------------------------------------ event listeners

  function nowTs() {
    return L.now();
  }

  // Deepest element in the COMPOSED tree (shadow-piercing).  Events fired on
  // shadow-DOM content (web components like the Chrome new-tab page) surface
  // e.target as the shadow HOST from a top-document listener; clicking the
  // host does nothing, so the REAL inner target must be recorded instead.
  function composedTarget(e) {
    var path = (typeof e.composedPath === 'function') ? e.composedPath() : [];
    var t = (path && path[0] && path[0].nodeType === 1) ? path[0] : e.target;
    return (t && t.nodeType === 1) ? t : null;
  }

  window.addEventListener('input', function (e) {
    // Events inside shadow roots retarget to the host from a top-document
    // listener - the composed target is the real editable element (e.g. the
    // Chrome new-tab search box lives inside ntp-app's shadow DOM, so e.target
    // would be ntp-app and the typing would never buffer).
    var t = composedTarget(e);
    if (!isEditable(t)) return;
    var ie = e;
    // IME composition fires intermediate insertCompositionText events; only
    // the commit (isComposing false) or a plain insert lands in the buffer,
    // otherwise composed text would be duplicated ("你你好").
    if (ie.isComposing) return;
    var inputType = ie.inputType || '';
    var data = ie.data;

    // insertReplacementText covers autofill; a missing inputType (older
    // engines) with data still means text was inserted - never drop typing.
    if (inputType === 'insertText' || inputType === 'insertCompositionText' ||
        inputType === 'insertFromPaste' || inputType === 'insertFromDrop' ||
        inputType === 'insertReplacementText' || inputType === '') {
      if (data) {
        if (!keyBuffer || keyBuffer.el !== t) {
          flushKeyBuffer();
          keyBuffer = {
            el: t,
            locator: L.locatorFor(t),
            text: '',
            startTs: nowTs(),
            lastTs: nowTs(),
            sensitive: isSensitive(t),
          };
        }
        keyBuffer.text += data;
        keyBuffer.lastTs = nowTs();
      }
    } else if (inputType === 'deleteContentBackward' || inputType === 'deleteContentForward') {
      flushKeyBuffer();
      var key = inputType === 'deleteContentBackward' ? 'Backspace' : 'Delete';
      emitKeydown(key, []);
    } else if (inputType === 'insertLineBreak') {
      flushKeyBuffer();
      emitKeydown('Enter', []);
    }
  }, true);

  function modsOf(e) {
    var mods = [];
    if (e.ctrlKey) mods.push('Ctrl');
    if (e.metaKey) mods.push('Meta');
    if (e.altKey) mods.push('Alt');
    if (e.shiftKey) mods.push('Shift');
    return mods;
  }

  function emitKeydown(key, modifiers, state) {
    // Keys are BARE presses - no locator, no element binding.  Like desktop
    // sequences they replay at whatever element is focused when the chain
    // runs: the session's own clicks/types (or the PREVIOUS web sequence
    // node) do the focusing, so composing sequences stays stateless and the
    // key lands where the last sequence left off - never on a stale target
    // re-resolved from an old recording.
    var evt = baseFields('key', nowTs(), null);
    evt.key = key;
    evt.modifiers = modifiers;
    evt.state = state || 'down';
    emit(evt);
  }

  window.addEventListener('keydown', function (e) {
    // ESC is the global "stop recording and save" control signal.
    if (e.key === 'Escape') {
      emit(baseFields('stop', nowTs(), e.target));
      return;
    }
    // Right Ctrl is the hover-hold chord - never a key action.
    if (e.code === HOVER_HOLD_CODE) {
      beginHoverHold(e);
      return;
    }
    // Insert is the repeating-element marker - never a key action.
    if (isEntityKey(e)) {
      entityKeyHeld = true;
      return;
    }
    var t = composedTarget(e);
    if (isEditable(t) && e.key && e.key.length === 1 && !e.ctrlKey && !e.altKey && !e.metaKey) {
      return; // plain text goes through the input buffer, not here
    }
    if (e.repeat) return; // key-repeat is not a new user action
    flushKeyBuffer();
    // Every other key press records as a key action - including single-char
    // hotkeys on non-editable targets (YouTube j/k, Gmail navigation) that
    // were previously dropped.
    emitKeydown(e.key || 'Unknown', modsOf(e), 'down');
  }, true);

  window.addEventListener('keyup', function (e) {
    // ESC is the global stop chord - its keyup must never become an action,
    // or every stop press floods the session with Escape noise (sessions of
    // pure Escape keyups replay as "0/0 actions").
    if (e.key === 'Escape') return;
    // Right Ctrl release ends the hover-hold and emits the hover action.
    if (e.code === HOVER_HOLD_CODE) {
      endHoverHold(e);
      return;
    }
    if (isEntityKey(e)) {
      entityKeyHeld = false;
      entityKeyUpTs = nowTs();
      // Releasing the marker ENDS the gesture: the two marked clicks that
      // define a row set must come from ONE Insert hold, so a later marked
      // click can never pair with a stale one.
      markAnchor = null;
      return;
    }
    var t = composedTarget(e);
    if (isEditable(t) && e.key && e.key.length === 1 && !e.ctrlKey && !e.altKey && !e.metaKey) {
      return; // plain text belongs to the input buffer, not the key stream
    }
    if (e.repeat) return;
    // Key releases are bare too - see emitKeydown (no element binding).
    var evt = baseFields('key', nowTs(), null);
    evt.key = e.key || 'Unknown';
    evt.modifiers = modsOf(e);
    evt.state = 'up';
    emit(evt);
  }, true);

  ['click', 'dblclick', 'contextmenu'].forEach(function (type) {
    window.addEventListener(type, function (e) {
      // A real click supersedes any pending pointerdown fallback press.
      if (pendingPress) {
        clearTimeout(pendingPress.timer);
        pendingPress = null;
      }
      flushKeyBuffer();
      var t = composedTarget(e);
      if (!t) return;
      if (type === 'click' && armedMatches(t)) {
        // This press was already emitted from pointerdown (the page may have
        // swallowed the click and navigated).  Mirror the buffer so a
        // same-origin navigation cannot lose it, then disarm: a second real
        // click of a double-click is recorded normally.
        persistEvents();
        armedClick = null;
        return;
      }
      var evt = baseFields(type, nowTs(), t);
      evt.button = e.button != null ? String(e.button) : '0';
      evt.coordinates = { x: e.clientX, y: e.clientY };
      evt.modifiers = modsOf(e); // Ctrl+click / Shift+click replay support
      // Insert held = mark this click as a repeating element ("kind"): the
      // locator is replaced with a selector that matches the whole set.
      markEntityIfHeld(evt, t);
      emit(evt);
    }, true);
  });

  // ---------------------------------------------------- click fallback

  // Some pages / WebUI components (e.g. Chrome's new-tab customize panel)
  // consume the `click` event - or swallow it entirely - while still
  // dispatching pointerdown.  Remember the last pointerdown and, if no real
  // click lands on the same element within the window, emit it as a click so
  // the interaction is still recorded.  Movement cancels the press (a drag is
  // not a click); a real click on any element cancels it too.
  var pendingPress = null; // {el, coords, ts, timer}
  var pressClickWindowMs = 600;

  // Clicks that NAVIGATE are frequently swallowed: Google-style result links
  // start navigation on mousedown, before the real `click` event, and a
  // cross-origin jump destroys the page (and its sessionStorage mirror) with
  // the old origin - so a 600ms fallback timer would die with the document
  // and the click would never be recorded.  For ACTIVATABLE targets
  // (links/buttons/submits) the click is therefore emitted straight from
  // pointerdown into the LIVE buffer; the Python drain picks it up within its
  // poll interval, long before the destination commits.  A confirming real
  // `click` mirrors it (same-origin durability), a drag retracts it, and
  // pagehide mirrors the buffer for same-origin navigations.
  function isActivatable(el) {
    if (!el || el.nodeType !== 1) return false;
    var tag = (el.tagName || '').toLowerCase();
    if (tag === 'a') return !!(el.getAttribute && el.getAttribute('href'));
    if (tag === 'button') return true;
    if (tag === 'input') {
      var ty = ((el.getAttribute && el.getAttribute('type')) || 'text').toLowerCase();
      return ty === 'submit' || ty === 'button';
    }
    return !!(el.getAttribute && el.getAttribute('role') === 'button');
  }

  // Clicks land on the DEEPEST element under the pointer - for a link that is
  // almost always its text child (an h3/span/div), never the <a> itself.  The
  // press is armed on the nearest ACTIVATABLE ancestor so text-child clicks
  // get the same instant pointerdown capture as a direct click on the <a>:
  // Google-style links navigate on mousedown and swallow the click event
  // before any 600ms fallback timer could fire, so the arm at pointerdown is
  // the only chance to record them.  Replay resolves the locator on the
  // anchor - a better target than the text child anyway.
  function nearestActivatable(el) {
    var n = el;
    while (n && n.nodeType === 1) {
      if (isActivatable(n)) return n;
      n = n.parentElement;
    }
    return null;
  }

  var armedClick = null; // {el, liveEvt} - click emitted at pointerdown

  function armPrimaryClick(t, coords, mods) {
    var evt = baseFields('click', nowTs(), t);
    evt.button = '0';
    evt.coordinates = coords;
    evt.modifiers = mods;
    // Stamp the page identity: an armed click is pushed straight into the LIVE
    // buffer (not through emit()), and without a url _finalize_actions treats
    // the whole session as non-page and saves it empty.
    evt.url = location.href;
    evt.title = document.title;
    // Activatable list cards (links / role=button) take THIS path, so the
    // Insert marker must be applied here too - not only in the click listener.
    markEntityIfHeld(evt, t);
    events.push(evt); // LIVE buffer only - mirroring happens on confirm/pagehide
    // Keep the press geometry: the pointermove handler compares against it to
    // turn a drag into a retraction (a missing coords threw on every move).
    armedClick = { el: t, coords: coords, liveEvt: evt };
  }

  function retractArmed() {
    if (!armedClick) return;
    var i = events.indexOf(armedClick.liveEvt);
    if (i >= 0) events.splice(i, 1);
    armedClick = null;
  }

  function armedMatches(t) {
    if (!armedClick) return false;
    // No age limit: the arm is cleared by every later pointerdown, retract
    // (drag), or matching real click, so a real click on the armed element is
    // ALWAYS the same press - however slow the release.  An expiry would turn
    // a slow deliberate click (press held >600ms) into two recorded clicks:
    // the pointerdown emit plus the real one after the window lapsed.
    var el = t;
    while (el && el.nodeType === 1) {
      if (el === armedClick.el) return true;
      el = el.parentElement;
    }
    return false;
  }

  window.addEventListener('pointerdown', function (e) {
    flushKeyBuffer();
    var t = composedTarget(e);
    if (!t) return;
    if (pendingPress) {
      clearTimeout(pendingPress.timer);
      pendingPress = null;
    }
    if (e.button === 0) {
      var act = nearestActivatable(t);
      if (act) {
        // Navigation may start on mousedown and swallow the click - emit now,
        // on the activatable ancestor (the anchor) that replay can actually
        // click.  A second press of a double-click on the same target is NOT
        // re-armed (the first armed press still covers it; its real click
        // will follow).
        if (!armedMatches(act)) {
          retractArmed();
          armPrimaryClick(act, { x: e.clientX, y: e.clientY }, modsOf(e));
        }
        return;
      }
    }
    retractArmed();
    pendingPress = {
      el: t,
      coords: { x: e.clientX, y: e.clientY },
      ts: nowTs(),
      mods: modsOf(e),
      timer: setTimeout(function () {
        if (!pendingPress) return;
        var p = pendingPress;
        pendingPress = null;
        var evt = baseFields('click', p.ts, p.el);
        evt.button = '0';
        evt.coordinates = p.coords;
        evt.modifiers = p.mods;
        markEntityIfHeld(evt, p.el); // a swallowed click still honours the marker
        emit(evt);
      }, pressClickWindowMs),
    };
  }, true);

  window.addEventListener('pointermove', function (e) {
    if (armedClick &&
        (Math.abs(e.clientX - armedClick.coords.x) > 6 ||
         Math.abs(e.clientY - armedClick.coords.y) > 6)) {
      retractArmed();  // a drag, not a click - undo the pointerdown emit
    }
    if (pendingPress &&
        (Math.abs(e.clientX - pendingPress.coords.x) > 6 ||
         Math.abs(e.clientY - pendingPress.coords.y) > 6)) {
      // A drag, not a click: cancel the click fallback but KEEP the source
      // element and start coords so pointerup can emit a drag action (the
      // desktop recorder's drag_drop equivalent).
      clearTimeout(pendingPress.timer);
      pendingPress.dragging = true;
    }
  }, true);

  window.addEventListener('pointerup', function (e) {
    if (pendingPress && pendingPress.dragging) {
      var p = pendingPress;
      pendingPress = null;
      var drop = composedTarget(e);
      var evt = baseFields('drag', p.ts, p.el);
      evt.coordinates = p.coords;
      evt.context = {
        drop_x: e.clientX,
        drop_y: e.clientY,
        drop_locator: drop && drop.nodeType === 1 ? L.locatorFor(drop) : null,
      };
      emit(evt);
    }
  }, true);

  window.addEventListener('change', function (e) {
    var t = e.target;
    if (!t || t.nodeType !== 1) return;
    if ((t.tagName || '').toLowerCase() === 'select') {
      var evt = baseFields('select', nowTs(), t);
      evt.value = t.value != null ? String(t.value) : null;
      emit(evt);
    }
  }, true);

  window.addEventListener('focusin', function (e) {
    var t = e.target;
    if (!t || t.nodeType !== 1) return;
    if (!isEditable(t)) return;
    var evt = baseFields('focus', nowTs(), t);
    emit(evt);
  }, true);

  window.addEventListener('submit', function (e) {
    flushKeyBuffer();
    var t = e.target;
    if (t && t.nodeType === 1) emit(baseFields('submit', nowTs(), t));
  }, true);

  // ------------------------------------------- scroll burst grouping (LoOper)

  var scrollBurst = null;

  function finalizeScrollBurst() {
    if (!scrollBurst) return null;
    var evt = baseFields('scroll', scrollBurst.startTs, scrollBurst.el);
    evt.scroll = {
      total_delta: scrollBurst.total_delta,
      steps: scrollBurst.steps,
      start: scrollBurst.start,
      end: scrollBurst.end,
    };
    scrollBurst = null;
    return evt;
  }

  window.addEventListener('wheel', function (e) {
    var ts = nowTs();
    var delta = Math.round(e.deltaY || 0);
    if (scrollBurst &&
        ts - scrollBurst.lastTs <= CFG.scrollBurstTimeoutMs &&
        delta !== 0 &&
        (delta > 0) === (scrollBurst.total_delta > 0)) {
      scrollBurst.total_delta += delta;
      scrollBurst.steps += 1;
      scrollBurst.lastTs = ts;
      scrollBurst.end = { x: e.clientX, y: e.clientY };
    } else {
      var finished = finalizeScrollBurst();
      if (delta !== 0) {
        scrollBurst = {
          el: e.target,
          total_delta: delta,
          steps: 1,
          start: { x: e.clientX, y: e.clientY },
          end: { x: e.clientX, y: e.clientY },
          startTs: ts,
          lastTs: ts,
        };
      }
      if (finished) emit(finished);
    }
  }, true);

  // --------------------------------------------- hover (Right-Ctrl hold)

  // Hover is captured INTENTIONALLY: hold Right Ctrl, move the pointer over
  // the target, release - one hover action is emitted for the element under
  // the cursor.  The element is captured at RELEASE with a shadow-piercing
  // elementFromPoint, so the innermost element is recorded and its frame path
  // travels with the event; replay resolves it inside the right frame via the
  // existing resolve_element frame ladder (the hover is frame-independent).
  var hoverHold = null; // {moved, last: {x, y}}

  // Deepest element at a viewport point, piercing open shadow roots (a plain
  // document.elementFromPoint returns the shadow HOST for web components).
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

  function beginHoverHold(e) {
    if (hoverHold) return; // auto-repeat of a held Right Ctrl
    flushKeyBuffer();
    hoverHold = { moved: false, last: { x: e.clientX, y: e.clientY } };
  }

  // -------------------------------------- repeating-element (kind) selector

  // Derive the selectors matching the WHOLE set a repeating element belongs to
  // (a "kind"), so replay can act on each match in order.  Ordered strongest
  // first: a STABLE container-scoped identity, then a stable list-item data-*
  // on the element or an ancestor (data-* / name / type / role / test-id - the
  // reliable anchors, e.g. LinkedIn job cards are li[data-occludable-job-id]),
  // then the clicked element's OWN SIBLINGS under its direct parent (the
  // primary list strategy: the items living alongside it), then the page-wide
  // attribute hooks, then classes last.  Hashed CSS-module classes and changing
  // inner text are unreliable, so they are only a fallback; a bare tag is never
  // used on its own (it would match far too much - and an ANCESTOR-scoped bare
  // tag matches the whole nested subtree, outer div first).  A list is returned
  // because a session recorded on one page shape must still resolve on another:
  // replay tries every candidate in order until one matches.
  // ---- deep query: the current document PLUS its open shadow roots ---------
  // The recorder runs inside the element's OWN document, so a repeating set
  // lives here (or in a shadow root of it) - never in a child frame.  The
  // shadow scan is memoized per top-level derivation so several deepMatches()
  // calls share one traversal (resetShadowScan() at each entry point).
  var shadowScan = null;
  function resetShadowScan() { shadowScan = null; }
  function shadowDescendants() {
    if (shadowScan) return shadowScan;
    shadowScan = [];
    (function walk(root, depth) {
      if (!root || depth > 12 || !root.querySelectorAll) return;
      var all = null;
      try { all = root.querySelectorAll('*'); } catch (e) { return; }
      for (var i = 0; i < all.length; i++) {
        var host = all[i];
        if (!host.shadowRoot) continue;
        var inner = null;
        try { inner = host.shadowRoot.querySelectorAll('*'); } catch (e2) { inner = null; }
        if (inner) for (var kk = 0; kk < inner.length; kk++) shadowScan.push(inner[kk]);
        walk(host.shadowRoot, depth + 1);
      }
    })(document, 0);
    return shadowScan;
  }
  function matchesEl(node, sel) {
    try { return !!(node && node.matches && node.matches(sel)); } catch (e) { return false; }
  }
  function deepMatches(sel) {
    var out = [];
    try {
      var d = document.querySelectorAll(sel);
      for (var i = 0; i < d.length; i++) out.push(d[i]);
    } catch (e) { return []; }
    var sh = shadowDescendants();
    for (var j = 0; j < sh.length; j++) {
      if (out.indexOf(sh[j]) < 0 && matchesEl(sh[j], sel)) out.push(sh[j]);
    }
    return out;
  }
  function count(sel) { return deepMatches(sel).length; }
  function esc(c) { return String(c).replace(/([^a-zA-Z0-9_-])/g, '\\$1'); }
  function escVal(v) { return String(v).replace(/(["\\])/g, '\\$1'); }
  function tagOf(node) { return ((node && node.tagName) || '').toLowerCase(); }
  function attribNames(node) {
    return (node && node.getAttributeNames && node.getAttributeNames()) || [];
  }
  function getAttr(node, name) {
    try { return node && node.getAttribute ? node.getAttribute(name) : null; } catch (e) { return null; }
  }

  // Site-authored automation hooks - the STABLE anchors a derived selector may
  // scope itself to.  Module-level because BOTH kind derivations need the same
  // notion of "stable": the single-mark guess below and the pair derivation
  // (pairAttempt) that builds a repeating set from two marked siblings.
  var TEST_IDS = [
    'data-testid', 'data-test-id', 'data-test', 'data-cy', 'data-qa',
    'data-qa-id', 'data-hook', 'data-automation-id', 'data-component-id'
  ];

  function entitySelectorFor(el) {
    if (!el || el.nodeType !== 1) return null;
    resetShadowScan();

    var PREFERRED = [
      'data-occludable-job-id', 'data-job-id', 'data-urn', 'data-id',
      'data-item-id', 'data-key'
    ];
    // Attributes whose VALUE identifies the set (a radio group name, an input
    // type, a widget role).  Value form only - a presence form would be far too
    // broad (every named input / every [type] element would match).
    var VALUED = ['name', 'type', 'role'];

    var tag = tagOf(el);
    var role = getAttr(el, 'role') || '';
    var typeSel = tag + (role ? '[role="' + role + '"]' : '');

    // Self + ancestors, collected once and reused by every strategy below.
    var nodes = [];
    for (var node = el; node && node.nodeType === 1 && nodes.length < 8; ) {
      nodes.push(node);
      node = node.parentElement;
    }
    var ancestors = [];
    for (var an = el.parentElement; an && an.nodeType === 1 && ancestors.length < 8;
         an = an.parentElement) {
      ancestors.push(an);
    }
    function hitsAncestor(sel) {
      for (var ai = 0; ai < ancestors.length; ai++) {
        if (matchesEl(ancestors[ai], sel)) return true;
      }
      return false;
    }

    // Ordered, deduped candidate list.  Class-based candidates carry the
    // ancestor guard (a class combo matching a container would click the
    // container); attribute/structural candidates do not, because an identity
    // attribute on an ancestor IS the repeating item (li[data-occludable-job-id]).
    var cands = [];
    var seenSel = {};

    // A "kind" is a set of SIBLINGS that CONTAINS the clicked element - or one
    // of its ancestors, when the click landed inside a row.  Only such a set is
    // the repeating list; "more than one match" is NOT enough:
    //   * a page-wide attribute selector (div[data-testid]) matches hundreds of
    //     scattered elements and its FIRST match in document order can sit in
    //     the site's nav bar - clicking the nav bar instead of the list;
    //   * a nested subtree (an ancestor-scoped descendant) puts parents before
    //     children, so the outer div wins;
    //   * the clicked element's own children are not the row either.
    //
    // Diagnostics: WHY each candidate was rejected, surfaced in the recording
    // log - "entity=null" alone cannot tell "nothing matched" from "matched but
    // not the row set", and the page DOM is not visible from the code.
    var diag = [];
    function diagAdd(what, why) {
      if (diag.length < 16) diag.push(what + ' :: ' + why);
    }
    function sameParent(list) {
      var box = list[0].parentElement || null;
      for (var i = 1; i < list.length; i++) {
        if ((list[i].parentElement || null) !== box) return false;
      }
      return true;
    }
    function containsRow(list) {
      var box = list[0].parentElement || null;
      for (var a = el; a && a.nodeType === 1; a = a.parentElement) {
        if (list.indexOf(a) >= 0) return true;
        if (a === box) break;
      }
      return false;
    }

    function addSel(sel, guardAncestor) {
      if (!sel || seenSel[sel]) return false;
      if (guardAncestor && hitsAncestor(sel)) {
        diagAdd(sel, 'rejected: matches an ancestor');
        return false;
      }
      var m = deepMatches(sel);
      if (m.length < 2) { diagAdd(sel, 'rejected: n=' + m.length); return false; }
      if (!sameParent(m)) {
        diagAdd(sel, 'rejected: n=' + m.length + ' multi-parent');
        return false;
      }
      if (!containsRow(m)) {
        diagAdd(sel, 'rejected: n=' + m.length + ' does not contain the row');
        return false;
      }
      seenSel[sel] = true;
      cands.push(sel);
      diagAdd(sel, 'OK n=' + m.length);
      return true;
    }

    // A stable container anchor: id, a test-id hook, or a data-* (value form
    // when it has a value, else presence).  Never a hashed class.
    function stableAnchorCss(node) {
      if (!node || node.nodeType !== 1) return null;
      var t = tagOf(node);
      if (node.id && !/[\s"'<>\\]/.test(node.id)) return t + '#' + esc(node.id);
      for (var i = 0; i < TEST_IDS.length; i++) {
        var tv = getAttr(node, TEST_IDS[i]);
        if (tv) return t + '[' + TEST_IDS[i] + '="' + escVal(tv) + '"]';
      }
      var names = attribNames(node);
      for (var j = 0; j < names.length; j++) {
        if (names[j].indexOf('data-') !== 0) continue;
        var dv = getAttr(node, names[j]);
        if (dv) return t + '[' + names[j] + '="' + escVal(dv) + '"]';
        return t + '[' + names[j] + ']';
      }
      return null;
    }

    // The element's OWN stable attribute selector, used to scope a kind.
    function selfStableSel(node) {
      for (var i = 0; i < VALUED.length; i++) {
        var v = getAttr(node, VALUED[i]);
        if (v) return tagOf(node) + '[' + VALUED[i] + '="' + escVal(v) + '"]';
      }
      for (var j = 0; j < TEST_IDS.length; j++) {
        if (getAttr(node, TEST_IDS[j])) return tagOf(node) + '[' + TEST_IDS[j] + ']';
      }
      var names = attribNames(node);
      for (var k = 0; k < names.length; k++) {
        if (names[k].indexOf('data-') === 0) return tagOf(node) + '[' + names[k] + ']';
      }
      return null;
    }

    // 1) Stable container-SCOPED identity: an ancestor's stable anchor + this
    //    element's OWN precise identity (a radio group name / a test-id).  Never
    //    the bare type: an ancestor-scoped DESCENDANT selector matches a whole
    //    nested subtree (outer div first), which is not the repeating set.
    var par = el.parentElement;
    diagAdd('el ' + tag + (el.classList ? '.' + Array.prototype.slice.call(el.classList).join('.') : ''),
            'par ' + (par && par.nodeType === 1
              ? tagOf(par) + (par.id ? '#' + par.id : '') +
                (par.classList ? '.' + Array.prototype.slice.call(par.classList).join('.') : '')
              : 'none'));
    var anchorCss = null;
    for (var anc = par;
         anc && anc.nodeType === 1 && anc !== document.body &&
         anc !== document.documentElement; anc = anc.parentElement) {
      anchorCss = stableAnchorCss(anc);
      if (anchorCss) break;
    }
    if (anchorCss) {
      // Prefer the element's OWN identity (a radio group name / test-id) over
      // the bare type, so a form's radios are not confused with its text boxes.
      var own = selfStableSel(el);
      if (own) addSel(anchorCss + ' ' + own, false);
      if (role) addSel(anchorCss + ' ' + typeSel, false);
    }

    // 2) Global STABLE kind: a stable attribute on the element or an ancestor.
    //    PREFERRED list-item data-* first (the strongest identity), then the
    //    valued attrs (radio name / input type / widget role), then test-id
    //    hooks, then any other data-*.
    for (var p = 0; p < PREFERRED.length; p++) {
      for (var pn = 0; pn < nodes.length; pn++) {
        if (attribNames(nodes[pn]).indexOf(PREFERRED[p]) >= 0) {
          addSel(tagOf(nodes[pn]) + '[' + PREFERRED[p] + ']', false);
        }
      }
    }

    // 3) ROWS - the repeating ITEM set: the elements of the same kind living
    //    alongside the clicked one in the SAME container (job cards, mail rows,
    //    list rows).  Climb from the clicked element to the nearest ancestor
    //    whose CHILD - the one holding the clicked element - has >=2 same-tag
    //    siblings sharing its class signature: that level is the repeating
    //    container.  A row's inner title/meta divs do NOT share classes while
    //    the rows do, so a click ON a row and a click INSIDE a row both resolve
    //    to the same row set (the clicked element's OWN parent is only the row
    //    set when the click landed ON the row).  A direct child of that
    //    container can never be one of the element's own ancestors (never an
    //    OUTER container) and the child combinator keeps it out of the INNER
    //    subtree - unlike a descendant selector anchored on an ancestor, which
    //    matched the whole nested tree (outer div first) and made replay click
    //    outer divs on a list.  It must also rank ABOVE the page-wide attribute
    //    hooks below for exactly that reason.
    var rowBoxCss = null;
    var rowTag = null;
    for (var rowWalk = el, rowUp = 0;
         rowUp < 6 && rowWalk && rowWalk.parentElement; rowUp++) {
      var rowBox = rowWalk.parentElement;
      if (rowBox === document.body || rowBox === document.documentElement) break;
      var rowOwn = Array.prototype.slice.call(rowWalk.classList || []);
      var rowSibs = rowBox.children || [];
      var rowShared = 0;
      var rowHits = 0;
      for (var ri = 0; ri < rowOwn.length && !rowShared; ri++) {
        rowHits = 0;
        for (var rj = 0; rj < rowSibs.length; rj++) {
          var rowSib = rowSibs[rj];
          if (!rowSib.classList || tagOf(rowSib) !== tagOf(rowWalk)) continue;
          if (Array.prototype.indexOf.call(rowSib.classList, rowOwn[ri]) >= 0) rowHits++;
        }
        if (rowHits >= 2) rowShared++;
      }
      if (rowShared) {
        // The container locator must be UNIQUE.  A stable anchor shared by
        // SEVERAL containers (LinkedIn renders more than one
        // data-testid="lazy-column") makes the row selector span them all, so
        // its first match lands in a DIFFERENT section and the set stops being
        // the clicked item's list.  Fall back to the container's own path,
        // which is built to be unique.
        var rowBoxSel = stableAnchorCss(rowBox);
        if (rowBoxSel && deepMatches(rowBoxSel).length !== 1) rowBoxSel = null;
        if (!rowBoxSel) {
          try {
            var rowLoc = L.locatorFor(rowBox);
            rowBoxSel = rowLoc && rowLoc.css;
          } catch (e) {}
        }
        if (rowBoxSel) { rowBoxCss = rowBoxSel; rowTag = tagOf(rowWalk); break; }
      }
      diagAdd('climb ' + tagOf(rowWalk) + '.' + rowOwn.join('.'),
              rowShared ? 'shared=' + rowHits : 'no-shared siblings=' + rowSibs.length);
      rowWalk = rowBox;
    }
    if (rowBoxCss) {
      if (role) addSel(rowBoxCss + ' > ' + rowTag + '[role="' + role + '"]', false);
      addSel(rowBoxCss + ' > ' + rowTag, false);
    }

    for (var v = 0; v < VALUED.length; v++) {
      for (var vn = 0; vn < nodes.length; vn++) {
        var vval = getAttr(nodes[vn], VALUED[v]);
        if (vval) addSel(tagOf(nodes[vn]) + '[' + VALUED[v] + '="' + escVal(vval) + '"]', false);
      }
    }
    for (var h = 0; h < TEST_IDS.length; h++) {
      for (var hn = 0; hn < nodes.length; hn++) {
        if (getAttr(nodes[hn], TEST_IDS[h])) {
          addSel(tagOf(nodes[hn]) + '[' + TEST_IDS[h] + ']', false);
        }
      }
    }

    // 4) The element's OWN classes.  Per-row CSS-module classes differ (a
    //    per-instance token, or a state token like "selected"), and generic
    //    layout classes also sit on ANCESTORS/containers.  So: reject any combo
    //    that matches an ANCESTOR (that would resolve to - and click - a
    //    container, which in document order comes BEFORE the items), then keep
    //    the combo matching the MOST SIBLINGS (tie -> more specific, then the
    //    wider document set).  Generated-looking tokens are skipped when a
    //    stable candidate already exists; a single class is a last resort.
    var VOLATILE = /\d{3,}|[0-9a-f]{6,}/i;
    function looksVolatile(c) { return !!c && VOLATILE.test(c); }
    var classesAll = Array.prototype.slice.call(el.classList || [])
      .filter(function (c) { return c && c.length <= 60; })
      .slice(0, 6);
    var classesStable = classesAll.filter(function (c) { return !looksVolatile(c); });
    var classes = cands.length ? classesStable
                               : (classesStable.length ? classesStable : classesAll);
    function combos(arr, k) {
      var out = [];
      (function walk(start, cur) {
        if (cur.length === k) { out.push(cur.slice()); return; }
        for (var i = start; i < arr.length; i++) { cur.push(arr[i]); walk(i + 1, cur); cur.pop(); }
      })(0, []);
      return out;
    }
    var sibs = (par && par.children) ? par.children : [];
    function sibHits(sel) {
      var n = 0;
      for (var sj = 0; sj < sibs.length; sj++) if (matchesEl(sibs[sj], sel)) n++;
      return n;
    }
    if (classes.length >= 2) {
      var bestSel = null, bestSib = 1, bestArity = 0, bestDoc = 0;
      for (var k = classes.length; k >= 2; k--) {
        var cs = combos(classes, k);
        for (var ci = 0; ci < cs.length; ci++) {
          var csel = tag + '.' + cs[ci].map(esc).join('.');
          if (hitsAncestor(csel)) continue;      // would resolve to a container
          var dc = count(csel);
          if (dc < 2) continue;
          var sb = sibs.length ? sibHits(csel) : dc;  // no parent -> doc count
          if (sb < 2) continue;
          if (sb > bestSib ||
              (sb === bestSib && (cs[ci].length > bestArity ||
               (cs[ci].length === bestArity && dc > bestDoc)))) {
            bestSib = sb; bestArity = cs[ci].length; bestDoc = dc; bestSel = csel;
          }
        }
      }
      if (bestSel) addSel(bestSel, true);
    }
    for (var si = 0; si < classes.length; si++) {
      addSel(tag + '.' + esc(classes[si]), true);
    }

    // 5) Any other data-* attribute (e.g. data-testid, a framework scope token)
    //    matching >1.  Deliberately LAST among the stable strategies: a generic
    //    data-* is frequently page-wide (data-v-*, data-reactroot), so it ranks
    //    below the sibling group.
    for (var dn = 0; dn < nodes.length; dn++) {
      var dnames = attribNames(nodes[dn]);
      for (var dk = 0; dk < dnames.length; dk++) {
        if (dnames[dk].indexOf('data-') !== 0) continue;
        addSel(tagOf(nodes[dn]) + '[' + dnames[dk] + ']', false);
      }
    }

    // 6) Parent-scoped fallback.
    if (par && par.nodeType === 1) {
      var ptag = tagOf(par);
      var pc = Array.prototype.slice.call(par.classList || []).filter(function (c) { return c; });
      var pcands = [];
      if (pc.length) pcands.push(ptag + '.' + esc(pc[0]) + ' > ' + tag);
      if (par.id) pcands.push('#' + esc(par.id) + ' > ' + tag);
      for (var pk = 0; pk < pcands.length; pk++) addSel(pcands[pk], false);
    }

    if (!cands.length) {
      diagAdd('none', 'no candidate was a row set');
    }
    return {
      selector: cands[0] || null,
      selectors: cands.slice(0, 4),
      key_attr: entityKeyAttr(el),
      diag: diag,
    };
  }

  function entityKeyAttr(el) {
    // The stable identity may sit on the element OR on an ancestor (LinkedIn
    // job cards: li[data-occludable-job-id] wrapping the clicked div), so walk
    // the chain exactly like the kind derivation does.
    var guard = 0;
    for (var node = el; node && node.nodeType === 1 && guard++ < 8;
         node = node.parentElement) {
      var names = (node.getAttributeNames && node.getAttributeNames()) || [];
      for (var i = 0; i < names.length; i++) {
        if (/^data-(id|key|urn|uid|occludable|job|item)/i.test(names[i])) return names[i];
      }
    }
    return null;
  }

  // ------------------------------------ pair-defined kind (two marked clicks)
  //
  // ONE marked click can only be GUESSED at, which is why the kind drifted onto
  // outer containers, nav bars and inner divs.  A SECOND marked click on a
  // sibling is ground truth: what the two share is the row identity, the level
  // where they diverge is the row container, and the derived selector is then
  // VALIDATED against both marks - so a wrong guess can never be recorded.  The
  // clicks may be the rows themselves or anything INSIDE two different rows
  // (the usual case): the lowest common ancestor settles it either way.
  var markAnchor = null;  // {evt, el, ts, type} - the previous marked click
  var pairReason = '';
  var PAIR_WINDOW_MS = 30000;

  // A wrapper with `display: contents` (LinkedIn's data-display-contents) has NO
  // box: replaying a click on it fails with "element not interactable: has no
  // size and location".  Only a boxed element is a row worth clicking.
  function hasBox(node) {
    if (!node || !node.getBoundingClientRect) return true; // unknown -> assume clickable
    try {
      var r = node.getBoundingClientRect();
      return !!(r && (r.width > 0 || r.height > 0));
    } catch (e) { return true; }
  }

  // LCA-child ... node (inclusive), or null when node is not below the LCA.
  function chainTo(lca, node) {
    var chain = [];
    for (var n = node; n && n !== lca; n = n.parentElement) chain.unshift(n);
    return (chain.length && chain[0].parentElement === lca) ? chain : null;
  }

  function sharedClassesOf(a, b) {
    var out = [];
    var ca = (a && a.classList) ? Array.prototype.slice.call(a.classList) : [];
    for (var i = 0; i < ca.length; i++) {
      if (b && b.classList && Array.prototype.indexOf.call(b.classList, ca[i]) >= 0) {
        out.push(ca[i]);
      }
    }
    return out;
  }

  function sharedDataAttrOf(a, b) {
    var names = attribNames(a);
    for (var i = 0; i < names.length; i++) {
      if (names[i].indexOf('data-') !== 0) continue;
      if (getAttr(b, names[i]) !== null) return names[i];
    }
    return null;
  }

  function pairAttempt(a, b) {
    pairReason = '';
    if (!a || !b || a === b || a.nodeType !== 1 || b.nodeType !== 1) {
      pairReason = 'needs two different elements';
      return null;
    }
    resetShadowScan();
    var ancA = [];
    for (var x = a; x && x.nodeType === 1; x = x.parentElement) ancA.push(x);
    var ancB = [];
    for (var y = b; y && y.nodeType === 1; y = y.parentElement) ancB.push(y);
    var lca = null;
    for (var i = 0; i < ancA.length && !lca; i++) {
      if (ancB.indexOf(ancA[i]) >= 0) lca = ancA[i];
    }
    if (!lca || lca === document.body || lca === document.documentElement) {
      pairReason = 'no common container';
      return null;
    }
    if (ancA.indexOf(lca) > 8 || ancB.indexOf(lca) > 8) {
      pairReason = 'marks are too far apart';
      return null;
    }
    var chainA = chainTo(lca, a);
    var chainB = chainTo(lca, b);
    if (!chainA || !chainB || chainA.length !== chainB.length) {
      pairReason = 'marks sit at different depths';
      return null;
    }
    // The ROW is the HIGHEST CLICKABLE element on each path: an outer wrapper
    // may be `display: contents` (no box) and can never be clicked, so the real
    // row is the boxed element inside it.
    var rowIdx = -1;
    var rowIdxB = -1;
    for (var k = 0; k < chainA.length; k++) {
      if (hasBox(chainA[k])) rowIdx = k;
    }
    for (var k2 = 0; k2 < chainB.length; k2++) {
      if (hasBox(chainB[k2])) rowIdxB = k2;
    }
    if (rowIdx < 0 || rowIdxB < 0) {
      pairReason = 'no clickable row (every wrapper is zero-sized)';
      return null;
    }
    if (rowIdx !== rowIdxB) {
      pairReason = 'rows sit at different depths';
      return null;
    }
    var rowA = chainA[rowIdx];
    var rowB = chainB[rowIdx];
    if (rowA === rowB) {
      pairReason = 'both marks are the same row';
      return null;
    }
    if (tagOf(rowA) !== tagOf(rowB)) {
      pairReason = 'different row tags (' + tagOf(rowA) + '/' + tagOf(rowB) + ')';
      return null;
    }
    var boxCss = null;
    try { var bl = L.locatorFor(lca); boxCss = bl && bl.css; } catch (e) {}
    if (!boxCss || deepMatches(boxCss).length !== 1) {
      pairReason = 'container has no unique selector';
      return null;
    }
    // Parent path: the LCA plus every level ABOVE the row (identical tags,
    // classes shared by the two marks).  This also expresses one wrapper PER
    // row, where the rows are not siblings of each other.
    var parentSel = boxCss;
    for (var lvl = 0; lvl < rowIdx; lvl++) {
      if (tagOf(chainA[lvl]) !== tagOf(chainB[lvl])) {
        pairReason = 'paths differ at level ' + lvl;
        return null;
      }
      parentSel += ' > ' + tagOf(chainA[lvl]);
      var lvlShared = sharedClassesOf(chainA[lvl], chainB[lvl]);
      if (lvlShared.length) parentSel += '.' + lvlShared.map(esc).join('.');
    }
    var rowTag = tagOf(rowA);
    // What the two marks AGREE on is the row identity: the classes both carry,
    // a data-* both carry, a role both carry.  That agreement is the only thing
    // that survives a re-render, so it is what replay must navigate by.
    var rowIdents = [];
    var shared = sharedClassesOf(rowA, rowB);
    if (shared.length) {
      rowIdents.push(rowTag + '.' + shared.map(esc).join('.'));
    }
    var attr = sharedDataAttrOf(rowA, rowB);
    if (attr) rowIdents.push(rowTag + '[' + attr + ']');
    var role = getAttr(rowA, 'role');
    if (role && role === getAttr(rowB, 'role')) {
      rowIdents.push(rowTag + '[role="' + escVal(role) + '"]');
    }
    // Ordered strongest first: the recorded CONTAINER path (exact for the page
    // this was recorded on), then the same row identity scoped to the row's
    // nearest STABLE ancestor (id / test-id - a hook that outlives the wrapper
    // levels), then the identity UNSCOPED.  Wrapper levels come and go between
    // one link of a site and another, and a container path that resolved at
    // record time can therefore match NOTHING later; the last two scopes do not
    // depend on it.  Replay walks this list in order, so the precise scope
    // still wins whenever it resolves - and when NONE resolves, replay has a
    // real ladder to work with instead of the recorded single element (which
    // is the one row the marks were made on, so it would be clicked on every
    // pass and the set would never advance).
    var cands = [];
    for (var ci0 = 0; ci0 < rowIdents.length; ci0++) {
      cands.push(parentSel + ' > ' + rowIdents[ci0]);
    }
    cands.push(parentSel + ' > ' + rowTag);
    var stableAnc = null;
    for (var san = rowA.parentElement; san && san.nodeType === 1 &&
         san !== document.body && san !== document.documentElement;
         san = san.parentElement) {
      if (san.id && !/[\s"'<>\\]/.test(san.id)) {
        stableAnc = tagOf(san) + '#' + esc(san.id);
        break;
      }
      for (var sti = 0; sti < TEST_IDS.length; sti++) {
        var tv = getAttr(san, TEST_IDS[sti]);
        if (tv) {
          stableAnc = tagOf(san) + '[' + TEST_IDS[sti] + '="' + escVal(tv) + '"]';
          break;
        }
      }
      if (stableAnc) break;
    }
    if (stableAnc) {
      for (var ci1 = 0; ci1 < rowIdents.length; ci1++) {
        cands.push(stableAnc + ' ' + rowIdents[ci1]);
      }
    }
    for (var ci2 = 0; ci2 < rowIdents.length; ci2++) {
      cands.push(rowIdents[ci2]);
    }
    // Both marks are ground truth: a candidate that misses either one is not
    // the sibling set.  EVERY verified candidate is kept, in order - the first
    // is the strongest, and the rest are the ladder replay falls back through.
    var verified = [];
    for (var c = 0; c < cands.length; c++) {
      if (verified.indexOf(cands[c]) >= 0) continue;
      var m = deepMatches(cands[c]);
      if (m.length < 2) continue;
      if (m.indexOf(rowA) < 0 || m.indexOf(rowB) < 0) continue;
      verified.push(cands[c]);
    }
    if (verified.length) {
      return {
        selector: verified[0],
        selectors: verified.slice(0, 5),
        key_attr: entityKeyAttr(rowA),
        pair_rows: deepMatches(verified[0]).length,
      };
    }
    pairReason = 'no verified candidate (' + cands.join(' | ') + ')';
    return null;
  }

  // A marker tap just before the click still counts (the user may release
  // Insert a heartbeat before the click lands).
  var ENTITY_HOLD_GRACE_MS = 1500;

  function entityMarkActive() {
    // The page's own Insert state, OR the state the Python side bridges in from
    // the OS hook (window.__wvpMarkerHeld) - the low-level hook sees Insert even
    // when the browser page never receives the key.
    if (entityKeyHeld || window.__wvpMarkerHeld) return true;
    var pageUp = (!window.__wvpMarkerHeld && typeof window.__wvpMarkerStamp === 'number')
      ? window.__wvpMarkerStamp : 0;
    var up = Math.max(entityKeyUpTs || 0, pageUp);
    return up > 0 && (nowTs() - up) <= ENTITY_HOLD_GRACE_MS;
  }

  // Apply the repeating-element marker to a click when Insert is held.  Shared
  // by BOTH click paths: the plain `click` listener and the armed pointerdown
  // path (armPrimaryClick).  Activatable targets (links, buttons, role=button
  // list cards) take the armed path, so marking only in the click listener left
  // `entity` null for exactly the list items the marker is meant for.
  function markEntityIfHeld(evt, el) {
    var active = entityMarkActive();
    var ent = entitySelectorFor(el);
    // A SECOND marked click on a sibling is ground truth: pair the two and the
    // derived kind is verified against both, instead of guessed from one.  The
    // first click only NAMED the element, so this one carries the kind and the
    // first is dropped at save time (supersedes_ts) - one repeating action.
    var pair = null;
    var prev = markAnchor;
    if (active && prev && prev.el && prev.el !== el &&
        (evt.ts - prev.ts) <= PAIR_WINDOW_MS) {
      pair = pairAttempt(prev.el, el);
      if (pair) {
        pair.supersedes_ts = prev.ts;
        pair.supersedes_type = prev.type || 'click';
      }
    }
    // Surface the outcome in the recording log (recorder.py): a marker key that
    // never reached the page is distinguishable from an underivable kind.
    window.__wvpEntityMark = {
      held: !!entityKeyHeld,
      bridged: !!window.__wvpMarkerHeld,
      active: active,
      selector: (ent && ent.selector) || null,
      selectors: (ent && ent.selectors) || null,
      key_attr: (ent && ent.key_attr) || null,
      diag: (ent && ent.diag) || null,
      pair: pair ? pair.selector : null,
      pair_diag: pair ? '' : (pairReason || ''),
    };
    if (!active) return;
    markAnchor = { evt: evt, el: el, ts: evt.ts, type: evt.type };
    if (!pair && (!ent || !ent.selector)) return;
    evt.entity = pair || ent;
    evt.modifiers = (evt.modifiers || []).filter(function (m) { return m !== 'Ctrl'; });
    // Keep the FULL locator baseFields() already recorded (id / text / aria /
    // xpath / ancestor_css / viewport).  Replay refuses the recorded locator
    // when the kind (set) selector no longer matches the live DOM, so
    // overwriting it here would leave a marked click with no other identity.
  }

  function endHoverHold(e) {
    if (!hoverHold) return;
    var hold = hoverHold;
    hoverHold = null;
    if (!hold.moved) return; // a bare Right-Ctrl tap is not a hover
    var el = deepElementFromPoint(hold.last.x, hold.last.y);
    if (!el || el === document.body || el === document.documentElement) return;
    var evt = baseFields('hover', nowTs(), el);
    evt.coordinates = { x: hold.last.x, y: hold.last.y };
    emit(evt);
  }

  window.addEventListener('pointermove', function (e) {
    if (hoverHold &&
        (Math.abs(e.clientX - hoverHold.last.x) > 4 ||
         Math.abs(e.clientY - hoverHold.last.y) > 4)) {
      hoverHold.moved = true;
      hoverHold.last = { x: e.clientX, y: e.clientY };
    }
  }, true);

  // No navigation detection: URL changes are NEVER recorded as actions.
  // Moving between pages must be a real user interaction (clicking a link,
  // submitting a form, or browser back/forward via Alt+Left / Alt+Right) so
  // SPA route changes stay page state instead of becoming hard-navigations
  // that break replay on dynamic content.  Replay is interaction-driven too:
  // the browser's recorded clicks/typing/keys drive navigation (native Enter
  // submits, Alt+Left goes back) and the chain's shared workbench browser
  // carries the page state - no automatic driver.get(url) is performed.

  // Auto-flush the typing buffer when idle (e.g. user pauses mid-field).
  setInterval(function () {
    if (keyBuffer && nowTs() - keyBuffer.lastTs > CFG.keyFlushTimeoutMs) flushKeyBuffer();
  }, 300);

  // ------------------------------- flush API used by the Python side at stop

  // Shadow-piercing frame enumeration.  WebDriver's find_elements cannot see
  // an <iframe> that lives INSIDE a shadow root, so the Python drain - which
  // walks frames via WebDriver - never reads those documents' events.  The
  // top document collects them itself: it descends every open shadow root and
  // every SAME-ORIGIN iframe document (cross-origin contentDocuments are
  // unreadable and stay unsupported), splicing their buffers into ONE list.
  // Collector runs only for frames that are UNREACHABLE to WebDriver - those
  // inside a shadow root, plus everything below such a frame - so light-DOM
  // frames (drained normally by Python) are never collected twice.
  window.__wvpDrainShadow = function () {
    var out = [];
    var seen = [];
    var MAX_DEPTH = 8;

    function collectBuffer(win) {
      if (!win) return;
      var buf = win.__webversionpw_events;
      if (!buf || !buf.length) return;
      var evts = buf.splice(0);
      for (var k = 0; k < evts.length; k++) {
        var e = evts[k];
        if (!e) continue;
        if (!e.context) e.context = {};
        // Marked so replay routes the event through the in-browser dispatch
        // (WebDriver cannot switch into this frame) instead of frame entry.
        e.context.wvp_shadow_frame = true;
        out.push(e);
      }
    }

    function collect(root, depth, unreachable) {
      if (!root || depth > MAX_DEPTH) return;
      if (seen.indexOf(root) >= 0) return;
      seen.push(root);
      // A ShadowRoot owns the iframes inside it; WebDriver cannot reach them.
      var rootIsShadow = !!(root.getRootNode && root.getRootNode().nodeType === 11);
      var iframes;
      try { iframes = root.querySelectorAll('iframe'); } catch (err) { iframes = []; }
      for (var i = 0; i < iframes.length; i++) {
        var f = iframes[i];
        var cd = null;
        try { cd = f.contentDocument; } catch (err) { cd = null; }
        if (!cd) continue;  // cross-origin / not yet loaded - unreachable
        var fUnreachable = unreachable || rootIsShadow;
        if (fUnreachable) {
          var w = null;
          try { w = f.contentWindow; } catch (err) { w = null; }
          collectBuffer(w);
        }
        collect(cd, depth + 1, fUnreachable);
      }
      var all;
      try { all = root.querySelectorAll('*'); } catch (err) { all = []; }
      for (var j = 0; j < all.length; j++) {
        var h = all[j];
        if (h.shadowRoot) collect(h.shadowRoot, depth + 1, unreachable);
      }
    }

    collect(document, 0, false);
    return out;
  };

  // ---------------------------------------------------------------- overlay
  // Dev-tools-style overlay shown WHILE RECORDING (never used by replay): a
  // highlight box + caption over the element under the cursor, so the user
  // sees exactly what the next click/type will be recorded as, plus a
  // top-right semi-transparent cheat sheet of the recording modifiers.  Purely
  // visual - every node is pointer-events:none (also ignored by
  // deepElementFromPoint) so it is never itself recorded.  Removed on stop.
  var overlayBox = null;
  var overlayLabel = null;
  var overlayCheat = null;

  function overlayEl(tag, css) {
    var el = document.createElement(tag);
    try { el.setAttribute('data-wvp-overlay', '1'); } catch (e) {}
    try { el.style.cssText = css; } catch (e) {}
    return el;
  }

  // The recording overlay must live in the browser's TOP LAYER: an open
  // <dialog showModal()> popup paints ABOVE every z-index, so a plain fixed
  // overlay - even at z-index 2147483647 - stayed INVISIBLE while recording
  // over a modal.  The Popover API lifts it into that same layer.
  // Feature-detected, so a stub page (no popover support) is untouched.
  function raiseTopLayer(el) {
    try {
      if (!el || el.popover === undefined) return;
      if (!el.hasAttribute('popover')) el.setAttribute('popover', 'manual');
      if (!el.showPopover) return;
      if (el.matches && el.matches(':popover-open')) return;
      // showPopover() REFUSES an element whose computed display is none - the
      // overlay starts hidden, so the call silently failed and it stayed UNDER
      // the popup.  Make it displayable for the call; the top-layer entry
      // survives the restore.
      var was = el.style.display;
      if (was === 'none') el.style.display = 'block';
      try { el.showPopover(); } finally { el.style.display = was; }
    } catch (e) {}
  }

  // Only WHILE recording: the Python side sets this sessionStorage flag to
  // 'on' at record start (it survives SAME-origin navigation) and 'off' at
  // stop, so the overlay never lingers after recording ends.
  function overlayWanted() {
    try { return sessionStorage.getItem('looper.web.overlay') === 'on'; } catch (e) { return false; }
  }

  // The overlay lives on document.documentElement, which does NOT exist yet
  // when the recorder is injected at document start: CDP
  // Page.addScriptToEvaluateOnNewDocument runs BEFORE <html> is parsed, so an
  // append there throws.  Creation is therefore deferred until the root
  // exists and re-asserted on every page / DOM change - otherwise the overlay
  // shows on the first (fully loaded) document and never again after a
  // navigation or a framework re-render.
  function ensureOverlay() {
    if (!overlayWanted()) return;
    // TOP document only.  Teardown (`_disable_overlay_all`) reaches the top
    // document of every window, so an overlay created inside a frame could
    // never be removed and would outlive the recording for good.
    if (!isTop) return;
    var root = document.documentElement;
    if (!root) return;  // injected before <html> - a ready hook retries
    observeOverlayRoot();
    if (!overlayBox || !overlayBox.isConnected) {
      overlayBox = overlayLabel = null;
      try {
        overlayBox = overlayEl('div',
          'position:fixed;inset:auto;margin:0;z-index:2147483647;pointer-events:none;box-sizing:border-box;' +
          'border:2px solid #00e0b8;background:rgba(0,224,184,0.12);display:none;');
        overlayLabel = overlayEl('div',
          'position:fixed;inset:auto;margin:0;z-index:2147483647;pointer-events:none;' +
          'background:#00e0b8;color:#00201a;font:11px/1.4 monospace;padding:1px 4px;' +
          'border:0;border-radius:3px;display:none;white-space:nowrap;');
        root.appendChild(overlayBox);
        root.appendChild(overlayLabel);
        raiseTopLayer(overlayBox);
        raiseTopLayer(overlayLabel);
      } catch (e) { overlayBox = overlayLabel = null; return; }
    }
    if (overlayCheat && overlayCheat.isConnected) return;
    overlayCheat = null;
    try {
      overlayCheat = overlayEl('div',
        'position:fixed;inset:auto;margin:0;top:12px;right:12px;z-index:2147483647;pointer-events:none;' +
        'background:rgba(0,32,26,0.72);color:#d6fff5;font:12px/1.5 monospace;' +
        'padding:8px 12px;border-radius:6px;border:1px solid rgba(0,224,184,0.55);' +
        'box-shadow:0 2px 10px rgba(0,0,0,0.35);white-space:pre;');
      overlayCheat.textContent =
        'RECORDING\n' +
        'Right Ctrl + move   hover\n' +
        'Insert + click      repeat per item\n' +
        'ESC                 stop & save';
      root.appendChild(overlayCheat);
      raiseTopLayer(overlayCheat);
    } catch (e) { overlayCheat = null; }
  }

  // Observe html's DIRECT children only (cheap): that is where the overlay
  // lives and where a page that swaps the whole document shows up.  A deep
  // subtree observer would fire on every SPA render.
  var overlayRootObserved = false;
  function observeOverlayRoot() {
    if (overlayRootObserved || !document.documentElement) return;
    overlayRootObserved = true;
    try {
      new MutationObserver(reassertOverlay)
        .observe(document.documentElement, { childList: true });
    } catch (e) { overlayRootObserved = false; }
  }

  // Rebuild whatever the page dropped - the root element appearing late (a
  // navigation), a framework re-render, or the page wiping html's children.
  function reassertOverlay() {
    if (!overlayWanted()) return;
    if (overlayBox && overlayBox.isConnected &&
        (!isTop || (overlayCheat && overlayCheat.isConnected))) return;
    ensureOverlay();
  }

  function removeOverlay() {
    try {
      if (overlayBox && overlayBox.parentNode) overlayBox.parentNode.removeChild(overlayBox);
      if (overlayLabel && overlayLabel.parentNode) overlayLabel.parentNode.removeChild(overlayLabel);
      if (overlayCheat && overlayCheat.parentNode) overlayCheat.parentNode.removeChild(overlayCheat);
    } catch (e) {}
    overlayBox = overlayLabel = overlayCheat = null;
  }

  function overlayCaption(el) {
    var tag = (el.tagName || '').toLowerCase();
    var id = el.id ? ('#' + el.id) : '';
    var t = (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 48);
    return tag + id + (t ? '  "' + t + '"' : '');
  }

  function onOverlayMove(e) {
    if (!overlayBox || !overlayBox.isConnected) reassertOverlay();
    if (!overlayBox) return;
    var el = deepElementFromPoint(e.clientX, e.clientY);
    var isOverlay = !!(el && el.getAttribute && el.getAttribute('data-wvp-overlay'));
    if (!el || el === document.body || el === document.documentElement || isOverlay) {
      try { overlayBox.style.display = 'none'; overlayLabel.style.display = 'none'; } catch (err) {}
      return;
    }
    try {
      var r = el.getBoundingClientRect();
      overlayBox.style.display = 'block';
      overlayBox.style.left = r.left + 'px';
      overlayBox.style.top = r.top + 'px';
      overlayBox.style.width = r.width + 'px';
      overlayBox.style.height = r.height + 'px';
      if (overlayLabel) {
        overlayLabel.textContent = overlayCaption(el);
        overlayLabel.style.display = 'block';
        var ly = r.top - 18;
        if (ly < 0) ly = r.bottom + 2;
        overlayLabel.style.left = Math.max(0, r.left) + 'px';
        overlayLabel.style.top = ly + 'px';
      }
    } catch (err) {}
  }

  // Manual hooks so the Python side can (re)create or remove the overlay even
  // when a RE-RECORD does not re-run this payload: the recorder is already
  // injected on the page (fingerprint up to date), so the init-time
  // ensureOverlay() below runs only once per document.
  window.__wvpOverlayOn = function () {
    try { sessionStorage.setItem('looper.web.overlay', 'on'); } catch (e) {}
    try { reassertOverlay(); } catch (e) {}
  };
  window.__wvpOverlayOff = function () {
    try { removeOverlay(); } catch (e) {}
    try { sessionStorage.setItem('looper.web.overlay', 'off'); } catch (e) {}
  };

  ensureOverlay();
  // Retry once <html> exists (navigations inject BEFORE it) and re-assert on
  // every page / DOM change - a framework re-render or the page wiping html's
  // children must not permanently lose the overlay.
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', reassertOverlay);
  }
  document.addEventListener('readystatechange', reassertOverlay);
  window.addEventListener('load', reassertOverlay);
  window.addEventListener('pageshow', reassertOverlay);
  try {
    new MutationObserver(reassertOverlay).observe(document, { childList: true });
  } catch (e) {}
  window.addEventListener('pointermove', onOverlayMove, true);

  window.__wvpFlush = function () {
    settleAndPersist();
    removeOverlay();
    // Clearing the gate IS part of the teardown: onOverlayMove (pointermove,
    // capture) re-creates the overlay whenever the gate is 'on', so a flush
    // that only removed the nodes would let the next mouse move bring them
    // back.
    try { sessionStorage.setItem('looper.web.overlay', 'off'); } catch (e) {}
    return window.__webversionpw_events ? window.__webversionpw_events.splice(0) : [];
  };

  // Initial context marker so the Python side can sanity-check the bridge
  // (goes through emit() so url/title are stamped).  No navigate event is
  // emitted: replay is interaction-driven and runs on the browser's current
  // page - the Python side drops interactions recorded on Chrome-internal
  // pages, and the browser is left open so the next recording/run continues
  // where the chain left off.
  if (isTop) {
    emit(baseFields('bridge_ready', nowTs(), null));
  }
})();
