"""Tests for :class:`LazyBatteryDataView` selection over a lazy backend.

These drive selection the way the browser actually does: by writing the
Wunderbaum widget's ``source`` param (the full serialised node tree, echoed back
whenever a checkbox toggles). Selection is deliberately **not** read from the
widget's per-node ``select`` events — those arrive through a single
``_event_data`` slot and a checkbox click fires click→deactivate→select→activate
in one JS tick, so Bokeh coalesces the writes and only the last reaches Python,
dropping the ``select``. ``source`` is full-state and survives coalescing; these
tests pin that transport so the regression can't silently return.

Skipped as a group when the ``[view]`` extra is not installed.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import pytest

pytest.importorskip("panel")
pytest.importorskip("panelini")

from _lazy_backend import MemoryBatteryBackend  # noqa: E402

from opensemantic.batteries.view import LazyBatteryDataView  # noqa: E402


def _cat(key: str, title: str, *, selected: bool = False, children=None) -> Dict:
    """A serialised *class* node as ``getSerializableSource`` would emit it."""
    node: Dict = {"key": key, "title": title, "kind": "class", "lazy": True}
    if selected:
        node["selected"] = True
    if children is not None:
        node["expanded"] = True
        node["children"] = children
    return node


def _inst(key: str, title: str, *, selected: bool = False) -> Dict:
    node: Dict = {"key": key, "title": title, "kind": "instance"}
    if selected:
        node["selected"] = True
    return node


def _tick_cell_category(view: LazyBatteryDataView, *, selected: bool) -> None:
    """Mimic the browser echoing ``source`` after (un)ticking CylindricalCell."""
    view._cell_tree.source = [
        _cat(
            "Category:BatteryCell",
            "BatteryCell",
            children=[
                _cat("Category:CylindricalCell", "CylindricalCell", selected=selected),
                _cat("Category:PrismaticCell", "PrismaticCell"),
            ],
        )
    ]


def _tick_formation(view: LazyBatteryDataView, *, selected: bool) -> None:
    view._proc_tree.source = [
        _cat(
            "Category:ElectrochemicalTestProcedure",
            "ElectrochemicalTestProcedure",
            children=[
                _cat("Category:AgingTestProcedure", "AgingTestProcedure"),
                _cat(
                    "Category:FormationTestProcedure",
                    "FormationTestProcedure",
                    selected=selected,
                ),
            ],
        )
    ]


@pytest.fixture
def view() -> LazyBatteryDataView:
    return LazyBatteryDataView(backend=MemoryBatteryBackend())


def _instance_labels(view) -> List[str]:
    tree = view._instances_tree
    return [] if tree is None else [n["title"] for n in tree.source]


def test_category_toggle_cascades_to_descendant_instances(view):
    _tick_cell_category(view, selected=True)
    # Ticking the CylindricalCell category selects every instance below it,
    # even though the browser never loaded that subtree.
    assert view._sel_instances["cell"] == {"Item:CellA", "Item:CellB"}
    assert "Category:CylindricalCell" in view._sel_classes["cell"]


def test_untick_category_clears_its_instances(view):
    _tick_cell_category(view, selected=True)
    _tick_cell_category(view, selected=False)
    assert view._sel_instances["cell"] == set()


def test_selection_yields_matching_datasets_and_instances_tree(view):
    _tick_cell_category(view, selected=True)  # Cell A + Cell B
    _tick_formation(view, selected=True)  # Formation

    labels = sorted(m["label"] for m in view._matching_tests())
    assert labels == ["Cell A - Formation", "Cell B - Formation"]
    # The Instances card now shows a populated tree (both matches, both checked).
    assert sorted(_instance_labels(view)) == [
        "Cell A - Formation",
        "Cell B - Formation",
    ]
    assert len(view._resolve_traces()) == 2


def test_instance_level_toggle_via_source(view):
    # Expand CylindricalCell in the browser, then tick only Cell A.
    view._cell_tree.source = [
        _cat(
            "Category:BatteryCell",
            "BatteryCell",
            children=[
                _cat(
                    "Category:CylindricalCell",
                    "CylindricalCell",
                    children=[
                        _inst("Item:CellA", "Cell A", selected=True),
                        _inst("Item:CellB", "Cell B"),
                    ],
                ),
                _cat("Category:PrismaticCell", "PrismaticCell"),
            ],
        )
    ]
    _tick_formation(view, selected=True)

    assert view._sel_instances["cell"] == {"Item:CellA"}
    assert [m["label"] for m in view._matching_tests()] == ["Cell A - Formation"]


def test_category_select_issues_expand_and_select_batch(view):
    # Ticking a category must push a single atomic batch to the browser that
    # both expands the subtree (so the cascade becomes visible) and ticks every
    # descendant — issuing these as separate actions would coalesce to one.
    _tick_cell_category(view, selected=True)
    action = view._cell_tree._tree_action
    assert action.get("action") == "batch"
    ops = action["payload"]
    assert {"action": "expandNode", "key": "Category:CylindricalCell",
            "expanded": True} in ops
    assert {"action": "selectNode", "key": "Item:CellA", "selected": True} in ops
    assert {"action": "selectNode", "key": "Item:CellB", "selected": True} in ops


def test_no_selection_lists_nothing(view):
    assert view._matching_tests() == []
    assert _instance_labels(view) == []
    assert view._resolve_traces() == []


def test_unit_selection_actually_converts_the_numbers(view):
    # Regression: selecting a different unit must convert the plotted values,
    # not just relabel the axis. The rows are v1 QuantityValues; converting via
    # the channel-resolved (v2) enum silently no-ops (UndefinedUnitError), so
    # this pins that the value's *own* enum is used. voltage rows are 3.0 V.
    _tick_cell_category(view, selected=True)
    _tick_formation(view, selected=True)
    traces = view._resolve_traces()
    assert traces, "expected at least one plotted trace"

    view._unit_selections["voltage"] = "volt"
    volts = view._get_vals(traces, "voltage")[0]
    view._unit_selections["voltage"] = "milli_volt"
    millivolts = view._get_vals(traces, "voltage")[0]

    assert volts[0] == pytest.approx(3.0)
    assert millivolts[0] == pytest.approx(3000.0)
    assert millivolts == [pytest.approx(v * 1000.0) for v in volts]


def test_scalar_in_unit_converts_values_lacking_to_unit():
    # Wiki-loaded rows are ``osw.model.entity.*`` objects: they carry a unit enum
    # but NO ``to_unit`` method (and are not dicts), so the converter must
    # re-wrap them in the field's characteristic class to convert. Regression for
    # "picoV relabels the axis but leaves the numbers as volts".
    from opensemantic.batteries.view import LazyBatteryDataView  # noqa: F401
    from opensemantic.batteries.view._battery_dashboard import BatteryDataView
    from opensemantic.characteristics.quantitative.v1 import Voltage, VoltageUnit

    class OswStyleValue:  # no to_unit, has .value + enum .unit — like osw's model
        def __init__(self, value, unit):
            self.value = value
            self.unit = unit

    v = OswStyleValue(3.0, VoltageUnit.volt)
    assert not hasattr(v, "to_unit")
    assert BatteryDataView._scalar_in_unit(v, "volt", Voltage) == pytest.approx(3.0)
    assert BatteryDataView._scalar_in_unit(v, "milli_volt", Voltage) == pytest.approx(3000.0)
    assert BatteryDataView._scalar_in_unit(v, "kilo_volt", Voltage) == pytest.approx(0.003)
