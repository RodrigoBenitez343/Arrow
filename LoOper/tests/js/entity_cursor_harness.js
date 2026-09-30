/**
 * entity_cursor_harness.js - drives actions.JS_ENTITY_CURSOR under node.
 *
 * The repeating-element cursor for a shadow-DOM target is in-page JavaScript
 * (WebDriver cannot reach the element), so it needs its own check: a shadow
 * root is searched, a container match is skipped, an already-processed match is
 * skipped, and a fully processed set reports exhaustion.
 *
 * The snippet is extracted from actions.py (single source of truth) with the
 * arguments[...] placeholders bound to named parameters, then run against a
 * minimal fake DOM.  Exits 0 with a JSON summary on success, non-zero on
 * failure.
 */
'use strict';
const fs = require('fs');
const path = require('path');

const py = fs.readFileSync(
  path.join(__dirname, '..', '..', 'player', 'web', 'actions.py'),
  'utf8'
);
const m = py.match(/JS_ENTITY_CURSOR = """([\s\S]*?)"""/);
if (!m) {
  console.error('JS_ENTITY_CURSOR not found in actions.py');
  process.exit(2);
}
const expr = m[1]
  .trim()
  .replace(/^return\s+/, '')
  .replace(/arguments\[0\]/g, 'selectors')
  .replace(/arguments\[1\]/g, 'keyAttr')
  .replace(/arguments\[2\]/g, 'processed')
  .replace(/;\s*$/, '');
const vm = require('vm');
const runCursor = vm.runInThisContext(
  '(function (document, selectors, keyAttr, processed) { return (' + expr + '); })',
  { filename: 'entity_cursor.js' }
);

// ---- minimal DOM --------------------------------------------------------
const SEL = 'div[role="switch"]';

function matchSimple(el, sel) {
  const av = /^([a-z0-9]*)\[([A-Za-z0-9_-]+)=["']([^"']*)["']\]$/.exec(sel);
  if (av) {
    const tag = av[1];
    if (tag && el.tagName.toLowerCase() !== tag) return false;
    return el.getAttribute(av[2]) === av[3];
  }
  const a = /^([a-z0-9]*)\[([A-Za-z0-9_-]+)\]$/.exec(sel);
  if (a) {
    const tag = a[1];
    if (tag && el.tagName.toLowerCase() !== tag) return false;
    return el.getAttribute(a[2]) !== null;
  }
  const c = /^([a-z0-9]*)((?:\.[A-Za-z0-9_-]+)*)$/.exec(sel);
  if (!c) return false;
  const tag = c[1];
  const cls = c[2] ? c[2].split('.').filter(Boolean) : [];
  if (tag && el.tagName.toLowerCase() !== tag) return false;
  return cls.every((k) => el.classList.indexOf(k) >= 0);
}

function makeEl(tag, attrs, children) {
  const el = {
    nodeType: 1,
    tagName: tag,
    classList: [],
    children: children || [],
    _attrs: attrs || {},
    getAttribute(n) { return this._attrs[n] != null ? this._attrs[n] : null; },
  };
  el.querySelectorAll = (sel) => {
    const out = [];
    (function walk(node) {
      node.children.forEach((c) => {
        if (matchSimple(c, sel)) out.push(c);
        walk(c);
      });
    })(el);
    return out;
  };
  return el;
}

// A shadow host wrapping the three switches; a light-DOM container that ALSO
// matches the selector (the outer box the cursor must skip).
const sw1 = makeEl('DIV', { role: 'switch' });
const sw2 = makeEl('DIV', { role: 'switch' });
const sw3 = makeEl('DIV', { role: 'switch' });
const shadowRoot = makeEl('DIV', {}, [sw1, sw2, sw3]);
const host = makeEl('DIV', {});
host.shadowRoot = shadowRoot;
const lightContainer = makeEl('DIV', { role: 'switch' },
                              [makeEl('DIV', { role: 'switch' })]);

// The document has NO light-DOM matches, so the walk must descend into the
// shadow root and find the switches there.  The container is injected into the
// shadow list to exercise the "skip a match that encloses another match" rule.
let shadowMatches = [sw1, sw2, sw3];
const documentStub = {
  querySelectorAll(sel) {
    if (sel === '*') return [host];
    return []; // light DOM has nothing for SEL
  },
};
shadowRoot.querySelectorAll = (sel) => {
  if (sel === '*') return [sw1, sw2, sw3];
  return sel === SEL ? shadowMatches : [];
};
host.shadowRoot = shadowRoot;

// Bind the trusted local snippet to this fake document.
const cursor = (sels, keyAttr, processed) =>
  runCursor(documentStub, sels, keyAttr, processed);

// 1) fresh set -> first match (ordinal 0)
const first = cursor([SEL], null, []);
const firstOk = first && first.ordinal === 0 && first.total === 3 &&
  first.key === '__ordinal_0' && first.selector === SEL;

// 2) ordinal 0 processed -> the cursor advances to 1
const second = cursor([SEL], null, ['__ordinal_0']);
const secondOk = second && second.ordinal === 1 && second.key === '__ordinal_1';

// 3) a match that ENCLOSES another match (the container) is skipped
shadowMatches = [lightContainer, sw1, sw2];
const skipBox = cursor([SEL], null, []);
const skipOk = skipBox && skipBox.ordinal === 1 && skipBox.total === 3;

// 4) every match processed -> exhausted (NOT a miss)
const done = cursor([SEL], null,
                    ['__ordinal_0', '__ordinal_1', '__ordinal_2']);
const doneOk = done && done.exhausted === true && done.total === 3;

// 5) no candidate matches -> null (the caller falls back to the recorded locator)
const miss = cursor([], null, []);
const missOk = miss === null;

// 6) a stable key_attr overrides position
shadowMatches = [sw1, sw2, sw3];
sw2._attrs['data-id'] = 'row-b';
const keyed = cursor([SEL], 'data-id', ['row-b']);
const keyedOk = keyed && keyed.ordinal === 0 && keyed.key === '__ordinal_0';

const ok = firstOk && secondOk && skipOk && doneOk && missOk && keyedOk;
console.log(JSON.stringify({
  firstOrdinal: first && first.ordinal,
  secondOrdinal: second && second.ordinal,
  skippedContainerOrdinal: skipBox && skipBox.ordinal,
  exhausted: done && done.exhausted,
  miss,
  keyedKey: keyed && keyed.key,
  ok,
}));
process.exit(ok ? 0 : 1);
