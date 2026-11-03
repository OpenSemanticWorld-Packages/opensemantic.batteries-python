"""The Maccor dashboard example, loaded from its file and driven like a click.

The example builds its backend and view at import, so a change to the view or
the dataset API that the example does not follow fails here. It reads the
Maccor files under tests/data; no network.
"""

from __future__ import annotations

import logging
import runpy
from pathlib import Path

import pytest

pytest.importorskip("panel")
pytest.importorskip("panelini")

from opensemantic.batteries.view import LazyBatteryDataView  # noqa: E402

EXAMPLE = (
    Path(__file__).parent.parent / "examples" / "maccor" / "battery_dashboard_maccor.py"
)
LOGGER = "opensemantic.batteries.view"
UNITS = {
    "test_time": "second",
    "voltage": "volt",
    "current": "ampere",
    "capacity": "coulomb",
}


@pytest.fixture(scope="module")
def example():
    return runpy.run_path(str(EXAMPLE))


@pytest.fixture
def backend(example):
    return example["MaccorBatteryBackend"]()


def test_every_file_loads_as_a_frame_with_units(backend):
    for cell, procedure in backend._DATASETS:
        datasets = backend.datasets([cell], [procedure])
        assert len(datasets) == 1, (cell, procedure)
        df = datasets[0].df
        assert len(df) > 0, (cell, procedure)
        for column, unit in UNITS.items():
            assert str(df[column].pint.units) == unit, (cell, procedure, column)
        assert df["step_type"].notna().any(), (cell, procedure)


def test_selection_draws_the_time_series_and_cycle_plots(backend, caplog):
    view = LazyBatteryDataView(backend=backend)
    caplog.clear()
    caplog.set_level(logging.INFO, logger=LOGGER)
    view._sel_instances["cell"] = {"Item:CellA", "Item:CellB"}
    view._sel_instances["proc"] = {"Item:AgingA", "Item:AgingB"}
    view._apply_selection()
    messages = [r.getMessage() for r in caplog.records]
    assert not [m for m in messages if "dataset query failed" in m]
    assert not [m for m in messages if "time-series plot shows a placeholder" in m]
    assert not [m for m in messages if "cycle plot shows a placeholder" in m]
