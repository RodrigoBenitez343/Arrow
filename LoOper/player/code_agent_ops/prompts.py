"""Lean prompt texts for the Code Node Studio agent.

The agent works on ONE self-contained file — the code node's script.py.
No workspace browsing, no helper files, no other nodes.  When the coding
model needs to inspect the code or run it, it replies with a short plain
request and the panel routes it through the Needle 2 dev-tool engine
(search_code / read_code / run_node), which returns structured calls the
panel executes deterministically.

Little-coder pattern for small models, unchanged:
- the SYSTEM stays short and stable;
- guidance blocks are appended to the END of the per-turn message (protocol
  card on the first turn, corrective cards after a failure);
- every rule is also enforced mechanically by the panel (exact-match hunks,
  single-file scope, tool execution), never by hoping the model obeys.
"""
from player.code_agent_ops.constants import MAIN_FILE

__all__ = [
    'SYSTEM_AGENT',
    'protocol_card',
    'first_turn_text',
    'fix_error_text',
    'edit_retry_text',
    'empty_reply_text',
    'dup_reply_text',
    'truncated_text',
    'tool_results_text',
    'no_tool_text',
]

SYSTEM_AGENT = (
    'You are the coding agent of a Code node in a workflow automation tool.\n'
    'The node runs a SINGLE self-contained Python script named {main}; there '
    'are no other files. The message below contains the complete state.\n'
    'Rules:\n'
    '- Assign the node output to the variable "result".\n'
    '- Use only the declared input ports as variables - never invent upstream '
    'data.\n'
    '- After every code change the node runs automatically. Read the run '
    'output, fix errors, and stop once the code runs clean.\n'
    '- Code that opens a window or loops forever is NOT auto-run: keep the '
    'logic in plain functions and the GUI shell thin.\n'
    '- Keep the script self-contained: standard and pip libraries only, no '
    'helper files.\n'
    '- Reply concisely: no apologies, disclaimers, markdown tables or '
    'meta-commentary.\n'
).format(main=MAIN_FILE)


def protocol_card():
    """The editing + tool protocol - appended on turns where the agent may
    act."""
    return (
        'WORK PROTOCOL:\n'
        '1. New or small script: put the complete code in ONE ```python '
        'block. It becomes the whole {main} file.\n'
        '2. Editing the existing {main}: return ONLY SEARCH/REPLACE hunks '
        'inside that ```python block:\n'
        '<<<<<<< SEARCH\n'
        '<exact current lines - copy them from a tool result>\n'
        '=======\n'
        '<new lines>\n'
        '>>>>>>> REPLACE\n'
        'Every SEARCH side must match the file exactly once (whitespace '
        'included).\n'
        '3. Inspecting or running the code: reply with ONLY a short plain '
        'request describing what you need, for example:\n'
        '   - "search for \'result\' in the code"\n'
        '   - "search for \'def calculate\'"\n'
        '   - "show the whole code"\n'
        '   - "run the node"\n'
        'The panel picks the right tool, executes it and returns the results '
        'with line numbers. You may ask again as many times as needed - '
        'search first, then edit with hunks copied from real output. Never '
        'guess lines you have not seen.\n'
        '4. Done: when your last code change ran clean, stop.'
    ).format(main=MAIN_FILE)


def first_turn_text(goal):
    """The user request as the model sees it on the very first turn."""
    return ('### Request\n' + str(goal)).strip()


def fix_error_text(error_tail):
    """Sent after the auto-run of the agent's code failed."""
    return (
        '### Run failed - fix the code so it runs clean.\n'
        'If you need to see the real code first, reply with ONLY a plain '
        'request (e.g. "search for \'<name>\'", "show the whole code") and '
        'the results will come back with line numbers. Then return the fix '
        'as SEARCH/REPLACE hunks. Do not resend the same code unchanged.\n'
        '### Run output\n'
        + (str(error_tail or '').strip() or '(no output)')
    )


def edit_retry_text(err=''):
    """A SEARCH/REPLACE hunk did not apply - ask for a corrected edit."""
    note = ('\nDetail: ' + str(err)) if err else ''
    return (
        'Your SEARCH/REPLACE edit did not apply: every SEARCH side must match '
        'the current file exactly once (whitespace included). Search for the '
        'real lines first ("search for \'<text>\'"), copy them exactly into '
        'the SEARCH side, and reply with the corrected hunks only.' + note
    )


def empty_reply_text():
    return (
        'Your reply contained no code and no tool request. Either write the '
        'code the request asks for (full ```python block for a new or small '
        'script, SEARCH/REPLACE hunks to edit {main}), or reply with ONLY a '
        'plain request to inspect/run the code first.'.format(main=MAIN_FILE)
    )


def no_tool_text():
    return (
        'Your request did not map to any tool (search the code, show the '
        'whole code, run the node). Restate it as one of those, or produce '
        'the code change directly.'
    )


def dup_reply_text():
    return (
        'You sent the same code as your previous reply, which already ran and '
        'failed above - resending it cannot fix anything. Search the code for '
        'the failing part, find what is actually wrong, and produce a '
        'different edit.'
    )


def truncated_text():
    return (
        'Your reply was cut off mid-code (an unclosed code fence). Resend the '
        'complete code.'
    )


def tool_results_text():
    return (
        'The tool results are in the context above. Investigate as needed, '
        'then produce the code change (SEARCH/REPLACE hunks for edits).'
    )
