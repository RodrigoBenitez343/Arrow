"""End-to-end CRAG form fill: the AI api headless, the player's real node.

Everything the live LinkedIn run needed but could not give - a form whose every
field has ONE right answer, a source document that actually holds it, and a
reproducible page - is here instead:

    fixtures/form_filler_probe_form.html   served from a local http.server on a
                                           random port, opened in a HEADLESS
                                           Chrome of its own profile (never the
                                           user's browser, never a second window
                                           on the shared profile)

    Rodrigo_Benitez_Resume.md              the ONLY knowledge pool, exactly the
                                           one the user wired to the Context node

    AI/api.py                              booted headless (or reused if it is
                                           already up) for /llamacpp/generate and
                                           /embeddings; Laya's daemon is spawned
                                           by AI/laya_client on the first score

The fill itself is NOT a re-implementation: it runs the node's real entry
``_execute_form_filling_node`` - enumeration, the CRAG pass per field
(``AI/crag`` through ``form_filler_modules/crag.py``), extraction, the write,
the verify and the repair - and then reads the PAGE back and grades every field.

Run it:
    python LoOper/tests/test_form_filler_crag_e2e.py            # whole form
    python LoOper/tests/test_form_filler_crag_e2e.py --max-fields 6
    python LoOper/tests/test_form_filler_crag_e2e.py --headed --keep-open
    $env:LOOPER_E2E=1; python -m pytest LoOper/tests/test_form_filler_crag_e2e.py

pytest SKIPS it unless ``LOOPER_E2E=1``: it boots three engines and takes
minutes, which does not belong in the unit suite.
"""

import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent                  # LoOper/tests
_LOOPER = HERE.parent                                   # LoOper
FIXTURE = HERE / "fixtures" / "form_filler_probe_form.html"

# The same single source the user wired to the Context node of repairtest.json.
RESUME = os.environ.get("LOOPER_E2E_RESUME") or os.path.join(
    "C:", os.sep + "Users", "LoOper", "Documents", "resumes",
    "Rodrigo_Benitez_Resume.md")

# The chain's own extractor - the 3B model the live run used, so a verdict here
# is a verdict about THAT model's reading of the pack, not about a bigger one.
MODEL = os.environ.get("LOOPER_E2E_MODEL",
                        "llama-3.2-3b-instruct-q8_0.gguf")

_SKIP = os.environ.get("LOOPER_E2E") != "1"

# Every line lands twice: on the console - which the app's own boxy log
# formatter wraps - and in a plain file, so the report stays readable.
LOG_PATH = os.environ.get("LOOPER_E2E_LOG") or os.path.join(
    str(_LOOPER.parent), ".qoder", "tmp", "e2e_run_report.txt")
_REAL_PRINT = print
_LOG = None


def say(*a, **_kw):
    """The module's ONE output path: console + the plain-text report.

    Swallows print's keyword arguments (``flush=``) so shadowing ``print``
    cannot break a line that still spells it the built-in way.
    """
    line = " ".join(str(x) for x in a)
    _REAL_PRINT(line, flush=True)
    global _LOG
    try:
        if _LOG is None:
            os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
            _LOG = open(LOG_PATH, "w", encoding="utf-8")
        _LOG.write(line + "\n")
        _LOG.flush()
    except Exception:
        pass


print = say  # noqa: A001  (every report line below goes through say())


# ── the environment: API, page, browser ────────────────────────────────────
def _api_url():
    """The app's AI gateway, started headless when nothing answers on it."""
    import requests

    port = int(os.environ.get("API_PORT") or 8000)
    url = "http://127.0.0.1:%d" % port
    try:
        if requests.get(url + "/health", timeout=3).status_code < 500:
            print("api: already up at %s" % url, flush=True)
            return url
    except Exception:
        pass
    print("api: not up - starting AI/api.py headless", flush=True)
    import subprocess
    popen_kwargs = {
        "cwd": str(_LOOPER),
        "stdout": open(_LOOPER / ".." / ".qoder" / "tmp" / "e2e_api.log",
                       "ab"),
        "stderr": subprocess.STDOUT,
        "env": dict(os.environ, API_PORT=str(port)),
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = 0x08000000  # DETACHED_PROCESS
    subprocess.Popen([sys.executable, str(_LOOPER / "AI" / "api.py")],
                     **popen_kwargs)
    deadline = time.time() + 90
    while time.time() < deadline:
        try:
            if requests.get(url + "/health", timeout=3).status_code < 500:
                print("api: ready in %.1fs" % (90 - (deadline - time.time())),
                      flush=True)
                return url
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("AI api did not become ready on %s" % url)


def _serve_fixture():
    """Serve the fixture page from a throwaway port; returns its URL."""
    import functools
    import http.server
    import socketserver
    import threading

    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler,
        directory=str(FIXTURE.parent))
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    print("page: http://127.0.0.1:%d/%s" % (port, FIXTURE.name), flush=True)
    return "http://127.0.0.1:%d/%s" % (port, FIXTURE.name), httpd


