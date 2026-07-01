"""Tests for the battery cycling data model."""

from opensemantic.batteries import BatteryCyclingDataset, CyclingDataRow
from opensemantic.characteristics.quantitative.v1 import (
    ElectricCurrent,
    Time,
    Voltage,
)


def _sample() -> BatteryCyclingDataset:
    return BatteryCyclingDataset(
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


def test_to_df_columns_and_roundtrip():
    ds = _sample()
    df = ds.to_df()
    assert {"test_time", "voltage", "current"} <= set(df.columns)
    assert str(df["voltage"].dtype) == "pint[volt][Float64]"

    restored = BatteryCyclingDataset.from_df(df)
    assert len(restored.rows) == 2
    assert restored.rows[1].voltage.value == 3.1
    assert restored.rows[1].current.value == 0.5


def test_compact_export_roundtrip():
    ds = _sample()
    # exclude_defaults gives a compact representation (fields left at their
    # default unit/type are omitted, leaving just the value)
    payload = ds.to_json(exclude_defaults=True)
    assert payload["rows"][1]["voltage"] == {"value": 3.1}

    restored = BatteryCyclingDataset.from_json(payload)
    assert restored.rows[1].voltage.value == 3.1
    assert restored.rows[1].voltage.unit.name == "volt"  # default unit restored
