"""Battery cycling data view — BatteryDataView.

Sidebar layout (top to bottom):
  - Cell card:      cell tree (SubClassOf + HasType hierarchy, hierarchical
                    checkbox); fixed height, scrolls
  - Procedure card: procedure tree; same fixed height as the cell card, scrolls
  - Instances card: live list of test runs matching the cell+procedure
                    selection; per-instance checkbox toggles whether it is plotted
  - Time Series - Axes & Units card: the axis/unit grid (below Instances)
  - Cycle Series - Axes & Units card: the axis/unit grid of the cycle plot
                    (capacities and coulomb efficiencies per cycle)
  - Batches card:   assigns every plotted dataset to a named batch
  - Batch Series - Axes & Units card: the same grid for the batch plot
  - Config card

Time Series - Axes & Units grid
  rows = data fields (test_time, voltage, current, ...)
  cols = x | y1 | y2 | Unit
  Per-column radio: exactly one field per axis at a time.
  Unit column: dropdown for each field, auto-detected from opensemantic type.
  A field typed as an Enum (e.g. ``step_type``) is categorical: no unit, it
  cannot be x, and as y1 / y2 it is drawn as a step line on a category axis.

Plot
  Single Bokeh figure, left y1 axis + optional right y2 axis.
  Both axis ranges are derived from data — 0 is never forced as baseline.
  The plot re-renders whenever the selection changes.

Cycle series plot
  Second collapsible card below the plot: the charge and discharge capacity of
  every cycle (x = cycle number), one colour per trace, charge solid and
  discharge dashed, capacities as circles and efficiencies as squares. The
  values come from ``cycle_summary`` and need the ``step_type`` of the rows;
  without it the card shows a placeholder.
  The grid has the same layout as the time-series grid, but y1 and y2 accept
  several fields. Fields on one axis share a dimension (capacity or
  efficiency) and one unit; a field is on at most one axis.

Batch series plot
  Third collapsible card: the mean of the cycle values over the datasets of
  each batch, with the sample standard deviation as error bars (only where a
  cycle has at least two values). The Batches card suggests batches from the
  cell category or the procedure of each dataset and lets the user move
  datasets between batches. A dataset in "(none)" is not plotted.

Uses BaseDataView mixin for shared plot / log / config cards.
"""

from __future__ import annotations

import logging
from enum import Enum
from functools import partial
from typing import Any, Callable, Dict, List, Optional, Tuple

import panel as pn
from bokeh.models import (
    ColumnDataSource,
    FixedTicker,
    HoverTool,
    LinearAxis,
    Range1d,
    Whisker,
)
from bokeh.plotting import figure as bk_figure
from panelini import Panelini
from panelini.panels.jsoneditor import JsonEditor
from panelini.panels.wunderbaum import Wunderbaum

from opensemantic.base.view import (
    COLORS,
    BaseDataView,
    BaseViewConfig,
    get_available_units,
)
from opensemantic.batteries._batch_summary import aggregate_batch
from opensemantic.batteries._cycle_summary import cycle_summary
from opensemantic.batteries.view._battery_utils import (
    build_oold_tree_source,
    get_checked_instance_ids,
    set_selected_instances,
)
from opensemantic.characteristics.quantitative.v1 import (
    Count,
    Efficiency,
    ElectricCharge,
)

_logger = logging.getLogger(__name__)
_view_log = logging.getLogger("opensemantic.batteries.view")


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


