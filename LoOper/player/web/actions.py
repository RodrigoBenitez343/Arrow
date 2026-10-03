from __future__ import annotations

import logging
import re
import time

logger = logging.getLogger(__name__)
"""Shared primitives for the web replay engine + the do_* compat facade.

The per-action interpreters live in player/web/handlers (one class per action
type, each running a deterministic method ladder and reporting which method
worked); this module keeps the low-level building blocks they all share:
locator-chain resolution (with iframe switching and shadow-root fallbacks),
the in-browser JS dispatch snippets, the trusted-native key/chord tables, and
the release guard.  The do_* names at the bottom are the historic function
API, re-exported from the handler registry so engine/tests imports keep
working unchanged.

Known limitation (by design): JS-dispatched events have isTrusted=false, so
sites that check event trust ignore them - that is why native is the default
and the JS path is only the fallback.
"""

from typing import Any, Optional

from .events import Event, Locator, is_volatile_id

# Selenium imports are guarded so the package still imports when the web
# dependencies are missing; the driver factory (web/session.py) raises the
# authoritative error before any dispatcher can run.
try:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    _SELENIUM_AVAILABLE = True
except Exception:  # pragma: no cover - dependency guard
    By = ActionChains = Keys = EC = WebDriverWait = None
    _SELENIUM_AVAILABLE = False

# Canonical recorded modifiers -> Selenium Keys for the native chord path.
MOD_KEYS = {
    "Ctrl": Keys.CONTROL,
    "Control": Keys.CONTROL,  # browser e.key name for a lone Ctrl press
    "Alt": Keys.ALT,
    "Shift": Keys.SHIFT,
    "Meta": Keys.META,
}

# Browser-chrome chords: these drive the browser UI (back/forward, refresh,
# tabs, omnibox), which untrusted synthetic JS events can never trigger - so
# they are replayed through the native ActionChains path even when the
# native_actions toggle is off.
REPLAY_CHROME_CHORDS: set[tuple[str, frozenset[str]]] = {
    ("ArrowLeft", frozenset({"Alt"})),
    ("ArrowRight", frozenset({"Alt"})),
    ("F5", frozenset()),
    ("r", frozenset({"Ctrl"})),
    ("t", frozenset({"Ctrl"})),
    ("w", frozenset({"Ctrl"})),
    ("T", frozenset({"Ctrl", "Shift"})),
    ("n", frozenset({"Ctrl"})),
    ("l", frozenset({"Ctrl"})),
    ("h", frozenset({"Ctrl"})),
    ("Tab", frozenset({"Ctrl"})),
    ("Tab", frozenset({"Ctrl", "Shift"})),
    ("0", frozenset({"Ctrl"})),
    ("1", frozenset({"Ctrl"})),
    ("2", frozenset({"Ctrl"})),
    ("3", frozenset({"Ctrl"})),
    ("4", frozenset({"Ctrl"})),
    ("5", frozenset({"Ctrl"})),
    ("6", frozenset({"Ctrl"})),
    ("7", frozenset({"Ctrl"})),
    ("8", frozenset({"Ctrl"})),
    ("9", frozenset({"Ctrl"})),
    ("+", frozenset({"Ctrl"})),
    ("=", frozenset({"Ctrl"})),
    ("-", frozenset({"Ctrl"})),
}


# Keys whose browser DEFAULT action (editing, caret movement, scrolling,
# selection, modifier hold) only fires for trusted input - untrusted synthetic
# events never perform default actions, so these replay natively like the
# desktop player does.  Modifier keys are included so a lone Shift/Ctrl press
# actually registers.
NATIVE_KEYS = frozenset({
    "Backspace", "Delete", "Enter", "Tab", "Escape",
    "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
    "Home", "End", "PageUp", "PageDown", "Insert",
    " ", "Space",
    "Shift", "Control", "Alt", "Meta",
})

# Modifier combos over these letters are browser defaults (select-all, copy,
# cut, paste, undo/redo) that synthetic events never trigger.
MODIFIER_DEFAULT_LETTERS = frozenset("acvxzy")

# Chrome-affecting chords -> deterministic do_chrome actions.  Chromedriver
# synthesizes input at the renderer level, so raw key chords may never reach
# browser chrome (tabs, back/forward, omnibox) - route them semantically with
# the existing tab-count guards instead (Ctrl+W must never kill the last tab).
_SEMANTIC_CHROME: dict[tuple[str, frozenset[str]], tuple[str, Optional[int]]] = {
    ("ArrowLeft", frozenset({"Alt"})): ("back", None),
    ("ArrowRight", frozenset({"Alt"})): ("forward", None),
    ("F5", frozenset()): ("reload", None),
    ("r", frozenset({"Ctrl"})): ("reload", None),
    ("t", frozenset({"Ctrl"})): ("new_tab", None),
    ("w", frozenset({"Ctrl"})): ("close_tab", None),
    ("n", frozenset({"Ctrl"})): ("new_tab", None),
    ("l", frozenset({"Ctrl"})): ("omnibox_focus", None),
    ("Tab", frozenset({"Ctrl"})): ("next_tab", None),
    ("Tab", frozenset({"Ctrl", "Shift"})): ("prev_tab", None),
    ("1", frozenset({"Ctrl"})): ("switch_tab", 0),
    ("2", frozenset({"Ctrl"})): ("switch_tab", 1),
    ("3", frozenset({"Ctrl"})): ("switch_tab", 2),
    ("4", frozenset({"Ctrl"})): ("switch_tab", 3),
    ("5", frozenset({"Ctrl"})): ("switch_tab", 4),
    ("6", frozenset({"Ctrl"})): ("switch_tab", 5),
    ("7", frozenset({"Ctrl"})): ("switch_tab", 6),
    ("8", frozenset({"Ctrl"})): ("switch_tab", 7),
    ("9", frozenset({"Ctrl"})): ("switch_tab", -1),  # Ctrl+9 = last tab
}


def _semantic_chrome_action(event: Event) -> Optional[tuple[str, Optional[int]]]:
    """Deterministic do_chrome action for a chrome-affecting chord, if any."""
    key = event.key or ""
    mods = frozenset(getattr(event, "modifiers", None) or [])
    return _SEMANTIC_CHROME.get((key, mods))


def _is_replay_chrome_chord(event: Event) -> bool:
    """True when the key event drives browser chrome and needs native replay."""
    return (event.key, frozenset(event.modifiers or [])) in REPLAY_CHROME_CHORDS

JS_SCROLL_INTO_VIEW = (
    "arguments[0].scrollIntoView({block:'center', inline:'center'});"
    "if (arguments[0].focus) arguments[0].focus({preventScroll:true});"
    "return true;"
)

JS_CLICK_SEQUENCE = """
(function (el, button) {
  var b = typeof button === 'string' && button !== '' ? parseInt(button, 10) : 0;
  var init = {
    bubbles: true, cancelable: true, composed: true, view: window,
    button: b, buttons: b === 2 ? 2 : 1
  };
  var types = ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'];
  for (var i = 0; i < types.length; i++) {
    var ctor = types[i].indexOf('pointer') === 0 ? PointerEvent : MouseEvent;
    try { el.dispatchEvent(new ctor(types[i], init)); } catch (err) {}
  }
  return true;
})(arguments[0], arguments[1]);
"""

# NOTE: `return (` MUST stay on one line with the IIFE - a newline after
# `return` triggers ASI (a bare `return;`) so the IIFE runs as a discarded
# expression and the value comes back as None.

# Shared shadow-piercing deep search, prepended to the in-browser dispatch
# snippets below (each is a self-contained script string).  WebDriver cannot
# see elements inside shadow roots - nor frames nested inside them - so the
# search walks every open shadow root and every SAME-ORIGIN iframe document.
# ``__wvpDeepFindAny`` tries EVERY recorded CSS candidate (strongest first) so
# a volatile id never hides a stable class / aria-label / text fallback, and
# ``__wvpDeepFindText`` is the text-match last resort.
JS_DEEP_SEARCH = """
// ROOT: the document the shadow-piercing search starts from.  A chain shares
// ONE browser and WebDriver keeps its frame context between commands, so an
// action can be left executing inside a frame a previous node entered - while
// the element it must act on is whatever is RENDERED now (the top document
// composites every frame).  Climb to the top-most SAME-ORIGIN document so the
// action guides itself through the whole frame tree instead of being trapped
// in a stale frame; falls back to ``document`` for a cross-origin ancestor or
// a runner that binds ``document`` without a ``window.parent``.
function __wvpRootDoc() {
  try {
    var w = (typeof window !== 'undefined' && window) ? window : null;
    var guard = 0, top = null;
    while (w && guard++ < 12) {
      top = w;
      var p = null;
      try { p = w.parent; } catch (e) { break; }
      if (!p || p === w) break;
      var pd = null;
      try { pd = p.document; } catch (e) { break; }
      if (!pd) break;
      w = p;
    }
    if (top && top.document) return top.document;
  } catch (e) {}
  return document;
}
function __wvpDeepFind(sel, want) {
  var out = null;
  function collect(root, depth) {
    if (out || depth > 12 || !root || !root.querySelectorAll) return;
    var r = null;
    try { r = root.querySelectorAll(sel); } catch (e) { return; }
    if (r.length) { out = r[(want || 0) % r.length]; return; }
    var all = root.querySelectorAll('*');
    for (var j = 0; j < all.length; j++) {
      if (out) return;
      var h = all[j];
      if (h.shadowRoot) collect(h.shadowRoot, depth + 1);
      else if (h.contentDocument) collect(h.contentDocument, depth + 1);
    }
  }
  collect(__wvpRootDoc(), 0);
  return out;
}
function __wvpDeepFindAny(selectors, want) {
  for (var i = 0; selectors && i < selectors.length; i++) {
    var el = __wvpDeepFind(selectors[i], want);
    if (el) return el;
  }
  return null;
}
function __wvpDeepFindText(needle) {
  if (!needle) return null;
  needle = String(needle).replace(/\\s+/g, ' ').trim();
  if (!needle) return null;
  var out = null;
  function scan(root, depth) {
    if (out || depth > 12 || !root || !root.querySelectorAll) return;
    var all = root.querySelectorAll('*');
    for (var j = 0; j < all.length; j++) {
      if (out) return;
      var e = all[j];
      var t = (e.textContent || '').replace(/\\s+/g, ' ').trim();
      if (t === needle) { out = e; return; }
      if (e.shadowRoot) scan(e.shadowRoot, depth + 1);
      else if (e.contentDocument) scan(e.contentDocument, depth + 1);
    }
  }
  scan(__wvpRootDoc(), 0);
  return out;
}
function __wvpDeepFindAll(selectors) {
  var out = [];
  for (var i = 0; selectors && i < selectors.length; i++) {
    (function (sel) {
      (function collect(root, depth) {
        if (depth > 12 || !root || !root.querySelectorAll) return;
        var r = null;
        try { r = root.querySelectorAll(sel); } catch (e) { return; }
        for (var k = 0; k < r.length; k++) out.push(r[k]);
        var all = root.querySelectorAll('*');
        for (var j = 0; j < all.length; j++) {
          var h = all[j];
          if (h.shadowRoot) collect(h.shadowRoot, depth + 1);
          else if (h.contentDocument) collect(h.contentDocument, depth + 1);
        }
      })(__wvpRootDoc(), 0);
    })(selectors[i]);
  }
  return out;
}
function __wvpDeepTextAll(needle) {
  if (!needle) return [];
  var want = String(needle).replace(/\s+/g, ' ').trim();
  if (!want) return [];
  var out = [];
  (function scan(root, depth) {
    if (depth > 12 || !root || !root.querySelectorAll) return;
    var all;
    try { all = root.querySelectorAll('*'); } catch (e) { return; }
    for (var j = 0; j < all.length; j++) {
      var e = all[j];
      if ((e.textContent || '').replace(/\s+/g, ' ').trim() === want) out.push(e);
      if (e.shadowRoot) scan(e.shadowRoot, depth + 1);
      else if (e.contentDocument) scan(e.contentDocument, depth + 1);
    }
  })(__wvpRootDoc(), 0);
  return out;
}
function __wvpCssEsc(s) {
  try { return (window.CSS && CSS.escape) ? CSS.escape(String(s)) : String(s); }
  catch (e) { return String(s); }
}
function __wvpDeepFindById(id) {
  if (!id) return null;
  var out = null;
  function collect(root, depth) {
    if (out || depth > 12 || !root || !root.querySelectorAll) return;
    var r = null;
    try { r = root.querySelector('#' + __wvpCssEsc(id)); } catch (e) { r = null; }
    if (r) { out = r; return; }
    var all = root.querySelectorAll('*');
    for (var j = 0; j < all.length; j++) {
      if (out) return;
      var h = all[j];
      if (h.shadowRoot) collect(h.shadowRoot, depth + 1);
      else if (h.contentDocument) collect(h.contentDocument, depth + 1);
    }
  }
  collect(__wvpRootDoc(), 0);
  return out;
}
/**
 * Resolve a form control by its visible LABEL - the field's portable identity.
 *
 * A site's generated id/name carries per-instance tokens (LinkedIn's Easy Apply
 * ids embed the job id), which rotate when the form is a different instance;
 * the label the user reads does not.  Labels live in the control's OWN root (a
 * form rendered into a shadow root keeps its label there), so this walks every
 * open shadow root and same-origin frame rather than the light DOM only.
 *
 * Returns null unless EXACTLY one control claims the label: an ambiguous label
 * must never pick a lookalike, it must let the recorded selectors decide.
 */
function __wvpDeepFindLabel(needle) {
  if (!needle) return null;
  var want = String(needle).replace(/\s+/g, ' ').trim().toLowerCase();
  if (!want) return null;
  var found = [];
  function controlOf(label, root) {
    var el = null;
    var f = (label.htmlFor || label.getAttribute('for') || '').trim();
    if (f) {
      try { el = root.querySelector('#' + __wvpCssEsc(f)); } catch (e) { el = null; }
      if (!el) el = __wvpDeepFindById(f);
    }
    if (!el && label.querySelector) {
      el = label.querySelector(
        'input, textarea, select, [contenteditable=""], [contenteditable="true"]');
    }
    return el || null;
  }
  function scan(root, depth) {
    if (found.length > 1 || depth > 12 || !root || !root.querySelectorAll) return;
    var labels = null;
    try { labels = root.querySelectorAll('label'); } catch (e) { labels = null; }
    if (labels) {
      for (var i = 0; i < labels.length; i++) {
        var t = (labels[i].textContent || '').replace(/\s+/g, ' ').trim().toLowerCase();
        if (t !== want) continue;
        var c = controlOf(labels[i], root);
        if (c && found.indexOf(c) < 0) found.push(c);
      }
    }
    var all = null;
    try { all = root.querySelectorAll('*'); } catch (e) { return; }
    for (var j = 0; j < all.length; j++) {
      if (found.length > 1) return;
      var h = all[j];
      if (h.shadowRoot) scan(h.shadowRoot, depth + 1);
      else if (h.contentDocument) scan(h.contentDocument, depth + 1);
    }
  }
  scan(__wvpRootDoc(), 0);
  return found.length === 1 ? found[0] : null;
}
"""

