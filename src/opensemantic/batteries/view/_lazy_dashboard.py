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

Category checkboxes cascade: ticking a category (e.g. ``CylindricalCell``)
eagerly walks the backend down to the leaves and selects **every** descendant
instance; unticking deselects them all. See :meth:`LazyBatteryDataView._on_tree_event`.

Everything data-source-specific lives behind the backend, so swapping the wiki
for a cache, a file or a test double is a new ``BatteryDataBackend`` and no
change here. See :mod:`opensemantic.batteries.view._backend`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

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
    * selection is driven by the widget's per-node ``select`` events (see
      :meth:`_on_tree_event`) into an authoritative in-Python selection set —
      not read back from the serialised ``source`` — because a cascade can
      select instances that live in subtrees the browser has never loaded;
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

        # Authoritative selection state, per tree ("cell" / "proc"). The browser
        # ``source`` only holds nodes the user has actually expanded, but a
        # category cascade can select instances in never-loaded subtrees, so the
        # truth lives here (populated from ``select`` events), not in ``source``.
        self._sel_instances: Dict[str, Set[str]] = {"cell": set(), "proc": set()}
        self._sel_classes: Dict[str, Set[str]] = {"cell": set(), "proc": set()}

        # key -> "class" | "instance", learned as nodes are built or walked, so a
        # source diff (which yields only keys) can tell the two apart.
        self._node_kind: Dict[str, str] = {}
        # class IRI -> flat list of all descendant instance IRIs (memoised; the
        # recursive backend walk is the expensive part of a cascade).
        self._descendant_cache: Dict[str, List[str]] = {}
        # Per-tree set of node keys currently ticked *in the browser* (i.e. present
        # with ``selected: true`` in the serialised ``source``). Diffing this on
        # every ``source`` change is how a user toggle is detected — see
        # :meth:`_on_source_change`. Selection is read from ``source`` and **not**
        # from per-node ``select`` events: those arrive via the widget's single
        # ``_event_data`` slot, and a checkbox click fires click→deactivate→
        # select→activate within one JS tick, so Bokeh coalesces the writes and
        # only the last (``activate``) reaches Python — the ``select`` is dropped.
        # ``source`` is full-state and idempotent, so coalescing can't lose it.
        self._source_selected: Dict[str, Set[str]] = {"cell": set(), "proc": set()}

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
            "cell", self._backend.cell_root, self._cell_tree_title
        )
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
            "proc", self._backend.procedure_root, self._proc_tree_title
        )
        self._proc_card = pn.Card(
            self._proc_tree,
            title="Procedures",
            collapsed=False,
            height=self._TREE_CARD_HEIGHT,
            scroll=True,
        )

    def _make_lazy_tree(self, which: str, root: TreeNode, col: str) -> Wunderbaum:
        """A Wunderbaum showing one collapsed, lazy root category.

        ``selectMode: "multi"`` keeps every checkbox independent — the
        category-cascade is done explicitly in Python (see
        :meth:`_on_source_change`), not via the library's ``"hier"``
        propagation, so it can reach instances in subtrees the browser hasn't
        loaded yet. The lazy loader is bound to *which* tree (``"cell"`` /
        ``"proc"``); selection is picked up by watching the widget's ``source``
        param (the reliable JS→Python channel) rather than its lossy per-node
        ``select`` events.
        """
        tree = Wunderbaum(
            source=[self._wb_node(which, root)],
            columns=[{"id": "*", "title": col, "width": "220px"}],
            options={"checkbox": True, "selectMode": "multi"},
            lazy_load_callback=lambda key, req, w=which: self._lazy_children(w, key, req),
        )
        tree.param.watch(
            lambda event, w=which: self._on_source_change(w, event), ["source"]
        )
        return tree

    def _tree(self, which: str) -> Wunderbaum:
        return self._cell_tree if which == "cell" else self._proc_tree

    def _wb_node(self, which: str, node: TreeNode) -> Dict:
        """Convert a backend :class:`TreeNode` to a Wunderbaum node dict.

        Both class and instance nodes are checkable; a class node lazily loads
        and cascades on toggle. Each node's ``selected`` flag is rendered from
        the authoritative Python selection sets, so a subtree loaded *after* a
        cascade still shows the right check state. The node's ``kind`` is
        recorded in :attr:`_node_kind` so a later ``select`` event (key + flag
        only) can be routed correctly.
        """
        self._node_kind[node.iri] = node.kind
        if node.is_class:
            return {
                "title": node.label,
                "key": node.iri,
                "lazy": True,
                "checkbox": True,
                "expanded": False,
                "selected": node.iri in self._sel_classes[which],
                "data": {"kind": "class", "node_id": node.iri},
            }
        return {
            "title": node.label,
            "key": node.iri,
            "checkbox": True,
            "selected": node.iri in self._sel_instances[which],
            "data": {"kind": "instance", "node_id": node.iri},
        }

    def _lazy_children(self, which: str, node_key: str, _request: Dict) -> List[Dict]:
        """Wunderbaum lazy-load callback: children of the expanded category.

        ``node_key`` is the category IRI; the backend returns its sub-categories
        (further lazy class nodes) and instances (checkable leaves), each
        rendered against *which* tree's current selection.
        """
        return [
            self._wb_node(which, child)
            for child in self._backend.children(node_key)
        ]

    # -- Selection -> query -> plot ------------------------------------------

    def _on_source_change(self, which: str, event: Any) -> None:
        """React to a tree's ``source`` change by diffing its ticked nodes.

        Wunderbaum echoes its full node tree back into ``source`` whenever the
        selection changes; comparing the set of ``selected`` keys against the
        last-seen set yields exactly the nodes the user just toggled. An
        instance toggle flips that one IRI; a **category** toggle cascades — the
        whole subtree below it is lazily loaded down to the leaves and every
        descendant instance is set to match. Ticking ``CylindricalCell`` thus
        selects ``CellA`` + ``CellB``; unticking it clears them.
        """
        if self._restoring:
            return
        new_sel: Set[str] = set()
        self._collect_selected(event.new or [], new_sel)
        old_sel = self._source_selected[which]
        if new_sel == old_sel:
            return
        self._source_selected[which] = new_sel

        changed = self._toggle_keys(which, new_sel - old_sel, True)
        changed |= self._toggle_keys(which, old_sel - new_sel, False)

        # Only re-query when the authoritative instance set actually moved. A
        # cascade's own ``select_node`` calls echo back through ``source``; those
        # rounds re-toggle instances already in the set (idempotent), so skipping
        # the requery there stops a feedback loop without losing any change.
        if changed:
            self._apply_selection()

    def _collect_selected(self, nodes: List[Dict], acc: Set[str]) -> None:
        """Recursively gather ``selected`` node keys from a serialised source.

        Also records each visited node's ``kind`` (``getSerializableSource``
        spreads a node's ``data`` — which carries ``kind`` — onto the node) so a
        toggled key can be routed as class vs instance without a separate lookup.
        """
        for node in nodes:
            key = node.get("key")
            if key:
                kind = node.get("kind") or (node.get("data") or {}).get("kind")
                if kind:
                    self._node_kind[key] = kind
                if node.get("selected"):
                    acc.add(key)
            children = node.get("children")
            if children:
                self._collect_selected(children, acc)

    def _toggle_keys(self, which: str, keys: Set[str], flag: bool) -> bool:
        """Apply a set of just-(de)selected keys; return whether instances moved.

        A class key cascades into every descendant instance (loading the subtree
        server-side) and best-effort-syncs any *already loaded* descendant
        checkbox in the browser; an instance key flips itself. Returns True iff
        this tree's authoritative instance set changed.
        """
        if not keys:
            return False
        instances = self._sel_instances[which]
        classes = self._sel_classes[which]
        tree = self._tree(which)
        before = set(instances)
        for key in keys:
            if self._node_kind.get(key, "instance") == "class":
                (classes.add if flag else classes.discard)(key)
                for iri in self._descendant_instance_iris(key):
                    (instances.add if flag else instances.discard)(iri)
                    # Visual sync of already-loaded descendant checkboxes; a no-op
                    # for keys the browser hasn't loaded (those render from the
                    # set when their subtree is later expanded).
                    tree.select_node(iri, flag)
            else:
                (instances.add if flag else instances.discard)(key)
        return instances != before

    def _descendant_instance_iris(self, class_iri: str) -> List[str]:
        """All instance IRIs anywhere below a category, loading the subtree.

        Recursively calls the backend's ``children`` (forcing the lazy load
        server-side rather than waiting on the browser) and collects every
        ``instance`` leaf. Memoised per category; also records each visited
        node's kind in :attr:`_node_kind`.
        """
        if class_iri in self._descendant_cache:
            return self._descendant_cache[class_iri]

        found: List[str] = []
        seen: Set[str] = set()

        def walk(iri: str) -> None:
            for child in self._backend.children(iri):
                if child.iri in seen:
                    continue  # guard against cycles / diamond hierarchies
                seen.add(child.iri)
                self._node_kind[child.iri] = child.kind
                if child.kind == "instance":
                    found.append(child.iri)
                else:
                    walk(child.iri)

        walk(class_iri)
        self._descendant_cache[class_iri] = found
        return found

    def _apply_selection(self) -> None:
        """Push the selection sets into the query and re-plot."""
        self._selected_cell_ids = list(self._sel_instances["cell"])
        self._selected_proc_ids = list(self._sel_instances["proc"])
        self._run_query()
        self._refresh_instances()
        self._build_figure()

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
            self._sel_instances = {
                "cell": set(state.cell_ids),
                "proc": set(state.proc_ids),
            }
            # Category-checked state isn't persisted (only instances are); the
            # tree isn't re-ticked on restore anyway, so start it clean.
            self._sel_classes = {"cell": set(), "proc": set()}
            # Forget the browser's last-seen ticks too, so the next real toggle
            # diffs against a clean slate rather than the pre-restore selection.
            self._source_selected = {"cell": set(), "proc": set()}
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
