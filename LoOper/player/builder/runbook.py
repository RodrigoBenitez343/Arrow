"""runbook.py — the Arrow node runbook: one structured source of truth.

Every node type the builder may create: what it is FOR, the ports it exposes
(collected from ``NGUI/nodes_resources/*.py`` — the source of truth), the
fields that matter (aligned with ``node_templates.NODE_EDIT_FIELDS``), when to
use it, and when the builder must ASK the user instead of guessing.

Consumed by the builder chat (``laya_grades.pick_node_type`` criteria,
``node_templates.NODE_PURPOSES``) and rendered to ``docs/NODE_RUNBOOK.md``:

    python -m player.builder.runbook --write     # regenerate the doc
    python -m player.builder.runbook --print     # stdout
"""

from typing import Dict, List, Tuple

#: Node types the builder deliberately declines (user adds them manually).
DECLINED_TYPES = ("mcp", "container")

#: Per-node runbook entries.  Keep each field meaning to one line.
RUNBOOK: Dict[str, dict] = {
    "input": {
        "label": "Input",
        "purpose": (
            "Data injection / asking: resolves a value (agent slot, default, "
            "carry, or a user question) and feeds it downstream; with "
            "decision mode ON it also judges the value and routes true/false."
        ),
        "ports_in": ("input",),
        "ports_out": ("output", "data"),
        "fields": {
            "label": "the slot's name; agent_modifiable slots are advertised "
                     "by this label (bind_input targets it)",
            "default_value": "value used when nothing else resolves",
            "user_prompt": "the question shown in agent mode",
            "passthrough": "data-only mode: no screen typing, just the value",
            "web_mode": "resolve from the shared browser page instead of asking",
            "agent_modifiable": "the agent (or a bound value) fills this slot",
            "question_mode": "how the node asks: free text / yes-no / choices",
            "choices": "the fixed answer set for choice questions",
            "route_on_answer": "yes/no answers drive the true/false ports",
            "accept_text": "accept free text alongside a choice question",
            "decision_mode": "the node itself judges the resolved value",
            "decision_criterion": "natural-language criterion for the judgement",
            "decision_evaluator": "llm (one small turn) or laya (embedded, typed)",
            "decision_model": "provider:model for the judgement; blank borrows "
                              "the chain LLM node, else the app default",
            "decision_default": "the fail-closed branch a decision takes when "
                                "the judgement cannot run",
            "accept_images": "accept pasted/attached images as the value",
            "accept_documents": "accept attached documents as the value",
            "tts_enabled": "speak the resolved value",
            "tts_text": "spoken text instead of the raw value",
        },
        "modes": {
            "passthrough": "data-only: resolves the value and forwards it; "
                           "nothing is typed on screen",
            "ask": "asks the user (question_mode text/yes_no/choice); "
                   "route_on_answer drives the true/false ports on yes/no",
            "web": "web_mode: types the resolved value into the shared "
                   "browser's focused editable instead of the desktop",
            "agent slot": "agent_modifiable: the agent (or bind_input) "
                          "fills it; advertised to the planner by its label",
            "decision": "decision_mode: judges its own resolved value "
                        "against decision_criterion (evaluator llm or laya) "
                        "and routes true/false; decision_default picks the "
                        "fail-closed branch",
        },
        "when_to_use": (
            "A chain needs a parameter or the user's goal before acting.",
            "A step needs a yes/no gate (decision mode + route_on_answer).",
            "The agent should fill a value at run time (agent_modifiable).",
        ),
        "when_to_ask": (
            "What the slot means / the exact question wording.",
            "The choice set or default value when the goal does not say it.",
        ),
        "example": "Input 'query' (agent_modifiable, question_mode=free) -> "
                   "drives a search sequence.",
    },
    "output": {
        "label": "Output",
        "purpose": (
            "Result sink: names the finished value and shows it to the "
            "user/agent (agent_visible, render_mode, popup_on_finish)."
        ),
        "ports_in": ("input",),
        "ports_out": ("output",),
        "fields": {
            "label": "what the result is called",
            "variable_name": "variable the result publishes under",
            "agent_visible": "the agent (chat mode) can read this result",
            "overlay_visible": "show it on the canvas overlay",
            "popup_on_finish": "pop a dialog when the chain finishes",
            "render_mode": "how it is rendered: text | audio | image | "
                           "html | markdown",
            "show_rating": "show the thumbs-rating control on the popup "
                           "(feedback feeds description repair)",
            "image_source": "the image to show in image render mode",
            "tts_enabled": "speak the result",
            "tts_text": "spoken text instead of the raw result",
        },
        "modes": {
            "agent_visible": "the agent (chat mode) can read this result",
            "overlay_visible": "post it to the agent chat overlay",
            "popup_on_finish": "pop a dialog when the chain finishes "
                               "(manual runs)",
            "tts": "speak the collected content (tts_text as the fallback)",
        },
        "when_to_use": (
            "A system chain needs to hand a result back to the user/agent.",
            "A scraping/extraction chain must expose what it found.",
        ),
        "when_to_ask": (
            "Whether the result should be agent-visible or a UI popup.",
        ),
        "example": "Output variable_name='jobs' agent_visible=true.",
    },
    "llm": {
        "label": "LLM",
        "purpose": (
            "Reasoning/generation: a prompt over a model; orchestrator_mode "
            "morphs it into the goal-loop brain over the chains wired to its "
            "tools port, with sub-brains on its brains port."
        ),
        "ports_in": ("input", "prompt", "context"),
        "ports_out": ("output", "context"),
        "ports_in_orch_add": ("tools", "brains"),
        "ports_out_orch_add": ("trace", "route"),
        "fields": {
            "prompt": "the prompt template ({variable} placeholders allowed); "
                      "text wired to the 'prompt' port REPLACES it",
            "system_message": "system instructions (never replaced by ports)",
            "model": "provider:model for BYOK/cloud, an Ollama tag, or a GGUF "
                     "name for the local engine; blank = chain/app default",
            "temperature": "sampling temperature",
            "max_tokens": "generation budget",
            "api_url": "override the gateway URL for this node",
            "write_text": "type the answer on screen",
            "use_vision": "send a screenshot to a vision model",
            "vision_model": "the vision model used when use_vision is on",
            "screenshot_enabled": "capture the screen for the call",
            "input_source": "where input text comes from (none/clipboard/...)",
            "orchestrator_mode": "ON = this node becomes the system brain",
            "orch_max_steps": "maximum orchestrator steps per run",
            "orch_goal": "the standing goal the brain loops toward",
            "orch_synthesize": "synthesize a final answer after the loop",
            "orch_synthesis_system": "system prompt for that synthesis",
            "orch_use_goal_ledger": "track goal progress in a ledger across "
                                    "orchestrator steps",
            "orch_headless": "run the orchestrator's web work headless",
            "use_llamacpp": "run against the built-in llama.cpp engine",
            "llamacpp_model_path": "GGUF file for the engine",
            "web_mode": "reason over the shared browser page",
            "semantic_description": "what this node does (agent routing)",
            "output_variable": "variable the answer publishes under",
            "use_async": "run the call on a worker (UI stays responsive)",
            "ocr_region": "screen region read with OCR before the call",
            "ocr_confidence": "OCR acceptance floor for that read",
            "typing_batch_size": "characters per batch when write_text types",
            "typing_batch_delay": "delay between typing batches (seconds)",
            "rag_top_k": "retrieved chunks fed from the direct RAG store",
        },
        "modes": {
            "vanilla": "orchestrator_mode OFF: one prompt over a model; "
                       "text wired to the prompt port REPLACES the template",
            "orchestrator": "orchestrator_mode ON: the system brain: a "
                            "goal loop (orch_goal) over the chains wired to "
                            "tools, sub-brains on brains, trace/route "
                            "outputs; orch_max_steps caps one activation",
            "vision": "use_vision + screenshot_enabled: the call carries a "
                      "screenshot to vision_model",
            "web": "web_mode: reasons over the shared browser page",
        },
        "when_to_use": (
            "A step needs judgement, extraction, or generation.",
            "A system chain needs its brain: orchestrator_mode=true.",
            "A nested level needs a sub-brain (its own orchestrator chain).",
        ),
        "when_to_ask": (
            "The goal/persona when the user's wording does not state it.",
            "Which model/provider when the choice matters to them.",
        ),
        "example": "LLM orchestrator_mode=true, tools port <- scraping "
                   "chain_import, brains port <- login sub-brain.",
    },
    "code": {
        "label": "Code",
        "purpose": (
            "Deterministic logic: a self-contained Python script; data "
            "travels through NAMED IO ports (the node's IO editor) — the "
            "code reads the declared input variable(s) and sets the declared "
            "output variable(s) (defaults: data in, result out)."
        ),
        "ports_in": ("input",),
        "ports_out": ("output", "error"),
        "fields": {
            "code": ("the Python source; reads the named input var, sets "
                     "the named output var"),
            "gen_model": "provider:model the coder model uses to write it",
            "input_vars": ("declared named input ports (JSON [{name,type}]) "
                           "the code reads"),
            "output_vars": ("declared named output ports (JSON [{name,type}]) "
                            "the code sets"),
        },
        "modes": {
            "static": "the code text IS the program (named input var in, "
                      "named output var out) — exact every run, no model in "
                      "the loop",
            "generated": "the Code Node Studio chat authors the code from "
                         "gen_prompt with gen_model; the node still runs the "
                         "plain text",
        },
        "when_to_use": (
            "Parsing/transforming data (JSON, CSV, regex, math).",
            "Anything that must be exact every run (no model in the loop).",
        ),
        "when_to_ask": (
            "What the transformation must produce when the goal is vague.",
        ),
        "example": "Code: result = [j for j in input_data if j['remote']].",
    },
    "conditional": {
        "label": "Conditional",
        "purpose": (
            "Branching: routes its true/false output ports on a criterion "
            "(one small LLM turn or Laya); web_mode routes on the shared "
            "browser page condition instead."
        ),
        "ports_in": ("input",),
        "ports_out": ("true", "false"),
        "fields": {
            "label": "what the branch decides",
            "decision_criterion": "the natural-language condition",
            "decision_evaluator": "llm (one small turn) or laya (typed, fast)",
            "web_mode": "judge a browser-page condition instead",
            "web_condition_type": "which web condition (presence/text/url/...)",
            "llm_model": "provider:model for the judgement; blank = default",
            "condition_type": "which evaluator routes it (llm / code / "
                              "presence / ocr / loops — the dialog sets it)",
            "llm_prompt": "the criterion an LLM validates (condition_type llm)",
            "llm_engine": "ollama | llamacpp — where the evaluator runs",
            "llm_use_vision": "let the evaluator read a screenshot too",
            "llm_timeout": "evaluator timeout in seconds",
            "code": "python for condition_type code: set result True/False",
            "description": "what this check decides (orchestrator routing "
                           "context)",
        },
        "modes": {
            "desktop visual": "web_mode OFF: routes on the screen — "
                              "presence/absence triggers, OCR, layout "
                              "match, and while/until loops",
            "web": "web_mode ON: routes on the shared browser page; "
                   "web_condition_type = element_located | text_present | "
                   "browser_js | llm | layout_match",
            "ports": "true / false outputs; the unchosen branch is skipped",
        },
        "when_to_use": (
            "The chain must take different paths on a judgement.",
            "Error/fallback routing (true = happy path, false = recovery).",
        ),
        "when_to_ask": (
            "What decides the branch when the goal does not say it.",
            "Which path is the safe default (fail-open vs fail-closed).",
        ),
        "example": "Conditional 'page shows results' -> true: extract, "
                   "false: retry search.",
    },
    "chain_import": {
        "label": "Chain Import",
        "purpose": (
            "Composition: runs another chain file in place. classify_chain "
            "decides its role: contains an orchestrator -> brain (brains "
            "port), otherwise a deterministic chain (tools port)."
        ),
        "ports_in": ("input",),
        "ports_out": ("output",),
        "fields": {
            "chain_file_path": "the chain JSON to run",
            "import_mode": "full / sequences_only / nodes_only",
            "loop_count": "how many times to run it",
            "prefix": "prefix added to the imported node names",
            "enabled": "temporarily bypass without deleting",
        },
        "modes": {
            "import_mode": "full | sequences_only | nodes_only — how much "
                           "of the file runs",
            "repeat": "loop_count runs the imported chain N times with "
                      "extra_delay between passes",
            "brain vs chain": "kind is decided by content (classify_chain): "
                              "contains an orchestrator → wired to brains, "
                              "otherwise a deterministic chain → tools",
        },
        "when_to_use": (
            "Compose an existing chain into a system chain (tools port).",
            "Wire a sub-brain into an orchestrator (brains port).",
        ),
        "when_to_ask": (
            "Which existing chain to import (list chains for the user).",
        ),
        "example": "Chain_import 'linkedin scrape.json' on the tools port.",
    },
    "sequence": {
        "label": "Sequence (desktop)",
        "purpose": (
            "Recorded desktop actions: replays a sequences/*.json captured "
            "by the desktop recorder (clicks, typing, scrolls, drags)."
        ),
        "ports_in": ("input",),
        "ports_out": ("output",),
        "fields": {
            "sequence_file": "the recording file",
            "description": "what the recording does (agent routing)",
            "loop_count": "run the recording N times",
            "extra_delay": "seconds between passes / before replay",
            "use_app_opened": "target the app this sequence opened",
        },
        "modes": {
            "element-first": "replays by re-finding the recorded UIA "
                             "element and clicking its live box (survives a "
                             "moved window / scrolled list)",
            "visual": "clicks recorded with right-Ctrl (match='visual') "
                      "replay by template-matching the recorded image crop",
            "repeating set": "Insert-marked rows: each pass clicks the "
                             "NEXT unclicked row of the recorded set",
            "app guard": "the recorded app (app_context) is verified and "
                         "focused before replay",
        },
        "when_to_use": (
            "A step is mechanical desktop interaction the user demonstrated.",
            "Login/navigation flows on native apps.",
        ),
        "when_to_ask": (
            "Always ask the user to RECORD the step when no recording exists.",
        ),
        "example": "Sequence 'click apply.json' after a form chain.",
    },
    "web_sequence": {
        "label": "Web Sequence",
        "purpose": (
            "Recorded DOM actions: replays a web_sequences/*.json captured by "
            "the CDP web recorder — element-targeted, cursor never hijacked."
        ),
        "ports_in": ("input",),
        "ports_out": ("output", "ctx_out"),
        "fields": {
            "session_file": "the web recording file (recordings live in "
                            "web_sequences/)",
            "sequence_file": "legacy alias for session_file",
            "name": "display name of the recording",
            "description": "what the recording does (agent routing)",
            "loop_count": "run the recording N times",
            "speed": "replay speed multiplier",
            "headless": "replay without showing the browser window",
            "native_actions": "trusted CDP input first (off = JS dispatch)",
            "repeat_mode": "count | repeating set (advance the entity set)",
            "extra_delay": "seconds before / between passes",
            "extract_items": "page data captured after replay for ctx_out "
                             "(page_text, page_html, title, url, "
                             "entity_exhausted)",
        },
        "modes": {
            "dom replay": "acts on real elements in the shared browser "
                          "(CDP) — the physical cursor is never hijacked",
            "repeating set": "marked element lists advance to the next "
                             "unhandled entity each pass",
        },
        "when_to_use": (
            "The step happens in the shared browser (DOM-driven).",
            "Prefer this over desktop sequences for any web action.",
        ),
        "when_to_ask": (
            "Always ask the user to RECORD the web step when none exists.",
        ),
        "example": "Web_sequence 'search bar.json' -> 'open profile.json'.",
    },
    "form_filler": {
        "label": "Form Filler",
        "purpose": (
            "Fills one page of a form with a small LLM: profiles the fields "
            "(fields_include/skip), answers each one, verifies and repairs; "
            "learned answers are reused so common fields stop re-generating."
        ),
        "ports_in": ("input", "ctx_in"),
        "ports_out": ("output", "ctx_out"),
        "fields": {
            "mode": "web or desktop form",
            "instruction": "what the filling should accomplish",
            "fields_include": "fields to fill (names/labels)",
            "fields_skip": "fields to leave alone",
            "engine": "which engine runs the filler",
            "model": "provider:model for the per-field answers",
            "verify": "verify each answer before submitting",
            "repair": "retry/repair failed fields",
        },
        "modes": {
            "web": "mode=web: fills the shared browser page",
            "desktop": "mode=desktop: fills the focused desktop window",
            "verify / repair": "verify each answer and retry failed "
                               "fields; learned answers are reused so "
                               "common fields stop re-generating",
        },
        "when_to_use": (
            "A page has many fields and the values come from context.",
            "Job-application / signup forms with learned answers.",
        ),
        "when_to_ask": (
            "Which values the user wants when context does not carry them.",
        ),
        "example": "Form_filler mode=web, fields_skip='cover letter'.",
    },
    "handle": {
        "label": "Handle",
        "purpose": (
            "Vision-grounded screen action: locates the goal object on a "
            "screenshot (LocateAnything-3B) and clicks or drags it — the "
            "fallback when a step is too unstable to record."
        ),
        "ports_in": ("input", "data"),
        "ports_out": ("output", "data"),
        "fields": {
            "prompt": "the on-screen goal (e.g. 'open the messages menu')",
            "description": "what this handle is for (agent routing)",
        },
        "modes": {
            "click / drag": "action_type: one grounded action per "
                            "activation (drag grounds the source, then the "
                            "target)",
            "web": "web_mode: grounds on the shared browser page instead "
                   "of the desktop screen",
            "agent-adaptive": "agent_adaptive: a connected Input (or the "
                              "user) supplies the goal text when the node "
                              "has none",
        },
        "when_to_use": (
            "A step cannot be recorded because the UI varies each run.",
            "Recovering from popups/dialogs that move around.",
        ),
        "when_to_ask": (
            "Whether the step is stable enough to record instead (prefer "
            "recordings; handle is the fallback).",
        ),
        "example": "Handle prompt='dismiss the cookie banner'.",
    },
    "context": {
        "label": "Context",
        "purpose": (
            "Persistent memory across turns: stores turn records (SQLite) so "
            "the next turn carries what happened (query/decision/tool/result)."
        ),
        "ports_in": ("ctx_in",),
        "ports_out": ("ctx_out",),
        "fields": {
            "label": "what this memory is about",
            "persistent": "keep records across runs",
            "clear_on_finish": "wipe the store when the chain completes",
            "max_history": "how many turn records to keep",
            "scope": "local (this chain only) or global (shared store)",
        },
        "modes": {
            "persistent": "keep turn records across runs (off = per-run)",
            "scope": "local = this chain only; global = shared store other "
                     "chains can read",
            "clear_on_finish": "wipe the store when the chain completes",
        },
        "when_to_use": (
            "A repeated agent flow needs to remember previous turns.",
            "Feed an LLM node's context port across iterations.",
        ),
        "when_to_ask": (
            "Whether memory should persist between runs.",
        ),
        "example": "Context -> ctx_in; LLM context port <- ctx_out.",
    },
    "mcp": {
        "label": "MCP",
        "purpose": (
            "External MCP client (declined for builder creation)."
        ),
        "ports_in": ("input",),
        "ports_out": ("output",),
        "fields": {"label": "what the MCP node is for"},
        "when_to_use": (
            "External tools served by an MCP server.",
        ),
        "when_to_ask": (
            "Ask the user to add and configure it manually.",
        ),
        "example": "(user added)",
    },
    "container": {
        "label": "Container",
        "purpose": (
            "Nested subgraph container (declined for builder creation)."
        ),
        "ports_in": ("input",),
        "ports_out": ("output",),
        "fields": {"label": "what the container groups"},
        "when_to_use": (
            "Grouping nodes into a reusable subgraph.",
        ),
        "when_to_ask": (
            "Ask the user to add it manually.",
        ),
        "example": "(user added)",
    },
}

