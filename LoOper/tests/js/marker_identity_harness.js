/**
 * marker_identity_harness.js - drives actions.JS_FF_MARKER_SAME_AS_IDENTITY
 * (the guard that decides whether Laya's tree pick may override a field's own
 * id/name identity) under node.
 *
 * A MERGED radio/checkbox group is ONE question carried by N controls, so its
 * identity ladder (`input[name=..]`) matches the WHOLE group - never a single
 * element.  The old check only accepted a UNIQUE match, so a group could never
 * confirm its own marker: it answered 'no-identity' and ANY pick survived,
 * including another question's control.  Live: the ML-experience question
 * resolved to the SALARY textarea, the answer was written there, the read-back
 * echoed it and the field was reported "filled".
 *
 * The check must accept the marker when it IS ONE OF the identity's controls
 * and refuse it ('differs') otherwise - counting a control whose label is the
 * visible thing (a styled radio hides the real input behind its label).
 *
 * Exits 0 with a JSON summary on success, non-zero on failure.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ACTIONS = path.join(__dirname, '..', '..', 'player', 'web', 'actions.py');

function extract(name) {
  const src = fs.readFileSync(ACTIONS, 'utf8');
  const m = src.match(new RegExp(
    name + '\\s*=\\s*(?:[A-Z_]+\\s*\\+\\s*)*"""([\\s\\S]*?)"""'));
  if (!m) {
    console.error(name + ' not found');
    process.exit(2);
  }
  return m[1].trim().replace(/\\\\/g, '\\');
}

const deep = extract('JS_DEEP_SEARCH');
const foreground = extract('JS_FOREGROUND');
const check = extract('JS_FF_MARKER_SAME_AS_IDENTITY');

// ---- minimal DOM --------------------------------------------------------
function rect(l, t, w, h) {
  return { left: l, top: t, right: l + w, bottom: t + h, width: w, height: h };
}

function matchesAttr(el, sel) {
  const m = /^([a-z0-9]*)\[([A-Za-z0-9_-]+)=["']?([^"']*)["']?\]$/.exec(sel.trim());
  if (!m) return false;
  if (m[1] && el.tagName.toLowerCase() !== m[1]) return false;
  return el._attrs[m[2]] === m[3];
}

function matchSimple(el, sel) {
  sel = sel.trim();
  if (sel === '*') return true;
  if (sel[0] === '[') return matchesAttr(el, sel);
  return matchesAttr(el, sel) ||
    (() => {
      const m = /^([a-z0-9]*)$/.exec(sel);
      return !!m && (!m[1] || el.tagName.toLowerCase() === m[1]);
    })();
}

function walk(el, out) {
  (el.children || []).forEach((c) => { out.push(c); walk(c, out); });
  return out;
}

function makeEl(tag, attrs, children, text, box) {
  const el = {
    nodeType: 1,
    tagName: tag,
    id: (attrs && attrs.id) || '',
    children: children || [],
    _attrs: attrs || {},
    textContent: text || '',
    innerText: text || '',
    _box: box,
    parentElement: null,
    ownerDocument: null,
    hidden: false,
    getAttribute(n) { return this._attrs[n] != null ? this._attrs[n] : null; },
    hasAttribute(n) { return this._attrs[n] != null; },
    getBoundingClientRect() { return this._box; },
    getRootNode() { return this._root || doc; },
    closest(sel) {
      let n = this;
      while (n) {
        if (String(sel) === 'label' && n.tagName.toLowerCase() === 'label') return n;
        n = n.parentElement;
      }
      return null;
    },
    querySelectorAll(sel) {
      return walk(this, []).filter((n) => String(sel).split(',')
        .some((part) => matchSimple(n, part)));
    },
    querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
  };
  (children || []).forEach((c) => { c.parentElement = el; });
  return el;
}

// A shipped radio HIDES the real input behind its label: zero box on the
// input, a real box on the <label for=..>.  A group of them is one question.
const groupHidden = [];
const hiddenLabels = [];
for (let i = 1; i <= 3; i++) {
  const r = makeEl('INPUT', { type: 'radio', name: 'gh', id: 'h' + i }, [],
    '', rect(0, 0, 0, 0));
  const l = makeEl('LABEL', { 'for': 'h' + i }, [], 'Option ' + i,
    rect(200, 100 + i * 20, 120, 16));
  groupHidden.push(r);
  hiddenLabels.push(l);
}
// A visible group, for the plain case.
const groupVisible = [];
for (let i = 1; i <= 3; i++) {
  groupVisible.push(makeEl('INPUT', { type: 'radio', name: 'gv', id: 'v' + i },
    [], '', rect(400, 100 + i * 20, 14, 14)));
}
// Another question's control: the wrong thing a pick can land on.
const other = makeEl('TEXTAREA', { id: 'other' }, [], '',
  rect(600, 100, 200, 40));

const root = makeEl('DIV', { id: 'root' }, [].concat(
  groupHidden, hiddenLabels, groupVisible, [other]));
root.nodeType = 9;
root.getElementById = (id) => walk(root, []).find((n) => n.id === id) || null;

const view = {
  innerWidth: 1200,
  innerHeight: 800,
  getComputedStyle: () => ({}),
};
const doc = root;
doc.defaultView = view;
// Hit-testing: the deepest element whose box holds the point; nothing covers
// the page in this fixture, so a miss falls back to the document itself.
doc.elementFromPoint = (x, y) => {
  const hit = walk(root, []).filter((n) => {
    const b = n._box;
    return b && x >= b.left && x <= b.right && y >= b.top && y <= b.bottom;
  });
  return hit.length ? hit[hit.length - 1] : root;
};
[].concat(root.children, hiddenLabels).forEach((el) => { el.ownerDocument = doc; });

global.window = view;
global.document = doc;
global.CSS = { escape: (s) => String(s) };
view.CSS = global.CSS;

const verdict = vm.runInThisContext(
  '(function () { ' + deep + '; ' + foreground + '; return function () { ' +
  check + ' }; })()', { filename: 'marker_identity.js' });

// The marker attribute a pick leaves on ONE element.
const MV = '[data-wvp-ff="1"]';
function markOn(el) {
  walk(root, []).forEach((e) => { delete e._attrs['data-wvp-ff']; });
  el._attrs['data-wvp-ff'] = '1';
}

// The marker is ONE OF the group's controls -> the pick is kept.
markOn(groupVisible[1]);
const sameVisible = verdict(['input[name="gv"]'], MV, []) === 'same';
// ...and refused when it is ANOTHER question's control.
markOn(other);
const differsFromOther = verdict(['input[name="gv"]'], MV, []) === 'differs';
// No identity selector resolves -> the rotated-id case: never a veto.
const noIdentity = verdict(['[name="nope"]'], MV, []) === 'no-identity';

// THE REGRESSION: a shipped group's real inputs are invisible behind their
// labels.  The marker sits on one of the group -> kept; on another question's
// control -> refused (the old unique-match check answered 'no-identity' here,
// so the wrong pick survived).
markOn(groupHidden[1]);
const sameHidden = verdict(['input[name="gh"]'], MV, []) === 'same';
markOn(other);
const differsHidden = verdict(['input[name="gh"]'], MV, []) === 'differs';

const ok = sameVisible && differsFromOther && differsHidden;

console.log(JSON.stringify({
  sameVisible, differsFromOther, noIdentity, sameHidden, differsHidden, ok,
}));
process.exit(ok ? 0 : 1);
