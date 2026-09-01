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

    panel serve examples/battery_dashboard_OSL.py --dev

Requires the ``osw`` package (``pip install -e ".[osl]"``) and a credentials
file next to this script (``examples/accounts.pwd.yaml``) — the same connection
``OSL_helper/Query.py`` uses. Without them the dashboard still builds; the trees
just stay empty because every query returns nothing.
"""

from pathlib import Path

import panel as pn

from opensemantic.batteries.view import (
    LazyBatteryDataView,
    OSLBatteryBackend,
    connect_osw,
)

# Row class whose typed fields (Time, Voltage, ElectricCurrent, ...) drive the
# axis grid and unit dropdowns. Same characteristic classes the wiki datasets
# use, so loaded rows convert units through the shared machinery unchanged.
from battery_example_data import CyclingDataRow

pn.extension()

backend = OSLBatteryBackend(
    osw_obj=connect_osw(Path(__file__).parent / "accounts.pwd.yaml"),
    row_class=CyclingDataRow,
    field_names=["test_time", "voltage", "current", "capacity", "cycle_count"],
)

view = LazyBatteryDataView(
    backend=backend,
    title="Battery Cycling Dashboard — OSL",
)

view.servable()