# ---------------------------------------------------------------- sections

PORT_RULES: Tuple[str, ...] = (
    "Every node has an `input` port (multi-connection) and at least one "
    "output; data flows output -> input.",
    "Roles: sequences/web_sequences ACT, llm/code THINK, conditional and "
    "input(decision) CHECK, output SHOWS, context REMEMBERS.",
    "Conditional nodes expose `true` and `false` outputs; the unchosen branch "
    "is skipped.",
    "The LLM node also takes `prompt` (text REPLACES its prompt template) and "
    "`context` (history/data) inputs; its `context` output feeds Context nodes "
    "and other LLMs.",
    "Orchestrator mode adds `tools` (deterministic chains) and `brains` "
    "(sub-brains) inputs plus `trace` and `route` outputs.",
    "Context nodes connect via `ctx_in` -> `ctx_out` (NOT input/output).",
    "Form Filler and Handle expose a secondary `data`/`ctx_out` channel for "
    "passing values without triggering the action.",
    "A node with several inputs runs only after ALL upstream branches feeding "
    "it complete.",
    "Never wire to a port that does not exist; the builder reads the canvas "
    "and only wires real ports.",
)

SYSTEM_CHAIN: Tuple[str, ...] = (
    "Shape: input -> LLM node with orchestrator_mode=true -> output.",
    "The orchestrator's `tools` port takes DETERMINISTIC chains "
    "(classify_chain: no orchestrator inside) — scraping chains, sequences, "
    "form fillers.",
    "The `brains` port takes SUB-BRAINS: chains that themselves contain an "
    "orchestrator. Nested brains receive a task list; leaf workers a single "
    "scoped directive.",
    "A sub-brain is built as its own chain file and imported with a "
    "chain_import node (ask the user for a name; classify_chain decides the "
    "port).",
    "chain_import files that contain an orchestrator are skipped with a "
    "wire-it-to-brains warning when placed on `tools`.",
    "Set the orchestrator's `orch_goal` from the user's request; the loop is "
    "observe -> pick -> run -> verify per step.",
)