class _CycleGrid:
    """State and widgets of one cycle axis/unit grid.

    The cycle plot and the batch plot each own one instance. Rows are the cycle
    fields, columns are x | y1 | y2 | Unit. ``cycle_count`` is the fixed x.
    Fields on one axis share a dimension (capacity or efficiency) and one unit,
    and a field is on at most one axis. ``redraw`` is called after every change
    the user makes in the grid.
    """

    # field -> (dimension, characteristic class, default unit name)
    SPECS: Dict[str, Tuple[str, type, str]] = {
        "cycle_count": ("count", Count, ""),
        "charge_capacity": ("capacity", ElectricCharge, "coulomb"),
        "discharge_capacity": ("capacity", ElectricCharge, "coulomb"),
        "charge_coulomb_efficiency": ("efficiency", Efficiency, "percent"),
        "discharge_coulomb_efficiency": ("efficiency", Efficiency, "percent"),
    }

    def __init__(self, redraw: Callable[[], None]):
        self.redraw = redraw
        self.specs = self.SPECS
        self.unit_options: Dict[str, Dict[str, str]] = {}
        self.units: Dict[str, str] = {}
        for field, (_dim, char_cls, default) in self.specs.items():
            if field == "cycle_count":
                continue
            units = get_available_units(_FieldChannel(field, char_cls))
            self.unit_options[field] = {u["symbol"]: u["name"] for u in units}
            names = [u["name"] for u in units]
            self.units[field] = (
                default if default in names else (names[0] if names else "")
            )
        # Fields on each axis, in tick order; one unit per axis
        self.axis_fields: Dict[str, List[str]] = {
            "y1": ["charge_capacity", "discharge_capacity"],
            "y2": [],
        }
        self.checkboxes: Dict[Tuple[str, str], pn.widgets.Checkbox] = {}
        self.unit_sels: Dict[str, pn.widgets.Select] = {}
        self._updating = False
        self.panel = self._build()

    def _build(self) -> pn.Column:
        """Grid: rows = cycle fields, cols = x | y1 | y2 | Unit.

        Same layout as the time-series grid. ``cycle_count`` is the fixed x
        (checked, disabled); every other field can be on y1 or y2.
        """
        COL_W = 36
        LABEL_W = 100

        header = pn.Row(
            pn.pane.Markdown("**Field**", width=LABEL_W),
            pn.pane.Markdown("**x**", width=COL_W),
            pn.pane.Markdown("**y1**", width=COL_W),
            pn.pane.Markdown("**y2**", width=COL_W),
            pn.pane.Markdown("**Unit**", width=80),
        )
        rows: List[Any] = [header]

        for field in self.specs:
            is_x = field == "cycle_count"
            cbs = []
            for ax in ("x", "y1", "y2"):
                cb = pn.widgets.Checkbox(
                    value=(is_x and ax == "x") or field in self.axis_fields.get(ax, []),
                    width=COL_W,
                    margin=(4, 2),
                    # cycle_count is the fixed x and cannot be on y1 / y2
                    disabled=(ax == "x") or is_x,
                )
                self.checkboxes[(field, ax)] = cb
                cb.param.watch(
                    lambda evt, f=field, a=ax: self._on_checkbox_change(f, a, evt.new),
                    ["value"],
                )
                cbs.append(cb)

            opts = self.unit_options.get(field, {})
            if opts:
                unit_sel = pn.widgets.Select(
                    options=opts,
                    value=self.units[field],
                    width=90,
                    margin=(2, 2),
                )
                unit_sel.param.watch(
                    lambda evt, f=field: self._on_unit_change(f, evt.new),
                    ["value"],
                )
                self.unit_sels[field] = unit_sel
            else:
                unit_sel = pn.pane.Markdown("—", width=72)

            rows.append(
                pn.Row(
                    pn.pane.Markdown(field, width=LABEL_W),
                    *cbs,
                    unit_sel,
                    margin=(2, 0),
                )
            )

        return pn.Column(*rows)

    def _sync(self) -> None:
        """Set the y1 / y2 checkboxes and unit Selects from the state.

        Runs under ``_updating`` so the watchers it triggers are no-ops.
        """
        self._updating = True
        try:
            for (field, ax), cb in self.checkboxes.items():
                if ax == "x":
                    continue
                expected = field in self.axis_fields.get(ax, [])
                if cb.value != expected:
                    cb.value = expected
            for field, sel in self.unit_sels.items():
                if sel.value != self.units[field]:
                    sel.value = self.units[field]
        finally:
            self._updating = False

    def _on_checkbox_change(self, field: str, axis: str, checked: bool) -> None:
        if self._updating:
            return
        fields = self.axis_fields
        if checked:
            other = "y2" if axis == "y1" else "y1"
            if field in fields[other]:
                fields[other].remove(field)
            if field not in fields[axis]:
                dim = self.specs[field][0]
                # fields on one axis share a dimension; else the new one replaces
                if any(self.specs[f][0] != dim for f in fields[axis]):
                    fields[axis] = []
                if fields[axis]:
                    # one unit per axis: take the unit of the fields already there
                    self.units[field] = self.units[fields[axis][0]]
                fields[axis].append(field)
        elif field in fields[axis]:
            fields[axis].remove(field)
        self._sync()
        self.redraw()

    def _on_unit_change(self, field: str, unit: str) -> None:
        if self._updating:
            return
        self.units[field] = unit
        for members in self.axis_fields.values():
            if field in members:
                for f in members:
                    self.units[f] = unit
        self._sync()
        self.redraw()

    def axis_label(self, axis: str) -> str:
        """``"field1, field2 [unit]"`` of the fields on an axis."""
        fields = self.axis_fields[axis]
        opts = self.unit_options.get(fields[0], {})
        name = self.units.get(fields[0])
        symbol = next((s for s, n in opts.items() if n == name), None)
        text = ", ".join(fields)
        return f"{text} [{symbol}]" if symbol else text


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
    # The Instances card holds a flat one-level tree, so it needs less room than
    # a full tree card — but not too little; ~3/4 height, scrolling when there
    # are many matches.
    _INSTANCES_CARD_HEIGHT = _TREE_CARD_HEIGHT * 3 // 4

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

        # Global axis assignment: field name per axis (None = axis unused).
        # The defaults skip categorical fields, which cannot be x.
        numeric = [f for f in field_names if self._categorical_values(f) is None]
        self._axis_map: Dict[str, Optional[str]] = {
            "x": numeric[0] if len(numeric) > 0 else None,
            "y1": numeric[1] if len(numeric) > 1 else None,
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
        # Pint DataFrame per trace, kept across re-plots so a unit or axis
        # change does not rebuild it: key -> (rows it was built from, frame)
        self._df_cache: Dict[Any, Tuple[List[Any], Any]] = {}
        # Cycle summary frame per trace, cached the same way
        self._cycle_cache: Dict[Any, Tuple[List[Any], Any]] = {}
        # Batches: names in creation order, dataset key -> batch name (or
        # _NO_BATCH), and the rule ("cell" / "proc") of the last suggestion
        self._batch_names: List[str] = []
        self._batch_assign: Dict[str, str] = {}
        self._batch_rule: Optional[str] = None

        self._build_cell_tree(cell_nodes, cell_edges)
        self._build_procedure_card(procedure_nodes, procedure_edges)
        self._build_instances_card()
        self._build_axis_card()
        self._build_cycle_card()
        self._build_batch_cards()
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

    def _categorical_values(self, field: str) -> Optional[List[str]]:
        """The category values of a categorical field, else ``None``.

        A field is categorical when its characteristic class is an ``Enum``;
        the values are the members' values in declaration order.
        """
        ch = self._field_channels.get(field)
        char_cls = getattr(ch, "_characteristic_class", None) if ch else None
        if isinstance(char_cls, type) and issubclass(char_cls, Enum):
            return [m.value for m in char_cls]
        return None

    # -- Cell tree -----------------------------------------------------------

    def _make_tree(self, source: List[Dict], title: str) -> Wunderbaum:
        """Build a checkbox Wunderbaum over *source* wired to ``_on_tree_change``.

        Tree edits (the ``source`` echo from the browser) trigger a re-plot.
        """
        tree = Wunderbaum(
            source=source,
            columns=[{"id": "*", "title": title, "width": "220px"}],
            options={"checkbox": True, "selectMode": "hier"},
        )
        tree.param.watch(self._on_tree_change, ["source"])
        return tree

    def _build_cell_tree(self, nodes: Dict, edges: List) -> None:
        source = build_oold_tree_source(nodes, edges)
        self._cell_tree = self._make_tree(set_selected_instances(source, set()), "Cell")
        self._cell_card = pn.Card(
            self._cell_tree,
            title="Cells",
            collapsed=False,
            height=self._TREE_CARD_HEIGHT,
            scroll=True,
        )

    # -- Procedure card (tree only) ------------------------------------------

    def _build_procedure_card(self, nodes: Dict, edges: List) -> None:
        source = build_oold_tree_source(nodes, edges)
        self._proc_tree = self._make_tree(
            set_selected_instances(source, set()), "Procedure"
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
        wires up ``_axis_checkboxes``.
        """
        self._axis_card = pn.Card(
            self._build_axis_grid(),
            title="Time Series - Axes & Units",
            collapsed=False,
        )

    # -- Cycle series card ---------------------------------------------------

    def _build_cycle_card(self) -> None:
        """Controls card and plot card of the cycle series.

        The controls (an axis/unit grid like the time-series one) redraw only
        the cycle plot. The plot column is reassigned wholesale on every
        redraw, like ``_plot_col``.
        """
        self._cycle_grid = _CycleGrid(self._build_cycle_figure)
        self._cycle_axis_card = pn.Card(
            self._cycle_grid.panel,
            title="Cycle Series - Axes & Units",
            collapsible=True,
            collapsed=False,
        )
        self._cycle_col = pn.Column(
            pn.pane.Markdown("_No cycle data._", margin=(10, 6)),
            sizing_mode="stretch_width",
        )
        self._cycle_card = pn.Card(
            self._cycle_col,
            title="Cycle Series",
            collapsible=True,
            collapsed=False,
            sizing_mode="stretch_width",
        )

    # -- Batches -------------------------------------------------------------

    _NO_BATCH = "(none)"

    def _build_batch_cards(self) -> None:
        """Batches card, grid card and plot card of the batch series.

        The grid is a second :class:`_CycleGrid` that redraws only the batch
        plot. Batch edits redraw only the batch plot as well.
        """
        self._batch_grid = _CycleGrid(self._build_batch_figure)
        self._batch_axis_card = pn.Card(
            self._batch_grid.panel,
            title="Batch Series - Axes & Units",
            collapsible=True,
            collapsed=False,
        )

        self._batch_cell_button = pn.widgets.Button(name="Cell batches", width=110)
        self._batch_cell_button.on_click(lambda _e: self._suggest_batches("cell"))
        self._batch_proc_button = pn.widgets.Button(name="Procedure batches", width=130)
        self._batch_proc_button.on_click(lambda _e: self._suggest_batches("proc"))
        self._batch_input = pn.widgets.TextInput(
            placeholder="New batch name", width=150, margin=(4, 2)
        )
        self._batch_add_button = pn.widgets.Button(name="Add batch", width=90)
        self._batch_add_button.on_click(lambda _e: self._add_batch())
        self._batch_rows = pn.Column(
            pn.pane.Markdown("_No datasets._"), sizing_mode="stretch_width"
        )
        self._batches_card = pn.Card(
            pn.Row(self._batch_cell_button, self._batch_proc_button),
            pn.Row(self._batch_input, self._batch_add_button),
            self._batch_rows,
            title="Batches",
            collapsible=True,
            collapsed=False,
        )
        self._batch_col = pn.Column(
            pn.pane.Markdown("_No batch data._", margin=(10, 6)),
            sizing_mode="stretch_width",
        )
        self._batch_card = pn.Card(
            self._batch_col,
            title="Batch Series",
            collapsible=True,
            collapsed=False,
            sizing_mode="stretch_width",
        )

    @staticmethod
    def _suggested_name(record: Dict, rule: str) -> Optional[str]:
        """Batch name the rule gives a dataset record, or ``None``."""
        if rule == "cell":
            category = record.get("cell_category")
            return category[1] if category else None
        return record.get("procedure_label")

    def _ensure_batch(self, name: str) -> None:
        if name not in self._batch_names:
            self._batch_names.append(name)

    def _suggest_batches(self, rule: str) -> None:
        """Replace all assignments by the groups of *rule* (``cell`` / ``proc``).

        Datasets without the needed information go to "(none)". The rule is
        kept and also applies to datasets that appear later.
        """
        self._batch_rule = rule
        self._batch_assign = {}
        self._batch_names = []
        for record in self._dataset_records():
            name = self._suggested_name(record, rule)
            if name:
                self._ensure_batch(name)
            self._batch_assign[record["key"]] = name or self._NO_BATCH
        # sorted, so batch order and colours do not depend on the match order
        self._batch_names.sort()
        self._refresh_batch_rows()
        self._build_batch_figure()

    def _add_batch(self) -> None:
        name = (self._batch_input.value or "").strip()
        if not name or name == self._NO_BATCH or name in self._batch_names:
            return
        self._batch_names.append(name)
        self._batch_input.value = ""
        self._refresh_batch_rows()
        self._build_batch_figure()

    def _refresh_batches(self) -> None:
        """Assign new datasets by the last rule, then rebuild the rows.

        A known dataset key keeps its batch, also while the dataset is not
        ticked. A new key joins (or creates) the batch the last suggestion rule
        gives it, else "(none)".
        """
        for record in self._dataset_records():
            key = record["key"]
            if key in self._batch_assign:
                continue
            name = (
                self._suggested_name(record, self._batch_rule)
                if self._batch_rule
                else None
            )
            if name:
                self._ensure_batch(name)
            self._batch_assign[key] = name or self._NO_BATCH
        self._refresh_batch_rows()

    def _refresh_batch_rows(self) -> None:
        """One row per ticked dataset: its label and a Select of the batches."""
        records = self._dataset_records()
        if not records:
            self._batch_rows.objects = [pn.pane.Markdown("_No datasets._")]
            return
        options = [self._NO_BATCH] + self._batch_names
        rows = []
        for record in records:
            key = record["key"]
            sel = pn.widgets.Select(
                options=options,
                value=self._batch_assign.get(key, self._NO_BATCH),
                width=140,
                margin=(2, 2),
            )
            sel.param.watch(
                lambda evt, k=key: self._on_batch_select(k, evt.new), ["value"]
            )
            rows.append(
                pn.Row(
                    pn.pane.Markdown(record["label"], width=150),
                    sel,
                    margin=(2, 0),
                )
            )
        self._batch_rows.objects = rows

    def _on_batch_select(self, key: str, name: str) -> None:
        self._batch_assign[key] = name
        self._build_batch_figure()

    def _build_axis_grid(self) -> pn.Column:
        """Grid: rows = fields, cols = x | y1 | y2 | Unit.

        Radio enforcement per column: the _on_checkbox_change handler
        re-syncs all checkboxes from _axis_map under a flag so watchers
        that fire during the bulk update are no-ops.
        """
        COL_W = 36
        LABEL_W = 100

        header = pn.Row(
            pn.pane.Markdown("**Field**", width=LABEL_W),
            pn.pane.Markdown("**x**", width=COL_W),
            pn.pane.Markdown("**y1**", width=COL_W),
            pn.pane.Markdown("**y2**", width=COL_W),
            pn.pane.Markdown("**Unit**", width=80),
        )
        rows: List[Any] = [header]

        for field in self._field_names:
            cbs = []
            for ax in ("x", "y1", "y2"):
                cb = pn.widgets.Checkbox(
                    value=(self._axis_map.get(ax) == field),
                    width=COL_W,
                    margin=(4, 2),
                    # a categorical field cannot be the (numeric) x axis
                    disabled=(
                        ax == "x" and self._categorical_values(field) is not None
                    ),
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

            rows.append(
                pn.Row(
                    pn.pane.Markdown(field, width=LABEL_W),
                    *cbs,
                    unit_sel,
                    margin=(2, 0),
                )
            )

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
            expected = self._axis_map.get(a) == f
            if cb.value != expected:
                cb.value = expected
        self._updating_checkboxes = False

        self._build_time_series_figure()

    def _on_unit_change(self, field: str, unit: str) -> None:
        self._unit_selections[field] = unit
        self._build_time_series_figure()

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
            height=self._INSTANCES_CARD_HEIGHT,
            scroll=True,
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
        being reassigned) so the browser repaints; an in-place ``source`` update
        can be swallowed.
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
            # _on_instance_source_change. idx is also stashed under ``data``.
            source.append(
                {
                    "title": m["label"],
                    "key": f"inst-{idx}",
                    "checkbox": True,
                    "selected": checked,
                    "data": {"idx": idx},
                }
            )

        self._instances_tree = Wunderbaum(
            source=source,
            columns=[{"id": "*", "title": "Instance", "width": "220px"}],
            options={"checkbox": True, "selectMode": "multi"},
        )
        # Read toggles from the echoed ``source``, NOT per-node ``select`` events:
        # a checkbox click fires click→deactivate→select→activate in one JS tick,
        # all through the widget's single ``_event_data`` slot, so Bokeh coalesces
        # them and only the last (``activate``) reaches Python — the ``select`` is
        # dropped, so unchecking an instance never removed its trace. ``source`` is
        # full-state and idempotent, so it survives coalescing. The watch is
        # attached *after* construction so the initial ``source`` above doesn't
        # trigger it (the selections it carries already match). Same fix as the
        # cell/procedure trees — see the README "read from source" gotcha.
        self._instances_tree.param.watch(self._on_instance_source_change, ["source"])
        self._instances_card[0] = self._instances_tree

    def _on_instance_source_change(self, event: Any) -> None:
        """Apply instance-tab checkbox toggles from the echoed ``source``.

        The Instances tab has final authority over what is plotted: each checked
        leaf is a plotted trace, each unchecked leaf is dropped. Diffs every
        leaf's ``selected`` flag (``getSerializableSource`` omits it when false)
        against :attr:`_instance_selections`, keyed by the test index recovered
        from the node's ``data.idx`` (or its ``inst-<idx>`` key), and re-renders
        only when something actually changed.
        """
        changed = False

        def walk(nodes: List[Dict]) -> None:
            nonlocal changed
            for node in nodes:
                idx = self._instance_idx(node)
                if idx is not None:
                    selected = bool(node.get("selected"))
                    if self._instance_selections.get(idx, True) != selected:
                        self._instance_selections[idx] = selected
                        changed = True
                children = node.get("children")
                if children:
                    walk(children)

        walk(event.new or [])
        if changed:
            self._build_figure()

    @staticmethod
    def _instance_idx(node: Dict) -> Optional[int]:
        """Test index for an instances-tree node (``data.idx`` or ``inst-<idx>``)."""
        idx = node.get("idx")
        if idx is None:
            idx = (node.get("data") or {}).get("idx")
        if idx is None:
            key = node.get("key", "")
            if isinstance(key, str) and key.startswith("inst-"):
                try:
                    idx = int(key[len("inst-") :])
                except ValueError:
                    return None
        return idx if isinstance(idx, int) else None

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

        selected_proc_iris = {self._obj_iri(p) for p in selected_procs}
        selected_proc_iris.discard(None)

        matches = []
        for idx, test in enumerate(self._tests):
            dut: List[Any] = getattr(test, "device_under_test", []) or []
            cell_match = any(
                self._same_object(c, t) for c in selected_cells for t in dut
            )
            proc_match = bool(self._test_proc_iris(test) & selected_proc_iris)
            if cell_match and proc_match:
                matches.append(
                    {"idx": idx, "test": test, "label": self._test_label(test)}
                )
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
            traces.append(
                {
                    "label": f"{'/'.join(cell_labels)} — {proc_label}",
                    "rows": rows,
                    "key": f"test-{m['idx']}",
                }
            )
        return traces

    def _dataset_records(self) -> List[Dict]:
        """One record per ticked match, for the Batches card.

        A record is ``{"key", "label", "cell_iri", "cell_category",
        "procedure_iri", "procedure_label"}``; ``key`` equals the ``"key"`` of
        the matching trace. ``cell_category`` is ``(iri, label)`` of the
        cell's class. Unknown values are ``None``. The cell is the first
        device under test and the procedure the first procedure IRI.
        """
        by_iri = self._proc_by_iri()
        records = []
        for m in self._matching_tests():
            if not self._instance_selections.get(m["idx"], True):
                continue
            test = m["test"]
            dut: List[Any] = getattr(test, "device_under_test", []) or []
            cell = dut[0] if dut else None
            category = None
            if cell is not None:
                cls = type(cell)
                getter = getattr(cls, "get_cls_iri", None)
                try:
                    cat_iri = getter() if callable(getter) else cls.__name__
                except Exception:  # noqa: BLE001 - fall back to the class name
                    cat_iri = cls.__name__
                category = (cat_iri, cls.__name__)
            proc_iris = sorted(self._test_proc_iris(test))
            proc_iri = proc_iris[0] if proc_iris else None
            records.append(
                {
                    "key": f"test-{m['idx']}",
                    "label": m["label"],
                    "cell_iri": self._obj_iri(cell) if cell is not None else None,
                    "cell_category": category,
                    "procedure_iri": proc_iri,
                    "procedure_label": (
                        self._obj_label(by_iri[proc_iri])
                        if proc_iri in by_iri
                        else None
                    ),
                }
            )
        return records

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

    def _get_vals(self, traces: List[Dict], field: str) -> List[List[Any]]:
        """Return per-trace lists of converted scalars for *field*.

        Conversion goes through the whole column as a pint frame, so every
        value of a trace is converted the same way and on one Pydantic layer.
        A trace whose rows cannot be framed contributes nothing: drawing it
        unconverted would put a second scale on the same axis, which reads as
        data rather than as a failure.
        """
        target = self._unit_selections.get(field)
        ch = self._field_channels.get(field)
        char_cls = getattr(ch, "_characteristic_class", None) if ch else None
        result = []
        for trace in traces:
            vals = self._column_in_unit(trace, field, target, char_cls)
            if vals is None:
                _logger.warning(
                    "%s: %r could not be converted to a frame, dropping the trace",
                    trace.get("label") or "trace",
                    field,
                )
                vals = []
            result.append(vals)
        return result

    def _column_in_unit(
        self,
        trace: Dict,
        field: str,
        target_unit_name: Optional[str],
        char_cls: Optional[type],
    ) -> Optional[List[Any]]:
        """A trace column converted through pint, or ``None`` if unavailable.

        A categorical field is not converted: its category strings are
        returned (``None`` for a missing value).

        Converting the whole column at once keeps the values in one Pydantic
        layer: the target unit is resolved to a pint string through the field's
        own characteristic class, so no unit enum has to be matched against a
        value. Returns ``None`` when the rows cannot be framed.
        """
        import pandas as pd

        df = self._cached_trace_df(trace)
        if df is None or field not in df.columns:
            return None

        series = df[field]
        if self._categorical_values(field) is not None:
            return [
                (
                    None
                    if v is None or (not isinstance(v, Enum) and pd.isna(v))
                    else (v.value if isinstance(v, Enum) else str(v))
                )
                for v in series
            ]
        if not hasattr(series, "pint"):
            return None
        if target_unit_name and char_cls is not None:
            to_pint_str = getattr(char_cls, "get_pint_ureg_compatible_str", None)
            if callable(to_pint_str):
                try:
                    series = series.pint.to(to_pint_str(target_unit_name))
                except Exception:  # noqa: BLE001 - bad unit: keep the raw column
                    pass
        magnitudes = series.pint.magnitude
        return [None if pd.isna(v) else float(v) for v in magnitudes]

    def _cached_trace_df(self, trace: Dict):
        """The trace's pint DataFrame, built once per rows list.

        The key is the trace's ``"key"`` (a stable dataset id) if it has one,
        else the identity of its rows list. The frame is reused only while the
        trace carries the same rows object it was built from, so new rows for a
        known key replace the entry instead of returning a stale frame. A
        failed conversion is cached as ``None`` too.
        """
        rows = trace.get("rows")
        key = trace.get("key", id(rows))
        hit = self._df_cache.get(key)
        if hit is not None and hit[0] is rows:
            return hit[1]
        df = self._trace_df(trace)
        self._df_cache[key] = (rows, df)
        return df

    @staticmethod
    def _trace_df(trace: Dict):
        """The trace's rows as a pint DataFrame, or ``None`` if not convertible."""
        rows = trace.get("rows") or []
        if not rows:
            return None
        try:
            from opensemantic.batteries._cycling import CyclingDatasetController
            from opensemantic.core.v1 import Label

            dataset = CyclingDatasetController(
                label=[Label(text=trace.get("label") or "trace")], data=rows
            )
            return dataset.to_df()
        except Exception:  # noqa: BLE001 - fall back to the per-value path
            return None

    def _axis_label(self, field: str) -> str:
        name = self._unit_selections.get(field)
        opts = self._field_unit_options.get(field, {})
        symbol = next((s for s, n in opts.items() if n == name), None)
        return f"{field} [{symbol}]" if symbol else field

    def _categories_of(self, field: str, per_trace: List[List[Any]]) -> List[str]:
        """The categories present across all traces, in Enum declaration order.

        Values that are not Enum members are appended in first-seen order.
        """
        present = {v for trace_vals in per_trace for v in trace_vals if v is not None}
        declared = self._categorical_values(field) or []
        ordered = [c for c in declared if c in present]
        for trace_vals in per_trace:
            for v in trace_vals:
                if v is not None and v not in ordered:
                    ordered.append(v)
        return ordered

    @staticmethod
    def _data_range(
        per_trace: List[List[Optional[float]]],
    ) -> Optional[Tuple[float, float]]:
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
        """Re-render the time-series, cycle and batch plots and the batch rows."""
        self._build_time_series_figure()
        self._build_cycle_figure()
        if hasattr(self, "_batch_col"):
            self._refresh_batches()
            self._build_batch_figure()

    def _build_time_series_figure(self) -> None:
        """Re-render the time-series plot from the current selection.

        Reassigning ``_plot_col.objects`` wholesale is what makes the
        browser pane refresh immediately (an in-place ``col[i] = x`` mutation
        can lag a render until the next interaction).
        """
        if not hasattr(self, "_plot_col"):
            return  # _build_plot has not run yet
        fig = self._make_figure()
        self._log_plot("time-series plot", fig, "no data selected")
        self._plot_col.objects = [
            (
                pn.pane.Bokeh(fig, sizing_mode="stretch_width")
                if fig is not None
                else pn.pane.Markdown("_No data selected._", margin=(10, 6))
            )
        ]

    @staticmethod
    def _to_positions(
        per_trace: List[List[Any]], categories: List[str]
    ) -> List[List[Optional[int]]]:
        """Replace each category string by its integer position (None stays)."""
        index = {c: i for i, c in enumerate(categories)}
        return [[index.get(v) for v in trace_vals] for trace_vals in per_trace]

    @staticmethod
    def _set_category_ticks(axis, categories: List[str]) -> None:
        """Label the axis ticks 0..n-1 with the category names."""
        axis.ticker = FixedTicker(ticks=list(range(len(categories))))
        axis.major_label_overrides = {i: c for i, c in enumerate(categories)}

    def _make_figure(self):
        """Build and return a Bokeh figure from the current selection.

        Returns ``None`` when there is nothing to plot.
        """
        traces = self._resolve_traces()
        if not traces:
            return None

        x_field = self._axis_map.get("x")
        y1_field = self._axis_map.get("y1")
        y2_field = self._axis_map.get("y2")

        if not x_field or not y1_field:
            return None
        if self._categorical_values(x_field) is not None:
            return None

        xs_all = self._get_vals(traces, x_field)
        y1s_all = self._get_vals(traces, y1_field)

        # A categorical axis plots each category at its integer position
        y1_cats: Optional[List[str]] = None
        if self._categorical_values(y1_field) is not None:
            y1_cats = self._categories_of(y1_field, y1s_all)
            y1s_all = self._to_positions(y1s_all, y1_cats)

        x_rng = self._data_range(xs_all)
        y1_rng = (-0.5, len(y1_cats) - 0.5) if y1_cats else self._data_range(y1s_all)
        if x_rng is None or y1_rng is None:
            return None

        fig = bk_figure(
            height=350,
            sizing_mode="stretch_width",
            x_axis_label=self._axis_label(x_field),
            y_axis_label=self._axis_label(y1_field),
            x_range=Range1d(*x_rng),
            y_range=Range1d(*y1_rng),  # explicit range — 0 never forced
            tools="pan,wheel_zoom,box_zoom,reset,save",
        )
        if y1_cats:
            self._set_category_ticks(fig.yaxis[0], y1_cats)

        # Optional right y2 axis — its range is also derived from data only
        y2_active = False
        y2s_all: List[List[Any]] = []
        y2_cats: Optional[List[str]] = None
        if y2_field:
            y2s_all = self._get_vals(traces, y2_field)
            if self._categorical_values(y2_field) is not None:
                y2_cats = self._categories_of(y2_field, y2s_all)
                y2s_all = self._to_positions(y2s_all, y2_cats)
            y2_rng = (
                (-0.5, len(y2_cats) - 0.5) if y2_cats else self._data_range(y2s_all)
            )
            if y2_rng is not None:
                fig.extra_y_ranges = {"y2": Range1d(*y2_rng)}
                y2_axis = LinearAxis(
                    y_range_name="y2", axis_label=self._axis_label(y2_field)
                )
                if y2_cats:
                    self._set_category_ticks(y2_axis, y2_cats)
                fig.add_layout(y2_axis, "right")
                y2_active = True

        for i, trace in enumerate(traces):
            color = COLORS[i % len(COLORS)]
            xs = xs_all[i]
            y1s = y1s_all[i]

            # y1 (solid, left axis)
            pairs = [(x, y) for x, y in zip(xs, y1s) if x is not None and y is not None]
            if pairs:
                xc, yc = zip(*pairs)
                draw = partial(fig.step, mode="after") if y1_cats else fig.line
                draw(
                    "x",
                    "y",
                    source=ColumnDataSource({"x": list(xc), "y": list(yc)}),
                    legend_label=f"{trace['label']} ({y1_field})",
                    color=color,
                    line_width=2,
                )

            # y2 (dashed, right axis)
            if y2_active and y2s_all:
                y2s = y2s_all[i]
                pairs2 = [
                    (x, y) for x, y in zip(xs, y2s) if x is not None and y is not None
                ]
                if pairs2:
                    xc2, yc2 = zip(*pairs2)
                    draw2 = partial(fig.step, mode="after") if y2_cats else fig.line
                    draw2(
                        "x",
                        "y",
                        source=ColumnDataSource({"x": list(xc2), "y": list(yc2)}),
                        legend_label=f"{trace['label']} ({y2_field})",
                        color=color,
                        line_width=2,
                        line_dash="dashed",
                        y_range_name="y2",
                    )

        fig.legend.click_policy = "hide"
        return fig

    # -- Cycle series --------------------------------------------------------

    def _cached_cycle_df(self, trace: Dict):
        """The trace's cycle summary as a pint DataFrame, built once per rows list.

        Cached like :meth:`_cached_trace_df`: keyed by the trace's ``"key"`` or
        the identity of its rows, reused only while the trace carries the same
        rows object. ``None`` when the rows give no cycle table.
        """
        rows = trace.get("rows")
        key = trace.get("key", id(rows))
        hit = self._cycle_cache.get(key)
        if hit is not None and hit[0] is rows:
            return hit[1]
        df = self._cycle_df(trace)
        self._cycle_cache[key] = (rows, df)
        return df

    @staticmethod
    def _cycle_df(trace: Dict):
        """The cycle summary of the trace's rows as a frame, or ``None``."""
        rows = trace.get("rows") or []
        if not rows:
            return None
        try:
            summary = cycle_summary(rows, label=trace.get("label") or "trace")
            return summary.to_df() if summary.data else None
        except Exception:  # noqa: BLE001 - rows without cycle data
            return None

    def _cycle_series(self, trace: Dict, column: str, unit_name: Optional[str]):
        """``(cycles, values)`` of a summary column in the unit, or ``None``.

        Cycles whose value is missing are left out; ``None`` when no cycle
        has a value.
        """
        import pandas as pd

        df = self._cached_cycle_df(trace)
        if df is None or column not in df.columns or not hasattr(df[column], "pint"):
            return None
        series = df[column]
        char_cls = _CycleGrid.SPECS[column][1]
        if unit_name:
            try:
                series = series.pint.to(
                    char_cls.get_pint_ureg_compatible_str(unit_name)
                )
            except Exception:  # noqa: BLE001 - bad unit: keep the stored unit
                pass
        cycles = df["cycle_count"].pint.magnitude
        pairs = [
            (float(c), float(v))
            for c, v in zip(cycles, series.pint.magnitude)
            if not pd.isna(c) and not pd.isna(v)
        ]
        if not pairs:
            return None
        xs, ys = zip(*pairs)
        return list(xs), list(ys)

    def _make_cycle_figure(self):
        """Build the cycle figure from the current selection, or ``None``.

        One series per (trace, field) on y1 / y2. ``None`` when no field is on
        an axis or no series can be drawn. Colours follow the trace order of
        the time-series figure; charge fields are solid, discharge dashed;
        capacities use circles and efficiencies squares.
        """
        grid = self._cycle_grid
        if not any(grid.axis_fields.values()):
            return None

        drawn: Dict[str, List[Any]] = {"y1": [], "y2": []}
        for i, trace in enumerate(self._resolve_traces()):
            for axis in ("y1", "y2"):
                for field in grid.axis_fields[axis]:
                    data = self._cycle_series(trace, field, grid.units[field])
                    if data is not None:
                        drawn[axis].append((i, trace, field, data))
        if not drawn["y1"] and not drawn["y2"]:
            return None

        fig = bk_figure(
            height=350,
            sizing_mode="stretch_width",
            x_axis_label="cycle_count",
            tools="pan,wheel_zoom,box_zoom,reset,save",
        )
        if drawn["y1"]:
            fig.yaxis[0].axis_label = grid.axis_label("y1")
        else:
            fig.yaxis[0].visible = False
        if drawn["y2"]:
            rng = self._data_range([d[3][1] for d in drawn["y2"]])
            fig.extra_y_ranges = {"y2": Range1d(*rng)}
            fig.add_layout(
                LinearAxis(y_range_name="y2", axis_label=grid.axis_label("y2")),
                "right",
            )

        for axis in ("y1", "y2"):
            y_kwargs = {"y_range_name": "y2"} if axis == "y2" else {}
            for i, trace, field, (xs, ys) in drawn[axis]:
                color = COLORS[i % len(COLORS)]
                source = ColumnDataSource({"x": xs, "y": ys})
                label = f"{trace['label']} {field}"
                dash = "solid" if field.startswith("charge") else "dashed"
                is_capacity = grid.specs[field][0] == "capacity"
                fig.line(
                    "x",
                    "y",
                    source=source,
                    legend_label=label,
                    color=color,
                    line_width=2,
                    line_dash=dash,
                    **y_kwargs,
                )
                fig.scatter(
                    "x",
                    "y",
                    source=source,
                    legend_label=label,
                    color=color,
                    marker="circle" if is_capacity else "square",
                    size=6,
                    **y_kwargs,
                )

        fig.legend.click_policy = "hide"
        return fig

    def _build_cycle_figure(self) -> None:
        """Re-render the cycle plot (the column is reassigned wholesale)."""
        if not hasattr(self, "_cycle_col"):
            return  # _build_cycle_card has not run yet
        fig = self._make_cycle_figure()
        note = self._step_type_note("cycle") if fig is None else None
        self._log_plot("cycle plot", fig, note or "no cycle series can be drawn")
        self._cycle_col.objects = [
            (
                pn.pane.Bokeh(fig, sizing_mode="stretch_width")
                if fig is not None
                else pn.pane.Markdown(note or "_No cycle data._", margin=(10, 6))
            )
        ]

    # -- Batch series --------------------------------------------------------

    def _cycle_member_values(self, trace: Dict, field: str):
        """``({cycle: value}, unit)`` of a cycle column in its stored unit.

        ``({}, None)`` when the trace has no value of the field.
        """
        import pandas as pd

        df = self._cached_cycle_df(trace)
        if df is None or field not in df.columns or not hasattr(df[field], "pint"):
            return {}, None
        series = df[field]
        values = {
            float(c): float(v)
            for c, v in zip(df["cycle_count"].pint.magnitude, series.pint.magnitude)
            if not pd.isna(c) and not pd.isna(v)
        }
        return values, series.pint.units

    def _batch_series(self, members: List[Dict], field: str, unit_name: Optional[str]):
        """Aggregated ``(cycle, mean, std, n)`` rows of a batch, in the unit.

        The members are averaged in their stored unit; mean and standard
        deviation are converted afterwards (the offered units are pure scale
        factors). ``None`` when no member has a value.
        """
        per_member = []
        stored = None
        for trace in members:
            values, units = self._cycle_member_values(trace, field)
            if values:
                per_member.append(values)
                stored = stored if stored is not None else units
        rows = aggregate_batch(per_member)
        if not rows:
            return None
        factor = 1.0
        if unit_name and stored is not None:
            char_cls = _CycleGrid.SPECS[field][1]
            try:
                target = char_cls.get_pint_ureg_compatible_str(unit_name)
                factor = float((1.0 * stored).to(target).magnitude)
            except Exception:  # noqa: BLE001 - bad unit: keep the stored unit
                factor = 1.0
        return [
            (c, m * factor, None if sd is None else sd * factor, n)
            for c, m, sd, n in rows
        ]

    def _make_batch_figure(self):
        """Build the batch figure from the current assignments, or ``None``.

        One series per (batch, field) on y1 / y2: the mean per cycle as line
        and markers, the sample standard deviation as whiskers where a cycle
        has at least two values. Colours follow the batch order; charge fields
        are solid, discharge dashed; capacities use circles and efficiencies
        squares. Batches without a member and series without a value are
        skipped.
        """
        grid = self._batch_grid
        if not any(grid.axis_fields.values()):
            return None

        members: Dict[str, List[Dict]] = {name: [] for name in self._batch_names}
        for trace in self._resolve_traces():
            name = self._batch_assign.get(trace.get("key"))
            if name in members:
                members[name].append(trace)

        drawn: Dict[str, List[Any]] = {"y1": [], "y2": []}
        for i, name in enumerate(self._batch_names):
            if not members[name]:
                continue
            for axis in ("y1", "y2"):
                for field in grid.axis_fields[axis]:
                    data = self._batch_series(members[name], field, grid.units[field])
                    if data is not None:
                        drawn[axis].append((i, name, field, data))
        if not drawn["y1"] and not drawn["y2"]:
            return None

        fig = bk_figure(
            height=350,
            sizing_mode="stretch_width",
            x_axis_label="cycle_count",
            tools="pan,wheel_zoom,box_zoom,reset,save",
        )

        def spread_range(series: List[Any]):
            # explicit range: Bokeh's auto range ignores the whiskers
            return self._data_range(
                [
                    [m for _c, m, _sd, _n in d[3]]
                    + [m + sd for _c, m, sd, _n in d[3] if sd is not None]
                    + [m - sd for _c, m, sd, _n in d[3] if sd is not None]
                    for d in series
                ]
            )

        if drawn["y1"]:
            fig.yaxis[0].axis_label = grid.axis_label("y1")
            fig.y_range = Range1d(*spread_range(drawn["y1"]))
        else:
            fig.yaxis[0].visible = False
        if drawn["y2"]:
            rng = spread_range(drawn["y2"])
            fig.extra_y_ranges = {"y2": Range1d(*rng)}
            fig.add_layout(
                LinearAxis(y_range_name="y2", axis_label=grid.axis_label("y2")),
                "right",
            )

        marker_renderers = []
        for axis in ("y1", "y2"):
            y_kwargs = {"y_range_name": "y2"} if axis == "y2" else {}
            for i, name, field, rows in drawn[axis]:
                color = COLORS[i % len(COLORS)]
                source = ColumnDataSource(
                    {
                        "x": [r[0] for r in rows],
                        "y": [r[1] for r in rows],
                        "std_text": [
                            "-" if r[2] is None else f"{r[2]:.6g}" for r in rows
                        ],
                        "n": [r[3] for r in rows],
                        "batch": [name] * len(rows),
                        "field": [field] * len(rows),
                    }
                )
                label = f"{name} {field}"
                dash = "solid" if field.startswith("charge") else "dashed"
                is_capacity = grid.specs[field][0] == "capacity"
                fig.line(
                    "x",
                    "y",
                    source=source,
                    legend_label=label,
                    color=color,
                    line_width=2,
                    line_dash=dash,
                    **y_kwargs,
                )
                marker_renderers.append(
                    fig.scatter(
                        "x",
                        "y",
                        source=source,
                        legend_label=label,
                        color=color,
                        marker="circle" if is_capacity else "square",
                        size=6,
                        **y_kwargs,
                    )
                )
                spread = [r for r in rows if r[2] is not None]
                if spread:
                    fig.add_layout(
                        Whisker(
                            base="x",
                            upper="upper",
                            lower="lower",
                            source=ColumnDataSource(
                                {
                                    "x": [r[0] for r in spread],
                                    "upper": [r[1] + r[2] for r in spread],
                                    "lower": [r[1] - r[2] for r in spread],
                                }
                            ),
                            line_color=color,
                            level="annotation",
                            **y_kwargs,
                        )
                    )

        fig.add_tools(
            HoverTool(
                renderers=marker_renderers,
                tooltips=[
                    ("batch", "@batch"),
                    ("field", "@field"),
                    ("cycle", "@x"),
                    ("mean", "@y"),
                    ("std", "@std_text"),
                    ("n", "@n"),
                ],
            )
        )
        fig.legend.click_policy = "hide"
        return fig

    def _build_batch_figure(self) -> None:
        """Re-render the batch plot (the column is reassigned wholesale)."""
        if not hasattr(self, "_batch_col"):
            return  # _build_batch_cards has not run yet
        fig = self._make_batch_figure()
        note = self._step_type_note("batch") if fig is None else None
        self._log_plot("batch plot", fig, note or "no batch series can be drawn")
        self._batch_col.objects = [
            (
                pn.pane.Bokeh(fig, sizing_mode="stretch_width")
                if fig is not None
                else pn.pane.Markdown(note or "_No batch data._", margin=(10, 6))
            )
        ]

    def _step_type_note(self, what: str) -> Optional[str]:
        """Placeholder text for datasets without rows or ``step_type``, else None.

        Without ``step_type`` on any row the cycle summary cannot separate
        charge from discharge, so the cycle and batch plots stay empty. A
        dataset without rows is reported separately (nothing loaded).
        """
        traces = self._resolve_traces()
        # the lazy view leaves datasets without rows out of the traces
        n_empty = len([t for t in traces if not t.get("rows")])
        n_empty += self._unplotted_empty_datasets()
        no_step = [
            t
            for t in traces
            if t.get("rows")
            and all(getattr(r, "step_type", None) is None for r in t["rows"])
        ]
        total = len(traces) + self._unplotted_empty_datasets()
        if not total or n_empty + len(no_step) != total:
            return None

        def count(n: int) -> str:
            noun, verb = ("dataset", "has") if total == 1 else ("datasets", "have")
            return f"{n} of {total} {noun} {verb}"

        parts = []
        if n_empty:
            parts.append(f"{count(n_empty)} no rows (nothing loaded)")
        if no_step:
            parts.append(
                f"{count(len(no_step))} no step_type, so charge and discharge "
                "cannot be separated"
            )
        return f"_No {what} data: " + "; ".join(parts) + "._"

    def _unplotted_empty_datasets(self) -> int:
        """Ticked datasets that are left out of the traces for having no rows."""
        return 0

    @staticmethod
    def _log_plot(name: str, fig: Any, reason: str) -> None:
        """Log a plot rebuild: its series count, or why it shows a placeholder."""
        if fig is None:
            _view_log.info("%s shows a placeholder: %s", name, reason)
            return
        try:
            series = sum(len(legend.items) for legend in fig.legend)
        except Exception:  # noqa: BLE001 - the count is only informative
            series = -1
        _view_log.debug("%s rebuilt: %d series", name, series)

    def _build_plot(self) -> None:
        """Override BaseDataView: render the initial plot into ``_plot_col``."""
        super()._build_plot()  # sets self._plot_col + self._plot_card
        self._build_figure()

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
            self._cycle_axis_card,
            self._batches_card,
            self._batch_axis_card,
            self._config_card,
        ]

    @property
    def main_cards(self) -> List[Any]:
        return [self._plot_card, self._cycle_card, self._batch_card, self._log_card]
