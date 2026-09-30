/**
 * recorder_hover_harness.js - drives recorder.js's hover capture under node.
 *
 * Minimal DOM/global stub so the in-page recorder runs headless, then:
 *   1. plain pointer movement must emit NO hover (dwell capture removed);
 *   2. Right Ctrl + move + release must emit exactly ONE hover on the element
 *      under the cursor;
 *   3. ControlRight must never become a key action.
 *
 * Exits 0 with a JSON summary on success, non-zero on failure.
 */
'use strict';
const fs = require('fs');
const path = require('path');

const listeners = {};

function addL(type, fn) {
  (listeners[type] = listeners[type] || []).push(fn);
}

const bodyEl = { nodeType: 1, tagName: 'BODY' };
const htmlEl = { nodeType: 1, tagName: 'HTML', appendChild() {} };

const documentStub = {
  body: bodyEl,
  documentElement: htmlEl,
  title: 'Test',
  activeElement: null,
  addEventListener: addL,
  createElement: () => ({ setAttribute() {}, style: {}, appendChild() {}, parentNode: null }),
  elementFromPoint: () => null,
};

const windowStub = {
  self: null,
  top: null,
  addEventListener: addL,
  document: documentStub,
  sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  innerWidth: 1440,
  innerHeight: 773,
  __wvpLoc: {
    now: () => 1000,
    locatorFor: (el) => ({
      tag: ((el && el.tagName) || 'div').toLowerCase(),
      css: (el && el.__structuralCss) || null,
    }),
    framePathFromTop: () => ({ path: [], crossOrigin: false }),
  },
};
windowStub.self = windowStub;
windowStub.top = windowStub;

global.window = windowStub;
global.document = documentStub;
global.location = { href: 'https://example.com/' };
global.sessionStorage = windowStub.sessionStorage;
global.setInterval = () => 0;
global.setTimeout = () => 0;
global.clearTimeout = () => {};

const src = fs.readFileSync(
  path.join(__dirname, '..', '..', 'player', 'web', 'inject', 'recorder.js'),
  'utf8'
);
// Run the trusted local recorder source in this context (vm, not eval).
require('vm').runInThisContext(src, { filename: 'recorder.js' });

function fire(type, ev) {
  (listeners[type] || []).forEach((fn) => fn(ev));
}
function events() {
  return windowStub.__webversionpw_events || [];
}

// 1) Plain pointer movement / mouseover emits NO hover.
fire('pointermove', { clientX: 10, clientY: 10 });
fire('mouseover', { target: { nodeType: 1, tagName: 'DIV' }, clientX: 10, clientY: 10 });
const afterPlain = events().filter((e) => e.type === 'hover').length;

// 2) Right Ctrl hold + move + release emits one hover on the element under the
//    cursor (deepElementFromPoint target).
const target = { nodeType: 1, tagName: 'BUTTON' };
documentStub.elementFromPoint = () => target;
fire('keydown', { key: 'Control', code: 'ControlRight', clientX: 1, clientY: 1 });
fire('pointermove', { clientX: 100, clientY: 100 });
fire('keyup', { key: 'Control', code: 'ControlRight' });

const hovers = events().filter((e) => e.type === 'hover');

// 3) ControlRight is never a key action (down or up).
const ctrlKeyActions = events().filter(
  (e) => e.type === 'key' && (e.key === 'Control' || e.code === 'ControlRight')
);

// ---- Insert marks a repeating element ("kind") --------------------------
function descendants(root) {
  const out = [];
  (function walk(n) {
    (n.children || []).forEach((c) => { out.push(c); walk(c); });
  })(root);
  return out;
}