def _open_page(url, headless=True):
    """A browser of OUR OWN: fresh disposable profile, never the shared one."""
    from player.web.session import create_session

    driver = create_session(headless=headless)
    driver.get(url)
    driver.execute_script("return document.readyState")
    print("browser: opened %s (title %r)" % (url, driver.title), flush=True)
    return driver


# ── reading the page back ──────────────────────────────────────────────────
JS_READ_BACK = r"""
return (function () {
  const form = document.getElementById('apply-form');
  const rows = [];
  const clean = (x) => String(x == null ? '' : x).replace(/\s+/g, ' ').trim();
  // The label a HUMAN reads for a control: its own <label for>, else the text
  // wrapped around it, else its fieldset's <legend>.
  const labelFor = (el) => {
    if (el.id) {
      const lb = form.querySelector('label[for="' + el.id + '"]');
      if (lb) return clean(lb.innerText);
    }
    const own = el.closest('label');
    if (own) return clean(own.innerText);
    const fs = el.closest('fieldset');
    if (fs) {
      const lg = fs.querySelector('legend');
      if (lg) return clean(lg.innerText);
    }
    const holder = el.closest('.switch');
    if (holder) return clean(holder.innerText);
    return clean(el.getAttribute('aria-label'));
  };
  form.querySelectorAll('input, select, textarea').forEach(function (el) {
    const type = (el.type || '').toLowerCase();
    if (type === 'radio') return;                    // graded as one group
    if (type === 'checkbox' && el.name === 'areas') return;
    if (el.tagName === 'SELECT') {
      rows.push({label: labelFor(el), kind: 'select',
                 value: clean(el.options[el.selectedIndex] &&
                              el.options[el.selectedIndex].text)});
      return;
    }
    rows.push({label: labelFor(el),
               kind: el.getAttribute('role') || type || 'text',
               value: el.value || ''});
  });
  // One row per radio GROUP: the question is the fieldset's <legend> (the
  // option's own <label> would name the ANSWER, not the field).
  const groups = {};
  form.querySelectorAll('input[type=radio]').forEach(function (el) {
    (groups[el.name] = groups[el.name] || []).push(el);
  });
  Object.keys(groups).forEach(function (name) {
    const first = groups[name][0];
    const fs = first.closest('fieldset');
    const question = fs && fs.querySelector('legend')
        ? clean(fs.querySelector('legend').innerText) : name;
    const on = groups[name].filter(function (el) { return el.checked; })[0];
    rows.push({label: question, kind: 'choice',
               value: on ? clean(on.closest('label').innerText) : ''});
  });
  // The checkbox group, walked in DOM order so the joins are stable.
  const boxes = form.querySelectorAll('input[name="areas"]');
  const checked = [];
  Array.prototype.forEach.call(boxes, function (box) {
    if (box.checked) checked.push(clean(box.closest('label').innerText));
  });
  rows.push({label: 'Which areas do you have experience with?',
             kind: 'choice', value: checked.join(' | ')});
  // The ARIA toggle: what the page now says it is set to.
  form.querySelectorAll('[role="switch"]').forEach(function (el) {
    rows.push({label: labelFor(el), kind: 'switch',
               value: el.getAttribute('aria-checked') === 'true'
                      ? 'Yes' : 'No'});
  });
  return JSON.stringify(rows);
})()
"""


def _page_state(driver):
    """label -> {'kind', 'value'} as the PAGE holds it right now."""
    import json
    rows = json.loads(driver.execute_script(JS_READ_BACK))
    out = {}
    for row in rows:
        label = (row.get("label") or "").strip().rstrip("*").strip()
        if label:
            out[label] = {"kind": row.get("kind"), "value": row.get("value")}
    return out


# ── the grading table: one right answer per field ───────────────────────────
def _norm(s):
    return "".join(str(s or "").lower().split())


def _no_wrong_city(v):
    bad = [c for c in ("saint paul", "oxnard", "florida", "valencia",
                       "springfield", "minnesota")
           if c in (v or "").lower()]
    return (not bad), ("mixes in %s" % ", ".join(bad)) if bad else ""


