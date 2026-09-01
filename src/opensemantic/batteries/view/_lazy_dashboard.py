"""Lazy, backend-driven battery cycling dashboard — LazyBatteryDataView.

A :class:`~opensemantic.batteries.view.BatteryDataView` whose cell and procedure
trees are **lazy** and whose matching tests come from a pluggable
:class:`~opensemantic.batteries.view._backend.BatteryDataBackend` instead of a
pre-materialised OO-LD graph.

Unlike the base view (everything in memory up front), nothing is held eagerly:
each tree starts as a single collapsed root category and only asks the backend
for a node's children when the user expands it. Selecting cells + procedures
asks the backend for the matching cycling datasets, which are then plotted
through the base view's unchanged axis / unit / freeze pipeline.

Everything data-source-specific lives behind the backend, so swapping the wiki
for a cache, a file or a test double is a new ``BatteryDataBackend`` and no
change here. See :mod:`opensemantic.batteries.view._backend`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import panel as pn
from panelini.panels.wunderbaum import Wunderbaum

from opensemantic.batteries.view._backend import BatteryDataBackend, TreeNode
from opensemantic.batteries.view._battery_dashboard import (
    BatteryDataView,
    _FieldChannel,
)


class LazyBatteryDataView(BatteryDataView):
    """``BatteryDataView`` driven by a lazy :class:`BatteryDataBackend`.

    Only the source-facing parts of the base view are overridden:

    * the two trees are built **lazy** (root category only; children fetched
      from the backend on expand) rather than from a pre-built OO-LD graph;
    * selection is read from the tree's serialised ``source`` (tolerant of the
      flattened shape the browser sends back after a checkbox toggle);
    * ``_matching_tests`` / ``_resolve_traces`` are served from ``self._matches``,
      which the backend's :meth:`~BatteryDataBackend.datasets` repopulates on
      every selection change.

    Everything downstream of the selection — axis grid, unit dropdowns, plot,
    freeze / unfreeze — is inherited unchanged.
    """

    def __init__(
        self,
        backend: BatteryDataBackend,
        title: str = "Battery Cycling Dashboard",
        field_names: Optional[List[str]] = None,
    ) -> None:
        self._backend = backend
        self._row_cls = backend.row_class

        # Datasets matching the current selection: [{idx, id, label, rows}].
        # Set before super().__init__ because it builds the Instances card,
        # which reads _matching_tests().
        self._matches: List[Dict[str, Any]] = []

        super().__init__(
            tests=[],
            cell_nodes={},
            cell_edges=[],
            cell_objects={},
            procedure_nodes={},
            procedure_edges=[],
            procedure_objects={},
            field_names=field_names if field_names is not None else backend.field_names,
            title=title,
        )

    # -- Field channels from the row class (no sample rows to scan) -----------

    def _detect_field_channels(
        self, tests: List[Any], field_names: List[str]
    ) -> Dict[str, _FieldChannel]:
        """Build one channel per field from ``row_class``' declared field types.

        The base class scans live rows for a first non-None value; here there
        are none up front, so read the characteristic class straight off the
        (pydantic v1) model field's ``type_``.
        """
        channels: Dict[str, _FieldChannel] = {}
        fields = getattr(self._row_cls, "__fields__", {})
        for f in field_names:
            fld = fields.get(f)
            if fld is not None and isinstance(getattr(fld, "type_", None), type):
                channels[f] = _FieldChannel(f, fld.type_)
        return channels

    # -- Lazy trees ----------------------------------------------------------

    def _build_cell_tree(self, nodes: Dict, edges: List) -> None:
        self._cell_tree_title = "Cell"
        self._cell_tree = self._make_lazy_tree(
            self._backend.cell_root, self._cell_tree_title
        )
        self._cell_tree.param.watch(self._on_lazy_tree_change, ["source"])
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
            self._backend.procedure_root, self._proc_tree_title
        )
        self._proc_tree.param.watch(self._on_lazy_tree_change, ["source"])
        self._proc_card = pn.Card(
            self._proc_tree,
            title="Procedures",
            collapsed=False,
            height=self._TREE_CARD_HEIGHT,
            scroll=True,
        )

    def _make_lazy_tree(self, root: TreeNode, col: str) -> Wunderbaum:
        """A Wunderbaum showing one collapsed, lazy root category.

        ``selectMode: "multi"`` keeps each instance checkbox independent, so a
        checkbox toggle maps one-to-one to a selected instance (no hierarchical
        cascade to reconcile against lazily-loaded children).
        """
        return Wunderbaum(
            source=[self._wb_node(root)],
            columns=[{"id": "*", "title": col, "width": "220px"}],
            options={"checkbox": True, "selectMode": "multi"},
            lazy_load_callback=self._lazy_children,
            tree_event_callback=None,
        )

    @staticmethod
    def _wb_node(node: TreeNode) -> Dict:
        """Convert a backend :class:`TreeNode` to a Wunderbaum node dict.

        Class nodes are collapsed, lazy and unchecked; instance nodes are
        checkable leaves. Custom keys (``kind`` / ``node_id``) are nested under
        ``data`` — the browser flattens them to the top level on round-trip,
        which :meth:`_checked_instances` tolerates.
        """
        if node.is_class:
            return {
                "title": node.label,
                "key": node.iri,
                "lazy": True,
                "checkbox": False,
                "expanded": False,
                "data": {"kind": "class", "node_id": node.iri},
            }
        return {
            "title": node.label,
            "key": node.iri,
            "checkbox": True,
            "selected": False,
            "data": {"kind": "instance", "node_id": node.iri},
        }

    def _lazy_children(self, node_key: str, _request: Dict) -> List[Dict]:
        """Wunderbaum lazy-load callback: children of the expanded category.

        ``node_key`` is the category IRI; the backend returns its sub-categories
        (further lazy class nodes) and instances (checkable leaves).
        """
        return [self._wb_node(child) for child in self._backend.children(node_key)]

    # -- Selection -> query -> plot ------------------------------------------

    def _on_lazy_tree_change(self, *_args: Any) -> None:
        if self._restoring:
            return
        self._selected_cell_ids = self._checked_instances(self._cell_tree.source)
        self._selected_proc_ids = self._checked_instances(self._proc_tree.source)
        self._run_query()
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

    def _run_query(self) -> None:
        """Repopulate ``_matches`` for the current cell + procedure selection.

        Delegates the whole cell×procedure lookup (pairing, de-duplication, row
        loading) to the backend; matching is authoritative, so
        :meth:`_matching_tests` just hands the list back.
        """
        self._matches = []
        if not self._selected_cell_ids or not self._selected_proc_ids:
            return
        datasets = self._backend.datasets(
            self._selected_cell_ids, self._selected_proc_ids
        )
        self._matches = [
            {"idx": i, "id": ds.id, "label": ds.label, "rows": ds.rows}
            for i, ds in enumerate(datasets)
        ]

    # -- Reuse the base plot pipeline, served from _matches ------------------

    def _matching_tests(self) -> List[Dict]:
        return self._matches

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
        re-ticked (a deliberate simplification for the lazy layer).
        """
        self._restoring = True
        try:
            self._selected_cell_ids = list(state.cell_ids)
            self._selected_proc_ids = list(state.proc_ids)
            self._instance_selections = dict(state.instance_selections)
            self._axis_map = dict(state.axis_map)
            self._unit_selections = dict(state.unit_selections)
            self._run_query()
            self._refresh_instances()
            self._sync_axis_grid()
            self._sync_unit_selects()
        finally:
            self._restoring = False
        self._build_figure()

    def _state_summary(self, state: Any) -> str:
        cells = [self._backend.page_name(c) for c in state.cell_ids]
        procs = [self._backend.page_name(p) for p in state.proc_ids]
        axis = state.axis_map
        y = axis.get("y1") or "—"
        if axis.get("y2"):
            y = f"{y}+{axis['y2']}"
        return (
            f"{', '.join(cells) or '—'} / {', '.join(procs) or '—'} · "
            f"{y} vs {axis.get('x') or '—'}"
        )
