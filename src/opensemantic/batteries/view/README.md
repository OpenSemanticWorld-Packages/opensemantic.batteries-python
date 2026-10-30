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
| `_battery_dashboard.py` | `BatteryDataView` — the in-memory cycling-data dashboard |
| `_lazy_dashboard.py` | `LazyBatteryDataView` — lazy, backend-driven variant of the dashboard |
| `_backend.py` | `BatteryDataBackend` (ABC), `TreeNode`, `Dataset` — the swappable-backend seam |
| `_osl_backend.py` | `OSLBatteryBackend`, `connect_osw` — live-OSL-wiki backend |
| `_oold_tree.py` | `OOLDTreeBuilder`, `PythonSource`, `LazySource`, `RelationSpec`, `has_type`, `field_rel` |
| `_battery_utils.py` | `build_oold_tree_source`, `get_checked_instance_ids`, `set_selected_instances`, `inject_axis_children` |

All public names are re-exported from `opensemantic.batteries.view`:

```python
from opensemantic.batteries.view import (
    BatteryDataView,
    LazyBatteryDataView, BatteryDataBackend, TreeNode, Dataset,
    OSLBatteryBackend, connect_osw,
    OOLDTreeBuilder, PythonSource, LazySource, RelationSpec, has_type, field_rel,
)
```

## Lazy dashboard & swappable backends

`BatteryDataView` materialises the whole cell/procedure graph up front from OO-LD
nodes/edges. `LazyBatteryDataView` instead loads **on demand** from a
`BatteryDataBackend`: each tree starts as one collapsed root category and only
asks the backend for a node's children when the user expands it; selecting cells
+ procedures asks the backend for the matching `Dataset`s, which flow through the
base view's unchanged axis / unit / plot pipeline.

A backend answers four things (see `_backend.py`): the two tree roots
(`cell_root` / `procedure_root`), a category's `children(iri)`, the
`datasets(cell_iris, proc_iris)` for a selection, and the `row_class` whose typed
fields drive the axis grid. Swapping the data source (a live wiki, a local cache,
a test double, a REST API) is a new `BatteryDataBackend` subclass — the view and
the example wiring don't change.

`OSLBatteryBackend` is the reference implementation over a live OSL wiki; `osw`
is imported lazily (only `connect_osw` needs it), so importing the `view` package
never requires the optional `osw` dependency — install it with the `osl` extra
(`pip install -e ".[osl]"`). Runnable wiring:
[`examples/battery_dashboard_OSL.py`](../../../../examples/battery_dashboard_OSL.py).

```python
from opensemantic.batteries.view import (
    LazyBatteryDataView, OSLBatteryBackend, connect_osw,
)
backend = OSLBatteryBackend(
    osw_obj=connect_osw("accounts.pwd.yaml"),
    row_class=CyclingDataRow,
)
LazyBatteryDataView(backend=backend).servable()
```

After the dataset titles are known, `OSLBatteryBackend.datasets` runs one
read-only `ask` query per chunk of 50 titles (`[[HasOutput::t1||t2...]]` with
the printouts `HasDut`, `HasProcedure` and `HasOutput`) and fills `cell_iri`
and `procedure_iri` of each `Dataset` from the test that outputs it. A dataset
without a test keeps `None` and logs a warning; with several tests the first
one wins and a warning is logged. A cell without a parent category goes to
"(none)" in the cell batches and logs a warning.

### Logging and the log console

The backend and the lazy view log to `logging.getLogger("opensemantic.batteries.view")`:
queries (DEBUG: query text, INFO: hits, duration and retries), row loading,
selections, plot rebuilds and mapping problems (WARNING). Configure the level and
the handlers as usual; `examples/battery_dashboard_OSL.py` reads the level from
the environment variable `OSB_VIEW_LOG` (default `INFO`).

