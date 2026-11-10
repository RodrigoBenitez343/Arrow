"""Shared setup for the engine benchmarks (run with the repo venv).

    .venv\\Scripts\\python.exe benchmarks\\bench_residency.py --fields 12

Every script here drives the REAL burst engines (Laya, the llama.cpp
embedding server, the llama.cpp generation server) and reads the numbers back
from ``AI/engine_metrics.py`` — the same recorder the app writes at runtime,
so a benchmark and a live run cannot disagree about what "ms" means.

The scripts are not tests: nothing asserts, everything prints.  Each one exits
with code 2 and an actionable message when a prerequisite (GGUF, binary,
venv) is missing, because a benchmark that silently measures nothing is worse
than no benchmark.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOOPER = ROOT / "LoOper"


def bootstrap() -> None:
    """Put ``LoOper/`` first on sys.path so ``AI.*`` imports resolve."""
    path = str(LOOPER)
    if path not in sys.path:
        sys.path.insert(0, path)


def require(condition, message: str) -> None:
    """Exit 2 with *message* when *condition* is false."""
    if condition:
        return
    print("\n[benchmark] cannot run: %s" % message, file=sys.stderr)
    print("[benchmark] run it with %s"
          % os.path.join(str(ROOT), ".venv", "Scripts", "python.exe"),
          file=sys.stderr)
    raise SystemExit(2)


def table(headers, rows) -> None:
    """Print a fixed-width table (numbers right-aligned)."""
    cells = [[str(c) for c in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in cells:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def line(row):
        parts = []
        for i, cell in enumerate(row):
            if i == 0:
                parts.append(cell.ljust(widths[i]))
            else:
                parts.append(cell.rjust(widths[i]))
        return "  ".join(parts)

    print(line(headers))
    print("  ".join("-" * w for w in widths))
    for row in cells:
        print(line(row))


def mean(values) -> float:
    values = [float(v) for v in (values or [])]
    return round(sum(values) / len(values), 1) if values else 0.0


def halves(values):
    """``(first_half_mean, second_half_mean)`` — the flat-cost evidence."""
    values = [float(v) for v in (values or [])]
    if len(values) < 2:
        return (round(mean(values), 1), round(mean(values), 1))
    mid = len(values) // 2
    return (mean(values[:mid]), mean(values[mid:]))


def jsonl_path() -> str:
    """Where ``AI/engine_metrics.py`` appends its samples."""
    from AI import engine_metrics
    return engine_metrics.jsonl_path()


def read_series(path: str = "", kind: str = "", field: str = "ms"):
    """One numeric column of a metrics JSONL, as a series (post-run analysis).

    ``engine_residency.jsonl`` does not exist in a shipped build; this reads
    whatever the recorder appended on THIS machine, so a benchmark can be
    compared with a live run field by field.
    """
    from AI import engine_metrics
    return engine_metrics.read_series(path or "", kind or "", field or "ms")
