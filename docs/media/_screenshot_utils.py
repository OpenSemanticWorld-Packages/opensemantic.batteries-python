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
