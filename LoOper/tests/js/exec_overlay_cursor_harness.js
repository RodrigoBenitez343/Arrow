/**
 * exec_overlay_cursor_harness.js - drives exec_overlay.js's virtual cursor under node.
 *
 * The in-page execution overlay owns the pointer during a web replay: it must
 * WALK a cursor to each action's target (so a web sequence reads like a desktop
 * one) and report that walk's duration, so the action waits behind the cursor
 * instead of firing before it arrives.  Nothing here may touch an OS pointer.
 *
 * Minimal DOM/canvas/RAF stub so the overlay runs headless, then:
 *   1. a mark returns a duration inside the glide bounds (180..550 ms);
 *   2. stepping the frames walks the cursor onto the target's CENTER;
 *   3. the walked path drops trail points (the line follows the cursor);
 *   4. with the overlay gate OFF, a mark parks the cursor (no walk, duration 0).
 *
 * Exits 0 with a JSON summary on success, non-zero on failure.
 */
'use strict';
const fs = require('fs');
const path = require('path');

let clock = 1000;
const listeners = {};
const rafQueue = [];

function addL(type, fn) {
  (listeners[type] = listeners[type] || []).push(fn);
}

// ---- canvas 2d stub: keeps every coordinate it was asked to draw -------------
const ops = [];
function ctxStub() {
  const rec = (name) => (...args) => ops.push([name].concat(args));
  return {
    setTransform: () => {},
    clearRect: () => {},
    beginPath: () => {},
    closePath: () => {},
    moveTo: rec('moveTo'),
    lineTo: rec('lineTo'),
    arc: rec('arc'),
    stroke: () => ops.push(['stroke']),
    fill: () => ops.push(['fill']),
    fillRect: rec('fillRect'),
    fillText: rec('fillText'),
    strokeRect: rec('strokeRect'),
    measureText: () => ({ width: 10 }),
    save: () => {},
    restore: () => {},
  };
}

const documentStub = {
  documentElement: {
    nodeType: 1,
    tagName: 'HTML',
    isConnected: true,
    appendChild(child) { child.isConnected = true; child.parentNode = this; },
  },
  readyState: 'complete',
  addEventListener: addL,
  createElement(tag) {
    if (tag === 'canvas') {
      return {
        tagName: 'CANVAS',
        isConnected: false,
        parentNode: null,
        width: 0,
        height: 0,
        style: {},
        setAttribute() {},
        getContext: () => ctxStub(),
      };
    }
    return { isConnected: false, parentNode: null, style: {}, setAttribute() {},
             textContent: '' };
  },
};

const store = {};
const windowStub = {
  innerWidth: 1000,
  innerHeight: 600,
  devicePixelRatio: 1,
  addEventListener: addL,
  requestAnimationFrame(fn) { rafQueue.push(fn); return rafQueue.length; },
  cancelAnimationFrame() { rafQueue.length = 0; },
  sessionStorage: {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
  },
};
windowStub.window = windowStub;

global.window = windowStub;
global.document = documentStub;
global.sessionStorage = windowStub.sessionStorage;
global.MutationObserver = function () { this.observe = () => {}; };
global.requestAnimationFrame = windowStub.requestAnimationFrame;
global.cancelAnimationFrame = windowStub.cancelAnimationFrame;
Date.now = () => clock;

const src = fs.readFileSync(
  path.join(__dirname, '..', '..', 'player', 'web', 'inject', 'exec_overlay.js'),
  'utf8'
);
require('vm').runInThisContext(src, { filename: 'exec_overlay.js' });

function step(ms) {
  clock += ms;
  const pending = rafQueue.splice(0);
  pending.forEach((fn) => fn());
}

function fail(msg) {
  console.error('FAIL: ' + msg);
  process.exit(1);
}

// With the gate OFF: a mark must NOT walk (nothing is drawn to pace behind).
const offDur = windowStub.__wvpExecMark({ x: 10, y: 10, w: 20, h: 20 }, 'off', '0: click');
if (offDur !== 0) fail('a gated-off mark reported a walk of ' + offDur);
if (rafQueue.length) fail('a gated-off mark started an animation');

windowStub.__wvpExecOn();
if (!rafQueue.length) fail('the overlay did not start drawing when enabled');

// First mark: walk from the viewport centre to the target's centre.
ops.length = 0;
const dur = windowStub.__wvpExecMark({ x: 100, y: 100, w: 40, h: 20 }, 'go', '1: click');
if (!(dur >= 180 && dur <= 550)) fail('walk duration out of bounds: ' + dur);

step(16);
const firstMoves = ops.filter((o) => o[0] === 'moveTo');
if (!firstMoves.length) fail('no cursor was drawn');

// Let the whole walk play out, then look at the LAST drawn cursor position.
for (let t = 16; t <= dur + 64; t += 16) step(16);
const cursorMoves = ops.filter((o) => o[0] === 'moveTo').slice(-1)[0];
const [cx, cy] = [cursorMoves[1], cursorMoves[2]];
if (Math.abs(cx - 120) > 1 || Math.abs(cy - 110) > 1) {
  fail('the cursor stopped at (' + cx + ', ' + cy + '), expected the target centre (120, 110)');
}

// The walked path must have left a trail (stroke calls), not one straight hop.
const strokes = ops.filter((o) => o[0] === 'stroke').length;
if (strokes < 3) fail('the walked path left no trail (strokes=' + strokes + ')');

// A second mark keeps walking from where the cursor rests, and never resets to
// the viewport centre.
ops.length = 0;
const dur2 = windowStub.__wvpExecMark({ x: 480, y: 300, w: 40, h: 20 }, 'next', '2: click');
for (let t = 16; t <= dur2 + 32; t += 16) step(16);
const end2 = ops.filter((o) => o[0] === 'moveTo').slice(-1)[0];
if (Math.abs(end2[1] - 500) > 1 || Math.abs(end2[2] - 310) > 1) {
  fail('the second walk ended at (' + end2[1] + ', ' + end2[2] + '), expected (500, 310)');
}

console.log(JSON.stringify({
  first_walk_ms: dur, second_walk_ms: dur2, trail_strokes: strokes,
  parked: [end2[1], end2[2]],
}));
process.exit(0);
