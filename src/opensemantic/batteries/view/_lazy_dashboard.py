"""Lazy, backend-driven battery cycling dashboard — LazyBatteryDataView.

A :class:`~opensemantic.batteries.view.BatteryDataView` whose cell and procedure
trees are **lazy** and whose matching tests come from a pluggable
:class:`~opensemantic.batteries.view._backend.BatteryDataBackend` instead of a
pre-materialised OO-LD graph.

Unlike the base view (everything in memory up front), nothing is held eagerly:
each tree starts as a single collapsed root category and only asks the backend
for a node's children when the user expands it. Selecting cells + procedures
asks the backend for the matching cycling datasets, which are then plotted
through the base view's unchanged axis / unit / plot pipeline.

Category checkboxes cascade: ticking a category (e.g. ``CylindricalCell``)
eagerly walks the backend down to the leaves and selects **every** descendant
instance; unticking deselects them all. See :meth:`LazyBatteryDataView._on_tree_event`.

Everything data-source-specific lives behind the backend, so swapping the wiki
for a cache, a file or a test double is a new ``BatteryDataBackend`` and no
change here. See :mod:`opensemantic.batteries.view._backend`.
"""

from __future__ import annotations

import collections
import html
import logging
import time
import weakref
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

import panel as pn
from panelini.panels.wunderbaum import Wunderbaum

from opensemantic.batteries.view._backend import BatteryDataBackend, TreeNode
from opensemantic.batteries.view._battery_dashboard import (
    BatteryDataView,
    _FieldChannel,
)

_log = logging.getLogger("opensemantic.batteries.view")

# Newest log records kept in the log console
_LOG_CAPACITY = 500

# One log handler per session (Bokeh document, or ``None`` without a server), so a
# ``panel serve --dev`` reload replaces the handler of the previous view
_SESSION_HANDLERS: Dict[Any, "_PaneLogHandler"] = {}


