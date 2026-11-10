"""Render the engine metrics log as one self-contained HTML page.

    .venv\\Scripts\\python.exe benchmarks\\report.py
    .venv\\Scripts\\python.exe benchmarks\\report.py --src "%LOCALAPPDATA%\\Arrow\\Arrow\\engine_metrics.jsonl" --open

Reads the JSONL that ``AI/engine_metrics.py`` appends at runtime (the same file
the app writes, so a live run and a benchmark land in the same page) and writes
``benchmarks/report.html``: latency curves, cold-load vs warm-use bars, the
context trim curve, the KV cache per start, and a table of recent samples.

No dependencies and no CDN: the charts are inline SVG built here, so the page
opens offline on an old machine and can be attached to an issue as-is.
"""

import argparse
import html
import json
import math
import os
import sys
import webbrowser
from collections import OrderedDict

import bench_common as bc

bc.bootstrap()

DEFAULT_OUT = bc.ROOT / "benchmarks" / "report.html"

#: Engine code -> label (see ``AI/engine_metrics.ENGINE_CODES``).
ENGINES = {1: "laya", 2: "embed", 3: "llama"}

#: Kinds whose ``ms`` field is a latency curve.  ``form_field`` and
#: ``context_trim`` are left out on purpose: each has its own section below and
#: a second copy of the same curve is noise.
TIMED = ("engine_load", "engine_start", "forward", "embed")


# ---------------------------------------------------------------- data in ---

