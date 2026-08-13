"""Shared builders for the BatteryDataView tests.

Not a test module (no ``test_`` prefix, so pytest won't collect it). Test
modules import from here *after* their own ``importorskip`` guards, so the view
imports below only run when the ``[view]`` extra is present.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from opensemantic.batteries.v1 import (
    AgingTestProcedure,
    BatteryCell,
    ElectrochemicalTestProcedure,
    FormationTestProcedure,
)
from opensemantic.batteries.view import (
    BatteryDataView,
    OOLDTreeBuilder,
    PythonSource,
    has_type,
)
from opensemantic.characteristics.quantitative.v1 import (
    Characteristic,
    ElectricCurrent,
    Time,
    Voltage,
)
from opensemantic.core.v1 import Item, Label
from opensemantic.lab.v1 import AnalyticalLaboratoryProcess


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


def make_view() -> BatteryDataView:
    """A BatteryDataView over: Cell A (Aging A, Formation) and Cell B (Formation).

    The source objects are attached to the view as ``_cell_a`` / ``_cell_b`` /
    ``_aging_a`` / ``_formation`` for convenient lookup in tests.
    """
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
    v._cell_a, v._cell_b = cell_a, cell_b
    v._aging_a, v._formation = aging_a, formation
    return v


def node_id_for(obj: Any, mapping: Dict[str, Any]) -> str:
    """Return the tree node_id whose mapped object is *obj*."""
    return next(k for k, o in mapping.items() if o is obj)


def select(view: BatteryDataView, *, cell: Any, proc: Any) -> None:
    """Set the view's cell+proc selection and refresh instances + plot."""
    view._selected_cell_ids = [node_id_for(cell, view._cell_objects)]
    view._selected_proc_ids = [node_id_for(proc, view._procedure_objects)]
    view._refresh_instances()
    view._build_figure()
