"""Shared Playwright/Panel helpers for the battery dashboard demo recorders.

Adapted from ``opensemantic.base``'s ``docs/_screenshot_utils.py``. Provides the
``panel serve`` lifecycle, Wunderbaum shadow-root checkbox clicking (the battery
dashboard has *two* trees — cell and procedure — so the multi-tree helper is
used), a real-mouse click for icon-only Panel buttons, and the screenshot-to-
frame capture used to assemble the GIF.

Used by the ``generate_battery_dashboard*.py`` recorders in this folder.
"""

import io
import os
import subprocess
import sys
import time

import imageio.v3 as iio

MEDIA_DIR = os.path.dirname(os.path.abspath(__file__))  # docs/media
REPO_DIR = os.path.dirname(os.path.dirname(MEDIA_DIR))  # repo root


def all_wb_shadows_js():
    """JS defining allWbShadows(root): every Wunderbaum shadow root, in order."""
    return """
    function allWbShadows(root) {
        const out = [];
        function rec(r) {
            for (const el of r.querySelectorAll('*')) {
                if (el.shadowRoot) {
                    if (el.shadowRoot.querySelectorAll('.wb-row').length > 0)
                        out.push(el.shadowRoot);
                    rec(el.shadowRoot);
                }
            }
        }
        rec(root);
        return out;
    }
    """


def click_tree_checkbox(page, tree_idx, cb_idx):
    """Click the cb_idx-th checkbox of the tree_idx-th Wunderbaum (0 = first).

    Tree 0 is the cell tree, tree 1 the procedure tree (build order in
    BatteryDataView). With ``selectMode: hier`` clicking the root checkbox
    (cb_idx 0) selects every descendant. Returns True if a checkbox was clicked.
    """
    return page.evaluate(
        f"""() => {{
        {all_wb_shadows_js()}
        const sh = allWbShadows(document)[{tree_idx}];
        if (sh) {{
            const cbs = sh.querySelectorAll('i.wb-checkbox');
            if (cbs[{cb_idx}]) {{ cbs[{cb_idx}].click(); return true; }}
        }}
        return false;
    }}"""
    )


def click_by_text(page, text):
    """Click the first checkbox/label/button containing ``text`` (across shadow DOMs).

    The instance and axis controls are Panel widgets (regular checkboxes with a
    text label), not Wunderbaum rows, so match them by their visible label.
    """
    return page.evaluate(
        """(t) => {
            function f(r) {
                for (const lbl of r.querySelectorAll('label')) {
                    if ((lbl.textContent || '').includes(t)) {
                        const inp = lbl.querySelector('input')
                            || (lbl.htmlFor && r.getElementById(lbl.htmlFor));
                        (inp || lbl).click();
                        return true;
                    }
                }
                for (const b of r.querySelectorAll('button')) {
                    if ((b.textContent || '').includes(t)) { b.click(); return true; }
                }
                for (const e of r.querySelectorAll('*')) {
                    if (e.shadowRoot && f(e.shadowRoot)) return true;
                }
                return false;
            }
            return f(document);
        }""",
        text,
    )


def real_click_selector(page, selector):
    """Dispatch a *real* mouse click at the center of the first ``selector`` match.

    Panel/Bokeh widgets (e.g. Panelini's sidebar-toggle ``Button``) do not fire
    their handler on a synthetic ``el.click()``; a real pointer down/up is
    required. Searches every shadow DOM for the element's bounding rect, then
    drives the Playwright mouse. Returns True if the element was found.
    """
    box = page.evaluate(
        """(sel) => {
            function f(r) {
                const el = r.querySelector(sel);
                if (el) {
                    const b = el.getBoundingClientRect();
                    return {x: b.x + b.width / 2, y: b.y + b.height / 2};
                }
                for (const e of r.querySelectorAll('*')) {
                    if (e.shadowRoot) { const g = f(e.shadowRoot); if (g) return g; }
                }
                return null;
            }
            return f(document);
        }""",
        selector,
    )
    if not box:
        return False
    page.mouse.move(box["x"], box["y"])
    page.mouse.down()
    page.wait_for_timeout(60)
    page.mouse.up()
    return True


def open_sidebar(page):
    """Reveal Panelini's left sidebar (it defaults to collapsed).

    ``sidebar_visible`` defaults to False, so the cell / procedure trees start
    hidden; click the navbar toggle so they are visible in the recording.
    """
    return real_click_selector(page, ".left-navbar-button")


# -- Axes & Units grid (anonymous checkboxes + unit selects) -----------------
# The grid rows are ``[field label | x | y1 | y2 | unit-select]`` and none of the
# widgets carry an id or css class, so they are located *geometrically*: find the
# vertical center of the row whose field-label text matches, then pick the
# checkbox / select whose bounding box sits on that same row.

_ROW_CENTER_JS = """
function* allEls(r){
    for (const el of r.querySelectorAll('*')){
        yield el;
        if (el.shadowRoot) yield* allEls(el.shadowRoot);
    }
}
function rowCenterY(field){
    for (const el of allEls(document)){
        if (el.children.length === 0 && (el.textContent || '').trim() === field){
            const b = el.getBoundingClientRect();
            if (b.width > 0 && b.height > 0) return b.top + b.height / 2;
        }
    }
    return null;
}
"""


def _real_click(page, pos):
    page.mouse.move(pos["x"], pos["y"])
    page.mouse.down()
    page.wait_for_timeout(60)
    page.mouse.up()


