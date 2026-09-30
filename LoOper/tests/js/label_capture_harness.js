/**
 * label_capture_harness.js - drives locators.js ``__wvpLoc.labelTextOf`` under
 * node.
 *
 * Capturing the label is what makes a form click portable: the recorded id may
 * embed per-instance tokens (LinkedIn's Easy Apply ids carry the job id) while
 * the label stays the same.  The label must therefore be found in the CONTROL'S
 * OWN ROOT - a form rendered into a shadow root keeps its label there, where
 * ``document.querySelectorAll('label')`` cannot see it.
 *
 * The real locators.js is loaded against a minimal fake DOM.  Exits 0 with a
 * JSON summary on success, non-zero on failure.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const src = fs.readFileSync(
  path.join(__dirname, '..', '..', 'player', 'web', 'inject', 'locators.js'),
  'utf8'
);

// ---- minimal DOM --------------------------------------------------------
const ATTR_SEL = /^([a-z0-9]*)\[([A-Za-z0-9_-]+)=["']?([^"']*)["']?\]$/;

function matches(el, sel) {
  const m = ATTR_SEL.exec(sel);
  if (!m) return false;
  if (m[1] && el.tagName.toLowerCase() !== m[1]) return false;
  return el.getAttribute(m[2]) === m[3];
}

function makeEl(tag, attrs, options) {
  options = options || {};
  const el = {
    nodeType: 1,
    tagName: tag,
    id: (attrs && attrs.id) || '',
    classList: [],
    attributes: [],
    textContent: options.text || '',
    parentElement: options.parent || null,
    _attrs: attrs || {},
    getAttribute(n) { return this._attrs[n] != null ? this._attrs[n] : null; },
    getAttributeNames() { return Object.keys(this._attrs); },
    getRootNode() { return options.root || documentStub; },
    closest(sel) { return sel === 'label' ? (options.wrappingLabel || null) : null; },
    querySelectorAll(sel) { return (options.children || []).filter((c) => matches(c, sel)); },
    querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
  };
  if (attrs && attrs.__for) el.htmlFor = attrs.__for;
  return el;
}

// The label lives INSIDE the shadow root, next to the control it names - the
// light DOM holds neither of them.
const label = makeEl('LABEL', { for: 'elem-jobB-1-text' }, { text: '  First\nname ' });
const input = makeEl('INPUT', { id: 'elem-jobB-1-text' }, { root: null });
const shadowRoot = makeEl('DIV', {}, { children: [label, input] });
input.getRootNode = () => shadowRoot;
const host = makeEl('DIV', { id: 'interop-outlet' }, { children: [shadowRoot] });
shadowRoot.parentElement = host;

// A control whose label WRAPS it (no for=), and one with no label at all.
const wrapInput = makeEl('INPUT', {});
const wrapLabel = makeEl('LABEL', { __for: '' }, { text: 'Phone number' });
wrapInput.closest = (sel) => (sel === 'label' ? wrapLabel : null);
const orphan = makeEl('INPUT', { id: 'no-label-here' }, { root: shadowRoot });

const documentStub = {
  documentElement: makeEl('HTML', {}, { root: null }),
  body: makeEl('BODY', {}, { root: null }),
  // The light DOM has NO labels: only a root-aware lookup can find them.
  querySelectorAll() { return []; },
  querySelector() { return null; },
};
documentStub.documentElement.getRootNode = () => documentStub;
documentStub.body.getRootNode = () => documentStub;

global.window = {};
global.document = documentStub;
global.CSS = { escape: (s) => String(s) };
// The snippet is page code; run it in this stub's scope.
vm.runInThisContext(src, { filename: 'locators.js' });
const loc = global.window.__wvpLoc;

const firstOk = loc.labelTextOf(input) === 'First name';   // whitespace collapsed
const wrapOk = loc.labelTextOf(wrapInput) === 'Phone number';
const orphanOk = loc.labelTextOf(orphan) === null;
const nonFieldOk = loc.labelTextOf(label) === null;        // a LABEL is not a field

const ok = firstOk && wrapOk && orphanOk && nonFieldOk;
console.log(JSON.stringify({ firstOk, wrapOk, orphanOk, nonFieldOk, ok }));
process.exit(ok ? 0 : 1);
