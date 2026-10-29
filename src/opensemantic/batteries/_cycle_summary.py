"""Per-cycle summary table of a cycling dataset.

:class:`CycleDataRow` and :class:`CycleSummaryDataset` are temporary local
extensions: the world.opensemantic.batteries schema has no cycle table yet.
Once it ships one, regenerate the models and remove this module.

A summary has one row per cycle with the charge and the discharge capacity of
that cycle and the two coulomb efficiencies derived from them (wide form).
:func:`cycle_summary` builds it from the rows of a cycling dataset, using the
``step_type`` of each row to tell the charge step from the discharge step.

Layer note (see CLAUDE.md): Pydantic **v1**, like the cycling classes.
"""

import math
from typing import Dict, List, Optional, Union

from pydantic.v1 import Field

from opensemantic.base.v1 import Dataset
from opensemantic.batteries._cycling import StepType
from opensemantic.characteristics.quantitative.v1 import (
    Count,
    Efficiency,
    ElectricCharge,
)
from opensemantic.core.v1 import Characteristic, Label

__all__ = ["CycleDataRow", "CycleSummaryDataset", "cycle_summary"]


class CycleDataRow(Characteristic):
    """One row of a cycle summary: the capacities and efficiencies of a cycle.

    Temporary local extension until the world.opensemantic.batteries schema
    ships a cycle table; then drop this class.
    """

    cycle_count: Count = Field(..., description="Cycle number.")
    charge_capacity: Optional[ElectricCharge] = Field(
        None, description="Charge capacity of the cycle."
    )
    discharge_capacity: Optional[ElectricCharge] = Field(
        None, description="Discharge capacity of the cycle."
    )
    charge_coulomb_efficiency: Optional[Efficiency] = Field(
        None,
        description="Charge coulomb efficiency of the cycle: charge capacity "
        "divided by discharge capacity. Stored as a dimensionless ratio.",
    )
    discharge_coulomb_efficiency: Optional[Efficiency] = Field(
        None,
        description="Discharge coulomb efficiency of the cycle: discharge "
        "capacity divided by charge capacity. Stored as a dimensionless ratio.",
    )


class CycleSummaryDataset(Dataset):
    """A per-cycle summary dataset, with a default label for the frame conversion.

    Temporary local extension until the world.opensemantic.batteries schema
    ships a cycle table; then drop this class.
    """

    data: Optional[List[CycleDataRow]] = None

    @classmethod
    def from_df(
        cls,
        df,
        label: Optional[Union[str, List[Label]]] = "Cycle Summary",
        **kwargs,
    ):
        """Build a dataset from a pint-pandas frame.

        ``label`` is required by the model, so a default is supplied; pass a
        string or a list of ``Label``.
        """
        labels = [Label(text=label)] if isinstance(label, str) else label
        return super().from_df(df, label=labels, **kwargs)


def _in_coulomb(value: Optional[ElectricCharge]) -> Optional[float]:
    """The magnitude of a capacity in coulomb, or ``None`` if it is missing."""
    if value is None or value.value is None:
        return None
    return float(value.to_pint().to("coulomb").magnitude)


# Step types whose capacity counts as charge / discharge capacity
_CHARGE_STEPS = {StepType.CC_CHG, StepType.CV_CHG, StepType.CCCV_CHG}
_DISCHARGE_STEPS = {StepType.CC_DCH, StepType.CV_DCH, StepType.CCCV_DCH}


def cycle_summary(
    rows: List,
    label: Union[str, List[Label]] = "Cycle Summary",
) -> CycleSummaryDataset:
    """Summarize cycling rows into one capacity and efficiency record per cycle.

    Rows are grouped by ``cycle_count``; rows without one are skipped. The
    charge capacity of a cycle is the sum of the capacity increases of all its
    charge steps (``CC_CHG``, ``CV_CHG``, ``CCCV_CHG``), the discharge
    capacity the same sum over its discharge steps (``CC_DCH``, ``CV_DCH``,
    ``CCCV_DCH``). A step is a run of consecutive rows with the same cycle,
    ``step_count`` and ``step_type``. Its increase is its last capacity minus
    a baseline: the capacity of the row before the step if the step continues
    that counter (first value not below it), else zero (the cycler reset the
    counter).
    Capacities are converted to coulomb first. A capacity is ``None`` when
    the cycle has no such steps or they carry no capacity. Rows without a
    ``step_type`` count for neither. The result is sorted by cycle number.

    The discharge coulomb efficiency of a cycle is its discharge capacity
    divided by its charge capacity, and the charge coulomb efficiency is the
    inverse. Each is a dimensionless ratio, and ``None`` unless both
    capacities exist and the denominator is greater than zero.
    """
    charge: Dict[float, Optional[float]] = {}
    discharge: Dict[float, Optional[float]] = {}
    previous: Optional[float] = None  # capacity of the last row that had one
    step_key = None  # (cycle, step_count, step_type) of the current step
    last: Optional[float] = None  # capacity of the previous row in the step
    for row in rows:
        capacity = _in_coulomb(getattr(row, "capacity", None))
        cycle = getattr(row, "cycle_count", None)
        if cycle is None or cycle.value is None:
            previous = capacity if capacity is not None else previous
            continue
        number = cycle.value
        charge.setdefault(number, None)
        discharge.setdefault(number, None)
        step = getattr(row, "step_type", None)
        target = (
            charge
            if step in _CHARGE_STEPS
            else discharge if step in _DISCHARGE_STEPS else None
        )
        count = getattr(row, "step_count", None)
        key = (number, count.value if count is not None else None, step)
        if key != step_key:
            step_key = key
            last = None
        if target is not None and capacity is not None:
            if last is None:
                # first row of the step: continue the counter or start at zero
                base = previous if previous is not None and capacity >= previous else 0
            else:
                base = last
            target[number] = (target[number] or 0.0) + capacity - base
            last = capacity
        previous = capacity if capacity is not None else previous

    def _value(capacity: Optional[float]) -> Optional[ElectricCharge]:
        return None if capacity is None else ElectricCharge(value=capacity)

    def _ratio(
        numerator: Optional[float], denominator: Optional[float]
    ) -> Optional[Efficiency]:
        if numerator is None or denominator is None or not denominator > 0:
            return None
        ratio = numerator / denominator
        return Efficiency(value=ratio) if math.isfinite(ratio) else None

    summary_rows = [
        CycleDataRow(
            cycle_count=Count(value=number),
            charge_capacity=_value(charge[number]),
            discharge_capacity=_value(discharge[number]),
            charge_coulomb_efficiency=_ratio(charge[number], discharge[number]),
            discharge_coulomb_efficiency=_ratio(discharge[number], charge[number]),
        )
        for number in sorted(charge)
    ]
    labels = [Label(text=label)] if isinstance(label, str) else label
    return CycleSummaryDataset(label=labels, data=summary_rows)
