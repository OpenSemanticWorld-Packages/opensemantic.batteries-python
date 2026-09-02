"""Round-trip a loaded Maccor dataset through on-disk JSON serialization.

Loads a real Maccor cycler export into a typed, unit-aware
``ElectrochemicalCyclingDataset``, serializes it to a JSON file, reads the file
back, deserializes it, and asserts the round-tripped dataset matches the
original.

Run from the package root:

    python examples/roundtrip_maccor_json.py

Requires the optional Maccor importer dependency:

    pip install opensemantic.batteries[maccor]
"""

import json
from pathlib import Path

from opensemantic.batteries import (
    ElectrochemicalCyclingDataset,
    dataset_to_df,
    read_maccor,
)

HERE = Path(__file__).resolve().parent
MACCOR_DIR = HERE.parent / "tests" / "data" / "cycling" / "maccor"
SOURCE = MACCOR_DIR / "raz-IDCyLIB-E1-full cell2_mims_client1_trimmed.txt"
OUT_JSON = HERE / "roundtrip_maccor_dataset.json"


def main() -> None:
    # 1. Import the cycler export into a typed, unit-aware dataset.
    dataset = read_maccor(SOURCE, fmt="mims_client1")
    print(f"Imported {len(dataset.data)} rows from {SOURCE.name}")

    # 2. Serialize to a JSON-compatible payload and write it to disk.
    #    exclude_defaults keeps the file compact (values left at their default,
    #    e.g. a characteristic's default unit, are omitted and restored on load).
    payload = dataset.to_json(exclude_defaults=True)
    with OUT_JSON.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f"Wrote JSON to {OUT_JSON} ({OUT_JSON.stat().st_size} bytes)")

    # 3. Read the file back and deserialize into the typed model.
    with OUT_JSON.open("r", encoding="utf-8") as fh:
        loaded_payload = json.load(fh)
    restored = ElectrochemicalCyclingDataset.from_json(loaded_payload)
    print(f"Re-imported {len(restored.data)} rows from {OUT_JSON.name}")

    # 4. Verify the round-trip. Compare the tabular form: this normalizes units
    #    and column ordering, so it catches any value or unit drift across the
    #    serialize -> file -> deserialize path.
    original_df = dataset_to_df(dataset)
    restored_df = dataset_to_df(restored)

    assert len(restored.data) == len(dataset.data), (
        f"row count changed: {len(dataset.data)} -> {len(restored.data)}"
    )
    # pint-pandas frames compare equal element-wise when values and units match.
    assert original_df.pint.dequantify().equals(restored_df.pint.dequantify()), (
        "round-tripped data does not match the original"
    )

    first = restored.data[0]
    print(
        "Round-trip OK: "
        f"{len(restored.data)} rows match; "
        f"row 0 voltage = {first.voltage.value} {first.voltage.unit.name}"
    )


if __name__ == "__main__":
    main()
