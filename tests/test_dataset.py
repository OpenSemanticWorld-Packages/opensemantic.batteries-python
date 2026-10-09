"""Tests for the battery cycling dataset controller."""

from opensemantic.batteries import (
    CyclingDataRow,
    CyclingDatasetController,
    ElectrochemicalCyclingDataset,
)
from opensemantic.core.v1 import Label


def _sample() -> CyclingDatasetController:
    return CyclingDatasetController(
        label=[Label(text="sample")],
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


def test_to_df_columns_and_roundtrip():
    ds = _sample()
    df = ds.to_df()
    assert {"test_time", "voltage", "current"} <= set(df.columns)
    assert str(df["voltage"].dtype) == "pint[volt][Float64]"

    restored = CyclingDatasetController.from_df(df)
    assert len(restored.data) == 2
    assert restored.data[1].voltage.value == 3.1
    assert restored.data[1].current.value == 0.5


def test_compact_export_roundtrip():
    ds = _sample()
    # exclude_defaults gives a compact representation (fields left at their
    # default unit/type are omitted, leaving just the value)
    payload = ds.to_json(exclude_defaults=True)
    assert payload["data"][1]["voltage"] == {"value": 3.1}

    restored = ElectrochemicalCyclingDataset.from_json(payload)
    assert restored.data[1].voltage.value == 3.1
    assert restored.data[1].voltage.unit.name == "volt"  # default unit restored
