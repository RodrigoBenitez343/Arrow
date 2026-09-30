/**
 * recorder_overlay_lifecycle_harness.js - drives recorder.js's recording
 * overlay through the lifecycle that used to lose it.
 *
 * The recorder is injected by CDP at DOCUMENT CREATION, before <html> exists,
 * so document.documentElement is null at init - an append there throws and the
 * overlay was never retried (it showed on the first, fully loaded document and
 * never again after any navigation).  This harness asserts:
 *   1. init with NO documentElement does not throw and creates nothing;
 *   2. once the root appears (DOMContentLoaded) the overlay + cheat sheet are
 *      built;
 *   3. if the page drops the overlay, the next DOM change / pointer move
 *      rebuilds it;
 *   4. no overlay is built while the session flag is off (after stop).
 *
 * Exits 0 with a JSON summary on success, non-zero on failure.
 */
'use strict';
const fs = require('fs');
const path = require('path');

const listeners = {};
function addL(type, fn) {
  (listeners[type] = listeners[type] || []).push(fn);
}
function fire(type, ev) {
  (listeners[type] || []).forEach((fn) => fn(ev || {}));
}

// A node whose isConnected reflects whether it is currently in a root.
function makeNode(tag) {
  const node = {
    nodeType: 1,
    tagName: String(tag).toUpperCase(),
    style: {},
    textContent: '',
    parentNode: null,
    isConnected: false,
    attrs: {},
    setAttribute(n, v) { this.attrs[n] = v; },
    getAttribute(n) { return this.attrs[n] != null ? this.attrs[n] : null; },
  };
  return node;
}

const flag = { value: 'on' };
const htmlEl = makeNode('html');
const appended = [];
htmlEl.appendChild = function (child) {
  child.parentNode = this;
  child.isConnected = true;
  appended.push(child);
};

const documentStub = {
  body: makeNode('body'),
  documentElement: null, // injected at document creation - <html> not parsed yet
  title: 'Test',
  readyState: 'loading',
  activeElement: null,
  addEventListener: addL,
  createElement: makeNode,
  elementFromPoint: () => null,
};

const windowStub = {
  self: null,
  top: null,
  addEventListener: addL,
  document: documentStub,
  sessionStorage: {
    getItem: (k) => (k === 'looper.web.overlay' ? flag.value : null),
    setItem() {},
    removeItem() {},
  },
  innerWidth: 1440,
  innerHeight: 773,
  __wvpLoc: {
    now: () => 1000,
    locatorFor: () => ({ tag: 'div', css: null }),
    framePathFromTop: () => ({ path: [], crossOrigin: false }),
  },
};
windowStub.self = windowStub;
windowStub.top = windowStub;

let observerCallback = null;
class MutationObserverStub {
  constructor(cb) { observerCallback = cb; }
  observe() {}
}
let observerCount = 0;

global.window = windowStub;
global.document = documentStub;
global.location = { href: 'https://example.com/' };
global.sessionStorage = windowStub.sessionStorage;
global.MutationObserver = function (cb) { observerCount += 1; return new MutationObserverStub(cb); };
global.setInterval = () => 0;
global.setTimeout = () => 0;
global.clearTimeout = () => {};

const src = fs.readFileSync(
  path.join(__dirname, '..', '..', 'player', 'web', 'inject', 'recorder.js'),
  'utf8'
);
require('vm').runInThisContext(src, { filename: 'recorder.js' });

// 1) No root at init -> nothing built, no throw.
const afterInit = appended.length;

// 2) Root appears -> DOMContentLoaded rebuilds the overlay.
documentStub.documentElement = htmlEl;
documentStub.readyState = 'interactive';
fire('DOMContentLoaded');
const afterReady = appended.slice();
const box = afterReady.find((n) => n.attrs['data-wvp-overlay'] === '1');
const cheat = afterReady.find((n) => /RECORDING/.test(n.textContent || ''));
const readyCount = appended.length;  // box + label + cheat sheet
const readyOk = afterInit === 0 && !!box && !!cheat && readyCount === 3;

// 3) The page drops the overlay (framework re-render) -> a DOM change rebuilds
//    it.  Simulate by detaching everything the harness appended.
appended.forEach((n) => { n.isConnected = false; });
htmlEl.appendChild = function (child) {
  child.parentNode = this;
  child.isConnected = true;
  appended.push(child);
};
observerCallback([], {});
const rebuilt = appended.filter((n) => n.isConnected);
const rebuildOk = rebuilt.length === 3;

// 4) After stop (flag off) nothing is rebuilt.
appended.forEach((n) => { n.isConnected = false; });
const beforeOff = appended.length;
flag.value = 'off';
fire('pointermove', { clientX: 5, clientY: 5 });
const offOk = appended.length === beforeOff;

const ok = readyOk && rebuildOk && offOk && observerCount >= 1;
console.log(JSON.stringify({
  afterInit,
  afterReady: readyCount,
  hasBox: !!box,
  hasCheat: !!cheat,
  rebuildOk,
  offOk,
  observers: observerCount,
  ok,
}));
process.exit(ok ? 0 : 1);
