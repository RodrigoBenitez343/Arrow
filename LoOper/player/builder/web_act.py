"""web_act.py — the provider DRIVES the browser before asking the user.

The teach flow's missing half: when a tool matches no existing chain, the
builder does not immediately hand the work to the user.  The configured
provider (the builder's engine/model — DeepSeek, any BYOK model) is given a
REAL Chrome through LoOper's own web stack — the shared workbench browser
recordings use — and drives it action by action (navigate / click / type /
scroll / press) until the routine works or it reports itself stuck.

Every executed action is recorded as a web ``Event`` (the recorder's own
schema, ``player/web/events.py``), so a successful demonstration freezes
into ``web_sequences/<slug>.json``, is wrapped into a sub-chain
(``wrap.wrap_recording``) and wired to the orchestrator's ``tools`` port —
a REAL tool, demonstrated instead of described.  A failure returns the
attempt summary so the teach card can say what was tried before asking the
user to record it.

Execution goes through the replay handlers (``player/web/handlers``): the
same native-first ladders web replays use, so what the provider does here is
exactly what a later replay does — if the captured locator cannot resolve,
the attempt fails loudly instead of freezing a fragile routine.
"""

import logging
import os
import re
import time

logger = logging.getLogger(__name__)

#: Hard cap on provider-driven actions per routine.
MAX_STEPS = 15

#: Page digest sizes the provider reads each turn.
STATE_TEXT_CHARS = 1400
STATE_ELEMS = 25

#: Actions-so-far lines handed back to the provider.
HISTORY_LINES = 8

#: Two consecutive failures and the attempt ends (the provider is looping).
MAX_CONSECUTIVE_FAILURES = 2

_WEB_HINT = re.compile(
    r'\b(web|browser|navigate|site|url|http|www|page|scroll|browse|click|'
    r'login|sign in|google|duckduckgo|reddit|linkedin|youtube|search)\b',
    re.IGNORECASE,
)


def looks_web(step):
    """True when a tool line plausibly involves the browser."""
    return bool(_WEB_HINT.search(str(step or '')))


# ---------------------------------------------------------------- seams
# The four functions below are the act loop's integration seams (driver,
# observation, target resolution, action execution).  Tests monkeypatch
# them; the loop mechanics stay under test.


def _driver():
    """The shared workbench browser, or None (never raises)."""
    try:
        from player.web.session import ensure_workbench_session
        return ensure_workbench_session()
    except Exception:
        logger.debug('web act: no browser available', exc_info=True)
        return None


def _page_digest(driver):
    """URL + title + visible elements + page-text excerpt (strings only)."""
    try:
        url = str(driver.current_url or '')
    except Exception:
        url = ''
    try:
        title = str(driver.title or '')
    except Exception:
        title = ''
    elems = []
    try:
        elems = driver.execute_script(_ENUMERATE_JS, STATE_ELEMS) or []
    except Exception:
        elems = []
    text = ''
    try:
        text = str(driver.execute_script(
            "return document.body ? document.body.innerText : ''") or '')
    except Exception:
        text = ''
    lines = ['URL: %s' % url, 'TITLE: %s' % title, 'ELEMENTS:']
    for item in elems:
        if not isinstance(item, dict):
            continue
        bits = ['[%s]' % str(item.get('tag') or '')]
        if item.get('text'):
            bits.append('"%s"' % str(item['text'])[:90])
        if item.get('placeholder'):
            bits.append('placeholder="%s"' % str(item['placeholder'])[:60])
        if item.get('type'):
            bits.append('type=%s' % str(item['type'])[:20])
        lines.append('- ' + ' '.join(bits))
    if not elems:
        lines.append('- (none found)')
    lines.append('PAGE TEXT (excerpt):')
    lines.append(' '.join(text.split())[:STATE_TEXT_CHARS] or '(empty)')
    return '\n'.join(lines)


def _find_element(driver, target):
    """(element, Locator) for a target description, or (None, None).

    The descriptor matching runs in the page (exact text first, then
    contains, then placeholder/aria); a CSS-looking target tries the driver
    selector path first.  The captured Locator carries the replay layers
    (id / attributes / text xpath / classes), so replay re-finds the same
    element on later page instances.
    """
    target = str(target or '').strip()
    if not target:
        return None, None
    element = None
    looks_css = bool(target[:1] in '#.[' or ' > ' in target)
    if looks_css:
        try:
            from selenium.webdriver.common.by import By
            found = driver.find_elements(By.CSS_SELECTOR, target)
            for candidate in found:
                try:
                    if candidate.is_displayed():
                        element = candidate
                        break
                except Exception:
                    continue
        except Exception:
            element = None
    if element is None:
        try:
            match = driver.execute_script(_FIND_JS, target)
        except Exception:
            match = None
        if isinstance(match, dict):
            element = match.get('el')
    if element is None:
        return None, None
    locator = _capture_locator(driver, element)
    return element, locator


