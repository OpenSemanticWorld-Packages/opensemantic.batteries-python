"""OSL (Open Semantic Lab) backend for the lazy battery dashboard.

Reference :class:`~opensemantic.batteries.view._backend.BatteryDataBackend`
implementation over a **live OSL wiki**. It answers the view's four questions
with semantic searches (see ``OSL_helper/Query.py`` for the raw calls):

* sub-categories of a category ``C``   ``[[SubClassOf::C]]``
* instances of a category ``C``        ``[[HasSchema::C]]``
* datasets for a cell + procedure      ``[[-HasOutput.HasSchema::<ElectrochemicalTest>]]``
                                       ``[[-HasOutput.HasDut::<cell>]][[-HasOutput.HasProcedure::<proc>]]``

Category IRIs default to the generated classes' ``get_cls_iri()`` — no
hand-copied OSW ids. The dataset query uses the *inverse-output* path (a page
that is the ``HasOutput`` of a matching test), so each result is a cycling
dataset whose rows come straight from ``osw_obj.load_entity(<title>).data``.

``osw`` is imported lazily (only :func:`connect_osw` needs it), so importing
this module — and the whole ``view`` package — never requires the optional
``osw`` dependency. Install it with the ``osl`` extra to actually connect.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional, Set, Union

from opensemantic.batteries.v1 import (
    BatteryCell,
    ElectrochemicalTest,
    ElectrochemicalTestProcedure,
)
from opensemantic.batteries.view._backend import (
    BatteryDataBackend,
    Dataset,
    TreeNode,
)

DEFAULT_WIKI_DOMAIN = "wiki-dev.open-semantic-lab.org"


def connect_osw(
    cred_filepath: Union[str, Path],
    wiki_domain: str = DEFAULT_WIKI_DOMAIN,
) -> Optional[Any]:
    """Open an ``OswExpress`` session, or return ``None`` if it can't be made.

    Returning ``None`` (rather than raising) lets a dashboard load without a
    wiki connection — handy for a quick UI smoke test; every query then no-ops.
    The credentials file (a ``.pwd.yaml`` secret) is referenced only by path and
    never read here — keep it out of version control.
    """
    try:
        from osw.defaults import params as default_params
        from osw.defaults import paths as default_paths
        from osw.express import OswExpress

        cred = Path(cred_filepath)
        default_paths.cred_filepath = cred
        default_params.wiki_domain = wiki_domain
        return OswExpress(domain=wiki_domain, cred_filepath=cred)
    except Exception as exc:  # noqa: BLE001 — degrade gracefully, log why
        print(f"[opensemantic.batteries.view] no OSL connection: {exc}")
        return None


class OSLBatteryBackend(BatteryDataBackend):
    """Battery data served from a live OSL wiki via ``OswExpress``.

    Holds the wiki session, the tree-root / test category IRIs and the row
    model, plus small caches (page names, loaded rows) so panning around the
    trees doesn't re-hit the wiki. When ``osw_obj`` is ``None`` every method
    degrades to empty results, so the dashboard still builds offline.
    """

    # ``semantic_search`` intermittently raises ``AttributeError: 'list' object
    # has no attribute 'values'`` — even for queries that *do* have results (the
    # same query flip-flops between the list and the error across calls). So the
    # AttributeError is retried a few times before being taken as "truly empty",
    # otherwise valid rows would randomly vanish from the dashboard.
    _SEARCH_RETRIES = 4

    def __init__(
        self,
        osw_obj: Optional[Any],
        row_class: type,
        cell_root_iri: Optional[str] = None,
        procedure_root_iri: Optional[str] = None,
        test_category_iri: Optional[str] = None,
        field_names: Optional[List[str]] = None,
        cell_root_label: str = BatteryCell.__name__,
        procedure_root_label: str = ElectrochemicalTestProcedure.__name__,
    ) -> None:
        self._osw = osw_obj
        self._row_class = row_class
        self._cell_root_iri = cell_root_iri or BatteryCell.get_cls_iri()
        self._proc_root_iri = (
            procedure_root_iri or ElectrochemicalTestProcedure.get_cls_iri()
        )
        self._test_category_iri = (
            test_category_iri or ElectrochemicalTest.get_cls_iri()
        )
        self._field_names = field_names
        self._cell_root_label = cell_root_label
        self._proc_root_label = procedure_root_label

        # Caches so panning around the trees doesn't re-hit the wiki.
        self._name_cache: dict = {}
        self._rows_cache: dict = {}

    # -- Row model -----------------------------------------------------------

    @property
    def row_class(self) -> type:
        return self._row_class

    @property
    def field_names(self) -> List[str]:
        if self._field_names is not None:
            return self._field_names
        return super().field_names

    # -- Trees ---------------------------------------------------------------

    @property
    def cell_root(self) -> TreeNode:
        return TreeNode(self._cell_root_iri, self._cell_root_label, "class")

    @property
    def procedure_root(self) -> TreeNode:
        return TreeNode(self._proc_root_iri, self._proc_root_label, "class")

    def children(self, category_iri: str) -> List[TreeNode]:
        """Sub-categories (class nodes) + instances (leaf nodes) of a category."""
        nodes = [
            TreeNode(sub, self.page_name(sub), "class")
            for sub in self._subclasses(category_iri)
        ]
        nodes += [
            TreeNode(inst, self.page_name(inst), "instance")
            for inst in self._instances(category_iri)
        ]
        return nodes

    # -- Query ---------------------------------------------------------------

    def datasets(
        self, cell_iris: List[str], proc_iris: List[str]
    ) -> List[Dataset]:
        """Cycling datasets for each (cell, procedure) pair, de-duplicated.

        Runs one dataset search per pair and drops repeats, resolving each
        dataset's rows (cached). Matching happens server-side, so every returned
        page is a genuine match.
        """
        if not cell_iris or not proc_iris or self._osw is None:
            return []
        seen: Set[str] = set()
        result: List[Dataset] = []
        for cell in cell_iris:
            for proc in proc_iris:
                for title in self._datasets_for(cell, proc):
                    if title in seen:
                        continue
                    seen.add(title)
                    result.append(
                        Dataset(
                            id=title,
                            label=self.page_name(title),
                            rows=self._rows_for(title),
                        )
                    )
        return result

    # -- Labels --------------------------------------------------------------

    def page_name(self, iri: str) -> str:
        """Human-readable ``name`` for a page title, cached (falls back to title)."""
        if iri in self._name_cache:
            return self._name_cache[iri]
        name = iri
        if self._osw is not None:
            try:
                content = self._osw.site.get_page_content([iri]).contents[iri]
                name = content["jsondata"].get("name", iri)
            except Exception:  # noqa: BLE001
                pass
        self._name_cache[iri] = name
        return name

    # -- Wiki queries (see OSL_helper/Query.py) ------------------------------

    def _search(self, query: str) -> List[str]:
        """Run a semantic search, returning page titles (``[]`` on empty/failure)."""
        if self._osw is None:
            return []
        for _ in range(self._SEARCH_RETRIES):
            try:
                return list(
                    self._osw.site.semantic_search(
                        self._osw.site.SearchParam(query=query, debug=False)
                    )
                )
            except AttributeError:
                continue  # flaky zero-hit quirk — retry, then treat as empty
            except Exception as exc:  # noqa: BLE001
                print(f"[opensemantic.batteries.view] query failed: {query!r}: {exc}")
                return []
        return []

    def _subclasses(self, cat_iri: str) -> List[str]:
        return self._search(f"[[SubClassOf::{cat_iri}]]")

    def _instances(self, cat_iri: str) -> List[str]:
        return self._search(f"[[HasSchema::{cat_iri}]]")

    def _datasets_for(self, cell_iri: str, proc_iri: str) -> List[str]:
        """Cycling datasets output by an ElectrochemicalTest on this cell + proc.

        Uses the inverse-property path from ``OSL_helper/Query.py``: find each
        page that is the ``HasOutput`` of a test whose schema / DUT / procedure
        match. The results are the dataset pages themselves, so
        ``load_entity(title).data`` yields the rows directly — no second hop
        through the test. (The forward ``[[HasDut::…]][[HasProcedure::…]]`` combo
        does not reliably intersect on the wiki once the procedure link moved
        into ``TestProcedureItem``; the inverse-output path does.)
        """
        return self._search(
            f"[[-HasOutput.HasSchema::{self._test_category_iri}]]"
            f"[[-HasOutput.HasDut::{cell_iri}]]"
            f"[[-HasOutput.HasProcedure::{proc_iri}]]"
        )

    # -- Row loading ---------------------------------------------------------

    def _rows_for(self, title: str) -> List[Any]:
        """Load a test (or dataset) page and return its cycling rows, cached.

        ``load_entity`` yields either a dataset (has ``.data`` directly — as in
        Query.py) or an ``ElectrochemicalTest`` whose ``.output`` references one
        or more datasets; both are resolved to a flat list of rows.
        """
        if title in self._rows_cache:
            return self._rows_cache[title]
        rows: List[Any] = []
        if self._osw is not None:
            try:
                rows = self._entity_rows(self._osw.load_entity(title))
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[opensemantic.batteries.view] load_entity({title!r}) failed: {exc}"
                )
        self._rows_cache[title] = rows
        return rows

    def _entity_rows(self, entity: Any) -> List[Any]:
        data = getattr(entity, "data", None)
        if data:
            return list(data)
        outputs = getattr(entity, "output", None) or []
        if not isinstance(outputs, (list, tuple)):
            outputs = [outputs]
        rows: List[Any] = []
        for out in outputs:
            if isinstance(out, str):  # an IRI — load the referenced dataset
                if self._osw is None:
                    continue
                try:
                    out = self._osw.load_entity(out)
                except Exception:  # noqa: BLE001
                    continue
            d = getattr(out, "data", None)
            if d:
                rows.extend(d)
        return rows
