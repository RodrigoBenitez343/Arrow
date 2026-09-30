"""Laya-first element resolution: the container TREE and its navigation.

The flat field inventory (``fields``/``web``) answers "what can be filled".  It
identifies each control by a selector ladder and a ``type_index``, and every
ladder rung degenerates on the pages that hurt most: LinkedIn rotates
``#emberNNN`` ids and hashes its CSS-module class names, so a written ladder can
resolve nothing - or, worse, resolve a SIBLING that happens to match.

So the target element is instead resolved the way a Handle node resolves a click
in web mode: a tree of the page is built once, and the decision engine picks the
node.  This module owns that tree and its navigation:

* :meth:`_ff_tree_scan` builds it from the live DOM - rooted at the PICKED
  container when the node is scoped, else at the whole document - piercing
  shadow roots and same-origin iframes, so a control inside a popup, a shadow
  widget or an embedded frame is still reachable;
* :meth:`_ff_tree_pick` walks the tree top-down with typed Laya ``choice``
  questions ("which part of the page holds the control that answers this?")
  until it reaches a control;
* :meth:`_ff_resolve_target` writes the winner's marker onto the field, and the
  field's selector ladder puts that marker FIRST.

The ladder is never removed: it stays behind the marker, so Laya off/down, an
empty tree or a refused pick all degrade to exactly the previous behaviour.
"""
import json
import logging
import re

from .common import log_block, log_table, logger


