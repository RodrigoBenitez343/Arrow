"""Rich rendering helpers shared by the agent overlay and web server.

Offline-safe, stdlib-only.  ``md_to_html`` converts a safe subset of
Markdown to HTML (raw HTML is escaped first — LLM-produced text is never
trusted); ``sanitize_html`` strips anything outside a small whitelist.
"""

import html
import re

_ALLOWED_TAGS = {
    "p", "b", "i", "u", "em", "strong", "h1", "h2", "h3", "h4",
    "ul", "ol", "li", "a", "img", "code", "pre", "table", "tr",
    "td", "th", "blockquote", "br", "hr",
}
_FORBIDDEN_TAGS = re.compile(
    r"<\s*(script|iframe|object|embed)\b[^>]*>.*?<\s*/\s*\1\s*>", re.I | re.S
)
_ON_ATTR = re.compile(r"\s+on\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)


def sanitize_html(raw):
    """Strip everything outside the whitelist from HTML.

    Drops ``script/iframe/object/embed`` blocks, ``on*`` attributes and
    any attribute on non-whitelisted tags.  ``img`` only keeps ``data:``
    or http(s) ``src``.  Not a hardened parser — a whitelist regex pass,
    adequate for LLM output rendered in Qt/JS clients.
    """
    if not raw:
        return ""
    text = _FORBIDDEN_TAGS.sub("", raw)
    # Remove on* attributes from every tag.
    text = _ON_ATTR.sub("", text)

    def _tag(m):
        full = m.group(0)
        if full.startswith("</"):
            name = full[2:-1].strip().lower()
            return f"</{name}>" if name in _ALLOWED_TAGS else ""
        name = re.match(r"<\s*([a-zA-Z0-9]+)", full)
        if not name:
            return ""
        name = name.group(1).lower()
        if name not in _ALLOWED_TAGS:
            return ""
        attrs = ""
        for am in re.finditer(r"([a-zA-Z0-9-]+)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", full):
            key, val = am.group(1).lower(), am.group(2)
            if key in ("onclick", "onload", "onerror", "onmouseover", "onfocus"):
                continue
            if key in ("href", "src"):
                v = val.strip("\"'").lower()
                if key == "src" and not (v.startswith("data:") or v.startswith("http")):
                    continue
                if key == "href" and not v.startswith(("http", "mailto:")):
                    continue
            attrs += f" {key}={val}"
        return f"<{name}{attrs}>"

    return re.sub(r"</?[a-zA-Z][^>]*>", _tag, text)


def md_to_html(text):
    """Convert a safe subset of Markdown to HTML.

    Supports: headings (h1–h4), bold/italic, inline + fenced code,
    unordered/ordered lists, links, blockquotes, hr and simple pipe
    tables.  Raw HTML in the input is escaped (safe default for
    LLM-produced text).  No nested lists, no image syntax.
    """
    if not text:
        return ""
    escaped = html.escape(text, quote=False)
    lines = escaped.splitlines()
    out = []
    in_fence = False
    fence_lang = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        # Fenced code blocks
        fm = re.match(r"^```\s*(\w*)\s*$", line)
        if fm:
            if in_fence:
                out.append("</code></pre>")
                in_fence = False
            else:
                fence_lang = fm.group(1)
                cls_attr = (' class="language-%s"' % fence_lang) if fence_lang else ""
                out.append(f"<pre><code{cls_attr}>")
                in_fence = True
            i += 1
            continue
        if in_fence:
            out.append(line)
            i += 1
            continue
        # Headings
        hm = re.match(r"^(#{1,4})\s+(.*)$", line)
        if hm:
            level = len(hm.group(1))
            out.append(f"<h{level}>{_inline(hm.group(2))}</h{level}>")
            i += 1
            continue
        # Horizontal rule
        if re.match(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$", line):
            out.append("<hr/>")
            i += 1
            continue
        # Blockquote (simple: one level, may span consecutive lines)
        if line.startswith("&gt; "):
            buf = []
            while i < len(lines) and lines[i].startswith("&gt; "):
                buf.append(lines[i][5:])
                i += 1
            out.append("<blockquote>" + "<br/>".join(_inline(b) for b in buf) + "</blockquote>")
            continue
        # Lists
        ul = re.match(r"^\s*[-*]\s+(.*)$", line)
        ol = re.match(r"^\s*\d+\.\s+(.*)$", line)
        if ul or ol:
            tag = "ul" if ul else "ol"
            out.append(f"<{tag}>")
            while i < len(lines):
                m = re.match(r"^\s*[-*]\s+(.*)$", lines[i]) if tag == "ul" else re.match(r"^\s*\d+\.\s+(.*)$", lines[i])
                if not m:
                    break
                out.append(f"<li>{_inline(m.group(1))}</li>")
                i += 1
            out.append(f"</{tag}>")
            continue
        # Simple pipe table: header row + separator row
        if "|" in line and i + 1 < len(lines) and re.match(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", lines[i + 1]):
            head = [c.strip() for c in line.strip().strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and "|" in lines[i]:
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            out.append("<table><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr></thead><tbody>")
            for row in rows:
                out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>")
            out.append("</tbody></table>")
            continue
        out.append(_inline(line))
        i += 1
    if in_fence:
        out.append("</code></pre>")
    return "\n".join(out)


def _inline(text):
    """Inline Markdown: bold, italic, inline code, links."""
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', text)
    return text
