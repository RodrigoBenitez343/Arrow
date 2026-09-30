/**
 * label_lookup_harness.js - drives actions.JS_DEEP_SEARCH's __wvpDeepFindLabel
 * under node.
 *
 * The label resolver is the portable identity of a form field (a site's
 * generated id/name carries per-instance tokens that rotate between forms), and
 * it runs in-page because WebDriver cannot reach a shadow root.  So it needs its
 * own check: a shadow-hosted label resolves to its control, whitespace/case are
 * forgiven, and an absent OR ambiguous label returns null (never a lookalike).
 *
 * The snippet is extracted from actions.py (single source of truth) with
 * ``document`` bound to a minimal fake DOM.  Exits 0 with a JSON summary on
 * success, non-zero on failure.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const py = fs.readFileSync(
  path.join(__dirname, '..', '..', 'player', 'web', 'actions.py'),
  'utf8'
);
const m = py.match(/JS_DEEP_SEARCH = """([\s\S]*?)"""/);
if (!m) {
  console.error('JS_DEEP_SEARCH not found in actions.py');
  process.exit(2);
}
const expr = m[1].trim().replace(/;\s*$/, '');

// ---- minimal DOM --------------------------------------------------------
function matches(el, sel) {
  return String(sel).split(',').some((part) => {
    part = part.trim();
    let x;
    if ((x = /^#(.+)$/.exec(part))) return el._attrs.id === x[1];
    if ((x = /^([a-z0-9]+)$/.exec(part))) return el.tagName.toLowerCase() === x[1];
    if ((x = /^([a-z0-9]*)\[([A-Za-z0-9_-]+)=["']?([^"']*)["']?\]$/.exec(part))) {
      return (!x[1] || el.tagName.toLowerCase() === x[1]) && el._attrs[x[2]] === x[3];
    }
    return part === '*';
  });
}

function walk(el, out) {
  el.children.forEach((c) => { out.push(c); walk(c, out); });
  return out;
}

function makeEl(tag, attrs, children) {
  const el = {
    nodeType: 1,
    tagName: tag,
    children: children || [],
    _attrs: attrs || {},
    textContent: (attrs && attrs.__text) || '',
    htmlFor: (attrs && attrs.__for) || '',
    getAttribute(n) { return this._attrs[n] != null ? this._attrs[n] : null; },
  };
  el.querySelectorAll = (sel) => walk(el, []).filter((n) => matches(n, sel));
  el.querySelector = (sel) => el.querySelectorAll(sel)[0] || null;
  return el;
}

// A LinkedIn-shaped Easy Apply form rendered INTO a shadow root: every control
// is anonymous apart from its label, and the ids embed per-form tokens (here
// the "job id" differs from the recording's - which is exactly why the label
// has to carry the identity).
const firstLabel = makeEl('LABEL', { for: 'elem-jobB-1-text', __text: '  First\nname ' });
const firstInput = makeEl('INPUT', { id: 'elem-jobB-1-text', type: 'text' });
const lastLabel = makeEl('LABEL', { for: 'elem-jobB-2-text', __text: 'Last name' });
const lastInput = makeEl('INPUT', { id: 'elem-jobB-2-text', type: 'text' });
// Two labels sharing one text: ambiguous -> must never resolve to a lookalike.
const cityLabelA = makeEl('LABEL', { for: 'elem-jobB-3-text', __text: 'City' });
const cityInputA = makeEl('INPUT', { id: 'elem-jobB-3-text', type: 'text' });
const cityLabelB = makeEl('LABEL', { for: 'elem-jobB-4-text', __text: 'City' });
const cityInputB = makeEl('INPUT', { id: 'elem-jobB-4-text', type: 'text' });
// A label WRAPPING its control (no for=) is the second association shape.
const wrapInput = makeEl('INPUT', { type: 'tel' });
const wrapLabel = makeEl('LABEL', { __text: 'Phone number' }, [wrapInput]);
const unlabeled = makeEl('INPUT', { id: 'elem-jobB-5-text', type: 'text' });

const formEls = [firstLabel, firstInput, lastLabel, lastInput,
                 cityLabelA, cityInputA, cityLabelB, cityInputB,
                 wrapLabel, unlabeled];
const shadowRoot = makeEl('DIV', {}, formEls);
const host = makeEl('DIV', { id: 'interop-outlet' });
host.shadowRoot = shadowRoot;
// A light-DOM input: an id lookup must descend into the shadow root for a
// shadow-only id rather than stopping at the light DOM.
const lightDup = makeEl('INPUT', { id: 'light-dup-text', type: 'text' });

const documentStub = {
  querySelectorAll(sel) { return walk(fileRoot, []).filter((n) => matches(n, sel)); },
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
};
const fileRoot = makeEl('DIV', {}, [host, lightDup]);

global.window = { CSS: { escape: (s) => String(s) } };
global.CSS = global.window.CSS;

const run = vm.runInThisContext(
  '(function (document) { ' + expr + '; return { label: __wvpDeepFindLabel, byId: __wvpDeepFindById }; })',
  { filename: 'deep_search.js' }
);
const deep = run(documentStub);

const first = deep.label('First name');
const firstOk = first === firstInput && first !== lightDup;
const wsOk = deep.label('  first\nNAME ') === firstInput;   // case + whitespace forgiven
const lastOk = deep.label('Last name') === lastInput;
const wrapOk = deep.label('Phone number') === wrapInput;
const ambiguousOk = deep.label('City') === null;            // 2 claimants -> null
const missOk = deep.label('Nonexistent') === null;
const emptyOk = deep.label('') === null && deep.label(null) === null;
const byIdOk = deep.byId('elem-jobB-2-text') === lastInput; // descends into the root

const ok = firstOk && wsOk && lastOk && wrapOk && ambiguousOk && missOk &&
  emptyOk && byIdOk;
console.log(JSON.stringify({
  firstOk, wsOk, lastOk, wrapOk, ambiguousOk, missOk, emptyOk, byIdOk, ok,
}));
process.exit(ok ? 0 : 1);
