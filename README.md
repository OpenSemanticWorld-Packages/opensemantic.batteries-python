[![PyPI-Server](https://img.shields.io/pypi/v/opensemantic.batteries.svg)](https://pypi.org/project/opensemantic.batteries/)
[![Coveralls](https://img.shields.io/coveralls/github/OpenSemanticWorld-Packages/opensemantic.batteries/main.svg)](https://coveralls.io/r/OpenSemanticWorld-Packages/opensemantic.batteries)

# opensemantic.batteries

> Battery-domain Python models derived from the page package
> `world.opensemantic.batteries`, plus a typed, unit-aware battery cycling data
> model with cycler-file importers.

The package has two parts:

1. **Metadata models** - auto-generated Pydantic models (v1 and v2) for the
   battery domain: cells, modules and packs, cell formats and chemistries,
   electrode and electrolyte materials, electrochemical and mechanical tests,
   test procedures, and battery testing devices.
2. **Cycling data model** - the OSW-generated `ElectrochemicalCyclingDataset` /
   `CyclingDataRow` and `CyclingDatasetController`, which adds the pandas / pint
   round-trip and the file distribution handling, plus importers for real cycler
   export files (Maccor).

Builds on
[opensemantic.core](https://github.com/OpenSemanticWorld-Packages/opensemantic.core-python),
[opensemantic.base](https://github.com/OpenSemanticWorld-Packages/opensemantic.base-python),
[opensemantic.lab](https://github.com/OpenSemanticWorld-Packages/opensemantic.lab-python)
and
[opensemantic.characteristics.quantitative](https://github.com/OpenSemanticWorld-Packages/opensemantic.characteristics.quantitative-python).

## Data flow

Vendor cycler exports are parsed into a unified, unit-aware dataset, uploaded to
an OSL wiki (the shared store of record), then browsed, queried, downloaded and
plotted through the dashboard. The OSL wiki sits at the centre: ingestion
**writes** to it, the viewer **reads** from it.

```mermaid
flowchart TB
    file[("vendor-specific format")]
    unified[("unified dataset")]
    osl[("OSL wiki<br/>store of record")]
    view["plot"]

    file -- parse --> unified
    unified -- upload --> osl
    osl -- query & download --> view
```

### Detailed flow

```mermaid
flowchart TB
    file[("vendor-specific format")]

    subgraph ingest["Ingest"]
        direction TB
        parse["parse"]
        unify["unify → dataset<br/>(unit-aware)"]
        serialize["serialize + upload"]
        parse --> unify --> serialize
    end

    osl[("OSL wiki<br/>store of record")]

    subgraph viewer["Dashboard"]
        direction TB
        browse["browse category trees"]
        select["select cell(s) + procedure(s)"]
        query["query matching datasets"]
        download["download dataset"]
        plot["plot"]
        browse --> select --> query --> download --> plot
    end

    file --> parse
    serialize -- upload --> osl
    osl -- categories / instances --> browse
    osl -- matching datasets --> query
```

### Multiple Users

```mermaid
flowchart TB
    vA[("vendor format A")]
    vB[("vendor format B")]

    subgraph prodA["Producer A — full upload"]
        direction TB
        parseA["parse"] --> pubA["upload Dataset + Distribution (File)"]
    end
    subgraph prodB["Producer B — metadata only"]
        direction TB
        parseB["parse"] --> pubB["upload Dataset (metadata)"]
        parseB -. File kept local .-> localB[("local File store")]
    end

    vA --> parseA
    vB --> parseB

    osl[("OSL wiki<br/>store of record")]

    pubA -- Dataset + File --> osl
    pubB -- Dataset metadata --> osl

    subgraph cons1["Consumer — plotting"]
        direction TB
        sel1["select dataset"] --> plot["plot"]
    end
    subgraph cons2["Consumer — analytics / ML"]
        direction TB
        sel2["select dataset"] --> analyze["analytics / postprocessing / ML"]
    end

    osl -- query & download --> sel1
    osl -- query metadata --> sel2
    analyze -- "🔒 gated File request" --> localB
```

Stage status today: **parse** and **unify** and the read-side
**browse → query → download → plot** path are implemented; the **upload** step
(serialising the dataset and writing it to the wiki) is not yet wired in the
library — the viewer currently reads pages that were uploaded out of band.

## Installation

```bash
pip install opensemantic.batteries              # metadata models only
pip install opensemantic.batteries[maccor]      # + Maccor cycler-file import
pip install opensemantic.batteries[controller]  # + controller extras (via opensemantic.lab)
pip install opensemantic.batteries[view]        # + dashboard view extras (via opensemantic.lab)
```

## Metadata models

The generated models mirror the OSW schema classes. Every model is an
`OswBaseModel`, so it carries an OSW identity (`uuid`, `type`) and can produce
its IRI - a *type* (e.g. `BatteryCellType`) resolves to a `Category:` IRI, a
concrete instance (e.g. `BatteryCell`) to an `Item:` IRI:

```python
import opensemantic.batteries as batteries        # Pydantic v2 models
import opensemantic.batteries.v1 as batteries_v1   # Pydantic v1 models

# A cell *type* (a Category) links a chemistry, form factor and format.
# "range" fields accept the linked object directly.
cell_type = batteries.BatteryCellType(
    label=[{"text": "18650 NMC"}],
    battery_chemistry=batteries.BatteryChemistry(
        label=[{"text": "Lithium-ion (NMC)"}]
    ),
    cell_form_factor=batteries.BatteryCellFormFactor(
        label=[{"text": "Cylindrical"}]
    ),
    cell_format=batteries.BatteryCellFormat(label=[{"text": "18650"}]),
)
cell_type.get_iri()                      # 'Category:OSW...'  (a type -> Category)
cell_type.battery_chemistry.get_iri()    # 'Item:OSW...'

# A concrete cell (an Item) with its electrode / electrolyte parts.
cell = batteries.BatteryCell(
    label=[{"text": "My 18650 cell #1"}],
    positive_electrode=batteries.Electrode(label=[{"text": "NMC cathode"}]),
    negative_electrode=batteries.Electrode(label=[{"text": "Graphite anode"}]),
    electrolyte=batteries.Electrolyte(label=[{"text": "LiPF6 in EC:DMC"}]),
)
cell.get_iri()                           # 'Item:OSW...'
cell.positive_electrode.get_iri()        # 'Item:OSW...'

# Linked ("range") fields serialise as IRI references:
cell_type.to_json()["battery_chemistry"]  # 'Item:OSW...'
```

`BatteryCellType` also exposes `positive_electrode` / `negative_electrode` /
`reference_electrode` / `electrolyte` / `separator` as electrode/material *type*
references (IRI strings), while `BatteryCell` carries the concrete `Electrode`,
`Electrolyte` and `Separator` parts.

### Available classes

Cells, modules and packs:
`BatteryCell`, `BatteryCellType`, `BatteryModule`, `BatteryPack`,
`BatteryModuleWithSensors`, `ElectrochemicalCell`,
`ElectrochemicalEnergyStorageDevice`, `EESD`, `EESDType`.

Formats and form factors:
`BatteryFormat`, `BatteryCellFormat`, `BatteryCellFormatType`,
`CoinCellFormat`, `CylindricalCellFormat`, `PouchCellFormat`,
`PrismaticCellFormat`, `SwagelockCellFormat`, `FormFactor`,
`BatteryCellFormFactor`.

Chemistry and materials:
`BatteryChemistry`, `ChemicalSystem`, `BatteryCellMaterial`,
`BatteryElectrodeMaterial`, `BatteryElectrolyteMaterial`, `Electrolyte`,
`ElectrolyteAdditive`, `Electrode`, `BatteryElectrodeType`, `CurrentCollector`,
`Separator`.

Tests, procedures and devices:
`ElectrochemicalTest`, `ElectrochemicalTestProcedure`, `BatteryTestProcedure`,
`BatteryStatePreparationTestProcedure`, `FormationTestProcedure`,
`PostMortemExperiment`, `BatteryCellOpening`, `BatteryCycler`,
`ElectrochemicalTestingDevice`.

(See `src/opensemantic/batteries/_model.py` for the full set of ~95 classes,
including the shared base and enumeration types.)

### Linking a test to its procedure

`ElectrochemicalTest.test_procedure` is a list of `TestProcedureItem`, and the
item references the procedure by its OSW IRI string (`procedure.get_iri()`),
not by the object:

```python
test = ElectrochemicalTest(
    label=[Label(text="Cell A - Formation")],
    test_procedure=[TestProcedureItem(test_procedure_instance=procedure.get_iri())],
    output=[dataset],
)
```

`BatteryDataView` resolves those IRIs back through `procedure_objects`; a
single `.protocol` object is still accepted as a legacy fallback. `output` is a
**list** of datasets, and the dashboard plots the first one's `.data`.

## Pydantic layers (v1 and v2)

Every generated model exists twice:

| import path | pydantic |
|---|---|
| `opensemantic.batteries` | v2 |
| `opensemantic.batteries.v1` | v1 |

They are separate class hierarchies, and mixing them fails: a v1 value passed
where a v2 model is expected does not validate. The subtle half is units -
**each layer has its own unit enums**. Passing a v2 `VoltageUnit` member to a
v1 value's `to_unit()` raises `UndefinedUnitError`, because the member is a
`str` subclass that the v1 code routes through its string branch and parses as
a unit name. That bug reached the dashboard once.

Pick one layer per module and stay in it. The dashboard, the examples and
`CyclingDatasetController` use **v1** throughout, matching the
`opensemantic.characteristics.quantitative.v1` values of the row fields; the
package root stays v2 like every other generated model there.

## Cycling data model

`ElectrochemicalCyclingDataset` holds its rows in `.data`, an ordered list of
`CyclingDataRow`. Each row field is a typed, unit-aware quantity, so the field
name stays unit-agnostic and the unit lives in the value:

| field | quantity | default unit | notes |
|---|---|---|---|
| `test_time` | Time | second | required |
| `voltage` | Voltage | volt | required |
| `current` | Electric current | ampere | required |
| `cycle_count`, `step_count` | Dimensionless | dimensionless | optional |
| `step_time` | Time | second | optional |
| `capacity` | Electric charge | coulomb | optional |
| `energy` | Energy | joule | optional |

> **Note.** `ElectrochemicalCyclingDataset` / `CyclingDataRow` are OSW-generated
> models from the schema package. Work with them through
> `CyclingDatasetController`, which carries the conversion methods. A `label` is
> required on the dataset.
>
> The controller is built on the **v1** models, matching the
> `opensemantic.characteristics.quantitative.v1` values of the row fields, so
> import the row class from `opensemantic.batteries.v1`. The package root stays
> v2, like every other generated model there.

```python
from opensemantic.batteries import CyclingDatasetController
from opensemantic.batteries.v1 import CyclingDataRow
from opensemantic.core.v1 import Label

ds = CyclingDatasetController(
    label=[Label(text="example")],
    data=[
        CyclingDataRow(
            test_time={"value": 0.0},
            voltage={"value": 3.0},
            current={"value": 0.0},
        ),
        CyclingDataRow(
            test_time={"value": 1.0},
            voltage={"value": 3.1},
            current={"value": 0.5},
        ),
    ],
)
```

### Importing cycler files

`read_maccor` parses a Maccor text / MIMS export into an
`ElectrochemicalCyclingDataset` (labelled with the source file name; requires the
`[maccor]` extra). The five formats produced by
[maccor-utility](https://github.com/OpenBattTools/maccor-utility) are supported
and detected from the file name; pass `fmt=` to override:

```python
from opensemantic.batteries import read_maccor

ds = read_maccor("cell_export2.024.txt")          # format detected from the name
ds = read_maccor("data.txt", fmt="maccor_export2")  # or set it explicitly
len(ds.data)
```

Supported formats: `maccor_export1`, `maccor_export2`, `mims_client1`,
`mims_client2`, `mims_server2`. To add another cycler, subclass `CyclerImporter`
and implement `to_dataframe()`.

### DataFrames (pandas + pint)

`ds.to_df()` yields a [pint-pandas](https://github.com/hgrecco/pint-pandas)
DataFrame (one column per field, unit carried by the dtype).
`CyclingDatasetController.from_df(df, label=...)` is the inverse; it maps columns
to the fixed `CyclingDataRow` schema (converting each to the field's default
unit) and **ignores** any extra column:

```python
from opensemantic.batteries import CyclingDatasetController

df = ds.to_df()
df["voltage"].dtype                       # pint[volt][Float64]

# unit-aware math on the frame; 'power' is not a row field, so it stays on the
# DataFrame only (from_df drops it)
df["power"] = df["voltage"] * df["current"]

enriched = CyclingDatasetController.from_df(df, label="derived")
```

Both methods come from `DatasetController` in `opensemantic.base`, so any
dataset with typed rows gets them.

### Serialisation

Compact JSON round-trip - `exclude_defaults` drops values left at their default
unit/type, leaving just the number, and the canonical units are restored on load:

```python
payload = enriched.to_json(exclude_defaults=True)
payload["data"][1]["voltage"]             # {'value': 3.1}

restored = CyclingDatasetController.from_json(payload)
restored.data[1].voltage.unit.name        # 'volt'
```

Unit-aware CSV - pint-pandas `dequantify()` writes a dedicated unit header line,
keeping the column keys unit-free:

```python
print(enriched.to_df().pint.dequantify().to_csv())
# ,test_time,voltage,current,...
# unit,second,volt,ampere,...
# 0,0.0,3.0,0.0,...
```

A runnable end-to-end example (import a Maccor file, derive a column, serialise,
re-import, export CSV) is in
[`examples/import_maccor.py`](examples/import_maccor.py).

## Dashboard (view)

`opensemantic.batteries.view` provides an interactive Panel/Bokeh dashboard,
`BatteryDataView`, for plotting cycling data, plus `OOLDTreeBuilder` for driving
its cell / procedure trees straight from Python objects. Units are switched via
the characteristics' own pint-backed `.to_unit()` (no hard-coded factor tables).

The sidebar has an **Instances** card that live-lists the test runs matching the
current cell + procedure selection; each has a checkbox so you can pick exactly
which datasets are plotted.

Plots live in a single ordered stack. Exactly one plot is **active** — the one
the sidebar selection drives — and it shows a blue **❄ Freeze plot** button;
every other plot is a frozen snapshot showing an **Unfreeze** button. Each
plot's full state is a `PlotState` (Pydantic) — the selected cells, procedures,
per-instance toggles, axis assignment and unit choices. **Freezing** drops a
static snapshot (that `PlotState` + the figure) directly below the active plot,
which stays put and keeps tracking the sidebar, so you can compare
configurations. **Unfreezing** a snapshot swaps roles *in place* — nothing is
reordered: the clicked plot becomes active where it sits (its button turns into
the blue Freeze) and its saved `PlotState` is restored into every tab (including
the tree checkboxes), while the previously-active plot freezes where it sits (its
button turns into Unfreeze). Every plot also has a **Delete** button: deleting a
frozen plot removes that snapshot; deleting the active plot promotes a neighbour
(or leaves a fresh empty plot if it was the last one).

```bash
pip install opensemantic.batteries[view]
panel serve examples/battery_dashboard.py --dev
```

```python
from opensemantic.batteries.view import BatteryDataView, OOLDTreeBuilder, PythonSource, has_type
```

See [`src/opensemantic/batteries/view/README.md`](src/opensemantic/batteries/view/README.md)
for the architecture (it extends `BaseDataView` from `opensemantic.base.view`),
the unit-conversion design, and the v1/v2 layer pitfall. Runnable examples:
[`examples/battery_dashboard.py`](examples/battery_dashboard.py) and
[`examples/battery_tree_example.py`](examples/battery_tree_example.py).

## Examples

Commands run from the repository root unless noted. The OSL scripts default to
`accounts.pwd.yaml` in the working directory, so they are run from `examples/`
(see [`examples/accounts.pwd.yaml.template`](examples/accounts.pwd.yaml.template)),
or given `--cred-filepath`.

### Dashboards

```bash
pip install -e ".[view]"

panel serve examples/battery_dashboard.py --dev         # synthetic data
panel serve examples/battery_dashboard_maccor.py --dev  # real Maccor exports
panel serve examples/battery_tree_example.py --dev      # tree only
panel serve examples/battery_dashboard_lazy.py --dev    # lazily loaded trees

pip install -e ".[osl]"
panel serve examples/battery_dashboard_OSL.py --dev     # trees + data from OSL
```

### Importing and converting

```bash
pip install -e ".[maccor]"

python examples/import_maccor.py          # cycler file -> dataset -> DataFrame
python examples/roundtrip_maccor_json.py  # dataset -> compact JSON -> dataset
```

### Working with OpenSemanticLab (OSL)

Both upload scripts are a dry run unless `--store` is passed.

```bash
pip install -e ".[osl,maccor]"   # maccor only needed by the large-dataset script
cd examples

# the example tree: three cells, three procedures, six tests
python upload_example_data_OSL.py
python upload_example_data_OSL.py --store

# a large dataset, rows stored out-of-band in a WikiFile
python upload_large_dataset_OSL.py --dataset cell_c
python upload_large_dataset_OSL.py --dataset cell_d --store

# the same as CSV: the pint-pandas frame with its unit header row. After
# --store the rows are materialized back from the file as a round-trip check.
python upload_large_dataset_OSL.py --dataset cell_d --format csv --store

# the semantic searches the dashboard backend runs
python osl_queries.py                 # list the available queries
python osl_queries.py --query datasets
```

Both accept `--domain` and `--cred-filepath`; `--dataset` takes `cell_c`,
`cell_d` or `cell_e`, `--format` either `json` (typed values) or `csv`, and
`--query` one of `cell_subcategories`,
`cell_instances`, `procedure_subcategories`, `procedure_instances`,
`tests_of_cell`, `tests_of_cell_and_procedure` or `datasets`.

### Tests

```bash
pytest                      # or: tox

# the OSL round-trip, opt-in because it writes to OSL
TEST_OSL_DOMAIN=wiki-dev.open-semantic-lab.org   TEST_OSL_CRED_FILEPATH=examples/accounts.pwd.yaml   pytest tests/test_dataset_distribution_wiki.py
```

## Note

The models in `src/opensemantic/batteries/_model.py` and
`src/opensemantic/batteries/v1/_model.py` are generated by the
[osw-python-package-generator](https://github.com/OpenSemanticWorld-Packages/osw-python-package-generator)
from the `world.opensemantic.batteries` schema package. Do not edit them
manually. The cycling data helpers (`_cycling.py`) and importers (`_importers/`)
are hand-written around those generated classes.

Hand-written extensions subclass or import the generated classes and live in
their own modules, so regeneration never touches them. Runtime dependencies
belong in `setup.cfg` (`[options] install_requires` and `extras_require`), not
in `pyproject.toml`: this is a PyScaffold `src/` layout with `find_namespace:`
discovery over the PEP 420 `opensemantic` namespace.
