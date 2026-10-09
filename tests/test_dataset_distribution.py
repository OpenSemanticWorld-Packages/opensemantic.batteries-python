"""Round-trip of a cycling dataset through a file distribution.

Writes the rows to a local file, attaches it as a ``Distribution`` and reads
them back, for both supported formats. Runs without network or wiki access; the
live counterpart is ``test_dataset_distribution_wiki.py``.
"""

import pytest

from opensemantic.batteries import CyclingDataRow, CyclingDatasetController
from opensemantic.core.v1 import Label, LocalFile

FORMATS = ["json", "csv"]


def _dataset() -> CyclingDatasetController:
    return CyclingDatasetController(
        label=[Label(text="cycling")],
        data=[
            CyclingDataRow(
                test_time={"value": float(i)},
                voltage={"value": 3.0 + i * 0.1},
                current={"value": 0.5 - i * 0.1},
            )
            for i in range(4)
        ],
    )


def _local_file(tmp_path, name):
    return LocalFile(
        label=[Label(text=name)], local_path=str(tmp_path / name)
    )


@pytest.mark.parametrize("fmt", FORMATS)
def test_externalize_writes_a_file_and_clears_the_rows(fmt, tmp_path):
    dataset = _dataset()
    target = tmp_path / f"rows.{fmt}"

    distribution = dataset.externalize_data(
        _local_file(tmp_path, f"rows.{fmt}"), fmt=fmt
    )

    assert dataset.data is None
    assert dataset.distributions == [distribution]
    assert target.exists()
    assert distribution.byte_size == target.stat().st_size
    assert distribution.media_type == (
        "text/csv" if fmt == "csv" else "application/json"
    )


@pytest.mark.parametrize("fmt", FORMATS)
def test_materialize_restores_the_rows(fmt, tmp_path):
    dataset = _dataset()
    expected = dataset.to_df()

    dataset.externalize_data(_local_file(tmp_path, f"rows.{fmt}"), fmt=fmt)
    rows = dataset.materialize_data()

    assert len(rows) == 4
    assert dataset.data is not None
    restored = dataset.to_df()
    assert list(restored.columns) == list(expected.columns)
    assert restored["voltage"].pint.magnitude.tolist() == pytest.approx(
        expected["voltage"].pint.magnitude.tolist()
    )
    assert restored["test_time"].pint.magnitude.tolist() == pytest.approx(
        expected["test_time"].pint.magnitude.tolist()
    )


@pytest.mark.parametrize("fmt", FORMATS)
def test_units_survive_the_round_trip(fmt, tmp_path):
    dataset = _dataset()
    dataset.externalize_data(_local_file(tmp_path, f"rows.{fmt}"), fmt=fmt)
    dataset.materialize_data()

    row = dataset.data[1]
    assert row.voltage.unit.name == "volt"
    assert row.test_time.unit.name == "second"
    assert row.voltage.value == pytest.approx(3.1)


def test_keep_data_leaves_the_rows_in_place(tmp_path):
    dataset = _dataset()
    dataset.externalize_data(
        _local_file(tmp_path, "rows.json"), fmt="json", keep_data=True
    )
    assert dataset.data is not None
    assert len(dataset.data) == 4


def test_materialize_without_a_distribution_fails_clearly():
    dataset = _dataset()
    with pytest.raises(ValueError, match="distribution"):
        dataset.materialize_data()


def test_unsupported_format_is_rejected():
    with pytest.raises(ValueError, match="format"):
        _dataset().serialize_data("parquet")
