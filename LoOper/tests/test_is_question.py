"""A build request is never "just a question", however politely it is phrased.

"can you make me a to-do app?" used to be classified as a question: the agent
answered with prose and the loop ended with "Question answered - no code
requested", silently dropping the request.
"""

from player.code_agent_ops.parsing import _is_question

REQUESTS = [
    'can you make me a to-do app?',
    'Could you build a small app that watches a folder?',
    'i need a small app for my desk to track what i have to do today',
    'make me a thing that watches a folder and tells me when new files show up?',
    'please write a function that parses "1h 30m" into minutes',
    'can you fix the crash?',
    'why is result empty? can you fix it?',   # starts with why -> question
    'Write a PyQt5 app that shows a task list, importing parsers',
    'add a timer to each task',
    'show me a better layout?',
]

QUESTIONS = [
    'what does this code do?',
    'how does the parser work?',
    'why is result empty?',
    'does the timer work on windows?',
    'tell me what the output port contains',
    'explain the retry loop',
    'is there a way to run it faster',
    'which engine is used?',
]


def test_requests_are_not_questions():
    bad = [r for r in REQUESTS if _is_question(r)]
    assert not bad, 'misread as questions: {}'.format(bad)


def test_real_questions_stay_questions():
    bad = [q for q in QUESTIONS if not _is_question(q)]
    assert not bad, 'misread as requests: {}'.format(bad)


def test_empty_is_not_a_question():
    assert _is_question('') is False
    assert _is_question('   ') is False