def _capture_locator(driver, element):
    """The recorded Locator for a live element (recorder fields)."""
    try:
        from player.web.events import Locator
    except Exception:
        return None
    try:
        desc = driver.execute_script(_CAPTURE_JS, element)
    except Exception:
        desc = None
    if not isinstance(desc, dict):
        return None
    fields = {
        'tag': desc.get('tag'),
        'id': desc.get('id'),
        'name': desc.get('name'),
        'role': desc.get('role'),
        'text': desc.get('text'),
        'href': desc.get('href'),
        'placeholder': desc.get('placeholder'),
        'aria_label': desc.get('aria_label'),
        'input_type': desc.get('input_type'),
        'data_attrs': dict(desc.get('data_attrs') or {}),
        'classes': list(desc.get('classes') or []),
        'link_text': desc.get('link_text'),
        'title': desc.get('title'),
        'value': desc.get('value'),
    }
    text = str(desc.get('text') or '').strip()
    if text and fields.get('tag'):
        fields['text_xpath'] = _text_xpath(fields['tag'], text)
    return Locator(**fields)


def _execute_event(driver, event, config):
    """Run one action through the replay handlers (native-first ladder)."""
    try:
        from player.web.handlers import HANDLERS
    except Exception as exc:
        return {'ok': False, 'error': 'web handlers unavailable: %s' % exc}
    handler = HANDLERS.get(event.type)
    if handler is None:
        return {'ok': False, 'error': 'no handler for %s' % event.type}
    return handler.execute(driver, event, config)


# ------------------------------------------------------------- internals

_ENUMERATE_JS = """
var limit = arguments[0] || 25;
var nodes = document.querySelectorAll(
    'a,button,input,textarea,select,[role="button"],[role="link"],' +
    '[role="tab"],[role="menuitem"],[contenteditable="true"]');
var out = [];
for (var i = 0; i < nodes.length && out.length < limit; i++) {
  var el = nodes[i];
  var r = el.getBoundingClientRect();
  if (r.width < 2 || r.height < 2) continue;
  var st = window.getComputedStyle(el);
  if (st.visibility === 'hidden' || st.display === 'none') continue;
  var text = (el.innerText || el.value || el.getAttribute('aria-label') ||
              el.getAttribute('placeholder') || el.getAttribute('title') || '')
              .replace(/\\s+/g, ' ').trim();
  var tag = el.tagName.toLowerCase();
  if (!text && tag !== 'input' && tag !== 'textarea') continue;
  out.push({tag: tag, text: text.slice(0, 110),
            placeholder: (el.getAttribute('placeholder') || '').slice(0, 80),
            type: el.getAttribute('type') || ''});
}
return out;
"""

_FIND_JS = """
var want = String(arguments[0] || '').replace(/\\s+/g, ' ').trim().toLowerCase();
if (!want) return null;
var nodes = document.querySelectorAll(
    'a,button,input,textarea,select,[role="button"],[role="link"],' +
    '[role="tab"],[role="menuitem"],[contenteditable="true"]');
var best = null, bestScore = 0, bestText = '';
for (var i = 0; i < nodes.length; i++) {
  var el = nodes[i];
  var r = el.getBoundingClientRect();
  if (r.width < 2 || r.height < 2) continue;
  var st = window.getComputedStyle(el);
  if (st.visibility === 'hidden' || st.display === 'none') continue;
  var text = String(el.innerText || el.value || '').replace(/\\s+/g, ' ').trim();
  var label = text || String(el.getAttribute('placeholder') ||
      el.getAttribute('aria-label') || el.getAttribute('title') || '')
      .replace(/\\s+/g, ' ').trim();
  var low = label.toLowerCase();
  if (!low) continue;
  var score = 0;
  if (low === want) score = 100;
  else if (low.indexOf(want) >= 0) score = 60 + (want.length / Math.max(1, low.length)) * 20;
  else if (want.indexOf(low) >= 0 && low.length > 2) score = 40;
  if (score > bestScore) { bestScore = score; best = el; bestText = label; }
}
if (!best || bestScore < 40) return null;
return {el: best, text: bestText.slice(0, 110)};
"""

