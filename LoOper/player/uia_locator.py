"""Element-first locator for desktop replay (Windows UI Automation).

Recording stores, alongside each click's image crop, the UIA element the click
landed on (``NGUI/widgets/recording_overlay.py`` -> ``recorder/element_recorder.py``).
Replay tries that element FIRST - the desktop twin of the web engine resolving
an entity by its recorded selectors - and only falls back to the template-
matching cascade in ``action_handlers.handle_click_action`` when the element
cannot be resolved or verified.

Why this is safer than image-only: UIA reports the element's LIVE bounding box,
so a click survives a moved window, a changed theme/DPI/scale, an added title
bar or a scrolled list - all of which break template matching.  The image stays
as the final net, so the two locators are independent signals instead of one.

Anti-misfire: a resolved element is trusted only when its ControlType matches
the recording AND its box is a plausible size relative to the recorded box;
otherwise the next (weaker) candidate is tried, ending at the image fallback.
An ambiguous weak match must never pick a lookalike.

comtypes is already a dependency (the recorder overlay uses it); no new one.
"""
from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger(__name__)

# UIA property ids / values / tree scopes.  Hardcoded standard constants so we
# do not depend on comtypes' generated module being importable at import time.

_PROCESS_ID = 30002
_CONTROL_TYPE = 30003
_NAME = 30005
_AUTOMATION_ID = 30011
_CLASS_NAME = 30012
_FRAMEWORK_ID = 30024
_WINDOW_CONTROL_TYPE = 50032
_SCOPE_CHILDREN = 2
_SCOPE_DESCENDANTS = 4

# A hung UIA provider must never stall the click path: the resolve runs on a
# short-lived daemon thread and a timeout behaves exactly like "not found".
_RESOLVE_TIMEOUT_S = 2.0

# live/recorded box size ratio accepted as "the same element".  Guards a weak
# Name/Class match from landing on a much larger container (e.g. a panel).
_SIZE_MIN_RATIO = 0.4
_SIZE_MAX_RATIO = 2.5


def click_point_from_rect(rect, rel_x=0.5, rel_y=0.5):
    """Recorded element box + relative click offset -> the click point.

    ``rect`` is (l, t, r, b) in screen pixels; ``rel_*`` are the recorded
    click's position inside the box (0..1, default center).  Returns (x, y).
    """
    left, top, right, bottom = (float(v) for v in rect)
    try:
        rel_x = float(rel_x)
    except Exception:
        rel_x = 0.5
    try:
        rel_y = float(rel_y)
    except Exception:
        rel_y = 0.5
    return (int(round(left + rel_x * (right - left))),
            int(round(top + rel_y * (bottom - top))))


def rect_size_plausible(recorded, live,
                        min_ratio=_SIZE_MIN_RATIO, max_ratio=_SIZE_MAX_RATIO):
    """True when ``live`` box is roughly the recorded box's size."""
    try:
        rec_w = float(recorded[2]) - float(recorded[0])
        rec_h = float(recorded[3]) - float(recorded[1])
        live_w = float(live[2]) - float(live[0])
        live_h = float(live[3]) - float(live[1])
    except Exception:
        return False
    if rec_w <= 0 or rec_h <= 0 or live_w <= 0 or live_h <= 0:
        return False
    return (min_ratio <= live_w / rec_w <= max_ratio
            and min_ratio <= live_h / rec_h <= max_ratio)


def rect_contains(outer, inner, tolerance=4):
    """True when ``inner`` box sits within ``outer`` (a few px of slack)."""
    return (inner[0] >= outer[0] - tolerance
            and inner[1] >= outer[1] - tolerance
            and inner[2] <= outer[2] + tolerance
            and inner[3] <= outer[3] + tolerance)


def element_tiers(desc):
    """Ordered, weakest-last candidate conditions for a recorded element.

    Each tier is ``(pairs, label)`` where ``pairs`` is a list of
    ``(property_id, value)`` to AND together.  Only tiers carrying a stable
    signal are used; a bare ControlType match is deliberately NOT offered (the
    first button on screen must never win).
    """
    automation_id = str(desc.get("automation_id") or "")
    name = str(desc.get("name") or "")
    class_name = str(desc.get("class_name") or "")
    control_type = desc.get("control_type") or 0
    tiers = []
    if automation_id and control_type:
        tiers.append(([(_AUTOMATION_ID, automation_id),
                       (_CONTROL_TYPE, control_type)], "AutomationId+ControlType"))
    if name and control_type:
        tiers.append(([(_NAME, name), (_CONTROL_TYPE, control_type)],
                      "Name+ControlType"))
    if class_name and control_type:
        tiers.append(([(_CONTROL_TYPE, control_type), (_CLASS_NAME, class_name)],
                      "ControlType+ClassName"))
    return tiers