# Foreground gate for FORM FILLING only.  Deliberately NOT part of
# JS_DEEP_SEARCH: the pointer/typing snippets are prepended with that string and
# must stay coordinate-free (a click is element-based, never elementFromPoint),
# and every other web snippet would otherwise carry three unused helpers.
# Prepended to the form-fill snippets only: __wvpDeepAll / __wvpVisible /
# __wvpTopmost.
JS_FOREGROUND = """
function __wvpDeepAll(sel) {
  var out = [];
  (function collect(root, depth) {
    if (depth > 12 || !root || !root.querySelectorAll) return;
    var r = null;
    try { r = root.querySelectorAll(sel); } catch (e) { return; }
    for (var i = 0; i < r.length; i++) out.push(r[i]);
    var all = root.querySelectorAll('*');
    for (var j = 0; j < all.length; j++) {
      var h = all[j];
      if (h.shadowRoot) collect(h.shadowRoot, depth + 1);
      else if (h.contentDocument) collect(h.contentDocument, depth + 1);
    }
  })(__wvpRootDoc(), 0);
  return out;
}
/**
 * VISIBLE: not display:none / visibility:hidden, not under a hidden /
 * aria-hidden / inert ancestor (crossing shadow hosts), non-empty box.
 */
function __wvpVisible(el) {
  if (!el || el.nodeType !== 1) return false;
  var view = (el.ownerDocument && el.ownerDocument.defaultView)
             || (el.getRootNode && el.getRootNode().defaultView) || window;
  try {
    var st = view.getComputedStyle(el);
    if (st && (st.display === 'none' || st.visibility === 'hidden'
               || st.visibility === 'collapse')) return false;
  } catch (e) {}
  var n = el, guard = 0;
  while (n && n.nodeType === 1 && guard++ < 60) {
    if (n.hidden === true) return false;
    try {
      if (n.getAttribute && n.getAttribute('aria-hidden') === 'true') return false;
      if (n.hasAttribute && n.hasAttribute('inert')) return false;
    } catch (e) {}
    n = n.parentElement || (n.getRootNode && n.getRootNode().host) || null;
  }
  var r = null;
  try { r = el.getBoundingClientRect(); } catch (e) {}
  return !!(r && r.width > 0 && r.height > 0);
}
/**
 * ON TOP: the element (or its own <label>) is what a click at its centre
 * hits - a field covered by a modal / overlay, or a background frame's twin,
 * is never filled or clicked.  Form filling is foreground-only.
 */
function __wvpTopmost(el) {
  try {
    var doc = el.ownerDocument || document;
    var view = doc.defaultView || window;
    var r = el.getBoundingClientRect();
    if (!r || r.width <= 0 || r.height <= 0) return false;
    var cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    if (cx < 0 || cy < 0 || cx > view.innerWidth || cy > view.innerHeight) {
      // Scrolled out of view: cannot hit-test, and being off-screen is NOT
      // "covered" - rejecting it here dropped every field below the fold.
      return true;
    }
    var top = doc.elementFromPoint(cx, cy);
    var guard = 0;
    while (top && top.shadowRoot && guard++ < 10) {
      var inner = top.shadowRoot.elementFromPoint(cx, cy);
      if (!inner || inner === top) break;
      top = inner;
    }
    if (!top) return true;
    // Ancestor test that CROSSES shadow boundaries: ``Node.contains`` never
    // does, so a shadow HOST was judged "covered" by its own shadow content -
    // the hit element is a shadow DESCENDANT of the host, not a light-DOM
    // child - and the whole shadow subtree (every field of a LinkedIn-style
    // form) was silently skipped.  Walking up via ``getRootNode().host``
    // relates them correctly while a genuinely covering overlay still does not.
    function owns(node, target) {
      var n = node, g = 0;
      while (n && g++ < 60) {
        if (n === target) return true;
        n = n.parentElement || (n.getRootNode && n.getRootNode().host) || null;
      }
      return false;
    }
    if (owns(top, el) || owns(el, top)) return true;
    // A control styled with pointer-events:none is driven by its <label>.
    if ((top.tagName || '').toLowerCase() === 'label'
        && (top.control === el || (el.id && top.htmlFor === el.id))) return true;
    return false;
  } catch (e) {
    return true;
  }
}
/**
 * ENTERABLE: a shadow host / frame is worth descending into when it is not
 * HIDDEN - its OWN box is deliberately NOT required.
 *
 * These outlets are commonly absolutely-positioned wrappers with a ZERO-height
 * box whose shadow content overflows it (LinkedIn's ``interop-outlet`` is
 * 1360x0, position:absolute), so a box test rejected the host and every field
 * inside it was invisible to the enumerator (the Easy Apply form reported
 * 0 fields).  The fields themselves still pass the per-field visible / on-top
 * gates, so a covered or hidden subtree is filtered at the control level.
 */
function __wvpHostEnterable(el) {
  if (!el || el.nodeType !== 1) return false;
  try {
    var view = (el.ownerDocument && el.ownerDocument.defaultView)
               || (el.getRootNode && el.getRootNode().defaultView) || window;
    var st = view.getComputedStyle(el);
    if (st && (st.display === 'none' || st.visibility === 'hidden'
               || st.visibility === 'collapse')) return false;
  } catch (e) {}
  var n = el, guard = 0;
  while (n && n.nodeType === 1 && guard++ < 60) {
    if (n.hidden === true) return false;
    try {
      if (n.getAttribute && n.getAttribute('aria-hidden') === 'true') return false;
      if (n.hasAttribute && n.hasAttribute('inert')) return false;
    } catch (e) {}
    n = n.parentElement || (n.getRootNode && n.getRootNode().host) || null;
  }
  return true;
}
/**
 * CONTAINS across shadow boundaries: ``Node.contains`` stops at a shadow
 * boundary, so a field inside a portal host would look "outside" its own modal.
 */
function __wvpOwns(node, target) {
  var n = node, guard = 0;
  while (n && guard++ < 80) {
    if (n === target) return true;
    n = n.parentElement || (n.getRootNode && n.getRootNode().host) || null;
  }
  return false;
}
/**
 * The human LABEL of a control - ONE resolver shared by the enumerator, the
 * write and the read-back, so all three agree on what an option is CALLED.
 *
 * SPAs (LinkedIn et al.) render a choice option as a hidden <input> plus an
 * EMPTY <label for> (the radio circle) and put the OPTION TEXT in a SIBLING
 * block.  Reading only `label[for]` / `closest('label')` yields '' for such a
 * control, `matches('')` is always false, and the write reported 'target
 * unresolved or not editable' while the model had answered CORRECTLY (verified
 * live: the enumerator read the full option text, the write read an empty
 * string).  A plain `label[for]` lookup returning '' is also why the enumerator
 * probes SPAs by their DOM id - so the SPA scan below is the missing half.
 */
function __wvpFieldLabel(el) {
  function clean(x) {
    try { return (x.innerText || x.textContent || '').replace(/\\s+/g, ' ').trim(); }
    catch (e) { return ''; }
  }
  // A character COUNTER or a VALIDATION message is not a question.  The sibling
  // walk below reaches any nearby block, so it adopted the page's own "0/20 0 of
  // 20 characters" and later "Invalid input 133/20 133 of 20 characters" as the
  // field's LABEL: the model was then asked a nonsense question, and the label
  // changed between passes so the field could never be recognised again.
  // NOTE: these are DOUBLE-escaped (\\b) on purpose - this JS lives in a
  // NON-raw Python string, where a single \\b is Python's BACKSPACE escape, so
  // the regex reached the browser with control bytes instead of word
  // boundaries and matched nothing: the counter/validation text was then
  // adopted as the field's LABEL (live: "Invalid input 418/20 418 of 20
  // characters" became the question, so the repair re-asked nonsense and the
  // field stayed rejected).  \\s / \\d survive as written; \\b does NOT.
  function isNoise(t) {
    if (!t) return false;
    return /^\s*\d+\s*\/\s*\d+/.test(t)              // "0/20", "133/20"
        || /\\bcharacters?\\b/i.test(t)                 // "...of 20 characters"
        || /^(invalid|please|required|this field|too (short|long))\\b/i.test(t)
        || /\\binvalid (input|value|format)\\b/i.test(t);
  }
  try {
    var lb = el.getAttribute('aria-labelledby');
    if (lb) {
      var parts = [], ids = lb.split(/\\s+/);
      for (var i = 0; i < ids.length; i++) {
        var ref = ids[i] ? document.getElementById(ids[i]) : null;
        if (ref) { var rt = clean(ref); if (rt) parts.push(rt); }
      }
      if (parts.length) return parts.join(' ');
    }
  } catch (e) {}
  try {
    if (el.id) {
      var l = document.querySelector('label[for="' + __wvpCssEsc(el.id) + '"]');
      if (l) { var t = clean(l); if (t) return t; }
    }
  } catch (e) {}
  try {
    var p = el.closest ? el.closest('label') : null;
    if (p) { var t2 = clean(p); if (t2) return t2; }
  } catch (e) {}
  try {
    var node = el;
    for (var d = 0; d < 6 && node && node.parentElement; d++) {
      node = node.parentElement;
      var cand = node.querySelector(
        ':scope > label, :scope > legend, :scope > [class*="label" i], ' +
        ':scope > div > [class*="label" i], :scope > [data-test*="label" i]');
      if (cand) {
        var ct = clean(cand);
        if (ct && ct.length <= 200 && !isNoise(ct)) return ct;
      }
      var kids = node.children;
      for (var k = 0; k < kids.length; k++) {
        var kid = kids[k];
        if (kid === el || kid.contains(el)) continue;
        var kt = clean(kid);
        if (kt && kt.length >= 2 && kt.length <= 120
            && !isNoise(kt) && !/^(required|\\*|optional)$/i.test(kt)) return kt;
      }
    }
  } catch (e) {}
  return (el.getAttribute('aria-label') || el.getAttribute('placeholder')
          || el.getAttribute('name') || '').trim();
}
/**
 * The <label> that DRIVES a control (its own wrapper, else the one that
 * declares it with for=).
 *
 * A choice option is routinely a REAL <input> hidden behind a styled label
 * (LinkedIn hides the radio and shows its circle), so the input can never
 * pass a visibility gate while its label always can.
 */
function __wvpLabelOf(el) {
  try {
    if (el.id) {
      var l = document.querySelector('label[for="' + __wvpCssEsc(el.id) + '"]');
      if (l) return l;
    }
    if (el.closest) return el.closest('label');
  } catch (e) {}
  return null;
}
/**
 * What a user click on this control actually HITS: the control itself, else
 * its label when THAT is the visible / on-top thing.  null = not actionable.
 *
 * The WRITE, the READ-BACK and the enumeration all need this ONE predicate: a
 * hidden radio's only actionable target is its label, so a read-back that
 * required the raw input to be visible collected NO control and returned an
 * empty value for a selection that had really landed (verified live).
 */
function __wvpClickTarget(el) {
  if (__wvpVisible(el) && __wvpTopmost(el)) return el;
  var lb = __wvpLabelOf(el);
  if (lb && lb !== el && __wvpVisible(lb) && __wvpTopmost(lb)) return lb;
  return null;
}
/**
 * The OPTION's OWN text for a radio/checkbox control - the ONE resolver the
 * enumerator OFFERS, the write MATCHES and the read-back REPORTS, so the three
 * can never disagree about what an option is called.
 *
 * ``__wvpFieldLabel`` walks UP to any nearby block, so when the option's text
 * is a bare TEXT NODE beside the control (no element of its own) it skips it
 * and adopts the group's QUESTION: every option then reads as the question and
 * the write can never match the answer (verified live: two of four
 * questionnaire groups returned the question for ALL three options).  The
 * option text therefore comes from the control's OWN label / aria-label /
 * adjacent text / value - never an ancestor's question block.
 */
function __wvpOptionLabel(el) {
  function clean(x) {
    try { return String(x == null ? '' : x).replace(/\\s+/g, ' ').trim(); }
    catch (e) { return ''; }
  }
  function ownText(node) {
    if (!node) return '';
    if (node.nodeType === 3) return clean(node.nodeValue);
    if (node.nodeType !== 1) return '';
    if (node.contains && node.contains(el)) return '';
    if (node.querySelector && node.querySelector('input,select,textarea')) {
      return '';
    }
    return clean(node.innerText || node.textContent);
  }
  try {
    var al = clean(el.getAttribute('aria-label'));
    if (al) return al;
  } catch (e) {}
  try {
    if (el.id) {
      var lb = document.querySelector('label[for="' + __wvpCssEsc(el.id) + '"]');
      var lt = ownText(lb);
      if (lt) return lt;
    }
  } catch (e) {}
  try {
    var wrap = el.closest ? el.closest('label') : null;
    // This control's OWN wrapping label - never a label that wraps the whole
    // group (its text is the question).
    if (wrap && wrap.querySelectorAll('input,select,textarea').length <= 1) {
      var wt = clean(wrap.textContent);
      if (wt) return wt;
    }
  } catch (e) {}
  // The option's OWN text block: walk up to the NEAREST ancestor holding a
  // non-control sibling - the whole option, which on a real form is a full
  // SENTENCE (LinkedIn renders `<div><div><input><label/></div>
  // <div><p>the option</p></div></div>`, the label empty).  The acceptance is
  // deliberately generous (400 chars): the generic resolver caps sibling text
  // at 120, so a long option was REJECTED there and the walk climbed to the
  // question instead - the whole list then read back as the question.
  try {
    var node = el;
    for (var d = 0; d < 3 && node && node.parentElement; d++) {
      node = node.parentElement;
      var cs = node.childNodes;
      for (var k = 0; k < cs.length; k++) {
        var c = cs[k];
        if (c === el) continue;
        if (c.nodeType === 1 && c.contains(el)) continue;
        // Another option's block (it holds its own control) is not this
        // option's text.
        if (c.nodeType === 1 && c.querySelector
            && c.querySelector('input,select,textarea')) continue;
        var t = (c.nodeType === 3)
          ? clean(c.nodeValue)
          : clean(c.innerText || c.textContent);
        if (t && t.length >= 2 && t.length <= 400
            && !/^(required|\*|optional)$/i.test(t)) return t;
      }
    }
  } catch (e) {}
  return clean(el.getAttribute('value'));
}
/**
 * The page's current TOP LAYER (an open popup's root), or null.
 *
 * An UNSCOPED scan must be confined to an open popup: the page BEHIND it is
 * not "covered" everywhere (a header search box stays visible next to a modal),
 * so the per-field on-top gate alone let BASE-page fields into the fill - the
 * model answered them and the write landed on the document UNDER the popup
 * (log: `Search` filled while the Easy Apply modal was open).  Two signals:
 * the standard modal marker (``aria-modal``), else the element at the viewport
 * CENTRE climbed to its highest non-root ancestor.  null = no popup, so the
 * scan is unchanged.
 */
function __wvpTopLayers() {
  var doc = __wvpRootDoc();
  var out = [];
  function add(el) {
    if (el && out.indexOf(el) < 0 && el !== doc.body && el !== doc.documentElement) {
      out.push(el);
    }
  }
  // 1. The container of what is RENDERED at the viewport centre: climb while
  //    the ancestor does not span the whole viewport (the page wrapper does,
  //    the popup does not).  This is the element holding the popup's own
  //    controls - LinkedIn's Easy Apply <dialog> holds only its chrome (header
  //    / backdrop) while the form fields sit in a SIBLING outlet, so the dialog
  //    element itself is not a usable layer boundary (verified live).
  var vw = window.innerWidth || 0, vh = window.innerHeight || 0;
  var el = null;
  try { el = doc.elementFromPoint(vw / 2, vh / 2); } catch (e) { el = null; }
  var guard = 0;
  while (el && guard++ < 80) {
    var up = el.parentElement || (el.getRootNode && el.getRootNode().host) || null;
    if (!up || up === doc.body || up === doc.documentElement) break;
    try {
      var r = up.getBoundingClientRect();
      if (r && r.width >= vw - 4 && r.height >= vh - 4) break;   // page wrapper
    } catch (e) {}
    el = up;
  }
  add(el);
  // 2-4. Explicit popup markers, most specific first.
  function lastVisible(sel) {
    var found = null;
    try {
      var ms = __wvpDeepFindAll([sel]);
      for (var i = 0; i < ms.length; i++) {
        if (__wvpVisible(ms[i])) found = ms[i];   // a portal appends its popup last
      }
    } catch (e) {}
    return found;
  }
  add(lastVisible('[aria-modal="true"]'));
  add(lastVisible('dialog[open]'));
  add(lastVisible('[role="dialog"]'));
  return out;
}
"""

# SETTLE GATE: a page that is still RENDERING must not be acted on.  After a
# "next" click a wizard swaps the modal's body for a spinner and re-renders;
# an enumeration that runs a moment later sees the popup with NO fields yet,
# and the scan then fell back to the document UNDER it (the global 'Search' box
# beside the modal was focused/typed).  The form filler arms a MutationObserver
# ONCE and polls its quiet-age, so it WAITS for the page to stop mutating
# instead of acting on a half-loaded document.  Coordinate-free, no side effect.
JS_SETTLE_ARM = """return (function () {
  try {
    if (window.__wvpSettle) return true;
    window.__wvpSettle = {n: 0, ts: Date.now()};
    var bump = function () {
      window.__wvpSettle.n++;
      window.__wvpSettle.ts = Date.now();
    };
    var obs = new MutationObserver(bump);
    obs.observe(document, {
      childList: true, subtree: true
    });
    window.__wvpSettleObs = obs;
    return true;
  } catch (e) { return false; }
})();
"""

# Milliseconds since the last DOM mutation seen by JS_SETTLE_ARM, or -1 when
# the observer is not armed (the caller then must NOT block on it).
JS_SETTLE_READ = """return (function () {
  var s = window.__wvpSettle;
  if (!s || typeof s.ts !== 'number') return -1;
  return Date.now() - s.ts;
})();
"""


# Device-pixel viewport box of a shadow-DOM / shadow-hosted-frame target, run
# with ``JS_DEEP_SEARCH`` prepended.  WebDriver cannot return an element inside
# a shadow root, so a region-scoped capture (LLM-node web OCR) resolves the
# element in-page, scrolls it into view and reports its box scaled to DEVICE
# pixels (matching ``driver.save_screenshot``) so the screenshot can be cropped
# to it.  null when the target is not found.
#
# Resolution MUST be ambiguity-aware (mirrors ``_find_by_chain``): a generic
# class (``.ph5`` appears on every row) matching many elements must never beat a
# stable identity, and a container is re-found by its recorded TEXT before any
# ambiguous selector is trusted - otherwise the crop lands on a random
# lookalike (a "0%" progress bar) once the volatile recorded id rotates.
JS_DEEP_RECT = JS_DEEP_SEARCH + """return (function (selectors, text, tag) {
  var want = (tag || '').toLowerCase();
  function box(el) {
    if (!el || el.nodeType !== 1) return null;
    if (want && (el.tagName || '').toLowerCase() !== want) return null;
    try { el.scrollIntoView({block: 'center', inline: 'center'}); } catch (e) {}
    var r = el.getBoundingClientRect();
    if (!r || r.width <= 0 || r.height <= 0) return null;
    var dpr = window.devicePixelRatio || 1;
    return {x: r.left * dpr, y: r.top * dpr, w: r.width * dpr, h: r.height * dpr};
  }
  // Every deep match of one selector (open shadow roots + same-origin frames),
  // counted GLOBALLY so an ambiguous class is seen as ambiguous.
  function findAll(sel) {
    var out = [];
    (function collect(root, depth) {
      if (depth > 12 || !root || !root.querySelectorAll) return;
      var r = null;
      try { r = root.querySelectorAll(sel); } catch (e) { return; }
      for (var i = 0; i < r.length; i++) out.push(r[i]);
      var all = root.querySelectorAll('*');
      for (var j = 0; j < all.length; j++) {
        var h = all[j];
        if (h.shadowRoot) collect(h.shadowRoot, depth + 1);
        else if (h.contentDocument) collect(h.contentDocument, depth + 1);
      }
    })(__wvpRootDoc(), 0);
    return out;
  }
  // 1. A selector that PINPOINTS exactly one element wins.
  var weak = null;
  for (var i = 0; selectors && i < selectors.length; i++) {
    var m = findAll(selectors[i]);
    if (m.length === 1) { var b = box(m[0]); if (b) return b; }
    if (!weak && m.length) weak = m[0];
  }
  // 2. Exact visible-text match: a container is identified by the text it
  //    renders - tried before any ambiguous selector can crop the wrong region.
  if (text) { var bt = box(__wvpDeepFindText(text)); if (bt) return bt; }
  // 3. Only a record with NO text may fall back to an ambiguous selector: a
  //    text record that no longer matches means "not this element", where a
  //    full-page read beats a bogus one-element crop.
  return text ? null : box(weak);
})(arguments[0], arguments[1], arguments[2]);
"""


# Visible page text that also pierces open shadow roots and same-origin
# iframes.  ``document.body.innerText`` alone misses a form/panel rendered in a
# shadow root (e.g. LinkedIn's interop-outlet) or an embedded frame - exactly
# the text an LLM node reading "the page text" must see.  A ShadowRoot has no
# innerText, so its textContent is used: over-inclusion beats omission when the
# reader only needs to FIND the text.
#
# Executed STANDALONE (``_web_page_text`` / the orchestrator state digest), so
# it carries its own helper bundle: ``__wvpRootDoc`` lives in JS_DEEP_SEARCH
# and without the prefix the whole call died with "__wvpRootDoc is not
# defined" - the page-text source silently read as EMPTY.
JS_VISIBLE_TEXT = JS_DEEP_SEARCH + """return (function () {
  var parts = [];
  function collect(root, depth) {
    if (!root || depth > 12 || !root.querySelectorAll) return;
    var t = '';
    try { t = (root.body || root).innerText || ''; } catch (e) { t = ''; }
    if (!t) { try { t = root.textContent || ''; } catch (e) {} }
    if (t && t.trim()) parts.push(t.trim());
    var all = root.querySelectorAll('*');
    for (var j = 0; j < all.length; j++) {
      var h = all[j];
      if (h.shadowRoot) collect(h.shadowRoot, depth + 1);
      else if (h.contentDocument) collect(h.contentDocument, depth + 1);
    }
  }
  collect(__wvpRootDoc(), 0);
  return parts.join('\\n');
})();
"""