class _PaneLogHandler(logging.Handler):
    """Logging handler that writes the newest records into a view's log pane.

    Holds the view by weak reference: when the view is collected, the handler
    removes itself from the logger on the next record.
    """

    def __init__(self, view: "LazyBatteryDataView") -> None:
        super().__init__()
        self._view = weakref.ref(view)
        self.lines: Deque[str] = collections.deque(maxlen=_LOG_CAPACITY)
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        view = self._view()
        if view is None:
            _log.removeHandler(self)
            return
        stamp = time.strftime("%H:%M:%S", time.localtime(record.created))
        text = html.escape(
            f"{stamp}.{int(record.msecs):03d} {record.levelname:<7} "
            f"{self.format(record)}"
        )
        self.lines.appendleft(f"<div>{text}</div>")
        self.refresh()

    def refresh(self) -> None:
        view = self._view()
        if view is not None:
            view._log_pane.object = (
                "<div style='font-family:monospace;white-space:pre-wrap'>"
                + "".join(self.lines)
                + "</div>"
                if self.lines
                else ""
            )

    def clear(self) -> None:
        self.lines.clear()
        self.refresh()


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

    Everything downstream of the selection (axis grid, unit dropdowns, plot)
    is inherited unchanged.
    """

    def __init__(
        self,
        backend: BatteryDataBackend,
        title: str = "Battery Cycling Dashboard",
        field_names: Optional[List[str]] = None,
        show_log: bool = False,
    ) -> None:
        self._backend = backend
        self._row_cls = backend.row_class

        # ``show_log`` shows the log card (records of the
        # ``opensemantic.batteries.view`` logger, newest first) and a status line
        # above the plots. The status texts are kept in ``_status_history``.
        self._show_log = show_log
        self._log_handler: Optional[_PaneLogHandler] = None
        self._status_history: List[str] = []
        self._status_pane = pn.pane.Markdown(
            "", margin=(5, 10), visible=show_log, sizing_mode="stretch_width"
        )
        # cells without a parent category already reported
        self._warned_no_parent: Set[str] = set()

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
        # class IRI -> (descendant class IRIs, descendant instance IRIs), memoised;
        # the recursive backend walk is the expensive part of a cascade.
        self._descendant_cache: Dict[str, Tuple[List[str], List[str]]] = {}
        # node IRI -> IRI of its direct parent category, recorded while the trees
        # are walked or expanded (the first parent found wins)
        self._parent_of: Dict[str, str] = {}
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
        # Categories already expand-requested in the browser, per tree. The
        # cascade expands one *loaded* category per echo round (see
        # :meth:`_queue_expansion`); this tracks which are done so it advances
        # through the subtree instead of re-expanding the same node forever.
        self._expanded_classes: Dict[str, Set[str]] = {"cell": set(), "proc": set()}

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
        if show_log:
            self._attach_log_handler()

    # -- Log console and status line (opt-in) --------------------------------

    def _build_log_console(self) -> None:
        super()._build_log_console()
        if not self._show_log:
            return
        self._log_card.visible = True
        clear = pn.widgets.Button(name="Clear", button_type="default", width=80)
        clear.on_click(lambda _event: self._clear_log())
        self._log_card.insert(0, clear)

    def _attach_log_handler(self) -> None:
        """Attach one pane handler; replace the one of a previous view."""
        if self._log_handler is not None:
            return
        session = pn.state.curdoc
        key = id(session) if session is not None else None
        previous = _SESSION_HANDLERS.pop(key, None)
        if previous is not None:
            _log.removeHandler(previous)
        handler = _PaneLogHandler(self)
        _log.addHandler(handler)
        if _log.level == logging.NOTSET:  # a configured level is respected
            _log.setLevel(logging.INFO)
        _SESSION_HANDLERS[key] = handler
        self._log_handler = handler

    def _clear_log(self) -> None:
        if self._log_handler is not None:
            self._log_handler.clear()

    def _set_status(self, text: str) -> None:
        self._status_history.append(text)
        self._status_pane.object = text

    @property
    def main_cards(self) -> List[Any]:
        cards = super().main_cards
        return [self._status_pane] + cards if self._show_log else cards

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
        self._cell_tree = self._make_lazy_tree("cell", self._backend.cell_root, "Cell")
        self._cell_card = pn.Card(
            self._cell_tree,
            title="Cells",
            collapsed=False,
        )

    def _build_procedure_card(self, nodes: Dict, edges: List) -> None:
        self._proc_tree = self._make_lazy_tree(
            "proc", self._backend.procedure_root, "Procedure"
        )
        self._proc_card = pn.Card(
            self._proc_tree,
            title="Procedures",
            collapsed=False,
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
            stylesheets=self._tree_stylesheets(self._TREE_CARD_HEIGHT),
            lazy_load_callback=lambda key, req, w=which: self._lazy_children(
                w, key, req
            ),
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
        children = self._backend.children(node_key)
        for child in children:
            self._parent_of.setdefault(child.iri, node_key)
        return [self._wb_node(which, child) for child in children]

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
        new_sel: Set[str] = set()
        present: Set[str] = set()
        self._collect_selected(event.new or [], new_sel, present)
        old_sel = self._source_selected[which]

        # Accumulate every browser-side action for this change into one list and
        # issue a *single* ``batch_update``: the widget's ``_tree_action`` is a
        # single param, so back-to-back ``select_node`` / ``expand_node`` calls
        # in one callback would coalesce to only the last (the same Bokeh
        # same-tick collapse that drops ``select`` events). One batch is atomic.
        actions: List[Dict] = []
        changed = False
        if new_sel != old_sel:
            self._source_selected[which] = new_sel
            changed = self._toggle_keys(which, new_sel - old_sel, True, actions)
            changed |= self._toggle_keys(which, old_sel - new_sel, False, actions)

        # Reveal the cascade by expanding selected categories — but only ones
        # already *loaded* in the browser (present in ``source``), and only
        # **one** per round. Expanding an unloaded lazy node fires a lazy-load
        # request through the single ``_lazy_request`` slot; expanding several at
        # once would coalesce those requests and load only the last. Expanding
        # one loaded node reveals its (loaded) children, which the next echo
        # round then expands — serialising the lazy loads so none is dropped.
        self._queue_expansion(which, present, actions)

        if actions:
            self._tree(which).batch_update(actions)

        # Only re-query when the authoritative instance set actually moved. A
        # cascade's own actions echo back through ``source``; those follow-up
        # rounds re-toggle nodes already in the set (idempotent), so skipping the
        # requery there stops a feedback loop without losing any change.
        if changed:
            self._apply_selection()

    def _queue_expansion(
        self, which: str, present: Set[str], actions: List[Dict]
    ) -> None:
        """Append an ``expandNode`` for one not-yet-expanded, loaded category."""
        for iri in self._sel_classes[which]:
            if iri in present and iri not in self._expanded_classes[which]:
                self._expanded_classes[which].add(iri)
                actions.append({"action": "expandNode", "key": iri, "expanded": True})
                return

    def _collect_selected(
        self, nodes: List[Dict], selected: Set[str], present: Set[str]
    ) -> None:
        """Gather each node's key into ``present`` and ``selected`` keys too.

        Also records each visited node's ``kind`` (``getSerializableSource``
        spreads a node's ``data`` — which carries ``kind`` — onto the node) so a
        toggled key can be routed as class vs instance without a separate lookup.
        ``present`` (every key in the tree, loaded or not) tells the expansion
        queue which categories the browser has actually materialised.
        """
        for node in nodes:
            key = node.get("key")
            if key:
                present.add(key)
                kind = node.get("kind") or (node.get("data") or {}).get("kind")
                if kind:
                    self._node_kind[key] = kind
                if node.get("selected"):
                    selected.add(key)
            children = node.get("children")
            if children:
                self._collect_selected(children, selected, present)

    def _toggle_keys(
        self, which: str, keys: Set[str], flag: bool, actions: List[Dict]
    ) -> bool:
        """Apply a set of just-(de)selected keys, appending browser actions.

        A class key cascades into every descendant category *and* instance
        (loading the subtree server-side); the sets are updated authoritatively
        and ``actions`` gets a ``selectNode`` per descendant so already-loaded
        checkboxes flip live (unloaded ones render from the sets when their
        subtree is later expanded — see :meth:`_wb_node`). Revealing the subtree
        by expansion is handled separately, one node per round, in
        :meth:`_queue_expansion`. An instance key just flips itself. Returns True
        iff this tree's authoritative instance set changed.
        """
        if not keys:
            return False
        instances = self._sel_instances[which]
        classes = self._sel_classes[which]
        expanded = self._expanded_classes[which]
        before = set(instances)
        for key in keys:
            if self._node_kind.get(key, "instance") == "class":
                (classes.add if flag else classes.discard)(key)
                sub_classes, sub_instances = self._descendants(key)
                for iri in sub_instances:
                    (instances.add if flag else instances.discard)(iri)
                    actions.append(
                        {"action": "selectNode", "key": iri, "selected": flag}
                    )
                for iri in sub_classes:
                    (classes.add if flag else classes.discard)(iri)
                    actions.append(
                        {"action": "selectNode", "key": iri, "selected": flag}
                    )
                if not flag:
                    # Forget expansions so re-selecting re-reveals the subtree.
                    expanded.discard(key)
                    expanded.difference_update(sub_classes)
            else:
                (instances.add if flag else instances.discard)(key)
        return instances != before

    def _descendant_instance_iris(self, class_iri: str) -> List[str]:
        """All instance IRIs anywhere below a category (see :meth:`_descendants`)."""
        return self._descendants(class_iri)[1]

    def _descendants(self, class_iri: str) -> Tuple[List[str], List[str]]:
        """``(sub_class_iris, instance_iris)`` anywhere below a category.

        Recursively calls the backend's ``children`` (forcing the lazy load
        server-side rather than waiting on the browser), collecting every
        descendant *category* IRI (so the cascade can expand them) and every
        *instance* leaf IRI (so the cascade can select them). Memoised per
        category; also records each visited node's kind in :attr:`_node_kind`.
        """
        if class_iri in self._descendant_cache:
            return self._descendant_cache[class_iri]

        sub_classes: List[str] = []
        instances: List[str] = []
        seen: Set[str] = set()

        def walk(iri: str) -> None:
            for child in self._backend.children(iri):
                if child.iri in seen:
                    continue  # guard against cycles / diamond hierarchies
                seen.add(child.iri)
                self._node_kind[child.iri] = child.kind
                self._parent_of.setdefault(child.iri, iri)
                if child.kind == "instance":
                    instances.append(child.iri)
                else:
                    sub_classes.append(child.iri)
                    walk(child.iri)

        walk(class_iri)
        self._descendant_cache[class_iri] = (sub_classes, instances)
        return self._descendant_cache[class_iri]

    def _apply_selection(self) -> None:
        """Push the selection sets into the query and re-plot.

        With ``show_log`` the status line shows "Querying" first. Under a
        server (a Bokeh document is present) the query and the plots then run
        in the next tick, so the browser shows the status before the work.
        """
        self._selected_cell_ids = list(self._sel_instances["cell"])
        self._selected_proc_ids = list(self._sel_instances["proc"])
        n_cells = len(self._selected_cell_ids)
        n_procs = len(self._selected_proc_ids)
        if self._show_log and n_cells and n_procs:
            self._set_status(
                f"Querying datasets for {n_cells} cells x {n_procs} procedures..."
            )
            doc = pn.state.curdoc
            if doc is not None:
                doc.add_next_tick_callback(self._finish_selection)
                return
        self._finish_selection()

    def _finish_selection(self) -> None:
        """Run the query for the current selection, then refresh and re-plot."""
        began = time.perf_counter()
        if not self._selected_cell_ids or not self._selected_proc_ids:
            if self._show_log:
                self._set_status("No query: select at least one cell and one procedure")
            self._run_query()
            self._refresh_instances()
            self._build_figure()
            return
        failed_before = getattr(self._backend, "failed_queries", 0)
        try:
            self._run_query()
        except Exception as exc:  # noqa: BLE001 - keep the dashboard alive
            _log.warning("dataset query failed: %s", exc)
            self._matches = []
            if self._show_log:
                self._set_status(f"Failed: {exc}")
        else:
            if self._show_log:
                failed = getattr(self._backend, "failed_queries", 0) - failed_before
                note = f" ({failed} wiki queries failed, see log)" if failed else ""
                self._set_status(
                    f"Done: {len(self._matches)} datasets in "
                    f"{time.perf_counter() - began:.1f} s{note}"
                )
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
        began = time.perf_counter()
        datasets = self._backend.datasets(
            self._selected_cell_ids, self._selected_proc_ids
        )
        _log.info(
            "selection: cells %s, procedures %s, %d datasets in %.2f s",
            self._labelled(self._selected_cell_ids),
            self._labelled(self._selected_proc_ids),
            len(datasets),
            time.perf_counter() - began,
        )
        self._matches = [
            {
                "idx": i,
                "id": ds.id,
                "label": ds.label,
                "rows": ds.rows,
                "cell_iri": ds.cell_iri,
                "procedure_iri": ds.procedure_iri,
            }
            for i, ds in enumerate(datasets)
        ]

    def _labelled(self, iris: List[str]) -> List[str]:
        """``iri (label)`` for each IRI, for the log."""
        return [f"{iri} ({self._backend.page_name(iri)})" for iri in iris]

    # -- Reuse the base plot pipeline, served from _matches ------------------

    def _matching_tests(self) -> List[Dict]:
        return self._matches

    def _dataset_records(self) -> List[Dict]:
        """One record per ticked match, from the backend's ``Dataset`` fields.

        The cell category is the direct parent category recorded while the cell
        tree was walked; the labels come from ``backend.page_name``.
        """
        records = []
        for m in self._matching_tests():
            if not self._instance_selections.get(m["idx"], True):
                continue
            cell_iri = m.get("cell_iri")
            proc_iri = m.get("procedure_iri")
            parent = self._parent_of.get(cell_iri) if cell_iri else None
            if cell_iri and not parent and cell_iri not in self._warned_no_parent:
                self._warned_no_parent.add(cell_iri)
                _log.warning("cell %s has no parent category", cell_iri)
            records.append(
                {
                    "key": m["id"],
                    "label": m["label"],
                    "cell_iri": cell_iri,
                    "cell_category": (
                        (parent, self._backend.page_name(parent)) if parent else None
                    ),
                    "procedure_iri": proc_iri,
                    "procedure_label": (
                        self._backend.page_name(proc_iri) if proc_iri else None
                    ),
                }
            )
        return records

    def _unplotted_empty_datasets(self) -> int:
        return len(
            [
                m
                for m in self._matching_tests()
                if self._instance_selections.get(m["idx"], True) and not m["rows"]
            ]
        )

    def _resolve_traces(self) -> List[Dict]:
        traces: List[Dict] = []
        for m in self._matching_tests():
            if not self._instance_selections.get(m["idx"], True):
                continue
            if m["rows"]:
                traces.append({"label": m["label"], "rows": m["rows"], "key": m["id"]})
        return traces
