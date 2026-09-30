from typing import Dict, Optional

# Sentinel marker used between the CURRENT INPUT section and the QUESTION section.
# The executor.py context-supplement code replaces this marker with the
# interaction-history block (if any), or removes it if no history is present.
_HISTORY_INSERT_MARKER = "\x00__HISTORY_INSERT_POINT__\x00"

# Guard: prevent variable values larger than this from being dumped into the
# prompt verbatim — small models (~4092 tokens) can't handle megabytes of raw
# text, and embedding large content hangs the tool-retrieval pipeline.
_MAX_VAR_LENGTH = 10_000


def substitute_variables(prompt: str, variables: Optional[Dict[str, object]]) -> str:
    """Replace {var} placeholders in a prompt with provided variable values.

    Values exceeding ``_MAX_VAR_LENGTH`` are truncated and annotated so the LLM
    still sees a representative prefix without context-window overflow.

    Internal variables (prefixed with ``_``) are excluded from substitution to
    prevent accidental injection of internal state into LLM prompts.
    """
    if not prompt:
        return ""
    if not variables:
        return prompt
    result = prompt
    for name, value in variables.items():
        # Skip internal variables (prefixed with _) to prevent accidental
        # template substitution into LLM prompts.
        if name.startswith('_'):
            continue
        try:
            text = str(value)
            if len(text) > _MAX_VAR_LENGTH:
                text = (
                    text[:_MAX_VAR_LENGTH]
                    + "\n\n[TRUNCATED: original value was "
                    + str(len(text))
                    + " chars; showing first "
                    + str(_MAX_VAR_LENGTH)
                    + "]"
                )
            result = result.replace(f"{{{name}}}", text)
        except Exception:
            # Best-effort substitution; continue on errors
            continue
    return result


def assemble_enhanced_prompt(prompt: str, input_source: str, input_text: str, include_raw: bool = True) -> str:
    """Combine input source text and user prompt for model consumption.
    
    SIMPLIFIED for small local models - minimal structure, just the facts.
    
    Returns a prompt structured as:
        [Current input text if any]
        [History marker - will be replaced with previous inputs]
        [User's question/prompt]
    """
    # For small models: keep it simple, just input + history + question
    if not include_raw or not input_text or not input_text.strip():
        # No input text, just the question with history marker
        return f"{_HISTORY_INSERT_MARKER}\n{prompt}"
    
    # Has input text - show it simply, then history, then question
    return f"{input_text}\n\n{_HISTORY_INSERT_MARKER}\n\n{prompt}"