# Unified shadow-piercing pointer dispatch, ELEMENT-BASED ONLY.  The recorded
# selector is deep-searched across every shadow root (recorded elements may
# have moved into a shadow root by replay time, e.g. the Chrome new-tab search
# box hydrates into ntp-app's shadow DOM) and the events are dispatched on the
# element itself - viewport coordinates are never used, since they are only
# valid for the recording window size/scroll/zoom and would hit a DIFFERENT
# element on replay.  The element is resolved AND dispatched on inside this
# single script: WebDriver cannot serialize elements that live inside shadow
# roots (they come back as None), so the element must never cross the boundary.
JS_DOM_POINTER = JS_DEEP_SEARCH + """return (function (selectors, button, mode, mods, index, text, label) {
  // The recorded ordinal picks the SAME element the native ladder would (a
  // repeating-element cursor steers locator.index); without it the fallback
  // would always take match 0 and re-click the first item every pass.
  var want = (typeof index === 'number' && index >= 0) ? index : 0;
  function collapse(s) {
    return String(s == null ? '' : s).replace(/\\s+/g, ' ').trim();
  }
  // The recorded TEXT is the element's identity.  A volatile recorded id (an
  // Ember id that now belongs to an UNRELATED node) or a weak class candidate
  // (LinkedIn's .artdeco-button--primary matches EVERY primary button) would
  // otherwise win first-match: the click lands on the WRONG button (the "Back"
  // one when "Next" was recorded) and still reports success, so the page simply
  // never advances.  A candidate must AGREE with the record before it is used.
  var needle = text ? collapse(text).slice(0, 120) : '';
  function agrees(el) {
    if (!el) return false;
    return !needle || collapse(el.textContent).indexOf(needle) >= 0;
  }
  // Rendered (not display:none / hidden / aria-hidden / inert): a background
  // twin under a modal is not what the click must hit.
  function rendered(el) {
    try {
      if (typeof el.checkVisibility === 'function' &&
          !el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}))
        return false;
    } catch (e) {}
    var n = el, g = 0;
    while (n && n.nodeType === 1 && g++ < 60) {
      if (n.hidden === true) return false;
      try {
        if (n.getAttribute && n.getAttribute('aria-hidden') === 'true') return false;
        if (n.hasAttribute && n.hasAttribute('inert')) return false;
      } catch (e) {}
      n = n.parentElement || (n.getRootNode && n.getRootNode().host) || null;
    }
    return true;
  }
  // Intersecting the viewport: a "Next" PAGINATION far below the fold must not
  // beat the visible modal button.  Element-based only - never a coordinate hit.
  function onScreen(el) {
    var r = null;
    try { r = el.getBoundingClientRect(); } catch (e) { return false; }
    if (!r || r.width <= 0 || r.height <= 0) return false;
    var vw = window.innerWidth || 0, vh = window.innerHeight || 0;
    return r.left < vw && r.top < vh && r.right > 0 && r.bottom > 0;
  }
  // Has a real box anywhere on the page.  The last-resort branches scroll the
  // target into view first, so viewport intersection is too strict there - but
  // a ZERO-SIZE element is never a click target: dispatching on it is a silent
  // no-op that still reports success (measured 2026-10-03: a stale 'Network'
  // locator resolved to a zero-size node, native failed with "element not
  // interactable: has no size and location", the JS fallback reported OK, and
  // the page never moved while the run closed 'done').
  function hasBox(el) {
    var r = null;
    try { r = el.getBoundingClientRect(); } catch (e) { return false; }
    return !!(r && r.width > 0 && r.height > 0);
  }
  // The recorded LABEL comes first for form fields: it is the field's portable
  // identity (its id/name may embed per-instance tokens that rotate between
  // forms), and __wvpDeepFindLabel only answers when exactly ONE control claims
  // it - so a unique label can never be beaten by a volatile-selector lookalike.
  var labelEl = __wvpDeepFindLabel(label);
  var el = (labelEl && agrees(labelEl)) ? labelEl : null;
  if (!el) {
    // EVERY deep match of EVERY recorded candidate (strongest first) plus the
    // exact-text matches, keeping only those that AGREE with the record - the
    // condition probe's proven resolution, now applied to the dispatch.
    var cands = [];
    if (labelEl) cands.push(labelEl);
    var found = __wvpDeepFindAll(selectors);
    for (var i = 0; i < found.length; i++) cands.push(found[i]);
    var texts = needle ? __wvpDeepTextAll(text) : [];
    for (var j = 0; j < texts.length; j++) cands.push(texts[j]);
    var agreeing = [];
    for (var k = 0; k < cands.length; k++) {
      if (agrees(cands[k]) && agreeing.indexOf(cands[k]) < 0) agreeing.push(cands[k]);
    }
    // Prefer the most PRECISE rendered + on-screen match (shortest text that
    // still contains the recorded text): the button, not a wrapping container.
    var best = null, bestLen = -1;
    for (var bi = 0; bi < agreeing.length; bi++) {
      var cand = agreeing[bi];
      if (!rendered(cand) || !onScreen(cand)) continue;
      var candLen = collapse(cand.textContent).length;
      if (best === null || candLen < bestLen) { best = cand; bestLen = candLen; }
    }
    // Nothing on screen: the recorded ordinal among the agreeing set (a
    // repeating-element cursor).  A candidate that ENCLOSES another agreeing
    // candidate is a WRAPPER (the modal / footer div whose text merely CONTAINS
    // the record - it may even have regained the volatile recorded id), and
    // clicking it misses the button: the recorded click landed on an OUTER
    // element.  Wrappers are dropped unless everything nests.
    el = best;
    if (!el && agreeing.length) {
      var pool = [];
      for (var li = 0; li < agreeing.length; li++) {
        var wraps = false;
        for (var lj = 0; lj < agreeing.length; lj++) {
          if (lj === li) continue;
          try {
            if (agreeing[li].contains && agreeing[li].contains(agreeing[lj])) { wraps = true; break; }
          } catch (e) {}
        }
        if (!wraps) pool.push(agreeing[li]);
      }
      if (!pool.length) pool = agreeing;
      el = pool[want % pool.length];
    }
  }
  // LAST RESORT, still identity-gated.  A weak recorded candidate that no
  // longer matches the record - a generated id that now belongs to another
  // control, or a class shared by EVERY primary button - must never be
  // clicked.  That is how a chain's FINAL step (a modal's "Submit
  // application") landed on a BACKGROUND button of the page underneath while
  // the modal it belongs to was still painting, and reported success while
  // doing the wrong thing.  Report NOT FOUND instead: the caller waits briefly
  // and then fails the action, rather than clicking a stranger.
  if (!el) {
    var anyEl = __wvpDeepFindAny(selectors, want);
    if (anyEl && agrees(anyEl) && rendered(anyEl) && hasBox(anyEl)) el = anyEl;
  }
  if (!el && needle) {
    var textEl = __wvpDeepFindText(text);
    if (textEl && agrees(textEl) && rendered(textEl) && hasBox(textEl)) el = textEl;
  }
  if (!el) return false;
  // Synthetic pointer events do NOT trigger the default focus behaviour, so
  // clicks replayed through this path must focus explicitly - otherwise a
  // following type action lands on an unfocused element.
  if (mode !== 'hover') { try { el.focus({preventScroll:true}); } catch (e) {} }
  try { el.scrollIntoView({block:'center', inline:'center'}); } catch (e) {}
  var b = typeof button === 'string' && button !== '' ? parseInt(button, 10) : 0;
  var init = { bubbles: true, cancelable: true, composed: true, view: window, button: b, buttons: b === 2 ? 2 : 1 };
  var m = mods || [];
  if (m.indexOf('Ctrl') >= 0) init.ctrlKey = true;
  if (m.indexOf('Shift') >= 0) init.shiftKey = true;
  if (m.indexOf('Alt') >= 0) init.altKey = true;
  if (m.indexOf('Meta') >= 0) init.metaKey = true;
  var isHover = mode === 'hover';
  var types;
  if (isHover) {
    types = ['pointerover', 'mouseover', 'pointerenter', 'mouseenter'];
  } else if (mode === 'contextmenu') {
    types = ['pointerdown', 'mousedown', 'contextmenu', 'pointerup', 'mouseup'];
  } else if (mode === 'dblclick') {
    types = ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click',
             'pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click', 'dblclick'];
  } else {
    types = ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'];
  }
  for (var k = 0; k < types.length; k++) {
    var ctor = types[k].indexOf('pointer') === 0 ? PointerEvent : MouseEvent;
    try { el.dispatchEvent(new ctor(types[k], init)); } catch (err) {}
  }
  return true;
})(arguments[0], arguments[1], arguments[2], arguments[3], arguments[4], arguments[5],
   arguments[6]);
"""

# Repeating-element cursor, evaluated IN the page.  Enumerates the matches of
# the FIRST candidate selector that matches - mirroring ``__wvpDeepFind``'s
# first-root-with-matches traversal across open shadow roots and same-origin
# iframes, so the ordinal maps onto the SAME node the later
# ``__wvpDeepFindAny(selectors, index)`` dispatch picks - skips container
# matches that enclose another match, and returns the first match whose identity
# is not yet processed.  Used for a target WebDriver cannot reach (a shadow
# root or a shadow-hosted frame); a light-DOM target in an iframe uses the
# native frame-aware path instead.
JS_ENTITY_CURSOR = """return (function (selectors, keyAttr, processed) {
  var seen = {};
  var done = processed || [];
  for (var s0 = 0; s0 < done.length; s0++) seen[String(done[s0])] = true;
  function firstRootMatches(sel) {
    var found = null;
    (function walk(root, depth) {
      if (found || depth > 12 || !root || !root.querySelectorAll) return;
      var r = null;
      try { r = root.querySelectorAll(sel); } catch (e) { return; }
      if (r.length) { found = r; return; }
      var all = null;
      try { all = root.querySelectorAll('*'); } catch (e2) { return; }
      for (var j = 0; j < all.length; j++) {
        if (found) return;
        var h = all[j];
        if (h.shadowRoot) walk(h.shadowRoot, depth + 1);
        else if (h.contentDocument) walk(h.contentDocument, depth + 1);
      }
    })(document, 0);
    return found;
  }
  var list = selectors || [];
  for (var i = 0; i < list.length; i++) {
    var sel = list[i];
    var matches = firstRootMatches(sel);
    if (!matches || !matches.length) continue;
    // A match that ENCLOSES another match is the outer container (a class combo
    // covering every row also matches the row container) - never click it.
    var encloses = [];
    var allEnclose = true;
    for (var e = 0; e < matches.length; e++) {
      var isBox = false;
      try { isBox = matches[e].querySelectorAll(sel).length > 0; } catch (err) {}
      encloses.push(isBox);
      if (!isBox) allEnclose = false;
    }
    var skip = !allEnclose;
    for (var o = 0; o < matches.length; o++) {
      if (skip && encloses[o]) continue;
      var key = null;
      if (keyAttr) { try { key = matches[o].getAttribute(keyAttr); } catch (err2) {} }
      if (!key) key = '__ordinal_' + o;
      if (seen[String(key)]) continue;
      return { selector: sel, ordinal: o, total: matches.length, key: key };
    }
    // The strongest matching candidate is fully processed: the set is exhausted.
    return { selector: sel, total: matches.length, exhausted: true };
  }
  return null;
})(arguments[0], arguments[1], arguments[2]);
"""

JS_HOVER = """
(function (el) {
  var init = { bubbles: true, cancelable: true, composed: true, view: window };
  var types = ['pointerover', 'mouseover', 'pointerenter', 'mouseenter'];
  for (var i = 0; i < types.length; i++) {
    var ctor = types[i].indexOf('pointer') === 0 ? PointerEvent : MouseEvent;
    try { el.dispatchEvent(new ctor(types[i], init)); } catch (err) {}
  }
  return true;
})(arguments[0]);
"""

JS_TYPE = """
(function (el, text) {
  if (!el) return false;
  try { el.focus({preventScroll:true}); } catch (e) {}
  if (el.isContentEditable) {
    // Contenteditable hosts have no value setter - caret-aware insertText
    // (fires input, undoable) is the standard insertion path.
    try {
      if (document.execCommand('insertText', false, text)) {
        el.dispatchEvent(new Event('input', { bubbles: true }));
        return true;
      }
    } catch (e) {}
  }
  if (!(el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement)) {
    return false;
  }
  var proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
            : HTMLInputElement.prototype;
  var setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
  setter.call(el, (el.value || '') + text);
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
})(arguments[0], arguments[1]);
"""

# Shadow-piercing typing fallback: the recorded candidates are searched FIRST
# through every shadow root and same-origin iframe (strongest first); only a
# gone target falls back to the element the preceding click/focus focused
# (deepest active element, pierced through open shadow roots).
JS_TYPE_DOM = JS_DEEP_SEARCH + """return (function (selectors, text, matchText, label) {
  function isEditable(el) {
    if (!el || el.nodeType !== 1) return false;
    var tag = (el.tagName || '').toLowerCase();
    return tag === 'input' || tag === 'textarea' || el.isContentEditable;
  }
  function deepestActive() {
    var ae = document.activeElement;
    var guard = 0;
    while (ae && ae.shadowRoot && ae.shadowRoot.activeElement && guard++ < 8) {
      ae = ae.shadowRoot.activeElement;
    }
    return ae && ae.nodeType === 1 ? ae : null;
  }
  // The recorded LABEL comes first for form fields: it is the field's portable
  // identity (its id/name may embed per-instance tokens that rotate between
  // forms), and __wvpDeepFindLabel only answers when exactly ONE control claims
  // it.  Otherwise the recorded candidates are authoritative and are searched
  // FIRST through every shadow root and same-origin iframe (a volatile id must
  // not hide a stable class/aria/text fallback), so a shadow-DOM target is
  // typed into even when focus sits on an editable in the original document.
  // Only when the recorded target is gone do we fall back to the focused
  // editable.
  var el = __wvpDeepFindLabel(label);
  if (!el || !isEditable(el)) el = __wvpDeepFindAny(selectors, 0);
  if (!el && matchText) el = __wvpDeepFindText(matchText);
  if (el && !isEditable(el)) el = null;
  if (!el) {
    el = deepestActive();
    if (el && !isEditable(el)) el = null;
  }
  if (!el || !isEditable(el)) return false;
  try { el.scrollIntoView({block:'center', inline:'center'}); } catch (e) {}
  try { el.focus({preventScroll:true}); } catch (e) {}
  if (el.isContentEditable) {
    try {
      if (document.execCommand('insertText', false, text)) {
        el.dispatchEvent(new Event('input', { bubbles: true }));
        return true;
      }
    } catch (e) {}
  }
  if (!(el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement)) {
    return false;
  }
  var proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
            : HTMLInputElement.prototype;
  var setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
  setter.call(el, (el.value || '') + text);
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
})(arguments[0], arguments[1], arguments[2], arguments[3]);
"""

# ── DOM-grounded click (Handle node web mode): enumerate clickables ──────────
# A request text ("click the sign in button") is resolved against the LIVE
# page instead of a screenshot: every visible, labelled clickable element is
# enumerated (shadow roots and same-origin iframes pierced), MARKED with
# ``data-wvp-handle="<index>"`` and returned as a compact candidate list
# (label + tag only - never raw HTML, so the picker's window is not blown by
# markup).  The marker IS the pickup handle: the marked element resolves both
# natively (light DOM CSS attribute selector) and through the shadow-piercing
# JS dispatch, so the standard click ladder can act on the pick.  Requires
# ``arguments[0]`` = max candidates.
MARKER_ATTR = "data-wvp-handle"
JS_ENUMERATE_CLICKABLES = JS_DEEP_SEARCH + JS_FOREGROUND + """return (function (max) {
  var SEL = 'a[href], button, input[type="submit"], input[type="button"],'
    + ' input[type="image"], [role="button"], [role="link"], [role="menuitem"],'
    + ' [role="tab"], [role="option"], [role="checkbox"], [role="switch"],'
    + ' summary, [onclick]';
  function clean(x) {
    try { return (x || '').replace(/\\s+/g, ' ').trim(); } catch (e) { return ''; }
  }
  var out = [], seen = [], els = [];
  try { els = __wvpDeepAll(SEL); } catch (e) { els = []; }
  for (var i = 0; i < els.length && out.length < max; i++) {
    var el = els[i];
    if (seen.indexOf(el) >= 0) continue;
    seen.push(el);
    if (!__wvpVisible(el)) continue;
    var label = '';
    try {
      var attr = [
        el.getAttribute('aria-label'),
        (el.tagName === 'INPUT' ? (el.value || '') : ''),
        el.getAttribute('placeholder'),
        el.getAttribute('title'),
        el.getAttribute('alt')
      ];
      for (var j = 0; j < attr.length && !label; j++) label = clean(attr[j]);
    } catch (e) {}
    if (!label) label = clean(el.innerText || el.textContent || '');
    if (!label) continue;
    var idx = out.length;
    try { el.setAttribute('data-wvp-handle', String(idx)); } catch (e) { continue; }
    out.push({ i: idx, tag: (el.tagName || '').toLowerCase(),
               label: label.slice(0, 100) });
  }
  return out;
})(arguments[0]);
"""

# Remove the candidate markers once the pick was clicked (best-effort cleanup).
JS_CLEAR_HANDLE_MARKS = JS_DEEP_SEARCH + """return (function () {
  var els = [];
  try { els = __wvpDeepFindAll(['[data-wvp-handle]']); } catch (e) { els = []; }
  for (var i = 0; i < els.length; i++) {
    try { els[i].removeAttribute('data-wvp-handle'); } catch (e) {}
  }
  return els.length;
})();
"""

