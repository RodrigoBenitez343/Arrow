"""node_templates.py — the builder's node semantics and edit allowlists.

Every node type Arrow has, what it is FOR, and which properties the builder
may set through ``edit_node``.  The node objects themselves are created by
the live graph (``GraphManager.create_node``) so their full default field
sets come from the same NGUI node classes the canvas uses — the builder only
overrides semantic fields, never structure.
"""

import re

# ---------------------------------------------------------------- creatable

#: Node types the builder may create through the live canvas.
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
)

#: Types the builder deliberately declines (asks the user to add manually).
DECLINED_TYPES = ("mcp", "container")

# ---------------------------------------------------------------- registry

#: Short builder name -> the key ``GraphManager.create_node`` expects.  The
#: canvas registers nodes as ``<module>.<ClassName>`` (see
#: ``graph_manager.register_nodes``); passing the short name fails with
#: ``Can't find node: "code"``.
NODE_REGISTRY_KEY = {
    "input": "input.InputNode",
    "output": "output.OutputNode",
    "llm": "llm.LLMNode",
    "code": "code.CodeNode",
    "conditional": "conditional.ConditionalNode",
    "chain_import": "chain_import.ChainImportNode",
    "sequence": "sequence.SequenceNode",
    "web_sequence": "web_sequence.WebSequenceNode",
    "form_filler": "form_filler.FormFillerNode",
    "handle": "handle.HandleNode",
    "context": "context.ContextNode",
}

#: Lower-cased registry key -> short builder name (normalising what a live
#: node reports back).
_SHORT_BY_REGISTRY_KEY = {v.lower(): k for k, v in NODE_REGISTRY_KEY.items()}

# ---------------------------------------------------------------- purposes

#: What each node is for — a view over the runbook (single source of
#: truth: player/builder/runbook.py).
from player.builder.runbook import purpose_line as _purpose_line

NODE_PURPOSES = {
    _t: _purpose_line(_t)
    for _t in (
        "input", "output", "llm", "code", "conditional", "chain_import",
        "sequence", "web_sequence", "form_filler", "handle", "context",
        "mcp", "container",
    )
}

# -------------------------------------------------------------- allowlists

#: Properties the builder may write through edit_node, per node type.
#: Everything else is refused (the ConfigManager allowlist lesson: a property
#: that is not persisted silently disappears).
NODE_EDIT_FIELDS = {
    "input": (
        "label", "default_value", "user_prompt", "passthrough", "web_mode",
        "agent_modifiable", "decision_mode", "decision_criterion",
        "decision_evaluator", "decision_model", "decision_default",
        "question_mode", "choices", "route_on_answer", "accept_text",
        "accept_images", "accept_documents", "tts_enabled", "tts_text",
    ),
    "output": (
        "label", "variable_name", "agent_visible", "overlay_visible",
        "popup_on_finish", "render_mode", "show_rating", "image_source",
        "tts_enabled", "tts_text",
    ),
    "llm": (
        "prompt", "system_message", "model", "temperature", "max_tokens",
        "api_url", "write_text", "use_vision", "vision_model",
        "screenshot_enabled", "input_source", "orchestrator_mode",
        "orch_max_steps", "orch_goal", "orch_synthesize",
        "orch_synthesis_system", "orch_use_goal_ledger", "orch_headless",
        "use_llamacpp", "llamacpp_model_path", "web_mode",
        "semantic_description", "output_variable", "use_async",
        "ocr_region", "ocr_confidence", "typing_batch_size",
        "typing_batch_delay", "rag_top_k",
    ),
    "code": ("code", "gen_model", "input_vars", "output_vars"),
    "conditional": (
        "label", "decision_criterion", "decision_evaluator", "llm_model",
        "web_mode", "web_condition_type", "condition_type", "llm_prompt",
        "llm_engine", "llm_use_vision", "llm_timeout", "code",
        "description",
    ),
    "chain_import": (
        "chain_file_path", "import_mode", "loop_count", "prefix", "enabled",
    ),
    "sequence": (
        "sequence_file", "description", "loop_count", "extra_delay",
        "use_app_opened",
    ),
    "web_sequence": (
        "session_file", "sequence_file", "description", "name",
        "loop_count", "speed", "headless", "native_actions", "repeat_mode",
        "extra_delay", "extract_items",
    ),
    "form_filler": (
        "mode", "instruction", "fields_include", "fields_skip", "engine",
        "model", "verify", "repair",
    ),
    "handle": ("prompt", "description"),
    "context": ("label", "persistent", "clear_on_finish", "max_history",
                "scope"),
    "mcp": ("label",),
    "container": ("label",),
}


