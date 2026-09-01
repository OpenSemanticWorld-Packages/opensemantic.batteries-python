"""Lazy battery dashboard over an in-memory test-double backend.

Mirrors ``battery_dashboard_OSL.py`` but with a tiny hand-built
:class:`~opensemantic.batteries.view.BatteryDataBackend` instead of a live OSL
wiki — so the lazy trees / cascade / instances behaviour can be driven (and
reproduced in a browser) without credentials or network.

Run with:
    panel serve examples/battery_dashboard_lazy.py --dev

Hierarchy:
    Cells:       BatteryCell -> {CylindricalCell -> [Cell A, Cell B],
                                 PrismaticCell   -> [Cell C]}
    Procedures:  ElectrochemicalTestProcedure -> {AgingTestProcedure   -> [Aging A],
                                                  FormationTestProcedure -> [Formation]}
"""

from __future__ import annotations

from typing import Dict, List, Tuple

import panel as pn

from opensemantic.batteries.view import (
    BatteryDataBackend,
    Dataset,
    LazyBatteryDataView,
    TreeNode,
)
from opensemantic.characteristics.quantitative.v1 import (
    Characteristic,
    ElectricCurrent,
    Time,
    Voltage,
)

pn.extension()


class Row(Characteristic):
    test_time: Time = None
    voltage: Voltage = None
    current: ElectricCurrent = None


def _rows() -> List[Row]:
    return [
        Row(
            test_time=Time(value=float(i)),
            voltage=Voltage(value=3.0 + 0.1 * i),
            current=ElectricCurrent(value=0.5 * (i % 2)),
        )
        for i in range(5)
    ]


class MemoryBatteryBackend(BatteryDataBackend):
    """A fixed, in-memory hierarchy + dataset table for reproducing the view."""

    # category / instance iri -> (label, kind, [child iris])
    _NODES: Dict[str, Tuple[str, str, List[str]]] = {
        # cells
        "Category:BatteryCell": (
            "BatteryCell",
            "class",
            ["Category:CylindricalCell", "Category:PrismaticCell"],
        ),
        "Category:CylindricalCell": (
            "CylindricalCell",
            "class",
            ["Item:CellA", "Item:CellB"],
        ),
        "Category:PrismaticCell": ("PrismaticCell", "class", ["Item:CellC"]),
        "Item:CellA": ("Cell A", "instance", []),
        "Item:CellB": ("Cell B", "instance", []),
        "Item:CellC": ("Cell C", "instance", []),
        # procedures
        "Category:ElectrochemicalTestProcedure": (
            "ElectrochemicalTestProcedure",
            "class",
            ["Category:AgingTestProcedure", "Category:FormationTestProcedure"],
        ),
        "Category:AgingTestProcedure": (
            "AgingTestProcedure",
            "class",
            ["Item:AgingA"],
        ),
        "Category:FormationTestProcedure": (
            "FormationTestProcedure",
            "class",
            ["Item:Formation"],
        ),
        "Item:AgingA": ("Aging A", "instance", []),
        "Item:Formation": ("Formation", "instance", []),
    }

    # (cell iri, proc iri) that actually have a dataset
    _DATASETS = {
        ("Item:CellA", "Item:AgingA"),
        ("Item:CellA", "Item:Formation"),
        ("Item:CellB", "Item:Formation"),
        ("Item:CellC", "Item:Formation"),
    }

    @property
    def row_class(self) -> type:
        return Row

    @property
    def cell_root(self) -> TreeNode:
        return self._node("Category:BatteryCell")

    @property
    def procedure_root(self) -> TreeNode:
        return self._node("Category:ElectrochemicalTestProcedure")

    def _node(self, iri: str) -> TreeNode:
        label, kind, _ = self._NODES[iri]
        return TreeNode(iri=iri, label=label, kind=kind)

    def children(self, category_iri: str) -> List[TreeNode]:
        _, _, kids = self._NODES.get(category_iri, ("", "", []))
        return [self._node(k) for k in kids]

    def datasets(
        self, cell_iris: List[str], proc_iris: List[str]
    ) -> List[Dataset]:
        out: List[Dataset] = []
        for c in cell_iris:
            for p in proc_iris:
                if (c, p) in self._DATASETS:
                    out.append(
                        Dataset(
                            id=f"{c}|{p}",
                            label=f"{self.page_name(c)} - {self.page_name(p)}",
                            rows=_rows(),
                        )
                    )
        return out

    def page_name(self, iri: str) -> str:
        label, _, _ = self._NODES.get(iri, (iri, "", []))
        return label


view = LazyBatteryDataView(
    backend=MemoryBatteryBackend(),
    title="Battery Cycling Dashboard — In-memory",
)
view.servable()
