# CLAUDE.md — opensemantic.batteries

Guidance for working in this repository. Read this first.

## What this package is

`opensemantic.batteries` has three parts:

1. **Metadata models** (`_model.py`, `v1/_model.py`) — auto-generated Pydantic
   models for the battery domain (cells, formats, chemistries, materials, tests,
   procedures, devices). **Generated — do not edit by hand.** See the *Generated
   code* section below.
2. **Cycling data model** (`_dataset.py`) + **importers** (`_importers/`) —
   hand-written, unit-aware `BatteryCyclingDataset` with pandas/pint round-trips
   and cycler-file importers (Maccor).
3. **View / dashboard** (`view/`) — hand-written Panel/Bokeh dashboard for
   plotting cycling data, plus a generic OO-LD → tree builder. Moved here from
   `opensemantic.base` in Aug 2026 because it is battery-specific. See
   [`src/opensemantic/batteries/view/README.md`](src/opensemantic/batteries/view/README.md).

Depends on `opensemantic.core`, `opensemantic.base`, `opensemantic.lab`, and
`opensemantic.characteristics.quantitative`.

## The v1 / v2 dual-layer gotcha (read this)

Every generated model exists in **two layers**:

- **Top level** — `opensemantic.batteries` — Pydantic **v2**.
- **`.v1` subpackage** — `opensemantic.batteries.v1` — Pydantic **v1**.

They are separate class hierarchies. **Do not mix them**: a `v1` value passed
where a v2 model is expected (or vice versa) fails validation, and — subtler —
each layer has its **own** unit enums. Passing a v2 `VoltageUnit` member to a
`v1` value's `.to_unit()` raises `UndefinedUnitError` (this exact bug bit the
dashboard once; see the view README).

**Pick one layer per module and stay in it.** The view examples and dashboard
use the **v1** layer throughout because the wider OSW tooling (oold, lab) is
still v1-centric.

## Environment

- Python **3.12**, venv at `.venv/` (`.venv/Scripts/python.exe` on Windows).
- `opensemantic.base` and `opensemantic.batteries` are installed **editable**
  (`pip install -e`). Edits to the base-python `src/` tree are **live** here —
  no reinstall needed. This is why view code split across the two repos "just
  works" in this venv.
- Windows console: prefix Python invocations with `PYTHONIOENCODING=utf-8` when
  output may contain unit symbols (µ, Ω, …) or the default cp1252 codec raises
  `UnicodeEncodeError`.

```bash
# run something in the venv
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe your_script.py

# install the view extras (pulls opensemantic.base[view] + opensemantic.lab[view])
.venv/Scripts/python.exe -m pip install -e ".[view]"
```

## Layout

```
src/opensemantic/batteries/
  __init__.py         # re-exports models + dataset + importers (v2 layer)
  _model.py           # GENERATED v2 models — do not edit
  v1/_model.py        # GENERATED v1 models — do not edit
  _dataset.py         # hand-written cycling data model (BatteryCyclingDataset)
  _importers/         # hand-written cycler-file importers (Maccor)
  _controller.py      # controller extras (optional import)
  view/               # hand-written dashboard + OO-LD tree builder (see its README)
examples/             # runnable panel-serve examples + import demos
tests/
```

## Running things

```bash
# dashboard (opens a browser)
.venv/Scripts/panel serve examples/battery_dashboard.py --dev

# minimal tree-only example
.venv/Scripts/panel serve examples/battery_tree_example.py --dev

# tests
.venv/Scripts/python.exe -m pytest
# or via tox
tox

# demo GIFs (Playwright drives panel serve, frames -> imageio GIF in docs/media/)
.venv/Scripts/python.exe -m pip install -e ".[docs]" && playwright install chromium
.venv/Scripts/python.exe docs/media/generate_battery_dashboard.py        # synthetic
.venv/Scripts/python.exe docs/media/generate_battery_dashboard_maccor.py  # Maccor
```

Examples import their sample data from `examples/battery_example_data.py`
(`from battery_example_data import ...`), which relies on `panel serve` adding
the script's directory to `sys.path`. Run them from the repo root as shown.

## Generated code — regeneration & rules

`_model.py` and `v1/_model.py` are produced by
[osw-python-package-generator](https://github.com/OpenSemanticWorld-Packages/osw-python-package-generator)
from the `world.opensemantic.batteries` schema package.

- **Never hand-edit** the generated files — changes are lost on regeneration.
- To add domain concepts, change the **schema** upstream and regenerate.
- Hand-written extensions (`_dataset.py`, `_importers/`, `view/`) subclass or
  import the generated classes; keep them in separate modules so regeneration
  never touches them.

## Working conventions

- Real runtime dependencies live in **`setup.cfg`** (`[options] install_requires`
  and `[options.extras_require]`), **not** `pyproject.toml` — this is a
  PyScaffold `src/`-layout project with `find_namespace:` discovery and the
  PEP-420 `opensemantic` namespace.
- The `view` extra now includes both `opensemantic.lab[view]` and
  `opensemantic.base[view]`.
- Match the surrounding module's Pydantic layer (v1 vs v2) and its import style.
- Prefer importing existing generated classes (e.g. `BatteryCell`,
  `AgingTestProcedure`, `FormationTestProcedure`,
  `ElectrochemicalTestProcedure`) over redefining them in examples.
- A test links its procedure(s) via `test_procedure` — a list of
  `TestProcedureItem` whose `test_procedure_instance` is the procedure's OSW IRI
  string (`obj.get_iri()`), **not** the object. `BatteryDataView` resolves those
  IRIs back through `procedure_objects`; a single `.protocol` object still works
  as a legacy fallback (Maccor example).
