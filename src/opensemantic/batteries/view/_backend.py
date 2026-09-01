"""Backend abstraction for the lazy battery cycling dashboard.

:class:`LazyBatteryDataView` is deliberately backend-agnostic: it knows how to
paint two lazy category trees, read the checked instances out of them, run a
query for the matching cycling datasets and plot them — but it does **not** know
where those categories, instances or datasets come from. That knowledge lives
behind :class:`BatteryDataBackend`.

A backend answers four questions the view asks:

* what are the two tree roots?          → :attr:`~BatteryDataBackend.cell_root`
                                          / :attr:`~BatteryDataBackend.procedure_root`
* what are a category's children?        → :meth:`~BatteryDataBackend.children`
* which datasets match this selection?   → :meth:`~BatteryDataBackend.datasets`
* what typed row model do they use?      → :attr:`~BatteryDataBackend.row_class`

Swapping the data source (a live OSL wiki, a local cache, a test double, a REST
API, …) is then just a matter of writing another :class:`BatteryDataBackend`;
the view and the example wiring stay untouched. :class:`OSLBatteryBackend`
(``_osl_backend.py``) is the reference implementation over a live OSL wiki.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, List


@dataclass(frozen=True)
class TreeNode:
    """One node the view should paint into a lazy category tree.

    ``kind`` is either ``"class"`` — a category that can be expanded further
    (rendered as a collapsed, lazy, unchecked node) — or ``"instance"`` — a
    selectable leaf (rendered with a checkbox). ``iri`` is the node's stable key
    (an OSW ``Category:``/``Item:`` title, or whatever the backend uses); the
    view hands it straight back to :meth:`BatteryDataBackend.children` on expand
    and to :meth:`BatteryDataBackend.datasets` as a selected id.
    """

    iri: str
    label: str
    kind: str  # "class" | "instance"

    @property
    def is_class(self) -> bool:
        return self.kind == "class"


@dataclass
class Dataset:
    """A plottable cycling dataset matching the current cell + procedure selection.

    ``rows`` are the typed row objects the view plots (instances of the
    backend's :attr:`~BatteryDataBackend.row_class`). ``id`` is a stable,
    de-duplicated identifier; ``label`` is what the Instances card and plot
    legend show.
    """

    id: str
    label: str
    rows: List[Any] = field(default_factory=list)


class BatteryDataBackend(ABC):
    """Data source for :class:`LazyBatteryDataView`.

    Implementations own *all* provenance: the tree roots, lazy child lookups,
    the dataset query and the row model. The view never talks to a wiki, a file
    or an API directly — it only calls these methods, so a new data source is a
    new subclass and nothing else changes.
    """

    # -- Row model -----------------------------------------------------------

    @property
    @abstractmethod
    def row_class(self) -> type:
        """The (pydantic) model class of a single cycling row.

        Its typed characteristic fields (``Time``, ``Voltage``, …) drive the
        axis grid and unit dropdowns, and the objects in :attr:`Dataset.rows`
        are instances of it.
        """

    @property
    def field_names(self) -> List[str]:
        """Row fields to offer as plot axes (default: all but ``type``)."""
        fields = getattr(self.row_class, "__fields__", None) or getattr(
            self.row_class, "model_fields", {}
        )
        return [f for f in fields if f != "type"]

    # -- Trees ---------------------------------------------------------------

    @property
    @abstractmethod
    def cell_root(self) -> TreeNode:
        """Root (class) node the cell tree starts collapsed at."""

    @property
    @abstractmethod
    def procedure_root(self) -> TreeNode:
        """Root (class) node the procedure tree starts collapsed at."""

    @abstractmethod
    def children(self, category_iri: str) -> List[TreeNode]:
        """Sub-categories and instances of a category, for lazy expansion.

        Called when the user expands a class node; ``category_iri`` is that
        node's :attr:`TreeNode.iri`. Return sub-categories as ``"class"`` nodes
        and instances as ``"instance"`` nodes.
        """

    # -- Query ---------------------------------------------------------------

    @abstractmethod
    def datasets(
        self, cell_iris: List[str], proc_iris: List[str]
    ) -> List[Dataset]:
        """Cycling datasets for the selected cells crossed with procedures.

        The view passes the currently checked cell and procedure instance ids;
        the backend returns the matching, de-duplicated datasets with their rows
        resolved. Returns ``[]`` when either selection is empty.
        """

    # -- Labels --------------------------------------------------------------

    def page_name(self, iri: str) -> str:  # noqa: D401 - simple default
        """Human-readable label for an id (default: the id itself)."""
        return iri