_CAPTURE_JS = """
var el = arguments[0];
var norm = function (s) { return String(s || '').replace(/\\s+/g, ' ').trim(); };
var attrs = {}, i;
for (i = 0; i < el.attributes.length; i++) {
  var a = el.attributes[i];
  if (a.name.indexOf('data-') === 0) attrs[a.name] = String(a.value).slice(0, 120);
}
var classes = [];
if (el.className && typeof el.className === 'string') {
  classes = el.className.split(/\\s+/).filter(function (c) { return c; }).slice(0, 5);
}
return {
  tag: el.tagName.toLowerCase(),
  id: el.id || null,
  name: el.getAttribute('name'),
  role: el.getAttribute('role'),
  text: norm(el.innerText || el.textContent || '').slice(0, 120) || null,
  href: (el.getAttribute('href') || '').slice(0, 200) || null,
  placeholder: el.getAttribute('placeholder'),
  aria_label: el.getAttribute('aria-label'),
  input_type: el.getAttribute('type'),
  data_attrs: attrs,
  classes: classes,
  link_text: (el.tagName === 'A' ? norm(el.innerText || '').slice(0, 80) || null : null),
  title: el.getAttribute('title'),
  value: (el.value !== undefined && el.value !== null ? String(el.value).slice(0, 120) : null)
};
"""


def _text_xpath(tag, text):
    """The text layer replay resolves when ids/classes rotate."""
    body = ' '.join(str(text or '').split())[:60]
    if not body:
        return None
    if '"' in body and "'" in body:
        body = body.replace('"', '')
    quote = '"' if '"' not in body else "'"
    return '//%s[contains(normalize-space(.), %s%s%s)]' % (
        str(tag or '*'), quote, body, quote)


def _window_ordinal(driver):
    try:
        return list(driver.window_handles).index(driver.current_window_handle)
    except Exception:
        return None


def _recordings_dir(ctx):
    """``web_sequences/`` next to the chains folder (app layout)."""
    chains_dir = str(ctx.get('chains_dir') or '')
    base = os.path.dirname(chains_dir) if chains_dir else ''
    if not base:
        base = os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, 'web_sequences')


def _unique_path(path):
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    n = 2
    while os.path.exists('%s_%d%s' % (stem, n, ext)):
        n += 1
    return '%s_%d%s' % (stem, n, ext)


def _replay_config():
    """Native-first config for the act loop's action execution."""
    try:
        from player.web.engine import ReplayConfig
        return ReplayConfig(headless=False, speed=1.0, min_pause=0.1,
                            max_pause=0.6, element_timeout=6.0,
                            native_actions=True)
    except Exception:
        return None