# --------------------------------------------------------------------- UIA

# COM objects are apartment-bound, so the IUIAutomation instance is per-thread.
# The recorder overlay keeps the same per-thread singleton; duplicated here on
# purpose so the player never has to import the GUI layer.
_uia_local = threading.local()


def _get_uia():
    """Per-thread ``IUIAutomation`` instance, or None when unavailable."""
    if getattr(_uia_local, "tried", False):
        return getattr(_uia_local, "uia", None)
    _uia_local.tried = True
    _uia_local.uia = None
    try:
        import comtypes
        import comtypes.client

        comtypes.CoInitialize()
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient

        _uia_local.uia = comtypes.client.CreateObject(
            UIAutomationClient.CUIAutomation
        )
    except Exception as exc:
        logger.debug("UIA unavailable for replay locator: %s", exc)
    return _uia_local.uia


def _and_condition(uia, pairs):
    """AND the given ``(property_id, value)`` pairs into one condition."""
    condition = None
    for prop, value in pairs:
        piece = uia.CreatePropertyCondition(prop, value)
        condition = piece if condition is None else uia.CreateAndCondition(condition, piece)
    return condition


def _window_name_of(desc):
    """Recorded name of the element's top-level window, or ''."""
    for ancestor in reversed(desc.get("ancestors") or []):
        if ancestor.get("control_type") == _WINDOW_CONTROL_TYPE:
            return ancestor.get("name") or ""
    return ""


def _find_window_root(uia, root, desc):
    """Scope the search to the element's own top-level window when possible.

    Prefers a window of the recorded process whose title matches the recorded
    window name (so two windows of one app do not cross-match); falls back to
    the desktop root, which just widens the search - the tiers still decide.
    """
    pid = desc.get("process_id")
    if not pid:
        return root
    try:
        condition = _and_condition(uia, [
            (_CONTROL_TYPE, _WINDOW_CONTROL_TYPE),
            (_PROCESS_ID, int(pid)),
        ])
        windows = root.FindAll(_SCOPE_CHILDREN, condition)
    except Exception:
        return root
    if windows is None or windows.Length == 0:
        return root
    want = _window_name_of(desc)
    if not want:
        # No recorded Window ancestor - desktop icons, the desktop list and
        # taskbar/notification-area elements have none, so there is no window to
        # scope by. Picking an arbitrary same-process window is WRONG: for
        # explorer.exe the first one is usually a folder window, not "Program
        # Manager", so scoping to it made every desktop icon AND the desktop
        # list unresolvable and element-based drags fell back to absolute
        # coordinates. Widen to the desktop root; the identity tiers plus the
        # size/visibility guards still decide.
        return root
    for index in range(windows.Length):
        window = windows.GetElement(index)
        try:
            if str(window.CurrentName or "") == want:
                return window
        except Exception:
            continue
    # Recorded title not found (e.g. the page/window renamed itself): widening
    # beats an arbitrary window of the process, which can exclude the real one.
    return root


def _visible_at(uia, point, element_rect):
    """True when the element (or a child of it) is the topmost thing at ``point``.

    UIA can resolve an element that the user CANNOT see - covered by another
    window or scrolled out from under an overlay - and a blind coordinate click
    would then land on whatever is on top.  Hit-testing the click point and
    requiring the hit to sit inside the resolved box closes that gap.  Fails
    CLOSED: an unreadable hit is treated as "not visible", so the worst case is
    falling back to the image locator, never a wrong click.
    """
    try:
        from ctypes import wintypes

        hit = uia.ElementFromPoint(wintypes.POINT(int(point[0]), int(point[1])))
        r = hit.CurrentBoundingRectangle
        hit_rect = (int(r.left), int(r.top), int(r.right), int(r.bottom))
    except Exception:
        return False
    if hit_rect[2] - hit_rect[0] < 2 or hit_rect[3] - hit_rect[1] < 2:
        return False
    return rect_contains(element_rect, hit_rect)


