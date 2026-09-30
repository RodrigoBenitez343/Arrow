from __future__ import annotations

"""Web session event model — the shared contract between recorder and replay.

LoOper-shaped (mirrors desktop sequences): a session file has a metadata block
plus a list of typed actions, identified by ``mode: "web"`` and
``schema_version: 1`` so the two domains never collide.  Headless-first:
``dom_snapshot`` may exist only as a debug aid and is never used by the replay
engine.  ``start_url`` stays in the metadata as a reference (the page the
recording happened on) — the replay engine never navigates to it.  The
recorder emits a single ``navigate`` action only for a cold start (a
recording that began on the browser's default page and navigated to a site
through the address bar / new-tab search — the landing page is recorded as an
explicit "Open page" action); all other navigation is recorded as clicks and
keys, so button/element-driven dynamic chains replay as recorded.
"""

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

SCHEMA_VERSION = 1
MODE_WEB = "web"


# Ids a FRAMEWORK assigns per render, not ids a page author wrote.  Ember
# (LinkedIn) numbers ``emberNNN`` in render order, so the SAME id names a
# different control on the next instance of the page; React's ``:r0:`` /
# ``react-select-N``, MUI / radix / headlessui vended ids and hashed ``css-`` /
# ``sc-`` ids rotate the same way, and a trailing 3+ digit run is the generic
# generated shape (``job-4457256950``).  Such an id can never be an identity,
# because a web sequence must replay on ANY instance of the page.
# ponytail: prefix list + digit-tail heuristic; a new framework's id scheme
# needs one more alternative here, never a new resolution layer.
_VOLATILE_ID = re.compile(
    r"(?:^(?:ember|react-select|downshift|headlessui|radix|mui|jsx|css|sc|ng|vue|svelte)[-_:]?\d"
    r"|:r[0-9a-z]+:"
    r"|\d{3,}$)",
    re.IGNORECASE,
)


def is_volatile_id(value: Optional[str]) -> bool:
    """True when *value* looks framework-generated rather than authored.

    A generated id names one render instance, not the element: it must not be
    used to identify a target on the next page instance.
    """
    return bool(value) and bool(_VOLATILE_ID.search(str(value)))