function matchSimple(pool, sel) {
  const attrVal = /^([a-z0-9]*)\[([A-Za-z0-9_-]+)=["']([^"']*)["']\]$/.exec(sel);
  if (attrVal) {
    const tag = attrVal[1];
    const name = attrVal[2];
    const val = attrVal[3];
    return pool.filter((el) => {
      if (tag && (el.tagName || '').toLowerCase() !== tag) return false;
      return el.getAttribute(name) === val;
    });
  }
  const attr = /^([a-z0-9]*)\[([A-Za-z0-9_-]+)\]$/.exec(sel);
  if (attr) {
    const tag = attr[1];
    const name = attr[2];
    return pool.filter((el) => {
      if (tag && (el.tagName || '').toLowerCase() !== tag) return false;
      return el.getAttributeNames().indexOf(name) >= 0;
    });
  }
  const idm = /^([a-z0-9]*)#([A-Za-z0-9_-]+)$/.exec(sel);
  if (idm) {
    const tag = idm[1];
    const idv = idm[2];
    return pool.filter((el) => {
      if (tag && (el.tagName || '').toLowerCase() !== tag) return false;
      return el.id === idv;
    });
  }
  const m = /^([a-z0-9]*)((?:\.[A-Za-z0-9_-]+)*)$/.exec(sel);
  if (!m) return [];
  const tag = m[1];
  const cls = m[2] ? m[2].split('.').filter(Boolean) : [];
  return pool.filter((el) => {
    if (tag && (el.tagName || '').toLowerCase() !== tag) return false;
    return cls.every((c) => el.classList.indexOf(c) >= 0);
  });
}

// Supports the three selector shapes the recorder emits: a simple compound, a
// direct-child combinator (A > B) and a descendant combinator (A B).
function matchSel(pool, sel) {
  sel = String(sel).trim();
  const childIdx = sel.indexOf(' > ');
  if (childIdx >= 0) {
    const kids = [];
    matchSel(pool, sel.slice(0, childIdx)).forEach((p) =>
      (p.children || []).forEach((c) => kids.push(c)));
    return matchSel(kids, sel.slice(childIdx + 3));
  }
  const spIdx = sel.indexOf(' ');
  if (spIdx >= 0) {
    const desc = [];
    matchSel(pool, sel.slice(0, spIdx)).forEach((p) => descendants(p).forEach((c) => desc.push(c)));
    return matchSel(desc, sel.slice(spIdx + 1));
  }
  return matchSimple(pool, sel);
}

function makeEl(tag, classes, attrs, parent) {
  const el = {
    nodeType: 1,
    tagName: tag.toUpperCase(),
    id: (attrs && attrs.id) || '',
    classList: classes.slice(),
    children: [],
    parentElement: parent || null,
    getAttributeNames: () => Object.keys(attrs || {}),
    getAttribute: (n) => (attrs || {})[n] || null,
  };
  el.querySelectorAll = (sel) => matchSel(descendants(el), sel);
  el.matches = (sel) => matchSel([el], sel).length > 0;
  if (parent) (parent.children = parent.children || []).push(el);
  return el;
}
const listA = makeEl('div', ['job-card'], { 'data-job-id': 'a' });
// The stub's locatorFor reports the recorded instance locator: marking must
// PRESERVE it (only evt.entity carries the kind selector).
listA.__structuralCss = 'div.job-card';
const listB = makeEl('div', ['job-card'], { 'data-job-id': 'b' });
// LinkedIn-style list: the clicked card has NO stable classes of its own, but
// sits inside an <li> carrying a stable data attribute.
const liA = makeEl('li', [], { 'data-occludable-job-id': '1001' });
const liB = makeEl('li', [], { 'data-occludable-job-id': '1002' });
const cardA = makeEl('div', [], { role: 'button' }, liA);
const cardB = makeEl('div', [], { role: 'button' }, liB);
const allEls = [listA, listB, liA, liB, cardA, cardB];
documentStub.querySelectorAll = (sel) => matchSel(allEls, sel);

const entityBefore = events().filter((e) => e.entity).length;
fire('keydown', { key: 'Insert', code: 'Insert' });
fire('click', { target: listA, clientX: 5, clientY: 5, button: 0, ctrlKey: true });
fire('keyup', { key: 'Insert', code: 'Insert' });

