"""Field identity: selector ladders, choice merging, include/skip filter."""

import json
import re


class FormFillerFieldsMixin:
    """Field identity: selector ladders, choice merging, include/skip filter."""

    # The ONE sentinel the extraction prompt names ("never reply SKIP"): a
    # stray reply of exactly this word is not a value and is never written into
    # the page.  No synonym list - 'none' / 'unknown' are ordinary values.
    _SKIP_TOKEN = "skip"

    def _ff_selectors(self, field):
        """Build the runtime selector-candidate ladder for one field."""
        sels = []
        try:
            from .....web import actions as web_actions
            xpath_lit = web_actions._xpath_lit
        except Exception:
            xpath_lit = lambda s: "'" + str(s).replace("'", "''") + "'"  # noqa: E731
        fid = field.get("id") or ""
        if fid:
            # An ATTRIBUTE selector, never '#id': a generated id can hold
            # characters that are invalid in a CSS identifier - React 19's
            # useId emits '«r54»' - and '#«r54»' makes querySelectorAll THROW,
            # so the candidate silently resolved nothing and the field became
            # unwritable (live: "target unresolved or not editable ...
            # selectors=['#«r54»', ...]").  A quoted attribute selector accepts
            # any character.
            sels.append('[id="%s"]' % str(fid).replace('"', '\\"'))
        name = field.get("name") or ""
        if name:
            sels.append('[name="%s"]' % name.replace('"', '\\"'))
        ph = field.get("placeholder") or ""
        if ph:
            sels.append('[placeholder="%s"]' % ph.replace('"', '\\"'))
        aria = field.get("aria_label") or ""
        if aria:
            sels.append('[aria-label="%s"]' % aria.replace('"', '\\"'))
        label = field.get("label") or ""
        if label:
            sels.append(
                "//label[normalize-space(.)=%s]/following::input[1]" % xpath_lit(label)
            )
        tag = (field.get("tag") or "input").lower()
        itype = (field.get("type") or "").lower()
        kind = (field.get("kind") or "").lower()
        if tag == "input" and itype and itype != "text":
            sels.append('input[type="%s"]' % itype)
        else:
            sels.append(tag)
        # A ROLE-based widget (<div role="radio">Yes</div>, <button role="switch">)
        # is identified by its role + its OWN visible text - the label ladder
        # above looks for a following <input> and resolves nothing for it.
        if (kind in ("choice", "switch") and label
                and tag not in ("input", "textarea", "select")):
            sels.append("//*[@role][normalize-space(.)=%s]" % xpath_lit(label))
        return sels

    @staticmethod
    def _ff_scope_selectors(scope):
        """Stable-first selector ladder for a picked scope container.

        The picker records a rich locator (css / id / classes / ancestor_css /
        data_attrs).  Its recorded CSS embeds PER-INSTANCE tokens - an ember
        ``#emberNNN`` id and a hashed CSS-module class - which rotate on the
        next form instance, so resolving by ``css`` alone finds nothing and the
        scope silently yields 0 fields.  The ladder therefore tries the STABLE
        shape first (parent tag + utility classes, then the id, then data
        attrs) and keeps the recorded css as the last resort.  Empty/None means
        the whole document (no scoping).
        """
        if not scope:
            return []
        if isinstance(scope, str):
            try:
                scope = json.loads(scope)
            except Exception:
                return []
        if not isinstance(scope, dict):
            return []
        loc = scope.get("locator")
        if not isinstance(loc, dict):
            loc = scope
        sels = []

        def add(s):
            s = str(s or "").strip()
            if s and s not in sels:
                sels.append(s)

        tag = str(loc.get("tag") or "").strip()
        classes = [str(c) for c in (loc.get("classes") or []) if c]
        # The structural chain ends with the target itself, so the parent tag
        # is the second-to-last segment ('... > form > div' -> 'form').
        parent_tag = ""
        chain = str(loc.get("ancestor_css") or "")
        if chain:
            tags = re.findall(r"(?:^|>)\s*([a-zA-Z][a-zA-Z0-9-]*)", chain)
            if len(tags) >= 2:
                parent_tag = tags[-2]
        if tag and classes:
            cls = ".".join(classes)
            if parent_tag:
                add("%s > %s.%s" % (parent_tag, tag, cls))
            add("%s.%s" % (tag, cls))
        if parent_tag and tag:
            add("%s > %s" % (parent_tag, tag))
        sid = loc.get("id")
        if sid:
            add('[id="%s"]' % str(sid).replace('"', '\\"'))
        for k, v in (loc.get("data_attrs") or {}).items():
            add('[%s="%s"]' % (k, v) if v not in (None, "") else "[%s]" % k)
        add(loc.get("css"))
        return sels

    @staticmethod
    def _ff_scope_frame(scope):
        """Window / iframe context recorded with the picked container, or None.

        The picker resolves the TOP layer, so the pick itself is correct - but
        a container inside a POPUP WINDOW (``_window_ordinal``) or an IFRAME
        (``frame_path``) is not reachable from the base document.  The scope is
        therefore re-entered before it is resolved (exactly like web-sequence
        replay's ``switch_to_window_ordinal`` + ``enter_recorded_frame``);
        without it every op runs in the base/opener document and the popup is
        ignored (the fields of the page UNDER the modal get enumerated).
        """
        if isinstance(scope, str):
            try:
                scope = json.loads(scope) if scope else None
            except Exception:
                return None
        if not isinstance(scope, dict):
            return None
        ordinal = scope.get("_window_ordinal")
        try:
            ordinal = int(ordinal) if ordinal is not None else None
        except (TypeError, ValueError):
            ordinal = None
        path = scope.get("frame_path")
        if not isinstance(path, list):
            path = []
        return {
            "window_ordinal": ordinal,
            "frame_path": path,
            "cross_origin_frame": bool(scope.get("cross_origin_frame")),
        }

    def _ff_field_selectors(self, field):
        """Selector ladder for a field: precomputed for a merged choice group,
        otherwise the identity/placeholder/aria/label ladder.

        A Laya-resolved TREE marker (when the resolver ran) is the FIRST rung: it
        is the identity of the element the decision engine picked, and unlike the
        recorded rungs it does not depend on a rotated ``#emberNNN`` id or a
        hashed class name.  The recorded ladder stays BEHIND it, so Laya off/down
        - or a marker the page has since re-rendered away - behaves exactly as it
        did before the resolver existed.
        """
        ladder = field.get("selectors") or self._ff_selectors(field)
        marker = field.get("_ff_tree_sel")
        return ([marker] + list(ladder)) if marker else ladder

    @staticmethod
    def _ff_specific_selectors(sels):
        """Keep only candidates that pinpoint a control; drop generic sweeps.

        A bare tag ('input'), a bare widget family ('input[type="radio"]') or an
        empty entry matches EVERY control on the page - a ladder built from them
        can never resolve the answer's own option (observed: a merged 'Choose
        one' group wrote ``selectors=['input', '']`` and the write reported
        'target unresolved or not editable').
        """
        keep = []
        for s in sels or []:
            s = str(s or "").strip()
            if not s or s in keep:
                continue
            low = s.lower()
            if low in ("input", "select", "textarea", "div", "span", "button",
                       "li", "a"):
                continue
            if low.startswith('input[type='):
                continue
            keep.append(s)
        return keep

    def _ff_merge_choices(self, fields):
        """Collapse radio/checkbox controls sharing a GROUP into ONE field.

        A radio group is ONE question with N options; enumerating each control
        separately would ask the model about the OPTION, not the question.  The
        merged field keeps the group's QUESTION as its label, the option labels
        in order, and a selector ladder that resolves the WHOLE group at write
        time (all controls by shared ``name``; a lone control by its own
        identity).
        """
        out = []
        groups = {}
        for f in fields:
            if (f.get("kind") or "") != "choice":
                out.append(f)
                continue
            gk = f.get("group") or ("solo:%s" % f.get("index"))
            g = groups.get(gk)
            if g is None:
                g = {
                    "kind": "choice",
                    "type": (f.get("type") or "radio").lower(),
                    "tag": f.get("tag") or "input",
                    "name": f.get("name") or "",
                    "id": f.get("id") or "",
                    "group": gk,
                    "label": (f.get("question") or "").strip(),
                    "required": bool(f.get("required")),
                    "options": [],
                    "type_index": f.get("type_index") or 0,
                    "_first": f,
                    "_controls": [],
                }
                groups[gk] = g
                out.append(g)
            opt = (f.get("label") or "").strip()
            if opt and opt not in g["options"]:
                g["options"].append(opt)
            if not g["name"] and f.get("name"):
                g["name"] = f.get("name")
            g["_controls"].append(f)
        for g in groups.values():
            name = g.get("name")
            if name:
                g["selectors"] = ['input[name="%s"]' % name, '[name="%s"]' % name]
            else:
                # The group ladder must resolve EVERY option.  JS_SET_CHOICE and
                # the read-back collect controls across ALL candidates, so the
                # union of each option's OWN (label-anchored) ladder IS the group
                # ladder.  Generic candidates are dropped: a bare 'input' matches
                # every control on the page, which is what left a merged 'Choose
                # one' group with no usable handle (live: selectors=['input', '']
                # -> write 'target unresolved or not editable').
                ladders = []
                for cf in g.pop("_controls", []):
                    for s in self._ff_specific_selectors(
                            cf.get("selectors") or self._ff_selectors(cf)):
                        if s not in ladders:
                            ladders.append(s)
                g["selectors"] = ladders
            g.pop("_first", None)
            if not g["label"]:
                g["label"] = (
                    "Choose one: " + ", ".join(g["options"])
                    if g["options"] else "Selection"
                )
        return out

    @staticmethod
    def _ff_haystack(field):
        """All the identity text an include/skip token may match.

        A field's visible question often lives OUTSIDE the control (a sibling
        block) or only in its name/placeholder - a date input's label can be
        empty - so matching the label alone silently ignores a skip token like
        'date' and the field gets filled anyway.
        """
        parts = [
            field.get("label"), field.get("question"), field.get("name"),
            field.get("id"), field.get("placeholder"), field.get("aria_label"),
        ]
        return " ".join(str(p).lower() for p in parts if p)

    def _ff_filter(self, fields, cfg):
        include = cfg["fields_include"]
        skip = cfg["fields_skip"]
        out = []
        for f in fields:
            hay = self._ff_haystack(f)
            if include and not any(tok in hay for tok in include):
                continue
            if skip and any(tok in hay for tok in skip):
                continue
            out.append(f)
        return out[: max(1, cfg["max_fields"])]