WHEN_TO_ASK: Tuple[str, ...] = (
    "No recording exists for a desktop/web step -> ask the user to record it "
    "(this is the primary teaching flow, not an error).",
    "A branch needs a criterion only the user knows.",
    "The goal does not name the node/tool that should be created.",
    "The import target chain name is ambiguous or missing -> ask.",
    "Destructive or irreversible actions (submit, send, delete) -> confirm "
    "before wiring them into a chain.",
    "Declined node types (mcp, container) -> ask the user to add manually.",
    "UNANSWERABLE otherwise: prefer asking once over guessing twice.",
)

PATTERNS: Dict[str, Tuple[str, ...]] = {
    "Linear": (
        "[Sequence 1] -> [Sequence 2] -> [Output]",
        "Step-by-step automations with no decisions.",
    ),
    "Branch": (
        "[Conditional] --true-->  [Happy path]",
        "             --false--> [Recovery / fallback]",
        "Use for decision points; always wire BOTH branches.",
    ),
    "Loop": (
        "[Conditional: while] --true--> [Work] --+",
        "        ^--------------------------------+",
        "        --false--> [Continue]",
        "Set max iterations; the false branch is the exit.",
    ),
    "Scraping chain (tools port)": (
        "[Input site] + [Input query] + [Input pages] (agent_modifiable)",
        "  -> [Web sequence: open search] -> [Code: parse results]",
        "  -> [Output variable_name='rows']",
        "Many agent_modifiable inputs: chat mode binds them per run "
        "(bind_input) instead of re-generating values.",
    ),
    "Brain and Hands (system chain)": (
        "[Input goal] -> [LLM orchestrator] -> [Output answer]",
        "                   tools  ^             ^ brains",
        "                          |             |",
        "[scrape chain] [form chain]      [login sub-brain]",
        "Nested brains get task lists; workers get one scoped directive.",
    ),
    "Memory loop": (
        "[Work] -> [Context] -> [LLM context port] -> ... -> [Context]",
        "Carries what happened across turns of an agent flow.",
    ),
}

