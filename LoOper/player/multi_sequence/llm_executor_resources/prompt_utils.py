from typing import Dict, Optional


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
    """Combine input source text and user prompt for model consumption."""
    input_source = (input_source or "none").upper()
    if include_raw and input_text and input_text.strip():
        return f"[Input Source: {input_source}]\n{input_text}\n\n[User Prompt:]\n{prompt}"
    else:
        return f"[Input Source: {input_source} - No content found]\n\n[User Prompt:]\n{prompt}"