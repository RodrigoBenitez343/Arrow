"""player.builder — chat-driven system chain builder.

The builder NEVER writes chain JSON itself and no model ever emits schema.
Three actors:

- the builder LLM chats in prose;
- Needle 2 constrains the prose to grammar-valid canvas tool calls
  (``player/builder/devtools.py``);
- Python executes those calls against the live canvas (the only JSON writer).

Laya (``player/builder/laya_grades.py``) grades the micro-decisions; every
grade is fail-open (None degrades to LLM judgment).
"""
