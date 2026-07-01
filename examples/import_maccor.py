"""Minimal example: import a Maccor cycler export, derive a column, serialise it.

Run from the package root:

    python examples/import_maccor.py

Requires the optional Maccor importer dependency:

    pip install opensemantic.batteries[maccor]
"""

from pathlib import Path

import yaml

from opensemantic.batteries import BatteryCyclingDataset, read_maccor

HERE = Path(__file__).resolve().parent
MACCOR_DIR = HERE.parent / "tests" / "data" / "cycling" / "maccor"
SOURCE = MACCOR_DIR / "231004_test_data_export2_trimmed.024.txt"

# 1. Import the cycler export into a typed, unit-aware dataset.
dataset = read_maccor(SOURCE)
print(f"Imported {len(dataset.rows)} rows from {SOURCE.name}")

# 2. Derive an optional column with unit-aware math (power = voltage * current)
#    and round-trip it back into the typed model as a Power column.
df = dataset.to_df().iloc[100:103].reset_index(drop=True)
df["power"] = df["voltage"] * df["current"]
enriched = BatteryCyclingDataset.from_df(df)
power0 = enriched.rows[0].power
print(f"Derived column 'power' as {type(power0).__name__} in {power0.unit.name}")

# 3. Serialise the enriched dataset compactly - exclude_defaults skips values
#    left at their default (e.g. units at their default unit) - and print as YAML.
payload = enriched.to_json(exclude_defaults=True)
print(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))

# 4. Re-import into the canonical dataset to show the data is portable (default
#    units are restored on load; the derived 'power' extra is not part of the
#    canonical row model, so it is dropped here).
restored = BatteryCyclingDataset.from_json(payload)
first = restored.rows[0]
print(
    f"Re-imported {len(restored.rows)} rows; "
    f"row 0 voltage = {first.voltage.value} {first.voltage.unit.name}"
)

# 5. Export the tabular form as a unit-aware CSV: pint-pandas `dequantify()`
#    writes a dedicated unit header line, keeping the column keys unit-free.
print("\nUnit-aware CSV (a 'unit' header line carries each column's unit):")
print(enriched.to_df().pint.dequantify().to_csv())