@dataclass
class Locator:
    """A target element described by a chain of progressively weaker candidates.

    The chain is the web analogue of the desktop recorder's layered tries:
    every layer is one independent way to re-find the element, and replay runs
    them strongest-first until one resolves.  Preference order: id -> test-id
    /data-* attributes -> stable attribute fallbacks (name / placeholder /
    aria-label / type / href) -> visible-text matches (link text / text XPath /
    role+text / label-anchored XPath) -> unique CSS -> ancestor-anchored CSS ->
    classes -> structural XPath -> raw XPath.  Raw XPath is the last resort
    because it breaks on the first DOM change; ``viewport`` geometry powers the
    coordinate hit-test fallback used when every selector misses.
    """

    tag: Optional[str] = None
    id: Optional[str] = None
    name: Optional[str] = None
    role: Optional[str] = None
    text: Optional[str] = None
    href: Optional[str] = None
    placeholder: Optional[str] = None
    aria_label: Optional[str] = None
    input_type: Optional[str] = None
    data_attrs: dict[str, str] = field(default_factory=dict)
    classes: list[str] = field(default_factory=list)
    link_text: Optional[str] = None
    alt_text: Optional[str] = None
    title: Optional[str] = None
    value: Optional[str] = None
    label_text: Optional[str] = None
    index: Optional[int] = None  # 0-based among same-tag+class siblings
    text_index: Optional[int] = None  # 0-based among text_xpath matches
    text_xpath: Optional[str] = None
    role_text_xpath: Optional[str] = None
    label_xpath: Optional[str] = None
    css: Optional[str] = None
    ancestor_css: Optional[str] = None
    structural_xpath: Optional[str] = None
    xpath: Optional[str] = None
    # Shadow hosts enclosing the element (outermost-first), each a serialized
    # Locator.  Non-empty means the element lives INSIDE a shadow root, which
    # WebDriver cannot see: replay must use the shadow-piercing dispatch, never
    # native resolution (a light-DOM lookalike would otherwise be acted on).
    shadow_hosts: list[dict[str, Any]] = field(default_factory=list)
    viewport: Optional[dict[str, Any]] = None  # document-space geometry

    def chain(self) -> list[dict[str, Any]]:
        """Ordered candidates, strongest first. Used by the replay engine.

        A GENERATED id never leads: it names the recording's render instance,
        so on any other instance of the page it points at a different control.
        The portable layers go first - test-id hooks, then the element's own
        attributes (name / placeholder / aria-label / type / href), then its
        visible text (link text / text XPath / role+text / label) - because
        those are what survive across page instances, which is the whole point
        of a modular web sequence.  A generated id is kept, but only as a late
        candidate.
        """
        chain: list[dict[str, Any]] = []
        stable_id = self.id if self.id and not is_volatile_id(self.id) else None
        if stable_id:
            chain.append({"kind": "id", "value": stable_id})
        for key, value in self.data_attrs.items():
            chain.append({"kind": "data", "name": key, "value": value})
        # Stable attribute fallbacks captured at recording time - they locate
        # the element even when ids/classes change between sessions.
        if self.name:
            chain.append({"kind": "data", "name": "name", "value": self.name})
        if self.placeholder:
            chain.append({"kind": "data", "name": "placeholder", "value": self.placeholder})
        if self.aria_label:
            chain.append({"kind": "data", "name": "aria-label", "value": self.aria_label})
        if self.input_type and self.tag:
            chain.append({"kind": "data", "name": "type", "value": self.input_type})
        if self.href and self.tag == "a":
            chain.append({"kind": "data", "name": "href", "value": self.href})
        # Visible-text layers - survive attribute/class rotation on links and
        # buttons better than any selector chain.
        if self.link_text and self.tag == "a":
            chain.append({"kind": "link_text", "value": self.link_text})
        if self.text_xpath:
            chain.append({"kind": "text", "value": self.text_xpath})
        if self.role_text_xpath:
            chain.append({"kind": "role_text", "value": self.role_text_xpath})
        if self.label_xpath:
            chain.append({"kind": "label", "value": self.label_xpath})
        if self.css:
            chain.append({"kind": "css", "value": self.css})
        if self.ancestor_css:
            chain.append({"kind": "css", "value": self.ancestor_css})
        if self.classes:
            chain.append({"kind": "class", "value": "." + ".".join(self.classes)})
        # A GENERATED id trails every portable layer: it names the recording's
        # render instance, not the element (see ``is_volatile_id``).
        if self.id and not stable_id:
            chain.append({"kind": "id", "value": self.id})
        # ``positional``: the candidate describes WHERE the element sat on the
        # recording page, not what it is.  Replay may only use it when nothing
        # else matched - a shifted/re-rendered page turns a bare structural path
        # into a lookalike, which is what the sibling/structural strategy is
        # reserved for (repeating-element sets), never a general identity.
        if self.structural_xpath:
            chain.append({"kind": "xpath", "value": self.structural_xpath,
                          "positional": True})
        if self.xpath:
            chain.append({"kind": "xpath", "value": self.xpath,
                          "positional": True})
        return chain

    def to_dict(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "text": self.text,
            "href": self.href,
            "placeholder": self.placeholder,
            "aria_label": self.aria_label,
            "input_type": self.input_type,
            "data_attrs": self.data_attrs,
            "classes": self.classes,
            "link_text": self.link_text,
            "alt_text": self.alt_text,
            "title": self.title,
            "value": self.value,
            "label_text": self.label_text,
            "index": self.index,
            "text_index": self.text_index,
            "text_xpath": self.text_xpath,
            "role_text_xpath": self.role_text_xpath,
            "label_xpath": self.label_xpath,
            "css": self.css,
            "ancestor_css": self.ancestor_css,
            "structural_xpath": self.structural_xpath,
            "xpath": self.xpath,
            "shadow_hosts": self.shadow_hosts,
            "viewport": self.viewport,
        }

    @classmethod
    def from_dict(cls, raw: Optional[dict[str, Any]]) -> Optional["Locator"]:
        if not raw:
            return None
        return cls(
            tag=raw.get("tag"),
            id=raw.get("id"),
            name=raw.get("name"),
            role=raw.get("role"),
            text=raw.get("text"),
            href=raw.get("href"),
            placeholder=raw.get("placeholder"),
            aria_label=raw.get("aria_label"),
            input_type=raw.get("input_type"),
            data_attrs=dict(raw.get("data_attrs") or {}),
            classes=list(raw.get("classes") or []),
            link_text=raw.get("link_text"),
            alt_text=raw.get("alt_text"),
            title=raw.get("title"),
            value=raw.get("value"),
            label_text=raw.get("label_text"),
            index=raw.get("index"),
            text_index=raw.get("text_index"),
            text_xpath=raw.get("text_xpath"),
            role_text_xpath=raw.get("role_text_xpath"),
            label_xpath=raw.get("label_xpath"),
            css=raw.get("css"),
            ancestor_css=raw.get("ancestor_css"),
            structural_xpath=raw.get("structural_xpath"),
            xpath=raw.get("xpath"),
            shadow_hosts=list(raw.get("shadow_hosts") or []),
            viewport=raw.get("viewport"),
        )

    def describe(self) -> str:
        """Short human-readable element summary for the recording signal."""
        parts = []
        identity = self.tag or ""
        if self.id:
            identity += "#" + self.id
        if self.name:
            identity += f"[name={self.name}]"
        if identity:
            parts.append(identity)
        if self.role and not (self.tag and (self.id or self.name)):
            parts.append(f"role={self.role}")
        if self.text:
            text = re.sub(r"\s+", " ", self.text).strip()[:40]
            if text:
                parts.append(f'"{text}"')
        if not parts:
            return "element"
        return " ".join(parts)


