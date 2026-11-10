"""node_templates.py — Arrow's node vocabulary for the builder.

The builder no longer creates or edits nodes, so this module is now the
**reading vocabulary**: the node types a chain can contain, what role each
one plays in a run, the fields that describe its behaviour, and the
normalisation of the several type strings a live node may report.

The port side of the vocabulary lives in ``ports.py``.
"""

import re

#: Node types the reader knows (creation is not a builder capability).
NODE_TYPES = (
    "input",
    "output",
    "llm",
    "code",
    "conditional",
    "chain_import",
    "sequence",
    "web_sequence",
    "form_filler",
    "handle",
    "context",
    "action",
    "mcp",
    "container",
)

#: Types the builder never reasons about beyond naming them.
SECONDARY_TYPES = ("mcp", "container", "action")

#: Lower-cased registry key / class name -> short builder name.
_REGISTRY_KEY = {
    "input.InputNode": "input",
    "output.OutputNode": "output",
    "llm.LLMNode": "llm",
    "code.CodeNode": "code",
    "conditional.ConditionalNode": "conditional",
    "chain_import.ChainImportNode": "chain_import",
    "sequence.SequenceNode": "sequence",
    "web_sequence.WebSequenceNode": "web_sequence",
    "form_filler.FormFillerNode": "form_filler",
    "handle.HandleNode": "handle",
    "context.ContextNode": "context",
    "action.ActionNode": "action",
    "mcp.MCPNode": "mcp",
    "container.ContainerNode": "container",
}

#: What a node DOES in an execution walk.  The digest prints this next to
#: every step so "what runs next" and "why" never come apart.
NODE_ROLE = {
    "sequence": "ACT",
    "web_sequence": "ACT",
    "action": "ACT",
    "handle": "ACT",
    "form_filler": "ACT",
    "chain_import": "ACT",
    "code": "COMPUTE",
    "llm": "THINK",
    "conditional": "CHECK",
    "input": "VALUE",
    "output": "REPORT",
    "context": "MEMORY",
    "mcp": "TOOL",
    "container": "GROUP",
}

#: Nodes that never run on their own: they are consulted through data ports.
PASSIVE_TYPES = ("context",)


def _flatten(text):
    """Case/separator-insensitive form of a type token (``web_sequence`` ->
    ``websequence``), so every spelling of one type collapses to one key."""
    return "".join(ch for ch in str(text or "").lower()
                   if ch.isalnum())


#: Registry lookups are lower-cased in ``short_type``; the literal above is
#: not, so index it once here instead of never matching.
_REGISTRY_KEY_LOWER = {key.lower(): value for key, value in _REGISTRY_KEY.items()}
#: Flattened identifier -> short name (``websequence`` -> ``web_sequence``).
_FLAT_TYPES = {_flatten(name): name for name in NODE_TYPES}

#: Properties that describe one node's behaviour.  Read-only: used by the
#: digest and by ``read_node``.  Only properties the node class really
#: declares belong here (``node.properties()`` is the check).
NODE_FIELDS = {
    "input": (
        "label", "default_value", "user_prompt", "passthrough", "web_mode",
        "agent_modifiable", "decision_mode", "decision_criterion",
        "decision_evaluator", "decision_model", "decision_default",
        "question_mode", "choices", "route_on_answer", "accept_text",
        "accept_images", "accept_documents", "tts_enabled", "tts_text",
        "description",
    ),
    "output": (
        "label", "variable_name", "agent_visible", "overlay_visible",
        "popup_on_finish", "render_mode", "show_rating", "image_source",
        "tts_enabled", "tts_text", "description",
    ),
    "llm": (
        "prompt", "system_message", "model", "temperature", "max_tokens",
        "api_url", "write_text", "use_vision", "vision_model",
        "screenshot_enabled", "input_source", "orchestrator_mode",
        "orch_max_steps", "orch_goal", "orch_synthesize",
        "orch_synthesis_system", "orch_use_goal_ledger", "orch_headless",
        "use_llamacpp", "llamacpp_model_path", "web_mode",
        "semantic_description", "output_variable", "use_async", "ocr_region",
        "ocr_confidence", "rag_top_k", "description",
    ),
    "code": ("code", "gen_model", "input_vars", "output_vars",
             "description"),
    # A check is its CRITERION: the file it looks for, the text it reads, the
    # snippet it runs, the sentence it asks.  Leaving those out of this list
    # made every live check describe itself as "a condition it was picked for"
    # — the mechanism, never the thing being evaluated.
    "conditional": (
        "condition_type", "decision_criterion", "decision_evaluator",
        # desktop visual / OCR / wait payload: WHAT the check looks at
        "image_path", "image_data", "threshold", "confidence", "timeout",
        "wait_time", "max_attempts", "target_text", "ocr_text", "region",
        "case_sensitive", "target_x", "target_y", "target_w", "target_h",
        # a small model classifies what the prompt asks about
        "llm_model", "llm_engine", "llm_prompt", "llm_timeout",
        "llm_use_vision",
        # a Python snippet decides the branch
        "code", "description",
        # web mode reads the live page instead of the desktop screen
        "web_mode", "web_condition_type", "web_timeout",
        "web_element_locator", "web_layout_locator", "web_text_source",
        "web_text_locator", "web_target_text", "web_js", "web_llm_prompt",
        "web_llm_model", "web_llm_engine", "web_case_sensitive",
        # a file loop replays a chain while its own criterion holds
        "loop_type", "loop_position", "sequence_file", "loop_condition",
        "max_loops",
    ),
    "chain_import": (
        "chain_file_path", "import_mode", "loop_count", "prefix", "enabled",
        "description",
    ),
    "sequence": (
        "sequence_file", "description", "loop_count", "extra_delay",
        "use_app_opened",
    ),
    "web_sequence": (
        "session_file", "sequence_file", "description", "name", "loop_count",
        "speed", "headless", "native_actions", "repeat_mode", "extra_delay",
        "extract_items",
    ),
    "form_filler": (
        "mode", "instruction", "fields_include", "fields_skip", "engine",
        "model", "verify", "repair", "crag_enabled", "crag_decompose_mode",
        "crag_top_k", "description",
    ),
    "handle": ("action_type", "goal_description", "target_description",
               "description"),
    "context": (
        "label", "persistent", "clear_on_finish", "index_ephemeral",
        "max_history", "scope", "documents", "skills", "description",
    ),
    "action": ("label", "action_type", "description"),
    "mcp": ("label", "description"),
    "container": ("label", "description"),
}