def set_axis(page, field, axis):
    """Toggle the x / y1 / y2 checkbox of the Axes & Units row for ``field``.

    Real-clicks the checkbox (Panel/Bokeh widgets can ignore synthetic clicks).
    The row must be within the viewport — size the viewport so the field is
    visible. Returns True if the checkbox was found.
    """
    axis_idx = {"x": 0, "y1": 1, "y2": 2}[axis]
    pos = page.evaluate(
        """(args) => {
            %s
            const rowY = rowCenterY(args.field);
            if (rowY == null) return null;
            const cbs = [];
            for (const el of allEls(document)){
                if (el.tagName === 'INPUT' && el.type === 'checkbox'){
                    const b = el.getBoundingClientRect();
                    if (b.width > 0 && Math.abs((b.top + b.height / 2) - rowY) < 20)
                        cbs.push({x: b.left + b.width / 2, y: b.top + b.height / 2,
                                  left: b.left});
                }
            }
            cbs.sort((a, b) => a.left - b.left);
            const t = cbs[args.axisIdx];
            return t ? {x: t.x, y: t.y} : null;
        }"""
        % _ROW_CENTER_JS,
        {"field": field, "axisIdx": axis_idx},
    )
    if not pos:
        return False
    _real_click(page, pos)
    return True


def real_click_by_text(page, text):
    """Real-mouse-click the first ``<button>`` whose label contains ``text``.

    Panel ``Button`` widgets (Freeze plot / Unfreeze) ignore a synthetic
    ``el.click()``; drive the Playwright mouse at the button center instead.
    Searches every shadow DOM. Returns True if a button was found.
    """
    pos = page.evaluate(
        """(t) => {
            %s
            for (const el of allEls(document)){
                if (el.tagName === 'BUTTON' && (el.textContent || '').includes(t)){
                    const b = el.getBoundingClientRect();
                    if (b.width > 0 && b.height > 0)
                        return {x: b.left + b.width / 2, y: b.top + b.height / 2};
                }
            }
            return null;
        }"""
        % _ROW_CENTER_JS,
        text,
    )
    if not pos:
        return False
    _real_click(page, pos)
    return True


def toggle_row_checkbox(page, text):
    """Real-click the single checkbox on the row whose label contains ``text``.

    For the Instances card (one checkbox + a text label per row): finds the leaf
    element containing ``text``, then the checkbox to its left on the same row.
    Returns True if found.
    """
    pos = page.evaluate(
        """(t) => {
            %s
            let rowY = null, labelLeft = null;
            for (const el of allEls(document)){
                if (el.children.length === 0 && (el.textContent || '').includes(t)){
                    const b = el.getBoundingClientRect();
                    if (b.width > 0 && b.height > 0){
                        rowY = b.top + b.height / 2; labelLeft = b.left; break;
                    }
                }
            }
            if (rowY == null) return null;
            let best = null;
            for (const el of allEls(document)){
                if (el.tagName === 'INPUT' && el.type === 'checkbox'){
                    const b = el.getBoundingClientRect();
                    if (b.width > 0 && Math.abs((b.top + b.height / 2) - rowY) < 18
                        && b.left < labelLeft){
                        if (!best || b.left > best.left)
                            best = {x: b.left + b.width / 2, y: b.top + b.height / 2,
                                    left: b.left};
                    }
                }
            }
            return best ? {x: best.x, y: best.y} : null;
        }"""
        % _ROW_CENTER_JS,
        text,
    )
    if not pos:
        return False
    _real_click(page, pos)
    return True


def set_unit(page, field, unit_symbol):
    """Pick ``unit_symbol`` (e.g. "mA") in the Axes & Units unit select for ``field``.

    Sets the aligned ``<select>``'s value to the option whose visible text is
    ``unit_symbol`` and dispatches a ``change`` event so Bokeh updates the model.
    Returns True if the option was found and applied.
    """
    return page.evaluate(
        """(args) => {
            %s
            const rowY = rowCenterY(args.field);
            if (rowY == null) return false;
            for (const el of allEls(document)){
                if (el.tagName === 'SELECT'){
                    const b = el.getBoundingClientRect();
                    if (b.width > 0 && Math.abs((b.top + b.height / 2) - rowY) < 16){
                        for (const opt of el.options){
                            if ((opt.textContent || '').trim() === args.unit){
                                el.value = opt.value;
                                el.dispatchEvent(new Event('change', {bubbles: true}));
                                return true;
                            }
                        }
                    }
                }
            }
            return false;
        }"""
        % _ROW_CENTER_JS,
        {"field": field, "unit": unit_symbol},
    )


def capture(page, frames, delay=800, full_page=False):
    """Wait ``delay`` ms, then append a screenshot to ``frames``."""
    page.wait_for_timeout(delay)
    frames.append(iio.imread(io.BytesIO(page.screenshot(full_page=full_page))))


def start_server(example, port, env=None, settle=12):
    """Start ``panel serve <example> --port <port>`` and return the process.

    ``settle`` is the seconds to wait for the first module execution (loading the
    data + building the trees) before returning. The log is written beside the
    recorders (``docs/media/``); serving runs from the repo root so the example's
    ``from battery_example_data import ...`` resolves.
    """
    logf = open(os.path.join(MEDIA_DIR, f"_gen_server_{port}.log"), "w")
    proc_env = dict(os.environ)
    if env:
        proc_env.update(env)
    proc = subprocess.Popen(
        [sys.executable, "-m", "panel", "serve", example, "--port", str(port)],
        stdout=logf,
        stderr=subprocess.STDOUT,
        cwd=REPO_DIR,
        env=proc_env,
    )
    time.sleep(settle)
    return proc


def stop_server(proc):
    # On Windows, terminate() leaves the `panel serve` child alive (it keeps the
    # port and leaks servers across runs); kill the whole process tree.
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
        )
    else:
        proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
