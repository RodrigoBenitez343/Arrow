'use strict';
/**
 * root_doc_harness.js - drives actions.JS_DEEP_SEARCH's __wvpRootDoc root.
 *
 * A chain shares ONE browser and WebDriver keeps its frame context between
 * commands, so an action can be left executing inside a frame a previous node
 * entered.  The element it must act on is whatever is RENDERED now, which the
 * TOP document composites - so the in-page search must climb to the top-most
 * SAME-ORIGIN document instead of being trapped in the stale frame.
 *
 * This check binds ``document`` to an inner frame that does NOT hold the
 * target, with ``window.parent`` pointing at a top document that does.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const py = fs.readFileSync(
  path.join(__dirname, '..', '..', 'player', 'web', 'actions.py'), 'utf8'
);
const m = py.match(/JS_DEEP_SEARCH = """([\s\S]*?)"""/);
if (!m) { console.error('JS_DEEP_SEARCH not found'); process.exit(2); }
const expr = m[1].trim().replace(/;\s*$/, '');

// ---- minimal DOM --------------------------------------------------------
function matches(el, sel) {
  return String(sel).split(',').some((part) => {
    part = part.trim();
    let x;
    if ((x = /^([a-z0-9]*)#(.+)$/.exec(part)))
      return (!x[1] || el.tagName.toLowerCase() === x[1]) && el._attrs.id === x[2];
    if ((x = /^#(.+)$/.exec(part))) return el._attrs.id === x[1];
    if ((x = /^([a-z0-9]+)$/.exec(part))) return el.tagName.toLowerCase() === x[1];
    return part === '*';
  });
}
function walk(el, out) {
  el.children.forEach((c) => { out.push(c); walk(c, out); });
  return out;
}
function makeEl(tag, attrs, children) {
  const el = {
    nodeType: 1, tagName: tag, children: children || [], _attrs: attrs || {},
    textContent: (attrs && attrs.__text) || '',
    getAttribute(n) { return this._attrs[n] != null ? this._attrs[n] : null; },
  };
  el.querySelectorAll = (sel) => walk(el, []).filter((n) => matches(n, sel));
  el.querySelector = (sel) => el.querySelectorAll(sel)[0] || null;
  return el;
}

// Top document: the shadow host that holds the real target.
const submit = makeEl('BUTTON', { id: 'submit' });
const shadowRoot = makeEl('DIV', {}, [submit]);
const host = makeEl('DIV', { id: 'interop-outlet' });
host.shadowRoot = shadowRoot;
const topRoot = makeEl('DIV', {}, [host]);
const topDoc = { querySelectorAll: (s) => walk(topRoot, []).filter((n) => matches(n, s)) };

// Inner frame document: rendered, but holds the target NOWHERE.
const frameRoot = makeEl('DIV', {}, [makeEl('BUTTON', { id: 'other' })]);
const frameDoc = { querySelectorAll: (s) => walk(frameRoot, []).filter((n) => matches(n, s)) };

const winTop = { document: topDoc };
winTop.parent = winTop;               // top-most
const winFrame = { document: frameDoc, parent: winTop };
global.window = winFrame;
global.CSS = { escape: (s) => String(s) };

const deep = vm.runInThisContext(
  '(function (document) { ' + expr + '; return { find: __wvpDeepFind, ' +
  'root: __wvpRootDoc }; })', { filename: 'deep_search.js' }
)(frameDoc);

const directMiss = frameDoc.querySelectorAll('button#submit').length === 0;
const rootIsTop = deep.root() === topDoc;
const found = deep.find('button#submit', 0) === submit;
const byIdFound = (function () {
  const d2 = vm.runInThisContext(
    '(function (document) { ' + expr + '; return __wvpDeepFindById; })',
    { filename: 'deep_search_id.js' }
  )(frameDoc);
  return d2('submit') === submit;
})();

// Fallback: no window.parent -> the CURRENT document is the root.
const noParentDoc = { querySelectorAll: () => [] };
global.window = { document: noParentDoc };
const deep2 = vm.runInThisContext(
  '(function (document) { ' + expr + '; return __wvpRootDoc; })',
  { filename: 'deep_search_fb.js' }
)(noParentDoc);
const fallbackOk = deep2() === noParentDoc;

const ok = directMiss && rootIsTop && found && byIdFound && fallbackOk;
console.log(JSON.stringify({
  directMiss, rootIsTop, found, byIdFound, fallbackOk, ok,
}));
process.exit(ok ? 0 : 1);
