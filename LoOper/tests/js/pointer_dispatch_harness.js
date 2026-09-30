/**
 * pointer_dispatch_harness.js - drives actions.JS_DOM_POINTER under node.
 *
 * A web action whose target lives in a shadow root is dispatched in-page.  The
 * recorded id is VOLATILE (LinkedIn/Ember ids like ``ember316`` rotate between
 * sessions) and the class candidates are WEAK (``.artdeco-button--primary``
 * matches EVERY primary button), so first-match-wins dispatched on the WRONG
 * element ("Back" when "Next" was recorded, a wrapping container that reuses
 * the id, or an off-screen "Next" pagination) and still reported success - the
 * page simply never advanced.
 *
 * The dispatch must therefore AGREE with the record: the recorded TEXT is the
 * element's identity, the match must be RENDERED + ON SCREEN, and the most
 * PRECISE match (shortest text that still contains the recorded text) wins.
 * The snippet is extracted from the .py source (single source of truth) with
 * ``document``/``window`` stubbed.
 *
 * Exits 0 with a JSON summary on success, non-zero on failure.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ACTIONS = path.join(__dirname, '..', '..', 'player', 'web', 'actions.py');

function extractBody(file, name) {
  const src = fs.readFileSync(file, 'utf8');
  // ``NAME = JS_DEEP_SEARCH + """..."""`` (the pointer) or ``NAME = """..."""``.
  const m = src.match(new RegExp(
    name + '\\s*=\\s*(?:JS_DEEP_SEARCH\\s*\\+\\s*)?"""([\\s\\S]*?)"""'));
  if (!m) {
    console.error(name + ' not found in ' + file);
    process.exit(2);
  }
  return m[1].trim().replace(/;\s*$/, '');
}

const deep = extractBody(ACTIONS, 'JS_DEEP_SEARCH');
const pointer = extractBody(ACTIONS, 'JS_DOM_POINTER');

// ---- minimal DOM --------------------------------------------------------
const ON = { left: 10, top: 10, right: 120, bottom: 42, width: 110, height: 32 };
const OFF = { left: 10, top: 3096, right: 76, bottom: 3128, width: 66, height: 32 };

function matches(el, sel) {
  return String(sel).split(',').some((part) => {
    part = part.trim();
    if (part === '*') return true;
    let x;
    if ((x = /^([a-z0-9]+)?#([\w-]+)$/.exec(part))) {
      if (x[1] && el.tagName.toLowerCase() !== x[1]) return false;
      return el._attrs.id === x[2];
    }
    if ((x = /^#([\w-]+)$/.exec(part))) return el._attrs.id === x[1];
    if ((x = /^([a-z0-9]+)?((?:\.[\w-]+)+)$/.exec(part))) {
      if (x[1] && el.tagName.toLowerCase() !== x[1]) return false;
      const have = (el._attrs.class || '').split(/\s+/);
      return x[2].slice(1).split('.').every((c) => have.indexOf(c) >= 0);
    }
    if ((x = /^([a-z0-9]+)$/.exec(part))) return el.tagName.toLowerCase() === x[1];
    return false;
  });
}

function walk(el, out) {
  el.children.forEach((c) => { out.push(c); walk(c, out); });
  return out;
}

function makeEl(tag, attrs, children, text) {
  const el = {
    nodeType: 1,
    tagName: tag,
    children: children || [],
    _attrs: attrs || {},
    textContent: text || '',
    _rect: ON,
    _visible: true,
    _targeted: false,
    parentElement: null,
    contains(other) {
      let n = other;
      while (n) { if (n === el) return true; n = n.parentElement; }
      return false;
    },
    getAttribute(n) { return this._attrs[n] != null ? this._attrs[n] : null; },
    hasAttribute(n) { return this._attrs[n] != null; },
    getBoundingClientRect() { return this._rect; },
    checkVisibility() { return this._visible; },
    getRootNode() { return { host: null }; },
    focus() { this._targeted = true; },
    scrollIntoView() {},
    dispatchEvent(ev) { this._events = (this._events || 0) + 1; return true; },
  };
  (children || []).forEach((c) => { c.parentElement = el; });
  el.querySelectorAll = (sel) => walk(el, []).filter((n) => matches(n, sel));
  el.querySelector = (sel) => el.querySelectorAll(sel)[0] || null;
  return el;
}

function shadowHosted(children) {
  const shadowRoot = makeEl('DIV', {}, children);
  const host = makeEl('DIV', { id: 'interop-outlet' }, []);
  host.shadowRoot = shadowRoot;
  return makeEl('DIV', {}, [host]);
}

// A wrapping container that REUSES the recorded volatile id (regains "ember316"
// on a later render) and holds the whole form's text, next to the real button.
const buildIdReused = () => {
  const container = makeEl('DIV', { id: 'ember316', class: 'form' }, [],
                            'Back Next Review');
  const realNext = makeEl(
    'BUTTON',
    { class: 'artdeco-button artdeco-button--2 artdeco-button--primary' },
    [], 'Next');
  return { root: shadowHosted([container, realNext]), container, realNext };
};

// TWO exact-text "Next" buttons, the first OFF-SCREEN (pagination far below
// the fold) and the second the modal's own VISIBLE button.
const buildOffscreenTwin = () => {
  const pag = makeEl('BUTTON', { class: 'next' }, [], 'Next');
  pag._rect = OFF;
  const modal = makeEl('BUTTON', { class: 'next' }, [], 'Next');
  modal._rect = ON;
  return { root: shadowHosted([pag, modal]), pag, modal };
};

global.window = {
  CSS: { escape: (s) => String(s) },
  innerWidth: 1280,
  innerHeight: 800,
};
global.CSS = global.window.CSS;
// Node has no PointerEvent/MouseEvent; the dispatch loop builds them.
function FakeEvent(type, init) { this.type = type; this.init = init; }
global.PointerEvent = FakeEvent;
global.MouseEvent = FakeEvent;

function dispatch(root, selectors, text, index) {
  const factory = vm.runInThisContext(
    '(function (document) { ' + deep + '; return function (' +
      'selectors, button, mode, mods, index, text, label) { ' + pointer +
      ' }; })',
    { filename: 'pointer_dispatch.js' });
  const run = factory(root);
  return run(selectors, '0', 'click', [], index, text, null);
}

// A container that reuses the idle recorded id must NOT be clicked; the real
// (shorter-text) button wins.
const reused = buildIdReused();
dispatch(reused.root,
         ['button#ember316', '#ember316',
          '.artdeco-button.artdeco-button--2.artdeco-button--primary'],
         'Next', 0);
const idReuseOk = reused.realNext._targeted === true
  && reused.container._targeted === false;

// The visible modal button must beat the off-screen pagination twin.
const twin = buildOffscreenTwin();
dispatch(twin.root, ['.next'], 'Next', 0);
const offscreenOk = twin.modal._targeted === true && twin.pag._targeted === false;

// No candidate agrees with the record: NOTHING is dispatched.  Clicking a
// weak-class lookalike (a sibling button of the modal, or a control on the
// background document) would be a WRONG click that still reports success - the
// action must wait and then fail loudly instead.
const miss = buildOffscreenTwin();
const missDispatched = dispatch(miss.root, ['.next'], 'Gone away', 0) === true;
const fallbackOk = !missDispatched
  && miss.pag._targeted === false && miss.modal._targeted === false;

// NOTHING is on screen and the recorded VOLATILE id now sits on a WRAPPER (the
// outer modal / footer div) that carries the same text: the recorded click must
// still land on the BUTTON, never on the enclosing outer element.
const buildOffscreenWrapper = () => {
  const inner = makeEl('BUTTON', { class: 'artdeco-button--primary' }, [],
                       'Submit application');
  inner._rect = OFF;
  const wrapper = makeEl('DIV', { id: 'ember900' }, [inner], 'Submit application');
  wrapper._rect = OFF;
  return { root: shadowHosted([wrapper]), wrapper, inner };
};
const wrap = buildOffscreenWrapper();
dispatch(wrap.root, ['button#ember900', '#ember900', '.artdeco-button--primary'],
         'Submit application', 0);
const wrapperOk = wrap.inner._targeted === true && wrap.wrapper._targeted === false;

const ok = idReuseOk && offscreenOk && fallbackOk && wrapperOk;
console.log(JSON.stringify({ idReuseOk, offscreenOk, fallbackOk, wrapperOk, ok }));
process.exit(ok ? 0 : 1);
