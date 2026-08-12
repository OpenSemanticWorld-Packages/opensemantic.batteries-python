"""Tests for the BatteryDataView "Instances" sidebar card.

The card lists the test runs whose cell *and* procedure are both selected, with
one checkbox per instance; unchecking one drops it from the plot. These tests
drive the view in ``embeddable=True`` mode (no browser / Panelini app) and poke
the selection/toggle handlers the tree widgets would otherwise call.

Skipped as a group when the ``[view]`` extra is not installed.
"""

from __future__ import annotations

from typing import List, Optional

import pytest

panel = pytest.importorskip("panel")
pytest.importorskip("panelini")

Checkbox = panel.widgets.Checkbox

from opensemantic.batteries.v1 import (  # noqa: E402
    AgingTestProcedure,
    BatteryCell,
    ElectrochemicalTestProcedure,
    FormationTestProcedure,
)
from opensemantic.batteries.view import (  # noqa: E402
    BatteryDataView,
    OOLDTreeBuilder,
    PythonSource,
    has_type,
)
from opensemantic.characteristics.quantitative.v1 import (  # noqa: E402
    Characteristic,
    ElectricCurrent,
    Time,
    Voltage,
)
from opensemantic.core.v1 import Item, Label  # noqa: E402
from opensemantic.lab.v1 import AnalyticalLaboratoryProcess  # noqa: E402


# --- Minimal example schema (mirrors examples/battery_example_data.py) -------


class _Row(Characteristic):
    test_time: Time = None
    voltage: Voltage = None
    current: ElectricCurrent = None


class _Dataset(Item):
    data: List[_Row] = []


class _Test(AnalyticalLaboratoryProcess):
    protocol: Optional[ElectrochemicalTestProcedure] = None
    output: Optional[_Dataset] = None


def _dataset(name: str) -> _Dataset:
    return _Dataset(
        label=[Label(text=name)],
        data=[
            _Row(
                test_time=Time(value=0.0),
                voltage=Voltage(value=3.0),
                current=ElectricCurrent(value=0.0),
            ),
            _Row(
                test_time=Time(value=1.0),
                voltage=Voltage(value=3.1),
                current=ElectricCurrent(value=0.5),
            ),
        ],
    )


@pytest.fixture
def view():
    """A BatteryDataView over: Cell A (Aging A, Formation) and Cell B (Formation)."""
    cell_a = BatteryCell(label=[Label(text="Cell A")])
    cell_b = BatteryCell(label=[Label(text="Cell B")])
    aging_a = AgingTestProcedure(label=[Label(text="Aging Test A")])
    formation = FormationTestProcedure(label=[Label(text="Formation Test")])

    tests = [
        _Test(
            label=[Label(text="Cell A - Aging (A)")],
            device_under_test=[cell_a],
            protocol=aging_a,
            output=_dataset("Cell A - Aging (A) Dataset"),
        ),
        _Test(
            label=[Label(text="Cell A - Formation")],
            device_under_test=[cell_a],
            protocol=formation,
            output=_dataset("Cell A - Formation Dataset"),
        ),
        _Test(
            label=[Label(text="Cell B - Formation")],
            device_under_test=[cell_b],
            protocol=formation,
            output=_dataset("Cell B - Formation Dataset"),
        ),
    ]

    cell_builder = OOLDTreeBuilder(
        source=PythonSource([cell_a, cell_b]),
        relations=[has_type()],
        ceiling=BatteryCell,
    )
    proc_builder = OOLDTreeBuilder(
        source=PythonSource([aging_a, formation]),
        relations=[has_type()],
        ceiling=ElectrochemicalTestProcedure,
    )

    v = BatteryDataView(
        tests=tests,
        cell_nodes=cell_builder.build_nodes(),
        cell_edges=cell_builder.build_edges(),
        cell_objects=cell_builder.get_object_map(),
        procedure_nodes=proc_builder.build_nodes(),
        procedure_edges=proc_builder.build_edges(),
        procedure_objects=proc_builder.get_object_map(),
        embeddable=True,
    )
    # Attach the source objects for the tests to reference.
    v._cell_a, v._cell_b = cell_a, cell_b
    v._aging_a, v._formation = aging_a, formation
    return v


def _select(view, obj, mapping) -> str:
    """Return the node_id whose mapped object is *obj*."""
    return next(k for k, o in mapping.items() if o is obj)


def _instance_labels(view) -> List[str]:
    return [w.name for w in view._instances_col if isinstance(w, Checkbox)]


def test_no_selection_lists_nothing(view):
    assert view._matching_tests() == []
    assert _instance_labels(view) == []  # only a placeholder pane, no checkboxes
    assert view._resolve_traces() == []


def test_selection_lists_matching_instances(view):
    cell_key = _select(view, view._cell_a, view._cell_objects)
    proc_key = _select(view, view._formation, view._procedure_objects)
    view._selected_cell_ids = [cell_key]
    view._selected_proc_ids = [proc_key]
    view._refresh_instances()

    # Cell A + Formation matches exactly one test run.
    assert _instance_labels(view) == ["Cell A - Formation Dataset"]
    assert len(view._resolve_traces()) == 1


def test_unchecking_instance_removes_it_from_plot(view):
    # Select Cell A + Cell B and Formation -> two matching instances.
    cell_a_key = _select(view, view._cell_a, view._cell_objects)
    cell_b_key = _select(view, view._cell_b, view._cell_objects)
    proc_key = _select(view, view._formation, view._procedure_objects)
    view._selected_cell_ids = [cell_a_key, cell_b_key]
    view._selected_proc_ids = [proc_key]
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
    cell_key = _select(view, view._cell_a, view._cell_objects)
    proc_key = _select(view, view._formation, view._procedure_objects)
    view._selected_cell_ids = [cell_key]
    view._selected_proc_ids = [proc_key]
    view._refresh_instances()

    idx = view._matching_tests()[0]["idx"]
    view._on_instance_toggle(idx, False)

    # A subsequent refresh (e.g. another tree change) keeps the instance
    # unchecked rather than resetting it to the default checked state.
    view._refresh_instances()
    cb = next(w for w in view._instances_col if isinstance(w, Checkbox))
    assert cb.value is False
    assert view._resolve_traces() == []