@dataclass
class Event:
    """A single recorded user action.

    ``ts`` is epoch milliseconds (Date.now()/time.time()*1000), shared by the
    in-page recorder and the OS-level chrome hooks so session ordering is
    unambiguous; ``frame_path`` is the same-origin iframe chain from the top
    document to the event's frame; ``cross_origin_frame`` marks events that
    arrived from a cross-origin iframe whose frame_path could not be captured.

    ``window_ordinal`` is the recorded window's index in the driver's
    ``window_handles`` at capture time (0 = the opener/first window, 1 = the
    first popup, ...).  It lets replay re-enter the SAME window an action ran
    in - even when a popup shares the opener's host, where url-host matching
    cannot tell them apart.  ``frame_index_path`` is the index-based frame
    chain (top-down) the drain walked to reach the event's document; unlike
    the locator-based ``frame_path`` it also describes cross-origin frames,
    so replay can re-enter the exact frame deterministically.
    """

    type: str
    ts: float
    locator: Optional[Locator] = None
    frame_path: list[dict[str, Any]] = field(default_factory=list)
    cross_origin_frame: bool = False
    window_ordinal: Optional[int] = None
    frame_index_path: list[int] = field(default_factory=list)
    value: Optional[str] = None
    coordinates: Optional[dict[str, int]] = None
    button: Optional[str] = None
    key: Optional[str] = None
    modifiers: list[str] = field(default_factory=list)
    state: Optional[str] = None  # key press/release state: "down" | "up"
    repeat: Optional[int] = None  # coalesced key-repeat count (reserved)
    action: Optional[str] = None  # semantic browser-chrome action (back/forward/...)
    scroll: Optional[dict[str, Any]] = None
    url: Optional[str] = None
    title: Optional[str] = None
    sensitive: bool = False
    dom_snapshot: Optional[str] = None  # debug aid only, never used by replay
    gap_ms: Optional[int] = None  # recorded ms since the previous action (pacing)
    context: dict[str, Any] = field(default_factory=dict)
    # Repeating-element marker: selectors that match the WHOLE set a click
    # belongs to (a "kind"), so replay can act on each match in order instead of
    # the single recorded instance.  ``selector`` is the strongest candidate
    # (kept for older readers); ``selectors`` is the ordered list replay tries
    # (stable, container-scoped first, volatile classes last).  ``key_attr``
    # names a stable data-* the item is identified by; None means use the
    # element's POSITION in the matched set.
    entity: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "ts": self.ts,
            "locator": self.locator.to_dict() if self.locator else None,
            "frame_path": self.frame_path,
            "cross_origin_frame": self.cross_origin_frame,
            "window_ordinal": self.window_ordinal,
            "frame_index_path": self.frame_index_path,
            "value": self.value,
            "coordinates": self.coordinates,
            "button": self.button,
            "key": self.key,
            "modifiers": self.modifiers,
            "state": self.state,
            "repeat": self.repeat,
            "action": self.action,
            "scroll": self.scroll,
            "url": self.url,
            "title": self.title,
            "sensitive": self.sensitive,
            "dom_snapshot": self.dom_snapshot,
            "gap_ms": self.gap_ms,
            "context": self.context,
            "entity": self.entity,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Event":
        return cls(
            type=raw.get("type") or "unknown",
            ts=float(raw.get("ts") or 0.0),
            locator=Locator.from_dict(raw.get("locator")),
            frame_path=list(raw.get("frame_path") or []),
            cross_origin_frame=bool(raw.get("cross_origin_frame")),
            window_ordinal=(int(raw["window_ordinal"])
                            if raw.get("window_ordinal") is not None else None),
            frame_index_path=[int(i) for i in (raw.get("frame_index_path") or [])],
            value=raw.get("value"),
            coordinates=raw.get("coordinates"),
            button=raw.get("button"),
            key=raw.get("key"),
            modifiers=list(raw.get("modifiers") or []),
            state=raw.get("state"),
            repeat=raw.get("repeat"),
            action=raw.get("action"),
            scroll=raw.get("scroll"),
            url=raw.get("url"),
            title=raw.get("title"),
            sensitive=bool(raw.get("sensitive")),
            dom_snapshot=raw.get("dom_snapshot"),
            gap_ms=raw.get("gap_ms"),
            context=dict(raw.get("context") or {}),
            entity=(dict(raw.get("entity")) if raw.get("entity") else None),
        )

    def describe(self) -> str:
        """One-line summary of the action, used for the live recording signal."""
        target = self.locator.describe() if self.locator else "page"
        if self.type == "navigate":
            return f"navigate to {self.url or 'unknown url'}"
        if self.type in ("click", "dblclick", "contextmenu"):
            if self.locator:
                suffix = " (repeating element)" if self.entity else ""
                return f"{self.type} {target}{suffix}"
            if self.coordinates:
                return f"{self.type} at ({self.coordinates.get('x', '?')}, {self.coordinates.get('y', '?')})"
            return f"{self.type} (no target)"
        if self.type == "hover":
            return f"hover {target}"
        if self.type == "focus":
            return f"focus {target}"
        if self.type == "type":
            shown = "*****" if self.sensitive else (self.value or "")
            return f'type "{shown}" into {target}'
        if self.type == "key":
            mods = "+".join(self.modifiers)
            mods = mods + "+" if mods else ""
            return f"press {mods}{self.key or '?'}"
        if self.type == "scroll":
            s = self.scroll or {}
            if "delta_x" in s or "delta_y" in s:
                return f"scroll dx={s.get('delta_x', 0)} dy={s.get('delta_y', 0)}"
            return f"scroll {s}"
        if self.type == "select":
            return f'select "{self.value or ""}" in {target}'
        if self.type == "submit":
            return f"submit {target}"
        if self.type == "chrome":
            return f"chrome {self.action or 'action'}"
        return f"{self.type} on {target}"