const entityEvents = events().filter((e) => e.entity);
const ent = entityEvents[entityEvents.length - 1];
const entityOk =
  entityBefore === 0 &&
  !!ent &&
  ent.entity.selector === 'div[data-job-id]' &&
  ent.entity.key_attr === 'data-job-id' &&
  // The kind selector must NOT overwrite the recorded instance locator -
  // replay needs that chain as the fallback when the kind no longer matches.
  ent.locator.css === 'div.job-card' &&
  ent.modifiers.indexOf('Ctrl') < 0;
const entityKeyNoise = events().filter(
  (e) => e.type === 'key' && (e.key === 'Insert' || e.code === 'Insert')
);
const entityKeyOk = entityKeyNoise.length === 0;

// Numpad Insert reports code 'Numpad0' - the marker must match on key too.
fire('keydown', { key: 'Insert', code: 'Numpad0' });
fire('click', { target: listB, clientX: 6, clientY: 6, button: 0 });
fire('keyup', { key: 'Insert', code: 'Numpad0' });
const entityEvents2 = events().filter((e) => e.entity);
const numpadOk =
  entityEvents2.length === 2 &&
  entityEvents2[1].entity.selector === 'div[data-job-id]';

// An ARMED click (activatable target, e.g. role=button) is pushed straight
// into the live buffer - it must still carry a url, or _finalize_actions treats
// the whole session as non-page and saves it empty.
const activatable = makeEl('div', [], { role: 'button' });
const beforeArmed = events().length;
fire('pointerdown', { button: 0, target: activatable, clientX: 3, clientY: 3,
                      composedPath: () => [activatable] });
const armed = events().slice(beforeArmed).find((e) => e.type === 'click');
const armedUrlOk = !!armed && !!armed.url;

// A drag after an armed pointerdown must RETRACT the phantom click without
// throwing (armedClick.coords must exist for the movement comparison).
fire('pointermove', { clientX: 50, clientY: 50 });
const dragRetractedOk = !!armed && events().indexOf(armed) < 0;

// Hashed/unstable own classes: the kind must be found via a stable data-*
// attribute on an ancestor (LinkedIn job cards: li[data-occludable-job-id]).
fire('keydown', { key: 'Insert', code: 'Insert' });
fire('click', { target: cardA, clientX: 7, clientY: 7, button: 0 });
fire('keyup', { key: 'Insert', code: 'Insert' });
const dataEnts = events().filter(
  (e) => e.entity && e.entity.key_attr === 'data-occludable-job-id'
);
const dataEnt = dataEnts[dataEnts.length - 1];
const dataAttrOk =
  !!dataEnt && dataEnt.entity.selector === 'li[data-occludable-job-id]';

// The ARMED (pointerdown) path must ALSO honour the Insert marker: activatable
// list cards (role=button / links) never reach the click listener, so marking
// only there left entity=null for exactly the list items it is meant for.
// Distinct data-* so the armed event is unambiguous.
const itemA = makeEl('div', [], { 'data-item-id': 'x', role: 'button' });
const itemB = makeEl('div', [], { 'data-item-id': 'y', role: 'button' });
allEls.push(itemA, itemB);
fire('keydown', { key: 'Insert', code: 'Insert' });
fire('pointerdown', { button: 0, target: itemA, clientX: 9, clientY: 9,
                      composedPath: () => [itemA] });
fire('keyup', { key: 'Insert', code: 'Insert' });
const armedEnts = events().filter(
  (e) => e.type === 'click' && e.entity && e.entity.selector === 'div[data-item-id]'
);
const armedEnt = armedEnts[armedEnts.length - 1];
const armedEntityOk = armedEnts.length === 1;

// Two items sharing a class combination but carrying NO stable data-* and no
// labelled container: the SHARED classes ARE the type (the modern-SPA case).
const cls1 = makeEl('div', ['f4741eb7', 'a0abfd76']);
const cls2 = makeEl('div', ['f4741eb7', 'a0abfd76']);
allEls.push(cls1, cls2);
fire('keydown', { key: 'Insert', code: 'Insert' });
fire('click', { target: cls1, clientX: 11, clientY: 11, button: 0 });
fire('keyup', { key: 'Insert', code: 'Insert' });
const clsEnts = events().filter(
  (e) => e.type === 'click' && e.entity &&
         e.entity.selector === 'div.f4741eb7.a0abfd76'
);
const classTypeOk = clsEnts.length === 1;

