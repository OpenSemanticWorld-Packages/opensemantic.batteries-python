"""Record a demo GIF (and a still) for the **sample** battery dashboard.

Serves ``examples/battery_dashboard.py`` (synthetic sample data: three cells,
three procedures, six tests) and drives the UI with Playwright, capturing frames
stitched into an animated GIF.

The story the GIF tells (a fuller walk-through than the Maccor demo):
  1. Empty dashboard; open the left sidebar to reveal the category trees.
  2. Check only the round cells (the ``CylindricalCell`` subtree -> Cell A, B).
  3. Check the whole procedure tree (all procedures) -> every matching test
     plots as its own trace.
  4. Uncheck the "Aging (B)" run in the Instances card -> that trace drops.
  5. Put ``current`` on the y2 (right) axis in Axes & Units -> a second axis
     appears with the current traces.
  6. Switch the current unit from A to mA -> the right axis rescales.

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
    set_axis,
    set_unit,
    start_server,
    stop_server,
    toggle_row_checkbox,
)
from playwright.sync_api import sync_playwright

MEDIA_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(os.path.dirname(MEDIA_DIR))
EXAMPLE = os.path.join(REPO_DIR, "examples", "battery_dashboard.py")
PORT = 5022
URL = f"http://localhost:{PORT}/battery_dashboard"
# Tall viewport so the whole sidebar (down to the ``current`` row of the Axes &
# Units grid) is on-screen — the axis/unit helpers click by viewport geometry.
VIEWPORT = {"width": 1500, "height": 1400}

# Cell-tree checkbox order (selectMode: hier): 0 BatteryCell (root), 1
# CylindricalCell (-> Cell A, Cell B = the round cells), 4 PrismaticCell.
CELL_TREE, PROC_TREE = 0, 1
CYLINDRICAL_CB = 1
PROC_ROOT_CB = 0

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

            print("Select round (cylindrical) cells...")
            print("  cylindrical:", click_tree_checkbox(page, CELL_TREE, CYLINDRICAL_CB))
            capture(page, frames, 1500)

            print("Select all procedures...")
            print("  proc root:", click_tree_checkbox(page, PROC_TREE, PROC_ROOT_CB))
            capture(page, frames, 3000)  # matching tests plot as traces
            plot_frame = frames[-1]

            print("Uncheck the Aging (B) instance...")
            print("  uncheck:", toggle_row_checkbox(page, "Aging (B)"))
            capture(page, frames, 2500)  # that trace drops

            print("Put current on the y2 axis...")
            print("  current y2:", set_axis(page, "current", "y2"))
            capture(page, frames, 2500)  # second (right) axis appears

            print("Switch current unit A -> mA...")
            print("  mA:", set_unit(page, "current", "mA"))
            capture(page, frames, 2500)  # right axis rescales
            plot_frame = frames[-1]
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
