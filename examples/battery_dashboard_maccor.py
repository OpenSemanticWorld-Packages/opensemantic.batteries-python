"""Lazy battery dashboard over real Maccor cycler files.

Same behaviour as ``battery_dashboard_lazy.py`` (lazy trees, cascade,
instances), but each (cell, procedure) pair plots a real Maccor export read
with ``read_maccor`` instead of synthetic rows. No credentials or network are
needed, so this is the local development environment for
``battery_dashboard_OSL.py``.

Run with:
    panel serve examples/battery_dashboard_maccor.py --dev

Hierarchy:
    Cells:       BatteryCell -> CylindricalCell -> [Cell A, Cell B]
    Procedures:  ElectrochemicalTestProcedure -> AgingTestProcedure
                     -> [Aging A, Aging B]

Each (cell, procedure) pair has its own Maccor file,
``tests/data/cycling/maccor/cell<A|B>_aging<A|B>_mims_client1.txt``. Each
file is read once, the first time a selection needs it.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Tuple

import panel as pn

from opensemantic.batteries import StepTypedCyclingDataRow, read_maccor
from opensemantic.batteries.view import (
    BatteryDataBackend,
    Dataset,
    LazyBatteryDataView,
    TreeNode,
)

pn.extension()

_MACCOR_DIR = Path(__file__).parent.parent / "tests" / "data" / "cycling" / "maccor"


@lru_cache(maxsize=None)
def _rows(path: Path) -> List[StepTypedCyclingDataRow]:
    """Read a Maccor file; its rows are already ``StepTypedCyclingDataRow``."""
    return read_maccor(path, fmt="mims_client1").data


class MaccorBatteryBackend(BatteryDataBackend):
    """A fixed hierarchy with one Maccor file per (cell, procedure) pair."""

    # category / instance iri -> (label, kind, [child iris])
    _NODES: Dict[str, Tuple[str, str, List[str]]] = {
        # cells
        "Category:BatteryCell": (
            "BatteryCell",
            "class",
            ["Category:CylindricalCell"],
        ),
        "Category:CylindricalCell": (
            "CylindricalCell",
            "class",
            ["Item:CellA", "Item:CellB"],
        ),
        "Item:CellA": ("Cell A", "instance", []),
        "Item:CellB": ("Cell B", "instance", []),
        # procedures
        "Category:ElectrochemicalTestProcedure": (
            "ElectrochemicalTestProcedure",
            "class",
            ["Category:AgingTestProcedure"],
        ),
        "Category:AgingTestProcedure": (
            "AgingTestProcedure",
            "class",
            ["Item:AgingA", "Item:AgingB"],
        ),
        "Item:AgingA": ("Aging A", "instance", []),
        "Item:AgingB": ("Aging B", "instance", []),
    }

    # (cell iri, proc iri) -> Maccor file
    _DATASETS: Dict[Tuple[str, str], Path] = {
        ("Item:CellA", "Item:AgingA"): _MACCOR_DIR / "cellA_agingA_mims_client1.txt",
        ("Item:CellB", "Item:AgingA"): _MACCOR_DIR / "cellB_agingA_mims_client1.txt",
        ("Item:CellA", "Item:AgingB"): _MACCOR_DIR / "cellA_agingB_mims_client1.txt",
        ("Item:CellB", "Item:AgingB"): _MACCOR_DIR / "cellB_agingB_mims_client1.txt",
    }

    @property
    def row_class(self) -> type:
        return StepTypedCyclingDataRow

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
                path = self._DATASETS.get((c, p))
                if path is not None:
                    out.append(
                        Dataset(
                            id=f"{c}|{p}",
                            label=f"{self.page_name(c)} - {self.page_name(p)}",
                            rows=_rows(path),
                            cell_iri=c,
                            procedure_iri=p,
                        )
                    )
        return out

    def page_name(self, iri: str) -> str:
        label, _, _ = self._NODES.get(iri, (iri, "", []))
        return label


view = LazyBatteryDataView(
    backend=MaccorBatteryBackend(),
    title="Battery Cycling Dashboard - Maccor",
)
view.servable()