// Rows with a DIFFERING per-instance class but a SHARED class pair (the LinkedIn
// job-card shape): the kind must be the shared pair covering the whole list,
// NOT a narrow single class that matches only a couple of rows.
const rowA = makeEl('div', ['r1', 'shared', 'common']);
const rowB = makeEl('div', ['r2', 'shared', 'common']);
const rowC = makeEl('div', ['r3', 'shared', 'common']);
allEls.push(rowA, rowB, rowC);
fire('keydown', { key: 'Insert', code: 'Insert' });
fire('click', { target: rowA, clientX: 13, clientY: 13, button: 0 });
fire('keyup', { key: 'Insert', code: 'Insert' });
const sharedEnts = events().filter(
  (e) => e.type === 'click' && e.entity &&
         e.entity.selector === 'div.shared.common'
);
const sharedComboOk = sharedEnts.length === 1;

// A list container where the clicked row carries a STATE token the other rows
// do not (the LinkedIn 'selected' class): the kind must be the class pair shared
// by the whole list, grounded on the clicked element's siblings - not the
// state-token combo that only the clicked row has.
const listBox = makeEl('div', ['listbox'], {});
const sibA = makeEl('div', ['state', 'alpha', 'beta'], {}, listBox);
const sibB = makeEl('div', ['alpha', 'beta'], {}, listBox);
const sibC = makeEl('div', ['alpha', 'beta'], {}, listBox);
allEls.push(listBox, sibA, sibB, sibC);
fire('keydown', { key: 'Insert', code: 'Insert' });
fire('click', { target: sibA, clientX: 15, clientY: 15, button: 0 });
fire('keyup', { key: 'Insert', code: 'Insert' });
const sibEnts = events().filter(
  (e) => e.type === 'click' && e.entity && e.entity.selector === 'div.alpha.beta'
);
const siblingListOk = sibEnts.length === 1;

// A generic layout class SHARED with an ancestor must be rejected: a kind that
// matches an ancestor resolves to the container (earlier in document order) and
// clicks IT instead of the item.
const boxOuter = makeEl('div', ['page'], {});
const box = makeEl('div', ['gen', 'box'], {}, boxOuter);
const gRowA = makeEl('div', ['gen', 'box', 'zeta1', 'zeta2'], {}, box);
const gRowB = makeEl('div', ['gen', 'box', 'zeta1', 'zeta2'], {}, box);
allEls.push(boxOuter, box, gRowA, gRowB);
fire('keydown', { key: 'Insert', code: 'Insert' });
fire('click', { target: gRowA, clientX: 17, clientY: 17, button: 0 });
fire('keyup', { key: 'Insert', code: 'Insert' });
const ancEnts = events().filter(
  (e) => e.type === 'click' && e.entity &&
         e.entity.selector === 'div.gen.box.zeta1.zeta2'
);
const ancestorRejectOk = ancEnts.length === 1;

// The container SHARES the row classes, so a class combo covering all rows also
// matches the container.  The STRUCTURAL kind (parent locator > same-type child)
// targets the siblings only and can never select the container.
const contBox = makeEl('div', ['container', 'shared'], {});
contBox.__structuralCss = 'div.container.shared';
const cRowA = makeEl('div', ['container', 'shared', 'x1'], {}, contBox);
const cRowB = makeEl('div', ['container', 'shared', 'x2'], {}, contBox);
const cRowC = makeEl('div', ['container', 'shared'], {}, contBox);
allEls.push(contBox, cRowA, cRowB, cRowC);
fire('keydown', { key: 'Insert', code: 'Insert' });
fire('click', { target: cRowA, clientX: 19, clientY: 19, button: 0 });
fire('keyup', { key: 'Insert', code: 'Insert' });
const structEnts = events().filter(
  (e) => e.type === 'click' && e.entity &&
         e.entity.selector === 'div.container.shared > div'
);
const structuralKindOk = structEnts.length === 1;

