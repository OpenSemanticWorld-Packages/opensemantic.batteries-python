"""Battery cycling dashboard on a **real Maccor cycler file**.

Unlike ``battery_dashboard.py`` (which plots synthetic sample data), this loads
an actual Maccor export via the public ``read_maccor`` importer — the same file
and loading path as ``examples/import_maccor.py`` — and plots it in the
``BatteryDataView`` dashboard.

Run with:
    panel serve examples/battery_dashboard_maccor.py --dev

A single cycler file is one measurement of one cell, so the category tree is
deliberately minimal: one cell instance and one procedure instance. The file
carries no metadata about cell form factor or test type, so we pick neutral
placeholders — a ``CylindricalCell`` and a generic ``ElectrochemicalTestProcedure``.
If you know the real form factor / test kind, swap in e.g. ``PrismaticCell`` or
``FormationTestProcedure`` / ``AgingTestProcedure`` for a richer tree.
"""

from pathlib import Path

import panel as pn

from battery_example_data import (
    CylindricalCell,
    ElectrochemicalCyclingDataRow,
    ElectrochemicalCyclingDataset,
    ElectrochemicalTest,
)
from opensemantic.batteries import read_maccor
from opensemantic.batteries.v1 import BatteryCell, ElectrochemicalTestProcedure
from opensemantic.batteries.view import (
    BatteryDataView,
    OOLDTreeBuilder,
    PythonSource,
    has_type,
)
from opensemantic.core.v1 import Label

pn.extension()

# The same file used by examples/import_maccor.py (format detected from the name).
SOURCE = (
    Path(__file__).parent.parent
    / "tests"
    / "data"
    / "cycling"
    / "maccor"
    / "231004_test_data_export2_trimmed.024.txt"
)

# ---------------------------------------------------------------------------
# Load the real data
# ---------------------------------------------------------------------------

# read_maccor returns a BatteryCyclingDataset whose rows are CyclingDataRow.
# Its fields are the same v1 quantitative characteristics used by the dashboard's
# ElectrochemicalCyclingDataRow, so we can hand the values straight across.
dataset = read_maccor(SOURCE)

# Fields shared between CyclingDataRow (source) and ElectrochemicalCyclingDataRow
# (what the dashboard plots). Energy is dropped — the dashboard row has no such
# field — but it is easy to add there if you want it on an axis.
_SHARED_FIELDS = [
    f for f in ElectrochemicalCyclingDataRow.__fields__ if f in dataset.rows[0].__fields__
]


def _to_dashboard_rows(rows):
    """Copy each Maccor CyclingDataRow into an ElectrochemicalCyclingDataRow.

    Both sides use the identical ``opensemantic.characteristics.quantitative.v1``
    characteristic classes, so the typed values pass through unchanged.
    """
    return [
        ElectrochemicalCyclingDataRow(
            **{f: getattr(r, f) for f in _SHARED_FIELDS if getattr(r, f) is not None}
        )
        for r in rows
    ]


test = ElectrochemicalTest(
    label=[Label(text="Maccor Export (024)")],
    device_under_test=[CylindricalCell(label=[Label(text="Maccor Test Cell")])],
    protocol=ElectrochemicalTestProcedure(
        label=[Label(text="Maccor Cycling Procedure")]
    ),
    output=ElectrochemicalCyclingDataset(
        label=[Label(text=SOURCE.name)],
        data=_to_dashboard_rows(dataset.rows),
    ),
)

# ---------------------------------------------------------------------------
# Single-file category tree: one cell, one procedure
# ---------------------------------------------------------------------------

cell_builder = OOLDTreeBuilder(
    source=PythonSource(test.device_under_test),
    relations=[has_type()],
    ceiling=BatteryCell,
)

procedure_builder = OOLDTreeBuilder(
    source=PythonSource([test.protocol]),
    relations=[has_type()],
    ceiling=ElectrochemicalTestProcedure,
)

view = BatteryDataView(
    tests=[test],
    cell_nodes=cell_builder.build_nodes(),
    cell_edges=cell_builder.build_edges(),
    cell_objects=cell_builder.get_object_map(),
    procedure_nodes=procedure_builder.build_nodes(),
    procedure_edges=procedure_builder.build_edges(),
    procedure_objects=procedure_builder.get_object_map(),
    title="Battery Cycling Dashboard — Maccor Export",
)

view.servable()
