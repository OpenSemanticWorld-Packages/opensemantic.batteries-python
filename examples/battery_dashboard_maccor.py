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
    CyclingDataRow,
    CylindricalCell,
    ElectrochemicalCyclingDataset,
    ElectrochemicalTest,
)
from opensemantic.batteries import read_maccor
from opensemantic.batteries.v1 import (
    BatteryCell,
    ElectrochemicalTestProcedure,
    TestProcedureItem,
)
from opensemantic.batteries.view import (
    BatteryDataView,
    OOLDTreeBuilder,
    PythonSource,
    has_type,
)
from opensemantic.core.v1 import Label

pn.extension()

# The same files used by examples/import_maccor.py. Each entry is one
# measurement of one cell; ``fmt`` selects the Maccor export flavour (``None``
# lets read_maccor detect it from the name). They are loaded and shown side by
# side in the dashboard tree.
_MACCOR_DIR = (
    Path(__file__).parent.parent / "tests" / "data" / "cycling" / "maccor"
)
SOURCES = [
    (_MACCOR_DIR / "231004_test_data_export2_trimmed.024.txt", None),
    (_MACCOR_DIR / "raz-IDCyLIB-E1-full cell2_mims_client1_trimmed.txt", "mims_client1"),
    (_MACCOR_DIR / "raz-IDCyLIB-E1-full cell4_mims_client1_trimmed.txt", "mims_client1"),
]

# ---------------------------------------------------------------------------
# Load the real data
# ---------------------------------------------------------------------------

# read_maccor returns a BatteryCyclingDataset whose rows are the *package's*
# CyclingDataRow (opensemantic.batteries). Its fields are the same v1 quantitative
# characteristics used by this example's CyclingDataRow (from battery_example_data),
# so we can hand the values straight across.
datasets = [
    read_maccor(src, fmt=fmt) if fmt else read_maccor(src) for src, fmt in SOURCES
]


def _to_dashboard_rows(rows):
    """Copy each Maccor source row into this example's CyclingDataRow.

    Both sides use the identical ``opensemantic.characteristics.quantitative.v1``
    characteristic classes, so the typed values pass through unchanged. Fields not
    shared between the Maccor source rows and the example's CyclingDataRow (e.g.
    energy — the dashboard row has no such field) are dropped.
    """
    shared_fields = [
        f for f in CyclingDataRow.__fields__ if f in rows[0].__fields__
    ]
    return [
        CyclingDataRow(
            **{f: getattr(r, f) for f in shared_fields if getattr(r, f) is not None}
        )
        for r in rows
    ]


# One generic procedure and one cell per measurement. A test links its
# procedure(s) via ``test_procedure`` — a list of ``TestProcedureItem`` whose
# ``test_procedure_instance`` is the procedure's OSW IRI (``obj.get_iri()``),
# not the object. The dashboard resolves that IRI back through the
# ``procedure_objects`` map built below.
tests = []
procedures = []
for (src, _fmt), dataset in zip(SOURCES, datasets):
    procedure = ElectrochemicalTestProcedure(
        label=[Label(text=f"Maccor Cycling Procedure — {src.name}")]
    )
    test = ElectrochemicalTest(
        label=[Label(text=src.name)],
        device_under_test=[CylindricalCell(label=[Label(text=f"Cell — {src.name}")])],
        test_procedure=[
            TestProcedureItem(
                test_procedure_instance=procedure.get_iri(),
                test_procedure_instance_property="Property:HasProcedure",
            )
        ],
        output=[ElectrochemicalCyclingDataset(
            label=[Label(text=src.name)],
            data=_to_dashboard_rows(dataset.rows),
        )],
    )
    tests.append(test)
    procedures.append(procedure)

# ---------------------------------------------------------------------------
# Category tree: one cell and one procedure per measurement
# ---------------------------------------------------------------------------

cell_builder = OOLDTreeBuilder(
    source=PythonSource([c for t in tests for c in t.device_under_test]),
    relations=[has_type()],
    ceiling=BatteryCell,
)

procedure_builder = OOLDTreeBuilder(
    source=PythonSource(procedures),
    relations=[has_type()],
    ceiling=ElectrochemicalTestProcedure,
)

view = BatteryDataView(
    tests=tests,
    cell_nodes=cell_builder.build_nodes(),
    cell_edges=cell_builder.build_edges(),
    cell_objects=cell_builder.get_object_map(),
    procedure_nodes=procedure_builder.build_nodes(),
    procedure_edges=procedure_builder.build_edges(),
    procedure_objects=procedure_builder.get_object_map(),
    title="Battery Cycling Dashboard — Maccor Export",
)

view.servable()