// The LinkedIn job-card shape: a GLOBAL NAV BAR carrying data-testid (the
// page-wide hook whose first match in document order is a nav element) sits in
// a different subtree, while the clicked card sits in a container with its
// siblings, under an OUTER wrapper that also carries a generic data-*.  A
// descendant selector on that outer anchor matches the whole nested tree (outer
// div FIRST) - which is what made replay click outer divs and never reach the
// list items - and the page-wide presence hook would resolve to the nav bar.
// The kind must be the card's OWN SIBLINGS, and neither of those may be used.
const pageShell = makeEl('div', ['pageshell'], {});
const navBar = makeEl('div', ['nav'], { 'data-testid': 'globalnav' }, pageShell);
const navBtn = makeEl('div', ['navbtn'], {}, navBar);
const jobOuter = makeEl('div', ['results'],
                        { 'data-display-contents': 'true', 'data-testid': 'results' });
const jobList = makeEl('div', ['joblist'], {}, jobOuter);
jobList.__structuralCss = 'div.joblist';
const jobA = makeEl('div', ['ec39a3eb', 'abd3f6f8'], {}, jobList);
const jobB = makeEl('div', ['ec39a3eb', 'abd3f6f8'], {}, jobList);
const jobC = makeEl('div', ['ec39a3eb', 'abd3f6f8'], {}, jobList);
makeEl('div', ['title'], {}, jobB);  // inner divs a descendant match would hit
makeEl('div', ['meta'], {}, jobB);
allEls.push(pageShell, navBar, navBtn, jobOuter, jobList, jobA, jobB, jobC);
fire('keydown', { key: 'Insert', code: 'Insert' });
fire('click', { target: jobB, clientX: 27, clientY: 27, button: 0 });
fire('keyup', { key: 'Insert', code: 'Insert' });
const jobEnt = events().filter((e) => e.type === 'click' && e.entity).pop();
const brothersFirstOk =
  !!jobEnt &&
  jobEnt.entity.selectors[0] === 'div.joblist > div' &&
  jobEnt.entity.selectors.indexOf('div[data-display-contents="true"] div') < 0 &&
  jobEnt.entity.selectors.indexOf('div[data-testid]') < 0;

// A click landing INSIDE a row (the card's own title div) must resolve to the
// ROW set, not to the clicked element's inner siblings: the card's inner divs
// do not share classes while the cards do, so the row level is one above.
const innerTitle = makeEl('div', ['cardtitle'], {}, jobC);
allEls.push(innerTitle);
fire('keydown', { key: 'Insert', code: 'Insert' });
fire('click', { target: innerTitle, clientX: 29, clientY: 29, button: 0 });
fire('keyup', { key: 'Insert', code: 'Insert' });
const innerEnt = events().filter((e) => e.type === 'click' && e.entity).pop();
const rowFromInsideOk =
  !!innerEnt && innerEnt.entity.selectors[0] === 'div.joblist > div';

// A stable container anchor that is NOT unique (LinkedIn renders more than one
// data-testid="lazy-column") must not scope the rows: the selector would span
// every such container, so the first match sits in a different section and the
// list never belongs to the clicked item.  The container's own unique path wins.
const colA = makeEl('div', ['col', 'page-a'], { 'data-testid': 'lazy-column' });
const colB = makeEl('div', ['col', 'page-b'], { 'data-testid': 'lazy-column' });
colA.__structuralCss = 'div.col.page-a';
colB.__structuralCss = 'div.col.page-b';
const cardOne = makeEl('div', ['cardz'], {}, colA);
const cardTwo = makeEl('div', ['cardz'], {}, colA);
const otherOne = makeEl('div', ['cardz'], {}, colB);
const otherTwo = makeEl('div', ['cardz'], {}, colB);
allEls.push(colA, colB, cardOne, cardTwo, otherOne, otherTwo);
fire('keydown', { key: 'Insert', code: 'Insert' });
fire('click', { target: cardTwo, clientX: 33, clientY: 33, button: 0 });
fire('keyup', { key: 'Insert', code: 'Insert' });
const colEnt = events().filter((e) => e.type === 'click' && e.entity).pop();
const uniqueContainerOk =
  !!colEnt &&
  colEnt.entity.selectors[0] === 'div.col.page-a > div' &&
  colEnt.entity.selectors.indexOf('div[data-testid="lazy-column"] > div') < 0;