def save_session(
    actions: list[Event],
    path: str | Path,
    *,
    start_url: Optional[str] = None,
    started_at: Optional[float] = None,
    duration_sec: Optional[float] = None,
    name: Optional[str] = None,
) -> dict[str, Any]:
    """Write a web session file: schema_version + metadata + typed actions.

    ``name`` (optional) is the user-facing label of the session, chosen at
    record time - it lets the GUI library show a friendly name instead of the
    timestamped filename.
    """
    started_at = started_at or time.time()
    metadata = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started_at)),
        "total_actions": len(actions),
        "duration_sec": round(duration_sec or 0.0, 2),
        "mode": MODE_WEB,
        "start_url": start_url,
    }
    if name:
        metadata["name"] = name
    payload = {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE_WEB,
        "metadata": metadata,
        "actions": [action.to_dict() for action in actions],
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def load_session(path: str | Path) -> dict[str, Any]:
    """Load a web session file, returning {'metadata', 'actions', 'schema_version'}."""
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"Session file not found: {source}")
    raw = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "actions" not in raw:
        raise ValueError(f"Not a web session file (missing 'actions' key): {source}")
    schema = int(raw.get("schema_version") or 1)
    if schema != SCHEMA_VERSION:
        logger.warning(
            "Web session schema_version %s != current %s: %s",
            schema, SCHEMA_VERSION, source,
        )
    actions = [Event.from_dict(item) for item in raw["actions"] if isinstance(item, dict)]
    return {
        "metadata": raw.get("metadata") or {},
        "actions": actions,
        "schema_version": schema,
    }


def is_web_session(path: str | Path) -> bool:
    """True when the JSON file at *path* is a web session (mode == 'web').

    Used by the sequence executor to route web sessions to the web engine and
    to reject desktop/chain files during web session resolution.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return False
    return (
        isinstance(data, dict)
        and isinstance(data.get("actions"), list)
        and data.get("mode") == MODE_WEB
    )
