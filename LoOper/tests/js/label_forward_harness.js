/**
 * label_forward_harness.js - drives recorder.js label-forwarding under node.
 *
 * Clicking a <label> makes the browser forward a synthetic click AND a focusin
 * to the control it labels.  Those are ONE physical press.  The recorder must
 * record a single action, else replay toggles a checkbox/radio twice (a net
 * no-op) - the reported jobs.json defect (3 clicks recorded as 6 actions).
 *
 * Scenarios:
 *   1. click a for= label  -> label click recorded once; the forwarded control
 *      focus + click are dropped;
 *   2. click a wrapping label (control is a descendant) -> same collapse;
 *   3. click the control directly -> one click, NOT armed as a label (kept);
 *   4. the guard does not leak: a later, unrelated focus/click on another
 *      control is still recorded.
 *
 * Exits 0 with a JSON summary on success, non-zero on failure.
 */
'use strict';
const fs = require('fs');
const path = require('path');

const listeners = {};
function addL(type, fn) { (listeners[type] = listeners[type] || []).push(fn); }

const byId = {};

const bodyEl = { nodeType: 1, tagName: 'BODY' };
const htmlEl = { nodeType: 1, tagName: 'HTML', appendChild() {} };

const documentStub = {
  body: bodyEl,
  documentElement: htmlEl,
  title: 'Test',
  activeElement: null,
  addEventListener: addL,
  createElement: () => ({ setAttribute() {}, style: {}, appendChild() {}, parentNode: null }),
  elementFromPoint: () => null,
  getElementById: (id) => byId[id] || null,
  querySelectorAll: () => [],
};

const windowStub = {
  self: null,
  top: null,
  addEventListener: addL,
  document: documentStub,
  sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  innerWidth: 1440,
  innerHeight: 773,
  __wvpLoc: {
    now: () => 1000,
    locatorFor: (el) => ({ tag: ((el && el.tagName) || 'div').toLowerCase() }),
    framePathFromTop: () => ({ path: [], crossOrigin: false }),
  },
};
windowStub.self = windowStub;
windowStub.top = windowStub;

global.window = windowStub;
global.document = documentStub;
global.location = { href: 'https://www.linkedin.com/jobs/' };
global.sessionStorage = windowStub.sessionStorage;
global.setInterval = () => 0;
global.setTimeout = () => 0;
global.clearTimeout = () => {};

const src = fs.readFileSync(
  path.join(__dirname, '..', '..', 'player', 'web', 'inject', 'recorder.js'),
  'utf8'
);
require('vm').runInThisContext(src, { filename: 'recorder.js' });

function fire(type, ev) { (listeners[type] || []).forEach((fn) => fn(ev)); }
function events() { return windowStub.__webversionpw_events || []; }
// The recorder aliases its closure buffer to window.__webversionpw_events, so
// clear it IN PLACE - reassigning the property would decouple the two.
function resetBuffer() { windowStub.__webversionpw_events.splice(0); }

function makeEl(tag, attrs, parent) {
  const el = {
    nodeType: 1,
    tagName: tag.toUpperCase(),
    id: (attrs && attrs.id) || '',
    children: [],
    parentElement: parent || null,
    getAttributeNames: () => Object.keys(attrs || {}),
    getAttribute: (n) => (attrs || {})[n] || null,
  };
  el.querySelectorAll = () => [];
  // querySelector used by the recorder's wrapping-label lookup: return the
  // first labelable descendant.
  el.querySelector = (sel) => {
    const want = String(sel).split(',');
    for (const c of el.children) {
      if (want.indexOf((c.tagName || '').toLowerCase()) >= 0) return c;
    }
    return null;
  };
  if (attrs && attrs.id) byId[attrs.id] = el;
  if (parent) (parent.children = parent.children || []).push(el);
  return el;
}

// 1) for= label + its checkbox control -------------------------------------
resetBuffer();
const ctlFor = makeEl('input', { id: 'cb1', type: 'checkbox' });
const labelFor = makeEl('label', { for: 'cb1' });
// One physical click on the label: the browser then focuses + clicks cb1.
fire('click', { target: labelFor, clientX: 100, clientY: 100, button: 0 });
fire('focusin', { target: ctlFor });
fire('click', { target: ctlFor, clientX: 100, clientY: 100, button: 0 });
const forEvents = events();
const forClicks = forEvents.filter((e) => e.type === 'click');
const forFocuses = forEvents.filter((e) => e.type === 'focus');
const htmlForOk = forClicks.length === 1 && forFocuses.length === 0 &&
  forClicks[0].locator && forClicks[0].locator.tag === 'label';

// 2) wrapping label (control is a descendant) ------------------------------
resetBuffer();
const ctlWrap = makeEl('input', { id: 'cb2', type: 'checkbox' });
const labelWrap = makeEl('label', {}, null);
ctlWrap.parentElement = labelWrap;
labelWrap.children.push(ctlWrap);
fire('click', { target: labelWrap, clientX: 120, clientY: 120, button: 0 });
fire('focusin', { target: ctlWrap });
fire('click', { target: ctlWrap, clientX: 120, clientY: 120, button: 0 });
const wrapEvents = events();
const wrapClicks = wrapEvents.filter((e) => e.type === 'click');
const wrapFocuses = wrapEvents.filter((e) => e.type === 'focus');
const htmlWrapOk = wrapClicks.length === 1 && wrapFocuses.length === 0;

// 3) direct click on the control itself -> one action, never dropped -------
resetBuffer();
const ctlSolo = makeEl('input', { id: 'cb3', type: 'checkbox' });
fire('focusin', { target: ctlSolo });   // focusing the box by clicking it
fire('click', { target: ctlSolo, clientX: 140, clientY: 140, button: 0 });
const soloEvents = events();
const soloClicks = soloEvents.filter((e) => e.type === 'click');
const htmlDirectClickOk = soloClicks.length === 1;

// 4) the guard does not leak onto a later, unrelated control ---------------
resetBuffer();
const otherLabel = makeEl('label', { for: 'cb9' });
makeEl('input', { id: 'cb9', type: 'checkbox' });
const laterCtl = makeEl('input', { id: 'cbX', type: 'checkbox' });
fire('click', { target: otherLabel, clientX: 160, clientY: 160, button: 0 });
fire('focusin', { target: byId.cb9 });
fire('click', { target: byId.cb9, clientX: 160, clientY: 160, button: 0 });
// Much later, the user really clicks a DIFFERENT checkbox: must be recorded.
fire('click', { target: laterCtl, clientX: 200, clientY: 200, button: 0 });
const leakEvents = events();
const leakClicks = leakEvents.filter((e) => e.type === 'click');
const noLeakOk = leakClicks.length === 2;  // the label press + the real later one

const ok = htmlForOk && htmlWrapOk && htmlDirectClickOk && noLeakOk;

console.log(JSON.stringify({
  forClicks: forClicks.length,
  forFocuses: forFocuses.length,
  wrapClicks: wrapClicks.length,
  wrapFocuses: wrapFocuses.length,
  soloClicks: soloClicks.length,
  leakClicks: leakClicks.length,
  ok,
}));
process.exit(ok ? 0 : 1);
