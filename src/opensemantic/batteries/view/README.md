# opensemantic.batteries.view

Panel/Bokeh dashboard for battery cycling data, plus a generic OO-LD →
Wunderbaum tree builder. Battery-specific; the shared dashboard infrastructure
it builds on (`BaseDataView`, config, unit helpers) lives in
`opensemantic.base.view`.

Requires the `[view]` extra:

```bash
pip install -e ".[view]"   # pulls opensemantic.base[view] + opensemantic.lab[view]
```

## Modules

| module | contents |
|---|---|
| `_battery_dashboard.py` | `BatteryDataView` — the cycling-data dashboard |
| `_oold_tree.py` | `OOLDTreeBuilder`, `PythonSource`, `LazySource`, `RelationSpec`, `has_type`, `field_rel` |
| `_battery_utils.py` | `build_oold_tree_source`, `get_checked_instance_ids`, `set_selected_instances`, `inject_axis_children` |

All public names are re-exported from `opensemantic.batteries.view`:

```python
from opensemantic.batteries.view import (
    BatteryDataView,
    OOLDTreeBuilder, PythonSource, LazySource, RelationSpec, has_type, field_rel,
)
```

## Quick start

A runnable version is in [`examples/battery_dashboard.py`](../../../../examples/battery_dashboard.py).

```python
import panel as pn
from opensemantic.batteries.v1 import BatteryCell
from opensemantic.batteries.view import BatteryDataView, OOLDTreeBuilder, PythonSource, has_type
from battery_example_data import (
    ElectrochemicalTestProcedure, cell_a, cell_b, cell_c,
    aging_test_a, aging_test_b, formation_procedure, test_cell_a_aging_a, ...,
)

pn.extension()

# Build the cell tree from Python objects — no hand-written nodes/edges dicts.
# has_type() links each instance to its Python class; the builder walks the MRO
# (SubClassOf) up to `ceiling`, which becomes the tree root.
cell_builder = OOLDTreeBuilder(
    source=PythonSource([cell_a, cell_b, cell_c]),
    relations=[has_type()],
    ceiling=BatteryCell,
)
proc_builder = OOLDTreeBuilder(
    source=PythonSource([aging_test_a, aging_test_b, formation_procedure]),
    relations=[has_type()],
    ceiling=ElectrochemicalTestProcedure,
)

view = BatteryDataView(
    tests=[test_cell_a_aging_a, ...],
    cell_nodes=cell_builder.build_nodes(),
    cell_edges=cell_builder.build_edges(),
    cell_objects=cell_builder.get_object_map(),
    procedure_nodes=proc_builder.build_nodes(),
    procedure_edges=proc_builder.build_edges(),
    procedure_objects=proc_builder.get_object_map(),
)
view.servable()
```

Run it: `panel serve examples/battery_dashboard.py --dev`.

## How it fits together

```
opensemantic.base.view            (shared, stays in base-python)
  BaseDataView   ── plot / log / config cards, _numeric() unit conversion
  COLORS, DashboardConfig, get_available_units
        ▲
        │ imported by
        │
opensemantic.batteries.view       (this package)
  BatteryDataView(BaseDataView)    ── cell tree + procedure tree
                                      + instances list + axis/unit grid
                                      + freeze/unfreeze plot snapshots
  OOLDTreeBuilder                  ── Python objects → Wunderbaum tree source
  _battery_utils                   ── tree-source + checkbox/selection helpers
```

`BatteryDataView` **extends** `BaseDataView` (the same mixin `DataToolView` and
`ProcessObjectView` use in base-python), so it inherits the shared plot/log/config
cards and — crucially — the `_numeric()` unit-conversion path.

### `BatteryDataView` inputs

Each item in `tests` must expose:

- `.device_under_test` — list of cell objects
- `.test_procedure` — list of `TestProcedureItem`, each whose
  `.test_procedure_instance` is the procedure's OSW IRI string; the view
  resolves those back to procedure objects via `procedure_objects`.
  (Legacy: a single `.protocol` procedure object is still accepted as a
  fallback — used by the Maccor example.)
- `.output.data` — list of row objects (each a `QuantityValue`-typed
  characteristic per field: `test_time`, `voltage`, `current`, …)

