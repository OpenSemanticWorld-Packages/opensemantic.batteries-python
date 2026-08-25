"""Record a demo GIF (and a still) for the **sample** battery dashboard.

Serves ``examples/battery_dashboard.py`` (synthetic sample data: three cells,
three procedures, six tests) and drives the UI with Playwright, capturing frames
stitched into an animated GIF.

Unlike the single-file Maccor demo, this one checks each tree's *root* checkbox
(``selectMode: hier`` selects every descendant), so all cells and procedures are
picked at once and every matching test plots — showing multiple overlaid traces.

The story the GIF tells:
  1. Empty dashboard; open the left sidebar to reveal the category trees.
  2. Check the whole cell tree (all cells).
  3. Check the whole procedure tree (all procedures) -> every matching test
     plots as its own trace.

Prerequisites:
    pip install -e ".[docs]"      # playwright + imageio
    playwright install chromium

Usage:
    python docs/media/generate_battery_dashboard.py
"""

import os

import imageio.v3 as iio
from _screenshot_utils import (
    capture,
    click_tree_checkbox,
    open_sidebar,
    start_server,
    stop_server,
)
from playwright.sync_api import sync_playwright

MEDIA_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(os.path.dirname(MEDIA_DIR))
EXAMPLE = os.path.join(REPO_DIR, "examples", "battery_dashboard.py")
PORT = 5022
URL = f"http://localhost:{PORT}/battery_dashboard"
VIEWPORT = {"width": 1500, "height": 1000}

# selectMode: hier -> checking the root checkbox (index 0) selects every cell /
# procedure under it, so all six tests plot at once.
CELL_TREE, PROC_TREE = 0, 1
ROOT_CB = 0

GIF_OUT = os.path.join(MEDIA_DIR, "battery_dashboard.gif")
PNG_OUT = os.path.join(MEDIA_DIR, "battery_dashboard.png")


def main():
    frames = []
    plot_frame = None

    proc = start_server(EXAMPLE, PORT)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch(headless=True).new_page(viewport=VIEWPORT)
            page.goto(URL, timeout=120000, wait_until="domcontentloaded")
            page.wait_for_timeout(6000)
            capture(page, frames, 800)  # empty dashboard, sidebar collapsed

            print("Open the sidebar...")
            print("  toggle:", open_sidebar(page))
            capture(page, frames, 1500)  # trees revealed

            print("Select all cells...")
            print("  cell root:", click_tree_checkbox(page, CELL_TREE, ROOT_CB))
            capture(page, frames, 1500)

            print("Select all procedures...")
            print("  proc root:", click_tree_checkbox(page, PROC_TREE, ROOT_CB))
            capture(page, frames, 3000)  # matching tests selected -> traces plot
            plot_frame = frames[-1]
            capture(page, frames, 1500)  # hold on the plotted result
    finally:
        stop_server(proc)

    if os.environ.get("GEN_DEBUG"):
        for i, fr in enumerate(frames):
            iio.imwrite(os.path.join(MEDIA_DIR, f"_frame_{i}.png"), fr)
        print(f"dumped {len(frames)} debug frames")

    iio.imwrite(GIF_OUT, frames, duration=1400, loop=0)
    print(f"{GIF_OUT}: {len(frames)} frames")
    if plot_frame is not None:
        iio.imwrite(PNG_OUT, plot_frame)
        print(f"{PNG_OUT} saved")


if __name__ == "__main__":
    main()