def _resolve(desc):
    """Ladder of candidates; returns the click point (x, y) or None."""
    uia = _get_uia()
    if uia is None:
        return None
    try:
        root = uia.GetRootElement()
    except Exception:
        return None
    scope_root = _find_window_root(uia, root, desc)
    recorded_rect = desc.get("rect")
    recorded_control_type = desc.get("control_type") or 0
    rel_x = desc.get("rel_x", 0.5)
    rel_y = desc.get("rel_y", 0.5)

    for pairs, label in element_tiers(desc):
        try:
            matches = scope_root.FindAll(_SCOPE_DESCENDANTS,
                                         _and_condition(uia, pairs))
        except Exception:
            matches = None
        if matches is None or not matches.Length:
            continue
        # A weak tier ("ControlType+ClassName" = "any Edit") matches several
        # lookalikes in the app, and FindFirst returns the WRONG one - that is
        # how "any text input" got clicked instead of the recorded one. Collect
        # every candidate that passes the guards and prefer the one whose box
        # sits where the element was recorded: the recorded box disambiguates
        # between same-kind controls.
        candidates = []
        for index in range(matches.Length):
            element = matches.GetElement(index)
            try:
                r = element.CurrentBoundingRectangle
                live = (int(r.left), int(r.top), int(r.right), int(r.bottom))
            except Exception:
                continue
            if live[2] - live[0] < 2 or live[3] - live[1] < 2:
                continue
            if recorded_control_type:
                try:
                    if int(element.CurrentControlType) != int(recorded_control_type):
                        continue
                except Exception:
                    pass
            if recorded_rect and not rect_size_plausible(recorded_rect, live):
                logger.debug("UIA %s match rejected by size check: %s", label, live)
                continue
            score = _rect_distance(recorded_rect, live) if recorded_rect else 0.0
            candidates.append((score, live))
        if not candidates:
            continue
        candidates.sort(key=lambda item: item[0])
        for score, live in candidates:
            point = click_point_from_rect(live, rel_x, rel_y)
            if not _visible_at(uia, point, live):
                logger.debug("UIA %s match not visible at click point %s; skipping",
                             label, point)
                continue
            logger.info("Resolved element via %s at %s (rect dist %.0f)",
                        label, live, score)
            return point
    return None


