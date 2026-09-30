/**
 * exec_overlay_idempotent_harness.js - proves exec_overlay.js is idempotent per
 * document.
 *
 * The Python side (player/web/exec_overlay.py) evaluates the payload into the
 * CURRENT document on every enable() - engine.run() calls it once per replay
 * pass - and ALSO registers it through CDP for every future document.  Without
 * the `window.__wvpExec` guard each evaluation appends ANOTHER full-screen
 * canvas and ANOTHER corner banner at the same fixed position and stacks them,
 * which is what made the playback overlay look like it never cleared.
 *
 * `requestAnimationFrame` is a no-op here, so `draw()` never runs and no 2D
 * canvas context is needed: the test only counts appended nodes.
 *
 * Exits 0 with a JSON summary on success, non-zero on failure.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

// Optional argv[2] points at another copy of the payload, so the harness can be
// run against a deliberately unguarded build to prove it discriminates.
const FILE = process.argv[2] || path.join(__dirname, '..', '..', 'player', 'web',
                                          'inject', 'exec_overlay.js');
const payload = fs.readFileSync(FILE, 'utf8');

const appended = [];
const root = {
  appendChild(node) {
    node.isConnected = true;
    node.parentNode = this;
    appended.push(node);
    return node;
  },
  removeChild(node) {
    node.isConnected = false;
    node.parentNode = null;
    return node;
  },
};
function makeEl(tag) {
  return {
    tagName: tag, style: {}, textContent: '', parentNode: null,
    isConnected: false, setAttribute() {}, getContext() { return null; },
  };
}

global.window = {
  devicePixelRatio: 1, innerWidth: 1280, innerHeight: 800,
  addEventListener() {}, requestAnimationFrame() { return 0; },
};
global.document = {
  documentElement: root, readyState: 'complete',
  addEventListener() {}, createElement: makeEl,
};
global.sessionStorage = { getItem: () => 'on', setItem() {}, removeItem() {} };
global.MutationObserver = function () { this.observe = () => {}; };
global.cancelAnimationFrame = () => {};

// Evaluate exactly the way the Python side does: twice in the SAME document.
vm.runInThisContext(payload, { filename: 'exec_overlay.js' });
const afterFirst = appended.length;
vm.runInThisContext(payload, { filename: 'exec_overlay.js' });
const afterSecond = appended.length;

const firstOk = afterFirst === 2;              // one canvas + one banner
const secondOk = afterSecond === afterFirst;   // a re-evaluation adds nothing
const apiOk = typeof window.__wvpExecMark === 'function'
  && typeof window.__wvpExecOn === 'function'
  && typeof window.__wvpExecOff === 'function';

// The API must stay reachable after a re-evaluation (enable() calls it every time).
global.sessionStorage.setItem('looper.web.execOverlay', 'on');
let onOk = true;
try { window.__wvpExecOn(); } catch (e) { onOk = false; }

const ok = firstOk && secondOk && apiOk && onOk;
console.log(JSON.stringify({ afterFirst, afterSecond, apiOk, onOk, ok }));
process.exit(ok ? 0 : 1);
