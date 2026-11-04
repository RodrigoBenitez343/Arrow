"""Modular project pipeline for small coding models.

The Code node is a PROJECT: ``script.py`` is the entry script, every other
file is a module.  Instead of asking one model call for the whole solution
(which small models answer with no implementation at all), the pipeline
runs a few SMALL, focused calls:

    plan        ->  MAIN: <entry purpose>   MODULE: <path> | <purpose>
    per module  ->  one fenced block = that file
    assemble    ->  script.py that imports the modules and sets ``result``

Each call sees only the request, the node's IO ports, the plan and the
signatures of the modules already written - never the whole codebase - so a
local/small model stays inside its context window and produces auditable
files one at a time.

Pure functions only (no Qt, no HTTP): prompts, plan parsing, code extraction
and syntax validation.  The QThread runner lives in ``project_runner``.
"""
import ast
import re

from NGUI.i18n import _
from player.code_agent_ops.constants import MAIN_FILE
from player.code_agent_ops.parsing import _ast_outline, _clip, _extract_fenced

__all__ = [
    'MAX_MODULES',
    'parse_plan',
    'extract_code',
    'validate_python',
    'project_outline',
    'plan_prompt',
    'module_prompt',
    'entry_prompt',
    'pipeline_summary',
]

MAX_MODULES = 5               # keep the pipeline bounded (and cheap)
MAX_MODULE_LINES = 300        # a module larger than this is asking for trouble


def validate_python(code):
    """'' when ``code`` parses, else a one-line syntax error."""
    try:
        ast.parse(str(code or ''))
        return ''
    except SyntaxError as e:
        return f'{e.msg} (line {e.lineno})'
    except Exception as e:
        return str(e)


def extract_code(text):
    """The first fenced block of a model reply, or '' when there is none."""
    return _extract_fenced(text)


def parse_plan(text):
    """Parse the planning reply into ``(main_desc, [(path, desc), ...])``.

    Tolerant on purpose (small models decorate lines): a ``MAIN:`` line and
    zero or more ``MODULE: <path> | <desc>`` lines, ``- `` bullets allowed,
    paths normalised to posix.  Duplicates and ``script.py`` are dropped.
    """
    main_desc = ''
    modules = []
    seen = set()
    in_fence = False
    for raw in str(text or '').splitlines():
        stripped = raw.strip()
        if stripped.startswith('```'):
            # Skip code bodies: a "# MODULE: x.py" comment inside code would
            # otherwise parse as a module.  A plan written BEFORE a code
            # block is still valid and used to be thrown away wholesale.
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        line = raw.strip().lstrip('-*# ').strip()
        m = re.match(r'(?i)^MAIN\s*:\s*(.+)$', line)
        if m and not main_desc:
            main_desc = m.group(1).strip()
            continue
        m = re.match(r'(?i)^MODULE\s*:\s*(.+)$', line)
        if not m:
            continue
        body = m.group(1).strip()
        path, _, desc = body.partition('|')
        path = path.strip().strip('`').replace('\\', '/').lstrip('./')
        desc = desc.strip()
        if not path or path == MAIN_FILE or path in seen:
            continue
        if not desc or desc.lower() in (path.lower(), path.lower()[:-3]):
            continue          # "MODULE: x.py | x.py" states no purpose
        if not re.match(r'^[A-Za-z0-9_\-./ ]+\.py$', path):
            continue          # python modules only; skip prose / data files
        seen.add(path)
        modules.append((path, desc))
        if len(modules) >= MAX_MODULES:
            break
    if not main_desc:
        return '', []
    return main_desc, modules


def project_outline(files):
    """Signature sheet of the project: one block per file (path + outline).

    The assembler and every module prompt get this instead of the sources,
    so a small model knows the public surface without the bodies."""
    parts = []
    for rel in sorted(files or {}):
        outline = '; '.join(_ast_outline(files[rel]))
        parts.append(f'### {rel}\n{outline or "(empty)"}')
    return '\n'.join(parts)