`LazyBatteryDataView(..., show_log=True)` shows the log card (newest record
first, the last 500 records, with a "Clear" button) and a status line above the
plots ("Querying datasets for N cells x M procedures...", then "Done: K datasets
in X.X s" or "Failed: ..."). A failed wiki call returns an empty result, so
"Done" adds "(N wiki queries failed, see log)" when the backend's
`failed_queries` counter rose during the query. With an empty selection it
shows "No query:
select at least one cell and one procedure". Under `panel serve` the query and
the plots run in the next tick, so the browser shows the status first. With
`show_log=False` (default) nothing changes. If the logger has no level set,
`show_log=True` sets it to INFO; an explicit level is kept.

When no plotted dataset has a `step_type`, the cycle and batch plots show a
placeholder that names the number of datasets without it (datasets without rows
are named separately). Use
`StepTypedCyclingDataRow` as `row_class` for such data.

To try the lazy view without a wiki (no credentials, no network) run
[`examples/battery_dashboard_lazy.py`](../../../../examples/battery_dashboard_lazy.py),
which wires an in-memory `BatteryDataBackend` (the same fixture the
`tests/test_lazy_view.py` regression tests use).

### Selection is read from `source`, not from `select` events (gotcha)

`LazyBatteryDataView` learns what the user ticked by **watching each tree's
`source` param** and diffing the set of `selected` node keys — *not* from the
widget's per-node `select` event. Those events all arrive through Wunderbaum's
single `_event_data` slot, and one checkbox click fires
`click → deactivate → select → activate` within a single JS tick; Bokeh
coalesces same-tick param writes and transmits only the **last** value, so the
`select` is silently dropped and the cascade never runs in the browser (it looks
fine in direct-Python tests, which is how this regressed once). `source` is
full-state and idempotent, so coalescing can't lose it. If you touch the
selection path, drive it from `source` and keep the `tests/test_lazy_view.py`
tests (they write `source` exactly as the browser does) green.

The **Instances tab** (`_on_instance_source_change`, in the base
`BatteryDataView`, so both the eager and lazy views get it) has the same trap and
the same fix: it reads each leaf's checkbox off `source`, not from `select`
events — otherwise unchecking an instance silently fails to remove its trace.

The same single-slot coalescing bites two Python→JS channels, so the cascade
works around both: (1) all `select_node` / `expand_node` calls for one change go
out as a single atomic `batch_update` (`_tree_action` is one slot, so separate
calls would collapse to the last); (2) selecting a category expands its subtree
**one loaded node per echo round** — expanding several unloaded lazy nodes at
once would fire multiple lazy-load requests through the single `_lazy_request`
slot and load only the last. Expanding one loaded node reveals its (loaded)
children, which the next `source` echo expands, serialising the lazy loads.

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
                                      + a single plot following the selection
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
  fallback.)
- `.output` — a **list** of datasets (or a single dataset object); the view
  plots the first one's `.data` — a list of row objects (each a
  `QuantityValue`-typed characteristic per field: `test_time`, `voltage`,
  `current`, …)

The cell/procedure `*_nodes`/`*_edges`/`*_objects` come straight from an
`OOLDTreeBuilder` (`build_nodes()`, `build_edges()`, `get_object_map()`).

### Instances card

Below the cell and procedure cards, an **Instances** card lists the test runs
whose cell *and* procedure are both currently checked — i.e. the exact set that
`_resolve_traces()` would plot. It refreshes live on every tree change
(`_on_tree_change` → `_refresh_instances`).