CHECKS = (
    # label substring, hard?, predicate(value) -> (ok, why)
    ("first name", True, lambda v: _norm(v) == "rodrigo",
     "the resume header's first token"),
    ("last name", True, lambda v: _norm(v) == "benitez",
     "the resume header's surname"),
    ("email address", True,
     lambda v: _norm(v) == "benitezrodrigo169@gmail.com",
     "THE case that kept falling back to asking the user"),
    ("phone country code", True, lambda v: "argentina" in v.lower(),
     "the (+54) dial code the material holds verbatim"),
    ("mobile phone", True, lambda v: "5498" in "".join(v.split()),
     "the resume's own number, digits intact"),
    ("location (city)", True, lambda v: "buenos aires" in v.lower()
     and _no_wrong_city(v)[0],
     "regression: 'Buenos Aires, Saint Paul' merged a 2018 employer row"),
    ("current company", False, lambda v: "synopsys" in v.lower(),
     "combo/typeahead grounded in the current role"),
    ("current job title", False, lambda v: "devops" in v.lower()
     or "engineer" in v.lower(), "the current role's title"),
    ("total years of experience", False, lambda v: "6 to 8" in v.lower()
     or "9 or more" in v.lower(), "'7+ years' -> the bucket holding it"),
    ("highest level of education", False,
     lambda v: bool(v), "no degree / other - either is honest"),
    ("do you prefer remote work", False, lambda v: v.lower() == "yes",
     "every role on the resume is Remote"),
    ("legally authorized to work in argentina", False,
     lambda v: v.lower() == "yes", "based in Buenos Aires, Argentina"),
    ("which areas", True, lambda v: "rag" in v.lower()
     and "forklift" not in v.lower() and "payroll" not in v.lower(),
     "a checkbox group must be SELECTIVE, not greedy"),
    ("consent", False, lambda v: v.lower() in ("yes", "true", "on", ""),
     "an ARIA switch"),
    ("desired annual salary", True, lambda v: not any(
        c.isdigit() for c in (v or "")),
     "the resume holds NOTHING - any number is invented"),
    ("why are you a fit", True, lambda v: len((v or "").strip()) >= 20
     and _no_wrong_city(v)[0],
     "free text: grounded, and never the wrong-city blob"),
)


def _grade(page, node_results):
    """Apply CHECKS to the page read-back; returns rows + hard failures."""
    rows, failures = [], []
    by_label = {_norm(r.get("label")): r for r in (node_results or [])}
    for frag, hard, predicate, why in CHECKS:
        key = _norm(frag)
        page_row = next((v for k, v in page.items() if key in _norm(k)), None)
        node_row = next((v for k, v in by_label.items() if key in k), None)
        value = (page_row or {}).get("value", "")
        ok, reason = False, "field not on the page"
        if page_row is not None:
            try:
                ok = bool(predicate(value))
                reason = "" if ok else _no_wrong_city(value)[1]
            except Exception as exc:
                reason = "predicate error: %s" % exc
        rows.append({
            "field": frag, "hard": hard, "ok": ok, "value": value,
            "status": (node_row or {}).get("status", ""),
            "route": (node_row or {}).get("route", ""),
            "why": reason or why,
        })
        if hard and not ok:
            failures.append("%s -> %r (%s)" % (frag, value, reason or why))
    return rows, failures