PROVIDER_NOTE: Tuple[str, ...] = (
    "Every model-using field (llm.model, input.decision_model, "
    "conditional.llm_model, code.gen_model, form_filler.model, the builder "
    "chat model) accepts three forms:",
    "  - a plain Ollama tag (llama3.2:1b) or a GGUF name for the local "
    "engine;",
    "  - `provider:model` for a configured BYOK provider "
    "(openai:gpt-4o-mini, anthropic:claude-3-5-sonnet, ollama:llama3.2:1b, "
    "lm-studio:...);",
    "  - blank = fall back to the chain's LLM node, then the app default / "
    "the AI-Settings fallback routing.",
    "Configure keys and fetch model lists in AI Settings (gear) -> "
    "Providers.",
)


# ------------------------------------------------------------------ helpers

def purpose_line(node_type: str) -> str:
    """The one-line purpose for a node type ('' when unknown)."""
    entry = RUNBOOK.get(str(node_type or "").strip().lower())
    return str(entry.get("purpose") or "") if entry else ""


def criteria_for(node_type: str) -> str:
    """Purpose + first usage hint + mode names — the description Laya
    chooses over when it picks the node type for a tool."""
    entry = RUNBOOK.get(str(node_type or "").strip().lower())
    if not entry:
        return ""
    text = str(entry.get("purpose") or "")
    uses = entry.get("when_to_use") or ()
    if uses:
        text += " Use when: " + str(uses[0])
    modes = entry.get("modes") or {}
    if modes:
        text += " Modes: " + ", ".join(modes) + "."
    return text[:400]


