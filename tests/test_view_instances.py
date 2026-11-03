"""Tests for the BatteryDataView "Instances" sidebar card.

The card lists the test runs whose cell *and* procedure are both selected as a
flat :class:`Wunderbaum` — one checkable leaf per instance; unchecking one drops
it from the plot. These tests drive the view in ``embeddable=True`` mode (no
browser / Panelini app) and poke the selection/toggle handlers the tree widgets
would otherwise call — for the instances tree, by writing its ``source`` param
the way the browser echoes it on a checkbox toggle (``selected`` omitted when
unchecked). Selection is read from ``source`` and **not** from per-node
``select`` events, which coalesce away on a click (see the view README).

Skipped as a group when the ``[view]`` extra is not installed.
"""

from __future__ import annotations

from typing import List

import pytest

panel = pytest.importorskip("panel")
pytest.importorskip("panelini")

from _view_helpers import make_view, node_id_for, select  # noqa: E402


@pytest.fixture
def view():
    return make_view()


def _instance_labels(view) -> List[str]:
    """Leaf titles in the instances tree (``[]`` when it shows the placeholder)."""
    tree = view._instances_tree
    if tree is None:
        return []
    return [n["title"] for n in tree.source]


def _toggle_instance(view, idx: int, flag: bool) -> None:
    """Echo the instances tree's ``source`` after (un)checking one leaf.

    Mirrors ``getSerializableSource``: every leaf is re-emitted, with ``selected``
    present only when checked (omitted when unchecked) — the same shape the
    browser sends and :meth:`_on_instance_source_change` diffs.
    """
    new_source = []
    for node in view._instances_tree.source:
        n = {k: v for k, v in node.items() if k != "selected"}
        node_idx = view._instance_idx(node)
        checked = flag if node_idx == idx else bool(node.get("selected"))
        if checked:
            n["selected"] = True
        new_source.append(n)
    view._instances_tree.source = new_source


def test_no_selection_lists_nothing(view):
    assert view._matching_tests() == []
    assert _instance_labels(view) == []  # placeholder prompt, no tree
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
    _toggle_instance(view, idx, False)
    assert len(view._resolve_traces()) == 1


def test_toggle_state_persists_across_refresh(view):
    select(view, cell=view._cell_a, proc=view._formation)

    idx = view._matching_tests()[0]["idx"]
    _toggle_instance(view, idx, False)

    # A subsequent refresh (e.g. another tree change) keeps the instance
    # unchecked rather than resetting it to the default checked state.
    view._refresh_instances()
    node = view._instances_tree.source[0]
    assert node["selected"] is False
    assert view._resolve_traces() == []
