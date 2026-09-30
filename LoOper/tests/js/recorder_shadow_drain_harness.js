/**
 * recorder_shadow_drain_harness.js - drives recorder.js's __wvpDrainShadow under
 * node.
 *
 * WebDriver's find_elements cannot see an <iframe> that lives inside a shadow
 * root, so the Python drain (which walks frames via WebDriver) never reads that
 * frame's events.  __wvpDrainShadow closes the gap by walking shadow roots and
 * same-origin iframe documents in-page.  This harness asserts:
 *   1. a SAME-ORIGIN iframe inside a shadow root has its buffer collected and
 *      tagged context.wvp_shadow_frame (and the buffer is spliced, not copied);
 *   2. a LIGHT-DOM iframe's buffer is NOT collected (the Python drain owns it,
 *      so collecting here would double-record).
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

function fakeDoc(nodeType) {
  const doc = {
    nodeType: nodeType,
    body: null,
    documentElement: null,
    title: 'Test',
    addEventListener: addL,
    createElement: () => ({ setAttribute() {}, style: {}, appendChild() {}, parentNode: null }),
    elementFromPoint: () => null,
    querySelectorAll: () => [],
    getRootNode: function () { return this; },
  };
  return doc;
}

const documentStub = fakeDoc(9);
documentStub.body = { nodeType: 1, tagName: 'BODY' };
documentStub.documentElement = { nodeType: 1, tagName: 'HTML', appendChild() {} };

const windowStub = {
  self: null,
  top: null,
  addEventListener: addL,
  document: documentStub,
  sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  __wvpLoc: {
    now: () => 1,
    locatorFor: () => ({ tag: 'div', css: null }),
    framePathFromTop: () => ({ path: [], crossOrigin: false }),
  },
};
windowStub.self = windowStub;
windowStub.top = windowStub;

global.window = windowStub;
global.document = documentStub;
global.location = { href: 'https://example.com/' };
global.sessionStorage = windowStub.sessionStorage;
global.setInterval = () => 0;
global.setTimeout = () => 0;
global.clearTimeout = () => {};

const src = fs.readFileSync(
  path.join(__dirname, '..', '..', 'player', 'web', 'inject', 'recorder.js'),
  'utf8'
);
require('vm').runInThisContext(src, { filename: 'recorder.js' });

// --- tree: a shadow host wrapping a same-origin iframe, plus a light-DOM iframe.
const shadowRoot = fakeDoc(11); // ShadowRoot == DocumentFragment (nodeType 11)
const shadowIframe = { nodeType: 1, tagName: 'IFRAME' };
const shadowFrameDoc = fakeDoc(9);
shadowIframe.contentDocument = shadowFrameDoc;
const shadowEvt = { type: 'click', ts: 5, context: {} };
shadowIframe.contentWindow = { __webversionpw_events: [shadowEvt] };

const shadowHost = {
  nodeType: 1,
  tagName: 'DIV',
  shadowRoot: shadowRoot,
  getRootNode: function () { return documentStub; },
};
shadowRoot.querySelectorAll = (sel) => (sel === 'iframe' ? [shadowIframe] : []);

const lightIframe = { nodeType: 1, tagName: 'IFRAME' };
const lightFrameDoc = fakeDoc(9);
lightIframe.contentDocument = lightFrameDoc;
const lightEvt = { type: 'click', ts: 6, context: {} };
lightIframe.contentWindow = { __webversionpw_events: [lightEvt] };

documentStub.querySelectorAll = (sel) => {
  if (sel === 'iframe') return [lightIframe];
  if (sel === '*') return [shadowHost];
  return [];
};

const drained = windowStub.__wvpDrainShadow();
const drainedShadow = drained.filter((e) => e.ts === 5);
const drainedLight = drained.filter((e) => e.ts === 6);

const shadowTagged = !!(
  drainedShadow[0] && drainedShadow[0].context && drainedShadow[0].context.wvp_shadow_frame
);
const ok =
  drainedShadow.length === 1 &&
  shadowTagged &&
  drainedLight.length === 0 &&
  shadowIframe.contentWindow.__webversionpw_events.length === 0 && // spliced
  lightIframe.contentWindow.__webversionpw_events.length === 1; // untouched

console.log(JSON.stringify({
  shadowCollected: drainedShadow.length,
  shadowTagged,
  lightCollected: drainedLight.length,
  shadowBufferLeft: shadowIframe.contentWindow.__webversionpw_events.length,
  lightBufferLeft: lightIframe.contentWindow.__webversionpw_events.length,
  ok,
}));
process.exit(ok ? 0 : 1);