// A marked click whose only page-wide candidates are SCATTERED derives NOTHING
// (entity stays null, so replay keeps the recorded locator) - and the recorder
// must still explain WHY, since the recording log prints the diagnosis.
const shell3 = makeEl('div', ['shell3'], {});
const nav3 = makeEl('div', ['nav3'], { 'data-testid': 'nav3' }, shell3);
const wide3 = makeEl('div', ['wide3'], { 'data-testid': 'wide3' });
const plainBox = makeEl('div', ['plainbox'], {}, wide3);
const plainOne = makeEl('div', ['plainone'], {}, plainBox);
allEls.push(shell3, nav3, wide3, plainBox, plainOne);
const beforeNoRow = events().filter((e) => e.entity).length;
fire('keydown', { key: 'Insert', code: 'Insert' });
fire('click', { target: plainOne, clientX: 31, clientY: 31, button: 0 });
fire('keyup', { key: 'Insert', code: 'Insert' });
const afterNoRow = events().filter((e) => e.entity).length;
const mark = windowStub.__wvpEntityMark || {};
const noRowDiagOk =
  afterNoRow === beforeNoRow &&
  mark.selector === null &&
  Array.isArray(mark.diag) &&
  mark.diag.length > 0 &&
  mark.diag.some((d) => String(d).indexOf('multi-parent') >= 0);

// TWO Insert-marked clicks define the siblings (verified, not guessed): the
// second click carries the kind and the ts of the first, which is dropped at
// save time - so one repeating action iterates the rows.  The two clicks are
// two DIFFERENT rows of the same list.
const pairList = makeEl('div', ['pairlist'], {});
pairList.__structuralCss = 'div.pairlist';
const pRowOne = makeEl('div', ['pb1', 'pcommon'], {}, pairList);
const pRowTwo = makeEl('div', ['pb2', 'pcommon'], {}, pairList);
const pRowThree = makeEl('div', ['pb3', 'pcommon'], {}, pairList);
allEls.push(pairList, pRowOne, pRowTwo, pRowThree);
const pairBase = events().length;
fire('keydown', { key: 'Insert', code: 'Insert' });
fire('click', { target: pRowOne, clientX: 41, clientY: 41, button: 0 });
fire('click', { target: pRowThree, clientX: 42, clientY: 42, button: 0 });
fire('keyup', { key: 'Insert', code: 'Insert' });
const pairClicks = events().slice(pairBase).filter((e) => e.type === 'click');
const pairMarked = pairClicks.filter((e) => e.entity && e.entity.supersedes_ts);
const pairOk =
  pairClicks.length === 2 &&
  pairMarked.length === 1 &&
  pairMarked[0].entity.selector === 'div.pairlist > div.pcommon' &&
  pairMarked[0].entity.supersedes_ts === pairClicks[0].ts &&
  pairMarked[0].entity.supersedes_type === 'click' &&
  pairMarked[0].entity.pair_rows === 3;

// Marked clicks that are NOT siblings must NOT pair (nothing is dropped): the
// two elements have no common container, so each keeps its own kind.
const unrelA = makeEl('div', ['unrela'], {});
const unrelB = makeEl('div', ['unrelb'], {});
allEls.push(unrelA, unrelB);
const unrelBase = events().length;
fire('keydown', { key: 'Insert', code: 'Insert' });
fire('click', { target: unrelA, clientX: 43, clientY: 43, button: 0 });
fire('click', { target: unrelB, clientX: 44, clientY: 44, button: 0 });
fire('keyup', { key: 'Insert', code: 'Insert' });
const unrelClicks = events().slice(unrelBase).filter((e) => e.type === 'click');
const unrelDiag = (windowStub.__wvpEntityMark || {}).pair_diag;
const noPairOk =
  unrelClicks.length === 2 &&
  unrelClicks.every((e) => !(e.entity && e.entity.supersedes_ts)) &&
  !!unrelDiag;

