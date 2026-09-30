/**
 * foreground_topmost_harness.js - drives actions.JS_FOREGROUND's __wvpTopmost
 * under node.
 *
 * Form filling is foreground-only: a field covered by a modal / overlay is
 * never enumerated or filled.  The check is a hit-test, and ``Node.contains``
 * does NOT cross shadow boundaries - so a shadow HOST whose shadow content is
 * hit at its centre looked "covered" by its own content, and the whole shadow
 * subtree (every field of a LinkedIn-style form) was silently skipped.  The
 * snippet is extracted from actions.py (single source of truth) with a minimal
 * fake DOM.  Exits 0 with a JSON summary on success, non-zero on failure.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const py = fs.readFileSync(
  path.join(__dirname, '..', '..', 'player', 'web', 'actions.py'),
  'utf8'
);
const m = py.match(/JS_FOREGROUND = """([\s\S]*?)"""/);
if (!m) {
  console.error('JS_FOREGROUND not found in actions.py');
  process.exit(2);
}
const foreground = m[1].trim();

const ON = { left: 10, top: 10, right: 110, bottom: 30, width: 100, height: 20 };
const OFF = { left: 2000, top: 2000, right: 2100, bottom: 2020, width: 100, height: 20 };

function node(tag, props, rect) {
  const el = Object.assign({ nodeType: 1, tagName: tag.toUpperCase() }, props || {});
  el.getBoundingClientRect = () => rect || ON;
  return el;
}

// ---- shadow-hosted form: host -> shadow root -> div -> input -------------
let hitTarget = null;
const doc = {
  nodeType: 9,
  defaultView: { innerWidth: 1280, innerHeight: 800 },
  elementFromPoint: () => hitTarget,
};
const field = node('input', { id: 'first-name' });
const innerDiv = node('div');
const shadowRoot = {
  nodeType: 11,
  host: null, // set below
  elementFromPoint: () => field,
};
const host = node('div', { id: 'interop-outlet', shadowRoot });
field.parentElement = innerDiv;
innerDiv.parentElement = null;
field.getRootNode = () => shadowRoot;
innerDiv.getRootNode = () => shadowRoot;
host.getRootNode = () => doc;
host.parentElement = null;
shadowRoot.host = host;

// A genuinely covering element (an unrelated overlay, no shadow root).
const overlay = node('div', { id: 'overlay' });
overlay.parentElement = null;
overlay.getRootNode = () => doc;

global.window = {
  getComputedStyle: (el) => el._style || {},
};
global.document = doc;
vm.runInThisContext(
  foreground +
    '; global.__wvpTopmost = __wvpTopmost;' +
    ' global.__wvpHostEnterable = __wvpHostEnterable;',
  { filename: 'foreground.js' }
);

// The host whose shadow content is hit at its centre is on top (NOT covered).
hitTarget = host;
const hostOk = global.__wvpTopmost(host) === true;
// A field inside the shadow root, with itself as the hit target, is on top.
hitTarget = host; // document.elementFromPoint -> host, pierced to the field
const fieldOk = global.__wvpTopmost(field) === true;
// An unrelated element covering the field still rejects it (foreground only).
hitTarget = overlay;
const coveredOk = global.__wvpTopmost(field) === false;
// Scrolled out of view cannot be hit-tested and is NOT "covered".
hitTarget = overlay;
const offscreenOk = global.__wvpTopmost(node('input', { id: 'below-fold' }, OFF)) === true;

// ---- host gate: a shadow host is entered when NOT HIDDEN, box NOT required --
// LinkedIn's interop-outlet is position:absolute with a ZERO-height box
// (1360x0) whose shadow content overflows it: a box test rejected the whole
// subtree and the Easy Apply form enumerated as 0 fields.
const zeroHost = node('div', { id: 'interop-outlet' }, {
  left: 0, top: 641, right: 1360, bottom: 641, width: 1360, height: 0,
});
const zeroBoxOk = global.__wvpHostEnterable(zeroHost) === true;
// A display:none outlet is NOT enterable.
const noneBoxOk = global.__wvpHostEnterable(
  node('div', { _style: { display: 'none' } })) === false;
// An aria-hidden / inert subtree is NOT enterable.
const ariaOk = global.__wvpHostEnterable(node('div', {
  getAttribute: (n) => (n === 'aria-hidden' ? 'true' : null),
})) === false;

const ok = hostOk && fieldOk && coveredOk && offscreenOk &&
  zeroBoxOk && noneBoxOk && ariaOk;
console.log(JSON.stringify(
  { hostOk, fieldOk, coveredOk, offscreenOk, zeroBoxOk, noneBoxOk, ariaOk, ok }));
process.exit(ok ? 0 : 1);
