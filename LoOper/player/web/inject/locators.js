/**
 * locators.js - locator chain extraction for the LoOper web recorder.
 *
 * Exposes window.__wvpLoc with functions that turn a DOM element into an
 * ordered chain of progressively weaker candidates (the web analogue of the
 * desktop recorder's layered tries): id -> test-id/data-* attributes -> stable
 * attribute fallbacks (name/placeholder/aria/type/href) -> visible-text
 * matches (link text / text XPath / role+text / label) -> unique CSS ->
 * ancestor-anchored CSS -> classes -> structural XPath -> full XPath, plus a
 * viewport geometry record for the last-resort coordinate hit-test.
 *
 * The more identity the recorder captures, the more layers the replay engine
 * can try - capture is cheap, replay misses are expensive.
 *
 * This file has NO side effects beyond defining the namespace; recorder.js
 * is concatenated after it and consumes it.
 */
(function () {
  'use strict';
  if (window.__wvpLoc) return;

  var BAD_ID = /[\s"'<>\\]/;
  // Site-authored automation hooks - collected FIRST so the chain prefers the
  // site's own stable test ids over generic data-* attributes.
  var TEST_ID_ATTRS = [
    'data-testid', 'data-test-id', 'data-test', 'data-cy', 'data-qa',
    'data-qa-id', 'data-hook', 'data-automation-id', 'data-component-id',
  ];
  // Text that looks generated (order numbers, timestamps) never makes a
  // stable text locator.
  var DYNAMIC_TEXT = /\d{4,}/;

  function now() {
    return Date.now();
  }

  function cleanText(el) {
    var t = el && el.textContent ? el.textContent : '';
    t = t.replace(/\s+/g, ' ').trim();
    return t.slice(0, 120);
  }

  function describe(el) {
    var out = { tag: (el.tagName || '').toLowerCase() };
    if (el.id) out.id = el.id;
    if (el.name) out.name = el.name;
    if (el.getAttribute) {
      var role = el.getAttribute('role');
      if (role) out.role = role;
      var href = el.getAttribute('href');
      if (href) out.href = href;
      var placeholder = el.getAttribute('placeholder');
      if (placeholder) out.placeholder = placeholder;
      var aria = el.getAttribute('aria-label');
      if (aria) out.aria_label = aria;
      var inputType = el.getAttribute('type');
      if (inputType && (el.tagName || '').toLowerCase() === 'input') out.input_type = inputType;
    }
    out.text = cleanText(el);
    return out;
  }

  /**
   * Collect up to `limit` data-* attributes: test-id hooks first (they exist
   * precisely so automation can find the element), then the rest.
   */
  function dataAttrs(el, limit) {
    limit = limit || 5;
    var out = {};
    if (!el.attributes) return out;
    for (var i = 0; i < TEST_ID_ATTRS.length; i++) {
      var hook = el.getAttribute(TEST_ID_ATTRS[i]);
      if (hook && hook.trim() && hook.trim().length <= 100) {
        out[TEST_ID_ATTRS[i]] = hook.trim();
        if (Object.keys(out).length >= limit) return out;
      }
    }
    for (var j = 0; j < el.attributes.length; j++) {
      var attr = el.attributes[j];
      if (attr.name.indexOf('data-') === 0 && attr.value) {
        var v = attr.value.trim();
        if (v && v.length <= 100) {
          out[attr.name] = v;
          if (Object.keys(out).length >= limit) break;
        }
      }
    }
    return out;
  }

  /** Shortest unique CSS selector: tag[#id].classes chain, trimmed from the left. */
  function cssChain(el) {
    var parts = [];
    var node = el;
    while (node && node.nodeType === 1 && node !== document.documentElement) {
      var seg = node.tagName.toLowerCase();
      if (node.id && !BAD_ID.test(node.id)) {
        seg += '#' + CSS.escape(node.id);
        parts.unshift(seg);
        break; // ids are unique in practice - stop the chain here
      }
      var classes = Array.prototype.slice.call(node.classList).slice(0, 3);
      if (classes.length) {
        seg += '.' + classes.map(function (c) { return CSS.escape(c); }).join('.');
      }
      parts.unshift(seg);
      node = node.parentElement;
    }
    return parts;
  }

  function uniqueCss(el) {
    var chain = cssChain(el);
    for (var start = 0; start < chain.length; start++) {
      var sel = chain.slice(start).join(' > ');
      try {
        if (document.querySelectorAll(sel).length === 1) return sel;
      } catch (e) {
        return null;
      }
    }
    return chain.length ? chain.join(' > ') : null;
  }

  function nthOfTypeIndex(node) {
    var index = 1;
    var sib = node.previousElementSibling;
    while (sib) {
      if (sib.tagName === node.tagName) index++;
      sib = sib.previousElementSibling;
    }
    return index;
  }

  /** Short structural path like /html/body/div[2]/form/button - preferred over the full one. */
  function structuralXPath(el, maxDepth) {
    maxDepth = maxDepth || 6;
    var parts = [];
    var node = el;
    var depth = 0;
    while (node && node.nodeType === 1 && node !== document.body && node !== document.documentElement && depth < maxDepth) {
      var seg = node.tagName.toLowerCase() + '[' + nthOfTypeIndex(node) + ']';
      parts.unshift(seg);
      node = node.parentElement;
      depth++;
    }
    return parts.length ? '/html/body/' + parts.join('/') : null;
  }

  /** Absolute XPath with id hints when available - the last-resort locator. */
  function fullXPath(el) {
    var parts = [];
    var node = el;
    while (node && node.nodeType === 1) {
      var seg = node.tagName.toLowerCase();
      if (node.id) {
        seg += '[@id="' + String(node.id).replace(/"/g, '\\"') + '"]';
      } else {
        seg += '[' + nthOfTypeIndex(node) + ']';
      }
      parts.unshift(seg);
      node = node.parentElement;
    }
    return '/' + parts.join('/');
  }

  /** Outermost-first list of shadow host elements enclosing `el`, if any. */
  function shadowHosts(el) {
    var hosts = [];
    var root = el.getRootNode ? el.getRootNode() : null;
    var node = el;
    while (root && root.nodeType === 11) { // DocumentFragment == shadow root
      var host = root.host;
      if (!host) break;
      hosts.unshift(host);
      root = host.getRootNode ? host.getRootNode() : null;
    }
    return hosts;
  }

  /**
   * Associated label text for form fields (label[for=id], then wrapping label).
   *
   * The label is a field's PORTABLE identity - a site's generated id/name
   * frequently embeds per-instance tokens (LinkedIn's Easy Apply ids carry the
   * job id), which rotate between forms, while the label the user reads stays
   * the same.  Root-aware: a form rendered into a shadow root (LinkedIn's
   * ``interop-outlet``) keeps its label in that SAME root, which
   * ``document.querySelectorAll`` cannot see - so the lookup starts at the
   * element's own root and only then widens to the document.
   */
  function labelTextOf(el) {
    if (!el || el.nodeType !== 1) return null;
    var tag = (el.tagName || '').toLowerCase();
    if (tag !== 'input' && tag !== 'textarea' && tag !== 'select') return null;
    var root = (el.getRootNode && el.getRootNode()) || document;
    var label = null;
    if (el.id) {
      try {
        label = root.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      } catch (e) {
        label = null;
      }
      if (!label) {
        // Shadow roots do not expose :scope-relative form association, so the
        // htmlFor walk is the fallback for a root that cannot be queried.
        var labels = root.querySelectorAll ? root.querySelectorAll('label') : [];
        for (var i = 0; i < labels.length; i++) {
          if (labels[i].htmlFor === el.id) { label = labels[i]; break; }
        }
      }
    }
    if (!label) {
      var p = (el.closest && el.closest('label')) || null;
      if (!p) {
        var q = el.parentElement;
        var guard = 0;
        while (q && q.nodeType === 1 && q.tagName.toLowerCase() !== 'label' && guard++ < 4) {
          q = q.parentElement;
        }
        if (q && q.tagName.toLowerCase() === 'label') p = q;
      }
      label = p;
    }
    if (!label) return null;
    var t = cleanText(label).trim();
    return t && t.length >= 2 && t.length <= 80 ? t : null;
  }

  /**
   * Visible-text XPath, e.g. //button[normalize-space(.)="Log in"].  Only for
   * short, quote-free, non-generated text - long labels rot, quotes need
   * concat()-escaping, and order numbers rotate between runs.
   */
  function textXPath(el) {
    var t = cleanText(el).trim();
    if (!t || t.length < 2 || t.length > 40) return null;
    if (DYNAMIC_TEXT.test(t)) return null;
    if (t.indexOf("'") >= 0 || t.indexOf('"') >= 0) return null;
    var tag = (el.tagName || '').toLowerCase();
    return '//' + tag + '[normalize-space(.)="' + t + '"]';
  }

  /** Role + visible text, e.g. //*[@role="button" and normalize-space(.)="Save"]. */
  function roleTextXPath(el) {
    var role = el.getAttribute && el.getAttribute('role');
    var t = cleanText(el).trim();
    if (!role || !t || t.length > 40) return null;
    if (t.indexOf("'") >= 0 || t.indexOf('"') >= 0) return null;
    return '//*[@role="' + role + '" and normalize-space(.)="' + t + '"]';
  }

  /**
   * Label-anchored field locator: //label[normalize-space(.)="Email"]/following::input[1].
   * Finds the field whose visible label precedes it in document order - the
   * classic login-form fallback when the input has no name/id/placeholder.
   */
  function labelXPath(el) {
    var tag = (el.tagName || '').toLowerCase();
    if (tag !== 'input' && tag !== 'textarea' && tag !== 'select') return null;
    var t = labelTextOf(el);
    if (!t || t.indexOf("'") >= 0 || t.indexOf('"') >= 0) return null;
    return '//label[normalize-space(.)="' + t + '"]/following::' + tag + '[1]';
  }

  /** Nearest ancestor with a stable id or test-id hook, or null. */
  function ancestorAnchor(el) {
    var node = el.parentElement;
    var guard = 0;
    while (node && node.nodeType === 1 &&
           node !== document.body && node !== document.documentElement &&
           guard++ < 6) {
      if (node.id && !BAD_ID.test(node.id)) return node;
      for (var i = 0; i < TEST_ID_ATTRS.length; i++) {
        if (node.getAttribute(TEST_ID_ATTRS[i])) return node;
      }
      node = node.parentElement;
    }
    return null;
  }

  /** CSS path from the anchor down to el, :nth-of-type-exact at every step. */
  function cssFrom(el, anchor) {
    var parts = [];
    var node = el;
    while (node && node.nodeType === 1 && node !== anchor) {
      parts.unshift(node.tagName.toLowerCase() + ':nth-of-type(' + nthOfTypeIndex(node) + ')');
      node = node.parentElement;
    }
    if (!parts.length) return null;
    if (anchor) {
      var a = anchor.tagName.toLowerCase();
      if (anchor.id) {
        a += '#' + CSS.escape(anchor.id);
      } else {
        // Prefer a test-id hook over classes - it is authored for automation
        // and immune to styling changes.
        var hook = null;
        for (var i = 0; i < TEST_ID_ATTRS.length; i++) {
          var hv = anchor.getAttribute(TEST_ID_ATTRS[i]);
          if (hv && hv.trim()) { hook = TEST_ID_ATTRS[i]; break; }
        }
        if (hook) {
          var hv2 = anchor.getAttribute(hook).trim().replace(/'/g, "\\'");
          a += '[' + hook + "='" + hv2 + "']";
        } else {
          var ac = Array.prototype.slice.call(anchor.classList).slice(0, 2);
          if (ac.length) a += '.' + ac.map(function (c) { return CSS.escape(c); }).join('.');
        }
      }
      parts.unshift(a);
    }
    return parts.join(' > ');
  }

  /** 0-based position among same-tag siblings sharing the same class list. */
  function siblingIndex(el) {
    var tag = el.tagName.toLowerCase();
    var cls = Array.prototype.slice.call(el.classList).sort().join('.');
    var sib = el.parentElement ? Array.prototype.slice.call(el.parentElement.children) : [];
    var count = 0;
    for (var i = 0; i < sib.length; i++) {
      var s = sib[i];
      if (s.nodeType !== 1 || s.tagName.toLowerCase() !== tag) continue;
      var sc = Array.prototype.slice.call(s.classList).sort().join('.');
      if (sc === cls) {
        if (s === el) return count;
        count++;
      }
    }
    return null;
  }

  /** 0-based position among elements matching the recorded text XPath. */
  function textMatchIndex(el, xp) {
    if (!xp) return null;
    try {
      var all = document.evaluate(xp, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
      if (all.snapshotLength < 2) return 0;
      for (var i = 0; i < all.snapshotLength; i++) {
        if (all.snapshotItem(i) === el) return i;
      }
    } catch (e) {}
    return null;
  }

  /**
   * Document-space geometry for the last-resort coordinate hit-test: the
   * element's rect translated by the scroll offsets, plus the viewport size
   * so replay can scroll it back into view and elementFromPoint() the center.
   */
  function viewportOf(el) {
    try {
      var r = el.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) return null;
      return {
        x: Math.round(r.left + window.scrollX),
        y: Math.round(r.top + window.scrollY),
        w: Math.round(r.width),
        h: Math.round(r.height),
        vw: window.innerWidth,
        vh: window.innerHeight,
      };
    } catch (e) {
      return null;
    }
  }

  /** Full locator record for an element. */
  function locatorFor(el) {
    if (!el || el.nodeType !== 1) return null;
    var d = describe(el);
    var tag = d.tag;
    var t = d.text || '';
    var tx = textXPath(el);
    var loc = {
      tag: tag,
      id: d.id || null,
      name: d.name || null,
      role: d.role || null,
      text: t || null,
      href: d.href || null,
      placeholder: d.placeholder || null,
      aria_label: d.aria_label || null,
      input_type: d.input_type || null,
      data_attrs: dataAttrs(el),
      css: uniqueCss(el),
      structural_xpath: structuralXPath(el),
      xpath: fullXPath(el),
    };
    // Visible-text layers - the biggest recovery win for links/buttons whose
    // attributes rotate between sessions.
    if (tag === 'a' && t.length >= 2 && t.length <= 80 && t.indexOf("'") < 0 && t.indexOf('"') < 0) {
      loc.link_text = t;
    }
    if (tx) {
      loc.text_xpath = tx;
      loc.text_index = textMatchIndex(el, tx);
    }
    var rtx = roleTextXPath(el);
    if (rtx) loc.role_text_xpath = rtx;
    // The label TEXT is recorded independently of the label XPath: replay's
    // in-browser (shadow-piercing) dispatch cannot evaluate XPath, so it matches
    // on the text, and a label containing a quote has no expressible XPath.
    var lt = labelTextOf(el);
    if (lt) loc.label_text = lt;
    var lx = labelXPath(el);
    if (lx) loc.label_xpath = lx;
    var classes = Array.prototype.slice.call(el.classList).slice(0, 3);
    if (classes.length) loc.classes = classes;
    if (el.getAttribute) {
      var alt = el.getAttribute('alt');
      if (alt && alt.trim() && alt.trim().length <= 60) loc.alt_text = alt.trim();
      var title = el.getAttribute('title');
      if (title && title.trim() && title.trim().length <= 60) loc.title = title.trim();
      var value = el.getAttribute('value');
      if (value && value.trim() && value.trim().length <= 60 && tag !== 'input') loc.value = value.trim();
    }
    var anchor = ancestorAnchor(el);
    var acss = cssFrom(el, anchor);
    if (acss) loc.ancestor_css = acss;
    var idx = siblingIndex(el);
    if (idx !== null) loc.index = idx;
    var vp = viewportOf(el);
    if (vp) loc.viewport = vp;
    var hosts = shadowHosts(el);
    if (hosts.length) {
      loc.shadow_hosts = hosts.map(locatorFor);
    }
    return loc;
  }

  /**
   * Same-origin iframe chain from the top document to this frame (top-down).
   * Cross-origin frames expose null window.frameElement, so we cannot build
   * the path there - the caller marks such events cross_origin_frame instead.
   */
  function framePathFromTop() {
    var path = [];
    var w = window;
    while (w && w !== window.top) {
      var fe = w.frameElement;
      if (!fe) return { path: path, crossOrigin: true };
      var loc = locatorFor(fe);
      if (loc) path.unshift(loc);
      w = w.parent;
    }
    return { path: path, crossOrigin: false };
  }

  window.__wvpLoc = {
    now: now,
    describe: describe,
    dataAttrs: dataAttrs,
    uniqueCss: uniqueCss,
    structuralXPath: structuralXPath,
    fullXPath: fullXPath,
    shadowHosts: shadowHosts,
    labelTextOf: labelTextOf,
    textXPath: textXPath,
    roleTextXPath: roleTextXPath,
    labelXPath: labelXPath,
    viewportOf: viewportOf,
    locatorFor: locatorFor,
    framePathFromTop: framePathFromTop,
  };
})();
