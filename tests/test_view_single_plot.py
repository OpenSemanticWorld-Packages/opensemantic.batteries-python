"""Tests for the single plot of BatteryDataView.

The view holds one plot container that is re-rendered whenever the selection
changes. Driven in ``embeddable=True`` mode (no browser) through the same
watchers a user triggers (tree ``source`` and axis checkboxes).

Skipped as a group when the ``[view]`` extra is not installed.
"""

from __future__ import annotations

import copy
from typing import Any, List

import pytest

panel = pytest.importorskip("panel")
pytest.importorskip("panelini")

from bokeh.plotting import figure as bk_figure  # noqa: E402
from _view_helpers import make_view  # noqa: E402


@pytest.fixture
def view(monkeypatch):
    v = make_view()

    # Isolate the container logic from the pandas/pint figure building: return a
    # fresh, distinct Bokeh figure whenever the selection resolves to traces.
    def fake_make_figure():
        return bk_figure() if v._resolve_traces() else None

    monkeypatch.setattr(v, "_make_figure", fake_make_figure)
    return v


def _tick(tree: Any, obj_ids: List[str]) -> None:
    """Mimic the browser echoing ``source`` with the given instances ticked."""
    source = copy.deepcopy(tree.source)

    def walk(nodes):
        for n in nodes:
            if n.get("data", {}).get("node_id") in obj_ids:
                n["selected"] = True
            walk(n.get("children", []))

    walk(source)
    tree.source = source


def _node_id(obj: Any, mapping: dict) -> str:
    return next(k for k, o in mapping.items() if o is obj)


def _select_via_trees(view, cell: Any, proc: Any) -> None:
    _tick(view._cell_tree, [_node_id(cell, view._cell_objects)])
    _tick(view._proc_tree, [_node_id(proc, view._procedure_objects)])


def _bokeh_panes(view) -> List[Any]:
    return [o for o in view._plot_col.objects if isinstance(o, panel.pane.Bokeh)]


def _all_buttons(obj: Any) -> List[Any]:
    return list(obj.select(panel.widgets.Button))


def test_plot_card_holds_exactly_one_container_and_no_buttons(view):
    assert list(view._plot_card) == [view._plot_col]
    layout = view.sidebar_cards + view.main_cards
    names = [b.name for c in layout for b in _all_buttons(c)]
    assert not any(
        word in name for name in names for word in ("Freeze", "Unfreeze", "Delete")
    )


def test_selection_change_replaces_the_bokeh_pane(view):
    _select_via_trees(view, view._cell_a, view._formation)
    first = _bokeh_panes(view)
    assert len(first) == 1
    first_fig = first[0].object

    # Change the selection through the axis checkbox watcher (y1 -> current).
    view._axis_checkboxes[("current", "y1")].value = True
    second = _bokeh_panes(view)
    assert len(second) == 1
    assert second[0].object is not first_fig
    assert len(view._plot_col.objects) == 1


def test_empty_selection_shows_placeholder(view):
    _select_via_trees(view, view._cell_a, view._formation)
    assert _bokeh_panes(view)

    # Untick everything via the tree watcher.
    _tick_off = copy.deepcopy(view._cell_tree.source)

    def clear(nodes):
        for n in nodes:
            n["selected"] = False
            clear(n.get("children", []))

    clear(_tick_off)
    view._cell_tree.source = _tick_off

    assert _bokeh_panes(view) == []
    (placeholder,) = view._plot_col.objects
    assert "No data selected" in placeholder.object