// A row wrapper with `display: contents` (LinkedIn's data-display-contents) has
// NO BOX, so replaying a click on it fails with "element not interactable: has
// no size and location".  The row must be the CLICKABLE element inside it - and
// with one wrapper PER row the rows are not siblings of each other, which is
// what the parent path expresses.
const zList = makeEl('div', ['zerolist'], {});
zList.__structuralCss = 'div.zerolist';
const zWrapOne = makeEl('div', ['zwrap'], {}, zList);
const zWrapTwo = makeEl('div', ['zwrap'], {}, zList);
zWrapOne.getBoundingClientRect = () => ({ width: 0, height: 0 });
zWrapTwo.getBoundingClientRect = () => ({ width: 0, height: 0 });
const zRowOne = makeEl('div', ['zcard'], {}, zWrapOne);
const zRowTwo = makeEl('div', ['zcard'], {}, zWrapTwo);
const zRowThree = makeEl('div', ['zcard'], {}, zWrapTwo);
allEls.push(zList, zWrapOne, zWrapTwo, zRowOne, zRowTwo, zRowThree);
const zBase = events().length;
fire('keydown', { key: 'Insert', code: 'Insert' });
fire('click', { target: zRowOne, clientX: 45, clientY: 45, button: 0 });
fire('click', { target: zRowTwo, clientX: 46, clientY: 46, button: 0 });
fire('keyup', { key: 'Insert', code: 'Insert' });
const zEnt = events().slice(zBase)
  .filter((e) => e.type === 'click' && e.entity && e.entity.supersedes_ts).pop();
const zeroBoxRowOk =
  !!zEnt &&
  zEnt.entity.selector === 'div.zerolist > div.zwrap > div.zcard' &&
  zEnt.entity.pair_rows === 3;

// FORM CONTROLS.  A radio group inside a labelled form must derive a
// CONTAINER-SCOPED kind keyed by the group name - the form bug in the field:
// the old recorder returned a chain of hashed CSS-module classes that matched
// nothing on replay, so the repeating cursor never advanced.
const form = makeEl('form', [], { id: 'survey' });
const r1 = makeEl('input', [], { name: 'q1', type: 'radio' }, form);
const r2 = makeEl('input', [], { name: 'q1', type: 'radio' }, form);
const r3 = makeEl('input', [], { name: 'q1', type: 'radio' }, form);
allEls.push(form, r1, r2, r3);
fire('keydown', { key: 'Insert', code: 'Insert' });
fire('click', { target: r1, clientX: 21, clientY: 21, button: 0 });
fire('keyup', { key: 'Insert', code: 'Insert' });
const formEnt = events().filter((e) => e.type === 'click' && e.entity).pop();
const formScopeOk =
  !!formEnt &&
  formEnt.entity.selectors[0] === 'form#survey input[name="q1"]' &&
  // Stable candidates only - no hashed class leaked into the list.
  formEnt.entity.selectors.every((s) => s.indexOf('.') < 0);

// Text inputs with DISTINCT names but a shared type: the kind is the stable
// GLOBAL type (act on each field in order), because no per-element identity
// matches the whole set.
const t1 = makeEl('input', [], { name: 'a', type: 'text' });
const t2 = makeEl('input', [], { name: 'b', type: 'text' });
const t3 = makeEl('input', [], { name: 'c', type: 'text' });
allEls.push(t1, t2, t3);
fire('keydown', { key: 'Insert', code: 'Insert' });
fire('click', { target: t1, clientX: 23, clientY: 23, button: 0 });
fire('keyup', { key: 'Insert', code: 'Insert' });
const textEnt = events().filter((e) => e.type === 'click' && e.entity).pop();
const textOrderOk = !!textEnt && textEnt.entity.selectors[0] === 'input[type="text"]';