# ── the run ────────────────────────────────────────────────────────────────
def run_fill(max_fields=0, headless=True, keep_open=False, verbose=True):
    """Fill the fixture form with the real node; returns (rows, failures)."""
    import json

    if str(_LOOPER) not in sys.path:
        sys.path.insert(0, str(_LOOPER))
    if str(_LOOPER.parent) not in sys.path:
        sys.path.insert(0, str(_LOOPER.parent))

    from player.multi_sequence.worflow_interpreter_modules.executor_modules\
        .handle_ops import HandleMixin
    from player.multi_sequence.worflow_interpreter_modules.executor_modules\
        .form_filler_modules._test_support import _Harness
    from player.multi_sequence.worflow_interpreter_modules.executor_modules\
        .form_filler_modules.crag import FormFillerCragMixin

    api_url = _api_url()
    url, httpd = _serve_fixture()

    with open(RESUME, "r", encoding="utf-8", errors="ignore") as handle:
        pool = "## Documents\n" + handle.read()
    print("pool: %d chars from %s" % (len(pool), RESUME), flush=True)

    driver = _open_page(url, headless=headless)

    class Studio(_Harness, HandleMixin):
        """The node with a browser and a pool: nothing else is stubbed."""

        # The unit harness answers every line with 0.0 (a unit run has no
        # engine).  THIS run is the engine: the header lines are scored by
        # Laya, which is what decides which part of the document answers.
        _ff_answer_scores = FormFillerCragMixin._ff_answer_scores

        def __init__(self):
            super().__init__()
            self._ff_web_driver = driver

        def _ff_gather_source_text(self, node):
            return pool

        def _ff_shared_driver(self):
            return driver

        def _ff_enable_overlay(self, *_a, **_k):
            return None

    harness = Studio()
    asked = []
    harness.set_variable(
        "_ask_user_v2_callback",
        lambda req: (asked.append(req.get("question") or "?"),
                     {"value": ""})[1])

    # Spy on the CRAG entry so the report shows WHICH route each field took.
    routes = {}
    real_retrieve = harness._ff_retrieve

    def _spied_retrieve(label, *args, **kwargs):
        t0 = time.time()
        value = real_retrieve(label, *args, **kwargs)
        info = {
            "route": getattr(harness, "_ff_last_route", "") or "",
            "pack": len(value or ""),
            "secs": round(time.time() - t0, 1),
        }
        routes[_norm(label)] = info
        # Streamed so a multi-minute run is observable while it works.
        say("  retrieved %-34r %ss | %s | pack %d chars"
            % (str(label)[:34], info["secs"], info["route"], info["pack"]))
        return value
    harness._ff_retrieve = _spied_retrieve

    node_data = {
        "mode": "web",
        "instruction": "Fill the form using the provided context.",
        "engine": "llamacpp",
        "model": MODEL,
        "temperature": 0.1,
        "max_tokens": 500,
        "context_size": 4096,
        "verify": True,
        "repair": True,
        "repair_attempts": 2,
        "ask_user": False,          # unattended: never block on a human
        "answer_no": True,
        "answer_na": True,
        "max_fields": max_fields or 30,
        "web_scope": "",            # the page holds ONLY the form
    }
    node = {"id": "ff_e2e", "data": node_data, "inputs": [],
            "connections": {"output": []}}

    t0 = time.time()
    try:
        harness._execute_form_filling_node(node, None)
    except Exception as exc:
        say("NODE RAISED: %r" % exc)
        raise
    finally:
        elapsed = time.time() - t0

    payload = harness.get_variable("node_ff_e2e_output") or "{}"
    try:
        summary = json.loads(payload)
    except Exception:
        summary = {}
    results = summary.get("fields") or []
    for row in results:
        info = routes.get(_norm(row.get("label")), {})
        row["route"] = info.get("route", "")
        row["retrieve_secs"] = info.get("secs", 0)

    page = _page_state(driver)
    rows, failures = _grade(page, results)

    if verbose:
        _report(rows, results, page, summary, elapsed, asked, failures)

    if keep_open:
        from player.web.session import detach_session
        detach_session(driver)
        print("browser left open for inspection (LOOPER_E2E_KEEP_OPEN)",
              flush=True)
    else:
        from player.web.session import cleanup_session
        cleanup_session(driver)
    httpd.shutdown()
    return rows, failures


def _report(rows, results, page, summary, elapsed, asked, failures):
    print("\n" + "=" * 100)
    print("NODE SUMMARY: filled=%s skipped=%s failed=%s na=%s repaired=%s "
          "in %.1fs" % (summary.get("filled"), summary.get("skipped"),
                        summary.get("failed"), summary.get("answered_na"),
                        summary.get("repaired"), elapsed))
    print("=" * 100)
    by_label = {_norm(r.get("label")): r for r in results}
    print("%-4s %-34s %-8s %-9s %s" % ("ok", "field", "status", "retr.s",
                                       "value read back from the page"))
    print("-" * 100)
    for row in rows:
        flag = ("PASS" if row["ok"] else ("FAIL" if row["hard"] else "warn"))
        print("%-4s %-34s %-8s %-9s %r" % (
            flag, row["field"][:34], row["status"] or "-",
            (by_label.get(_norm(row["field"]), {}) or {}).get("retrieve_secs",
                                                              ""),
            (row["value"] or "")[:52]))
    print("-" * 100)
    print("CRAG route per field:")
    for row in results:
        print("  - %-32s %-46s pack=%s" % (
            str(row.get("label"))[:32], str(row.get("route"))[:46],
            row.get("retrieve_secs")))
    if asked:
        print("ASKED THE USER (%d): %s" % (len(asked), asked))
    print("\nHARD FAILURES (%d): %s" % (len(failures), failures or "none"))


def test_crag_form_fill_end_to_end():
    """The whole form, graded - skipped unless LOOPER_E2E=1."""
    import pytest
    if _SKIP:
        pytest.skip("set LOOPER_E2E=1 to run the CRAG end-to-end form fill")
    _rows, failures = run_fill(
        max_fields=int(os.environ.get("LOOPER_E2E_MAX_FIELDS") or 0),
        headless=os.environ.get("LOOPER_E2E_HEADED") != "1",
        keep_open=os.environ.get("LOOPER_E2E_KEEP_OPEN") == "1")
    assert not failures, "fields the pipeline got wrong:\n%s" % "\n".join(failures)


def main(argv):
    max_fields = 0
    if "--max-fields" in argv:
        max_fields = int(argv[argv.index("--max-fields") + 1])
    _rows, failures = run_fill(
        max_fields=max_fields,
        headless="--headed" not in argv,
        keep_open="--keep-open" in argv)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