# ── Form Filling: the Laya-navigable element TREE ──────────────────────────
# The flat field inventory below answers "what can be filled"; this answers
# "WHERE on the page is it".  It builds a small CONTAINER -> CONTROL tree rooted
# at the PICKED container (the scope), or at the whole document when unscoped,
# piercing shadow roots and same-origin iframes - parentElement is null across
# both boundaries, so the climb continues through the host / the frameElement -
# so a control inside a popup, a shadow widget or an embedded frame is still
# reachable by the tree.
#
# Every emitted node is MARKED with ``data-wvp-ff="<index>"``: that marker is
# both the identity the Laya navigation hands back and the selector the
# write/read ladder acts on, so acting on the pick never depends on a rotated
# ``#emberNNN`` id or a hashed CSS-module class.  A branch's own label is only
# what the page states about it (aria-label / legend / heading); the caller
# summarises a label-less branch from the controls it contains.
# Requires arguments[0]=scope selectors, [1]=in_frame, [2]=max nodes.
FF_TREE_MARKER_ATTR = "data-wvp-ff"
JS_ENUMERATE_FORM_TREE = JS_DEEP_SEARCH + JS_FOREGROUND + """return (function (scopeSelectors, inFrame, maxNodes) {
  if (inFrame) { __wvpRootDoc = function () { return document; }; }
  var CONTROL_SEL = 'input, textarea, select, [contenteditable=""], '
    + '[contenteditable="true"], [role="combobox"], [role="listbox"], '
    + '[role="switch"], [role="radio"], [role="checkbox"], '
    + '[role="textbox"], [role="spinbutton"]';
  var MARK = 'data-wvp-ff';
  function clean(x) {
    try { return String(x == null ? '' : x).replace(/\\s+/g, ' ').trim(); }
    catch (e) { return ''; }
  }
  // What the PAGE says about an element of its own (never a control's question:
  // that is __wvpFieldLabel, used for the leaves).
  function ownLabel(el) {
    var t = '';
    try { t = clean(el.getAttribute('aria-label')); } catch (e) {}
    if (!t) {
      try {
        var head = el.querySelector(
          ':scope > legend, :scope > h1, :scope > h2, :scope > h3');
        t = head ? clean(head.textContent) : '';
      } catch (e) {}
    }
    return t.slice(0, 120);
  }
  function offLimits(el) {
    var tag = (el.tagName || '').toLowerCase();
    var itype = tag === 'input'
      ? (el.getAttribute('type') || 'text').toLowerCase() : tag;
    if (tag === 'input' && (itype === 'hidden' || itype === 'submit'
        || itype === 'button' || itype === 'reset' || itype === 'image'
        || itype === 'file' || itype === 'search')) return true;
    return (el.getAttribute('role') || '').toLowerCase() === 'searchbox';
  }
  // Outermost-first element chain from the scan root down to *el*, crossing
  // shadow and same-origin iframe boundaries (the climb goes on through the
  // shadow host / the frameElement, which are the only links across them).
  function chainTo(el, stopEl) {
    var chain = [], n = el;
    while (n && n !== stopEl) {
      chain.push(n);
      if (n.parentElement) { n = n.parentElement; continue; }
      var rn = n.getRootNode ? n.getRootNode() : null;
      if (rn && rn.host) { n = rn.host; continue; }
      if (rn && rn.defaultView && rn.defaultView.frameElement) {
        n = rn.defaultView.frameElement; continue;
      }
      break;
    }
    return chain.reverse();
  }
  // Stale markers from an earlier scan must never be mistaken for this one's:
  // the marker IS the node index, so a leftover would silently rewire a pick.
  try {
    var stale = __wvpDeepFindAll(['[' + MARK + ']']);
    for (var z = 0; z < stale.length; z++) {
      try { stale[z].removeAttribute(MARK); } catch (e) {}
    }
  } catch (e) {}
  var root = __wvpRootDoc(), scoped = false;
  if (scopeSelectors && scopeSelectors.length) {
    for (var s = 0; s < scopeSelectors.length && !scoped; s++) {
      var ms = [];
      try { ms = __wvpDeepAll(scopeSelectors[s]); } catch (e) { ms = []; }
      for (var m = 0; m < ms.length; m++) {
        try {
          if (__wvpVisible(ms[m]) && __wvpTopmost(ms[m])) {
            root = ms[m]; scoped = true; break;
          }
        } catch (e) {}
      }
    }
    if (!scoped) {
      // A PICKED container is WHERE the form is: no whole-page fallback (the
      // page's global 'Search' box behind a modal is not the form).
      return JSON.stringify({nodes: [], scoped: false, root: '',
                             reason: 'picked container not resolved'});
    }
  }
  var out = [];
  function addNode(parent, el, depth, isControl) {
    var node = {
      i: out.length, parent: parent, depth: depth,
      control: isControl ? 1 : 0,
      tag: (el.tagName || '').toLowerCase(),
      role: (el.getAttribute ? (el.getAttribute('role') || '') : '')
              .toLowerCase(),
      label: isControl ? clean(__wvpFieldLabel(el)) : ownLabel(el),
      value: ''
    };
    if (isControl) {
      try {
        node.value = (el.value != null ? String(el.value) : '').slice(0, 120);
      } catch (e) {}
    }
    out.push(node);
    try { el.setAttribute(MARK, String(node.i)); } catch (e) {}
    return node.i;
  }
  function marked(el) {
    var raw = null;
    try { raw = el.getAttribute(MARK); } catch (e) { return -1; }
    var i = raw === null ? NaN : parseInt(raw, 10);
    return (isNaN(i) || i < 0 || i >= out.length) ? -1 : i;
  }
  // Phase 1: the candidate controls, each with the element chain that reaches
  // it (the chain IS the path the tree will be built from).
  var candidates = [];
  var controls = [];
  try { controls = __wvpDeepAll(CONTROL_SEL); } catch (e) { controls = []; }
  for (var c = 0; c < controls.length; c++) {
    var el = controls[c];
    if (!el || offLimits(el)) continue;
    try { if (!__wvpVisible(el)) continue; } catch (e) { continue; }
    if (scoped) {
      // Inside a PICKED scope the whole subtree counts - including a control
      // scrolled out of view (the covered / on-top gate is dropped there).
      try { if (!__wvpOwns(el, root)) continue; } catch (e) { continue; }
    } else {
      // Unscoped: never a control the current layer COVERS, so an open popup's
      // field wins over the page's lookalike behind it.
      try { if (!__wvpTopmost(el)) continue; } catch (e) {}
    }
    var chain = chainTo(el, scoped ? root : null);
    if (!chain.length || chain[chain.length - 1] !== el) continue;
    var onForm = false;
    if (!scoped) {
      // Root an unscoped chain at the control's OWN <form>.  The document chain
      // (html > body > div > ...) is a dozen single-child levels of page chrome,
      // so the tree's first real choice would be the site's global navigation
      // instead of the form.  A picked scope already names the container, so it
      // is left exactly as resolved.
      for (var q = chain.length - 2; q >= 1; q--) {
        if ((chain[q].tagName || '').toLowerCase() === 'form') {
          chain = chain.slice(q); onForm = true; break;
        }
      }
    }
    candidates.push({el: el, chain: chain, form: onForm});
  }
  // Real form questions sit in a <form>; the site's navigation chrome does not.
  // When the page has any, the chrome is not part of THIS form's tree at all -
  // otherwise the global 'Search' box would be offered as a branch of it.
  var onForms = [];
  for (var f = 0; f < candidates.length; f++) {
    if (candidates[f].form) onForms.push(candidates[f]);
  }
  if (onForms.length) candidates = onForms;
  // Phase 2: emit the branches a candidate needs, then the candidate itself.
  for (var n = 0; n < candidates.length; n++) {
    if (out.length >= maxNodes - 1) break;
    var cand = candidates[n];
    var parent = -1;
    for (var k = 0; k < cand.chain.length - 1; k++) {
      var key = marked(cand.chain[k]);
      if (key < 0) {
        if (out.length >= maxNodes - 1) break;
        key = addNode(parent, cand.chain[k], k, false);
      }
      parent = key;
    }
    if (out.length >= maxNodes) break;
    addNode(parent, cand.el, cand.chain.length - 1, true);
  }
  return JSON.stringify({
    nodes: out, scoped: scoped,
    root: scoped ? (ownLabel(root) || '(picked container)') : 'top layer'
  });
})(arguments[0], arguments[1], arguments[2]);
"""

# ── Form Filling (live DOM): enumerate / write / read form fields ─────────────
# The RUNTIME owns the field->value binding.  This enumerator turns the page's
# visible form controls into a compact inventory (label + identity + a
# same-kind ordinal for the ambiguous fallback selector) - NEVER raw HTML, so a
# small model's window is not blown by markup.  It pierces open shadow roots
# and same-origin iframes exactly like the replay deep search.
JS_ENUMERATE_FORM_FIELDS = JS_DEEP_SEARCH + JS_FOREGROUND + """return (function (scopeSelectors, inFrame) {
  // A pick made inside an IFRAME (a popup) must be resolved from THAT frame's
  // document.  The plain deep search climbs to the top-most same-origin document
  // (so a stale frame left by a previous node can never trap an action), which
  // would look for the picked container in the WRONG document - the popup was
  // "ignored" and a base-page lookalike resolved instead.  The caller enters the
  // picked frame first, so with ``inFrame`` the CURRENT document is the root.
  if (inFrame) { __wvpRootDoc = function () { return document; }; }
  var out = [];
  var typeCount = {};
  var containers = [];
  function cssEsc(s) {
    try { return (window.CSS && CSS.escape) ? CSS.escape(s) : String(s); }
    catch (e) { return String(s); }
  }
  function _cleanText(x) {
    try {
      return (x.innerText || x.textContent || '').replace(/\\s+/g, ' ').trim();
    } catch (e) { return ''; }
  }
  // Resolve the human label for a field.  Delegates to the SHARED resolver so
  // the enumeration, the write and the read-back can NEVER disagree about what
  // an option is called - that disagreement (an empty write-side label) was
  // exactly the choice-write failure.
  function labelFor(el) {
    return __wvpFieldLabel(el);
  }
  // The page's OWN character limit when it sets NO native maxlength.  A
  // framework that enforces the limit in JS renders a counter / validation line
  // ("418/20 418 of 20 characters") and wires it through aria-describedby: the
  // limit stayed INVISIBLE, the model answered far too long, the page rejected
  // the field - and the repair pass had no limit to flag or fit, so the field
  // was unfixable and the Review loop never ended (live: LinkedIn's gross-salary
  // input, 418 chars in a 20-char field).  Read it off the field's OWN
  // described-by text, the one place the page states the limit.
  function charLimitFromPage(el) {
    try {
      var ids = String(el.getAttribute('aria-describedby') || '').split(/\\s+/);
      var root = (el.getRootNode && el.getRootNode()) || el.ownerDocument
                 || document;
      for (var i = 0; i < ids.length && i < 4; i++) {
        if (!ids[i] || !root.getElementById) continue;
        var n = root.getElementById(ids[i]);
        var t = String((n && n.textContent) || '').replace(/\\s+/g, ' ').trim();
        if (!t) continue;
        // "... of 20 characters", or a bare "N/20" counter node.
        var m = t.match(/\\bof\\s+(\\d+)\\s+characters?\\b/i)
             || t.match(/^\\d+\\s*\\/\\s*(\\d+)$/);
        if (m && parseInt(m[1], 10) > 0) return m[1];
      }
    } catch (e) {}
    return '';
  }
  // Stable identity for a group container (radiogroup / listbox / fieldset).
  function cid(c) {
    var i = containers.indexOf(c);
    if (i < 0) { i = containers.length; containers.push(c); }
    return i;
  }
  // The GROUP QUESTION of a radio/checkbox control: the option text is the
  // control's own <label>, but the question lives on the enclosing group
  // (aria-labelledby / role=radiogroup / fieldset legend / a nearby block).
  // Without it the model is asked about the OPTION, never the question.
  function qTextFor(el) {
    // The nearest preceding sibling BLOCK, when it reads like a question rather
    // than a chunk of the form.  A group whose container carries no
    // aria-label/aria-labelledby/legend has NO other handle on its question
    // (LinkedIn renders `<div>the question?</div><fieldset
    // role=radiogroup>...</fieldset>`), so the merged field had no label and
    // fell back to listing its options as the question.
    function prevBlockText(node) {
      try {
        var p = node && node.previousElementSibling;
        var t = p ? _cleanText(p) : '';
        return (t && t.length >= 3 && t.length <= 300) ? t : '';
      } catch (e) { return ''; }
    }
    try {
      var lb = el.getAttribute('aria-labelledby');
      if (lb) {
        var parts = [], ids = lb.split(/\\s+/);
        for (var i = 0; i < ids.length; i++) {
          var ref = document.getElementById(ids[i]);
          if (ref) { var rt = _cleanText(ref); if (rt) parts.push(rt); }
        }
        if (parts.length) return parts.join(' ');
      }
    } catch (e) {}
    try {
      var rg = el.closest ? el.closest(
        '[role="radiogroup"],[role="group"],[role="listbox"]') : null;
      if (rg) {
        var al = (rg.getAttribute('aria-label') || '').trim();
        if (al) return al;
        var lb2 = rg.getAttribute('aria-labelledby');
        if (lb2) {
          var r2 = document.getElementById(lb2.split(/\\s+/)[0]);
          var t2 = r2 ? _cleanText(r2) : '';
          if (t2) return t2;
        }
        var pq = prevBlockText(rg);
        if (pq) return pq;
      }
    } catch (e) {}
    try {
      var fs = el.closest ? el.closest('fieldset') : null;
      if (fs) {
        var lg = fs.querySelector('legend');
        if (lg) { var t3 = _cleanText(lg); if (t3) return t3; }
        var pf = prevBlockText(fs);
        if (pf) return pf;
      }
    } catch (e) {}
    try {
      var node = el;
      for (var d = 0; d < 6 && node && node.parentElement; d++) {
        node = node.parentElement;
        var cand = node.querySelector(
          ':scope > legend, :scope > [class*="question" i], ' +
          ':scope > [class*="label" i], :scope > [data-test*="label" i]');
        if (cand) {
          var ct = _cleanText(cand);
          if (ct && ct.length >= 3 && ct.length <= 300) return ct;
        }
      }
    } catch (e) {}
    return '';
  }
  // The control FAMILY the runtime writes with: text / select / combo (a
  // typeahead combobox) / choice (radio or checkbox) / switch (an ARIA toggle).
  function kindOf(el, tag, itype) {
    if (tag === 'select') return 'select';
    if (isChoiceRole(el, tag, itype)) return 'choice';
    var role = (el.getAttribute('role') || '').toLowerCase();
    if (role === 'switch') return 'switch';
    if (role === 'combobox' || role === 'listbox') return 'combo';
    if (el.getAttribute('aria-haspopup') || el.getAttribute('aria-autocomplete')) return 'combo';
    if (tag === 'button' && el.getAttribute('aria-pressed') != null) return 'switch';
    // A native datalist input (`<input list="...">`) and an editable nested in
    // a role=combobox wrapper are LIST-BACKED controls too: they accept free
    // text, but the page only accepts a listed value.  Read as plain text, the
    // datalist was never offered, so the model typed a value the page refused
    // to submit (observed: a 'City' autocomplete enumerated as text).
    if (tag === 'input' && el.getAttribute('list')) return 'combo';
    if (el.closest && el.closest('[role="combobox"]')) return 'combo';
    return 'text';
  }
  function isChoiceRole(el, tag, itype) {
    if (tag === 'input' && (itype === 'radio' || itype === 'checkbox')) return true;
    var role = (el.getAttribute('role') || '').toLowerCase();
    return role === 'radio' || role === 'checkbox';
  }
  // A NON-NATIVE dropdown (a typeahead combobox / a datalist input) keeps its
  // options OUTSIDE itself, so they were never enumerated and the model
  // answered free text against a list it could never select.  Read them from
  // the input's datalist and from the popup the control declares
  // (aria-controls / aria-owns) or sits inside (role=combobox > role=listbox).
  function comboOptions(el) {
    var opts = [];
    function add(t) {
      t = String(t == null ? '' : t).replace(/\s+/g, ' ').trim();
      if (t && opts.indexOf(t) < 0 && opts.length < 300) opts.push(t);
    }
    try {
      var lid = el.getAttribute('list');
      var dl = lid ? document.getElementById(lid) : null;
      if (dl) {
        var ds = dl.querySelectorAll('option');
        for (var i = 0; i < ds.length; i++) add(ds[i].value || ds[i].textContent);
      }
    } catch (e) {}
    try {
      var box = null;
      var owns = el.getAttribute('aria-controls') || el.getAttribute('aria-owns');
      if (owns) {
        var ids = owns.split(/\s+/);
        for (var k = 0; k < ids.length && !box; k++) box = document.getElementById(ids[k]);
      }
      if (!box && el.closest) box = el.closest('[role="combobox"]');
      if (box) {
        var list = ((box.getAttribute('role') || '') === 'listbox')
                 ? box : box.querySelector('[role="listbox"]');
        var os = list ? list.querySelectorAll('[role="option"]') : [];
        for (var j = 0; j < os.length; j++) add(os[j].textContent);
      }
    } catch (e) {}
    return opts.length ? opts : null;
  }
  function visit(root, depth, scoped, confine) {
    if (depth > 12 || !root || !root.querySelectorAll) return;
    // The SCOPE ROOT itself may be the shadow HOST / iframe the user picked
    // (LinkedIn's interop-outlet, a popup iframe): its fields live INSIDE it,
    // and a root that was never descended into yielded 0 fields - so the scan
    // fell back to the whole page and enumerated the fields UNDER the popup.
    if (root.shadowRoot) visit(root.shadowRoot, depth + 1, scoped, confine);
    else if (root.contentDocument) visit(root.contentDocument, depth + 1, scoped, confine);
    var nodes = [];
    try {
      nodes = root.querySelectorAll(
        // ARIA form widgets: a custom combobox / toggle / radio-or-checkbox
        // group is NOT an <input>, so the old query never saw it - a whole
        // Yes/No question list simply "did not exist" for the filler.
        'input, textarea, select, [contenteditable=""], [contenteditable="true"], ' +
        '[role="combobox"], [role="listbox"], [role="switch"], [role="radio"], ' +
        '[role="checkbox"], [role="textbox"], [role="spinbutton"]');
    } catch (e) { nodes = []; }
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      var tag = (el.tagName || '').toLowerCase();
      var itype = tag === 'input' ? (el.getAttribute('type') || 'text').toLowerCase() : tag;
      if (tag === 'input' && (itype === 'hidden' || itype === 'submit'
          || itype === 'button' || itype === 'reset' || itype === 'image'
          || itype === 'file' || itype === 'search')) continue;
      var role = (el.getAttribute('role') || '').toLowerCase();
      if (role === 'searchbox') continue;
      // A NON-NATIVE ARIA widget is accepted only inside a <form> or the picked
      // scope: a job-search page's filter chips are role=radio too, so a
      // whole-page ARIA sweep filled them.  Real form questions sit in a
      // <form>; nav chrome (the global search box) does not.
      var native = (tag === 'input' || tag === 'textarea' || tag === 'select'
                    || el.isContentEditable === true
                    || (el.getAttribute('contenteditable') || '') !== '');
      if (!native) {
        // A wrapper widget whose real control is a native CHILD must not be
        // counted twice: that control IS the target and is already in this
        // list, so enumerating the wrapper too would double the field.
        try {
          if (el.querySelector && el.querySelector(
              'input, textarea, select, [contenteditable=""], '
              + '[contenteditable="true"]')) continue;
        } catch (e3) {}
        // ...and a NON-NATIVE widget only counts inside a <form> or the picked
        // scope, so a job-search page's filter chips (role=radio) are never
        // treated as form choices.
        if (!(scoped || (el.closest && el.closest('form')))) continue;
      }
      if (el.disabled) continue;
      // VISIBILITY / ON-TOP are judged on what a user actually acts on.  A
      // styled radio / checkbox / switch is the REAL input hidden behind a
      // label (display:none, zero box), so testing the raw input dropped the
      // whole Yes/No list as "invisible" - exactly like JS_SET_CHOICE, the
      // LABEL is the clickable surface and must pass the gate instead.
      var vis_el = el;
      if (isChoiceRole(el, tag, itype) || role === 'switch') {
        var vis_lab = (el.closest ? el.closest('label') : null);
        if (!vis_lab && el.id) {
          try {
            vis_lab = document.querySelector('label[for="' + cssEsc(el.id) + '"]');
          } catch (e2) { vis_lab = null; }
        }
        if (vis_lab) vis_el = vis_lab;
      }
      // Never enumerate what the user cannot see; outside a picked scope a
      // field that is COVERED (a modal over it) is skipped too, and an open
      // POPUP confines the whole scan to its own subtree.
      //
      // The CONTROL **or** its LABEL satisfies the gate - never one instead of
      // the other.  A styled radio/checkbox hides the REAL input behind the
      // label, and a design that styles the INPUT and hides the label must keep
      // working, so this is purely ADDITIVE: it can only accept MORE than the
      // old raw-element test, never less.
      if (!__wvpVisible(el) && !__wvpVisible(vis_el)) continue;
      if (!scoped && !__wvpTopmost(el) && !__wvpTopmost(vis_el)) continue;
      // __wvpOwns(node, target) walks UP from node: the field is inside the
      // popup layer when walking up from the FIELD reaches that layer.
      if (confine && !__wvpOwns(el, confine)) continue;
      var key = tag + ':' + itype;
      var ord = (typeCount[key] || 0);
      typeCount[key] = ord + 1;
      var kind = kindOf(el, tag, itype);
      var options = null;
      if (tag === 'select') {
        options = [];
        // ponytail: 300 bounds a pathological list; every real form list
        // (countries, roles, universities) is well under it.  The old 40 cut
        // the TAIL off a country/role select, so the prompt never offered the
        // options the page actually had.
        for (var o = 0; o < el.options.length && options.length < 300; o++) {
          var _o = el.options[o];
          // A PROMPT entry is the widget asking, not an option: an empty value
          // attribute is the HTML convention for it and the only
          // language-independent signal, so it is never offered as an answer
          // (the model would otherwise 'select' the prompt and select nothing).
          if (_o.disabled || String(_o.value || '') === '') continue;
          var _ot = String(_o.text || _o.value || '').trim();
          if (_ot && options.indexOf(_ot) < 0) options.push(_ot);
        }
      } else if (kind === 'combo') {
        options = comboOptions(el);
      }
      var choice = isChoiceRole(el, tag, itype);
      // A choice control's option text is its OWN; only fall back to the generic
      // resolver when the control really has none of its own.
      var olabel = choice ? __wvpOptionLabel(el) : labelFor(el);
      if (!olabel) olabel = labelFor(el);
      if (choice && !olabel) {
        olabel = (el.getAttribute('aria-label') || _cleanText(el)
                  || el.getAttribute('value') || '').trim();
      }
      var gkey = '';
      if (choice) {
        if (el.name) {
          gkey = 'n:' + el.name;
        } else {
          var cont = el.closest ? el.closest(
            '[role="radiogroup"],[role="listbox"],fieldset') : null;
          gkey = cont ? ('c:' + cid(cont)) : ('s:' + out.length);
        }
      }
      out.push({
        index: out.length,
        tag: tag,
        type: itype,
        kind: kind,
        id: el.id || '',
        name: el.getAttribute('name') || '',
        placeholder: el.getAttribute('placeholder') || '',
        aria_label: el.getAttribute('aria-label') || '',
        label: olabel,
        question: choice ? qTextFor(el) : '',
        group: gkey,
        required: !!el.required,
        pattern: el.getAttribute('pattern') || '',
        inputmode: el.getAttribute('inputmode') || '',
        minlength: el.getAttribute('minlength') || '',
        // A maxlength set as a PROPERTY (React/Vue render it as a live
        // constraint, and a counter element like "0/20" proves the page
        // enforces one) leaves the ATTRIBUTE empty, so the limit was invisible
        // and an over-long value was written - the page rejected it and the
        // repair loop could never satisfy it.
        maxlength: el.getAttribute('maxlength')
          || (typeof el.maxLength === 'number' && el.maxLength > 0
              ? String(el.maxLength) : '')
          || charLimitFromPage(el),
        min: el.getAttribute('min') || '',
        max: el.getAttribute('max') || '',
        step: el.getAttribute('step') || '',
        options: options,
        type_index: ord
      });
    }
    var all = root.querySelectorAll('*');
    for (var j = 0; j < all.length; j++) {
      var h = all[j];
      // A host is entered when it is not HIDDEN - its own box is NOT required.
      // These outlets are position:absolute wrappers with a zero-height box
      // whose shadow content overflows it (LinkedIn's interop-outlet is
      // 1360x0), so a box/on-top test on the host rejected the whole subtree and
      // the form enumerated as 0 fields.  The fields inside still pass the
      // per-field visible / on-top gates below.
      if (!(h.shadowRoot || h.contentDocument)) continue;
      if (!__wvpHostEnterable(h)) continue;
      if (h.shadowRoot) visit(h.shadowRoot, depth + 1, scoped, confine);
      else if (h.contentDocument) visit(h.contentDocument, depth + 1, scoped, confine);
    }
  }
  // Optionally confine the scan to a picked container (a form / modal / an
  // iframe): resolve the scope by its deep-searched CSS selector so fields
  // behind the top frame (page chrome, the page under a modal) are never
  // enumerated.  Falls back to the whole document when the scope is gone.
  //
  // The picked container must resolve to the LAYER the user picked.  A plain
  // first-match deep search returns as soon as the FIRST root matches, and the
  // LIGHT DOM is searched before any shadow root - so a weak ladder candidate
  // ('form > div' matches the page's own form) resolved to the page UNDERNEATH
  // a shadow-hosted modal, and - because a scope drops the covered/on-top gate
  // - the fields BEHIND the modal were enumerated ('it only sees the base
  // one').  Collect EVERY deep match and prefer one that is rendered AND on top
  // (uncovered = the modal); fall back to the first match so a legitimate
  // container that is merely scrolled out of view is never lost.
  function scopeMatch(selectors) {
    var first = null;
    for (var si = 0; selectors && si < selectors.length; si++) {
      var ms = [];
      try { ms = __wvpDeepAll([selectors[si]]); } catch (e) { ms = []; }
      for (var mi = 0; mi < ms.length; mi++) {
        if (!first) first = ms[mi];
        try {
          if (__wvpVisible(ms[mi]) && __wvpTopmost(ms[mi])) return ms[mi];
        } catch (e) {}
      }
    }
    return first;
  }
  var root = __wvpRootDoc();
  var scoped = false;
  // With NO picked scope, an open POPUP confines the scan to its own layer: the
  // page BEHIND a popup is not "covered" everywhere (a header search box stays
  // visible beside the modal), so the per-field on-top gate alone let BASE-page
  // fields into the fill - they were answered and written while the popup was
  // the layer on screen (live: LinkedIn's global `Search` box beside the Easy
  // Apply dialog).  Layers are tried best-first and the first that holds a
  // field wins; none holding one leaves the scan exactly as it was.
  var layers = [];
  if (scopeSelectors && scopeSelectors.length) {
    var scopeEl = scopeMatch(scopeSelectors);
    if (scopeEl) {
      // A PICKED container IS the current layer: its whole subtree is
      // enumerated - including fields scrolled out of view - so the covered /
      // on-top gate is dropped inside a scope (the user chose this subtree).
      scoped = true;
      root = ((scopeEl.tagName || '').toLowerCase() === 'iframe'
              && scopeEl.contentDocument)
             ? scopeEl.contentDocument : scopeEl;
    }
  } else {
    layers = __wvpTopLayers();
  }
  if (layers.length) {
    for (var li = 0; li < layers.length; li++) {
      out = [];
      typeCount = {};
      containers = [];
      visit(root, 0, scoped, layers[li]);
      if (out.length) break;            // this layer holds the popup's fields
    }
    // An OPEN popup is the ONLY place a field may live: there is NO whole-page
    // fallback.  A wizard mid-transition holds no field for a moment, and that
    // fallback is what reached the global 'Search' box BEHIND the modal.
    // Returning nothing is correct - the caller's settle gate waits and re-scans.
  } else {
    visit(root, 0, scoped, null);
  }
  return JSON.stringify(out);
})(arguments[0], arguments[1]);
"""