def cheat_sheet() -> str:
    """Compact node catalogue + wiring rules for the builder LLM's prompts.

    The planning / reasoning model must know the real Arrow building
    blocks — node types, what each mode changes, and how ports connect —
    before it can write a buildable tool list.  Bounded so a small local
    model is not flooded.
    """
    lines = [
        "Arrow building blocks (name these when planning tools). Wiring: "
        "output -> input flows data; conditional routes true/false; text "
        "wired to an LLM's prompt port REPLACES its template; an "
        "orchestrator LLM takes deterministic chains on 'tools' and "
        "sub-brains on 'brains'; context nodes use ctx_in/ctx_out; prefer "
        "recorded sequences/web_sequences over handle, and code for exact "
        "transformations; output SHOWS the result."
    ]
    for node_type, entry in RUNBOOK.items():
        if node_type in DECLINED_TYPES:
            continue
        purpose = str(entry.get("purpose") or "").strip()
        line = "- %s: %s" % (node_type, purpose[:130])
        modes = entry.get("modes") or {}
        if modes:
            line += " [modes: %s]" % ", ".join(modes)
        lines.append(line)
    return "\n".join(lines)[:3000]


def model_using_fields() -> List[str]:
    """``"<node>.<field>"`` pairs that accept a provider:model choice."""
    pairs = [("llm", "model"), ("input", "decision_model"),
             ("conditional", "llm_model"), ("code", "gen_model"),
             ("form_filler", "model")]
    return ["%s.%s" % p for p in pairs]


