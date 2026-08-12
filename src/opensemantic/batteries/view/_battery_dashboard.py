"""Battery cycling data view — BatteryDataView.

Sidebar layout:
  - Cell tree     (SubClassOf + HasType hierarchy, hierarchical checkbox)
  - Procedure card: procedure tree + axis/unit grid below it
  - Instances card: live list of test runs matching the cell+procedure
    selection; per-instance checkbox toggles whether it is plotted
  - Config card

Axis/unit grid (below procedure tree)
  rows = data fields (test_time, voltage, current, ...)
  cols = x | y1 | y2 | Unit
  Per-column radio: exactly one field per axis at a time.
  Unit column: dropdown for each field, auto-detected from opensemantic type.

Plot
  Single Bokeh figure, left y1 axis + optional right y2 axis.
  Both axis ranges are derived from data — 0 is never forced as baseline.

Uses BaseDataView mixin for shared plot / log / config cards.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import panel as pn
from bokeh.models import ColumnDataSource, LinearAxis, Range1d
from bokeh.plotting import figure as bk_figure
from panelini import Panelini
from panelini.panels.wunderbaum import Wunderbaum

from opensemantic.base.view import (
    COLORS,
    BaseDataView,
    DashboardConfig,
    get_available_units,
)

from opensemantic.batteries.view._battery_utils import (
    build_oold_tree_source,
    get_checked_instance_ids,
)

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
          .protocol           procedure object
          .output.data        list of row objects (ElectrochemicalCyclingDataRow)
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
        self._config = DashboardConfig()
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

        self._build_cell_tree(cell_nodes, cell_edges)
        self._build_procedure_card(procedure_nodes, procedure_edges)
        self._build_instances_card()
        self._build_plot()
        self._build_log_console()
        self._build_config_editor()
        self._build_layout()

    # -- Field / type detection ----------------------------------------------

    @staticmethod
    def _detect_fields(tests: List[Any]) -> List[str]:
        for test in tests:
            output = getattr(test, "output", None)
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
            output = getattr(test, "output", None)
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

    def _build_cell_tree(self, nodes: Dict, edges: List) -> None:
        source = build_oold_tree_source(nodes, edges)
        self._cell_tree = Wunderbaum(
            source=source,
            columns=[{"id": "*", "title": "Cell", "width": "220px"}],
            options={"checkbox": True, "selectMode": "hier"},
        )
        self._cell_tree.param.watch(self._on_tree_change, ["source"])
        self._cell_card = pn.Card(
            self._cell_tree,
            title="Cells",
            collapsed=False,
        )

    # -- Procedure card (tree + axis/unit grid) ------------------------------

    def _build_procedure_card(self, nodes: Dict, edges: List) -> None:
        source = build_oold_tree_source(nodes, edges)
        self._proc_tree = Wunderbaum(
            source=source,
            columns=[{"id": "*", "title": "Procedure", "width": "220px"}],
            options={"checkbox": True, "selectMode": "hier"},
        )
        self._proc_tree.param.watch(self._on_tree_change, ["source"])

        self._proc_card = pn.Card(
            self._proc_tree,
            pn.layout.Divider(),
            self._build_axis_grid(),
            title="Procedures",
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
        if self._updating_checkboxes:
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
        self._unit_selections[field] = unit
        self._build_figure()

    # -- Instances card ------------------------------------------------------

    def _build_instances_card(self) -> None:
        """Sidebar card listing the test runs that match the current selection.

        Populated live by :meth:`_refresh_instances` on every tree change.
        """
        self._instances_col = pn.Column()
        self._instances_card = pn.Card(
            self._instances_col,
            title="Instances",
            collapsed=False,
        )
        self._refresh_instances()

    def _refresh_instances(self) -> None:
        """Rebuild the instance checkbox list from the current cell+proc selection.

        Each matching test gets a checkbox; unchecking it drops that instance
        from the plot. Toggle state persists across refreshes (keyed by test
        index) so re-selecting a cell/procedure keeps prior choices; newly
        matching instances default to checked.
        """
        self._instances_col.clear()
        matches = self._matching_tests()
        if not matches:
            self._instances_col.append(
                pn.pane.Markdown("_Select a cell and a procedure._")
            )
            return
        for m in matches:
            idx = m["idx"]
            checked = self._instance_selections.get(idx, True)
            self._instance_selections[idx] = checked
            cb = pn.widgets.Checkbox(label=m["label"], value=checked, margin=(2, 8))
            cb.param.watch(
                lambda evt, i=idx: self._on_instance_toggle(i, evt.new),
                ["value"],
            )
            self._instances_col.append(cb)

    def _on_instance_toggle(self, idx: int, checked: bool) -> None:
        self._instance_selections[idx] = checked
        self._build_figure()

    # -- Tree selection ------------------------------------------------------

    def _on_tree_change(self, *_args: Any) -> None:
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

        matches = []
        for idx, test in enumerate(self._tests):
            dut: List[Any] = getattr(test, "device_under_test", []) or []
            proto: Any = getattr(test, "protocol", None)
            cell_match = any(self._same_object(c, t) for c in selected_cells for t in dut)
            proc_match = any(self._same_object(p, proto) for p in selected_procs)
            if cell_match and proc_match:
                matches.append({"idx": idx, "test": test, "label": self._test_label(test)})
        return matches

    def _test_label(self, test: Any) -> str:
        """Display label for an instance — the output dataset's, else the test's."""
        output = getattr(test, "output", None)
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
            proto: Any = getattr(test, "protocol", None)
            output = getattr(test, "output", None)
            rows: List[Any] = getattr(output, "data", []) if output else []
            cell_labels = [self._obj_label(c) for c in dut]
            traces.append({
                "label": f"{'/'.join(cell_labels)} — {self._obj_label(proto)}",
                "rows": rows,
            })
        return traces

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
        self._plot_col.clear()
        traces = self._resolve_traces()
        if not traces:
            return

        x_field  = self._axis_map.get("x")
        y1_field = self._axis_map.get("y1")
        y2_field = self._axis_map.get("y2")

        if not x_field or not y1_field:
            return

        xs_all  = self._get_vals(traces, x_field)
        y1s_all = self._get_vals(traces, y1_field)

        x_rng  = self._data_range(xs_all)
        y1_rng = self._data_range(y1s_all)
        if x_rng is None or y1_rng is None:
            return

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
        self._plot_col.append(pn.pane.Bokeh(fig, sizing_mode="stretch_width"))

    def _update_log_console(self) -> None:
        pass

    async def _load_and_plot(self) -> None:
        self._build_figure()

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
            self._config_card,
        ]

    @property
    def main_cards(self) -> List[Any]:
        return [self._plot_card, self._log_card]
