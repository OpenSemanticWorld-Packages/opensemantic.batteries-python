"""Tests for the Maccor importer against real cycler export files.

Fixtures in ``tests/data/cycling/maccor/`` are real Maccor text/MIMS exports
(one per supported format). Each is imported, checked for the required
battery-data-format fields, and round-tripped through ``to_df``/``from_df``.
"""

from pathlib import Path

import pytest

DATA_DIR = Path(__file__).parent / "data" / "cycling" / "maccor"
MACCOR_FILES = sorted(DATA_DIR.glob("*.txt"))


def test_fixtures_present():
    assert MACCOR_FILES, f"no Maccor fixture files found in {DATA_DIR}"


@pytest.mark.parametrize("path", MACCOR_FILES, ids=lambda p: p.name)
def test_maccor_import_roundtrip(path):
    pytest.importorskip("maccor_utility")
    from opensemantic.batteries import BatteryCyclingDataset, read_maccor

    ds = read_maccor(path)
    assert isinstance(ds, BatteryCyclingDataset)
    assert len(ds.rows) > 0

    row = ds.rows[0]
    # BDF-required fields present and typed in their canonical unit
    assert row.test_time is not None
    assert row.voltage is not None and row.voltage.unit.name == "volt"
    assert row.current is not None and row.current.unit.name == "ampere"

    # test_time is monotonically non-decreasing
    times = [r.test_time.value for r in ds.rows]
    assert all(b >= a for a, b in zip(times, times[1:]))

    # to_df / from_df round-trip preserves row count
    df = ds.to_df()
    assert "voltage" in df.columns
    assert len(BatteryCyclingDataset.from_df(df).rows) == len(ds.rows)