# Write ONE value into ONE enumerated field, resolved by the field's own
# selector candidates (label/name/id ladder).  Setting ``value`` through the
# NATIVE setter plus input/change dispatch is required for React/Vue controlled
# inputs.  Selects match the value against option text/value; checkboxes/radios
# treat a truthy value as "checked".  The model never supplies a selector.
JS_SET_FIELD = JS_DEEP_SEARCH + JS_FOREGROUND + """return (function (selectors, want, value, kind) {
  function isEditable(el) {
    if (!el || el.nodeType !== 1) return false;
    var tag = (el.tagName || '').toLowerCase();
    return tag === 'input' || tag === 'textarea' || tag === 'select' || el.isContentEditable;
  }
  function norm(s) {
    return String(s == null ? '' : s).replace(/\\s+/g, ' ').trim().toLowerCase();
  }
  function yesNo(v) {
    var YES = ['yes', 'true', 'y', 'on', '1'];
    var NO = ['no', 'false', 'n', 'off', '0'];
    return YES.indexOf(v) >= 0 ? 'yes' : (NO.indexOf(v) >= 0 ? 'no' : '');
  }
  // Foreground only: never type into a covered / background twin; the
  // recorded ladder is a last resort and must ALSO be visible + on top.
  //
  // AMBIGUITY GUARD: a candidate that matches SEVERAL visible controls is NOT
  // this field's identity - a bare 'input' matches EVERY input on the page - so
  // it is SKIPPED rather than resolved to its first match.  That first match IS
  // the form's first field, and taking it is exactly how a value meant for one
  // question was typed over the already-correct 'First name'.
  function onlyOne(sel) {
    var m = __wvpDeepAll(sel), hit = null, n = 0;
    for (var i = 0; i < m.length; i++) {
      if (!(__wvpVisible(m[i]) && __wvpTopmost(m[i]))) continue;
      if (n === 0) hit = m[i];
      if (++n > 1) return null;
    }
    return n === 1 ? hit : null;
  }
  function bareTag(sel) { return /^[a-z][a-z0-9]*$/i.test(String(sel).trim()); }
  var el = null;
  for (var _s = 0; selectors && _s < selectors.length && !el; _s++) {
    el = onlyOne(selectors[_s]);
  }
  if (!el) {
    // The want-indexed fallback is only safe for a SPECIFIC candidate: applied
    // to a bare tag it would take the Nth control of the WHOLE page.
    for (var _f = 0; selectors && _f < selectors.length && !el; _f++) {
      if (bareTag(selectors[_f])) continue;
      var _fb = __wvpDeepFind(selectors[_f], want);
      if (_fb && __wvpVisible(_fb) && __wvpTopmost(_fb)) el = _fb;
    }
  }
  if (!el || !isEditable(el)) return false;
  try { el.scrollIntoView({block: 'center', inline: 'center'}); } catch (e) {}
  try { el.focus({preventScroll: true}); } catch (e) {}
  var tag = (el.tagName || '').toLowerCase();
  var itype = tag === 'input' ? (el.getAttribute('type') || 'text').toLowerCase() : tag;
  function fire() {
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
  }
  if (itype === 'checkbox' || itype === 'radio') {
    var truthy = /^(true|1|yes|on|checked)$/i.test(String(value).trim());
    el.checked = truthy;
    fire();
    return true;
  }
  if (tag === 'select') {
    // Exact option text/value first, then a normalized/partial match, then a
    // yes/no synonym fold (a "Have you ever..." select answers Yes|No).
    var v = norm(value);
    var best = -1, score = 0;
    for (var i = 0; i < el.options.length; i++) {
      var o = el.options[i];
      if (o.disabled) continue;
      var ot = norm(o.text), ov = norm(o.value);
      if (ot === v || ov === v) { best = i; score = 3; break; }
      if (score < 2 && v && ot && (ot.indexOf(v) >= 0 || v.indexOf(ot) >= 0)) {
        best = i; score = 2;
      }
    }
    if (best < 0 && v) {
      var yv = yesNo(v);
      if (yv) {
        for (var j = 0; j < el.options.length; j++) {
          if (yesNo(norm(el.options[j].text)) === yv
              || yesNo(norm(el.options[j].value)) === yv) { best = j; break; }
        }
      }
    }
    if (best < 0) return false;
    el.selectedIndex = best;
    fire();
    return true;
  }
  if (el.isContentEditable) {
    try {
      document.execCommand('selectAll', false, null);
      document.execCommand('insertText', false, String(value));
      el.dispatchEvent(new Event('input', {bubbles: true}));
      return true;
    } catch (e) {}
  }
  // Realm-safe: a field inside a SAME-ORIGIN IFRAME is not an instance of the
  // TOP window's HTMLInputElement, so `instanceof` wrongly rejects it and every
  // write into an iframe-hosted form is reported as failed.  Compare tag names
  // and use the element's OWN window prototype for the native value setter.
  if (tag !== 'textarea' && tag !== 'input') return false;
  var win = (el.ownerDocument && el.ownerDocument.defaultView) || window;
  var proto = (tag === 'textarea')
      ? ((win.HTMLTextAreaElement || HTMLTextAreaElement).prototype)
      : ((win.HTMLInputElement || HTMLInputElement).prototype);
  var setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
  setter.call(el, String(value));
  fire();
  // A typeahead combobox (role=combobox / aria-haspopup) only COMMITS its
  // value once one of its suggestions is ACCEPTED - typing alone leaves the
  // widget with an uncommitted value, which is why the field read back empty.
  // A synthetic Enter is untrusted and most widgets ignore it, so the option
  // whose text matches the value is CLICKED - what a user does - and the keys
  // stay as the last-resort fallback.
  if (kind === 'combo') {
    try {
      var W = (el.ownerDocument && el.ownerDocument.defaultView) || window;
      var list = null;
      try {
        var owns = el.getAttribute('aria-controls') || el.getAttribute('aria-owns');
        if (owns) {
          var ids = owns.split(/\s+/);
          for (var qi = 0; qi < ids.length && !list; qi++) list = document.getElementById(ids[qi]);
        }
        if (!list && el.closest) {
          var cbox = el.closest('[role="combobox"]');
          if (cbox) list = ((cbox.getAttribute('role') || '') === 'listbox')
                      ? cbox : cbox.querySelector('[role="listbox"]');
        }
        if (!list) {
          // The PAGE-WIDE listbox fallback is for a widget that DECLARES itself
          // a combobox (role / aria-haspopup / aria-autocomplete): a datalist
          // input has no listbox at all, and grabbing an unrelated open listbox
          // would click a foreign option.
          var declared = (el.getAttribute('role') || '').toLowerCase() === 'combobox'
              || el.getAttribute('aria-haspopup') || el.getAttribute('aria-autocomplete');
          if (declared) {
            var lists = document.querySelectorAll('[role="listbox"]');
            for (var li = 0; li < lists.length; li++) {
              if (__wvpVisible(lists[li])) { list = lists[li]; break; }
            }
          }
        }
      } catch (e) {}
      var vc = norm(value), hit = null;
      if (list) {
        var els = list.querySelectorAll('[role="option"], li');
        for (var oi = 0; oi < els.length; oi++) {
          var ot = norm(els[oi].textContent);
          if (ot && (ot === vc || ot.indexOf(vc) >= 0 || vc.indexOf(ot) >= 0)) {
            hit = els[oi];
            break;
          }
        }
      }
      if (hit) { try { hit.click(); } catch (e) {} }
      el.dispatchEvent(new W.KeyboardEvent('keydown', {key: 'ArrowDown', bubbles: true}));
      el.dispatchEvent(new W.KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
    } catch (e) {}
  }
  // A TYPED input (date / number / time / month...) SILENTLY rejects a
  // malformed value and falls back to EMPTY.  Report the failure instead of a
  // false success, otherwise the runtime believes the field was filled while
  // the page holds nothing (and the repair pass never sees a reason to act).
  try {
    if (String(value) !== '' && el.value === '') return false;
  } catch (e) {}
  return true;
})(arguments[0], arguments[1], arguments[2], arguments[3]);
"""

# Read a COMBOBOX's option list, OPENING the control when its popup is not
# rendered yet.  A typeahead renders its options only once it is OPEN, so the
# enumerator (which never opens a control) saw none, the model answered free
# text, and the page refused to submit it (observed: a 'Location (city)' combo
# answered 'Springfield, United States' - no such option - and every later pass
# then skipped the field as 'already filled').  ``open`` lets this call peek the
# popup (focus + click + a keyboard nudge); the caller polls until the list
# renders.  Read-only: nothing is ever selected.  The resolved control must
# actually BE a list-backed control, so a generic ladder candidate can never
# read an unrelated input's options.
JS_COMBO_OPTIONS = JS_DEEP_SEARCH + JS_FOREGROUND + """return (function (selectors, want, open) {
  function norm(s) { return String(s == null ? '' : s).replace(/\\s+/g, ' ').trim(); }
  function isCombo(el) {
    var role = (el.getAttribute('role') || '').toLowerCase();
    return role === 'combobox' || el.getAttribute('list')
        || el.getAttribute('aria-haspopup') || el.getAttribute('aria-autocomplete')
        || (el.closest && el.closest('[role="combobox"]'));
  }
  function fromDatalist(el) {
    try {
      var lid = el.getAttribute('list');
      var dl = lid ? document.getElementById(lid) : null;
      if (!dl) return [];
      var out = [], ds = dl.querySelectorAll('option');
      for (var i = 0; i < ds.length && out.length < 60; i++) {
        var v = norm(ds[i].value || ds[i].textContent);
        if (v && out.indexOf(v) < 0) out.push(v);
      }
      return out;
    } catch (e) { return []; }
  }
  function fromPopup(el, allowHidden) {
    var out = [];
    function collect(node) {
      try {
        var els = node.querySelectorAll(
          '[role="option"], [role="menuitem"], li, option');
        for (var i = 0; i < els.length && out.length < 60; i++) {
          var t = norm(els[i].textContent || els[i].value || '');
          if (t && t.length <= 120 && out.indexOf(t) < 0) out.push(t);
        }
      } catch (e) {}
    }
    try {
      var nodes = [];
      var owns = el.getAttribute('aria-controls') || el.getAttribute('aria-owns');
      if (owns) {
        var ids = owns.split(/\\s+/);
        for (var k = 0; k < ids.length; k++) {
          var n = document.getElementById(ids[k]);
          if (n) nodes.push(n);
        }
      }
      var cbox = el.closest ? el.closest('[role="combobox"]') : null;
      if (cbox) nodes.push(cbox);
      var all = document.querySelectorAll('[role="listbox"]');
      for (var j = 0; j < all.length; j++) nodes.push(all[j]);
      for (var m = 0; m < nodes.length; m++) {
        var lb = (nodes[m].getAttribute && nodes[m].getAttribute('role') === 'listbox')
               ? nodes[m]
               : (nodes[m].querySelector ? nodes[m].querySelector('[role="listbox"]') : null);
        if (lb && (allowHidden || __wvpVisible(lb))) collect(lb);
      }
    } catch (e) {}
    return out;
  }
  var el = null;
  for (var s = 0; selectors && s < selectors.length && !el; s++) {
    var ms = __wvpDeepAll(selectors[s]);
    for (var i2 = 0; i2 < ms.length; i2++) {
      if (__wvpVisible(ms[i2]) && __wvpTopmost(ms[i2]) && isCombo(ms[i2])) {
        el = ms[i2]; break;
      }
    }
  }
  if (!el) return '[]';
  var opts = fromDatalist(el);
  if (!opts.length) opts = fromPopup(el, false);
  if (!opts.length && open) {
    try { el.scrollIntoView({block: 'center', inline: 'center'}); } catch (e) {}
    try { el.focus({preventScroll: true}); } catch (e) {}
    try { el.click(); } catch (e) {}
    try {
      var W = (el.ownerDocument && el.ownerDocument.defaultView) || window;
      el.dispatchEvent(new W.MouseEvent('mousedown', {bubbles: true}));
      el.dispatchEvent(new W.KeyboardEvent('keydown', {key: 'ArrowDown', bubbles: true}));
    } catch (e) {}
    opts = fromPopup(el, true);
  }
  return JSON.stringify(opts);
})(arguments[0], arguments[1], arguments[2]);
"""