def _io_block(inputs, outputs):
    ins = ', '.join(str(v.get('name', '')) for v in (inputs or [])
                    if v.get('name')) or '(none)'
    outs = ', '.join(str(v.get('name', '')) for v in (outputs or [])
                     if v.get('name')) or '(none)'
    return f'Input variables: {ins}\nOutput variables: {outs}'


def plan_prompt(request, inputs, outputs, existing_files):
    """The one planning call: a feature list, not code."""
    existing = ('\n'.join('- ' + str(f) for f in existing_files[:40])
                or '(empty project)')
    return (
        '### Request\n' + str(request or '').strip() + '\n\n'
        '### Node ports\n' + _io_block(inputs, outputs) + '\n\n'
        '### Existing project files\n' + existing + '\n\n'
        '### Task\n'
        'Design a SMALL Python project that fulfils the request. '
        f'{MAIN_FILE} is the entry script that runs and assigns "result"; '
        'everything else is a module it imports.\n'
        'Reply with EXACTLY these lines (no code, no prose):\n'
        f'MAIN: <one line: what {MAIN_FILE} does>\n'
        'MODULE: <name.py> | <one line: purpose + public functions>\n'
        f'(use 0 to {MAX_MODULES} MODULE lines; keep each module focused)\n'
    )


def module_prompt(request, path, desc, plan, written_files, inputs, outputs,
                  existing_code=''):
    """One call = one file.  Small context by construction."""
    plan_lines = '\n'.join('- ' + p for p in (plan or [])) or '(none)'
    interfaces = project_outline(written_files) or '(no modules yet)'
    current = ''
    if existing_code:
        current = ('\n### Current content of this file (rewrite it whole)\n'
                   '```python\n' + _clip(existing_code, 3500) + '\n```\n')
    return (
        '### Request\n' + str(request or '').strip() + '\n\n'
        '### Node ports\n' + _io_block(inputs, outputs) + '\n\n'
        '### Project plan (for imports - do not edit other files)\n'
        + plan_lines + '\n\n'
        '### Modules already written (their public surface)\n'
        + interfaces + '\n\n'
        f'### Write ONLY this file: {path}\n{desc}\n'
        + current +
        '\nRules:\n'
        '- Plain Python 3, standard library or pip packages only.\n'
        '- Import sibling modules by their module name (e.g. "from parsers '
        'import parse"), never by path.\n'
        f'- Keep it under {MAX_MODULE_LINES} lines and focused on its purpose.\n'
        '- Reply with ONE ```python block containing the whole file. '
        'No prose before or after.\n'
    )


def entry_prompt(request, main_desc, files, inputs, outputs):
    """The assembly call: the entry script that ties the modules together."""
    interfaces = project_outline(files) or '(no modules)'
    outs = [str(v.get('name', '')) for v in (outputs or []) if v.get('name')]
    result_line = (f'Set "result" (and any declared output variable: '
                   f'{", ".join(outs)}).' if outs else 'Set "result".')
    return (
        '### Request\n' + str(request or '').strip() + '\n\n'
        '### Node ports\n' + _io_block(inputs, outputs) + '\n\n'
        '### Project modules (already written - import them, do not re-define '
        'their code)\n' + interfaces + '\n\n'
        f'### Task\nWrite {MAIN_FILE}, the entry script for this project.\n'
        + (f'Entry purpose: {main_desc}\n' if main_desc else '') +
        f'- Import the project modules listed above and orchestrate them.\n'
        '- Import ONLY those modules - a module that is not in that list does '
        'not exist, and importing it makes the whole app fail at start.\n'
        f'- Declared input variables are already in scope.\n'
        f'- {result_line}\n'
        '- A GUI window and its event loop ARE allowed in this file: the '
        'panel launches it separately and checks the window with a vision '
        'model, so the entry script may own the QApplication.\n'
        '- Reply with ONE ```python block containing the whole file. '
        'No prose.\n'
    )


def pipeline_summary(main_desc, written, failed):
    """Human-readable result of one pipeline run (chat bubble text)."""
    lines = []
    if main_desc:
        lines.append(main_desc)
    for rel in written:
        lines.append('  + ' + rel)
    for rel, err in failed:
        lines.append('  ! ' + rel + ': ' + _clip(err, 160))
    return '\n'.join(lines) or _('No implementation was produced.')