def attempt_web_tool(step, goal, ctx, brain_id='', trace=None, settings=None):
    """Demonstrate one routine in the real browser, or report the failure.

    Returns ``(ok, lines, note)``: ``ok`` True means the routine was
    performed, recorded and wired (a tool was added — ``wrap.wrap_recording``
    emitted the wire lines into ``lines``).  On failure ``note`` carries the
    short summary the teach card shows before asking the user to record it.
    Never raises.
    """
    from player.builder.trace import emit as _emit

    lines = []

    def _step(message):
        _emit(trace, message)
        lines.append(message)

    if settings is None:
        try:
            from player.builder import settings as _settings
            settings = _settings.load()
        except Exception:
            settings = {}
    if not (settings or {}).get('reasoning', True):
        return False, lines, 'the builder reasoning model is off'

    try:
        from player.builder import reasoning as _reasoning
    except Exception:
        return False, lines, 'the reasoning module is unavailable'

    stop = ctx.get('stop')

    def _stopped():
        try:
            return bool(stop()) if callable(stop) else False
        except Exception:
            return False

    driver = _driver()
    if driver is None:
        return False, lines, 'no browser could be started'
    config = _replay_config()

    step = str(step or '').strip()
    _step('[act] demonstrating "%s" in the browser' % step[:70])
    try:
        start_url = str(driver.current_url or '')
    except Exception:
        start_url = ''

    from player.web.events import Event, save_session

    events = []
    history = []
    last_ts = None
    failures = 0

    for _i in range(MAX_STEPS):
        if _stopped():
            return False, lines, 'stopped by the user'
        digest = _page_digest(driver)
        try:
            action = _reasoning.act_web(goal, step, digest, history, settings)
        except Exception:
            logger.debug('act_web call failed', exc_info=True)
            action = None
        if not action:
            note = ('the model stopped answering after %d action(s)'
                    % len(events)) if events else \
                'the model did not answer'
            if events:
                break                       # freeze whatever worked
            return False, lines, note
        kind = str(action.get('action') or '')

        if kind == 'done':
            break
        if kind == 'stuck':
            reason = str(action.get('reason') or '').strip()
            return (False, lines,
                    ('tried %d action(s) then got stuck: %s'
                     % (len(events), reason[:200])) if reason
                    else 'got stuck after %d action(s)' % len(events))
        if kind == 'wait':
            try:
                seconds = max(0.5, min(float(action.get('seconds') or 2.0), 10.0))
            except Exception:
                seconds = 2.0
            time.sleep(seconds)
            history.append('wait %.0fs' % seconds)
            continue

        event = None
        summary = ''
        ordinal = _window_ordinal(driver)
        ts = time.time() * 1000.0
        gap = int(ts - last_ts) if last_ts else None

        if kind == 'navigate':
            url = str(action.get('url') or '').strip()
            if not url.lower().startswith(('http://', 'https://')):
                failures += 1
                history.append('navigate FAILED: %r is not an http(s) URL' % url)
                if failures >= MAX_CONSECUTIVE_FAILURES:
                    return False, lines, 'the model kept sending bad URLs'
                continue
            event = Event(type='navigate', ts=ts, url=url)
            summary = 'navigate to %s' % url[:80]
        elif kind in ('click', 'type'):
            target = str(action.get('target') or '').strip()
            element, locator = (None, None)
            try:
                element, locator = _find_element(driver, target)
            except Exception:
                logger.debug('find failed', exc_info=True)
            if kind == 'click':
                if element is None:
                    failures += 1
                    history.append('click FAILED: no visible element matching '
                                   '%r' % target[:60])
                    if failures >= MAX_CONSECUTIVE_FAILURES:
                        return False, lines, ('could not find "%s" on the page'
                                              % target[:80])
                    continue
                event = Event(type='click', ts=ts, locator=locator,
                              button='left', window_ordinal=ordinal)
                summary = 'click "%s"' % (target or
                                          (locator.text if locator else ''))[:70]
            else:
                value = str(action.get('value') or '')
                if not value:
                    failures += 1
                    history.append('type FAILED: no value given')
                    continue
                event = Event(type='type', ts=ts, locator=locator,
                              value=value, window_ordinal=ordinal)
                summary = 'type "%s" into "%s"' % (
                    value[:40], target[:40] or '(focused)')
        elif kind == 'scroll':
            try:
                dy = int(float(action.get('value') or 800))
            except Exception:
                dy = 800
            dy = max(-5000, min(5000, dy)) or 800
            event = Event(type='scroll', ts=ts, window_ordinal=ordinal,
                          scroll={'delta_x': 0, 'delta_y': dy,
                                  'total_delta': dy})
            summary = 'scroll %+d' % dy
        elif kind == 'press':
            key = str(action.get('key') or '').strip()
            if not key:
                failures += 1
                history.append('press FAILED: no key given')
                continue
            event = Event(type='key', ts=ts, key=key,
                          modifiers=[str(m) for m in
                                     (action.get('modifiers') or [])],
                          window_ordinal=ordinal)
            summary = 'press %s' % key[:30]
        else:
            failures += 1
            history.append('unknown action %r' % kind[:30])
            continue

        if event is not None:
            event.gap_ms = gap
            result = _execute_event(driver, event, config) \
                if config is not None else {'ok': False,
                                            'error': 'no replay config'}
            if not isinstance(result, dict) or not result.get('ok'):
                err = (result or {}).get('error') if isinstance(result, dict) \
                    else 'handler failed'
                failures += 1
                history.append('%s FAILED: %s' % (summary, str(err)[:120]))
                if failures >= MAX_CONSECUTIVE_FAILURES:
                    return False, lines, ('%s failed twice (%s)'
                                          % (summary[:60], str(err)[:120]))
                continue
            failures = 0
            events.append(event)
            last_ts = ts
            history.append('%s — ok (%s)' % (summary, result.get('method') or ''))
            _step('[act] %s' % summary)
            time.sleep(0.6)

    if not events:
        return False, lines, 'nothing was performed'

    # Freeze the demonstration: recording file -> sub-chain -> wired import.
    try:
        from player.builder.node_templates import slugify
        name = slugify(step)[:60] or 'act'
        web_dir = _recordings_dir(ctx)
        rec_path = _unique_path(os.path.join(web_dir, name + '.json'))
        save_session(events, rec_path, start_url=start_url,
                     name=step[:120])
    except Exception as e:
        return False, lines, 'recording the demonstration failed: %s' % e
    _step('[act] recorded %d action(s) → %s'
          % (len(events), os.path.basename(rec_path)))

    try:
        from player.builder import wrap as _wrap
        wlines, chain_path = _wrap.wrap_recording(
            ctx, rec_path, step, kind='web_sequence', brain_id=brain_id,
            trace=trace)
        lines.extend(wlines)
    except Exception as e:
        return False, lines, 'wiring the demonstration failed: %s' % e
    if not chain_path:
        return False, lines, ('the demonstration was recorded (%s) but '
                              'wiring it failed' % os.path.basename(rec_path))
    _step('[act] "%s" demonstrated and wired (%s)'
          % (step[:70], os.path.basename(chain_path)))
    return True, lines, ''
