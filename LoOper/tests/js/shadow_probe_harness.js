/**
 * shadow_probe_harness.js - drives web_conditions.JS_SHADOW_ON_SCREEN (with
 * actions.JS_DEEP_SEARCH prepended) under node.
 *
 * A web conditional's element_located routes on a shadow-root target that
 * WebDriver cannot see, so the on-screen verdict is taken in-page.  The probe
 * must AGREE with the record: the recorded TEXT is the identity (a dynamic
 * element may re-render with a different tag), so the element's text must
 * CONTAIN it - a shorter lookalike must be rejected, and the TAG is only
 * required when no text was recorded.
 * The snippets are extracted from the .py sources (single source of truth) with
 * ``document``/``window`` stubbed.
 *
 * Exits 0 with a JSON summary on success, non-zero on failure.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

function extract(file, name) {
  const src = fs.readFileSync(file, 'utf8');
  const m = src.match(new RegExp(name + ' = """([\\s\\S]*?)"""'));
  if (!m) {
    console.error(name + ' not found in ' + file);
    process.exit(2);
  }
  return m[1].trim().replace(/;\s*$/, '');
}

const ACTIONS = path.join(__dirname, '..', '..', 'player', 'web', 'actions.py');
const CONDITIONS = path.join(
  __dirname, '..', '..', 'player', 'multi_sequence',
  'conditional_fallback_resources', 'web_conditions.py');
const deep = extract(ACTIONS, 'JS_DEEP_SEARCH');
const probe = extract(CONDITIONS, 'JS_SHADOW_ON_SCREEN');

// ---- minimal DOM --------------------------------------------------------
function matches(el, sel) {
  return String(sel).split(',').some((part) => {
    part = part.trim();
    let x;
    if ((x = /^\.([\w-]+)$/.exec(part))) return (el._attrs.class || '').split(/\s+/).indexOf(x[1]) >= 0;
    if ((x = /^([a-z0-9]+)$/.exec(part))) return el.tagName.toLowerCase() === x[1];
    if (part === '*') return true;
    return false;
  });
}

function walk(el, out) {
  el.children.forEach((c) => { out.push(c); walk(c, out); });
  return out;
}

const ON = { left: 10, top: 10, right: 110, bottom: 30, width: 100, height: 20 };
const OFF = { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 };

function makeEl(tag, attrs, children, text) {
  const el = {
    nodeType: 1,
    tagName: tag,
    children: children || [],
    _attrs: attrs || {},
    textContent: text || '',
    _rect: ON,
    _visible: true,
    getAttribute(n) { return this._attrs[n] != null ? this._attrs[n] : null; },
    getBoundingClientRect() { return this._rect; },
    checkVisibility() { return this._visible; },
  };
  el.querySelectorAll = (sel) => walk(el, []).filter((n) => matches(n, sel));
  el.querySelector = (sel) => el.querySelectorAll(sel)[0] || null;
  return el;
}

// The shadow root holds a LOOKALIKE first (same classes, shorter text) then the
// real target - the selector lands on the lookalike, the text match on the real
// one, exactly like LinkedIn's "Apply" vs "Easy Apply" span pair.
function build(targetRect, targetVisible) {
  const lookalike = makeEl('BUTTON', { class: 'apply' }, [], 'Apply');
  const target = makeEl('BUTTON', { class: 'apply' }, [], 'Easy Apply');
  target._rect = targetRect;
  target._visible = targetVisible !== false;
  const shadowRoot = makeEl('DIV', {}, [lookalike, target]);
  const host = makeEl('DIV', { id: 'interop-outlet' }, []);
  host.shadowRoot = shadowRoot;
  return makeEl('DIV', {}, [host]);
}

// A single shadow-hosted target (no lookalike): the reuse case where the same
// condition runs against an element that now renders extra text.
function buildSingle(text) {
  const target = makeEl('BUTTON', { class: 'apply' }, [], text);
  const shadowRoot = makeEl('DIV', {}, [target]);
  const host = makeEl('DIV', { id: 'interop-outlet' }, []);
  host.shadowRoot = shadowRoot;
  return makeEl('DIV', {}, [host]);
}

// TWO exact-text twins, the first OFF-SCREEN (a "Next" pagination far below the
// fold) and the second the modal's own VISIBLE "Next" button.  The probe must
// answer TRUE: inspecting only the first match routed FALSE for a visible target.
function buildOffscreenTwin() {
  const pag = makeEl('BUTTON', { class: 'next' }, [], 'Next');
  pag._rect = { left: 10, top: 3096, right: 76, bottom: 3128, width: 66, height: 32 };
  const modal = makeEl('BUTTON', { class: 'next' }, [], 'Next');
  modal._rect = ON;
  const shadowRoot = makeEl('DIV', {}, [pag, modal]);
  const host = makeEl('DIV', { id: 'interop-outlet' }, []);
  host.shadowRoot = shadowRoot;
  return makeEl('DIV', {}, [host]);
}

global.window = {
  CSS: { escape: (s) => String(s) },
  innerWidth: 1280,
  innerHeight: 800,
};
global.CSS = global.window.CSS;

const factory = vm.runInThisContext(
  '(function (document) { ' + deep + '; return function (selectors, text, tag, label) { ' +
    probe + ' }; })',
  { filename: 'shadow_probe.js' }
);

// Lookalike matched by the selector, real target found by exact text -> TRUE.
const good = factory(build(ON, true));
const matchOk = good(['.apply'], 'Easy Apply', 'button', null) === true;
// Recorded text no longer present anywhere -> null (keeps polling, never TRUE).
const missOk = good(['.apply'], 'Gone away', 'button', null) === null;
// The right element, but hidden (zero box) -> false, not TRUE.
const hiddenOk = factory(build(OFF, true))(['.apply'], 'Easy Apply', 'button', null) === false;
// A dynamic element re-rendered with a DIFFERENT tag but the same text -> TRUE
// (the recorded text is the identity; the tag is not required).
const tagIgnoredOk = factory(build(ON, true))(['.apply'], 'Easy Apply', 'span', null) === true;
// Reused condition: the element is present but now renders EXTRA text -> TRUE.
const driftOk = factory(buildSingle('Easy Apply to this job'))(
  ['.apply'], 'Easy Apply', 'button', null) === true;
// A shorter lookalike is still rejected (the record is not contained).
const shortOk = factory(buildSingle('Apply'))(
  ['.apply'], 'Easy Apply', 'button', null) === null;
// With NO recorded text the tag is the only signal -> it must agree.
const noTextTagOk = factory(buildSingle(''))(['.apply'], '', 'button', null) === true
  && factory(buildSingle(''))(['.apply'], '', 'span', null) === null;
// The first exact-text twin is OFF-SCREEN; the VISIBLE one must still win.
const offscreenTwinOk = factory(buildOffscreenTwin())(
  ['.next'], 'Next', 'button', null) === true;

const ok = matchOk && missOk && hiddenOk && tagIgnoredOk && driftOk && shortOk
  && noTextTagOk && offscreenTwinOk;
console.log(JSON.stringify(
  { matchOk, missOk, hiddenOk, tagIgnoredOk, driftOk, shortOk, noTextTagOk,
    offscreenTwinOk, ok }));
process.exit(ok ? 0 : 1);