// Toggles (role=switch) inside a labelled panel: the kind is the scoped role
// selector, so every switch in the panel is acted on in order.
const panel = makeEl('div', [], { id: 'settings' });
const sw1 = makeEl('div', [], { role: 'switch' }, panel);
const sw2 = makeEl('div', [], { role: 'switch' }, panel);
allEls.push(panel, sw1, sw2);
fire('keydown', { key: 'Insert', code: 'Insert' });
fire('click', { target: sw1, clientX: 25, clientY: 25, button: 0 });
fire('keyup', { key: 'Insert', code: 'Insert' });
const swEnt = events().filter((e) => e.type === 'click' && e.entity).pop();
const switchScopeOk =
  !!swEnt && swEnt.entity.selectors[0] === 'div#settings div[role="switch"]';

const ok =
  afterPlain === 0 &&
  hovers.length === 1 &&
  ctrlKeyActions.length === 0 &&
  hovers[0].locator &&
  hovers[0].locator.tag === 'button' &&
  entityOk &&
  entityKeyOk &&
  numpadOk &&
  armedUrlOk &&
  dataAttrOk &&
  armedEntityOk &&
  classTypeOk &&
  dragRetractedOk &&
  sharedComboOk &&
  siblingListOk &&
  ancestorRejectOk &&
  structuralKindOk &&
  brothersFirstOk &&
  rowFromInsideOk &&
  uniqueContainerOk &&
  noRowDiagOk &&
  pairOk &&
  noPairOk &&
  zeroBoxRowOk &&
  formScopeOk &&
  textOrderOk &&
  switchScopeOk;

console.log(JSON.stringify({
  afterPlain,
  hoverCount: hovers.length,
  ctrlKeyActions: ctrlKeyActions.length,
  hoverTag: hovers[0] && hovers[0].locator && hovers[0].locator.tag,
  entitySelector: ent && ent.entity && ent.entity.selector,
  entityKeyAttr: ent && ent.entity && ent.entity.key_attr,
  entityModifiers: ent && ent.modifiers,
  entityKeyNoise: entityKeyNoise.length,
  numpadEntities: entityEvents2.length,
  armedUrl: armed && armed.url,
  dataEntitySelector: dataEnt && dataEnt.entity.selector,
  armedEntitySelector: armedEnt && armedEnt.entity && armedEnt.entity.selector,
  classTypeSelector: clsEnts.length && clsEnts[0].entity && clsEnts[0].entity.selector,
  sharedComboSelector: sharedEnts.length && sharedEnts[0].entity && sharedEnts[0].entity.selector,
  siblingListSelector: sibEnts.length && sibEnts[0].entity && sibEnts[0].entity.selector,
  ancestorRejectSelector: ancEnts.length && ancEnts[0].entity && ancEnts[0].entity.selector,
  structuralKindSelector: structEnts.length && structEnts[0].entity && structEnts[0].entity.selector,
  brothersFirstSelector: jobEnt && jobEnt.entity && jobEnt.entity.selectors[0],
  brothersFirstList: jobEnt && jobEnt.entity && jobEnt.entity.selectors,
  rowFromInsideSelector: innerEnt && innerEnt.entity && innerEnt.entity.selectors[0],
  uniqueContainerSelector: colEnt && colEnt.entity && colEnt.entity.selectors[0],
  noRowEntity: mark.selector,
  noRowDiag: mark.diag,
  pairSelector: pairMarked.length && pairMarked[0].entity.selector,
  pairSupersedesTs: pairMarked.length && pairMarked[0].entity.supersedes_ts,
  unrelPairDiag: unrelDiag,
  zeroBoxRowSelector: zEnt && zEnt.entity.selector,
  formSelector: formEnt && formEnt.entity && formEnt.entity.selectors[0],
  textInputSelector: textEnt && textEnt.entity && textEnt.entity.selectors[0],
  switchSelector: swEnt && swEnt.entity && swEnt.entity.selectors[0],
  dragRetracted: dragRetractedOk,
  ok,
}));
process.exit(ok ? 0 : 1);