# -------------------------------------------------------------- field schema
#
# Machine-readable field schema: for every editable field its KIND and — for
# closed-set fields — the canonical OPTION values.  ONE source of truth for
# both (a) deterministic value normalisation when the builder writes a field
# and (b) the builder's Laya resolver (``choice`` over the field menu, then
# ``choice`` over that field's options).  Options are the lowercase RUNTIME
# values the nodes and the executor compare against — never the UI labels.

KIND_BOOL = "bool"
KIND_ENUM = "enum"
KIND_NUMBER = "number"
KIND_TEXT = "text"
KIND_MODEL = "model"   # pick from the available model list
KIND_PATH = "path"     # pick from a library (chains/sequences) or a file
KIND_JSON = "json"


def _f(kind, options=(), help=""):
    """One field spec: kind + closed options (empty for open kinds) + help."""
    return {"kind": kind, "options": tuple(options), "help": help}


#: field name -> spec, shared across node types.  ``help`` is the one phrase
#: the Laya field picker reads when choosing which field an ask refers to.
_FIELD = {
    # free text
    "label": _f(KIND_TEXT, help="short human-readable name / description"),
    "name": _f(KIND_TEXT, help="short human-readable name"),
    "description": _f(KIND_TEXT, help="what this node does"),
    "semantic_description": _f(KIND_TEXT, help="short semantic label for routing"),
    "variable_name": _f(KIND_TEXT, help="name of the variable this node writes"),
    "output_variable": _f(KIND_TEXT, help="variable the LLM output is stored in"),
    "default_value": _f(KIND_TEXT, help="default / fallback value"),
    "user_prompt": _f(KIND_TEXT, help="question shown when user input is requested"),
    "prompt": _f(KIND_TEXT, help="the instruction / prompt text"),
    "system_message": _f(KIND_TEXT, help="system message prepended to the prompt"),
    "llm_prompt": _f(KIND_TEXT, help="prompt for the LLM condition"),
    "orch_goal": _f(KIND_TEXT, help="high-level goal for orchestrator mode"),
    "orch_synthesis_system": _f(KIND_TEXT, help="system message for final synthesis"),
    "instruction": _f(KIND_TEXT, help="instruction / brief for the node"),
    "tts_text": _f(KIND_TEXT, help="static text to speak"),
    "prefix": _f(KIND_TEXT, help="name prefix applied to imported nodes"),
    "fields_include": _f(KIND_TEXT, help="list of form fields to fill"),
    "fields_skip": _f(KIND_TEXT, help="list of form fields to skip"),
    "api_url": _f(KIND_TEXT, help="API endpoint URL"),
    "ocr_region": _f(KIND_TEXT, help="screen region [x,y,w,h] or element locator"),
    "code": _f(KIND_TEXT, help="python source to run"),
    "input_vars": _f(KIND_JSON, help="declared named input ports (variables the code reads)"),
    "output_vars": _f(KIND_JSON, help="declared named output ports (variables the code sets)"),
    "decision_criterion": _f(KIND_TEXT, help="natural-language pass/fail criterion"),
    "repeat_mode": _f(KIND_TEXT, help="count | repeating set (advance the marked element set)"),

    # bools
    "passthrough": _f(KIND_BOOL, help="data-only input, do not type on screen"),
    "web_mode": _f(KIND_BOOL, help="act on the browser, not the desktop"),
    "agent_modifiable": _f(KIND_BOOL, help="parent agent may fill this value"),
    "decision_mode": _f(KIND_BOOL, help="judge the value and route true/false"),
    "decision_default": _f(KIND_BOOL, help="branch taken when the judge fails"),
    "route_on_answer": _f(KIND_BOOL, help="yes/no answer drives the branch"),
    "accept_text": _f(KIND_BOOL, help="accept text input"),
    "accept_images": _f(KIND_BOOL, help="accept image input"),
    "accept_documents": _f(KIND_BOOL, help="accept document input"),
    "tts_enabled": _f(KIND_BOOL, help="speak the text"),
    "agent_visible": _f(KIND_BOOL, help="visible to Agent Mode"),
    "overlay_visible": _f(KIND_BOOL, help="post to the agent overlay"),
    "popup_on_finish": _f(KIND_BOOL, help="show a popup when done (manual mode)"),
    "show_rating": _f(KIND_BOOL, help="show the rating widget"),
    "write_text": _f(KIND_BOOL, help="type the answer on screen"),
    "use_vision": _f(KIND_BOOL, help="attach a screenshot to the request"),
    "llm_use_vision": _f(KIND_BOOL, help="attach a screenshot to the LLM condition"),
    "screenshot_enabled": _f(KIND_BOOL, help="capture the screen"),
    "orchestrator_mode": _f(KIND_BOOL, help="use orchestrator (tools/brains) mode"),
    "orch_synthesize": _f(KIND_BOOL, help="synthesize a final answer from the steps"),
    "orch_use_goal_ledger": _f(KIND_BOOL, help="keep a goal ledger across steps"),
    "orch_headless": _f(KIND_BOOL, help="run the orchestrator without the overlay"),
    "use_llamacpp": _f(KIND_BOOL, help="run on local llama.cpp instead of Ollama"),
    "use_async": _f(KIND_BOOL, help="run asynchronously"),
    "use_direct_rag": _f(KIND_BOOL, help="retrieve context directly before the call"),
    "verify": _f(KIND_BOOL, help="verify filled values and repair"),
    "repair": _f(KIND_BOOL, help="attempt repair on mismatched values"),
    "enabled": _f(KIND_BOOL, help="node is active"),
    "use_app_opened": _f(KIND_BOOL, help="use the already-opened app"),
    "headless": _f(KIND_BOOL, help="run the browser headless"),
    "native_actions": _f(KIND_BOOL, help="use native actions where possible"),
    "persistent": _f(KIND_BOOL, help="keep context across runs"),
    "clear_on_finish": _f(KIND_BOOL, help="clear stored data when the chain ends"),

    # numbers
    "temperature": _f(KIND_NUMBER, help="sampling temperature"),
    "max_tokens": _f(KIND_NUMBER, help="max tokens to generate"),
    "orch_max_steps": _f(KIND_NUMBER, help="max orchestrator steps"),
    "ocr_confidence": _f(KIND_NUMBER, help="OCR confidence threshold"),
    "typing_batch_size": _f(KIND_NUMBER, help="characters per typing batch"),
    "typing_batch_delay": _f(KIND_NUMBER, help="delay between typing batches"),
    "rag_top_k": _f(KIND_NUMBER, help="retrieved chunks to use"),
    "loop_count": _f(KIND_NUMBER, help="how many times to repeat"),
    "extra_delay": _f(KIND_NUMBER, help="delay between repetitions (s)"),
    "speed": _f(KIND_NUMBER, help="replay speed multiplier"),
    "max_history": _f(KIND_NUMBER, help="recent entries to keep (0 = all)"),
    "llm_timeout": _f(KIND_NUMBER, help="LLM condition timeout (s)"),

    # model / path picks
    "model": _f(KIND_MODEL, help="the model to run"),
    "vision_model": _f(KIND_MODEL, help="the vision model"),
    "gen_model": _f(KIND_MODEL, help="model used to generate code"),
    "llm_model": _f(KIND_MODEL, help="model for the LLM condition"),
    "llamacpp_model_path": _f(KIND_MODEL, help="local GGUF model file"),
    "chain_file_path": _f(KIND_PATH, help="imported chain file"),
    "sequence_file": _f(KIND_PATH, help="recorded sequence file"),
    "session_file": _f(KIND_PATH, help="recorded web session file"),
    "image_source": _f(KIND_PATH, help="image file for image render mode"),

    # enums (canonical runtime values)
    "decision_evaluator": _f(KIND_ENUM, ("llm", "laya"), help="judge with the LLM or Laya"),
    "question_mode": _f(KIND_ENUM, ("text", "yes_no", "choice"), help="kind of question asked"),
    "render_mode": _f(KIND_ENUM, ("text", "audio", "image", "html", "markdown"), help="how the output is shown"),
    "input_source": _f(KIND_ENUM, ("none", "ocr", "page_text", "clipboard"), help="where the node reads input"),
    "condition_type": _f(KIND_ENUM, ("presence", "absence", "ocr", "llm", "code", "layout_match"), help="what the condition tests"),
    "web_condition_type": _f(KIND_ENUM, ("element_located", "text_present", "browser_js"), help="what the web condition tests"),
    "llm_engine": _f(KIND_ENUM, ("ollama", "llamacpp"), help="engine for the LLM condition"),
    "engine": _f(KIND_ENUM, ("llamacpp", "ollama"), help="embedding / inference engine"),
    "mode": _f(KIND_ENUM, ("web", "desktop"), help="form-filling target surface"),
    "import_mode": _f(KIND_ENUM, ("full", "sequences_only", "nodes_only"), help="what to import"),
    "scope": _f(KIND_ENUM, ("local", "global"), help="context scope"),
}


