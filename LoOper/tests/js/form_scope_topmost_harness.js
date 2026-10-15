/**
 * form_scope_topmost_harness.js - drives actions.JS_ENUMERATE_FORM_FIELDS'
 * picked-container (scope) resolution under node.
 *
 * The picked container is recorded as a rich locator whose STABLE-shape
 * candidates (``form > div.ph5``, ``div.ph5``, ``form > div``) are tried before
 * the hashed recorded css.  On a page with a SHADOW-HOSTED modal (LinkedIn's
 * interop-outlet) a weak candidate ALSO matches the page's own form, and the
 * deep search used to return as soon as the FIRST root matched - the LIGHT DOM
 * is searched before any shadow root, so the scope resolved to the page
 * UNDERNEATH the modal.  A scope deliberately drops the covered/on-top gate, so
 * the fields BEHIND the modal were enumerated ("it only sees the base one").
 *
 * The scope must resolve to the layer the user picked: among every deep match,
 * the rendered + ON TOP (uncovered) one wins.  Exits 0 with a JSON summary on
 * success, non-zero on failure.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ACTIONS = path.join(__dirname, '..', '..', 'player', 'web', 'actions.py');

function extractBody(name) {
  const src = fs.readFileSync(ACTIONS, 'utf8');
  const m = src.match(new RegExp(name + '\\s*=\\s*(?:[A-Z_]+\\s*\\+\\s*)*' +
    '"""([\\s\\S]*?)"""'));
  if (!m) {
    console.error(name + ' not found in ' + ACTIONS);
    process.exit(2);
  }
  // The file holds the PYTHON literal, so every escape is doubled there and
  // node sees one backslash too many: /\\/ inside a regex literal would end the
  // regex and its remainder parse as flags.  Unescape to what Python passes to
  // execute_script.
  return m[1].trim().replace(/\\\\/g, '\\');
}

const deep = extractBody('JS_DEEP_SEARCH');
const foreground = extractBody('JS_FOREGROUND');
const enumerator = extractBody('JS_ENUMERATE_FORM_FIELDS');

// ---- minimal DOM --------------------------------------------------------
const BASE_RECT = { left: 100, top: 100, right: 300, bottom: 400, width: 200, height: 300 };
const MODAL_RECT = { left: 500, top: 100, right: 700, bottom: 400, width: 200, height: 300 };

function matchSimple(el, part) {
  part = part.trim();
  if (part === '*') return true;
  const m = /^([a-z0-9]*)((?:[.#][\w-]+)*)$/.exec(part);
  if (!m) return false; // unsupported (attribute selectors etc.)
  if (m[1] && el.tagName.toLowerCase() !== m[1]) return false;
  const toks = (m[2] || '').match(/[.#][\w-]+/g) || [];
  for (let i = 0; i < toks.length; i++) {
    const t = toks[i];
    if (t[0] === '#') {
      if (el._attrs.id !== t.slice(1)) return false;
    } else if ((el._attrs.class || '').split(/\s+/).indexOf(t.slice(1)) < 0) {
      return false;
    }
  }
  return true;
}

function matches(el, sel) {
  return String(sel).split(',').some((group) => {
    const parts = group.split('>').map((s) => s.trim()).filter(Boolean);
    if (!parts.length) return false;
    if (!matchSimple(el, parts[parts.length - 1])) return false;
    let n = el.parentElement;
    for (let i = parts.length - 2; i >= 0; i--) {
      if (!n || !matchSimple(n, parts[i])) return false;
      n = n.parentElement;
    }
    return true;
  });
}

function walk(el, out) {
  el.children.forEach((c) => { out.push(c); walk(c, out); });
  return out;
}

function makeEl(tag, attrs, children, text, rect) {
  const el = {
    nodeType: 1,
    tagName: tag,
    id: (attrs && attrs.id) || '',
    children: children || [],
    _attrs: attrs || {},
    textContent: text || '',
    _rect: rect,
    parentElement: null,
    ownerDocument: null,
    getAttribute(n) { return this._attrs[n] != null ? this._attrs[n] : null; },
    hasAttribute(n) { return this._attrs[n] != null; },
    getBoundingClientRect() { return this._rect; },
    getRootNode() { return { host: null }; },
    closest() { return null; },
    querySelectorAll(sel) { return walk(this, []).filter((n) => matches(n, sel)); },
    querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
  };
  (children || []).forEach((c) => { c.parentElement = el; });
  return el;
}

// ---- page: a base form (light DOM) UNDER a shadow-hosted modal -----------
const baseInput = makeEl('INPUT', { id: 'base-field', name: 'baseField', type: 'text' },
                        [], '', BASE_RECT);
const baseRow = makeEl('DIV', { id: 'base-row', class: 'ph5' }, [baseInput], 'Base form', BASE_RECT);
const baseForm = makeEl('FORM', {}, [baseRow], 'Base form');

const modalInput = makeEl('INPUT', { id: 'modal-field', name: 'modalField', type: 'text' },
                         [], '', MODAL_RECT);
const modalRow = makeEl('DIV', { id: 'modal-row', class: 'ph5' }, [modalInput],
                        'Contact info', MODAL_RECT);
const modalForm = makeEl('FORM', {}, [modalRow], 'Contact info');
const shadowRoot = makeEl('DIV', {}, [modalForm], 'Contact info');
const host = makeEl('DIV', { id: 'interop-outlet' }, []);
host.shadowRoot = shadowRoot;
shadowRoot.host = host;

const doc = makeEl('DIV', { id: 'root' }, [baseForm, host]);
doc.nodeType = 9;
doc.getElementById = () => null;
// The shadow-hosted modal is the layer on top: a hit-test at the BASE form
// returns the modal overlay (an unrelated element), never the base row.
const overlay = makeEl('DIV', { id: 'modal-overlay' }, []);
doc.elementFromPoint = (cx) => (cx >= 400 ? modalInput : overlay);

const win = {
  innerWidth: 1360,
  innerHeight: 800,
  document: doc,
  getComputedStyle: () => ({}),
};
[doc, baseInput, baseRow, baseForm, modalInput, modalRow, modalForm, shadowRoot, host, overlay]
  .forEach((el) => { el.ownerDocument = doc; });
doc.defaultView = win;
global.window = win;
global.document = doc;

const run = vm.runInThisContext(
  '(function () { ' + deep + '; ' + foreground + '; return function () { ' +
  enumerator + ' }; })()',
  { filename: 'form_scope_topmost.js' });

function fieldIds(scopeSelectors) {
  const fields = JSON.parse(run(scopeSelectors) || '[]');
  return fields.map((f) => f.id);
}

// The weak stable-shape candidate matches BOTH the base form and the modal:
// the pick was the MODAL, so only the modal's field may be enumerated.
const scoped = fieldIds(['form > div.ph5']);
const scopedOk = scoped.length === 1 && scoped[0] === 'modal-field';

// The whole-page scan is foreground-only, so it also skips the covered base row.
const whole = fieldIds([]);
const wholeOk = whole.length === 1 && whole[0] === 'modal-field';

// A portal outlet COVERS the viewport, so the pick frequently resolves to the
// shadow HOST itself (probe-confirmed on a shadow-root popup).  The host IS a
// valid container: its subtree - the modal - must be enumerated, not the page
// underneath it.
const hostScope = fieldIds(['#interop-outlet']);
const hostOk = hostScope.length === 1 && hostScope[0] === 'modal-field';

// DISCRIMINATION PROOF: the previous first-match resolution (deep find returns
// as soon as the FIRST root matches = the light DOM base form) resolves the
// very same candidate to the field BEHIND the modal - the "it only sees the
// base one" symptom this fix removes.
const first = vm.runInThisContext(
  '(function () { ' + deep + '; ' + foreground + '; return function () { ' +
  enumerator.replace('scopeMatch(scopeSelectors)',
                     '__wvpDeepFindAny(scopeSelectors, 0)') + ' }; })()',
  { filename: 'form_scope_firstmatch.js' });
const firstIds = (JSON.parse(first(['form > div.ph5']) || '[]')).map((f) => f.id);
const firstOk = firstIds.length === 1 && firstIds[0] === 'base-field';

// ---- the scope pick must refuse to guess between lookalikes --------------
// A site that styles EVERY popup with the same CSS-module classes makes a
// content rung match several containers at once. Taking the first match is how
// a control OUTSIDE the picked div (a header 'Search' dialog) got enumerated,
// read back and fed to ComoRAG as if it belonged to the form: an ambiguous rung
// now names NOTHING, and the ladder keeps walking to a rung that does resolve.
const dupA = makeEl('DIV', { id: 'dup-a', class: 'dup' }, [], 'A', MODAL_RECT);
const dupB = makeEl('DIV', { id: 'dup-b', class: 'dup' }, [], 'B', BASE_RECT);
doc.children.push(dupA, dupB);
dupA.parentElement = doc; dupB.parentElement = doc;
dupA.ownerDocument = doc; dupB.ownerDocument = doc;

const pickScope = vm.runInThisContext(
  '(function () { ' + deep + '; ' + foreground + '; ' +
  'return { pick: __wvpScopePick }; })()',
  { filename: 'form_scope_pick.js' });
const ambiguous = pickScope.pick(['div.dup'], true);
const ambiguousOk = !ambiguous;
// Ambiguous first rung, unambiguous second: the ladder still finds the pick.
const fellThrough = pickScope.pick(['div.dup', 'form > div.ph5'], true);
const fellThroughOk = !!fellThrough && fellThrough.id === 'modal-row';

const ok = scopedOk && wholeOk && hostOk && firstOk && ambiguousOk && fellThroughOk;
console.log(JSON.stringify({ scoped, whole, hostScope, firstIds, scopedOk, wholeOk,
                             hostOk, firstOk,
                             ambiguous: ambiguous && ambiguous.id, ambiguousOk,
                             fellThrough: fellThrough && fellThrough.id,
                             fellThroughOk, ok }));
process.exit(ok ? 0 : 1);
