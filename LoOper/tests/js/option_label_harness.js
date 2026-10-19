/**
 * option_label_harness.js - drives actions.JS_FOREGROUND's __wvpOptionLabel
 * under node against the REAL LinkedIn Easy Apply DOM shape.
 *
 * A radio option's accessible `aria-label` is the GROUP'S QUESTION on every
 * option input (LinkedIn sets it that way), and the visible option text is a
 * plain text block NEXT TO the input (the input's own `<label for>` is the
 * empty radio circle).  A resolver that trusts aria-label first therefore
 * offers the QUESTION as every option, `_ff_merge_choices` dedupes the list to
 * one entry that IS the question, and the model is asked to pick from a single
 * bogus option - which is how a multi-choice question got the question written
 * into it as its "answer".
 *
 * The snippet is extracted from actions.py (single source of truth) and run
 * against a minimal fake DOM.  Exits 0 with a JSON summary on success,
 * non-zero on failure.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const py = fs.readFileSync(
  path.join(__dirname, '..', '..', 'player', 'web', 'actions.py'),
  'utf8'
);
function block(name) {
  const m = py.match(new RegExp(name + ' = """([\\s\\S]*?)"""'));
  if (!m) {
    console.error(name + ' not found in actions.py');
    process.exit(2);
  }
  return m[1].trim().replace(/;\s*$/, '');
}
const expr = block('JS_DEEP_SEARCH') + ';\n' + block('JS_FOREGROUND');

// ---- minimal DOM --------------------------------------------------------
function makeText(value) {
  return { nodeType: 3, nodeValue: value, parentElement: null };
}

function textOf(node) {
  if (!node) return '';
  if (node.nodeType === 3) return node.nodeValue;
  if (node.nodeType !== 1) return '';
  return (node.childNodes || []).map(textOf).join('');
}

function makeEl(tag, attrs, children) {
  const el = {
    nodeType: 1,
    tagName: tag,
    childNodes: [],
    parentElement: null,
    _attrs: attrs || {},
    id: (attrs && attrs.id) || '',
    getAttribute(n) { return this._attrs[n] != null ? this._attrs[n] : null; },
  };
  (children || []).forEach((k) => { k.parentElement = el; });
  el.childNodes = children || [];
  el.contains = (t) => {
    let n = t;
    while (n) { if (n === el) return true; n = n.parentElement; }
    return false;
  };
  el.querySelectorAll = (sel) => {
    const want = String(sel).split(',').map((s) => s.trim().toLowerCase());
    const out = [];
    (function walk2(node) {
      (node.childNodes || []).forEach((c) => {
        if (c.nodeType !== 1) return;
        if (want.indexOf(c.tagName.toLowerCase()) >= 0) out.push(c);
        walk2(c);
      });
    })(el);
    return out;
  };
  el.querySelector = (sel) => el.querySelectorAll(sel)[0] || null;
  el.closest = (sel) => {
    let n = el;
    while (n && n.nodeType === 1) {
      if (sel === 'label' && (n.tagName || '').toLowerCase() === 'label') return n;
      n = n.parentElement;
    }
    return null;
  };
  Object.defineProperty(el, 'textContent', { get() { return textOf(el); } });
  Object.defineProperty(el, 'innerText', { get() { return textOf(el); } });
  return el;
}

// The LinkedIn Easy Apply shape, per option:
//   <div class=row><div><input aria-label=QUESTION><label for=..></label></div>
//     <div><p>the option text</p></div></div>
function optionRow(id, optionText) {
  const input = makeEl('INPUT', {
    type: 'radio', id: id, name: 'radio-group-g1',
    'aria-label': '¿Cuál es tu nivel de inglés?',
  });
  const circle = makeEl('LABEL', { 'for': id }, []);
  const wrap = makeEl('DIV', {}, [input, circle]);
  const textBlock = makeEl('DIV', {}, [makeEl('P', {}, [makeText(optionText)])]);
  return { row: makeEl('DIV', {}, [wrap, textBlock]), input: input };
}

const basic = optionRow('_r_66_', 'Básico: conozco la sintaxis');
const inter = optionRow('_r_5j_', 'Intermedio: he entrenado modelos');
const group = makeEl('DIV', {}, [basic.row, inter.row]);

// An ARIA-only widget: NO visible text of its own, aria-label IS the option.
const ariaOnly = makeEl('DIV', { role: 'radio', 'aria-label': 'Yes' }, []);

const root = makeEl('DIV', {}, [group, ariaOnly]);

const documentStub = {
  querySelectorAll(sel) {
    const attr = /^([a-z0-9]*)\[([A-Za-z0-9_-]+)=["']?([^"']*)["']?\]$/.exec(sel);
    const out = [];
    (function walk2(node) {
      (node.childNodes || []).forEach((c) => {
        if (c.nodeType !== 1) return;
        if (attr &&
            (!attr[1] || c.tagName.toLowerCase() === attr[1]) &&
            c.getAttribute(attr[2]) === attr[3]) out.push(c);
        walk2(c);
      });
    })(root);
    return out;
  },
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
  getElementById(id) {
    let hit = null;
    (function walk2(node) {
      (node.childNodes || []).forEach((c) => {
        if (c.nodeType !== 1) return;
        if (c.id === id) hit = c;
        walk2(c);
      });
    })(root);
    return hit;
  },
};

global.window = { CSS: { escape: (s) => String(s) } };
global.CSS = global.window.CSS;
global.document = documentStub;

const factory = vm.runInThisContext(
  '(function () { ' + expr + '; return __wvpOptionLabel; })',
  { filename: 'foreground.js' }
);
const run = factory();

// The option's OWN text wins over the group question on its aria-label.
const ownTextOk = run(basic.input) === 'Básico: conozco la sintaxis';
const siblingOk = run(inter.input) === 'Intermedio: he entrenado modelos';
// The question NEVER leaks in as an option.
const noQuestionOk = run(basic.input).indexOf('nivel de inglés') < 0;
// An ARIA-only widget still resolves from its aria-label.
const ariaOk = run(ariaOnly) === 'Yes';

const ok = ownTextOk && siblingOk && noQuestionOk && ariaOk;
console.log(JSON.stringify({ ownTextOk, siblingOk, noQuestionOk, ariaOk, ok }));
process.exit(ok ? 0 : 1);
