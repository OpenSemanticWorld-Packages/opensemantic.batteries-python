"""Minimal example: auto-build a Wunderbaum from in-memory OO-LD objects.

Run with:
    panel serve examples/battery_tree_example.py --dev

What it shows
-------------
Three cells (Cell A/B = cylindrical, Cell C = prismatic) are passed to
OOLDTreeBuilder.  The builder:
  1. Uses has_type() to link each instance to its Python class.
  2. Walks the MRO (SubClassOf) up to BatteryCell (the ceiling = root).
  3. Produces a Wunderbaum without any manually written nodes/edges dicts.

Swap the source to LazySource to load from a remote backend:

    source = LazySource(lambda: my_osw.load("opensemantic:BatteryCell"))
"""

import panel as pn

from battery_example_data import cell_a, cell_b, cell_c
from opensemantic.batteries.v1 import BatteryCell
from opensemantic.batteries.view import OOLDTreeBuilder, PythonSource, has_type

pn.extension()

builder = OOLDTreeBuilder(
    source=PythonSource([cell_a, cell_b, cell_c]),
    relations=[has_type()],
    ceiling=BatteryCell,
)

tree = builder.widget(
    columns=[{"id": "*", "title": "Cell", "width": "260px"}],
    options={"checkbox": True, "selectMode": "hier"},
)

pn.Column(
    "## Cell hierarchy",
    tree,
).servable()
