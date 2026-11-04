"""runbook.py — the Arrow node runbook: one structured source of truth.

Every node type a chain can contain: what it is FOR, the ports it exposes
(collected from ``NGUI/nodes_resources/*.py`` — the source of truth), the
fields that describe it (aligned with ``node_templates.NODE_FIELDS``), and the
cases it is for.

Consumed by the analyst prompt (``capability_registry``), by the digest's node
vocabulary, and rendered to ``docs/NODE_RUNBOOK.md``:

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
        "ports_out_modes": {"decision_mode / route_on_answer": ("true", "false")},
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
        "ports_named": {
            "input": "execution trigger only",
            "prompt": "text REPLACES the prompt template (system_message is untouched)",
            "context": "history / pool material appended to the call",
            "tools": "deterministic chains (orchestrator mode)",
            "brains": "sub-brains (orchestrator mode)",
        },
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
        "ports_named": {
            "input": "execution trigger only — data arrives on the DECLARED input variables",
            "output": "execution continuation only — data leaves on the DECLARED output variables",
            "error": "taken when the script raises",
        },
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
        "ports_out_modes": {
            "file loop (loop_type + sequence_file)": ("output",),
        },
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
            "loop_type": "file-loop kind (while/until/...); with sequence_file "
                         "the node loops the file and exposes a single output",
            "loop_position": "where the loop sits relative to the file (pre/post)",
            "sequence_file": "the recording a FILE-LOOP conditional replays",
            "max_loops": "cap on loop passes for a file loop",
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
            "action_type": "click or drag (which grounded action the node "
                           "performs)",
            "goal_description": "the on-screen goal to locate (click target, "
                               "or drag source)",
            "target_description": "where a drag ends (drag only)",
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
            "documents": "JSON list of file paths (or literal notes) the pool "
                        "reads and serves as its Documents stream",
            "skills": "JSON list of skill files / free-text notes served as "
                      "its Skills stream",
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
    "action": {
        "label": "Action",
        "purpose": (
            "Legacy single-action node: one simple action with an execution "
            "output. Recordings belong to Sequence / Web Sequence and "
            "vision-grounded actions to Handle."
        ),
        "ports_in": ("input",),
        "ports_out": ("output",),
        "fields": {
            "label": "what the action does",
            "action_type": "the action kind the node performs",
            "description": "what this action does (agent routing context)",
        },
        "modes": {
            "single action": "performs its one action when execution reaches "
                             "it, then continues on output",
        },
        "when_to_use": (
            "A chain needs one simple action that has no recording.",
        ),
        "when_to_ask": (
            "Whether the step should be recorded instead (a Sequence replays "
            "exactly what the user showed).",
        ),
        "example": "(legacy) Action 'press Enter'.",
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


#: One wiring note per node — the "connect what to what" half of the
#: capability registry (the base rule output -> next input is implied).
WIRE_NOTES: Dict[str, str] = {
    "input": "output -> next input (exec); data -> an LLM's prompt port "
             "(REPLACES the template); true/false ports when routing on "
             "the answer",
    "output": "only sinks: the last step's output -> input",
    "llm": "output -> next input; context -> context node ctx_in; "
           "input/prompt ports take a wired input's text",
    "conditional": "true -> continue, false -> the other branch",
    "sequence": "output -> next input (replays its recorded actions)",
    "web_sequence": "output -> next input (replays its web recording)",
    "chain_import": "output -> next input; into an orchestrator's "
                    "'tools' (chains) or 'brains' (sub-brains)",
    "code": "NAMED IO ports only: previous -> first named input, first "
            "named output -> next",
    "context": "ctx_in <- llm context outputs; ctx_out -> the llm that "
               "summarizes them",
    "form_filler": "output -> next input (fills the form autonomously)",
    "handle": "output -> next input (vision-grounded click/drag)",
    "mcp": "output -> next input (external tool call)",
    "container": "wire through its input/output",
    "action": "output -> next input (performs its one action)",
}


def capability_registry() -> str:
    """What each node is FOR, its PORTS and how they WIRE.

    The single injection the analyst prompt carries: a node's purpose, its port
    names and the wiring rule — so a description names the real ports and the
    behaviour they create instead of guessing from the node's title.

    COMPACT on purpose.  The first version also carried "when to use" advice and
    every mode name (5.2k chars): that is authoring guidance, and it made the
    system prompt 14k characters — an 8k model then refused the whole turn.
    Reading a chain needs the purpose, the ports and the wire rule, nothing else.
    """
    lines = [
        "NODE TYPES (purpose, ports, how they wire — read behaviour from these):",
        "- base wiring: output -> next input moves EXECUTION; data ports move "
        "VALUES; an orchestrator LLM takes chains on 'tools', sub-brains on "
        "'brains'.",
    ]
    for node_type, entry in RUNBOOK.items():
        purpose = " ".join(str(entry.get("purpose") or "").split())[:110]
        ports_in = ", ".join(entry.get("ports_in") or ())
        ports_out = ", ".join(entry.get("ports_out") or ())
        wire = WIRE_NOTES.get(node_type, "output -> next input")
        lines.append("- %s: %s | in(%s) out(%s) | %s"
                     % (node_type, purpose, ports_in or "none",
                        ports_out or "none", wire))
    return "\n".join(lines)[:2500]


def model_using_fields() -> List[str]:
    """``"<node>.<field>"`` pairs that accept a provider:model choice."""
    pairs = [("llm", "model"), ("input", "decision_model"),
             ("conditional", "llm_model"), ("code", "gen_model"),
             ("form_filler", "model")]
    return ["%s.%s" % p for p in pairs]


# ----------------------------------------------------------------- markdown

_DOC_HEADER = """# Arrow Node Runbook

Reference for reading and authoring Arrow chains: what every node does, the
role of every port it exposes, and how the wiring composes behaviour.

> Generated from `LoOper/player/builder/runbook.py` — edit the source, then
> run `python -m player.builder.runbook --write`.

## Ports carry roles, not just names

An Arrow chain has two kinds of edge, and they are never interchangeable:

- **Execution edges** decide *when* a node runs: `output` (exec out) into
  `input` (exec in). A conditional routes on `true` / `false`; a FILE-LOOP
  conditional (`loop_type` + `sequence_file`) exposes a single `output`.
- **Data edges** decide *what* a node sees, and every destination port has
  its own meaning: `input.data -> llm.prompt` REPLACES the prompt template;
  `llm.context -> context.ctx_in` STORES that turn; `context.ctx_out ->
  llm.context` SERVES the pool; code nodes use their declared NAMED IO
  ports (their base `output` only carries execution, `error` is the failure
  branch); an orchestrator LLM takes chains on `tools` and sub-brains on
  `brains`. A context pool is PASSIVE: never executed, only written and
  consulted through its data ports.

## How chains are built

The builder chat is read-only: it explains a chain from its topology and
never creates, edits, wires or records anything. Chains are authored on the
canvas (nodes + their dialogs) and recorded with the recorders; the panel
can load documents into a selected node's own field — a context node's
Documents / Skills streams, an LLM's system message, a sequence's recording
path.

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