def resolve_element(desc, timeout=_RESOLVE_TIMEOUT_S):
    """Element recording -> click point (x, y), or None to fall back to image.

    Runs UIA on a short-lived daemon thread so a hung provider cannot freeze
    the click; a timeout is treated as "not found".  Never raises.

    ponytail: a fresh thread per call means the per-thread UIA object is rebuilt
    each click (small but nonzero cost).  Swap to one persistent worker thread
    with a UIA instance if click latency ever shows up.
    """
    if not isinstance(desc, dict) or not desc.get("rect"):
        return None
    result = {}

    def _work():
        try:
            result["point"] = _resolve(desc)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("UIA element resolve failed: %s", exc)
            result["point"] = None

    worker = threading.Thread(target=_work, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        logger.warning("UIA element resolve timed out (%.1fs); using image fallback", timeout)
        return None
    return result.get("point")


# ------------------------------------------------- repeating sibling set
#
# The desktop twin of the web recorder's "pair-defined kind": HOLD Insert and
# click a row, then click a sibling row.  One mark can only GUESS what repeats,
# so the second is treated as ground truth - the rows are the two elements just
# below their shared container, and what the two agree on IS the row identity.

# Maximum levels between a marked element and the shared container (mirrors the
# web recorder's depth guard) - a row is a child of its list container.
_PAIR_MAX_DEPTH = 8


def _chain(desc):
    """A descriptor plus its ancestors, nearest first."""
    return [desc] + list(desc.get("ancestors") or [])


def _rid(entry):
    """Runtime id of a chain entry as a tuple (empty when unavailable)."""
    return tuple(entry.get("runtime_id") or [])


def sibling_kind_from_pair(desc_a, desc_b):
    """Two marked sibling elements -> the verified repeating ROW SET, or None.

    The rows are the two elements just below their shared container (lowest
    common ancestor); they must sit at the same depth and agree on control type
    and class.  A stable, shared class is REQUIRED - refusing to guess beats
    clicking the wrong set.

    Returns ``{"container": <descriptor>, "kind": {"control_type",
    "class_name"}}`` (the recorder adds the click offset and mode), or None.
    """
    if not isinstance(desc_a, dict) or not isinstance(desc_b, dict):
        return None
    chain_a = _chain(desc_a)
    chain_b = _chain(desc_b)
    ids_b = {_rid(e) for e in chain_b if _rid(e)}
    if not ids_b:
        return None
    idx_a = next((i for i, e in enumerate(chain_a) if _rid(e) and _rid(e) in ids_b), None)
    if idx_a is None:
        return None
    idx_b = next((i for i, e in enumerate(chain_b)
                  if _rid(e) == _rid(chain_a[idx_a])), None)
    if idx_b is None or idx_a == 0 or idx_b == 0:
        return None  # the mark itself is the shared container, not a row
    if idx_a != idx_b or idx_a > _PAIR_MAX_DEPTH:
        return None  # rows at different depths (or too far below the container)
    row_a = chain_a[idx_a - 1]
    row_b = chain_b[idx_b - 1]
    if _rid(row_a) == _rid(row_b):
        return None  # both marks are the same row
    if row_a.get("control_type") != row_b.get("control_type"):
        return None
    # The shared control type IS the row identity; a shared class REFINES it.
    # Two DIFFERENT non-empty classes are not the same row kind (refuse), but an
    # empty class is common (SysListView32 rows, some Electron proxy elements) -
    # then the control type alone, scoped to the verified shared container,
    # mirrors the web recorder's tag-only fallback.
    class_a = str(row_a.get("class_name") or "")
    class_b = str(row_b.get("class_name") or "")
    if class_a != class_b:
        return None
    container = dict(chain_a[idx_a])
    container.setdefault("process_id", desc_a.get("process_id") or 0)
    kind = {"control_type": row_a.get("control_type"), "class_name": class_a}
    # Extra PARITY: a generic "list element" class can cover unrelated items
    # (a doc / icon / folder / script all share one control).  The UI framework
    # narrows it further; it is only added when the provider exposes it, so a
    # missing value never over-constrains the match.
    framework_id = str(row_a.get("framework_id") or "")
    if framework_id:
        kind["framework_id"] = framework_id
    # EXTENT parity: the marked row's own box.  A container that resolves too
    # high exposes toolbar buttons / scrollbars / titles as "siblings"; they
    # share the control type but NOT the row's shape, so this is what keeps the
    # cursor on rows instead of anything else of the same kind.
    row_rect = row_a.get("rect")
    if row_rect:
        kind["row_rect"] = [int(v) for v in row_rect]
    # NAME parity: the marked row's display name.  On a shell file list this is
    # the ONLY handle on the row's file type, and the extension it resolves to
    # is what keeps a PDF set from swallowing folders and shortcuts (see
    # ``_shell_folder_extensions``).
    row_name = str(row_a.get("name") or "")
    if row_name:
        kind["row_name"] = row_name
    return {"container": container, "kind": kind}


def _prop(element, name, default=""):
    """Read a UIA ``Current*`` property, tolerating a provider that throws."""
    try:
        value = getattr(element, name)
    except Exception:
        return default
    return default if value is None else value


def _element_rect(element):
    """Element bbox (l, t, r, b) in screen pixels, or None."""
    try:
        r = element.CurrentBoundingRectangle
        return (int(r.left), int(r.top), int(r.right), int(r.bottom))
    except Exception:
        return None


def _row_keyer(matches):
    """Key function for a sibling set: AutomationId, unique Name, else ordinal.

    Mirrors the web cursor's ``key_attr``-or-ordinal: an identity that survives
    re-render / removal so a row is never clicked twice.  Name is only used when
    it is UNIQUE in the set - rows that share a name would share one key and the
    cursor would call the set exhausted after a single click, so those fall back
    to the positional ordinal.
    """
    names = [str(_prop(element, "CurrentName") or "") for element in matches]
    duplicated = {name for name in names if name and names.count(name) > 1}

    def key_of(ordinal, element):
        automation_id = str(_prop(element, "CurrentAutomationId") or "")
        if automation_id:
            return automation_id
        name = str(_prop(element, "CurrentName") or "")
        if name and name not in duplicated:
            return "name:%s" % name
        return "ordinal_%d" % ordinal

    return key_of


def _rect_distance(a, b):
    """Coarse mismatch between two rects: centre distance + size difference.

    Used to pick WHICH of several same-class matches is the recorded one - a
    list has sibling containers that share a class, so the first match is often
    the wrong region.
    """
    a_cx, a_cy = (a[0] + a[2]) / 2.0, (a[1] + a[3]) / 2.0
    b_cx, b_cy = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
    centre = abs(a_cx - b_cx) + abs(a_cy - b_cy)
    size = (abs((a[2] - a[0]) - (b[2] - b[0]))
            + abs((a[3] - a[1]) - (b[3] - b[1])))
    return centre + size


def _find_container(uia, desc):
    """Live element for a container descriptor.

    A container's identity tiers can match SEVERAL elements (a list of rows has
    sibling containers sharing a class), so every match of the strongest tier is
    scored against the recorded box and the closest wins.  The size/visibility
    guards of the single-element locator do not apply to a big region.
    """
    try:
        root = uia.GetRootElement()
    except Exception:
        return None
    scope_root = _find_window_root(uia, root, desc)
    recorded_rect = desc.get("rect")
    for pairs, _label in element_tiers(desc):
        try:
            matches = scope_root.FindAll(_SCOPE_DESCENDANTS, _and_condition(uia, pairs))
        except Exception:
            matches = None
        if matches is None or not matches.Length:
            continue
        if not recorded_rect:
            return matches.GetElement(0)
        best = None
        best_score = None
        for index in range(matches.Length):
            element = matches.GetElement(index)
            rect = _element_rect(element)
            if rect is None:
                continue
            score = _rect_distance(recorded_rect, rect)
            if best_score is None or score < best_score:
                best_score, best = score, element
        if best is not None:
            return best
    return None


def _find_container_by_kind(uia, desc_repeat, condition):
    """Container re-derived from the ROWS' shared kind, or None.

    ``_find_container`` re-finds the container by its OWN recorded identity
    (window path / AutomationId / Name / ClassName) - the part that describes
    WHERE the list sat on the recording page, and the first thing to break on a
    different instance of that page.  The rows' shared KIND is what the two
    marked siblings agreed on, and it survives.  So the set is re-derived from
    it: the same-kind elements under the recorded window are grouped by their
    parent and the parent holding the most rows wins - that parent IS the
    container the pair shared.  Box-scored against the recorded container when
    one was stored, so a same-kind group in another panel cannot beat it; a
    group of one is not a repeating set and never wins.
    """
    desc = desc_repeat.get("container") or {}
    try:
        root = uia.GetRootElement()
        scope_root = _find_window_root(uia, root, desc)
        matches = scope_root.FindAll(_SCOPE_DESCENDANTS, condition)
        walker = uia.ControlViewWalker
    except Exception:
        return None
    if matches is None or not matches.Length:
        return None
    recorded_rect = desc.get("rect")
    groups = {}
    for index in range(matches.Length):
        element = matches.GetElement(index)
        try:
            parent = walker.GetParentElement(element)
        except Exception:
            continue
        if parent is None:
            continue
        key = _runtime_of(parent)
        if not key:
            continue  # an unidentifiable parent can never be grouped safely
        entry = groups.get(key)
        if entry is None:
            groups[key] = entry = [parent, 0]
        entry[1] += 1
    best = None
    best_score = None
    for parent, count in groups.values():
        if count < 2:
            continue
        if recorded_rect:
            rect = _element_rect(parent)
            if rect is None:
                continue
            score = (_rect_distance(recorded_rect, rect), -count)
        else:
            score = (0, -count)
        if best_score is None or score < best_score:
            best_score, best = score, parent
    return best


def _desktop_bounds(uia):
    """Virtual desktop rect (l, t, r, b), or None."""
    try:
        r = uia.GetRootElement().CurrentBoundingRectangle
        return (int(r.left), int(r.top), int(r.right), int(r.bottom))
    except Exception:
        return None


def _runtime_of(element):
    """Element runtime id as a tuple, or () when the provider withholds it."""
    try:
        return tuple(int(v) for v in element.GetRuntimeId())
    except Exception:
        return ()


def _siblings_of(uia, container, condition):
    """Rows that are CHILDREN of the container - siblings only.

    Descendants are deliberately NOT accepted as-is: a same-kind element nested
    deeper is a "cousin" (another row's child, a wrapper, a sub-item) and must
    never be clicked.  UIA's ``FindAll(Children)`` walks the RAW tree while the
    recorded chain came from the CONTROL view, so when the direct-children pass
    is empty the descendants whose CONTROL-VIEW parent IS the container are taken
    instead - still siblings, never cousins or uncles.
    """
    try:
        children = container.FindAll(_SCOPE_CHILDREN, condition)
    except Exception:
        children = None
    if children is not None and children.Length:
        return [children.GetElement(i) for i in range(children.Length)]
    try:
        deep = container.FindAll(_SCOPE_DESCENDANTS, condition)
    except Exception:
        return []
    if deep is None or not deep.Length:
        return []
    try:
        walker = uia.ControlViewWalker
    except Exception:
        return [deep.GetElement(i) for i in range(deep.Length)]
    container_id = _runtime_of(container)
    if not container_id:
        return [deep.GetElement(i) for i in range(deep.Length)]
    out = []
    for index in range(deep.Length):
        element = deep.GetElement(index)
        try:
            parent = walker.GetParentElement(element)
            if parent is not None and _runtime_of(parent) == container_id:
                out.append(element)
        except Exception:
            continue
    return out


# ------------------------------------------------- shell file-type parity
#
# A shell file list (the desktop and Explorer folder views) is a
# ``SysListView32`` whose rows all report the same ControlType, an EMPTY class
# and an EMPTY AutomationId, with the file extension hidden from the display
# name.  So ``control_type`` + ``row_rect`` cannot tell a PDF from a folder or a
# shortcut - every desktop icon is an equally good "sibling" - and a marked PDF
# set swept in all 56 icons.  The row's real type is only knowable from the file
# system, so the MARKED row's extension is used as parity.

_SHELL_LIST_CLASSES = frozenset(("syslistview32",))


def _desktop_dirs():
    """The Desktop folders the shell shows (per-user + shared), or []."""
    dirs = []
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(260)
        # CSIDL_DESKTOP, CSIDL_COMMON_DESKTOPDIRECTORY
        for csidl in (0x0000, 0x0019):
            if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buffer) == 0:
                if buffer.value and buffer.value not in dirs:
                    dirs.append(buffer.value)
    except Exception:
        pass
    return dirs


