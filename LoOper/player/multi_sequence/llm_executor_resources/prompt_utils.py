from typing import Dict, Optional

# Sentinel marker used between the CURRENT INPUT section and the QUESTION section.
# The executor.py context-supplement code replaces this marker with the
# interaction-history block (if any), or removes it if no history is present.
_HISTORY_INSERT_MARKER = "\x00__HISTORY_INSERT_POINT__\x00"


def substitute_variables(prompt: str, variables: Optional[Dict[str, object]]) -> str:
    """Replace {var} placeholders in a prompt with provided variable values."""
    if not prompt:
        return ""
    if not variables:
        return prompt
    result = prompt
    for name, value in variables.items():
        try:
            result = result.replace(f"{{{name}}}", str(value))
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