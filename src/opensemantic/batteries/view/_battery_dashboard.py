"""Battery cycling data view — BatteryDataView.

Sidebar layout (top to bottom):
  - Cell card:      cell tree (SubClassOf + HasType hierarchy, hierarchical
                    checkbox); fixed height, scrolls
  - Procedure card: procedure tree; same fixed height as the cell card, scrolls
  - Instances card: live list of test runs matching the cell+procedure
                    selection; per-instance checkbox toggles whether it is plotted
  - Axes & Units card: the axis/unit grid (below Instances)
  - Config card

Axes & Units grid
  rows = data fields (test_time, voltage, current, ...)
  cols = x | y1 | y2 | Unit
  Per-column radio: exactly one field per axis at a time.
  Unit column: dropdown for each field, auto-detected from opensemantic type.

Plot
  Single Bokeh figure, left y1 axis + optional right y2 axis.
  Both axis ranges are derived from data — 0 is never forced as baseline.

Freeze / unfreeze / delete
  Every plot — the live *active* one and each frozen snapshot — is a record in
  a single ordered ``self._plots`` list rendered as its own panel inside
  ``self._plots_col``. Exactly one record has ``active=True``: it renders live
  with a blue "Freeze plot" button; the rest are static snapshots with an
  "Unfreeze" button. Each plot's full state is a :class:`PlotState`. Freezing
  drops a snapshot (all tab selections + the static figure) directly below the
  active record (one slot down), which stays active in place and keeps tracking
  the sidebar. Unfreezing swaps roles *in place* — the clicked record becomes
  active where it sits (its ``PlotState`` restored into every tab, including the
  tree checkboxes) and the previously-active record freezes where it sits — so
  the visible order never shuffles. Every plot also has a "Delete" button:
  deleting a frozen plot removes just that snapshot; deleting the active plot
  promotes a neighbour (or leaves a fresh empty plot if it was the last one).
  ``_build_figure`` rebuilds only the active panel and reassigns
  ``_plots_col.objects`` wholesale so the browser repaints immediately.

Uses BaseDataView mixin for shared plot / log / config cards.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import panel as pn
from bokeh.models import ColumnDataSource, LinearAxis, Range1d
from bokeh.plotting import figure as bk_figure
from panelini import Panelini
from panelini.panels.jsoneditor import JsonEditor
from panelini.panels.wunderbaum import Wunderbaum
from pydantic import BaseModel, Field

from opensemantic.base.view import (
    COLORS,
    BaseDataView,
    BaseViewConfig,
    get_available_units,
)

from opensemantic.batteries.view._battery_utils import (
    build_oold_tree_source,
    get_checked_instance_ids,
    set_selected_instances,
)


class PlotState(BaseModel):
    """Serializable snapshot of everything that defines a single plot.

    One ``PlotState`` fully describes what a plot shows: which cell and
    procedure instances are selected, which of the matching test runs are
    toggled on, the field-per-axis assignment, and the per-field unit choice.
    It is the unit of persistence for the freeze / unfreeze feature — the
    active plot captures one on freeze and a frozen plot restores its own back
    into every widget on unfreeze.

    Only plain, JSON-friendly values are stored (node-id / enum-member-name
    strings, ints, bools), never live OSW model objects, so a state is trivial
    to copy, compare and (if ever needed) serialise.
    """

    cell_ids: List[str] = Field(default_factory=list)
    proc_ids: List[str] = Field(default_factory=list)
    instance_selections: Dict[int, bool] = Field(default_factory=dict)
    axis_map: Dict[str, Optional[str]] = Field(default_factory=dict)
    unit_selections: Dict[str, str] = Field(default_factory=dict)

# ---------------------------------------------------------------------------
# Field-channel wrapper
# ---------------------------------------------------------------------------


class _FieldChannel:
    """Channel-like adapter around a row field's characteristic class.

    Row fields (``test_time``, ``voltage``, ``current``, ...) are typed
    :class:`QuantityValue` characteristics (``Time``, ``Voltage``,
    ``ElectricCurrent``, ...). Wrapping the field's characteristic class in a
    channel-like object (carrying ``__iris__``) lets this view reuse the same
    unit machinery the process/datatool views use — ``get_available_units`` for
    discovery and ``BaseDataView._numeric`` (``value.to_unit(...)``) for
    conversion — instead of a hand-maintained unit-factor table.
    """

    def __init__(self, name: str, char_cls: type):
        self.name = name
        self.uuid = name
        self.label = None
        self.unit = None
        self.description = None
        self._characteristic_class = char_cls
        self.__iris__ = (
            {"characteristic": char_cls.get_cls_iri()}
            if hasattr(char_cls, "get_cls_iri")
            else {}
        )


class BatteryDataView(BaseDataView):
    """In-memory cycling dataset viewer.

    Parameters
    ----------
    tests
        List of test-run objects. Each must expose:
          .device_under_test  list of cell objects
          .test_procedure     list of TestProcedureItem, each carrying a
                              ``test_procedure_instance`` OSW IRI string
                              (legacy: a single ``.protocol`` procedure object)
          .output             dataset (or list of datasets); the first one's
                              ``.data`` is the list of rows (CyclingDataRow)
    cell_nodes / cell_edges
        OO-LD graph for the cell class hierarchy + instances.
    cell_objects
        Mapping node_id → Python cell object.
    procedure_nodes / procedure_edges
        OO-LD graph for the procedure class hierarchy + instances.
    procedure_objects
        Mapping node_id → Python procedure object.
    field_names
        Row field names shown in the axis grid.
        Auto-detected from the first test's row class if omitted.
    """

    # Fixed height for the Cell and Procedure tree cards, so they sit at the
    # same height in the sidebar and their trees scroll rather than resize.
    _TREE_CARD_HEIGHT = 260

    def __init__(
        self,
        tests: List[Any],
        cell_nodes: Dict[str, Dict],
        cell_edges: List[Dict],
        cell_objects: Dict[str, Any],
        procedure_nodes: Dict[str, Dict],
        procedure_edges: List[Dict],
        procedure_objects: Dict[str, Any],
        field_names: Optional[List[str]] = None,
        title: str = "Battery Cycling Dashboard",
        embeddable: bool = False,
    ):
        self._tests = tests
        self._config = BaseViewConfig()
        self._title = title
        self._embeddable = embeddable
        self._cell_objects = cell_objects
        self._procedure_objects = procedure_objects

        if field_names is None:
            field_names = self._detect_fields(tests)
        self._field_names = field_names

        # Wrap each field's characteristic class in a channel-like adapter so
        # the shared QuantityValue unit machinery works on it directly.
        self._field_channels: Dict[str, _FieldChannel] = self._detect_field_channels(
            tests, field_names
        )

        # Per-field unit options (symbol → enum-member name) and current
        # selection (enum-member name), discovered from the QuantityValue's
        # unit enum instead of a hard-coded table.
        self._field_unit_options: Dict[str, Dict[str, str]] = {}
        self._unit_selections: Dict[str, str] = {}
        for f in field_names:
            ch = self._field_channels.get(f)
            units = get_available_units(ch) if ch is not None else []
            if units:
                self._field_unit_options[f] = {u["symbol"]: u["name"] for u in units}
                self._unit_selections[f] = units[0]["name"]

        # Global axis assignment: field name per axis (None = axis unused)
        self._axis_map: Dict[str, Optional[str]] = {
            "x":  field_names[0] if len(field_names) > 0 else None,
            "y1": field_names[1] if len(field_names) > 1 else None,
            "y2": None,
        }

        # BaseDataView contract
        self._groups: Dict[str, List] = {}
        self._unit_controls = pn.Column()

        # Selection state
        self._selected_cell_ids: List[str] = []
        self._selected_proc_ids: List[str] = []

        # Per-instance plot toggle, keyed by index into ``self._tests``.
        # Missing key => checked (plotted) by default.
        self._instance_selections: Dict[int, bool] = {}

        # Checkbox widgets keyed by (field, axis) — radio enforcement via flag
        self._axis_checkboxes: Dict[Tuple[str, str], pn.widgets.Checkbox] = {}
        self._updating_checkboxes: bool = False

        # Unit dropdowns keyed by field — kept so a restored state can be
        # written back into the widgets on unfreeze.
        self._unit_selects: Dict[str, pn.widgets.Select] = {}

        # Plot stack. One ordered list holds every plot; exactly one carries
        # ``active=True`` (the plot the sidebar selection drives — rendered live
        # with a blue "Freeze" button), the rest are frozen snapshots (rendered
        # static with an "Unfreeze" button). ``_plots`` / ``_plots_col`` are
        # created in ``_build_plot``. ``_restoring`` guards widget-sync watchers
        # while a saved state is being written back on unfreeze / delete.
        self._plot_counter: int = 0
        self._restoring: bool = False

        self._build_cell_tree(cell_nodes, cell_edges)
        self._build_procedure_card(procedure_nodes, procedure_edges)
        self._build_instances_card()
        self._build_axis_card()
        self._build_plot()
        self._build_log_console()
        self._build_config_editor()
        self._build_layout()

    # -- Field / type detection ----------------------------------------------

    @staticmethod
    def _detect_fields(tests: List[Any]) -> List[str]:
        for test in tests:
            output = BatteryDataView._test_output(test)
            rows = getattr(output, "data", []) if output else []
            if not rows:
                continue
            row = rows[0]
            if hasattr(row, "model_fields"):
                return [f for f in row.model_fields if f != "type"]
            return [k for k in vars(row) if not k.startswith("_") and k != "type"]
        return []

    @staticmethod
    def _detect_field_channels(
        tests: List[Any], field_names: List[str]
    ) -> Dict[str, "_FieldChannel"]:
        """Build one ``_FieldChannel`` per field from its characteristic class.

        Scans rows for the first non-None value of each field and captures its
        (QuantityValue) class, so units and conversions come from the value's
        own type rather than a name-based lookup table.
        """
        channels: Dict[str, _FieldChannel] = {}
        for test in tests:
            output = BatteryDataView._test_output(test)
            rows = getattr(output, "data", []) if output else []
            for row in rows:
                for f in field_names:
                    if f in channels:
                        continue
                    v = getattr(row, f, None)
                    if v is not None:
                        channels[f] = _FieldChannel(f, type(v))
                if len(channels) == len(field_names):
                    return channels
        return channels

    # -- Cell tree -----------------------------------------------------------

    def _make_tree(self, source: List[Dict], title: str) -> Wunderbaum:
        """Build a checkbox Wunderbaum over *source* wired to ``_on_tree_change``.

        Used both for the initial trees and to rebuild a tree on restore — see
        :meth:`_restore_tree` for why a rebuild (not a ``source`` reassignment)
        is what makes the browser checkboxes repaint.
        """
        tree = Wunderbaum(
            source=source,
            columns=[{"id": "*", "title": title, "width": "220px"}],
            options={"checkbox": True, "selectMode": "hier"},
        )
        tree.param.watch(self._on_tree_change, ["source"])
        return tree

    def _build_cell_tree(self, nodes: Dict, edges: List) -> None:
        self._cell_tree_title = "Cell"
        # Keep a pristine, plain-data copy of the tree source. A restore rebuilds
        # from *this*, never from the live ``tree.source`` — once the browser has
        # edited a Wunderbaum, its ``source`` holds live model references that
        # ``copy.deepcopy`` (in ``set_selected_instances``) would drag a whole
        # Tornado ``IOLoop`` into, blowing up. ``set_selected_instances`` returns
        # a fresh deep copy, so the widget never shares the pristine object.
        self._cell_tree_source = build_oold_tree_source(nodes, edges)
        self._cell_tree = self._make_tree(
            set_selected_instances(self._cell_tree_source, set()),
            self._cell_tree_title,
        )
        self._cell_card = pn.Card(
            self._cell_tree,
            title="Cells",
            collapsed=False,
            height=self._TREE_CARD_HEIGHT,
            scroll=True,
        )

    # -- Procedure card (tree only) ------------------------------------------

    def _build_procedure_card(self, nodes: Dict, edges: List) -> None:
        self._proc_tree_title = "Procedure"
        # Pristine plain-data source — see _build_cell_tree for why.
        self._proc_tree_source = build_oold_tree_source(nodes, edges)
        self._proc_tree = self._make_tree(
            set_selected_instances(self._proc_tree_source, set()),
            self._proc_tree_title,
        )
        self._proc_card = pn.Card(
            self._proc_tree,
            title="Procedures",
            collapsed=False,
            height=self._TREE_CARD_HEIGHT,
            scroll=True,
        )

    # -- Axes & units card ---------------------------------------------------

    def _build_axis_card(self) -> None:
        """Standalone card for the x / y1 / y2 axis + unit grid.

        Lives below the Instances card (see ``sidebar_cards``). Building the grid
        wires up ``_axis_checkboxes`` and ``_unit_selects``.
        """
        self._axis_card = pn.Card(
            self._build_axis_grid(),
            title="Axes & Units",
            collapsed=False,
        )

    def _build_axis_grid(self) -> pn.Column:
        """Grid: rows = fields, cols = x | y1 | y2 | Unit.

        Radio enforcement per column: the _on_checkbox_change handler
        re-syncs all checkboxes from _axis_map under a flag so watchers
        that fire during the bulk update are no-ops.
        """
        COL_W = 36
        LABEL_W = 100

        header = pn.Row(
            pn.pane.Markdown("**Field**",  width=LABEL_W),
            pn.pane.Markdown("**x**",      width=COL_W),
            pn.pane.Markdown("**y1**",     width=COL_W),
            pn.pane.Markdown("**y2**",     width=COL_W),
            pn.pane.Markdown("**Unit**",   width=80),
        )
        rows: List[Any] = [header]

        for field in self._field_names:
            cbs = []
            for ax in ("x", "y1", "y2"):
                cb = pn.widgets.Checkbox(
                    value=(self._axis_map.get(ax) == field),
                    width=COL_W,
                    margin=(4, 2),
                )
                self._axis_checkboxes[(field, ax)] = cb
                cb.param.watch(
                    lambda evt, f=field, a=ax: self._on_checkbox_change(f, a, evt.new),
                    ["value"],
                )
                cbs.append(cb)

            # Unit dropdown (or dash if no units known). Options map display
            # symbol → enum-member name; the selection stored is the name.
            opts = self._field_unit_options.get(field, {})
            if opts:
                unit_sel = pn.widgets.Select(
                    options=opts,
                    value=self._unit_selections.get(field, next(iter(opts.values()))),
                    width=90,
                    margin=(2, 2),
                )
                unit_sel.param.watch(
                    lambda evt, f=field: self._on_unit_change(f, evt.new),
                    ["value"],
                )
                self._unit_selects[field] = unit_sel
            else:
                unit_sel = pn.pane.Markdown("—", width=72)

            rows.append(pn.Row(
                pn.pane.Markdown(field, width=LABEL_W),
                *cbs,
                unit_sel,
                margin=(2, 0),
            ))

        return pn.Column(*rows)

    def _on_checkbox_change(self, field: str, axis: str, checked: bool) -> None:
        if self._updating_checkboxes or self._restoring:
            return

        if checked:
            self._axis_map[axis] = field
        elif self._axis_map.get(axis) == field:
            self._axis_map[axis] = None

        # Bulk-sync all checkboxes to axis_map; flag prevents re-entrancy
        self._updating_checkboxes = True
        for (f, a), cb in self._axis_checkboxes.items():
            expected = (self._axis_map.get(a) == f)
            if cb.value != expected:
                cb.value = expected
        self._updating_checkboxes = False

        self._build_figure()

    def _on_unit_change(self, field: str, unit: str) -> None:
        if self._restoring:
            return
        self._unit_selections[field] = unit
        self._build_figure()

    # -- Instances card ------------------------------------------------------

    def _build_instances_card(self) -> None:
        """Sidebar card listing the test runs that match the current selection.

        The list is a flat, single-level :class:`Wunderbaum` (one checkable leaf
        per matching test) rebuilt live by :meth:`_refresh_instances` on every
        tree change — mirroring the cell / procedure trees.
        """
        self._instances_tree: Optional[Wunderbaum] = None
        self._instances_card = pn.Card(
            pn.pane.Markdown("_Select a cell and a procedure._"),
            title="Instances",
            collapsed=False,
        )
        self._refresh_instances()

    def _refresh_instances(self) -> None:
        """Rebuild the instances tree from the current cell+proc selection.

        Each matching test becomes a checkable leaf; unchecking it drops that
        instance from the plot. Toggle state persists across refreshes (keyed by
        test index) so re-selecting a cell/procedure keeps prior choices; newly
        matching instances default to checked. When nothing matches, the card
        falls back to a prompt.

        The fresh widget is swapped into ``card[0]`` (rather than the source
        being reassigned) so the browser repaints — see :meth:`_restore_tree`
        for why an in-place ``source`` update can be swallowed.
        """
        matches = self._matching_tests()
        if not matches:
            self._instances_tree = None
            self._instances_card[0] = pn.pane.Markdown(
                "_Select a cell and a procedure._"
            )
            return

        source: List[Dict] = []
        for m in matches:
            idx = m["idx"]
            checked = self._instance_selections.get(idx, True)
            self._instance_selections[idx] = checked
            # Key encodes the test index (labels can collide); parsed back in
            # _on_instance_event. idx is also stashed under ``data``.
            source.append({
                "title": m["label"],
                "key": f"inst-{idx}",
                "checkbox": True,
                "selected": checked,
                "data": {"idx": idx},
            })

        self._instances_tree = Wunderbaum(
            source=source,
            columns=[{"id": "*", "title": "Instance", "width": "220px"}],
            options={"checkbox": True, "selectMode": "multi"},
            tree_event_callback=self._on_instance_event,
        )
        self._instances_card[0] = self._instances_tree

    def _on_instance_event(self, event_name: str, params: Dict) -> None:
        """Handle an instances-tree widget event; only ``select`` toggles a plot.

        ``params`` is ``{"key", "flag"}``; ``key`` is ``inst-<idx>`` (see
        :meth:`_refresh_instances`), so the toggled test index is recovered from
        it and its plot state set to ``flag``.
        """
        if event_name != "select" or self._restoring:
            return
        key = params.get("key")
        if not isinstance(key, str) or not key.startswith("inst-"):
            return
        try:
            idx = int(key[len("inst-"):])
        except ValueError:
            return
        self._instance_selections[idx] = bool(params.get("flag"))
        self._build_figure()

    # -- Tree selection ------------------------------------------------------

    def _on_tree_change(self, *_args: Any) -> None:
        if self._restoring:
            return
        self._selected_cell_ids = get_checked_instance_ids(self._cell_tree.source)
        self._selected_proc_ids = get_checked_instance_ids(self._proc_tree.source)
        self._refresh_instances()
        self._build_figure()

    # -- Trace resolution ----------------------------------------------------

    def _matching_tests(self) -> List[Dict]:
        """Tests whose cell *and* procedure are both currently selected.

        Returns one dict per match: ``{"idx", "test", "label"}``. ``idx`` is the
        index into ``self._tests`` and is the stable key for the per-instance
        plot toggles. Ignores the instance checkboxes — that filtering happens in
        :meth:`_resolve_traces`.
        """
        selected_cells = [
            self._cell_objects[k]
            for k in self._selected_cell_ids
            if k in self._cell_objects
        ]
        selected_procs = [
            self._procedure_objects[k]
            for k in self._selected_proc_ids
            if k in self._procedure_objects
        ]
        if not selected_cells or not selected_procs:
            return []

        selected_proc_iris = {
            self._obj_iri(p) for p in selected_procs
        }
        selected_proc_iris.discard(None)

        matches = []
        for idx, test in enumerate(self._tests):
            dut: List[Any] = getattr(test, "device_under_test", []) or []
            cell_match = any(self._same_object(c, t) for c in selected_cells for t in dut)
            proc_match = bool(self._test_proc_iris(test) & selected_proc_iris)
            if cell_match and proc_match:
                matches.append({"idx": idx, "test": test, "label": self._test_label(test)})
        return matches

    def _test_label(self, test: Any) -> str:
        """Display label for an instance — the output dataset's, else the test's."""
        output = self._test_output(test)
        if output is not None and getattr(output, "label", None):
            return self._obj_label(output)
        return self._obj_label(test)

    def _resolve_traces(self) -> List[Dict]:
        traces = []
        for m in self._matching_tests():
            if not self._instance_selections.get(m["idx"], True):
                continue
            test = m["test"]
            dut: List[Any] = getattr(test, "device_under_test", []) or []
            output = self._test_output(test)
            rows: List[Any] = getattr(output, "data", []) if output else []
            cell_labels = [self._obj_label(c) for c in dut]
            proc_label = self._proc_label(test)
            traces.append({
                "label": f"{'/'.join(cell_labels)} — {proc_label}",
                "rows": rows,
            })
        return traces

    def _proc_by_iri(self) -> Dict[str, Any]:
        """Map procedure IRI -> procedure object, cached from ``procedure_objects``."""
        cache = getattr(self, "_proc_by_iri_cache", None)
        if cache is None:
            cache = {}
            for obj in self._procedure_objects.values():
                iri = self._obj_iri(obj)
                if iri:
                    cache[iri] = obj
            self._proc_by_iri_cache = cache
        return cache

    def _test_proc_iris(self, test: Any) -> set:
        """Procedure instance IRIs a test is linked to.

        The current linkage is ``test.test_procedure`` — a list of
        ``TestProcedureItem`` whose ``test_procedure_instance`` is an OSW IRI
        string. The legacy single ``test.protocol`` object (still used by the
        Maccor example) is folded in via its own ``get_iri()``.
        """
        iris = set()
        for item in getattr(test, "test_procedure", None) or []:
            inst = getattr(item, "test_procedure_instance", None)
            if isinstance(inst, str) and inst:
                iris.add(inst)
            elif inst is not None:
                iris.add(self._obj_iri(inst))
        proto = getattr(test, "protocol", None)
        if proto is not None:
            iris.add(self._obj_iri(proto))
        iris.discard(None)
        return iris

    def _proc_label(self, test: Any) -> str:
        """Display name(s) of a test's procedure(s), resolved from its IRIs."""
        by_iri = self._proc_by_iri()
        names = [
            self._obj_label(by_iri[iri])
            for iri in self._test_proc_iris(test)
            if iri in by_iri
        ]
        return "/".join(names) if names else "?"

    @staticmethod
    def _same_object(a: Any, b: Any) -> bool:
        if a is b:
            return True
        if a is None or b is None:
            return False
        uuid_a = getattr(a, "uuid", None)
        uuid_b = getattr(b, "uuid", None)
        if uuid_a is not None and uuid_b is not None and uuid_a == uuid_b:
            return True
        return BatteryDataView._obj_label(a) == BatteryDataView._obj_label(b)

    @staticmethod
    def _test_output(test: Any) -> Any:
        """The test's output dataset.

        The generated ``ElectrochemicalTest`` carries ``output`` as a *list* of
        datasets; older/hand-written tests (the Maccor example) pass a single
        dataset object. Return the first dataset in either case, or ``None``.
        """
        output = getattr(test, "output", None)
        if isinstance(output, (list, tuple)):
            return output[0] if output else None
        return output

    @staticmethod
    def _obj_iri(obj: Any) -> Optional[str]:
        """OSW instance IRI (``Item:OSW…``) of an object, if it exposes one."""
        getter = getattr(obj, "get_iri", None)
        if callable(getter):
            try:
                return getter()
            except Exception:
                return None
        return None

    @staticmethod
    def _obj_label(obj: Any) -> str:
        labels = getattr(obj, "label", None)
        if labels:
            first = labels[0]
            return getattr(first, "text", str(first))
        return str(obj)

    # -- Scalar extraction with unit conversion ------------------------------

    def _get_vals(self, traces: List[Dict], field: str) -> List[List[Optional[float]]]:
        """Return per-trace lists of converted scalars for *field*.

        Conversion is delegated to ``BaseDataView._numeric``, which calls the
        QuantityValue's own ``to_unit`` — the same path the process/datatool
        views use — so no unit-factor table is maintained here.
        """
        ch = self._field_channels.get(field)
        target = self._unit_selections.get(field)
        result = []
        for trace in traces:
            vals = []
            for r in trace["rows"]:
                v = getattr(r, field, None)
                num = self._numeric(v, ch, target) if v is not None else None
                vals.append(float(num) if isinstance(num, (int, float, bool)) else None)
            result.append(vals)
        return result

    def _axis_label(self, field: str) -> str:
        name = self._unit_selections.get(field)
        opts = self._field_unit_options.get(field, {})
        symbol = next((s for s, n in opts.items() if n == name), None)
        return f"{field} [{symbol}]" if symbol else field

    @staticmethod
    def _data_range(per_trace: List[List[Optional[float]]]) -> Optional[Tuple[float, float]]:
        flat = [v for trace_vals in per_trace for v in trace_vals if v is not None]
        if not flat:
            return None
        lo, hi = min(flat), max(flat)
        pad = (hi - lo) * 0.05 or 1.0
        return lo - pad, hi + pad

    # -- BaseDataView contract -----------------------------------------------

    def _update_unit_controls(self) -> None:
        pass

    def _build_figure(self) -> None:
        """Re-render the *active* plot in place from the current selection.

        Only the active plot tracks the live selection; frozen plots keep their
        captured figures. Rebuilding just the active record's panel and then
        reassigning ``_plots_col.objects`` wholesale is what makes the browser
        pane refresh immediately (an in-place ``col[i] = x`` mutation can lag a
        render until the next interaction).
        """
        if not getattr(self, "_plots", None):
            return  # _build_plot has not run yet
        active = self._active_record()
        active["panel"] = self._build_active_panel(active)
        self._render_plots()

    def _make_figure(self):
        """Build and return a Bokeh figure from the current selection.

        Returns ``None`` when there is nothing to plot. Kept separate from
        :meth:`_build_figure` so a freeze can snapshot an independent figure
        object without disturbing the live active plot.
        """
        traces = self._resolve_traces()
        if not traces:
            return None

        x_field  = self._axis_map.get("x")
        y1_field = self._axis_map.get("y1")
        y2_field = self._axis_map.get("y2")

        if not x_field or not y1_field:
            return None

        xs_all  = self._get_vals(traces, x_field)
        y1s_all = self._get_vals(traces, y1_field)

        x_rng  = self._data_range(xs_all)
        y1_rng = self._data_range(y1s_all)
        if x_rng is None or y1_rng is None:
            return None

        fig = bk_figure(
            height=350,
            sizing_mode="stretch_width",
            x_axis_label=self._axis_label(x_field),
            y_axis_label=self._axis_label(y1_field),
            x_range=Range1d(*x_rng),
            y_range=Range1d(*y1_rng),   # explicit range — 0 never forced
            tools="pan,wheel_zoom,box_zoom,reset,save",
        )

        # Optional right y2 axis — its range is also derived from data only
        y2_active = False
        y2s_all: List[List[Optional[float]]] = []
        if y2_field:
            y2s_all = self._get_vals(traces, y2_field)
            y2_rng = self._data_range(y2s_all)
            if y2_rng is not None:
                fig.extra_y_ranges = {"y2": Range1d(*y2_rng)}
                fig.add_layout(
                    LinearAxis(y_range_name="y2", axis_label=self._axis_label(y2_field)),
                    "right",
                )
                y2_active = True

        for i, trace in enumerate(traces):
            color = COLORS[i % len(COLORS)]
            xs  = xs_all[i]
            y1s = y1s_all[i]

            # y1 (solid, left axis)
            pairs = [(x, y) for x, y in zip(xs, y1s) if x is not None and y is not None]
            if pairs:
                xc, yc = zip(*pairs)
                fig.line(
                    "x", "y",
                    source=ColumnDataSource({"x": list(xc), "y": list(yc)}),
                    legend_label=f"{trace['label']} ({y1_field})",
                    color=color,
                    line_width=2,
                )

            # y2 (dashed, right axis)
            if y2_active and y2s_all:
                y2s = y2s_all[i]
                pairs2 = [
                    (x, y) for x, y in zip(xs, y2s)
                    if x is not None and y is not None
                ]
                if pairs2:
                    xc2, yc2 = zip(*pairs2)
                    fig.line(
                        "x", "y",
                        source=ColumnDataSource({"x": list(xc2), "y": list(yc2)}),
                        legend_label=f"{trace['label']} ({y2_field})",
                        color=color,
                        line_width=2,
                        line_dash="dashed",
                        y_range_name="y2",
                    )

        fig.legend.click_policy = "hide"
        return fig

    # -- Freeze / unfreeze ---------------------------------------------------

    def _build_plot(self) -> None:
        """Override BaseDataView: one unified, ordered stack of plots.

        Every plot — the live *active* one and each frozen snapshot — is a
        record in ``self._plots`` rendered as its own bordered panel inside
        ``self._plots_col``. Each panel carries its own toggle button::

            <plot 1>   Freeze (blue)  Delete   <- active: driven by the sidebar
            <plot 2>   Unfreeze       Delete   <- frozen snapshot
            <plot 3>   Unfreeze       Delete
            ...

        Exactly one record has ``active=True``. Because the active plot is just
        whichever record is flagged active — not a structurally separate area —
        freezing / unfreezing flips that flag *in place* and the visible order
        never shuffles.
        """
        super()._build_plot()  # sets self._plot_col + self._plot_card

        self._plots_col = pn.Column(sizing_mode="stretch_width")
        # Keep the base ``_plot_col`` name pointing at the container so any
        # inherited helper keeps working.
        self._plot_col = self._plots_col

        # Start with a single empty active plot carrying the default axis / unit
        # choices, so the sidebar has something to drive immediately.
        first = self._make_plot_record(
            PlotState(
                axis_map=dict(self._axis_map),
                unit_selections=dict(self._unit_selections),
            ),
            active=True,
        )
        self._plots: List[Dict[str, Any]] = [first]

        self._plot_card[:] = [self._plots_col]
        self._render_plots()

    def _capture_state(self) -> PlotState:
        """Snapshot everything that defines the active plot (all tab selections)."""
        return PlotState(
            cell_ids=list(self._selected_cell_ids),
            proc_ids=list(self._selected_proc_ids),
            instance_selections=dict(self._instance_selections),
            axis_map=dict(self._axis_map),
            unit_selections=dict(self._unit_selections),
        )

    def _apply_state(self, state: PlotState) -> None:
        """Write a captured :class:`PlotState` back into every widget, then re-plot.

        ``_restoring`` suppresses the per-widget watchers so the many syncs
        below collapse into a single :meth:`_build_figure` at the end.
        """
        self._restoring = True
        try:
            self._selected_cell_ids = list(state.cell_ids)
            self._selected_proc_ids = list(state.proc_ids)
            self._instance_selections = dict(state.instance_selections)
            self._axis_map = dict(state.axis_map)
            self._unit_selections = dict(state.unit_selections)

            # Trees, instances list, axis grid and unit dropdowns.
            self._cell_tree = self._restore_tree(
                self._cell_card, self._cell_tree_source, self._cell_tree_title,
                self._selected_cell_ids,
            )
            self._proc_tree = self._restore_tree(
                self._proc_card, self._proc_tree_source, self._proc_tree_title,
                self._selected_proc_ids,
            )
            self._refresh_instances()
            self._sync_axis_grid()
            self._sync_unit_selects()
        finally:
            self._restoring = False

        self._build_figure()

    def _restore_tree(
        self, card: pn.Card, pristine: List[Dict], title: str, ids: List[str]
    ) -> Wunderbaum:
        """Rebuild a tree with a restored selection and swap it into its card.

        Returns the new widget (the caller rebinds ``self._*_tree`` to it).

        The new source is derived from the *pristine* plain-data source captured
        at build time, **not** from the live ``tree.source``. Once the browser
        has edited a Wunderbaum, its ``source`` holds live model references, and
        the ``copy.deepcopy`` inside :func:`set_selected_instances` would try to
        copy a Tornado ``IOLoop`` and raise. The pristine source is guaranteed to
        stay plain dicts/lists.

        A rebuild — rather than reassigning ``tree.source`` — is also what makes
        the browser checkboxes actually repaint. Wunderbaum diffs a reassigned
        ``source`` against its live tree through a ``change:source`` echo guard
        that swallows the update whenever the last tree edit came from the
        browser (exactly the Unfreeze case), so the checkboxes would silently
        stay put. A fresh widget has no prior state to diff against: it renders
        its ``selected`` flags straight from ``source``. Replacing the card's
        child is a structural (add/remove) change that Panel always syncs.
        """
        source = set_selected_instances(pristine, set(ids))
        new_tree = self._make_tree(source, title)
        card[0] = new_tree
        return new_tree

    def _sync_axis_grid(self) -> None:
        """Set axis checkboxes from ``_axis_map`` (guarded against re-entrancy)."""
        self._updating_checkboxes = True
        for (f, a), cb in self._axis_checkboxes.items():
            cb.value = (self._axis_map.get(a) == f)
        self._updating_checkboxes = False

    def _sync_unit_selects(self) -> None:
        """Set unit dropdowns from ``_unit_selections`` (watchers guarded)."""
        for f, sel in self._unit_selects.items():
            target = self._unit_selections.get(f)
            if target is not None and sel.value != target:
                sel.value = target

    def _state_summary(self, state: PlotState) -> str:
        """Short human label describing a frozen plot's selection."""
        cells = [
            self._obj_label(self._cell_objects[k])
            for k in state.cell_ids if k in self._cell_objects
        ]
        procs = [
            self._obj_label(self._procedure_objects[k])
            for k in state.proc_ids if k in self._procedure_objects
        ]
        axis = state.axis_map
        y = axis.get("y1") or "—"
        if axis.get("y2"):
            y = f"{y}+{axis['y2']}"
        axes = f"{y} vs {axis.get('x') or '—'}"
        return f"{', '.join(cells) or '—'} / {', '.join(procs) or '—'} · {axes}"

    # -- Plot-stack helpers --------------------------------------------------

    def _active_record(self) -> Dict[str, Any]:
        """Return the single plot record currently flagged active."""
        return next(r for r in self._plots if r["active"])

    def _make_plot_record(
        self, state: PlotState, active: bool, figure: Any = None
    ) -> Dict[str, Any]:
        """Create a plot record and build its panel.

        ``figure`` is the captured Bokeh figure for a frozen record; the active
        record ignores it and renders live from the current selection.
        """
        self._plot_counter += 1
        record: Dict[str, Any] = {
            "id": self._plot_counter,
            "state": state,
            "active": active,
            "figure": figure,
        }
        record["panel"] = (
            self._build_active_panel(record)
            if active
            else self._build_frozen_panel(record)
        )
        return record

    def _render_plots(self) -> None:
        """Push the current stack of panels into the column in one assignment.

        Reassigning ``objects`` wholesale (rather than mutating in place) is the
        reliable render path — it is exactly how Panelini syncs its own panes —
        so a role swap or a rebuilt active panel repaints immediately instead of
        waiting for the next interaction.
        """
        self._plots_col.objects = [r["panel"] for r in self._plots]

    def _build_active_panel(self, record: Dict[str, Any]) -> pn.Column:
        """Build the live active-plot panel: blue Freeze + Delete, live figure."""
        freeze_btn = pn.widgets.Button(
            name="❄ Freeze plot",
            button_type="primary",           # blue = "this is the active plot"
            width=140,
            margin=(6, 6),
        )
        freeze_btn.on_click(self._on_freeze_click)

        delete_btn = pn.widgets.Button(
            name="🗑 Delete", button_type="default", width=110, margin=(6, 6),
        )
        delete_btn.on_click(lambda evt, r=record: self._on_delete_click(r))

        fig = self._make_figure()
        body = (
            pn.pane.Bokeh(fig, sizing_mode="stretch_width")
            if fig is not None
            else pn.pane.Markdown("_No data selected._", margin=(10, 6))
        )
        return pn.Column(
            pn.Row(freeze_btn, delete_btn, sizing_mode="stretch_width"),
            body,
            styles={"border": "2px solid var(--panel-primary-color, #0072B5)",
                    "border-radius": "4px", "margin-top": "8px"},
            sizing_mode="stretch_width",
        )

    def _build_frozen_panel(self, record: Dict[str, Any]) -> pn.Column:
        """Build a frozen-snapshot panel: Unfreeze + Delete, captured figure."""
        unfreeze_btn = pn.widgets.Button(
            name="Unfreeze", button_type="default", width=110, margin=(6, 6),
        )
        unfreeze_btn.on_click(lambda evt, r=record: self._on_unfreeze_click(r))

        delete_btn = pn.widgets.Button(
            name="🗑 Delete", button_type="default", width=110, margin=(6, 6),
        )
        delete_btn.on_click(lambda evt, r=record: self._on_delete_click(r))

        fig = record.get("figure")
        body = (
            pn.pane.Bokeh(fig, sizing_mode="stretch_width")
            if fig is not None
            else pn.pane.Markdown("_Empty plot._", margin=(10, 6))
        )
        return pn.Column(
            pn.Row(
                unfreeze_btn,
                delete_btn,
                pn.pane.Markdown(
                    f"**Frozen #{record['id']}:** "
                    f"{self._state_summary(record['state'])}",
                    margin=(10, 6),
                ),
            ),
            body,
            styles={"border": "1px solid var(--panel-surface-color, #ccc)",
                    "border-radius": "4px", "margin-top": "8px"},
            sizing_mode="stretch_width",
        )

    def _freeze_record_in_place(self, record: Dict[str, Any]) -> None:
        """Turn the (active) *record* into a frozen snapshot without moving it.

        Captures the live figure + state, flips ``active`` off and rebuilds the
        record's panel as a frozen panel (Freeze button → Unfreeze button).
        """
        record["state"] = self._capture_state()
        record["figure"] = self._make_figure()
        record["active"] = False
        record["panel"] = self._build_frozen_panel(record)

    def _on_freeze_click(self, _event: Any = None) -> None:
        """Freeze the active plot: drop a snapshot directly below it, in place.

        The active plot stays active where it is (its button stays a blue
        Freeze); a static snapshot of its current figure + state is inserted one
        slot down, so freezing pushes a copy down exactly one place rather than
        moving the working plot itself.
        """
        fig = self._make_figure()
        if fig is None:
            return  # nothing plotted — nothing to freeze
        active = self._active_record()
        active["state"] = self._capture_state()
        snapshot = self._make_plot_record(
            self._capture_state(), active=False, figure=fig,
        )
        idx = self._plots.index(active)
        self._plots.insert(idx + 1, snapshot)
        self._render_plots()

    def _on_unfreeze_click(self, record: Dict[str, Any]) -> None:
        """Unfreeze a plot: it becomes active in place; the old active freezes.

        Roles swap where the plots already sit — nothing is reordered:

        - the currently-active plot is frozen *in its own slot* (its Freeze
          button becomes Unfreeze), keeping its figure as captured;
        - the clicked plot becomes the active one *in its own slot* (its
          Unfreeze button becomes a blue Freeze) and its saved :class:`PlotState`
          is restored into every tab — trees, instances, axis grid, unit
          dropdowns — so the sidebar jumps to it.

        If the previously-active plot was empty (nothing plotted), it is dropped
        rather than kept as an empty frozen snapshot.
        """
        if record not in self._plots or record["active"]:
            return

        active = self._active_record()
        had_figure = self._make_figure() is not None
        if had_figure:
            self._freeze_record_in_place(active)
        else:
            self._plots.remove(active)

        # Promote the clicked plot to active in place, then restore its state
        # (which rebuilds its active panel and re-renders the whole stack).
        record["active"] = True
        record["figure"] = None
        self._apply_state(record["state"])

    def _on_delete_click(self, record: Dict[str, Any]) -> None:
        """Delete any plot. Deleting the active one promotes a neighbour.

        A frozen plot is simply removed. Deleting the active plot removes it and
        promotes the plot that fell into its slot (the one directly below, i.e.
        the most recent freeze) — or the one above if it was last — restoring
        that plot's state. If it was the only plot, a fresh empty active plot
        takes its place.
        """
        if record not in self._plots:
            return
        was_active = record["active"]
        idx = self._plots.index(record)
        self._plots.remove(record)

        if not was_active:
            self._render_plots()
            return

        if self._plots:
            promote = self._plots[min(idx, len(self._plots) - 1)]
            promote["active"] = True
            promote["figure"] = None
            self._apply_state(promote["state"])
        else:
            fresh = self._make_plot_record(
                PlotState(
                    axis_map=dict(self._axis_map),
                    unit_selections=dict(self._unit_selections),
                ),
                active=True,
            )
            self._plots.append(fresh)
            self._apply_state(fresh["state"])

    def _update_log_console(self) -> None:
        pass

    async def _load_and_plot(self) -> None:
        self._build_figure()

    def _build_config_editor(self) -> None:
        # Localized here because base's BaseDataView dropped this method in its
        # composable-config refactor (base commit b3c3f1c). Builds a collapsed
        # JSON editor card bound to this view's ``BaseViewConfig`` instance.
        schema = self._config.model_json_schema()
        self._config_editor = JsonEditor(
            value=self._config.model_dump(),
            options={
                "schema": schema,
                "no_additional_properties": True,
                "disable_edit_json": False,
            },
        )
        self._config_editor.param.watch(self._on_config_editor_change, ["value"])
        self._config_card = pn.Card(
            pn.Column(
                self._config_editor,
                sizing_mode="stretch_width",
                max_height=1000,
                scroll=True,
            ),
            title="Config",
            collapsed=True,
        )

    def _on_config_editor_change(self, event: Any) -> None:
        pass

    # -- Layout --------------------------------------------------------------

    def _build_layout(self) -> None:
        if self._embeddable:
            self._app = None
            return
        self._app = Panelini(
            title=self._title,
            sidebar_enabled=True,
            sidebars_max_width=420,
        )
        self._app.sidebar_set(self.sidebar_cards)
        self._app.main_set(self.main_cards)

    @property
    def sidebar_cards(self) -> List[Any]:
        return [
            self._cell_card,
            self._proc_card,
            self._instances_card,
            self._axis_card,
            self._config_card,
        ]

    @property
    def main_cards(self) -> List[Any]:
        return [self._plot_card, self._log_card]