def load_rows(path=""):
    """Every parseable row of the metrics JSONL, oldest first."""
    target = path or bc.jsonl_path()
    rows = []
    try:
        with open(target, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if isinstance(row, dict) and row.get("kind"):
                    rows.append(row)
    except OSError:
        return []
    rows.sort(key=lambda r: float(r.get("t") or 0.0))
    return rows


def by_kind(rows):
    """``{kind: [row, ...]}`` preserving kind order of first appearance."""
    out = OrderedDict()
    for row in rows:
        out.setdefault(str(row.get("kind")), []).append(row)
    return out


def numbers(kind_rows, field):
    return [float(r.get(field) or 0.0) for r in kind_rows
            if r.get(field) is not None]


def _mean(values):
    values = [float(v) for v in values or []]
    return round(sum(values) / len(values), 1) if values else 0.0


# ------------------------------------------------------------ svg drawing ---

def _nice_max(value):
    """Round *value* up to a readable axis top (1, 2, 2.5, 5 x 10^n)."""
    if value <= 0:
        return 1.0
    exp = math.floor(math.log10(value))
    base = 10.0 ** exp
    for mult in (1.0, 2.0, 2.5, 5.0, 10.0):
        if value <= base * mult:
            return base * mult
    return base * 10.0


def _fmt(value):
    value = float(value)
    if abs(value) >= 10000:
        return "%.0f" % value
    if abs(value) >= 100:
        return "%.0f" % value
    if abs(value) >= 10:
        return "%.1f" % value
    return "%.2f" % value if value else "0"


def line_chart(series, width=680, height=170, unit="", names=None,
               legend=("series", "second")):
    """One polyline over sample index; *names* draws a second series.

    Both series must share a unit: two curves on one axis are a comparison,
    not two measurements stacked for decoration.
    """
    sets = [s for s in ([series] if names is None else [series, names])
            if s]
    if not sets:
        return '<p class="empty">no samples</p>'
    top = _nice_max(max(max(s) for s in sets) or 1.0)
    pad_l, pad_r, pad_t, pad_b = 62, 12, 12, 22
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    def xy(index, value, count):
        x = pad_l + (plot_w * index / max(1, count - 1)) if count > 1 else pad_l
        y = pad_t + plot_h - plot_h * (float(value) / top)
        return round(x, 1), round(y, 1)

    parts = ['<svg viewBox="0 0 %d %d" width="%d" height="%d" role="img">'
             % (width, height, width, height)]
    for frac in (0.0, 0.5, 1.0):
        y = round(pad_t + plot_h - plot_h * frac, 1)
        parts.append('<line x1="%d" y1="%s" x2="%d" y2="%s" class="grid"/>'
                     % (pad_l, y, width - pad_r, y))
        parts.append('<text x="%d" y="%s" class="axis" text-anchor="end">%s</text>'
                     % (pad_l - 6, y + 3.5, _fmt(top * frac)))
    colors = ("#2563eb", "#c2410c")
    labels = legend
    for index, values in enumerate(sets):
        count = len(values)
        points = [xy(i, v, count) for i, v in enumerate(values)]
        if count == 1:
            x, y = points[0]
            parts.append('<circle cx="%s" cy="%s" r="3" fill="%s"/>'
                         % (x, y, colors[index]))
        else:
            path = " ".join("%s,%s" % p for p in points)
            parts.append('<polyline fill="none" stroke="%s" stroke-width="1.6" '
                         'points="%s"/>' % (colors[index], path))
    parts.append('<text x="%d" y="%d" class="axis">0</text>'
                 % (pad_l, height - 6))
    count = max(len(s) for s in sets)
    parts.append('<text x="%d" y="%d" class="axis" text-anchor="end">%d %s</text>'
                 % (width - pad_r, height - 6, count,
                    "sample" if count == 1 else "samples"))
    if unit:
        parts.append('<text x="%d" y="%d" class="axis" text-anchor="end">'
                     '%s</text>' % (width - pad_r, pad_t + 8,
                                    html.escape(unit)))
    if names is not None:
        parts.append('<g class="legend">')
        for index, label in enumerate(labels):
            x = width - pad_r - 150 + index * 78
            parts.append('<rect x="%d" y="%d" width="10" height="3" fill="%s"/>'
                         % (x, 8, colors[index]))
            parts.append('<text x="%d" y="%d" class="axis">%s</text>'
                         % (x + 14, 12, label))
        parts.append('</g>')
    parts.append('</svg>')
    return "".join(parts)


def h_bars(items, width=680, bar=22, unit=""):
    """Horizontal bars: ``[(label, value), ...]`` with the value on the right."""
    items = [(str(l), float(v or 0.0)) for l, v in items]
    if not items:
        return '<p class="empty">no samples</p>'
    top = _nice_max(max(v for _, v in items) or 1.0)
    label_w = 190
    plot_w = width - label_w - 80
    height = 8 + len(items) * (bar + 8)
    parts = ['<svg viewBox="0 0 %d %d" width="%d" height="%d" role="img">'
             % (width, height, width, height)]
    for index, (label, value) in enumerate(items):
        y = 6 + index * (bar + 8)
        w = max(0.0, plot_w * (value / top))
        parts.append('<text x="0" y="%d" class="bar-label">%s</text>'
                     % (y + bar - 6, html.escape(label[:34])))
        parts.append('<rect x="%d" y="%d" width="%s" height="%d" rx="2" '
                     'fill="#2563eb"/>' % (label_w, y, round(w, 1), bar))
        parts.append('<text x="%s" y="%d" class="bar-value">%s%s</text>'
                     % (round(label_w + w + 6, 1), y + bar - 6,
                        _fmt(value), html.escape(unit)))
    parts.append('</svg>')
    return "".join(parts)


# ------------------------------------------------------------- sections ----

def section_summary(rows, kinds):
    parts = ['<h2>Summary</h2>']
    if not rows:
        parts.append('<p class="empty">No samples in the metrics log yet. '
                     'Run a benchmark or use the app, then rebuild.</p>')
        return "".join(parts)
    span = float(rows[-1].get("t") or 0.0) - float(rows[0].get("t") or 0.0)
    table_rows = []
    for kind, kind_rows in kinds.items():
        times = numbers(kind_rows, "ms")
        table_rows.append("<tr><td>%s</td><td>%d</td><td>%s</td><td>%s</td>"
                          "<td>%s</td></tr>"
                          % (html.escape(kind), len(kind_rows),
                             _mean(times) if times else "-",
                             _fmt(max(times)) if times else "-",
                             _fmt(sum(times)) if times else "-"))
    parts.append('<p class="meta">%d samples over %.1f s | %d kinds</p>'
                 % (len(rows), span, len(kinds)))
    parts.append("<table><thead><tr><th>kind</th><th>samples</th>"
                 "<th>mean ms</th><th>max ms</th><th>total ms</th></tr>"
                 "</thead><tbody>%s</tbody></table>" % "".join(table_rows))
    return "".join(parts)


def section_latency(kinds):
    blocks = []
    for kind in TIMED:
        kind_rows = kinds.get(kind) or []
        times = numbers(kind_rows, "ms")
        if not times:
            continue
        blocks.append('<div class="card"><h3>%s <span class="meta">%d '
                      'samples | mean %s ms | max %s ms</span></h3>%s</div>'
                      % (html.escape(kind), len(times), _mean(times),
                         _fmt(max(times)), line_chart(times, unit="ms")))
    if not blocks:
        return ""
    return ('<h2>Latency over sample</h2><p class="meta">One point per recorded '
            'sample, oldest left. A flat line is a warm engine; a step is a cold '
            'load.</p>%s' % "".join(blocks))


def section_load_vs_use(kinds):
    """Cold load ms vs warm use ms, per engine code."""
    loads = {}
    for row in kinds.get("engine_load") or []:
        code = int(row.get("engine_code") or 0)
        loads.setdefault(code, []).append(float(row.get("ms") or 0.0))
    starts = {}
    for row in kinds.get("engine_start") or []:
        code = int(row.get("engine_code") or 0)
        starts.setdefault(code, []).append(float(row.get("ms") or 0.0))
    uses = {}
    for kind in ("forward", "embed"):
        for row in kinds.get(kind) or []:
            code = int(row.get("engine_code") or 0)
            uses.setdefault(code, []).append(float(row.get("ms") or 0.0))

    if not loads and not starts:
        return ""
    # code 0 = samples recorded before the engine code existed: no engine to
    # compare, so they would only add noise to a per-engine table.
    codes = sorted(c for c in set(loads) | set(starts) | set(uses) if c)
    if not codes:
        return ""
    items = []
    for code in codes:
        name = ENGINES.get(code, "engine %d" % code)
        cold = _mean((loads.get(code) or []) + (starts.get(code) or []))
        warm = _mean(uses.get(code) or [])
        if cold:
            items.append(("%s: cold load" % name, cold))
        if warm:
            items.append(("%s: warm use" % name, warm))
    if not items:
        return ""
    detail = []
    for code in codes:
        cold = _mean((loads.get(code) or []) + (starts.get(code) or []))
        warm = _mean(uses.get(code) or [])
        ratio = ("%.1fx" % (cold / warm)) if cold and warm else "-"
        detail.append("<tr><td>%s</td><td>%d</td><td>%s</td><td>%d</td>"
                      "<td>%s</td><td>%s</td></tr>"
                      % (html.escape(ENGINES.get(code, str(code))),
                         len(loads.get(code) or []) + len(starts.get(code) or []),
                         cold or "-", len(uses.get(code) or []),
                         warm or "-", ratio))
    return ('<h2>Cold load vs warm use</h2><p class="meta">What one residency '
            'hold is worth: the load paid once against the per-call use.</p>%s'
            '<table><thead><tr><th>engine</th><th>loads</th><th>mean load ms</th>'
            '<th>uses</th><th>mean use ms</th><th>load/use</th></tr></thead>'
            '<tbody>%s</tbody></table>'
            % (h_bars(items, unit=" ms"), "".join(detail)))


def section_context(kinds):
    rows = kinds.get("context_trim") or []
    if not rows:
        return ""
    before = numbers(rows, "chars_before")
    after = numbers(rows, "chars_after")
    saved = int(sum(before) - sum(after))
    dropped = int(sum(numbers(rows, "turns_dropped")))
    return ('<h2>Context budget</h2><p class="meta">Every trim: the prompt size '
            'before (blue) and after (orange). The gap is the bytes the budget '
            'removed.</p><div class="card">%s</div>'
            '<p class="meta">%d trims | %d turns dropped | %s chars removed '
            '| mean %.0f -> %.0f</p>'
            % (line_chart(before, unit="chars", names=after,
                          legend=("before", "after")), len(rows), dropped,
               "{:,}".format(saved), _mean(before), _mean(after)))


def section_kv(kinds):
    rows = kinds.get("engine_start") or []
    kv_rows = [r for r in rows if r.get("kv_bytes") and r.get("engine_code")]
    if not kv_rows:
        return ""
    seen = OrderedDict()
    for row in kv_rows:
        ctx = int(row.get("ctx") or 0)
        mb = round(float(row.get("kv_bytes") or 0.0) / (1024 * 1024), 1)
        name = ENGINES.get(int(row.get("engine_code") or 0), "engine")
        key = (name, ctx)
        seen[key] = max(seen.get(key, 0.0), mb)
    items = [("%s ctx %d" % (name, ctx), mb) for (name, ctx), mb in seen.items()]
    table = "".join("<tr><td>%s</td><td>%d</td><td>%s</td></tr>"
                    % (html.escape(name), ctx, _fmt(mb))
                    for (name, ctx), mb in seen.items())
    return ('<h2>KV cache at engine start</h2><p class="meta">Resident KV bytes '
            'reserved for the configured window, per start sample.</p>%s'
            '<table><thead><tr><th>engine</th><th>ctx</th><th>KV MB</th>'
            '</tr></thead><tbody>%s</tbody></table>'
            % (h_bars(items, unit=" MB"), table))


def section_form(kinds):
    rows = kinds.get("form_field") or []
    if not rows:
        return ""
    times = numbers(rows, "ms")
    forwards = numbers(rows, "forwards")
    kept = int(sum(numbers(rows, "kept")))
    verdict = {}
    for row in rows:
        code = int(row.get("action") if row.get("action") is not None else -1)
        verdict[code] = verdict.get(code, 0) + 1
    names = {-1: "none", 0: "incorrect", 1: "ambiguous", 2: "correct"}
    verdict_line = " | ".join("%s %d" % (names.get(k, k), v)
                              for k, v in sorted(verdict.items()))
    return ('<h2>Form field cost</h2><p class="meta">Wall ms per field. Flat '
            'ms with flat forwards per field means the cost is per field, not '
            'per document.</p><div class="card">%s</div>'
            '<p class="meta">%d fields | %d blocks kept | %s | mean %s ms | '
            'forwards/field mean %s, max %s</p>'
            % (line_chart(times, unit="ms"), len(rows), kept,
               html.escape(verdict_line), _mean(times), _mean(forwards),
               _fmt(max(forwards)) if forwards else "-"))


def section_recent(rows, limit=40):
    if not rows:
        return ""
    base = float(rows[0].get("t") or 0.0)
    # Only fields that carry a value often enough to read a column of them;
    # the union of every field in the log is mostly dashes.
    fields = []
    for row in rows:
        for key, value in row.items():
            if key in ("kind", "t") or value is None or key in fields:
                continue
            filled = sum(1 for r in rows if r.get(key) is not None)
            if filled * 4 >= len(rows):
                fields.append(key)
    fields = fields[:10]
    head = "".join("<th>%s</th>" % html.escape(f) for f in fields)
    body = []
    for row in rows[-limit:]:
        cells = "".join("<td>%s</td>" % ("-" if row.get(f) is None
                                         else _fmt(row.get(f))) for f in fields)
        body.append("<tr><td>%s</td><td>%s</td>%s</tr>"
                    % (html.escape(str(row.get("kind"))),
                       _rel(row, base), cells))
    return ('<h2>Recent samples</h2><p class="meta">Last %d of %d, columns '
            'kept where a value is present in at least a quarter of the '
            'samples.</p><div class="card"><table>'
            '<thead><tr><th>kind</th><th>t+ s</th>%s</tr></thead><tbody>%s'
            '</tbody></table></div>' % (min(limit, len(rows)), len(rows), head,
                                        "".join(body)))


def _rel(row, base=0.0):
    """Seconds since the first sample (a raw epoch is unreadable)."""
    return "%.1f" % (float(row.get("t") or 0.0) - float(base or 0.0))


# --------------------------------------------------------------- page ----

CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; padding: 32px 28px 64px; background: #fbfbfa; color: #17181a;
       font: 14px/1.5 "Segoe UI", system-ui, sans-serif; }
