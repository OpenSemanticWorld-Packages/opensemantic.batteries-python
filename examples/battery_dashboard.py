"""Battery cycling dashboard example.

Run with:
    panel serve examples/battery_dashboard.py --dev

The cell and procedure trees are built automatically from the Python objects
using OOLDTreeBuilder — no manually written nodes/edges/objects dicts.
Swap PythonSource for LazySource to load from a remote backend.
"""

import panel as pn

from battery_example_data import (
    CylindricalCell, PrismaticCell,
    ElectrochemicalTestProcedure,
    cell_a, cell_b, cell_c,
    aging_test_a, aging_test_b, formation_procedure,
    test_cell_a_aging_a, test_cell_a_formation,
    test_cell_b_aging_a, test_cell_b_aging_b,
    test_cell_b_formation, test_cell_c_formation,
)
from opensemantic.batteries.v1 import BatteryCell
from opensemantic.batteries.view import BatteryDataView, OOLDTreeBuilder, PythonSource, has_type

pn.extension()

tests = [
    test_cell_a_aging_a,
    test_cell_a_formation,
    test_cell_b_aging_a,
    test_cell_b_aging_b,
    test_cell_b_formation,
    test_cell_c_formation,
]

# Cell hierarchy: Cell A/B/C → CylindricalCell/PrismaticCell → BatteryCell
cell_builder = OOLDTreeBuilder(
    source=PythonSource([cell_a, cell_b, cell_c]),
    relations=[has_type()],
    ceiling=BatteryCell,
)

# Procedure hierarchy: aging_test_a/b, formation → AgingTestProcedure/FormationTestProcedure → ElectrochemicalTestProcedure
procedure_builder = OOLDTreeBuilder(
    source=PythonSource([aging_test_a, aging_test_b, formation_procedure]),
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
    title="Battery Cycling Dashboard",
)

view.servable()
