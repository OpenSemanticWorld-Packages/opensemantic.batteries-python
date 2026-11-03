"""Record a demo GIF (and a still) for the **Maccor** battery dashboard.

Serves ``examples/maccor/battery_dashboard_maccor.py`` (a real Maccor cycler file
plotted in ``BatteryDataView`` — self-contained, no database) and drives the UI
with Playwright, capturing frames that are stitched into an animated GIF.

The story the GIF tells:
  1. Empty dashboard; the left sidebar is open on start (category trees,
     instances) and the plot cards are collapsed.
  2. Check the root of the cell tree (Cells A and B).
  3. Check the root of the procedure tree (Aging A and B) -> the four
     matching tests are selected. Expand "Time Series" -> its cycling data
     plots (voltage vs test_time).
  4. Expand "Cycle Series" -> discharge capacity per cycle.
  5. Collapse "Time Series", expand "Batches" and click "Procedure batches",
     then expand "Batch Series" -> the batch mean of the discharge capacity
     with error bars.

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
    click_button,
    click_tree_checkbox,
    collapse_card,
    expand_card,
    expand_tree_nodes,
    open_sidebar,
    start_server,
    stop_server,
)
from playwright.sync_api import sync_playwright

MEDIA_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(os.path.dirname(MEDIA_DIR))
EXAMPLE = os.path.join(REPO_DIR, "examples", "maccor", "battery_dashboard_maccor.py")
PORT = 5021
URL = f"http://localhost:{PORT}/battery_dashboard_maccor"
VIEWPORT = {"width": 1500, "height": 1000}

# Wunderbaum checkbox order per tree: index 0 is the tree root (the ceiling
# class node). The trees are lazy and start collapsed; with ``selectMode: hier``
# ticking the root selects every instance below it (Cells A, B and Aging A, B).
CELL_TREE, PROC_TREE = 0, 1
ROOT_CB = 0

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
            capture(page, frames, 800)  # empty dashboard, sidebar open on start

            print("Make sure the sidebar is open...")
            print("  sidebar:", open_sidebar(page))

            print("Select all cells...")
            print("  cell cb:", click_tree_checkbox(page, CELL_TREE, ROOT_CB))
            capture(page, frames, 1500)

            print("Select all procedures...")
            print("  proc cb:", click_tree_checkbox(page, PROC_TREE, ROOT_CB))
            expand_tree_nodes(page)  # show the lazily loaded instances
            capture(page, frames, 2000)  # matching tests selected

            print("Expand the Time Series plot card...")
            print("  card:", expand_card(page, "Time Series"))
            capture(page, frames, 1500)  # cycling data plots
            plot_frame = frames[-1]

            print("Expand the Cycle Series plot card...")
            print("  card:", expand_card(page, "Cycle Series"))
            capture(page, frames, 1500)  # discharge capacity per cycle

            print("Collapse Time Series to make room for the batch plot...")
            print("  card:", collapse_card(page, "Time Series"))

            print("Expand Batches and group by procedure...")
            print("  card:", expand_card(page, "Batches"))
            click_button(page, "Procedure batches")
            capture(page, frames, 1000)

            print("Expand the Batch Series plot card...")
            print("  card:", expand_card(page, "Batch Series"))
            capture(page, frames, 2000)  # batch mean with error bars
            capture(page, frames, 1500)  # hold on the final result
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