class FormFillerTreeMixin:
    """Resolve WHICH element a field means by navigating the page tree with Laya."""

    # A hard ceiling on the emitted tree (a page of 400 controls is already far
    # beyond anything the filler will act on) and the depth the walk may climb.
    _FF_TREE_NODES_MAX = 400
    # Laya's typed ``choice`` scores a bounded option set; the engine's own
    # consumers cap at 12-16, so a wider level is pre-ranked and cut.
    _FF_TREE_OPTIONS = 12
    # Safety bound on the DECISIONS one field may cost.  Only a level with more
    # than one node is a decision (see _ff_tree_pick_from), so a deep but
    # single-child chain - html > body > form > div > ... - never runs this out.
    _FF_TREE_DEPTH = 8
    # Absolute bound on the walk itself, so a pathological tree can never spin.
    _FF_TREE_LEVELS_MAX = 40
    # Option labels travel inside the engine's option map, so they must be
    # short; the state line is capped like the Handle node's request.
    _FF_TREE_LABEL_MAX = 90
    _FF_TREE_STATE_MAX = 300

    # ── the tree ────────────────────────────────────────────────────────────

    def _ff_forget_tree(self):
        """Drop the cached tree, so the next resolve re-reads the DOM.

        A write re-renders (React/Ember swap the field's subtree), and the
        marker IS the node index: reusing a stale tree would aim the next field
        at whatever now occupies that ordinal.
        """
        self._ff_tree = None
        self._ff_tree_gen = getattr(self, "_ff_tree_gen", 0) + 1

    def _ff_tree_nodes(self):
        return getattr(self, "_ff_tree", None) or []

    def _ff_tree_scan(self, cfg, stop_flag):
        """Build the container tree for the CURRENT DOM; None when unavailable.

        Uses only the browser session the run already holds (``_ff_web_driver``,
        resolved by the field enumeration): a resolve must never open a browser
        of its own, and with no live session there is nothing to resolve.
        """
        try:
            driver = getattr(self, "_ff_web_driver", None)
        except Exception:
            driver = None
        if driver is None:
            return None
        try:
            from .....web import actions as web_actions
        except Exception as exc:
            log_block(logger, logging.WARNING, "Form Tree",
                      "web modules unavailable: %s" % exc)
            return None
        if self._ff_halt(stop_flag):
            return None
        raw = None
        try:
            raw = driver.execute_script(
                web_actions.JS_ENUMERATE_FORM_TREE,
                list(self._ff_scope_selectors(cfg.get("web_scope")) or []),
                bool(getattr(self, "_ff_scope_in_frame", False)),
                self._FF_TREE_NODES_MAX,
            )
        except Exception as exc:
            log_block(logger, logging.WARNING, "Form Tree",
                      "the page tree could not be read: %s" % exc)
            return None
        try:
            data = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        nodes = [n for n in (data.get("nodes") or []) if isinstance(n, dict)]
        by_index = {}
        for n in nodes:
            n["children"] = []
            try:
                by_index[int(n.get("i", -1))] = n
            except (TypeError, ValueError):
                continue
        roots = []
        for n in nodes:
            try:
                parent = int(n.get("parent", -1))
            except (TypeError, ValueError):
                parent = -1
            p = by_index.get(parent)
            # A parent that is not a node (or is the node itself) is a ROOT:
            # a malformed edge must never build a cycle and hang the walk.
            if p is not None and p is not n:
                p["children"].append(n)
            else:
                roots.append(n)
        self._ff_tree = roots
        self._ff_tree_scope = str(data.get("root") or "")
        reasons = str(data.get("reason") or "")
        log_table(
            logger, logging.INFO, "Form Tree",
            [("Root", self._ff_tree_scope or "(whole page)"),
             ("Scoped to the picked container",
              "yes" if data.get("scoped") else "no"),
             ("Nodes", "%d (%d control(s))" % (
                 len(nodes), sum(1 for n in nodes if n.get("control")))),
             ("Note", reasons or "ok")],
        )
        return roots

    # ── one level of the descent ────────────────────────────────────────────

    @staticmethod
    def _ff_tree_words(text):
        """Lowercased alphanumeric words (2+ chars) of a text."""
        return [w for w in re.split(r"[^a-z0-9]+", str(text or "").lower())
                if len(w) > 1]

    @classmethod
    def _ff_tree_score(cls, question, label):
        """How many words of *question* the *label* carries.

        The same containment-tolerant score the Handle node's web picker uses: a
        verbatim word counts 2, a 3+ letter overlap on BOTH sides counts 1.  It
        only ORDERS the options (and trims a level wider than the engine's
        slots) - the pick itself is Laya's.
        """
        hay = cls._ff_tree_words(label)
        hay_set = set(hay)
        score = 0
        for word in cls._ff_tree_words(question):
            if word in hay_set:
                score += 2
            elif len(word) >= 3 and any(
                len(other) >= 3 and (word in other or other in word)
                for other in hay
            ):
                score += 1
        return score

    def _ff_tree_control_labels(self, node, limit=4):
        """The labels of the controls a branch CONTAINS (bounded)."""
        found = []
        queue = list(node.get("children") or [])
        while queue and len(found) < limit:
            cur = queue.pop(0)
            if cur.get("control"):
                text = str(cur.get("label") or "").strip()
                if text and text not in found:
                    found.append(text)
                continue
            queue.extend(cur.get("children") or [])
        return found

    def _ff_tree_option_label(self, node):
        """What this node is CALLED in the Laya option list.

        A branch carries no name of its own on most pages (the page names the
        QUESTION, not the wrapper), so a label-less branch is described by the
        controls it contains - which is exactly the information the pick needs.
        """
        label = str(node.get("label") or "").strip()
        if label:
            return label[:self._FF_TREE_LABEL_MAX]
        summary = " / ".join(self._ff_tree_control_labels(node))
        if summary:
            return summary[:self._FF_TREE_LABEL_MAX]
        return "%s #%s" % (node.get("tag") or "node", node.get("i", 0))

    def _ff_tree_choose(self, question, nodes):
        """Offer a level's nodes to Laya; the picked NODE, or None.

        The engine answers with one criterion KEY, so the option map must be
        keyed by label - a duplicate label would silently collapse two nodes
        into one option, hence the de-duplicating suffix.  None means "no
        decision" (engine off/down, unreadable reply, or a refusal); the caller
        then keeps the ladder rather than guessing.
        """
        if not nodes:
            return None
        ranked = sorted(
            ((self._ff_tree_score(question, self._ff_tree_option_label(n)), n)
             for n in nodes),
            key=lambda row: -row[0],
        )
        offered = []
        used = {}
        for _score, node in ranked[:self._FF_TREE_OPTIONS]:
            label = self._ff_tree_option_label(node)
            if label in used:
                used[label] += 1
                label = "%s (%d)" % (label, used[label])
            else:
                used[label] = 1
            offered.append((label, node))
        logger.debug("[FORM][TREE] Level options: %s",
                     [lab for lab, _n in offered])
        try:
            from AI import laya_client
        except Exception as exc:
            logger.info("[FORM][TREE] Laya unavailable: %s", exc)
            return None
        try:
            picked = laya_client.choice(
                "Field question: %s" % str(question or "")[:self._FF_TREE_STATE_MAX],
                "Which part of the page holds the control that answers the "
                "question? Pick the container or the control whose label "
                "answers it.",
                {label: "" for label, _n in offered},
            )
        except Exception as exc:  # noqa: BLE001 - never break a fill
            logger.warning("[FORM][TREE] Laya pick failed: %s", exc)
            return None
        for label, node in offered:
            if label == picked:
                return node
        if picked is not None:
            logger.info("[FORM][TREE] Laya picked %r, which was not offered",
                        picked)
        return None

    def _ff_tree_pick(self, question, stop_flag):
        """The CONTROL node the question means, or None (caller keeps the ladder)."""
        for root in self._ff_tree_nodes():
            node = self._ff_tree_pick_from(root, question, stop_flag)
            if node is not None:
                return node
        return None

    def _ff_tree_pick_from(self, root, question, stop_flag):
        """Descend from *root* until a control; None when this root does not hold it.

        Only a level holding SEVERAL nodes is a decision, and only decisions are
        bounded: a document tree reaches its form through a dozen single-child
        levels (html > body > div > ...), which cost no forward and must not
        exhaust a decision budget.
        """
        current = root
        level = [root]
        decisions = 0
        for _step in range(self._FF_TREE_LEVELS_MAX):
            if stop_flag and stop_flag():
                return None
            if len(level) == 1:
                # Only one way down: entering it costs no engine forward.
                current = level[0]
            else:
                decisions += 1
                if decisions > self._FF_TREE_DEPTH:
                    # More forks than one answer should need: refuse rather than
                    # keep spending forwards on a tree that is not a form.
                    logger.info(
                        "[FORM][TREE] gave up after %d decision(s) - keeping "
                        "the selector ladder", self._FF_TREE_DEPTH)
                    return None
                current = self._ff_tree_choose(question, level)
                if current is None:
                    return None
            if current.get("control"):
                return current
            level = list(current.get("children") or [])
            if not level:
                # A branch with no control under it: nothing to act on here.
                return None
        return None

    # ── the entry point the node loop calls ─────────────────────────────────

    def _ff_resolve_target(self, field, cfg, stop_flag):
        """Aim *field* at ONE element through the Laya-navigated tree.

        Sets ``field['_ff_tree_sel']`` (the marker selector) so the field's own
        ladder writes/reads THAT element first, and returns ``"laya-tree"``.
        Returns ``"ladder"`` - leaving today's behaviour untouched - when there
        is no live browser session, when the engine is switched off, when the
        tree holds no control, or when Laya refuses to pick.
        """
        if not field:
            return "ladder"
        # No live session means the run is not driving a page (a unit-test
        # harness, a desktop pass): never open a browser just to resolve.
        if getattr(self, "_ff_web_driver", None) is None:
            return "ladder"
        try:
            from AI import laya_client
        except Exception:
            return "ladder"
        try:
            if laya_client.status() == "off":
                return "ladder"
        except Exception:
            return "ladder"

        generation = getattr(self, "_ff_tree_gen", 0)
        if (field.get("_ff_tree_sel")
                and field.get("_ff_tree_gen") == generation):
            return "laya-tree"  # resolved already, and the DOM has not moved
        if getattr(self, "_ff_tree", None) is None:
            if self._ff_tree_scan(cfg, stop_flag) is None:
                return "ladder"

        label = str(field.get("label") or field.get("aria_label") or "").strip()
        node = self._ff_tree_pick(label, stop_flag)
        field.pop("_ff_tree_sel", None)
        field.pop("_ff_tree_gen", None)
        if not node:
            log_block(
                logger, logging.INFO, "Form Resolve: %s" % (label or "?"),
                "Laya resolved no node for this field - keeping the selector "
                "ladder",
            )
            return "ladder"
        try:
            from .....web import actions as web_actions
            attr = web_actions.FF_TREE_MARKER_ATTR
        except Exception:
            return "ladder"
        try:
            index = int(node.get("i", -1))
        except (TypeError, ValueError):
            return "ladder"
        if index < 0:
            return "ladder"
        field["_ff_tree_sel"] = '[%s="%d"]' % (attr, index)
        field["_ff_tree_gen"] = generation
        log_block(
            logger, logging.INFO, "Form Resolve: %s" % (label or "?"),
            "Laya picked the %s %r (node %d, depth %s) of %r -> %s"
            % (node.get("tag") or "control", str(node.get("label") or "")[:80],
               index, node.get("depth"), self._ff_tree_scope or "the page",
               field["_ff_tree_sel"]),
        )
        return "laya-tree"
