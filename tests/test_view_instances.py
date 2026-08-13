"""Tests for the BatteryDataView "Instances" sidebar card.

The card lists the test runs whose cell *and* procedure are both selected, with
one checkbox per instance; unchecking one drops it from the plot. These tests
drive the view in ``embeddable=True`` mode (no browser / Panelini app) and poke
the selection/toggle handlers the tree widgets would otherwise call.

Skipped as a group when the ``[view]`` extra is not installed.
"""

from __future__ import annotations

from typing import List

import pytest

panel = pytest.importorskip("panel")
pytest.importorskip("panelini")

from _view_helpers import make_view, node_id_for, select  # noqa: E402

Checkbox = panel.widgets.Checkbox


@pytest.fixture
def view():
    return make_view()


def _instance_labels(view) -> List[str]:
    return [w.name for w in view._instances_col if isinstance(w, Checkbox)]


def test_no_selection_lists_nothing(view):
    assert view._matching_tests() == []
    assert _instance_labels(view) == []  # only a placeholder pane, no checkboxes
    assert view._resolve_traces() == []


def test_selection_lists_matching_instances(view):
    select(view, cell=view._cell_a, proc=view._formation)

    # Cell A + Formation matches exactly one test run.
    assert _instance_labels(view) == ["Cell A - Formation Dataset"]
    assert len(view._resolve_traces()) == 1


def test_unchecking_instance_removes_it_from_plot(view):
    # Select Cell A + Cell B and Formation -> two matching instances.
    view._selected_cell_ids = [
        node_id_for(view._cell_a, view._cell_objects),
        node_id_for(view._cell_b, view._cell_objects),
    ]
    view._selected_proc_ids = [node_id_for(view._formation, view._procedure_objects)]
    view._refresh_instances()

    assert set(_instance_labels(view)) == {
        "Cell A - Formation Dataset",
        "Cell B - Formation Dataset",
    }
    assert len(view._resolve_traces()) == 2

    # Uncheck the first matching instance -> dropped from the plotted traces.
    idx = view._matching_tests()[0]["idx"]
    view._on_instance_toggle(idx, False)
    assert len(view._resolve_traces()) == 1


def test_toggle_state_persists_across_refresh(view):
    select(view, cell=view._cell_a, proc=view._formation)

    idx = view._matching_tests()[0]["idx"]
    view._on_instance_toggle(idx, False)

    # A subsequent refresh (e.g. another tree change) keeps the instance
    # unchecked rather than resetting it to the default checked state.
    view._refresh_instances()
    cb = next(w for w in view._instances_col if isinstance(w, Checkbox))
    assert cb.value is False
    assert view._resolve_traces() == []
