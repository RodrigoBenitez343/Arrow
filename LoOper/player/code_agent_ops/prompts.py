"""Lean prompt texts for the Code Node Studio agent.

The agent works on a small PROJECT: the code node's workspace.  ``script.py``
({main}) is the entry script that gets executed; every other file (modules it
imports, data files) lives beside it and is written with a
``### FILE: <relative/path>`` section in the reply.  The panel executes the
top-level entry script and puts the project folder on ``sys.path``, so sibling
modules import normally.

When the coding model needs to inspect the code or run it, it replies with a
short plain request and the panel routes it through the Needle 2 dev-tool
engine (search_code / read_code / run_node), which returns structured calls
the panel executes deterministically.

Little-coder pattern for small models, unchanged:
- the SYSTEM stays short and stable;
- guidance blocks are appended to the END of the per-turn message (protocol
  card on the first turn, corrective cards after a failure);
- every rule is also enforced mechanically by the panel (exact-match hunks,
  safe path joins, tool execution), never by hoping the model obeys.
"""
from player.code_agent_ops.constants import MAIN_FILE

__all__ = [
    'SYSTEM_EDIT',
    'SYSTEM_ANSWER',
    'first_turn_text',
    'fix_error_text',
    'edit_retry_text',
    'empty_reply_text',
    'dup_reply_text',
    'truncated_text',
    'tool_results_text',
    'no_tool_text',
    'gui_report_text',
    'bad_imports_text',
]

# ONE template per call.  A small model cannot hold a long system prompt plus
# a long protocol card that repeat the same rules (and used to disagree:
# "single self-contained file" vs "extra project files").  The rules below are
# the whole instruction set for an EDIT turn; nothing else is appended.
SYSTEM_EDIT = (
    'You are the coding agent of a Code node: a small Python PROJECT. {main} '
    'is the entry script that runs; the other project files sit beside it and '
    'import normally. The message holds the complete current state.\n'
    'Reply with ONE of these, never a mix:\n'
    'A. New or small script: one ```python block with the whole {main}.\n'
    'B. Extra file: a line "### FILE: name.py" then one fenced block per '
    'file.\n'
    'C. Edit: SEARCH/REPLACE hunks inside one ```python block:\n'
    '   <<<<<<< SEARCH\n'
    '   <exact current lines, copied from a tool result>\n'
    '   =======\n'
    '   <new lines>\n'
    '   >>>>>>> REPLACE\n'
    'D. Look something up: ONE plain line, e.g. "search for \'result\'", '
    '"show the whole code", "run the node". The panel runs it and returns the '
    'output with line numbers.\n'
    'Rules:\n'
    '- Assign the node output to "result"; only declared input ports exist.\n'
    '- Imports: stdlib, pip packages, and files of THIS project. The host app '
    '(NGUI, player, AI, main_window, styles, chains, sequences, utils) is NOT '
    'importable - never import it, and import no module you have not '
    'written.\n'
    '- Every code change is run automatically: fix what the output says and '
    'stop once it runs clean.\n'
    '- Windowed code is launched and screenshotted for a vision model whose '
    'report you receive; never ask the user to run or describe it.\n'
    '- No apologies, no markdown tables, no meta-commentary.\n'
).format(main=MAIN_FILE)

# Question turns get their own template: asking for an answer is NOT asking
# for an edit, and mixing both in one prompt made the model rewrite the code.
SYSTEM_ANSWER = (
    'You answer a QUESTION about the code of this Code node project. The '
    'message holds the current state.\n'
    'Rules:\n'
    '- Answer in at most 8 short lines, from the code shown - never guess.\n'
    '- Write no code and no SEARCH/REPLACE hunks; no change was requested.\n'
    '- Need a part of the code that is not shown? Reply with one plain line: '
    '"search for \'<text>\'" or "show the whole code".\n'
    '- No apologies, no markdown tables.\n'
)


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
        'Check these small-model traps first:\n'
        '- ModuleNotFoundError for NGUI/player/AI/main_window/styles/chains/'
        'sequences/utils: the host app is NOT importable here - drop that '
        'import and use the stdlib, a pip library, or a file you wrote in '
        'this project.\n'
        '- NameError: a variable you never set, or an input port you did not '
        'declare. Only declared input ports arrive as variables.\n'
        '- The node output must be assigned to the variable "result".\n'
        '### Run output\n'
        + (str(error_tail or '').strip() or '(no output)')
    )


def bad_imports_text(names):
    """Sent instead of running: the code imports modules that exist nowhere
    (not stdlib, not a project file, not a pip package)."""
    listed = ', '.join(str(n) for n in (names or []))
    return (
        '### Pre-run check failed - these imports do not exist: ' + listed + '\n'
        'They are not Python stdlib, not files in this project, and not '
        'installed packages, so the run would die with ModuleNotFoundError '
        '(and pip cannot install them - they are not real).\n'
        'Fix it one of two ways:\n'
        '1. Write each one as its own section: "### FILE: ' +
        (str(names[0]) if names else 'name') + '.py" followed by a fenced '
        'block with the real code (declare everything the main script uses).\n'
        '2. Remove the import and do the work inline with the stdlib.\n'
        'Never import a module you have not written. Reply with the corrected '
        'main code (and the FILE sections you add).'
    )


def gui_report_text(report):
    """Sent after the app was launched and its window reviewed by the vision
    model (the agent cannot see the screen, only this report)."""
    return (
        '### GUI run - the vision model looked at your app window\n'
        'Your script was launched and its window screenshotted; this is what '
        'the vision model reported. Fix what it says is wrong - do not ask '
        'the user to run or describe the app.\n'
        'Typical causes: a widget or variable that does not exist, a layout '
        'that never gets shown, an exception swallowed in a callback, a '
        'missing import, or no main window shown at all.\n'
        '### Screenshot report\n'
        + (str(report or '').strip() or '(no report)')
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


def tool_results_text(tired=False):
    """After a dev-tool turn.  *tired* means the model already spent several
    turns inspecting: it must write code now, not search again."""
    text = ('The tool results are in the context above. Investigate as needed, '
            'then produce the code change (SEARCH/REPLACE hunks for edits).')
    if tired:
        text += (' A missing module can only be fixed by WRITING it - reply '
                 'with "### FILE: <name>.py" and its code, or with the fixed '
                 '```python block. Do not ask for another search.')
    return text