#: Per-node, per-field kind + options.  One source of truth for deterministic
#: normalisation AND the builder's Laya field/option resolver.
NODE_SCHEMA = {
    _t: {_field: _FIELD.get(_field) or _f(KIND_TEXT) for _field in _fields}
    for _t, _fields in NODE_EDIT_FIELDS.items()
}


#: Ports the orchestrator LLM node exposes while the switch is ON
#: (deterministic chains on tools, sub-brains on brains).
ORCH_TOOLS_PORT = "tools"
ORCH_BRAINS_PORT = "brains"

# ------------------------------------------------------------------ helpers


def allowed_fields(node_type):
    """Editable property names for a node type (always a tuple).

    Accepts a short name, a registry key (``code.CodeNode``) or a bare class
    name (``CodeNode``).
    """
    return NODE_EDIT_FIELDS.get(short_type(node_type), ())


def slugify(name):
    """Filesystem-safe chain name: lowercase alnum runs joined by '_'."""
    slug = re.sub(r"[^a-z0-9]+", "_", str(name or "").strip().lower())
    return slug.strip("_")[:40] or "built_chain"


def node_type_of(node):
    """Best-effort runtime type of a NodeGraphQt node object."""
    for attr in ("type_", "__identifier__"):
        try:
            v = getattr(node, attr, None)
            if v:
                return str(v)
        except Exception:
            continue
    try:
        return str(node.__class__.__name__).lower()
    except Exception:
        return ""


