"""Record a demo GIF (and a still) for the **Maccor** battery dashboard.

Serves ``examples/battery_dashboard_maccor.py`` (a real Maccor cycler file
plotted in ``BatteryDataView`` — self-contained, no database) and drives the UI
with Playwright, capturing frames that are stitched into an animated GIF.

The story the GIF tells:
  1. Empty dashboard; open the left sidebar to reveal the category trees.
  2. Check the cell instance in the cell tree.
  3. Check the procedure instance in the procedure tree -> the one matching
     test is selected and its cycling data plots (voltage vs test_time).

Prerequisites:
    pip install -e ".[docs]"      # playwright + imageio
    playwright install chromium

Usage:
    python docs/media/generate_battery_dashboard_maccor.py
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
EXAMPLE = os.path.join(REPO_DIR, "examples", "battery_dashboard_maccor.py")
PORT = 5021
URL = f"http://localhost:{PORT}/battery_dashboard_maccor"
VIEWPORT = {"width": 1500, "height": 1000}

# Wunderbaum checkbox order per tree: index 0 is the tree root (the ceiling
# class node), index 1 the first concrete instance under it.
CELL_TREE, PROC_TREE = 0, 1
FIRST_INSTANCE_CB = 1

GIF_OUT = os.path.join(MEDIA_DIR, "battery_dashboard_maccor.gif")
PNG_OUT = os.path.join(MEDIA_DIR, "battery_dashboard_maccor.png")


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

            print("Select cell instance...")
            print("  cell cb:", click_tree_checkbox(page, CELL_TREE, FIRST_INSTANCE_CB))
            capture(page, frames, 1500)

            print("Select procedure instance...")
            print("  proc cb:", click_tree_checkbox(page, PROC_TREE, FIRST_INSTANCE_CB))
            capture(page, frames, 2500)  # matching test selected -> plot renders
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