# Write ONE choice answer (radio / checkbox group / ARIA switch): resolve the
# group's controls by their own selector ladder (deep) and select the control
# whose OPTION LABEL matches the answer.  A negation leaves a lone checkbox
# unchecked - the field IS answered, just negatively.  The model never supplies
# a selector.
JS_SET_CHOICE = JS_DEEP_SEARCH + JS_FOREGROUND + """return (function (selectors, want, value) {
  function findAll(sel) {
    var out = [];
    (function collect(root, depth) {
      if (depth > 12 || !root || !root.querySelectorAll) return;
      var r = null;
      try { r = root.querySelectorAll(sel); } catch (e) { return; }
      for (var i = 0; i < r.length; i++) out.push(r[i]);
      var all = root.querySelectorAll('*');
      for (var j = 0; j < all.length; j++) {
        var h = all[j];
        if (h.shadowRoot) collect(h.shadowRoot, depth + 1);
        else if (h.contentDocument) collect(h.contentDocument, depth + 1);
      }
    })(__wvpRootDoc(), 0);
    return out;
  }
  function norm(s) {
    return String(s == null ? '' : s).replace(/\\s+/g, ' ').trim().toLowerCase();
  }
  var v = norm(value);
  var YES = ['yes', 'true', 'y', 'on', '1'];
  var NO = ['no', 'false', 'n', 'off', '0'];
  function matches(label) {
    var l = norm(label);
    if (!v || !l) return false;
    if (v === l) return true;
    if (YES.indexOf(v) >= 0) return YES.indexOf(l) >= 0 || /^(yes|agree|accept|true)/.test(l);
    if (NO.indexOf(v) >= 0) return NO.indexOf(l) >= 0 || /^(no|disagree|decline|none|false)/.test(l);
    return l.indexOf(v) >= 0 || v.indexOf(l) >= 0;
  }
  function isChecked(el) {
    try { if (el.checked != null) return !!el.checked; } catch (e) {}
    return el.getAttribute('aria-checked') === 'true'
        || el.getAttribute('aria-pressed') === 'true';
  }
  function activate(c) {
    var t = c.hit || c.el;
    try { t.scrollIntoView({block: 'center', inline: 'center'}); } catch (e) {}
    try { t.focus({preventScroll: true}); } catch (e) {}
    try { t.click(); } catch (e) {}
    // A wrapper may swallow the click: fall back to the control's REAL label
    // when the state did not move, so the option ends up actually selected.
    if (!isChecked(c.el)) {
      var lb = __wvpLabelOf(c.el);
      if (lb && lb !== t) { try { lb.click(); } catch (e) {} }
    }
    return true;
  }
  // Foreground only: a covered / background twin must never be clicked.
  // Collect across EVERY candidate and dedupe: a merged group's ladder is one
  // anchor per OPTION, so stopping at the first selector that matched anything
  // left the answer's own option OUT of the set and nothing could click it
  // (live: selectors=['input', ''] -> 'target unresolved or not editable').
  var controls = [];
  var _seen = [];
  for (var s = 0; selectors && s < selectors.length; s++) {
    var _all = findAll(selectors[s]);
    for (var _v = 0; _v < _all.length; _v++) {
      if (_seen.indexOf(_all[_v]) >= 0) continue;
      var _hit = __wvpClickTarget(_all[_v]);
      if (_hit) { _seen.push(_all[_v]); controls.push({el: _all[_v], hit: _hit}); }
    }
    if (controls.length > 80) break;   // runaway guard, never a match
  }
  if (!controls.length) {
    var one = __wvpDeepFindAny(selectors, want);
    var _h = one ? __wvpClickTarget(one) : null;
    if (_h) controls = [{el: one, hit: _h}];
  }
  if (!controls.length) return false;
  // 1. The control whose OPTION LABEL matches the answer.
  for (var c = 0; c < controls.length; c++) {
    if (matches(__wvpOptionLabel(controls[c].el))) {
      if (!isChecked(controls[c].el)) activate(controls[c]);
      return true;
    }
  }
  // 2. A lone boolean control (single checkbox / switch): set it per the answer
  //    (a "No" answer means ensure unchecked - still an answer).
  if (controls.length === 1) {
    var only = controls[0];
    var truthy = YES.indexOf(v) >= 0;
    var falsy = NO.indexOf(v) >= 0;
    if (truthy || falsy) {
      if (isChecked(only.el) !== truthy) activate(only);
      return true;
    }
  }
  return false;
})(arguments[0], arguments[1], arguments[2]);
"""

# Read back ONE enumerated field's STATE (verify + repair pass).  Returns a JSON
# object ``{found, value, valid, message}``: ``value`` is the field's own value
# (the checked OPTION label for a choice group, 'on'/'off' for a switch - a
# deliberate "No" reads back as answered rather than empty), ``valid`` is the
# field's NATIVE constraint state (type=email/date/number, pattern, min/max/step,
# required) and ``message`` the browser's validationMessage.  A value the page
# REJECTS this way is corrected by the repair pass instead of being left wrong.
JS_FIELD_STATE = JS_DEEP_SEARCH + JS_FOREGROUND + """return (function (selectors, want, kind) {
  function findAll(sel) {
    var out = [];
    (function collect(root, depth) {
      if (depth > 12 || !root || !root.querySelectorAll) return;
      var r = null;
      try { r = root.querySelectorAll(sel); } catch (e) { return; }
      for (var i = 0; i < r.length; i++) out.push(r[i]);
      var all = root.querySelectorAll('*');
      for (var j = 0; j < all.length; j++) {
        var h = all[j];
        if (h.shadowRoot) collect(h.shadowRoot, depth + 1);
        else if (h.contentDocument) collect(h.contentDocument, depth + 1);
      }
    })(__wvpRootDoc(), 0);
    return out;
  }
  function isChecked(el) {
    try { if (el.checked != null) return !!el.checked; } catch (e) {}
    return el.getAttribute('aria-checked') === 'true'
        || el.getAttribute('aria-pressed') === 'true';
  }
  // LAST-RESORT fallback ONLY: a bare ``role="alert"`` node (a picker's live
  // region looks identical) still has to READ like feedback.  Every stronger
  // signal - native validity, ``aria-invalid``, ARIA association, an
  // error/invalid-classed channel - is read STRUCTURALLY, so this list never
  // has to grow for another language or framework.
  var ERROR_TEXT_RE = /please|invalid|required|must|cannot|can't|maximum|minimum|too (short|long)|exceed|allowed|not valid|enter a valid|isn't valid|doesn't match|must match/i;
  function errorText(nodeEl) {
    try { return String(nodeEl.textContent || '').trim(); } catch (e) { return ''; }
  }
  function isErrorChannel(nodeEl) {
    try {
      if (nodeEl.getAttribute && nodeEl.getAttribute('aria-invalid') === 'true') return true;
      var cls = nodeEl.className;
      return typeof cls === 'string' && /error|invalid|feedback/i.test(cls);
    } catch (e) { return false; }
  }
  function associatedError(el, value) {
    // The page's OWN wiring, read structurally: the field names the node that
    // holds its message, so the trigger is independent of language and of any
    // keyword list.  ``aria-errormessage`` names an error outright; an
    // ``aria-describedby`` target only counts when the page itself marks it as
    // an error channel (an unmarked one is usually helper text).
    var errId = '', descIds = '';
    try {
      if (el.getAttribute) {
        errId = String(el.getAttribute('aria-errormessage') || '');
        descIds = String(el.getAttribute('aria-describedby') || '');
      }
    } catch (e) { return ''; }
    var root = null;
    try { root = (el.getRootNode && el.getRootNode()) || el.ownerDocument || document; }
    catch (e) { root = el.ownerDocument || document; }
    var want = String(value == null ? '' : value).trim().toLowerCase();
    function pick(ids, requireChannel) {
      var list = String(ids || '').split(' ');
      for (var i = 0; i < list.length && i < 4; i++) {
        if (!list[i]) continue;
        var n = null;
        try { n = root.querySelector('#' + __wvpCssEsc(list[i])); } catch (e) { n = null; }
        if (!n) continue;
        if (requireChannel && !isErrorChannel(n)) continue;
        var t = errorText(n);
        if (!t || t.length > 200) continue;
        if (want && t.toLowerCase() === want) continue;
        return t;
      }
      return '';
    }
    return pick(errId, false) || pick(descIds, true);
  }
  function pageError(el, value) {
    // Framework-rendered rejections (LinkedIn's "Please enter a valid answer",
    // Ember/React inline feedback) do NOT set el.validity.valid, so a form that
    // only shows its errors once a "Review"/"Next" click submits it looked
    // VALID to the repair pass and could never be repaired.  Walk a few
    // ancestors and read the page's error channel instead.
    try {
      if (el.getAttribute && el.getAttribute('aria-invalid') === 'true') {
        return associatedError(el, value) || 'the page marks this field invalid';
      }
      // The field NAMES its own message (ARIA association): the trigger is the
      // page's wiring, not a keyword, so it holds in any language.
      var assoc = associatedError(el, value);
      if (assoc) return assoc;
      var current = String(value == null ? '' : value).replace(/\\s+/g, ' ').trim().toLowerCase();
      var node = el;
      for (var up = 0; up < 4 && node; up++) {
        var kids = node.querySelectorAll ? node.querySelectorAll(
          '[role="alert"], [aria-invalid="true"], [class*="error"], ' +
          '[class*="invalid"], [class*="inline-feedback"]') : [];
        for (var i = 0; i < kids.length && i < 8; i++) {
          var t = (kids[i].textContent || '').replace(/\\s+/g, ' ').trim();
          if (!t || t.length > 160) continue;
          if (current && t.toLowerCase() === current) continue;
          // STRONG channel: the page itself marks this node invalid or names it
          // an error / feedback slot, so its text IS feedback whatever language
          // it is written in.  A localized message ("Introduce una respuesta
          // valida") never matched the English keyword list, so the rejected
          // field was never flagged and the repair loop re-ran without fixing it.
          var _cls = kids[i].className;
          var strongChannel = kids[i].getAttribute('aria-invalid') === 'true'
            || (typeof _cls === 'string' && /error|invalid|inline-feedback/i.test(_cls));
          // WEAK channel (role=alert / aria-live): a picker's month header
          // ("September 2026") and the field's own value also live here, so its
          // text must still READ like validation feedback before it counts.
          if (strongChannel || ERROR_TEXT_RE.test(t)) return t;
        }
        node = node.parentElement;
      }
    } catch (e) {}
    return '';
  }
  function state(el, value) {
    var valid = true, message = '';
    try {
      if (el.validity) { valid = !!el.validity.valid; message = el.validationMessage || ''; }
    } catch (e) {}
    if (valid) {
      var pe = pageError(el, value);
      if (pe) { valid = false; message = 'the page rejected this value: ' + pe; }
    }
    return JSON.stringify({
      found: true,
      tag: (el.tagName || '').toLowerCase(),
      value: value == null ? '' : String(value),
      valid: valid,
      message: message
    });
  }
  // Collect across EVERY candidate (deduped): see JS_SET_CHOICE - a merged
  // group's ladder is one anchor per OPTION and the read-back must see them all.
  //
  // AMBIGUITY GUARD (a single-control kind): a candidate matching SEVERAL
  // visible controls is NOT this field's identity - a bare 'input' matches every
  // input on the page - so it is SKIPPED rather than resolved to its first
  // match, which IS the form's first field.  That is how a value meant for
  // another question was read from / written over the correct 'First name'.
  var controls = [];
  var _seen = [];
  var _choice = (kind === 'choice' || kind === 'switch');
  for (var s = 0; selectors && s < selectors.length; s++) {
    var _all = findAll(selectors[s]);
    var _hits = [];
    for (var _v = 0; _v < _all.length; _v++) {
      if (_seen.indexOf(_all[_v]) >= 0) continue;
      if (__wvpClickTarget(_all[_v])) _hits.push(_all[_v]);
    }
    if (_choice) {
      for (var _h = 0; _h < _hits.length; _h++) {
        _seen.push(_hits[_h]);
        controls.push(_hits[_h]);
      }
      if (controls.length > 80) break;
      continue;
    }
    if (_hits.length === 1) { _seen.push(_hits[0]); controls = [_hits[0]]; break; }
  }
  if (!controls.length) {
    for (var _q = 0; selectors && _q < selectors.length && !controls.length; _q++) {
      // The want-indexed fallback is only safe for a SPECIFIC candidate:
      // applied to a bare tag it would take the Nth control of the page.
      if (/^[a-z][a-z0-9]*$/i.test(String(selectors[_q]).trim())) continue;
      var one = __wvpDeepFind(selectors[_q], want);
      if (one) controls = [one];
    }
  }
  if (!controls.length) {
    return JSON.stringify({found: false, value: '', valid: false,
                           message: 'field not found'});
  }
  if (kind === 'choice' || kind === 'switch') {
    if (controls.length === 1) {
      var only = controls[0];
      var role = (only.getAttribute('role') || '').toLowerCase();
      if (role === 'switch' || only.getAttribute('aria-pressed') != null
          || only.getAttribute('aria-checked') != null) {
        return state(only, isChecked(only) ? 'on' : 'off');
      }
      if ((only.getAttribute('type') || '').toLowerCase() === 'checkbox') {
        return state(only, isChecked(only) ? 'checked' : 'unchecked');
      }
    }
    for (var c = 0; c < controls.length; c++) {
      if (isChecked(controls[c])) return state(controls[c], __wvpOptionLabel(controls[c]));
    }
    return state(controls[0], '');
  }
  var el = controls[0];
  var tag = (el.tagName || '').toLowerCase();
  if (tag === 'select') {
    var sv = '';
    try {
      var so = el.selectedIndex >= 0 ? el.options[el.selectedIndex] : null;
      // A select sitting on its PROMPT option (the HTML convention is an empty
      // value attribute) has selected NOTHING: reporting the prompt text as a
      // value made the harness read it as 'already filled' and skip the field.
      if (so && String(so.value || '') !== '') sv = String(so.text || so.value || '');
    } catch (e) {}
    return state(el, sv);
  }
  // An ARIA combobox often keeps the committed choice OUTSIDE the input (in
  // the active option), so a plain el.value read came back empty and a land-
  // ed selection was reported as a failed write.
  if (kind === 'combo') {
    try {
      var ad = el.getAttribute('aria-activedescendant');
      var adn = ad ? document.getElementById(ad) : null;
      if (adn) {
        var adt = String(adn.textContent || '').replace(/\s+/g, ' ').trim();
        if (adt) return state(el, adt);
      }
    } catch (e) {}
  }
  if (el.isContentEditable) return state(el, el.textContent || '');
  return state(el, el.value != null ? el.value : '');
})(arguments[0], arguments[1], arguments[2]);
"""

# Shadow-piercing focus fallback: tries every recorded CSS candidate across
# every shadow root and same-origin iframe (Google login's identifierId lives
# under a c-wiz custom element) and focuses it - WebDriver find_elements cannot
# see those elements.
JS_FOCUS_DOM = JS_DEEP_SEARCH + """return (function (selectors) {
  var el = __wvpDeepFindAny(selectors, 0);
  if (!el) return false;
  try { el.scrollIntoView({block:'center', inline:'center'}); } catch (e) {}
  try { el.focus({preventScroll:true}); } catch (e) {}
  return true;
})(arguments[0]);
"""

JS_KEY = """
(function (key, modifiers) {
  var init = { bubbles: true, cancelable: true, composed: true, view: window, key: key };
  var mods = modifiers || [];
  if (mods.indexOf('Ctrl') >= 0) init.ctrlKey = true;
  if (mods.indexOf('Alt') >= 0) init.altKey = true;
  if (mods.indexOf('Shift') >= 0) init.shiftKey = true;
  if (mods.indexOf('Meta') >= 0) init.metaKey = true;
  var target = document.activeElement || document.body;
  target.dispatchEvent(new KeyboardEvent('keydown', init));
  target.dispatchEvent(new KeyboardEvent('keyup', init));
  return true;
})(arguments[0], arguments[1]);
"""

JS_SELECT = """
(function (el, value) {
  el.value = value;
  el.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
})(arguments[0], arguments[1]);
"""

JS_SUBMIT = """
(function (el) {
  if (typeof el.requestSubmit === 'function') { el.requestSubmit(); return true; }
  var btn = el.querySelector('button[type=submit], input[type=submit]');
  if (btn) { btn.click(); return true; }
  el.submit();
  return true;
})(arguments[0]);
"""

JS_SCROLL = JS_DEEP_SEARCH + """
(function (el, totalDelta, px, py, selectors) {
  // A wheel event scrolls the NEAREST scrollable ancestor of the element under
  // the cursor, NOT the document - feed/chat/lightbox panes scroll themselves
  // while the body stays fixed.  findScroller() walks up from the wheel target;
  // elementFromPoint() at the recorded cursor position is preferred because a
  // locator can degenerate into a generic attribute match (e.g. hundreds of
  // imgs sharing data-loaded=true) and resolve to a lookalike far from the
  // position the wheel actually scrolled.  When neither is available every
  // recorded candidate is deep-searched through shadow roots and same-origin
  // iframes (WebDriver cannot reach a shadow-hosted frame).
  function findScroller(node) {
    for (var n = node; n && n.nodeType === 1; n = n.parentElement) {
      var oy = getComputedStyle(n).overflowY;
      if (n.scrollHeight > n.clientHeight + 1 &&
          (oy === 'auto' || oy === 'scroll' || oy === 'overlay')) return n;
    }
    return null;
  }
  var scroller = null;
  if (typeof px === 'number' && typeof py === 'number') {
    try {
      var hit = document.elementFromPoint(px, py);
      if (hit && hit.nodeType === 1) scroller = findScroller(hit);
    } catch (err) {}
  }
  if (!scroller && el && el.nodeType === 1) scroller = findScroller(el);
  if (!scroller && selectors && selectors.length) {
    try { el = __wvpDeepFindAny(selectors, 0); } catch (err) { el = null; }
    if (el && el.nodeType === 1) scroller = findScroller(el);
  }
  if (!scroller) {
    var doc = (el && el.nodeType === 1) ? (el.ownerDocument || document) : document;
    scroller = doc.scrollingElement || doc.documentElement;
  }
  var steps = Math.max(1, Math.min(10, Math.round(Math.abs(totalDelta) / 120)));
  var per = totalDelta / steps;
  for (var i = 0; i < steps; i++) scroller.scrollBy(0, per);
  return true;
})(arguments[0], arguments[1], arguments[2], arguments[3], arguments[4]);
"""

# Last-resort JS drag: synthetic pointer events on the source element, moving
# from the recorded start to the recorded drop offset.  Coarse by design - the
# native ActionChains drag is the primary path; this only exists so a drag
# never silently no-ops when the source sits in a shadow root WebDriver
# cannot pierce.
JS_DRAG = JS_DEEP_SEARCH + """return (function (selectors, sx, sy, dx, dy) {
  var el = __wvpDeepFindAny(selectors, 0);
  if (!el) return false;
  var cx = sx + (dx || 0);
  var cy = sy + (dy || 0);
  var types = ['pointerdown', 'mousedown', 'pointermove', 'pointerup', 'mouseup'];
  var pts = [[sx, sy], [sx, sy], [cx, cy], [cx, cy], [cx, cy]];
  for (var i = 0; i < types.length; i++) {
    var init = {
      bubbles: true, cancelable: true, composed: true, view: window,
      clientX: pts[i][0], clientY: pts[i][1], button: 0, buttons: 1
    };
    var ctor = types[i].indexOf('pointer') === 0 ? PointerEvent : MouseEvent;
    try { el.dispatchEvent(new ctor(types[i], init)); } catch (err) {}
  }
  return true;
})(arguments[0], arguments[1], arguments[2], arguments[3], arguments[4]);
"""


# Last-resort coordinate hit-test: scroll the recorded document-space point
# back into the viewport center, then elementFromPoint() the exact pixel the
# user pressed.  Layout must have kept the element near its recorded position;
# used ONLY when every selector-based layer (strict + fuzzy + shadow deep-find)
# has already missed - the web analogue of the desktop recorder's low-confidence
# region search.  Shadow-DOM elements come back as None (WebDriver cannot
# serialize them) - the JS dispatch path covers those instead.
JS_ELEMENT_FROM_POINT = """return (function (x, y, vw, vh) {
  window.scrollTo(Math.max(0, x - Math.floor(vw / 2)), Math.max(0, y - Math.floor(vh / 2)));
  var el = document.elementFromPoint(Math.floor(vw / 2), Math.floor(vh / 2));
  if (!el || el.nodeType !== 1) return null;
  return el;
})(arguments[0], arguments[1], arguments[2], arguments[3]);
"""


# Visibility probe used by the replay wait helpers.  Prefers the native
# checkVisibility() (opacity/CSS-visibility aware - the same check the LinkedIn
# helper scripts run before clicking) and degrades to computed-style + box
# geometry where checkVisibility is unavailable.
JS_ELEMENT_VISIBLE = """return (function (el) {
  if (!el || el.nodeType !== 1) return false;
  try {
    if (typeof el.checkVisibility === 'function') {
      return el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true});
    }
  } catch (e) {}
  var s = window.getComputedStyle(el);
  if (!s || s.visibility === 'hidden' || s.display === 'none') return false;
  var r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
})(arguments[0]);
"""


class ElementNotFoundError(RuntimeError):
    """Raised when no candidate in a locator chain resolves to an element."""


def _css_attr_val(value: str) -> str:
    """Escape an attribute value for a single-quoted CSS [attr='...'] selector.
    Backslashes and quotes break the selector; control whitespace is collapsed
    so multi-line attribute values cannot invalidate the expression."""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("\t", " ")
    )