The cell/procedure `*_nodes`/`*_edges`/`*_objects` come straight from an
`OOLDTreeBuilder` (`build_nodes()`, `build_edges()`, `get_object_map()`).

### Instances card

Below the cell and procedure cards, an **Instances** card lists the test runs
whose cell *and* procedure are both currently checked — i.e. the exact set that
`_resolve_traces()` would plot. It refreshes live on every tree change
(`_on_tree_change` → `_refresh_instances`).

Each matching instance gets a checkbox (labelled with its output dataset's label,
falling back to the test-run label), checked by default. Unchecking one removes
that instance from the plot without changing the tree selection —
`_resolve_traces()` skips any instance whose toggle is off. When no cell or
procedure is selected, the card shows a placeholder prompt instead.

Toggle state lives in `self._instance_selections`, keyed by the test's index in
`tests`, so it **persists across refreshes**: re-selecting a cell/procedure keeps
your prior on/off choices, while newly matching instances default to on. The
shared matching logic lives in `_matching_tests()`, used by both the card and
`_resolve_traces()`.

### Sidebar order & card heights

The sidebar cards (`sidebar_cards`) are, top to bottom: **Cells**,
**Procedures**, **Instances**, **Axes & Units**, **Configuration**. The Cell and
Procedure tree cards share a fixed height (`_TREE_CARD_HEIGHT`) and scroll, so
they sit level regardless of how deep either tree is. The axis / unit grid is its
own **Axes & Units** card (`_build_axis_card`, wiring `_axis_checkboxes` and
`_unit_selects`) placed below the Instances card — not nested under the
Procedure tree.

### Freeze / unfreeze

All plots live in **one ordered list**, `self._plots` — each entry a record
`{"id", "state": PlotState, "active": bool, "figure", "panel"}` — rendered as
bordered panels inside a single `self._plots_col`. Exactly one record has
`active=True`: it is rendered *live* from the current selection with a blue
**❄ Freeze plot** button; every other record is a static snapshot with an
**Unfreeze** button. Because the active plot is just whichever record is flagged
active — not a structurally separate area — freezing / unfreezing flips that flag
*in place* and the visible order never shuffles (this is what fixed the old
"unfreeze moves the plot to the top" bug).

Each plot's full state is a **`PlotState`** (a small Pydantic model): the
selected cell ids, procedure ids, per-instance toggles, axis map and per-field
unit selections. It stores only JSON-friendly values (id / enum-name strings,
ints, bools) — never live OSW objects — so a snapshot is trivial to copy and
compare. `_capture_state()` builds one from the live widgets; `_apply_state()`
writes one back into them.

- **Freeze** (`_on_freeze_click`) snapshots the active plot: it captures a
  `PlotState` via `_capture_state()` and an *independent* Bokeh figure
  (`_make_figure()`) into a frozen record (`_make_plot_record(..., active=False)`)
  **inserted directly below the active record** (`idx + 1`), so a copy drops down
  exactly one slot. The active record stays active in place and keeps tracking
  the sidebar.
- **Unfreeze** (`_on_unfreeze_click`, one button per frozen panel) swaps roles
  **in place**, so the visible order never shuffles: the currently-active record
  is frozen *in its own slot* (`_freeze_record_in_place` — its Freeze button
  becomes Unfreeze), and the clicked record becomes active *in its own slot* (its
  Unfreeze button becomes the blue Freeze). Then `_apply_state()` writes the
  unfrozen record's saved `PlotState` back into every tab — trees, instances
  card, axis grid and unit dropdowns — so the sidebar jumps to it. (If the
  previously-active plot was empty, it is dropped rather than kept as an empty
  frozen snapshot.)
- **Delete** (`_on_delete_click`) — every plot has one. Deleting a frozen record
  just removes it. Deleting the active record removes it and promotes the plot
  that fell into its slot (the one directly below — the most recent freeze — or
  the one above if it was last), restoring that plot's `PlotState`; if it was the
  only plot, a fresh empty active plot takes its place.

Key design points:

- `_make_figure()` returns a figure (or `None`); `_build_figure()` rebuilds *only
  the active record's panel* from it and then reassigns `self._plots_col.objects`
  wholesale via `_render_plots()`. That wholesale reassignment — exactly how
  Panelini syncs its own panes — is what makes the browser pane repaint
  immediately on a role swap or selection change, rather than lagging a render
  until the next interaction (the old "it only updates when I change the
  selection again" bug). Freeze reuses `_make_figure()` to get a *separate*
  figure object, so the frozen and active panes never share a Bokeh model.
- `_apply_state()` restores each tree by **rebuilding the widget**
  (`_restore_tree()` → `_make_tree()` with a source whose `selected` flags come
  from `set_selected_instances`) and swapping the new widget into its card
  (`card[0] = new_tree`). A rebuild — not a `tree.source` reassignment — is what
  makes the browser checkboxes actually repaint: Wunderbaum diffs a reassigned
  `source` through a `change:source` echo guard that swallows the update whenever
  the last tree edit came from the browser (exactly the Unfreeze case), leaving
  the checkboxes stale. A fresh widget has no prior state to diff against and
  renders its flags straight from `source`, and replacing a card child is a
  structural change Panel always syncs. (Plot correctness itself never depends on
  the JS round-trip — it reads the restored `self._selected_*_ids` directly.)
- The restored source is derived from a **pristine, plain-data copy** of each
  tree's source (`self._cell_tree_source` / `self._proc_tree_source`, captured in
  `_build_cell_tree` / `_build_procedure_card`), *not* from the live
  `tree.source`. Once the browser has edited a Wunderbaum, its `source` holds
  live model references, and the `copy.deepcopy` inside `set_selected_instances`
  would try to copy a Tornado `IOLoop` and raise `RuntimeError`. Restoring from
  the pristine source (guaranteed plain dicts/lists) avoids that entirely — the
  widget is always handed a fresh `set_selected_instances(pristine, …)` copy, so
  the pristine object itself is never mutated.
- A `self._restoring` guard suppresses the per-widget watchers
  (`_on_tree_change`, `_on_unit_change`, `_on_instance_toggle`,
  `_on_checkbox_change`) during a restore, so the many widget syncs collapse into
  a single `_build_figure()` at the end.

### `OOLDTreeBuilder`

Turns in-memory Python objects into a Wunderbaum tree without hand-writing
nodes/edges. Concepts:

- `PythonSource(objects)` — objects already in memory.
- `LazySource(loader)` — objects fetched on demand (OSW, SPARQL, any backend);
  swap it in for `PythonSource` to drive the tree from a remote store.
- `RelationSpec` — a named edge rule. Helpers: `has_type()` (instance → its
  Python class) and `field_rel(field, relation)` (obj → `getattr(obj, field)`).
- `ceiling` — walk the MRO up to (and including) this class; it becomes the root.

## Unit conversion — the important design point

Row fields are typed `QuantityValue` characteristics (`Voltage`, `Time`,
`ElectricCurrent`, …). The dashboard converts units the **same way** the
process/datatool views do: it calls the value's own `.to_unit(<UnitEnum member>)`
(pint-backed), rather than any hand-maintained unit-factor table.

Mechanics:

- `_FieldChannel` wraps each field's characteristic class and carries
  `__iris__ = {"characteristic": cls.get_cls_iri()}`, so the shared
  `opensemantic.base.view` channel helpers work on it.
- Available units per field are discovered via `get_available_units(channel)`
  (from the characteristic's unit enum), which populates the per-field unit
  dropdowns — no hard-coded unit lists.
- Conversion is delegated to `BaseDataView._numeric(value, channel, target)`.

**v1/v2 pitfall (already handled, don't reintroduce):** the unit enum must come
from the **value's own class**, not the global type registry. Passing a v2
`VoltageUnit` member to a `v1` value's `.to_unit()` raises
`UndefinedUnitError('item')`, which was being swallowed silently and returned the
raw (unconverted) number. `_numeric` therefore prefers
`get_unit_enum_for_class(type(value))` before falling back to the channel's enum.
Keep example/dashboard values and their unit enums in the **same** layer (v1).

## Editing note

The shared pieces (`BaseDataView`, `_numeric`, `_channel_utils`,
`get_available_units`) live in **`opensemantic.base` at
`.../opensemantic.base-python/src/opensemantic/base/view/`** and are installed
**editable** into this venv — so changes there are live here. If you touch
conversion or channel logic, that is the file to edit, and it affects
`DataToolView` and `ProcessObjectView` too.