# ----------------------------------------------------------------- markdown

_DOC_HEADER = """# Arrow Node Runbook

Complete reference for building Arrow chains: what every node does, how to
connect them, and when the builder chat must ask the user instead of guessing.

> Generated from `LoOper/player/builder/runbook.py` — edit the source, then
> run `python -m player.builder.runbook --write`.

## The template pipeline — never emit node JSON

Chains are strictly typed templates. The builder runs as a pipeline: the
builder LLM reasons about WHAT node is needed, FOR WHAT, and WHICH arguments
it carries (prose only); Laya selects the node type from this catalogue;
needle2 fills the selected template's arguments field by field; Python
creates the node, wires the ports and writes the chain file — the only
writer. This runbook therefore documents each node as intent (purpose,
ports, usage cases, when to ask), not as JSON anyone types.

## How the builder composes a system chain

Structure is never a model decision — the composer assembles the same shape
the hand-built system chains use:

1. Skeleton, always: `input -> orchestrator LLM -> output`, wired
deterministically (`input.output -> input`, `input.data -> prompt`,
`llm.output -> output`).
2. The reasoning LLM decomposes the goal into 3-6 TOOL lines (prose —
reusable capabilities the bot needs, not screen micro-steps).
3. Each tool is matched against the existing chains in the library with the
runtime direct-chain gate's measured rules: the chain's routing signal
(description + served examples; learned artifacts by examples only), the
goal-framed Laya read, and a floor + object-confirmation + margin gate.
A match becomes a `chain_import` wired to `tools` (or `brains` when the
chain file contains an orchestrator, via `classify_chain`).
A plan line that NAMES its own node type ("input (ask): …",
"llm (vanilla): …", "web_sequence (dom replay, repeating set): …") routes
itself: input/output/orchestrator lines ARE the skeleton (skipped, never
matched — matching them stole real tools' chains); llm lines are DESIGNED
LLM steps (authored prompt, chain file, wired import); code lines are REAL
code steps (the Python is authored ONCE, compile-validated, and the code
node runs it deterministically — a broken script falls back to an LLM
step); conditional lines become CHECK steps (input -> LLM conditional over
the line's criterion -> true/false outputs); an llm line with an
orchestrator modifier becomes a sub-brain. Designed steps never match the
library (matching these picked stale artifacts at the margins), and every
designed step runs on the LOCAL engine by default — the build-time
provider never leaks into the artifact. Only the remaining lines are
matched.
4. A tool with no matching chain is TAUGHT, cheapest first (one teach
round per unmatched tool — the plan is the budget):
- a WEB routine is DEMONSTRATED first: the configured provider drives the
shared browser itself (navigate / click / type / scroll / press) until the
routine works; every executed action is recorded with the recorder's own
event schema, frozen into `web_sequences/<tool>.json`, wrapped into its
own sub-chain file and wired to `tools` — no user involvement. An attempt
that gets stuck stops and reports what it tried.
- the teach card is the fallback (its body carries the failed attempt):
`web`/`desktop` = the user records it on a fresh node and it is WRAPPED
into its own sub-chain file (the recording file itself is never touched);
`llm` = the builder AUTHORS the instruction prompt and Python writes an
`input -> LLM step -> output` chain file (local engine + app GGUF by
default; the prompt is never empty — the tool line is the fallback);
`brain` = a nested system chain is written with the subtask as its
`orch_goal` and imported wired to `brains`.
Only when teaching is declined or unavailable is the tool reported
instead; it is never faked with an empty node.
5. Description + save close the pass; the result is a real
`collection="System"` chain, ready for agent mode. The user audits and
edits the topology in the graph; on-the-fly chain learning stays a
runtime agent-mode feature, not a build-time one.
"""