def _xpath_lit(value: str) -> str:
    """XPath 1.0 string literal for *value*: single-quoted when possible,
    double-quoted when the value contains only single quotes, concat()-spliced
    when it contains both (no literal can hold both quote types in XPath 1.0)."""
    value = str(value)
    if "'" not in value:
        return "'" + value + "'"
    if '"' not in value:
        return '"' + value + '"'
    return "concat(" + ",\"'\",".join("'" + p + "'" for p in value.split("'")) + ")"


def _candidate_selector(candidate: dict[str, Any]):
    kind = candidate["kind"]
    if kind == "id":
        return By.ID, candidate["value"]
    if kind == "data":
        name = candidate.get("name", "")
        return By.CSS_SELECTOR, f"[{name}='{_css_attr_val(candidate.get('value', ''))}']"
    if kind == "css":
        return By.CSS_SELECTOR, candidate["value"]
    if kind == "class":
        # value is stored dot-joined (e.g. ".btn.btn-primary"); a bare class
        # chain is a weak-but-valid fallback for elements whose other identity
        # rotated between sessions.
        return By.CSS_SELECTOR, candidate["value"]
    if kind == "xpath":
        return By.XPATH, candidate["value"]
    if kind == "link_text":
        # Visible-text match on anchors - survives attribute/class rotation far
        # better than any selector.  normalize-space() forgives the whitespace
        # cleanup the recorder applied to the captured text.
        return By.XPATH, f"//a[normalize-space(.)={_xpath_lit(candidate['value'])}]"
    if kind == "text":
        return By.XPATH, candidate["value"]
    if kind == "role_text":
        return By.XPATH, candidate["value"]
    if kind == "label":
        return By.XPATH, candidate["value"]
    raise ValueError(f"Unknown locator candidate kind: {kind}")


def _candidate_css(candidate: dict[str, Any]):
    """CSS-selector form of a candidate for the shadow-piercing deep query.
    XPath candidates cannot be queried via querySelectorAll - they stay
    top-document-only."""
    kind = candidate["kind"]
    if kind == "id":
        return "#" + str(candidate["value"]).replace('\\', '\\\\').replace('"', '\\"')
    if kind == "data":
        name = candidate.get("name", "")
        return f"[{name}='{_css_attr_val(candidate.get('value', ''))}']"
    if kind == "css":
        return candidate["value"]
    if kind == "class":
        return candidate["value"]
    return None


def _pick_match(matches: list, locator: Optional[Locator], kind: str):
    """Choose the right element when several match one candidate.

    Recordings store the 0-based position of the clicked element among its
    lookalikes (``index`` for sibling lookalikes, ``text_index`` for visible-
    text matches), so repeated elements (menu rows, list items, pagination)
    replay on the SAME instance instead of always the first.
    """
    if len(matches) < 2 or locator is None:
        return matches[0]
    want = locator.text_index if kind in ("link_text", "text", "role_text", "label") else locator.index
    if want is None:
        return matches[0]
    return matches[want % len(matches)]


# The element's own visible text + aria-label, collapsed.  The native ladder
# checks a unique match against the RECORDED identity with this (mirrors
# JS_DOM_POINTER.agrees): a weak/generated candidate that matches one page
# element is often NOT the record.
JS_ELEMENT_TEXT = """
return (function (el) {
  if (!el) return '';
  var t = '';
  try { t = (el.innerText || el.textContent || ''); } catch (e) {}
  var a = '';
  try { a = el.getAttribute('aria-label') || ''; } catch (e) {}
  return (t + ' ' + a).replace(/\\s+/g, ' ').trim().slice(0, 200);
})(arguments[0]);
"""

# Candidates built FROM the recorded text: their matches agree with the record
# by construction, so they are never treated as lookalikes.
_IDENTITY_KINDS = ("text", "link_text", "role_text", "label")


def _record_identity_text(locator: Optional[Locator]) -> str:
    """The element's OWN identity text a native match must agree with, or ''.

    Only attributes that describe the element itself are used (its visible text
    / link text / aria-label).  ``label_text`` is deliberately excluded: a
    form control does not render its own label, so requiring it would reject
    correct matches.
    """
    if locator is None:
        return ""
    for attr in ("text", "link_text", "aria_label"):
        val = getattr(locator, attr, None)
        if val and str(val).strip():
            return " ".join(str(val).split())
    return ""


def _native_match_agrees(driver, element, want: str) -> bool:
    """True when a native match carries the recorded identity text.

    A driver that cannot run the probe (JS bridge down / stub) returns True so
    today's behaviour is kept instead of failing every action.
    """
    if not want:
        return True
    try:
        got = driver.execute_script(JS_ELEMENT_TEXT, element)
    except Exception:
        return True
    if not isinstance(got, str):
        return True
    return want.lower() in " ".join(got.split()).lower()


def _find_by_chain(driver, chain: list[dict[str, Any]], timeout: float,
                   locator: Optional[Locator] = None):
    """Layered-tries resolution: prefer the candidate that PINPOINTS one element.

    A generic candidate (a data-* shared by every column/row, e.g. LinkedIn's
    ``data-component-type=LazyColumn``) can match many elements; returning its
    indexed pick immediately would click the WRONG one when the recorded ordinal
    no longer maps to the same instance.  So the FIRST candidate with exactly
    one match wins; a non-unique candidate is only remembered as a fallback and
    the search continues for an element-specific candidate (label / unique text
    / css / ancestor_css).

    A candidate marked ``positional`` (the recorded structural / absolute XPath)
    never wins that race: it records WHERE the element sat on the RECORDING
    page, not what it is, so a re-rendered or different instance of the same
    form turns it into a lookalike and the action silently misclicks.  Position
    is a last resort - used only when nothing else matched anything.

    When nothing is unique the fallback (the recorded lookalike index) is used;
    when nothing matched at all, the strongest candidate is awaited (dynamic
    pages).
    """
    last_error: Optional[Exception] = None
    fallback = None
    positional_fallback = None
    lookalike = None
    want_text = _record_identity_text(locator)
    for candidate in chain:
        positional = bool(candidate.get("positional"))
        try:
            by, value = _candidate_selector(candidate)
            found = driver.find_elements(by, value)
        except Exception as exc:  # invalid selector etc. - try next candidate
            last_error = exc
            continue
        if not found:
            continue
        if len(found) == 1 and not positional:
            # A unique match is only trusted when it AGREES with the recorded
            # identity.  A generated css chain / an index-based selector
            # matches exactly one page element that is frequently NOT the
            # record - live: a recorded 'Easy Apply' click resolved to a filter
            # label reading 'Employment type', and clicking it is a silent
            # wrong click on the document underneath the popup.  Keep it as a
            # LAST resort and let a candidate that agrees win instead.
            if candidate.get("kind") not in _IDENTITY_KINDS and \
                    not _native_match_agrees(driver, found[0], want_text):
                if lookalike is None:
                    lookalike = found[0]
                logger.debug(
                    "locator hit via %s (unique but NOT the record)",
                    candidate["kind"],
                )
                continue
            logger.debug("locator hit via %s (unique)", candidate["kind"])
            return found[0]
        slot = positional_fallback if positional else fallback
        if slot is None:
            pick = _pick_match(found, locator, candidate["kind"])
            if positional:
                positional_fallback = pick
            else:
                fallback = pick
            logger.debug(
                "locator hit via %s%s (%d match(es), kept as fallback)",
                candidate["kind"], " [positional]" if positional else "",
                len(found),
            )
    if fallback is not None:
        return fallback
    if positional_fallback is not None:
        return positional_fallback
    if lookalike is not None:
        return lookalike
    if not chain:
        raise ElementNotFoundError("empty locator chain")
    try:
        by, value = _candidate_selector(chain[0])
        element = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((by, value))
        )
        if element is not None:
            return element
    except Exception as exc:
        raise ElementNotFoundError(f"locator chain failed: {chain}") from last_error or exc
    raise ElementNotFoundError(f"locator chain failed: {chain}") from last_error


def _fuzzy_chain(event: Event) -> list[dict[str, Any]]:
    """Relaxed candidates derived from a locator when the strict chain missed.

    The desktop recorder's layered tries end in low-confidence, high-tolerance
    matches; the web equivalent is ``contains()``-style attribute and text
    substring searches that survive dynamic id/class suffixes, absolute-vs-
    relative href drift, and whitespace/case differences between sessions.
    Every candidate stays tag-restricted where possible to avoid grabbing a
    different element that merely shares the fragment.
    """
    loc = event.locator
    if loc is None:
        return []
    tag = loc.tag
    out: list[dict[str, Any]] = []

    def tag_guard() -> str:
        return f"[self::{tag} and " if tag else "["

    # Attribute fragments >= 3 chars: exact matches already failed, so match
    # the stable part (placeholder="Search for products" -> contains "Search").
    for name, val in (("placeholder", loc.placeholder), ("aria-label", loc.aria_label),
                      ("name", loc.name), ("title", loc.title), ("alt", loc.alt_text)):
        if val and len(str(val)) >= 3:
            out.append({"kind": "xpath", "value":
                        f"//*{tag_guard()}contains(@{name}, {_xpath_lit(str(val))})]"})
    # href: the recorded absolute URL may carry a different scheme/host on
    # replay - match the stable path suffix instead of the whole value.
    if loc.href and loc.tag == "a":
        path = str(loc.href).split("?", 1)[0].split("#", 1)[0]
        if len(path) >= 5:
            out.append({"kind": "xpath", "value":
                        f"//a[contains(@href, {_xpath_lit(path)})]"})
    # Visible text substrings: partial link text first, then any-element text.
    if loc.link_text and len(loc.link_text) >= 4:
        out.append({"kind": "xpath", "value":
                    f"//a[contains(normalize-space(.), {_xpath_lit(loc.link_text)})]"})
    # Exact normalize-space text match (LinkedIn wait_span_click semantics):
    # stricter than the contains() fragment below, so a dynamic page whose
    # label matches verbatim is found before the looser relaxation.
    if loc.text:
        exact = re.sub(r"\s+", " ", str(loc.text)).strip()
        if 3 <= len(exact) <= 80:
            out.append({"kind": "xpath", "value":
                        f"//{tag or '*'}["
                        f"normalize-space(.)={_xpath_lit(exact)}]"})
    if loc.text and 4 <= len(loc.text) <= 60:
        # Case-insensitive text fragment - the lowest-confidence layer, the web
        # analogue of the desktop recorder's low-confidence region search.
        translate = ("translate(normalize-space(.), "
                     "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')")
        out.append({"kind": "xpath", "value":
                    f"//{tag or '*'}["
                    f"contains({translate}, {_xpath_lit(loc.text.lower())})]"})
    return out


def _find_viewport(driver, event: Event) -> Any:
    """Last-resort coordinate hit-test: scroll the recorded document-space
    center back into the viewport and return elementFromPoint() there."""
    vp = (event.locator.viewport if event.locator else None) or None
    if not vp:
        raise ElementNotFoundError("no viewport geometry recorded")
    try:
        x = int(vp.get("x", 0) or 0) + int(vp.get("w", 0) or 0) // 2
        y = int(vp.get("y", 0) or 0) + int(vp.get("h", 0) or 0) // 2
        vw = int(vp.get("vw", 1280) or 1280)
        vh = int(vp.get("vh", 720) or 720)
        element = driver.execute_script(JS_ELEMENT_FROM_POINT, x, y, vw, vh)
    except Exception as exc:
        raise ElementNotFoundError("viewport hit-test failed") from exc
    if element is None:
        raise ElementNotFoundError("viewport hit-test found nothing at recorded position")
    return element


def _scan_frames(driver, event: Event, timeout: float, include_top: bool = True):
    """Frame-by-index fallback: search the current document and every iframe -
    NESTED and cross-origin frames included.

    Used when the recorded frame_path cannot be entered (the iframe element's
    own locator went stale - iframes rarely carry stable ids) or for
    cross-origin frames that never captured a path.  Frames are entered by
    INDEX (which works across origins) and the search RECURSES into child
    frames, so a target inside a nested/cross-origin iframe resolves.  Inside
    each frame the same layered ladder runs: strict chain, then fuzzy.

    ``include_top`` is False for an event that WAS recorded inside a frame: the
    top/initial document must not be searched, or a lookalike there is acted on
    ("playback happens in the initial frame instead of the opened one").
    """
    chain = event.locator.chain() if event.locator else []
    fuzzy = _fuzzy_chain(event)
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    deadline = time.time() + max(0.5, float(timeout or 0.0))
    try:
        found = _scan_frames_at(driver, event, chain, fuzzy, 0, deadline, include_top)
    finally:
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
    if found is not None:
        return found
    raise ElementNotFoundError(f"locator chain failed in all frames: {chain}")


def _scan_frames_at(driver, event: Event, chain, fuzzy, depth: int, deadline: float,
                    include_top: bool = True):
    """Search the CURRENT frame context, then recurse into its child frames.

    ``driver`` must already be positioned in the context to search.  A short
    per-frame timeout keeps the whole scan inside the caller's deadline so a
    page with many frames cannot stall replay.  With ``include_top=False`` the
    top context (depth 0) is only descended into, never searched.
    """
    if include_top or depth > 0:
        remaining = max(0.2, deadline - time.time())
        for candidates in (chain, fuzzy):
            if not candidates:
                continue
            try:
                return _find_by_chain(
                    driver, candidates, min(1.5, remaining), locator=event.locator
                )
            except ElementNotFoundError:
                continue
            except Exception:
                continue
    if depth >= _FRAME_SCAN_MAX_DEPTH or time.time() >= deadline:
        return None
    try:
        count = len(driver.find_elements(By.TAG_NAME, "iframe"))
    except Exception:
        count = 0
    for index in range(count):
        if time.time() >= deadline:
            return None
        try:
            driver.switch_to.frame(index)
        except Exception:
            continue  # frame not switchable - try the next one
        try:
            found = _scan_frames_at(driver, event, chain, fuzzy, depth + 1, deadline,
                                    include_top)
        except Exception:
            found = None
        try:
            driver.switch_to.parent_frame()
        except Exception:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
        if found is not None:
            return found
    return None


def _is_shadow_frame_event(event: Event) -> bool:
    """True for an event captured inside a shadow-hosted iframe.

    Such a frame is invisible to WebDriver (``find_elements`` cannot pierce a
    shadow root, so ``switch_to.frame`` cannot reach it), so replay must use
    the in-browser, shadow-piercing dispatch instead of element resolution.
    """
    ctx = getattr(event, "context", None) or {}
    return bool(ctx.get("wvp_shadow_frame"))


def _is_shadow_dom_event(event: Event) -> bool:
    """True when the recorded element lives inside a shadow root.

    WebDriver cannot see or return elements inside shadow roots, so native
    resolution can only match a light-DOM lookalike - the "playback keeps
    running in the original frame" symptom.  Such events must use the
    shadow-piercing in-browser dispatch instead.

    Two signals, in order:
    - ``shadow_hosts`` (current recordings): the hosts enclosing the element;
    - a legacy recording (saved before shadow_hosts was persisted): the
      recorded absolute XPath is NOT rooted at ``/html`` when the element walk
      stopped at a shadow boundary, while a light-DOM element is always
      ``/html/...``.
    """
    loc = getattr(event, "locator", None)
    if loc is None:
        return False
    if getattr(loc, "shadow_hosts", None):
        return True
    xp = getattr(loc, "xpath", None)
    return bool(xp) and not str(xp).lstrip().lower().startswith("/html")


def _needs_dom_dispatch(event: Event) -> bool:
    """True when WebDriver cannot reach the target - a shadow root OR a
    shadow-hosted frame - so the in-browser dispatch must be used."""
    return _is_shadow_frame_event(event) or _is_shadow_dom_event(event)


def needs_deep_dispatch(event: Event) -> bool:
    """True when the in-page, shadow-piercing search must resolve the target.

    Extends ``_needs_dom_dispatch`` with a frame path whose frames live inside a
    shadow root: WebDriver's frame tree never exposes a shadow-hosted iframe,
    so a recorded ``frame_path`` can never be entered and native resolution is
    guaranteed to miss.  A condition (or a picker text capture) that skipped
    this routed FALSE / came back empty for a visible target.
    """
    if _needs_dom_dispatch(event):
        return True
    for raw in getattr(event, "frame_path", None) or []:
        loc = Locator.from_dict(raw)
        if loc is not None and getattr(loc, "shadow_hosts", None):
            return True
    return False


def resolve_element(driver, event: Event, timeout: float = 15.0):
    """Resolve the event's locator chain to a WebElement, entering frames first.

    Runs the full layered-tries ladder - strict chain (with lookalike index
    disambiguation), cross-origin iframe scan, then fuzzy contains()-relaxation,
    then the recorded-position hit-test - so every recorded identity layer gets
    a chance before the caller falls back to in-browser JS dispatch.  Frame
    entry is best-effort: a stale frame_path (the iframe element's locator
    rotated) falls through to the index-based frame scan instead of aborting
    the action.
    """
    if not event.locator:
        raise ElementNotFoundError(f"event '{event.type}' has no locator")
    if _needs_dom_dispatch(event):
        # WebDriver cannot reach an element inside a shadow root or a
        # shadow-hosted frame; raise so the caller takes the in-browser,
        # shadow-piercing dispatch instead of matching a light-DOM lookalike.
        raise ElementNotFoundError(
            "event needs the shadow-piercing dispatch (not WebDriver-reachable)"
        )
    chain = event.locator.chain()
    if not chain and not event.locator.viewport:
        # A locator with no selector candidates AND no geometry is unusable;
        # a viewport-only locator (the element had no stable identity) still
        # falls through to the coordinate hit-test below.
        raise ElementNotFoundError(f"event '{event.type}' has an empty locator chain")

    entered_path = False
    if event.frame_path:
        # Enter the recorded same-origin iframe chain.  A failure here (stale
        # iframe locator) must NOT abort the action - the index path / index
        # scan below re-enter frames without needing the recorded path.
        try:
            driver.switch_to.default_content()
            for frame_locator_raw in event.frame_path:
                frame_locator = Locator.from_dict(frame_locator_raw)
                if not frame_locator:
                    raise ElementNotFoundError("frame_path contains an invalid locator")
                frame_element = _find_by_chain(driver, frame_locator.chain(), min(timeout, 5.0))
                driver.switch_to.frame(frame_element)
            entered_path = True
        except ElementNotFoundError:
            entered_path = False
            try:
                driver.switch_to.default_content()
            except Exception:
                pass

    # The recorded index-based frame chain (also covers cross-origin frames
    # the locator frame_path could not describe) is the deterministic fallback
    # when the locator path is absent or failed to enter.
    if not entered_path and event.frame_index_path:
        if _enter_frame_index_path(driver, event.frame_index_path):
            entered_path = True

    has_scope = bool(event.frame_path) or bool(event.frame_index_path)
    try:
        if not has_scope or entered_path:
            if not has_scope:
                # No recorded frame scope = the event ran on the top document.
                # The driver can still be sitting inside the PREVIOUS action's
                # frame (WebDriver keeps the frame context between calls), so
                # reset it or the search would run in the wrong document.
                try:
                    driver.switch_to.default_content()
                except Exception:
                    pass
            return _find_by_chain(driver, chain, timeout, locator=event.locator)
    except ElementNotFoundError:
        pass

    # Cross-origin frames (no recorded path) OR a failed path entry -> enter
    # every frame by index and run the same strict+fuzzy ladder inside each.
    # A frame-scoped event (has_scope) must NOT be matched in the top document:
    # only an unscoped cross-origin pick may search the top/initial frame.
    if event.cross_origin_frame or (has_scope and not entered_path):
        try:
            return _scan_frames(driver, event, timeout, include_top=not has_scope)
        except ElementNotFoundError:
            pass

    # Fuzzy relaxation: dynamic ids/classes/hrefs drift between sessions, the
    # strict chain misses, but a contains() fragment still lands.
    try:
        fuzzy = _fuzzy_chain(event)
        if fuzzy:
            return _find_by_chain(driver, fuzzy, min(timeout, 5.0), locator=event.locator)
    except ElementNotFoundError:
        pass
    # Coordinate last resort: everything selector-based missed.
    try:
        return _find_viewport(driver, event)
    except ElementNotFoundError:
        pass
    raise ElementNotFoundError(f"locator chain failed: {chain}")