def _ext_map_for(folders):
    """``{display_name: '.ext'}`` built from the listings of ``folders``.

    A row's display name is the file name without its extension, so the base
    name is the key.  Lower-cased, so ``.PDF`` and ``.pdf`` are one type.
    """
    mapping = {}
    for folder in folders:
        try:
            names = os.listdir(folder)
        except Exception:
            continue
        for filename in names:
            base, ext = os.path.splitext(filename)
            mapping.setdefault(base, ext.lower())
    return mapping or None


def _shell_folder_extensions(container):
    """Extension of each row of a shell file list, or None when it is not one.

    Only a Win32 ``SysListView32`` is a file list; every other list keeps the
    property-only behaviour.  The folder is the container's own ancestor whose
    Name is a real directory; with none, the Desktop folders are assumed (the
    desktop's list has no folder-named ancestor at all).

    ponytail: a folder listing, not a shell API - it covers the desktop and
    Explorer, which is where file-type repetition lives.  Swap to LVM_GETITEM +
    SHGetNameFromIDList if some other shell view ever needs this.
    """
    try:
        class_name = str(_prop(container, "CurrentClassName") or "").lower()
    except Exception:
        return None
    if class_name not in _SHELL_LIST_CLASSES:
        return None
    folders = []
    try:
        walker = _get_uia().ControlViewWalker
    except Exception:
        walker = None
    node = container
    for _ in range(12):
        try:
            node = walker.GetParentElement(node) if walker else None
        except Exception:
            node = None
        if node is None:
            break
        name = str(_prop(node, "CurrentName") or "")
        if name and os.path.isdir(name):
            folders = [name]
            break
    if not folders:
        folders = _desktop_dirs()
    return _ext_map_for(folders)


