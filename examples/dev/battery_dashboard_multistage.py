"""Lazy battery dashboard over the multi-stage cells converted to CSV.

Same behaviour as ``battery_dashboard_maccor.py``, but each cell is a
multi-stage aging cell read from ``csv_osl`` (written by
``convert_multistage_to_csv.py``). The CSV is the file that OSL stores for the
cell, so the load path is the one of ``materialize_data``: ``read_csv`` of the
bytes, then ``from_df`` of the dataset class. The rows of a cell are loaded the
first time a selection needs them and then kept, without a limit.

Run with:
    panel serve examples/dev/battery_dashboard_multistage.py

The folder with ``index.json`` and the CSV files is ``CSV_DIR``; set the
environment variable ``MULTISTAGE_CSV_DIR`` to read another folder.

Hierarchy:
    Cells:       BatteryCell -> CylindricalCell -> [Cell <id>, ...]
    Procedures:  ElectrochemicalTestProcedure -> AgingTestProcedure
                     -> one procedure per first stage C-rate

The time of each load (file read, ``read_csv``, ``from_df``), of each
``datasets()`` call and of each plot rendering is printed to the console and
written to the log card of the view.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import panel as pn

from opensemantic.base import _pint_csv
from opensemantic.batteries import CyclingDatasetController, StepTypedCyclingDataRow
from opensemantic.batteries.view import (
    BatteryDataBackend,
    Dataset,
    LazyBatteryDataView,
    TreeNode,
)

pn.extension()

CSV_DIR = Path(
    os.environ.get(
        "MULTISTAGE_CSV_DIR",
        r"C:\Users\feiler\Desktop\Example_Data_For_BatterieDigital\csv_osl",
    )
)

# the logger of the view; its records appear in the log card
_log = logging.getLogger("opensemantic.batteries.view")


def _report(text: str) -> None:
    """Print to the console and write to the log card."""
    print(text, flush=True)
    _log.info(text)


class MultistageBatteryBackend(BatteryDataBackend):
    """One CylindricalCell per cell, one AgingTestProcedure per first stage C-rate."""

    _CELL_CLASS = "Category:CylindricalCell"
    _PROC_CLASS = "Category:AgingTestProcedure"

    def __init__(self, csv_dir: Path = CSV_DIR) -> None:
        self._dir = Path(csv_dir)
        entries = json.loads((self._dir / "index.json").read_text(encoding="utf-8"))
        # cell iri -> (label, index entry); procedure iri -> label
        self._cells: Dict[str, Tuple[str, Dict]] = {}
        self._procedures: Dict[str, str] = {}
        self._cell_procedure: Dict[str, str] = {}
        for entry in entries:
            cell_iri = f"Item:Cell{entry['cell']}"
            proc_iri = f"Item:Procedure{entry['c_rate']}C"
            self._cells[cell_iri] = (f"Cell {entry['cell']}", entry)
            self._procedures[proc_iri] = entry["procedure"]
            self._cell_procedure[cell_iri] = proc_iri
        self._procedures = dict(sorted(self._procedures.items()))
        # cell iri -> rows, loaded on first selection and never evicted
        self._rows: Dict[str, List[StepTypedCyclingDataRow]] = {}

    @property
    def row_class(self) -> type:
        return StepTypedCyclingDataRow

    @property
    def cell_root(self) -> TreeNode:
        return TreeNode("Category:BatteryCell", "BatteryCell", "class")

    @property
    def procedure_root(self) -> TreeNode:
        return TreeNode(
            "Category:ElectrochemicalTestProcedure",
            "ElectrochemicalTestProcedure",
            "class",
        )

    def children(self, category_iri: str) -> List[TreeNode]:
        if category_iri == "Category:BatteryCell":
            return [TreeNode(self._CELL_CLASS, "CylindricalCell", "class")]
        if category_iri == self._CELL_CLASS:
            return [
                TreeNode(iri, label, "instance")
                for iri, (label, _) in self._cells.items()
            ]
        if category_iri == "Category:ElectrochemicalTestProcedure":
            return [TreeNode(self._PROC_CLASS, "AgingTestProcedure", "class")]
        if category_iri == self._PROC_CLASS:
            return [
                TreeNode(iri, label, "instance")
                for iri, label in self._procedures.items()
            ]
        return []

    def _load(self, cell_iri: str) -> List[StepTypedCyclingDataRow]:
        """Rows of a cell, read once the way ``materialize_data`` reads CSV."""
        if cell_iri in self._rows:
            return self._rows[cell_iri]
        label, entry = self._cells[cell_iri]
        began = time.perf_counter()
        payload = (self._dir / entry["file"]).read_bytes()
        t_read = time.perf_counter()
        frame = _pint_csv.read_csv(payload)
        t_csv = time.perf_counter()
        rows = CyclingDatasetController.from_df(frame, label=label).data
        t_rows = time.perf_counter()
        self._rows[cell_iri] = rows
        _report(
            f"load {label}: file read {t_read - began:.2f} s, "
            f"read_csv {t_csv - t_read:.2f} s, from_df {t_rows - t_csv:.2f} s, "
            f"total {t_rows - began:.2f} s, {len(rows)} rows"
        )
        return rows

    def datasets(self, cell_iris: List[str], proc_iris: List[str]) -> List[Dataset]:
        began = time.perf_counter()
        out: List[Dataset] = []
        for cell_iri in cell_iris:
            proc_iri = self._cell_procedure.get(cell_iri)
            if proc_iri is None or proc_iri not in proc_iris:
                continue
            label = self._cells[cell_iri][0]
            out.append(
                Dataset(
                    id=f"{cell_iri}|{proc_iri}",
                    label=f"{label} - {self._procedures[proc_iri]}",
                    rows=self._load(cell_iri),
                    cell_iri=cell_iri,
                    procedure_iri=proc_iri,
                )
            )
        _report(
            f"datasets(): {len(out)} datasets in {time.perf_counter() - began:.2f} s"
        )
        return out

    def page_name(self, iri: str) -> str:
        if iri in self._cells:
            return self._cells[iri][0]
        return self._procedures.get(iri, iri.split(":", 1)[-1])


def _timed(view: Any, name: str, text: str) -> None:
    """Report the time of ``view.<name>`` on every call (instance-level wrap)."""
    method = getattr(view, name)

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        began = time.perf_counter()
        try:
            return method(*args, **kwargs)
        finally:
            _report(f"{text}: {time.perf_counter() - began:.2f} s")

    setattr(view, name, wrapper)


view = LazyBatteryDataView(
    backend=MultistageBatteryBackend(),
    title="Battery Cycling Dashboard - Multi-stage cells",
    show_log=True,
)
# _build_figure renders the time series, cycle and batch plots together, so
# its time includes the batch plot line below. Redraws started from the cycle
# and batch grids use methods bound before this wrap and are not timed.
_timed(view, "_build_figure", "render all plots")
_timed(view, "_build_batch_figure", "render batch plot")
view.servable()