def _enter_frame_index_path(driver, index_path) -> bool:
    """Enter a recorded index-based frame chain (top-down), or False.

    Index entry works across origins (a locator path cannot), so it is the
    deterministic way to re-enter an iframe the locator-based ``frame_path``
    could not describe or reach.  Restores default_content on failure so the
    caller is never left in a half-entered context.
    """
    if not index_path:
        return False
    try:
        driver.switch_to.default_content()
    except Exception:
        return False
    for index in index_path:
        try:
            driver.switch_to.frame(int(index))
        except Exception:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
            return False
    return True


def enter_recorded_frame(driver, event: Event, timeout: float = 5.0) -> bool:
    """Enter the event's recorded iframe so a direct find runs in its document.

    Index path first (works across origins), then the locator-based
    ``frame_path``.  Returns True when the driver is positioned inside the
    event's frame; False when the event has no frame scope or no frame could be
    reached (the caller then searches the top document).  On failure the driver
    is restored to ``default_content`` so it is never left half-entered.
    """
    if _enter_frame_index_path(driver, event.frame_index_path):
        return True
    if not event.frame_path:
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        return False
    try:
        driver.switch_to.default_content()
        for frame_locator_raw in event.frame_path:
            frame_locator = Locator.from_dict(frame_locator_raw)
            if not frame_locator:
                raise ElementNotFoundError("frame_path contains an invalid locator")
            frame_element = _find_by_chain(
                driver, frame_locator.chain(), min(timeout, 5.0)
            )
            driver.switch_to.frame(frame_element)
        return True
    except Exception:
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        return False


def switch_to_window_ordinal(driver, ordinal, wait_s: float = 2.0) -> bool:
    """Move the driver to the window at the recorded ordinal (handle index).

    The ordinal is the window's index in ``window_handles`` at capture time
    (0 = opener, 1 = first popup, ...); replay re-derives the same index.  It
    disambiguates a SAME-HOST popup from its opener, which url-host matching
    cannot.  Polls briefly because a popup opens a beat after the click that
    caused it.  Returns True when the driver was moved.
    """
    if ordinal is None:
        return False
    try:
        ordinal = int(ordinal)
    except (TypeError, ValueError):
        return False
    deadline = time.time() + max(0.0, wait_s)
    while True:
        try:
            handles = list(driver.window_handles)
        except Exception:
            return False
        if 0 <= ordinal < len(handles):
            target = handles[ordinal]
            try:
                if target == driver.current_window_handle:
                    return False
                driver.switch_to.window(target)
                return True
            except Exception:
                return False
        if time.time() >= deadline:
            return False
        time.sleep(0.2)


def _scroll_into_view(driver, element) -> None:
    try:
        driver.execute_script(JS_SCROLL_INTO_VIEW, element)
    except Exception:
        pass  # non-fatal: dispatch may still work


# Cap on the native-path presence wait: long enough for a dynamic/hydrated
# page to render the target, short enough that a shadow-DOM target (which
# never resolves natively) still falls through to the JS dispatch promptly.
# Kept at 3s to match the prior single-shot resolve's worst case (no regression
# for shadow-DOM-heavy pages) while adding the visibility poll + scroll retry.
_NATIVE_WAIT_CEILING_S = 3.0
_NATIVE_WAIT_POLL_S = 0.25

# Depth cap for the recursive frame scan: a target inside a nested / cross-origin
# iframe resolves without an exact recorded frame path.
_FRAME_SCAN_MAX_DEPTH = 4


def _element_visibility(driver, element):
    """True/False when the browser can determine visibility, None when it
    cannot (JS bridge down / stub driver).  None is treated as visible by the
    waiter so a driver we cannot probe never stalls replay."""
    try:
        result = driver.execute_script(JS_ELEMENT_VISIBLE, element)
    except Exception:
        return None
    if result is None:
        return None
    return bool(result)


def wait_for_element(driver, event: Event, config, *, wait_timeout: Optional[float] = None):
    """Resolve the event's locator, polling until it is present AND visible.

    Selenium-style wait (the LinkedIn helper set's ``wait_span_click`` +
    ``scroll_to_view``): dynamic pages frequently render the target a beat
    after the preceding action, so a moment-in-time resolve misses it.  Polls
    the full ``resolve_element`` ladder within a bounded window, scrolling the
    element into view and confirming visibility before returning it.  An
    element that is present but never reports visible is returned anyway (some
    elements are reported invisible yet still interactive) - the wait must
    never be stricter than the interaction it guards.
    """
    ceiling = (
        float(getattr(config, "element_timeout", 15.0) or 15.0)
        if wait_timeout is None
        else float(wait_timeout)
    )
    # ponytail: 5s native ceiling; raise it if a site needs a longer hydrate.
    deadline = time.time() + max(0.5, min(ceiling, _NATIVE_WAIT_CEILING_S))
    element = None
    while element is None:
        remaining = max(0.5, deadline - time.time())
        try:
            element = resolve_element(driver, event, min(3.0, remaining))
        except Exception:
            element = None
        if element is not None:
            break
        if time.time() >= deadline:
            raise ElementNotFoundError("locator did not resolve within the wait window")
        time.sleep(_NATIVE_WAIT_POLL_S)

    while True:
        _scroll_into_view(driver, element)
        if _element_visibility(driver, element) is not False:
            return element
        if time.time() >= deadline:
            return element  # present but not reporting visible - act anyway
        time.sleep(_NATIVE_WAIT_POLL_S)


def _try_resolve_element(driver, event: Event, config):
    """Locator-chain resolution for the NATIVE interaction path.

    Runs the same layered ladder as ``resolve_element`` (strict -> frames ->
    fuzzy -> viewport) but with a BOUNDED presence+visibility wait
    (``wait_for_element``): a dynamic page gets a beat to render the target,
    while a shadow-DOM target that never resolves natively still falls through
    to the unified JS dispatch quickly.  Returns None when the locator misses
    so the caller can take the JS path.
    """
    if not event.locator:
        return None
    if _needs_dom_dispatch(event):
        return None  # WebDriver cannot reach it - take the JS dispatch path
    try:
        return wait_for_element(driver, event, config)
    except Exception:
        return None


def _first_css_selector(event: Event) -> Optional[str]:
    """Best CSS selector for an event's locator, for in-browser deep finds.

    The recorded explicit ``css`` candidate is preferred over id/data-derived
    selectors: a bare id can match a shadow HOST sharing that id (e.g.
    ``cr-searchbox-input#input`` on the new-tab page) instead of the real
    element, while the tag-restricted ``input#input`` lands on the actual
    input.  Falls back to any css-capable candidate, then None.
    """
    if not event.locator:
        return None
    for candidate in event.locator.chain():
        if candidate["kind"] == "css":
            return _candidate_css(candidate)
    for candidate in event.locator.chain():
        css = _candidate_css(candidate)
        if css:
            return css
    return None


def _css_selectors(event: Event) -> list[str]:
    """Ordered CSS-capable candidates for the in-browser deep search.

    The in-browser path cannot run XPath, so every candidate is expressed as
    CSS: the recorded explicit ``css`` first (a bare id can match a shadow HOST
    that shares it), then id / data-* (incl. name / placeholder / aria-label /
    type / href) / ancestor_css / class.  Trying the WHOLE list matters because
    the recorded id is frequently volatile (Ember ids like ``ember748`` rotate
    between sessions) while a class / aria-label / data-* still matches.

    A ``css`` anchored on a GENERATED id is the exception: ``button#ember395``
    pins the recording's render instance, and on the next instance of the page
    that id belongs to a different control - so such a css must TRAIL the
    portable candidates instead of leading them, or a web sequence stops being
    replayable on any page but the one it was recorded on.
    """
    if not event.locator:
        return []
    chain = event.locator.chain()
    css = [c for c in chain if c.get("kind") == "css"]
    rest = [c for c in chain if c.get("kind") != "css"]
    ordered = (rest + css) if is_volatile_id(event.locator.id) else (css + rest)
    out: list[str] = []
    seen: set[str] = set()
    for candidate in ordered:
        css = _candidate_css(candidate)
        if css and css not in seen:
            seen.add(css)
            out.append(css)
    return out


def deep_element_rect(driver, event: Event) -> Optional[list[int]]:
    """Device-pixel viewport box [x, y, w, h] of a shadow-root target, or None.

    A region-scoped capture (LLM-node web OCR) needs a box for a target
    WebDriver cannot return; the deep search resolves it in-page and the box is
    reported in device pixels so it lines up with ``driver.save_screenshot``.
    """
    if not event.locator:
        return None
    selectors = _css_selectors(event)
    text = event.locator.text or None
    if not selectors and not text:
        return None
    tag = event.locator.tag or ""
    try:
        rect = driver.execute_script(JS_DEEP_RECT, selectors, text, tag)
    except Exception as exc:
        logger.debug("deep_element_rect failed: %s", exc)
        return None
    if not rect:
        return None
    try:
        return [int(rect["x"]), int(rect["y"]), int(rect["w"]), int(rect["h"])]
    except Exception:
        return None


# Shadow-piercing text read of a target: label first (a form field's portable
# identity), then the recorded selectors, then an exact text match.  WebDriver
# cannot RETURN an element inside a shadow root, so an element-scoped text read
# always came back empty and the condition routed FALSE.  Returns null when no
# recorded candidate matched (the caller keeps polling), "" for an empty target.
JS_DEEP_ELEMENT_TEXT = """return (function (selectors, text, label) {
  var el = __wvpDeepFindLabel(label);
  if (!el) el = __wvpDeepFindAny(selectors, 0);
  if (!el && text) el = __wvpDeepFindText(text);
  if (!el) return null;
  try { return (el.innerText || el.textContent || ''); } catch (e) { return ''; }
})(arguments[0], arguments[1], arguments[2]);
"""


def deep_element_text(driver, event: Event) -> Optional[str]:
    """Shadow-piercing read of an event target's visible text, or None.

    The text counterpart of :func:`deep_element_rect`: a target inside a shadow
    root (or a shadow-hosted frame) is unreachable by WebDriver, so its text is
    read in-page.  None means no recorded candidate matched.
    """
    if not event.locator:
        return None
    selectors = _css_selectors(event)
    text = event.locator.text or None
    label = event.locator.label_text or None
    if not selectors and not text and not label:
        return None
    try:
        return driver.execute_script(
            JS_DEEP_SEARCH + JS_DEEP_ELEMENT_TEXT, selectors, text, label)
    except Exception as exc:
        logger.debug("deep_element_text failed: %s", exc)
        return None


# The in-page dispatch acts ONLY on a target that agrees with the recorded
# identity (see JS_DOM_POINTER), so a wizard step whose DOM has not painted yet
# would otherwise be missed outright.  Wait briefly for it: 0 disables the wait.
_DOM_DISPATCH_WAIT_S = 2.5
_DOM_DISPATCH_POLL_S = 0.25


def _dispatch_dom_pointer(driver, event: Event, config, button: str, mode: str,
                          timeout: float = _DOM_DISPATCH_WAIT_S) -> bool:
    """Resolve and dispatch a pointer action entirely inside the browser.

    Element-based only: every recorded candidate is deep-searched across every
    shadow root and same-origin iframe (with a text match as the last resort),
    and the events are dispatched on the element itself - viewport coordinates
    are never used (they are only valid for the recording window size/scroll
    and would hit a different element on replay).

    The target must AGREE with the record, so this never dispatches on a
    lookalike; a target that is merely LATE is waited for up to ``timeout``.
    Returns True when a target was found and events were dispatched.
    """
    selectors = _css_selectors(event)
    text = (event.locator.text if event.locator else None) or None
    # The field's label is passed SEPARATELY: it is not a CSS selector, and it is
    # the only identity a form field keeps across instances (the recorded id
    # may embed per-instance tokens), so the in-page dispatch resolves it first.
    label = (event.locator.label_text if event.locator else None) or None
    if not selectors and not text and not label:
        return False
    # Carry the recorded ordinal so the shadow-piercing fallback clicks the
    # SAME match the native ladder would (repeating-element cursor).
    index = getattr(event.locator, "index", None) if event.locator else None
    deadline = time.time() + max(0.0, timeout)
    while True:
        try:
            if driver.execute_script(
                JS_DOM_POINTER, selectors, button, mode,
                getattr(event, "modifiers", None) or [], index, text, label,
            ):
                return True
        except Exception:
            return False
        if time.time() >= deadline:
            return False
        time.sleep(_DOM_DISPATCH_POLL_S)


def _click_with_modifiers(driver, element, modifiers, kind: str = "click") -> None:
    """Native click/dblclick/contextmenu on *element*, holding any recorded
    modifiers (Ctrl+click / Shift+click multi-select macros)."""
    chain = ActionChains(driver)
    mods = [MOD_KEYS[m] for m in (modifiers or []) if m in MOD_KEYS]
    for mod in mods:
        chain = chain.key_down(mod)
    chain = chain.move_to_element(element)
    if kind == "dblclick":
        chain = chain.double_click()
    elif kind == "contextmenu":
        chain = chain.context_click()
    else:
        chain = chain.click()
    for mod in reversed(mods):
        chain = chain.key_up(mod)
    chain.perform()


def _climb_clickable(driver, element):
    """Nearest clickable ANCESTOR (button / a / submit input / role=button) of
    an element, or None.

    The recorder captures the DEEPEST element under the cursor - for a button
    that is usually an inert inner span/div with no box of its own (Google's
    Material buttons are span stacks; ``element not interactable: has no size
    and location``).  After a trusted click on that inert element fails, the
    clickable ancestor is retried with a trusted click - which also runs
    default actions (form submit, navigation) that synthetic JS events never
    trigger.  Starts at the PARENT so a directly-recorded button is never
    re-clicked pointlessly.
    """
    if element is None:
        return None
    try:
        return driver.execute_script(
            "return (function (el) {"
            "  var n = el ? el.parentElement : null;"
            "  while (n && n.nodeType === 1) {"
            "    var tag = (n.tagName || '').toLowerCase();"
            "    if (tag === 'button' || tag === 'a') return n;"
            "    if (tag === 'input') {"
            "      var ty = ((n.getAttribute && n.getAttribute('type')) || '')"
            "        .toLowerCase();"
            "      if (ty === 'submit' || ty === 'button') return n;"
            "    }"
            "    if (n.getAttribute && n.getAttribute('role') === 'button') return n;"
            "    n = n.parentElement;"
            "  }"
            "  return null;"
            "})(arguments[0])",
            element,
        ) or None
    except Exception:
        return None


def _focused_editable(driver) -> bool:
    """True when an editable element (input/textarea/contenteditable) has page
    focus right now, pierced through open shadow roots.

    Typing and keys replay at the element focused at replay time (desktop-
    sequence semantics), so a recorded type whose locator went stale still
    writes when the preceding Tab/click left focus in a writable field - the
    hover/click that preceded the typing did the focusing, so their own
    failure must not cancel the writing.
    """
    try:
        return bool(driver.execute_script(
            "return (function () {"
            "  var ae = document.activeElement;"
            "  var guard = 0;"
            "  while (ae && ae.shadowRoot && ae.shadowRoot.activeElement && guard++ < 8)"
            "    ae = ae.shadowRoot.activeElement;"
            "  if (!ae || ae.nodeType !== 1) return false;"
            "  var t = (ae.tagName || '').toLowerCase();"
            "  return t === 'input' || t === 'textarea' || ae.isContentEditable;"
            "})()"
        ))
    except Exception:
        return False


def type_into_focused(driver, text) -> bool:
    """Type ``text`` into whatever editable the browser has focused.

    Web-mode twin of the desktop LLM node's auto-write: the driver's focused
    input/textarea/contenteditable receives the text and nothing else is
    touched.  Returns False when no editable is focused (nothing is written).

    Bare trusted keys go through ActionChains - the same "native-focus" path a
    web sequence uses when its recorded type target is gone - so shadow-root
    fields and hydrated inputs are reached without a locator.  A JS insertion
    into the deepest active editable is the fallback.
    """
    if not text:
        return False
    if not _focused_editable(driver):
        return False
    try:
        ActionChains(driver).send_keys(str(text)).perform()
        return True
    except Exception:
        logger.debug("type_into_focused: native send_keys failed", exc_info=True)
        try:
            # JS_TYPE_DOM with no recorded candidates falls back to the focused
            # editable (deepest active element, pierced through shadow roots).
            return bool(driver.execute_script(JS_TYPE_DOM, [], str(text), None))
        except Exception:
            return False


def _dispatch_dom_type(driver, event: Event, config) -> bool:
    """Shadow-piercing typing fallback: resolve and insert inside the browser.

    WebDriver find_elements cannot see elements inside shadow roots, so the
    recorded LABEL (the field's portable identity) is resolved first, then the
    recorded candidates are deep-searched across every shadow root and
    same-origin iframe (a text match as the last resort); only when the target
    is gone does JS_TYPE_DOM fall back to the focused editable.
    """
    selectors = _css_selectors(event)
    text = (event.locator.text if event.locator else None) or None
    label = (event.locator.label_text if event.locator else None) or None
    try:
        return bool(driver.execute_script(
            JS_TYPE_DOM, selectors, event.value, text, label
        ))
    except Exception:
        return False


def release_all_keys(driver) -> None:
    """Release every native modifier; safe to call after aborted replays.

    Mirrors the desktop player's release_all_keys guard so an interrupted
    native chord can never leave Ctrl/Alt/Shift/Meta stuck in the browser.
    """
    if not _SELENIUM_AVAILABLE:
        return
    try:
        chain = ActionChains(driver)
        for key in (Keys.CONTROL, Keys.ALT, Keys.SHIFT, Keys.META):
            chain = chain.key_up(key)
        chain.perform()
    except Exception:
        logger.debug("release_all_keys failed (ignored)", exc_info=True)


# --- historic function API (compat facade) -------------------------------
# The dispatcher implementations live in the handlers package; these aliases
# keep engine/NGUI/test imports of player.web.actions.do_* working unchanged.
# ponytail: facade - delete when call sites migrate to handlers.HANDLERS.
from .handlers import HANDLERS  # noqa: E402

do_navigate = HANDLERS["navigate"]._execute
do_click = HANDLERS["click"]._execute
do_dblclick = HANDLERS["dblclick"]._execute
do_contextmenu = HANDLERS["contextmenu"]._execute
do_hover = HANDLERS["hover"]._execute
do_drag = HANDLERS["drag"]._execute
do_focus = HANDLERS["focus"]._execute
do_type = HANDLERS["type"]._execute
do_key = HANDLERS["key"]._execute
do_chrome = HANDLERS["chrome"]._execute
do_scroll = HANDLERS["scroll"]._execute
do_select = HANDLERS["select"]._execute
do_submit = HANDLERS["submit"]._execute