Each matching instance is a checkable leaf in a flat `Wunderbaum` (keyed
`inst-<idx>`, labelled with its output dataset's label, falling back to the
test-run label), checked by default. Unchecking one removes that instance from
the plot without changing the tree selection — `_resolve_traces()` skips any
instance whose toggle is off. Toggles are read from the tree's **`source`** param
(`_on_instance_source_change`), which diffs each leaf's `selected` flag and
recovers the test index from `data.idx` / the `inst-<idx>` key — **not** from
per-node `select` events, which coalesce away on a checkbox click (see the "read
from source" gotcha below), so unchecking would otherwise never reach Python.
When no cell or procedure is selected, the card shows a placeholder prompt instead.

Toggle state lives in `self._instance_selections`, keyed by the test's index in
`tests`, so it **persists across refreshes**: re-selecting a cell/procedure keeps
your prior on/off choices, while newly matching instances default to on. The
shared matching logic lives in `_matching_tests()`, used by both the card and
`_resolve_traces()`.

### Sidebar order & card heights

The sidebar cards (`sidebar_cards`) are, top to bottom: **Cells**,
**Procedures**, **Instances**, **Time Series - Axes & Units**, **Cycle Series -
Axes & Units**, **Batches**, **Batch Series - Axes & Units**, **Configuration**. The Cell and
Procedure tree cards share a fixed height (`_TREE_CARD_HEIGHT`) and scroll, so
they sit level regardless of how deep either tree is. The axis / unit grid is its
own **Time Series - Axes & Units** card (`_build_axis_card`, wiring
`_axis_checkboxes`) placed below the Instances card, not nested under the
Procedure tree. The **Cycle Series - Axes & Units** card (`_build_cycle_card`)
holds a grid with the same layout as the time-series grid. The grid is the
class `_CycleGrid` (`self._cycle_grid`), which owns the widgets and the
state and takes a redraw callback. Its rows are `cycle_count`
(the fixed x: checked and disabled), `charge_capacity`, `discharge_capacity`,
`charge_coulomb_efficiency` and `discharge_coulomb_efficiency`. The state is in
`axis_fields` (fields per axis, y1 and y2) and `units` of the grid. The
rules:

- A field is on at most one axis: ticking y2 unticks its y1 and the reverse.
- Several fields may share an axis if they have the same dimension (capacity
  or efficiency). Ticking a field of the other dimension removes the fields
  already on that axis.
- One unit per axis: a newly ticked field takes the unit of the fields already
  on the axis, and a unit change applies to all fields on that axis. The
  widgets are updated under the grid's `_updating` flag, so the watchers do not
  recurse. A unit change of a field on no axis is only stored.
- Defaults: both capacities on y1 (coulomb), y2 empty. Efficiencies are shown
  in percent by default.

The **Batch Series - Axes & Units** card holds a second `_CycleGrid`
(`self._batch_grid`) with the same rows, rules and defaults; only its redraw
callback differs (`_build_batch_figure`).

The main cards (`main_cards`) are the time-series plot card, the collapsible
**Cycle Series** card, the collapsible **Batch Series** card and the log card.

### Batches card

`Dataset` (and the match dict of the lazy view) carries the optional
`cell_iri` and `procedure_iri`. `_dataset_records()` returns one record per
ticked match with the key, label, cell IRI, cell category `(iri, label)`,
procedure IRI and procedure label (`None` for unknown values). The lazy view
takes the cell category from `_parent_of`, the direct parent category that
`_descendants` and `_lazy_children` record while the tree is walked (the
first parent found wins), and the labels from `backend.page_name`. The
non-lazy view uses the class of the first device under test and the first
procedure IRI of the test. A backend that leaves the IRIs empty gives
"(none)" in the suggestions.

- **Cell batches** and **Procedure batches** replace all assignments with the
  groups of that rule (cell category label or procedure label) and keep the
  rule. They also replace the list of batch names, so names added by hand are
  dropped. Datasets without the needed value go to "(none)".
- Each ticked dataset has a row with a Select of the batch names and
  "(none)". **Add batch** adds a name from the text field (an empty or
  duplicate name is ignored) and appends it. A name stays an option when it has
  no member, but a batch without a member is not plotted. The y ranges of the
  batch plot are explicit and cover mean +/- std, because Bokeh's auto range
  ignores the whiskers.
- On a selection or instances change, `_refresh_batches()` keeps the batch of
  every known dataset key (also while it is unticked). A new key joins the
  batch the last rule gives it, else "(none)". The rows are rebuilt.
- The batch plot aggregates the cycle tables of the members
  (`_cached_cycle_df`, key = dataset key) with `aggregate_batch` in
  `_batch_summary.py`: mean over the members that have a value for the cycle
  and the sample standard deviation (`ddof=1`) for two or more. Mean and
  standard deviation are converted to the selected unit afterwards, in the
  stored unit before (coulomb, dimensionless ratio).

### Plots

The view holds three plots. The batch plot is in `self._batch_col`, built by
`_make_batch_figure()` and `_build_batch_figure()` like the cycle plot, with a
"No batch data" placeholder. The time-series plot is in `self._plot_col` (the base
class column inside the plot card). `_make_figure()` returns a Bokeh figure or
`None`, and `_build_time_series_figure()` reassigns `self._plot_col.objects`
wholesale to either the new Bokeh pane or a "No data selected" placeholder. The
cycle plot is in `self._cycle_col` (inside the Cycle Series card):
`_make_cycle_figure()` and `_build_cycle_figure()` work the same way, with a
"No cycle data" placeholder. The wholesale reassignment, which is also how
Panelini syncs its own panes, makes the browser pane repaint immediately on a
selection change, rather than lagging a render until the next interaction.

The selection watchers (`_on_tree_change`, `_on_instance_source_change`) end in
`_build_figure()`, which rebuilds the three plots and the batch rows. A
time-series axis or unit change
(`_on_checkbox_change`, `_on_unit_change`) calls only
`_build_time_series_figure()`, a cycle control change calls only
`_build_cycle_figure()`, and a batch grid control change or a batch edit calls
only `_build_batch_figure()`. The
instances card swaps in a fresh tree widget (`card[0] = new_tree`) instead of
reassigning `tree.source`, because Wunderbaum can swallow an in-place `source`
update when the last tree edit came from the browser.

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
