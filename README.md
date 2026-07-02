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
2. **Cycling data model** - a hand-written `BatteryCyclingDataset` built on the
   quantitative characteristics, with pandas / pint round-trips and importers
   for real cycler export files (Maccor).

Builds on
[opensemantic.core](https://github.com/OpenSemanticWorld-Packages/opensemantic.core-python),
[opensemantic.base](https://github.com/OpenSemanticWorld-Packages/opensemantic.base-python),
[opensemantic.lab](https://github.com/OpenSemanticWorld-Packages/opensemantic.lab-python)
and
[opensemantic.characteristics.quantitative](https://github.com/OpenSemanticWorld-Packages/opensemantic.characteristics.quantitative-python).

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

## Cycling data model

`BatteryCyclingDataset` is an ordered list of `CyclingDataRow`. Each row field is
a typed, unit-aware characteristic (from
`opensemantic.characteristics.quantitative`), so the field name stays
unit-agnostic and the unit lives in the value:

| field | type | notes |
|---|---|---|
| `test_time` | `Time` | required |
| `voltage` | `Voltage` | required |
| `current` | `ElectricCurrent` | required |
| `cycle_count`, `step_count` | `Count` | optional |
| `step_time` | `Time` | optional |
| `capacity` | `ElectricCharge` | optional |
| `energy` | `Energy` | optional |

```python
from opensemantic.batteries import BatteryCyclingDataset, CyclingDataRow
from opensemantic.characteristics.quantitative.v1 import (
    ElectricCurrent,
    Time,
    Voltage,
)

ds = BatteryCyclingDataset(
    rows=[
        CyclingDataRow(
            test_time=Time(value=0.0),
            voltage=Voltage(value=3.0),
            current=ElectricCurrent(value=0.0),
        ),
        CyclingDataRow(
            test_time=Time(value=1.0),
            voltage=Voltage(value=3.1),
            current=ElectricCurrent(value=0.5),
        ),
    ]
)
```

### Importing cycler files

`read_maccor` parses a Maccor text / MIMS export into a `BatteryCyclingDataset`
(requires the `[maccor]` extra). The five formats produced by
[maccor-utility](https://github.com/OpenBattTools/maccor-utility) are supported
and detected from the file name; pass `fmt=` to override:

```python
from opensemantic.batteries import read_maccor

ds = read_maccor("cell_export2.024.txt")          # format detected from the name
ds = read_maccor("data.txt", fmt="maccor_export2")  # or set it explicitly
len(ds.rows)
```

Supported formats: `maccor_export1`, `maccor_export2`, `mims_client1`,
`mims_client2`, `mims_server2`. To add another cycler, subclass `CyclerImporter`
and implement `to_dataframe()`.

### DataFrames (pandas + pint)

`to_df()` yields a [pint-pandas](https://github.com/hgrecco/pint-pandas)
DataFrame (one column per field, unit carried by the dtype). `from_df()` is the
inverse and **auto-extends** the row model for any extra column, inferring the
quantity type from its pint dtype:

```python
df = ds.to_df()
df["voltage"].dtype                       # pint[volt][Float64]

# unit-aware math; power comes out as pint[watt]
df["power"] = df["voltage"] * df["current"]

enriched = BatteryCyclingDataset.from_df(df)
type(enriched.rows[0].power).__name__     # 'Power' (auto-typed extra column)
```

### Serialisation

Compact JSON round-trip - `exclude_defaults` drops values left at their default
unit/type, leaving just the number, and the canonical units are restored on load:

```python
payload = enriched.to_json(exclude_defaults=True)
payload["rows"][1]["voltage"]             # {'value': 3.1}

restored = BatteryCyclingDataset.from_json(payload)
restored.rows[1].voltage.unit.name        # 'volt'
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

## Note

The models in `src/opensemantic/batteries/_model.py` and
`src/opensemantic/batteries/v1/_model.py` are generated by the
[osw-python-package-generator](https://github.com/OpenSemanticWorld-Packages/osw-python-package-generator)
from the `world.opensemantic.batteries` schema package. Do not edit them
manually. The cycling data model (`_dataset.py`) and importers (`_importers/`)
are hand-written.
