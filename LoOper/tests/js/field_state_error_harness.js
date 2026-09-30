'use strict';
/**
 * field_state_error_harness.js - drives actions.JS_FIELD_STATE's pageError().
 *
 * A framework-rendered rejection never sets el.validity.valid, so the repair
 * pass reads the page's own invalid MARK.  The trigger is the page's STRUCTURE
 * and WIRING - never the message wording - so it holds in any language and no
 * per-locale keyword list has to grow:
 *
 *   1. native constraint validity            (browser-localized message)
 *   2. aria-invalid="true"                   (state, no text needed)
 *   3. aria-errormessage / aria-describedby  (the field names its own message)
 *   4. a nearby error/invalid-classed node   (channel, any language)
 *   5. a bare role="alert" node              (LAST resort -> keyword list)
 *
 * The message text is only carried as a HINT for the repair prompt.
 */
const fs = require('fs');
const path = require('path');

const py = fs.readFileSync(
  path.join(__dirname, '..', '..', 'player', 'web', 'actions.py'), 'utf8'
);
const m = py.match(/JS_FIELD_STATE = JS_DEEP_SEARCH \+ JS_FOREGROUND \+ """([\s\S]*?)"""/);
if (!m) { console.error('JS_FIELD_STATE not found'); process.exit(2); }
const body = m[1];

const reSrc = body.match(/var ERROR_TEXT_RE = (\/[\s\S]*?\/i);/)[1];
// errorText + isErrorChannel + associatedError + pageError, up to state().
const helpers = body.match(
  /function errorText\(nodeEl\) \{[\s\S]*?(?=\n  function state\()/
)[0];
// One scope for the regex, the CSS-id escape and the helpers.
const pageError = new Function(
  'var __wvpCssEsc = function (s) { return String(s); };\n' +
  'var ERROR_TEXT_RE = ' + reSrc + ';\n' + helpers + '\nreturn pageError;'
)();

function mk(attrs, text, kids) {
  const n = {
    textContent: text || '',
    className: (attrs && attrs.__class) || '',
    id: (attrs && attrs.__id) || '',
    getAttribute(name) { return attrs && attrs[name] != null ? attrs[name] : null; },
    querySelectorAll() { return kids || []; },
    querySelector(sel) {
      const id = sel.charAt(0) === '#' ? sel.slice(1) : sel;
      return (kids || []).filter((k) => k.id === id)[0] || null;
    },
    parentElement: (attrs && attrs.__parent) || null,
    ownerDocument: null,
    getRootNode() { return this.ownerDocument; },
  };
  return n;
}
function doc(registry) {
  return {
    querySelector(sel) {
      const id = sel.charAt(0) === '#' ? sel.slice(1) : sel;
      return (registry || {})[id] || null;
    },
  };
}
// A field whose NEARBY ancestor holds the given nodes (structural walk).
function fieldNearby(kids, attrs) {
  const ancestor = mk({}, '', kids);
  const field = mk(Object.assign({ __parent: ancestor }, attrs || {}), '');
  field.ownerDocument = doc({});
  return field;
}
// A field that NAMES its message through ARIA (association walk).
function fieldWired(attrs, registry) {
  const field = mk(attrs, '');
  field.ownerDocument = doc(registry);
  return field;
}

const value = 'USD 4,000+ per month (gross)';

const localized = pageError(                       // 4. error class, Spanish
  fieldNearby([mk({ __class: 'artdeco-inline-feedback--error' }, 'Introduce una respuesta válida')]),
  value,
);
const ariaInvalid = pageError(                     // 2. aria-invalid, Spanish
  fieldNearby([mk({}, 'Introduce un número válido')], { 'aria-invalid': 'true' }),
  value,
);
const wiredErrormessage = pageError(               // 3. aria-errormessage names it
  fieldWired({ 'aria-errormessage': 'msg1' },
             { msg1: mk({}, 'Introduce la remuneración pretendida') }),
  value,
);
const wiredDescribedby = pageError(                // 3b. describedby + error channel
  fieldWired({ 'aria-describedby': 'msg2' },
             { msg2: mk({ __class: 'form-error' }, 'Introduce una cifra válida') }),
  value,
);
const helperText = pageError(                      // 3c. describedby WITHOUT an error channel = helper text
  fieldWired({ 'aria-describedby': 'help1' },
             { help1: mk({}, 'Introduce your answer in your own words') }),
  value,
);
const monthHeader = pageError(                     // 5. weak live region, not feedback
  fieldNearby([mk({ role: 'alert' }, 'September 2026')]),
  value,
);
const english = pageError(                         // 5. weak live region, real feedback
  fieldNearby([mk({ role: 'alert' }, 'Please enter a valid answer')]),
  value,
);
const ownValue = pageError(                        // the field's own value is not feedback
  fieldNearby([mk({ __class: 'error' }, value)]),
  value,
);

const ok = localized.length > 0 && ariaInvalid.length > 0 &&
  wiredErrormessage.length > 0 && wiredDescribedby.length > 0 &&
  helperText === '' && monthHeader === '' && english.length > 0 && ownValue === '';
console.log(JSON.stringify({
  localized, ariaInvalid, wiredErrormessage, wiredDescribedby,
  helperText, monthHeader, english, ownValue, ok,
}));
process.exit(ok ? 0 : 1);
