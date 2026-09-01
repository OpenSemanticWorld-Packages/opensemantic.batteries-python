"""In-memory :class:`BatteryDataBackend` test double for the lazy view tests.

Not a test module (no ``test_`` prefix). A tiny fixed hierarchy + dataset table
so :class:`LazyBatteryDataView` can be exercised without a live OSL wiki:

    Cells:       BatteryCell -> {CylindricalCell -> [Cell A, Cell B],
                                 PrismaticCell   -> [Cell C]}
    Procedures:  ElectrochemicalTestProcedure -> {AgingTestProcedure    -> [Aging A],
                                                   FormationTestProcedure -> [Formation]}
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from opensemantic.batteries.view import BatteryDataBackend, Dataset, TreeNode
from opensemantic.characteristics.quantitative.v1 import (
    Characteristic,
    ElectricCurrent,
    Time,
    Voltage,
)


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
        for i in range(3)
    ]


class MemoryBatteryBackend(BatteryDataBackend):
    _NODES: Dict[str, Tuple[str, str, List[str]]] = {
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
        "Category:ElectrochemicalTestProcedure": (
            "ElectrochemicalTestProcedure",
            "class",
            ["Category:AgingTestProcedure", "Category:FormationTestProcedure"],
        ),
        "Category:AgingTestProcedure": ("AgingTestProcedure", "class", ["Item:AgingA"]),
        "Category:FormationTestProcedure": (
            "FormationTestProcedure",
            "class",
            ["Item:Formation"],
        ),
        "Item:AgingA": ("Aging A", "instance", []),
        "Item:Formation": ("Formation", "instance", []),
    }

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

    def datasets(self, cell_iris: List[str], proc_iris: List[str]) -> List[Dataset]:
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
