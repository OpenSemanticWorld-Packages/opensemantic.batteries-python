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
  A field typed as an Enum (e.g. ``step_type``) is categorical: no unit, it
  cannot be x, and as y1 / y2 it is drawn as a step line on a category axis.

Plot
  Single Bokeh figure, left y1 axis + optional right y2 axis.
  Both axis ranges are derived from data — 0 is never forced as baseline.
  The plot re-renders whenever the selection changes.

Uses BaseDataView mixin for shared plot / log / config cards.
"""

from __future__ import annotations

import logging
from enum import Enum
from functools import partial
from typing import Any, Dict, List, Optional, Tuple

import panel as pn
from bokeh.models import ColumnDataSource, FixedTicker, LinearAxis, Range1d
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
from opensemantic.batteries.view._battery_utils import (
    build_oold_tree_source,
    get_checked_instance_ids,
    set_selected_instances,
)

_logger = logging.getLogger(__name__)


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
        self._cell_tree = self._make_tree(
            set_selected_instances(source, set()), "Cell"
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

        self._build_figure()

    def _on_unit_change(self, field: str, unit: str) -> None:
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
                }
            )
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
                None
                if v is None or (not isinstance(v, Enum) and pd.isna(v))
                else (v.value if isinstance(v, Enum) else str(v))
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
        """Re-render the single plot from the current selection.

        Reassigning ``_plot_col.objects`` wholesale is what makes the
        browser pane refresh immediately (an in-place ``col[i] = x`` mutation
        can lag a render until the next interaction).
        """
        if not hasattr(self, "_plot_col"):
            return  # _build_plot has not run yet
        fig = self._make_figure()
        self._plot_col.objects = [
            pn.pane.Bokeh(fig, sizing_mode="stretch_width")
            if fig is not None
            else pn.pane.Markdown("_No data selected._", margin=(10, 6))
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
            self._config_card,
        ]

    @property
    def main_cards(self) -> List[Any]:
        return [self._plot_card, self._log_card]
