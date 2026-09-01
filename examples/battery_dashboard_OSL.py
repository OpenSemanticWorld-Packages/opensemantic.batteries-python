"""Battery cycling dashboard backed by a **live OSL (Open Semantic Lab)** wiki.

Unlike ``battery_dashboard.py`` (synthetic, everything in memory) this example
keeps *nothing* in memory up front. The cell and procedure trees are **lazy**:
they start as a single collapsed root category and only query the wiki for their
sub-categories and instances when the user expands a node. Selecting cells +
procedures then runs a semantic search for the matching ``ElectrochemicalTest``
instances, loads their cycling datasets on demand, and plots them.

Run with::

    panel serve examples/battery_dashboard_OSL.py --dev

Requires the ``osw`` package and a credentials file next to this script
(``examples/accounts.pwd.yaml``) — the same connection ``OSL_helper/Query.py``
uses. Without them the dashboard still builds; the trees just stay empty because
every query returns nothing.

How the wiki is queried (see ``OSL_helper/Query.py`` for the raw calls)
----------------------------------------------------------------------
* sub-categories of a category ``C``   ``[[SubClassOf::C]]``
* instances of a category ``C``        ``[[HasSchema::C]]``
* datasets for a cell + procedure      ``[[-HasOutput.HasSchema::<ElectrochemicalTest>]]``
                                       ``[[-HasOutput.HasDut::<cell>]][[-HasOutput.HasProcedure::<proc>]]``

Category IRIs come straight from the generated classes' ``get_cls_iri()`` — no
hand-copied OSW ids. The dataset query uses the *inverse-output* path (a page
that is the ``HasOutput`` of a matching test), so each result is a cycling
dataset whose rows come straight from ``osw_obj.load_entity(<title>).data``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import panel as pn
from panelini.panels.wunderbaum import Wunderbaum

from opensemantic.batteries.v1 import (
    BatteryCell,
    ElectrochemicalTest,
    ElectrochemicalTestProcedure,
)
from opensemantic.batteries.view._battery_dashboard import (
    BatteryDataView,
    _FieldChannel,
)

# Row class whose typed fields (Time, Voltage, ElectricCurrent, ...) drive the
# axis grid and unit dropdowns. Same characteristic classes the wiki datasets
# use, so loaded rows convert units through the shared machinery unchanged.
from battery_example_data import CyclingDataRow

pn.extension()


# ---------------------------------------------------------------------------
# OSL connection (mirrors OSL_helper/Query.py)
# ---------------------------------------------------------------------------

def _connect_osw() -> Optional[Any]:
    """Open an OswExpress session, or return None if it can't be established.

    Returning None (rather than raising) lets the dashboard load without a wiki
    connection — handy for a quick UI smoke test; every query then no-ops.
    """
    try:
        from osw.defaults import params as default_params
        from osw.defaults import paths as default_paths
        from osw.express import OswExpress

        cred = Path(__file__).parent / "accounts.pwd.yaml"
        default_paths.cred_filepath = cred
        default_params.wiki_domain = "wiki-dev.open-semantic-lab.org"
        return OswExpress(
            domain="wiki-dev.open-semantic-lab.org", cred_filepath=cred
        )
    except Exception as exc:  # noqa: BLE001 — degrade gracefully, log why
        print(f"[battery_dashboard_OSL] no OSL connection: {exc}")
        return None


# ---------------------------------------------------------------------------
# Lazy, OSL-backed view
# ---------------------------------------------------------------------------

class OSLBatteryDataView(BatteryDataView):
    """``BatteryDataView`` whose trees and tests come from a live OSL wiki.

    Everything downstream of the selection (axis grid, unit dropdowns, plot,
    freeze/unfreeze) is inherited unchanged. Only three things are overridden:

    * the two trees are built **lazy** (root category only; children fetched on
      expand) instead of from a pre-materialised OO-LD graph;
    * selection is read from the tree's serialised ``source`` (tolerant of the
      flattened shape the browser sends back after a checkbox toggle);
    * ``_matching_tests`` / ``_resolve_traces`` are served from ``self._tests``,
      which a semantic search repopulates on every selection change.
    """

    def __init__(
        self,
        osw_obj: Optional[Any],
        cell_root_iri: str,
        proc_root_iri: str,
        test_category_iri: str,
        row_cls: type,
        field_names: List[str],
        title: str = "Battery Cycling Dashboard — OSL",
    ) -> None:
        self._osw = osw_obj
        self._cell_root_iri = cell_root_iri
        self._proc_root_iri = proc_root_iri
        self._test_category_iri = test_category_iri
        self._row_cls = row_cls

        # Matches for the current selection: [{idx, test(title), label, rows}].
        # Set before super().__init__ because it builds the Instances card,
        # which reads _matching_tests().
        self._osl_matches: List[Dict[str, Any]] = []
        # Caches so panning around the trees doesn't re-hit the wiki.
        self._name_cache: Dict[str, str] = {}
        self._rows_cache: Dict[str, List[Any]] = {}

        super().__init__(
            tests=[],
            cell_nodes={},
            cell_edges=[],
            cell_objects={},
            procedure_nodes={},
            procedure_edges=[],
            procedure_objects={},
            field_names=field_names,
            title=title,
        )

    # -- Field channels from the row class (no sample rows to scan) -----------

    def _detect_field_channels(
        self, tests: List[Any], field_names: List[str]
    ) -> Dict[str, _FieldChannel]:
        """Build one channel per field from ``row_cls``' declared field types.

        The base class scans live rows for a first non-None value; here there
        are none up front, so read the characteristic class straight off the
        (pydantic v1) model field's ``type_``.
        """
        channels: Dict[str, _FieldChannel] = {}
        for f in field_names:
            fld = self._row_cls.__fields__.get(f)
            if fld is not None and isinstance(fld.type_, type):
                channels[f] = _FieldChannel(f, fld.type_)
        return channels

    # -- Lazy trees ----------------------------------------------------------

    def _build_cell_tree(self, nodes: Dict, edges: List) -> None:
        self._cell_tree_title = "Cell"
        self._cell_tree = self._make_lazy_tree(
            self._cell_root_iri, BatteryCell.__name__, self._cell_tree_title
        )
        self._cell_tree.param.watch(self._on_osl_tree_change, ["source"])
        self._cell_card = pn.Card(
            self._cell_tree,
            title="Cells",
            collapsed=False,
            height=self._TREE_CARD_HEIGHT,
            scroll=True,
        )

    def _build_procedure_card(self, nodes: Dict, edges: List) -> None:
        self._proc_tree_title = "Procedure"
        self._proc_tree = self._make_lazy_tree(
            self._proc_root_iri,
            ElectrochemicalTestProcedure.__name__,
            self._proc_tree_title,
        )
        self._proc_tree.param.watch(self._on_osl_tree_change, ["source"])
        self._proc_card = pn.Card(
            self._proc_tree,
            title="Procedures",
            collapsed=False,
            height=self._TREE_CARD_HEIGHT,
            scroll=True,
        )

    def _make_lazy_tree(self, root_iri: str, root_title: str, col: str) -> Wunderbaum:
        """A Wunderbaum showing one collapsed, lazy root category.

        ``selectMode: "multi"`` keeps each instance checkbox independent, so a
        checkbox toggle maps one-to-one to a selected instance (no hierarchical
        cascade to reconcile against lazily-loaded children).
        """
        root = self._class_node(root_iri, label=root_title)
        return Wunderbaum(
            source=[root],
            columns=[{"id": "*", "title": col, "width": "220px"}],
            options={"checkbox": True, "selectMode": "multi"},
            lazy_load_callback=self._lazy_children,
            tree_event_callback=None,
        )

    def _class_node(self, cat_iri: str, label: Optional[str] = None) -> Dict:
        """A collapsed, lazy, *unchecked* category node."""
        return {
            "title": label or self._page_name(cat_iri),
            "key": cat_iri,
            "lazy": True,
            "checkbox": False,
            "expanded": False,
            "data": {"kind": "class", "node_id": cat_iri},
        }

    def _instance_node(self, item_iri: str) -> Dict:
        """A checkable leaf node for an instance."""
        return {
            "title": self._page_name(item_iri),
            "key": item_iri,
            "checkbox": True,
            "selected": False,
            "data": {"kind": "instance", "node_id": item_iri},
        }

    def _lazy_children(self, node_key: str, _request: Dict) -> List[Dict]:
        """Wunderbaum lazy-load callback: children of the expanded category.

        ``node_key`` is the category IRI. Sub-categories become further lazy
        class nodes; instances become checkable leaves.
        """
        children: List[Dict] = [
            self._class_node(sub) for sub in self._subclasses(node_key)
        ]
        children += [self._instance_node(inst) for inst in self._instances(node_key)]
        return children

    # -- Selection -> query -> plot ------------------------------------------

    def _on_osl_tree_change(self, *_args: Any) -> None:
        if self._restoring:
            return
        self._selected_cell_ids = self._checked_instances(self._cell_tree.source)
        self._selected_proc_ids = self._checked_instances(self._proc_tree.source)
        self._run_test_query()
        self._refresh_instances()
        self._build_figure()

    @staticmethod
    def _checked_instances(source: List[Dict]) -> List[str]:
        """Collect node_ids of checked instance nodes from a serialised tree.

        Tolerant of both shapes the widget uses: the pristine Python source
        nests custom keys under ``data``; once the browser round-trips a
        selection it flattens ``node_id`` / ``kind`` to the top level.
        """
        result: List[str] = []

        def walk(nodes: List[Dict]) -> None:
            for n in nodes:
                data = n.get("data", {})
                kind = n.get("kind") or data.get("kind", "")
                node_id = n.get("node_id") or data.get("node_id")
                if n.get("selected") and kind == "instance" and node_id:
                    result.append(node_id)
                walk(n.get("children", []))

        walk(source or [])
        return result

    def _run_test_query(self) -> None:
        """Repopulate ``_osl_matches`` for the current cell + procedure selection.

        Runs one dataset search per (cell, procedure) pair and de-duplicates the
        resulting dataset pages, then resolves each one's cycling rows (cached).
        Matching happens server-side, so every returned dataset is a genuine
        match — ``_matching_tests`` just hands the list back. Each match is one
        test's output dataset (labelled by its page name).
        """
        self._osl_matches = []
        if not self._selected_cell_ids or not self._selected_proc_ids or self._osw is None:
            return

        seen: Set[str] = set()
        matches: List[Dict[str, Any]] = []
        for cell in self._selected_cell_ids:
            for proc in self._selected_proc_ids:
                for dataset in self._datasets_for(cell, proc):
                    if dataset in seen:
                        continue
                    seen.add(dataset)
                    matches.append({
                        "idx": len(matches),
                        "test": dataset,
                        "label": self._page_name(dataset),
                        "rows": self._rows_for_test(dataset),
                    })
        self._osl_matches = matches

    # -- Reuse the base plot pipeline, served from _osl_matches --------------

    def _matching_tests(self) -> List[Dict]:
        return self._osl_matches

    def _resolve_traces(self) -> List[Dict]:
        traces: List[Dict] = []
        for m in self._matching_tests():
            if not self._instance_selections.get(m["idx"], True):
                continue
            if m["rows"]:
                traces.append({"label": m["label"], "rows": m["rows"]})
        return traces

    # -- Freeze/unfreeze: restore selection state without rebuilding trees ----

    def _apply_state(self, state: Any) -> None:
        """Restore a frozen plot's state, re-querying instead of rebuilding trees.

        Lazy trees can't be repainted from a pristine full source (there isn't
        one), so unfreeze restores the selected IRIs + axis/unit choices and
        re-runs the query. The plot returns; the tree checkboxes are not
        re-ticked (a deliberate simplification for this example).
        """
        self._restoring = True
        try:
            self._selected_cell_ids = list(state.cell_ids)
            self._selected_proc_ids = list(state.proc_ids)
            self._instance_selections = dict(state.instance_selections)
            self._axis_map = dict(state.axis_map)
            self._unit_selections = dict(state.unit_selections)
            self._run_test_query()
            self._refresh_instances()
            self._sync_axis_grid()
            self._sync_unit_selects()
        finally:
            self._restoring = False
        self._build_figure()

    def _state_summary(self, state: Any) -> str:
        cells = [self._page_name(c) for c in state.cell_ids]
        procs = [self._page_name(p) for p in state.proc_ids]
        axis = state.axis_map
        y = axis.get("y1") or "—"
        if axis.get("y2"):
            y = f"{y}+{axis['y2']}"
        return (
            f"{', '.join(cells) or '—'} / {', '.join(procs) or '—'} · "
            f"{y} vs {axis.get('x') or '—'}"
        )

    # -- Wiki queries (see OSL_helper/Query.py) ------------------------------

    # ``semantic_search`` intermittently raises ``AttributeError: 'list' object
    # has no attribute 'values'`` — even for queries that *do* have results (the
    # same query flip-flops between the list and the error across calls). So the
    # AttributeError is retried a few times before being taken as "truly empty",
    # otherwise valid rows would randomly vanish from the dashboard.
    _SEARCH_RETRIES = 4

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
                print(f"[battery_dashboard_OSL] query failed: {query!r}: {exc}")
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

    def _page_name(self, title: str) -> str:
        """Human-readable ``name`` for a page title, cached (falls back to title)."""
        if title in self._name_cache:
            return self._name_cache[title]
        name = title
        if self._osw is not None:
            try:
                content = self._osw.site.get_page_content([title]).contents[title]
                name = content["jsondata"].get("name", title)
            except Exception:  # noqa: BLE001
                pass
        self._name_cache[title] = name
        return name

    def _rows_for_test(self, title: str) -> List[Any]:
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
                print(f"[battery_dashboard_OSL] load_entity({title!r}) failed: {exc}")
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


# ---------------------------------------------------------------------------
# Build and serve
# ---------------------------------------------------------------------------

view = OSLBatteryDataView(
    osw_obj=_connect_osw(),
    cell_root_iri=BatteryCell.get_cls_iri(),
    proc_root_iri=ElectrochemicalTestProcedure.get_cls_iri(),
    test_category_iri=ElectrochemicalTest.get_cls_iri(),
    row_cls=CyclingDataRow,
    field_names=["test_time", "voltage", "current", "capacity", "cycle_count"],
)

view.servable()