def enumerate_repeat(desc_repeat, processed):
    """Next unprocessed sibling row of a recorded set.

    Returns ``{"point", "key", "total", "rect"}`` for the next row, ``None``
    when nothing is clickable this pass (every row processed, or the rest are
    scrolled off-screen), or ``{"missing": True}`` when the container/kind no
    longer matches anything - the caller must NOT then fall back to the
    single-element locator, because that is the ONE row the marks were made on
    and it would be clicked again on every pass.  ``processed`` is keyed by row
    identity and must persist ACROSS passes (it lives in ``selection_memory``
    and survives loop boundaries).
    """
    kind = desc_repeat.get("kind") or {}
    control_type = kind.get("control_type") or 0
    class_name = str(kind.get("class_name") or "")
    if not control_type:
        return {"missing": True}
    uia = _get_uia()
    if uia is None:
        return {"missing": True}
    pairs = [(_CONTROL_TYPE, control_type)]
    if class_name:
        pairs.append((_CLASS_NAME, class_name))
    framework_id = str(kind.get("framework_id") or "")
    if framework_id:
        pairs.append((_FRAMEWORK_ID, framework_id))
    try:
        condition = _and_condition(uia, pairs)
    except Exception:
        return {"missing": True}
    # The container's identity tiers are the page-specific part and are tried
    # first; when every one of them misses, the rows' shared kind re-derives the
    # container, so resolution stays SET-based instead of degrading to the
    # recorded single row.
    container = _find_container(uia, desc_repeat.get("container") or {})
    if container is None:
        container = _find_container_by_kind(uia, desc_repeat, condition)
    if container is None:
        return {"missing": True}
    # SIBLINGS ONLY: never cousins (nested same-kind elements) or uncles.
    matches = _siblings_of(uia, container, condition)
    if not matches:
        return {"missing": True}
    # FILE-TYPE parity: on a shell file list every icon is the same control type
    # with no class, so only the marked row's extension separates a PDF set from
    # folders and shortcuts.  Absent (non-shell list, or the marked file is
    # gone) the filter is simply not applied.
    ext_by_name = _shell_folder_extensions(container)
    want_ext = None
    if ext_by_name:
        want_ext = ext_by_name.get(str(kind.get("row_name") or ""))
    rel_x = desc_repeat.get("rel_x", 0.5)
    rel_y = desc_repeat.get("rel_y", 0.5)
    row_rect = kind.get("row_rect")
    bounds = _desktop_bounds(uia)
    key_of = _row_keyer(matches)
    # Keep only clickable rows FIRST, so ``total`` reports the set that is
    # actually clickable (a 1/56 line in the log was how the too-wide set
    # announced itself) instead of every same-kind element in the container.
    rows = []
    for ordinal, element in enumerate(matches):
        rect = _element_rect(element)
        # A degenerate box (a hidden/collapsed toolbar item reports (0,0,0,0)) is
        # not a clickable row - leave it unprocessed rather than click (0, 0).
        if rect is None or rect[2] - rect[0] < 2 or rect[3] - rect[1] < 2:
            continue
        # EXTENT parity: only elements shaped like the marked ROW.  A container
        # that resolved too high exposes title-bar / toolbar children of the
        # same kind - they never match the row's box, so they are skipped.
        if row_rect and not rect_size_plausible(row_rect, rect, 0.5, 2.0):
            logger.debug("Repeating set: skip %s - box %s is not row-shaped %s",
                         key_of(ordinal, element), rect, tuple(row_rect))
            continue
        if want_ext is not None:
            name = str(_prop(element, "CurrentName") or "")
            if ext_by_name.get(name) != want_ext:
                logger.debug("Repeating set: skip %s - %r is not a %r row",
                             key_of(ordinal, element), name,
                             want_ext or "no-extension")
                continue
        rows.append((key_of(ordinal, element), rect))
    if not rows:
        return None
    total = len(rows)
    for key, rect in rows:
        if key in processed:
            continue
        point = click_point_from_rect(rect, rel_x, rel_y)
        # A row scrolled fully off the desktop is left UNPROCESSED so it is
        # clicked once it scrolls into view, never clicked at a nonsense point.
        if bounds and not (bounds[0] <= point[0] <= bounds[2]
                           and bounds[1] <= point[1] <= bounds[3]):
            continue
        return {"point": point, "key": key, "total": total, "rect": rect}
    return None


def resolve_repeat(desc_repeat, processed, timeout=_RESOLVE_TIMEOUT_S):
    """Cursor over a recorded sibling set; see ``enumerate_repeat``.

    Same hang-proofing as ``resolve_element``: a short-lived daemon thread with
    a timeout, and a timeout behaves exactly like "nothing clickable".
    """
    if not isinstance(desc_repeat, dict) or not desc_repeat.get("container"):
        return {"missing": True}
    try:
        done = set(processed or ())
    except Exception:
        done = set()
    result = {}

    def _work():
        try:
            result["info"] = enumerate_repeat(desc_repeat, done)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("UIA repeat cursor failed: %s", exc)
            result["info"] = {"missing": True}

    worker = threading.Thread(target=_work, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        logger.warning("UIA repeat cursor timed out (%.1fs); skipping pass", timeout)
        return None
    return result.get("info")
