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
| `_battery_utils.py` | `build_oold_tree_source`, `get_checked_instance_ids`, `inject_axis_children` |

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
  OOLDTreeBuilder                  ── Python objects → Wunderbaum tree source
  _battery_utils                   ── tree-source + checkbox helpers
```

`BatteryDataView` **extends** `BaseDataView` (the same mixin `DataToolView` and
`ProcessObjectView` use in base-python), so it inherits the shared plot/log/config
cards and — crucially — the `_numeric()` unit-conversion path.

### `BatteryDataView` inputs

Each item in `tests` must expose:

- `.device_under_test` — list of cell objects
- `.protocol` — the procedure object
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
