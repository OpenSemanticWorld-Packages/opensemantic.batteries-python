"""Battery cycling dashboard backed by a **live OSL (Open Semantic Lab)** wiki.

Unlike ``battery_dashboard.py`` (synthetic, everything in memory) this example
keeps *nothing* in memory up front. The cell and procedure trees are **lazy**:
they start as a single collapsed root category and only query the wiki for their
sub-categories and instances when the user expands a node. Selecting cells +
procedures then runs a semantic search for the matching cycling datasets, loads
them on demand, and plots them.

All the OSL logic now lives in the package
(:class:`opensemantic.batteries.view.OSLBatteryBackend` +
:class:`~opensemantic.batteries.view.LazyBatteryDataView`); this file is just
the wiring. To plot from a different source, write another
``BatteryDataBackend`` and hand it to ``LazyBatteryDataView`` instead — nothing
else here changes.

Run with::

    panel serve examples/osl/battery_dashboard_OSL.py --dev

Requires the ``osw`` package (``pip install -e ".[osl]"``) and a credentials
file in the parent folder (``examples/accounts.pwd.yaml``) — the same connection
``examples/osl/osl_queries.py`` runs. Without them the dashboard still builds; the trees
just stay empty because every query returns nothing.
"""

import logging
import os
from pathlib import Path

import panel as pn

# Row class whose typed fields (Time, Voltage, ElectricCurrent, ...) drive the
# axis grid and unit dropdowns. It adds step_type, which the cycle and batch
# plots need to separate charge from discharge.
from opensemantic.batteries import StepTypedCyclingDataRow
from opensemantic.batteries.view import (
    LazyBatteryDataView,
    OSLBatteryBackend,
    connect_osw,
)

# Log level of the dashboard: set the environment variable OSB_VIEW_LOG (for
# example DEBUG or WARNING) or change this constant. DEBUG logs every query text.
# An explicit level is kept when the view starts with show_log=True.
DEFAULT_LOG_LEVEL = "INFO"

logger = logging.getLogger("opensemantic.batteries.view")
logger.setLevel(os.environ.get("OSB_VIEW_LOG", DEFAULT_LOG_LEVEL).upper())
if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
    _terminal = logging.StreamHandler()
    _terminal.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
    )
    logger.addHandler(_terminal)
# The handler above prints the records, so do not pass them to the root handler
# that panel serve configures (every record would appear twice).
logger.propagate = False

pn.extension()

backend = OSLBatteryBackend(
    osw_obj=connect_osw(Path(__file__).resolve().parent.parent / "accounts.pwd.yaml"),
    row_class=StepTypedCyclingDataRow,
    field_names=[
        "test_time",
        "voltage",
        "current",
        "capacity",
        "cycle_count",
        "step_type",
    ],
)

view = LazyBatteryDataView(
    backend=backend,
    title="Battery Cycling Dashboard — OSL",
    show_log=True,
)

view.servable()