def _field_lines(fields: Dict[str, str]) -> List[str]:
    return ["- `%s` — %s" % (k, v) for k, v in fields.items()]


def render_markdown() -> str:
    """The full, human-readable runbook (mirrored to docs/NODE_RUNBOOK.md)."""
    out: List[str] = [_DOC_HEADER]

    out.append("## Node reference\n")
    for node_type, entry in RUNBOOK.items():
        out.append("### %s (`%s`)\n" % (entry.get("label", node_type), node_type))
        out.append("%s\n" % entry.get("purpose", ""))
        ports_in = list(entry.get("ports_in") or ())
        ports_out = list(entry.get("ports_out") or ())
        extra_in = list(entry.get("ports_in_orch_add") or ())
        extra_out = list(entry.get("ports_out_orch_add") or ())
        if extra_in:
            ports_in.append("+ orchestrator: " + ", ".join(extra_in))
        if extra_out:
            ports_out.append("+ orchestrator: " + ", ".join(extra_out))
        out.append("- **Inputs:** %s" % (", ".join("`%s`" % p for p in ports_in) or "(none)"))
        out.append("- **Outputs:** %s" % (", ".join("`%s`" % p for p in ports_out) or "(none)"))
        out.append("")
        out.append("**Fields:**")
        out.extend(_field_lines(entry.get("fields") or {}))
        out.append("")
        modes = entry.get("modes") or {}
        if modes:
            out.append("**Modes:**")
            out.extend(_field_lines(modes))
            out.append("")
        out.append("**Use it when:**")
        out.extend("- %s" % line for line in entry.get("when_to_use") or ())
        out.append("")
        out.append("**Ask the user when:**")
        out.extend("- %s" % line for line in entry.get("when_to_ask") or ())
        out.append("")
        out.append("**Example:** %s" % entry.get("example", ""))
        out.append("")

    out.append("## Connection rules\n")
    out.extend("- %s" % line for line in PORT_RULES)
    out.append("")

    out.append("## System chains (orchestrator mode)\n")
    out.extend("- %s" % line for line in SYSTEM_CHAIN)
    out.append("")

    out.append("## When to ask vs when not\n")
    out.extend("- %s" % line for line in WHEN_TO_ASK)
    out.append("")

    out.append("## Patterns\n")
    for name, lines in PATTERNS.items():
        out.append("### %s\n" % name)
        out.append("```\n%s\n```" % "\n".join(lines[: max(1, len(lines) - 1)]))
        out.append("")
        out.append(lines[-1])
        out.append("")

    out.append("## Models and providers\n")
    out.extend("- %s" % line for line in PROVIDER_NOTE)
    out.append("")
    out.append("Model-using fields: %s."
               % ", ".join("`%s`" % p for p in model_using_fields()))
    out.append("")
    return "\n".join(out)


def _doc_path() -> str:
    import os
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    return os.path.join(root, "docs", "NODE_RUNBOOK.md")


def main(argv: List[str]) -> int:
    import os
    if "--write" in argv:
        path = _doc_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(render_markdown())
        print("wrote %s" % path)
        return 0
    print(render_markdown())
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
