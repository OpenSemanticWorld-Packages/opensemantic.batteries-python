"""Debug dashboard: plot one OSL test or dataset given by its OSW ID.

Same view and OSL connection as ``battery_dashboard_OSL.py``, but instead of
the cell and procedure trees the sidebar has a text field for a single OSW ID.
"Plot" loads that page and plots its rows like the regular dashboard:

- a dataset page (``ElectrochemicalCyclingDataset``) is plotted directly,
- a test page (``ElectrochemicalTest``) is resolved to its output datasets.

The default ID is the test of cell 1-1 on the dev wiki (the shortened
multi-stage example), so the page plots it right after it opens.

Run from the repository root::

    panel serve examples/osl/battery_dashboard_OSL_single.py --dev

Requires the ``osw`` package and ``examples/accounts.pwd.yaml``.
"""

import logging
import os
from pathlib import Path
from typing import Any, List, Optional, Tuple

import panel as pn
from osw.wtsite import WtSite

from opensemantic.batteries import StepTypedCyclingDataRow
from opensemantic.batteries.view import (
    Dataset,
    LazyBatteryDataView,
    OSLBatteryBackend,
    TreeNode,
    connect_osw,
)

# Test "Cell 1-1 - Multi-stage discharge aging, 5C first stage" on the dev wiki
DEFAULT_ID = "Item:OSWcdf15ab375155e6b82eca0bfb20f0a14"

# Placeholder tree node: the selection always holds it, the ID field decides
# what is plotted.
_ROOT = "Category:SingleEntity"

DEFAULT_LOG_LEVEL = "INFO"

logger = logging.getLogger("opensemantic.batteries.view")
logger.setLevel(os.environ.get("OSB_VIEW_LOG", DEFAULT_LOG_LEVEL).upper())
if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
    _terminal = logging.StreamHandler()
    _terminal.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
    )
    logger.addHandler(_terminal)
logger.propagate = False

pn.extension()


def _iri(value: Any) -> Optional[str]:
    """IRI of a referenced entity: the string itself or the object's IRI."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    get_iri = getattr(value, "get_iri", None)
    return get_iri() if callable(get_iri) else None


class SingleEntityBackend(OSLBatteryBackend):
    """OSL backend whose datasets come from one test or dataset ID."""

    def __init__(self, iri: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.iri = iri

    @property
    def cell_root(self) -> TreeNode:
        return TreeNode(iri=_ROOT, label="Single entity", kind="class")

    @property
    def procedure_root(self) -> TreeNode:
        return TreeNode(iri=_ROOT, label="Single entity", kind="class")

    def children(self, category_iri: str) -> List[TreeNode]:
        return []

    def page_name(self, iri: str) -> str:
        if iri == _ROOT:
            return "Single entity"
        return super().page_name(iri)

    def _resolve(self, iri: str) -> Tuple[List[str], Optional[str], Optional[str]]:
        """Dataset titles, cell IRI and procedure IRI for a test or dataset.

        Reads the page's jsondata: a loaded ElectrochemicalTest comes back with
        empty TestProcedureItem entries, so the procedure would be lost.
        """
        page = self._osw.site.get_page(WtSite.GetPageParam(titles=[iri])).pages[0]
        data = page.get_slot_content("jsondata") or {}
        outputs = data.get("output") or []
        if not isinstance(outputs, list):
            outputs = [outputs]
        titles = [t for t in (_iri(o) for o in outputs) if t] or [iri]
        duts = data.get("device_under_test") or []
        procedures = data.get("test_procedure") or []
        cell = _iri(duts[0]) if duts else None
        procedure = procedures[0].get("test_procedure_instance") if procedures else None
        return titles, cell, procedure

    def datasets(self, cell_iris: List[str], proc_iris: List[str]) -> List[Dataset]:
        if not self.iri or self._osw is None:
            return []
        titles, cell, procedure = self._resolve(self.iri)
        return [
            Dataset(
                id=title,
                label=self.page_name(title),
                rows=self._rows_for(title),
                cell_iri=cell,
                procedure_iri=procedure,
            )
            for title in titles
        ]


class SingleEntityView(LazyBatteryDataView):
    """Lazy view with an ID field in place of the cell and procedure trees."""

    def __init__(self, backend: SingleEntityBackend, **kwargs: Any) -> None:
        super().__init__(backend=backend, **kwargs)
        self._id_input = pn.widgets.TextInput(
            name="OSW ID (test or dataset)", value=backend.iri
        )
        button = pn.widgets.Button(name="Plot", button_type="primary")
        button.on_click(lambda _: self.plot(self._id_input.value))
        self._id_card = pn.Card(self._id_input, button, title="Entity")
        if self._app is not None:
            self._app.sidebar_set(self.sidebar_cards)

    @property
    def sidebar_cards(self) -> List[Any]:
        cards = super().sidebar_cards
        id_card = self.__dict__.get("_id_card")
        if id_card is None:  # during the base __init__
            return cards
        trees = (self._cell_card, self._proc_card)
        return [id_card] + [c for c in cards if all(c is not t for t in trees)]

    def plot(self, iri: str) -> None:
        """Load and plot the test or dataset with this ID."""
        self._backend.iri = iri.strip()
        self._sel_instances["cell"] = {_ROOT}
        self._sel_instances["proc"] = {_ROOT}
        self._apply_selection()


backend = SingleEntityBackend(
    iri=DEFAULT_ID,
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

view = SingleEntityView(
    backend=backend,
    title="Battery Cycling Dashboard - OSL single entity",
    show_log=True,
)
pn.state.onload(lambda: view.plot(DEFAULT_ID))
view.servable()