def registry_key(node_type):
    """The canvas registration key for a short builder node type.

    Unknown types pass through unchanged (the canvas then reports its own
    ``Can't find node`` error).
    """
    raw = str(node_type or "").strip()
    return NODE_REGISTRY_KEY.get(raw.lower(), raw)


def short_type(node_type):
    """Normalise a node's reported type to the short builder name.

    Accepts a short name (``code``), a registry key (``code.CodeNode``) or a
    bare class name (``CodeNode``) — a live node may report any of them.
    """
    raw = str(node_type or "").strip()
    if not raw:
        return ""
    mapped = _SHORT_BY_REGISTRY_KEY.get(raw.lower())
    if mapped:
        return mapped
    tail = raw.rsplit(".", 1)[-1]
    if tail.lower().endswith("node") and len(tail) > 4:
        tail = tail[:-4]
    return tail.lower()


# ------------------------------------------------------------ schema access


def field_spec(node_type, field):
    """The kind + options + help for one editable field, or None."""
    return (NODE_SCHEMA.get(short_type(node_type)) or {}).get(str(field or ""))


def field_menu(node_type):
    """{field: help} for every editable field of a node — the Laya field picker."""
    return {
        _field: _spec["help"]
        for _field, _spec in (NODE_SCHEMA.get(short_type(node_type)) or {}).items()
    }


def option_menu(node_type, field):
    """{option: ''} for a closed enum field, else {} — the Laya value picker."""
    _spec = field_spec(node_type, field)
    if not _spec or _spec.get("kind") != KIND_ENUM:
        return {}
    return {o: "" for o in _spec.get("options", ())}


_TRUE_WORDS = ("true", "1", "yes", "on", "y")
_FALSE_WORDS = ("false", "0", "no", "off", "n", "")


def coerce_bool(value, default=False):
    """A bool from a string / bool / int without assuming the type (the builder
    writes native bools; the node classes write 'true'/'false' strings)."""
    if isinstance(value, bool):
        return value
    _s = str(value).strip().lower()
    if _s in _TRUE_WORDS:
        return True
    if _s in _FALSE_WORDS:
        return False
    return default


def _fold(text):
    """alnum-only lowercase key for tolerant option matching ('llama.cpp' ->
    'llamacpp', 'page text' -> 'pagetext')."""
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def normalize_value(node_type, field, value):
    """Best-effort canonical form of *value* for *field*.

    bool -> 'true'/'false' string, number -> its string form, enum -> the
    matching canonical option (fold match).  This is the deterministic first
    pass that lands a native-typed / natural-language write in the exact form
    the node and the executor read; an unresolved enum is left unchanged for
    the Laya resolver.  Open fields (text / model / path / json) pass through.
    """
    _spec = field_spec(node_type, field)
    if not _spec:
        return value
    _kind = _spec.get("kind")
    if _kind == KIND_BOOL:
        if isinstance(value, bool):
            return "true" if value else "false"
        _s = str(value).strip().lower()
        if _s in _TRUE_WORDS:
            return "true"
        if _s in _FALSE_WORDS:
            return "false"
        return value
    if _kind == KIND_NUMBER:
        if isinstance(value, bool):
            return value
        return str(value)
    if _kind == KIND_ENUM:
        _opts = _spec.get("options", ())
        if value in _opts:
            return value
        _folded = _fold(value)
        for _opt in _opts:
            if _fold(_opt) == _folded:
                return _opt
        return value
    return value
