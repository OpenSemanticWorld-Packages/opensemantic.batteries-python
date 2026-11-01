"""Tests for the per-cycle summary.

``cycle_summary`` reads either the typed rows or the frame of a cycling
dataset. The two must agree exactly: the dashboard plots the frame form while
the upload examples pass rows, and a difference between them would show up as
a capacity that changes depending on where it was asked for.

The cases below are the ones the scan is sensitive to, because a step's
contribution depends on the row before it: a counter that continues across a
step boundary, a counter the cycler reset, a cycle split into several steps,
and rows that carry no cycle or no step type.
"""

from typing import List, Optional

import pytest

from opensemantic.batteries import (
    CyclingDatasetController,
    StepType,
    StepTypedCyclingDataRow,
    cycle_summary,
)
from opensemantic.characteristics.quantitative.v1 import (
    Count,
    ElectricCharge,
    ElectricCurrent,
    Time,
    Voltage,
)
from opensemantic.core.v1 import Label


def _row(
    t: float,
    capacity: Optional[float] = None,
    cycle: Optional[float] = None,
    step: Optional[StepType] = None,
    count: Optional[float] = None,
) -> StepTypedCyclingDataRow:
    return StepTypedCyclingDataRow(
        test_time=Time(value=t),
        voltage=Voltage(value=3.0),
        current=ElectricCurrent(value=0.5),
        capacity=None if capacity is None else ElectricCharge(value=capacity),
        cycle_count=None if cycle is None else Count(value=cycle),
        step_count=None if count is None else Count(value=count),
        step_type=step,
    )


def _as_frame(rows: List[StepTypedCyclingDataRow]):
    """The same rows in frame form, the way the dashboard holds them."""

    class _Typed(CyclingDatasetController):
        data: Optional[List[StepTypedCyclingDataRow]] = None

    return _Typed(label=[Label(text="rows")], data=rows).to_df()


def _capacities(summary):
    return [
        (
            row.cycle_count.value,
            (
                None
                if row.charge_capacity is None
                else round(row.charge_capacity.value, 6)
            ),
            (
                None
                if row.discharge_capacity is None
                else round(row.discharge_capacity.value, 6)
            ),
        )
        for row in summary.data
    ]


CASES = {
    "counter continues across the step": [
        _row(0, 0.0, 1, StepType.CC_CHG, 1),
        _row(1, 10.0, 1, StepType.CC_CHG, 1),
        _row(2, 18.0, 1, StepType.CC_DCH, 2),
    ],
    "cycler reset the counter": [
        _row(0, 0.0, 1, StepType.CC_CHG, 1),
        _row(1, 10.0, 1, StepType.CC_CHG, 1),
        _row(2, 0.0, 1, StepType.CC_DCH, 2),
        _row(3, 7.0, 1, StepType.CC_DCH, 2),
    ],
    "several charge steps in one cycle": [
        _row(0, 0.0, 1, StepType.CC_CHG, 1),
        _row(1, 5.0, 1, StepType.CC_CHG, 1),
        _row(2, 5.0, 1, StepType.REST, 2),
        _row(3, 5.0, 1, StepType.CV_CHG, 3),
        _row(4, 9.0, 1, StepType.CV_CHG, 3),
    ],
    "rows without a cycle or a step type": [
        _row(0, 1.0),
        _row(1, 3.0, 1, None, 1),
        _row(2, 8.0, 1, StepType.CC_CHG, 2),
        _row(3, 12.0, 2, StepType.CC_DCH, 1),
    ],
    "two cycles": [
        _row(0, 0.0, 1, StepType.CC_CHG, 1),
        _row(1, 6.0, 1, StepType.CC_CHG, 1),
        _row(2, 11.0, 1, StepType.CC_DCH, 2),
        _row(3, 0.0, 2, StepType.CC_CHG, 1),
        _row(4, 4.0, 2, StepType.CC_CHG, 1),
    ],
}


@pytest.mark.parametrize("name", list(CASES))
def test_rows_and_frame_agree(name):
    rows = CASES[name]

    from_rows = _capacities(cycle_summary(rows))
    from_frame = _capacities(cycle_summary(_as_frame(rows)))

    assert from_rows == from_frame


def test_capacity_of_a_continuing_counter():
    summary = cycle_summary(CASES["counter continues across the step"])
    assert _capacities(summary) == [(1.0, 10.0, 8.0)]


def test_capacity_after_a_reset():
    """A counter that restarts below the previous value starts the step at zero."""
    summary = cycle_summary(CASES["cycler reset the counter"])
    assert _capacities(summary) == [(1.0, 10.0, 7.0)]


def test_charge_steps_are_summed():
    summary = cycle_summary(CASES["several charge steps in one cycle"])
    assert _capacities(summary) == [(1.0, 9.0, None)]


def test_efficiency_needs_both_capacities():
    summary = cycle_summary(CASES["counter continues across the step"])
    row = summary.data[0]
    assert row.discharge_coulomb_efficiency.value == pytest.approx(0.8)
    assert row.charge_coulomb_efficiency.value == pytest.approx(1.25)

    only_charge = cycle_summary(CASES["several charge steps in one cycle"]).data[0]
    assert only_charge.discharge_coulomb_efficiency is None
    assert only_charge.charge_coulomb_efficiency is None


def test_an_empty_input_summarizes_to_nothing():
    assert cycle_summary([]).data == []