h1 { font-size: 22px; margin: 0 0 4px; font-weight: 600; }
h2 { font-size: 15px; margin: 34px 0 8px; font-weight: 600;
     border-bottom: 1px solid #e3e3e0; padding-bottom: 6px; }
h3 { font-size: 13px; margin: 0 0 6px; font-weight: 600; }
p.meta { color: #62656b; font-size: 12px; margin: 0 0 10px; }
p.empty { color: #62656b; font-style: italic; margin: 6px 0; }
.card { border: 1px solid #e3e3e0; border-radius: 6px; padding: 12px;
        background: #fff; margin-bottom: 12px; overflow-x: auto; }
table { border-collapse: collapse; font-size: 12.5px; }
th, td { text-align: left; padding: 4px 14px 4px 0; border-bottom: 1px solid
         #efefec; white-space: nowrap; }
th { color: #62656b; font-weight: 600; }
svg { display: block; max-width: 100%; height: auto; }
.grid { stroke: #e9e9e5; stroke-width: 1; }
.axis { fill: #8b8f96; font-size: 10px; }
.bar-label { fill: #17181a; font-size: 11.5px; }
.bar-value { fill: #62656b; font-size: 11px; }
.legend text { fill: #62656b; font-size: 10px; }
footer { margin-top: 40px; color: #8b8f96; font-size: 11.5px; }
code { background: #f1f1ee; padding: 1px 5px; border-radius: 3px; font-size:
       11.5px; }
"""


def build(rows, src=""):
    """The whole page as a string (no rows -> an explanatory page)."""
    kinds = by_kind(rows)
    sections = [
        section_summary(rows, kinds),
        section_load_vs_use(kinds),
        section_latency(kinds),
        section_form(kinds),
        section_context(kinds),
        section_kv(kinds),
        section_recent(rows),
    ]
    span = ("%.1f s" % (float(rows[-1].get("t") or 0.0)
                        - float(rows[0].get("t") or 0.0))) if rows else "0 s"
    return """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Arrow engine metrics</title>
<style>%s</style></head><body>
<h1>Arrow engine metrics</h1>
<p class="meta">Source <code>%s</code> | %d samples | %s</p>
%s
<footer>Rebuild with <code>.venv\\Scripts\\python.exe benchmarks\\report.py</code>.
Charts are inline SVG: no network, no scripts.</footer>
</body></html>
""" % (CSS, html.escape(src or bc.jsonl_path()), len(rows), span,
       "".join(s for s in sections if s))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", default="",
                        help="metrics JSONL (default: the app's runtime log)")
    parser.add_argument("--out", default=str(DEFAULT_OUT),
                        help="output HTML (default: benchmarks/report.html)")
    parser.add_argument("--open", action="store_true",
                        help="open the page in the default browser")
    args = parser.parse_args()

    src = args.src or bc.jsonl_path()
    rows = load_rows(src)
    page = build(rows, src)
    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(page)
    print("wrote %s (%d samples, %.1f KB)"
          % (out, len(rows), len(page) / 1024.0))
    if not rows:
        print("the log is empty: run a benchmark first, e.g. "
              "benchmarks\\bench_residency.py --fields 6", file=sys.stderr)
    if args.open:
        webbrowser.open("file:///" + out.replace("\\", "/"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
