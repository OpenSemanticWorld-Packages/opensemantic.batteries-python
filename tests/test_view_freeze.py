"""Tests for the BatteryDataView freeze / unfreeze / delete feature.

Every plot — the live *active* one and each frozen snapshot — is one record in
a single ordered ``view._plots`` list; exactly one record carries
``active=True``. Freezing drops a static snapshot of the active plot directly
below it (the active plot stays put). Unfreezing swaps roles *in place*: the
clicked snapshot becomes active where it sits and the previously-active plot
freezes where it sits — nothing is reordered. Deleting the active plot promotes
a neighbour. Driven in ``embeddable=True`` mode (no browser).

Skipped as a group when the ``[view]`` extra is not installed.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

panel = pytest.importorskip("panel")
pytest.importorskip("panelini")

from _view_helpers import make_view, select  # noqa: E402

Button = panel.widgets.Button


@pytest.fixture
def view():
    return make_view()


def _active_has_figure(view) -> bool:
    return view._make_figure() is not None


def _frozen(view) -> List[Dict[str, Any]]:
    return [r for r in view._plots if not r["active"]]


def _button_labels(record: Dict[str, Any]) -> List[str]:
    toolbar = record["panel"][0]
    return [w.name for w in toolbar if isinstance(w, Button)]


def _freeze_button(record: Dict[str, Any]) -> Button:
    toolbar = record["panel"][0]
    return next(w for w in toolbar if isinstance(w, Button))


# -- Layout / initial state --------------------------------------------------


def test_plot_card_holds_the_unified_plots_column(view):
    card = list(view._plot_card)
    assert card == [view._plots_col]
    # Exactly one plot to begin with, and it is the active one.
    assert len(view._plots) == 1
    assert view._plots[0]["active"] is True
    assert view._active_record() is view._plots[0]


def test_active_plot_has_blue_freeze_and_delete_buttons(view):
    active = view._active_record()
    labels = _button_labels(active)
    assert any("Freeze" in name for name in labels)
    assert any("Delete" in name for name in labels)
    # The Freeze button is styled blue (primary) to mark the active plot.
    assert _freeze_button(active).button_type == "primary"


# -- Freeze ------------------------------------------------------------------


def test_freeze_inserts_snapshot_directly_below_active(view):
    select(view, cell=view._cell_a, proc=view._formation)
    assert _active_has_figure(view)
    assert _frozen(view) == []

    view._on_freeze_click()

    # Active plot stays active, in place at index 0; the snapshot lands at 1.
    assert view._plots[0]["active"] is True
    assert view._plots[1]["active"] is False
    assert len(view._plots) == 2
    assert "Cell A" in view._state_summary(view._plots[1]["state"])
    # Active plot still driven by the live selection.
    assert _active_has_figure(view)
    assert len(view._resolve_traces()) == 1


def test_freeze_moves_snapshot_down_one_slot_only(view):
    select(view, cell=view._cell_a, proc=view._formation)
    view._on_freeze_click()
    select(view, cell=view._cell_b, proc=view._formation)
    view._on_freeze_click()

    # Newest snapshot (Cell B) sits directly under the active plot (index 1),
    # older snapshot (Cell A) is pushed one further down (index 2).
    assert view._plots[0]["active"] is True
    assert "Cell B" in view._state_summary(view._plots[1]["state"])
    assert "Cell A" in view._state_summary(view._plots[2]["state"])


def test_frozen_panel_has_unfreeze_and_delete_buttons(view):
    select(view, cell=view._cell_a, proc=view._formation)
    view._on_freeze_click()

    labels = _button_labels(view._plots[1])
    assert "Unfreeze" in labels
    assert any("Delete" in name for name in labels)


def test_freeze_with_nothing_plotted_is_noop(view):
    view._on_freeze_click()
    assert len(view._plots) == 1
    assert _frozen(view) == []


def test_changing_selection_after_freeze_only_affects_active(view):
    select(view, cell=view._cell_a, proc=view._formation)
    view._on_freeze_click()
    frozen_state = view._plots[1]["state"].model_copy(deep=True)

    select(view, cell=view._cell_b, proc=view._formation)
    assert [t["label"] for t in view._resolve_traces()] == ["Cell B — Formation Test"]

    # The frozen snapshot (a PlotState) is unchanged.
    assert view._plots[1]["state"] == frozen_state
    assert len(_frozen(view)) == 1


def test_captured_state_is_a_plotstate(view):
    from opensemantic.batteries.view._battery_dashboard import PlotState

    select(view, cell=view._cell_a, proc=view._formation)
    view._on_freeze_click()

    state = view._plots[1]["state"]
    assert isinstance(state, PlotState)
    cell_a_id = next(k for k, o in view._cell_objects.items() if o is view._cell_a)
    assert state.cell_ids == [cell_a_id]


# -- Unfreeze ----------------------------------------------------------------


def test_unfreeze_makes_clicked_plot_active_and_freezes_old_active(view):
    select(view, cell=view._cell_a, proc=view._formation)
    view._on_freeze_click()
    select(view, cell=view._cell_b, proc=view._formation)

    record = view._plots[1]            # frozen Cell A snapshot
    view._on_unfreeze_click(record)

    # Sidebar jumped to the unfrozen (Cell A) selection.
    assert [t["label"] for t in view._resolve_traces()] == ["Cell A — Formation Test"]
    assert view._selected_cell_ids == [
        next(k for k, o in view._cell_objects.items() if o is view._cell_a)
    ]
    # The clicked plot is now the active one; the previously-active plot (Cell B)
    # is now frozen. Count of frozen plots stays 1.
    assert record["active"] is True
    assert len(_frozen(view)) == 1
    assert "Cell B" in view._state_summary(_frozen(view)[0]["state"])


def test_unfreeze_button_toggles_freeze_and_unfreeze(view):
    select(view, cell=view._cell_a, proc=view._formation)
    view._on_freeze_click()
    select(view, cell=view._cell_b, proc=view._formation)

    frozen_rec = view._plots[1]
    active_rec = view._plots[0]
    view._on_unfreeze_click(frozen_rec)

    # The unfrozen plot now shows a blue Freeze button (it is active).
    assert "❄ Freeze plot" in _button_labels(frozen_rec)
    assert _freeze_button(frozen_rec).button_type == "primary"
    # The previously-active plot now shows an Unfreeze button (it is frozen).
    assert "Unfreeze" in _button_labels(active_rec)


def test_unfreeze_swaps_in_place_without_reordering(view):
    # Two snapshots below the active plot: order (top->bottom) B, A.
    select(view, cell=view._cell_a, proc=view._formation)
    view._on_freeze_click()
    select(view, cell=view._cell_b, proc=view._formation)
    view._on_freeze_click()
    # Move active to a third distinct selection (Cell A + Aging Test A).
    select(view, cell=view._cell_a, proc=view._aging_a)
    # Stack now: [active(A/Aging), frozen(B), frozen(A/Formation)]
    assert view._plots[0]["active"] is True

    bottom = view._plots[2]            # frozen Cell A + Formation
    view._on_unfreeze_click(bottom)

    # Active jumped to the unfrozen selection.
    assert [t["label"] for t in view._resolve_traces()] == ["Cell A — Formation Test"]
    # Still three plots, one active; nothing was reordered.
    assert len(view._plots) == 3
    # The unfrozen plot is active *in its own slot* (index 2) — it did NOT move
    # to the top.
    assert view._plots[2] is bottom
    assert view._plots[2]["active"] is True
    # The previously-active plot (Cell A + Aging) froze in place at index 0.
    assert view._plots[0]["active"] is False
    assert "Aging Test A" in view._state_summary(view._plots[0]["state"])
    # The middle snapshot (Cell B) is undisturbed.
    assert "Cell B" in view._state_summary(view._plots[1]["state"])


def test_unfreeze_restores_tree_widget_state(view):
    from opensemantic.batteries.view._battery_utils import get_checked_instance_ids

    select(view, cell=view._cell_a, proc=view._formation)
    view._on_freeze_click()
    select(view, cell=view._cell_b, proc=view._formation)

    view._on_unfreeze_click(view._plots[1])

    checked = get_checked_instance_ids(view._cell_tree.source)
    cell_a_id = next(k for k, o in view._cell_objects.items() if o is view._cell_a)
    assert checked == [cell_a_id]


def test_unfreeze_does_not_deepcopy_live_tree_source(view):
    # Regression: once the browser has edited a Wunderbaum, its live ``source``
    # holds unpicklable live references (deepcopy would drag in a Tornado
    # IOLoop). Restore must rebuild from the pristine plain-data source, never
    # from ``tree.source``. Poison the live sources so any deepcopy of them
    # raises, then confirm unfreeze still succeeds.
    class _Undeepcopyable:
        def __deepcopy__(self, memo):
            raise RuntimeError("live tree.source must not be deep-copied")

    select(view, cell=view._cell_a, proc=view._formation)
    view._on_freeze_click()
    select(view, cell=view._cell_b, proc=view._formation)

    poison = [{"data": {"kind": "instance", "node_id": "x"},
               "selected": True, "live": _Undeepcopyable()}]
    view._cell_tree.source = poison
    view._proc_tree.source = poison

    # Must not raise.
    view._on_unfreeze_click(view._plots[1])

    assert [t["label"] for t in view._resolve_traces()] == ["Cell A — Formation Test"]


def test_restore_rebuilds_tree_widget_in_place(view):
    select(view, cell=view._cell_a, proc=view._formation)
    old_tree = view._cell_tree
    view._on_freeze_click()
    select(view, cell=view._cell_b, proc=view._formation)

    view._on_unfreeze_click(view._plots[1])

    assert view._cell_tree is not old_tree          # a fresh widget
    assert view._cell_card[0] is view._cell_tree     # swapped into the card


def test_unfreeze_restores_axis_and_unit_widgets(view):
    select(view, cell=view._cell_a, proc=view._formation)

    view._on_checkbox_change("current", "y2", True)
    assert view._axis_map["y2"] == "current"
    assert view._axis_checkboxes[("current", "y2")].value is True

    voltage_sel = view._unit_selects.get("voltage")
    alt_unit = None
    if voltage_sel is not None:
        alt_unit = next(
            (v for v in voltage_sel.options.values() if v != voltage_sel.value),
            None,
        )
        if alt_unit is not None:
            voltage_sel.value = alt_unit  # fires _on_unit_change
            assert view._unit_selections["voltage"] == alt_unit

    view._on_freeze_click()

    # Change axis + unit on the (still) active plot.
    view._on_checkbox_change("current", "y2", False)
    assert view._axis_map["y2"] is None
    if alt_unit is not None:
        default_unit = next(
            v for v in voltage_sel.options.values() if v != alt_unit
        )
        voltage_sel.value = default_unit

    # Unfreeze -> axis + unit widgets jump back to the frozen state.
    view._on_unfreeze_click(view._plots[1])

    assert view._axis_map["y2"] == "current"
    assert view._axis_checkboxes[("current", "y2")].value is True
    if alt_unit is not None:
        assert view._unit_selections["voltage"] == alt_unit
        assert voltage_sel.value == alt_unit


# -- Delete ------------------------------------------------------------------


def test_delete_frozen_removes_only_that_snapshot(view):
    select(view, cell=view._cell_a, proc=view._formation)
    view._on_freeze_click()
    select(view, cell=view._cell_b, proc=view._formation)
    view._on_freeze_click()
    assert len(_frozen(view)) == 2

    # Delete the older snapshot (Cell A, bottom).
    target = view._plots[2]
    assert "Cell A" in view._state_summary(target["state"])
    view._on_delete_click(target)

    assert target not in view._plots
    assert len(_frozen(view)) == 1
    # The active plot (Cell B) is untouched.
    assert [t["label"] for t in view._resolve_traces()] == ["Cell B — Formation Test"]


def test_delete_active_with_no_frozen_clears_plot(view):
    select(view, cell=view._cell_a, proc=view._formation)
    assert _active_has_figure(view)

    view._on_delete_click(view._active_record())

    # A fresh empty active plot replaces it.
    assert len(view._plots) == 1
    assert view._plots[0]["active"] is True
    assert view._selected_cell_ids == []
    assert view._selected_proc_ids == []
    assert view._resolve_traces() == []
    assert not _active_has_figure(view)


def test_delete_active_promotes_neighbour(view):
    select(view, cell=view._cell_a, proc=view._formation)
    view._on_freeze_click()
    select(view, cell=view._cell_b, proc=view._formation)
    assert [t["label"] for t in view._resolve_traces()] == ["Cell B — Formation Test"]
    assert len(_frozen(view)) == 1

    view._on_delete_click(view._active_record())

    # Active (Cell B) is discarded; the frozen Cell A snapshot is promoted.
    assert [t["label"] for t in view._resolve_traces()] == ["Cell A — Formation Test"]
    assert len(view._plots) == 1
    assert view._plots[0]["active"] is True
    assert _active_has_figure(view)