#: Fields whose value is a free-text document body; the digest never prints
#: them in full (they belong to ``read_node``, which prints a clipped copy).
LONG_FIELDS = ("prompt", "system_message", "code", "llm_prompt",
               "web_js", "instruction", "orch_goal", "orch_synthesis_system",
               "skills", "documents")


def short_type(node_type):
    """Normalise a reported node type to the short builder name.

    A live node reports its type in whichever spelling the registry it came
    from used: ``code``, ``CodeNode``, ``code.CodeNode`` — and, for the types
    whose identifier carries an underscore, the FLATTENED class name
    (``websequence`` for ``web_sequence.WebSequenceNode``).  All of them must
    land on the same table key, or the whole digest raises ``UnknownPortError``
    on a perfectly ordinary node.
    """
    raw = str(node_type or "").strip()
    if not raw:
        return ""
    mapped = _REGISTRY_KEY_LOWER.get(raw.lower())
    if mapped:
        return mapped
    tail = raw.rsplit(".", 1)[-1]
    if tail.lower().endswith("node") and len(tail) > 4:
        tail = tail[:-4]
    flat = _flatten(tail)
    return _FLAT_TYPES.get(flat, flat)


def node_type_of(node):
    """Best-effort runtime type of a live NodeGraphQt node object."""
    for attr in ("type_", "__identifier__"):
        value = getattr(node, attr, None)
        if value:
            return str(value)
    return str(node.__class__.__name__).lower()


def node_name(node):
    """The node's display name."""
    return str(node.name() or "")


def node_id(node):
    """The node's canvas id."""
    return str(getattr(node, "id", "") or "")


def fields_of(node_type):
    """The descriptive property names of a node type (always a tuple)."""
    return NODE_FIELDS.get(short_type(node_type), ())


def role_of(node_type):
    """The node's role word in an execution walk ('' when unknown)."""
    return NODE_ROLE.get(short_type(node_type), "")


def node_properties(node):
    """``{name: value}`` for the node's declared descriptive properties.

    NodeGraphQt keeps a node's CUSTOM properties nested under the serialised
    ``custom`` key (``node.properties()`` returns ``{'id', 'name', 'color',
    ..., 'custom': {'session_file': ...}}``), while ``get_property`` reads one
    value directly.  Reading only the top level silently reported EVERY node as
    having no config — which is how a lone ``web_sequence`` node whose
    recording was attached got described as "replays nothing".  Both shapes are
    read here: flat (a stub node, a snapshot) and nested (a live canvas).
    """
    declared = dict(node.properties() or {})
    custom = declared.pop('custom', None)
    if isinstance(custom, dict):
        declared.update(custom)
    ntype = short_type(node_type_of(node))
    out = {}
    for field in fields_of(ntype):
        if field in declared:
            out[field] = declared[field]
    return out


def meaningful(props):
    """Drop empty property values (None, '', [], {}, '[]')."""
    out = {}
    for name, value in (props or {}).items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, str) and value.strip() in ("[]", "{}"):
            continue
        if isinstance(value, (list, dict, tuple)) and not value:
            continue
        out[name] = value
    return out


def slug(text, limit=40):
    """Filesystem-safe token (used only for report labels)."""
    value = re.sub(r"[^a-z0-9]+", "_", str(text or "").strip().lower())
    return value.strip("_")[:limit] or "node"
